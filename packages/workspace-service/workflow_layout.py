"""Project-bound, validated workflow definitions and evidence-backed node state."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from workflow import STAGES, STAGE_LABEL, now


SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_layouts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_workflow_layouts (
  task_id TEXT PRIMARY KEY, layout_id TEXT NOT NULL, selected_node TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS workflow_layouts_project_deleted
AFTER DELETE ON research_tasks BEGIN
  DELETE FROM project_workflow_layouts WHERE task_id=OLD.id;
END;
"""
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_DONE = {"done", "completed", "success", "succeeded", "downloaded"}
_RUNNING = {"running", "in_progress", "downloading", "reading", "translating"}
_FAILED = {"failed", "error", "manual"}


def _linear(stages: list[str]) -> dict[str, Any]:
    return {"nodes": [{"id": s, "step": s, "label": STAGE_LABEL[s]} for s in stages],
            "edges": [{"source": a, "target": b} for a, b in zip(stages, stages[1:])],
            "order": stages}


def ensure_schema(db: Any) -> None:
    db.db.executescript(SCHEMA)
    seeds = [
        ("builtin.workflow.research", "完整科研工作流", "保留现有全部研究步骤", STAGES),
        ("builtin.workflow.reading", "文献精读", "检索、下载、翻译与整理", ["search", "download", "notes", "map"]),
        ("builtin.workflow.writing", "论文写作", "大纲、写作、审查与导出", ["outline", "map", "write", "review", "entrans", "convert"]),
    ]
    # Seed only once, including after the user deliberately deletes a template.
    if not db.db.execute("SELECT 1 FROM workflow_layouts LIMIT 1").fetchone():
        for wid, name, description, stages in seeds:
            stamp = now()
            db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)",
                          (wid, name, description, json.dumps(_linear(stages), ensure_ascii=False), stamp, stamp))
    db.db.commit()


def validate_definition(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a DAG of real UI steps; a step appears once to avoid false state."""
    if not isinstance(data, dict):
        raise ValueError("工作流定义必须是 JSON 对象")
    nodes, edges = data.get("nodes"), data.get("edges", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= len(STAGES):
        raise ValueError(f"工作流需包含 1 至 {len(STAGES)} 个节点")
    if not isinstance(edges, list):
        raise ValueError("edges 必须是数组")
    clean, ids, steps = [], set(), set()
    for raw in nodes:
        if not isinstance(raw, dict):
            raise ValueError("每个节点必须是 JSON 对象")
        nid, step = str(raw.get("id", "")), str(raw.get("step", ""))
        if not _ID.fullmatch(nid) or nid in ids:
            raise ValueError("节点 id 须唯一且仅包含字母、数字、下划线、点和横线")
        if step not in STAGES:
            raise ValueError("不支持的工作流步骤：" + step)
        if step in steps:
            raise ValueError("同一步骤不能重复，避免节点状态含义不清：" + step)
        label = str(raw.get("label") or STAGE_LABEL[step]).strip()
        if not label or len(label) > 120:
            raise ValueError("节点名称须为 1 至 120 字符")
        clean.append({"id": nid, "step": step, "label": label})
        ids.add(nid)
        steps.add(step)
    links, seen = [], set()
    parents = {n["id"]: [] for n in clean}
    children = {n["id"]: [] for n in clean}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("每条连线必须是 JSON 对象")
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if source not in ids or target not in ids or source == target:
            raise ValueError("连线必须连接两个存在且不同的节点")
        if (source, target) in seen:
            raise ValueError("工作流中存在重复连线")
        seen.add((source, target))
        parents[target].append(source)
        children[source].append(target)
        links.append({"source": source, "target": target})
    waiting = {key: len(value) for key, value in parents.items()}
    queue = [n["id"] for n in clean if not waiting[n["id"]]]
    ordered = []
    while queue:
        nid = queue.pop(0)
        ordered.append(nid)
        for child in children[nid]:
            waiting[child] -= 1
            if not waiting[child]:
                queue.append(child)
    if len(ordered) != len(clean):
        raise ValueError("工作流不能包含循环依赖（必须是 DAG）")
    order = data.get("order", ordered)
    if not isinstance(order, list) or any(not isinstance(n, str) for n in order):
        raise ValueError("order 必须是节点 id 数组")
    if len(order) != len(ids) or set(order) != ids:
        raise ValueError("order 必须且只能包含每个节点一次")
    ranks = {nid: index for index, nid in enumerate(order)}
    if any(ranks[e["source"]] >= ranks[e["target"]] for e in links):
        raise ValueError("节点顺序必须与连线依赖一致")
    return {"nodes": clean, "edges": links, "order": order}


def _public(row: Any) -> dict[str, Any]:
    data = dict(row)
    data.update(json.loads(data.pop("definition")))
    return data


def _layout(db: Any, wid: str) -> dict[str, Any]:
    row = db.db.execute("SELECT * FROM workflow_layouts WHERE id=?", (wid,)).fetchone()
    if not row:
        raise KeyError("工作流不存在：" + wid)
    return _public(row)


def _project(db: Any, task_id: str) -> None:
    if not task_id or not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone():
        raise ValueError("请先选择有效项目")


def save_layout(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    name, description = str(body.get("name", "")).strip(), str(body.get("description", "")).strip()
    if not name or len(name) > 120:
        raise ValueError("工作流名称须为 1 至 120 字符")
    if len(description) > 4000:
        raise ValueError("工作流说明不能超过 4000 字符")
    definition = validate_definition(body)
    wid, stamp = str(body.get("id") or ""), now()
    if wid:
        _layout(db, wid)
        db.db.execute("UPDATE workflow_layouts SET name=?,description=?,definition=?,updated_at=? WHERE id=?",
                      (name, description, json.dumps(definition, ensure_ascii=False), stamp, wid))
        valid = {n["id"] for n in definition["nodes"]}
        for binding in db.db.execute("SELECT task_id,selected_node FROM project_workflow_layouts WHERE layout_id=?", (wid,)).fetchall():
            if binding["selected_node"] not in valid:
                db.db.execute("UPDATE project_workflow_layouts SET selected_node=?,updated_at=? WHERE task_id=?",
                              (definition["order"][0], stamp, binding["task_id"]))
    else:
        wid = uuid.uuid4().hex
        db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)",
                      (wid, name, description, json.dumps(definition, ensure_ascii=False), stamp, stamp))
    db.db.commit()
    return {"ok": True, "layout": _layout(db, wid)}


def select_layout(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    tid, wid = str(body.get("task_id", "")), str(body.get("layout_id", ""))
    _project(db, tid)
    layout = _layout(db, wid)
    selected = str(body.get("node_id") or layout["order"][0])
    if selected not in {n["id"] for n in layout["nodes"]}:
        raise ValueError("所选节点不属于当前工作流")
    db.db.execute("INSERT INTO project_workflow_layouts VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                  "layout_id=excluded.layout_id,selected_node=excluded.selected_node,updated_at=excluded.updated_at",
                  (tid, wid, selected, now()))
    db.db.commit()
    return state(db, tid)


def _status(raw: str) -> str:
    if raw in _DONE:
        return "done"
    if raw in _RUNNING:
        return "running"
    if raw in _FAILED:
        return "failed"
    if raw in {"cancelled", "canceled", "stopped"}:
        return "cancelled"
    if raw == "blocked":
        return "blocked"
    return "pending"


def _evidence(db: Any, tid: str) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    moments: dict[str, float] = {}

    def moment(stamp: str) -> float:
        try:
            # Team records historically use local wall time without an offset;
            # datetime.timestamp handles that, unlike lexical ISO comparison.
            return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
        except (OSError, OverflowError, TypeError, ValueError):
            return float("-inf")

    def offer(stage: str, raw: str, summary: str, stamp: str, source: str, ref: str) -> None:
        if stage not in STAGES:
            return
        value = {"status": _status(raw), "raw_status": raw, "summary": summary,
                 "updated_at": stamp, "source": source, "record_id": ref}
        point = moment(stamp)
        if stage not in evidence or point >= moments[stage]:
            evidence[stage] = value
            moments[stage] = point

    for row in db.db.execute("SELECT * FROM steps WHERE task_id=? ORDER BY created_at,rowid", (tid,)):
        offer(row["stage"], row["status"], row["summary"], row["created_at"], "steps", row["id"])
    # Existing streaming stages historically recorded only chat, not a stage row.
    for row in db.db.execute("SELECT * FROM chat WHERE task_id=? AND role='assistant' ORDER BY created_at,rowid", (tid,)):
        stage = row["scope"] or "idea"
        try:
            events = json.loads(row["events"] or "[]")
        except (TypeError, ValueError):
            events = []
        failed = any(isinstance(ev, dict) and ev.get("type") == "error" for ev in events)
        offer(stage, "failed" if failed else "done", "最近一次步骤对话" + ("失败" if failed else "已完成"),
              row["created_at"], "chat", row["id"])
    for row in db.db.execute("SELECT t.*,m.step FROM team_tasks t JOIN team_members m ON m.id=t.member_id "
                             "WHERE t.task_id=? ORDER BY t.created_at,t.rowid", (tid,)):
        offer(row["step"], row["status"], row["reason"] or row["title"],
              row["ended_at"] or row["started_at"] or row["created_at"], "team_tasks", row["id"])
    queue = list(db.db.execute("SELECT * FROM dl_queue WHERE task_id=?", (tid,)))
    if queue:
        raw = ("running" if any(r["status"] in _RUNNING for r in queue) else
               "failed" if any(r["status"] in _FAILED for r in queue) else
               "queued" if any(r["status"] in {"queued", "pending"} for r in queue) else "done")
        latest = max(queue, key=lambda row: moment(row["updated_at"]))
        offer("download", raw, f"下载队列：{len(queue)} 项", latest["updated_at"], "dl_queue", latest["result_id"])
    return evidence


def state(db: Any, task_id: str) -> dict[str, Any]:
    _project(db, task_id)
    layouts = [_public(row) for row in db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id")]
    binding = db.db.execute("SELECT * FROM project_workflow_layouts WHERE task_id=?", (task_id,)).fetchone()
    layout = next((item for item in layouts if binding and item["id"] == binding["layout_id"]), layouts[0])
    if not binding or binding["layout_id"] != layout["id"]:
        db.db.execute("INSERT OR REPLACE INTO project_workflow_layouts VALUES (?,?,?,?)",
                      (task_id, layout["id"], layout["order"][0], now()))
        db.db.commit()
        selected = layout["order"][0]
    else:
        selected = binding["selected_node"] or layout["order"][0]
    evidence = _evidence(db, task_id)
    nodes = {node["id"]: dict(node) for node in layout["nodes"]}
    for node in nodes.values():
        node.update(evidence.get(node["step"], {"status": "pending", "raw_status": "", "summary": "尚无执行记录",
                                              "updated_at": "", "source": "", "record_id": ""}))
    for nid in layout["order"]:
        node = nodes[nid]
        parents = [edge["source"] for edge in layout["edges"] if edge["target"] == nid]
        node["dependencies"] = parents
        node["level"] = max((nodes[parent]["level"] + 1 for parent in parents), default=0)
        if node["status"] == "pending":
            node["status"] = "ready" if all(nodes[parent]["status"] == "done" for parent in parents) else "waiting"
    return {"task_id": task_id, "layouts": layouts, "layout": layout, "selected_node": selected,
            "nodes": [nodes[nid] for nid in layout["order"]], "edges": layout["edges"],
            "observed_at": now(), "status_note": "节点状态来自真实步骤、对话、团队任务和下载队列；依赖展示不会自动执行步骤。"}


def route(method: str, path: str, body: dict[str, Any] | None, query: dict[str, Any], db: Any) -> dict[str, Any] | None:
    endpoints = {("GET", "/api/workflow/layouts"), ("GET", "/api/workflow/layouts/state"),
                 ("POST", "/api/workflow/layouts/save"), ("POST", "/api/workflow/layouts/delete"),
                 ("POST", "/api/workflow/layouts/select")}
    if (method, path) not in endpoints:
        return None
    ensure_schema(db)
    body = body or {}
    if path == "/api/workflow/layouts":
        return {"layouts": [_public(row) for row in db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id")],
                "stages": [{"id": step, "name": STAGE_LABEL[step]} for step in STAGES]}
    if path.endswith("/state"):
        raw = query.get("task_id", query.get("id", ""))
        return state(db, str(raw[0] if isinstance(raw, list) and raw else raw))
    if path.endswith("/save"):
        return save_layout(db, body)
    if path.endswith("/select"):
        return select_layout(db, body)
    wid = str(body.get("id", ""))
    _layout(db, wid)
    if db.db.execute("SELECT COUNT(*) FROM workflow_layouts").fetchone()[0] <= 1:
        raise ValueError("至少保留一个工作流")
    db.db.execute("DELETE FROM project_workflow_layouts WHERE layout_id=?", (wid,))
    db.db.execute("DELETE FROM workflow_layouts WHERE id=?", (wid,))
    db.db.commit()
    return {"ok": True}
