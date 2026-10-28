"""One worker executes generic and stage work with durable real artifacts."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import project_bindings as bindings
import project_task_execution as worker
import project_tasks as tasks
import workflow


class ProjectTaskExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rw-project-worker-")
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.pid = self.db.create_task("Worker project", base_dir=str(self.root))
        self.db.assistant_save("worker-writer", name="Writer", enabled=1)
        self.binding = bindings.register(self.db, self.pid, "assistant", "worker-writer")
        self.ui = tasks.CommandContext.ui(self.pid)

    def queue(self, **fields):
        task = tasks.create(self.db, {"goal": "Analyze evidence", "executor_binding_id": self.binding["id"], **fields}, authority=self.ui)
        return task, tasks.start(self.db, task["id"], authority=self.ui)

    def test_generic_report_is_real_and_context_restores_across_nested_work(self):
        before = object()
        self.db.project_task_context = before
        task, run = self.queue()
        seen = []
        def execute(db, pid, prompt, **kwargs):
            seen.append(kwargs)
            self.assertEqual(pid, self.pid)
            self.assertEqual(db.project_task_context.parent_task_id, task["id"])
            db.save_agent_checkpoint(messages=[{"role": "assistant", "content": "checkpoint"}])
            child, child_run = self.queue()
            with patch.object(workflow, "run_agent", return_value={"reply": "Nested report"}):
                self.assertEqual(worker.execute(db, child_run["id"])["status"], "awaiting_review")
            self.assertEqual(db.project_task_context.parent_task_id, task["id"])
            return {"reply": "Evidence report", "run_id": "agent-inner"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
            worker.execute(self.db, run["id"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        self.assertIs(self.db.project_task_context, before)
        self.assertEqual(Path(done["outputs"][0]["path"]).read_text(encoding="utf-8"), "Evidence report")
        checkpoint = done["latest_run"]["checkpoint"]
        self.assertEqual(checkpoint["agent_run_id"], "agent-inner")
        self.assertEqual(checkpoint["messages"][0]["content"], "checkpoint")

    def test_stage_preserves_capture_and_freezes_files_per_attempt(self):
        task, run = self.queue(metadata={"stage": "outline", "node_config": {"tools": ["doc_write"]}, "upstream_context": {"goal": "Original research", "upstream_hash": "upstream-v1"}})
        def execute(db, pid, prompt, **kwargs):
            self.assertEqual(kwargs["tool_allowlist"], ["doc_write"])
            workflow.doc_write(db, pid, "outline", "# Outline\nActual stage content")
            return {"reply": "Stage reply"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        capture = done["latest_run"]["checkpoint"]["output"]
        self.assertTrue(capture["valid"])
        self.assertTrue(capture["fingerprint"])
        self.assertEqual(done["latest_run"]["checkpoint"]["input"]["upstream_hash"], "upstream-v1")
        self.assertEqual(len(done["outputs"]), 2)
        workflow.doc_write(self.db, self.pid, "outline", "Later independent stage content")
        self.assertIn("Actual stage content", Path(done["outputs"][0]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(tasks.review(self.db, task["id"], approved=True, authority=self.ui)["status"], "delivered")

    def test_empty_output_and_missing_stage_artifact_fail(self):
        for fields, reply in (({}, ""), ({"metadata": {"stage": "outline"}}, "Pretend done")):
            task, run = self.queue(**fields)
            with patch.object(workflow, "run_agent", return_value={"reply": reply}):
                done = worker.execute(self.db, run["id"])
            self.assertEqual(done["status"], "failed")
            self.assertEqual(done["deliveries"], [])
            self.assertTrue(done["latest_run"]["error"])

    def test_required_file_and_json_output_contract_are_enforced(self):
        task, run = self.queue(output_contract={"required_files": ["analysis.txt"]})
        def execute(db, *args, **kwargs):
            (self.root / "analysis.txt").write_text("Detailed analysis", encoding="utf-8")
            return {"reply": "Saved analysis"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        self.assertEqual(done["outputs"][0]["relative"], "analysis.txt")
        self.assertIn("/.tasks/", done["outputs"][0]["path"].replace("\\", "/"))

    def test_empty_allowlist_and_selected_member_never_fall_back(self):
        team = self.db.team_add("Office", "custom", project_id=self.pid)
        self.db.team_member_add(team["id"], "Leader", "leader", assistant_id="worker-writer")
        member = self.db.team_member_add(team["id"], "Exact", "member", assistant_id="worker-writer", prompt="EXACT_MEMBER_RULE")
        grant = bindings.register(self.db, self.pid, "team", team["id"], tools=[])
        task, run = self.queue(executor_binding_id=grant["id"], member_id=member["id"])
        def execute(db, *args, **kwargs):
            self.assertEqual(kwargs["member"]["id"], member["id"])
            self.assertEqual(kwargs["tools"], [])
            self.assertEqual(kwargs["tool_allowlist"], [])
            self.assertIn("EXACT_MEMBER_RULE", kwargs["extra_context"])
            return {"reply": "Exact member report"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])

    def test_queued_model_config_is_frozen_and_nested_model_restores(self):
        first = self.db.add_model("First", "http://127.0.0.1:1/v1", "first-model")
        selected = self.db.add_model("Selected", "http://127.0.0.1:2/v1", "selected-model")
        self.db.set_active(first["id"])
        self.db.assistant_save("worker-writer", name="Writer", model_id=selected["id"], enabled=1)
        task, run = self.queue()
        self.db.assistant_save("worker-writer", name="Writer", model_id=first["id"], enabled=1)
        def execute(db, *args, **kwargs):
            self.assertEqual(db.active_model()["id"], selected["id"])
            return {"reply": "Selected model report"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        self.assertEqual(self.db.active_model()["id"], first["id"])
        task, run = self.queue()
        self.db.assistant_save("worker-writer", name="Writer", model_id=selected["id"], enabled=1)
        def model(profile, *args, **kwargs):
            self.assertEqual(profile["id"], first["id"])
            return {"content": "Actual frozen profile report"}
        with patch.object(workflow, "_chat_raw", side_effect=model):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])

    def test_cancel_during_agent_never_accepts_late_output(self):
        task, run = self.queue()
        def execute(db, *args, **kwargs):
            tasks.cancel(db, task["id"], authority=self.ui)
            return {"reply": "Too late"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])
        self.assertEqual(done["status"], "cancelled")
        self.assertEqual(done["deliveries"], [])
        self.assertFalse(hasattr(self.db, "project_task_context"))

    def test_recovery_preserves_attempt_and_agent_checkpoint(self):
        task, run = self.queue()
        tasks.claim(self.db, run["id"])
        tasks.save_checkpoint(self.db, run["id"], messages=[{"role": "user", "content": "Resume marker"}], template_snapshot={"id": "template-fixed"})
        worker.recover(self.db.path)
        interrupted = tasks.get(self.db, task["id"])
        self.assertEqual(interrupted["latest_run"]["status"], "interrupted")
        resumed = tasks.start(self.db, task["id"], authority=self.ui)
        self.assertEqual(resumed["id"], run["id"])
        def execute(db, *args, **kwargs):
            self.assertTrue(db.resuming)
            self.assertEqual(db.job_checkpoint["messages"][0]["content"], "Resume marker")
            self.assertEqual(db.template_snapshot["id"], "template-fixed")
            return {"reply": "Recovered report"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            self.assertEqual(worker.execute(self.db, run["id"])["status"], "awaiting_review")

    def test_model_io_cancellation_callback_is_safe_on_another_thread(self):
        import execution
        task, run = self.queue()
        def execute(db, *args, **kwargs):
            owner = threading.get_ident()
            def model_io():
                self.assertNotEqual(threading.get_ident(), owner)
                self.assertFalse(kwargs["cancel_check"]())
                return "Threaded model report"
            return {"reply": execution.interruptible(model_io)}
        with patch.object(workflow, "run_agent", side_effect=execute):
            self.assertEqual(worker.execute(self.db, run["id"])["status"], "awaiting_review")

    def test_recovery_never_interrupts_an_active_worker(self):
        task, run = self.queue()
        tasks.claim(self.db, run["id"])
        key = (str(self.db.path.resolve()), run["id"])
        with patch.dict(worker._workers, {key: threading.Event()}):
            worker.recover(self.db.path)
        self.assertEqual(tasks.get_run(self.db, run["id"])["status"], "running")

    def test_recovery_exposes_unclaimed_queued_attempt_for_explicit_resume(self):
        task, run = self.queue()
        with patch.object(workflow, "run_agent") as model:
            worker.recover(self.db.path)
        model.assert_not_called()
        restored = tasks.get(self.db, task["id"])
        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["latest_run"]["status"], "interrupted")
        self.assertEqual(tasks.start(self.db, task["id"], authority=self.ui)["id"], run["id"])

    def test_queued_upstream_context_and_node_prompt_are_frozen(self):
        import json
        task, run = self.queue(metadata={"node_config": {"prompt": "ORIGINAL_PROMPT"}, "upstream_context": {"goal": "ORIGINAL_GOAL"}, "layout_snapshot": {"nodes": [{"prompt": "FUTURE_NODE_INSTRUCTION"}]}})
        self.db.db.execute("UPDATE project_tasks SET metadata=? WHERE id=?", (json.dumps({"node_config": {"prompt": "MUTATED_PROMPT"}, "upstream_context": {"goal": "MUTATED_GOAL"}}), task["id"]))
        self.db.db.commit()
        def execute(db, pid, prompt, **kwargs):
            self.assertEqual(prompt, "ORIGINAL_PROMPT")
            self.assertIn("ORIGINAL_GOAL", kwargs["extra_context"])
            self.assertNotIn("MUTATED_GOAL", kwargs["extra_context"])
            self.assertNotIn("FUTURE_NODE_INSTRUCTION", kwargs["extra_context"])
            return {"reply": "Frozen context report"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            self.assertEqual(worker.execute(self.db, run["id"])["status"], "awaiting_review")

    def test_revoked_tool_permission_blocks_queued_attempt(self):
        self.db.assistant_save("worker-writer", name="Writer", permission="full_access", enabled=1)
        task, run = self.queue()
        self.db.assistant_save("worker-writer", name="Writer", permission="read_only", enabled=1)
        with patch.object(workflow, "run_agent") as model:
            done = worker.execute(self.db, run["id"])
        model.assert_not_called()
        self.assertEqual(done["status"], "failed")

    def test_model_retry_after_successful_tool_resumes_without_repeating_write(self):
        import json
        task, run = self.queue(metadata={"stage": "outline", "node_config": {"tools": ["doc_write"]}})
        call = {"id": "write-once", "type": "function", "function": {"name": "doc_write", "arguments": json.dumps({"markdown": "# Written once\nDurable evidence"})}}
        with patch.object(workflow, "_chat_raw", side_effect=[{"tool_calls": [call]}, RuntimeError("Temporary model outage")]), patch.object(workflow, "_tool_impl", wraps=workflow._tool_impl) as tool:
            failed = worker.execute(self.db, run["id"])
        self.assertEqual(failed["status"], "failed", failed["latest_run"]["error"])
        self.assertEqual(tool.call_count, 1)
        retry = tasks.start(self.db, task["id"], authority=self.ui)
        self.assertNotEqual(retry["id"], run["id"])
        with patch.object(workflow, "_chat_raw", return_value={"content": "Used saved artifact"}) as model, patch.object(workflow, "_tool_impl", wraps=workflow._tool_impl) as tool:
            done = worker.execute(self.db, retry["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        tool.assert_not_called()
        self.assertTrue(any(m.get("role") == "tool" for m in model.call_args.args[1]))


if __name__ == "__main__":
    unittest.main()
