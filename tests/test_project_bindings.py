"""Project grants, workflow role binding and evidence-only legacy migration."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import project_bindings as bindings
import project_todos
import workflow
import workflow_layout


class ProjectBindingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rw-project-bindings-")
        self.addCleanup(self.temporary.cleanup)
        self.db = workflow.WorkflowDB(Path(self.temporary.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.project = self.db.create_task("Binding project", base_dir=str(Path(self.temporary.name) / "project"))
        self.other = self.db.create_task("Other project", base_dir=str(Path(self.temporary.name) / "other"))
        for aid in ("binding-writer", "binding-reader", "binding-outsider"):
            self.db.assistant_save(aid, name=aid, enabled=1)

    def office(self, name="Office", project=None):
        team = self.db.team_add(name, "custom", project_id=project or self.project)
        lead = self.db.team_member_add(team["id"], "Lead", "leader", assistant_id="binding-writer")
        member = self.db.team_member_add(team["id"], "Reader", "member", assistant_id="binding-reader")
        return team, lead, member

    def template(self, *, team_id="", member_id="", assistant_id="", role="writer"):
        workflow_layout.ensure_schema(self.db)
        data = workflow_layout._linear(["outline"])
        data["nodes"][0].update(executor_role_id=role, team_id=team_id, member_id=member_id, assistant_id=assistant_id)
        saved = workflow_layout.save_layout(self.db, {"name": "Role template", **data})["layout"]
        # Exercise the role contract independently of old UI validators.
        self.db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?", (json.dumps(data), saved["id"]))
        self.db.db.commit()
        return saved

    def test_project_grants_remain_scoped_while_global_profiles_are_available(self):
        grant = bindings.register(self.db, self.project, "assistant", "binding-writer")
        shown = bindings.list_bindings(self.db, self.project)
        self.assertTrue({"binding-writer", "binding-reader", "binding-outsider"}.issubset({b["target_id"] for b in shown}))
        self.assertEqual(bindings.resolve(self.db, self.project, grant["id"])["assistant_id"], "binding-writer")
        with self.assertRaisesRegex(ValueError, "当前项目"):
            bindings.resolve(self.db, self.other, grant["id"])
        foreign, _, _ = self.office(project=self.other)
        shared = bindings.register(self.db, self.project, "team", foreign["id"])
        resolved = bindings.resolve(self.db, self.project, shared["id"])
        self.assertEqual(resolved["team"]["project_id"], self.project)
        self.assertNotEqual(resolved["team_id"], foreign["id"])

    def test_list_resolve_settings_and_roles_do_not_write(self):
        grant = bindings.register(self.db, self.project, "assistant", "binding-writer")
        template = self.template()
        bindings.bind_role(self.db, self.project, template["id"], "writer", grant["id"])
        denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE}
        self.db.db.set_authorizer(lambda operation, *_: sqlite3.SQLITE_DENY if operation in denied else sqlite3.SQLITE_OK)
        try:
            self.assertIn(grant["id"], [row["id"] for row in bindings.list_bindings(self.db, self.project)])
            self.assertEqual(bindings.resolve(self.db, self.project, grant["id"])["kind"], "assistant")
            self.assertEqual(bindings.settings(self.db, self.project)["autonomy_mode"], "supervised")
            self.assertTrue(bindings.list_roles(self.db, self.project, template["id"])[0]["available"])
        finally:
            self.db.db.set_authorizer(None)

    def test_legacy_reimport_never_revives_a_disabled_or_restricted_grant(self):
        grant = bindings.register(self.db, self.project, "assistant", "binding-writer", tools=["doc_read"], enabled=False)
        self.db.team_member_add(self.project, "Old direct member", "member", assistant_id="binding-writer")
        bindings.import_legacy(self.db, self.project)
        found = bindings.list_bindings(self.db, self.project)[0]
        self.assertEqual(found["id"], grant["id"])
        self.assertFalse(found["enabled"])
        self.assertEqual(found["tools"], ["doc_read"])
        self.assertEqual(found["source"], "user")
        before = self.db.db.total_changes
        bindings.import_legacy(self.db, self.project)
        self.assertEqual(self.db.db.total_changes, before)

    def test_team_member_is_exact_and_tool_constraints_intersect(self):
        team, leader, member = self.office()
        grant = bindings.register(self.db, self.project, "team", team["id"], tools=["doc_read", "doc_write"])
        bindings.register(self.db, self.project, "assistant", "binding-reader", tools=["doc_read"])
        default = bindings.resolve(self.db, self.project, grant["id"])
        self.assertEqual(default["member_id"], leader["id"])
        self.assertFalse(default["member_selected"])
        exact = bindings.resolve(self.db, self.project, grant["id"], member_id=member["id"])
        self.assertEqual(exact["team_id"], team["id"])
        self.assertEqual(exact["assistant_id"], "binding-reader")
        self.assertEqual(exact["tools"], ["doc_read"])
        self.assertTrue(exact["member_selected"])
        _, _, outsider = self.office("Second team")
        with self.assertRaisesRegex(ValueError, "不属于"):
            bindings.resolve(self.db, self.project, grant["id"], member_id=outsider["id"])
        self.db.team_member_update(member["id"], status="disabled")
        with self.assertRaisesRegex(ValueError, "停用"):
            bindings.resolve(self.db, self.project, grant["id"], member_id=member["id"])

    def test_missing_disabled_or_reviewer_member_never_falls_back(self):
        team, leader, member = self.office()
        grant = bindings.register(self.db, self.project, "team", team["id"])
        self.db.assistant_save("binding-writer", enabled=0)
        with self.assertRaisesRegex(ValueError, "停用"):
            bindings.resolve(self.db, self.project, grant["id"])
        self.db.assistant_save("binding-writer", enabled=1)
        reviewer = self.db.team_member_add(team["id"], "Reviewer", "reviewer", assistant_id="binding-reader")
        with self.assertRaisesRegex(ValueError, "执行资格"):
            bindings.resolve(self.db, self.project, grant["id"], member_id=reviewer["id"])
        self.db.team_member_remove(leader["id"])
        with self.assertRaisesRegex(ValueError, "唯一"):
            bindings.resolve(self.db, self.project, grant["id"])

    def test_disabled_project_assistant_is_denied_even_through_team(self):
        team, _, member = self.office()
        team_grant = bindings.register(self.db, self.project, "team", team["id"])
        bindings.register(self.db, self.project, "assistant", "binding-reader", enabled=False)
        with self.assertRaisesRegex(ValueError, "当前项目中已停用"):
            bindings.resolve(self.db, self.project, team_grant["id"], member_id=member["id"])

    def test_project_defaults_are_independent_from_assistant_tool_permission(self):
        self.db.assistant_save("binding-writer", permission="full_access")
        original = bindings.settings(self.db, self.project)
        self.assertEqual(original["autonomy_mode"], "supervised")
        self.assertEqual(original["revision"], 0)
        saved = bindings.save_settings(self.db, self.project, "full_access")
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(bindings.save_settings(self.db, self.project, "full_access")["revision"], 1)
        self.assertEqual(bindings.settings(self.db, self.other)["autonomy_mode"], "supervised")
        self.assertEqual(bindings.save_settings(self.db, self.project, "supervised")["revision"], 2)
        with self.assertRaises(ValueError):
            bindings.save_settings(self.db, self.project, "auto")

    def test_same_template_role_uses_each_projects_binding_and_fixed_member(self):
        one, _, member_one = self.office()
        two, _, member_two = self.office(project=self.other)
        binding_one = bindings.register(self.db, self.project, "team", one["id"])
        binding_two = bindings.register(self.db, self.other, "team", two["id"])
        template = self.template()
        bindings.bind_role(self.db, self.project, template["id"], "writer", binding_one["id"], member_id=member_one["id"])
        bindings.bind_role(self.db, self.other, template["id"], "writer", binding_two["id"], member_id=member_two["id"])
        self.assertEqual(bindings.resolve_role(self.db, self.project, template["id"], "writer")["member_id"], member_one["id"])
        self.assertEqual(bindings.resolve_role(self.db, self.other, template["id"], "writer")["team_id"], two["id"])
        with self.assertRaises(ValueError):
            bindings.bind_role(self.db, self.project, template["id"], "unknown", binding_one["id"])
        with self.assertRaises(ValueError):
            bindings.bind_role(self.db, self.project, template["id"], "writer", binding_two["id"])
        row = bindings.list_roles(self.db, self.project, template["id"])[0]
        self.assertEqual(row["executor_binding_id"], binding_one["id"])
        self.db.team_member_remove(member_one["id"])
        with self.assertRaisesRegex(ValueError, "移除"):
            bindings.resolve_role(self.db, self.project, template["id"], "writer")

    def test_legacy_import_uses_actual_teams_todos_chats_and_saved_node(self):
        team, _, member = self.office()
        foreign, _, _ = self.office(project=self.other)
        template = self.template(team_id=team["id"], member_id=member["id"])
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": template["id"]})
        self.db.assistant_save("from-todo", name="from-todo", enabled=1)
        self.db.assistant_save("from-chat", name="from-chat", enabled=1)
        todo = project_todos.add_todo(self.db, self.project, "Existing assignment", assigned_assistant="from-todo")
        chat = self.db.create_chat("Bound chat")
        self.db.bind_chat(chat, self.project)
        self.db.set_chat_assistant(chat, "from-chat")
        bindings.import_legacy(self.db, self.project)
        result = bindings.list_bindings(self.db, self.project)
        targets = {(b["kind"], b["target_id"]) for b in result}
        self.assertTrue({("team", team["id"]), ("assistant", "binding-writer"), ("assistant", "binding-reader"), ("assistant", "from-todo"), ("assistant", "from-chat")}.issubset(targets))
        self.assertIn(("team", foreign["id"]), targets)
        self.assertFalse(self.db.db.execute("SELECT 1 FROM project_executor_bindings WHERE project_id=? AND target_id=?", (self.project, foreign["id"])).fetchone())
        self.assertEqual(bindings.resolve_role(self.db, self.project, template["id"], "writer")["member_id"], member["id"])
        sources = {(r["source"], r["source_id"]) for r in self.db.db.execute("SELECT * FROM project_binding_origins")}
        self.assertIn(("todo", todo["id"]), sources)
        self.assertIn(("chat", chat), sources)
        before = self.db.db.total_changes
        bindings.import_legacy(self.db, self.project)
        self.assertEqual(self.db.db.total_changes, before)

    def test_import_does_not_seed_missing_configuration_or_overwrite_role_override(self):
        bindings.import_legacy(self.db, self.project)
        self.assertTrue(bindings.list_bindings(self.db, self.project))
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_executor_bindings WHERE project_id=?", (self.project,)).fetchone()[0], 0)
        self.assertEqual(self.db.team_members(self.project), [])
        team, _, _ = self.office()
        template = self.template(team_id=team["id"])
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": template["id"]})
        bindings.import_legacy(self.db, self.project)
        direct = bindings.register(self.db, self.project, "assistant", "binding-outsider")
        bindings.bind_role(self.db, self.project, template["id"], "writer", direct["id"])
        bindings.import_legacy(self.db, self.project)
        self.assertEqual(bindings.resolve_role(self.db, self.project, template["id"], "writer")["assistant_id"], "binding-outsider")

    def test_legacy_names_only_import_when_unambiguous(self):
        self.db.assistant_save("binding-writer", name="Same name")
        self.db.assistant_save("binding-reader", name="Same name")
        project_todos.add_todo(self.db, self.project, "Ambiguous old assignment", assigned_assistant="Same name")
        bindings.import_legacy(self.db, self.project)
        self.assertTrue(bindings.list_bindings(self.db, self.project))
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_executor_bindings WHERE project_id=?", (self.project,)).fetchone()[0], 0)

    def test_imported_role_tracks_saved_legacy_config_until_user_takes_ownership(self):
        template = self.template(assistant_id="binding-writer")
        workflow_layout.select_layout(self.db, {"task_id": self.project, "layout_id": template["id"]})
        bindings.import_legacy(self.db, self.project)
        data = bindings._template(self.db, template["id"])
        data["nodes"][0]["assistant_id"] = "binding-reader"
        self.db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?", (json.dumps(data), template["id"]))
        self.db.db.commit()
        bindings.import_legacy(self.db, self.project)
        resolved = bindings.resolve_role(self.db, self.project, template["id"], "writer")
        self.assertEqual(resolved["assistant_id"], "binding-reader")
        binding_id = resolved["binding_id"]
        owned = bindings.bind_role(self.db, self.project, template["id"], "writer", binding_id)
        self.assertEqual(owned["source"], "user")
        data["nodes"][0]["assistant_id"] = "binding-outsider"
        self.db.db.execute("UPDATE workflow_layouts SET definition=? WHERE id=?", (json.dumps(data), template["id"]))
        self.db.db.commit()
        bindings.import_legacy(self.db, self.project)
        self.assertEqual(bindings.resolve_role(self.db, self.project, template["id"], "writer")["binding_id"], binding_id)

    def test_deleted_project_removes_grants_settings_and_roles(self):
        grant = bindings.register(self.db, self.project, "assistant", "binding-writer")
        template = self.template()
        bindings.bind_role(self.db, self.project, template["id"], "writer", grant["id"])
        bindings.save_settings(self.db, self.project, "full_access")
        self.db.db.execute("DELETE FROM research_tasks WHERE id=?", (self.project,))
        self.db.db.commit()
        for table in ("project_executor_bindings", "project_execution_settings", "project_workflow_roles"):
            self.assertEqual(self.db.db.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (self.project,)).fetchone()[0], 0)
        self.assertEqual(self.db.db.execute("SELECT COUNT(*) FROM project_binding_origins WHERE binding_id=?", (grant["id"],)).fetchone()[0], 0)
