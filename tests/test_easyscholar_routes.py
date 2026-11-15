import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyscholar_routes import route
import migrations


class EasyScholarRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-easyscholar-module-")
        self.conn = sqlite3.connect(Path(self.tmp.name) / "test.sqlite3")
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_config_routes_are_owned_by_the_module(self):
        saved = route("POST", "/api/easyscholar/config",
                      {"enabled": True, "secret_key": "sec_test"}, {}, self.conn)
        self.assertTrue(saved["ok"])
        loaded = route("GET", "/api/easyscholar/config", {}, {}, self.conn)
        self.assertTrue(loaded["enabled"])
        self.assertTrue(loaded["key_present"])
        self.assertNotIn("secret_key", loaded)

    def test_batch_rank_route_returns_summary_for_search_cards(self):
        result = {"ok": True, "badges": [{"label": "中科院1区"}],
                  "officialRank": {"all": {"sciUp": "1", "sciUpTop": "1", "sci": "Q1"}}}
        with patch("easyscholar_routes.easyscholar.query_publication_rank", return_value=result):
            out = route("POST", "/api/easyscholar/ranks", {"venues": ["Nature"]}, {}, self.conn)
        self.assertEqual(out["ranks"]["Nature"]["tag"], "中科院1区")
        self.assertEqual(out["ranks"]["Nature"]["sci"], "1")
        self.assertTrue(out["ranks"]["Nature"]["top"])
        self.assertEqual(out["ranks"]["Nature"]["jcr"], "Q1")

    def test_batch_rank_route_rejects_invalid_or_oversized_lists(self):
        with self.assertRaises(ValueError):
            route("POST", "/api/easyscholar/ranks", {"venues": "Nature"}, {}, self.conn)
        with self.assertRaises(ValueError):
            route("POST", "/api/easyscholar/ranks", {"venues": [str(i) for i in range(31)]}, {}, self.conn)

    def test_other_paths_are_declined(self):
        self.assertIsNone(route("GET", "/api/todos", {}, {}, self.conn))


if __name__ == "__main__":
    unittest.main()
