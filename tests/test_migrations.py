"""Tests for SQLite versioned migrations and backup."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
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
        self.assertEqual(versions, ["001_core_schema", "002_workflow_schema", "003_reliability_tables", "004_v130_features", "005_asset_graph_upgrade", "006_team_task_dag", "007_search_rank", "008_semantic", "009_step_delivery", "010_workflow_coordination"])

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

    def test_run_migrations_is_idempotent(self):
        applied1 = migrations.run_migrations(self.conn, db_path=self.db_path)
        self.assertTrue(len(applied1) >= 3)
        applied2 = migrations.run_migrations(self.conn, db_path=self.db_path)
        self.assertEqual(applied2, [])

    def test_backup_failure_aborts_migration_before_schema_changes(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_add_feature.sql").write_text(
            "CREATE TABLE feature_data (id INTEGER);", encoding="utf-8")
        self.conn.execute("CREATE TABLE existing_data (id INTEGER)")
        self.conn.commit()

        with patch.object(migrations, "backup_database", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                migrations.run_migrations(self.conn, migrations_dir=scripts, db_path=self.db_path)

        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='feature_data'").fetchone())
        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())

    def test_missing_or_empty_scripts_fail_before_creating_partial_database(self):
        missing = Path(self.tmp.name) / "missing"
        for directory in (missing, Path(self.tmp.name)):
            with self.assertRaisesRegex(FileNotFoundError, "migration scripts are missing"):
                migrations.run_migrations(self.conn, migrations_dir=directory)
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master").fetchall(), [])

    def test_backup_creates_checkpoint_file(self):
        migrations.run_migrations(self.conn, db_path=self.db_path)
        backup = migrations.backup_database(self.db_path)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())
    def test_backup_is_a_verified_pre_migration_snapshot(self):
        self.conn.execute("CREATE TABLE existing_data (value TEXT)")
        self.conn.execute("INSERT INTO existing_data VALUES ('keep')")
        self.conn.commit()
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_add_feature.sql").write_text(
            "CREATE TABLE feature_data (value TEXT);", encoding="utf-8")

        migrations.run_migrations(self.conn, migrations_dir=scripts, db_path=self.db_path)

        backups = list(Path(self.tmp.name).glob("test_mig.sqlite3.bak-*"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(backup.execute("SELECT value FROM existing_data").fetchone()[0], "keep")
            self.assertIsNone(backup.execute(
                "SELECT name FROM sqlite_master WHERE name='feature_data'").fetchone())

    def test_failed_migration_rolls_back_its_schema_changes(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        (scripts / "001_broken.sql").write_text(
            "CREATE TABLE partial_change (id INTEGER); CREATE TABLE partial_change (id INTEGER);",
            encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Migration failed"):
            migrations.run_migrations(self.conn, migrations_dir=scripts)

        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='partial_change'").fetchone())
        self.assertEqual(self.conn.execute("SELECT version FROM schema_migrations").fetchall(), [])

    def test_checksum_drift_is_rejected_before_any_pending_migration(self):
        scripts = Path(self.tmp.name) / "scripts"
        scripts.mkdir()
        migration = scripts / "001_base.sql"
        migration.write_text("CREATE TABLE base_data (id INTEGER);", encoding="utf-8")
        migrations.run_migrations(self.conn, migrations_dir=scripts)
        migration.write_text("CREATE TABLE base_data (id INTEGER, value TEXT);", encoding="utf-8")
        (scripts / "002_next.sql").write_text("CREATE TABLE next_data (id INTEGER);", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            migrations.run_migrations(self.conn, migrations_dir=scripts)

        self.assertIsNone(self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name='next_data'").fetchone())


if __name__ == "__main__":
    unittest.main()
