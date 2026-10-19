"""项目空间增强服务：聚合搜索与项目笔记选择/预览。"""
from __future__ import annotations

from typing import Any
import json

import asset_service


def _conn(db: Any):
    return db.db if hasattr(db, "db") else db


def search_project_space(db: Any, task_id: str, query: str) -> dict[str, Any]:
    """Search the canonical asset graph, then fall back to legacy tables."""
    q = (query or "").strip().lower()
    if not task_id or not q:
        return {"q": query, "papers": [], "notes": [], "evidence": []}

    conn = _conn(db)
    asset_hits = asset_service.search(db, query, project_id=task_id, limit=90)

    def metadata(item: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(item.get("metadata") or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def field(row: Any, name: str, default: Any = "") -> Any:
        if isinstance(row, dict):
            return row.get(name, default)
        try:
            return row[name]
        except (IndexError, KeyError, TypeError):
            return default

    matched_papers: list[dict[str, Any]] = []
    matched_notes: list[dict[str, Any]] = []
    matched_evidence: list[dict[str, Any]] = []
    for item in asset_hits:
        meta = metadata(item)
        if item.get("kind") == "paper":
            matched_papers.append({
                "id": meta.get("paper_id") or meta.get("legacy_id") or item["id"],
                "asset_id": item["id"], "title": item.get("title") or "未命名文献",
                "authors": meta.get("authors", ""), "year": meta.get("year", ""),
                "doi": meta.get("doi", ""), "journal": meta.get("journal", ""),
                "snippet": item.get("snippet") or item.get("title", ""),
            })
        elif item.get("kind") == "evidence":
            matched_evidence.append({
                "id": meta.get("evidence_id") or item["id"], "asset_id": item["id"],
                "paper_id": meta.get("paper_id", ""),
                "page": meta.get("page_no", meta.get("page", "")),
                "claim": item.get("title", ""), "quote": (item.get("snippet") or "")[:140],
            })
        elif item.get("kind") in {"note", "document", "memory"}:
            matched_notes.append({
                "id": meta.get("legacy_id") or item["id"], "asset_id": item["id"],
                "kind": item.get("kind", "note"), "title": item.get("title") or "项目笔记",
                "snippet": item.get("snippet") or "", "updated_at": item.get("updated_at"),
            })

    paper_ids = {str(item.get("id")) for item in matched_papers}
    paper_rows = db.papers_of(task_id) if hasattr(db, "papers_of") else []
    for p in paper_rows:
        title = str(p.get("title") or p.get("filename") or "")
        authors = str(p.get("authors") or "")
        doi = str(p.get("doi") or "")
        journal = str(p.get("journal") or p.get("venue") or "")
        abstract = str(p.get("abstract") or "")
        text = f"{title} {authors} {doi} {journal} {abstract}".lower()
        if q in text and str(p.get("id")) not in paper_ids:
            matched_papers.append({"id": p.get("id"), "title": title, "authors": authors,
                                   "year": p.get("year"), "doi": doi, "journal": journal,
                                   "snippet": abstract[:150] if abstract else title})

    note_ids = {str(item.get("id")) for item in matched_notes}
    try:
        rows = conn.execute(
            "SELECT id, title, content, updated_at FROM md_notes WHERE task_id=? ORDER BY updated_at DESC",
            (task_id,),
        ).fetchall()
        for nid, title, content, updated_at in rows:
            full = f"{title or ''} {content or ''}".lower()
            if q not in full or str(nid) in note_ids:
                continue
            idx = full.find(q)
            matched_notes.append({"id": nid, "kind": "md", "title": title or "未命名文献",
                                  "snippet": (content or "")[max(0, idx - 40):idx + 80].strip(),
                                  "updated_at": updated_at})
            note_ids.add(str(nid))
    except Exception:
        pass

    try:
        for n in (db.notes(task_id) if hasattr(db, "notes") else []):
            title, content = str(n.get("title") or ""), str(n.get("content") or n.get("markdown") or "")
            if q not in f"{title} {content}".lower() or str(n.get("id")) in note_ids:
                continue
            idx = f"{title} {content}".lower().find(q)
            matched_notes.append({"id": n.get("id"), "kind": "proj", "title": title or "项目笔记",
                                  "snippet": content[max(0, idx - 40):idx + 80].strip(),
                                  "updated_at": n.get("created_at")})
            note_ids.add(str(n.get("id")))
    except Exception:
        pass

    evidence_ids = {str(item.get("id")) for item in matched_evidence}
    try:
        for e in (db.evidence(task_id) if hasattr(db, "evidence") else []):
            quote = str(field(e, "quote") or field(e, "excerpt") or "")
            claim = str(field(e, "claim") or field(e, "value") or "")
            eid = field(e, "id")
            if q not in f"{quote} {claim}".lower() or str(eid) in evidence_ids:
                continue
            matched_evidence.append({"id": eid, "paper_id": field(e, "paper_id"),
                                     "page": field(e, "page", field(e, "page_no")), "claim": claim,
                                     "quote": quote[:140]})
            evidence_ids.add(str(eid))
    except Exception:
        pass

    matched_todos = []
    try:
        rows = conn.execute(
            "SELECT id, title, description, priority, status, stage, due_date FROM project_todos WHERE task_id=?",
            (task_id,),
        ).fetchall()
        for tid, title, desc, pri, status, stage, due in rows:
            if q in f"{title or ''} {desc or ''} {stage or ''}".lower():
                matched_todos.append({"id": tid, "title": title or "未命名文献", "priority": pri,
                                      "status": status, "stage": stage, "due_date": due})
    except Exception:
        pass

    papers, notes, evidence, todos = (matched_papers[:30], matched_notes[:30],
                                      matched_evidence[:30], matched_todos[:30])
    return {"q": query, "total": len(papers) + len(notes) + len(evidence) + len(todos),
            "assets": asset_hits, "papers": papers, "notes": notes, "evidence": evidence,
            "todos": todos, "results": {"papers": papers, "notes": notes,
                                          "evidence": evidence, "todos": todos}}

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
