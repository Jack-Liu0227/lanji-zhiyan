"""Keep legacy project links usable while moving default roots to title names."""
import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import asset_service
import paths
import project_data
import workflow_db


@unittest.skipUnless(os.name == "nt", "Windows directory junctions preserve legacy paths")
class ProjectDirectoryAlignmentTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rw-directory-align-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        data = self.root / "app-data"
        overrides = patch.multiple(
            paths, DATA=data, BACKUP=data / "backups", CACHE=data / "cache",
            LOWTMP=data / "cache" / "lowtmp", PAPERS=data / "papers", NOTES=data / "notes",
            OUT=data / "out", UPLOADS=data / "uploads", REF=data / "ref",
            SNAP=data / "backups" / ".rwbak",
        )
        overrides.start()
        self.addCleanup(overrides.stop)
        self.db_path = self.root / "database-location" / "research.sqlite3"
        self.db = workflow_db.WorkflowDB(self.db_path)
        self.addCleanup(self.db.close)
        patcher = patch("lowbox.mark_low", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _legacy_project(self, title="Materials review"):
        task_id = self.db.create_task(title)
        created_root = self.db.base_dir(task_id)
        old_root = self.db.path.resolve().parent / "projects" / task_id
        os.replace(created_root, old_root)
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(old_root), task_id))
        self.db.db.commit()
        report = old_root / "literature_review.md"
        report.write_text("Accepted report", encoding="utf-8")
        asset = asset_service.register_file(
            self.db, report, project_id=task_id, kind="document", title=report.stem
        )
        return task_id, old_root, report, asset

    def test_moves_legacy_root_updates_asset_uri_and_keeps_old_path(self):
        task_id, old_root, report, asset = self._legacy_project()

        result = project_data.align_legacy_default_directory(self.db, task_id)

        new_root = Path(result["base_dir"])
        self.assertEqual(new_root.name, "Materials review")
        self.assertEqual(self.db.base_dir(task_id), new_root)
        self.assertTrue(old_root.is_junction())
        self.assertEqual((old_root / report.name).read_text(encoding="utf-8"), "Accepted report")
        self.assertEqual(Path(asset_service.get(self.db, asset["id"])["canonical_uri"]), new_root / report.name)
        self.assertEqual((new_root / report.name).read_text(encoding="utf-8"), "Accepted report")

    def test_existing_title_directory_is_preserved_and_suffix_is_used(self):
        task_id, old_root, _, _ = self._legacy_project("Research")
        occupied = old_root.parent / "Research"
        occupied.mkdir()
        marker = occupied / "keep.txt"
        marker.write_text("Other data", encoding="utf-8")

        result = project_data.align_legacy_default_directory(self.db, task_id)

        self.assertEqual(Path(result["base_dir"]).name, "Research (2)")
        self.assertEqual(marker.read_text(encoding="utf-8"), "Other data")
        self.assertTrue(old_root.is_junction())

    def test_explicit_directory_is_not_renamed(self):
        explicit = self.root / "chosen-location"
        explicit.mkdir()
        task_id = workflow_db.ResearchDB.create_task(self.db, "Explicit project", base_dir=str(explicit))

        result = project_data.align_legacy_default_directory(self.db, task_id)

        self.assertFalse(result["migrated"])
        self.assertEqual(self.db.base_dir(task_id), explicit)

    def test_running_project_is_not_moved(self):
        task_id, old_root, _, _ = self._legacy_project()
        with patch("project_data.preview", return_value={"running": True}):
            with self.assertRaises(ValueError):
                project_data.align_legacy_default_directory(self.db, task_id)
        self.assertTrue(old_root.is_dir())
        self.assertEqual(self.db.base_dir(task_id), old_root)

    def test_failed_junction_creation_rolls_the_folder_move_back(self):
        task_id, old_root, report, _ = self._legacy_project()
        with patch("_winapi.CreateJunction", side_effect=OSError("junction failed")):
            with self.assertRaisesRegex(OSError, "junction failed"):
                project_data.align_legacy_default_directory(self.db, task_id)
        self.assertTrue(old_root.is_dir())
        self.assertEqual((old_root / report.name).read_text(encoding="utf-8"), "Accepted report")
        self.assertEqual(self.db.base_dir(task_id), old_root)
        self.assertEqual(list(old_root.parent.glob("Materials review*")), [])

    def test_recovers_after_folder_move_before_database_update(self):
        task_id, old_root, report, _ = self._legacy_project()
        destination = old_root.parent / "Materials review"
        os.replace(old_root, destination)
        digest = hashlib.sha256(task_id.encode()).hexdigest()[:16]
        journal = self.db.path.with_name(f".{self.db.path.name}.directory-migration-{digest}.json")
        journal.write_text(json.dumps({"project_id": task_id, "source": str(old_root),
                                       "destination": str(destination)}), encoding="utf-8")

        result = project_data.align_legacy_default_directory(self.db, task_id)

        self.assertTrue(result["migrated"])
        self.assertTrue(old_root.is_junction())
        self.assertEqual((old_root / report.name).read_text(encoding="utf-8"), "Accepted report")
        self.assertFalse(journal.exists())
        self.assertEqual(self.db.base_dir(task_id), destination)

    def test_recovers_when_database_commit_preceded_journal_cleanup(self):
        task_id, old_root, report, asset = self._legacy_project()
        destination = old_root.parent / "Materials review"
        os.replace(old_root, destination)
        import _winapi
        _winapi.CreateJunction(str(destination), str(old_root))
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(destination), task_id))
        self.db.db.commit()
        digest = hashlib.sha256(task_id.encode()).hexdigest()[:16]
        journal = self.db.path.with_name(f".{self.db.path.name}.directory-migration-{digest}.json")
        journal.write_text(json.dumps({"project_id": task_id, "source": str(old_root),
                                       "destination": str(destination)}), encoding="utf-8")

        project_data.align_legacy_default_directory(self.db, task_id)

        self.assertFalse(journal.exists())
        self.assertEqual((old_root / report.name).read_text(encoding="utf-8"), "Accepted report")
        self.assertEqual(Path(asset_service.get(self.db, asset["id"])["canonical_uri"]), destination / report.name)


if __name__ == "__main__":
    unittest.main()
