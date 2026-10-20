"""Project space note listing and search tests."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrations
import space_service


class SpaceServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-space-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_notes_and_search(self):
        self.conn.execute(
            "INSERT INTO research_tasks (id, title, question, created_at, updated_at) "
            "VALUES ('t1', '钙钛矿电池研究', '如何提升光电转化效率', '2026-10-18T00:00:00', '2026-10-18T00:00:00')"
        )
        self.conn.execute(
            "INSERT INTO md_notes (id, title, content, task_id, paper_id, created_at, updated_at) "
            "VALUES ('n1', '钙钛矿实验记录', '测试了碘化铅在不同温度下的结晶行为', 't1', '', "
            "'2026-10-18T00:00:00', '2026-10-18T00:00:00')"
        )
        self.conn.commit()

        class MockDB:
            def __init__(self, conn):
                self.db = conn

            def papers_of(self, tid):
                return [{"id": "p1", "title": "Perovskite Solar Cells Efficiency", "authors": "Zhang et al.",
                         "year": "2024", "doi": "10.1038/s41586-024-001", "venue": "Nature",
                         "abstract": "High efficiency perovskite cells"}]

            def notes(self, tid):
                return []

            def evidence(self, tid):
                return [{"id": "e1", "paper_id": "p1", "page": 3, "claim": "效率突破 25%",
                         "quote": "Achieved 25.4% certified PCE"}]

        mock = MockDB(self.conn)
        res = space_service.search_project_space(mock, "t1", "perovskite")
        self.assertEqual(len(res["papers"]), 1)
        self.assertEqual(res["papers"][0]["id"], "p1")

        res_note = space_service.search_project_space(mock, "t1", "结晶")
        self.assertEqual(len(res_note["notes"]), 1)
        self.assertEqual(res_note["notes"][0]["id"], "n1")

        p_notes = space_service.list_project_notes(mock, "t1")
        self.assertEqual(len(p_notes), 1)
        self.assertEqual(p_notes[0]["title"], "钙钛矿实验记录")

        detail = space_service.get_project_note_detail(mock, "n1")
        self.assertEqual(detail["title"], "钙钛矿实验记录")
        self.assertIn("碘化铅", detail["content"])


if __name__ == "__main__":
    unittest.main()
