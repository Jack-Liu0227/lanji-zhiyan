"""Scheduled execution uses canonical delivery and preserves schedule archives."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_bindings
import project_tasks
import schedule
import schedule_workflow
import workflow
import workflow_execution
import workflow_layout


class ScheduleProjectTaskTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rw-schedule-tasks-")
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Schedule project", base_dir=str(Path(self.temp.name) / "project"))
        self.db.assistant_save("schedule-writer", name="Schedule writer", enabled=1)
        self.sched = self.db.schedule_save({"name": "Evidence digest", "project_id": self.pid, "assistant_id": "schedule-writer", "prompt": "Produce evidence digest", "frequency": "manual", "output_dir": "Reports"})
        patched = patch.object(schedule, "DB_PATH", self.db.path)
        patched.start()
        self.addCleanup(patched.stop)

    def test_report_has_canonical_task_and_preserved_legacy_attempts(self):
        with patch.object(workflow, "run_agent", return_value={"reply": "Canonical evidence digest"}) as agent:
            attempt = schedule.run_once(self.sched, "manual")
        self.assertEqual(attempt["status"], "completed", attempt)
        self.assertEqual(agent.call_count, 1)
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertEqual(task["status"], "delivered")
        self.assertEqual(task["deliveries"][0]["source"], "policy")
        self.assertEqual(task["latest_run"]["policy_snapshot"]["autonomy_mode"], "supervised")
        self.assertTrue(all(Path(f["path"]).is_relative_to(self.db.base_dir(self.pid)) for f in task["outputs"]))
        self.assertEqual(self.db.db.execute("SELECT status FROM workflow_runs WHERE id=?", (attempt["workflow_run_id"],)).fetchone()["status"], "succeeded")
        self.assertTrue(project_tasks.get(self.db, task["id"])["origins"])
        self.assertIn("Canonical evidence digest", Path(attempt["output_path"]).read_text(encoding="utf-8"))

    def test_human_review_from_task_updates_schedule_and_archives_once(self):
        with patch.object(workflow, "run_agent", return_value={"reply": "Awaiting accepted evidence"}):
            attempt = schedule.run_once({**self.sched, "review_required": True})
        self.assertEqual(attempt["status"], "awaiting_review")
        self.assertEqual(self.db.md_notes(), [])
        self.assertEqual(schedule.run_once(self.sched)["status"], "skipped")
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        project_tasks.review(self.db, task["id"], approved=True, authority=project_tasks.CommandContext.ui(self.pid))
        record = self.db.schedule_runs()[0]
        self.assertEqual(record["status"], "completed")
        note = self.db.md_note(record["note_id"])
        path = self.db._md_note_path(note)
        path.write_text(path.read_text(encoding="utf-8") + "\nUser annotation", encoding="utf-8")
        schedule_workflow.reconcile(self.db)
        self.assertEqual(len(self.db.md_notes()), 1)
        self.assertIn("User annotation", path.read_text(encoding="utf-8"))

    def test_project_full_access_is_inherited_without_changing_tool_permission(self):
        project_bindings.save_settings(self.db, self.pid, "full_access")
        self.db.assistant_save("schedule-writer", name="Schedule writer", enabled=1, permission="read_only")
        with patch.object(workflow, "run_agent", return_value={"reply": "Read-only evidence summary"}):
            attempt = schedule.run_once({**self.sched, "review_required": True})
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertEqual(task["status"], "delivered")
        self.assertEqual(task["latest_run"]["executor_snapshot"]["runtime_config"]["permission"], "read_only")

    def test_disabled_grant_is_not_revived_by_scheduled_execution(self):
        project_bindings.register(self.db, self.pid, "assistant", "schedule-writer", enabled=False)
        with patch.object(workflow, "run_agent") as agent:
            attempt = schedule.run_once(self.sched)
        agent.assert_not_called()
        self.assertEqual(attempt["status"], "blocked")
        self.assertFalse(project_bindings.list_bindings(self.db, self.pid)[0]["enabled"])
        self.assertEqual(self.db.md_notes(), [])

    def test_cancellation_through_schedule_never_archives_late_model_reply(self):
        def execute(db, *args, **kwargs):
            schedule.cancel_run(self.sched["id"], db.path)
            return {"reply": "Too late"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            attempt = schedule.run_once(self.sched)
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(attempt["status"], "canceled")
        self.assertEqual(self.db.md_notes(), [])

    def test_failed_report_has_no_note_and_keeps_canonical_error(self):
        with patch.object(workflow, "run_agent", side_effect=RuntimeError("MODEL_FAILURE_MARKER")):
            attempt = schedule.run_once(self.sched)
        self.assertEqual(attempt["status"], "failed")
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertIn("MODEL_FAILURE_MARKER", task["latest_run"]["error"])
        self.assertEqual(self.db.md_notes(), [])

    def test_rejected_dispatch_stays_blocked_until_a_real_retry(self):
        import execution_board
        import execution_conversations
        import project_task_execution
        with patch.object(project_tasks, 'start', side_effect=ValueError('START_REJECTED')):
            attempt = schedule.run_once(self.sched)
        task = project_tasks.find_origin(self.db, 'schedule_run', attempt['id'])
        schedule_workflow.reconcile(self.db)
        self.assertEqual(project_tasks.get(self.db, task['id'])['status'], 'blocked')
        record = self.db.schedule_runs()[0]
        self.assertEqual(record['status'], 'blocked')
        self.assertEqual(record['reason'], 'START_REJECTED')
        self.assertTrue(record['ended_at'])
        card = next(row for row in execution_board.snapshot(self.db, self.pid)['items'] if row['id'] == task['id'])
        self.assertEqual((card['status'], card['error']), ('blocked', 'START_REJECTED'))
        self.assertEqual(execution_conversations.read(self.db, task['chat_id'])['execution']['error'], 'START_REJECTED')
        run = project_tasks.start(self.db, task['id'], authority=project_tasks.CommandContext.ui(self.pid))
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'queued')
        with patch.object(workflow, 'run_agent', return_value={'reply': 'Recovered report'}):
            project_task_execution.execute(self.db, run['id'])
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'completed')
        self.assertEqual(self.db.schedule_runs()[0]['reason'], '')
        self.assertNotIn('_start_error', project_tasks.get(self.db, task['id'])['metadata'])

    def test_recovery_closes_legacy_unstarted_schedule_without_running_model(self):
        with patch.object(project_tasks, 'start', side_effect=ValueError('LOST_START')), patch.object(project_tasks, 'block_start'):
            attempt = schedule.run_once(self.sched)
        task = project_tasks.find_origin(self.db, 'schedule_run', attempt['id'])
        self.db.db.execute("UPDATE schedule_runs SET status='running',reason='' WHERE id=?", (attempt['id'],))
        self.db.db.commit()
        schedule_workflow.reconcile(self.db)
        self.assertEqual(self.db.schedule_runs()[0]['status'], 'queued')
        with patch.object(workflow, 'run_agent') as model:
            self.assertEqual(schedule.recover_runs(), 1)
            self.assertEqual(schedule.recover_runs(), 0)
        model.assert_not_called()
        self.assertEqual(project_tasks.get(self.db, task['id'])['status'], 'blocked')
        record = self.db.schedule_runs()[0]
        self.assertEqual(record['status'], 'blocked')
        self.assertIn('启动执行前中断', record['reason'])
        self.assertEqual(self.db.md_notes(), [])

    def test_scheduled_workflow_task_review_wakes_archival_pipeline(self):
        workflow_layout.ensure_schema(self.db)
        data = workflow_layout._linear(["outline"])
        data["nodes"][0]["assistant_id"] = "schedule-writer"
        layout = workflow_layout.save_layout(self.db, {"name": "Reviewed workflow", **data})["layout"]
        sched = self.db.schedule_save({**self.sched, "workflow_id": layout["id"]})
        with patch.object(workflow_execution, "kick"):
            attempt = schedule.run_once(sched)
        def execute(db, pid, *args, **kwargs):
            workflow.doc_write(db, pid, "outline", "# Workflow evidence\nFinal accepted report")
            return {"reply": "Workflow digest"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            run = workflow_execution.advance(self.db, attempt["workflow_run_id"])
        task = project_tasks.find_origin(self.db, "workflow_node", run["current_node"])
        with patch.object(workflow_execution, "kick") as kick:
            project_tasks.review(self.db, task["id"], approved=True, authority=project_tasks.CommandContext.ui(self.pid))
            kick.assert_called_once_with(self.db.path, run["id"])
        workflow_execution.advance(self.db, run["id"])
        self.assertEqual(self.db.schedule_runs()[0]["status"], "completed")

    def linked_stage(self):
        import project_todos
        workflow_layout.ensure_schema(self.db)
        data = workflow_layout._linear(["outline"])
        data["nodes"][0]["assistant_id"] = "schedule-writer"
        layout = workflow_layout.save_layout(self.db, {"name": "Stage schedule", **data})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        todo = project_todos.add_todo(self.db, self.pid, "Scheduled stage", stage="outline")
        return self.db.schedule_save({**self.sched, "todo_id": todo["id"]})

    def test_linked_stage_requires_real_artifact_before_human_review(self):
        sched = self.linked_stage()
        with patch.object(workflow, "run_agent", return_value={"reply": "Only claims it is complete"}):
            attempt = schedule.run_once(sched)
        self.assertEqual(attempt["status"], "failed")
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertEqual(task["metadata"]["stage"], "outline")
        self.assertTrue(task["review_required"])
        self.assertEqual(task["deliveries"], [])
        self.assertEqual(self.db.md_notes(), [])

    def test_linked_stage_review_rejects_edited_working_artifact(self):
        sched = self.linked_stage()
        def execute(db, pid, *args, **kwargs):
            self.assertEqual(kwargs["scope"], "outline")
            workflow.doc_write(db, pid, "outline", "# Evidence\nVersion accepted for review")
            return {"reply": "Stage ready"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            attempt = schedule.run_once(sched)
        self.assertEqual(attempt["status"], "awaiting_review")
        task = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
        self.assertTrue(task["metadata"]["legacy_stage_compat"])
        self.assertTrue(task["latest_run"]["checkpoint"]["input"]["upstream_hash"])
        workflow.doc_write(self.db, self.pid, "outline", "Edited during review")
        with self.assertRaises(ValueError):
            project_tasks.review(self.db, task["id"], approved=True, authority=project_tasks.CommandContext.ui(self.pid))
        self.assertEqual(self.db.md_notes(), [])


if __name__ == "__main__":
    unittest.main()
