"""easyScholar configuration, badge parsing, and cache tests."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import easyscholar
import migrations


class EasyScholarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-easyscholar-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_config_and_badge_parsing(self):
        cfg = easyscholar.get_config(self.conn)
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["key_present"])

        saved = easyscholar.save_config(self.conn, enabled=True, secret_key="test-secret-key-12345")
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["enabled"])
        self.assertTrue(saved["key_present"])

        cfg2 = easyscholar.get_config(self.conn)
        self.assertTrue(cfg2["enabled"])
        self.assertTrue(cfg2["key_present"])
        self.assertEqual(cfg2["secret_key"], "test-secret-key-12345")

        official = {
            "all": {
                "sciUp": "1",
                "sciUpTop": "1",
                "sciwarn": "0",
                "sci": "Q1",
                "sciif": "15.82",
                "ccf": "A",
                "pku": "1",
                "cssci": "1",
            }
        }
        custom = {
            "rankInfo": [{"uuid": "custom_001", "abbName": "DUFE", "threeRankText": "B"}],
            "rank": ["custom_001&&&3"],
        }
        badges = easyscholar.parse_badges(official, custom)
        labels = [b["label"] for b in badges]
        self.assertIn("中科院1区", labels)
        self.assertIn("TOP", labels)
        self.assertIn("JCR Q1", labels)
        self.assertIn("IF 15.82", labels)
        self.assertIn("CCF-A", labels)
        self.assertIn("北大核心", labels)
        self.assertIn("南大核心", labels)
        self.assertIn("DUFE B", labels)

    def test_cache_and_expiration(self):
        official = {"all": {"sciUp": "1", "sci": "Q1", "sciif": "50.5"}}
        custom = {"rankInfo": [], "rank": []}
        self.conn.execute(
            "INSERT OR REPLACE INTO easyscholar_cache (publication_name, official_rank, custom_rank, updated_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            ("nature", json.dumps(official), json.dumps(custom)),
        )
        self.conn.commit()

        res = easyscholar.query_publication_rank(self.conn, "Nature")
        self.assertTrue(res["ok"])
        labels = [b["label"] for b in res["badges"]]
        self.assertIn("中科院1区", labels)
        self.assertIn("JCR Q1", labels)
        self.assertIn("IF 50.5", labels)


if __name__ == "__main__":
    unittest.main()
