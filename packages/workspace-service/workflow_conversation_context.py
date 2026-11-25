"""Resolve explicit conversation ownership without falling back to a project run."""
import json


def context(db, project_id, chat_id):
    result = {'project_id': project_id, 'chat_id': chat_id, 'runs': [], 'run_ids': [], 'task_ids': []}
    if not chat_id:
        return result
    chat = db.db.execute('SELECT task_id FROM chats WHERE id=?', (chat_id,)).fetchone()
    if not chat or (chat['task_id'] or '') != project_id:
        raise ValueError('此对话不属于当前项目')
    binding = db.db.execute('SELECT * FROM execution_conversations WHERE chat_id=?', (chat_id,)).fetchone()
    task_id, workflow_id, binding_kind = '', '', ''
    if binding:
        if binding['project_id'] != project_id:
            raise ValueError('此对话不属于当前项目')
        binding_kind = binding['kind']
        if binding['kind'] == 'workflow':
            workflow_id = binding['entity_id']
        elif binding['kind'] == 'task':
            task = db.db.execute('SELECT id,workflow_run_id FROM project_tasks WHERE id=? AND project_id=?',
                                 (binding['entity_id'], project_id)).fetchone()
            if task:
                task_id, workflow_id = task['id'], task['workflow_run_id'] or ''
    rows = db.db.execute('''
        SELECT r.id,r.status,r.started_at,e.layout_snapshot FROM workflow_runs r
        JOIN workflow_executions e ON e.run_id=r.id WHERE r.project_id=? AND
        (r.id=? OR json_extract(e.settings,'$.agent_invocation.chat_id')=?
          OR json_extract(e.settings,'$.research_setup_chat_id')=?
          OR json_extract(e.settings,'$.origin_chat_id')=?)
        ORDER BY r.started_at DESC,r.rowid DESC
    ''', (project_id, workflow_id, chat_id, chat_id, chat_id)).fetchall()
    result['runs'] = [{'id': row['id'], 'status': row['status'], 'started_at': row['started_at'],
                       'layout_name': json.loads(row['layout_snapshot']).get('name', '')} for row in rows]
    import workflow_live_status
    for run in result['runs']:
        run['live'] = workflow_live_status.summary(db, run['id'])
    result['run_ids'] = [row['id'] for row in rows]
    # A workflow chat owns every node in its bound workflow. A node chat owns
    # its subtree; explicitly launched workflows add their own nodes without
    # widening that node chat to the original workflow's sibling nodes.
    explicit_run_ids = [row['id'] for row in rows if row['id'] != workflow_id]
    workflow_task_ids = json.dumps(explicit_run_ids)
    bound_task = task_id if binding_kind == 'task' else ''
    bound_workflow = workflow_id
    result['task_ids'] = [row['id'] for row in db.db.execute('''
        WITH RECURSIVE roots(id) AS (
          SELECT id FROM project_tasks WHERE project_id=? AND
            ((id=? AND id!='') OR (workflow_run_id=? AND workflow_run_id!='') OR
             (workflow_run_id IN (SELECT value FROM json_each(?) WHERE value!='')))
        ), members(id) AS (
          SELECT id FROM roots
          UNION
          SELECT t.id FROM project_tasks t JOIN members m ON t.parent_task_id=m.id WHERE t.project_id=?
        ) SELECT t.id FROM project_tasks t JOIN members m ON m.id=t.id ORDER BY t.rowid
    ''', (project_id, bound_task, bound_workflow, workflow_task_ids, project_id))]
    return result


def require_task(db, project_id, chat_id, task_id):
    if not task_id or task_id not in context(db, project_id, chat_id)['task_ids']:
        raise ValueError('此任务不属于当前对话')
    import project_tasks
    return project_tasks.get(db, task_id)


def route(method, path, body, query, db):
    if method != 'GET' or path != '/api/agent-settings/workflow-context':
        return None
    def q(name):
        value = query.get(name, '')
        return str(value[0] if isinstance(value, list) and value else value or '')
    return context(db, q('project_id'), q('chat_id'))
