"""Exercise project grants, file tasks and review against real HTTP routes in Edge."""
from __future__ import annotations

import http.server
import json
import threading
import time
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime
from tests import test_connected_research_runtime as connected
import workflow
import project_tasks


class TaskModelFixture:
    """A real local completion endpoint; no patched execution or fabricated artifact."""

    def __enter__(self):
        self.requests = []
        requests = self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                reply = "# 项目任务报告\nPROJECT_TASK_REAL_OUTPUT\n已根据当前项目材料完成分析。"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if payload.get("stream") else "application/json")
                self.end_headers()
                if payload.get("stream"):
                    value = {"choices": [{"delta": {"content": reply}, "finish_reason": "stop"}]}
                    self.wfile.write(("data: " + json.dumps(value) + "\n\ndata: [DONE]\n\n").encode())
                else:
                    self.wfile.write(json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}]}).encode())
                self.wfile.flush()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class ProjectTasksRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page
    use_model = connected.ConnectedResearchRuntimeTest.use_model
    def poll_json(self, path, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.page.request.get(self.url.rstrip("/") + path).json()
            if predicate(data):
                return data
            self.page.wait_for_timeout(100)
        summary = [{"id": task["id"], "title": task["title"], "status": task["status"],
                    "run_error": (task.get("latest_run") or {}).get("error"),
                    "phase": (task.get("latest_run") or {}).get("checkpoint", {}).get("phase")}
                   for task in data.get("tasks", [])] if "tasks" in data else data
        self.fail("API state did not converge: " + json.dumps(summary, ensure_ascii=False)[:2000])

    def seed(self):
        self.assistant = "project-writer-" + self._testMethodName
        with workflow.WorkflowDB(self.database_path) as db:
            self.pid = db.create_task("项目任务验收", base_dir=str(self.temporary_root / self._testMethodName))
            db.assistant_save(self.assistant, name="项目分析专家", enabled=1, permission="full_access")
            root = db.base_dir(self.pid)
            root.mkdir(parents=True, exist_ok=True)
            (root / "input.md").write_text("# PROJECT_INPUT\n仅供当前项目分析。", encoding="utf-8")
        self.load_page()
        self.page.evaluate("pid=>{curTask=pid;}", self.pid)

    def grant(self):
        result = self.page.evaluate("""async data=>api('/api/project-tasks/bindings',{
          project_id:data.pid,kind:'assistant',target_id:data.assistant})""", {"pid": self.pid, "assistant": self.assistant})
        return result["binding"]["id"]

    def task_state(self, title, status):
        return self.poll_json("/api/project-tasks?project_id=" + self.pid,
            lambda data: any(t["title"] == title and t["status"] in status for t in data.get("tasks", [])))

    def detail(self, task_id):
        return self.page.request.get(self.url + "api/project-tasks/detail?project_id=" + self.pid + "&id=" + task_id).json()["task"]

    def compose(self, binding, title, mode="supervised"):
        self.page.evaluate("""data=>{ProjectTasks.compose(data.pid,{inputs:[{relative:'input.md'}],executor_binding_id:data.binding});}""",
            {"pid": self.pid, "binding": binding})
        self.page.locator("[data-pt-title]").fill(title)
        self.page.locator("[data-pt-goal]").fill("分析附件并给出可核查的结论。" + title)
        self.page.wait_for_function("id=>document.querySelector('[data-pt-executor]')?.value===id", arg=binding)
        self.page.locator("[data-pt-mode]").select_option(mode)

    def test_ordinary_file_task_runs_without_workflow_and_requires_real_human_review(self):
        self.seed()
        with TaskModelFixture() as model:
            self.use_model(model.url)
            binding = self.grant()
            self.compose(binding, "MANUAL_FILE_TASK")
            self.page.locator("[data-pt-create-start]").click()
            state = self.task_state("MANUAL_FILE_TASK", {"awaiting_review", "failed"})
            task = next(t for t in state["tasks"] if t["title"] == "MANUAL_FILE_TASK")
            self.assertEqual(task["status"], "awaiting_review", (task.get("latest_run") or {}).get("error"))
            task = self.detail(task["id"])
            self.assertFalse(task["workflow_run_id"])
            self.assertEqual(task["scope"]["kind"], "project")
            self.assertEqual(task["latest_run"]["policy_snapshot"]["autonomy_mode"], "supervised")
            self.assertEqual(task["deliveries"], [])
            self.assertTrue(task["outputs"])
            self.assertTrue(task["latest_run"]["input_snapshot"]["assets"])
            self.page.locator('#pwMessages [data-pw-output]').wait_for(timeout=15000)
            self.assertIn("PROJECT_TASK_REAL_OUTPUT", self.page.locator("#pwMessages").inner_text())
            self.page.evaluate("""async id=>{
              S.events=(await api('/api/calendar/linked')).events;
              const event=S.events.find(item=>item.target_type==='project_task' && item.target_id===id);
              if(!event) throw new Error('待核查任务缺少关联日历入口');
              await ConnectedResearch.openCalendarSource(event.id);
            }""", task["id"])
            self.page.locator('#pwApprove').wait_for(timeout=15000)
            self.assertTrue(self.page.locator('#projectWorkspace').is_visible())
            self.page.evaluate("async()=>{await ConnectedResearch.refreshReviews();await ConnectedResearch.reviewInbox();}")
            self.page.locator('[data-review-open="' + task['id'] + '"]').click()
            self.page.locator('[data-pt-action="approve"]').wait_for(timeout=15000)
            self.page.set_viewport_size({"width": 800, "height": 650})
            self.assertTrue(self.page.locator("dialog.pt-dialog").evaluate("node=>{const r=node.getBoundingClientRect();return r.left>=0 && r.top>=0 && r.right<=innerWidth && r.bottom<=innerHeight;}"))
            self.page.locator("[data-pt-feedback]").fill("已核对输入和产物，人工通过。")
            self.page.locator('[data-pt-action="approve"]').click()
            self.task_state("MANUAL_FILE_TASK", {"delivered"})
            task = self.detail(task["id"])
            self.assertEqual(task["deliveries"][-1]["source"], "human")
            self.assertIn("人工通过", task["deliveries"][-1]["feedback"])
            self.assertTrue(model.requests)
        self.assertEqual(self.errors, [])

    def test_unknown_tool_result_requires_explicit_checked_restart(self):
        self.seed()
        binding = self.grant()
        with workflow.WorkflowDB(self.database_path) as db:
            task = project_tasks.create(db, {"project_id": self.pid, "goal": "核对后继续工具任务", "executor_binding_id": binding},
                authority=project_tasks.CommandContext.ui(self.pid))
            run = project_tasks.start(db, task["id"], authority=project_tasks.CommandContext.ui(self.pid))
            project_tasks.claim(db, run["id"])
            project_tasks.save_checkpoint(db, run["id"], phase="tool", inflight={"name": "project_note_write", "path": "笔记/待核对.md"})
            project_tasks.fail(db, run["id"], "测试工具返回丢失，尚未确认写入结果")
        with TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("data=>{ProjectTasks.openTask(data.id,data.pid);}", {"id": task["id"], "pid": self.pid})
            self.page.locator('[data-pt-action="start"]').click()
            self.page.locator("[data-pt-error]").filter(has_text="工具结果未确认").wait_for()
            self.assertEqual(model.requests, [])
            self.page.locator('[data-pt-action="restart"]').click()
            with self.page.expect_response(lambda response: response.url.endswith('/api/project-tasks/start')) as response_info:
                self.page.locator('dialog.modal [data-ok]').click()
            self.assertTrue(response_info.value.ok, response_info.value.text())
            self.poll_json("/api/project-tasks/detail?project_id=" + self.pid + "&id=" + task["id"],
                lambda data: data["task"]["status"] in ("awaiting_review", "failed"))
            detail = self.detail(task["id"])
            self.assertEqual(detail["status"], "awaiting_review", (detail.get("latest_run") or {}).get("error"))
            self.assertTrue(detail["latest_run"]["checkpoint"]["restart_approved"])
            self.assertEqual(len(detail["runs"]), 2)
            self.assertTrue(model.requests)
        self.assertEqual(self.errors, [])

    def test_full_access_records_policy_delivery_and_project_default_is_explicit(self):
        self.seed()
        with TaskModelFixture() as model:
            self.use_model(model.url)
            binding = self.grant()
            self.compose(binding, "AUTO_FILE_TASK", "full_access")
            self.page.locator("[data-pt-create-start]").click()
            state = self.task_state("AUTO_FILE_TASK", {"delivered", "failed"})
            task = self.detail(next(t["id"] for t in state["tasks"] if t["title"] == "AUTO_FILE_TASK"))
            self.assertEqual(task["status"], "delivered", (task.get("latest_run") or {}).get("error"))
            self.assertEqual(task["deliveries"][-1]["source"], "policy")
            self.assertEqual(task["deliveries"][-1]["policy_snapshot"]["autonomy_mode"], "full_access")
            self.page.locator('[data-execution-id="'+task['id']+'"]').wait_for()
            self.page.evaluate('args=>ProjectTasks.openTask(...args)',[task['id'],self.pid])
            self.page.locator("[data-pt-decision-source]").filter(has_text="Full access").wait_for(timeout=15000)
            self.page.locator("dialog.pt-dialog [data-close]").click()
            self.page.evaluate("pid=>{ProjectTasks.openBindings(pid);}", self.pid)
            self.page.locator("[data-pt-policy]").select_option("full_access")
            self.page.locator("[data-pt-policy-save]").click()
            self.poll_json("/api/project-tasks/policy?project_id=" + self.pid, lambda d: d["policy"]["autonomy_mode"] == "full_access")
            self.page.locator("dialog.pt-dialog [data-close]").click()
            self.page.evaluate("pid=>{ProjectTasks.compose(pid);}", self.pid)
            self.page.wait_for_function("document.querySelector('[data-pt-mode]')?.value==='full_access'")
        self.assertEqual(self.errors, [])

    def test_project_grants_and_workflow_roles_are_dynamic_and_agent_cannot_elevate(self):
        self.seed()
        self.page.evaluate("pid=>{curTask=pid;spId=pid;spTab='plan';openTab('space');}", self.pid)
        self.page.locator('#pwProjectSettings summary').click()
        self.page.locator('#pwBindings').click()
        self.page.locator("[data-pt-grant-target]").select_option(self.assistant)
        with self.page.expect_response(lambda response:response.url.endswith('/api/project-tasks/bindings')) as saved:
            self.page.locator("[data-pt-grant]").click()
        self.assertTrue(saved.value.ok,saved.value.text())
        self.page.locator('[data-pt-binding-toggle="'+saved.value.json()['binding']['id']+'"]').wait_for()
        bindings = self.page.request.get(self.url + "api/project-tasks/executors?project_id=" + self.pid).json()["bindings"]
        binding = next(b["id"] for b in bindings if b["target_id"] == self.assistant)
        self.page.locator("dialog.pt-dialog").last.locator("[data-close]").click()
        template = self.page.evaluate("""async ()=> (await api('/api/workflow/layouts/save',{
          name:'动态任务角色模板',nodes:[{id:'inspect',step:'task',label:'核对材料',executor_role_id:'material_reviewer',review_required:true,prompt:'核对项目材料'}],edges:[],order:['inspect']})).layout""")
        workspace_section(self.page,'workflow')
        self.page.locator('#pwRoles').click()
        self.page.locator("[data-pt-role-template]").select_option(template["id"])
        row = self.page.locator('[data-pt-role="material_reviewer"]')
        row.locator("select").select_option(binding)
        row.locator("button").click()
        roles = self.poll_json("/api/project-tasks/workflow-roles?project_id=" + self.pid + "&template_id=" + template["id"],
            lambda d: any(r["role_id"] == "material_reviewer" and r["executor_binding_id"] == binding for r in d["roles"]))
        self.assertEqual(next(r for r in roles["roles"] if r["role_id"] == "material_reviewer")["executor_binding_id"], binding)
        with workflow.WorkflowDB(self.database_path) as db:
            task = project_tasks.create(db, {"project_id": self.pid, "goal": "权限验证", "executor_binding_id": binding}, authority=project_tasks.CommandContext.ui(self.pid))
            with self.assertRaises(ValueError):
                project_tasks.start(db, task["id"], authority=project_tasks.CommandContext.agent(self.pid), autonomy_mode="full_access")
            with self.assertRaises(ValueError):
                project_tasks.set_mode(db, task["id"], "full_access", authority=project_tasks.CommandContext.agent(self.pid))
        self.assertEqual(self.errors, [])

    def test_file_scope_switch_preserves_draft_and_save_failure_blocks_task_creation(self):
        self.seed()
        self.grant()
        self.page.evaluate("()=>openTab('workflow')")
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="input.md"]').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator("#flEditText").fill("UNSAVED_PROJECT_FILE")
        self.page.route("**/api/conv/save", lambda route: route.fulfill(status=400, json={"error": "PROJECT_SAVE_BARRIER"}))
        self.page.locator("#flHand").click()
        self.page.locator("[data-instruction]").fill("PRESERVED_PROJECT_FILE_GOAL")
        self.page.locator("[data-handoff-project]").click()
        self.assertEqual(self.page.locator("[data-pt-goal]").input_value(), "PRESERVED_PROJECT_FILE_GOAL")
        self.page.locator("[data-pt-executor]").select_option(index=1)
        self.page.locator("[data-pt-prepare]").click()
        self.page.locator("[data-pt-error]").filter(has_text="文件尚未保存").wait_for()
        self.assertEqual(self.page.locator("[data-pt-goal]").input_value(), "PRESERVED_PROJECT_FILE_GOAL")
        self.assertEqual(self.page.locator("#flEditText").input_value(), "UNSAVED_PROJECT_FILE")
        self.assertEqual(self.page.request.get(self.url + "api/project-tasks?project_id=" + self.pid).json()["tasks"], [])
        self.assertEqual(self.errors, [])

    def test_prepared_workflow_defers_execution_and_mode_change_advances_distinct_task_nodes(self):
        self.seed()
        binding = self.grant()
        with TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.evaluate("""async data=>{
              const saved=await api('/api/workflow/layouts/save',{name:'项目自定义节点协作',
                nodes:[{id:'analysis',step:'task',label:'材料分析',executor_role_id:'analyst',review_required:true,prompt:'分析材料'},
                       {id:'verification',step:'task',label:'核查结论',executor_role_id:'analyst',review_required:true,prompt:'核查上游交付'}],
                edges:[{source:'analysis',target:'verification'}],order:['analysis','verification']});
              await api('/api/project-tasks/workflow-roles',{project_id:data.pid,template_id:saved.layout.id,role_id:'analyst',executor_binding_id:data.binding});
              await api('/api/workflow/layouts/select',{task_id:data.pid,layout_id:saved.layout.id});
              ConnectedResearch.openRun(data.pid);
            }""", {"pid": self.pid, "binding": binding})
            with self.page.expect_response(lambda response: response.url.endswith('/api/workflow/execution/prepare')) as response_info:
                self.page.locator("[data-sequence-prepare]").click()
            response = response_info.value
            self.assertTrue(response.ok, response.text())
            prepared = self.poll_json('/api/workflow/execution?task_id=' + self.pid,
                lambda data: bool((data.get("run") or {}).get("settings", {}).get("task_ids")))["run"]
            self.assertEqual(model.requests, [])
            self.assertEqual(prepared["status"], "queued")
            self.assertEqual(len(set(prepared["settings"]["task_ids"].values())), 2)
            self.page.locator("[data-sequence-start-prepared]").click()
            waiting = self.poll_json('/api/workflow/execution?task_id=' + self.pid,
                lambda data: (data.get("run") or {}).get("status") in ("awaiting_review", "failed"))["run"]
            self.assertEqual(waiting["status"], "awaiting_review", waiting.get("error"))
            self.page.locator("[data-sequence-approve]").wait_for(timeout=15000)
            self.page.locator("[data-sequence-run-mode]").select_option("full_access")
            self.page.locator("[data-sequence-mode-apply]").click()
            completed = self.poll_json('/api/workflow/execution?task_id=' + self.pid,
                lambda data: (data.get("run") or {}).get("status") in ("succeeded", "failed"))["run"]
            self.assertEqual(completed["status"], "succeeded", completed.get("error"))
            self.assertEqual([node["status"] for node in completed["nodes"]], ["delivered", "delivered"])
            self.assertTrue(all(node["checkpoint"]["delivery_source"] == "policy" for node in completed["nodes"]))
            self.page.get_by_text("全部完成", exact=True).wait_for(timeout=15000)
            self.assertEqual(self.page.locator("[data-sequence-decision-source]").filter(has_text="Full access").count(), 2)
            self.assertNotIn("[object Object]", self.page.locator("dialog.connected-dialog").inner_text())
        self.assertEqual(self.errors, [])
