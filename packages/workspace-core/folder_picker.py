"""Native folder selection without Windows temporary-file sharing locks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


_HELPER = """import json, sys
sys.path.insert(0, sys.argv[1])
from folder_picker import _select_folder
print(json.dumps({'dir': _select_folder(sys.argv[2], sys.argv[3])}, ensure_ascii=True), flush=True)
"""


def _select_folder(initial: str, title: str) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(parent=root, title=title, initialdir=initial or str(Path.home())) or ""
    finally:
        root.destroy()


def helper_main(arguments: list[str]) -> int:
    """Frozen app entry point: --pick-folder-helper result-file initial title."""
    if len(arguments) != 3:
        return 2
    output, initial, title = arguments
    try:
        result = {"dir": _select_folder(initial, title)}
    except Exception as exc:
        result = {"error": f"打开文件夹选择框失败：{type(exc).__name__}: {exc}"}
    # A windowed PyInstaller executable has no stdout. The parent creates only
    # the private directory, not an open result file, so Windows locks cannot recur.
    Path(output).write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    return 0


def _result(raw: bytes) -> dict[str, Any]:
    result = json.loads(raw.decode("utf-8-sig").strip())
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        return {"error": result["error"]}
    if not isinstance(result, dict) or not isinstance(result.get("dir"), str):
        raise ValueError("folder dialog returned an invalid path")
    return {"dir": result["dir"]}


def pick_folder(initial: str = "", title: str = "选择产出文件夹") -> dict[str, Any]:
    """Run Tk on its own main thread and return a path, cancellation, or error.

    Source runs use stdout; windowed frozen executables use a private result
    file without an open mkstemp descriptor. CREATE_NO_WINDOW hides only the
    console, not the native Tk dialog.
    """
    try:
        options = {"capture_output": True, "timeout": 600,
                   "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0}
        args = [str(initial or ""), str(title or "选择产出文件夹")]
        if getattr(sys, "frozen", False):
            with tempfile.TemporaryDirectory(prefix="rw-folder-picker-") as directory:
                output = Path(directory) / "result.json"
                proc = subprocess.run([sys.executable, "--pick-folder-helper", str(output), *args], **options)
                if proc.returncode == 0:
                    return _result(output.read_bytes())
        else:
            proc = subprocess.run([sys.executable, "-c", _HELPER, str(Path(__file__).resolve().parent), *args], **options)
        if proc.returncode:
            detail = proc.stderr.decode("utf-8", "replace").strip()[-500:]
            return {"error": "打开文件夹选择框失败：" + (detail or f"exit {proc.returncode}")}
        return _result(proc.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "文件夹选择已超时，请重新打开选择框"}
    except (OSError, ValueError, UnicodeError) as exc:
        return {"error": f"打开文件夹选择框失败：{type(exc).__name__}: {exc}"}
