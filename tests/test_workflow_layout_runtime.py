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

    def create_project(self, title):
        result = self.page.request.post(self.url + "api/task/create", data={"title": title}).json()
        self.assertNotIn("error", result)
        tid = result["task_id"]
        self.page.evaluate("async tid => { await refreshState(false); curTask=tid; localStorage.setItem('rw-task',tid); openTab('workflow'); }", tid)
        self.page.wait_for_selector(".wfl-shell")
        return tid

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
            self.assertIn("Bottom card project", self.page.locator(".proj-cards").inner_text())
        self.assertEqual(self.errors, [])

    def test_workflow_settings_save_graph_selection_and_polling(self):
        self.load_workspace()
        tid = self.create_project("Workflow browser project")
        self.page.locator("[data-wfl-settings]").click()
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
        self.page.evaluate("openTab('workflow')")
        self.page.wait_for_selector(f'[data-wfl-layout="{custom["id"]}"]')
        self.page.locator(f'[data-wfl-layout="{custom["id"]}"]').click()
        self.page.wait_for_function("document.querySelector('.wfl-graph summary')?.textContent.includes('Browser custom workflow')")
        visible_steps = self.page.locator(".wf-step:visible").evaluate_all("els=>els.map(el=>el.dataset.step)")
        self.assertEqual(visible_steps, ["search", "idea", "review"])
        self.assertEqual(self.page.locator(".wfl-node").count(), 3)
        with self.page.expect_response(lambda response: response.url.endswith("/api/workflow/layouts/select")):
            self.page.locator('[data-wfl-node="review"]').click()
        self.page.wait_for_function("document.querySelector('.wf-step.on')?.dataset.step==='review'")
        self.page.wait_for_function("document.querySelector('[data-wfl-node=review]')?.classList.contains('selected')")
        # A poll updates status without replacing the user's in-progress editor.
        editor = self.page.locator("#review-ask")
        editor.fill("Keep my unsent text")
        self.page.route("**/api/workflow/layouts/state?*", lambda route: route.fulfill(
            json=self.state_with_running_search(tid)))
        self.page.locator("[data-wfl-refresh]").click()
        self.page.wait_for_selector('[data-wfl-node="search"].running')
        self.assertEqual(editor.input_value(), "Keep my unsent text")
        self.page.unroute("**/api/workflow/layouts/state?*")
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_selector('[data-wfl-node="review"].selected')
        self.assertIn("Browser custom workflow", self.page.locator(".wfl-graph summary").inner_text())
        self.assertEqual(self.errors, [])

    def state_with_running_search(self, tid):
        response = self.page.request.get(self.url + "api/workflow/layouts/state?task_id=" + tid).json()
        for node in response["nodes"]:
            if node["step"] == "search":
                node.update(status="running", summary="Browser state fixture", source="test")
        return response


if __name__ == "__main__":
    unittest.main()
