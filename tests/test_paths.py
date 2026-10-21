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
from unittest.mock import patch

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

    def test_文件闸门只放行自己的目录和用户选过的目录(self) -> None:
        """resolve_allowed 是唯一白名单闸门：data/ 里的放行，系统目录/相对路径一律拒。"""
        inside = self.paths.DATA / "probe.txt"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("x", encoding="utf-8")
        self.assertEqual(self.paths.resolve_allowed(str(inside)), inside.resolve())
        for bad in ("C:/Windows/win.ini", "relative/path.txt", ""):
            with self.assertRaises(ValueError):
                self.paths.resolve_allowed(bad)
        with self.assertRaises(ValueError):                 # 越界要记一条 deny 审计
            self.paths.resolve_allowed("C:/Windows/system.ini")
        self.assertIn("deny", self.paths.AUDIT.read_text(encoding="utf-8"))

    def test_env_override_wins(self) -> None:
        os.environ["RESEARCH_WORKBENCH_DB"] = str(self.home / "custom.sqlite3")
        paths = importlib.reload(self.paths)
        self.assertEqual(paths.DB_PATH, self.home / "custom.sqlite3")


class WriteRootsStampTest(unittest.TestCase):
    """标 Low 只该跑一次：文件数没变就不再递归 icacls —— 那 1.5s 挡在「起子进程」之前。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["LANJI_ZHIYAN_HOME"] = str(Path(self.tmp.name) / "home")
        self.addCleanup(os.environ.pop, "LANJI_ZHIYAN_HOME", None)
        import paths
        self.p = importlib.reload(paths)              # 换 HOME → 戳文件也落在临时目录
        for d in (self.p.HOME, self.p.DATA, self.p.CACHE):
            d.mkdir(parents=True, exist_ok=True)
        (self.p.DATA / "a.txt").write_text("x", encoding="utf-8")
        self.p.GRANT = ""                             # 无通道 → 自己标

    def _run(self):
        with patch("lowbox.available", return_value=True), \
             patch("lowbox.mark_low", return_value=True) as m:
            return self.p.mark_write_roots_low(), m

    def test_整棵树只标一次_之后只做非递归(self) -> None:
        res, m = self._run()
        self.assertEqual(m.call_count, 1, "HOME 的 /T 已经覆盖 data/，不该再标第二遍")
        self.assertEqual(str(m.call_args_list[0].args[0]), str(self.p.HOME))
        self.assertTrue(m.call_args_list[0].kwargs.get("recursive"), "首次要把整棵树标掉")
        self.assertEqual(res[str(self.p.DATA)], "covered")

        _res, m = self._run()
        self.assertEqual(m.call_count, 2, "HOME + data 各一次非递归标")
        self.assertFalse(any(c.kwargs.get("recursive") for c in m.call_args_list),
                         "戳记里已有 → 以后不该再跑 /T（那 1.5s 就是启动慢的元凶）")


if __name__ == "__main__":
    unittest.main()

class GrantDirTest(unittest.TestCase):
    """Low IL 服务端写不进用户新选的中等完整性目录 → 得先请窗口宿主代建并标 Low。"""

    def test_没有通道时自己建目录(self):
        import paths
        old = paths.GRANT
        paths.GRANT = ""
        try:
            target = Path(tempfile.mkdtemp()) / "新项目"
            self.assertTrue(paths.grant_dir(target))
            self.assertTrue(target.is_dir())
        finally:
            paths.GRANT = old
