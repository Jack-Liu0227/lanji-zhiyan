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
    run=db.get_async_task(run_id)
    if not run or run['owner_id']!=chat_id:raise ValueError('此运行不属于当前对话')
    limit=max(1,min(int(limit),500))
    rows=db.db.execute("SELECT * FROM run_events WHERE run_id=? AND event_id>? AND kind NOT IN ('assistant_delta','reasoning_delta') ORDER BY event_id LIMIT ?",
                       (run_id,int(after),limit+1)).fetchall()
    records=[{'event_id':row['event_id'],'kind':row['kind'],'created_at':row['created_at'],'payload':redact(json.loads(row['payload']))} for row in rows[:limit]]
    return {'run_id':run_id,'status':run['status'],'events':records,'has_more':len(rows)>limit,
            'next_after':records[-1]['event_id'] if records else int(after)}


def route(method,path,body,query,db):
    if method!='GET' or path not in ('/api/agent-settings/trajectory','/api/agent-settings/trajectory/events'):return None
    def q(key,default=''):
        value=query.get(key,[default]);return value[0] if isinstance(value,list) else value
    if path.endswith('/events'):
        return events(db,str(q('chat_id')),str(q('project_id')),str(q('run_id')),after=q('after',0),limit=q('limit',300))
    return listing(db,str(q('chat_id')),str(q('project_id')),before=q('before',0),limit=q('limit',20))
