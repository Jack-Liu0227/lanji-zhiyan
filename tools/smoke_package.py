"""Exercise the packaged executable and installer outside the source tree."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import shutil
import sqlite3
import subprocess
import tempfile
import time
from typing import Callable
import urllib.request

from installer import APP, verify_payload


def isolated_env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LANJI_", "RW_", "RESEARCH_WORKBENCH_", "PYTHON"))}
    system = Path(os.environ["SystemRoot"]) / "System32"
    env.update(LANJI_ZHIYAN_HOME=str(home), LOCALAPPDATA=str(home.parent / "local"),
               PATH=os.pathsep.join((str(system), str(system / "WindowsPowerShell/v1.0"))))
    return env


def smoke_app(directory: Path, home: Path, on_ready: Callable[[], None] | None = None) -> dict:
    verify_payload(directory)
    home.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = isolated_env(home)
    proc = subprocess.Popen([str(directory / (APP + ".exe")), "--no-open", "--port", str(port)],
                            cwd=home, env=env, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"Packaged app exited with code {proc.returncode}")
            try:
                with http.open(base + "/api/state", timeout=2) as response:
                    state = json.load(response)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Packaged app did not become ready")
        assert state["tasks"] and state["stages"], "Application state is incomplete"
        with http.open(base + "/api/providers", timeout=5) as response:
            providers = json.load(response)["providers"]
        catalog = json.loads((directory / "packages/contracts/providers_catalog.json").read_text(encoding="utf-8"))
        assert {p["id"] for p in providers} >= set(catalog), "Provider catalog was not loaded"
        for url in ("/", "/views/app-core.js", "/views/notes-and-settings.js",
                    "/views/kanban-view.js", "/views/workflow-view.js", "/vendor/pdfjs/pdf.min.js"):
            with http.open(base + url, timeout=5) as response:
                body = response.read()
                assert response.status == 200 and body, url
                relative = "index.html" if url == "/" else url.lstrip("/")
                assert body == (directory / "web" / relative).read_bytes(), \
                    f"Server is serving a different application: {url}"
        with closing(sqlite3.connect(home / "data/research.sqlite3")) as db:
            versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
            expected = sorted(p.stem for p in (directory / "migrations").glob("*.sql"))
            assert versions == expected, (versions, expected)
            for table in ("assets", "task_links", "team_tasks"):
                db.execute(f"SELECT 1 FROM {table} LIMIT 1")
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if on_ready is not None:
            on_ready()
        return {"migrations": versions, "project_id": state["tasks"][0]["id"],
                "http": "ok", "python_on_path": False}
    except Exception as exc:
        log = home / "data/cache/server-error.log"
        detail = log.read_text(encoding="utf-8") if log.exists() else "No startup error log"
        raise RuntimeError(f"{exc}\n{detail}") from exc
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=15)


def smoke_installer(setup: Path, root: Path) -> dict:
    home = root / "home"
    env = isolated_env(home)
    directory = root / "installed app 科研" / "app"
    command = [str(setup), "--silent", "--no-launch", "--no-shortcut", "--install-dir", str(directory)]
    subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL, check=True, timeout=120)
    sentinel = home / "data" / "install-preservation.txt"

    def reinstall() -> None:
        sentinel.write_text("用户数据不得覆盖", encoding="utf-8")
        (directory / "obsolete-file.txt").write_text("old version", encoding="utf-8")
        subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL, check=True, timeout=120)

    first = smoke_app(directory, home, on_ready=reinstall)
    second = smoke_app(directory, home)
    assert first["project_id"] == second["project_id"], "Reinstall replaced the database"
    assert sentinel.read_text(encoding="utf-8") == "用户数据不得覆盖"
    assert not (directory / "obsolete-file.txt").exists(), "Reinstall kept stale program files"
    return {"fresh_install": first, "reinstall": "ok", "user_data_preserved": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="App directory or setup executable")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lanji-package-check-") as temp:
        root = Path(temp)
        path = args.path.resolve()
        if path.is_file():
            result = smoke_installer(path, root)
        else:
            directory = root / "portable app 科研"
            shutil.copytree(path, directory)
            result = smoke_app(directory, root / "home")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
