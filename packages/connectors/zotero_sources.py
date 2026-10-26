"""Collection sources are independent of explicit project membership."""
from __future__ import annotations

import time

import project_library
import service_settings
import zotero_sync as sync


def configure(db, pid, sources, export_collection=''):
    with sync._sync_lock:
        if not db.db.execute('SELECT 1 FROM research_tasks WHERE id=?', (pid,)).fetchone():
            raise ValueError('项目不存在')
        if not isinstance(sources, list):
            raise ValueError('来源必须是 Collection 列表')
        cfg = sync.config(db)
        lib = sync.library(cfg)
        checked = {}
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError('每个来源必须包含 Collection 和同步范围')
            key = sync._key(str(source.get('collection') or ''))
            mode = source.get('mode', 'selected')
            if mode not in ('selected', 'all'):
                raise ValueError('来源范围应为逐篇选择或全部跟随')
            if key in checked:
                raise ValueError('来源 Collection 重复')
            sync.request(cfg, '/collections/' + key)
            checked[key] = mode
        if export_collection:
            sync.request(cfg, '/collections/' + sync._key(export_collection))
        old = sync.binding(db, pid)
        # Keys are library-local. Switching a library with existing mappings needs an explicit detach first.
        if old and old['library'] != lib and db.db.execute(
                'SELECT 1 FROM zotero_item_links WHERE task_id=?', (pid,)).fetchone():
            raise ValueError('项目已有其他 Library 的条目映射，请使用原文献库或为新文献库建立项目')
        with db.db:
            db.db.execute('INSERT OR REPLACE INTO zotero_bindings VALUES (?,?,?,?)',
                          (pid, lib, export_collection, old.get('last_sync', '') if old.get('library') == lib else ''))
            db.db.execute('DELETE FROM zotero_project_sources WHERE task_id=?', (pid,))
            db.db.executemany('INSERT INTO zotero_project_sources VALUES (?,?,?,?)',
                              [(pid, lib, key, mode) for key, mode in checked.items()])
        service_settings.write(db, 'zotero_plan', {}, pid)
        return sync.binding(db, pid)


def candidates(db, pid):
    bound = sync.binding(db, pid)
    cfg = sync.config(db)
    if not bound or bound['library'] != sync.library(cfg):
        raise ValueError('请先保存当前项目的 Zotero 来源')
    sources = {s['collection'] for s in bound['sources']}
    memberships = {r['item_key']: r['status'] for r in db.db.execute(
        'SELECT item_key,status FROM zotero_project_memberships WHERE task_id=? AND library=?', (pid, bound['library']))}
    mapped = {r['item_key'] for r in db.db.execute('SELECT item_key FROM zotero_item_links WHERE task_id=?', (pid,))}
    rows = []
    for item in sync.library_items(db, cfg):
        data = item.get('data', {})
        if data.get('itemType') in ('note', 'attachment', 'annotation'):
            continue
        if sources and not sources.intersection(data.get('collections', [])):
            continue
        status = memberships.get(item['key'], 'active' if item['key'] in mapped else '')
        rows.append({**sync._from_remote(item), 'key': item['key'], 'collections': data.get('collections', []),
                     'status': status, 'selected': status in ('active', 'selected')})
    return {'items': rows, 'binding': bound}


def select(db, pid, keys):
    """Persist explicit intent; the shared preview/apply sync performs metadata and tag writes."""
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) for k in keys):
        raise ValueError('请至少选择一条文献')
    with sync._sync_lock:
        available = candidates(db, pid)
        by_key = {r['key']: r for r in available['items']}
        if not set(keys) <= by_key.keys():
            raise ValueError('所选文献已移出来源或不存在，请刷新后重新选择')
        lib = available['binding']['library']
        with db.db:
            for key in dict.fromkeys(keys):
                row = by_key[key]
                if row['status'] == 'removing':
                    raise ValueError('文献正在解除关联，请先完成该操作')
                if row['selected']:
                    continue
                # Explicitly selecting an excluded item supersedes its old removal batch.
                old = db.db.execute('SELECT result_id FROM zotero_project_memberships WHERE task_id=? AND library=? AND item_key=?',
                                    (pid, lib, key)).fetchone()
                db.db.execute("INSERT OR REPLACE INTO zotero_project_memberships VALUES (?,?,?,?,?,'selected','',?)",
                              (pid, lib, key, old['result_id'] if old else '', project_library.identity(row), str(time.time())))
        service_settings.write(db, 'zotero_plan', {}, pid)
        return {'ok': True, 'selected': len(set(keys))}
