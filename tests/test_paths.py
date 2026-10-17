"""paths.py 自检：唯一路径来源 / 建树 / 旧 data 迁移 / 环境变量覆盖。

跑：python -m unittest tests.test_paths -v
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "workspace-core"))


class PathsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="rw-paths-"))
        self._saved = {k: os.environ.get(k) for k in
                       ("RW_HOME", "RESEARCH_WORKBENCH_DATA", "RESEARCH_WORKBENCH_DB")}
        os.environ["RW_HOME"] = str(self.home)
        os.environ.pop("RESEARCH_WORKBENCH_DATA", None)
        os.environ.pop("RESEARCH_WORKBENCH_DB", None)
        import paths
        self.paths = importlib.reload(paths)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import paths
        importlib.reload(paths)

    def test_layout_created_under_home(self) -> None:
        self.paths.ensure_layout()
        for d in (self.paths.DATA, self.paths.USER, self.paths.BACKUP,
                  self.paths.EXECUTORS / "agents", self.paths.EXECUTORS / "skills",
                  self.paths.EXECUTORS / "tools"):
            self.assertTrue(d.is_dir(), d)
        meta = json.loads((self.home / "layout.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["layout"], 2)

    def test_db_and_mail_are_not_in_app_tree(self) -> None:
        self.assertEqual(self.paths.DB_PATH, self.home / "data" / "research.sqlite3")
        self.assertEqual(self.paths.MAIL_FILE, self.home / "user" / "mail.json")
        self.assertNotEqual(self.paths.DATA, self.paths.APP / "data")

    def test_legacy_migration_copies_verifies_and_is_idempotent(self) -> None:
        legacy = self.home / "legacy-data"
        (legacy / "papers").mkdir(parents=True)
        db = sqlite3.connect(legacy / "research.sqlite3")
        db.execute("CREATE TABLE research_tasks(id TEXT)")
        db.execute("INSERT INTO research_tasks VALUES('t1')")
        db.commit()
        db.close()
        (legacy / "papers" / "a.pdf").write_bytes(b"%PDF-1.4")
        (legacy / "mail.json").write_text('{"account":"a@b.c"}', encoding="utf-8")

        self.paths.ensure_layout()
        out = self.paths.migrate_legacy(legacy)
        self.assertTrue(out["done"], out)
        self.assertTrue(self.paths.DB_PATH.is_file())
        self.assertTrue((self.paths.PAPERS / "a.pdf").is_file())
        self.assertTrue(self.paths.MAIL_FILE.is_file())
        self.assertFalse(legacy.is_dir(), "旧目录应已改名保留")
        self.assertFalse(self.paths.migrate_legacy(legacy)["done"], "第二次应跳过")

    def test_env_override_wins(self) -> None:
        os.environ["RESEARCH_WORKBENCH_DB"] = str(self.home / "custom.sqlite3")
        paths = importlib.reload(self.paths)
        self.assertEqual(paths.DB_PATH, self.home / "custom.sqlite3")


if __name__ == "__main__":
    unittest.main()
