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
        data=conversations.read(self.db,run['chat_id'],self.pid)
        self.assertEqual(len(data['execution']['children']),2)
        self.assertEqual(len({row['chat_id'] for row in data['execution']['children']}),2)
        rows=execution_board.snapshot(self.db,self.pid)['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['chat_id'],run['chat_id'])
        self.assertEqual(rows[0]['execution_kind'],'workflow')

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
