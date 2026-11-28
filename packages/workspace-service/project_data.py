"""Storage locations and the information shown before project deletion."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3

import paths

EXECUTION_TABLES = (
    "execution_board_events", "execution_board_hidden", "execution_conversations",
    "model_requests", "audit_events", "workflow_runs",
)


def delete(db, project_id: str, *, require_idle: bool) -> dict:
    """Delete database records and keep files in place."""
    import memory_store
    import semantic

    memory_store.ready(db)  # Schema initialization may commit.
    conn = db.db
    memory_store.ensure_fts_healthy(conn, commit=True)
    if not require_idle and conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        _cancel_legacy_runs(db, project_id)  # Schedule cancellation uses a separate connection.
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with db.transaction():
            conn.execute("BEGIN IMMEDIATE")
            project = preview(db, project_id)
            if require_idle and project["running"]:
                raise ValueError("请先停止项目中正在运行或排队的任务，再删除项目")
            try:
                semantic.invalidate_project(db, project_id)
            except sqlite3.DatabaseError:
                memory_store.repair_fts_table(conn, "sem_fts")
                semantic.invalidate_project(db, project_id)
            _delete_records(conn, project_id)
            conn.execute(
                "INSERT INTO deleted_project_locations VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                (project_id, project["title"], project["base_dir"]),
            )
            # Cascading foreign keys cover the modern task records and their descendants.
            conn.execute("DELETE FROM research_tasks WHERE id=?", (project_id,))
    finally:
        if not foreign_keys:
            conn.execute("PRAGMA foreign_keys=OFF")
    return {"deleted": True, "id": project_id, "files_removed": 0}


def _cancel_legacy_runs(db, project_id: str) -> None:
    import schedule
    import team
    from run_manager import RunManager

    manager = RunManager.get_instance()
    args = (project_id,)
    for row in db.db.execute("SELECT id FROM tasks WHERE owner_id=?", args):
        manager.cancel_run(row["id"])
    for row in db.db.execute("SELECT id FROM schedules WHERE project_id=?", args):
        schedule.cancel_run(row["id"], db.path)
    for row in db.db.execute("SELECT id FROM team_tasks WHERE task_id=?", args):
        signal = team._cancel_signals.get(row["id"])
        if signal:
            signal.set()


def _delete_records(conn, project_id: str) -> None:
    args = (project_id,)
    execution_chat_ids = "SELECT chat_id FROM execution_conversations WHERE project_id=?1"
    run_ids = f"SELECT id FROM tasks WHERE owner_id=?1 OR owner_id IN ({execution_chat_ids})"
    conn.execute(f"DELETE FROM task_attempts WHERE task_id IN ({run_ids})", args)
    conn.execute(f"DELETE FROM run_events WHERE run_id IN ({run_ids})", args)
    conn.execute(f"DELETE FROM tasks WHERE id IN ({run_ids})", args)
    schedule_ids = "SELECT id FROM schedules WHERE project_id=?"
    conn.execute(f"DELETE FROM schedule_workflows WHERE schedule_id IN ({schedule_ids})", args)
    conn.execute(f"DELETE FROM schedule_runs WHERE schedule_id IN ({schedule_ids})", args)
    conn.execute("DELETE FROM schedules WHERE project_id=?", args)
    paper_ids = "SELECT id FROM papers WHERE task_id=?"
    conn.execute(f"DELETE FROM paper_evidence_recheck WHERE evidence_id IN (SELECT id FROM evidence WHERE paper_id IN ({paper_ids}))", args)
    for table in ("paper_pages", "evidence"):
        conn.execute(f"DELETE FROM {table} WHERE paper_id IN ({paper_ids})", args)
    import memory_store
    for table in (
        "papers", "artifacts", "runs", "calendar_events", "notes", "steps", "search_cache", "chat",
        "paper_graph", "kb_graph", "project_memory", "md_notes", "dl_queue", "team_tasks", "team_events",
        "zotero_bindings", "zotero_project_sources", "zotero_project_memberships", "zotero_link_batches",
        "project_paper_links", "project_todos", "paper_zotero_refs", "zotero_save_plans",
        "zotero_pending_writes", "zotero_downloads",
        "background_jobs", "idea_input", "tr_hl", "zotero_staging",
    ):
        conn.execute(f"DELETE FROM {table} WHERE task_id=?", args)
    memory_store.safe_delete_fts(conn, "chat_fts", "task_id=?", args)
    # These legacy tables are created on demand.
    for table in ("project_workflow_layouts", "zotero_item_links"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            conn.execute(f"DELETE FROM {table} WHERE task_id=?", args)
    for table in ("task_links", "research_setups"):
        conn.execute(f"DELETE FROM {table} WHERE project_id=?", args)
    project_memory_ids = "SELECT id FROM memory WHERE scope='project' AND scope_id=?"
    conn.execute(f"DELETE FROM memory_revisions WHERE memory_id IN ({project_memory_ids})", args)
    memory_store.safe_delete_fts(conn, "memory_fts", f"mem_id IN ({project_memory_ids})", args)
    conn.execute("DELETE FROM memory WHERE scope='project' AND scope_id=?", args)
    _delete_execution_records(conn, project_id)
    asset_ids = "SELECT id FROM assets WHERE project_id=?"
    memory_store.safe_delete_fts(conn, "asset_fts", f"asset_id IN ({asset_ids})", args)
    for table in ("asset_provenance", "asset_revisions"):
        conn.execute(f"DELETE FROM {table} WHERE asset_id IN ({asset_ids})", args)
    conn.execute(f"DELETE FROM asset_relations WHERE from_asset_id IN ({asset_ids}) "
                 f"OR to_asset_id IN ({asset_ids})", (project_id, project_id))
    conn.execute("DELETE FROM assets WHERE project_id=?", args)
    conn.execute("UPDATE chats SET task_id='' WHERE task_id=?", args)
    conn.execute("UPDATE teams SET project_id='' WHERE project_id=?", args)


def _delete_execution_records(conn, project_id: str) -> None:
    args = (project_id,)
    workflow_ids = "SELECT id FROM workflow_runs WHERE project_id=?"
    conn.execute("DELETE FROM review_reminders WHERE node_id IN "
                 f"(SELECT id FROM workflow_nodes WHERE run_id IN ({workflow_ids}))", args)
    conn.execute(f"DELETE FROM workflow_nodes WHERE run_id IN ({workflow_ids})", args)
    chat_ids = ("SELECT c.id FROM chats c JOIN execution_conversations e ON e.chat_id=c.id "
                "WHERE e.project_id=?1 AND c.task_id IN ('',?1)")
    import memory_store
    conn.execute(f"DELETE FROM chat WHERE task_id IN ({chat_ids})", args)
    memory_store.safe_delete_fts(conn, "chat_fts", f"task_id IN ({chat_ids})", args)
    conn.execute(f"DELETE FROM chats WHERE id IN ({chat_ids})", args)
    for table in EXECUTION_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE project_id=?", args)


def preview(db, project_id: str) -> dict:
    conn = db.db
    project = conn.execute(
        "SELECT id,title,base_dir FROM research_tasks WHERE id=?", (project_id,)
    ).fetchone()
    if project is None:
        raise KeyError(project_id)
    ordinary = conn.execute(
        "SELECT count(*) FROM chats WHERE task_id=? AND id NOT IN "
        "(SELECT chat_id FROM execution_conversations WHERE project_id=?)",
        (project_id, project_id),
    ).fetchone()[0]
    running = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM project_tasks t WHERE t.project_id=? "
        "AND t.status IN ('queued','running')) OR "
        "EXISTS(SELECT 1 FROM workflow_runs WHERE project_id=? AND status IN ('queued','running')) OR "
        "EXISTS(SELECT 1 FROM tasks WHERE status IN ('queued','running') AND "
        "(owner_id=? OR owner_id IN (SELECT id FROM chats WHERE task_id=?))) OR "
        "EXISTS(SELECT 1 FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id "
        "WHERE s.project_id=? AND r.status='running') OR "
        "EXISTS(SELECT 1 FROM team_tasks WHERE task_id=? AND status='running')",
        (project_id,) * 6,
    ).fetchone()[0]
    return {**dict(project), "conversations": ordinary, "running": bool(running)}


def align_legacy_default_directory(db, project_id: str, *, title: str | None = None) -> dict:
    """Move an ID-named default root to a title-named root and retain its old path."""
    import hashlib
    import json
    import os
    import tempfile
    import workflow_search

    row = db.db.execute(
        "SELECT id,title,base_dir FROM research_tasks WHERE id=?", (project_id,)
    ).fetchone()
    if not row:
        raise KeyError(project_id)
    if not re.fullmatch(r"[0-9a-f]{32}", project_id):
        return {"migrated": False, "base_dir": str(row["base_dir"] or "")}
    old_root = db.path.resolve().parent / "projects" / project_id
    journal = db.path.with_name(
        f".{db.path.name}.directory-migration-{hashlib.sha256(project_id.encode()).hexdigest()[:16]}.json"
    )
    raw_current = str(row["base_dir"] or "").strip()
    has_journal = journal.exists()
    if not raw_current and not has_journal:
        return {"migrated": False, "base_dir": ""}
    current = old_root if has_journal else Path(raw_current).expanduser()
    expected_old = os.path.normcase(str(old_root.absolute()))
    if not has_journal and os.path.normcase(str(current.absolute())) != expected_old:
        return {"migrated": False, "base_dir": str(current)}
    if os.name != "nt":
        raise OSError("旧默认目录迁移需要 Windows junction 支持")
    import _winapi

    if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
        if not journal.exists():
            return {"migrated": False, "base_dir": str(current)}

    def finish(destination: Path) -> None:
        if (destination.parent != old_root.parent or destination.is_symlink() or
                getattr(destination, "is_junction", lambda: False)() or not destination.is_dir()):
            raise ValueError("舊目錄遷移記錄中的目標位置無效")
        if current.exists() and not getattr(current, "is_junction", lambda: False)():
            raise ValueError("舊路徑與新目錄同時存在，請先檢查遷移記錄")
        if not current.exists():
            _winapi.CreateJunction(str(destination), str(current))
        elif current.resolve() != destination.resolve():
            raise ValueError("舊路徑鏈接指向其他目錄，未修改項目路徑")
        old_prefix, new_prefix = str(current.absolute()), str(destination.absolute())
        with db.db:
            db.db.execute("UPDATE research_tasks SET base_dir=? WHERE id=?", (new_prefix, project_id))
            for asset in db.db.execute(
                "SELECT id,canonical_uri FROM assets WHERE project_id=? AND storage='file'", (project_id,)
            ).fetchall():
                uri = asset["canonical_uri"]
                if os.path.normcase(uri).startswith(os.path.normcase(old_prefix + os.sep)):
                    updated = new_prefix + uri[len(old_prefix):]
                    db.db.execute("UPDATE assets SET canonical_uri=? WHERE id=?", (updated, asset["id"]))

    if journal.exists():
        try:
            intent = json.loads(journal.read_text(encoding="utf-8"))
            destination = Path(intent["destination"])
            if intent.get("project_id") != project_id or Path(intent.get("source", "")) != old_root:
                raise ValueError("舊目錄遷移記錄與項目不匹配")
            if destination.parent != old_root.parent:
                raise ValueError("舊目錄遷移記錄指向項目目錄之外")
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("無法讀取舊目錄遷移記錄，未更改文件") from exc
        if destination.is_dir() and (
                not current.exists() or getattr(current, "is_junction", lambda: False)()):
            finish(destination)
            journal.unlink(missing_ok=True)
            return {"migrated": True, "base_dir": str(destination), "legacy_alias": str(current)}
        if current.is_dir() and not getattr(current, "is_junction", lambda: False)():
            if destination.is_dir() and not any(destination.iterdir()):
                destination.rmdir()
            elif destination.exists():
                raise ValueError("舊目錄和目標目錄都有內容，請先檢查兩邊文件")
            journal.unlink()
        else:
            raise ValueError("舊目錄遷移狀態不完整，請先檢查項目目錄")

    if os.path.normcase(str(current.absolute())) != expected_old:
        return {"migrated": False, "base_dir": str(current)}
    if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
        raise ValueError("舊默認目錄是鏈接，不能自動遷移")
    if not current.is_dir():
        raise ValueError("舊默認目錄不存在，未修改項目路徑")
    paths.validate_project_dir(current)
    if preview(db, project_id)["running"]:
        raise ValueError("項目仍有運行或排隊任務，不能遷移目錄")
    new_title = str(title if title is not None else row["title"]).strip()
    destination = workflow_search.reserve_default_project_root(db, new_title)
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(dir=journal.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"project_id": project_id, "source": str(current),
                       "destination": str(destination)}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, journal)
        temp_name = None
        destination.rmdir()
        os.replace(current, destination)
        finish(destination)
        try:
            journal.unlink()
        except OSError:
            pass
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        try:
            if getattr(current, "is_junction", lambda: False)():
                os.rmdir(current)
            if destination.is_dir() and not current.exists():
                os.replace(destination, current)
            journal.unlink(missing_ok=True)
            if current.is_dir() and destination.is_dir() and not any(destination.iterdir()):
                destination.rmdir()
        except OSError:
            pass
        raise
    return {"migrated": True, "base_dir": str(destination), "legacy_alias": str(current)}


def locations(db) -> dict:
    """Use the database opened by the running app, including legacy fallback."""
    database = db.path.resolve()
    paths_by_kind = {
        "data": paths.DATA.resolve(),
        "database": database,
        "projects": database.parent / "projects",
        "backups": paths.BACKUP.resolve(),
    }
    rows = db.db.execute(
        "SELECT project_id,title,base_dir,deleted_at FROM deleted_project_locations "
        "WHERE base_dir<>'' ORDER BY deleted_at DESC, project_id"
    )
    return {
        "paths": {kind: str(path) for kind, path in paths_by_kind.items()},
        "retained_projects": [dict(row) for row in rows],
    }


def historical_locations(db) -> list[dict]:
    """Find missing projects in local database snapshots, without importing any data."""
    database = db.path.resolve()
    pattern = re.compile(re.escape(database.name) + r"\.bak-\d+(?:-\d+)?")
    backups = [file for folder in {database.parent, paths.BACKUP.resolve()}
               for file in folder.glob(f"{database.name}.bak-*")
               if pattern.fullmatch(file.name) and file.is_file()]
    known = {row[0] for row in db.db.execute("SELECT id FROM research_tasks UNION SELECT project_id FROM deleted_project_locations")}
    found = []
    for file in sorted(backups, reverse=True)[:20]:
        try:
            with closing(sqlite3.connect(file.as_uri() + "?mode=ro&immutable=1", uri=True)) as history:
                rows = history.execute("SELECT id,title,base_dir FROM research_tasks WHERE base_dir<>''").fetchall()
        except sqlite3.Error:
            continue
        for project_id, title, base_dir in rows:
            if project_id not in known:
                known.add(project_id)
                count = sum(db.db.execute(f"SELECT count(*) FROM {table} WHERE project_id=?", (project_id,)).fetchone()[0]
                            for table in EXECUTION_TABLES)
                found.append({"project_id": project_id, "title": title, "base_dir": base_dir, "execution_records": count})
    return found


def clean_historical_execution(db, project_id: str) -> None:
    if not any(item["project_id"] == project_id for item in historical_locations(db)):
        raise ValueError("近期备份中未找到这个已删除的项目")
    conn = db.db
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with db.transaction():
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
                raise ValueError("项目仍在使用，无法清理旧执行记录")
            _delete_execution_records(conn, project_id)
    finally:
        if not foreign_keys:
            conn.execute("PRAGMA foreign_keys=OFF")


def open_location(db, kind: str, project_id: str = "") -> None:
    if kind == "project":
        row = db.db.execute(
            "SELECT base_dir FROM research_tasks WHERE id=? UNION ALL "
            "SELECT base_dir FROM deleted_project_locations WHERE project_id=? LIMIT 1",
            (project_id, project_id),
        ).fetchone()
        raw = row[0] if row else ""
    else:
        raw = locations(db)["paths"].get(kind, "")
    if not raw:
        raise ValueError("没有可打开的位置")
    target = Path(raw)
    if kind == "database":
        target = target.parent
    if not target.is_absolute() or not target.is_dir():
        raise ValueError("文件夹不存在或当前不可访问")
    if not paths.host_open(target):
        raise ValueError("无法打开文件夹，可复制路径到资源管理器")


def forget_location(db, project_id: str) -> None:
    with db.transaction():
        db.db.execute("DELETE FROM deleted_project_locations WHERE project_id=?", (project_id,))
