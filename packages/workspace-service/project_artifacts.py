"""Write task-owned text artifacts inside a project directory."""
from __future__ import annotations

import csv_text as csv
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import asset_service
import fileguard
import project_notes


TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".csv", ".json", ".tex", ".bib", ".yaml", ".yml",
})


def validate_text(relative: str, content: str) -> None:
    suffix = Path(relative).suffix.lower()
    if suffix == '.csv':
        try:
            reader = csv.reader(io.StringIO(content, newline=''), strict=True)
            rows = [(reader.line_num, row) for row in reader if any(cell.strip() for cell in row)]
        except csv.Error as exc:
            raise ValueError(f'约定 CSV 格式无效：{relative}；{exc}') from exc
        if not rows:
            raise ValueError('约定 CSV 必须包含表头：' + relative)
        expected = len(rows[0][1])
        for line, row in rows[1:]:
            if len(row) != expected:
                raise ValueError(f'约定 CSV 每行字段数必须一致：{relative}；第 {line} 行应有 {expected} 列，实际 {len(row)} 列。请按表头逐列修正空字段与引号后重新保存')
    elif suffix == '.json':
        try:
            json.loads(content)
        except ValueError as exc:
            raise ValueError('约定 JSON 格式无效：' + relative) from exc


def _task_evidence(db, task_id: str, project_id: str) -> list[dict]:
    accepted_ids = []
    for row in db.db.execute("SELECT checkpoint FROM project_task_runs WHERE task_id=? ORDER BY attempt", (task_id,)):
        try:
            checkpoint = json.loads(row["checkpoint"] or "{}")
        except (TypeError, ValueError):
            continue
        for key, result in (checkpoint.get("done_tools") or {}).items():
            if str(key).split("\0", 1)[0] != "paper_evidence_record" or not isinstance(result, dict):
                continue
            accepted_ids.extend(item.get("id", "") for item in result.get("accepted", []) if isinstance(item, dict))
    accepted_ids = list(dict.fromkeys(value for value in accepted_ids if value))
    if not accepted_ids:
        return []
    placeholders = ",".join("?" for _ in accepted_ids)
    rows = db.db.execute(
        f"SELECT e.id,e.paper_id,e.page_no,e.field,e.value,p.filename,COALESCE(m.title,'') AS title "
        f"FROM evidence e JOIN papers p ON p.id=e.paper_id "
        f"LEFT JOIN paper_meta m ON m.paper_id=p.id WHERE p.task_id=? AND e.id IN ({placeholders})",
        [project_id, *accepted_ids],
    )
    evidence = {row["id"]: dict(row) for row in rows}
    return [evidence[value] for value in accepted_ids if value in evidence]


def _attach_task_evidence(db, task_id: str, project_id: str, relative: str, content: str) -> str:
    records = _task_evidence(db, task_id, project_id)
    if not records:
        return content
    suffix = Path(relative).suffix.lower()
    if suffix in (".md", ".markdown"):
        missing = [row for row in records if row["id"] not in content]
        if not missing:
            return content
        lines = ["", "", "## Registered source evidence"]
        lines.extend(f"- `{row['id']}`: {row['filename']} p.{row['page_no']}, {row['field']} ({row['value']})."
                     for row in missing)
        return content.rstrip() + "\n" + "\n".join(lines) + "\n"
    if suffix != ".csv":
        return content
    try:
        rows = list(csv.reader(io.StringIO(content, newline=""), strict=True))
    except csv.Error:
        return content
    if not rows or not {"citation", "comparability_note"}.issubset(rows[0]):
        return content
    citation_index, note_index = rows[0].index("citation"), rows[0].index("comparability_note")
    papers = db.papers_of(project_id)
    references = {}
    for row in records:
        paper = next((paper for paper in papers if paper["id"] == row["paper_id"]), None)
        if not paper:
            continue
        title = "".join(ch.casefold() for ch in (paper.get("title") or "") if ch.isalnum())
        filename = "".join(ch.casefold() for ch in Path(row["filename"]).stem if ch.isalnum())
        references[row["id"]] = (title, filename, row)
    changed = False
    for row in rows[1:]:
        if len(row) != len(rows[0]):
            continue
        citation = "".join(ch.casefold() for ch in row[citation_index] if ch.isalnum())
        for evidence_id, (title, filename, evidence) in references.items():
            if not citation or not any(key and (key in citation or citation in key) for key in (title, filename)):
                continue
            if evidence_id in row[note_index]:
                continue
            row[note_index] = (row[note_index].rstrip(" ;") + "; " if row[note_index].strip() else "") + \
                f"evidence_id={evidence_id} ({evidence['filename']} p.{evidence['page_no']})"
            changed = True
    if not changed:
        return content
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue()


def current(db, project_id: str) -> list[dict]:
    """Read declared working files so the active task can merge a version conflict."""
    import project_tasks
    context = getattr(db, "project_task_context", None)
    task_id = getattr(context, "parent_task_id", "")
    if not task_id:
        raise PermissionError("只能读取当前执行任务的工作产物")
    task = project_tasks.get(db, task_id, project_id=project_id)
    if task["status"] != "running":
        raise PermissionError("只能读取当前执行任务的工作产物")
    files = []
    for declared in task["output_contract"].get("required_files", []):
        relative = declared.get("relative") or declared.get("path") if isinstance(declared, dict) else str(declared)
        _, target = _path(db, project_id, relative)
        if not target.is_file():
            files.append({"path": relative, "exists": False, "content_hash": ""})
            continue
        asset = asset_service.path_asset(db, project_id, target)
        metadata = asset.get("metadata") or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
        prior_run_id = str(metadata.get("task_run_id") or "")
        prior_run = db.db.execute("SELECT task_id FROM project_task_runs WHERE id=?", (prior_run_id,)).fetchone()
        if (asset.get("project_id") != project_id or metadata.get("project_task_id") != task_id or
                asset.get("source") != "workflow_artifact" or not prior_run or prior_run["task_id"] != task_id):
            raise PermissionError("只能读取当前任务先前尝试生成的工作产物")
        raw = target.read_bytes()
        digest = asset_service.sha256_bytes(raw)
        files.append({"path": relative, "exists": True, "content": raw.decode("utf-8-sig"),
                      "content_hash": digest})
    return files


def _path(db, project_id: str, raw: str) -> tuple[Path, Path]:
    root = project_notes.project_root(db, project_id, writable=True).resolve()
    value = str(raw or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("产物路径不能为空")
    candidate = Path(value).expanduser()
    relative_parts = PurePosixPath(value).parts
    if (candidate.is_absolute() or PureWindowsPath(value).drive or any(part in ("", ".", "..") for part in relative_parts)):
        raise ValueError("产物路径不能包含盘符、点路径或父目录")
    lexical = root.joinpath(*relative_parts)
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        raise PermissionError("产物只能写入当前项目目录") from None
    if any((root / Path(*relative.parts[:index])).is_symlink() for index in range(1, len(relative.parts) + 1)):
        raise PermissionError("产物路径不能经过符号链接")
    target = lexical.resolve()
    if not target.is_relative_to(root):
        raise PermissionError("产物只能写入当前项目目录")
    if ".tasks" in target.relative_to(root).parts:
        raise PermissionError("不能直接写入任务内部归档目录")
    if target.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("产物只支持 Markdown、CSV、JSON、TeX 等文本文件")
    return root, target


def bind_write_version(db, project_id: str, args: dict, receipts: list[dict]) -> dict:
    """Reuse a successful write's version only while the file is unchanged."""
    if args.get('expected_hash') not in (None, ''):
        return args
    _, target = _path(db, project_id, args.get('path'))
    receipt = next((item for item in reversed(receipts) if item.get('path') == args.get('path')), None)
    if not receipt or not target.is_file() or asset_service.sha256_file(target) != receipt.get('content_hash'):
        return args
    asset = asset_service.path_asset(db, project_id, target)
    metadata = asset.get('metadata') or {}
    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    context = getattr(db, 'project_task_context', None)
    if metadata.get('project_task_id') != getattr(context, 'parent_task_id', ''):
        return args
    return {**args, 'expected_hash': receipt['content_hash']}


def cached_write_current(db, project_id: str, args: dict, result: dict) -> bool:
    if not isinstance(result, dict) or not result.get('content_hash'):
        return False
    _, target = _path(db, project_id, args.get('path'))
    if not target.is_file() or asset_service.sha256_file(target) != result['content_hash']:
        return False
    asset = asset_service.path_asset(db, project_id, target)
    return (asset.get('id') == result.get('asset_id')
            and saved_version_current(db, project_id, args['path'], asset, result['content_hash']))


def saved_version_current(db, project_id: str, relative: str, asset: dict, content_hash: str) -> bool:
    """Accept an active write or an unchanged receipt inherited by normal resume."""
    metadata = asset.get('metadata') or {}
    metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    context = getattr(db, 'project_task_context', None)
    task_id = getattr(context, 'parent_task_id', '')
    run = db.db.execute('SELECT r.id,r.checkpoint FROM project_tasks t '
                        'JOIN project_task_runs r ON r.id=t.latest_run_id '
                        'WHERE t.id=? AND t.project_id=? AND t.status=\'running\'',
                        (task_id, project_id)).fetchone()
    if (not run or asset.get('project_id') != project_id or asset.get('source') != 'workflow_artifact'
            or metadata.get('project_task_id') != task_id or asset.get('content_hash') != content_hash):
        return False
    if metadata.get('task_run_id') == run['id']:
        return True
    prior = db.db.execute('SELECT task_id FROM project_task_runs WHERE id=?',
                         (metadata.get('task_run_id'),)).fetchone()
    checkpoint = json.loads(run['checkpoint'] or '{}')
    if not prior or prior['task_id'] != task_id or not checkpoint.get('resumed_from'):
        return False
    receipts = [result for signature, result in (checkpoint.get('done_tools') or {}).items()
                if str(signature).split('\0', 1)[0] == 'project_artifact_write']
    receipts.extend((checkpoint.get('context_state') or {}).get('progress', {}).get('writes') or [])
    return any(isinstance(receipt, dict) and not receipt.get('error') and receipt.get('ok') is not False
               and receipt.get('path') == relative and receipt.get('asset_id') == asset.get('id')
               and receipt.get('content_hash') == content_hash for receipt in receipts)


def submission(db, project_id: str) -> dict:
    """Check saved files before handing them to the normal worker validation."""
    files = current(db, project_id)
    if not files or any(not item['exists'] for item in files):
        raise ValueError('Save every required file before submitting outputs.')
    for item in files:
        _, target = _path(db, project_id, item['path'])
        asset = asset_service.path_asset(db, project_id, target)
        if not saved_version_current(db, project_id, item['path'], asset, item['content_hash']):
            raise ValueError('Save each required file at its current version in this attempt, or resume its verified saved write receipt before submitting.')
    return {'ok': True, 'submitted': True, 'approved': False,
            'files': [{key: item[key] for key in ('path', 'content_hash')} for item in files]}


def write(db, project_id: str, args: dict) -> dict:
    """Atomically save one declared project artifact and register its version."""
    context = getattr(db, "project_task_context", None)
    task_id = getattr(context, "parent_task_id", "")
    task_row = db.db.execute(
        "SELECT id,latest_run_id,output_contract,metadata,status FROM project_tasks WHERE id=? AND project_id=?",
        (task_id, project_id),
    ).fetchone() if task_id else None
    if not task_row or task_row["status"] != "running":
        raise PermissionError("项目产物写入仅限当前运行中的项目任务")
    try:
        contract = json.loads(task_row["output_contract"] or "{}")
        metadata = json.loads(task_row["metadata"] or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError("项目任务的产物约定无法读取") from exc
    node = metadata.get("node_config") or {}
    if not contract.get("required_files") and node:
        import workflow_layout
        required = workflow_layout.required_outputs({**node, "output_contract": contract})
        contract["required_files"] = required

    content = str(args.get("content") or "")
    if not content.strip():
        raise ValueError("产物内容不能为空")
    root, target = _path(db, project_id, args.get("path"))
    relative = target.relative_to(root).as_posix()
    required_paths = {str(item.get("relative") or item.get("path")) if isinstance(item, dict) else str(item)
                      for item in contract.get("required_files") or []}
    if relative not in required_paths:
        raise PermissionError("产物路径必须与当前任务声明的 required_files 一致；可写路径：" + '、'.join(sorted(required_paths)))
    if contract.get("requires_evidence"):
        content = _attach_task_evidence(db, task_id, project_id, relative, content)
    validate_text(relative, content)
    import artifact_quality
    artifact_quality.validate_rules(contract)
    artifact_quality.validate(relative, content, (contract.get('file_rules') or {}).get(relative, {}))
    expected = args.get("expected_hash")
    if expected in (None, "") and target.is_file():
        existing_row = db.db.execute("SELECT * FROM assets WHERE canonical_uri=?", (str(target),)).fetchone()
        if existing_row:
            existing_asset = dict(existing_row)
            existing_metadata = existing_asset.get("metadata") or {}
            existing_metadata = json.loads(existing_metadata) if isinstance(existing_metadata, str) else existing_metadata
            if (existing_metadata.get("project_task_id") == task_id and
                    existing_asset.get("source") == "workflow_artifact" and
                    existing_metadata.get("task_run_id") != task_row["latest_run_id"]):
                current = asset_service.sha256_file(target)
                if not existing_asset.get("content_hash") or current != existing_asset["content_hash"]:
                    raise ValueError('产物文件在上次登记后已变化，请先读取工作产物并核对最新版本')
                expected = current
    if expected is not None:
        current = asset_service.sha256_file(target) if target.is_file() else ""
        if current != str(expected):
            raise ValueError('产物版本不匹配。请用 workflow_context_read(section="artifacts") 读取当前内容和 content_hash，合并后将该哈希作为 expected_hash 重试；新建文件时省略 expected_hash')
    elif target.is_file():
        row = db.db.execute("SELECT metadata FROM assets WHERE canonical_uri=?", (str(target),)).fetchone()
        try:
            owner = json.loads(row["metadata"] or "{}") if row else {}
        except (TypeError, ValueError):
            owner = {}
        if owner.get("project_task_id") != task_id:
            raise ValueError("覆盖已有项目文件需要传入读取时取得的 expected_hash")
        asset_service.require_path_context(db, project_id, target)
    fileguard.before_write(target, "工作流产物保存")
    asset_service.atomic_write_text(target, content)
    asset = asset_service.register_file(
        db, target, project_id=project_id, kind="task_output",
        title=str(args.get("title") or target.stem), source="workflow_artifact",
        body=content, metadata={"project_task_id": task_id, "task_run_id": task_row["latest_run_id"]},
    )
    fileguard.audit("save_artifact", target, bytes=target.stat().st_size, project_id=project_id)
    return {
        "ok": True,
        "path": relative,
        "absolute_path": str(target),
        "name": target.name,
        "content_hash": asset["content_hash"],
        "asset_id": asset["id"],
        "output_assets": [asset["id"]],
        "bytes": target.stat().st_size,
    }
