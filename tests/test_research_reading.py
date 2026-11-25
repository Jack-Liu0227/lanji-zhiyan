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

    def test_evidence_registration_uses_source_offsets_from_focus(self):
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / "r.sqlite") as db:
            project = db.create_task("Offset evidence", "Register exact source text", "")
            page_text = "Methods\nCells were tested at 4.2 V after cycling."
            source = write_text_pdf(db.base_dir(project) / "offset.pdf", [page_text])
            with patch("pdfio.page_texts", return_value=[page_text]):
                paper_id = db.ingest_pdf(project, source)["id"]
            focused = workflow._tool_impl("paper_focus", db, project, {
                "question": "Which voltage was used?", "paper_ids": [paper_id], "keywords": ["4.2 V"],
            })
            match = focused["matches"][0]
            result = workflow._tool_impl("paper_evidence_record", db, project, {
                "paper_id": paper_id,
                "items": [{"page_no": match["page_no"], "start": match["start"], "end": match["end"],
                           "field": "test voltage", "value": "4.2 V"}],
            })
            self.assertEqual(result["rejected"], [])
            recorded = db.evidence(project)[0]
            expected = " ".join(page_text[match["start"]:match["end"]].split())
            self.assertEqual(recorded["excerpt"], expected)
            self.assertIn("4.2 V", recorded["excerpt"])

    def test_search_record_includes_download_instruction_and_is_not_a_pdf_id(self):
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / 'r.sqlite') as db:
            project = db.create_task('Metadata', 'Read a real source', '')
            result_id = db.cache_result(project, {'title': 'Indexed metadata', 'doi': '10.test/metadata',
                                                  'abstract': 'An abstract with no locally indexed PDF.'})
            item = workflow._tool_impl('paper_abstracts', db, project, {})['papers'][0]
            self.assertEqual(item['result_id'], result_id)
            self.assertEqual(item['fulltext_status'], 'not_indexed')
            self.assertEqual(item['download_with'], {'tool': 'paper_download', 'result_id': result_id})
            with self.assertRaisesRegex(ValueError, 'paper_download'):
                workflow._tool_impl('paper_focus', db, project, {'question': 'Check source',
                    'paper_ids': [result_id], 'keywords': ['abstract']})
            other = db.create_task('Other project', '', '')
            with self.assertRaises(PermissionError):
                workflow._tool_impl('paper_focus', db, other, {'question': 'Check source',
                    'paper_ids': [result_id], 'keywords': ['abstract']})

    def test_selected_abstract_pool_pages_only_requested_results_and_rejects_other_projects(self):
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / 'r.sqlite') as db:
            project = db.create_task('Focused screening', '', '')
            first = db.cache_result(project, {'title': 'Selected first', 'doi': '10.test/first',
                'authors': 'A. Researcher', 'year': '2026', 'source': 'crossref',
                'abstract': 'Visible source abstract for the first selected work.'})
            db.cache_result(project, {'title': 'Unselected history', 'doi': '10.test/history'})
            second = db.cache_result(project, {'title': 'Selected second', 'doi': '10.test/second'})
            page = workflow._tool_impl('paper_abstracts', db, project, {
                'result_ids': [first, second], 'offset': 0, 'limit': 1})
            self.assertEqual([item['title'] for item in page['papers']], ['Selected first'])
            self.assertEqual(page['next_offset'], 1)
            self.assertEqual(page['papers'][0]['authors'], 'A. Researcher')
            self.assertEqual(page['papers'][0]['year'], '2026')
            last = workflow._tool_impl('paper_abstracts', db, project, {
                'result_ids': [first, second], 'offset': page['next_offset'], 'limit': 1})
            self.assertEqual([item['title'] for item in last['papers']], ['Selected second'])
            self.assertIsNone(last['next_offset'])
            other = db.create_task('Other screening project', '', '')
            foreign = db.cache_result(other, {'title': 'Foreign work', 'doi': '10.test/foreign'})
            with self.assertRaisesRegex(ValueError, '当前项目'):
                workflow._tool_impl('paper_abstracts', db, project, {'result_ids': [foreign]})

    def test_linked_search_metadata_reports_existing_pdf_and_focus_instruction(self):
        import project_library
        with tempfile.TemporaryDirectory() as temporary, WorkflowDB(Path(temporary) / 'r.sqlite') as db:
            project = db.create_task('Linked full text', '', '')
            source = write_text_pdf(db.base_dir(project) / 'source.pdf', [
                'Abstract\nAn interface coating reduced resistance under matched test conditions.\n1 Introduction\nBody.'])
            paper = db.ingest_pdf(project, source)
            result_id = db.cache_result(project, {'title': 'Interface coating study', 'doi': '10.test/coating',
                'abstract': 'An interface coating reduced resistance under matched test conditions.'})
            project_library.link_pdf(db, project, result_id, paper['id'])
            result = workflow._tool_impl('paper_abstracts', db, project, {'result_ids': [result_id]})
            item = next(row for row in result['papers'] if row.get('result_id') == result_id)
            self.assertEqual(item['fulltext_status'], 'indexed')
            self.assertEqual(item['paper_id'], paper['id'])
            self.assertEqual(item['read_with'], {'tool': 'paper_focus', 'paper_ids': [paper['id']]})
            self.assertNotIn('download_with', item)
