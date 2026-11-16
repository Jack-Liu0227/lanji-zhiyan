"""Persistent history commands preserve files and reject destructive active-run changes."""
from pathlib import Path
import tempfile
import unittest

import agent_jobs
import conversation_history as history
from run_manager import RunManager
import workflow


class ConversationHistoryTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=workflow.WorkflowDB(Path(tmp.name)/'app.sqlite3');self.addCleanup(self.db.close)
        self.pid=self.db.create_task('History project');self.cid=self.db.create_chat('Research dialogue');self.db.bind_chat(self.cid,self.pid)
        self.db.add_message(self.cid,'assistant','Persistent scientific notes')

    def test_pin_rename_archive_trash_restore_and_export_keep_content(self):
        history.command(self.db,'chat',self.cid,'pin')
        history.command(self.db,'chat',self.cid,'rename',title='Renamed research')
        self.assertTrue(self.db.chat(self.cid)['pinned_at'])
        history.command(self.db,'chat',self.cid,'archive')
        self.assertNotIn(self.cid,[row['id'] for row in self.db.chats()])
        exported=history.command(self.db,'chat',self.cid,'export')
        self.assertIn('Persistent scientific notes',exported['content'])
        history.command(self.db,'chat',self.cid,'trash')
        self.assertTrue(self.db.messages(self.cid))
        history.command(self.db,'chat',self.cid,'restore')
        self.assertIn(self.cid,[row['id'] for row in self.db.chats()])
        self.assertFalse(self.db.chat(self.cid)['archived_at'])

    def test_active_run_blocks_trash_and_hidden_chat_cannot_start(self):
        run=RunManager.get_instance().create_run(self.cid,'history-'+self.cid,self.db)
        with self.assertRaises(ValueError):history.command(self.db,'chat',self.cid,'trash')
        run.emit({'type':'done','content':'Finished'},self.db)
        history.command(self.db,'chat',self.cid,'trash')
        with self.assertRaises(ValueError):agent_jobs.start(self.db.path,{'task_id':self.cid,'message':'Should not run'})
        other=self.db.create_task('Other')
        with self.assertRaises(ValueError):history.command(self.db,'chat',self.cid,'restore',project_id=other)

    def test_team_sessions_use_the_same_flags_without_changing_team_files(self):
        import agent_settings
        team=self.db.team_add('Research team',project_id=self.pid)
        session=agent_settings.session(self.db,team['id'],create=True,title='Team dialogue')
        self.db.add_message(team['id'],'assistant','Team research',scope='team:'+session['id'])
        history.command(self.db,'team',session['id'],'pin',team_id=team['id'])
        history.command(self.db,'team',session['id'],'trash',team_id=team['id'])
        with self.assertRaises(ValueError):agent_settings.session(self.db,team['id'],session['id'])
        history.command(self.db,'team',session['id'],'restore',team_id=team['id'])
        self.assertIn('Team research',history.command(self.db,'team',session['id'],'export',team_id=team['id'])['content'])

    def test_trash_only_dissolves_the_chat_owned_temporary_team_and_cancels_its_work(self):
        import agent_settings
        import project_bindings
        import project_tasks
        other = self.db.create_chat('Other steward dialogue')
        args = {'project': self.pid, 'name': 'Temporary extraction', 'assistant_ids': ['builtin-searcher', 'builtin-extractor']}
        temporary = workflow._tool_impl('team_create', self.db, self.pid, args, origin_chat_id=self.cid)['team']
        formed = workflow._tool_impl('team_form', self.db, self.pid, {
            'purpose': 'Check provenance', 'members': [self.db.assistant('builtin-searcher')['name']],
        }, origin_chat_id=self.cid)
        retained = workflow._tool_impl('team_create', self.db, self.pid, {**args, 'persistent': True}, origin_chat_id=self.cid)['team']
        unrelated = workflow._tool_impl('team_create', self.db, self.pid, args, origin_chat_id=other)['team']
        session = agent_settings.session(self.db, temporary['id'], create=True)
        binding = next(item for item in project_bindings.list_bindings(self.db, self.pid)
                       if item['target_id'] == temporary['id'])
        task = project_tasks.create(self.db, {
            'project_id': self.pid, 'goal': 'Work that must stop',
            'executor_binding_id': binding['id'],
        }, authority=project_tasks.CommandContext.ui(self.pid))
        history.command(self.db, 'chat', self.cid, 'trash')
        self.assertEqual(self.db.team(temporary['id'])['status'], 'dissolved')
        self.assertEqual(self.db.team(formed['id'])['status'], 'dissolved')
        self.assertEqual(project_tasks.get(self.db, task['id'])['status'], 'cancelled')
        self.assertEqual(self.db.db.execute('SELECT active FROM team_sessions WHERE id=?', (session['id'],)).fetchone()[0], 0)
        self.assertNotIn(temporary['id'], [row['id'] for row in self.db.teams()])
        self.assertNotEqual(self.db.team(unrelated['id'])['status'], 'dissolved')
        self.assertNotEqual(self.db.team(retained['id'])['status'], 'dissolved')
        history.command(self.db, 'chat', self.cid, 'purge')
        self.assertEqual(self.db.team(temporary['id'])['status'], 'dissolved')
