"""Durable tool boundaries, addressable long context and human reminders."""
import json
import unittest
from unittest.mock import patch

from tests import test_workflow_execution as fixtures
import workflow
import workflow_context
import workflow_execution as engine
import review_inbox
import calendar_service
import project_tasks


class WorkflowContinuityTest(unittest.TestCase):
    default_executor = 'builtin-steward'
    setUp = fixtures.WorkflowExecutionTest.setUp
    layout = fixtures.WorkflowExecutionTest.layout
    execute = fixtures.WorkflowExecutionTest.execute

    def test_model_failure_resumes_after_successful_tool_without_repeating_write(self):
        self.layout([{'step': 'outline'}])
        run = engine.create(self.db, self.pid, 'Durable tool boundary')
        call = {'id': 'write-once', 'type': 'function', 'function': {'name': 'doc_write',
            'arguments': json.dumps({'markdown': '# Saved once\nFull artifact'})}}
        with patch.object(workflow, '_chat_raw', side_effect=[{'tool_calls': [call]}, RuntimeError('temporary model outage')]), \
                patch.object(workflow, '_tool_impl', wraps=workflow._tool_impl) as tool:
            failed = engine.advance(self.db, run['id'])
            self.assertEqual(failed['status'], 'failed', failed)
            self.assertEqual(tool.call_count, 1)
        saved = failed['nodes'][-1]['checkpoint']['agent']
        self.assertEqual(saved['phase'], 'model')
        self.assertTrue(saved['done_tools'])
        with patch.object(workflow, '_chat_raw', return_value={'content': 'Used saved artifact'}) as model, \
                patch.object(workflow, '_tool_impl', wraps=workflow._tool_impl) as tool:
            run = engine.advance(self.db, run['id'])
            self.assertEqual(run['status'], 'awaiting_review', run)
            tool.assert_not_called()
            self.assertTrue(any(m.get('role') == 'tool' for m in model.call_args.args[1]))
        self.assertEqual(run['nodes'][-1]['checkpoint']['resumed_from'], failed['current_node'])

    def test_unknown_tool_boundary_requires_explicit_restart(self):
        self.layout([{'step': 'outline'}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', side_effect=RuntimeError('Stopped')):
            run = engine.advance(self.db, run['id'])
        cp = run['nodes'][-1]['checkpoint']
        cp['agent'] = {'phase': 'tool', 'inflight': {'name': 'write'}}
        engine._checkpoint(self.db, run['current_node'], cp)
        with patch.object(engine, 'kick') as kick:
            with self.assertRaisesRegex(ValueError, '工具结果未确认'):
                engine.route('POST', '/api/workflow/execution/resume', {'id': run['id']}, {}, self.db)
            kick.assert_not_called()
            engine.route('POST', '/api/workflow/execution/resume', {'id': run['id'], 'restart_node': True}, {}, self.db)
            self.assertTrue(engine.detail(self.db, run['id'])['nodes'][-1]['checkpoint']['restart_approved'])

    def test_crash_between_tools_resumes_pending_calls_before_next_model_request(self):
        self.layout([{'step':'outline'}])
        run=engine.create(self.db,self.pid)
        calls=[{'id':str(i),'type':'function','function':{'name':'doc_write','arguments':json.dumps({'markdown':'Artifact '+str(i)})}} for i in (1,2)]
        original=project_tasks.save_checkpoint
        class Crash(BaseException):
            pass
        def checkpoint(db,task_run_id,**value):
            original(db,task_run_id,**value)
            if value.get('phase')=='tools_ready':
                raise Crash()
        with patch.object(workflow,'_chat_raw',return_value={'tool_calls':calls}), patch.object(project_tasks,'save_checkpoint',side_effect=checkpoint), \
                patch.object(workflow,'_tool_impl',wraps=workflow._tool_impl) as tool:
            with self.assertRaises(Crash):
                engine.advance(self.db,run['id'])
            self.assertEqual(tool.call_count,1)
        engine.recover(self.db.path)
        with patch.object(workflow,'_chat_raw',return_value={'content':'Both saved'}) as model, \
                patch.object(workflow,'_tool_impl',wraps=workflow._tool_impl) as tool:
            result=engine.advance(self.db,run['id'])
            self.assertEqual(result['status'],'awaiting_review',result)
            self.assertEqual(tool.call_count,1)
            self.assertEqual(tool.call_args.args[3]['markdown'],'Artifact 2')
            self.assertEqual(len([m for m in model.call_args.args[1] if m['role']=='tool']),2)

    def test_long_history_is_bounded_but_complete_evidence_remains_pageable(self):
        self.layout([{'step': 'outline'}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', side_effect=self.execute):
            run = engine.advance(self.db, run['id'])
        cp = run['nodes'][-1]['checkpoint']
        cp['input']['history'] = [{'reply': 'A' * 100000 + 'UNIQUE_TAIL_EVIDENCE'}]
        engine._checkpoint(self.db, run['current_node'], cp)
        compact = workflow_context.prompt(cp['input'], run['id'], run['current_node'])
        self.assertLess(len(compact), 24000)
        self.assertIn('workflow_context_read', compact)
        page = workflow_context.read(self.db, self.pid, {'run_id': run['id'], 'section': 'history', 'offset': 99950})
        self.assertIn('UNIQUE_TAIL_EVIDENCE', page['content'])
        with self.assertRaisesRegex(ValueError, '当前项目'):
            workflow_context.read(self.db, 'other-project', {'run_id': run['id']})
        messages = [{'role': 'system', 'content': 'A'*100000}, {'role': 'assistant', 'tool_calls': [
            {'id': 'x', 'function': {'name': 'read', 'arguments': json.dumps({'body': 'B'*90000})}}]},
            {'role': 'tool', 'tool_call_id': 'x', 'content': 'C'*100000}]
        bounded = workflow_context.bounded_messages(messages)
        self.assertLess(len(json.dumps(bounded)), 60000)
        self.assertEqual(bounded[2]['tool_calls'][0]['id'], bounded[3]['tool_call_id'])
        self.assertEqual(len(messages[0]['content']), 100000)

    def test_reminders_never_approve_and_calendar_preserves_received_time(self):
        self.layout([{'step': 'outline'}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', side_effect=self.execute):
            run = engine.advance(self.db, run['id'])
        item = review_inbox.pending(self.db)[0]
        self.assertTrue(item['notify'])
        review_inbox.update(self.db, item['node_id'], {'snooze_minutes': 60, 'due_at': '2030-01-02T10:00:00+08:00'})
        updated = review_inbox.pending(self.db)[0]
        self.assertFalse(updated['notify'])
        self.assertEqual(updated['received_at'], item['received_at'])
        self.assertEqual(engine.detail(self.db, run['id'])['status'], 'awaiting_review')
        event = next(e for e in calendar_service.derived(self.db) if e.get('node_id') == item['node_id'])
        self.assertEqual(event['date'], '2030-01-02')
        self.assertTrue(event['received_at'])
        engine.review(self.db, run['id'], item['node_id'], True)
        self.assertEqual(review_inbox.pending(self.db), [])


if __name__ == '__main__':
    unittest.main()
