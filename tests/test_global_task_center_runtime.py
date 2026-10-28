"""Global navigation uses the same project tasks, conversations and review state."""
from pathlib import Path
import unittest

import project_bindings
import project_tasks
import research_plans
import workflow
from tests import test_web_runtime as web
from tests import test_project_workspace_runtime as workspace


@unittest.skipUnless(web.sync_playwright and web.EDGE,'Requires Playwright and Edge')
class GlobalTaskCenterRuntimeTest(unittest.TestCase):
    setUpClass=classmethod(web.WebRuntimeTest.setUpClass.__func__)
    setUp=web.WebRuntimeTest.setUp
    load_page=web.WebRuntimeTest.load_page
    seed=workspace.ProjectWorkspaceRuntimeTest.seed

    def test_global_automation_filter_and_refresh_share_the_original_rules(self):
        self.seed()
        rules=[]
        for pid in (self.pid,self.other):
            body={'project_id':pid,'name':'GLOBAL_RULE_'+pid,'prompt':'Prepare report','frequency':'manual','cron':'0 9 * * *','assistant_id':'workspace-writer','enabled':False}
            response=self.page.request.post(self.url+'api/schedule/save',data=body)
            self.assertTrue(response.ok,response.text())
            rules.append(body)
        self.page.locator('#sidebar [data-nav="task-center"]').click()
        self.page.locator('[data-ec-view="automation"]').click()
        for rule in rules:self.page.get_by_text(rule['name'],exact=True).wait_for()
        self.page.locator('[data-ec-project]').select_option(self.other)
        self.page.wait_for_function("document.querySelectorAll('[data-sch-edit]').length===1")
        self.assertIn(rules[1]['name'],self.page.locator('[data-ec-content]').inner_text())
        data=self.page.request.get(self.url+'api/schedules').json()['schedules']
        rule=next(row for row in data if row['name']==rules[1]['name'])
        response=self.page.request.post(self.url+'api/schedule/save',data={**rules[1],'id':rule['id'],'name':'UPDATED_GLOBAL_RULE'})
        self.assertTrue(response.ok,response.text())
        self.page.locator('[data-ec-refresh]').click()
        self.page.get_by_text('UPDATED_GLOBAL_RULE',exact=True).wait_for()
        self.page.locator('[data-sch-edit]').click()
        self.assertEqual(self.page.locator('#schProj').input_value(),self.other)
        self.assertTrue(self.page.locator('#schProj').is_disabled())
        self.page.locator('#schCancel').click()
        self.assertEqual(self.errors,[])

    def test_global_filter_navigation_cleanup_and_plan_dispatch(self):
        self.seed()
        tasks=[];bindings={}
        with workflow.WorkflowDB(self.database_path) as db:
            for pid in (self.pid,self.other):
                binding=project_bindings.register(db,pid,'assistant','workspace-writer')['id']
                bindings[pid]=binding
                tasks.append(project_tasks.create(db,{'project_id':pid,'title':'同名研究任务','executor_binding_id':binding},authority=project_tasks.CommandContext.ui(pid)))
                research_plans.route('POST','/api/plans/save',{'project_id':pid,'title':'前置计划 '+pid},{},db)
        page=self.page
        shots=Path(web.ROOT)/'.codex-tasks/20261029-global-center';shots.mkdir(parents=True,exist_ok=True)
        def capture():
            (shots/'last-page.txt').write_text(page.locator('#main').inner_text()+'\nERRORS: '+str(self.errors),encoding='utf-8')
            page.screenshot(path=str(shots/'last-page.png'))
        self.addCleanup(capture)
        page.locator('#sidebar [data-nav="task-center"]').click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===2")
        self.assertEqual(page.evaluate('route()'),'task-center')
        self.assertEqual(page.locator('[data-ec-project]').input_value(),'')
        self.assertEqual(page.locator('.ec-project-label').count(),2)
        page.wait_for_function("document.querySelector('[data-ec-connection]').textContent.includes('实时连接')")
        page.locator('[data-ec-project]').select_option(self.other)
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===1")
        self.assertEqual(page.locator('[data-ec-task]').get_attribute('data-ec-owner'),self.other)
        page.locator('[data-ec-task]').dblclick()
        page.locator('[data-execution-id="'+tasks[1]['id']+'"]').wait_for()
        self.assertEqual(page.locator('#pwMessages').get_attribute('data-chat-id'),tasks[1]['chat_id'])
        self.assertEqual(page.locator('#pwProject').input_value(),self.other)
        page.locator('#sidebar [data-nav="task-center"]').click()
        page.locator('[data-ec-project]').select_option('')
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===2")
        page.locator('.ec-kpi-clear[data-ec-clear="pending"]').click()
        self.assertIn('2 条记录',page.locator('dialog').inner_text())
        page.locator('dialog [data-ok]').click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===0")
        with workflow.WorkflowDB(self.database_path) as db:
            for task in tasks:self.assertFalse(db.chat(task['chat_id'])['deleted_at'])
        page.locator('[data-ec-trash]').click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===2")
        page.locator('[data-ec-task]').first.click(button='right')
        page.get_by_role('menuitem',name='恢复到看板',exact=True).click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===1")
        page.locator('[data-ec-trash]').click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===1")
        page.locator('[data-ec-view="plans"]').click()
        page.locator('[data-ec-new]').click()
        page.locator('.ec-project-dialog [name="project_id"]').select_option(self.other)
        page.locator('.ec-project-dialog [type=submit]').click()
        page.locator('.ec-plan-form [name="title"]').fill('全局入口新建计划')
        self.assertEqual(page.locator('.ec-plan-form [name="dependencies"] option').count(),1)
        self.assertIn(self.other,page.locator('.ec-plan-form [name="dependencies"]').inner_text())
        page.locator('.ec-plan-form [type=submit]').click()
        card=page.locator('[data-plan-id]').filter(has_text='全局入口新建计划')
        card.wait_for();card.click(button='right')
        page.get_by_role('menuitem',name='交给 Agent / 团队',exact=True).click()
        page.locator('[data-pt-executor]:not([disabled])').wait_for()
        page.locator('[data-pt-executor]').select_option(bindings[self.other])
        page.locator('[data-pt-prepare]').click()
        page.locator('dialog.pt-dialog').wait_for(state='detached')
        page.locator('[data-execution-id]').wait_for()
        created=page.request.get(self.url+'api/project-tasks?project_id='+self.other).json()['tasks']
        created=next(task for task in created if task['title']=='全局入口新建计划')
        self.assertEqual(page.locator('#pwMessages').get_attribute('data-chat-id'),created['chat_id'])
        self.assertEqual(page.locator('#pwProject').input_value(),self.other)
        page.locator('#sidebar [data-nav="task-center"]').click()
        page.locator('[data-ec-view="board"]').click()
        page.wait_for_function("document.querySelectorAll('[data-ec-task]').length===2")
        page.screenshot(path=str(shots/'global-dark.png'))
        page.set_viewport_size({'width':900,'height':800});page.evaluate("setTheme('light')")
        page.emulate_media(reduced_motion='reduce')
        page.wait_for_function("getComputedStyle(document.querySelector('.sidebar')).backgroundColor==='rgb(242, 246, 248)'")
        page.screenshot(path=str(shots/'global-light.png'))
        self.assertTrue(page.locator('#main').evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
        self.assertEqual(self.errors,[])
