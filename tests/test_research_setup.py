"""Steward setup must create real configuration and resume without duplicates."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capabilities
import project_bindings
import project_tasks
import project_workflows
import research_setup
import workflow
import workflow_execution


class ResearchSetupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(tmp.name) / 'app.sqlite3');self.addCleanup(self.db.close)
        self.chat = self.db.create_chat('Global steward')

    def test_global_tool_creates_real_experts_team_graph_and_is_idempotent(self):
        args = {'idea': 'Study transport mechanisms and prepare a research proposal', 'request_key': 'idea-001'}
        with patch('workflow_execution.kick') as kick:
            result = workflow._tool_impl('project_create_from_idea', self.db, self.chat, args)
            again = workflow._tool_impl('project_create_from_idea', self.db, self.chat, args)
        self.assertEqual(result['project_id'], again['project_id'])
        self.assertEqual(result['run_id'], again['run_id'])
        self.assertTrue(again['reused'])
        self.assertEqual(kick.call_count, 1)
        self.assertEqual(len(self.db.tasks()), 1)
        self.assertEqual(len(result['experts']), 4)
        self.assertTrue(all(self.db.assistant(row['assistant_id']) for row in result['experts']))
        self.assertEqual(len(self.db.team_members(result['team_id'])), 4)
        self.assertNotEqual(self.db.team_members(result['team_id'])[0]['assistant_id'], 'builtin-steward')
        layout = project_workflows.current(self.db, result['project_id'])
        self.assertTrue(all(node['executor_binding_id'] for node in layout['nodes']))
        self.assertEqual(layout['id'], result['layout_id'])
        tasks = project_tasks.list_tasks(self.db, result['project_id'], workflow_run_id=result['run_id'])
        self.assertEqual(len(tasks), 4)
        self.assertTrue((self.db.base_dir(result['project_id']) / result['plan_path']).is_file())
        self.assertEqual(result['status'], 'queued')

    def test_custom_dag_and_skill_use_real_ids_and_do_not_change_shared_template(self):
        pid = self.db.create_task('Existing')
        skill = self.db.base_dir(pid) / '.agents/skills/lab-method/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: lab-method\ndescription: Laboratory method\n---\nCheck evidence.', encoding='utf-8')
        before = len(self.db.assistants())
        args = {'idea': 'Design a controlled experiment', 'skills': ['lab-method'], 'auto_start_graph': False,
            'experts': [{'role_id': 'scientist', 'name': 'Lab scientist'}],
            'workflow': {'nodes': [{'id': 'design', 'step': 'task', 'label': 'Design', 'executor_role_id': 'scientist', 'prompt': 'Use the lab method'}], 'edges': []}}
        result = workflow._tool_impl('project_create_from_idea', self.db, pid, args)
        self.assertEqual(result['project_id'], pid)
        self.assertEqual(result['status'], 'configured')
        self.assertEqual(len(self.db.tasks()), 1)
        self.assertEqual(len(self.db.assistants()), before + 1)
        node = project_workflows.current(self.db, pid)['nodes'][0]
        self.assertEqual(node['skills'], ['lab-method'])
        self.assertEqual(node['prompt'], 'Use the lab method')
        with self.assertRaises(PermissionError): workflow._tool_impl('project_create_from_idea', self.db, pid, {**args, 'project': self.db.create_task('Other')})

    def test_failed_setup_resumes_existing_objects_and_configuration_conflicts_fail(self):
        args = {'idea': 'Recover interrupted research setup', 'request_key': 'recover', 'auto_start_graph': False}
        original = project_workflows.save
        with patch.object(project_workflows, 'save', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'): research_setup.configure(self.db, args)
        projects, teams, experts = len(self.db.tasks()), len(self.db.teams()), len(self.db.assistants())
        with patch.object(project_workflows, 'save', wraps=original): result = research_setup.configure(self.db, args)
        self.assertEqual((len(self.db.tasks()), len(self.db.teams()), len(self.db.assistants())), (projects, teams, experts))
        self.assertTrue(result['ok'])
        with self.assertRaises(ValueError): research_setup.configure(self.db, {**args, 'idea': 'Different configuration'})

    def test_disabled_capability_and_unavailable_skill_fail_before_creating_project(self):
        capabilities.route('POST', '/api/capabilities/binding', {'assistant_id': 'builtin-steward', 'kind': 'tool', 'item_id': 'team_create', 'enabled': False}, {}, self.db)
        with self.assertRaises(PermissionError): research_setup.configure(self.db, {'idea': 'Research goal'})
        self.assertEqual(self.db.tasks(), [])
        capabilities.route('POST', '/api/capabilities/binding', {'assistant_id': 'builtin-steward', 'kind': 'tool', 'item_id': 'team_create', 'enabled': True}, {}, self.db)
        with self.assertRaises(ValueError): research_setup.configure(self.db, {'idea': 'Research goal', 'skills': ['missing-skill']})
        self.assertEqual(self.db.tasks(), [])

    def test_new_experts_inherit_selected_chat_model_and_project_list_reports_real_counts(self):
        self.db.add_model('Selected chat model','http://127.0.0.1:9999/v1','selected','fixture')
        selected=next(model['id'] for model in self.db.models() if model['name']=='Selected chat model')
        args={'idea':'Model-scoped research','request_key':'selected-model'}
        with patch('workflow_execution.kick'):
            result=workflow._tool_impl('project_create_from_idea',self.db,self.chat,args,profile={'id':selected,'assistant_id':'builtin-steward'})
        self.assertTrue(all(self.db.assistant(expert['assistant_id'])['model_id']==selected for expert in result['experts']))
        projects=workflow._tool_impl('project_list',self.db,self.chat,{})['projects']
        counts=next(project['execution_counts'] for project in projects if project['id']==result['project_id'])
        self.assertEqual(sum(counts.values()),4)
