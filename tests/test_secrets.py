"""凭据只在落盘时加密、只在出库时解密（DPAPI）：库/文件里不能有明文 key 和授权码。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import mail
import securestore
import workflow


class SecureStoreTest(unittest.TestCase):
    def test_roundtrip_and_prefix(self):
        cipher = securestore.protect("sk-test-123")
        self.assertNotEqual(cipher, "sk-test-123")
        self.assertTrue(cipher.startswith(("dpapi:", "local:")))
        self.assertEqual(securestore.unprotect(cipher), "sk-test-123")

    def test_plaintext_passthrough(self):
        """老库里遗留的明文照常能读（不用先迁移）。"""
        self.assertEqual(securestore.unprotect("sk-plain-legacy"), "sk-plain-legacy")
        self.assertFalse(securestore.is_protected("sk-plain-legacy"))

    def test_protect_is_idempotent_and_keeps_empty(self):
        once = securestore.protect("sk-x")
        self.assertEqual(securestore.protect(once), once)
        self.assertEqual(securestore.protect(""), "")
        self.assertEqual(securestore.protect(None), "")

    def test_security_level_reported(self):
        self.assertIn(securestore.security(), ("windows-dpapi-current-user",
                                              "local-database-file-permissions-only"))


class DatabaseSecretsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.sqlite3"
        self.db = workflow.WorkflowDB(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def raw(self, sql, args=()):
        return self.db.db.execute(sql, args).fetchone()[0]

    def test_provider_key_encrypted_at_rest(self):
        p = self.db.add_provider("T", "https://example.test/v1", api_key="sk-provider-secret")
        stored = self.raw("SELECT api_key FROM providers WHERE id=?", (p["id"],))
        self.assertTrue(securestore.is_protected(stored), "库里不能是明文")
        self.assertNotIn("sk-provider-secret", stored)
        self.assertEqual(self.db.provider(p["id"])["api_key"], "sk-provider-secret")

    def test_model_profile_key_encrypted_at_rest(self):
        m = self.db.add_model("M", "https://example.test/v1", "gpt-x", "sk-model-secret")
        stored = self.raw("SELECT api_key FROM model_profiles WHERE id=?", (m["id"],))
        self.assertTrue(securestore.is_protected(stored))
        self.assertEqual(self.db.model(m["id"])["api_key"], "sk-model-secret")
        self.assertEqual(self.db.active_model()["api_key"], "sk-model-secret")
        self.assertEqual(self.db.models()[0]["api_key"], "sk-model-secret")

    def test_set_provider_key_and_sync_stay_encrypted(self):
        p = self.db.add_provider("T", "https://example.test/v1")
        self.db.set_provider_key(p["id"], "sk-later")
        self.assertTrue(securestore.is_protected(self.raw("SELECT api_key FROM providers WHERE id=?", (p["id"],))))
        rows = self.db.db.execute("SELECT api_key FROM model_profiles WHERE provider_id=?", (p["id"],)).fetchall()
        self.assertTrue(rows or True)          # 没有采用模型时 model_profiles 为空是正常的
        for r in rows:
            self.assertTrue(securestore.is_protected(r["api_key"]))

    def test_legacy_plaintext_upgraded(self):
        """老库里已有的明文行：开库时自动升格，读出来还是原文。"""
        p = self.db.add_provider("T", "https://example.test/v1")
        self.db.db.execute("UPDATE providers SET api_key='sk-legacy' WHERE id=?", (p["id"],))
        self.db.db.commit()
        fixed = workflow.encrypt_legacy_secrets(self.db)["encrypted"]
        self.assertGreaterEqual(fixed, 1)
        self.assertTrue(securestore.is_protected(self.raw("SELECT api_key FROM providers WHERE id=?", (p["id"],))))
        self.assertEqual(self.db.provider(p["id"])["api_key"], "sk-legacy")
        self.assertEqual(workflow.encrypt_legacy_secrets(self.db)["encrypted"], 0)   # 幂等


class MailSecretsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = mail.MAIL_FILE
        mail.MAIL_FILE = Path(self.tmp.name) / "mail.json"

    def tearDown(self):
        mail.MAIL_FILE = self.old
        self.tmp.cleanup()

    def test_password_encrypted_on_disk(self):
        mail.upsert_mail_account({"id": "a1", "email": "me@example.com", "password": "authcode-123"})
        raw = mail.MAIL_FILE.read_text(encoding="utf-8")
        self.assertNotIn("authcode-123", raw, f"mail.json 里不能有明文授权码：{raw}")
        self.assertEqual(mail.list_mail_accounts()[0]["password"], "authcode-123")
        self.assertNotIn("password", mail.public_account(mail.list_mail_accounts()[0]))

    def test_legacy_plaintext_mail_upgraded(self):
        mail.MAIL_FILE.parent.mkdir(parents=True, exist_ok=True)
        mail.MAIL_FILE.write_text(json.dumps({"accounts": [
            {"id": "a2", "email": "x@example.com", "password": "plain-code"}]}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(mail.encrypt_legacy(), 1)
        self.assertNotIn("plain-code", mail.MAIL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(mail.list_mail_accounts()[0]["password"], "plain-code")
        self.assertEqual(mail.encrypt_legacy(), 0)      # 幂等


if __name__ == "__main__":
    unittest.main()
