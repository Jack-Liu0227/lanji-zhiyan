"""Node artifact workspaces with live previews and immutable reviewed versions."""
from dataclasses import dataclass
import csv
import io
import json
from pathlib import Path
import re

import project_tasks as tasks

TEXT = {'.md', '.markdown', '.txt', '.json', '.yaml', '.yml', '.csv', '.tex', '.bib', '.py', '.js', '.css', '.html'}
MAX_TEXT = 8 * 1024 * 1024


@dataclass(frozen=True)
class Selection:
    project_id: str
    task_id: str
    task_run_id: str = ''
    output_revision: int | None = None


def _workflow(db, task):
    if not task.get('workflow_run_id') or task.get('parent_task_id'):
        return None
    row = db.db.execute('SELECT r.project_id,e.settings FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id WHERE r.id=?', (task['workflow_run_id'],)).fetchone()
    if not row or row['project_id'] != task['project_id']:
        return None
    settings = json.loads(row['settings'])
    if settings.get('task_ids', {}).get(task['workflow_node_id']) != task['id']:
        return None
    return settings


def mark_agent_started(db, run):
    """Only the trusted Agent tool boundary calls this before dispatch."""
    if run['settings'].get('agent_invocation', {}).get('source') == 'agent_tool':
        return
    if run['status'] in ('succeeded', 'cancelled') or (run['nodes'] and not run['settings'].get('prepared')):
        raise ValueError('此运行已由其他入口执行，请由专家准备并启动新的工作流')
    import workflow_tasks
    context = getattr(db, 'usage_context', {}) or {}
    authority = getattr(db, 'project_task_context', None)
    run['settings']['agent_invocation'] = {'source': 'agent_tool', 'started_at': tasks._now(),
                                         'chat_id': context.get('owner_id', ''),
                                         'parent_task_id': getattr(authority, 'parent_task_id', '')}
    workflow_tasks._save(db, run)


def target(db, task, run=None):
    run = run or task.get('latest_run')
    settings = _workflow(db, task)
    empty = {'available': False, 'workspace': settings is not None, 'project_id': task['project_id'],
             'task_id': task['id'], 'workflow_run_id': task.get('workflow_run_id', ''),
             'node_id': task.get('workflow_node_id', ''), 'title': task['title']}
    snapshot = (run or {}).get('output_snapshot') or {}
    if not run or run.get('status') != 'succeeded' or not (snapshot.get('files') or snapshot.get('content')):
        return empty
    if settings is None:
        return empty
    return {'available': True, 'workspace': True, 'project_id': task['project_id'], 'task_id': task['id'],
            'workflow_run_id': task['workflow_run_id'], 'workflow_node_id': task['workflow_node_id'],
            'node_id': task['workflow_node_id'], 'task_run_id': run['id'],
            'output_revision': run['checkpoint'].get('output_revision', 0), 'title': task['title']}


def _files(db, task, run):
    root = tasks._root(db, task['project_id'])
    files = run['output_snapshot'].get('files') or []
    if not files and not run['output_snapshot'].get('content'):
        raise ValueError('节点尚未形成有效产物')
    for item in files:
        path = Path(item.get('path', '')).resolve()
        expected = item.get('content_hash') or item.get('sha256')
        if not expected or not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size or tasks._hash(path) != expected:
            raise ValueError('节点的固定产物已变化或不存在，请核对执行结果')
    return files


def _load(db, selection, *, task=None):
    import task_output_versions
    if not selection.project_id or not selection.task_id:
        raise ValueError('请选择当前项目的节点结果')
    if task is None:
        task = tasks.get(db, selection.task_id, project_id=selection.project_id)
    if task['metadata'].get('deleted_at'):
        raise ValueError('节点任务已删除，请先恢复任务记录')
    settings = _workflow(db, task)
    if not settings:
        raise ValueError('请由 Agent 调用工作流，并在节点完成后查看本次产物')
    run_id = selection.task_run_id or task['latest_run_id']
    if not run_id:
        raise ValueError('此节点尚未执行完成')
    if task.get('latest_run') and task['latest_run'].get('id') == run_id:
        run = task['latest_run']
    else:
        run = tasks.get_run(db, run_id)
    if run['task_id'] != task['id'] or run['status'] != 'succeeded':
        raise ValueError('仅已成功完成的节点尝试可以查看产物')
    task_output_versions.remember(db, task, run)
    revision = run['checkpoint'].get('output_revision', 0) if selection.output_revision is None else selection.output_revision
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError('产物版本无效')
    run = task_output_versions.read(db, task, run, revision)
    _files(db, task, run)
    return task, run, settings


def _identity(task, run):
    return {'project_id': task['project_id'], 'workflow_run_id': task['workflow_run_id'],
            'workflow_node_id': task['workflow_node_id'], 'task_id': task['id'],
            'task_run_id': run['id'], 'task_version': task['version'],
            'output_revision': run['checkpoint'].get('output_revision', 0),
            'output_digest': tasks._digest(run['output_snapshot'])}


def _editable(task, run):
    latest = task.get('latest_run') or {}
    return (task['status'] == 'awaiting_review' and task['latest_run_id'] == run['id'] and
            latest.get('checkpoint', {}).get('output_revision', 0) == run['checkpoint'].get('output_revision', 0))


def _kind(node, content):
    import node_output_editors
    if node.get('node_kind') == 'tool':
        return 'tool'
    kind = node_output_editors.describe({'content': content}, node)
    return {'evidence_graph': 'graph'}.get(kind, kind)


def _file_kind(item):
    suffix = Path(item['path']).suffix.lower()
    if suffix in ('.md', '.markdown'):
        return 'markdown'
    if suffix == '.csv':
        return 'table'
    if suffix in TEXT:
        return 'text'
    if suffix == '.pdf':
        return 'pdf'
    if suffix in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg'):
        return 'image'
    return 'files'


def _history(db, task):
    return [{'task_run_id': row['task_run_id'], 'output_revision': row['revision'],
             'label': '第 ' + str(row['attempt']) + ' 次 · 修订 v' + str(row['revision']),
             'created_at': row['created_at']}
            for row in db.db.execute('SELECT v.task_run_id,v.revision,v.created_at,r.attempt FROM project_task_output_versions v JOIN project_task_runs r ON r.id=v.task_run_id WHERE v.task_id=? AND r.status=\'succeeded\' ORDER BY r.attempt DESC,v.revision DESC', (task['id'],))]


def _previous(db, task, run):
    import task_output_versions
    revision = run['checkpoint'].get('output_revision', 0)
    if revision:
        row = db.db.execute('SELECT revision FROM project_task_output_versions WHERE task_run_id=? AND revision<? ORDER BY revision DESC LIMIT 1', (run['id'], revision)).fetchone()
        if row:
            return task_output_versions.read(db, task, run, row['revision'])
    row = db.db.execute("SELECT id FROM project_task_runs WHERE task_id=? AND status='succeeded' AND attempt<? ORDER BY attempt DESC LIMIT 1", (task['id'], run['attempt'])).fetchone()
    return tasks.get_run(db, row['id']) if row else None


def read(db, selection, *, authority, window_id='', task=None, include_content=True):
    import project_task_review
    tasks._authority(authority, selection.project_id, human=True)
    task, run, settings = _load(db, selection, task=task)
    node = run['input_snapshot'].get('execution_context', {}).get('node_config') or task['metadata'].get('node_config') or {}
    raw_content = run['output_snapshot'].get('content', '')
    content = raw_content if include_content else None
    editable = _editable(task, run)
    artifacts = []
    kind = _kind(node, raw_content)
    if raw_content and kind != 'files' and (kind != 'text' or not isinstance(raw_content, str) or not run['output_snapshot'].get('files')):
        artifacts.append({'id': 'structured', 'name': node.get('label') or task['title'], 'kind': kind,
                          'editable': editable and node.get('node_kind') != 'tool' and node.get('step') != 'convert'})
    for index, item in enumerate(run['output_snapshot'].get('files') or []):
        can_edit = project_task_review.can_edit_file(task, item) if hasattr(project_task_review, 'can_edit_file') else (task['metadata'].get('stage', 'task') in ('', 'task', 'outline', 'write', 'review') and Path(item['path']).suffix.lower() in TEXT)
        artifacts.append({'id': 'file:' + str(index), 'name': item.get('name') or Path(item['path']).name,
                          'kind': _file_kind(item), 'content_hash': item.get('content_hash', item.get('sha256')),
                          'editable': editable and can_edit and node.get('node_kind') != 'tool',
                          'size': Path(item['path']).stat().st_size})
    if node.get('step') == 'convert':
        import workflow_export_revision
        if workflow_export_revision.source(db, task, run):
            artifacts.append({'id': 'export-source', 'name': '排版源稿 · 编辑并重新生成', 'kind': 'export', 'editable': editable})
    if node.get('step') in ('notes', 'convert'):
        artifacts.sort(key=lambda item: 0 if item['kind'] == 'pdf' else 1)
    drafts = [dict(row) for row in db.db.execute('SELECT * FROM workflow_node_drafts WHERE task_run_id=? AND owner_id=? ORDER BY updated_at DESC', (run['id'], authority.actor_id))
              if not window_id or row['window_id'] == window_id]
    for draft in drafts:
        draft['base_revision'] = draft['revision']
        draft['stale'] = draft['revision'] != run['checkpoint'].get('output_revision', 0)
    previous = _previous(db, task, run) if include_content else None
    sync_errors = {}
    for event in task['events']:
        if event['kind'] == 'projection_error':
            sync_errors[event['data']['adapter']] = event['data']
        elif event['kind'] == 'projection_recovered':
            sync_errors.pop(event['data']['adapter'], None)
    return {'identity': _identity(task, run), 'task': tasks.public_view(task),
            'run': tasks.public_view({'latest_run': run})['latest_run'], 'node': node,
            'artifacts': artifacts, 'history': _history(db, task), 'drafts': drafts, 'editable': editable,
            'content': content, 'previous_content': (previous or {}).get('output_snapshot', {}).get('content'),
            'chat_id': task.get('chat_id', ''), 'origin_chat_id': settings.get('agent_invocation', {}).get('chat_id', ''),
            'checks': node.get('acceptance') or task['output_contract'],
            'can_rework': task['status'] in ('awaiting_review', 'blocked') and task['latest_run_id'] == run['id'],
            'sync_errors': list(sync_errors.values()),
            'fileop_conflicts': [dict(row) for row in db.db.execute("SELECT id,error,updated_at FROM project_task_file_operations WHERE task_id=? AND state='conflict' ORDER BY updated_at DESC", (task['id'],))],
            'scheduling': '按依赖顺序逐个执行，等待人工核查时暂停推进'}


def _artifact(run, artifact_id):
    if artifact_id == 'structured':
        return None
    if not isinstance(artifact_id, str) or not re.fullmatch(r'file:\d+', artifact_id):
        raise ValueError('请选择本次节点产物')
    index = int(artifact_id.split(':')[1])
    files = run['output_snapshot'].get('files') or []
    if index >= len(files):
        raise ValueError('产物不属于本次节点结果')
    return index


def preview(db, selection, artifact_id, *, authority, task=None):
    tasks._authority(authority, selection.project_id, human=True)
    task, run, _ = _load(db, selection, task=task)
    node = run['input_snapshot'].get('execution_context', {}).get('node_config') or task['metadata'].get('node_config') or {}
    if artifact_id == 'export-source':
        import workflow_export_revision
        data = workflow_export_revision.source(db, task, run)
        if not data:
            raise ValueError('本次导出没有可编辑的源稿')
        return {'kind': 'export', 'data': data, 'editable': _editable(task, run)}
    index = _artifact(run, artifact_id)
    previous = _previous(db, task, run)
    if index is None:
        content = run['output_snapshot'].get('content', '')
        return {'kind': _kind(node, content), 'data': content,
                'editable': _editable(task, run) and node.get('node_kind') != 'tool' and node.get('step') != 'convert',
                'previous_content': (previous or {}).get('output_snapshot', {}).get('content')}
    item = run['output_snapshot']['files'][index]
    path = Path(item['path'])
    kind = _file_kind(item)
    import project_task_review
    can_edit = project_task_review.can_edit_file(task, item) if hasattr(project_task_review, 'can_edit_file') else task['metadata'].get('stage', 'task') in ('', 'task', 'outline', 'write', 'review')
    result = {'kind': kind, 'name': item.get('name') or path.name, 'content_hash': tasks._hash(path),
              'editable': _editable(task, run) and can_edit and node.get('node_kind') != 'tool' and path.stat().st_size <= MAX_TEXT}
    if path.suffix.lower() in TEXT and path.stat().st_size <= MAX_TEXT:
        text = path.read_text(encoding='utf-8')
        result['text'] = text
        if path.suffix.lower() == '.csv':
            result['data'] = list(csv.DictReader(io.StringIO(text)))
        if previous and index < len(previous['output_snapshot'].get('files', [])):
            old = Path(previous['output_snapshot']['files'][index]['path'])
            if old.is_file() and old.suffix.lower() in TEXT and old.stat().st_size <= MAX_TEXT:
                result['previous_content'] = old.read_text(encoding='utf-8')
    else:
        import workflow
        result['preview'] = workflow.conv_preview(str(path))
        result['editable'] = False
        if path.suffix.lower() == '.docx' and result['preview'].get('kind') == 'docx':
            from docx import Document
            document = Document(path)
            result.update(kind='text', text='\n'.join([paragraph.text for paragraph in document.paragraphs] +
                ['\t'.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows]))
            result['preview_note'] = '当前设备未生成 PDF 排版预览，此处展示 Word 文件中的文字与表格内容。'
    return result


def _steps(db, task, settings):
    row = db.db.execute('SELECT layout_snapshot FROM workflow_executions WHERE run_id=?', (task['workflow_run_id'],)).fetchone()
    layout = json.loads(row['layout_snapshot'])
    by_id = {node['id']: node for node in layout.get('nodes', [])}
    result = []
    for node_id in layout.get('order') or by_id:
        task_id = settings.get('task_ids', {}).get(node_id)
        current = db.db.execute('SELECT title,status FROM project_tasks WHERE id=?', (task_id,)).fetchone()
        if current:
            result.append({'task_id': task_id, 'node_id': node_id, 'title': current['title'],
                           'status': current['status'], 'step': by_id.get(node_id, {}).get('step', 'task')})
    return result


def _saved(db, task, run):
    """Use only this attempt's recorded tool writes, never the whole project library."""
    import asset_service
    checkpoint = run.get('checkpoint') or {}
    content = ''
    stage = task['metadata'].get('stage', 'task')
    saved_rows = {}
    for signature, result in (checkpoint.get('done_tools') or {}).items():
        if signature.split('\0', 1)[0] in ('search', 'paper_search') and isinstance(result, dict):
            for row in result.get('saved') or []:
                if isinstance(row, dict) and row.get('id') and row.get('task_id') == task['project_id']:
                    saved_rows[row['id']] = row
    if stage == 'search' and saved_rows:
        content = list(saved_rows.values())
    root = tasks._root(db, task['project_id'])
    files = []
    for asset_id in dict.fromkeys(checkpoint.get('output_assets') or []):
        asset = asset_service.get(db, asset_id)
        if not asset or asset['project_id'] != task['project_id'] or asset.get('storage') != 'file':
            continue
        metadata = asset.get('metadata') or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if metadata.get('project_task_id') != task['id']:
            continue
        path = Path(asset['canonical_uri']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
            continue
        files.append({'asset_id': asset_id, 'path': str(path), 'name': path.name,
                      'content_hash': tasks._hash(path), 'relative': path.relative_to(root).as_posix()})
    if stage == 'download' and files:
        content = [{'id': file['asset_id'], 'title': file['name'], 'sha256': file['content_hash']}
                   for file in files if Path(file['path']).suffix.lower() == '.pdf']
    return {'content': content, 'files': files}


def workspace(db, selection, *, authority, window_id='', task=None):
    tasks._authority(authority, selection.project_id, human=True)
    if task is None:
        task = tasks.get(db, selection.task_id, project_id=selection.project_id)
    settings = _workflow(db, task)
    if settings is None or task['metadata'].get('deleted_at'):
        raise ValueError('请选择当前项目中的工作流节点')
    run = tasks.get_run(db, selection.task_run_id) if selection.task_run_id else task.get('latest_run')
    if run and run['task_id'] != task['id']:
        raise ValueError('产物不属于当前节点')
    if run and run['status'] == 'succeeded':
        result = read(db, selection, authority=authority, window_id=window_id, task=task, include_content=False)
        return {**result, 'provisional': False, 'steps': _steps(db, task, settings)}
    run = run or {'id': '', 'attempt': 0, 'status': task['status'], 'checkpoint': {}, 'input_snapshot': {}, 'executor_snapshot': {}}
    output = _saved(db, task, run)
    node = task['metadata'].get('node_config') or {'step': task['metadata'].get('stage', 'task')}
    artifacts = []
    if output['content']:
        artifacts.append({'id': 'structured', 'kind': _kind(node, output['content']), 'name': task['title'], 'editable': False})
    for item in output['files']:
        artifacts.append({'id': 'asset:' + item['asset_id'], 'kind': _file_kind(item), 'name': item['name'],
                          'content_hash': item['content_hash'], 'editable': False})
    candidate = {**run, 'output_snapshot': output}
    return {'identity': _identity(task, candidate), 'task': tasks.public_view(task),
            'run': tasks.public_view({'latest_run': candidate})['latest_run'], 'node': node,
            'content': output['content'], 'artifacts': artifacts, 'history': [], 'drafts': [], 'editable': False,
            'provisional': True, 'can_collect': bool(artifacts) and task['status'] in ('failed', 'interrupted', 'cancelled', 'blocked'),
            'steps': _steps(db, task, settings), 'origin_chat_id': settings.get('agent_invocation', {}).get('chat_id', ''),
            'checks': task['output_contract'], 'can_rework': False,
            'scheduling': '已保存的中间产物可预览，固定为核查版本后可编辑并交给下一节点'}


def workspace_preview(db, selection, artifact_id, *, authority, task=None):
    if task is None:
        task = tasks.get(db, selection.task_id, project_id=selection.project_id)
    run = tasks.get_run(db, selection.task_run_id) if selection.task_run_id else task.get('latest_run')
    if run and run['status'] == 'succeeded':
        return preview(db, selection, artifact_id, authority=authority, task=task)
    result = workspace(db, selection, authority=authority, task=task)
    if not any(item['id'] == artifact_id for item in result['artifacts']):
        raise ValueError('产物不属于本次节点记录')
    if artifact_id == 'structured':
        return {'kind': _kind(result['node'], result['content']), 'data': result['content'], 'editable': False}
    output = result['run']['output_snapshot']
    item = next(item for item in output['files'] if 'asset:' + item['asset_id'] == artifact_id)
    path = Path(item['path'])
    value = {'kind': _file_kind(item), 'editable': False, 'content_hash': item['content_hash']}
    if path.suffix.lower() in TEXT and path.stat().st_size <= MAX_TEXT:
        value['text'] = path.read_text(encoding='utf-8')
        if path.suffix.lower() == '.csv':
            value['data'] = list(csv.DictReader(io.StringIO(value['text'])))
    else:
        import workflow
        value['preview'] = workflow.conv_preview(str(path))
    return value


def collect(db, task_id, body, *, authority):
    """Freeze saved work after interruption, as a fresh mandatory review attempt."""
    import project_task_execution as worker
    import workflow_execution
    import workflow_tasks
    tasks._authority(authority, authority.project_id, human=True)
    request_key = str(body.get('request_key') or '')
    if not request_key:
        raise ValueError('固定产物需要请求标识')
    with tasks._lock:
        task = tasks.get(db, task_id, project_id=authority.project_id)
        old = db.db.execute('SELECT id FROM project_task_runs WHERE task_id=? AND request_key=?', (task_id, request_key)).fetchone()
        if old:
            return task
        if task['latest_run_id'] != body.get('task_run_id') or task['version'] != body.get('expected_version'):
            raise ValueError('节点已有更新，请重新载入已保存产物')
        if worker.is_active(db.path, task['latest_run_id']):
            raise ValueError('执行线程仍在退出，请稍后再固定产物')
        value = workspace(db, Selection(task['project_id'], task_id), authority=authority)
        if not value.get('can_collect'):
            raise ValueError('请先停止执行，再固定已保存的产物')
        source = value['run']['output_snapshot']
        attempt = tasks.start(db, task_id, authority=authority, request_key=request_key, restart=True)
        db.db.execute('UPDATE project_task_runs SET policy_snapshot=? WHERE id=?',
                      (tasks._json({**attempt['policy_snapshot'], 'manual_review_required': True}), attempt['id']))
        db.db.commit()
        attempt = tasks.get_run(db, attempt['id'])
        workflow_tasks._record_attempt(db, workflow_execution.detail(db, task['workflow_run_id']),
                                      task['workflow_node_id'], task, attempt, attempt['input_snapshot'])
        try:
            capture = {'content': source['content'], 'files': [item['path'] for item in source['files']]}
            output = worker._outputs(db, task, attempt, {}, capture)
            return tasks.complete(db, attempt['id'], {'output_snapshot': output,
                'reply': '已将本节点实际保存的产物固定为人工核查版本。',
                'checkpoint': {'output': output, 'collected_from': body['task_run_id']}})
        except Exception as exc:
            tasks.fail(db, attempt['id'], str(exc))
            raise


def _base(db, identity, authority, *, current=True):
    if not isinstance(identity, dict):
        raise ValueError('请先打开节点产物再操作')
    selection = Selection(str(identity.get('project_id') or ''), str(identity.get('task_id') or ''),
                          str(identity.get('task_run_id') or ''), identity.get('output_revision'))
    tasks._authority(authority, selection.project_id, human=True)
    task, run, _ = _load(db, selection)
    actual = _identity(task, run)
    fields = tuple(actual) if current else tuple(key for key in actual if key != 'task_version')
    if any(identity.get(key) != actual[key] for key in fields):
        raise ValueError('节点结果或版本已变化，请保留草稿并重新载入')
    if current and not _editable(task, run):
        raise ValueError('只能修改或审核当前待核查产物；历史结果请另建任务')
    return task, run


def save_draft(db, identity, body, *, authority):
    task, run = _base(db, identity, authority, current=False)
    artifact_id = str(body.get('artifact_id') or 'structured')
    if artifact_id == 'export-source':
        import workflow_export_revision
        if not workflow_export_revision.source(db, task, run):
            raise ValueError('本次导出没有可编辑的源稿')
    else:
        _artifact(run, artifact_id)
    window_id = str(body.get('window_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', window_id):
        raise ValueError('草稿窗口标识无效')
    text, reason, feedback, instruction = body.get('text', ''), body.get('reason', ''), body.get('feedback', ''), body.get('instruction', '')
    if not all(isinstance(value, str) for value in (text, reason, feedback, instruction)) or len(text.encode()) > MAX_TEXT or len(reason) > 4000 or len(feedback) > 20000 or len(instruction) > 20000:
        raise ValueError('草稿内容过长或格式无效')
    key = (run['id'], identity['output_revision'], artifact_id, authority.actor_id, window_id)
    with tasks._lock, tasks._atomic(db):
        old = db.db.execute('SELECT * FROM workflow_node_drafts WHERE task_run_id=? AND revision=? AND artifact_id=? AND owner_id=? AND window_id=?', key).fetchone()
        if body.get('expected_draft_version', 0) != (old['version'] if old else 0):
            raise ValueError('这份草稿已有更新，请载入最新草稿后合并')
        version = (old['version'] if old else 0) + 1
        db.db.execute('INSERT INTO workflow_node_drafts VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_run_id,revision,artifact_id,owner_id,window_id) DO UPDATE SET text=excluded.text,reason=excluded.reason,feedback=excluded.feedback,instruction=excluded.instruction,version=excluded.version,updated_at=excluded.updated_at',
                      (*key, identity['output_digest'], text, reason, feedback, instruction, version, tasks._now()))
    return dict(db.db.execute('SELECT * FROM workflow_node_drafts WHERE task_run_id=? AND revision=? AND artifact_id=? AND owner_id=? AND window_id=?', key).fetchone())


def _selection(run, value):
    if not value:
        return {}
    if isinstance(value, str):
        if len(value) > 20000:
            raise ValueError('引用选区过长')
        return {'text': value}
    if not isinstance(value, dict):
        raise ValueError('引用选区格式无效')
    artifact = str(value.get('artifact_id') or 'structured')
    index = _artifact(run, artifact)
    text = value.get('text', '')
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError('引用选区过长或格式无效')
    if index is not None:
        path = Path(run['output_snapshot']['files'][index]['path'])
        if path.suffix.lower() not in TEXT or path.stat().st_size > MAX_TEXT:
            if value.get('page') is None:
                raise ValueError('二进制文件引用请提供来源页码')
        elif text and text not in path.read_text(encoding='utf-8'):
            raise ValueError('选区不属于正在查看的固定产物版本')
    return {'artifact_id': artifact, 'text': text, **{k: value[k] for k in ('start', 'end', 'page') if k in value}}


def command(db, task_id, body, *, authority):
    action, identity = str(body.get('action') or ''), body.get('identity')
    if not isinstance(identity, dict) or identity.get('task_id') != task_id or identity.get('project_id') != authority.project_id:
        raise ValueError('操作不属于当前节点或项目')
    tasks._authority(authority, identity['project_id'], human=True)
    request_key = str(body.get('request_key') or '')
    request_digest = tasks._digest(body)
    if action in ('revise', 'revise-graph', 'revise-structured', 'revise-export'):
        if not request_key or len(request_key) > 200:
            raise ValueError('保存修订需要有效的请求标识')
        old = db.db.execute('SELECT * FROM workflow_node_commands WHERE task_id=? AND request_key=?', (task_id, request_key)).fetchone()
        if old:
            if old['request_digest'] != request_digest or old['action'] != action:
                raise ValueError('该请求标识已用于另一份节点修改')
            return {'result': read(db, Selection(authority.project_id, task_id, old['task_run_id'], old['output_revision']), authority=authority)}
    if action == 'review' and not isinstance(body.get('approved'), bool):
        raise ValueError('请明确选择通过或退回')
    if action == 'review' and request_key:
        old = db.db.execute('SELECT * FROM project_task_deliveries WHERE task_id=? AND request_key=?', (task_id, request_key)).fetchone()
        if old:
            if (old['task_run_id'] != identity['task_run_id'] or old['decision'] != ('approved' if body.get('approved') else 'rejected') or
                    old['feedback'] != str(body.get('feedback') or '').strip()[:20000] or
                    tasks._digest(json.loads(old['output_versions'])) != identity['output_digest']):
                raise ValueError('该请求标识已用于另一份核查决定')
            return {'task': tasks.get(db, task_id, project_id=authority.project_id)}
    if action == 'draft':
        return {'draft': save_draft(db, identity, body, authority=authority)}
    if action == 'rework':
        # Domain command checks idempotency before rejecting an old base token.
        _base(db, identity, authority, current=False)
        old = tasks.get_run(db, identity['task_run_id'])
        selection = _selection(old, body.get('selection'))
        run = tasks.rework(db, task_id, authority=authority, base=identity,
                           instruction=body.get('instruction'), request_key=str(body.get('request_key') or ''),
                           selection=selection, tool_args=body.get('tool_args'))
        import project_task_execution
        project_task_execution.kick(db.path, run['id'])
        return {'task': tasks.get(db, task_id), 'run': run}
    if action in ('discuss', 'duplicate', 'reconcile'):
        task, run = _base(db, identity, authority, current=False)
    else:
        task, run = _base(db, identity, authority)
    if action == 'reconcile':
        tasks._notify(db, task_id, reconcile=True)
        return {'task': tasks.get(db, task_id)}
    if action == 'duplicate':
        goal = str(body.get('goal') or body.get('instruction') or '').strip()
        if not goal or not request_key:
            raise ValueError('请填写新任务目标')
        inputs = [{'relative': Path(item['path']).relative_to(tasks._root(db, task['project_id'])).as_posix(),
                   'sha256': item.get('content_hash', item.get('sha256')), 'allow_draft': True}
                  for item in run['output_snapshot']['files']]
        created = tasks.create(db, {'project_id': task['project_id'], 'title': '关联任务 · ' + task['title'],
                                   'goal': goal, 'executor_binding_id': task['executor_binding_id'], 'member_id': task['member_id'],
                                   'inputs': inputs, 'scope': task['scope'], 'review_required': True,
                                   'metadata': {'source_node': identity}}, authority=authority,
                               request_key='node-related:' + task_id + ':' + request_key)
        return {'task': created}
    if action == 'discuss':
        message = str(body.get('message') or '').strip()
        if not message or len(message) > 20000 or not request_key:
            raise ValueError('请填写讨论内容')
        selection = _selection(run, body.get('selection'))
        context = {'identity': identity, 'requirements': task['goal'], 'outputs': run['output_snapshot'], 'selection': selection,
                   'dependencies': run['input_snapshot'].get('dependencies', [])}
        encoded = tasks._json(context)
        if len(encoded) > 120000:
            context['outputs'] = {'files': run['output_snapshot']['files'], 'content': str(run['output_snapshot'].get('content', ''))[:40000]}
        prompt = '讨论以下固定节点结果，给出建议；本次讨论不修改节点文件或审核状态。\n' + tasks._json(context) + '\n用户问题：' + message
        import agent_jobs
        import project_task_execution
        node = task['metadata'].get('node_config') or {}
        specs, _ = project_task_execution._tools(db, task, run, node)
        readonly_tools = [spec['function']['name'] for spec in specs
                          if spec['function']['name'] in ('doc_read', 'map_read', 'workflow_context_read', 'library_overview', 'asset_read', 'project_sources', 'paper_info', 'note_read')]
        job = agent_jobs.start(db.path, {'task_id': task['chat_id'], 'message': prompt,
                                        'executor_binding_id': task['executor_binding_id'], 'member_id': task['member_id'],
                                        '_node_discussion': True, '_node_discussion_tools': readonly_tools,
                                        'idempotency_key': 'node-discuss:' + task_id + ':' + request_key})
        return {'chat_id': task['chat_id'], 'run_id': job.run_id}
    import project_task_review
    revision_body = {'reviewed_run_id': run['id'], 'expected_version': task['version'], 'reason': body.get('reason', ''),
                     '_command_receipt': {'request_key': request_key, 'action': action, 'request_digest': request_digest}}
    if action == 'revise-export':
        import workflow_export_revision
        task = workflow_export_revision.revise(db, task_id, {**revision_body, 'content': body.get('content')}, authority=authority)
    elif action == 'revise':
        index = _artifact(run, body.get('artifact_id'))
        if index is None:
            raise ValueError('结构化产物请使用对应编辑器')
        item = run['output_snapshot']['files'][index]
        task = project_task_review.revise(db, task_id, {**revision_body, 'output_index': index,
                                         'expected_hash': item.get('content_hash', item.get('sha256')), 'text': body.get('text')}, authority=authority)
    elif action == 'revise-graph':
        task = project_task_review.revise_graph(db, task_id, {**revision_body, 'graph': body.get('graph')}, authority=authority)
    elif action == 'revise-structured':
        task = project_task_review.revise_structured(db, task_id, {**revision_body, 'content': body.get('content')}, authority=authority)
    elif action == 'review':
        task = tasks.review(db, task_id, approved=body.get('approved'), feedback=body.get('feedback', ''), authority=authority,
                            request_key=request_key, reviewed_run_id=run['id'], reviewed_output_version=identity['output_revision'])
        if task['status'] == 'delivered':
            import workflow_execution
            current = workflow_execution.detail(db, task['workflow_run_id'])
            if current['status'] == 'awaiting_review':
                workflow_execution._status(db, task['workflow_run_id'], 'queued')
            workflow_execution.kick(db.path, task['workflow_run_id'])
        return {'task': task}
    else:
        raise ValueError('不支持的节点产物操作')
    return {'result': read(db, Selection(task['project_id'], task_id), authority=authority)}
