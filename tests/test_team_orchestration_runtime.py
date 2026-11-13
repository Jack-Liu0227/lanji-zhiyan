"""A team goal crosses the browser, real tool calls and local concurrent model I/O."""
import http.server
import json
from pathlib import Path
import threading
import time
import unittest

from tests import test_web_runtime as runtime
import workflow


class TeamModelFixture:
    def __init__(self, members):
        self.members = members
        self.live = 0
        self.maximum = 0
        self.lock = threading.Lock()
        self.both = threading.Event()

    def __enter__(self):
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                system = '\n'.join(str(m.get('content', '')) for m in data['messages'] if m['role'] == 'system')
                calls = []
                if '你是负责人派发的独立研究专家' in system:
                    with fixture.lock:
                        fixture.live += 1
                        fixture.maximum = max(fixture.maximum, fixture.live)
                        if fixture.live == 2:
                            fixture.both.set()
                    fixture.both.wait(8)
                    time.sleep(.1)
                    with fixture.lock:
                        fixture.live -= 1
                    reply = 'REAL_TEAM_EVIDENCE，原文第 2 节；证据与局限已核对。'
                elif 'delivery_id' in system:
                    reply = 'REAL_TEAM_FINAL，已汇总两位专家的交付版本与证据。'
                elif any(m['role'] == 'tool' for m in data['messages']):
                    reply = '已派发研究任务，等待成员交付后继续汇总。'
                else:
                    reply = ''
                    for i, member in enumerate(fixture.members):
                        calls.append({'id': 'team-call-' + str(i), 'type': 'function', 'function': {
                            'name': 'task_create', 'arguments': json.dumps({'member_id': member['id'],
                            'title': '独立证据 ' + str(i + 1), 'goal': '检索独立来源，说明证据定位和局限',
                            'request_key': 'evidence-' + str(i)}, ensure_ascii=False)}})
                message = {'role': 'assistant', 'content': reply}
                if calls:
                    message['tool_calls'] = calls
                finish = 'tool_calls' if calls else 'stop'
                streaming = data.get('stream')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream' if streaming else 'application/json')
                self.end_headers()
                if streaming:
                    delta = {'tool_calls': [dict(call, index=i) for i, call in enumerate(calls)]} if calls else {'content': reply}
                    chunk = {'choices': [{'delta': delta, 'finish_reason': finish}]}
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
                else:
                    self.wfile.write(json.dumps({'choices': [{'message': message, 'finish_reason': finish}]}).encode())
                self.wfile.flush()

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1]) + '/v1'
        return self

    def __exit__(self, *_args):
        self.both.set()
        self.server.shutdown()
        self.server.server_close()


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, 'Requires Playwright and Microsoft Edge')
class TeamOrchestrationRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page

    def test_team_goal_runs_parallel_tools_and_persists_final_review(self):
        with workflow.WorkflowDB(self.database_path) as db:
            pid = db.create_task('团队协作验收', base_dir=str(self.temporary_root / 'team-research'))
            office = db.team_add('并行研究团队', 'custom', project_id=pid)
            members = []
            for i in range(3):
                aid = 'runtime-team-expert-' + str(i)
                db.assistant_save(aid, name='研究专家 ' + str(i), enabled=1, permission='full_access')
                member = db.team_member_add(office['id'], '负责人' if i == 0 else '成员 ' + str(i),
                                            'leader' if i == 0 else 'member', assistant_id=aid)
                if i:
                    members.append(member)
        with TeamModelFixture(members) as model:
            with workflow.WorkflowDB(self.database_path) as db:
                db.add_model('Team local fixture', model.url, 'fake', 'fake', activate=True)
            self.load_page()
            self.page.evaluate('pid=>{curTask=pid;}', pid)
            self.page.locator('#sidebar [data-nav="team"]').click()
            self.page.locator('.tof-room[data-office="' + office['id'] + '"]').click()
            self.page.locator('#tofEnter').click()
            self.page.locator('#tcSay').fill('比较两条研究路线，提供可核查证据')
            self.page.locator('#tcSend').click()
            deadline = time.monotonic() + 35
            state = {}
            while time.monotonic() < deadline:
                state = self.page.request.get(self.url + 'api/team/chat?scope=' + office['id']).json()
                if (state.get('orchestration') or {}).get('status') in ('awaiting_review', 'failed'):
                    break
                self.page.wait_for_timeout(100)
            self.assertEqual(state.get('orchestration', {}).get('status'), 'awaiting_review', json.dumps(state, ensure_ascii=False)[-3000:])
            self.assertEqual(model.maximum, 2)
            self.assertEqual(state['orchestration']['counts'].get('delivered'), 2)
            self.page.reload(wait_until='networkidle')
            self.page.locator('.tof-room[data-office="' + office['id'] + '"]').click()
            self.page.locator('#tofEnter').click()
            self.page.locator('#tcExecution').wait_for()
            self.assertEqual(self.page.locator('[data-tc-task]').count(), 2)
            self.assertIn('REAL_TEAM_FINAL', self.page.locator('#tcChat').inner_text())
            evidence = runtime.ROOT / 'output/team-orchestration/20261031'
            evidence.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(evidence / 'team-final-review.png'), full_page=True)
            self.page.locator('#tcExecution').click()
            self.page.locator('#pwApprove').wait_for()
            self.assertTrue(self.page.locator('#pwReview').is_visible())
            with workflow.WorkflowDB(self.database_path) as db:
                import project_tasks
                task = project_tasks.get(db, state['orchestration']['task_id'])
                self.assertEqual(task['accepted_delivery_id'], '')
                self.assertIn('REAL_TEAM_FINAL', Path(task['outputs'][0]['path']).read_text(encoding='utf-8'))
            self.page.locator('#pwApprove').click()
            self.page.wait_for_function("id=>ProjectWorkspace.active()&&document.querySelector('#pwReview')?.hidden", arg=state['orchestration']['task_id'])
            with workflow.WorkflowDB(self.database_path) as db:
                task = project_tasks.get(db, state['orchestration']['task_id'])
                self.assertEqual(task['status'], 'delivered')
                self.assertEqual(task['deliveries'][-1]['source'], 'human')
            self.assertEqual(self.errors, [])
