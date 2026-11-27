"""Actual workers validate saved files after a model explicitly submits them."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import project_bindings
import project_artifacts
import project_task_execution as worker
import project_tasks as tasks
import service_settings
import workflow
import workflow_execution
import workflow_layout
from tests import test_project_artifacts as artifact_fixtures


class WorkflowOutputSubmissionTest(unittest.TestCase):
    def setUp(self):
        artifact_fixtures.ProjectArtifactTest.setUp(self)
        model = self.db.add_model("Submission fixture", "http://127.0.0.1:1/v1",
                                  "submission-fixture", "fixture", activate=True)
        service_settings.write(self.db, "model_context", {
            "context_window": 262144, "max_output": 16000,
        }, owner=model["id"])
        project_bindings.set_workflow_review_mode(self.db, "human_review")

    def start_node(self, contract):
        layout = workflow_layout.save_layout(self.db, {
            "name": "Explicit file submission",
            "nodes": [{"id": "report", "step": "task", "assistant_id": "builtin-steward",
                       "executor_binding_id": self.binding["id"], "tools": ["project_artifact_write"],
                       "output_contract": contract}], "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        flow = workflow_execution.create(self.db, self.project, "Save and submit the declared report.")
        task_id = flow["settings"]["task_ids"]["report"]
        attempt = tasks.start(self.db, task_id, authority=tasks.CommandContext.ui(self.project))
        return task_id, attempt["id"]

    def call(self, name, arguments, call_id):
        return {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}

    def reply(self, *calls):
        return {"content": "", "tool_calls": list(calls)}

    def execute_script(self, attempt_id, script):
        pending = iter(script)
        self.observed_events = []
        def model(profile, messages, tools=None, **kwargs):
            self.assertIn("workflow_submit_outputs", {t["function"]["name"] for t in tools or []})
            step = next(pending)
            return step() if callable(step) else step
        with patch.object(workflow, "chat_raw", side_effect=model) as transport, \
                patch.object(self.db, "project_task_emit", self.observed_events.append, create=True):
            result = worker.execute(self.db, attempt_id)
        return result, transport.call_count

    def events(self, attempt_id, name):
        return [event for event in self.observed_events
                if event.get("type") == "tool_result" and event.get("name") == name]

    def test_saved_required_file_and_submit_reach_normal_human_review(self):
        task_id, attempt_id = self.start_node({"required_files": ["outputs/report.md"],
                                              "file_rules": {"outputs/report.md": {"min_chars": 30}}})
        content = "# Report\n\nThe saved report contains the required verified analysis."
        result, requests = self.execute_script(attempt_id, [self.reply(
            self.call("project_artifact_write", {"path": "outputs/report.md", "content": content}, "write"),
            self.call("workflow_submit_outputs", {"summary": "Report submitted for validation."}, "submit"),
        )])
        self.assertEqual(requests, 1)
        self.assertEqual(result["status"], "awaiting_review", tasks.get_run(self.db, attempt_id).get("error"))
        self.assertEqual(tasks.get_run(self.db, attempt_id)["status"], "succeeded")
        self.assertEqual(tasks.get(self.db, task_id)["accepted_delivery_id"], "")
        self.assertEqual((Path(self.db.base_dir(self.project)) / "outputs/report.md").read_text(encoding="utf-8"), content)
        self.assertTrue(self.events(attempt_id, "workflow_submit_outputs")[0]["ok"])
        output = tasks.get_run(self.db, attempt_id)["output_snapshot"]
        self.assertIn("outputs/report.md", {item["relative"] for item in output["files"]})

    def test_submit_with_a_missing_required_file_cannot_deliver(self):
        task_id, attempt_id = self.start_node({"required_files": ["outputs/first.md", "outputs/missing.md"]})
        result, requests = self.execute_script(attempt_id, [
            self.reply(self.call("project_artifact_write", {"path": "outputs/first.md", "content": "# First file"}, "write"),
                       self.call("workflow_submit_outputs", {"summary": "Try submitting incomplete files."}, "submit")),
            {"content": "The second file remains missing."},
            {"content": "Repair still has no second file."},
        ])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(tasks.get_run(self.db, attempt_id)["status"], "failed")
        self.assertEqual(tasks.get(self.db, task_id)["accepted_delivery_id"], "")
        self.assertFalse((Path(self.db.base_dir(self.project)) / "outputs/missing.md").exists())
        self.assertFalse(self.events(attempt_id, "workflow_submit_outputs")[0]["ok"])
        self.assertGreaterEqual(requests, 2)

    def test_successful_submit_does_not_bypass_content_validation_or_repair(self):
        task_id, attempt_id = self.start_node({"required_files": ["outputs/report.md"], "min_chars": 500})
        result, requests = self.execute_script(attempt_id, [
            self.reply(self.call("project_artifact_write", {"path": "outputs/report.md", "content": "# Short draft"}, "write"),
                       self.call("workflow_submit_outputs", {"summary": "Submit the saved short draft."}, "submit")),
            self.reply(self.call("workflow_submit_outputs", {"summary": "Repair resubmits the unchanged draft."}, "repair-submit")),
        ])
        self.assertEqual(requests, 2)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(tasks.get_run(self.db, attempt_id)["status"], "failed")
        self.assertEqual(tasks.get(self.db, task_id)["accepted_delivery_id"], "")
        self.assertEqual([event["ok"] for event in self.events(attempt_id, "workflow_submit_outputs")], [True, True])
        self.assertEqual((Path(self.db.base_dir(self.project)) / "outputs/report.md").read_text(encoding="utf-8"), "# Short draft")
        self.assertIn("one repair attempt also failed", tasks.get_run(self.db, attempt_id)["error"])

    def test_normal_resume_can_submit_unchanged_required_file_from_checkpoint_receipt(self):
        task_id, first_run_id = self.start_node({"required_files": ["outputs/report.md"]})
        tasks.claim(self.db, first_run_id)
        self.db.project_task_context = tasks.CommandContext.agent(self.project, task_id)
        content = "# Existing draft\n\nPreviously saved complete work."
        receipt = project_artifacts.write(self.db, self.project, {
            "path": "outputs/report.md", "content": content,
        })
        tasks.save_checkpoint(self.db, first_run_id, phase="model", messages=[{"role": "user", "content": "Continue."}],
                              done_tools={"project_artifact_write\0saved": receipt},
                              context_state={"version": 1, "progress": {"writes": [receipt]}})
        tasks.fail(self.db, first_run_id, "Model stopped after saving the required file")

        resumed = tasks.start(self.db, task_id, authority=tasks.CommandContext.ui(self.project))
        self.assertNotEqual(resumed["id"], first_run_id)
        self.assertEqual(resumed["checkpoint"]["resumed_from"], first_run_id)
        tasks.claim(self.db, resumed["id"])
        self.db.project_task_context = tasks.CommandContext.agent(self.project, task_id)

        submission = project_artifacts.submission(self.db, self.project)
        self.assertTrue(submission["submitted"])
        self.assertEqual(submission["files"][0]["content_hash"], receipt["content_hash"])

        target = Path(self.db.base_dir(self.project)) / "outputs/report.md"
        target.write_text("# Changed outside the checkpoint", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Save each required file"):
            project_artifacts.submission(self.db, self.project)

        target.write_text(content, encoding="utf-8")
        tasks.fail(self.db, resumed["id"], "Checkpoint is no longer eligible for normal continuation")
        restarted = tasks.start(self.db, task_id, authority=tasks.CommandContext.ui(self.project), restart=True)
        tasks.claim(self.db, restarted["id"])
        self.db.project_task_context = tasks.CommandContext.agent(self.project, task_id)
        with self.assertRaisesRegex(ValueError, "Save each required file"):
            project_artifacts.submission(self.db, self.project)

    def test_empty_hash_reuses_own_write_receipt_but_preserves_a_later_human_edit(self):
        task_id, attempt_id = self.start_node({"required_files": ["outputs/report.md"]})
        target = Path(self.db.base_dir(self.project)) / "outputs/report.md"
        def human_edit_then_write():
            self.assertEqual(target.read_text(encoding="utf-8"), "# Updated by this attempt")
            target.write_text("# Later human revision", encoding="utf-8")
            return self.reply(self.call("project_artifact_write", {
                "path": "outputs/report.md", "content": "# Must not overwrite human work", "expected_hash": ""}, "stale-write"))
        result, requests = self.execute_script(attempt_id, [
            self.reply(self.call("project_artifact_write", {"path": "outputs/report.md", "content": "# Original", "expected_hash": ""}, "first-write")),
            self.reply(self.call("project_artifact_write", {"path": "outputs/report.md", "content": "# Updated by this attempt", "expected_hash": ""}, "second-write")),
            human_edit_then_write,
            {"content": "Preserve the newer human revision for review."},
        ])
        self.assertEqual(requests, 4)
        self.assertEqual([event["ok"] for event in self.events(attempt_id, "project_artifact_write")], [True, True, False])
        self.assertEqual(target.read_text(encoding="utf-8"), "# Later human revision")
        self.assertNotEqual(result["status"], "delivered")
        self.assertEqual(tasks.get(self.db, task_id)["accepted_delivery_id"], "")


if __name__ == "__main__":
    unittest.main()
