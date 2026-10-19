"""Real browser checks for project creation and persisted workflow diagrams."""
import tempfile
import unittest

from tests import test_web_runtime as runtime


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class WorkflowLayoutRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page

    def load_workspace(self):
        self.load_page()

    def test_bottom_new_project_card_creates_and_persists_project(self):
        self.load_workspace()
        with tempfile.TemporaryDirectory(prefix="rw-project-base-") as base:
            self.page.evaluate("base => { tDir=base; openTab('tasks'); }", base)
            self.page.locator("button.proj-add").click()
            self.assertTrue(self.page.locator("dialog.wfl-project-dialog").is_visible())
            self.page.locator("#t-go").click()
            self.assertIn("请填写", self.page.locator("#t-title").evaluate("el => el.validationMessage"))
            self.page.locator("#t-title").fill("Bottom card project")
            self.page.locator("#t-q").fill("Does creation persist?")
            self.page.locator("#t-kw").fill("regression, workflow")
            with self.page.expect_response(lambda response: response.url.endswith("/api/task/create")):
                self.page.locator("#t-go").click()
            self.page.wait_for_function("S.tasks.some(t=>t.title==='Bottom card project')")
            self.page.wait_for_selector("dialog.wfl-project-dialog", state="detached")
            self.assertEqual(self.page.locator("dialog.wfl-project-dialog").count(), 0)
            self.assertIn("项目已创建", self.page.locator(".wfl-project-notice").inner_text())
            tid = self.page.evaluate("curTask")
            saved = self.page.request.get(self.url + "api/task?id=" + tid).json()["task"]
            self.assertEqual(saved["base_dir"], base)
            self.assertEqual(saved["question"], "Does creation persist?")
            self.page.reload(wait_until="networkidle")
            self.page.wait_for_function("S.loaded")
            self.page.evaluate("openTab('tasks')")
            self.assertIn("Bottom card project", self.page.locator(".proj-cards").inner_text())
        self.assertEqual(self.errors, [])

    def test_workflow_editor_saves_custom_order(self):
        self.load_workspace()
        self.page.evaluate("setSec='workflows'; openTab('settings'); render()")
        self.page.wait_for_selector("[data-wfl-new]")
        self.page.locator("[data-wfl-new]").click()
        self.page.locator("[data-wfl-name]").fill("Browser custom workflow")
        self.page.locator("[data-wfl-add-step]").select_option("search")
        self.page.locator("[data-wfl-add]").click()
        self.page.locator("[data-wfl-add-step]").select_option("review")
        self.page.locator("[data-wfl-add]").click()
        # Non-default order must be reflected in the actual step rail as well as the graph.
        self.page.locator('[data-wfl-up="1"]').click()
        self.page.locator("[data-wfl-chain]").click()
        self.page.locator("[data-wfl-save]").click()
        self.page.wait_for_function("document.querySelector('[data-wfl-message]')?.textContent.includes('已保存')")
        listing = self.page.request.get(self.url + "api/workflow/layouts").json()["layouts"]
        custom = next(layout for layout in listing if layout["name"] == "Browser custom workflow")
        self.assertEqual(custom["order"], ["search", "idea", "review"])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
