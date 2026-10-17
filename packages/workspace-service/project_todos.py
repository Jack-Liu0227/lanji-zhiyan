"""项目级任务与待办管理服务 (Project TODO Service)。"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

PRIORITIES = ("P0", "P1", "P2")
STATUSES = ("TODO", "IN_PROGRESS", "DONE")


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
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_project_todos_task_id ON project_todos(task_id, status);")
    conn.commit()


def list_todos(db: Any, task_id: str = "") -> list[dict[str, Any]]:
    ensure_table(db)
    conn = _conn(db)
    if task_id:
        rows = conn.execute(
            "SELECT * FROM project_todos WHERE task_id=? ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 1 WHEN 'TODO' THEN 2 ELSE 3 END, priority, updated_at DESC",
            (task_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM project_todos ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 1 WHEN 'TODO' THEN 2 ELSE 3 END, priority, updated_at DESC"
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
        INSERT INTO project_todos (id, task_id, title, description, status, priority, assigned_assistant, due_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'TODO', ?, ?, ?, ?, ?)
        """,
        (todo_id, task_id or "", title, description or "", priority, assigned_assistant or "", due_date or "", now, now),
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

    allowed = {"title", "description", "status", "priority", "assigned_assistant", "due_date"}
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
