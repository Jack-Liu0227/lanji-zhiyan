"""Versioned schema migrations for SQLite databases."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
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


def backup_database(db_path: Path) -> Path | None:
    if not db_path.is_file() or str(db_path) == ":memory:":
        return None
    backup_path = db_path.with_name(f"{db_path.name}.bak-{time.time_ns()}")
    try:
        source_uri = db_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)
        return backup_path
    except (OSError, sqlite3.Error):
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


_ADD_COLUMN_RX = re.compile(r"^\s*ALTER\s+TABLE\s+\"?(\w+)\"?\s+ADD\s+COLUMN\s+\"?(\w+)\"?", re.I)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        return any(r[1] == column for r in conn.execute(f'PRAGMA table_info("{table}")'))
    except sqlite3.Error:
        return False


def skip_existing_columns(conn: sqlite3.Connection, sql: str) -> str:
    """老库里已存在的列，让 ADD COLUMN 那句失效而不是整条迁移炸掉。

    1.0 早期版本（工具箱那轮）用 PRAGMA 补过 chats.channel / model_id / reasoning / permission，
    2.0 的 024+ 迁移也 ADD 同名列 → duplicate column name 会让整个迁移链停在半路，库卡在中间版本。
    只处理「一行一条 ALTER TABLE … ADD COLUMN」的写法（本项目所有迁移都这么写），别的语句原样保留。
    """
    out = []
    for line in sql.splitlines():
        m = _ADD_COLUMN_RX.match(line)
        if m and _has_column(conn, m.group(1), m.group(2)):
            out.append("-- skip existing column: " + line.strip())
        else:
            out.append(line)
    return "\n".join(out)


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None,
                   db_path: Path | None = None, create_backup: bool = False) -> list[str]:
    mdir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    if not mdir.is_dir() or not any(mdir.glob("*.sql")):
        raise FileNotFoundError(f"Database migration scripts are missing: {mdir}")

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
        if backup_database(db_path) is None:
            raise RuntimeError(f"Database backup failed before migration: {db_path}")

    applied_now = []
    for version, fpath, checksum in pending:
        sql = skip_existing_columns(conn, fpath.read_text(encoding="utf-8"))
        try:
            # executescript commits a pending transaction before running its SQL.
            conn.executescript("BEGIN IMMEDIATE;\n" + sql)
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
