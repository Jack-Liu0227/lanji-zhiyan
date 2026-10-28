"""Scheduled workflow attempts keep waiting for review until final delivery."""
from __future__ import annotations

from datetime import datetime
import json

import asset_service


def pending(db, schedule_id):
    existing = db.db.execute("SELECT w.id FROM schedule_workflow_runs l JOIN schedule_runs s ON s.id=l.schedule_run_id JOIN workflow_runs w ON w.id=l.workflow_run_id WHERE s.schedule_id=? AND w.status IN ('queued','running','awaiting_review','blocked','interrupted')", (schedule_id,)).fetchone()
    return existing or db.db.execute("SELECT t.id FROM project_task_origins o JOIN project_tasks t ON t.id=o.task_id JOIN schedule_runs s ON s.id=o.legacy_id WHERE o.kind='schedule_run' AND s.schedule_id=? AND t.status IN ('ready','queued','running','awaiting_review','blocked')", (schedule_id,)).fetchone()


def start_task(db, sched, run, *, emit=None, cancel_check=None):
    """Adapt one scheduled report into canonical work while retaining archive IDs."""
    import project_bindings
    import project_tasks
    import project_task_execution
    import project_todos
    import run_service
    import schedule
    schedule.output_dir_for(db, sched)
    assistant = db.assistant(sched['assistant_id'])
    if not assistant or not assistant.get('enabled'):
        raise ValueError('绑定的专家不存在或已停用')
    grant = project_bindings.register(db, sched['project_id'], 'assistant', sched['assistant_id'], source='legacy')
    inputs = [a['id'] for a in asset_service.research_assets(db, sched['project_id'], limit=200)]
    metadata = {'schedule_id': sched['id'], 'schedule_snapshot': sched, 'schedule_run_id': run['id']}
    project_todos.ensure_table(db)
    linked = db.db.execute('SELECT p.stage FROM task_links l JOIN project_todos p ON p.id=l.todo_id WHERE l.schedule_id=?', (sched['id'],)).fetchone()
    stage = linked['stage'] if linked else ''
    scope = {}
    if stage:
        import workflow_state
        import workflow_layout
        layout = workflow_state.definition(db, sched['project_id'])
        normalized = workflow_layout.validate_definition(layout)
        candidates = [n for n in normalized['nodes'] if n['step'] == stage]
        if len(candidates) != 1:
            raise ValueError('该阶段无法唯一定位节点，请将定时任务绑定到具体工作流')
        if workflow_state.gate(db, sched['project_id'], stage)['blocked']:
            raise ValueError('上游交付尚未验收或已变化，请先处理上游节点')
        handoff, fingerprint, stage_inputs = workflow_state.handoff(db, sched['project_id'], stage)
        node_config = {**candidates[0], 'prompt': sched['prompt'], 'label': sched['name']}
        stages = [n['step'] for n in normalized['nodes']]
        compat = all(s in workflow_layout.STAGES for s in stages) and len(stages) == len(set(stages))
        metadata.update(stage=stage, node_config=node_config, legacy_stage_compat=compat,
            layout_snapshot={**layout, **normalized}, upstream_context={'project': db.summary(sched['project_id'])['task'],
                'goal': sched['prompt'], 'handoff': json.loads(handoff or '[]'), 'upstream_hash': fingerprint,
                'history': [], 'memory': db.memory_block(sched['project_id']), 'input_assets': stage_inputs})
        if node_config.get('tools') is not None:
            scope['tools'] = node_config['tools']
    task = project_tasks.create(db, {'project_id': sched['project_id'], 'title': sched['name'], 'goal': sched['prompt'],
        'executor_binding_id': grant['id'], 'inputs': inputs, 'scope': scope, 'review_required': bool(stage) or bool(sched.get('review_required', False)),
        'metadata': metadata},
        authority=project_tasks.CommandContext.system(sched['project_id']), request_key='schedule:' + run['id'])
    tracked = run_service.start(db, sched['project_id'], 'schedule', idempotency_key='schedule:' + run['id'])
    node = run_service.node_start(db, tracked['id'], 'schedule', inputs=inputs)
    run['workflow_run_id'] = tracked['id']
    db.schedule_run_add(run)
    project_tasks.link_origin(db, task['id'], 'schedule_run', run['id'])
    project_tasks.link_origin(db, task['id'], 'schedule_node', node['id'])
    authority = project_tasks.CommandContext.system(sched['project_id'])
    try:
        attempt = project_tasks.start(db, task['id'], authority=authority, request_key='schedule:' + run['id'])
    except Exception as exc:
        project_tasks.block_start(db, task['id'], str(exc), authority=authority)
        raise
    project_todos.sync_execution(db, schedule_id=sched['id'], status='running', run_id=tracked['id'])
    db.project_task_emit = emit
    def removed_or_cancelled():
        current = db.schedule(sched['id'])
        return bool(cancel_check and cancel_check()) or not current or current.get('project_id') != sched['project_id'] or not db.db.execute('SELECT 1 FROM research_tasks WHERE id=?', (sched['project_id'],)).fetchone()
    project_task_execution.execute(db, attempt['id'], removed_or_cancelled)
    return refresh_task(db, project_tasks.get(db, task['id'])) or run


def refresh_task(db, task):
    """Project state into its schedule attempt; archive only a delivered version."""
    import schedule
    import project_todos
    import workflow
    origin = next((o for o in task['origins'] if o['kind'] == 'schedule_run'), None)
    if not origin:
        return
    record = db.db.execute("SELECT r.*,n.id AS note_id FROM schedule_runs r LEFT JOIN md_notes n ON n.id='schedule-' || r.id WHERE r.id=?", (origin['legacy_id'],)).fetchone()
    if not record:
        return
    run = dict(record)
    metadata = task['metadata']
    sched = metadata.get('schedule_snapshot') or db.schedule(run['schedule_id'])
    if not sched or not db.schedule(run['schedule_id']):
        return
    attempt = task.get('latest_run') or {}
    checkpoint = attempt.get('checkpoint') or {}
    status = {'ready': 'queued', 'delivered': 'completed', 'cancelled': 'canceled'}.get(task['status'], task['status'])
    error = attempt.get('error') or metadata.get('_start_error', '')
    ended_at = attempt.get('ended_at', '') or (task['updated_at'] if status == 'blocked' and not attempt else '')
    run.update(status=status, reason=error, ended_at=ended_at)
    nodes = [o['legacy_id'] for o in task['origins'] if o['kind'] == 'schedule_node']
    assets = [f['asset_id'] for f in task['outputs'] if f.get('asset_id')]
    tracked = db.db.execute('SELECT id FROM workflow_runs WHERE idempotency_key=?', ('schedule:' + run['id'],)).fetchone()
    tracked_id = tracked['id'] if tracked else ''
    run['workflow_run_id'] = tracked_id
    projected = 'succeeded' if status == 'completed' else 'cancelled' if status == 'canceled' else status
    with db.db:
        for node_id in nodes:
            db.db.execute('UPDATE workflow_nodes SET status=?,checkpoint=?,output_assets=?,error=?,ended_at=? WHERE id=?',
                (projected, json.dumps(checkpoint, ensure_ascii=False), json.dumps(assets), run['reason'], run['ended_at'], node_id))
        if tracked_id:
            db.db.execute('UPDATE workflow_runs SET status=?,error=?,ended_at=? WHERE id=?', (projected, run['reason'], run['ended_at'], tracked_id))
    if status == 'completed' and not run.get('note_id'):
        try:
            text = str(checkpoint.get('reply') or (attempt.get('output_snapshot') or {}).get('content') or '').strip()
            header = f"# {sched['name']}\n\n> 由定时任务生成 · {run['started_at']} · Cron `{sched['cron']}`\n\n"
            folder = schedule.output_dir_for(db, sched)
            path = folder / f"{schedule._safe_name(sched['name'])[:60]}-{run['id'][:8]}.md"
            asset_service.atomic_write_text(path, header + text + '\n')
            asset = asset_service.register_file(db, path, project_id=sched['project_id'], kind='schedule_output', source='schedule', run_id=tracked_id,
                body=header + text, metadata={'schedule_id': sched['id'], 'schedule_run_id': run['id'], 'project_task_id': task['id']})
            for source_id in assets:
                asset_service.link(db, asset['id'], 'derived_from', source_id)
            note = schedule.archive_note(db, sched, run, header + text + '\n', asset)
            run.update(output_path=str(path), summary=text[:160].replace('\n', ' '), note_id=note['id'], note_path=note['canonical_path'], reason='',
                output_assets=list(dict.fromkeys([*assets, asset['id'], *([note['asset_id']] if note.get('asset_id') else [])])))
        except Exception as exc:
            run.update(status='partial', reason='任务已交付，笔记归档待重试：' + str(exc))
    if run.get('note_id'):
        from pathlib import Path
        note_path = db._md_note_path(db.md_note(run['note_id']))
        run['note_path'] = str(note_path)
        archived = db.db.execute('SELECT id FROM assets WHERE canonical_uri=?', (str(Path(run['output_path']).resolve()),)).fetchone() if run.get('output_path') else None
        note_asset = db.db.execute('SELECT id FROM assets WHERE canonical_uri=?', (str(note_path.resolve()),)).fetchone() if note_path else None
        run['output_assets'] = list(dict.fromkeys([*assets, *([archived['id']] if archived else []), *([note_asset['id']] if note_asset else [])]))
    db.schedule_run_add(run)
    if run['status'] not in ('queued', 'running', 'awaiting_review', 'blocked'):
        db.schedule_mark_run(sched['id'], run['ended_at'] or workflow.now(), schedule.next_run_at(sched['cron']))
    project_todos.sync_execution(db, schedule_id=sched['id'], status=run['status'], run_id=tracked_id, assets=run.get('output_assets'))
    return run


def task_changed(db, task):
    refresh_task(db, task)
    if task['status'] != 'delivered' or not task['workflow_run_id']:
        return
    if db.db.execute('SELECT 1 FROM schedule_workflow_runs WHERE workflow_run_id=? AND archived=0', (task['workflow_run_id'],)).fetchone():
        import workflow_execution
        workflow_execution.kick(db.path, task['workflow_run_id'])


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
    import project_tasks
    for row in db.db.execute("SELECT task_id FROM project_task_origins WHERE kind='schedule_run'").fetchall():
        refresh_task(db, project_tasks.get(db, row['task_id']))


def cancel(db, schedule_id):
    import project_tasks
    for row in db.db.execute("SELECT o.task_id FROM project_task_origins o JOIN schedule_runs s ON s.id=o.legacy_id WHERE o.kind='schedule_run' AND s.schedule_id=?", (schedule_id,)).fetchall():
        task = project_tasks.get(db, row['task_id'])
        if task['status'] not in ('delivered', 'cancelled'):
            project_tasks.cancel(db, task['id'], authority=project_tasks.CommandContext.system(task['project_id']))
    import workflow_execution
    for row in db.db.execute("SELECT w.id FROM schedule_workflow_runs l JOIN schedule_runs s ON s.id=l.schedule_run_id JOIN workflow_runs w ON w.id=l.workflow_run_id WHERE s.schedule_id=? AND w.status NOT IN ('succeeded','cancelled')", (schedule_id,)).fetchall():
        workflow_execution.route('POST', '/api/workflow/execution/cancel', {'id': row['id']}, {}, db)
