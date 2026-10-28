"""Board and conversation state must describe the same live execution."""
import unittest

from tests import test_project_task_service as service
import execution_board
import execution_conversations
import project_tasks
from run_manager import RunManager


class ExecutionBoardStateTest(unittest.TestCase):
    setUp=service.ProjectTaskServiceTest.setUp
    create=service.ProjectTaskServiceTest.create
    result=service.ProjectTaskServiceTest.result

    def card(self,task):
        return next(row for row in execution_board.snapshot(self.db,self.pid)['items'] if row['id']==task['id'])

    def assert_state(self,task,status):
        self.assertEqual(self.card(task)['status'],status)
        self.assertEqual(execution_conversations.read(self.db,task['chat_id'])['execution']['status'],status)

    def chat_run(self,task):
        run=RunManager.get_instance().create_run(task['chat_id'],db=self.db)
        self.db.update_async_task(run.run_id,status='running')
        run.emit({'type':'run_started'},self.db)
        return run

    def test_task_runs_reviews_and_deliveries_have_matching_board_status(self):
        task=self.create();self.assert_state(task,'ready')
        run=project_tasks.start(self.db,task['id'],authority=self.ui)
        self.assert_state(task,'queued')
        project_tasks.claim(self.db,run['id']);self.assert_state(task,'running')
        project_tasks.complete(self.db,run['id'],self.result(run));self.assert_state(task,'awaiting_review')
        chat=self.chat_run(task);self.assert_state(task,'running')
        self.assertEqual(self.card(task)['active_run_id'],chat.run_id)
        chat.emit({'type':'done','content':'Explained'},self.db)
        self.assert_state(task,'awaiting_review')
        project_tasks.review(self.db,task['id'],approved=True,authority=self.ui)
        self.assert_state(task,'delivered')

    def test_followup_stop_does_not_cancel_a_delivered_task(self):
        task=self.create(review_required=False)
        run=project_tasks.start(self.db,task['id'],authority=self.ui)
        project_tasks.complete(self.db,run['id'],self.result(run))
        chat=self.chat_run(task);self.assert_state(task,'running')
        card=self.card(task)
        self.assertEqual((card['active_run_kind'],card['execution_status']),('conversation','delivered'))
        execution_board.route('POST','/api/execution-board/conversation-action',
            {'project_id':self.pid,'run_id':card['active_run_id'],'action':'stop'},{},self.db)
        self.assert_state(task,'delivered')
        self.assertEqual(project_tasks.get(self.db,task['id'])['deliveries'][-1]['source'],'policy')

    def test_finishing_a_discussion_does_not_claim_an_unstarted_task_is_delivered(self):
        task=self.create();chat=self.chat_run(task);self.assert_state(task,'running')
        chat.emit({'type':'done','content':'Discussed goal'},self.db)
        self.assert_state(task,'ready')
        self.assertEqual(self.card(task)['activity'],'not_started')
        self.assertFalse(project_tasks.get(self.db,task['id'])['deliveries'])

    def test_later_completed_turn_does_not_hide_an_older_running_turn(self):
        cid=self.db.create_chat('Parallel runs',task_id=self.pid)
        first=RunManager.get_instance().create_run(cid,db=self.db)
        self.db.update_async_task(first.run_id,status='running')
        second=RunManager.get_instance().create_run(cid,db=self.db)
        second.emit({'type':'done','content':'Second finished'},self.db)
        rows=execution_board.snapshot(self.db,self.pid)['items']
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['status'],rows[0]['run_id']),('running',first.run_id))
        first.emit({'type':'done','content':'First finished'},self.db)
        self.assertEqual(execution_board.snapshot(self.db,self.pid)['items'][0]['status'],'completed')
