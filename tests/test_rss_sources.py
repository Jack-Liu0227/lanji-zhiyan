# -*- coding: utf-8 -*-
"""情报日报订阅源管理自检：增删改 / URL 校验 / 只抓启用源 / 删光不自动补。

    python -m unittest tests.test_rss_sources -v
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

import rss                                   # noqa: E402
import rss_routes                            # noqa: E402
from workflow import WorkflowDB              # noqa: E402


class TestRssSources(unittest.TestCase):
    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.db = WorkflowDB(Path(self.td.name) / "r.sqlite3")
        self._old_fetch = rss.fetch
        self.fetched: list[str] = []

    def tearDown(self) -> None:
        rss.fetch = self._old_fetch
        self.db.close()
        self.td.cleanup()

    def _fake_fetch(self, url: str, timeout: int = 0) -> list:
        self.fetched.append(url)
        return [{"link": f"https://e.example/{len(self.fetched)}", "title": "T"}]

    def test_save_edit_delete(self) -> None:
        got = rss.save_source(self.db, {"url": "https://a.example/rss", "title": "A 源"})["source"]
        self.assertEqual(got["title"], "A 源")
        self.assertEqual(got["enabled"], 1)
        edited = rss.save_source(self.db, {"id": got["id"], "enabled": False})["source"]
        self.assertEqual(edited["enabled"], 0)
        self.assertEqual(edited["url"], "https://a.example/rss")        # 没传的字段保留
        self.assertEqual(rss.delete_source(self.db, {"id": got["id"]})["deleted"], got["id"])
        self.assertEqual(self.db.rss_sources(), [])

    def test_url_validation(self) -> None:
        for bad in ("", "ftp://a.example/rss", "not a url", "https://has space/rss"):
            with self.assertRaises(ValueError, msg=bad):
                rss.save_source(self.db, {"url": bad})
        rss.save_source(self.db, {"url": "https://a.example/rss"})
        with self.assertRaises(ValueError):                             # 重复地址不许静默覆盖
            rss.save_source(self.db, {"url": "https://a.example/rss", "title": "重名"})
        with self.assertRaises(ValueError):                             # 不存在的源
            rss.save_source(self.db, {"id": "nope", "enabled": True})

    def test_delete_keeps_items(self) -> None:
        src = rss.save_source(self.db, {"url": "https://a.example/rss"})["source"]
        self.db.save_rss_items(self.db.rss_source(src["id"]), [{"link": "https://e/1", "title": "T"}])
        self.assertEqual(rss.delete_source(self.db, {"id": src["id"]})["retained_items"], 1)
        items = self.db.rss_items()
        self.assertEqual(len(items), 1)                                 # 条目留着
        self.assertTrue(items[0]["source_deleted"])

    def test_refresh_only_enabled(self) -> None:
        rss.fetch = self._fake_fetch
        rss.add_defaults(self.db)                                       # 5 个内置源
        first = self.db.rss_sources()[0]
        rss.save_source(self.db, {"id": first["id"], "enabled": False})
        res = rss.refresh(self.db)
        self.assertEqual(res["sources"], 4)
        self.assertEqual(res["fetched"], 4)
        self.assertNotIn(first["url"], self.fetched)

    def test_registered_route_dispatch(self) -> None:
        listed = rss_routes.route("GET", "/api/rss/sources", {}, {}, self.db)
        self.assertEqual(listed, {"sources": [], "defaults": rss.DEFAULT_SOURCES})
        defaults = rss_routes.route("POST", "/api/rss/sources/defaults", {}, {}, self.db)
        self.assertEqual(defaults["added"], 5)
        saved = rss_routes.route("POST", "/api/rss/sources/save",
                                 {"url": "https://route.example/rss"}, {}, self.db)
        source_id = saved["source"]["id"]
        deleted = rss_routes.route("POST", "/api/rss/sources/delete",
                                   {"id": source_id}, {}, self.db)
        self.assertEqual(deleted["deleted"], source_id)
        rss.fetch = self._fake_fetch
        refreshed = rss_routes.route("POST", "/api/rss/refresh", {}, {}, self.db)
        self.assertEqual(refreshed["fetched"], 5)
        self.assertIsNone(rss_routes.route("GET", "/api/rss/refresh", {}, {}, self.db))

    def test_cleared_sources_are_not_reseeded(self) -> None:
        rss.fetch = self._fake_fetch
        rss.add_defaults(self.db)
        for s in self.db.rss_sources():
            rss.delete_source(self.db, {"id": s["id"]})
        self.assertEqual(self.db.rss_sources(), [])
        self.assertEqual(rss.refresh(self.db)["sources"], 0)             # 用户删光了：不许再补内置源
        self.assertEqual(self.db.rss_sources(), [])
        self.assertEqual(rss.add_defaults(self.db)["added"], 5)          # 想补回来得显式点


if __name__ == "__main__":
    unittest.main(verbosity=2)
