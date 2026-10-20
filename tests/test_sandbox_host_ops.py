"""沙箱边界回归：Low IL 服务端做不了的事，必须转给宿主（Medium）做。

这三条都是实测出来的（2026-10-20）：
  · Low 里 ShellExecute 打开文档/目录直接返回 SE_ERR_ACCESSDENIED(5)，只有宿主打得开；
  · Low 起的子进程继承 Low 令牌 —— Zotero 这类程序写不了自己的数据目录；
  · Low 对 Medium 目录跑 icacls 返回 5 —— 启动时的「把项目目录标 Low」得宿主来做。

只要有人把这几处改回本地直做，这些用例就会红。
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import fileguard  # noqa: E402
import paths  # noqa: E402


class _Host(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *_a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Host.seen.append((self.path, json.loads(body or b"{}"),
                           self.headers.get("X-RW-Key")))
        out = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class HostBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        _Host.seen = []
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Host)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.patch = mock.patch.multiple(paths, GRANT="127.0.0.1:%d" % self.port,
                                        GRANT_KEY="test-key")
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.srv.shutdown()
        self.srv.server_close()

    def test_ops_go_through_host(self) -> None:
        paths.grant_dir(Path(paths.DATA))
        paths.host_open("C:/tmp/a.docx")
        paths.host_app("zotero")
        self.assertEqual([p for p, _b, _k in _Host.seen], ["/grant", "/open", "/app"])
        self.assertTrue(all(k == "test-key" for _p, _b, k in _Host.seen))

    def test_open_document_asks_host_and_reports_refusal(self) -> None:
        doc = Path(paths.DATA) / "_bridge_test.docx"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_bytes(b"x")
        try:
            with mock.patch.object(paths, "host_open", return_value=True) as host:
                self.assertEqual(fileguard.open_document(doc)["opened"], str(doc))
                host.assert_called_once()
            with mock.patch.object(paths, "host_open", return_value=False):
                with self.assertRaises(ValueError):        # 宿主拒绝 → 说人话，别静默成功
                    fileguard.open_document(doc)
        finally:
            doc.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
