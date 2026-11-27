"""Document rules stay whole while only source data follows the model budget."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import capabilities
import workflow
import workflow_context
import workflow_converters
import workflow_documents as documents
from workflow_compaction import WorkflowContext
from tests import test_workflow_full_sources as fixtures


class WorkflowDocumentSourcesTest(unittest.TestCase):
    setUp = fixtures.WorkflowFullSourcesTest.setUp

    def documents(self):
        outline = "## Framework\n" + "Full outline argument. " * 2000 + "OUTLINE_TAIL_MUST_SURVIVE"
        manuscript = "# Manuscript\n" + "Complete source evidence. " * 10000 + "MANUSCRIPT_TAIL_MUST_SURVIVE"
        for kind, text in (("outline", outline), ("write", manuscript)):
            path = documents.doc_path(self.db, self.project, kind)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return outline, manuscript

    def test_long_map_fields_and_reference_paths_survive_saving_and_reading(self):
        label = "Long source label " * 30 + "LABEL_TAIL"
        detail = "Full source explanation. " * 1000 + "DETAIL_TAIL"
        reference = "folder/" * 60 + "EXACT_REFERENCE_TAIL.pdf"
        edge_label = "Complete relationship " * 20 + "EDGE_LABEL_TAIL"
        result = documents.map_write(self.db, self.project, {
            "nodes": [{"id": "s", "type": "section", "label": "Framework"},
                      {"id": "p", "type": "paper", "label": label, "detail": detail, "ref": reference}],
            "edges": [{"from": "s", "to": "p", "label": edge_label}],
        })

        saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        restored = documents.map_read(self.db, self.project)
        self.assertEqual(saved["nodes"][1]["label"], label)
        self.assertEqual(saved["nodes"][1]["detail"], detail)
        self.assertEqual(saved["nodes"][1]["ref"], reference)
        self.assertEqual(saved["edges"][0]["label"], edge_label)
        self.assertEqual(restored["nodes"][1]["ref"], reference)

    def test_each_document_system_supplies_complete_sources_and_skill_instructions(self):
        outline, manuscript = self.documents()
        brief = "Full evidence briefing. " * 1000 + "BRIEF_TAIL_MUST_SURVIVE"
        citation = "Complete citation evidence. " * 1000 + "CITATION_TAIL_MUST_SURVIVE"
        skills = []
        skill_text = "Mandatory full skill instruction. " * 200 + "SKILL_INSTRUCTION_TAIL"
        for name in ("paper-reading-reports", "paper-spine", "paper-writer", "research-topic-selection",
                     "idea-evaluator", "manuscript-writing", "pre-submission-reviewer"):
            path = Path(self.temp.name) / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(skill_text, encoding="utf-8")
            skills.append({"id": name, "path": str(path)})
        citations = {"path": str(self.root / "citations.md"), "count": 1, "markdown": citation}
        figures = [{"name": "figure-" + str(index), "path": str(self.root / (str(index) + ".png")),
                    "ext": "png", "size": 12} for index in range(65)]
        with patch.object(capabilities, "list_skills", return_value=skills), \
                patch.object(capabilities, "enabled", return_value=True), \
                patch.object(workflow_converters, "cite_sync", return_value=citations), \
                patch.object(workflow_converters, "fig_list", return_value={"files": figures}):
            prompts = {"outline": documents.outline_system(self.db, self.project),
                       "map": documents.map_system(self.db, self.project),
                       "write": documents.write_system(self.db, self.project),
                       "evidence": documents.evidence_system(self.db, self.project),
                       "critic": documents.critic_system(self.db, self.project, brief),
                       "review": documents.review_system(self.db, self.project)}

        for name, prompt in prompts.items():
            instructions, _, payload = prompt.rpartition(workflow_context.DOCUMENT_SOURCES_TAG)
            value = json.loads(payload)
            self.assertIn("%%MEMORY%%", instructions)
            self.assertNotIn("%%MEMORY%%", payload)
            if name in ("outline", "map", "write", "evidence", "critic"):
                self.assertEqual(value["data"]["outline"]["markdown"], outline)
            if name in ("write", "critic", "review"):
                self.assertEqual(value["data"]["manuscript"]["markdown"], manuscript)
            if name in ("map", "write", "review"):
                self.assertIn(skill_text, instructions)
        writing = json.loads(prompts["write"].rpartition(workflow_context.DOCUMENT_SOURCES_TAG)[2])
        self.assertEqual(writing["data"]["figures"], figures)
        self.assertEqual(writing["data"]["citations"]["markdown"], citation)
        critique = json.loads(prompts["critic"].rpartition(workflow_context.DOCUMENT_SOURCES_TAG)[2])
        self.assertEqual(critique["data"]["brief"], brief)

    def test_model_requests_keep_full_sources_or_compact_only_data(self):
        _, manuscript = self.documents()
        with patch.object(documents, "skill_text", return_value="Do not invent source evidence."):
            system = documents.review_system(self.db, self.project)
        memory = 'User memory with "quotes"\nand a second line.'
        system = system.replace("%%MEMORY%%", memory)
        original_rules, _, original_json = system.rpartition(workflow_context.DOCUMENT_SOURCES_TAG)
        original = json.loads(original_json)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": "Review the complete manuscript."}]
        profile = {**self.db.active_model(), "context_window": 1000000}
        controller = WorkflowContext(self.db, save_checkpoint=lambda **values: None,
                                     summarize=lambda *args: "Earlier source context is retained.")
        large = controller.prepare(profile, messages, [], 8000)
        self.assertIn(system, [message["content"] for message in large.messages])
        self.assertIn("MANUSCRIPT_TAIL_MUST_SURVIVE", large.messages[0]["content"])

        small = controller.prepare({**profile, "context_window": 65536}, messages, [], 8000)
        supplied = next(message["content"] for message in small.messages
                        if workflow_context.DOCUMENT_SOURCES_TAG in message["content"])
        rules, _, payload = supplied.rpartition(workflow_context.DOCUMENT_SOURCES_TAG)
        bounded = json.loads(payload)
        self.assertEqual(rules, original_rules)
        self.assertIn(memory, rules)
        self.assertEqual(bounded["read_with"], original["read_with"])
        self.assertEqual(bounded["data"]["manuscript"]["path"], original["data"]["manuscript"]["path"])
        self.assertLess(len(bounded["data"]["manuscript"]["markdown"]), len(manuscript))
        self.assertEqual(bounded["full_context"]["section"], "agent")
        self.assertTrue(small.budget.fits)
        self.assertEqual(messages[0]["content"], system)

        supplied_requests = []
        self.db.workflow_end_to_end = True

        def model(candidate, request, **kwargs):
            supplied_requests.append(request)
            return {"content": "The complete manuscript is available for review."}

        with patch.object(workflow, "chat_raw", side_effect=model):
            result = workflow.run_agent(self.db, self.project, "Review the complete manuscript.",
                profile=profile, system=system, tools=[], publish=False, max_steps=2)
        self.assertEqual(result["reply"], "The complete manuscript is available for review.")
        request_source = next(message["content"] for message in supplied_requests[0]
                              if workflow_context.DOCUMENT_SOURCES_TAG in message.get("content", ""))
        actual, _ = json.JSONDecoder().raw_decode(request_source.rpartition(workflow_context.DOCUMENT_SOURCES_TAG)[2])
        self.assertEqual(actual["data"]["manuscript"]["markdown"], manuscript)
        self.assertTrue(request_source.startswith(system))

    def test_document_audit_uses_evidence_after_old_note_and_character_limits(self):
        notes = [{"markdown": "Ordinary source text."} for _ in range(80)]
        notes.append({"markdown": "Full source explanation. " * 1000 + "Measured strength was 17.25 MPa."})

        with patch.object(self.db, "notes", return_value=notes):
            saved = documents.doc_write(self.db, self.project, "review", "Measured strength was 17.25 MPa.")

        self.assertEqual(saved["markdown"], "Measured strength was 17.25 MPa.\n")
        self.assertEqual(Path(saved["path"]).read_text(encoding="utf-8"), "Measured strength was 17.25 MPa.\n")


if __name__ == "__main__":
    unittest.main()
