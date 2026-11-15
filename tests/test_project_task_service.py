"""Behavioral contracts for project work, authority and immutable deliveries."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import project_bindings as bindings
import project_tasks as tasks
import workflow


class ProjectTaskServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rw-project-tasks-")
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.pid = self.db.create_task("Task project", base_dir=str(self.root))
        self.other = self.db.create_task("Other project", base_dir=str(Path(self.temp.name) / "other"))
        self.db.assistant_save("task-writer", name="Writer", enabled=1, permission="full_access")
        self.binding = bindings.register(self.db, self.pid, "assistant", "task-writer")
        self.ui = tasks.CommandContext.ui(self.pid)

    def create(self, **fields):
        return tasks.create(self.db, {"project_id": self.pid, "title": "Write report", "goal": "Analyze the evidence", "executor_binding_id": self.binding["id"], **fields}, authority=self.ui)

    def result(self, run, text="Evidence and analysis", **extra):
        folder = self.root / ".tasks" / run["task_id"] / run["id"] / "outputs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "result.md"
        path.write_text(text, encoding="utf-8")
        return {"reply": text, "output_snapshot": {"content": text, "files": [{"path": str(path), "content_hash": tasks._hash(path), "relative": "result.md"}]}, **extra}

    def test_project_task_without_flow_is_idempotent(self):
        spec = {"project_id": self.pid, "goal": "Report", "executor_binding_id": self.binding["id"]}
        one = tasks.create(self.db, spec, authority=self.ui, request_key="create-1")
        self.assertEqual(one["id"], tasks.create(self.db, spec, authority=self.ui, request_key="create-1")["id"])
        with self.assertRaises(ValueError):
            tasks.create(self.db, {**spec, "goal": "Changed"}, authority=self.ui, request_key="create-1")
        run = tasks.start(self.db, one["id"], authority=self.ui, request_key="start-1")
        self.assertEqual(run["id"], tasks.start(self.db, one["id"], authority=self.ui, request_key="start-1")["id"])
        self.assertEqual(one["workflow_run_id"], "")
        self.assertEqual(run["policy_snapshot"]["autonomy_mode"], "supervised")

    def test_tool_permission_does_not_authorize_automatic_review(self):
        task = self.create()
        run = tasks.start(self.db, task["id"], authority=self.ui)
        done = tasks.complete(self.db, run["id"], self.result(run))
        self.assertEqual(done["status"], "awaiting_review")
        with self.assertRaises(ValueError):
            tasks.review(self.db, task["id"], approved=True, authority=tasks.CommandContext.agent(self.pid))
        delivered = tasks.review(self.db, task["id"], approved=True, authority=self.ui)
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual(delivered["deliveries"][0]["source"], "human")
        self.assertEqual(len(tasks.review(self.db, task["id"], approved=True, authority=self.ui)["deliveries"]), 1)

    def test_full_access_is_trusted_and_still_checks_versions(self):
        task = self.create()
        with self.assertRaises(ValueError):
            tasks.start(self.db, task["id"], authority=tasks.CommandContext.agent(self.pid), autonomy_mode="full_access")
        run = tasks.start(self.db, task["id"], authority=self.ui, autonomy_mode="full_access")
        done = tasks.complete(self.db, run["id"], self.result(run))
        self.assertEqual(done["status"], "delivered")
        self.assertEqual(done["deliveries"][0]["source"], "policy")
        self.assertEqual(done["deliveries"][0]["policy_snapshot"]["source"], "user")

    def test_review_checks_actual_frozen_output_and_binding(self):
        task = self.create()
        run = tasks.start(self.db, task["id"], authority=self.ui)
        value = tasks.complete(self.db, run["id"], self.result(run))
        path = Path(value["outputs"][0]["path"])
        path.write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            tasks.review(self.db, task["id"], approved=True, authority=self.ui)
        self.assertEqual(tasks.get(self.db, task["id"])["status"], "awaiting_review")
        path.write_text("Evidence and analysis", encoding="utf-8")
        bindings.register(self.db, self.pid, "assistant", "task-writer", enabled=False)
        with self.assertRaises(ValueError):
            tasks.review(self.db, task["id"], approved=True, authority=self.ui)

    def test_rejection_retries_same_task_with_feedback_and_new_run(self):
        task = self.create()
        one = tasks.start(self.db, task["id"], authority=self.ui, autonomy_mode="full_access")
        tasks.set_mode(self.db, task["id"], "supervised", authority=self.ui)
        tasks.complete(self.db, one["id"], self.result(one))
        tasks.review(self.db, task["id"], approved=False, feedback="Add source evidence", authority=self.ui)
        two = tasks.start(self.db, task["id"], authority=self.ui)
        self.assertNotEqual(one["id"], two["id"])
        self.assertEqual(two["attempt"], 2)
        self.assertEqual(two["input_snapshot"]["feedback"], "Add source evidence")
        self.assertTrue(Path(tasks.get_run(self.db, one["id"])["output_snapshot"]["files"][0]["path"]).is_file())

    def test_input_copy_is_pinned_and_foreign_path_rejected(self):
        source = self.root / "input.txt"
        source.write_text("version-one", encoding="utf-8")
        task = self.create(inputs=[{"relative": "input.txt"}])
        run = tasks.start(self.db, task["id"], authority=self.ui)
        source.write_text("version-two", encoding="utf-8")
        self.assertEqual(Path(run["input_snapshot"]["assets"][0]["path"]).read_text(encoding="utf-8"), "version-one")
        self.assertEqual(tasks.complete(self.db, run["id"], self.result(run))["status"], "awaiting_review")
        bad = self.create(inputs=[{"relative": "../outside.txt"}])
        with self.assertRaises(ValueError):
            tasks.start(self.db, bad["id"], authority=self.ui)

    def test_child_scope_and_policy_cannot_escape_workflow(self):
        spec = {"project_id": self.pid, "goal": "Flow work", "executor_binding_id": self.binding["id"], "workflow_run_id": "flow-1", "workflow_node_id": "node-1", "scope": {"allowed_binding_ids": [self.binding["id"]], "tools": ["doc_read"]}}
        with self.assertRaises(ValueError):
            tasks.create(self.db, spec, authority=self.ui)
        parent = tasks.create(self.db, spec, authority=tasks.CommandContext.system(self.pid))
        tasks.start(self.db, parent["id"], authority=self.ui, autonomy_mode="full_access")
        authority = tasks.CommandContext.agent(self.pid, parent["id"])
        child_spec = {"project_id": self.pid, "goal": "Child", "executor_binding_id": self.binding["id"]}
        child = tasks.create(self.db, child_spec, authority=authority)
        self.assertEqual(child["workflow_run_id"], "flow-1")
        self.assertEqual(child["workflow_node_id"], "node-1")
        self.assertEqual(tasks.start(self.db, child["id"], authority=authority)["policy_snapshot"]["autonomy_mode"], "full_access")
        for fields in ({"scope": {"tools": None}}, {"workflow_run_id": "flow-2"}, {"review_required": False}, {"parent_task_id": "other"}):
            with self.assertRaises(ValueError):
                tasks.create(self.db, {**child_spec, **fields}, authority=authority)

    def test_contract_and_legacy_capture_are_independent(self):
        task = self.create(output_contract={"min_chars": 10, "sections": ["Sources"], "required_files": ["result.md"]})
        run = tasks.start(self.db, task["id"], authority=self.ui)
        with self.assertRaises(ValueError):
            tasks.complete(self.db, run["id"], self.result(run, "short"))
        capture = {"valid": True, "fingerprint": "legacy-version", "content": "Sources: evidence"}
        done = tasks.complete(self.db, run["id"], self.result(run, "Sources: evidence", checkpoint={"output": capture}))
        self.assertEqual(done["latest_run"]["checkpoint"]["output"], capture)
        self.assertIn("files", done["latest_run"]["output_snapshot"])
        json_task = self.create(output_contract={"format": "json", "json_schema": {"type": "object", "required": ["score"], "properties": {"score": {"type": "number"}}}})
        json_run = tasks.start(self.db, json_task["id"], authority=self.ui)
        with self.assertRaises(ValueError):
            tasks.complete(self.db, json_run["id"], self.result(json_run, '{"score":true}'))
        self.assertEqual(tasks.complete(self.db, json_run["id"], self.result(json_run, '{"score":1}'))["status"], "awaiting_review")

    def test_cancel_cascades_and_late_result_cannot_deliver(self):
        parent = self.create()
        child = tasks.create(self.db, {"goal": "Child", "executor_binding_id": self.binding["id"]}, authority=tasks.CommandContext.agent(self.pid, parent["id"]))
        run = tasks.start(self.db, child["id"], authority=self.ui)
        tasks.cancel(self.db, parent["id"], authority=self.ui)
        self.assertEqual(tasks.complete(self.db, run["id"], self.result(run))["status"], "cancelled")
        self.assertEqual(tasks.get(self.db, child["id"])["deliveries"], [])

    def test_children_gate_automatic_delivery_and_resume_aggregation(self):
        parent = self.create()
        run = tasks.start(self.db, parent["id"], authority=self.ui, autonomy_mode="full_access")
        child = tasks.create(self.db, {"goal": "Child", "executor_binding_id": self.binding["id"]}, authority=tasks.CommandContext.agent(self.pid, parent["id"]))
        self.assertEqual(tasks.complete(self.db, run["id"], self.result(run))["status"], "blocked")
        child_run = tasks.start(self.db, child["id"], authority=tasks.CommandContext.agent(self.pid, parent["id"]))
        with mock.patch("project_task_execution.kick") as kick:
            self.assertEqual(tasks.complete(self.db, child_run["id"], self.result(child_run))["status"], "delivered")
            kick.assert_called_once()
        retry = tasks.get(self.db, parent["id"])["latest_run"]
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(retry["input_snapshot"]["children"][0]["task_id"], child["id"])

    def test_origin_links_preserve_legacy_ids_and_are_unique(self):
        task = self.create()
        run = tasks.start(self.db, task["id"], authority=self.ui)
        tasks.link_origin(self.db, task["id"], "workflow_node", "old-attempt", run["id"])
        self.assertEqual(tasks.find_origin(self.db, "workflow_node", "old-attempt")["id"], task["id"])
        with self.assertRaises(ValueError):
            tasks.link_origin(self.db, self.create()["id"], "workflow_node", "old-attempt")

    def legacy(self, *, status="delivered", node_id="legacy-node"):
        path = self.root / (node_id + ".md")
        path.write_text("Historical evidence", encoding="utf-8")
        return {"id": node_id, "status": status, "checkpoint": {"reply": "Historical evidence", "input": {"goal": "Historical goal"},
            "output": {"valid": True, "files": [str(path)], "content": "Historical evidence", "file_versions": [{"path": str(path), "sha256": tasks._hash(path)}]}}}

    def test_restore_delivered_versions_uses_legacy_source_and_retains_old_id(self):
        task = self.create()
        node = self.legacy()
        restored = tasks.restore_attempt(self.db, task["id"], node)
        self.assertEqual(restored["status"], "delivered", restored["latest_run"]["error"])
        self.assertEqual(restored["deliveries"][0]["source"], "legacy")
        self.assertEqual(restored["deliveries"][0]["actor_id"], "")
        self.assertTrue(restored["latest_run"]["checkpoint"]["legacy_imported"])
        self.assertEqual(restored["latest_run"]["input_snapshot"]["execution_context"]["upstream_context"]["goal"], "Historical goal")
        self.assertEqual(tasks.find_origin(self.db, "workflow_node", node["id"])["id"], task["id"])
        self.assertEqual(tasks.restore_attempt(self.db, task["id"], node)["latest_run"]["id"], restored["latest_run"]["id"])
        Path(node["checkpoint"]["output"]["files"][0]).write_text("Later edit", encoding="utf-8")
        self.assertEqual(Path(restored["outputs"][0]["path"]).read_text(encoding="utf-8"), "Historical evidence")

    def test_restore_review_uses_canonical_human_decision(self):
        task = self.create()
        restored = tasks.restore_attempt(self.db, task["id"], self.legacy(status="awaiting_review"))
        self.assertEqual(restored["status"], "awaiting_review")
        self.assertEqual(restored["deliveries"], [])
        done = tasks.review(self.db, task["id"], approved=True, authority=self.ui)
        self.assertEqual(done["deliveries"][0]["source"], "human")

    def test_restore_changed_legacy_output_blocks_without_models(self):
        task = self.create()
        node = self.legacy()
        Path(node["checkpoint"]["output"]["files"][0]).write_text("Changed before import", encoding="utf-8")
        with mock.patch.object(__import__("workflow"), "run_agent") as model:
            restored = tasks.restore_attempt(self.db, task["id"], node)
        model.assert_not_called()
        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["deliveries"], [])
        self.assertEqual(restored["latest_run"]["checkpoint"]["output"], node["checkpoint"]["output"])

    def test_restore_interrupted_agent_state_and_explicit_unknown_tool_restart(self):
        task = self.create()
        node = {"id": "legacy-interrupted", "status": "interrupted", "checkpoint": {"agent": {"phase": "tools_ready", "messages": [{"role": "tool", "content": "Saved result"}], "done_tools": {"one": True}}}}
        restored = tasks.restore_attempt(self.db, task["id"], node)
        run = tasks.start(self.db, task["id"], authority=self.ui)
        self.assertEqual(run["id"], restored["latest_run"]["id"])
        self.assertEqual(run["checkpoint"]["done_tools"], {"one": True})
        tasks.claim(self.db, run["id"])
        tasks.save_checkpoint(self.db, run["id"], phase="tool", inflight={"name": "write"})
        tasks.fail(self.db, run["id"], "Unknown outcome")
        with self.assertRaises(ValueError):
            tasks.start(self.db, task["id"], authority=self.ui)
        restarted = tasks.start(self.db, task["id"], authority=self.ui, restart=True)
        self.assertNotEqual(run["id"], restarted["id"])
        self.assertTrue(restarted["checkpoint"]["restart_approved"])

    def test_projection_failure_is_recorded_and_can_be_reconciled(self):
        with mock.patch("task_adapters.project", side_effect=RuntimeError("PROJECTION_FAILURE")):
            task = self.create()
            run = tasks.start(self.db, task["id"], authority=self.ui)
            tasks.complete(self.db, run["id"], self.result(run))
        value = tasks.get(self.db, task["id"])
        self.assertEqual(value["status"], "awaiting_review")
        self.assertTrue(any(e["kind"] == "projection_error" for e in value["events"]))
        tasks._notify(self.db, task["id"], reconcile=True)


if __name__ == "__main__":
    unittest.main()
