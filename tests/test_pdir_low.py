"""pdir 出来的项目子目录必须标成低完整性（Low IL）。

漏标的表现：babeldoc / xelatex 跑在 Low 沙箱里，往 Medium 目录写就是
PermissionError [WinError 5] '_work' → 用户一按翻译就收到「翻到第 1 页时失败（退出码 1）」。
启动那轮 mark_write_roots_low 只覆盖当时已存在的项目，启动之后新建的项目得靠 pdir 自己标。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import workflow_db                                    # noqa: E402
from workflow import WorkflowDB                       # noqa: E402


class PdirLowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = WorkflowDB(Path(self.td.name) / "wb.sqlite3")
        self.tid = self.db.create_task("低完整性", "翻译能写进去吗", "NaNiFeMn")
        self.base = Path(self.td.name) / "proj"
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(self.base), self.tid))
        workflow_db._LOW_MARKED.clear()

    def tearDown(self) -> None:
        self.db.close()
        self.td.cleanup()

    def test_pdir_marks_low_once_per_dir(self) -> None:
        calls: list[str] = []
        with patch("lowbox.mark_low", side_effect=lambda p, recursive=True: calls.append(str(p)) or True):
            lib = self.db.pdir(self.tid, "文献库")
            self.db.pdir(self.tid, "文献库")               # 第二次不再 icacls
            self.db.pdir(self.tid, "图")
        self.assertTrue(lib.is_dir())
        self.assertEqual(calls, [str(lib), str(self.base / "图")])
        self.assertEqual([Path(p).name for p in calls], ["文献库", "图"])

    def test_mark_failure_does_not_break_pdir(self) -> None:
        """标不上（非 Windows / icacls 被拒）也得把目录给出去，不能让权限把功能搞挂。"""
        with patch("lowbox.mark_low", side_effect=OSError("no icacls")):
            self.assertTrue(self.db.pdir(self.tid, "笔记").is_dir())


if __name__ == "__main__":
    unittest.main()
