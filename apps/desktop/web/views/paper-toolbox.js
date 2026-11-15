/* Global paper helpers use the same model profiles and durable run stream. */
const PaperToolbox=(()=>{
  const h=value=>esc(String(value??''));
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))||fallback;}catch{return fallback;}};
  let panel=null,diagramView=null,following=false,starting=false,opening=0,tools=[],presets=[],state=read('rw-paper-toolbox',{tool:'translate',text:'',direction:'zh_en',format:'docx',project_id:'',model_id:'',terminology:'',result:'',run:null,result_run_id:''});
  const el=name=>panel?.querySelector('[data-tb-'+name+']');
  const imports={
    caption:['.png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf','PNG、JPG、WebP、TIFF 或 PDF · 每批最多 20 个，PDF 最多 30 页'],
    image_convert:['.png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf','PNG、JPG、WebP、TIFF 或 PDF · 每批最多 20 个，PDF 最多 30 页'],
    translate:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    polish:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    convert:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    references:['.bib,.bibtex,.ris,.txt','BibTeX、RIS 或 TXT · 追加到原文，请保持同一种引文格式'],
    pdf_translate:['.pdf','PDF · 先选择结果归属项目，导入文献后选择一篇启动翻译'],
    mermaid:['.mmd,.mermaid,.md,.txt','Mermaid、Markdown 或 TXT · 每次 1 个，替换当前图表源码'],
  };
  function paintImport(){
    if(!el('import-input'))return;
    const config=imports[state.tool],busy=!!state.run||starting;
    el('import-input').accept=config[0];el('import-input').multiple=state.tool!=='mermaid';
    el('import-input').disabled=busy;el('choose-file').disabled=busy;el('paper').disabled=busy;
    el('import-hint').textContent=config[1]+' · 每个文件最多 20 MB';
    panel.dataset.dropLabel='松开鼠标导入到「'+(tools.find(item=>item.id===state.tool)?.name||'当前工具')+'」';
    el('import-zone').setAttribute('aria-busy',String(busy));
  }
  function persist(){try{localStorage.setItem('rw-paper-toolbox',JSON.stringify(state));}catch{}}
  function error(message){if(el('error'))el('error').textContent=message||'';}
  function paint(){if(!panel?.isConnected)return;const busy=!!state.run||starting,diagram=state.tool==='mermaid';panel.querySelectorAll('[name]').forEach(input=>input.disabled=busy);el('run').disabled=busy;el('stop').hidden=diagram||!state.run;el('output').value=state.result||'';el('copy').disabled=!state.result;el('save').disabled=!state.result||!state.result_run_id;el('file').hidden=diagram||!state.path;el('status').textContent=starting?'正在提交处理请求':state.run?'执行中 · 可以关闭面板，后台继续运行':state.result?(state.errors?.length?'部分完成 · '+state.errors.length+' 个文件未完成':'处理完成'):'使用已配置模型处理文本，或使用本地整理与转换工具';for(const name of ['run','copy','save','status'])el(name).hidden=diagram;paintPrompts();paintImages();paintImport();}
  function paintImages(){
    if(!el('images'))return;
    el('images').hidden=!['caption','image_convert'].includes(state.tool);
    el('image-list').innerHTML=(state.images||[]).map((item,index)=>`<li>${h(item.name)} <button type="button" class="pw-text-button" data-remove-image="${index}" ${state.run||starting?'disabled':''} >移除</button></li>`).join('');
    el('image-list').querySelectorAll('button').forEach(button=>button.onclick=()=>{state.images.splice(Number(button.dataset.removeImage),1);persist();paintImages();});
    el('downloads').innerHTML=(state.files||[]).map(file=>`<li><a href="/api/paper-toolbox/file?asset_id=${encodeURIComponent(file.asset_id)}&project_id=${encodeURIComponent(state.project_id||'')}" download>${h(file.name)}</a></li>`).join('');
    el('image-input').disabled=!!state.run||starting;
  }
  async function loadPapers(){
    if(!panel?.isConnected)return;
    el('paper-row').hidden=state.tool!=='pdf_translate';if(state.tool!=='pdf_translate')return;
    const project=state.project_id;
    if(!project){el('paper').innerHTML='<option value="">请先选择项目</option>';return;}
    try{
      const data=await api('/api/task?id='+encodeURIComponent(project));
      if(!panel?.isConnected||state.project_id!==project||state.tool!=='pdf_translate')return;
      el('paper').innerHTML=(data.paper_list||[]).map(paper=>`<option value="${h(paper.id)}">${h(paper.title||paper.filename)}</option>`).join('')||'<option value="">当前项目没有已入库 PDF</option>';
      if((data.paper_list||[]).some(paper=>paper.id===state.paper_id))el('paper').value=state.paper_id;
      el('paper').onchange=()=>{state.paper_id=el('paper').value;persist();};
    }catch(err){error(err.message);}
  }
  function promptConfig(){
    const initial=presets.find(item=>item.tool===state.tool);if(!initial)return null;
    state.prompt_configs||={};let value=state.prompt_configs[state.tool];
    if(!value||!presets.some(item=>item.id===value.preset_id&&item.tool===state.tool))value=state.prompt_configs[state.tool]={preset_id:initial.id,prompt:initial.prompt,original_prompt:initial.prompt,reference:null};
    if(value.reference&&value.reference.project_id!==state.project_id)value=state.prompt_configs[state.tool]={preset_id:initial.id,prompt:initial.prompt,original_prompt:initial.prompt,reference:null};
    const base=presets.find(item=>item.id===value.preset_id);if(!value.reference&&value.original_prompt!==base.prompt){const edited=value.prompt!==value.original_prompt;value.original_prompt=base.prompt;if(!edited)value.prompt=base.prompt;}
    return value;
  }
  function paintPrompts(){
    const config=promptConfig(),host=el('prompts');if(!host)return;host.hidden=!config;if(!config)return;
    const busy=!!state.run||starting,changed=config.prompt!==config.original_prompt;
    const available=presets.filter(item=>item.tool===state.tool),categories=[...new Set(available.map(item=>item.category||'内置风格'))];
    el('preset').innerHTML=categories.map(category=>`<optgroup label="${h(category)}">${available.filter(item=>(item.category||'内置风格')===category).map(item=>`<option value="${h(item.id)}">${h(item.name)}</option>`).join('')}</optgroup>`).join('')+(config.reference?`<optgroup label="项目模板"><option value="project">${h(config.reference.title)} · v${h(config.reference.revision)}</option></optgroup>`:'');
    el('preset').value=config.reference?'project':config.preset_id;
    el('prompt').value=config.prompt;el('prompt').disabled=busy;el('preset').disabled=busy;
    for(const name of ['pick-template','save-template']){el(name).disabled=busy||!state.project_id;el(name).title=state.project_id?'复用项目提示词模板库':'先选择结果归属项目，再使用项目模板库';}
    el('reset-prompt').disabled=busy||!changed;
    el('prompt-caption').textContent=(config.reference?'项目模板 · v'+config.reference.revision:'内置风格')+(changed?' · 已作本次修改':'')+' · 仅用于当前工具';
    el('prompt-purpose').textContent=config.reference?'复用当前项目模板，变量已填写；下方的调整仅用于本次处理。':available.find(item=>item.id===config.preset_id)?.description||'';
    el('prompt-project-hint').hidden=!!state.project_id;
  }
  async function savePrompt(){
    const project=state.project_id,tool=state.tool,config=promptConfig();if(!project||!config||state.run)return;
    const preset=presets.find(item=>item.id===config.preset_id),tags=[...new Set(['论文工具箱',tool==='translate'?'翻译':'润色',...(config.reference?config.source_tags||[]:[preset.category])])];
    const content=config.prompt;if(!content.trim())return error('请先填写要保存的风格提示词');
    const dialog=Notebook.dialog('保存为提示词模板',`<form class="nb-form"><p class="hint">保存到当前项目的提示词模板库，可在笔记、Agent 对话和工具箱中复用。</p><label>模板名称<input name="title" required maxlength="100" value="${h(tools.find(item=>item.id===state.tool)?.name+'风格模板')}"></label><button type="submit" class="btn">保存模板</button></form>`);
    dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=dialog.querySelector('[type=submit]');button.disabled=true;try{await api('/api/project/note/save',{project_id:project,path:'',title:dialog.querySelector('[name=title]').value,content,create:true,metadata:{note_type:'prompt_template',category_id:'prompt_templates',tags}});dialog.close();if(el('status'))el('status').textContent='已保存到项目提示词模板库';}catch(err){Notebook.error(dialog,err);}finally{button.disabled=false;}};
  }
  async function open(options={}){
    const ticket=++opening;
    const host=options.host;
    if(panel?.isConnected&&(!host||panel.parentElement===host)){if(state.tool==='mermaid')diagramView?.focus();else el('text').focus({preventScroll:true});return;}
    diagramView?.destroy();diagramView=null;panel?.remove();
    const selection=window.getSelection()?.toString();if(selection&&!state.run)state.text=selection;
    try{const catalog=await api('/api/paper-toolbox');tools=catalog.tools;presets=catalog.prompt_presets||[];}catch(err){uiNotify(err.message);return;}
    if(ticket!==opening||(host&&route()!=='toolbox'))return;
    panel=document.createElement(host?'section':'aside');panel.className='paper-toolbox'+(host?' ptb-page':'');panel.setAttribute('aria-label','论文工具箱');panel.setAttribute('role',host?'region':'complementary');
    panel.innerHTML=`<header class="ptb-header"><div><span class="pw-eyebrow">论文辅助</span><h2>工具箱</h2></div><button type="button" class="pw-text-button" data-tb-close aria-label="关闭论文工具箱">×</button></header><nav class="ptb-tools" aria-label="论文工具">${tools.map(tool=>`<button type="button" data-tb-tool="${tool.id}" aria-pressed="${state.tool===tool.id}">${h(tool.name)}</button>`).join('')}</nav><div class="ptb-pins" data-tb-pins></div><div class="ptb-body"><p class="hint" data-tb-description></p><div class="ptb-config"><label>结果归属<select name="project_id" class="control"><option value="">即时处理</option>${S.tasks.map(project=>`<option value="${h(project.id)}">${h(project.title)}</option>`).join('')}</select></label><label>模型<select name="model_id" class="control"><option value="">当前默认模型</option>${(S.models||[]).map(model=>`<option value="${h(model.id)}">${h(model.name)}</option>`).join('')}</select></label><label data-tb-direction-row>翻译方向<select name="direction" class="control"><option value="zh_en">中文 → English</option><option value="en_zh">English → 中文</option></select></label><label data-tb-format-row>导出格式<select name="format" class="control"><option value="docx">Word</option><option value="md">Markdown</option><option value="html">HTML</option><option value="pdf">PDF · 项目转换服务</option></select></label><label data-tb-paper-row>项目文献<select class="control" data-tb-paper></select></label></div><details data-tb-terminology-row><summary>术语约定</summary><textarea name="terminology" rows="2" class="control" placeholder="例如：solid electrolyte = 固态电解质"></textarea></details><div class="ptb-editors"><label>原文<textarea class="control" data-tb-text name="text" rows="13" placeholder="粘贴需要处理的文本、Markdown、BibTeX 或 RIS"></textarea></label><label>处理结果<textarea class="control" data-tb-output rows="13" readonly></textarea></label></div><p class="pw-error" data-tb-error role="alert"></p><p class="hint" data-tb-status role="status"></p></div><footer class="ptb-footer"><button type="button" class="pw-text-button" data-tb-pin>固定当前工具</button><span class="pw-spacer"></span><button type="button" class="btn ghost" data-tb-copy>复制结果</button><button type="button" class="btn ghost" data-tb-file hidden>打开文件</button><button type="button" class="btn ghost" data-tb-save>保存到项目</button><button type="button" class="btn ghost" data-tb-stop hidden>停止</button><button type="button" class="btn" data-tb-run>开始处理</button></footer>`;
    if(host){host.replaceChildren(panel);panel.id='paperToolboxPage';panel.querySelector('h2').textContent='论文工具箱';el('close').hidden=true;panel.querySelector('h2').insertAdjacentHTML('afterend','<p class="ptb-intro">翻译、润色、文稿整理与图表预览，使用已配置的模型与本地工具。</p>');}
    else document.body.append(panel);
    el('text').closest('.ptb-editors').insertAdjacentHTML('beforebegin','<section data-tb-mermaid hidden aria-label="Mermaid 图表工具"></section>');
    el('mermaid').insertAdjacentHTML('afterend',`<section data-tb-images hidden><label class="btn ghost">添加图片或 PDF<input type="file" data-tb-image-input multiple accept=".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf"></label><p class="hint">最多 20 个文件，每个 20 MB。PDF 按页处理。图注需支持视觉的模型。原图保留。</p><ul data-tb-image-list></ul></section><ul data-tb-downloads></ul>`);
    async function uploadImages(files){
      if(state.run||starting)return error('当前正在处理，请完成后再上传');
      if((state.images||[]).length+files.length>20)return error('最多选择 20 个文件');
      if(files.some(file=>!FileDrop.image(file)&&!/\.pdf$/i.test(file.name)))return error('图片工具支持 PNG、JPG、JPEG、WebP、TIFF 和 PDF');
      starting=true;paint();const errors=[];
      try{for(const [index,file] of files.entries()){try{if(file.size>20*1024*1024)throw new Error('超过 20 MB');el('status').textContent=`上传 ${index+1}/${files.length}：${file.name}`;const added=await api('/api/paper-toolbox/upload',{name:file.name,data:await FileDrop.dataURL(file),project_id:state.project_id});(state.images||=[]).push(added);persist();paintImages();}catch(err){errors.push(file.name+'：'+err.message);}}}finally{starting=false;paint();if(errors.length)error(errors.join('；'));}
    }
    el('image-input').onchange=event=>{const files=[...event.target.files];event.target.value='';uploadImages(files);};
    el('description').insertAdjacentHTML('afterend',`<section class="ptb-import" data-tb-import-zone aria-label="导入当前工具的文件"><span class="ptb-import-icon" aria-hidden="true">↥</span><div><strong>拖拽文件到这里</strong><p class="hint" data-tb-import-hint></p><p class="ptb-import-result" data-tb-import-result role="status"></p></div><button class="btn ghost" type="button" data-tb-choose-file>选择文件</button><input type="file" data-tb-import-input hidden></section>`);
    async function importFiles(files){
      if(state.run||starting)return error('当前正在处理，请完成后再上传');
      if(!files.length)return;
      error('');el('import-result').textContent='';
      const tool=state.tool,project=state.project_id,allowed=imports[tool][0].split(',');
      if(files.length>(tool==='mermaid'?1:20))return error(tool==='mermaid'?'Mermaid 每次导入一个文件':'每批最多 20 个文件');
      const invalid=files.find(file=>!allowed.some(suffix=>file.name.toLowerCase().endsWith(suffix))||file.size>20*1024*1024||!file.size);
      if(invalid)return error(invalid.name+'：请检查文件类型、内容和大小。'+imports[tool][1]);
      if(tool==='pdf_translate'&&!project)return error('请先选择结果归属项目，再导入 PDF');
      if(['caption','image_convert'].includes(tool))return uploadImages(files);
      const errors=[],added=[];starting=true;paint();
      try{
        for(const [index,file] of files.entries()){
          try{
            if(el('import-result'))el('import-result').textContent=`正在导入 ${index+1}/${files.length}：${file.name}`;
            const result=await api('/api/paper-toolbox/import',{tool,project_id:project,name:file.name,data:await FileDrop.dataURL(file)});
            if(tool==='mermaid')diagramView.importCode(result.text,file.name);
            else if(tool==='pdf_translate')state.paper_id=result.paper_id;
            else{
              const next=[state.text,result.text].filter(Boolean).join('\n\n');
              if(next.length>200000)throw new Error('合并后超过 200000 字符，请减少原文或拆分处理');
              state.text=next;if(el('text'))el('text').value=next;
            }
            added.push(file.name);persist();
          }catch(err){errors.push(file.name+'：'+err.message);}
        }
        if(tool==='pdf_translate')await loadPapers();
      }finally{
        starting=false;paint();
        if(el('import-result'))el('import-result').textContent=added.length?'已导入 '+added.length+' 个文件：'+added.join('、'):'';
        if(errors.length)error(errors.join('；'));
      }
    }
    el('choose-file').onclick=()=>el('import-input').click();
    el('import-input').onchange=event=>{const files=[...event.target.files];event.target.value='';importFiles(files);};
    FileDrop.bind(panel,{busy:()=>!!state.run||starting,onError:error,onFiles:importFiles});
    const headerActions=document.createElement('div');headerActions.className='ptb-header-actions';el('close').before(headerActions);
    headerActions.innerHTML='<button type="button" class="btn ghost" data-tb-manage-templates>管理模板</button>';headerActions.append(el('close'));
    el('manage-templates').onclick=()=>{
      const tool=state.tool,editable=!!promptConfig()&&!state.run&&!starting;
      PromptLibrary.open({project_id:state.project_id||curTask||'',onChanged:change=>{
        if(change.deleted||change.status&&change.status!=='active')for(const [id,config] of Object.entries(state.prompt_configs||{}))if(config.reference?.project_id===change.project_id&&config.reference?.template_id===change.template_id)delete state.prompt_configs[id];
        persist();paintPrompts();
      },...(editable?{onChoose:(reference,rendered)=>{
        if(!panel?.isConnected||state.run||starting||state.tool!==tool)throw new Error('当前工具正在运行或已切换，请稍后重新选择模板');
        if(rendered.rendered.length>20000)throw new Error('模板内容超过 20000 字符，请缩小风格指令范围');
        state.project_id=reference.project_id;panel.querySelector('[name=project_id]').value=state.project_id;
        const config=promptConfig();Object.assign(config,{reference,prompt:rendered.rendered,original_prompt:rendered.rendered,source_tags:rendered.tags||[]});persist();paintPrompts();loadPapers();
      }}:{})});
    };
    el('terminology-row').insertAdjacentHTML('beforebegin',`<section class="ptb-prompts" data-tb-prompts aria-label="风格与提示词"><div class="ptb-prompt-toolbar"><label>提示词模板<select class="control" data-tb-preset aria-label="提示词模板"></select></label><button type="button" class="btn ghost" data-tb-pick-template>从模板库选择</button><button type="button" class="btn ghost" data-tb-save-template>保存为模板</button></div><p class="hint" data-tb-prompt-caption></p><p class="hint" data-tb-prompt-project-hint>选择结果归属项目后，可使用和保存该项目的提示词模板。</p><details><summary>查看与调整本次提示词</summary><label class="ptb-prompt-editor"><span class="sr-only">本次风格提示词</span><textarea class="control" data-tb-prompt rows="4" maxlength="20000" aria-label="本次风格提示词"></textarea></label><button type="button" class="pw-text-button" data-tb-reset-prompt>恢复所选模板</button></details></section>`);
    el('prompt-caption').insertAdjacentHTML('beforebegin','<p class="hint" data-tb-prompt-purpose></p>');
    const glyphs={translate:'文 / A',polish:'Aa',references:'[ ]',convert:'↗',pdf_translate:'PDF',mermaid:'◇'};
    const captions={translate:'文本双语翻译',polish:'表达与措辞',references:'BibTeX / RIS',convert:'文稿格式导出',pdf_translate:'整篇论文翻译',mermaid:'预览与高清导出'};
    panel.querySelectorAll('[data-tb-tool]').forEach(button=>{const id=button.dataset.tbTool;button.innerHTML=`<span class="ptb-tool-icon" aria-hidden="true">${glyphs[id]||'+'}</span><span class="ptb-tool-name"><strong>${h(tools.find(tool=>tool.id===id).name)}</strong><small>${h(captions[id]||'')}</small></span>`;});
    panel.querySelectorAll('[name]').forEach(input=>{input.value=state[input.name]||'';input.oninput=()=>{state[input.name]=input.value;persist();};});
    panel.querySelector('[name=project_id]').onchange=()=>{state.project_id=panel.querySelector('[name=project_id]').value;state.images=[];paintImages();paintPrompts();persist();loadPapers();};
    el('preset').onchange=()=>{const preset=presets.find(item=>item.id===el('preset').value);if(!preset||state.run)return;state.prompt_configs[state.tool]={preset_id:preset.id,prompt:preset.prompt,original_prompt:preset.prompt,reference:null};persist();paintPrompts();};
    el('prompt').oninput=()=>{const config=promptConfig();if(config&&!state.run){config.prompt=el('prompt').value;persist();paintPrompts();}};
    el('reset-prompt').onclick=()=>{const config=promptConfig();if(config&&!state.run){config.prompt=config.original_prompt;persist();paintPrompts();}};
    el('pick-template').onclick=()=>{const project=state.project_id,tool=state.tool;PromptUI.picker(project,(reference,rendered)=>{if(state.run||project!==state.project_id||tool!==state.tool)throw new Error('工具或项目已切换，请重新选择模板');if(rendered.rendered.length>20000)throw new Error('模板内容超过 20000 字符，请缩小风格指令范围');const config=promptConfig();Object.assign(config,{reference,prompt:rendered.rendered,original_prompt:rendered.rendered,source_tags:rendered.tags||[]});persist();paintPrompts();},{applyLabel:'用于当前工具',bindings:false});};
    el('save-template').onclick=savePrompt;
    function choose(tool){if(state.run||starting)return;state.tool=tool;el('import-result').textContent='';persist();updateTool();}
    panel.querySelectorAll('[data-tb-tool]').forEach(button=>button.onclick=()=>choose(button.dataset.tbTool));
    function pins(){const ids=read('rw-paper-tool-pins',[]);el('pins').innerHTML=ids.filter(id=>tools.some(tool=>tool.id===id)).map(id=>`<button class="pw-text-button" type="button" data-tb-pinned="${id}">${h(tools.find(tool=>tool.id===id).name)}</button>`).join('');el('pins').querySelectorAll('button').forEach(button=>button.onclick=()=>choose(button.dataset.tbPinned));el('pin').textContent=ids.includes(state.tool)?'取消固定':'固定当前工具';}
    function updateTool(){const diagram=state.tool==='mermaid',images=['caption','image_convert'].includes(state.tool);const format=panel.querySelector('[name=format]');format.innerHTML=(state.tool==='image_convert'?['png','jpg','jpeg','pdf','webp','tiff']:['docx','md','html','pdf']).map(value=>`<option value="${value}">${value.toUpperCase()}</option>`).join('');format.value=state.format;if(!format.value){format.selectedIndex=0;state.format=format.value;}panel.querySelectorAll('[data-tb-tool]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tbTool===state.tool)));el('description').textContent=tools.find(tool=>tool.id===state.tool)?.description||'';el('direction-row').hidden=state.tool!=='translate';el('format-row').hidden=!['convert','image_convert'].includes(state.tool);el('terminology-row').hidden=!['translate','polish'].includes(state.tool);el('text').closest('label').hidden=['pdf_translate','image_convert'].includes(state.tool);el('text').placeholder=state.tool==='caption'?'可选：研究背景、已有图注或需要强调的信息':'粘贴需要处理的内容';el('text').closest('.ptb-editors').hidden=diagram;panel.querySelector('.ptb-config').hidden=diagram;el('mermaid').hidden=!diagram;if(diagram&&!diagramView)diagramView=MermaidTool.mount(el('mermaid'));error('');pins();loadPapers();paint();}
    el('pin').onclick=()=>{const ids=read('rw-paper-tool-pins',[]);localStorage.setItem('rw-paper-tool-pins',JSON.stringify(ids.includes(state.tool)?ids.filter(id=>id!==state.tool):[...ids,state.tool]));pins();};
    el('close').onclick=()=>{diagramView?.destroy();diagramView=null;panel.remove();panel=null;document.getElementById('paperToolbox')?.focus();};
    panel.onkeydown=event=>{if(event.key==='Escape'&&!host){event.preventDefault();event.stopPropagation();el('close').click();}};
    el('copy').onclick=()=>navigator.clipboard.writeText(state.result).then(()=>{if(el('status'))el('status').textContent='结果已复制';},()=>error('复制失败，请选中结果后复制'));
    el('file').onclick=async()=>{try{await api('/api/conv/open',{path:state.path});}catch(err){error(err.message);}};
    el('stop').onclick=async()=>{try{await api('/api/agent/cancel',{idempotency_key:state.run?.key});}catch(err){error(err.message);}};
    el('save').onclick=async()=>{if(!state.project_id)return error('请选择要保存结果的项目');const button=el('save');button.disabled=true;try{const saved=await api('/api/paper-toolbox/save',{project_id:state.project_id,run_id:state.result_run_id,title:tools.find(tool=>tool.id===state.tool)?.name||'论文工具结果'});if(el('status'))el('status').textContent='已保存：'+saved.path;}catch(err){error(err.message);}finally{if(button.isConnected)button.disabled=false;}};
    el('run').onclick=async()=>{
      if(state.run||starting)return;error('');starting=true;paint();
      try{const request={...state,image_ids:(state.images||[]).map(item=>item.asset_id),request_key:crypto.randomUUID(),paper_id:el('paper').value};delete request.result;delete request.run;delete request.prompt_configs;const config=promptConfig();if(config)Object.assign(request,{prompt_preset:config.preset_id,prompt:config.prompt,prompt_template:config.reference});const made=await api('/api/paper-toolbox/start',request);
        if(state.tool==='pdf_translate'){state.result='';state.path='';state.result_run_id='';persist();paint();el('status').textContent=made.msg||'PDF 翻译已启动，可在项目文献中查看';pollPdf(request.paper_id,state.project_id);return;}
        state.result='';state.path='';state.files=[];state.result_run_id='';state.run={key:made.request_key,api:'/api/agent',runId:made.run_id,cursor:0,status:'running',request:{task_id:made.chat_id}};persist();paint();follow();
      }catch(err){error(err.message);}finally{starting=false;paint();}
    };
    updateTool();if(state.run)follow();if(state.tool==='mermaid')diagramView?.focus();else el('text').focus({preventScroll:true});
  }
  async function pollPdf(id,project){if(!panel?.isConnected)return;try{const result=await api('/api/trans/status?id='+encodeURIComponent(id)+'&task='+project);if(el('status'))el('status').textContent=result.msg||result.state;if(result.state==='running')setTimeout(()=>pollPdf(id,project),2000);else if(result.state==='done'){state.result=result.msg||'译文 PDF 已生成，请在项目文献中打开';paint();}}catch(err){error(err.message);}}
  async function follow(){
    if(following||!state.run)return;following=true;const run=state.run;
    try{await observeAgentRun(run,event=>{
      if(event.type==='tool_result'&&el('status'))el('status').textContent=event.summary;
      if(event.type==='toolbox_result'){state.result=event.content||'';state.path=event.path||'';state.files=event.files||[];state.errors=event.errors||[];}
      if(event.type==='done'){state.result=event.content||state.result;state.result_run_id=run.runId;}
      if(event.type==='error')error(event.message||'处理失败');
      persist();paint();
    },()=>persist(),()=>{if(el('status'))el('status').textContent='连接中断，正在恢复运行记录';});
    }catch(err){error(err.message);}finally{if(state.run===run)state.run=null;following=false;persist();paint();}
  }
  async function openResult(runId,project){
    try{const data=await api('/api/paper-toolbox/result?run_id='+encodeURIComponent(runId)+'&project_id='+encodeURIComponent(project||'')),dialog=document.createElement('dialog');dialog.className='pt-dialog';dialog.setAttribute('aria-label','论文工具处理记录');
      dialog.innerHTML=`<header class="history-preview-head"><h3>${h(data.title)}</h3><button type="button" class="pw-text-button" data-result-close>关闭</button></header><p class="hint">${h({done:'处理完成',running:'正在处理',pending:'等待处理',failed:'处理失败',cancelled:'已停止'}[data.status]||data.status)}</p><pre class="history-preview">${h(data.content||'处理结果将在完成后保存。可关闭此窗口，在看板中跟踪进度。')}</pre>${data.path?'<button type="button" class="btn" data-result-file>打开转换文件</button>':''}`;
      dialog.querySelector('[data-result-close]').onclick=()=>dialog.close();const file=dialog.querySelector('[data-result-file]');if(file)file.onclick=()=>api('/api/conv/open',{path:data.path}).catch(err=>uiNotify(err.message));dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
      if(data.prompt)dialog.querySelector('.history-preview').insertAdjacentHTML('beforebegin',`<details class="pt-preview"><summary>本次提示词 · ${h(data.prompt.title)}${data.prompt.revision?' · v'+h(data.prompt.revision):''}${data.prompt.modified?' · 本次已修改':''}</summary><pre>${h(data.prompt.text)}</pre></details>`);
    }catch(err){uiNotify(err.message);}
  }
  return {open,openResult,render:host=>open({host})};
})();
