"""本机 Zotero 写入通道：工具注册、权限闸门、路径越界、授权 key 加密存库。"""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import agent_integrations
import securestore
import workflow
import zotero


class _Reply:
    """假 HTTP 响应：urlopen 用 with 打开。"""

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self, *args):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ZoteroLocalWriteTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="zotero-local-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.task = self.db.create_task("Cu 掺杂论文")
        self.pdf = Path(self.db.base_dir(self.task)) / "文献" / "s41563.pdf"
        self.pdf.parent.mkdir(parents=True, exist_ok=True)
        self.pdf.write_bytes(b"%PDF-1.4")

    def test_tools_are_registered(self):
        names = {t["function"]["name"] for t in agent_integrations.tool_specs()}
        for name in ("zotero_local_state", "zotero_local_authorize", "zotero_local_collection_create",
                     "zotero_local_item_add", "zotero_local_link_pdf"):
            self.assertIn(name, names)

    def test_write_tools_need_full_access(self):
        for name, args in (("zotero_local_collection_create", {"name": "x"}),
                           ("zotero_local_item_add", {"item": {"itemType": "journalArticle"}}),
                           ("zotero_local_link_pdf", {"key": "ABCD1234", "pdf": str(self.pdf)}),
                           ("zotero_local_authorize", {})):
            with self.subTest(name=name), self.assertRaises(ValueError):
                agent_integrations.invoke(name, self.db, self.task, args, permission="read_only")
            with self.subTest(name=name), self.assertRaises(ValueError):
                agent_integrations.invoke(name, self.db, self.task, args, permission="auto")

    def test_unauthorized_state_and_no_zotero(self):
        self.assertFalse(zotero.local_write_state(self.db)["authorized"])
        with patch.object(zotero, "_authorize", side_effect=zotero.ZoteroError("连不上 Zotero 本机接口")):
            with self.assertRaises(zotero.ZoteroError):
                zotero.create_collection(self.db, "MLCU")

    def test_write_auto_renews_single_use_key_on_401(self):
        """Zotero 常只发一次性 key：第一次写 401 → 自动换 key 重试一次。"""
        keys, calls = iter(["KEY-A", "KEY-B"]), []

        def fake_authorize(db, app_name="览籍智研"):
            return {"key": next(keys), "server_id": "SID", "remember": False}

        seen = []

        class Answer:
            def __init__(self, code, payload):
                self.code, self._raw = code, json.dumps(payload).encode()

            def read(self, *a):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            seen.append(request.headers.get("Zotero-api-key") or request.headers.get("Zotero_API_Key")
                        or request.get_header("Zotero-api-key"))
            if len(seen) == 1:
                raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
            return Answer(200, {"successful": {"0": {"key": "NEW00001"}}})

        with patch.object(zotero, "_authorize", side_effect=fake_authorize), \
             patch.object(zotero.urllib.request, "urlopen", side_effect=fake_urlopen):
            out = zotero.create_collection(self.db, "MLCU")
        self.assertEqual(out["key"], "NEW00001")
        self.assertEqual(seen, ["KEY-A", "KEY-B"])

    def test_collection_item_and_linked_file_payloads(self):
        calls = []

        def fake_write(db, path, payload=None, method="POST"):
            calls.append((path, payload))
            return {"successful": {"0": {"key": "NEW00001"}}}

        with patch.object(zotero, "_write", side_effect=fake_write):
            out = agent_integrations.invoke("zotero_local_collection_create", self.db, self.task,
                                            {"name": "MLCU"}, permission="full_access")
            self.assertEqual(out["key"], "NEW00001")
            self.assertEqual(calls[-1][1], [{"name": "MLCU", "parentCollection": False}])

            item = {"itemType": "journalArticle", "title": "T", "DOI": "10.1/x"}
            agent_integrations.invoke("zotero_local_item_add", self.db, self.task, {"item": item},
                                      permission="full_access")
            self.assertEqual(calls[-1][1][0]["itemType"], "journalArticle")

            agent_integrations.invoke("zotero_local_link_pdf", self.db, self.task,
                                      {"key": "NEW00001", "pdf": str(self.pdf)}, permission="full_access")
            attachment = calls[-1][1][0]
            self.assertEqual(attachment["linkMode"], "linked_file")
            self.assertEqual(attachment["parentItem"], "NEW00001")
            self.assertEqual(Path(attachment["path"]), self.pdf.resolve())

    def test_link_pdf_outside_project_is_refused(self):
        outside = self.root / "外部.pdf"
        outside.write_bytes(b"%PDF-1.4")
        with patch.object(zotero, "_write", return_value={"successful": {"0": {"key": "K"}}}):
            with self.assertRaises(PermissionError):
                agent_integrations.invoke("zotero_local_link_pdf", self.db, self.task,
                                          {"key": "K", "pdf": str(outside)}, permission="full_access")

    def test_delete_sends_version_header(self):
        """DELETE 不带 If-Unmodified-Since-Version 会吃 428（实测）。"""
        sent = {}

        class Answer:
            def __init__(self, payload):
                self._raw = json.dumps(payload).encode()
                self.status = 200
                self.headers = {"Last-Modified-Version": str((payload or {}).get("version") or "")}

            def read(self, *a):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_authorize(db, app_name="览籍智研"):
            return {"key": "K" * 32, "server_id": "SID", "remember": False}

        def fake_urlopen(request, timeout=None):
            if request.get_method() == "GET":
                sent["get"] = request.full_url
                return Answer({"key": "COLL0001", "version": 77, "data": {"version": 77}})
            sent["hdr"] = request.headers
            return Answer(None)

        with patch.object(zotero, "_authorize", side_effect=fake_authorize), \
             patch.object(zotero.urllib.request, "urlopen", side_effect=fake_urlopen):
            zotero._write(self.db, "/users/0/collections/COLL0001", method="DELETE")
        self.assertIn("/api/users/0/collections/COLL0001", sent["get"])
        self.assertEqual(sent["hdr"].get("If-unmodified-since-version"), "77")

    def test_authorize_stores_protected_key_only_when_remembered(self):
        with patch.object(zotero, "_server_id", return_value="SID123456"), \
             patch.object(zotero.urllib.request, "urlopen",
                          return_value=_Reply({"key": "K" * 32, "remember": True})):
            out = zotero.authorize_local_write(self.db)
        self.assertTrue(out["stored"])
        stored = zotero.store.read(self.db, zotero.LOCAL_SERVICE)
        self.assertTrue(securestore.is_protected(stored["key"]))
        self.assertEqual(securestore.unprotect(stored["key"]), "K" * 32)
        self.assertTrue(zotero.local_write_state(self.db)["authorized"])

        with patch.object(zotero, "_server_id", return_value="SID123456"), \
             patch.object(zotero.urllib.request, "urlopen",
                          return_value=_Reply({"key": "O" * 32, "remember": False})):
            with patch.object(zotero.store, "write") as wrote:
                out = zotero.authorize_local_write(self.db)
        self.assertFalse(out["stored"])
        wrote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
