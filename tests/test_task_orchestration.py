"""Project roles, generic nodes and agent boundaries through public commands."""
import json
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

import workflow
import workflow_execution as engine
import workflow_layout
import workflow_state
import project_bindings as bindings
import project_tasks as tasks
import workflow_tasks


class TaskOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.pid = self.db.create_task("Project", base_dir=str(Path(self.tmp.name) / "project"))
        workflow_layout.ensure_schema(self.db)
        self.grant = bindings.register(self.db, self.pid, "assistant", "builtin-steward")

    def layout(self, steps):
        nodes = [{"id": "n" + str(i), "step": stage, "label": "Node " + str(i), "executor_role_id": "role" + str(i)} for i, stage in enumerate(steps)]
        layout = workflow_layout.save_layout(self.db, {"name": "Flow", "nodes": nodes,
            "edges": [{"source": a["id"], "target": b["id"]} for a, b in zip(nodes, nodes[1:])]})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        for node in nodes:
            bindings.bind_role(self.db, self.pid, layout["id"], node["executor_role_id"], self.grant["id"])
        return layout

    def test_prepare_is_model_free_and_critical_reviews_survive_tool_full_access(self):
        self.layout(["task", "task"])
        self.db.assistant_save("builtin-steward", permission="full_access")
        with patch.object(workflow, "run_agent", return_value={"reply": "Actual report"}) as model:
            run = engine.create(self.db, self.pid)
            model.assert_not_called()
            self.assertEqual(len(run["settings"]["task_ids"]), 2)
            run = engine.advance(self.db, run["id"])
            self.assertEqual(run["status"], "awaiting_review", run["error"])
            self.assertEqual(model.call_count, 1)
            first = tasks.find_origin(self.db, "workflow_node", run["current_node"])
            self.assertEqual(first["workflow_node_id"], "n0")
            engine.review(self.db, run["id"], run["current_node"], True, "CHECKED")
            run = engine.advance(self.db, run["id"])
            self.assertEqual(model.call_count, 2)
            self.assertEqual(len(run["nodes"]), 2)
            states = workflow_layout.state(self.db, self.pid)["nodes"]
            self.assertEqual([n["status"] for n in states], ["delivered", "awaiting_review"])
            self.assertNotEqual(states[0]["canonical_task_id"], states[1]["canonical_task_id"])

    def node_command(self, node_id='n0', **extra):
        import file_handoff
        target = file_handoff.targets(self.db, self.pid)
        node = next(n for n in target['nodes'] if n['id']==node_id)
        return {'project_id':self.pid, 'layout_id':target['layout_id'], 'revision':target['revision'],
                'node_id':node_id, **node['actors'][0], 'goal':'Analyze project material', 'request_key':'node-prepare', **extra}

    def test_prepare_node_is_idempotent_and_rejects_foreign_or_stale_target(self):
        self.layout(['task'])
        body=self.node_command()
        with patch.object(workflow, 'run_agent') as model:
            prepared=engine.route('POST','/api/workflow/execution/prepare-node',body,{},self.db)
            again=engine.route('POST','/api/workflow/execution/prepare-node',body,{},self.db)
            model.assert_not_called()
        self.assertEqual(prepared['task']['id'],again['task']['id'])
        self.assertEqual(prepared['task']['scope']['allowed_binding_ids'],[self.grant['id']])
        self.assertEqual(prepared['task']['goal'],body['goal'])
        for forged in ({'revision':'obsolete'}, {'assistant_id':'builtin-writer'}, {'node_id':'foreign'}, {'task_id':'another-project'}):
            with self.assertRaises(ValueError):
                engine.route('POST','/api/workflow/execution/prepare-node',{**body,**forged},{},self.db)
        with self.assertRaises(ValueError):
            workflow_tasks.prepare_node(self.db,{**body,'goal':'Different work'})

    def test_active_workflow_node_task_inherits_dependencies_and_scope(self):
        self.layout(['task','task'])
        run=engine.create(self.db,self.pid)
        parent=tasks.get(self.db,run['settings']['task_ids']['n1'])
        result=workflow_tasks.prepare_node(self.db,self.node_command('n1'))['task']
        self.assertEqual(result['parent_task_id'],parent['id'])
        self.assertEqual(result['workflow_node_id'],'n1')
        self.assertEqual(result['scope'],parent['scope'])
        self.assertEqual(result['dependencies'],parent['dependencies'])
        with self.assertRaises(ValueError):
            tasks.start(self.db,result['id'],authority=tasks.CommandContext.ui(self.pid))

    def test_review_of_previous_run_cannot_accept_a_retry(self):
        self.layout(['task'])
        run=engine.create(self.db,self.pid)
        with patch.object(workflow,'run_agent',return_value={'reply':'First output'}):
            run=engine.advance(self.db,run['id'])
        task=tasks.get(self.db,run['settings']['task_ids']['n0'])
        previous=task['latest_run']['id']
        tasks.review(self.db,task['id'],approved=False,feedback='Revise',authority=tasks.CommandContext.ui(self.pid),reviewed_run_id=previous)
        with patch.object(workflow,'run_agent',return_value={'reply':'Second output'}):
            engine._status(self.db,run['id'],'queued')
            run=engine.advance(self.db,run['id'])
        with self.assertRaisesRegex(ValueError,'新的运行'):
            tasks.review(self.db,task['id'],approved=True,authority=tasks.CommandContext.ui(self.pid),reviewed_run_id=previous)

    def test_repeated_stage_freezes_prior_output_and_mode_change_advances(self):
        self.layout(["outline", "outline"])
        calls = []
        def execute(db, pid, prompt, **kwargs):
            calls.append(kwargs)
            workflow.doc_write(db, pid, "outline", "# Output " + str(len(calls)))
            return {"reply": "Round " + str(len(calls))}
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", side_effect=execute):
            run = engine.advance(self.db, run["id"])
            first = tasks.find_origin(self.db, "workflow_node", run["current_node"])
            output = Path(first["outputs"][0]["path"])
            original = output.read_bytes()
            workflow_tasks.set_mode(self.db, run["id"], "full_access")
            run = engine.advance(self.db, run["id"])
        self.assertEqual(run["status"], "succeeded", run["error"])
        self.assertEqual(output.read_bytes(), original)
        for task in tasks.list_tasks(self.db, self.pid, workflow_run_id=run["id"]):
            self.assertEqual(task["status"], "delivered")
            self.assertEqual(task["deliveries"][-1]["source"], "policy")
        self.assertEqual(len(calls), 2)

    def test_missing_roles_never_fall_back_and_role_changes_invalidate_prepared_task(self):
        layout = self.layout(["task"])
        bindings.register(self.db, self.pid, "assistant", "builtin-steward", enabled=False)
        with self.assertRaisesRegex(ValueError, "停用"):
            engine.create(self.db, self.pid)
        self.assertFalse(tasks.list_tasks(self.db, self.pid))

    def test_changed_template_keeps_prepared_run_frozen(self):
        layout = self.layout(["task"])
        run = engine.create(self.db, self.pid)
        layout["nodes"][0]["prompt"] = "Changed after prepare"
        workflow_layout.save_layout(self.db, layout)
        with patch.object(workflow, "run_agent", return_value={'reply':'Original task evidence and limitations'}) as model:
            run = engine.advance(self.db, run["id"])
            model.assert_called_once()
            self.assertNotIn('Changed after prepare',model.call_args.args[2])
        self.assertEqual(run["status"], "awaiting_review",run['error'])
        self.assertNotEqual(run['layout']['nodes'][0]['prompt'],'Changed after prepare')

    def test_http_run_id_cannot_change_another_projects_mode(self):
        self.layout(["task"])
        run = engine.create(self.db, self.pid)
        other = self.db.create_task("Other", base_dir=str(Path(self.tmp.name) / "other"))
        for action in ("prepare", "start"):
            with self.subTest(action=action), patch.object(engine, "kick") as kick:
                with self.assertRaisesRegex(ValueError, "不属于当前项目"):
                    engine.route("POST", "/api/workflow/execution/" + action,
                        {"id": run["id"], "task_id": other, "autonomy_mode": "full_access"}, {}, self.db)
                kick.assert_not_called()
        task = tasks.get(self.db, run["settings"]["task_ids"]["n0"])
        self.assertNotIn("_autonomy_override", task["metadata"])

    def test_cancelling_prepared_tasks_preserves_previous_stage_delivery(self):
        self.layout(["outline"])
        workflow.doc_write(self.db, self.pid, "outline", "# Accepted working document")
        delivery = workflow_state.accept(self.db, self.pid, "outline")
        run = engine.create(self.db, self.pid)
        workflow_tasks.cancel(self.db, run["id"])
        state = workflow_state.states(self.db, self.pid)["outline"]
        self.assertEqual(state["status"], "delivered")
        self.assertEqual(state["delivery_id"], delivery["id"])

    def test_assistants_and_teams_share_one_flow_without_losing_member_limits(self):
        layout = self.layout(["task", "task"])
        office = self.db.team_add("Evidence", "custom", project_id=self.pid)
        member = self.db.team_member_add(office["id"], "Reader", "leader", assistant_id="builtin-steward")
        grant = bindings.register(self.db, self.pid, "team", office["id"])
        bindings.bind_role(self.db, self.pid, layout["id"], "role1", grant["id"], member_id=member["id"])
        run = engine.create(self.db, self.pid)
        with patch.object(workflow, "run_agent", return_value={"reply": "Actual report"}):
            workflow_tasks.set_mode(self.db, run["id"], "full_access")
            run = engine.advance(self.db, run["id"])
        self.assertEqual(run["status"], "succeeded", run["error"])
        self.assertEqual(len(run["nodes"]), 2)
        second = tasks.find_origin(self.db, "workflow_node", run["nodes"][1]["id"])
        self.assertEqual(second["latest_run"]["executor_snapshot"]["member_id"], member["id"])

    def test_team_children_aggregate_and_resume_the_workflow_automatically(self):
        import project_task_execution as worker
        layout = self.layout(["task", "task"])
        office = self.db.team_add("Evidence", "custom", project_id=self.pid)
        self.db.team_member_add(office["id"], "Lead", "leader", assistant_id="builtin-steward")
        member = self.db.team_member_add(office["id"], "Reader", "member", assistant_id="builtin-steward")
        grant = bindings.register(self.db, self.pid, "team", office["id"])
        bindings.bind_role(self.db, self.pid, layout["id"], "role0", grant["id"])
        run = engine.create(self.db, self.pid)
        workflow_tasks.set_mode(self.db, run["id"], "full_access")
        pending = []
        def execute(db, pid, prompt, **kwargs):
            task = tasks.get(db, db.project_task_context.parent_task_id)
            if task["workflow_node_id"] == "n0" and not task["parent_task_id"] and not task["children"]:
                child = tasks.create(db, {"goal": "Read evidence", "executor_binding_id": grant["id"], "member_id": member["id"]}, authority=db.project_task_context)
                pending.append(tasks.start(db, child["id"], authority=db.project_task_context)["id"])
            return {"reply": "Actual report for " + task["title"]}
        with patch.object(workflow, "run_agent", side_effect=execute):
            stopped = engine.advance(self.db, run["id"])
            self.assertEqual(stopped["status"], "blocked")
            worker.kick(self.db.path, pending[0])
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                completed = engine.detail(self.db, run["id"])
                active = any(key[0] == str(self.db.path.resolve()) for key in (*engine._workers, *worker._workers))
                if completed["status"] == "succeeded" and not active:
                    break
                time.sleep(.03)
            self.assertEqual(completed["status"], "succeeded", completed["error"])
        roots = [t for t in tasks.list_tasks(self.db, self.pid, workflow_run_id=run["id"]) if not t["parent_task_id"]]
        self.assertTrue(all(t["status"] == "delivered" for t in roots))
        self.assertEqual(len(roots[0]["runs"]) + len(roots[1]["runs"]), 3)
        self.assertEqual(len([n for n in completed["nodes"] if n["status"] == "delivered"]), 2)

    def test_tools_inherit_parent_scope_and_cannot_forge_user_mode_or_review(self):
        layout = self.layout(["task"])
        run = engine.create(self.db, self.pid)
        parent = tasks.get(self.db, run["settings"]["task_ids"]["n0"])
        self.db.project_task_context = tasks.CommandContext.agent(self.pid, parent["id"])
        created = workflow._tool_impl("task_create", self.db, self.pid,
            {"goal": "Child", "executor_binding_id": self.grant["id"]})["task"]
        self.assertEqual(created["workflow_run_id"], run["id"])
        self.assertEqual(created["parent_task_id"], parent["id"])
        for name, args in (("task_start", {"id": created["id"], "autonomy_mode": "full_access"}),
                           ("workflow_prepare", {"template_id": layout["id"]}),
                           ("workflow_start", {"id": run["id"]})):
            with self.subTest(name=name), self.assertRaises(ValueError):
                workflow._tool_impl(name, self.db, self.pid, args)
        self.assertNotIn("task_review", {s["function"]["name"] for s in workflow.TOOL_SPECS})
        stopped = workflow._tool_impl("workflow_cancel", self.db, self.pid, {"id": run["id"]})["run"]
        self.assertEqual(stopped["status"], "cancelled")
        self.assertTrue(all(t["status"] == "cancelled" for t in tasks.list_tasks(self.db, self.pid, workflow_run_id=run["id"])))

    def test_old_deliveries_and_waiting_review_are_adopted_without_reexecution(self):
        layout = workflow_layout.save_layout(self.db, {"name": "Legacy", "nodes": [
            {"id": "outline", "step": "outline", "assistant_id": "builtin-steward", "review_required": False},
            {"id": "write", "step": "write", "assistant_id": "builtin-steward"}],
            "edges": [{"source": "outline", "target": "write"}]})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout["id"]})
        run = engine.create(self.db, self.pid)
        def execute(db, pid, prompt, **kwargs):
            workflow.doc_write(db, pid, kwargs["scope"], "# Original output")
            return {"reply": "Original result"}
        with patch.object(workflow, "run_agent", side_effect=execute):
            run = engine.advance(self.db, run["id"])
        self.assertEqual(run["status"], "awaiting_review", run["error"])
        old_ids = [n["id"] for n in run["nodes"]]
        original_step_id = run["nodes"][0]["checkpoint"]["delivery_id"]
        for task_id in run["settings"].pop("task_ids").values():
            self.db.db.execute("DELETE FROM project_tasks WHERE id=?", (task_id,))
        run["settings"].pop("legacy_stage_compat", None)
        self.db.db.execute("UPDATE workflow_executions SET settings=? WHERE run_id=?", (json.dumps(run["settings"]), run["id"]))
        self.db.db.commit()
        with patch.object(workflow, "run_agent") as model:
            engine.review(self.db, run["id"], run["current_node"], True, "Upgrade checked")
            restored = engine.advance(self.db, run["id"])
            model.assert_not_called()
        self.assertEqual(restored["status"], "succeeded", restored["error"])
        self.assertEqual([n["id"] for n in restored["nodes"]], old_ids)
        self.assertEqual(restored["nodes"][0]["checkpoint"]["delivery_id"], original_step_id)
        first = tasks.find_origin(self.db, "workflow_node", old_ids[0])
        self.assertEqual(first["deliveries"][-1]["source"], "legacy")


if __name__ == "__main__":
    unittest.main()
