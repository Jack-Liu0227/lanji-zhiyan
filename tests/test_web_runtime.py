from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(__file__).resolve().parents[1]
EDGE = next(
    (path for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
     if base and (path := Path(base) / "Microsoft/Edge/Application/msedge.exe").is_file()),
    None,
)


@unittest.skipUnless(sync_playwright and EDGE, "Requires Playwright and Microsoft Edge")
class WebRuntimeTest(unittest.TestCase):
    """Execute the actual page: syntax checks cannot catch failed DOM bindings."""

    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="rw-web-runtime-")
        cls.addClassCleanup(temporary.cleanup)
        log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        cls.addClassCleanup(log.close)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/"
        env = dict(os.environ, RESEARCH_WORKBENCH_DB=str(Path(temporary.name) / "research.sqlite3"),
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        server = subprocess.Popen(
            [sys.executable, str(ROOT / "apps/desktop/server.py"), "--port", str(port), "--no-open"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

        def stop_server() -> None:
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)

        cls.addClassCleanup(stop_server)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and server.poll() is None:
            try:
                with urllib.request.urlopen(cls.url, timeout=1) as response:
                    response.read()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            log.seek(0)
            raise RuntimeError(f"Test server failed to start (rc={server.poll()}): {log.read()}")

        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=False)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def load_page(self, suffix: str = "") -> None:
        self.page.goto(self.url + suffix, wait_until="networkidle")
        self.assertEqual(self.errors, [], "Browser initialization failed: " + "\n".join(self.errors))

    def test_startup_renders_navigation_and_agent(self) -> None:
        self.load_page("?pw=1")
        self.assertTrue(self.page.evaluate("S.loaded"))
        self.assertGreater(self.page.locator("#sidebar [data-nav]").count(), 5)
        self.assertGreater(len(self.page.locator("#main").inner_text().strip()), 20)
        self.assertEqual(self.page.locator("#tabsbar [data-t]").count(), 1)
        self.assertIn("SQLite", self.page.locator("#statusbar").inner_text())

    def test_zotero_modal_closes_from_button_and_mask(self) -> None:
        self.load_page()
        # Exercise the real modal and its bindings without requiring Zotero to run.
        modal = self.page.locator("#zoteroModal")
        for selector in ("#btnZotClose", "#zoteroModal .modal-mask"):
            modal.evaluate("element => element.classList.remove('hidden')")
            self.page.locator(selector).click(position={"x": 5, "y": 5})
            self.assertTrue(modal.evaluate("element => element.classList.contains('hidden')"))
        self.assertEqual(self.errors, [])

    def test_project_notes_dashboard_opens_existing_markdown(self) -> None:
        self.load_page()
        self.page.evaluate("openTab('notes')")
        self.page.locator('[data-tree-path="笔记/文献矩阵"]').wait_for()
        self.page.evaluate("""async () => {
          const project_id = S.tasks[0].id;
          await api('/api/project/note/save', {project_id, path:'笔记/文献矩阵/浏览器验证.md', content:'# 浏览器验证\\n\\n已有内容'});
        }""")
        self.page.locator('[data-tree-path="笔记/文献矩阵"]').click()
        self.page.locator('[data-pn-file="笔记/文献矩阵/浏览器验证.md"]').click()
        self.page.locator("#mdSource").wait_for()
        self.assertIn("已有内容", self.page.locator("#mdSource").input_value())
        self.assertEqual(self.errors, [])

    def test_steward_binds_existing_project_and_restores_selection(self) -> None:
        self.load_page()
        project = self.page.evaluate("S.tasks[0]")
        picker = self.page.get_by_role("combobox", name="选择已有项目")
        picker.select_option(project["id"])
        self.page.wait_for_function("id => S.chats.find(c => c.id === curChat)?.task_id === id && document.querySelector('#outpick')?.disabled", arg=project["id"])
        self.assertTrue(self.page.locator("#outpick").is_disabled())
        self.assertIn(project["title"], self.page.locator(".at-project-scope").inner_text())
        self.assertIn("复制后处理", self.page.locator(".at-project-scope").inner_text())
        self.page.reload(wait_until="networkidle")
        self.assertEqual(self.page.locator("#projbind").input_value(), project["id"])
        error = self.page.evaluate("""async () => {
          try { await api('/api/chat/bind', {id:curChat, task_id:'missing-project'}); return ''; }
          catch (e) { return e.message; }
        }""")
        self.assertTrue(error)
        self.page.locator("#projbind").select_option("")
        self.page.wait_for_function("S.chats.find(c => c.id === curChat)?.task_id === '' && !document.querySelector('#outpick')?.disabled")
        self.assertFalse(self.page.locator("#outpick").is_disabled())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
