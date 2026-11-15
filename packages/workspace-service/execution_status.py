"""Shared display state for a conversation and its actual execution."""
import json


def normalize(status):
    return {'pending':'queued','done':'completed','succeeded':'completed','suspended':'waiting_input','canceled':'cancelled'}.get(status,status)


def chat_run(db, chat_id):
    row=db.db.execute("SELECT * FROM tasks WHERE owner_id=? AND kind='agent_run' ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 WHEN 'suspended' THEN 2 ELSE 3 END,rowid DESC LIMIT 1",(chat_id,)).fetchone()
    if not row:return None
    result=dict(row)
    result['checkpoint']=json.loads(result['checkpoint'] or '{}')
    return result


def project(db, chat_id, status, *, kind, run_id=''):
    """Discussion work can be active without approving the task's delivery."""
    base=normalize(status)
    result={'status':base,'execution_status':base,'active_run_id':run_id if base in ('queued','running') else '',
            'active_run_kind':kind if base in ('queued','running') else ''}
    chat=chat_run(db,chat_id)
    if chat:
        result.update(chat_run_id=chat['id'],chat_status=normalize(chat['status']))
        if base!='running' and (chat['status']=='running' or (chat['status'] in ('pending','suspended') and base!='queued')):
            result.update(status=normalize(chat['status']),active_run_id=chat['id'],active_run_kind='conversation')
    return result
