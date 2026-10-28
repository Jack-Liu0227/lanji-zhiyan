"""Human edits create new output versions while preserving the model's original files."""
import json
from pathlib import Path
import uuid

import project_tasks as tasks

TEXT={'.md','.markdown','.txt','.json','.yaml','.yml','.csv','.tex','.bib','.py','.js','.css','.html'}
DOCUMENT_STAGES={'outline','write','review'}


def read_graph(db, task_id, project_id):
    task = tasks.get(db, task_id, project_id=project_id)
    run = task.get('latest_run') or {}
    if task['metadata'].get('stage') != 'map' or not run.get('output_snapshot'):
        raise ValueError('此节点没有证据链图谱产物')
    return {'graph': run['checkpoint']['output']['content'], 'task_version': task['version'],
            'run_id': run['id'], 'output_revision': run['checkpoint'].get('output_revision', 0),
            'editable': task['status'] == 'awaiting_review'}


def revise_graph(db, task_id, body, *, authority):
    import math
    import workflow
    import workflow_documents
    import workflow_state
    import project_task_execution
    graph = body.get('graph')
    if not isinstance(graph, dict) or not isinstance(graph.get('nodes'), list) or not isinstance(graph.get('edges'), list):
        raise ValueError('请提供图谱节点与连线')
    if not 1 <= len(graph['nodes']) <= 500 or len(graph['edges']) > 2000:
        raise ValueError('图谱需包含 1 至 500 个节点，连线不能超过 2000 条')
    ids = set()
    for node in graph['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or node['id'] in ids:
            raise ValueError('图谱节点 ID 必须唯一')
        if not isinstance(node.get('label'), str) or not node['label'].strip() or len(node['label']) > 120:
            raise ValueError('图谱节点标题需为 1 至 120 字符')
        if node.get('type', 'note') not in workflow_documents.MAP_TYPES:
            raise ValueError('图谱节点类型无效')
        for key, limit in (('detail', 600), ('ref', 300)):
            if not isinstance(node.get(key, ''), str) or len(node.get(key, '')) > limit:
                raise ValueError('图谱说明或来源过长')
        if any(key in node and (not isinstance(node[key], (int, float)) or not math.isfinite(node[key])) for key in ('x', 'y')):
            raise ValueError('图谱坐标无效')
        ids.add(node['id'])
    for edge in graph['edges']:
        if not isinstance(edge, dict) or not isinstance(edge.get('from'), str) or not isinstance(edge.get('to'), str) or edge['from'] not in ids or edge['to'] not in ids:
            raise ValueError('连线必须指向已有节点')
        if not isinstance(edge.get('label', ''), str) or len(edge.get('label', '')) > 40:
            raise ValueError('连线说明不能超过 40 字符')
    with tasks._lock:
        task = tasks.get(db, task_id, project_id=authority.project_id)
        tasks._authority(authority, task['project_id'], human=True)
        run = task['latest_run']
        if task['metadata'].get('stage') != 'map' or task['status'] != 'awaiting_review' or not run:
            raise ValueError('仅待人工核查的证据链节点可修订')
        if body.get('reviewed_run_id') != run['id'] or body.get('expected_version') != task['version']:
            raise ValueError('节点产物已有更新，请重新打开当前版本')
        tasks._validate_versions(db, task, run)
        current = workflow_state.capture(db, task['project_id'], 'map')
        if current['fingerprint'] != run['checkpoint']['output'].get('fingerprint'):
            raise ValueError('项目图谱已在其他页面修改，请重试节点后再核查')
        tasks._validate_contract(task['output_contract'], {**run['output_snapshot'], 'content': graph})
        revision = run['checkpoint'].get('output_revision', 0) + 1
        root = tasks._root(db, task['project_id'], writable=True)
        working = workflow.map_path(db, task['project_id']).resolve()
        if not working.is_relative_to(root):
            raise ValueError('图谱文件超出项目范围')
        original = working.read_text(encoding='utf-8')
        folder = root / '.tasks' / task_id / run['id'] / 'revisions' / uuid.uuid4().hex
        written_hash = ''
        try:
            workflow.map_write(db, task['project_id'], graph, keep_positions=False)
            written_hash = tasks._hash(working)
            capture = workflow_state.capture(db, task['project_id'], 'map')
            output = project_task_execution._outputs(db, task, run, {'reply': run['checkpoint'].get('reply', '')}, capture, folder=folder)
            checkpoint = {**run['checkpoint'], 'output': capture, 'output_revision': revision}
            tasks._validate_versions(db, task, {**run, 'checkpoint': checkpoint, 'output_snapshot': output})
            with tasks._atomic(db):
                old, new = run['output_snapshot']['files'][0], output['files'][0]
                db.db.execute('INSERT INTO project_task_output_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('revision-' + uuid.uuid4().hex, task_id, run['id'], revision, 0, old['path'], new['path'],
                     old['content_hash'], new['content_hash'], authority.actor_id, str(body.get('reason') or '人工修订证据链')[:4000], tasks._now()))
                db.db.execute('UPDATE project_task_runs SET output_snapshot=?,checkpoint=? WHERE id=?', (tasks._json(output), tasks._json(checkpoint), run['id']))
                db.db.execute('UPDATE project_tasks SET version=version+1,updated_at=? WHERE id=?', (tasks._now(), task_id))
                tasks._event(db, task_id, 'output_revised', {'revision': revision, 'editor': 'evidence_graph', 'actor_id': authority.actor_id}, run['id'])
        except Exception:
            if written_hash and working.is_file() and tasks._hash(working) == written_hash:
                import asset_service
                asset_service.atomic_write_text(working, original)
                asset_service.register_file(db, working, project_id=task['project_id'], kind='document', source='human_revision_rollback', body=original)
            raise
        tasks._notify(db, task_id)
        return tasks.get(db, task_id)


def _output(db,task,index):
    files=(task.get('latest_run') or {}).get('output_snapshot',{}).get('files',[])
    if not 0<=index<len(files):raise ValueError('产物不存在')
    item=files[index];path=Path(item['path']).resolve();root=tasks._root(db,task['project_id'])
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('产物已不存在或超出项目范围')
    if path.suffix.lower() not in TEXT or path.stat().st_size>2*1024*1024:raise ValueError('此产物不支持文本修订，请使用对应研究工具编辑')
    stage=task['metadata'].get('stage','task')
    if stage not in {'task','',*DOCUMENT_STAGES}:raise ValueError('此阶段包含结构化产物，请在研究工具中编辑后重新执行并核查')
    return item,path


def read(db,task_id,project_id,index):
    task=tasks.get(db,task_id,project_id=project_id);item,path=_output(db,task,index)
    return {'text':path.read_text(encoding='utf-8'),'content_hash':tasks._hash(path),'task_version':task['version'],
            'run_id':task['latest_run']['id'],'output_revision':task['latest_run']['checkpoint'].get('output_revision',0),
            'editable':task['status']=='awaiting_review','name':item.get('name') or path.name}


def revise(db,task_id,body,*,authority):
    import asset_service
    import fileguard
    import workflow_state
    with tasks._lock:
        task=tasks.get(db,task_id,project_id=authority.project_id);tasks._authority(authority,task['project_id'],human=True)
        run=task['latest_run']
        if task['status']!='awaiting_review' or not run or run['status']!='succeeded':raise ValueError('仅待人工核查的产物可以修订；已交付版本请在新运行中修改')
        if body.get('reviewed_run_id')!=run['id'] or body.get('expected_version')!=task['version']:
            raise ValueError('任务已有更新，请保留内容并重新载入当前产物')
        index=int(body.get('output_index',0));item,path=_output(db,task,index)
        old_hash=tasks._hash(path)
        if body.get('expected_hash')!=old_hash:raise ValueError('产物已被修改，请重新载入后再保存')
        text=body.get('text')
        if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>2*1024*1024:raise ValueError('修订内容不能为空且不能超过2MB')
        tasks._validate_versions(db,task,run)
        root=tasks._root(db,task['project_id'],writable=True)
        revision=int(run['checkpoint'].get('output_revision',0))+1
        folder=(root/'.tasks'/task_id/run['id']/'revisions'/uuid.uuid4().hex).resolve()
        if not folder.is_relative_to(root):raise ValueError('修订目录超出当前项目')
        output=json.loads(json.dumps(run['output_snapshot']))
        checkpoint=dict(run['checkpoint'])
        old_text=path.read_text(encoding='utf-8')
        matching=[]
        for file_index,file in enumerate(output['files']):
            candidate=Path(file['path'])
            same=file.get('content_hash',file.get('sha256'))==old_hash
            if not same and candidate.suffix.lower() in TEXT and candidate.stat().st_size<=2*1024*1024:
                try:same=candidate.read_text(encoding='utf-8')==old_text
                except UnicodeError:pass
            if same:matching.append(file_index)
        if (item.get('name') or path.name)=='result.md' or output.get('content')==old_text:output['content']=text
        if output.get('reply')==old_text:output['reply']=text
        tasks._validate_contract(task['output_contract'],output)
        stage=task['metadata'].get('stage','task')
        working=[]
        if stage in DOCUMENT_STAGES:
            versions={str(Path(version['path']).resolve()):version['sha256'] for version in output.get('original_versions',[])}
            for file_index,file in enumerate(output['files']):
                if file_index in matching and file.get('original_path'):
                    source=Path(file['original_path']).resolve()
                    expected=versions.get(str(source),file.get('content_hash',file.get('sha256')))
                    if not source.is_relative_to(root) or not source.is_file() or tasks._hash(source)!=expected:raise ValueError('研究文档已经变化，请重新执行节点后再修订')
                    working.append(source)
            if not working:raise ValueError('没有找到对应的研究文档，请在研究工具中编辑')
            for source in dict.fromkeys(working):
                fileguard.before_write(source,'人工修订节点产物');asset_service.atomic_write_text(source,text)
                asset_service.register_file(db,source,project_id=task['project_id'],kind='document',source='human_revision',body=text)
            capture=workflow_state.capture(db,task['project_id'],stage)
            if not capture['valid']:raise ValueError('修订后的研究文档未形成有效产物')
            checkpoint['output']=capture;output['content']=capture['content']
        folder.mkdir(parents=True,exist_ok=False)
        revised_hash='';revised_path=''
        for file_index,file in enumerate(output['files']):
            if file_index not in matching:continue
            destination=folder/(str(file_index)+'-'+Path(file['path']).name)
            asset_service.atomic_write_text(destination,text)
            asset=asset_service.register_file(db,destination,project_id=task['project_id'],kind='task_output',source='human_revision',run_id=run['id'],body=text,
                metadata={'project_task_id':task_id,'task_run_id':run['id'],'revision':revision,'original_path':file['path']})
            digest=tasks._hash(destination)
            file.update(path=str(destination),asset_id=asset['id'],content_hash=digest,sha256=digest,output_revision=revision,revision_source='human')
            if file_index==index:revised_hash=digest;revised_path=str(destination)
        for source in output.get('original_versions',[]):
            if Path(source['path']).resolve() in working:source['sha256']=tasks._hash(Path(source['path']))
        checkpoint['output_revision']=revision
        if stage not in DOCUMENT_STAGES:checkpoint['output']=output
        candidate={**run,'checkpoint':checkpoint,'output_snapshot':output}
        tasks._validate_versions(db,task,candidate)
        with tasks._atomic(db):
            current=tasks._task(db,task_id)
            if current['version']!=task['version'] or current['status']!='awaiting_review':raise ValueError('任务状态已变化，请刷新')
            db.db.execute('INSERT INTO project_task_output_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('revision-'+uuid.uuid4().hex,task_id,run['id'],revision,index,str(path),revised_path,old_hash,revised_hash,authority.actor_id,str(body.get('reason') or '人工修订')[:4000],tasks._now()))
            db.db.execute('UPDATE project_task_runs SET output_snapshot=?,checkpoint=? WHERE id=?',(tasks._json(output),tasks._json(checkpoint),run['id']))
            db.db.execute('UPDATE project_tasks SET version=version+1,updated_at=? WHERE id=?',(tasks._now(),task_id))
            tasks._event(db,task_id,'output_revised',{'revision':revision,'output_index':index,'original_hash':old_hash,'revised_hash':revised_hash,'actor_id':authority.actor_id},run['id'])
        tasks._notify(db,task_id)
        return tasks.get(db,task_id)
