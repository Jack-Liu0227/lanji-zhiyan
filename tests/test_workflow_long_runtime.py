"""Edge acceptance of a real, locally streamed workflow that waits over 40 seconds.

Run with ``.venv/Scripts/python.exe -m unittest -v tests.test_workflow_long_runtime``.
The application server, model endpoint, database and projects are isolated. No
agent, tool, clock, heartbeat or browser response is patched.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import http.server
import json
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
import workflow
import workflow_layout


class LongSSEModel:
    """Hold the first long-node response until both 40 seconds and UI checks pass."""
    minimum_wait = 40.0

    def __enter__(self):
        self.started, self.release, self.stopped = threading.Event(), threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.requests, self.errors = [], []
        self.started_at = None
        self.held_seconds = None
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    messages = payload['messages']
                    prompt = '\n'.join(str(message.get('content', '')) for message in messages
                                       if message['role'] == 'user')
                    node = 'long' if 'ACCEPTANCE_LONG_NODE' in prompt else 'independent'
                    tools = [message for message in messages if message['role'] == 'tool']
                    with fixture.lock:
                        fixture.requests.append({'node': node, 'payload': payload, 'path': self.path,
                                                 'at': time.monotonic(), 'tools': tools})
                    if not payload.get('stream') or not self.path.endswith('/chat/completions'):
                        raise ValueError('Expected the real streaming chat-completions protocol')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()

                    def send(delta, finish=None):
                        value = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                        self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                        self.wfile.flush()

                    if not tools and node == 'long':
                        fixture.started_at = time.monotonic()
                        send({'reasoning_content': 'LOCAL_SSE_WAITING_FOR_RELEASE'})
                        fixture.started.set()
                        while not fixture.stopped.is_set():
                            elapsed = time.monotonic() - fixture.started_at
                            if fixture.release.is_set() and elapsed >= fixture.minimum_wait:
                                fixture.held_seconds = elapsed
                                break
                            if elapsed > 90:
                                raise TimeoutError('The browser did not release the local SSE fixture')
                            # These are SSE comments, not agent progress or fabricated heartbeat events.
                            self.wfile.write(b': local-fixture-keepalive\n\n')
                            self.wfile.flush()
                            fixture.stopped.wait(0.5)
                        if fixture.stopped.is_set():
                            return
                    if not tools:
                        send({'tool_calls': [{'index': 0, 'id': 'library-' + node, 'type': 'function',
                                             'function': {'name': 'library_overview', 'arguments': '{}'}}]},
                             'tool_calls')
                    else:
                        result = json.loads(tools[-1]['content'])
                        marker = 'LONG_RUNTIME_COMPLETE' if node == 'long' else 'INDEPENDENT_BRANCH_COMPLETE'
                        send({'content': '# 本地工作流验收\n\n' + marker + '\n\n真实工具结果：\n\n```json\n'
                                         + json.dumps(result, ensure_ascii=False, indent=2) + '\n```'}, 'stop')
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    if not fixture.stopped.is_set():
                        with fixture.lock:
                            fixture.errors.append('The real model stream disconnected before completion')
                except Exception as exc:
                    with fixture.lock:
                        fixture.errors.append(type(exc).__name__ + ': ' + str(exc))

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='long-runtime-model')
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def __exit__(self, *_args):
        self.stopped.set()
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Microsoft Edge')
class WorkflowLongRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed
    poll_json = workspace.ProjectWorkspaceRuntimeTest.poll_json

    def shot(self, label):
        directory = web.ROOT / '.codex-tasks/20261123-workflow-monitoring/screenshots'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ('workflow-long-runtime-' + self.stamp + '-' + label + '.png')
        self.page.screenshot(path=str(target), full_page=True)
        self.screenshots.append(str(target.relative_to(web.ROOT)))

    def ledger(self, task_id):
        with closing(sqlite3.connect(self.database_path, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            task = dict(connection.execute('SELECT * FROM project_tasks WHERE id=?', (task_id,)).fetchone())
            attempts = [dict(row) for row in connection.execute(
                'SELECT * FROM project_task_runs WHERE task_id=? ORDER BY attempt', (task_id,))]
            events = [dict(row) for row in connection.execute(
                'SELECT * FROM execution_conversation_events WHERE task_id=? ORDER BY id', (task_id,))]
        for attempt in attempts:
            for key in ('checkpoint', 'input_snapshot', 'output_snapshot', 'executor_snapshot'):
                attempt[key] = json.loads(attempt[key])
        for event in events:
            event['payload'] = json.loads(event['payload'])
        return task, attempts, events

    def context_for(self, chat_id):
        response = self.page.request.get(self.url + 'api/agent-settings/workflow-context',
                                         params={'project_id': self.pid, 'chat_id': chat_id})
        self.assertTrue(response.ok, response.text())
        return response.json()

    def assert_selected_chat(self, chat_id):
        self.page.wait_for_function("id => document.querySelector('#pwMessages')?.dataset.chatId === id", arg=chat_id)
        self.assertEqual(self.page.evaluate("pid => JSON.parse(localStorage.getItem('rw-workspace:'+pid)).chatId", self.pid),
                         chat_id)

    def test_long_dag_keeps_expert_monitoring_across_empty_conversation_and_review(self):
        self.stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.screenshots, samples, ui_polls = [], [], []
        self.seed()
        self.long_attempt = ''

        def capture_poll(response):
            query = parse_qs(urlsplit(response.url).query)
            if '/api/agent-settings/trajectory/events?' not in response.url or query.get('run_id') != [self.long_attempt]:
                return
            if response.ok:
                data = response.json()
                ui_polls.append({'at': time.monotonic(), 'status': data.get('status'),
                                 'monitoring': data.get('monitoring') or {},
                                 'event_ids': [event['event_id'] for event in data.get('events', [])]})

        self.page.on('response', capture_poll)
        with LongSSEModel() as model:
            with workflow.WorkflowDB(self.database_path) as db:
                workflow_layout.ensure_schema(db)
                profile = db.add_model('Long runtime local SSE', model.url, 'long-runtime-fixture', activate=True)
                db.assistant_save('workspace-writer', model_id=profile['id'])
                chat_a = db.create_chat('长任务会话 A', task_id=self.pid)
                chat_b = db.create_chat('空会话 B', task_id=self.pid)
                nodes = [
                    {'id': 'long', 'step': 'task', 'label': '长时证据分析', 'prompt': 'ACCEPTANCE_LONG_NODE',
                     'review_required': True, 'manual_review_required': True},
                    {'id': 'independent', 'step': 'task', 'label': '独立证据核对', 'prompt': 'ACCEPTANCE_INDEPENDENT_NODE',
                     'review_required': False},
                    {'id': 'join', 'step': 'task', 'label': '等待确认后汇合', 'prompt': 'ACCEPTANCE_JOIN_NODE',
                     'review_required': True},
                ]
                for node in nodes:
                    node.update(executor_binding_id=self.binding, tools=['library_overview'])
                layout = workflow_layout.save_layout(db, {'name': '长任务监控验收 DAG', 'nodes': nodes,
                    'edges': [{'source': 'long', 'target': 'join'}, {'source': 'independent', 'target': 'join'}]})['layout']
                workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            self.page.evaluate('''async args => {
                await refreshState(false);
                await ProjectWorkspace.openConversation(args.chat, args.project);
            }''', {'chat': chat_a, 'project': self.pid})
            self.assert_selected_chat(chat_a)
            run = self.page.evaluate("args => api('/api/workflow/execution/start', args)",
                {'project_id': self.pid, 'chat_id': chat_a, 'goal': '本地 SSE 长任务与会话隔离验收',
                 'idempotency_key': 'long-runtime-' + self.stamp, 'autonomy_mode': 'supervised'})['run']
            ids = run['settings']['task_ids']
            self.assertTrue(run['settings']['dag_scheduler'])
            self.assertEqual(run['settings']['origin_chat_id'], chat_a)
            self.assertTrue(model.started.wait(10), 'The real run_agent never reached the local HTTP fixture')
            self.long_attempt = self.ledger(ids['long'])[0]['latest_run_id']
            self.assertTrue(self.long_attempt)
            self.assertIn(run['id'], self.context_for(chat_a)['run_ids'])
            self.assertEqual(self.context_for(chat_b)['task_ids'], [])

            try:
                self.page.evaluate('args => ProjectWorkspace.openWorkflowDock(args.project,args.run,"long")',
                                   {'project': self.pid, 'run': run['id']})
                self.page.locator('[data-wfl-node="long"]').click()
                self.page.locator('[data-pw-expert-tab="trajectory"]').click()
                monitor = self.page.locator('#pwResource [data-wt-monitor]')
                monitor.wait_for(state='visible')
                self.page.wait_for_function("document.querySelector('#pwResource [data-wt-monitor]')?.textContent.length > 0")
                self.assertEqual(self.page.locator('#pwResource [data-wt-runs]').input_value(), self.long_attempt)
                self.assertEqual(set(self.page.locator('[data-pw-dock]').evaluate_all(
                    'buttons => buttons.map(button=>button.dataset.pwDock)')),
                    {'workflow', 'node-result', 'expert-chat', 'file-preview'})
                self.shot('01-a-running')

                switched, restored, late_checked = False, False, False
                heartbeat_in_b = ''
                while time.monotonic() - model.started_at < 41.5:
                    elapsed = time.monotonic() - model.started_at
                    task, attempts, events = self.ledger(ids['long'])
                    heartbeat = attempts[0]['checkpoint'].get('monitoring') or {}
                    visible = self.page.evaluate('''() => {
                        const monitor=document.querySelector('#pwResource [data-wt-monitor]');
                        return {selected_task:document.querySelector('[data-pw-expert-task]')?.value||'',
                            expert_mode:document.querySelector('[data-pw-expert-tab].is-active')?.dataset.pwExpertTab||'',
                            monitor_visible:!!monitor&&monitor.getClientRects().length>0,
                            monitor_text:monitor?.innerText||'',monitor_class:monitor?.className||''};
                    }''')
                    samples.append({'elapsed': elapsed, 'heartbeat_at': heartbeat.get('heartbeat_at'),
                                    'status': task['status'], 'events': len(events),
                                    'chat': 'B' if switched and not restored else 'A', 'ui': visible})
                    self.assertEqual(task['status'], 'running', 'The held node exited before the fixture was released')
                    self.assertEqual(len(attempts), 1)
                    self.assertEqual(sum(event['payload']['type'] == 'model_request' for event in events), 1)
                    self.assertFalse(any(event['payload']['type'] == 'tool_result' for event in events))
                    self.assertFalse(model.release.is_set())

                    if elapsed >= 7 and not switched:
                        self.page.locator('#pwSessions [data-pw-chat="' + chat_b + '"]').click()
                        self.assert_selected_chat(chat_b)
                        with self.subTest(stage='empty conversation B isolates the running DAG'):
                            context = self.context_for(chat_b)
                            self.assertEqual(context['run_ids'], [])
                            self.assertEqual(context['task_ids'], [])
                            self.assertEqual(self.page.locator('#pwResource [data-wt-monitor]:visible').count(), 0)
                            self.assertNotIn('LOCAL_SSE_WAITING_FOR_RELEASE', self.page.locator('#pwMessages').inner_text())
                            self.assertNotIn('LONG_RUNTIME_COMPLETE', self.page.locator('#pwMessages').inner_text())
                        heartbeat_in_b = heartbeat.get('heartbeat_at')
                        switched = True
                        self.shot('02-b-empty-background-running')
                    if elapsed >= 14 and switched and not restored:
                        with self.subTest(stage='background worker keeps pulsing while B is selected'):
                            self.assertGreater(heartbeat.get('heartbeat_at') or '', heartbeat_in_b or '')
                            independent, independent_attempts, _ = self.ledger(ids['independent'])
                            self.assertEqual(independent['status'], 'delivered')
                            self.assertEqual(len(independent_attempts), 1)
                            self.assertFalse(self.ledger(ids['join'])[0]['latest_run_id'])
                        self.page.locator('#pwSessions [data-pw-chat="' + chat_a + '"]').click()
                        self.assert_selected_chat(chat_a)
                        with self.subTest(stage='A restores the selected expert and trajectory without reopening'):
                            monitor.wait_for(state='visible', timeout=8000)
                            self.assertEqual(self.page.locator('[data-pw-dock="expert-chat"]').get_attribute('aria-selected'), 'true')
                            self.assertIn('is-active', self.page.locator('[data-pw-expert-tab="trajectory"]').get_attribute('class'))
                            self.assertEqual(self.page.locator('[data-pw-expert-task]').input_value(), ids['long'])
                            self.assertEqual(self.page.locator('#pwResource [data-wt-runs]').input_value(), self.long_attempt)
                        restored = True
                        self.shot('03-a-restored')
                    if elapsed >= 38 and restored and not late_checked:
                        with self.subTest(stage='expert monitoring continues after 36 seconds'):
                            self.assertEqual(visible['selected_task'], ids['long'], visible)
                            self.assertEqual(visible['expert_mode'], 'trajectory', visible)
                            self.assertTrue(visible['monitor_visible'], visible)
                            self.assertTrue(visible['monitor_text'].strip(), 'Worker heartbeat is not visible')
                            self.assertNotIn('is-stale', visible['monitor_class'])
                            late_polls = [poll for poll in ui_polls if poll['at'] - model.started_at > 36]
                            self.assertTrue(late_polls, 'The actual browser stopped polling trajectory after 36 seconds')
                            self.assertTrue(late_polls[-1]['monitoring']['worker_alive'])
                            self.assertTrue(late_polls[-1]['monitoring']['heartbeat_at'])
                            self.assertGreater(len({sample['heartbeat_at'] for sample in samples if sample['heartbeat_at']}), 5)
                            age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                                late_polls[-1]['monitoring']['heartbeat_at'])).total_seconds()
                            self.assertLess(age, 15)
                        with self.subTest(stage='heartbeat monitor has readable status labels'):
                            self.assertTrue(visible['monitor_text'], 'The selected expert monitor disappeared')
                            self.assertNotIn('????', visible['monitor_text'], 'Heartbeat status labels are corrupted')
                        self.shot('04-a-monitor-after-36s')
                        late_checked = True
                    self.page.wait_for_timeout(500)

                self.assertTrue(switched and restored and late_checked, 'The controlled wait skipped a required live observation')
                model.release.set()
                final = self.poll_json('/api/workflow/execution?id=' + run['id'],
                    lambda data: data['run']['status'] in ('failed', 'blocked') or (
                        data['run']['status'] == 'awaiting_review' and bool(data['run']['settings'].get('checkpoint'))), timeout=30)['run']
                self.assertEqual(final['status'], 'awaiting_review', final['error'])
                self.assertGreaterEqual(model.held_seconds, model.minimum_wait)
                self.assertEqual(final['settings']['checkpoint']['next'], ['pause'])
                self.assertEqual(final['settings']['checkpoint']['interrupts'][0]['node_id'], 'long')
                self.assertFalse(self.ledger(ids['join'])[0]['latest_run_id'])
                self.assertEqual(model.errors, [])

                counts = {}
                for nid, expected in [('long', 'awaiting_review'), ('independent', 'delivered')]:
                    task, attempts, events = self.ledger(ids[nid])
                    self.assertEqual(task['status'], expected)
                    self.assertEqual(len(attempts), 1, 'Switching conversations must not create another attempt')
                    attempt = attempts[0]
                    self.assertEqual(attempt['status'], 'succeeded')
                    self.assertEqual(attempt['executor_snapshot']['runtime_config']['model_id'], profile['id'])
                    self.assertEqual(attempt['executor_snapshot']['runtime_config']['model_identity']['model'],
                                     'long-runtime-fixture')
                    payloads = [event['payload'] for event in events]
                    kinds = Counter(payload['type'] for payload in payloads)
                    counts[nid] = dict(kinds)
                    for kind, amount in [('model_request', 2), ('model_response', 2), ('tool_call', 1), ('tool_result', 1)]:
                        self.assertEqual(kinds[kind], amount, (nid, kind, kinds))
                    self.assertEqual(kinds['error'], 0)
                    for event in events:
                        self.assertEqual(event['task_id'], ids[nid])
                        self.assertEqual(event['run_id'], attempt['id'])
                        self.assertEqual(event['payload']['task_run_id'], attempt['id'])
                        self.assertEqual(event['payload']['workflow_node_id'], nid)
                        self.assertEqual(event['payload']['attempt'], 1)
                    tool = next(payload for payload in payloads if payload['type'] == 'tool_result')
                    self.assertEqual(tool['name'], 'library_overview')
                    self.assertTrue(tool['ok'])
                    self.assertEqual(tool['result']['任务'], '研究工作区验收')
                    self.assertEqual(tool['result']['文献'], 0)
                    self.assertIn('检索结果', tool['result'])
                    requests = [request for request in model.requests if request['node'] == nid]
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(requests[0]['tools'], [])
                    self.assertEqual(len(requests[1]['tools']), 1)
                    self.assertEqual(requests[1]['tools'][0]['tool_call_id'], 'library-' + nid)
                    self.assertEqual(json.loads(requests[1]['tools'][0]['content']), tool['result'])
                    marker = 'LONG_RUNTIME_COMPLETE' if nid == 'long' else 'INDEPENDENT_BRANCH_COMPLETE'
                    self.assertIn(marker, attempt['checkpoint']['reply'])
                    files = attempt['output_snapshot']['files']
                    self.assertTrue(files)
                    for file in files:
                        path = Path(file['path']).resolve()
                        self.assertTrue(path.is_relative_to(self.temporary_root.resolve()))
                        self.assertTrue(path.is_file())
                    self.assertIn(marker, Path(files[0]['path']).read_text(encoding='utf-8'))
                self.assertEqual(len(model.requests), 4)
                print('\nLONG_RUNTIME_BACKEND_EVIDENCE ' + json.dumps({
                    'held_seconds': model.held_seconds, 'status': final['status'],
                    'model_requests': len(model.requests), 'event_counts': counts,
                    'database_samples': len(samples), 'heartbeat_versions': len({sample['heartbeat_at'] for sample in samples}),
                    'browser_polls_after_36s': sum(poll['at'] - model.started_at > 36 for poll in ui_polls),
                    'last_ui': samples[-1]['ui']}, ensure_ascii=True), flush=True)

                with self.subTest(stage='final expert trajectory remains selected'):
                    self.assertEqual(self.page.locator('[data-pw-expert-task]').input_value(), ids['long'])
                    self.assertIn('is-active', self.page.locator('[data-pw-expert-tab="trajectory"]').get_attribute('class'))
                # Record the live-view failure first, then inspect durable replay and
                # the actual artifact even if the application lost the selected node.
                if self.page.locator('[data-pw-expert-task]').input_value() != ids['long']:
                    self.page.locator('[data-pw-expert-task]').select_option(ids['long'])
                if 'is-active' not in (self.page.locator('[data-pw-expert-tab="trajectory"]').get_attribute('class') or ''):
                    self.page.locator('[data-pw-expert-tab="trajectory"]').click()

                tool_row = self.page.locator('#pwResource [data-wt-record]').filter(has_text='library_overview').first
                tool_row.wait_for(timeout=10000)
                self.page.locator('#pwResource .wt-event.success').filter(has_text='library_overview').wait_for(timeout=10000)
                tool_row.click()
                self.assertIn('研究工作区验收', self.page.locator('#pwResource [data-wt-inspector]').inner_text())
                self.assertEqual(self.page.locator('#pwResource [data-wt-error]').inner_text(), '')
                self.shot('05-real-tool-result-awaiting-review')
                self.page.locator('[data-pw-expert-tab="artifact"]').click()
                self.page.locator('[data-pw-preview-art]').first.click()
                self.page.locator('[data-wfp-body]').filter(has_text='LONG_RUNTIME_COMPLETE').wait_for(timeout=10000)
                self.assertEqual(self.page.locator('[data-pw-dock="file-preview"]').get_attribute('aria-selected'), 'true')
                self.assertIn(self.long_attempt, self.page.locator('[data-wfp-source]').inner_text())
                self.shot('06-final-artifact-preview')
                with closing(sqlite3.connect(self.database_path)) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM chat WHERE task_id=?', (chat_b,)).fetchone()[0], 0)
                    self.assertGreater(connection.execute('SELECT COUNT(*) FROM checkpoints WHERE thread_id=?', (run['id'],)).fetchone()[0], 1)
                self.assertEqual(self.context_for(chat_b)['run_ids'], [])
                self.assertEqual(self.errors, [])
                print('\nLONG_RUNTIME_EVIDENCE ' + json.dumps({
                    'edge': str(web.EDGE), 'held_seconds': model.held_seconds,
                    'status': final['status'], 'model_requests': len(model.requests), 'event_counts': counts,
                    'database_samples': len(samples), 'heartbeat_versions': len({sample['heartbeat_at'] for sample in samples}),
                    'browser_polls_after_36s': sum(poll['at'] - model.started_at > 36 for poll in ui_polls),
                    'screenshots': self.screenshots}, ensure_ascii=True), flush=True)
            except BaseException:
                self.shot('failure')
                print('\nLONG_RUNTIME_DIAGNOSTIC ' + json.dumps({
                    'elapsed': time.monotonic() - model.started_at, 'page_errors': self.errors,
                    'screen': self.page.locator('#pwResource').inner_text()[:3000],
                    'samples': samples[-4:], 'screenshots': self.screenshots}, ensure_ascii=True), flush=True)
                raise


if __name__ == '__main__':
    unittest.main()
