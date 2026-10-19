"""Persistent workflow run and node primitives used by Agent and team workers."""
from __future__ import annotations

import json
import uuid
from typing import Any


def _conn(db):
    return db.db if hasattr(db, "db") else db


def _now():
    import workflow
    return workflow.now()


def start(db, project_id: str, workflow: str, *, idempotency_key: str = "") -> dict[str, Any]:
    conn = _conn(db)
    if idempotency_key:
        old = conn.execute("SELECT * FROM workflow_runs WHERE project_id=? AND idempotency_key=?",
                           (project_id, idempotency_key)).fetchone()
        if old and old["status"] in ("queued", "running", "succeeded"):
            return dict(old)
    rid = "wrun-" + uuid.uuid4().hex
    stamp = _now()
    conn.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?)",
                 (rid, project_id or "", workflow, "running", "", idempotency_key or "", stamp, "", ""))
    conn.commit()
    return dict(conn.execute("SELECT * FROM workflow_runs WHERE id=?", (rid,)).fetchone())


def node_start(db, run_id: str, stage: str, *, inputs: list[str] | None = None,
               acceptance: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = _conn(db)
    nid = "wnode-" + uuid.uuid4().hex
    stamp = _now()
    conn.execute("""INSERT INTO workflow_nodes
        (id,run_id,stage,status,input_assets,acceptance,attempt,started_at)
        VALUES(?,?,?,?,?,?,?,?)""",
                 (nid, run_id, stage, "running", json.dumps(inputs or []),
                  json.dumps(acceptance or {}, ensure_ascii=False), 1, stamp))
    conn.execute("UPDATE workflow_runs SET current_node=? WHERE id=?", (nid, run_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM workflow_nodes WHERE id=?", (nid,)).fetchone())


def finish_node(db, node_id: str, status: str, *, outputs: list[str] | None = None,
                checkpoint: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    conn = _conn(db)
    node = conn.execute("SELECT * FROM workflow_nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        raise KeyError(node_id)
    ended = _now()
    conn.execute("""UPDATE workflow_nodes SET status=?, output_assets=?, checkpoint=?, ended_at=?, error=?
        WHERE id=?""", (status, json.dumps(outputs or []), json.dumps(checkpoint or {}, ensure_ascii=False),
                          ended, error[:2000], node_id))
    if status in ("failed", "cancelled"):
        conn.execute("UPDATE workflow_runs SET status=?, ended_at=?, error=? WHERE id=?",
                     (status, ended, error[:2000], node["run_id"]))
    elif status == "succeeded":
        pending = conn.execute("SELECT COUNT(*) AS n FROM workflow_nodes WHERE run_id=? AND status IN ('queued','running')",
                               (node["run_id"],)).fetchone()["n"]
        if not pending:
            conn.execute("UPDATE workflow_runs SET status='succeeded', ended_at=? WHERE id=?", (ended, node["run_id"]))
    conn.commit()
    return dict(conn.execute("SELECT * FROM workflow_nodes WHERE id=?", (node_id,)).fetchone())


def finish(db, run_id: str, status: str, *, error: str = "") -> dict[str, Any]:
    conn = _conn(db)
    conn.execute("UPDATE workflow_runs SET status=?, ended_at=?, error=? WHERE id=?",
                 (status, _now(), error[:2000], run_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone())


def get(db, run_id: str) -> dict[str, Any] | None:
    row = _conn(db).execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def nodes(db, run_id: str) -> list[dict[str, Any]]:
    rows = _conn(db).execute(
        "SELECT * FROM workflow_nodes WHERE run_id=? ORDER BY started_at, id", (run_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def detail(db, run_id: str) -> dict[str, Any] | None:
    run = get(db, run_id)
    if not run:
        return None
    run["nodes"] = nodes(db, run_id)
    return run


def list_runs(db, project_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    conn = _conn(db)
    if project_id:
        rows = conn.execute("SELECT * FROM workflow_runs WHERE project_id=? ORDER BY started_at DESC LIMIT ?",
                            (project_id, max(1, min(int(limit), 500)))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
                            (max(1, min(int(limit), 500)),)).fetchall()
    return [dict(row) for row in rows]


def recover_stale(db, max_age_minutes: int = 15) -> int:
    """Move abandoned running runs back to queued so the caller can retry them."""
    conn = _conn(db)
    stale = [row["id"] for row in conn.execute(
        "SELECT id FROM workflow_runs WHERE status='running' "
        "AND datetime(replace(started_at, 'T', ' ')) < datetime('now', ?)",
        (f"-{max(1, int(max_age_minutes))} minutes",)).fetchall()]
    if stale:
        marks = ",".join("?" for _ in stale)
        conn.execute(f"UPDATE workflow_nodes SET status='queued', ended_at='', error='requeued after restart' "
                     f"WHERE run_id IN ({marks}) AND status='running'", stale)
    cur = conn.execute("""UPDATE workflow_runs SET status='queued', error='服务重启后恢复'
        WHERE status='running' AND datetime(replace(started_at, 'T', ' ')) < datetime('now', ?)""",
                       (f"-{max(1, int(max_age_minutes))} minutes",))
    conn.commit()
    return int(cur.rowcount or 0)
