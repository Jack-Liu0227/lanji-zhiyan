"""Team goals execute as durable, bounded, versioned coordinator/worker tasks."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import workflow
import project_bindings as bindings
import project_tasks as tasks
import project_task_execution as worker
import team
import team_orchestration as orchestration


class TeamOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'app.sqlite3'
        self.db = workflow.WorkflowDB(self.path)
        self.pid = self.db.create_task('Team research', base_dir=str(Path(self.tmp.name) / 'project'))
        self.db.add_model('Local test', 'http://127.0.0.1:1/v1', 'fake', 'fake', activate=True)
        self.office = self.db.team_add('Research team', 'custom', project_id=self.pid)
        self.lead = self.db.team_member_add(self.office['id'], 'Coordinator', 'leader', assistant_id='builtin-writer')
        self.members = []
        for i in range(4):
            aid = 'team-expert-' + str(i)
            self.db.assistant_save(aid, name='Expert ' + str(i), enabled=1)
            self.members.append(self.db.team_member_add(self.office['id'], 'Expert ' + str(i), 'member', assistant_id=aid))
        self.binding = bindings.register(self.db, self.pid, 'team', self.office['id'])
        self.ui = tasks.CommandContext.ui(self.pid)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.wait_idle()
        self.db.close()
        self.tmp.cleanup()

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.02)
        self.fail('Team execution did not converge')

    def wait_idle(self):
        key = str(self.path.resolve())
        self.wait_for(lambda: not any(k[0] == key for k in (*worker._workers, *worker._executing)))

    def root(self, **fields):
        return tasks.create(self.db, {'goal': 'Compare independent research evidence',
                                     'executor_binding_id': self.binding['id'], **fields}, authority=self.ui)

    def create_child(self, db, member, **fields):
        return workflow._tool_impl('task_create', db, self.pid,
            {'goal': 'Evidence for ' + member['name'], 'member_id': member['id'],
             'request_key': member['id'], **fields})['task']

    def test_dynamic_workers_overlap_respect_limit_and_require_new_aggregation(self):
        root = self.root(metadata={'upstream_context': {'history': ['PRIVATE_COORDINATOR_HISTORY']}})
        run = tasks.start(self.db, root['id'], authority=self.ui)
        live = 0
        maximum = 0
        started = []
        lock = threading.Lock()
        aggregations = []

        def model(db, pid, prompt, **kwargs):
            nonlocal live, maximum
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if current['parent_task_id']:
                self.assertNotIn('PRIVATE_COORDINATOR_HISTORY', kwargs['extra_context'])
                self.assertNotIn('doc_write', {t['function']['name'] for t in kwargs['tools']})
                with lock:
                    live += 1
                    maximum = max(maximum, live)
                    started.append(current['id'])
                self.release.wait(10)
                with lock:
                    live -= 1
                return {'reply': 'Evidence from source A, section 2; limitations recorded.'}
            if not current['children']:
                for member in self.members:
                    self.create_child(db, member)
                return {'reply': 'Research tasks dispatched; waiting for evidence.'}
            context = json.loads(kwargs['extra_context'])
            aggregations.append(context['task_inputs']['children'])
            return {'reply': 'Final synthesis based on all delivered evidence.'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            pending = worker.execute(self.db, run['id'])
            self.assertEqual(pending['status'], 'blocked')
            self.wait_for(lambda: len(started) == 3)
            children = tasks.get(self.db, root['id'])['children']
            self.assertEqual(sum(c['status'] == 'running' for c in children), 3)
            self.assertEqual(sum(c['status'] == 'queued' for c in children), 1)
            self.release.set()
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()
        result = tasks.get(self.db, root['id'])
        self.assertEqual(maximum, 3)
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(len(aggregations[0]), 4)
        self.assertTrue(all(c['delivery_id'] and c['outputs'][0]['content_hash'] for c in aggregations[0]))
        self.assertTrue(all(c['status'] == 'delivered' for c in result['children']))
        self.assertEqual(result['accepted_delivery_id'], '')
        self.assertEqual(Path(result['outputs'][0]['path']).read_text(encoding='utf-8'), 'Final synthesis based on all delivered evidence.')

    def test_dependencies_handoff_pins_delivery_and_worker_cannot_write(self):
        root = self.root()
        run = tasks.start(self.db, root['id'], authority=self.ui)
        received = []

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                first = self.create_child(db, self.members[0])
                self.create_child(db, self.members[1], dependencies=[first['id']])
            elif current['parent_task_id']:
                with self.assertRaises(PermissionError):
                    workflow._tool_impl('doc_write', db, pid, {'content': 'unauthorized'})
                received.append(tasks.get_run(db, current['latest_run_id'])['input_snapshot']['dependencies'])
            return {'reply': 'Verified evidence with source and location.'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            worker.execute(self.db, run['id'])
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0], [])
        self.assertTrue(received[1][0]['delivery_id'])
        self.assertIn('Verified evidence', received[1][0]['outputs'][0].get('body', '') or Path(received[1][0]['outputs'][0]['path']).read_text(encoding='utf-8'))

    def test_final_file_contract_is_checked_after_members_deliver(self):
        root = self.root(output_contract={'required_files': ['final.md']})
        run = tasks.start(self.db, root['id'], authority=self.ui)
        final = Path(self.db.base_dir(self.pid)) / 'final.md'

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                self.create_child(db, self.members[0])
            elif not current['parent_task_id']:
                final.write_text('Final document based on delivered evidence', encoding='utf-8')
            return {'reply': 'Research progress and evidence'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            worker.execute(self.db, run['id'])
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()
        result = tasks.get(self.db, root['id'])
        self.assertEqual(len(result['runs']), 2)
        self.assertTrue(any(f.get('original_path') == str(final.resolve()) for f in result['outputs']))

    def test_fast_workers_still_require_a_synthesis_with_their_delivery_versions(self):
        root = self.root()
        run = tasks.start(self.db, root['id'], authority=self.ui)

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                child = self.create_child(db, self.members[0])
                workflow._tool_impl('task_start', db, pid, {'id': child['id']})
                self.wait_for(lambda: tasks.get(db, child['id'])['status'] == 'delivered')
                return {'reply': 'Planning reply, without reading the returned evidence'}
            return {'reply': 'Synthesis using pinned delivery evidence'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            worker.execute(self.db, run['id'])
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()
        result = tasks.get(self.db, root['id'])
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(result['latest_run']['output_snapshot']['reply'], 'Synthesis using pinned delivery evidence')
        self.assertEqual(len(result['latest_run']['input_snapshot']['children']), 1)

    def test_stage_artifact_is_created_and_old_round_budget_does_not_block_rework(self):
        root = self.root(metadata={'stage': 'outline'})
        run = tasks.start(self.db, root['id'], authority=self.ui)

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                self.create_child(db, self.members[0])
            elif not current['parent_task_id']:
                workflow._tool_impl('doc_write', db, pid, {'markdown': '# Evidence-based outline\n\nVerified synthesis'}, scope='outline')
            return {'reply': 'Evidence and synthesis'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            worker.execute(self.db, run['id'])
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()
        self.assertIn('Verified synthesis', workflow.doc_read(self.db, self.pid, 'outline')['markdown'])
        with patch.dict(orchestration.LIMITS, {'max_rounds': 1}):
            bounded = self.root()
        first = tasks.start(self.db, bounded['id'], authority=self.ui)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Candidate report'}):
            worker.execute(self.db, first['id'])
        tasks.review(self.db, bounded['id'], approved=False, feedback='Revise evidence', authority=self.ui)
        second = tasks.start(self.db, bounded['id'], authority=self.ui)
        self.assertEqual(second['attempt'], 2)

    def test_cancellation_stops_running_and_queued_workers_without_late_delivery(self):
        root = self.root()
        run = tasks.start(self.db, root['id'], authority=self.ui)
        started = []

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id']:
                for member in self.members:
                    self.create_child(db, member)
            else:
                started.append(current['id'])
                self.release.wait(10)
            return {'reply': 'Candidate evidence'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            worker.execute(self.db, run['id'])
            self.wait_for(lambda: len(started) == 3)
            tasks.cancel(self.db, root['id'], authority=self.ui)
            self.release.set()
            self.wait_idle()
        result = tasks.get(self.db, root['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(all(c['status'] == 'cancelled' for c in result['children']))
        self.assertFalse(self.db.db.execute('SELECT 1 FROM project_task_deliveries').fetchone())

    def test_member_scope_review_and_no_old_child_budget(self):
        root = self.root(scope={'manual_review_required': True})
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, root['id'])
        child = self.create_child(self.db, self.members[0])
        self.assertTrue(child['review_required'])
        self.assertTrue(child['scope']['manual_review_required'])
        other = bindings.register(self.db, self.pid, 'assistant', 'builtin-reviewer')
        with self.assertRaises(ValueError):
            self.create_child(self.db, self.members[1], executor_binding_id=other['id'])
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, child['id'])
        with self.assertRaises((ValueError, PermissionError)):
            self.create_child(self.db, self.members[2])
        with self.assertRaises(ValueError):
            tasks.create(self.db, {'goal': 'Skip review', 'executor_binding_id': self.binding['id'],
                                  'member_id': self.members[1]['id'], 'review_required': False},
                         authority=tasks.CommandContext.agent(self.pid, root['id']))
        with patch.dict(orchestration.LIMITS, {'max_tasks': 1}):
            bounded = self.root()
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, bounded['id'])
        first = self.create_child(self.db, self.members[0])
        self.assertEqual(self.create_child(self.db, self.members[0])['id'], first['id'])
        second = self.create_child(self.db, self.members[1])
        self.assertNotEqual(first['id'], second['id'])
        direct = self.root(member_id=self.members[0]['id'])
        self.assertNotIn('_team_orchestration', direct['metadata'])

    def test_team_chat_status_and_stop_survive_memory_state_loss(self):
        old_path = team.DB_PATH
        team.DB_PATH = self.path
        self.addCleanup(setattr, team, 'DB_PATH', old_path)
        team.say(self.office['id'], 'Investigate sources', start=False)
        with patch.object(worker, 'kick'), patch.object(workflow, 'run_agent', return_value={'reply': 'Coordinator summary'}):
            team._round(self.office['id'], 6)
        state = team.chat_state(self.office['id'])
        self.assertIsNone(state['running'])
        self.assertIn(state['orchestration']['status'], ('awaiting_review', 'failed'))
        self.assertIsNone(state['orchestration'].get('workflow'))
        self.assertNotIn(self.office['id'], team.TEAM_RUNS)
        task_id = state['orchestration']['task_id']
        self.assertEqual(tasks.get(self.db, task_id)['parent_task_id'], '')
        with self.assertRaises(ValueError):
            team.say(self.office['id'], 'Do not silently drop this goal')
        self.assertNotIn('Do not silently drop', str(team.transcript(self.office['id'])))
        team.stop(self.office['id'])
        self.assertEqual(tasks.get(self.db, task_id)['status'], 'cancelled')

    def test_recovery_keeps_waiting_graph_and_resumes_saved_worker_explicitly(self):
        root = self.root()
        run = tasks.start(self.db, root['id'], authority=self.ui)

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                self.create_child(db, self.members[0])
            return {'reply': 'Evidence or final synthesis, with source.'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            with patch.object(worker, 'kick'):
                worker.execute(self.db, run['id'])
            child = tasks.get(self.db, root['id'])['children'][0]
            worker.recover(self.path)
            saved = tasks.get(self.db, child['id'])
            self.assertEqual(saved['status'], 'blocked')
            self.assertEqual(saved['latest_run']['status'], 'interrupted')
            self.assertEqual(tasks.get(self.db, root['id'])['status'], 'blocked')
            resumed = tasks.start(self.db, child['id'], authority=self.ui)
            self.assertEqual(resumed['id'], saved['latest_run_id'])
            worker.kick(self.path, resumed['id'])
            self.wait_for(lambda: tasks.get(self.db, root['id'])['status'] == 'awaiting_review')
            self.wait_idle()

    def test_workflow_team_synthesis_returns_to_the_current_human_review_checkpoint(self):
        import workflow_execution as engine
        import workflow_layout
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {'name': 'Team workflow', 'nodes': [
            {'id': 'research', 'step': 'task', 'label': 'Team research', 'executor_role_id': 'research'}], 'edges': []})['layout']
        workflow_layout.select_layout(self.db, {'task_id': self.pid, 'layout_id': layout['id']})
        bindings.bind_role(self.db, self.pid, layout['id'], 'research', self.binding['id'])
        run = engine.create(self.db, self.pid)

        def model(db, pid, prompt, **kwargs):
            current = tasks.get(db, db.project_task_context.parent_task_id)
            if not current['parent_task_id'] and not current['children']:
                self.create_child(db, self.members[0])
            return {'reply': 'Workflow evidence and final synthesis'}

        with patch.object(workflow, 'run_agent', side_effect=model):
            engine.advance(self.db, run['id'])
            self.wait_for(lambda: engine.detail(self.db, run['id'])['status'] == 'awaiting_review')
            self.wait_idle()
            current = engine.detail(self.db, run['id'])
            root = tasks.get(self.db, current['settings']['task_ids']['research'])
            self.assertEqual(len(root['runs']), 2)
            engine.review(self.db, run['id'], current['current_node'], True,
                          reviewed_run_id=root['latest_run_id'], reviewed_output_version=0)
            self.assertEqual(engine.advance(self.db, run['id'])['status'], 'succeeded')

    def test_team_leader_can_prepare_and_start_a_workflow_from_the_team_task(self):
        import workflow_layout
        import workflow_execution
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {'name': 'Team-led workflow', 'nodes': [
            {'id': 'node-a', 'step': 'task', 'label': 'Team-led node', 'executor_binding_id': self.binding['id'],
             'manual_review_required': True}], 'edges': []})['layout']
        workflow_layout.select_layout(self.db, {'task_id': self.pid, 'layout_id': layout['id']})
        root = self.root()
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, root['id'])
        prepared = workflow._tool_impl('workflow_prepare', self.db, self.pid,
                                        {'template_id': layout['id'], 'goal': 'Team leader owns every workflow node',
                                         'request_key': 'team-leader-workflow'}, scope='task:' + root['id'])
        run_id = prepared['run']['id']
        parent = tasks.get(self.db, root['id'])
        self.assertEqual(parent['metadata']['_team_workflow_run_id'], run_id)
        with patch.object(workflow_execution, 'kick') as kick:
            started = workflow._tool_impl('workflow_start', self.db, self.pid, {'id': run_id}, scope='task:' + root['id'])
            kick.assert_called_once()
        self.assertEqual(started['started_by_team_task'], root['id'])
        self.assertEqual(started['run']['id'], run_id)

    def test_any_authorized_worker_can_handoff_to_another_member_with_persisted_context(self):
        root = self.root()
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, root['id'])
        child = self.create_child(self.db, self.members[0])
        run = tasks.start(self.db, child['id'], authority=tasks.CommandContext.system(self.pid, root['id']))
        with patch.object(worker, 'kick'):
            self.db.project_task_context = tasks.CommandContext.agent(self.pid, child['id'])
            with self.assertRaises(__import__('execution').Handoff) as caught:
                workflow._tool_impl('task_handoff', self.db, self.pid,
                                    {'id': child['id'], 'target_member_id': self.members[1]['id'],
                                     'reason': '该成员负责当前数据源，继续核对页码和反例'}, scope='task:' + child['id'])
        result = caught.exception.handoff
        handed = tasks.get(self.db, child['id'])
        self.assertEqual(handed['member_id'], self.members[1]['id'])
        self.assertEqual(handed['latest_run']['id'], result['target_run_id'])
        self.assertEqual(handed['latest_run']['executor_snapshot']['member_id'], self.members[1]['id'])
        row = self.db.db.execute('SELECT * FROM agent_handoffs WHERE id=?', (result['handoff_id'],)).fetchone()
        self.assertEqual(row['target_member_id'], self.members[1]['id'])
        self.assertIn('页码', json.loads(row['context_snapshot'])['reason'])

    def test_handoff_rejects_out_of_scope_member_and_non_team_task(self):
        root = self.root()
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, root['id'])
        child = self.create_child(self.db, self.members[0])
        run = tasks.start(self.db, child['id'], authority=tasks.CommandContext.system(self.pid, root['id']))
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, child['id'])
        with self.assertRaises(ValueError):
            tasks.handoff(self.db, child['id'], 'outside-member', 'invalid', authority=self.db.project_task_context)
        assistant_binding = bindings.register(self.db, self.pid, 'assistant', 'builtin-steward')
        direct = tasks.create(self.db, {'goal': 'Direct task', 'executor_binding_id': assistant_binding['id']}, authority=self.ui)
        direct_run = tasks.start(self.db, direct['id'], authority=self.ui)
        with self.assertRaises(ValueError):
            tasks.handoff(self.db, direct['id'], self.members[1]['id'], 'not a team task', authority=self.ui)


if __name__ == '__main__':
    unittest.main()
