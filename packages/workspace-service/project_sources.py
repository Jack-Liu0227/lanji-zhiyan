"""Resolve Zotero attachments read-only and preserve existing project copies."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

import asset_service
import fileguard
import project_notes
import zotero


def resolve_pdf_for_read(db, project_id: str, paper_id: str) -> Path | None:
    import zotero_sync
    import project_library

    row = db.db.execute("""SELECT p.*, r.library, r.item_key, r.attachment_key
        FROM papers p LEFT JOIN paper_zotero_refs r ON p.id=r.paper_id
        WHERE p.id=? AND p.task_id=?""", (paper_id, project_id)).fetchone()
    if not row or not project_library.visible(db, project_id, [dict(row)], papers=True):
        return None
    raw = row["source_path"]
    if row["library"]:
        cfg = zotero_sync.config(db)
        if cfg.get("transport") != "local" or zotero_sync.library(cfg) != row["library"]:
            return None
        raw = zotero.pdf_path(row["item_key"], cfg.get("base_url"), attachment_key=row["attachment_key"])
    path = Path(raw) if raw else None
    if path and path.suffix.lower() == ".pdf" and path.is_file():
        if row["library"]:
            fileguard.protect_source(path)
            _refresh_index(db, row, path)
        return path
    return None


def _refresh_index(db, paper, path):
    import pdfio
    import semantic

    digest = asset_service.sha256_file(path)
    if paper['sha256'] == digest:
        return
    pages = pdfio.page_texts(path)
    if asset_service.sha256_file(path) != digest:
        raise ValueError('Zotero 附件读取期间发生变化，请重试')
    paper_id = paper['id']
    length = sum(map(len, pages))
    with db.db:
        if db.db.execute('UPDATE papers SET sha256=?,pages=?,text_chars=?,ocr_status=? WHERE id=? AND sha256=?',
                         (digest, len(pages), length, 'text' if length >= 100 else 'likely-scan', paper_id, paper['sha256'])).rowcount:
            db.db.execute('DELETE FROM paper_pages WHERE paper_id=?', (paper_id,))
            db.db.executemany('INSERT INTO paper_pages VALUES (?,?,?)',
                              ((paper_id, n, text) for n, text in enumerate(pages, 1)))
            db.db.execute('INSERT OR IGNORE INTO paper_evidence_recheck SELECT id FROM evidence WHERE paper_id=?', (paper_id,))
            for row in db.db.execute('SELECT id FROM evidence WHERE paper_id=?', (paper_id,)).fetchall():
                semantic.invalidate_source(db, 'evidence', row['id'])
            semantic.invalidate_source(db, 'paper', paper_id)
            db.db.execute('DELETE FROM paper_graph WHERE task_id=?', (paper['task_id'],))
    semantic.warm_async(str(db.path))


def reference_zotero_pdf(db, project_id: str, key: str, attachment_key: str = "", *, replace_id: str = "") -> dict:
    import zotero_sync

    with zotero_sync._sync_lock:
        cfg = zotero_sync.config(db)
        if cfg.get("transport") != "local":
            raise ValueError("请切换到本机 Zotero 以引用 PDF")
        library = zotero_sync.library(cfg)
        key = zotero._key(key)
        attachment = zotero.pdf_attachment(key, cfg.get("base_url"), attachment_key=attachment_key)
        if not attachment:
            raise FileNotFoundError("Zotero 附件尚不可读，请先在 Zotero 中下载 PDF")
        path = Path(attachment["path"])
        fileguard.protect_source(path)
        ref = (library, key, attachment["key"])
        existing = db.db.execute("""SELECT paper_id FROM paper_zotero_refs
            WHERE task_id=? AND library=? AND item_key=? AND attachment_key=?""", (project_id, *ref)).fetchone()
        if existing and replace_id and existing['paper_id'] != replace_id:
            raise ValueError('此项目已引用该附件，原 PDF 记录保留')
        paper_id = existing["paper_id"] if existing else db.ingest_pdf(
            project_id, path, zotero_ref=ref, replace_id=replace_id)["id"]
        return {"paper_id": paper_id, "zotero_key": key, "attachment_key": ref[2], "reused": bool(existing)}


def use_zotero_reference(db, project_id, paper_id):
    import zotero_sync as sync
    import project_library

    with sync._sync_lock:
        bound = sync.binding(db, project_id)
        if not bound or bound['library'] != sync.library(sync.config(db)):
            raise ValueError('请核对项目绑定的 Zotero 库')
        row = next((item for item in project_library.items(db, project_id)
                    if item['paper_id'] == paper_id and item['zotero_key']), None)
        if not row:
            raise ValueError('未找到此项目的 Zotero 文献')
        path = Path(row['source_path']) if row['source_path'] else None
        if not path or not path.is_file() or asset_service.sha256_file(path) != row['sha256']:
            raise ValueError('项目 PDF 已变化或不可读，请保留当前文件')
        linked = reference_zotero_pdf(db, project_id, row['zotero_key'], replace_id=paper_id)
        project_library.index_record(db, project_id, row['result_id'])
        return linked


def restore_source_protection(db) -> None:
    for row in db.db.execute("SELECT metadata FROM assets WHERE source='zotero_copy'"):
        try:
            metadata = json.loads(row["metadata"])
            if isinstance(metadata, dict) and metadata.get("source_read_only") and metadata.get("source_path"):
                fileguard.protect_source(metadata["source_path"])
        except (ValueError, TypeError, OSError):
            continue


def import_zotero_pdf(db, project_id: str, key: str) -> dict:
    root = project_notes.project_root(db, project_id)
    key = str(key or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{8}", key):
        raise ValueError("需要有效的 Zotero 条目 key（8 位字母或数字）")
    item = zotero.item(key)
    raw = zotero.pdf_path(key)
    if not raw:
        raise ValueError("Zotero 条目没有可读取的本机 PDF，请先下载附件")
    source = Path(raw).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("Zotero 附件不是 PDF")
    fileguard.protect_source(source)
    digest = asset_service.sha256_file(source)
    folder = root / "文献库" / "Zotero副本"
    if not folder.resolve().is_relative_to(root):
        raise ValueError("Zotero 副本目录越过项目文件夹")
    # Reuse an unchanged project copy, never an external/global hash match.
    for asset in asset_service.find_by_metadata(db, project_id=project_id, kind="paper", key="zotero_key", value=key):
        metadata = json.loads(asset["metadata"])
        candidate = Path(asset["canonical_uri"]).resolve()
        if (metadata.get("source_sha256") == digest and candidate.is_relative_to(folder.resolve())
                and candidate.is_file() and not candidate.samefile(source)
                and asset_service.sha256_file(candidate) == digest):
            paper_id = metadata.get("paper_id")
            if paper_id and db.db.execute("SELECT 1 FROM papers WHERE id=? AND task_id=? AND source_path=?",
                                         (paper_id, project_id, str(candidate))).fetchone():
                return {"project_id": project_id, "paper_id": paper_id, "path": str(candidate),
                        "zotero_key": key, "source_sha256": digest, "reused": True}
    dest = folder / f"{key}-{uuid.uuid4().hex}" / "source.pdf"
    dest.parent.mkdir(parents=True, exist_ok=False)
    try:
        with source.open("rb") as reader, dest.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        if asset_service.sha256_file(dest) != digest or asset_service.sha256_file(source) != digest:
            raise ValueError("导入期间 Zotero 附件发生变化，请重试")
        paper = db.ingest_pdf(project_id, dest, deduplicate=False)
    except Exception:
        dest.unlink(missing_ok=True)
        dest.parent.rmdir()
        raise
    metadata = {"paper_id": paper["id"], "zotero_key": key, "zotero_version": item.get("version", 0),
                "source_path": str(source), "source_sha256": digest, "source_read_only": True}
    asset_service.register_file(db, dest, project_id=project_id, kind="paper", source="zotero_copy",
                                title=item.get("title") or source.stem, metadata=metadata)
    asset_service.atomic_write_text(dest.with_suffix(".source.json"), json.dumps(metadata, ensure_ascii=False, indent=2))
    fileguard.audit("zotero_copy", dest, source=str(source), project_id=project_id, zotero_key=key)
    return {"project_id": project_id, "paper_id": paper["id"], "path": str(dest),
            "zotero_key": key, "source_sha256": digest, "reused": False}
