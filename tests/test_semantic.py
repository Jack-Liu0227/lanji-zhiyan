"""语义检索层自检（离线：嵌入用确定性假实现，不联网、不依赖 ollama）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import numpy as np  # noqa: E402

import semantic  # noqa: E402
import workflow  # noqa: E402


def _fake_embed(texts, cfg=None, timeout=120):
    """字符袋向量：同字共现就相似。够验证召回链路，不冒充语义质量。"""
    rows = []
    for t in texts:
        v = np.zeros(64, dtype=np.float32)
        for ch in t:
            v[hash(ch) % 64] += 1.0
        n = np.linalg.norm(v) or 1.0
        rows.append(v / n)
    return np.asarray(rows, dtype=np.float32)


class _NoEmbed(semantic.EmbedError):
    pass


class ChunkTest(unittest.TestCase):
    def test_long_text_splits_with_overlap_and_keeps_short_whole(self):
        body = ("钠离子电池层状正极的掺杂策略影响结构稳定性。" * 60)
        chunks = semantic.chunk_text(body, size=300, overlap=50)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 320 for c in chunks), [len(c) for c in chunks])
        self.assertEqual(semantic.chunk_text("短"), [])
        self.assertEqual(semantic.chunk_text(""), [])

    def test_single_long_sentence_is_hard_split(self):
        chunks = semantic.chunk_text("啊" * 900, size=300, overlap=50)
        self.assertGreaterEqual(len(chunks), 3)


class IndexTest(unittest.TestCase):
    def setUp(self):
        semantic.embed = _fake_embed
        semantic.LOCK_TIMEOUT = 0
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-sem-")
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "research.sqlite3")
        self.project = self.db.create_task("钠电项目", "问题", "NFM")
        self.other = self.db.create_task("另一个项目", "问题", "其它")
        # 一篇两页的「论文」+ 一条笔记
        self.db.db.execute(
            "INSERT INTO papers VALUES ('p1',?, 'cu_doped.pdf','/x.pdf','sha',2,100,'ok','2026-01-01')",
            (self.project,))
        self.db.db.executemany("INSERT INTO paper_pages VALUES (?,?,?)", [
            ("p1", 1, "铜掺杂能提高层状正极的循环稳定性。掺杂量 2% 时容量保持率最高。"),
            ("p1", 2, "第一性原理计算显示铜氧键更强，抑制相变。实验与计算一致。"),
        ])
        self.db.db.execute("INSERT INTO notes VALUES ('n1',?, NULL, '精读结论', ?, '2026-01-01')",
                           (self.project, "# 结论\n\n铜掺杂 2% 最优，容量保持率 91%。"))
        self.db.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_sync_indexes_pages_with_page_numbers(self):
        stats = semantic.sync(self.db)
        self.assertEqual(stats["indexed"], 2)          # 论文 + 笔记
        self.assertGreaterEqual(stats["chunks"], 3)
        rows = self.db.db.execute(
            "SELECT page_no, text FROM sem_chunks WHERE source_kind='paper' ORDER BY page_no").fetchall()
        self.assertEqual({r["page_no"] for r in rows}, {1, 2})

    def test_sync_is_idempotent_and_reindexes_on_change(self):
        semantic.sync(self.db)
        again = semantic.sync(self.db)
        self.assertEqual(again["indexed"], 0)
        self.assertEqual(again["skipped"], 2)
        self.db.db.execute("UPDATE paper_pages SET text=? WHERE paper_id='p1' AND page_no=2",
                           ("新的第二页：锰掺杂对比。",))
        self.db.db.commit()
        third = semantic.sync(self.db)
        self.assertEqual(third["indexed"], 1)
        self.assertEqual(third["skipped"], 1)

    def test_hybrid_recalls_both_legs_and_cites_page(self):
        semantic.sync(self.db)
        out = semantic.hybrid(self.db, "铜掺杂 循环稳定性", limit=10)
        self.assertEqual(out["mode"], "hybrid")
        top = out["hits"][0]
        self.assertEqual(top["kind"], "paper")
        self.assertIn(top["page_no"], (1, 2))
        self.assertTrue(top["citation"].startswith("cu_doped.pdf p."))
        self.assertIn("vector", top["matched"])
        self.assertEqual(len(top["matched"]), len(set(top["matched"])), "同一页的多个块不能把 matched 刷成重复项")
        self.assertLessEqual(top["rrf"], round(2 / (semantic.RRF_K + 1), 5), "RRF 每路只算一次，不能按块累加")

    def test_reference_page_is_not_indexed(self):
        refs = "\n".join("[%d] Author A, B. Some paper title. Journal, 2020, 12(3): 45-67." % i for i in range(20))
        self.assertFalse(semantic._worth_indexing(refs))
        self.assertTrue(semantic._worth_indexing("铜掺杂 2% 时容量保持率最高，100 圈后仍达 91%。" * 3))

    def test_inline_ref_list_and_table_dump_and_ack_are_dropped(self):
        inline = ("7677–89.[329] Epps RW, Volk AA, Reyes KG, Abolhasani M.Accelerated AI development "
                  "[330] Smith J, Doe A, Roe B. Autonomous labs. Nature 2023; 615: 1–9.[331] Lee K, "
                  "Park S, Kim H. Robotic synthesis. Science 2022; 376: 10–8.[332] Wang L, Zhao Q. " * 3)
        self.assertFalse(semantic._worth_indexing(inline), "整段没换行的参考文献要按 [n] 密度判")
        table = " ".join("ZrInCu2 (2,1,0) Fm-3m -1.157 -0.495 -0.958 0.463" for _ in range(12))
        self.assertFalse(semantic._worth_indexing(table), "数值表格堆不该进索引")
        self.assertFalse(semantic._worth_indexing("ACKNOWLEDGEMENTS This work was supported by NSFC 12345."))
        self.assertTrue(semantic._worth_indexing(
            "我们用水热法合成 O3 型正极，2% 铜掺杂样品在 100 圈后容量保持率最高。"))

    def test_project_scope_filters(self):
        semantic.sync(self.db)
        out = semantic.hybrid(self.db, "铜掺杂", project_ids=[self.other], limit=10)
        self.assertEqual(out["hits"], [])

    def test_short_query_falls_back_to_like(self):
        semantic.sync(self.db)
        lex = semantic.lexical(self.db, "铜掺", k=5)      # 2 字符：trigram 切不出
        self.assertTrue(lex)
        self.assertIn("铜掺", lex[0]["context"])

    def test_embed_down_degrades_to_lexical(self):
        semantic.sync(self.db)

        def boom(texts, cfg=None, timeout=120):
            raise semantic.EmbedError("服务没开")

        semantic.embed = boom
        out = semantic.hybrid(self.db, "铜掺杂", limit=5)
        self.assertEqual(out["mode"], "lexical")
        self.assertTrue(out["hits"])

    def test_status_reports_pending(self):
        semantic.sync(self.db)
        st = semantic.status(self.db)
        self.assertEqual(st["chunks"] > 0, True)
        self.assertEqual(st["sources_indexed"], 2)
        self.assertIn(st["dim"], (0, 64))


class KnowledgeQueryWiringTest(unittest.TestCase):
    """Agent 真正调用的入口必须吃到语义层，且索引没建时不能空手回去。"""

    def setUp(self):
        semantic.embed = _fake_embed
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-sem-wire-")
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "research.sqlite3")
        self.project = self.db.create_task("钠电项目", "问题", "NFM")
        self.db.db.execute(
            "INSERT INTO papers VALUES ('p1',?, 'cu.pdf','/x','sha',1,100,'ok','2026-01-01')",
            (self.project,))
        self.db.db.execute("INSERT INTO paper_pages VALUES ('p1',3,?)",
                           ("铜掺杂层状正极在 2% 掺杂量时容量保持率 91%。",))
        self.db.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_knowledge_query_uses_hybrid_and_cites_page(self):
        semantic.sync(self.db)
        out = workflow._knowledge_query(self.db, self.project, "铜掺杂 容量保持率")
        self.assertEqual(out["mode"], "hybrid")
        self.assertEqual(out["hits"][0]["citation"], "cu.pdf p.3")

    def test_knowledge_query_falls_back_when_index_empty(self):
        out = workflow._knowledge_query(self.db, self.project, "铜掺杂")     # 没 sync 过
        self.assertIn(out["mode"], ("lexical", "vector", "hybrid"))
        self.assertEqual(out["hits"], [])                                  # 索引空 + 子串也空
        self.assertIn("reason", out)

    def test_semantic_index_tool_exposes_status(self):
        res = workflow._tool_impl("semantic_index", self.db, self.project, {"action": "status"})
        self.assertIn("chunks", res)

    def test_warm_opens_its_own_connection(self):
        """后台预热走的是自己那条连接（主连接不能跨线程借），不能炸。"""
        semantic.embed = _fake_embed
        stats = semantic.warm(str(self.db.path), idle=0)
        self.assertGreaterEqual(stats["indexed"], 1)
        self.assertEqual(semantic.warm(str(self.db.path), idle=0)["skipped"], stats["indexed"])


if __name__ == "__main__":
    unittest.main()
