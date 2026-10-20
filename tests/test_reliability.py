"""Tests for tasks, task_attempts, run_events, and audit_events."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research_core import ResearchDB
from run_manager import RunManager


class ReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-test-rel-")
        self.db_path = Path(self.tmp.name) / "test_rel.sqlite3"
        self.db = ResearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_async_task_lifecycle(self):
        task = self.db.create_async_task("translation", "proj-1", idempotency_key="idemp-1", checkpoint={"page": 1})
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["checkpoint"], {"page": 1})
        self.assertEqual(task["progress"], 0.0)

        by_idemp = self.db.get_async_task_by_idempotency("idemp-1")
        self.assertEqual(by_idemp["id"], task["id"])

        updated = self.db.update_async_task(task["id"], status="running", progress=0.5, checkpoint={"page": 5})
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["progress"], 0.5)
        self.assertEqual(updated["checkpoint"], {"page": 5})

        cancelled = self.db.update_async_task(task["id"], cancel_requested=True)
        self.assertEqual(cancelled["cancel_requested"], 1)

    def test_task_attempts_record(self):
        task = self.db.create_async_task("download", "proj-2")
        att1 = self.db.record_task_attempt(task["id"], 1, "failed", error_code="TIMEOUT")
        self.assertEqual(att1["error_code"], "TIMEOUT")
        att2 = self.db.record_task_attempt(task["id"], 2, "success")
        self.assertEqual(att2["status"], "success")

    def test_run_events_persistence_and_after_replay(self):
        run_id = "run-test-123"
        self.db.record_run_event(run_id, 1, "start", {"msg": "Starting"})
        self.db.record_run_event(run_id, 2, "progress", {"pct": 50})
        self.db.record_run_event(run_id, 3, "done", {"pct": 100})

        all_events = self.db.get_run_events(run_id, after_id=0)
        self.assertEqual(len(all_events), 3)
        self.assertEqual(all_events[0]["event_id"], 1)

        after_events = self.db.get_run_events(run_id, after_id=1)
        self.assertEqual(len(after_events), 2)
        self.assertEqual(after_events[0]["event_id"], 2)

    def test_audit_events_logging_and_filtering(self):
        self.db.record_audit_event("asst-1", assistant_id="asst-1", tool="paper_search",
                                  project_id="proj-audit", input_hash="hash-1", outcome="ok", duration_ms=45)
        self.db.record_audit_event("asst-2", assistant_id="asst-2", tool="doc_write",
                                  project_id="proj-audit-2", input_hash="hash-2", outcome="error", duration_ms=120)

        events_proj = self.db.get_audit_events(project_id="proj-audit")
        self.assertEqual(len(events_proj), 1)
        self.assertEqual(events_proj[0]["tool"], "paper_search")
        self.assertEqual(events_proj[0]["duration_ms"], 45)


class RunManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-test-run-manager-")
        self.db_path = Path(self.tmp.name) / "test_run_manager.sqlite3"
        self.db = ResearchDB(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_cancel_arriving_before_run_creation_is_preserved(self):
        manager = RunManager.get_instance()
        key = "cancel-before-create-reliability"
        self.assertTrue(manager.cancel_by_key(key, db=self.db))
        run = manager.create_run(task_id="proj-1", idempotency_key=key, db=self.db)
        self.assertTrue(run.cancel_requested)
        self.assertEqual(run.status, "cancelled")

    def test_background_jobs_persistence_and_run_manager(self):
        job = self.db.create_background_job("bg-123", task_id="proj-1", status="running", progress=0.1, last_event_id=5)
        self.assertEqual(job["id"], "bg-123")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["last_event_id"], 5)

        updated = self.db.update_background_job("bg-123", status="suspended", progress=0.5, last_event_id=10)
        self.assertEqual(updated["status"], "suspended")
        self.assertEqual(updated["progress"], 0.5)
        self.assertEqual(updated["last_event_id"], 10)

        manager = RunManager.get_instance()
        run = manager.create_run(task_id="proj-1", idempotency_key="idemp-bg-test", db=self.db)
        self.assertTrue(run.run_id)

        bg_job = self.db.get_background_job(run.run_id)
        self.assertIsNotNone(bg_job)
        self.assertEqual(bg_job["status"], "running")

        event = run.emit({"type": "info", "message": "processing step 1"}, db=self.db)
        self.assertEqual(event["event_id"], 1)
        self.assertEqual(self.db.get_background_job(run.run_id)["last_event_id"], 1)

        run.emit({"type": "done", "content": "finished"}, db=self.db)
        done = self.db.get_background_job(run.run_id)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["progress"], 1.0)


if __name__ == "__main__":
    unittest.main()
