"""Versioned schema migrations for SQLite databases."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:                                    # 程序根只有一处来源；导入早期也能兜底
    from paths import app_dir

    ROOT = app_dir()
except ImportError:
    ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATIONS_DIR = ROOT / "migrations"


def compute_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version TEXT PRIMARY KEY,
      checksum TEXT NOT NULL,
      applied_at TEXT NOT NULL
    );
    """)
    conn.commit()


def get_applied_migrations(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    ensure_migration_table(conn)
    rows = conn.execute("SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall()
    return {r[0]: {"checksum": r[1], "applied_at": r[2]} for r in rows}


def backup_database(db_path: Path) -> Path | None:
    if not db_path.is_file() or str(db_path) == ":memory:":
        return None
    backup_path = db_path.with_name(f"{db_path.name}.bak-{int(time.time())}")
    try:
        shutil.copy2(db_path, backup_path)
        return backup_path
    except OSError:
        return None


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None,
                   db_path: Path | None = None, create_backup: bool = False) -> list[str]:
    mdir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not mdir.is_dir():
        return []

    ensure_migration_table(conn)
    applied = get_applied_migrations(conn)

    migration_files = sorted(mdir.glob("*.sql"), key=lambda p: p.name)
    pending: list[tuple[str, Path, str]] = []

    for f in migration_files:
        version = f.stem
        checksum = compute_checksum(f)
        if version not in applied:
            pending.append((version, f, checksum))

    if not pending:
        return []

    if create_backup and db_path:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        backup_database(db_path)

    applied_now = []
    for version, fpath, checksum in pending:
        sql = fpath.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
                (version, checksum, now_iso),
            )
            conn.commit()
            applied_now.append(version)
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"Migration failed at {fpath.name}: {exc}") from exc

    return applied_now
