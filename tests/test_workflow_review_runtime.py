"""Chat tool call to graph editing, acceptance and rework using real HTTP and Edge."""
import http.server
import json
import os
from pathlib import Path
import threading
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
import workflow
import workflow_layout
import project_bindings


class WorkflowModel:
    def __init__(self, template_id):
        self.template_id = template_id
        self.calls = []
        self.node_requests = []
        self.run_id = ''

    def __enter__(self):
        fixture = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                last = payload['messages'][-1]
                name, args = '', {}
                if any('执行当前项目任务「Draft report」' in str(m.get('content')) for m in payload['messages'] if m['role'] == 'system'):
                    fixture.node_requests.append(payload)
                    delta = {'content': '# Research report\n\nBROWSER_GENERATED_REPORT'}
                else:
                    previous = last.get('name', '')
                    if last['role'] == 'user':
                        name = 'workflow_templates'
                    elif previous == 'workflow_templates':
                        name, args = 'workflow_prepare', {'template_id': fixture.template_id, 'goal': 'Review the evidence and report', 'request_key': 'browser-workflow'}
                    elif previous == 'workflow_prepare':
                        fixture.run_id = json.loads(last['content'])['run']['id']
                        name, args = 'workflow_start', {'id': fixture.run_id}
                    elif previous == 'workflow_start':
                        name, args = 'workflow_status', {'id': fixture.run_id}
                    if name:
                        fixture.calls.append(name)
                    delta = {'tool_calls': [{'index': 0, 'id': 'call-' + name, 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(args)}}]} if name else {'content': '工作流已启动，请在下方审查节点。'}
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                self.wfile.write(('data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': 'tool_calls' if name else 'stop'}]}) + '\n\ndata: [DONE]\n\n').encode())
                self.wfile.flush()
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        return self

    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close()


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowReviewRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed
    use_model = workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json = workspace.ProjectWorkspaceRuntimeTest.poll_json

    def capture(self, name):
        if folder := os.environ.get('RW_WORKFLOW_EVIDENCE'):
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target / (name + '.png')), full_page=True)

    def test_failed_project_chat_preserves_draft_without_orphan_records(self):
        self.seed()
        managed = self.temporary_root / 'managed-data'
        managed.mkdir()
        (managed / 'zotero.sqlite').touch()
        with workflow.WorkflowDB(self.database_path) as db:
            db.db.execute('UPDATE research_tasks SET base_dir=? WHERE id=?', (str(managed / 'storage'), self.pid))
            db.db.commit()
            before = {chat['id'] for chat in db.chats()}
        for _ in range(2):
            self.page.locator('#pwAsk').fill('你可以做什么')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwError').textContent.includes('Zotero')&&!document.querySelector('#pwSend').disabled")
            self.assertEqual(self.page.locator('#pwAsk').input_value(), '你可以做什么')
            with workflow.WorkflowDB(self.database_path) as db:
                self.assertEqual({chat['id'] for chat in db.chats()}, before)
        self.assertFalse((managed / 'storage').exists())
        self.assertEqual(self.errors, [])

    def test_chat_review_graph_save_resume_return_and_rework(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            # Reproduce the user's ordinary project under directories named Zotero.
            root = self.temporary_root / 'zotero/zotero/LLM'
            root.mkdir(parents=True)
            db.db.execute('UPDATE research_tasks SET base_dir=? WHERE id=?', (str(root), self.pid))
            db.db.commit()
            workflow_layout.ensure_schema(db)
            team = db.team_add('Report team', 'custom', project_id=self.pid)
            db.team_member_add(team['id'], 'Report expert', 'leader', assistant_id='workspace-writer')
            team_binding = project_bindings.register(db, self.pid, 'team', team['id'])['id']
            child = workflow_layout.save_layout(db, {'name': 'Nested evidence check', 'nodes': [
                {'id': 'validate', 'step': 'task', 'label': 'Check evidence', 'executor_binding_id': self.binding,
                 'node_kind': 'tool', 'tool_name': 'library_overview', 'tool_args': {}}], 'edges': []})['layout']
            layout = workflow_layout.save_layout(db, {'name': 'Evidence review loop', 'nodes': [
                {'id': 'evidence', 'step': 'map', 'label': 'Evidence graph', 'executor_binding_id': self.binding,
                 'node_kind': 'tool', 'tool_name': 'map_write', 'tool_args': {'data': {
                     'nodes': [{'id': 'claim', 'type': 'section', 'label': 'Original claim'},
                               {'id': 'data', 'type': 'data', 'label': 'Evidence source'}],
                     'edges': [{'from': 'claim', 'to': 'data', 'label': 'supports'}]}}},
                {'id': 'draft', 'step': 'task', 'label': 'Draft report', 'executor_binding_id': team_binding},
                {'id': 'check', 'step': 'task', 'label': 'Check subworkflow',
                 'node_kind': 'workflow', 'workflow_template_id': child['id']}],
                'edges': [{'source': 'evidence', 'target': 'draft'}, {'source': 'draft', 'target': 'check'}]})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
        with WorkflowModel(layout['id']) as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('START_WORKFLOW')
            self.page.locator('#pwSend').click()
            data = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                lambda d: any(t['workflow_node_id'] == 'evidence' and t['status'] == 'awaiting_review' for t in d['tasks']))
            ids = {t['workflow_node_id']: t['id'] for t in data['tasks']}
            first_id, draft_id, next_id = (ids[n] for n in ('evidence', 'draft', 'check.validate'))
            self.page.locator('[data-wr-task="' + first_id + '"]').first.wait_for(timeout=25000)
            self.page.reload(wait_until='networkidle')
            self.page.locator('[data-wr-task="' + first_id + '"]').first.click()
            self.page.locator('[data-pw-graph-edit]').click()
            self.page.locator('[data-wr-node="claim"]').click()
            self.page.locator('[data-wr-label]').fill('BROWSER_VERIFIED_CLAIM')
            self.page.locator('[data-wr-detail]').fill('Checked experimental measurements and source.')
            self.capture('graph-review')
            self.page.locator('[data-wr-save]').click()
            self.page.locator('dialog[aria-label="修订节点证据链"]').wait_for(state='detached')
            self.page.get_by_text('人工修订 v1 · 核查以当前产物为准', exact=True).wait_for()
            self.page.get_by_role('button', name='定位工作流节点', exact=True).click()
            self.page.locator('[data-pwg-mode="run"][aria-pressed="true"]').wait_for()
            self.assertIn('evidence', self.page.locator('.pwg-node-heading').inner_text())
            self.page.locator('[data-pwg-task="' + first_id + '"]').click()
            self.page.locator('#pwApprove').click()
            self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + draft_id,
                           lambda data: data['task']['status'] == 'awaiting_review')
            self.page.get_by_role('button', name='返回发起对话', exact=True).click()
            self.page.locator('[data-wr-task="' + draft_id + '"]').first.click()
            self.page.locator('[data-pw-output-edit]').first.click()
            self.page.locator('[data-por-text]').fill('# Reviewed report\n\nBROWSER_REVIEWED_REPORT')
            self.capture('file-review')
            self.page.locator('[data-por-save]').click()
            self.page.locator('dialog[aria-label="人工修订产物"]').wait_for(state='detached')
            self.page.locator('#pwApprove').click()
            self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + next_id,
                           lambda data: data['task']['status'] == 'awaiting_review')
            self.page.get_by_role('button', name='返回发起对话', exact=True).click()
            self.page.locator('[data-wr-task="' + next_id + '"]').first.click()
            self.page.locator('#pwFeedback').fill('Please check once more with my feedback.')
            self.page.locator('#pwRejectRetry').click()
            retried = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + next_id,
                lambda data: data['task']['status'] == 'awaiting_review' and data['task']['latest_run']['attempt'] == 2)['task']
            self.assertIn('Please check once more', retried['latest_run']['input_snapshot']['feedback'])
            self.page.get_by_role('button', name='返回发起对话', exact=True).click()
            self.page.locator('[data-wr-task="' + next_id + '"]').first.click()
            self.page.locator('#pwApprove').click()
            completed = self.poll_json('/api/workflow/execution?id=' + model.run_id,
                                      lambda data: data.get('run', {}).get('status') == 'succeeded')['run']
            self.assertEqual(completed['settings']['checkpoint']['next'], [])
            self.assertEqual(completed['settings']['checkpoint']['interrupts'], [])
            first = self.page.request.get(self.url + 'api/project-tasks/detail?project_id=' + self.pid + '&id=' + first_id).json()['task']
            self.assertEqual(len(first['runs']), 1)
            self.assertIn('BROWSER_VERIFIED_CLAIM', Path(first['outputs'][0]['path']).read_text(encoding='utf-8'))
            final_tasks = self.page.request.get(self.url + 'api/project-tasks?project_id=' + self.pid).json()['tasks']
            self.assertEqual({task['status'] for task in final_tasks}, {'delivered'})
            self.assertTrue(all(task['deliveries'][-1]['source'] == 'human' for task in final_tasks))
            report = next(task for task in final_tasks if task['id'] == draft_id)
            self.assertIn('BROWSER_REVIEWED_REPORT', Path(report['outputs'][0]['path']).read_text(encoding='utf-8'))
            self.assertIn('BROWSER_VERIFIED_CLAIM', json.dumps(model.node_requests, ensure_ascii=False))
            self.assertEqual(len(model.node_requests), 1)
            self.assertEqual(model.calls, ['workflow_templates', 'workflow_prepare', 'workflow_start', 'workflow_status'])
            self.page.get_by_role('button', name='返回发起对话', exact=True).click()
            self.page.reload(wait_until='networkidle')
            self.page.get_by_text('工作流已完成', exact=True).first.wait_for()
            self.capture('workflow-complete')
            persisted = self.page.request.get(self.url + 'api/workflow/execution?id=' + model.run_id).json()['run']
            self.assertEqual(persisted['status'], 'succeeded')
            with workflow.WorkflowDB(self.database_path) as db:
                chats = [chat for chat in db.chats() if chat['task_id'] == self.pid]
                self.assertEqual(len(chats), 1 + len(final_tasks) + 1)
                self.assertIn(persisted['chat_id'], {chat['id'] for chat in chats})
            self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
