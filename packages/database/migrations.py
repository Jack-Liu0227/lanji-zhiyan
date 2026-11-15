"""Versioned schema migrations for SQLite databases."""
from __future__ import annotations

import hashlib

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

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


def backup_database(db_path: Path, conn: sqlite3.Connection | None = None) -> Path | None:
    """Write an integrity-checked SQLite snapshot; never silently skip a failed backup."""
    if not db_path.is_file() or str(db_path) == ":memory:":
        return None
    backup_path = db_path.with_name(f"{db_path.name}.bak-{time.time_ns()}")
    temp_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
    own_conn = conn is None
    source = conn or sqlite3.connect(db_path, timeout=60.0)
    target = None
    try:
        if source.in_transaction:
            raise RuntimeError("Cannot back up a database with an open transaction")
        target = sqlite3.connect(temp_path)
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {result!r}")
        target.close()
        target = None
        temp_path.replace(backup_path)
        return backup_path
    except Exception:
        if target is not None:
            target.close()
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if own_conn:
            source.close()


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None,
                   db_path: Path | None = None, create_backup: bool = True) -> list[str]:
    mdir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not mdir.is_dir() or not any(mdir.glob("*.sql")):
        raise FileNotFoundError(f"Database migration scripts are missing: {mdir}")

    migration_files = sorted(mdir.glob("*.sql"), key=lambda p: p.name)
    checksums = {path.stem: compute_checksum(path) for path in migration_files}
    has_migration_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    applied = {}
    if has_migration_table:
        rows = conn.execute("SELECT version, checksum, applied_at FROM schema_migrations").fetchall()
        applied = {row[0]: {"checksum": row[1], "applied_at": row[2]} for row in rows}

    removed = set(applied) - set(checksums)
    if removed:
        raise RuntimeError(f"Applied migrations are missing from disk: {', '.join(sorted(removed))}")
    for version, record in applied.items():
        if record["checksum"] != checksums[version]:
            raise RuntimeError(f"Migration checksum mismatch: {version}")

    pending = [(version, path, checksums[version]) for version, path in
               ((p.stem, p) for p in migration_files) if version not in applied]
    if not pending:
        return []

    if create_backup and db_path is not None and db_path.is_file():
        backup_database(db_path, conn)

    if not has_migration_table:
        ensure_migration_table(conn)

    applied_now = []
    for version, fpath, checksum in pending:
        sql = fpath.read_text(encoding="utf-8")
        try:
            # Executes the whole migration under one SQLite transaction, including its ledger row.
            conn.executescript("BEGIN IMMEDIATE;\n" + sql + "\n")
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
