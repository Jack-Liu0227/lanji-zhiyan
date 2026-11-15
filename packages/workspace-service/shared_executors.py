"""Global executor profiles resolve to project-owned team runtime instances."""
import hashlib
import threading

_lock=threading.RLock()


def is_instance(db, team_id):
    return bool(db.db.execute('SELECT 1 FROM team_execution_instances WHERE instance_team_id=?',(team_id,)).fetchone())


def templates(db):
    return [dict(row) for row in db.db.execute("SELECT * FROM teams WHERE status NOT IN ('archived','disabled','dissolved') AND id NOT IN (SELECT instance_team_id FROM team_execution_instances) ORDER BY name,id")]


def profile_members(db,team_id,project_id):
    team=db.team(team_id) or {}
    rows=db.team_members(team_id)
    if team.get('project_id')!=project_id:
        return [{**row,'note':'','status':row['status'] if row['status'] in ('disabled','archived') else 'idle'} for row in rows]
    return rows


def virtual(db,project_id,kind,target_id):
    target=db.assistant(target_id) if kind=='assistant' else db.team(target_id) if kind=='team' else None
    if not target: raise ValueError('全局执行者不存在')
    if kind=='team' and is_instance(db,target_id): raise ValueError('执行实例不能作为全局团队配置')
    revision=int(hashlib.sha256(str(target.get('updated_at','')).encode()).hexdigest()[:7],16)
    return {'id':'global:'+kind+':'+target_id,'project_id':project_id,'kind':kind,'target_id':target_id,'enabled':1,'tools':None,'source':'global','revision':revision,'created_at':target.get('created_at',''),'updated_at':target.get('updated_at','')}


def team_instance(db,project_id,template_id):
    template=db.team(template_id)
    if not template or template['status'] in ('disabled','archived','dissolved'): raise ValueError('团队配置已停用或不存在')
    if is_instance(db,template_id):
        if template['project_id']!=project_id: raise ValueError('团队实例不属于当前项目')
        return template
    if template['project_id']==project_id: return template
    roster=db.team_members(template_id)
    if len([member for member in roster if member['role']=='leader'])!=1: raise ValueError('团队必须有唯一负责人')
    with _lock:
        record=db.db.execute('SELECT * FROM team_execution_instances WHERE project_id=? AND template_team_id=?',(project_id,template_id)).fetchone()
        if record and db.team(record['instance_team_id']): instance=db.team(record['instance_team_id'])
        else:
            instance=db.team_add(template['name'],kind='execution_instance',project_id=project_id,purpose=template.get('purpose',''))
            db.db.execute('INSERT OR REPLACE INTO team_execution_instances VALUES (?,?,?,?)',(project_id,template_id,instance['id'],''));db.db.commit()
        active=db.db.execute("SELECT 1 FROM project_tasks t JOIN project_task_runs r ON r.id=t.latest_run_id WHERE t.project_id=? AND t.status IN ('running','queued','awaiting_review') AND json_extract(r.executor_snapshot,'$.team_id')=?",(project_id,instance['id'])).fetchone()
        if not active:
            for original in roster:
                mapping=db.db.execute('SELECT instance_member_id FROM team_execution_members WHERE instance_team_id=? AND template_member_id=?',(instance['id'],original['id'])).fetchone()
                current=db.team_member(mapping[0]) if mapping else None
                fields={key:original.get(key,'') for key in ('name','prompt','assistant_id','model_id','reasoning','permission')}
                if current:
                    changed={key:value for key,value in fields.items() if current.get(key)!=value}
                    if changed:db.team_member_update(current['id'],**changed)
                else:
                    current=db.team_member_add(instance['id'],fields.pop('name'),role=original['role'],**fields)
                    db.db.execute('INSERT OR REPLACE INTO team_execution_members VALUES (?,?,?)',(instance['id'],original['id'],current['id']))
                db.db.execute('UPDATE team_members SET role=?,status=? WHERE id=?',(original['role'],'disabled' if original.get('status') in ('disabled','archived') else 'idle',current['id']))
            keep=[member['id'] for member in roster]
            for mapping in db.db.execute('SELECT * FROM team_execution_members WHERE instance_team_id=?',(instance['id'],)).fetchall():
                if mapping['template_member_id'] not in keep: db.db.execute("UPDATE team_members SET status='disabled',role='member' WHERE id=?",(mapping['instance_member_id'],))
            db.db.execute('UPDATE team_execution_instances SET template_revision=? WHERE instance_team_id=?',(template['updated_at'],instance['id']));db.db.commit()
        return instance


def member_id(db,instance_id,requested):
    if not requested:return ''
    row=db.db.execute('SELECT instance_member_id FROM team_execution_members WHERE instance_team_id=? AND template_member_id=?',(instance_id,requested)).fetchone()
    return row[0] if row else requested


def portable_node(db,node):
    value=dict(node)
    identifier=value.get('executor_binding_id','')
    if identifier:
        if identifier.startswith('global:'): _,kind,target=identifier.split(':',2)
        else:
            row=db.db.execute('SELECT kind,target_id FROM project_executor_bindings WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('节点执行者不存在')
            kind,target=row['kind'],row['target_id']
        if kind=='team':
            instance=db.db.execute('SELECT template_team_id FROM team_execution_instances WHERE instance_team_id=?',(target,)).fetchone()
            if instance:
                mapping=db.db.execute('SELECT template_member_id FROM team_execution_members WHERE instance_team_id=? AND instance_member_id=?',(target,value.get('member_id',''))).fetchone()
                target=instance[0]
                if mapping:value['member_id']=mapping[0]
            value.update(execution_mode='team',team_id=target,assistant_id='')
        else:value.update(execution_mode='assistant',assistant_id=target,team_id='',member_id='')
        value['executor_binding_id']=''
    return value
