"""Read-only projections of durable conversation events, with bounded secret-safe results."""
import json
import re
import base64
from datetime import datetime, timezone

SENSITIVE={'api_key','apikey','api-key','password','passwd','secret','access_token','refresh_token','authorization','credential','client_secret','token'}
DELTAS=('assistant_delta','reasoning_delta')


def event_payload(event):
    value = redact(event)
    kind = value.get('type') or value.get('kind') or ''
    if kind == 'model_request':
        value = {key: item for key, item in value.items() if key in
                 ('type', 'kind', 'id', 'round', 'model', 'permission', 'created_at', 'agent_run_id', 'agent_node_id')}
    else:
        for key in ('request', 'request_body', 'request_payload', 'headers', 'messages', '_runtime_snapshot'):
            value.pop(key, None)
    if kind.startswith('reasoning_'):
        text = value.get('text') or value.get('content') or ''
        value.update(text=text, content=text)
    return value


def monitoring(run):
    checkpoint = run.get('checkpoint') or {}
    saved = checkpoint.get('monitoring') or {}
    heartbeat_at = saved.get('heartbeat_at') or ''
    alive = False
    if heartbeat_at and run['status'] == 'running' and saved.get('worker_alive'):
        try:
            heartbeat = datetime.fromisoformat(heartbeat_at.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
            alive = 0 <= age <= 15
        except (ValueError, TypeError):
            pass
    return {'heartbeat_at': heartbeat_at, 'last_progress_at': saved.get('last_progress_at') or '',
            'phase': saved.get('phase') or checkpoint.get('phase') or '',
            'phase_started_at': saved.get('phase_started_at') or saved.get('last_progress_at') or '',
            'worker_alive': alive}


def redact(value):
    if isinstance(value,dict):
        return {key:'[已隐藏]' if str(key).lower() in SENSITIVE else redact(item) for key,item in value.items()}
    if isinstance(value,list):return [redact(item) for item in value]
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,(dict,list)):return json.dumps(redact(parsed),ensure_ascii=False)
        except (ValueError,TypeError):pass
        value=re.sub(r'(?i)(bearer\s+)[\w.\-]+',r'\1[已隐藏]',value)
        value=re.sub(r'(?i)((?:api[_-]?key|password|access_token|client_secret)\s*[=:]\s*)[^\s,;]+',r'\1[已隐藏]',value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{16,}\b','[已隐藏]',value)
    return value


def safe_result(value):
    value=redact(value)
    text=json.dumps(value,ensure_ascii=False,default=str)
    return json.loads(text) if len(text)<=150000 else {'preview':text[:150000],'truncated':True,'total_characters':len(text)}


def owner(db,chat_id,project_id):
    row=db.db.execute('SELECT id,task_id FROM chats WHERE id=?',(chat_id,)).fetchone()
    if not row or (row['task_id'] or '')!=project_id:
        raise ValueError('此对话不属于当前项目')


def _item_sequence(sort_time, source, run_id):
    try:
        dt = datetime.fromisoformat(str(sort_time).replace('Z', '+00:00'))
        us = int((dt.timestamp() - 1767225600) * 1_000_000)
    except Exception:
        us = 0
    src_val = 5000 if source == 'execution' else 0
    clean = re.sub(r'[^a-zA-Z0-9]', '', str(run_id).lower())
    first = (ord(clean[0]) - ord('0')) if clean and clean[0].isdigit() else (ord(clean[0]) - ord('a') + 10) if clean else 0
    last = (ord(clean[-1]) - ord('0')) if clean and clean[-1].isdigit() else (ord(clean[-1]) - ord('a') + 10) if clean else 0
    id_val = (first * 36 + last) if clean else 0
    return us * 10000 + src_val + id_val


def listing(db,chat_id,project_id,*,before=0,limit=20):
    owner(db,chat_id,project_id)
    execution=_execution_listing(db,chat_id,project_id,before=before,limit=limit)
    if execution is not None:return execution
    limit=max(1,min(int(limit),50))
    rows=db.db.execute('SELECT rowid AS sequence,* FROM tasks WHERE owner_id=? AND (?=0 OR rowid<?) ORDER BY rowid DESC LIMIT ?',
                       (chat_id,int(before),int(before),limit+1)).fetchall()
    runs=[]
    for row in rows[:limit]:
        checkpoint=json.loads(row['checkpoint'] or '{}')
        request=checkpoint.get('request') or {}
        config=request.get('_runtime_snapshot') or {}
        model={}
        if config.get('model_id'):
            try:model=db.model(config['model_id']) or {}
            except (KeyError,ValueError):pass
        counts={item['kind']:item['amount'] for item in db.db.execute('SELECT kind,COUNT(*) AS amount FROM run_events WHERE run_id=? GROUP BY kind',(row['id'],))}
        runs.append({'id':row['id'],'sequence':row['sequence'],'status':row['status'],'created_at':row['created_at'],'updated_at':row['updated_at'],
                     'message':redact(request.get('message','')),'runtime':redact(config),'model_name':model.get('name') or model.get('model') or '',
                     'counts':counts})
    return {'runs':runs,'next_before':runs[-1]['sequence'] if len(rows)>limit else None,'chat_id':chat_id,'project_id':project_id}


def events(db,chat_id,project_id,run_id,*,after=0,limit=300):
    owner(db,chat_id,project_id)
    execution=_execution_events(db,chat_id,project_id,run_id,after=after,limit=limit)
    if execution is not None:return execution
    run=db.get_async_task(run_id)
    if not run or run['owner_id']!=chat_id:raise ValueError('此运行不属于当前对话')
    limit=max(1,min(int(limit),500))
    rows=db.db.execute("SELECT * FROM run_events WHERE run_id=? AND event_id>? AND kind NOT IN ('assistant_delta','reasoning_delta') ORDER BY event_id LIMIT ?",
                       (run_id,int(after),limit+1)).fetchall()
    records=[{'event_id':row['event_id'],'kind':row['kind'],'created_at':row['created_at'],'payload':event_payload({'kind':row['kind'], **json.loads(row['payload'])})} for row in rows[:limit]]
    return {'run_id':run_id,'status':run['status'],'events':records,'has_more':len(rows)>limit,
            'next_after':records[-1]['event_id'] if records else int(after)}


def _execution_binding(db,chat_id,project_id):
    """Return the durable task/workflow binding used by execution chats.

    Execution chats are projections of project task runs. They intentionally do
    not create rows in the legacy ``tasks``/``run_events`` tables, so the
    regular trajectory reader must use their event ledger instead.
    """
    import execution_conversations
    binding=execution_conversations.by_chat(db,chat_id)
    if not binding:return None
    if project_id and binding.get('project_id')!=project_id:raise ValueError('此对话不属于当前项目')
    return binding


def _execution_listing(db,chat_id,project_id,*,before=0,limit=20):
    binding=_execution_binding(db,chat_id,project_id)
    task_ids = _trajectory_task_ids(db, chat_id, project_id, binding)
    if not binding and not task_ids:return None
    limit=max(1,min(int(limit),50))
    before_seq = int(before or 0)
    rows = db.db.execute('''SELECT * FROM (
        SELECT r.id,r.started_at AS sort_time,'execution' AS source
        FROM project_task_runs r
        WHERE r.task_id IN (SELECT value FROM json_each(?))
        UNION ALL SELECT t.id,t.created_at AS sort_time,'chat' AS source
        FROM tasks t WHERE t.owner_id=?
        ) ORDER BY sort_time DESC,source DESC,id DESC''',
        (json.dumps(task_ids), chat_id)).fetchall()
    candidates = []
    for row in rows:
        seq = _item_sequence(row['sort_time'], row['source'], row['id'])
        if before_seq > 0 and seq >= before_seq:
            continue
        candidates.append((seq, row))
    runs=[]
    import project_tasks
    for seq, row in candidates[:limit]:
        if row['source'] == 'chat':
            run = db.get_async_task(row['id'])
            counts = {item['kind']: item['amount'] for item in db.db.execute(
                'SELECT kind,COUNT(*) AS amount FROM run_events WHERE run_id=? GROUP BY kind', (row['id'],))}
            runs.append({'id': row['id'], 'sequence': seq, 'status': run['status'],
                         'created_at': run['created_at'], 'updated_at': run['updated_at'],
                         'message': redact((run['checkpoint'].get('request') or {}).get('message', '')),
                         'runtime': {}, 'model_name': '', 'counts': counts})
            continue
        run = project_tasks.get_run(db, row['id'])
        task = project_tasks._task(db, run['task_id'])
        count = db.db.execute('SELECT COUNT(*) FROM execution_conversation_events WHERE run_id=? AND task_id=?',
                              (run['id'], task['id'])).fetchone()[0]
        runs.append({'id': run['id'], 'sequence': seq, 'status': run['status'],
                     'created_at': run['started_at'], 'updated_at': run['ended_at'] or task['updated_at'],
                     'message': redact(task['title']), 'runtime': {}, 'model_name': '',
                     'workflow_run_id': task['workflow_run_id'], 'node_id': task['workflow_node_id'],
                     'task_id': task['id'], 'task_run_id': run['id'], 'attempt': run['attempt'],
                     'counts': {'execution_event': count}, 'monitoring': monitoring(run)})
    return {'runs':runs,'next_before':runs[-1]['sequence'] if len(candidates)>limit else None,'chat_id':chat_id,'project_id':project_id}


def _trajectory_task_ids(db, chat_id, project_id, binding):
    import workflow_conversation_context
    ctx = workflow_conversation_context.context(db, project_id, chat_id)
    if not binding or binding.get('kind') != 'task':
        return ctx['task_ids']
    task_id = binding.get('entity_id')
    task_row = db.db.execute('SELECT workflow_run_id FROM project_tasks WHERE id=?', (task_id,)).fetchone()
    bound_flow = (task_row[0] or '') if task_row else ''
    explicit_runs = [r['id'] for r in ctx.get('runs', []) if r['id'] != bound_flow]
    return [row['id'] for row in db.db.execute('''
        WITH RECURSIVE roots(id) AS (
            SELECT id FROM project_tasks WHERE project_id=? AND (id=? OR (workflow_run_id IN (SELECT value FROM json_each(?)) AND workflow_run_id!=''))
        ), members(id) AS (
            SELECT id FROM roots
            UNION
            SELECT t.id FROM project_tasks t JOIN members m ON t.parent_task_id=m.id WHERE t.project_id=?
        ) SELECT t.id FROM project_tasks t JOIN members m ON m.id=t.id ORDER BY t.rowid
    ''', (project_id, task_id, json.dumps(explicit_runs), project_id))]


def _execution_events(db,chat_id,project_id,run_id,*,after=0,limit=300):
    import project_tasks
    import execution_conversations
    saved = db.db.execute('SELECT task_id FROM project_task_runs WHERE id=?', (run_id,)).fetchone()
    if not saved:return None
    binding=_execution_binding(db,chat_id,project_id)
    if saved['task_id'] not in _trajectory_task_ids(db,chat_id,project_id,binding):
        raise ValueError('此运行不属于当前对话')
    run = project_tasks.get_run(db, run_id)
    task = project_tasks._task(db, saved['task_id'])
    limit=max(1,min(int(limit),500)); after=int(after or 0)
    rows=db.db.execute('SELECT rowid AS event_id,payload,created_at FROM execution_conversation_events WHERE run_id=? AND task_id=? AND rowid>? ORDER BY rowid LIMIT ?',
                       (run_id,task['id'],after,limit+1)).fetchall()
    records=[]
    for row in rows[:limit]:
        payload=execution_conversations.project_event(db, task, run, json.loads(row['payload'] or '{}'))
        records.append({'event_id':row['event_id'],'kind':payload.get('type','execution_event'),'created_at':row['created_at'],'payload':payload})
    return {'run_id':run_id,'status':run['status'],'events':records,'has_more':len(rows)>limit,'monitoring':monitoring(run),
            'next_after':records[-1]['event_id'] if records else after}


def route(method,path,body,query,db):
    if method!='GET' or path not in ('/api/agent-settings/trajectory','/api/agent-settings/trajectory/events'):return None
    def q(key,default=''):
        value=query.get(key,[default]);return value[0] if isinstance(value,list) else value
    if path.endswith('/events'):
        return events(db,str(q('chat_id')),str(q('project_id')),str(q('run_id')),after=q('after',0),limit=q('limit',300))
    return listing(db,str(q('chat_id')),str(q('project_id')),before=q('before',0),limit=q('limit',20))
