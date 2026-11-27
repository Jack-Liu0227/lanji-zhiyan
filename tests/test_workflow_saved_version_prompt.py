import unittest

import project_artifacts
import project_tasks
from tests import test_project_artifacts as artifact_fixtures
from project_task_execution import _artifact_prompt_versions


class WorkflowSavedVersionPromptTest(unittest.TestCase):
    setUp = artifact_fixtures.ProjectArtifactTest.setUp
    queue = artifact_fixtures.ProjectArtifactTest.queue

    def save_and_fail(self):
        task, first = self.queue(output_contract={"required_files": ["outputs/report.md"]})
        project_tasks.claim(self.db, first["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        receipt = project_artifacts.write(self.db, self.project, {
            "path": "outputs/report.md", "content": "# Existing draft\n\nSaved before resume."})
        project_tasks.save_checkpoint(
            self.db, first["id"], phase="model", messages=[{"role": "user", "content": "Continue."}],
            done_tools={"project_artifact_write\0saved": receipt},
            context_state={"version": 1, "progress": {"writes": [receipt]}},
        )
        project_tasks.fail(self.db, first["id"], "Model stopped after saving the required file")
        return task, first, receipt

    def test_normal_resume_prompt_lists_verified_saved_hash_for_direct_submission(self):
        task, first, receipt = self.save_and_fail()
        resumed = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        self.assertEqual(resumed["checkpoint"]["resumed_from"], first["id"])
        project_tasks.claim(self.db, resumed["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])

        versions = _artifact_prompt_versions(self.db, task)

        self.assertEqual(versions["target_versions"], {"outputs/report.md": receipt["content_hash"]})
        self.assertEqual(versions["saved_versions"], {"outputs/report.md": receipt["content_hash"]})
        self.assertEqual(versions["required_to_write"], [])

    def test_explicit_restart_does_not_list_an_old_write_as_directly_submittable(self):
        task, first, receipt = self.save_and_fail()
        restarted = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project),
                                        restart=True)
        self.assertNotIn("resumed_from", restarted["checkpoint"])
        project_tasks.claim(self.db, restarted["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])

        versions = _artifact_prompt_versions(self.db, task)

        self.assertEqual(versions["target_versions"], {"outputs/report.md": receipt["content_hash"]})
        self.assertEqual(versions["saved_versions"], {})
        self.assertEqual(versions["required_to_write"], ["outputs/report.md"])


if __name__ == "__main__":
    unittest.main()
