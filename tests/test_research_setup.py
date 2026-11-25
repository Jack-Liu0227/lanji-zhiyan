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
import workflow_layout
import workflow_tasks


class ResearchSetupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True);self.addCleanup(tmp.cleanup)
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

    def test_steward_full_research_uses_both_offices_and_needs_no_routine_approval(self):
        with patch('workflow_execution.kick') as kick:
            result = workflow._tool_impl('project_create_from_idea', self.db, self.chat,
                                         {'idea': 'Automate the complete research lifecycle', 'fully_automatic': True})
        run = workflow_execution.detail(self.db, result['run_id'])
        self.assertEqual(run['layout']['order'], workflow.STAGES)
        self.assertEqual(run['settings']['research_setup_chat_id'], self.chat)
        self.assertEqual(set(result['team_ids']), {'builtin-literature-lead', 'builtin-writing-lead'})
        tasks = project_tasks.list_tasks(self.db, result['project_id'], workflow_run_id=run['id'])
        self.assertTrue(all(not task['review_required'] and task['metadata']['_team_orchestration']['worker_ids']
                            for task in tasks))
        kick.assert_called_once_with(self.db.path, run['id'])

    def test_custom_langgraph_nodes_can_bind_an_expert_or_a_team(self):
        pid = self.db.create_task('Mixed actor workflow')
        args = {
            'idea': 'Compare two evidence streams with a team review',
            'project': pid,
            'auto_start_graph': False,
            'experts': [
                {'role_id': 'solo', 'name': 'Solo evidence expert'},
                {'role_id': 'collab', 'name': 'Collaboration specialist'},
            ],
            'workflow': {
                'nodes': [
                    {'id': 'solo-node', 'step': 'search', 'label': 'Solo search',
                     'executor_role_id': 'solo', 'execution_mode': 'assistant'},
                    {'id': 'team-node', 'step': 'task', 'label': 'Team synthesis',
                     'executor_role_id': 'collab', 'execution_mode': 'team'},
                ],
                'edges': [{'source': 'solo-node', 'target': 'team-node'}],
            },
        }
        result = workflow._tool_impl('project_create_from_idea', self.db, pid, args)
        layout = project_workflows.current(self.db, pid)
        nodes = {node['id']: node for node in layout['nodes']}
        self.assertEqual(nodes['solo-node']['execution_mode'], 'assistant')
        self.assertEqual(nodes['team-node']['execution_mode'], 'team')
        solo = workflow_layout.resolve_node(self.db, pid, layout['id'], nodes['solo-node'], materialize=False)
        team = workflow_layout.resolve_node(self.db, pid, layout['id'], nodes['team-node'], materialize=False)
        self.assertEqual(solo['execution_mode'], 'assistant')
        self.assertTrue(solo['assistant_id'])
        self.assertFalse(solo.get('team_id'))
        self.assertEqual(team['execution_mode'], 'team')
        self.assertTrue(team['team_id'])
        self.assertFalse(team.get('assistant_id'))
        self.assertEqual(result['status'], 'configured')
        run = workflow_tasks.prepare(self.db, pid, 'Compare two evidence streams', 'mixed-actors', layout['id'])
        prepared = {task['workflow_node_id']: task for task in project_tasks.list_tasks(self.db, pid, workflow_run_id=run['id'])}
        prepared_solo = project_bindings.resolve(self.db, pid, prepared['solo-node']['executor_binding_id'],
                                                 member_id=prepared['solo-node'].get('member_id', ''), materialize=False)
        prepared_team = project_bindings.resolve(self.db, pid, prepared['team-node']['executor_binding_id'],
                                                 member_id=prepared['team-node'].get('member_id', ''), materialize=False)
        self.assertEqual(prepared_solo['kind'], 'assistant')
        self.assertEqual(prepared_team['kind'], 'team')

    def test_review_stage_is_a_human_gate_in_the_frozen_run(self):
        pid = self.db.create_task('Manual review acceptance')
        args = {'idea': 'Complete a real literature review', 'project': pid,
            'graph_type': 'full_research', 'auto_start_graph': False,
            'experts': [{'role_id': node, 'name': node} for node in
                        ('search', 'download', 'map', 'write', 'review')],
            'workflow': {'nodes': [
                {'id': 'A', 'step': 'search', 'label': 'Search', 'executor_role_id': 'search',
                 'tools': ['paper_search'], 'output_contract': {
                     'required_files': ['candidate_papers.csv'], 'requires_evidence': True}},
                {'id': 'B', 'step': 'notes', 'label': 'Evidence', 'executor_role_id': 'download', 'depends_on': ['A'],
                 'tools': ['paper_focus'], 'output_contract': {
                     'required_files': ['evidence_analysis.md'], 'requires_evidence': True}},
                {'id': 'C', 'step': 'map', 'label': 'Comparison', 'executor_role_id': 'map', 'depends_on': ['A']},
                {'id': 'D', 'step': 'write', 'label': 'Draft', 'executor_role_id': 'write', 'depends_on': ['B', 'C']},
                {'id': 'E', 'step': 'review', 'label': 'Review', 'executor_role_id': 'review', 'depends_on': ['D']}],
                'edges': [{'source': source, 'target': target} for source, target in
                          [('A','B'),('A','C'),('B','D'),('C','D'),('D','E')]]}}
        configured = research_setup.configure(self.db, args, project_id=pid, origin_chat_id=self.chat)
        layout = project_workflows.current(self.db, pid, configured['layout_id'])
        review = next(node for node in layout['nodes'] if node['id'] == 'E')
        search = next(node for node in layout['nodes'] if node['id'] == 'A')
        self.assertIn('project_artifact_write', search['tools'])
        self.assertTrue(review['review_required'])
        self.assertTrue(review['manual_review_required'])
        run = workflow_tasks.prepare(self.db, pid, args['idea'], 'manual-review', configured['layout_id'])
        tasks = {task['workflow_node_id']: task for task in project_tasks.list_tasks(
            self.db, pid, workflow_run_id=run['id'])}
        task = tasks['E']
        self.assertFalse(tasks['A']['output_contract'].get('requires_evidence', False))
        self.assertTrue(tasks['B']['output_contract']['requires_evidence'])
        self.assertIn('paper_evidence_record', tasks['B']['scope']['tools'])
        self.assertIn('paper_evidence_get', tasks['B']['scope']['tools'])
        self.assertEqual(tasks['A']['output_contract']['file_rules'][
            run['layout']['nodes'][0]['output_contract']['required_files'][0]]['required_columns'], [
                'title', 'authors', 'year', 'doi_or_stable_url', 'source', 'oa_status',
                'screening_status', 'screening_reason', 'evidence_level', 'full_text_availability'])
        self.assertTrue(task['scope']['manual_review_required'])

    def test_default_literature_study_is_five_nodes_with_quality_and_storage_contracts(self):
        pid = self.db.create_task('Battery interface review')
        configured = research_setup.configure(self.db, {'idea': '全固态锂电池界面机理与改性策略综述',
            'project': pid, 'graph_type': 'literature_study', 'auto_start_graph': False},
            project_id=pid, origin_chat_id=self.chat)
        layout = project_workflows.current(self.db, pid, configured['layout_id'])
        self.assertEqual([node['id'] for node in layout['nodes']], [
            'literature_retrieval', 'mechanism_evidence', 'strategy_metrics',
            'literature_survey', 'citation_review'])
        edges = {(edge['source'], edge['target']) for edge in layout['edges']}
        self.assertIn(('literature_retrieval', 'mechanism_evidence'), edges)
        self.assertIn(('literature_retrieval', 'strategy_metrics'), edges)
        self.assertIn(('mechanism_evidence', 'literature_survey'), edges)
        self.assertIn(('strategy_metrics', 'literature_survey'), edges)
        final = layout['nodes'][-1]
        self.assertTrue(final['manual_review_required'])
        self.assertTrue(final['tools'] is None or 'paper_search' in final['tools'])
        with patch('workflow_execution.kick'):
            result = research_setup.configure(self.db, {'idea': '全固态锂电池界面机理与改性策略综述',
                'project': pid, 'graph_type': 'literature_study', 'request_key': 'quality-lifecycle',
                'workflow_name': '质量规则实跑', 'auto_start_graph': True},
                project_id=pid, origin_chat_id=self.chat)
        run = workflow_execution.detail(self.db, result['run_id'])
        self.assertTrue(run['settings']['managed_artifacts'])
        tasks = {task['workflow_node_id']: task for task in project_tasks.list_tasks(
            self.db, pid, workflow_run_id=run['id'])}
        a = tasks['literature_retrieval']['output_contract']
        self.assertFalse(a.get('requires_evidence', False))
        self.assertGreaterEqual(a['file_rules']['研究过程/' + run['id'] + '/literature_retrieval/candidate_papers.csv']['min_rows'], 8)
        candidate_columns = a['file_rules']['研究过程/' + run['id'] + '/literature_retrieval/candidate_papers.csv']['required_columns']
        self.assertIn('doi_or_stable_url', candidate_columns)
        self.assertIn('screening_reason', candidate_columns)
        self.assertIn('full_text_availability', candidate_columns)
        c = tasks['strategy_metrics']['output_contract']
        self.assertFalse(c.get('requires_evidence', False))
        metric_columns = c['file_rules']['研究过程/' + run['id'] + '/strategy_metrics/methods_metrics.csv']['required_columns']
        self.assertIn('modification_method', metric_columns)
        self.assertIn('comparability_note', metric_columns)
        self.assertIn('doi_or_stable_url', metric_columns)
        e = tasks['citation_review']['output_contract']
        self.assertEqual(e['file_rules']['最终产物/' + run['id'] + '/references.bib']['min_entries'], 8)
        self.assertTrue(tasks['citation_review']['scope']['manual_review_required'])

    def test_custom_notes_step_uses_the_same_csv_columns_in_prompt_and_frozen_contract(self):
        pid = self.db.create_task('Custom comparison review')
        args = {'idea': 'Compare interface modifications', 'graph_type': 'literature_study',
                'request_key': 'custom-notes-columns', 'auto_start_graph': True,
                'experts': [{'role_id': 'comparison', 'name': 'Comparison expert'}],
                'workflow': {'nodes': [
                    {'id': 'C', 'step': 'notes', 'label': 'Methods',
                     'executor_role_id': 'comparison', 'prompt': 'Compare the accepted papers.',
                     'output_contract': {'required_files': ['methods_metrics.csv'],
                         'file_rules': {'methods_metrics.csv': {'required_columns': ['legacy_column']}}}}
                ], 'edges': []}}
        with patch('workflow_execution.kick'):
            result = research_setup.configure(self.db, args, project_id=pid)
        run = workflow_execution.detail(self.db, result['run_id'])
        node = run['layout']['nodes'][0]
        task = project_tasks.list_tasks(self.db, pid, workflow_run_id=run['id'])[0]
        path = task['output_contract']['required_files'][0]
        columns = task['output_contract']['file_rules'][path]['required_columns']
        self.assertEqual(columns, ['citation', 'interface', 'modification_method', 'material_system',
                                  'conditions', 'metric', 'value', 'unit', 'evidence_level',
                                  'comparability_note', 'doi_or_stable_url'])
        self.assertIn(','.join(columns), node['prompt'])

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
