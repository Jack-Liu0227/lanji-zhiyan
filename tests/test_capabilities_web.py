"""Exercise the actual capability editor against an isolated HTTP service."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
EDGE = next((path for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
             if base and (path := Path(base) / "Microsoft/Edge/Application/msedge.exe").is_file()), None)


@unittest.skipUnless(sync_playwright and EDGE, "Requires Playwright and Microsoft Edge")
class CapabilityWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="rw-capabilities-web-")
        cls.addClassCleanup(temporary.cleanup)
        home = Path(temporary.name) / "home"
        cls.skill = home / ".agents/skills/demo/SKILL.md"
        cls.skill.parent.mkdir(parents=True)
        cls.skill.write_text("---\nname: demo\ndescription: Safe browser fixture\n---\nOriginal fixture body\n", encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/"
        log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        cls.addClassCleanup(log.close)
        env = dict(os.environ, RESEARCH_WORKBENCH_DB=str(Path(temporary.name) / "test.sqlite3"),
                   USERPROFILE=str(home), HOME=str(home), PYTHONIOENCODING="utf-8")
        server = subprocess.Popen([sys.executable, str(ROOT / "apps/desktop/server.py"), "--port", str(port), "--no-open"],
                                  cwd=ROOT, env=env, stdout=log, stderr=log)

        def stop():
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)

        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and server.poll() is None:
            try:
                urllib.request.urlopen(cls.url, timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            log.seek(0)
            raise RuntimeError("Test server failed to start: " + log.read())
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=False)
        cls.addClassCleanup(cls.browser.close)

    def test_skill_matrix_mcp_editor_and_mobile_dialog(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_function("S.loaded")
        page.evaluate("document.getElementById('splash')?.remove(); setSec='skills'; openTab('settings'); render()")
        page.locator('[data-cap-preview="demo"]').click()
        page.locator("[data-edit]").click()
        page.locator("[data-content]").fill("---\nname: demo\ndescription: Safe browser fixture\n---\nEdited by browser fixture\n")
        page.locator("[data-save]").click()
        page.wait_for_selector(".cap-overlay", state="detached")
        self.assertIn("Edited by browser fixture", self.skill.read_text(encoding="utf-8"))
        page.locator('[data-cap-view="matrix"]').click()
        with page.expect_response(lambda response: response.url.endswith("/api/capabilities/binding") and response.request.method == "POST"):
            page.locator('[data-cap-binding][data-assistant="*"]').first.uncheck()
        page.wait_for_function("document.querySelector('[data-cap-binding][data-assistant=\"*\"]')?.checked === false")
        self.assertFalse(page.locator('[data-cap-binding][data-assistant="*"]').first.is_checked())
        page.evaluate("setSec='tools'; render()")
        page.wait_for_selector('[data-cap-binding][data-kind="tool"]')
        page.evaluate("setSec='mcp'; render()")
        page.locator("#cap-mcp-json").click()
        config = {"mcpServers": {"fixture": {"command": "python", "args": ["--version"], "env": {"API_KEY": "test-browser-secret"}}}}
        page.locator("[data-json]").fill(json.dumps(config))
        page.locator("[data-save]").click()
        page.wait_for_selector(".cap-overlay", state="detached")
        page.locator("#cap-mcp-json").click()
        self.assertNotIn("test-browser-secret", page.locator("[data-json]").input_value())
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("[data-reveal]").click()
        page.wait_for_function("document.querySelector('[data-json]').value.includes('test-browser-secret')")
        page.locator("[data-hide]").click()
        self.assertNotIn("test-browser-secret", page.locator("[data-json]").input_value())
        page.locator("[data-close]").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("setSec='skills'; render()")
        page.locator("#cap-project").wait_for()
        page.locator('[data-cap-view="list"]').click()
        page.locator('[data-cap-preview="demo"]').click()
        page.locator('[role="dialog"]').wait_for(state="visible")
        self.assertTrue(page.locator('[role="dialog"]').is_visible())
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".cap-overlay").count(), 0)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
