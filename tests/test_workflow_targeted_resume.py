"""Recover a failed branch without changing accepted deliveries or attempts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from tests import test_workflow_ready_scheduler as fixture
import project_task_execution as worker
import project_tasks as tasks
import workflow_execution as engine


class TargetedResumeTest(unittest.TestCase):
    setUp = fixture.WorkflowReadySchedulerTest.setUp
    layout = fixture.WorkflowReadySchedulerTest.layout
    make_run = fixture.WorkflowReadySchedulerTest.make_run

    def snapshot(self, task_id):
        task = tasks.get(self.db, task_id)
        return (task['accepted_delivery_id'], task['latest_run_id'],
                [(item['path'], hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()) for item in task['outputs']])

    def test_model_checkpoint_continues_after_independent_sibling_is_delivered(self):
        run = self.make_run(3, [('n0', 'n1'), ('n0', 'n2')])
        ids, execute = run['settings']['task_ids'], worker.execute

        def pause_branch(db, attempt, cancel):
            task = tasks.get(db, tasks.get_run(db, attempt)['task_id'])
            if task['workflow_node_id'] == 'n1':
                tasks.save_checkpoint(db, attempt, phase='model', turn=4,
                                      messages=[{'role': 'user', 'content': 'Continue saved evidence'}])
                return tasks.fail(db, attempt, 'MODEL_TIMEOUT')
            return execute(db, attempt, cancel)

        with patch.object(worker, 'execute', side_effect=pause_branch):
            pending = engine.advance(self.db, run['id'])
        failed = tasks.get(self.db, ids['n1'])
        sibling = tasks.get(self.db, ids['n2'])
        self.assertEqual(sibling['status'], 'delivered')
        metadata = failed['metadata']
        metadata['upstream_context']['history'].append({
            'task_id': sibling['id'], 'node_id': 'n2', 'delivery_id': sibling['accepted_delivery_id']})
        metadata['upstream_context']['memory'] += '\n- Independent sibling note'
        self.db.db.execute('UPDATE project_tasks SET metadata=? WHERE id=?',
                           (json.dumps(metadata), failed['id']))
        self.db.db.commit()

        with patch.object(engine, 'kick'):
            engine.resume(self.db, run['id'], node_id='n1', expected_task_run_id=failed['latest_run_id'])

        resumed = tasks.get(self.db, failed['id'])
        self.assertEqual(resumed['latest_run']['checkpoint']['turn'], 4)
        self.assertEqual(resumed['latest_run']['checkpoint']['messages'][0]['content'], 'Continue saved evidence')
        self.assertEqual(self.snapshot(ids['n2'])[0], sibling['accepted_delivery_id'])

    def test_five_node_resume_preserves_siblings_and_is_idempotent_after_reopen(self):
        run = self.make_run(5, [('n0', 'n1'), ('n0', 'n2'), ('n1', 'n3'), ('n2', 'n3'), ('n3', 'n4')])
        ids, calls, execute = run['settings']['task_ids'], Counter(), worker.execute

        def controlled(db, attempt, cancel):
            node = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            calls[node] += 1
            if node == 'n1' and calls[node] == 1:
                return tasks.fail(db, attempt, 'MODEL_TIMEOUT')
            return execute(db, attempt, cancel)

        with patch.object(worker, 'execute', side_effect=controlled):
            pending = engine.advance(self.db, run['id'])
            self.assertEqual(pending['status'], 'blocked')
            accepted = {node: self.snapshot(ids[node]) for node in ('n0', 'n2')}
            attempt = tasks.get(self.db, ids['n1'])['latest_run_id']
            failed_node = next(node for node in pending['nodes']
                               if node['checkpoint'].get('canonical_run_id') == attempt)
            failed_history = (failed_node['status'], failed_node['error'], failed_node['ended_at'])
            options = {'node_id': 'n1', 'request_key': 'retry-B', 'expected_task_run_id': attempt}
            with patch.object(engine, 'kick'):
                engine.resume(self.db, run['id'], **options)
                engine.resume(self.db, run['id'], **options)
            completed = engine.advance(self.db, run['id'])
        self.assertEqual(completed['status'], 'succeeded', completed['error'])
        self.assertEqual(calls, Counter(n0=1, n1=2, n2=1, n3=1, n4=1))
        for node, value in accepted.items():
            self.assertEqual(self.snapshot(ids[node]), value)
        from workflow import WorkflowDB
        with WorkflowDB(self.db.path) as reopened, patch.object(engine, 'kick') as kick:
            self.assertEqual(engine.resume(reopened, run['id'], **options)['id'], run['id'])
            kick.assert_not_called()
        self.assertEqual(len(tasks.get(self.db, ids['n1'])['runs']), 2)
        original = next(node for node in completed['nodes'] if node['id'] == failed_node['id'])
        self.assertEqual((original['status'], original['error'], original['ended_at']), failed_history)

    def test_targeted_retry_does_not_clear_other_failure_and_rejects_stale_attempt(self):
        run = self.make_run(3, [('n0', 'n2'), ('n1', 'n2')])
        ids = run['settings']['task_ids']
        execute = worker.execute
        def fail_roots(db, attempt, cancel):
            return tasks.fail(db, attempt, 'FAILED_ROOT')
        with patch.object(worker, 'execute', side_effect=fail_roots):
            engine.advance(self.db, run['id'])
        with patch.object(engine, 'kick'):
            with self.assertRaisesRegex(ValueError, '新的尝试'):
                engine.resume(self.db, run['id'], node_id='n0', expected_task_run_id='old')
            engine.resume(self.db, run['id'], node_id='n0', request_key='retry-only-A')
        with patch.object(worker, 'execute', wraps=execute) as resumed:
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(resumed.call_count, 1)
        self.assertEqual(pending['status'], 'blocked')
        self.assertEqual(set(pending['settings']['dag_errors']), {'n1'})
        self.assertEqual(len(tasks.get(self.db, ids['n1'])['runs']), 1)
        self.assertFalse(tasks.get(self.db, ids['n2'])['latest_run_id'])
        with patch.object(engine, 'kick'):
            engine.resume(self.db, run['id'], request_key='retry-remaining')
        self.assertEqual(engine.advance(self.db, run['id'])['status'], 'succeeded')

    def test_review_manual_and_live_workers_are_not_restarted(self):
        run = self.make_run(2, [], manual=['n0'], review=['n0'])
        pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'blocked')
        with patch.object(engine, 'kick') as kick:
            with self.assertRaisesRegex(ValueError, '没有可恢复'):
                engine.resume(self.db, run['id'])
            kick.assert_not_called()
        key = (str(Path(self.db.path).resolve()), run['id'])
        with patch.dict(engine._workers, {key: Event()}):
            with self.assertRaisesRegex(ValueError, '其他分支'):
                engine.resume(self.db, run['id'])

    def test_failed_branch_can_resume_while_independent_output_awaits_review(self):
        run = self.make_run(3, [('n0','n2'), ('n1','n2')], review=['n1'])
        execute = worker.execute
        def fail_one(db, attempt, cancel):
            task = tasks.get(db, tasks.get_run(db, attempt)['task_id'])
            return tasks.fail(db, attempt, 'BRANCH_FAILED') if task['workflow_node_id']=='n0' else execute(db, attempt, cancel)
        with patch.object(worker, 'execute', side_effect=fail_one):
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'awaiting_review')
        review_id = run['settings']['task_ids']['n1']
        review_attempt = tasks.get(self.db, review_id)['latest_run_id']
        with patch.object(engine, 'kick'):
            engine.resume(self.db, run['id'], node_id='n0')
        with patch.object(worker, 'execute', wraps=execute) as resumed:
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(resumed.call_count, 1)
        self.assertEqual(pending['status'], 'awaiting_review')
        self.assertEqual(tasks.get(self.db, review_id)['latest_run_id'], review_attempt)
        self.assertEqual(tasks.get(self.db, review_id)['status'], 'awaiting_review')

    def test_explicit_allow_model_switch_preserves_saved_checkpoint_on_dead_channel(self):
        self.db.add_model('Initial model', 'http://127.0.0.1:1/v1', 'initial-model', 'key', activate=True)
        run = self.make_run(3, [('n0', 'n1'), ('n0', 'n2')])
        ids, execute = run['settings']['task_ids'], worker.execute
        with patch.object(worker, 'execute', side_effect=lambda db, attempt, cancel: (
                tasks.save_checkpoint(db, attempt, phase='model', turn=3,
                                      done_tools={'t1': {'result': 'cached'}},
                                      messages=[{'role': 'user', 'content': 'Continue'}]),
                tasks.fail(db, attempt, 'MODEL_CHANNEL_UNAVAILABLE'))[-1]
                if tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id'] == 'n0'
                else execute(db, attempt, cancel)):
            engine.advance(self.db, run['id'])
        self.db.add_model('Backup model', 'http://127.0.0.1:2/v1', 'backup-model', 'key', activate=True)
        with patch.object(engine, 'kick'):
            with self.assertRaisesRegex(ValueError, '输入或执行配置已变化'):
                engine.resume(self.db, run['id'], node_id='n0')
            resumed = engine.resume(self.db, run['id'], node_id='n0', allow_model_switch=True)
        task = tasks.get(self.db, ids['n0'])
        self.assertEqual(task['latest_run']['checkpoint']['turn'], 3)
        self.assertIn('t1', task['latest_run']['checkpoint']['done_tools'])
        self.assertEqual(task['latest_run']['checkpoint']['model_switched_from'], 'initial-model')
        self.assertEqual(resumed['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
