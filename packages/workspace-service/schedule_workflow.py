"""Scheduled workflow attempts keep waiting for review until final delivery."""
from __future__ import annotations

from datetime import datetime
import json

import asset_service


def pending(db, schedule_id):
    return db.db.execute("SELECT w.id FROM schedule_workflow_runs l JOIN schedule_runs s ON s.id=l.schedule_run_id JOIN workflow_runs w ON w.id=l.workflow_run_id WHERE s.schedule_id=? AND w.status IN ('queued','running','awaiting_review','blocked','interrupted')", (schedule_id,)).fetchone()


def start(db, sched, run):
    import schedule
    import workflow_execution as engine
    import workflow_layout
    schedule.output_dir_for(db, sched)
    with engine._lock:
        previous = engine.latest(db, sched['project_id'])
        if previous and previous['status'] in engine.ACTIVE:
            raise ValueError('该项目已有进行中或待审的工作流，请先处理原运行')
        workflow_layout.select_layout(db, {'task_id': sched['project_id'], 'layout_id': sched['workflow_id']})
        execution = engine.create(db, sched['project_id'], sched['prompt'], 'schedule:' + run['id'])
        run['workflow_run_id'] = execution['id']
        db.db.execute('INSERT INTO schedule_workflow_runs VALUES (?,?,?,0)',
            (run['id'], execution['id'], json.dumps(sched, ensure_ascii=False)))
        db.db.commit()
        db.schedule_mark_run(sched['id'], run['started_at'], schedule.next_run_at(sched['cron']))
        refresh(db, execution['id'])
        engine.kick(db.path, execution['id'])
    return run


def refresh(db, run_id):
    import schedule
    import project_todos
    link = db.db.execute('SELECT * FROM schedule_workflow_runs WHERE workflow_run_id=?', (run_id,)).fetchone()
    if not link or link['archived']:
        return
    record = db.db.execute('SELECT * FROM schedule_runs WHERE id=?', (link['schedule_run_id'],)).fetchone()
    execution = db.db.execute('SELECT * FROM workflow_runs WHERE id=?', (run_id,)).fetchone()
    if not record or not execution or not db.schedule(record['schedule_id']):
        return
    run, execution = dict(record), dict(execution)
    sched = json.loads(link['schedule_snapshot'])
    run['workflow_run_id'] = run_id
    status = execution['status']
    run.update(status={'succeeded': 'completed', 'queued': 'running', 'cancelled': 'canceled'}.get(status, status),
        reason=execution['error'], ended_at=execution['ended_at'])
    if status == 'succeeded':
        try:
            import workflow_execution
            full = workflow_execution.detail(db, run_id)
            delivered = [n for n in full['nodes'] if n['status'] == 'delivered']
            if not delivered or len(delivered) != len(full['layout']['nodes']):
                raise ValueError('工作流尚有未交付节点，不能归档为完成')
            sections = [f"# {sched['name']}", f"工作流运行：`{run_id}`", full['settings'].get('goal', '')]
            for node in delivered:
                cp = node['checkpoint']
                sections += ['## ' + node['acceptance']['config']['label'], cp.get('reply', '')]
                if cp.get('feedback'):
                    sections.append('审查意见：' + cp['feedback'])
                sections += [f'- 产物：{path}' for path in cp.get('output', {}).get('files', [])]
            content = delivered[-1]['checkpoint'].get('output', {}).get('content')
            if isinstance(content, str) and content:
                sections += ['## 最终交付内容', content]
            text = '\n\n'.join(str(s) for s in sections if s) + '\n'
            folder = schedule.output_dir_for(db, sched)
            path = folder / f"{schedule._safe_name(sched['name'])}-{run['id'][:8]}.md"
            asset_service.atomic_write_text(path, text)
            asset = asset_service.register_file(db, path, project_id=sched['project_id'], kind='schedule_output',
                source='schedule', run_id=run_id, body=text, metadata={'schedule_id': sched['id'], 'schedule_run_id': run['id']})
            for node in delivered:
                for output in node['output_assets']:
                    asset_service.link(db, asset['id'], 'derived_from', output)
            note = schedule.archive_note(db, sched, run, text, asset)
            run.update(output_path=str(path), summary=delivered[-1]['checkpoint'].get('reply', '')[:160],
                       note_id=note['id'], reason='')
        except Exception as exc:
            run.update(status='partial', reason='工作流已交付，笔记归档待重试：' + str(exc))
    db.schedule_run_add(run)
    if run['status'] == 'completed' and run.get('note_id'):
        db.db.execute('UPDATE schedule_workflow_runs SET archived=1 WHERE workflow_run_id=?', (run_id,))
        db.db.commit()
    project_todos.sync_execution(db, schedule_id=sched['id'], status=run['status'], run_id=run_id)


def reconcile(db):
    for row in db.db.execute('SELECT workflow_run_id FROM schedule_workflow_runs WHERE archived=0').fetchall():
        refresh(db, row['workflow_run_id'])


def cancel(db, schedule_id):
    import workflow_execution
    for row in db.db.execute("SELECT w.id FROM schedule_workflow_runs l JOIN schedule_runs s ON s.id=l.schedule_run_id JOIN workflow_runs w ON w.id=l.workflow_run_id WHERE s.schedule_id=? AND w.status NOT IN ('succeeded','cancelled')", (schedule_id,)).fetchall():
        workflow_execution.route('POST', '/api/workflow/execution/cancel', {'id': row['id']}, {}, db)
