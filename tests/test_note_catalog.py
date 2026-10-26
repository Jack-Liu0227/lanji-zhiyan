import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))
import workflow
import note_catalog
import project_notes


class NoteCatalogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "db.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("notes", base_dir=str(self.root))

    def test_five_defaults_and_legacy_file_keeps_path(self):
        relative = "笔记/文献精读笔记/old.md"
        path = self.root / relative
        path.parent.mkdir(parents=True)
        path.write_text("# Evidence", encoding="utf-8")
        board = project_notes.dashboard(self.db, self.pid)
        self.assertEqual(len(board["catalog"]["categories"]), 5)
        self.assertEqual(board["items"][0]["category_id"], "literature")
        self.assertEqual(board["items"][0]["tags"], ["精读"])
        self.assertEqual(path.read_text(encoding="utf-8"), "# Evidence")

    def test_two_levels_rename_move_and_default_snapshot(self):
        catalog = note_catalog.mutate(self.db, self.pid, {"name": "复现", "parent_id": "experiment_analysis"})
        child = catalog["categories"][-1]
        with self.assertRaises(ValueError):
            note_catalog.mutate(self.db, self.pid, {"name": "三级", "parent_id": child["id"]})
        with self.assertRaises(ValueError):
            note_catalog.mutate(self.db, self.pid, {"name": "复现", "parent_id": "experiment_analysis"})
        saved = project_notes.write(self.db, self.pid, "", "body", metadata={"category_id": child["id"], "tags": []})
        note_catalog.mutate(self.db, self.pid, {"id": child["id"], "name": "计算复现", "parent_id": ""})
        self.assertTrue((self.root / saved["path"]).exists())
        note_catalog.mutate(self.db, self.pid, {"action": "set_default"})
        other = self.root.parent / "other"
        other.mkdir()
        pid2 = self.db.create_task("other", base_dir=str(other))
        note_catalog.mutate(self.db, self.pid, {"id": child["id"], "name": "再次改名", "parent_id": ""})
        self.assertEqual(note_catalog.category(note_catalog.load(self.db, pid2), child["id"])["name"], "计算复现")

    def test_metadata_survives_old_editor_and_explicit_empty_tags(self):
        relative = "笔记/文献精读笔记/old.md"
        saved = project_notes.write(self.db, self.pid, relative, "# Note", metadata={"tags": [" #精读 ", "精读", "方法"]})
        saved = project_notes.write(self.db, self.pid, relative, "# Update", metadata={"tags": []}, expected_hash=saved["content_hash"])
        row = self.db.db.execute("SELECT id FROM md_notes WHERE task_id=?", (self.pid,)).fetchone()
        self.db.save_md_note(row["id"], "new title", "# Old editor")
        note = project_notes.read(self.db, self.pid, relative)
        self.assertEqual(note["tags"], [])
        self.assertEqual(note["category_id"], "literature")
        self.assertIn("Old editor", note["body"])
        self.assertEqual(project_notes.dashboard(self.db, self.pid)["items"][0]["tags"], [])

    def test_reorder_swaps_only_neighbor_and_preserves_hierarchy(self):
        original = note_catalog.load(self.db, self.pid)
        data = note_catalog.mutate(self.db, self.pid, {"action":"reorder", "id":"experiment_analysis", "direction":"up"})
        order = [c['id'] for c in sorted(data['categories'], key=lambda c:c['sort_order'])]
        self.assertEqual(order, ['planning','experiment_analysis','literature','writing_submission','prompt_templates'])
        self.assertEqual({c['id']:c['folder'] for c in data['categories']}, {c['id']:c['folder'] for c in original['categories']})

    def test_delete_rehomes_notes_without_removing_files(self):
        catalog = note_catalog.mutate(self.db, self.pid, {"name": "Custom"})
        key = catalog["categories"][-1]["id"]
        saved = project_notes.write(self.db, self.pid, "", "keep", metadata={"category_id": key, "tags": ["重要"]})
        with self.assertRaises(ValueError):
            note_catalog.mutate(self.db, self.pid, {"action": "delete", "id": key})
        note_catalog.mutate(self.db, self.pid, {"action": "delete", "id": key, "destination_id": "planning"})
        note = project_notes.read(self.db, self.pid, saved["path"])
        self.assertEqual(note["category_id"], "planning")
        self.assertEqual(note["tags"], ["重要"])
        self.assertEqual(project_notes.dashboard(self.db, self.pid)["total"], 1)

    def test_deleted_default_does_not_reappear_and_conflict_rejected(self):
        before = note_catalog.load(self.db, self.pid)
        note_catalog.mutate(self.db, self.pid, {"action": "delete", "id": "literature"})
        self.assertIsNone(note_catalog.category(note_catalog.load(self.db, self.pid), "literature"))
        with self.assertRaises(ValueError):
            note_catalog.mutate(self.db, self.pid, {"name": "stale", "expected_hash": before["content_hash"]})

    def test_batch_tags_and_category(self):
        saved = [project_notes.write(self.db, self.pid, "", "body", metadata={"category_id": "planning", "tags": ["Old"]}) for _ in range(2)]
        project_notes.batch_update(self.db, self.pid, {"paths": [n["path"] for n in saved], "rename_tags": {"Old": "New"}, "add_tags": ["方法"], "category_id": "literature"})
        for note in project_notes.dashboard(self.db, self.pid)["items"]:
            self.assertEqual(note["tags"], ["New", "方法"])
            self.assertEqual(note["category_id"], "literature")
