"""项目空间增强服务：聚合搜索与项目笔记选择/预览。"""
from __future__ import annotations

import re
from typing import Any


def _conn(db: Any):
    return db.db if hasattr(db, "db") else db


def search_project_space(db: Any, task_id: str, query: str) -> dict[str, Any]:
    """在当前项目空间内检索文献、笔记、证据片段。"""
    q = (query or "").strip().lower()
    if not task_id or not q:
        return {"q": query, "papers": [], "notes": [], "evidence": []}

    conn = _conn(db)

    # 1. 检索文献 (papers)
    paper_rows = db.papers_of(task_id) if hasattr(db, "papers_of") else []
    matched_papers = []
    for p in paper_rows:
        title = str(p.get("title") or p.get("filename") or "")
        authors = str(p.get("authors") or "")
        doi = str(p.get("doi") or "")
        journal = str(p.get("journal") or p.get("venue") or "")
        abstract = str(p.get("abstract") or "")
        text = f"{title} {authors} {doi} {journal} {abstract}".lower()
        if q in text:
            matched_papers.append({
                "id": p.get("id"),
                "title": title,
                "authors": authors,
                "year": p.get("year"),
                "doi": doi,
                "journal": journal,
                "snippet": abstract[:150] if abstract else title,
            })

    # 2. 检索笔记 (notes & md_notes)
    matched_notes = []
    # 2.1 从 md_notes 查
    try:
        md_rows = conn.execute(
            "SELECT id, title, content, updated_at FROM md_notes WHERE task_id=? ORDER BY updated_at DESC",
            (task_id,),
        ).fetchall()
        for nid, title, content, updated_at in md_rows:
            full_txt = f"{title or ''} {content or ''}".lower()
            if q in full_txt:
                idx = full_txt.find(q)
                start = max(0, idx - 40)
                snip = (content or "")[start:start + 120].strip()
                matched_notes.append({
                    "id": nid,
                    "kind": "md",
                    "title": title or "未命名笔记",
                    "snippet": snip,
                    "updated_at": updated_at,
                })
    except Exception:
        pass

    # 2.2 从 project notes 查
    try:
        p_notes = db.notes(task_id) if hasattr(db, "notes") else []
        for n in p_notes:
            t = str(n.get("title") or "")
            c = str(n.get("content") or "")
            if q in f"{t} {c}".lower():
                idx = f"{t} {c}".lower().find(q)
                start = max(0, idx - 40)
                snip = c[start:start + 120].strip()
                if not any(m["id"] == n.get("id") for m in matched_notes):
                    matched_notes.append({
                        "id": n.get("id"),
                        "kind": "proj",
                        "title": t or "项目笔记",
                        "snippet": snip,
                        "updated_at": n.get("created_at"),
                    })
    except Exception:
        pass

    # 3. 检索证据 (evidence)
    matched_evidence = []
    try:
        ev_rows = db.evidence(task_id) if hasattr(db, "evidence") else []
        for e in ev_rows:
            quote = str(e.get("quote") or "")
            claim = str(e.get("claim") or "")
            txt = f"{quote} {claim}".lower()
            if q in txt:
                matched_evidence.append({
                    "id": e.get("id"),
                    "paper_id": e.get("paper_id"),
                    "page": e.get("page"),
                    "claim": claim,
                    "quote": quote[:140],
                })
    except Exception:
        pass

    # 4. 检索待办 (todos)
    matched_todos = []
    try:
        todo_rows = conn.execute(
            "SELECT id, title, description, priority, status, stage, due_date FROM project_todos WHERE task_id=?",
            (task_id,),
        ).fetchall()
        for tid, title, desc, pri, st, stage, due in todo_rows:
            txt = f"{title or ''} {desc or ''} {stage or ''}".lower()
            if q in txt:
                matched_todos.append({
                    "id": tid,
                    "title": title or "未命名待办",
                    "priority": pri,
                    "status": st,
                    "stage": stage,
                    "due_date": due,
                })
    except Exception:
        pass

    papers = matched_papers[:30]
    notes = matched_notes[:30]
    evidence = matched_evidence[:30]
    todos = matched_todos[:30]
    total = len(papers) + len(notes) + len(evidence) + len(todos)

    return {
        "q": query,
        "total": total,
        "papers": papers,
        "notes": notes,
        "evidence": evidence,
        "todos": todos,
        "results": {
            "papers": papers,
            "notes": notes,
            "evidence": evidence,
            "todos": todos,
        },
    }


def list_project_notes(db: Any, task_id: str) -> list[dict[str, Any]]:
    """获取绑定到当前项目的所有 Markdown 笔记列表（用于项目空间笔记选择器）。"""
    if not task_id:
        return []
    conn = _conn(db)
    notes: list[dict[str, Any]] = []

    # 1. md_notes 绑定当前任务的笔记
    try:
        rows = conn.execute(
            "SELECT id, title, content, paper_id, created_at, updated_at FROM md_notes WHERE task_id=? ORDER BY updated_at DESC",
            (task_id,),
        ).fetchall()
        for nid, title, content, paper_id, created_at, updated_at in rows:
            notes.append({
                "id": nid,
                "title": title or "未命名笔记",
                "char_count": len(content or ""),
                "snippet": (content or "")[:120].strip(),
                "paper_id": paper_id or "",
                "created_at": created_at,
                "updated_at": updated_at,
                "source": "md_note",
            })
    except Exception:
        pass

    # 2. 传统 notes 表中的笔记
    try:
        p_notes = db.notes(task_id) if hasattr(db, "notes") else []
        for n in p_notes:
            nid = n.get("id")
            if any(it["id"] == nid for it in notes):
                continue
            cnt = n.get("content") or ""
            notes.append({
                "id": nid,
                "title": n.get("title") or "项目笔记",
                "char_count": len(cnt),
                "snippet": cnt[:120].strip(),
                "paper_id": n.get("paper_id") or "",
                "created_at": n.get("created_at"),
                "updated_at": n.get("created_at"),
                "source": "task_note",
            })
    except Exception:
        pass

    return notes


def get_project_note_detail(db: Any, note_id: str) -> dict[str, Any]:
    """获取单篇笔记详情与 Markdown 内容（用于实时预览与编辑）。"""
    conn = _conn(db)
    # 先查 md_notes
    try:
        row = conn.execute(
            "SELECT id, title, content, task_id, paper_id, created_at, updated_at FROM md_notes WHERE id=?",
            (note_id,),
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "title": row[1] or "未命名笔记",
                "content": row[2] or "",
                "task_id": row[3] or "",
                "paper_id": row[4] or "",
                "created_at": row[5],
                "updated_at": row[6],
                "source": "md_note",
            }
    except Exception:
        pass

    # 再查 notes
    try:
        row = conn.execute("SELECT id, task_id, title, content, created_at FROM notes WHERE id=?", (note_id,)).fetchone()
        if row:
            return {
                "id": row[0],
                "title": row[2] or "项目笔记",
                "content": row[3] or "",
                "task_id": row[1] or "",
                "paper_id": "",
                "created_at": row[4],
                "updated_at": row[4],
                "source": "task_note",
            }
    except Exception:
        pass

    raise KeyError(f"Note not found: {note_id}")
