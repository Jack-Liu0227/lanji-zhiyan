from pathlib import Path
import csv
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import asset_service
import evoflow_sdk
import execution
import project_artifacts
import project_bindings
import project_library
import project_task_execution
import project_task_review
import project_tasks
import research_reading
import workflow
import workflow_execution
import workflow_context
import workflow_layout
import workflow_node_workbench
import workflow_search
import workflow_stages
import workflow_file_preview
import workflow_export_revision
import node_output_editors
from tests._pdf_fixture import write_text_pdf


class ProjectArtifactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Artifacts", base_dir=str(Path(self.tmp.name) / "project"))
        workflow_layout.ensure_schema(self.db)
        self.binding = project_bindings.register(self.db, self.project, "assistant", "builtin-steward")

    def queue(self, **fields):
        task = project_tasks.create(self.db, {
            "project_id": self.project, "title": "Evidence", "goal": "save evidence",
            "executor_binding_id": self.binding["id"], **fields,
        }, authority=project_tasks.CommandContext.system(self.project))
        run = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        return task, run

    def test_legacy_prompt_output_becomes_required_file_contract(self):
        definition = workflow_layout.validate_definition({
            "nodes": [{"id": "evidence", "step": "task", "label": "Evidence",
                       "prompt": "提取证据。目标产出文件：outputs/wf_review/02_extracted_evidence.md"}],
            "edges": [], "order": ["evidence"],
        })
        self.assertEqual(definition["nodes"][0]["output_contract"], {
            "required_files": ["outputs/wf_review/02_extracted_evidence.md"]
        })

    def test_existing_builtin_presets_gain_contracts_without_losing_user_edits(self):
        evoflow_sdk.ensure_presets(self.db)
        preset_id = "builtin.evoflow.literature_review"
        row = self.db.db.execute("SELECT definition FROM workflow_layouts WHERE id=?", (preset_id,)).fetchone()
        definition = json.loads(row["definition"])
        for node in definition["nodes"]:
            node["output_contract"] = {}
        definition["nodes"][1]["prompt"] += "\nUSER_CUSTOM_INSTRUCTION"
        self.db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?", (json.dumps(definition), preset_id))
        self.db.db.commit()
        evoflow_sdk.ensure_presets(self.db)
        current = workflow_layout._layout(self.db, preset_id)
        evidence = current["nodes"][1]
        self.assertTrue(evidence["output_contract"]["requires_evidence"])
        self.assertEqual(evidence["output_contract"]["required_files"], ["outputs/wf_review/02_extracted_evidence.md"])
        self.assertIn("USER_CUSTOM_INSTRUCTION", evidence["prompt"])

    def test_artifact_write_is_task_scoped_and_registers_output(self):
        task = project_tasks.create(self.db, {
            "project_id": self.project, "title": "Evidence", "goal": "save evidence",
            "executor_binding_id": self.binding["id"],
            "output_contract": {"required_files": ["outputs/wf_review/02_extracted_evidence.md"]},
        }, authority=project_tasks.CommandContext.system(self.project))
        run = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.system(self.project))
        claimed = project_tasks.claim(self.db, run["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        result = project_artifacts.write(self.db, self.project, {
            "path": "outputs/wf_review/02_extracted_evidence.md",
            "content": "# Evidence\n\nA saved result.",
        })
        path = Path(self.db.base_dir(self.project)) / "outputs/wf_review/02_extracted_evidence.md"
        self.assertTrue(path.is_file())
        self.assertEqual(result["path"], "outputs/wf_review/02_extracted_evidence.md")
        self.assertEqual(result["output_assets"], [result["asset_id"]])
        registered = asset_service.get(self.db, result["asset_id"])
        metadata = json.loads(registered["metadata"])
        self.assertEqual(metadata["project_task_id"], task["id"])
        self.assertEqual(metadata["task_run_id"], claimed["run"]["id"])
        frozen = project_task_execution._outputs(
            self.db, claimed["task"], claimed["run"],
            {"reply": "Evidence saved", "output_assets": result["output_assets"]}, None,
        )
        self.assertTrue(any(item.get("original_path") == str(path.resolve()) for item in frozen["files"]))

        with self.assertRaises(PermissionError):
            project_artifacts.write(self.db, self.project, {
                "path": "outputs/other.md", "content": "not declared",
            })

    def test_retry_can_read_and_replace_its_own_prior_attempt_artifact(self):
        task = project_tasks.create(self.db, {
            "project_id": self.project, "title": "Retry artifact", "goal": "revise prior output",
            "executor_binding_id": self.binding["id"],
            "output_contract": {"required_files": ["outputs/report.md"]},
        }, authority=project_tasks.CommandContext.system(self.project))
        first = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        claimed_first = project_tasks.claim(self.db, first["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        prior = project_artifacts.write(self.db, self.project, {
            "path": "outputs/report.md", "content": "# First attempt",
        })
        project_tasks.fail(self.db, first["id"], "The first attempt did not finish")

        second = project_tasks.start(self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        claimed_second = project_tasks.claim(self.db, second["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        current = project_artifacts.current(self.db, self.project)
        self.assertEqual(current[0]["content"], "# First attempt")
        self.assertEqual(current[0]["content_hash"], prior["content_hash"])

        revised = project_artifacts.write(self.db, self.project, {
            "path": "outputs/report.md", "content": "# Revised attempt",
            "expected_hash": "",
        })
        registered = asset_service.get(self.db, revised["asset_id"])
        metadata = json.loads(registered["metadata"])
        self.assertEqual(metadata["project_task_id"], task["id"])
        self.assertEqual(metadata["task_run_id"], claimed_second["run"]["id"])
        frozen = project_task_execution._outputs(
            self.db, claimed_second["task"], claimed_second["run"],
            {"reply": "Revised", "output_assets": revised["output_assets"]}, None,
        )
        self.assertTrue(any(item.get("original_path", "").endswith("report.md") for item in frozen["files"]))
        self.assertNotEqual(claimed_first["run"]["id"], claimed_second["run"]["id"])

    def test_registered_attempt_evidence_is_added_to_markdown_before_contract_validation(self):
        source = write_text_pdf(Path(self.db.base_dir(self.project)) / "source.pdf", [
            "Methods\nCells were tested at 4.2 V after cycling.",
        ])
        paper_id = self.db.ingest_pdf(self.project, source)["id"]
        page = self.db.paper(paper_id)["pages"][0]["text"]
        evidence = self.db.add_evidence(paper_id, {
            "page_no": 1, "field": "test voltage", "value": "4.2 V",
            "excerpt": page[page.index("Cells were tested"):],
        })
        task, run = self.queue(output_contract={"required_files": ["evidence.md"], "requires_evidence": True})
        claimed = project_tasks.claim(self.db, run["id"])
        project_tasks.save_checkpoint(self.db, run["id"], phase="model", done_tools={
            "paper_evidence_record\0{}": {"accepted": [evidence]},
        })
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        saved = project_artifacts.write(self.db, self.project, {
            "path": "evidence.md", "content": "# Evidence analysis\n\nA source-supported statement.",
        })
        output = project_task_execution._outputs(
            self.db, claimed["task"], claimed["run"],
            {"reply": "Saved", "output_assets": saved["output_assets"]}, None,
        )
        self.assertIn(evidence["id"], output["content"])
        done = project_tasks.complete(self.db, run["id"], {
            "reply": "Saved", "output_snapshot": output,
        })
        self.assertEqual(done["status"], "awaiting_review", done["latest_run"]["error"])

    def test_evidence_lookup_returns_more_than_twelve_records_without_repeated_calls(self):
        source = write_text_pdf(Path(self.db.base_dir(self.project)) / "source.pdf", [
            "Cells were tested at 4.2 V after cycling."])
        paper_id = self.db.ingest_pdf(self.project, source)["id"]
        records = [self.db.add_evidence(paper_id, {
            "page_no": 1, "field": "voltage-" + str(index), "value": "4.2 V",
            "excerpt": "Cells were tested at 4.2 V after cycling."}) for index in range(15)]
        ids = [item["id"] for item in records]
        results = research_reading.evidence(self.db, self.project, ids + [ids[0]])
        self.assertEqual([item["id"] for item in results], ids)
        self.assertTrue(all(item["paper_id"] == paper_id and item["page_no"] == 1 for item in results))
        other = self.db.create_task("Other project", base_dir=str(Path(self.tmp.name) / "other"))
        with self.assertRaisesRegex(ValueError, "证据不属于"):
            research_reading.evidence(self.db, other, ids)

    def test_registered_attempt_evidence_is_added_to_matching_csv_comparability_cell(self):
        source = write_text_pdf(Path(self.db.base_dir(self.project)) / "source.pdf", [
            "Methods\nCells were tested at 4.2 V after cycling.",
        ])
        paper_id = self.db.ingest_pdf(self.project, source)["id"]
        page = self.db.paper(paper_id)["pages"][0]["text"]
        evidence = self.db.add_evidence(paper_id, {
            "page_no": 1, "field": "test voltage", "value": "4.2 V",
            "excerpt": page[page.index("Cells were tested"):],
        })
        task, run = self.queue(output_contract={"required_files": ["methods_metrics.csv"], "requires_evidence": True})
        project_tasks.claim(self.db, run["id"])
        project_tasks.save_checkpoint(self.db, run["id"], phase="model", done_tools={
            "paper_evidence_record\0{}": {"accepted": [evidence]},
        })
        source_paper = self.db.paper(paper_id)
        content = ("citation,comparability_note\n" +
                   f'"{source_paper["filename"]}","Testing conditions require cross-study review."\n')
        attached = project_artifacts._attach_task_evidence(
            self.db, task["id"], self.project, "methods_metrics.csv", content,
        )
        row = next(csv.DictReader(io.StringIO(attached, newline="")))
        self.assertIn(evidence["id"], row["comparability_note"])
        self.assertIn("p.1", row["comparability_note"])

    def test_path_and_read_only_boundaries(self):
        with self.assertRaises(PermissionError):
            project_artifacts.write(self.db, self.project, {"path": "outputs/a.md", "content": "A"})
        for raw in ("../outside.md", "C:/outside.md", "/outside.md", ".tasks/result.md"):
            with self.subTest(raw=raw), self.assertRaises((PermissionError, ValueError)):
                project_artifacts._path(self.db, self.project, raw)
        with self.assertRaises(PermissionError):
            workflow.enforce_tool_policy("project_artifact_write", {}, "read_only")
        workflow.enforce_tool_policy("project_artifact_write", {}, "workspace_write")

    def test_conflict_can_be_read_and_merged_without_overwriting_a_later_edit(self):
        definition = {"nodes": [{"id": "A", "step": "task", "assistant_id": "builtin-steward",
                                "output_contract": {"required_files": ["outputs/report.md"]}}], "edges": []}
        layout = workflow_layout.save_layout(self.db, {"name": "Conflict recovery", **definition})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        flow = workflow_execution.create(self.db, self.project)
        def model(db, pid, *args, **kwargs):
            initial = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "outputs/report.md", "content": "# Original", "expected_hash": ""})
            with self.assertRaisesRegex(ValueError, 'section="artifacts"'):
                workflow._tool_impl("project_artifact_write", db, pid, {
                    "path": "outputs/report.md", "content": "# Update", "expected_hash": ""})
            snapshot = workflow_context.read(db, pid, {"section": "artifacts"})
            saved = json.loads(snapshot["content"])[0]
            self.assertEqual(saved["content"], "# Original")
            self.assertEqual(saved["content_hash"], initial["content_hash"])
            target = Path(db.base_dir(pid)) / saved["path"]
            target.write_text("# Human edit", encoding="utf-8")
            with self.assertRaises(ValueError):
                workflow._tool_impl("project_artifact_write", db, pid, {
                    "path": saved["path"], "content": "# Stale update", "expected_hash": saved["content_hash"]})
            self.assertEqual(target.read_text(encoding="utf-8"), "# Human edit")
            saved = json.loads(workflow_context.read(db, pid, {"section": "artifacts"})["content"])[0]
            result = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": saved["path"], "content": saved["content"] + "\nMerged result.",
                "expected_hash": saved["content_hash"]})
            return {"reply": "Saved merged result", "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=model), patch.object(workflow_execution, "kick"):
            flow = workflow_execution.advance(self.db, flow["id"])
        self.assertEqual(flow["status"], "awaiting_review", flow.get("error"))
        self.assertEqual((Path(self.db.base_dir(self.project)) / "outputs/report.md").read_text(encoding="utf-8"),
                         "# Human edit\nMerged result.")
        with self.assertRaises(PermissionError):
            workflow_context.read(self.db, self.project, {"run_id": flow["id"], "node_id": "A", "section": "artifacts"})

    def test_malformed_csv_is_rejected_during_write_then_corrected_in_the_same_attempt(self):
        _, run = self.queue(output_contract={"required_files": ["table.csv"]})
        target = Path(self.db.base_dir(self.project)) / 'table.csv'
        def model(db, pid, *args, **kwargs):
            with self.assertRaisesRegex(ValueError, '第 2 行应有 3 列，实际 4 列'):
                workflow._tool_impl('project_artifact_write', db, pid, {
                    'path': 'table.csv', 'content': 'title,method,metric\nStudy,,,\n'})
            self.assertFalse(target.exists())
            result = workflow._tool_impl('project_artifact_write', db, pid, {
                'path': 'table.csv', 'content': 'title,method,metric\nStudy,,\n'})
            return {'reply': 'Corrected table saved', 'output_assets': result['output_assets']}
        with patch.object(workflow, 'run_agent', side_effect=model):
            task = project_task_execution.execute(self.db, run['id'])
        self.assertEqual(task['status'], 'awaiting_review', task['latest_run']['error'])
        self.assertEqual(len(task['runs']), 1)
        self.assertEqual(target.read_text(encoding='utf-8'), 'title,method,metric\nStudy,,\n')

    def test_downstream_pages_the_complete_accepted_file_and_checks_its_version(self):
        source = '# Source\n' + ('A source paragraph with quoted text.\n' * 800) + '\nLAST_VERIFIED_DOI: 10.test/end\n'
        layout = workflow_layout.save_layout(self.db, {'name': 'Complete source handoff', 'nodes': [
            {'id': 'A', 'step': 'task', 'assistant_id': 'builtin-steward',
             'output_contract': {'required_files': ['source.md']}},
            {'id': 'B', 'step': 'task', 'assistant_id': 'builtin-steward',
             'output_contract': {'required_files': ['summary.md']}},
        ], 'edges': [{'source': 'A', 'target': 'B'}]})['layout']
        workflow_layout.select_layout(self.db, {'task_id': self.project, 'layout_id': layout['id']})
        flow = workflow_execution.create(self.db, self.project)
        def model(db, pid, *args, **kwargs):
            task = project_tasks.get(db, db.project_task_context.parent_task_id)
            if task['workflow_node_id'] == 'A':
                name, text = 'source.md', source
            else:
                self.assertIn('read_with', kwargs['extra_context'])
                pages, offset = [], 0
                while offset is not None:
                    page = workflow_context.read(db, pid, {'node_id': task['dependencies'][0], 'section': 'artifact',
                                                          'artifact': 'source.md', 'offset': offset, 'limit': 8000})
                    pages.append(page['content'])
                    self.assertTrue(page['delivery_id'])
                    offset = page['next_offset']
                self.assertGreater(len(pages), 1)
                self.assertEqual(''.join(pages), source)
                name, text = 'summary.md', '# Summary\nLAST_VERIFIED_DOI: 10.test/end'
            result = workflow._tool_impl('project_artifact_write', db, pid, {'path': name, 'content': text})
            return {'reply': 'Saved', 'output_assets': result['output_assets']}
        with patch.object(workflow, 'run_agent', side_effect=model), patch.object(workflow_execution, 'kick'):
            flow = workflow_execution.advance(self.db, flow['id'])
            first = project_tasks.get(self.db, flow['settings']['task_ids']['A'])
            working = asset_service.path_asset(self.db, self.project, Path(self.db.base_dir(self.project)) / 'source.md')
            frozen = asset_service.get(self.db, first['outputs'][0]['asset_id'])
            provenance = json.loads(frozen['metadata'])
            self.assertEqual(provenance['working_asset_id'], working['id'])
            self.assertEqual(provenance['working_content_hash'], working['content_hash'])
            import project_resources
            listing = project_resources.catalog(self.db, self.project, 'output', review_status='all')
            row = next(item for item in listing['items'] if item.get('asset_id') == frozen['id'])
            self.assertEqual(row['produced_by']['node_id'], 'A')
            self.assertEqual(row['produced_by']['working_asset_id'], working['id'])
            project_tasks.review(self.db, first['id'], approved=True, authority=project_tasks.CommandContext.ui(self.project))
            flow = workflow_execution.advance(self.db, flow['id'])
        self.assertEqual(flow['status'], 'awaiting_review', flow.get('error'))
        with self.assertRaises(ValueError):
            workflow_context.read(self.db, self.project, {'run_id': flow['id'], 'node_id': 'B',
                                                        'section': 'artifact', 'artifact': 'summary.md'})
        Path(first['outputs'][0]['path']).write_text('Tampered source', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '版本发生变化'):
            workflow_context.read(self.db, self.project, {'run_id': flow['id'], 'node_id': 'A',
                                                        'section': 'artifact', 'artifact': 'source.md'})

    def test_quality_contract_rejects_short_report_before_write_and_at_submission(self):
        _, run = self.queue(output_contract={'required_files': ['review.md'],
            'file_rules': {'review.md': {'min_chars': 2500}}})
        def model(db, pid, *args, **kwargs):
            with self.assertRaisesRegex(ValueError, 'min_chars.*2500.*1200'):
                workflow._tool_impl('project_artifact_write', db, pid, {
                    'path': 'review.md', 'content': 'x' * 1200})
            self.assertFalse((Path(db.base_dir(pid)) / 'review.md').exists())
            result = workflow._tool_impl('project_artifact_write', db, pid, {
                'path': 'review.md', 'content': 'x' * 2500})
            return {'reply': 'Saved complete-length candidate', 'output_assets': result['output_assets']}
        with patch.object(workflow, 'run_agent', side_effect=model):
            task = project_task_execution.execute(self.db, run['id'])
        self.assertEqual(task['status'], 'awaiting_review', task['latest_run']['error'])
        frozen = Path(task['outputs'][0]['path'])
        frozen.write_text('Too short after an external change', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'min_chars'):
            project_tasks._validate_contract(task['output_contract'], task['latest_run']['output_snapshot'])

    def test_missing_and_malformed_artifacts_do_not_succeed(self):
        _, run = self.queue(output_contract={"required_files": ["outputs/pool.csv"]})
        with patch.object(workflow, "run_agent", return_value={"reply": "Cannot save target"}):
            task = project_task_execution.execute(self.db, run["id"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("outputs/pool.csv", task["latest_run"]["error"])
        self.assertEqual(task["deliveries"], [])

        _, run = self.queue(output_contract={"required_files": ["outputs/pool.csv"]})
        def write(db, pid, *args, **kwargs):
            result = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "outputs/pool.csv", "content": "title,doi\nIncomplete row\n",
            })
            return {"reply": "Saved", "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=write):
            task = project_task_execution.execute(self.db, run["id"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("CSV", task["latest_run"]["error"])

    def test_existing_target_can_be_replaced_with_the_supplied_version_hash(self):
        target = Path(self.db.base_dir(self.project)) / "outputs/report.md"
        asset_service.atomic_write_text(target, "# Previous draft")
        previous_hash = asset_service.sha256_file(target)
        _, run = self.queue(output_contract={"required_files": ["outputs/report.md"]})
        def write(db, pid, *args, **kwargs):
            self.assertIn(previous_hash, kwargs["system"])
            result = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "outputs/report.md", "content": "# Updated result", "expected_hash": previous_hash,
            })
            return {"reply": "Saved", "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=write):
            task = project_task_execution.execute(self.db, run["id"])
        self.assertEqual(task["status"], "awaiting_review", task["latest_run"]["error"])
        self.assertEqual(target.read_text(encoding="utf-8"), "# Updated result")

    def test_previous_task_file_does_not_count_as_a_new_delivery(self):
        contract = {"required_files": ["outputs/report.md"]}
        _, first = self.queue(output_contract=contract)
        def write(db, pid, *args, **kwargs):
            result = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "outputs/report.md", "content": "# First saved result",
            })
            return {"reply": "Saved", "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=write):
            self.assertEqual(project_task_execution.execute(self.db, first["id"])["status"], "awaiting_review")
        _, second = self.queue(output_contract=contract)
        with patch.object(workflow, "run_agent", return_value={"reply": "Already done"}):
            task = project_task_execution.execute(self.db, second["id"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("当前尝试", task["latest_run"]["error"])

    def test_download_reuses_an_existing_project_pdf(self):
        pdf = write_text_pdf(Path(self.db.base_dir(self.project)) / "existing.pdf", ["Existing source"])
        paper = self.db.ingest_pdf(self.project, pdf)
        result_id = self.db.cache_result(self.project, {"title": "Existing source", "doi": "10.test/reused"})
        project_library.link_pdf(self.db, self.project, result_id, paper["id"])
        with patch.object(workflow_stages, "download_pdf") as download:
            result = workflow_stages.stage_download(self.db, self.project, result_id)
        download.assert_not_called()
        self.assertTrue(result["reused"])
        self.assertEqual(result["paper"]["id"], paper["id"])

    def test_abstract_only_cannot_complete_fulltext_evidence_task(self):
        _, run = self.queue(output_contract={
            "required_files": ["outputs/evidence.md"], "requires_evidence": True,
        })
        def write(db, pid, *args, **kwargs):
            result = workflow._tool_impl("project_artifact_write", db, pid, {
                "path": "outputs/evidence.md", "content": "# Evidence\nAbstract only. Full text remains unavailable.",
            })
            return {"reply": "Saved partial evidence", "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=write):
            task = project_task_execution.execute(self.db, run["id"])
        self.assertEqual(task["status"], "failed")
        self.assertIn("全文证据", task["latest_run"]["error"])
        self.assertEqual(task["deliveries"], [])

    def test_five_node_literature_flow_writes_exact_files_and_passes_frozen_evidence(self):
        definition = evoflow_sdk.compile_plan_to_dag("界面机制文献综述")
        for node in definition["nodes"]:
            node["assistant_id"] = "builtin-steward"
        layout = workflow_layout.save_layout(self.db, {"name": "Verified literature flow", **definition})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        flow = workflow_execution.create(self.db, self.project, "Produce a grounded review")
        pdf = write_text_pdf(Path(self.db.base_dir(self.project)) / "source.pdf", [
            "The LiCoO2 interface operates at 4.2 V under the reported test conditions."
        ])
        paper = self.db.ingest_pdf(self.project, pdf)
        called = []
        def model(db, pid, *args, **kwargs):
            task = project_tasks.get(db, db.project_task_context.parent_task_id)
            path = task["output_contract"]["required_files"][0]
            if path.endswith(".csv"):
                content = "title,doi\nInterface study,10.test/source\n"
            elif task["workflow_node_id"] == "p1_evidence":
                item = research_reading.record(db, pid, paper["id"], [{
                    "page_no": 1, "field": "voltage", "value": "4.2 V",
                    "excerpt": "The LiCoO2 interface operates at 4.2 V under the reported test conditions.",
                }])["accepted"][0]
                content = "# Evidence\n4.2 V. Source page 1. Evidence " + item["id"]
            else:
                content = "# Review\nGrounded result with limitations."
            called.append((task["workflow_node_id"], kwargs["extra_context"]))
            result = workflow._tool_impl("project_artifact_write", db, pid, {"path": path, "content": content})
            return {"reply": "Saved " + path, "output_assets": result["output_assets"]}
        with patch.object(workflow, "run_agent", side_effect=model), patch.object(workflow_execution, "kick"):
            for _ in range(8):
                flow = workflow_execution.advance(self.db, flow["id"])
                if flow["status"] == "succeeded":
                    break
                self.assertEqual(flow["status"], "awaiting_review", flow.get("error"))
                for task in project_tasks.list_tasks(self.db, self.project, workflow_run_id=flow["id"]):
                    if task["status"] == "awaiting_review":
                        workspace = workflow_node_workbench.read(
                            self.db, workflow_node_workbench.Selection(self.project, task["id"]),
                            authority=project_tasks.CommandContext.ui(self.project),
                        )
                        self.assertFalse(any(a["id"] == "structured" for a in workspace["artifacts"]))
                        self.assertTrue(workspace["artifacts"][0]["editable"])
                        if task["workflow_node_id"] == "s1_lit_search":
                            current = project_task_review.read(self.db, task["id"], self.project, 0)
                            project_task_review.revise(self.db, task["id"], {
                                "reviewed_run_id": current["run_id"], "expected_version": current["task_version"],
                                "expected_hash": current["content_hash"], "output_index": 0,
                                "text": "title,doi\nReviewed interface study,10.test/source\n",
                            }, authority=project_tasks.CommandContext.ui(self.project))
                            task = project_tasks.get(self.db, task["id"])
                        project_tasks.review(self.db, task["id"], approved=True, feedback="Checked fixture source",
                                             reviewed_output_version=task["latest_run"]["checkpoint"].get("output_revision", 0),
                                             authority=project_tasks.CommandContext.ui(self.project))
        self.assertEqual(flow["status"], "succeeded", flow.get("error"))
        root = Path(self.db.base_dir(self.project))
        for node in definition["nodes"]:
            self.assertTrue((root / node["output_contract"]["required_files"][0]).is_file())
        synthesis = next(context for node, context in called if node == "j1_synthesis")
        self.assertIn("4.2 V", synthesis)
        self.assertIn("Grounded result", synthesis)

    def test_large_markdown_review_output_is_readable_for_revision(self):
        root = Path(self.db.base_dir(self.project))
        path = root / "large-review.md"
        content = "Review section. " * 180000 + "LARGE_REVIEW_TAIL"
        path.write_text(content, encoding="utf-8")
        project_bindings.set_workflow_review_mode(self.db, "human_review")
        task, run = self.queue(review_required=True,
            output_contract={"required_files": [path.name]})
        digest = project_tasks._hash(path)
        project_tasks.complete(self.db, run["id"], {"output_snapshot": {
            "content": content, "files": [{"path": str(path), "relative": path.name,
                "name": path.name, "content_hash": digest}]}})
        current = project_task_review.read(self.db, task["id"], self.project, 0)
        self.assertEqual(current["text"], content)
        revised = content + "\nREVISED_TAIL_MUST_SURVIVE"
        project_task_review.revise(self.db, task["id"], {
            "reviewed_run_id": current["run_id"], "expected_version": current["task_version"],
            "expected_hash": current["content_hash"], "output_index": 0, "text": revised,
        }, authority=project_tasks.CommandContext.ui(self.project))
        saved = project_task_review.read(self.db, task["id"], self.project, 0)
        self.assertEqual(saved["text"], revised)
        self.assertNotEqual(saved["content_hash"], digest)
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_large_markdown_and_csv_previews_preserve_content_and_row_pagination(self):
        root = Path(self.db.base_dir(self.project))
        markdown = "# Source\n" + "verified text\n" * 220000
        markdown_path = root / "large-preview.md"
        markdown_path.write_bytes(markdown.encode("utf-8"))
        preview = workflow_file_preview._markdown(markdown_path.name,
            workflow_file_preview._read_file(markdown_path), 0, 100, {"kind": "project"})
        self.assertEqual(preview["text"], markdown)

        csv_text = "id,value\n" + "x,0123456789abcdef\n" * 600000
        csv_path = root / "large-preview.csv"
        csv_path.write_bytes(csv_text.encode("utf-8"))
        table = workflow_file_preview._csv(csv_path.name,
            workflow_file_preview._read_file(csv_path), 1, 3, {"kind": "project"})
        self.assertEqual(table["columns"], ["id", "value"])
        self.assertEqual(table["offset"], 1)
        self.assertEqual(table["limit"], 3)
        self.assertEqual(table["total_rows"], 600000)
        self.assertEqual(len(table["rows"]), 3)
        self.assertTrue(table["truncated"])

    def test_structured_review_editor_accepts_content_over_old_byte_limits(self):
        text = "reviewed conclusion " * 500000
        self.assertGreater(len(text.encode("utf-8")), 8 * 1024 * 1024)
        self.assertEqual(node_output_editors.parse_content("review", text, "old"), text)

    def test_export_revision_accepts_large_markdown_and_validates_format(self):
        markdown = "# Review\n" + "source-grounded discussion\n" * 180000
        self.assertGreater(len(markdown.encode("utf-8")), 2 * 1024 * 1024)
        self.assertEqual(workflow_export_revision.editable_source({"markdown": markdown, "format": "md"}),
                         {"markdown": markdown, "format": "md"})
        with self.assertRaisesRegex(ValueError, "格式"):
            workflow_export_revision.editable_source({"markdown": markdown, "format": "exe"})

    def test_html_link_and_http_forbidden_use_available_pdf_without_temp_leaks(self):
        folder = Path(self.tmp.name) / "downloads"
        with patch.object(workflow_search, "open_url", side_effect=[
            io.BytesIO(b'<html><meta name="citation_pdf_url" content="/paper.pdf"></html>'),
            io.BytesIO(b"%PDF-1.7 fixture"),
        ]):
            path = workflow_search.download_pdf({"title": "Paper", "url": "https://example.test/landing"}, folder)
        self.assertEqual(path.read_bytes(), b"%PDF-1.7 fixture")
        blocked = urllib.error.HTTPError("https://example.test/blocked", 403, "Forbidden", {}, None)
        with patch.object(workflow_search, "open_url", side_effect=[blocked, io.BytesIO(b"%PDF-1.7 open")]), \
             patch.object(workflow_search, "_oa_pdf_candidates", return_value=["https://repository.test/open.pdf"]):
            path = workflow_search.download_pdf({"title": "Open paper", "doi": "10.test/oa", "pdf_url": "https://example.test/blocked"}, folder)
        self.assertEqual(path.read_bytes(), b"%PDF-1.7 open")
        self.assertEqual(list(folder.glob("*.part")), [])

    def test_cancellation_propagates_and_cleans_temp_files(self):
        folder = Path(self.tmp.name) / "cancelled"
        with patch.object(workflow_search, "open_url", side_effect=execution.Cancelled("Stopped")):
            with self.assertRaises(execution.Cancelled):
                workflow_search.download_pdf({"url": "https://example.test/source.pdf"}, folder)
        self.assertEqual(list(folder.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
