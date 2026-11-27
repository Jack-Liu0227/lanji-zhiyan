"""Named cumulative upgrades and distributed historical archive behavior."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

import migrations


class NamedMigrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scripts = Path(self.tmp.name) / "scripts"
        self.scripts.mkdir()
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)

    def write(self, name, sql):
        path = self.scripts / (name + ".sql")
        path.write_text(sql, encoding="utf-8", newline="\n")
        return path

    def run_scripts(self):
        return migrations.run_migrations(self.db, self.scripts)

    def test_same_name_replacement_updates_existing_rows_without_duplicate_inserts(self):
        self.write("project_fields", "CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT);\n"
                   "INSERT OR IGNORE INTO items VALUES(1, 'original');")
        self.assertEqual(self.run_scripts(), ["project_fields"])
        self.assertEqual(self.run_scripts(), [])
        self.write("project_fields", "CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT);\n"
                   "INSERT OR IGNORE INTO items VALUES(1, 'original');\n"
                   "ALTER TABLE items ADD COLUMN extra TEXT DEFAULT 'new';")
        self.assertEqual(self.run_scripts(), ["project_fields"])
        self.assertEqual(self.db.execute("SELECT * FROM items").fetchall(), [(1, "original", "new")])
        self.assertEqual(self.run_scripts(), [])
        self.assertEqual(self.db.execute("SELECT name FROM schema_patch_runs ORDER BY id").fetchall(),
                         [("project_fields",), ("project_fields",)])

    def test_dependency_change_reapplies_current_downstream_patch(self):
        self.write("upstream", "CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT);\n"
                   "INSERT OR IGNORE INTO items VALUES(1, 'first');")
        self.write("downstream", "-- depends_on: upstream\nCREATE TABLE IF NOT EXISTS summaries(value TEXT);\n"
                   "DELETE FROM summaries; INSERT INTO summaries SELECT value FROM items;")
        self.assertEqual(self.run_scripts(), ["upstream", "downstream"])
        self.write("upstream", "CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, value TEXT);\n"
                   "INSERT OR IGNORE INTO items VALUES(1, 'first');\n"
                   "UPDATE items SET value='second' WHERE value='first';")
        self.assertEqual(self.run_scripts(), ["upstream", "downstream"])
        self.assertEqual(self.db.execute("SELECT value FROM summaries").fetchall(), [("second",)])

    def test_old_signature_is_not_confused_with_latest_state(self):
        first = "CREATE TABLE IF NOT EXISTS items(value TEXT);\nDELETE FROM items; INSERT INTO items VALUES('first');"
        self.write("current_state", first)
        self.run_scripts()
        self.write("current_state", first.replace("'first'", "'second'"))
        self.run_scripts()
        self.write("current_state", first)
        self.assertEqual(self.run_scripts(), ["current_state"])
        self.assertEqual(self.db.execute("SELECT value FROM items").fetchall(), [("first",)])

    def test_line_endings_do_not_reapply_content(self):
        path = self.write("probe", "CREATE TABLE IF NOT EXISTS probe(id INTEGER);\n")
        self.run_scripts()
        path.write_bytes(b"\xef\xbb\xbfCREATE TABLE IF NOT EXISTS probe(id INTEGER);\r\n")
        self.assertEqual(self.run_scripts(), [])

    def test_invalid_dependency_graph_fails_before_database_changes(self):
        self.write("first", "-- depends_on: second\nCREATE TABLE probe(id INTEGER);")
        with self.assertRaisesRegex(ValueError, "Missing named"):
            self.run_scripts()
        self.write("second", "-- depends_on: first\nSELECT 1;")
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.run_scripts()
        self.assertEqual(self.db.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_failed_patch_and_receipt_remain_retryable(self):
        self.write("probe", "CREATE TABLE probe(id INTEGER); INSERT INTO missing VALUES(1);")
        with self.assertRaises(RuntimeError):
            self.run_scripts()
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchone())
        self.assertEqual(self.db.execute("SELECT name FROM schema_patch_runs").fetchall(), [])
        self.write("probe", "CREATE TABLE IF NOT EXISTS probe(id INTEGER);")
        self.assertEqual(self.run_scripts(), ["probe"])
        self.db.executescript("CREATE TRIGGER reject_receipt BEFORE INSERT ON schema_patch_runs "
                             "BEGIN SELECT RAISE(ABORT, 'receipt rejected'); END;")
        self.write("probe", "CREATE TABLE IF NOT EXISTS probe(id INTEGER); ALTER TABLE probe ADD COLUMN extra TEXT;")
        with self.assertRaisesRegex(RuntimeError, "receipt rejected"):
            self.run_scripts()
        self.assertEqual([row[1] for row in self.db.execute("PRAGMA table_info(probe)")], ["id"])
        self.db.execute("DROP TRIGGER reject_receipt")
        self.db.commit()
        self.assertEqual(self.run_scripts(), ["probe"])

    def test_archived_historical_sql_remains_readable(self):
        sql = migrations.read_migration_sql("020_project_workflow_nodes")
        self.assertIn("project_workflow_definitions", sql)
        self.assertIn("project_task_output_revisions", sql)

    def test_corrupt_archive_is_rejected_before_touching_database(self):
        archive = Path(self.tmp.name) / "archive"
        shutil.copytree(migrations.DEFAULT_ARCHIVE_DIR, archive)
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        (archive / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            migrations.run_migrations(self.db, self.scripts, archive_dir=archive)
        self.assertEqual(self.db.execute("SELECT name FROM sqlite_master").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
