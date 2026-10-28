"""Shared project bibliography for workflow, Zotero sync and project space."""
from __future__ import annotations

from pathlib import Path
import re

import asset_service


def identity(row):
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(row.get("doi") or "").strip(), flags=re.I).casefold()
    return "doi:" + doi if doi else "title:" + re.sub(r"\s+", " ", row.get("title") or "").strip().casefold()


def visible(db, project_id, rows, papers=False):
    excluded = [dict(r) for r in db.db.execute("SELECT result_id,identity FROM zotero_project_memberships WHERE task_id=? AND status IN ('excluded','removing')", (project_id,))]
    included = [dict(r) for r in db.db.execute("SELECT result_id,identity FROM zotero_project_memberships WHERE task_id=? AND status IN ('active','selected')", (project_id,))]
    active_ids = {r['result_id'] for r in included if r['result_id']}
    active_dois = {r['identity'] for r in included if r['identity'].startswith('doi:')}
    identifiers = {r['result_id'] for r in excluded if r['result_id'] and r['result_id'] not in active_ids and r['identity'] not in active_dois}
    identities = {r["identity"] for r in excluded if r["identity"].startswith("doi:")} - active_dois
    if papers:
        identifiers = {r['paper_id'] for r in db.db.execute('SELECT result_id,paper_id FROM project_paper_links WHERE task_id=?', (project_id,)) if r['result_id'] in identifiers}
    return [r for r in rows if r["id"] not in identifiers and identity(r) not in identities]


def records(db, project_id):
    groups = {}
    for row in sorted(db.results(project_id), key=lambda r: (r["created_at"], r["id"])):
        key = identity(row)
        if not row.get("doi"):
            key += ":" + row["id"]
        if key in groups:
            groups[key]["result_ids"].append(row["id"])
        else:
            groups[key] = {**row, "result_id": row["id"], "result_ids": [row["id"]]}
    return list(groups.values())


def link_pdf(db, project_id, result_id, paper_id):
    if db.result(result_id)["task_id"] != project_id or not db.db.execute(
            "SELECT 1 FROM papers WHERE id=? AND task_id=?", (paper_id, project_id)).fetchone():
        raise ValueError("文献与 PDF 必须属于同一项目")
    db.db.execute("INSERT OR REPLACE INTO project_paper_links VALUES (?,?,?)", (result_id, project_id, paper_id))
    db.db.commit()
    index_record(db, project_id, result_id)


def index_record(db, project_id, result_id):
    row = db.result(result_id)
    if row["task_id"] != project_id:
        raise ValueError("文献不属于当前项目")
    linked = db.db.execute("SELECT paper_id FROM project_paper_links WHERE result_id=?", (result_id,)).fetchone()
    meta = {**row, "result_id": result_id, "legacy_id": result_id}
    if linked:
        meta["paper_id"] = linked["paper_id"]
    asset = asset_service.register(db, project_id=project_id, kind="paper", title=row["title"],
        canonical_uri=f"research://{project_id}/literature/{result_id}", storage="virtual", source=row["source"],
        metadata=meta, body="\n".join(str(row.get(k) or "") for k in ("title", "authors", "doi", "abstract")))
    if linked:
        import papergraph
        papergraph.ensure_meta(db.db, project_id)
        db.db.execute("UPDATE paper_meta SET title=?,authors=?,doi=?,year=? WHERE paper_id=?",
            (row["title"], row["authors"], row["doi"], row["year"], linked["paper_id"]))
        db.db.commit()
        for source in asset_service.find_by_metadata(db, project_id=project_id, kind="paper", key="paper_id", value=linked["paper_id"]):
            if source["id"] != asset["id"]:
                asset_service.link(db, source["id"], "bibliography", asset["id"])
    return asset


def update_record(db, project_id, result_id, fields):
    row = db.result(result_id)
    if row["task_id"] != project_id:
        raise ValueError("文献不属于当前项目")
    current = next(r for r in records(db, project_id) if result_id in r["result_ids"])
    allowed = ("title", "authors", "year", "doi", "url", "abstract")
    values = {k: str(fields[k] or "") for k in allowed if k in fields}
    if values:
        for rid in current["result_ids"]:
            db.db.execute("UPDATE search_cache SET " + ",".join(k + "=?" for k in values) + " WHERE id=? AND task_id=?",
                          (*values.values(), rid, project_id))
        db.db.commit()
        for rid in current["result_ids"]:
            index_record(db, project_id, rid)
    return db.result(result_id)


def items(db, project_id):
    papers = {r["id"]: r for r in db.papers_of(project_id)}
    explicit = {r["result_id"]: r["paper_id"] for r in db.db.execute("SELECT * FROM project_paper_links WHERE task_id=?", (project_id,))}
    queue = {r["id"]: r for r in db.queue(project_id)}
    has_links = db.db.execute("SELECT 1 FROM sqlite_master WHERE name='zotero_item_links'").fetchone()
    zotero = {r["result_id"]: r["item_key"] for r in db.db.execute("SELECT * FROM zotero_item_links WHERE task_id=?", (project_id,))} if has_links else {}
    used, result = set(), []
    for row in records(db, project_id):
        pid = next((explicit[r] for r in row["result_ids"] if r in explicit), "")
        if not pid:
            matches = [p for p in papers.values() if identity(p) == identity(row) or any(
                queue.get(r, {}).get("saved_path") == p["source_path"] for r in row["result_ids"])]
            pid = matches[0]["id"] if len(matches) == 1 else ""
        paper = papers.get(pid, {})
        used.add(pid)
        result.append({**paper, **row, "paper_id": pid, "source_path": paper.get("source_path", ""),
                       "has_pdf": bool(paper and Path(paper["source_path"]).is_file()),
                       "zotero_key": next((zotero[r] for r in row["result_ids"] if r in zotero), "")})
    result.extend({**p, "paper_id": p["id"], "result_id": "", "result_ids": [], "zotero_key": "",
                   "has_pdf": Path(p["source_path"]).is_file()} for pid, p in papers.items() if pid not in used)
    return result


def route(method, path, body, query, db):
    if path != "/api/library/project":
        return None
    project_id = str(body.get("task_id") or (query.get("task_id") or [""])[0])
    import workflow_layout
    workflow_layout._project(db, project_id)
    if method == "GET":
        rows = items(db, project_id)
        return {"task_id": project_id, "items": rows, "version": __import__("workflow_state").digest(rows)}
    if method == "POST":
        import zotero_sync
        with zotero_sync._sync_lock:
            rid = str(body.get('result_id') or '')
            row = db.result(rid)
            if row['task_id'] != project_id:
                raise ValueError('文献不属于当前项目')
            expected = body.get('expected')
            if expected is not None:
                keys = ('title', 'authors', 'year', 'doi', 'url', 'abstract')
                if not isinstance(expected, dict) or set(expected) != set(keys):
                    raise ValueError('请提供完整的题录编辑基准')
                if any(str(row.get(key) or '') != str(expected[key] or '') for key in keys):
                    raise ValueError('编辑期间文献已更新，已保留你的编辑内容；请重新读取最新题录后修改')
            return {'item': update_record(db, project_id, rid, body.get('fields') or {})}
    return None
