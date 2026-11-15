"""Editable export source and regenerated, versioned final documents."""
import json
from pathlib import Path
import shutil

import project_tasks as tasks

FORMATS = ('docx', 'pdf', 'html', 'md')


def source(db, task, run):
    output = run.get('output_snapshot') or {}
    if output.get('export_source'):
        return output['export_source']
    if task['metadata'].get('stage') != 'convert':
        return None
    files = [f for f in output.get('files', []) if f.get('name') != 'result.md']
    first = next((f for f in files if Path(f['path']).suffix.lower() in ('.pdf', '.docx', '.html', '.md')), None)
    fmt = Path(first['path']).suffix[1:].lower() if first else 'docx'
    name = Path(first.get('name') or first['path']).stem if first else 'paper'
    text = ''
    for signature in (run.get('checkpoint') or {}).get('done_tools', {}):
        tool, _, args = signature.partition('\0')
        if tool == 'stage_convert':
            try:
                text = json.loads(args).get('markdown') or text
            except (ValueError, AttributeError):
                pass
    for item in files:
        if not text and Path(item['path']).suffix.lower() in ('.md', '.markdown'):
            text = Path(item['path']).read_text(encoding='utf-8')
    if not text:
        for dependency in reversed(run.get('input_snapshot', {}).get('dependencies', [])):
            for item in reversed(dependency.get('outputs', [])):
                path = Path(item.get('path', ''))
                if path.suffix.lower() not in ('.md', '.json', '.txt') or not path.is_file():
                    continue
                value = path.read_text(encoding='utf-8')
                try:
                    parsed = json.loads(value)
                    value = parsed.get('result', '') if isinstance(parsed, dict) else ''
                except ValueError:
                    pass
                if value.strip():
                    text = value
                    break
            if text:
                break
    if not text and first:
        if fmt == 'docx':
            from docx import Document
            text = '\n\n'.join(p.text for p in Document(first['path']).paragraphs)
        elif fmt == 'pdf':
            import pdfio
            text = '\n\n'.join(pdfio.page_texts(first['path']))
    if not text.strip():
        return None
    return {'markdown': text, 'format': fmt, 'name': name}


def render(markdown, destination, bases):
    fmt = destination.suffix[1:].lower()
    if fmt == 'md':
        destination.write_text(markdown, encoding='utf-8')
    elif fmt == 'html':
        import html
        # A portable standalone preview with the same Markdown source retained.
        import re
        parts = []
        for line in markdown.splitlines():
            heading = re.match(r'^(#{1,6})\s+(.*)$', line)
            parts.append(f'<h{len(heading[1])}>{html.escape(heading[2])}</h{len(heading[1])}>' if heading
                         else '<p>' + html.escape(line) + '</p>')
        destination.write_text('<!doctype html><meta charset="utf-8"><title>Research document</title>'
            + '<style>body{max-width:52rem;margin:3rem auto;line-height:1.8;font-family:serif}</style>'
            + '\n'.join(parts), encoding='utf-8')
    elif fmt in ('docx', 'pdf'):
        import workflow_docx
        docx = destination if fmt == 'docx' else destination.with_suffix('.docx')
        workflow_docx.build(markdown, docx, bases=bases)
        if fmt == 'pdf':
            import workflow_converters
            rendered = workflow_converters.docx_to_pdf(docx)
            if not rendered:
                raise ValueError('本机未能完成 PDF 排版，请选择 Word 或 HTML 保存；安装 LibreOffice 后可重新生成 PDF。')
            shutil.copy2(rendered, destination)
    else:
        raise ValueError('请选择 Word、PDF、HTML 或 Markdown 格式')


def revise(db, task_id, body, *, authority):
    import asset_service
    import project_task_execution as worker
    import project_task_review as review
    import task_output_versions as versions
    with tasks._lock:
        task, run = review._current(db, task_id, body, authority)
        if task['metadata'].get('stage') != 'convert':
            raise ValueError('只有最终导出节点支持重新排版')
        value = body.get('content')
        if not isinstance(value, dict) or not isinstance(value.get('markdown'), str) or not value['markdown'].strip():
            raise ValueError('请填写排版源稿')
        if len(value['markdown'].encode('utf-8')) > 2 * 1024 * 1024 or value.get('format') not in FORMATS:
            raise ValueError('排版源稿或导出格式无效')
        revision = int(run['checkpoint'].get('output_revision', 0)) + 1
        folder = review._folder(db, task, run)
        target = folder / ('paper.' + value['format'])
        render(value['markdown'], target, [tasks._root(db, task['project_id'])])
        draft = asset_service.atomic_write_text(folder / 'source.md', value['markdown'])
        capture = {'files': [str(target), str(draft)], 'content': [str(target)]}
        output = worker._outputs(db, task, run, {}, capture, folder=folder / 'outputs')
        output['export_source'] = {'markdown': value['markdown'], 'format': value['format'], 'name': 'paper'}
        tasks._validate_contract(task['output_contract'], output)
        checkpoint = {**run['checkpoint'], 'output': capture, 'output_revision': revision}
        versions.remember(db, task, run)
        operation = versions.begin(db, task, run, revision, [])
        try:
            return review._commit(db, task, run, output, checkpoint, body, authority, operation, 0, 'export')
        except Exception as exc:
            versions.rollback(db, task, operation, str(exc))
            raise
