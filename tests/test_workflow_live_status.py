"""Read the same durable workflow facts shown in the conversation monitor."""
import json
import unittest
from unittest.mock import patch

from tests import test_workflow_ready_scheduler as fixture
import project_tasks as tasks
import project_task_execution as worker
import workflow_execution as engine
import workflow_live_status as live
import evoflow_sdk
import workflow_state


class LiveStatusTest(unittest.TestCase):
    setUp = fixture.WorkflowReadySchedulerTest.setUp
    layout = fixture.WorkflowReadySchedulerTest.layout
    make_run = fixture.WorkflowReadySchedulerTest.make_run

    def test_review_is_not_delivery_and_saved_boundary_is_read_only(self):
        run = self.make_run(5, [('n0','n1'),('n0','n2'),('n1','n3'),('n2','n3'),('n3','n4')], review=['n2'])
        execute = worker.execute
        def fail_branch(db, attempt, cancel):
            task = tasks.get(db, tasks.get_run(db, attempt)['task_id'])
            if task['workflow_node_id'] == 'n1':
                tasks.save_checkpoint(db, attempt, phase='tools_ready', done_tools={'saved': {'file': 'evidence.md'}},
                    pending_calls=[{'id': 'remaining'}], messages=[{'role': 'tool', 'content': 'PRIVATE_TRANSCRIPT'}])
                return tasks.fail(db, attempt, 'MODEL_TIMEOUT')
            return execute(db, attempt, cancel)
        with patch.object(worker, 'execute', side_effect=fail_branch):
            engine.advance(self.db, run['id'])
        changes = self.db.db.total_changes
        result = live.summary(self.db, run['id'])
        self.assertEqual(self.db.db.total_changes, changes)
        self.assertEqual((result['total_count'], result['delivered_count'], result['review_count']), (5, 1, 1))
        nodes = {n['node_id']: n for n in result['nodes']}
        self.assertEqual(nodes['n1']['recovery']['reusable_calls'], 1)
        self.assertEqual(nodes['n1']['recovery']['pending_calls'], 1)
        self.assertTrue(nodes['n1']['recovery']['can_continue'])
        self.assertEqual(nodes['n3']['waiting_for'], ['n1', 'n2'])
        self.assertNotIn('PRIVATE_TRANSCRIPT', json.dumps(result))
        self.assertFalse(nodes['n2']['recovery']['can_continue'])

    def test_agent_resume_keeps_authority_and_cannot_restart_uncertain_tool(self):
        run = self.make_run(3, [('n0', 'n2'), ('n1', 'n2')])
        with patch.object(worker, 'execute', side_effect=lambda db, attempt, cancel: tasks.fail(db, attempt, 'FAILED')):
            engine.advance(self.db, run['id'])
        task = tasks.get(self.db, run['settings']['task_ids']['n0'])
        tasks.save_checkpoint(self.db, task['latest_run_id'], phase='tool', inflight={'name': 'file_write'})
        recovery = tasks.get(self.db, task['id'])['recovery']
        self.assertFalse(recovery['can_continue'])
        self.assertTrue(recovery['can_restart'])
        with patch.object(engine, 'kick') as kick:
            with self.assertRaises(ValueError):
                evoflow_sdk.evoflow_supervisor(self.db, self.pid, {'action': 'resume', 'run_id': run['id'],
                    'node_id': 'n0', 'restart_node': True})
            kick.assert_not_called()
        self.assertEqual(len(tasks.get(self.db, task['id'])['runs']), 1)

    def test_empty_selected_template_does_not_break_project_state(self):
        self.db.db.execute('INSERT OR REPLACE INTO project_workflow_layouts VALUES (?,?,?,?)', (self.pid, '', '', tasks._now()))
        self.db.db.commit()
        self.db.log_step(self.pid, 'search', 'done', 'Existing history survives template deletion')
        self.assertEqual(workflow_state.states(self.db, self.pid), {})
        self.assertIsInstance(self.db.stage_now(self.pid), str)


if __name__ == '__main__':
    unittest.main()
