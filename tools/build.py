"""Build and verify both Windows distribution formats using one interpreter."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

from installer import APP

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, env: dict | None = None) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def main() -> None:
    if sys.platform != "win32" or sys.version_info < (3, 10):
        raise RuntimeError("Windows and Python 3.10 or newer are required")
    missing = [name for name in ("PyInstaller", "webview", "pythonnet", "docx", "yaml",
                                "pypdf", "pdfplumber", "numpy", "qrcode", "lark_oapi")
               if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError("Missing dependencies: %s\nRun: %s -m pip install -r requirements-build.txt"
                           % (", ".join(missing), sys.executable))
    print("Build Python: " + sys.executable, flush=True)
    run("-m", "PyInstaller", "tools/览籍智研.spec", "--noconfirm", "--clean")
    payload = ROOT / "dist" / APP
    run("tools/smoke_package.py", str(payload))
    env = dict(os.environ, LANJI_PACKAGE_DIR=str(payload))
    run("-m", "PyInstaller", "tools/installer.spec", "--noconfirm", "--clean", env=env)
    setup = ROOT / "dist" / (APP + ".exe")
    run("tools/smoke_package.py", str(setup))
    release = ROOT / "dist/release"
    release.mkdir(exist_ok=True)
    setup_release = release / (APP + ".exe")
    shutil.copy2(setup, setup_release)
    portable = Path(shutil.make_archive(str(release / (APP + "-免安装版")),
                                       "zip", root_dir=payload.parent, base_dir=payload.name))
    checksums = []
    for artifact in (setup_release, portable):
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        checksums.append(digest.hexdigest() + "  " + artifact.name)
    (release / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print("Verified setup: " + str(setup_release))
    print("Portable ZIP: " + str(portable))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
