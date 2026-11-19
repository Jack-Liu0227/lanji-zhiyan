"""Project-owned workspace behavior in a real isolated desktop server and Edge."""
from pathlib import Path
from contextlib import closing
import http.server
import sqlite3
import threading
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime
from tests import test_project_tasks_runtime as tasks_runtime
from tests import test_connected_research_runtime as connected
import workflow
import workflow_execution
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

    def test_project_chat_and_artifact_workbench_tabs_open_a_prepared_node(self):
        self.seed()

        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            layout = workflow_layout.save_layout(db, {
                'name': 'Prepared node workbench',
                'nodes': [{'id': 'node-search', 'step': 'search', 'label': 'Prepared search',
                           'executor_binding_id': self.binding}],
                'edges': [],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': layout['id']})
            first_run = workflow_execution.create(db, self.pid, 'Prepare a searchable evidence set')
            workflow_execution._status(db, first_run['id'], 'cancelled')
            alternate = workflow_layout.save_layout(db, {
                'name': 'Alternate search workbench',
                'nodes': [{'id': 'node-search', 'step': 'search', 'label': 'Alternate search',
                           'executor_binding_id': self.binding}],
                'edges': [],
            })['layout']
            workflow_layout.select_layout(db, {'task_id': self.pid, 'layout_id': alternate['id']})
            second_run = workflow_execution.create(db, self.pid, 'Prepare another searchable evidence set')

        self.assertTrue(self.page.locator('#pwTab-chat').is_visible())
        self.assertTrue(self.page.locator('#pwTab-workbench').is_visible())
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertFalse(self.page.evaluate(
            "value=>ProjectWorkspace.openWorkflowNode(value.projectId,value.nodeId,'different-layout')",
            {'projectId': self.pid, 'nodeId': 'node-search'}))
        self.assertTrue(self.page.evaluate(
            "value=>ProjectWorkspace.openWorkflowNode(value.projectId,value.nodeId,value.layoutId)",
            {'projectId': self.pid, 'nodeId': 'node-search', 'layoutId': layout['id']}))
        self.page.locator('.wnw').wait_for()
        self.assertIn('Prepared search', self.page.locator('[data-wnw-title]').inner_text())
        self.page.locator('[data-wnw-origin]').click()
        self.assertTrue(self.page.evaluate(
            "value=>ProjectWorkspace.openWorkflowNode(value.projectId,value.nodeId,value.layoutId)",
            {'projectId': self.pid, 'nodeId': 'node-search', 'layoutId': alternate['id']}))
        self.page.locator('.wnw').wait_for()
        self.assertIn('Alternate search', self.page.locator('[data-wnw-title]').inner_text())
        self.page.locator('#pwTab-workbench').click()
        self.assertEqual(self.page.locator('#projectWorkspace').get_attribute('data-surface'), 'workbench')
        run_picker = self.page.locator('[data-pw-artifact-run]')
        run_picker.select_option(first_run['id'])
        self.page.locator('[data-pw-run-list]').get_by_text('Prepared search', exact=True).wait_for()
        run_picker.select_option(second_run['id'])
        self.page.locator('[data-pw-run-list]').get_by_text('Alternate search', exact=True).wait_for()
        first_nodes_url = self.url + 'api/project-tasks?project_id=' + self.pid + '&workflow_run_id=' + first_run['id']
        delayed = []
        self.page.route(first_nodes_url, lambda route: delayed.append(route))
        run_picker.focus()
        with self.page.expect_request(first_nodes_url):
            run_picker.select_option(first_run['id'])
        run_picker.select_option(second_run['id'])
        self.page.locator('[data-pw-run-list]').get_by_text('Alternate search', exact=True).wait_for()
        with self.page.expect_response(first_nodes_url):
            delayed[0].continue_()
        self.page.unroute(first_nodes_url)
        self.assertEqual(run_picker.input_value(), second_run['id'])
        self.assertNotIn('Prepared search', self.page.locator('[data-pw-run-list]').inner_text())
        self.assertEqual(self.page.locator('[data-pw-artifact-run]:focus').count(), 1)
        self.page.locator('[data-pw-workbench-files]').click()
        self.page.locator('#flList [data-rel="evidence.md"]').click()
        self.page.locator('#flStage').get_by_role('heading', name='Workspace evidence').wait_for()
        self.assertEqual(self.page.locator('#projectWorkspace').get_attribute('data-surface'), 'workbench')
        self.page.locator('#pwBack').click()
        self.page.locator('[data-pw-run-list]').get_by_text('Alternate search', exact=True).wait_for()
        self.page.locator('#pwTab-chat').click()
        self.assertTrue(self.page.locator('#pwAsk').is_visible())
        self.assertEqual(self.errors, [])

    def test_legacy_workbench_bookmark_and_saved_route_open_project_artifacts(self):
        self.seed()
        self.load_page('?bookmark=1#workbench')
        self.page.locator('[data-pw-workbench-files]').wait_for()
        self.assertEqual(self.page.locator('#projectWorkspace').get_attribute('data-project-id'), self.pid)
        self.assertEqual(self.page.locator('#sidebar [data-nav="workbench"]').count(), 0)
        self.assertEqual(self.page.locator('#wfPage').count(), 0)
        self.page.evaluate("localStorage.setItem('rw-route','workbench')")
        self.load_page('?restore=1')
        self.page.locator('[data-pw-workbench-files]').wait_for()
        self.assertEqual(self.page.evaluate('route()'), 'workspace')
        self.assertEqual(self.page.locator('#projectWorkspace').get_attribute('data-surface'), 'workbench')
        self.assertEqual(self.errors, [])

    def test_project_deletion_keeps_files_and_exposes_their_location(self):
        self.seed()
        root = self.temporary_root / self._testMethodName
        self.page.locator('#pwProjectSettings summary').click()
        self.page.locator('#pwProjectEdit').click()
        self.page.locator('[data-pm-delete]').click()
        removal = self.page.get_by_role('dialog', name='删除项目记录')
        removal.get_by_text(str(root), exact=True).wait_for()
        removal.get_by_role('button', name='取消').click()
        self.assertTrue((root / 'evidence.md').is_file())
        self.page.locator('[data-pm-delete]').click()
        removal.get_by_role('button', name='删除记录').click()
        removal.wait_for(state='detached')
        self.page.wait_for_function('id => !S.tasks.some(task => task.id === id)', arg=self.pid)
        self.page.evaluate("openTab('settings')")
        self.page.locator('[data-set="data"]').click()
        location = self.page.locator('.data-location').filter(has_text=str(root))
        location.get_by_role('button', name='打开文件夹').wait_for()
        location.get_by_role('button', name='移除此条记录').click()
        self.page.get_by_role('dialog', name='移除文件夹记录').get_by_role('button', name='移除', exact=True).click()
        location.wait_for(state='detached')
        self.assertTrue((root / 'evidence.md').is_file())
        self.assertEqual(self.errors, [])

    def test_find_old_project_and_clean_only_its_execution_records(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            old = db.create_task('Archived experiment')
            old_root = db.base_dir(old)
            for project in (old, self.other):
                db.db.execute('INSERT INTO execution_board_events(project_id,source,kind,created_at) VALUES (?,?,?,?)',
                              (project, 'task', 'started', '2026-11-18'))
            db.db.commit()
            backup = self.database_path.with_name(self.database_path.name + '.bak-1000')
            self.addCleanup(backup.unlink, missing_ok=True)
            with closing(sqlite3.connect(backup)) as snapshot:
                db.db.backup(snapshot)
            db.db.execute('DELETE FROM research_tasks WHERE id=?', (old,))
            db.db.commit()
        self.page.evaluate("openTab('settings')")
        self.page.locator('[data-set="data"]').click()
        self.page.get_by_role('button', name='查找旧项目').click()
        record = self.page.locator('.data-location').filter(has_text='Archived experiment')
        record.get_by_text(str(old_root), exact=True).wait_for()
        record.get_by_role('button', name='清理残留执行记录（1）').click()
        self.page.get_by_role('dialog', name='清理旧项目').get_by_role('button', name='清理执行记录').click()
        record.locator('[data-history-clean]').wait_for(state='detached')
        self.assertTrue(old_root.is_dir())
        self.assertTrue(backup.is_file())
        with workflow.WorkflowDB(self.database_path) as db:
            ids = {row[0] for row in db.db.execute('SELECT project_id FROM execution_board_events')}
            self.assertNotIn(old, ids)
            self.assertIn(self.other, ids)
        self.assertEqual(self.errors, [])

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
            self.page.wait_for_function("!document.querySelector('#pwSend').classList.contains('stop')")
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
            self.poll_json('/api/project-tasks?project_id='+self.pid,lambda data:data['tasks'][0]['status']=='awaiting_review')
            # A manually prepared node keeps its task controls; the result
            # workbench is opened only by an Agent-started workflow.
            self.page.locator('#pwMessages [data-execution-detail]').click()
            self.page.locator('#pwResource [data-wnw-title]').wait_for()
            self.page.locator('#pwResource [data-wnw-approve]').click()
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


    def test_failed_submitted_input_is_not_restored_or_duplicated(self):
        self.seed()
        class FailingModel:
            def __enter__(fixture):
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self,*_args): pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        self.send_response(503);self.send_header('Content-Type','application/json');self.end_headers()
                        self.wfile.write(b'{"error":"TEMPORARY_PROVIDER_FAILURE"}')
                fixture.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
                threading.Thread(target=fixture.server.serve_forever,daemon=True).start()
                fixture.url=f'http://127.0.0.1:{fixture.server.server_address[1]}/v1'
                return fixture
            def __exit__(fixture,*_args):
                fixture.server.shutdown();fixture.server.server_close()
        with FailingModel() as model:
            self.use_model(model.url)
            self.page.locator('#pwAsk').fill('FAILED_INPUT_SHOULD_APPEAR_ONCE')
            self.page.locator('#pwSend').click()
            self.page.wait_for_function("document.querySelector('#pwError').textContent.length > 0")
            self.page.wait_for_function("!document.querySelector('#pwSend').classList.contains('stop')")
            self.page.wait_for_function("document.querySelector('#pwSend').getAttribute('aria-label')==='发送'")
            self.assertEqual(self.page.locator('#pwAsk').input_value(),'')
            visible=self.page.locator('#pwMessages').inner_text()
            self.assertEqual(visible.count('FAILED_INPUT_SHOULD_APPEAR_ONCE'),1)
            chat=self.page.locator('[data-pw-chat][aria-current="true"]').get_attribute('data-pw-chat')
            records=self.page.request.get(self.url+'api/chat?id='+chat).json()['messages']
            self.assertEqual(sum(message.get('role')=='user' and 'FAILED_INPUT_SHOULD_APPEAR_ONCE' in message.get('content','') for message in records),1)
        self.assertEqual(self.errors,[])


if __name__ == "__main__":
    unittest.main()
