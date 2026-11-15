# 回归自检：开库不能把团队「复制」回 chats（智研管家左栏删不掉的根因）。
# python -m unittest test_chat_migrate -v
from __future__ import annotations
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, os.path.join(_ROOT, "packages", _n))

import pathlib
import tempfile
import unittest

_DB = pathlib.Path(tempfile.gettempdir()) / "chat_migrate_test.sqlite3"


class ChatMigrateTest(unittest.TestCase):
    def setUp(self):
        os.environ["RESEARCH_WORKBENCH_DB"] = str(_DB)
        import gc
        gc.collect()
        for p in (_DB, _DB.with_suffix(".sqlite3-wal"), _DB.with_suffix(".sqlite3-shm")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        self.workflow = __import__("workflow")

    def _open(self):
        self.workflow.WorkflowDB(str(_DB)).close()

    def _sql(self, q, args=()):
        import sqlite3
        c = sqlite3.connect(_DB)
        try:
            rows = list(c.execute(q, args))
            c.commit()
            return rows
        finally:
            c.close()

    def test_teams_do_not_become_chats(self):
        self._open()                                     # 首开：建表
        team = __import__("team")
        team.DB_PATH = str(_DB)                          # 不走 server 兜底
        with self.workflow.WorkflowDB(_DB) as db:
            project = db.create_task("Team migration")
        team.ensure_defaults(project)                    # 显式采用预置团队
        self.assertTrue(self._sql("SELECT 1 FROM teams LIMIT 1"), "固定团队应该被播种")
        self.assertEqual(self._sql("SELECT id FROM chats"), [], "团队不该被抄进 chats")

        self._open()                                     # 再开几次也都不许补
        self._open()
        self.assertEqual(self._sql("SELECT id FROM chats"), [], "重复开库不该resurrect任何对话")

    def test_deleted_chat_stays_deleted(self):
        self._open()
        self._sql("INSERT INTO chats (id,title,out_dir,created_at,updated_at) VALUES ('c1','论文写作','','t','t')")
        self._sql("DELETE FROM chats WHERE id='c1'")
        self._open()
        self.assertEqual(self._sql("SELECT id FROM chats"), [], "删掉的对话重启后不能复活")


if __name__ == "__main__":
    unittest.main()
