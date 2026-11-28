"""Zotero MCP status and bootstrap routes."""
from __future__ import annotations

import json
from typing import Any

import capabilities
import zotero

_STATUS_CACHE: dict[str, dict[str, Any]] = {}


def _status(db, *, check: bool = False) -> dict[str, Any]:
    server = capabilities._config(db).get("mcpServers", {}).get("zotero") or {}
    result = {"registered": bool(server), "enabled": bool(server.get("enabled", False)),
              "checked": False, "connected": False, "tools_count": 0,
              "local_readable": False, "writable": False, "zotero_installed": bool(zotero.find_zotero_executable()),
              "error": ""}
    if not result["enabled"]:
        return {**result, "server": server}
    fingerprint = capabilities._fingerprint(server)
    cached = _STATUS_CACHE.get(fingerprint)
    if not check and cached:
        return {**result, **cached, "server": server}
    if not check:
        result["error"] = "尚未测试连接"
        return {**result, "server": server}
    try:
        discovered = capabilities.discover_mcp(db, "zotero", probe_tool="zotero_status")
        result["tools_count"] = len(discovered["tools"])
        probe = discovered["probe"]
        payload = next((item.get("text") for item in probe.get("content", [])
                        if isinstance(item, dict) and item.get("type") == "text"
                        and isinstance(item.get("text"), str)), "")
        status = json.loads(payload) if payload else {}
        result["connected"] = not bool(probe.get("isError"))
        result["checked"] = True
        result["local_readable"] = bool((status.get("local") or {}).get("readable"))
        result["local_message"] = (status.get("local") or {}).get("message", "")
        result["writable"] = bool(status.get("writable"))
        result["write_hint"] = str(status.get("write_hint") or "")
        if not result["connected"]:
            result["error"] = "Zotero MCP 服务已启动，但 zotero_status 调用失败"
    except (OSError, ValueError, TimeoutError, EOFError, FileNotFoundError) as exc:
        result["checked"] = True
        result["error"] = f"Zotero MCP 连接检查失败（{type(exc).__name__}）"
    _STATUS_CACHE.clear()
    _STATUS_CACHE[fingerprint] = {key: value for key, value in result.items()
                                  if key not in ("server", "registered", "enabled")}
    return {**result, "server": server}


def route(method: str, path: str, body: dict[str, Any], query: dict[str, list[str]], db: Any) -> Any | None:
    if method == "GET" and path == "/api/zotero/mcp/status":
        return _status(db)
    if method == "POST" and path == "/api/zotero/mcp/enable":
        capabilities.ensure_zotero_mcp(db)
        return {**_status(db, check=True), "ok": True}
    if method == "POST" and path == "/api/zotero/mcp/check":
        return _status(db, check=True)
    if method == "POST" and path == "/api/zotero/mcp/launch-zotero":
        launched = zotero.launch_zotero()
        return {**launched, "status": _status(db, check=bool(launched.get("success")))}
    return None
