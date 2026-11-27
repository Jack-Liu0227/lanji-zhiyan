"""Read-only Markdown and CSV previews scoped to a project and conversation."""
from __future__ import annotations

import csv_text as csv
import hashlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat

import project_notes

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_OFFSET = 2**31 - 1
MARKDOWN = {'.md', '.markdown'}


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError(f'{name} 必须为整数')
    # Bound the string before int() as well as the resulting value.
    if len(str(value)) > 10 or not minimum <= int(value) <= maximum:
        raise ValueError(f'{name} 超出范围（{minimum}–{maximum}）')
    return int(value)


def _string(value, name, *, required=False):
    if not isinstance(value, str) or '\0' in value or len(value) > 4096 or (required and not value.strip()):
        raise ValueError(f'{name} 无效')
    return value


def _project_path(root, reference):
    """A leading slash denotes the project root; drive paths retain their drive."""
    raw = reference.replace('\\', '/')
    drive_path = re.match(r'^[a-z]:/', raw, re.I)
    if not raw or raw.startswith('//') or (re.match(r'^[a-z][a-z0-9+.-]*:', raw, re.I) and not drive_path):
        raise ValueError('请选择当前项目内的本地文件')
    if ':' in (raw[2:] if drive_path else raw) or re.search(r'[\x00-\x1f]', raw):
        raise ValueError('文件路径不能包含设备或备用数据流名称')
    if PureWindowsPath(raw).drive:
        if not Path(raw).is_absolute():
            raise ValueError('文件路径越过当前项目范围')
        path = Path(raw).resolve()
    elif Path(raw).is_absolute() and Path(raw).is_relative_to(root):
        path = Path(raw).resolve()
    else:
        path = (root / raw.lstrip('/')).resolve()
    if not path.is_relative_to(root):
        raise ValueError('文件路径越过当前项目范围')
    return path


def _file_limit(path):
    suffix = path.suffix.lower()
    if suffix not in MARKDOWN | {'.csv'}:
        raise ValueError('仅支持 Markdown（.md/.markdown）和 CSV 文件')
    try:
        info = path.stat()
    except FileNotFoundError:
        raise ValueError('预览文件不存在') from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('只能预览普通文件')


def _read_file(path, *, expected_hash=''):
    _file_limit(path)
    try:
        with path.open('rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('只能预览普通文件')
            content = stream.read()
    except FileNotFoundError:
        raise ValueError('预览文件不存在') from None
    except PermissionError:
        raise ValueError('没有权限读取预览文件') from None
    if expected_hash and hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError('节点的固定产物已变化，请重新核对版本')
    try:
        return content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('预览文件必须使用 UTF-8 编码') from None


def _page(name, columns, rows, offset, limit, source):
    total = len(rows)
    if offset > total:
        raise ValueError(f'offset 超出总行数（{total}）')
    return {'kind': 'table', 'name': name, 'columns': columns, 'rows': rows[offset:offset + limit],
            'offset': offset, 'limit': limit, 'total_rows': total,
            'truncated': offset + limit < total, 'source': source}


def _csv(name, text, offset, limit, source):
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        columns = next(reader, [])
        width, total, rows = len(columns), 0, []
        for index, row in enumerate(reader):
            total += 1
            width = max(width, len(row))
            if offset <= index < offset + limit:
                rows.append(row)
    except csv.Error as exc:
        raise ValueError(f'CSV 格式或单元格大小无效：{exc}') from exc
    if offset > total:
        raise ValueError(f'offset 超出总行数（{total}）')
    columns += [f'第 {index + 1} 列' for index in range(len(columns), width)]
    rows = [row + [''] * (width - len(row)) for row in rows]
    return {'kind': 'table', 'name': name, 'columns': columns, 'rows': rows,
            'offset': offset, 'limit': limit, 'total_rows': total,
            'truncated': offset + limit < total, 'source': source}


def _markdown(name, text, offset, limit, source):
    if offset:
        raise ValueError('Markdown 预览不支持非零 offset')
    return {'kind': 'markdown', 'name': name, 'text': text.lstrip('\ufeff'), 'offset': 0,
            'limit': limit, 'total_rows': 0, 'truncated': False, 'source': source}


def _table(name, data, offset, limit, source):
    if isinstance(data, dict) and isinstance(data.get('rows'), list):
        columns = data.get('columns') or []
        rows = data['rows']
    else:
        columns, rows = [], data
    if not isinstance(rows, list) or not isinstance(columns, list):
        raise ValueError('节点表格内容无效')
    if rows and all(isinstance(row, dict) for row in rows):
        columns = list(dict.fromkeys([*columns, *(key for row in rows for key in row)]))
        rows = [[row.get(key, '') for key in columns] for row in rows]
    elif not all(isinstance(row, (list, tuple)) for row in rows):
        raise ValueError('节点表格必须包含对象行或数组行')
    width = max([len(columns), *(len(row) for row in rows)])
    columns = [str(column) for column in columns] + [f'第 {i + 1} 列' for i in range(len(columns), width)]
    rows = [[json.dumps(cell, ensure_ascii=False) if isinstance(cell, (dict, list)) else cell for cell in row]
            + [''] * (width - len(row)) for row in rows]
    return _page(name, columns, rows, offset, limit, source)


def _node_preview(db, root, project_id, chat_id, task_id, task_run_id, revision, artifact_id, reference, offset, limit):
    import project_tasks as tasks
    import task_output_versions
    import workflow_conversation_context
    import workflow_node_workbench as nodes

    task = workflow_conversation_context.require_task(db, project_id, chat_id, task_id)
    if not task_run_id or revision is None:
        raise ValueError('节点预览必须指定 task_run_id 和 output_revision 固定版本')
    run = tasks.get_run(db, task_run_id)
    if run['task_id'] != task_id or run['status'] != 'succeeded':
        raise ValueError('请选择此节点已成功完成的固定运行版本')
    version = task_output_versions.read(db, task, run, revision)
    files = version['output_snapshot'].get('files') or []
    requested = _project_path(root, reference) if reference else None

    def matches(item):
        return any(_project_path(root, item[key]) == requested
                   for key in ('path', 'relative', 'original_path') if item.get(key))

    if not artifact_id:
        if requested is None:
            raise ValueError('请指定节点文件的 path 或 artifact_id')
        indices = [index for index, item in enumerate(files) if matches(item)]
        if len(indices) != 1:
            raise ValueError('path 无法唯一匹配该节点版本的文件，请指定准确的 artifact_id')
        artifact_id = f'file:{indices[0]}'
    item = None
    if artifact_id != 'structured':
        if not re.fullmatch(r'file:[0-9]{1,10}', artifact_id):
            raise ValueError('artifact_id 必须指向该版本的文件或 structured')
        index = int(artifact_id.split(':')[1])
        if index >= len(files):
            raise ValueError('artifact_id 不属于该节点版本')
        item = files[index]
        if requested is not None and not matches(item):
            raise ValueError('path 与指定的 artifact_id 不匹配')
        path = _project_path(root, item['path'])
        _file_limit(path)
    elif requested is not None:
        raise ValueError('文件 path 不能指向结构化产物')

    selection = nodes.Selection(project_id, task_id, task_run_id, revision)
    result = nodes.workspace_preview(db, selection, artifact_id, authority=tasks.CommandContext.ui(project_id), task=task)
    source = {'kind': 'node', 'project_id': project_id, 'chat_id': chat_id, 'task_id': task_id,
              'task_run_id': task_run_id, 'output_revision': revision, 'artifact_id': artifact_id}
    if item is not None:
        source['relative'] = path.relative_to(root).as_posix()
        # Relative links belong to the original document directory, including for revised copies.
        original = item.get('original_path') or item.get('relative') or item['path']
        source['base_relative'] = _project_path(root, original).relative_to(root).as_posix()
        text = _read_file(path, expected_hash=result.get('content_hash') or item.get('content_hash') or item.get('sha256'))
        name = item.get('name') or path.name
        return (_csv if path.suffix.lower() == '.csv' else _markdown)(name, text, offset, limit, source)
    name = result.get('name') or task['title']
    if result['kind'] == 'table':
        return _table(name, result.get('data'), offset, limit, source)
    text = result.get('text', result.get('data'))
    if result['kind'] not in {'markdown', 'text', 'checklist'} or not isinstance(text, str):
        raise ValueError('此节点产物不是 Markdown 或表格')
    return _markdown(name, text, offset, limit, source)


def preview(db, project_id, chat_id, *, relative='', path='', task_id='', task_run_id='',
            output_revision=None, artifact_id='', offset=0, limit=DEFAULT_LIMIT):
    """Return the preview value; route() wraps it in the HTTP response envelope."""
    for name, value in (('project_id', project_id), ('chat_id', chat_id)):
        _string(value, name, required=True)
    for name, value in (('relative', relative), ('path', path), ('task_id', task_id),
                        ('task_run_id', task_run_id), ('artifact_id', artifact_id)):
        _string(value, name)
    offset = _integer(offset, 'offset', 0, MAX_OFFSET)
    limit = _integer(limit, 'limit', 1, MAX_LIMIT)
    revision = None if output_revision is None else _integer(output_revision, 'output_revision', 0, MAX_OFFSET)
    chat = db.db.execute('SELECT task_id FROM chats WHERE id=?', (chat_id,)).fetchone()
    if not chat or chat['task_id'] != project_id:
        raise ValueError('此对话不属于当前项目')
    root = project_notes.project_root(db, project_id)
    if relative and path and _project_path(root, relative) != _project_path(root, path):
        raise ValueError('relative 与 path 指向不同文件')
    reference = path or relative
    if task_id:
        return _node_preview(db, root, project_id, chat_id, task_id, task_run_id, revision, artifact_id, reference, offset, limit)
    if task_run_id or revision is not None or artifact_id:
        raise ValueError('节点预览必须指定 task_id')
    if not reference:
        raise ValueError('请选择项目内的文件')
    resolved = _project_path(root, reference)
    source = {'kind': 'project', 'project_id': project_id, 'chat_id': chat_id,
              'relative': resolved.relative_to(root).as_posix()}
    text = _read_file(resolved)
    return (_csv if resolved.suffix.lower() == '.csv' else _markdown)(resolved.name, text, offset, limit, source)


def route(method, path, body, query, db):
    if method != 'GET' or path != '/api/project-tasks/file-preview':
        return None

    def q(name, default=''):
        value = query.get(name, default)
        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise ValueError(f'{name} 只能指定一次')
            value = value[0]
        return value

    task_id, alias = q('task_id'), q('id')
    if task_id and alias and task_id != alias:
        raise ValueError('id 与 task_id 不一致')
    return {'preview': preview(db, q('project_id'), q('chat_id'), relative=q('relative'), path=q('path'),
                               task_id=task_id or alias, task_run_id=q('task_run_id'),
                               output_revision=q('output_revision', None), artifact_id=q('artifact_id'),
                               offset=q('offset', 0), limit=q('limit', DEFAULT_LIMIT))}
