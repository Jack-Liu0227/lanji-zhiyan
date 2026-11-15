"""Tests for SQLite versioned migrations and backup."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import migrations


class MigrationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-test-mig-")
        self.db_path = Path(self.tmp.name) / "test_mig.sqlite3"
        self.conn = sqlite3.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_run_migrations_applies_all_versioned_sql(self):
        applied = migrations.run_migrations(self.conn, db_path=self.db_path)
        self.assertIn("001_core_schema", applied)
        self.assertIn("002_workflow_schema", applied)
        self.assertIn("003_reliability_tables", applied)
        self.assertIn("004_v130_features", applied)

        rows = self.conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version").fetchall()
        versions = [r[0] for r in rows]
        expected = sorted(p.stem for p in migrations.DEFAULT_MIGRATIONS_DIR.glob("*.sql"))
        self.assertEqual(versions, expected)
        self.assertEqual(applied, expected)

        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("research_tasks", tables)
        self.assertIn("calendar_events", tables)
        self.assertIn("tasks", tables)
        self.assertIn("task_attempts", tables)
        self.assertIn("run_events", tables)
        self.assertIn("task_links", tables)
        self.assertIn("audit_events", tables)
        self.assertIn("easyscholar_cache", tables)
        self.assertIn("project_todos", tables)
        self.assertIn("integration_settings", tables)
        self.assertIn("prompt_template_bindings", tables)

    def test_run_migrations_is_idempotent(self):
        applied1 = migrations.run_migrations(self.conn, db_path=self.db_path)
        self.assertTrue(len(applied1) >= 3)
        applied2 = migrations.run_migrations(self.conn, db_path=self.db_path)
        self.assertEqual(applied2, [])

    def test_missing_or_empty_scripts_fail_before_creating_partial_database(self):
        missing = Path(self.tmp.name) / "missing"
        for directory in (missing, Path(self.tmp.name)):
            with self.assertRaisesRegex(FileNotFoundError, "migration scripts are missing"):
                migrations.run_migrations(self.conn, migrations_dir=directory)
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_failed_migration_rolls_back_schema_and_data_and_can_retry(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_base.sql").write_text(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT);\n"
            "INSERT INTO items VALUES (1, 'original');\n", encoding="utf-8")
        upgrade = scripts / "002_upgrade.sql"
        valid_sql = (
            "ALTER TABLE items ADD COLUMN extra TEXT;\n"
            "UPDATE items SET value = 'upgraded';\n"
            "CREATE TABLE details (id INTEGER);\n"
        )
        upgrade.write_text(valid_sql + "INSERT INTO missing_table VALUES (1);\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Migration failed at 002_upgrade.sql"):
            migrations.run_migrations(self.conn, migrations_dir=scripts)

        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT * FROM items").fetchall(), [(1, "original")])
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='details'").fetchone())
        self.assertEqual(self.conn.execute("SELECT version FROM schema_migrations").fetchall(), [("001_base",)])

        upgrade.write_text(valid_sql, encoding="utf-8")
        self.assertEqual(migrations.run_migrations(self.conn, migrations_dir=scripts), ["002_upgrade"])
        self.assertEqual(self.conn.execute("SELECT * FROM items").fetchall(), [(1, "upgraded", None)])
        self.assertEqual(migrations.run_migrations(self.conn, migrations_dir=scripts), [])

    def test_failed_migration_record_rolls_back_the_script(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_probe.sql").write_text("CREATE TABLE probe (id INTEGER);", encoding="utf-8")
        migrations.ensure_migration_table(self.conn)
        self.conn.executescript("""
            CREATE TRIGGER reject_migration BEFORE INSERT ON schema_migrations
            BEGIN SELECT RAISE(ABORT, 'migration record rejected'); END;
        """)

        with self.assertRaisesRegex(RuntimeError, "migration record rejected"):
            migrations.run_migrations(self.conn, migrations_dir=scripts)

        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchone())
        self.assertEqual(self.conn.execute("SELECT version FROM schema_migrations").fetchall(), [])
        self.conn.execute("DROP TRIGGER reject_migration")
        self.assertEqual(migrations.run_migrations(self.conn, migrations_dir=scripts), ["001_probe"])

    def test_backup_includes_committed_wal_data(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA wal_autocheckpoint=0")
        migrations.run_migrations(self.conn, db_path=self.db_path)
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.execute(
            "INSERT INTO research_tasks (id, title, created_at, updated_at) "
            "VALUES ('saved-task', 'Keep me', 'now', 'now')")
        self.conn.commit()
        self.assertGreater(Path(str(self.db_path) + "-wal").stat().st_size, 0)

        backup = migrations.backup_database(self.db_path)
        self.assertIsNotNone(backup)
        with closing(sqlite3.connect(backup)) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT id, title FROM research_tasks").fetchall(),
                             [("saved-task", "Keep me")])
            self.assertEqual(restored.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall(),
                             self.conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall())

    def test_requested_backup_preserves_state_before_upgrade(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_base.sql").write_text("CREATE TABLE items (id INTEGER);", encoding="utf-8")
        migrations.run_migrations(self.conn, migrations_dir=scripts)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("INSERT INTO items VALUES (42)")
        self.conn.commit()
        (scripts / "002_upgrade.sql").write_text("ALTER TABLE items ADD COLUMN extra TEXT;", encoding="utf-8")

        self.assertEqual(migrations.run_migrations(
            self.conn, migrations_dir=scripts, db_path=self.db_path, create_backup=True), ["002_upgrade"])

        backups = list(self.db_path.parent.glob(self.db_path.name + ".bak-*"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as restored:
            self.assertEqual(restored.execute("SELECT * FROM items").fetchall(), [(42,)])
            self.assertEqual(restored.execute("SELECT version FROM schema_migrations").fetchall(), [("001_base",)])
        self.assertEqual(self.conn.execute("SELECT * FROM items").fetchall(), [(42, None)])

    def test_requested_backup_failure_stops_before_upgrade(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_probe.sql").write_text("CREATE TABLE probe (id INTEGER);", encoding="utf-8")
        with patch.object(migrations, "backup_database", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Database backup failed before migration"):
                migrations.run_migrations(
                    self.conn, migrations_dir=scripts, db_path=self.db_path, create_backup=True)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchone())
        self.assertEqual(self.conn.execute("SELECT version FROM schema_migrations").fetchall(), [])

    def test_failed_backup_does_not_leave_a_partial_file(self):
        corrupt = Path(self.tmp.name) / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a SQLite database")
        self.assertIsNone(migrations.backup_database(corrupt))
        self.assertEqual(list(corrupt.parent.glob(corrupt.name + ".bak-*")), [])


if __name__ == "__main__":
    unittest.main()
