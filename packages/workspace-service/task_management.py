"""Human task organization never rewrites execution or review states."""
from datetime import date
import json

import project_tasks


def command(db, project_id, task_id, action, body):
    import workflow
    task = project_tasks.get(db, task_id, project_id=project_id)
    meta = dict(task['metadata'])
    if action == 'location':
        return {'path': str(db.base_dir(project_id) / '.tasks' / task_id)}
    if action == 'duplicate':
        copy = project_tasks.create(db, {k: task[k] for k in (
            'project_id', 'goal', 'executor_binding_id', 'member_id', 'inputs', 'output_contract', 'review_required')}
            | {'title': task['title'] + '（副本）'}, authority=project_tasks.CommandContext.ui(project_id))
        return {'task': copy}
    title = task['title']
    if action == 'delete':
        if task['status'] in ('queued', 'running'):
            raise ValueError('请先停止正在执行的任务，再删除记录')
        if task['workflow_run_id']:
            row = db.db.execute('SELECT status FROM workflow_runs WHERE id=?', (task['workflow_run_id'],)).fetchone()
            if row and row['status'] not in ('succeeded', 'failed', 'cancelled'):
                raise ValueError('工作流尚未结束，请先完成或停止工作流，再删除节点记录')
        for row in db.db.execute('SELECT id,dependencies,status FROM project_tasks WHERE project_id=?', (project_id,)):
            if task_id in json.loads(row['dependencies']) and row['status'] not in ('delivered', 'cancelled'):
                raise ValueError('仍有任务依赖此交付，请先处理依赖任务')
        meta['deleted_at'] = workflow.now()
    elif action == 'restore':
        meta.pop('deleted_at', None)
    elif action == 'organize':
        if 'title' in body:
            title = str(body['title']).strip()
            if not title or len(title) > 500:
                raise ValueError('任务名称须为 1 至 500 字符')
        for key in ('priority', 'start_date', 'due_date', 'pinned'):
            if key not in body:
                continue
            value = body[key]
            if key == 'priority' and value not in ('P0', 'P1', 'P2'):
                raise ValueError('优先级须为 P0、P1 或 P2')
            if key.endswith('_date') and value:
                date.fromisoformat(str(value))
            meta[key] = bool(value) if key == 'pinned' else str(value or '')
        if meta.get('start_date') and meta.get('due_date') and meta['start_date'] > meta['due_date']:
            raise ValueError('截止日期不能早于开始日期')
    else:
        raise ValueError('不支持的任务管理操作')
    with db.db:
        db.db.execute('UPDATE project_tasks SET title=?,metadata=?,updated_at=? WHERE id=?',
            (title, json.dumps(meta, ensure_ascii=False), workflow.now(), task_id))
        project_tasks._event(db, task_id, action)
        if action=='organize' and 'title' in body:
            db.db.execute("UPDATE chats SET title=? WHERE id IN (SELECT chat_id FROM execution_conversations WHERE kind='task' AND entity_id=?)",(title,task_id))
    return {'task': project_tasks.get(db, task_id, project_id=project_id)}
