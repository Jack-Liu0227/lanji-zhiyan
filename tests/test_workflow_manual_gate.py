"""Automatic workflow policy cannot bypass an explicitly required human gate."""
import unittest

import project_artifacts
import project_task_execution as worker
import project_tasks as tasks
from tests import test_project_artifacts as fixtures


class WorkflowManualGateTest(unittest.TestCase):
    def setUp(self):
        fixtures.ProjectArtifactTest.setUp(self)

    def candidate(self, *, manual, mode="auto"):
        task = tasks.create(self.db, {
            "project_id": self.project, "title": "Final human gate", "goal": "Save a valid report.",
            "executor_binding_id": self.binding["id"], "review_required": True,
            "scope": {"manual_review_required": manual, "workflow_review_mode": mode},
            "output_contract": {"required_files": ["report.md"]},
        }, authority=tasks.CommandContext.system(self.project))
        run = tasks.start(self.db, task["id"], authority=tasks.CommandContext.ui(self.project))
        claimed = tasks.claim(self.db, run["id"])
        self.db.project_task_context = tasks.CommandContext.agent(self.project, task["id"])
        saved = project_artifacts.write(self.db, self.project, {
            "path": "report.md", "content": "# Valid report\n\nThe report still requires its configured human decision.",
        })
        output = worker._outputs(self.db, claimed["task"], claimed["run"], {
            "reply": "Saved report.", "output_assets": saved["output_assets"],
        }, None)
        return task, run, output

    def test_auto_policy_keeps_an_explicit_manual_gate_unapproved(self):
        task, run, output = self.candidate(manual=True)
        result = tasks.complete(self.db, run["id"], {"reply": "Saved report.", "output_snapshot": output})
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(result["accepted_delivery_id"], "")
        self.assertEqual(tasks.get_run(self.db, run["id"])["status"], "succeeded")
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_task_deliveries WHERE task_id=?", (task["id"],)).fetchone()[0], 0)

    def test_auto_policy_still_delivers_a_node_without_a_manual_gate(self):
        task, run, output = self.candidate(manual=False)
        result = tasks.complete(self.db, run["id"], {"reply": "Saved report.", "output_snapshot": output})
        self.assertEqual(result["status"], "delivered")
        self.assertTrue(result["accepted_delivery_id"])

    def test_switching_review_mode_to_auto_does_not_approve_a_manual_gate(self):
        task, run, output = self.candidate(manual=True, mode="human_review")
        result = tasks.complete(self.db, run["id"], {"reply": "Saved report.", "output_snapshot": output})
        self.assertEqual(result["status"], "awaiting_review")
        result = tasks.set_review_mode(self.db, task["id"], "auto", authority=tasks.CommandContext.ui(self.project))
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(result["accepted_delivery_id"], "")


if __name__ == "__main__":
    unittest.main()
