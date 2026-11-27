"""Named input reads resolve only verified copies from the current task attempt."""
import hashlib
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
for _package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(_ROOT / "packages" / _package))

import project_tasks
import workflow_context
import workflow_execution


class WorkflowInputArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript("""
            CREATE TABLE project_tasks(
                id TEXT PRIMARY KEY, workflow_run_id TEXT, workflow_node_id TEXT,
                project_id TEXT, status TEXT, latest_run_id TEXT, accepted_delivery_id TEXT
            );
            CREATE TABLE project_task_origins(
                kind TEXT, legacy_id TEXT, task_id TEXT, task_run_id TEXT
            );
            CREATE TABLE project_task_runs(id TEXT PRIMARY KEY, task_id TEXT);
        """)
        self.connection.execute("INSERT INTO project_tasks VALUES(?,?,?,?,?,?,?)",
                                ("task-D", "flow", "D", "project", "running", "task-run-D", ""))
        self.connection.execute("INSERT INTO project_task_origins VALUES(?,?,?,?)",
                                ("workflow_node", "D", "task-D", "task-run-D"))
        self.connection.execute("INSERT INTO project_task_runs VALUES(?,?)", ("task-run-D", "task-D"))
        self.connection.commit()
        self.db = SimpleNamespace(
            db=self.connection,
            base_dir=lambda project_id: self.root,
            workflow_context_run_id="flow",
            workflow_context_node_id="D",
            project_task_context=SimpleNamespace(parent_task_id="task-D"),
        )
        self.text = "Frozen source text.\nSecond line.\n"
        self.frozen = self.root / ".tasks" / "task-D" / "task-run-D" / "inputs" / "0-recovery.md"
        self.frozen.parent.mkdir(parents=True)
        self.frozen.write_bytes(self.text.encode("utf-8"))
        self.digest = hashlib.sha256(self.frozen.read_bytes()).hexdigest()
        self.asset = {"path": str(self.frozen), "original_path": str(self.root / "notes" / "recovery.md"),
                      "relative": "notes/recovery.md", "content_hash": self.digest,
                      "sha256": self.digest, "asset_id": "asset-input"}
        self.checkpoint = {"input": {"goal": "Keep legacy input reads unchanged."}}
        self.run = {"project_id": "project", "settings": {"task_ids": {"D": "task-D"}},
                    "layout": {"nodes": [{"id": "D", "step": "write"}]},
                    "nodes": [{"id": "D", "stage": "write", "checkpoint": self.checkpoint}]}
        self.saved = {"checkpoint": self.checkpoint, "input_snapshot": {"assets": [self.asset]},
                      "output_snapshot": {}}
        self.patches = [patch.object(workflow_execution, "detail", return_value=self.run),
                        patch.object(project_tasks, "get_run", return_value=self.saved)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def read(self, artifact=None, **extra):
        args = {"run_id": "flow", "node_id": "D", "section": "input", **extra}
        if artifact is not None:
            args["artifact"] = artifact
        return workflow_context.read(self.db, "project", args)

    def test_relative_path_and_unique_basename_read_the_frozen_copy_in_pages(self):
        first = self.read("notes/recovery.md", offset=0, limit=12)
        second = self.read("recovery.md", offset=12, limit=1000)

        self.assertEqual(first["content"], self.text[:12])
        self.assertEqual(first["total"], len(self.text))
        self.assertEqual(first["next_offset"], 12)
        self.assertEqual(first["artifact"], "recovery.md")
        self.assertEqual(first["relative"], "notes/recovery.md")
        self.assertEqual(first["content_hash"], self.digest)
        self.assertEqual(first["asset_id"], "asset-input")
        self.assertEqual(second["content"], self.text[12:])
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["node_id"], "D")
        self.assertEqual(first["section"], "input")
        self.assertNotIn("delivery_id", first)

    def test_absolute_path_inside_the_project_resolves_to_the_frozen_copy(self):
        result = self.read(str(self.root / "notes" / "recovery.md"), limit=1000)
        self.assertEqual(result["content"], self.text)
        self.assertEqual(result["relative"], "notes/recovery.md")

    def test_missing_or_ambiguous_names_never_fall_back_to_the_input_snapshot(self):
        with self.assertRaisesRegex(ValueError, "没有匹配"):
            self.read("missing.md")

        duplicate = self.root / ".tasks" / "task-D" / "task-run-D" / "inputs" / "1-recovery.md"
        duplicate.write_text("Another source.", encoding="utf-8")
        second = {**self.asset, "path": str(duplicate), "relative": "supplement/recovery.md",
                  "content_hash": hashlib.sha256(duplicate.read_bytes()).hexdigest()}
        self.saved["input_snapshot"]["assets"].append(second)
        with self.assertRaisesRegex(ValueError, "不唯一"):
            self.read("recovery.md")

        legacy = self.read(limit=100)
        self.assertIn('"goal": "Keep legacy input reads unchanged."', legacy["content"])
        self.assertNotIn("artifact", legacy)

    def test_frozen_hash_mismatch_and_non_own_reads_are_rejected(self):
        self.frozen.write_bytes(b"Changed after freezing.")
        with self.assertRaisesRegex(ValueError, "哈希不匹配"):
            self.read("notes/recovery.md")

        self.frozen.write_bytes(self.text.encode("utf-8"))
        self.db.project_task_context = SimpleNamespace(parent_task_id="another-task")
        self.db.workflow_context_node_id = "another-node"
        with self.assertRaisesRegex(PermissionError, "当前执行节点"):
            self.read("notes/recovery.md")

    def test_input_path_must_stay_inside_the_project(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("Must not be read.", encoding="utf-8")
        self.saved["input_snapshot"]["assets"] = [{**self.asset, "path": str(outside),
                                                     "relative": "outside.md"}]
        with self.assertRaisesRegex(ValueError, "没有匹配"):
            self.read("outside.md")

        with self.assertRaisesRegex(PermissionError, "超出"):
            self.read(str(outside))

    def test_missing_hash_non_text_and_missing_frozen_file_are_rejected(self):
        self.asset.pop("content_hash")
        self.asset.pop("sha256")
        with self.assertRaisesRegex(ValueError, "缺少版本哈希"):
            self.read("notes/recovery.md")
        self.asset["content_hash"] = self.digest

        self.asset["path"] = str(self.frozen.with_suffix(".pdf"))
        with self.assertRaisesRegex(ValueError, "文本附件"):
            self.read("notes/recovery.md")

        self.asset["path"] = str(self.frozen.with_name("missing.md"))
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.read("notes/recovery.md")

    def test_an_old_attempt_cannot_use_runtime_ownership_to_read_its_input(self):
        self.connection.execute("UPDATE project_tasks SET latest_run_id='new-task-run-D' WHERE id='task-D'")
        self.connection.commit()
        with self.assertRaisesRegex(PermissionError, "本次冻结"):
            self.read("notes/recovery.md")


if __name__ == "__main__":
    unittest.main()
