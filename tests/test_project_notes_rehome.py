# -*- coding: utf-8 -*-
"""项目笔记分类落盘：精读产出的 .md 进「文献精读笔记」，笔记根目录下的旧文件按标题搬过去。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(Path(_ROOT) / "packages" / _n))

import project_notes  # noqa: E402
import workflow  # noqa: E402


class ProjectNoteCategoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "t.sqlite3"
        os.environ["RESEARCH_WORKBENCH_DB"] = str(db_path)
        self.base = Path(self.tmp.name) / "proj"
        (self.base / "笔记").mkdir(parents=True)
        self.db = workflow.WorkflowDB(db_path)
        self.tid = self.db.create_task("笔记项目", "q", "Ga2O3", str(self.base))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_add_note_lands_in_read_category(self):
        note = self.db.add_note(self.tid, "精读 · a.pdf", "# x\n")
        path = Path(note["canonical_path"])
        self.assertEqual(path.parent.name, "文献精读笔记", path)
        self.assertTrue(path.is_file())

    def test_review_notes_use_their_own_category(self):
        note = self.db.add_note(self.tid, "稿件审查", "# x\n", category="写作投稿")
        self.assertEqual(Path(note["canonical_path"]).parent.name, "写作投稿")

    def test_legacy_root_file_is_rehomed_and_titled(self):
        notes = self.base / "笔记"
        legacy = notes / "deadbeef.md"
        legacy.write_text("# old\n", encoding="utf-8")
        self.db.db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)",
                           ("deadbeef", self.tid, "", "精读 · b.pdf", "# old\n", "2026-10-21T00:00:00"))
        self.db.db.commit()
        data = project_notes.dashboard(self.db, self.tid)
        groups = {g["name"]: g for g in data["groups"]}
        self.assertEqual(groups["未分类"]["count"], 0)
        self.assertEqual(groups["文献精读笔记"]["count"], 1)
        self.assertEqual(groups["文献精读笔记"]["files"][0]["title"], "精读 · b.pdf")
        self.assertTrue((notes / "文献精读笔记" / "deadbeef.md").is_file())
        self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
