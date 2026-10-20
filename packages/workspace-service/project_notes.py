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
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        raise ValueError("笔记路径越过项目目录") from None
    if path.is_symlink():
        raise ValueError("不能打开符号链接笔记")
    if create:
        category = root / "笔记" / parts[1]
        if len(parts) > 2 and parts[1] not in {name for name, _ in CATEGORIES} and not category.is_dir():
            raise ValueError("请先选择一个笔记分类")
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def dashboard(db: Any, project_id: str) -> dict:
    notes = ensure_categories(db, project_id)
    groups = []
    for name, description in CATEGORIES:
        folder = notes / name
        files = []
        for path in folder.rglob("*.md"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(notes.parent).as_posix()
            except ValueError:
                continue
            files.append({"path": rel, "title": path.stem, "updated_at": path.stat().st_mtime})
        files.sort(key=lambda item: item["updated_at"], reverse=True)
        groups.append({"name": name, "path": f"笔记/{name}", "description": description, "count": len(files), "files": files})
    known = {name for name, _ in CATEGORIES}
    for folder in notes.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.name in known or folder.name.startswith("."):
            continue
        files = []
        for path in folder.rglob("*.md"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(notes.parent).as_posix()
            except ValueError:
                continue
            files.append({"path": rel, "title": path.stem, "updated_at": path.stat().st_mtime})
        files.sort(key=lambda item: item["updated_at"], reverse=True)
        groups.append({"name": folder.name, "path": f"笔记/{folder.name}", "description": "已有笔记分类", "count": len(files), "files": files})
    legacy = []
    for path in notes.glob("*.md"):
        if path.name == "README.md" or path.is_symlink():
            continue
        legacy.append({"path": f"笔记/{path.name}", "title": path.stem, "updated_at": path.stat().st_mtime})
    legacy.sort(key=lambda item: item["updated_at"], reverse=True)
    groups.append({"name": "未分类", "path": "笔记", "description": "已有笔记", "count": len(legacy), "files": legacy})
    return {"project_id": project_id, "path_format": "笔记/<分类>/<文件名>.md",
            "groups": groups, "total": sum(g["count"] for g in groups)}


def read(db: Any, project_id: str, relative: str) -> dict:
    path = note_path(db, project_id, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError("笔记超过 5 MB")
    return {"path": relative, "title": path.stem, "content": path.read_text(encoding="utf-8")}


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
