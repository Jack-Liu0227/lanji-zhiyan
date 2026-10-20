"""文件安全三件套 + key 域名绑定：这些是安全路径，必须有能红的检查。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import fileguard
import workflow


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self._snap, self._audit = fileguard.SNAP, fileguard.AUDIT   # 别写到真 data/
        fileguard.SNAP = self.dir / "snap"
        fileguard.AUDIT = self.dir / "audit.jsonl"
        self.addCleanup(self._restore)

    def _restore(self):
        fileguard.SNAP, fileguard.AUDIT = self._snap, self._audit

    # ---------- 子进程环境 ----------
    def test_clean_env_drops_secrets_keeps_path(self):
        os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
        os.environ["RESEARCH_WORKBENCH_DB"] = "C:/secret.sqlite3"
        os.environ["GITHUB_TOKEN"] = "ghp_nope"
        try:
            env = fileguard.clean_env("C:/tex/bin")
        finally:
            for k in ("OPENAI_API_KEY", "RESEARCH_WORKBENCH_DB", "GITHUB_TOKEN"):
                os.environ.pop(k, None)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("RESEARCH_WORKBENCH_DB", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertTrue(env["PATH"].startswith("C:/tex/bin"))

    # ---------- 快照 / 回滚 ----------
    def test_before_write_snapshots_then_rollback_restores(self):
        f = self.dir / "正文.md"
        f.write_text("第一版", encoding="utf-8")
        self.assertIsNotNone(fileguard.before_write(f, "测试"))
        f.write_text("第二版（覆盖）", encoding="utf-8")
        out = fileguard.rollback(f)
        self.assertTrue(out["ok"])
        self.assertEqual(f.read_text(encoding="utf-8"), "第一版")
        self.assertEqual(len(fileguard.snapshots(f)), 1)

    def test_before_write_skips_missing_or_empty(self):
        self.assertIsNone(fileguard.before_write(self.dir / "没有这个.md"))
        empty = self.dir / "空.md"
        empty.write_text("", encoding="utf-8")
        self.assertIsNone(fileguard.before_write(empty))

    def test_rollback_without_snapshot_refuses(self):
        f = self.dir / "没写过.md"
        f.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            fileguard.rollback(f)

    def test_snapshot_retention_keeps_last_n(self):
        f = self.dir / "多版.md"
        for i in range(fileguard.KEEP_PER_FILE + 4):
            f.write_text(f"v{i}", encoding="utf-8")
            fileguard.before_write(f)
        self.assertLessEqual(len(fileguard.snapshots(f)), fileguard.KEEP_PER_FILE)

    def test_audit_written_as_jsonl(self):
        f = self.dir / "审计.md"
        f.write_text("a", encoding="utf-8")
        fileguard.before_write(f, "测试")
        fileguard.audit("save", f, bytes=1)
        rows = [json.loads(x) for x in fileguard.AUDIT.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["action"] for r in rows], ["snapshot", "save"])
        self.assertTrue(all("ts" in r for r in rows))

    # ---------- 打开文件 ----------
    def test_open_document_refuses_executables(self):
        for name in ("payload.exe", "run.bat", "x.ps1", "某个.lnk", "noext"):
            p = self.dir / name
            p.write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError, msg=name):
                fileguard.open_document(p)

    def test_open_document_allows_pdf(self):
        p = self.dir / "paper.pdf"
        p.write_bytes(b"%PDF-1.4")
        with patch.object(os, "startfile", create=True) as fake:
            out = fileguard.open_document(p)
        fake.assert_called_once()
        self.assertEqual(out["opened"], str(p))


class KeyHostTest(unittest.TestCase):
    """key 与域名绑定：换了接口域名就不许拿旧 key 发。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "k.sqlite3")
        self.addCleanup(self.db.close)

    def test_key_host_recorded_and_enforced(self):
        p = self.db.add_provider("测试", "https://api.deepseek.com/v1", api_key="sk-abc123")
        self.assertEqual(p["key_host"], "api.deepseek.com")
        row = self.db.db.execute("SELECT key_host FROM providers WHERE id=?", (p["id"],)).fetchone()
        self.assertEqual(row["key_host"], "api.deepseek.com")

        same = {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-abc123",
                "key_host": p["key_host"]}
        workflow._assert_key_host(same)                       # 同域名：放行

        moved = {**same, "base_url": "https://evil.example.com/v1"}
        with self.assertRaises(RuntimeError):
            workflow._assert_key_host(moved)

    def test_model_profile_gets_key_host_through_sync(self):
        p = self.db.add_provider("测试", "https://api.example.test/v1", api_key="sk-x")
        self.db.adopt_model(p["id"], "m1")
        self.db.set_provider_key(p["id"], "sk-y")             # 走 sync_provider_models 那条写路径
        rows = self.db.db.execute("SELECT key_host, api_key FROM model_profiles WHERE provider_id=?",
                                  (p["id"],)).fetchall()
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["key_host"], "api.example.test")
            self.assertTrue(str(r["api_key"]).startswith(("dpapi:", "local:")))   # 库里不是明文


if __name__ == "__main__":
    unittest.main()
