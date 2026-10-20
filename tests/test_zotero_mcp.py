"""Zotero MCP auto-registration test."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import capabilities
import migrations


class ZoteroMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-zotero-mcp-test-")
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

    def test_auto_registration(self):
        res = capabilities.ensure_zotero_mcp(self.db)
        self.assertTrue(res["ok"])
        args = res["server"].get("args") or [""]
        server_txt = (res["server"].get("description", "") or "") + " " + args[0]
        self.assertIn("zotero", server_txt.lower())

        cfg = capabilities._config(self.db)
        self.assertIn("zotero", cfg.get("mcpServers", {}))
        self.assertTrue(cfg["mcpServers"]["zotero"]["enabled"])


if __name__ == "__main__":
    unittest.main()
