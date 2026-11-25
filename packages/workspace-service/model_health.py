"""Model health state, circuit-breaker cooldowns, and global fallback routing policy."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

DEFAULT_COOLDOWN_SECONDS = 120
QUOTA_COOLDOWN_SECONDS = 600
TRANSIENT_TRIP_THRESHOLD = 2


def _conn(db: Any) -> Any:
    return db.db if hasattr(db, "db") else db


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().isoformat()


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def ensure_schema(db: Any) -> None:
    if getattr(db, "_model_health_initialized", False):
        return
    conn = _conn(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_health_state (
          profile_id TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'unknown',
          consecutive_failures INTEGER NOT NULL DEFAULT 0,
          cooldown_until TEXT NOT NULL DEFAULT '',
          last_error TEXT NOT NULL DEFAULT '',
          last_error_code TEXT NOT NULL DEFAULT '',
          capabilities_json TEXT NOT NULL DEFAULT '{}',
          last_checked_at TEXT NOT NULL DEFAULT '',
          last_ok_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    db._model_health_initialized = True


def _decode_row(row: Any, now_dt: datetime | None = None) -> dict[str, Any]:
    data = dict(row)
    try:
        caps = json.loads(data.pop("capabilities_json") or "{}")
    except (TypeError, ValueError):
        caps = {}
    if not isinstance(caps, dict):
        caps = {}
    data["capabilities"] = caps
    now_dt = now_dt or _now_dt()
    until = _parse_iso(data.get("cooldown_until", ""))
    remaining = max(0, int((until - now_dt).total_seconds())) if until and until > now_dt else 0
    data["in_cooldown"] = remaining > 0
    data["cooldown_remaining_seconds"] = remaining
    return data


def get(db: Any, profile_id: str) -> dict[str, Any]:
    if not profile_id:
        return {"profile_id": "", "status": "unknown", "consecutive_failures": 0, "cooldown_until": "",
                "last_error": "", "last_error_code": "", "capabilities": {}, "last_checked_at": "",
                "last_ok_at": "", "updated_at": "", "in_cooldown": False, "cooldown_remaining_seconds": 0}
    ensure_schema(db)
    row = _conn(db).execute("SELECT * FROM model_health_state WHERE profile_id=?", (profile_id,)).fetchone()
    if not row:
        return {"profile_id": profile_id, "status": "unknown", "consecutive_failures": 0, "cooldown_until": "",
                "last_error": "", "last_error_code": "", "capabilities": {}, "last_checked_at": "",
                "last_ok_at": "", "updated_at": "", "in_cooldown": False, "cooldown_remaining_seconds": 0}
    return _decode_row(row)


def list_states(db: Any) -> dict[str, dict[str, Any]]:
    ensure_schema(db)
    now_dt = _now_dt()
    rows = _conn(db).execute("SELECT * FROM model_health_state").fetchall()
    return {row["profile_id"]: _decode_row(row, now_dt) for row in rows}


def routing_policy(db: Any) -> dict[str, Any]:
    import service_settings
    data = service_settings.read(db, "model_routing") or {}
    fallbacks = [str(x).strip() for x in (data.get("fallback_model_ids") or []) if str(x).strip()]
    auto_fallback = data.get("auto_fallback", True)
    try:
        cooldown = max(15, min(3600, int(data.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN_SECONDS
    return {
        "fallback_model_ids": list(dict.fromkeys(fallbacks)),
        "auto_fallback": bool(auto_fallback),
        "cooldown_seconds": cooldown,
    }


def save_routing_policy(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    import service_settings
    raw_ids = body.get("fallback_model_ids")
    if raw_ids is None:
        raw_ids = routing_policy(db)["fallback_model_ids"]
    if not isinstance(raw_ids, list) or len(raw_ids) > 20:
        raise ValueError("备用模型列表必须是最多 20 个模型 ID 的数组")
    valid_ids = []
    for item in raw_ids:
        mid = str(item or "").strip()
        if not mid:
            continue
        db.model(mid)
        if mid not in valid_ids:
            valid_ids.append(mid)
    current = routing_policy(db)
    auto_fallback = bool(body["auto_fallback"]) if "auto_fallback" in body else current["auto_fallback"]
    if "cooldown_seconds" in body:
        try:
            cooldown = int(body["cooldown_seconds"])
        except (TypeError, ValueError) as exc:
            raise ValueError("熔断冷却时间必须为整数秒") from exc
        if not 15 <= cooldown <= 3600:
            raise ValueError("熔断冷却时间须在 15 至 3600 秒之间")
    else:
        cooldown = current["cooldown_seconds"]
    return service_settings.write(db, "model_routing", {
        "fallback_model_ids": valid_ids,
        "auto_fallback": auto_fallback,
        "cooldown_seconds": cooldown,
    })


def record_success(db: Any, profile_id: str, *, capability: str = "") -> dict[str, Any] | None:
    if not db or not profile_id:
        return None
    ensure_schema(db)
    conn = _conn(db)
    stamp = _now_iso()
    current = get(db, profile_id)
    caps = dict(current.get("capabilities") or {})
    if capability:
        caps[capability] = True
    try:
        with conn:
            conn.execute("""
                INSERT INTO model_health_state(
                  profile_id, status, consecutive_failures, cooldown_until, last_error,
                  last_error_code, capabilities_json, last_checked_at, last_ok_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(profile_id) DO UPDATE SET
                  status='healthy',
                  consecutive_failures=0,
                  cooldown_until='',
                  last_error='',
                  last_error_code='',
                  capabilities_json=excluded.capabilities_json,
                  last_checked_at=CASE WHEN ?<>'' THEN excluded.last_checked_at ELSE model_health_state.last_checked_at END,
                  last_ok_at=excluded.last_ok_at,
                  updated_at=excluded.updated_at
            """, (
                profile_id, "healthy", 0, "", "", "", json.dumps(caps, ensure_ascii=False),
                stamp if capability else "", stamp, stamp, capability,
            ))
    except sqlite3.Error:
        return None
    return get(db, profile_id)


def record_failure(db: Any, profile_id: str, error: Any, *, error_kind: str = "",
                   error_code: str = "", provider_id: str = "", capability: str = "") -> dict[str, Any] | None:
    if not db or not profile_id:
        return None
    ensure_schema(db)
    conn = _conn(db)
    now_dt = _now_dt()
    stamp = now_dt.isoformat()
    message = str(error or "").strip()[:500]
    kind = str(error_kind or "").strip()
    code = str(error_code or "").strip()[:80]
    if kind == "context_overflow":
        return get(db, profile_id)

    policy = routing_policy(db)
    base_cooldown = policy["cooldown_seconds"]

    def _upsert(pid: str, status: str, failures: int, cooldown_until: str, caps: dict[str, Any]) -> None:
        conn.execute("""
            INSERT INTO model_health_state(
              profile_id, status, consecutive_failures, cooldown_until, last_error,
              last_error_code, capabilities_json, last_checked_at, last_ok_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              status=excluded.status,
              consecutive_failures=excluded.consecutive_failures,
              cooldown_until=excluded.cooldown_until,
              last_error=excluded.last_error,
              last_error_code=excluded.last_error_code,
              capabilities_json=excluded.capabilities_json,
              last_checked_at=CASE WHEN ?<>'' THEN excluded.last_checked_at ELSE model_health_state.last_checked_at END,
              updated_at=excluded.updated_at
        """, (
            pid, status, failures, cooldown_until, message, code,
            json.dumps(caps, ensure_ascii=False),
            stamp if capability else "", "", stamp, capability,
        ))

    current = get(db, profile_id)
    failures = int(current.get("consecutive_failures") or 0) + 1
    caps = dict(current.get("capabilities") or {})
    if capability:
        caps[capability] = False

    if kind == "quota_or_auth":
        status = "quota_exceeded" if "quota" in code or "额度" in message else "auth_error"
        cooldown_until = (now_dt + timedelta(seconds=max(base_cooldown, QUOTA_COOLDOWN_SECONDS))).isoformat()
    elif kind == "model_unavailable":
        status = "unavailable"
        cooldown_until = (now_dt + timedelta(seconds=max(base_cooldown, 300))).isoformat()
    elif kind == "transient":
        if failures >= TRANSIENT_TRIP_THRESHOLD:
            status = "cooldown"
            cooldown_until = (now_dt + timedelta(seconds=base_cooldown)).isoformat()
        else:
            status = "degraded"
            cooldown_until = ""
    else:
        status = "degraded"
        cooldown_until = (now_dt + timedelta(seconds=min(base_cooldown, 60))).isoformat() if failures >= TRANSIENT_TRIP_THRESHOLD else ""

    try:
        with conn:
            _upsert(profile_id, status, failures, cooldown_until, caps)
            if kind == "quota_or_auth" and provider_id:
                siblings = conn.execute(
                    "SELECT id FROM model_profiles WHERE provider_id=? AND retired=0 AND id<>?",
                    (provider_id, profile_id),
                ).fetchall()
                for row in siblings:
                    sib = get(db, row["id"])
                    sib_failures = int(sib.get("consecutive_failures") or 0) + 1
                    _upsert(row["id"], status, sib_failures, cooldown_until, dict(sib.get("capabilities") or {}))
    except sqlite3.Error:
        return None
    return get(db, profile_id)


def clear(db: Any, profile_id: str = "", *, provider_id: str = "") -> None:
    if not db:
        return
    ensure_schema(db)
    conn = _conn(db)
    with conn:
        if profile_id:
            conn.execute("DELETE FROM model_health_state WHERE profile_id=?", (profile_id,))
        elif provider_id:
            conn.execute(
                "DELETE FROM model_health_state WHERE profile_id IN (SELECT id FROM model_profiles WHERE provider_id=?)",
                (provider_id,),
            )
        else:
            conn.execute("DELETE FROM model_health_state")


def batch_check(db: Any, profile_ids: list[str] | None = None, *, capability: str = "text") -> list[dict[str, Any]]:
    import model_checks
    models = db.models()
    if profile_ids:
        wanted = set(profile_ids)
        targets = [m for m in models if m["id"] in wanted]
    else:
        targets = models
    results = []
    for profile in targets:
        try:
            res = model_checks.generate(profile, capability=capability, source="model_batch_check", db=db)
            results.append({"id": profile["id"], "name": profile.get("name") or profile["model"],
                            "ok": True, "health": get(db, profile["id"]), "result": res})
        except Exception as exc:
            results.append({"id": profile["id"], "name": profile.get("name") or profile["model"],
                            "ok": False, "error": str(exc), "health": get(db, profile["id"])})
    return results
