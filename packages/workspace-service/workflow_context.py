"""Bound model input while keeping complete, addressable workflow checkpoints."""
from __future__ import annotations

from copy import deepcopy
import json


def _clip(value, text_limit, item_limit=16):
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        if value.startswith('CURRENT_NODE_INPUT '):
            head = text_limit * 3 // 4
            tail = text_limit - head
            return value[:head] + '\n[已节选，完整节点上下文可分页读取]\n' + value[-tail:]
        return value[:text_limit] + '\n[已节选，使用 workflow_context_read 读取完整节点上下文]'
    if isinstance(value, list):
        values = [_clip(v, text_limit, item_limit) for v in value[:item_limit]]
        if len(value) > item_limit:
            values.append({'remaining_items': len(value) - item_limit, 'read_tool': 'workflow_context_read'})
        return values
    if isinstance(value, dict):
        return {k: _clip(v, text_limit, item_limit) for k, v in value.items()}
    return value


def prompt(snapshot, run_id, node_id, budget=24000):
    identity = {'run_id': run_id, 'node_id': node_id, 'tool': 'workflow_context_read'}
    priority = ('goal', 'task_goal', 'accepted_upstream', 'handoff_context', 'task_inputs', 'task_id')
    ordered = {key: snapshot[key] for key in priority if key in snapshot}
    ordered.update({key: value for key, value in snapshot.items() if key not in ordered})
    for keep_history in (True, False):
        source = ordered if keep_history else {k: v for k, v in ordered.items() if k not in ('history', 'memory')}
        for limit in (4000, 2000, 1000, 400, 120):
            view = _clip(source, limit)
            view['full_context'] = identity
            text = json.dumps(view, ensure_ascii=False, default=str)
            if len(text) <= budget:
                return text
    return json.dumps({**_clip({k: ordered[k] for k in priority if k in ordered}, 1000, 4),
        'full_context': identity, 'notice': '完整输入可通过 workflow_context_read 分页读取。'}, ensure_ascii=False, default=str)


def bounded_messages(messages, budget=60000, *, run_id="", node_id=""):
    """Create a provider-safe view without failing a valid workflow node.

    Complete messages remain in the node checkpoint and can be paged with
    ``workflow_context_read``.  This function only compacts the copy sent to a
    model; a long history must never turn into a terminal node failure.
    """
    if len(json.dumps(messages, ensure_ascii=False, default=str)) <= budget:
        return messages

    def groups(source):
        grouped = []
        index = 0
        while index < len(source):
            message = source[index]
            if message.get('role') == 'system':
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

    systems = [deepcopy(message) for message in messages if message.get('role') == 'system']
    all_groups = groups(messages)
    note = {'role': 'system', 'content': (
        'Earlier workflow messages were compacted for this model request. '
        'The complete input, history, tool calls, and outputs remain in the checkpoint. '
        'Use workflow_context_read with section=input, history, agent, or output when evidence is needed. '
        + json.dumps({'run_id': run_id, 'node_id': node_id})
    )}

    def compact(limit, selected):
        result = []
        for item in systems:
            copied = deepcopy(item)
            content = copied.get('content') or ''
            pinned = isinstance(content, str) and content.startswith('【当前节点输入与前置交付】')
            copied['content'] = _clip(content, max(limit, budget // 2) if pinned else limit)
            for call in copied.get('tool_calls') or []:
                function = call.get('function') or {}
                arguments = function.get('arguments', '')
                if len(arguments) > limit:
                    function['arguments'] = json.dumps({
                        'recorded_arguments_excerpt': arguments[:limit],
                        'full_arguments': 'workflow_context_read section=agent'}, ensure_ascii=False)
            result.append(copied)
        result.append(deepcopy(note))
        for group in selected:
            copied = deepcopy(group)
            for message in copied:
                message['content'] = _clip(message.get('content') or '', limit)
                for call in message.get('tool_calls') or []:
                    function = call.get('function') or {}
                    arguments = function.get('arguments', '')
                    if len(arguments) > limit:
                        function['arguments'] = json.dumps({
                            'recorded_arguments_excerpt': arguments[:limit],
                            'full_arguments': 'workflow_context_read section=agent'}, ensure_ascii=False)
            result.extend(copied)
        return result

    # Retain the user's objective and then pack recent complete exchanges.
    latest_user = next((group for group in reversed(all_groups) if group[0].get('role') == 'user'), None)
    selected = [latest_user] if latest_user else []
    best = compact(120, selected)
    for group in reversed(all_groups):
        if group is latest_user:
            continue
        trial = ([latest_user] if latest_user else []) + [group] + [g for g in selected if g is not latest_user]
        candidate = None
        for limit in (6000, 3000, 1500, 500, 120):
            value = compact(limit, trial)
            if len(json.dumps(value, ensure_ascii=False, default=str)) <= budget:
                candidate = value
                break
        if candidate is None:
            break
        selected, best = trial, candidate
    for limit in (8000, 4000, 2000, 1000, 400, 120):
        candidate = compact(limit, selected)
        if len(json.dumps(candidate, ensure_ascii=False, default=str)) <= budget:
            return candidate
    return best


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
    if section not in ('input', 'output', 'agent', 'history'):
        raise ValueError('不支持的上下文部分')
    canonical = db.db.execute("SELECT o.task_id,o.task_run_id,t.status,t.latest_run_id,t.accepted_delivery_id "
        "FROM project_task_origins o JOIN project_tasks t ON t.id=o.task_id WHERE o.kind='workflow_node' AND o.legacy_id=?", (node_id,)).fetchone()
    context = getattr(db, 'project_task_context', None)
    own = bool(canonical and getattr(context, 'parent_task_id', '') == canonical['task_id'] and
               canonical['task_run_id'] == canonical['latest_run_id'])
    if not own:
        own = (getattr(db, 'workflow_context_run_id', '') == run_id and
               getattr(db, 'workflow_context_node_id', '') == node_id)
    if section in ('output', 'agent') and not own:
        accepted = bool(canonical and canonical['status'] == 'delivered' and canonical['task_run_id'] == canonical['latest_run_id'] and
            db.db.execute("SELECT 1 FROM project_task_deliveries WHERE id=? AND task_run_id=? AND decision='approved'",
                (canonical['accepted_delivery_id'], canonical['task_run_id'])).fetchone())
        if not accepted:
            raise ValueError('其他节点的产物须交付后才能作为当前节点上下文')
    checkpoint = node['checkpoint']
    if canonical:
        import project_tasks
        saved = project_tasks.get_run(db, canonical['task_run_id'])
        checkpoint = {**checkpoint, **saved['checkpoint']}
        if section == 'agent':
            checkpoint = {**checkpoint, 'agent': {key: saved['checkpoint'].get(key) for key in ('messages', 'events', 'turn', 'done_tools')}}
    value = checkpoint.get(section, {}) if section != 'history' else checkpoint.get('input', {}).get('history', [])
    if section == 'output' and canonical and not own:
        import project_tasks
        saved = project_tasks.get_run(db, canonical['task_run_id'])
        value = saved['checkpoint'].get('output', saved['output_snapshot'])
    text = json.dumps(value, ensure_ascii=False, default=str)
    offset, limit = max(0, int(args.get('offset') or 0)), max(100, min(16000, int(args.get('limit') or 8000)))
    return {'run_id': run_id, 'node_id': node_id, 'section': section, 'content': text[offset:offset+limit],
            'total': len(text), 'next_offset': offset + limit if offset + limit < len(text) else None}
