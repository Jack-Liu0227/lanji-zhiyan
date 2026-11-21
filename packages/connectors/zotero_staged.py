"""Explicit, user-triggered import of project-staged papers into Zotero."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import zotero


PLAN_TTL = 600


def _task(db: Any, task_id: str) -> dict[str, Any]:
    row = db.db.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("project not found")
    return dict(row)


def _row(db: Any, task_id: str, result_id: str) -> dict[str, Any]:
    import project_library
    result = db.result(result_id)
    if result["task_id"] != task_id or not project_library.visible(db, task_id, [result]):
        raise ValueError("record does not belong to this project")
    return result


def _input(db, task_id, rid):
    from asset_service import sha256_file
    from paths import CACHE

    staged = db.db.execute("SELECT path,sha256 FROM zotero_downloads WHERE task_id=? AND result_id=?", (task_id, rid)).fetchone()
    paper = db.db.execute("""SELECT p.id,p.source_path FROM project_paper_links l JOIN papers p ON p.id=l.paper_id
        WHERE l.task_id=? AND l.result_id=? AND p.task_id=l.task_id""", (task_id, rid)).fetchone()
    queued = db.db.execute("SELECT path FROM dl_queue WHERE task_id=? AND result_id=? AND status='downloaded'", (task_id, rid)).fetchone()
    raw = staged['path'] if staged else (paper['source_path'] if paper else '') or (queued['path'] if queued else '')
    if not raw:
        return None
    path = Path(raw).resolve()
    root = (CACHE / 'zotero-downloads') if staged else db.base_dir(task_id)
    if staged and not path.exists() and paper and db.db.execute(
            'SELECT 1 FROM paper_zotero_refs WHERE paper_id=? AND task_id=?', (paper['id'], task_id)).fetchone():
        return None
    if not root or not path.is_relative_to(Path(root).resolve()) or path.suffix.lower() != '.pdf' or not path.is_file():
        raise ValueError('PDF 不存在或不在当前项目的登记目录中')
    digest = sha256_file(path)
    if staged and digest != staged['sha256']:
        raise ValueError('暂存 PDF 已变化，请重新下载')
    return {'path': str(path), 'sha256': digest, 'temporary': bool(staged), 'paper_id': paper['id'] if paper else ''}


def list_staged(db: Any, task_id: str) -> dict[str, Any]:
    _task(db, task_id)
    rows = []
    for item in db.queue(task_id):
        if item.get("status") != "downloaded":
            continue
        if item.get("zotero_status") in {"external", "imported"}:
            continue
        rows.append(item)
    return {"task_id": task_id, "items": rows}


def download(db, task_id, result_ids):
    from asset_service import sha256_file
    from paths import CACHE
    from workflow_stages import download_pdf
    import zotero_sync as sync

    if not isinstance(result_ids, list) or not result_ids or any(not isinstance(rid, str) or not rid for rid in result_ids):
        raise ValueError('请选择至少一篇项目文献')
    with sync._sync_lock:
        rows = [(rid, _row(db, task_id, rid)) for rid in dict.fromkeys(result_ids)]
        staged, failed = [], []
        for rid, row in rows:
            try:
                if _input(db, task_id, rid):
                    staged.append(rid)
                    continue
                root = (CACHE / 'zotero-downloads').resolve()
                folder = root / uuid.uuid4().hex
                path = download_pdf(row, target_dir=folder).resolve()
                if not path.is_relative_to(folder) or path.suffix.lower() != '.pdf':
                    raise ValueError('下载文件不在登记的暂存目录')
                with db.db:
                    db.db.execute('INSERT INTO zotero_downloads VALUES (?,?,?,?)', (task_id, rid, str(path), sha256_file(path)))
                db.set_queue(task_id, rid, 'downloaded')
                staged.append(rid)
            except Exception as exc:
                failed.append({'result_id': rid, 'error': str(exc)})
        return {'result_ids': staged, 'failed': failed}


def _doi(value: Any) -> str:
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or "").strip(), flags=re.I).casefold().rstrip(".,;)]}'\"")


def _authors(value: Any) -> list[dict[str, str]]:
    names = [part.strip() for part in re.split(r"[;；\n]+", str(value or "")) if part.strip()]
    return [{"creatorType": "author", "name": name} for name in names]


def _payload(row: dict[str, Any], collection: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "itemType": "journalArticle",
        "title": str(row.get("title") or "").strip(),
        "creators": _authors(row.get("authors")),
        "date": str(row.get("year") or "").strip(),
        "DOI": str(row.get("doi") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "abstractNote": str(row.get("abstract") or "").strip(),
    }
    if collection:
        payload["collections"] = [collection]
    return payload


def _match(row: dict[str, Any]) -> dict[str, Any]:
    doi = _doi(row.get("doi"))
    title = str(row.get("title") or "").strip().casefold()
    query = doi or title
    if not query:
        return {"kind": "create", "key": "", "candidates": []}
    found = []
    start = 0
    while True:
        page = zotero.items(q=query, start=start, limit=100)
        items = page.get('items', [])
        found.extend(item for item in items if item.get('type') not in {'attachment', 'annotation', 'note'})
        if not items or not page.get('hasMore'):
            break
        start += page.get('returned') or len(items)
    doi_matches = [item for item in found if doi and _doi(item.get("doi")) == doi]
    if len(doi_matches) == 1:
        return {"kind": "match", "key": doi_matches[0]["key"], "candidates": doi_matches}
    if len(doi_matches) > 1:
        return {"kind": "conflict", "key": "", "candidates": doi_matches}
    title_matches = [item for item in found if not doi and title and str(item.get("title") or "").strip().casefold() == title]
    if len(title_matches) == 1:
        return {"kind": "match", "key": title_matches[0]["key"], "candidates": title_matches}
    if len(title_matches) > 1:
        return {"kind": "conflict", "key": "", "candidates": title_matches}
    return {"kind": "create", "key": "", "candidates": []}


def _target(db, task_id, collection):
    import zotero_sync as sync
    cfg = sync.config(db)
    if cfg.get('transport') != 'local':
        raise ValueError('请切换到本机 Zotero 保存项目文献')
    library = sync.library(cfg)
    bound = sync.binding(db, task_id)
    if bound and bound['library'] != library:
        raise ValueError('项目绑定的 Zotero 库已变更，请核对连接')
    collection = collection or bound.get('collection', '')
    if not collection:
        raise ValueError('请选择要保存到的 Zotero 分类')
    collection = zotero._key(collection)
    target = sync.request(cfg, '/collections/' + collection)
    return cfg, library, collection, (target.get('data') or {}).get('name') or collection


def plan(db: Any, task_id: str, result_ids: list[str] | None = None,
         collection: str = '', attachment_mode: str = 'imported_file', matches: dict | None = None,
         *, allow_apply: bool = True) -> dict:
    import zotero_sync as sync
    _task(db, task_id)
    if not isinstance(result_ids, list) or not result_ids or any(not isinstance(rid, str) or not rid for rid in result_ids):
        raise ValueError('请选择至少一篇项目文献')
    if attachment_mode not in {'imported_file', 'plugin'}:
        raise ValueError('不支持该 PDF 附件模式')
    if matches is not None and not isinstance(matches, dict):
        raise ValueError('Zotero 条目匹配必须按文献 ID 指定')
    with sync._sync_lock:
        _, library, collection, name = _target(db, task_id, collection)
        actions, conflicts = [], []
        for rid in dict.fromkeys(result_ids):
            row = _row(db, task_id, rid)
            mapped = db.db.execute('SELECT item_key FROM zotero_item_links WHERE task_id=? AND result_id=?', (task_id, rid)).fetchone()
            pending = db.db.execute('SELECT item_key FROM zotero_pending_writes WHERE task_id=? AND result_id=? AND library=?',
                                    (task_id, rid, library)).fetchone()
            key = (mapped['item_key'] if mapped else '') or (pending['item_key'] if pending else '')
            match = {'kind': 'match', 'key': key, 'candidates': []} if key else _match(row)
            choice = (matches or {}).get(rid)
            if choice and choice in {item['key'] for item in match['candidates']}:
                match = {**match, 'kind': 'match', 'key': choice}
            if match['kind'] == 'conflict' or (pending and not match['key']):
                conflicts.append({'result_id': rid, 'title': row['title'], 'reason': '请核对 Zotero 中的对应条目',
                                  'candidates': match['candidates']})
                continue
            key = match['key']
            remote = zotero.item(key) if key else {}
            pdf = _input(db, task_id, rid)
            actions.append({'kind': match['kind'], 'result_id': rid, 'key': key, 'title': row['title'],
                            'version': remote.get('version', 0), 'add_collection': collection not in remote.get('collections', []),
                            'pdf': pdf, 'payload': _payload(row, collection)})
        result = {'task_id': task_id, 'library': library, 'collection': collection, 'collection_name': name,
                  'actions': [{**{k: v for k, v in action.items() if k not in {'pdf', 'payload'}}, 'pdf': bool(action['pdf'])}
                              for action in actions], 'conflicts': conflicts,
                  'expires_in': PLAN_TTL, 'attachment_mode': 'imported_file'}
        if not allow_apply:
            return result
        token = uuid.uuid4().hex
        result['plan_token'] = token
        with db.db:
            db.db.execute('DELETE FROM zotero_save_plans WHERE expires < ?', (time.time(),))
            db.db.execute('INSERT INTO zotero_save_plans VALUES (?,?,?,?)',
                          (token, task_id, time.time() + PLAN_TTL, json.dumps({**result, 'actions': actions}, ensure_ascii=False)))
        return result


def apply(db: Any, task_id: str, plan_token: str, confirm: bool = False) -> dict[str, Any]:
    import zotero_sync as sync
    import project_library
    import project_sources
    from asset_service import sha256_file

    if confirm is not True:
        raise ValueError('Zotero import requires explicit confirmation')
    with sync._sync_lock:
        saved = db.db.execute('SELECT data FROM zotero_save_plans WHERE token=? AND task_id=? AND expires>=?',
                              (plan_token, task_id, time.time())).fetchone()
        if not saved:
            raise ValueError('预览已过期或不属于当前项目，请重新预览')
        batch = json.loads(saved['data'])
        if batch['conflicts'] or not batch['actions']:
            raise ValueError('请先解决导入冲突并重新预览')
        cfg, library, collection, _ = _target(db, task_id, batch['collection'])
        if library != batch['library']:
            raise ValueError('Zotero 文献库已变更，请重新预览')
        with db.db:
            db.db.execute('DELETE FROM zotero_save_plans WHERE token=?', (plan_token,))
        applied, failed = [], []
        for action in batch['actions']:
            rid, key = action['result_id'], action['key']
            try:
                row = _row(db, task_id, rid)
                pdf = _input(db, task_id, rid)
                if action['payload'] != _payload(row, collection) or pdf != action['pdf']:
                    raise ValueError('题录或 PDF 已变化，请重新预览')
                if not key:
                    if (_match(row)['kind'] != 'create' or db.db.execute(
                            'SELECT 1 FROM zotero_pending_writes WHERE task_id=? AND result_id=? AND library=?',
                            (task_id, rid, library)).fetchone()):
                        raise ValueError('Zotero 候选条目已变化，请重新预览')
                    with db.db:
                        db.db.execute('INSERT INTO zotero_pending_writes VALUES (?,?,?,?)', (task_id, rid, library, ''))
                    key = zotero.add_item(db, action['payload'])['key']
                    with db.db:
                        db.db.execute('UPDATE zotero_pending_writes SET item_key=? WHERE task_id=? AND result_id=? AND library=?',
                                      (key, task_id, rid, library))
                else:
                    remote = zotero.item(key)
                    if remote.get('version', 0) != action['version']:
                        raise ValueError('Zotero 条目已变化，请重新预览')
                    if collection not in remote.get('collections', []):
                        sync.request(cfg, '/items/' + key, 'PATCH', {'collections': [*remote.get('collections', []), collection], 'version': action['version']})
                remote = zotero.item(key)
                if collection not in remote.get('collections', []):
                    raise ValueError('未能核对 Zotero 分类，请刷新后重试')
                attachment_key = ''
                if pdf:
                    attachment = zotero.import_pdf(db, key, pdf['path'], row['title'] or 'PDF')
                    attachment_key = attachment['key']
                    if pdf['temporary']:
                        managed = zotero.pdf_path(key, attachment_key=attachment_key)
                        if not managed or Path(managed).samefile(pdf['path']) or sha256_file(Path(managed)) != pdf['sha256']:
                            raise ValueError('Zotero 附件尚未独立保存或本机不可读，保留暂存文件')
                        reference = project_sources.reference_zotero_pdf(db, task_id, key, attachment_key)
                        project_library.link_pdf(db, task_id, rid, reference['paper_id'])
                with db.db:
                    if not sync.binding(db, task_id):
                        db.db.execute('INSERT OR IGNORE INTO zotero_bindings VALUES (?,?,?,?)', (task_id, library, '', ''))
                sync.record_membership(db, task_id, library, rid, key, remote.get('version', 0))
                with db.db:
                    db.db.execute('DELETE FROM zotero_pending_writes WHERE task_id=? AND result_id=? AND library=?',
                                  (task_id, rid, library))
                if pdf and pdf['temporary']:
                    db.set_queue(task_id, rid, 'downloaded')
                db.set_zotero_stage(task_id, rid, 'imported', zotero_key=key, attachment_key=attachment_key)
                if pdf and pdf['temporary']:
                    source = Path(pdf['path'])
                    if source.resolve() != source or sha256_file(source) != pdf['sha256']:
                        raise ValueError('暂存文件已变化，未清理')
                    source.unlink()
                if not pdf or pdf['temporary']:
                    with db.db:
                        db.db.execute('DELETE FROM zotero_downloads WHERE task_id=? AND result_id=?', (task_id, rid))
                applied.append({'result_id': rid, 'key': key, 'attachment_key': attachment_key})
            except Exception as exc:
                db.set_zotero_stage(task_id, rid, 'failed', zotero_key=key, error=str(exc)[:1000])
                failed.append({'result_id': rid, 'key': key, 'error': str(exc)})
        return {'ok': not failed, 'partial': bool(applied and failed), 'applied': applied, 'failed': failed}


def route(method: str, path: str, body: dict, query: dict, db: Any) -> dict | None:
    if not path.startswith("/api/zotero/web/staged"):
        return None
    task_id = str(body.get("task_id") or (query.get("task_id") or [""])[0])
    if method == "GET" and path == "/api/zotero/web/staged":
        return list_staged(db, task_id)
    if method == 'POST' and path == '/api/zotero/web/staged/download':
        return download(db, task_id, body.get('result_ids'))
    if method == "POST" and path == "/api/zotero/web/staged/plan":
        return plan(db, task_id, body.get("result_ids"), str(body.get("collection") or ""),
                    str(body.get("attachment_mode") or "imported_file"), body.get('matches'))
    if method == "POST" and path == "/api/zotero/web/staged/apply":
        return apply(db, task_id, str(body.get("plan_token") or ""), body.get("confirm") is True)
    return None
