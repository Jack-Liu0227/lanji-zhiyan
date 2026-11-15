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
        workflow.chat = lambda *a, **k: "从「文献精读」同步到：10 篇已精读。计划：1 定框架 2 补引用 3 定稿"
        try:
            import workflow_layout
            import workflow_state
            workflow_layout.ensure_schema(self.db)
            layout = workflow_layout.save_layout(self.db, {"name": "Writing", **workflow_layout._linear(["outline", "write"])})["layout"]
            workflow_layout.select_layout(self.db, {"task_id": self.task, "layout_id": layout["id"]})
            workflow.doc_write(self.db, self.task, "outline", "# Confirmed outline\nEvidence and methods")
            workflow_state.accept(self.db, self.task, "outline")
            self.assertTrue(step_done(self.db, self.task, "outline"))
            got = step_plan(self.db, self.task, "write")
            self.assertFalse(got.get("blocked"))
            self.assertIn("同步", got["text"])
            self.assertFalse(got["cached"])
            again = step_plan(self.db, self.task, "write")
            self.assertTrue(again["cached"])                      # 已有计划直接复用，不再烧模型
        finally:
            workflow.chat = orig

    def test_中间跳步也被挡住(self) -> None:
        # 只交付了第一步，第 4 步仍然接不上（它要上一步 notes 的交付）
        self.db.log_step(self.task, "idea", "done")
        self.assertTrue(step_plan(self.db, self.task, "notes").get("blocked"))

    def test_交付时把这一步的产物落库(self) -> None:
        """确认交付不能只记一句「用户确认交付」：产物现算出来、跟着台账一起存。"""
        import json
        from workflow import doc_write, step_delivery
        doc_write(self.db, self.task, "outline", "# 大纲\n## 一 引言\n### 1.1 背景\n## 二 方法\n")
        dv = step_delivery(self.db, self.task, "outline")
        self.assertEqual(dv["step"], "outline")
        self.assertEqual(dv["items"]["结构"], "2 节 / 1 小节")
        self.assertNotEqual(dv["items"]["字数"], "0")
        self.assertTrue(dv["files"] and dv["files"][0].endswith(".md"))

        self.db.log_step(self.task, "idea", "done", "创新点 12 字", delivery=dv)
        row = self.db.steps(self.task)[0]
        self.assertEqual(row["stage"], "idea")
        self.assertEqual(json.loads(row["delivery"])["items"]["结构"], "2 节 / 1 小节")

    def test_产物读失败也不连累交付(self) -> None:
        from workflow import step_delivery
        dv = step_delivery(self.db, self.task, "convert")      # 项目目录还没建也得出得来
        self.assertEqual(dv["step"], "convert")
        self.assertIn("items", dv)

    def test_上一轮撞工具上限判失败_产物齐了仍可交付(self) -> None:
        """撞「工具调用上限」= 暂停，不是失败；结果都在，就不该把下游锁死。

        实现方式：run_agent 撞上限时发的 error 事件带 soft=True（老记录没标记，按「工具调用上限」文案认），
        _evidence 不再把它判成 failed —— 否则交付按钮永远点不动。
        """
        import workflow_layout
        import workflow_state
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(
            self.db, {"name": "Lit", **workflow_layout._linear(["search"])})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.task, "layout_id": layout["id"]})
        self.db.cache_results(self.task, [{"title": "Cu 掺杂 NFM", "source": "crossref", "doi": "10.1/x"}])
        self.db.add_message(self.task, "assistant", "本轮已达到工具调用上限，暂停继续执行。",
                            [{"type": "error", "content": "工具调用上限", "soft": True}], scope="search")
        self.assertEqual(workflow_state.states(self.db, self.task)["search"]["status"], "succeeded",
                         "撞上限算暂停，不算失败")
        row = workflow_state.accept(self.db, self.task, "search")     # 不该再抛 409
        self.assertEqual(row["stage"], "search")
        self.assertEqual(workflow_state.states(self.db, self.task)["search"]["status"], "delivered")

    def test_真失败还是不许交付(self) -> None:
        """不带 soft 的 error（真崩了）仍然拦交付 —— 别为了放开暂停把这道闸拆了。"""
        import workflow_layout
        import workflow_state
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(
            self.db, {"name": "Lit", **workflow_layout._linear(["search"])})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.task, "layout_id": layout["id"]})
        self.db.cache_results(self.task, [{"title": "Cu 掺杂 NFM", "source": "crossref", "doi": "10.1/x"}])
        self.db.add_message(self.task, "assistant", "模型接口 500", [{"type": "error", "content": "provider failed"}],
                            scope="search")
        state = workflow_state.states(self.db, self.task)["search"]
        self.assertEqual(state["status"], "failed")
        with self.assertRaises(ValueError):
            workflow_state.accept(self.db, self.task, "search")


if __name__ == "__main__":
    unittest.main()
