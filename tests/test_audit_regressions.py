"""Behavioral regressions from the project-wide workflow audit."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import agent_jobs
import asset_service
import project_notes
import schedule
import space_service
import team
import workflow
import workflow_layout
import workflow_runners
import workflow_search
import workflow_state
import zotero_sync
from run_manager import RunManager


class AuditRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "audit.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Audit", base_dir=str(Path(self.tmp.name) / "project"))
        workflow.make_project_tree(Path(self.tmp.name) / "project")
        self.manager = RunManager()
        replacement = patch.object(RunManager, "_instance", self.manager)
        replacement.start()
        self.addCleanup(replacement.stop)

    def layout(self, steps):
        workflow_layout.ensure_schema(self.db)
        saved = workflow_layout.save_layout(self.db, {"name": "Audit", **workflow_layout._linear(steps)})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": saved["id"]})

    def wait(self, run):
        deadline = time.monotonic() + 8
        while run.worker_started and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(run.worker_started, "Worker did not finish")

    def test_failed_or_partial_map_draft_preserves_manuscript_and_blocks_delivery(self):
        self.layout(["write"])
        workflow.doc_write(self.db, self.pid, "outline", "# Outline\n## Introduction\n## Methods")
        workflow.map_write(self.db, self.pid, {"nodes": [
            {"id": "a", "label": "Introduction", "type": "section"},
            {"id": "b", "label": "Methods", "type": "section"}], "edges": []})
        for responses in (["", ""], ["A complete section", RuntimeError("provider failed")]):
            with self.subTest(responses=responses):
                workflow.doc_write(self.db, self.pid, "write", "KEEP_REVIEWED_MANUSCRIPT")
                previous = workflow.doc_read(self.db, self.pid, "write")["markdown"]
                with patch.object(workflow, "chat", side_effect=responses), patch.object(workflow, "agent_profile", return_value={}):
                    with self.assertRaisesRegex(RuntimeError, "原稿未改动"):
                        workflow_runners.run_write(self.db, self.pid, "整篇重写")
                self.assertEqual(workflow.doc_read(self.db, self.pid, "write")["markdown"], previous)
                with self.assertRaises(ValueError):
                    workflow_state.accept(self.db, self.pid, "write")

    def test_old_missing_section_placeholder_cannot_be_delivered(self):
        self.layout(["write"])
        workflow.doc_write(self.db, self.pid, "write", "# Introduction\n（本节未生成 —— 跟 agent 说「重写」补上）")
        with self.assertRaisesRegex(ValueError, "完整产物"):
            workflow_state.accept(self.db, self.pid, "write")

    def test_downloads_with_colliding_titles_preserve_both_files(self):
        folder = Path(self.tmp.name) / "downloads"
        for titles in (("Editorial", "Editorial"), ("A" * 80 + " first", "A" * 80 + " second"), ("A/B", "A:B")):
            with self.subTest(titles=titles), patch.object(workflow_search, "open_url", side_effect=[
                    io.BytesIO(b"%PDF-1 first"), io.BytesIO(b"%PDF-2 second")]):
                first = workflow_search.download_pdf({"title": titles[0], "pdf_url": "https://example.test/first"}, folder)
                second = workflow_search.download_pdf({"title": titles[1], "pdf_url": "https://example.test/second"}, folder)
                self.assertNotEqual(first, second)
                self.assertEqual(first.read_bytes(), b"%PDF-1 first")
                self.assertEqual(second.read_bytes(), b"%PDF-2 second")
        self.assertEqual(len(list(folder.glob("*.pdf"))), 6)
        self.assertEqual(list(folder.glob("*.part")), [])

    def zotero_setup(self, doi="10.1000/first"):
        zotero_sync.ensure(self.db)
        item = self.db.cache_results(self.pid, [{"source": "zotero", "title": "Editorial", "doi": doi, "authors": "First author"}])[0]
        self.db.db.execute("INSERT INTO zotero_bindings (task_id,library,collection) VALUES (?,?,?)", (self.pid, "users/123", "ABCDEFGH"))
        self.db.db.execute("INSERT INTO zotero_project_sources VALUES (?,?,?,'all')", (self.pid, 'users/123', 'ABCDEFGH'))
        self.db.db.commit()
        return item

    @staticmethod
    def remote(doi, key="REMOTE01"):
        return {"key": key, "version": 1, "data": {"itemType": "journalArticle", "title": "Editorial", "DOI": doi, "collections": ["ABCDEFGH"]}}

    def sync_preview(self, remote, mode="pull", apply=False):
        with patch.object(zotero_sync, "config", return_value={"library_type": "users", "library_id": "123"}), \
                patch.object(zotero_sync, "all_pages", return_value=remote), patch.object(zotero_sync, "request") as request:
            preview = zotero_sync.sync(self.db, self.pid, mode=mode)
            if apply:
                result = zotero_sync.sync(self.db, self.pid, mode=mode, dry_run=False, plan_token=preview["plan_token"])
                self.assertTrue(result["ok"], result)
                request.assert_not_called()
        return preview

    def test_zotero_same_title_different_doi_remains_two_papers(self):
        item = self.zotero_setup()
        self.sync_preview([self.remote("10.1000/second")], apply=True)
        self.assertEqual(self.db.result(item["id"])["doi"], "10.1000/first")
        self.assertEqual({p["doi"] for p in self.db.results(self.pid)}, {"10.1000/first", "10.1000/second"})
        self.assertNotEqual(self.db.cache_result(self.pid, {"source": "zotero", "title": "Editorial", "doi": "10.1000/third"}), item["id"])

    def test_zotero_exact_doi_wins_over_earlier_same_title(self):
        item = self.zotero_setup()
        plan = self.sync_preview([self.remote("10.1000/second"), self.remote("10.1000/first", "REMOTE02")])
        matched = next(a for a in plan["actions"] if a["result_id"] == item["id"])
        self.assertEqual(matched["key"], "REMOTE02")

    def test_zotero_ambiguous_title_requires_resolution(self):
        self.zotero_setup(doi="")
        plan = self.sync_preview([self.remote("10.1000/first"), self.remote("10.1000/second", "REMOTE02")])
        self.assertTrue(plan["conflicts"])

    def test_project_deletion_removes_jobs_events_schedules_and_recovery_is_safe(self):
        run = self.manager.create_run(self.pid, "deleted-project", self.db)
        self.db.update_async_task(run.run_id, status="running", checkpoint={
            "request": {"task_id": self.pid, "message": "Resume", "idempotency_key": "deleted-project"}, "step": "outline", "phase": "model"})
        run.emit({"type": "assistant", "content": "pending"}, self.db)
        sched = self.db.schedule_save({"name": "Report", "project_id": self.pid, "assistant_id": "builtin-steward"})
        self.db.schedule_run_add({"id": "scheduled-attempt", "schedule_id": sched["id"], "status": "running"})
        self.db.delete_task(self.pid)
        self.assertIsNone(self.db.get_async_task(run.run_id))
        self.assertEqual(self.db.get_run_events(run.run_id), [])
        self.assertIsNone(self.db.schedule(sched["id"]))
        self.assertFalse(any(r["id"] == "scheduled-attempt" for r in self.db.schedule_runs()))
        self.assertTrue(run.cancel_requested)
        self.assertEqual(agent_jobs.recover(self.db.path), {"resumed": [], "interrupted": []})

    def test_legacy_orphan_job_does_not_prevent_other_jobs_recovering(self):
        orphan = self.manager.create_run("deleted-project", "orphan", self.db)
        good = self.manager.create_run(self.pid, "good", self.db)
        for run, pid in ((orphan, "deleted-project"), (good, self.pid)):
            self.db.update_async_task(run.run_id, status="running", checkpoint={
                "request": {"task_id": pid, "message": "Resume", "idempotency_key": run.idempotency_key}, "step": "", "phase": "model"})
        with patch.object(workflow, "_chat_raw", return_value={"content": "Recovered"}):
            result = agent_jobs.recover(self.db.path)
            self.wait(good)
        self.assertEqual(result["interrupted"], [orphan.run_id])
        self.assertEqual(result["resumed"], [good.run_id])
        self.assertEqual(self.db.get_async_task(good.run_id)["status"], "done")

    def test_note_create_edit_delete_keeps_storage_and_search_in_sync(self):
        relative = "笔记/研究计划/fresh.md"
        project_notes.write(self.db, self.pid, relative, "UNIQUE_NEW_NOTE")
        self.assertEqual(space_service.search_project_space(self.db, self.pid, "UNIQUE_NEW_NOTE")["total"], 1)
        for create, table, field in ((self.db.save_md_note, "md_notes", "content"), (self.db.add_note, "notes", "markdown")):
            with self.subTest(table=table):
                note = create("", "Existing note", "OLD_NOTE_CONTENT", task_id=self.pid) if table == "md_notes" else create(self.pid, "Existing note", "OLD_NOTE_CONTENT")
                relative = Path(note["canonical_path"]).relative_to(self.db.base_dir(self.pid)).as_posix()
                project_notes.write(self.db, self.pid, relative, "UPDATED_NOTE_CONTENT")
                self.assertEqual(space_service.search_project_space(self.db, self.pid, "OLD_NOTE_CONTENT")["total"], 0)
                self.assertEqual(space_service.search_project_space(self.db, self.pid, "UPDATED_NOTE_CONTENT")["total"], 1)
                import note_catalog
                stored = self.db.db.execute(f"SELECT {field} FROM {table} WHERE id=?", (note["id"],)).fetchone()[0]
                self.assertEqual(note_catalog.split(stored)[1], "UPDATED_NOTE_CONTENT")
                self.assertEqual(stored, (self.db.base_dir(self.pid) / relative).read_text(encoding="utf-8"))
                project_notes.delete(self.db, self.pid, relative)
                self.assertEqual(space_service.search_project_space(self.db, self.pid, "UPDATED_NOTE_CONTENT")["total"], 0)
                self.assertIsNone(self.db.db.execute(f"SELECT id FROM {table} WHERE id=?", (note["id"],)).fetchone())

    def test_note_folder_delete_and_other_editor_preserve_canonical_path(self):
        relative = "笔记/研究计划/sub/note.md"
        project_notes.write(self.db, self.pid, relative, "FIRST_CONTENT")
        note = next(n for n in self.db.md_notes() if n["task_id"] == self.pid)
        saved = self.db.save_md_note(note["id"], "Renamed note", "SECOND_CONTENT")
        self.assertEqual(Path(saved["canonical_path"]), self.db.base_dir(self.pid) / relative)
        asset_service.rebuild_project_index(self.db, self.pid)
        project_notes.delete(self.db, self.pid, "笔记/研究计划/sub")
        self.assertEqual(space_service.search_project_space(self.db, self.pid, "SECOND_CONTENT")["total"], 0)

    def test_note_changes_invalidate_old_semantic_chunks(self):
        import numpy as np
        import semantic
        note = self.db.save_md_note("", "Semantic note", "OLD_SEMANTIC_CONTENT " * 30, task_id=self.pid)
        relative = Path(note["canonical_path"]).relative_to(self.db.base_dir(self.pid)).as_posix()
        with patch.object(semantic, "config", return_value={"model": "audit"}), \
                patch.object(semantic, "embed", side_effect=lambda texts, cfg: np.ones((len(texts), 2), dtype=np.float32)):
            semantic.sync(self.db)
            self.assertGreater(self.db.db.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0], 0)
            project_notes.write(self.db, self.pid, relative, "NEW_SEMANTIC_CONTENT " * 30)
            self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0], 0)
            semantic.sync(self.db)
            self.assertGreater(self.db.db.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0], 0)
            project_notes.delete(self.db, self.pid, relative)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0], 0)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM sem_state").fetchone()[0], 0)

    def test_conversion_requires_registered_export_and_tracks_its_bytes(self):
        self.layout(["convert"])
        workflow.doc_write(self.db, self.pid, "write", "Source draft")
        with self.assertRaisesRegex(ValueError, "完整产物"):
            workflow_state.accept(self.db, self.pid, "convert")
        output = workflow.stage_convert(self.db, self.pid, "Exported manuscript", fmt="md")
        workflow_state.accept(self.db, self.pid, "convert")
        Path(output["path"]).write_text("Changed export", encoding="utf-8")
        self.assertEqual(workflow_state.states(self.db, self.pid)["convert"]["status"], "stale")
        workflow.stage_convert(self.db, self.pid, "Replacement manuscript", fmt="md", name="replacement")
        Path(output["path"]).unlink()
        self.assertEqual(workflow_state.accept(self.db, self.pid, "convert")["status"], "delivered")

    def test_active_stage_cannot_be_delivered_after_faster_run_finishes(self):
        self.layout(["outline"])
        entered, release = threading.Event(), threading.Event()
        def runner(db, pid, prompt, emit):
            if prompt == "slow":
                entered.set()
                release.wait(8)
            workflow.doc_write(db, pid, "outline", "# " + prompt)
            return {"reply": prompt}
        with patch.object(workflow, "run_outline", side_effect=runner):
            slow = agent_jobs.start(self.db.path, {"task_id": self.pid, "message": "slow", "idempotency_key": "slow"}, "outline")
            try:
                self.assertTrue(entered.wait(5))
                fast = agent_jobs.start(self.db.path, {"task_id": self.pid, "message": "fast", "idempotency_key": "fast"}, "outline")
                self.wait(fast)
                self.assertEqual(workflow_state.states(self.db, self.pid)["outline"]["status"], "running")
                with self.assertRaises(ValueError):
                    workflow_state.accept(self.db, self.pid, "outline")
            finally:
                release.set()
                self.wait(slow)
        self.assertEqual(workflow_state.accept(self.db, self.pid, "outline")["status"], "delivered")

    def test_manual_and_scheduled_runs_share_one_execution_guard(self):
        row = self.db.schedule_save({"name": "Report", "project_id": self.pid, "assistant_id": "builtin-steward", "prompt": "Report"})
        entered, release = threading.Event(), threading.Event()
        completed = []
        def model(*args, **kwargs):
            entered.set()
            release.wait(8)
            return {"reply": "Generated report"}
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", side_effect=model) as call:
            worker = threading.Thread(target=lambda: completed.append(schedule.run_once(row, trigger="manual")))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(schedule.run_once(row, trigger="manual")["status"], "skipped")
                with patch.object(schedule, "due_list", return_value=[row]):
                    self.assertEqual(schedule.tick(), 0)
                self.assertEqual(call.call_count, 1)
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(completed[0]["status"], "completed")
            self.assertEqual(schedule.run_once(row, trigger="manual")["status"], "completed")
        self.assertEqual(len([r for r in self.db.schedule_runs() if r["schedule_id"] == row["id"]]), 2)

    def test_cancel_then_immediate_retry_runs_after_old_worker_exits(self):
        entered, release = threading.Event(), threading.Event()
        def model(*args, **kwargs):
            entered.set()
            release.wait(8)
            return {"reply": "Task output"}
        with patch.object(team, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", side_effect=model):
            chat_team = self.db.team_add("Chat queue", "custom")["id"]
            member = team.add_member(chat_team, "Audit worker")
            row = team.assign(chat_team, member["id"], "Do work")
            worker = threading.Thread(target=team.tick)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                team.cancel(row["id"])
                self.assertEqual(team.retry(row["id"])["queued"], row["id"])
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.db.team_task(row["id"])["status"], "queued")
            self.assertEqual(team.tick(), 1)
        self.assertEqual(self.db.team_task(row["id"])["status"], "done")

    def test_second_cancel_withdraws_pending_retry(self):
        entered, release = threading.Event(), threading.Event()
        def model(*args, **kwargs):
            entered.set()
            release.wait(8)
            return {"reply": "Task output"}
        with patch.object(team, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", side_effect=model):
            chat_team = self.db.team_add("Chat queue", "custom")["id"]
            member = team.add_member(chat_team, "Audit worker")
            row = team.assign(chat_team, member["id"], "Do work")
            worker = threading.Thread(target=team.tick)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                team.cancel(row["id"])
                team.retry(row["id"])
                team.cancel(row["id"])
            finally:
                release.set()
                worker.join(10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(self.db.team_task(row["id"])["status"], "canceled")
            self.assertEqual(team.tick(), 0)

    def test_deleted_project_schedule_cannot_publish_late_output(self):
        row = self.db.schedule_save({"name": "Report", "project_id": self.pid, "assistant_id": "builtin-steward", "prompt": "Report"})
        entered, release = threading.Event(), threading.Event()
        completed = []
        def model(*args, **kwargs):
            entered.set()
            release.wait(8)
            return {"reply": "Late output"}
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "run_agent", side_effect=model):
            worker = threading.Thread(target=lambda: completed.append(schedule.run_once(row)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.db.delete_task(self.pid)
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(completed[0]["status"], "failed")
        self.assertFalse(any(r["schedule_id"] == row["id"] for r in self.db.schedule_runs()))
        self.assertEqual(asset_service.list_assets(self.db, self.pid), [])

    def test_schedule_provider_cancellation_check_is_thread_safe(self):
        row = self.db.schedule_save({"name": "Report", "project_id": self.pid, "assistant_id": "builtin-steward", "prompt": "Report"})
        def model(*args, **kwargs):
            if kwargs.get("on_delta"):
                kwargs["on_delta"]("Streaming report")
            return {"content": "Streaming report"}
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "_chat_raw", side_effect=model):
            result = schedule.run_once(row, trigger="manual")
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("Streaming report", Path(result["output_path"]).read_text(encoding="utf-8"))

    def test_write_pipeline_resume_keeps_review_and_revision(self):
        self.resume_write_pipeline()

    def test_legacy_write_pipeline_checkpoint_resumes_review(self):
        self.resume_write_pipeline(legacy=True)

    def test_legacy_writer_interruption_after_file_write_still_reaches_review(self):
        self.resume_write_pipeline(legacy=True, interrupt_writer=True)

    def resume_write_pipeline(self, legacy=False, interrupt_writer=False):
        workflow.doc_write(self.db, self.pid, "outline", "# Outline\n## Introduction")
        self.layout(["write"])
        run = self.manager.create_run(self.pid, "pipeline", self.db)
        self.db.job_checkpoint = {}
        def save(**values):
            self.db.job_checkpoint.update(values)
            self.db.update_async_task(run.run_id, checkpoint=self.db.job_checkpoint)
        self.db.save_agent_checkpoint = save
        def write_call(text):
            return {"tool_calls": [{"id": "write", "type": "function", "function": {
                "name": "doc_write", "arguments": json.dumps({"markdown": text})}}]}
        initial = [{"content": "Material"}, write_call("DRAFT_BEFORE_RESTART")]
        if not interrupt_writer:
            initial.append({"content": "Draft saved"})
        initial.append(SystemExit("Simulated process stop"))
        with patch.object(workflow, "agent_profile", return_value={"permission": "workspace_write"}), \
                patch.object(workflow, "_chat_raw", side_effect=initial):
            with self.assertRaises(SystemExit):
                workflow_runners.run_write(self.db, self.pid, "请开始写作")
        self.assertEqual(workflow.doc_read(self.db, self.pid, "write")["markdown"].strip(), "DRAFT_BEFORE_RESTART")
        with workflow.WorkflowDB(self.db.path) as restored:
            restored.job_checkpoint = restored.get_async_task(run.run_id)["checkpoint"]
            self.assertEqual(restored.job_checkpoint["write_mode"], "pipeline")
            if legacy:
                restored.job_checkpoint.pop("write_mode")
            restored.resuming = True
            remaining = ([{"content": "Draft saved"}] if interrupt_writer else []) + [
                {"content": "- Revise introduction"}, write_call("FINAL_AFTER_REVIEW"), {"content": "Final saved"}]
            with patch.object(workflow, "agent_profile", return_value={"permission": "workspace_write"}), \
                    patch.object(workflow, "_chat_raw", side_effect=remaining) as call:
                result = workflow_runners.run_write(restored, self.pid, "请开始写作")
            self.assertEqual(call.call_count, len(remaining))
            self.assertIn("流水线完成", result["reply"])
            self.assertEqual(workflow.doc_read(restored, self.pid, "write")["markdown"].strip(), "FINAL_AFTER_REVIEW")


if __name__ == "__main__":
    unittest.main()
