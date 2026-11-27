"""Workflow commands and workers preserve complete source text before budgeting."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_jobs
import project_bindings as bindings
import project_task_execution as worker
import project_tasks as tasks
import workflow
import workflow_execution as engine
import workflow_layout
import workflow_node_workbench as workbench
import workflow_tasks
from workflow_compaction import WorkflowContext


class WorkflowFullSourcesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="rw-full-sources-")
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.project = self.db.create_task("Complete workflow sources", base_dir=str(self.root))
        self.db.assistant_save("full-source-writer", name="Writer", enabled=1)
        self.binding = bindings.register(self.db, self.project, "assistant", "full-source-writer")
        self.db.add_model("Local fixture", "http://127.0.0.1:1/v1", "full-source-fixture", "", activate=True)
        bindings.set_workflow_review_mode(self.db, "human_review")
        self.authority = tasks.CommandContext.ui(self.project)

    def prepare(self, *, nodes=("report",), goal="Review the complete evidence.", inputs=None):
        layout = workflow_layout.save_layout(self.db, {
            "name": "Complete source handoff",
            "nodes": [{"id": node, "step": "task", "label": node,
                       "executor_binding_id": self.binding["id"]} for node in nodes],
            "edges": [{"source": source, "target": target} for source, target in zip(nodes, nodes[1:])],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        return workflow_tasks.prepare(self.db, self.project, goal, inputs=inputs)

    def finish(self, run, reply="Saved complete evidence."):
        with patch.object(workflow, "run_agent", return_value={"reply": reply}):
            result = engine.advance(self.db, run["id"])
        self.assertEqual(result["status"], "awaiting_review", result.get("error"))
        return result

    def result(self, run, node="report"):
        task_id = run["settings"]["task_ids"][node]
        return workbench.read(self.db, workbench.Selection(self.project, task_id), authority=self.authority)

    def command(self, result, action, **body):
        return workbench.command(self.db, result["task"]["id"], {
            "action": action, "identity": result["identity"], "request_key": "full-source-" + action,
            **body,
        }, authority=self.authority)

    def test_prepared_goal_and_text_guidance_keep_their_complete_tails(self):
        goal = "研究目标 " * 6000 + "GOAL_TAIL_MUST_SURVIVE"
        guidance = "来源阅读要求 " * 5000 + "GUIDANCE_TAIL_MUST_SURVIVE"
        run = self.prepare(goal=goal, inputs=[{"type": "text", "content": guidance}])
        restored = engine.detail(self.db, run["id"])
        task = tasks.get(self.db, restored["settings"]["task_ids"]["report"])

        self.assertEqual(restored["settings"]["goal"], goal)
        self.assertEqual(restored["settings"]["task_guidance"], guidance)
        self.assertIn("GOAL_TAIL_MUST_SURVIVE", task["goal"])
        self.assertIn("GUIDANCE_TAIL_MUST_SURVIVE", task["goal"])

    def test_worker_passes_complete_node_input_before_model_budgeting(self):
        goal = "完整节点要求 " * 7000 + "NODE_INPUT_TAIL_MUST_SURVIVE"
        run = self.prepare(goal=goal)
        received = []

        def model(db, project_id, prompt, **kwargs):
            received.append(kwargs["extra_context"])
            return {"reply": "The complete node input was received."}

        with patch.object(workflow, "run_agent", side_effect=model):
            finished = engine.advance(self.db, run["id"])

        self.assertEqual(finished["status"], "awaiting_review", finished.get("error"))
        context = json.loads(received[0])
        self.assertEqual(context["goal"], goal)
        self.assertIn("NODE_INPUT_TAIL_MUST_SURVIVE", context["task_inputs"]["goal"])
        task = tasks.get(self.db, finished["settings"]["task_ids"]["report"])
        self.assertIn("NODE_INPUT_TAIL_MUST_SURVIVE", task["latest_run"]["checkpoint"]["input"]["goal"])

    def test_large_accepted_upstream_text_remains_readable_and_version_checked(self):
        run = self.prepare(nodes=("source", "review"))
        source_id = run["settings"]["task_ids"]["source"]
        path = self.root / "evidence.md"
        content = "SOURCE_START\n" + "verified source\n" * 150000 + "\nSOURCE_TAIL_MUST_SURVIVE"
        path.write_text(content, encoding="utf-8")
        self.assertGreater(path.stat().st_size, 2 * 1024 * 1024)
        digest = tasks._hash(path)
        dependency = {"task_id": source_id, "delivery_id": "fixed-delivery", "outputs": [{
            "path": str(path), "name": "evidence.md", "content_hash": digest}]}

        accepted = worker._accepted_upstream(self.db, self.project, [dependency])

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["delivery_id"], "fixed-delivery")
        output = accepted[0]["outputs"][0]
        self.assertEqual(output["content"], content)
        self.assertEqual(output["total_chars"], len(content))
        self.assertEqual(output["content_hash"], digest)
        self.assertEqual(output["read_with"]["node_id"], "source")
        self.assertEqual(output["read_with"]["artifact"], "evidence.md")
        self.assertEqual(output["read_with"]["section"], "artifact")
        path.write_text(content + "changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Accepted upstream output changed"):
            worker._accepted_upstream(self.db, self.project, [dependency])

    def test_return_to_predecessor_preserves_feedback_and_idempotency(self):
        run = self.finish(self.prepare(nodes=("source", "review")))
        source_id = run["settings"]["task_ids"]["source"]
        tasks.review(self.db, source_id, approved=True, authority=self.authority)
        run = self.finish(run)
        review = self.result(run, "review")
        feedback = "逐条核对来源并补充原文 " * 3000 + "REWORK_FEEDBACK_TAIL_MUST_SURVIVE"

        with patch.object(engine, "kick"):
            returned = self.command(review, "return-to-stage", target_node_id="source", feedback=feedback)
            repeated = self.command(review, "return-to-stage", target_node_id="source", feedback=feedback)

        source = tasks.get(self.db, source_id)
        rejected = tasks.get(self.db, review["task"]["id"])
        self.assertEqual(returned["target_task_id"], source_id)
        self.assertEqual(repeated, returned)
        self.assertEqual(source["metadata"]["_return_feedback"], feedback)
        self.assertEqual(rejected["deliveries"][-1]["feedback"], feedback)
        self.assertEqual(source["status"], "ready")

    def test_discussion_keeps_large_fixed_output_selection_and_user_message(self):
        content = "SOURCE_START\n" + "Fixed evidence statement. " * 6000 + "\nFIXED_OUTPUT_TAIL_MUST_SURVIVE"
        result = self.result(self.finish(self.prepare(), reply=content))
        message = "请逐条核查 " * 5000 + "DISCUSSION_QUESTION_TAIL_MUST_SURVIVE"
        selection = content[1000:34000]

        with patch.object(agent_jobs, "start") as start:
            start.return_value.run_id = "discussion-fixture"
            response = self.command(result, "discuss", message=message,
                                    selection={"artifact_id": "file:0", "text": selection})

        supplied = start.call_args.args[1]
        encoded = supplied["message"].split("\n", 1)[1].split("\n用户问题：", 1)[0]
        context = json.loads(encoded)
        self.assertEqual(context["outputs"]["content"], content)
        self.assertEqual(context["selection"]["text"], selection)
        self.assertTrue(supplied["message"].endswith(message))
        self.assertTrue(supplied["_node_discussion"])
        self.assertEqual(response["run_id"], "discussion-fixture")
        self.assertEqual(tasks.get(self.db, result["task"]["id"])["status"], "awaiting_review")
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.command(result, "discuss", message="Inspect invalid selection",
                         selection={"artifact_id": "file:0", "text": "UNRELATED_SOURCE_TEXT"})

    def test_long_draft_feedback_and_instructions_keep_type_validation(self):
        result = self.result(self.finish(self.prepare()))
        feedback = "检查意见 " * 5000 + "DRAFT_FEEDBACK_TAIL"
        instruction = "修订要求 " * 5000 + "DRAFT_INSTRUCTION_TAIL"
        reason = "来源说明 " * 1200 + "DRAFT_REASON_TAIL"
        text = "Complete draft. " * 550000 + "LARGE_DRAFT_TAIL"
        draft = self.command(result, "draft", window_id="full-source-window", artifact_id="file:0",
                             text=text, reason=reason, feedback=feedback,
                             instruction=instruction, expected_draft_version=0)["draft"]

        self.assertEqual(draft["feedback"], feedback)
        self.assertEqual(draft["instruction"], instruction)
        self.assertEqual(draft["reason"], reason)
        self.assertEqual(draft["text"], text)
        restored = workbench.read(self.db, workbench.Selection(self.project, result["task"]["id"]),
                                  authority=self.authority, window_id="full-source-window")
        self.assertEqual(restored["drafts"][0]["feedback"], feedback)
        self.assertEqual(restored["drafts"][0]["text"], text)
        with self.assertRaisesRegex(ValueError, "格式无效"):
            self.command(result, "draft", window_id="invalid-window", artifact_id="file:0",
                         text="Draft", feedback=["Invalid type"], expected_draft_version=0)

    def test_long_review_feedback_retries_compare_the_complete_decision(self):
        result = self.result(self.finish(self.prepare()))
        feedback = "核查来源与数值 " * 4000 + "REVIEW_FEEDBACK_TAIL_MUST_SURVIVE"

        reviewed = self.command(result, "review", approved=False, feedback=feedback)["task"]
        repeated = self.command(result, "review", approved=False, feedback=feedback)["task"]

        self.assertEqual(reviewed["deliveries"][-1]["feedback"], feedback)
        self.assertEqual(repeated["deliveries"], reviewed["deliveries"])
        with self.assertRaisesRegex(ValueError, "另一份核查决定"):
            self.command(result, "review", approved=False, feedback=feedback + "CHANGED_TAIL")

    def test_output_repair_keeps_reply_and_all_tool_result_tails(self):
        task = tasks.create(self.db, {
            "goal": "Save the verified report.", "executor_binding_id": self.binding["id"],
            "output_contract": {"required_files": ["outputs/report.md"]},
        }, authority=self.authority)
        attempt = tasks.start(self.db, task["id"], authority=self.authority)
        previous_reply = "The previous complete analysis. " * 10000 + "REPAIR_REPLY_TAIL_MUST_SURVIVE"
        events = [{"type": "tool_result", "name": "source-" + str(index), "ok": True,
                   "summary": "Source details. " * 400 + "TOOL_RESULT_TAIL_" + str(index)}
                  for index in range(14)]
        received = []

        def model(db, project_id, prompt, **kwargs):
            received.append(kwargs["extra_context"])
            if len(received) == 1:
                return {"reply": previous_reply, "events": events}
            saved = workflow._tool_impl("project_artifact_write", db, project_id, {
                "path": "outputs/report.md", "content": "# Verified report\nComplete evidence saved."})
            return {"reply": "The report is now saved.", "output_assets": saved["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=model):
            result = worker.execute(self.db, attempt["id"])

        self.assertEqual(result["status"], "awaiting_review", result["latest_run"].get("error"))
        self.assertEqual(len(received), 2)
        self.assertIn(previous_reply, received[1])
        for event in events:
            self.assertIn(event["summary"], received[1])
        self.assertEqual((self.root / "outputs/report.md").read_text(encoding="utf-8"),
                         "# Verified report\nComplete evidence saved.")
        source, end = json.JSONDecoder().raw_decode(received[1])
        self.assertEqual(source["repair_sources"]["previous_reply"], previous_reply)
        self.assertEqual(source["repair_sources"]["previous_tool_results"][0]["summary"], events[0]["summary"])
        constraints = received[1][end:]
        self.assertIn("[Output validation failure]", constraints)
        self.assertNotIn("REPAIR_REPLY_TAIL_MUST_SURVIVE", constraints)

        profile = {**self.db.active_model(), "context_window": 65536}
        controller = WorkflowContext(self.db, save_checkpoint=lambda **values: None,
                                     summarize=lambda *args: "Earlier analysis is preserved in the checkpoint.")
        prepared = controller.prepare(profile, [
            {"role": "system", "content": "Save every declared output and verify its actual contents."},
            {"role": "system", "content": "【当前节点输入与前置交付】\n" + received[1]},
            {"role": "user", "content": "Repair the missing report."},
        ], [], 8000)
        supplied = next(message["content"] for message in prepared.messages
                        if message["content"].startswith("【当前节点输入与前置交付】"))
        self.assertTrue(prepared.budget.fits)
        self.assertLess(len(supplied), len(received[1]))
        self.assertTrue(supplied.endswith(constraints))
        self.assertIn("outputs/report.md", supplied)

    def test_large_text_preview_and_selection_preserve_content(self):
        text = "Source evidence. " * 550000 + "LARGE_FILE_TAIL"
        path = self.root / "large-evidence.md"
        path.write_text(text, encoding="utf-8")

        preview = workbench._file_preview(path, "markdown", editable=True)
        selection = workbench._selection({"output_snapshot": {"files": [{"path": str(path)}]}},
                                         {"artifact_id": "file:0", "text": "LARGE_FILE_TAIL"})

        self.assertEqual(preview["text"], text)
        self.assertTrue(preview["editable"])
        self.assertEqual(selection["text"], "LARGE_FILE_TAIL")
        with self.assertRaisesRegex(ValueError, "不属于"):
            workbench._selection({"output_snapshot": {"files": [{"path": str(path)}]}},
                                 {"artifact_id": "file:0", "text": "UNRELATED_LARGE_FILE_TEXT"})


if __name__ == "__main__":
    unittest.main()
