# -*- coding: utf-8 -*-
"""项目文件夹（三栏页第 11 项）：列目录、两道白名单闸、交给某一步的对话。

不联网、不调模型；数据全在临时目录里。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))

import paths
import workflow
from workflow_converters import file_hand, tree_list


class FilesPanelTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="rw-files-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.old_db = paths.DB_PATH
        paths.DB_PATH = self.root / "test.sqlite3"        # 白名单（project_roots）从这里读，别碰真库
        self.addCleanup(setattr, paths, "DB_PATH", self.old_db)
        self.db = workflow.WorkflowDB(paths.DB_PATH)
        self.addCleanup(self.db.close)
        self.tid = self.db.create_task("文件面板")
        self.proj = self.root / "proj"
        (self.proj / "论文正文").mkdir(parents=True)
        (self.proj / "论文正文" / "正文.md").write_text("# 正文\n第一段", encoding="utf-8")
        (self.proj / "论文正文" / "引用台账.md").write_text("- [1] Left2025", encoding="utf-8")
        (self.proj / "_work").mkdir()                     # 中间产物夹：该被跳过
        (self.proj / "~$临时.docx").write_text("x", encoding="utf-8")
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(self.proj), self.tid))
        self.db.db.commit()

    def test_root_lists_dirs_first_and_skips_work_dirs(self):
        d = tree_list(self.db, self.tid)
        self.assertEqual([r["name"] for r in d["rows"]], ["论文正文"])   # _work / ~$ 文件不算
        self.assertTrue(d["rows"][0]["dir"])
        self.assertEqual(d["rows"][0]["count"], 2)

    def test_sub_dir_lists_files_newest_first(self):
        import os
        base = self.proj / "论文正文"
        os.utime(base / "引用台账.md", (2_000_000_000, 2_000_000_000))    # 定死 mtime，别靠写盘先后
        os.utime(base / "正文.md", (1_900_000_000, 1_900_000_000))
        d = tree_list(self.db, self.tid, "论文正文")
        self.assertEqual(d["rel"], "论文正文")
        self.assertEqual([r["name"] for r in d["rows"]], ["引用台账.md", "正文.md"])
        self.assertEqual(d["rows"][0]["rel"], "论文正文/引用台账.md")
        self.assertTrue(d["rows"][0]["path"].endswith("引用台账.md"))

    def test_escape_to_sibling_project_is_refused(self):
        other = self.root / "别的项目"
        other.mkdir()
        self.db.create_task("别的项目", "", "")
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE title='别的项目'", (str(other),))
        self.db.db.commit()
        # 同级项目也在白名单里 → 过得了 _safe_path，必须靠 is_relative_to(root) 那道闸拦住
        with self.assertRaises(ValueError):
            tree_list(self.db, self.tid, "../别的项目")

    def test_hand_writes_into_that_step_scope_only(self):
        got = file_hand(self.db, self.tid, "论文正文/正文.md", "write", "按台账补第 4 条引用")
        self.assertEqual(got["who"], "写作助手")
        msgs = self.db.messages(self.tid, "write")
        self.assertEqual(len(msgs), 1)
        self.assertIn("论文正文/正文.md", msgs[0]["content"])
        self.assertIn("按台账补第 4 条引用", msgs[0]["content"])
        self.assertEqual(self.db.messages(self.tid, "review"), [])

    def test_hand_rejects_unknown_step_and_missing_file(self):
        with self.assertRaises(ValueError):
            file_hand(self.db, self.tid, "论文正文/正文.md", "nope")
        with self.assertRaises(ValueError):
            file_hand(self.db, self.tid, "论文正文/没有这个.md", "write")


if __name__ == "__main__":
    unittest.main()
