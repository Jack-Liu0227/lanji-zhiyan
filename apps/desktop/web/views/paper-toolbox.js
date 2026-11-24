/* Global paper helpers use the same model profiles and durable run stream. */
/* FileDrop 由 views/file-drop.js 提供（2.0 同步时新增），此处不再重复声明，否则顶层 const 冲突整份脚本不执行 */
const PaperToolbox=(()=>{
  const h=value=>esc(String(value??''));
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))||fallback;}catch{return fallback;}};
  let panel=null,diagramView=null,following=false,starting=false,opening=0,tools=[],presets=[],documentConversion=null,state=read('rw-paper-toolbox',{tool:'translate',text:'',direction:'zh_en',lang:'en',format:'docx',dpi:'300',project_id:'',model_id:'',terminology:'',result:'',run:null,result_run_id:''});
  if(!state.dpi)state.dpi='300';
  let icvSig='';
  const el=name=>panel?.querySelector('[data-tb-'+name+']');
  const imports={
    caption:['.png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf','PNG、JPG、WebP、TIFF 或 PDF · 每批最多 20 个，PDF 最多 30 页'],
    image_convert:['.png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf','PNG、JPG、WebP、TIFF 或 PDF · 每批最多 20 个，PDF 最多 30 页'],
    translate:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    polish:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    convert:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 提取后追加到原文'],
    references:['.bib,.bibtex,.ris,.txt','BibTeX、RIS 或 TXT · 追加到原文，请保持同一种引文格式'],
    compliance:['.txt,.md,.markdown,.tex,.docx,.pdf','TXT、Markdown、LaTeX、Word 或文字型 PDF · 正文与文末参考文献一起导入'],
    pdf_translate:['.pdf','PDF · 先选择结果归属项目，导入文献后选择一篇启动翻译'],
    mermaid:['.mmd,.mermaid,.md,.txt','Mermaid、Markdown 或 TXT · 每次 1 个，替换当前图表源码'],
  };
  const documentMode=()=>state.tool==='convert'&&state.convertMode==='document'&&documentConversion?.available;
  function activeImport(){
    return documentMode()?[documentConversion.inputs.map(ext=>'.'+ext).join(','),'DOCX、Markdown、HTML 或 LaTeX · 每次 1 个原文件']:imports[state.tool];
  }
  function fileUrl(file,project){
    return '/api/paper-toolbox/file?asset_id='+encodeURIComponent(file.asset_id)+'&project_id='+encodeURIComponent(file.project_id??project??'');
  }
  function paintImport(){
    if(!el('import-input'))return;
    const config=activeImport(),busy=!!state.run||starting,direct=documentMode();
    el('import-input').accept=config[0];el('import-input').multiple=state.tool!=='mermaid'&&!direct;
    el('convert-mode-row').hidden=state.tool!=='convert';
    el('document-mode').checked=direct;
    el('document-mode').disabled=busy||!documentConversion?.available;
    el('document-help').textContent=documentConversion?.available?'本机 Pandoc':'未检测到 Pandoc';
    el('document-help').href='https://pandoc.org/installing.html';
    el('document-help').hidden=!!documentConversion?.available;
    el('document-remove').hidden=!direct||!state.document;
    el('document-remove').disabled=busy;
    el('document-selected').textContent=direct&&state.document?state.document.name:'';
    const file=state.document_result&&state.files?.[0];
    el('document-download').hidden=!file||state.tool!=='convert';
    if(file)el('document-download').href=fileUrl(file,state.project_id);
    if(direct&&!state.document)el('run').disabled=true;
    if(state.tool==='convert'&&file)el('save').hidden=true;
    el('import-input').disabled=busy;el('choose-file').disabled=busy;el('paper').disabled=busy;
    el('import-hint').textContent=config[1]+' · 每个文件最多 20 MB';
    panel.dataset.dropLabel='松开鼠标导入到「'+(tools.find(item=>item.id===state.tool)?.name||'当前工具')+'」';
    el('import-zone').setAttribute('aria-busy',String(busy));
  }
  function persist(){try{localStorage.setItem('rw-paper-toolbox',JSON.stringify(state));}catch{}}
  function error(message){if(el('error'))el('error').textContent=message||'';}
  const CHIP_KEY={project_id:'项目',model_id:'模型',direction:'方向',format:'格式',dpi:'分辨率',cite_style:'引文',lang:'输出'};
  const CHIP_CHEV='<svg class="ptb-chip-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m7 10 5 5 5-5"/></svg>';
  const CHIP_CHECK='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
  function chipLabel(select){const label=select.closest('label');if(!label||label.dataset.chip==='1')return;label.dataset.chip='1';
    const key=CHIP_KEY[select.name]||'模板';if(label.lastElementChild!==select){}const span=document.createElement('span');span.className='ptb-chip-k';span.textContent=key;
    select.before(span);label.insertAdjacentHTML('beforeend',CHIP_CHEV);label.title='点击选择'+key;}
  function closeChip(){if(!panel)return;panel.querySelectorAll('.ptb-chip-pop').forEach(box=>box.remove());}
  function openChip(label){
    const select=label.querySelector('select');if(!select)return;closeChip();
    const box=document.createElement('div');box.className='ptb-chip-pop';
    const head=(CHIP_KEY[select.name]||'模板');
    box.innerHTML=`<div class="pp-h">选择${head}</div>`+[...select.options].map((option,index)=>`<button type="button" class="ptb-chip-opt${option.selected?' on':''}" data-chip-index="${index}"><span>${h(option.textContent.trim())}</span>${CHIP_CHECK}</button>`).join('');
    panel.append(box);
    const rect=label.getBoundingClientRect(),boxRect=box.getBoundingClientRect();
    box.style.left=Math.max(8,Math.min(rect.left,innerWidth-boxRect.width-8))+'px';
    box.style.top=(rect.bottom+boxRect.height+10>innerHeight?rect.top-boxRect.height-6:rect.bottom+6)+'px';
    box.querySelectorAll('[data-chip-index]').forEach(button=>button.onclick=()=>{select.selectedIndex=Number(button.dataset.chipIndex);select.dispatchEvent(new Event('change',{bubbles:true}));closeChip();});
    box.addEventListener('pointerdown',event=>event.stopPropagation());
  }
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('.ptb-chip-pop'))closeChip();},true);
  document.addEventListener('click',event=>{const label=event.target.closest('.ptb-config>label,.ptb-prompt-toolbar>label');if(!label||!label.querySelector('select'))return;event.preventDefault();openChip(label);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeChip();});
  function chipify(){panel.querySelectorAll('.ptb-config>label>select,.ptb-prompt-toolbar>label>select').forEach(chipLabel);}

  function paint(){if(!panel?.isConnected)return;chipify();const busy=!!state.run||starting,diagram=state.tool==='mermaid';panel.querySelectorAll('[name]').forEach(input=>input.disabled=busy);el('run').disabled=busy;el('stop').hidden=diagram||!state.run;el('output').value=state.result||'';el('copy').disabled=!state.result;el('save').disabled=!state.result||!state.result_run_id;el('file').hidden=diagram||!state.path||state.tool==='convert';el('status').textContent=starting?'正在提交处理请求':state.run?'执行中 · 可以关闭面板，后台继续运行':state.result?(state.errors?.length?'部分完成 · '+state.errors.length+' 个文件未完成':'处理完成'):'使用已配置模型处理文本，或使用本地整理与转换工具';for(const name of ['run','copy','status'])el(name).hidden=diagram;el('save').hidden=diagram;const tin=el('text'),mIn=el('meta-in'),mOut=el('meta-out');if(mIn)mIn.textContent=tin&&tin.value?tin.value.length+' 字':'';if(mOut)mOut.textContent=state.result?state.result.length+' 字':'';paintPrompts();paintImages();paintImport();paintIssues();}
  function paintIssues(){
    const host=el('issues'),out=el('output');if(!host||!out)return;
    const on=state.tool==='compliance'&&!!state.result;
    host.hidden=!on;out.hidden=on;
    if(!on){host.innerHTML='';return;}
    let n=0,html='';
    for(const raw of String(state.result).split('\n')){
      const line=raw.trim();if(!line)continue;
      if(line.startsWith('- ')){n++;html+=`<div class="ptb-issue"><i>${n}</i><div>${h(line.slice(2).replace(/\*\*/g,''))}</div></div>`;}
      else if(line.startsWith('#'))html+=`<p class="ptb-issue-h">${h(line.replace(/^#+\s*/,'').replace(/\*\*/g,''))}</p>`;
      else if(!line.startsWith('本工具按固定规则'))html+=`<p class="ptb-issue-p">${h(line.replace(/\*\*/g,''))}</p>`;
    }
    host.innerHTML=html||`<p class="ptb-issue-p">未发现硬性问题。</p>`;
  }
  function fileExt(name){const suffix=String(name||'').split('.').pop().toUpperCase();return suffix.length<=4?suffix:'FILE';}
  function paintImages(){
    if(!el('images'))return;
    el('images').hidden=!['image_convert','convert'].includes(state.tool);
    el('image-input').disabled=!!state.run||starting;
    const images=state.images||[],busy=!!state.run||starting;
    /* 已产出：state.files 是唯一来源，toolbox_file 事件逐条追加进来 */
    const files=(state.files||[]).filter(file=>file.path);
    const convIn=state.tool==='convert',items=convIn?(state.document?[{name:state.document.name}]:(state.conv_in||[])):images;
    const total=convIn?items.length:images.reduce((sum,item)=>sum+(Number(item.pages)||1),0),failedCount=(state.errors||[]).length;
    /* 网格只在「文件集合变了」时重建，否则每次事件重绘会中断缩略图加载 */
    const signature=(convIn?items.map(item=>item.name).join('|'):items.map(item=>item.asset_id).join('|'))+'~'+files.map(file=>file.path||file.asset_id).join('|')+'~'+failedCount+'~'+(state.fresh_asset||'');
    if(el('icv-in')&&signature!==icvSig){
      icvSig=signature;
      el('icv-in').innerHTML=(convIn?items.map(item=>`<div class="icv-t"><span class="icv-th"><span class="icv-tag">${h(fileExt(item.name))}</span></span><span class="icv-fn" title="${h(item.name)}">${h(item.name)}</span></div>`).join(''):items.map((item,index)=>`<div class="icv-t"><span class="icv-th"><img data-src="${previewURL(item.asset_id,1)}" alt=""><span class="icv-tag">${h(fileExt(item.name))}</span></span><span class="icv-fn" title="${h(item.name)}">${h(item.name)}</span><button type="button" class="icv-x" data-icv-drop="${index}" aria-label="移除 ${h(item.name)}" ${busy?'disabled':''}>×</button></div>`).join(''))
        +(items.length<20?`<label class="icv-add" for="icv-file"><span class="icv-addbox" aria-hidden="true">＋</span><span class="icv-fn">拖入或点选</span></label>`:'');
      el('icv-in').querySelectorAll('img[data-src]').forEach(img=>{img.src=img.dataset.src;});
      el('icv-in').querySelectorAll('[data-icv-drop]').forEach(button=>button.onclick=()=>{state.images.splice(Number(button.dataset.icvDrop),1);persist();paintImages();});
      const failed=(state.errors||[]);
      el('icv-out').innerHTML=files.map(file=>{const parts=String(file.name||'').split(' → ');const format=(parts[1]||fileExt(file.path)).toLowerCase();
        const body=`<span class="icv-th">${convIn?'':`<img data-src="${previewURL(file.asset_id,1)}" alt="">`}<span class="icv-tag is-out">${h(format.toUpperCase())}</span></span><span class="icv-fn">${h(parts[0]||file.name)}</span>`;
        const fresh=file.asset_id===state.fresh_asset?' is-fresh':'';
        return file.asset_id
          ?`<a class="icv-t${fresh}" href="/api/paper-toolbox/file?asset_id=${encodeURIComponent(file.asset_id)}&project_id=${encodeURIComponent(file.project_id??state.project_id??'')}" download title="${h(file.name)}">${body}</a>`
          :`<button type="button" class="icv-t${fresh}" data-conv-open="${h(file.path)}" title="${h(file.name)}">${body}</button>`;}).join('')
        +failed.map(item=>`<div class="icv-t is-bad" title="${h(item.error)}"><span class="icv-th"><span class="icv-tag is-bad">!</span></span><span class="icv-fn">${h(item.name)}</span><span class="icv-why">${h(item.error||'未完成')}</span></div>`).join('')
        ||`<p class="icv-empty">转换完成后，${convIn?'文件':'每一张'}都会出现在这里</p>`;
      el('icv-out').querySelectorAll('img[data-src]').forEach(img=>{img.src=img.dataset.src;});
      el('icv-out').querySelectorAll('[data-conv-open]').forEach(button=>button.onclick=()=>api('/api/conv/open',{path:button.dataset.convOpen}).catch(err=>error(err.message)));
    }
    el('icv-in-n').textContent=items.length?(convIn?`${items.length} 个`:`${items.length} 个 · 共 ${total} 页`):'空';
    el('icv-out-n').textContent=(files.length||failedCount)+' / '+(total||0);
    const pct=total?Math.min(100,Math.round(files.length/total*100)):0;
    if(el('icv-bar'))el('icv-bar').style.width=pct+'%';
    el('icv-prog').textContent=state.run?(convIn?`转换中 · ${files.length} 个`:`转换中 · ${files.length} / ${total} 页`):(files.length?(convIn?`已产出 ${files.length} 个`:`${files.length} / ${total} 页`):'等待开始');
    if(el('icv-in-note'))el('icv-in-note').textContent=convIn?'DOCX · MD · HTML · PDF · 导入后合并为原文':'PNG · JPG · WebP · TIFF · PDF';
    el('downloads').innerHTML=(state.files||[]).map(file=>`<li><a href="/api/paper-toolbox/file?asset_id=${encodeURIComponent(file.asset_id)}&project_id=${encodeURIComponent(file.project_id??state.project_id??'')}" download>${h(file.name)}</a></li>`).join('');
    paintCaption();
  }
  function cbKey(item){return String(item.asset_id||'')+'#'+String(item.page||1);}
  function previewURL(assetId,page){return '/api/paper-toolbox/preview?asset_id='+encodeURIComponent(assetId)+'&project_id='+encodeURIComponent(state.project_id||'')+'&page='+(page||1);}
  function captionItems(){return state.caption_items||[];}
  function paintCaption(){
    if(!el('caption'))return;
    const images=state.images||[],items=captionItems(),busy=!!state.run||starting;
    el('cb-count').textContent=images.length?images.length+' / 20':'';
    let sel=state.caption_sel&&images.some(item=>item.asset_id===state.caption_sel.asset_id)?state.caption_sel:(images[0]?{asset_id:images[0].asset_id,page:1}:null);
    const stage=el('cb-stage');
    if(!sel)stage.innerHTML='<p class="hint">先添加图片或 PDF，预览会出现在这里</p>';
    else{
      const pages=Number(images.find(item=>item.asset_id===sel.asset_id)?.pages)||1,page=Math.min(Math.max(1,Number(sel.page)||1),pages);
      sel={asset_id:sel.asset_id,page};
      stage.innerHTML=`<img src="${previewURL(sel.asset_id,page)}" alt="图片预览" loading="lazy">${pages>1?`<div class="ptb-cb-pager"><button type="button" class="pw-text-button" data-cb-page="-1" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${pages}</span><button type="button" class="pw-text-button" data-cb-page="1" ${page>=pages?'disabled':''}>下一页</button></div>`:''}`;
      stage.querySelectorAll('[data-cb-page]').forEach(button=>button.onclick=()=>{state.caption_sel={asset_id:sel.asset_id,page:page+Number(button.dataset.cbPage)};persist();paintCaption();});
    }
    state.caption_sel=sel;
    el('cb-film').innerHTML=images.map((item,index)=>{const on=!!sel&&item.asset_id===sel.asset_id;const done=items.some(it=>it.asset_id===item.asset_id&&it.content),bad=items.some(it=>it.asset_id===item.asset_id&&it.error);
      return `<li class="ptb-cb-card${on?' is-on':''}"><button type="button" class="ptb-cb-thumb" data-cb-pick="${index}" aria-pressed="${on}" title="${h(item.name)}"><img src="${previewURL(item.asset_id,1)}" alt="" loading="lazy"><span class="ptb-cb-dot${done?' is-ok':bad?' is-bad':''}"></span></button><span class="ptb-cb-name">${h(item.name)}</span><button type="button" class="ptb-cb-del" data-cb-drop="${index}" aria-label="移除 ${h(item.name)}" ${busy?'disabled':''}>×</button></li>`;}).join('')+(images.length<20?`<li class="ptb-cb-addli"><label class="ptb-cb-add" title="添加图片或 PDF">＋<span>添加</span><input type="file" hidden data-cb-input multiple accept=".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf"></label></li>`:'');
    el('cb-film').querySelectorAll('[data-cb-pick]').forEach(button=>button.onclick=()=>{state.caption_sel={asset_id:images[Number(button.dataset.cbPick)].asset_id,page:1};persist();paintCaption();});
    el('cb-film').querySelectorAll('[data-cb-drop]').forEach(button=>button.onclick=()=>{state.images.splice(Number(button.dataset.cbDrop),1);if(state.caption_sel&&!state.images.some(item=>item.asset_id===state.caption_sel.asset_id))state.caption_sel=null;persist();paintImages();});
    const add=el('cb-film').querySelector('[data-cb-input]');if(add)add.onchange=event=>{const files=[...event.target.files];event.target.value='';uploadImages(files);};
    if(el('cb-text')&&document.activeElement!==el('cb-text'))el('cb-text').value=state.text||'';
    el('cb-sum').textContent=items.filter(it=>it.content).length&&items.length?`${items.filter(it=>it.content).length} / ${items.length} 条${state.run?' · 处理中':''}`:'';
    el('cb-cards').innerHTML=items.length?items.map(it=>`<li class="ptb-cb-card${it.error?' is-bad':''}"><div class="ptb-cb-card-head"><b>${h(it.label)}</b><span class="ptb-cb-tag">${it.error?'失败':'已生成'}</span></div><div class="ptb-cb-card-body">${h(it.error||it.content)}</div><div class="ptb-cb-card-foot"><button type="button" class="pw-text-button" data-cb-copy="${h(cbKey(it))}" ${it.content?'':'disabled'}>复制</button><button type="button" class="pw-text-button" data-cb-regen="${h(it.asset_id)}" ${busy?'disabled':''}>重新生成</button></div></li>`).join(''):`<li class="hint">${images.length?'点「开始处理」生成图注：每张图一条，PDF 按页。':'先添加图片或 PDF。'}</li>`;
    el('cb-cards').querySelectorAll('[data-cb-copy]').forEach(button=>button.onclick=()=>{const it=captionItems().find(entry=>cbKey(entry)===button.dataset.cbCopy);if(!it)return;navigator.clipboard.writeText(it.content).then(()=>{el('status').textContent='已复制：'+it.label;},()=>error('复制失败，请手动选择文本'));});
    el('cb-cards').querySelectorAll('[data-cb-regen]').forEach(button=>button.onclick=()=>runTool([button.dataset.cbRegen]));
  }
  async function loadPapers(){
    if(!panel?.isConnected)return;
    const needsPaper=state.tool==='pdf_translate';el('paper-row').hidden=!needsPaper;if(!needsPaper)return;
    const project=state.project_id;
    if(!project){el('paper').innerHTML='<option value="">请先选择项目</option>';return;}
    try{
      const data=await api('/api/task?id='+encodeURIComponent(project));
      if(!panel?.isConnected||state.project_id!==project||state.tool!=='pdf_translate')return;
      el('paper').innerHTML=(data.paper_list||[]).map(paper=>`<option value="${h(paper.id)}">${h(paper.title||paper.filename)}</option>`).join('')||'<option value="">当前项目没有已入库 PDF</option>';
      if((data.paper_list||[]).some(paper=>paper.id===state.paper_id))el('paper').value=state.paper_id;
      el('paper').onchange=()=>{state.paper_id=el('paper').value;persist();paint();};
      paint();
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
    const config=promptConfig(),host=el('prompts');if(!host)return;host.hidden=true;if(!config)return;
    const busy=!!state.run||starting,changed=config.prompt!==config.original_prompt;
    const available=presets.filter(item=>item.tool===state.tool),categories=[...new Set(available.map(item=>item.category||'内置风格'))];
    el('preset').innerHTML=categories.map(category=>`<optgroup label="${h(category)}">${available.filter(item=>(item.category||'内置风格')===category).map(item=>`<option value="${h(item.id)}">${h(item.name)}</option>`).join('')}</optgroup>`).join('');
    el('preset').value=config.preset_id;
    el('prompt').value=config.prompt;el('prompt').disabled=busy;el('preset').disabled=busy;
    el('reset-prompt').disabled=busy||!changed;
    el('prompt-caption').textContent=(config.reference?'项目模板 · v'+config.reference.revision:'内置风格')+(changed?' · 已作本次修改':'')+' · 仅用于当前工具';
    el('prompt-purpose').textContent=config.reference?'复用当前项目模板，变量已填写；下方的调整仅用于本次处理。':available.find(item=>item.id===config.preset_id)?.description||'';
  }
  async function open(options={}){
    const ticket=++opening;
    const host=options.host;
    if(panel?.isConnected&&(!host||panel.parentElement===host)){if(state.tool==='mermaid')diagramView?.focus();else el('text').focus({preventScroll:true});return;}
    diagramView?.destroy();diagramView=null;PaperTranslation?.unmount?.();panel?.remove();
    const selection=window.getSelection()?.toString();if(selection&&!state.run)state.text=selection;
    try{const catalog=await api('/api/paper-toolbox');tools=catalog.tools;presets=catalog.prompt_presets||[];documentConversion=catalog.document_conversion;}catch(err){uiNotify(err.message);return;}
    if(!tools.some(tool=>tool.id===state.tool))state.tool=tools[0]?.id||'translate';
    if(ticket!==opening||(host&&route()!=='toolbox'))return;
    panel=document.createElement(host?'section':'aside');panel.className='paper-toolbox'+(host?' ptb-page':'');panel.setAttribute('aria-label','论文工具箱');panel.setAttribute('role',host?'region':'complementary');
    panel.innerHTML=`<header class="ptb-header"><div><span class="pw-eyebrow">论文辅助</span><h2>工具箱</h2></div><button type="button" class="pw-text-button" data-tb-close aria-label="关闭论文工具箱">×</button></header><nav class="ptb-tools" aria-label="论文工具">${tools.map(tool=>`<button type="button" data-tb-tool="${tool.id}" aria-pressed="${state.tool===tool.id}">${h(tool.name)}</button>`).join('')}</nav><div class="ptb-pins" data-tb-pins></div><div class="ptb-body"><p class="hint" data-tb-description></p><div class="ptb-config"><label><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg></span><select name="project_id" class="control"><option value="">即时处理</option>${S.tasks.map(project=>`<option value="${h(project.id)}">${h(project.title)}</option>`).join('')}</select></label><label><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4.5 13H11l-1 9 8.5-11H12z"/></svg></span><select name="model_id" class="control"><option value="">当前默认模型</option>${(S.models||[]).map(model=>`<option value="${h(model.id)}">${h(model.name)}</option>`).join('')}</select></label><label data-tb-direction-row><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h8M3 11h5"/><path d="M14 13h7M18 17h3"/></svg></span><select name="direction" class="control"><option value="zh_en">中文 → English</option><option value="en_zh">English → 中文</option></select></label><label data-tb-format-row><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/><path d="M9 14l3 3 3-3"/></svg></span><select name="format" class="control"><option value="docx">Word</option><option value="md">Markdown</option><option value="html">HTML</option><option value="pdf">PDF · 项目转换服务</option></select></label><label data-tb-dpi-row hidden><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9V5a2 2 0 012-2h4M21 9V5a2 2 0 00-2-2h-4M3 15v4a2 2 0 002 2h4M21 15v4a2 2 0 01-2 2h-4"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg></span><select name="dpi" class="control"><option value="180">180 DPI · 屏幕</option><option value="300">300 DPI · 投稿</option><option value="600">600 DPI · 印刷</option></select></label><label data-tb-style-row><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h10M9 5v14M6 19h6"/><path d="M15 12h6M18 9v6"/></svg></span><select name="cite_style" class="control"><option value="">不排格式</option><option value="gb7714">GB/T 7714</option><option value="nature">Nature</option><option value="ieee">IEEE</option></select></label><label data-tb-paper-row><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/><path d="M9 12h6M9 16h4"/></svg></span><select class="control" data-tb-paper></select></label><label data-tb-lang-row><span class="ptb-ico" aria-hidden="true"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h9M8.5 6v2c0 4-2 6-5 7"/><path d="M6 11c1.5 3 4 5 7 6"/><path d="M13.5 20l3.5-9 3.5 9M15 17h4"/></svg></span>输出<select name="lang" class="control"><option value="en">English</option><option value="zh">中文</option></select></label></div><details data-tb-terminology-row><summary>术语约定</summary><textarea name="terminology" rows="2" class="control" placeholder="例如：solid electrolyte = 固态电解质"></textarea></details><div class="ptb-editors"><div class="ptb-cap">原文<span data-tb-meta-in></span></div><div class="ptb-cap">处理结果<span data-tb-meta-out></span></div><label class="ptb-pane"><span class="sr-only">原文</span><textarea class="control" data-tb-text name="text" rows="13" placeholder="粘贴需要处理的文本、Markdown、BibTeX 或 RIS"></textarea></label><label class="ptb-pane out"><span class="sr-only">处理结果</span><textarea class="control" data-tb-output rows="13" readonly></textarea></label></div><p class="pw-error" data-tb-error role="alert"></p><p class="hint" data-tb-status role="status"></p></div><footer class="ptb-footer"><button type="button" class="pw-text-button" data-tb-pin>固定当前工具</button><span class="pw-spacer"></span><button type="button" class="btn ghost" data-tb-copy>复制结果</button><button type="button" class="btn ghost" data-tb-file hidden>打开文件</button><button type="button" class="btn ghost" data-tb-save>保存到项目</button><button type="button" class="btn ghost" data-tb-stop hidden>停止</button><button type="button" class="btn" data-tb-run>开始处理</button></footer>`;
    if(host){host.replaceChildren(panel);panel.id='paperToolboxPage';panel.querySelector('h2').textContent='论文工具箱';el('close').hidden=true;panel.querySelector('h2').insertAdjacentHTML('afterend','<p class="ptb-intro">翻译、逐段润色、引文整理、合规预检、文稿转换与图表预览，使用已配置的模型与本地工具。</p>');}
    else document.body.append(panel);
    el('text').closest('.ptb-editors').insertAdjacentHTML('beforebegin','<section class="ptb-pdfread" data-tb-pdfread hidden aria-label="PDF 论文翻译"></section>');
    el('text').closest('.ptb-editors').insertAdjacentHTML('beforebegin','<section data-tb-mermaid hidden aria-label="Mermaid 图表工具"></section>');
    el('mermaid').insertAdjacentHTML('afterend',`<section class="icv" data-tb-images hidden><input type="file" id="icv-file" data-tb-image-input multiple hidden accept=".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf"><ul data-tb-image-list hidden></ul>
      <div class="icv-card">
        <header class="icv-h"><span class="icv-ico" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg></span><b>待转换</b><span class="icv-n" data-tb-icv-in-n></span><label class="icv-btn" for="icv-file">添加</label></header>
        <div class="icv-b"><div class="icv-grid" data-tb-icv-in></div></div>
        <footer class="icv-f"><span data-tb-icv-in-note>PNG · JPG · WebP · TIFF · PDF</span><span class="icv-r">单个 ≤ 20 MB · PDF ≤ 30 页</span></footer>
      </div>
      <div class="icv-card">
        <header class="icv-h"><span class="icv-ico" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v16H4z"/><path d="m8 14 3-3 3 3 2-2"/></svg></span><b>已产出</b><span class="icv-n" data-tb-icv-out-n>0 / 0</span></header>
        <div class="icv-b"><div class="icv-grid" data-tb-icv-out></div></div>
        <footer class="icv-f"><span class="icv-bar"><i data-tb-icv-bar></i></span><span data-tb-icv-prog></span></footer>
      </div>
    </section><ul data-tb-downloads hidden></ul>
    <section class="ptb-cb" data-tb-caption hidden>
      <div class="ptb-cb-left">
        <div class="ptb-cv">
          <label class="ptb-cv-top" data-cb-drop-wrap><span class="ptb-cv-ico" aria-hidden="true">＋</span><span><b>添加图片或 PDF</b><em>拖拽到任意位置</em></span><span class="ptb-cv-n" data-tb-cb-count></span><input type="file" data-cb-drop-input hidden multiple accept=".png,.jpg,.jpeg,.webp,.tif,.tiff,.pdf"></label>
          <div class="ptb-cb-stage" data-tb-cb-stage><p class="hint">先添加图片或 PDF，预览会出现在这里</p></div>
        </div>
        <ul class="ptb-cb-film" data-tb-cb-film></ul>
      </div>
      <div class="ptb-cb-right">
        <label class="ptb-cb-brief"><span>原文 / 背景</span><textarea class="control" data-tb-cb-text rows="3" placeholder="可选：研究背景、已有图注或需要强调的信息"></textarea></label>
        <div class="ptb-cb-results">
          <div class="ptb-cb-rhead"><b>处理结果</b><span data-tb-cb-sum></span></div>
          <ul class="ptb-cb-cards" data-tb-cb-cards></ul>
        </div>
      </div>
    </section>`);
    async function uploadImages(files){
      if(state.run||starting)return error('当前正在处理，请完成后再上传');
      if((state.images||[]).length+files.length>20)return error('最多选择 20 个文件');
      if(files.some(file=>!FileDrop.image(file)&&!/\.pdf$/i.test(file.name)))return error('图片工具支持 PNG、JPG、JPEG、WebP、TIFF 和 PDF');
      starting=true;paint();const errors=[];
      try{for(const [index,file] of files.entries()){try{if(file.size>20*1024*1024)throw new Error('超过 20 MB');el('status').textContent=`上传 ${index+1}/${files.length}：${file.name}`;const added=await api('/api/paper-toolbox/upload',{name:file.name,data:await FileDrop.dataURL(file),project_id:state.project_id});(state.images||=[]).push(added);persist();paintImages();}catch(err){errors.push(file.name+'：'+err.message);}}}finally{starting=false;paint();if(errors.length)error(errors.join('；'));}
    }
    el('image-input').onchange=event=>{const files=[...event.target.files];event.target.value='';if(state.tool==='convert')return importFiles(files);uploadImages(files);};
    const cbDropInput=panel.querySelector('[data-cb-drop-input]');if(cbDropInput)cbDropInput.onchange=event=>{const files=[...event.target.files];event.target.value='';uploadImages(files);};
    el('description').insertAdjacentHTML('afterend',`<section class="ptb-import" data-tb-import-zone aria-label="导入当前工具的文件"><span class="ptb-import-icon" aria-hidden="true">↥</span><div><strong>拖拽文件到这里</strong><p class="hint" data-tb-import-hint></p><p class="ptb-import-result" data-tb-import-result role="status"></p></div><button class="btn ghost" type="button" data-tb-choose-file>选择文件</button><input type="file" data-tb-import-input hidden></section>`);
    el('import-zone').insertAdjacentHTML('beforebegin',`<div class="ptb-config" data-tb-convert-mode-row hidden>
      <label><input type="checkbox" data-tb-document-mode> 原文件转换</label>
      <a data-tb-document-help target="_blank" rel="noopener"></a>
      <span data-tb-document-selected></span><button type="button" class="pw-text-button" data-tb-document-remove hidden>移除文件</button></div>`);
    el('file').insertAdjacentHTML('afterend','<a class="btn ghost" data-tb-document-download download hidden>下载转换文件</a>');
    {const modeRow=el('convert-mode-row'),icvHead=el('images')?.querySelector('.icv-h');if(modeRow&&icvHead)icvHead.insertBefore(modeRow,icvHead.querySelector('.icv-btn'));}
    {const capIn=panel.querySelector('.ptb-editors > .ptb-cap');if(capIn&&!capIn.querySelector('[data-tb-pane-import]')){capIn.insertAdjacentHTML('beforeend','<label class="ptb-cap-link" data-tb-pane-import>导入<input type="file" hidden multiple accept=".txt,.md,.markdown,.tex,.docx,.pdf" data-tb-pane-file></label>');capIn.querySelector('[data-tb-pane-file]').onchange=event=>{const files=[...event.target.files];event.target.value='';importFiles(files);};}}
    {const outPane=panel.querySelector('.ptb-editors > .ptb-pane.out');if(outPane&&!outPane.querySelector('[data-tb-issues]'))outPane.insertAdjacentHTML('beforeend','<div class="ptb-issues" data-tb-issues hidden></div>');}
    el('document-mode').onchange=()=>{state.convertMode=el('document-mode').checked?'document':'text';persist();updateTool();};
    el('document-remove').onclick=()=>{state.document=null;persist();updateTool();};
    async function importFiles(files){
      if(state.run||starting)return error('当前正在处理，请完成后再上传');
      if(!files.length)return;
      error('');el('import-result').textContent='';
      const tool=state.tool,project=state.project_id,direct=documentMode();
      const config=activeImport(),allowed=config[0].split(',');
      if(files.length>(tool==='mermaid'||direct?1:20))return error(tool==='mermaid'||direct?'每次请选择一个文件':'每批最多 20 个文件');
      const invalid=files.find(file=>!allowed.some(suffix=>file.name.toLowerCase().endsWith(suffix))||file.size>20*1024*1024||!file.size);
      if(invalid)return error(invalid.name+'：请检查文件类型、内容和大小。'+config[1]);
      if(tool==='pdf_translate'&&!project)return error('请先选择结果归属项目，再导入 PDF');
      if(['caption','image_convert'].includes(tool))return uploadImages(files);
      const errors=[],added=[];starting=true;paint();
      try{
        for(const [index,file] of files.entries()){
          try{
            if(el('import-result'))el('import-result').textContent=`正在导入 ${index+1}/${files.length}：${file.name}`;
            const result=await api('/api/paper-toolbox/import',{tool,mode:direct?'document':'text',project_id:project,name:file.name,data:await FileDrop.dataURL(file)});
            if(direct){state.document=result;updateTool();}
            else if(tool==='mermaid')diagramView.importCode(result.text,file.name);
            else if(tool==='pdf_translate')state.paper_id=result.paper_id;
            else{
              const next=[state.text,result.text].filter(Boolean).join('\n\n');
              if(next.length>200000)throw new Error('合并后超过 200000 字符，请减少原文或拆分处理');
              state.text=next;if(el('text'))el('text').value=next;
              if(tool==='convert')(state.conv_in||=[]).push({name:file.name});
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
    headerActions.append(el('close'));
    el('terminology-row').insertAdjacentHTML('beforebegin',`<section class="ptb-prompts" data-tb-prompts aria-label="风格与提示词"><div class="ptb-prompt-toolbar"><label>提示词模板<select class="control" data-tb-preset aria-label="提示词模板"></select></label></div><p class="hint" data-tb-prompt-caption></p><details><summary>查看与调整本次提示词</summary><label class="ptb-prompt-editor"><span class="sr-only">本次风格提示词</span><textarea class="control" data-tb-prompt rows="4" maxlength="20000" aria-label="本次风格提示词"></textarea></label><button type="button" class="pw-text-button" data-tb-reset-prompt>恢复所选模板</button></details></section>`);
    el('prompt-caption').insertAdjacentHTML('beforebegin','<p class="hint" data-tb-prompt-purpose></p>');
    const ICONS={caption:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M7 20h10M7 9h6"/></svg>',image_convert:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="12" height="12" rx="2"/><path d="M9 9h12v12H9z"/></svg>',translate:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h8M3 11h5"/><path d="M14 13h7M18 17h3"/><path d="M12 3l-3 7h2.4l-.9 8 5.5-9H13.4l1.6-6z"/></svg>',polish:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 4c-6 1-9 4.4-10 9l3 3c4.6-1 8-4 10-10"/><path d="M4 20l5-5"/></svg>',references:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3h10a2 2 0 012 2v16l-3-2-3 2-3-2-3 2V5a2 2 0 012-2z"/><path d="M10 8h4"/></svg>',compliance:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6a2 2 0 012 2H7a2 2 0 012-2z"/><path d="M7 6H5v15h14V6h-2"/><path d="M9 13l2 2 4-4"/></svg>',convert:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/><path d="M9 14l3 3 3-3"/></svg>',pdf_translate:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/><circle cx="12" cy="15" r="3"/></svg>',mermaid:'<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="6" height="5" rx="1.5"/><rect x="15" y="16" width="6" height="5" rx="1.5"/><rect x="3" y="16" width="6" height="5" rx="1.5"/><path d="M6 8v8M9 18.5h6M18 16V9a3 3 0 00-3-3H9"/></svg>'};
    const CAPTIONS={caption:'生成与润色图注',image_convert:'本机转换，不占模型',translate:'保留公式与引用',polish:'逐段润色',references:'去重 / 排格式',compliance:'字数 · 图表 · 引文自查',convert:'Word / MD / HTML',pdf_translate:'整篇对照输出',mermaid:'本地预览与导出'};
    panel.querySelectorAll('[data-tb-tool]').forEach(button=>{const id=button.dataset.tbTool;button.innerHTML=`<span class="ptb-tool-icon" aria-hidden="true">${ICONS[id]||''}</span><span class="ptb-tool-name"><strong>${h(tools.find(tool=>tool.id===id).name)}</strong><small>${h(CAPTIONS[id]||'')}</small></span>`;});
    const langSelect=panel.querySelector('[name=lang]');if(langSelect){langSelect.value=state.lang||'en';langSelect.onchange=()=>{state.lang=langSelect.value;persist();};}
    panel.querySelectorAll('[name]').forEach(input=>{input.value=state[input.name]||'';input.oninput=()=>{state[input.name]=input.value;persist();};});
    panel.querySelector('[name=project_id]').onchange=()=>{state.project_id=panel.querySelector('[name=project_id]').value;state.images=[];state.conv_in=[];state.document=null;state.caption_items=[];state.caption_sel=null;persist();updateTool();};
    el('preset').onchange=()=>{const preset=presets.find(item=>item.id===el('preset').value);if(!preset||state.run)return;state.prompt_configs[state.tool]={preset_id:preset.id,prompt:preset.prompt,original_prompt:preset.prompt,reference:null};persist();paintPrompts();};
    el('prompt').oninput=()=>{const config=promptConfig();if(config&&!state.run){config.prompt=el('prompt').value;persist();paintPrompts();}};
    el('cb-text').oninput=()=>{state.text=el('cb-text').value;if(el('text'))el('text').value=state.text;persist();};
    el('reset-prompt').onclick=()=>{const config=promptConfig();if(config&&!state.run){config.prompt=config.original_prompt;persist();paintPrompts();}};
    function choose(tool){if(state.run||starting)return;state.tool=tool;el('import-result').textContent='';persist();updateTool();}
    panel.querySelectorAll('[data-tb-tool]').forEach(button=>button.onclick=()=>choose(button.dataset.tbTool));
    function pins(){const ids=read('rw-paper-tool-pins',[]);el('pins').innerHTML=ids.filter(id=>tools.some(tool=>tool.id===id)).map(id=>`<button class="pw-text-button" type="button" data-tb-pinned="${id}">${h(tools.find(tool=>tool.id===id).name)}</button>`).join('');el('pins').querySelectorAll('button').forEach(button=>button.onclick=()=>choose(button.dataset.tbPinned));el('pin').textContent=ids.includes(state.tool)?'取消固定':'固定当前工具';}
    const configRow=panel.querySelector('.ptb-body > .ptb-config:not([data-tb-convert-mode-row])');
    function updateTool(){panel.classList.toggle('is-caption',state.tool==='caption');const diagram=state.tool==='mermaid',images=['caption','image_convert'].includes(state.tool);const direct=documentMode(),format=panel.querySelector('[name=format]');
      const sourceFormat={markdown:'md',htm:'html'}[state.document?.format]||state.document?.format;
      const formats=direct?documentConversion.outputs.filter(value=>value!==sourceFormat):state.tool==='image_convert'?['png','jpg','jpeg','pdf','webp','tiff']:['docx','md','html','pdf'];
      format.innerHTML=formats.map(value=>`<option value="${value}">${value.toUpperCase()}</option>`).join('');format.value=state.format;if(!format.value){format.selectedIndex=0;state.format=format.value;}panel.querySelectorAll('[data-tb-tool]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tbTool===state.tool)));el('description').textContent=tools.find(tool=>tool.id===state.tool)?.description||'';el('direction-row').hidden=state.tool!=='translate';el('format-row').hidden=!['convert','image_convert'].includes(state.tool);el('dpi-row').hidden=state.tool!=='image_convert';el('terminology-row').hidden=!['translate','polish'].includes(state.tool);el('style-row').hidden=state.tool!=='references';el('lang-row').hidden=state.tool!=='caption';panel.querySelector('[name=model_id]').closest('label').hidden=['convert','compliance','image_convert'].includes(state.tool);
      el('text').closest('label').hidden=false;el('text').readOnly=!!direct;el('text').value=direct?'':state.text;
      el('text').placeholder=direct?(state.document?'原文件：'+state.document.name:'选择或拖入要转换的原文件'):{caption:'可选：研究背景、已有图注或需要强调的信息',polish:'粘贴一个段落（建议 200–800 字）',compliance:'粘贴论文正文与文末参考文献，按 [1] 编号',references:'粘贴 BibTeX 或 RIS 条目'}[state.tool]||'粘贴需要处理的内容';el('text').closest('.ptb-editors').hidden=diagram||state.tool==='caption'||['pdf_translate','image_convert','convert'].includes(state.tool);el('caption').hidden=state.tool!=='caption';el('import-zone').hidden=['caption','image_convert','convert','translate','polish','references','compliance'].includes(state.tool);const icvFile=panel.querySelector('#icv-file');if(icvFile)icvFile.accept=state.tool==='convert'?imports.convert[0]:imports.image_convert[0];panel.classList.toggle('ptb-cb-mode',state.tool==='caption');
      // 图注模式把「模板选择 + 本次提示词」搬到顶部胶囊行，画布直接顶到上沿
      const config=configRow,prompts=el('prompts'),toolbar=prompts?.querySelector('.ptb-prompt-toolbar'),details=prompts?.querySelector('details');
      if(toolbar&&details){if(toolbar.parentElement!==config){config.append(toolbar,details);prompts.hidden=true;}}configRow.hidden=diagram;el('mermaid').hidden=!diagram;if(diagram&&!diagramView)diagramView=MermaidTool.mount(el('mermaid'));error('');pins();loadPapers();paint();paintPdfRead();}
    function paintPdfRead(){
      /* PDF 论文翻译：整块换成 1.0 的翻译工作台（添加论文 / 双语阅读 / 荧光笔 / 笔记 / BabelDOC 进度） */
      const on=state.tool==='pdf_translate';panel.classList.toggle('is-pdfread',on);if(el('pdfread'))el('pdfread').hidden=!on;if(!on)return;
      const host=el('pdfread');
      if(host.firstElementChild)return;           // 已经挂过：项目跟随全局当前项目，不用重建
      if(!window.PaperTranslation)return error('翻译阅读器未加载（paper-translation.js 缺失）');
      try{window.PaperTranslation.mount(host,'');} // 1.0 口径：作用域 = 当前项目（trTask 回退 curTask）
      catch(err){host.textContent='翻译阅读器加载失败：'+err.message;}
    }
    el('pin').onclick=()=>{const ids=read('rw-paper-tool-pins',[]);localStorage.setItem('rw-paper-tool-pins',JSON.stringify(ids.includes(state.tool)?ids.filter(id=>id!==state.tool):[...ids,state.tool]));pins();};
    el('close').onclick=()=>{PaperTranslation?.unmount?.();diagramView?.destroy();diagramView=null;panel.remove();panel=null;document.getElementById('paperToolbox')?.focus();};
    panel.onkeydown=event=>{if(event.key==='Escape'&&!host){if(state.tool==='pdf_translate'&&event.target.closest?.('[data-tb-pdfread]'))return;event.preventDefault();event.stopPropagation();el('close').click();}};
    el('copy').onclick=()=>navigator.clipboard.writeText(state.result).then(()=>{if(el('status'))el('status').textContent='结果已复制';},()=>error('复制失败，请选中结果后复制'));
    el('file').onclick=async()=>{try{await api('/api/conv/open',{path:state.path});}catch(err){error(err.message);}};
    el('stop').onclick=async()=>{try{await api('/api/agent/cancel',{idempotency_key:state.run?.key});}catch(err){error(err.message);}};
    el('save').onclick=async()=>{if(!state.project_id)return error('请选择要保存结果的项目');const button=el('save');button.disabled=true;try{const saved=await api('/api/paper-toolbox/save',{project_id:state.project_id,run_id:state.result_run_id,title:tools.find(tool=>tool.id===state.tool)?.name||'论文工具结果'});if(el('status'))el('status').textContent='已保存：'+saved.path;}catch(err){error(err.message);}finally{if(button.isConnected)button.disabled=false;}};
    async function runTool(imageIds){
      if(state.run||starting)return;error('');starting=true;paint();
      try{const request={...state,document_asset_id:documentMode()?state.document?.asset_id:'',image_ids:imageIds||(state.images||[]).map(item=>item.asset_id),request_key:crypto.randomUUID(),paper_id:el('paper').value};delete request.result;delete request.run;delete request.prompt_configs;if(state.tool==='convert')request.source_name=(state.conv_in||[])[0]?.name||state.document?.name||'';if(state.tool!=='references')delete request.cite_style;const config=promptConfig();if(config)Object.assign(request,{prompt_preset:config.preset_id,prompt:config.prompt,prompt_template:config.reference});const made=await api('/api/paper-toolbox/start',request);
        if(state.tool==='pdf_translate'){state.result='';state.path='';state.result_run_id='';persist();paint();el('status').textContent=made.msg||'PDF 翻译已启动，可在项目文献中查看';pollPdf(request.paper_id,state.project_id);return;}
        state.result='';state.path='';state.files=[];state.errors=[];state.fresh_asset='';state.document_result=false;state.result_run_id='';state.run={key:made.request_key,api:'/api/agent',runId:made.run_id,cursor:0,status:'running',request:{task_id:made.chat_id}};persist();paint();follow();
      }catch(err){error(err.message);}finally{starting=false;paint();}
    }
    el('run').onclick=()=>runTool();
    updateTool();if(state.run)follow();if(state.tool==='mermaid')diagramView?.focus();else el('text').focus({preventScroll:true});
  }
  async function pollPdf(id,project){if(!panel?.isConnected)return;try{const result=await api('/api/trans/status?id='+encodeURIComponent(id)+'&task='+project);if(el('status'))el('status').textContent=result.msg||result.state;if(result.state==='running')setTimeout(()=>pollPdf(id,project),2000);else if(result.state==='done'){state.result=result.msg||'译文 PDF 已生成，请在项目文献中打开';paint();}}catch(err){error(err.message);}}
  async function follow(){
    if(following||!state.run)return;following=true;const run=state.run;
    try{await observeAgentRun(run,event=>{
      if(event.type==='tool_result'&&el('status'))el('status').textContent=event.summary;
      if(event.type==='toolbox_file'&&event.file){const rest=(state.files||[]).filter(file=>file.asset_id!==event.file.asset_id);state.files=[...rest,event.file];state.fresh_asset=event.file.asset_id;}
      if(event.type==='toolbox_result'){state.result=event.content||'';state.path=event.path||'';state.files=(event.files&&event.files.length)?event.files:(event.path?[{asset_id:event.asset_id||'',name:event.name||String(event.path).split(/[\\/]/).pop(),path:event.path,project_id:state.project_id}]:[]);state.errors=event.errors||[];state.document_result=event.engine==='pandoc';
        if(Array.isArray(event.items)&&event.items.length){const map=new Map(captionItems().map(item=>[cbKey(item),item]));for(const item of event.items)map.set(cbKey(item),item);state.caption_items=[...map.values()];}}
      if(event.type==='done'){state.result=event.content||state.result;state.result_run_id=run.runId;}
      if(event.type==='error')error(event.message||'处理失败');
      persist();paint();
    },()=>persist(),()=>{if(el('status'))el('status').textContent='连接中断，正在恢复运行记录';});
    }catch(err){error(err.message);}finally{if(state.run===run)state.run=null;following=false;persist();paint();}
  }
  async function openResult(runId,project){
    try{const data=await api('/api/paper-toolbox/result?run_id='+encodeURIComponent(runId)+'&project_id='+encodeURIComponent(project||'')),dialog=document.createElement('dialog');dialog.className='pt-dialog';dialog.setAttribute('aria-label','论文工具处理记录');
      dialog.innerHTML=`<header class="history-preview-head"><h3>${h(data.title)}</h3><button type="button" class="pw-text-button" data-result-close>关闭</button></header>
        <p class="hint">${h({done:'处理完成',running:'正在处理',pending:'等待处理',failed:'处理失败',cancelled:'已停止'}[data.status]||data.status)}</p>
        ${data.input?`<p class="hint">输入</p><pre class="history-preview" data-toolbox-input>${h(data.input)}</pre>`:''}
        <p class="hint" data-toolbox-result-label>处理结果</p><pre class="history-preview">${h(data.content||'处理结果将在完成后保存。可关闭此窗口，在看板中跟踪进度。')}</pre>
        ${data.path?'<button type="button" class="btn" data-result-file>打开转换文件</button>':''}`;
      dialog.querySelector('[data-result-close]').onclick=()=>dialog.close();const file=dialog.querySelector('[data-result-file]');if(file)file.onclick=()=>api('/api/conv/open',{path:data.path}).catch(err=>uiNotify(err.message));dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
      for(const result of data.files||[]){
        if(!Object.hasOwn(result,'project_id'))continue;
        const download=document.createElement('a');
        download.className='btn ghost';download.download='';download.href=fileUrl(result,data.project_id);
        download.textContent='下载 '+result.name;dialog.append(download);
      }
      if(data.prompt)dialog.querySelector('[data-toolbox-result-label]').insertAdjacentHTML('beforebegin',`<details class="pt-preview"><summary>本次提示词 · ${h(data.prompt.title)}${data.prompt.revision?' · v'+h(data.prompt.revision):''}${data.prompt.modified?' · 本次已修改':''}</summary><pre>${h(data.prompt.text)}</pre></details>`);
    }catch(err){uiNotify(err.message);}
  }
  return {open,openResult,render:host=>open({host})};
})();
