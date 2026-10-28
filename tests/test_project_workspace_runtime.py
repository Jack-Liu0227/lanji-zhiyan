"""Project-owned workspace behavior in a real isolated desktop server and Edge."""
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime
from tests import test_project_tasks_runtime as tasks_runtime
from tests import test_connected_research_runtime as connected
import workflow
import project_bindings
import workflow_layout


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Edge")
class ProjectWorkspaceRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page
    use_model = connected.ConnectedResearchRuntimeTest.use_model
    poll_json = tasks_runtime.ProjectTasksRuntimeTest.poll_json

    def seed(self):
        with workflow.WorkflowDB(self.database_path) as db:
            self.pid = db.create_task("研究工作区验收", base_dir=str(self.temporary_root / self._testMethodName))
            self.other = db.create_task("隔离项目", base_dir=str(self.temporary_root / (self._testMethodName + "-other")))
            db.assistant_save("workspace-writer", name="课题分析专家", enabled=1, permission="full_access")
            self.binding = project_bindings.register(db, self.pid, "assistant", "workspace-writer")["id"]
            root = db.base_dir(self.pid)
            root.mkdir(parents=True, exist_ok=True)
            (root / "evidence.md").write_text("# Workspace evidence\nSAVED_PROJECT_MATERIAL", encoding="utf-8")
        self.load_page()
        self.page.evaluate("pid=>{curTask=pid;localStorage.setItem('rw-task',pid);openTab('workspace');}", self.pid)
        self.page.locator("#projectWorkspace").wait_for()

    def test_task_prepare_execute_output_review_and_full_access(self):
        self.seed()
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)", arg=self.binding)
            workspace_section(self.page, 'files')
            self.page.locator('#flList [data-rel="evidence.md"]').click()
            self.page.locator('#flStage .md-render').wait_for()
            self.page.locator('#pwUseMaterial').click()
            self.page.locator('#pwRecipient').select_option('binding:' + self.binding)
            self.page.locator('#pwAsk').fill('WORKSPACE_REVIEW_REPORT')
            self.page.locator('#pwPrepareTask').click()
            self.page.locator('#pwMessages [data-pw-start]').wait_for()
            self.assertEqual(model.requests, [])
            self.page.locator('#pwMessages [data-pw-start]').click()
            self.page.locator('#pwMessages [data-pw-output]').last.wait_for(timeout=30000)
            self.page.locator('#pwMessages [data-pw-output]').last.click()
            self.page.wait_for_function("document.querySelector('#flStage').textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.assertEqual(self.page.locator('#flEditText').count(), 0)
            self.assertTrue(self.page.locator('#pwApprove').is_visible())
            self.page.locator('#pwFeedback').fill('REVIEWED_FIXED_OUTPUT')
            self.page.locator('#pwApprove').click()
            data = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                lambda d: any(t['status']=='delivered' for t in d['tasks']))
            task = next(t for t in data['tasks'] if t['title']=='WORKSPACE_REVIEW_REPORT')
            self.assertEqual(task['deliveries'][-1]['source'], 'human')
            self.assertIn('REVIEWED_FIXED_OUTPUT', task['deliveries'][-1]['feedback'])
            self.page.locator('#pwTab-chat').click()
            self.page.locator('#pwProjectSettings summary').click()
            self.page.locator('#pwMode').select_option('full_access')
            self.page.locator('#pwProjectSettings summary').click()
            self.page.locator('#pwAsk').fill('WORKSPACE_AUTO_REPORT')
            self.page.locator('#pwPrepareTask').click()
            self.page.locator('#pwMessages [data-pw-start]').click()
            data = self.poll_json('/api/project-tasks?project_id=' + self.pid,
                lambda d: any(t['title']=='WORKSPACE_AUTO_REPORT' and t['status']=='delivered' for t in d['tasks']))
            task = next(t for t in data['tasks'] if t['title']=='WORKSPACE_AUTO_REPORT')
            self.assertEqual(task['deliveries'][-1]['source'], 'policy')
        self.assertEqual(self.errors, [])

    def test_discussions_drafts_and_reload_are_project_owned(self):
        self.seed()
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('PROJECT_DISCUSSION_ONE')
            self.page.locator('#pwAsk').press('Control+Enter')
            self.page.wait_for_function("document.querySelector('#pwMessages').textContent.includes('PROJECT_TASK_REAL_OUTPUT')")
            self.page.wait_for_function("document.querySelector('#pwSend').getAttribute('aria-label')==='发送'")
            first = self.page.locator('[data-pw-chat]').first.get_attribute('data-pw-chat')
            self.page.locator('#pwAsk').fill('DRAFT_FIRST_CHAT')
            self.page.locator('#pwNewChat').click()
            self.page.wait_for_function("document.querySelector('#pwAsk').value===''&&!document.querySelector('#pwNewChat').disabled")
            second=self.page.locator('[data-pw-chat][aria-current="true"]').get_attribute('data-pw-chat')
            self.assertEqual(self.page.locator('#pwAsk').input_value(), '')
            self.page.locator('#pwAsk').fill('DRAFT_SECOND_CHAT')
            self.page.locator(f'[data-pw-chat="{first}"]').click()
            self.assertEqual(self.page.locator('#pwAsk').input_value(), 'DRAFT_FIRST_CHAT')
            self.page.locator('#pwProject').select_option(self.other)
            self.page.wait_for_function("id=>document.querySelector('#projectWorkspace').dataset.projectId===id", arg=self.other)
            self.assertNotIn('PROJECT_DISCUSSION_ONE', self.page.locator('#pwMessages').inner_text())
            self.assertEqual(self.page.locator('#pwAsk').input_value(), '')
            self.page.locator('#pwAsk').fill('OTHER_PROJECT_DRAFT')
            self.page.locator('#pwProject').select_option(self.pid)
            self.page.wait_for_function("id=>document.querySelector('#projectWorkspace').dataset.projectId===id", arg=self.pid)
            self.assertEqual(self.page.locator('#pwAsk').input_value(), 'DRAFT_FIRST_CHAT')
            self.page.reload(wait_until='networkidle')
            self.assertEqual(self.page.locator('#pwAsk').input_value(), 'DRAFT_FIRST_CHAT')
            self.page.locator(f'[data-pw-chat="{second}"]').click()
            self.assertEqual(self.page.locator('#pwAsk').input_value(), 'DRAFT_SECOND_CHAT')
        self.assertEqual(self.errors, [])

    def test_save_failure_keeps_file_project_and_navigation(self):
        self.seed()
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator('#flEditText').fill('# UNSAVED_MATERIAL')
        self.page.route('**/api/conv/save', lambda route: route.fulfill(status=500, content_type='application/json', body='{"error":"fixture save failure"}'))
        self.page.locator('#pwProject').select_option(self.other)
        self.page.wait_for_function("document.querySelector('#pwError').textContent.includes('保存失败')")
        self.assertEqual(self.page.locator('#pwProject').input_value(), self.pid)
        self.assertEqual(self.page.locator('#flEditText').input_value(), '# UNSAVED_MATERIAL')
        workspace_section(self.page, 'papers')
        self.assertTrue(self.page.locator('#flEditText').is_visible())
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        self.assertTrue(self.page.locator('#projectWorkspace').is_visible())
        self.page.unroute('**/api/conv/save')
        self.page.locator('#sidebar [data-nav="calendar"]').click()
        self.page.wait_for_function("route()==='calendar'")
        with workflow.WorkflowDB(self.database_path) as db:
            self.assertEqual((db.base_dir(self.pid)/'evidence.md').read_text(encoding='utf-8'), '# UNSAVED_MATERIAL')
        self.assertEqual(self.errors, [])

    def test_workflow_node_uses_current_role_and_pins_input(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            flow = workflow_layout.save_layout(db, {'name':'当前项目节点', 'nodes':[{'id':'analyze', 'step':'task', 'label':'分析节点', 'executor_role_id':'analyst'}], 'edges':[]})['layout']
            workflow_layout.select_layout(db, {'task_id':self.pid, 'layout_id':flow['id']})
            project_bindings.bind_role(db,self.pid,flow['id'],'analyst',self.binding)
        self.page.evaluate('()=>ProjectWorkspace.refresh()')
        self.page.locator('#pwScope').select_option('workflow')
        self.page.wait_for_function("document.querySelector('#pwRecipient').options.length===2")
        self.page.locator('#pwRecipient').select_option(index=1)
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        self.page.locator('#flStage .md-render').wait_for()
        self.page.locator('#pwUseMaterial').click()
        self.page.locator('#pwAsk').fill('NODE_SCOPED_ANALYSIS')
        self.page.locator('#pwSend').click()
        self.page.locator('#pwMessages [data-pw-start]').wait_for()
        data=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()
        task=data['tasks'][0]
        self.assertEqual(task['workflow_node_id'],'analyze')
        self.assertEqual(task['scope']['kind'],'workflow')
        self.assertEqual(task['scope']['allowed_binding_ids'],[self.binding])
        self.assertTrue(task['inputs'][0]['sha256'])
        self.assertEqual(self.page.locator('#pwScope').input_value(),'project')
        self.assertEqual(self.page.locator('#pwRecipient').input_value(),'binding:'+self.binding)
        with tasks_runtime.TaskModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#pwMessages [data-pw-start]').click()
            self.page.locator('#pwMessages [data-pw-output]').last.wait_for(timeout=30000)
            self.page.locator('#pwMessages [data-pw-output]').last.click()
            self.page.locator('#pwApprove').click()
            self.poll_json('/api/project-tasks?project_id='+self.pid,lambda d:d['tasks'][0]['status']=='delivered')
        self.assertEqual(self.errors, [])

    def test_team_members_follow_project_and_workflow_grants(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save('workspace-member',name='本项目成员专家',enabled=1)
            db.assistant_save('workspace-outsider',name='OUTSIDE_WORKFLOW_ASSISTANT',enabled=1)
            team=db.team_add('当前课题团队','custom',project_id=self.pid)
            db.team_member_add(team['id'],'项目负责人','leader',assistant_id='workspace-writer')
            member=db.team_member_add(team['id'],'分析成员','member',assistant_id='workspace-member')
            other=db.team_add('FOREIGN_TEAM','custom',project_id=self.other)
            db.team_member_add(other['id'],'外部成员','leader',assistant_id='workspace-outsider')
            grant=project_bindings.register(db,self.pid,'team',team['id'])
            workflow_layout.ensure_schema(db)
            flow=workflow_layout.save_layout(db,{'name':'团队节点','nodes':[{'id':'team-node','step':'task','label':'团队分析','executor_role_id':'team-role'}],'edges':[]})['layout']
            workflow_layout.select_layout(db,{'task_id':self.pid,'layout_id':flow['id']})
            project_bindings.bind_role(db,self.pid,flow['id'],'team-role',grant['id'])
        self.page.evaluate('()=>ProjectWorkspace.refresh()')
        select=self.page.locator('#pwRecipient')
        self.assertIn('FOREIGN_TEAM',select.inner_text())
        select.select_option('binding:'+grant['id'])
        self.page.locator('#pwRecipientDetail').select_option('member:'+grant['id']+':'+member['id'])
        self.page.locator('#pwAsk').fill('MEMBER_SPECIFIC_TASK')
        self.page.locator('#pwPrepareTask').click()
        self.page.locator('#pwMessages [data-pw-start]').wait_for()
        task=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'][0]
        self.assertEqual(task['member_id'],member['id'])
        self.assertEqual(task['executor_binding_id'],grant['id'])
        self.page.locator('#pwTab-chat').click()
        self.page.locator('#pwScope').select_option('workflow')
        self.page.wait_for_function("document.querySelector('#pwScope').value==='workflow' && document.querySelector('#pwRecipient').textContent.includes('团队分析')")
        self.assertIn('团队分析',select.inner_text())
        self.assertNotIn('OUTSIDE_WORKFLOW_ASSISTANT',select.inner_text())
        self.assertNotIn('FOREIGN_TEAM',select.inner_text())
        self.assertEqual(self.errors,[])

    def test_removed_target_keeps_goal_and_requires_reselection(self):
        self.seed()
        self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=self.binding)
        self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
        self.page.locator('#pwAsk').fill('KEEP_GOAL_AFTER_REVOKE')
        self.page.evaluate("async pid=>api('/api/project-tasks/bindings',{project_id:pid,kind:'assistant',target_id:'workspace-writer',enabled:false})",self.pid)
        self.page.evaluate('()=>ProjectWorkspace.refresh()')
        self.assertEqual(self.page.locator('#pwSend').get_attribute('aria-label'),'发送')
        self.page.locator('#pwPrepareTask').click()
        self.page.wait_for_function("document.querySelector('#pwError').textContent.includes('重新选择')")
        self.assertEqual(self.page.locator('#pwAsk').input_value(),'KEEP_GOAL_AFTER_REVOKE')
        self.assertEqual(self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks'],[])
        self.assertEqual(self.errors,[])

    def test_delayed_prepare_does_not_erase_the_next_draft(self):
        self.seed()
        self.page.wait_for_function("b=>[...document.querySelector('#pwRecipient').options].some(o=>o.value==='binding:'+b)",arg=self.binding)
        pending=[]
        self.page.route('**/api/project-tasks/create',lambda route:pending.append((route,route.fetch())))
        self.page.locator('#pwRecipient').select_option('binding:'+self.binding)
        self.page.locator('#pwAsk').fill('SUBMITTED_TASK')
        self.page.locator('#pwPrepareTask').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(pending),1)
        self.page.locator('#pwAsk').fill('NEXT_UNSUBMITTED_DRAFT')
        self.page.locator('#pwNewChat').click()
        pending[0][0].fulfill(response=pending[0][1])
        self.page.wait_for_function("document.querySelector('#pwNotice').textContent.includes('已准备')")
        self.assertEqual(self.page.locator('#pwAsk').input_value(),'NEXT_UNSUBMITTED_DRAFT')
        tasks=self.page.request.get(self.url+'api/project-tasks?project_id='+self.pid).json()['tasks']
        self.assertEqual([t['goal'] for t in tasks],['SUBMITTED_TASK'])
        self.assertEqual(self.errors,[])

    def test_rejected_discussion_request_restores_its_draft(self):
        self.seed()
        self.page.evaluate('()=>{requireModelKey=async()=>true;}')
        self.page.route('**/api/agent',lambda route:route.fulfill(status=400,json={'error':'REQUEST_REJECTED'}))
        self.page.locator('#pwAsk').fill('RETRY_THIS_REQUEST')
        self.page.locator('#pwSend').click()
        self.page.wait_for_function("document.querySelector('#pwError').textContent.includes('REQUEST_REJECTED')")
        self.assertEqual(self.page.locator('#pwAsk').input_value(),'RETRY_THIS_REQUEST')
        self.assertEqual(self.errors,[])

    def test_graph_edits_block_navigation_until_saved_to_original_project(self):
        self.seed()
        workspace_section(self.page,'workflow')
        self.page.locator('[data-pwg-prompt]').fill('UNSAVED_PROJECT_NODE')
        self.page.route('**/api/workflow/layouts/project',lambda route:route.fulfill(status=500,json={'error':'SAVE_FAILED'}))
        self.page.locator('#pwProject').select_option(self.other)
        self.page.wait_for_function("id=>document.querySelector('#pwProject').value===id&&!document.querySelector('#pwProject').disabled",arg=self.pid)
        self.assertEqual(self.page.locator('[data-pwg-prompt]').input_value(),'UNSAVED_PROJECT_NODE')
        self.page.unroute('**/api/workflow/layouts/project')
        self.page.locator('#pwProject').select_option(self.other)
        self.page.wait_for_function("id=>curTask===id&&document.querySelector('#projectWorkspace').dataset.projectId===id",arg=self.other)
        original=self.page.request.get(self.url+'api/workflow/layouts/state?task_id='+self.pid).json()
        other=self.page.request.get(self.url+'api/workflow/layouts/state?task_id='+self.other).json()
        self.assertIn('UNSAVED_PROJECT_NODE',str(original['layout']))
        self.assertNotIn('UNSAVED_PROJECT_NODE',str(other['layout']))
        self.assertEqual(self.errors,[])


    def test_workspace_routes_resources_and_real_render(self):
        self.seed()
        self.assertEqual(self.page.locator('#sidebar [data-nav="space"],#sidebar [data-nav="workflow"]').count(), 0)
        self.assertEqual(self.page.locator('#sidebar [data-nav="workspace"]').count(), 1)
        for section, expected in (("papers", "#projectLiterature"), ("notes", "#mdDashboard"), ("plan", ".execution-center"), ("workflow", "#pwWorkflowChoice"), ("files", "#flList")):
            workspace_section(self.page, section)
            self.page.locator(expected).wait_for(state="visible")
            self.assertTrue(self.page.locator("#pwAsk").is_hidden())
        self.assertEqual(self.errors, [])
        folder = Path(__file__).resolve().parents[1] / ".codex-tasks/20261027-project-workspace"
        self.page.screenshot(path=str(folder / "workspace-desktop.png"), full_page=True)

    def test_reading_focus_themes_small_window_and_legacy_routes(self):
        self.seed()
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        self.page.locator('#flStage .md-render').wait_for()
        self.page.locator('#pwFocus').click()
        self.assertTrue(self.page.locator('#projectWorkspace').evaluate("el=>el.classList.contains('pw-reading')"))
        self.page.locator('#pwQuote').click()
        self.page.locator('dialog input').fill('QUOTED_SAVED_EVIDENCE')
        self.page.locator('dialog [data-ok]').click()
        self.assertIn('evidence.md',self.page.locator('#pwRefs').inner_text())
        self.page.locator('[data-pw-ref]').click()
        self.page.locator('#pwFocus').click()
        folder=Path(__file__).resolve().parents[1]/'.codex-tasks/20261027-project-workspace'
        for theme in ('light','dark'):
            self.page.evaluate('theme=>setTheme(theme)',theme)
            self.page.wait_for_timeout(200)
            self.page.screenshot(path=str(folder/('workspace-reading-'+theme+'.png')))
        self.page.set_viewport_size({'width':900,'height':650})
        self.page.locator('#pwTab-chat').click()
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.page.locator('#pwSend').scroll_into_view_if_needed()
        self.assertTrue(self.page.locator('#pwSend').is_enabled())
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),902)
        self.page.screenshot(path=str(folder/'workspace-small.png'))
        for old in ('space','workflow','notes'):
            self.page.evaluate('r=>openTab(r)',old)
            self.page.wait_for_function("route()==='workspace'")
            self.assertEqual(self.page.evaluate("tabs.filter(t=>['space','workflow','notes','workspace'].includes(t.route)).length"),1)
        self.assertEqual(self.errors,[])


if __name__ == "__main__":
    unittest.main()
