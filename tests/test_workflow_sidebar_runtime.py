"""Conversation-scoped workflow and file inspection in the actual desktop UI."""
from pathlib import Path
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
import workflow
import workflow_execution
import workflow_layout
import workflow_tasks


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowSidebarRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = workspace.ProjectWorkspaceRuntimeTest.seed

    def prepare(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            self.first = db.create_chat('Workflow inspection A', task_id=self.pid)
            self.second = db.create_chat('Empty conversation B', task_id=self.pid)
            layout = workflow_layout.save_layout(db, {'name': 'Conversation-owned workflow', 'nodes': [
                {'id': 'inspect', 'step': 'task', 'label': 'Inspect saved evidence',
                 'executor_binding_id': self.binding, 'node_kind': 'tool',
                 'tool_name': 'library_overview', 'tool_args': {}, 'review_required': True}], 'edges': []})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            self.run = workflow_execution.create(db, self.pid, 'Inspect evidence')
            self.run['settings']['origin_chat_id'] = self.first
            workflow_tasks._save(db, self.run)
        self.page.evaluate('async x=>{await refreshState(false);await ProjectWorkspace.openConversation(x.chat,x.project);}',
                           {'chat': self.first, 'project': self.pid})

    def select(self, chat):
        self.page.evaluate('x=>ProjectWorkspace.openConversation(x.chat,x.project)', {'chat': chat, 'project': self.pid})

    def screenshot(self, name):
        path = Path(web.ROOT) / '.codex-tasks/20261123-workflow-monitoring/screenshots' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)

    def test_empty_conversation_clears_all_dock_content_and_return_restores_run(self):
        self.prepare()
        self.page.evaluate('x=>ProjectWorkspace.openWorkflowDock(x.project,x.run)', {'project': self.pid, 'run': self.run['id']})
        self.page.locator('.project-workflow-graph').wait_for()
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.select(self.second)
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.assertEqual(self.page.locator('#pwResource').inner_text(), '')
        self.select(self.first)
        self.page.locator('.project-workflow-graph').wait_for()
        self.assertIn('Conversation-owned workflow', self.page.locator('#pwResource').inner_text())
        self.page.reload(wait_until='networkidle')
        self.page.locator('.project-workflow-graph').wait_for()
        self.page.locator('#pwNewChat').click()
        self.page.wait_for_function("document.querySelector('#pwDocumentPane').hidden")
        self.assertEqual(self.page.locator('#pwResource').inner_text(), '')
        self.screenshot('empty-conversation.png')
        self.assertEqual(self.errors, [])

    def test_central_workflow_status_remains_visible_with_dag_dock_open(self):
        self.prepare()
        with workflow.WorkflowDB(self.database_path) as db:
            completed = workflow_execution.advance(db, self.run['id'])
            self.assertEqual(completed['status'], 'awaiting_review')
        self.page.evaluate('ProjectWorkspace.refresh()')
        self.page.wait_for_function('document.querySelector("#pwWorkflowLive").dataset.status==="awaiting_review"')
        self.page.evaluate('x=>ProjectWorkspace.openWorkflowDock(x.project,x.run)',
                           {'project': self.pid, 'run': self.run['id']})
        self.page.locator('.project-workflow-graph').wait_for()
        self.assertTrue(self.page.locator('#pwWorkflowLive').is_visible())
        self.assertIn('等待人工核查', self.page.locator('#pwWorkflowLive').inner_text())
        self.assertTrue(self.page.locator('[data-live-quick-review]').is_visible())
        self.page.reload(wait_until='networkidle')
        self.page.locator('.project-workflow-graph').wait_for()
        self.assertTrue(self.page.locator('#pwWorkflowLive').is_visible())
        self.assertEqual(self.errors, [])

    def test_late_workflow_context_cannot_reopen_dock_after_chat_switch(self):
        self.prepare()
        self.page.evaluate('x=>ProjectWorkspace.openWorkflowDock(x.project,x.run)', {'project': self.pid, 'run': self.run['id']})
        self.page.locator('.project-workflow-graph').wait_for()
        pending = []
        path = self.url + 'api/agent-settings/workflow-context?project_id=' + self.pid + '&chat_id=' + self.first
        self.page.route(path, lambda route: pending.append(route))
        with self.page.expect_request(path):
            self.page.locator('[data-pw-dock="expert-chat"]').click()
        self.select(self.second)
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        with self.page.expect_response(path):
            pending[0].continue_()
        self.page.unroute(path)
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.assertEqual(self.page.locator('#pwResource').inner_text(), '')
        self.assertEqual(self.errors, [])

    def test_markdown_and_csv_links_open_in_dock_and_follow_conversation(self):
        self.prepare()
        with workflow.WorkflowDB(self.database_path) as db:
            folder = db.base_dir(self.pid) / 'outputs'
            folder.mkdir(exist_ok=True)
            (folder / 'report.md').write_text('# Preview report\n\nSIDEBAR_MARKDOWN\n\n[Evidence](evidence.csv)', encoding='utf-8')
            (folder / 'evidence.csv').write_text('\ufeff名称,说明,值\n甲,"逗号,与换行\n继续",3\n', encoding='utf-8')
            db.add_message(self.first, 'assistant', '[Report](outputs/report.md)\n\n[CSV](outputs/evidence.csv)')
        self.select(self.first)
        self.page.locator('#pwMessages a').filter(has_text='Report').click()
        self.page.wait_for_function("document.querySelector('#pwResource').textContent.includes('SIDEBAR_MARKDOWN')")
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertEqual(self.page.locator('[data-pw-dock="file-preview"]').get_attribute('aria-selected'), 'true')
        self.page.locator('#pwMessages a').filter(has_text='CSV').click()
        self.page.locator('#pwResource table').wait_for()
        self.assertIn('逗号,与换行', self.page.locator('#pwResource table').inner_text())
        self.assertIn('继续', self.page.locator('#pwResource table').inner_text())
        self.select(self.second)
        self.assertTrue(self.page.locator('#pwDocumentPane').is_hidden())
        self.select(self.first)
        self.page.locator('#pwResource table').wait_for()
        self.screenshot('csv-preview.png')
        self.page.set_viewport_size({'width': 900, 'height': 850})
        self.assertTrue(self.page.locator('#main').evaluate('node=>node.scrollWidth<=node.clientWidth+1'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
