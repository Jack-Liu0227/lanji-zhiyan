"""Device-flow registration is mocked at the urllib boundary; no Feishu calls."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "connectors"))

import feishu
import service_settings


class FakeResponse:
    def __init__(self, data: dict):
        self.data = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


class FakeDB:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE assistants (id TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO assistants VALUES (?)", [("assistant-a",), ("assistant-b",)])
        self.db.commit()

    def assistant(self, aid):
        row = self.db.execute("SELECT id FROM assistants WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else None

    def close(self):
        self.db.close()


class FeishuDeviceFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()

    def tearDown(self):
        self.db.close()

    def route(self, endpoint, body):
        return feishu.route("POST", "/api/feishu/registration/" + endpoint, body, {}, self.db)

    def test_begin_uses_registration_form_and_hides_device_code(self):
        device_code = "internal-device-code-a"

        def urlopen(req, timeout):
            self.assertEqual(timeout, 25)
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.full_url, feishu.REGISTRATION_FEISHU + feishu.REGISTRATION_PATH)
            self.assertIn("application/x-www-form-urlencoded", req.get_header("Content-type"))
            form = parse_qs(req.data.decode("utf-8"))
            self.assertEqual(form, {
                "action": ["begin"], "archetype": ["PersonalAgent"],
                "auth_method": ["client_secret"], "request_user_info": ["open_id"],
            })
            return FakeResponse({
                "device_code": device_code,
                "verification_uri_complete": "https://accounts.feishu.cn/device?user_code=ABCD",
                "interval": 4,
                "expires_in": 600,
            })

        with patch.object(feishu.urllib.request, "urlopen", side_effect=urlopen):
            result = self.route("begin", {"assistant_id": "assistant-a", "region": "feishu"})

        self.assertEqual(result["assistant_id"], "assistant-a")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["interval"], 4)
        self.assertTrue(result["session_id"])
        self.assertEqual(result["verification_uri_complete"].split("?")[0], "https://accounts.feishu.cn/device")
        self.assertTrue(result["qr_data_uri"].startswith("data:image/svg+xml;base64,"))
        self.assertNotIn(device_code, json.dumps(result))
        self.assertNotIn("device_code", result)
        saved = service_settings.read(self.db, feishu.REGISTRATION_SERVICE, "assistant-a")
        self.assertEqual(saved["device_code"], device_code)
        self.assertEqual(saved["session_id"], result["session_id"])

    def test_begin_accepts_official_open_domain_launcher_urls(self):
        cases = (
            ("feishu", "https://open.feishu.cn/page/launcher?user_code=ABCD"),
            ("lark", "https://open.larksuite.com/page/launcher?user_code=EFGH"),
        )
        for region, qr_url in cases:
            with self.subTest(region=region):
                with patch.object(feishu.urllib.request, "urlopen", return_value=FakeResponse({
                    "device_code": "server-only-" + region,
                    "verification_uri_complete": qr_url,
                })):
                    result = self.route("begin", {"assistant_id": "assistant-a", "region": region})
                self.assertEqual(result["qr_url"].split("?")[0], qr_url.split("?")[0])
                self.assertTrue(result["qr_data_uri"].startswith("data:image/svg+xml;base64,"))

    def test_begin_rejects_wrong_region_or_untrusted_qr_hosts(self):
        for region, qr_url in (
            ("feishu", "https://open.larksuite.com/page/launcher?user_code=ABCD"),
            ("lark", "https://open.feishu.cn/page/launcher?user_code=ABCD"),
            ("feishu", "https://example.com/page/launcher?user_code=ABCD"),
        ):
            with self.subTest(region=region, qr_url=qr_url):
                with patch.object(feishu.urllib.request, "urlopen", return_value=FakeResponse({
                    "device_code": "server-only", "verification_uri_complete": qr_url,
                })):
                    with self.assertRaisesRegex(ValueError, "不受信任的扫码地址"):
                        self.route("begin", {"assistant_id": "assistant-a", "region": region})

    def test_poll_rejects_missing_or_wrong_session_without_upstream_call(self):
        with patch.object(feishu.urllib.request, "urlopen", return_value=FakeResponse({
            "device_code": "only-on-server", "verification_uri_complete": "https://accounts.feishu.cn/device",
        })):
            started = self.route("begin", {"assistant_id": "assistant-a"})

        with patch.object(feishu.urllib.request, "urlopen") as urlopen:
            for body in (
                {"assistant_id": "assistant-a"},
                {"assistant_id": "assistant-a", "session_id": "wrong-session"},
                {"assistant_id": "assistant-b", "session_id": started["session_id"]},
            ):
                with self.subTest(body=body), self.assertRaises(ValueError):
                    self.route("poll", body)
            urlopen.assert_not_called()

    def test_poll_sends_server_side_device_code_and_pending_never_echoes_it(self):
        device_code = "device-code-stays-server-side"
        with patch.object(feishu.urllib.request, "urlopen", return_value=FakeResponse({
            "device_code": device_code,
            "verification_uri_complete": "https://accounts.feishu.cn/device?user_code=ABCD",
        })):
            started = self.route("begin", {"assistant_id": "assistant-a"})

        def poll(req, timeout):
            self.assertEqual(req.method, "POST")
            self.assertEqual(urlparse(req.full_url).path, feishu.REGISTRATION_PATH)
            form = parse_qs(req.data.decode("utf-8"))
            self.assertEqual(form, {"action": ["poll"], "device_code": [device_code]})
            return FakeResponse({"error": "authorization_pending"})

        with patch.object(feishu.urllib.request, "urlopen", side_effect=poll):
            result = self.route("poll", {"assistant_id": "assistant-a", "session_id": started["session_id"]})

        self.assertEqual(result["status"], "pending")
        self.assertNotIn(device_code, json.dumps(result))
        self.assertNotIn("device_code", result)

    def test_success_secrets_stay_private_and_config_is_assistant_scoped(self):
        client_secret = "private-app-secret"
        calls = []

        def urlopen(req, timeout):
            self.assertEqual(timeout, 25)
            form = parse_qs(req.data.decode("utf-8"))
            calls.append(form)
            if form["action"] == ["begin"]:
                code = "device-for-" + str(len(calls))
                return FakeResponse({
                    "device_code": code,
                    "verification_uri_complete": "https://accounts.feishu.cn/device?user_code=ABCD",
                })
            if form["device_code"] == ["device-for-2"]:
                return FakeResponse({"error": "authorization_pending"})
            self.assertNotEqual(form["device_code"], ["attacker-controlled-code"])
            return FakeResponse({
                "client_id": "created-app-id",
                "client_secret": client_secret,
                "user_info": {"open_id": "open-id-a", "tenant_brand": "feishu"},
            })

        with patch.object(feishu.urllib.request, "urlopen", side_effect=urlopen):
            started_a = self.route("begin", {"assistant_id": "assistant-a"})
            started_b = self.route("begin", {"assistant_id": "assistant-b"})
            completed = self.route("poll", {
                "assistant_id": "assistant-a", "session_id": started_a["session_id"],
                # Client-supplied device_code is deliberately ignored.
                "device_code": "attacker-controlled-code",
            })
            pending_b = self.route("poll", {"assistant_id": "assistant-b", "session_id": started_b["session_id"]})
            public_a = feishu.route("GET", "/api/feishu/config", {}, {"assistant_id": ["assistant-a"]}, self.db)
            public_b = feishu.route("GET", "/api/feishu/config", {}, {"assistant_id": ["assistant-b"]}, self.db)

        self.assertEqual(completed["status"], "success")
        self.assertEqual(completed["open_id"], "open-id-a")
        self.assertEqual(completed["config"]["app_id"], "created-app-id")
        self.assertTrue(completed["config"]["app_secret_present"])
        self.assertNotIn("app_secret", completed["config"])
        self.assertNotIn(client_secret, json.dumps(completed))
        self.assertNotIn("device-for-1", json.dumps(completed))
        self.assertEqual(pending_b["status"], "pending")
        self.assertEqual(public_a["config"]["receive_id"], "open-id-a")
        self.assertNotIn(client_secret, json.dumps(public_a))
        self.assertNotIn("app_secret", public_a["config"])
        self.assertNotIn("app_id", public_b["config"])
        self.assertFalse(public_b["config"]["app_secret_present"])
        self.assertEqual(service_settings.read(self.db, feishu.REGISTRATION_SERVICE, "assistant-a"), {})
        self.assertEqual(service_settings.read(self.db, feishu.REGISTRATION_SERVICE, "assistant-b")["session_id"], started_b["session_id"])

    def test_lark_registration_sends_via_lark_open_api_host(self):
        service_settings.write(self.db, "feishu", {
            "enabled": True, "app_id": "lark-app", "app_secret": "private", "receive_id": "open-id",
            "receive_id_type": "open_id", "registration_region": "lark",
        }, "assistant-a")
        with patch.object(feishu, "request", side_effect=[
                {"tenant_access_token": "tenant-token"}, {"data": {"message_id": "message-id"}}]) as request:
            result = feishu.send(self.db, "assistant-a", "hello")

        self.assertEqual(result["message_id"], "message-id")
        self.assertEqual(request.call_args_list[0].kwargs["api"], "https://open.larksuite.com/open-apis")
        self.assertEqual(request.call_args_list[1].kwargs["api"], "https://open.larksuite.com/open-apis")

    def test_cancel_requires_current_session_and_clears_only_its_assistant(self):
        with patch.object(feishu.urllib.request, "urlopen", return_value=FakeResponse({
            "device_code": "device-code",
            "verification_uri_complete": "https://accounts.feishu.cn/device?user_code=ABCD",
        })):
            started = self.route("begin", {"assistant_id": "assistant-a"})

        with patch.object(feishu.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(ValueError):
                self.route("cancel", {"assistant_id": "assistant-a", "session_id": "not-current"})
            urlopen.assert_not_called()

        cancelled = self.route("cancel", {"assistant_id": "assistant-a", "session_id": started["session_id"]})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(service_settings.read(self.db, feishu.REGISTRATION_SERVICE, "assistant-a"), {})


if __name__ == "__main__":
    unittest.main()
