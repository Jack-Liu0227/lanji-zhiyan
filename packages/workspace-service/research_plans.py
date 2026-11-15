"""Editable research goals linked to many independently versioned task runs."""
import json

import execution_board
import project_tasks
import project_todos


def linked(db, plan_id):
    return [dict(row) for row in db.db.execute('SELECT t.id,t.title,t.status,t.latest_run_id FROM project_tasks t JOIN plan_task_links l ON l.task_id=t.id WHERE l.plan_id=? ORDER BY l.created_at',(plan_id,))]


def check_dependencies(db, task_id):
    for row in db.db.execute('SELECT p.dependencies FROM project_todos p JOIN plan_task_links l ON l.plan_id=p.id WHERE l.task_id=?',(task_id,)):
        for plan_id in json.loads(row['dependencies'] or '[]'):
            dependency=db.db.execute('SELECT status FROM project_todos WHERE id=?',(plan_id,)).fetchone()
            if not dependency or dependency['status']!='DONE': raise ValueError('前置研究计划尚未完成验收')


def list_plans(db, project_id):
    plans=project_todos.list_todos(db,project_id)
    for plan in plans:
        plan['project_id']=project_id
        plan['tasks']=linked(db,plan['id'])
        plan['dependencies']=json.loads(plan.get('dependencies') or '[]')
    return plans


def route(method,path,body,query,db):
    if not path.startswith('/api/plans'): return None
    pid=str(body.get('project_id') or (query.get('project_id') or [''])[0])
    if method=='GET' and not pid and (query.get('all_projects') or [''])[0]=='1':
        projects=[{'id':row['id'],'title':row['title']} for row in db.tasks()]
        plans=[dict(plan,project_name=project['title']) for project in projects for plan in list_plans(db,project['id'])]
        return {'plans':plans,'project_id':'','projects':projects}
    project_tasks._project(db,pid)
    if method=='GET':
        return {'plans':list_plans(db,pid),'project_id':pid}
    identifier=str(body.get('id') or '')
    old=db.db.execute('SELECT * FROM project_todos WHERE id=? AND task_id=?',(identifier,pid)).fetchone() if identifier else None
    if identifier and not old: raise ValueError('计划不属于当前项目')
    if path.endswith('/delete'):
        return project_todos.delete_todo(db,identifier)
    title=str(body.get('title') or (old['title'] if old else '')).strip()
    if not title or len(title)>500: raise ValueError('计划标题须为 1 至 500 字')
    acceptance=str(body.get('acceptance',old['acceptance'] if old else ''))
    deps=body.get('dependencies',json.loads(old['dependencies']) if old else [])
    if len(acceptance)>20000 or not isinstance(deps,list) or any(not isinstance(dep,str) for dep in deps): raise ValueError('验收要求或依赖无效')
    for dep in deps:
        if dep==identifier or not db.db.execute('SELECT 1 FROM project_todos WHERE id=? AND task_id=?',(dep,pid)).fetchone(): raise ValueError('前置计划必须属于同一项目且不能是自身')
        pending=[dep];seen=set()
        while pending:
            current=pending.pop()
            if current==identifier: raise ValueError('计划依赖不能形成循环')
            if current in seen: continue
            seen.add(current)
            row=db.db.execute('SELECT dependencies FROM project_todos WHERE id=?',(current,)).fetchone()
            if row: pending.extend(json.loads(row['dependencies']))
    fields={key:body[key] for key in ('description','priority','assigned_assistant','due_date','status','stage') if key in body}
    if old: plan=project_todos.update_todo(db,identifier,title=title,**fields)
    else: plan=project_todos.add_todo(db,pid,title,str(fields.get('description') or ''),str(fields.get('priority') or 'P1'),str(fields.get('assigned_assistant') or ''),str(fields.get('due_date') or ''),str(fields.get('stage') or ''))
    if not old and fields.get('status'): plan=project_todos.update_todo(db,plan['id'],status=fields['status'])
    identifier=plan['id']
    with db.db:
        db.db.execute('UPDATE project_todos SET acceptance=?,dependencies=? WHERE id=?',(acceptance,json.dumps(list(dict.fromkeys(deps))),identifier))
        execution_board.publish(db,pid,'plan_updated',source='plan',task_id=identifier,activity=title)
    return {'plan':dict(db.db.execute('SELECT * FROM project_todos WHERE id=?',(identifier,)).fetchone())}
