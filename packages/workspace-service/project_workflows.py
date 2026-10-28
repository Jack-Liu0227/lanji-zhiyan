"""Project-owned DAG revisions. Shared templates and running snapshots stay independent."""
import hashlib
import json
from pathlib import Path

import project_bindings
import workflow_layout
from workflow import now


def ensure(db):
    if getattr(db,'_project_workflows_initialized',False):return
    tables={row[0] for row in db.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('project_workflow_definitions','project_task_output_revisions')")}
    if len(tables)!=2:
        db.db.executescript((Path(__file__).resolve().parents[2]/'migrations/020_project_workflow_nodes.sql').read_text(encoding='utf-8'))
    db._project_workflows_initialized=True


def fingerprint(layout):
    return hashlib.sha256(json.dumps({key:layout.get(key) for key in ('name','description','nodes','edges','order')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def effective(db,project_id,template):
    ensure(db)
    row=db.db.execute('SELECT * FROM project_workflow_definitions WHERE project_id=? AND layout_id=?',(project_id,template['id'])).fetchone()
    value={**template,**json.loads(row['definition'])} if row else dict(template)
    return {**value,'revision':row['revision'] if row else 0,'fingerprint':fingerprint(value),'project_override':bool(row)}


def current(db,project_id,template_id=''):
    workflow_layout.ensure_schema(db);workflow_layout._project(db,project_id)
    if not template_id:
        row=db.db.execute('SELECT layout_id FROM project_workflow_layouts WHERE task_id=?',(project_id,)).fetchone()
        template_id=row['layout_id'] if row else workflow_layout.DEFAULT_LAYOUT_ID
    return effective(db,project_id,workflow_layout._layout(db,template_id))


def save(db,project_id,body):
    import project_tasks
    workflow_layout.ensure_schema(db);ensure(db)
    with project_tasks._lock,project_tasks._atomic(db):
        old=current(db,project_id,str(body.get('layout_id') or ''))
        if body.get('expected_revision')!=old['revision'] or body.get('expected_fingerprint')!=old['fingerprint']:
            raise ValueError('工作流已被修改，请重新载入后再保存')
        definition=workflow_layout.validate_definition(body)
        name=str(body.get('name') or old['name']).strip()
        if not name or len(name)>120:raise ValueError('工作流名称须为1至120字')
        definition.update(name=name,description=str(body.get('description') or '')[:4000])
        for node in definition['nodes']:
            if node.get('executor_binding_id'):
                project_bindings.resolve(db,project_id,node['executor_binding_id'],member_id=node.get('member_id',''))
        stamp=now()
        db.db.execute('INSERT INTO project_workflow_definitions VALUES (?,?,?,?,?,?) ON CONFLICT(project_id,layout_id) DO UPDATE SET revision=excluded.revision,definition=excluded.definition,base_fingerprint=excluded.base_fingerprint,updated_at=excluded.updated_at',
                      (project_id,old['id'],old['revision']+1,json.dumps(definition,ensure_ascii=False),old['fingerprint'],stamp))
        selected=db.db.execute('SELECT selected_node FROM project_workflow_layouts WHERE task_id=?',(project_id,)).fetchone()
        if selected and selected['selected_node'] not in {node['id'] for node in definition['nodes']}:
            db.db.execute('UPDATE project_workflow_layouts SET selected_node=?,updated_at=? WHERE task_id=?',(definition['order'][0],stamp,project_id))
    return current(db,project_id,old['id'])


def route(method,path,body,query,db):
    if path=='/api/workflow/layouts/publish' and method=='POST':
        pid=str(body.get('project_id') or '');old=current(db,pid,str(body.get('layout_id') or ''))
        template=workflow_layout._layout(db,old['id'])
        if body.get('expected_fingerprint')!=old['fingerprint'] or body.get('expected_template_updated_at')!=template['updated_at']: raise ValueError('模板或项目配置已变化，请重新载入后发布')
        saved=workflow_layout.save_layout(db,{**old,'id':old['id']})['layout']
        with db.db:
            db.db.execute('DELETE FROM project_workflow_definitions WHERE project_id=? AND layout_id=?',(pid,old['id']))
            db.db.execute('DELETE FROM project_workflow_roles WHERE project_id=? AND template_id=?',(pid,old['id']))
        return {'layout':current(db,pid,saved['id'])}
    if path!='/api/workflow/layouts/project':return None
    if method=='POST':
        return {'layout':save(db,str(body.get('project_id') or ''),body)}
    if method=='GET':
        def q(key):
            value=query.get(key,['']);return value[0] if isinstance(value,list) else value
        return {'layout':current(db,str(q('project_id')),str(q('layout_id')))}
    raise ValueError('不支持的项目工作流操作')
