"""Resolve a UI file reference and open its directory through the desktop host."""
from pathlib import Path

import fileguard
import paths
import project_notes


def locate(db, body: dict) -> dict:
    project_id = str(body.get("project_id") or "")
    relative = str(body.get("relative") or "")
    if project_id:
        root = project_notes.project_root(db, project_id)
        if Path(relative).is_absolute() or Path(relative).drive:
            raise ValueError("项目内路径必须是相对路径")
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ValueError("路径越过项目文件夹")
    elif body.get("zotero_key"):
        import zotero
        pdf = zotero.pdf_path(str(body["zotero_key"]))
        if not pdf:
            raise ValueError("此文献没有本地 PDF 附件")
        target = paths.resolve_allowed(pdf, must_be_file=True)
    else:
        target = paths.resolve_allowed(str(body.get("path") or ""))
    if not target.exists():
        raise ValueError("文件或文件夹已不存在，请刷新列表")
    directory = target if target.is_dir() else target.parent
    opened = body.get("open") is True
    if opened:
        if not paths.host_open(directory):
            raise ValueError("无法打开所在文件夹，请复制路径后在资源管理器中打开")
        fileguard.audit("open_location", target, directory=str(directory))
    return {"path": str(target), "directory": str(directory), "opened": opened}
