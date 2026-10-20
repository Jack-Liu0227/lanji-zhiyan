"""Dispatch tests for the extended feature routes."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import feature_routes
import migrations


class FeatureRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-feature-routes-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)
        self.conn.execute(
            "INSERT OR IGNORE INTO research_tasks (id, title, created_at, updated_at) "
            "VALUES ('t1', '测试项目', datetime('now'), datetime('now'))"
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO md_notes (id, title, content, task_id, created_at, updated_at) "
            "VALUES ('n1', '测试笔记', '正文', 't1', datetime('now'), datetime('now'))"
        )
        self.conn.commit()

        class MockDB:
            def __init__(self, conn):
                self.db = conn

            def papers_of(self, tid):
                return []

            def notes(self, tid):
                return []

            def evidence(self, tid):
                return []

        self.db = MockDB(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_dispatch(self):
        add_res = feature_routes.route("POST", "/api/todos/add", {"task_id": "t1", "title": "路由测试待办"}, {}, self.db)
        self.assertIsNotNone(add_res)
        self.assertEqual(add_res["title"], "路由测试待办")
        todo_id = add_res["id"]

        list_res = feature_routes.route("GET", "/api/todos", {}, {"task_id": ["t1"]}, self.db)
        self.assertEqual(len(list_res["todos"]), 1)

        toggled = feature_routes.route("POST", "/api/todos/toggle", {"id": todo_id}, {}, self.db)
        self.assertEqual(toggled["status"], "DONE")

        deleted = feature_routes.route("POST", "/api/todos/delete", {"id": todo_id}, {}, self.db)
        self.assertTrue(deleted["ok"])

        cfg_res = feature_routes.route(
            "POST", "/api/easyscholar/config", {"enabled": True, "secret_key": "sec_abc"}, {}, self.db
        )
        self.assertTrue(cfg_res["ok"])

        cfg_get = feature_routes.route("GET", "/api/easyscholar/config", {}, {}, self.db)
        self.assertTrue(cfg_get["enabled"])
        self.assertEqual(cfg_get["secret_key"], "sec_abc")

        rank_res = feature_routes.route("GET", "/api/easyscholar/rank", {}, {"journal": ["Nature"]}, self.db)
        self.assertIn("badges", rank_res)

        mcp_status = feature_routes.route("GET", "/api/zotero/mcp/status", {}, {}, self.db)
        self.assertIn("registered", mcp_status)

        mcp_enabled = feature_routes.route("POST", "/api/zotero/mcp/enable", {}, {}, self.db)
        self.assertTrue(mcp_enabled["ok"])

        sp_search = feature_routes.route("GET", "/api/space/search", {}, {"task_id": ["t1"], "q": ["测试"]}, self.db)
        self.assertIn("results", sp_search)

        sp_notes = feature_routes.route("GET", "/api/space/notes", {}, {"task_id": ["t1"]}, self.db)
        self.assertIn("notes", sp_notes)

        sp_note = feature_routes.route("GET", "/api/space/note", {}, {"id": ["n1"]}, self.db)
        self.assertEqual(sp_note["note"]["id"], "n1")


if __name__ == "__main__":
    unittest.main()
