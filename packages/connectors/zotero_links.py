"""Project membership changes, with persistent intent and reversible batches."""
from __future__ import annotations

import json
import time
import uuid

import project_library
import zotero_sync as sync


def _project(db, pid):
    bound = sync.binding(db, pid)
    cfg = sync.config(db)
    if not db.db.execute('SELECT 1 FROM research_tasks WHERE id=?', (pid,)).fetchone() or not bound or bound['library'] != sync.library(cfg):
        raise ValueError('请先保存当前项目的 Zotero 来源')
    return bound, cfg


def get(db, ident):
    row = db.db.execute('SELECT * FROM zotero_link_batches WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('关联操作不存在')
    value = dict(row)
    value['data'] = json.loads(value['data'])
    return value


def _save(db, batch):
    db.db.execute('UPDATE zotero_link_batches SET data=?,status=? WHERE id=?',
        (json.dumps(batch['data'], ensure_ascii=False), batch['status'], batch['id']))
    db.db.commit()


def preview(db, pid, keys=None):
    bound, cfg = _project(db, pid)
    remote = sync.library_items(db, cfg)
    mapped = {r['item_key']: r['result_id'] for r in db.db.execute('SELECT * FROM zotero_item_links WHERE task_id=?', (pid,))}
    memberships = {r['item_key']: dict(r) for r in db.db.execute(
        'SELECT * FROM zotero_project_memberships WHERE task_id=? AND library=?', (pid, bound['library']))}
    active = {key for key, row in memberships.items() if row['status'] in ('active', 'selected')}
    excluded = {key for key, row in memberships.items() if row['status'] in ('excluded', 'removing')}
    selected = set(keys or [])
    if any(not isinstance(k, str) for k in selected):
        raise ValueError('条目 Key 格式无效')
    tagged = {r['key'] for r in remote if any(t.get('tag') == sync.project_tag(pid) for t in r['data'].get('tags', []))}
    following = {s['collection'] for s in bound['sources'] if s['mode'] == 'all'}
    included = {r['key'] for r in remote if following.intersection(r['data'].get('collections', []))
                and r['data'].get('itemType') not in ('note', 'attachment', 'annotation')}
    if selected and not selected <= tagged | set(mapped) | active | included:
        raise ValueError('只能解除当前项目已关联的文献')
    wanted = selected or ((tagged | set(mapped) | active) - excluded)
    rows = []
    by_key = {r['key']: r for r in remote}
    for key in sorted(wanted):
        item = by_key.get(key)
        if not item:
            # A removed remote item can still have its local association detached.
            item = {'key': key, 'version': 0, 'data': {}}
        data = item['data']
        rid = mapped.get(key, '')
        row = db.result(rid) if rid else sync._from_remote(item)
        shared = [r['title'] for r in db.db.execute('SELECT t.title FROM zotero_project_memberships m JOIN research_tasks t ON t.id=m.task_id WHERE m.library=? AND m.item_key=? AND m.task_id!=? AND m.status=?',
            (bound['library'], key, pid, 'active'))]
        notes = db.db.execute('SELECT COUNT(*) FROM notes n JOIN project_paper_links l ON l.paper_id=n.paper_id WHERE l.result_id=?', (rid,)).fetchone()[0] if rid else 0
        runs = [dict(r) for r in db.db.execute('SELECT DISTINCT r.id,r.status FROM workflow_runs r JOIN workflow_nodes n ON n.run_id=r.id WHERE r.project_id=? AND (instr(n.checkpoint,?)>0 OR instr(n.checkpoint,?)>0)',
            (pid, key, rid or key))]
        rows.append({'key': key, 'result_id': rid, 'identity': project_library.identity(row),
            'title': row.get('title') or data.get('title', key), 'shared_projects': shared, 'status': 'pending',
            'references': {'notes': notes, 'runs': runs},
            'before': {'tag': key in tagged, 'collection': False},
            'version': item['version']})
    batch = {'id': 'zlink-' + uuid.uuid4().hex, 'task_id': pid, 'library': bound['library'],
             'collection': bound['collection'], 'created_at': str(time.time()), 'status': 'preview',
             'data': {'items': rows, 'preserve_history': True, 'preserve_collections': True}}
    db.db.execute('INSERT INTO zotero_link_batches VALUES (?,?,?,?,?,?,?)',
        (batch['id'], pid, batch['library'], batch['collection'], json.dumps(batch['data'], ensure_ascii=False), batch['created_at'], batch['status']))
    db.db.commit()
    return batch


def apply(db, ident, restore=False):
    with sync._sync_lock:
        batch = get(db, ident)
        pid = batch['task_id']
        bound, cfg = _project(db, pid)
        if bound['library'] != batch['library']:
            raise ValueError('项目 Library 绑定已变更，不能执行旧操作')
        if not restore and batch['status'] == 'preview' and not batch['data'].get('preserve_collections'):
            raise ValueError('旧版预览包含 Collection 移除，请重新预览项目关联')
        if not restore and batch['status'] == 'preview' and time.time() - float(batch['created_at']) > 600:
            raise ValueError('解除关联预览已过期，请重新预览')
        if restore and batch['status'] not in ('applied', 'partial', 'restoring', 'restored'):
            raise ValueError('该操作尚未执行，不能恢复')
        if not restore and (batch['status'] in ('restoring', 'restored') or batch['data'].get('operation') == 'restore'):
            raise ValueError('该批次已恢复，请重新预览解除关联')
        batch['data']['operation'] = 'restore' if restore else 'remove'
        batch['status'] = 'restoring' if restore else 'applying'
        _save(db, batch)
        for row in batch['data']['items']:
            if (restore and row['status'] == 'restored') or (not restore and row['status'] == 'removed'):
                continue
            key = row['key']
            try:
                previous = db.db.execute('SELECT * FROM zotero_project_memberships WHERE task_id=? AND library=? AND item_key=?',
                    (pid, bound['library'], key)).fetchone()
                if restore and (not previous or previous['batch_id'] != ident):
                    raise ValueError('关联已由其他操作变更，不能用旧批次恢复')
                if not restore:
                    db.db.execute("INSERT OR REPLACE INTO zotero_project_memberships VALUES (?,?,?,?,?,'removing',?,?)",
                        (pid, bound['library'], key, row['result_id'], row['identity'], ident, str(time.time())))
                    db.db.commit()
                try:
                    item = sync.request(cfg, '/items/' + sync._key(key))
                except (ValueError, RuntimeError) as exc:
                    if '404' not in str(exc) or restore:
                        raise
                    item = None
                if item:
                    data = dict(item['data'])
                    tag = sync.project_tag(pid)
                    tags = list(data.get('tags', []))
                    collections = list(data.get('collections', []))
                    if restore:
                        if row['before']['tag'] and not any(t.get('tag') == tag for t in tags):
                            tags.append({'tag': tag})
                        if row['before']['collection'] and batch['collection'] not in collections:
                            collections.append(batch['collection'])
                    else:
                        tags = [t for t in tags if t.get('tag') != tag]
                    if tags != data.get('tags', []) or collections != data.get('collections', []):
                        data.update(key=key, version=item['version'], tags=tags, collections=collections)
                        item = sync._saved(sync.request(cfg, '/items', 'POST', [data]))
                    if item.get('data'):
                        sync.cache_item(db, bound['library'], item)
                    if restore and row['result_id']:
                        db.db.execute('UPDATE zotero_item_links SET remote_version=? WHERE task_id=? AND result_id=?',
                            (item.get('version', 0), pid, row['result_id']))
                db.db.execute('UPDATE zotero_project_memberships SET status=?,updated_at=? WHERE task_id=? AND library=? AND item_key=?',
                    ('active' if restore else 'excluded', str(time.time()), pid, bound['library'], key))
                db.db.commit()
                row.update(status='restored' if restore else 'removed', error='')
            except Exception as exc:
                row['error'] = str(exc)
            _save(db, batch)
        expected = 'restored' if restore else 'removed'
        batch['status'] = ('restored' if restore else 'applied') if all(r['status'] == expected for r in batch['data']['items']) else 'partial'
        _save(db, batch)
        return batch


def comparison(db, pid, rid, key):
    bound, cfg = _project(db, pid)
    local = db.result(rid)
    if local['task_id'] != pid:
        raise ValueError('文献不属于当前项目')
    remote = sync.request(cfg, '/items/' + sync._key(key))
    # A caller may select a key from a fresh ambiguity preview, but must not silently substitute another paper.
    mapped = db.db.execute('SELECT item_key FROM zotero_item_links WHERE task_id=? AND result_id=?', (pid, rid)).fetchone()
    if mapped and mapped['item_key'] != key:
        raise ValueError('条目映射不一致，请重新预览')
    return {'local': local, 'remote': sync._from_remote(remote), 'version': remote['version'], 'local_hash': sync._hash(local), 'key': key}


def resolve(db, pid, body):
    with sync._sync_lock:
        rid, key = str(body.get('result_id') or ''), str(body.get('key') or '')
        bound, cfg = _project(db, pid)
        check = comparison(db, pid, rid, key)
        if check['version'] != body.get('version') or check['local_hash'] != body.get('local_hash'):
            raise ValueError('比较期间文献已变化，请重新读取冲突')
        remote = sync.request(cfg, '/items/' + key)
        if remote['version'] != check['version']:
            raise ValueError('Zotero 版本已变化，请重新比较')
        decision = body.get('decision')
        if decision not in ('local', 'zotero'):
            raise ValueError('请选择保留项目或 Zotero 的题录')
        chosen = check['local'] if decision == 'local' else check['remote']
        payload = sync._to_remote(chosen, bound['collection'], remote, pid)
        written = sync._saved(sync.request(cfg, '/items', 'POST', [payload]))
        if decision == 'zotero':
            project_library.update_record(db, pid, rid, check['remote'])
        db.db.execute('INSERT OR REPLACE INTO zotero_item_links VALUES (?,?,?,?,?)',
            (pid, rid, key, written.get('version', check['version']), sync._hash(db.result(rid))))
        db.db.execute("INSERT OR REPLACE INTO zotero_project_memberships VALUES (?,?,?,?,?,'active','',?)",
            (pid, bound['library'], key, rid, project_library.identity(db.result(rid)), str(time.time())))
        db.db.commit()
        return {'ok': True}


def route(method, path, body, query, db):
    prefix = '/api/zotero/web/links'
    if not path.startswith(prefix):
        return None
    pid = str(body.get('task_id') or (query.get('task_id') or [''])[0])
    if method == 'GET':
        if path.endswith('/compare'):
            return comparison(db, pid, (query.get('result_id') or [''])[0], (query.get('key') or [''])[0])
        return {'batches': [get(db, r['id']) for r in db.db.execute('SELECT id FROM zotero_link_batches WHERE task_id=? ORDER BY created_at DESC LIMIT 40', (pid,))]}
    if path.endswith('/preview'):
        return {'batch': preview(db, pid, body.get('keys'))}
    if body.get('confirm') is not True:
        raise ValueError('请确认已预览的关联变更')
    if path.endswith('/resolve'):
        return resolve(db, pid, body)
    batch = get(db, str(body.get('batch_id') or ''))
    if batch['task_id'] != pid:
        raise ValueError('操作不属于当前项目')
    if path.endswith('/apply') or path.endswith('/restore'):
        return {'batch': apply(db, batch['id'], path.endswith('/restore'))}
    raise ValueError('未知关联操作')
