import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_delegate import WorkflowDB, workflow
from tests._pdf_fixture import write_text_pdf


class ResearchReadingTest(unittest.TestCase):
    def setUp(self):
        # Keep the test directory free of asynchronous index writers.
        indexer = patch("semantic.warm_async")
        indexer.start()
        self.addCleanup(indexer.stop)

    def test_abstract_first_then_verified_evidence_is_shared(self):
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / "r.sqlite") as db:
            project = db.create_task("NFM", "Find a fair comparison", "NFM")
            pages = [
                "Abstract\nA controlled study of sodium cathodes reports improved retention under fixed testing conditions."
                "\n1 Introduction\nUNRELATED_BODY",
                "Methods\nCu dopant was compared with a matched control at 4.2 V.",
            ]
            source = write_text_pdf(db.base_dir(project) / "fixture.pdf", pages)
            with patch("pdfio.page_texts", return_value=pages):
                paper_id = db.ingest_pdf(project, source)["id"]
            with patch.object(workflow, "chat_raw") as model:
                screening = workflow._tool_impl("paper_abstracts", db, project, {})
                model.assert_not_called()
            abstract = screening["papers"][0]
            self.assertEqual(abstract["status"], "available")
            self.assertNotIn("UNRELATED_BODY", abstract["abstract"])
            self.assertNotIn("4.2", abstract["abstract"])
            matched = workflow._tool_impl("paper_focus", db, project, {
                "question": "Was the comparison matched?", "paper_ids": [paper_id],
                "keywords": ["matched control", "Cu dopant"],
            })
            self.assertEqual(matched["matched_pages"], 1)
            hit = matched["matches"][0]
            self.assertEqual(hit["page_no"], 2)
            accepted = workflow._tool_impl("paper_evidence_record", db, project, {
                "paper_id": paper_id,
                "items": [{"page_no": 2, "field": "control voltage", "value": "4.2 V", "excerpt": hit["excerpt"]}],
            })["accepted"]
            evidence_id = accepted[0]["id"]
            with patch.object(workflow, "chat_json", return_value={"verdict": "Accept with Revisions"}) as judge:
                idea = workflow._tool_impl("idea_review", db, project, {
                    "idea": "Compare Cu dopant", "evidence_ids": [evidence_id],
                })
            self.assertIn(evidence_id, judge.call_args.args[1][-1]["content"])
            self.assertIn(evidence_id, idea["markdown"])
            reused = workflow._tool_impl("paper_evidence_get", db, project, {"ids": [evidence_id]})
            self.assertEqual(reused["evidence"][0]["page_no"], 2)
            workflow.map_write(db, project, {"nodes": [
                {"id": "methods", "type": "section", "label": "Methods"},
                {"id": "source", "type": "evidence", "label": "Matched control", "ref": evidence_id},
            ], "edges": [{"from": "methods", "to": "source", "label": "支撑"}]})
            source_draft = workflow._tool_impl("draft_write", db, project, {})["markdown"]
            self.assertIn("fixture.pdf p.2", source_draft)
            self.assertIn(evidence_id, source_draft)
            self.assertIn("4.2 V", source_draft)
            other = db.create_task("Other", "Scope", "")
            with self.assertRaises(ValueError):
                workflow._tool_impl("paper_evidence_get", db, other, {"ids": [evidence_id]})

    def test_absent_abstract_keeps_paper_pending(self):
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / "r.sqlite") as db:
            project = db.create_task("Screen", "Collect papers", "")
            source = write_text_pdf(Path(temporary) / "no-abstract.pdf", ["1 Introduction\nMain text only"])
            paper_id = db.ingest_pdf(project, source)["id"]
            item = workflow._tool_impl("paper_abstracts", db, project, {})["papers"][0]
            self.assertEqual((item["paper_id"], item["status"]), (paper_id, "missing"))
            self.assertNotIn("abstract", item)
