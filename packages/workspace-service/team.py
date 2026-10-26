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
    ensure_leader(task_id)
    with workflow.WorkflowDB(_db_path()) as db:
        return db.team_members(task_id)


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


def assign(task_id: str, member: str, prompt: str, title: str = "", trigger: str = "leader",
           origin: str = "", depends_on: Any = None, output_contract: dict[str, Any] | None = None,
           reviewer_id: str = "", todo_id: str = "", db: Any = None) -> dict[str, Any]:
    """把一件事派给某个成员 → 落一条 queued 任务，等队列 worker 串行跑。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("要派的事不能为空")
    with (nullcontext(db) if db is not None else workflow.WorkflowDB(_db_path())) as db:
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
        project_id = (db.team(task_id) or {}).get("project_id") or task_id
        snapshot = prompt_templates.resolve(db, project_id, step=hit.get("step", ""),
                                            member_id=hit["id"], assistant_id=hit.get("assistant_id", "")) if db.base_dir(project_id) else None
        with db.transaction():
            db.db.execute("BEGIN IMMEDIATE")
            row = db.team_task_add(task_id, hit["id"], hit["name"], title or prompt[:40], prompt,
                                   trigger, origin, deps, contract, reviewer, commit=False)
            db.db.execute("UPDATE team_tasks SET checkpoint=? WHERE id=?",
                          (json.dumps({"queued_template_snapshot": snapshot}, ensure_ascii=False), row["id"]))
            if todo_id:
                import project_todos
                project_todos.bind_execution(db, todo_id, task_id, team_task_id=row["id"], commit=False)
        db.team_event_add(row["id"], task_id, "assign", f"{trigger} 派给「{hit['name']}」：{prompt[:160]}")
        db.team_member_update(hit["id"], status="queued")
        return row


def board(task_id: str, limit: int = 40) -> dict[str, Any]:
    """看板数据：成员（含状态）+ 任务列表 + 事件流（对话状态与执行情况）。"""
    ensure_leader(task_id)
    with workflow.WorkflowDB(_db_path()) as db:
        mem = db.team_members(task_id)
        tasks = db.team_tasks(task_id, limit)
        events = db.team_events(task_id, 200)
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


def cancel(task_id_row: str) -> dict[str, Any]:
    with _lock, workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        if str(row.get("origin") or "").startswith("workflow:"):
            raise ValueError("此任务由工作流管理，请在工作流中审查并继续后续节点")
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


def retry(task_id_row: str) -> dict[str, Any]:
    with _lock, workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        if str(row.get("origin") or "").startswith("workflow:"):
            raise ValueError("此任务由工作流管理，请在工作流中重试当前节点")
        signal = _cancel_signals.get(task_id_row)
        if row["status"] == "running" or (signal and not signal.is_set()):
            raise ValueError("还在跑")
        if task_id_row in _cancel_signals:
            _pending_retries.add(task_id_row)
        db.team_task_mark(task_id_row, "queued", started_at="", ended_at="", reason="", result="", checkpoint=_template_checkpoint(row))
        db.team_event_add(task_id_row, row["task_id"], "note", "重新排队")
        return {"queued": task_id_row}


def review(task_id_row: str, approved: bool, reviewer_id: str = "", feedback: str = "") -> dict[str, Any]:
    """Approve a gated team output or return it to the worker with feedback."""
    with workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
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
            member = db.team_member(row["member_id"]) or {}
            if member.get("step"):
                db.log_step(row["task_id"], member["step"], "cancelled" if signal.is_set() else "failed", str(exc))
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
    import execution
    t_id = row["task_id"]
    attempt = int(row.get("attempt") or 0) + 1
    team_run_id = ""
    with workflow.WorkflowDB(_db_path()) as db:
        import asset_service
        import run_service
        if (db.team_task(row["id"]) or {}).get("status") == "canceled":
            return
        tracked = run_service.start(db, t_id, "team_task",
                                    idempotency_key=f"team-task:{row['id']}:{attempt}")
        team_run_id = tracked["id"]
        input_assets = [item["id"] for item in asset_service.research_assets(db, t_id, limit=40)]
        for dependency in _task_ids(row.get("depends_on")):
            upstream = db.team_task(dependency) or {}
            input_assets.extend(json.loads(upstream.get("output_assets") or "[]"))
        input_assets = list(dict.fromkeys(input_assets))
        input_assets = [key for key in input_assets if (asset_service.get(db, key) and not asset_service.is_instruction_asset(asset_service.get(db, key)))]
        checkpoint = json.loads(row.get("checkpoint") or "{}")
        db.team_task_mark(row["id"], "running", started_at=_now(), attempt=attempt,
                          checkpoint=checkpoint or {"phase": "started", "attempt": attempt}, run_id=team_run_id,
                          input_assets=input_assets)
        db.team_member_update(row["member_id"], status="running")
        db.team_event_add(row["id"], t_id, "start", "开始执行")
        mem = db.team_member(row["member_id"]) or {}
        brief = (mem.get("prompt") or "").strip()
        step = mem.get("step") or ""
        if step:
            db.log_step(t_id, step, "running", "团队任务开始执行")
        role = ("你是《%s》项目的 Partner Agent「%s」" % (db.summary(t_id)["task"]["title"], row["member_name"]))
        if step:
            role += "，负责工作流里的「%s」环节" % step
        extra = role + "。只在这个项目范围内工作。\n" + (brief + "\n" if brief else "")
        contract = row.get("output_contract") or "{}"
        try:
            contract = json.loads(contract) if isinstance(contract, str) else contract
        except (TypeError, ValueError):
            contract = {}
        if contract:
            extra += "交付契约（必须覆盖其中要求，无法满足的项目要明确标注）：\n"
            extra += json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
        for aid in input_assets:
            asset = asset_service.get(db, aid)
            if asset and asset["project_id"] == t_id and not asset_service.is_instruction_asset(asset):
                path = Path(asset["canonical_uri"])
                if path.suffix.lower() in (".md", ".txt") and path.is_file():
                    extra += f"\n【输入产物 {aid}：{asset['title']}】\n" + path.read_text(encoding="utf-8")[:6000]
    outs: list[str] = []

    active_db: list[Any] = []

    def emit(ev: dict[str, Any]) -> None:
        t = ev.get("type")
        c_db = active_db[0] if active_db else None
        if t == "tool_call":
            _ev(row, "tool", "调用 %s" % ev.get("name"), db=c_db)
        elif t == "tool_result":
            _ev(row, "tool", ("%s：%s" % (ev.get("name"), ev.get("summary"))) if ev.get("ok") else ("失败：" + str(ev.get("summary"))), db=c_db)
        elif t == "assistant" and ev.get("content"):
            outs.append(ev["content"])
            _ev(row, "assistant", str(ev["content"])[:400], db=c_db)
        elif t == "usage":
            _ev(row, "note", "tokens %s" % ev.get("total"), db=c_db)

    try:
        with workflow.WorkflowDB(_db_path()) as db:
            active_db.append(db)
            db.job_checkpoint = checkpoint
            if "queued_template_snapshot" in checkpoint:
                db.template_snapshot = checkpoint["queued_template_snapshot"]
                db.template_resolved = True
            db.resuming = bool(checkpoint.get("messages") or checkpoint.get("completed_calls"))
            def save_checkpoint(**values):
                checkpoint.update(values)
                db.db.execute("UPDATE team_tasks SET checkpoint=? WHERE id=?",
                              (json.dumps(checkpoint, ensure_ascii=False), row["id"]))
                db.db.commit()
            db.save_agent_checkpoint = save_checkpoint
            try:
                out = workflow.run_agent(db, t_id, row["prompt"], emit=emit, publish=False,
                                         extra_context=extra, max_steps=8, assistant_id=mem.get("assistant_id", ""), member=mem,
                                         scope=step, cancel_check=signal.is_set)
            finally:
                active_db.clear()
        text = (out.get("reply") or "\n".join(outs) or "").strip()
        execution.check()
        if not text:
            raise ValueError("团队任务未产出内容")
        missing = [str(s) for s in contract.get("sections", []) if str(s).casefold() not in text.casefold()]
        if missing or len(text) < int(contract.get("min_chars") or 1):
            raise ValueError("产物不满足交付契约：" + ("缺少 " + "、".join(missing) if missing else "内容长度不足"))
        with workflow.WorkflowDB(_db_path()) as db:
            import asset_service
            import run_service
            folder = db.pdir(t_id, "其余") / "团队"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ("%s-%s.md" % ((row["member_name"] or "member").replace("/", "_"),
                                           datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + row["id"][:8]))
            content = "> 团队任务「%s」 · 成员 %s · %s\n\n%s\n" % (
                row["title"], row["member_name"], _now(), text or "没有产出内容")
            asset_service.atomic_write_text(path, content)
            output = asset_service.register_file(
                db, path, project_id=t_id, kind="team_output", title=row["title"],
                source="team_worker", run_id=team_run_id, body=content,
                metadata={"team_task_id": row["id"], "member_id": row["member_id"]})
            if out.get("template_snapshot"):
                used = out["template_snapshot"]
                asset_service.link(db, output["id"], "used_template", used["asset_id"], {"revision_id": used["revision_id"]})
            input_ids = input_assets
            for input_id in input_ids:
                try:
                    asset_service.link(db, output["id"], "derived_from", input_id,
                                       {"team_task_id": row["id"]})
                except (KeyError, ValueError):
                    pass
            asset_service.provenance(
                db, output["id"], team_run_id, "team_task_complete",
                actor=row["member_name"],
                input_snapshot={"task_id": row["id"], "inputs": input_ids})
            needs_review = bool(row.get("reviewer_id"))
            execution.check()
            db.team_task_mark(row["id"], "review" if needs_review else "done", ended_at=_now(),
                              output_path=str(path), result=text[:500],
                              checkpoint={**_template_checkpoint({"checkpoint": checkpoint}), "phase": "review" if needs_review else "done", "attempt": attempt},
                              output_assets=list(dict.fromkeys([*(out.get("output_assets") or []), output["id"]])),
                              review_status="pending" if needs_review else "not_required")
            run_service.finish(db, team_run_id, "succeeded")
            if step:
                db.log_step(t_id, step, "succeeded", "团队产物已生成，等待交付验收")
            db.team_member_update(row["member_id"], status="review" if needs_review else "done",
                                  note=("等待验收：" if needs_review else "最近完成：") + row["title"])
            db.team_event_add(row["id"], t_id, "review" if needs_review else "done",
                              "完成 → %s%s" % (path.name, "（等待 Reviewer）" if needs_review else ""))
    except Exception as exc:
        with workflow.WorkflowDB(_db_path()) as db:
            if not db.team_task(row["id"]):
                return
            cancelled = isinstance(exc, execution.Cancelled) or signal.is_set()
            if team_run_id:
                import run_service
                run_service.finish(db, team_run_id, "cancelled" if cancelled else "failed", error=str(exc))
            db.team_task_mark(row["id"], "canceled" if cancelled else "failed", ended_at=_now(),
                              reason="%s: %s" % (type(exc).__name__, exc),
                              checkpoint={**checkpoint, "phase": "failed", "attempt": attempt},
                              retry_at="")
            if step:
                db.log_step(t_id, step, "cancelled" if cancelled else "failed", str(exc))
            db.team_member_update(row["member_id"], status="idle" if cancelled else "failed")
            db.team_event_add(row["id"], t_id, "fail", "%s: %s" % (type(exc).__name__, exc))


def _ev(row: dict[str, Any], kind: str, text: str, db: Any = None) -> None:
    try:
        if db is not None:
            db.team_event_add(row["id"], row["task_id"], kind, text)
        else:
            with workflow.WorkflowDB(_db_path()) as conn:
                conn.team_event_add(row["id"], row["task_id"], kind, text)
    except Exception:
        pass


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


# ============================ v2：成员=助手 + 团队内 AI 聊天平台 ============================

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
    """团队里被选中的助手成员（不含 Leader）。"""
    return [m for m in members(scope) if m.get("role") == "member"]


def add_assistant(scope: str, assistant_id: str) -> dict[str, Any]:
    """把一个助手拉进团队（团队成员就是助手实例）。最多 MAX_PICKS 个。"""
    with workflow.WorkflowDB(_db_path()) as db:
        asst = db.assistant(assistant_id)
        if not asst:
            raise ValueError("没有这个助手 —— 先去「助手」页建一个")
        asst = db.require_assistant(assistant_id)
        cur = db.team_members(scope)
        if any(m["assistant_id"] == assistant_id for m in cur):
            raise ValueError("「%s」已经在团队里了" % asst["name"])
        return db.team_member_add(scope, asst["name"], "member", "", asst["rules"] or "",
                                  assistant_id=assistant_id, color=1 + len([m for m in cur if m["role"] == "member"]))


def _history_scope(db, scope):
    import agent_settings
    return "team:" + agent_settings.session(db, scope)["id"]


def transcript(scope: str, limit: int | None = None) -> list[dict[str, Any]]:
    """团队群里的对话（谁说的都记在 sender 上）。"""
    with workflow.WorkflowDB(_db_path()) as db:
        rows = [{"role": m["role"], "content": m["content"],
                 "sender": m.get("sender") or ("你" if m["role"] == "user" else "成员"),
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
    return {"ok": True}


def _round(scope: str, turns: int) -> None:
    """一个 AI 聊天平台：成员之间互相 @ 派话，一轮接一轮把活推下去（串行，一轮一个人）。"""
    import workflow as _wf
    try:
        with workflow.WorkflowDB(_db_path()) as db:
            history_scope = _history_scope(db, scope)
            ms = [m for m in db.team_members(scope) if m["role"] == "member" or m.get("assistant_id") or m.get("model_id")
                  or m["name"] != LEADER_NAME or m["prompt"] != LEADER_BRIEF]
            names = [m["name"] for m in ms]
            by_name = {m["name"]: m for m in ms}
            if not names:
                db.add_message(scope, "assistant", "团队里还没有人 —— 先从左边的助手里选几个。",
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
                prof = None                                  # 这个成员背后接的模型（= 它那个助手绑的模型）
                extra = (
                    "【团队协作】你现在的身份是%s「%s」（不是首页全局助手）：%s\n"
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
                db.add_message(scope, "assistant", body, scope=history_scope, sender=speaker)
                last = speaker
                if next_speaker(body, names, last) is None:
                    db.add_message(scope, "assistant", "团队：都齐了，收工。", scope=history_scope, sender="系统")
                    return
            _set(scope, phase="轮次用完（%d 轮）" % turns)
    finally:
        release(scope)                     # 机动团队：跑完就把成员释放掉、标记收工（名额还给别人）
        with _LOCK:
            TEAM_RUNS.pop(scope, None)


def chat_state(scope: str) -> dict[str, Any]:
    """团队页要的一切：这个团队 + 成员（带色 + 各自模型）+ 群记录 + 正在跑的状态 + 会话历史 + 上限。"""
    ensure_leader(scope)
    with workflow.WorkflowDB(_db_path()) as db:
        import agent_settings
        cur_session = agent_settings.session(db, scope)
        sessions = [dict(r) for r in db.db.execute("SELECT * FROM team_sessions WHERE team_id=? ORDER BY updated_at DESC", (scope,))]
        ms = members(scope)
        for m in ms:
            m["model"] = _member_model(db, m)
        t_rec = db.team(scope)
    return {"team": t_rec, "members": ms, "max": MAX_PICKS, "palette": MEMBER_COLORS,
            "transcript": transcript(scope), "running": TEAM_RUNS.get(scope),
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
    if pid:
        ensure_defaults(pid)
    with workflow.WorkflowDB(_db_path()) as db:
        ts = db.teams()
        if pid:
            ts = [x for x in ts if (x.get("project_id") or "") == pid]
        avatars = {a["id"]: (a.get("avatar") or "") for a in db.assistants()}   # 成员专属 emoji
        out = []
        for x in ts:
            ensure_leader(x["id"], db)
            rows = db.team_members(x["id"])
            for m in rows:
                m["avatar"] = avatars.get(m.get("assistant_id") or "", "")
                m["model"] = _member_model(db, m)
            ms = [m for m in rows if m["role"] == "member"]
            ev = [{"text": r["text"], "kind": r["kind"], "at": r["created_at"]}
                  for r in db.db.execute("SELECT text,kind,created_at FROM team_events"
                                         " WHERE task_id=? ORDER BY created_at DESC LIMIT 4", (x["id"],))]
            out.append({**x, "members": ms, "leader": next((m for m in rows if m["role"] == "leader"), None),
                        "picks": len(ms), "events": ev})
        return {"teams": out, "max": MAX_TEAMS, "max_fixed": MAX_FIXED, "max_temp": MAX_TEMP,
                "fixed": len([x for x in out if x["kind"] == "fixed"]),
                "temp": len([x for x in out if x["kind"] == "temp"])}


def new_team(kind: str = "temp", name: str = "", project_id: str = "", purpose: str = "") -> dict[str, Any]:
    """建团队。每个项目：常驻位 2 个（文献抽取 / 文件撰写，只改名不解散）、加急位 3 个
    （加急位满了会先把「已收工」的自动收掉，用完即释放）。"""
    pid = (project_id or "").strip()
    with workflow.WorkflowDB(_db_path()) as db:
        ensure_defaults(pid)
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
        return db.team_add((name or "").strip() or default, kind, project_id=pid, purpose=purpose)


def form_team(project_id: str, purpose: str, members: list[str]) -> dict[str, Any]:
    """智研管家组队：开一间加急办公室，把点到的助手按名字拉进来。"""
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
        raise ValueError("这些助手都没找到：%s" % "、".join(missing))
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
    """机动团队「用完」：释放成员（把助手让出来）、标记收工；对话记录留着还能看。"""
    with workflow.WorkflowDB(_db_path()) as db:
        t = db.team(tid)
        if not t or t["kind"] != "temp":
            return
        for m in db.team_members(tid):
            if m["role"] == "member":
                db.team_member_remove(m["id"])
        db.team_update(tid, status="done")


def _member_model(db, m: dict[str, Any]) -> str:
    """这个成员背后接的是哪个模型（助手绑的那个）。"""
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
