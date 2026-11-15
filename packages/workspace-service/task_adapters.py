"""Version guards and compatibility projections for canonical project tasks."""
from __future__ import annotations

import json
import uuid


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def validate(db, task, run):
    metadata = task.get("metadata") or {}
    import workflow_layout
    import workflow_state
    project_id = task["project_id"]
    frozen = metadata.get("layout_snapshot")
    owner = metadata.get("workflow_node") or {}
    if not owner and not metadata.get("legacy_stage_compat"):
        return
    if owner:
        import workflow_execution
        import file_handoff
        file_handoff.validate_run(db, workflow_execution.detail(db, owner["run_id"]))
    if frozen and 'revision' not in frozen and not frozen.get('project_override'):
        current = workflow_layout._layout(db, frozen["id"])
        normalized = workflow_layout.validate_definition(current)
        if current["id"] != frozen["id"] or any(normalized[k] != frozen[k] for k in ("nodes", "edges", "order")):
            raise ValueError("工作流定义已变化，请结束本次运行后重新启动")
    if not metadata.get("legacy_stage_compat"):
        return
    stage = metadata["stage"]
    incoming = (run.get("checkpoint") or {}).get("input") or metadata.get("upstream_context") or {}
    _, digest, _ = workflow_state.handoff(db, project_id, stage,frozen)
    if (incoming.get("upstream_hash") and digest != incoming["upstream_hash"]) or workflow_state.gate(db, project_id, stage,frozen)["blocked"]:
        raise ValueError("上游交付已变化，请重新执行当前节点")
    expected = (run.get("checkpoint") or {}).get("output") or {}
    if expected.get("fingerprint"):
        capture = workflow_state.capture(db, project_id, stage)
        if not capture["valid"] or capture["fingerprint"] != expected["fingerprint"]:
            raise ValueError("待审查产物已变化，请重新执行当前节点后审查")


def _stage_projection(db, task, run, checkpoint):
    import workflow
    import workflow_state
    stage = task["metadata"].get("stage")
    if not run or not task["metadata"].get("legacy_stage_compat") or not stage:
        return checkpoint
    status = {"running": "running", "awaiting_review": "succeeded", "delivered": "delivered",
              "failed": "failed", "cancelled": "cancelled"}.get(task["status"])
    if not status:
        return checkpoint
    latest = db.db.execute("SELECT * FROM steps WHERE task_id=? AND stage=? ORDER BY rowid DESC LIMIT 1",
                           (task["project_id"], stage)).fetchone()
    if status == "delivered":
        if checkpoint.get("legacy_imported") and checkpoint.get("delivery_source") == "legacy":
            return checkpoint
        delivery = dict(checkpoint.get("output") or {})
        if not delivery.get("valid"):
            raise ValueError("工作流交付缺少已校验的阶段产物")
        source = checkpoint.get("input") or {}
        delivery["upstream"] = {h["step"]: h["delivery_id"] for h in source.get("handoff", [])}
        frozen = task["metadata"].get("layout_snapshot") or {}
        delivery["definition_hash"] = workflow_state.digest(frozen.get("edges", []))
        delivery["assets"] = []
        for file in delivery.get("file_versions", []):
            asset = db.db.execute("SELECT id,current_revision_id FROM assets WHERE canonical_uri=?", (file["path"],)).fetchone()
            if asset:
                delivery["assets"].append({"id": asset["id"], "revision_id": asset["current_revision_id"]})
        key = task["accepted_delivery_id"]
        checkpoint.update(delivery_id=key, reviewed_at=workflow.now())
        if not db.db.execute("SELECT 1 FROM steps WHERE id=?", (key,)).fetchone():
            db.db.execute("INSERT INTO steps(id,task_id,stage,status,summary,created_at,delivery) VALUES (?,?,?,?,?,?,?)",
                          (key, task["project_id"], stage, status, checkpoint.get("reply", "")[:200], workflow.now(), _json(delivery)))
    elif not latest or latest["status"] != status:
        db.db.execute("INSERT INTO steps(id,task_id,stage,status,summary,created_at,delivery) VALUES (?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex, task["project_id"], stage, status, (run.get("error") or checkpoint.get("reply") or "项目任务执行")[:200], workflow.now(), ""))
    mapped = {"running": "IN_PROGRESS", "succeeded": "REVIEW", "delivered": "DONE"}.get(status, "TODO")
    db.db.execute("UPDATE project_todos SET status=?,updated_at=? WHERE task_id=? AND stage=?",
                  (mapped, workflow.now(), task["project_id"], stage))
    return checkpoint


def project(task, db):
    """Write derived old response fields without another delivery decision."""
    import workflow
    run = task.get("latest_run") or {}
    checkpoint = dict(run.get("checkpoint") or {})
    if "template_snapshot" in task["metadata"]:
        checkpoint.setdefault("queued_template_snapshot", task["metadata"]["template_snapshot"])
    if any(k in checkpoint for k in ("messages", "phase", "done_tools")):
        checkpoint["agent"] = {k: v for k, v in checkpoint.items() if k not in ("agent", "input", "reply", "output")}
    decisions = task.get("deliveries") or []
    decision = decisions[-1] if decisions else None
    if decision:
        checkpoint.update(feedback=decision.get("feedback", ""), delivery_source=decision["source"], delivery=decision)
    checkpoint["canonical_task_id"] = task["id"]
    checkpoint["canonical_run_id"] = run.get("id", "")
    team_origin = next((o for o in task.get("origins", []) if o["kind"] == "team_task" and (not o.get("task_run_id") or o["task_run_id"] == run.get("id"))), None)
    if team_origin:
        checkpoint["team_task_id"] = team_origin["legacy_id"]
    stamp = workflow.now()
    with db.db:
        checkpoint = _stage_projection(db, task, run, checkpoint)
        for origin in task.get("origins", []):
            kind, key = origin["kind"], origin["legacy_id"]
            # Historical attempts keep their own output and review feedback.
            if origin.get("task_run_id") and origin["task_run_id"] != run.get("id"):
                continue
            if kind == "workflow_node":
                status = "interrupted" if run.get("status") == "interrupted" else {"ready": "queued", "blocked": "rejected"}.get(task["status"], task["status"])
                assets = [f.get("asset_id") or f.get("id") for f in task.get("outputs", []) if isinstance(f, dict) and (f.get("asset_id") or f.get("id"))]
                db.db.execute("UPDATE workflow_nodes SET status=?,checkpoint=?,output_assets=?,error=?,ended_at=? WHERE id=?",
                    (status, _json(checkpoint), _json(assets), run.get("error", ""), run.get("ended_at", ""), key))
                node = db.db.execute("SELECT run_id FROM workflow_nodes WHERE id=?", (key,)).fetchone()
                if node:
                    parent_status = {"delivered": "queued", "awaiting_review": "awaiting_review", "blocked": "blocked",
                                     "failed": "failed", "cancelled": "cancelled", "running": "running"}.get(task["status"])
                    if run.get("status") == "interrupted":
                        parent_status = "interrupted"
                    if parent_status:
                        error = run.get("error", "") or ("人工审查已退回，请按修改意见重试节点" if task["status"] == "blocked" else "")
                        db.db.execute("UPDATE workflow_runs SET status=?,error=? WHERE id=? AND status<>'cancelled'", (parent_status, error, node["run_id"]))
            elif kind == "team_task":
                status = {"ready": "queued", "awaiting_review": "review", "delivered": "done", "cancelled": "canceled", "blocked": "queued"}.get(task["status"], task["status"])
                files = task.get("outputs") or []
                paths = [f.get("path", "") for f in files if isinstance(f, dict)]
                assets = [f.get("asset_id") or f.get("id") for f in files if isinstance(f, dict) and (f.get("asset_id") or f.get("id"))]
                db.db.execute("UPDATE team_tasks SET status=?,result=?,reason=?,run_id=?,output_path=?,output_assets=?,checkpoint=?,review_status=?,review_feedback=?,ended_at=? WHERE id=?",
                    (status, checkpoint.get("reply", ""), run.get("error", ""), checkpoint.get("agent_run_id") or run.get("id", ""), paths[0] if paths else "", _json(assets), _json(checkpoint),
                     "approved" if task["status"] == "delivered" else "rejected" if task["status"] == "blocked" and decision and decision["decision"] == "rejected" else "pending",
                     checkpoint.get("feedback", ""), run.get("ended_at", ""), key))
                db.db.execute("UPDATE team_tasks SET attempt=?,started_at=?,review_attempt=?,approved_at=? WHERE id=?",
                    (run.get("attempt", 0), run.get("started_at", ""), sum(d["decision"] == "rejected" for d in decisions),
                     decision.get("created_at", "") if task["status"] == "delivered" and decision else "", key))
                member = db.db.execute("SELECT member_id FROM team_tasks WHERE id=?", (key,)).fetchone()
                if member:
                    states = {r["status"] for r in db.db.execute("SELECT DISTINCT p.status FROM project_tasks p JOIN project_task_origins o ON o.task_id=p.id JOIN team_tasks t ON t.id=o.legacy_id WHERE o.kind='team_task' AND t.member_id=?", (member["member_id"],))}
                    visible = next((s for s in ("running", "queued", "awaiting_review", "blocked", "failed", "delivered") if s in states), "cancelled")
                    visible = {"awaiting_review": "review", "delivered": "done", "cancelled": "idle"}.get(visible, visible)
                    db.db.execute("UPDATE team_members SET status=?,updated_at=? WHERE id=?", (visible, stamp, member["member_id"]))
            elif kind == "todo":
                status = {"queued": "IN_PROGRESS", "running": "IN_PROGRESS", "awaiting_review": "REVIEW", "delivered": "DONE"}.get(task["status"], "TODO")
                db.db.execute("UPDATE project_todos SET status=?,updated_at=? WHERE id=? AND task_id=?", (status, stamp, key, task["project_id"]))
            if kind in ("todo", "team_task"):
                field = "todo_id" if kind == "todo" else "team_task_id"
                status = {"ready": "queued", "awaiting_review": "review", "delivered": "done"}.get(task["status"], task["status"])
                assets = [f["asset_id"] for f in task.get("outputs", []) if f.get("asset_id")]
                db.db.execute("UPDATE task_links SET status=?,run_id=?,output_assets=?,updated_at=? WHERE " + field + "=?",
                              (status, checkpoint.get("agent_run_id") or run.get("id", ""), _json(assets), stamp, key))
