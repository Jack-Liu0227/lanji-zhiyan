"""Real browser / HTTP checks; model output is controlled locally, never remote."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_web_runtime as runtime
import workflow
import workflow_layout
from run_manager import RunManager


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Microsoft Edge")
class WorkflowRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="rw-workflow-browser-")
        cls.addClassCleanup(tmp.cleanup)
        cls.path = Path(tmp.name) / "app.sqlite3"
        sys.path.insert(0, str(runtime.ROOT / "apps/desktop"))
        import server
        import team
        import schedule
        for module in (server, team, schedule):
            replacement = patch.object(module, "DB_PATH", cls.path)
            replacement.start()
            cls.addClassCleanup(replacement.stop)
        cls.manager = RunManager()
        replacement = patch.object(RunManager, "_instance", cls.manager)
        replacement.start()
        cls.addClassCleanup(replacement.stop)
        cls.release, cls.entered = threading.Event(), threading.Event()
        cls.calls = []

        def model(profile, messages, **kwargs):
            text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            if "[hold]" in text or "[stall]" in text:
                cls.calls.append(text)
                if "[hold]" in text and kwargs.get("on_delta"):
                    kwargs["on_delta"]("FIRST-PART ")
                cls.entered.set()
                cls.release.wait(12)
                if kwargs.get("on_delta"):
                    kwargs["on_delta"]("FINAL-PART")
                return {"content": "FIRST-PART FINAL-PART"}
            return {"content": "Local plan"}

        replacement = patch.object(workflow, "_chat_raw", side_effect=model)
        replacement.start()
        cls.addClassCleanup(replacement.stop)
        with workflow.WorkflowDB(cls.path) as db:
            db.add_model("Local fixture", "http://127.0.0.1:1/v1", "fixture", "local-test-only", activate=True)
        http = server.Server(("127.0.0.1", 0), server.Handler)
        cls.url = f"http://127.0.0.1:{http.server_port}/"
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        cls.addClassCleanup(http.server_close)
        cls.addClassCleanup(http.shutdown)
        playwright = runtime.sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(executable_path=str(runtime.EDGE), headless=True)
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.release.clear()
        self.entered.clear()
        self.calls.clear()
        with workflow.WorkflowDB(self.path) as db:
            self.project = db.create_task(self.id())
            self.other = db.create_task("Other project")
            self.chat = db.create_chat("Browser chat")
            workflow_layout.ensure_schema(db)
            layout = workflow_layout.save_layout(db, {"name": "Two stages", **workflow_layout._linear(["search", "outline"])})["layout"]
            workflow_layout.select_layout(db, {"task_id": self.project, "layout_id": layout["id"]})
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url, wait_until="networkidle")
        self.page.wait_for_function("S.loaded")

    def tearDown(self):
        self.release.set()
        deadline = time.monotonic() + 5
        while any(run.worker_started for run in self.manager.runs.values()) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.errors, [])

    def open_workflow(self):
        self.page.evaluate("pid => { curTask=pid; localStorage.setItem('rw-task',pid); wfStep='search'; openTab('workflow'); }", self.project)
        self.page.wait_for_selector("#wv-ask")
        self.page.wait_for_function("document.querySelector('#wv-send')?.onclick != null")

    def test_custom_navigation_reload_replays_without_second_execution(self):
        self.open_workflow()
        self.assertEqual(self.page.locator(".wf-step[data-step]").evaluate_all("nodes=>nodes.map(n=>n.dataset.step)"), ["search", "outline"])
        self.page.locator("#wv-ask").fill("[hold] workflow reload")
        self.page.locator("#wv-send").click()
        self.page.wait_for_function("document.querySelector('#wv-msgs')?.textContent.includes('FIRST-PART')")
        self.assertEqual(self.page.locator("#wv-send").inner_text(), "■")
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#wv-msgs')?.textContent.includes('FIRST-PART')")
        self.release.set()
        self.page.wait_for_function("wfRuns.size === 0")
        self.page.wait_for_function("document.querySelector('#wv-msgs')?.textContent.includes('FINAL-PART')")
        self.assertEqual(len(self.calls), 1)
        self.page.locator("#wv-ask").press("ArrowUp")
        self.assertEqual(self.page.locator("#wv-ask").input_value(), "[hold] workflow reload")

    def test_stop_before_first_model_byte_and_switch_project(self):
        self.open_workflow()
        self.page.locator("#wv-ask").fill("[stall] cancel")
        self.page.locator("#wv-send").click()
        self.page.wait_for_function("!!wfActiveRun()?.runId")
        self.assertTrue(self.entered.wait(5))
        run_id = self.page.evaluate("wfActiveRun().runId")
        self.page.locator("#wv-ask").press("Escape")
        self.page.wait_for_function("wfRuns.size === 0", timeout=3000)
        self.assertEqual(self.page.request.get(self.url + "api/runs/" + run_id).json()["status"], "cancelled")
        self.page.select_option("#wfProjSelect", self.other)
        self.page.wait_for_function("pid=>curTask===pid && document.querySelectorAll('.wf-step[data-step]').length===10", arg=self.other)
        self.release.set()
        self.page.wait_for_timeout(100)
        self.assertNotIn("FINAL-PART", self.page.locator("#wv-msgs").inner_text())

    def test_main_chat_reload_resumes_same_run(self):
        self.page.evaluate("cid => { curChat=cid; localStorage.setItem('rw-chat',cid); openTab('agent'); }", self.chat)
        self.page.locator("#ask").fill("[hold] chat reload")
        self.page.locator("#send").click()
        self.page.wait_for_function("document.querySelector('#msgs')?.textContent.includes('FIRST-PART')")
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("document.querySelector('#msgs')?.textContent.includes('FIRST-PART')")
        self.release.set()
        self.page.wait_for_function("!activeAgentRun")
        self.page.wait_for_function("document.querySelector('#msgs')?.textContent.includes('FINAL-PART')")
        self.assertEqual(len(self.calls), 1)

    def test_schedule_editor_binds_project_todo(self):
        with workflow.WorkflowDB(self.path) as db:
            import project_todos
            todo = project_todos.add_todo(db, self.project, "Scheduled research")
        self.page.evaluate("pid=>{curTask=pid; openTab('schedules');}", self.project)
        self.page.locator("#schNew").click()
        self.page.wait_for_function("document.querySelector('#schTodo') && !document.querySelector('#schTodo').disabled")
        self.page.locator("#schTodo").select_option(todo["id"])
        self.page.locator("#schAssistant").select_option("builtin-steward")
        self.page.locator("#schSave").click()
        self.page.wait_for_function("schEdit === null")
        with workflow.WorkflowDB(self.path) as db:
            row = next(s for s in db.schedules() if s["todo_id"] == todo["id"])
            linked = project_todos.list_todos(db, self.project)[0]
            self.assertEqual(linked["execution"]["schedule_id"], row["id"])
