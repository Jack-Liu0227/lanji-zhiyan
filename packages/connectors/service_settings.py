"""Database-scoped connector settings; secrets never enter general state responses."""
from __future__ import annotations

import json
import time
from typing import Any


def ensure(db: Any) -> None:
    db.db.execute("""CREATE TABLE IF NOT EXISTS service_settings (
        service TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '', data TEXT NOT NULL,
        PRIMARY KEY(service, owner))""")
    db.db.commit()


def read(db: Any, service: str, owner: str = "") -> dict[str, Any]:
    ensure(db)
    row = db.db.execute("SELECT data FROM service_settings WHERE service=? AND owner=?",
                        (service, owner)).fetchone()
    return json.loads(row["data"]) if row else {}


def write(db: Any, service: str, data: dict[str, Any], owner: str = "") -> dict[str, Any]:
    ensure(db)
    db.db.execute("INSERT OR REPLACE INTO service_settings VALUES (?,?,?)",
                  (service, owner, json.dumps(data, ensure_ascii=False)))
    db.db.commit()
    return data


def public(data: dict[str, Any]) -> dict[str, Any]:
    secret = {"api_key", "app_secret", "access_token", "refresh_token", "webhook", "signing_secret"}
    return {**{k: v for k, v in data.items() if k not in secret},
            **{k + "_present": bool(data.get(k)) for k in secret}}


def merge_secrets(base: dict[str, Any], body: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    out = dict(base)
    secret = {"api_key", "app_secret", "webhook", "signing_secret"}
    for key in allowed:
        if key not in body:
            continue
        if key in secret and not body[key]:
            if body.get("clear_" + key) is True:
                out[key] = ""
            continue
        out[key] = body[key]
    out["updated_at"] = int(time.time())
    return out
