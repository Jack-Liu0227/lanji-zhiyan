"""Project DAG changes are versioned and cannot alter another project or a running snapshot."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import project_bindings
import project_workflows
import workflow
import workflow_layout
import workflow_state
import workflow_tasks
import workflow_execution
import file_handoff
import project_task_routes


class WorkflowNodeEditingTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rw-node-edit-')
        self.addCleanup(temporary.cleanup)
        self.db=workflow.WorkflowDB(Path(temporary.name)/'test.sqlite3')
        self.addCleanup(self.db.close)
        self.pid=self.db.create_task('Node editing',base_dir=str(Path(temporary.name)/'project'))
        self.other=self.db.create_task('Other nodes',base_dir=str(Path(temporary.name)/'other'))
        self.db.assistant_save('node-editor',name='Node executor',enabled=1)
        workflow_layout.ensure_schema(self.db)
        self.binding=project_bindings.register(self.db,self.pid,'assistant','node-editor')['id']
        self.foreign=project_bindings.register(self.db,self.other,'assistant','node-editor')['id']
        self.layout=workflow_layout.save_layout(self.db,{'name':'Shared node template','nodes':[
            {'id':'first','step':'task','label':'First node','executor_binding_id':self.binding},
            {'id':'second','step':'task','label':'Second node','executor_binding_id':self.binding}],
            'edges':[{'source':'first','target':'second'}]})['layout']
        workflow_layout.select_layout(self.db,{'task_id':self.pid,'layout_id':self.layout['id']})
        workflow_layout.select_layout(self.db,{'task_id':self.other,'layout_id':self.layout['id']})

    def draft(self):
        current=project_workflows.current(self.db,self.pid)
        return {**deepcopy(current),'project_id':self.pid,'layout_id':current['id'],'expected_revision':current['revision'],'expected_fingerprint':current['fingerprint']}

    def test_edit_is_project_owned_and_shared_template_is_unchanged(self):
        draft=self.draft();draft['nodes'][0].update(label='Human edited node',prompt='NEW_INSTRUCTION',review_required=True)
        value=project_workflows.save(self.db,self.pid,draft)
        self.assertEqual(value['revision'],1)
        self.assertEqual(workflow_state.definition(self.db,self.pid)['nodes'][0]['prompt'],'NEW_INSTRUCTION')
        self.assertNotEqual(project_workflows.current(self.db,self.other)['nodes'][0]['label'],'Human edited node')
        self.assertNotEqual(workflow_layout._layout(self.db,self.layout['id'])['nodes'][0]['label'],'Human edited node')

    def test_stale_revision_cycle_and_foreign_executor_are_rejected_without_overwrite(self):
        draft=self.draft();project_workflows.save(self.db,self.pid,draft)
        with self.assertRaisesRegex(ValueError,'已被修改'):project_workflows.save(self.db,self.pid,draft)
        cycle=self.draft();cycle['edges'].append({'source':'second','target':'first'})
        with self.assertRaises(ValueError):project_workflows.save(self.db,self.pid,cycle)
        foreign=self.draft();foreign['nodes'][0]['executor_binding_id']=self.foreign
        with self.assertRaises(ValueError):project_workflows.save(self.db,self.pid,foreign)
        self.assertEqual(project_workflows.current(self.db,self.pid)['revision'],1)

    def test_new_nodes_are_selectable_and_existing_run_keeps_original_graph(self):
        running=workflow_tasks.prepare(self.db,self.pid,'Before edit',idempotency_key='before-edit')
        draft=self.draft();draft['nodes'].append({'id':'human-check','step':'task','label':'Human check','review_required':True,'executor_binding_id':self.binding})
        draft['edges'].append({'source':'second','target':'human-check'})
        draft['order'].append('human-check')
        project_workflows.save(self.db,self.pid,draft)
        state=workflow_layout.select_layout(self.db,{'task_id':self.pid,'layout_id':self.layout['id'],'node_id':'human-check'})
        self.assertEqual(state['selected_node'],'human-check')
        self.assertEqual(len(workflow_execution.detail(self.db,running['id'])['layout']['nodes']),2)
        self.assertEqual(len(workflow_state.definition(self.db,self.pid)['nodes']),3)
        task_ids=workflow_execution.detail(self.db,running['id'])['settings']['task_ids']
        self.assertEqual(set(task_ids),{'first','second'})

    def test_project_nodes_share_role_picker_and_file_handoff_targets(self):
        draft=self.draft()
        draft['nodes'].append({'id':'local-node','executor_role_id':'local-role','step':'task','label':'Local node'})
        draft['order'].append('local-node')
        project_workflows.save(self.db,self.pid,draft)
        project_bindings.bind_role(self.db,self.pid,self.layout['id'],'local-role',self.binding)
        roles=project_task_routes.route('GET','/api/project-tasks/workflow-roles',{},
            {'project_id':[self.pid],'template_id':[self.layout['id']]},self.db)
        self.assertIn('local-node',{node['id'] for node in roles['template']['nodes']})
        self.assertIn('local-role',{role['role_id'] for role in roles['roles']})
        data=file_handoff.targets(self.db,self.pid)
        local=next(node for node in data['nodes'] if node['id']=='local-node')
        self.assertEqual(local['actors'][0]['assistant_id'],'node-editor')
        with self.assertRaises(ValueError):
            project_bindings.bind_role(self.db,self.other,self.layout['id'],'local-role',self.foreign)

    def test_explicit_node_binding_wins_over_role_and_target_reads_preserve_transaction(self):
        self.db.assistant_save('fallback-executor',name='Fallback executor',enabled=1)
        fallback=project_bindings.register(self.db,self.pid,'assistant','fallback-executor')['id']
        project_bindings.bind_role(self.db,self.pid,self.layout['id'],'first',fallback)
        self.db._project_workflows_initialized=False
        self.db.db.execute('SAVEPOINT picker_read')
        data=file_handoff.targets(self.db,self.pid)
        self.db.db.execute('ROLLBACK TO picker_read');self.db.db.execute('RELEASE picker_read')
        first=next(node for node in data['nodes'] if node['id']=='first')
        self.assertEqual(first['actors'][0]['assistant_id'],'node-editor')

    def test_started_file_task_keeps_project_graph_but_checks_current_executor_grant(self):
        root=self.db.base_dir(self.pid);root.mkdir(parents=True,exist_ok=True)
        (root/'material.md').write_text('Frozen file input',encoding='utf-8')
        data=file_handoff.targets(self.db,self.pid);node=data['nodes'][0];actor=node['actors'][0]
        draft=file_handoff.prepare(self.db,{'project_id':self.pid,'layout_id':data['layout_id'],'revision':data['revision'],
            'node_id':node['id'],**{key:actor[key] for key in ('assistant_id','team_id','member_id')},
            'relative':'material.md','instruction':'Inspect frozen input','request_key':'frozen-file'})
        started=file_handoff.start(self.db,draft['id'])
        config=self.draft();config['nodes'][0]['prompt']='NEXT_RUN_ONLY'
        project_workflows.save(self.db,self.pid,config)
        file_handoff.validate_run(self.db,started['run'])
        self.db.assistant_save('node-editor',enabled=0)
        with self.assertRaises(ValueError):file_handoff.validate_run(self.db,started['run'])


if __name__=='__main__':unittest.main()
