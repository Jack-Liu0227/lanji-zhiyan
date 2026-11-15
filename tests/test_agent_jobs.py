"""Durable requests, cancellation during a stalled provider, and replay boundaries."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import agent_jobs
import asset_service
import execution
from run_manager import RunManager
import workflow


class AgentJobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "app.sqlite3"
        self.manager = RunManager()
        replacement = patch.object(RunManager, "_instance", self.manager)
        replacement.start()
        self.addCleanup(replacement.stop)
        with workflow.WorkflowDB(self.path) as db:
            self.project = db.create_task("Jobs", base_dir=str(Path(self.tmp.name) / "project"))

    def wait(self, run):
        deadline = time.monotonic() + 8
        while run.worker_started and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(run.worker_started, "Worker did not settle")

    def request(self, key="test"):
        return {"task_id": self.project, "message": "perform task", "idempotency_key": key}

    def test_idempotent_request_keeps_snapshot_after_template_deletion(self):
        import project_notes
        with workflow.WorkflowDB(self.path) as db:
            template = project_notes.write(db, self.project, "", "FROZEN_TEMPLATE_REQUIREMENT",
                metadata={"category_id":"prompt_templates", "note_type":"prompt_template"})
        entered, release = threading.Event(), threading.Event()
        def model(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"content":"Done"}
        request = {**self.request('template-reconnect'), "template_ref":{"template_id":template['template_id']}}
        with patch.object(workflow, "_chat_raw", side_effect=model):
            run = agent_jobs.start(self.path, request)
            try:
                self.assertTrue(entered.wait(5))
                with workflow.WorkflowDB(self.path) as db:
                    project_notes.delete(db, self.project, template['path'])
                self.assertIs(agent_jobs.start(self.path, request), run)
            finally:
                release.set()
            self.wait(run)
        self.assertEqual(run.status, 'done')

    def test_detached_worker_persists_events_and_same_request_runs_once(self):
        entered, release = threading.Event(), threading.Event()

        def model(*args, **kwargs):
            entered.set()
            release.wait(30)
            kwargs["on_delta"]("Persisted after disconnection")
            return {"content": "Persisted after disconnection"}

        with patch.object(workflow, "_chat_raw", side_effect=model) as call:
            run = agent_jobs.start(self.path, self.request())
            try:
                self.assertTrue(entered.wait(20))
                same = agent_jobs.start(self.path, self.request())
                self.assertIs(same, run)
                release.set()
                self.wait(run)
                self.assertEqual(call.call_count, 1)
            finally:
                release.set()
                self.wait(run)
        with workflow.WorkflowDB(self.path) as db:
            events = db.get_run_events(run.run_id)
            self.assertEqual(events[-1]["kind"], "done")
            self.assertIn("Persisted", events[-1]["payload"]["content"])
            self.assertEqual(db.get_async_task(run.run_id)["status"], "done")
            self.assertEqual(db.get_run_events(run.run_id, events[-2]["event_id"]), [events[-1]])
            restored = RunManager().create_run(self.project, "test", db)
            self.assertEqual(restored.run_id, run.run_id)
            self.assertFalse(restored.claim())

    def test_cancel_stalled_model_is_bounded_and_late_reply_cannot_finish(self):
        entered, release = threading.Event(), threading.Event()

        def model(*args, **kwargs):
            entered.set()
            release.wait(30)
            return {"content": "Too late"}

        with patch.object(workflow, "_chat_raw", side_effect=model):
            run = agent_jobs.start(self.path, self.request())
            try:
                self.assertTrue(entered.wait(20))
                start = time.monotonic()
                with workflow.WorkflowDB(self.path) as db:
                    self.manager.cancel_run(run.run_id, db)
                self.wait(run)
                self.assertLess(time.monotonic() - start, 1.5)
            finally:
                release.set()
                self.wait(run)
        with workflow.WorkflowDB(self.path) as db:
            self.assertEqual(db.get_async_task(run.run_id)["status"], "cancelled")
            self.assertEqual(db.get_background_job(run.run_id)["status"], "cancelled")
            self.assertFalse(any(e["kind"] == "done" for e in db.get_run_events(run.run_id)))

    def test_retry_without_initial_chat_reuses_created_conversation(self):
        request = {"message": "Start new chat", "idempotency_key": "new-chat"}
        with patch.object(workflow, "_chat_raw", return_value={"content": "Started"}) as model:
            first = agent_jobs.start(self.path, request)
            self.wait(first)
            second = agent_jobs.start(self.path, request)
            self.assertIs(first, second)
            self.assertEqual(model.call_count, 1)
        with workflow.WorkflowDB(self.path) as db:
            self.assertEqual(db.get_async_task(first.run_id)["owner_id"], first.task_id)

    def test_error_is_terminal_and_not_overwritten_by_done(self):
        with patch.object(workflow, "_chat_raw", side_effect=RuntimeError("provider failed")):
            run = agent_jobs.start(self.path, self.request())
            self.wait(run)
        with workflow.WorkflowDB(self.path) as db:
            run.emit({"type": "done"}, db)
            self.assertEqual(db.get_async_task(run.run_id)["status"], "failed")
            self.assertEqual(db.get_background_job(run.run_id)["status"], "failed")
            self.assertEqual(db.get_run_events(run.run_id)[-1]["kind"], "error")

    def test_recovery_executes_saved_request_but_does_not_replay_unknown_tool(self):
        with workflow.WorkflowDB(self.path) as db:
            safe = self.manager.create_run(self.project, "safe", db)
            unsafe = self.manager.create_run(self.project, "unsafe", db)
            for run, phase in [(safe, "model"), (unsafe, "tool")]:
                db.update_async_task(run.run_id, status="running", checkpoint={
                    "request": self.request(run.idempotency_key), "step": "", "phase": phase})
        with patch.object(workflow, "_chat_raw", return_value={"content": "Recovered"}) as model:
            result = agent_jobs.recover(self.path)
            self.wait(safe)
            self.assertEqual(model.call_count, 1)
        self.assertEqual(result["resumed"], [safe.run_id])
        self.assertEqual(result["interrupted"], [unsafe.run_id])
        with self.assertRaisesRegex(ValueError, "工具执行中"):
            agent_jobs.start(self.path, {}, resume_id=unsafe.run_id)

    def test_model_boundary_reuses_tool_results_without_executing_again(self):
        import hashlib
        with workflow.WorkflowDB(self.path) as db:
            run = self.manager.create_run(self.project, "checkpoint", db)
            request = self.request("checkpoint")
            signature = hashlib.sha256(json.dumps([(self.project, request["message"]), ""]).encode()).hexdigest()
            db.update_async_task(run.run_id, status="running", checkpoint={
                "request": request, "step": "", "phase": "model", "signature": signature, "turn": 1,
                "messages": [{"role": "user", "content": "perform task"},
                             {"role": "assistant", "tool_calls": [{"id": "call", "type": "function", "function": {"name": "calendar_add", "arguments": "{}"}}]},
                             {"role": "tool", "tool_call_id": "call", "content": "Already created calendar item"}]})
        with patch.object(workflow, "_chat_raw", return_value={"content": "Used saved result"}) as model, \
                patch.object(workflow, "_tool_impl") as tool:
            agent_jobs.recover(self.path)
            self.wait(run)
            tool.assert_not_called()
            self.assertTrue(any(m.get("role") == "tool" and "Already created" in m.get("content", "")
                                for m in model.call_args.args[1]))

    def test_agent_outputs_have_run_provenance_and_immutable_revisions(self):
        calls = [{"id": "write", "type": "function", "function": {
            "name": "doc_write", "arguments": json.dumps({"markdown": "First version"})}}]
        with workflow.WorkflowDB(self.path) as db, patch.object(workflow, "chat_raw", side_effect=[
            {"tool_calls": calls}, {"content": "Written"}]):
            out = workflow.run_agent(db, self.project, "write", profile={"permission": "workspace_write"},
                                     scope="outline", tools=workflow.doc_tools("outline"))
            node = db.db.execute("SELECT output_assets FROM workflow_nodes WHERE run_id=?", (out["run_id"],)).fetchone()
            assets = json.loads(node["output_assets"])
            self.assertEqual(len(assets), 1)
            revision = db.db.execute("SELECT * FROM asset_revisions WHERE asset_id=?", (assets[0],)).fetchone()
            workflow.doc_write(db, self.project, "outline", "Second version")
            self.assertIn("First version", Path(revision["content_uri"]).read_text(encoding="utf-8"))
            self.assertTrue(db.db.execute("SELECT 1 FROM asset_provenance WHERE run_id=?", (out["run_id"],)).fetchone())
