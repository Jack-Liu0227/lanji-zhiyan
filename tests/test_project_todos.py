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


class CurrentStageTest(unittest.TestCase):
    """当前阶段口径：十步里「按顺序最靠后的已完成步」，不是「第一处缺口」。

    用户跳着做（先写正文/转格式，精读还没补完），缺口口径会永远停在缺口那一步。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rw-stage-test-")
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        migrations.run_migrations(self.conn, db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _step(self, i, stage, status):
        self.conn.execute(
            "INSERT INTO steps (id,task_id,stage,status,summary,created_at) VALUES (?,?,?,?,?,?)",
            (f"s{i}", "t1", stage, status, "", f"2026-10-{i + 1:02d}T00:00:00+00:00"),
        )

    def test_last_done_step_wins_over_first_gap(self):
        import workflow
        for i, (stage, status) in enumerate([("idea", "done"), ("search", "done"), ("download", "done"),
                                             ("write", "done"), ("convert", "done"), ("notes", "running")]):
            self._step(i, stage, status)
        self._step(9, "agent", "done")            # agent 之类不属于十步，不能干扰
        self.assertEqual(workflow.current_stage_key(self.conn, "t1"), "convert")

    def test_nothing_done_falls_back_to_first_step(self):
        import workflow
        self.assertEqual(workflow.current_stage_key(self.conn, "t1"), "idea")

    def test_定时任务不往十步账上记账(self):
        """quiet_steps（定时任务）写进来的 write/convert 不能算进度 —— 日报曾把进度虚高 2 步。"""
        import workflow
        db = workflow.WorkflowDB(Path(self.db_path))
        try:
            db.quiet_steps = True
            self.assertTrue(db.log_step("t1", "write", "done", "每日文献推送 草稿 500 字")["skipped"])
            self.assertEqual(len(db.steps("t1")), 0)
            db.quiet_steps = False
            db.log_step("t1", "write", "done", "引言 草稿 500 字")
            self.assertEqual(len(db.steps("t1")), 1)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
