"""Canonical asset, revision, relation and provenance services.

The service is deliberately small: SQLite remains the metadata/index store while
large or user-editable content stays in the existing project files.
"""
from __future__ import annotations

import hashlib
import base64
import json
import mimetypes
import os
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Any
import project_notes


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


def is_instruction_asset(asset):
    import note_catalog
    if _metadata(asset.get("metadata")).get("note_type") == "prompt_template":
        return True
    path = Path(asset.get("canonical_uri") or "")
    if asset.get("storage") == "file" and path.suffix.lower() == ".md" and path.is_file():
        try:
            with path.open(encoding="utf-8") as stream:
                return note_catalog.is_template(stream.read(65536))
        except (OSError, UnicodeError):
            return False
    return False


def eligibility_index(db, project_id):
    """Read one project once when classifying a resource list or automatic input set."""
    conn = _conn(db)
    index = {"tasks": {}, "runs": {}, "deliveries": {}, "assets": {}, "paths": {}, "agent_workflows": set(),
             "catalog": {}, "source_paths": {}, "legacy": {}, "workflow_tasks": {}}
    for row in conn.execute("SELECT * FROM assets WHERE project_id=?", (project_id,)):
        asset = dict(row)
        index["catalog"][asset["id"]] = asset
        if asset["storage"] == "file":
            index["source_paths"][_path_key(asset["canonical_uri"])] = asset
        if asset["status"] == "active":
            metadata = _metadata(asset["metadata"])
            for value in {str(metadata[key]) for key in ("legacy_id", "paper_id", "evidence_id", "result_id") if metadata.get(key)}:
                index["legacy"].setdefault((asset["kind"], value), []).append(asset)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_tasks'").fetchone():
        return index
    for row in conn.execute("SELECT id,title,status,latest_run_id,accepted_delivery_id,workflow_run_id,workflow_node_id,parent_task_id "
                            "FROM project_tasks WHERE project_id=?", (project_id,)):
        index["tasks"][row["id"]] = dict(row)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_executions'").fetchone():
        for row in conn.execute("SELECT e.run_id,e.settings FROM workflow_executions e JOIN workflow_runs r ON r.id=e.run_id "
                                "WHERE r.project_id=?", (project_id,)):
            settings = _metadata(row["settings"])
            if settings.get("agent_invocation", {}).get("source") == "agent_tool":
                index["agent_workflows"].add(row["run_id"])
                index["workflow_tasks"][row["run_id"]] = settings.get("task_ids", {})
    for row in conn.execute("SELECT d.id,d.task_id,d.task_run_id,d.decision,d.output_versions FROM project_task_deliveries d "
                            "JOIN project_tasks t ON t.id=d.task_id WHERE t.project_id=?", (project_id,)):
        index["deliveries"][row["id"]] = dict(row)
    for row in conn.execute("SELECT r.id,r.task_id,r.status,r.output_snapshot,r.checkpoint,r.input_snapshot FROM project_task_runs r "
                            "JOIN project_tasks t ON t.id=r.task_id WHERE t.project_id=? ORDER BY r.rowid", (project_id,)):
        run = dict(row)
        run["output_snapshot"] = _metadata(run["output_snapshot"])
        run["checkpoint"] = _metadata(run["checkpoint"])
        run["input_snapshot"] = _metadata(run["input_snapshot"])
        index["runs"][run["id"]] = run
        for output in run["output_snapshot"].get("files", []):
            if not isinstance(output, dict):
                continue
            reference = {"task_id": run["task_id"], "task_run_id": run["id"], "file": output}
            if output.get("asset_id"):
                index["assets"].setdefault(output["asset_id"], []).append(reference)
            for key in ("path", "original_path"):
                if output.get(key):
                    index["paths"].setdefault(_path_key(output[key]), []).append(reference)
    return index


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def eligibility(db, asset, *, index=None):
    """Derive publication state from canonical attempts and accepted deliveries."""
    metadata = _metadata(asset.get("metadata"))
    uri = asset.get("canonical_uri", "")
    path_key = _path_key(uri) if asset.get("storage") == "file" and uri else ""
    index = index if index is not None else eligibility_index(db, asset.get("project_id", ""))
    references = index["assets"].get(asset.get("id"), []) or index["paths"].get(path_key, [])
    task_id = metadata.get("project_task_id", "")
    run_id = metadata.get("task_run_id", "")
    if task_id and references:
        references = [r for r in references if r["task_id"] == task_id and (not run_id or r["task_run_id"] == run_id)]
    reference = references[-1] if references else None
    if reference:
        task_id, run_id = reference["task_id"], reference["task_run_id"]
    controlled = bool(task_id or run_id or reference or metadata.get("delivery_ids") or
                      asset.get("kind") in ("task_output", "schedule_output") or
                      (asset.get("storage") == "file" and ".tasks" in Path(uri).parts))
    result = {"controlled": controlled, "consumable": False, "review_status": "unverified" if controlled else "source",
              "task_id": task_id, "task_run_id": run_id, "output_revision": 0, "delivery_id": "",
              "workflow_run_id": metadata.get("workflow_run_id", ""), "workflow_node_id": "", "node_result": None, "expected_hash": ""}
    if not controlled:
        result["consumable"] = asset.get("status") == "active"
        return result
    task = index["tasks"].get(task_id)
    run = index["runs"].get(run_id or (task or {}).get("latest_run_id"))
    if not task or not run:
        # A workflow archive is explicitly published only after every node is delivered.
        delivery_ids = metadata.get("delivery_ids") or []
        if delivery_ids and all(_current_delivery(index, key) for key in delivery_ids):
            result.update(review_status="delivered", delivery_id=delivery_ids[-1],
                          expected_hash=metadata.get("publication_hash", ""))
        return _check_eligibility_hash(asset, result)
    run_id = run["id"]
    result.update(task_run_id=run_id, output_revision=run["checkpoint"].get("output_revision", 0),
                  workflow_run_id=task["workflow_run_id"], workflow_node_id=task["workflow_node_id"])
    if (task["workflow_run_id"] in index["agent_workflows"] and not task["parent_task_id"] and
            index["workflow_tasks"].get(task["workflow_run_id"], {}).get(task["workflow_node_id"]) == task_id and
            run["status"] == "succeeded" and run["output_snapshot"].get("files")):
        result["node_result"] = {"available": True, "project_id": asset["project_id"], "id": task_id, "task_id": task_id,
                                 "workflow_run_id": task["workflow_run_id"], "workflow_node_id": task["workflow_node_id"],
                                 "node_id": task["workflow_node_id"], "title": task["title"],
                                 "task_run_id": run_id, "output_revision": result["output_revision"]}
    if task["latest_run_id"] != run_id:
        result["review_status"] = "superseded"
    elif run["status"] != "succeeded":
        result["review_status"] = run["status"]
    elif not reference and not metadata.get("delivery_id"):
        result["review_status"] = "superseded"
    elif task["status"] == "delivered" and _current_delivery(index, task["accepted_delivery_id"]):
        result.update(review_status="delivered", delivery_id=task["accepted_delivery_id"])
    elif task["status"] == "blocked":
        rejected = any(d["task_run_id"] == run_id and d["decision"] == "rejected" for d in index["deliveries"].values())
        result["review_status"] = "rejected" if rejected else "blocked"
    else:
        result["review_status"] = task["status"]
    if reference:
        result["expected_hash"] = reference["file"].get("content_hash", reference["file"].get("sha256", ""))
    elif metadata.get("delivery_id") == result["delivery_id"]:
        result["expected_hash"] = metadata.get("publication_hash", "")
    if result["review_status"] == "superseded" and not reference and result["node_result"]:
        revision = metadata.get("revision", metadata.get("output_revision", 0))
        revision = revision if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0 else 0
        result["output_revision"] = revision
        result["node_result"]["output_revision"] = revision
    return _check_eligibility_hash(asset, result)


def _current_delivery(index, delivery_id):
    delivery = index["deliveries"].get(delivery_id)
    task = index["tasks"].get((delivery or {}).get("task_id"))
    return bool(delivery and task and delivery["decision"] == "approved" and task["status"] == "delivered" and
                task["accepted_delivery_id"] == delivery_id and task["latest_run_id"] == delivery["task_run_id"])


def _check_eligibility_hash(asset, result):
    if result["review_status"] == "delivered":
        expected = result["expected_hash"]
        matches = bool(expected and expected == asset.get("content_hash"))
        if matches and asset.get("storage") == "file":
            path = Path(asset["canonical_uri"])
            try:
                matches = path.is_file() and sha256_file(path) == expected
            except OSError:
                matches = False
        if not matches:
            result["review_status"] = "modified"
        else:
            result["consumable"] = asset.get("status") == "active"
    return result


def require_input(db, asset, allow_draft=False, *, index=None):
    """Validate an explicit input without committing the caller's transaction."""
    result = eligibility(db, asset, index=index)
    if asset.get("status") != "active" or is_instruction_asset(asset):
        raise ValueError("输入资料不可用或属于指令模板")
    if result["controlled"] and not result["consumable"] and not allow_draft:
        raise ValueError("任务产物尚未交付或版本已被替代，请选择已交付版本；使用草稿需要明确选择")
    if result["controlled"] and result["consumable"] and asset.get("storage") == "file":
        path = Path(asset["canonical_uri"])
        if not path.is_file() or sha256_file(path) != result["expected_hash"]:
            raise ValueError("已交付资料的工作文件已变化，请重新核查后再采用")
    return {**result, "draft_selected": bool(allow_draft and result["controlled"] and not result["consumable"])}


def require_context(db, asset, *, index=None):
    """Read published sources, the active task's own files, or explicitly frozen inputs."""
    index = index if index is not None else eligibility_index(db, asset.get("project_id", ""))
    result = eligibility(db, asset, index=index)
    if asset.get("status") != "active" or is_instruction_asset(asset):
        raise ValueError("资料不可用或属于指令模板")
    if result["consumable"]:
        return result
    context = getattr(db, "project_task_context", None)
    task_id = getattr(context, "parent_task_id", "")
    task = index["tasks"].get(task_id) if getattr(context, "project_id", "") == asset.get("project_id") else None
    run = index["runs"].get((task or {}).get("latest_run_id"))
    if task and run:
        if result["task_id"] == task_id and result["task_run_id"] == run["id"]:
            return {**result, "context_basis": "own_attempt"}
        if asset.get("storage") == "file":
            path = Path(asset["canonical_uri"])
            digest = sha256_file(path) if path.is_file() else ""
        else:
            digest = virtual_snapshot(db, asset)["content_hash"]
        frozen = run["input_snapshot"]
        for item in frozen.get("assets", []):
            expected = item.get("content_hash", item.get("sha256", ""))
            by_asset = item.get("allow_draft") is True and item.get("asset_id") == asset.get("id")
            by_copy = item.get("path") and asset.get("storage") == "file" and _path_key(item["path"]) == _path_key(asset["canonical_uri"])
            if expected and digest == expected and (by_asset or by_copy):
                return {**result, "context_basis": "frozen_input"}
        base = frozen.get("base_output") or {}
        authorization = base.get("draft_authorization") or {}
        base_run_id = frozen.get("base_run_id", base.get("base_run_id"))
        base_revision = frozen.get("base_revision", base.get("base_revision"))
        authorized = (authorization.get("source") == "human_rework" and authorization.get("task_id") == task_id and
                      authorization.get("task_run_id") == base_run_id and authorization.get("output_revision") == base_revision)
        if authorized:
            if asset.get('storage') == 'file':
                for item in base.get('original_versions', []):
                    if (item.get('path') and _path_key(item['path']) == _path_key(asset['canonical_uri']) and
                            digest == item.get('sha256') and result['task_id'] == task_id and result['task_run_id'] == base_run_id):
                        return {**result, 'context_basis': 'human_rework_working_copy'}
            for item in base.get("files", []):
                by_asset = item.get("asset_id") and item["asset_id"] == asset.get("id")
                by_copy = asset.get("storage") == "file" and any(item.get(key) and _path_key(item[key]) == _path_key(asset["canonical_uri"])
                                                                               for key in ("path", "original_path"))
                if digest and digest == item.get("content_hash", item.get("sha256")) and (by_asset or by_copy):
                    return {**result, "context_basis": "human_rework"}
    raise ValueError("该资料属于未交付、退回或已替代的任务产物，不能作为当前专家的默认依据")


def context_available(db, asset, *, index=None):
    try:
        require_context(db, asset, index=index)
        return True
    except (ValueError, OSError):
        return False


def path_asset(db, project_id, path, *, index=None):
    index = index if index is not None else eligibility_index(db, project_id)
    source = Path(path).resolve()
    asset = index["source_paths"].get(_path_key(source))
    if asset:
        return asset
    row = _conn(db).execute("SELECT * FROM assets WHERE canonical_uri=?", (str(source),)).fetchone()
    if row:
        return dict(row)
    controlled = _path_key(source) in index["paths"] or ".tasks" in source.parts
    return {"id": "", "project_id": project_id, "kind": "file", "storage": "file", "status": "active",
            "canonical_uri": str(source), "metadata": {}, "content_hash": sha256_file(source) if controlled and source.is_file() else ""}


def require_path_context(db, project_id, path, *, index=None):
    index = index if index is not None else eligibility_index(db, project_id)
    asset = path_asset(db, project_id, path, index=index)
    if asset.get("project_id") != project_id:
        index = eligibility_index(db, asset.get("project_id", ""))
    return require_context(db, asset, index=index)


def path_context_available(db, project_id, path, *, index=None):
    try:
        require_path_context(db, project_id, path, index=index)
        return True
    except (ValueError, OSError):
        return False


def legacy_context_available(db, project_id, kind, legacy_id, *, index=None):
    index = index if index is not None else eligibility_index(db, project_id)
    kind = "note" if kind in ("md_note", "big-note", "proj", "md") else kind
    return all(context_available(db, asset, index=index) for asset in index["legacy"].get((kind, str(legacy_id)), []))


def research_assets(db, project_id, limit=200, *, allow_draft=False):
    index = eligibility_index(db, project_id)
    return [a for a in list_assets(db, project_id, limit=1000)
            if a["project_id"] == project_id and a["status"] == "active" and not is_instruction_asset(a)
            and (allow_draft or eligibility(db, a, index=index)["consumable"])][:limit]


def is_instruction_note(db, project_id, note_id, content):
    import note_catalog
    if note_catalog.is_template(content or ""):
        return True
    rows = _conn(db).execute("SELECT * FROM assets WHERE project_id=? AND kind='note' AND status='active' "
                             "AND json_extract(metadata,'$.legacy_id')=?", (project_id or "", note_id)).fetchall()
    return any(is_instruction_asset(dict(row)) for row in rows)


def register(db, *, project_id: str = "", kind: str, title: str = "", canonical_uri: str,
             storage: str = "file", mime: str = "", content_hash: str = "",
             source: str = "local", metadata: dict[str, Any] | None = None,
             body: str = "") -> dict[str, Any]:
    conn = _conn(db)
    stamp = _now(db)
    asset_id = "asset-" + uuid.uuid4().hex
    previous = conn.execute("SELECT content_hash FROM assets WHERE canonical_uri=?", (canonical_uri,)).fetchone()
    payload = {"title": title or "", "metadata": _metadata(metadata), "body": body or ""}
    if storage != "file":
        content_hash = sha256_bytes(_json(payload).encode("utf-8"))
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
    if storage != "file":
        content_uri = "data:application/json;base64," + base64.b64encode(_json(payload).encode("utf-8")).decode("ascii")
        add_revision(db, asset["id"], content_uri, content_hash, created_by=source,
                     message="virtual content snapshot", force=bool(previous and previous["content_hash"] != content_hash))
        asset = get(db, asset["id"]) or asset
    return asset


def virtual_snapshot(db, asset):
    """Freeze real non-file content, including legacy assets with an empty stored hash."""
    if asset.get("storage") == "file":
        raise ValueError("文件资产请使用文件快照")
    row = _conn(db).execute("SELECT body FROM asset_fts WHERE asset_id=? LIMIT 1", (asset["id"],)).fetchone()
    payload = {"title": asset.get("title", ""), "metadata": _metadata(asset.get("metadata")), "body": row["body"] if row else ""}
    return {**payload, "content_hash": sha256_bytes(_json(payload).encode("utf-8")),
            "revision_id": asset.get("current_revision_id", "")}


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
    existing = _conn(db).execute("SELECT content_hash,metadata FROM assets WHERE canonical_uri=?", (str(target),)).fetchone()
    metadata = {**(_metadata(existing["metadata"]) if existing else {}), **(metadata or {})}
    task_context = getattr(db, "project_task_context", None)
    task_id = getattr(task_context, "parent_task_id", "")
    if task_id:
        task = _conn(db).execute("SELECT latest_run_id FROM project_tasks WHERE id=? AND project_id=?", (task_id, project_id)).fetchone()
        if task and task["latest_run_id"]:
            metadata.update(project_task_id=task_id, task_run_id=task["latest_run_id"])
    if kind == "note":
        import note_catalog
        raw = target.read_text(encoding="utf-8")
        root = db.base_dir(project_id) if hasattr(db, "base_dir") and project_id else None
        relative = target.relative_to(root.resolve()).as_posix() if root and target.is_relative_to(root.resolve()) else ""
        metadata.update(note_catalog.attributes(raw, relative, note_catalog.load(db, project_id) if root else None))
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
    merged = {"legacy_id": legacy_id, **(metadata or {})}
    return register(db, project_id=project_id, kind=kind, title=title,
                    canonical_uri=uri, storage="sqlite", source="legacy_rebuild", metadata=merged, body=body)


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


@project_notes.synchronized
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
