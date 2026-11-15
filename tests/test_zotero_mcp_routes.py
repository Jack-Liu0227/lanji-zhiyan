"""Zotero MCP module route contract."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrations
import zotero_mcp_routes


class ZoteroMcpRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="rw-zotero-mcp-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

        class MockDB:
            def __init__(self, conn):
                self.db = conn

        self.db = MockDB(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_status_enable_status_round_trip(self):
        before = zotero_mcp_routes.route("GET", "/api/zotero/mcp/status", {}, {}, self.db)
        self.assertIn("registered", before)

        enabled = zotero_mcp_routes.route("POST", "/api/zotero/mcp/enable", {}, {}, self.db)
        self.assertTrue(enabled["ok"])

        after = zotero_mcp_routes.route("GET", "/api/zotero/mcp/status", {}, {}, self.db)
        self.assertTrue(after["registered"])
        self.assertTrue(after["enabled"])
        self.assertEqual(after["server"], enabled["server"])
        self.assertIsNone(zotero_mcp_routes.route("GET", "/api/unknown", {}, {}, self.db))


if __name__ == "__main__":
    unittest.main()
