"""Zotero MCP 服务（stdio）：给 agent 用的 Zotero 读 + 写工具。

读：Zotero 本机 Local API（127.0.0.1:23119，只读、无需 key、不起网）。
写：Zotero Web API（需 key；先取环境变量 ZOTERO_API_KEY / ZOTERO_LIBRARY_ID /
    ZOTERO_LIBRARY_TYPE，没有就取程序「设置」里已经存的那份，不重复填）。
不碰 zotero.sqlite，不改本机库内文件。

    python zotero_mcp.py        # stdin/stdout 走 JSON-RPC；日志只写 stderr
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
for _n in ("database", "workspace-core", "workspace-service", "connectors"):
    _p = str(_ROOT / "packages" / _n)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import zotero                                                    # noqa: E402
import zotero_sync                                               # noqa: E402

PROTOCOL = "2024-11-05"


class _DB:
    """给 zotero_sync 用的最小 db 壳：只读程序里那条 zotero_web 配置。"""

    def __init__(self) -> None:
        import paths
        self.db = sqlite3.connect(str(paths.DB_PATH))
        self.db.row_factory = sqlite3.Row


def _write_config() -> dict[str, Any]:
    key = os.environ.get("ZOTERO_API_KEY", "")
    if key:
        return {"api_key": key, "library_type": os.environ.get("ZOTERO_LIBRARY_TYPE", "user"),
                "library_id": os.environ.get("ZOTERO_LIBRARY_ID", "")}
    try:
        return zotero_sync.config(_DB())
    except Exception:
        return {}


# ---------------------------------------------------------------- 工具

def tool_status(_: dict) -> dict:
    out = {"local": zotero.status()}
    cfg = _write_config()
    out["writable"] = bool(cfg.get("api_key"))
    out["write_hint"] = "" if out["writable"] else (
        "写入需要 Zotero Web API key：在 https://www.zotero.org/settings/keys/new 生成后，"
        "填到本 MCP 服务的 env ZOTERO_API_KEY（Library ID 填 ZOTERO_LIBRARY_ID）。")
    return out


def tool_search(a: dict) -> dict:
    page = zotero.items(collection=a.get("collection", ""), q=a.get("q", ""),
                        start=int(a.get("start") or 0), limit=min(int(a.get("limit") or 20), 100))
    keep = ("key", "type", "title", "creators", "year", "venue", "doi", "tags", "abstract")
    return {"total": page["total"], "hasMore": page["hasMore"],
            "items": [{k: it[k] for k in keep} for it in page["items"]]}


def tool_item(a: dict) -> dict:
    return zotero.item(a.get("key", ""))


def tool_pdf(a: dict) -> dict:
    path = zotero.pdf_path(a.get("key", ""))
    return {"key": a.get("key", ""), "path": path,
            "found": bool(path), "hint": "" if path else "这条没有 PDF 附件（或附件不在本机）"}


def tool_create_item(a: dict) -> dict:
    cfg = _write_config()
    item = dict(a.get("item") or {})
    if not item.get("title"):
        raise ValueError("写入条目至少要给 title")
    item.setdefault("itemType", "journalArticle")
    item.pop("key", None)
    item.pop("version", None)
    if a.get("collection"):
        item["collections"] = [str(a["collection"])]
    return {"ok": True, "item": zotero_sync._saved(zotero_sync.request(cfg, "/items", "POST", [item]))}


def tool_create_note(a: dict) -> dict:
    cfg = _write_config()
    if not (a.get("parent_key") or "").strip():
        raise ValueError("写笔记要指定 parent_key（条目 key）")
    note = {"itemType": "note", "parentItem": a["parent_key"], "note": a.get("note") or "",
            "tags": [{"tag": str(t)} for t in (a.get("tags") or [])]}
    return {"ok": True, "item": zotero_sync._saved(zotero_sync.request(cfg, "/items", "POST", [note]))}


TOOLS: list[dict[str, Any]] = [
    {"name": "zotero_status", "run": tool_status,
     "description": "Zotero 本机连接状态 + 是否能写入（不能写会说明原因）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "zotero_search", "run": tool_search,
     "description": "按分类/关键词检索本机 Zotero 条目，返回题录（key/标题/作者/年份/期刊/DOI/标签/摘要）。",
     "inputSchema": {"type": "object", "properties": {
         "collection": {"type": "string", "description": "分类 key，空=全库"},
         "q": {"type": "string", "description": "标题/作者/标签关键词"},
         "limit": {"type": "integer", "description": "1-100，默认 20"},
         "start": {"type": "integer", "description": "分页起点，默认 0"}}}},
    {"name": "zotero_item", "run": tool_item,
     "description": "按条目 key 取单条完整题录（含摘要、标签、所在分类）。",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "zotero_pdf", "run": tool_pdf,
     "description": "这条论文在本机的 PDF 绝对路径（找不到返回 found=false）。",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "zotero_create_item", "run": tool_create_item,
     "description": "往 Zotero 写入一条文献（Web API，需要 key）。",
     "inputSchema": {"type": "object", "properties": {
         "item": {"type": "object", "description": "Zotero item 字段，至少 title；可带 DOI/creators/date/publicationTitle/abstractNote"},
         "collection": {"type": "string", "description": "放进哪个分类 key（可空）"}}, "required": ["item"]}},
    {"name": "zotero_create_note", "run": tool_create_note,
     "description": "给已有条目挂一条笔记（Web API，需要 key）。",
     "inputSchema": {"type": "object", "properties": {
         "parent_key": {"type": "string"},
         "note": {"type": "string", "description": "笔记正文，HTML 或纯文本"},
         "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["parent_key", "note"]}},
]


def _tools_list() -> dict:
    return {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]}


def _tools_call(params: dict) -> dict:
    name = params.get("name") or ""
    args = params.get("arguments") or {}
    for t in TOOLS:
        if t["name"] == name:
            try:
                out = t["run"](args)
                return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
            except Exception as exc:                     # 工具失败回给 agent，别把服务打挂
                traceback.print_exc(file=sys.stderr)
                return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": f"没有这个工具：{name}"}], "isError": True}


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:                                      # 通知（initialized 等）不用回
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "zotero", "version": "1.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": _tools_list()}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": mid, "result": _tools_call(msg.get("params") or {})}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method: {method}"}}


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "invalid JSON"}}), flush=True)
            continue
        try:
            out = handle(msg if isinstance(msg, dict) else {})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            out = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                   "error": {"code": -32603, "message": str(exc)}}
        if out is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
