"""HTTP routes for RSS source management and refresh."""
from __future__ import annotations

from typing import Any

import rss


def route(method: str, path: str, body: dict[str, Any],
          query: dict[str, list[str]], db: Any) -> Any | None:
    if method == "GET" and path == "/api/rss/sources":
        return {"sources": db.rss_sources(), "defaults": rss.DEFAULT_SOURCES}
    if method != "POST":
        return None
    if path == "/api/rss/refresh":
        return rss.refresh(db)
    if path == "/api/rss/recover-arxiv":
        item_id = body.get("item_id")
        if set(body) != {"item_id"} or not isinstance(item_id, str) or not item_id or len(item_id) > 64:
            raise ValueError("请提供有效的情报条目 ID")
        return {"item": db.recover_arxiv_metadata(item_id)}
    if path == "/api/rss/sources/save":
        return rss.save_source(db, body)
    if path == "/api/rss/sources/delete":
        return rss.delete_source(db, body)
    if path == "/api/rss/sources/defaults":
        return rss.add_defaults(db)
    return None
