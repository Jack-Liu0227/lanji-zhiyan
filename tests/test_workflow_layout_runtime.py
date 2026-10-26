"""Real browser checks for project creation and persisted workflow diagrams."""
import tempfile
import unittest
from pathlib import Path

import papertran
from _pdf_fixture import write_text_pdf

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

    def test_platform_switches_saved_workflow_and_restores_custom_step(self):
        self.load_workspace()
        self.page.evaluate("wfMin=false; openTab('workflow')")
        self.page.get_by_role("combobox", name="项目工作流").wait_for()
        listing = self.page.request.get(self.url + "api/workflow/layouts").json()
        canonical = {stage["id"]: stage["name"] for stage in listing["stages"]}
        for button in self.page.locator(".wf-step").all():
            self.assertEqual(button.locator(".wf-lbl").inner_text(), canonical[button.get_attribute("data-step")])
        project = self.page.evaluate("curTask")
        response = self.page.evaluate("""async () => api('/api/workflow/layouts/save', {
          name:'Browser translated paper', nodes:[{id:'translate',step:'notes',label:'Custom translation'}],
          edges:[], order:['translate']
        })""")
        self.page.evaluate("render()")
        picker = self.page.get_by_role("combobox", name="项目工作流")
        picker.locator(f'option[value="{response["layout"]["id"]}"]').wait_for(state="attached")
        picker.select_option(response["layout"]["id"])
        self.page.wait_for_function("WF_ORDER.length===1 && WF_ORDER[0]==='notes' && !document.querySelector('#wfLayoutSelect').disabled")
        self.assertEqual(self.page.locator(".wf-lbl").inner_text(), "Custom translation")
        self.page.wait_for_function("document.querySelector('#wfAgent')?.textContent.includes('翻译助手')")
        state = self.page.request.get(self.url + "api/workflow/layouts/state?task_id=" + project).json()
        self.assertEqual(state["selected_node"], "translate")
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_function("S.loaded")
        self.page.evaluate("openTab('workflow')")
        self.page.wait_for_function("WF_ORDER.length===1 && WF_ORDER[0]==='notes'")
        self.assertEqual(self.page.locator(".wf-lbl").inner_text(), "Custom translation")
        self.assertEqual(self.errors, [])

    def test_settings_step_change_updates_default_label_and_keeps_custom_label(self):
        self.load_workspace()
        self.page.evaluate("setSec='workflows'; openTab('settings'); render()")
        self.page.locator("[data-wfl-new]").click()
        self.page.locator("[data-wfl-step]").select_option("notes")
        self.assertEqual(self.page.locator("[data-wfl-label]").input_value(), "论文翻译")
        self.page.locator("[data-wfl-label]").fill("My custom name")
        self.page.locator("[data-wfl-step]").select_option("review")
        self.assertEqual(self.page.locator("[data-wfl-label]").input_value(), "My custom name")
        self.assertEqual(self.errors, [])

    def test_team_api_exposes_professional_leaders(self):
        self.load_workspace()
        project = self.page.evaluate("curTask")
        offices = self.page.request.get(self.url + "api/teams?project=" + project).json()["teams"]
        self.assertEqual({t["leader"]["name"] for t in offices}, {"文献研究负责人", "学术写作负责人"})
        self.assertTrue(all(t["leader"]["assistant_id"] != "builtin-steward" for t in offices))
        self.assertEqual(self.errors, [])

    def test_settings_refreshes_external_templates_without_losing_unsaved_changes(self):
        self.load_workspace()
        self.page.evaluate("setSec='workflows'; openTab('settings'); render()")
        self.page.locator("[data-wfl-new]").click()
        self.page.locator("[data-wfl-name]").fill("My unsaved workflow")
        self.page.evaluate("""async () => api('/api/workflow/layouts/save', {
          name:'Created by steward', nodes:[{id:'review',step:'review',label:'Review'}], edges:[], order:['review']
        })""")
        self.page.evaluate("openTab('agent'); openTab('settings'); render()")
        self.page.wait_for_function("[...document.querySelectorAll('[data-wfl-edit]')].some(el=>el.textContent.includes('Created by steward'))", timeout=5000)
        self.assertEqual(self.page.locator("[data-wfl-name]").input_value(), "My unsaved workflow")
        self.assertEqual(self.errors, [])

    def test_platform_observes_backend_selection_in_the_same_project(self):
        self.load_workspace()
        self.page.evaluate("""async () => {
          await api('/api/workflow/layouts/select',{task_id:curTask,layout_id:'builtin.workflow.research',node_id:'write'});
          openTab('workflow');
        }""")
        self.page.wait_for_function("document.querySelector('.wf-step.on')?.dataset.step==='write'")
        self.page.evaluate("""async () => {
          await api('/api/workflow/layouts/select',{task_id:curTask,layout_id:'builtin.workflow.writing',node_id:'outline'});
          await renderWorkflow(document.getElementById('main'));
        }""")
        self.assertEqual(self.page.locator(".wf-step.on").get_attribute("data-step"), "outline")
        self.assertEqual(self.errors, [])

    def test_rapid_step_selection_keeps_last_choice_when_saves_are_delayed(self):
        self.load_workspace()
        self.page.evaluate("""async () => {
          await api('/api/workflow/layouts/select',{task_id:curTask,layout_id:'builtin.workflow.research'});
          openTab('workflow');
        }""")
        self.page.locator('.wf-step[data-step="write"]').wait_for()
        self.page.evaluate("""() => {
          const original=api;
          window.savedChoices=[];
          api=async (path, body)=>{
            if(path==='/api/workflow/layouts/select' && body?.node_id==='search')
              await new Promise(resolve=>setTimeout(resolve,150));
            const result=await original(path,body);
            if(path==='/api/workflow/layouts/select') window.savedChoices.push(body.node_id);
            return result;
          };
          document.querySelector('.wf-step[data-step="search"]').click();
          document.querySelector('.wf-step[data-step="write"]').click();
        }""")
        self.page.wait_for_function("window.savedChoices.length===2")
        project = self.page.evaluate("curTask")
        state = self.page.request.get(self.url + "api/workflow/layouts/state?task_id=" + project).json()
        self.assertEqual(state["selected_node"], "write")
        self.assertEqual(self.page.locator(".wf-step.on").get_attribute("data-step"), "write")
        self.assertEqual(self.errors, [])

    def test_translation_status_restores_project_checkpoint_after_restart(self):
        self.load_workspace()
        project = self.page.evaluate("curTask")
        task = self.page.request.get(self.url + "api/task?id=" + project).json()["task"]
        out = Path(task["base_dir"]) / "文献库"
        out.mkdir(parents=True, exist_ok=True)
        source = write_text_pdf(out / "status-source.pdf", ["Research source"])
        paper = self.page.evaluate("""async args => (await api('/api/projects/'+args.project+'/papers/ingest', {path:args.path})).paper""",
                                   {"project": project, "path": str(source)})
        papertran._save_state(out, paper["id"], 3, 7, False)
        state = self.page.request.get(self.url + f"api/trans/status?id={paper['id']}&task={project}").json()
        self.assertEqual(state["state"], "interrupted")
        self.assertEqual(state["pages_done"], 3)
        self.assertEqual(state["total_pages"], 7)
        other = self.page.evaluate("async () => (await api('/api/task/create',{title:'Other translation project'})).task_id")
        refused = self.page.request.get(self.url + f"api/trans/status?id={paper['id']}&task={other}")
        self.assertEqual(refused.status, 400)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
