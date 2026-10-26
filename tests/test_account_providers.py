"""Account providers use official CLI sessions and the workbench tool loop."""
import json
import http.server
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch

import account_providers
import agent_protocols
import agent_settings
import execution
import paths
import workflow


class AccountProvidersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = workflow.WorkflowDB(Path(self.tmp.name) / "app.sqlite3")
        self.addCleanup(self.db.close)

    def route(self, name, data=None, method="POST"):
        return agent_settings.route(method, "/api/agent-settings/" + name, data or {}, {}, self.db)

    def test_catalog_accounts_can_be_adopted_without_keys_and_survive_reopen(self):
        catalog = self.route("state", method="GET")["catalog"]
        for pid, account in account_providers.CATALOG.items():
            item = next(p for p in catalog if p["id"] == pid)
            self.assertTrue(item["supported"])
            self.assertEqual(item["auth"], ["account"])
            self.route("provider-save", {"id": pid, "name": item["name"], "api": item["api"]})
            self.route("provider-models", {"id": pid, "models": ["auto"], "auto": True})
            model = self.db.active_model()
            self.assertEqual(model["api"], account["wire"])
            self.assertEqual(model["api_key"], "")
        with workflow.WorkflowDB(self.db.path) as reopened:
            self.assertEqual(len(reopened.models()), 3)
        self.assertNotIn('"api_key"', json.dumps(self.route("state", method="GET")))

    def test_missing_cli_and_auth_status_do_not_return_secrets(self):
        with patch.object(account_providers.shutil, "which", return_value=None):
            self.assertFalse(account_providers.status("openai-codex")["installed"])
        with patch.object(account_providers, "command", return_value=["claude"]), patch.object(account_providers.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b'{"loggedIn":true,"access_token":"private"}', b"")):
            status = account_providers.status("claude-code")
        self.assertTrue(status["authenticated"])
        self.assertNotIn("private", json.dumps(status))

    def test_cli_response_requests_workbench_tools_and_preserves_history(self):
        tools = [{"type": "function", "function": {"name": "library_overview", "parameters": {"type": "object"}}}]
        reply = {"content": "查阅项目资料", "tool_calls": [{"name": "library_overview", "arguments": {}}]}
        events = "\n".join(json.dumps(event) for event in [
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(reply)}},
            {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 8}}])
        profile = {"api": "codex-cli", "model": "auto", "base_url": "account://openai-codex"}
        with patch.object(account_providers, "command", return_value=["codex"]), patch.object(account_providers, "_run", return_value=events) as run:
            response = workflow.chat_raw(profile, [{"role": "user", "content": "UPSTREAM_MEMORY"}], tools)
        self.assertEqual(response["tool_calls"][0]["function"]["name"], "library_overview")
        self.assertIn("UPSTREAM_MEMORY", run.call_args.args[1])
        self.assertEqual(response["usage"]["total"], 28)
        self.assertIn("read-only", run.call_args.args[0])

    def test_cli_errors_and_unavailable_tools_fail_honestly(self):
        profile = {"api": "claude-code-cli", "model": "auto", "base_url": "account://claude-code"}
        event = json.dumps({"result": json.dumps({"content": "", "tool_calls": [{"name": "unapproved", "arguments": {}}]})})
        with patch.object(account_providers, "command", return_value=["claude"]), patch.object(account_providers, "_run", return_value=event):
            with self.assertRaisesRegex(RuntimeError, "未授权"):
                agent_protocols.complete(profile, [], tools=[])
        with patch.object(account_providers, "command", return_value=["claude"]), patch.object(account_providers, "_run", return_value='{"is_error":true,"result":"private token"}'):
            with self.assertRaisesRegex(RuntimeError, "未成功"):
                agent_protocols.complete(profile, [])

    def test_real_subprocess_is_cancelled_and_does_not_linger(self):
        with self.assertRaises(execution.Cancelled):
            account_providers._run([sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(20)"], "hello", 30, lambda: True)

    def test_antigravity_streams_prompt_on_stdin_without_command_line_limit(self):
        raw = json.dumps({'event':'init'}) + '\n' + json.dumps({'event':'result','result':{'status':'SUCCESS',
            'response':json.dumps({'content':'STREAM_OK','tool_calls':[]})}})
        with patch.object(account_providers,'command',return_value=['agy']), patch.object(account_providers,'_run',return_value=raw) as run:
            result=account_providers.complete({'api':'antigravity-cli'},[{'role':'user','content':'UPSTREAM'*10000}])
        self.assertEqual(result['content'],'STREAM_OK')
        args, stdin = run.call_args.args[:2]
        self.assertNotIn('-p',args)
        self.assertIn('--input-format',args)
        self.assertEqual(json.loads(stdin)['event'],'user')
        self.assertIn('UPSTREAM'*10000,json.loads(stdin)['message']['content'])

    def test_claude_npm_native_binary_wrapper_is_supported(self):
        root=Path(self.tmp.name)
        native=root/'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
        native.parent.mkdir(parents=True);native.write_bytes(b'fixture')
        with patch.object(account_providers.shutil,'which',return_value=str(root/'claude.cmd')):
            self.assertEqual(account_providers.command('claude-code'),[str(native)])

    def test_account_runs_through_actual_workbench_tool_loop(self):
        self.route("provider-save", {"id": "openai-codex", "name": "Codex", "api": "codex-cli"})
        self.route("provider-models", {"id": "openai-codex", "models": ["auto"], "auto": True})
        cid = self.db.create_chat("Account tool loop")
        def event(response):
            return json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response)}})
        responses = [event({"content": "", "tool_calls": [{"name": "library_overview", "arguments": {}}]}),
                     event({"content": "ACCOUNT_LOOP_FINISHED", "tool_calls": []})]
        with patch.object(account_providers, "command", return_value=["codex"]), patch.object(account_providers, "_run", side_effect=responses) as run:
            result = workflow.run_agent(self.db, cid, "Look up library", publish=False, tool_allowlist=["library_overview"])
        self.assertEqual(result["reply"], "ACCOUNT_LOOP_FINISHED")
        self.assertEqual(run.call_count, 2)
        continued = json.loads(run.call_args.args[1].split("\n", 1)[1])
        self.assertTrue(any(m["role"] == "tool" for m in continued["messages"]))
        self.assertEqual([t["function"]["name"] for t in continued["tools"]], ["library_overview"])

    def test_low_integrity_calls_use_authenticated_desktop_host(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "desktop"))
        import server
        owned = []
        constructor = http.server.ThreadingHTTPServer
        def build(*args, **kwargs):
            instance = constructor(*args, **kwargs)
            owned.append(instance)
            return instance
        with patch.object(paths, "project_roots", return_value=[]), patch.object(http.server, "ThreadingHTTPServer", side_effect=build):
            port, key = server._start_grant_helper()
        self.addCleanup(owned[0].server_close)
        self.addCleanup(owned[0].shutdown)
        main = threading.current_thread()
        event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"content": "HOST_MODEL_RESULT", "tool_calls": []})}})
        with patch.multiple(paths, GRANT=f"127.0.0.1:{port}", GRANT_KEY=key), \
             patch.object(account_providers, "_needs_host", side_effect=lambda: threading.current_thread() is main), \
             patch.object(account_providers, "command", return_value=["codex"]), \
             patch.object(account_providers, "_run", return_value=event) as run:
            result = account_providers.complete({"api": "codex-cli", "model": "auto"}, [{"role": "user", "content": "Host prompt"}])
        self.assertEqual(result["content"], "HOST_MODEL_RESULT")
        self.assertIn("Host prompt", run.call_args.args[1])

    def test_host_rejects_arbitrary_commands_and_remembers_early_cancellation(self):
        with patch.object(account_providers.subprocess, "Popen") as spawn:
            result = account_providers.host_request({"request_id": "a" * 32, "action": "complete", "provider": "powershell", "command": "anything"})
        self.assertIn("error", result)
        spawn.assert_not_called()
        ident = "b" * 32
        account_providers.host_request({"request_id": ident, "action": "cancel"})
        def cancelled(profile, messages, tools, **kwargs):
            self.assertTrue(kwargs["cancel_check"]())
            raise execution.Cancelled("运行已中断")
        with patch.object(account_providers, "complete", side_effect=cancelled):
            result = account_providers.host_request({"request_id": ident, "action": "complete", "provider": "openai-codex", "messages": [], "tools": []})
        self.assertIn("中断", result["error"])
