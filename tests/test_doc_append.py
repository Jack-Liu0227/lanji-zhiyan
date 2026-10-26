# -*- coding: utf-8 -*-
"""正文撰写：doc_write(mode="append") 一段一段写 —— 写完一段左边就该多一段，且整份文件不能被写坏。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

from workflow import WorkflowDB
from workflow_documents import doc_read, doc_write


class DocAppendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = WorkflowDB(Path(self.td.name) / "wb.sqlite3")
        self.task = self.db.create_task("O3 掺杂", "哪种掺杂元素值得验证", "NaNi1/3Fe1/3Mn1/3O2")

    def tearDown(self) -> None:
        self.db.close()
        self.td.cleanup()

    def test_一段一段追加且replace仍然整份覆盖(self) -> None:
        doc_write(self.db, self.task, "write", "# 1 引言\n第一段正文。")
        doc_write(self.db, self.task, "write", "## 2 方法\n第二段正文。", mode="append")
        doc_write(self.db, self.task, "write", "## 3 结果\n第三段正文。", mode="append")
        md = doc_read(self.db, self.task, "write")["markdown"]
        for piece in ("# 1 引言", "第一段正文", "## 2 方法", "第二段正文", "## 3 结果", "第三段正文"):
            self.assertIn(piece, md)                       # 每段都在，且顺序累加
        self.assertLess(md.index("# 1 引言"), md.index("## 3 结果"))
        self.assertLessEqual(md.count("## 机器校验"), 1)   # 校验段不能被追加三次

        doc_write(self.db, self.task, "write", "# 整篇重写\n只有这一段。")   # 默认 replace
        md2 = doc_read(self.db, self.task, "write")["markdown"]
        self.assertIn("整篇重写", md2)
        self.assertNotIn("第三段正文", md2)                # 覆盖写就该清掉旧的

    def test_空段拒写不会清空文件(self) -> None:
        doc_write(self.db, self.task, "write", "# 1 引言\n留着。")
        with self.assertRaises(ValueError):
            doc_write(self.db, self.task, "write", "   ", mode="append")
        self.assertIn("留着", doc_read(self.db, self.task, "write")["markdown"])


if __name__ == "__main__":
    unittest.main()
