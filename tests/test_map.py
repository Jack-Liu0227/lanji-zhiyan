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
        self.assertEqual(workflow.STAGE_LABEL["map"], "梳理精读")

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
