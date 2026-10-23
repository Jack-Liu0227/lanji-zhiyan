"""Tests for SQLite versioned migrations and backup."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

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

    def test_backup_creates_checkpoint_file(self):
        migrations.run_migrations(self.conn, db_path=self.db_path)
        backup = migrations.backup_database(self.db_path)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())


if __name__ == "__main__":
    unittest.main()
