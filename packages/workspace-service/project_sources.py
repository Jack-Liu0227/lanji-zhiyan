"""Copy Zotero inputs into a project without changing Zotero-owned files."""
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
