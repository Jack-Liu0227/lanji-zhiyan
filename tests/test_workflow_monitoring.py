"""Isolated task attempts, discussion runs and worker liveness without real models."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_workflow_conversation_context import WorkflowFixture
import execution_conversations
import project_task_execution as execution
import project_tasks as tasks
import workflow
import workspace_trajectory as trace


class WorkflowMonitoringTest(WorkflowFixture, unittest.TestCase):
    def test_trajectory_rejects_other_attempts_including_same_project_sibling_nodes(self):
        run = self.make_workflow()
        first, second = self.node_task(run, 'first'), self.node_task(run, 'second')
        one, two = (tasks.start(self.db, task['id'], authority=self.ui) for task in (first, second))
        unrelated = self.create()
        three = tasks.start(self.db, unrelated['id'], authority=self.ui)
        for chat, attempt in ((first['chat_id'], two), (run['chat_id'], three), (unrelated['chat_id'], one)):
            with self.assertRaises(ValueError):
                trace.events(self.db, chat, self.pid, attempt['id'])
        self.assertEqual(trace.events(self.db, run['chat_id'], self.pid, two['id'])['events'], [])
        self.assertEqual(trace.events(self.db, first['chat_id'], self.pid, one['id'])['status'], 'queued')
        with self.assertRaises(ValueError):
            trace.events(self.db, first['chat_id'], self.other, one['id'])
        with self.assertRaises(ValueError):
            trace.events(self.db, first['chat_id'], self.pid, 'missing-run')

    def test_execution_chat_keeps_discussion_run_events_alongside_task_attempts(self):
        task = self.create()
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        discussion = self.db.create_async_task('agent_run', task['chat_id'], checkpoint={'request': {'message': 'Discuss'}})
        self.db.record_run_event(discussion['id'], 1, 'reasoning_done', {'type': 'reasoning_done', 'content': 'Discuss evidence'})
        self.db.record_run_event(discussion['id'], 2, 'tool_result', {'type': 'tool_result', 'result': 'Useful detail'})
        listing = trace.listing(self.db, task['chat_id'], self.pid, limit=1)
        remaining = trace.listing(self.db, task['chat_id'], self.pid, before=listing['next_before'], limit=1)
        self.assertEqual({item['id'] for item in listing['runs'] + remaining['runs']}, {attempt['id'], discussion['id']})
        events = trace.events(self.db, task['chat_id'], self.pid, discussion['id'], limit=1)
        self.assertEqual(events['events'][0]['payload']['text'], 'Discuss evidence')
        self.assertTrue(events['has_more'])
        self.assertEqual(trace.events(self.db, task['chat_id'], self.pid, discussion['id'],
            after=events['next_after'])['events'][0]['payload']['result'], 'Useful detail')
        foreign = self.db.create_async_task('agent_run', 'another-chat')
        with self.assertRaises(ValueError):
            trace.events(self.db, task['chat_id'], self.pid, foreign['id'])

    def test_node_trajectory_adds_explicit_workflows_without_exposing_original_siblings(self):
        bound = self.make_workflow()
        first, sibling = self.node_task(bound, 'first'), self.node_task(bound, 'sibling')
        child = tasks.create(self.db, {'project_id': self.pid, 'goal': 'Own child',
            'executor_binding_id': self.binding['id']}, authority=tasks.CommandContext.agent(self.pid, first['id']))
        visible = [first, child]
        for origin in ({'agent_invocation': {'chat_id': first['chat_id']}},
                       {'research_setup_chat_id': first['chat_id']}, {'origin_chat_id': first['chat_id']}):
            launched = self.make_workflow(origin)
            node = self.node_task(launched, 'launched')
            descendant = tasks.create(self.db, {'project_id': self.pid, 'goal': 'Legacy descendant',
                'executor_binding_id': self.binding['id']}, authority=tasks.CommandContext.agent(self.pid, node['id']))
            with self.db.db:
                self.db.db.execute("UPDATE project_tasks SET workflow_run_id='' WHERE id=?", (descendant['id'],))
            visible.extend([node, descendant])
        other = self.node_task(self.make_workflow({'origin_chat_id': sibling['chat_id']}), 'other')
        attempts = [tasks.start(self.db, task['id'], authority=self.ui) for task in visible]
        denied = [tasks.start(self.db, task['id'], authority=self.ui) for task in (sibling, other)]
        execution_conversations.record(self.db, visible[2], attempts[2],
            {'type': 'reasoning_done', 'content': 'Explicitly launched workflow evidence'})
        self.assertEqual({item['id'] for item in trace.listing(self.db, first['chat_id'], self.pid)['runs']},
                         {attempt['id'] for attempt in attempts})
        for attempt in attempts:
            self.assertEqual(trace.events(self.db, first['chat_id'], self.pid, attempt['id'])['status'], 'queued')
        self.assertEqual(trace.events(self.db, first['chat_id'], self.pid, attempts[2]['id'])['events'][0]
                         ['payload']['content'], 'Explicitly launched workflow evidence')
        for attempt in denied:
            with self.assertRaises(ValueError):
                trace.events(self.db, first['chat_id'], self.pid, attempt['id'])

    def test_latest_discussion_precedes_six_old_attempts_and_pages_survive_newer_inserts(self):
        task = self.create()
        attempts = []
        for index in range(6):
            with patch.object(tasks, '_now', return_value=f'2026-11-23T00:00:0{index + 1}+00:00'):
                attempt = tasks.start(self.db, task['id'], authority=self.ui, restart=True)
                tasks.claim(self.db, attempt['id'])
                tasks.fail(self.db, attempt['id'], 'Old failed attempt')
            attempts.append(attempt['id'])
        discussion = self.db.create_async_task('agent_run', task['chat_id'])
        with self.db.db:
            self.db.db.execute('UPDATE tasks SET created_at=? WHERE id=?',
                               ('2026-11-23T00:00:10+00:00', discussion['id']))
        first = trace.listing(self.db, task['chat_id'], self.pid, limit=1)
        self.assertEqual([item['id'] for item in first['runs']], [discussion['id']])
        before = first['next_before']
        self.assertIsInstance(before, int)
        previous = {item['id']: item['sequence'] for item in trace.listing(self.db, task['chat_id'], self.pid)['runs']}
        newer = self.db.create_async_task('agent_run', task['chat_id'])
        with self.db.db:
            self.db.db.execute('UPDATE tasks SET created_at=? WHERE id=?',
                               ('2026-11-23T00:00:11+00:00', newer['id']))
        refreshed = trace.listing(self.db, task['chat_id'], self.pid)['runs']
        self.assertEqual(refreshed[0]['id'], newer['id'])
        self.assertEqual({item['id']: item['sequence'] for item in refreshed if item['id'] != newer['id']}, previous)
        remaining = []
        for _ in attempts:
            page = trace.listing(self.db, task['chat_id'], self.pid, before=before, limit=1)
            self.assertEqual(len(page['runs']), 1)
            remaining.append(page['runs'][0]['id'])
            before = page['next_before']
        self.assertIsNone(before)
        self.assertEqual(remaining, list(reversed(attempts)))

    def test_mixed_trajectory_timestamp_ties_page_deterministically(self):
        task = self.create()
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        for run_id in ('discussion-z', 'discussion-a'):
            self.db.create_async_task('agent_run', task['chat_id'], task_id=run_id)
        stamp = '2026-11-23T00:00:01+00:00'
        with self.db.db:
            self.db.db.execute('UPDATE project_task_runs SET started_at=? WHERE id=?', (stamp, attempt['id']))
            self.db.db.execute('UPDATE tasks SET created_at=? WHERE owner_id=?', (stamp, task['chat_id']))
        observed, before = [], 0
        for _ in range(3):
            page = trace.listing(self.db, task['chat_id'], self.pid, before=before, limit=1)
            self.assertEqual(len(page['runs']), 1)
            observed.append(page['runs'][0]['id'])
            before = page['next_before']
        self.assertIsNone(before)
        self.assertEqual(observed, [attempt['id'], 'discussion-z', 'discussion-a'])

    def test_origin_chat_includes_only_linked_workflows_and_its_own_independent_runs(self):
        chat = self.db.create_chat('Origin', task_id=self.pid)
        linked = self.make_workflow({'origin_chat_id': chat})
        task = self.node_task(linked, 'linked')
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        own = self.db.create_async_task('agent_run', chat)
        other = self.create()
        foreign = tasks.start(self.db, other['id'], authority=self.ui)
        self.assertEqual({r['id'] for r in trace.listing(self.db, chat, self.pid)['runs']}, {attempt['id'], own['id']})
        self.assertEqual(trace.events(self.db, chat, self.pid, attempt['id'])['status'], 'queued')
        with self.assertRaises(ValueError):
            trace.events(self.db, chat, self.pid, foreign['id'])

    def test_execution_events_normalize_reasoning_keep_identity_and_hide_requests(self):
        flow = self.make_workflow()
        task = self.node_task(flow, 'inspect')
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        tasks.save_checkpoint(self.db, attempt['id'], workflow_run_id='agent-run', node_id='agent-node')
        execution_conversations.record(self.db, task, attempt, {'type': 'model_request', 'id': 'request-1',
            'model': 'fixture', 'messages': [{'content': 'PRIVATE_PROMPT'}], 'body': 'PRIVATE_BODY',
            'request': {'authorization': 'PRIVATE_AUTH'}})
        execution_conversations.record(self.db, task, attempt, {'type': 'reasoning_done', 'id': 'thought', 'content': 'Inspect sources'})
        execution_conversations.record(self.db, task, attempt, {'type': 'tool_result', 'id': 'read',
            'result': {'text': 'Evidence', 'api_key': 'PRIVATE_KEY'}})
        response = trace.events(self.db, flow['chat_id'], self.pid, attempt['id'], limit=2)
        payload = response['events'][1]['payload']
        self.assertEqual(payload['text'], 'Inspect sources')
        self.assertEqual(payload['content'], payload['text'])
        self.assertEqual((payload['workflow_run_id'], payload['node_id'], payload['task_id'],
            payload['task_run_id'], payload['attempt'], payload['agent_run_id'], payload['agent_node_id']),
            (flow['id'], 'inspect', task['id'], attempt['id'], 1, 'agent-run', 'agent-node'))
        tail = trace.events(self.db, flow['chat_id'], self.pid, attempt['id'], after=response['next_after'])
        self.assertFalse(tail['has_more'])
        messages = execution_conversations.read(self.db, task['chat_id'], self.pid)['messages']
        self.assertEqual(messages[1]['reasoning'], 'Inspect sources')
        self.assertNotIn('PRIVATE_', json.dumps([response, tail, messages]))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail('Timed out waiting for local worker')

    def test_blocked_worker_heartbeats_without_fabricating_output_progress_and_stops_on_completion(self):
        self.assertEqual(execution.HEARTBEAT_INTERVAL, 5.0)
        task = self.create()
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        entered, release = threading.Event(), threading.Event()
        errors = []
        def model(db, *args, **kwargs):
            db.save_agent_checkpoint(phase='model', messages=[{'content': 'PRIVATE_CHECKPOINT'}], workflow_run_id='agent-local')
            kwargs['emit']({'type': 'model_request', 'model': 'fixture', 'request_body': 'PRIVATE_BODY'})
            entered.set()
            if not release.wait(4):
                raise RuntimeError('Local fixture was not released')
            kwargs['emit']({'type': 'reasoning_done', 'content': 'Completed locally'})
            return {'reply': 'Local result', 'run_id': 'agent-local'}
        def worker():
            try:
                with workflow.WorkflowDB(self.db.path) as db:
                    execution.execute(db, attempt['id'])
            except BaseException as exc:
                errors.append(exc)
        with patch.object(workflow, 'run_agent', side_effect=model), patch.object(execution, 'HEARTBEAT_INTERVAL', .03):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(4))
                initial = self.wait_for(lambda: (value if (value := trace.events(self.db, task['chat_id'], self.pid,
                    attempt['id'])['monitoring'])['phase'] == 'model' else None))
                later = self.wait_for(lambda: (value if (value := trace.events(self.db, task['chat_id'], self.pid,
                    attempt['id'])['monitoring'])['heartbeat_at'] != initial['heartbeat_at'] else None))
                self.assertTrue(later['worker_alive'])
                self.assertEqual(later['last_progress_at'], initial['last_progress_at'])
                self.assertNotIn('PRIVATE_', json.dumps(trace.events(self.db, task['chat_id'], self.pid, attempt['id'])))
                self.assertEqual(tasks.get_run(self.db, attempt['id'])['checkpoint']['messages'][0]['content'], 'PRIVATE_CHECKPOINT')
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        terminal = trace.events(self.db, task['chat_id'], self.pid, attempt['id'])
        self.assertEqual(terminal['status'], 'succeeded')
        self.assertFalse(terminal['monitoring']['worker_alive'])
        saved = tasks.get_run(self.db, attempt['id'])['checkpoint']
        monitor = execution._RunMonitor(self.db.path, attempt['id'])
        self.assertFalse(monitor._tick())
        self.assertEqual(tasks.get_run(self.db, attempt['id'])['checkpoint'], saved)

    def test_cancelled_or_stale_worker_is_not_alive_and_late_events_cannot_change_terminal_result(self):
        task = self.create()
        attempt = tasks.start(self.db, task['id'], authority=self.ui)
        tasks.claim(self.db, attempt['id'])
        monitor = execution._RunMonitor(self.db.path, attempt['id'])
        self.assertTrue(monitor._tick())
        self.assertTrue(trace.events(self.db, task['chat_id'], self.pid, attempt['id'])['monitoring']['worker_alive'])
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        with self.db.db:
            self.db.db.execute("UPDATE project_task_runs SET checkpoint=json_set(checkpoint,'$.monitoring.heartbeat_at',?) WHERE id=?", (old, attempt['id']))
        self.assertFalse(trace.events(self.db, task['chat_id'], self.pid, attempt['id'])['monitoring']['worker_alive'])
        tasks.cancel(self.db, task['id'], authority=self.ui)
        self.assertFalse(monitor._tick())
        execution_conversations.record(self.db, task, attempt, {'type': 'reasoning_done', 'content': 'Late output'})
        tasks.complete(self.db, attempt['id'], self.result(attempt, 'Late result'))
        response = trace.events(self.db, task['chat_id'], self.pid, attempt['id'])
        self.assertEqual(response['status'], 'cancelled')
        self.assertEqual(response['events'], [])
        self.assertFalse(response['monitoring']['worker_alive'])


if __name__ == '__main__':
    unittest.main()
