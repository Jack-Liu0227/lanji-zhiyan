"""Conversation-owned, read-only workflow monitoring projection."""
import json
from datetime import datetime, timezone

import project_tasks
from workspace_trajectory import monitoring, redact


def summary(db, run_id):
    row = db.db.execute('''SELECT r.*,e.layout_snapshot,e.settings,e.updated_at FROM workflow_runs r
        JOIN workflow_executions e ON e.run_id=r.id WHERE r.id=?''', (run_id,)).fetchone()
    layout, settings = json.loads(row['layout_snapshot']), json.loads(row['settings'])
    nodes = []
    task_ids = settings.get('task_ids') or {}
    for node in layout.get('nodes', []):
        task = db.db.execute('''SELECT * FROM project_tasks WHERE workflow_run_id=? AND
            (id=? OR workflow_node_id=?) AND parent_task_id='' ORDER BY rowid DESC LIMIT 1''',
            (run_id, task_ids.get(node['id'], ''), node['id'])).fetchone()
        task = project_tasks._decode(task, project_tasks._TASK_JSON) if task else {}
        attempt = project_tasks.get_run(db, task['latest_run_id']) if task.get('latest_run_id') else {}
        checkpoint = attempt.get('checkpoint') or {}
        watch = monitoring(attempt) if attempt else {}
        status = task.get('status', 'pending')
        error = attempt.get('error') or task.get('metadata', {}).get('_start_error') or settings.get('dag_errors', {}).get(node['id']) or ''
        if error and status not in ('running', 'delivered', 'awaiting_review', 'cancelled'):
            status = 'failed'
        recovery = project_tasks.recovery_summary({**task, 'status': status, 'latest_run': attempt})
        dependencies = json.loads(row['layout_snapshot']).get('edges', [])
        waits = [e['source'] for e in dependencies if e['target'] == node['id']]
        nodes.append({'node_id': node['id'], 'task_id': task.get('id', ''), 'task_run_id': attempt.get('id', ''),
            'label': task.get('title') or node.get('label') or node['id'], 'status': status,
            'attempt': attempt.get('attempt'), 'started_at': attempt.get('started_at', ''),
            'ended_at': attempt.get('ended_at', ''), 'monitoring': watch, 'recovery': recovery,
            'error': redact(str(error))[:500], 'waiting_for': waits,
            'output_revision': checkpoint.get('output_revision', 0),
            'delivery_id': task.get('accepted_delivery_id', ''),
            'outputs': [{'name': f.get('name', ''), 'relative': f.get('relative', ''),
                         'content_hash': f.get('content_hash') or f.get('sha256', '')}
                        for f in attempt.get('output_snapshot', {}).get('files', [])]})
    delivered = {n['node_id'] for n in nodes if n['status'] == 'delivered' and n['delivery_id']}
    for node in nodes:
        node['waiting_for'] = [parent for parent in node['waiting_for'] if parent not in delivered]
    return {'id': run_id, 'name': layout.get('name') or '工作流', 'status': row['status'],
        'prepared': bool(settings.get('prepared')), 'started_at': row['started_at'], 'ended_at': row['ended_at'],
        'observed_at': datetime.now(timezone.utc).isoformat(), 'updated_at': row['updated_at'],
        'total_count': len(nodes), 'delivered_count': len(delivered),
        'review_count': sum(n['status'] == 'awaiting_review' for n in nodes), 'nodes': nodes}
