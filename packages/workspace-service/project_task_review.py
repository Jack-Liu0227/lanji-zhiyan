"""Human revisions preserve fixed outputs and recover interrupted working-file writes."""
from __future__ import annotations

import json
import math
from pathlib import Path
import uuid

import project_tasks as tasks
import task_output_versions as versions

TEXT = {'.md', '.markdown', '.txt', '.json', '.yaml', '.yml', '.csv', '.tex', '.bib', '.py', '.js', '.css', '.html'}
DOCUMENT_STAGES = {'outline', 'write', 'review'}
_TEXT_STAGES = {'', 'task', *DOCUMENT_STAGES, 'entrans', 'notes'}


def _tool(task):
    return task.get('metadata', {}).get('node_config', {}).get('node_kind') == 'tool'


def can_edit_file(task, item):
    stage = task.get('metadata', {}).get('stage', 'task')
    name = item.get('name') or Path(item.get('path', '')).name
    structured = not isinstance((task.get('latest_run') or {}).get('output_snapshot', {}).get('content', ''), str)
    summary = name in ('result.md', 'reviewed-result.json')
    return (not _tool(task) and stage in _TEXT_STAGES and Path(name).suffix.lower() in TEXT and
            not (stage in DOCUMENT_STAGES | {'notes', 'entrans'} and summary and structured))


def _current(db, task_id, body, authority):
    task = tasks.get(db, task_id, project_id=authority.project_id)
    tasks._authority(authority, task['project_id'], human=True)
    run = task['latest_run']
    if task['status'] != 'awaiting_review' or not run or run['status'] != 'succeeded':
        raise ValueError('仅待人工核查的产物可以修订；已交付版本请在新运行中修改')
    if _tool(task):
        raise ValueError('固定工具的真实输出不可手动改写，请修改参数后重新执行')
    if body.get('reviewed_run_id') != run['id'] or body.get('expected_version') != task['version']:
        raise ValueError('任务已有更新，请保留内容并重新载入当前产物')
    if body.get('reviewed_output_version', run['checkpoint'].get('output_revision', 0)) != run['checkpoint'].get('output_revision', 0):
        raise ValueError('产物修订版本已变化，请重新打开')
    tasks._validate_versions(db, task, run)
    return task, run


def _folder(db, task, run):
    root = tasks._root(db, task['project_id'], writable=True)
    folder = root / '.tasks' / task['id'] / run['id'] / 'revisions' / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _candidate_file(db, task, run, source, destination, text, revision):
    import asset_service
    asset_service.atomic_write_text(destination, text)
    asset = asset_service.register_file(db, destination, project_id=task['project_id'], kind='task_output',
                                        source='human_revision', run_id=run['id'], body=text,
                                        metadata={'project_task_id': task['id'], 'task_run_id': run['id'],
                                                  'revision': revision, 'original_path': source['path']})
    digest = tasks._hash(destination)
    return {**source, 'path': str(destination), 'asset_id': asset['id'], 'content_hash': digest, 'sha256': digest,
            'output_revision': revision, 'revision_source': 'human'}


def _commit(db, task, run, output, checkpoint, body, authority, operation, index, editor):
    revision = checkpoint['output_revision']
    candidate = {**run, 'checkpoint': checkpoint, 'output_snapshot': output}
    tasks._validate_versions(db, task, candidate)
    with tasks._atomic(db):
        current = tasks._task(db, task['id'])
        if (current['version'] != task['version'] or current['status'] != 'awaiting_review' or
                current['latest_run_id'] != run['id']):
            raise ValueError('任务状态已变化，请保留内容并刷新')
        old, new = run['output_snapshot']['files'][index], output['files'][index]
        db.db.execute('INSERT INTO project_task_output_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('revision-' + uuid.uuid4().hex, task['id'], run['id'], revision, index,
                       old['path'], new['path'], old.get('content_hash', old.get('sha256', '')),
                       new.get('content_hash', new.get('sha256', '')), authority.actor_id,
                       str(body.get('reason') or '人工修订')[:4000], tasks._now()))
        db.db.execute('UPDATE project_task_runs SET output_snapshot=?,checkpoint=? WHERE id=?',
                      (tasks._json(output), tasks._json(checkpoint), run['id']))
        db.db.execute('UPDATE project_tasks SET version=version+1,updated_at=? WHERE id=?', (tasks._now(), task['id']))
        versions.remember(db, task, candidate, commit=False)
        receipt = body.get('_command_receipt')
        if receipt:
            if not isinstance(receipt, dict) or any(not isinstance(receipt.get(key), str) or not receipt[key]
                                                    for key in ('request_key', 'action', 'request_digest')):
                raise ValueError('修订请求记录无效')
            db.db.execute('INSERT INTO workflow_node_commands '
                          '(task_id,request_key,action,request_digest,task_run_id,output_revision,created_at) VALUES (?,?,?,?,?,?,?)',
                          (task['id'], receipt['request_key'], receipt['action'], receipt['request_digest'],
                           run['id'], revision, tasks._now()))
        tasks._event(db, task['id'], 'output_revised', {'revision': revision, 'output_index': index,
                                                     'editor': editor, 'actor_id': authority.actor_id}, run['id'])
        versions.finish(db, operation)
    tasks._notify(db, task['id'])
    return tasks.get(db, task['id'])


def read_graph(db, task_id, project_id):
    task = tasks.get(db, task_id, project_id=project_id)
    run = task.get('latest_run') or {}
    if task['metadata'].get('stage') != 'map' or run.get('status') != 'succeeded' or not run.get('output_snapshot'):
        raise ValueError('此节点没有已经完成的证据链图谱产物')
    return {'graph': run['checkpoint']['output']['content'], 'task_version': task['version'],
            'run_id': run['id'], 'output_revision': run['checkpoint'].get('output_revision', 0),
            'editable': task['status'] == 'awaiting_review' and not _tool(task)}


def _graph(graph):
    import workflow_documents
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        raise ValueError('请提供图谱节点与连线')
    if not 1 <= len(graph['nodes']) <= 500 or len(graph['edges']) > 2000:
        raise ValueError('图谱需包含 1 至 500 个节点，连线不能超过 2000 条')
    ids = set()
    for node in graph['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or node['id'] in ids:
            raise ValueError('图谱节点 ID 必须唯一')
        if not isinstance(node.get('label'), str) or not node['label'].strip() or len(node['label']) > 120:
            raise ValueError('图谱节点标题需为 1 至 120 字符')
        if node.get('type', 'note') not in workflow_documents.MAP_TYPES:
            raise ValueError('图谱节点类型无效')
        for key, limit in (('detail', 600), ('ref', 300)):
            if not isinstance(node.get(key, ''), str) or len(node.get(key, '')) > limit:
                raise ValueError('图谱说明或来源过长')
        if any(key in node and (isinstance(node[key], bool) or not isinstance(node[key], (int, float)) or
                               not math.isfinite(node[key])) for key in ('x', 'y')):
            raise ValueError('图谱坐标无效')
        ids.add(node['id'])
    for edge in graph['edges']:
        if not isinstance(edge, dict) or not isinstance(edge.get('from'), str) or not isinstance(edge.get('to'), str) or edge['from'] not in ids or edge['to'] not in ids:
            raise ValueError('连线必须指向已有节点')
        if not isinstance(edge.get('label', ''), str) or len(edge.get('label', '')) > 40:
            raise ValueError('连线说明不能超过 40 字符')
    return json.loads(json.dumps(graph, ensure_ascii=False, allow_nan=False))


def revise_graph(db, task_id, body, *, authority):
    import asset_service
    import workflow
    import workflow_state
    import project_task_execution
    graph = _graph(body.get('graph'))
    with tasks._lock:
        task, run = _current(db, task_id, body, authority)
        if task['metadata'].get('stage') != 'map':
            raise ValueError('仅证据链节点可修订图谱')
        current = workflow_state.capture(db, task['project_id'], 'map')
        if current['fingerprint'] != run['checkpoint']['output'].get('fingerprint'):
            raise ValueError('项目图谱已在其他页面修改，请重试节点后再核查')
        tasks._validate_contract(task['output_contract'], {**run['output_snapshot'], 'content': graph})
        revision = int(run['checkpoint'].get('output_revision', 0)) + 1
        folder = _folder(db, task, run)
        working = workflow.map_path(db, task['project_id']).resolve()
        staged = folder / 'graph-candidate.json'
        asset_service.atomic_write_text(staged, json.dumps({**graph, 'updated': tasks._now()}, ensure_ascii=False, indent=1))
        versions.remember(db, task, run)
        operation = versions.begin(db, task, run, revision,
                                   [{'path': str(working), 'source': str(staged), 'old_hash': tasks._hash(working)}])
        try:
            versions.apply(db, task, operation)
            capture = workflow_state.capture(db, task['project_id'], 'map')
            output = project_task_execution._outputs(db, task, run, {'reply': run['checkpoint'].get('reply', '')},
                                                      capture, folder=folder / 'outputs')
            checkpoint = {**run['checkpoint'], 'output': capture, 'output_revision': revision}
            return _commit(db, task, run, output, checkpoint, body, authority, operation, 0, 'evidence_graph')
        except Exception as exc:
            versions.rollback(db, task, operation, str(exc))
            raise


def _output(db, task, index):
    run = task.get('latest_run') or {}
    files = run.get('output_snapshot', {}).get('files', [])
    if run.get('status') != 'succeeded' or not 0 <= index < len(files):
        raise ValueError('此节点尚无已完成的指定产物')
    item = files[index]
    path = Path(item['path']).resolve()
    if not path.is_relative_to(tasks._root(db, task['project_id'])) or not path.is_file():
        raise ValueError('产物已不存在或超出项目范围')
    if path.suffix.lower() not in TEXT or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('此产物不支持文本修订，请使用对应研究工具编辑')
    if not can_edit_file(task, item):
        raise ValueError('此阶段包含结构化产物，请使用节点结构化编辑器')
    return item, path


def read(db, task_id, project_id, index):
    task = tasks.get(db, task_id, project_id=project_id)
    item, path = _output(db, task, index)
    return {'text': path.read_text(encoding='utf-8'), 'content_hash': tasks._hash(path), 'task_version': task['version'],
            'run_id': task['latest_run']['id'], 'output_revision': task['latest_run']['checkpoint'].get('output_revision', 0),
            'editable': task['status'] == 'awaiting_review', 'name': item.get('name') or path.name}


def revise(db, task_id, body, *, authority):
    import workflow_state
    with tasks._lock:
        task, run = _current(db, task_id, body, authority)
        index = int(body.get('output_index', 0))
        item, path = _output(db, task, index)
        old_hash = tasks._hash(path)
        if body.get('expected_hash') != old_hash:
            raise ValueError('产物已被修改，请重新载入后再保存')
        text = body.get('text')
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('修订内容不能为空且不能超过2MB')
        revision = int(run['checkpoint'].get('output_revision', 0)) + 1
        output = json.loads(tasks._json(run['output_snapshot']))
        checkpoint = dict(run['checkpoint'])
        old_text = path.read_text(encoding='utf-8')
        matching = []
        for file_index, file in enumerate(output['files']):
            candidate = Path(file['path'])
            same = file.get('content_hash', file.get('sha256')) == old_hash
            if not same and candidate.suffix.lower() in TEXT and candidate.is_file() and candidate.stat().st_size <= 2 * 1024 * 1024:
                try:
                    same = candidate.read_text(encoding='utf-8') == old_text
                except UnicodeError:
                    pass
            if same:
                matching.append(file_index)
        structured = isinstance(output.get('content'), (dict, list))
        primary = (item.get('name') or path.name) in ('result.md', 'reviewed-result.json') or output.get('content') == old_text
        if structured and not primary:
            try:
                primary = json.loads(old_text) == output['content']
            except ValueError:
                pass
        if primary:
            if structured:
                try:
                    output['content'] = json.loads(text)
                except ValueError as exc:
                    raise ValueError('JSON 产物修订必须保持有效 JSON') from exc
            else:
                output['content'] = text
        if output.get('reply') == old_text:
            output['reply'] = text
        tasks._validate_contract(task['output_contract'], output)
        stage = task['metadata'].get('stage', 'task')
        originals = {str(Path(v['path']).resolve()): v['sha256'] for v in output.get('original_versions', [])}
        replacements = []
        folder = _folder(db, task, run)
        for file_index in matching:
            file = output['files'][file_index]
            changed = _candidate_file(db, task, run, file, folder / (str(file_index) + '-' + Path(file['path']).name), text, revision)
            if stage in DOCUMENT_STAGES | {'entrans', 'notes'} and file.get('original_path'):
                source = Path(file['original_path']).resolve()
                expected = originals.get(str(source), file.get('content_hash', file.get('sha256')))
                if str(source) not in {r['path'] for r in replacements}:
                    replacements.append({'path': str(source), 'source': changed['path'], 'old_hash': expected})
            output['files'][file_index] = changed
        if stage in DOCUMENT_STAGES and not replacements:
            raise ValueError('没有找到对应的研究文档，请在研究工具中编辑')
        versions.remember(db, task, run)
        operation = versions.begin(db, task, run, revision, replacements)
        try:
            versions.apply(db, task, operation)
            if replacements:
                capture = workflow_state.capture(db, task['project_id'], stage)
                if not capture['valid']:
                    raise ValueError('修订后的研究文档未形成有效产物')
                checkpoint['output'] = capture
                output['content'] = capture['content']
                summary = capture['content'] if isinstance(capture['content'], str) else json.dumps(capture['content'], ensure_ascii=False, indent=2)
                for file_index, file in enumerate(output['files']):
                    name = file.get('name') or Path(file['path']).name
                    if file_index not in matching and name in ('result.md', 'reviewed-result.json'):
                        destination = folder / ('summary-' + str(file_index) + '.md')
                        output['files'][file_index] = _candidate_file(db, task, run, file, destination, summary, revision)
            else:
                checkpoint['output'] = output
            for original in output.get('original_versions', []):
                if str(Path(original['path']).resolve()) in {r['path'] for r in replacements}:
                    original['sha256'] = tasks._hash(Path(original['path']))
            checkpoint['output_revision'] = revision
            return _commit(db, task, run, output, checkpoint, body, authority, operation, index, 'text')
        except Exception as exc:
            versions.rollback(db, task, operation, str(exc))
            raise


def revise_structured(db, task_id, body, *, authority):
    import node_output_editors
    import workflow_state
    with tasks._lock:
        task, run = _current(db, task_id, body, authority)
        stage = task['metadata'].get('stage', 'task')
        if stage == 'map':
            return revise_graph(db, task_id, {**body, 'graph': body.get('content')}, authority=authority)
        output = json.loads(tasks._json(run['output_snapshot']))
        content = node_output_editors.parse_content(stage, body.get('content'), output.get('content'))
        output['content'] = content
        output['review_annotations'] = body.get('content')
        tasks._validate_contract(task['output_contract'], output)
        revision = int(run['checkpoint'].get('output_revision', 0)) + 1
        folder = _folder(db, task, run)
        index = next((i for i, file in enumerate(output['files'])
                      if (file.get('name') or Path(file['path']).name) in ('result.md', 'reviewed-result.json')), 0)
        output['files'][index] = _candidate_file(db, task, run, output['files'][index], folder / 'reviewed-result.json',
                                                json.dumps(content, ensure_ascii=False, indent=2), revision)
        output['files'][index]['name'] = 'reviewed-result.json'
        rows = []
        if task['metadata'].get('legacy_stage_compat'):
            baseline = run['checkpoint'].get('output', {}).get('content', run['output_snapshot'].get('content'))
            rows.append(node_output_editors.overlay_change(db, task['project_id'], stage, baseline, content))
        versions.remember(db, task, run)
        operation = versions.begin(db, task, run, revision, [], rows=rows)
        try:
            versions.apply(db, task, operation)
            capture = workflow_state.capture(db, task['project_id'], stage) if rows else output
            checkpoint = {**run['checkpoint'], 'output': capture, 'output_revision': revision}
            return _commit(db, task, run, output, checkpoint, body, authority, operation, index, node_output_editors.kind(task['metadata'].get('node_config') or {'step': stage}, content))
        except Exception as exc:
            versions.rollback(db, task, operation, str(exc))
            raise
