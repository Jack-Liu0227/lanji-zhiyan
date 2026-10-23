# -*- coding: utf-8 -*-
"""硬限制自检：流程步必须「上一步已交付」，否则只能当独立功能块（不写计划、不落库）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import workflow
from workflow import STAGES, WorkflowDB, step_done, step_plan


class StepGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = WorkflowDB(Path(self.td.name) / "wb.sqlite3")
        self.task = self.db.create_task("O3 掺杂", "哪种掺杂元素值得验证", "NaNi1/3Fe1/3Mn1/3O2")

    def tearDown(self) -> None:
        self.db.close()
        self.td.cleanup()

    def test_第一步不设闸(self) -> None:
        self.assertFalse(step_plan(self.db, self.task, "idea").get("blocked"))

    def test_上一步没交付就是blocked且不落库(self) -> None:
        self.assertFalse(step_done(self.db, self.task, "notes"))
        got = step_plan(self.db, self.task, "outline")
        self.assertTrue(got.get("blocked"))
        self.assertEqual(got["prev"], STAGES[STAGES.index("outline") - 1])
        self.assertEqual(got["prev_label"], "论文翻译")
        self.assertEqual(self.db.messages(self.task, "outline"), [])   # 不写计划、不缓存空计划

    def test_交付后放行(self) -> None:
        orig = workflow.chat
        workflow.chat = lambda *a, **k: "从「论文翻译」同步到：10 篇已译。计划：1 定框架 2 补引用 3 定稿"
        try:
            self.db.log_step(self.task, "notes", "done")
            self.assertTrue(step_done(self.db, self.task, "notes"))
            got = step_plan(self.db, self.task, "outline")
            self.assertFalse(got.get("blocked"))
            self.assertIn("同步", got["text"])
            self.assertFalse(got["cached"])
            again = step_plan(self.db, self.task, "outline")
            self.assertTrue(again["cached"])                      # 已有计划直接复用，不再烧模型
        finally:
            workflow.chat = orig

    def test_中间跳步也被挡住(self) -> None:
        # 只交付了第一步，第 4 步仍然接不上（它要上一步 notes 的交付）
        self.db.log_step(self.task, "idea", "done")
        self.assertTrue(step_plan(self.db, self.task, "notes").get("blocked"))


if __name__ == "__main__":
    unittest.main()
