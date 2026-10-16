"""Isolated regression tests for selectable models, policies and team history."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for name in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(ROOT / "packages" / name))

import agent_integrations
import agent_protocols
import agent_settings
import capabilities
import team
import workflow


class AgentSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.db = workflow.WorkflowDB(self.path)
        self.old_path = team.DB_PATH
        team.DB_PATH = self.path

    def tearDown(self):
        team.DB_PATH = self.old_path
        self.db.close()
        self.tmp.cleanup()

    def route(self, action, body=None, query=None, method="POST"):
        return agent_settings.route(method, "/api/agent-settings/" + action, body or {}, query or {}, self.db)

    def provider(self, models=("first", "second")):
        p = self.route("provider-save", {"name": "Test", "base_url": "https://example.test/v1", "api": "openai-completions", "api_key": "secret"})["provider"]
        self.route("provider-models", {"id": p["id"], "models": list(models)})
        return p

    def test_all_adopted_models_selectable_and_credentials_follow_edit(self):
        p = self.provider()
        self.assertEqual({m["model"] for m in self.db.models()}, {"first", "second"})
        mids = {m["model"]: m["id"] for m in self.db.models()}
        self.db.assistant_save("mine", model_id=mids["second"])
        self.route("provider-save", {"id": p["id"], "name": "Updated", "base_url": "https://new.test/v1", "api": "anthropic-messages", "api_key": "new-secret"})
        m = self.db.model(mids["second"])
        self.assertEqual((m["base_url"], m["api_key"], m["api"]), ("https://new.test/v1", "new-secret", "anthropic-messages"))
        self.assertIn("Updated", m["name"])
        with workflow.WorkflowDB(self.path) as reopened:
            self.assertEqual(reopened.assistant("mine")["model_id"], mids["second"])
            self.assertEqual(len(reopened.models()), 2)

    def test_key_kept_when_omitted_and_explicitly_revealed_only(self):
        p = self.provider()
        self.route("provider-save", {"id": p["id"], "name": "Keep", "base_url": "http://localhost:9000/v1", "api": "openai-responses", "api_key": None})
        state = self.route("state", method="GET")
        self.assertNotIn("new-secret", json.dumps(state))
        self.assertNotIn('"api_key"', json.dumps(state))
        self.assertTrue(state["providers"][0]["key_present"])
        self.assertEqual(self.route("provider-secret", query={"id": [p["id"]]}, method="GET")["api_key"], "secret")

    def test_full_list_no_truncation_public_eligibility_enforced(self):
        for i in range(70):
            self.db.assistant_save("mine-" + str(i), name="User " + str(i), enabled=0)
        self.db.assistant_save("builtin-searcher", enabled=0)
        ids = {a["id"] for a in self.db.selectable_assistants()}
        self.assertTrue(all("mine-" + str(i) in ids for i in range(70)))
        self.assertNotIn("builtin-searcher", ids)
        cid = self.db.create_chat()
        with self.assertRaises(ValueError):
            self.db.set_chat_assistant(cid, "builtin-searcher")
        with self.assertRaises(ValueError):
            team.add_assistant(cid, "builtin-searcher")
        self.db.set_chat_assistant(cid, "mine-69")
        for i in range(10):
            team.add_assistant(cid, "mine-" + str(i))
        self.assertEqual(len(team.picks(cid)), 10)

    def test_durable_team_sessions_legacy_migration_and_switch(self):
        tid = self.db.team_add("Session team", "custom")["id"]
        self.db.add_message(tid, "user", "old conversation", scope="team")
        first = agent_settings.session(self.db, tid)
        self.assertEqual(self.db.messages(tid, "team:" + first["id"])[0]["content"], "old conversation")
        second = self.route("team-session", {"team_id": tid, "create": True})["session"]
        self.assertNotEqual(first["id"], second["id"])
        team.say(tid, "new conversation", start=False)
        self.assertEqual(team.transcript(tid)[0]["content"], "new conversation")
        self.route("team-session", {"team_id": tid, "session_id": first["id"]})
        self.assertEqual(team.transcript(tid)[0]["content"], "old conversation")
        with workflow.WorkflowDB(self.path) as reopened:
            self.assertEqual(agent_settings.session(reopened, tid)["id"], first["id"])
            self.assertEqual(reopened.db.execute("SELECT count(*) FROM team_sessions WHERE team_id=?", (tid,)).fetchone()[0], 2)

    def test_leader_can_change_and_uses_own_model_rules_and_policy(self):
        self.provider()
        selected = self.db.models()[1]
        self.db.assistant_save("leader-asst", name="Research lead", rules="CUSTOM LEADER RULE", model_id=selected["id"], reasoning="high", permission="read_only")
        t = self.route("team-save", {"name": "Custom team"})["team"]
        m = self.route("member-save", {"team_id": t["id"], "assistant_id": "leader-asst"})["member"]
        self.route("leader", {"team_id": t["id"], "member_id": m["id"]})
        self.assertEqual(sum(x["role"] == "leader" for x in self.db.team_members(t["id"])), 1)
        seen = {}
        def complete(profile, messages, tools=None, **kwargs):
            seen.update(profile=profile, messages=messages, tools=tools)
            return {"content": "完成", "tool_calls": []}
        with patch.object(workflow, "chat_raw", complete):
            team._round(t["id"], 1)
        self.assertEqual(seen["profile"]["id"], selected["id"])
        self.assertEqual(seen["profile"]["reasoning"], "high")
        self.assertEqual(seen["profile"]["permission"], "read_only")
        self.assertIn("CUSTOM LEADER RULE", "\n".join(m["content"] for m in seen["messages"] if m["role"] == "system"))
        self.assertIn("Research lead", {m["sender"] for m in team.transcript(t["id"])})

    def test_read_only_blocks_forged_write_even_if_model_ignores_tools(self):
        self.provider()
        self.db.assistant_save("readonly", permission="read_only")
        cid = self.db.create_chat()
        self.db.set_chat_assistant(cid, "readonly")
        seen = []
        def complete(profile, messages, tools=None, **kwargs):
            seen.extend(t["function"]["name"] for t in tools)
            if messages[-1]["role"] == "tool":
                return {"content": "blocked", "tool_calls": []}
            return {"content": "", "tool_calls": [{"id": "bad", "function": {"name": "memory_save", "arguments": '{"text":"unsafe"}'}}]}
        with patch.object(workflow, "chat_raw", complete), patch.object(workflow, "_tool_impl") as invoke:
            out = workflow.run_agent(self.db, cid, "test", publish=False)
        self.assertNotIn("memory_save", seen)
        invoke.assert_not_called()
        failure = next(e for e in out["events"] if e["type"] == "tool_result")
        self.assertFalse(failure["ok"])
        self.assertIn("未获准", failure["summary"])

    def test_full_access_passes_policy_to_real_connector_dispatch(self):
        self.provider()
        self.db.assistant_save("trusted", permission="full_access", reasoning="xhigh")
        cid = self.db.create_chat()
        self.db.set_chat_assistant(cid, "trusted")
        count = [0]
        def complete(profile, messages, tools=None, **kwargs):
            self.assertEqual(profile["reasoning"], "xhigh")
            count[0] += 1
            if count[0] > 1:
                return {"content": "sent", "tool_calls": []}
            return {"content": "", "tool_calls": [{"id": "send", "function": {"name": "mail_send", "arguments": '{"to":"test@example.test"}'}}]}
        with patch.object(workflow, "chat_raw", complete), patch.object(agent_integrations, "invoke", return_value={"ok": True}) as invoke:
            workflow.run_agent(self.db, cid, "send", publish=False)
        self.assertEqual(invoke.call_args.kwargs, {"assistant_id": "trusted", "permission": "full_access"})

    def test_chat_overrides_persist_and_invalid_policies_rejected(self):
        self.provider()
        cid = self.db.create_chat()
        mid = self.db.models()[1]["id"]
        self.route("runtime-save", {"id": cid, "model_id": mid, "reasoning": "low", "permission": "workspace_write"})
        cfg = agent_settings.runtime_config(self.db, cid)
        self.assertEqual((cfg["model_id"], cfg["reasoning"], cfg["permission"]), (mid, "low", "workspace_write"))
        with self.assertRaises(ValueError):
            self.route("runtime-save", {"id": cid, "permission": "invented"})
        with self.assertRaises(ValueError):
            self.route("provider-save", {"name": "bad", "base_url": "file:///C:/secret", "api": "openai-completions"})

    def test_disabled_tool_matrix_applies_to_forged_calls(self):
        self.provider()
        cid = self.db.create_chat()
        capabilities.filter_tools(self.db, "", [])
        self.db.db.execute("INSERT INTO capability_bindings (assistant_id,kind,item_id,enabled) VALUES ('*','tool','library_overview',0)")
        self.db.db.commit()
        def complete(profile, messages, tools=None, **kwargs):
            self.assertNotIn("library_overview", {t["function"]["name"] for t in tools})
            if messages[-1]["role"] == "tool":
                return {"content": "blocked", "tool_calls": []}
            return {"content": "", "tool_calls": [{"id": "bad", "function": {"name": "library_overview", "arguments": "{}"}}]}
        with patch.object(workflow, "chat_raw", complete), patch.object(workflow, "_tool_impl") as invoke:
            workflow.run_agent(self.db, cid, "test", publish=False)
        invoke.assert_not_called()

    def test_delegate_inherits_team_assistant_matrix_model_and_policy(self):
        self.provider()
        selected = self.db.models()[1]
        self.db.assistant_save("delegate-owner", rules="TEAM CHILD RULE", model_id=selected["id"], reasoning="high", permission="read_only")
        tid = self.db.team_add("Delegate policy team", "custom")["id"]
        capabilities.filter_tools(self.db, "delegate-owner", [])
        self.db.db.execute("INSERT INTO capability_bindings (assistant_id,kind,item_id,enabled) VALUES ('delegate-owner','tool','library_overview',0)")
        self.db.db.commit()
        seen = []
        def complete(profile, messages, tools=None, **kwargs):
            seen.append((dict(profile), {t["function"]["name"] for t in tools}))
            self.assertIn("TEAM CHILD RULE", "\n".join(m["content"] for m in messages if m["role"] == "system"))
            if messages[-1]["role"] == "tool":
                return {"content": "blocked", "tool_calls": []}
            return {"content": "", "tool_calls": [{"id": "bad", "function": {"name": "library_overview", "arguments": "{}"}}]}
        profile = {**selected, "assistant_id": "delegate-owner", "reasoning": "high", "permission": "read_only"}
        with patch.object(workflow, "chat_raw", complete), patch.object(workflow, "_tool_impl") as invoke:
            out = workflow._delegate(self.db, tid, [{"prompt": "check inherited controls"}], profile=profile)
        self.assertTrue(out["delegated"][0]["ok"], out)
        self.assertTrue(seen)
        self.assertEqual(seen[0][0]["id"], selected["id"])
        self.assertEqual(seen[0][0]["assistant_id"], "delegate-owner")
        self.assertEqual(seen[0][0]["reasoning"], "high")
        self.assertEqual(seen[0][0]["permission"], "read_only")
        self.assertNotIn("library_overview", seen[0][1])
        self.assertNotIn("paper_read", seen[0][1])
        invoke.assert_not_called()

    def test_project_skill_priority_and_disabled_stage_injection(self):
        project = Path(self.tmp.name) / "project"
        project_skill = project / ".agents" / "skills" / "idea-evaluator" / "SKILL.md"
        project_skill.parent.mkdir(parents=True)
        project_skill.write_text("---\nname: idea-evaluator\n---\nPROJECT UNIQUE RULE", encoding="utf-8")
        tid = self.db.create_task("Project", "Q", "M")
        self.db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (str(project), tid))
        self.db.db.commit()
        self.assertIn("PROJECT UNIQUE RULE", workflow.idea_system(self.db, tid))
        capabilities.filter_tools(self.db, "", [])
        self.db.db.execute("INSERT INTO capability_bindings (assistant_id,kind,item_id,enabled) VALUES ('*','skill','idea-evaluator',0)")
        self.db.db.commit()
        self.assertNotIn("PROJECT UNIQUE RULE", workflow.idea_system(self.db, tid))

    def test_custom_team_role_can_be_added_without_assistant(self):
        self.provider()
        tid = self.db.team_add("Custom role", "custom")["id"]
        member = self.route("member-save", {"team_id": tid, "name": "Independent reviewer", "prompt": "Review evidence"})["member"]
        self.assertEqual(member["assistant_id"], "")
        self.assertEqual(member["prompt"], "Review evidence")
        self.route("leader", {"team_id": tid, "member_id": member["id"]})
        self.assertEqual(self.db.team_member(member["id"])["role"], "leader")
        seen = []
        def complete(profile, messages, tools=None, **kwargs):
            seen.extend(m["content"] for m in messages if m["role"] == "system")
            return {"content": "Done", "tool_calls": []}
        with patch.object(workflow, "chat_raw", complete):
            team._round(tid, 1)
        self.assertIn("团队 Leader，负责协调与决策", "\n".join(seen))
        self.assertIn("Independent reviewer", {m["sender"] for m in team.transcript(tid)})

    def test_signed_reasoning_preserved_through_actual_tool_loop(self):
        self.provider()
        cid = self.db.create_chat()
        variants = {
            "openai-responses": ({"output": [{"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque-signed"}, {"type": "function_call", "call_id": "c1", "name": "library_overview", "arguments": "{}"}]}, "input"),
            "anthropic-messages": ({"content": [{"type": "thinking", "thinking": "private thought", "signature": "opaque-signed"}, {"type": "tool_use", "id": "c1", "name": "library_overview", "input": {}}]}, "messages"),
            "google-generative-ai": ({"candidates": [{"content": {"parts": [{"functionCall": {"name": "library_overview", "args": {}}, "thoughtSignature": "opaque-signed"}]}}]}, "contents"),
        }
        for wire, (first, payload_key) in variants.items():
            with self.subTest(wire=wire):
                profile = {**self.db.models()[0], "api": wire, "reasoning": "high"}
                with patch.object(agent_protocols, "_request", side_effect=[first, {}]) as transport, patch.object(workflow, "_tool_impl", return_value={"ok": True}):
                    workflow.run_agent(self.db, cid, "inspect", profile=profile, publish=False)
                self.assertEqual(transport.call_count, 2)
                continued = transport.call_args_list[1].args[1][payload_key]
                self.assertIn("opaque-signed", json.dumps(continued))


class ProtocolTest(unittest.TestCase):
    def profile(self, api):
        return {"model": "model", "base_url": "https://example.test/v1", "api_key": "secret", "api": api, "reasoning": "high"}

    def test_chat_completions_effort_reaches_payload(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
        with patch.object(workflow, "open_url", return_value=response) as transport:
            workflow.chat_raw(self.profile("openai-completions"), [{"role": "user", "content": "hi"}])
        payload = json.loads(transport.call_args.args[0].data)
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_responses_normalizes_tool_calls_and_effort(self):
        raw = {"output": [{"type": "function_call", "call_id": "call1", "name": "read", "arguments": "{}"}], "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}}
        with patch.object(agent_protocols, "_request", return_value=raw) as send:
            result = agent_protocols.complete(self.profile("openai-responses"), [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
        self.assertEqual(send.call_args.args[1]["reasoning"], {"effort": "high"})
        self.assertEqual(result["tool_calls"][0]["id"], "call1")
        self.assertEqual(result["usage"]["total"], 5)

    def test_anthropic_normalizes_tools_and_sends_thinking(self):
        raw = {"content": [{"type": "tool_use", "id": "t", "name": "read", "input": {"path": "test"}}]}
        with patch.object(agent_protocols, "_request", return_value=raw) as send:
            result = agent_protocols.complete(self.profile("anthropic-messages"), [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
        self.assertEqual(send.call_args.args[2]["x-api-key"], "secret")
        self.assertGreater(send.call_args.args[1]["max_tokens"], send.call_args.args[1]["thinking"]["budget_tokens"])
        self.assertEqual(json.loads(result["tool_calls"][0]["function"]["arguments"]), {"path": "test"})

    def test_gemini_normalizes_tools_and_discovery(self):
        raw = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "read", "args": {}}}]}}]}
        with patch.object(agent_protocols, "_request", return_value=raw) as send:
            result = agent_protocols.complete(self.profile("google-generative-ai"), [{"role": "user", "content": "hi"}])
        self.assertEqual(send.call_args.args[2]["x-goog-api-key"], "secret")
        self.assertIn("thinkingConfig", send.call_args.args[1]["generationConfig"])
        self.assertEqual(result["tool_calls"][0]["function"]["name"], "read")
        with patch.object(agent_protocols, "_request", return_value={"models": [{"name": "models/gemini-test", "displayName": "Gemini", "supportedGenerationMethods": ["generateContent"]}]}):
            self.assertEqual(agent_protocols.discover(self.profile("google-generative-ai"))[0]["id"], "gemini-test")


if __name__ == "__main__":
    unittest.main()
