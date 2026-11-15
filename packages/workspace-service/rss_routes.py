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
    if path == "/api/rss/sources/save":
        return rss.save_source(db, body)
    if path == "/api/rss/sources/delete":
        return rss.delete_source(db, body)
    if path == "/api/rss/sources/defaults":
        return rss.add_defaults(db)
    return None
