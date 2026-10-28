"""Acceptance tests for project ownership, durable memory and inbound routing.

All databases and documents are temporary. No real model, mail or Feishu request.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for package in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / package))

import capabilities
import feishu
import paths
import project_todos
import service_settings
import workflow


class AcceptanceBoundariesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rw-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = workflow.WorkflowDB(self.root / "acceptance.sqlite3")
        self.addCleanup(self.db.close)
        self.a = self.db.create_task("Project A")
        self.b = self.db.create_task("Project B")

    def test_direct_and_chat_agents_cannot_write_other_project_or_read_its_files(self):
        chat = self.db.create_chat("Bound A")
        self.db.bind_chat(chat, self.a)
        secret = self.db.base_dir(self.b) / "private.txt"
        secret.write_text("project-b-secret", encoding="utf-8")
        calls = [
            {"id": "foreign-note", "type": "function", "function": {
                "name": "project_note_write", "arguments": json.dumps({
                    "project": self.b, "path": "笔记/研究计划/foreign.md", "content": "bad"})}},
            {"id": "foreign-file", "type": "function", "function": {
                "name": "read_attachment", "arguments": json.dumps({"path": str(secret)})}},
        ]
        for entry in (self.a, chat):
            with self.subTest(entry=entry), patch.object(workflow, "chat_raw", side_effect=[
                {"content": "", "tool_calls": calls}, {"content": "done"},
            ]):
                result = workflow.run_agent(self.db, entry, "isolation probe", profile={"permission": "workspace_write"})
            outcomes = [e for e in result["events"] if e["type"] == "tool_result"]
            self.assertEqual([e["ok"] for e in outcomes], [False, False])
        self.assertFalse((self.db.base_dir(self.b) / "笔记/研究计划/foreign.md").exists())

    def test_schedule_ids_cannot_modify_another_project(self):
        other = self.db.schedule_save({"name": "B only", "project_id": self.b,
                                       "assistant_id": "builtin-steward", "enabled": False})
        with self.assertRaises(PermissionError):
            workflow._tool_impl("schedule_manage", self.db, self.a,
                                {"action": "toggle", "id": other["id"], "enabled": True})
        rows = workflow._tool_impl("schedule_manage", self.db, self.a, {"action": "list"})["schedules"]
        self.assertNotIn(other["id"], [row["id"] for row in rows])
        self.assertFalse(self.db.schedule(other["id"])["enabled"])

    def test_project_knowledge_query_excludes_other_projects_and_global_notes(self):
        self.db.save_md_note("", "A", "needle A", task_id=self.a)
        self.db.save_md_note("", "B", "needle B", task_id=self.b)
        self.db.save_md_note("", "Global", "needle Global")
        results = workflow._tool_impl("knowledge_query", self.db, self.a, {"query": "needle"})
        self.assertEqual([row["title"] for row in results["hits"]], ["A"])
        self.assertEqual(results["hits"][0]["project"], "Project A")

    def test_long_memory_survives_reopen_and_is_injected_only_into_its_project(self):
        self.db.add_memory(self.a, "long", "A durable decision")
        self.db.add_memory(self.b, "long", "B private decision")
        memory_file = self.db.base_dir(self.a) / "记忆/长期记忆.md"
        memory_file.write_text(memory_file.read_text(encoding="utf-8") + "\n- A manual decision\n", encoding="utf-8")
        self.db.add_memory(self.a, "long", "A second decision")
        with workflow.WorkflowDB(self.db.path) as reopened:
            ctx = reopened.memory_block(self.a)
            self.assertIn("A durable decision", ctx)
            self.assertIn("A manual decision", ctx)
            self.assertIn("A second decision", ctx)
            self.assertNotIn("B private decision", ctx)
            with patch.object(workflow, "chat_raw", return_value={"content": "done"}) as model:
                workflow.run_agent(reopened, self.a, "memory probe", profile={})
            context = model.call_args.args[1][1]["content"]
            self.assertIn("A manual decision", context)
            self.assertNotIn("B private decision", context)

    def test_custom_named_zotero_database_directory_is_rejected(self):
        managed = self.root / "custom-library"
        managed.mkdir()
        (managed / "zotero.sqlite").write_bytes(b"test marker, not a real library")
        with self.assertRaises(ValueError):
            paths.validate_project_dir(managed / "storage" / "ABCD")

    def test_feishu_project_switch_starts_a_fresh_project_chat(self):
        cfg = {"enabled": True, "inbound_enabled": True, "project_id": self.a,
               "allowed_sender": "ou_test", "app_id": "fake", "app_secret": "fake"}
        service_settings.write(self.db, "feishu", cfg, "builtin-steward")
        event = {"event": {"sender": {"sender_id": {"open_id": "ou_test"}}, "message": {
            "message_id": "m1", "chat_id": "c1", "message_type": "text",
            "content": json.dumps({"text": "task"})}}}
        with patch.object(workflow, "run_agent", return_value={"reply": "ok"}), patch.object(feishu, "send"), patch.object(feishu, "start_typing", return_value=""):
            first = feishu.handle_inbound(self.db.path, "builtin-steward", event)
            self.db.add_message(first["local_chat_id"], "assistant", "A private history")
            service_settings.write(self.db, "feishu", {**cfg, "project_id": self.b}, "builtin-steward")
            event["event"]["message"]["message_id"] = "m2"
            second = feishu.handle_inbound(self.db.path, "builtin-steward", event)
        self.assertNotEqual(first["local_chat_id"], second["local_chat_id"])
        self.assertEqual(self.db.chat(first["local_chat_id"])["task_id"], self.a)
        self.assertEqual(self.db.chat(second["local_chat_id"])["task_id"], self.b)
        self.assertEqual(self.db.messages(second["local_chat_id"]), [])

    def test_steward_tools_create_real_project_assets(self):
        invoke = lambda name, args: workflow._tool_impl(name, self.db, self.a, args)
        project_dir = str(self.db.base_dir(self.a))
        steward = capabilities.invoke_tool(self.db, "builtin-steward", "skill_read",
                                          {"skill_id": "research-steward"}, project_dir)
        self.assertEqual(steward["scope"], "project")
        self.assertIn("project_task_manage", steward["content"])
        expert = invoke("assistant_manage", {"action": "create", "name": "Acceptance expert", "rules": "Check evidence"})["assistant"]
        team = invoke("team_create", {"name": "Acceptance team", "assistant_ids": [expert["id"]]})
        self.assertEqual(team["team"]["project_id"], self.a)
        self.assertEqual(team["members"][0]["assistant_id"], expert["id"])
        skill = invoke("skill_create", {"name": "acceptance", "description": "Test skill", "instructions": "Check sources"})
        self.assertIn("Check sources", Path(skill["path"]).read_text(encoding="utf-8"))
        self.assertTrue(Path(skill["path"]).is_relative_to(self.db.base_dir(self.a)))
        layout = invoke("workflow_create", {"name": "Acceptance workflow", "steps": ["idea", "search", "outline"]})
        self.assertTrue(layout["layout"]["id"])
        note = invoke("project_note_write", {"path": "笔记/研究计划/acceptance.md", "content": "# Evidence"})
        self.assertEqual(invoke("project_note_read", {"path": note["path"]})["content"], "# Evidence")
        todo = invoke("project_task_manage", {"action": "create", "title": "Check sources", "assistant_id": expert["id"]})
        invoke("project_task_manage", {"action": "update", "id": todo["id"], "status": "done"})
        self.assertEqual(project_todos.list_todos(self.db, self.a)[0]["status"], "DONE")
        self.assertEqual(project_todos.list_todos(self.db, self.b), [])
        invoke("mcp_register", {"name": "acceptance", "command": "must-never-be-executed"})
        self.assertFalse(capabilities._config(self.db)["mcpServers"]["acceptance"]["enabled"])

    def test_agent_can_register_disabled_mcp_without_external_execution_permission(self):
        call = {"id": "register", "type": "function", "function": {"name": "mcp_register", "arguments": json.dumps({
            "name": "from-agent", "command": "never-execute-this"})}}
        with patch.object(workflow, "chat_raw", side_effect=[{"tool_calls": [call]}, {"content": "registered"}]):
            result = workflow.run_agent(self.db, self.a, "register", profile={"permission": "workspace_write"})
        self.assertTrue(next(e for e in result["events"] if e["type"] == "tool_result")["ok"])
        self.assertFalse(capabilities._config(self.db)["mcpServers"]["from-agent"]["enabled"])

    def test_missing_or_disabled_expert_blocks_schedule_before_model_call(self):
        import schedule
        with patch.object(schedule, "DB_PATH", self.db.path), patch.object(workflow, "run_agent") as model:
            row = self.db.schedule_save({"name": "Unbound", "project_id": self.a, "enabled": True})
            self.assertEqual(row["assistant_id"], "builtin-scheduler")
            self.db.assistant_save("builtin-scheduler", enabled=False)
            self.assertEqual(schedule.run_once(row)["status"], "blocked")
            row["assistant_id"] = "missing-expert"
            self.assertEqual(schedule.run_once(row)["status"], "blocked")
            model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
