"""Immutable task output snapshots and recoverable working-file replacements."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import uuid

import project_tasks as tasks


def remember(db, task, run, commit=True):
    revision = int(run.get('checkpoint', {}).get('output_revision', 0))
    exists = db.db.execute('SELECT 1 FROM project_task_output_versions WHERE task_run_id=? AND revision=? AND task_id=?',
                           (run['id'], revision, task['id'])).fetchone()
    if exists:
        return revision
    db.db.execute('INSERT OR IGNORE INTO project_task_output_versions '
                  '(task_run_id,revision,task_id,output_snapshot,checkpoint,created_at) VALUES (?,?,?,?,?,?)',
                  (run['id'], revision, task['id'], tasks._json(run.get('output_snapshot') or {}),
                   tasks._json(run.get('checkpoint') or {}), tasks._now()))
    if commit:
        db.db.commit()
    return revision


def read(db, task, run, revision):
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError('产物版本必须是非负整数')
    if run['task_id'] != task['id']:
        raise ValueError('产物运行不属于当前任务')
    if revision == int(run.get('checkpoint', {}).get('output_revision', 0)) and run.get('output_snapshot'):
        return run
    row = db.db.execute('SELECT * FROM project_task_output_versions WHERE task_run_id=? AND revision=? AND task_id=?',
                        (run['id'], revision, task['id'])).fetchone()
    if row:
        return {**run, 'output_snapshot': json.loads(row['output_snapshot']), 'checkpoint': json.loads(row['checkpoint'])}
    if revision == int(run.get('checkpoint', {}).get('output_revision', 0)) and run.get('output_snapshot'):
        return {**run, 'output_snapshot': json.loads(tasks._json(run['output_snapshot'])),
                'checkpoint': json.loads(tasks._json(run.get('checkpoint') or {}))}
    raise ValueError('该运行没有指定的产物版本')


def _path(root, raw):
    path = Path(raw).resolve()
    if not path.is_relative_to(root) or Path(raw).is_symlink():
        raise ValueError('修订文件超出项目范围')
    return path


def _restore_bytes(source, target, expected_hash):
    import fileguard
    fileguard.assert_writable(target)
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError('修订候选文件或备份已变化，请停止覆盖并重新核查')
    descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=str(target.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def begin(db, task, run, revision, replacements, rows=None):
    """Persist backups and intent before replacing any shared project content."""
    root = tasks._root(db, task['project_id'], writable=True)
    files = []
    for item in replacements:
        target, source = _path(root, item['path']), _path(root, item['source'])
        if not target.is_file() or not source.is_file() or tasks._hash(target) != item['old_hash']:
            raise ValueError('研究文件已经变化，请重新载入后修订')
        digest = tasks._hash(source)
        backup = source.parent / 'backups' / (str(len(files)) + '-' + target.name)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, backup)
        if tasks._hash(backup) != item['old_hash']:
            raise ValueError('保存修订备份时原文件发生变化')
        prior = db.db.execute('SELECT * FROM assets WHERE canonical_uri=?', (str(target),)).fetchone()
        files.append({'path': str(target), 'source': str(source), 'backup': str(backup),
                      'old_hash': item['old_hash'], 'new_hash': digest,
                      'asset': dict(prior) if prior else None, 'state': 'prepared'})
    operation = {'id': 'file-op-' + uuid.uuid4().hex, 'task_id': task['id'], 'task_run_id': run['id'],
                 'base_revision': int(run.get('checkpoint', {}).get('output_revision', 0)),
                 'target_revision': revision, 'files': {'files': files, 'rows': [{**row, 'state': 'prepared'} for row in rows or []]}}
    stamp = tasks._now()
    db.db.execute('INSERT INTO project_task_file_operations VALUES (?,?,?,?,?,?,\'prepared\',\'\',?,?)',
                  (operation['id'], task['id'], run['id'], operation['base_revision'], revision,
                   tasks._json(operation['files']), stamp, stamp))
    db.db.commit()
    return operation


def _row(db, record):
    if record.get('table') != 'service_settings' or set(record.get('key', {})) != {'service', 'owner'}:
        raise ValueError('无效的修订配置记录')
    key = record['key']
    row = db.db.execute('SELECT * FROM service_settings WHERE service=? AND owner=?',
                        (key['service'], key['owner'])).fetchone()
    return dict(row) if row else None


def _write_row(db, record, value):
    key = record['key']
    if value is None:
        db.db.execute('DELETE FROM service_settings WHERE service=? AND owner=?', (key['service'], key['owner']))
    else:
        db.db.execute('INSERT OR REPLACE INTO service_settings(service,owner,data) VALUES (?,?,?)',
                      (key['service'], key['owner'], value['data']))


def _save_files(db, operation):
    db.db.execute('UPDATE project_task_file_operations SET files=?,updated_at=? WHERE id=?',
                  (tasks._json(operation['files']), tasks._now(), operation['id']))
    db.db.commit()


def apply(db, task, operation):
    import asset_service
    root = tasks._root(db, task['project_id'], writable=True)
    for item in operation['files']['files']:
        path, source = _path(root, item['path']), _path(root, item['source'])
        if not path.is_file() or tasks._hash(path) != item['old_hash'] or tasks._hash(source) != item['new_hash']:
            raise ValueError('研究文件已在其他页面修改，请保留内容并刷新')
        item['state'] = 'writing'
        _save_files(db, operation)
        _restore_bytes(source, path, item['new_hash'])
        prior = item.get('asset') or {}
        asset_service.register_file(db, path, project_id=task['project_id'], kind=prior.get('kind', 'document'),
                                    title=prior.get('title', ''), source='human_revision', body=path.read_text(encoding='utf-8'),
                                    metadata=json.loads(prior.get('metadata') or '{}'))
        item['state'] = 'applied'
    for item in operation['files']['rows']:
        if _row(db, item) != item['before']:
            raise ValueError('节点核查配置已经变化，请刷新')
        item['state'] = 'writing'
        _save_files(db, operation)
        _write_row(db, item, item['after'])
        item['state'] = 'applied'
    _save_files(db, operation)
    db.db.execute("UPDATE project_task_file_operations SET state='applied',updated_at=? WHERE id=?",
                  (tasks._now(), operation['id']))
    db.db.commit()


def finish(db, operation, commit=False):
    db.db.execute("UPDATE project_task_file_operations SET state='committed',error='',updated_at=? WHERE id=?",
                  (tasks._now(), operation['id']))
    if commit:
        db.db.commit()


def rollback(db, task, operation, error=''):
    """Compare before restoring so recovery never overwrites another editor's work."""
    import asset_service
    db.db.rollback()
    completed = db.db.execute('SELECT state,files FROM project_task_file_operations WHERE id=?', (operation['id'],)).fetchone()
    if completed and completed['state'] == 'committed':
        return True
    if completed:
        operation['files'] = json.loads(completed['files'])
    root = tasks._root(db, task['project_id'], writable=True)
    conflicts = []
    for item in reversed(operation['files']['rows']):
        if item.get('state') == 'prepared':
            continue
        current = _row(db, item)
        if current == item['after']:
            _write_row(db, item, item['before'])
        elif current != item['before']:
            conflicts.append('核查配置已由其他操作更新')
    db.db.commit()
    for item in reversed(operation['files']['files']):
        if item.get('state') == 'prepared':
            continue
        path, backup = _path(root, item['path']), _path(root, item['backup'])
        current = tasks._hash(path) if path.is_file() else ''
        if current == item['old_hash']:
            continue
        if current != item['new_hash'] or not backup.is_file() or tasks._hash(backup) != item['old_hash']:
            conflicts.append(str(path))
            continue
        try:
            _restore_bytes(backup, path, item['old_hash'])
            prior = item.get('asset') or {}
            asset_service.register_file(db, path, project_id=task['project_id'], kind=prior.get('kind', 'document'),
                                        title=prior.get('title', ''), source=prior.get('source', 'human_revision_rollback'),
                                        body=path.read_text(encoding='utf-8'), metadata=json.loads(prior.get('metadata') or '{}'))
        except (OSError, ValueError) as exc:
            conflicts.append(str(path) + ': ' + str(exc))
    db.db.execute('UPDATE project_task_file_operations SET state=?,error=?,updated_at=? WHERE id=?',
                  ('conflict' if conflicts else 'rolled_back', '; '.join([str(error), *conflicts]).strip('; ')[:4000], tasks._now(), operation['id']))
    db.db.commit()
    return not conflicts


def recover(db):
    count = 0
    with tasks._lock:
        for row in db.db.execute("SELECT * FROM project_task_file_operations WHERE state IN ('prepared','applied') ORDER BY created_at").fetchall():
            operation = dict(row)
            try:
                operation['files'] = json.loads(operation['files'])
                task = tasks.get(db, operation['task_id'])
                run = tasks.get_run(db, operation['task_run_id'])
                recorded = db.db.execute('SELECT 1 FROM project_task_output_versions WHERE task_run_id=? AND revision=?',
                                        (run['id'], operation['target_revision'])).fetchone()
                if recorded and int(run['checkpoint'].get('output_revision', 0)) >= operation['target_revision']:
                    finish(db, operation, commit=True)
                else:
                    rollback(db, task, operation, '恢复未完成的人工修订')
            except (OSError, ValueError, KeyError) as exc:
                db.db.rollback()
                db.db.execute("UPDATE project_task_file_operations SET state='conflict',error=?,updated_at=? WHERE id=?",
                              (str(exc)[:4000], tasks._now(), operation['id']))
                db.db.commit()
            count += 1
    return count
