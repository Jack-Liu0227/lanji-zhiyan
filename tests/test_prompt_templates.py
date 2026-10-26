import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))
import workflow
import note_catalog
import project_notes
import prompt_templates
import asset_service


class PromptTemplatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "db.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.pid = self.db.create_task("Test", "Known question", base_dir=str(self.root))

    def template(self, text="Review {{research_question}} for {{focus}}", **meta):
        return project_notes.write(self.db, self.pid, "", text, title="Review template", metadata={
            "note_type": "prompt_template", "category_id": "prompt_templates", "tags": ["审稿"], **meta})

    def test_discover_render_defaults_missing_and_validation(self):
        saved = self.template()
        tid = saved["template_id"]
        self.assertEqual(len(prompt_templates.search(self.db, self.pid, tags=["审稿"])["templates"]), 1)
        result = prompt_templates.render(self.db, self.pid, tid)
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], ["focus"])
        result = prompt_templates.render(self.db, self.pid, tid, {"focus": "controls"})
        self.assertEqual(result["rendered"], "Review Known question for controls")
        with self.assertRaises(ValueError):
            prompt_templates.render(self.db, self.pid, tid, {"unknown": "bad"})

    def test_revisions_pinned_binding_and_copy_identity(self):
        saved = self.template("First")
        tid = saved["template_id"]
        prompt_templates.save_binding(self.db, self.pid, {"template_id": tid})
        project_notes.write(self.db, self.pid, saved["path"], "Second", expected_hash=saved["content_hash"])
        self.assertEqual(prompt_templates.resolve(self.db, self.pid)["rendered"], "First")
        self.assertEqual(prompt_templates.resolve(self.db, self.pid, {"template_id": tid})["rendered"], "Second")
        copied = prompt_templates.copy_template(self.db, self.pid, saved["path"])
        self.assertNotEqual(tid, copied["template_id"])
        with self.assertRaises(ValueError):
            self.template("duplicate", template_id=tid)

    def test_category_changes_do_not_change_template_and_plain_notes_stay_plain(self):
        saved = self.template("Fixed prompt")
        project_notes.write(self.db, self.pid, saved["path"], saved["content"], metadata={"category_id": "planning"})
        self.assertEqual(prompt_templates.read(self.db, self.pid, saved["template_id"])["body"], "Fixed prompt")
        plain = project_notes.write(self.db, self.pid, "", "plain", metadata={"category_id": "prompt_templates", "tags": ["模板"]})
        self.assertEqual(plain["note_type"], "note")
        self.assertEqual(len(prompt_templates.search(self.db, self.pid)["templates"]), 1)

    def test_disabled_and_cross_project_are_rejected(self):
        saved = self.template("Fixed")
        other = self.root.parent / "other"
        other.mkdir()
        pid2 = self.db.create_task("Other", base_dir=str(other))
        with self.assertRaises(ValueError):
            prompt_templates.read(self.db, pid2, saved["template_id"])
        project_notes.write(self.db, self.pid, saved["path"], saved["content"], metadata={"template_status": "disabled"})
        with self.assertRaises(ValueError):
            prompt_templates.read(self.db, self.pid, saved["template_id"])

    def test_asset_variable_and_read_is_not_an_output(self):
        source = project_notes.write(self.db, self.pid, "", "EVIDENCE", metadata={"category_id": "literature"})
        saved = self.template("Use {{material}}", variables={"material": {"type": "asset_ref"}})
        self.db.asset_run = {"id": "testing", "inputs": [], "outputs": []}
        result = prompt_templates.render(self.db, self.pid, saved["template_id"], {"material": source["asset_id"]})
        self.assertIn("EVIDENCE", result["rendered"])
        self.assertEqual(self.db.asset_run["outputs"], [])
        self.assertEqual(result["input_assets"][0]["asset_id"], source["asset_id"])

    def test_history_records_exact_revision(self):
        saved = self.template("Trace me")
        rendered = prompt_templates.render(self.db, self.pid, saved["template_id"])
        prompt_templates.usage(self.db, rendered, "test-run")
        history = prompt_templates.history(self.db, self.pid, saved["template_id"])
        self.assertEqual(history[0]["input_snapshot"]["revision_id"], rendered["revision_id"])
