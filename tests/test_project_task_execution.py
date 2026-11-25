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
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="rw-project-worker-")
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

    def test_contract_failure_gets_one_same_attempt_repair_with_saved_tools(self):
        task, run = self.queue(output_contract={"required_files": ["outputs/report.md"]})
        calls = []

        def model(db, project_id, prompt, **kwargs):
            calls.append((prompt, kwargs))
            if len(calls) == 1:
                db.save_agent_checkpoint(done_tools={"paper_focus\x00{}": {"paper_id": "saved-paper"}})
                return {"reply": "Evidence collection is complete."}
            self.assertTrue(kwargs.get("resume_saved_tools"))
            self.assertIn("paper_focus\x00{}", db.job_checkpoint["done_tools"])
            saved = workflow._tool_impl("project_artifact_write", db, project_id, {
                "path": "outputs/report.md", "content": "# Evidence report\nVerified current-attempt artifact."})
            return {"reply": "The required artifact is now saved.", "output_assets": saved["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=model):
            done = worker.execute(self.db, run["id"])

        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        self.assertEqual(len(calls), 2)
        self.assertIn("outputs/report.md", calls[1][1]["extra_context"])
        file = next(item for item in done["outputs"] if item["relative"] == "outputs/report.md")
        metadata = __import__("json").loads(
            self.db.db.execute("SELECT metadata FROM assets WHERE id=?", (file["asset_id"],)).fetchone()[0])
        self.assertEqual(metadata["task_run_id"], run["id"])

    def test_saved_output_repair_can_finish_after_more_than_eight_model_rounds(self):
        import hashlib
        import json
        self.db.add_model('Repair fixture', 'http://127.0.0.1:1/v1', 'fixture', 'test-key', activate=True)
        task, first = self.queue(output_contract={"required_files": ["outputs/report.md"]})
        tasks.claim(self.db, first['id'])
        prompt = task['goal']
        scope = 'task:' + task['id']
        repair_prompt = prompt + '\n\nRepair the previous output using the validation feedback in the current context.'
        def signature(text):
            return hashlib.sha256(json.dumps([[self.pid, text], scope], default=str).encode()).hexdigest()
        tasks.save_checkpoint(self.db, first['id'], phase='model', turn=8,
            signature=signature(repair_prompt),
            completed_calls={signature(prompt): {'reply': 'No artifact was saved.', 'events': []}},
            messages=[{'role': 'system', 'content': 'Finish the current saved repair.'},
                      {'role': 'user', 'content': repair_prompt}])
        tasks.fail(self.db, first['id'], 'Repair was interrupted')
        resumed = tasks.start(self.db, task['id'], authority=self.ui)
        rounds = []

        def model(_profile, _messages, **_kwargs):
            rounds.append(len(rounds))
            if len(rounds) <= 9:
                name, args = 'library_overview', {}
            elif len(rounds) == 10:
                name, args = 'project_artifact_write', {
                    'path': 'outputs/report.md', 'content': '# Completed evidence report\nSaved after continuing the repair.'}
            else:
                return {'content': 'The evidence report is saved.', 'tool_calls': []}
            return {'content': '', 'tool_calls': [{'id': 'call-' + str(len(rounds)), 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]}

        with patch.object(workflow, 'chat_raw', side_effect=model):
            done = worker.execute(self.db, resumed['id'])

        self.assertEqual(done['status'], 'awaiting_review', done['latest_run']['error'])
        self.assertEqual((self.root / 'outputs/report.md').read_text(encoding='utf-8'),
                         '# Completed evidence report\nSaved after continuing the repair.')

    def test_evidence_tasks_hide_duplicate_excerpt_and_unregistered_evidence_read_tools(self):
        names = ["paper_abstracts", "paper_focus", "paper_excerpt", "paper_evidence_get",
                 "paper_evidence_record", "project_artifact_write", "workflow_context_read"]
        specs = [{"function": {"name": name}} for name in names]
        task = {"project_id": self.pid, "workflow_run_id": "wrun-test", "scope": {"tools": names},
                "metadata": {"stage": "task"}, "output_contract": {"requires_evidence": True}}
        run = {"executor_snapshot": {"assistant_id": "worker-writer", "tools": names}}
        with patch("workflow_execution._tools", return_value=specs), \
                patch("team_orchestration.tools", side_effect=lambda _task, available: available):
            available, _ = worker._tools(self.db, task, run, {"tools": names})
        available_names = {spec["function"]["name"] for spec in available}
        self.assertTrue({"paper_focus", "paper_evidence_record", "project_artifact_write"}.issubset(available_names))
        self.assertNotIn("paper_excerpt", available_names)
        self.assertNotIn("paper_evidence_get", available_names)

    def test_focus_excerpt_id_registers_exact_evidence_without_copying_offsets(self):
        from tests._pdf_fixture import write_text_pdf
        page = "Methods\nThe coating was applied at 700 C before cell assembly."
        source = write_text_pdf(self.root / "coating.pdf", [page])
        paper_id = self.db.ingest_pdf(self.pid, source)["id"]
        task, run = self.queue(output_contract={
            "required_files": ["outputs/evidence.md"], "requires_evidence": True,
        })

        def execute(db, project_id, *_args, **_kwargs):
            focused = workflow._tool_impl("paper_focus", db, project_id, {
                "question": "What temperature is explicitly reported?",
                "paper_ids": [paper_id], "keywords": ["700 C"],
            })
            match = focused["matches"][0]
            db.save_agent_checkpoint(done_tools={"paper_focus\0focus-fixture": focused})
            recorded = workflow._tool_impl("paper_evidence_record", db, project_id, {
                "paper_id": paper_id,
                "items": [{"excerpt_id": match["excerpt_id"], "field": "process temperature", "value": "700 C"}],
            })
            self.assertEqual(recorded["rejected"], [])
            db.save_agent_checkpoint(done_tools={"paper_evidence_record\0record-fixture": recorded})
            saved = workflow._tool_impl("project_artifact_write", db, project_id, {
                "path": "outputs/evidence.md", "content": "# Verified source\nThe coating temperature is 700 C.",
            })
            return {"reply": "Evidence saved", "output_assets": saved["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=execute):
            done = worker.execute(self.db, run["id"])

        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        evidence = self.db.evidence(self.pid)
        self.assertEqual(len(evidence), 1)
        indexed_page = self.db.paper(paper_id)["pages"][0]["text"]
        self.assertEqual(evidence[0]["excerpt"], " ".join(indexed_page.split()))
        artifact = self.root / "outputs/evidence.md"
        self.assertIn(evidence[0]["id"], artifact.read_text(encoding="utf-8"))

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

    def test_model_turn_budget_preserves_checkpoint_and_can_resume_to_real_output(self):
        task, run = self.queue(scope={"tools": ["library_overview"]})
        response = {"content": "", "tool_calls": [{"id": "inspect-library", "type": "function",
                    "function": {"name": "library_overview", "arguments": "{}"}}]}
        with patch.object(workflow, "chat_raw", return_value=response) as model:
            stopped = worker.execute(self.db, run["id"])
        self.assertEqual(stopped["status"], "failed")
        self.assertIn("工具调用上限", stopped["latest_run"]["error"])
        self.assertEqual(model.call_count, worker.MAX_AGENT_STEPS)
        saved = stopped["latest_run"]["checkpoint"]
        self.assertEqual(saved["phase"], "model")
        self.assertEqual(len(saved["done_tools"]), 1)
        self.assertEqual(saved["turn"], worker.MAX_AGENT_STEPS)

        resumed = tasks.start(self.db, task["id"], authority=self.ui)
        with patch.object(workflow, "chat_raw", return_value={"content": "Resumed evidence report."}):
            done = worker.execute(self.db, resumed["id"])
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])
        self.assertEqual(Path(done["outputs"][0]["path"]).read_text(encoding="utf-8"),
                         "Resumed evidence report.")

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

    def test_previous_attempt_files_carry_over_only_when_disk_matches_the_registered_hash(self):
        import json
        task, first = self.queue(output_contract={"required_files": ["outputs/a.md", "outputs/b.md"]})
        tasks.claim(self.db, first["id"])
        for rel, body in (("outputs/a.md", "# Draft A"), ("outputs/b.md", "# Draft B")):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            import asset_service
            asset_service.register_file(
                self.db, target, project_id=self.pid, kind="task_output", title=rel,
                source="workflow_artifact", body=body,
                metadata={"project_task_id": task["id"], "task_run_id": first["id"]},
            )
        tasks.fail(self.db, first["id"], "Retry all files")

        carried = tasks.start(self.db, task["id"], authority=self.ui)
        tasks.claim(self.db, carried["id"])
        output = worker._outputs(self.db, task, carried, {"reply": "reused"}, None)
        self.assertEqual({item["relative"] for item in output["files"]},
                         {"outputs/a.md", "outputs/b.md", ".tasks/" + task["id"] + "/" + carried["id"] + "/outputs/result.md"})

        tasks.fail(self.db, carried["id"], "Retry after external edit")
        (self.root / "outputs/b.md").write_text("# Draft B edited outside the workflow", encoding="utf-8")
        edited = tasks.start(self.db, task["id"], authority=self.ui)
        tasks.claim(self.db, edited["id"])
        with self.assertRaises(ValueError) as ctx:
            worker._outputs(self.db, task, edited, {"reply": "reused"}, None)
        self.assertIn("outputs/b.md", str(ctx.exception))
        self.assertNotIn("outputs/a.md", str(ctx.exception))
        self.assertIn("outputs/b.md", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
