"""HTTP routes for RSS item listing and literature import (GET + POST).

只做搬运：GET 侧原来内联在 handlers/misc.py，POST 侧在 literature_import.route，
两边合成一个 module 是因为它们同一个前缀、同一份 annotate 逻辑。
"""
from __future__ import annotations

from typing import Any

import literature_import


def route(method: str, path: str, body: dict[str, Any],
          query: dict[str, list[str]], db: Any) -> Any | None:
    if method == "GET" and path == "/api/rss/items":
        sid = (query.get("source") or [""])[0]
        kw = (query.get("q") or [""])[0].strip()
        yr = (query.get("year") or [""])[0].strip()
        pid = (query.get("project_id") or [""])[0]
        return {"items": literature_import.annotate(db, db.rss_items(sid, kw, yr), pid),
                "total": db.rss_count(sid, kw, yr)}
    if method == "GET" and path == "/api/library/project":
        return {"items": db.results((query.get("task_id") or [""])[0])}
    return literature_import.route(method, path, body, query, db)
