"""Install real files through the desktop UI using an isolated app instance."""
from __future__ import annotations

import json
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from test_web_runtime import EDGE, ROOT, sync_playwright


@unittest.skipUnless(sync_playwright and EDGE, "Requires Playwright and Microsoft Edge")
class SkillInstallationWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="skill-install-web-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.home = cls.root / "home"
        cls.db_path = cls.root / "app.sqlite3"
        cls.source = cls.root / "source/browser-skill"
        cls.source.mkdir(parents=True)
        (cls.source / "SKILL.md").write_text("---\nname: browser-skill\ndescription: Browser installation fixture\n---\n# Browser skill\n", encoding="utf-8")
        (cls.source / "assets").mkdir()
        (cls.source / "assets/example.bin").write_bytes(b"\xff\x00\x80")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.url = f"http://127.0.0.1:{port}/"
        log = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        cls.addClassCleanup(log.close)
        env = dict(os.environ, LANJI_ZHIYAN_HOME=str(cls.root / "app-home"),
                   LANJI_ZHIYAN_DB=str(cls.db_path), RESEARCH_WORKBENCH_DB=str(cls.db_path),
                   USERPROFILE=str(cls.home), HOME=str(cls.home), PYTHONIOENCODING="utf-8")
        server = subprocess.Popen([sys.executable, str(ROOT / "apps/desktop/server.py"), "--port", str(port), "--no-open"],
                                  cwd=ROOT, env=env, stdout=log, stderr=log,
                                  creationflags=0x08000000 if os.name == "nt" else 0)
        def stop():
            if server.poll() is None:
                server.terminate()
            server.wait(timeout=10)
        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and server.poll() is None:
            try:
                urllib.request.urlopen(cls.url, timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            log.seek(0)
            raise RuntimeError("Test service failed: " + log.read())
        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def test_built_in_search_install_local_duplicate_and_mobile_flow(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(15000)
        errors, install_requests = [], []
        page.on("pageerror", lambda failure: errors.append(str(failure)))
        page.on("request", lambda request: install_requests.append(request.post_data_json)
                if request.url.endswith("/api/capabilities/skill-install") else None)
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_function("S.loaded")
        page.evaluate("setSec='skills'; openTab('settings'); render()")
        page.locator("#cap-install").click()
        page.wait_for_function("document.querySelector('[data-si-provider]')?.disabled === false")
        self.assertEqual(page.locator("[data-si-provider] option").all_text_contents(), ["全部来源", "skills.sh", "SkillHub", "SkillsMP"])
        self.assertEqual(page.locator("[data-si-provider]").input_value(), "all")
        page.locator("[data-si-provider]").select_option("skillhub")
        response = {"provider": "skillhub", "query": "browser", "skills": [{
            "name": "browser-skill", "description": "Browser installation fixture", "author": "Fixture",
            "source": str(self.source), "url": "https://skillhub.cn/skills/browser-skill", "downloads": 10}]}
        page.route("**/api/capabilities/skill-search?**", lambda route: route.fulfill(json=response))
        page.locator("[data-si-query]").fill("browser")
        page.locator("[data-si-search]").click()
        page.locator("[data-si-result-preview]").click()
        page.locator("[data-si-install]").wait_for(state="visible")
        self.assertEqual(page.locator("[data-si-skill]:checked").count(), 1)
        self.assertEqual(page.locator(".cap-dialog [data-assistant]").count(), 0)
        screenshots = os.environ.get("LANJI_TEST_SCREENSHOTS")
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(screenshots) / "skill-installation-desktop.png"))
        with closing(sqlite3.connect(self.db_path)) as db:
            before = list(db.execute("SELECT * FROM capability_bindings"))
        page.locator("[data-si-install]").click()
        page.locator("[data-si-done]").wait_for()
        target = self.home / ".agents/skills/browser-skill"
        self.assertEqual((target / "assets/example.bin").read_bytes(), b"\xff\x00\x80")
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertNotIn("assistant_id", install_requests[0])
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(list(db.execute("SELECT * FROM capability_bindings")), before)
            self.assertEqual(db.execute("SELECT provider FROM skill_installations WHERE name='browser-skill'").fetchone()[0], "skillhub")
        page.locator("[data-si-done]").click()
        page.locator('[data-cap-row="browser-skill"]').wait_for()
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#cap-install").click()
        page.locator('[data-si-tab="import"]').click()
        page.locator("[data-si-source]").fill(str(self.source))
        page.locator("[data-si-preview-button]").click()
        page.locator("[data-si-install]").wait_for(state="visible")
        page.locator("[data-si-install]").click()
        page.wait_for_function("document.querySelector('.cap-dialog [data-error]')?.textContent.includes('同名')")
        self.assertEqual((target / "assets/example.bin").read_bytes(), b"\xff\x00\x80")
        self.assertTrue(page.evaluate("document.querySelector('.cap-dialog').getBoundingClientRect().width <= innerWidth"))
        if screenshots:
            page.screenshot(path=str(Path(screenshots) / "skill-installation-mobile.png"))
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".cap-overlay").count(), 0)
        self.assertEqual(errors, [])

    def test_all_source_results_and_persistent_source_management(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        page.on("pageerror", lambda failure: errors.append(str(failure)))
        page.goto(self.url, wait_until="networkidle")
        page.wait_for_function("S.loaded")
        page.evaluate("setSec='skills'; openTab('settings'); render()")
        self.addCleanup(lambda: page.request.post(self.url + "api/capabilities/skill-sources/restore", data={}))
        page.locator("#cap-install").click()
        page.wait_for_function("document.querySelector('[data-si-provider]')?.disabled === false")
        self.assertEqual(page.locator("[data-si-provider]").input_value(), "all")
        result = {"provider": "all", "skills": [{"provider": "skillhub", "provider_name": "SkillHub", "name": "browser-skill",
            "description": "Example skill", "source": str(self.source), "url": "https://skillhub.cn/skills/browser-skill"}],
            "errors": [{"provider": "skillsmp", "name": "SkillsMP", "error": "temporary quota"}]}
        requests = []
        def search_response(route):
            requests.append(route.request.url)
            route.fulfill(json=result)
        page.route("**/api/capabilities/skill-search?**", search_response)
        page.locator("[data-si-query]").fill("browser")
        page.locator("[data-si-search]").click()
        page.locator(".si-origin").wait_for()
        self.assertIn("provider=all", requests[0])
        self.assertEqual(page.locator(".si-origin").inner_text(), "SkillHub")
        self.assertIn("temporary quota", page.locator("[data-si-source-errors]").inner_text())
        with page.expect_request(lambda request: request.url.endswith("/api/capabilities/skill-preview")) as preview:
            page.locator("[data-si-result-preview]").click()
        self.assertEqual(preview.value.post_data_json["provider"], "skillhub")
        page.locator("[data-si-install]").wait_for()
        page.keyboard.press("Escape")
        page.locator("#cap-skill-sources").click()
        page.locator('[data-si-source-id="skills-sh"]').wait_for()
        self.assertTrue(page.get_by_role("dialog", name="技能源管理", exact=True).is_visible())
        source_row = page.locator('[data-si-source-id="skills-sh"]')
        source_row.locator('[data-si-source-action="edit"]').click()
        page.locator('[data-si-config="name"]').fill("技能目录 A")
        page.locator("[data-si-source-save]").click()
        page.wait_for_function("document.querySelector('[data-si-source-id=\"skills-sh\"]')?.textContent.includes('技能目录 A')")
        source_row.locator('[data-si-source-action="toggle"]').click()
        source_row.get_by_role("button", name="启用", exact=True).wait_for()
        page.locator('[data-si-source-id="skillsmp"] [data-si-source-action="delete"]').click()
        page.locator('[data-si-source-id="skillsmp"]').wait_for(state="detached")
        page.locator("[data-si-source-add]").click()
        self.assertFalse(page.locator("[data-si-download-field]").is_visible())
        page.locator('[data-si-config="name"]').fill("我的技能源")
        page.locator('[data-si-config="url"]').fill("https://example.org")
        page.locator('[data-si-config="api_url"]').fill("https://example.org/catalog.json")
        with page.expect_response(lambda response: response.url.endswith("/api/capabilities/skill-sources") and response.request.method == "POST") as saved:
            page.locator("[data-si-source-save]").click()
        custom_id = saved.value.json()["source"]["id"]
        self.addCleanup(lambda: page.request.post(self.url + "api/capabilities/skill-sources/delete", data={"id": custom_id}))
        page.locator('[data-si-source-id="' + custom_id + '"]').wait_for()
        page.keyboard.press("Escape")
        page.locator("#cap-skill-sources").click()
        page.locator('[data-si-source-id="' + custom_id + '"]').wait_for()
        self.assertEqual(page.locator('[data-si-source-id="skillsmp"]').count(), 0)
        page.locator('[data-si-source-id="skills-sh"]').get_by_role("button", name="启用", exact=True).wait_for()
        page.locator('[data-si-tab="community"]').click()
        self.assertEqual(page.locator("[data-si-provider] option").all_text_contents(), ["全部来源", "SkillHub", "我的技能源"])
        page.locator('[data-si-tab="sources"]').click()
        page.locator("[data-si-source-restore]").click()
        page.locator('[data-si-source-id="skillsmp"]').wait_for()
        self.assertIn("skills.sh", page.locator('[data-si-source-id="skills-sh"]').inner_text())
        self.assertEqual(page.locator('[data-si-source-id="' + custom_id + '"]').count(), 1)
        page.locator('[data-si-source-id="' + custom_id + '"] [data-si-source-action="delete"]').click()
        page.locator('[data-si-source-id="' + custom_id + '"]').wait_for(state="detached")
        screenshots = os.environ.get("LANJI_TEST_SCREENSHOTS")
        if screenshots:
            Path(screenshots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(screenshots) / "skill-sources-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('[data-si-source-id="skills-sh"] [data-si-source-action="edit"]').click()
        self.assertTrue(page.evaluate("document.querySelector('.cap-dialog').getBoundingClientRect().width <= innerWidth"))
        if screenshots:
            page.screenshot(path=str(Path(screenshots) / "skill-sources-mobile.png"))
        page.keyboard.press("Escape")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
