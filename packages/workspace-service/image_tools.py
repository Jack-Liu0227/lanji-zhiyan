"""Validated image inputs, local format conversion and multimodal figure captions."""
import base64
import io
import json
from pathlib import Path
import uuid

import asset_service
import execution
import paths

FORMATS = {'png':'PNG','jpg':'JPEG','jpeg':'JPEG','webp':'WEBP','tif':'TIFF','tiff':'TIFF','pdf':'PDF'}


def folder(db, project_id, kind):
    if project_id:
        import project_notes
        root=project_notes.project_root(db,project_id,writable=True)/'其余'/'图片工具'/kind
    else:
        root=Path(db.path).resolve().parent/'image-toolbox'/kind
    root.mkdir(parents=True,exist_ok=True)
    return root


def upload(db, body):
    from PIL import Image
    name=Path(str(body.get('name') or '')).name
    suffix=Path(name).suffix.lower()
    if suffix.lstrip('.') not in FORMATS:
        raise ValueError('支持 PNG、JPG、JPEG、WebP、TIFF 和 PDF')
    encoded=str(body.get('data') or '').split(',',1)[-1]
    if len(encoded)>28_000_000:raise ValueError('每个文件最多 20 MB')
    try:raw=base64.b64decode(encoded,validate=True)
    except ValueError:raise ValueError('图片内容编码无效') from None
    if not raw:raise ValueError('文件为空')
    if len(raw)>20*1024*1024:raise ValueError('每个文件最多 20 MB')
    if suffix=='.pdf':
        import pypdfium2
        with pypdfium2.PdfDocument(raw) as document:
            if not 1<=len(document)<=30:raise ValueError('每个 PDF 支持 1 至 30 页')
    else:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>40_000_000:raise ValueError('图片最多 4000 万像素')
            image.verify()
    pid=str(body.get('project_id') or '')
    dest=folder(db,pid,'输入')/(uuid.uuid4().hex+suffix)
    with dest.open('xb') as output:output.write(raw)
    asset=asset_service.register_file(db,dest,project_id=pid,kind='image',source='image_upload',title=name)
    return {'asset_id':asset['id'],'name':name,'bytes':len(raw)}


def inputs(db, project_id, ids):
    if not isinstance(ids,list) or not 1<=len(ids)<=20:raise ValueError('请选择 1 至 20 个图片或 PDF 文件')
    result=[]
    for identifier in ids:
        asset=asset_service.get(db,str(identifier))
        if not asset or asset['project_id']!=project_id or asset['source'] not in ('image_upload','project_upload'):
            raise ValueError('图片不存在或不属于当前项目')
        path=Path(asset['canonical_uri'])
        if path.suffix.lower().lstrip('.') not in FORMATS:raise ValueError('此文件不是支持的图片或 PDF')
        if not path.is_file() or asset_service.sha256_file(path)!=asset['content_hash']:
            raise ValueError('图片输入已变化，请重新添加')
        result.append((asset,path))
    return result


def pages(path,dpi=180):
    from PIL import Image,ImageOps
    if path.suffix.lower()=='.pdf':
        import pypdfium2
        with pypdfium2.PdfDocument(path) as document:
            for i in range(len(document)):
                execution.check()
                page=document[i]
                try:
                    width,height=page.get_size();scale=min(dpi/72,(40_000_000/max(1,width*height))**.5)
                    bitmap=page.render(scale=scale)
                    try:yield i+1,bitmap.to_pil().copy()
                    finally:bitmap.close()
                finally:page.close()
    else:
        with Image.open(path) as source:
            yield 1,ImageOps.exif_transpose(source).copy()


def rgb(image):
    from PIL import Image
    if 'A' in image.getbands() or image.mode=='P':
        rgba=image.convert('RGBA');result=Image.new('RGB',rgba.size,'white');result.paste(rgba,mask=rgba.getchannel('A'));return result
    return image.convert('RGB')


def message_images(db, project_id, ids, snapshots=None):
    selected=[({},Path(item['path'])) for item in snapshots] if snapshots else inputs(db,project_id,ids)
    if len(selected)>5:raise ValueError('每条对话最多附带 5 个图片文件')
    result=[]
    for asset,path in selected:
        for _,image in pages(path):
            try:
                if len(result)>=10:raise ValueError('每条对话最多 10 页图片，请使用工具箱批量处理')
                image.thumbnail((2048,2048));buffer=io.BytesIO();image.convert('RGB').save(buffer,format='PNG')
                result.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}})
            finally:image.close()
    return result


def transform(db, project_id, spec, emit):
    import workflow
    selected=inputs(db,project_id,spec['image_ids'])
    results=[];files=[];errors=[]
    dest=folder(db,project_id,'输出')/uuid.uuid4().hex;dest.mkdir()
    for index,(asset,path) in enumerate(selected,1):
        try:
            for page_no,image in pages(path,spec.get('dpi',180)):
                execution.check()
                label=asset['title']+(f' · 第 {page_no} 页' if path.suffix.lower()=='.pdf' else '')
                emit({'type':'tool_result','name':spec['tool'],'ok':True,'summary':f'处理 {index}/{len(selected)}：{label}'})
                try:
                    if spec['tool']=='caption':
                        model=db.active_model()
                        if not model:raise ValueError('请先配置支持图片输入的模型')
                        import account_providers
                        if model.get('api','') in account_providers.WIRES:
                            raise ValueError('当前账号适配器不支持图片输入，请选择支持视觉的 API 模型')
                        image.thumbnail((2048,2048));buffer=io.BytesIO();image.save(buffer,format='PNG')
                        instruction=spec.get('prompt') or '根据图片生成严谨的论文图注；识别子图、坐标、图例与可见趋势。未知实验条件、样本量或统计方法列为待确认，不编造。输出英文 caption、中文说明和待补充项。若给定原图注，先核对再润色。'
                        result=workflow.chat_raw(model,[{'role':'system','content':instruction},{'role':'user','content':[
                            {'type':'text','text':'图片：'+label+'\n研究背景或原图注：'+spec.get('text','')},
                            {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}}]}],max_tokens=4000)
                        content=str(result.get('content') or '').strip()
                        if not content:raise ValueError('模型未返回图注')
                        results.append('## '+label+'\n\n'+content)
                    else:
                        fmt=spec['format'];output=dest/(f'{index:02d}-{page_no:02d}.'+fmt)
                        converted=rgb(image) if fmt in ('jpg','jpeg','pdf') else image
                        try:converted.save(output,format=FORMATS[fmt],**({'resolution':spec['dpi']} if fmt=='pdf' else {'quality':95} if fmt in ('jpg','jpeg','webp') else {}))
                        finally:
                            if converted is not image:converted.close()
                        saved=asset_service.register_file(db,output,project_id=project_id,kind='image',source='image_conversion',title=label)
                        files.append({'asset_id':saved['id'],'name':label+' → '+fmt.upper(),'path':str(output)})
                finally:image.close()
        except execution.Cancelled:raise
        except Exception as exc:errors.append({'name':asset['title'],'error':str(exc)[:500]})
    content='\n\n'.join(results) if spec['tool']=='caption' else '\n'.join('- '+f['name'] for f in files)
    if errors:content+='\n\n## 未完成的文件\n'+'\n'.join('- '+e['name']+'：'+e['error'] for e in errors)
    if not results and not files:raise ValueError(content or '未生成结果')
    if results:
        output=dest/'captions.md';asset_service.atomic_write_text(output,content)
        saved=asset_service.register_file(db,output,project_id=project_id,kind='note',source='image_caption',title='图片图注')
        files.append({'asset_id':saved['id'],'name':'图注文档','path':str(output)})
    return {'content':content,'files':files,'errors':errors,'path':files[0]['path'] if files else ''}
