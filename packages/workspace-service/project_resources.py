"""Typed views over existing project files, bibliography and canonical assets."""
from pathlib import Path
import json
import os
import uuid

import asset_service
import note_catalog
import project_library
import project_notes

KINDS = ('literature', 'note', 'manuscript', 'experiment', 'output', 'file')
TEXT = {'.md', '.markdown', '.txt', '.csv', '.json', '.tex', '.py', '.ipynb'}


def category(path, kind='', metadata=None):
    metadata = metadata or {}
    parts = Path(path).parts
    if kind == 'paper': return 'literature'
    if metadata.get('category_id') == 'experiment_analysis' or any(p in ('实验', '实验记录', '实验数据') for p in parts): return 'experiment'
    if '论文正文' in parts or kind == 'manuscript': return 'manuscript'
    if kind == 'note' or '笔记' in parts: return 'note'
    if kind in ('report', 'output') or any(p in ('阶段产物', '定时任务', '.tasks') for p in parts): return 'output'
    return 'file'


def catalog(db, project_id, kind='', query=''):
    root = project_notes.project_root(db, project_id)
    if kind and kind not in KINDS: raise ValueError('资料类型无效')
    rows = {}
    for row in db.db.execute('SELECT * FROM assets WHERE project_id=? ORDER BY updated_at DESC', (project_id,)):
        asset = dict(row)
        if asset['status'] != 'active' or asset['kind'] == 'evidence': continue
        metadata = json.loads(asset.get('metadata') or '{}')
        path = Path(asset['canonical_uri']) if asset['storage'] == 'file' else None
        if path and path.name.lower() == 'readme.md': continue
        relative = path.relative_to(root).as_posix() if path and path.is_relative_to(root) else ''
        item = {'id': asset['id'], 'asset_id': asset['id'], 'title': asset['title'],
                'kind': category(str(path or ''), asset['kind'], metadata),
                'path': str(path or ''), 'relative': relative, 'paper_id': metadata.get('paper_id', ''),
                'updated_at': asset['updated_at'], 'content_hash': asset['content_hash'],
                'revision_id': asset['current_revision_id'], 'source': asset['source'],
                'metadata': metadata, 'exists': path.is_file() if path else False,
                'read_only': relative.startswith('.tasks/') or bool(path and not relative)}
        rows[str(path) if path else asset['canonical_uri']] = item
    # Disk Markdown is visible even before the user explicitly rebuilds the index.
    for note in project_notes.dashboard(db, project_id)['items']:
        if Path(note['path']).name.lower() == 'readme.md': continue
        path = root / note['path']
        key = str(path)
        previous = rows.get(key, {})
        rows[key] = {**previous, 'id': previous.get('id', 'file:' + note['path']),
                     'title': note['title'], 'path': key, 'relative': note['path'],
                     'kind': category(key, 'note', note), 'metadata': {**previous.get('metadata', {}), **note},
                     'exists': True}
    for paper in project_library.items(db, project_id):
        key = str(Path(paper['source_path']).resolve()) if paper.get('source_path') else 'bibliography:' + str(paper.get('result_id') or paper.get('id'))
        previous = rows.get(key, {})
        rows[key] = {**previous, 'id': previous.get('id', key), 'title': paper.get('title') or paper.get('filename'),
                     'kind': 'literature', 'paper_id': paper.get('paper_id', ''),
                     'path': paper.get('source_path', ''), 'exists': paper.get('has_pdf', False),
                     'metadata': {**previous.get('metadata', {}), 'doi': paper.get('doi', ''), 'result_id': paper.get('result_id', '')}}
    physical_papers = {item.get('paper_id') for item in rows.values() if item.get('path') and item.get('paper_id')}
    values = [item for item in rows.values() if not (item['kind'] == 'literature' and not item.get('path') and item.get('paper_id') in physical_papers)]
    counts = {key: sum(item['kind'] == key for item in values) for key in KINDS}
    needle = query.casefold().strip()
    values = [item for item in values if (not kind or item['kind'] == kind) and
              (not needle or needle in (str(item.get('title', '')) + ' ' + item.get('relative', '') + ' ' + json.dumps(item.get('metadata', {}), ensure_ascii=False)).casefold())]
    values.sort(key=lambda item: (str(item.get('updated_at', '')), item.get('title') or ''), reverse=True)
    return {'project_id': project_id, 'items': values, 'counts': counts, 'total': len(values)}


def index(db, project_id):
    """Explicit bounded index refresh; never relocate, rewrite or delete content."""
    root = project_notes.project_root(db, project_id)
    paper_paths = {str(Path(row['source_path']).resolve()) for row in db.papers_of(project_id) if row.get('source_path')}
    for other in db.db.execute("SELECT canonical_uri FROM assets WHERE storage='file' AND project_id<>? AND project_id<>'' AND status='active'", (project_id,)):
        path = Path(other['canonical_uri'])
        if path.is_relative_to(root) or str(path) in paper_paths:
            raise ValueError('资料路径已归属其他项目，请为当前项目使用独立目录或导入副本')
    asset_service.rebuild_project_index(db, project_id, include_files=True)
    count = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not name.startswith('.') and name not in ('node_modules', '__pycache__') and not (Path(folder) / name).is_symlink()]
        for name in files:
            if name.lower() == 'readme.md': continue
            path = Path(folder) / name
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file(): continue
            if path.suffix.lower() not in TEXT | {'.pdf', '.docx', '.xlsx', '.png', '.jpg'}: continue
            existing = db.db.execute('SELECT * FROM assets WHERE canonical_uri=?', (str(path.resolve()),)).fetchone()
            metadata = json.loads(existing['metadata'] or '{}') if existing else {}
            body = path.read_text(encoding='utf-8', errors='replace') if path.suffix.lower() in TEXT and path.stat().st_size <= project_notes.MAX_NOTE_BYTES else ''
            file_kind = existing['kind'] if existing else ('note' if '笔记' in path.relative_to(root).parts else 'document')
            asset_service.register_file(db, path, project_id=project_id, kind=file_kind,
                title=existing['title'] if existing else path.stem, source=existing['source'] if existing else 'project_index',
                metadata=metadata, body=body)
            count += 1
            if count >= 10000: return {'project_id': project_id, 'indexed': count, 'truncated': True}
    return {'project_id': project_id, 'indexed': count, 'truncated': False}


def read(db, project_id, asset_id):
    root = project_notes.project_root(db, project_id)
    asset = asset_service.get(db, asset_id)
    if not asset or asset['project_id'] != project_id: raise PermissionError('资料不属于当前项目')
    path = Path(asset['canonical_uri'])
    if asset['storage'] != 'file' or path.is_symlink() or not path.resolve().is_relative_to(root): raise PermissionError('请先将资料作为项目副本导入')
    if path.suffix.lower() not in TEXT: raise ValueError('该类型请使用 PDF 阅读或文件预览工具')
    if path.stat().st_size > project_notes.MAX_NOTE_BYTES: raise ValueError('文本超过 5 MB')
    return {'asset_id': asset_id, 'path': path.relative_to(root).as_posix(), 'title': asset['title'],
            'content': path.read_text(encoding='utf-8'), 'content_hash': asset_service.sha256_file(path)}


@project_notes.synchronized
def write(db, project_id, args):
    kind = str(args.get('kind') or 'note')
    if kind not in ('note', 'experiment', 'manuscript'): raise ValueError('可写类型为笔记、实验记录或论文稿件')
    content = str(args.get('content') or '')
    if len(content.encode('utf-8')) > project_notes.MAX_NOTE_BYTES: raise ValueError('内容超过 5 MB')
    root = project_notes.project_root(db, project_id, writable=True)
    asset_id = str(args.get('asset_id') or '')
    asset = asset_service.get(db, asset_id) if asset_id else None
    if asset_id and (not asset or asset['project_id'] != project_id): raise PermissionError('资料不属于当前项目')
    if asset:
        path = Path(asset['canonical_uri'])
        if asset['storage'] != 'file' or path.is_symlink() or not path.resolve().is_relative_to(root): raise PermissionError('资料不在项目目录内')
        if path.suffix.lower() not in ('.md', '.markdown'): raise ValueError('仅支持编辑 Markdown 资料')
        if args.get('expected_hash') is None: raise ValueError('更新资料需要 expected_hash，先读取当前内容')
    else:
        folder = '论文正文' if kind == 'manuscript' else '笔记/实验分析' if kind == 'experiment' else '笔记/研究规划'
        path = root / folder / (uuid.uuid4().hex + '.md')
    title = str(args.get('title') or (asset['title'] if asset else '') or '未命名资料').strip()
    relative = path.relative_to(root).as_posix()
    if not relative.startswith(('笔记/', '论文正文/')): raise PermissionError('只能编辑笔记与稿件，任务交付快照需要通过核查修订')
    if relative.startswith('笔记/'):
        metadata = {'category_id': 'experiment_analysis'} if kind == 'experiment' else None
        return project_notes.write(db, project_id, relative, content, title=title, create=not asset,
                                   expected_hash=args.get('expected_hash'), metadata=metadata)
    if asset and asset_service.sha256_file(path) != args['expected_hash']: raise ValueError('资料已更新，请重新读取后合并')
    import fileguard
    fileguard.before_write(path, '项目稿件保存')
    asset_service.atomic_write_text(path, content)
    saved = asset_service.register_file(db, path, project_id=project_id, kind='manuscript', title=title, body=content, source='project_resource')
    return {'asset_id': saved['id'], 'path': relative, 'content_hash': saved['content_hash'], 'revision_id': saved['current_revision_id']}
