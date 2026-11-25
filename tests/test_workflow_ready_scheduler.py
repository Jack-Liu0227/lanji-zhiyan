"""Dependency-ready scheduling against temporary databases and real worker threads."""
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event, Lock, Thread, get_ident
import unittest
from unittest.mock import patch

from tests import test_task_orchestration as orchestration
import project_task_execution as worker
import project_tasks as tasks
import workflow
import workflow_execution as engine
import workflow_layout
import workflow_tasks


class WorkflowReadySchedulerTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def make_run(self, count, edges, *, review=(), manual=()):
        layout = self.layout(['task'] * count)
        layout['edges'] = [{'source': source, 'target': target} for source, target in edges]
        for node in layout['nodes']:
            node.update(node_kind='tool', tool_name='library_overview', tool_args={},
                        review_required=node['id'] in review, manual_execution=node['id'] in manual)
        workflow_layout.save_layout(self.db, layout)
        run = engine.create(self.db, self.pid)
        self.assertTrue(run['settings'].get('dag_scheduler'))
        return run

    @contextmanager
    def advancing(self, run, execute, *, cancel_check=None, release=()):
        result, finished = {}, Event()

        def advance():
            try:
                with workflow.WorkflowDB(self.db.path) as db:
                    result['owner'] = (db, get_ident())
                    result['run'] = engine.advance(db, run['id'], cancel_check)
            except BaseException as exc:
                result['error'] = exc
            finally:
                finished.set()

        with patch.object(worker, 'execute', side_effect=execute), patch.object(
                workflow, 'run_agent', side_effect=AssertionError('No model calls in scheduler tests')):
            thread = Thread(target=advance, name='test-workflow-driver', daemon=True)
            thread.start()
            try:
                yield result, finished
            finally:
                for event in release:
                    event.set()
                thread.join(10)
                self.assertFalse(thread.is_alive(), 'The scheduler must drain its workers before returning')
                if 'error' in result:
                    raise result['error']

    def test_fast_successors_start_and_finish_before_slow_branch_is_released(self):
        run = self.make_run(5, [('n1', 'n2'), ('n2', 'n3'), ('n0', 'n4'), ('n3', 'n4')])
        ids = run['settings']['task_ids']
        started = Barrier(2, timeout=5)
        release_slow, successor, chain, joined = Event(), Event(), Event(), Event()
        execute = worker.execute
        orchestration_calls = []
        sync = workflow_tasks._sync_workflow_node

        def synchronize(db, current, task, *args):
            orchestration_calls.append((db, get_ident()))
            return sync(db, current, task, *args)

        def controlled(db, attempt, cancel):
            task = tasks.get(db, tasks.get_run(db, attempt)['task_id'])
            nid = task['workflow_node_id']
            if nid in ('n0', 'n1'):
                started.wait()
            if nid == 'n0':
                self.assertTrue(release_slow.wait(8), 'Slow branch was never released')
            if nid == 'n2':
                parent = next(node for node in engine.detail(db, run['id'])['nodes']
                              if node['checkpoint'].get('canonical_task_id') == ids['n1'])
                self.assertEqual(parent['status'], 'delivered')
                successor.set()
            if nid == 'n3':
                chain.set()
            if nid == 'n4':
                self.assertTrue(release_slow.is_set())
                joined.set()
            return execute(db, attempt, cancel)

        with patch.object(workflow_tasks, '_sync_workflow_node', side_effect=synchronize):
            with self.advancing(run, controlled, release=[release_slow]) as (result, finished):
                self.assertTrue(successor.wait(5), 'Fast successor waited for the unrelated slow branch')
                self.assertTrue(chain.wait(5), 'The ready closure stopped after one successor')
                self.assertFalse(release_slow.is_set())
                self.assertFalse(joined.is_set(), 'Join started before every dependency delivered')
                self.assertFalse(finished.is_set())
                release_slow.set()
                self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'succeeded', result['run']['error'])
        self.assertTrue(joined.is_set())
        self.assertTrue(orchestration_calls)
        self.assertTrue(all(call == result['owner'] for call in orchestration_calls))
        for task_id in ids.values():
            self.assertEqual(len(tasks.get(self.db, task_id)['runs']), 1)
        join = tasks.get(self.db, ids['n4'])['latest_run']['input_snapshot']['dependencies']
        self.assertEqual({item['task_id'] for item in join}, {ids['n0'], ids['n3']})
        self.assertEqual(set(result['run']['settings']['checkpoint']['values']['attempted']), set(ids))

    def test_at_most_three_attempts_run_and_other_roots_wait_for_capacity(self):
        run = self.make_run(5, [])
        ids = run['settings']['task_ids']
        started = Barrier(4, timeout=5)
        release = Event()
        lock = Lock()
        active, peak, connections, calls = 0, 0, set(), Counter()
        execute = worker.execute

        def controlled(db, attempt, cancel):
            nonlocal active, peak
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            with lock:
                active += 1
                peak = max(peak, active)
                calls[nid] += 1
                if nid in ('n0', 'n1', 'n2'):
                    connections.add(id(db.db))
            try:
                if nid in ('n0', 'n1', 'n2'):
                    started.wait()
                self.assertTrue(release.wait(8))
                return execute(db, attempt, cancel)
            finally:
                with lock:
                    active -= 1

        with self.advancing(run, controlled, release=[release]) as (result, finished):
            started.wait()
            self.assertEqual(peak, 3)
            self.assertEqual(len(connections), 3)
            for nid in ('n3', 'n4'):
                self.assertFalse(tasks.get(self.db, ids[nid])['latest_run_id'])
            release.set()
            self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'succeeded', result['run']['error'])
        self.assertEqual(peak, 3)
        self.assertEqual(calls, Counter({nid: 1 for nid in ids}))

    def test_review_holds_only_its_descendants_and_resume_does_not_repeat_the_closure(self):
        run = self.make_run(6, [('n2', 'n3'), ('n3', 'n4'), ('n0', 'n5'),
                                ('n1', 'n5'), ('n4', 'n5')], review=['n0'])
        ids = run['settings']['task_ids']
        started = Barrier(3, timeout=5)
        release_slow, independent = Event(), Event()
        execute = worker.execute
        calls = Counter()

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            calls[nid] += 1
            if nid in ('n0', 'n1', 'n2'):
                started.wait()
            if nid == 'n1':
                self.assertTrue(release_slow.wait(8))
            if nid == 'n4':
                independent.set()
            return execute(db, attempt, cancel)

        with self.advancing(run, controlled, release=[release_slow]) as (result, finished):
            self.assertTrue(independent.wait(5), 'Review stopped an independent ready branch')
            self.assertFalse(tasks.get(self.db, ids['n5'])['latest_run_id'])
            release_slow.set()
            self.assertTrue(finished.wait(8))
        pending = result['run']
        self.assertEqual(pending['status'], 'awaiting_review', pending['error'])
        self.assertEqual(tasks.get(self.db, ids['n0'])['status'], 'awaiting_review')
        self.assertEqual(pending['settings']['checkpoint']['next'], ['pause'])
        self.assertEqual(pending['settings']['checkpoint']['interrupts'][0]['node_id'], 'n0')
        self.assertEqual(set(pending['settings']['checkpoint']['values']['attempted']), set(ids) - {'n5'})
        deliveries = {nid: tasks.get(self.db, ids[nid])['accepted_delivery_id']
                      for nid in ('n1', 'n2', 'n3', 'n4')}
        with workflow.WorkflowDB(self.db.path) as reopened:
            engine.review(reopened, run['id'], pending['current_node'], True)
            with patch.object(worker, 'execute', wraps=execute) as resumed:
                complete = engine.advance(reopened, run['id'])
                again = engine.advance(reopened, run['id'])
            self.assertEqual(complete['status'], 'succeeded', complete['error'])
            self.assertEqual(again['status'], 'succeeded')
            self.assertEqual(resumed.call_count, 1)
            for nid, task_id in ids.items():
                task = tasks.get(reopened, task_id)
                self.assertEqual(len(task['runs']), 1)
                if nid in deliveries:
                    self.assertEqual(task['accepted_delivery_id'], deliveries[nid])
            dependencies = tasks.get(reopened, ids['n5'])['latest_run']['input_snapshot']['dependencies']
            self.assertEqual({item['task_id']: item['delivery_id'] for item in dependencies},
                             {ids[nid]: tasks.get(reopened, ids[nid])['accepted_delivery_id']
                              for nid in ('n0', 'n1', 'n4')})
        self.assertEqual(calls, Counter({nid: 1 for nid in ids if nid != 'n5'}))

    def test_manual_node_waits_while_automatic_branch_delivers_and_resumes_once(self):
        run = self.make_run(4, [('n1', 'n2'), ('n0', 'n3'), ('n2', 'n3')],
                            manual=['n0'], review=['n0'])
        ids = run['settings']['task_ids']
        with patch.object(workflow, 'run_agent') as model:
            pending = engine.advance(self.db, run['id'])
            self.assertEqual(pending['status'], 'blocked', pending['error'])
            self.assertEqual(pending['settings']['manual_pending']['node_id'], 'n0')
            self.assertFalse(tasks.get(self.db, ids['n0'])['latest_run_id'])
            self.assertFalse(tasks.get(self.db, ids['n3'])['latest_run_id'])
            self.assertEqual(tasks.get(self.db, ids['n2'])['status'], 'delivered')
            pending = workflow_tasks.submit_manual(self.db, pending, {'node_id': 'n0', 'text': 'Checked manual evidence'})
            self.assertEqual(pending['status'], 'awaiting_review')
            engine.review(self.db, run['id'], pending['current_node'], True)
            with patch.object(worker, 'execute', wraps=worker.execute) as resumed:
                complete = engine.advance(self.db, run['id'])
            self.assertEqual(complete['status'], 'succeeded', complete['error'])
            self.assertEqual(resumed.call_count, 1)
            model.assert_not_called()
        for task_id in ids.values():
            self.assertEqual(len(tasks.get(self.db, task_id)['runs']), 1)

    def test_failed_branch_is_attempted_once_and_independent_successors_keep_running(self):
        run = self.make_run(5, [('n2', 'n3'), ('n0', 'n4'), ('n1', 'n4'), ('n3', 'n4')])
        ids = run['settings']['task_ids']
        started = Barrier(3, timeout=5)
        release_slow, independent = Event(), Event()
        execute = worker.execute
        calls = Counter()

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            calls[nid] += 1
            if nid in ('n0', 'n1', 'n2'):
                started.wait()
            if nid == 'n0':
                return tasks.fail(db, attempt, 'FAILED_BRANCH')
            if nid == 'n1':
                self.assertTrue(release_slow.wait(8))
            if nid == 'n3':
                independent.set()
            return execute(db, attempt, cancel)

        with self.advancing(run, controlled, release=[release_slow]) as (result, finished):
            self.assertTrue(independent.wait(5), 'Failure stopped an independent ready successor')
            release_slow.set()
            self.assertTrue(finished.wait(8))
        pending = result['run']
        self.assertEqual(pending['status'], 'blocked', pending['error'])
        self.assertEqual(pending['settings']['dag_errors'], {'n0': 'FAILED_BRANCH'})
        self.assertEqual(calls, Counter({nid: 1 for nid in ('n0', 'n1', 'n2', 'n3')}))
        with workflow.WorkflowDB(self.db.path) as reopened, patch.object(worker, 'execute') as resumed:
            pending = engine.advance(reopened, run['id'])
            resumed.assert_not_called()
            self.assertEqual(pending['status'], 'blocked')
        self.assertEqual(len(tasks.get(self.db, ids['n0'])['runs']), 1)
        self.assertFalse(tasks.get(self.db, ids['n4'])['latest_run_id'])

    def test_new_successor_prepare_failure_is_recorded_and_not_retried(self):
        run = self.make_run(4, [('n0', 'n1'), ('n1', 'n3'), ('n2', 'n3')])
        ids = run['settings']['task_ids']
        start = tasks.start
        calls = Counter()

        def prepare(db, task_id, **kwargs):
            calls[task_id] += 1
            if task_id == ids['n1']:
                raise ValueError('SUCCESSOR_PREPARE_FAILED')
            return start(db, task_id, **kwargs)

        with patch.object(tasks, 'start', side_effect=prepare):
            pending = engine.advance(self.db, run['id'])
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'blocked', pending['error'])
        self.assertEqual(pending['settings']['dag_errors'], {'n1': 'SUCCESSOR_PREPARE_FAILED'})
        self.assertEqual(calls, Counter({ids[nid]: 1 for nid in ('n0', 'n1', 'n2')}))
        self.assertFalse(tasks.get(self.db, ids['n1'])['latest_run_id'])
        self.assertFalse(tasks.get(self.db, ids['n3'])['latest_run_id'])

    def test_unknown_tool_boundary_blocks_retry_until_explicit_restart(self):
        run = self.make_run(4, [('n1', 'n2'), ('n0', 'n3'), ('n2', 'n3')])
        ids = run['settings']['task_ids']
        execute = worker.execute
        calls = Counter()

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            calls[nid] += 1
            if nid == 'n0':
                tasks.save_checkpoint(db, attempt, phase='tool')
                return tasks.interrupt(db, attempt, 'UNCERTAIN_TOOL_RESULT')
            return execute(db, attempt, cancel)

        with patch.object(worker, 'execute', side_effect=controlled):
            pending = engine.advance(self.db, run['id'])
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'blocked', pending['error'])
        self.assertEqual(calls, Counter({nid: 1 for nid in ('n0', 'n1', 'n2')}))
        self.assertEqual(tasks.get(self.db, ids['n0'])['status'], 'blocked')
        self.assertFalse(tasks.get(self.db, ids['n3'])['latest_run_id'])
        with patch.object(engine, 'kick'):
            with self.assertRaisesRegex(ValueError, '工具结果未确认'):
                engine.resume(self.db, run['id'])
            engine.resume(self.db, run['id'], restart_node=True)
        with patch.object(worker, 'execute', wraps=execute) as resumed:
            complete = engine.advance(self.db, run['id'])
        self.assertEqual(complete['status'], 'succeeded', complete['error'])
        self.assertEqual(resumed.call_count, 2)
        self.assertEqual(len(tasks.get(self.db, ids['n0'])['runs']), 2)
        for nid in ('n1', 'n2', 'n3'):
            self.assertEqual(len(tasks.get(self.db, ids[nid])['runs']), 1)

    def test_review_rejection_retries_only_that_branch(self):
        run = self.make_run(4, [('n1', 'n2'), ('n0', 'n3'), ('n2', 'n3')], review=['n0'])
        ids = run['settings']['task_ids']
        pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'awaiting_review', pending['error'])
        original = tasks.get(self.db, ids['n0'])['latest_run_id']
        engine.review(self.db, run['id'], pending['current_node'], False, 'Check the source again')
        with patch.object(worker, 'execute', wraps=worker.execute) as retried:
            pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'awaiting_review', pending['error'])
        self.assertEqual(retried.call_count, 1)
        with self.assertRaisesRegex(ValueError, '新的尝试'):
            engine.review(self.db, run['id'], pending['current_node'], True, reviewed_run_id=original)
        engine.review(self.db, run['id'], pending['current_node'], True)
        complete = engine.advance(self.db, run['id'])
        self.assertEqual(complete['status'], 'succeeded', complete['error'])
        self.assertEqual(len(tasks.get(self.db, ids['n0'])['runs']), 2)
        for nid in ('n1', 'n2', 'n3'):
            self.assertEqual(len(tasks.get(self.db, ids[nid])['runs']), 1)

    def test_cancellation_prevents_dispatch_and_waits_for_running_worker_to_exit(self):
        run = self.make_run(3, [('n1', 'n2')])
        ids = run['settings']['task_ids']
        started = Barrier(2, timeout=5)
        cancelled, observed, release_slow = Event(), Event(), Event()
        execute = worker.execute

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            started.wait()
            if nid == 'n0':
                self.assertTrue(cancelled.wait(5))
                self.assertTrue(cancel())
                observed.set()
                self.assertTrue(release_slow.wait(8))
                return execute(db, attempt, cancel)
            result = execute(db, attempt, cancel)
            cancelled.set()
            return result

        with self.advancing(run, controlled, cancel_check=cancelled.is_set,
                            release=[cancelled, release_slow]) as (result, finished):
            self.assertTrue(observed.wait(5), 'Running worker did not observe cancellation')
            self.assertFalse(finished.is_set(), 'Scheduler returned before the running worker exited')
            self.assertFalse(tasks.get(self.db, ids['n2'])['latest_run_id'])
            release_slow.set()
            self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'cancelled', result['run']['error'])
        self.assertEqual(tasks.get(self.db, ids['n0'])['status'], 'cancelled')
        self.assertEqual(tasks.get(self.db, ids['n1'])['status'], 'delivered')
        self.assertFalse(tasks.get(self.db, ids['n2'])['latest_run_id'])

    def test_cancelled_worker_signals_other_workers_without_external_callback(self):
        run = self.make_run(3, [('n1', 'n2')])
        ids = run['settings']['task_ids']
        started = Barrier(2, timeout=5)
        observed, release_slow = Event(), Event()

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            started.wait()
            if nid == 'n1':
                return tasks.fail(db, attempt, 'Cancelled branch', cancelled=True)
            for _ in range(500):
                if cancel():
                    observed.set()
                    break
                release_slow.wait(0.01)
            self.assertTrue(observed.is_set(), 'Cancellation was not propagated to the other worker')
            self.assertTrue(release_slow.wait(8))
            return tasks.fail(db, attempt, 'Observed cancellation', cancelled=True)

        with self.advancing(run, controlled, release=[release_slow]) as (result, finished):
            self.assertTrue(observed.wait(6))
            self.assertFalse(finished.is_set())
            self.assertFalse(tasks.get(self.db, ids['n2'])['latest_run_id'])
            release_slow.set()
            self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'cancelled', result['run']['error'])

    def test_database_cancellation_is_observed_while_all_workers_are_running(self):
        run = self.make_run(3, [('n1', 'n2')])
        ids = run['settings']['task_ids']
        started = Barrier(3, timeout=5)
        observed, release = Event(), Event()

        def controlled(db, attempt, cancel):
            started.wait()
            for _ in range(500):
                if cancel():
                    observed.set()
                    break
                release.wait(0.01)
            self.assertTrue(observed.is_set())
            self.assertTrue(release.wait(8))
            return tasks.fail(db, attempt, 'Workflow cancelled', cancelled=True)

        with self.advancing(run, controlled, release=[release]) as (result, finished):
            started.wait()
            workflow_tasks.cancel(self.db, run['id'])
            self.assertTrue(observed.wait(6), 'The scheduler did not observe persisted cancellation')
            self.assertFalse(finished.is_set())
            self.assertFalse(tasks.get(self.db, ids['n2'])['latest_run_id'])
            release.set()
            self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'cancelled', result['run']['error'])
        self.assertTrue(all(tasks.get(self.db, task_id)['status'] == 'cancelled' for task_id in ids.values()))

    def test_recovery_replays_dispatch_without_repeating_delivered_successors(self):
        run = self.make_run(4, [('n1', 'n2'), ('n0', 'n3'), ('n2', 'n3')])
        ids = run['settings']['task_ids']
        started = Barrier(2, timeout=5)
        interrupted = Event()
        execute, sync = worker.execute, workflow_tasks._sync_workflow_node

        def controlled(db, attempt, cancel):
            nid = tasks.get(db, tasks.get_run(db, attempt)['task_id'])['workflow_node_id']
            if nid in ('n0', 'n1'):
                started.wait()
            if nid == 'n0':
                for _ in range(500):
                    if cancel():
                        interrupted.set()
                        return tasks.interrupt(db, attempt, 'Simulated service restart')
                    interrupted.wait(0.01)
                self.fail('In-flight worker was not drained after orchestration failed')
            return execute(db, attempt, cancel)

        def crash_after_successor(db, current, task, *args):
            sync(db, current, task, *args)
            if task['workflow_node_id'] == 'n2':
                raise RuntimeError('SIMULATED_ORCHESTRATOR_RESTART')

        with patch.object(workflow_tasks, '_sync_workflow_node', side_effect=crash_after_successor):
            with self.advancing(run, controlled) as (result, finished):
                self.assertTrue(finished.wait(8))
        self.assertEqual(result['run']['status'], 'failed')
        self.assertTrue(interrupted.is_set())
        saved_attempt = tasks.get(self.db, ids['n0'])['latest_run_id']
        accepted = {nid: tasks.get(self.db, ids[nid])['accepted_delivery_id'] for nid in ('n1', 'n2')}
        self.assertGreater(self.db.db.execute('SELECT COUNT(*) FROM checkpoints WHERE thread_id=?',
                                             (run['id'],)).fetchone()[0], 0)
        with workflow.WorkflowDB(self.db.path) as reopened, patch.object(worker, 'execute', wraps=execute) as resumed:
            complete = engine.advance(reopened, run['id'])
            self.assertEqual(complete['status'], 'succeeded', complete['error'])
            self.assertEqual(resumed.call_count, 2)
            self.assertEqual(tasks.get(reopened, ids['n0'])['latest_run_id'], saved_attempt)
            for nid, task_id in ids.items():
                task = tasks.get(reopened, task_id)
                self.assertEqual(len(task['runs']), 1)
                if nid in accepted:
                    self.assertEqual(task['accepted_delivery_id'], accepted[nid])

    def test_resume_rejects_changed_accepted_output_before_successor_can_deliver(self):
        run = self.make_run(4, [('n0', 'n1'), ('n1', 'n3'), ('n2', 'n3')], review=['n0'])
        ids = run['settings']['task_ids']
        pending = engine.advance(self.db, run['id'])
        self.assertEqual(pending['status'], 'awaiting_review', pending['error'])
        source = tasks.get(self.db, ids['n0'])
        with self.assertRaisesRegex(ValueError, '修订版本'):
            engine.review(self.db, run['id'], pending['current_node'], True, reviewed_output_version=1)
        engine.review(self.db, run['id'], pending['current_node'], True, reviewed_output_version=0)
        Path(source['outputs'][0]['path']).write_text('Changed after human acceptance', encoding='utf-8')
        with workflow.WorkflowDB(self.db.path) as reopened:
            pending = engine.advance(reopened, run['id'])
        self.assertEqual(pending['status'], 'blocked', pending['error'])
        self.assertIn('n1', pending['settings']['dag_errors'])
        self.assertIn('依赖产物已变化', pending['settings']['dag_errors']['n1'])
        self.assertNotEqual(tasks.get(self.db, ids['n1'])['status'], 'delivered')
        self.assertFalse(tasks.get(self.db, ids['n3'])['latest_run_id'])


if __name__ == '__main__':
    unittest.main()
