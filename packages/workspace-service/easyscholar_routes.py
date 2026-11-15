"""HTTP endpoints for the easyScholar integration module."""
from __future__ import annotations

from typing import Any

import easyscholar


def route(method: str, path: str, body: dict[str, Any],
          query: dict[str, list[str]], db: Any) -> Any | None:
    conn = db.db if hasattr(db, "db") else db
    if path == "/api/easyscholar/config" and method == "GET":
        config = easyscholar.get_config(conn)
        config.pop("secret_key", None)
        return config
    if path == "/api/easyscholar/config" and method == "POST":
        return easyscholar.save_config(
            conn,
            enabled=bool(body.get("enabled", True)),
            secret_key=str(body.get("secret_key", "")),
            options=body.get("options") if isinstance(body.get("options"), dict) else None,
        )
    if path == "/api/easyscholar/test" and method == "POST":
        return easyscholar.test_connection(str(body.get("secret_key", "")))
    if path == "/api/easyscholar/rank" and method == "GET":
        journal = (query.get("journal") or query.get("name") or [""])[0]
        refresh = (query.get("refresh") or ["0"])[0] in ("1", "true", "True")
        return easyscholar.query_publication_rank(conn, journal, force_refresh=refresh)
    if path == "/api/easyscholar/ranks" and method == "POST":
        venues = body.get("venues")
        if not isinstance(venues, list) or any(not isinstance(name, str) for name in venues):
            raise ValueError("venues 必须是字符串数组")
        names = list(dict.fromkeys(name.strip() for name in venues if name.strip()))
        if len(names) > 30:
            raise ValueError("单次最多查询 30 个期刊")
        ranks = {}
        for name in names:
            result = easyscholar.query_publication_rank(conn, name)
            if not result.get("ok"):
                continue
            official = result.get("officialRank") or {}
            fields = official.get("select") or official.get("all") or {}
            badges = result.get("badges") or []
            top = str(fields.get("sciUpTop") or "").strip().lower() not in ("", "0", "false", "no", "none")
            ranks[name] = {
                "tag": (badges[0].get("label") if badges else ""),
                "sci": str(fields.get("sciUp") or ""),
                "top": top,
                "jcr": str(fields.get("sci") or ""),
                "tip": " · ".join(str(item.get("label") or "") for item in badges if item.get("label")),
            }
        return {"ranks": ranks}
    return None
