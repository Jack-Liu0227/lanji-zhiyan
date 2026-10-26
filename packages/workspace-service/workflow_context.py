"""Bound model input while keeping complete, addressable workflow checkpoints."""
from __future__ import annotations

from copy import deepcopy
import json


def _clip(value, text_limit, item_limit=16):
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
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
    for limit in (4000, 2000, 1000, 400, 120):
        view = _clip(snapshot, limit)
        view['full_context'] = {'run_id': run_id, 'node_id': node_id, 'tool': 'workflow_context_read'}
        text = json.dumps(view, ensure_ascii=False, default=str)
        if len(text) <= budget:
            return text
    return json.dumps({'goal': str(snapshot.get('goal', ''))[:4000], 'full_context': view['full_context'],
        'notice': '上下文超出本次输入预算，请先分页读取 input 和 history，承接证据后再执行。'}, ensure_ascii=False)


def bounded_messages(messages, budget=60000):
    if len(json.dumps(messages, ensure_ascii=False, default=str)) <= budget:
        return messages
    # Preserve roles, call IDs and response pairing. Full messages remain in agent checkpoints.
    for limit in (6000, 3000, 1500, 500, 120):
        result = deepcopy(messages)
        for message in result:
            message['content'] = _clip(message.get('content') or '', limit)
            for call in message.get('tool_calls') or []:
                function = call.get('function') or {}
                arguments = function.get('arguments', '')
                if len(arguments) > limit:
                    function['arguments'] = json.dumps({'recorded_arguments_excerpt': arguments[:limit],
                        'full_arguments': 'workflow_context_read section=agent'}, ensure_ascii=False)
        result.insert(0, {'role': 'system', 'content': '长消息已节选。执行前使用 workflow_context_read 分页读取当前节点 input、history 和需要的 agent 记录；完整历史仍保存在节点检查点，不得把节选视为全部证据。'})
        if len(json.dumps(result, ensure_ascii=False, default=str)) <= budget:
            return result
    raise ValueError('节点历史超过输入预算，请从已交付节点开始新的工作流运行')


def read(db, project_id, args):
    import workflow_execution
    run_id = str(args.get('run_id') or getattr(db, 'workflow_context_run_id', ''))
    run = workflow_execution.detail(db, run_id)
    if run['project_id'] != project_id:
        raise ValueError('只能读取当前项目的工作流上下文')
    node_id = str(args.get('node_id') or run['current_node'])
    node = next((n for n in run['nodes'] if n['id'] == node_id), None)
    if not node:
        raise ValueError('节点不存在')
    section = args.get('section', 'input')
    if section not in ('input', 'output', 'agent', 'history'):
        raise ValueError('不支持的上下文部分')
    value = node['checkpoint'].get(section, {}) if section != 'history' else node['checkpoint'].get('input', {}).get('history', [])
    text = json.dumps(value, ensure_ascii=False, default=str)
    offset, limit = max(0, int(args.get('offset') or 0)), max(100, min(16000, int(args.get('limit') or 8000)))
    return {'run_id': run_id, 'node_id': node_id, 'section': section, 'content': text[offset:offset+limit],
            'total': len(text), 'next_offset': offset + limit if offset + limit < len(text) else None}
