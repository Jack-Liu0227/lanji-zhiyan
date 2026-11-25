import json
import tempfile
import unittest
from pathlib import Path

import workflow_layout
import workflow_execution
import workflow_tasks
from workflow import WorkflowDB, now


class WorkflowStudioAndDeletionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False)
        self.tmp.close()
        self.db = WorkflowDB(self.tmp.name)
        workflow_layout.ensure_schema(self.db)
        self.assistant = self.db.assistant_save("as_test", name="测试专家")
        self.project_id = self.db.create_task('测试项目', '')
        import project_bindings
        self.binding = project_bindings.register(self.db, self.project_id, 'assistant', 'as_test')

    def tearDown(self):
        try:
            self.db.db.close()
            Path(self.tmp.name).unlink(missing_ok=True)
        except Exception:
            pass

    def test_delete_node_with_restitch(self):
        layout = {
            "id": "tpl_test",
            "name": "Test Layout",
            "nodes": [
                {"id": "node_a", "step": "idea", "label": "Step A"},
                {"id": "node_b", "step": "review", "label": "Step B"},
                {"id": "node_c", "step": "write", "label": "Step C"}
            ],
            "edges": [
                {"source": "node_a", "target": "node_b"},
                {"source": "node_b", "target": "node_c"}
            ],
            "order": ["node_a", "node_b", "node_c"]
        }
        repaired = workflow_layout.delete_node_from_layout(layout, "node_b", restitch=True)
        self.assertEqual(len(repaired["nodes"]), 2)
        self.assertEqual([n["id"] for n in repaired["nodes"]], ["node_a", "node_c"])
        self.assertEqual(len(repaired["edges"]), 1)
        self.assertEqual(repaired["edges"][0]["source"], "node_a")
        self.assertEqual(repaired["edges"][0]["target"], "node_c")
        self.assertEqual(repaired["order"], ["node_a", "node_c"])

    def test_delete_node_single_node_prevented(self):
        layout = {
            "id": "tpl_single",
            "name": "Single",
            "nodes": [{"id": "only_one", "step": "task", "label": "Only"}],
            "edges": [],
            "order": ["only_one"]
        }
        with self.assertRaises(ValueError):
            workflow_layout.delete_node_from_layout(layout, "only_one", restitch=True)

    def test_delete_builtin_template_is_persistent(self):
        workflow_layout.delete_layout(self.db, "builtin.workflow.reading")
        with WorkflowDB(self.tmp.name) as reopened:
            self.assertNotIn('builtin.workflow.reading', [row['id'] for row in workflow_layout.list_layouts(reopened)['layouts']])

    def test_delete_custom_template_fallback(self):
        custom = workflow_layout.save_layout(self.db, {
            "id": "custom_to_delete",
            "name": "Custom Workflow",
            "description": "desc",
            "nodes": [{"id": "n1", "step": "task", "label": "Node 1"}],
            "edges": [],
            "order": ["n1"]
        })["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project_id, "layout_id": custom["id"]})

        # Ensure project is bound to custom
        bound = self.db.db.execute("SELECT layout_id FROM project_workflow_layouts WHERE task_id=?", (self.project_id,)).fetchone()
        self.assertEqual(bound[0], custom["id"])

        # Delete custom layout
        res = workflow_layout.delete_layout(self.db, custom["id"])
        self.assertTrue(res["ok"])

        # Deleting a template clears the selection without substituting a different workflow.
        bound_after = self.db.db.execute("SELECT layout_id FROM project_workflow_layouts WHERE task_id=?", (self.project_id,)).fetchone()
        self.assertEqual(bound_after[0], '')
        self.assertIsNone(workflow_layout.state(self.db, self.project_id)['layout'])

    def test_workflow_execution_delete(self):
        import project_bindings
        layout = workflow_layout._layout(self.db, workflow_layout.DEFAULT_LAYOUT_ID)
        workflow_layout.select_layout(self.db, {"task_id": self.project_id, "layout_id": layout["id"]})
        for node in layout["nodes"]:
            role = node.get("executor_role_id") or node["id"]
            project_bindings.bind_role(self.db, self.project_id, layout["id"], role, self.binding["id"])

        # Prepare run
        run = workflow_tasks.prepare(self.db, self.project_id, "目标", "idemp_test")
        run_id = run["id"]

        # Verify run exists
        self.assertIsNotNone(workflow_execution.detail(self.db, run_id))

        # Delete run
        res = workflow_execution.route("POST", "/api/workflow/execution/delete", {"id": run_id, "project_id": self.project_id}, {}, self.db)
        self.assertTrue(res.get("deleted"))

        # Verify run deleted
        with self.assertRaises(ValueError):
            workflow_execution.detail(self.db, run_id)
        task_count = self.db.db.execute("SELECT COUNT(*) FROM project_tasks WHERE workflow_run_id=?", (run_id,)).fetchone()[0]
        self.assertEqual(task_count, 0)


if __name__ == '__main__':
    unittest.main()
