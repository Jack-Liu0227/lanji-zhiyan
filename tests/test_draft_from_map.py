# -*- coding: utf-8 -*-
"""按谱系图起稿：先铺骨架问确认、确认后一节一次调用逐节写、每节写完把「（待写）」换成正文；
没有图时走老的流水线。

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
        self._orig_raw = workflow.chat_raw

        def fake_raw(profile, messages, **kw):
            self.calls.append((messages[0]["content"], messages[1]["content"]))
            return {"role": "assistant", "tool_calls": [],
                    "content": ("### 模型自带的标题\n\n"
                                "这一节正文用来验证逐节落盘与替换：每一节生成完就要立刻整份重写一次，"
                                "把该节的占位标记替换成正文，左边的展示区才是一段一段长出来的。"
                                "这里的内容纯粹为了凑够清洗阈值，讲的全是调度与拼装，不涉及任何真实数据、"
                                "不涉及任何实验结论，也不涉及任何文献引用，字数继续凑到一百二十个字以上为止。"
                                + str(len(self.calls))),
                    "usage": {"prompt": 900, "completion": 300, "total": 1200, "exact": True}}
        workflow.chat_raw = fake_raw

    def tearDown(self):
        workflow.chat_raw = self._orig_raw
        self.db.close()
        self.tmp.cleanup()

    def _draft(self, text="写一篇初稿吧"):
        """两拍：先铺骨架（不调模型），再回一句「开始」逐节写。"""
        first = workflow_runners.run_write(self.db, self.tid, text)
        second = workflow_runners.run_write(self.db, self.tid, "开始")
        return first, second

    def _md(self):
        return workflow.doc_read(self.db, self.tid, "write")["markdown"]

    # ---- ① 骨架：先给结构、等确认，不动笔 ----
    def test_先铺骨架再等确认(self):
        out = workflow_runners.run_write(self.db, self.tid, "写一篇初稿吧")
        md = self._md()
        heads = [l for l in md.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["## 1 引言", "### 1.1 背景", "### 1.2 现状", "## 2 方法", "### 2.1 结构"])
        self.assertEqual(md.count(workflow_runners.PLACEHOLDER), 5, "每节一个「（待写）」占位")
        self.assertEqual(self.calls, [], "铺骨架不许调模型")
        self.assertTrue(out.get("awaiting"), "要停下来等确认")
        self.assertIn("开始", out["reply"])

    # ---- ② 确认后逐节写，写完把占位换成正文 ----
    def test_draft_written_section_by_section_in_map_order(self):
        self._draft()
        md = self._md()
        heads = [l for l in md.splitlines() if l.startswith("#")]
        self.assertEqual(heads, ["## 1 引言", "### 1.1 背景", "### 1.2 现状", "## 2 方法", "### 2.1 结构"],
                         "章节层级与顺序照谱系图的树，模型自己带的那行标题被剥掉")
        self.assertEqual(len(self.calls), 5, "一节一次调用，不多不少")
        self.assertNotIn("模型自带的标题", md)
        self.assertNotIn(workflow_runners.PLACEHOLDER, md, "写完不许再有占位")

    def test_each_section_only_gets_its_own_evidence(self):
        self._draft()
        by_label = {c[1].split("「")[1].split("」")[0]: c[0] for c in self.calls}
        self.assertIn("精读-A", by_label["1.2 现状"], "挂在 1.2 上的笔记要进 1.2 的提示词")
        self.assertIn("支撑（背景）", by_label["1.2 现状"])
        self.assertIn("Cu 掺杂带隙收窄", by_label["1.2 现状"], "要带上精读结论那两行，不能只给标题")
        self.assertNotIn("精读-A", by_label["1.1 背景"], "别的节的证据不许串过去")
        self.assertIn("没挂证据", by_label["2.1 结构"])

    def test_no_evidence_section_writes_itself_without_daibu(self):
        """没挂证据的节：按框架自己写满，prompt 里不许再要求「待补」；整份稿子也不许留占位。"""
        self._draft()
        by_label = {c[1].split("「")[1].split("」")[0]: c[0] for c in self.calls}
        sys21 = by_label["2.1 结构"]
        self.assertIn("直接写成正常正文", sys21)
        self.assertIn("不要出现「待补」", sys21)
        for sec_sys, _ in self.calls:                     # 全篇口径都要覆盖掉技能包里那句「缺数据写待补」
            self.assertIn("正文里不出现「待补」", sec_sys)
        self.assertNotIn("待补", self._md())

    def test_每写完一节就落盘一次_左边才能一段段显示(self):
        """每节写完立刻整份重写（那节的占位被正文替换），左边才一段段长出来。"""
        writes = []
        orig = workflow_runners.doc_write

        def spy(db, tid, kind, markdown, mode="replace"):
            out = orig(db, tid, kind, markdown, mode=mode)
            writes.append(len(out["markdown"]))
            return out

        workflow_runners.doc_write = spy
        try:
            self._draft()
        finally:
            workflow_runners.doc_write = orig
        self.assertEqual(len(writes), 6, "1 次铺骨架 + 5 次逐节替换")
        self.assertEqual(writes, sorted(writes), "每次落盘之后正文只增不减")

    # ---- 其它路由 ----
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
        self.assertIn("已经改好的正文", self._md())

    def test_explicit_whole_rewrite_uses_map_again(self):
        """他明说「整篇重写」才按现在的图再走一遍：一节一次，直接替换旧稿。

        骨架确认只在空稿时走（那时没有东西可毁）；有稿的改写反过来 —— 先按节写，
        写不全就整份回滚到旧稿（见 test_audit_regressions 里那条原稿不动）。
        """
        workflow.doc_write(self.db, self.tid, "write", "# 旧正文\n\n旧内容。\n")
        workflow_runners.run_write(self.db, self.tid, "按现在的谱系图把整篇重写")
        self.assertEqual(len(self.calls), 5, "整篇重写 → 一节一次")
        self.assertNotIn("旧内容", self._md(), "旧正文被新写的这一版替换")
        self.assertNotIn(workflow_runners.PLACEHOLDER, self._md())


    def test_逐节起稿也报token和产出(self):
        """右栏的「token / 工具数 / 产出目录」靠事件：逐节起稿这条路必须自己报 usage + doc_write 结果。"""
        _, second = self._draft()
        kinds = [e.get("type") for e in second["events"]]
        self.assertGreaterEqual(kinds.count("usage"), 5, "五节五次模型调用 → 五条 usage")
        self.assertGreaterEqual(kinds.count("tool_result"), 5, "五节五次落盘 → 五条 tool_result")
        toks = sum(e.get("total", 0) for e in second["events"] if e.get("type") == "usage")
        self.assertEqual(toks, 5 * 1200, "token 数要真的累加，不能是 0")
        paths = [e.get("path") for e in second["events"] if e.get("type") == "tool_result"]
        self.assertTrue(all(p and p.endswith("正文.md") for p in paths), "产出目录要能看到正文文件")


    def test_中译英这一步的system钉死了正文(self):
        """这一步的助手必须读「论文撰写」的正文.md、写同目录的 正文_en.md，不许另建零碎文件。"""
        import workflow_documents as docs
        txt = workflow_runners.entrans_system(self.db, self.tid)
        self.assertIn(str(docs.doc_path(self.db, self.tid, "write")), txt, "源路径要写死在 system 里")
        self.assertIn("正文_en.md", txt, "产物路径要说清")
        self.assertIn("read_attachment", txt, "要告诉它怎么把正文读进来")
        self.assertIn("不许另建零碎文件", txt)


if __name__ == "__main__":
    unittest.main()
