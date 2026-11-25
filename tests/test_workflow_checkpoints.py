"""Restart, review, composition and deterministic execution through public services."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_task_orchestration as orchestration
import project_tasks as tasks
import project_task_review
import project_bindings
import workflow
import workflow_execution as engine
import workflow_layout
import workflow_tasks
import task_tools


class WorkflowCheckpointsTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def test_restart_resumes_persisted_review_without_repeating_model_or_writes(self):
        self.layout(['task', 'task'])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'First evidence'}) as model:
            pending = engine.advance(self.db, run['id'])
            self.assertEqual(model.call_count, 1)
        cursor = pending['settings']['checkpoint']
        self.assertEqual(cursor['thread_id'], run['id'])
        self.assertEqual(cursor['next'], ['review_0'])
        self.assertEqual(cursor['interrupts'][0]['node_id'], 'n0')
        self.assertGreater(self.db.db.execute('SELECT COUNT(*) FROM checkpoints WHERE thread_id=?', (run['id'],)).fetchone()[0], 1)
        with workflow.WorkflowDB(self.db.path) as reopened:
            target = task_tools.call(reopened, self.pid, 'workflow_status', {'id': run['id']})['review_targets'][0]
            self.assertEqual(target['task_id'], run['settings']['task_ids']['n0'])
            engine.review(reopened, run['id'], pending['current_node'], True, 'Human checked')
            with patch.object(workflow, 'run_agent', return_value={'reply': 'Second evidence'}) as model:
                following = engine.advance(reopened, run['id'])
                self.assertEqual(model.call_count, 1)
            self.assertEqual(following['settings']['checkpoint']['next'], ['review_1'])
            self.assertEqual(len(following['nodes']), 2)

    def test_rejection_loops_only_current_node_and_carries_feedback(self):
        self.layout(['task', 'task'])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Evidence'}) as model:
            run = engine.advance(self.db, run['id'])
            engine.review(self.db, run['id'], run['current_node'], True)
            run = engine.advance(self.db, run['id'])
            engine.review(self.db, run['id'], run['current_node'], False, 'CHECK_PAGE_42')
            run = engine.advance(self.db, run['id'])
            self.assertEqual(model.call_count, 3)
            self.assertIn('CHECK_PAGE_42', model.call_args.kwargs['extra_context'])
        first = tasks.get(self.db, run['settings']['task_ids']['n0'])
        second = tasks.get(self.db, run['settings']['task_ids']['n1'])
        self.assertEqual(len(first['runs']), 1)
        self.assertEqual(len(second['runs']), 2)
        self.assertEqual(run['settings']['checkpoint']['next'], ['review_1'])

    def test_fixed_tool_uses_executor_permission_without_model(self):
        layout = self.layout(['task'])
        layout['nodes'][0].update(node_kind='tool', tool_name='library_overview', tool_args={})
        workflow_layout.save_layout(self.db, layout)
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent') as model:
            run = engine.advance(self.db, run['id'])
            model.assert_not_called()
        self.assertEqual(run['status'], 'awaiting_review', run['error'])
        task = tasks.get(self.db, run['settings']['task_ids']['n0'])
        self.assertTrue(json.loads(Path(task['outputs'][0]['path']).read_text(encoding='utf-8')))
        engine.review(self.db, run['id'], run['current_node'], True)
        self.assertEqual(engine.advance(self.db, run['id'])['status'], 'succeeded')

    def test_manual_delivery_passes_the_accepted_file_to_a_fixed_tool(self):
        layout = self.layout(['task', 'task'])
        layout['nodes'][0]['manual_execution'] = True
        layout['nodes'][1].update(node_kind='tool', tool_name='read_attachment', tool_args={})
        layout['edges'][0].update(artifact='reviewed.md', argument='path')
        workflow_layout.save_layout(self.db, layout)
        reviewed = Path(self.db.base_dir(self.pid)) / 'reviewed.md'
        reviewed.write_text('Checked evidence identifier MANUAL_314', encoding='utf-8')
        run = engine.create(self.db, self.pid)
        workflow_tasks.set_mode(self.db, run['id'], 'full_access')
        with patch.object(workflow, 'run_agent') as model, patch('paths.project_roots', return_value=[reviewed.parent]):
            run = engine.advance(self.db, run['id'])
            self.assertEqual(run['status'], 'blocked', run['error'])
            self.assertEqual(run['settings']['manual_pending']['node_id'], 'n0')
            run = engine.route('POST', '/api/workflow/execution/submit-manual', {
                'id': run['id'], 'project_id': self.pid, 'node_id': 'n0', 'paths': ['reviewed.md'],
            }, {}, self.db)['run']
            self.assertEqual(run['status'], 'awaiting_review', run['error'])
            engine.review(self.db, run['id'], run['current_node'], True)
            reviewed.write_text('Draft changed after acceptance', encoding='utf-8')
            run = engine.advance(self.db, run['id'])
            self.assertEqual(run['status'], 'succeeded', run['error'])
            model.assert_not_called()
        source, reader = [tasks.get(self.db, task_id) for task_id in run['settings']['task_ids'].values()]
        handoff = reader['latest_run']['input_snapshot']['handoffs'][0]
        self.assertEqual(handoff['delivery_id'], source['accepted_delivery_id'])
        self.assertIn('MANUAL_314', reader['latest_run']['output_snapshot']['content'])
        self.assertNotIn('Draft changed', reader['latest_run']['output_snapshot']['content'])

    def test_independent_branch_runs_while_other_branch_waits_for_review(self):
        layout = self.layout(['task'] * 4)
        layout['edges'] = [{'source': source, 'target': target}
                           for source, target in [('n0', 'n1'), ('n0', 'n2'), ('n1', 'n3'), ('n2', 'n3')]]
        for index, node in enumerate(layout['nodes']):
            node.update(node_kind='tool', tool_name='library_overview', tool_args={}, review_required=index == 1)
        workflow_layout.save_layout(self.db, layout)
        run = engine.create(self.db, self.pid)
        from threading import Barrier
        import project_task_execution as worker
        barrier = Barrier(2, timeout=5)
        execute = worker.execute
        branches = {run['settings']['task_ids'][node] for node in ('n1', 'n2')}

        def synchronize(db, attempt, cancel):
            if tasks.get_run(db, attempt)['task_id'] in branches:
                barrier.wait()
            return execute(db, attempt, cancel)

        with patch.object(worker, 'execute', side_effect=synchronize):
            run = engine.advance(self.db, run['id'])
        ids = run['settings']['task_ids']
        self.assertEqual(run['status'], 'awaiting_review', run['error'])
        self.assertEqual(tasks.get(self.db, ids['n1'])['status'], 'awaiting_review')
        self.assertEqual(tasks.get(self.db, ids['n2'])['status'], 'delivered')
        self.assertFalse(tasks.get(self.db, ids['n3'])['latest_run_id'])
        with workflow.WorkflowDB(self.db.path) as reopened:
            engine.review(reopened, run['id'], run['current_node'], True)
            run = engine.advance(reopened, run['id'])
            self.assertEqual(run['status'], 'succeeded', run['error'])
            tasks_after = [tasks.get(reopened, task_id) for task_id in ids.values()]
            self.assertTrue(all(len(task['runs']) == 1 for task in tasks_after))
            self.assertEqual({dep['task_id'] for dep in tasks_after[3]['latest_run']['input_snapshot']['dependencies']},
                             {ids['n1'], ids['n2']})

    def test_missing_bound_artifact_stops_before_downstream_execution(self):
        layout = self.layout(['task', 'task'])
        for node in layout['nodes']:
            node.update(node_kind='tool', tool_name='library_overview', tool_args={}, review_required=False)
        layout['nodes'][1].update(tool_name='read_attachment')
        layout['edges'][0].update(artifact='missing.csv', argument='path')
        workflow_layout.save_layout(self.db, layout)
        run = engine.create(self.db, self.pid)
        run = engine.advance(self.db, run['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertIn('missing.csv', run['error'])
        downstream = tasks.get(self.db, run['settings']['task_ids']['n1'])
        self.assertFalse(downstream['latest_run_id'])

    def test_fixed_tool_cannot_bypass_allowlist_or_dispatch_workflow(self):
        layout = self.layout(['task'])
        for name, allowed in [('library_overview', []), ('workflow_start', ['workflow_start'])]:
            with self.subTest(tool=name):
                layout['nodes'][0].update(node_kind='tool', tool_name=name, tool_args={}, tools=allowed)
                workflow_layout.save_layout(self.db, layout)
                with self.assertRaises(ValueError):
                    engine.create(self.db, self.pid)
        self.assertEqual(tasks.list_tasks(self.db, self.pid), [])

    def test_subworkflow_is_frozen_and_nested_review_remains_in_parent_run(self):
        child = self.layout(['task', 'task'])
        parent = workflow_layout.save_layout(self.db, {'name': 'Composite task', 'nodes': [
            {'id': 'study', 'step': 'task', 'label': 'Study', 'node_kind': 'workflow', 'workflow_template_id': child['id']}], 'edges': []})['layout']
        workflow_layout.select_layout(self.db, {'task_id': self.pid, 'layout_id': parent['id']})
        run = engine.create(self.db, self.pid)
        self.assertEqual(list(run['settings']['task_ids']), ['study.n0', 'study.n1'])
        child['nodes'][0]['prompt'] = 'FUTURE_RUN_ONLY'
        workflow_layout.save_layout(self.db, child)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Frozen subworkflow result'}) as model:
            run = engine.advance(self.db, run['id'])
        self.assertEqual(run['status'], 'awaiting_review', run['error'])
        self.assertNotIn('FUTURE_RUN_ONLY', model.call_args.args[2])
        self.assertEqual(run['settings']['checkpoint']['interrupts'][0]['node_id'], 'study.n0')

    def test_recursive_templates_fail_before_creating_tasks(self):
        layout = self.layout(['task'])
        layout['nodes'][0].update(node_kind='workflow', workflow_template_id=layout['id'])
        workflow_layout.save_layout(self.db, layout)
        with self.assertRaisesRegex(ValueError, '递归'):
            engine.create(self.db, self.pid)
        self.assertEqual(tasks.list_tasks(self.db, self.pid), [])

    def test_composed_team_retains_team_wide_dispatch_scope(self):
        child = self.layout(['task'])
        team = self.db.team_add('Research team', 'custom', project_id=self.pid)
        self.db.team_member_add(team['id'], 'Lead', 'leader', assistant_id='builtin-steward')
        self.db.team_member_add(team['id'], 'Reader', 'member', assistant_id='builtin-steward')
        binding = project_bindings.register(self.db, self.pid, 'team', team['id'])
        project_bindings.bind_role(self.db, self.pid, child['id'], 'role0', binding['id'])
        parent = workflow_layout.save_layout(self.db, {'name': 'Team composite', 'nodes': [
            {'id': 'team-study', 'step': 'task', 'node_kind': 'workflow', 'workflow_template_id': child['id']}], 'edges': []})['layout']
        run = workflow_tasks.prepare(self.db, self.pid, template_id=parent['id'])
        task = tasks.get(self.db, run['settings']['task_ids']['team-study.n0'])
        self.assertEqual(task['member_id'], '')
        resolved = project_bindings.resolve(self.db, self.pid, task['executor_binding_id'])
        members = {member['id'] for member in self.db.team_members(resolved['team_id'])}
        self.assertTrue(members.issubset(task['scope']['allowed_member_ids']))

    def test_composite_tool_limit_applies_to_nested_tool(self):
        child = self.layout(['task'])
        child['nodes'][0].update(node_kind='tool', tool_name='library_overview', tool_args={})
        workflow_layout.save_layout(self.db, child)
        parent = workflow_layout.save_layout(self.db, {'name': 'Restricted composite', 'nodes': [
            {'id': 'nested', 'step': 'task', 'node_kind': 'workflow', 'workflow_template_id': child['id'], 'tools': []}], 'edges': []})['layout']
        with self.assertRaisesRegex(ValueError, '未获授权'):
            workflow_tasks.prepare(self.db, self.pid, template_id=parent['id'])
        self.assertEqual(tasks.list_tasks(self.db, self.pid), [])

    def pending_graph(self):
        self.layout(['map', 'task'])
        graph = {'nodes': [{'id': 'claim', 'type': 'section', 'label': 'Original claim'},
                           {'id': 'evidence', 'type': 'data', 'label': 'Experiment'}],
                 'edges': [{'from': 'claim', 'to': 'evidence', 'label': 'supports'}]}
        def produce(db, pid, *args, **kwargs):
            workflow.map_write(db, pid, graph)
            return {'reply': 'Evidence graph created'}
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', side_effect=produce):
            run = engine.advance(self.db, run['id'])
        self.assertEqual(run['status'], 'awaiting_review', run['error'])
        task = tasks.get(self.db, run['settings']['task_ids']['n0'])
        snapshot = project_task_review.read_graph(self.db, task['id'], self.pid)
        body = {'reviewed_run_id': snapshot['run_id'], 'expected_version': snapshot['task_version'],
                'graph': snapshot['graph']}
        return run, task, body

    def test_graph_revision_is_immutable_versioned_and_used_downstream(self):
        run, task, body = self.pending_graph()
        originals = {file['path']: Path(file['path']).read_bytes() for file in task['outputs']}
        body['graph']['nodes'][0]['label'] = 'HUMAN_VERIFIED_CLAIM'
        body['graph']['edges'][0]['label'] = 'refutes'
        ui = tasks.CommandContext.ui(self.pid)
        updated = project_task_review.revise_graph(self.db, task['id'], body, authority=ui)
        self.assertEqual(updated['latest_run']['checkpoint']['output_revision'], 1)
        self.assertEqual(workflow.map_read(self.db, self.pid)['edges'][0]['label'], 'refutes')
        for path, content in originals.items():
            self.assertEqual(Path(path).read_bytes(), content)
        with self.assertRaises(ValueError):
            project_task_review.revise_graph(self.db, task['id'], body, authority=ui)
        with self.assertRaises(ValueError):
            tasks.review(self.db, task['id'], approved=True, authority=ui, reviewed_output_version=0)
        tasks.review(self.db, task['id'], approved=True, authority=ui, reviewed_output_version=1)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Downstream synthesis'}) as model:
            following = engine.advance(self.db, run['id'])
        self.assertEqual(following['status'], 'awaiting_review', following['error'])
        self.assertIn('HUMAN_VERIFIED_CLAIM', model.call_args.kwargs['extra_context'])

    def test_graph_rejects_agent_edit_invalid_edges_and_changed_working_file(self):
        _, task, body = self.pending_graph()
        with self.assertRaises(ValueError):
            project_task_review.revise_graph(self.db, task['id'], body, authority=tasks.CommandContext.agent(self.pid))
        bad = {**body, 'graph': {**body['graph'], 'edges': [{'from': 'missing', 'to': 'claim'}]}}
        with self.assertRaises(ValueError):
            project_task_review.revise_graph(self.db, task['id'], bad, authority=tasks.CommandContext.ui(self.pid))
        workflow.map_write(self.db, self.pid, {'nodes': [{'id': 'other', 'label': 'External edit'}], 'edges': []})
        with self.assertRaises(ValueError):
            project_task_review.revise_graph(self.db, task['id'], body, authority=tasks.CommandContext.ui(self.pid))
        self.assertEqual(workflow.map_read(self.db, self.pid)['nodes'][0]['id'], 'other')

    def test_failed_graph_snapshot_restores_working_graph_and_keeps_pending_version(self):
        import project_task_execution
        _, task, body = self.pending_graph()
        path = workflow.map_path(self.db, self.pid)
        original = path.read_bytes()
        body['graph']['nodes'][0]['label'] = 'UNSAVED_REVISION'
        with patch.object(project_task_execution, '_outputs', side_effect=OSError('Disk write failed')):
            with self.assertRaises(OSError):
                project_task_review.revise_graph(self.db, task['id'], body, authority=tasks.CommandContext.ui(self.pid))
        self.assertEqual(path.read_bytes(), original)
        unchanged = tasks.get(self.db, task['id'])
        self.assertEqual(unchanged['version'], task['version'])
        self.assertEqual(unchanged['output_revisions'], [])


if __name__ == '__main__':
    unittest.main()
