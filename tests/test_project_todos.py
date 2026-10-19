"""Project TODO lifecycle tests."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import migrations
import project_todos


class ProjectTodosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-todos-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_lifecycle(self):
        t1 = project_todos.add_todo(self.conn, "task_1", "测试待办1", "待办描述", "P0")
        self.assertEqual(t1["title"], "测试待办1")
        self.assertEqual(t1["status"], "TODO")
        self.assertEqual(t1["priority"], "P0")

        todos = project_todos.list_todos(self.conn, "task_1")
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["id"], t1["id"])

        toggled = project_todos.toggle_todo(self.conn, t1["id"])
        self.assertEqual(toggled["status"], "DONE")

        updated = project_todos.update_todo(self.conn, t1["id"], title="已修改待办", priority="P2")
        self.assertEqual(updated["title"], "已修改待办")
        self.assertEqual(updated["priority"], "P2")

        created = project_todos.import_workflow_stages_as_todos(self.conn, "task_1")
        self.assertEqual(len(created), 10)

        deleted = project_todos.delete_todo(self.conn, t1["id"])
        self.assertTrue(deleted["ok"])
        todos_after = project_todos.list_todos(self.conn, "task_1")
        self.assertEqual(len(todos_after), 10)


if __name__ == "__main__":
    unittest.main()
