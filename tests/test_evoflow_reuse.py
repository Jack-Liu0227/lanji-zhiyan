# -*- coding: utf-8 -*-
import tempfile
import unittest
from unittest.mock import patch

import evoflow_sdk
import project_bindings
import project_tasks
import workflow
import workflow_execution
import workflow_layout


class TestEvoFlowReuse(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="rw-evoflow-reuse-")
        self.db_path = f"{self._temp_dir.name}\\test_reuse.db"

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_auto_decompose_goal_literature_vs_default(self):
        # 1. 包含综述关键词，自动拆解为 5 节点文献综述 DAG
        lit_steps = evoflow_sdk.auto_decompose_goal("关于室温超导机理的文献综述")
        self.assertEqual(len(lit_steps), 5)
        self.assertEqual(lit_steps[0]["id"], "s1_lit_search")
        self.assertEqual(lit_steps[1]["id"], "p1_evidence")
        self.assertEqual(lit_steps[2]["id"], "p2_methodology")
        self.assertEqual(lit_steps[3]["id"], "j1_synthesis")
        self.assertEqual(lit_steps[4]["id"], "s2_review")
        self.assertIn("室温超导机理", lit_steps[0]["goal"])
        self.assertNotIn("{{topic}}", lit_steps[0]["goal"])

        # 2. 普通通用目标，拆解为标准 6 节点协同 DAG
        gen_steps = evoflow_sdk.auto_decompose_goal("多模态大模型代码生成评测研究")
        self.assertEqual(len(gen_steps), 6)
        self.assertEqual(gen_steps[0]["id"], "s1_seed")
        self.assertIn("多模态大模型代码生成评测研究", gen_steps[0]["goal"])

    def test_compile_plan_to_dag_variable_substitution(self):
        steps = [
            {
                "id": "step_a",
                "name": "步骤 A",
                "goal": "围绕「旧的课题」开展{{topic}}调研，总目标是{{goal}}",
                "assigned_agent": "search-worker",
            },
            {
                "id": "step_b",
                "name": "步骤 B",
                "goal": "对「旧的课题」进行梳理",
                "depends_on": ["step_a"],
                "assigned_agent": "project-planner",
            },
        ]
        dag = evoflow_sdk.compile_plan_to_dag(
            goal="全新研究方向：钙钛矿太阳能电池",
            steps=steps,
            template_desc="围绕「旧的课题」开展研究",
        )
        self.assertEqual(len(dag["nodes"]), 2)
        node_a = dag["nodes"][0]
        self.assertIn("围绕「全新研究方向：钙钛矿太阳能电池」", node_a["prompt"])
        self.assertIn("总目标是全新研究方向：钙钛矿太阳能电池", node_a["prompt"])
        self.assertNotIn("旧的课题", node_a["prompt"])
        node_b = dag["nodes"][1]
        self.assertIn("全新研究方向：钙钛矿太阳能电池", node_b["prompt"])

    def test_workflow_list_includes_builtin_presets(self):
        with workflow.WorkflowDB(self.db_path) as db:
            project = db.create_task("测试预设列表项目", base_dir=self._temp_dir.name)
            res = evoflow_sdk.platform_call(db, project, "workflow.list")
            self.assertTrue(res["ok"])
            layouts = res["result"]["layouts"]
            layout_ids = [l["id"] for l in layouts]
            self.assertIn("builtin.evoflow.literature_review", layout_ids)
            self.assertIn("builtin.evoflow.default_research", layout_ids)
            lit_layout = next(l for l in layouts if l["id"] == "builtin.evoflow.literature_review")
            self.assertEqual(lit_layout["source"], "builtin")
            self.assertEqual(len(lit_layout["nodes"]), 5)

    def test_cross_project_save_as_template_and_reuse(self):
        with workflow.WorkflowDB(self.db_path) as db:
            # 创建项目 1：自定义并沉淀模板
            proj1 = db.create_task("项目 1：制定图神经网络综述流程", base_dir=self._temp_dir.name)
            db.assistant_save("evo-worker", name="通用研究专家", enabled=1, permission="full_access")
            project_bindings.register(db, proj1, "assistant", "evo-worker")

            create_res = evoflow_sdk.platform_call(db, proj1, "workflow.create", {
                "template_id": "tpl_gnn_survey",
                "name": "图神经网络前沿综述五节点流",
                "goal": "围绕「图神经网络表达能力」开展综述梳理",
                "save_as_template": True,
            })
            self.assertTrue(create_res["ok"])
            self.assertEqual(create_res["result"]["template_id"], "tpl_gnn_survey")

            # 验证模板已全局沉淀入 workflow_layouts
            row = db.db.execute("SELECT * FROM workflow_layouts WHERE id='tpl_gnn_survey'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["name"], "图神经网络前沿综述五节点流")

            # 创建项目 2：全新的项目，跨项目直接通过 template_id 复用并注入新目标
            proj2 = db.create_task("项目 2：固态电解质文献综述", base_dir=self._temp_dir.name)
            project_bindings.register(db, proj2, "assistant", "evo-worker")

            with patch("workflow_execution.kick") as mock_kick:
                run_res = evoflow_sdk.platform_call(db, proj2, "workflow.run", {
                    "template_id": "tpl_gnn_survey",
                    "goal": "固态电解质离子传输机制文献综述",
                })
            self.assertTrue(run_res["ok"])
            mock_kick.assert_called_once()

            # 检查项目 2 的节点任务是否成功创建且注入了新课题
            tasks = project_tasks.list_tasks(db, proj2)
            self.assertEqual(len(tasks), 5)
            # 验证新课题注入到了提示词中
            first_task = next(t for t in tasks if t["workflow_node_id"] == "s1_lit_search")
            self.assertIn("固态电解质离子传输机制文献综述", first_task["goal"])
            self.assertNotIn("图神经网络", first_task["goal"])

            # 验证原始模板在 workflow_layouts 中未被破坏
            orig_row = db.db.execute("SELECT * FROM workflow_layouts WHERE id='tpl_gnn_survey'").fetchone()
            self.assertEqual(orig_row["name"], "图神经网络前沿综述五节点流")

    def test_fuzzy_and_intent_template_match_in_workflow_run(self):
        with workflow.WorkflowDB(self.db_path) as db:
            project = db.create_task("语义意图匹配项目", base_dir=self._temp_dir.name)
            db.assistant_save("evo-worker", name="通用研究专家", enabled=1, permission="full_access")
            project_bindings.register(db, project, "assistant", "evo-worker")

            with patch("workflow_execution.kick"):
                # 仅传入 goal 包含综述意图，未传 template_id，自动匹配 literature_review 预设
                res = evoflow_sdk.platform_call(db, project, "workflow.run", {
                    "name": "文献综述",
                    "goal": "关于钙钛矿光伏电池界面钝化的文献综述",
                })
            self.assertTrue(res["ok"])
            tasks = project_tasks.list_tasks(db, project)
            self.assertEqual(len(tasks), 5)
            first_task = tasks[0]
            self.assertIn("钙钛矿光伏电池界面钝化", first_task["goal"])


if __name__ == "__main__":
    unittest.main()
