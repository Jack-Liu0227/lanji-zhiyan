"""Create project files and directories without overwriting existing material."""
from pathlib import Path
import re

import fileguard
import project_notes


def upload(db, project_id, name, content):
    import asset_service
    root=project_notes.project_root(db,project_id,writable=True)
    if not content or len(content)>50*1024*1024:
        raise ValueError('请选择非空文件，每个文件最多 50 MB')
    name=str(name).strip()
    if not name or name in ('.','..') or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name.endswith('.'):
        raise ValueError('文件名无效，请使用不含路径的文件名')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',name,re.I):
        raise ValueError('此名称是系统保留名称')
    folder=(root/'其余'/'上传文件').resolve()
    if not folder.is_relative_to(root):raise ValueError('上传目录越过项目边界')
    folder.mkdir(parents=True,exist_ok=True)
    original=Path(name);index=1
    while True:
        target=folder/(name if index==1 else f'{original.stem}-{index}{original.suffix}')
        fileguard.assert_writable(target)
        try:
            stream=target.open('xb')
            break
        except FileExistsError:index+=1
    with stream:stream.write(content)
    fileguard.audit('upload',target,bytes=len(content))
    asset=asset_service.register_file(db,target,project_id=project_id,kind='document',source='project_upload',title=name)
    return {'asset_id':asset['id'],'name':target.name,'relative':target.relative_to(root).as_posix(),'size':len(content)}


def create(db, body):
    project_id=str(body.get('project_id') or '')
    if not project_id:
        raise ValueError('请先选择项目')
    root=project_notes.project_root(db,project_id,writable=True)
    relative=str(body.get('parent') or '').replace('\\','/')
    if Path(relative).is_absolute() or Path(relative).drive:
        raise ValueError('父目录必须是项目内相对路径')
    parent=(root/relative).resolve()
    if not parent.is_relative_to(root) or not parent.is_dir():
        raise ValueError('父目录不存在或越过项目文件夹')
    name=str(body.get('name') or '').strip()
    if not name or name in {'.','..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name.endswith('.'):
        raise ValueError('名称须为有效文件名，不能包含路径或特殊字符')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',name,re.I):
        raise ValueError('此名称是系统保留名称')
    target=parent/name
    if target.exists():
        raise ValueError('已存在同名文件或文件夹')
    kind=body.get('kind')
    if kind=='folder':
        target.mkdir()
    elif kind=='file':
        with target.open('x',encoding='utf-8') as stream:
            stream.write('')
    else:
        raise ValueError('请选择新建文件或文件夹')
    fileguard.audit('create_'+kind,target)
    return {'ok':True,'project_id':project_id,'rel':target.relative_to(root).as_posix(),
            'path':str(target),'name':name,'ext':target.suffix.lstrip('.'),'kind':kind}
