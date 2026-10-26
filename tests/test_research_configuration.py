"""Research configuration reaches the actual teams, workflow and deliverables."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_settings
import papertran
import reproduction_routes
import service_settings
import team
import workflow
import workflow_layout
import workflow_state
from _pdf_fixture import write_text_pdf


class ResearchConfigurationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="research-config-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Research", base_dir=str(self.root / "project"))
        db_patch = patch.object(team, "DB_PATH", self.db.path)
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def tool(self, tool_name, **args):
        return workflow._tool_impl(tool_name, self.db, self.project, args)

    def test_catalog_and_legacy_labels_match_platform_without_losing_custom_names(self):
        listing = self.tool("workflow_manage", action="list")
        self.assertEqual([s["id"] for s in listing["stages"]], workflow.STAGES)
        for layout in listing["layouts"]:
            for node in layout["nodes"]:
                self.assertEqual(node["label"], workflow.STAGE_LABEL[node["step"]])
        layout = listing["layouts"][0]
        layout["nodes"][1]["label"] = "My custom search"
        layout["nodes"][3]["label"] = "文献精读"
        self.db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?",
                           (json.dumps({k: layout[k] for k in ("nodes", "edges", "order")}), layout["id"]))
        self.db.db.commit()
        service_settings.ensure(self.db)
        self.db.db.execute("DELETE FROM service_settings WHERE service='workflow_catalog'")
        self.db.db.commit()
        with workflow.WorkflowDB(self.db.path) as reopened:
            migrated = workflow_layout.route("GET", "/api/workflow/layouts", None, {}, reopened)["layouts"][0]
        self.assertEqual(migrated["nodes"][1]["label"], "My custom search")
        self.assertEqual(migrated["nodes"][3]["label"], "论文翻译")
        self.assertEqual(workflow.STEP_AGENT["notes"], "翻译助手")

    def test_saved_custom_label_is_preserved_when_it_matches_a_legacy_name(self):
        self.tool("workflow_manage", action="list")
        definition = workflow_layout._linear(["notes"])
        definition["nodes"][0]["label"] = "文献精读"
        layout = workflow_layout.save_layout(self.db, {"name": "User chosen name", **definition})["layout"]
        with workflow.WorkflowDB(self.db.path) as reopened:
            workflow_layout.ensure_schema(reopened)
            saved = workflow_layout._layout(reopened, layout["id"])
        self.assertEqual(saved["nodes"][0]["label"], "文献精读")

    def test_removed_roster_stays_empty_after_refresh_and_reopen(self):
        office = team.teams(self.project)["teams"][0]
        for member in office["members"]:
            team.remove_member(member["id"])
        after = next(t for t in team.teams(self.project)["teams"] if t["id"] == office["id"])
        self.assertEqual(after["members"], [])
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual([m for m in reopened.team_members(office["id"]) if m["role"] == "member"], [])

    def test_preset_roster_uses_expert_ids_when_experts_are_renamed(self):
        self.db.assistant_save("builtin-searcher", name="My search expert")
        offices = team.teams(self.project)["teams"]
        literature = next(t for t in offices if t["leader"]["assistant_id"] == "builtin-literature-lead")
        self.assertIn("builtin-searcher", {m["assistant_id"] for m in literature["members"]})

    def test_invalid_member_runtime_configuration_does_not_leave_partial_member(self):
        office = self.tool("team_create", name="Team")["team"]
        before = {m["id"] for m in self.db.team_members(office["id"])}
        for invalid in ({"model_id": "missing-model"}, {"reasoning": "invalid"}, {"permission": "invalid"}):
            with self.subTest(invalid=invalid), self.assertRaises((KeyError, ValueError)):
                agent_settings.route("POST", "/api/agent-settings/member-save", {
                    "team_id": office["id"], "name": "Invalid member", **invalid}, {}, self.db)
            self.assertEqual({m["id"] for m in self.db.team_members(office["id"])}, before)

    def test_editing_member_cannot_duplicate_an_existing_expert(self):
        created = self.tool("team_create", name="Team", assistant_ids=["builtin-searcher", "builtin-reviewer"])
        leader, member = created["members"]
        with self.assertRaisesRegex(ValueError, "已在"):
            self.tool("team_manage", action="update_member", team_id=created["team"]["id"],
                      member_id=member["id"], assistant_id=leader["assistant_id"])
        self.assertEqual(self.db.team_member(member["id"])["assistant_id"], "builtin-reviewer")

    def test_fixed_teams_have_distinct_leaders_and_renaming_does_not_duplicate_them(self):
        offices = team.teams(self.project)["teams"]
        self.assertEqual({t["leader"]["assistant_id"] for t in offices}, set(team.FIXED_LEADERS.values()))
        for office in offices:
            self.assertNotEqual(office["leader"]["name"], "智研管家")
            self.assertTrue(office["leader"]["prompt"])
        chosen = offices[0]
        team.rename_team(chosen["id"], "My renamed office")
        leader = chosen["leader"]
        self.db.team_member_update(leader["id"], prompt="My custom leadership rules", reasoning="high")
        after = team.teams(self.project)["teams"]
        self.assertEqual({t["id"] for t in after}, {t["id"] for t in offices})
        saved = next(t for t in after if t["id"] == chosen["id"])["leader"]
        self.assertEqual(saved["id"], leader["id"])
        self.assertEqual(saved["prompt"], "My custom leadership rules")
        self.assertEqual(saved["reasoning"], "high")

    def test_legacy_leader_migration_keeps_members_models_and_history(self):
        office = self.db.team_add("文献抽取", "fixed", project_id=self.project)
        leader = self.db.team_member_add(office["id"], "智研管家", "leader", assistant_id="builtin-steward")
        model = self.db.add_model("Fake", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)["id"]
        self.db.team_member_update(leader["id"], model_id=model, reasoning="high", permission="read_only")
        member = self.db.team_member_add(office["id"], "Existing member")
        session = agent_settings.session(self.db, office["id"])
        self.db.add_message(office["id"], "user", "Keep this discussion", scope="team:" + session["id"])
        migrated = team.ensure_leader(office["id"], self.db)
        self.assertEqual(migrated["id"], leader["id"])
        self.assertEqual(migrated["assistant_id"], "builtin-literature-lead")
        self.assertEqual((migrated["model_id"], migrated["reasoning"], migrated["permission"]), (model, "high", "read_only"))
        self.assertIsNotNone(self.db.team_member(member["id"]))
        self.assertEqual(team.transcript(office["id"])[0]["content"], "Keep this discussion")
        self.assertEqual(team.ensure_leader(office["id"], self.db)["id"], leader["id"])

    def test_professional_leader_starts_actual_team_relay(self):
        office = team.teams(self.project)["teams"][0]
        self.db.add_model("Fake", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)
        systems = []

        def complete(profile, messages, **kwargs):
            systems.extend(m["content"] for m in messages if m["role"] == "system")
            return {"content": "完成", "tool_calls": []}

        with patch.object(workflow, "chat_raw", complete):
            team._round(office["id"], 1)
        self.assertIn(office["leader"]["name"], systems[0])
        self.assertNotIn("首页身份是", systems[0])
        self.assertIn(office["leader"]["name"], {m["sender"] for m in team.transcript(office["id"])})

    def test_steward_configures_existing_team_and_selects_workflow(self):
        office = team.teams(self.project)["teams"][0]
        listed = self.tool("team_manage", action="list")["teams"]
        self.assertEqual(len(listed), 2)
        self.tool("team_manage", action="update", team_id=office["id"], description="Configured by steward")
        member = self.tool("team_manage", action="add_member", team_id=office["id"],
                           assistant_id="builtin-reviewer", name="Evidence reviewer")["member"]
        self.tool("team_manage", action="update_member", team_id=office["id"], member_id=member["id"], prompt="Check every source")
        self.tool("team_manage", action="set_leader", team_id=office["id"], member_id=member["id"])
        members = team.members(office["id"])
        self.assertEqual(members[0]["id"], member["id"])
        self.assertEqual(members[0]["prompt"], "Check every source")
        self.assertEqual(self.db.team(office["id"])["description"], "Configured by steward")
        state = self.tool("workflow_manage", action="select", layout_id="builtin.workflow.writing")
        self.assertEqual(state["nodes"][0]["step"], "outline")
        self.assertEqual(workflow_state.definition(self.db, self.project)["id"], "builtin.workflow.writing")

    def test_configuration_respects_project_and_permission_boundaries(self):
        office = self.tool("team_create", name="Custom team")["team"]
        self.assertEqual(office["kind"], "custom")
        self.assertNotEqual(team.members(office["id"])[0]["name"], "智研管家")
        other = self.db.create_task("Other")
        foreign = self.db.team_add("Foreign", "custom", project_id=other)
        with self.assertRaises(PermissionError):
            self.tool("team_manage", action="update", team_id=foreign["id"], name="Wrong project")
        with self.assertRaises(PermissionError):
            self.tool("team_manage", action="update", team_id=office["id"], permission="full_access")
        for name in ("team_manage", "workflow_manage"):
            workflow.enforce_tool_policy(name, {"action": "list"}, "read_only")
            with self.assertRaises(PermissionError):
                workflow.enforce_tool_policy(name, {"action": "select"}, "read_only")
        workflow.enforce_tool_policy("paper_translate", {"action": "status"}, "read_only")
        with self.assertRaises(PermissionError):
            workflow.enforce_tool_policy("paper_translate", {"action": "start"}, "read_only")

    def test_steward_tool_loop_persists_configuration(self):
        office = self.tool("team_create", name="Team")["team"]
        self.db.add_model("Fake", "http://127.0.0.1:1/v1", "fake", "sk", activate=True)
        calls = [
            {"id": "team-edit", "type": "function", "function": {"name": "team_manage", "arguments": json.dumps({
                "action": "update", "team_id": office["id"], "description": "Saved through the agent"})}},
            {"id": "workflow-select", "type": "function", "function": {"name": "workflow_manage", "arguments": json.dumps({
                "action": "select", "layout_id": "builtin.workflow.writing"})}},
        ]
        responses = [{"content": "", "tool_calls": calls}, {"content": "Configuration saved", "tool_calls": []}]
        with patch.object(workflow, "chat_raw", side_effect=responses):
            result = workflow.run_agent(self.db, self.project, "Configure my team and workflow", publish=False)
        self.assertEqual(result["reply"], "Configuration saved")
        self.assertEqual(self.db.team(office["id"])["description"], "Saved through the agent")
        self.assertEqual(workflow_state.definition(self.db, self.project)["id"], "builtin.workflow.writing")
        self.assertFalse(any(event.get("type") == "tool_error" for event in result["events"]))

    def test_steward_cannot_be_promoted_and_compatibility_route_persists_leader(self):
        office = self.tool("team_create", name="Team")["team"]
        steward = self.db.team_member_add(office["id"], "智研管家", assistant_id="builtin-steward")
        with self.assertRaisesRegex(ValueError, "专业负责人"):
            agent_settings.set_leader(self.db, office["id"], steward["id"])
        with self.assertRaisesRegex(ValueError, "专业负责人"):
            self.tool("team_create", name="Bad team", assistant_ids=["builtin-steward"])
        expert = self.db.team_member_add(office["id"], "Expert", assistant_id="builtin-reviewer")
        result = reproduction_routes.route("POST", f"/api/teams/{office['id']}/leader",
                                           {"member_id": expert["id"]}, {}, self.db)
        self.assertEqual(result["leader_id"], expert["id"])
        self.assertEqual(team.members(office["id"])[0]["id"], expert["id"])

    def test_translation_acceptance_requires_finished_translation_files(self):
        workflow_layout.ensure_schema(self.db)
        layout = workflow_layout.save_layout(self.db, {"name": "Translate", **workflow_layout._linear(["notes"])})["layout"]
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": layout["id"]})
        source = write_text_pdf(self.db.pdir(self.project, "文献库") / "source.pdf", ["A research paper"])
        paper = self.db.ingest_pdf(self.project, source)
        self.db.add_note(self.project, "Reading notes", "Notes are not a translation", paper["id"])
        self.assertFalse(workflow_state.capture(self.db, self.project, "notes")["valid"])
        with self.assertRaisesRegex(ValueError, "完整产物"):
            workflow_state.accept(self.db, self.project, "notes")
        out = self.db.pdir(self.project, "文献库")
        _, mono = papertran.out_paths(source, out)
        write_text_pdf(mono, ["Translated paper"])
        papertran._save_state(out, paper["id"], 1, 2, False)
        self.assertFalse(workflow_state.capture(self.db, self.project, "notes")["valid"])
        papertran._save_state(out, paper["id"], 2, 2, True)
        self.assertTrue(workflow_state.capture(self.db, self.project, "notes")["valid"])
        delivery = workflow_state.accept(self.db, self.project, "notes")
        self.assertIn(str(mono), json.loads(delivery["delivery"])["files"])
        mono.unlink()
        self.assertEqual(workflow_state.states(self.db, self.project)["notes"]["status"], "stale")

    def test_translation_tool_uses_project_output_and_rejects_foreign_papers(self):
        source = write_text_pdf(self.db.pdir(self.project, "文献库") / "paper.pdf", ["Research source"])
        paper = self.db.ingest_pdf(self.project, source)
        with patch.object(papertran, "start", return_value={"state": "running"}) as start:
            self.assertEqual(self.tool("paper_translate", paper_id=paper["id"])["state"], "running")
        self.assertEqual(start.call_args.args[2], self.db.pdir(self.project, "文献库"))
        other = self.db.create_task("Other")
        with self.assertRaises(PermissionError), patch.object(papertran, "start") as start:
            workflow._tool_impl("paper_translate", self.db, other, {"paper_id": paper["id"]})
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
