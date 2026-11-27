from pathlib import Path
import io
import tempfile
import unittest

import artifact_quality
import project_bindings
import project_artifacts
import project_task_execution
import project_tasks
import workflow
import workflow_layout
import workflow_node_workbench
import workflow_file_preview
import workflow_tasks
import csv_text


class WorkflowCSVFieldTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rw-csv-fields-")
        self.addCleanup(self.temp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.project = self.db.create_task("Large CSV fields", base_dir=str(self.root))
        workflow_layout.ensure_schema(self.db)
        self.binding = project_bindings.register(self.db, self.project, "assistant", "builtin-steward")
        self.db.add_model("CSV fixture", "http://127.0.0.1:1/v1", "csv-fixture", "", activate=True)
        project_bindings.set_workflow_review_mode(self.db, "human_review")

    def queue(self, relative, *, file_rules=None):
        layout = workflow_layout.save_layout(self.db, {
            "name": "Large CSV field workflow",
            "nodes": [{"id": "csv", "step": "task", "label": "Write large CSV",
                       "executor_binding_id": self.binding["id"],
                       "output_contract": {"required_files": [relative], "file_rules": file_rules or {}}}],
            "edges": [],
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        flow = workflow_tasks.prepare(self.db, self.project, "Preserve a long CSV field through review.")
        task = project_tasks.get(self.db, flow["settings"]["task_ids"]["csv"])
        attempt = project_tasks.start(
            self.db, task["id"], authority=project_tasks.CommandContext.ui(self.project))
        claimed = project_tasks.claim(self.db, attempt["id"])
        self.db.project_task_context = project_tasks.CommandContext.agent(self.project, task["id"])
        return task, claimed["run"]

    def test_shared_parser_preserves_a_long_quoted_field(self):
        large = "FIELD_START" + "x" * 140_000 + "FIELD_TAIL"
        rows = list(csv_text.DictReader(io.StringIO("id,value\n1,\"" + large + "\"\n", newline=""), strict=True))
        self.assertEqual(rows[0]["value"], large)

    def test_long_cell_keeps_strict_csv_and_column_count_checks(self):
        large = "x" * 140_000
        with self.assertRaisesRegex(ValueError, "CSV"):
            project_artifacts.validate_text("report.csv", "id,value\n1,\"" + large)
        with self.assertRaisesRegex(ValueError, "字段数"):
            project_artifacts.validate_text("report.csv", "id,value\n1,\"" + large + "\",extra\n")

    def test_long_cell_survives_candidate_and_methods_contract_validation(self):
        long_title = "Candidate " + "T" * 140_000 + " CANDIDATE_TAIL"
        candidate_csv = "title,doi_or_stable_url\n\"" + long_title + "\",10.1234/large\n"
        sources = artifact_quality.candidate_sources(candidate_csv)
        self.assertEqual(sources, [{"title": long_title, "doi_or_stable_url": "10.1234/large"}])

        methods_csv = (
            "citation,interface,modification_method,material_system,evidence_level,"
            "comparability_note,doi_or_stable_url\n"
            f'"{long_title}",interface,method,material,primary,"{("N" * 140_000)}",10.1234/large\n'
        )
        artifact_quality.validate("methods_metrics.csv", methods_csv, {})
        artifact_quality.validate_methods_metrics_candidates("methods_metrics.csv", methods_csv, candidate_csv)
        with self.assertRaisesRegex(ValueError, "accepted candidate"):
            artifact_quality.validate_methods_metrics_candidates(
                "methods_metrics.csv", methods_csv.replace("10.1234/large", "10.1234/other"), candidate_csv)

    def test_long_cell_can_be_written_submitted_and_read_in_node_workbench(self):
        relative = "methods_metrics.csv"
        task, run = self.queue(relative)
        long_cell = "METHOD_START" + "M" * 140_000 + "METHOD_TAIL"
        content = "citation,interface,modification_method,material_system,evidence_level,comparability_note,doi_or_stable_url\n"
        content += f'"Candidate",interface,method,material,primary,"{long_cell}",10.1234/large\n'
        receipt = project_artifacts.write(self.db, self.project, {"path": relative, "content": content})
        self.assertEqual(project_artifacts.submission(self.db, self.project)["files"][0]["content_hash"], receipt["content_hash"])

        root_path = self.root / relative
        output = project_task_execution._outputs(
            self.db, task, run, {"output_assets": receipt["output_assets"]}, None)
        completed = project_tasks.complete(self.db, run["id"], {
            "reply": "Saved the long CSV cell.", "output_snapshot": output,
        })
        self.assertEqual(completed["status"], "awaiting_review")
        selection = workflow_node_workbench.Selection(self.project, task["id"])
        preview = workflow_node_workbench.preview(
            self.db, selection, "file:0", authority=project_tasks.CommandContext.ui(self.project))
        self.assertEqual(preview["data"][0]["comparability_note"], long_cell)
        self.assertEqual(root_path.read_text(encoding="utf-8"), content)

    def test_long_cell_is_readable_in_project_csv_preview(self):
        long_cell = "PREVIEW_START" + "P" * 140_000 + "PREVIEW_TAIL"
        text = "id,value\n1,\"" + long_cell + "\"\n"
        path = self.root / "preview.csv"
        path.write_text(text, encoding="utf-8")
        preview = workflow_file_preview._csv(
            path.name, workflow_file_preview._read_file(path), 0, 10, {"kind": "project"})
        self.assertEqual(preview["rows"][0][1], long_cell)


if __name__ == "__main__":
    unittest.main()
