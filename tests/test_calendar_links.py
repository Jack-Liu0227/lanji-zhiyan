from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import calendar_service
import project_todos
import schedule
import workflow
import workflow_execution
import workflow_layout


class CalendarLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Calendar links", base_dir=str(Path(self.tmp.name) / "project"))

    def linked(self, kind):
        return [e for e in self.db.events() if e.get("source_type") == kind]

    def test_todo_deadline_moves_and_calendar_checkbox_updates_canonical_task(self):
        todo = project_todos.add_todo(self.db, self.pid, "Evidence deadline", due_date="2026-10-26")
        first = self.linked("todo")[0]
        self.assertEqual(first["task_id"], self.pid)
        self.assertTrue(first["can_toggle"])
        self.assertEqual(self.db.events("2026-10")[0]["source_id"], todo["id"])
        project_todos.update_todo(self.db, todo["id"], due_date="2026-11-02", title="Updated evidence deadline")
        self.assertEqual(self.db.events("2026-10"), [])
        current = self.db.events("2026-11")[0]
        self.assertEqual(current["id"], first["id"])
        self.assertEqual(current["title"], "Updated evidence deadline")
        self.db.toggle_event(current["id"], True)
        self.assertEqual(project_todos.list_todos(self.db, self.pid)[0]["status"], "DONE")
        self.assertEqual(self.db.event(current["id"])["done"], 1)
        with self.assertRaisesRegex(ValueError, "来源"):
            self.db.delete_event(current["id"])
        project_todos.delete_todo(self.db, todo["id"])
        self.assertEqual(self.linked("todo"), [])

    def test_scheduled_occurrence_follows_rule_changes_and_pause_without_duplicates(self):
        sched = self.db.schedule_save({"name": "Daily digest", "project_id": self.pid,
            "assistant_id": "builtin-steward", "prompt": "Digest", "next_run_at": "2026-10-27T09:00:00"})
        first = self.linked("schedule")[0]
        self.assertEqual((first["date"], first["time"]), ("2026-10-27", "09:00"))
        self.db.schedule_save({**sched, "next_run_at": "2026-10-28T10:30:00"})
        current = self.linked("schedule")
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["id"], first["id"])
        self.assertEqual((current[0]["date"], current[0]["time"]), ("2026-10-28", "10:30"))
        self.db.schedule_set_enabled(sched["id"], False)
        self.assertEqual(self.linked("schedule"), [])
        self.db.schedule_set_enabled(sched["id"], True)
        self.db.schedule_delete(sched["id"])
        self.assertEqual(self.linked("schedule"), [])

    def test_successful_schedule_calendar_event_opens_its_project_note(self):
        sched = self.db.schedule_save({"name": "Report", "project_id": self.pid, "frequency": "manual",
            "assistant_id": "builtin-steward", "prompt": "Report"})
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", return_value={"reply": "CALENDAR_NOTE_EVIDENCE"}):
            run = schedule.run_once(sched, "manual")
        event = self.linked("schedule_run")[0]
        self.assertEqual(event["source_id"], run["id"])
        self.assertEqual((event["target_type"], event["target_id"]), ("note", run["note_id"]))
        self.assertTrue((self.db.base_dir(self.pid) / event["note_path"]).is_file())
        self.assertTrue(event["done"])
        with self.assertRaisesRegex(ValueError, "审查"):
            self.db.toggle_event(event["id"], False)
        self.assertEqual(self.db.schedule_runs()[0]["status"], "completed")

    def test_workflow_reviews_are_visible_and_calendar_cannot_bypass_acceptance(self):
        workflow_layout.ensure_schema(self.db)
        definition = workflow_layout._linear(["outline"])
        definition["nodes"][0]["assistant_id"] = "builtin-steward"
        layout = workflow_layout.save_layout(self.db, {"name": "Review gate", **definition})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        run = workflow_execution.create(self.db, self.pid)
        def write(db, pid, prompt, **kwargs):
            workflow.doc_write(db, pid, "outline", "# Calendar review evidence")
            return {"reply": "Saved"}
        with patch.object(workflow, "run_agent", side_effect=write):
            run = workflow_execution.advance(self.db, run["id"])
        event = self.linked("workflow_review")[0]
        self.assertEqual((event["date"], event["status"]), (date.today().isoformat(), "awaiting_review"))
        self.assertEqual(event["run_id"], run["id"])
        self.assertFalse(event["can_toggle"])
        with self.assertRaisesRegex(ValueError, "人工审查"):
            self.db.toggle_event(event["id"], True)
        self.assertEqual(workflow_execution.detail(self.db, run["id"])["status"], "awaiting_review")
        workflow_execution.review(self.db, run["id"], run["current_node"], True, "Calendar approval")
        workflow_execution.advance(self.db, run["id"])
        self.assertEqual(self.db.event(event["id"])["status"], "delivered")
        self.assertTrue(self.db.event(event["id"])["done"])
        self.assertEqual(len(self.linked("workflow_run")), 1)

    def test_manual_events_and_project_deletion_keep_their_existing_semantics(self):
        manual = self.db.add_event("Independent meeting", "2026-10-26", self.pid)
        self.db.toggle_event(manual["id"], True)
        self.assertTrue(self.db.event(manual["id"])["done"])
        project_todos.add_todo(self.db, self.pid, "Linked deadline", due_date="2026-10-26")
        self.db.delete_task(self.pid)
        self.assertEqual(self.db.events(), [])
