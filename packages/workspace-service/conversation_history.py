"""Shared history commands; deleting a conversation never deletes project files."""
import json
from datetime import datetime, timezone
from functools import wraps
import threading

_lifecycle_lock=threading.RLock()


def synchronized(function):
    @wraps(function)
    def call(*args,**kwargs):
        with _lifecycle_lock:return function(*args,**kwargs)
    return call


def _flags(db,kind,identifier):
    if kind=='chat':
        row=db.db.execute('SELECT * FROM chats WHERE id=?',(identifier,)).fetchone()
    elif kind=='team':
        row=db.db.execute("SELECT s.*,COALESCE(f.pinned_at,'') AS pinned_at,COALESCE(f.archived_at,'') AS archived_at,COALESCE(f.deleted_at,'') AS deleted_at FROM team_sessions s LEFT JOIN conversation_flags f ON f.kind='team' AND f.id=s.id WHERE s.id=?",(identifier,)).fetchone()
    else:raise ValueError('会话类型无效')
    if not row:raise ValueError('会话不存在')
    return dict(row)


def _busy(db,kind,row):
    if kind=='chat':
        import execution_conversations
        if execution_conversations.busy(db,row['id']):return True
    if kind=='team':
        import team
        return bool(row['active'] and team.TEAM_RUNS.get(row['team_id']))
    if db.db.execute("SELECT 1 FROM tasks WHERE owner_id=? AND kind='agent_run' AND status IN ('pending','running','suspended')",(row['id'],)).fetchone():return True
    from run_manager import RunManager
    return any(run.task_id==row['id'] and run.worker_started for run in list(RunManager.get_instance().runs.values()))


def _publish_change(db,kind,row,action):
    if kind=='chat' and row.get('task_id'):
        import execution_board
        execution_board.publish(db,row['task_id'],'conversation_'+action,task_id=row['id'],source='conversation',activity='会话记录已更新')
        db.db.commit()


@synchronized
def command(db,kind,identifier,action,*,title='',project_id='',team_id=''):
    row=_flags(db,kind,identifier)
    if kind=='chat' and project_id and row['task_id']!=project_id: raise ValueError('会话不属于当前项目')
    if kind=='team' and row['team_id']!=team_id: raise ValueError('会话不属于当前团队')
    if action in ('archive','trash','purge') and _busy(db,kind,row): raise ValueError('请先停止并等待当前会话运行结束，再归档或删除')
    if action=='export':
        messages=db.messages(row['id']) if kind=='chat' else db.messages(team_id,'team:'+identifier)
        if kind=='chat':
            import execution_conversations
            execution=execution_conversations.read(db,row['id'])
            if execution:messages=execution['messages']
        text='# '+row['title']+'\n\n'+'\n\n'.join('## '+str(message.get('sender') or message.get('role',''))+'\n\n'+str(message.get('content','')) for message in messages)
        return {'title':row['title'],'content':text,'messages':messages}
    if action=='rename':
        title=str(title or '').strip()
        if not title or len(title)>120: raise ValueError('对话名称须为 1 至 120 字')
        with db.db:db.db.execute('UPDATE '+('chats' if kind=='chat' else 'team_sessions')+' SET title=? WHERE id=?',(title,identifier))
    elif action=='purge':
        if not row['deleted_at']:raise ValueError('请先将会话移入最近删除')
        with db.db:
            if kind=='chat':
                db.clear_messages(identifier);db.delete_chat(identifier)
            else:
                db.db.execute('DELETE FROM chat WHERE task_id=? AND scope=?',(team_id,'team:'+identifier))
                db.db.execute('DELETE FROM team_sessions WHERE id=?',(identifier,))
                db.db.execute("DELETE FROM conversation_flags WHERE kind='team' AND id=?",(identifier,))
        _publish_change(db,kind,row,action)
        return {'ok':True}
    else:
        stamp=datetime.now(timezone.utc).isoformat()
        if action not in ('pin','unpin','archive','unarchive','trash','restore'):raise ValueError('不支持的会话操作')
        fields={'pin':{'pinned_at':stamp},'unpin':{'pinned_at':''},'archive':{'archived_at':stamp},'unarchive':{'archived_at':''},'trash':{'deleted_at':stamp},'restore':{'deleted_at':'','archived_at':''}}[action]
        with db.db:
            if kind=='chat':db.db.execute('UPDATE chats SET '+','.join(key+'=?' for key in fields)+' WHERE id=?',[*fields.values(),identifier])
            else:
                db.db.execute('INSERT OR IGNORE INTO conversation_flags(kind,id) VALUES (?,?)',('team',identifier))
                db.db.execute("UPDATE conversation_flags SET "+','.join(key+'=?' for key in fields)+" WHERE kind='team' AND id=?",[*fields.values(),identifier])
                if action in ('trash','archive'):db.db.execute('UPDATE team_sessions SET active=0 WHERE id=?',(identifier,))
    _publish_change(db,kind,row,action)
    return {'ok':True,'conversation':_flags(db,kind,identifier)}


def route(method,path,body,query,db):
    if not path.startswith('/api/conversation-history'):return None
    def q(key,default=''):return (query.get(key) or [default])[0]
    kind=str(body.get('kind') or q('kind','chat'));project=str(body.get('project_id') or q('project_id'));team_id=str(body.get('team_id') or q('team_id'))
    if method=='POST':return command(db,kind,str(body.get('id') or ''),str(body.get('action') or ''),title=body.get('title',''),project_id=project,team_id=team_id)
    if kind=='chat':
        rows=[dict(row) for row in db.db.execute("SELECT * FROM chats WHERE channel='conversation' "+('AND task_id=? ' if project else '')+'ORDER BY pinned_at DESC,updated_at DESC',([project] if project else []))]
    elif kind=='team':
        if not team_id or not db.team(team_id):raise ValueError('请选择有效团队')
        rows=[_flags(db,'team',row[0]) for row in db.db.execute('SELECT id FROM team_sessions WHERE team_id=? ORDER BY updated_at DESC',(team_id,))]
        rows.sort(key=lambda row:(row['pinned_at'],row['updated_at']),reverse=True)
    else:raise ValueError('会话类型无效')
    view=q('view','active');needle=q('q').casefold()
    rows=[row for row in rows if (bool(row['deleted_at']) if view=='trash' else not row['deleted_at'] and bool(row['archived_at']) if view=='archive' else not row['deleted_at'] and not row['archived_at']) and (not needle or needle in row['title'].casefold())]
    for row in rows:row['running']=_busy(db,kind,row)
    return {'conversations':rows,'kind':kind}
