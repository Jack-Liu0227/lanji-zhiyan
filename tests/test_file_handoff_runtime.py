"""Exercise the real file picker, save barrier and node run over HTTP in Edge."""
from pathlib import Path
import unittest
from tests.test_web_runtime import workspace_section

from tests import test_web_runtime as runtime
from tests import test_connected_research_runtime as connected
import workflow
import workflow_layout


@unittest.skipUnless(runtime.sync_playwright and runtime.EDGE, "Requires Playwright and Edge")
class FileHandoffRuntimeTest(unittest.TestCase):
    setUpClass = classmethod(runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = runtime.WebRuntimeTest.setUp
    load_page = runtime.WebRuntimeTest.load_page
    use_model = connected.ConnectedResearchRuntimeTest.use_model
    poll_json = connected.ConnectedResearchRuntimeTest.poll_json

    def seed(self):
        with workflow.WorkflowDB(self.database_path) as db:
            workflow_layout.ensure_schema(db)
            self.pid = db.create_task("File handoff", base_dir=str(self.temporary_root / self._testMethodName))
            db.assistant_save("handoff-writer", name="Workflow Writer", enabled=1)
            db.assistant_save("handoff-outside", name="OUTSIDE_ASSISTANT", enabled=1)
            definition = workflow_layout._linear(["outline", "write"])
            for n in definition["nodes"]:
                n.update(assistant_id="handoff-writer", tools=["doc_write"], prompt="BROWSER_" + n["step"].upper())
            self.layout = workflow_layout.save_layout(db, {"name": "File flow", **definition})["layout"]
            workflow_layout.select_layout(db, {"task_id": self.pid, "layout_id": self.layout["id"]})
            root = db.base_dir(self.pid)
            root.mkdir(parents=True, exist_ok=True)
            for name in ("one.md", "two.md"):
                (root / name).write_text("# SAVED_" + name, encoding="utf-8")
        self.load_page()
        self.page.evaluate("pid => {curTask=pid; openTab('workflow');}", self.pid)
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="one.md"]').wait_for()

    def choose_first(self):
        self.page.locator(".handoff-dialog [data-target]").first.click()

    def test_context_file_is_used_and_original_editor_and_chat_are_preserved(self):
        self.seed()
        self.page.locator('#pwTab-chat').click()
        self.page.locator('#pwAsk').fill('EXISTING_CHAT_DRAFT')
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator("#flEditText").fill("UNSAVED_ONE")
        self.page.locator('#flList [data-rel="two.md"]').click(button="right")
        self.page.get_by_role("menuitem", name="交给当前工作流…").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.assertIn("two.md", self.page.locator(".handoff-dialog [data-body]").inner_text())
        self.assertNotIn("OUTSIDE_ASSISTANT", self.page.locator(".handoff-dialog").inner_text())
        self.page.locator('[data-view="assistant"]').click()
        self.assertEqual(self.page.locator(".handoff-dialog [data-target]").count(), 1)
        self.choose_first()
        self.assertTrue(self.page.locator("[data-prepare]").is_disabled())
        self.page.locator("[data-handoff-node]").select_option("0")
        self.page.locator("[data-instruction]").fill("MY_FILE_TASK")
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("已准备，尚未执行", exact=True).wait_for()
        items = self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["payload"]["relative"], "two.md")
        self.assertFalse(items[0]["run_id"])
        self.page.locator(".handoff-dialog [data-close]").click()
        self.assertEqual(self.page.locator("#flEditText").input_value(), "UNSAVED_ONE")
        self.assertEqual(self.page.locator("#pwAsk").input_value(), "EXISTING_CHAT_DRAFT")
        self.assertEqual(self.errors, [])

    def test_shortcut_save_failure_keeps_draft_and_does_not_prepare(self):
        self.seed()
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator("#flEditText").fill("DIRTY_INPUT")
        self.page.route("**/api/conv/save", lambda r: r.fulfill(status=400, json={"error": "TEST_SAVE_FAILURE"}))
        self.page.keyboard.press("Alt+Shift+H")
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.choose_first()
        self.page.locator("[data-instruction]").fill("PRESERVED_REQUIREMENT")
        self.page.locator("[data-start]").click()
        self.page.get_by_text("文件尚未保存，交接已停止。", exact=False).wait_for()
        self.assertEqual(self.page.locator("[data-instruction]").input_value(), "PRESERVED_REQUIREMENT")
        self.assertEqual(self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"], [])
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.locator("#flEditText").input_value(), "DIRTY_INPUT")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "flEditText")
        self.assertEqual(self.errors, [])

    def test_changed_workflow_preserves_input_and_requires_reselection(self):
        self.seed()
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.choose_first()
        self.page.locator("[data-instruction]").fill("KEEP_ACROSS_CONFIG_CHANGE")
        with workflow.WorkflowDB(self.database_path) as db:
            data = {**self.layout, "name": "Renamed flow"}
            data["nodes"][0]["label"] = "Renamed node"
            workflow_layout.save_layout(db, data)
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("工作流或执行配置已变化", exact=False).wait_for()
        self.page.locator("[data-refresh]").click()
        self.page.get_by_text("工作流：Renamed flow", exact=False).wait_for()
        self.assertTrue(self.page.locator("[data-start]").is_disabled())
        self.assertEqual(self.page.locator("[data-instruction]").input_value(), "KEEP_ACROSS_CONFIG_CHANGE")
        self.choose_first()
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("已准备，尚未执行", exact=True).wait_for()
        self.assertEqual(self.errors, [])

    def test_real_model_runs_only_selected_node_and_review_controls_delivery(self):
        self.seed()
        with connected.ModelFixture() as model:
            self.use_model(model.url)
            self.page.locator('#flList [data-rel="one.md"]').click()
            self.page.locator("#flHand").click()
            self.page.locator(".handoff-dialog [data-target]").first.wait_for()
            self.choose_first()
            self.page.locator("[data-instruction]").fill("FILE_SPECIFIC_GOAL")
            self.page.locator("[data-start]").click()
            self.page.locator('[data-action="approve"]').wait_for(timeout=30000)
            items = self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"]
            run = items[0]["run"]
            self.assertEqual(run["status"], "awaiting_review", run.get("error"))
            self.assertEqual([n["stage"] for n in run["nodes"]], ["outline"])
            self.assertTrue(self.page.locator("[data-output]").count())
            self.assertIn("FILE_SPECIFIC_GOAL", str(model.requests))
            self.assertIn("file_input", str(model.requests))
            self.page.locator('[data-action="approve"]').click()
            self.poll_json("/api/handoff/detail?id=" + items[0]["id"], lambda d: d["handoff"]["status"] == "succeeded")
            self.assertFalse(any("BROWSER_WRITE" in str(r) for r in model.requests))
        self.assertEqual(self.errors, [])

    def test_pinned_target_refreshes_and_dialog_fits_small_window(self):
        self.seed()
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.choose_first()
        self.page.locator("[data-pin]").click()
        self.page.keyboard.press("Escape")
        self.page.locator("#flHandoffPins button").wait_for()
        self.page.locator("#flHandoffPins button").click()
        self.page.locator("[data-pin]").wait_for()
        self.assertEqual(self.page.locator("[data-pin]").inner_text(), "取消常用")
        self.assertFalse(self.page.locator("[data-prepare]").is_disabled())
        destination = Path(runtime.ROOT) / "output" / "file-handoff-review"
        destination.mkdir(parents=True, exist_ok=True)
        for theme in ("light", "dark"):
            self.page.evaluate("theme=>setTheme(theme)", theme)
            self.page.screenshot(path=str(destination / ("dialog-" + theme + ".png")), animations="disabled")
        self.page.set_viewport_size({"width": 900, "height": 680})
        self.assertTrue(self.page.evaluate("(()=>{const r=document.querySelector('.handoff-dialog').getBoundingClientRect();return r.left>=0 && r.right<=innerWidth && r.top>=0 && r.bottom<=innerHeight;})()"))
        self.page.screenshot(path=str(destination / "dialog-small.png"))
        self.page.keyboard.press("Escape")
        with workflow.WorkflowDB(self.database_path) as db:
            db.assistant_save("handoff-writer", enabled=0)
        self.page.evaluate("FileHandoff.preview({project_id:curTask,relative:flSel})")
        self.page.wait_for_function("document.querySelector('#flHandoffPins').children.length===0")
        self.assertEqual(self.errors, [])

    def test_supplement_picker_links_only_matching_node_task(self):
        self.seed()
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.choose_first()
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("已准备，尚未执行", exact=True).wait_for()
        parent = self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"][0]
        self.page.keyboard.press("Escape")
        self.page.locator('#flList [data-rel="two.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.page.locator(".handoff-dialog [data-target]").nth(1).click()
        self.assertEqual(self.page.locator("[data-parent] option").count(), 1)
        self.choose_first()
        self.page.locator("[data-parent]").select_option(parent["id"])
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("已准备，尚未执行", exact=True).wait_for()
        items = self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"]
        self.assertEqual(items[0]["payload"]["parent_id"], parent["id"])
        self.assertEqual(items[1]["status"], "draft")
        self.assertEqual(self.errors, [])

    def test_late_tree_response_cannot_restore_another_projects_file(self):
        self.seed()
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.locator("#flEditText").wait_for()
        self.page.evaluate("""() => {
          const original=api, project=curTask;
          api=async (...args)=>{
            const result=await original(...args);
            if(args[0].startsWith('/api/tree?id='+project) && !window.releaseOldTree){
              await new Promise(resolve=>window.releaseOldTree=resolve);
            }
            return result;
          };
          window.pendingOldTree=flInitTree();
        }""")
        self.page.wait_for_function("!!window.releaseOldTree")
        with workflow.WorkflowDB(self.database_path) as db:
            other = db.create_task("Other file project", base_dir=str(self.temporary_root / (self._testMethodName + "-other")))
            workflow_layout.select_layout(db, {"task_id": other, "layout_id": self.layout["id"]})
            root = db.base_dir(other)
            root.mkdir(parents=True, exist_ok=True)
            (root / "one.md").write_text("OTHER_PROJECT_FILE", encoding="utf-8")
        self.page.evaluate("async pid=>{await refreshState(false);curTask=pid;openTab('workflow');}", other)
        self.page.wait_for_function("pid=>document.querySelector('#projectWorkspace')?.dataset.projectId===pid", arg=other)
        workspace_section(self.page, 'files')
        row = self.page.locator(f'#flList [data-project-id="{other}"][data-rel="one.md"]')
        row.wait_for()
        self.page.evaluate("async()=>{window.releaseOldTree();await window.pendingOldTree;}")
        self.assertEqual(self.page.evaluate("flSel"), "")
        self.assertTrue(self.page.locator("#flHand").is_disabled())
        self.assertTrue(self.page.locator("#flList [data-rel]").evaluate_all("(rows,pid)=>rows.every(row=>row.dataset.projectId===pid)", other))
        row.click()
        self.page.locator('[data-fl-mode="edit"]').click()
        self.page.wait_for_function("document.querySelector('#flEditText')?.value==='OTHER_PROJECT_FILE'")
        self.page.locator("#flHand").click()
        self.page.locator(".handoff-dialog [data-target]").first.wait_for()
        self.assertIn("Other file project", self.page.locator(".handoff-dialog").inner_text())
        self.assertEqual(self.errors, [])

    def test_legacy_project_teams_and_assistants_are_selectable_in_dropdowns(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            for aid in ("builtin-literature-lead", "builtin-writing-lead", "builtin-searcher"):
                db.assistant_save(aid, enabled=1)
            literature = db.team_add("Existing literature team", "fixed", project_id=self.pid)
            writing = db.team_add("Existing writing team", "fixed", project_id=self.pid)
            db.team_member_add(literature["id"], "Lead", "leader", assistant_id="builtin-literature-lead")
            db.team_member_add(literature["id"], "Existing searcher", "member", assistant_id="builtin-searcher")
            db.team_member_add(writing["id"], "Lead", "leader", assistant_id="builtin-writing-lead")
            unrelated = db.team_add("DO_NOT_SHOW_THIS_TEAM", "custom", project_id=self.pid)
            db.team_member_add(unrelated["id"], "Other", "leader", assistant_id="handoff-outside")
            workflow_layout.select_layout(db, {"task_id": self.pid, "layout_id": "builtin.workflow.research"})
        self.page.evaluate("render()")
        self.page.wait_for_function("ProjectWorkspace.active()")
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator("[data-target-select]:not(:disabled)").wait_for()
        self.page.locator('[data-view="team"]').click()
        team_select = self.page.get_by_role("combobox", name="选择团队", exact=True)
        self.assertEqual(team_select.locator("option").count(), 3)
        self.assertNotIn("DO_NOT_SHOW_THIS_TEAM", team_select.inner_text())
        team_select.select_option(literature["id"])
        self.assertTrue(self.page.locator("[data-prepare]").is_disabled())
        self.page.locator("[data-handoff-node]").select_option(label="论文搜索 · Existing literature team / Lead")
        executor = self.page.get_by_role("combobox", name="执行专家", exact=True)
        self.assertEqual(executor.locator("option").count(), 2)
        searcher = executor.locator("option").filter(has_text="Existing searcher").get_attribute("value")
        executor.select_option(searcher)
        self.page.locator('[data-view="assistant"]').click()
        assistant_select = self.page.get_by_role("combobox", name="选择专家", exact=True)
        self.assertEqual(assistant_select.locator("option").count(), 4)
        assistant_select.select_option("builtin-searcher")
        self.assertIn("论文搜索", self.page.locator("[data-selection]").inner_text())
        self.assertFalse(self.page.locator("[data-prepare]").is_disabled())
        output = Path(runtime.ROOT) / "output" / "file-handoff-review"
        self.page.screenshot(path=str(output / "legacy-team-dropdown.png"), animations="disabled")
        self.page.locator("[data-prepare]").click()
        self.page.get_by_text("已准备，尚未执行", exact=True).wait_for()
        item = self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"][0]
        self.assertEqual(item["payload"]["assistant_id"], "builtin-searcher")
        self.assertEqual(item["payload"]["team_id"], literature["id"])
        self.assertEqual(item["node_id"], "search")
        self.assertEqual(self.errors, [])

    def test_old_leader_repair_recovers_targets_and_keeps_instruction(self):
        self.seed()
        with workflow.WorkflowDB(self.database_path) as db:
            for aid in ("builtin-literature-lead", "builtin-writing-lead", "builtin-searcher"):
                db.assistant_save(aid, enabled=1)
            for name in ("文献抽取", "文件撰写"):
                office = db.team_add(name, "fixed", project_id=self.pid)
                db.team_member_add(office["id"], "智研管家", "leader", assistant_id="")
                if name == "文献抽取":
                    db.team_member_add(office["id"], "文献检索员", "member", assistant_id="builtin-searcher")
            workflow_layout.select_layout(db, {"task_id": self.pid, "layout_id": "builtin.workflow.research"})
        self.page.evaluate("render()")
        self.page.wait_for_function("ProjectWorkspace.active()")
        workspace_section(self.page, 'files')
        self.page.locator('#flList [data-rel="one.md"]').click()
        self.page.locator("#flHand").click()
        self.page.locator("[data-repair]").wait_for()
        self.assertTrue(self.page.locator("[data-target-select]").is_disabled())
        self.page.locator("[data-instruction]").fill("KEEP_REQUIREMENT_DURING_REPAIR")
        self.page.locator("[data-repair]").click()
        self.page.locator("[data-target-select]:not(:disabled)").wait_for()
        self.assertEqual(self.page.locator("[data-instruction]").input_value(), "KEEP_REQUIREMENT_DURING_REPAIR")
        self.page.locator('[data-view="assistant"]').click()
        assistant = self.page.get_by_role("combobox", name="选择专家", exact=True)
        self.assertEqual(assistant.locator("option").count(), 4)
        assistant.select_option("builtin-searcher")
        self.assertFalse(self.page.locator("[data-prepare]").is_disabled())
        self.assertEqual(self.page.request.get(self.url + "api/handoff/history?project_id=" + self.pid).json()["items"], [])
        self.page.screenshot(path=str(Path(runtime.ROOT) / "output/file-handoff-review/old-leader-repaired.png"), animations="disabled")
        self.assertEqual(self.errors, [])
