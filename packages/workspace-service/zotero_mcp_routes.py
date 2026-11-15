"""Zotero MCP status and bootstrap routes."""
from __future__ import annotations

from typing import Any

import capabilities


def route(method: str, path: str, body: dict[str, Any], query: dict[str, list[str]], db: Any) -> Any | None:
    if method == "GET" and path == "/api/zotero/mcp/status":
        server = capabilities._config(db).get("mcpServers", {}).get("zotero") or {}
        return {
            "registered": bool(server),
            "enabled": bool(server.get("enabled", False)),
            "server": server,
        }
    if method == "POST" and path == "/api/zotero/mcp/enable":
        return capabilities.ensure_zotero_mcp(db)
    return None
