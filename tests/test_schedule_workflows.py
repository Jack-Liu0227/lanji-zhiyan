from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import schedule
import schedule_workflow
import workflow
import workflow_execution as engine
import workflow_layout


class ScheduleWorkflowsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = workflow.WorkflowDB(Path(temp.name) / 'test.sqlite3')
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task('Scheduled workflow', base_dir=str(Path(temp.name) / 'project'))
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {'name': 'Scheduled outline', **workflow_layout._linear(['outline'])})['layout']
        self.sched = self.db.schedule_save({'name': 'Reviewed report', 'project_id': self.pid, 'workflow_id': layout['id'],
            'prompt': 'Build reviewed evidence', 'frequency': 'manual', 'output_dir': 'Reports'})
        patched = patch.object(schedule, 'DB_PATH', self.db.path)
        patched.start()
        self.addCleanup(patched.stop)
        kick = patch.object(engine, 'kick')
        kick.start()
        self.addCleanup(kick.stop)

    def advance(self, run_id):
        def write(db, pid, prompt, **kwargs):
            workflow.doc_write(db, pid, 'outline', '# Scheduled artifact\nAccepted evidence')
            return {'reply': 'Scheduled result'}
        with patch.object(workflow, 'run_agent', side_effect=write):
            return engine.advance(self.db, run_id)

    def test_schedule_waits_for_review_and_archives_once_after_final_delivery(self):
        attempt = schedule.run_once(self.sched)
        run_id = attempt['workflow_run_id']
        run = self.advance(run_id)
        self.assertEqual(run['status'], 'awaiting_review')
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'awaiting_review')
        self.assertIsNone(self.db.schedule_runs()[0]['note_id'])
        self.assertEqual(schedule.run_once(self.sched)['status'], 'skipped')
        engine.review(self.db, run_id, run['current_node'], True, 'Human evidence approved')
        self.advance(run_id)
        result = self.db.schedule_runs()[0]
        self.assertEqual(result['status'], 'completed')
        note = self.db.md_note(result['note_id'])
        note_path = self.db._md_note_path(note)
        content = note_path.read_text(encoding='utf-8')
        self.assertIn('Accepted evidence', content)
        self.assertIn('Human evidence approved', content)
        self.assertIn(run_id, content)
        note_path.write_text(content + '\nUser edit', encoding='utf-8')
        schedule_workflow.reconcile(self.db)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM md_notes WHERE id LIKE 'schedule-%'").fetchone()[0], 1)
        self.assertIn('User edit', note_path.read_text(encoding='utf-8'))

    def test_rejection_and_restart_preserve_waiting_attempt(self):
        attempt = schedule.run_once(self.sched)
        run = self.advance(attempt['workflow_run_id'])
        engine.review(self.db, run['id'], run['current_node'], False, 'Add evidence')
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'blocked')
        engine.recover(self.db.path)
        schedule.recover_runs()
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'blocked')
        self.assertIsNone(self.db.schedule_runs()[0]['note_id'])
        self.assertEqual(schedule.run_once(self.sched)['status'], 'skipped')

    def test_cancel_schedule_cancels_linked_workflow_without_success_note(self):
        attempt = schedule.run_once(self.sched)
        self.advance(attempt['workflow_run_id'])
        schedule.cancel_run(self.sched['id'], self.db.path)
        self.assertEqual(engine.detail(self.db, attempt['workflow_run_id'])['status'], 'cancelled')
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'canceled')
        self.assertIsNone(self.db.schedule_runs()[0]['note_id'])

    def test_schedule_does_not_adopt_unrelated_existing_workflow(self):
        workflow_layout.select_layout(self.db, {'task_id': self.pid, 'layout_id': self.sched['workflow_id']})
        original = engine.create(self.db, self.pid, 'Manual unrelated run')
        attempt = schedule.run_once(self.sched)
        self.assertEqual(attempt['status'], 'blocked')
        self.assertEqual(engine.detail(self.db, original['id'])['settings']['goal'], 'Manual unrelated run')
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM schedule_workflow_runs').fetchone()[0], 0)
