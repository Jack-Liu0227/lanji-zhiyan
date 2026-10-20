# POST 必须带 Content-Type: application/json —— 服务端 do_POST 见不到这个头就直接 415，
# 请求连路由都进不去（窗口按钮/上传就是栽在这上面）。
# python -m unittest test_web_fetch -v
from __future__ import annotations
import pathlib
import re
import sys
import unittest
import subprocess
import tempfile


# ---------------- 来自 test_web_fetch.py ----------------
ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "apps" / "desktop" / "web"
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / _n))

CALL = re.compile(r"fetch\((.*?)\)", re.S)
EXEMPT = "/api/upload"          # 唯一豁免：它走原始字节 body（仍要过 Origin 校验，但不收 JSON 头）


class WebFetchTest(unittest.TestCase):
    def test_post_fetches_carry_json_header(self) -> None:
        bad = []
        files = [WEB / "index.html"] + sorted((WEB / "vendor").glob("*.js"))
        for f in files:
            for m in CALL.finditer(f.read_text(encoding="utf-8")):
                arg = m.group(1)
                if '"POST"' not in arg and "'POST'" not in arg:
                    continue
                if EXEMPT in arg or "Content-Type" in arg:
                    continue
                bad.append("%s: %s" % (f.name, " ".join(arg.split())[:90]))
        self.assertEqual(bad, [], "这些 POST 少了 Content-Type: application/json（会被 415 挡掉）：\n" + "\n".join(bad))


class ChatWithoutConversationTest(unittest.TestCase):
    """主界面没有对话就直接发消息：服务端必须现开一条不绑项目的对话。
    以前空 id 一路走到 run_agent 的 db.summary('') → KeyError，整轮发言变成一条红字报错。"""

    def setUp(self) -> None:
        self.server = (ROOT / "apps" / "desktop" / "server.py").read_text(encoding="utf-8")
        self.chat_js = (WEB / "chat-views.js").read_text(encoding="utf-8")

    def test_server_creates_chat_when_task_id_is_empty_or_stale(self) -> None:
        self.assertIn("db.create_chat(\"\")", self.server)
        self.assertIn("SELECT 1 FROM chats WHERE id=?", self.server)
        self.assertIn("SELECT 1 FROM research_tasks WHERE id=?", self.server)

    def test_frontend_no_longer_blocks_send(self) -> None:
        self.assertNotIn('alert("先新建一个对话（左上角 ＋）")', self.chat_js)
        self.assertIn("/api/chat/create", self.chat_js)


class UploadOriginGateTest(unittest.TestCase):
    """附件上传走原始字节 body，绕不过浏览器的 preflight —— 所以服务端必须自己先挡跨站 Origin。
    2026-10-20 之前 /api/upload 在 Origin 校验之前就 return 了：任何网页都能往 uploads/ 塞文件。"""

    @classmethod
    def setUpClass(cls) -> None:
        import os, socket, subprocess, tempfile, time
        cls.tmp = tempfile.TemporaryDirectory(prefix="rw-upload-gate-")
        cls.addClassCleanup(cls.tmp.cleanup)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        env = dict(os.environ, LANJI_ZHIYAN_HOME=cls.tmp.name, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "apps/desktop/server.py"),
                                     "--port", str(cls.port), "--no-open"],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.proc.kill)
        for _ in range(80):                                  # 最多等 8s 起服务
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.5).close()
                return
            except OSError:
                time.sleep(0.1)

    def _post(self, desc: str, origin: str | None, body: bytes = b"hi") -> int:
        import urllib.error, urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload?name=probe.txt", data=body,
                                     headers={"Content-Type": "text/plain"})   # 跨站「简单请求」的形状
        if origin:
            req.add_header("Origin", origin)
        try:
            return urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_跨站来源被挡(self) -> None:
        self.assertEqual(self._post("evil", "https://evil.example"), 403)

    def test_本机来源放行(self) -> None:
        self.assertIn(self._post("self", f"http://127.0.0.1:{self.port}"), (200, 400))

    def test_没有_Origin_的本机进程照旧可用(self) -> None:
        self.assertIn(self._post("none", None), (200, 400))


class MainScriptSyntaxTest(unittest.TestCase):
    """web/index.html 里那块主脚本必须过 node --check。
    2026-10-12 踩过：加一个 class 时多带了一个反引号，模板串提前闭合，整页只剩一句 SyntaxError 白屏 ——
    这种错 py_compile / 肉眼都抓不到，只有把脚本抠出来 node --check 才看得见。"""

    def test_main_inline_script_parses(self):
        h = (WEB / "index.html").read_text(encoding="utf-8")
        blocks = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", h, re.S)
        main = max(blocks, key=len)
        tmp = pathlib.Path(tempfile.mkstemp(suffix=".js")[1])
        tmp.write_text(main, encoding="utf-8")
        try:
            r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.skipTest("没装 node（或超时），跳过")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass          # Windows：node 刚读完句柄可能还没放，删不掉就算了（Temp 目录会自己清）
        self.assertEqual(r.returncode, 0, "主脚本语法错：" + chr(10) + (r.stderr or "")[-900:])


if __name__ == "__main__":
    unittest.main()
