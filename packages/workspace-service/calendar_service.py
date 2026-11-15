"""Calendar projections of canonical tasks, schedules, runs and human reviews."""
from __future__ import annotations

from datetime import datetime
import json

PREFIX = "linked:"
LABELS = {"todo": "任务截止", "schedule": "定时任务", "schedule_run": "任务运行",
          "workflow_review": "节点审查", "workflow_run": "工作流状态", "project_task_review": "任务核查"}


def _when(value):
    try:
        stamp = str(value or "")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.date().isoformat(), parsed.strftime("%H:%M") if "T" in stamp or " " in stamp else ""
    except (ValueError, TypeError, OverflowError):
        return "", ""


def _event(kind, ident, project_id, title, when, *, status="pending", done=False,
           pending=True, target_type="", target_id="", note="", created_at="", **extra):
    day, time = _when(when)
    return {"id": PREFIX + kind + ":" + ident, "task_id": project_id,
        "title": title, "date": day, "time": time, "kind": "todo" if kind == "todo" else "milestone",
        "note": note, "created_at": created_at or str(when), "done": int(done), "is_pending": pending,
        "linked": True, "source_type": kind, "source_id": ident, "source_label": LABELS[kind],
        "status": status, "target_type": target_type, "target_id": target_id or ident,
        "can_toggle": False, **extra}


def derived(db, month=None):
    """Read live source state. No copied calendar rows can drift or be orphaned."""
    conn = db.db
    projects = {r["id"] for r in conn.execute("SELECT id FROM research_tasks")}
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = []
    if "project_todos" in tables:
        states = {}
        for record in conn.execute("SELECT * FROM project_todos WHERE due_date!=''"):
            row = dict(record)
            if row["task_id"] not in projects:
                continue
            status = row["status"]
            import project_tasks
            canonical = project_tasks.find_origin(db, "todo", row["id"]) if "project_task_origins" in tables else None
            if canonical:
                status = {"queued": "IN_PROGRESS", "running": "IN_PROGRESS", "awaiting_review": "REVIEW", "delivered": "DONE"}.get(canonical["status"], "TODO")
            elif row.get("stage"):
                import workflow_state
                pid = row["task_id"]
                if pid not in states:
                    states[pid] = workflow_state.states(db, pid)
                status = {"delivered": "DONE", "succeeded": "REVIEW", "stale": "REVIEW", "running": "IN_PROGRESS"}.get(
                    states[pid].get(row["stage"], {}).get("status"), "TODO")
            result.append(_event("todo", row["id"], row["task_id"], row["title"], row["due_date"],
                status=status, done=status == "DONE", pending=status != "DONE", target_type="project_task" if canonical else "todo",
                target_id=canonical["id"] if canonical else row["id"],
                note=row["description"], created_at=row["created_at"], can_toggle=not canonical and not bool(row.get("stage")),
                stage=row.get("stage", ""), assistant_id=row["assigned_assistant"]))
    if "schedules" in tables:
        import schedule
        for record in conn.execute("SELECT * FROM schedules WHERE enabled=1 AND frequency!='manual'"):
            row = dict(record)
            if row["project_id"] not in projects:
                continue
            stamp = row["next_run_at"] or schedule.next_run_at(row["cron"])
            result.append(_event("schedule", row["id"], row["project_id"], "下次运行 · " + row["name"], stamp,
                status="scheduled", target_type="schedule", note="应用运行时执行；Cron " + row["cron"],
                created_at=row["created_at"], assistant_id=row["assistant_id"]))
    if "schedule_runs" in tables:
        sql = ("SELECT r.*,s.project_id,n.id AS note_id,w.workflow_run_id FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id "
               "LEFT JOIN md_notes n ON n.id='schedule-' || r.id LEFT JOIN schedule_workflow_runs w ON w.schedule_run_id=r.id")
        args = []
        if month:
            sql += " WHERE substr(r.started_at,1,7)=?"
            args.append(month)
        sql += " ORDER BY r.started_at DESC LIMIT 2000"
        for record in conn.execute(sql, args):
            row = dict(record)
            if row["project_id"] not in projects:
                continue
            path = ""
            if row["note_id"]:
                root = db.base_dir(row["project_id"])
                canonical = db._md_note_path(db.md_note(row["note_id"]))
                if root and canonical and canonical.is_relative_to(root):
                    path = canonical.relative_to(root).as_posix()
            result.append(_event("schedule_run", row["id"], row["project_id"], "运行 · " + row["name"], row["started_at"],
                status=row["status"], done=row["status"] == "completed", pending=row["status"] in ("running", "awaiting_review", "blocked", "interrupted"),
                target_type="note" if path else "workflow" if row["workflow_run_id"] else "schedule", target_id=row["note_id"] if path else row["workflow_run_id"] or row["schedule_id"],
                note=row["reason"] or row["summary"], created_at=row["started_at"],
                note_id=row["note_id"] or "", note_path=path, schedule_id=row["schedule_id"], run_id=row["workflow_run_id"] or ""))
    if "workflow_executions" in tables:
        reminders = {r['node_id']: dict(r) for r in conn.execute('SELECT * FROM review_reminders')} if 'review_reminders' in tables else {}
        runs = [dict(r) for r in conn.execute("SELECT r.* FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id")]
        for run in runs:
            if run["project_id"] not in projects:
                continue
            if run["status"] in ("succeeded", "failed", "blocked", "interrupted", "cancelled"):
                pending_run = run["status"] in ("failed", "blocked", "interrupted")
                title = "工作流已完成" if run["status"] == "succeeded" else "工作流需要处理" if pending_run else "工作流已停止"
                stamp = datetime.now().date().isoformat() if pending_run else run["ended_at"] or run["started_at"]
                result.append(_event("workflow_run", run["id"], run["project_id"], title, stamp,
                    status=run["status"], done=run["status"] == "succeeded", pending=pending_run,
                    target_type="workflow", created_at=run["started_at"], run_id=run["id"], note=run["error"]))
            for record in conn.execute("SELECT * FROM workflow_nodes WHERE run_id=?", (run["id"],)):
                node = dict(record)
                acceptance = json.loads(node["acceptance"] or "{}")
                if not acceptance.get("review_required", True) or node["status"] in ("queued", "running", "failed", "interrupted"):
                    continue
                checkpoint = json.loads(node["checkpoint"] or "{}")
                config = acceptance.get("config", {})
                pending = node["status"] == "awaiting_review" and run["status"] == "awaiting_review"
                reminder = reminders.get(node['id'], {})
                stamp = (reminder.get('due_at') or datetime.now().date().isoformat()) if pending else node["ended_at"] or node["started_at"]
                result.append(_event("workflow_review", node["id"], run["project_id"], "审查 · " + config.get("label", node["stage"]), stamp,
                    status=node["status"], done=node["status"] == "delivered", pending=pending,
                    target_type="workflow", target_id=run["id"], note=checkpoint.get("feedback") or "查看节点产物后通过或退回，日历勾选不能代替人工审查。",
                    created_at=node["started_at"], run_id=run["id"], node_id=node["id"], stage=node["stage"],
                    received_at=node['ended_at'] or node['started_at'], due_at=reminder.get('due_at', ''), remind_at=reminder.get('remind_at', ''),
                    assistant_id=checkpoint.get("input", {}).get("assistant_id", "")))
    if "project_tasks" in tables:
        import review_inbox
        for item in review_inbox.pending(db):
            if not item.get("canonical_task_id") or item.get("run_id"):
                continue
            result.append(_event("project_task_review", item["canonical_task_id"], item["project_id"], "核查 · " + item["label"],
                item["due_at"] or datetime.now().date().isoformat(), status="awaiting_review", target_type="project_task",
                target_id=item["canonical_task_id"], note=item["summary"], created_at=item["received_at"],
                received_at=item["received_at"], due_at=item["due_at"], remind_at=item["remind_at"]))
    return [event for event in result if event["date"] and (not month or event["date"].startswith(month))]


def event(db, event_id):
    row = next((item for item in derived(db) if item["id"] == event_id), None)
    if not row:
        raise KeyError("关联日程的来源已变更或删除")
    return row


def toggle(db, event_id, done=None):
    current = event(db, event_id)
    if not current["can_toggle"]:
        raise ValueError("请打开关联任务或工作流处理；日历勾选不能代替执行与人工审查")
    import project_todos
    value = not current["done"] if done is None else bool(done)
    project_todos.update_todo(db, current["source_id"], status="DONE" if value else "TODO")
    return event(db, event_id)
