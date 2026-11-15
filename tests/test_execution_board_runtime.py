"""Actual browser, HTTP model and SSE events connect plans, Agent work and artifacts."""
import unittest
from pathlib import Path

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_project_tasks_runtime as tasks_runtime


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ExecutionBoardRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model

    def test_plan_capture_and_real_task_board_share_execution_and_review(self):
        self.seed()
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            web.workspace_section(self.page,'plan')
            self.page.locator('[data-ec-new]').click()
            self.page.locator('dialog [name=title]').fill('Study ion transport')
            self.page.locator('dialog [name=acceptance]').fill('Evidence and limitations in a report')
            self.page.locator('dialog button[type=submit]').click()
            row=self.page.locator('[data-plan-id]').filter(has_text='Study ion transport');row.wait_for()
            plan_id=row.get_attribute('data-plan-id')
            row.locator('[data-plan-dispatch]').click()
            self.page.locator('[data-pt-executor]').select_option(self.binding)
            self.page.locator('[data-pt-create-start]').click()
            self.page.locator('#pwMessages [data-pw-output]').wait_for(timeout=30000)
            web.workspace_section(self.page,'tasks')
            card=self.page.locator('[data-ec-task]').filter(has_text='Study ion transport');card.wait_for()
            self.page.wait_for_function("document.querySelector('[data-ec-connection]').textContent.includes('实时连接')")
            self.assertEqual(card.get_attribute('data-status'),'awaiting_review')
            card.dblclick();self.page.locator('[data-execution-detail]').click()
            self.page.locator('#pwApprove').click()
            web.workspace_section(self.page,'plan')
            self.page.wait_for_function("id=>!document.querySelector('[data-plan-id=\"'+id+'\"]')",arg=plan_id)
            self.page.locator('[data-ec-status]').select_option('history')
            self.page.locator('[data-plan-id="'+plan_id+'"]').filter(has_text='已派发').wait_for()
            self.assertEqual(self.page.request.get(self.url+'api/plans?project_id='+self.pid).json()['plans'][0]['status'],'DONE')
            self.page.locator('[data-plan-id="'+plan_id+'"]').click(button='right')
            self.assertTrue(self.page.get_by_role('menuitem',name='恢复为待安排',exact=True).is_disabled())
            self.page.keyboard.press('Escape')
            self.assertTrue(model.requests)
        self.assertEqual(self.errors,[])

    def test_history_context_menu_pin_rename_delete_and_restore(self):
        self.seed()
        self.page.locator('#pwNewChat').click()
        self.page.locator('[data-history-id]').first.wait_for()
        row=self.page.locator('[data-history-id]').first
        cid=row.get_attribute('data-history-id')
        row.click(button='right');self.page.get_by_role('menuitem',name='置顶对话',exact=True).click()
        self.page.locator('[data-history-id="'+cid+'"].is-pinned').wait_for()
        self.page.locator('[data-history-id="'+cid+'"]').click(button='right')
        self.page.get_by_role('menuitem',name='重命名…',exact=True).click()
        self.page.locator('#uiPromptIn').fill('Pinned research conversation');self.page.locator('dialog [data-ok]').click()
        self.page.locator('[data-history-id="'+cid+'"]').filter(has_text='Pinned research conversation').wait_for()
        self.page.locator('[data-history-id="'+cid+'"]').click(button='right')
        self.page.get_by_role('menuitem',name='删除对话…',exact=True).click();self.page.locator('dialog [data-ok]').click()
        self.page.locator('[data-history-view]').select_option('trash')
        deleted=self.page.locator('[data-history-id="'+cid+'"]');deleted.wait_for();deleted.click(button='right')
        self.page.get_by_role('menuitem',name='恢复对话',exact=True).click()
        self.page.locator('[data-history-view]').select_option('active')
        self.page.locator('[data-history-id="'+cid+'"]').wait_for()
        self.assertEqual(self.errors,[])

    def test_global_toolbox_runs_model_and_saves_result_into_project(self):
        self.seed()
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#paperToolbox').click()
            self.page.locator('.paper-toolbox [name=project_id]').select_option(self.pid)
            self.page.locator('[data-tb-text]').fill('Translate the research goal into Chinese.')
            self.page.locator('[data-tb-run]').click()
            self.page.wait_for_function("document.querySelector('[data-tb-output]').value.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.page.wait_for_function("!document.querySelector('[data-tb-save]').disabled")
            self.page.locator('.paper-toolbox [name=project_id]').select_option(self.pid)
            self.page.locator('[data-tb-save]').click()
            self.page.wait_for_function("document.querySelector('[data-tb-status]').textContent.includes('已保存')")
            resources=self.page.request.get(self.url+'api/project/resources?project_id='+self.pid).json()['items']
            self.assertTrue(any(row.get('source')=='project_notes' for row in resources))
            self.page.locator('[data-tb-close]').click()
            web.workspace_section(self.page,'tasks')
            self.page.locator('[data-ec-task]').filter(has_text='中英互译').dblclick()
            self.page.locator('dialog .history-preview').filter(has_text='PROJECT_TASK_REAL_OUTPUT').wait_for()
        self.assertEqual(self.errors,[])
