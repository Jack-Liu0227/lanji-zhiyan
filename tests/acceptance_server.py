"""Isolated browser acceptance fixture with a controllable local SSE model.

Run manually; prints metadata path. POST /release or /shutdown to the model URL.
Never loads the user's application database or calls an external model.
"""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output/acceptance/2026-10-21"


def main():
    with tempfile.TemporaryDirectory(prefix="rw-browser-acceptance-") as temporary:
        os.environ["LANJI_ZHIYAN_HOME"] = temporary
        os.environ["LANJI_ZHIYAN_DB"] = str(Path(temporary) / "data/research.sqlite3")
        os.environ["NO_PROXY"] = "localhost,127.0.0.1"
        for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
            sys.path.insert(0, str(ROOT / "packages" / package))
        import workflow

        release, shutdown = threading.Event(), threading.Event()
        requests = []

        class Model(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = json.dumps(requests).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.path in ("/release", "/shutdown"):
                    (release if self.path == "/release" else shutdown).set()
                    self.send_response(200)
                    self.end_headers()
                    return
                payload = json.loads(body)
                prompt = payload["messages"][-1]["content"]
                user_prompt = next((m.get("content", "") for m in reversed(payload["messages"]) if m.get("role") == "user"), "")
                record = {"stream": bool(payload.get("stream")), "prompt": prompt,
                          "started": time.time(), "finished": False}
                requests.append(record)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if record["stream"] else "application/json")
                self.end_headers()
                try:
                    if not record["stream"]:
                        self.wfile.write(json.dumps({"choices": [{"message": {"content": "NONSTREAM"}}]}).encode())
                        return
                    def delta(text):
                        data = {"choices": [{"delta": {"content": text}}]}
                        self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
                        self.wfile.flush()
                    if "presentation-check" in user_prompt:
                        if payload["messages"][-1]["role"] != "tool":
                            thinking = {"choices": [{"delta": {"reasoning_content": "先核对项目数据，再展示公式与结论。"}}]}
                            self.wfile.write(("data: " + json.dumps(thinking) + "\n\n").encode())
                            self.wfile.flush()
                            release.wait(20)
                            release.clear()
                            calls = {"choices": [{"delta": {"tool_calls": [
                                {"index": 0, "id": "presentation-a", "type": "function", "function": {"name": "project_note_list", "arguments": "{}"}},
                                {"index": 1, "id": "presentation-b", "type": "function", "function": {"name": "library_overview", "arguments": "{}"}}]}}]}
                            self.wfile.write(("data: " + json.dumps(calls) + "\n\n").encode())
                        else:
                            delta("## 验证结果\n\n行内公式 $E=mc^2$，以及 \\(x_i^2\\)。\n\n")
                            delta("$$\\frac{a}{b}=\\sqrt{x}$$\n\n\\[\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}\\]\n\n")
                            delta("| 项目 | 结果 |\n| --- | --- |\n| 公式 | 正常 |\n\n```python\nformula = '$not_math$'\n```\n")
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        record["finished"] = True
                        return
                    delta("FIRST-PART ")
                    if "cancel" in prompt:
                        for _ in range(120):
                            if shutdown.wait(0.2):
                                return
                            delta("tick ")
                    else:
                        release.wait(20)
                        release.clear()
                        delta("FINAL-PART")
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    record["finished"] = True
                except (ConnectionError, OSError):
                    record["disconnected"] = True

        model = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        model_url = f"http://127.0.0.1:{model.server_port}"
        with workflow.WorkflowDB(os.environ["LANJI_ZHIYAN_DB"]) as db:
            a = db.create_task("Acceptance A")
            b = db.create_task("Acceptance B")
            db.add_model("Local acceptance fixture", model_url + "/v1", "fixture", "test-only-key", activate=True)
            chat = db.create_chat("Acceptance chat")
            note = db.base_dir(a) / "笔记/文献矩阵/existing.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("# Existing matrix\n\nEvidence in existing Markdown.", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        REPORT.mkdir(parents=True, exist_ok=True)
        with (REPORT / "fixture-server.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "apps/desktop/server.py"), "--port", str(port), "--no-open"],
                                       cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING="utf-8"), stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(url, timeout=0.5):
                            break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError("Fixture app failed; see fixture-server.log")
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Fixture app startup timed out")
                metadata = {"url": url, "model_url": model_url, "project_a": a, "project_b": b, "chat": chat}
                (REPORT / "fixture.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                print(json.dumps(metadata), flush=True)
                shutdown.wait()
            finally:
                process.terminate()
                process.wait(timeout=10)
                model.shutdown()
                model.server_close()
                (REPORT / "model-requests.json").write_text(json.dumps(requests, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
