"""Interaction and visual regressions against disposable projects and local model fixtures."""
from pathlib import Path
import time
import threading
import unittest

from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace
from tests import test_connected_research_runtime as connected
from tests import test_project_tasks_runtime as tasks
import workflow


@unittest.skipUnless(web.sync_playwright and web.EDGE, 'Requires Playwright and Edge')
class WorkspaceUIAuditRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed
    use_model=connected.ConnectedResearchRuntimeTest.use_model
    poll_json=tasks.ProjectTasksRuntimeTest.poll_json

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def shot(self,name):
        folder=Path(web.ROOT)/'.codex-tasks/20261028-product-updates/visual'
        folder.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(folder/(name+'.png')))

    def board(self):
        web.workspace_section(self.page,'tasks')
        self.page.locator('[data-ec-view=board]').click()
        self.page.locator('[data-ec-content]').wait_for()

    def make_task(self,title='整理证据链并撰写研究摘要'):
        result=self.page.request.post(self.url+'api/project-tasks/create',data={'project_id':self.pid,'title':title,'goal':title,'executor_binding_id':self.binding})
        self.assertTrue(result.ok,result.text())
        return result.json()['task']['id']

    def menu(self,card,name):
        card.click(button='right')
        self.page.get_by_role('menuitem',name=name,exact=True).click()

    def prompt(self,value):
        self.page.locator('#uiPromptIn').fill(value)
        self.page.locator('#uiPromptIn').press('Enter')

    def test_task_menu_organization_copy_duplicate_navigation_and_restore(self):
        self.seed();task=self.make_task();self.board()
        card=self.page.locator('[data-ec-task="'+task+'"]')
        for action,value in [('重命名任务…','已核对的实验任务'),('优先级…','P0'),('开始日期…','2026-10-01'),('截止日期…','2026-11-01')]:
            self.menu(card,action);self.prompt(value)
            self.page.wait_for_timeout(120)
        self.menu(card,'置顶任务');card.get_by_text('置顶',exact=True).wait_for()
        self.assertIn('P0',card.inner_text());self.assertIn('2026-11-01',card.inner_text())
        self.menu(card,'取消置顶');card.get_by_text('置顶',exact=True).wait_for(state='hidden')
        self.context.grant_permissions(['clipboard-read','clipboard-write'])
        self.menu(card,'复制任务链接')
        self.page.wait_for_function('id=>navigator.clipboard.readText().then(text=>text.includes(id))',arg=task)
        with self.context.expect_page() as opened:self.menu(card,'在新标签页打开')
        other=opened.value;other.locator('[data-pw-start]:visible').wait_for();other.close()
        self.menu(card,'复制本地工作目录')
        self.page.wait_for_function('id=>navigator.clipboard.readText().then(text=>text.includes(id)&&text.includes(".tasks"))',arg=task)
        self.menu(card,'打开任务与审查');self.page.locator('[data-pw-start]:visible').wait_for();self.board()
        self.menu(card,'复制为新任务');self.page.locator('[data-pw-start]:visible').wait_for()
        rows=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks']
        self.assertEqual(len(rows),2);self.assertTrue(any('副本' in row['title'] for row in rows))
        self.board();self.menu(card,'从看板删除');self.page.locator('dialog [data-ok]').click();card.wait_for(state='hidden')
        self.page.locator('[data-ec-trash]').click();self.menu(card,'恢复到看板');card.wait_for(state='hidden')
        self.page.locator('[data-ec-trash]').click();card.wait_for()
        self.shot('task-board')

    def test_live_and_replayed_messages_are_unique_and_tools_are_collapsed(self):
        self.seed()
        with tasks.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('ONE_USER_TURN')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwMessages')?.textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.page.wait_for_function("document.querySelector('#pwSend')?.getAttribute('aria-label')==='发送'")
            self.assertEqual(self.page.locator('#pwMessages .msg-user').count(),1)
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)
            self.page.reload(wait_until='networkidle')
            self.page.locator('#pwMessages .msg-assistant').wait_for()
            self.assertEqual(self.page.locator('#pwMessages .msg-user').count(),1)
            self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)
        self.page.evaluate('''()=>{
          const box=document.querySelector('#pwMessages');box.replaceChildren();
          appendOne(box,{type:'user',content:'请检索证据并给出总结'});
          appendOne(box,{type:'assistant_delta',message_id:'audit-reply',content:'研究总结：已找到可核验的证据。'});
          appendOne(box,{type:'tool_call',id:'audit-tool',round:1,name:'project_search',args:{query:'电池材料 电化学稳定性'}});
          const result={type:'tool_result',id:'audit-tool',round:1,name:'project_search',ok:true,duration_ms:210,result:{sources:['evidence.md'],count:1},summary:'找到 1 条可核验资料'};
          appendOne(box,result);appendOne(box,result);
          const reply={type:'assistant',message_id:'audit-reply',content:'研究总结：已找到可核验的证据。'};
          appendOne(box,reply);appendOne(box,reply);
        }''')
        self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)
        self.assertEqual(self.page.locator('.tool-output pre').count(),1)
        self.assertFalse(self.page.locator('.tool-card details').evaluate('el=>el.open'))
        self.shot('messages-collapsed')
        self.page.locator('.tool-card summary').click()
        self.assertIn('evidence.md',self.page.locator('.tool-body').inner_text())
        self.shot('messages-expanded')
        self.page.evaluate('''()=>{const box=document.querySelector('#pwMessages');box.replaceChildren();appendOne(box,{type:'assistant',content:'FINAL',replay:true,events:[{type:'assistant',content:'FINAL  ',message_id:'old-record'}]});}''')
        self.assertEqual(self.page.locator('#pwMessages .msg-assistant').count(),1)

    def test_searches_and_visual_surfaces_desktop_and_compact(self):
        self.seed();self.make_task()
        with workflow.WorkflowDB(self.database_path) as db:
            db.schedule_save({'name':'每日文献总结','project_id':self.pid,'prompt':'检索项目文献','enabled':False})
            team=db.team_add('文献分析与证据核查','custom',project_id=self.pid)
            db.team_member_add(team['id'],'研究负责人','leader',assistant_id='workspace-writer')
            db.team_member_add(team['id'],'证据核查员','reviewer',assistant_id='workspace-writer')
        self.page.request.post(self.url+'api/todos/add',data={'task_id':self.pid,'title':'核对实验设计与证据来源','priority':'P0'})
        destinations=[
          ('experts',"openTab('assistants')",'#ag-assistant-new','搜索专家名称、专长或模型','课题分析'),
          ('teams',"openTab('team')",'#tofNew','搜索团队或成员','NO_MATCH_AUDIT'),
          ('channels',"setSec='channels';openTab('settings')",'[data-channel=telegram]','搜索消息渠道','Telegram'),
          ('models',"setSec='models';openTab('settings')",'#ag-provider-select','搜索已配置的模型提供商','NO_MATCH_AUDIT'),
          ('toolbox',"openTab('toolbox')",'[data-tb-tool=caption]','搜索论文工具','图注'),
          ('usage',"openTab('usage')",'.usage-kpis',None,None),
        ]
        for name,action,ready,search,query in destinations:
            self.page.evaluate('()=>{'+action+'}')
            self.page.locator(ready).wait_for()
            if search:
                field=self.page.get_by_role('searchbox',name=search,exact=True);field.fill(query)
                if name=='experts':
                    self.assertTrue(self.page.locator('.ag-grid > .card:visible').count()>0)
                    self.assertTrue(all('课题分析' in text for text in self.page.locator('.ag-grid > .card:visible').all_text_contents()))
                self.shot(name+'-search');field.press('Escape');self.assertEqual(field.input_value(),'')
            for width,theme in [(1440,'dark'),(900,'light')]:
                self.page.set_viewport_size({'width':width,'height':900 if width==1440 else 700})
                self.page.evaluate('value=>setTheme(value)',theme)
                self.shot(f'{name}-{width}')
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),name)
                self.assertTrue(self.page.locator('#main').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),name+' main overflow')
        self.page.set_viewport_size({'width':1440,'height':900})
        self.page.evaluate('pid=>ProjectWorkspace.openProject(pid)',self.pid)
        web.workspace_section(self.page,'plan');self.page.locator('[data-plan-id]').first.wait_for();self.shot('plans')
        self.board();self.page.locator('[data-ec-task]').first.wait_for();self.shot('board')
        self.page.locator('[data-ec-task]').first.click(button='right');self.shot('task-menu');self.page.keyboard.press('Escape')
        self.page.locator('[data-ec-view=automation]').click();self.page.locator('[data-schedule-id]').wait_for()
        field=self.page.get_by_role('searchbox',name='搜索自动化规则与运行记录',exact=True);field.fill('不存在')
        self.assertEqual(self.page.locator('.sch-card:visible').count(),0);field.press('Escape');self.shot('automation')
        self.page.locator('#todoToggle').click();self.page.locator('[data-global-todo-id]').first.wait_for();self.shot('todo')

    def test_other_pages_visual_and_search_access(self):
        self.page.emulate_media(reduced_motion='reduce')
        self.seed()
        destinations=[('calendar',"openTab('calendar')",'[data-cal-search]'),
          ('knowledge',"openTab('library')",'.sm-search input'),
          ('intelligence',"openTab('intel')",'#itProject'),
          ('mail',"openTab('mail')",'#mailSearch'),
          ('tools',"setSec='tools';openTab('settings')",'.set-body'),
          ('skills',"setSec='skills';openTab('settings')",'.set-body'),
          ('mcp',"setSec='mcp';openTab('settings')",'.set-body'),
          ('rss',"setSec='rss';openTab('settings')",'[data-rss-list]'),
          ('workflow-templates',"setSec='workflows';openTab('settings')",'.wfl-editor-list')]
        for name,action,ready in destinations:
            self.page.evaluate('()=>{'+action+'}')
            self.page.locator(ready).wait_for()
            for width,theme in [(1440,'dark'),(900,'light')]:
                self.page.set_viewport_size({'width':width,'height':800})
                self.page.evaluate('value=>setTheme(value)',theme)
                self.page.locator(ready).wait_for()
                self.page.wait_for_timeout(150)
                self.shot(name+'-'+str(width))
                self.assertTrue(self.page.locator('#main').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),name)
            if name=='workflow-templates':
                field=self.page.get_by_role('searchbox',name='搜索工作流模板',exact=True)
                field.fill('文献精读')
                self.assertEqual(self.page.locator('[data-wfl-edit]:visible').count(),1)
                field.fill('NO_MATCH_AUDIT')
                self.assertEqual(self.page.locator('[data-wfl-edit]:visible').count(),0)
                field.press('Escape')
            if name=='knowledge':
                field=self.page.get_by_role('searchbox',name='搜索知识库项目',exact=True)
                field.fill('NO_MATCH_AUDIT');self.assertEqual(self.page.locator('[data-sm-project]:visible').count(),0)
                field.fill('研究工作区验收');self.page.locator('[data-sm-card]:visible').first.click()
                self.assertIn('task=',self.page.locator('#kbFrame').get_attribute('src'))
                self.page.locator('.sm-back').click();self.assertTrue(self.page.locator('.sm-projects').is_visible())
        self.page.evaluate('pid=>ProjectWorkspace.openProject(pid)',self.pid)
        for section in ('catalog','files','notes','papers','stars','workflow'):
            web.workspace_section(self.page,section)
            self.page.wait_for_timeout(150)
            self.shot('project-'+section)
            self.assertTrue(self.page.locator('#main').evaluate('el=>el.scrollWidth<=el.clientWidth+1'),section)
        self.page.get_by_role('searchbox',name='搜索工作流节点',exact=True).fill('论文搜索')
        self.page.locator('[data-pwg-match]').click()
        self.assertEqual(self.page.locator('[data-pwg-label]').input_value(),'论文搜索')

    def test_todo_and_plan_context_actions(self):
        self.seed();self.context.grant_permissions(['clipboard-read','clipboard-write'])
        made=self.page.request.post(self.url+'api/todos/add',data={'task_id':self.pid,'title':'菜单验收待办'}).json()
        self.page.evaluate('()=>GlobalTodos.open()')
        row=self.page.locator('[data-global-todo-id="'+made['id']+'"]')
        for name,value in [('编辑待办…','修改后的待办'),('优先级…','P0'),('截止日期…','2026-11-02')]:
            self.menu(row,name);self.prompt(value)
            self.page.wait_for_function("args=>document.querySelector('[data-global-todo-id=\"'+args.id+'\"]')?.textContent.includes(args.value)",arg={'id':made['id'],'value':value})
        self.assertIn('P0',row.inner_text());self.assertIn('2026-11-02',row.inner_text())
        self.menu(row,'复制待办内容');self.page.wait_for_function("navigator.clipboard.readText().then(text=>text.includes('修改后的待办'))")
        self.menu(row,'复制为新待办');self.page.get_by_text('修改后的待办（副本）',exact=True).wait_for()
        self.menu(row,'标记为已完成');row.wait_for(state='hidden');self.page.locator('#todoShowDone').check()
        self.menu(row,'重新打开待办');row.wait_for(state='hidden');self.page.locator('#todoShowDone').uncheck()
        self.menu(row,'新增待办');self.assertEqual(self.page.evaluate('document.activeElement.id'),'todoCaptureInput')
        self.page.evaluate('()=>GlobalTodos.open()');self.menu(row,'查看项目进度')
        self.page.locator('[data-plan-id]').first.wait_for()
        plan=self.page.locator('[data-plan-id="'+made['id']+'"]')
        self.menu(plan,'优先级…');self.page.locator('.ec-plan-form [name=priority]').select_option('P1');self.page.locator('.ec-plan-form [type=submit]').click()
        self.page.locator('.ec-plan-form').wait_for(state='hidden')
        self.menu(plan,'截止日期…');self.page.locator('.ec-plan-form [name=due_date]').fill('2026-12-01');self.page.locator('.ec-plan-form [type=submit]').click()
        self.page.locator('.ec-plan-form').wait_for(state='hidden')
        self.menu(plan,'复制计划内容');self.page.wait_for_function("navigator.clipboard.readText().then(text=>text.includes('修改后的待办'))")
        self.menu(plan,'交给 Agent / 团队');self.page.locator('dialog.pt-dialog').wait_for();self.page.keyboard.press('Escape')
        self.menu(plan,'删除计划…');self.page.locator('dialog [data-ok]').click()
        self.page.wait_for_function("document.querySelectorAll('[data-plan-id]').length===1")
        self.page.evaluate('()=>GlobalTodos.open()');remaining=self.page.locator('[data-global-todo-id]').filter(has_text='副本').first
        self.menu(remaining,'删除待办…');self.page.locator('dialog [data-ok]').click();remaining.wait_for(state='hidden')

    def test_schedule_menu_and_model_configuration_to_real_http_call(self):
        self.seed()
        with tasks.TaskModelFixture() as model:
            self.page.evaluate("()=>{setSec='models';openTab('settings')}")
            self.page.locator('#ag-provider-select').select_option('custom');self.page.locator('#ag-provider-add').click()
            self.page.locator('#ag-p-name').fill('界面配置验收模型')
            self.page.locator('#ag-p-url').fill(model.url)
            self.page.locator('#ag-p-manual').fill('browser-test');self.page.locator('#ag-p-add').click()
            self.page.locator('#ag-p-auto').click();self.page.locator('.ag-modal').wait_for(state='hidden')
            self.page.locator('[data-ag-active]').filter(has_text='当前默认').wait_for()
            self.page.evaluate('pid=>ProjectWorkspace.openProject(pid)',self.pid)
            self.page.locator('#pwAsk').fill('MODEL_CONFIG_UI_CALL');self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwMessages')?.textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.assertEqual(model.requests[-1]['model'],'browser-test')
            with workflow.WorkflowDB(self.database_path) as db:
                rule=db.schedule_save({'name':'菜单定时规则','project_id':self.pid,'prompt':'MENU_SCHEDULE_HTTP_CALL','enabled':False})
            web.workspace_section(self.page,'automation');self.page.locator('[data-ec-view=automation]').click()
            row=self.page.locator('[data-schedule-id="'+rule['id']+'"]')
            self.menu(row,'✓ 启用');row.locator('[data-sch-toggle]').filter(has_text='暂停').wait_for()
            self.menu(row,'⏸ 暂停');row.locator('[data-sch-toggle]').filter(has_text='启用').wait_for()
            self.menu(row,'✎ 编辑');self.page.locator('#schName').fill('更名后的规则');self.page.locator('#schSave').click()
            row.filter(has_text='更名后的规则').wait_for()
            before=len(model.requests)
            with self.page.expect_response(lambda response:response.url.endswith('/api/schedule/run')) as sent:self.menu(row,'立即运行一次')
            self.assertTrue(sent.value.ok,sent.value.text())
            deadline=time.monotonic()+15
            while len(model.requests)==before and time.monotonic()<deadline:self.page.wait_for_timeout(100)
            self.assertGreater(len(model.requests),before)
            self.menu(row,'🗑 归档');self.page.locator('dialog [data-ok]').click();row.wait_for(state='hidden')

    def test_file_context_create_copy_rename_search_handoff_and_delete(self):
        self.seed();self.context.grant_permissions(['clipboard-read','clipboard-write'])
        web.workspace_section(self.page,'files')
        row=self.page.locator('#flList [data-rel="evidence.md"]');row.wait_for()
        for name,expected in [('复制','SAVED_PROJECT_MATERIAL'),('复制路径','evidence.md'),('复制相对路径','evidence.md'),('复制终端切换目录命令','cd "')]:
            self.menu(row,name)
            self.page.wait_for_function('value=>navigator.clipboard.readText().then(text=>text.includes(value))',arg=expected)
        row.focus();self.page.keyboard.press('Control+Alt+Shift+c')
        self.page.wait_for_function("navigator.clipboard.readText().then(text=>text==='evidence.md')")
        self.menu(row,'打开文件');self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('SAVED_PROJECT_MATERIAL')")
        opened=[]
        self.page.route('**/api/file/location',lambda route:(opened.append(route.request.post_data_json),route.fulfill(json={'path':str(self.temporary_root/'evidence.md'),'opened':True})))
        self.menu(row,'在文件资源管理器中显示');self.page.wait_for_timeout(100)
        self.assertTrue(opened[-1]['open']);self.assertEqual(opened[-1]['relative'],'evidence.md');self.page.unroute('**/api/file/location')
        self.menu(row,'新文件');self.prompt('menu-created.txt');self.page.locator('#flEditText').wait_for()
        self.page.locator('#flEditText').fill('CONTEXT_FILE_CONTENT');self.page.locator('#flSaveBtn').click()
        created=self.page.locator('#flList [data-rel="menu-created.txt"]');created.wait_for()
        self.page.once('dialog',lambda dialog:dialog.accept('renamed-menu.txt'))
        self.menu(created,'重命名');created=self.page.locator('#flList [data-rel="renamed-menu.txt"]');created.wait_for()
        self.menu(created,'新建文件夹');self.prompt('menu-folder');self.page.locator('#flList [data-rel="menu-folder"]').wait_for()
        self.menu(created,'交接记录与回执');self.page.get_by_role('dialog').wait_for();self.page.keyboard.press('Escape')
        self.menu(created,'交给当前工作流…');self.page.get_by_role('dialog').wait_for();self.page.keyboard.press('Escape')
        self.menu(created,'删除');self.page.locator('dialog [data-ok]').click();created.wait_for(state='hidden')
        folder=self.page.locator('#flList [data-rel="menu-folder"]');self.menu(folder,'删除');self.page.locator('dialog [data-ok]').click();folder.wait_for(state='hidden')
        self.menu(row,'搜索同名项目资料');self.page.locator('#pwSearchInput').wait_for()
        self.assertEqual(self.page.locator('#pwSearchInput').input_value(),'evidence.md')

    def test_running_task_and_conversation_can_stop_from_board_menu(self):
        self.seed()
        for kind in ('task','conversation'):
            with tasks.TaskModelFixture() as model:
                release=threading.Event();entered=threading.Event()
                original=model.server.RequestHandlerClass.do_POST
                def paused(handler):
                    entered.set();release.wait(15)
                    try:original(handler)
                    except (BrokenPipeError,ConnectionResetError):pass
                model.server.RequestHandlerClass.do_POST=paused
                try:
                    self.use_model(model.url)
                    if kind=='task':
                        task_id=self.make_task('测试停止任务')
                        response=self.page.request.post(self.url+'api/project-tasks/start',data={'project_id':self.pid,'id':task_id})
                        self.assertTrue(response.ok,response.text())
                    else:
                        self.page.locator('#pwTab-chat').click();self.page.locator('#pwAsk').fill('测试停止对话');self.page.locator('#pwSend').click()
                    self.assertTrue(entered.wait(8));self.board()
                    card=self.page.locator('[data-ec-task][data-status=running]').first;card.wait_for()
                    run_id=card.get_attribute('data-ec-task')
                    card.click(button='right');self.page.locator('[data-ec-refresh]').evaluate('button=>button.click()')
                    self.page.wait_for_timeout(250)
                    self.page.get_by_role('menuitem',name='停止执行',exact=True).click()
                    self.poll_json('/api/execution-board?project_id='+self.pid,lambda data:any(row['id']==run_id and row['status']=='cancelled' for row in data['items']))
                finally:release.set()

    def test_calendar_search_and_global_project_search(self):
        self.seed()
        result=self.page.request.post(self.url+'api/calendar/add',data={'task_id':self.pid,'title':'CALENDAR_SEARCH_EVIDENCE','date':'2026-12-11','kind':'todo'})
        self.assertTrue(result.ok,result.text())
        self.page.evaluate("async()=>{await refreshState(false);openTab('calendar')}")
        field=self.page.get_by_role('searchbox',name='搜索全部日程');field.fill('CALENDAR_SEARCH_EVIDENCE')
        self.page.locator('[data-cal-result]').click()
        self.assertEqual(self.page.evaluate('calSel'),'2026-12-11')
        self.page.keyboard.press('Control+k');self.page.locator('#cmdPaletteInput').fill('研究工作区验收')
        self.page.locator('#cmdPaletteResults .cmd-item').filter(has_text='项目: 研究工作区验收').first.click()
        self.page.locator('#projectWorkspace').wait_for()
