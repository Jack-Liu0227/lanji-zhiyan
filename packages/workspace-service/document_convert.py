"""Local paper-toolbox conversion following the wshobson/agents file-conversion skill."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile

import asset_service
import execution

READERS = {"docx": "docx", "md": "markdown-raw_html-raw_tex", "markdown": "markdown-raw_html-raw_tex",
           "html": "html-raw_html", "htm": "html-raw_html", "tex": "latex"}
WRITERS = {"md": "markdown", "docx": "docx", "html": "html5", "tex": "latex"}
MAX_BYTES = 20 * 1024 * 1024


def executable():
    explicit = os.environ.get("RW_PANDOC", "").strip()
    if explicit:
        return explicit if Path(explicit).is_file() else None
    direct = shutil.which("pandoc")
    if direct:
        return direct
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        candidate = Path(root) / "Pandoc" / "pandoc.exe" if root else None
        if candidate and candidate.is_file():
            return str(candidate)
    return None


def require_engine():
    engine = executable()
    if not engine:
        raise ValueError("原文件转换需要 Pandoc。安装后重新打开工具箱，或使用现有的文本导入方式。")
    return str(Path(engine).resolve())


def _verify_docx(source):
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if (len(names) > 3000 or sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024
                or "word/document.xml" not in names or "[Content_Types].xml" not in names
                or any(name.lower().endswith("vbaproject.bin") for name in names)):
            raise ValueError("请选择有效、无宏的 DOCX，解压后不得超过 64 MB")
        # Reopen the package with the document reader, not just a ZIP signature check.
        from docx import Document
        from lxml.etree import XMLSyntaxError
        try:
            Document(source)
        except XMLSyntaxError as exc:
            raise ValueError("DOCX 内部 XML 无法读取") from exc


def upload(db, body):
    import io
    from toolbox_import import decode_file

    name, raw = decode_file(body)
    suffix = Path(name).suffix.lower().lstrip(".")
    if suffix not in READERS:
        raise ValueError("原文件转换支持 DOCX、Markdown、HTML 和 LaTeX")
    if suffix == "docx":
        try:
            _verify_docx(io.BytesIO(raw))
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            raise ValueError("Word 文件无法读取，请检查文件是否损坏、加密或含宏") from exc
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("原文件转换请使用 UTF-8 编码") from exc
        if not text.strip() or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
            raise ValueError("请选择有效的 UTF-8 文档")
    project_id = str(body.get("project_id") or "")
    if project_id:
        import project_tasks
        project_tasks._project(db, project_id)
    root = Path(db.path).resolve().parent / "document-toolbox" / "inputs"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (uuid.uuid4().hex + "." + suffix)
    with path.open("xb") as stream:
        stream.write(raw)
    try:
        asset = asset_service.register_file(db, path, project_id=project_id, kind="document",
                                            source="document_conversion_input", title=name)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"asset_id": asset["id"], "name": name, "format": suffix}


def input_asset(db, project_id, asset_id, target):
    asset = asset_service.get(db, asset_id)
    if not asset or asset["project_id"] != project_id or asset["source"] != "document_conversion_input":
        raise ValueError("文档不存在或不属于当前项目，请重新添加")
    path = Path(asset["canonical_uri"]).resolve()
    root = Path(db.path).resolve().parent / "document-toolbox" / "inputs"
    suffix = path.suffix.lower().lstrip(".")
    if not path.is_relative_to(root) or suffix not in READERS or not path.is_file():
        raise ValueError("转换输入已不可用，请重新添加")
    if path.stat().st_size > MAX_BYTES or asset_service.sha256_file(path) != asset["content_hash"]:
        raise ValueError("转换输入已变化，请重新添加")
    if target not in WRITERS or target == {"markdown": "md", "htm": "html"}.get(suffix, suffix):
        raise ValueError("请选择不同的受支持输出格式")
    return asset, path, suffix


def _run(arguments, folder):
    # The document never supplies arguments, filters, defaults, or a PDF engine.
    with (folder / "pandoc.log").open("w+b") as log:
        with subprocess.Popen(arguments, cwd=folder, stdin=subprocess.DEVNULL, stdout=log,
                              stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            deadline = time.monotonic() + 90
            try:
                while process.poll() is None:
                    execution.check()
                    if time.monotonic() >= deadline:
                        raise ValueError("Pandoc 转换超时，请拆分文件后重试")
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    process.kill()
            if process.returncode:
                log.seek(0)
                message = log.read(1600).decode("utf-8", "replace").strip()
                raise ValueError("Pandoc 无法转换此文件：" + message)


def transform(db, project_id, asset_id, target):
    asset, source, source_format = input_asset(db, project_id, asset_id, target)
    engine = require_engine()
    root = db.pdir(project_id, "最终产物") if project_id else Path(db.path).resolve().parent / "document-toolbox" / "outputs"
    root.mkdir(parents=True, exist_ok=True)
    batch = uuid.uuid4().hex
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(asset["title"]).stem).strip(". ")[:80] or "转换结果"
    path = root / (stem + "-" + batch[:8] + "." + target)
    with tempfile.TemporaryDirectory(prefix=".convert-", dir=root, ignore_cleanup_errors=True) as directory:
        folder = Path(directory)
        snapshot = folder / ("input." + source_format)
        shutil.copyfile(source, snapshot)
        if asset_service.sha256_file(snapshot) != asset["content_hash"]:
            raise ValueError("转换输入已变化，请重新添加")
        output = folder / ("result." + target)
        args = [engine, "--sandbox", "--from", READERS[source_format], "--to", WRITERS[target],
                "--output", str(output), str(snapshot), "+RTS", "-M512M", "-RTS"]
        if target == "html":
            args[2:2] = ["--standalone", "--mathml", "--metadata=pagetitle:Converted document"]
        _run(args, folder)
        execution.check()
        if not output.is_file() or not 0 < output.stat().st_size <= 35 * 1024 * 1024:
            raise ValueError("转换未生成可用的结果，或结果超过 35 MB")
        if target == "docx":
            _verify_docx(output)
        else:
            text = output.read_text(encoding="utf-8")
            if not text.strip() or (target == "html" and not re.search(r"<body(?:\s|>)", text, re.I)):
                raise ValueError("未能验证转换结果")
            if target == "html":
                # A locally opened export should not fetch references embedded in the input.
                policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\';">'
                text = re.sub(r"<head(?:\s[^>]*)?>", lambda match: match.group(0) + "\n" + policy, text, count=1, flags=re.I)
                output.write_text(text, encoding="utf-8")
        execution.check()
        output.replace(path)
    try:
        saved = asset_service.register_file(db, path, project_id=project_id, kind="export", source="document_conversion")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"engine": "pandoc", "content": f"已使用本机 Pandoc 完成转换\n{asset['title']} → {path.name}\n请复核图像与复杂版式。",
            "path": str(path), "files": [{"asset_id": saved["id"], "name": path.name, "project_id": project_id}]}
