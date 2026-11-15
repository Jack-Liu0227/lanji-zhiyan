"""Actual saved output visibility, interruption recovery, and context identity."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_task_orchestration as orchestration
import asset_service
import project_tasks as tasks
import project_task_execution as worker
import workflow_execution as engine
import workflow_node_workbench as nodes
import workflow_tasks
import workflow_context
import workflow
import project_task_routes


class WorkflowArtifactWorkspaceTest(unittest.TestCase):
    setUp = orchestration.TaskOrchestrationTest.setUp
    layout = orchestration.TaskOrchestrationTest.layout

    def running(self):
        self.layout(['search', 'task'])
        flow = engine.create(self.db, self.pid)
        task = tasks.get(self.db, flow['settings']['task_ids']['n0'])
        run = tasks.start(self.db, task['id'], authority=tasks.CommandContext.ui(self.pid))
        tasks.claim(self.db, run['id'])
        workflow_tasks._record_attempt(self.db, flow, 'n0', task, run, {})
        return flow, tasks.get(self.db, task['id']), run

    def saved(self, task, run):
        root = tasks._root(self.db, self.pid)
        path = root / 'saved-report.md'
        path.write_text('# Actually saved evidence', encoding='utf-8')
        asset = asset_service.register_file(self.db, path, project_id=self.pid,
            metadata={'project_task_id': task['id'], 'task_run_id': run['id']})
        rid = self.db.cache_result(self.pid, {'title': 'Owned search result', 'source': 'manual'})
        self.db.cache_result(self.pid, {'title': 'Unrelated existing paper', 'source': 'manual'})
        row = next(r for r in self.db.results(self.pid) if r['id'] == rid)
        tasks.save_checkpoint(self.db, run['id'], phase='model', output_assets=[asset['id']],
            done_tools={'paper_search\0{}': {'saved': [row]}},
            input={'goal': 'Complete the research objective'}, messages=[{'role': 'user', 'content': 'full context ' * 10000}])
        return nodes.Selection(self.pid, task['id']), asset

    def test_running_and_failed_node_show_only_their_saved_results(self):
        _, task, run = self.running()
        selection, asset = self.saved(task, run)
        ui = tasks.CommandContext.ui(self.pid)
        for status in ('running', 'failed'):
            if status == 'failed':
                tasks.fail(self.db, run['id'], 'Provider disconnected')
            value = nodes.workspace(self.db, selection, authority=ui)
            self.assertTrue(value['provisional'])
            self.assertFalse(value['editable'])
            self.assertEqual([row['title'] for row in value['content']], ['Owned search result'])
            preview = nodes.workspace_preview(self.db, selection, 'asset:' + asset['id'], authority=ui)
            self.assertIn('Actually saved evidence', preview['text'])
            self.assertEqual(len(value['steps']), 2)
        with self.assertRaises(ValueError):
            nodes.workspace_preview(self.db, selection, 'asset:foreign', authority=ui)

    def test_interrupted_saved_output_can_be_fixed_edited_and_passed_forward(self):
        flow, task, run = self.running()
        selection, _ = self.saved(task, run)
        tasks.interrupt(self.db, run['id'])
        task = tasks.get(self.db, task['id'])
        ui = tasks.CommandContext.ui(self.pid)
        body = {'request_key': 'collect-1', 'expected_version': task['version'], 'task_run_id': run['id']}
        value = nodes.collect(self.db, task['id'], body, authority=ui)
        self.assertEqual(value['status'], 'awaiting_review')
        self.assertEqual(nodes.collect(self.db, task['id'], body, authority=ui)['latest_run_id'], value['latest_run_id'])
        result = nodes.read(self.db, selection, authority=ui)
        content = result['content']
        content[0]['reason'] = 'HUMAN_CHECKED_EVIDENCE'
        result = nodes.command(self.db, task['id'], {'action': 'revise-structured', 'identity': result['identity'],
            'request_key': 'revise-1', 'content': content}, authority=ui)['result']
        with patch.object(engine, 'kick'):
            nodes.command(self.db, task['id'], {'action': 'review', 'identity': result['identity'],
                'approved': True, 'request_key': 'review-1'}, authority=ui)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Final downstream report'}) as model:
            engine.advance(self.db, flow['id'])
        self.assertIn('HUMAN_CHECKED_EVIDENCE', model.call_args.kwargs['extra_context'])
        self.assertEqual(tasks.get_run(self.db, run['id'])['status'], 'interrupted')

    def test_task_and_attempt_ids_resolve_the_current_context(self):
        flow, task, run = self.running()
        self.saved(task, run)
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, task['id'])
        for run_id in (flow['id'], task['id'], run['id']):
            page = workflow_context.read(self.db, self.pid, {'run_id': run_id, 'node_id': 'search', 'section': 'input'})
            self.assertIn('Complete the research objective', page['content'])
            self.assertEqual(page['run_id'], flow['id'])
        with self.assertRaises(ValueError):
            workflow_context.read(self.db, 'other-project', {'run_id': task['id'], 'section': 'input'})

    def test_polling_omits_full_transcript_but_preserves_durable_checkpoint(self):
        _, task, run = self.running()
        self.saved(task, run)
        data = project_task_routes.route('GET', '/api/project-tasks/detail', {},
            {'project_id': [self.pid], 'id': [task['id']]}, self.db)
        self.assertLess(len(json.dumps(data)), 40000)
        self.assertNotIn('messages', data['task']['latest_run']['checkpoint'])
        self.assertGreater(len(tasks.get_run(self.db, run['id'])['checkpoint']['messages'][0]['content']), 100000)

    def test_compaction_keeps_goal_and_real_context_identity(self):
        messages = [{'role': 'system', 'content': 'Research instructions'}, {'role': 'user', 'content': 'ORIGINAL_RESEARCH_GOAL'}]
        for i in range(15):
            messages.extend([{'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'paper_read', 'arguments': '{}'}}]},
                             {'role': 'tool', 'tool_call_id': str(i), 'content': 'Long paper text ' * 1000}])
        compact = workflow_context.bounded_messages(messages, 8000, run_id='real-flow', node_id='real-node')
        text = json.dumps(compact)
        self.assertIn('ORIGINAL_RESEARCH_GOAL', text)
        self.assertIn('real-flow', text)
        calls = [call['id'] for m in compact for call in m.get('tool_calls', [])]
        self.assertEqual(calls, [m['tool_call_id'] for m in compact if m['role'] == 'tool'])

    def test_compaction_preserves_current_node_and_accepted_human_output(self):
        messages = [
            {'role': 'system', 'content': 'Instructions'},
            {'role': 'system', 'content': 'CURRENT_NODE_INPUT ' + 'filler ' * 8000},
            {'role': 'user', 'content': 'Continue from accepted evidence'},
        ]
        messages[1]['content'] += '\nACCEPTED_HUMAN_REVISION'
        compact = workflow_context.bounded_messages(messages, 8000, run_id='flow', node_id='node')
        self.assertIn('ACCEPTED_HUMAN_REVISION', json.dumps(compact))
        prompt = workflow_context.prompt({'goal': 'Goal', 'task_inputs': {'dependencies': []},
            'accepted_upstream': [{'outputs': [{'content': 'ACCEPTED_HUMAN_OUTPUT'}]}]}, 'flow', 'node')
        self.assertLess(prompt.index('ACCEPTED_HUMAN_OUTPUT'), prompt.index('task_inputs'))

    def test_missing_typed_output_cannot_succeed_using_model_narration(self):
        self.layout(['search'])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, 'run_agent', return_value={'reply': 'I completed the literature search'}):
            result = engine.advance(self.db, run['id'])
        task = tasks.get(self.db, result['settings']['task_ids']['n0'])
        self.assertEqual(task['status'], 'failed')
        self.assertFalse(task['outputs'])
