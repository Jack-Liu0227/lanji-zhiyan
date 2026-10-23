"""Project workflow dependencies, immutable handoffs and explicit acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def definition(db, project_id: str) -> dict:
    import workflow_layout
    workflow_layout.ensure_schema(db)
    workflow_layout._project(db, project_id)
    binding = db.db.execute("SELECT layout_id FROM project_workflow_layouts WHERE task_id=?",
                            (project_id,)).fetchone()
    if binding:
        return workflow_layout._layout(db, binding["layout_id"])
    rows = list(db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id"))
    row = next((r for r in rows if r["id"] == workflow_layout.DEFAULT_LAYOUT_ID), rows[0])
    return workflow_layout._public(row)


def capture(db, project_id: str, step: str) -> dict:
    import workflow
    import asset_service
    result = workflow.step_delivery(db, project_id, step)
    payload: Any = None
    valid = False
    if step == "idea":
        payload = {"input": db.idea_input(project_id), "report": db.idea_verdict(project_id)}
        valid = bool(payload["input"] and payload["report"].get("markdown"))
    elif step == "search":
        payload = sorted(db.results(project_id), key=lambda r: r["id"])
        valid = bool(payload)
    elif step == "download":
        payload = sorted([{"id": p["id"], "title": p.get("title"), "sha256": p.get("sha256"),
                           "text_chars": p.get("text_chars")} for p in db.papers_of(project_id)],
                         key=lambda r: r["id"])
        valid = bool(payload)
    elif step == "notes":
        payload = sorted([{"id": n["id"], "title": n.get("title"), "markdown": n.get("markdown")}
                          for n in db.notes(project_id) if n.get("paper_id") and n.get("markdown", "").strip()],
                         key=lambda r: r["id"])
        valid = bool(payload)
        folder = db.pdir(project_id, "笔记") / workflow.NOTE_CATEGORY
        note_files = sorted(p for p in folder.rglob("*.md") if p.name.lower() != "readme.md" and p.stat().st_size)
        result["files"] = [str(p) for p in note_files]
        valid = valid or bool(note_files)
    elif step in ("outline", "write", "review"):
        doc = workflow.doc_read(db, project_id, step)
        payload = doc.get("markdown", "") if doc.get("exists") else ""
        valid = bool(payload.strip()) and (step != "write" or "（本节未生成 ——" not in payload)
    elif step == "map":
        data = workflow.map_read(db, project_id)
        payload = {"nodes": data.get("nodes", []), "edges": data.get("edges", [])}
        valid = bool(payload["nodes"])
    elif step == "entrans":
        data = workflow.en_state(db, project_id)
        payload = {"source": data.get("source_text", ""), "result": data.get("result_text", "")}
        valid = bool(payload["result"].strip())
    elif step == "convert":
        payload = result["files"]
        valid = bool(payload)
    files = []
    for raw in result["files"]:
        path = Path(raw)
        if not path.is_file() or not path.stat().st_size:
            valid = False
            continue
        files.append({"path": str(path.resolve()), "sha256": asset_service.sha256_file(path)})
    result.update({"valid": valid and "读取产物出错" not in result["items"],
                   "fingerprint": digest({"payload": payload, "files": files}),
                   "content": payload, "file_versions": files})
    return result


def _running_stages(db, project_id: str) -> set[str]:
    import project_todos
    project_todos.ensure_table(db)
    stages = set()
    for row in db.db.execute("SELECT checkpoint FROM tasks WHERE owner_id=? AND kind='agent_run' "
                             "AND status IN ('pending','running','suspended')", (project_id,)):
        try:
            stages.add(json.loads(row["checkpoint"] or "{}").get("step", ""))
        except (TypeError, ValueError):
            continue
    stages.update(r["step"] for r in db.db.execute(
        "SELECT m.step FROM team_tasks t JOIN team_members m ON m.id=t.member_id "
        "WHERE t.task_id=? AND t.status='running'", (project_id,)))
    stages.update(r["stage"] for r in db.db.execute(
        "SELECT t.stage FROM project_todos t JOIN task_links l ON l.todo_id=t.id "
        "JOIN schedule_runs r ON r.schedule_id=l.schedule_id "
        "WHERE t.task_id=? AND r.status='running'", (project_id,)))
    return stages


def states(db, project_id: str, layout: dict | None = None) -> dict[str, dict]:
    import workflow_layout
    layout = layout or definition(db, project_id)
    evidence = workflow_layout._evidence(db, project_id)
    running = _running_stages(db, project_id)
    latest = {}
    for row in db.db.execute("SELECT rowid AS sequence,* FROM steps WHERE task_id=? ORDER BY rowid", (project_id,)):
        latest[row["stage"]] = dict(row)
    nodes = {n["id"]: n for n in layout["nodes"]}
    result = {}
    for node_id in layout["order"]:
        step = nodes[node_id]["step"]
        parents = [nodes[e["source"]]["step"] for e in layout["edges"] if e["target"] == node_id]
        blocked = [p for p in parents if result[p]["status"] != "delivered"]
        entry = dict(evidence.get(step, {"status": "pending", "summary": "尚无执行记录"}))
        row = latest.get(step, {})
        delivery = {}
        try:
            delivery = json.loads(row.get("delivery") or "{}")
        except (ValueError, TypeError):
            pass
        if step in running:
            entry.update(status="running", summary="本环节仍有执行中的任务")
        elif entry["status"] in ("running", "failed", "cancelled"):
            pass
        elif row.get("status") in ("delivered", "done") and delivery:
            current = capture(db, project_id, step)
            upstream = {p: result[p].get("delivery_id", "") for p in parents}
            accepted = (delivery.get("fingerprint") == current["fingerprint"] and current["valid"]
                        and delivery.get("upstream", {}) == upstream and not blocked
                        and delivery.get("definition_hash") == digest(layout["edges"]))
            entry["status"] = "delivered" if accepted else "stale"
        elif entry["status"] in ("done", "succeeded", "review"):
            entry["status"] = "succeeded"
        elif entry["status"] == "pending":
            entry["status"] = "waiting" if blocked else "ready"
        entry.update({"dependencies": parents, "blocked_by": blocked, "delivery": delivery,
                      "delivery_id": row.get("id", "") if entry["status"] == "delivered" else ""})
        result[step] = entry
    return result


def gate(db, project_id: str, step: str) -> dict:
    import workflow
    state = states(db, project_id)
    if step not in state:
        return {"blocked": True, "independent": True, "prev": "", "prev_label": "",
                "blocked_by": [], "reason": "此功能未加入当前项目的工作流"}
    blocked = state[step]["blocked_by"]
    prev = blocked[0] if blocked else ""
    return {"blocked": bool(blocked), "prev": prev, "prev_label": workflow.STAGE_LABEL.get(prev, ""),
            "blocked_by": blocked, "dependencies": state[step]["dependencies"]}


def accept(db, project_id: str, step: str, summary: str = "") -> dict:
    layout = definition(db, project_id)
    state = states(db, project_id, layout)
    if step not in state:
        raise ValueError("此功能未加入当前项目的工作流")
    if state[step]["blocked_by"]:
        raise ValueError("上游尚未交付或已变更：" + "、".join(state[step]["blocked_by"]))
    delivery = capture(db, project_id, step)
    if not delivery["valid"]:
        raise ValueError("本步尚无完整产物，请完成后再确认交付")
    if state[step]["status"] in ("running", "failed", "cancelled"):
        raise ValueError("本步仍在运行、失败或已取消，请完成后再确认交付")
    if state[step]["status"] == "delivered":
        return next(r for r in db.steps(project_id) if r["id"] == state[step]["delivery_id"])
    delivery["upstream"] = {p: state[p]["delivery_id"] for p in state[step]["dependencies"]}
    delivery["definition_hash"] = digest(layout["edges"])
    delivery["assets"] = []
    import asset_service
    for item in delivery["file_versions"]:
        prior = db.db.execute("SELECT * FROM assets WHERE canonical_uri=?", (item["path"],)).fetchone()
        asset = dict(prior) if prior else {}
        if not asset.get("current_revision_id") or asset.get("content_hash") != item["sha256"]:
            path = Path(item["path"])
            body = path.read_text(encoding="utf-8") if path.suffix.lower() in (".md", ".txt", ".json") else ""
            asset = asset_service.register_file(db, path, project_id=project_id,
                kind=asset.get("kind", "document"), title=asset.get("title", ""),
                source=asset.get("source", "workflow_delivery"),
                metadata=json.loads(asset.get("metadata") or "{}"), body=body)
        delivery["assets"].append({"id": asset["id"], "revision_id": asset["current_revision_id"]})
    text = summary or "；".join(f"{k} {v}" for k, v in delivery["items"].items())
    row = db.log_step(project_id, step, "delivered", text[:200], delivery=delivery)
    import project_todos
    project_todos.sync_stage(db, project_id, step, "DONE")
    return row


def handoff(db, project_id: str, step: str) -> tuple[str, str, list[str]]:
    state = states(db, project_id)
    if step not in state:
        return "", "", []
    records, ids = [], []
    for parent in state[step]["dependencies"]:
        item = state[parent]
        if item["status"] != "delivered":
            continue
        delivery = item["delivery"]
        records.append({"step": parent, "delivery_id": item["delivery_id"], "delivery": delivery})
        ids.extend(a["id"] for a in delivery.get("assets", []))
    return json.dumps(records, ensure_ascii=False, default=str), digest(records), list(dict.fromkeys(ids))
