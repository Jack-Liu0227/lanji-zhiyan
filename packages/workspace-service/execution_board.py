"""Project-scoped projections of authoritative tasks and actual Agent events."""
from datetime import datetime, timezone
import json

import project_tasks
import execution_status


def publish(db, project_id, kind, *, task_id='', run_id='', source='task', activity=''):
    if not project_id or not db.db.execute("SELECT 1 FROM sqlite_master WHERE name='execution_board_events'").fetchone(): return
    db.db.execute('INSERT INTO execution_board_events(project_id,task_id,run_id,source,kind,activity,created_at) VALUES (?,?,?,?,?,?,?)',
        (project_id, task_id, run_id, source, kind, str(activity)[:500], datetime.now(timezone.utc).isoformat()))


def agent_event(db, owner_id, run_id, event):
    import team_runtime
    team_runtime.agent_event(db, run_id, event)
    kind = event.get('type', '')
    if kind.endswith('_delta') or kind in ('usage', 'thinking', 'reasoning'): return
    row = db.db.execute('SELECT task_id FROM chats WHERE id=?', (owner_id,)).fetchone()
    project = row['task_id'] if row else owner_id if db.db.execute('SELECT 1 FROM research_tasks WHERE id=?', (owner_id,)).fetchone() else ''
    publish(db, project, kind, task_id=owner_id, run_id=run_id, source='conversation',
            activity=event.get('name') or {'run_started':'开始执行','done':'执行结束','error':'执行失败','cancelled':'已停止'}.get(kind, kind))
    db.db.commit()


def task_event(db, task_id, kind, data=None, run_id=''):
    row = db.db.execute('SELECT project_id FROM project_tasks WHERE id=?', (task_id,)).fetchone()
    if not row: return
    publish(db, row['project_id'], kind, task_id=task_id, run_id=run_id, activity=(data or {}).get('name') or kind)
    refresh_plans(db, task_id)


def refresh_plans(db, task_id):
    if not db.db.execute("SELECT 1 FROM sqlite_master WHERE name='plan_task_links'").fetchone(): return
    plan_ids = [r['plan_id'] for r in db.db.execute('SELECT plan_id FROM plan_task_links WHERE task_id=?', (task_id,))]
    for pid in plan_ids:
        states = [r[0] for r in db.db.execute('SELECT t.status FROM project_tasks t JOIN plan_task_links l ON l.task_id=t.id WHERE l.plan_id=?', (pid,))]
        status = 'DONE' if states and all(state == 'delivered' for state in states) else 'REVIEW' if 'awaiting_review' in states else 'IN_PROGRESS' if any(state in ('queued','running','delivered') for state in states) else 'TODO'
        db.db.execute('UPDATE project_todos SET status=?,updated_at=? WHERE id=?', (status, datetime.now(timezone.utc).isoformat(), pid))


def events(db, project_id, after=0, *, task_id='', run_id='', limit=200, latest=False, all_projects=False):
    if all_projects and not project_id:
        where='id>?';args=[max(0,int(after))]
    else:
        project_tasks._project(db, project_id)
        where='project_id=? AND id>?';args=[project_id, max(0,int(after))]
    if task_id: where+=' AND task_id=?';args.append(task_id)
    if run_id: where+=' AND run_id=?';args.append(run_id)
    rows=[dict(row) for row in db.db.execute('SELECT * FROM execution_board_events WHERE '+where+(' ORDER BY id DESC' if latest else ' ORDER BY id')+' LIMIT ?', [*args,max(1,min(int(limit),500))])]
    if latest: rows.reverse()
    return {'events':rows, 'cursor':rows[-1]['id'] if rows else int(after)}


def snapshot(db, project_id):
    project_tasks._project(db, project_id)
    cursor=db.db.execute('SELECT COALESCE(MAX(id),0) FROM execution_board_events WHERE project_id=?',(project_id,)).fetchone()[0]
    rows=[]
    hidden={(row['item_kind'],row['item_id']):row['deleted_at'] for row in db.db.execute('SELECT * FROM execution_board_hidden WHERE project_id=?',(project_id,))}
    for row in db.db.execute("SELECT t.*,c.id AS chat_id,c.title AS conversation_title FROM project_tasks t JOIN execution_conversations x ON x.kind='task' AND x.entity_id=t.id JOIN chats c ON c.id=x.chat_id AND c.deleted_at='' WHERE t.project_id=? AND t.workflow_run_id='' ORDER BY t.updated_at DESC LIMIT 300", (project_id,)):
        task=dict(row);run=project_tasks.get_run(db,task['latest_run_id']) if task['latest_run_id'] else {}
        actor=run.get('executor_snapshot',{})
        if not actor:
            import project_bindings
            try: actor=project_bindings.resolve(db,project_id,task['executor_binding_id'],member_id=task.get('member_id',''),materialize=False)
            except ValueError: actor={}
        latest=db.db.execute('SELECT * FROM execution_board_events WHERE project_id=? AND task_id=? ORDER BY id DESC LIMIT 1',(project_id,task['id'])).fetchone()
        grant=db.db.execute('SELECT kind,target_id FROM project_executor_bindings WHERE id=?',(task['executor_binding_id'],)).fetchone()
        assistant=db.assistant(actor.get('assistant_id','')) or {}
        meta=json.loads(task['metadata'] or '{}')
        rows.append({'id':task['id'],'kind':'task','title':task['conversation_title'],'status':task['status'],
            'deleted_at':hidden.get(('task',task['id']),meta.get('deleted_at','')),'pinned':meta.get('pinned',False),'priority':meta.get('priority','P1'),
            'start_date':meta.get('start_date',''),'due_date':meta.get('due_date',''),
            'project_id':project_id,'chat_id':task['chat_id'],'parent_task_id':task['parent_task_id'],'workflow_run_id':task['workflow_run_id'],
            'workflow_node_id':task['workflow_node_id'],'run_id':run.get('id',''),'attempt':run.get('attempt',0),
            'assistant_name':assistant.get('name',''),'model_id':actor.get('runtime_config',{}).get('model_id',''),
            'executor_kind':grant['kind'] if grant else actor.get('kind',''), 'activity':latest['activity'] if latest else '',
            'updated_at':task['updated_at'],'error':run.get('error') or meta.get('_start_error',''),'plans':[r[0] for r in db.db.execute('SELECT plan_id FROM plan_task_links WHERE task_id=?',(task['id'],))]})
    for row in db.db.execute("SELECT t.*,c.title,c.channel,c.task_id AS project_id FROM tasks t JOIN chats c ON c.id=t.owner_id WHERE c.task_id=? AND c.deleted_at='' AND t.kind='agent_run' AND NOT EXISTS (SELECT 1 FROM execution_conversations x WHERE x.chat_id=c.id) AND t.id=(SELECT r.id FROM tasks r WHERE r.owner_id=c.id AND r.kind='agent_run' ORDER BY CASE r.status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 WHEN 'suspended' THEN 2 ELSE 3 END,r.rowid DESC LIMIT 1) ORDER BY t.updated_at DESC LIMIT 100",(project_id,)):
        request=json.loads(row['checkpoint'] or '{}').get('request',{})
        cfg=request.get('_runtime_snapshot',{})
        assistant=db.assistant(cfg.get('assistant_id','builtin-steward')) or {}
        latest=db.db.execute('SELECT kind,payload FROM run_events WHERE run_id=? AND kind NOT LIKE ? ORDER BY event_id DESC LIMIT 1',(row['id'],'%_delta')).fetchone()
        event=json.loads(latest['payload']) if latest else {}
        rows.append({'id':row['owner_id'],'kind':'conversation','channel':row['channel'],'chat_id':row['owner_id'],'project_id':project_id,
            'deleted_at':hidden.get(('conversation',row['owner_id']),''),
            'title':row['title'],'status':execution_status.normalize(row['status']),
            'run_id':row['id'],'assistant_name':assistant.get('name',''),'model_id':cfg.get('model_id',''),
            'activity':event.get('name') or (latest['kind'] if latest else ''),'updated_at':row['updated_at'],
            'error':event.get('message','') if row['status']=='failed' else '', 'attempt':1})
    for row in db.db.execute("SELECT r.*,x.chat_id,c.title,c.updated_at FROM workflow_runs r JOIN execution_conversations x ON x.kind='workflow' AND x.entity_id=r.id JOIN chats c ON c.id=x.chat_id WHERE x.project_id=? AND c.deleted_at='' ORDER BY c.updated_at DESC LIMIT 100",(project_id,)):
        rows.append({'id':row['chat_id'],'chat_id':row['chat_id'],'kind':'conversation','execution_kind':'workflow',
                     'project_id':project_id,'title':row['title'],'workflow_run_id':row['id'],'run_id':row['id'],
                     'status':{'succeeded':'completed'}.get(row['status'],row['status']),'assistant_name':'工作流',
                     'activity':row['current_node'],'updated_at':row['updated_at'],'error':row['error'],
                     'deleted_at':hidden.get(('conversation',row['chat_id']),'')})
    for item in rows:
        kind='workflow' if item.get('execution_kind')=='workflow' else item['kind']
        item.update(execution_status.project(db,item['chat_id'],item['status'],kind=kind,run_id=item['run_id']))
        if item['active_run_kind']=='conversation' and kind!='conversation':
            chat=execution_status.chat_run(db,item['chat_id'])
            cfg=chat['checkpoint'].get('request',{}).get('_runtime_snapshot',{})
            item.update(activity='conversation_running',updated_at=chat['updated_at'],
                        assistant_name=cfg.get('assistant_name') or item['assistant_name'],model_id=cfg.get('model_id') or item.get('model_id',''))
        elif item['status'] in ('draft','ready') and not item['run_id']:
            item['activity']='not_started'
    rows.sort(key=lambda item:(bool(item.get('pinned')),item['updated_at']),reverse=True)
    return {'project_id':project_id,'items':rows,'cursor':cursor}


def snapshot_all(db):
    projects=[{'id':row['id'],'title':row['title']} for row in db.tasks()]
    cursor=db.db.execute('SELECT COALESCE(MAX(id),0) FROM execution_board_events').fetchone()[0]
    items=[]
    for project in projects:
        items.extend(dict(row, project_name=project['title']) for row in snapshot(db,project['id'])['items'])
    items.sort(key=lambda row:(bool(row.get('pinned')),row['updated_at']),reverse=True)
    return {'project_id':'','projects':projects,'items':items,'cursor':cursor}


def visibility(db, project_id, action, items):
    """Hide board entries without rewriting execution, conversation or artifact state."""
    project_tasks._project(db,project_id)
    if action not in ('delete','restore') or not isinstance(items,list) or not 1<=len(items)<=500:
        raise ValueError('请选择 1 至 500 条记录进行删除或恢复')
    selected={}
    for item in items:
        if not isinstance(item,dict):raise ValueError('看板记录无效')
        kind=item.get('kind');identifier=str(item.get('id') or '')
        if kind=='task':
            row=db.db.execute('SELECT id,metadata FROM project_tasks WHERE id=? AND project_id=?',(identifier,project_id)).fetchone()
        elif kind=='conversation':
            row=db.db.execute("SELECT id FROM chats WHERE id=? AND task_id=? AND deleted_at=''",(identifier,project_id)).fetchone()
            if not row:
                row=db.db.execute("SELECT c.id FROM tasks t JOIN chats c ON c.id=t.owner_id WHERE t.id=? AND t.kind='agent_run' AND c.task_id=? AND c.deleted_at=''",(identifier,project_id)).fetchone()
            if row:identifier=row['id']
        else:raise ValueError('看板记录类型无效')
        if not row:raise ValueError('记录已删除或不属于当前项目，请刷新看板')
        selected[(kind,identifier)]=dict(row)
    stamp=datetime.now(timezone.utc).isoformat()
    with db.db:
        for (kind,identifier),row in selected.items():
            if action=='delete':
                db.db.execute('INSERT OR IGNORE INTO execution_board_hidden VALUES (?,?,?,?)',(project_id,kind,identifier,stamp))
            else:
                db.db.execute('DELETE FROM execution_board_hidden WHERE project_id=? AND item_kind=? AND item_id=?',(project_id,kind,identifier))
                if kind=='task':
                    metadata=json.loads(row['metadata'] or '{}')
                    if metadata.pop('deleted_at',None):
                        db.db.execute('UPDATE project_tasks SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),identifier))
            publish(db,project_id,'board_'+action,task_id=identifier if kind=='task' else '',run_id=identifier if kind=='conversation' else '',source='board',activity='看板记录已删除' if action=='delete' else '看板记录已恢复')
    return {'ok':True,'count':len(selected)}


def link_plan(db, project_id, plan_id, task_id):
    task=project_tasks.get(db,task_id,project_id=project_id)
    plan=db.db.execute('SELECT * FROM project_todos WHERE id=? AND task_id=?',(plan_id,project_id)).fetchone()
    if not plan: raise ValueError('计划不属于当前项目')
    with db.db:
        db.db.execute('INSERT OR IGNORE INTO plan_task_links VALUES (?,?,?)',(plan_id,task_id,datetime.now(timezone.utc).isoformat()))
        task_event(db,task['id'],'plan_linked',{'plan_id':plan_id})
    return task


def route(method,path,body,query,db):
    if not path.startswith('/api/execution-board'): return None
    def q(key,default=''): return (query.get(key) or [default])[0]
    project_id=str(body.get('project_id') or q('project_id'))
    if method=='GET' and q('all_projects')=='1' and not project_id:
        if path=='/api/execution-board': return snapshot_all(db)
        if path=='/api/execution-board/events': return events(db,'',q('after',0),all_projects=True)
    project_tasks._project(db,project_id)
    if method=='GET' and path=='/api/execution-board': return snapshot(db,project_id)
    if method=='POST' and path=='/api/execution-board/visibility': return visibility(db,project_id,str(body.get('action') or ''),body.get('items'))
    if method=='GET' and path.endswith('/events'): return events(db,project_id,q('after',0),task_id=q('task_id'),run_id=q('run_id'),limit=q('limit',200),latest=q('latest')=='1')
    if method=='POST' and path.endswith('/link-plan'): return {'task':link_plan(db,project_id,str(body.get('plan_id') or ''),str(body.get('task_id') or ''))}
    if method=='POST' and path.endswith('/conversation-action'):
        from run_manager import RunManager
        import agent_jobs
        run=db.get_async_task(str(body.get('run_id') or ''))
        owner=db.db.execute("SELECT task_id FROM chats WHERE id=? AND deleted_at=''",(run['owner_id'],)).fetchone() if run and run['kind']=='agent_run' else None
        if not owner or owner['task_id']!=project_id: raise ValueError('运行不属于当前项目')
        if body.get('action')=='stop': RunManager.get_instance().cancel_run(run['id'],db=db)
        elif body.get('action')=='resume': agent_jobs.start(db.path,{},resume_id=run['id'])
        else: raise ValueError('不支持的运行操作')
        return {'ok':True}
