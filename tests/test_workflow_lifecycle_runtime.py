"""Run the approved conversation and recovery flows against an isolated desktop UI."""
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_web_runtime as web
from tests import test_workflow_sidebar_runtime as fixture
import workflow
import workflow_layout
import workflow_execution as engine
import workflow_tasks
import project_task_execution as worker
import project_tasks as tasks


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkflowLifecycleRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp = web.WebRuntimeTest.setUp
    load_page = web.WebRuntimeTest.load_page
    seed = fixture.WorkflowSidebarRuntimeTest.seed
    prepare = fixture.WorkflowSidebarRuntimeTest.prepare
    select = fixture.WorkflowSidebarRuntimeTest.select

    def screenshot(self, name):
        path = web.ROOT / '.codex-tasks/workflow-lifecycle-20261124/screenshots' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)

    def test_empty_sidebar_has_no_graph_and_prompt_shortcut_does_not_send(self):
        self.prepare()
        self.select(self.second)
        self.page.locator('#pwTopLanggraph').click()
        self.page.locator('.pwg-empty').wait_for()
        self.assertIn('当前对话暂无工作流运行', self.page.locator('#pwResource').inner_text())
        for selector in ('.wfl-node', '.pwg-inspector', '.pwg-toolbar', '.pwg-zoom', '#pwWorkflowRun'):
            self.assertEqual(self.page.locator('#pwResource '+selector).count(), 0)
        before = self.page.locator('#pwMessages .msg-user').count()
        self.screenshot('empty-conversation.png')
        self.page.locator('[data-pwg-ask-steward]').click()
        self.page.wait_for_function("document.querySelector('#pwAsk').value.includes('编排工作流')")
        self.assertIn('编排工作流', self.page.locator('#pwAsk').input_value())
        self.assertEqual(self.page.locator('#pwMessages .msg-user').count(), before)
        self.assertEqual(self.errors, [])

    def test_failed_branch_recovers_from_ui_and_delivered_nodes_stay_green(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            self.first = db.create_chat('Recovery conversation', task_id=self.pid)
            layout = workflow_layout.save_layout(db, {'name': 'Five-node recovery', 'nodes': [
                {'id': 'n'+str(i), 'step': 'task', 'label': ['Pool', 'Evidence branch', 'Independent branch', 'Join', 'Finish'][i],
                 'executor_binding_id': self.binding, 'node_kind': 'tool', 'tool_name': 'library_overview',
                 'tool_args': {}, 'review_required': False} for i in range(5)],
                'edges': [{'source': a, 'target': b} for a, b in [('n0','n1'),('n0','n2'),('n1','n3'),('n2','n3'),('n3','n4')]]})['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            run = engine.create(db, self.pid)
            run['settings']['origin_chat_id'] = self.first
            workflow_tasks._save(db, run)
            execute = worker.execute
            def fail_branch(node_db, attempt, cancel):
                task = tasks.get(node_db, tasks.get_run(node_db, attempt)['task_id'])
                if task['workflow_node_id'] == 'n1':
                    return tasks.fail(node_db, attempt, 'MODEL_TIMEOUT_TEST')
                return execute(node_db, attempt, cancel)
            with patch.object(worker, 'execute', side_effect=fail_branch):
                pending = engine.advance(db, run['id'])
            self.assertEqual(pending['status'], 'blocked', pending['error'])
            accepted = {node: tasks.get(db, run['settings']['task_ids'][node])['accepted_delivery_id'] for node in ('n0','n2')}
        self.page.evaluate('async x=>{await refreshState(false);await ProjectWorkspace.openConversation(x.chat,x.project);await ProjectWorkspace.openWorkflowDock(x.project,x.run);}',
                           {'chat': self.first, 'project': self.pid, 'run': run['id']})
        self.page.locator('.wfl-node.failed').wait_for()
        self.assertIn('✗ 失败', self.page.locator('.wfl-node.failed').text_content())
        self.assertEqual(self.page.locator('.wfl-node.delivered').count(), 2)
        self.assertIn('40%', self.page.locator('.pwg-run-progress').inner_text())
        self.page.locator('.wfl-node.failed').click()
        self.page.locator('[data-pwg-retry-node]').wait_for(state='attached')
        border = self.page.locator('.wfl-node.failed .wfl-row').evaluate('node=>getComputedStyle(node).borderColor')
        self.assertNotEqual(border, self.page.locator('.wfl-node.delivered .wfl-row').first.evaluate('node=>getComputedStyle(node).borderColor'))
        self.screenshot('failed-and-delivered.png')
        self.page.evaluate("setTheme('light')")
        self.screenshot('failed-and-delivered-light.png')
        self.page.evaluate("setTheme('dark')")
        self.page.locator('[data-pwg-resume]').click()
        self.page.wait_for_function("document.querySelectorAll('.wfl-node.delivered').length===5", timeout=20000)
        self.assertIn('100%', self.page.locator('.pwg-run-progress').inner_text())
        self.screenshot('recovered.png')
        with workflow.WorkflowDB(self.database_path) as db:
            for node, delivery in accepted.items():
                task = tasks.get(db, run['settings']['task_ids'][node])
                self.assertEqual(task['accepted_delivery_id'], delivery)
                self.assertEqual(len(task['runs']), 1)
        self.page.set_viewport_size({'width': 900, 'height': 850})
        self.assertTrue(self.page.locator('#main').evaluate('node=>node.scrollWidth<=node.clientWidth+1'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
