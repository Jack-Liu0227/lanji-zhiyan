"""Browser acceptance through real HTTP routes with a local model fixture."""
import http.server
import json
import os
from pathlib import Path
import threading
import time
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime
import workflow


class ModelFixture:
    def __enter__(self):
        self.requests = []
        requests = self.requests
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                user = next((m["content"] for m in reversed(payload["messages"]) if m["role"] == "user"), "")
                step = "write" if "BROWSER_WRITE" in user else "outline"
                if "BROWSER_SCHEDULE" in user:
                    delta = {"content": "BROWSER_SCHEDULE_NOTE_CONTENT"}
                elif payload["messages"][-1]["role"] == "tool":
                    delta = {"content": "BROWSER_RESULT_MEMORY_" + step}
                else:
                    delta = {"tool_calls": [{"index": 0, "id": "browser-" + step, "type": "function", "function": {
                        "name": "doc_write", "arguments": json.dumps({"markdown": "# " + step + "\nBROWSER_ARTIFACT_" + step})}}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
                self.end_headers()
                if payload.get("stream"):
                    self.wfile.write(("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\ndata: [DONE]\n\n").encode())
                else:
                    self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", **delta}}]}).encode())
                self.wfile.flush()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class ConnectedResearchRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page

    def project(self, name):
        return self.page.evaluate("""async name => {
          const made=await api('/api/projects',{title:name});
          await refreshState(false); curTask=made.task_id;
          return curTask;
        }""", name)

    def use_model(self, url):
        self.page.evaluate("""async url => {
          const saved=await api('/api/agent-settings/provider-save',{name:'Browser model',base_url:url,api:'openai-completions'});
          await api('/api/agent-settings/provider-models',{id:saved.provider.id,models:['browser-test'],auto:true});
          await refreshState(false);
        }""", url)

    def poll_json(self, path, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.page.request.get(self.url.rstrip("/") + path).json()
            if predicate(data):
                return data
            self.page.wait_for_timeout(100)
        self.fail("API state did not converge: " + json.dumps(data, ensure_ascii=False))

    def test_account_login_catalog_and_default_node_review_configuration(self):
        self.page.route("**/api/agent-settings/provider-account-status", lambda route: route.fulfill(json={
            "installed": False, "authenticated": False, "message": "TEST_CLI_NOT_INSTALLED", "login_command": "codex login"}))
        self.load_page()
        self.page.evaluate("() => { setSec='models'; openTab('settings'); }")
        self.page.locator("#ag-provider-select").wait_for()
        for provider in ("openai-codex", "claude-code", "antigravity"):
            self.assertEqual(self.page.locator(f'#ag-provider-select option[value="{provider}"]').count(), 1)
        self.page.locator("#ag-provider-select").select_option("openai-codex")
        self.page.locator("#ag-provider-add").click()
        self.page.get_by_text("TEST_CLI_NOT_INSTALLED", exact=False).wait_for()
        self.assertTrue(self.page.get_by_role("button", name="打开官方账号登录").is_visible())
        self.page.locator("dialog.ag-modal [data-close]").click()
        self.page.evaluate("() => { setSec='workflows'; render(); }")
        self.page.locator("[data-wfl-row] details summary").first.click()
        self.assertTrue(self.page.locator("[data-wfl-review]").first.is_checked())
        self.page.locator("[data-wfl-review]").first.uncheck()
        self.page.locator("[data-wfl-save]").click()
        self.page.get_by_text("已保存", exact=True).wait_for()
        self.page.reload(wait_until="networkidle")
        self.page.evaluate("() => { setSec='workflows'; openTab('settings'); }")
        self.page.locator("[data-wfl-row] details summary").first.click()
        self.assertFalse(self.page.locator("[data-wfl-review]").first.is_checked())
        self.assertEqual(self.errors, [])

    def test_sequential_run_approval_and_memory_reach_real_next_model_request(self):
        self.load_page()
        pid = self.project("Browser connected workflow")
        with ModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("""async pid => {
              const result=await api('/api/workflow/layouts/save',{name:'Browser sequence',
                nodes:[{id:'o',step:'outline',label:'Outline',assistant_id:'builtin-steward',prompt:'BROWSER_OUTLINE'},
                       {id:'w',step:'write',label:'Write',assistant_id:'builtin-steward',prompt:'BROWSER_WRITE',review_required:false}],
                edges:[{source:'o',target:'w'}],order:['o','w']});
              await api('/api/workflow/layouts/select',{task_id:pid,layout_id:result.layout.id});
              openTab('workflow');
            }""", pid)
            self.page.locator('#pwWorkflowRun').click()
            self.page.locator("[data-sequence-start]").click()
            state = self.poll_json('/api/workflow/execution?task_id=' + pid, lambda d: (d.get('run') or {}).get('status') in ('awaiting_review', 'failed'))['run']
            self.assertEqual(state["status"], "awaiting_review", state)
            current=next((node for node in state['nodes'] if node['id']==state['current_node']),None)
            self.assertIsNotNone(current,state)
            self.assertEqual(current['status'],'awaiting_review',state)
            try:
                self.page.locator("[data-sequence-approve]").wait_for(timeout=8000)
            except Exception:
                self.fail(json.dumps({'dialog':self.page.locator('dialog.connected-dialog').inner_text(),'errors':self.errors,'state':state},ensure_ascii=False,default=str))
            self.assertIn("BROWSER_ARTIFACT_outline", self.page.locator("dialog.connected-dialog").inner_text())
            self.page.locator("[data-sequence-feedback]").fill("BROWSER_HUMAN_FEEDBACK")
            self.page.locator("[data-sequence-approve]").click()
            result = self.poll_json('/api/workflow/execution?task_id=' + pid, lambda d: d['run']['status'] in ('succeeded', 'failed'))['run']
            self.assertEqual(result['status'], 'succeeded', result)
            self.assertEqual([n["status"] for n in result["nodes"]], ["delivered", "delivered"])
            writes = [p for p in model.requests if any(m["role"] == "user" and "BROWSER_WRITE" in m["content"] for m in p["messages"])]
            self.assertTrue(writes)
            for marker in ("BROWSER_HUMAN_FEEDBACK", "BROWSER_ARTIFACT_outline", "BROWSER_RESULT_MEMORY_outline"):
                self.assertIn(marker, json.dumps(writes[0], ensure_ascii=False))
            self.page.get_by_text("全部完成", exact=True).wait_for(timeout=10000)
            screenshots = Path(os.environ.get("LANJI_TEST_SCREENSHOT_DIR", str(self.temporary_root)))
            screenshots.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(screenshots / "connected-workflow.png"))
        self.assertEqual(self.errors, [])

    def test_scheduled_report_opens_archived_note_from_run_history(self):
        self.load_page()
        pid = self.project("Browser schedule notes")
        with ModelFixture() as model:
            self.use_model(model.url)
            sid = self.page.evaluate("""async pid => {
              const saved=await api('/api/schedule/save',{name:'Browser report',project_id:pid,
                assistant_id:'builtin-steward',prompt:'BROWSER_SCHEDULE',frequency:'manual',cron:'0 9 * * *',output_dir:'Reports',enabled:true});
              const id=saved.schedule?.id || saved.id;
              await api('/api/schedule/run',{id}); return id;
            }""", pid)
            runs = self.poll_json('/api/schedules', lambda d: any(r['schedule_id'] == sid and r.get('note_id') for r in d['runs']))['runs']
            self.assertTrue(next(r for r in runs if r["schedule_id"] == sid).get("note_path"), runs)
            self.page.evaluate("() => openTab('schedules')")
            self.page.locator("[data-run-note]").first.click()
            self.page.wait_for_function("document.querySelector('#mdSource')?.value.includes('BROWSER_SCHEDULE_NOTE_CONTENT')", timeout=20000)
            self.assertEqual(self.page.evaluate("mdProj.pid"), pid)
        self.assertEqual(self.errors, [])

    def test_project_and_workflow_bibliography_refresh_the_same_record(self):
        self.load_page()
        with workflow.WorkflowDB(self.database_path) as db:
            pid = db.create_task("Browser shared bibliography", base_dir=str(self.temporary_root / "bibliography"))
            rid = db.cache_result(pid, {"source": "zotero", "title": "Before sync", "doi": "10.123/browser"})
        self.page.evaluate("async pid => { await refreshState(false); curTask=pid; spId=pid; spTab='papers'; openTab('space'); }", pid)
        self.page.get_by_text("Before sync", exact=False).wait_for()
        self.page.evaluate("async data => api('/api/library/project',{task_id:data.pid,result_id:data.rid,fields:{title:'After sync shared title'}})", {"pid": pid, "rid": rid})
        self.page.get_by_text("After sync shared title", exact=False).wait_for(timeout=15000)
        self.page.evaluate("() => openTab('workflow')")
        workspace_section(self.page, 'papers')
        self.page.get_by_text('After sync shared title',exact=False).wait_for(timeout=15000)
        self.assertEqual(self.errors, [])

    def test_calendar_todo_checkbox_updates_task_and_source_opens_editor(self):
        self.load_page()
        pid = self.project("Browser calendar deadline")
        todo = self.page.evaluate("async pid => api('/api/todos/add',{task_id:pid,title:'CALENDAR_DEADLINE_TASK',due_date:today()})", pid)
        event_id = "linked:todo:" + todo["id"]
        self.page.evaluate("async () => { await refreshState(false); calSel=today(); openTab('calendar'); }")
        row = self.page.locator(f'.check-list [data-chk="{event_id}"]')
        row.check()
        self.poll_json('/api/todos?task_id=' + pid, lambda d: any(t['id'] == todo['id'] and t['status'] == 'DONE' for t in d['todos']))
        self.page.locator(f'.check-list [data-cal-open="{event_id}"]').click()
        self.page.locator('dialog:has(.ec-plan-form)').wait_for(timeout=15000)
        self.assertIn('CALENDAR_DEADLINE_TASK', self.page.locator('dialog:has(.ec-plan-form)').inner_text() + self.page.locator('dialog:has(.ec-plan-form)').evaluate("node=>[...node.querySelectorAll('input')].map(i=>i.value).join(' ' )"))
        self.assertEqual(self.errors, [])

    def test_calendar_opens_scheduled_result_note(self):
        self.load_page()
        pid = self.project("Browser calendar note")
        with ModelFixture() as model:
            self.use_model(model.url)
            sid = self.page.evaluate("""async pid => {
              const saved=await api('/api/schedule/save',{name:'Calendar report',project_id:pid,
                assistant_id:'builtin-steward',prompt:'BROWSER_SCHEDULE',frequency:'manual',cron:'0 9 * * *',output_dir:'Reports',enabled:true});
              const id=saved.schedule?.id || saved.id; await api('/api/schedule/run',{id}); return id;
            }""", pid)
            data = self.poll_json('/api/calendar/linked', lambda d: any(e.get('schedule_id') == sid and e.get('note_id') for e in d['events']))
            event = next(e for e in data['events'] if e.get('schedule_id') == sid and e.get('note_id'))
            self.page.evaluate("async () => { await refreshState(false); calSel=today(); openTab('calendar'); }")
            self.page.locator(f'.check-list [data-cal-open="{event["id"]}"]').click()
            self.page.wait_for_function("document.querySelector('#mdSource')?.value.includes('BROWSER_SCHEDULE_NOTE_CONTENT')", timeout=20000)
            self.assertEqual(self.page.evaluate('mdProj.pid'), pid)
        self.assertEqual(self.errors, [])

    def test_calendar_opens_human_review_and_updates_after_approval(self):
        self.load_page()
        pid = self.project("Browser calendar review")
        with ModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("""async pid => {
              const saved=await api('/api/workflow/layouts/save',{name:'Calendar review',
                nodes:[{id:'o',step:'outline',label:'CALENDAR_REVIEW_NODE',assistant_id:'builtin-steward',prompt:'BROWSER_OUTLINE'}],edges:[],order:['o']});
              await api('/api/workflow/layouts/select',{task_id:pid,layout_id:saved.layout.id});
              await api('/api/workflow/execution/start',{task_id:pid});
            }""", pid)
            data = self.poll_json('/api/calendar/linked', lambda d: any(e.get('task_id') == pid and e.get('source_type') == 'workflow_review' and e.get('status') == 'awaiting_review' for e in d['events']))
            event = next(e for e in data['events'] if e.get('task_id') == pid and e.get('source_type') == 'workflow_review')
            self.page.evaluate("async () => { await refreshState(false); calSel=today(); openTab('calendar'); }")
            self.assertTrue(self.page.locator(f'.check-list [data-chk="{event["id"]}"]').is_disabled())
            self.page.locator(f'.check-list [data-cal-open="{event["id"]}"]').click()
            self.page.locator('[data-sequence-approve]').click()
            self.poll_json('/api/workflow/execution?task_id=' + pid, lambda d: d['run']['status'] == 'succeeded')
            self.page.locator('[data-sequence-close]').click()
            self.page.wait_for_function("id=>document.querySelector(`.check-list [data-chk=\"${id}\"]`)?.checked", arg=event['id'], timeout=15000)
        self.assertEqual(self.errors, [])
