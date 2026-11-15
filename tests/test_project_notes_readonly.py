"""Existing protected projects remain readable without changing their files."""
from pathlib import Path
import tempfile
import unittest

import project_notes
import workflow


class ReadOnlyProjectNotesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rw-readonly-notes-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "notes.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Legacy project")
        self.folder = self.root / "zotero" / "existing-project"
        self.note = self.folder / "笔记/研究计划/existing.md"
        self.note.parent.mkdir(parents=True)
        (self.root / "zotero/zotero.sqlite").touch()
        self.note.write_text("# Existing note\n\nREAD_ONLY_EVIDENCE", encoding="utf-8")
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(self.folder), self.project))
        self.db.db.commit()

    def snapshot(self):
        return {p.relative_to(self.folder).as_posix(): p.read_bytes()
                for p in self.folder.rglob("*") if p.is_file()}

    def test_dashboard_lists_protected_project_without_creating_or_moving_files(self):
        legacy = self.folder / "笔记/legacy.md"
        legacy.write_text("Legacy content", encoding="utf-8")
        self.db.db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)",
                           ("legacy", self.project, "", "精读 · legacy.pdf", "Legacy content", "2026-10-24"))
        self.db.db.commit()
        before = self.snapshot()
        result = project_notes.dashboard(self.db, self.project)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.folder / "笔记/文献精读笔记").exists())

    def test_read_protected_note_returns_content_and_read_only_status(self):
        before = self.snapshot()
        result = project_notes.read(self.db, self.project, "笔记/研究计划/existing.md")
        self.assertIn("READ_ONLY_EVIDENCE", result["content"])
        self.assertTrue(result["read_only"])
        self.assertEqual(self.snapshot(), before)

    def test_reading_empty_protected_project_does_not_create_notes_directory(self):
        self.note.unlink()
        self.note.parent.rmdir()
        self.note.parent.parent.rmdir()
        result = project_notes.dashboard(self.db, self.project)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["total"], 0)
        self.assertFalse((self.folder / "笔记").exists())

    def test_protected_note_mutations_remain_blocked(self):
        before = self.snapshot()
        operations = [
            lambda: project_notes.write(self.db, self.project, "笔记/研究计划/existing.md", "overwrite"),
            lambda: project_notes.write(self.db, self.project, "笔记/new/fresh.md", "new"),
            lambda: project_notes.create_folder(self.db, self.project, "笔记/new"),
            lambda: project_notes.delete(self.db, self.project, "笔记/研究计划/existing.md"),
            lambda: project_notes.delete(self.db, self.project, "笔记/研究计划"),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "Zotero"):
                operation()
        self.assertEqual(self.snapshot(), before)
