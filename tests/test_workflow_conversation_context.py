"""Conversation ownership uses explicit origins and canonical execution bindings."""
import json
import unittest
from unittest.mock import patch

from tests import test_project_task_service as service
import agent_settings
import execution_conversations
import project_tasks as tasks
import run_service
import workflow_conversation_context as conversations
import workflow_execution
import workflow_layout


class WorkflowFixture:
    setUp = service.ProjectTaskServiceTest.setUp
    create = service.ProjectTaskServiceTest.create
    result = service.ProjectTaskServiceTest.result

    def make_workflow(self, settings=None):
        workflow_layout.ensure_schema(self.db)
        run = run_service.start(self.db, self.pid, 'research-sequence')
        layout = {'name': 'Explicit workflow', 'nodes': [], 'edges': [], 'order': []}
        with self.db.db:
            self.db.db.execute('INSERT INTO workflow_executions VALUES (?,?,?,?)',
                (run['id'], json.dumps(layout), json.dumps(settings or {}), tasks._now()))
        chat = execution_conversations.ensure(self.db, 'workflow', run['id'])
        return {**run, 'chat_id': chat}

    def node_task(self, run, node_id):
        return tasks.create(self.db, {'project_id': self.pid, 'title': node_id, 'goal': 'Inspect evidence',
            'executor_binding_id': self.binding['id'], 'workflow_run_id': run['id'], 'workflow_node_id': node_id},
            authority=tasks.CommandContext.system(self.pid))


class WorkflowConversationContextTest(WorkflowFixture, unittest.TestCase):
    def test_empty_and_unrelated_chat_never_select_latest_project_workflow(self):
        self.make_workflow()
        chat = self.db.create_chat('Unrelated', task_id=self.pid)
        for chat_id in ('', chat):
            self.assertEqual(conversations.context(self.db, self.pid, chat_id),
                {'project_id': self.pid, 'chat_id': chat_id, 'runs': [], 'run_ids': [], 'task_ids': []})
        with self.assertRaises(ValueError):
            conversations.context(self.db, self.other, chat)
        with self.assertRaises(ValueError):
            conversations.context(self.db, self.pid, 'missing-chat')

    def test_all_explicit_origins_and_execution_kinds_include_nodes_and_descendants(self):
        chat = self.db.create_chat('Origin', task_id=self.pid)
        origins = [{'agent_invocation': {'chat_id': chat}}, {'research_setup_chat_id': chat}, {'origin_chat_id': chat}]
        run_ids, task_ids = set(), set()
        for settings in origins:
            run = self.make_workflow(settings)
            first = self.node_task(run, 'first')
            second = self.node_task(run, 'second')
            child = tasks.create(self.db, {'project_id': self.pid, 'goal': 'Child evidence',
                'executor_binding_id': self.binding['id']}, authority=tasks.CommandContext.agent(self.pid, first['id']))
            with self.db.db:
                self.db.db.execute("UPDATE project_tasks SET workflow_run_id='' WHERE id=?", (child['id'],))
            expected = {first['id'], second['id'], child['id']}
            for execution_chat in (run['chat_id'], first['chat_id'], child['chat_id']):
                # Legacy children without workflow ownership retain their own subtree.
                if execution_chat == child['chat_id']:
                    continue
                context = conversations.context(self.db, self.pid, execution_chat)
                self.assertEqual(context['run_ids'], [run['id']])
                self.assertEqual(set(context['task_ids']), expected)
            run_ids.add(run['id'])
            task_ids.update(expected)
        context = agent_settings.route('GET', '/api/agent-settings/workflow-context', {},
            {'project_id': [self.pid], 'chat_id': [chat]}, self.db)
        self.assertEqual(set(context['run_ids']), run_ids)
        self.assertEqual(set(context['task_ids']), task_ids)
        self.assertTrue(all(item['layout_name'] == 'Explicit workflow' for item in context['runs']))
        task_id = next(iter(task_ids))
        self.assertEqual(conversations.require_task(self.db, self.pid, chat, task_id)['id'], task_id)
        unrelated = self.db.create_chat('Unrelated', task_id=self.pid)
        with self.assertRaises(ValueError):
            conversations.require_task(self.db, self.pid, unrelated, task_id)

    def test_standalone_task_context_has_only_its_subtree(self):
        parent = self.create()
        child = tasks.create(self.db, {'project_id': self.pid, 'goal': 'Subtask',
            'executor_binding_id': self.binding['id']}, authority=tasks.CommandContext.agent(self.pid, parent['id']))
        self.create()
        self.assertEqual(conversations.context(self.db, self.pid, parent['chat_id'])['run_ids'], [])
        self.assertEqual(set(conversations.context(self.db, self.pid, parent['chat_id'])['task_ids']),
                         {parent['id'], child['id']})

    def test_execution_chats_union_bound_workflow_with_all_explicit_origins(self):
        bound = self.make_workflow()
        first, sibling = self.node_task(bound, 'first'), self.node_task(bound, 'sibling')
        for chat_id in (bound['chat_id'], first['chat_id']):
            with self.subTest(chat_id=chat_id):
                expected_runs = {bound['id']}
                expected_tasks = {first['id'], sibling['id']}
                for origin in ({'agent_invocation': {'chat_id': chat_id}},
                               {'research_setup_chat_id': chat_id}, {'origin_chat_id': chat_id}):
                    launched = self.make_workflow(origin)
                    node = self.node_task(launched, 'launched')
                    expected_runs.add(launched['id'])
                    expected_tasks.add(node['id'])
                self.make_workflow({'origin_chat_id': self.db.create_chat('Unrelated', task_id=self.pid)})
                result = conversations.context(self.db, self.pid, chat_id)
                self.assertEqual(set(result['run_ids']), expected_runs)
                self.assertEqual(len(result['run_ids']), len(expected_runs))
                self.assertEqual(set(result['task_ids']), expected_tasks)

    def test_prepare_start_validate_and_persist_origin_without_requiring_legacy_callers_to_supply_it(self):
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {'name': 'Prepared', 'nodes': [
            {'id': 'first', 'step': 'task', 'label': 'First', 'executor_binding_id': self.binding['id']}], 'edges': []})['layout']
        chat = self.db.create_chat('Origin', task_id=self.pid)
        foreign = self.db.create_chat('Foreign', task_id=self.other)
        body = {'project_id': self.pid, 'template_id': layout['id'], 'chat_id': foreign}
        before = self.db.db.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0]
        with self.assertRaises(ValueError):
            workflow_execution.route('POST', '/api/workflow/execution/prepare', body, {}, self.db)
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], before)
        body['chat_id'] = chat
        prepared = workflow_execution.route('POST', '/api/workflow/execution/prepare', body, {}, self.db)['run']
        self.assertEqual(prepared['settings']['origin_chat_id'], chat)
        with patch.object(workflow_execution, 'kick') as kick:
            started = workflow_execution.route('POST', '/api/workflow/execution/start',
                {'id': prepared['id'], 'project_id': self.pid, 'chat_id': chat}, {}, self.db)['run']
            kick.assert_called_once()
        self.assertFalse(started['settings']['prepared'])
        self.assertEqual(started['settings']['origin_chat_id'], chat)
        legacy = workflow_execution.route('POST', '/api/workflow/execution/prepare',
            {'id': prepared['id'], 'project_id': self.pid}, {}, self.db)['run']
        self.assertEqual(legacy['id'], prepared['id'])


if __name__ == '__main__':
    unittest.main()
