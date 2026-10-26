from unittest.mock import patch
import json

import unittest
from tests import test_prompt_templates as fixtures
import workflow
import prompt_templates
import project_notes
import asset_service
import semantic
import workflow_documents


class PromptRuntimeTest(unittest.TestCase):
    setUp = fixtures.PromptTemplatesTest.setUp
    template = fixtures.PromptTemplatesTest.template
    def test_selected_template_is_user_instruction_and_run_records_revision(self):
        saved = self.template("ONLY_SELECTED_TEMPLATE {{focus}}")
        captured = []
        def model(profile, messages, **kwargs):
            captured.extend(messages)
            return {"content": "Done", "tool_calls": []}
        with patch.object(workflow, "chat_raw", side_effect=model):
            out = workflow.run_agent(self.db, self.pid, "Review", profile={"permission": "read_only"},
                template_ref={"template_id": saved["template_id"], "variables": {"focus": "controls"}})
        self.assertIn("ONLY_SELECTED_TEMPLATE controls", captured[-1]["content"])
        self.assertEqual(captured[-1]["role"], "user")
        self.assertFalse(any("ONLY_SELECTED_TEMPLATE" in m["content"] for m in captured if m["role"] == "system"))
        node = self.db.db.execute("SELECT * FROM workflow_nodes WHERE run_id=?", (out["run_id"],)).fetchone()
        self.assertEqual(json.loads(node["checkpoint"])["template_snapshot"]["template_id"], saved["template_id"])
        self.assertNotIn(saved["asset_id"], json.loads(node["input_assets"]))
        self.assertEqual(out["output_assets"], [])

    def test_default_binding_and_tool_render_are_available_in_read_only(self):
        saved = self.template("Use me")
        prompt_templates.save_binding(self.db, self.pid, {"template_id": saved["template_id"]})
        captured = []
        def model(profile, messages, **kwargs):
            captured.append(kwargs["tools"])
            return {"content": "Done", "tool_calls": []}
        with patch.object(workflow, "chat_raw", side_effect=model):
            out = workflow.run_agent(self.db, self.pid, "task", profile={"permission": "read_only"})
        self.assertEqual(out["template_snapshot"]["template_id"], saved["template_id"])
        names = {x["function"]["name"] for x in captured[0]}
        self.assertIn("prompt_template_render", names)
        self.assertNotIn("project_note_write", names)

    def test_research_sources_exclude_templates(self):
        saved = self.template("UNRELATED_INSTRUCTION " * 30)
        evidence = project_notes.write(self.db, self.pid, "", "RESEARCH_EVIDENCE " * 30, metadata={"category_id": "literature"})
        self.assertNotIn(saved["asset_id"], {a["id"] for a in asset_service.research_assets(self.db, self.pid)})
        self.assertIn(evidence["asset_id"], {a["id"] for a in asset_service.research_assets(self.db, self.pid)})
        self.assertFalse(any("UNRELATED_INSTRUCTION" in str(s) for s in semantic._sources(self.db.db)))
        self.assertFalse(workflow._knowledge_query_literal(self.db, "UNRELATED_INSTRUCTION", [(self.pid, "Test")], True)["hits"])
        self.assertNotIn(saved["path"].split("/", 1)[1], {n["name"] for n in workflow_documents.map_sources(self.db, self.pid)["notes"]})

    def test_frozen_snapshot_survives_template_change(self):
        saved = self.template("Original instruction")
        self.db.template_snapshot = prompt_templates.render(self.db, self.pid, saved["template_id"])
        project_notes.write(self.db, self.pid, saved["path"], "Changed instruction")
        messages = []
        def model(profile, conversation, **kwargs):
            messages.extend(conversation)
            return {"content": "Done", "tool_calls": []}
        with patch.object(workflow, "chat_raw", side_effect=model):
            workflow.run_agent(self.db, self.pid, "task", profile={"permission": "read_only"})
        self.assertIn("Original instruction", messages[-1]["content"])
        self.assertNotIn("Changed instruction", messages[-1]["content"])

    def test_queued_team_template_and_retry_keep_original_revision(self):
        import team
        saved = self.template("Original team requirement")
        member = self.db.team_member_add(self.pid, "Reviewer", step="review")
        prompt_templates.save_binding(self.db, self.pid, {"scope":"member", "scope_id":member["id"],
            "template_id":saved["template_id"], "follow_latest":True})
        with patch.object(team, "DB_PATH", self.db.path):
            row = team.assign(self.pid, member["id"], "Review", db=self.db)
            checkpoint = json.loads(self.db.team_task(row["id"])["checkpoint"])
            snapshot = checkpoint["queued_template_snapshot"]
            self.assertEqual(snapshot["rendered"], "Original team requirement")
            project_notes.write(self.db, self.pid, saved["path"], "Changed after queue")
            self.db.team_task_mark(row["id"], "failed", checkpoint=checkpoint)
            team.retry(row["id"])
            retried = json.loads(self.db.team_task(row["id"])["checkpoint"])
            self.assertEqual(retried["queued_template_snapshot"]["revision_id"], snapshot["revision_id"])

    def test_frozen_empty_selection_does_not_pick_up_new_binding(self):
        saved = self.template("Added after queue")
        self.db.template_resolved = True
        self.db.template_snapshot = None
        prompt_templates.save_binding(self.db, self.pid, {"template_id":saved["template_id"]})
        with patch.object(workflow, "chat_raw", return_value={"content":"Done", "tool_calls":[]}):
            result = workflow.run_agent(self.db, self.pid, "task", profile={"permission":"read_only"})
        self.assertIsNone(result["template_snapshot"])

    def test_external_template_change_is_not_exposed_as_stale_evidence(self):
        import note_catalog
        import space_service
        saved = project_notes.write(self.db, self.pid, "", "EXTERNAL_CHANGE_MARKER", metadata={"category_id":"literature"})
        path = self.root / saved["path"]
        path.write_text(note_catalog.combine({"note_type":"prompt_template","template_id":"external-template"}, "EXTERNAL_CHANGE_MARKER"), encoding="utf-8")
        self.assertFalse(any('EXTERNAL_CHANGE_MARKER' in str(s) for s in semantic._sources(self.db.db)))
        self.assertFalse(workflow._knowledge_query_literal(self.db, 'EXTERNAL_CHANGE_MARKER', [(self.pid,'Test')], True)['hits'])
        self.assertEqual(space_service.search_project_space(self.db, self.pid, 'EXTERNAL_CHANGE_MARKER')['total'], 0)
