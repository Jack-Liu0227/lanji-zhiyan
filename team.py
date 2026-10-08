# -*- coding: utf-8 -*-
"""团队（A 档）：Leader + Partner 成员 + 派活队列 + 运行事件。

抄 AionUi 的 Teams 模型（`packages/desktop/src/common/types/team/teamTypes.ts`）：
  · 角色 `role = 'leader' | 'teammate'`；**Leader 恒为成员列表第一个、不可移除**（AionUi 原话）；
  · 成员状态 `pending | idle | active | completed | failed | dormant` → 这里落成 idle|queued|running|done|failed|blocked；
  · 任务看板（AionUi 的 Activity：TaskCard + MessageCard）→ 这里落成 team_tasks + team_events。

与原版的**硬差别（如实说明）**：AionUi 每个成员是独立 agent 进程、能真并行；本应用单进程、
模型调用阻塞 → 这里是**串行队列**（一次只跑一个成员），排队状态在界面上明确显示。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Any

import workflow

DB_PATH: Any = None          # 由 server.py 运行时注入（和 schedule.py 同一套路）

TICK = 3.0                   # 队列轮询间隔（秒）
RUNNING: set[str] = set()    # 正在跑的任务行 id（防重入）
_lock = threading.Lock()

STEPS = ["idea", "search", "download", "notes", "write", "review", "convert"]   # 可绑的工作流步骤
LEADER_NAME = "全局 Leader"
LEADER_BRIEF = "你好，我是 Leader，负责理解你的目标并协调团队。描述你想做的事，我来安排。"


def _db_path():
    if DB_PATH:
        return DB_PATH
    import server
    return server.DB_PATH


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------- 成员 ----------------
def ensure_leader(task_id: str) -> dict[str, Any]:
    """确保团队里有 leader 行（幂等）。AionUi：Leader 恒为列表第一个、不可移除。"""
    with workflow.WorkflowDB(_db_path()) as db:
        rows = [m for m in db.team_members(task_id) if m["role"] == "leader"]
        if rows:
            return rows[0]
        return db.team_member_add(task_id, LEADER_NAME, "leader", "", LEADER_BRIEF)


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
            raise ValueError("Leader 不能移除（AionUi 同款规则）")
        db.team_member_remove(member_id)
        return {"removed": member_id}


# ---------------- 派活 ----------------
def assign(task_id: str, member: str, prompt: str, title: str = "", trigger: str = "leader",
           origin: str = "") -> dict[str, Any]:
    """把一件事派给某个成员 → 落一条 queued 任务，等队列 worker 串行跑。"""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("要派的事不能为空")
    ensure_leader(task_id)
    with workflow.WorkflowDB(_db_path()) as db:
        rows = db.team_members(task_id)
        key = (member or "").strip().lower()
        hit = next((m for m in rows if m["id"] == member or m["name"].lower() == key), None)
        if not hit:
            names = "、".join(m["name"] for m in rows)
            raise ValueError(f"团队里没有成员「{member}」（现有：{names}）")
        row = db.team_task_add(task_id, hit["id"], hit["name"], title or prompt[:40], prompt, trigger, origin)
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
            "leader_brief": LEADER_BRIEF, "steps": STEPS,
            "serial_note": "单进程串行队列：一次只跑一个成员（AionUi 是真并行，那是独立 agent 进程换来的）",
            "running": sorted(RUNNING)}


def cancel(task_id_row: str) -> dict[str, Any]:
    with workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        if row["status"] == "running":
            raise ValueError("正在运行，停不下来（模型调用是阻塞的）—— 等它跑完再重试")
        db.team_task_mark(task_id_row, "canceled", ended_at=_now(), reason="手动取消")
        return {"canceled": task_id_row}


def retry(task_id_row: str) -> dict[str, Any]:
    with workflow.WorkflowDB(_db_path()) as db:
        row = db.team_task(task_id_row)
        if not row:
            raise ValueError("任务不存在")
        if row["status"] == "running":
            raise ValueError("还在跑")
        db.team_task_mark(task_id_row, "queued", started_at="", ended_at="", reason="", result="")
        db.team_event_add(task_id_row, row["task_id"], "note", "重新排队")
        return {"queued": task_id_row}


# ---------------- 队列 worker（串行） ----------------
def _run_one(row: dict[str, Any]) -> None:
    import team as _self                      # 便于测试替换 run_agent
    t_id = row["task_id"]
    with workflow.WorkflowDB(_db_path()) as db:
        db.team_task_mark(row["id"], "running", started_at=_now())
        db.team_member_update(row["member_id"], status="running")
        db.team_event_add(row["id"], t_id, "start", "开始执行")
        mem = db.team_member(row["member_id"]) or {}
        brief = (mem.get("prompt") or "").strip()
        step = mem.get("step") or ""
        role = ("你是《%s》项目的 Partner Agent「%s」" % (db.summary(t_id)["task"]["title"], row["member_name"]))
        if step:
            role += "，负责工作流里的「%s」环节" % step
        extra = role + "。只在这个项目范围内工作。\n" + (brief + "\n" if brief else "")
    outs: list[str] = []

    def emit(ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "tool_call":
            _ev(row, "tool", "调用 %s" % ev.get("name"))
        elif t == "tool_result":
            _ev(row, "tool", ("%s：%s" % (ev.get("name"), ev.get("summary"))) if ev.get("ok") else ("失败：" + str(ev.get("summary"))))
        elif t == "assistant" and ev.get("content"):
            outs.append(ev["content"])
            _ev(row, "assistant", str(ev["content"])[:400])
        elif t == "usage":
            _ev(row, "note", "tokens %s" % ev.get("total"))

    try:
        with workflow.WorkflowDB(_db_path()) as db:
            out = workflow.run_agent(db, t_id, row["prompt"], emit=emit, publish=False,
                                     extra_context=extra, max_steps=8)
        text = (out.get("reply") or "\n".join(outs) or "").strip()
        with workflow.WorkflowDB(_db_path()) as db:
            folder = db.pdir(t_id, "其余") / "团队"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ("%s-%s.md" % ((row["member_name"] or "member").replace("/", "_"),
                                           datetime.now().strftime("%Y%m%d-%H%M%S")))
            path.write_text("> 团队任务「%s」· 成员 %s · %s\n\n%s\n" %
                            (row["title"], row["member_name"], _now(), text or "（没有产出内容）"), encoding="utf-8")
            db.team_task_mark(row["id"], "done", ended_at=_now(),
                              output_path=str(path), result=text[:500])
            db.team_member_update(row["member_id"], status="done", note="最近完成：" + row["title"])
            db.team_event_add(row["id"], t_id, "done", "完成 → %s" % path.name)
    except Exception as exc:
        with workflow.WorkflowDB(_db_path()) as db:
            db.team_task_mark(row["id"], "failed", ended_at=_now(),
                              reason="%s: %s" % (type(exc).__name__, exc))
            db.team_member_update(row["member_id"], status="failed")
            db.team_event_add(row["id"], t_id, "fail", "%s: %s" % (type(exc).__name__, exc))


def _ev(row: dict[str, Any], kind: str, text: str) -> None:
    try:
        with workflow.WorkflowDB(_db_path()) as db:
            db.team_event_add(row["id"], row["task_id"], kind, text)
    except Exception:
        pass


def tick() -> int:
    """跑一轮队列（串行：一次一个）。返回本轮跑了几个。"""
    done = 0
    with workflow.WorkflowDB(_db_path()) as db:
        rows = [t for t in db.team_tasks(None, 200) if t["status"] == "queued"]
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
    threading.Thread(target=_loop, daemon=True).start()


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
