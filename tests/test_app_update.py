"""Version and installer download safeguards for the in-app updater."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    "lanji_app_update", Path(__file__).resolve().parents[1] / "packages/workspace-core/app_update.py"
)
app_update = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(app_update)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1):
        if size < 0:
            payload, self.payload = self.payload, b""
            return payload
        payload, self.payload = self.payload[:size], self.payload[size:]
        return payload


class AppUpdateTest(unittest.TestCase):
    def test_version_comparison_normalizes_v_and_missing_patch(self):
        self.assertTrue(app_update.is_newer("v0.5", "0.4.9"))
        self.assertFalse(app_update.is_newer("0.4.0", "v0.4"))
        with self.assertRaises(app_update.UpdateError):
            app_update.version_key("nightly")

    def test_source_mode_reports_version_without_network_or_install_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("0.4.0\n", encoding="utf-8")
            with patch.object(app_update, "_fetch_json", side_effect=AssertionError("network")):
                result = app_update.release_info(root, frozen=False)
        self.assertEqual(result["current_version"], "0.4.0")
        self.assertFalse(result["supported"])
        self.assertFalse(result["available"])

    def test_release_selects_setup_asset_and_downloads_matching_digest(self):
        payload = b"installer payload"
        release = {
            "tag_name": "v0.5.0",
            "name": "0.5.0",
            "published_at": "2026-10-31T00:00:00Z",
            "body": "修复与改进",
            "assets": [
                {"name": "LanjiZhiyan-0.5.0-Portable.zip", "browser_download_url": "https://github.com/example/portable"},
                {"name": "LanjiZhiyan-0.5.0-Setup.exe", "browser_download_url": "https://github.com/example/setup.exe",
                 "size": len(payload), "digest": "sha256:" + hashlib.sha256(payload).hexdigest()},
            ],
        }

        def opener(request, timeout):
            self.assertGreater(timeout, 0)
            if request.full_url.endswith("releases/latest"):
                return _Response(json.dumps(release).encode("utf-8"))
            return _Response(payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("0.4.0", encoding="utf-8")
            info = app_update.release_info(root, frozen=True, opener=opener)
            target, digest = app_update.download_update(info, root / "updates", opener=opener)
            downloaded = target.read_bytes()

        self.assertTrue(info["available"])
        self.assertEqual(info["asset_name"], "LanjiZhiyan-0.5.0-Setup.exe")
        self.assertEqual(target.name, "LanjiZhiyan-0.5.0-Setup.exe")
        self.assertEqual(downloaded, payload)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

    def test_download_rejects_digest_mismatch_and_removes_temporary_file(self):
        info = {
            "supported": True,
            "asset_url": "https://github.com/example/setup.exe",
            "asset_name": "setup.exe",
            "asset_digest": "sha256:" + "0" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(app_update.UpdateError, "校验失败"):
                app_update.download_update(info, Path(tmp), opener=lambda *_args, **_kwargs: _Response(b"bad"))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_installer_receives_active_directory_and_shortcut_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer = Path(tmp) / "setup.exe"
            installer.write_bytes(b"stub")
            with patch.object(app_update.subprocess, "Popen") as popen:
                app_update.start_installer(installer, install_dir=Path(tmp) / "portable", create_shortcut=False)
            args = popen.call_args.args[0]
            self.assertEqual(args[:2], [str(installer), "--silent"])
            self.assertIn(str(Path(tmp) / "portable"), args)
            self.assertIn("--no-shortcut", args)


if __name__ == "__main__":
    unittest.main()
