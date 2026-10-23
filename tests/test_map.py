# -*- coding: utf-8 -*-
"""第 5 步「梳理精读」：谱系图读写 + 数据源 + 十步口径。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(Path(_ROOT) / "packages" / _n))

import workflow  # noqa: E402
import workflow_documents  # noqa: E402


class MapStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.sqlite3"
        os.environ["RESEARCH_WORKBENCH_DB"] = str(self.db_path)
        self.db = workflow.WorkflowDB(self.db_path)
        base = Path(self.tmp.name) / "proj"
        (base / "论文正文").mkdir(parents=True)
        (base / "笔记").mkdir()
        (base / "其余" / "数据").mkdir(parents=True)
        (base / "论文正文" / "框架.md").write_text(
            "# 题目\n\n## 1 引言\n\n### 1.1 背景\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n", encoding="utf-8")
        (base / "笔记" / "精读-A.md").write_text("结论：Cu 掺杂带隙收窄\n", encoding="utf-8")
        (base / "其余" / "数据" / "XRD-2026-10.csv").write_text("2theta,int\n28.3,100\n", encoding="utf-8")
        self.tid = self.db.create_task("谱系项目", "q", "Na", str(base))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_step_order_is_ten_with_map_after_outline(self):
        self.assertEqual(len(workflow.STAGES), 10)
        self.assertEqual(workflow.STAGES.index("map"), workflow.STAGES.index("outline") + 1)
        self.assertEqual(workflow.STAGE_LABEL["map"], "证据链谱系")

    def test_write_read_roundtrip_and_dirty_data_dropped(self):
        r = workflow.map_write(self.db, self.tid, {
            "nodes": [{"id": "s1", "type": "section", "label": "1.1 背景", "detail": "背景"},
                      {"id": "p1", "type": "paper", "label": "Left2025", "detail": "带隙收窄", "ref": "精读-A.md"},
                      {"id": "x1", "type": "不认识的类型", "label": "杂项"},
                      {"label": "没有 id"},                                   # 丢
                      "不是字典"],                                            # 丢
            "edges": [{"from": "s1", "to": "p1", "label": "支撑"},
                      {"from": "s1", "to": "no-such-node"},                   # 丢
                      {"from": "p1", "to": "x1"}]})                           # 留（两边都在）
        self.assertEqual([n["id"] for n in r["nodes"]], ["s1", "p1", "x1"])
        self.assertEqual(r["nodes"][2]["type"], "note", "未知 type 兜成 note")
        self.assertEqual(len(r["edges"]), 2)
        self.assertEqual(r["sections"], 1)
        self.assertEqual(r["refs"], 2)
        self.assertTrue(r["exists"])
        again = workflow.map_read(self.db, self.tid)
        self.assertEqual(len(again["nodes"]), 3)
        self.assertEqual(json.loads(Path(r["path"]).read_text(encoding="utf-8"))["updated"], r["updated"])

    def test_read_empty_and_broken_json(self):
        empty = workflow.map_read(self.db, self.tid)
        self.assertFalse(empty["exists"])
        self.assertEqual((empty["nodes"], empty["edges"]), ([], []))
        Path(empty["path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(empty["path"]).write_text("{坏 JSON", encoding="utf-8")
        broken = workflow.map_read(self.db, self.tid)
        self.assertTrue(broken["broken"])
        self.assertEqual(broken["nodes"], [])

    def test_sources_list_outline_notes_and_data(self):
        s = workflow.map_sources(self.db, self.tid)
        self.assertEqual(s["sections"], ["1.1 背景", "1.2 现状"])
        # 笔记给「标题 + id」，不给哈希文件名 —— 助手要能认出挂的是哪一篇
        self.assertIn("精读-A", [n["id"] for n in s["notes"]])
        self.assertIn("精读-A.md", [n["title"] for n in s["notes"]])
        self.assertIn("XRD-2026-10.csv", s["data"])
        self.assertTrue(s["map_path"].endswith("谱系.json"))

    def test_tidy_reframes_by_current_outline_order(self):
        """重新整理：他把 2.1 和 1.1 调换（大纲顺序变了）→ 图按新顺序重排，挂着的笔记跟着走。"""
        base = Path(self.db.base_dir(self.tid))
        out = base / "论文正文" / "框架.md"
        out.write_text("# 题目\n\n## 1 引言\n\n### 1.1 背景\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n",
                       encoding="utf-8")
        workflow.map_seed(self.db, self.tid)
        seeded = workflow.map_read(self.db, self.tid)
        ids = {n["label"]: n["id"] for n in seeded["nodes"] if n["type"] == "section"}
        workflow.map_write(self.db, self.tid, {
            "nodes": [dict(n, x=10.0, y=20.0) if n["type"] == "section" else n for n in seeded["nodes"]]
            + [{"id": "r1", "type": "note", "label": "精读-A.md", "ref": "精读-A.md", "x": 5.0, "y": 6.0}],
            "edges": seeded["edges"] + [{"from": ids["1.2 现状"], "to": "r1", "label": "支撑"}]})

        out.write_text("# 题目\n\n## 1 引言\n\n### 1.2 现状\n- 说明：y\n\n### 1.1 背景\n- 说明：x\n",
                       encoding="utf-8")
        r = workflow.map_tidy(self.db, self.tid)
        labels = [n["label"] for n in r["nodes"] if n["type"] == "section"]
        self.assertEqual(labels, ["1 引言", "1.2 现状", "1.1 背景"], "小节顺序照大纲，不再按老图的顺序")
        self.assertEqual({n["label"]: n["id"] for n in r["nodes"] if n["type"] == "section"}, ids,
                         "标题没变 → 沿用原 id，挂着的资料才跟得上")
        note = [n for n in r["nodes"] if n["id"] == "r1"][0]
        self.assertNotIn("x", note, "手动坐标清掉，画布才按新层级重排")
        self.assertIn({"from": ids["1.2 现状"], "to": "r1", "label": "支撑"}, r["edges"])
        self.assertTrue(any("顺序按大纲重排" in c for c in r["changed"]), r["changed"])

    def test_tidy_drops_sections_gone_from_outline(self):
        base = Path(self.db.base_dir(self.tid))
        out = base / "论文正文" / "框架.md"
        workflow.map_seed(self.db, self.tid)
        r = workflow.map_read(self.db, self.tid)
        workflow.map_write(self.db, self.tid, {
            "nodes": r["nodes"] + [{"id": "r1", "type": "note", "label": "旧笔记", "ref": "精读-A.md"}],
            "edges": r["edges"] + [{"from": [n for n in r["nodes"] if n["label"] == "1.2 现状"][0]["id"],
                                    "to": "r1", "label": "支撑"}]})
        out.write_text("# 题目\n\n## 1 引言\n\n### 1.1 背景\n- 说明：x\n", encoding="utf-8")
        r = workflow.map_tidy(self.db, self.tid)
        self.assertEqual([n["label"] for n in r["nodes"]], ["1 引言", "1.1 背景"])
        self.assertTrue(any("已经删掉" in c for c in r["changed"]), r["changed"])
        self.assertEqual(r["refs"], 0, "被删小节上挂的笔记一起摘掉")

    def test_note_node_detail_follows_note_file(self):
        """精读笔记重读/补写之后，图上节点「连线后面那行字」跟着更新。"""
        workflow.map_write(self.db, self.tid, {
            "nodes": [{"id": "s1", "type": "section", "label": "1.1 背景"},
                      {"id": "r1", "type": "note", "label": "精读-A", "ref": "精读-A.md"}],
            "edges": [{"from": "s1", "to": "r1", "label": "支撑"}]})
        first = workflow.map_read(self.db, self.tid)
        self.assertIn("Cu 掺杂带隙收窄", [n for n in first["nodes"] if n["id"] == "r1"][0]["detail"])

        base = Path(self.db.base_dir(self.tid))
        (base / "笔记" / "精读-A.md").write_text(
            "# 精读 · A\n\n**PAGE 1**\n- 新结论：氧空位梯度压低暗电流\n- 响应度同时回升\n", encoding="utf-8")
        again = workflow.map_read(self.db, self.tid)
        detail = [n for n in again["nodes"] if n["id"] == "r1"][0]["detail"]
        self.assertIn("氧空位梯度压低暗电流", detail)
        self.assertIn("响应度同时回升", detail)

    def _seed_map(self, nodes=()):
        workflow.map_seed(self.db, self.tid)
        cur = workflow.map_read(self.db, self.tid)
        extra = list(nodes)
        edges = list(cur["edges"])
        for nid, label, parent in extra:
            edges.append({"from": parent, "to": nid, "label": "支撑"})
        workflow.map_write(self.db, self.tid, {"nodes": cur["nodes"] + [
            {"id": nid, "type": "note", "label": label, "ref": "精读-A.md"} for nid, label, _ in extra], "edges": edges})
        return {n["label"]: n["id"] for n in workflow.map_read(self.db, self.tid)["nodes"] if n["type"] == "section"}

    def test_tidy_recognises_section_by_number_not_text(self):
        """他手改序号 / 改标题文字，都还是同一节 —— 只是认的依据不同：先序号，后文字。"""
        ids = self._seed_map([("r1", "笔记一", "s2")])
        out = Path(self.db.base_dir(self.tid)) / "论文正文" / "框架.md"
        # 1) 序号没变、标题文字改了 → 认序号
        out.write_text("# 题目\n\n## 1 引言\n\n### 1.1 背景（改写过的说法）\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n",
                       encoding="utf-8")
        r = workflow.map_tidy(self.db, self.tid)
        got = {n["label"]: n["id"] for n in r["nodes"] if n["type"] == "section"}
        self.assertEqual(got["1.1 背景（改写过的说法）"], ids["1.1 背景"], "序号没变就是同一节")
        self.assertIn({"from": ids["1.1 背景"], "to": "r1", "label": "支撑"}, r["edges"], "笔记照旧挂着")
        # 2) 只改序号（文字不动）→ 按文字认回同一节
        out.write_text("# 题目\n\n## 1 引言\n\n### 1.4 背景（改写过的说法）\n- 说明：x\n\n### 1.2 现状\n- 说明：y\n",
                       encoding="utf-8")
        r = workflow.map_tidy(self.db, self.tid)
        self.assertEqual({n["label"]: n["id"] for n in r["nodes"] if n["type"] == "section"}["1.4 背景（改写过的说法）"],
                         ids["1.1 背景"], "序号改了但文字还在 → 还是同一节")

    def test_tidy_keeps_same_named_sections_apart(self):
        """「实验方法」这种同名小标题在不同章里很常见，不能按文字认成同一节。"""
        out = Path(self.db.base_dir(self.tid)) / "论文正文" / "框架.md"
        out.write_text("# 题目\n\n## 1 引言\n\n### 1.1 实验方法\n- 说明：x\n\n"
                       "## 2 讨论\n\n### 2.1 实验方法\n- 说明：y\n", encoding="utf-8")
        ids = self._seed_map([("r1", "笔记一", "s2"), ("r2", "笔记二", "s4")])
        self.assertEqual(len(ids), 4)
        r = workflow.map_tidy(self.db, self.tid)
        self.assertEqual(len([n for n in r["nodes"] if n["type"] == "section"]), 4, "两节都还在，没被并成一个")
        self.assertIn({"from": ids["1.1 实验方法"], "to": "r1", "label": "支撑"}, r["edges"])
        self.assertIn({"from": ids["2.1 实验方法"], "to": "r2", "label": "支撑"}, r["edges"])

    def test_hand_made_node_roundtrip_and_survives_tidy(self):
        """节点尾部 ＋ 手建的节点：没有 ref、大纲里也没有它 —— 写进去要原样留住，挂在某节下面就别被「重新整理」清掉。"""
        workflow.map_seed(self.db, self.tid)
        cur = workflow.map_read(self.db, self.tid)
        sec = [n for n in cur["nodes"] if n["label"] == "1.2 现状"][0]["id"]
        r = workflow.map_write(self.db, self.tid, {
            "nodes": cur["nodes"] + [{"id": "r1", "type": "note", "label": "我手加的一条", "detail": "自己的说明"}],
            "edges": cur["edges"] + [{"from": sec, "to": "r1", "label": "包含"}]}, keep_positions=False)
        got = [n for n in r["nodes"] if n["id"] == "r1"][0]
        self.assertEqual((got["label"], got["detail"], got["type"], got["ref"]),
                         ("我手加的一条", "自己的说明", "note", ""))
        self.assertIn({"from": sec, "to": "r1", "label": "包含"}, r["edges"])
        after = workflow.map_tidy(self.db, self.tid)
        self.assertIn("我手加的一条", [n["label"] for n in after["nodes"]], "挂在节下面 → 重新整理留着")
        self.assertIn({"from": sec, "to": "r1", "label": "包含"}, after["edges"])

    def test_tidy_keeps_hand_made_nodes(self):
        """他自己用 ＋ 加的节点（mine=true）大纲里没有 —— 重新整理不许删，坐标、类型、边原样留着。"""
        workflow.map_seed(self.db, self.tid)
        cur = workflow.map_read(self.db, self.tid)
        sec = [n for n in cur["nodes"] if n["label"] == "1 引言"][0]["id"]
        r = workflow.map_write(self.db, self.tid, {
            "nodes": cur["nodes"] + [{"id": "r1", "type": "section", "label": "1.4 我自己加的一节",
                                      "mine": True, "x": 11.0, "y": 22.0}],
            "edges": cur["edges"] + [{"from": sec, "to": "r1", "label": "包含"}]}, keep_positions=False)
        self.assertTrue([n for n in r["nodes"] if n["id"] == "r1"][0]["mine"], "mine 要能落盘（map_write 不能剥掉）")
        after = workflow.map_tidy(self.db, self.tid)
        got = [n for n in after["nodes"] if n["id"] == "r1"]
        self.assertEqual(len(got), 1, "手建的节点不该被重新整理删掉")
        self.assertEqual((got[0]["label"], got[0]["type"], got[0].get("x"), got[0].get("y")),
                         ("1.4 我自己加的一节", "section", 11.0, 22.0))
        self.assertIn({"from": sec, "to": "r1", "label": "包含"}, after["edges"])
        self.assertTrue(any("手建的 1 个节点" in c for c in after["changed"]), after["changed"])

    def test_doc_write_refuses_empty_markdown(self):
        """模型偶尔发个丢参数的工具调用：空 markdown 被照写就把整份正文清空（实测发生过）。"""
        workflow.doc_write(self.db, self.tid, "write", "# 已有正文\n\n第一段。\n")
        with self.assertRaises(ValueError):
            workflow.doc_write(self.db, self.tid, "write", "   ")
        with self.assertRaises(ValueError):
            workflow.doc_write(self.db, self.tid, "write", "")
        kept = workflow.doc_read(self.db, self.tid, "write")["markdown"]
        self.assertIn("第一段", kept, "拒写之后原正文得还在")

    def test_open_write_is_once(self):
        """谱系图交付 → 文章撰写接手只留一条开场白（重复交付不刷屏）。"""
        r1 = workflow_documents.open_write(self.db, self.tid)
        self.assertTrue(r1["opened"])
        r2 = workflow_documents.open_write(self.db, self.tid)
        self.assertFalse(r2["opened"])
        msgs = [m for m in self.db.messages(self.tid, "write") if m["role"] == "assistant"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("谱系图我已经看过了", msgs[0]["content"])

    def test_write_system_no_map_block_after_draft(self):
        """口径（2026-10-22 改）：谱系图只在「从零起稿」那一次当骨架用（逐节生成，见 test_draft_from_map）；
        写下正文之后改图不许影响改稿，所以 write_system 里不再挂谱系图那段。
        以前这条测的是反过来的口径（system 里必须有图），起稿改成分节生成后它就没有消费方了。"""
        self._seed_map([("r1", "笔记一", "s2")])
        txt = workflow_documents.write_system(self.db, self.tid)
        self.assertNotIn("【谱系图", txt)
        self.assertNotIn("笔记一", txt)

    def test_write_system_lists_figures_and_asks_for_captions(self):
        """文章撰写要读「图」文件夹，并要求在正文里落 [图位 N] 图题（格式转换左栏读的就是这个夹）。"""
        fig = Path(self.db.base_dir(self.tid)) / "图"
        fig.mkdir(parents=True, exist_ok=True)
        (fig / "fig1_器件结构.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        txt = workflow_documents.write_system(self.db, self.tid)
        self.assertIn("fig1_器件结构.png", txt)
        self.assertIn("图位", txt)
        self.assertIn("图题", txt)
        import workflow_converters
        listed = workflow_converters.fig_list(self.db, self.tid)["files"]
        self.assertEqual([x["name"] for x in listed], ["fig1_器件结构.png"])

    def test_map_system_renders_notes_list(self):
        """谱系 agent 的 system 必须能拼出来 —— 笔记源是 dict，join 错了整步 agent 直接 500。"""
        sys_txt = workflow_documents.map_system(self.db, self.tid)
        self.assertIn("精读-A", sys_txt)
        self.assertIn("大纲", sys_txt)

    def test_tools_and_tool_impl(self):
        names = [t["function"]["name"] for t in workflow.map_tools()]
        self.assertIn("map_write", names)
        self.assertIn("map_sources", names)
        self.assertNotIn("doc_write", names, "这一步不该能改正文/框架")
        self.assertNotIn("cite_add", names)
        workflow._tool_impl("map_write", self.db, self.tid,
                            {"data": json.dumps({"nodes": [{"id": "s1", "type": "section", "label": "A"}], "edges": []})},
                            scope="map")
        self.assertEqual(workflow._tool_impl("map_read", self.db, self.tid, {}, scope="map")["sections"], 1)
        self.assertTrue(workflow._tool_impl("map_sources", self.db, self.tid, {}, scope="map")["sections"])


if __name__ == "__main__":
    unittest.main()
