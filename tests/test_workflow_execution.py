"""Real workflow artifacts prove ordering, review, lineage and restart behavior."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workflow
import workflow_execution as engine
import workflow_layout
import workflow_state


class WorkflowExecutionTest(unittest.TestCase):
    default_executor = "builtin-steward"
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Sequential", base_dir=str(Path(self.tmp.name) / "project"))
        self.seen = []

    def layout(self, nodes):
        workflow_layout.ensure_schema(self.db)
        steps = [n["step"] for n in nodes]
        data = workflow_layout._linear(steps)
        data["nodes"] = [{**n, **({"assistant_id": self.default_executor} if getattr(self, "default_executor", "") and not any(config.get(k) for k in ("assistant_id", "team_id", "member_id")) else {}), **config}
                         for n, config in zip(data["nodes"], nodes)]
        layout = workflow_layout.save_layout(self.db, {"name": "Sequential", **data})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})

    def execute(self, db, pid, prompt, **kwargs):
        self.seen.append(kwargs)
        stage = kwargs["scope"]
        workflow.doc_write(db, pid, stage, "# " + stage + "\nACTUAL_ARTIFACT_" + str(len(self.seen)))
        return {"reply": "RESULT_MEMORY_" + stage, "output_assets": []}

    def test_default_review_stops_each_node_and_feedback_flows_downstream(self):
        self.layout([{"step": "outline"}, {"step": "write"}, {"step": "review"}])
        run = engine.create(self.db, self.pid, "RESEARCH_GOAL")
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, run["id"])
            self.assertEqual(run["status"], "awaiting_review", run)
            self.assertEqual(len(self.seen), 1)
            self.assertEqual(run["nodes"][0]["checkpoint"]["input"]["goal"], "RESEARCH_GOAL")
            first = run["nodes"][0]
            engine.review(self.db, run["id"], first["id"], True, "KEEP_CONTROL_GROUP")
            run = engine.advance(self.db, run["id"])
            self.assertEqual(len(self.seen), 2)
            context = self.seen[1]["extra_context"]
            for token in ("KEEP_CONTROL_GROUP", "RESULT_MEMORY_outline", "ACTUAL_ARTIFACT_1", "RESEARCH_GOAL"):
                self.assertIn(token, context)
            self.assertEqual(run["nodes"][1]["checkpoint"]["input"]["handoff"][0]["step"], "outline")
            engine.review(self.db, run["id"], run["current_node"], True)
            run = engine.advance(self.db, run["id"])
            self.assertIn("KEEP_CONTROL_GROUP", self.seen[2]["extra_context"])
            engine.review(self.db, run["id"], run["current_node"], True)
            self.assertEqual(engine.advance(self.db, run["id"])["status"], "succeeded")
        self.assertTrue(all(s["status"] == "delivered" for s in engine.detail(self.db, run["id"])["nodes"]))

    def test_optional_reviews_execute_end_to_end_and_repeated_start_is_idempotent(self):
        self.layout([{"step": "outline", "review_required": False}, {"step": "write", "review_required": False}])
        run = engine.create(self.db, self.pid, idempotency_key="request-one")
        self.assertEqual(engine.create(self.db, self.pid)["id"], run["id"])
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            self.assertEqual(engine.advance(self.db, run["id"])["status"], "succeeded")
            self.assertEqual(engine.advance(self.db, run["id"])["status"], "succeeded")
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(engine.create(self.db, self.pid, idempotency_key="request-one")["id"], run["id"])

    def test_rejection_retries_same_node_with_feedback_and_saved_upstream(self):
        self.layout([{"step": "outline", "review_required": False}, {"step": "write"}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, run["id"])
            rejected = engine.review(self.db, run["id"], run["current_node"], False, "ADD_MISSING_EVIDENCE")
            self.assertEqual(rejected["status"], "blocked")
            run = engine.advance(self.db, run["id"])
        self.assertEqual(len(self.seen), 3)
        self.assertEqual([s["scope"] for s in self.seen], ["outline", "write", "write"])
        self.assertIn("ADD_MISSING_EVIDENCE", self.seen[-1]["extra_context"])
        self.assertEqual(run["nodes"][-1]["checkpoint"]["feedback"], "ADD_MISSING_EVIDENCE")

    def test_upstream_changes_during_review_block_approval_and_do_not_run_descendants(self):
        self.layout([{"step": "outline", "review_required": False}, {"step": "write"}, {"step": "review"}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, run["id"])
            workflow.doc_write(self.db, self.pid, "outline", "Changed upstream after generation")
            with self.assertRaisesRegex(ValueError, "上游交付"):
                engine.review(self.db, run["id"], run["current_node"], True)
        self.assertEqual(len(self.seen), 2)
        self.assertTrue(workflow_state.gate(self.db, self.pid, "review")["blocked"])

    def test_team_member_and_tool_allowlist_are_used_by_node(self):
        team = self.db.team_add("Evidence team", "custom", project_id=self.pid)
        member = self.db.team_member_add(team["id"], "Writer", "leader", assistant_id="builtin-steward", prompt="TEAM_RULE")
        self.layout([{"step": "outline", "team_id": team["id"], "member_id": member["id"], "tools": ["doc_write"]}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, run["id"])
        self.assertEqual(run["status"], "awaiting_review", run)
        self.assertEqual(self.seen[0]["member"]["id"], member["id"])
        self.assertEqual(self.seen[0]["tool_allowlist"], ["doc_write"])
        self.assertIn("TEAM_RULE", self.seen[0]["extra_context"])
        self.assertEqual(self.db.team_tasks(team["id"])[0]["status"], "review")
        engine.review(self.db, run["id"], run["current_node"], True)
        self.assertEqual(self.db.team_tasks(team["id"])[0]["status"], "done")

    def test_failed_empty_artifact_and_restart_preserve_context_without_success(self):
        self.layout([{"step": "outline", "review_required": False}, {"step": "write"}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", return_value={"reply": "Pretend done"}):
            failed = engine.advance(self.db, run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("完整产物", failed["error"])
        engine._status(self.db, run["id"], "running")
        engine.recover(self.db.path)
        restored = engine.detail(self.db, run["id"])
        self.assertEqual(restored["status"], "failed")
        self.assertTrue(restored["nodes"][0]["checkpoint"]["input"])

    def test_pending_review_survives_restart_and_cancel_preserves_output(self):
        self.layout([{"step": "outline"}])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, run["id"])
        engine.recover(self.db.path)
        with workflow.WorkflowDB(self.db.path) as reopened:
            restored = engine.detail(reopened, run["id"])
            self.assertEqual(restored["status"], "awaiting_review")
            stopped = engine.route("POST", "/api/workflow/execution/cancel", {"id": run["id"]}, {}, reopened)["run"]
        self.assertEqual(stopped["status"], "cancelled")
        self.assertEqual(stopped["nodes"][0]["status"], "cancelled")
        self.assertTrue(workflow.doc_read(self.db, self.pid, "outline")["exists"])

    def test_cancellation_during_node_does_not_start_next_node(self):
        self.layout([{"step": "outline", "review_required": False}, {"step": "write", "review_required": False}])
        run = engine.create(self.db, self.pid)
        cancelled = []
        def run_and_cancel(*args, **kwargs):
            result = self.execute(*args, **kwargs)
            cancelled.append(True)
            return result
        with patch.object(workflow, "run_agent", side_effect=run_and_cancel):
            run = engine.advance(self.db, run["id"], lambda: bool(cancelled))
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(len(self.seen), 1)

    def test_node_model_is_used_by_nested_tools_and_restored_after_execution(self):
        first = self.db.add_model("Default", "http://127.0.0.1:1/v1", "default-model")
        selected = self.db.add_model("Selected", "http://127.0.0.1:2/v1", "selected-model")
        self.db.set_active(first["id"])
        self.db.assistant_save("selected-expert", name="Selected expert", model_id=selected["id"], enabled=1)
        self.layout([{"step": "outline", "assistant_id": "selected-expert"}])
        run = engine.create(self.db, self.pid)
        def execute(db, *args, **kwargs):
            self.assertEqual(db.active_model()["model"], "selected-model")
            return self.execute(db, *args, **kwargs)
        with patch.object(workflow, "run_agent", side_effect=execute):
            run = engine.advance(self.db, run["id"])
        self.assertEqual(run["status"], "awaiting_review", run)
        self.assertEqual(self.db.active_model()["id"], first["id"])
