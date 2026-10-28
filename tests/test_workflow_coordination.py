"""Cross-module acceptance uses real artifacts and project workflow dependencies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asset_service
import project_todos
import workflow
import workflow_converters
import workflow_layout
import workflow_state


class CoordinationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Coordination", base_dir=str(Path(self.tmp.name) / "project"))
        workflow.make_project_tree(Path(self.tmp.name) / "project")

    def layout(self, steps, edges=None):
        workflow_layout.ensure_schema(self.db)
        data = workflow_layout._linear(steps)
        if edges is not None:
            data["edges"] = [{"source": a, "target": b} for a, b in edges]
        saved = workflow_layout.save_layout(self.db, {"name": "Test", **data})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": saved["id"]})

    def write(self, step, text="A complete artifact"):
        return workflow.doc_write(self.db, self.project, step, text)

    def accept(self, step):
        return workflow_state.accept(self.db, self.project, step)

    def test_custom_root_and_all_branch_dependencies_drive_gate(self):
        self.layout(["outline", "write", "review"], [("outline", "review"), ("write", "review")])
        self.assertFalse(workflow_state.gate(self.db, self.project, "write")["blocked"])
        self.write("outline")
        self.accept("outline")
        self.assertEqual(workflow_state.gate(self.db, self.project, "review")["blocked_by"], ["write"])
        self.write("write")
        self.accept("write")
        self.assertFalse(workflow_state.gate(self.db, self.project, "review")["blocked"])

    def test_execution_success_is_not_delivery_and_empty_artifact_is_rejected(self):
        self.layout(["outline", "write"])
        self.db.log_step(self.project, "outline", "done")
        self.assertFalse(workflow.step_done(self.db, self.project, "outline"))
        self.assertTrue(workflow_state.gate(self.db, self.project, "write")["blocked"])
        with self.assertRaisesRegex(ValueError, "完整产物"):
            self.accept("outline")
        self.write("outline")
        delivered = self.accept("outline")
        self.assertEqual(self.accept("outline")["id"], delivered["id"])
        self.assertTrue(workflow.step_done(self.db, self.project, "outline"))
        self.db.log_step(self.project, "outline", "failed")
        self.assertFalse(workflow.step_done(self.db, self.project, "outline"))

    def test_chat_in_same_second_cannot_hide_failed_stage(self):
        self.layout(["outline"])
        self.write("outline")
        self.db.log_step(self.project, "outline", "failed")
        self.db.add_message(self.project, "assistant", "Previous output", scope="outline")
        self.db.db.execute("UPDATE steps SET created_at='2026-10-24T10:00:00' WHERE task_id=?", (self.project,))
        self.db.db.execute("UPDATE chat SET created_at='2026-10-24T10:00:00' WHERE task_id=?", (self.project,))
        self.db.db.commit()
        self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "failed")
        with self.assertRaises(ValueError):
            self.accept("outline")

    def test_changes_invalidate_descendants_and_plans_use_confirmed_snapshot(self):
        self.layout(["outline", "write", "review"])
        self.write("outline", "CONFIRMED_OUTLINE_VERSION_ONE")
        first = self.accept("outline")
        self.write("write", "First draft")
        self.accept("write")
        context = workflow._plan_context(self.db, self.project, "write")
        self.assertIn(first["id"], context)
        self.assertIn("CONFIRMED_OUTLINE_VERSION_ONE", context)
        with patch.object(workflow, "chat", return_value="A plan") as chat:
            self.assertFalse(workflow.step_plan(self.db, self.project, "write")["cached"])
            self.assertTrue(workflow.step_plan(self.db, self.project, "write")["cached"])
            self.write("outline", "VERSION_TWO")
            self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "stale")
            self.assertTrue(workflow_state.gate(self.db, self.project, "review")["blocked"])
            self.accept("outline")
            self.assertFalse(workflow.step_plan(self.db, self.project, "write")["cached"])
            self.assertEqual(chat.call_count, 2)

    def test_todo_import_is_idempotent_and_tracks_acceptance(self):
        self.layout(["outline", "write"])
        first = project_todos.import_workflow_stages_as_todos(self.db, self.project)
        second = project_todos.import_workflow_stages_as_todos(self.db, self.project)
        self.assertEqual([t["id"] for t in first], [t["id"] for t in second])
        self.assertEqual({t["stage"] for t in first}, {"outline", "write"})
        self.write("outline")
        self.db.log_step(self.project, "outline", "done")
        todo = lambda: next(t for t in project_todos.list_todos(self.db, self.project) if t["stage"] == "outline")
        self.assertEqual(todo()["status"], "REVIEW")
        self.accept("outline")
        self.assertEqual(todo()["status"], "DONE")
        self.write("outline", "Changed after delivery")
        self.assertEqual(todo()["status"], "REVIEW")

    def test_empty_translation_preserves_previous_output_and_progress(self):
        self.layout(["entrans"])
        path = Path(workflow.en_state(self.db, self.project)["result_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Previous translation", encoding="utf-8")
        with patch.object(self.db, "active_model", return_value={"model": "mock"}), \
                patch.object(workflow, "chat", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "空内容"):
                workflow_converters.en_run(self.db, self.project, "Source")
        self.assertEqual(path.read_text(encoding="utf-8"), "Previous translation")
        self.assertEqual(self.db.steps(self.project), [])

    def test_named_translation_is_restored_and_registered(self):
        self.layout(["entrans"])
        with patch.object(self.db, "active_model", return_value={"model": "mock"}), \
                patch.object(workflow, "chat", return_value="Translated abstract"):
            result = workflow_converters.en_run(self.db, self.project, "Original abstract", name="abstract")
        state = workflow.en_state(self.db, self.project)
        self.assertEqual(state["result_path"], result["path"])
        self.assertEqual(state["source_text"], "Original abstract")
        self.accept("entrans")
        self.assertTrue(workflow.step_done(self.db, self.project, "entrans"))

    def test_expert_dispatch_reviews_real_artifact_and_updates_all_views(self):
        import project_tasks
        import project_task_execution
        self.layout(["outline"])
        todo = project_todos.add_todo(self.db, self.project, "Outline", stage="outline",
                                      assigned_assistant="builtin-steward")
        with patch.object(project_task_execution, "kick"):
            task = project_todos.dispatch(self.db, todo["id"])
        with self.assertRaisesRegex(ValueError, "已有执行任务"):
            project_todos.dispatch(self.db, todo["id"])
        self.assertEqual(len(self.db.team_tasks(self.project)), 0)

        def run(db, tid, prompt, **kwargs):
            workflow.doc_write(db, tid, "outline", "# Outline\nResearch question and evidence")
            return {"reply": "Outline saved"}

        with patch.object(workflow, "run_agent", side_effect=run):
            project_task_execution.execute(self.db, task["latest_run"]["id"])
            self.assertEqual(project_todos.list_todos(self.db, self.project)[0]["status"], "REVIEW")
            project_tasks.review(self.db, task["id"], approved=True, authority=project_tasks.CommandContext.ui(self.project))
        overview = project_todos.kanban_overview(self.db, self.project)
        self.assertEqual(overview["stats"]["stages_done"], 1)
        self.assertEqual(overview["todos"][0]["status"], "DONE")
        self.assertTrue(overview["todos"][0]["execution"]["run_id"])
        self.assertTrue(json.loads(overview["todos"][0]["execution"]["output_assets"]))
        self.write("outline", "Changed version")
        self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "stale")
        self.assertEqual(project_tasks.get(self.db, task["id"])["status"], "delivered")

    def test_schedule_and_export_outputs_are_searchable_and_linked(self):
        import schedule
        todo = project_todos.add_todo(self.db, self.project, "Scheduled report")
        sched = self.db.schedule_save({"name": "Report", "project_id": self.project,
            "assistant_id": "builtin-steward", "prompt": "Prepare report", "todo_id": todo["id"]})
        with patch.object(schedule, "DB_PATH", self.db.path), \
                patch.object(workflow, "run_agent", return_value={"reply": "unique_schedule_evidence"}):
            run = schedule.run_once(sched)
        self.assertEqual(run["status"], "completed", run)
        linked = project_todos.list_todos(self.db, self.project)[0]["execution"]
        self.assertEqual(linked["run_id"], run["workflow_run_id"])
        self.assertTrue(asset_service.search(self.db, "unique_schedule_evidence", project_id=self.project))
        output = workflow.stage_convert(self.db, self.project, "unique_export_evidence", fmt="md")
        self.assertTrue(Path(output["path"]).is_file())
        self.assertTrue(asset_service.search(self.db, "unique_export_evidence", project_id=self.project))
        other = self.db.create_task("Other")
        with self.assertRaisesRegex(ValueError, "其他项目"):
            self.db.schedule_save({**sched, "project_id": other})
        foreign_todo = project_todos.add_todo(self.db, other, "Foreign")
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.db.schedule_save({"id": "invalid-schedule", "project_id": self.project,
                                   "todo_id": foreign_todo["id"]})
        self.assertIsNone(self.db.schedule("invalid-schedule"))

    def test_delivery_keeps_source_metadata_and_invalidates_removed_files(self):
        self.layout(["outline"])
        self.write("outline")
        path = workflow.doc_read(self.db, self.project, "outline")["path"]
        asset = asset_service.register_file(self.db, path, project_id=self.project, kind="note",
                                            source="zotero_copy", metadata={"source_read_only": True,
                                            "source_path": str(Path(self.tmp.name) / "original.pdf")})
        self.accept("outline")
        saved = asset_service.get(self.db, asset["id"])
        self.assertEqual(saved["source"], "zotero_copy")
        self.assertTrue(json.loads(saved["metadata"])["source_read_only"])
        Path(path).unlink()
        self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "stale")

    def test_linked_schedule_produces_stage_artifact_and_restart_requires_review(self):
        import schedule
        import run_service
        self.layout(["outline"])
        todo = project_todos.add_todo(self.db, self.project, "Scheduled outline", stage="outline")
        sched = self.db.schedule_save({"name": "Outline", "project_id": self.project,
            "assistant_id": "builtin-steward", "prompt": "Prepare outline", "todo_id": todo["id"]})
        def produce(db, pid, prompt, **kwargs):
            self.assertEqual(kwargs["scope"], "outline")
            workflow.doc_write(db, pid, "outline", "Scheduled artifact")
            return {"reply": "Saved"}
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", side_effect=produce):
            attempt = schedule.run_once(sched)
            self.assertEqual(attempt["status"], "awaiting_review")
            self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "succeeded")
            self.assertEqual(workflow.doc_read(self.db, self.project, "outline")["markdown"].strip(), "Scheduled artifact")
            import project_tasks
            canonical = project_tasks.find_origin(self.db, "schedule_run", attempt["id"])
            project_tasks.review(self.db, canonical["id"], approved=True, authority=project_tasks.CommandContext.ui(self.project))
            self.assertEqual(self.db.schedule_runs()[0]["status"], "completed")
            self.db.schedule_run_add({"id": "crashed", "schedule_id": sched["id"], "status": "running"})
            tracked = run_service.start(self.db, self.project, "schedule", idempotency_key="schedule:crashed")
            run_service.node_start(self.db, tracked["id"], "schedule")
            self.assertEqual(schedule.recover_runs(), 1)
            self.assertEqual(schedule.recover_runs(), 0)
            self.assertEqual(run_service.get(self.db, tracked["id"])["status"], "interrupted")
            self.assertEqual(workflow_state.states(self.db, self.project)["outline"]["status"], "failed")
            self.assertNotEqual(project_todos.list_todos(self.db, self.project)[0]["status"], "DONE")

    def test_failed_pdf_download_keeps_existing_file(self):
        import io
        import workflow_search
        folder = Path(self.tmp.name) / "papers"
        folder.mkdir()
        path = folder / "paper.pdf"
        path.write_bytes(b"%PDF-original")
        with patch.object(workflow_search, "open_url", return_value=io.BytesIO(b"<html>error</html>")):
            with self.assertRaisesRegex(ValueError, "PDF"):
                workflow_search.download_pdf({"title": "paper", "pdf_url": "https://example.test/paper"}, folder)
        self.assertEqual(path.read_bytes(), b"%PDF-original")
        self.assertEqual(list(folder.glob("*.part")), [])
