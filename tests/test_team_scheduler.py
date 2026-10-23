"""Focused tests for dependency-aware team execution and review gates."""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import team  # noqa: E402
import workflow  # noqa: E402


class TeamSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-team-dag-")
        self.path = Path(self.tmp.name) / "research.sqlite3"
        self.previous_db = team.DB_PATH
        team.DB_PATH = self.path
        with workflow.WorkflowDB(self.path) as db:
            self.project = db.create_task(
                "DAG project", "question", "material", str(Path(self.tmp.name) / "workspace")
            )
        self.worker = team.add_member(self.project, "worker", "search")
        self.reviewer = team.add_member(self.project, "reviewer", "review")

    def tearDown(self) -> None:
        team.DB_PATH = self.previous_db
        self.tmp.cleanup()

    def _run_fake(self):
        def fake_run_agent(db, task_id, prompt, **kwargs):
            return {"reply": "# summary\ncompleted: " + prompt + "\n# gaps\nNone"}

        return fake_run_agent

    def _status(self, row_id: str) -> dict:
        with workflow.WorkflowDB(self.path) as db:
            return db.team_task(row_id) or {}

    def test_dependencies_hold_queue_until_prerequisite_finishes(self) -> None:
        first = team.assign(self.project, self.worker["id"], "collect sources")
        second = team.assign(
            self.project,
            self.worker["id"],
            "summarize sources",
            depends_on=[first["id"]],
            output_contract={"sections": ["summary", "gaps"]},
        )

        old = workflow.run_agent
        workflow.run_agent = self._run_fake()
        try:
            self.assertEqual(team.tick(), 1)
            self.assertEqual(self._status(first["id"])["status"], "done")
            self.assertEqual(self._status(second["id"])["status"], "queued")
            self.assertEqual(team.tick(), 1)
        finally:
            workflow.run_agent = old

        finished = self._status(second["id"])
        self.assertEqual(finished["status"], "done")
        self.assertEqual(finished["depends_on"], "[\"%s\"]" % first["id"])
        self.assertIn("sections", finished["output_contract"])

    def test_missing_dependency_and_invalid_contract_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "依赖任务不存在"):
            team.assign(self.project, self.worker["id"], "blocked", depends_on=["not-a-task"])
        with self.assertRaisesRegex(ValueError, "output_contract"):
            team.assign(self.project, self.worker["id"], "bad contract", output_contract=["summary"])

    def test_failed_dependency_blocks_downstream_task(self) -> None:
        first = team.assign(self.project, self.worker["id"], "collect sources")
        second = team.assign(self.project, self.worker["id"], "summarize sources",
                             depends_on=[first["id"]])
        with workflow.WorkflowDB(self.path) as db:
            db.team_task_mark(first["id"], "failed", reason="upstream failure")
        self.assertEqual(team.tick(), 0)
        blocked = self._status(second["id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(first["id"], blocked["reason"])

    def test_reviewer_gate_requeues_on_rejection_and_completes_on_approval(self) -> None:
        row = team.assign(
            self.project,
            self.worker["id"],
            "draft evidence table",
            reviewer_id=self.reviewer["id"],
        )
        old = workflow.run_agent
        workflow.run_agent = self._run_fake()
        try:
            self.assertEqual(team.tick(), 1)
            review_row = self._status(row["id"])
            self.assertEqual(review_row["status"], "review")
            self.assertEqual(review_row["review_status"], "pending")

            returned = team.review(row["id"], False, self.reviewer["id"], "add page citations")
            self.assertEqual(returned["status"], "queued")
            self.assertEqual(returned["review_attempt"], 1)

            self.assertEqual(team.tick(), 1)
            self.assertEqual(self._status(row["id"])["status"], "review")
            approved = team.review(row["id"], True, self.reviewer["id"], "looks good")
        finally:
            workflow.run_agent = old

        self.assertEqual(approved["status"], "done")
        self.assertEqual(approved["review_status"], "approved")
        self.assertEqual(approved["review_feedback"], "looks good")

    def test_reviewer_must_belong_to_project_team(self) -> None:
        with self.assertRaisesRegex(ValueError, "Reviewer"):
            team.assign(self.project, self.worker["id"], "draft", reviewer_id="external-member")

    def test_running_task_can_stop_without_late_success(self):
        entered, release = threading.Event(), threading.Event()
        row = team.assign(self.project, self.worker["id"], "collect sources")
        def model(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"content": "Late response"}
        with patch.object(workflow, "_chat_raw", side_effect=model):
            worker = threading.Thread(target=team.tick)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                team.cancel(row["id"])
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(self._status(row["id"])["status"], "canceled")
                self.assertFalse(self._status(row["id"])["output_path"])
            finally:
                release.set()
                worker.join(5)

    def test_restart_queues_saved_model_boundary_and_blocks_unknown_tool(self):
        safe = team.assign(self.project, self.worker["id"], "safe restart")
        unsafe = team.assign(self.project, self.worker["id"], "unsafe restart")
        with workflow.WorkflowDB(self.path) as db:
            db.team_task_mark(safe["id"], "running", checkpoint={"phase": "model", "messages": [{"role": "user", "content": "saved"}]})
            db.team_task_mark(unsafe["id"], "running", checkpoint={"phase": "tool"})
        result = team.recover_tasks()
        self.assertEqual(result["resumed"], [safe["id"]])
        self.assertEqual(result["interrupted"], [unsafe["id"]])
        def restored(db, *args, **kwargs):
            self.assertTrue(db.resuming)
            self.assertEqual(db.job_checkpoint["messages"][0]["content"], "saved")
            return {"reply": "Recovered output"}
        with patch.object(workflow, "run_agent", side_effect=restored) as run:
            self.assertEqual(team.tick(), 1)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self._status(safe["id"])["status"], "done")
        self.assertEqual(self._status(unsafe["id"])["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
