"""Installer copy, upgrade and rollback behavior without touching a real installation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location("lanji_installer", Path(__file__).resolve().parents[1] / "tools/installer.py")
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "payload"
        for name in (installer.APP + ".exe", "VERSION", "web/index.html", "build/icon.ico",
                     "packages/contracts/providers_catalog.json", "migrations/005_asset_graph_upgrade.sql"):
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("new version", encoding="utf-8")
        installer.write_manifest(self.src)
        self.app = self.root / "installed/app"
        self.app.mkdir(parents=True)
        (self.app / "old.exe").write_text("old version", encoding="utf-8")
        self.data = self.app.parent / "data/research.sqlite3"
        self.data.parent.mkdir()
        self.data.write_bytes(b"user data")
        self.stop = patch.object(installer, "_stop_installed_app")
        self.stop.start()
        self.addCleanup(self.stop.stop)

    def test_upgrade_removes_stale_program_files_and_preserves_data(self):
        installer.install_payload(self.src, self.app)
        self.assertFalse((self.app / "old.exe").exists())
        self.assertEqual(installer.verify_payload(self.app), 6)
        self.assertEqual(self.data.read_bytes(), b"user data")

    def test_corrupt_payload_leaves_current_installation_intact(self):
        (self.src / (installer.APP + ".exe")).write_text("corrupted", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            installer.install_payload(self.src, self.app)
        self.assertEqual((self.app / "old.exe").read_text(encoding="utf-8"), "old version")

    def test_copy_failure_does_not_replace_current_installation(self):
        with patch.object(installer.shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                installer.install_payload(self.src, self.app)
        self.assertTrue((self.app / "old.exe").is_file())

    def test_failed_replacement_restores_previous_version(self):
        original = Path.rename

        def rename(path, destination):
            if path.name == "incoming":
                raise PermissionError("replacement failed")
            return original(path, destination)

        with patch.object(Path, "rename", rename):
            with self.assertRaisesRegex(PermissionError, "replacement failed"):
                installer.install_payload(self.src, self.app)
        self.assertTrue((self.app / "old.exe").is_file())
        self.assertEqual(self.data.read_bytes(), b"user data")
