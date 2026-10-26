"""Pending human reviews with reminders independent of workflow approval."""
from datetime import datetime, timedelta, timezone


def _stamp(value):
    if not value:
        return ''
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return parsed.astimezone(timezone.utc).isoformat()


def pending(db):
    now = datetime.now(timezone.utc).isoformat()
    rows = db.db.execute("""SELECT n.id,n.run_id,n.started_at,n.ended_at,n.acceptance,n.checkpoint,
        r.project_id,t.title,m.due_at,m.remind_at,m.dismissed FROM workflow_nodes n
        JOIN workflow_runs r ON r.id=n.run_id JOIN workflow_executions e ON e.run_id=r.id
        JOIN research_tasks t ON t.id=r.project_id LEFT JOIN review_reminders m ON m.node_id=n.id
        WHERE n.status='awaiting_review' AND r.status='awaiting_review' AND r.current_node=n.id""").fetchall()
    import json
    items = []
    for row in rows:
        received = _stamp(row['ended_at'] or row['started_at'])
        due, remind = _stamp(row['due_at']), _stamp(row['remind_at']) or received
        items.append({'node_id': row['id'], 'run_id': row['run_id'], 'project_id': row['project_id'],
            'project_title': row['title'], 'label': json.loads(row['acceptance']).get('config', {}).get('label', '节点审查'),
            'received_at': received, 'due_at': due, 'remind_at': remind,
            'dismissed': bool(row['dismissed']), 'overdue': bool(due and due < now),
            'notify': bool(not row['dismissed'] and remind <= now),
            'summary': json.loads(row['checkpoint']).get('reply', '')[:500]})
    return sorted(items, key=lambda r: r['due_at'] or r['received_at'])


def update(db, node_id, body):
    item = next((r for r in pending(db) if r['node_id'] == node_id), None)
    if not item:
        raise ValueError('该节点已不在待审查列表中，请刷新')
    due = _stamp(body.get('due_at', item['due_at']))
    remind = _stamp(body.get('remind_at', item['remind_at']))
    dismissed = bool(body.get('dismissed', item['dismissed']))
    if 'snooze_minutes' in body:
        minutes = max(1, min(10080, int(body['snooze_minutes'])))
        remind = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        dismissed = False
    db.db.execute('INSERT OR REPLACE INTO review_reminders(node_id,due_at,remind_at,dismissed,updated_at) VALUES(?,?,?,?,?)',
        (node_id, due, remind, int(dismissed), datetime.now(timezone.utc).isoformat()))
    db.db.commit()
    return {'items': pending(db)}


def route(method, path, body, query, db):
    if path != '/api/workflow/reviews':
        return None
    if method == 'POST':
        return update(db, str(body.get('node_id') or ''), body)
    items = pending(db)
    return {'items': items, 'count': len(items), 'notify_count': sum(r['notify'] for r in items)}
