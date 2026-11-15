"""Board deletion is independent; conversation deletion propagates through real SSE."""
from pathlib import Path
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
import workflow
import project_tasks
from run_manager import RunManager


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class BoardCleanupRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed

    def tearDown(self):
        self.assertEqual(self.errors,[])

    def conversation(self):
        with workflow.WorkflowDB(self.database_path) as db:
            cid=db.create_chat('关联错误对话',task_id=self.pid)
            db.add_message(cid,'user','保留的真实对话内容')
            ids=[]
            for attempt in range(2):
                run=RunManager.get_instance().create_run(cid,f'{cid}-{attempt}',db)
                run.emit({'type':'error','message':'模型网关没有可用渠道'},db);ids.append(run.run_id)
        return cid,ids

    def board(self):
        web.workspace_section(self.page,'tasks')
        self.page.locator('[data-ec-view=board]').click()
        self.page.wait_for_function("document.querySelector('[data-ec-connection]').textContent.includes('实时连接')")

    def screenshot(self,name):
        folder=Path(web.ROOT)/'.codex-tasks/20261029-board-cleanup'
        folder.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(folder/(name+'.png')))

    def test_history_deletion_removes_failed_cards_in_an_already_open_board(self):
        self.seed();cid,ids=self.conversation();self.board()
        card=self.page.locator('[data-ec-task="'+cid+'"]');card.wait_for()
        other=self.context.new_page();self.addCleanup(other.close)
        other.goto(self.url,wait_until='networkidle')
        other.evaluate('args=>ProjectWorkspace.openConversation(...args)',[cid,self.pid])
        history=other.locator('[data-history-id="'+cid+'"]');history.wait_for();history.click(button='right')
        other.get_by_role('menuitem',name='删除对话…',exact=True).click()
        other.locator('dialog [data-ok]').click()
        card.wait_for(state='detached')
        self.assertEqual(self.page.locator('[data-ec-task]').count(),0)
        self.assertEqual(self.page.locator('[data-ec-summary-filter=attention]').count(),0)
        self.page.locator('[data-ec-trash]').click()
        self.assertEqual(self.page.locator('[data-ec-task]').count(),0)
        other.locator('[data-history-view]').select_option('trash')
        history.click(button='right');other.get_by_role('menuitem',name='恢复对话',exact=True).click()
        self.page.locator('[data-ec-trash]').click();card.wait_for()
        self.assertEqual(self.page.locator('[data-ec-task]').count(),1)

    def test_individual_board_delete_keeps_chat_and_can_restore_after_refresh(self):
        self.seed();cid,ids=self.conversation();self.board()
        card=self.page.locator('[data-ec-task="'+cid+'"]');card.wait_for();card.click(button='right')
        self.page.get_by_role('menuitem',name='从看板删除',exact=True).click()
        self.page.locator('dialog [data-ok]').click();card.wait_for(state='detached')
        self.page.reload(wait_until='networkidle');self.board()
        self.page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===0")
        data=self.page.request.get(self.url+'api/chat?id='+cid).json()
        self.assertIn('保留的真实对话内容',str(data))
        self.page.locator('[data-ec-trash]').click();card.wait_for();card.click()
        self.page.locator('[data-ec-visibility]').filter(has_text='恢复到看板').click();card.wait_for(state='detached')
        self.page.locator('[data-ec-trash]').click();card.wait_for();card.click(button='right')
        self.page.get_by_role('menuitem',name='查看执行详情',exact=True).click()
        self.page.locator('[data-ec-visibility]').filter(has_text='从看板删除').click()
        self.page.locator('dialog [data-ok]').click();card.wait_for(state='detached')
        self.assertTrue(self.page.locator('[data-ec-inspector]').is_hidden())
        self.assertFalse(self.page.request.get(self.url+'api/conversation-history?project_id='+self.pid).json()['conversations'][0]['deleted_at'])

    def test_every_status_column_can_clear_its_records_without_changing_execution(self):
        self.seed()
        states={'pending':'ready','running':'running','review':'awaiting_review','done':'delivered','attention':'failed','stopped':'cancelled'}
        ids={}
        with workflow.WorkflowDB(self.database_path) as db:
            for lane,status in states.items():
                task=project_tasks.create(db,{'project_id':self.pid,'title':'清理验收 '+lane,'executor_binding_id':self.binding},authority=project_tasks.CommandContext.ui(self.pid))
                db.db.execute('UPDATE project_tasks SET status=? WHERE id=?',(status,task['id']));ids[lane]=task['id']
            db.db.commit()
        cid,runs=self.conversation();self.board()
        self.page.locator('[data-ec-task]').first.wait_for();self.screenshot('all-status-cleanup')
        self.page.locator('.ec-kpi-clear[data-ec-clear=pending]').click()
        self.page.locator('dialog [data-no]').click()
        self.assertEqual(self.page.locator('[data-ec-task]').count(),7)
        for lane in states:
            area='.ec-lane-head' if lane=='attention' else '.ec-kpi-wrap'
            self.page.locator(area+' [data-ec-clear="'+lane+'"]').click()
            if lane=='running':self.assertIn('执行会继续',self.page.locator('dialog').inner_text())
            self.page.locator('dialog [data-ok]').click()
            self.page.locator('[data-ec-task="'+ids[lane]+'"]').wait_for(state='detached')
        self.assertEqual(self.page.locator('[data-ec-task]').count(),0)
        self.assertIn('0 项运行中',self.page.locator('[data-ec-total]').inner_text())
        self.page.locator('[data-ec-trash]').click()
        self.assertEqual(self.page.locator('[data-ec-task]').count(),7)
        with workflow.WorkflowDB(self.database_path) as db:
            for lane,status in states.items():
                self.assertEqual(db.db.execute('SELECT status FROM project_tasks WHERE id=?',(ids[lane],)).fetchone()[0],status)
            self.assertFalse(db.chat(cid)['deleted_at'])
        self.page.set_viewport_size({'width':900,'height':800});self.page.emulate_media(reduced_motion='reduce')
        self.page.evaluate("setTheme('light')")
        self.page.wait_for_function("getComputedStyle(document.querySelector('.ec-run-card')).backgroundColor==='rgb(255, 255, 255)'")
        self.page.locator('.ec-kpi').first.wait_for();self.screenshot('cleanup-compact-light')
        self.assertTrue(self.page.locator('#main').evaluate('element=>element.scrollWidth<=element.clientWidth+1'))
        self.assertTrue(self.page.locator('.ec-kpi-label').evaluate_all('labels=>labels.every(e=>e.clientHeight<20)'))
        self.page.locator('.ec-run-card').first.scroll_into_view_if_needed();self.screenshot('cleanup-light-cards')
