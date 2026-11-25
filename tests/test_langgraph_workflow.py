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
        self.assertEqual(topo[0]["step"], "idea")
        self.assertEqual(topo[0]["execution_mode"], "assistant")
        self.assertEqual(topo[1]["id"], "literature_gap")
        self.assertEqual(topo[1]["step"], "search")
        self.assertEqual(topo[2]["id"], "methodology_design")
        self.assertEqual(topo[2]["execution_mode"], "team")
        self.assertEqual(topo[3]["id"], "proposal_synthesis")
        self.assertEqual(topo[3]["step"], "write")

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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix='rw-unified-graph-') as folder:
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
                self.assertEqual(len(tasks),5)
                self.assertTrue(any(task['inputs']==inputs for task in tasks))
                self.assertEqual(db.db.execute('SELECT COUNT(*) FROM langgraph_runs').fetchone()[0],0)
                other=db.create_task('Other project',base_dir=str(Path(folder)/'other'))
                with self.assertRaises(ValueError):langgraph_workflow.get_graph_state(db,other,run['id'])

    def test_agent_cannot_change_human_review_decisions(self):
        with workflow.WorkflowDB(self.db_path) as db:
            for action in ('approve','reject'):
                with self.assertRaises(PermissionError):
                    langgraph_workflow.review_checkpoint(db,'run','node',action,'Feedback',is_human=False)

    def test_hybrid_research_dag_topology_and_execution(self):
        g = langgraph_workflow.create_hybrid_research_dag()
        topo = g.get_topology()
        self.assertEqual(len(topo), 6)
        by_id = {item["id"]: item for item in topo}
        self.assertEqual(by_id["s1_seed"]["level"], 0)
        self.assertEqual(set(by_id["s1_seed"]["next"]), {"p1_overview", "p2_methodology", "p3_risk_audit"})
        self.assertEqual(by_id["p1_overview"]["level"], 1)
        self.assertEqual(by_id["p2_methodology"]["level"], 1)
        self.assertEqual(by_id["p3_risk_audit"]["level"], 1)
        self.assertEqual(by_id["j1_synthesis"]["level"], 2)
        self.assertEqual(by_id["s2_verification"]["level"], 3)

    def test_workflow_create_auto_binds_and_auto_starts_custom_dag(self):
        import project_bindings
        import project_tasks
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix='rw-dag-autostart-') as folder:
            with workflow.WorkflowDB(self.db_path) as db:
                project = db.create_task('Auto DAG Project', base_dir=folder)
                db.assistant_save('dag-expert', name='科研执行专家', enabled=1, permission='full_access')
                binding = project_bindings.register(db, project, 'assistant', 'dag-expert')
                nodes = [
                    {'id': 's1', 'step': 'idea', 'label': 'Step 1: S1 建目录与素材'},
                    {'id': 'p1', 'step': 'search', 'label': 'Step 2: P1 概览文档'},
                    {'id': 'p2', 'step': 'notes', 'label': 'Step 3: P2 步骤文档'},
                    {'id': 'p3', 'step': 'map', 'label': 'Step 4: P3 风险文档'},
                    {'id': 'j1', 'step': 'write', 'label': 'Step 5: J1 汇合汇编'},
                    {'id': 's2', 'step': 'review', 'label': 'Step 6: S2 终检自证'},
                ]
                edges = [
                    {'source': 's1', 'target': 'p1'},
                    {'source': 's1', 'target': 'p2'},
                    {'source': 's1', 'target': 'p3'},
                    {'source': 'p1', 'target': 'j1'},
                    {'source': 'p2', 'target': 'j1'},
                    {'source': 'p3', 'target': 'j1'},
                    {'source': 'j1', 'target': 's2'},
                ]
                with patch('workflow_execution.kick'):
                    res = workflow._tool_impl('workflow_create', db, project, {
                        'name': '验证W5·串并混合六节点文档流',
                        'goal': '自动编排并启动六节点串并混合流',
                        'auto_start': True,
                        'nodes': nodes,
                        'edges': edges,
                    })
                self.assertTrue(res['ok'], res)
                self.assertIn('run', res)
                self.assertEqual(res['navigation']['workflow_run_id'], res['run']['id'])
                for node in res['layout']['nodes']:
                    self.assertEqual(node['executor_binding_id'], binding['id'])
                tasks = project_tasks.list_tasks(db, project, workflow_run_id=res['run']['id'])
                self.assertEqual(len(tasks), 6)
                self.assertTrue(all(t['executor_binding_id'] == binding['id'] for t in tasks))

    def test_derive_chat_title_from_first_sentence(self):
        raw = "帮我编排一个有五六个节点的流程，任务有并行的也有串行的。就这样一个节点，你去运行吧\n\n【当前项目明确引用的材料】\n文件：a.md"
        title = workflow.derive_chat_title(raw)
        self.assertEqual(title, "帮我编排一个有五六个节点的流程，任务有并行的也有串行的")
        self.assertEqual(workflow.derive_chat_title("   "), "新的课题讨论")

    def test_evoflow_agent_tools_preview_and_require_human_review(self):
        import evoflow_sdk
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            with workflow.WorkflowDB(self.db_path) as db:
                project = db.create_task('EvoFlow project', base_dir=folder)
                with patch('workflow_execution.kick') as kick:
                    result = workflow._tool_impl('plan', db, project, {'goal': 'Prepare a plan'}, '')
                self.assertGreater(result['plan']['total_steps'], 0)
                self.assertFalse(workflow._tool_impl('supervisor', db, project, {'action': 'get_status'}, '')['has_run'])
                self.assertTrue(workflow._tool_impl('platform', db, project, {'action': 'workflow.schema'}, '')['ok'])
                kick.assert_not_called()
                with self.assertRaisesRegex(PermissionError, '人工核查'):
                    evoflow_sdk.platform_call(db, project, 'tasks.set_state', {'task_id': 'unused', 'state': 'completed'})

    def test_evoflow_sdk_plan_supervisor_and_platform_bus(self):
        import evoflow_sdk
        import project_bindings
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix='rw-evoflow-sdk-') as folder:
            with workflow.WorkflowDB(self.db_path) as db:
                project = db.create_task('并行串行混合流程编排验证', base_dir=folder)
                db.assistant_save('evo-worker', name='EvoFlow 执行专家', enabled=1, permission='full_access')
                project_bindings.register(db, project, 'assistant', 'evo-worker')
                with patch('workflow_execution.kick'):
                    gen = evoflow_sdk.platform_call(db, project, 'workflow.generate', {
                        'name': '验证W5·串并混合六节点文档流',
                        'goal': '帮我编排一个有五六个节点的流程，任务有并行的也有串行的',
                        'auto_start': True,
                    })
                self.assertTrue(gen['ok'], gen)
                self.assertEqual(gen['navigation']['dock_tab'], 'workflow')
                self.assertTrue(gen['navigation']['open_right_stage'])
                run_id = gen['result']['run']['id']
                status = evoflow_sdk.platform_call(db, project, 'workflow.status', {'runId': run_id})
                self.assertTrue(status['result']['has_run'])
                self.assertEqual(status['result']['total_count'], 6)
                panel = evoflow_sdk.platform_call(db, project, 'ui.panel', {'op': 'openRightStage', 'kind': 'workflow'})
                self.assertEqual(panel['navigation']['dock_tab'], 'workflow')

    def test_literature_review_dag_topology_and_start(self):
        g = langgraph_workflow.create_literature_review_dag()
        self.assertEqual(g.name, "literature_review_dag")
        self.assertEqual(len(g.nodes), 5)
        self.assertEqual(g.entry_point, "lit_search")
        edges = {(e["source"], e["target"]) for e in g.get_edges()}
        expected_edges = {
            ("lit_search", "lit_method"),
            ("lit_search", "lit_metrics"),
            ("lit_method", "lit_matrix"),
            ("lit_metrics", "lit_matrix"),
            ("lit_matrix", "lit_draft"),
        }
        self.assertEqual(edges, expected_edges)
        self.assertFalse(g.nodes["lit_search"].review_required)
        self.assertTrue(g.nodes["lit_draft"].review_required)

        with workflow.WorkflowDB(self.db_path) as db:
            db.create_task("文献综述测试", "综述深度研读", "AI科研综述")
            proj = db.db.execute("SELECT id FROM research_tasks LIMIT 1").fetchone()
            project_id = proj["id"]
            with patch("workflow_execution.kick"):
                res = langgraph_workflow.start_graph(db, project_id, "literature_review_dag", "文献综述执行")
            self.assertTrue(res["has_run"])
            self.assertEqual(res["run"]["graph_type"], "literature_review_dag")
            run_id = res["run"]["id"]
            state = langgraph_workflow.get_graph_state(db, project_id, run_id)
            self.assertEqual(state["run"]["id"], run_id)
            self.assertEqual(len(state["checkpoints"]), 5)


if __name__ == "__main__":
    unittest.main()
