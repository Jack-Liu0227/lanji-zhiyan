"""论文工具箱：公式保护、引文去重、本机导出、Mermaid 纯前端。"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paper_toolbox
import workflow


class PaperToolboxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Paper tools")
        self.db.add_model("Fixture", "http://127.0.0.1:9999/v1", "fixture", "test-only", activate=True)

    def test_catalog_lists_eight_tools_and_mermaid_is_browser_only(self):
        catalog = paper_toolbox.route("GET", "/api/paper-toolbox", {}, {}, self.db)
        ids = [tool["id"] for tool in catalog["tools"]]
        self.assertEqual(ids, ["caption", "image_convert", "translate", "polish",
                               "references", "convert", "pdf_translate", "mermaid"])
        self.assertEqual(next(t for t in catalog["tools"] if t["id"] == "mermaid")["runtime"], "browser")

    def test_translation_restores_formula_and_citation_markers(self):
        text = "离子扩散满足 $D=D_0e^{-E/kT}$，参见 [1] 和 {{C2}}。"
        with patch.object(workflow, "chat_raw",
                          side_effect=lambda profile, messages, **kw: {"content": "Translated " + messages[-1]["content"]}) as model:
            result = paper_toolbox.transform(self.db, self.pid, {"tool": "translate", "text": text})
        self.assertIn("$D=D_0e^{-E/kT}$", result["content"])
        self.assertIn("{{C2}}", result["content"])
        self.assertNotIn("⟦KEEP_", result["content"])
        self.assertNotIn("$D=", model.call_args.args[1][-1]["content"])
        with patch.object(workflow, "chat_raw", return_value={"content": "Dropped all protected markers"}):
            with self.assertRaises(ValueError):
                paper_toolbox.transform(self.db, self.pid, {"tool": "translate", "text": text})

    def test_references_dedup_and_docx_export_touch_real_files(self):
        bib = ("@article{one,\n title={Ion transport},\n author={Li},\n year={2026},\n doi={10.1000/example}\n}\n"
               "@article{two,\n title={Ion transport},\n author={Li},\n year={2026},\n doi={10.1000/example}\n}")
        result = paper_toolbox.transform(self.db, self.pid, {"tool": "references", "text": bib})
        self.assertIn("保留 1 条", result["content"])
        exported = paper_toolbox.transform(self.db, self.pid,
                                          {"tool": "convert", "format": "docx",
                                           "text": "# Research report\n\nEvidence and limitations."})
        self.assertTrue(Path(exported["path"]).is_file())
        self.assertEqual(Path(exported["path"]).suffix, ".docx")
        # quiet_steps 生效：工具箱导出不记账到十步进度
        self.assertFalse(any(step["stage"] == "convert" for step in self.db.steps(self.pid)))

    def test_style_presets_and_edits_reach_the_model(self):
        with patch.object(workflow, "chat_raw",
                          side_effect=lambda profile, messages, **kw: {"content": messages[-1]["content"]}) as model:
            paper_toolbox.transform(self.db, self.pid,
                                    {"tool": "translate", "text": "带有 $x$ 和 [2] 的原文", "prompt_preset": "translate_literal"})
            self.assertIn("逐句核对", model.call_args.args[1][0]["content"])
            paper_toolbox.transform(self.db, self.pid,
                                    {"tool": "translate", "direction": "en_zh", "text": "Source $x$ [2]", "prompt": "CUSTOM_STYLE_ONE"})
            instruction = model.call_args.args[1][0]["content"]
            self.assertIn("CUSTOM_STYLE_ONE", instruction)
            self.assertIn("学术中文", instruction)
        for spec in ({"tool": "polish", "prompt_preset": "translate_literal"},
                     {"tool": "translate", "prompt": "x" * 20001},
                     {"tool": "references", "prompt": "Ignored style"}):
            with self.subTest(tool=spec["tool"]), self.assertRaises(ValueError):
                paper_toolbox.validate({"text": "Source", **spec})

    def test_mermaid_never_starts_a_model_job(self):
        with patch("agent_jobs.start") as jobs, patch.object(workflow, "chat_raw") as model:
            with self.assertRaisesRegex(ValueError, "本地预览和导出"):
                paper_toolbox.start(self.db, {"tool": "mermaid", "text": "flowchart LR\n A --> B"})
            jobs.assert_not_called()
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
