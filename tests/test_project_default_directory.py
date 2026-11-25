"""New default roots use project titles without adopting existing project data."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import tests
import fileguard
import paths
from research_core import ResearchDB
import workflow_db


class ProjectDefaultDirectoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rw-default-root-", ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        data = self.root / "app-data"
        for module, values in (
            (paths, {"DATA": data}),
            (fileguard, {"DATA": data, "SNAP": data / "backups", "AUDIT": data / "audit.jsonl"}),
            (workflow_db, {"DATA": data, "PAPERS": data / "papers", "NOTES": data / "notes", "OUT": data / "out"}),
        ):
            override = patch.multiple(module, **values)
            override.start()
            self.addCleanup(override.stop)
        low = patch("lowbox.mark_low", return_value=True)
        low.start()
        self.addCleanup(low.stop)
        self.db_path = self.root / "database-location" / "research.sqlite3"
        self.db = workflow_db.WorkflowDB(self.db_path)
        self.addCleanup(self.db.close)
        self.projects = self.db_path.parent / "projects"

    def test_chinese_title_is_the_default_directory_under_the_actual_database(self):
        pid = self.db.create_task("固态电解质文献综述")
        expected = self.projects / "固态电解质文献综述"
        self.assertEqual(self.db.base_dir(pid), expected)
        self.assertEqual(self.db.summary(pid)["task"]["base_dir"], str(expected))
        self.assertTrue((expected / "README.md").read_text(encoding="utf-8").startswith("# 固态电解质文献综述\n"))
        self.assertFalse((paths.DATA / "projects").exists())

    def test_windows_invalid_characters_are_sanitized_and_collide_safely(self):
        for index, character in enumerate('<>:"/\\|?*\x00\x01\x1f', start=1):
            with self.subTest(character=repr(character)):
                pid = self.db.create_task("A" + character + "B")
                expected = "A_B" if index == 1 else f"A_B ({index})"
                self.assertEqual(self.db.base_dir(pid).name, expected)
                self.assertEqual(self.db.summary(pid)["task"]["title"], "A" + character + "B")

    def test_windows_reserved_names_are_safe_even_with_extensions(self):
        cases = (
            ("CON", "_CON"), ("con.txt", "_con.txt"), ("PRN", "_PRN"),
            ("AUX", "_AUX"), ("NUL", "_NUL"), ("COM1", "_COM1"),
            ("Lpt9.csv", "_Lpt9.csv"), ("COM¹", "_COM¹"), ("LPT².md", "_LPT².md"),
            ("CONIN$", "_CONIN$"), ("CONOUT$", "_CONOUT$"), ("CLOCK$", "_CLOCK$"),
            ("AUX .txt", "_AUX .txt"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                pid = self.db.create_task(title)
                self.assertEqual(self.db.base_dir(pid).name, expected)
                self.assertTrue(self.db.base_dir(pid).is_dir())

    def test_trailing_dots_spaces_and_empty_sanitized_names(self):
        for title, expected in (
            ("  Review .  ", "Review"), ("\tWorking title\n", "Working title"),
            ("...", "未命名项目"), ("..", "未命名项目 (2)"),
        ):
            with self.subTest(title=title):
                pid = self.db.create_task(title)
                self.assertEqual(self.db.base_dir(pid).name, expected)

    def test_long_unicode_names_are_bounded_without_splitting_surrogate_pairs(self):
        pid = self.db.create_task("材料" * 100)
        self.assertEqual(self.db.base_dir(pid).name, "材料" * 40)
        pid = self.db.create_task("x" * 79 + "😀end")
        self.assertEqual(self.db.base_dir(pid).name, "x" * 79)

    def test_existing_empty_or_nonempty_directory_is_never_adopted(self):
        occupied = self.projects / "Review"
        occupied.mkdir(parents=True)
        empty = self.projects / "Review (2)"
        empty.mkdir()
        marker = occupied / "existing.txt"
        marker.write_text("Other project data", encoding="utf-8")
        first = self.db.create_task("Review")
        second = self.db.create_task("Review")
        self.assertEqual(self.db.base_dir(first), self.projects / "Review (3)")
        self.assertEqual(self.db.base_dir(second), self.projects / "Review (4)")
        self.assertEqual(marker.read_text(encoding="utf-8"), "Other project data")
        self.assertEqual(list(empty.iterdir()), [])
        self.assertFalse((occupied / "README.md").exists())

    def test_existing_file_is_never_replaced(self):
        self.projects.mkdir()
        occupied = self.projects / "Report"
        occupied.write_text("Keep this file", encoding="utf-8")
        pid = self.db.create_task("Report")
        self.assertEqual(self.db.base_dir(pid), self.projects / "Report (2)")
        self.assertEqual(occupied.read_text(encoding="utf-8"), "Keep this file")

    def test_directory_created_by_a_competitor_during_reservation_is_not_adopted(self):
        occupied = self.projects / "Race"
        mkdir = Path.mkdir

        def competing_mkdir(path, *args, **kwargs):
            if path == occupied and not os.path.lexists(occupied):
                mkdir(occupied)
                (occupied / "owner.txt").write_text("Concurrent owner", encoding="utf-8")
            return mkdir(path, *args, **kwargs)

        with patch.object(Path, "mkdir", competing_mkdir):
            pid = self.db.create_task("Race")
        self.assertEqual(self.db.base_dir(pid), self.projects / "Race (2)")
        self.assertEqual((occupied / "owner.txt").read_text(encoding="utf-8"), "Concurrent owner")
        self.assertFalse((occupied / "README.md").exists())

    def test_existing_and_broken_directory_links_are_not_followed(self):
        self.projects.mkdir()
        target = self.root / "external"
        target.mkdir()
        try:
            (self.projects / "Linked").symlink_to(target, target_is_directory=True)
            (self.projects / "Linked (2)").symlink_to(self.root / "missing", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks are unavailable: {exc}")
        pid = self.db.create_task("Linked")
        self.assertEqual(self.db.base_dir(pid), self.projects / "Linked (3)")
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue((self.projects / "Linked (2)").is_symlink())

    def test_missing_registered_or_retained_roots_are_not_reused(self):
        registered = self.projects / "Research"
        old = ResearchDB.create_task(self.db, "Earlier project", base_dir=str(registered))
        retained = self.projects / "Research (2)"
        self.db.db.execute("INSERT INTO deleted_project_locations VALUES (?,?,?,?)",
                           ("deleted-project", "Earlier deleted project", str(retained), "2026-01-01"))
        self.db.db.commit()
        pid = self.db.create_task("Research")
        self.assertEqual(self.db.base_dir(pid), self.projects / "Research (3)")
        self.assertEqual(self.db.base_dir(old), registered)
        self.assertFalse(registered.exists())
        self.assertFalse(retained.exists())

    def test_default_root_cannot_enclose_another_registered_root(self):
        registered = self.projects / "Research" / "existing-child"
        ResearchDB.create_task(self.db, "Child project", base_dir=str(registered))
        pid = self.db.create_task("Research")
        self.assertEqual(self.db.base_dir(pid), self.projects / "Research (2)")
        self.assertFalse(registered.parent.exists())

    def test_default_parent_inside_another_project_is_rejected(self):
        ResearchDB.create_task(self.db, "Owner", base_dir=str(self.projects))
        with self.assertRaises(ValueError):
            self.db.create_task("New project")
        self.assertFalse(self.projects.exists())
        self.assertEqual([row["title"] for row in self.db.tasks()], ["Owner"])

    def test_concurrent_same_title_creates_have_distinct_roots(self):
        barrier = threading.Barrier(2)

        def create():
            with workflow_db.WorkflowDB(self.db_path) as db:
                barrier.wait(timeout=10)
                pid = db.create_task("Shared title")
                return pid, db.base_dir(pid)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            created = [future.result(timeout=30) for future in futures]
        self.assertEqual({root for _, root in created},
                         {self.projects / "Shared title", self.projects / "Shared title (2)"})
        self.assertEqual(len({pid for pid, _ in created}), 2)
        for pid, root in created:
            self.assertEqual(self.db.base_dir(pid), root)
            self.assertTrue((root / "README.md").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive filesystem behavior")
    def test_windows_case_collision_allocates_a_new_directory(self):
        first = self.db.create_task("Review")
        second = self.db.create_task("review")
        self.assertEqual(self.db.base_dir(first).name, "Review")
        self.assertEqual(self.db.base_dir(second).name, "review (2)")

    def test_saved_legacy_base_dir_stays_stable_after_title_update_and_reopen(self):
        legacy = self.projects / "0123456789abcdef"
        legacy.mkdir(parents=True)
        marker = legacy / "old.txt"
        marker.write_text("Existing content", encoding="utf-8")
        pid = ResearchDB.create_task(self.db, "Old title", base_dir=str(legacy))
        self.db.db.execute("UPDATE research_tasks SET title=? WHERE id=?", ("New title", pid))
        self.db.db.commit()
        with workflow_db.WorkflowDB(self.db_path) as reopened:
            self.assertEqual(reopened.base_dir(pid), legacy)
            chat = reopened.create_chat("Continue research", task_id=pid)
            self.assertEqual(reopened.chat(chat)["task_id"], pid)
        self.assertEqual(marker.read_text(encoding="utf-8"), "Existing content")
        self.assertFalse((self.projects / "New title").exists())

    def test_legacy_project_without_root_gets_title_directory_when_bound_to_chat(self):
        pid = ResearchDB.create_task(self.db, "旧项目标题")
        chat = self.db.create_chat("继续研究", task_id=pid)
        expected = self.projects / "旧项目标题"
        self.assertEqual(self.db.base_dir(pid), expected)
        self.assertEqual(self.db.chat(chat)["task_id"], pid)
        self.assertTrue((expected / "README.md").is_file())
        self.assertFalse((self.projects / pid).exists())

    def test_explicit_directory_is_respected(self):
        chosen = self.root / "user-selected-location"
        chosen.mkdir()
        marker = chosen / "keep.txt"
        marker.write_text("User material", encoding="utf-8")
        pid = self.db.create_task("A different project title", base_dir=str(chosen))
        self.assertEqual(self.db.base_dir(pid), chosen)
        self.assertEqual(marker.read_text(encoding="utf-8"), "User material")
        self.assertFalse(self.projects.exists())

    def test_memory_database_has_no_default_disk_root(self):
        with workflow_db.WorkflowDB(":memory:") as db:
            pid = db.create_task("Memory project")
            self.assertIsNone(db.base_dir(pid))
            self.assertEqual(db.summary(pid)["task"]["title"], "Memory project")
        self.assertFalse(self.projects.exists())

    def test_empty_title_does_not_reserve_a_directory(self):
        with self.assertRaisesRegex(ValueError, "task title is required"):
            self.db.create_task("   ")
        self.assertFalse(self.projects.exists())
        self.assertEqual(self.db.tasks(), [])


if __name__ == "__main__":
    unittest.main()
