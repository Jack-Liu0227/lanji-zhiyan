"""Explicit, user-triggered import of project-staged papers into Zotero."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import service_settings as store
import zotero


PLAN_SERVICE = "zotero_staged_plan"
PLAN_TTL = 600


def _task(db: Any, task_id: str) -> dict[str, Any]:
    row = db.db.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("project not found")
    return dict(row)


def _root(db: Any, task_id: str) -> Path:
    root = db.base_dir(task_id)
    if not root:
        raise ValueError("project has no base folder for staged PDFs")
    return Path(root).resolve()


def _row(db: Any, task_id: str, result_id: str) -> dict[str, Any]:
    raw = next((item for item in db.queue(task_id) if item.get("id") == result_id), None)
    if not raw:
        raise ValueError("staged record not found")
    result = db.result(result_id)
    if result["task_id"] != task_id:
        raise ValueError("record does not belong to this project")
    value = {**result, **raw, "path": raw.get("saved_path", "")}
    return value


def list_staged(db: Any, task_id: str) -> dict[str, Any]:
    _task(db, task_id)
    rows = []
    for item in db.queue(task_id):
        if item.get("status") != "downloaded":
            continue
        if item.get("zotero_status") in {"external", "imported"}:
            continue
        rows.append(item)
    return {"task_id": task_id, "items": rows}


def _doi(value: Any) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I).casefold().rstrip(".,;)]}'\"")


def _authors(value: Any) -> list[dict[str, str]]:
    names = [part.strip() for part in re.split(r"[;；\n]+", str(value or "")) if part.strip()]
    return [{"creatorType": "author", "name": name} for name in names]


def _payload(row: dict[str, Any], collection: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "itemType": "journalArticle",
        "title": str(row.get("title") or "").strip(),
        "creators": _authors(row.get("authors")),
        "date": str(row.get("year") or "").strip(),
        "DOI": str(row.get("doi") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "abstractNote": str(row.get("abstract") or "").strip(),
    }
    if collection:
        payload["collections"] = [collection]
    return payload


def _match(row: dict[str, Any]) -> dict[str, Any]:
    doi = _doi(row.get("doi"))
    title = str(row.get("title") or "").strip().casefold()
    query = doi or title
    if not query:
        return {"kind": "create", "key": "", "candidates": []}
    found = [item for item in zotero.items(q=query, start=0, limit=20).get("items", [])
             if item.get("type") != "attachment"]
    doi_matches = [item for item in found if doi and _doi(item.get("doi")) == doi]
    if len(doi_matches) == 1:
        return {"kind": "match", "key": doi_matches[0]["key"], "candidates": doi_matches}
    if len(doi_matches) > 1:
        return {"kind": "conflict", "key": "", "candidates": doi_matches}
    title_matches = [item for item in found if title and str(item.get("title") or "").strip().casefold() == title]
    if len(title_matches) == 1:
        return {"kind": "match", "key": title_matches[0]["key"], "candidates": title_matches}
    if len(title_matches) > 1:
        return {"kind": "conflict", "key": "", "candidates": title_matches}
    return {"kind": "create", "key": "", "candidates": []}


def plan(db: Any, task_id: str, result_ids: list[str] | None = None,
         collection: str = "", attachment_mode: str = "imported_file") -> dict[str, Any]:
    _task(db, task_id)
    if attachment_mode not in {"imported_file", "plugin"}:
        raise ValueError("attachment_mode must be imported_file or plugin")
    collection = str(collection or "").strip()
    if collection:
        zotero._key(collection)
    ids = [str(value) for value in (result_ids or []) if str(value).strip()]
    if not ids:
        ids = [item["id"] for item in list_staged(db, task_id)["items"]]
    if not ids:
        raise ValueError("select at least one staged PDF")
    root = _root(db, task_id)
    actions, conflicts = [], []
    for rid in dict.fromkeys(ids):
        row = _row(db, task_id, rid)
        if row.get("status") != "downloaded":
            conflicts.append({"result_id": rid, "title": row.get("title", rid),
                              "reason": "the paper has not finished downloading into the project staging area"})
            continue
        if row.get("zotero_status") == "imported":
            conflicts.append({"result_id": rid, "title": row.get("title", rid),
                              "reason": "this staged paper is already imported into Zotero"})
            continue
        path = Path(str(row.get("path") or row.get("saved_path") or "")).expanduser().resolve()
        if not path.is_file() or not path.is_relative_to(root):
            conflicts.append({"result_id": rid, "title": row.get("title", rid),
                              "reason": "staged PDF is missing or outside the project directory"})
            continue
        mapped = db.db.execute("SELECT item_key FROM zotero_item_links WHERE task_id=? AND result_id=?",
                              (task_id, rid)).fetchone() if db.db.execute(
                                  "SELECT 1 FROM sqlite_master WHERE name='zotero_item_links'").fetchone() else None
        match = {"kind": "match", "key": mapped["item_key"], "candidates": []} if mapped else _match(row)
        if match["kind"] == "conflict":
            conflicts.append({"result_id": rid, "title": row.get("title", rid),
                              "reason": "multiple Zotero matches; choose one explicitly",
                              "candidates": [{"key": x.get("key", ""), "title": x.get("title", ""),
                                              "doi": x.get("doi", "")} for x in match["candidates"]]})
            continue
        actions.append({"kind": match["kind"], "result_id": rid, "key": match["key"],
                        "title": row.get("title", ""), "path": str(path),
                        "payload": _payload(row, collection), "attachment_mode": "imported_file"})
    token = hashlib.sha256(json.dumps({"task_id": task_id, "actions": actions,
                                       "conflicts": conflicts}, sort_keys=True,
                                      ensure_ascii=False).encode()).hexdigest()
    result = {"task_id": task_id, "actions": actions, "conflicts": conflicts,
              "plan_token": token, "expires_in": PLAN_TTL, "attachment_mode": "imported_file",
              "policy": "Only an explicit confirmation writes metadata and imports the PDF; the staged file is never deleted."}
    store.write(db, PLAN_SERVICE, {"token": token, "expires": time.time() + PLAN_TTL, "plan": result}, task_id)
    return result


def apply(db: Any, task_id: str, plan_token: str, confirm: bool = False) -> dict[str, Any]:
    _task(db, task_id)
    if confirm is not True:
        raise ValueError("Zotero import requires explicit confirmation")
    saved = store.read(db, PLAN_SERVICE, task_id)
    if not plan_token or saved.get("token") != plan_token or saved.get("expires", 0) < time.time():
        raise ValueError("staged import preview expired; create a new preview")
    plan_data = saved.get("plan") or {}
    if plan_data.get("task_id") != task_id:
        raise PermissionError("staged import preview belongs to another project")
    import zotero_sync
    zotero_sync.ensure(db)
    root = _root(db, task_id)
    applied, failed = [], []
    for action in plan_data.get("actions", []):
        rid, key = action["result_id"], str(action.get("key") or "")
        try:
            row = _row(db, task_id, rid)
            path = Path(str(row.get("path") or row.get("saved_path") or "")).expanduser().resolve()
            if not path.is_file() or not path.is_relative_to(root):
                raise PermissionError("暂存 PDF 已不存在，或已移出当前项目目录")
            if not key:
                created = zotero.add_item(db, action["payload"])
                key = created["key"]
            db.set_zotero_stage(task_id, rid, "metadata_written", zotero_key=key)
            attachment = zotero.import_pdf(db, key, str(path), action.get("title") or "PDF")
            remote = zotero.item(key)
            db.db.execute("INSERT OR REPLACE INTO zotero_item_links VALUES (?,?,?,?,?)",
                          (task_id, rid, key, int(remote.get("version") or 0), zotero_sync._hash(row)))
            db.db.commit()
            db.set_zotero_stage(task_id, rid, "imported", zotero_key=key,
                                attachment_key=attachment.get("key", ""))
            applied.append({"result_id": rid, "key": key, "attachment_key": attachment.get("key", "")})
        except Exception as exc:
            db.set_zotero_stage(task_id, rid, "failed", zotero_key=key, error=str(exc)[:1000])
            failed.append({"result_id": rid, "key": key, "error": str(exc)})
    store.write(db, PLAN_SERVICE, {}, task_id)
    return {"ok": not failed, "partial": bool(applied and failed), "applied": applied,
            "failed": failed}


def route(method: str, path: str, body: dict, query: dict, db: Any) -> dict | None:
    if not path.startswith("/api/zotero/web/staged"):
        return None
    task_id = str(body.get("task_id") or (query.get("task_id") or [""])[0])
    if method == "GET" and path == "/api/zotero/web/staged":
        return list_staged(db, task_id)
    if method == "POST" and path == "/api/zotero/web/staged/plan":
        return plan(db, task_id, body.get("result_ids"), str(body.get("collection") or ""),
                    str(body.get("attachment_mode") or "imported_file"))
    if method == "POST" and path == "/api/zotero/web/staged/apply":
        return apply(db, task_id, str(body.get("plan_token") or ""), body.get("confirm") is True)
    return None
