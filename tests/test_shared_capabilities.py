"""Enabled profiles are reusable; execution context and denials stay project scoped."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_bindings as bindings
import project_task_execution
import project_tasks
import project_workflows
import workflow
import workflow_execution
import workflow_layout
import file_handoff
import workflow_tasks
import task_adapters
import project_task_routes


class SharedCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=workflow.WorkflowDB(Path(tmp.name)/'app.sqlite3');self.addCleanup(self.db.close)
        workflow_layout.ensure_schema(self.db)
        self.a=self.db.create_task('A');self.b=self.db.create_task('B')
        self.db.assistant_save('shared-scientist',name='Shared scientist')
        self.team=self.db.team_add('Shared laboratory',project_id=self.a)
        self.member=self.db.team_member_add(self.team['id'],'Scientist',role='leader',assistant_id='shared-scientist')

    def test_catalog_is_global_and_reading_it_does_not_create_project_instances(self):
        self.db.team_member_update(self.member['id'],note='PRIVATE_PROJECT_A_ACTIVITY',status='running')
        before=self.db.db.total_changes
        catalog=bindings.list_bindings(self.db,self.b)
        self.assertTrue(any(row['target_id']==self.team['id'] for row in catalog))
        self.assertTrue(any(row['target_id']=='shared-scientist' for row in catalog))
        self.assertEqual(before,self.db.db.total_changes)
        data=project_task_routes.route('GET','/api/project-tasks/executors',{}, {'project_id':[self.b]},self.db)
        self.assertNotIn('PRIVATE_PROJECT_A_ACTIVITY',str(data))
        bindings.register(self.db,self.b,'assistant','shared-scientist',enabled=False)
        with self.assertRaises(ValueError):bindings.resolve(self.db,self.b,'global:assistant:shared-scientist')
        self.db.assistant_save('shared-scientist',enabled=False)
        self.assertFalse(next(row for row in bindings.list_bindings(self.db,self.a) if row['target_id']==self.team['id'])['available'])

    def test_shared_team_executes_inside_caller_project_with_own_members_and_files(self):
        task=project_tasks.create(self.db,{'project_id':self.b,'title':'Independent research','executor_binding_id':'global:team:'+self.team['id'],'member_id':self.member['id'],'review_required':False},authority=project_tasks.CommandContext.ui(self.b))
        run=project_tasks.start(self.db,task['id'],authority=project_tasks.CommandContext.ui(self.b))
        seen=[]
        def execute(db,pid,prompt,**kwargs):
            seen.append((pid,kwargs['member']))
            return {'reply':'# Project B research\nActual report saved by worker.'}
        with patch.object(workflow,'run_agent',side_effect=execute):result=project_task_execution.execute(self.db,run['id'])
        self.assertEqual(result['status'],'delivered',result)
        self.assertEqual(seen[0][0],self.b)
        self.assertNotEqual(seen[0][1]['id'],self.member['id'])
        self.assertEqual(self.db.team(seen[0][1]['task_id'])['project_id'],self.b)
        self.assertTrue(all(Path(output['path']).is_relative_to(self.db.base_dir(self.b)) for output in result['outputs']))
        self.assertEqual(len(self.db.teams()),1)

    def test_one_global_workflow_definition_runs_in_both_projects(self):
        workflow_layout.ensure_schema(self.db)
        layout=workflow_layout.save_layout(self.db,{'name':'Shared research','nodes':[{'id':'analyze','step':'task','label':'Analyze','executor_binding_id':'global:team:'+self.team['id'],'member_id':self.member['id']}],'edges':[]})['layout']
        self.assertFalse(layout['nodes'][0]['executor_binding_id'])
        with patch('workflow_execution.kick'):
            for pid in (self.a,self.b):
                workflow_layout.select_layout(self.db,{'task_id':pid,'layout_id':layout['id']})
                run=workflow_execution.create(self.db,pid,'Use the shared template')
                self.assertEqual(run['layout']['id'],layout['id'])
                tasks=project_tasks.list_tasks(self.db,pid,workflow_run_id=run['id'])
                self.assertEqual(len(tasks),1)
                actor=bindings.resolve(self.db,pid,tasks[0]['executor_binding_id'],member_id=tasks[0]['member_id'])
                self.assertEqual(actor['team']['project_id'],pid)

    def test_shared_team_picker_is_read_only_and_native_assignment_is_project_local(self):
        layout=workflow_layout.save_layout(self.db,{'name':'Shared files','nodes':[{'id':'analyze','step':'task','label':'Analyze','team_id':self.team['id'],'member_id':self.member['id']}],'edges':[]})['layout']
        workflow_layout.select_layout(self.db,{'task_id':self.b,'layout_id':layout['id']})
        before=self.db.db.total_changes
        choices=file_handoff.targets(self.db,self.b)
        self.assertEqual(self.db.db.total_changes,before)
        self.assertFalse(self.db.db.execute('SELECT 1 FROM team_execution_instances').fetchone())
        self.assertEqual(choices['nodes'][0]['actors'][0]['team_id'],self.team['id'])
        board=workflow._tool_impl('team_board',self.db,self.b,{'team_id':self.team['id']})
        self.assertEqual(board['team_id'],self.team['id'])
        self.assertEqual(board['tasks'],[])
        self.assertFalse(self.db.db.execute('SELECT 1 FROM team_execution_instances').fetchone())
        with patch('project_task_execution.kick'):
            assigned=workflow._tool_impl('team_assign',self.db,self.b,{'team_id':self.team['id'],'member':self.member['id'],'prompt':'Analyze within B'})
        self.assertTrue(assigned['canonical_task_id'])
        task=project_tasks.get(self.db,assigned['canonical_task_id'])
        self.assertEqual(task['project_id'],self.b)
        self.assertNotEqual(task['latest_run']['executor_snapshot']['team_id'],self.team['id'])
        self.assertEqual(self.db.team(task['latest_run']['executor_snapshot']['team_id'])['project_id'],self.b)

    def test_publishing_changes_future_projects_but_keeps_active_snapshot_and_local_override(self):
        layout=workflow_layout.save_layout(self.db,{'name':'Reusable analysis','nodes':[{'id':'analyze','step':'task','label':'Analyze','assistant_id':'shared-scientist','prompt':'ORIGINAL_REQUIREMENTS'}],'edges':[]})['layout']
        for pid in (self.a,self.b):workflow_layout.select_layout(self.db,{'task_id':pid,'layout_id':layout['id']})
        run=workflow_tasks.prepare(self.db,self.b,'Research',idempotency_key='before-publish')
        original_task=project_tasks.get(self.db,run['settings']['task_ids']['analyze'])
        original_run=project_tasks.start(self.db,original_task['id'],authority=project_tasks.CommandContext.ui(self.b))
        draft=project_workflows.current(self.db,self.a)
        draft['nodes'][0]['prompt']='NEW_REQUIREMENTS'
        draft=project_workflows.save(self.db,self.a,{**draft,'layout_id':layout['id'],'expected_revision':draft['revision'],'expected_fingerprint':draft['fingerprint']})
        project_workflows.route('POST','/api/workflow/layouts/publish',{'project_id':self.a,'layout_id':layout['id'],'expected_fingerprint':draft['fingerprint'],'expected_template_updated_at':layout['updated_at']},{},self.db)
        self.assertEqual(project_workflows.current(self.db,self.b)['nodes'][0]['prompt'],'NEW_REQUIREMENTS')
        frozen=workflow_execution.detail(self.db,run['id'])
        self.assertEqual(frozen['layout']['nodes'][0]['prompt'],'ORIGINAL_REQUIREMENTS')
        task_adapters.validate(self.db,original_task,original_run)
