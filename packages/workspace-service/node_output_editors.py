"""Typed edits of frozen node results; source PDFs and search records stay intact."""
from __future__ import annotations

import json

import project_tasks as tasks

def describe(snapshot, node):
    if node.get('node_kind') == 'tool':
        return 'tool'
    if node.get('output_contract', {}).get('required_files'):
        return 'files'
    stage = node.get('step', node.get('stage', 'task'))
    return {'idea': 'idea', 'search': 'table', 'download': 'papers', 'notes': 'bilingual',
            'map': 'evidence_graph', 'entrans': 'bilingual', 'review': 'checklist',
            'convert': 'files'}.get(stage, 'text' if isinstance(snapshot.get('content'), str) else 'json')


def kind(node, content):
    return describe({'content': content}, node)


def _text(value, label, *, required=True):
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(label + '必须是有效且非空的文本')
    return value


def search_editor_rows(output):
    return output.get('review_annotations', output.get('content'))


def reading_note_editable(row):
    return isinstance(row, dict) and (bool(row.get('reading_notes')) or isinstance(row.get('translation'), str))


def _reading_notes(content, baseline):
    if not isinstance(content, list) or not isinstance(baseline, list) or not content or len(content) != len(baseline):
        raise ValueError('精读修订须保留本次运行的全部文献')
    for row, original in zip(content, baseline):
        if not isinstance(row, dict) or not reading_note_editable(original):
            raise ValueError('当前版本没有可修订的精读笔记')
        if not original.get('reading_notes'):
            _text(row.get('translation'), '精读笔记', required=False)
            if tasks._digest({**row, 'translation': ''}) != tasks._digest({**original, 'translation': ''}):
                raise ValueError('文献与 PDF 来源为只读')
            continue
        notes = row.get('reading_notes')
        if not isinstance(notes, list) or len(notes) != len(original['reading_notes']):
            raise ValueError('精读修订须保留本次运行的全部笔记')
        for note in notes:
            if not isinstance(note, dict):
                raise ValueError('精读笔记格式无效')
            _text(note.get('markdown'), '精读笔记', required=False)

        metadata = {**row, 'reading_notes': [{**note, 'markdown': ''} for note in notes]}
        expected = {**original, 'reading_notes': [{**note, 'markdown': ''} for note in original['reading_notes']]}
        if tasks._digest(metadata) != tasks._digest(expected):
            raise ValueError('文献、PDF 来源、页码映射及笔记标识均为只读')
    return content


def _readonly_fields(record, editable):
    return {key: value for key, value in record.items() if key not in editable}


def _translation(content, baseline):
    if not isinstance(content, dict) or not isinstance(baseline, dict):
        raise ValueError('英译修订必须保留原文和译文结构')
    _text(content.get('result'), '译文')
    if _readonly_fields(content, {'result', 'segments'}) != _readonly_fields(baseline, {'result', 'segments'}):
        raise ValueError('原文及来源字段为只读')
    original = baseline.get('segments')
    segments = content.get('segments')
    if not original:
        if ('segments' in content) != ('segments' in baseline) or segments != original:
            raise ValueError('不能为旧版译文添加未经验证的段落映射')
        return
    _segments(segments)
    if not isinstance(original, list) or len(segments) != len(original):
        raise ValueError('译文修订必须保留原有段落顺序和映射')

    translations = []
    for segment, prior in zip(segments, original):
        if not isinstance(prior, dict):
            raise ValueError('原译文缺少有效的段落映射')
        field = 'target' if 'target' in prior else 'translation'
        if _readonly_fields(segment, {field}) != _readonly_fields(prior, {field}):
            raise ValueError('原文段落及映射为只读')
        translations.append(_text(segment.get(field), '译文段落'))
    expected = '\n\n'.join(translations) if segments != original else baseline.get('result')
    if content['result'] != expected:
        raise ValueError('译文正文必须与段落译文一致')


def _review_issues(content, baseline):
    if not isinstance(content, dict):
        raise ValueError('审阅修订必须保留原有问题清单')
    _text(content.get('summary', ''), '审阅摘要', required=False)
    key = 'issues' if 'issues' in baseline else 'checklist'
    issues, originals = content.get(key), baseline.get(key)
    if not isinstance(issues, list) or not isinstance(originals, list) or len(issues) != len(originals) or len(issues) > 1000:
        raise ValueError('审阅修订必须保留原有问题清单')
    if _readonly_fields(content, {'summary', key}) != _readonly_fields(baseline, {'summary', key}):
        raise ValueError('审阅来源字段为只读')
    for issue, prior in zip(issues, originals):
        if not isinstance(issue, dict) or not isinstance(prior, dict):
            raise ValueError('审阅问题必须是对象')
        detail = next((field for field in ('detail', 'problem', 'text', 'title') if isinstance(prior.get(field), str)), 'detail')
        editable = {detail, 'comment', 'resolved'}
        if _readonly_fields(issue, editable) != _readonly_fields(prior, editable):
            raise ValueError('问题 ID、原稿位置和来源字段为只读')
        _text(issue.get(detail), '问题说明')
        _text(issue.get('comment', ''), '处理意见', required=False)
        if 'resolved' in issue and not isinstance(issue['resolved'], bool):
            raise ValueError('问题处理状态必须是布尔值')


def parse_content(stage, content, baseline, *, keep_excluded=False):
    try:
        encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('产物必须是有效的 JSON 内容') from exc
    content = json.loads(encoded)
    if stage == 'idea':
        if not isinstance(content, dict) or not isinstance(content.get('report'), dict):
            raise ValueError('创新点审查需包含 input 和 report')
        _text(content.get('input'), '研究问题')
        _text(content['report'].get('markdown'), '审查报告')
    elif stage == 'notes':
        content = _reading_notes(content, baseline)
    elif stage in ('search', 'download'):
        if not isinstance(content, list) or not content or len(content) > 1000 or not isinstance(baseline, list):
            raise ValueError('请提供非空的原始结果条目列表')
        sources = {str(row['id']): row for row in baseline if isinstance(row, dict) and row.get('id')}
        seen, reviewed = set(), []
        for row in content:
            if not isinstance(row, dict) or str(row.get('id', '')) not in sources or str(row['id']) in seen:
                raise ValueError('修订条目必须引用本次产物中唯一的记录 ID')
            seen.add(str(row['id']))
            original = sources[str(row['id'])]
            if 'included' in row and not isinstance(row['included'], bool):
                raise ValueError('纳入或排除必须使用布尔值')
            editable_fields = {'included', 'reason', 'annotation'}
            if any(key not in editable_fields and (key not in original or value != original[key])
                   for key, value in row.items()):
                raise ValueError('检索题录为只读，可调整纳入状态、顺序、理由和批注')
            for key in ('annotation', 'reason', 'source', 'translation'):
                if key in row:
                    _text(row[key], key, required=False)
            if 'segments' in row:
                _segments(row['segments'])
            reviewed.append({**original, **row, 'included': row.get('included', True)})
        selected = [row for row in reviewed if row['included']]
        if not selected:
            raise ValueError('至少保留一条有效产物，全部排除时请退回节点重做')
        content = reviewed if stage == 'search' and keep_excluded else selected
    elif stage == 'entrans':
        _translation(content, baseline)
    elif stage == 'review':
        if isinstance(baseline, dict):
            _review_issues(content, baseline)
        else:
            _text(content, '审阅报告')
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
