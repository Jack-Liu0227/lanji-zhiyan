"""Drive completed Agent workflow results through the actual page and HTTP services."""
from pathlib import Path
import json
import os
import runpy
import unittest
from unittest.mock import patch

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_workflow_review_runtime import WorkflowModel
import workflow
import workflow_execution
import workflow_layout
import workflow_stages


def run_fixture_server():
    server = runpy.run_path('apps/desktop/server.py')
    search = workflow_stages.search
    enrich = workflow_stages.enrich_oa

    def fixture_search(query, sources, rows):
        if query == 'Verified literature':
            return [{'source': 'manual', 'title': query, 'abstract': 'Evidence abstract', 'doi': '10.1234/test'}]
        return search(query, sources, rows)

    def fixture_enrich(rows):
        if rows and all(row.get('doi') == '10.1234/test' for row in rows):
            return 0
        return enrich(rows)

    with patch.object(workflow_stages, 'search', fixture_search), patch.object(workflow_stages, 'enrich_oa', fixture_enrich):
        return server['main']()


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowNodeWorkbenchRuntimeTest(unittest.TestCase):
    source_server_args = ('-c', 'from tests.test_workflow_node_workbench_runtime import run_fixture_server; '
                               'raise SystemExit(run_fixture_server())')
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed
    use_model = workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json = workspace.ProjectWorkspaceRuntimeTest.poll_json

    def capture(self, name):
        if folder := os.environ.get('RW_NODE_WORKBENCH_EVIDENCE'):
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(target / (name + '.png')), full_page=True)

    def layout(self):
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            layout = workflow_layout.save_layout(db, {
                'name': 'Node result workbench',
                'nodes': [
                    {'id': 'draft', 'step': 'task', 'label': 'Draft report',
                     'executor_binding_id': self.binding, 'manual_review_required': True},
                    {'id': 'tool', 'step': 'task', 'label': 'Check project materials',
                     'executor_binding_id': self.binding, 'node_kind': 'tool',
                     'tool_name': 'library_overview', 'tool_args': {}, 'manual_review_required': True},
                ],
                'edges': [{'source': 'draft', 'target': 'tool'}],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            return layout

    def tasks(self):
        return self.page.request.get(self.url + 'api/project-tasks?project_id=' + self.pid).json()['tasks']

    def start(self, model):
        self.use_model(model.url)
        self.page.locator('#pwAsk').fill('START_NODE_RESULT_WORKBENCH')
        self.page.locator('#pwSend').click()
        data = self.poll_json('/api/project-tasks?project_id=' + self.pid,
            lambda data: any(task['workflow_node_id'] == 'draft' and task['status'] == 'awaiting_review'
                             and task.get('node_result', {}).get('available') for task in data['tasks']))
        return {task['workflow_node_id']: task for task in data['tasks']}

    def test_completed_node_draft_revision_rework_and_next_node(self):
        self.seed()
        layout = self.layout()
        with WorkflowModel(layout['id']) as model:
            tasks = self.start(model)
            draft, following = tasks['draft'], tasks['tool']
            unavailable = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + following['id'])
            self.assertGreaterEqual(unavailable.status, 400)
            self.assertFalse(following.get('node_result', {}).get('available'))
            self.assertEqual(self.page.locator('[data-wr-result="' + following['id'] + '"]').count(), 0)
            self.page.locator('[data-wr-result="' + draft['id'] + '"]').first.click()
            self.page.locator('.wnw').wait_for()
            self.page.locator('[data-wnw-mode="edit"]').click()
            self.page.locator('[data-wnw-editor]').fill('# Reviewed report\n\nBROWSER_WORKBENCH_DRAFT')
            self.assertTrue(self.page.evaluate("""() => {
                const field=document.querySelector('[data-wnw-editor]');
                field.dispatchEvent(new Event('input',{bubbles:true}));
                return ProjectWorkspace.hasDirty();
            }"""))
            self.assertTrue(self.page.evaluate('async()=>await ProjectWorkspace.flush()'))
            self.assertFalse(self.page.evaluate('WorkflowNodeWorkbench.dirty()'))
            self.capture('node-text-edit')
            self.assertTrue(self.page.locator('[data-wnw-approve]').is_disabled())
            self.page.locator('[data-wnw-draft]').click()
            self.page.wait_for_function("document.querySelector('[data-wnw-draft-state]').textContent.includes('草稿已保存')")
            self.page.reload(wait_until='networkidle')
            self.page.locator('.wnw').wait_for()
            self.page.locator('[data-wnw-mode="edit"]').click()
            self.assertIn('BROWSER_WORKBENCH_DRAFT', self.page.locator('[data-wnw-editor]').input_value())
            self.page.locator('[data-wnw-reason]').fill('Checked source evidence in the node result')
            self.page.locator('[data-wnw-save]').click()
            self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
            self.page.locator('[data-wnw-mode="diff"]').click()
            self.page.locator('.wnw-added').first.wait_for()
            self.capture('node-version-diff')
            self.assertIn('BROWSER_WORKBENCH_DRAFT', self.page.locator('[data-wnw-diff-body]').inner_text())
            self.page.locator('[data-wnw-instruction]').fill('CHECK_CURRENT_WORKBENCH_REVISION and preserve its evidence')
            self.page.locator('[data-wnw-rework]').click()
            updated = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + draft['id'],
                lambda data: data['task']['status'] == 'awaiting_review' and data['task']['latest_run']['attempt'] == 2)['task']
            self.assertEqual(updated['id'], draft['id'])
            self.assertEqual(updated['workflow_run_id'], draft['workflow_run_id'])
            self.assertIn('CHECK_CURRENT_WORKBENCH_REVISION', str(updated['latest_run']['input_snapshot']))
            self.page.wait_for_function("document.querySelector('[data-wnw-approve]') && !document.querySelector('[data-wnw-approve]').disabled")
            self.page.locator('[data-wnw-approve]').wait_for()
            self.page.locator('[data-wnw-approve]').click()
            following = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + following['id'],
                lambda data: data['task']['status'] == 'awaiting_review')['task']
            self.assertTrue(following['node_result']['available'])
            self.page.locator('[data-wnw-origin]').click()
            self.page.locator('[data-wr-result="' + following['id'] + '"]').first.click()
            self.page.locator('[data-wnw-tool-fields]').wait_for()
            self.capture('node-fixed-tool')
            self.assertTrue(self.page.locator('[data-wnw-save]').is_disabled())
            self.page.locator('[data-wnw-approve]').click()
            self.poll_json('/api/workflow/execution?id=' + model.run_id,
                lambda data: data['run']['status'] == 'succeeded')
            with workflow.WorkflowDB(self.database_path) as db:
                row = db.db.execute('SELECT output_snapshot FROM project_task_runs WHERE id=?', (draft['latest_run_id'],)).fetchone()
                self.assertIn('BROWSER_WORKBENCH_DRAFT', row['output_snapshot'])
            self.assertIn('BROWSER_GENERATED_REPORT', Path(draft['outputs'][0]['path']).read_text(encoding='utf-8'))
            self.assertTrue(any('BROWSER_WORKBENCH_DRAFT' in str(request) for request in model.node_requests))
        self.assertEqual(self.errors, [])

    def test_other_window_revision_keeps_viewed_identity_and_draft(self):
        self.seed()
        layout = self.layout()
        with WorkflowModel(layout['id']) as model:
            task = self.start(model)['draft']
            self.page.locator('[data-wr-result="' + task['id'] + '"]').first.click()
            self.page.locator('[data-wnw-meta]').wait_for()
            identity = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']['identity']
            self.page.locator('[data-wnw-mode="edit"]').click()
            self.page.locator('[data-wnw-editor]').fill('UNSAVED_WINDOW_CONTENT')
            self.page.locator('[data-wnw-draft]').click()
            self.page.wait_for_function("document.querySelector('[data-wnw-draft-state]').textContent.includes('草稿已保存')")
            result = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
            artifact = next(item for item in result['artifacts'] if item['id'].startswith('file:') and item.get('editable'))
            changed = self.page.request.post(self.url + 'api/project-tasks/node-command', data={
                'project_id': self.pid, 'id': task['id'], 'action': 'revise', 'identity': identity,
                'artifact_id': artifact['id'], 'text': 'OTHER_WINDOW_NEW_REVISION',
                'reason': 'Concurrent human edit', 'request_key': 'browser-other-window-revision'})
            self.assertTrue(changed.ok, changed.text())
            self.page.locator('[data-wnw-latest]').wait_for(state='visible', timeout=15000)
            self.assertIn('修订 v0', self.page.locator('[data-wnw-meta]').inner_text())
            self.assertEqual(self.page.locator('[data-wnw-editor]').input_value(), 'UNSAVED_WINDOW_CONTENT')
            self.assertTrue(self.page.locator('[data-wnw-approve]').is_disabled())
            self.page.locator('[data-wnw-save]').click()
            self.page.locator('[data-wnw-error]').filter(has_text='版本').wait_for()
            self.assertEqual(self.page.locator('[data-wnw-editor]').input_value(), 'UNSAVED_WINDOW_CONTENT')
            self.capture('node-concurrent-version-conflict')
            self.page.locator('[data-wnw-latest]').click()
            self.page.locator('[data-wnw-stale-drafts]').wait_for(state='visible')
            self.assertIn('修订 v1', self.page.locator('[data-wnw-meta]').inner_text())
            self.page.locator('[data-wnw-stale-draft="0"] summary').click()
            self.assertIn('UNSAVED_WINDOW_CONTENT', self.page.locator('[data-wnw-stale-draft="0"]').inner_text())
            self.page.locator('[data-wnw-restore-draft="0"]').click()
            self.assertEqual(self.page.locator('[data-wnw-editor]').input_value(), 'UNSAVED_WINDOW_CONTENT')
            self.assertIn('修订 v1', self.page.locator('[data-wnw-meta]').inner_text())
            self.assertTrue(self.page.locator('[data-wnw-approve]').is_disabled())
            still_current = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
            self.assertEqual(still_current['content'], 'OTHER_WINDOW_NEW_REVISION')
            self.capture('node-stale-draft-restored')
            await_current = still_current
            self.page.evaluate('async value=>await ProjectWorkspace.openTask(value.id,value.project,{preferWorkbench:false})', {'id': task['id'], 'project': self.pid})
            newer = self.page.request.post(self.url + 'api/project-tasks/node-command', data={
                'project_id': self.pid, 'id': task['id'], 'action': 'revise', 'identity': await_current['identity'],
                'artifact_id': artifact['id'], 'text': 'DETAIL_WINDOW_NEW_REVISION',
                'request_key': 'browser-detail-window-revision'})
            self.assertTrue(newer.ok, newer.text())
            self.page.locator('[data-pw-review-reload]').wait_for(state='visible', timeout=15000)
            self.assertTrue(self.page.locator('#pwApprove').is_disabled())
            self.page.locator('#pwTaskAdvanced').click()
            self.page.locator('[data-pt-action="approve"]').wait_for()
            latest = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
            newest = self.page.request.post(self.url + 'api/project-tasks/node-command', data={
                'project_id': self.pid, 'id': task['id'], 'action': 'revise', 'identity': latest['identity'],
                'artifact_id': artifact['id'], 'text': 'ADVANCED_WINDOW_NEW_REVISION',
                'request_key': 'browser-advanced-window-revision'})
            self.assertTrue(newest.ok, newest.text())
            self.page.locator('[data-pt-review-reload]').wait_for(state='visible', timeout=15000)
            self.assertTrue(self.page.locator('[data-pt-action="approve"]').is_disabled())
            self.capture('node-legacy-review-version-protection')
        self.assertEqual(self.errors, [])

    def test_every_stage_opens_in_workbench_and_passes_edited_artifact_forward(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            nodes = [{'id': f'stage-{index}', 'step': 'task',
                      'label': f'Full stage {index}',
                      'executor_binding_id': self.binding,
                      'manual_review_required': True} for index in range(1, 5)]
            layout = workflow_layout.save_layout(db, {
                'name': 'Full stage artifact chain', 'nodes': nodes,
                'edges': [{'source': f'stage-{index}', 'target': f'stage-{index + 1}'}
                          for index in range(1, 4)],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})

        node_ids = [f'stage-{index}' for index in range(1, 5)]
        with WorkflowModel(layout['id'], artifact_marker='Full stage') as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('START_FULL_STAGE_CHAIN')
            self.page.locator('#pwSend').click()
            data = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                lambda value: all(any(task['workflow_node_id'] == node_id
                                       and task['status'] == 'awaiting_review'
                                       and task.get('node_result', {}).get('available')
                                       for task in value['tasks'])
                                   for node_id in node_ids[:1]))
            task_ids = {task['workflow_node_id']: task['id'] for task in data['tasks']}
            edited = {}
            for index, node_id in enumerate(node_ids):
                task_id = task_ids[node_id]
                task = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + task_id,
                    lambda value: value['task']['status'] == 'awaiting_review'
                    and value['task'].get('node_result', {}).get('available'))['task']
                self.page.locator('[data-wr-result="' + task_id + '"]').first.click()
                self.page.locator('.wnw').wait_for()
                self.page.locator('[data-wnw-mode="edit"]').click()
                marker = f'FULL_STAGE_EDIT_{index + 1}'
                self.page.locator('[data-wnw-editor]').fill('# ' + marker)
                self.page.locator('[data-wnw-reason]').fill('Reviewed this full-stage artifact')
                self.page.wait_for_function("document.querySelector('[data-wnw-save]') && !document.querySelector('[data-wnw-save]').disabled")
                self.page.locator('[data-wnw-save]').click()
                self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
                self.page.wait_for_function("document.querySelector('[data-wnw-approve]') && !document.querySelector('[data-wnw-approve]').disabled")
                self.page.locator('[data-wnw-approve]').click()
                edited[node_id] = marker
                if index + 1 < len(node_ids):
                    next_id = task_ids[node_ids[index + 1]]
                    following = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + next_id,
                        lambda value: value['task']['status'] == 'awaiting_review')['task']
                    self.assertIn(marker, json.dumps(model.node_requests[-1], ensure_ascii=False))
                    self.page.locator('[data-wnw-origin]').click()
            self.poll_json('/api/workflow/execution?id=' + model.run_id,
                lambda value: value.get('run', {}).get('status') == 'succeeded')

            final = self.page.request.get(self.url + 'api/project-tasks?project_id=' + self.pid).json()['tasks']
            for node_id, marker in edited.items():
                current = next(task for task in final if task['workflow_node_id'] == node_id)
                self.assertEqual(current['status'], 'delivered')
                self.assertIn(marker, Path(current['outputs'][0]['path']).read_text(encoding='utf-8'))
            self.assertEqual(len(model.node_requests), 4)
            # The task board projects the same canonical node results and must
            # expose a direct workbench action instead of an execution-status
            # detour.
            self.page.evaluate("async()=>ProjectWorkspace.showResource('tasks')")
            self.page.locator('[data-ec-view="board"]').wait_for()
            self.page.locator('[data-ec-open-artifact]').first.wait_for()
            self.assertGreaterEqual(self.page.locator('[data-ec-open-artifact]').count(), 4)

            # The graph is also a direct artifact entry point once a node has
            # a fixed result; clicking the node must open the workbench rather
            # than stopping at the run-status inspector.
            self.page.evaluate("async value=>ProjectWorkspace.openWorkflowRun(value.project,value.run)",
                               {'project': self.pid, 'run': model.run_id})
            self.page.locator('.project-workflow-graph').wait_for()
            self.page.locator('[data-wfl-node="stage-1"]').click()
            self.page.locator('.wnw').wait_for()
            self.page.wait_for_function("document.querySelector('[data-wnw-title]').textContent.includes('Full stage 1')")
        self.assertEqual(self.errors, [])

    def test_failed_workflow_node_opens_status_and_an_empty_read_only_workbench(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            layout = workflow_layout.save_layout(db, {
                'name': 'Failed search result guard',
                'nodes': [{'id': 'search', 'step': 'search', 'label': 'Failed search',
                           'executor_binding_id': self.binding, 'manual_review_required': True}],
                'edges': [],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})

        with WorkflowModel(layout['id'], artifact_marker='Failed search') as model:
            self.use_model(model.url)
            started = self.page.request.post(self.url + 'api/workflow/execution/start', data={
                'project_id': self.pid, 'template_id': layout['id'], 'goal': 'Check failure without saved search rows',
            })
            self.assertTrue(started.ok, started.text())
            run = self.poll_json('/api/workflow/execution?task_id=' + self.pid,
                lambda value: (value.get('run') or {}).get('status') in ('failed', 'awaiting_review'))['run']
            self.assertEqual(run['status'], 'failed', run)
            self.assertEqual(len(model.node_requests), 1)
            tasks = self.page.request.get(self.url + 'api/project-tasks?project_id=' + self.pid).json()['tasks']
            task = next(item for item in tasks if item['workflow_node_id'] == 'search')
            self.assertEqual(task['status'], 'failed')
            self.assertFalse(task['node_result']['available'])
            self.assertTrue(task['node_result']['workspace'])

            self.page.evaluate('async value=>ProjectWorkspace.openWorkflowRun(value.project,value.run)',
                               {'project': self.pid, 'run': run['id']})
            self.page.locator('.project-workflow-graph').wait_for()
            self.page.locator('[data-pwg-task]').click()
            self.page.locator('.wnw-empty').wait_for()
            self.assertEqual(self.page.locator('[data-wnw-file]').count(), 0)
            self.assertTrue(self.page.locator('[data-wnw-save]').is_disabled())
            self.assertEqual(self.page.locator('[data-wnw-status]').inner_text(), '失败')
        self.assertEqual(self.errors, [])

    def test_composed_run_opens_child_nodes_and_reruns_fixed_tool_with_new_input(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            root = db.base_dir(self.pid)
            source_path = root / 'evidence.md'
            revised_path = root / 'revised-evidence.md'
            revised_path.write_text('SECOND_TOOL_INPUT', encoding='utf-8')
            child = workflow_layout.save_layout(db, {
                'name': 'Read project sources',
                'nodes': [
                    {'id': 'read', 'step': 'task', 'label': 'Read first source', 'node_kind': 'tool',
                     'tool_name': 'read_attachment', 'tool_args': {'path': str(source_path)},
                     'executor_binding_id': self.binding, 'manual_review_required': True},
                    {'id': 'check', 'step': 'task', 'label': 'Check sources', 'node_kind': 'tool',
                     'tool_name': 'library_overview', 'tool_args': {}, 'executor_binding_id': self.binding},
                ],
                'edges': [{'source': 'read', 'target': 'check'}],
            })['layout']
            parent = workflow_layout.save_layout(db, {
                'name': 'Composed reading run',
                'nodes': [{'id': 'reading', 'step': 'task', 'node_kind': 'workflow', 'label': 'Reading workflow',
                           'workflow_template_id': child['id']}],
                'edges': [],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': parent['id']})
            run = workflow_execution.create(db, self.pid, 'Read the project evidence')

        read_id = run['settings']['task_ids']['reading.read']
        check_id = run['settings']['task_ids']['reading.check']
        self.page.locator('#pwTab-workbench').click()
        first_node = self.page.locator('[data-pw-run-list] .pw-output').filter(has_text='Reading workflow / Read first source')
        first_node.get_by_role('button', name='查看节点状态').click()
        self.page.locator('[data-wnw-step="' + check_id + '"]').click()
        self.page.locator('[data-wnw-title]').get_by_text('Reading workflow / Check sources', exact=True).wait_for()
        self.page.locator('[data-wnw-step="' + read_id + '"]').click()
        self.page.locator('[data-wnw-start]').click()
        first = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + read_id,
                               lambda payload: payload['task']['status'] == 'awaiting_review')['task']
        tool_fields = self.page.locator('[data-wnw-tool-fields]')
        tool_fields.locator('summary').click()
        tool_fields.locator('[data-wnw-tool-args]').fill(json.dumps({'path': str(revised_path)}))
        self.page.locator('[data-wnw-instruction]').fill('Read the revised project source')
        self.page.locator('[data-wnw-rework]').click()
        second = self.poll_json('/api/project-tasks/detail?project_id=' + self.pid + '&id=' + read_id,
                                lambda payload: payload['task']['status'] == 'awaiting_review'
                                and payload['task']['latest_run']['attempt'] == 2)['task']
        self.assertEqual(second['workflow_run_id'], run['id'])
        self.assertIn('SAVED_PROJECT_MATERIAL', Path(first['outputs'][0]['path']).read_text(encoding='utf-8'))
        self.assertIn('SECOND_TOOL_INPUT', Path(second['outputs'][0]['path']).read_text(encoding='utf-8'))
        self.assertEqual(self.errors, [])

    def test_ten_typed_stages_preview_edit_and_forward(self):
        """Exercise real persisted stage formats with local model and literature responses."""
        from tests._pdf_fixture import write_text_pdf
        import asset_service
        import papertran
        import service_settings
        import workflow_state
        self.seed()
        stages = ['idea', 'search', 'download', 'notes', 'map', 'outline', 'write', 'review', 'entrans', 'convert']
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            root = db.base_dir(self.pid)
            db.idea_input_save(self.pid, 'Research objective from a verifiable source')
            db.add_note(self.pid, '创新点审查', '# Idea evidence\n\nA testable research question')
            db.cache_result(self.pid, {'source': 'manual', 'title': 'Verified literature', 'abstract': 'Evidence abstract', 'doi': '10.1234/test'})
            source = root / '文献库' / 'verified-paper.pdf'
            source.parent.mkdir(parents=True, exist_ok=True)
            write_text_pdf(source, ['REAL PDF EVIDENCE - page 1', 'REAL PDF EVIDENCE - page 2'])
            paper = db.ingest_pdf(self.pid, source)
            evidence = db.add_evidence(paper['id'], {'page_no': 2, 'field': 'Result', 'value': 'Page two result',
                                                    'excerpt': 'REAL PDF EVIDENCE - page 2'})
            reading_note = db.add_note(self.pid, '精读 · ' + paper['filename'],
                                      '# Page 1 evidence\n\n' + ('Verified page-level evidence. ' * 24), paper['id'])
            dual, mono = papertran.out_paths(source, source.parent)
            for output in (dual, mono):
                write_text_pdf(output, ['TRANSLATED EVIDENCE - page 1', 'TRANSLATED EVIDENCE - page 2'])
            papertran._save_state(source.parent, paper['id'], 2, 2, True)
            source_bytes = source.read_bytes()
            translation_bytes = mono.read_bytes()
            workflow.map_write(db, self.pid, {'nodes': [{'id': 'claim', 'type': 'section', 'label': 'Evidence claim'},
                {'id': 'source', 'type': 'note', 'label': 'Verified source', 'ref': reading_note['id']},
                {'id': 'page-proof', 'type': 'evidence', 'label': 'Page two proof', 'ref': evidence['id']}],
                'edges': [{'from': 'claim', 'to': 'source', 'label': 'supported by'},
                          {'from': 'claim', 'to': 'page-proof', 'label': 'verified on page'}]})
            for stage in ('outline', 'write'):
                workflow.doc_write(db, self.pid, stage, '# ' + stage.title() + ' artifact\n\nVerified source on page 1')
            review = workflow.doc_write(db, self.pid, 'review', '# Review artifact\n\nCheck the source on page 1')
            service_settings.write(db, 'review-checklist', {'document_digest': workflow_state.digest(review['markdown']),
                'checklist': {'summary': 'Check the source', 'issues': [
                    {'id': 'source-check', 'problem': 'Confirm the source', 'location': {'section': 'Results', 'page': 1}}]}},
                owner=self.pid)
            workflow.doc_write(db, self.pid, 'outline', '# Outline artifact\n\n## Evidence requirements\n\nCheck source pages.')
            translation = workflow.doc_path(db, self.pid, 'write').with_name('verified_en.md')
            translation.write_text('# Translated manuscript\n\nVerified translation', encoding='utf-8')
            service_settings.write(db, 'translation:' + self.pid, {'result_path': str(translation), 'source_text': '原文研究结论',
                'segments': [{'id': 'segment-1', 'source': '原文研究结论', 'target': translation.read_text(encoding='utf-8')}],
                'custom_source': True})
            export = root / '最终产物' / 'final-paper.pdf'; export.parent.mkdir(parents=True, exist_ok=True)
            write_text_pdf(export, ['FINAL EXPORT - verified research'])
            asset_service.register_file(db, export, project_id=self.pid, kind='export')
            node_ids = ['draft', *stages[1:]]
            layout = workflow_layout.save_layout(db, {'name': 'All typed artifacts', 'nodes': [
                {'id': nid, 'step': stage, 'label': 'Stage ' + stage, 'executor_binding_id': self.binding,
                 'manual_review_required': True} for nid, stage in zip(node_ids, stages)],
                'edges': [{'source': a, 'target': b} for a, b in zip(node_ids, node_ids[1:])]})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
        with WorkflowModel(layout['id'], artifact_marker='Stage') as model:
            model.search_via_tool = True
            self.start(model)
            previous = None
            for node_id, stage in zip(node_ids, stages):
                response = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                    lambda data: any(t['workflow_node_id'] == node_id and t['status'] == 'awaiting_review' for t in data['tasks']))
                task = next(t for t in response['tasks'] if t['workflow_node_id'] == node_id)
                if previous and stage != 'write':
                    self.assertIn(previous, json.dumps(model.node_requests[-1], ensure_ascii=False))
                self.page.evaluate('async value=>ProjectWorkspace.openTask(value.id,value.project)', {'id': task['id'], 'project': self.pid})
                self.page.wait_for_function("s=>document.querySelector('[data-wnw-title]')?.textContent==='Stage '+s", arg=stage)
                self.assertEqual(self.page.locator('.pw-task-detail').count(), 0)
                self.assertEqual(self.page.locator('[data-wnw-step]').count(), len(stages))
                artifact = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
                if stage == 'convert':
                    self.page.locator('[data-wnw-content] iframe').wait_for()
                    preview_url = self.page.locator('[data-wnw-content] iframe').get_attribute('src')
                    self.assertEqual(self.page.request.get(preview_url).status, 200)
                elif stage == 'notes':
                    reading = self.page.locator('[data-wnw-reading-paper]')
                    reading.get_by_role('heading', name=reading_note['title']).wait_for()
                    reading.get_by_role('button', name='双语 PDF', exact=True).wait_for()
                    reading.locator('[data-wnw-compare]').click()
                    reading.locator('[data-wnw-reading-page]').select_option('1')
                    for title, expected in (('原文 PDF', source_bytes), ('译文 PDF', translation_bytes)):
                        url = reading.locator(f'iframe[title="{title}"]').get_attribute('src')
                        self.assertTrue(url.endswith('#page=2'), url)
                        preview = self.page.request.get(url)
                        self.assertTrue(preview.ok)
                        self.assertEqual(preview.body(), expected)
                    self.assertTrue(all(not file['editable'] for file in artifact['artifacts'] if file['kind'] == 'pdf'))
                elif stage == 'download':
                    self.page.locator('.wnw-download-paper summary').click()
                    self.page.get_by_role('heading', name='Page 1 evidence').wait_for()
                    self.page.locator('[data-wnw-paper-open]').first.click()
                    pdf = self.page.locator('[data-wnw-content] iframe')
                    pdf.wait_for()
                    self.assertEqual(self.page.request.get(pdf.get_attribute('src')).status, 200)
                elif stage == 'map':
                    self.page.locator('.wnw-graph svg').wait_for()
                    self.page.locator('[data-wnw-graph-node="page-proof"]').press('Enter')
                    self.page.locator('.wnw-graph-source').get_by_text('REAL PDF EVIDENCE - page 2').wait_for()
                    self.assertIn(' · 第 2 页', self.page.locator('.wnw-graph-source h4').inner_text())
                    self.page.locator('[data-wnw-graph-node="claim"]').press('Enter')
                elif stage == 'outline':
                    self.page.locator('.wnw-outline-index').get_by_role('button', name='Evidence requirements').click()
                    self.assertEqual(self.page.evaluate('document.activeElement.textContent'), 'Evidence requirements')
                elif stage == 'write':
                    self.page.locator('[data-wnw-content] .md-render').get_by_role('heading').first.wait_for()
                    self.assertIn('HUMAN_TYPED_MAP', artifact['content'])
                    self.assertIn('Verified page-level evidence.', artifact['content'])
                elif stage == 'review':
                    self.page.locator('[data-wnw-review-jump]').select_option('0')
                    self.assertIn('Results', self.page.locator('[data-wnw-review-issue]:focus').inner_text())
                self.capture('typed-' + stage + '-preview')
                content_box = self.page.locator('[data-wnw-content]').bounding_box()
                footer_box = self.page.locator('.wnw-footer').bounding_box()
                self.assertGreaterEqual(footer_box['y'], content_box['y'] + content_box['height'] - 1)
                if stage == 'convert':
                    self.page.locator('[data-wnw-export-edit]').click()
                    self.page.locator('[data-wnw-export-source]').fill('# Revised final paper\n\nHUMAN_TYPED_CONVERT')
                    self.page.locator('[data-wnw-export-format]').select_option('docx')
                    self.page.locator('[data-wnw-save]').click()
                    self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
                    result = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
                    from docx import Document
                    final = next(f for f in result['run']['output_snapshot']['files'] if f['name'].endswith('.docx'))
                    self.assertIn('HUMAN_TYPED_CONVERT', '\n'.join(p.text for p in Document(final['path']).paragraphs))
                    self.assertTrue(export.is_file())
                    self.page.locator('[data-wnw-approve]').click()
                    break
                structured = next((i for i, a in enumerate(artifact['artifacts']) if a['id'] == 'structured'), None)
                if structured is not None:
                    self.page.locator('[data-wnw-file="' + str(structured) + '"]').click()
                self.page.locator('[data-wnw-mode="edit"]').click()
                marker = 'HUMAN_TYPED_' + stage.upper()
                if stage == 'idea':
                    self.page.locator('[data-wnw-idea-report]').fill('# Human idea\n\n' + marker)
                elif stage in ('search', 'download'):
                    self.page.locator('[data-wnw-cell="0"][data-field="reason"]').fill(marker)
                elif stage == 'notes':
                    self.page.locator('[data-wnw-reading-note="0"]').fill(marker)
                elif stage == 'map':
                    self.page.locator('[data-wnw-graph-field="label"]').fill(marker)
                elif stage == 'entrans':
                    self.page.locator('[data-wnw-translation]').fill(marker)
                elif stage == 'review':
                    self.page.locator('[data-wnw-review-field="comment"]').fill(marker)
                    self.page.locator('[data-wnw-review-field="resolved"]').check()
                else:
                    self.page.locator('[data-wnw-editor]').fill('# Human revision\n\n' + marker)
                try:
                    self.page.wait_for_function(
                        "() => { const button = document.querySelector('[data-wnw-save]'); return button && !button.disabled; }",
                        timeout=10000)
                except Exception as error:
                    self.capture('typed-' + stage + '-save-disabled')
                    state = self.page.evaluate("""() => ({
                        title: document.querySelector('[data-wnw-title]')?.textContent,
                        meta: document.querySelector('[data-wnw-meta]')?.textContent,
                        draft: document.querySelector('[data-wnw-draft-state]')?.textContent,
                        error: document.querySelector('[data-wnw-error]')?.textContent,
                        editor: document.querySelector('[data-wnw-editor], [data-wnw-idea-report], [data-wnw-translation], [data-wnw-cell], [data-wnw-graph-field]')?.value
                    })""")
                    self.fail(f'{stage} artifact editor did not enable save: {state!r}; {error}')
                self.page.locator('[data-wnw-save]').click()
                self.page.wait_for_function("""stage => {
                    const error = document.querySelector('[data-wnw-error]');
                    if (error && !error.hidden && error.textContent.trim()) {
                        throw new Error(stage + ': ' + error.textContent);
                    }
                    return document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1');
                }""", arg=stage)
                saved = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
                self.assertIn(marker, json.dumps(saved['content'], ensure_ascii=False))
                if stage == 'map':
                    self.assertEqual(saved['content']['sources'], artifact['content']['sources'])
                if stage == 'review':
                    self.assertEqual(saved['content']['issues'][0]['id'], 'source-check')
                    self.assertTrue(saved['content']['issues'][0]['resolved'])
                if stage == 'entrans':
                    self.assertEqual(saved['content']['segments'][0]['source'], '原文研究结论')
                    final = next(file for file in saved['run']['output_snapshot']['files'] if file['name'] == 'verified_en.md')
                    self.assertEqual(Path(final['path']).read_text(encoding='utf-8'), marker)
                if stage == 'write':
                    self.assertIn(artifact['content'].strip(), saved['content'])
                    self.page.locator('[data-wnw-mode="diff"]').click()
                    self.page.locator('[data-wnw-diff-body]').get_by_text(marker, exact=False).wait_for()
                if stage == 'notes':
                    self.assertEqual(saved['content'][0]['page_map'], artifact['content'][0]['page_map'])
                    self.assertEqual(source.read_bytes(), source_bytes)
                    self.assertEqual(mono.read_bytes(), translation_bytes)
                    with workflow.WorkflowDB(self.database_path) as db:
                        self.assertEqual(db.notes(self.pid)[0]['markdown'], reading_note['markdown'])
                    self.page.locator('[data-wnw-mode="diff"]').click()
                    self.page.locator('[data-wnw-diff-body]').get_by_text(marker, exact=False).wait_for()
                    historical = self.page.request.get(self.url + 'api/project-tasks/node-preview', params={
                        'project_id': self.pid, 'id': task['id'], 'task_run_id': artifact['identity']['task_run_id'],
                        'output_revision': 0, 'artifact_id': 'structured',
                    }).json()['preview']
                    self.assertFalse(historical['editable'])
                    self.assertEqual(historical['data'][0]['reading_notes'][0]['markdown'], reading_note['markdown'])
                self.page.locator('[data-wnw-approve]').click()
                previous = marker
            self.poll_json('/api/workflow/execution?id=' + model.run_id, lambda d: d['run']['status'] == 'succeeded')
            self.assertEqual(len(model.node_requests), len(stages))
        self.assertEqual(self.errors, [])

    def test_search_result_selection_order_and_structured_revision(self):
        self.seed()
        papers = [{'source': 'manual', 'title': f'Search paper {number:02d}',
                   'abstract': f'Evidence abstract {number:02d}'} for number in range(21)]
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            reused_id = db.cache_result(self.pid, papers[0])
            unrelated_id = db.cache_result(self.pid, {'source': 'manual', 'title': 'Unrelated cached paper'})
            layout = workflow_layout.save_layout(db, {
                'name': 'Search results review',
                'nodes': [{'id': 'survey-custom', 'step': 'search', 'label': 'Survey evidence',
                           'executor_binding_id': self.binding, 'manual_review_required': True}], 'edges': []})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            flow = workflow_execution.create(db, self.pid, 'Find papers')
            calls = [{'id': 'search', 'type': 'function', 'function': {
                'name': 'paper_search', 'arguments': json.dumps({'query': 'Search'})}}]
            with patch.object(workflow, 'chat_raw', side_effect=[{'tool_calls': calls}, {'content': 'Search complete'}]), \
                    patch.object(workflow_stages, 'search', return_value=papers), \
                    patch.object(workflow_stages, 'enrich_oa', return_value=0):
                flow = workflow_execution.advance(db, flow['id'])
            self.assertEqual(flow['status'], 'awaiting_review', flow.get('error'))

        task_id = flow['settings']['task_ids']['survey-custom']
        result_path = '/api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task_id
        before = self.page.request.get(self.url.rstrip('/') + result_path).json()['result']['content']
        self.assertEqual({row['title'] for row in before}, {paper['title'] for paper in papers})
        self.assertIn(reused_id, {row['id'] for row in before})
        self.page.evaluate('value => ProjectWorkspace.openWorkflowRun(value.project, value.run, value.node)',
                           {'project': self.pid, 'run': flow['id'], 'node': 'survey-custom'})
        cards = self.page.locator('.wnw-search-paper')
        cards.first.wait_for()
        self.assertEqual(cards.count(), 20)
        self.page.locator('[data-wnw-page="1"]').click()
        self.assertEqual(cards.locator('h3').all_text_contents(), [before[-1]['title']])
        search = self.page.locator('[data-wnw-paper-search]')
        search.fill('Evidence abstract 00')
        self.assertEqual(cards.locator('h3').all_text_contents(), [papers[0]['title']])
        search.fill('')

        self.page.locator('[data-wnw-mode="edit"]').click()
        self.page.locator('[data-wnw-row-move="2:-1"]').click()
        self.page.locator('[data-wnw-row-move="1:-1"]').click()
        self.assertEqual(cards.first.locator('h3').inner_text(), before[2]['title'])
        self.assertEqual(self.page.locator('[data-wnw-cell][data-field="title"]').count(), 0)
        self.page.locator('[data-wnw-cell="2"][data-field="included"]').uncheck()
        self.page.locator('[data-wnw-cell="2"][data-field="reason"]').fill('Outside the review scope')
        self.page.locator('[data-wnw-cell="0"][data-field="reason"]').fill('Highest relevance after checking the abstract')
        self.page.locator('[data-wnw-paper-filter]').select_option('excluded')
        self.assertEqual(cards.locator('h3').all_text_contents(), [before[1]['title']])
        self.assertTrue(self.page.evaluate('async () => await ProjectWorkspace.flush()'))
        self.page.reload(wait_until='networkidle')
        self.page.locator('[data-wnw-mode="edit"]').click()
        self.assertEqual(self.page.locator('[data-wnw-cell="0"][data-field="reason"]').input_value(),
                         'Highest relevance after checking the abstract')
        self.assertFalse(self.page.locator('[data-wnw-cell="2"][data-field="included"]').is_checked())
        self.capture('node-search-selection-and-order')
        self.page.locator('[data-wnw-save]').click()
        self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
        saved = self.page.request.get(self.url.rstrip('/') + result_path).json()['result']
        self.assertEqual([item['id'] for item in saved['content']], [before[2]['id'], before[0]['id'],
                         *[row['id'] for row in before[3:]]])
        self.assertEqual(saved['content'][0]['reason'], 'Highest relevance after checking the abstract')
        self.page.locator('[data-wnw-mode="preview"]').click()
        self.page.locator('[data-wnw-paper-filter]').select_option('excluded')
        self.assertIn('Outside the review scope', cards.first.inner_text())
        self.page.locator('[data-wnw-mode="diff"]').click()
        self.page.locator('.wnw-added').first.wait_for()
        self.assertIn('Outside the review scope', self.page.locator('[data-wnw-diff-body]').inner_text())
        self.page.locator('[data-wnw-approve]').click()
        self.poll_json('/api/workflow/execution?id=' + flow['id'], lambda data: data['run']['status'] == 'succeeded')
        with workflow.WorkflowDB(self.database_path) as db:
            self.assertEqual({row['id'] for row in db.results(self.pid)}, {unrelated_id, *[row['id'] for row in before]})
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
