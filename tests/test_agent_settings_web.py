"""Browser regressions for real provider/assistant/team settings and persistence."""
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
class AgentSettingsWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="rw-agent-web-")
        cls.addClassCleanup(tmp.cleanup)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/"
        log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        cls.addClassCleanup(log.close)
        env = dict(os.environ, RESEARCH_WORKBENCH_DB=str(Path(tmp.name) / "test.sqlite3"), PYTHONIOENCODING="utf-8")
        server = subprocess.Popen([sys.executable, str(ROOT / "apps/desktop/server.py"), "--port", str(port), "--no-open"], cwd=ROOT, env=env, stdout=log, stderr=log)
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
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(self.url, wait_until="networkidle")
        self.page.wait_for_function("S.loaded")
        self.page.evaluate("document.getElementById('splash')?.remove()")

    def post(self, path, body):
        return self.page.evaluate("async ([path,body])=>api(path,body)", [path,body])

    def test_provider_modal_models_assistant_edit_and_mobile(self):
        page = self.page
        page.evaluate("setSec='models'; openTab('settings'); render()")
        page.locator("#ag-provider-select").select_option("custom")
        page.locator("#ag-provider-add").click()
        page.locator("#ag-p-name").fill("Browser provider")
        page.locator("#ag-p-url").fill("http://127.0.0.1:1/v1")
        page.locator("#ag-p-key").fill("browser-fake-secret")
        for model in ("model-one","model-two"):
            page.locator("#ag-p-manual").fill(model)
            page.locator("#ag-p-add").click()
        page.locator("#ag-p-apply").click()
        page.wait_for_selector("dialog.ag-modal", state="detached")
        page.wait_for_function("S.models.some(m=>m.model==='model-two')")
        self.assertEqual(page.locator("[data-ag-provider]").count(), 1)
        page.locator("[data-ag-provider]").click()
        page.locator("#ag-p-show").click()
        page.wait_for_function("document.querySelector('#ag-p-key').value==='browser-fake-secret'")
        self.assertEqual(page.locator("#ag-p-key").input_value(), "browser-fake-secret")
        page.locator("#ag-p-name").fill("Edited provider")
        page.locator("#ag-p-url").fill("http://127.0.0.1:2/v1")
        page.locator("#ag-p-save").click()
        page.wait_for_function("S.models.some(m=>m.base_url==='http://127.0.0.1:2/v1')")
        page.locator("dialog.ag-modal [data-close]").click()
        page.evaluate("openTab('assistants')")
        page.locator("#ag-assistant-new").click()
        page.locator("#ag-a-name").fill("Browser assistant")
        page.locator("#ag-a-model").select_option(label="Edited provider / model-two")
        page.locator("#ag-a-reasoning").select_option("high")
        page.locator("#ag-a-permission").select_option("full_access")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#ag-a-save").click()
        page.wait_for_selector("dialog.ag-modal", state="detached")
        page.wait_for_selector("#ag-a-edit")
        self.assertIn("Browser assistant", page.locator("#main").inner_text())
        self.assertIn("Full Access", page.locator("#main").inner_text())
        self.assertGreater(page.locator("[data-feishu-assistant]").count(), 0)
        page.locator("#ag-a-edit").click()
        self.assertEqual(page.locator("#ag-a-reasoning").input_value(), "high")
        self.assertEqual(page.locator("#ag-a-permission").input_value(), "full_access")
        page.set_viewport_size({"width": 390, "height": 844})
        bounds = page.locator("dialog.ag-modal").bounding_box()
        self.assertLessEqual(bounds["width"], 390)
        page.locator("dialog.ag-modal [data-close]").click()
        page.evaluate("openTab('agent')")
        page.wait_for_selector("#assist", state="attached")
        self.assertIn("Browser assistant", page.locator("#assist").inner_text())
        self.assertEqual(self.errors, [])

    def test_team_edit_leader_history_and_all_assistant_choices(self):
        page = self.page
        a = self.post("/api/agent-settings/assistant-save", {"name":"Team browser assistant", "enabled":False})["assistant"]
        self.post("/api/agent-settings/assistant-save", {"id":"builtin-searcher", "enabled":False})
        page.evaluate("refreshState(false)")
        page.evaluate("openTab('team')")
        page.locator("#ag-team-new").click()
        page.locator("#ag-t-name").fill("Browser durable team")
        page.locator("#ag-t-desc").fill("Editable team fixture")
        page.locator("#ag-t-save").click()
        page.wait_for_selector("dialog.ag-modal", state="detached")
        page.wait_for_function("document.querySelector('#main')?.innerText.includes('Editable team fixture')")
        page.locator("#ag-member-new").click()
        self.assertIn("Team browser assistant", page.locator("#ag-m-assistant").inner_text())
        self.assertNotIn("文献检索员", page.locator("#ag-m-assistant").inner_text())
        page.locator("#ag-m-assistant").select_option(a["id"])
        page.locator("#ag-m-save").click()
        page.wait_for_selector("dialog.ag-modal", state="detached")
        page.locator("[data-ag-member]").filter(has_text="Team browser assistant").click()
        page.locator("#ag-m-leader").click()
        page.wait_for_selector("dialog.ag-modal", state="detached")
        page.wait_for_function("[...document.querySelectorAll('[data-ag-member]')].some(x=>x.innerText.includes('Leader · Team browser assistant'))")
        previous = page.locator("#ag-team-session").input_value()
        page.locator("#ag-session-new").click()
        page.wait_for_function("document.querySelector('#ag-team-session').options.length===2")
        self.assertNotEqual(previous, page.locator("#ag-team-session").input_value())
        page.locator("#ag-team-session").select_option(previous)
        page.wait_for_function("document.querySelector('#ag-team-session').value===document.querySelector('#ag-team-session option:checked').value")
        page.reload(wait_until="networkidle")
        page.wait_for_function("S.loaded")
        page.evaluate("document.getElementById('splash')?.remove(); openTab('team')")
        page.wait_for_selector("#ag-team-session")
        self.assertEqual(page.locator("#ag-team-session").input_value(), previous)
        self.assertEqual(self.errors, [])

    def test_feishu_device_session_ui_is_assistant_scoped(self):
        page = self.page
        first = page.evaluate("S.assistants[0]?.id || ''")
        self.assertTrue(first)
        second = self.post("/api/agent-settings/assistant-save", {"name": "Feishu device UI assistant", "enabled": False})["assistant"]["id"]
        page.evaluate("refreshState(false)")
        page.evaluate("""() => {
          const realApi = api;
          window.__registrationCalls = [];
          api = async (path, body) => {
            if (path.startsWith('/api/feishu/registration/')) {
              window.__registrationCalls.push({path, body});
              if (path.endsWith('/begin')) return {
                assistant_id: body.assistant_id, session_id: 'session-' + body.assistant_id,
                region: body.region, qr_url: 'https://accounts.feishu.cn/device?user_code=TEST',
                qr_svg: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCI+PHJlY3Qgd2lkdGg9IjEwIiBoZWlnaHQ9IjEwIiBmaWxsPSIjMDAwIi8+PC9zdmc+',
                interval: 30, expires_in: 60, status: 'pending'
              };
              if (path.endsWith('/poll')) return {status: 'pending', interval: 30, expires_in: 60};
              return {status: 'cancelled'};
            }
            return realApi(path, body);
          };
        }""")
        page.evaluate("id => IntegrationSettings.openFeishu(id)", first)
        page.wait_for_selector("#fs-region")
        page.locator("#fs-region").select_option("lark")
        page.locator("#fs-device-begin").click()
        page.wait_for_selector("#fs-device-qr img")
        self.assertIn(first, page.evaluate("__registrationCalls.find(c => c.path.endsWith('/begin')).body.assistant_id"))

        page.locator("#int-assistant").select_option(second)
        page.wait_for_function("document.querySelector('#fs-device-qr')?.innerText.includes('点击')")
        self.assertEqual(page.locator("#fs-device-qr img").count(), 0)
        page.locator("#int-assistant").select_option(first)
        page.wait_for_selector("#fs-device-qr img")
        self.assertEqual(page.locator("#fs-region").input_value(), "lark")
        page.locator("#fs-device-cancel").click()
        page.wait_for_function("document.querySelector('#fs-device-status')?.innerText.includes('已取消')")
        cancel = page.evaluate("__registrationCalls.find(c => c.path.endsWith('/cancel'))")
        self.assertEqual(cancel["body"], {"assistant_id": first, "session_id": "session-" + first})
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
