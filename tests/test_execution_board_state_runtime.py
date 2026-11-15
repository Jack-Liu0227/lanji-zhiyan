"""Actual workers, missed notifications, review, double-click and follow-up cancellation."""
from pathlib import Path
import unittest
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests.test_status_board_toolbox_runtime import GatedModel


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class ExecutionBoardStateRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=workspace.ProjectWorkspaceRuntimeTest.use_model
    poll_json=workspace.ProjectWorkspaceRuntimeTest.poll_json

    def tearDown(self):self.assertEqual(self.errors,[])

    def board(self):web.workspace_section(self.page,'tasks')

    def lane(self,task,status):
        self.page.locator('[data-ec-lane="'+status+'"] [data-ec-task="'+task['id']+'"]').wait_for(timeout=15000)
        self.assertEqual(self.page.locator('[data-ec-task]').count(),1)

    def shot(self,name):
        folder=Path(web.ROOT)/'.codex-tasks/20261029-board-state';folder.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(folder/(name+'.png')))

    def test_live_states_recover_without_sse_and_double_click_opens_the_same_conversation(self):
        # Keep the connection "open" while deliberately withholding every notification.
        self.page.add_init_script("""(()=>{const Native=window.EventSource;window.EventSource=class {
          static OPEN=1;static CLOSED=2;
          constructor(url,...args){if(!String(url).includes('/api/execution-board/stream'))return new Native(url,...args);this.readyState=1;setTimeout(()=>this.onopen?.(new Event('open')),0);}
          close(){this.readyState=2;}
        };})()""")
        self.seed()
        with GatedModel() as model:
            self.use_model(model.url)
            task=self.page.request.post(self.url+'api/project-tasks/create',data={'project_id':self.pid,'title':'实时状态验收任务','goal':'WAIT_ONLY_FOR_BOARD 检查证据并形成报告','executor_binding_id':self.binding}).json()['task']
            self.board();self.lane(task,'pending')
            card=self.page.locator('[data-ec-task="'+task['id']+'"]')
            card.click()
            self.assertTrue(card.is_visible())
            self.assertTrue(self.page.locator('[data-ec-inspector]').is_hidden())
            result=self.page.request.post(self.url+'api/project-tasks/start',data={'project_id':self.pid,'id':task['id']})
            self.assertTrue(result.ok,result.text());self.assertTrue(model.started.wait(8))
            self.lane(task,'running');self.assertIn('进行中',card.inner_text())
            self.page.wait_for_function("document.querySelector('#pwRunning').textContent==='运行中 1'");self.shot('running')
            model.release.set();self.lane(task,'review')
            self.page.wait_for_function("document.querySelector('#pwPending').textContent==='待确认 1'");self.shot('waiting-review')
            current=self.page.request.get(self.url+'api/project-tasks/detail?project_id='+self.pid+'&id='+task['id']).json()['task']
            result=self.page.request.post(self.url+'api/project-tasks/review',data={'project_id':self.pid,'id':task['id'],'approved':True,'reviewed_run_id':current['latest_run_id'],'reviewed_output_version':0})
            self.assertTrue(result.ok,result.text());self.lane(task,'done')
            self.page.wait_for_function("document.querySelector('[data-ec-total]').textContent.includes('0 项运行中')")
            card.dblclick();self.page.locator('[data-execution-id="'+task['id']+'"]').wait_for()
            self.assertEqual(self.page.locator('#pwMessages').get_attribute('data-chat-id'),task['chat_id'])
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)
            # A follow-up is real work too, but stopping it must preserve the reviewed task.
            model.started.clear();model.release.clear()
            self.page.locator('#pwAsk').fill('解释报告结论')
            self.page.locator('#pwSend').click();self.assertTrue(model.started.wait(8))
            self.board();self.lane(task,'running')
            self.page.wait_for_function("document.querySelector('#pwRunning').textContent==='运行中 1'")
            card.click(button='right');self.page.get_by_role('menuitem',name='停止执行',exact=True).click()
            self.lane(task,'done');model.release.set()
            current=self.page.request.get(self.url+'api/project-tasks/detail?project_id='+self.pid+'&id='+task['id']).json()['task']
            self.assertEqual(current['status'],'delivered');self.assertEqual(len(current['deliveries']),1)
            self.page.emulate_media(reduced_motion='reduce')
            self.page.set_viewport_size({'width':900,'height':800});self.page.evaluate("setTheme('light')")
            self.page.wait_for_function("getComputedStyle(document.querySelector('.sidebar')).backgroundColor==='rgb(242, 246, 248)' && getComputedStyle(document.querySelector('.ec-lane-head h3')).color==='rgb(22, 32, 43)'")
            card.scroll_into_view_if_needed()
            self.shot('completed-light')
            self.assertTrue(self.page.locator('#main').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
            card.focus();card.press('Enter');self.page.locator('[data-execution-id="'+task['id']+'"]').wait_for()
            self.board();self.lane(task,'done');card.locator('[data-ec-open-chat]').click()
            self.page.locator('[data-execution-id="'+task['id']+'"]').wait_for()
