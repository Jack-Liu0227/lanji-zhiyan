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
import project_tasks
import project_task_execution


class WorkflowExecutionTest(unittest.TestCase):
    default_executor = "builtin-steward"
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

    def test_execution_listing_includes_the_run_layout_identity(self):
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {
            "name": "Layout identity",
            "nodes": [{"id": "search-node", "step": "search", "assistant_id": self.default_executor}],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        run = engine.create(self.db, self.pid, "Find relevant papers")

        listing = engine.route("GET", "/api/workflow/execution", {}, {"project_id": [self.pid]}, self.db)

        self.assertEqual(listing["runs"][0]["id"], run["id"])
        self.assertEqual(listing["runs"][0]["layout_id"], layout["id"])
        self.assertEqual(listing["runs"][0]["node_order"], ["search-node"])

    def test_agent_prepare_freezes_managed_paths_and_review_mode_before_start(self):
        self.layout([{"step": "search", "review_required": False,
                      "output_contract": {"required_files": ["candidate_papers.csv"]}},
                     {"step": "notes", "review_required": False}])
        result = workflow._tool_impl("workflow_prepare", self.db, self.pid, {
            "goal": "Review the literature",
            "template_id": workflow_layout.state(self.db, self.pid)["layout"]["id"],
            "request_key": "prepared-managed-review",
            "managed_artifacts": True,
            "review_mode": "human_review",
        })
        run = result["run"]
        self.assertEqual(run["status"], "queued")
        self.assertTrue(run["settings"]["prepared"])
        self.assertEqual(run["settings"]["review_mode"], "human_review")
        self.assertTrue(run["settings"]["managed_artifacts"])
        import project_tasks
        node_id = run["layout"]["nodes"][0]["id"]
        task = project_tasks.get(self.db, run["settings"]["task_ids"][node_id])
        self.assertEqual(task["output_contract"]["required_files"], [
            f"研究过程/{run['id']}/search/candidate_papers.csv"])

    def test_project_workflow_text_instructions_are_not_treated_as_file_inputs(self):
        self.layout([{"step": "notes", "review_required": False, "prompt": "Produce the requested handoff."}])
        source = Path(self.db.base_dir(self.pid)) / "materials" / "source.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("Source material.", encoding="utf-8")
        prepared = workflow._tool_impl("workflow_prepare", self.db, self.pid, {
            "goal": "Complete the evidence review",
            "template_id": workflow_layout.state(self.db, self.pid)["layout"]["id"],
            "request_key": "project-workspace-text-guidance",
            "inputs": [
                {"type": "text", "content": "Preserve existing deliveries and cite every source."},
                {"relative": "materials/source.md"},
            ],
        })["run"]

        task_id = prepared["settings"]["task_ids"]["notes"]
        task = project_tasks.get(self.db, task_id)
        self.assertEqual(task["inputs"], [{"relative": "materials/source.md"}])
        self.assertIn("Preserve existing deliveries and cite every source.", task["goal"])

        def complete_without_model(db, task_run_id, _cancel_check):
            task_run = project_tasks.get_run(db, task_run_id)
            task = project_tasks.get(db, task_run["task_id"])
            output = project_task_execution._outputs(
                db, task, task_run, {"reply": "Evidence handoff saved."}, None)
            project_tasks.complete(db, task_run_id, {
                "reply": "Evidence handoff saved.",
                "output_snapshot": output,
            })

        with patch.object(project_task_execution, "execute", side_effect=complete_without_model):
            completed = engine.advance(self.db, prepared["id"])

        self.assertEqual(completed["status"], "succeeded", completed)
        latest_task = project_tasks.get(self.db, task_id)
        self.assertEqual(latest_task["latest_run"]["input_snapshot"]["assets"][0]["relative"],
                         "materials/source.md")

    def test_resume_repairs_saved_text_guidance_before_retrying_blocked_node(self):
        self.layout([{"step": "notes", "review_required": False, "prompt": "Produce the requested handoff."}])
        prepared = workflow._tool_impl("workflow_prepare", self.db, self.pid, {
            "goal": "Complete the evidence review",
            "template_id": workflow_layout.state(self.db, self.pid)["layout"]["id"],
            "request_key": "project-workspace-resume-guidance",
        })["run"]
        task_id = prepared["settings"]["task_ids"]["notes"]
        legacy_text = {"type": "text", "content": "Preserve the accepted source files during retry."}
        settings = {**prepared["settings"], "task_inputs": [legacy_text], "dag_errors": {
            "notes": "输入须引用项目资产或相对文件"}}
        self.db.db.execute("UPDATE workflow_executions SET settings=? WHERE run_id=?",
                           (json.dumps(settings, ensure_ascii=False), prepared["id"]))
        self.db.db.execute("UPDATE workflow_runs SET status='blocked',error=? WHERE id=?",
                           ("输入须引用项目资产或相对文件", prepared["id"]))
        self.db.db.execute("UPDATE project_tasks SET status='blocked',inputs=? WHERE id=?",
                           (json.dumps([legacy_text], ensure_ascii=False), task_id))
        self.db.db.commit()

        def complete_without_model(db, task_run_id, _cancel_check):
            task_run = project_tasks.get_run(db, task_run_id)
            task = project_tasks.get(db, task_run["task_id"])
            output = project_task_execution._outputs(
                db, task, task_run, {"reply": "Evidence handoff saved."}, None)
            project_tasks.complete(db, task_run_id, {
                "reply": "Evidence handoff saved.",
                "output_snapshot": output,
            })

        with patch.object(project_task_execution, "execute", side_effect=complete_without_model), \
                patch.object(engine, "kick", side_effect=lambda _path, run_id: engine.advance(self.db, run_id)):
            resumed = engine.route("POST", "/api/workflow/execution/resume", {
                "id": prepared["id"], "request_key": "retry-text-guidance"}, {}, self.db)

        task = project_tasks.get(self.db, task_id)
        self.assertEqual(resumed["run"]["status"], "succeeded", resumed["run"])
        self.assertEqual(task["inputs"], [])
        self.assertIn("Preserve the accepted source files during retry.", task["goal"])

    def test_workflow_node_agent_has_a_bounded_model_turn_budget(self):
        self.layout([{"step": "outline", "review_required": False}])
        run = engine.create(self.db, self.pid, "Prepare a source-grounded outline")
        observed = {}

        def write_outline(db, project_id, prompt, **kwargs):
            observed.update(kwargs)
            workflow.doc_write(db, project_id, "outline", "# Outline\nVerified outline content")
            return {"reply": "Outline saved."}

        with patch.object(workflow, "run_agent", side_effect=write_outline):
            completed = engine.advance(self.db, run["id"])

        self.assertEqual(completed["status"], "succeeded", completed)
        self.assertEqual(observed["max_steps"], 64)

    def test_evidence_node_without_registration_tool_is_rejected_before_run_creation(self):
        self.layout([{"step": "notes", "review_required": True,
                      "tools": ["paper_focus", "paper_evidence_get", "project_artifact_write"],
                      "output_contract": {"required_files": ["evidence_analysis.md"],
                                          "requires_evidence": True}}])
        with self.assertRaisesRegex(ValueError, "paper_evidence_record"):
            workflow._tool_impl("workflow_prepare", self.db, self.pid, {
                "goal": "Extract full-text evidence", "request_key": "missing-evidence-registration"})
        self.assertIsNone(engine.latest(self.db, self.pid))

    def test_evidence_node_can_reuse_registered_evidence_within_its_allowlist(self):
        names = ["paper_focus", "paper_excerpt", "paper_evidence_record", "paper_evidence_get", "workflow_context_read"]
        task = {"project_id": self.pid, "workflow_run_id": "flow", "metadata": {},
                "scope": {"tools": names}, "output_contract": {"requires_evidence": True}}
        run = {"executor_snapshot": {"assistant_id": self.default_executor, "tools": None}}
        specs, _ = project_task_execution._tools(self.db, task, run, {"tools": names})
        visible = {item["function"]["name"] for item in specs}
        self.assertIn("paper_evidence_get", visible)
        self.assertIn("paper_excerpt", visible)
        self.assertIn("paper_evidence_record", visible)
        task["scope"]["tools"] = [name for name in names if name not in {"paper_evidence_get", "paper_excerpt"}]
        restricted, _ = project_task_execution._tools(self.db, task, run, {"tools": names})
        restricted_names = {item["function"]["name"] for item in restricted}
        self.assertNotIn("paper_evidence_get", restricted_names)
        self.assertNotIn("paper_excerpt", restricted_names)

    def test_execution_prepare_route_accepts_managed_artifact_setting(self):
        self.layout([{"step": "search", "review_required": False,
                      "output_contract": {"required_files": ["candidate_papers.csv"]}},
                     {"step": "notes", "review_required": False}])
        layout_id = workflow_layout.state(self.db, self.pid)["layout"]["id"]
        response = engine.route("POST", "/api/workflow/execution/prepare", {
            "project_id": self.pid, "template_id": layout_id, "goal": "Review the literature",
            "idempotency_key": "api-prepared-managed-review", "managed_artifacts": True,
            "review_mode": "human_review",
        }, {}, self.db)
        run = response["run"]
        self.assertEqual(run["settings"]["review_mode"], "human_review")
        self.assertTrue(run["settings"]["managed_artifacts"])

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

    def test_claim_audit_blocks_automatic_delivery_and_rework_uses_new_manuscript(self):
        import project_tasks
        import workflow_node_workbench as bench

        self.layout([{"step": "outline", "review_required": False}, {"step": "write", "review_required": False},
                     {"step": "review", "review_required": False, "claim_audit": True}])
        self.db.add_model("Fixture", "http://127.0.0.1:1/v1", "fixture", "", activate=True)
        from _pdf_fixture import write_text_pdf
        source = "After 100 cycles retention was 89% under the baseline protocol."
        pdf = write_text_pdf(self.db.pdir(self.pid, "文献库") / "source.pdf", [source])
        paper = self.db.ingest_pdf(self.pid, pdf)
        evidence_input = {"paper_id": paper["id"], "items": [{"page_no": 1, "field": "retention",
                          "value": "89%", "excerpt": source}, {"page_no": 2, "field": "retention",
                          "value": "99%", "excerpt": "This text does not exist in the PDF"}]}
        evidence_result = workflow._tool_impl("paper_evidence_record", self.db, self.pid, evidence_input)
        self.assertEqual(len(evidence_result["rejected"]), 1)
        evidence = evidence_result["accepted"][0]
        self.assertEqual(workflow._tool_impl("paper_evidence_record", self.db, self.pid, evidence_input)["accepted"], [evidence])
        run = engine.create(self.db, self.pid)
        attempts, feedback = [], []
        original = "After 100 cycles retention was 99% under the baseline protocol."
        def execute(db, pid, prompt, **kwargs):
            stage = kwargs["scope"]
            attempts.append(stage)
            if stage == "write":
                feedback.append(kwargs["extra_context"])
            manuscript = original if attempts.count("write") == 1 else source
            workflow.doc_write(db, pid, stage, manuscript if stage == "write" else "# " + stage)
            return {"reply": stage, "output_assets": []}
        def check_claim(_profile, messages):
            fragment = json.loads(messages[-1]["content"])["manuscript"]
            return {"claims": [{"text": fragment.strip(), "status": "supported", "evidence_ids": [evidence["id"]]}]}
        with patch.object(workflow, "run_agent", side_effect=execute), \
             patch.object(workflow, "chat_json", side_effect=check_claim), \
             patch.object(engine, "kick"):
            first = engine.advance(self.db, run["id"])
            self.assertEqual(first["status"], "awaiting_review", first["error"])
            ids = first["settings"]["task_ids"]
            authority = project_tasks.CommandContext.ui(self.pid)
            selection = bench.Selection(self.pid, ids["review"])
            review = bench.read(self.db, selection, authority=authority)
            claim = review["claim_audit"]["claims"][0]
            self.assertEqual(claim["status"], "partial")
            self.assertTrue(claim["sources"][0]["anchored"])
            with self.assertRaisesRegex(ValueError, "人工复核依据"):
                project_tasks.review(self.db, ids["review"], approved=True, authority=authority)
            prior = project_tasks.get(self.db, ids["write"])
            decision = {"action": "return-to-stage", "identity": review["identity"], "target_node_id": "write",
                        "feedback": "用 89% 更正数值", "request_key": "repair-audit"}
            result = bench.command(self.db, ids["review"], decision, authority=authority)
            self.assertEqual(result["target_task_id"], ids["write"])
            self.assertEqual(bench.command(self.db, ids["review"], decision, authority=authority), result)
            self.assertEqual(project_tasks.get(self.db, ids["outline"])["status"], "delivered")
            reworked = engine.advance(self.db, run["id"])
            self.assertEqual(reworked["status"], "succeeded", reworked["error"])
            self.assertEqual(attempts, ["outline", "write", "review", "write", "review"])
            self.assertIn("用 89% 更正数值", feedback[-1])
            old_file = next(item for item in prior["outputs"] if item["name"] != "result.md")
            self.assertIn("99%", Path(old_file["path"]).read_text(encoding="utf-8"))
            current = bench.read(self.db, selection, authority=authority)
            self.assertEqual(current["claim_audit"]["claims"][0]["status"], "supported")

    def test_review_translation_and_export_use_frozen_manuscripts(self):
        import workflow_converters
        import project_tasks
        self.layout([{"step": stage, "review_required": False}
                     for stage in ("write", "review", "entrans", "convert")])
        self.db.add_model("Fixture", "http://127.0.0.1:1/v1", "fixture", "", activate=True)
        chinese = "# 正文\n\n已验收原稿 MARK_FIRST"
        english = "# Manuscript\n\nAccepted translation MARK_EN"

        def execute(db, pid, prompt, **options):
            stage = options["scope"]
            if stage == "write":
                return workflow.doc_write(db, pid, stage, chinese)
            if stage == "review":
                return workflow._tool_impl("draft_review", db, pid, {}, scope=stage)
            if stage == "entrans":
                return workflow._tool_impl("manuscript_translate_en", db, pid, {}, scope=stage)
            return workflow._tool_impl("format_convert", db, pid, {"format": "md", "name": "accepted"}, scope=stage)

        with patch.object(workflow, "run_agent", side_effect=execute), \
             patch.object(workflow, "chat_raw", return_value={"content": json.dumps({"verdict": "小修", "scores": {}, "issues": [], "missing_evidence": []})}) as review, \
             patch.object(workflow_converters, "en_translate", return_value={"text": english, "chunks": 1, "ms": 1}) as translate:
            run = engine.create(self.db, self.pid)
            run = engine.advance(self.db, run["id"])
            self.assertEqual(run["status"], "succeeded", run["error"])
            self.assertIn("MARK_FIRST", review.call_args.args[1][-1]["content"])
            self.assertIn("MARK_FIRST", translate.call_args.args[1])
        task = project_tasks.get(self.db, run["settings"]["task_ids"]["convert"])
        export = next(file for file in task["outputs"] if file["name"] == "accepted.md")
        self.assertIn("MARK_EN", Path(export["path"]).read_text(encoding="utf-8"))
        workflow.doc_write(self.db, self.pid, "write", "# Working draft\n\nUnaccepted edit")
        self.assertIn("MARK_FIRST", workflow_converters.en_state(self.db, self.pid)["source_text"])

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
