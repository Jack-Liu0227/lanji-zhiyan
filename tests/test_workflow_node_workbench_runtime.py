"""Drive completed Agent workflow results through the actual page and HTTP services."""
from pathlib import Path
import json
import os
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_workflow_review_runtime import WorkflowModel
import workflow
import workflow_layout


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowNodeWorkbenchRuntimeTest(unittest.TestCase):
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

    def test_failed_workflow_node_keeps_status_without_empty_artifact_navigation(self):
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
            self.page.locator('#pwAsk').fill('START_FAILED_SEARCH')
            self.page.locator('#pwSend').click()
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
            self.assertEqual(self.page.locator('[data-pwg-result]').count(), 0)
            self.page.locator('[data-pwg-task]').click()
            self.page.locator('.pw-task-detail').wait_for()
            self.assertEqual(self.page.locator('.wnw').count(), 0)
        self.assertEqual(self.errors, [])

    def test_ten_typed_stages_preview_edit_and_forward(self):
        """Exercise real persisted stage formats; only the external model is a fixture."""
        from tests._pdf_fixture import write_text_pdf
        import asset_service
        import papertran
        import service_settings
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
            write_text_pdf(source, ['REAL PDF EVIDENCE - page 1'])
            paper = db.ingest_pdf(self.pid, source)
            dual, mono = papertran.out_paths(source, source.parent)
            for output in (dual, mono):
                write_text_pdf(output, ['TRANSLATED EVIDENCE - page 1'])
            papertran._save_state(source.parent, paper['id'], 1, 1, True)
            workflow.map_write(db, self.pid, {'nodes': [{'id': 'claim', 'type': 'section', 'label': 'Evidence claim'},
                {'id': 'source', 'type': 'paper', 'label': 'Verified source'}], 'edges': [{'from': 'claim', 'to': 'source', 'label': 'supported by'}]})
            for stage in ('outline', 'write', 'review'):
                workflow.doc_write(db, self.pid, stage, '# ' + stage.title() + ' artifact\n\nVerified source on page 1')
            translation = workflow.doc_path(db, self.pid, 'write').with_name('verified_en.md')
            translation.write_text('# Translated manuscript\n\nVerified translation', encoding='utf-8')
            service_settings.write(db, 'translation:' + self.pid, {'result_path': str(translation), 'source_text': '原文研究结论', 'custom_source': True})
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
            self.start(model)
            previous = None
            for node_id, stage in zip(node_ids, stages):
                response = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                    lambda data: any(t['workflow_node_id'] == node_id and t['status'] == 'awaiting_review' for t in data['tasks']))
                task = next(t for t in response['tasks'] if t['workflow_node_id'] == node_id)
                if previous:
                    self.assertIn(previous, json.dumps(model.node_requests[-1], ensure_ascii=False))
                self.page.evaluate('async value=>ProjectWorkspace.openTask(value.id,value.project)', {'id': task['id'], 'project': self.pid})
                self.page.wait_for_function("s=>document.querySelector('[data-wnw-title]')?.textContent==='Stage '+s", arg=stage)
                self.assertEqual(self.page.locator('.pw-task-detail').count(), 0)
                self.assertEqual(self.page.locator('[data-wnw-step]').count(), len(stages))
                artifact = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
                if stage in ('notes', 'convert'):
                    self.page.locator('[data-wnw-content] iframe').wait_for()
                    preview_url = self.page.locator('[data-wnw-content] iframe').get_attribute('src')
                    self.assertEqual(self.page.request.get(preview_url).status, 200)
                elif stage == 'download':
                    self.page.locator('.wnw-papers article').wait_for()
                    self.page.locator('[data-wnw-paper-open]').first.click()
                    self.page.locator('[data-wnw-content] iframe').wait_for()
                elif stage == 'map':
                    self.page.locator('.wnw-graph svg').wait_for()
                elif stage in ('outline', 'write', 'review'):
                    self.page.locator('[data-wnw-content] .md-render h1').wait_for()
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
                    self.page.locator('[data-wnw-cell="0"][data-field="translation"]').fill(marker)
                elif stage == 'map':
                    self.page.locator('[data-wnw-graph-field="label"]').fill(marker)
                elif stage == 'entrans':
                    self.page.locator('[data-wnw-translation]').fill(marker)
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
                self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
                saved = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
                self.assertIn(marker, json.dumps(saved['content'], ensure_ascii=False))
                self.page.locator('[data-wnw-approve]').click()
                previous = marker
            self.poll_json('/api/workflow/execution?id=' + model.run_id, lambda d: d['run']['status'] == 'succeeded')
            self.assertEqual(len(model.node_requests), len(stages))
        self.assertEqual(self.errors, [])

    def test_search_result_selection_order_and_structured_revision(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            # The Agent reviews actual cached project search records without using an external search service.
            original_ids = {db.cache_result(self.pid, {'source': 'manual', 'title': title, 'abstract': title + ' abstract'})
                            for title in ('Search paper alpha', 'Search paper beta', 'Search paper gamma')}
            layout = workflow_layout.save_layout(db, {
                'name': 'Search results review',
                'nodes': [{'id': 'draft', 'step': 'search', 'label': 'Draft report',
                           'executor_binding_id': self.binding, 'manual_review_required': True}], 'edges': []})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
        with WorkflowModel(layout['id']) as model:
            task = self.start(model)['draft']
            before = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']['content']
            self.page.locator('[data-wr-result="' + task['id'] + '"]').first.click()
            self.page.locator('[data-wnw-mode="edit"]').click()
            self.page.locator('[data-wnw-row-move="2:-1"]').click()
            self.page.locator('[data-wnw-row-move="1:-1"]').click()
            self.assertEqual(self.page.locator('[data-wnw-cell="0"][data-field="title"]').input_value(), before[2]['title'])
            self.capture('node-search-order-controls')
            self.page.locator('[data-wnw-cell="2"][data-field="included"]').uncheck()
            self.page.locator('[data-wnw-cell="0"][data-field="reason"]').fill('Highest relevance after checking the abstract')
            self.capture('node-search-selection-and-order')
            self.page.locator('[data-wnw-save]').click()
            self.page.wait_for_function("document.querySelector('[data-wnw-meta]').textContent.includes('修订 v1')")
            saved = self.page.request.get(self.url + 'api/project-tasks/node-result?project_id=' + self.pid + '&id=' + task['id']).json()['result']
            self.assertEqual([item['id'] for item in saved['content']], [before[2]['id'], before[0]['id']])
            self.assertEqual(saved['content'][0]['reason'], 'Highest relevance after checking the abstract')
            self.assertTrue(all(item['included'] for item in saved['content']))
            self.page.locator('[data-wnw-approve]').click()
            self.poll_json('/api/workflow/execution?id=' + model.run_id,
                lambda data: data['run']['status'] == 'succeeded')
            with workflow.WorkflowDB(self.database_path) as db:
                self.assertEqual({item['id'] for item in db.results(self.pid)}, original_ids)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
