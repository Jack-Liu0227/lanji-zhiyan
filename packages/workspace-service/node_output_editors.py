"""Typed edits of frozen node results; source PDFs and search records stay intact."""
from __future__ import annotations

import json
import math

import project_tasks as tasks

MAX_STRUCTURED_BYTES = 8 * 1024 * 1024


def describe(snapshot, node):
    if node.get('node_kind') == 'tool':
        return 'tool'
    stage = node.get('step', node.get('stage', 'task'))
    return {'idea': 'idea', 'search': 'table', 'download': 'papers', 'notes': 'bilingual',
            'map': 'evidence_graph', 'entrans': 'bilingual', 'review': 'checklist',
            'convert': 'files'}.get(stage, 'text' if isinstance(snapshot.get('content'), str) else 'json')


def kind(node, content):
    return describe({'content': content}, node)


def _text(value, label, *, required=True, limit=2 * 1024 * 1024):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit or (required and not value.strip()):
        raise ValueError(label + '必须是有效且非空的文本')
    return value


def parse_content(stage, content, baseline):
    try:
        encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('产物必须是有效的 JSON 内容') from exc
    if len(encoded.encode('utf-8')) > MAX_STRUCTURED_BYTES:
        raise ValueError('结构化修订不能超过 8MB')
    content = json.loads(encoded)
    if stage == 'idea':
        if not isinstance(content, dict) or not isinstance(content.get('report'), dict):
            raise ValueError('创新点审查需包含 input 和 report')
        _text(content.get('input'), '研究问题')
        _text(content['report'].get('markdown'), '审查报告')
    elif stage in ('search', 'download', 'notes'):
        if not isinstance(content, list) or not content or len(content) > 1000 or not isinstance(baseline, list):
            raise ValueError('请提供非空的原始结果条目列表')
        sources = {str(row['id']): row for row in baseline if isinstance(row, dict) and row.get('id')}
        seen, selected = set(), []
        for row in content:
            if not isinstance(row, dict) or str(row.get('id', '')) not in sources or str(row['id']) in seen:
                raise ValueError('修订条目必须引用本次产物中唯一的记录 ID')
            seen.add(str(row['id']))
            original = sources[str(row['id'])]
            if 'included' in row and not isinstance(row['included'], bool):
                raise ValueError('纳入或排除必须使用布尔值')
            if stage == 'search':
                _text(row.get('title'), '文献标题', limit=5000)
                for key in ('source', 'created_at', 'task_id'):
                    if key in original and row.get(key, original[key]) != original[key]:
                        raise ValueError('不能改写原始检索来源与项目归属')
                if 'score' in row and (isinstance(row['score'], bool) or not isinstance(row['score'], (int, float)) or not math.isfinite(row['score'])):
                    raise ValueError('评分必须是有限数值')
            else:
                for key in ('sha256', 'files', 'source_path'):
                    if key in original and row.get(key, original[key]) != original[key]:
                        raise ValueError('PDF 原件、文件路径和摘要不可改写')
            for key in ('annotation', 'reason', 'source', 'translation'):
                if key in row:
                    _text(row[key], key, required=False)
            if 'segments' in row:
                _segments(row['segments'])
            if row.get('included', True):
                selected.append({**original, **row, 'included': True})
        if not selected:
            raise ValueError('至少保留一条有效产物，全部排除时请退回节点重做')
        content = selected
    elif stage == 'entrans':
        if not isinstance(content, dict):
            raise ValueError('英译修订需包含 source 和 result')
        _text(content.get('source', ''), '原文', required=False)
        _text(content.get('result'), '译文')
        if 'segments' in content:
            _segments(content['segments'])
    elif stage == 'review' and isinstance(content, dict):
        _text(content.get('summary', ''), '审查摘要', required=False)
        issues = content.get('issues', content.get('checklist'))
        if not isinstance(issues, list) or not issues or len(issues) > 1000:
            raise ValueError('审查清单需包含有效问题条目')
        ids = set()
        for issue in issues:
            if not isinstance(issue, dict):
                raise ValueError('审查问题必须是对象')
            detail = issue.get('detail', issue.get('text', issue.get('title')))
            _text(detail, '问题说明')
            if issue.get('severity', 'normal') not in ('critical', 'major', 'minor', 'normal', 'high', 'medium', 'low'):
                raise ValueError('问题严重程度无效')
            if 'resolved' in issue and not isinstance(issue['resolved'], bool):
                raise ValueError('问题处理状态必须是布尔值')
            if issue.get('id'):
                if not isinstance(issue['id'], str) or len(issue['id']) > 200:
                    raise ValueError('审查问题 ID 必须是有效文本')
                if issue['id'] in ids:
                    raise ValueError('审查问题 ID 不能重复')
                ids.add(issue['id'])
    elif stage == 'convert':
        raise ValueError('导出文件应修改生成参数后重新执行，不能改写二进制产物')
    elif content in (None, '', [], {}):
        raise ValueError('修订产物不能为空')
    return content


def _segments(segments):
    if not isinstance(segments, list) or len(segments) > 5000:
        raise ValueError('双语对照必须是段落列表')
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('双语段落必须是对象')
        _text(segment.get('source', ''), '原文段落', required=False)
        _text(segment.get('target', segment.get('translation', '')), '译文段落')


def _setting(db, project_id, stage):
    if not db.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_settings'").fetchone():
        return None
    return db.db.execute('SELECT * FROM service_settings WHERE service=? AND owner=?',
                         ('node-review:' + stage, project_id)).fetchone()


def effective_content(db, project_id, stage, payload):
    if getattr(db, '_node_review_raw_capture', False):
        return payload
    row = _setting(db, project_id, stage)
    if row:
        saved = json.loads(row['data'])
        if saved.get('base_digest') == tasks._digest(payload):
            return saved['content']
    return payload


def overlay_change(db, project_id, stage, baseline, content):
    import service_settings
    import workflow_state
    service_settings.ensure(db)
    row = _setting(db, project_id, stage)
    before = dict(row) if row else None
    previous = getattr(db, '_node_review_raw_capture', False)
    db._node_review_raw_capture = True
    try:
        raw = workflow_state.capture(db, project_id, stage)['content']
    finally:
        db._node_review_raw_capture = previous
    base = tasks._digest(raw)
    key = {'service': 'node-review:' + stage, 'owner': project_id}
    return {'table': 'service_settings', 'key': key, 'before': before,
            'after': {**key, 'data': tasks._json({'base_digest': base, 'content': content})}}
