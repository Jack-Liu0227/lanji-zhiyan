"""Isolated capability policy, credential and real stdio MCP regressions."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import capabilities


MCP_FIXTURE = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"protocolVersion":"2025-03-26", "capabilities":{"tools":{}}, "serverInfo":{"name":"fixture","version":"1"}}
    elif method == "tools/list":
        if request.get("params",{}).get("cursor") == "second":
            result = {"tools":[{"name":"sum","description":"Add integers", "inputSchema":{"type":"object","properties":{"a":{"type":"integer"},"b":{"type":"integer"}}}}]}
        else:
            result = {"tools":[{"name":"echo", "description":"Echo", "inputSchema":{"type":"object","properties":{"text":{"type":"string"}}}}],"nextCursor":"second"}
    elif method == "tools/call":
        args = request["params"]["arguments"]
        text = args.get("text") if request["params"]["name"] == "echo" else str(args["a"] + args["b"])
        result = {"content":[{"type":"text","text":text}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc":"2.0","id":request["id"],"result":result}), flush=True)
'''


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.project = self.base / "project"
        self.db = sqlite3.connect(self.base / "test.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE assistants(id TEXT PRIMARY KEY,name TEXT,source TEXT,enabled INTEGER,sort_order INTEGER);
            INSERT INTO assistants VALUES('a','Assistant A','user',1,0),('b','Assistant B','builtin',0,1);
            CREATE TABLE research_tasks(id TEXT PRIMARY KEY,base_dir TEXT);
        """)
        self.db.execute("INSERT INTO research_tasks VALUES('p',?)", (str(self.project),))
        self.db.commit()
        self.home_patch = patch.object(capabilities.Path, "home", return_value=self.home)
        self.home_patch.start()
        self.app_patch = patch.object(capabilities.paths, "APP", self.base / "app")
        self.app_patch.start()
        self.tools_patch = patch.object(capabilities, "_builtin_specs", return_value=[
            {"type": "function", "function": {"name": "read", "description": "Original", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "write", "description": "Write", "parameters": {"type": "object"}}},
        ])
        self.tools_patch.start()

    def tearDown(self):
        self.app_patch.stop()
        self.tools_patch.stop()
        self.home_patch.stop()
        self.db.close()
        self.tmp.cleanup()

    def skill(self, root, folder, name, content):
        target = root / ".agents" / "skills" / folder / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"---\nname: {name}\ndescription: Description {name}\n---\n{content}", encoding="utf-8")
        return target

    def binding(self, aid, kind, item_id, value, **extra):
        return capabilities.route("POST", "/api/capabilities/binding",
                                  {"assistant_id": aid, "kind": kind, "item_id": item_id, "enabled": value, **extra}, {}, self.db)

    def test_project_skill_overrides_by_declared_name_without_truncation(self):
        self.skill(self.home, "older", "shared", "USER BODY")
        project_skill = self.skill(self.project, "nested/fresh", "shared", "PROJECT BODY")
        for i in range(85):
            self.skill(self.home, f"skill-{i}", f"skill-{i}", "body")
        items = capabilities.list_skills(str(self.project))
        self.assertEqual(len(items), 86)
        shared = next(item for item in items if item["id"] == "shared")
        self.assertEqual(shared["scope"], "project")
        self.assertEqual(shared["path"], str(project_skill.resolve()))
        meta = capabilities.route("GET", "/api/capabilities", {}, {"project_id": ["p"]}, self.db)
        self.assertEqual(meta["skills_total"], 86)
        self.assertEqual(len(meta["assistants"]), 2)

    def test_skill_preview_edit_and_path_confinement(self):
        path = self.skill(self.project, "sample", "sample", "old")
        result = capabilities.route("GET", "/api/capabilities/skill", {}, {"id": ["sample"], "project_id": ["p"]}, self.db)
        self.assertIn("old", result["content"])
        content = result["content"].replace("old", "updated")
        capabilities.route("POST", "/api/capabilities/skill", {"id": "sample", "project_id": "p", "content": content}, {}, self.db)
        self.assertIn("updated", path.read_text())
        with self.assertRaises(ValueError):
            capabilities.route("GET", "/api/capabilities/skill", {}, {"id": ["../../outside"]}, self.db)
        with self.assertRaises(ValueError):
            capabilities.route("POST", "/api/capabilities/skill", {"id": "sample", "project_id": "p", "content": content.replace("name: sample", "name: different")}, {}, self.db)

    def test_skill_context_read_and_disable_enforced_on_server(self):
        self.skill(self.home, "sample", "sample", "Secret skill body")
        self.assertIn("sample", capabilities.skill_context(self.db, "a"))
        self.assertNotIn("Secret skill body", capabilities.skill_context(self.db, "a"))
        result = capabilities.invoke_tool(self.db, "a", "skill_read", {"skill_id": "sample"})
        self.assertIn("Secret skill body", result["content"])
        self.binding("a", "skill", "sample", False)
        self.assertEqual(capabilities.skill_context(self.db, "a"), "")
        with self.assertRaises(PermissionError):
            capabilities.invoke_tool(self.db, "a", "skill_read", {"skill_id": "sample"})
        self.assertIn("sample", capabilities.skill_context(self.db, "b"))

    def test_matrix_default_and_individual_overrides_keep_dynamic_tools(self):
        self.binding("*", "tool", "read", False)
        tools = capabilities._builtin_specs() + [{"type": "function", "function": {"name": "dynamic", "parameters": {}}}]
        self.assertEqual([item["function"]["name"] for item in capabilities.filter_tools(self.db, "a", tools)], ["write", "dynamic"])
        self.binding("a", "tool", "read", True)
        self.assertEqual(len(capabilities.filter_tools(self.db, "a", tools)), 3)
        self.assertEqual(len(capabilities.filter_tools(self.db, "b", tools)), 2)
        with self.assertRaises(ValueError):
            self.binding("missing", "tool", "read", True)
        with self.assertRaises(ValueError):
            self.binding("a", "tool", "read", "false")

    def test_tool_metadata_edits_runtime_description_without_mutation(self):
        source = capabilities._builtin_specs()
        capabilities.route("POST", "/api/capabilities/tool", {"name": "read", "label": "Research reader", "description": "Updated instruction"}, {}, self.db)
        self.assertEqual(capabilities.list_tools(self.db)[0]["label"], "Research reader")
        result = capabilities.filter_tools(self.db, "a", source)
        self.assertEqual(result[0]["function"]["description"], "Updated instruction")
        self.assertEqual(source[0]["function"]["description"], "Original")

    def config(self):
        return {"mcpServers": {"remote": {"url": "https://example.invalid/mcp?token=secret-query", "headers": {"Authorization": "Bearer secret-bearer"}, "enabled": True},
                               "local": {"command": "python", "args": ["--api-key", "secret-argument"], "env": {"API_KEY": "secret-key"}}}}

    def test_mcp_secret_redaction_protected_round_trip_and_explicit_reveal(self):
        result = capabilities.save_mcp(self.db, self.config())
        public = json.dumps(result)
        for secret in ("secret-bearer", "secret-key", "secret-argument", "secret-query"):
            self.assertNotIn(secret, public)
            self.assertNotIn(secret, json.dumps(capabilities.settings_meta(self.db)))
        raw = self.db.execute("SELECT protected FROM capability_mcp_config").fetchone()[0]
        self.assertNotIn("secret-key", raw)
        if os.name == "nt":
            self.assertTrue(raw.startswith("dpapi:"))
            # DPAPI protects plaintext, not merely a base64-encoded JSON value.
            import base64
            self.assertNotIn(b"secret-key", base64.b64decode(raw.split(":", 1)[1]))
        capabilities.save_mcp(self.db, result["config"])
        self.assertEqual(capabilities._config(self.db), self.config())
        with self.assertRaises(ValueError):
            capabilities.route("POST", "/api/capabilities/mcp/reveal", {}, {}, self.db)
        reveal = capabilities.route("POST", "/api/capabilities/mcp/reveal", {"confirm": True}, {}, self.db)
        self.assertEqual(reveal["config"], self.config())

    def test_mcp_save_never_executes_and_invalid_json_does_not_overwrite(self):
        with patch.object(capabilities.subprocess, "Popen") as popen:
            capabilities.save_mcp(self.db, self.config())
            capabilities.settings_meta(self.db)
            capabilities.capability_tool_specs(self.db, "a")
            popen.assert_not_called()
        for value in ({"mcpServers": {"bad": {"command": "python", "url": "https://example.invalid"}}},
                      {"mcpServers": {"bad": {"command": "python", "args": "-m server"}}},
                      {"mcpServers": {"bad": {"url": "file:///private"}}},
                      {"mcpServers": {"bad": {"command": "python", "enabled": "false"}}}):
            with self.assertRaises(ValueError):
                capabilities.save_mcp(self.db, value)
        self.assertEqual(capabilities._config(self.db), self.config())

    def test_server_short_api_key_mask_preserves_saved_secret(self):
        config = {"mcpServers": {"local": {"command": "python", "env": {"api_key": "lowercase-key-secret"}}}}
        capabilities.save_mcp(self.db, config)
        scrubbed = capabilities._redact(config)
        scrubbed["mcpServers"]["local"]["env"]["api_key"] = "***"
        capabilities.save_mcp(self.db, scrubbed)
        self.assertEqual(capabilities._config(self.db), config)

    def test_real_stdio_mcp_pagination_discovery_call_and_revocation(self):
        fixture = self.base / "fixture.py"
        fixture.write_text(MCP_FIXTURE, encoding="utf-8")
        config = {"mcpServers": {"fixture-server": {"command": sys.executable, "args": [str(fixture)], "timeout": 5}}}
        capabilities.save_mcp(self.db, config)
        with self.assertRaises(ValueError):
            capabilities.route("POST", "/api/capabilities/mcp/discover", {"server": "fixture-server"}, {}, self.db)
        result = capabilities.route("POST", "/api/capabilities/mcp/discover", {"server": "fixture-server", "confirm": True}, {}, self.db)
        self.assertEqual(len(result["tools"]), 2)
        specs = capabilities.mcp_tool_specs(self.db, "a")
        self.assertEqual(len(specs), 2)
        self.assertTrue(all(len(item["function"]["name"]) <= 64 for item in specs))
        echo = next(item["function"]["name"] for item in specs if item["function"]["description"].endswith("Echo"))
        result = capabilities.invoke_mcp_tool(self.db, "a", echo, {"text": "real process response"})
        self.assertEqual(result["content"][0]["text"], "real process response")
        self.binding("a", "mcp", "fixture-server", False)
        self.assertEqual(capabilities.mcp_tool_specs(self.db, "a"), [])
        self.assertEqual(capabilities.filter_tools(self.db, "a", specs), [])
        with patch.object(capabilities.subprocess, "Popen") as popen, self.assertRaises(PermissionError):
            capabilities.invoke_mcp_tool(self.db, "a", echo, {"text": "blocked"})
        popen.assert_not_called()
        self.assertEqual(len(capabilities.mcp_tool_specs(self.db, "b")), 2)
        config["mcpServers"]["fixture-server"]["env"] = {"DIFFERENT": "1"}
        capabilities.save_mcp(self.db, config)
        self.assertEqual(capabilities.mcp_tool_specs(self.db, "b"), [])

    def test_mcp_tool_names_do_not_collide_after_sanitizing(self):
        self.assertNotEqual(capabilities._mcp_name("a-b", "c"), capabilities._mcp_name("a_b", "c"))
        self.assertLessEqual(len(capabilities._mcp_name("long" * 80, "name" * 80)), 64)

    @unittest.skipUnless(os.name == "nt" and shutil.which("npx"), "Requires the Windows npx shim")
    def test_windows_npx_resolves_native_node_without_cmd_shell(self):
        command = capabilities._stdio_command({"command": "npx", "args": ["--version"]})
        self.assertEqual(Path(command[0]).name.lower(), "node.exe")
        self.assertTrue(command[1].endswith("npx-cli.js"))
        result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+")

    def test_streamable_http_mcp_session_headers_and_sse_call(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append({"method": request["method"], "session": self.headers.get("Mcp-Session-Id"), "auth": self.headers.get("Authorization")})
                if "id" not in request:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                method = request["method"]
                if method == "initialize":
                    result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "http-fixture", "version": "1"}}
                elif method == "tools/list":
                    result = {"tools": [{"name": "echo", "description": "Echo HTTP", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
                else:
                    result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
                payload = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
                is_sse = method == "tools/call"
                raw = (("event: message\ndata: " + payload + "\n\n") if is_sse else payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if is_sse else "application/json")
                self.send_header("Mcp-Session-Id", "test-session")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_DELETE(self):
                requests.append({"method": "DELETE", "session": self.headers.get("Mcp-Session-Id"), "auth": self.headers.get("Authorization")})
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        capabilities.save_mcp(self.db, {"mcpServers": {"http": {"url": url, "headers": {"Authorization": "Bearer fixture-secret"}}}})
        capabilities.discover_mcp(self.db, "http")
        name = capabilities.mcp_tool_specs(self.db, "a")[0]["function"]["name"]
        result = capabilities.invoke_mcp_tool(self.db, "a", name, {"text": "HTTP over SSE"})
        self.assertEqual(result["content"][0]["text"], "HTTP over SSE")
        self.assertTrue(all(request["auth"] == "Bearer fixture-secret" for request in requests))
        self.assertTrue(all(request["session"] == "test-session" for request in requests if request["method"] != "initialize"))
        self.assertEqual(sum(item["method"] == "DELETE" for item in requests), 2)

    def test_routes_ignore_other_extensions(self):
        self.assertIsNone(capabilities.route("GET", "/api/other", {}, {}, self.db))
        with self.assertRaises(ValueError):
            capabilities.route("DELETE", "/api/capabilities/mcp", {}, {}, self.db)


if __name__ == "__main__":
    unittest.main()
