"""Drive original links, import previews and project switching in the real UI."""
from pathlib import Path
import unittest

import rss
import workflow
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_rss_settings_runtime import FeedFixture
from tests.test_status_board_toolbox_runtime import GatedModel


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class IntelligenceLibraryRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def test_preview_open_original_batch_import_and_manual_doi_share_project_library(self):
        self.seed()
        with FeedFixture() as feed,workflow.WorkflowDB(self.database_path) as db:
            db.db.execute('DELETE FROM rss_items');db.db.execute('DELETE FROM rss_sources');db.db.commit()
            source=rss.save_source(db,{'title':'Local feed','url':feed.url})['source']
            rss.refresh(db)
            self.page.locator('#sidebar [data-nav="intel"]').click()
            self.page.locator('#itProject').select_option(self.pid)
            self.page.locator('[data-intel-id]').filter(has_text='固态电解质订阅论文').wait_for()
            card=self.page.locator('[data-intel-id]').filter(has_text='固态电解质订阅论文')
            card.locator('summary').click();self.assertIn('可复查',card.locator('.it-summary').inner_text())
            with self.page.expect_popup() as popup:card.locator('[data-it-original]').click()
            popup.value.wait_for_load_state();self.assertTrue(popup.value.url.endswith('/article'));popup.value.close()
            self.page.locator('#itSelectAll').check();self.page.locator('#itImportSelected').click()
            dialog=self.page.get_by_role('dialog',name='导入情报到项目',exact=True)
            self.assertEqual(dialog.locator('[name=project_id]').input_value(),self.pid)
            with self.page.expect_response(lambda response:response.url.endswith('/api/rss/import')) as response:
                dialog.get_by_role('button',name='确认导入题录',exact=True).click()
            self.assertTrue(response.value.ok,response.value.text())
            dialog.locator('[data-li-status]').filter(has_text='已导入 1 条题录').wait_for()
            self.assertIn('非网页地址条目',dialog.locator('[data-li-error]').inner_text())
            dialog.get_by_role('button',name='再次检查导入',exact=True).click()
            dialog.locator('[data-li-status]').filter(has_text='已存在 1 条').wait_for()
            dialog.get_by_role('button',name='打开项目文献',exact=True).click()
            self.page.locator('#plList .pl-row').filter(has_text='固态电解质订阅论文').wait_for()
            self.assertEqual(self.page.locator('#plList .pl-row').filter(has_text='固态电解质订阅论文').get_by_role('link',name='查看原文 ↗').get_attribute('href'),feed.url.replace('/feed.xml','/article'))
            self.page.locator('#plLinkImport').click()
            dialog=self.page.get_by_role('dialog',name='通过链接或 DOI 导入文献',exact=True)
            dialog.get_by_label('文献标题',exact=True).fill('Manual DOI evidence')
            dialog.get_by_label('DOI',exact=True).fill('10.9876/example')
            dialog.get_by_role('button',name='确认导入题录',exact=True).click()
            dialog.locator('[data-li-status]').filter(has_text='文献题录已导入').wait_for()
            dialog.get_by_role('button',name='关闭',exact=True).last.click()
            self.page.locator('#plList .pl-row').filter(has_text='Manual DOI evidence').wait_for()
        records=self.page.request.get(self.url+'api/library/project?task_id='+self.pid).json()['items']
        self.assertEqual(len(records),2);self.assertFalse(any(row['has_pdf'] for row in records))
        self.assertEqual(self.page.request.get(self.url+'api/library/project?task_id='+self.other).json()['items'],[])
        self.assertEqual(self.errors,[])

    def test_bottom_project_switch_preserves_drafts_and_background_agent_board_state(self):
        self.seed()
        with GatedModel() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('WAIT_ONLY_FOR_BOARD project discussion')
            self.page.locator('#pwAsk').press('Control+Enter')
            self.assertTrue(model.started.wait(10))
            self.page.wait_for_function("document.querySelector('#pwSend').getAttribute('aria-label')==='停止当前对话'")
            self.page.locator('#pwAsk').fill('Draft in first project')
            self.page.locator('#pwComposeProject').select_option(self.other)
            self.page.wait_for_function('id=>document.querySelector("#pwProject").value===id',arg=self.other)
            self.assertEqual(self.page.locator('#pwAsk').input_value(),'')
            self.page.locator('#pwAsk').fill('Draft in second project')
            self.page.locator('#pwComposeProject').select_option(self.pid)
            self.page.wait_for_function('document.querySelector("#pwAsk").value==="Draft in first project"')
            web.workspace_section(self.page,'tasks')
            self.page.locator('[data-ec-task][data-status=running]').wait_for()
            model.release.set()
            self.page.locator('[data-ec-task][data-status=completed]').wait_for(timeout=30000)
            actual=self.page.request.get(self.url+'api/execution-board?project_id='+self.pid).json()['items']
            self.assertTrue(any(row['kind']=='conversation' and row['status']=='completed' for row in actual))
            self.assertFalse(self.page.request.get(self.url+'api/execution-board?project_id='+self.other).json()['items'])
            self.page.locator('#pwTab-chat').click()
            self.page.locator('#pwComposeProject').select_option(self.other)
            self.page.wait_for_function('document.querySelector("#pwAsk").value==="Draft in second project"')
            self.page.set_viewport_size({'width':820,'height':700})
            self.assertTrue(self.page.locator('#pwComposeProject').is_visible())
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[])
