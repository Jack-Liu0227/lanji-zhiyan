"""Runtime choices stay with a conversation and are fixed for one durable run."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import agent_jobs
import agent_settings
import workflow
import workspace_chat


class WorkspaceChatOptionsTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='rw-chat-options-')
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'test.sqlite3'
        with workflow.WorkflowDB(self.path) as db:
            self.project=db.create_task('Conversation choices',base_dir=str(Path(temporary.name)/'project'))
            self.one=db.create_chat('First')
            self.two=db.create_chat('Second')
            for chat in (self.one,self.two):
                db.db.execute('UPDATE chats SET task_id=? WHERE id=?',(self.project,chat))
            db.add_model('first','https://fixture.invalid/v1','first-model','fixture-key',activate=True)
            db.add_model('second','https://fixture.invalid/v1','second-model','fixture-key')
            self.models={m['name']:m['id'] for m in db.models()}
            db.db.commit()

    def save(self,db,chat,**values):
        return agent_settings.route('POST','/api/agent-settings/runtime-save',{'id':chat,**values},{},db)

    def wait(self,run):
        deadline=time.monotonic()+8
        while run.worker_started and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(run.worker_started)

    def test_per_chat_runtime_does_not_change_other_chat_or_global_model(self):
        with workflow.WorkflowDB(self.path) as db:
            self.save(db,self.one,model_id=self.models['second'],permission='read_only',reasoning='high')
            first=workspace_chat.snapshot(db,self.one)
            second=workspace_chat.snapshot(db,self.two)
            self.assertEqual((first['model_id'],first['permission'],first['reasoning']),(self.models['second'],'read_only','high'))
            self.assertEqual(second['model_id'],self.models['first'])
            self.assertEqual(db.active_model()['id'],self.models['first'])
            with self.assertRaises(ValueError):self.save(db,self.one,permission='invalid')

    def test_running_request_and_reconnect_keep_original_model_and_permission(self):
        entered,release=threading.Event(),threading.Event()
        profiles=[]
        def model(profile,*args,**kwargs):
            profiles.append(dict(profile));entered.set();release.wait(5)
            return {'content':'Runtime choices verified'}
        with workflow.WorkflowDB(self.path) as db:self.save(db,self.one,model_id=self.models['second'],permission='read_only')
        request={'task_id':self.one,'message':'Inspect current project','idempotency_key':'fixed-chat-choice'}
        with patch.object(workflow,'_chat_raw',side_effect=model):
            run=agent_jobs.start(self.path,request)
            try:
                self.assertTrue(entered.wait(5))
                with workflow.WorkflowDB(self.path) as db:
                    self.save(db,self.one,model_id=self.models['first'],permission='full_access')
                self.assertIs(agent_jobs.start(self.path,request),run)
            finally:
                release.set();self.wait(run)
        self.assertEqual(len(profiles),1)
        self.assertEqual(profiles[0]['id'],self.models['second'])
        self.assertEqual(profiles[0]['permission'],'read_only')
        with workflow.WorkflowDB(self.path) as db:
            saved=db.get_async_task(run.run_id)['checkpoint']['request']['_runtime_snapshot']
            self.assertEqual(saved['permission'],'read_only')
            self.assertNotIn('api_key',saved)

    def test_request_cannot_forge_a_more_permissive_runtime_snapshot(self):
        with workflow.WorkflowDB(self.path) as db:self.save(db,self.one,permission='read_only')
        with patch.object(workflow,'_chat_raw',return_value={'content':'Checked'}):
            run=agent_jobs.start(self.path,{'task_id':self.one,'message':'Read','_runtime_snapshot':{'permission':'full_access'}})
            self.wait(run)
        with workflow.WorkflowDB(self.path) as db:
            self.assertEqual(db.get_async_task(run.run_id)['checkpoint']['request']['_runtime_snapshot']['permission'],'read_only')


if __name__=='__main__':unittest.main()
