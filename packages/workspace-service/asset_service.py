"""Canonical asset, revision, relation and provenance services.

The service is deliberately small: SQLite remains the metadata/index store while
large or user-editable content stays in the existing project files.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Any


def _conn(db):
    return db.db if hasattr(db, "db") else db


def _now(db) -> str:
    try:
        import workflow
        return workflow.now()
    except ImportError:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write a file atomically so a killed process cannot leave a partial Markdown file."""
    target = Path(path)
    import fileguard
    fileguard.assert_writable(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return target


def _fts_upsert(conn, asset: dict[str, Any], body: str = "") -> None:
    conn.execute("DELETE FROM asset_fts WHERE asset_id=?", (asset["id"],))
    conn.execute("INSERT INTO asset_fts(asset_id,project_id,title,body) VALUES(?,?,?,?)",
                 (asset["id"], asset.get("project_id", ""), asset.get("title", ""), body or ""))


def get(db, asset_id: str) -> dict[str, Any] | None:
    row = _conn(db).execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    return dict(row) if row else None


def find_by_metadata(db, *, project_id: str = "", kind: str = "", key: str,
                     value: Any) -> list[dict[str, Any]]:
    """Find assets by a small legacy identifier stored in metadata JSON."""
    conn = _conn(db)
    clauses, args = ["1=1"], []
    if project_id:
        clauses.append("project_id=?")
        args.append(project_id)
    if kind:
        clauses.append("kind=?")
        args.append(kind)
    rows = conn.execute("SELECT * FROM assets WHERE " + " AND ".join(clauses) +
                        " ORDER BY updated_at DESC", args).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        metadata = _metadata(item.get("metadata"))
        if str(metadata.get(key, "")) == str(value):
            out.append(item)
    return out


def list_assets(db, project_id: str = "", kind: str = "", limit: int = 200) -> list[dict[str, Any]]:
    conn = _conn(db)
    clauses, args = ["1=1"], []
    if project_id:
        clauses.append("project_id=?")
        args.append(project_id)
    if kind:
        clauses.append("kind=?")
        args.append(kind)
    args.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(
        "SELECT * FROM assets WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?", args
    ).fetchall()]


def register(db, *, project_id: str = "", kind: str, title: str = "", canonical_uri: str,
             storage: str = "file", mime: str = "", content_hash: str = "",
             source: str = "local", metadata: dict[str, Any] | None = None,
             body: str = "") -> dict[str, Any]:
    conn = _conn(db)
    stamp = _now(db)
    asset_id = "asset-" + uuid.uuid4().hex
    conn.execute("""INSERT INTO assets
        (id,project_id,kind,title,canonical_uri,storage,mime,content_hash,source,metadata,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(canonical_uri) DO UPDATE SET
          project_id=excluded.project_id, kind=excluded.kind, title=excluded.title,
          storage=excluded.storage, mime=excluded.mime, content_hash=excluded.content_hash,
          source=excluded.source, metadata=excluded.metadata, updated_at=excluded.updated_at""",
                 (asset_id, project_id or "", kind, title or "", canonical_uri, storage,
                  mime or mimetypes.guess_type(canonical_uri)[0] or "", content_hash or "",
                  source or "local", _json(metadata), stamp, stamp))
    row = conn.execute("SELECT * FROM assets WHERE canonical_uri=?", (canonical_uri,)).fetchone()
    asset = dict(row)
    _fts_upsert(conn, asset, body)
    conn.commit()
    return asset


def register_file(db, path: str | Path, *, project_id: str = "", kind: str = "document",
                  title: str = "", source: str = "local", run_id: str = "",
                  body: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    import execution
    execution.check()
    context = getattr(db, "asset_run", None)
    run_id = run_id or (context["id"] if context else "")
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    existing = _conn(db).execute("SELECT content_hash FROM assets WHERE canonical_uri=?", (str(target),)).fetchone()
    current_hash = sha256_file(target)
    asset = register(db, project_id=project_id, kind=kind, title=title or target.stem,
                     canonical_uri=str(target), storage="file", content_hash=current_hash,
                     source=source, metadata=metadata, body=body)
    revision = add_revision(db, asset["id"], str(target), asset["content_hash"],
                            created_by=source, run_id=run_id, message="file sync",
                            force=bool(existing and existing["content_hash"] != current_hash))
    if revision:
        asset = get(db, asset["id"]) or asset
    if context:
        if asset["id"] not in context["outputs"]:
            context["outputs"].append(asset["id"])
        for input_id in context["inputs"]:
            if input_id != asset["id"]:
                link(db, asset["id"], "derived_from", input_id)
    if run_id and revision:
        provenance(db, asset["id"], run_id, "file_written", actor=source,
                   input_snapshot={"inputs": context["inputs"] if context else [],
                                   "revision_id": revision["id"]})
    return asset


def add_revision(db, asset_id: str, content_uri: str, content_hash: str, *,
                 created_by: str = "system", run_id: str = "", message: str = "",
                 force: bool = False) -> dict[str, Any] | None:
    conn = _conn(db)
    asset = get(db, asset_id)
    if not asset:
        raise KeyError(asset_id)
    if not force and content_hash and content_hash == asset.get("content_hash") and asset.get("current_revision_id"):
        return None
    row = conn.execute("SELECT COALESCE(MAX(revision),0) + 1 AS next FROM asset_revisions WHERE asset_id=?",
                       (asset_id,)).fetchone()
    revision = int(row["next"])
    if asset.get("storage") == "file" and Path(content_uri).is_file():
        import paths
        destination = paths.DATA / "revisions" / asset_id / (content_hash + Path(content_uri).suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
            os.close(fd)
            try:
                shutil.copyfile(content_uri, temporary)
                if sha256_file(temporary) != content_hash:
                    raise ValueError("文件在登记版本时发生变化，请重试")
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
        content_uri = str(destination)
    rid = "rev-" + uuid.uuid4().hex
    stamp = _now(db)
    conn.execute("""INSERT INTO asset_revisions
        (id,asset_id,revision,content_uri,content_hash,created_by,run_id,message,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
                 (rid, asset_id, revision, content_uri, content_hash, created_by, run_id, message, stamp))
    conn.execute("UPDATE assets SET content_hash=?,current_revision_id=?,updated_at=? WHERE id=?",
                 (content_hash, rid, stamp, asset_id))
    conn.commit()
    return dict(conn.execute("SELECT * FROM asset_revisions WHERE id=?", (rid,)).fetchone())


def link(db, from_asset_id: str, relation: str, to_asset_id: str,
         metadata: dict[str, Any] | None = None) -> None:
    conn = _conn(db)
    conn.execute("""INSERT OR REPLACE INTO asset_relations
        (from_asset_id,relation,to_asset_id,metadata,created_at) VALUES(?,?,?,?,?)""",
                 (from_asset_id, relation, to_asset_id, _json(metadata), _now(db)))
    conn.commit()


def provenance(db, asset_id: str, run_id: str, operation: str, *, actor: str = "system",
               input_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    row = ("prov-" + uuid.uuid4().hex, asset_id, run_id, actor, operation,
           _json(input_snapshot), _now(db))
    _conn(db).execute("INSERT INTO asset_provenance VALUES(?,?,?,?,?,?,?)", row)
    _conn(db).commit()
    return {"id": row[0], "asset_id": asset_id, "run_id": run_id, "actor": actor,
            "operation": operation, "input_snapshot": input_snapshot or {}, "created_at": row[6]}


def search(db, query: str, *, project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    conn = _conn(db)
    args: list[Any] = [query]
    where = ""
    if project_id:
        where = " AND a.project_id=?"
        args.append(project_id)
    args.append(max(1, min(int(limit), 200)))
    try:
        rows = conn.execute("""SELECT a.*, snippet(asset_fts, 3, '<mark>', '</mark>', ' … ', 24) AS snippet
            FROM asset_fts JOIN assets a ON a.id=asset_fts.asset_id
            WHERE a.status='active' AND asset_fts MATCH ?""" + where + " ORDER BY a.updated_at DESC LIMIT ?", args).fetchall()
    except Exception:
        rows = []
    # unicode61 does not split CJK text into word tokens, so retain a LIKE
    # fallback for Chinese notes while keeping FTS as the fast path.
    if not rows:
        project_where = ""
        like_args: list[Any] = [f"%{query}%", f"%{query}%"]
        if project_id:
            project_where = " AND f.project_id=?"
            like_args.append(project_id)
        like_args.append(max(1, min(int(limit), 200)))
        rows = conn.execute("""SELECT a.*, substr(f.body, 1, 180) AS snippet
            FROM asset_fts f JOIN assets a ON a.id=f.asset_id
            WHERE a.status='active' AND (f.title LIKE ? OR f.body LIKE ?)""" + project_where +
            " ORDER BY a.updated_at DESC LIMIT ?", like_args).fetchall()
    return [dict(row) for row in rows]


def relations(db, asset_id: str) -> list[dict[str, Any]]:
    conn = _conn(db)
    rows = conn.execute("""SELECT r.*, a.title, a.kind, a.canonical_uri
        FROM asset_relations r JOIN assets a ON a.id=r.to_asset_id
        WHERE r.from_asset_id=? ORDER BY r.created_at""", (asset_id,)).fetchall()
    return [dict(row) for row in rows]


def project_diagnostics(db, project_id: str = "") -> dict[str, Any]:
    assets = list_assets(db, project_id, limit=1000)
    return {"project_id": project_id, "assets": len(assets),
            "by_kind": {kind: sum(1 for item in assets if item["kind"] == kind)
                         for kind in sorted({item["kind"] for item in assets})},
            "conflicts": sum(1 for item in assets if item["status"] == "conflict")}


def _metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _virtual_asset(db, *, project_id: str, kind: str, legacy_id: str, title: str,
                   body: str, metadata: dict[str, Any] | None = None,
                   uri_prefix: str = "") -> dict[str, Any]:
    """Index a legacy SQLite row without inventing a filesystem mirror."""
    uri = f"db://{uri_prefix or kind}/{legacy_id}"
    old = _conn(db).execute("SELECT content_hash FROM assets WHERE canonical_uri=?", (uri,)).fetchone()
    digest = sha256_bytes((body or "").encode("utf-8")) if body else ""
    merged = {"legacy_id": legacy_id, **(metadata or {})}
    asset = register(db, project_id=project_id, kind=kind, title=title,
                     canonical_uri=uri, storage="sqlite", source="legacy_rebuild",
                     content_hash=digest, metadata=merged, body=body)
    add_revision(db, asset["id"], uri, digest, created_by="legacy_rebuild",
                 message="legacy index rebuild", force=bool(old and old["content_hash"] != digest))
    return get(db, asset["id"]) or asset


def _legacy_note_asset(db, row: dict[str, Any], project_id: str, table: str) -> dict[str, Any]:
    """Prefer the real Markdown mirror when rebuilding a legacy note."""
    legacy_id = str(row["id"])
    title = str(row.get("title") or "项目笔记")
    body = str(row.get("markdown") if table == "notes" else row.get("content") or "")
    for asset in find_by_metadata(db, project_id=project_id, kind="note", key="legacy_id", value=legacy_id):
        if asset["storage"] == "file" and asset["status"] == "active" and Path(asset["canonical_uri"]).is_file():
            body = Path(asset["canonical_uri"]).read_text(encoding="utf-8")
            return register_file(db, asset["canonical_uri"], project_id=project_id, kind="note", title=title,
                                 source=asset["source"], body=body, metadata=_metadata(asset["metadata"]))
    if project_id:
        path = db.pdir(project_id, "笔记") / f"{legacy_id}.md"
    else:
        try:
            from paths import NOTES
            path = NOTES / f"{legacy_id}.md"
        except ImportError:
            path = None
    if path and path.is_file():
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
        asset = register_file(db, path, project_id=project_id, kind="note", title=title,
                              source="legacy_rebuild", body=body,
                              metadata={"paper_id": row.get("paper_id") or "",
                                        "legacy_id": legacy_id, "table": table})
        # Older rebuilds used a db:// URI for the same note. Retain it for
        # provenance, but keep only the file-backed asset active.
        old_uri = f"db://{table}/{legacy_id}"
        db.db.execute("UPDATE assets SET status='archived', updated_at=? "
                      "WHERE canonical_uri=? AND id<>?", (_now(db), old_uri, asset["id"]))
        db.db.commit()
        return asset
    return _virtual_asset(db, project_id=project_id, kind="note", legacy_id=legacy_id,
                          title=title, body=body,
                          metadata={"paper_id": row.get("paper_id") or "", "table": table},
                          uri_prefix=table)


def rebuild_project_index(db, project_id: str, *, include_files: bool = True) -> dict[str, Any]:
    """Idempotently project legacy rows and known project files into the asset graph.

    This is intentionally an explicit operation: opening an old database must not
    unexpectedly scan or rewrite a user's project directory.
    """
    project_id = (project_id or "").strip()
    if not project_id:
        raise ValueError("project_id is required")
    conn = _conn(db)
    counts = {"papers": 0, "evidence": 0, "notes": 0, "documents": 0}

    try:
        paper_rows = db.papers_of(project_id)
    except Exception:
        paper_rows = [dict(r) for r in conn.execute("SELECT * FROM papers WHERE task_id=?", (project_id,))]
    paper_assets: dict[str, str] = {}
    for paper in paper_rows:
        pid = str(paper.get("id") or "")
        if not pid:
            continue
        title = str(paper.get("title") or paper.get("filename") or pid)
        body = ""
        try:
            body = "\n".join(str(r["text"] or "") for r in conn.execute(
                "SELECT text FROM paper_pages WHERE paper_id=? ORDER BY page_no", (pid,)))
        except Exception:
            pass
        source = Path(str(paper.get("source_path") or "")).expanduser()
        if include_files and source.is_file():
            asset = register_file(db, source, project_id=project_id, kind="paper", title=title,
                                  source="legacy_rebuild", body=body,
                                  metadata={"paper_id": pid, "legacy_id": pid,
                                            "pages": paper.get("pages", 0)})
        else:
            asset = _virtual_asset(db, project_id=project_id, kind="paper", legacy_id=pid,
                                   title=title, body=body,
                                   metadata={"paper_id": pid, "filename": paper.get("filename", "")})
        paper_assets[pid] = asset["id"]
        counts["papers"] += 1

    for evidence in conn.execute(
            "SELECT e.*, p.task_id FROM evidence e JOIN papers p ON p.id=e.paper_id WHERE p.task_id=?",
            (project_id,)).fetchall():
        row = dict(evidence)
        asset = _virtual_asset(db, project_id=project_id, kind="evidence",
                               legacy_id=str(row["id"]),
                               title=f"{row.get('field', '')}: {row.get('value', '')}".strip(": "),
                               body=str(row.get("excerpt") or ""),
                               metadata={"evidence_id": row["id"], "paper_id": row["paper_id"],
                                         "page_no": row.get("page_no", 0), "kind": row.get("kind", "")})
        if paper_assets.get(row["paper_id"]):
            link(db, asset["id"], "derived_from", paper_assets[row["paper_id"]],
                 {"page_no": row.get("page_no", 0)})
        counts["evidence"] += 1

    for note in conn.execute("SELECT * FROM notes WHERE task_id=?", (project_id,)).fetchall():
        row = dict(note)
        asset = _legacy_note_asset(db, row, project_id, "notes")
        if paper_assets.get(row.get("paper_id")):
            link(db, asset["id"], "derived_from", paper_assets[row["paper_id"]],
                 {"paper_id": row["paper_id"]})
        counts["notes"] += 1

    for note in conn.execute("SELECT * FROM md_notes WHERE task_id=?", (project_id,)).fetchall():
        row = dict(note)
        asset = _legacy_note_asset(db, row, project_id, "md_notes")
        if paper_assets.get(row.get("paper_id")):
            link(db, asset["id"], "derived_from", paper_assets[row["paper_id"]],
                 {"paper_id": row["paper_id"]})
        counts["notes"] += 1

    if include_files:
        try:
            base_raw = db.summary(project_id)["task"].get("base_dir") or ""
            base = Path(base_raw) if base_raw else None
        except Exception:
            base = None
        roots = [base / "论文正文", base / "记忆", base / "其余" / "团队"] if base else []
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".md", ".json", ".txt"}:
                    try:
                        body = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
                    register_file(db, path, project_id=project_id, kind="document",
                                  title=path.stem, source="legacy_rebuild", body=body,
                                  metadata={"legacy_path": str(path)})
                    counts["documents"] += 1
    return {"project_id": project_id, "indexed": counts,
            "assets": project_diagnostics(db, project_id)["assets"]}
