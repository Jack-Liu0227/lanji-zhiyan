"""Editable local skills, tool policy and explicitly connected MCP servers.

MCP discovery is an explicit user action. Rendering settings or starting an
assistant never launches an executable supplied by a configuration document.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import re
import securestore              # DPAPI 凭据保护（见 workspace-core/securestore.py）
import paths
import skill_registry
import shutil
import subprocess
import threading
import time
from typing import Any
import urllib.parse
import urllib.request

MASK = "********"
MAX_SKILL_BYTES = 1024 * 1024
_SECRET = re.compile(r"key|token|secret|password|authorization|credential", re.I)


def _conn(db):
    return db.db if hasattr(db, "db") else db


def _ensure(db):
    conn = _conn(db)
    for sql in (
        "CREATE TABLE IF NOT EXISTS capability_bindings (assistant_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(assistant_id,kind,item_id))",
        "CREATE TABLE IF NOT EXISTS capability_tool_metadata (name TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS capability_mcp_config (id INTEGER PRIMARY KEY CHECK(id=1), protected TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS capability_mcp_tools (server TEXT PRIMARY KEY, config_hash TEXT NOT NULL, tools TEXT NOT NULL DEFAULT '[]')",
    ):
        conn.execute(sql)
    conn.commit()
    return conn


def _assistants(db) -> list[dict]:
    conn = _conn(db)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='assistants'").fetchone():
        return []
    return [dict(row) for row in conn.execute("SELECT id,name,source,enabled FROM assistants ORDER BY sort_order,name")]


def _project_dir(db, body: dict, query: dict) -> str:
    def value(name):
        raw = body.get(name, query.get(name, ""))
        return str(raw[0] if isinstance(raw, list) and raw else raw or "")
    project_id = value("project_id")
    if project_id:
        item = _conn(db).execute("SELECT base_dir FROM research_tasks WHERE id=?", (project_id,)).fetchone()
        if not item:
            raise ValueError("Project not found")
        return item["base_dir"] or ""
    return value("project_dir")


def skill_roots(project_dir: str = "") -> list[tuple[str, Path]]:
    roots = [("builtin", paths.APP / ".agents" / "skills"),
             ("user", Path.home() / ".agents" / "skills")]
    if project_dir:
        roots.append(("project", Path(project_dir).expanduser().resolve() / ".agents" / "skills"))
    return roots


def _skill_frontmatter(text: str, fallback: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return fallback, ""
    block = text.split("---", 2)[1]
    try:
        import yaml
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return str(data.get("name") or fallback), str(data.get("description") or "")
    except Exception:
        pass
    name = re.search(r"^name:\s*(.+)$", block, re.M)
    desc = re.search(r"^description:\s*(.+)$", block, re.M)
    return (name.group(1).strip().strip("\"'") if name else fallback,
            desc.group(1).strip().strip("\"'") if desc else "")


def list_skills(project_dir: str = "") -> list[dict]:
    found: dict[str, dict] = {}
    for scope, root in skill_roots(project_dir):
        root = root.resolve()
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or resolved.stat().st_size > MAX_SKILL_BYTES:
                    continue
                text = resolved.read_text(encoding="utf-8-sig")
                name, description = _skill_frontmatter(text, path.parent.name)
                found[name] = {"id": name, "name": name, "desc": description,
                               "description": description, "scope": scope,
                               "path": str(resolved), "dir": str(resolved.parent)}
            except (OSError, UnicodeError):
                continue
    return sorted(found.values(), key=lambda item: item["name"].casefold())


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def write_skill(name: str, description: str, content: str, scope: str = "project",
                project_dir: str = "", mode: str = "replace") -> dict:
    """模型自己写 skill —— 这是「自我升级」的落点：跑完一类活儿，把做法沉淀成 SKILL.md，
    下次同类任务靠 description 被索引到、按需读全文。

    边界（别松）：名字只能是 [a-z0-9._-]，只能写 skill 根下自己的目录，正文 1 MiB 上限，
    frontmatter 的 name 必须等于目录名 —— 否则已有的助手绑定会找不到它。
    """
    name = str(name or "").strip().lower()
    if not SKILL_NAME.match(name):
        raise ValueError("skill 名字只能用小写字母/数字/-/._，且不超过 64 字符")
    scope = "user" if scope == "user" else "project"
    if scope == "project" and not project_dir:
        raise ValueError("这个项目没设文件夹，写不了项目级 skill；先设项目位置")
    root = (Path(project_dir).expanduser().resolve() / ".agents" / "skills") if scope == "project" \
        else (Path.home() / ".agents" / "skills")
    folder = (root / name).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError("skill 路径越界")
    target = folder / "SKILL.md"
    body = str(content or "")
    if body.startswith("---"):                    # 模型自己带了 frontmatter：只校验名字，别动它的字
        head_name, _ = _skill_frontmatter(body, name)
        if head_name != name:
            raise ValueError("frontmatter 里的 name 必须等于 skill 名字：" + name)
        text = body
    else:
        if not str(description or "").strip():
            raise ValueError("新建 skill 必须有 description（一行说清什么时候用它）")
        text = "---\nname: %s\ndescription: %s\n---\n\n%s\n" % (name, " ".join(description.split()), body.strip())
    if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
        raise ValueError("skill 正文超过 1 MiB")
    folder.mkdir(parents=True, exist_ok=True)
    exists = target.is_file()
    if exists and mode == "create":
        raise ValueError("同名 skill 已存在；要改它用 mode=append/replace")
    if exists and mode == "append":
        text = target.read_text(encoding="utf-8-sig").rstrip() + "\n\n" + body.strip() + "\n"
    target.write_text(text, encoding="utf-8")
    return {"id": name, "name": name, "scope": scope, "path": str(target),
            "created": not exists, "chars": len(text)}


def _skill(item_id: str, project_dir: str) -> dict:
    item = next((item for item in list_skills(project_dir) if item["id"] == item_id), None)
    if not item:
        raise ValueError("Skill not found in the configured user/project roots")
    return item


def _builtin_specs() -> list[dict]:
    tools: dict[str, dict] = {}
    for module_name in ("workflow", "agent_integrations"):
        try:
            module = importlib.import_module(module_name)
            specs = getattr(module, "TOOL_SPECS", []) if module_name == "workflow" else module.tool_specs()
            # Include specialized workflow tools too, not only the general agent.
            if module_name == "workflow":
                specs = list(specs)
                for name in ("IDEA_TOOLS", "MAP_TOOLS", "OUTLINE_TOOLS", "WRITE_TOOLS", "REVIEW_TOOLS"):
                    specs.extend(getattr(module, name, []) or [])
                if hasattr(module, "doc_tools"):
                    for kind in ("outline", "write", "review"):
                        specs.extend(module.doc_tools(kind))
                if hasattr(module, "map_tools"):
                    specs.extend(module.map_tools())
            for spec in specs:
                if spec.get("function", {}).get("name"):
                    tools[spec["function"]["name"]] = copy.deepcopy(spec)
        except (ImportError, AttributeError):
            continue
    return list(tools.values())


def list_tools(db) -> list[dict]:
    conn = _ensure(db)
    metadata = {row["name"]: dict(row) for row in conn.execute("SELECT * FROM capability_tool_metadata")}
    items = []
    for spec in _builtin_specs():
        fn = spec["function"]
        override = metadata.get(fn["name"], {})
        items.append({"id": fn["name"], "name": fn["name"], "label": override.get("label") or fn["name"],
                      "description": override.get("description") or fn.get("description", ""),
                      "parameters": fn.get("parameters", {}), "kind": "builtin"})
    return sorted(items, key=lambda item: item["name"])


def enabled(db, assistant_id: str, kind: str, item_id: str) -> bool:
    conn = _ensure(db)
    # Global defaults can be narrowed or overridden for an individual assistant.
    for aid in (assistant_id, "*") if assistant_id and assistant_id != "*" else ("*",):
        row = conn.execute("SELECT enabled FROM capability_bindings WHERE assistant_id=? AND kind=? AND item_id=?",
                           (aid, kind, item_id)).fetchone()
        if row is not None:
            return bool(row[0])
    return True


def filter_tools(db, assistant_id: str, tools: list[dict], project_dir: str = "") -> list[dict]:
    conn = _ensure(db)
    metadata = {row["name"]: dict(row) for row in conn.execute("SELECT * FROM capability_tool_metadata")}
    output = []
    for spec in tools:
        fn = spec.get("function", {})
        name = fn.get("name", "")
        if not enabled(db, assistant_id, "tool", name):
            continue
        if name.startswith("mcp_") and name != "mcp_register":
            match = _cached_mcp_tool(db, name)
            if not match or not enabled(db, assistant_id, "mcp", match[0]):
                continue
        clone = copy.deepcopy(spec)
        if metadata.get(name, {}).get("description"):
            clone["function"]["description"] = metadata[name]["description"]
        output.append(clone)
    return output


def skill_context(db, assistant_id: str, project_dir: str = "") -> str:
    items = [item for item in list_skills(project_dir) if enabled(db, assistant_id, "skill", item["id"])]
    if not items:
        return ""
    return ("\n\n[Enabled skills]\nUse skill_read with the exact skill_id before applying a skill. "
            "Disabled skills are unavailable. Project skills override user skills with the same name.\n" +
            "\n".join(f"- {item['id']}: {item['description']}" for item in items))


def steward_context(db, assistant_id: str, project_dir: str = "") -> str:
    """Load the steward entrypoint for ordinary steward turns, honoring overrides."""
    if assistant_id != "builtin-steward" or not enabled(db, assistant_id, "tool", "skill_read"):
        return ""
    if not enabled(db, assistant_id, "skill", "research-steward"):
        return ""
    if not any(item["id"] == "research-steward" for item in list_skills(project_dir)):
        return ""
    item = invoke_tool(db, assistant_id, "skill_read", {"skill_id": "research-steward"}, project_dir)
    return "\n\n[Active skill: research-steward]\n" + item["content"]


def install_steward_skill(project_dir: str | Path) -> None:
    """Seed new projects from the maintained skill; preserve existing user edits."""
    root = Path(project_dir).resolve()
    source = paths.APP / ".agents" / "skills" / "research-steward"
    target = root / ".agents" / "skills" / "research-steward"
    if not target.resolve().is_relative_to(root):
        raise ValueError("项目 Skill 路径越过项目文件夹")
    for relative in ("SKILL.md", "references/actions.md", "references/zotero.md"):
        dest = target / relative
        if not dest.resolve().is_relative_to(root):
            raise ValueError("项目 Skill 路径越过项目文件夹")
        if (source / relative).is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("x", encoding="utf-8") as stream:
                stream.write((source / relative).read_text(encoding="utf-8"))


def _protect(data: dict) -> str:
    """MCP 配置整体加密（DPAPI 那套实现搬到 securestore，这里只留格式约定）。"""
    return securestore.protect(json.dumps(data, ensure_ascii=False))


def _unprotect(value: str) -> dict:
    return json.loads(securestore.unprotect(value))


def default_mcp_config() -> dict:
    import sys
    zotero_script = str(Path(__file__).resolve().parents[1] / "connectors" / "zotero_mcp.py")
    return {
        "mcpServers": {
            "zotero": {
                "command": sys.executable or "python",
                "args": [zotero_script],
                "cwd": ".",
                "transport": "stdio",
                "enabled": True,
                "description": "Zotero 本机题录检索、PDF直读、文献更新与笔记写入"
            }
        }
    }


def ensure_zotero_mcp(db) -> dict:
    cfg = _config(db)
    servers = cfg.setdefault("mcpServers", {})
    import sys
    zotero_script = str(Path(__file__).resolve().parents[1] / "connectors" / "zotero_mcp.py")
    servers["zotero"] = {
        "command": sys.executable or "python",
        "args": [zotero_script],
        "cwd": ".",
        "transport": "stdio",
        "enabled": True,
        "description": "Zotero 本机题录检索、PDF直读、文献更新与笔记写入"
    }
    save_mcp(db, cfg)
    return {"ok": True, "server": servers["zotero"]}


def _config(db) -> dict:
    row = _ensure(db).execute("SELECT protected FROM capability_mcp_config WHERE id=1").fetchone()
    if row:
        return _unprotect(row[0])
    initial = default_mcp_config()
    conn = _conn(db)
    conn.execute("INSERT OR REPLACE INTO capability_mcp_config(id,protected) VALUES(1,?)", (_protect(initial),))
    conn.commit()
    return initial


def _redact(value: Any, key: str = "") -> Any:
    if key in ("env", "headers") and isinstance(value, dict):
        return {name: MASK if item else item for name, item in value.items()}
    if _SECRET.search(key) and isinstance(value, (str, int, float)):
        return MASK if value else value
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        # Command arguments often contain bearer tokens, so their values require
        # the same explicit reveal action as environment variables and headers.
        if key == "args":
            return [MASK for _ in value]
        return [_redact(item) for item in value]
    if key == "url" and isinstance(value, str):
        parsed = urllib.parse.urlsplit(value)
        if parsed.query or parsed.username or parsed.password:
            return MASK
    return value


def _restore_mask(new: Any, old: Any) -> Any:
    # The HTTP server's generic api_key scrubber uses a shorter mask.
    if isinstance(new, str) and new in (MASK, "***"):
        if old is None:
            raise ValueError("A masked secret has no saved value; enter the actual value")
        return old
    if isinstance(new, dict):
        previous = old if isinstance(old, dict) else {}
        return {key: _restore_mask(value, previous.get(key)) for key, value in new.items()}
    if isinstance(new, list):
        previous = old if isinstance(old, list) else []
        return [_restore_mask(value, previous[i] if i < len(previous) else None) for i, value in enumerate(new)]
    return new


def _validate_config(data: Any) -> dict:
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        raise ValueError('Use an object with an "mcpServers" object')
    if set(data) != {"mcpServers"}:
        raise ValueError('Only the "mcpServers" root field is supported')
    allowed = {"command", "args", "env", "cwd", "url", "headers", "enabled", "transport", "timeout", "description"}
    for name, server in data["mcpServers"].items():
        if not name or not isinstance(server, dict):
            raise ValueError("Each MCP server needs a name and configuration object")
        if set(server) - allowed:
            raise ValueError("Unknown MCP fields: " + ", ".join(sorted(set(server) - allowed)))
        if bool(server.get("command")) == bool(server.get("url")):
            raise ValueError("Each MCP server requires either command (stdio) or url (streamable HTTP)")
        if server.get("transport") not in (None, "stdio", "http", "streamable-http"):
            raise ValueError("Supported MCP transports are stdio and streamable-http")
        if server.get("url"):
            parsed = urllib.parse.urlsplit(server["url"])
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("MCP URL must be an absolute http(s) URL")
        if "command" in server and (not isinstance(server["command"], str) or not server["command"].strip()):
            raise ValueError("MCP command must be a nonempty executable name")
        if not isinstance(server.get("args", []), list) or not all(isinstance(arg, str) for arg in server.get("args", [])):
            raise ValueError("MCP args must be a string array")
        for field in ("env", "headers"):
            if not isinstance(server.get(field, {}), dict) or not all(isinstance(v, str) for v in server.get(field, {}).values()):
                raise ValueError(f"MCP {field} must map names to string values")
        if "enabled" in server and not isinstance(server["enabled"], bool):
            raise ValueError("MCP enabled must be true or false")
        for field in ("cwd", "description", "transport"):
            if field in server and not isinstance(server[field], str):
                raise ValueError(f"MCP {field} must be text")
        if "timeout" in server and (not isinstance(server["timeout"], (int, float)) or not 1 <= server["timeout"] <= 120):
            raise ValueError("MCP timeout must be between 1 and 120 seconds")
    return data


def _fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def save_mcp(db, data: Any) -> dict:
    conn = _ensure(db)
    if isinstance(data, str):
        data = json.loads(data)
    value = _validate_config(_restore_mask(data, _config(db)))
    conn.execute("INSERT OR REPLACE INTO capability_mcp_config(id,protected) VALUES(1,?)", (_protect(value),))
    for row in conn.execute("SELECT server,config_hash FROM capability_mcp_tools").fetchall():
        config = value["mcpServers"].get(row["server"])
        if not config or _fingerprint(config) != row["config_hash"]:
            conn.execute("DELETE FROM capability_mcp_tools WHERE server=?", (row["server"],))
    conn.commit()
    return {"ok": True, "config": _redact(value), "storage_security": storage_security()}


def storage_security() -> str:
    return securestore.security()


def _rpc_error(payload: dict) -> Any:
    if payload.get("error"):
        raise ValueError("MCP server returned an RPC error")
    return payload.get("result", {})


def _stdio_command(config: dict) -> list[str]:
    command = config["command"]
    args = list(config.get("args", []))
    resolved = shutil.which(command) or command
    if os.name == "nt" and Path(resolved).suffix.lower() in (".cmd", ".bat"):
        # npx/npm are Windows command shims, not executables CreateProcess can
        # launch. Use their actual Node entry point without shell interpolation.
        path = Path(resolved)
        stem = path.stem.lower()
        if stem in ("npx", "npm"):
            cli = path.parent / "node_modules" / "npm" / "bin" / (stem + "-cli.js")
            node = path.parent / "node.exe"
            node_command = str(node) if node.is_file() else shutil.which("node")
            if cli.is_file() and node_command:
                return [node_command, str(cli), *args]
        raise ValueError("Windows batch shims require a native executable; use node with its JavaScript entry point")
    return [resolved, *args]


_SERVER_SEMAPHORES: dict[str, threading.Semaphore] = {}
_SEMAPHORE_LOCK = threading.Lock()

def _get_mcp_semaphore(server_key: str, limit: int = 4) -> threading.Semaphore:
    with _SEMAPHORE_LOCK:
        if server_key not in _SERVER_SEMAPHORES:
            _SERVER_SEMAPHORES[server_key] = threading.Semaphore(limit)
        return _SERVER_SEMAPHORES[server_key]


class _MCPConnection:
    def __init__(self, config: dict):
        self.config = config
        self.server_key = str(config.get("name") or config.get("url") or config.get("command") or "mcp")
        self.timeout = float(config.get("timeout", 30))
        self.proc = None
        self.lines = queue.Queue()
        self.session_id = ""
        self.protocol_version = "2025-03-26"
        self.counter = 0
        self.stderr = bytearray()

    def __enter__(self):
        if self.config.get("command"):
            env = os.environ.copy()
            env.update(self.config.get("env", {}))
            self.proc = subprocess.Popen(_stdio_command(self.config),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         cwd=self.config.get("cwd") or None, env=env,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._stdout, daemon=True).start()
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            result = self.call("initialize", {"protocolVersion": self.protocol_version, "capabilities": {},
                                               "clientInfo": {"name": "research-workbench", "version": "1.0"}})
            negotiated = result.get("protocolVersion", self.protocol_version)
            if negotiated not in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
                raise ValueError("MCP server negotiated an unsupported protocol version")
            self.protocol_version = negotiated
            self.call("notifications/initialized", {}, notification=True)
        except Exception:
            self.close()
            raise
        return self

    def _stdout(self):
        try:
            for line in self.proc.stdout:
                if len(line) > 16 * 1024 * 1024:
                    self.lines.put(ValueError("MCP response exceeds 16 MiB"))
                    break
                try:
                    self.lines.put(json.loads(line))
                except (ValueError, UnicodeError):
                    continue
        finally:
            self.lines.put(EOFError("MCP process closed its output"))

    def _drain_stderr(self):
        # Drain without logging: stderr may contain credentials or private data.
        # Enforce max 2 MiB stderr limit to prevent uncontrolled memory/CPU exhaustion.
        total = 0
        max_bytes = 2 * 1024 * 1024
        try:
            while chunk := self.proc.stderr.read(4096):
                total += len(chunk)
                if total > max_bytes:
                    try:
                        self.proc.terminate()
                    except OSError:
                        pass
                    break
        except Exception:
            pass

    def call(self, method: str, params: dict, notification: bool = False):
        with _get_mcp_semaphore(self.server_key):
            return self._call_inner(method, params, notification=notification)

    def _call_inner(self, method: str, params: dict, notification: bool = False):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.counter
        if self.proc:
            self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            if notification:
                return {}
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    item = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    raise TimeoutError("MCP server response timed out") from None
                if isinstance(item, Exception):
                    raise item
                if item.get("id") == payload["id"] and "method" not in item:
                    return _rpc_error(item)
                if item.get("method") and "id" in item:
                    response = {"jsonrpc": "2.0", "id": item["id"], "error": {"code": -32601, "message": "Client method not supported"}}
                    self.proc.stdin.write((json.dumps(response) + "\n").encode())
                    self.proc.stdin.flush()
                if time.monotonic() >= deadline:
                    raise TimeoutError("MCP server response timed out")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": self.protocol_version, **self.config.get("headers", {})}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.config["url"], json.dumps(payload).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
            if notification:
                return {}
            if "text/event-stream" in response.headers.get("Content-Type", ""):
                # Return as soon as the requested event arrives; a stream may
                # intentionally stay open after delivering the RPC response.
                data_lines = []
                for raw_line in response:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        item = json.loads("\n".join(data_lines))
                        data_lines = []
                        if item.get("id") == payload["id"]:
                            return _rpc_error(item)
                raise ValueError("MCP HTTP stream ended without a matching response")
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("MCP response exceeds 16 MiB")
            item = json.loads(raw)
            if item.get("id") != payload["id"]:
                raise ValueError("MCP returned a mismatched response id")
            return _rpc_error(item)

    def close(self):
        if self.proc:
            try:
                if self.proc.poll() is None:
                    try:
                        self.proc.terminate()
                    except OSError:
                        pass
                try:
                    self.proc.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=2)
                    except OSError:
                        pass
            finally:
                for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    if stream and not getattr(stream, "closed", True):
                        try:
                            stream.close()
                        except Exception:
                            pass
        elif self.session_id:
            try:
                headers = {**self.config.get("headers", {}), "Mcp-Session-Id": self.session_id,
                           "MCP-Protocol-Version": self.protocol_version}
                with urllib.request.urlopen(urllib.request.Request(self.config["url"], headers=headers, method="DELETE"), timeout=5):
                    pass
            except Exception:
                pass

    def __exit__(self, *exc):
        self.close()


def discover_mcp(db, server: str) -> dict:
    conn = _ensure(db)
    config = _config(db)["mcpServers"].get(server)
    if not config or not config.get("enabled", True):
        raise ValueError("MCP server is missing or disabled")
    tools = []
    with _MCPConnection(config) as client:
        cursor = None
        cursors = set()
        for _ in range(100):
            page = client.call("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(page.get("tools"), list):
                raise ValueError("MCP tools/list must return a tools array")
            tools.extend(page["tools"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in cursors:
                raise ValueError("MCP returned a repeated pagination cursor")
            cursors.add(cursor)
        else:
            raise ValueError("MCP tools/list exceeded 100 pages")
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not isinstance(tool.get("inputSchema", {}), dict):
            raise ValueError("MCP returned an invalid tool schema")
    conn.execute("INSERT OR REPLACE INTO capability_mcp_tools VALUES(?,?,?)",
                 (server, _fingerprint(config), json.dumps(tools)))
    conn.commit()
    return {"ok": True, "server": server, "tools": tools}


def _mcp_name(server: str, name: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9_]", "_", f"{server}_{name}")[:42]
    suffix = hashlib.sha256(f"{server}\0{name}".encode()).hexdigest()[:12]
    return f"mcp_{label}_{suffix}"


def _cached_tools(db):
    config = _config(db)["mcpServers"]
    for row in _ensure(db).execute("SELECT * FROM capability_mcp_tools"):
        server = row["server"]
        spec = config.get(server)
        if not spec or not spec.get("enabled", True) or row["config_hash"] != _fingerprint(spec):
            continue
        for tool in json.loads(row["tools"]):
            yield server, tool


def _cached_mcp_tool(db, name: str):
    return next(((server, tool) for server, tool in _cached_tools(db) if _mcp_name(server, tool["name"]) == name), None)


def mcp_tool_specs(db, assistant_id: str, project_dir: str = "") -> list[dict]:
    specs = []
    for server, tool in _cached_tools(db):
        name = _mcp_name(server, tool["name"])
        if enabled(db, assistant_id, "mcp", server) and enabled(db, assistant_id, "tool", name):
            specs.append({"type": "function", "function": {"name": name,
                          "description": f"MCP {server}: {tool.get('description', tool['name'])}",
                          "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}}}})
    return specs


def capability_tool_specs(db, assistant_id: str, project_dir: str = "") -> list[dict]:
    specs = mcp_tool_specs(db, assistant_id, project_dir)
    if any(enabled(db, assistant_id, "skill", item["id"]) for item in list_skills(project_dir)):
        specs.append({"type": "function", "function": {"name": "skill_read",
                      "description": "Read an enabled skill or one of its relative text resources.",
                      "parameters": {"type": "object", "properties": {"skill_id": {"type": "string"},
                          "resource": {"type": "string", "description": "Relative path inside the skill, default SKILL.md"}}, "required": ["skill_id"]}}})
    if enabled(db, assistant_id, "tool", "skill_write"):
        specs.append({"type": "function", "function": {
            "name": "skill_write",
            "description": "把一个可复用的做法写成 skill（SKILL.md）：跑完一类活儿、踩过坑、或用户定了处理流程时用。"
                           "description 写「什么时候该用它」，下次同类任务会按它被索引到。不是记事实（那是 memory）。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "小写字母/数字/-，例如 paper-figure-style"},
                "description": {"type": "string", "description": "一行：什么场景下该读这个 skill"},
                "content": {"type": "string", "description": "Markdown 正文：步骤、判据、坑、可复制的命令"},
                "scope": {"type": "string", "enum": ["project", "user"],
                          "description": "project=只在这个项目用；user=跨项目（放 ~/.agents/skills）"},
                "mode": {"type": "string", "enum": ["create", "replace", "append"], "description": "默认 replace"}},
                "required": ["name", "description", "content"]}}})
    return filter_tools(db, assistant_id, specs, project_dir)


def invoke_mcp_tool(db, assistant_id: str, name: str, args: dict, project_dir: str = "") -> Any:
    match = _cached_mcp_tool(db, name)
    if not match:
        raise ValueError("MCP tool is not discovered; explicitly connect the server in Settings first")
    server, tool = match
    if not enabled(db, assistant_id, "mcp", server) or not enabled(db, assistant_id, "tool", name):
        raise PermissionError("This MCP tool is disabled for the assistant")
    config = _config(db)["mcpServers"][server]
    try:
        with _MCPConnection(config) as client:
            return client.call("tools/call", {"name": tool["name"], "arguments": args})
    except (OSError, ValueError, TimeoutError, EOFError) as exc:
        raise ValueError(f"MCP tool call failed ({type(exc).__name__}); inspect the trusted server logs") from None


def invoke_tool(db, assistant_id: str, name: str, args: dict, project_dir: str = "") -> Any:
    if name == "skill_write":
        if not enabled(db, assistant_id, "tool", name):
            raise PermissionError("This tool is disabled for the assistant")
        return write_skill(str(args.get("name") or ""), str(args.get("description") or ""),
                           str(args.get("content") or ""), str(args.get("scope") or "project"),
                           project_dir, str(args.get("mode") or "replace"))
    if name == "skill_read":
        item_id = str(args.get("skill_id", ""))
        if not enabled(db, assistant_id, "tool", name) or not enabled(db, assistant_id, "skill", item_id):
            raise PermissionError("This skill is disabled for the assistant")
        item = _skill(item_id, project_dir)
        relative = str(args.get("resource") or "SKILL.md").replace("\\", "/")
        if relative.startswith("/") or ":" in relative or any(p in ("", ".", "..") for p in relative.split("/")):
            raise ValueError("Skill resource must be a relative path inside the skill")
        root = Path(item["dir"])
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Skill resource not found inside the skill")
        if target.stat().st_size > MAX_SKILL_BYTES:
            raise ValueError("Skill resource exceeds 1 MiB")
        return {**item, "resource": relative, "content": target.read_text(encoding="utf-8-sig")}
    if name.startswith("mcp_"):
        return invoke_mcp_tool(db, assistant_id, name, args, project_dir)
    return None


def settings_meta(db, project_dir: str = "") -> dict:
    conn = _ensure(db)
    skills = list_skills(project_dir)
    installed = skill_registry.installations(db)
    for skill in skills:
        skill["installation"] = installed.get(skill["path"])
    config = _redact(_config(db))
    discovered: dict[str, list] = {}
    for server, tool in _cached_tools(db):
        discovered.setdefault(server, []).append({**tool, "runtime_name": _mcp_name(server, tool["name"])})
    mcp = [{"id": name, "name": name, "enabled": server.get("enabled", True),
            "transport": "stdio" if server.get("command") else "streamable-http",
            "cmd": server.get("command") or server.get("url", ""),
            "description": server.get("description", ""), "tools": discovered.get(name, [])}
           for name, server in config["mcpServers"].items()]
    return {"tools": list_tools(db), "skills": skills, "skills_total": len(skills),
            "skill_roots": [{"scope": scope, "path": str(path), "exists": path.is_dir()} for scope, path in skill_roots(project_dir)],
            "mcp": mcp, "mcp_config": config, "storage_security": storage_security(),
            "assistants": _assistants(db), "bindings": [dict(row) for row in conn.execute("SELECT * FROM capability_bindings")],
            "default_enabled": True, "project_dir": project_dir}


def route(method: str, path: str, body: dict, query: dict, db) -> dict | None:
    if not path.startswith("/api/capabilities"):
        return None
    body = body or {}
    query = query or {}
    project_dir = _project_dir(db, body, query)
    registry_response = skill_registry.route(method, path, body, query, db, project_dir)
    if registry_response is not None:
        return registry_response
    if method == "GET" and path == "/api/capabilities":
        return settings_meta(db, project_dir)
    if path == "/api/capabilities/skill" and method in ("GET", "POST"):
        raw = body.get("id", query.get("id", ""))
        item = _skill(str(raw[0] if isinstance(raw, list) and raw else raw), project_dir)
        target = Path(item["path"])
        if method == "POST":
            content = body.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SKILL_BYTES:
                raise ValueError("Skill content must be text, at most 1 MiB")
            # Preserve the stable name used by existing assistant bindings.
            name, _ = _skill_frontmatter(content, target.parent.name)
            if name != item["name"]:
                raise ValueError("Changing the skill name would break assistant bindings; keep its current name")
            target.write_text(content, encoding="utf-8")
        return {**item, "content": target.read_text(encoding="utf-8-sig")}
    if method == "POST" and path == "/api/capabilities/tool":
        name = str(body.get("name", ""))
        if name not in {item["id"] for item in list_tools(db)}:
            raise ValueError("Built-in tool not found")
        label, description = body.get("label", name), body.get("description", "")
        if not isinstance(label, str) or not isinstance(description, str) or len(description) > 16000:
            raise ValueError("Tool label/description must be text (description at most 16000 characters)")
        conn = _ensure(db)
        conn.execute("INSERT OR REPLACE INTO capability_tool_metadata VALUES(?,?,?)", (name, label, description))
        conn.commit()
        return {"ok": True}
    if method == "POST" and path in ("/api/capabilities/binding", "/api/capabilities/bindings"):
        conn = _ensure(db)
        if "bindings" in body and isinstance(body["bindings"], list):
            items = body["bindings"]
            for b in items:
                aid = str(b.get("assistant_id", ""))
                kind = str(b.get("kind", ""))
                item_id = str(b.get("item_id", ""))
                enabled_val = int(bool(b.get("enabled")))
                if kind in ("skill", "tool", "mcp") and item_id:
                    conn.execute("INSERT OR REPLACE INTO capability_bindings VALUES(?,?,?,?)",
                                 (aid, kind, item_id, enabled_val))
            conn.commit()
            return {"ok": True, "count": len(items)}
        aid, kind, item_id = (str(body.get(key, "")) for key in ("assistant_id", "kind", "item_id"))
        if aid != "*" and aid not in {item["id"] for item in _assistants(db)}:
            raise ValueError("Assistant not found")
        if kind not in ("skill", "tool", "mcp") or not item_id or not isinstance(body.get("enabled"), bool):
            raise ValueError("Supply kind (skill/tool/mcp), item_id and a boolean enabled")
        valid = ({item["id"] for item in list_skills(project_dir)} if kind == "skill" else
                 set(_config(db)["mcpServers"]) if kind == "mcp" else
                 {item["id"] for item in list_tools(db)} | {"skill_read", "skill_write"} | {_mcp_name(s, t["name"]) for s, t in _cached_tools(db)})
        if item_id not in valid:
            raise ValueError("Capability not found")
        conn.execute("INSERT OR REPLACE INTO capability_bindings VALUES(?,?,?,?)", (aid, kind, item_id, int(body["enabled"])))
        conn.commit()
        return {"ok": True}
    if path == "/api/capabilities/mcp" and method == "GET":
        return {"config": _redact(_config(db)), "storage_security": storage_security()}
    if path == "/api/capabilities/mcp/reveal" and method == "POST":
        if body.get("confirm") is not True:
            raise ValueError("Explicit confirmation is required to reveal credentials")
        return {"config": _config(db), "storage_security": storage_security()}
    if path == "/api/capabilities/mcp" and method == "POST":
        return save_mcp(db, body.get("config"))
    if path == "/api/capabilities/mcp/discover" and method == "POST":
        if body.get("confirm") is not True:
            raise ValueError("Explicit confirmation is required before executing/connecting an MCP server")
        try:
            return discover_mcp(db, str(body.get("server", "")))
        except (OSError, ValueError, TimeoutError, EOFError) as exc:
            # Do not echo server output, URLs, executable arguments or keys.
            raise ValueError(f"MCP connection failed ({type(exc).__name__}); check executable/URL, credentials and server logs") from None
    raise ValueError("Unknown capabilities route or unsupported HTTP method")
