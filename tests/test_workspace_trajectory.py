"""Trajectory records are scoped durable observations of actual model/tool work."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_jobs
import agent_settings
import workflow
import workspace_trajectory as trace


class WorkspaceTrajectoryTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rw-trajectory-')
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'test.sqlite3'
        with workflow.WorkflowDB(self.path) as db:
            self.project=db.create_task('Trace project',base_dir=str(Path(temporary.name)/'project'))
            self.other=db.create_task('Other trace project',base_dir=str(Path(temporary.name)/'other'))
            self.chat=db.create_chat('Tool trajectory')
            db.db.execute('UPDATE chats SET task_id=? WHERE id=?',(self.project,self.chat))
            db.add_model('Trace fixture','https://fixture.invalid/v1','trace-model','fixture-key',activate=True)
            db.db.commit()

    def test_completed_actual_tool_call_is_inspectable_after_reopening_database(self):
        replies=[{'content':'','tool_calls':[{'id':'trace-call','function':{'name':'library_overview','arguments':'{}'}}]},
                 {'content':'TRACE_COMPLETE','usage':{'prompt':30,'completion':12,'total':42,'exact':True}}]
        with patch.object(workflow,'_chat_raw',side_effect=replies):
            run=agent_jobs.start(self.path,{'task_id':self.chat,'message':'Inspect project library'})
            deadline=time.monotonic()+8
            while run.worker_started and time.monotonic()<deadline:time.sleep(.02)
            self.assertFalse(run.worker_started)
        with workflow.WorkflowDB(self.path) as db:
            listing=trace.listing(db,self.chat,self.project)
            self.assertEqual(listing['runs'][0]['status'],'done',[row['payload'] for row in db.get_run_events(run.run_id) if row['kind']=='error'])
            data=trace.events(db,self.chat,self.project,run.run_id)
            call=next(row['payload'] for row in data['events'] if row['kind']=='tool_call')
            result=next(row['payload'] for row in data['events'] if row['kind']=='tool_result')
            self.assertEqual(call['id'],result['id'])
            self.assertEqual(call['name'],'library_overview')
            self.assertTrue(result['ok'])
            self.assertIsInstance(result['result'],dict)
            self.assertGreaterEqual(result['duration_ms'],0)
            self.assertIn('round',call)
            response=next(row['payload'] for row in data['events'] if row['kind']=='model_response')
            self.assertGreaterEqual(response['duration_ms'],0)
            self.assertNotIn('fixture-key',str(listing))

    def test_other_project_and_other_conversations_run_cannot_be_read(self):
        with workflow.WorkflowDB(self.path) as db:
            with self.assertRaises(ValueError):trace.listing(db,self.chat,self.other)
            unknown=db.create_async_task('agent_run','other-chat')
            with self.assertRaises(ValueError):trace.events(db,self.chat,self.project,unknown['id'])

    def test_pagination_preserves_distinct_calls_and_does_not_create_missing_timings(self):
        with workflow.WorkflowDB(self.path) as db:
            ids=[]
            for i in range(4):
                row=db.create_async_task('agent_run',self.chat);ids.append(row['id'])
            recent=trace.listing(db,self.chat,self.project,limit=2)
            earlier=trace.listing(db,self.chat,self.project,before=recent['next_before'],limit=2)
            self.assertEqual([r['id'] for r in recent['runs']+earlier['runs']],list(reversed(ids)))
            for i in range(4):db.record_run_event(ids[-1],i+1,'tool_call',{'type':'tool_call','id':str(i),'name':'read_attachment','args':{}})
            first=trace.events(db,self.chat,self.project,ids[-1],limit=2)
            second=trace.events(db,self.chat,self.project,ids[-1],after=first['next_after'],limit=2)
            self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
            self.assertEqual([r['event_id'] for r in first['events']+second['events']],[1,2,3,4])
            self.assertNotIn('duration_ms',first['events'][0]['payload'])

    def test_sensitive_fields_are_masked_and_large_results_are_explicit_previews(self):
        value=trace.safe_result({'api_key':'HIDDEN_KEY','nested':{'Authorization':'Bearer SHOULD_NOT_APPEAR'},'result':'visible'})
        self.assertNotIn('HIDDEN_KEY',str(value));self.assertNotIn('SHOULD_NOT_APPEAR',str(value))
        self.assertEqual(value['result'],'visible')
        large=trace.safe_result({'text':'a'*160000})
        self.assertTrue(large['truncated']);self.assertGreater(large['total_characters'],150000)


if __name__=='__main__':unittest.main()
