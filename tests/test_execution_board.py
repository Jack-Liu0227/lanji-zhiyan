"""Plan goals, runtime attempts and project event replay remain distinct."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import execution_board as board
import project_bindings
import project_task_execution
import project_tasks
import project_todos
import research_plans
import project_task_routes
import task_tools
from run_manager import RunManager
import workflow
import conversation_history


class ExecutionBoardTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=workflow.WorkflowDB(Path(tmp.name)/'app.sqlite3');self.addCleanup(self.db.close)
        self.pid=self.db.create_task('Research');self.other=self.db.create_task('Other')
        self.binding=project_bindings.register(self.db,self.pid,'assistant','builtin-steward')['id']

    def conversation_run(self,status='failed',channel='conversation'):
        cid=self.db.create_chat('Bound dialogue',task_id=self.pid)
        self.db.db.execute('UPDATE chats SET channel=? WHERE id=?',(channel,cid));self.db.db.commit()
        self.db.add_message(cid,'user','KEEP_CONVERSATION_CONTENT')
        run=RunManager.get_instance().create_run(cid,'cleanup-'+cid,self.db)
        run.emit({'type':'error','message':'SAVED_FAILURE'} if status=='failed' else {'type':'done','content':'SAVED_RESULT'},self.db)
        return cid,run.run_id

    def test_global_reads_share_project_records_and_mutations_remain_scoped(self):
        tasks=[]
        for pid in (self.pid,self.other):
            binding=project_bindings.register(self.db,pid,'assistant','builtin-steward')['id']
            tasks.append(project_tasks.create(self.db,{'project_id':pid,'title':'Same title','executor_binding_id':binding},authority=project_tasks.CommandContext.ui(pid)))
            research_plans.route('POST','/api/plans/save',{'project_id':pid,'title':'Project plan'},{},self.db)
        query={'all_projects':['1']}
        result=board.route('GET','/api/execution-board',{},query,self.db)
        self.assertEqual({row['id'] for row in result['items']},{task['id'] for task in tasks})
        self.assertEqual({row['project_name'] for row in result['items']},{'Research','Other'})
        for pid in (self.pid,self.other):
            scoped=board.snapshot(self.db,pid)['items']
            self.assertEqual([r['chat_id'] for r in scoped],[r['chat_id'] for r in result['items'] if r['project_id']==pid])
        plans=research_plans.route('GET','/api/plans',{},query,self.db)['plans']
        self.assertEqual({p['project_id'] for p in plans},{self.pid,self.other})
        self.assertEqual({e['project_id'] for e in board.events(self.db,'',all_projects=True)['events']},{self.pid,self.other})
        cursor=result['cursor']
        board.publish(self.db,self.other,'test_global_update')
        self.assertEqual(board.events(self.db,'',cursor,all_projects=True)['events'][0]['kind'],'test_global_update')
        for router,path in ((board.route,'/api/execution-board/visibility'),(research_plans.route,'/api/plans/delete')):
            with self.assertRaises(ValueError):router('POST',path,{'id':plans[0]['id'],'items':[{'kind':'task','id':tasks[0]['id']}],'action':'delete'},query,self.db)
        with self.assertRaises(ValueError):board.visibility(self.db,self.pid,'delete',[{'kind':'task','id':tasks[1]['id']}])
        with self.assertRaises(ValueError):research_plans.route('POST','/api/plans/delete',{'project_id':self.other,'id':next(p['id'] for p in plans if p['project_id']==self.pid)},{},self.db)

    def test_deleted_conversations_remove_all_attempts_and_publish_live_refresh(self):
        cid,first=self.conversation_run()
        second=RunManager.get_instance().create_run(cid,'another-'+cid,self.db)
        second.emit({'type':'done','content':'Second result'},self.db)
        cursor=board.snapshot(self.db,self.pid)['cursor']
        conversation_history.command(self.db,'chat',cid,'trash',project_id=self.pid)
        self.assertFalse(board.snapshot(self.db,self.pid)['items'])
        self.assertTrue(any(row['kind']=='conversation_trash' for row in board.events(self.db,self.pid,cursor)['events']))
        self.assertTrue(self.db.messages(cid))
        conversation_history.command(self.db,'chat',cid,'restore',project_id=self.pid)
        self.assertEqual({item['id'] for item in board.snapshot(self.db,self.pid)['items']},{cid})
        conversation_history.command(self.db,'chat',cid,'trash',project_id=self.pid)
        conversation_history.command(self.db,'chat',cid,'purge',project_id=self.pid)
        self.assertFalse(board.snapshot(self.db,self.pid)['items'])

    def test_old_deleted_chats_are_filtered_without_repeating_deletion(self):
        cid,identifier=self.conversation_run()
        self.db.db.execute("UPDATE chats SET deleted_at='2026-10-28' WHERE id=?",(cid,));self.db.db.commit()
        self.assertFalse(board.snapshot(self.db,self.pid)['items'])
        for action in ('delete','restore'):
            with self.assertRaises(ValueError):board.visibility(self.db,self.pid,action,[{'kind':'conversation','id':identifier}])
        with self.assertRaises(ValueError):board.route('POST','/api/execution-board/conversation-action',{'project_id':self.pid,'run_id':identifier,'action':'resume'},{},self.db)

    def test_board_deletion_preserves_conversation_runtime_and_survives_reopen(self):
        cid,identifier=self.conversation_run()
        item={'kind':'conversation','id':identifier}
        board.visibility(self.db,self.pid,'delete',[item]);board.visibility(self.db,self.pid,'delete',[item])
        self.assertFalse(self.db.chat(cid)['deleted_at']);self.assertIn('KEEP_CONVERSATION_CONTENT',str(self.db.messages(cid)))
        self.assertEqual(self.db.get_async_task(identifier)['status'],'failed')
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertTrue(board.snapshot(reopened,self.pid)['items'][0]['deleted_at'])
        conversation_history.command(self.db,'chat',cid,'trash',project_id=self.pid)
        conversation_history.command(self.db,'chat',cid,'restore',project_id=self.pid)
        self.assertTrue(board.snapshot(self.db,self.pid)['items'][0]['deleted_at'])
        board.visibility(self.db,self.pid,'restore',[item])
        self.assertFalse(board.snapshot(self.db,self.pid)['items'][0]['deleted_at'])

    def test_batch_delete_all_statuses_is_scoped_atomic_and_does_not_cancel_tasks(self):
        identifiers=[]
        for status in ('ready','queued','running','awaiting_review','delivered','failed','cancelled','blocked','interrupted'):
            task=project_tasks.create(self.db,{'project_id':self.pid,'title':status,'executor_binding_id':self.binding},authority=project_tasks.CommandContext.ui(self.pid))
            self.db.db.execute('UPDATE project_tasks SET status=? WHERE id=?',(status,task['id']))
            identifiers.append({'kind':'task','id':task['id']})
        self.db.db.commit()
        cid,run=self.conversation_run(channel='toolbox');identifiers.append({'kind':'conversation','id':run})
        with self.assertRaises(ValueError):board.visibility(self.db,self.other,'delete',identifiers)
        with self.assertRaises(ValueError):board.visibility(self.db,self.pid,'delete',identifiers+[{'kind':'task','id':'MISSING'}])
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM execution_board_hidden').fetchone()[0],0)
        board.visibility(self.db,self.pid,'delete',identifiers)
        self.assertTrue(all(row['deleted_at'] for row in board.snapshot(self.db,self.pid)['items']))
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_tasks WHERE status='running'").fetchone()[0],1)
        self.assertTrue(all(not json.loads(row[0]).get('deleted_at') for row in self.db.db.execute('SELECT metadata FROM project_tasks')))
        self.assertFalse(self.db.chat(cid)['deleted_at'])
        board.visibility(self.db,self.pid,'restore',identifiers)
        self.assertFalse(any(row['deleted_at'] for row in board.snapshot(self.db,self.pid)['items']))

    def test_plan_links_multiple_tasks_and_completes_only_after_actual_delivery(self):
        plan=research_plans.route('POST','/api/plans/save',{'project_id':self.pid,'title':'Review evidence','acceptance':'Two independent reports'}, {},self.db)['plan']
        tasks=[]
        for title in ('Analyze evidence','Compare methods'):
            task=project_tasks.create(self.db,{'project_id':self.pid,'title':title,'executor_binding_id':self.binding,'review_required':False},authority=project_tasks.CommandContext.ui(self.pid))
            board.link_plan(self.db,self.pid,plan['id'],task['id']);tasks.append(task)
        with self.assertRaises(ValueError): project_todos.update_todo(self.db,plan['id'],status='DONE')
        for index,task in enumerate(tasks):
            run=project_tasks.start(self.db,task['id'],authority=project_tasks.CommandContext.ui(self.pid))
            with patch.object(workflow,'run_agent',return_value={'reply':'# Real saved report\nEvidence with limitations.'}):
                completed=project_task_execution.execute(self.db,run['id'])
            self.assertEqual(completed['status'],'delivered',completed)
            status=self.db.db.execute('SELECT status FROM project_todos WHERE id=?',(plan['id'],)).fetchone()[0]
            self.assertEqual(status,'DONE' if index==1 else 'IN_PROGRESS')
        self.assertEqual(len(research_plans.linked(self.db,plan['id'])),2)
        with self.assertRaises(ValueError): board.link_plan(self.db,self.other,plan['id'],tasks[0]['id'])

    def test_agent_events_replay_by_cursor_and_never_leak_other_project(self):
        chat=self.db.create_chat('Project dialogue');self.db.bind_chat(chat,self.pid)
        run=RunManager.get_instance().create_run(chat,'board-replay-'+self.pid,self.db)
        run.emit({'type':'tool_call','name':'project_resources','args':{'api_key':'do-not-copy'}},self.db)
        first=board.events(self.db,self.pid)
        self.assertEqual(first['events'][-1]['activity'],'project_resources')
        self.assertNotIn('do-not-copy',str(first))
        run.emit({'type':'done','content':'Result'},self.db)
        later=board.events(self.db,self.pid,first['cursor'])
        self.assertEqual(len(later['events']),1)
        self.assertEqual(board.events(self.db,self.other)['events'],[])
        card=next(row for row in board.snapshot(self.db,self.pid)['items'] if row['run_id']==run.run_id)
        self.assertEqual(card['status'],'completed')
        with self.assertRaises(ValueError):board.route('POST','/api/execution-board/conversation-action',{'project_id':self.other,'run_id':run.run_id,'action':'stop'},{},self.db)

    def test_plan_scope_acceptance_and_dependencies_apply_to_ui_and_agent(self):
        first=research_plans.route('POST','/api/plans/save',{'project_id':self.pid,'title':'Prerequisite'},{},self.db)['plan']
        plan=workflow._tool_impl('project_task_manage',self.db,self.pid,{'action':'create','title':'Evidence synthesis','acceptance':'Include provenance and limitations','dependencies':[first['id']]})
        args={'project_id':self.pid,'plan_id':plan['id'],'title':'Synthesis','executor_binding_id':self.binding}
        for create in (lambda body:project_task_routes.route('POST','/api/project-tasks/create',body,{},self.db),lambda body:task_tools.call(self.db,self.pid,'task_create',body)):
            task=create(args)['task']
            self.assertIn(plan['acceptance'],task['goal'])
            with self.assertRaisesRegex(ValueError,'前置研究计划'):project_tasks.start(self.db,task['id'],authority=project_tasks.CommandContext.ui(self.pid))
        count=len(project_tasks.list_tasks(self.db,self.other))
        with self.assertRaises(ValueError):project_task_routes.route('POST','/api/project-tasks/create',{**args,'project_id':self.other},{},self.db)
        self.assertEqual(len(project_tasks.list_tasks(self.db,self.other)),count)
