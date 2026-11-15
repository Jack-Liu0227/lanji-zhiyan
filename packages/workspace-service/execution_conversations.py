"""Durable one-to-one execution conversations, projected from canonical runs."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

import workspace_trajectory


def now():
    return datetime.now(timezone.utc).isoformat()


def _message_key(value):
    return ' '.join(str(value or '').split())


_INTERNAL_INPUT_MARKERS = (
    '【当前项目明确引用的材料】',
    '【@上下文】',
    '【用户明确使用的提示词模板：',
    '【用户选择的材料】',
    '【@提及的上下文】',
)


def _is_internal_input_variant(value, canonical):
    """Return whether ``value`` is the same visible prompt plus runtime context.

    The worker receives a prompt enriched with references and @ mentions. Older
    clients stored that enriched request in the chat table, while the execution
    conversation also renders the canonical task goal. Only the known internal
    suffixes are treated as duplicates so a later user follow-up remains a
    separate turn.
    """
    value_key, canonical_key = _message_key(value), _message_key(canonical)
    if not value_key or not canonical_key or value_key == canonical_key:
        return value_key == canonical_key and bool(canonical_key)
    if not value_key.startswith(canonical_key):
        return False
    suffix = value_key[len(canonical_key):].lstrip(' :\n')
    return any(suffix.startswith(marker.replace('\n', ' ')) for marker in _INTERNAL_INPUT_MARKERS)


def visible_messages(messages, *, canonical='', hidden_inputs=()):
    """Project durable messages into the conversation view without duplicate inputs.

    This is deliberately read-only. Existing rows remain available to exports
    and audit tools; only the UI/API projection collapses repeated user turns.
    """
    result=[]
    seen=set()
    canonical_key=_message_key(canonical)
    hidden_keys=[_message_key(value) for value in hidden_inputs or () if _message_key(value)]
    for message in messages or []:
        item=dict(message)
        if item.get('role') != 'user':
            result.append(item)
            continue
        key=_message_key(item.get('content'))
        if not key:
            result.append(item)
            continue
        if key in seen or (canonical_key and _is_internal_input_variant(key, canonical_key)) or any(
                _is_internal_input_variant(key, candidate) for candidate in hidden_keys):
            continue
        seen.add(key)
        result.append(item)
    return result


def link(db, kind, entity_id):
    row=db.db.execute('SELECT * FROM execution_conversations WHERE kind=? AND entity_id=?',(kind,entity_id)).fetchone()
    return dict(row) if row else None


def ensure(db, kind, entity_id):
    saved=link(db,kind,entity_id)
    if saved:return saved['chat_id']
    if kind=='task':
        row=db.db.execute('SELECT project_id,title,created_at,updated_at FROM project_tasks WHERE id=?',(entity_id,)).fetchone()
    elif kind=='workflow':
        row=db.db.execute("SELECT r.project_id,COALESCE(json_extract(e.layout_snapshot,'$.name'),'工作流') AS title,r.started_at AS created_at,e.updated_at FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id WHERE r.id=?",(entity_id,)).fetchone()
    else:raise ValueError('执行类型无效')
    if not row:raise ValueError('执行记录不存在')
    cid='execution-'+entity_id
    db.db.execute('INSERT OR IGNORE INTO chats(id,title,task_id,created_at,updated_at) VALUES (?,?,?,?,?)',(cid,row['title'],row['project_id'],row['created_at'],row['updated_at']))
    db.db.execute('INSERT OR IGNORE INTO execution_conversations VALUES (?,?,?,?)',(cid,row['project_id'],kind,entity_id))
    return cid


def by_chat(db, chat_id):
    row=db.db.execute('SELECT * FROM execution_conversations WHERE chat_id=?',(chat_id,)).fetchone()
    return dict(row) if row else None


def actor(db, snapshot):
    expert=snapshot.get('assistant') or db.assistant(snapshot.get('assistant_id','')) or {}
    member=snapshot.get('member') or {}
    return {'assistant_id':snapshot.get('assistant_id') or expert.get('id',''),
            'sender':(snapshot.get('assistant_name') or expert.get('name') or member.get('name') or '执行专家').replace('助手','专家'),
            'team_name':(snapshot.get('team') or {}).get('name',''),'member_name':member.get('name','')}


def record(db, task, run, event):
    kind=event.get('type','')
    if kind not in ('tool_call','tool_result','model_request','model_response','team','sub','error','reasoning_done'):return
    payload={**workspace_trajectory.redact(event),**actor(db,run['executor_snapshot'])}
    for field in ('args','result'):
        if field in payload:payload[field]=workspace_trajectory.safe_result(payload[field])
    identity=event.get('id') or event.get('message_id')
    key=run['id']+':'+kind+':'+str(event.get('round',''))+':'+str(identity) if identity else uuid.uuid4().hex
    db.db.execute('INSERT OR IGNORE INTO execution_conversation_events(task_id,run_id,event_key,payload,created_at) VALUES (?,?,?,?,?)',
                  (task['id'],run['id'],key,json.dumps(payload,ensure_ascii=False),now()))
    db.db.commit()


def touch(db, kind, entity_id):
    cid=ensure(db,kind,entity_id)
    db.db.execute('UPDATE chats SET updated_at=? WHERE id=?',(now(),cid))


def busy(db, chat_id):
    binding=by_chat(db,chat_id)
    if not binding:return False
    if binding['kind']=='task':
        row=db.db.execute('SELECT status FROM project_tasks WHERE id=?',(binding['entity_id'],)).fetchone()
        return bool(row and row['status'] in ('queued','running'))
    row=db.db.execute('SELECT status FROM workflow_runs WHERE id=?',(binding['entity_id'],)).fetchone()
    return bool(row and row['status'] in ('queued','running'))


def _error_detail(value):
    """Extract a useful error string from legacy and projected event shapes."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ''
    for key in ('message', 'detail', 'error', 'summary'):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    for key in ('output', 'result', 'payload'):
        detail = _error_detail(value.get(key))
        if detail:
            return detail
    return ''


def _prefer_error(*values):
    fallback = ''
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        if not fallback:
            fallback = text
        if text.casefold() not in {'error', 'failed', 'execution failed'}:
            return text
    return fallback


def _event_error_detail(event):
    if not isinstance(event, dict):
        return ''
    if event.get('type') not in ('error', 'failed') and event.get('kind') not in ('error', 'failed'):
        return ''
    return _error_detail(event)


def _ui_event(event):
    if not isinstance(event, dict):
        return event
    value = dict(event)
    if not value.get('type') and value.get('kind'):
        value['type'] = value['kind']
    if value.get('type') in ('error', 'failed') and not value.get('message'):
        detail = _error_detail(value)
        if detail:
            value['message'] = detail
    return value


def _task_messages(db, task):
    import project_tasks
    messages=[]
    for row in db.db.execute('SELECT id FROM project_task_runs WHERE task_id=? ORDER BY attempt',(task['id'],)):
        run=project_tasks.get_run(db,row['id']);checkpoint=run['checkpoint'];who=actor(db,run['executor_snapshot'])
        events=[_ui_event(json.loads(row['payload'])) for row in db.db.execute('SELECT payload FROM execution_conversation_events WHERE run_id=? ORDER BY id',(run['id'],))]
        if not events:events=[_ui_event(item) for item in workspace_trajectory.redact(checkpoint.get('events') or [])]
        event_error=next((detail for item in reversed(events) if (detail := _event_error_detail(item))), '')
        output_error=_error_detail(run.get('output_snapshot', {}))
        error_text=_prefer_error(event_error, run.get('error'), _error_detail(checkpoint.get('error')), output_error)
        reply=checkpoint.get('reply') or run.get('output_snapshot',{}).get('reply') or ('执行失败：'+error_text if error_text else '')
        if error_text and str(reply).strip().casefold() in {'error', 'failed', 'execution failed'}:
            reply='Execution failed: '+error_text
        if checkpoint.get('output_revision'):
            current = run.get('output_snapshot', {}).get('content')
            reply = '当前候选产物 · 修订 v' + str(checkpoint['output_revision']) + '\n\n' + (current if isinstance(current, str) else json.dumps(current, ensure_ascii=False, indent=2))
        messages.append({'id':run['id'],'role':'assistant','content':reply,'events':events,
                         'created_at':run['started_at'],'status':run['status'],'attempt':run['attempt'],
                         'task_id':task['id'],'task_title':task['title'],**who})
    return messages


def read(db, chat_id, project_id=''):
    binding=by_chat(db,chat_id)
    if not binding:return None
    chat=db.db.execute('SELECT title FROM chats WHERE id=?',(chat_id,)).fetchone()
    if not chat:return None
    if project_id and binding['project_id']!=project_id:raise ValueError('对话不属于当前项目')
    import project_tasks
    if binding['kind']=='task':
        task=project_tasks.get(db,binding['entity_id']);tasks=[task]
        executor=(task.get('latest_run') or {}).get('executor_snapshot') or {}
        if not executor:
            import project_bindings
            try:executor=project_bindings.resolve(db,binding['project_id'],task['executor_binding_id'],member_id=task['member_id'],materialize=False)
            except ValueError:pass
        latest_error=_prefer_error((task.get('latest_run') or {}).get('error'), task['metadata'].get('_start_error',''))
        if not latest_error or latest_error.strip().casefold() in {'error','failed','执行失败'}:
            latest_events=db.db.execute('SELECT payload FROM execution_conversation_events WHERE run_id=? ORDER BY id DESC',(task.get('latest_run_id',''),)).fetchall()
            for row in latest_events:
                payload=json.loads(row['payload'] or '{}')
                detail=_event_error_detail(payload)
                if detail:
                    latest_error=detail;break
        summary={'title':task['title'],'goal':task['goal'],'status':task['status'],'workflow_run_id':task['workflow_run_id'],'outputs':task['outputs'],
                 'error':latest_error,
                  'created_at':task['created_at'],'executor':actor(db,executor),'executor_binding_id':task['executor_binding_id'],'member_id':task['member_id'],
                  'node_result': task['node_result']}
        children=[{'id':child['id'],'title':child['title'],'status':child['status'],'chat_id':(link(db,'task',child['id']) or {}).get('chat_id',''),
                   'node_result': project_tasks.get(db, child['id'])['node_result']} for child in task['children']]
    else:
        import workflow_execution
        run=workflow_execution.detail(db,binding['entity_id'])
        tasks=project_tasks.list_tasks(db,binding['project_id'],workflow_run_id=run['id'])
        order=list(run['settings'].get('task_ids',{}).values())
        tasks.sort(key=lambda task:order.index(task['id']) if task['id'] in order else len(order))
        summary={'title':run['layout'].get('name','工作流'),'goal':run['settings'].get('goal',''),
                  'status':run['status'],'error':run.get('error',''),'workflow_run_id':run['id'],'created_at':run['started_at'],'prepared':bool(run['settings'].get('prepared'))}
        children=[{'id':task['id'],'title':task['title'],'status':task['status'],'chat_id':task.get('chat_id',''), 'node_result': task['node_result']} for task in tasks]
        if not summary.get('error') or str(summary.get('error')).strip().casefold() in {'error','failed','执行失败'}:
            for child in reversed(tasks):
                candidate=(child.get('latest_run') or {}).get('error','')
                if candidate and str(candidate).strip().casefold() not in {'error','failed','执行失败'}:
                    summary['error']=candidate;break
                run_id=child.get('latest_run_id','')
                for row in db.db.execute('SELECT payload FROM execution_conversation_events WHERE run_id=? ORDER BY id DESC',(run_id,)).fetchall():
                    detail=_event_error_detail(json.loads(row['payload'] or '{}'))
                    if detail and detail.casefold() not in {'error','failed','执行失败'}:
                        summary['error']=detail;break
                if summary.get('error') and str(summary['error']).casefold() not in {'error','failed','执行失败'}:break
    messages=[{'id':chat_id+':goal','role':'user','content':summary['goal'] or summary['title'],'created_at':summary['created_at'],'events':[]}]
    for task in tasks:messages.extend(_task_messages(db,task))
    tail=visible_messages(db.messages(chat_id), canonical=messages[0].get('content'),
                          hidden_inputs=[item.get('goal', '') for item in tasks])
    # Execution chats already expose the canonical goal above. Older clients
    # persisted the same user turn into the chat table, sometimes more than
    # once; remove every canonical duplicate while preserving later questions.
    goal_key = _message_key(messages[0].get('content'))
    tail = [item for item in tail if not (
        item.get('role') == 'user' and _is_internal_input_variant(item.get('content'), goal_key))]
    messages.extend(tail)
    import execution_status
    summary.update(execution_status.project(db,chat_id,summary['status'],kind=binding['kind'],
                   run_id=task['latest_run_id'] if binding['kind']=='task' else run['id']))
    result={'execution':{**binding,**summary,'title':chat['title'],'children':children},'messages':messages}
    result['revision']=hashlib.sha256(json.dumps(result,ensure_ascii=False,default=str).encode()).hexdigest()
    return result


def route(method,path,body,query,db):
    if path!='/api/execution-conversations':return None
    def q(name):return str(body.get(name) or (query.get(name) or [''])[0])
    if method!='GET':raise ValueError('不支持的对话操作')
    saved=by_chat(db,q('chat_id')) if q('chat_id') else link(db,q('kind'),q('entity_id'))
    if not saved:raise ValueError('未找到关联执行对话')
    return read(db,saved['chat_id'],q('project_id'))
