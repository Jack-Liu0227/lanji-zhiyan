"""Installer copy, upgrade and rollback behavior without touching a real installation."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location("lanji_installer", Path(__file__).resolve().parents[1] / "tools/installer.py")
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "payload"
        for name in (installer.APP + ".exe", "VERSION", "web/index.html", "build/icon.ico",
                     "packages/contracts/providers_catalog.json", "archive/migrations/001-046.zip",
                     "archive/migrations/manifest.json"):
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
        self.assertEqual(installer.verify_payload(self.app), 7)
        self.assertEqual(self.data.read_bytes(), b"user data")

    def test_corrupt_payload_leaves_current_installation_intact(self):
        (self.src / (installer.APP + ".exe")).write_text("corrupted", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "校验失败"):
            installer.install_payload(self.src, self.app)
        self.assertEqual((self.app / "old.exe").read_text(encoding="utf-8"), "old version")

    def test_missing_history_archive_leaves_current_installation_intact(self):
        archive = self.src / "archive/migrations/001-046.zip"
        archive.unlink()
        with self.assertRaisesRegex(RuntimeError, "001-046"):
            installer.install_payload(self.src, self.app)
        self.assertEqual((self.app / "old.exe").read_text(encoding="utf-8"), "old version")
        self.assertEqual(self.data.read_bytes(), b"user data")

    def test_corrupt_history_manifest_leaves_current_installation_intact(self):
        (self.src / "archive/migrations/manifest.json").write_text("corrupted", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "manifest.json"):
            installer.install_payload(self.src, self.app)
        self.assertEqual((self.app / "old.exe").read_text(encoding="utf-8"), "old version")
        self.assertEqual(self.data.read_bytes(), b"user data")

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


@unittest.skipUnless(os.name == "nt", "Windows process paths and directory junctions")
class InstallerProcessTest(unittest.TestCase):
    def test_stops_app_launched_through_junction_and_preserves_other_installation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            target = root / "installed"
            other = root / "other"
            target.mkdir()
            other.mkdir()
            alias = root / "alias"
            installer._powershell(
                "New-Item -ItemType Junction -Path $env:LANJI_TEST_ALIAS "
                "-Target $env:LANJI_TEST_TARGET | Out-Null",
                LANJI_TEST_ALIAS=str(alias), LANJI_TEST_TARGET=str(target))
            name = installer.APP + ".exe"
            ping = Path(os.environ["SystemRoot"]) / "System32/ping.exe"
            for directory in (target, other):
                shutil.copy2(ping, directory / name)
            processes = []
            try:
                for directory in (alias, other):
                    processes.append(subprocess.Popen(
                        [str(directory / name), "-t", "127.0.0.1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=installer.NO_WINDOW))
                installer._stop_installed_app(target / name)
                self.assertIsNotNone(processes[0].poll())
                self.assertIsNone(processes[1].poll())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)
