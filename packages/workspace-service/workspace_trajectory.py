"""Read-only projections of durable conversation events, with bounded secret-safe results."""
import json
import re

SENSITIVE={'api_key','apikey','api-key','password','passwd','secret','access_token','refresh_token','authorization','credential','client_secret','token'}
DELTAS=('assistant_delta','reasoning_delta')


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
                     'message':redact(request.get('message','')),'runtime':config,'model_name':model.get('name') or model.get('model') or '',
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
    records=[{'event_id':row['event_id'],'kind':row['kind'],'created_at':row['created_at'],'payload':redact(json.loads(row['payload']))} for row in rows[:limit]]
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
    if not binding:return None
    limit=max(1,min(int(limit),50)); before=int(before or 0)
    args=[binding['entity_id'] if binding['kind']=='task' else '',binding['entity_id'] if binding['kind']=='workflow' else '']
    where='task_id IN (SELECT id FROM project_tasks WHERE id=? OR workflow_run_id=?)'
    if before:
        where+=' AND rowid<?';args.append(before)
    args.append(limit+1)
    rows=db.db.execute('SELECT rowid AS sequence,run_id,MIN(created_at) AS created_at,MAX(created_at) AS updated_at,COUNT(*) AS event_count FROM execution_conversation_events WHERE '+where+' GROUP BY run_id ORDER BY sequence DESC LIMIT ?',args).fetchall()
    runs=[]
    for row in rows:
        task=db.db.execute('SELECT t.title,r.status,r.attempt,r.error FROM project_task_runs r JOIN project_tasks t ON t.id=r.task_id WHERE r.id=?',(row['run_id'],)).fetchone()
        if task is None:
            task=db.db.execute('SELECT title,status,0 AS attempt,error FROM workflow_runs WHERE id=?',(binding['entity_id'],)).fetchone()
        status=(task['status'] if task else 'done')
        runs.append({'id':row['run_id'],'sequence':row['sequence'],'status':status,'created_at':row['created_at'],'updated_at':row['updated_at'],
                     'message':redact(task['title'] if task else '工作流执行'),'runtime':{},'model_name':'',
                     'counts':{'execution_event':row['event_count']},'attempt':task['attempt'] if task else 0})
    return {'runs':runs[:limit],'next_before':runs[-1]['sequence'] if len(runs)>limit else None,'chat_id':chat_id,'project_id':project_id}


def _execution_events(db,chat_id,project_id,run_id,*,after=0,limit=300):
    binding=_execution_binding(db,chat_id,project_id)
    if not binding:return None
    limit=max(1,min(int(limit),500)); after=int(after or 0)
    rows=db.db.execute('SELECT rowid AS event_id,payload,created_at FROM execution_conversation_events WHERE run_id=? AND rowid>? ORDER BY rowid LIMIT ?',
                       (run_id,after,limit+1)).fetchall()
    records=[]
    for row in rows[:limit]:
        payload=redact(json.loads(row['payload'] or '{}'))
        records.append({'event_id':row['event_id'],'kind':payload.get('type','execution_event'),'created_at':row['created_at'],'payload':payload})
    task=db.db.execute('SELECT status FROM project_task_runs WHERE id=?',(run_id,)).fetchone()
    status=task['status'] if task else 'done'
    return {'run_id':run_id,'status':status,'events':records,'has_more':len(rows)>limit,
            'next_after':records[-1]['event_id'] if records else after}


def route(method,path,body,query,db):
    if method!='GET' or path not in ('/api/agent-settings/trajectory','/api/agent-settings/trajectory/events'):return None
    def q(key,default=''):
        value=query.get(key,[default]);return value[0] if isinstance(value,list) else value
    if path.endswith('/events'):
        return events(db,str(q('chat_id')),str(q('project_id')),str(q('run_id')),after=q('after',0),limit=q('limit',300))
    return listing(db,str(q('chat_id')),str(q('project_id')),before=q('before',0),limit=q('limit',20))
