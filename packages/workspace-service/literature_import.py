"""Import RSS evidence and manually entered references into the shared bibliography."""
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit

import project_library
import workflow_layout
import zotero_sync


def web_url(value):
    value = str(value or '').strip()
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password or any(c.isspace() for c in value)):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError('请提供有效的 http:// 或 https:// 原文链接') from None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ''))


def normalized_url(value):
    try:
        parts = urlsplit(web_url(value))
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return ''


def doi(value):
    value = unquote(str(value or '').strip())
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value, flags=re.I)
    return value.casefold() if re.fullmatch(r'10\.\d{4,9}/\S+', value, re.I) else ''


def from_feed(row):
    link = web_url(row['link'])
    match = re.search(r'10\.\d{4,9}/[^?#\s]+', unquote(urlsplit(link).path), re.I)
    year = re.search(r'\b(?:19|20)\d{2}\b', row.get('published_at') or '')
    return {'title': str(row['title'] or '').strip(), 'authors': row.get('authors') or '',
            'year': year.group() if year else '', 'doi': doi(match.group()) if match else '',
            'url': link, 'abstract': row.get('summary') or '',
            'source': 'RSS · ' + (row.get('source_title') or '情报日报')}


def existing(rows, item):
    item_doi = doi(item.get('doi'))
    link = normalized_url(item.get('url'))
    return next((row for row in rows if (item_doi and doi(row.get('doi')) == item_doi)
                 or (link and normalized_url(row.get('url')) == link)), None)


def removed_record(db, project_id, item):
    rows = [dict(row) for row in db.db.execute('SELECT * FROM search_cache WHERE task_id=?', (project_id,))]
    visible = {row['id'] for row in project_library.visible(db, project_id, rows)}
    return existing([row for row in rows if row['id'] not in visible], item)


def annotate(db, rows, project_id):
    if not project_id:
        return rows
    workflow_layout._project(db, project_id)
    records = project_library.records(db, project_id)
    links = {r['item_id']: r['result_id'] for r in db.db.execute(
        'SELECT item_id,result_id FROM rss_project_imports WHERE project_id=?', (project_id,))}
    visible_ids = {rid for record in records for rid in record['result_ids']}
    result = []
    for row in rows:
        rid = links.get(row['id'], '')
        if rid not in visible_ids:
            try:
                rid = (existing(records, from_feed(row)) or {}).get('result_id', '')
            except ValueError:
                rid = ''
        result.append({**row, 'imported_result_id': rid})
    return result


def import_rss(db, project_id, identifiers):
    workflow_layout._project(db, project_id)
    if (not isinstance(identifiers, list) or not identifiers or len(identifiers) > 200
            or any(not isinstance(i, str) for i in identifiers)):
        raise ValueError('请选择 1 至 200 条情报')
    identifiers = list(dict.fromkeys(identifiers))
    rows = []
    for identifier in identifiers:
        row = db.db.execute('SELECT * FROM rss_items WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('部分情报条目已不存在，请刷新后重新选择')
        rows.append(dict(row))
    results, failures = [], []
    with zotero_sync._sync_lock:
        records = project_library.records(db, project_id)
        visible_ids = {rid for record in records for rid in record['result_ids']}
        for row in rows:
            try:
                item = from_feed(row)
            except ValueError as exc:
                failures.append({'id': row['id'], 'title': row['title'], 'error': str(exc)})
                continue
            mapped = db.db.execute('SELECT result_id FROM rss_project_imports WHERE item_id=? AND project_id=?',
                                   (row['id'], project_id)).fetchone()
            rid = mapped['result_id'] if mapped and mapped['result_id'] in visible_ids else ''
            found = existing(records, item) if not rid else None
            rid = rid or (found or {}).get('result_id', '')
            already = bool(rid)
            if not rid:
                if removed_record(db, project_id, item):
                    failures.append({'id': row['id'], 'title': row['title'],
                                     'error': '此文献已移除项目关联，请先到项目文献的“恢复关联 / 操作记录”中恢复'})
                    continue
                # Source-specific cache matching is supplemented by DOI and URL matching above.
                rid = db.cache_result(project_id, item, match_title=False)
                if rid not in {r['result_id'] for r in records}:
                    records.append({**db.result(rid), 'result_id': rid})
                visible_ids.add(rid)
            project_library.index_record(db, project_id, rid)
            with db.db:
                db.db.execute('INSERT OR REPLACE INTO rss_project_imports VALUES (?,?,?,?)',
                              (row['id'], project_id, rid, datetime.now(timezone.utc).isoformat()))
            results.append({'id': row['id'], 'result_id': rid, 'existing': already})
    return {'project_id': project_id, 'imported': sum(not r['existing'] for r in results),
            'existing': sum(r['existing'] for r in results), 'items': results, 'failures': failures}


def import_reference(db, project_id, body):
    workflow_layout._project(db, project_id)
    item = {key: str(body.get(key) or '').strip() for key in ('title', 'authors', 'year', 'doi', 'url', 'abstract')}
    if not item['title'] or len(item['title']) > 2000:
        raise ValueError('请填写文献标题，最多 2000 字')
    if any(len(value) > 50000 for value in item.values()):
        raise ValueError('题录内容过长')
    if item['doi']:
        item['doi'] = doi(item['doi'])
        if not item['doi']:
            raise ValueError('DOI 格式应为 10.xxxx/xxxxx')
    item['url'] = web_url(item['url'] or ('https://doi.org/' + item['doi'] if item['doi'] else ''))
    item['source'] = '手动导入'
    with zotero_sync._sync_lock:
        found = existing(project_library.records(db, project_id), item)
        if not found and removed_record(db, project_id, item):
            raise ValueError('此文献已移除项目关联，请先在项目文献中恢复关联')
        rid = found['result_id'] if found else db.cache_result(project_id, item, match_title=False)
        project_library.index_record(db, project_id, rid)
    return {'project_id': project_id, 'result_id': rid, 'existing': bool(found)}


def route(method, path, body, query, db):
    if method == 'POST' and path == '/api/rss/import':
        return import_rss(db, str(body.get('project_id') or ''), body.get('ids'))
    if method == 'POST' and path == '/api/library/import':
        return import_reference(db, str(body.get('project_id') or ''), body)
    return None
