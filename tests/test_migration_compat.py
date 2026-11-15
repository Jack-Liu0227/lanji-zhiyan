"""老库里已存在的列不能把 024+ 迁移链卡在半路。

1.0 早期版本用 PRAGMA 手工补过 chats.channel / model_id / reasoning / permission，
2.0 的 024+ 迁移又 ADD 同名列 —— 不拦的话整条链停在 024，库卡在中间版本，程序起不来。
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "database"))
import migrations


class MigrationCompatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.all = sorted(migrations.DEFAULT_MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)

    def stage(self, name, files):
        target = self.tmp / name
        target.mkdir()
        for f in files:
            shutil.copy2(f, target / f.name)
        return target

    def seeded(self, tag):
        """跑到 023，然后装成「1.0 早期已经手工补过 channel 列」的老库。"""
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        early = [f for f in self.all if f.name < "024"]
        migrations.run_migrations(conn, self.stage(tag + "-early", early))
        conn.execute("ALTER TABLE chats ADD COLUMN channel TEXT NOT NULL DEFAULT 'conversation'")
        conn.commit()
        return conn

    def test_legacy_channel_column_does_not_stop_the_chain(self):
        conn = self.seeded("ok")
        applied = migrations.run_migrations(conn, self.stage("ok-full", self.all))
        self.assertIn("024_execution_workspace", applied)
        self.assertIn("035_zotero_staging_compat", applied)
        self.assertEqual(len(applied), 12, "024 到 035 共 12 个迁移都要落地")

    def test_without_the_guard_the_chain_would_break(self):
        """反证：把守卫换成恒等函数，同一场景必须炸 —— 否则上面那条测试什么也没保住。"""
        conn = self.seeded("反证")
        original = migrations.skip_existing_columns
        migrations.skip_existing_columns = lambda conn, sql: sql
        try:
            with self.assertRaises(RuntimeError) as ctx:
                migrations.run_migrations(conn, self.stage("反证-full", self.all))
            self.assertIn("duplicate column name", str(ctx.exception))
            self.assertIn("024_execution_workspace.sql", str(ctx.exception))
        finally:
            migrations.skip_existing_columns = original


if __name__ == "__main__":
    unittest.main()
