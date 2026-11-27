"""Execution conversations keep identity, authors and board visibility across retries."""
import json
import unittest

from tests import test_project_task_service as service
import project_tasks as tasks
import execution_board
import execution_conversations as conversations
import conversation_history
import workflow
from run_manager import RunManager


class ExecutionConversationsTest(unittest.TestCase):
    setUp=service.ProjectTaskServiceTest.setUp
    create=service.ProjectTaskServiceTest.create
    result=service.ProjectTaskServiceTest.result

    def test_same_question_after_a_reply_is_a_separate_turn(self):
        messages = [
            {'id':'first','role':'user','content':'Check this result'},
            {'id':'legacy-duplicate','role':'user','content':'Check this result'},
            {'id':'answer','role':'assistant','content':'The evidence is incomplete'},
            {'id':'followup','role':'user','content':'Check this result'},
        ]
        self.assertEqual([row['id'] for row in conversations.visible_messages(messages)],
                         ['first','answer','followup'])

    def test_desktop_http_route_opens_the_bound_execution_conversation(self):
        from pathlib import Path
        import sys
        import threading
        import urllib.request
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/desktop'))
        import server
        task = self.create()
        with patch.object(server, 'DB_PATH', self.db.path):
            http = server.Server(('127.0.0.1', 0), server.Handler)
            worker = threading.Thread(target=http.serve_forever, daemon=True)
            worker.start()
            try:
                path = '/api/execution-conversations?chat_id=' + task['chat_id'] + '&project_id=' + self.pid
                with urllib.request.urlopen(f'http://127.0.0.1:{http.server_port}' + path) as response:
                    result = json.load(response)
                self.assertEqual(result['execution']['entity_id'], task['id'])
                self.assertEqual(result['execution']['status'], 'ready')
            finally:
                http.shutdown()
                worker.join(3)
                http.server_close()

    def test_task_has_one_conversation_and_retry_keeps_expert_snapshot(self):
        task=self.create();cid=task['chat_id']
        self.assertEqual(self.db.chat(cid)['task_id'],self.pid)
        self.assertEqual(conversations.ensure(self.db,'task',task['id']),cid)
        first=tasks.start(self.db,task['id'],authority=self.ui)
        event={'type':'tool_call','id':'call-1','name':'read_file','args':{'api_key':'NEVER_DISPLAY','path':'report.md'}}
        conversations.record(self.db,task,first,event);conversations.record(self.db,task,first,event)
        conversations.record(self.db,task,first,{'type':'tool_result','id':'call-1','name':'read_file','ok':True,'result':{'text':'evidence'}})
        tasks.complete(self.db,first['id'],self.result(first,'FIRST_RESULT'))
        tasks.review(self.db,task['id'],approved=False,feedback='Improve evidence',authority=self.ui)
        self.db.assistant_save('task-writer',name='Changed expert',enabled=1)
        second=tasks.start(self.db,task['id'],authority=self.ui)
        tasks.complete(self.db,second['id'],self.result(second,'SECOND_RESULT'))
        data=conversations.read(self.db,cid,self.pid)
        self.assertEqual([row['sender'] for row in data['messages'][1:]],['Writer','Changed expert'])
        self.assertEqual(len(data['messages'][1]['events']),2)
        self.assertEqual(data['messages'][1]['started_at'],first['started_at'])
        self.assertTrue(data['messages'][1]['ended_at'])
        self.assertTrue(all(event.get('at') for event in data['messages'][1]['events']))
        self.assertNotIn('NEVER_DISPLAY',json.dumps(data))
        exported=conversation_history.command(self.db,'chat',cid,'export')
        self.assertIn('SECOND_RESULT',exported['content'])
        self.assertIn('## Changed expert',exported['content'])
        self.assertEqual(tasks.get(self.db,task['id'])['chat_id'],cid)
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual(conversations.read(reopened,cid)['revision'],data['revision'])
        with self.assertRaises(ValueError):conversations.read(self.db,cid,self.other)

    def test_conversation_delete_hides_task_without_removing_task_or_artifact(self):
        task=self.create();run=tasks.start(self.db,task['id'],authority=self.ui)
        with self.assertRaises(ValueError):conversation_history.command(self.db,'chat',task['chat_id'],'trash')
        tasks.complete(self.db,run['id'],self.result(run))
        conversation_history.command(self.db,'chat',task['chat_id'],'trash')
        self.assertFalse(execution_board.snapshot(self.db,self.pid)['items'])
        self.assertEqual(tasks.get(self.db,task['id'])['status'],'awaiting_review')
        conversation_history.command(self.db,'chat',task['chat_id'],'restore')
        item=execution_board.snapshot(self.db,self.pid)['items'][0]
        self.assertEqual(item['chat_id'],task['chat_id'])
        execution_board.visibility(self.db,self.pid,'delete',[{'kind':'task','id':task['id']}])
        self.assertFalse(self.db.chat(task['chat_id'])['deleted_at'])
        conversation_history.command(self.db,'chat',task['chat_id'],'trash')
        conversation_history.command(self.db,'chat',task['chat_id'],'purge')
        self.assertFalse(execution_board.snapshot(self.db,self.pid)['items'])
        self.assertTrue(conversations.link(self.db,'task',task['id']))
        self.assertIsNone(conversations.read(self.db,task['chat_id']))
        self.assertIsNone(self.db.db.execute('SELECT id FROM chats WHERE id=?',(task['chat_id'],)).fetchone())

    def test_board_repairs_missing_chat_and_keeps_its_saved_result(self):
        task = self.create()
        run = tasks.start(self.db, task['id'], authority=self.ui)
        tasks.complete(self.db, run['id'], self.result(run, 'SAVED_RESULT'))
        with self.db.db:
            self.db.db.execute('DELETE FROM chats WHERE id=?', (task['chat_id'],))
        history = conversation_history.route('GET', '/api/conversation-history', {}, {'project_id': [self.pid]}, self.db)
        conversation, = history['conversations']
        self.assertEqual((conversation['id'], conversation['execution_kind']), (task['chat_id'], 'task'))
        item, = execution_board.snapshot(self.db, self.pid)['items']
        self.assertEqual(item['chat_id'], task['chat_id'])
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual(reopened.chat(item['chat_id'])['task_id'], self.pid)
            self.assertIn('SAVED_RESULT', json.dumps(conversations.read(reopened, item['chat_id'])))

    def test_upgrade_restores_missing_chat_and_leaves_permanently_deleted_chat_absent(self):
        import migrations
        missing = self.create()
        purged = self.create()
        conversation_history.command(self.db, 'chat', purged['chat_id'], 'trash')
        conversation_history.command(self.db, 'chat', purged['chat_id'], 'purge')
        with self.db.db:
            self.db.db.execute('DELETE FROM chats WHERE id=?', (missing['chat_id'],))
            self.db.db.execute('DELETE FROM execution_conversations WHERE chat_id=?', (missing['chat_id'],))
            self.db.db.execute('DROP INDEX execution_board_conversation_lifecycle')
            self.db.db.execute("DELETE FROM schema_migrations WHERE version='038_execution_conversation_guarantees'")
        self.assertEqual(migrations.run_migrations(self.db.db), ['038_execution_conversation_guarantees'])
        self.assertEqual(self.db.chat(missing['chat_id'])['task_id'], self.pid)
        self.assertIsNone(self.db.db.execute('SELECT id FROM chats WHERE id=?', (purged['chat_id'],)).fetchone())

    def test_multiple_normal_turns_have_one_board_card(self):
        cid=self.db.create_chat('Conversation',task_id=self.pid)
        runs=[]
        for index in range(3):
            run=RunManager.get_instance().create_run(cid,cid+str(index),self.db)
            run.emit({'type':'done','content':str(index)},self.db);runs.append(run.run_id)
        rows=execution_board.snapshot(self.db,self.pid)['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],cid)
        self.assertEqual(rows[0]['run_id'],runs[-1])
        execution_board.visibility(self.db,self.pid,'delete',[{'kind':'conversation','id':cid}])
        fourth=RunManager.get_instance().create_run(cid,cid+'4',self.db)
        fourth.emit({'type':'done','content':'4'},self.db)
        self.assertTrue(execution_board.snapshot(self.db,self.pid)['items'][0]['deleted_at'])

    def test_followup_to_task_conversation_does_not_create_duplicate_card(self):
        task=self.create();cid=task['chat_id']
        run=RunManager.get_instance().create_run(cid,cid,self.db)
        run.emit({'type':'done','content':'Follow-up'},self.db)
        self.db.add_message(cid,'assistant','Follow-up',sender='Writer')
        rows=execution_board.snapshot(self.db,self.pid)['items']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['chat_id'],cid)
        self.assertEqual(conversations.read(self.db,cid)['messages'][-1]['sender'],'Writer')

    def test_rename_keeps_conversation_and_board_titles_consistent(self):
        import task_management
        task=self.create();cid=task['chat_id']
        conversation_history.command(self.db,'chat',cid,'rename',title='Conversation title')
        self.assertEqual(execution_board.snapshot(self.db,self.pid)['items'][0]['title'],'Conversation title')
        self.assertEqual(conversations.read(self.db,cid)['execution']['title'],'Conversation title')
        task_management.command(self.db,self.pid,task['id'],'organize',{'title':'Task title'})
        self.assertEqual(self.db.chat(cid)['title'],'Task title')
        self.assertEqual(execution_board.snapshot(self.db,self.pid)['items'][0]['title'],'Task title')

    def test_workflow_has_one_overview_card_and_dedicated_node_conversations(self):
        import workflow_layout
        import workflow_tasks
        workflow_layout.ensure_schema(self.db)
        template=workflow_layout.save_layout(self.db,{'name':'Evidence workflow','nodes':[
            {'id':'first','step':'task','label':'First expert','executor_binding_id':self.binding['id']},
            {'id':'second','step':'task','label':'Second expert','executor_binding_id':self.binding['id']}],
            'edges':[{'source':'first','target':'second'}]})['layout']
        run=workflow_tasks.prepare(self.db,self.pid,'Workflow goal','workflow-once',template['id'])
        again=workflow_tasks.prepare(self.db,self.pid,'Workflow goal','workflow-once',template['id'])
        self.assertEqual(run['chat_id'],again['chat_id'])
        with self.db.db:
            self.db.db.execute('DELETE FROM chats WHERE id=?', (run['chat_id'],))
        import workflow_execution
        self.assertEqual(workflow_execution.detail(self.db, run['id'])['chat_id'], run['chat_id'])
        self.assertEqual(self.db.chat(run['chat_id'])['task_id'], self.pid)
        data=conversations.read(self.db,run['chat_id'],self.pid)
        self.assertEqual(len(data['execution']['children']),2)
        self.assertEqual(len({row['chat_id'] for row in data['execution']['children']}),2)
        rows=execution_board.snapshot(self.db,self.pid)['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['chat_id'],run['chat_id'])
        self.assertEqual(rows[0]['execution_kind'],'workflow')

    def test_workflow_hides_internal_failed_node_input_from_overview_conversation(self):
        import workflow_layout
        import workflow_tasks
        workflow_layout.ensure_schema(self.db)
        template=workflow_layout.save_layout(self.db,{'name':'Failed input visibility','nodes':[
            {'id':'first','step':'task','label':'First expert','executor_binding_id':self.binding['id']}],
            'edges':[]})['layout']
        run=workflow_tasks.prepare(self.db,self.pid,'Workflow goal','workflow-failed-input',template['id'])
        child=tasks.list_tasks(self.db,self.pid,workflow_run_id=run['id'])[0]
        started=tasks.start(self.db,child['id'],authority=self.ui)
        tasks.fail(self.db,started['id'],'PROVIDER_FAILURE')
        self.db.add_message(run['chat_id'],'user',child['goal'])
        self.db.add_message(run['chat_id'],'user',child['goal']+'\n【当前项目明确引用的材料】\ninternal context')
        self.db.add_message(run['chat_id'],'user','A real follow-up question')
        data=conversations.read(self.db,run['chat_id'],self.pid)
        user_content=[item['content'] for item in data['messages'] if item['role']=='user']
        self.assertEqual(user_content,['Workflow goal','A real follow-up question'])
        self.assertIn('PROVIDER_FAILURE',json.dumps(data,ensure_ascii=False))

    def test_upgrade_backfills_legacy_tasks_without_losing_results(self):
        import migrations
        task=self.create();run=tasks.start(self.db,task['id'],authority=self.ui)
        tasks.complete(self.db,run['id'],self.result(run,'LEGACY_RESULT'))
        self.db.assistant_save('legacy-caption',name='图注助手',enabled=1)
        self.db.db.execute('DELETE FROM chats WHERE id IN (SELECT chat_id FROM execution_conversations)')
        self.db.db.execute('DROP TABLE execution_conversation_events')
        self.db.db.execute('DROP TABLE execution_conversations')
        self.db.db.execute("DELETE FROM schema_migrations WHERE version='030_execution_conversations'")
        self.db.db.commit()
        self.assertEqual(migrations.run_migrations(self.db.db),['030_execution_conversations'])
        upgraded=tasks.get(self.db,task['id'])
        self.assertEqual(upgraded['chat_id'],task['chat_id'])
        self.assertEqual(upgraded['status'],'awaiting_review')
        self.assertIn('LEGACY_RESULT',json.dumps(conversations.read(self.db,task['chat_id'])))
        self.assertEqual(self.db.assistant('legacy-caption')['name'],'图注专家')
        self.assertFalse(migrations.run_migrations(self.db.db))

    def test_team_message_keeps_expert_identity_when_member_is_reassigned(self):
        import team
        member={'id':'test-member','name':'证据分析员','assistant_id':'task-writer'}
        cid=self.db.create_chat('Team identity',task_id=self.pid)
        team._save_reply(self.db,cid,member,'Analysis','team:identity')
        self.db.assistant_save('task-writer',name='Renamed expert',enabled=1)
        saved=self.db.messages(cid,'team:identity')[0]
        self.assertEqual(saved['sender'],'证据分析员')
        self.assertEqual(saved['events'][0]['sender'],'Writer')

    def test_workflow_step_tasks_are_filtered_from_project_conversations_and_healed(self):
        w_run_id = 'wr-step-test'
        self.db.db.execute("INSERT INTO workflow_runs(id,project_id,workflow,status,started_at) VALUES (?,?,'default','running',datetime('now'))", (w_run_id, self.pid))
        layout = {
            'id': 'layout-step-test',
            'name': 'Test Step Flow',
            'nodes': [
                {'id': 'n1', 'label': 'S1 起步', 'step': 'task'},
                {'id': 'n2', 'label': 'P1 并行A', 'step': 'task'},
                {'id': 'n3', 'label': 'P2 并行B', 'step': 'task'},
                {'id': 'n4', 'label': 'J1 汇合', 'step': 'task'}
            ],
            'edges': [
                {'source': 'n1', 'target': 'n2'},
                {'source': 'n1', 'target': 'n3'},
                {'source': 'n2', 'target': 'n4'},
                {'source': 'n3', 'target': 'n4'}
            ]
        }
        self.db.db.execute("INSERT INTO workflow_executions(run_id,layout_snapshot,settings,updated_at) VALUES (?,?,'{}',datetime('now'))", (w_run_id, json.dumps(layout)))
        step_task = tasks.create(self.db, {"project_id": self.pid, "title": "Legacy Unformatted Step Title", "goal": "Analyze the evidence", "executor_binding_id": self.binding["id"], "workflow_run_id": w_run_id, "workflow_node_id": "n2", "scope": {"kind": "workflow", "allowed_binding_ids": [self.binding["id"]]}, "metadata": {}}, authority=tasks.CommandContext.system(self.pid))

        conversations.ensure_project(self.db, self.pid)
        res = conversation_history.route('GET', '/api/conversation-history', {}, {'project_id': [self.pid]}, self.db)
        conv_titles = [c['title'] for c in res['conversations']]

        self.assertNotIn('Legacy Unformatted Step Title', conv_titles)
        self.assertNotIn('Step 2.1: P1 并行A', conv_titles)
        self.assertNotIn('Test Step Flow', conv_titles)

        healed_task = tasks.get(self.db, step_task['id'])
        self.assertEqual(healed_task['title'], 'Step 2.1: P1 并行A')
        self.assertEqual(healed_task['metadata']['workflow_node']['step_no'], '2.1')
        self.assertTrue(healed_task['metadata']['workflow_node']['is_parallel'])

    def test_execution_chat_lifecycle_syncs_task_queries_plans_and_hidden_rows(self):
        import project_task_routes
        import project_todos
        import research_plans

        plan = project_todos.add_todo(self.db, self.pid, 'Plan synced with task chat')
        task = self.create(title='Synced task', goal='Draft synced evidence')
        self.db.db.execute(
            'INSERT INTO plan_task_links(plan_id,task_id,created_at) VALUES (?,?,datetime("now"))',
            (plan['id'], task['id']),
        )
        self.db.db.commit()

        chat_id = conversations.ensure(self.db, 'task', task['id'])
        self.assertIn(task['id'], [t['id'] for t in tasks.list_tasks(self.db, self.pid)])
        all_res = project_task_routes.route('GET', '/api/project-tasks/all', {}, {}, self.db)
        self.assertIn(task['id'], [t['id'] for t in all_res['tasks']])
        self.assertIn(task['id'], [t['id'] for t in research_plans.linked(self.db, plan['id'])])

        conversation_history.command(self.db, 'chat', chat_id, 'trash')
        self.assertNotIn(task['id'], [t['id'] for t in tasks.list_tasks(self.db, self.pid)])
        all_after_trash = project_task_routes.route('GET', '/api/project-tasks/all', {}, {}, self.db)
        self.assertNotIn(task['id'], [t['id'] for t in all_after_trash['tasks']])
        self.assertEqual(research_plans.linked(self.db, plan['id']), [])
        with self.assertRaises(ValueError):
            tasks.start(self.db, task['id'], authority=tasks.CommandContext.ui(self.pid))

        conversation_history.command(self.db, 'chat', chat_id, 'restore')
        self.assertIn(task['id'], [t['id'] for t in tasks.list_tasks(self.db, self.pid)])

        conversation_history.command(self.db, 'chat', chat_id, 'archive')
        self.assertNotIn(task['id'], [t['id'] for t in tasks.list_tasks(self.db, self.pid)])
        with self.assertRaises(ValueError):
            tasks.start(self.db, task['id'], authority=tasks.CommandContext.ui(self.pid))

        conversation_history.command(self.db, 'chat', chat_id, 'restore')
        conversation_history.command(self.db, 'chat', chat_id, 'trash')
        conversation_history.command(self.db, 'chat', chat_id, 'purge')
        self.assertEqual(
            self.db.db.execute(
                'SELECT COUNT(*) FROM execution_board_hidden WHERE project_id=? AND item_kind=? AND item_id=?',
                (self.pid, 'task', task['id']),
            ).fetchone()[0],
            0,
        )
        self.assertNotIn(task['id'], [t['id'] for t in tasks.list_tasks(self.db, self.pid)])
        with self.assertRaises(ValueError):
            tasks.start(self.db, task['id'], authority=tasks.CommandContext.ui(self.pid))

    def test_bind_chat_rejects_execution_toolbox_and_active_runs(self):
        task = self.create(title='Bound execution chat', goal='Should stay bound')
        exec_chat_id = conversations.ensure(self.db, 'task', task['id'])
        other_pid = self.db.create_task('Other project')
        with self.assertRaises(ValueError):
            self.db.bind_chat(exec_chat_id, other_pid)

        toolbox_cid = self.db.create_chat('Toolbox session')
        self.db.db.execute("UPDATE chats SET channel='toolbox' WHERE id=?", (toolbox_cid,))
        self.db.db.commit()
        with self.assertRaises(ValueError):
            self.db.bind_chat(toolbox_cid, other_pid)

        active_cid = self.db.create_chat('Running dialogue')
        run = RunManager.get_instance().create_run(active_cid, 'bind-' + active_cid, self.db)
        with self.assertRaises(ValueError):
            self.db.bind_chat(active_cid, other_pid)
        run.emit({'type': 'done', 'content': 'Finished'}, self.db)
        self.assertEqual(self.db.bind_chat(active_cid, other_pid)['task_id'], other_pid)
