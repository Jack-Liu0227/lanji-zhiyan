"""Old team and todo entry points share project task execution and delivery."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import project_bindings
import project_task_execution
import project_tasks
import project_todos
import team
import workflow
import workflow_layout


class ProjectTaskAdaptersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-task-adapters-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "app.sqlite3"
        self.db = workflow.WorkflowDB(self.path)
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Adapter project", base_dir=str(Path(self.tmp.name) / "project"))
        self.other = self.db.create_task("Other", base_dir=str(Path(self.tmp.name) / "other"))
        self.db.assistant_save("adapter-writer", name="Writer", enabled=1, permission="full_access")
        self.db.assistant_save("adapter-reader", name="Reader", enabled=1)
        self.writer_grant = project_bindings.register(self.db, self.project, "assistant", "adapter-writer")
        self.office = self.db.team_add("Project office", "custom", project_id=self.project)
        self.lead = self.db.team_member_add(self.office["id"], "Leader", "leader", assistant_id="adapter-writer")
        self.member = self.db.team_member_add(self.office["id"], "Reader", "member", assistant_id="adapter-reader")
        self.team_grant = project_bindings.register(self.db, self.project, "team", self.office["id"])
        self.ui = project_tasks.CommandContext.ui(self.project)
        self.addCleanup(patch.stopall)
        patch.object(team, "DB_PATH", self.path).start()
        self.kicks = patch.object(project_task_execution, "kick").start()

    def assign(self, prompt="Prepare report", **kwargs):
        return team.assign(self.office["id"], self.member["id"], prompt, db=self.db, authority=self.ui, **kwargs)

    def run_task(self, task_id):
        task = project_tasks.get(self.db, task_id)
        with patch.object(workflow, "run_agent", return_value={"reply": "# Report\nVerified evidence and concrete conclusions."}) as model:
            result = project_task_execution.execute(self.db, task["latest_run"]["id"])
        return result, model

    def test_project_team_uses_actual_project_and_one_canonical_worker(self):
        before = self.db.db.execute("SELECT COUNT(*) FROM team_members WHERE task_id=?", (self.project,)).fetchone()[0]
        legacy = self.assign()
        task = legacy["canonical_task"]
        self.assertEqual(task["project_id"], self.project)
        self.assertEqual(task["member_id"], self.member["id"])
        self.assertEqual(task["workflow_run_id"], "")
        self.assertEqual(task["latest_run"]["executor_snapshot"]["team_id"], self.office["id"])
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM team_members WHERE task_id=?", (self.project,)).fetchone()[0], before)
        result, model = self.run_task(task["id"])
        self.assertEqual(result["status"], "awaiting_review")
        self.assertEqual(model.call_args.args[1], self.project)
        self.assertEqual(model.call_args.kwargs["member"]["id"], self.member["id"])
        self.assertEqual(self.db.team_task(legacy["id"])["status"], "review")
        self.assertTrue(result["outputs"])
        self.assertTrue(all(Path(item["path"]).is_relative_to(self.db.base_dir(self.project)) for item in result["outputs"]))
        self.assertEqual(project_tasks.find_origin(self.db, "team_task", legacy["id"])["id"], task["id"])

    def test_team_review_reject_retry_and_approval_use_canonical_decisions(self):
        legacy = self.assign(reviewer_id=self.lead["id"])
        task, _ = self.run_task(legacy["canonical_task_id"])
        with self.assertRaisesRegex(ValueError, "用户操作"):
            team.review(legacy["id"], True, self.lead["id"])
        rejected = team.review(legacy["id"], False, self.lead["id"], "Add source citations", authority=self.ui)
        self.assertEqual(rejected["canonical_task"]["status"], "blocked")
        self.assertEqual(rejected["canonical_task"]["deliveries"][-1]["source"], "human")
        retried = team.retry(legacy["id"], authority=self.ui)
        self.assertEqual(retried["run"]["attempt"], 2)
        self.assertEqual(retried["run"]["input_snapshot"]["feedback"], "Add source citations")
        self.run_task(task["id"])
        approved = team.review(legacy["id"], True, self.lead["id"], "Sources checked", authority=self.ui)
        self.assertEqual(approved["status"], "done")
        self.assertEqual(approved["canonical_task"]["status"], "delivered")
        self.assertEqual(len(approved["canonical_task"]["runs"]), 2)
        with self.assertRaisesRegex(ValueError, "已交付"):
            team.retry(legacy["id"], authority=self.ui)

    def test_team_cancel_preserves_old_id_and_retry_is_another_attempt(self):
        row = self.assign()
        cancelled = team.cancel(row["id"], authority=self.ui)
        self.assertEqual(cancelled["task"]["status"], "cancelled")
        self.assertEqual(self.db.team_task(row["id"])["status"], "canceled")
        retried = team.retry(row["id"], authority=self.ui)
        self.assertEqual(retried["canonical_task_id"], row["canonical_task_id"])
        self.assertEqual(retried["run"]["attempt"], 2)

    def test_team_dependencies_wait_for_delivery_not_model_success(self):
        first = self.assign("First")
        second = self.assign("Second", depends_on=[first["id"]])
        task = project_tasks.get(self.db, second["canonical_task_id"])
        self.assertEqual(task["dependencies"], [first["canonical_task_id"]])
        self.assertIsNone(task["latest_run"])
        self.run_task(first["canonical_task_id"])
        self.assertEqual(team.tick(), 0)
        team.review(first["id"], True, authority=self.ui)
        with patch.object(workflow, "run_agent", return_value={"reply": "Second result and source evidence"}):
            self.assertEqual(team.tick(), 1)
        task = project_tasks.get(self.db, second["canonical_task_id"])
        self.assertEqual(task["status"], "awaiting_review")
        self.assertEqual(task["latest_run"]["input_snapshot"]["dependencies"][0]["delivery_id"],
                         project_tasks.get(self.db, first["canonical_task_id"])["accepted_delivery_id"])

    def test_project_grant_is_required_and_disabled_is_not_revived(self):
        project_bindings.register(self.db, self.project, "team", self.office["id"], enabled=False)
        with self.assertRaisesRegex(ValueError, "停用"):
            self.assign()
        self.assertEqual(self.db.team_tasks(self.office["id"]), [])
        grant = project_bindings.list_bindings(self.db, self.project)
        self.assertFalse(next(item for item in grant if item["id"] == self.team_grant["id"])["enabled"])

    def test_todo_without_workflow_has_no_fake_members_and_requires_human_review(self):
        todo = project_todos.add_todo(self.db, self.project, "Read evidence", assigned_assistant="adapter-writer")
        before = self.db.db.execute("SELECT COUNT(*) FROM team_members").fetchone()[0]
        task = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        self.assertEqual(task["workflow_run_id"], "")
        self.assertNotIn("stage", task["metadata"])
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM team_members").fetchone()[0], before)
        self.assertEqual(self.db.team_tasks(self.project), [])
        self.assertEqual(project_tasks.find_origin(self.db, "todo", todo["id"])["id"], task["id"])
        with self.assertRaisesRegex(ValueError, "勾选"):
            project_todos.toggle_todo(self.db, todo["id"])
        self.run_task(task["id"])
        shown = project_todos.list_todos(self.db, self.project)[0]
        self.assertEqual(shown["status"], "REVIEW")
        self.assertEqual(shown["canonical_task_id"], task["id"])
        with self.assertRaisesRegex(ValueError, "勾选"):
            project_todos.update_todo(self.db, todo["id"], status="DONE")
        accepted = project_tasks.review(self.db, task["id"], approved=True, authority=self.ui)
        self.assertEqual(accepted["deliveries"][-1]["source"], "human")
        self.assertEqual(project_todos.list_todos(self.db, self.project)[0]["status"], "DONE")
        with self.assertRaisesRegex(ValueError, "勾选"):
            project_todos.toggle_todo(self.db, todo["id"])

    def test_todo_manual_checkboxes_work_and_failed_dispatch_retries_same_task(self):
        manual = project_todos.add_todo(self.db, self.project, "Remember idea")
        self.assertEqual(project_todos.toggle_todo(self.db, manual["id"])["status"], "DONE")
        self.assertEqual(project_todos.toggle_todo(self.db, manual["id"])["status"], "TODO")
        todo = project_todos.add_todo(self.db, self.project, "Executable", assigned_assistant="adapter-writer")
        task = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        with self.assertRaisesRegex(ValueError, "已有执行任务"):
            project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        project_tasks.fail(self.db, task["latest_run"]["id"], "Model unavailable")
        retry = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        self.assertEqual(retry["id"], task["id"])
        self.assertEqual(retry["latest_run"]["attempt"], 2)

    def test_full_access_project_uses_policy_delivery_and_survives_old_sync(self):
        project_bindings.save_settings(self.db, self.project, "full_access")
        todo = project_todos.add_todo(self.db, self.project, "Auto report", assigned_assistant="adapter-writer")
        task = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        result, _ = self.run_task(task["id"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["deliveries"][-1]["source"], "policy")
        project_todos.bind_execution(self.db, todo["id"], self.project, schedule_id="old-schedule")
        project_todos.sync_execution(self.db, schedule_id="old-schedule", status="failed")
        self.assertEqual(project_todos.list_todos(self.db, self.project)[0]["status"], "DONE")

    def test_chat_team_keeps_legacy_queue_without_project_task(self):
        office = self.db.team_add("Chat group", "custom")
        member = self.db.team_member_add(office["id"], "Writer", "leader", assistant_id="adapter-writer")
        row = team.assign(office["id"], member["id"], "Discuss an idea", db=self.db)
        self.assertNotIn("canonical_task_id", row)
        self.assertIsNone(project_tasks.find_origin(self.db, "team_task", row["id"]))

    def test_agent_dispatch_inherits_parent_scope(self):
        parent = project_tasks.create(self.db, {"project_id": self.project, "title": "Scoped parent", "goal": "Read sources",
            "executor_binding_id": self.writer_grant["id"], "scope": {"allowed_binding_ids": [self.writer_grant["id"]]}}, authority=self.ui)
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, parent["id"])
        todo = project_todos.add_todo(self.db, self.project, "Child read", assigned_assistant="adapter-writer")
        child = project_todos.dispatch(self.db, todo["id"])
        self.assertEqual(child["parent_task_id"], parent["id"])
        with self.assertRaisesRegex(ValueError, "范围"):
            team.assign(self.office["id"], self.member["id"], "Escaped team task", db=self.db)
        self.assertEqual(self.db.team_tasks(self.office["id"]), [])

    def test_stage_todo_uses_saved_project_role_and_stage_artifact(self):
        workflow_layout.ensure_schema(self.db)
        data = workflow_layout._linear(["outline"])
        data["nodes"][0]["executor_role_id"] = "evidence-writer"
        layout = workflow_layout.save_layout(self.db, {"name": "Evidence flow", **data})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        project_bindings.bind_role(self.db, self.project, layout["id"], "evidence-writer", self.team_grant["id"], member_id=self.member["id"])
        todo = project_todos.add_todo(self.db, self.project, "Evidence outline", stage="outline", assigned_assistant="adapter-reader")
        task = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        self.assertEqual(task["executor_binding_id"], self.team_grant["id"])
        self.assertEqual(task["member_id"], self.member["id"])
        def model(db, project_id, prompt, **kwargs):
            workflow.doc_write(db, project_id, "outline", "# Outline\nResearch question, evidence and proposed experiments.")
            return {"reply": "Outline saved with evidence"}
        with patch.object(workflow, "run_agent", side_effect=model):
            task = project_task_execution.execute(self.db, task["latest_run"]["id"])
        self.assertEqual(task["status"], "awaiting_review", task)
        project_tasks.review(self.db, task["id"], approved=True, authority=self.ui)
        self.assertEqual(project_todos.list_todos(self.db, self.project)[0]["status"], "DONE")

    def test_project_team_conversation_keeps_turns_context_and_review_gate(self):
        team.say(self.office["id"], "Compare the evidence", start=False)
        team.TEAM_RUNS[self.office["id"]] = {"turn": 0, "total": 2, "stop": False}
        calls = []
        def model(db, project_id, prompt, **kwargs):
            calls.append((project_id, kwargs))
            if len(calls) == 1:
                return {"reply": "@Reader First-turn evidence"}
            if len(calls) == 2:
                self.assertIn("First-turn evidence", kwargs["extra_context"])
                return {"reply": "Second-turn evidence and open questions"}
            return {"reply": "Final team summary with checked evidence"}
        with patch.object(workflow, "run_agent", side_effect=model):
            team._round(self.office["id"], 2)
        rows = self.db.team_tasks(self.office["id"])
        self.assertEqual(len(rows), 1)
        task = project_tasks.find_origin(self.db, "team_task", rows[0]["id"])
        self.assertEqual(task["status"], "awaiting_review", task)
        self.assertEqual(len(task["children"]), 2)
        self.assertTrue(all(child["status"] == "delivered" for child in task["children"]))
        self.assertEqual([call[1]["member"]["id"] for call in calls], [self.lead["id"], self.member["id"], self.lead["id"]])
        messages = team.transcript(self.office["id"])
        self.assertTrue(any("First-turn evidence" in message["content"] for message in messages))
        self.assertTrue(any("核查并验收" in message["content"] for message in messages))
        self.assertNotIn(self.office["id"], team.TEAM_RUNS)
        self.assertEqual(len(self.db.team_members(self.office["id"])), 2)

    def test_stop_project_conversation_cancels_parent_and_member(self):
        team.say(self.office["id"], "Collect evidence", start=False)
        team.TEAM_RUNS[self.office["id"]] = {"turn": 0, "total": 2, "stop": False}
        def model(*args, **kwargs):
            team.stop(self.office["id"])
            return {"reply": "A late response must not become delivered"}
        with patch.object(workflow, "run_agent", side_effect=model):
            team._round(self.office["id"], 2)
        parent = project_tasks.find_origin(self.db, "team_task", self.db.team_tasks(self.office["id"])[0]["id"])
        self.assertEqual(parent["status"], "cancelled")
        self.assertTrue(all(child["status"] == "cancelled" for child in parent["children"]))
        self.assertEqual(parent["deliveries"], [])
        self.assertNotIn(self.office["id"], team.TEAM_RUNS)

    def test_only_root_workflow_task_review_resumes_coordinator(self):
        authority = project_tasks.CommandContext.system(self.project)
        spec = {"project_id": self.project, "title": "Flow root", "goal": "Synthesize evidence",
            "executor_binding_id": self.team_grant["id"], "member_id": self.lead["id"], "workflow_run_id": "test-root-flow",
            "workflow_node_id": "test-node", "scope": {"allowed_binding_ids": [self.team_grant["id"]]}}
        root = project_tasks.create(self.db, spec, authority=authority)
        child = project_tasks.create(self.db, {"project_id": self.project, "title": "Child", "goal": "Gather evidence",
            "executor_binding_id": self.team_grant["id"], "member_id": self.member["id"], "parent_task_id": root["id"]},
            authority=project_tasks.CommandContext.system(self.project, root["id"]))
        rows = []
        for task in (child, root):
            row = self.db.team_task_add(self.office["id"], task["member_id"], "Member", task["title"], task["goal"], origin="workflow:test-root-flow")
            project_tasks.link_origin(self.db, task["id"], "team_task", row["id"])
            rows.append(row)
        with patch("workflow_execution.kick") as coordinate:
            project_tasks.start(self.db, child["id"], authority=authority)
            self.run_task(child["id"])
            team.review(rows[0]["id"], True, authority=self.ui)
            coordinate.assert_not_called()
            project_tasks.start(self.db, root["id"], authority=authority)
            self.run_task(root["id"])
            team.review(rows[1]["id"], True, authority=self.ui)
            coordinate.assert_called_once_with(self.path, "test-root-flow")

    def test_rebound_team_cannot_fall_back_to_legacy_execution(self):
        row = self.assign()
        self.db.team_update(self.office["id"], project_id="")
        with patch.object(workflow, "run_agent") as model:
            self.assertEqual(team.tick(), 1)
            model.assert_not_called()
        task = project_tasks.get(self.db, row["canonical_task_id"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("项目", task["latest_run"]["error"])

    def test_existing_legacy_todo_execution_cannot_be_dispatched_twice(self):
        todo = project_todos.add_todo(self.db, self.project, "Scheduled evidence", assigned_assistant="adapter-writer")
        project_todos.bind_execution(self.db, todo["id"], self.project, schedule_id="pending-legacy-schedule")
        with self.assertRaisesRegex(ValueError, "已有执行任务"):
            project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        self.assertEqual(project_tasks.list_tasks(self.db, self.project), [])
        project_todos.sync_execution(self.db, schedule_id="pending-legacy-schedule", status="cancelled")
        task = project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        with self.assertRaisesRegex(ValueError, "已有执行任务"):
            project_todos.bind_execution(self.db, todo["id"], self.project, schedule_id="another-schedule")
        self.assertEqual(project_tasks.find_origin(self.db, "todo", todo["id"])["id"], task["id"])

    def test_explicit_todo_assignment_grants_assistant_without_reviving_denials(self):
        self.db.db.execute("DELETE FROM project_executor_bindings WHERE id=?", (self.writer_grant["id"],))
        self.db.db.commit()
        todo = project_todos.add_todo(self.db, self.project, "Newly assigned", assigned_assistant="adapter-writer")
        grant = next(item for item in project_bindings.list_bindings(self.db, self.project) if item["target_id"] == "adapter-writer")
        self.assertTrue(grant["enabled"])
        self.assertEqual(grant["source"], "legacy_todo")
        project_bindings.register(self.db, self.project, "assistant", "adapter-writer", enabled=False)
        project_todos.update_todo(self.db, todo["id"], assigned_assistant="adapter-writer")
        project_todos.add_todo(self.db, self.project, "Still denied", assigned_assistant="adapter-writer")
        grant = next(item for item in project_bindings.list_bindings(self.db, self.project) if item["target_id"] == "adapter-writer")
        self.assertFalse(grant["enabled"])
        with self.assertRaisesRegex(ValueError, "停用"):
            project_todos.dispatch(self.db, todo["id"], authority=self.ui)
        project_todos.update_todo(self.db, todo["id"], assigned_assistant="adapter-reader")
        self.assertTrue(any(item["target_id"] == "adapter-reader" for item in project_bindings.list_bindings(self.db, self.project)))

    def test_explicit_assistant_selection_grants_legacy_project_member(self):
        member = team.add_assistant(self.project, "adapter-reader")
        self.assertEqual(member["task_id"], self.project)
        self.assertTrue(any(item["target_id"] == "adapter-reader" for item in project_bindings.list_bindings(self.db, self.project)))
        self.assertEqual([row["id"] for row in team.members(self.project)], [member["id"]])

    def legacy_task(self, status, *, title="Saved team task"):
        import asset_service
        row = self.db.team_task_add(self.office["id"], self.member["id"], self.member["name"], title,
                                   "Analyze saved evidence", reviewer_id=self.lead["id"])
        folder = self.db.base_dir(self.project) / "legacy-team"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (row["id"] + ".md")
        path.write_text("# Legacy evidence\nSaved conclusions with source citations.", encoding="utf-8")
        old_run = "saved-run-" + row["id"]
        asset = asset_service.register_file(self.db, path, project_id=self.project, kind="team_output", run_id=old_run)
        self.db.team_task_mark(row["id"], status, result="Saved conclusions", output_path=str(path), output_assets=[asset["id"]],
            run_id=old_run, checkpoint={"phase": status, "legacy_marker": "saved-checkpoint"},
            review_status="approved" if status == "done" else "pending")
        return self.db.team_task(row["id"]), path

    def test_legacy_pending_review_is_adopted_and_human_can_review_saved_artifact(self):
        old, path = self.legacy_task("review")
        with patch.object(workflow, "run_agent") as model:
            reviewed = team.review(old["id"], True, self.lead["id"], "Checked the saved evidence", authority=self.ui)
            model.assert_not_called()
        task = reviewed["canonical_task"]
        self.assertEqual(task["status"], "delivered")
        self.assertEqual(len(task["runs"]), 1)
        self.assertEqual([decision["source"] for decision in task["deliveries"]], ["human"])
        self.assertEqual(task["latest_run"]["checkpoint"]["legacy_marker"], "saved-checkpoint")
        self.assertTrue(task["latest_run"]["checkpoint"]["legacy_imported"])
        self.assertEqual(task["metadata"]["legacy_record"]["run_id"], old["run_id"])
        self.assertEqual(project_tasks.find_origin(self.db, "team_task", old["id"])["id"], task["id"])
        self.assertTrue(any(Path(item["path"]).read_text(encoding="utf-8") == path.read_text(encoding="utf-8") for item in task["outputs"]))

    def test_legacy_done_dependency_is_versioned_and_invalid_output_blocks_dispatch(self):
        old, path = self.legacy_task("done")
        with patch.object(workflow, "run_agent") as model:
            downstream = self.assign("Continue from saved delivery", depends_on=[old["id"]])
            model.assert_not_called()
        restored = project_tasks.find_origin(self.db, "team_task", old["id"])
        self.assertEqual(restored["status"], "delivered")
        self.assertEqual([decision["source"] for decision in restored["deliveries"]], ["legacy"])
        self.assertEqual(downstream["canonical_task"]["latest_run"]["input_snapshot"]["dependencies"][0]["delivery_id"], restored["accepted_delivery_id"])
        result, _ = self.run_task(downstream["canonical_task_id"])
        self.assertEqual(result["status"], "awaiting_review")
        invalid, invalid_path = self.legacy_task("done", title="Changed old artifact")
        invalid_path.write_text("Changed after the recorded delivery", encoding="utf-8")
        with patch.object(workflow, "run_agent") as model:
            waiting = self.assign("Must wait for valid evidence", depends_on=[invalid["id"]])
            model.assert_not_called()
        blocked = project_tasks.find_origin(self.db, "team_task", invalid["id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["deliveries"], [])
        self.assertIn("版本已变化", blocked["latest_run"]["error"])
        self.assertIsNone(waiting["canonical_task"]["latest_run"])


if __name__ == "__main__":
    unittest.main()
