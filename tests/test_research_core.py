import tempfile
import sqlite3
import unittest
from pathlib import Path

import migrations
from _pdf_fixture import write_text_pdf
from research_core import ManagedConnection, ResearchDB


class CoreTest(unittest.TestCase):
    def test_ingest_is_hash_deduplicated_and_evidence_keeps_page(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); pdf = root / "paper.pdf"
            write_text_pdf(pdf, ["CuO material was sintered at 800 C and reached 120 MPa."])
            db = ResearchDB(root / "research.sqlite3")
            task = db.create_task("Cu test", "What is the process?", "CuO")
            first = db.ingest_pdf(task, pdf); second = db.ingest_pdf(task, pdf)
            self.assertEqual(first["id"], second["id"])
            rows = db.extract_materials(first["id"])
            self.assertTrue(rows)
            self.assertTrue(all(r["page_no"] == 1 and r["excerpt"] for r in rows))
            self.assertEqual(db.summary(task)["papers"], 1)
            with self.assertRaises(ValueError):
                db.add_evidence(first["id"], {"field": "bad", "value": "1", "page_no": 1, "excerpt": "not in source"})
            accepted = db.add_evidence(first["id"], {"field": "method", "value": "sintered", "page_no": 1, "excerpt": "CuO material was sintered"})
            self.assertEqual(accepted["page_no"], 1)
            exported = db.export_evidence(task, root / "out" / "evidence.json")
            self.assertTrue(exported.is_file())
            self.assertIn("sintered", exported.read_text(encoding="utf-8"))
            report = db.export_report(task, root / "out" / "report.md")
            self.assertIn("CuO", report.read_text(encoding="utf-8"))
            self.assertIn("p.1", report.read_text(encoding="utf-8"))
            db.close()


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-research-core-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_managed_connection_and_transaction_context(self):
        mc = ManagedConnection(self.db_path)
        with mc.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO background_jobs (id, task_id, status, created_at, updated_at) "
                "VALUES ('job-t1', 't1', 'running', datetime('now'), datetime('now'))"
            )

        row = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "running")

        with self.assertRaises(ZeroDivisionError):
            with mc.transaction() as conn:
                conn.execute("UPDATE background_jobs SET status='done' WHERE id='job-t1'")
                _ = 1 / 0

        row2 = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertEqual(row2["status"], "running")

        db = ResearchDB(self.db_path)
        with db.transaction():
            db.db.execute("UPDATE background_jobs SET status='completed' WHERE id='job-t1'")
        db.close()

        row3 = self.conn.execute("SELECT status FROM background_jobs WHERE id='job-t1'").fetchone()
        self.assertEqual(row3["status"], "completed")


if __name__ == "__main__":
    unittest.main()
