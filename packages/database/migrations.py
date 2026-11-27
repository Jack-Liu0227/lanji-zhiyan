"""Versioned schema migrations for SQLite databases."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import zipfile

try:                                    # 程序根只有一处来源；导入早期也能兜底
    from paths import app_dir

    ROOT = app_dir()
except ImportError:
    ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATIONS_DIR = ROOT / "migrations"
DEFAULT_ARCHIVE_DIR = ROOT / "archive" / "migrations"
_LEGACY_NAME = re.compile(r"^\d{3}_[a-zA-Z0-9_]+$")
_PATCH_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_DEPENDS = re.compile(r"^\s*--\s*depends_on:\s*(.*?)\s*$", re.I)


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str
    raw_bytes: bytes
    sql: str
    checksum: str
    is_named: bool = False
    effective_signature: str = ""
    depends_on: tuple[str, ...] = ()


def _migration(filename: str, raw: bytes) -> Migration:
    version = Path(filename).stem
    named = not bool(_LEGACY_NAME.fullmatch(version))
    if named and not _PATCH_NAME.fullmatch(version):
        raise ValueError(f"Invalid migration name: {filename}")
    sql = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    dependencies = []
    if named:
        for line in sql.splitlines():
            if line.strip() and not line.lstrip().startswith("--"):
                break
            match = _DEPENDS.match(line)
            if match:
                dependencies.extend(part for part in re.split(r"[,\s]+", match[1]) if part)
        if len(set(dependencies)) != len(dependencies) or any(
            not _PATCH_NAME.fullmatch(name) for name in dependencies
        ):
            raise ValueError(f"Invalid or duplicate dependencies: {filename}")
    checksum = hashlib.sha256(sql.encode("utf-8") if named else raw).hexdigest()
    return Migration(version, filename, raw, sql, checksum, named, depends_on=tuple(dependencies))


def _archive_migrations(directory: Path) -> list[Migration]:
    manifest_path, archive_path = directory / "manifest.json", directory / "001-046.zip"
    if not manifest_path.is_file() or not archive_path.is_file():
        raise FileNotFoundError(f"Database migration scripts are missing: {directory}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != 1 or manifest.get("archive") != archive_path.name:
            raise ValueError("Invalid migration archive format")
        if compute_checksum(archive_path) != manifest.get("sha256"):
            raise ValueError("Migration archive checksum mismatch")
        rows = manifest.get("migrations")
        if not isinstance(rows, list) or len(rows) != 46:
            raise ValueError("Historical migration archive must contain versions 001 through 046")
        expected = []
        for number, row in enumerate(rows, 1):
            filename = row["filename"]
            version = row["version"]
            if (not isinstance(filename, str) or filename != version + ".sql"
                    or not _LEGACY_NAME.fullmatch(version)
                    or not version.startswith(f"{number:03d}_")
                    or type(row["size"]) is not int or not 0 <= row["size"] <= 4 * 1024 * 1024):
                raise ValueError("Invalid historical migration archive member")
            expected.append(filename)
        result = []
        with zipfile.ZipFile(archive_path) as bundle:
            infos = bundle.infolist()
            if len(infos) != len(expected) or sorted(info.filename for info in infos) != sorted(expected):
                raise ValueError("Migration archive has missing, duplicate or unexpected members")
            for row in rows:
                info = bundle.getinfo(row["filename"])
                if info.file_size != row["size"]:
                    raise ValueError("Migration archive member size mismatch")
                raw = bundle.read(info)
                if hashlib.sha256(raw).hexdigest() != row["checksum"]:
                    raise ValueError("Migration archive member checksum mismatch")
                result.append(_migration(row["filename"], raw))
        return result
    except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError,
            zipfile.BadZipFile, OSError) as exc:
        raise ValueError(f"Invalid migration archive: {directory}: {exc}") from exc


def load_migrations(migrations_dir: Path | None = None,
                    archive_dir: Path | None = None) -> list[Migration]:
    directory = Path(migrations_dir) if migrations_dir is not None else DEFAULT_MIGRATIONS_DIR
    default = directory.resolve() == DEFAULT_MIGRATIONS_DIR.resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Database migration scripts are missing: {directory}")
    entries = _archive_migrations(Path(archive_dir) if archive_dir is not None else DEFAULT_ARCHIVE_DIR) \
        if default or archive_dir is not None else []
    entries.extend(_migration(path.name, path.read_bytes()) for path in sorted(directory.glob("*.sql")))
    if not entries:
        raise FileNotFoundError(f"Database migration scripts are missing: {directory}")
    by_version = {}
    legacy_numbers = set()
    for entry in entries:
        if entry.version in by_version:
            raise ValueError(f"Duplicate migration: {entry.version}")
        by_version[entry.version] = entry
        if not entry.is_named:
            number = entry.version[:3]
            if number in legacy_numbers:
                raise ValueError(f"Duplicate historical migration number: {number}")
            legacy_numbers.add(number)
    ordered = sorted((entry for entry in entries if not entry.is_named), key=lambda entry: entry.filename)
    patches = {entry.version: entry for entry in entries if entry.is_named}
    visited, visiting = set(), set()

    def visit(name: str) -> Migration:
        if name in visiting:
            raise ValueError(f"Migration dependency cycle: {name}")
        if name not in patches:
            raise ValueError(f"Missing named migration dependency: {name}")
        if name in visited:
            return patches[name]
        visiting.add(name)
        entry = patches[name]
        dependencies = [visit(dependency) for dependency in sorted(entry.depends_on)]
        signature = hashlib.sha256(json.dumps(
            {"checksum": entry.checksum,
             "depends": [(dependency.version, dependency.effective_signature) for dependency in dependencies]},
            separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
        entry = Migration(entry.version, entry.filename, entry.raw_bytes, entry.sql,
                          entry.checksum, True, signature, entry.depends_on)
        patches[name] = entry
        visiting.remove(name)
        visited.add(name)
        ordered.append(entry)
        return entry

    for name in sorted(patches):
        visit(name)
    return ordered


def read_migration_sql(version: str, migrations_dir: Path | None = None,
                       archive_dir: Path | None = None) -> str:
    for entry in load_migrations(migrations_dir, archive_dir):
        if entry.version == version:
            return entry.sql
    raise FileNotFoundError(f"Database migration script is missing: {version}")


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


def _execute_sql(conn: sqlite3.Connection, sql: str) -> None:
    # executescript commits the caller's transaction. Execute complete statements instead.
    statement = ""
    for character in sql:
        statement += character
        if character == ";" and sqlite3.complete_statement(statement):
            _execute_statement(conn, statement)
            statement = ""
    if statement.strip():
        _execute_statement(conn, statement)


def _execute_statement(conn: sqlite3.Connection, statement: str) -> None:
    head = re.sub(r"^(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", statement, flags=re.S)
    if re.match(r"(?:BEGIN|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE|VACUUM|ATTACH|DETACH)\b", head, re.I):
        raise ValueError("Migration SQL must not control transactions or attach databases")
    conn.execute(statement)


def _patch_signature(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT effective_signature FROM schema_patch_runs WHERE name=? "
                       "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    return row[0] if row else None


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None,
                   db_path: Path | None = None, create_backup: bool = False,
                   archive_dir: Path | None = None) -> list[str]:
    entries = load_migrations(migrations_dir, archive_dir)
    ensure_migration_table(conn)
    named = {entry.version: entry for entry in entries if entry.is_named}
    if named:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_patch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, checksum TEXT NOT NULL,
            effective_signature TEXT NOT NULL, applied_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_schema_patch_runs_name ON schema_patch_runs(name, id)")
        conn.commit()
    pending = any(
        _patch_signature(conn, entry.version) != entry.effective_signature if entry.is_named else
        conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (entry.version,)).fetchone() is None
        for entry in entries)
    if not pending:
        return []
    if create_backup and db_path and backup_database(db_path) is None:
        raise RuntimeError(f"Database backup failed before migration: {db_path}")
    applied_now = []
    for entry in entries:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if entry.is_named:
                for dependency in entry.depends_on:
                    if _patch_signature(conn, dependency) != named[dependency].effective_signature:
                        raise ValueError(f"Migration dependency changed during execution: {dependency}")
                current = _patch_signature(conn, entry.version)
                already_applied = current == entry.effective_signature
            else:
                already_applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                                               (entry.version,)).fetchone() is not None
            if already_applied:
                conn.commit()
                continue
            _execute_sql(conn, skip_existing_columns(conn, entry.sql))
            now_iso = datetime.now(timezone.utc).isoformat()
            if entry.is_named:
                conn.execute("INSERT INTO schema_patch_runs(name,checksum,effective_signature,applied_at) "
                             "VALUES (?,?,?,?)", (entry.version, entry.checksum, entry.effective_signature, now_iso))
            else:
                conn.execute("INSERT INTO schema_migrations(version,checksum,applied_at) VALUES (?,?,?)",
                             (entry.version, entry.checksum, now_iso))
            conn.commit()
            applied_now.append(entry.version)
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"Migration failed at {entry.filename}: {exc}") from exc
    return applied_now
