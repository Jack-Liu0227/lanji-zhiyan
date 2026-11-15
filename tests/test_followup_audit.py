"""Behavioral regressions for the second project audit."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

import asset_service
import project_notes
import paths
import semantic
import space_service
import workflow
import workflow_db
import zotero_sync


class FollowupAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-followup-")
        self.addCleanup(self.tmp.cleanup)
        for replacement in (patch.object(paths, "DATA", Path(self.tmp.name) / "data"),
                            patch.object(workflow_db, "NOTES", Path(self.tmp.name) / "notes")):
            replacement.start()
            self.addCleanup(replacement.stop)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "audit.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Audit")
        self.relative = "笔记/研究计划/note.md"
        for replacement in (
            patch.object(semantic, "config", return_value={"model": "audit", "base": "http://offline.test"}),
            patch.object(semantic, "embed", side_effect=self.embed),
        ):
            replacement.start()
            self.addCleanup(replacement.stop)

    @staticmethod
    def embed(texts, cfg=None, **kwargs):
        return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)

    def test_create_rejects_existing_file_without_changing_content_or_metadata(self):
        saved = project_notes.write(self.db, self.pid, self.relative, "ORIGINAL_NOTE", title="Original", create=True)
        with self.assertRaisesRegex(ValueError, "路径已存在"):
            project_notes.write(self.db, self.pid, self.relative, "REPLACEMENT", title="Replacement", create=True)
        current = project_notes.read(self.db, self.pid, self.relative)
        self.assertEqual(current["content"], "ORIGINAL_NOTE")
        self.assertEqual(current["title"], "Original")
        self.assertEqual(current["content_hash"], saved["content_hash"])

    def test_concurrent_editors_keep_file_database_search_and_hash_consistent(self):
        for other_editor in ("project", "md_notes"):
            with self.subTest(other_editor=other_editor):
                project_notes.write(self.db, self.pid, self.relative, "INITIAL")
                nid = self.db.db.execute("SELECT id FROM md_notes WHERE task_id=?", (self.pid,)).fetchone()[0]
                entered, release, second_started, second_done = (threading.Event() for _ in range(4))
                original = asset_service.atomic_write_text
                errors = []

                def write_file(path, content):
                    result = original(path, content)
                    if content == "FIRST_WRITER":
                        entered.set()
                        if not release.wait(8):
                            raise TimeoutError("Second writer did not start")
                    return result

                def save(first):
                    try:
                        with workflow.WorkflowDB(self.db.path) as db:
                            if first:
                                project_notes.write(db, self.pid, self.relative, "FIRST_WRITER")
                            else:
                                second_started.set()
                                if other_editor == "project":
                                    project_notes.write(db, self.pid, self.relative, "SECOND_WRITER")
                                else:
                                    db.save_md_note(nid, "Other editor", "SECOND_WRITER")
                                second_done.set()
                    except BaseException as exc:
                        errors.append(exc)

                with patch.object(asset_service, "atomic_write_text", side_effect=write_file):
                    first = threading.Thread(target=save, args=(True,))
                    second = threading.Thread(target=save, args=(False,))
                    first.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        second.start()
                        self.assertTrue(second_started.wait(5))
                        self.assertFalse(second_done.wait(.2), "Second save overtook an unfinished save")
                    finally:
                        release.set()
                        first.join(8)
                        if second.ident:
                            second.join(8)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                path = self.db.base_dir(self.pid) / self.relative
                self.assertEqual(path.read_text(encoding="utf-8"), "SECOND_WRITER")
                self.assertEqual(self.db.md_note(nid)["content"], "SECOND_WRITER")
                self.assertEqual(space_service.search_project_space(self.db, self.pid, "SECOND_WRITER")["total"], 1)
                self.assertEqual(space_service.search_project_space(self.db, self.pid, "FIRST_WRITER")["total"], 0)
                asset = self.db.db.execute("SELECT * FROM assets WHERE canonical_uri=?", (str(path.resolve()),)).fetchone()
                self.assertEqual(asset["content_hash"], asset_service.sha256_file(path))

    def test_stale_save_cannot_overwrite_other_editor_or_recreate_deleted_note(self):
        saved = project_notes.write(self.db, self.pid, self.relative, "ORIGINAL")
        project_notes.write(self.db, self.pid, self.relative, "OTHER_EDITOR", expected_hash=saved["content_hash"])
        with self.assertRaisesRegex(ValueError, "其他操作"):
            project_notes.write(self.db, self.pid, self.relative, "STALE", expected_hash=saved["content_hash"])
        latest = project_notes.read(self.db, self.pid, self.relative)
        self.assertEqual(latest["content"], "OTHER_EDITOR")
        project_notes.delete(self.db, self.pid, self.relative)
        with self.assertRaises(ValueError):
            project_notes.write(self.db, self.pid, self.relative, "RESTORED", expected_hash=latest["content_hash"])
        self.assertFalse((self.db.base_dir(self.pid) / self.relative).exists())

    def test_rename_updates_both_note_types_asset_search_and_dashboard(self):
        for kind in ("md_note", "note"):
            with self.subTest(kind=kind):
                if kind == "md_note":
                    project_notes.write(self.db, self.pid, self.relative, "Content to index after naming the note", title="Old title")
                    relative = self.relative
                else:
                    note = self.db.add_note(self.pid, "Old title", "Content to index after naming the note")
                    relative = Path(note["canonical_path"]).relative_to(self.db.base_dir(self.pid)).as_posix()
                semantic.sync(self.db)
                title = "RENAMED_" + kind
                saved = project_notes.write(self.db, self.pid, relative, "# " + title, title=title)
                self.assertEqual(project_notes.read(self.db, self.pid, relative)["title"], title)
                dashboard = project_notes.dashboard(self.db, self.pid)
                item = next(f for g in dashboard["groups"] for f in g["files"] if f["path"] == relative)
                self.assertEqual(item["title"], title)
                self.assertEqual(saved["title"], title)
                self.assertEqual(space_service.search_project_space(self.db, self.pid, title)["total"], 1)

    def test_zotero_multiple_local_claimants_block_all_sync_modes(self):
        zotero_sync.ensure(self.db)
        for doi in ("10.1000/first", "10.1000/second"):
            self.db.cache_results(self.pid, [{"source": "zotero", "title": "Editorial", "doi": doi}])
        self.db.db.execute("INSERT INTO zotero_bindings (task_id,library,collection) VALUES (?,?,?)", (self.pid, "users/123", "ABCDEFGH"))
        self.db.db.execute("INSERT INTO zotero_project_sources VALUES (?,?,?,'all')", (self.pid, 'users/123', 'ABCDEFGH'))
        self.db.db.commit()
        remote = [{"key": "REMOTE01", "version": 1, "data": {"itemType": "journalArticle", "title": "Editorial", "DOI": "", "collections": ["ABCDEFGH"]}}]
        before = self.db.results(self.pid)
        with patch.object(zotero_sync, "config", return_value={"library_type": "users", "library_id": "123"}), \
                patch.object(zotero_sync, "all_pages", return_value=remote), patch.object(zotero_sync, "request") as request:
            for linked in (False, True):
                if linked:
                    for row in before:
                        self.db.db.execute("INSERT OR REPLACE INTO zotero_item_links VALUES (?,?,?,?,?)",
                                           (self.pid, row["id"], "REMOTE01", 1, zotero_sync._hash(row)))
                    self.db.db.commit()
                for mode in ("pull", "push", "merge"):
                    with self.subTest(linked=linked, mode=mode):
                        plan = zotero_sync.sync(self.db, self.pid, mode=mode)
                        self.assertEqual(len(plan["conflicts"]), 2)
                        self.assertEqual(plan["actions"], [])
                        with self.assertRaises(ValueError):
                            zotero_sync.sync(self.db, self.pid, mode=mode, dry_run=False, plan_token=plan["plan_token"])
                        self.assertEqual(self.db.results(self.pid), before)
            request.assert_not_called()

    def test_project_deletion_removes_semantic_hits_and_cached_vectors(self):
        self.db.save_md_note("", "Project note", "ORPHAN_AUDIT_NEEDLE should disappear after project deletion. " * 4, task_id=self.pid)
        kept = self.db.save_md_note("", "Unbound", "KEEP_AUDIT_NEEDLE unbound research note should remain. " * 4)
        semantic.sync(self.db)
        self.assertTrue(semantic.hybrid(self.db, "ORPHAN_AUDIT_NEEDLE")["hits"])
        self.db.delete_task(self.pid)
        for table in ("sem_chunks", "sem_state"):
            self.assertEqual(self.db.db.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (self.pid,)).fetchone()[0], 0)
        self.assertEqual(semantic.lexical(self.db, "ORPHAN_AUDIT_NEEDLE"), [])
        self.assertEqual({h["id"] for h in semantic.search_vector(self.db, "anything")}, {kept["id"]})
        semantic.sync(self.db)
        self.assertEqual(semantic.lexical(self.db, "ORPHAN_AUDIT_NEEDLE"), [])

    def test_bounded_sync_prunes_legacy_orphans_but_keeps_existing_sources(self):
        gone = self.db.save_md_note("", "Old", "ORPHAN_AUDIT_NEEDLE old orphaned note text. " * 4, task_id=self.pid)
        kept = self.db.save_md_note("", "Keep", "KEEP_AUDIT_NEEDLE healthy note text. " * 4, task_id=self.pid)
        semantic.sync(self.db)
        self.db.db.execute("DELETE FROM md_notes WHERE id=?", (gone["id"],))
        self.db.db.commit()
        semantic.sync(self.db, limit=1)
        self.assertEqual(semantic.lexical(self.db, "ORPHAN_AUDIT_NEEDLE"), [])
        self.assertEqual({h["id"] for h in semantic.lexical(self.db, "KEEP_AUDIT_NEEDLE")}, {kept["id"]})
        self.assertIsNone(self.db.db.execute("SELECT 1 FROM sem_state WHERE source_id=?", (gone["id"],)).fetchone())

    def test_embedding_response_cannot_resurrect_deleted_project(self):
        self.db.save_md_note("", "Delete while indexing", "ORPHAN_AUDIT_NEEDLE pending embedding data. " * 4, task_id=self.pid)
        def delete_during_embed(texts, cfg):
            with workflow.WorkflowDB(self.db.path) as other:
                other.delete_task(self.pid)
            return self.embed(texts, cfg)
        with patch.object(semantic, "embed", side_effect=delete_during_embed):
            semantic.sync(self.db)
        for table in ("sem_chunks", "sem_state", "sem_fts"):
            self.assertEqual(self.db.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_project_scope_precedes_fts_and_short_query_limits(self):
        other = self.db.create_task("Other project")
        for i in range(121):
            self.db.db.execute("INSERT INTO md_notes (id,title,content,task_id,paper_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                               (f"foreign-{i}", "Other", "scopeaudit 掺杂 " * 12, other, "", "2026-10-24", "2026-10-24"))
        self.db.db.execute("INSERT INTO md_notes (id,title,content,task_id,paper_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                           ("target", "Target", "scopeaudit 掺杂 " + "Background information. " * 20, self.pid, "", "2026-10-24", "2026-10-24"))
        self.db.db.commit()
        semantic.sync(self.db)
        for query in ("scopeaudit", "掺杂"):
            with self.subTest(query=query), patch.object(semantic, "embed", side_effect=semantic.EmbedError("offline")):
                result = semantic.hybrid(self.db, query, [self.pid])
                self.assertEqual(result["mode"], "lexical")
                self.assertEqual([h["id"] for h in result["hits"]], ["target"])
                self.assertEqual(semantic.lexical(self.db, query, []), [])


if __name__ == "__main__":
    unittest.main()
