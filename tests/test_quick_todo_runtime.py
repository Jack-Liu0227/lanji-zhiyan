"""Persistent capture, progress context actions and file-location UI integration."""
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section
import uuid

from tests import test_web_runtime as runtime


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class QuickTodoRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page
    new_project_note = runtime.WebRuntimeTest.new_project_note
    save_nested_note = runtime.WebRuntimeTest.save_nested_note

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def add_quick(self, title, project=""):
        self.page.locator('#todoCaptureProject').select_option(project)
        self.page.locator('#todoCaptureInput').fill(title)
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('#todoCaptureInput').value==='' && !document.querySelector('#todoCaptureAdd').disabled")
        return self.page.evaluate("async title => (await api('/api/todos')).todos.find(t=>t.title===title)", title)

    def board(self):
        self.page.locator('[data-nav="workspace"]').click()
        workspace_section(self.page, 'plan')
        self.page.locator('.execution-center').wait_for()
        self.page.locator('[data-ec-layout]').select_option('list')

    def test_capture_stays_visible_and_keeps_draft_across_pages_and_reload(self):
        self.load_page('?pw=1')
        self.page.locator('#todoCaptureInput').fill('随时记录，不丢草稿')
        for route in ('tasks','space','workflow','calendar','settings','agent','assistants','team','schedules','intel','library','mail'):
            self.page.evaluate("r=>openTab(r)",route)
            field = self.page.locator('#todoCaptureInput')
            self.assertTrue(field.is_visible())
            self.assertEqual(field.input_value(), '随时记录，不丢草稿')
            self.assertLess(field.bounding_box()['y'], 100)
        self.page.reload(wait_until='networkidle')
        self.assertEqual(self.page.locator('#todoCaptureInput').input_value(), '随时记录，不丢草稿')
        self.page.locator('[data-nav="agent"]').click()
        self.page.locator('#ask').focus()
        self.page.keyboard.press('Control+Shift+Enter')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'todoCaptureInput')

    def test_personal_todo_lifecycle_survives_reload_and_supports_context_menu(self):
        self.load_page()
        title='个人备忘 '+uuid.uuid4().hex[:8]
        task=self.add_quick(title)
        self.assertEqual(task['task_id'], '')
        self.page.reload(wait_until='networkidle')
        self.page.locator('#todoToggle').click()
        self.page.locator('#todoListSearch').fill(title)
        row=self.page.locator(f'[data-global-todo-id="{task["id"]}"]')
        row.locator('[data-todo-action="toggle"]').click()
        row.wait_for(state='hidden')
        self.page.locator('#todoShowDone').check()
        row.wait_for()
        row.click(button='right')
        self.page.get_by_role('menuitem', name='重新打开待办', exact=True).click()
        row.wait_for(state='hidden')
        self.page.locator('#todoShowDone').uncheck()
        row.locator('[data-todo-action="edit"]').click()
        self.assertEqual(self.page.locator('#uiPromptIn').input_value(),title)
        self.page.locator('#uiPromptIn').fill(title+' 修改')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("title=>document.querySelector('#todoList').textContent.includes(title)",arg=title+' 修改')
        row.locator('[data-todo-action="delete"]').click()
        self.page.keyboard.press('Escape')
        self.assertTrue(row.is_visible())
        row.locator('[data-todo-action="delete"]').click()
        self.page.locator('dialog [data-ok]').click()
        row.wait_for(state='hidden')

    def test_capture_failure_keeps_draft_and_ime_enter_does_not_submit(self):
        self.load_page()
        self.page.route('**/api/todos/add',lambda route:route.fulfill(status=503,json={'error':'暂不可用'}))
        field=self.page.locator('#todoCaptureInput')
        field.fill('中文待办')
        field.dispatch_event('keydown',{'key':'Enter','isComposing':True,'bubbles':True})
        self.assertEqual(self.page.locator('#todoCaptureStatus').inner_text(),'')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('#todoCaptureStatus').textContent.includes('未保存')")
        self.assertEqual(field.input_value(),'中文待办')
        self.page.locator('[data-nav="calendar"]').click()
        self.assertEqual(field.input_value(),'中文待办')

    def test_pending_plan_creation_uses_current_project_without_runtime_status(self):
        self.load_page()
        project=self.page.evaluate("async()=>{const p=await api('/api/task/create',{title:'右键计划项目'});await refreshState(false);curTask=p.task_id;spId=p.task_id;return p.task_id;}")
        self.board()
        self.page.locator('[data-ec-new]').click()
        self.assertEqual(self.page.locator('.ec-plan-form [name=status]').count(),0)
        self.page.locator('.ec-plan-form [name=title]').fill('计划开展的实验')
        self.page.locator('.ec-plan-form button[type=submit]').click()
        self.page.locator('.ec-plan-form').wait_for(state='hidden')
        self.page.locator('[data-plan-id]').filter(has_text='计划开展的实验').wait_for()
        task=self.page.evaluate("async()=> (await api('/api/todos')).todos.find(t=>t.title==='计划开展的实验')")
        self.assertEqual(task['status'],'TODO');self.assertEqual(task['task_id'],project)
        quick=self.add_quick('顶部记下的项目任务',project)
        self.page.locator('[data-plan-id="'+quick['id']+'"]').wait_for()
        self.page.locator('#main').focus();self.page.keyboard.press('Alt+Shift+n')
        self.page.locator('.ec-plan-form [name=title]').wait_for();self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#pwTab-tasks[aria-selected="true"]').is_visible())

    def test_pending_card_edit_archive_restore_delete_and_failed_save(self):
        self.load_page();project=self.page.evaluate('S.tasks[0].id')
        self.page.evaluate('id=>{curTask=id;spId=id}',project)
        task=self.add_quick('可编辑的计划 '+uuid.uuid4().hex[:6],project);self.board()
        row=self.page.locator('[data-plan-id="'+task['id']+'"]')
        row.locator('[data-context-trigger]').click();self.page.get_by_role('menuitem',name='编辑研究计划…').click()
        self.page.locator('.ec-plan-form [name=title]').fill('保留失败的修改')
        self.page.route('**/api/plans/save',lambda route:route.fulfill(status=500,json={'error':'保存失败'}))
        self.page.locator('.ec-plan-form button[type=submit]').click()
        self.page.locator('[data-plan-error]').filter(has_text='保存失败').wait_for()
        self.assertEqual(self.page.locator('.ec-plan-form [name=title]').input_value(),'保留失败的修改')
        self.page.unroute('**/api/plans/save')
        self.page.locator('.ec-plan-form button[type=submit]').click()
        self.page.locator('.ec-plan-form').wait_for(state='hidden')
        row.filter(has_text='保留失败的修改').wait_for()
        row.locator('[data-context-trigger]').click()
        self.page.get_by_role('menuitem',name='归档计划',exact=True).click()
        row.wait_for(state='hidden')
        self.page.locator('[data-ec-status]').select_option('history');row.wait_for()
        row.locator('[data-context-trigger]').click()
        self.page.get_by_role('menuitem',name='恢复为待安排',exact=True).click()
        row.wait_for(state='hidden')
        self.page.locator('[data-ec-status]').select_option('pending');row.wait_for()
        row.locator('[data-plan-edit]').click();self.page.locator('[data-plan-delete]').click()
        self.page.keyboard.press('Escape');self.assertTrue(self.page.locator('.ec-plan-form').is_visible())
        self.page.locator('[data-plan-close]').click();self.assertTrue(row.is_visible())
        row.locator('[data-plan-edit]').click();self.page.locator('[data-plan-delete]').click()
        self.page.locator('dialog [data-ok]').click();row.wait_for(state='hidden')

    def test_note_and_workflow_file_location_target_the_clicked_file(self):
        self.new_project_note(title='文件位置验证',content='source')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function("document.querySelector('#mdSaveState')?.textContent==='已保存'")
        path=self.page.evaluate('mdProj.path')
        actual=self.page.evaluate("async relative=>api('/api/file/location',{project_id:mdDashProject,relative})",path)
        self.assertTrue(actual['path'].replace('\\','/').endswith(path))
        self.assertFalse(actual['opened'])
        self.page.locator('#mdDashBackFiles').click()
        row=self.page.locator('.nb-note-row').filter(has=self.page.locator(f'[data-pn-file="{path}"]'))
        row.wait_for()
        self.assertEqual(row.locator('button button').count(),0)
        captured=[]
        def location(route):
            captured.append(route.request.post_data_json)
            route.fulfill(json={'path':'D:\\项目\\笔记.md','directory':'D:\\项目','opened':True})
        self.page.route('**/api/file/location',location)
        row.click(button='right')
        self.page.get_by_role('menuitem').filter(has_text='在文件资源管理器中显示').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(captured[-1]['relative'],path)
        self.assertTrue(captured[-1]['open'])
        row.focus()
        self.page.keyboard.press('Alt+Shift+o')
        self.page.wait_for_timeout(150)
        self.assertEqual(len(captured),2)
        self.assertTrue(self.page.locator('#mdDashboard').is_visible())
        self.page.locator('[data-nav="workspace"]').click()
        workspace_section(self.page, 'files')
        folder=self.page.locator('.fl-tree-row[data-rel="笔记"]')
        folder.wait_for()
        folder.locator('[data-context-trigger]').click()
        self.page.get_by_role('menuitem').filter(has_text='在文件资源管理器中显示').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(captured[-1]['relative'],'笔记')
        self.assertEqual(captured[-1]['project_id'],self.page.evaluate('curTask'))

    def test_plan_dependencies_persist_without_another_workflow_view(self):
        self.load_page();project=self.page.evaluate('S.tasks[0].id')
        self.page.evaluate('id=>{curTask=id;spId=id}',project)
        first=self.add_quick('Gather evidence '+uuid.uuid4().hex[:6],project)
        second=self.add_quick('Write synthesis '+uuid.uuid4().hex[:6],project)
        self.board();self.page.locator('[data-plan-id="'+second['id']+'"] [data-plan-edit]').click()
        self.page.locator('.ec-plan-form [name=dependencies]').select_option(first['id'])
        self.page.locator('.ec-plan-form [name=acceptance]').fill('Use accepted evidence with provenance')
        self.page.locator('.ec-plan-form button[type=submit]').click();self.page.locator('.ec-plan-form').wait_for(state='hidden')
        plans=self.page.request.get(self.url+'api/plans?project_id='+project).json()['plans']
        saved=next(plan for plan in plans if plan['id']==second['id'])
        self.assertEqual(saved['dependencies'],[first['id']]);self.assertIn('provenance',saved['acceptance'])
        self.assertEqual(self.page.locator('[data-view="pipeline"]').count(),0)
        self.page.locator('#pwTab-flow').click();self.page.locator('[data-pwg-step]').wait_for()

    def test_visual_todo_and_progress_menu_in_light_dark_small_window(self):
        self.load_page('?pw=1')
        self.add_quick('对比实验结果，整理下一步计划')
        destination=Path(runtime.ROOT)/'.codex-tasks/20261028-intelligence-library/screenshots'
        destination.mkdir(parents=True,exist_ok=True)
        for theme in ('light','dark'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.page.locator('#todoToggle').click()
            self.page.locator('.global-todo-row').first.wait_for()
            self.page.screenshot(path=str(destination/f'todo-{theme}.png'))
            self.page.keyboard.press('Escape')
        self.page.set_viewport_size({'width':900,'height':640})
        self.board()
        project=self.page.evaluate('curTask')
        task=self.add_quick('项目计划右键布局 '+uuid.uuid4().hex[:6],project)
        self.page.locator('[data-plan-id="'+task['id']+'"] [data-context-trigger]').click()
        self.page.screenshot(path=str(destination/'progress-context-menu.png'))
        rect=self.page.locator('[role="menu"]').bounding_box()
        self.assertLessEqual(rect['y']+rect['height'],640)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
