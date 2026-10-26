from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _pdf_fixture import write_text_pdf
import project_library
import service_settings
import workflow
import zotero_sync


class ProjectLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Library", base_dir=str(Path(self.tmp.name) / "project"))

    def result(self, source="zotero"):
        return self.db.cache_result(self.pid, {"source": source, "title": "Original title", "doi": "10.123/test"})

    def remote(self, title="Remote title", version=1):
        return [{"key": "REMOTE01", "version": version, "data": {"key": "REMOTE01", "version": version, "itemType": "journalArticle", "title": title, "DOI": "10.123/test", "collections": ["COLL0001"], "tags": [{"tag": zotero_sync.project_tag(self.pid)}]}}]

    def bind(self):
        zotero_sync.ensure(self.db)
        self.db.db.execute("INSERT INTO zotero_bindings VALUES (?,?,?,?)", (self.pid, "users/123", "COLL0001", ""))
        self.db.db.commit()

    def test_same_doi_has_one_identity_and_pdf_metadata_tracks_updates(self):
        rid, alias = self.result(), self.result("crossref")
        pdf = write_text_pdf(Path(self.tmp.name) / "paper.pdf", ["Test text"])
        paper = self.db.ingest_pdf(self.pid, pdf)
        project_library.link_pdf(self.db, self.pid, rid, paper["id"])
        rows = project_library.items(self.db, self.pid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]["result_ids"]), {rid, alias})
        self.assertTrue(rows[0]["has_pdf"])
        project_library.update_record(self.db, self.pid, rid, {"title": "Shared new title"})
        self.assertEqual(self.db.result(alias)["title"], "Shared new title")
        self.assertEqual(self.db.papers_of(self.pid)[0]["title"], "Shared new title")
        self.assertEqual(project_library.items(self.db, self.pid)[0]["title"], "Shared new title")
        foreign = self.db.create_task("Other")
        with self.assertRaisesRegex(ValueError, "不属于"):
            project_library.update_record(self.db, foreign, rid, {"title": "wrong project"})

    def test_auto_pull_updates_shared_rows_and_conflicts_preserve_both_sides(self):
        self.bind()
        rid = self.result()
        config = {"library_type": "users", "library_id": "123", "api_key": "test-only"}
        with patch.object(zotero_sync, "config", return_value=config), patch.object(zotero_sync, "all_pages", return_value=self.remote()):
            zotero_sync.configure_auto(self.db, self.pid, {"enabled": True, "mode": "merge"})
            self.assertEqual(zotero_sync.auto_tick(self.db), 1)
            self.assertEqual(zotero_sync.auto_tick(self.db), 0)
        self.assertEqual(self.db.result(rid)["title"], "Remote title")
        self.assertEqual(project_library.items(self.db, self.pid)[0]["zotero_key"], "REMOTE01")
        project_library.update_record(self.db, self.pid, rid, {"title": "Local edit"})
        service_settings.write(self.db, "zotero_auto", {**zotero_sync.auto_state(self.db, self.pid), "next_at": 0}, self.pid)
        with patch.object(zotero_sync, "config", return_value=config), patch.object(zotero_sync, "all_pages", return_value=self.remote("Remote edit", 2)), patch.object(zotero_sync, "request") as remote_write:
            zotero_sync.auto_tick(self.db)
        remote_write.assert_not_called()
        self.assertEqual(zotero_sync.auto_state(self.db, self.pid)["status"], "conflict")
        self.assertEqual(self.db.result(rid)["title"], "Local edit")

    def test_sync_is_opt_in_and_invalid_project_or_settings_are_rejected(self):
        self.bind()
        with patch.object(zotero_sync, "all_pages") as fetch:
            self.assertEqual(zotero_sync.auto_tick(self.db), 0)
        fetch.assert_not_called()
        for settings in ({"enabled": "true"}, {"enabled": True, "interval_seconds": 1}, {"enabled": True, "mode": "delete"}):
            with self.assertRaises(ValueError):
                zotero_sync.configure_auto(self.db, self.pid, settings)
