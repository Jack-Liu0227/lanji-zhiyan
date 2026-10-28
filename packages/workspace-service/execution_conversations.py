"""Durable one-to-one execution conversations, projected from canonical runs."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

import workspace_trajectory


def now():
    return datetime.now(timezone.utc).isoformat()


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


def _task_messages(db, task):
    import project_tasks
    messages=[]
    for row in db.db.execute('SELECT id FROM project_task_runs WHERE task_id=? ORDER BY attempt',(task['id'],)):
        run=project_tasks.get_run(db,row['id']);checkpoint=run['checkpoint'];who=actor(db,run['executor_snapshot'])
        events=[json.loads(row['payload']) for row in db.db.execute('SELECT payload FROM execution_conversation_events WHERE run_id=? ORDER BY id',(run['id'],))]
        if not events:events=workspace_trajectory.redact(checkpoint.get('events') or [])
        reply=checkpoint.get('reply') or run.get('output_snapshot',{}).get('reply') or ('执行失败：'+run['error'] if run.get('error') else '')
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
        summary={'title':task['title'],'goal':task['goal'],'status':task['status'],'workflow_run_id':task['workflow_run_id'],'outputs':task['outputs'],
                 'error':(task.get('latest_run') or {}).get('error') or task['metadata'].get('_start_error',''),
                 'created_at':task['created_at'],'executor':actor(db,executor),'executor_binding_id':task['executor_binding_id'],'member_id':task['member_id']}
        children=[{'id':child['id'],'title':child['title'],'status':child['status'],'chat_id':(link(db,'task',child['id']) or {}).get('chat_id','')} for child in task['children']]
    else:
        import workflow_execution
        run=workflow_execution.detail(db,binding['entity_id'])
        tasks=project_tasks.list_tasks(db,binding['project_id'],workflow_run_id=run['id'])
        order=list(run['settings'].get('task_ids',{}).values())
        tasks.sort(key=lambda task:order.index(task['id']) if task['id'] in order else len(order))
        summary={'title':run['layout'].get('name','工作流'),'goal':run['settings'].get('goal',''),
                  'status':run['status'],'error':run.get('error',''),'workflow_run_id':run['id'],'created_at':run['started_at'],'prepared':bool(run['settings'].get('prepared'))}
        children=[{'id':task['id'],'title':task['title'],'status':task['status'],'chat_id':task.get('chat_id','')} for task in tasks]
    messages=[{'id':chat_id+':goal','role':'user','content':summary['goal'] or summary['title'],'created_at':summary['created_at'],'events':[]}]
    for task in tasks:messages.extend(_task_messages(db,task))
    messages.extend(db.messages(chat_id))
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
