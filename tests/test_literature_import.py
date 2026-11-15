# -*- coding: utf-8 -*-
"""情报条目 → 项目文献库自检：题录转换 / 幂等导入 / 回显已导入 / 手动题录 / 参数校验。

    python -m unittest tests.test_literature_import -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import literature_import as li            # noqa: E402
import rss                               # noqa: E402
from workflow import WorkflowDB          # noqa: E402


class TestLiteratureImport(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = WorkflowDB(Path(self.td.name) / "l.sqlite3")
        self.project = self.db.create_task("导入自检", "", "", str(Path(self.td.name) / "proj"))
        self.other = self.db.create_task("另一个项目", "", "", str(Path(self.td.name) / "proj2"))
        src = rss.save_source(self.db, {"url": "https://a.example/rss", "title": "A 源"})["source"]
        self.db.save_rss_items(src, [
            {"link": "https://e.example/1", "title": "钠电正极掺杂", "authors": "Zhang, Li",
             "published_at": "2026-10-06 08:30:00", "summary": "摘要一"},
            {"link": "https://doi.org/10.1038/s41560-026-01234-5", "title": "界面稳定化",
             "published_at": "2026-09-01 10:00:00", "summary": "摘要二"},
        ])
        self.rows = self.db.rss_items()

    def tearDown(self) -> None:
        self.db.close()
        self.td.cleanup()

    # ---------- 纯函数 ----------
    def test_from_feed(self) -> None:
        item = li.from_feed(self.rows[0])
        self.assertEqual(item["title"], "钠电正极掺杂")
        self.assertEqual(item["authors"], "Zhang, Li")
        self.assertEqual(item["year"], "2026")
        self.assertEqual(item["url"], "https://e.example/1")
        self.assertIn("A 源", item["source"])
        # doi.org 链接能抽出 DOI
        self.assertEqual(li.from_feed(self.rows[1])["doi"], "10.1038/s41560-026-01234-5")
        with self.assertRaises(ValueError):
            li.from_feed({"link": "javascript:alert(1)", "title": "x"})

    def test_normalized_url_strips_tracking(self) -> None:
        a = li.normalized_url("https://E.example/p/1/?utm_source=x&utm_medium=y")
        b = li.normalized_url("https://e.example/p/1")
        self.assertEqual(a, b)

    # ---------- 导入 ----------
    def test_registered_get_routes(self) -> None:
        import literature_import_routes

        listed = literature_import_routes.route("GET", "/api/rss/items", {},
                                                {"source": [""], "q": ["正极"],
                                                 "year": ["2026"], "project_id": [self.project]}, self.db)
        self.assertEqual(listed["total"], 1)
        self.assertEqual([item["title"] for item in listed["items"]], ["钠电正极掺杂"])
        self.assertEqual(listed["items"][0]["imported_result_id"], "")

    def test_import_is_idempotent(self) -> None:
        ids = [r["id"] for r in self.rows]
        first = li.import_rss(self.db, self.project, ids)
        self.assertEqual(first["imported"], 2)
        self.assertEqual(first["existing"], 0)
        self.assertEqual(first["failures"], [])
        self.assertEqual(len(self.db.results(self.project)), 2)      # 真进了项目文献库

        again = li.import_rss(self.db, self.project, ids)            # 再导一次
        self.assertEqual(again["imported"], 0)
        self.assertEqual(again["existing"], 2)
        self.assertEqual(len(self.db.results(self.project)), 2)      # 没有重复行

    def test_annotate_marks_imported(self) -> None:
        before = li.annotate(self.db, self.rows, self.project)
        self.assertEqual([r["imported_result_id"] for r in before], ["", ""])

        li.import_rss(self.db, self.project, [self.rows[0]["id"]])
        after = li.annotate(self.db, self.rows, self.project)
        self.assertTrue(after[0]["imported_result_id"])
        self.assertEqual(after[1]["imported_result_id"], "")         # 另一条还没导

        # 另一个项目里是干净的：不串项目
        other = li.annotate(self.db, self.rows, self.other)
        self.assertEqual([r["imported_result_id"] for r in other], ["", ""])
        # 不选项目时原样返回，不加字段
        self.assertNotIn("imported_result_id", li.annotate(self.db, self.rows, "")[0])

    def test_import_reference(self) -> None:
        out = li.import_reference(self.db, self.project,
                                  {"title": "手填的一篇", "doi": "10.1038/s41560-026-99999-9", "year": "2025"})
        self.assertFalse(out["existing"])
        again = li.import_reference(self.db, self.project,
                                    {"title": "手填的一篇", "doi": "https://doi.org/10.1038/s41560-026-99999-9"})
        self.assertTrue(again["existing"])
        self.assertEqual(again["result_id"], out["result_id"])
        self.assertEqual(len(self.db.results(self.project)), 1)

    # ---------- 校验 ----------
    def test_validation(self) -> None:
        with self.assertRaises(ValueError):                          # 项目不存在
            li.import_rss(self.db, "", [self.rows[0]["id"]])
        with self.assertRaises(ValueError):                          # 空选择
            li.import_rss(self.db, self.project, [])
        with self.assertRaises(ValueError):                          # 超量
            li.import_rss(self.db, self.project, [self.rows[0]["id"]] * 201)
        with self.assertRaises(ValueError):                          # 条目不存在
            li.import_rss(self.db, self.project, ["nope"])
        with self.assertRaises(ValueError):                          # 手填无标题
            li.import_reference(self.db, self.project, {"doi": "10.1038/x"})

    def test_bad_link_is_reported_not_fatal(self) -> None:
        src = rss.save_source(self.db, {"url": "https://b.example/rss", "title": "B 源"})["source"]
        self.db.save_rss_items(src, [{"link": "not-a-url", "title": "坏链接条目"}])
        bad = [r for r in self.db.rss_items() if r["title"] == "坏链接条目"][0]
        out = li.import_rss(self.db, self.project, [self.rows[0]["id"], bad["id"]])
        self.assertEqual(out["imported"], 1)                         # 好的照导
        self.assertEqual(len(out["failures"]), 1)                    # 坏的单独报


if __name__ == "__main__":
    unittest.main(verbosity=2)
