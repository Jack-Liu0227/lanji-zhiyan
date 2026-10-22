# -*- coding: utf-8 -*-
"""按谱系图起稿：一节一次调用、每节只喂自己挂的证据、拼成整份落盘；没有图时走老的流水线。

模型用假的（`workflow.chat` 打桩），验的是"调度与拼装"，不是文笔。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(Path(_ROOT) / "packages" / _n))

import workflow  # noqa: E402
import workflow_runners  # noqa: E402


class DraftFromMapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "t.sqlite3")
        base = Path(self.tmp.name) / "proj"
        (base / "论文正文").mkdir(parents=True)
        (base / "笔记").mkdir()
        (base / "论文正文" / "框架.md").write_text(
            "# 题目\n\n## 1 引言\n\n### 1.1 背景\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n\n"
            "## 2 方法\n\n### 2.1 结构\n- 说明：z\n", encoding="utf-8")
        (base / "笔记" / "精读-A.md").write_text("结论：Cu 掺杂带隙收窄\n- 界面钝化压低暗电流\n", encoding="utf-8")
        self.tid = self.db.create_task("起稿项目", "q", "Na", str(base))
        self.base = base
        # 谱系图：两章 + 三个小节，1.2 挂一篇精读笔记，2.1 什么都不挂
        workflow.map_write(self.db, self.tid, {
            "nodes": [{"id": "c1", "type": "section", "label": "1 引言", "detail": ""},
                      {"id": "s1", "type": "section", "label": "1.1 背景", "detail": ""},
                      {"id": "s2", "type": "section", "label": "1.2 现状", "detail": ""},
                      {"id": "c2", "type": "section", "label": "2 方法", "detail": ""},
                      {"id": "s3", "type": "section", "label": "2.1 结构", "detail": ""},
                      {"id": "r1", "type": "note", "label": "精读-A", "ref": "精读-A.md"}],
            "edges": [{"from": "c1", "to": "s1", "label": "包含"},
                      {"from": "c1", "to": "s2", "label": "包含"},
                      {"from": "c1", "to": "c2", "label": "下一章"},
                      {"from": "c2", "to": "s3", "label": "包含"},
                      {"from": "s2", "to": "r1", "label": "支撑（背景）"}]})
        self.calls = []
        self._orig_chat = workflow.chat

        def fake_chat(profile, messages, **kw):
            self.calls.append((messages[0]["content"], messages[1]["content"]))
            return "### 模型自带的标题\n\n这一节正文。" + str(len(self.calls))
        workflow.chat = fake_chat

    def tearDown(self):
        workflow.chat = self._orig_chat
        self.db.close()
        self.tmp.cleanup()

    def test_draft_written_section_by_section_in_map_order(self):
        workflow_runners.run_write(self.db, self.tid, "写一篇初稿吧")
        md = workflow.doc_read(self.db, self.tid, "write")["markdown"]
        heads = [l for l in md.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["## 1 引言", "### 1.1 背景", "### 1.2 现状", "## 2 方法", "### 2.1 结构"],
                         "章节层级与顺序照谱系图的树，模型自己带的那行标题被剥掉")
        self.assertEqual(len(self.calls), 5, "一节一次调用，不多不少")
        self.assertNotIn("模型自带的标题", md)

    def test_each_section_only_gets_its_own_evidence(self):
        workflow_runners.run_write(self.db, self.tid, "写一篇初稿吧")
        by_label = {c[1].split("「")[1].split("」")[0]: c[0] for c in self.calls}
        self.assertIn("精读-A", by_label["1.2 现状"], "挂在 1.2 上的笔记要进 1.2 的提示词")
        self.assertIn("支撑（背景）", by_label["1.2 现状"])
        self.assertIn("Cu 掺杂带隙收窄", by_label["1.2 现状"], "要带上精读结论那两行，不能只给标题")
        self.assertNotIn("精读-A", by_label["1.1 背景"], "别的节的证据不许串过去")
        self.assertIn("没挂证据", by_label["2.1 结构"])

    def test_no_evidence_section_writes_itself_without_daibu(self):
        """没挂证据的节：按框架自己写满，prompt 里不许再要求「待补」；整份稿子也不许留占位。"""
        workflow_runners.run_write(self.db, self.tid, "写一篇初稿吧")
        by_label = {c[1].split("「")[1].split("」")[0]: c[0] for c in self.calls}
        sys21 = by_label["2.1 结构"]
        self.assertIn("直接写成正常正文", sys21)
        self.assertIn("不要出现「待补」", sys21)
        for sec_sys, _ in self.calls:                     # 全篇口径都要覆盖掉技能包里那句「缺数据写待补」
            self.assertIn("正文里不出现「待补」", sec_sys)
        md = workflow.doc_read(self.db, self.tid, "write")["markdown"]
        self.assertNotIn("待补", md)

    def test_no_map_falls_back_to_pipeline(self):
        workflow.map_write(self.db, self.tid, {"nodes": [], "edges": []})
        self.assertEqual(workflow_runners._map_plan(workflow.map_read(self.db, self.tid)), [])
        hit = []
        orig = workflow_runners._run_write_pipeline
        workflow_runners._run_write_pipeline = lambda *a, **k: (hit.append("pipeline"),
                                                              {"reply": "p", "events": []})[1]
        try:
            workflow_runners.run_write(self.db, self.tid, "写一篇初稿吧")
        finally:
            workflow_runners._run_write_pipeline = orig
        self.assertEqual(hit, ["pipeline"], "没有谱系图 → 还走老的素材官→写手流水线")

    def test_write_system_no_longer_injects_map(self):
        """改谱系图不该改变后续写作（起稿用过图就够了）：system 里不再带谱系图那段。"""
        txt = workflow_runners.write_system(self.db, self.tid)
        self.assertNotIn("【谱系图", txt)
        self.assertIn("【框架", txt)

    def test_body_plus_rewrite_one_section_does_not_redraft_whole_paper(self):
        """已有正文时说「重写 1.2 这节」不能把整篇按图推倒重来 —— 那只该改这一节。"""
        workflow.doc_write(self.db, self.tid, "write", "# 旧正文\n\n## 1 引言\n\n已经改好的正文。\n")
        hit = []
        orig_agent, orig_pipe = workflow.run_agent, workflow_runners._run_write_pipeline
        workflow.run_agent = lambda *a, **k: (hit.append("agent"), {"reply": "a", "events": []})[1]
        workflow_runners._run_write_pipeline = lambda *a, **k: (hit.append("pipeline"), {"reply": "p", "events": []})[1]
        self.calls.clear()
        try:
            workflow_runners.run_write(self.db, self.tid, "把 1.2 现状这节重写一下")
        finally:
            workflow.run_agent, workflow_runners._run_write_pipeline = orig_agent, orig_pipe
        self.assertEqual(self.calls, [], "逐节按图生成不许被「重写某节」触发")
        self.assertNotIn("pipeline", hit, "也不许整篇走流水线")
        self.assertIn("agent", hit, "改一节 → 走对话 agent")
        self.assertIn("已经改好的正文", workflow.doc_read(self.db, self.tid, "write")["markdown"])

    def test_explicit_whole_rewrite_uses_map_again(self):
        """他明说「整篇重写」才按现在的图再走一遍逐节生成。"""
        workflow.doc_write(self.db, self.tid, "write", "# 旧正文\n\n旧内容。\n")
        workflow_runners.run_write(self.db, self.tid, "按现在的谱系图把整篇重写")
        self.assertEqual(len(self.calls), 5, "整篇重写 → 一节一次")
        self.assertNotIn("旧内容", workflow.doc_read(self.db, self.tid, "write")["markdown"])


if __name__ == "__main__":
    unittest.main()
