# -*- coding: utf-8 -*-
"""Per-user Windows installer. Verify a staged application before replacing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

APP = "览籍智研"
LEGACY_APPS = ("ResearchWorkbench",)
MANIFEST = "package-manifest.json"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(directory: Path) -> None:
    files = {p.relative_to(directory).as_posix(): _sha256(p)
             for p in sorted(directory.rglob("*")) if p.is_file() and p.name != MANIFEST}
    (directory / MANIFEST).write_text(
        json.dumps({"files": files}, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_payload(directory: Path) -> int:
    required = (APP + ".exe", "VERSION", "web/index.html", "build/icon.ico",
                "packages/contracts/providers_catalog.json", "migrations/005_asset_graph_upgrade.sql")
    for name in required:
        if not (directory / name).is_file():
            raise RuntimeError("安装包缺少文件：" + name)
    files = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))["files"]
    if not files or not all(name in files for name in required):
        raise RuntimeError("安装包文件清单不完整")
    for name, expected in files.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError("安装包包含无效路径：" + name)
        if not path.is_file() or _sha256(path) != expected:
            raise RuntimeError("安装包文件校验失败：" + name)
    return len(files)


def _copy_missing(src: Path, dst: Path) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_missing(child, dst / child.name)
    elif src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _migrate_legacy_data(root: Path, local: Path) -> None:
    for name in LEGACY_APPS:
        for old in (local / name, local / "Programs" / name):
            for sub in ("data", "user", "executors"):
                _copy_missing(old / sub, root / sub)


def _powershell(script: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, **env), capture_output=True, creationflags=NO_WINDOW,
        encoding="utf-8", errors="replace", timeout=45, check=True)


def _stop_installed_app(exe: Path) -> None:
    _powershell(
        "$ErrorActionPreference='Stop';"
        "$apps=@(Get-CimInstance Win32_Process | Where-Object {"
        "$_.ExecutablePath -eq $env:LANJI_INSTALL_EXE});"
        "foreach($item in $apps){"
        "$p=Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue;"
        "if($p){[void]$p.CloseMainWindow()}};"
        "foreach($item in $apps){"
        "$p=Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue;"
        "if($p -and !$p.WaitForExit(5000)){Stop-Process -Id $p.Id -Force}}",
        LANJI_INSTALL_EXE=str(exe))


def install_payload(src: Path, app_dir: Path) -> int:
    """Copy errors leave the current application intact; never copy user data."""
    count = verify_payload(src)
    app_dir = app_dir.absolute()
    app_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".lanji-install-", dir=app_dir.parent))
    backup = stage / "previous"
    incoming = stage / "incoming"
    try:
        shutil.copytree(src, incoming)
        verify_payload(incoming)
        _stop_installed_app(app_dir / (APP + ".exe"))
        if app_dir.exists():
            app_dir.rename(backup)
        try:
            incoming.rename(app_dir)
        except OSError:
            if backup.exists():
                backup.rename(app_dir)
            raise
    finally:
        if stage.parent == app_dir.parent and stage.name.startswith(".lanji-install-"):
            # Keep the previous app recoverable if rollback itself failed.
            if not backup.exists() or app_dir.exists():
                shutil.rmtree(stage, ignore_errors=True)
    return count


def create_shortcut(exe: Path) -> Path:
    result = _powershell(
        "$ErrorActionPreference='Stop';"
        "$desktop=[Environment]::GetFolderPath('Desktop');"
        "$link=Join-Path $desktop ($env:LANJI_INSTALL_TITLE+'.lnk');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($link);"
        "$s.TargetPath=$env:LANJI_INSTALL_EXE;"
        "$s.WorkingDirectory=Split-Path $env:LANJI_INSTALL_EXE;"
        "$s.IconLocation=Join-Path $s.WorkingDirectory 'build\\icon.ico';"
        "$s.Description=$env:LANJI_INSTALL_TITLE;$s.Save();"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();Write-Output $link",
        LANJI_INSTALL_EXE=str(exe), LANJI_INSTALL_TITLE=APP)
    link = Path(result.stdout.strip())
    if not link.is_file():
        raise RuntimeError("桌面快捷方式创建失败")
    return link


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP + " 安装程序")
    parser.add_argument("--install-dir", type=Path)
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--no-shortcut", action="store_true")
    parser.add_argument("--silent", action="store_true", help="完成后不等待按键")
    args = parser.parse_args(argv)
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    local = Path(os.environ["LOCALAPPDATA"])
    root = local / APP
    app_dir = args.install_dir or local / "LanjiZhiyan" / "app"
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    try:
        print("正在安装到：%s" % app_dir, flush=True)
        count = install_payload(here / APP, app_dir)
        for sub in ("data", "user", "executors/agents", "executors/skills", "executors/tools"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        if not (root / "layout.json").exists():
            (root / "layout.json").write_text('{"layout": 2}\n', encoding="utf-8")
        _migrate_legacy_data(root, local)
        exe = app_dir / (APP + ".exe")
        if not args.no_shortcut:
            print("桌面快捷方式：%s" % create_shortcut(exe), flush=True)
        if not args.no_launch:
            subprocess.Popen([str(exe)], cwd=app_dir, close_fds=True)
        print("安装完成，已校验 %d 个文件。" % count, flush=True)
        result = 0
    except Exception as exc:
        print("安装失败：%s" % exc, file=sys.stderr, flush=True)
        result = 1
    if not args.silent and sys.stdin is not None and sys.stdin.isatty():
        input("按回车关闭窗口 ...")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
