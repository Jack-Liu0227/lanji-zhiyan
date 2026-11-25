from pathlib import Path
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import evoflow_sdk
import project_bindings
import project_library
import project_resources
import project_task_execution
import project_tasks
import research_reading
import service_settings
import workflow
import workflow_context
import workflow_execution
import workflow_layout
import workflow_search
import workflow_tasks
from tests._pdf_fixture import write_text_pdf


class SelfOrchestrationImprovementsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("SelfOrch", base_dir=str(Path(self.tmp.name) / "project"))
        workflow.make_project_tree(Path(self.tmp.name) / "project", self.db.summary(self.project)["task"])
        workflow_layout.ensure_schema(self.db)
        self.binding = project_bindings.register(self.db, self.project, "assistant", "builtin-steward")

    def test_workflow_review_mode_setting_defaults_and_validates(self):
        self.assertEqual(project_bindings.workflow_review_mode(self.db), "human_review")
        self.assertEqual(project_bindings.set_workflow_review_mode(self.db, "auto"), "auto")
        self.assertEqual(project_bindings.workflow_review_mode(self.db), "auto")
        self.assertEqual(service_settings.read(self.db, "security").get("workflow_review_mode"), "auto")
        self.assertEqual(project_bindings.set_workflow_review_mode(self.db, "human_review"), "human_review")
        with self.assertRaises(ValueError):
            project_bindings.set_workflow_review_mode(self.db, "invalid_mode")

    def test_auto_review_mode_runs_multi_node_dag_and_enforces_contracts_without_manual_pause(self):
        project_bindings.set_workflow_review_mode(self.db, "auto")
        definition = {
            "nodes": [
                {
                    "id": "S1", "step": "search", "label": "Retrieve",
                    "tools": ["paper_search"],
                    "review_required": True,
                    "output_contract": {"required_files": ["文献库/candidate_papers.csv"]},
                },
                {
                    "id": "N1", "step": "notes", "label": "Evidence",
                    "tools": ["paper_abstracts"],
                    "review_required": True,
                    "output_contract": {"required_files": ["笔记/evidence_analysis.md"]},
                },
                {
                    "id": "R1", "step": "review", "label": "Final Review",
                    "tools": ["workflow_context_read"],
                    "review_required": True,
                    "manual_review_required": True,
                    "claim_audit": True,
                    "output_contract": {
                        "required_files": ["最终产物/literature_review.md"],
                        "file_rules": {"最终产物/literature_review.md": {"min_chars": 40}},
                    },
                },
            ],
            "edges": [
                {"source": "S1", "target": "N1", "artifact": "文献库/candidate_papers.csv"},
                {"source": "N1", "target": "R1", "artifact": "笔记/evidence_analysis.md"},
            ],
        }
        saved = workflow._tool_impl("workflow_create", self.db, self.project, {
            "name": "Cross-folder review workflow", **definition,
        })
        layout = saved["layout"]
        search_tools = layout["nodes"][0]["tools"]
        self.assertIn("project_artifact_write", search_tools)
        self.assertIn("workflow_context_read", search_tools)
        self.assertIn("paper_download_batch", search_tools)

        flow = workflow_execution.create(self.db, self.project, "Review topic")
        self.assertEqual(flow["settings"]["review_mode"], "auto")

        prompts = {}

        def model(db, pid, prompt, **kwargs):
            task = project_tasks.get(db, db.project_task_context.parent_task_id)
            nid = task["workflow_node_id"]
            prompts[nid] = kwargs["system"]
            if nid == "S1":
                res = workflow._tool_impl("project_artifact_write", db, pid, {
                    "path": "文献库/candidate_papers.csv",
                    "content": "title,doi\nInterface Study,10.test/a\n",
                })
            elif nid == "N1":
                res = workflow._tool_impl("project_artifact_write", db, pid, {
                    "path": "笔记/evidence_analysis.md",
                    "content": "# Evidence Analysis\nQuoted 4.2 V interface test.",
                })
            else:
                res = workflow._tool_impl("project_artifact_write", db, pid, {
                    "path": "最终产物/literature_review.md",
                    "content": "# Literature Review\nThis review synthesizes the 4.2 V interface evidence cleanly.",
                })
            return {"reply": "Done " + nid, "output_assets": res["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=model), patch.object(workflow_execution, "kick"):
            flow = workflow_execution.advance(self.db, flow["id"])

        self.assertEqual(flow["status"], "succeeded", flow.get("error"))
        self.assertIn("本阶段专业指引（search）", prompts["S1"])
        self.assertIn("严禁先写入“校验中/占位/待补充”", prompts["S1"])
        self.assertIn("paper_download_batch", prompts["S1"])

        catalog_lit = project_resources.catalog(self.db, self.project, "literature", review_status="all")["items"]
        catalog_note = project_resources.catalog(self.db, self.project, "note", review_status="all")["items"]
        catalog_out = project_resources.catalog(self.db, self.project, "output", review_status="delivered")["items"]
        self.assertTrue(any(item["relative"] == "文献库/candidate_papers.csv" for item in catalog_lit))
        self.assertTrue(any(item["relative"] == "笔记/evidence_analysis.md" for item in catalog_note))
        self.assertTrue(any(item["relative"] == "最终产物/literature_review.md" for item in catalog_out))

    def test_auto_review_mode_still_pauses_manual_execution_and_respects_human_default(self):
        layout = workflow_layout.save_layout(self.db, {
            "name": "Human default & manual upload",
            "nodes": [
                {"id": "A", "step": "task", "label": "Agent step", "assistant_id": "builtin-steward",
                 "review_required": True, "output_contract": {"required_files": ["笔记/a.md"]}},
            ],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        flow = workflow_execution.create(self.db, self.project, "Default human mode")
        with patch.object(workflow, "run_agent", side_effect=lambda db, pid, *a, **kw: {
            "reply": "ok",
            "output_assets": workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "笔记/a.md", "content": "# Draft A\nContent.",
            })["output_assets"],
        }), patch.object(workflow_execution, "kick"):
            flow = workflow_execution.advance(self.db, flow["id"])
        self.assertEqual(flow["status"], "awaiting_review")

        with patch.object(workflow_execution, "kick"):
            flow = workflow_tasks.set_mode(self.db, flow["id"], "", review_mode="auto")
            flow = workflow_execution.advance(self.db, flow["id"])
        self.assertEqual(flow["status"], "succeeded", flow.get("error"))

        project_bindings.set_workflow_review_mode(self.db, "auto")
        task = project_tasks.create(self.db, {
            "project_id": self.project, "title": "Manual upload", "goal": "Upload data",
            "executor_binding_id": self.binding["id"], "review_required": True,
            "scope": {"manual_execution": True, "workflow_review_mode": "auto"},
            "output_contract": {"required_files": ["其余/manual.md"]},
        }, authority=project_tasks.CommandContext.system(self.project))
        run = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        project_tasks.claim(self.db, run["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        res = workflow._tool_impl("project_artifact_write", self.db, self.project, {
            "path": "其余/manual.md", "content": "# Manual content",
        })
        completed = project_tasks.complete(self.db, run["id"], {
            "reply": "Uploaded",
            "output_snapshot": {"files": [{"path": res["absolute_path"], "relative": res["path"], "name": res["name"], "content_hash": res["content_hash"]}]},
            "checkpoint": {},
        })
        self.assertEqual(completed["status"], "awaiting_review")

    def test_edge_artifact_validation_and_evoflow_preserve_relative_paths(self):
        for bad in ("../escape.md", "/abs.md", "C:\\win.md", "dir/"):
            with self.assertRaises(ValueError):
                workflow_layout.validate_definition({
                    "nodes": [{"id": "A", "step": "task"}, {"id": "B", "step": "task"}],
                    "edges": [{"source": "A", "target": "B", "artifact": bad}],
                })
        dag = evoflow_sdk.compile_plan_to_dag(
            "目录协同工作流",
            steps=[
                {"id": "s1", "step": "search", "tools": ["paper_search"],
                 "output_contract": {"required_files": ["文献库/pool.csv"]}},
                {"id": "s2", "step": "notes", "depends_on": ["s1"], "edge_artifact": "文献库/pool.csv",
                 "output_contract": {"required_files": ["笔记/notes.md"]}},
            ],
        )
        self.assertEqual(dag["edges"][0]["artifact"], "文献库/pool.csv")
        self.assertIn("project_artifact_write", dag["nodes"][0]["tools"])
        self.assertIn("paper_download_batch", dag["nodes"][0]["tools"])

    def test_batch_download_retries_429_and_context_manifest_and_abstracts_limit(self):
        r1 = self.db.cache_result(self.project, {"title": "OA One", "pdf_url": "https://example.test/one.pdf", "doi": "10.test/1"})
        r2 = self.db.cache_result(self.project, {"title": "No OA", "pdf_url": "", "doi": ""})
        pdf_bytes = Path(write_text_pdf(Path(self.tmp.name) / "tmp.pdf", ["Solid electrolyte test result page 1"])).read_bytes()
        limit_err = urllib.error.HTTPError("https://example.test/one.pdf", 429, "Too Many Requests", {"Retry-After": "0.01"}, None)
        with patch.object(workflow_search, "open_url", side_effect=[limit_err, io.BytesIO(pdf_bytes)]):
            out = workflow._tool_impl("paper_download_batch", self.db, self.project, {"result_ids": [r1, r2]})
        self.assertEqual(out["summary"]["downloaded"], 1)
        self.assertEqual(out["summary"]["failed"], 1)
        self.assertTrue(out["downloaded"][0]["paper_id"])

        for idx in range(12):
            self.db.cache_result(self.project, {"title": f"Extra {idx}", "abstract": f"Abstract {idx}"})
        abs_page = research_reading.abstracts(self.db, self.project, offset=0, limit=15)
        self.assertGreaterEqual(len(abs_page["papers"]), 12)

        prompt_json = json.loads(workflow_context.prompt({
            "goal": "big",
            "accepted_upstream": [{
                "node_id": "N1",
                "outputs": [{"relative": "笔记/evidence.md", "name": "evidence.md", "size": 50000,
                             "excerpt": "A" * 10000,
                             "read_with": {"tool": "workflow_context_read", "section": "artifact", "node_id": "N1", "artifact": "笔记/evidence.md"}}],
            }],
        }, "run-1", "N2", budget=4000))
        self.assertIn("upstream_manifest", prompt_json)
        self.assertEqual(prompt_json["upstream_manifest"][0]["artifact"], "笔记/evidence.md")


try:
    from tests import test_web_runtime as web_runtime
except Exception:
    web_runtime = None


@unittest.skipUnless(web_runtime and web_runtime.sync_playwright and web_runtime.EDGE, "Requires Playwright and Microsoft Edge")
class SelfOrchestrationSettingsUITest(unittest.TestCase):
    setUpClass = classmethod(web_runtime.WebRuntimeTest.setUpClass.__func__)
    setUp = web_runtime.WebRuntimeTest.setUp
    load_page = web_runtime.WebRuntimeTest.load_page

    def test_settings_perm_toggles_workflow_review_mode_via_security_api(self):
        self.load_page("?pw=1")
        sec0 = self.page.request.get(self.url + "api/security").json()
        self.assertEqual(sec0.get("workflow_review_mode"), "human_review")
        self.page.evaluate("() => { openTab('settings'); setSec = 'perm'; render(); }")
        self.page.locator('input[name="wfreviewmode"][value="human_review"]').wait_for(state="visible")
        self.assertTrue(self.page.locator('input[name="wfreviewmode"][value="human_review"]').is_checked())
        self.assertFalse(self.page.locator('input[name="wfreviewmode"][value="auto"]').is_checked())
        self.page.locator('input[name="wfreviewmode"][value="auto"]').click()
        self.page.wait_for_function("() => (S.sec || {}).workflow_review_mode === 'auto'")
        sec1 = self.page.request.get(self.url + "api/security").json()
        self.assertEqual(sec1.get("workflow_review_mode"), "auto")
        bad = self.page.request.post(self.url + "api/security", data={"workflow_review_mode": "bad"})
        self.assertEqual(bad.status, 400)
        self.page.locator('input[name="wfreviewmode"][value="human_review"]').click()
        self.page.wait_for_function("() => (S.sec || {}).workflow_review_mode === 'human_review'")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
