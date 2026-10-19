"""Focused tests for dependency-aware team execution and review gates."""
from __future__ import annotations

import sys
import tempfile
import unittest
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
            return {"reply": "completed: " + prompt}

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


if __name__ == "__main__":
    unittest.main()
