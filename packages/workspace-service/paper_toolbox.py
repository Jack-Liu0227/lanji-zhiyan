"""Paper helpers share configured models, durable Agent jobs and project assets."""
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

import asset_service
import execution

TOOLS=[{'id':'caption','name':'图片图注','description':'单张或批量图片生成、核对与润色图注，需支持视觉的模型'},
       {'id':'image_convert','name':'图片格式转换','description':'本地转换 PNG、JPG、JPEG、WebP、TIFF、PDF；PDF 按页栅格化'},
       {'id':'translate','name':'中英互译','description':'双语学术文本翻译，保留公式和引用标记'},
       {'id':'polish','name':'学术润色','description':'改进表达，保留事实、数字、公式和引用'},
       {'id':'references','name':'引文整理','description':'检查 BibTeX / RIS 重复项与缺失字段，不判定文献真实性'},
       {'id':'convert','name':'格式转换','description':'导出 Markdown、HTML、Word；PDF 使用项目既有转换服务'},
       {'id':'pdf_translate','name':'PDF 论文翻译','description':'调用已有项目 PDF 翻译与进度服务'},
       {'id':'mermaid','name':'Mermaid 图表','description':'本地预览流程图、时序图等，导出 SVG 或高分辨率 PNG','runtime':'browser'}]
PROMPT_PRESETS=[{'id':'caption_academic','tool':'caption','category':'图注','name':'论文图注与润色','description':'依据可见内容生成英文 caption，标出需补充的信息。','prompt':'依据图像和提供的背景生成严谨的论文图注。说明子图、坐标、图例及可见趋势；不得编造方法、样本量、统计显著性或结论。未知信息单独列为待确认。输出英文 caption、中文说明和待补充项；如提供原图注，核对并润色。'},
    {'id':'translate_academic','tool':'translate','category':'学术写作','name':'学术准确','description':'适合论文正文，注重学科术语、论证关系和表述边界。',
     'prompt':'以学术论文正文为使用场景，采用本学科常用术语和规范表达。\n准确传达原文的因果、比较、转折和条件关系，保留限定条件与不确定性，不加强结论。\n遵循目标语言的自然语序，避免逐字对应的生硬句式。保留事实、数据、公式和引用，只给出译文。'},
    {'id':'translate_abstract','tool':'translate','category':'学术写作','name':'摘要翻译','description':'适合摘要，保留原有研究目标、方法、发现和结论。',
     'prompt':'翻译论文摘要，保持信息密度和学术语气。\n准确呈现原文已有的研究目标、方法、结果和结论，不强行补齐原文没有的信息或结构。\n采用紧凑、清晰的句子，保持术语一致和结论强度；不增加创新性宣传、背景论述或未经提供的数据。只输出译文。'},
    {'id':'translate_literal','tool':'translate','category':'对照与术语','name':'忠实直译','description':'适合逐句阅读与语义核对，保留句序和术语对应。',
     'prompt':'以逐句核对原文为目标进行忠实翻译。\n尽量保持原文的句序、段落结构和语义对应，避免合并省略、概括改写或解释性扩展。\n术语、缩写、数字、单位、公式和引用须前后一致。保留原文的歧义和限定范围，不擅自补充推断。只输出译文。'},
    {'id':'translate_terminology','tool':'translate','category':'对照与术语','name':'术语一致','description':'适合专业材料与方法，优先遵循术语表和缩写约定。',
     'prompt':'以专业术语准确一致为重点进行翻译，特别核对材料名称、方法名称、缩写和单位。\n优先采用用户提供的术语约定；没有约定时使用领域常用称谓，不能确定的专名保留原文，不自行发明术语。\n同一概念保持同一译法，不为追求变化而替换术语。保留原文事实、数值、公式、引用及不确定性，只输出译文。'},
    {'id':'translate_natural','tool':'translate','category':'阅读与交流','name':'自然流畅','description':'适合阅读理解与内部交流，改善语序和长句的可读性。',
     'prompt':'在忠实原意的基础上，让译文符合目标语言的自然表达习惯，适合阅读理解与内部交流。\n可调整句内语序、拆分冗长句子并改善衔接，避免逐字翻译和生硬套话。\n保持论证先后、关键术语、事实及限定条件，保留作者原有语气，不增删结论。只输出译文，不附解释。'},
    {'id':'polish_academic','tool':'polish','category':'学术表达','name':'学术规范','description':'适合论文正文，修正语法、用词和论证衔接。',
     'prompt':'润色学术论文正文，使用准确、克制的学术措辞，保持原语言与原有论证结构。\n修正语法、搭配、指代和句间衔接，保持专业术语一致，避免空泛评价与宣传性措辞。\n保留原文事实、数字、公式、引用、比较基准和结论强度，不补充未经提供的证据。只输出润色后的文本。'},
    {'id':'polish_cautious','tool':'polish','category':'学术表达','name':'论述严谨','description':'适合讨论与局限性，检查限定语及相关性、因果性表述。',
     'prompt':'重点改善学术论述的准确性和边界，保持原语言。\n保留条件、适用范围、局限性和不确定性，不把相关性改成因果，不把推测改成定论，也不擅自削弱原文的明确陈述。\n在不改变原意的前提下修正模糊指代与逻辑跳跃，不新增解释机制、实验结果或参考文献。只输出润色后的文本。'},
    {'id':'polish_concise','tool':'polish','category':'清晰与简洁','name':'简洁凝练','description':'适合压缩冗长段落，保留信息与必要的限定条件。',
     'prompt':'以简洁、清晰为目标润色原文，保持原语言。\n删去重复和空泛表达，缩短冗长句子，用直接的动词和明确的主语表达原意；避免反复替换同一专业术语。\n不得为追求简短而省略必要条件、比较基准、关键发现、局限性或引用，不增加新论点。只输出润色后的文本。'},
    {'id':'polish_natural','tool':'polish','category':'清晰与简洁','name':'自然表达','description':'适合改善阅读节奏，减少套话并保持作者原有语气。',
     'prompt':'改善原文的阅读节奏和句间衔接，保持原语言与作者原有语气。\n减少套话、机械重复和不必要的名词化，调整过长或单调的句式，使表达自然易懂。\n保持事实、论证关系、专业术语和结论强度，不加入个人经历、主观评价或未经提供的证据。只输出润色后的文本。'},
    {'id':'polish_abstract','tool':'polish','category':'投稿与沟通','name':'摘要精炼','description':'适合已有摘要，改善信息顺序与句子密度。',
     'prompt':'润色已经写好的论文摘要，保持原语言，改善信息组织、句间衔接与表达密度。\n突出原文已有的研究问题、方法、关键发现及结论，不自行补写缺失部分，不夸大创新性或应用价值。\n保留具体数据、限定条件、缩写及结论强度；未给出字数要求时不擅自删减关键信息。只输出润色后的摘要。'},
    {'id':'polish_response','tool':'polish','category':'投稿与沟通','name':'审稿回复措辞','description':'适合已有审稿回复，保持礼貌、具体并准确描述修改状态。',
     'prompt':'润色已经写好的审稿回复，保持原语言、答复立场、原有修改事实和完成状态。\n使用礼貌、具体、直接的表达，减少防御性或空泛的感谢，明确区分解释、已做修改与尚未完成的工作。\n不声称已经完成原文未说明的实验、分析或修订，不添加新数据、承诺或引用。只输出润色后的回复文本。'},
]
PROMPT_TOOLS={'translate','polish','caption'}
PROTECTED=re.compile(r'\$\$[\s\S]*?\$\$|(?<!\$)\$[^$\n]+\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\\(?:cite\w*|ref|eqref)\{[^}]*\}|\{\{C\d+\}\}|\[\d+(?:\s*[,–-]\s*\d+)*\]')
_save_lock=threading.RLock()


def validate(spec):
    tool=str(spec.get('tool') or 'translate');text=str(spec.get('text') or '')
    if tool not in {item['id'] for item in TOOLS}:raise ValueError('工具不存在')
    if tool=='mermaid':raise ValueError('Mermaid 图表请在工具箱中本地预览和导出')
    if tool not in ('pdf_translate','caption','image_convert') and (not text.strip() or len(text)>200000):raise ValueError('请输入 1 至 200000 字符的内容')
    direction=str(spec.get('direction') or 'zh_en')
    if direction not in ('zh_en','en_zh'):raise ValueError('翻译方向无效')
    fmt=str(spec.get('format') or 'docx')
    if fmt not in (('png','jpg','jpeg','webp','tif','tiff','pdf') if tool=='image_convert' else ('md','html','docx','pdf')):raise ValueError('导出格式无效')
    preset_id=str(spec.get('prompt_preset') or (tool+'_academic' if tool in PROMPT_TOOLS else ''))
    prompt=spec.get('prompt');reference=spec.get('prompt_template')
    if tool not in PROMPT_TOOLS and (prompt is not None or reference is not None or preset_id):
        raise ValueError('当前工具使用固定处理规则，不支持风格提示词')
    if preset_id and not any(item['id']==preset_id and item['tool']==tool for item in PROMPT_PRESETS):
        raise ValueError('提示词模板不适用于当前工具')
    if prompt is not None and (not isinstance(prompt,str) or len(prompt)>20000):
        raise ValueError('风格提示词须为不超过 20000 字符的文本')
    if reference is not None and (not isinstance(reference,dict) or not reference.get('template_id')):
        raise ValueError('请选择有效的项目提示词模板')
    return {'tool':tool,'text':text,'direction':direction,'format':fmt,'terminology':str(spec.get('terminology') or '')[:10000],
            'prompt_preset':preset_id,'prompt':prompt,'prompt_template':reference,
            'image_ids':spec.get('image_ids',[]),'dpi':max(72,min(600,int(spec.get('dpi') or 180)))}


def _prepare_prompt(db,project_id,spec):
    if spec['tool'] not in PROMPT_TOOLS:return spec
    snapshot=None
    if spec['prompt_template'] is not None:
        if not project_id:raise ValueError('使用项目模板前请先选择结果归属项目')
        import prompt_templates
        snapshot=prompt_templates.resolve(db,project_id,spec['prompt_template'])
    default=snapshot['rendered'] if snapshot else next(item['prompt'] for item in PROMPT_PRESETS if item['id']==spec['prompt_preset'])
    prompt=spec['prompt'] if spec['prompt'] is not None else default
    if len(prompt)>20000:raise ValueError('风格提示词不能超过 20000 字符，请缩小模板内容')
    return {**spec,'prompt':prompt,'_prompt_snapshot':snapshot}


def _prompt_record(spec):
    if spec.get('tool') not in PROMPT_TOOLS:return None
    snapshot=spec.get('_prompt_snapshot') or {}
    preset=next((item for item in PROMPT_PRESETS if item['id']==spec.get('prompt_preset')), {})
    original=snapshot.get('rendered',preset.get('prompt',''))
    return {'title':snapshot.get('title',preset.get('name','自定义提示词')),'revision':snapshot.get('revision'),
            'revision_id':snapshot.get('revision_id',''),'text':spec.get('prompt',''),'modified':spec.get('prompt','')!=original}


def _references(text):
    if re.search(r'^TY\s+-',text,re.M):
        entries=[part.strip()+'\nER  -' for part in re.split(r'^ER\s+-\s*$',text,flags=re.M) if part.strip()]
        fields=lambda entry:{match[0]:match[1].strip() for match in re.findall(r'^([A-Z0-9]{2})\s+-\s*(.+)$',entry,re.M)}
        required=('TI','AU','PY');doi_field='DO'
    else:
        starts=list(re.finditer(r'@\w+\s*\{\s*([^,]+),',text))
        if not starts:raise ValueError('请输入有效 BibTeX 或 RIS 条目')
        entries=[text[match.start():starts[i+1].start() if i+1<len(starts) else len(text)].strip() for i,match in enumerate(starts)]
        fields=lambda entry:{match[0].lower():match[1].strip('{}" ') for match in re.findall(r'(\w+)\s*=\s*([^\n]+)',entry)}
        required=('title','author','year');doi_field='doi'
    seen=set();kept=[];warnings=[]
    for i,entry in enumerate(entries,1):
        data=fields(entry);doi=re.search(r'10\.\d{4,9}/[^\s}"<>]+',data.get(doi_field,''),re.I)
        key=doi.group(0).lower().rstrip(',') if doi else re.sub(r'\s+','',data.get('title') or data.get('TI') or entry).casefold()
        if key in seen:warnings.append(f'第 {i} 条与已有条目重复，已从整理结果中合并');continue
        seen.add(key);kept.append(entry)
        missing=[field for field in required if not data.get(field)]
        if missing:warnings.append(f'第 {i} 条缺少：'+', '.join(missing))
    return '# 引文整理结果\n\n输入 '+str(len(entries))+' 条，保留 '+str(len(kept))+' 条。\n\n'+('\n'.join('- '+line for line in warnings) or '未发现重复项或所检查字段缺失。')+'\n\n```\n'+'\n\n'.join(kept)+'\n```\n\n本工具检查格式和字段，不核验文献是否真实发表。'


def transform(db,owner_id,spec,emit=None,*,prepared=False):
    import workflow
    spec=validate(spec);emit=emit or (lambda event:None)
    if not prepared:spec=_prepare_prompt(db,owner_id,spec)
    tool,text=spec['tool'],spec['text']
    if tool in ('caption','image_convert'):
        import image_tools
        project_id=owner_id if db.db.execute('SELECT 1 FROM research_tasks WHERE id=?',(owner_id,)).fetchone() else ''
        return image_tools.transform(db,project_id,spec,emit)
    if tool=='references':return {'content':_references(text)}
    if tool=='convert':
        old=getattr(db,'quiet_steps',False);db.quiet_steps=True
        try:
            if spec['format']=='pdf':
                if not db.db.execute('SELECT 1 FROM research_tasks WHERE id=?',(owner_id,)).fetchone():raise ValueError('导出 PDF 请选择项目，以使用项目的论文与引用配置')
                source=db.pdir(owner_id,'其余')/('toolbox-'+uuid.uuid4().hex+'.md');asset_service.atomic_write_text(source,text)
                result=workflow.conv_run(db,owner_id,'pdf',str(source))
            else:result=workflow.stage_convert(db,owner_id,text,spec['format'],'toolbox-'+uuid.uuid4().hex[:10])
        finally:db.quiet_steps=old
        return {'content':'已导出：'+result['path'],**result}
    if tool not in ('translate','polish'):raise ValueError('PDF 翻译请从已入库的项目文献启动')
    model=db.active_model()
    if not model:raise ValueError('请先配置并选择可用模型')
    protected=[]
    def mask(match):protected.append(match.group(0));return '⟦KEEP_'+str(len(protected)-1)+'⟧'
    masked=PROTECTED.sub(mask,text);chunks=[]
    while masked:
        cut=min(4000,len(masked))
        if cut<len(masked):
            boundary=masked.rfind('\n\n',0,cut)
            if boundary>cut//2:cut=boundary+2
            marker=masked.rfind('⟦KEEP_',0,cut)
            if marker>=0 and masked.find('⟧',marker)>=cut:cut=marker
        chunks.append(masked[:cut]);masked=masked[cut:]
    results=[]
    instruction=('Translate the text into academic English.' if spec['direction']=='zh_en' else '将文本译为准确、自然的学术中文。') if tool=='translate' else '润色学术文本，保持原语言、事实、数字和结论，不增加未经提供的证据。'
    instruction+='\n风格与写作偏好：\n'+(spec['prompt'] or '')
    instruction+='\n必须遵守：翻译方向和润色语言以当前工具要求为准。保留原文事实、数字、结论、段落和不确定性，不补充证据。保留所有 ⟦KEEP_n⟧ 标记及其出现次数，不解释标记。只输出处理后的文本。术语约定：'+spec['terminology']
    for index,chunk in enumerate(chunks,1):
        execution.check();emit({'type':'tool_call','name':tool,'args':{'part':index,'total':len(chunks)}})
        emit({'type':'model_request','model':model.get('model',''),'part':index,'total':len(chunks)})
        result=workflow.chat_raw(model,[{'role':'system','content':instruction},{'role':'user','content':chunk}],max_tokens=12000)
        content=str(result.get('content') or '').strip()
        if not content:raise ValueError('模型没有返回处理结果')
        for token in re.findall(r'⟦KEEP_\d+⟧',chunk):
            if content.count(token)!=chunk.count(token):raise ValueError('模型未完整保留公式或引用标记，请缩短段落后重试')
        results.append(content);emit({'type':'tool_result','name':tool,'ok':True,'summary':f'已完成 {index}/{len(chunks)} 段'})
    content='\n\n'.join(results)
    for index,value in enumerate(protected):content=content.replace('⟦KEEP_'+str(index)+'⟧',value)
    return {'content':content}


def start(db,body):
    import agent_jobs
    import workflow
    spec=validate(body);pid=str(body.get('project_id') or '')
    if pid:
        import project_tasks
        project_tasks._project(db,pid)
    if spec['tool']=='pdf_translate':
        paper=db.paper(str(body.get('paper_id') or ''))
        if not pid or not paper or paper['task_id']!=pid:raise ValueError('请选择当前项目已入库的 PDF')
        return workflow._tool_impl('paper_translate',db,pid,{'paper_id':paper['id'],'action':'start'})
    if spec['tool'] in ('caption','image_convert'):
        import image_tools
        image_tools.inputs(db,pid,spec['image_ids'])
    model_id=str(body.get('model_id') or '')
    if model_id:
        import agent_settings
        agent_settings.validate_runtime_fields(db,{'model_id':model_id})
    key=str(body.get('request_key') or uuid.uuid4().hex)
    digest=hashlib.sha256(json.dumps([pid,spec,model_id],sort_keys=True).encode()).hexdigest()
    old=db.get_async_task_by_idempotency(key)
    if old:
        request=old['checkpoint'].get('request',{})
        if request.get('toolbox_digest')!=digest:raise ValueError('该请求标识已经用于另一项处理')
        owner=old['owner_id']
    else:
        spec=_prepare_prompt(db,pid,spec)
        owner=db.create_chat(next(item['name'] for item in TOOLS if item['id']==spec['tool']),task_id=pid)
        db.db.execute("UPDATE chats SET channel='toolbox',model_id=?,permission='read_only' WHERE id=?",(model_id,owner));db.db.commit()
    run=agent_jobs.start(db.path,{'task_id':owner,'idempotency_key':key,'toolbox_spec':spec,'toolbox_digest':digest,'message':spec['text']},step='paper_toolbox')
    return {'run_id':run.run_id,'chat_id':owner,'request_key':key,'status':run.status}


def execute(db,request,emit):
    owner=request['task_id'];chat=db.chat(owner)
    target=chat['task_id'] or owner
    spec=request['toolbox_spec'];snapshot=spec.get('_prompt_snapshot')
    if snapshot:
        import prompt_templates
        run=db.get_async_task_by_idempotency(request['idempotency_key'])
        if not db.db.execute("SELECT 1 FROM asset_provenance WHERE asset_id=? AND run_id=? AND operation='template_used'",(snapshot['asset_id'],run['id'])).fetchone():
            prompt_templates.usage(db,{**snapshot,'applied_prompt':spec['prompt']},run['id'])
        emit({'type':'template_used','title':snapshot['title'],'revision':snapshot['revision'],'revision_id':snapshot['revision_id']})
    result=transform(db,target,spec,emit,prepared=True)
    execution.check()
    db.add_message(owner,'user',request['toolbox_spec']['text'],scope='agent')
    db.add_message(owner,'assistant',result['content'],scope='agent')
    emit({'type':'toolbox_result',**result})
    return {'reply':result['content'],'toolbox_result':result}


def route(method,path,body,query,db):
    if not path.startswith('/api/paper-toolbox'):return None
    if method=='POST' and path=='/api/paper-toolbox/import':
        import toolbox_import
        return toolbox_import.import_file(db,body)
    if method=='GET' and path=='/api/paper-toolbox':return {'tools':TOOLS,'prompt_presets':PROMPT_PRESETS}
    if method=='GET' and path.endswith('/result'):
        identifier=(query.get('run_id') or [''])[0];pid=(query.get('project_id') or [''])[0]
        run=db.get_async_task(identifier);chat=db.chat(run['owner_id']) if run else None
        if not chat or chat['channel']!='toolbox' or (pid and chat['task_id']!=pid):raise ValueError('工具处理记录不存在或不属于当前项目')
        content='';result_path='';result_files=[];result_errors=[]
        if run['status']=='done':
            content=next((m['content'] for m in reversed(db.messages(chat['id'],'agent')) if m['role']=='assistant'),'')
            event=db.db.execute("SELECT payload FROM run_events WHERE run_id=? AND kind='toolbox_result' ORDER BY event_id DESC LIMIT 1",(identifier,)).fetchone()
            if event:
                payload=json.loads(event['payload']);result_path=payload.get('path','');result_files=payload.get('files',[]);result_errors=payload.get('errors',[])
        return {'id':identifier,'title':chat['title'],'project_id':chat['task_id'],'status':run['status'],'content':content,'path':result_path,'files':result_files,'errors':result_errors,
                'prompt':_prompt_record(run.get('checkpoint',{}).get('request',{}).get('toolbox_spec',{}))}
    if method=='POST' and path.endswith('/upload'):
        import image_tools
        return image_tools.upload(db,body)
    if method=='POST' and path.endswith('/start'):return start(db,body)
    if method=='POST' and path.endswith('/save'):
        import project_resources
        run=db.get_async_task(str(body.get('run_id') or ''));pid=str(body.get('project_id') or '')
        chat=db.chat(run['owner_id']) if run else None
        if not run or run['status']!='done' or not chat or chat['channel']!='toolbox' or chat['task_id'] not in ('',pid):raise ValueError('处理尚未完成或结果不属于当前项目')
        messages=db.messages(chat['id'],'agent');content=next((message['content'] for message in reversed(messages) if message['role']=='assistant'),'')
        with _save_lock:
            old=asset_service.find_by_metadata(db,project_id=pid,key='toolbox_run_id',value=run['id'])
            if old:return {'asset_id':old[0]['id'],'path':old[0]['canonical_uri'],'reused':True}
            saved=project_resources.write(db,pid,{'kind':str(body.get('kind') or 'note'),'title':str(body.get('title') or chat['title']),'content':content})
            asset=asset_service.get(db,saved['asset_id']);metadata={**json.loads(asset['metadata']),'toolbox_run_id':run['id']}
            db.db.execute('UPDATE assets SET metadata=? WHERE id=?',(json.dumps(metadata),asset['id']));db.db.commit()
            return saved
    raise ValueError('不支持的论文工具操作')
