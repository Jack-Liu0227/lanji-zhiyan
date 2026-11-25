"""Click expert result tags in the real sidebar and keep their file/attempt identity."""
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from tests import test_web_runtime as web
from tests import test_workflow_sidebar_runtime as sidebar
import project_task_execution as worker
import project_tasks as tasks
import workflow


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowArtifactPreviewRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = sidebar.WorkflowSidebarRuntimeTest.seed
    prepare = sidebar.WorkflowSidebarRuntimeTest.prepare

    def screenshot(self, name):
        target = (Path(web.ROOT) / '.codex-tasks/20261123-workflow-monitoring/screenshots'
                  / ('artifact-preview-' + name + '.png'))
        target.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(target), full_page=True)

    def complete_attempt(self, attempt, csv_text=None):
        """Use real task lifecycle/output helpers, with no model or network fixture."""
        with workflow.WorkflowDB(self.database_path) as db:
            task_id = self.run['settings']['task_ids']['inspect']
            authority = tasks.CommandContext.ui(self.pid)
            if attempt > 1:
                previous = tasks.get(db, task_id)
                tasks.review(db, task_id, approved=False, feedback='Produce a new independent attempt',
                             authority=authority, reviewed_run_id=previous['latest_run_id'],
                             reviewed_output_version=0, request_key=f'artifact-preview-reject-{attempt}')
            run = tasks.start(db, task_id, authority=authority, restart=attempt > 1,
                              request_key=f'artifact-preview-attempt-{attempt}')
            claimed = tasks.claim(db, run['id'], worker_id='artifact-preview-runtime-fixture')
            self.assertIsNotNone(claimed)
            self.assertEqual(run['attempt'], attempt)
            folder = db.base_dir(self.pid) / 'runtime-fixtures' / run['id']
            folder.mkdir(parents=True, exist_ok=True)
            csv_path = folder / 'evidence.csv'
            csv_marker = f'CSV_ATTEMPT_{attempt}_ONLY'
            csv_path.write_text(csv_text if csv_text is not None else '\ufeffname,description,value\n'
                                f'{csv_marker},"quoted, field\nand another line",{attempt}\n',
                                encoding='utf-8', newline='')
            markdown_marker = f'MARKDOWN_ATTEMPT_{attempt}_ONLY'
            markdown = f'# Result from attempt {attempt}\n\n{markdown_marker}\n'
            # CSV is deliberately first. A fallback to files[0] cannot pass the MD assertions.
            output = worker._outputs(db, claimed['task'], claimed['run'], {'reply': markdown},
                                     {'files': [str(csv_path)], 'content': markdown})
            self.assertEqual([file['name'] for file in output['files']], ['evidence.csv', 'result.md'])
            completed = tasks.complete(db, run['id'], {'reply': markdown, 'output_snapshot': output,
                                                       'checkpoint': {'output_revision': 0}})
            self.assertEqual(completed['status'], 'awaiting_review')
            self.assertEqual(completed['latest_run']['status'], 'succeeded')
            return {'task_id': task_id, 'task_run_id': run['id'], 'attempt': attempt, 'output_revision': 0,
                    'files': {file['name']: file for file in output['files']},
                    'markdown': markdown, 'markdown_marker': markdown_marker, 'csv_marker': csv_marker}

    def open_expert(self):
        self.page.evaluate('''async value => {
            await refreshState(false);
            await ProjectWorkspace.openConversation(value.chat, value.project);
            await ProjectWorkspace.refresh();
            await ProjectWorkspace.openWorkflowDock(value.project, value.run);
        }''', {'chat': self.first, 'project': self.pid, 'run': self.run['id']})
        self.page.locator('#pwResource .project-workflow-graph').wait_for()
        self.page.locator('[data-pw-dock="expert-chat"]').click()
        self.page.locator('#pwResource [data-pw-expert-body] [data-pw-open-art]').first.wait_for()
        self.page.locator('#pwAsk').fill('UNSENT_MAIN_CONVERSATION_DRAFT')

    def test_workbench_csv_shows_all_source_columns_without_invented_screening_fields(self):
        self.prepare()
        attempt = self.complete_attempt(1, csv_text='paper,method,material,interface,control,system,temperature,pressure,metric,unit,value,evidence_level\nStudy,coating,,,,,,,,,,abstract only\n')
        self.page.evaluate('args=>ProjectWorkspace.openTask(...args)', [attempt['task_id'], self.pid])
        self.page.locator('[data-wnw-file]').filter(has_text='evidence.csv').click()
        self.page.locator('.wnw-table').wait_for()
        headers = self.page.locator('.wnw-table th').all_text_contents()
        self.assertEqual(headers[1:], ['paper', 'method', 'material', 'interface', 'control', 'system',
                                       'temperature', 'pressure', 'metric', 'unit', 'value', 'evidence_level'])
        self.assertIn('abstract only', self.page.locator('.wnw-table tbody').inner_text())
        self.assertNotIn('included', headers)
        self.assertEqual(self.page.locator('[data-field="included"]').count(), 0)

    def assert_dock_keeps_main_conversation(self):
        self.assertTrue(self.page.locator('#pwMessages').is_visible())
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertEqual(self.page.locator('#pwAsk').input_value(), 'UNSENT_MAIN_CONVERSATION_DRAFT')
        self.assertIn('MAIN_CONVERSATION_STAYS_VISIBLE', self.page.locator('#pwMessages').inner_text())
        self.assertEqual(self.page.locator('#projectWorkspace').get_attribute('data-surface'), 'chat')
        self.assertEqual(self.page.locator('[data-pw-dock="file-preview"]').get_attribute('aria-selected'), 'true')
        chat = self.page.locator('#projectWorkspace .pw-chat').bounding_box()
        dock = self.page.locator('#pwDocumentPane').bounding_box()
        self.assertIsNotNone(chat)
        self.assertIsNotNone(dock)
        self.assertGreaterEqual(dock['x'] + 1, chat['x'] + chat['width'], 'Preview must remain to the right of the main conversation')
        self.assertEqual(self.page.locator('#pwDocumentPane #pwResource .wfp').count(), 1)

    def assert_preview(self, response, attempt, name):
        self.assertTrue(response.ok, f'Clicked {name} in attempt {attempt["attempt"]}: HTTP {response.status}: {response.text()}')
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query.get('task_id', query.get('id')), [attempt['task_id']])
        self.assertEqual(query['task_run_id'], [attempt['task_run_id']])
        self.assertEqual(query['path'], [attempt['files'][name]['path']])
        result = response.json()['preview']
        self.assertEqual(result['name'], name)
        self.assertEqual(result['source']['task_run_id'], attempt['task_run_id'])
        self.assertEqual(result['source']['output_revision'], 0)
        self.assertEqual(result['source']['artifact_id'], 'file:1' if name == 'result.md' else 'file:0')
        self.assertEqual(self.page.locator('#pwResource [data-wfp-name]').inner_text(), name)
        self.assertEqual(self.page.locator('#pwResource .wfp-badge').inner_text(), '只读')
        self.assertEqual(self.page.locator('#pwResource .wfp textarea, #pwResource .wfp input, '
                                            '#pwResource .wfp [contenteditable]:not([contenteditable="false"])').count(), 0)
        self.assertIn(attempt['task_run_id'], self.page.locator('#pwResource [data-wfp-source]').inner_text())
        self.assertEqual(self.page.locator('#pwResource .wfp-error').count(), 0)
        if name == 'result.md':
            self.assertEqual(result['kind'], 'markdown')
            self.assertEqual(result['text'], attempt['markdown'])
            self.assertIn(attempt['markdown_marker'], self.page.locator('#pwResource [data-wfp-body]').inner_text())
            self.assertNotIn(attempt['csv_marker'], self.page.locator('#pwResource [data-wfp-body]').inner_text())
            self.assertEqual(self.page.locator('#pwResource .wfp table').count(), 0)
        else:
            self.assertEqual(result['kind'], 'table')
            self.assertEqual(result['columns'], ['name', 'description', 'value'])
            self.assertEqual(result['rows'], [[attempt['csv_marker'], 'quoted, field\nand another line', str(attempt['attempt'])]])
            self.assertIn(attempt['csv_marker'], self.page.locator('#pwResource .wfp table').inner_text())
            self.assertNotIn(attempt['markdown_marker'], self.page.locator('#pwResource [data-wfp-body]').inner_text())

    def click_artifact(self, attempt, name, screenshot):
        self.open_expert()
        label = self.page.locator(
            f'#pwResource [data-pw-expert-body] [data-pw-open-art][data-pw-art-run="{attempt["task_run_id"]}"]'
        ).filter(has_text=name)
        self.assertEqual(label.count(), 1)
        self.assertEqual(label.get_attribute('data-pw-open-art'), attempt['files'][name]['path'])
        with self.page.expect_response(lambda response: urlsplit(response.url).path == '/api/project-tasks/file-preview') as pending:
            label.click()
        self.page.locator('#pwResource .wfp[aria-busy="false"]').wait_for()
        self.screenshot(screenshot)
        self.assert_dock_keeps_main_conversation()
        self.assert_preview(pending.value, attempt, name)

    def prepare_artifacts(self):
        self.prepare()
        with workflow.WorkflowDB(self.database_path) as db:
            db.add_message(self.first, 'assistant', 'MAIN_CONVERSATION_STAYS_VISIBLE')
        return self.complete_attempt(1)

    def test_expert_tags_open_clicked_markdown_and_csv_in_right_dock(self):
        first = self.prepare_artifacts()
        for name, screenshot in (('result.md', 'clicked-result-attempt-1'), ('evidence.csv', 'clicked-csv-attempt-1')):
            with self.subTest(file=name):
                self.click_artifact(first, name, screenshot)
        self.assertEqual(self.errors, [])

    def test_retry_keeps_old_and_new_result_tags_pinned_to_the_clicked_attempt(self):
        first = self.prepare_artifacts()
        self.open_expert()
        old_label = self.page.locator('#pwResource [data-pw-open-art]').filter(has_text='result.md')
        self.assertEqual(old_label.get_attribute('data-pw-art-run'), first['task_run_id'])
        self.screenshot('expert-before-retry')
        second = self.complete_attempt(2)
        self.assertNotEqual(first['task_run_id'], second['task_run_id'])
        with workflow.WorkflowDB(self.database_path) as db:
            current = tasks.get(db, first['task_id'])
            self.assertEqual(current['latest_run_id'], second['task_run_id'])
            self.assertEqual([run['attempt'] for run in current['runs']], [1, 2])
        for attempt in (second, first):
            with self.subTest(clicked_attempt=attempt['attempt']):
                self.click_artifact(attempt, 'result.md', f'clicked-result-after-retry-attempt-{attempt["attempt"]}')
        self.assertEqual(self.errors, [])

    def test_explicit_old_attempt_opens_original_readonly_result_after_retry(self):
        first = self.prepare_artifacts()
        second = self.complete_attempt(2)
        self.open_expert()
        with self.page.expect_response(lambda response: urlsplit(response.url).path == '/api/project-tasks/file-preview') as pending:
            self.page.evaluate('''async value => {
                await ProjectWorkspace.openFilePreview(value.file, value.project);
            }''', {'project': self.pid, 'file': {'task_id': first['task_id'], 'task_run_id': first['task_run_id'],
                                               'output_revision': 0, 'path': first['files']['result.md']['path'], 'name': 'result.md'}})
        self.page.locator('#pwResource .wfp[aria-busy="false"]').wait_for()
        self.screenshot('explicit-old-attempt-after-retry')
        self.assert_dock_keeps_main_conversation()
        self.assert_preview(pending.value, first, 'result.md')
        self.assertNotIn(second['markdown_marker'], self.page.locator('#pwResource [data-wfp-body]').inner_text())
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
