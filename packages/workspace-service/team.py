# -*- coding: utf-8 -*-
"""团队（A 档）：Leader + Partner 成员 + 派活队列 + 运行事件。

团队模型（Leader + 成员 + 派活队列 + 运行事件）：
  · 角色 `role = 'leader' | 'teammate'`；**Leader 恒为成员列表第一个、不可移除**；
  · 成员状态 `pending | idle | active | completed | failed | dormant` → 这里落成 idle|queued|running|done|failed|blocked；
  · 任务看板（派活卡 + 消息卡）→ 落成 team_tasks + team_events。

本应用的**硬差别（如实说明）**：多成员协作靠独立 agent 进程才换得到真并行；本应用单进程、
模型调用阻塞 → 这里是**串行队列**（一次只跑一个成员），排队状态在界面上明确显示。
"""
from __future__ import annotations
from pathlib import Path
from contextlib import nullcontext

import json
import re
import threading
import time
import traceback
from datetime import datetime
from typing import Any

import workflow

DB_PATH: Any = None          # 由 server.py 运行时注入（和 schedule.py 同一套路）

TICK = 3.0                   # 队列轮询间隔（秒）
RUNNING: set[str] = set()    # 正在跑的任务行 id（防重入）
_lock = threading.Lock()

STEPS = list(workflow.STAGES)
LEADER_NAME = "团队协调员"
LEADER_BRIEF = "负责当前团队的目标拆解、专业分工、进度跟进和交付验收。项目及团队配置由智研管家管理。"
from team_configuration import PROFILES as FIXED_LEADERS


def _db_path():
    if DB_PATH:
        return DB_PATH
    import server
    return server.DB_PATH


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------- 成员 ----------------
def ensure_leader(task_id: str, db: Any = None) -> dict[str, Any]:
    """确保团队里有 leader 行（幂等）。Leader 恒为列表第一个、不可移除。"""
    if not (task_id or "").strip():
        raise ValueError("团队要挂在一个项目或对话上（scope 不能是空）")
    with (nullcontext(db) if db is not None else workflow.WorkflowDB(_db_path())) as db:
        team_row = db.team(task_id) or {}
        aid = FIXED_LEADERS.get(team_row.get("name", ""), "") if team_row.get("kind") == "fixed" else ""
        if not aid and team_row.get("kind") == "fixed":
            # A renamed preset keeps its specialization across application restarts.
            fixed = [t for t in db.teams() if t["kind"] == "fixed" and t.get("project_id") == team_row.get("project_id")]
            index = next((i for i, t in enumerate(fixed) if t["id"] == task_id), 0)
            aid = list(FIXED_LEADERS.values())[index % len(FIXED_LEADERS)]
        assistant = db.assistant(aid) if aid else None
        defaults = {"name": assistant["name"] if assistant else (team_row.get("name", "") + "负责人" if team_row else LEADER_NAME),
                    "prompt": assistant["rules"] if assistant else LEADER_BRIEF, "assistant_id": aid}
        rows = [m for m in db.team_members(task_id) if m["role"] == "leader"]
        if rows:
            leader = rows[0]
            legacy = leader.get("assistant_id") == "builtin-steward" or (
                not leader.get("assistant_id") and leader["name"] in ("全局 Leader", "智研管家"))
            if legacy:
                return db.team_member_update(leader["id"], **defaults)
            return rows[0]
        return db.team_member_add(task_id, defaults["name"], "leader", prompt=defaults["prompt"], assistant_id=aid)


def members(task_id: str) -> list[dict[str, Any]]:
    with workflow.WorkflowDB(_db_path()) as db:
        import team_runtime
        return team_runtime.snapshot(db,task_id)['members']


def add_member(task_id: str, name: str, step: str = "", prompt: str = "") -> dict[str, Any]:
    name = (name or "").strip() or "新成员"
    step = step if step in STEPS else ""
    with workflow.WorkflowDB(_db_path()) as db:
        return db.team_member_add(task_id, name, "member", step, prompt or "")


def update_member(member_id: str, **fields: Any) -> dict[str, Any]:
    fields = {k: v for k, v in fields.items() if v is not None}   # 没传的字段别写成 NULL
    with workflow.WorkflowDB(_db_path()) as db:
        return db.team_member_update(member_id, **fields)


def remove_member(member_id: str) -> dict[str, Any]:
    with workflow.WorkflowDB(_db_path()) as db:
        row = db.team_member(member_id)
        if not row:
            raise ValueError("成员不存在")
        if row["role"] == "leader":
            raise ValueError("Leader 不能移除（既定规则）")
        db.team_member_remove(member_id)
        return {"removed": member_id}


# ---------------- 派活 ----------------
def _task_ids(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("depends_on 必须是任务 ID 列表")
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _output_contract(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("output_contract 必须是 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ValueError("output_contract 必须是 JSON 对象")
    return value


def _validate_dependencies(db: Any, task_id: str, depends_on: list[str]) -> None:
    if not depends_on:
        return
    rows = {row["id"]: row for row in db.db.execute(
        "SELECT id, task_id, depends_on FROM team_tasks WHERE task_id=?", (task_id,))}
    missing = [item for item in depends_on if item not in rows]
    if missing:
        raise ValueError("依赖任务不存在：" + ", ".join(missing))


def _dependency_state(row: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> str:
    for dependency in _task_ids(row.get("depends_on")):
        state = tasks.get(dependency)
        if not state or state.get("status") in ("failed", "canceled", "blocked"):
            return "blocked"
        if state.get("status") != "done":
            return "waiting"
        if state.get("reviewer_id") and state.get("review_status") != "approved":
            return "waiting"
    return "ready"


def _project_scope(db, scope):
    office = db.team(scope)
    if office:
        project_id = office.get("project_id") or ""
        if project_id and not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
            raise ValueError("团队绑定的项目已不存在，请重新绑定项目")
        return project_id
    return scope if db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (scope,)).fetchone() else ""


def _command(db, project_id, authority=None):
    import project_tasks
    return authority or getattr(db, "project_task_context", None) or project_tasks.CommandContext.system(project_id)


def _executor(db, project_id, scope, member):
    import project_bindings
    office = db.team(scope)
    kind, target = ("team", scope) if office else ("assistant", member.get("assistant_id") or "")
    if office:
        template = db.db.execute('SELECT template_team_id FROM team_execution_instances WHERE instance_team_id=?', (scope,)).fetchone()
        if template: target = template[0]
    grant = db.db.execute("SELECT id FROM project_executor_bindings WHERE project_id=? AND kind=? AND target_id=?",
                          (project_id, kind, target)).fetchone()
    identifier = grant['id'] if grant else 'global:'+kind+':'+target
    return project_bindings.resolve(db, project_id, identifier, member_id=member["id"] if office else "")


def _legacy_output(db, project_id, row, checkpoint):
    import asset_service
    root = Path(db.base_dir(project_id)).resolve()
    versions = {str(Path(item["path"]).resolve()): item.get("content_hash") or item.get("sha256")
                for item in (checkpoint.get("output") or {}).get("file_versions", []) if isinstance(item, dict) and item.get("path")}
    paths = [row["output_path"]] if row.get("output_path") else []
    assets = row.get("output_assets") or []
    assets = json.loads(assets) if isinstance(assets, str) else assets
    if not isinstance(assets, list):
        raise ValueError("历史团队产物关联格式无效")
    for asset_id in assets:
        asset = asset_service.get(db, asset_id)
        if not asset or asset["project_id"] != project_id:
            raise ValueError("历史团队产物不存在或不属于当前项目")
        if asset.get("storage") != "file":
            continue
        path = str(Path(asset["canonical_uri"]).resolve())
        paths.append(path)
        revision = db.db.execute("SELECT content_hash FROM asset_revisions WHERE asset_id=? AND run_id=? ORDER BY revision DESC LIMIT 1",
                                 (asset_id, row.get("run_id") or "")).fetchone() if row.get("run_id") else None
        expected = revision["content_hash"] if revision else asset.get("content_hash")
        if expected:
            versions.setdefault(path, expected)
    files, content = [], [row.get("result") or checkpoint.get("reply") or ""]
    paths.extend(versions)
    for raw in dict.fromkeys(str(Path(path).resolve()) for path in paths):
        path = Path(raw)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("历史团队产物文件不存在或超出项目范围")
        digest = asset_service.sha256_file(path)
        if versions.get(raw) and versions[raw] != digest:
            raise ValueError("历史团队产物文件版本已变化")
        files.append({"path": raw, "content_hash": digest, "sha256": digest})
        if path.suffix.lower() in (".md", ".txt", ".json", ".tex", ".csv", ".yaml", ".yml"):
            content.append(path.read_text(encoding="utf-8", errors="replace"))
    if not files:
        raise ValueError("历史团队产物缺少可验证的项目文件")
    return {"files": files, "content": "\n\n".join(content)}


def _canonical_task(db, row, authority=None, visiting=None):
    """Preserve saved attempts; queued assignments keep the ordinary creation path."""
    import project_tasks
    existing = project_tasks.find_origin(db, "team_task", row["id"])
    if existing:
        return existing
    project_id = _project_scope(db, row["task_id"])
    if not project_id:
        return None
    visiting = set(visiting or ())
    if row["id"] in visiting:
        raise ValueError("团队任务依赖形成循环")
    visiting.add(row["id"])
    member = db.team_member(row["member_id"])
    if not member or member["task_id"] != row["task_id"]:
        raise ValueError("执行成员已移除或不属于当前团队")
    executor = _executor(db, project_id, row["task_id"], member)
    dependencies = []
    for key in _task_ids(row.get("depends_on")):
        legacy = db.team_task(key)
        if not legacy or legacy["task_id"] != row["task_id"]:
            raise ValueError("依赖任务不存在于当前团队：" + key)
        dependency = _canonical_task(db, legacy, authority, visiting)
        dependencies.append(dependency["id"])
    checkpoint = _template_checkpoint(row)
    snapshot = checkpoint.get("queued_template_snapshot")
    if not checkpoint:
        import prompt_templates
        snapshot = prompt_templates.resolve(db, project_id, step=member.get("step", ""),
            member_id=member["id"], assistant_id=executor["assistant_id"])
    context = _command(db, project_id, authority)
    task = project_tasks.create(db, {"project_id": project_id, "title": row["title"], "goal": row["prompt"],
        "executor_binding_id": executor["binding_id"], "member_id": executor["member_id"] if executor["kind"] == "team" else "",
        "output_contract": _output_contract(row.get("output_contract")), "review_required": True,
        "dependencies": dependencies, "metadata": {"template_snapshot": snapshot, "legacy_team_scope": row["task_id"],
            "legacy_member_id": member["id"], "reviewer_id": row.get("reviewer_id") or "", "legacy_record": dict(row)}},
        authority=context, request_key="team-task:" + row["id"])
    if row.get("status") != "queued":
        saved = row.get("checkpoint") or {}
        saved = json.loads(saved) if isinstance(saved, str) else dict(saved)
        saved.setdefault("reply", row.get("result") or "")
        if row.get("review_feedback"):
            saved.setdefault("feedback", row["review_feedback"])
        status = {"done": "delivered", "review": "awaiting_review", "canceled": "cancelled", "blocked": "interrupted"}.get(row["status"], row["status"])
        legacy = {**row, "status": status, "checkpoint": saved, "error": row.get("reason") or ""}
        output = None
        if status in ("delivered", "awaiting_review"):
            try:
                output = _legacy_output(db, project_id, row, saved)
            except (ValueError, OSError, KeyError) as exc:
                legacy["error"] = str(exc)
        task = project_tasks.restore_attempt(db, task["id"], legacy, output_snapshot=output, origin_kind="team_task")
    project_tasks.link_origin(db, task["id"], "team_task", row["id"])
    link = db.db.execute("SELECT todo_id FROM task_links WHERE team_task_id=?", (row["id"],)).fetchone()
    if link:
        project_tasks.link_origin(db, task["id"], "todo", link["todo_id"])
    return project_tasks.get(db, task["id"])


def _start_canonical(db, task, authority=None):
    import project_tasks
    if any(project_tasks.get(db, key)["status"] != "delivered" for key in task["dependencies"]):
        return None
    return project_tasks.start(db, task["id"], authority=_command(db, task["project_id"], authority))


def _with_canonical(db, row, task):
    result = dict(db.team_task(row["id"]) or row)
    result.update(canonical_task_id=task["id"], canonical_task=task)
    return result


def assign(task_id: str, member: str, prompt: str, title: str = "", trigger: str = "leader",
           origin: str = "", depends_on: Any = None, output_contract: dict[str, Any] | None = None,
           reviewer_id: str = "", todo_id: str = "", db: Any = None, *, authority=None) -> dict[str, Any]:
    """把一件事派给某个成员 → 落一条 queued 任务，等队列 worker 串行跑。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("要派的事不能为空")
    with (nullcontext(db) if db is not None else workflow.WorkflowDB(_db_path())) as db:
        project_id = _project_scope(db, task_id)
        if not project_id:
            ensure_leader(task_id, db)
        rows = db.team_members(task_id)
        key = (member or "").strip().lower()
        hit = next((m for m in rows if m["id"] == member or m["name"].lower() == key), None)
        if not hit:
            names = "、".join(m["name"] for m in rows)
            raise ValueError(f"团队里没有成员「{member}」（现有：{names}）")
        deps = _task_ids(depends_on)
        _validate_dependencies(db, task_id, deps)
        contract = _output_contract(output_contract)
        reviewer = (reviewer_id or "").strip()
        if reviewer and not any(item["id"] == reviewer for item in rows):
            raise ValueError("Reviewer 必须是当前项目团队成员")
        import prompt_templates
        if project_id:
            _executor(db, project_id, task_id, hit)
        snapshot = prompt_templates.resolve(db, project_id, step=hit.get("step", ""),
                                            member_id=hit["id"], assistant_id=hit.get("assistant_id", "")) if project_id else None
        with db.transaction():
            db.db.execute("BEGIN IMMEDIATE")
            row = db.team_task_add(task_id, hit["id"], hit["name"], title or prompt[:40], prompt,
                                   trigger, origin, deps, contract, reviewer, commit=False)
            db.db.execute("UPDATE team_tasks SET checkpoint=? WHERE id=?",
                          (json.dumps({"queued_template_snapshot": snapshot}, ensure_ascii=False), row["id"]))
            if todo_id:
                import project_todos
                project_todos.bind_execution(db, todo_id, project_id, team_task_id=row["id"], commit=False)
            canonical = _canonical_task(db, row, authority) if project_id else None
        db.team_event_add(row["id"], task_id, "assign", f"{trigger} 派给「{hit['name']}」：{prompt[:160]}")
        db.team_member_update(hit["id"], status="queued")
        if canonical:
            run = _start_canonical(db, canonical, authority)
            if run:
                import project_task_execution
                project_task_execution.kick(db.path, run["id"])
            import project_tasks
            return _with_canonical(db, row, project_tasks.get(db, canonical["id"]))
        return row


def board(task_id: str, limit: int = 40, *, db=None) -> dict[str, Any]:
    """看板数据：成员（含状态）+ 任务列表 + 事件流（对话状态与执行情况）。"""
    with (nullcontext(db) if db is not None else workflow.WorkflowDB(_db_path())) as db:
        import team_runtime
        mem = team_runtime.snapshot(db,task_id)['members']
        tasks = db.team_tasks(task_id, limit)
        events = db.team_events(task_id, 200)
        import project_tasks
        for row in tasks:
            canonical = project_tasks.find_origin(db, "team_task", row["id"])
            if canonical:
                row.update(canonical_task_id=canonical["id"], canonical_task=canonical)
    by_task: dict[str, list] = {}
    for e in events:
        by_task.setdefault(e["team_task_id"], []).append(e)
    for t in tasks:
        t["events"] = by_task.get(t["id"], [])
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {"members": mem, "tasks": tasks, "counts": counts,
            "leader_brief": next((m["prompt"] for m in mem if m["role"] == "leader"), LEADER_BRIEF), "steps": STEPS,
            "serial_note": "单进程串行队列：一次只跑一个成员（真并行要独立 agent 进程）",
            "running": sorted(RUNNING)}


def cancel(task_id_row: str, *, authority=None) -> dict[str, Any]:
    with _lock, workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        import project_tasks
        canonical = project_tasks.find_origin(db, "team_task", task_id_row)
        if not canonical and str(row.get("origin") or "").startswith("workflow:"):
            raise ValueError("此任务由工作流管理，请在工作流中停止当前运行")
        if not canonical and row["status"] in ("done", "failed", "canceled"):
            return {"status": row["status"], "id": task_id_row}
        if canonical or _project_scope(db, row["task_id"]):
            canonical = canonical or _canonical_task(db, row, authority)
            canonical = project_tasks.cancel(db, canonical["id"], authority=_command(db, canonical["project_id"], authority))
            return {"canceled": task_id_row, "canonical_task_id": canonical["id"], "task": canonical}
        _pending_retries.discard(task_id_row)
        if row["status"] in ("done", "failed", "canceled"):
            return {"status": row["status"], "id": task_id_row}
        signal = _cancel_signals.get(task_id_row)
        if signal:
            signal.set()
        db.team_task_mark(task_id_row, "canceled", ended_at=_now(), reason="手动取消")
        return {"canceled": task_id_row}


def _template_checkpoint(row):
    checkpoint = row.get("checkpoint") or {}
    if isinstance(checkpoint, str):
        checkpoint = json.loads(checkpoint)
    if "queued_template_snapshot" in checkpoint:
        return {"queued_template_snapshot": checkpoint["queued_template_snapshot"]}
    if "template_snapshot" in checkpoint:
        return {"queued_template_snapshot": checkpoint["template_snapshot"]}
    return {}


def retry(task_id_row: str, *, authority=None) -> dict[str, Any]:
    with _lock, workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        import project_tasks
        canonical = project_tasks.find_origin(db, "team_task", task_id_row)
        if not canonical and str(row.get("origin") or "").startswith("workflow:"):
            raise ValueError("此任务由工作流管理，请在工作流中重试当前节点")
        if canonical or _project_scope(db, row["task_id"]):
            canonical = canonical or _canonical_task(db, row, authority)
            run = project_tasks.start(db, canonical["id"], authority=_command(db, canonical["project_id"], authority))
            if canonical.get("workflow_run_id") and not canonical.get("parent_task_id"):
                import workflow_execution
                workflow_execution.kick(db.path, canonical["workflow_run_id"])
            else:
                import project_task_execution
                project_task_execution.kick(db.path, run["id"])
            return {"queued": task_id_row, "canonical_task_id": canonical["id"], "task": project_tasks.get(db, canonical["id"]), "run": run}
        signal = _cancel_signals.get(task_id_row)
        if row["status"] == "running" or (signal and not signal.is_set()):
            raise ValueError("还在跑")
        if task_id_row in _cancel_signals:
            _pending_retries.add(task_id_row)
        db.team_task_mark(task_id_row, "queued", started_at="", ended_at="", reason="", result="", checkpoint=_template_checkpoint(row))
        db.team_event_add(task_id_row, row["task_id"], "note", "重新排队")
        return {"queued": task_id_row}


def review(task_id_row: str, approved: bool, reviewer_id: str = "", feedback: str = "", *, authority=None) -> dict[str, Any]:
    """Approve a gated team output or return it to the worker with feedback."""
    with workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        import project_tasks
        canonical = project_tasks.find_origin(db, "team_task", task_id_row)
        if canonical or _project_scope(db, row["task_id"]):
            if not canonical:
                canonical = _canonical_task(db, row, authority)
            expected = (row.get("reviewer_id") or "").strip()
            if expected and (reviewer_id or "").strip() != expected:
                raise ValueError("当前成员不是该任务的 Reviewer")
            canonical = project_tasks.review(db, canonical["id"], approved=approved, feedback=feedback,
                authority=_command(db, canonical["project_id"], authority))
            if approved and canonical.get("workflow_run_id") and not canonical.get("parent_task_id"):
                import workflow_execution
                workflow_execution.kick(db.path, canonical["workflow_run_id"])
            return _with_canonical(db, row, canonical)
        if str(row.get("origin") or "").startswith("workflow:"):
            raise ValueError("此任务由工作流管理，请在工作流中审查并继续后续节点")
        expected = (row.get("reviewer_id") or "").strip()
        if not expected:
            raise ValueError("任务未配置 Reviewer")
        if expected and (reviewer_id or "").strip() != expected:
            raise ValueError("当前成员不是该任务的 Reviewer")
        if row.get("status") != "review":
            raise ValueError("任务尚未进入验收阶段")
        if approved:
            linked = db.db.execute("SELECT p.stage FROM task_links l JOIN project_todos p ON p.id=l.todo_id "
                                   "WHERE l.team_task_id=?", (task_id_row,)).fetchone()
            if linked and linked["stage"]:
                import workflow_state
                workflow_state.accept(db, row["task_id"], linked["stage"])
            db.team_task_mark(task_id_row, "done", review_status="approved", approved_at=_now(),
                              review_feedback=feedback[:2000], checkpoint={**_template_checkpoint(row), "phase": "approved"})
            db.team_member_update(row["member_id"], status="done")
            db.team_event_add(task_id_row, row["task_id"], "review", "Reviewer 已通过产物")
        else:
            count = int(row.get("review_attempt") or 0) + 1
            db.team_task_mark(task_id_row, "queued", review_status="rejected", review_attempt=count,
                              approved_at="", review_feedback=feedback[:2000], reason=feedback[:500],
                              checkpoint={**_template_checkpoint(row), "phase": "returned", "review_attempt": count}, retry_at="")
            db.team_member_update(row["member_id"], status="queued")
            db.team_event_add(task_id_row, row["task_id"], "review", "Reviewer 退回产物：" + feedback[:300])
        return db.team_task(task_id_row) or {}


# ---------------- 队列 worker（串行） ----------------
_cancel_signals: dict[str, threading.Event] = {}
_pending_retries: set[str] = set()


def _run_one(row: dict[str, Any]) -> None:
    with workflow.WorkflowDB(_db_path()) as db:
        import project_tasks
        if project_tasks.find_origin(db, "team_task", row["id"]) or _project_scope(db, row["task_id"]):
            return _run_project(row)
    import execution
    signal = threading.Event()
    _cancel_signals[row["id"]] = signal
    try:
        with execution.scope(signal.is_set):
            _execute_one(row, signal)
    except Exception as exc:
        # Preparation can fail before the model loop (for example, a removed
        # input file). Persist that failure instead of leaving a running task.
        with workflow.WorkflowDB(_db_path()) as db:
            current = db.team_task(row["id"]) or {}
            if not current:
                return
            status = "canceled" if signal.is_set() else "failed"
            db.team_task_mark(row["id"], status, ended_at=_now(), reason=str(exc))
            db.team_member_update(row["member_id"], status="idle" if signal.is_set() else "failed")
            if current.get("run_id"):
                import run_service
                run_service.finish(db, current["run_id"], "cancelled" if signal.is_set() else "failed", error=str(exc))
    finally:
        with _lock:
            _cancel_signals.pop(row["id"], None)
            if row["id"] in _pending_retries:
                _pending_retries.discard(row["id"])
                with workflow.WorkflowDB(_db_path()) as db:
                    if db.team_task(row["id"]):
                        db.team_task_mark(row["id"], "queued", started_at="", ended_at="", reason="",
                                          result="", checkpoint=_template_checkpoint(db.team_task(row["id"])), retry_at="")
                        db.team_member_update(row["member_id"], status="queued")


def _execute_one(row: dict[str, Any], signal: threading.Event) -> None:
    with workflow.WorkflowDB(_db_path()) as db:
        import project_tasks
        if project_tasks.find_origin(db, "team_task", row["id"]) or _project_scope(db, row["task_id"]):
            return _run_project(row, signal.is_set)
        current = db.team_task(row["id"]) or {}
        if current.get("status") == "canceled":
            return
        member = db.team_member(row["member_id"])
        if not member or member["task_id"] != row["task_id"]:
            raise ValueError("执行成员已移除或不属于当前聊天团队")
        import execution
        import agent_settings
        scope = row["task_id"]
        history_scope = _history_scope(db, scope)
        checkpoint = json.loads(row.get("checkpoint") or "{}")
        attempt = int(row.get("attempt") or 0) + 1
        db.team_task_mark(row["id"], "running", started_at=_now(), attempt=attempt, checkpoint=checkpoint)
        db.team_member_update(member["id"], status="running")
        db.team_event_add(row["id"], scope, "start", "开始执行聊天任务")
        db.job_checkpoint = checkpoint
        db.resuming = bool(checkpoint.get("messages") or checkpoint.get("completed_calls"))
        if "queued_template_snapshot" in checkpoint:
            db.template_snapshot = checkpoint["queued_template_snapshot"]
            db.template_resolved = True
        def save_checkpoint(**values):
            checkpoint.update(values)
            db.db.execute("UPDATE team_tasks SET checkpoint=? WHERE id=?", (json.dumps(checkpoint, ensure_ascii=False), row["id"]))
            db.db.commit()
        db.save_agent_checkpoint = save_checkpoint
        contract = _output_contract(row.get("output_contract"))
        if contract.get("required_files"):
            raise ValueError("聊天任务不能交付项目文件，请先将团队绑定到项目")
        history = workflow.recent_dialogue([dict(message) for message in db.messages(scope, history_scope)], workflow.context_budget(db))
        extra = json.dumps({"member_responsibility": member.get("prompt") or "", "history": history,
                            "output_contract": contract}, ensure_ascii=False)
        config = agent_settings.runtime_config(db, scope, member.get("assistant_id", ""), member)
        profile = db.model(config["model_id"]) if config.get("model_id") else None
        def emit(event):
            kind = event.get("type")
            if kind == "assistant" and event.get("content"):
                _ev(row, "assistant", str(event["content"])[:400], db=db)
            elif kind in ("tool_call", "tool_result"):
                _ev(row, "tool", str(event.get("name") or "") + " " + str(event.get("summary") or ""), db=db)
        out = workflow.run_agent(db, scope, row["prompt"], emit=emit, publish=False, profile=profile,
            extra_context=extra, assistant_id=member.get("assistant_id", ""), member=member,
            scope=history_scope, max_steps=8, cancel_check=signal.is_set)
        execution.check()
        if signal.is_set():
            raise execution.Cancelled("聊天任务已取消")
        text = str((out or {}).get("reply") or "").strip()
        if not text:
            raise ValueError("聊天任务未产出内容")
        missing = [str(section) for section in contract.get("sections", []) if str(section).casefold() not in text.casefold()]
        if missing or len(text) < int(contract.get("min_chars") or 1):
            raise ValueError("产物不满足交付契约：" + ("缺少 " + "、".join(missing) if missing else "内容长度不足"))
        _save_reply(db,scope,member,text,history_scope)
        needs_review = bool(row.get("reviewer_id"))
        status = "review" if needs_review else "done"
        db.team_task_mark(row["id"], status, ended_at=_now(), result=text, output_path="", output_assets=[],
            checkpoint={**_template_checkpoint({"checkpoint": checkpoint}), "phase": status, "attempt": attempt},
            review_status="pending" if needs_review else "not_required")
        db.team_member_update(member["id"], status=status, note=row["title"])
        db.team_event_add(row["id"], scope, status, "聊天产物已保存到团队会话" + ("，等待 Reviewer" if needs_review else ""))


def _ev(row: dict[str, Any], kind: str, text: str, db: Any = None) -> None:
    try:
        if db is not None:
            db.team_event_add(row["id"], row["task_id"], kind, text)
        else:
            with workflow.WorkflowDB(_db_path()) as conn:
                conn.team_event_add(row["id"], row["task_id"], kind, text)
    except Exception:
        pass


def _run_project(row, cancel_check=None):
    import project_tasks
    import project_task_execution
    with workflow.WorkflowDB(_db_path()) as db:
        task = _canonical_task(db, row)
        if task["metadata"].get("team_chat_scope") and not task["latest_run"]:
            return False
        if task["status"] == "ready":
            run = _start_canonical(db, task)
        elif task["status"] == "queued":
            run = task["latest_run"]
        else:
            return False
        if not run or run["status"] != "queued":
            return False
        project_task_execution.execute(db, run["id"], cancel_check=cancel_check)
        return True


def tick() -> int:
    """跑一轮队列（串行：一次一个）。返回本轮跑了几个。"""
    done = 0
    with workflow.WorkflowDB(_db_path()) as db:
        all_tasks = db.team_tasks(None, None)
        tasks_by_id = {task["id"]: task for task in all_tasks}
        rows = []
        for task in all_tasks:
            if task["status"] != "queued":
                continue
            import project_tasks
            if project_tasks.find_origin(db, "team_task", task["id"]) or _project_scope(db, task["task_id"]):
                rows.append(task)
                continue
            state = _dependency_state(task, tasks_by_id)
            if state == "blocked":
                reason = "依赖任务已失败或取消：" + ", ".join(_task_ids(task.get("depends_on")))
                db.team_task_mark(task["id"], "blocked", reason=reason,
                                  checkpoint={"phase": "blocked", "reason": reason})
                db.team_member_update(task["member_id"], status="blocked", note=reason[:120])
                db.team_event_add(task["id"], task["task_id"], "blocked", reason)
            elif state == "ready":
                rows.append(task)
    rows.sort(key=lambda r: r["created_at"])
    for row in rows:
        if row["id"] in RUNNING:
            continue
        with _lock:
            if row["id"] in RUNNING:
                continue
            RUNNING.add(row["id"])
        try:
            with workflow.WorkflowDB(_db_path()) as db:
                project_id = _project_scope(db, row["task_id"])
                canonical = project_tasks.find_origin(db, "team_task", row["id"])
            if project_id or canonical:
                done += int(_run_project(row))
            else:
                _run_one(row)
                done += 1
        finally:
            RUNNING.discard(row["id"])
    return done


def start_worker() -> None:
    recover_tasks()
    threading.Thread(target=_loop, daemon=True).start()


def recover_tasks() -> dict[str, list[str]]:
    result = {"resumed": [], "interrupted": []}
    with workflow.WorkflowDB(_db_path()) as db:
        for row in db.team_tasks(None, None):
            if row["status"] != "running" or row["id"] in RUNNING:
                continue
            import project_tasks
            if project_tasks.find_origin(db, "team_task", row["id"]):
                continue
            checkpoint = json.loads(row.get("checkpoint") or "{}")
            safe = checkpoint.get("phase") in ("model", "ready")
            state = "queued" if safe else "blocked"
            db.team_task_mark(row["id"], state, reason="从已保存的执行点恢复" if safe else "工具执行结果未确认，请核对产物后重试")
            if row.get("run_id"):
                import run_service
                run_service.finish(db, row["run_id"], "interrupted", error="服务重启")
            result["resumed" if safe else "interrupted"].append(row["id"])
    return result


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception:
            traceback.print_exc()
        time.sleep(TICK)


if __name__ == "__main__":       # 手跑自检：python team.py
    import os, tempfile
    os.environ.setdefault("RESEARCH_WORKBENCH_DB", str(tempfile.gettempdir() + "/team_selfcheck.sqlite3"))
    import pathlib
    p = pathlib.Path(os.environ["RESEARCH_WORKBENCH_DB"])
    if p.exists():
        p.unlink()
    DB_PATH = p
    with workflow.WorkflowDB(p) as db:
        tid = db.create_task("自检项目", "问题", "体系", "")
    ms = members(tid)
    assert ms and ms[0]["role"] == "leader", ms
    m2 = add_member(tid, "检索员", "search", "负责检索")
    assert len(members(tid)) == 2
    row = assign(tid, "检索员", "检索 O3 钠电掺杂近三年的论文")
    assert row["status"] == "queued"
    b = board(tid)
    assert b["members"][0]["name"] == LEADER_NAME
    assert b["counts"].get("queued") == 1
    try:
        remove_member(ms[0]["id"]); raise SystemExit("Leader 不该能删")
    except ValueError:
        pass
    cancel(row["id"])
    assert board(tid)["counts"].get("canceled") == 1
    retry(row["id"])
    assert board(tid)["counts"].get("queued") == 1
    print("team.py 自检通过：", [m["name"] + "/" + m["role"] for m in members(tid)], b["counts"])


# ============================ v2：成员=专家 + 团队内 AI 聊天平台 ============================

MAX_PICKS = 1000000      # Selection is not truncated; the UI shows the complete assistant roster.
# 身份色：0 号固定给 Leader，其余走低饱和 slate 系
MEMBER_COLORS = ["var(--brand)", "#5c9ea4", "#b58a5e", "#8f7fb5", "#7fa06a", "#b57f92"]
STOP_WORDS = ("完成", "全部完成", "已全部完成", "DONE", "收工")

TEAM_RUNS: dict[str, dict[str, Any]] = {}          # scope -> {turn,total,current,phase,stop}（在内存，重启即丢）
_LOCK = threading.Lock()
TEAM_SCOPE = "team"                              # 团队记录存在 chat 表的 scope 里


def color_of(m: dict[str, Any]) -> str:
    return MEMBER_COLORS[int(m.get("color") or 0) % len(MEMBER_COLORS)]


def picks(scope: str) -> list[dict[str, Any]]:
    """团队里被选中的专家成员（不含 Leader）。"""
    return [m for m in members(scope) if m.get("role") == "member"]


def add_assistant(scope: str, assistant_id: str) -> dict[str, Any]:
    """把一个专家拉进团队（团队成员就是专家实例）。最多 MAX_PICKS 个。"""
    with workflow.WorkflowDB(_db_path()) as db:
        project_id = _project_scope(db, scope)
        asst = db.assistant(assistant_id)
        if not asst:
            raise ValueError("没有这个专家 —— 先去「专家」页建一个")
        asst = db.require_assistant(assistant_id)
        cur = db.team_members(scope)
        if any(m["assistant_id"] == assistant_id for m in cur):
            raise ValueError("「%s」已经在团队里了" % asst["name"])
        member = db.team_member_add(scope, asst["name"], "member", "", asst["rules"] or "",
                                   assistant_id=assistant_id, color=1 + len([m for m in cur if m["role"] == "member"]))
        if project_id:
            import project_bindings
            project_bindings.register(db, project_id, "assistant", assistant_id, source="legacy_team")
        return member


def _history_scope(db, scope):
    import agent_settings
    return "team:" + agent_settings.session(db, scope)["id"]


def _save_reply(db, scope, member, content, history_scope, run=None):
    import execution_conversations
    identity=execution_conversations.actor(db,(run or {}).get('executor_snapshot') or {'assistant_id':member.get('assistant_id',''),'member':member})
    db.add_message(scope,'assistant',content,events=[{'type':'expert_identity',**identity}],scope=history_scope,sender=member['name'])


def transcript(scope: str, limit: int | None = None) -> list[dict[str, Any]]:
    """团队群里的对话（谁说的都记在 sender 上）。"""
    with workflow.WorkflowDB(_db_path()) as db:
        rows = [{"role": m["role"], "content": m["content"],
                 "sender": m.get("sender") or ("你" if m["role"] == "user" else "成员"),
                 "assistant_name": next((event.get('sender','') for event in m.get('events',[]) if event.get('type')=='expert_identity'),''),
                 "at": m["created_at"]} for m in db.messages(scope, _history_scope(db, scope))]
        return rows[-limit:] if limit else rows


def say(scope: str, text: str, start: bool = True) -> dict[str, Any]:
    """用户往群里插一句话 → 团队接着往下推进。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("要说点什么")
    with workflow.WorkflowDB(_db_path()) as db:
        history_scope = _history_scope(db, scope)
        db.add_message(scope, "user", text, scope=history_scope, sender="你")
        db.db.execute("UPDATE team_sessions SET title=CASE WHEN title='新会话' THEN ? ELSE title END, updated_at=? WHERE team_id=? AND active=1", (text[:40], _now(), scope))
        db.db.commit()
    started = kick(scope) if start else False
    return {"ok": True, "started": started, "running": scope in TEAM_RUNS}


def next_speaker(text: str, names: list[str], last: str | None) -> str | None:
    """交接规则（@ 交接）：说了收工词就散会；@ 谁就归谁；没说就按顺序轮下一个。"""
    if not names:
        return None
    blob = text or ""
    if any(w in blob for w in STOP_WORDS):
        return None
    for n in names:
        if ("@" + n) in blob or ("＠" + n) in blob:
            return n
    if last is None or last not in names:
        return names[0]
    return names[(names.index(last) + 1) % len(names)]


def _set(scope: str, **kw: Any) -> None:
    with _LOCK:
        st = TEAM_RUNS.get(scope)
        if st:
            st.update(kw)


def kick(scope: str, turns: int = 6) -> bool:
    """让这个团队跑起来（一次只跑一个团队；已经在跑就不重复起）。"""
    with _LOCK:
        if scope in TEAM_RUNS:
            return False
        TEAM_RUNS[scope] = {"turn": 0, "total": turns, "current": "", "phase": "排队中", "stop": False}
    threading.Thread(target=_round, args=(scope, turns), daemon=True).start()
    return True


def stop(scope: str) -> dict[str, Any]:
    _set(scope, stop=True, phase="停止中")
    task_id = (TEAM_RUNS.get(scope) or {}).get("canonical_task_id")
    if task_id:
        import project_tasks
        with workflow.WorkflowDB(_db_path()) as db:
            task = project_tasks.get(db, task_id)
            project_tasks.cancel(db, task_id, authority=project_tasks.CommandContext.system(task["project_id"]))
    return {"ok": True}


def _project_round(db, scope, turns, history_scope):
    import project_bindings
    import project_tasks
    import project_task_execution
    import prompt_templates
    project_id = _project_scope(db, scope)
    members = db.team_members(scope)
    leaders = [member for member in members if member["role"] == "leader"]
    if len(leaders) != 1:
        raise ValueError("请为项目团队配置唯一的负责人")
    executor = _executor(db, project_id, scope, leaders[0])
    available = []
    for member in members:
        try:
            project_bindings.resolve(db, project_id, executor["binding_id"], member_id=member["id"])
            available.append(member)
        except ValueError:
            continue
    names, by_name = [m["name"] for m in available], {m["name"]: m for m in available}
    history = [dict(message) for message in db.messages(scope, history_scope)]
    user_text = next((message["content"] for message in reversed(history) if message["role"] == "user"), "继续推进团队工作")
    scope_grant = {"allowed_binding_ids": [executor["binding_id"]], "allowed_member_ids": [m["id"] for m in available]}
    row = db.team_task_add(scope, leaders[0]["id"], leaders[0]["name"], user_text[:100], user_text,
                           trigger="team_chat", origin=history_scope)
    parent = project_tasks.create(db, {"project_id": project_id, "title": row["title"],
        "goal": "汇总本次团队讨论和成员产物，回答用户目标并明确证据、分歧和后续行动：\n" + user_text,
        "executor_binding_id": executor["binding_id"], "scope": scope_grant, "review_required": True,
        "metadata": {"team_chat_scope": scope, "team_chat_session": history_scope, "legacy_record": row}},
        authority=project_tasks.CommandContext.system(project_id), request_key="team-chat:" + row["id"])
    project_tasks.link_origin(db, parent["id"], "team_task", row["id"])
    _set(scope, canonical_task_id=parent["id"])
    last, previous_child = None, ""
    for turn in range(1, turns + 1):
        if (TEAM_RUNS.get(scope) or {}).get("stop"):
            project_tasks.cancel(db, parent["id"], authority=project_tasks.CommandContext.system(project_id))
            db.add_message(scope, "assistant", "（已停止）", scope=history_scope, sender="系统")
            return
        history = workflow.recent_dialogue([dict(m) for m in db.messages(scope, history_scope)], workflow.context_budget(db))
        speaker = next_speaker("\n".join((m.get("content") or "") for m in history[-2:]), names, last)
        if not speaker:
            break
        member = by_name[speaker]
        _set(scope, turn=turn, current=speaker, phase="%s 正在做（第 %d/%d 轮）" % (speaker, turn, turns))
        child = project_tasks.create(db, {"project_id": project_id, "title": "团队讨论 · %s · 第%d轮" % (speaker, turn),
            "goal": "推进团队目标，只负责自己的专业分工，说明可核查依据。需交给队友时在回复开头 @ 对方姓名；讨论完成时说明结论。\n用户目标：" + user_text,
            "executor_binding_id": executor["binding_id"], "member_id": member["id"], "parent_task_id": parent["id"],
            "review_required": False, "dependencies": [previous_child] if previous_child else [],
            "metadata": {"team_chat_scope": scope, "team_chat_session": history_scope,
                "upstream_context": {"history": history, "team_members": names}, "node_config": {"max_steps": 8},
                "template_snapshot": prompt_templates.resolve(db, project_id, member_id=member["id"], assistant_id=member["assistant_id"]) }},
            authority=project_tasks.CommandContext.system(project_id, parent["id"]))
        run = project_tasks.start(db, child["id"], authority=project_tasks.CommandContext.system(project_id, parent["id"]))
        child = project_task_execution.execute(db, run["id"], cancel_check=lambda: bool((TEAM_RUNS.get(scope) or {}).get("stop")))
        body = ((child.get("latest_run") or {}).get("checkpoint") or {}).get("reply") or ""
        if child["status"] != "delivered":
            error = (child.get("latest_run") or {}).get("error") or "成员任务尚未交付"
            current = project_tasks.get(db, parent["id"])
            if current["status"] != "cancelled":
                parent_run = project_tasks.start(db, parent["id"], authority=project_tasks.CommandContext.system(project_id))
                project_tasks.fail(db, parent_run["id"], "成员任务未交付：" + error)
            db.add_message(scope, "assistant", "（%s 这一轮停止：%s）" % (speaker, error), scope=history_scope, sender="系统")
            return
        _save_reply(db,scope,member,body,history_scope,child.get("latest_run"))
        previous_child, last = child["id"], speaker
    _set(scope, current=leaders[0]["name"], phase="负责人汇总成员产物")
    run = project_tasks.start(db, parent["id"], authority=project_tasks.CommandContext.system(project_id))
    parent = project_task_execution.execute(db, run["id"], cancel_check=lambda: bool((TEAM_RUNS.get(scope) or {}).get("stop")))
    body = ((parent.get("latest_run") or {}).get("checkpoint") or {}).get("reply") or (parent.get("latest_run") or {}).get("error") or "没有生成团队结论"
    _save_reply(db,scope,leaders[0],body,history_scope,parent.get("latest_run"))
    if parent["status"] == "awaiting_review":
        db.add_message(scope, "assistant", "团队结论已生成，请在项目任务或团队看板中核查并验收。", scope=history_scope, sender="系统")


def _round(scope: str, turns: int) -> None:
    """一个 AI 聊天平台：成员之间互相 @ 派话，一轮接一轮把活推下去（串行，一轮一个人）。"""
    import workflow as _wf
    project_bound = False
    try:
        with workflow.WorkflowDB(_db_path()) as db:
            history_scope = _history_scope(db, scope)
            project_bound = bool(_project_scope(db, scope))
            if project_bound:
                try:
                    _project_round(db, scope, turns, history_scope)
                except Exception as exc:
                    db.add_message(scope, "assistant", "团队任务未完成：" + str(exc), scope=history_scope, sender="系统")
                return
            ms = [m for m in db.team_members(scope) if m["role"] == "member" or m.get("assistant_id") or m.get("model_id")
                  or m["name"] != LEADER_NAME or m["prompt"] != LEADER_BRIEF]
            names = [m["name"] for m in ms]
            by_name = {m["name"]: m for m in ms}
            if not names:
                db.add_message(scope, "assistant", "团队里还没有人 —— 先从左边的专家里选几个。",
                               scope=history_scope, sender="系统")
                return
            last = None
            for t in range(1, turns + 1):
                st = TEAM_RUNS.get(scope) or {}
                if st.get("stop"):
                    db.add_message(scope, "assistant", "（已停止）", scope=history_scope, sender="系统")
                    return
                hist = workflow.recent_dialogue([dict(m) for m in db.messages(scope, history_scope)],
                                                workflow.context_budget(db))
                tail = "\n".join((h.get("content") or "") for h in hist[-2:])
                speaker = next_speaker(tail, names, last)
                if not speaker:
                    db.add_message(scope, "assistant", "团队：这件事告一段落（收到收工信号）。",
                                   scope=history_scope, sender="系统")
                    return
                m = by_name[speaker]
                _set(scope, turn=t, current=speaker, phase="%s 正在做（第 %d/%d 轮）" % (speaker, t, turns))
                prof = None                                  # 这个成员背后接的模型（= 它那个专家绑的模型）
                extra = (
                    "【团队协作】你现在的身份是%s「%s」（不是首页全局专家）：%s\n"
                    "团队名单：%s\n"
                    "干活规则：只做你这份；结论要能核查（引用来源/数字）；"
                    "要交给谁，就在回复开头写 @那个人的名字；整件事真做完了就回复「完成」并且不要再 @人。\n"
                    "最近的团队记录：\n%s" % ("团队 Leader，负责协调与决策" if m["role"] == "leader" else "团队成员", speaker, m["prompt"] or "（没写职责）", "、".join(names),
                                            "\n".join("%s：%s" % (h.get("sender") or h.get("role"), (h.get("content") or "")[:240])
                                                        for h in hist)))
                try:
                    import agent_settings
                    config = agent_settings.runtime_config(db, scope, m.get("assistant_id", ""), m)
                    if config.get("model_id"):
                        prof = db.model(config["model_id"])
                    out = _wf.run_agent(db, scope, "继续推进（你是 %s）" % speaker, extra_context=extra,
                                        scope=history_scope, publish=False, profile=prof,
                                        assistant_id=m.get("assistant_id", ""), member=m)
                    body = (out.get("reply") or "").strip() or "（这一轮没说话）"
                except Exception as exc:                       # 一个成员失败不该掀翻整个团队，但也别空转
                    db.add_message(scope, "assistant", "（%s 这一轮失败：%s）" % (speaker, exc),
                                   scope=history_scope, sender=speaker)
                    db.add_message(scope, "assistant", "团队：%s 这一轮出错了，先停在这里 —— 修好再继续。"
                                   % speaker, scope=history_scope, sender="系统")
                    return
                _save_reply(db,scope,m,body,history_scope)
                last = speaker
                if next_speaker(body, names, last) is None:
                    db.add_message(scope, "assistant", "团队：都齐了，收工。", scope=history_scope, sender="系统")
                    return
            _set(scope, phase="轮次用完（%d 轮）" % turns)
    finally:
        if not project_bound:
            release(scope)
        with _LOCK:
            TEAM_RUNS.pop(scope, None)


def chat_state(scope: str) -> dict[str, Any]:
    """团队页要的一切：这个团队 + 成员（带色 + 各自模型）+ 群记录 + 正在跑的状态 + 会话历史 + 上限。"""
    with workflow.WorkflowDB(_db_path()) as db:
        import agent_settings
        cur_session = agent_settings.session(db, scope)
        sessions = [dict(r) for r in db.db.execute("SELECT * FROM team_sessions WHERE team_id=? ORDER BY updated_at DESC", (scope,))]
        ms = members(scope)
        for m in ms:
            m["model"] = _member_model(db, m)
        import team_runtime
        live = team_runtime.snapshot(db,scope)
        t_rec = {**(db.team(scope) or {}), 'runtime_status':live['status']}
    return {"team": t_rec, "members": ms, "max": MAX_PICKS, "palette": MEMBER_COLORS,
            "transcript": transcript(scope), "running": TEAM_RUNS.get(scope), "communications":live['communications'],
            "colors": {m["id"]: color_of(m) for m in ms},
            "session": cur_session, "sessions": sessions}


# ==================== v3：多团队（2 固定 + 3 机动）+ 成员各用自己的模型 ====================

MAX_TEAMS, MAX_FIXED, MAX_TEMP = 5, 2, 3

# 预置成员使用稳定的专家 ID，用户改名后仍能识别。
#   文献抽取：检索 → 分块 → 读图表 → 抽数
#   文件撰写：定大纲 → 写作 → 成篇 → 润色
FIXED_TEAMS = [
    ("文献抽取", ["builtin-searcher", "builtin-chunker", "builtin-chartread", "builtin-extractor"]),
    ("文件撰写", ["builtin-outline", "builtin-writer", "builtin-paperwriting", "builtin-polish"]),
]
_PLACEHOLDER = re.compile(r"^团队\s*\d+$")      # 早期播的空占位团队


def _team_row(tid: str) -> dict[str, Any]:
    with workflow.WorkflowDB(_db_path()) as db:
        return db.team(tid) or {}


def _seed_roster(tid: str) -> int:
    """Initialize a preset roster once, preserving later removals and renames."""
    row = _team_row(tid)
    if row.get("roster_initialized"):
        return 0
    name = row.get("name") or ""
    want = dict(FIXED_TEAMS).get(name, [])
    current = members(tid)
    if not want:
        leader_id = next((m.get("assistant_id") for m in current if m["role"] == "leader"), "")
        name = next((name for name, aid in FIXED_LEADERS.items() if aid == leader_id), "")
        want = dict(FIXED_TEAMS).get(name, [])
    with workflow.WorkflowDB(_db_path()) as db:
        db.db.execute("UPDATE teams SET roster_initialized=1 WHERE id=?", (tid,))
        db.db.commit()
        by_id = {a["id"]: a for a in db.assistants()}
    if not want or any(m["role"] == "member" for m in current):
        return 0
    n = 0
    for aid in want:
        a = by_id.get(aid)
        if not a:
            continue
        try:
            add_assistant(tid, a["id"])
            n += 1
        except Exception:
            pass
    return n


def ensure_defaults(project_id: str = "") -> list[dict[str, Any]]:
    """保证这个项目有两间常驻办公室（幂等）。

    老库里没绑项目的固定团队按顺序接手第一个打开的项目 —— 只改名字，成员和会话都留着；
    没有可接手的新建一套并播种成员。播种只在「一个成员都没有」时做，用户删掉的人不会被加回来。
    """
    pid = (project_id or "").strip()
    made: list[str] = []
    with workflow.WorkflowDB(_db_path()) as db:
        ts = db.teams()
        own = [x for x in ts if (x.get("project_id") or "") == pid]
        orphans = [x for x in ts if not (x.get("project_id") or "") and x["kind"] == "fixed"]
        for name, _roster in FIXED_TEAMS:
            if len([x for x in own if x["kind"] == "fixed"]) >= MAX_FIXED:
                break
            if any(x["name"] == name and x["kind"] == "fixed" for x in own):
                continue
            if orphans:
                o = orphans.pop(0)
                db.team_update(o["id"], project_id=pid, name=name)
                own.append({**o, "project_id": pid, "name": name})
            else:
                created = db.team_add(name, "fixed", project_id=pid)
                own.append(created)
                made.append(created["id"])
        for x in db.teams():                    # 剩下没人接手的空占位 → 转成已收工的加急，把常驻位还回来
            if not (x.get("project_id") or "") and x["kind"] == "fixed" and _PLACEHOLDER.match(x["name"] or "") \
               and not [m for m in db.team_members(x["id"]) if m["role"] == "member"]:
                db.team_update(x["id"], kind="temp", status="done")
        fixed_ids = [x["id"] for x in db.teams()
                     if (x.get("project_id") or "") == pid and x["kind"] == "fixed"]
    for tid in fixed_ids:                       # 播种放在 DB 关掉之后（add_assistant 自己开会话）
        ensure_leader(tid)
        _seed_roster(tid)
    with workflow.WorkflowDB(_db_path()) as db:
        return [x for x in db.teams() if (x.get("project_id") or "") == pid]


def teams(project_id: str | None = None) -> dict[str, Any]:
    """团队清单（带成员、成员各自的模型、Leader、最近 4 条流水）。给 project_id 就只看这个项目。"""
    pid = (project_id or "").strip()
    with workflow.WorkflowDB(_db_path()) as db:
        ts = db.teams()
        if pid:
            ts = [x for x in ts if (x.get("project_id") or "") == pid]
        avatars = {a["id"]: (a.get("avatar") or "") for a in db.assistants()}   # 成员专属 emoji
        out = []
        for x in ts:
            import team_runtime
            live = team_runtime.snapshot(db,x['id'])
            rows = live['members']
            for m in rows:
                m["avatar"] = avatars.get(m.get("assistant_id") or "", "")
                m["model"] = _member_model(db, m)
            ms = [m for m in rows if m["role"] == "member"]
            ev = [{"text": r["text"], "kind": r["kind"], "at": r["created_at"]}
                  for r in db.db.execute("SELECT text,kind,created_at FROM team_events"
                                         " WHERE task_id=? ORDER BY created_at DESC LIMIT 4", (x["id"],))]
            out.append({**x, "runtime_status":live['status'], "members": ms, "leader": next((m for m in rows if m["role"] == "leader"), None),
                        "picks": len(ms), "events":live['communications'][:8], "communications":live['communications']})
        return {"teams": out, "max": MAX_TEAMS, "max_fixed": MAX_FIXED, "max_temp": MAX_TEMP,
                "fixed": len([x for x in out if x["kind"] == "fixed"]),
                "temp": len([x for x in out if x["kind"] == "temp"])}


def new_team(kind: str = "temp", name: str = "", project_id: str = "", purpose: str = "") -> dict[str, Any]:
    """建团队。每个项目：常驻位 2 个（文献抽取 / 文件撰写，只改名不解散）、加急位 3 个
    （加急位满了会先把「已收工」的自动收掉，用完即释放）。"""
    pid = (project_id or "").strip()
    with workflow.WorkflowDB(_db_path()) as db:
        ts = [x for x in db.teams() if (x.get("project_id") or "") == pid]
        if kind == "fixed":
            if len([x for x in ts if x["kind"] == "fixed"]) >= MAX_FIXED:
                raise ValueError("常驻办公室只能有 %d 间" % MAX_FIXED)
        else:
            temps = [x for x in ts if x["kind"] == "temp"]
            if len(temps) >= MAX_TEMP:
                done = [x for x in temps if x["status"] == "done"]
                if not done:
                    raise ValueError("三个加急位都在用 —— 等它们跑完，或先散一间")
                db.team_delete(done[0]["id"])          # 用完的加急团队自动回收，腾出名额
                ts = [x for x in ts if x["id"] != done[0]["id"]]   # 名额按删后的算，不然下面那道兜底还会拦
        if len(ts) >= MAX_TEAMS:                       # 兜底（正常走不到：上面两种位各卡了一道）
            raise ValueError("最多 %d 间办公室（%d 常驻 + %d 加急）" % (MAX_TEAMS, MAX_FIXED, MAX_TEMP))
        n = len([x for x in ts if x["kind"] == kind]) + 1
        default = ("%s %d" % ("常驻" if kind == "fixed" else "加急", n))
        created = db.team_add((name or "").strip() or default, kind, project_id=pid, purpose=purpose)
        ensure_leader(created["id"], db)
        return created


def form_team(project_id: str, purpose: str, members: list[str]) -> dict[str, Any]:
    """智研管家组队：开一间加急办公室，把点到的专家按名字拉进来。"""
    pid = (project_id or "").strip()
    purpose = (purpose or "").strip()
    if not pid:
        raise ValueError("组队要指定项目")
    if not purpose:
        raise ValueError("先说清这支队为什么组、要交什么")
    names = [str(n).strip() for n in (members or []) if str(n).strip()]
    if not names:
        raise ValueError("一个人都没点，组什么队")
    t = new_team("temp", purpose[:24], pid, purpose)
    with workflow.WorkflowDB(_db_path()) as db:
        pool = {a["name"]: a for a in db.assistants() if a.get("enabled", 1)}
    added, missing = [], []
    for n in names:
        a = pool.get(n)
        if not a:
            missing.append(n)
            continue
        try:
            add_assistant(t["id"], a["id"])
            added.append(n)
        except Exception:
            missing.append(n)
    if not added:                                  # 一个人都没进来就别留空房间
        dissolve(t["id"])
        raise ValueError("这些专家都没找到：%s" % "、".join(missing))
    return {**(_team_row(t["id"]) or t), "added": added, "missing": missing}


def rename_team(tid: str, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("名字不能空")
    with workflow.WorkflowDB(_db_path()) as db:
        if not db.team(tid):
            raise ValueError("没有这个团队")
        return db.team_update(tid, name=name)


def dissolve(tid: str) -> dict[str, Any]:
    """解散团队。机动的可以；固定的不行（2 个固定位是常驻的）。记录留在库里不删。"""
    with workflow.WorkflowDB(_db_path()) as db:
        t = db.team(tid)
        if not t:
            raise ValueError("没有这个团队")
        if t["kind"] == "fixed":
            raise ValueError("固定团队不能解散 —— 只能改名字")
        db.team_delete(tid)
        return {"ok": True, "dissolved": t["name"]}


def release(tid: str) -> None:
    """机动团队「用完」：释放成员（把专家让出来）、标记收工；对话记录留着还能看。"""
    with workflow.WorkflowDB(_db_path()) as db:
        t = db.team(tid)
        if not t or t["kind"] != "temp":
            return
        for m in db.team_members(tid):
            if m["role"] == "member":
                db.team_member_remove(m["id"])
        db.team_update(tid, status="done")


def _member_model(db, m: dict[str, Any]) -> str:
    """这个成员背后接的是哪个模型（专家绑的那个）。"""
    aid = m.get("assistant_id") or ""
    if not aid and not m.get("model_id"):
        return ""
    a = db.assistant(aid) or {}
    mid = a.get("model_id") or ""
    if not mid and not m.get("model_id"):
        return ""
    try:
        prof = db.model(m.get("model_id") or mid) or {}
    except KeyError:
        return "模型已移除，请重新配置"
    return prof.get("name") or ""
