"""Zotero MCP 服务（stdio）：给 agent 用的 Zotero 读 + 写工具。

默认读写本机 Zotero Local API；写入复用本机授权，无需云端 API Key。
显式设置 ZOTERO_API_KEY 时兼容 Web API。
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
    """复用应用的连接配置和加密本机授权，每次调用结束关闭连接。"""

    def __init__(self) -> None:
        import paths
        self.db = sqlite3.connect(str(paths.DB_PATH))
        self.db.row_factory = sqlite3.Row


def _write_config(db) -> dict[str, Any]:
    key = os.environ.get("ZOTERO_API_KEY", "")
    if key:
        kind = os.environ.get("ZOTERO_LIBRARY_TYPE", "users")
        return {"api_key": key, "library_type": {'user': 'users', 'group': 'groups'}.get(kind, kind),
                "library_id": os.environ.get("ZOTERO_LIBRARY_ID", "")}
    return zotero_sync.config(db)


def _request(path, method='GET', payload=None):
    db = _DB()
    try:
        return zotero_sync.request(_write_config(db), path, method, payload)
    finally:
        db.db.close()


# ---------------------------------------------------------------- 工具

def tool_status(_: dict) -> dict:
    out = {"local": zotero.status()}
    db = _DB()
    try:
        cfg = _write_config(db)
        local = cfg.get('transport') == 'local'
        out['transport'] = 'local' if local else 'web'
        out['authorization'] = zotero.local_write_state(db) if local else {}
        if local:
            try:
                zotero._server_id(zotero._base())
                out['writable'], out['write_hint'] = True, '本机写入；首次使用时由 Zotero 授权，之后复用。'
            except zotero.ZoteroError as exc:
                out['writable'], out['write_hint'] = False, str(exc)
        else:
            out['writable'], out['write_hint'] = bool(cfg.get('api_key')), '使用显式配置的 Web API'
    finally:
        db.db.close()
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
    item = dict(a.get("item") or {})
    if not item.get("title"):
        raise ValueError("写入条目至少要给 title")
    item.setdefault("itemType", "journalArticle")
    item.pop("key", None)
    item.pop("version", None)
    if a.get("collection"):
        item["collections"] = [str(a["collection"])]
    return {"ok": True, "item": zotero_sync._saved(_request("/items", "POST", [item]))}


def tool_create_note(a: dict) -> dict:
    if not (a.get("parent_key") or "").strip():
        raise ValueError("写笔记要指定 parent_key（条目 key）")
    note = {"itemType": "note", "parentItem": a["parent_key"], "note": a.get("note") or "",
            "tags": [{"tag": str(t)} for t in (a.get("tags") or [])]}
    return {"ok": True, "item": zotero_sync._saved(_request("/items", "POST", [note]))}


def tool_update_item(a: dict) -> dict:
    key = str(a.get('key') or '')
    if not zotero_sync.KEY.fullmatch(key):
        raise ValueError('无效的 Zotero 条目 Key')
    current = _request('/items/' + key)
    if int(a.get('version', -1)) != current['version']:
        raise ValueError('条目版本已变更，请重新读取后确认修改')
    data = {**current['data'], **(a.get('fields') or {}), 'key': key, 'version': current['version']}
    return {'ok': True, 'item': zotero_sync._saved(_request('/items', 'POST', [data]))}


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
     "description": "往 Zotero 写入一条文献（默认本机写入，自动复用本机授权）。",
     "inputSchema": {"type": "object", "properties": {
         "item": {"type": "object", "description": "Zotero item 字段，至少 title；可带 DOI/creators/date/publicationTitle/abstractNote"},
         "collection": {"type": "string", "description": "放进哪个分类 key（可空）"}}, "required": ["item"]}},
    {"name": "zotero_create_note", "run": tool_create_note,
     "description": "给已有条目挂一条笔记（默认本机写入，自动复用本机授权）。",
     "inputSchema": {"type": "object", "properties": {
         "parent_key": {"type": "string"},
         "note": {"type": "string", "description": "笔记正文，HTML 或纯文本"},
         "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["parent_key", "note"]}},
    {"name": "zotero_update_item", "run": tool_update_item,
     "description": "修改 Zotero 条目的指定字段（含 tags/collections），保留其余字段；必须提供读取时的版本。",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "version": {"type": "integer"},
         "fields": {"type": "object"}}, "required": ["key", "version", "fields"]}},
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
