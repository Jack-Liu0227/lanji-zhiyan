"""New opt-in runs bind working paths while retaining explicit files and history."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import project_bindings
import project_tasks
import project_task_execution
import workflow
import workflow_artifact_paths as paths
import workflow_execution
import workflow_layout
import workflow_tasks


class ArtifactPathBindingTest(unittest.TestCase):
    def layout(self):
        return {
            "nodes": [
                {"id": "search", "step": "write", "output_contract": {
                    "required_files": ["pool.csv"],
                    "file_rules": {"pool.csv": {"min_rows": 1}},
                }},
                {"id": "summary", "step": "search", "output_contract": {
                    "required_files": ["review.md"],
                    "file_rules": {"review.md": {"min_chars": 80}},
                }},
            ],
            "edges": [{"source": "search", "target": "summary", "artifact": "pool.csv"}],
        }

    def test_mapping_uses_dag_edges_and_preserves_input_and_handoff(self):
        layout = self.layout()
        original = deepcopy(layout)
        bound = paths.bind_managed_paths(layout, run_segment="wrun-abc")
        self.assertEqual(bound["nodes"][0]["output_contract"], {
            "required_files": ["研究过程/wrun-abc/search/pool.csv"],
            "file_rules": {"研究过程/wrun-abc/search/pool.csv": {"min_rows": 1}},
        })
        self.assertEqual(bound["nodes"][1]["output_contract"], {
            "required_files": ["最终产物/wrun-abc/review.md"],
            "file_rules": {"最终产物/wrun-abc/review.md": {"min_chars": 80}},
        })
        self.assertEqual(bound["edges"], [{"source": "search", "target": "summary", "artifact": "pool.csv"}])
        self.assertEqual(layout, original)
        self.assertEqual(paths.bind_managed_paths(bound, run_segment="wrun-abc"), bound)

    def test_explicit_paths_and_rules_remain_exactly_as_declared(self):
        layout = self.layout()
        node = layout["nodes"][0]
        node["output_contract"]["required_files"].extend(["outputs/wf_review/source.md", "custom\\draft.md"])
        node["output_contract"]["file_rules"]["outputs/wf_review/source.md"] = {"min_chars": 20}
        bound = paths.bind_managed_paths(layout, run_segment="run1")
        self.assertEqual(bound["nodes"][0]["output_contract"]["required_files"], [
            "研究过程/run1/search/pool.csv", "outputs/wf_review/source.md", "custom\\draft.md"])
        self.assertEqual(bound["nodes"][0]["output_contract"]["file_rules"], {
            "研究过程/run1/search/pool.csv": {"min_rows": 1},
            "outputs/wf_review/source.md": {"min_chars": 20},
        })

    def test_outputs_and_only_compiler_target_line_are_rewritten(self):
        layout = self.layout()
        node = layout["nodes"][0]
        node["outputs"] = ["pool.csv", "notes/check.md"]
        node["prompt"] = "正文保留 pool.csv 作为逻辑名称。\n目标产出文件：pool.csv、notes/check.md\n其他说明不变。"
        bound = paths.bind_managed_paths(layout, run_segment="run1")
        self.assertEqual(bound["nodes"][0]["outputs"], ["研究过程/run1/search/pool.csv", "notes/check.md"])
        self.assertEqual(bound["nodes"][0]["prompt"],
                         "正文保留 pool.csv 作为逻辑名称。\n目标产出文件：研究过程/run1/search/pool.csv、notes/check.md\n其他说明不变。")
        self.assertEqual(workflow_layout.required_outputs(bound["nodes"][0]),
                         ["研究过程/run1/search/pool.csv", "notes/check.md"])

    def test_ambiguous_human_declarations_are_rejected(self):
        for prompt in ("请先检索。目标产出文件：pool.csv", "目标文件：pool.csv",
                       "目标产出文件：pool.csv，保存后继续执行", "目标产出文件：pool.csv 并写出结论"):
            with self.subTest(prompt=prompt):
                layout = self.layout()
                layout["nodes"][0]["prompt"] = prompt
                with self.assertRaisesRegex(ValueError, "声明"):
                    paths.bind_managed_paths(layout, run_segment="run1")

    def test_terminal_case_insensitive_and_explicit_target_collisions(self):
        for second in ("REVIEW.md", "最终产物/run1/review.md", "最终产物/run1/review.md/child.txt"):
            with self.subTest(second=second):
                layout = {"nodes": [
                    {"id": "a", "output_contract": {"required_files": ["review.md"]}},
                    {"id": "b", "output_contract": {"required_files": [second]}},
                ], "edges": []}
                with self.assertRaisesRegex(ValueError, "冲突"):
                    paths.bind_managed_paths(layout, run_segment="run1")

    def test_node_directories_cannot_collide_by_windows_case(self):
        layout = self.layout()
        layout["nodes"][1]["id"] = "SEARCH"
        with self.assertRaisesRegex(ValueError, "大小写冲突"):
            paths.bind_managed_paths(layout, run_segment="run1")

    def test_windows_invalid_components_and_run_segments_are_rejected(self):
        for relative in ("CON.md", "COM1.csv", "LPT².txt", "bad.md.", "bad.md ",
                         "notes/NUL.txt", "notes /file.md", "a?.md", "../out.md", "C:/out.md"):
            with self.subTest(relative=relative):
                layout = {"nodes": [{"id": "a", "output_contract": {"required_files": [relative]}}]}
                with self.assertRaises(ValueError):
                    paths.bind_managed_paths(layout, run_segment="run1")
        for segment in ("../outside", "nested/run", "CON", "run.", "run "):
            with self.subTest(segment=segment), self.assertRaises(ValueError):
                paths.bind_managed_paths(self.layout(), run_segment=segment)

    def test_binder_has_no_filesystem_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pool.csv"
            source.write_text("ORIGINAL", encoding="utf-8")
            bound = paths.bind_managed_paths(self.layout(), run_segment="run1")
            self.assertEqual(bound["nodes"][1]["output_contract"]["required_files"], ["最终产物/run1/review.md"])
            self.assertEqual(list(root.iterdir()), [source])
            self.assertEqual(source.read_text(encoding="utf-8"), "ORIGINAL")


class ManagedWorkflowPrepareTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.db = workflow.WorkflowDB(Path(temporary.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Managed artifacts", base_dir=str(self.root))
        workflow_layout.ensure_schema(self.db)
        project_bindings.register(self.db, self.project, "assistant", "builtin-steward")
        self.layout = workflow_layout.save_layout(self.db, {"name": "Managed fixture", "nodes": [
            {"id": "search", "step": "task", "assistant_id": "builtin-steward",
             "prompt": "检索文献\n目标产出文件：pool.csv",
             "output_contract": {"required_files": ["pool.csv"], "file_rules": {"pool.csv": {"min_rows": 1}}}},
            {"id": "summary", "step": "task", "assistant_id": "builtin-steward",
             "prompt": "总结证据\n目标产出文件：review.md",
             "output_contract": {"required_files": ["review.md"]}},
        ], "edges": [{"source": "search", "target": "summary", "artifact": "pool.csv"}]})["layout"]

    def prepare(self, **kwargs):
        return workflow_tasks.prepare(self.db, self.project, "Research goal", "request1", self.layout["id"], **kwargs)

    def test_opt_in_persists_policy_and_matching_task_layout_contracts(self):
        template = self.db.db.execute("SELECT definition FROM workflow_layouts WHERE id=?", (self.layout["id"],)).fetchone()[0]
        self.root.mkdir(parents=True, exist_ok=True)
        original = self.root / "pool.csv"
        original.write_text("USER_DATA", encoding="utf-8")
        archive = self.root / ".tasks" / "history.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("OLD_SNAPSHOT", encoding="utf-8")
        run = self.prepare(managed_artifacts=True)
        self.assertEqual(run["settings"]["managed_artifacts"], {"version": 1, "run_segment": run["id"]})
        expected = [f"研究过程/{run['id']}/search/pool.csv", f"最终产物/{run['id']}/review.md"]
        for node, relative in zip(run["layout"]["nodes"], expected):
            task = project_tasks.get(self.db, run["settings"]["task_ids"][node["id"]])
            self.assertEqual(node["output_contract"]["required_files"], [relative])
            self.assertEqual(task["output_contract"], node["output_contract"])
            self.assertEqual(task["metadata"]["node_config"]["output_contract"], node["output_contract"])
            self.assertEqual(task["metadata"]["layout_snapshot"]["nodes"], run["layout"]["nodes"])
            self.assertIn("目标产出文件：" + relative, task["goal"])
        first = project_tasks.get(self.db, run["settings"]["task_ids"]["search"])
        self.assertEqual(first["output_contract"]["file_rules"], {expected[0]: {"min_rows": 1}})
        second = project_tasks.get(self.db, run["settings"]["task_ids"]["summary"])
        self.assertEqual(second["metadata"]["handoffs"][0]["artifact"], "pool.csv")
        self.assertEqual(self.db.db.execute("SELECT definition FROM workflow_layouts WHERE id=?", (self.layout["id"],)).fetchone()[0], template)
        self.assertEqual(original.read_text(encoding="utf-8"), "USER_DATA")
        self.assertEqual(archive.read_text(encoding="utf-8"), "OLD_SNAPSHOT")
        self.assertFalse((self.root / "研究过程").exists())
        self.assertFalse((self.root / "最终产物").exists())

    def test_repeat_compile_and_prepare_preserve_frozen_paths_and_tasks(self):
        run = self.prepare(managed_artifacts=True)
        saved = self.db.db.execute("SELECT layout_snapshot,settings FROM workflow_executions WHERE run_id=?", (run["id"],)).fetchone()
        again = workflow_tasks.compile_run(self.db, workflow_execution.detail(self.db, run["id"]))
        repeated = self.prepare(managed_artifacts=False)
        self.assertEqual(again["settings"]["task_ids"], run["settings"]["task_ids"])
        self.assertEqual(repeated["layout"], run["layout"])
        self.assertEqual(repeated["settings"]["managed_artifacts"], run["settings"]["managed_artifacts"])
        current = self.db.db.execute("SELECT layout_snapshot,settings FROM workflow_executions WHERE run_id=?", (run["id"],)).fetchone()
        self.assertEqual(tuple(current), tuple(saved))
        self.assertEqual(len(project_tasks.list_tasks(self.db, self.project, workflow_run_id=run["id"])), 2)

    def test_review_mode_and_managed_paths_remain_frozen_together(self):
        run = self.prepare(managed_artifacts=True, review_mode="auto")
        self.assertEqual(run["settings"]["review_mode"], "auto")
        for task_id in run["settings"]["task_ids"].values():
            self.assertEqual(project_tasks.get(self.db, task_id)["scope"]["workflow_review_mode"], "auto")
        repeated = self.prepare(managed_artifacts=False, review_mode="human_review")
        self.assertEqual(repeated["settings"]["review_mode"], "auto")
        self.assertEqual(repeated["layout"]["nodes"][0]["output_contract"]["required_files"],
                         [f"研究过程/{run['id']}/search/pool.csv"])
        self.assertEqual(repeated["settings"]["task_ids"], run["settings"]["task_ids"])

    def test_expanded_subworkflow_exit_uses_actual_frozen_outgoing_edge(self):
        child = workflow_layout.save_layout(self.db, {"name": "Managed child", "nodes": [
            {"id": "inner", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["child.md"]}},
        ], "edges": []})["layout"]
        parent = workflow_layout.save_layout(self.db, {"name": "Managed parent", "nodes": [
            {"id": "sub", "step": "task", "node_kind": "workflow", "workflow_template_id": child["id"]},
            {"id": "final", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["report.md"]}},
        ], "edges": [{"source": "sub", "target": "final", "artifact": "child.md"}]})["layout"]
        run = workflow_tasks.prepare(self.db, self.project, "Expanded goal", "expanded", parent["id"],
                                     managed_artifacts=True, review_mode="human_review")
        nodes = {node["id"]: node for node in run["layout"]["nodes"]}
        self.assertEqual(set(nodes), {"sub.inner", "final"})
        self.assertEqual(nodes["sub.inner"]["output_contract"]["required_files"],
                         [f"研究过程/{run['id']}/sub.inner/child.md"])
        self.assertEqual(nodes["final"]["output_contract"]["required_files"],
                         [f"最终产物/{run['id']}/report.md"])
        task = project_tasks.get(self.db, run["settings"]["task_ids"]["sub.inner"])
        self.assertEqual(task["metadata"]["layout_snapshot"]["nodes"], run["layout"]["nodes"])
        self.assertEqual(task["metadata"]["node_config"]["output_contract"], task["output_contract"])
        self.assertEqual(task["scope"]["workflow_review_mode"], "human_review")
        self.assertEqual(workflow_layout._layout(self.db, child["id"])["nodes"][0]["output_contract"]["required_files"],
                         ["child.md"])

    def test_default_off_and_existing_run_cannot_be_retrofitted(self):
        run = self.prepare()
        repeated = self.prepare(managed_artifacts=True)
        self.assertEqual(repeated["id"], run["id"])
        self.assertNotIn("managed_artifacts", repeated["settings"])
        self.assertEqual([n["output_contract"]["required_files"] for n in repeated["layout"]["nodes"]], [["pool.csv"], ["review.md"]])
        self.assertEqual(project_tasks.get(self.db, run["settings"]["task_ids"]["search"])["output_contract"]["file_rules"],
                         {"pool.csv": {"min_rows": 1}})

    def test_another_run_has_its_own_frozen_directory(self):
        first = self.prepare(managed_artifacts=True)
        workflow_tasks.cancel(self.db, first["id"])
        second = workflow_tasks.prepare(self.db, self.project, "Research goal", "request2", self.layout["id"], managed_artifacts=True)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["layout"]["nodes"][0]["output_contract"]["required_files"],
                         [f"研究过程/{second['id']}/search/pool.csv"])
        self.assertEqual(workflow_execution.detail(self.db, first["id"])["layout"]["nodes"][0]["output_contract"]["required_files"],
                         [f"研究过程/{first['id']}/search/pool.csv"])

    def test_explicit_directory_path_survives_managed_prepare(self):
        layout = workflow_layout.save_layout(self.db, {"name": "Explicit fixture", "nodes": [
            {"id": "final", "step": "task", "assistant_id": "builtin-steward",
             "prompt": "目标产出文件：outputs/existing.md",
             "output_contract": {"required_files": ["outputs/existing.md"],
                                 "file_rules": {"outputs/existing.md": {"min_chars": 10}}}},
        ], "edges": []})["layout"]
        run = workflow_tasks.prepare(self.db, self.project, "Explicit goal", "explicit", layout["id"], managed_artifacts=True)
        task = project_tasks.get(self.db, run["settings"]["task_ids"]["final"])
        self.assertEqual(task["output_contract"], {
            "required_files": ["outputs/existing.md"],
            "file_rules": {"outputs/existing.md": {"min_chars": 10}},
        })
        self.assertEqual(task["goal"], "目标产出文件：outputs/existing.md")

    def test_managed_write_still_freezes_an_immutable_attempt_output(self):
        run = self.prepare(managed_artifacts=True)
        task_id = run["settings"]["task_ids"]["search"]
        attempt = project_tasks.start(self.db, task_id, authority=project_tasks.CommandContext.system(self.project))
        relative = f"研究过程/{run['id']}/search/pool.csv"

        def model(db, project_id, *args, **kwargs):
            self.assertIn(relative, kwargs["system"])
            result = workflow._tool_impl("project_artifact_write", db, project_id, {
                "path": relative, "content": "title,doi\nStudy,10.test/source\n",
            })
            return {"reply": "Saved candidate pool", "output_assets": result["output_assets"]}

        with patch.object(workflow, "run_agent", side_effect=model):
            task = project_task_execution.execute(self.db, attempt["id"])
        self.assertEqual(task["status"], "awaiting_review", task["latest_run"]["error"])
        artifact = next(output for output in task["outputs"] if output["name"] == "pool.csv")
        self.assertEqual(artifact["relative"], relative)
        snapshot = Path(artifact["path"])
        self.assertEqual(snapshot.relative_to(self.root).parts[:4],
                         (".tasks", task_id, attempt["id"], "outputs"))
        self.assertEqual((self.root / relative).read_text(encoding="utf-8"), "title,doi\nStudy,10.test/source\n")
        (self.root / relative).write_text("LATER_WORKING_VERSION", encoding="utf-8")
        self.assertEqual(snapshot.read_text(encoding="utf-8"), "title,doi\nStudy,10.test/source\n")

    def test_prepare_collision_records_failed_run_without_creating_tasks(self):
        layout = workflow_layout.save_layout(self.db, {"name": "Collision fixture", "nodes": [
            {"id": "left", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["result.md"]}},
            {"id": "right", "step": "task", "assistant_id": "builtin-steward",
             "output_contract": {"required_files": ["RESULT.md"]}},
        ], "edges": []})["layout"]
        with self.assertRaisesRegex(ValueError, "冲突"):
            workflow_tasks.prepare(self.db, self.project, "Collision", "collision", layout["id"], managed_artifacts=True)
        row = self.db.db.execute("SELECT id,status FROM workflow_runs WHERE idempotency_key='collision'").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(project_tasks.list_tasks(self.db, self.project, workflow_run_id=row["id"]), [])
        self.assertIsNone(workflow_execution.active(self.db, self.project))
        recovered = self.prepare(managed_artifacts=True)
        self.assertEqual(recovered["layout"]["nodes"][0]["output_contract"]["required_files"],
                         [f"研究过程/{recovered['id']}/search/pool.csv"])


if __name__ == "__main__":
    unittest.main()
