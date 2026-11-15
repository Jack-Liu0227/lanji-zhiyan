"""Project Markdown notebook with a strict project directory boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from functools import wraps
import json
import os
import threading
import uuid
import weakref
import note_catalog


_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def synchronized(operation):
    """Serialize note files, metadata and indexes across connections to one DB.

    The database scope also covers folder deletion and project rebinding, which
    can change a note's canonical path while another editor is saving it.
    """
    @wraps(operation)
    def run(db, *args, **kwargs):
        key = os.path.normcase(str(Path(db.path).resolve())) if str(db.path) != ":memory:" else id(db.db)
        with _LOCKS_GUARD:
            lock = _LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _LOCKS[key] = lock
        with lock:
            return operation(db, *args, **kwargs)
    return run


CATEGORIES = tuple((name, description) for _, name, description, _ in note_catalog.DEFAULTS)
MAX_NOTE_BYTES = 5 * 1024 * 1024


def project_root(db: Any, project_id: str, *, writable: bool = False) -> Path:
    import paths

    root = db.base_dir(str(project_id or ""))
    if root is None or not root.is_dir():
        raise ValueError("请选择一个已设置文件夹的项目")
    return paths.validate_project_dir(root) if writable else root.resolve()


def _write_restriction(root: Path) -> str:
    import paths

    try:
        paths.validate_project_dir(root)
    except ValueError as exc:
        return str(exc)
    return ""


def ensure_categories(db: Any, project_id: str) -> Path:
    root = project_root(db, project_id, writable=True)
    notes = root / "笔记"
    if not notes.resolve().is_relative_to(root):
        raise ValueError("笔记目录越过项目文件夹")
    notes.mkdir(exist_ok=True)
    catalog = note_catalog.initialize(db, project_id)
    for category in catalog["categories"]:
        name = category["name"]
        target = folder_path(db, project_id, category["folder"])
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise ValueError(f"笔记分类目录不可用：{name}")
        target.mkdir(parents=True, exist_ok=True)
    return notes


def note_path(db: Any, project_id: str, relative: str, *, create: bool = False) -> Path:
    root = project_root(db, project_id, writable=create)
    raw = str(relative or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    if not parts or any(p in (".", "..") or p.startswith(".") for p in parts):
        raise ValueError("笔记路径必须是项目内的相对 Markdown 路径")
    if parts[0] != "笔记" or not parts[-1].lower().endswith((".md", ".markdown")):
        raise ValueError("笔记路径须以 笔记/ 开头并以 .md 结尾，例如：笔记/研究规划/计划.md")
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=False)
        notes_dir = (root / "笔记").resolve()
        resolved.relative_to(notes_dir)
    except ValueError:
        raise ValueError("笔记路径越过笔记目录") from None
    if path.is_symlink():
        raise ValueError("不能打开符号链接笔记")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def folder_path(db: Any, project_id: str, relative: str, *, create: bool = False) -> Path:
    root = project_root(db, project_id, writable=create)
    raw = str(relative or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    if not parts or parts[0] != "笔记" or any(p in (".", "..") or p.startswith(".") for p in parts):
        raise ValueError("文件夹路径必须是 笔记/ 开头的相对路径")
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=False)
        notes_dir = (root / "笔记").resolve()
        resolved.relative_to(notes_dir)
    except ValueError:
        raise ValueError("文件夹路径越过笔记目录") from None
    if path.is_symlink():
        raise ValueError("不能操作符号链接目录")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def create_folder(db: Any, project_id: str, relative: str) -> dict:
    path = folder_path(db, project_id, relative, create=True)
    return {"ok": True, "path": relative}


@synchronized
def rename(db: Any, project_id: str, relative: str, new_name: str) -> dict:
    root = project_root(db, project_id, writable=True)
    raw = str(relative or "").replace("\\", "/").strip("/")
    parts = [p for p in raw.split("/") if p]
    if not parts or parts[0] != "笔记":
        raise ValueError("只能重命名 笔记/ 目录下的笔记或文件夹")
    if raw == "笔记":
        raise ValueError("不能重命名笔记根目录")

    clean_name = str(new_name or "").strip().replace("\\", "/").split("/")[-1].strip()
    if not clean_name:
        raise ValueError("新名称不能为空")

    if raw.lower().endswith((".md", ".markdown")):
        path = note_path(db, project_id, relative)
        if not path.is_file():
            raise FileNotFoundError(f"笔记文件不存在: {relative}")
        if not clean_name.lower().endswith((".md", ".markdown")):
            clean_name += ".md"
        new_path = path.parent / clean_name
        if new_path.exists() and new_path != path:
            raise ValueError(f"已存在同名文件: {clean_name}")
        import fileguard
        fileguard.before_write(new_path, "笔记重命名")
        path.rename(new_path)
        new_relative = new_path.resolve().relative_to(root.resolve()).as_posix()
        db.db.execute("UPDATE assets SET canonical_uri=?, title=? WHERE project_id=? AND canonical_uri=?",
                      (str(new_path.resolve()), new_path.stem, project_id, str(path.resolve())))
        db.db.commit()
        return {"ok": True, "old_path": relative, "new_path": new_relative, "name": new_path.name}
    else:
        path = folder_path(db, project_id, relative)
        if not path.is_dir():
            raise FileNotFoundError(f"文件夹不存在: {relative}")
        new_path = path.parent / clean_name
        if new_path.exists() and new_path != path:
            raise ValueError(f"已存在同名文件夹: {clean_name}")
        old_prefix = str(path.resolve())
        path.rename(new_path)
        new_prefix = str(new_path.resolve())
        new_relative = new_path.resolve().relative_to(root.resolve()).as_posix()
        for asset in db.db.execute("SELECT id, canonical_uri FROM assets WHERE project_id=?", (project_id,)).fetchall():
            uri = asset["canonical_uri"]
            if uri.startswith(old_prefix):
                updated_uri = new_prefix + uri[len(old_prefix):]
                db.db.execute("UPDATE assets SET canonical_uri=? WHERE id=?", (updated_uri, asset["id"]))
        db.db.commit()
        return {"ok": True, "old_path": relative, "new_path": new_relative, "name": new_path.name}


@synchronized
def delete(db: Any, project_id: str, relative: str) -> dict:
    root = project_root(db, project_id, writable=True)
    raw = str(relative or "").replace("\\", "/").strip("/")
    parts = [p for p in raw.split("/") if p]
    if not parts or parts[0] != "笔记":
        raise ValueError("只能删除 笔记/ 目录下的笔记或文件夹")
    if raw == "笔记":
        raise ValueError("不能删除笔记根目录")
    if raw.lower().endswith((".md", ".markdown")):
        path = note_path(db, project_id, relative)
        if not path.exists():
            raise FileNotFoundError(f"笔记文件不存在: {relative}")
        files = [path]
        if path.is_file():
            path.unlink()
    else:
        path = folder_path(db, project_id, relative)
        if not path.exists():
            raise FileNotFoundError(f"目录不存在: {relative}")
        files = [p for p in path.rglob("*.md") if p.is_file() and not p.is_symlink()]
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
    import semantic
    for file in files:
        table, row, asset = _record(db, project_id, file)
        if row:
            db.db.execute(f"DELETE FROM {table} WHERE id=? AND task_id=?", (row["id"], project_id))
            semantic.invalidate_source(db, "note" if table == "notes" else "md_note", row["id"])
            _archive_aliases(db, project_id, row["id"])
        if asset:
            db.db.execute("UPDATE assets SET status='archived' WHERE id=?", (asset["id"],))
            db.db.execute("DELETE FROM asset_fts WHERE asset_id=?", (asset["id"],))
    db.db.commit()
    return {"ok": True, "deleted": relative}


def _record(db: Any, project_id: str, path: Path) -> tuple[str, dict, dict]:
    asset_row = db.db.execute("SELECT * FROM assets WHERE project_id=? AND canonical_uri=?",
                              (project_id, str(path.resolve()))).fetchone()
    asset = dict(asset_row) if asset_row else {}
    metadata = json.loads(asset.get("metadata") or "{}")
    nid = metadata.get("legacy_id") or path.stem
    for table in ("md_notes", "notes"):
        row = db.db.execute(f"SELECT * FROM {table} WHERE id=? AND task_id=?", (nid, project_id)).fetchone()
        if row:
            return table, dict(row), asset
    return "md_notes", {}, asset


def _archive_aliases(db: Any, project_id: str, nid: str, keep: str = "") -> None:
    import asset_service
    for asset in asset_service.find_by_metadata(db, project_id=project_id, kind="note", key="legacy_id", value=nid):
        if asset["id"] != keep:
            db.db.execute("UPDATE assets SET status='archived' WHERE id=?", (asset["id"],))
            db.db.execute("DELETE FROM asset_fts WHERE asset_id=?", (asset["id"],))


_LEGACY_TITLE_CATEGORY = (("精读 · ", "文献精读笔记"), ("笔记 · ", "文献精读笔记"),
                          ("创新点审查", "研究计划"), ("稿件审查", "写作投稿"))


@synchronized
def rehome_legacy(db: Any, project_id: str, notes: Path) -> int:
    """老版本把项目笔记直接写在 笔记/ 根上（笔记页只能把它们算「未分类」）。按标题前缀搬进分类目录，
    顺带把 assets 的规范路径指过去。认不出类别的原地不动，留在未分类里等人自己归。"""
    project_root(db, project_id, writable=True)
    rows = [dict(r) for r in db.notes(project_id)] + [n for n in db.md_notes() if n.get("task_id") == project_id]
    conn = getattr(db, "db", None)
    moved = 0
    for row in rows:
        old = notes / f"{row['id']}.md"
        if conn is None or not old.is_file():
            continue
        name = next((c for p, c in _LEGACY_TITLE_CATEGORY if str(row.get("title") or "").startswith(p)), "")
        if not name:
            continue
        new = notes / name / old.name
        new.parent.mkdir(exist_ok=True)
        old.replace(new)
        conn.execute("UPDATE assets SET canonical_uri=? WHERE canonical_uri=?",
                     (str(new.resolve()), str(old.resolve())))
        moved += 1
    if moved:
        conn.commit()
    return moved


def _file_rows(folder: Path, notes_root: Path, titles: dict[str, str]) -> list[dict]:
    """一个分类目录下的 .md 清单。标题优先取数据库里的（文件名是 uuid，直接显示等于一串乱码）。"""
    files = []
    for path in folder.rglob("*.md"):
        if path.is_symlink() or not path.is_file() or path.name.lower() == "readme.md":
            continue
        try:
            rel = path.resolve().relative_to(notes_root).as_posix()
        except ValueError:
            continue
        files.append({"path": rel, "title": titles.get(str(path.resolve())) or titles.get(path.stem) or path.stem,
                      "updated_at": path.stat().st_mtime})
    files.sort(key=lambda item: item["updated_at"], reverse=True)
    return files


def build_tree(folder: Path, base_root: Path, titles: dict[str, str]) -> dict:
    children = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception:
        entries = []
    for entry in entries:
        if entry.name.startswith((".", "~$")) or entry.is_symlink() or entry.name in ("__pycache__", "node_modules", ".git"):
            continue
        if entry.is_dir():
            children.append(build_tree(entry, base_root, titles))
        elif entry.is_file() and entry.name.lower().endswith((".md", ".markdown")):
            try:
                rel = entry.resolve().relative_to(base_root.resolve()).as_posix()
            except ValueError:
                continue
            children.append({
                "type": "file",
                "name": entry.name,
                "title": titles.get(str(entry.resolve())) or titles.get(entry.stem) or entry.name,
                "path": rel,
                "ext": "md",
                "updated_at": entry.stat().st_mtime,
                "size": entry.stat().st_size
            })
    try:
        rel_path = folder.resolve().relative_to(base_root.resolve()).as_posix()
        if rel_path == ".":
            rel_path = ""
    except ValueError:
        rel_path = folder.name
    try:
        direct_items = sum(1 for c in folder.iterdir() if not c.name.startswith((".", "~$")) and not c.is_symlink())
    except Exception:
        direct_items = len(children)
    return {
        "type": "dir",
        "name": folder.name,
        "path": rel_path,
        "count": direct_items,
        "children": children
    }


@synchronized
def dashboard(db: Any, project_id: str, query: str = "") -> dict:
    root = project_root(db, project_id)
    restriction = _write_restriction(root)
    notes = root / "笔记"
    if not notes.resolve().is_relative_to(root):
        raise ValueError("笔记目录越出项目文件夹")
    catalog = note_catalog.load(db, project_id)
    titles = {r["id"]: r["title"] for r in db.notes(project_id)}
    titles.update({n["id"]: n["title"] for n in db.md_notes() if n.get("task_id") == project_id})
    for asset in db.db.execute("SELECT canonical_uri,title FROM assets WHERE project_id=? "
                               "AND kind='note' AND storage='file' AND status='active'", (project_id,)):
        titles[str(Path(asset["canonical_uri"]).resolve())] = asset["title"]

    notes_tree = build_tree(notes, root, titles) if notes.is_dir() else {"type": "dir", "name": "笔记", "path": "笔记", "children": []}
    tree = notes_tree

    groups = []
    for name, description in CATEGORIES:
        files = _file_rows(notes / name, notes.parent, titles)
        groups.append({"name": name, "path": f"笔记/{name}", "description": description, "count": len(files), "files": files})
    known = {name for name, _ in CATEGORIES}
    for folder in notes.iterdir() if notes.is_dir() else ():
        if not folder.is_dir() or folder.is_symlink() or folder.name in known or folder.name.startswith("."):
            continue
        files = _file_rows(folder, notes.parent, titles)
        groups.append({"name": folder.name, "path": f"笔记/{folder.name}", "description": "已有笔记分类", "count": len(files), "files": files})
    legacy = []
    for path in notes.glob("*.md"):
        if path.name == "README.md" or path.is_symlink():
            continue
        legacy.append({"path": f"笔记/{path.name}", "title": titles.get(str(path.resolve())) or titles.get(path.stem) or path.stem,
                       "updated_at": path.stat().st_mtime})
    legacy.sort(key=lambda item: item["updated_at"], reverse=True)
    groups.append({"name": "未分类", "path": "笔记", "description": "笔记根目录下的散落笔记", "count": len(legacy), "files": legacy})
    items = []
    def collect(node):
        if node["type"] == "dir":
            for child in node.get("children", []):
                collect(child)
            return
        item = dict(node)
        try:
            path = note_path(db, project_id, node["path"])
            if path.stat().st_size > MAX_NOTE_BYTES:
                raise ValueError("笔记超过 5 MB")
            content = path.read_text(encoding="utf-8")
            item.update(note_catalog.attributes(content, node["path"], catalog))
            item["excerpt"] = note_catalog.split(content)[1][:400]
            item["matches_query"] = not query or query.casefold() in (item["title"] + " " + content + " " + " ".join(item["tags"])).casefold()
        except (OSError, ValueError) as exc:
            item.update(category_id="", tags=[], note_type="note", metadata_error=str(exc))
        items.append(item)
    collect(tree)
    items.sort(key=lambda n: n["updated_at"], reverse=True)
    return {"project_id": project_id, "path_format": "笔记/<分类>/<文件名>.md",
            "catalog": catalog, "items": items,
            "tags": sorted({tag for item in items for tag in item["tags"]}, key=str.casefold),
            "tree": notes_tree, "project_tree": notes_tree, "notes_tree": notes_tree,
            "project_name": root.name, "project_rel": f"projects/{project_id[:16]}",
            "groups": groups, "total": sum(g["count"] for g in groups),
            "read_only": bool(restriction), "read_only_reason": restriction}


def _title_of(db: Any, project_id: str, stem: str) -> str:
    """笔记文件名叫 uuid，标题得回数据库取（笔记页和编辑器标题框都靠它）。"""
    for row in db.notes(project_id):
        if row["id"] == stem:
            return row["title"]
    for note in db.md_notes():
        if note["id"] == stem and note.get("task_id") == project_id:
            return note["title"]
    return ""


@synchronized
def read(db: Any, project_id: str, relative: str) -> dict:
    import asset_service
    path = note_path(db, project_id, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    _, row, _ = _record(db, project_id, path)
    restriction = _write_restriction(project_root(db, project_id))
    content = path.read_text(encoding="utf-8")
    attrs = note_catalog.attributes(content, relative, note_catalog.load(db, project_id))
    meta, body = note_catalog.split(content)
    return {"path": relative, "title": row.get("title") or path.stem,
            "content": content, "body": body, "metadata": meta, **attrs,
            "content_hash": asset_service.sha256_file(path),
            "read_only": bool(restriction), "read_only_reason": restriction}


@synchronized
def write(db: Any, project_id: str, relative: str, content: str, *, title: str | None = None,
          create: bool = False, expected_hash: str | None = None, metadata: dict | None = None) -> dict:
    import asset_service
    import fileguard

    catalog = note_catalog.load(db, project_id)
    if not relative:
        cid = (metadata or {}).get("category_id") or ""
        category = note_catalog.category(catalog, cid)
        relative = (category["folder"] if category else "笔记") + "/" + uuid.uuid4().hex + ".md"
        create = True
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    path = note_path(db, project_id, relative, create=True)
    if create and path.exists():
        raise ValueError("新笔记的路径已存在，请使用不同的文件名")
    if expected_hash is not None:
        current_hash = asset_service.sha256_file(path) if path.is_file() else ""
        if current_hash != expected_hash:
            raise ValueError("笔记已被其他操作修改或删除，请保留当前编辑并重新打开笔记后合并")
    previous_content = path.read_text(encoding="utf-8") if path.is_file() else ""
    content = note_catalog.update_content(content, previous_content, metadata, relative, catalog)
    if note_catalog.is_template(content):
        import prompt_templates
        content = prompt_templates.prepare(db, project_id, relative, content, previous_content)
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    fileguard.before_write(path, "项目笔记保存")
    asset_service.atomic_write_text(path, content)
    import semantic
    import workflow
    table, row, previous = _record(db, project_id, path)
    stamp = workflow.now()
    nid = row.get("id") or uuid.uuid4().hex
    title = (title.strip() or "未命名笔记") if title is not None else (row.get("title") or path.stem)
    if row:
        if table == "notes":
            db.db.execute("UPDATE notes SET title=?,markdown=? WHERE id=? AND task_id=?", (title, content, nid, project_id))
        else:
            db.db.execute("UPDATE md_notes SET title=?,content=?,updated_at=? WHERE id=? AND task_id=?",
                          (title, content, stamp, nid, project_id))
        semantic.invalidate_source(db, "note" if table == "notes" else "md_note", nid)
    else:
        db.db.execute("INSERT INTO md_notes (id,title,content,task_id,paper_id,created_at,updated_at) VALUES (?,?,?,?,'',?,?)",
                      (nid, title, content, project_id, stamp, stamp))
    metadata = {**json.loads(previous.get("metadata") or "{}"), "legacy_id": nid,
                "paper_id": row.get("paper_id") or "", "table": table,
                **note_catalog.attributes(content, relative, catalog)}
    asset = asset_service.register_file(db, path, project_id=project_id, kind="note", title=title,
                                        source=previous.get("source") or "project_notes", body=content, metadata=metadata)
    db.db.execute("UPDATE assets SET status='active' WHERE id=?", (asset["id"],))
    _archive_aliases(db, project_id, nid, keep=asset["id"])
    db.db.commit()
    fileguard.audit("save", path, bytes=path.stat().st_size)
    return {"path": relative, "title": title, "bytes": path.stat().st_size, "content_hash": asset["content_hash"],
            "asset_id": asset["id"], "revision_id": asset["current_revision_id"], "content": content,
            **note_catalog.attributes(content, relative, catalog)}


@synchronized
def batch_update(db, project_id, body):
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or len(paths) > 500:
        raise ValueError("请选择 1 至 500 篇笔记")
    add, remove = note_catalog.tags(body.get("add_tags", [])), note_catalog.tags(body.get("remove_tags", []))
    renamed = body.get("rename_tags") or {}
    if not isinstance(renamed, dict):
        raise ValueError("标签重命名格式无效")
    results = []
    for relative in dict.fromkeys(paths):
        current = read(db, project_id, relative)
        updated = [renamed.get(t, t) for t in current["tags"] if t.casefold() not in {x.casefold() for x in remove}]
        changes = {"tags": note_catalog.tags(updated + add)}
        if "category_id" in body:
            changes["category_id"] = body["category_id"]
        results.append(write(db, project_id, relative, current["content"], metadata=changes,
                             expected_hash=current["content_hash"]))
    return {"updated": len(results)}
