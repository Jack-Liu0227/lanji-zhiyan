"""项目级任务与待办管理服务 (Project TODO Service)。"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

PRIORITIES = ("P0", "P1", "P2")
STATUSES = ("TODO", "IN_PROGRESS", "REVIEW", "DONE")


def _conn(db: Any) -> sqlite3.Connection:
    return db.db if hasattr(db, "db") else db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_table(db: Any) -> None:
    conn = _conn(db)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS project_todos (
      id TEXT PRIMARY KEY,
      task_id TEXT NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'TODO',
      priority TEXT NOT NULL DEFAULT 'P1',
      assigned_assistant TEXT NOT NULL DEFAULT '',
      due_date TEXT NOT NULL DEFAULT '',
      stage TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_todos_task_id ON project_todos(task_id, status);")
    # 迁移检查：如果旧表没有 stage 字段，平滑添加
    cols = [col[1] for col in conn.execute("PRAGMA table_info(project_todos)").fetchall()]
    if "stage" not in cols:
        try:
            conn.execute("ALTER TABLE project_todos ADD COLUMN stage TEXT NOT NULL DEFAULT '';")
        except Exception:
            pass
    conn.commit()


def list_todos(db: Any, task_id: str = "") -> list[dict[str, Any]]:
    ensure_table(db)
    conn = _conn(db)
    order_sql = "ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 1 WHEN 'TODO' THEN 2 WHEN 'REVIEW' THEN 3 ELSE 4 END, priority, updated_at DESC"
    if task_id:
        rows = conn.execute(
            f"SELECT * FROM project_todos WHERE task_id=? {order_sql}",
            (task_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM project_todos {order_sql}"
        ).fetchall()
    cols = [col[1] for col in conn.execute("PRAGMA table_info(project_todos)").fetchall()]
    return [dict(zip(cols, row)) for row in rows]


def add_todo(
    db: Any,
    task_id: str,
    title: str,
    description: str = "",
    priority: str = "P1",
    assigned_assistant: str = "",
    due_date: str = "",
    stage: str = "",
) -> dict[str, Any]:
    ensure_table(db)
    title = (title or "").strip()
    if not title:
        raise ValueError("任务标题不能为空")
    if priority not in PRIORITIES:
        priority = "P1"
    todo_id = f"td_{uuid.uuid4().hex[:12]}"
    now = _now()
    conn = _conn(db)
    conn.execute(
        """
        INSERT INTO project_todos (id, task_id, title, description, status, priority, assigned_assistant, due_date, stage, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'TODO', ?, ?, ?, ?, ?, ?)
        """,
        (todo_id, task_id or "", title, description or "", priority, assigned_assistant or "", due_date or "", stage or "", now, now),
    )
    conn.commit()
    return {
        "id": todo_id,
        "task_id": task_id or "",
        "title": title,
        "description": description or "",
        "status": "TODO",
        "priority": priority,
        "assigned_assistant": assigned_assistant or "",
        "due_date": due_date or "",
        "stage": stage or "",
        "created_at": now,
        "updated_at": now,
    }


def update_todo(db: Any, todo_id: str, **kwargs: Any) -> dict[str, Any]:
    ensure_table(db)
    conn = _conn(db)
    current = conn.execute("SELECT * FROM project_todos WHERE id=?", (todo_id,)).fetchone()
    if not current:
        raise ValueError("待办任务不存在")
    cols = [col[1] for col in conn.execute("PRAGMA table_info(project_todos)").fetchall()]
    cur_dict = dict(zip(cols, current))

    allowed = {"title", "description", "status", "priority", "assigned_assistant", "due_date", "stage"}
    updates = {}
    for k, v in kwargs.items():
        if k in allowed:
            if k == "status" and v not in STATUSES:
                continue
            if k == "priority" and v not in PRIORITIES:
                continue
            updates[k] = v

    if not updates:
        return cur_dict

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [todo_id]
    conn.execute(f"UPDATE project_todos SET {set_clause} WHERE id=?", values)
    conn.commit()
    cur_dict.update(updates)
    return cur_dict


def toggle_todo(db: Any, todo_id: str) -> dict[str, Any]:
    ensure_table(db)
    conn = _conn(db)
    row = conn.execute("SELECT status FROM project_todos WHERE id=?", (todo_id,)).fetchone()
    if not row:
        raise ValueError("待办任务不存在")
    cur_status = row[0]
    next_status = "DONE" if cur_status != "DONE" else "TODO"
    return update_todo(db, todo_id, status=next_status)


def delete_todo(db: Any, todo_id: str) -> dict[str, Any]:
    ensure_table(db)
    conn = _conn(db)
    conn.execute("DELETE FROM project_todos WHERE id=?", (todo_id,))
    conn.commit()
    return {"ok": True, "id": todo_id}


def import_workflow_stages_as_todos(db: Any, task_id: str) -> list[dict[str, Any]]:
    """将科研工作流阶段一键拆解为项目待办。"""
    stages = [
        ("01. 创新点审查与防御设计", "对研究假设和创新点进行 F1-F10 审查"),
        ("02. 三源文献精准检索", "针对研究问题检索 OpenAlex/Crossref/arXiv 论文"),
        ("03. 开放获取全文下载", "下载 OA 全文并建立证据提取管道"),
        ("04. 论文双语精读研读", "完成核心论文的中英精读与重点段落批注"),
        ("05. 论文大纲架构确认", "确立论文章节骨架与各段落论据支撑"),
        ("06. 知识与实验谱系梳理", "建立章节-文献-实验数据的证据拓扑图"),
        ("07. 论文正文模块化撰写", "按框架分节起草论文各核心段落"),
        ("08. 审稿人视角严格预审", "以 Reviewer 1/2/3 视角全面排查技术漏洞"),
        ("09. 学术英语精修与中译英", "按照顶刊风格进行句式润色与语言校对"),
        ("10. 格式转换与编译预览", "多格式编译并生成最终投稿预览稿"),
    ]
    created = []
    for title, desc in stages:
        created.append(add_todo(db, task_id, title=title, description=desc, priority="P1"))
    return created


def kanban_overview(db: Any, task_id: str = "") -> dict[str, Any]:
    ensure_table(db)
    conn = _conn(db)

    # 1. 确定当前目标任务
    all_tasks = db.tasks() if hasattr(db, "tasks") else []
    cur_t = None
    if task_id and task_id != "all":
        cur_t = next((t for t in all_tasks if (t.get("id") if isinstance(t, dict) else t["id"]) == task_id), None)
    elif not task_id and all_tasks:
        cur_t = all_tasks[0]
        task_id = cur_t.get("id") if isinstance(cur_t, dict) else cur_t["id"]

    # 2. 取待办列表 (如果 task_id == "all" 则取全部)
    query_task_id = "" if task_id == "all" else task_id
    todos = list_todos(db, query_task_id)

    # 3. 统计状态
    stats = {
        "total": len(todos),
        "todo": sum(1 for t in todos if t.get("status") == "TODO"),
        "in_progress": sum(1 for t in todos if t.get("status") == "IN_PROGRESS"),
        "review": sum(1 for t in todos if t.get("status") == "REVIEW"),
        "done": sum(1 for t in todos if t.get("status") == "DONE"),
        "p0_count": sum(1 for t in todos if t.get("priority") == "P0" and t.get("status") != "DONE"),
    }
    stats["completion_rate"] = round((stats["done"] / stats["total"] * 100) if stats["total"] > 0 else 0, 1)

    # 4. 科研阶段概览 (10 步) —— 用真正在记的那张 steps 表（stage/status）。
    # 原来查的 workflow_steps / workflow_results 这两张表从来没建过，查询一直静默失败，
    # 所以看板永远显示「第一步」；research_tasks.stage 存的是 definition/reading 这类旧口径，
    # 跟 STAGES 对不上，也不能拿来当当前步骤。
    from workflow import STAGES, STAGE_LABEL
    stage_list = []
    done_keys, running_keys = set(), []
    if task_id and task_id != "all":
        try:
            for row in conn.execute(
                    "SELECT stage, status FROM steps WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall():
                key = row["stage"]
                if key not in STAGES:
                    continue                       # agent 之类不属于十步
                if row["status"] == "done":
                    done_keys.add(key)
                elif row["status"] == "running":
                    running_keys.append(key)
        except Exception:
            pass

    # 当前阶段：正在跑且还没完成的那一步 > 第一个还没完成的 > 最后一步（十步都做完了）。
    # 「还在跑但后面已经记了 done」的行是崩溃/中断留下的残迹，不能当当前阶段。
    pending_running = [k for k in running_keys if k not in done_keys]
    cur_stage_key = (pending_running[-1] if pending_running else
                     next((s for s in STAGES if s not in done_keys), STAGES[-1]))

    for i, s_key in enumerate(STAGES):
        stage_list.append({
            "key": s_key,
            "label": STAGE_LABEL.get(s_key, s_key),
            "index": i + 1,
            "done": s_key in done_keys,
            "current": s_key == cur_stage_key and s_key not in done_keys,
        })

    # 5. 课题关联指标
    papers_count = 0
    notes_count = 0
    if task_id:
        try:
            papers_count = conn.execute("SELECT count(*) FROM papers WHERE task_id=?", (task_id,)).fetchone()[0]
        except Exception:
            papers_count = 0
        try:
            notes_count = conn.execute("SELECT count(*) FROM notes WHERE task_id=?", (task_id,)).fetchone()[0]
        except Exception:
            notes_count = 0

    stats["papers_count"] = papers_count
    stats["notes_count"] = notes_count
    # 项目进度口径 = 十步里做完几步（待办是用户手加的清单，空着不代表没进度）
    stats["stages_done"] = len(done_keys)
    stats["stage_total"] = len(STAGES)
    stats["stage_progress"] = round(len(done_keys) / len(STAGES) * 100, 1) if STAGES else 0

    # 6. 专家/助手列表
    assistants = db.assistants() if hasattr(db, "assistants") else []

    return {
        "task": dict(cur_t) if cur_t else None,
        "tasks": [dict(t) for t in all_tasks],
        "stats": stats,
        "stages": stage_list,
        "current_stage": {"key": cur_stage_key, "label": STAGE_LABEL.get(cur_stage_key, cur_stage_key)},
        "assistants": assistants,
        "todos": todos,
    }
