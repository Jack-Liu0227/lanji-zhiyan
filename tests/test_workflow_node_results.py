"""Completed-node result operations through the same boundary used by the UI."""
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_task_orchestration as orchestration
import project_bindings
import project_task_execution
import project_task_routes
import project_tasks as tasks
import task_tools
import workflow
import workflow_execution as engine
import workflow_layout
import workflow_node_workbench as nodes
import workflow_tasks
import execution_board


class WorkflowNodeResultsTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def prepare(self, steps=None, *, agent=True):
        self.layout(steps or ['task'])
        run = engine.create(self.db, self.pid)
        if agent:
            with patch.object(engine, 'kick'):
                task_tools.call(self.db, self.pid, 'workflow_start', {'id': run['id']})
        return engine.detail(self.db, run['id'])

    def finish(self, run):
        with patch.object(workflow, 'run_agent', return_value={'reply': '# ORIGINAL_EVIDENCE'}):
            run = engine.advance(self.db, run['id'])
        task = tasks.get(self.db, run['settings']['task_ids']['n0'])
        return task, run

    def read(self, task, **options):
        return nodes.read(self.db, nodes.Selection(self.pid, task['id'], options.pop('run_id', ''), options.pop('revision', None)),
                          authority=tasks.CommandContext.ui(self.pid), **options)

    def command(self, result, action, **body):
        return nodes.command(self.db, result['task']['id'], {'project_id': self.pid, 'id': result['task']['id'],
            'action': action, 'identity': result['identity'], 'request_key': 'request-' + action, **body},
            authority=tasks.CommandContext.ui(self.pid))

    def test_only_agent_started_successful_attempt_opens_and_next_node_remains_closed(self):
        run = self.prepare(['task', 'task'])
        board = next(item for item in execution_board.snapshot(self.db, self.pid)['items']
                     if item.get('execution_kind') == 'workflow')
        self.assertEqual(board['workflow_nodes'], [])
        first = tasks.get(self.db, run['settings']['task_ids']['n0'])
        with self.assertRaises(ValueError):
            self.read(first)
        first, run = self.finish(run)
        result = self.read(first)
        self.assertEqual(run['status'], 'awaiting_review')
        self.assertEqual(result['content'], '# ORIGINAL_EVIDENCE')
        self.assertTrue(result['editable'])
        next_task = tasks.get(self.db, run['settings']['task_ids']['n1'])
        self.assertFalse(next_task['node_result']['available'])
        with self.assertRaises(ValueError):
            self.read(next_task)
        board = next(item for item in execution_board.snapshot(self.db, self.pid)['items']
                     if item.get('execution_kind') == 'workflow')
        self.assertEqual([item['task_id'] for item in board['workflow_nodes']], [first['id']])
        targets = task_tools.call(self.db, self.pid, 'workflow_status', {'id': run['id']})['result_targets']
        self.assertEqual([item['task_id'] for item in targets], [first['id']])

    def test_manual_workflow_and_status_query_do_not_create_agent_origin(self):
        run = self.prepare(agent=False)
        task, run = self.finish(run)
        task_tools.call(self.db, self.pid, 'workflow_status', {'id': run['id']})
        self.assertTrue(task['node_result']['available'])
        self.assertEqual(self.read(task)['content'], '# ORIGINAL_EVIDENCE')
        self.assertNotIn('agent_invocation', run['settings'])

    def test_inflight_failed_and_corrupted_outputs_are_rejected_at_preview_endpoint(self):
        run = self.prepare()
        task, run = self.finish(run)
        selection = nodes.Selection(self.pid, task['id'])
        authority = tasks.CommandContext.ui(self.pid)
        for status in ('queued', 'running', 'failed', 'interrupted', 'cancelled'):
            self.db.db.execute('UPDATE project_task_runs SET status=? WHERE id=?', (status, task['latest_run_id']))
            self.db.db.commit()
            with self.assertRaises(ValueError):
                nodes.preview(self.db, selection, 'file:0', authority=authority)
        self.db.db.execute("UPDATE project_task_runs SET status='succeeded' WHERE id=?", (task['latest_run_id'],))
        self.db.db.commit()
        Path(task['outputs'][0]['path']).write_text('EXTERNAL_CHANGE', encoding='utf-8')
        with self.assertRaises(ValueError):
            nodes.preview(self.db, selection, 'file:0', authority=authority)

    def test_node_workspace_metadata_does_not_repeat_full_output_snapshot(self):
        task, _ = self.finish(self.prepare())
        response = project_task_routes.route(
            'GET', '/api/project-tasks/node-workspace', {},
            {'project_id': [self.pid], 'id': [task['id']]}, self.db)
        result = response['result']
        self.assertNotIn('content', result)
        self.assertNotIn('previous_content', result)
        self.assertNotIn('output_snapshot', result['run'])
        self.assertNotIn('output_snapshot', result['task']['latest_run'])

    def test_revision_preserves_history_is_idempotent_and_cannot_be_approved_from_old_view(self):
        task, _ = self.finish(self.prepare())
        original = self.read(task)
        command = {'artifact_id': 'file:0', 'text': '# HUMAN_REVISED', 'reason': 'Checked source'}
        changed = self.command(original, 'revise', **command)['result']
        repeated = self.command(original, 'revise', **command)['result']
        self.assertEqual(changed['identity']['output_revision'], 1)
        self.assertEqual(repeated['identity']['output_revision'], 1)
        with self.assertRaises(ValueError):
            self.command(original, 'revise', **{**command, 'text': 'DIFFERENT_REQUEST'})
        self.assertEqual(self.read(task, revision=0)['content'], '# ORIGINAL_EVIDENCE')
        with self.assertRaises(ValueError):
            self.command(original, 'review', approved=True)
        with patch.object(engine, 'kick'):
            accepted = self.command(changed, 'review', approved=True)['task']
            again = self.command(changed, 'review', approved=True)['task']
        self.assertEqual(accepted['status'], 'delivered')
        self.assertEqual(len(again['deliveries']), 1)
        self.assertFalse(self.read(task)['editable'])

    def test_window_drafts_survive_reload_keep_instruction_and_detect_conflicts(self):
        task, _ = self.finish(self.prepare())
        result = self.read(task)
        one = self.command(result, 'draft', artifact_id='file:0', window_id='window-one', text='DRAFT_ONE',
                           instruction='FIX_THIS_SELECTION', feedback='REVIEW_NOTE', expected_draft_version=0)['draft']
        self.command(result, 'draft', artifact_id='file:0', window_id='window-two', text='DRAFT_TWO', expected_draft_version=0)
        restored = self.read(task, window_id='window-one')['drafts'][0]
        self.assertEqual(restored['instruction'], 'FIX_THIS_SELECTION')
        self.assertEqual(restored['feedback'], 'REVIEW_NOTE')
        with self.assertRaises(ValueError):
            self.command(result, 'draft', artifact_id='file:0', window_id='window-one', text='OVERWRITE', expected_draft_version=0)
        self.command(result, 'revise', artifact_id='file:0', text='# NEW_BASELINE')
        latest = self.read(task, window_id='window-one')
        self.assertTrue(latest['drafts'][0]['stale'])
        self.assertEqual(latest['drafts'][0]['text'], one['text'])

    def test_rework_freezes_current_candidate_creates_one_attempt_and_keeps_old_result_readable(self):
        task, run = self.finish(self.prepare())
        original = self.read(task)
        body = {'instruction': 'Add a checked citation', 'selection': {'artifact_id': 'file:0', 'text': 'ORIGINAL_EVIDENCE'}}
        with patch.object(project_task_execution, 'kick'):
            changed = self.command(original, 'rework', **body)
            repeated = self.command(original, 'rework', **body)
        new_run = changed['run']
        self.assertEqual(new_run['id'], repeated['run']['id'])
        self.assertEqual(changed['task']['id'], task['id'])
        self.assertNotEqual(new_run['id'], original['run']['id'])
        self.assertEqual(new_run['input_snapshot']['base_output']['content'], '# ORIGINAL_EVIDENCE')
        self.assertEqual(Path(new_run['input_snapshot']['base_output']['files'][0]['path']).read_text(encoding='utf-8'), '# ORIGINAL_EVIDENCE')
        with self.assertRaises(ValueError):
            self.read(task)
        historical = self.read(task, run_id=original['run']['id'])
        self.assertFalse(historical['editable'])
        self.assertEqual(historical['content'], original['content'])
        with patch.object(workflow, 'run_agent', return_value={'reply': '# FIXED_BY_AGENT'}) as model:
            updated = project_task_execution.execute(self.db, new_run['id'])
        self.assertEqual(updated['status'], 'awaiting_review')
        self.assertIn('Add a checked citation', model.call_args.kwargs['extra_context'])
        self.assertIn('ORIGINAL_EVIDENCE', model.call_args.kwargs['extra_context'])

    def test_mandatory_review_survives_full_access_and_mode_changes(self):
        layout = self.layout(['task'])
        layout['nodes'][0]['manual_review_required'] = True
        workflow_layout.save_layout(self.db, layout)
        project_bindings.save_settings(self.db, self.pid, 'full_access')
        run = engine.create(self.db, self.pid)
        with patch.object(engine, 'kick'):
            task_tools.call(self.db, self.pid, 'workflow_start', {'id': run['id']})
        task, _ = self.finish(run)
        self.assertEqual(task['status'], 'awaiting_review')
        tasks.set_mode(self.db, task['id'], 'full_access', authority=tasks.CommandContext.ui(self.pid))
        self.assertEqual(tasks.get(self.db, task['id'])['status'], 'awaiting_review')

    def test_rework_rejects_changed_base_before_calling_model(self):
        task, _ = self.finish(self.prepare())
        original = self.read(task)
        with patch.object(project_task_execution, 'kick'):
            run = self.command(original, 'rework', instruction='Check again')['run']
        Path(run['input_snapshot']['base_output']['files'][0]['path']).write_text('TAMPERED', encoding='utf-8')
        with patch.object(workflow, 'run_agent') as model:
            failed = project_task_execution.execute(self.db, run['id'])
        self.assertEqual(failed['status'], 'failed')
        model.assert_not_called()
        self.assertFalse(failed['node_result']['available'])

    def test_downstream_context_prioritizes_only_current_accepted_content_after_rework(self):
        task, flow = self.finish(self.prepare(['task', 'task']))
        original = self.read(task)
        with patch.object(project_task_execution, 'kick'):
            replacement = self.command(original, 'rework', instruction='Use the checked evidence')['run']
        workflow_tasks._record_attempt(self.db, engine.detail(self.db, flow['id']), 'n0',
            tasks.get(self.db, task['id']), replacement, replacement['input_snapshot']['execution_context']['upstream_context'])
        with patch.object(workflow, 'run_agent', return_value={'reply': '# ACCEPTED_REPLACEMENT_EVIDENCE'}):
            project_task_execution.execute(self.db, replacement['id'])
        current = self.read(task)
        with patch.object(engine, 'kick'):
            self.command(current, 'review', approved=True, feedback='Checked replacement')
        with patch.object(workflow, 'run_agent', return_value={'reply': '# NEXT_NODE'}) as model:
            engine.advance(self.db, flow['id'])
        context = model.call_args.kwargs['extra_context']
        self.assertIn('ACCEPTED_REPLACEMENT_EVIDENCE', context)
        self.assertNotIn('ORIGINAL_EVIDENCE', context)
        self.assertNotIn('input_versions', context)
        self.assertEqual(len(tasks.get(self.db, task['id'])['deliveries']), 2)

    def test_new_task_can_use_explicitly_selected_unapproved_history(self):
        task, _ = self.finish(self.prepare())
        original = self.read(task)
        revised = self.command(original, 'revise', artifact_id='file:0', text='# ACCEPTED_NEW_VERSION')['result']
        with patch.object(engine, 'kick'):
            self.command(revised, 'review', approved=True)
        created = self.command(original, 'duplicate', goal='Compare the earlier result')['task']
        run = tasks.start(self.db, created['id'], authority=tasks.CommandContext.ui(self.pid))
        self.assertEqual(Path(run['input_snapshot']['assets'][0]['path']).read_text(encoding='utf-8'), '# ORIGINAL_EVIDENCE')
        self.assertEqual(created['metadata']['source_node']['output_revision'], 0)
        self.assertTrue(run['input_snapshot']['assets'][0]['allow_draft'])

    def test_other_agent_cannot_overwrite_a_pending_working_document(self):
        authority = tasks.CommandContext.ui(self.pid)
        def create(goal):
            return tasks.create(self.db, {'project_id': self.pid, 'goal': goal, 'executor_binding_id': self.grant['id']}, authority=authority)
        first = create('First document writer')
        tasks.start(self.db, first['id'], authority=authority)
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, first['id'])
        try:
            workflow.doc_write(self.db, self.pid, 'outline', '# FIRST_PENDING_DOCUMENT')
            second = create('Other document writer')
            tasks.start(self.db, second['id'], authority=authority)
            self.db.project_task_context = tasks.CommandContext.agent(self.pid, second['id'])
            with self.assertRaises(ValueError):
                workflow.doc_write(self.db, self.pid, 'outline', '# UNRELATED_OVERWRITE')
            self.assertIn('FIRST_PENDING_DOCUMENT', workflow.doc_read(self.db, self.pid, 'outline')['markdown'])
        finally:
            self.db.project_task_context = None

    def test_human_rework_can_read_and_replace_its_exact_original_working_copy(self):
        layout = self.layout(['task'])
        layout['nodes'][0]['output_contract'] = {'required_files': ['论文正文/框架.md']}
        workflow_layout.save_layout(self.db, layout)
        flow = engine.create(self.db, self.pid)
        with patch.object(engine, 'kick'):
            task_tools.call(self.db, self.pid, 'workflow_start', {'id': flow['id']})
        def write(db, pid, *_args, **_kwargs):
            workflow.doc_write(db, pid, 'outline', '# AUTHORIZED_WORKING_COPY')
            return {'reply': 'Document written'}
        with patch.object(workflow, 'run_agent', side_effect=write):
            flow = engine.advance(self.db, flow['id'])
        task = tasks.get(self.db, flow['settings']['task_ids']['n0'])
        with patch.object(project_task_execution, 'kick'):
            self.command(self.read(task), 'rework', instruction='Refine this same document')
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        try:
            current = workflow._tool_impl('doc_read', self.db, self.pid, {}, scope='outline')
            self.assertIn('AUTHORIZED_WORKING_COPY', current['markdown'])
            workflow.doc_write(self.db, self.pid, 'outline', '# REWORKED_WORKING_COPY')
            self.assertIn('REWORKED_WORKING_COPY', workflow.doc_read(self.db, self.pid, 'outline')['markdown'])
        finally:
            self.db.project_task_context = None

    def test_cross_project_agent_authority_and_forged_token_are_rejected(self):
        task, _ = self.finish(self.prepare())
        result = self.read(task)
        other = self.db.create_task('Other', base_dir=str(Path(self.tmp.name) / 'other'))
        with self.assertRaises(ValueError):
            nodes.read(self.db, nodes.Selection(other, task['id']), authority=tasks.CommandContext.ui(other))
        with self.assertRaises(ValueError):
            nodes.read(self.db, nodes.Selection(self.pid, task['id']), authority=tasks.CommandContext.agent(self.pid))
        result['identity']['workflow_run_id'] = 'forged-flow'
        with self.assertRaises(ValueError):
            self.command(result, 'review', approved=True)

    def test_http_result_boundary_uses_same_gate_and_old_review_requires_version(self):
        run = self.prepare()
        task_id = run['settings']['task_ids']['n0']
        query = {'id': [task_id], 'project_id': [self.pid]}
        with self.assertRaises(ValueError):
            project_task_routes.route('GET', '/api/project-tasks/node-result', {}, query, self.db)
        task, run = self.finish(run)
        result = project_task_routes.route('GET', '/api/project-tasks/node-result', {}, query, self.db)['result']
        self.assertEqual(result['task']['id'], task['id'])
        with self.assertRaises(ValueError):
            engine.route('POST', '/api/workflow/execution/review', {'id': run['id'], 'node_id': run['current_node'], 'approved': True}, {}, self.db)

    def test_discussion_uses_fixed_result_and_read_only_request_without_rework(self):
        task, _ = self.finish(self.prepare())
        result = self.read(task)
        import agent_jobs
        with patch.object(agent_jobs, 'start') as start:
            start.return_value.run_id = 'discussion-run'
            response = self.command(result, 'discuss', message='Check the evidence', selection={'artifact_id': 'file:0', 'text': 'ORIGINAL_EVIDENCE'})
        request = start.call_args.args[1]
        self.assertTrue(request['_node_discussion'])
        self.assertIn('ORIGINAL_EVIDENCE', request['message'])
        self.assertIn(result['identity']['task_run_id'], request['message'])
        self.assertEqual(response['chat_id'], task['chat_id'])
        self.assertEqual(tasks.get(self.db, task['id'])['status'], 'awaiting_review')
        self.assertEqual(len(tasks.get(self.db, task['id'])['runs']), 1)

    def test_permanent_task_removal_cleans_new_metadata_even_without_foreign_keys(self):
        task, _ = self.finish(self.prepare())
        result = self.read(task)
        self.command(result, 'draft', artifact_id='file:0', window_id='window-delete', text='SAVED_DRAFT', expected_draft_version=0)
        self.command(result, 'revise', artifact_id='file:0', text='# REVISION_WITH_RECEIPT')
        self.db.db.execute('DELETE FROM project_tasks WHERE id=?', (task['id'],))
        self.db.db.commit()
        for table in ('workflow_node_drafts', 'project_task_output_versions', 'workflow_node_commands', 'project_task_file_operations'):
            self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)


if __name__ == '__main__':
    unittest.main()
