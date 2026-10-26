"""Routing boundaries, durable drafts and real single-node delivery behavior."""
import json
import sqlite3
from pathlib import Path
import unittest
from unittest.mock import patch

import file_handoff as handoff
import workflow
import workflow_execution as engine
import workflow_layout
from tests import test_workflow_execution as fixture


class FileHandoffTest(unittest.TestCase):
    setUp = fixture.WorkflowExecutionTest.setUp
    layout = fixture.WorkflowExecutionTest.layout
    execute = fixture.WorkflowExecutionTest.execute

    def setup_file(self, nodes=None):
        self.db.assistant_save("writer", name="Writer", enabled=1)
        self.layout(nodes or [{"step": "outline", "assistant_id": "writer"}, {"step": "write", "assistant_id": "writer"}])
        self.source = self.db.base_dir(self.pid) / "input.md"
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_text("ORIGINAL_INPUT", encoding="utf-8")
        return self.request()

    def request(self, index=0, key="first"):
        data = handoff.targets(self.db, self.pid)
        node = data["nodes"][index]
        actor = node["actors"][0]
        return {"project_id": self.pid, "layout_id": data["layout_id"], "revision": data["revision"],
                "node_id": node["id"], **{k: actor[k] for k in ("assistant_id", "team_id", "member_id")},
                "relative": "input.md", "instruction": "USE_THIS_FILE", "request_key": key}

    def test_targets_are_read_only_and_never_include_global_actors(self):
        self.setup_file()
        self.db.assistant_save("outside", name="Outside", enabled=1)
        self.db.team_add("Unrelated", "custom", project_id=self.pid)
        before = self.db.db.total_changes
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE}
        self.db.db.set_authorizer(lambda op, *_: sqlite3.SQLITE_DENY if op in denied else sqlite3.SQLITE_OK)
        try:
            data = handoff.targets(self.db, self.pid)
        finally:
            self.db.db.set_authorizer(None)
        self.assertEqual(before, self.db.db.total_changes)
        self.assertEqual({a["assistant_id"] for n in data["nodes"] for a in n["actors"]}, {"writer"})
        self.assertEqual(len(data["nodes"]), 2)

    def test_prepare_is_durable_idempotent_and_does_not_start_execution(self):
        body = self.setup_file()
        with patch.object(engine, "kick") as kick:
            one = handoff.prepare(self.db, body)
            self.assertEqual(one["status"], "draft")
            self.assertEqual(one["id"], handoff.prepare(self.db, body)["id"])
            self.assertFalse(one["run_id"])
            kick.assert_not_called()
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual(handoff.detail(reopened, one["id"])["payload"]["instruction"], "USE_THIS_FILE")
        with self.assertRaisesRegex(ValueError, "另一份任务"):
            handoff.prepare(self.db, {**body, "instruction": "CHANGED"})

    def test_forged_actor_node_and_cross_project_file_are_rejected(self):
        body = self.setup_file()
        for changes in ({"assistant_id": "builtin-steward"}, {"node_id": "review"},
                        {"relative": "../outside.md"}, {"relative": str(self.source)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                handoff.prepare(self.db, {**body, **changes})

    def test_switching_workflow_or_disabling_actor_invalidates_open_picker(self):
        body = self.setup_file()
        self.db.assistant_save("writer", enabled=0)
        with self.assertRaisesRegex(ValueError, "已变化"):
            handoff.prepare(self.db, body)
        self.assertTrue(handoff.targets(self.db, self.pid)["nodes"][0]["disabled_reason"])
        self.db.assistant_save("writer", enabled=1)
        body = self.request()
        self.layout([{"step": "outline", "assistant_id": "writer"}])
        with self.assertRaisesRegex(ValueError, "已变化"):
            handoff.prepare(self.db, body)

    def test_team_candidates_obey_fixed_member_roles_and_project(self):
        team = self.db.team_add("Team", "custom", project_id=self.pid)
        self.db.assistant_save("writer", name="Writer", enabled=1)
        self.db.assistant_save("second", name="Second", enabled=1)
        leader = self.db.team_member_add(team["id"], "Leader", "leader", assistant_id="writer")
        reviewer = self.db.team_member_add(team["id"], "Reviewer", "reviewer", assistant_id="second")
        self.setup_file([{"step": "outline", "team_id": team["id"], "member_id": leader["id"]}])
        data = handoff.targets(self.db, self.pid)
        self.assertEqual([a["member_id"] for a in data["nodes"][0]["actors"]], [leader["id"]])
        with self.assertRaises(ValueError):
            handoff.prepare(self.db, {**self.request(), "member_id": reviewer["id"], "assistant_id": "second"})
        self.layout([{"step": "outline", "team_id": team["id"], "assistant_id": "second", "member_id": leader["id"]}])
        self.assertIn("冲突", handoff.targets(self.db, self.pid)["nodes"][0]["disabled_reason"])

    def test_unconfigured_nodes_do_not_fall_back_to_steward(self):
        self.layout([{"step": "outline"}])
        node = handoff.targets(self.db, self.pid)["nodes"][0]
        self.assertEqual(node["actors"], [])
        self.assertIn("尚未配置", node["disabled_reason"])

    def test_single_node_run_uses_frozen_file_and_stops_at_review(self):
        body = self.setup_file()
        item = handoff.prepare(self.db, body)
        item = handoff.start(self.db, item["id"])
        self.assertEqual(item["run_id"], handoff.start(self.db, item["id"])["run_id"])
        self.source.write_text("NEW_ORIGINAL", encoding="utf-8")
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, item["run_id"])
            self.assertEqual(run["status"], "awaiting_review", run.get("error"))
            self.assertEqual([x["scope"] for x in self.seen], ["outline"])
            snapshot = run["nodes"][0]["checkpoint"]["input"]["file_input"]
            self.assertEqual(Path(snapshot["path"]).read_text(encoding="utf-8"), "ORIGINAL_INPUT")
            self.assertIn("USE_THIS_FILE", self.seen[0]["extra_context"])
            engine.review(self.db, run["id"], run["current_node"], True)
            self.assertEqual(engine.advance(self.db, run["id"])["status"], "succeeded")
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(handoff.detail(self.db, item["id"])["status"], "succeeded")

    def test_dependencies_and_active_runs_keep_new_material_pending(self):
        self.setup_file()
        downstream = handoff.prepare(self.db, self.request(1))
        waiting = handoff.start(self.db, downstream["id"])
        self.assertFalse(waiting["run_id"])
        self.assertIn("前置", waiting["waiting_reason"])
        first = handoff.prepare(self.db, self.request(key="upstream"))
        running = handoff.start(self.db, first["id"])
        next_item = handoff.prepare(self.db, self.request(key="supplement"))
        self.assertFalse(handoff.start(self.db, next_item["id"])["run_id"])
        self.assertEqual(handoff.detail(self.db, running["id"])["run"]["settings"]["handoff_id"], first["id"])
        with self.assertRaisesRegex(ValueError, "文件节点任务"):
            engine.create(self.db, self.pid)

    def test_file_change_after_prepare_and_config_change_after_start_block_model(self):
        item = handoff.prepare(self.db, self.setup_file())
        self.source.write_text("EDITED", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "原文件已变化"):
            handoff.start(self.db, item["id"])
        item = handoff.prepare(self.db, self.request(key="edited"))
        item = handoff.start(self.db, item["id"])
        self.db.assistant_save("writer", enabled=0)
        with patch.object(workflow, "run_agent") as model:
            run = engine.advance(self.db, item["run_id"])
            self.assertEqual(run["status"], "failed")
            model.assert_not_called()

    def test_team_delivery_is_only_done_after_node_acceptance(self):
        self.db.assistant_save("writer", name="Writer", enabled=1)
        team = self.db.team_add("Team", "custom", project_id=self.pid)
        member = self.db.team_member_add(team["id"], "Leader", "leader", assistant_id="writer")
        body = self.setup_file([{"step": "outline", "team_id": team["id"], "member_id": member["id"]}])
        item = handoff.start(self.db, handoff.prepare(self.db, body)["id"])
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, item["run_id"])
        self.assertEqual(run["status"], "awaiting_review", run["error"])
        self.assertEqual(self.db.team_tasks(team["id"])[0]["status"], "review")
        engine.review(self.db, run["id"], run["current_node"], True)
        self.assertEqual(self.db.team_tasks(team["id"])[0]["status"], "done")

    def test_supplement_preserves_original_task_and_carries_both_inputs(self):
        body = self.setup_file()
        parent = handoff.prepare(self.db, body)
        other = self.source.with_name("extra.md")
        other.write_text("EXTRA_INPUT", encoding="utf-8")
        child = handoff.prepare(self.db, {**body, "parent_id": parent["id"], "relative": "extra.md",
                                          "instruction": "SUPPLEMENT_GOAL", "request_key": "extra"})
        started = handoff.start(self.db, child["id"])
        related = started["run"]["settings"]["file_input"]["related_inputs"]
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0]["instruction"], "USE_THIS_FILE")
        self.assertEqual(Path(related[0]["path"]).read_text(encoding="utf-8"), "ORIGINAL_INPUT")
        self.assertEqual(handoff.detail(self.db, parent["id"])["status"], "draft")
        with self.assertRaisesRegex(ValueError, "只能补充"):
            handoff.prepare(self.db, {**self.request(1, key="wrong-node"), "parent_id": parent["id"]})

    def test_absent_workflow_does_not_create_implicit_binding(self):
        self.layout([{"step": "outline"}])
        self.db.db.execute("DELETE FROM project_workflow_layouts WHERE task_id=?", (self.pid,))
        self.db.db.commit()
        with self.assertRaisesRegex(ValueError, "尚未绑定"):
            handoff.targets(self.db, self.pid)
        self.assertFalse(self.db.db.execute("SELECT 1 FROM project_workflow_layouts WHERE task_id=?", (self.pid,)).fetchone())

    def test_running_manual_node_also_keeps_material_pending(self):
        item = handoff.prepare(self.db, self.setup_file())
        self.db.log_step(self.pid, "outline", "running", "Manual node execution")
        waiting = handoff.start(self.db, item["id"])
        self.assertFalse(waiting["run_id"])
        self.assertIn("正在执行", waiting["waiting_reason"])

    def legacy_offices(self, layout_id="builtin.workflow.research"):
        self.setup_file()
        for aid in ("builtin-literature-lead", "builtin-writing-lead", "builtin-searcher"):
            self.db.assistant_save(aid, enabled=1)
        literature = self.db.team_add("Renamed literature office", "fixed", project_id=self.pid)
        writing = self.db.team_add("Renamed writing office", "fixed", project_id=self.pid)
        self.db.team_member_add(literature["id"], "Lead", "leader", assistant_id="builtin-literature-lead")
        self.db.team_member_add(literature["id"], "Searcher", "member", assistant_id="builtin-searcher")
        self.db.team_member_add(writing["id"], "Lead", "leader", assistant_id="builtin-writing-lead")
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": layout_id})
        return literature, writing

    def test_legacy_builtin_workflow_reads_existing_project_offices_without_writes(self):
        literature, writing = self.legacy_offices()
        extra = self.db.team_add("Unrelated office", "custom", project_id=self.pid)
        self.db.team_member_add(extra["id"], "Other", "leader", assistant_id="writer")
        foreign = self.db.team_add("Foreign office", "fixed", project_id="other-project")
        self.db.team_member_add(foreign["id"], "Other", "leader", assistant_id="builtin-writing-lead")
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE}
        self.db.db.set_authorizer(lambda op, *_: sqlite3.SQLITE_DENY if op in denied else sqlite3.SQLITE_OK)
        try:
            data = handoff.targets(self.db, self.pid)
        finally:
            self.db.db.set_authorizer(None)
        self.assertEqual({a["team_id"] for n in data["nodes"] for a in n["actors"]}, {literature["id"], writing["id"]})
        self.assertEqual({a["assistant_id"] for n in data["nodes"] for a in n["actors"]},
                         {"builtin-literature-lead", "builtin-writing-lead", "builtin-searcher"})
        search_nodes = [n["step"] for n in data["nodes"] if any(a["assistant_id"] == "builtin-searcher" for a in n["actors"])]
        self.assertEqual(search_nodes, ["search"])
        self.db.assistant_save("builtin-searcher", name="Renamed expert", enabled=0)
        self.assertFalse(any(a["assistant_id"] == "builtin-searcher" for n in handoff.targets(self.db, self.pid)["nodes"] for a in n["actors"]))

    def test_legacy_offices_do_not_leak_into_custom_or_explicitly_bound_nodes(self):
        literature, writing = self.legacy_offices()
        self.layout([{"step": "outline"}])
        self.assertFalse(handoff.targets(self.db, self.pid)["nodes"][0]["actors"])
        saved = workflow_layout._layout(self.db, "builtin.workflow.writing")
        saved["nodes"][0].update(assistant_id="writer", execution_mode="assistant")
        workflow_layout.save_layout(self.db, saved)
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": saved["id"]})
        node = handoff.targets(self.db, self.pid)["nodes"][0]
        self.assertEqual([a["assistant_id"] for a in node["actors"]], ["writer"])
        self.assertFalse(node["actors"][0]["team_id"])
        self.db.assistant_save("writer", enabled=0)
        self.assertFalse(handoff.targets(self.db, self.pid)["nodes"][0]["actors"])

    def test_legacy_office_target_runs_as_the_selected_team_member(self):
        literature, writing = self.legacy_offices("builtin.workflow.writing")
        request = self.request()
        self.assertEqual(request["assistant_id"], "builtin-writing-lead")
        item = handoff.start(self.db, handoff.prepare(self.db, request)["id"])
        with patch.object(workflow, "run_agent", side_effect=self.execute):
            run = engine.advance(self.db, item["run_id"])
        self.assertEqual(run["status"], "awaiting_review", run["error"])
        self.assertEqual(self.seen[0]["assistant_id"], "builtin-writing-lead")
        self.assertEqual(self.seen[0]["member"]["task_id"], writing["id"])
        self.assertTrue(run["nodes"][0]["checkpoint"]["team_task_id"])

    def old_offices(self):
        self.setup_file()
        for aid in ("builtin-literature-lead", "builtin-writing-lead", "builtin-searcher"):
            self.db.assistant_save(aid, enabled=1)
        offices = []
        for name in ("文献抽取", "文件撰写"):
            office = self.db.team_add(name, "fixed", project_id=self.pid)
            leader = self.db.team_member_add(office["id"], "智研管家", "leader", assistant_id="", prompt="KEEP_CUSTOM_PROMPT")
            offices.append((office, leader))
        self.db.team_member_add(offices[0][0]["id"], "文献检索员", "member", assistant_id="builtin-searcher")
        workflow_layout.select_layout(self.db, {"task_id": self.pid, "layout_id": "builtin.workflow.research"})
        return offices

    def test_old_project_leaders_are_diagnosed_and_repaired_in_place(self):
        offices = self.old_offices()
        task = self.db.team_task_add(offices[0][0]["id"], offices[0][1]["id"], "智研管家", "Keep task", "task input")
        before = self.db.db.total_changes
        data = handoff.targets(self.db, self.pid)
        self.assertEqual(self.db.db.total_changes, before)
        self.assertEqual(data["configuration"]["legacy_leaders"], 2)
        self.assertFalse(any(n["actors"] for n in data["nodes"]))
        result = handoff.repair_configuration(self.db, {"project_id": self.pid, "layout_id": data["layout_id"]})
        self.assertEqual(result["repaired_leaders"], 2)
        self.assertTrue(all(n["actors"] for n in result["targets"]["nodes"]))
        for office, leader in offices:
            changed = self.db.team_member(leader["id"])
            self.assertEqual(changed["prompt"], "KEEP_CUSTOM_PROMPT")
            self.assertEqual(changed["id"], leader["id"])
            self.assertIn(changed["assistant_id"], ("builtin-literature-lead", "builtin-writing-lead"))
        self.assertEqual(self.db.team_tasks(offices[0][0]["id"])[0]["id"], task["id"])
        self.assertEqual(handoff.repair_configuration(self.db, {"project_id": self.pid, "layout_id": data["layout_id"]})["repaired_leaders"], 0)

    def test_database_startup_migrates_old_leaders_without_visiting_team_page(self):
        self.old_offices()
        import team_configuration
        self.db.db.execute("DELETE FROM service_settings WHERE service=?", (team_configuration.MIGRATION,))
        self.db.db.commit()
        with patch("workflow_db._WORKFLOW_SCHEMA_INITIALIZED", set()):
            with workflow.WorkflowDB(self.db.path) as reopened:
                data = handoff.targets(reopened, self.pid)
                self.assertEqual(data["configuration"]["legacy_leaders"], 0)
                self.assertTrue(all(n["actors"] for n in data["nodes"]))

    def test_leader_repair_keeps_custom_assignments_and_disabled_assistants(self):
        offices = self.old_offices()
        custom = self.db.team_add("Custom office", "custom", project_id=self.pid)
        custom_leader = self.db.team_member_add(custom["id"], "智研管家", "leader", assistant_id="")
        self.db.assistant_save("builtin-writing-lead", enabled=0)
        self.db.team_member_update(offices[0][1]["id"], assistant_id="writer", name="User leader")
        data = handoff.targets(self.db, self.pid)
        result = handoff.repair_configuration(self.db, {"project_id": self.pid, "layout_id": data["layout_id"]})
        self.assertEqual(result["repaired_leaders"], 1)
        self.assertEqual(self.db.team_member(offices[0][1]["id"])["assistant_id"], "writer")
        self.assertEqual(self.db.team_member(custom_leader["id"])["assistant_id"], "")
        self.assertFalse(self.db.assistant("builtin-writing-lead")["enabled"])

    def test_repair_is_project_scoped_and_checks_active_layout(self):
        offices = self.old_offices()
        other = self.db.team_add("文献抽取", "fixed", project_id="other-project")
        leader = self.db.team_member_add(other["id"], "智研管家", "leader", assistant_id="")
        with self.assertRaisesRegex(ValueError, "已切换"):
            handoff.repair_configuration(self.db, {"project_id": self.pid, "layout_id": "other-layout"})
        data = handoff.targets(self.db, self.pid)
        handoff.repair_configuration(self.db, {"project_id": self.pid, "layout_id": data["layout_id"]})
        self.assertEqual(self.db.team_member(leader["id"])["assistant_id"], "")
