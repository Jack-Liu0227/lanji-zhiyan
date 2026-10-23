"""Project Markdown notebook with a strict project directory boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Any


CATEGORIES = (
    ("研究计划", "研究问题、假设与里程碑"),
    ("检索策略", "检索式、筛选标准与检索日志"),
    ("文献矩阵", "文献对照与证据表"),
    ("文献精读笔记", "单篇论文的证据与批注"),
    ("文献综述", "主题综合、争议与研究缺口"),
    ("每日推送", "每日文献与情报摘要"),
    ("实验方案", "实验设计与操作步骤"),
    ("数据记录", "原始数据说明与处理日志"),
    ("分析结果", "图表、统计与解释"),
    ("写作投稿", "章节、投稿与审稿往来"),
)
MAX_NOTE_BYTES = 5 * 1024 * 1024


def project_root(db: Any, project_id: str) -> Path:
    import paths

    root = db.base_dir(str(project_id or ""))
    if root is None or not root.is_dir():
        raise ValueError("请选择一个已设置文件夹的项目")
    return paths.validate_project_dir(root)


def ensure_categories(db: Any, project_id: str) -> Path:
    root = project_root(db, project_id)
    notes = root / "笔记"
    if not notes.resolve().is_relative_to(root):
        raise ValueError("笔记目录越过项目文件夹")
    notes.mkdir(exist_ok=True)
    for name, _ in CATEGORIES:
        target = notes / name
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise ValueError(f"笔记分类目录不可用：{name}")
        target.mkdir(exist_ok=True)
    return notes


def note_path(db: Any, project_id: str, relative: str, *, create: bool = False) -> Path:
    root = project_root(db, project_id)
    raw = str(relative or "").replace("\\", "/")
    parts = raw.split("/")
    if not raw or raw.startswith("/") or ":" in raw or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise ValueError("笔记路径必须是项目内的相对 Markdown 路径")
    if parts[0] != "笔记" or len(parts) < 2 or not parts[-1].lower().endswith(".md"):
        raise ValueError("笔记路径须以 笔记/ 开头并以 .md 结尾，例如：笔记/研究计划/计划.md")
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=False)
        notes_dir = (root / "笔记").resolve()
        resolved.relative_to(notes_dir)
    except ValueError:
        raise ValueError("笔记路径越过项目目录") from None
    if path.is_symlink():
        raise ValueError("不能打开符号链接笔记")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def folder_path(db: Any, project_id: str, relative: str, *, create: bool = False) -> Path:
    root = project_root(db, project_id)
    raw = str(relative or "").replace("\\", "/")
    parts = [p for p in raw.split("/") if p]
    if not parts or parts[0] != "笔记" or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise ValueError("文件夹路径必须是 笔记/ 开头的相对路径")
    path = root.joinpath(*parts)
    try:
        resolved = path.resolve(strict=False)
        notes_dir = (root / "笔记").resolve()
        resolved.relative_to(notes_dir)
    except ValueError:
        raise ValueError("文件夹路径越过项目目录") from None
    if path.is_symlink():
        raise ValueError("不能操作符号链接目录")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def create_folder(db: Any, project_id: str, relative: str) -> dict:
    path = folder_path(db, project_id, relative, create=True)
    return {"ok": True, "path": relative}


def delete(db: Any, project_id: str, relative: str) -> dict:
    raw = str(relative or "").replace("\\", "/").strip("/")
    if not raw or raw == "笔记":
        raise ValueError("不能删除笔记根目录")
    if raw.lower().endswith(".md"):
        path = note_path(db, project_id, relative)
        if not path.exists():
            raise FileNotFoundError(f"笔记文件不存在: {relative}")
        if path.is_file():
            path.unlink()
    else:
        path = folder_path(db, project_id, relative)
        if not path.exists():
            raise FileNotFoundError(f"目录不存在: {relative}")
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
    return {"ok": True, "deleted": relative}


_LEGACY_TITLE_CATEGORY = (("精读 · ", "文献精读笔记"), ("笔记 · ", "文献精读笔记"),
                          ("创新点审查", "研究计划"), ("稿件审查", "写作投稿"))


def rehome_legacy(db: Any, project_id: str, notes: Path) -> int:
    """老版本把项目笔记直接写在 笔记/ 根上（笔记页只能把它们算「未分类」）。按标题前缀搬进分类目录，
    顺带把 assets 的规范路径指过去。认不出类别的原地不动，留在未分类里等人自己归。"""
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
        files.append({"path": rel, "title": titles.get(path.stem) or path.stem,
                      "updated_at": path.stat().st_mtime})
    files.sort(key=lambda item: item["updated_at"], reverse=True)
    return files


def build_tree(folder: Path, notes_root: Path, titles: dict[str, str]) -> dict:
    children = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception:
        entries = []
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink():
            continue
        if entry.is_dir():
            children.append(build_tree(entry, notes_root, titles))
        elif entry.is_file() and entry.name.lower().endswith(".md") and entry.name.lower() != "readme.md":
            try:
                rel = entry.resolve().relative_to(notes_root.parent).as_posix()
            except ValueError:
                continue
            children.append({
                "type": "file",
                "name": entry.name,
                "title": titles.get(entry.stem) or entry.stem,
                "path": rel,
                "updated_at": entry.stat().st_mtime,
                "size": entry.stat().st_size
            })
    try:
        rel_path = folder.resolve().relative_to(notes_root.parent).as_posix()
    except ValueError:
        rel_path = "笔记"
    return {
        "type": "dir",
        "name": folder.name,
        "path": rel_path,
        "children": children
    }


def dashboard(db: Any, project_id: str) -> dict:
    notes = ensure_categories(db, project_id)
    rehome_legacy(db, project_id, notes)
    titles = {r["id"]: r["title"] for r in db.notes(project_id)}
    titles.update({n["id"]: n["title"] for n in db.md_notes() if n.get("task_id") == project_id})

    tree = build_tree(notes, notes, titles)

    groups = []
    for name, description in CATEGORIES:
        files = _file_rows(notes / name, notes.parent, titles)
        groups.append({"name": name, "path": f"笔记/{name}", "description": description, "count": len(files), "files": files})
    known = {name for name, _ in CATEGORIES}
    for folder in notes.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.name in known or folder.name.startswith("."):
            continue
        files = _file_rows(folder, notes.parent, titles)
        groups.append({"name": folder.name, "path": f"笔记/{folder.name}", "description": "已有笔记分类", "count": len(files), "files": files})
    legacy = []
    for path in notes.glob("*.md"):
        if path.name == "README.md" or path.is_symlink():
            continue
        legacy.append({"path": f"笔记/{path.name}", "title": titles.get(path.stem) or path.stem,
                       "updated_at": path.stat().st_mtime})
    legacy.sort(key=lambda item: item["updated_at"], reverse=True)
    groups.append({"name": "未分类", "path": "笔记", "description": "笔记根目录下的散落笔记", "count": len(legacy), "files": legacy})
    return {"project_id": project_id, "path_format": "笔记/<分类>/<文件名>.md",
            "tree": tree, "groups": groups, "total": sum(g["count"] for g in groups)}


def _title_of(db: Any, project_id: str, stem: str) -> str:
    """笔记文件名叫 uuid，标题得回数据库取（笔记页和编辑器标题框都靠它）。"""
    for row in db.notes(project_id):
        if row["id"] == stem:
            return row["title"]
    for note in db.md_notes():
        if note["id"] == stem and note.get("task_id") == project_id:
            return note["title"]
    return ""


def read(db: Any, project_id: str, relative: str) -> dict:
    path = note_path(db, project_id, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    return {"path": relative, "title": _title_of(db, project_id, path.stem) or path.stem,
            "content": path.read_text(encoding="utf-8")}


def write(db: Any, project_id: str, relative: str, content: str) -> dict:
    import asset_service
    import fileguard

    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    path = note_path(db, project_id, relative, create=True)
    fileguard.before_write(path, "项目笔记保存")
    asset_service.atomic_write_text(path, content)
    fileguard.audit("save", path, bytes=path.stat().st_size)
    return {"path": relative, "title": path.stem, "bytes": path.stat().st_size}
