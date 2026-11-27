"""Bound model input while keeping complete, addressable workflow checkpoints."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


_IDENTITY_FIELDS = {
    'id', 'goal', 'task_goal', 'name', 'title', 'label', 'path', 'relative', 'artifact',
    'canonical_uri', 'url', 'doi', 'section', 'tool', 'exists', 'read_with',
    'output_contract', 'required_files', 'file_rules', 'acceptance',
    'sha256', 'evidence_ids', 'download_with', 'citation_key', 'bibtex_key',
    'status', 'format', 'step', 'role', 'source', 'type', 'tool_name',
    'feedback', 'requirements', 'constraints', 'evidence_level',
    'outputs_ref', 'upstream_manifest_ref', 'content_ref',
}


SKILL_CATALOG_PREFIX = 'WORKFLOW_SKILL_CATALOG '
DOCUMENT_SOURCES_TAG = '\nWORKFLOW_DOCUMENT_SOURCES '


def source_system(instructions: str, data: dict, *, read_with: dict) -> str:
    """Keep document instructions outside the source data eligible for compaction."""
    return instructions.rstrip() + DOCUMENT_SOURCES_TAG + json.dumps(
        {'data': data, 'read_with': read_with}, ensure_ascii=False, default=str)


def skill_catalog(db, assistant_id, project_dir=''):
    """Keep skill discovery data separate from mandatory execution instructions."""
    import capabilities
    items = [{'skill_id': item['id'], 'description': item['description'],
              'read_with': {'tool': 'skill_read', 'skill_id': item['id']}}
             for item in capabilities.list_skills(project_dir)
             if capabilities.enabled(db, assistant_id, 'skill', item['id'])]
    if not items:
        return ''
    return (SKILL_CATALOG_PREFIX + json.dumps({'enabled_skills': items}, ensure_ascii=False) +
            '\nUse skill_read with the exact skill_id before applying a skill. '
            'Disabled skills are unavailable. Project skills override user skills with the same name. '
            'Descriptions may be excerpted; skill_read returns the complete instructions.')


def _clip(value, text_limit, item_limit=16):
    """Shorten descriptive text while retaining source and contract structure."""
    if isinstance(value, str):
        if text_limit is None or len(value) <= text_limit:
            return value
        head = text_limit * 3 // 4
        tail = text_limit - head
        return value[:head] + '\n[已节选]\n' + (value[-tail:] if tail else '')
    if isinstance(value, list):
        return [_clip(item, text_limit, item_limit) for item in value]
    if isinstance(value, dict):
        return {key: deepcopy(item) if key in _IDENTITY_FIELDS or key.endswith(('_id', '_hash'))
                else _clip(item, text_limit, item_limit) for key, item in value.items()}
    return value


def _tool_value(value, text_limit, args=None):
    if isinstance(value, dict):
        if (text_limit is not None and isinstance(value.get('query'), str)
                and isinstance(value.get('saved'), list) and isinstance(value.get('matches'), list)
                and isinstance(value.get('results'), list) and 'found' in value):
            # A search receipt may also carry the project's complete historical
            # result catalogue. Only matches belong to this query; the raw
            # catalogue remains addressable in the unchanged checkpoint.
            def result_row(row):
                if not isinstance(row, dict):
                    return deepcopy(row)
                return {key: _clip(item, text_limit) if key == 'abstract' else deepcopy(item)
                        for key, item in row.items()}

            view = deepcopy(value)
            view['saved'] = [result_row(row) for row in value['saved']]
            view['matches'] = [result_row(row) for row in value['matches']]
            view['results'] = deepcopy(view['matches'])
            view['results_scope'] = 'current_query_matches'
            if value['results'] != value['matches']:
                view['archived_project_results'] = {
                    'count': len(value['results']),
                    'location': {'tool_result': 'paper_search', 'query': value['query'], 'field': 'results'},
                    'read_with': {'tool': 'workflow_context_read', 'section': 'agent'},
                    'meaning': 'Project-wide historical results are omitted from this model view, not from the checkpoint. They are not additional matches for the current query.',
                }
            return view
        if isinstance(value.get('content'), str) and 'total' in value and 'next_offset' in value:
            if value.get('repeat'):
                return deepcopy(value)
            page = deepcopy(value)
            original = page['content']
            offset = page.get('offset', (args or {}).get('offset'))
            if offset is None:
                end = page.get('next_offset')
                offset = (end if end is not None else page['total']) - len(original)
            offset = max(0, int(offset))
            visible = original if text_limit is None else original[:max(1, text_limit)]
            page.update(content=visible, offset=offset, end_offset=offset + len(visible),
                        next_offset=offset + len(visible) if offset + len(visible) < page['total'] else None,
                        truncated=bool(page.get('truncated')) or len(visible) < len(original))
            if len(visible) < len(original):
                page['locally_excerpted'] = True
            if page.get('text') == original:
                if page.get('section') == 'attachment':
                    page.pop('text')
                else:
                    page['text'] = visible
            return page
        return {key: deepcopy(item) if key in _IDENTITY_FIELDS or key == 'read_progress' or key.endswith(('_id', '_hash'))
                else _tool_value(item, text_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_tool_value(item, text_limit) for item in value]
    if isinstance(value, str):
        return value if text_limit is None else value[:text_limit]
    return value


def serialize_tool_result(result, args=None, budget=None):
    """Serialize a bounded tool result without cutting its JSON or page cursor."""
    def render(limit):
        return json.dumps(_tool_value(result, limit, args), ensure_ascii=False, default=str)

    full = render(None)
    if budget is None or len(full) <= budget:
        return full
    best = render(1)
    if len(best) > budget:
        raise ValueError('Required tool result metadata exceeds the tool output budget.')
    low, high = 1, len(full)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = render(midpoint)
        if len(candidate) <= budget:
            low, best = midpoint, candidate
        else:
            high = midpoint - 1
    return best


def _tool_content(content, text_limit, args):
    if not isinstance(content, str):
        return _tool_value(content, text_limit, args)
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return _clip(content, text_limit)
    return json.dumps(_tool_value(value, text_limit, args), ensure_ascii=False, default=str)


def _source_locators(value):
    """Remove equivalent file-address fields from a copied source view."""
    if isinstance(value, list):
        return [_source_locators(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    view = {key: deepcopy(item) if key in ('output_contract', 'required_files', 'file_rules', 'acceptance')
            else _source_locators(item) for key, item in value.items()}
    relative, path = view.get('relative'), view.get('path')
    version = view.get('content_hash') or view.get('sha256')
    if version and isinstance(relative, str) and relative and isinstance(path, str):
        normalized_path = path.replace('\\', '/')
        normalized_relative = relative.replace('\\', '/').removeprefix('./')
        if normalized_path == normalized_relative or normalized_path.endswith('/' + normalized_relative):
            view.pop('path')
    if view.get('sha256') and view.get('sha256') == view.get('content_hash'):
        view.pop('sha256')
    if version and isinstance(relative, str) and view.get('name') == relative.replace('\\', '/').rsplit('/', 1)[-1]:
        view.pop('name')
    return view


def _node_source_view(value):
    """Keep one copy of versioned source metadata in the model-facing input."""
    view = deepcopy(value)
    if not isinstance(view, dict):
        return view
    task_inputs = view.get('task_inputs')
    if isinstance(task_inputs, dict):
        if task_inputs.get('goal') in (view.get('task_goal'), view.get('goal')):
            task_inputs.pop('goal', None)
        if task_inputs.get('handoffs') == view.get('handoffs'):
            task_inputs.pop('handoffs', None)
        history = {(item.get('task_id'), item.get('delivery_id')): item
                   for item in view.get('history') or [] if isinstance(item, dict)}
        for dependency in task_inputs.get('dependencies') or []:
            if not isinstance(dependency, dict):
                continue
            source = history.get((dependency.get('task_id'), dependency.get('delivery_id')))
            if source and 'outputs' in source and 'outputs' in dependency and dependency['outputs'] == source['outputs']:
                dependency.pop('outputs', None)
                dependency['outputs_ref'] = 'history: same task_id and delivery_id'
    for upstream in view.get('accepted_upstream') or []:
        if not isinstance(upstream, dict):
            continue
        seen = {}
        for output in upstream.get('outputs') or []:
            if not isinstance(output, dict):
                continue
            content = output.get('content')
            digest = str(output.get('content_hash') or output.get('sha256') or '')
            key = (digest, output.get('total_chars', len(content) if isinstance(content, str) else None))
            if not digest or not isinstance(content, str):
                continue
            previous = seen.get(key)
            if previous is None:
                seen[key] = output.get('relative') or output.get('name') or output.get('read_with')
                continue
            output.pop('content')
            output['content_ref'] = previous
    manifest = view.get('upstream_manifest')
    if manifest and manifest == _upstream_manifest(view):
        view.pop('upstream_manifest')
        view['upstream_manifest_ref'] = 'accepted_upstream.outputs'
    return _source_locators(view)


def _source_system(message, text_limit, run_id, node_id):
    """Compact tagged source data, keeping separately supplied instructions whole."""
    content = message.get('content')
    if text_limit is None or not isinstance(content, str):
        return deepcopy(message)
    instructions, embedded_tag, payload = content.rpartition(DOCUMENT_SOURCES_TAG)
    if embedded_tag:
        try:
            value, end = json.JSONDecoder().raw_decode(payload)
        except ValueError:
            return deepcopy(message)
        if not isinstance(value, dict) or not isinstance(value.get('data'), dict):
            return deepcopy(message)
        view = {**deepcopy(value), 'data': _clip(value['data'], text_limit), 'source_excerpt': True,
                'full_context': {'run_id': run_id, 'node_id': node_id,
                                 'tool': 'workflow_context_read', 'section': 'agent'}}
        return {**deepcopy(message), 'content': instructions + embedded_tag +
                json.dumps(view, ensure_ascii=False, default=str) + payload[end:]}
    prefix = next((tag for tag in ('CURRENT_NODE_INPUT ', '【当前节点输入与前置交付】\n', SKILL_CATALOG_PREFIX)
                   if content.startswith(tag)), None)
    if prefix is None:
        return deepcopy(message)
    payload = content[len(prefix):]
    leading = ''
    if payload.startswith('【人工退回意见，须落实】\n'):
        boundary = payload.find('\n\n{')
        if boundary < 0:
            return deepcopy(message)
        leading, payload = payload[:boundary + 2], payload[boundary + 2:]
    identity = {'run_id': run_id, 'node_id': node_id,
                'tool': 'workflow_context_read',
                'section': 'agent' if prefix == SKILL_CATALOG_PREFIX else 'input'}
    try:
        value, end = json.JSONDecoder().raw_decode(payload)
    except ValueError:
        if prefix != 'CURRENT_NODE_INPUT ':
            return deepcopy(message)
        view = {'excerpt': _clip(payload, text_limit), 'original_chars': len(payload),
                'omitted': len(payload) > text_limit, 'full_context': identity}
        suffix = ''
    else:
        if prefix == SKILL_CATALOG_PREFIX:
            view = deepcopy(value)
            excerpted = False
            for item in view.get('enabled_skills', []):
                if item.get('read_with') == {'tool': 'skill_read', 'skill_id': item.get('skill_id')}:
                    item.pop('read_with')
                description = item.get('description', '')
                if len(description) > text_limit:
                    item['description'] = description[:text_limit] + '...'
                    excerpted = True
            view['read_tool'] = 'skill_read'
            view['read_argument'] = 'skill_id'
            view['descriptions_excerpted'] = excerpted
        else:
            view = _clip(_node_source_view(value), text_limit)
        if not isinstance(view, dict):
            view = {'data': view}
        view.setdefault('full_context', identity)
        view['source_excerpt'] = True
        suffix = payload[end:]
    return {**deepcopy(message), 'content': prefix + leading + json.dumps(view, ensure_ascii=False, default=str) + suffix}


def _upstream_manifest(snapshot):
    manifest = []
    for upstream in snapshot.get('accepted_upstream') or []:
        node_id = upstream.get('node_id') or upstream.get('task_id') or ''
        for out in upstream.get('outputs') or []:
            manifest.append({
                'node_id': node_id,
                'artifact': out.get('relative') or out.get('name') or '',
                'bytes': out.get('size') or out.get('bytes'),
                'total_chars': out.get('total_chars'),
                'asset_id': out.get('asset_id', ''),
                'content_hash': out.get('content_hash') or out.get('sha256') or '',
                'delivery_id': upstream.get('delivery_id') or out.get('delivery_id') or '',
                'read_with': out.get('read_with') or {'section': 'artifact', 'node_id': node_id, 'artifact': out.get('relative') or out.get('name')},
            })
    return manifest


def prompt(snapshot, run_id, node_id, budget=None):
    identity = {'run_id': run_id, 'node_id': node_id, 'tool': 'workflow_context_read'}
    manifest = _upstream_manifest(snapshot)
    priority = ('goal', 'task_goal', 'output_contract', 'handoffs', 'accepted_upstream', 'handoff_context', 'task_inputs', 'task_id')
    ordered = {key: snapshot[key] for key in priority if key in snapshot}
    ordered.update({key: value for key, value in snapshot.items() if key not in ordered})
    if ordered.get('task_goal') == ordered.get('goal'):
        ordered.pop('task_goal', None)
    full = {**ordered, 'full_context': identity}
    if manifest:
        full['upstream_manifest'] = manifest
    text = json.dumps(full, ensure_ascii=False, default=str)
    if budget is None or len(text) <= budget:
        return text
    for keep_history in (True, False):
        source = ordered if keep_history else {k: v for k, v in ordered.items() if k not in ('history', 'memory')}
        for limit in (4000, 2000, 1000, 400, 120, 0):
            view = _clip(source, limit)
            if manifest:
                view['upstream_manifest'] = manifest
            view['full_context'] = identity
            text = json.dumps(view, ensure_ascii=False, default=str)
            if len(text) <= budget:
                return text
    raise ValueError('Required workflow sources and output contracts exceed the node input budget.')


def source_messages(messages, budget, *, run_id='', node_id='', measure):
    """Budget optional initial sources while keeping every historical exchange whole."""
    if measure(messages) <= budget:
        return messages

    def view(limit):
        return [_source_system(message, limit, run_id, node_id)
                if message.get('role') in ('system', 'developer') else deepcopy(message)
                for message in messages]

    best = view(1)
    if measure(best) >= budget:
        return best
    low, high = 1, max((len(str(message.get('content') or '')) for message in messages
                       if message.get('role') in ('system', 'developer')), default=1)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = view(midpoint)
        if measure(candidate) <= budget:
            low, best = midpoint, candidate
        else:
            high = midpoint - 1
    return best


def bounded_messages(messages, budget=60000, *, run_id="", node_id="", measure=None, minimum=False,
                     recent_only=False):
    """Fit complete exchanges and structured source pages into a request budget.

    ``measure`` counts the complete candidate request in the caller's units.
    Raw checkpoints remain unchanged; immutable constraints and tool arguments
    are never replaced with excerpts.
    """
    measure = measure or (lambda value: len(json.dumps(value, ensure_ascii=False, default=str)))
    if not minimum and not recent_only and measure(messages) <= budget:
        return messages

    def groups(source):
        grouped = []
        index = 0
        while index < len(source):
            message = source[index]
            if message.get('role') in ('system', 'developer'):
                index += 1
                continue
            group = [message]
            index += 1
            # Keep assistant tool calls paired with their following tool results.
            if message.get('role') == 'assistant' and message.get('tool_calls'):
                while index < len(source) and source[index].get('role') == 'tool':
                    group.append(source[index])
                    index += 1
            grouped.append(group)
        return grouped

    systems = [deepcopy(message) for message in messages if message.get('role') in ('system', 'developer')]
    all_groups = groups(messages)
    note = {'role': 'system', 'content': (
        'Earlier workflow messages were compacted for this model request. '
        'The complete input, history, tool calls, and outputs remain in the checkpoint. '
        'Use workflow_context_read with section=input, history, agent, or output when evidence is needed. '
        + json.dumps({'run_id': run_id, 'node_id': node_id})
    )}

    def compact(limit, selected, source_limit=1):
        result = [*[_source_system(message, source_limit, run_id, node_id) for message in systems], deepcopy(note)]
        for index in sorted(selected):
            group = all_groups[index]
            copied = deepcopy(group)
            arguments = {}
            for call in group[0].get('tool_calls') or []:
                try:
                    args = json.loads((call.get('function') or {}).get('arguments') or '{}')
                except (ValueError, TypeError):
                    args = {}
                arguments[call.get('id')] = args if isinstance(args, dict) else {}
            for message in copied:
                if message.get('role') == 'tool':
                    message['content'] = _tool_content(message.get('content') or '', limit,
                                                      arguments.get(message.get('tool_call_id'), {}))
                elif message.get('role') != 'user':
                    original_content = message.get('content') or ''
                    message['content'] = _clip(original_content, limit)
                    if message['content'] != original_content:
                        message.pop('_protocol', None)
            result.extend(copied)
        return result

    selected = {index for index, group in enumerate(all_groups) if group[0].get('role') == 'user'}
    latest_exchange = next((index for index in range(len(all_groups) - 1, -1, -1)
                            if all_groups[index][0].get('tool_calls')), len(all_groups) - 1)
    selected.update(range(max(0, latest_exchange), len(all_groups)))
    if minimum:
        smallest = compact(1, selected)
        if measure(smallest) > budget:
            raise ValueError('Required system instructions, user goals, tool arguments and source metadata exceed the model input budget.')
        return smallest
    limit = None
    best = compact(limit, selected)
    if measure(best) > budget:
        best = compact(1, selected)
        if measure(best) > budget:
            raise ValueError('Required system instructions, user goals, tool arguments and source metadata exceed the model input budget.')
        low, high = 1, max(len(str(message.get('content') or '')) for message in messages)
        while low < high:
            midpoint = (low + high + 1) // 2
            candidate = compact(midpoint, selected)
            if measure(candidate) <= budget:
                low, best = midpoint, candidate
            else:
                high = midpoint - 1
        limit = low
    if recent_only:
        return best
    for index in range(len(all_groups) - 1, -1, -1):
        if index in selected:
            continue
        candidate = compact(limit, selected | {index})
        if measure(candidate) > budget:
            break
        selected.add(index)
        best = candidate
    max_source = max((len(str(message.get('content') or '')) for message in systems), default=1)
    if max_source > 1 and measure(compact(limit, selected, 2)) <= budget:
        low, high = 1, max_source
        while low < high:
            midpoint = (low + high + 1) // 2
            candidate = compact(limit, selected, midpoint)
            if measure(candidate) <= budget:
                low, best = midpoint, candidate
            else:
                high = midpoint - 1
    return best


def validate_cached_read(db, project_id, args, cached):
    """Recheck delivery ownership and file version before serving a cached page."""
    fresh = read(db, project_id, {**args, 'offset': 0, 'limit': 100})
    return all(fresh.get(key) == cached.get(key) for key in
               ('run_id', 'node_id', 'artifact', 'content_hash', 'delivery_id', 'total'))


def _read_frozen_input_asset(db, project_id, input_snapshot, requested):
    """Read one text input from the current task attempt's frozen asset copies."""
    from hashlib import sha256
    from project_artifacts import TEXT_SUFFIXES

    root = Path(db.base_dir(project_id)).resolve()
    requested = str(requested or '').replace('\\', '/').strip()
    if not requested or requested in ('.', '..') or any(part == '..' for part in requested.split('/')):
        raise ValueError('输入附件名必须是项目内相对路径或唯一文件名')
    requested_path = Path(requested)
    if requested_path.is_absolute():
        resolved_request = requested_path.resolve()
        if not resolved_request.is_relative_to(root):
            raise PermissionError('冻结输入附件路径超出当前项目目录')
        requested = resolved_request.relative_to(root).as_posix()
    else:
        requested = requested.removeprefix('./').strip('/')

    matches = []
    for item in (input_snapshot or {}).get('assets') or []:
        if not isinstance(item, dict) or not item.get('path'):
            continue
        frozen = Path(item['path']).resolve()
        if not frozen.is_relative_to(root):
            continue
        aliases = set()
        basenames = {frozen.name.casefold()}
        for candidate in (item.get('relative'), item.get('path')):
            if not candidate:
                continue
            candidate_path = Path(str(candidate)).resolve() if Path(str(candidate)).is_absolute() else (root / str(candidate)).resolve()
            if candidate_path.is_relative_to(root):
                aliases.add(candidate_path.relative_to(root).as_posix().casefold())
        original = item.get('original_path')
        if original:
            original_value = str(original)
            original_path = Path(original_value).resolve()
            basenames.add(original_path.name.casefold())
            if original_path.is_relative_to(root):
                aliases.add(original_path.relative_to(root).as_posix().casefold())
        if item.get('name'):
            basenames.add(Path(str(item['name'])).name.casefold())
        wanted = requested.casefold()
        if '/' in requested:
            matched = wanted in aliases
        else:
            matched = wanted in basenames or any(Path(alias).name.casefold() == wanted for alias in aliases)
        if matched:
            matches.append((item, frozen))

    if len(matches) != 1:
        if matches:
            raise ValueError('输入附件文件名不唯一，请使用项目内相对路径')
        raise ValueError('本次冻结输入中没有匹配的附件；请使用 input_snapshot 中的相对路径或唯一文件名')

    item, path = matches[0]
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError('只能读取本次冻结输入中的文本附件')
    if not path.is_file():
        raise ValueError('本次冻结输入附件不存在')
    expected = str(item.get('content_hash') or item.get('sha256') or '').strip().lower()
    if not expected:
        raise ValueError('冻结输入附件缺少版本哈希，拒绝读取未校验版本')
    raw = path.read_bytes()
    actual = sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError('本次冻结输入附件版本哈希不匹配')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('冻结输入附件不是有效的 UTF-8 文本') from exc
    relative = str(item.get('relative') or path.relative_to(root).as_posix()).replace('\\', '/')
    return {'text': text, 'name': Path(relative).name, 'relative': relative,
            'path': str(path), 'content_hash': actual, 'asset_id': str(item.get('asset_id') or '')}


def read(db, project_id, args):
    import workflow_execution
    run_id = str(args.get('run_id') or getattr(db, 'workflow_context_run_id', '') or '')
    if not run_id:
        context = getattr(db, 'project_task_context', None)
        if context and getattr(context, 'workflow_run_id', ''):
            run_id = str(context.workflow_run_id)
        elif context and getattr(context, 'task_id', ''):
            row = db.db.execute("SELECT workflow_run_id FROM project_tasks WHERE id=?", (context.task_id,)).fetchone()
            if row and row['workflow_run_id']:
                run_id = str(row['workflow_run_id'])
    if not run_id:
        latest_exec = db.db.execute(
            "SELECT e.run_id FROM workflow_executions e JOIN workflow_runs r ON r.id=e.run_id WHERE r.project_id=? ORDER BY r.rowid DESC LIMIT 1", (project_id,)).fetchone()
        if latest_exec:
            run_id = str(latest_exec['run_id'])
    if not run_id:
        raise ValueError('当前项目尚未启动工作流执行')
    # Models sometimes use the canonical task/attempt ID in this field.
    alias = db.db.execute('SELECT workflow_run_id,workflow_node_id,project_id FROM project_tasks WHERE id=?', (run_id,)).fetchone()
    if not alias:
        alias = db.db.execute('SELECT t.workflow_run_id,t.workflow_node_id,t.project_id FROM project_task_runs r JOIN project_tasks t ON t.id=r.task_id WHERE r.id=?', (run_id,)).fetchone()
    alias_node = ''
    if alias:
        if alias['project_id'] != project_id:
            raise ValueError('只能读取当前项目的工作流上下文')
        run_id, alias_node = alias['workflow_run_id'], alias['workflow_node_id']
    run = workflow_execution.detail(db, run_id)
    if run['project_id'] != project_id:
        raise ValueError('只能读取当前项目的工作流上下文')
    node_id = str(args.get('node_id') or getattr(db, 'workflow_context_node_id', '') or alias_node or run.get('current_node') or (run['nodes'][0]['id'] if run.get('nodes') else ''))
    mapping = run['settings'].get('task_ids', {})
    task_alias = db.db.execute('SELECT workflow_run_id,workflow_node_id,project_id FROM project_tasks WHERE id=?',
                               (node_id,)).fetchone()
    if task_alias:
        if task_alias['project_id'] != project_id or task_alias['workflow_run_id'] != run_id:
            raise ValueError('节点任务不属于当前项目和工作流运行')
        node_id = task_alias['workflow_node_id']
    logical_ids = [n['id'] for n in run['layout']['nodes'] if node_id in (n['id'], n.get('step'))]
    if len(logical_ids) == 1 and mapping.get(logical_ids[0]):
        origin = db.db.execute("SELECT o.legacy_id FROM project_task_origins o JOIN project_tasks t ON t.id=o.task_id WHERE o.kind='workflow_node' AND t.id=? AND o.task_run_id=t.latest_run_id", (mapping[logical_ids[0]],)).fetchone()
        if origin:
            node_id = origin['legacy_id']
    matching = [node for node in run['nodes'] if node_id in (node.get('id'), node.get('stage'))]
    if len(matching) == 1:
        node_id = matching[0]['id']
    node = next((n for n in run['nodes'] if n['id'] == node_id), None)
    if not node:
        raise ValueError('节点不存在')
    section = args.get('section', 'input')
    if section not in ('input', 'output', 'agent', 'history', 'artifacts', 'artifact'):
        raise ValueError('不支持的上下文部分')
    canonical = db.db.execute("SELECT o.task_id,o.task_run_id,t.status,t.latest_run_id,t.accepted_delivery_id "
        "FROM project_task_origins o JOIN project_tasks t ON t.id=o.task_id WHERE o.kind='workflow_node' AND o.legacy_id=?", (node_id,)).fetchone()
    context = getattr(db, 'project_task_context', None)
    own = bool(canonical and getattr(context, 'parent_task_id', '') == canonical['task_id'] and
               canonical['task_run_id'] == canonical['latest_run_id'])
    if not own:
        own = (getattr(db, 'workflow_context_run_id', '') == run_id and
               getattr(db, 'workflow_context_node_id', '') == node_id)
    if section == 'artifacts' and not own:
        raise PermissionError('只能读取当前执行节点的工作产物')
    if section in ('output', 'agent', 'artifact') and not own:
        accepted = bool(canonical and canonical['status'] == 'delivered' and canonical['task_run_id'] == canonical['latest_run_id'] and
            db.db.execute("SELECT 1 FROM project_task_deliveries WHERE id=? AND task_run_id=? AND decision='approved'",
                (canonical['accepted_delivery_id'], canonical['task_run_id'])).fetchone())
        if not accepted:
            raise ValueError('其他节点的产物须交付后才能作为当前节点上下文')
    checkpoint = node['checkpoint']
    saved = None
    if canonical:
        import project_tasks
        saved = project_tasks.get_run(db, canonical['task_run_id'])
        checkpoint = {**checkpoint, **saved['checkpoint']}
        if section == 'agent':
            checkpoint = {**checkpoint, 'agent': {key: saved['checkpoint'].get(key) for key in
                          ('messages', 'events', 'turn', 'done_tools', 'initial_context_archive')}}
    input_asset = None
    if section == 'input' and str(args.get('artifact') or '').strip():
        if not own or (canonical and canonical['task_run_id'] != canonical['latest_run_id']):
            raise PermissionError('只能读取当前执行节点本次冻结的输入附件')
        if saved is None:
            raise ValueError('当前节点没有可校验的冻结输入快照')
        input_asset = _read_frozen_input_asset(
            db, project_id, saved.get('input_snapshot') or {}, args.get('artifact'))
    value = checkpoint.get(section, {}) if section != 'history' else checkpoint.get('input', {}).get('history', [])
    if section == 'artifacts':
        import project_artifacts
        value = project_artifacts.current(db, project_id)
        for item in value:
            item.setdefault('relative', item.get('path', ''))
            if item.get('exists') and not item.get('asset_id'):
                path = Path(db.base_dir(project_id)) / item['relative']
                row = db.db.execute('SELECT id FROM assets WHERE canonical_uri=?', (str(path.resolve()),)).fetchone()
                if row:
                    item['asset_id'] = row['id']
            item.setdefault('asset_id', '')
    if section == 'output' and canonical and not own:
        import project_tasks
        saved = project_tasks.get_run(db, canonical['task_run_id'])
        value = saved['checkpoint'].get('output', saved['output_snapshot'])
    artifact = None
    if section == 'artifact':
        if not canonical:
            raise ValueError('节点没有可读取的交付文件')
        import project_tasks
        saved = project_tasks.get_run(db, canonical['task_run_id'])
        requested = str(args.get('artifact') or '')
        matches = [f for f in saved.get('output_snapshot', {}).get('files', [])
                   if requested and requested in (f.get('name'), f.get('relative'))]
        if len(matches) != 1 and own:
            # A resumed attempt has no output snapshot yet, so the delivery list of
            # this attempt is empty while its declared files already exist on disk.
            # Match those declared paths instead of deadlocking the node.
            declared = db.db.execute("SELECT output_contract FROM project_tasks WHERE id=?",
                                     (getattr(context, 'parent_task_id', ''),)).fetchone()
            contract = json.loads(declared['output_contract'] or '{}') if declared else {}
            root = Path(db.base_dir(project_id)).resolve()
            wanted = requested.replace('\\\\', '/').strip('/').casefold()
            for item in contract.get('required_files') or []:
                relative = str(item.get('relative') or item.get('path') if isinstance(item, dict) else item).replace('\\\\', '/')
                if wanted not in (relative.casefold(), relative.rsplit('/', 1)[-1].casefold()):
                    continue
                candidate = (root / relative).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    continue
                import hashlib
                raw = candidate.read_bytes()
                matches = [{'name': candidate.name, 'relative': relative, 'path': str(candidate),
                            'content_hash': hashlib.sha256(raw).hexdigest()}]
                break
        if len(matches) != 1:
            raise ValueError('请用交付清单中的唯一文件名或相对路径读取产物')
        artifact = matches[0]
        root, path = Path(db.base_dir(project_id)).resolve(), Path(artifact['path']).resolve()
        from project_artifacts import TEXT_SUFFIXES
        if not path.is_relative_to(root) or path.suffix.lower() not in TEXT_SUFFIXES:
            raise PermissionError('只能读取当前项目内已交付的文本产物')
        if not path.is_file():
            raise ValueError('交付文件不存在')
        raw = path.read_bytes()
        import hashlib
        expected = artifact.get('content_hash') or artifact.get('sha256')
        if not expected or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('已交付文件版本发生变化，请重新核查来源节点')
        text = raw.decode('utf-8-sig')
    elif input_asset:
        text = input_asset['text']
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    offset = max(0, int(args.get('offset') or 0))
    limit = int(args['limit']) if args.get('limit') is not None else 8000
    if limit <= 0:
        raise ValueError('Workflow context page size must be positive.')
    offset = min(offset, len(text))
    content = text[offset:offset + limit]
    end_offset = offset + len(content)
    from hashlib import sha256
    return {'run_id': run_id, 'node_id': node_id, 'section': section, 'content': content,
            'offset': offset, 'end_offset': end_offset, 'total': len(text),
            'content_hash': expected if artifact else (input_asset['content_hash'] if input_asset
                                                       else sha256(text.encode('utf-8')).hexdigest()),
            'next_offset': end_offset if end_offset < len(text) else None, 'truncated': False,
            **({'files': [{key: item[key] for key in ('path', 'relative', 'asset_id', 'content_hash', 'exists')
                          if key in item} for item in value]} if section == 'artifacts' else {}),
            **({'artifact': artifact['name'], 'content_hash': expected,
                'asset_id': artifact.get('asset_id', ''), 'relative': artifact.get('relative', ''),
                'delivery_id': canonical['accepted_delivery_id']} if artifact else {}),
            **({'artifact': input_asset['name'], 'content_hash': input_asset['content_hash'],
                'asset_id': input_asset['asset_id'], 'relative': input_asset['relative']}
               if input_asset else {})}
