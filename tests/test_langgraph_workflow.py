# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

# Add package paths
ROOT = Path(__file__).resolve().parents[1]
for p in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    d = ROOT / "packages" / p
    if d.is_dir() and str(d) not in sys.path:
        sys.path.insert(0, str(d))

import workflow
import langgraph_workflow

class TestLangGraphWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = Path(self.tmp.name)
        self.tmp.close()

    def tearDown(self):
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def test_state_graph_topology(self):
        g = langgraph_workflow.create_research_proposal_graph()
        topo = g.get_topology()
        self.assertEqual(len(topo), 4)
        self.assertEqual(topo[0]["id"], "problem_formulation")
        self.assertEqual(topo[1]["id"], "literature_gap")
        self.assertEqual(topo[2]["id"], "methodology_design")
        self.assertEqual(topo[3]["id"], "proposal_synthesis")

    def test_graph_start_and_review(self):
        with workflow.WorkflowDB(self.db_path) as db:
            db.create_task("测试课题", "探讨电池机理", "固态电池")
            proj = db.db.execute("SELECT id FROM research_tasks LIMIT 1").fetchone()
            project_id = proj["id"]

            # Start graph
            with patch('workflow_execution.kick'):
                res = langgraph_workflow.start_graph(db, project_id, "research_proposal", "验证界面稳定性")
            self.assertTrue(res["has_run"])
            self.assertEqual(res["run"]["graph_type"], "research_proposal")
            run_id = res["run"]["id"]

            # Query state
            state = langgraph_workflow.get_graph_state(db, project_id, run_id)
            self.assertEqual(state["run"]["id"], run_id)

            # Test agent tool invocation
            tool_status = langgraph_workflow.invoke_agent_tool("graph_workflow_status", db, project_id, {"run_id": run_id})
            self.assertTrue(tool_status["has_run"])

    def test_new_graph_shares_canonical_execution_and_versioned_inputs(self):
        import workflow_execution
        import project_tasks
        with tempfile.TemporaryDirectory(prefix='rw-unified-graph-') as folder:
            with workflow.WorkflowDB(self.db_path) as db:
                project=db.create_task('Unified graph',base_dir=folder)
                (Path(folder)/'evidence.md').write_text('# Actual evidence',encoding='utf-8')
                inputs=[{'relative':'evidence.md'}]
                with patch('workflow_execution.kick'):
                    state=langgraph_workflow.start_graph(db,project,'literature_study','Read selected evidence',inputs)
                run=workflow_execution.latest(db,project)
                self.assertEqual(state['run']['id'],run['id'])
                selected=db.db.execute('SELECT layout_id FROM project_workflow_layouts WHERE task_id=?',(project,)).fetchone()
                self.assertEqual(selected['layout_id'],run['layout']['id'])
                tasks=project_tasks.list_tasks(db,project,workflow_run_id=run['id'])
                self.assertEqual(len(tasks),4)
                self.assertTrue(all(task['inputs']==inputs for task in tasks))
                self.assertEqual(db.db.execute('SELECT COUNT(*) FROM langgraph_runs').fetchone()[0],0)
                other=db.create_task('Other project',base_dir=str(Path(folder)/'other'))
                with self.assertRaises(ValueError):langgraph_workflow.get_graph_state(db,other,run['id'])

    def test_agent_cannot_change_human_review_decisions(self):
        with workflow.WorkflowDB(self.db_path) as db:
            for action in ('approve','reject'):
                with self.assertRaises(PermissionError):
                    langgraph_workflow.review_checkpoint(db,'run','node',action,'Feedback',is_human=False)

if __name__ == "__main__":
    unittest.main()
