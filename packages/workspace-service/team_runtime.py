"""One projection for the office, roster and collaboration conversation."""
import json


def snapshot(db, team_id):
    import team
    roster = db.team_members(team_id)
    instances = [team_id, *[r[0] for r in db.db.execute('SELECT instance_team_id FROM team_execution_instances WHERE template_team_id=?', (team_id,))]]
    aliases = {r['instance_member_id']: r['template_member_id'] for r in db.db.execute(
        'SELECT * FROM team_execution_members WHERE instance_team_id IN ('+','.join('?' for _ in instances)+')', instances)}
    members = {m['id']: {**m, 'status': m['status'] if m['status'] in ('disabled','archived') else 'idle', 'active_tasks': []} for m in roster}
    rank = {'idle':0,'failed':1,'blocked':2,'awaiting_review':3,'queued':4,'running':5,'disabled':6,'archived':6}
    communications = []
    def assign(member_id,status,record):
        member=members.get(aliases.get(member_id,member_id))
        if member and status in rank:
            if rank[status]>rank[member['status']]: member['status']=status
            if status in ('running','queued','awaiting_review','blocked'):member['active_tasks'].append(record)
    slots=','.join('?' for _ in instances)
    for row in db.db.execute("SELECT id,title,status,member_id FROM team_tasks WHERE task_id IN ("+slots+") AND status IN ('queued','running','blocked')",instances):
        assign(row['member_id'],row['status'],{key:row[key] for key in ('id','title','status')})
    for row in db.db.execute('SELECT t.id,t.title,t.status,t.project_id,r.executor_snapshot,r.checkpoint,r.ended_at FROM project_tasks t JOIN project_task_runs r ON r.id=t.latest_run_id WHERE json_extract(r.executor_snapshot,?) IN ('+slots+') ORDER BY t.updated_at DESC', ['$.team_id',*instances]):
        actor=json.loads(row['executor_snapshot']);record={k:row[k] for k in ('id','title','status','project_id')}
        assign(actor.get('member_id'),row['status'],record)
        reply=json.loads(row['checkpoint']).get('reply')
        if reply and len(communications)<40:
            communications.append({'id':row['id'],'kind':'delivery','text':str(reply)[:2000],'sender':(actor.get('member') or {}).get('name',''), 'at':row['ended_at'],'project_id':row['project_id']})
    for row in db.db.execute('SELECT id,status,checkpoint FROM tasks WHERE kind=? AND json_extract(checkpoint,?) IN ('+slots+')', ['agent_run','$.request._runtime_snapshot.team_id',*instances]):
        cfg=json.loads(row['checkpoint']).get('request',{}).get('_runtime_snapshot',{})
        assign(cfg.get('member_id'),{'pending':'queued','suspended':'blocked'}.get(row['status'],row['status']),{'id':row['id'],'status':row['status'],'title':'团队成员对话'})
    for scope in instances:
        live=team.TEAM_RUNS.get(scope)
        if live:
            for member in members.values():
                if member['name']==live.get('current'):assign(member['id'],'running',{'title':live.get('phase','团队对话')})
        communications.extend({**event,'at':event['created_at']} for event in db.team_events(scope,40))
    communications.sort(key=lambda row:row.get('at',''),reverse=True)
    state=max((m['status'] for m in members.values() if m['status'] not in ('disabled','archived')),key=lambda status:rank.get(status,0),default='idle')
    return {'members':list(members.values()),'status':state,'communications':communications[:60]}


def agent_event(db, run_id, event):
    if event.get('type') not in ('user','assistant','tool_call','error','cancelled'):
        return
    task=db.get_async_task(run_id)
    cfg=(task or {}).get('checkpoint',{}).get('request',{}).get('_runtime_snapshot',{})
    if not cfg.get('team_id'):
        return
    member=db.db.execute('SELECT name FROM team_members WHERE id=?',(cfg.get('member_id',''),)).fetchone()
    sender='用户' if event['type']=='user' else member['name'] if member else '团队'
    text=event.get('content') or event.get('message') or event.get('name') or ''
    db.team_event_add(run_id,cfg['team_id'],event['type'],sender+'：'+str(text)[:1900])
