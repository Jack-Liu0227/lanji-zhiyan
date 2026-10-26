from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asset_service
import project_notes
import schedule
import space_service
import workflow


class ScheduleNotesTest(unittest.TestCase):
    def test_completed_runs_archive_once_in_project_notes_with_provenance(self):
        with tempfile.TemporaryDirectory() as folder, workflow.WorkflowDB(Path(folder) / "app.sqlite3") as db:
            pid = db.create_task("Notes", base_dir=str(Path(folder) / "project"))
            sched = db.schedule_save({"name": "每周文献报告", "project_id": pid, "assistant_id": "builtin-steward", "prompt": "Report"})
            with patch.object(schedule, "DB_PATH", db.path), patch.object(workflow, "run_agent", return_value={"reply": "UNIQUE_SCHEDULE_NOTE"}):
                run = schedule.run_once(sched, "manual")
            self.assertEqual(run["status"], "completed", run)
            note = db.md_note(run["note_id"])
            self.assertEqual(note["task_id"], pid)
            self.assertIn("UNIQUE_SCHEDULE_NOTE", note["content"])
            self.assertIn(run["id"], note["content"])
            self.assertEqual(db.schedule_runs()[0]["note_id"], note["id"])
            self.assertIn(note["id"], {n["id"] for n in space_service.list_project_notes(db, pid)})
            self.assertTrue(any("UNIQUE_SCHEDULE_NOTE" in n["excerpt"] for n in project_notes.dashboard(db, pid)["items"]))
            output = asset_service.get(db, run["output_assets"][-2])
            schedule.archive_note(db, sched, run, "must not replace user changes", output)
            self.assertEqual(len(db.md_notes()), 1)
            self.assertEqual(db.md_note(note["id"])["content"], note["content"])
            edges = db.db.execute("SELECT * FROM asset_relations WHERE from_asset_id=? AND relation='derived_from'", (run["output_assets"][-1],)).fetchall()
            self.assertTrue(edges)

    def test_failed_run_does_not_create_note(self):
        with tempfile.TemporaryDirectory() as folder, workflow.WorkflowDB(Path(folder) / "app.sqlite3") as db:
            pid = db.create_task("Notes", base_dir=str(Path(folder) / "project"))
            sched = db.schedule_save({"name": "Fail", "project_id": pid, "assistant_id": "builtin-steward", "prompt": "Fail"})
            with patch.object(schedule, "DB_PATH", db.path), patch.object(workflow, "run_agent", side_effect=RuntimeError("model failed")):
                self.assertEqual(schedule.run_once(sched)["status"], "failed")
            self.assertEqual(db.md_notes(), [])
