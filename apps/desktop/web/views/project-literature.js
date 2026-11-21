/* The project bibliography owns selection, editing and Zotero membership management. */
(() => {
  'use strict';
  const h = value => esc(String(value ?? ''));
  const fields = [['title','标题'],['authors','作者（用分号分隔）'],['year','年份'],['doi','DOI'],['url','链接'],['abstract','摘要']];
  let current = null, openManagerFor = '';
  function html() {
    return `<section class="card project-literature" id="projectLiterature" aria-label="项目文献">
      <header class="pl-header">
        <div class="pl-tools"><button class="btn btn-primary" id="spZoteroBtn">从 Zotero 选文</button><button class="btn ghost" id="plLinkImport">链接 / DOI 导入</button><button class="btn ghost" id="plIntelligence">从情报日报选取</button></div>
        <span class="pill" id="plCount"></span></header>
      <details id="plManagement"><summary>管理 Zotero</summary><div class="pl-manager-settings"><button class="btn ghost" id="plConnection">Zotero 连接设置</button></div><div id="plZotero"><p class="hint">正在读取同步配置…</p></div></details>
      <div class="pl-tools"><input class="control" type="search" id="plSearch" aria-label="搜索项目文献" placeholder="搜索标题、作者、DOI 或 Zotero Key"><select class="control" id="plFilter" aria-label="筛选项目文献"><option value="all">全部文献</option><option value="zotero">已关联 Zotero</option><option value="pdf">有 PDF</option><option value="metadata">仅题录</option></select><button class="btn ghost" id="plRefresh">刷新</button></div>
      <div class="pl-tools pl-selection" id="plSelection" hidden><label><input type="checkbox" id="plSelectAll"> 全选当前列表</label><span class="hint" id="plSelected">已选 0 篇</span><button class="btn" id="plSaveZotero" disabled>保存所选到 Zotero</button><button class="btn ghost" id="plRemove" disabled>移除所选项目关联</button></div>
      <section id="plZoteroSave" class="pl-editor" hidden aria-label="保存到 Zotero"></section>
      <div id="plMessage" role="status" aria-live="polite"></div><div id="plError" class="pl-error" role="alert"></div>
      <section id="plEditor" class="pl-editor" hidden aria-label="编辑文献题录"></section>
      <div id="plList" class="pl-list"></div></section>`;
  }

  function mount(container, project, initialRows, isActive=()=>uiRouteIs('space')&&spTab==='papers') {
    const selected = new Set();
    let rows = initialRows || [], editorRow = null, managerPromise = null, saving = false;
    let revision, request = 0, inFlight = null;
    const $ = id => container.querySelector('#'+id);
    const live = () => current?.container === container && container.isConnected && curTask === project && isActive();
    const message = (text, error=false) => { if(live()) $(error?'plError':'plMessage').textContent=text; };
    const filtered = () => {
      const query=$('plSearch').value.trim().toLocaleLowerCase(), filter=$('plFilter').value;
      return rows.filter(r=>[r.title,r.authors,r.doi,r.zotero_key].join(' ').toLocaleLowerCase().includes(query)
        && (filter==='all'||filter==='zotero'&&r.zotero_key||filter==='pdf'&&r.has_pdf||filter==='metadata'&&!r.has_pdf));
    };
    async function run(button, work) {
      if(!live()) return;
      button.disabled=true;message('',true);
      try { await work(); } catch(err) { message(err.message,true); }
      finally { if(live()){button.disabled=false;updateSelection();} }
    }
    function updateSelection() {
      const keys=filtered().filter(r=>r.result_id).map(r=>r.result_id);
      $('plSelection').hidden=!keys.length&&!selected.size;
      $('plSelected').textContent=`已选 ${selected.size} 篇`;
      $('plRemove').disabled=!rows.some(r=>selected.has(r.result_id)&&r.zotero_key);
      $('plSaveZotero').disabled=!selected.size;
      $('plSaveZotero').textContent=`保存所选 ${selected.size} 篇到 Zotero`;
      $('plSelectAll').disabled=!keys.length;
      $('plSelectAll').checked=!!keys.length&&keys.every(k=>selected.has(k));
      $('plSelectAll').indeterminate=keys.some(k=>selected.has(k))&&!$('plSelectAll').checked;
    }
    function paint() {
      if(!live()) return;
      const visible=filtered(), available=new Set(rows.map(r=>r.result_id).filter(Boolean));
      for(const key of selected) if(!available.has(key)) selected.delete(key);
      $('plCount').textContent=`${rows.length} 篇`;
      $('plList').innerHTML=visible.map(r=>{
        const zoteroStatus=r.zotero_status==='imported'?'<span class="pill">已写入 Zotero</span>':r.zotero_status==='failed'?`<span class="pill">Zotero 待重试${r.zotero_error?`：${h(r.zotero_error)}`:''}</span>`:(r.has_pdf&&r.result_id&&r.zotero_status==='staged'&&!r.zotero_key?'<span class="pill">暂存待写入 Zotero</span>':'');
        const canImport=!!r.result_id;
        return `<article class="pl-row" data-pl-id="${h(r.id)}"><input type="checkbox" data-pl-key="${h(r.result_id)}" aria-label="选择 ${h(r.title||r.filename)}" ${r.result_id?'':'disabled'} ${selected.has(r.result_id)?'checked':''}><div class="pl-main"><h4>${h(r.title||r.filename||'未命名')}</h4><p>${h(r.authors||'作者未识别')}${r.year?' · '+h(r.year):''}${r.doi?' · DOI '+h(r.doi):''}</p><span class="pill">${r.zotero_reference?'Zotero PDF 引用':r.has_pdf?'有 PDF':'题录'}</span> ${r.zotero_key?`<span class="pill">Zotero · ${h(r.zotero_key)}</span>`:''}${zoteroStatus}<div class="es-badges" data-journal="${h(r.journal||r.title||'')}" style="margin-top:6px"></div></div><div class="pl-actions">${LiteratureImport.safeLink(r.url||r.doi&&"https://doi.org/"+r.doi)?`<a class="btn ghost" href="${h(LiteratureImport.safeLink(r.url||"https://doi.org/"+r.doi))}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`:""}${r.result_id?'<button class="btn ghost" data-pl-edit>编辑题录</button>':''}${r.has_pdf&&r.paper_id?'<button class="btn ghost" data-pl-read>阅读 PDF</button>':''}${r.zotero_key?'<button class="btn ghost" data-pl-open-zotero>在 Zotero 中打开</button>':''}${canImport?'<button class="btn ghost" data-pl-stage-import>保存到 Zotero</button>':''}</div></article>`;}).join('') || `<p class="hint">${rows.length?'没有匹配的文献，请调整筛选。':'项目暂无文献。可从情报日报、链接 / DOI 或 Zotero 添加题录。'}</p>`;
      $('plList').querySelectorAll('[data-pl-key]').forEach(el=>el.onchange=()=>{el.checked?selected.add(el.dataset.plKey):selected.delete(el.dataset.plKey);updateSelection();});
      $('plList').querySelectorAll('[data-pl-edit]').forEach(el=>el.onclick=()=>{
        if(saving) return;
        const row=rows.find(r=>r.id===el.closest('[data-pl-id]').dataset.plId);
        if(editorRow && editorRow.id!==row.id) {message('请先保存或取消当前题录编辑。',true);return;}
        if(!editorRow) edit(row);
      });
      $('plList').querySelectorAll('[data-pl-read]').forEach(el=>el.onclick=()=>run(el,async()=>{
        const row=rows.find(r=>r.id===el.closest('[data-pl-id]').dataset.plId);
        if(window.ProjectWorkspace?.active()) await ProjectWorkspace.openPaper(row);
        else await api('/api/paper/open?id='+encodeURIComponent(row.paper_id));
        message('已在本机阅读器打开 PDF。');
      }));
      $('plList').querySelectorAll('[data-pl-stage-import]').forEach(el=>el.onclick=()=>run(el,async()=>{
        const row=rows.find(r=>r.id===el.closest('[data-pl-id]').dataset.plId);
        await openSave([row.result_id]);
      }));
      $('plList').querySelectorAll('[data-pl-open-zotero]').forEach(el=>el.onclick=()=>run(el,async()=>{
        const row=rows.find(r=>r.id===el.closest('[data-pl-id]').dataset.plId);
        await api('/api/zotero/select?key='+encodeURIComponent(row.zotero_key));
      }));
      $('plList').querySelectorAll('[data-pl-id]').forEach(el=>{
        const row=rows.find(r=>r.id===el.dataset.plId);
        if(!row.zotero_key||row.zotero_reference) return;
        if(row.has_pdf&&row.paper_id){
          const button=document.createElement('button');button.className='btn ghost';button.textContent='改用 Zotero 原件';
          button.onclick=()=>run(button,async()=>{
            if(!await uiConfirm('改用 Zotero 中内容相同的 PDF？当前项目中的文件会保留。')||!live())return;
            await api('/api/zotero/web/reference',{task_id:project,paper_id:row.paper_id,confirm:true});
            message('已改用 Zotero 原件。');await refresh();
          });
          el.querySelector('.pl-actions').append(button);
          return;
        }
        const button=document.createElement('button');button.className='btn ghost';button.dataset.plImportPdf='';button.textContent='引用本机 PDF';
        button.onclick=()=>run(button,async()=>{
          const result=await api('/api/workflow/zotero',{task_id:project,keys:[row.zotero_key]});
          if(!live())return;
          message(result.downloaded?'已关联 Zotero PDF。':'Zotero 附件在本机不可读。');
          await refresh();
        });
        el.querySelector('.pl-actions').append(button);
      });
      if(typeof spLoadPaperBadges==='function') spLoadPaperBadges($('plList'));
      updateSelection();
    }
    async function openSave(resultIds){
      if(!resultIds.length)return;
      const [settings,list]=await Promise.all([api('/api/zotero/web/config?task_id='+encodeURIComponent(project)),api('/api/zotero/web/collections')]);
      if(!live())return;
      if(settings.config?.transport!=='local')throw new Error('请切换到本机 Zotero 保存项目文献');
      const panel=$('plZoteroSave'); let batch, matches={}, busy=false;
      panel.hidden=false;
      panel.innerHTML=`<h4>保存 ${resultIds.length} 篇到 Zotero</h4><div class="pl-tools"><label>目标分类 <select class="control" data-save-target><option value="">请选择分类</option>${(list.collections||[]).map(row=>`<option value="${h(row.key)}">${h(row.data?.name||row.key)} · ${h(row.key)}</option>`).join('')}</select></label><button class="btn ghost" data-save-download>下载所选开放获取 PDF</button><button class="btn" data-save-preview>预览</button><button class="btn" data-save-apply disabled>确认保存</button><button class="btn ghost" data-save-close>取消</button></div><div data-save-result aria-live="polite"></div>`;
      const get=key=>panel.querySelector(`[data-save-${key}]`), target=get('target'), details=get('result');
      target.value=settings.binding?.collection||'';
      if(target.selectedIndex<0)target.value='';
      const invalidate=()=>{batch=null;get('apply').disabled=true;};
      const operate=async work=>{
        if(busy||!live())return;
        busy=true;panel.querySelectorAll('button,select').forEach(node=>node.disabled=true);
        try{await work();}catch(err){details.textContent=err.message;invalidate();}
        finally{busy=false;panel.querySelectorAll('button,select').forEach(node=>node.disabled=false);get('apply').disabled=!batch||!!batch.conflicts.length;}
      };
      target.onchange=()=>{invalidate();details.replaceChildren();};
      get('close').onclick=()=>{invalidate();panel.hidden=true;};
      get('download').onclick=()=>operate(async()=>{
        invalidate();
        const eligible=rows.filter(row=>resultIds.includes(row.result_id)&&!row.has_pdf&&row.pdf_url).map(row=>row.result_id);
        if(!eligible.length){details.textContent='所选文献没有可下载的开放获取 PDF。';return;}
        const out=await api('/api/zotero/web/staged/download',{task_id:project,result_ids:eligible});
        details.textContent=`已暂存 ${out.result_ids.length} 篇 PDF。${out.failed.map(row=>row.error).join('；')}`;
        await refresh();
      });
      get('preview').onclick=()=>operate(async()=>{
        invalidate();
        if(!target.value)throw new Error('请选择目标分类');
        batch=await api('/api/zotero/web/staged/plan',{task_id:project,result_ids:resultIds,collection:target.value,matches});
        details.innerHTML=`<p>${h(batch.collection_name)} · ${batch.actions.length} 篇可保存${batch.conflicts.length?` · ${batch.conflicts.length} 项待核对`:''}</p><ul>${batch.actions.map(row=>`<li>${h(row.title)} · ${row.kind==='create'?'新建题录':row.add_collection?'复用并加入分类':'已在分类中'}${row.pdf?' · 导入或复用 PDF':''}</li>`).join('')}${batch.conflicts.map(row=>`<li>${h(row.title)} · ${h(row.reason)}${row.candidates.length?` <select class="control" data-save-match="${h(row.result_id)}" aria-label="选择对应条目"><option value="">请选择</option>${row.candidates.map(item=>`<option value="${h(item.key)}" ${matches[row.result_id]===item.key?'selected':''}>${h(item.title||item.key)} · ${h(item.key)}</option>`).join('')}</select>`:''}</li>`).join('')}</ul>`;
        details.querySelectorAll('[data-save-match]').forEach(el=>el.onchange=()=>{matches[el.dataset.saveMatch]=el.value;invalidate();});
      });
      get('apply').onclick=()=>operate(async()=>{
        if(!batch)return;
        const token=batch.plan_token;invalidate();
        const out=await api('/api/zotero/web/staged/apply',{task_id:project,plan_token:token,confirm:true});
        const done=new Set(out.applied.map(row=>row.result_id));
        done.forEach(id=>selected.delete(id));
        resultIds=resultIds.filter(id=>!done.has(id));
        panel.querySelector('h4').textContent=`保存 ${resultIds.length} 篇到 Zotero`;
        if(!resultIds.length)panel.hidden=true;
        message(`已保存 ${done.size} 篇到 Zotero。`);
        details.textContent=out.failed.map(row=>row.error).join('；');
        await refresh();
      });
      target.focus();panel.scrollIntoView({block:'nearest'});
    }
    function refresh({poll=false}={}) {
      if(!live())return;
      if(poll&&inFlight)return inFlight;
      const ticket=++request;
      inFlight=api('/api/library/project?task_id='+encodeURIComponent(project)).then(data=>{
        if(!live()||ticket!==request||revision===data.version)return;
        revision=data.version;rows=data.items;paint();
      }).finally(()=>{if(ticket===request)inFlight=null;});
      return inFlight;
    }
    $('plLinkImport').onclick=()=>LiteratureImport.open({projectId:project,onImported:refresh});
    $('plIntelligence').onclick=()=>AppNavigation.follow(()=>openTab('intel'));
    async function management(target='sources') {
      $('plManagement').open=true;
      if(!managerPromise) {
        managerPromise=ProjectZotero.mount($('plZotero'),project,refresh,live).catch(err=>{managerPromise=null;throw err;});
      }
      await managerPromise;
      if(live()){
        ProjectZotero.show($('plZotero'),target);
        $('plManagement').scrollIntoView({block:'nearest'});
      }
    }
    function edit(row) {
      editorRow={...row};$('plEditor').hidden=false;
      $('plEditor').innerHTML=`<form id="plEditForm"><h4>编辑题录</h4><div class="integration-grid">${fields.map(([key,label])=>`<label><span>${label}</span>${key==='abstract'?`<textarea class="control" name="${key}">${h(row[key])}</textarea>`:`<input class="control" name="${key}" value="${h(row[key])}" ${key==='title'?'required':''}>`}</label>`).join('')}</div><p class="hint">保存后工作流和项目空间同步更新。已关联 Zotero 的文献按项目的同步设置处理。</p><div class="pl-tools"><button class="btn" type="submit">保存题录</button><button class="btn ghost" type="button" id="plEditCancel">取消</button><button class="btn ghost" type="button" id="plEditLatest">重新读取最新题录</button></div></form>`;
      $('plEditForm').onsubmit=event=>{event.preventDefault();if(saving)return;run(event.submitter||$('plEditForm').querySelector('[type=submit]'),async()=>{
        const body=Object.fromEntries(new FormData($('plEditForm'))), expected=Object.fromEntries(fields.map(([key])=>[key,String(editorRow[key]||'')]));
        saving=true;
        $('plEditForm').querySelectorAll('input,textarea,button').forEach(el=>el.disabled=true);
        try {
          await api('/api/library/project',{task_id:project,result_id:editorRow.result_id,fields:body,expected});
          if(!live()) return;
          editorRow=null;$('plEditor').hidden=true;message('题录已保存。');await refresh();
        } finally {
          saving=false;
          if(live()&&editorRow) $('plEditForm').querySelectorAll('input,textarea,button').forEach(el=>el.disabled=false);
        }
      });};
      $('plEditCancel').onclick=()=>{if(saving)return;editorRow=null;$('plEditor').hidden=true;message('',true);};
      $('plEditLatest').onclick=async()=>{
        if(saving||!await uiConfirm('重新读取会替换尚未保存的编辑内容，继续？')||!live())return;
        await run($('plEditLatest'),async()=>{await refresh();if(live()) {const latest=rows.find(r=>r.id===row.id);if(latest)edit(latest);else message('此文献已移出项目，请保留需要的编辑内容后取消。',true);}});
      };
      $('plEditForm').querySelector('[name=title]').focus();
      $('plEditor').scrollIntoView({block:'nearest'});
    }
    current={container,project,refresh,live,management,
      refreshStatus(){if(live()&&$('plManagement').open)return ProjectZotero.refreshStatus($('plZotero'));}};
    $('plSearch').oninput=paint;$('plFilter').onchange=paint;
    $('plSelectAll').onchange=event=>{filtered().filter(r=>r.result_id).forEach(r=>event.target.checked?selected.add(r.result_id):selected.delete(r.result_id));paint();};
    $('plSaveZotero').onclick=event=>run(event.target,()=>openSave([...selected]));
    $('plRefresh').onclick=event=>run(event.target,refresh);
    $('spZoteroBtn').onclick=event=>run(event.target,()=>management('sources'));
    $('plManagement').ontoggle=()=>{if($('plManagement').open&&!managerPromise)management().catch(err=>message(err.message,true));};
    $('plRemove').onclick=event=>run(event.target,async()=>{const keys=rows.filter(r=>selected.has(r.result_id)&&r.zotero_key).map(r=>r.zotero_key);await management('history');if(live())await ProjectZotero.previewRemoval($('plZotero'),keys);});
    $('plConnection').onclick=()=>AppNavigation.follow(()=>IntegrationSettings.openZotero(project));
    paint();
    if(openManagerFor===project){openManagerFor='';management().catch(err=>message(err.message,true));}
    return refresh().catch(err=>message(err.message,true));
  }
  async function open(project, manage=false) {
    if(!project)return;
    if(current?.project===project&&current.live())return manage?current.management():current.refresh();
    return AppNavigation.follow(async()=>{
      await ProjectWorkspace.openProject(project);
      if(curTask!==project)return;
      openManagerFor=manage?project:'';
      await ProjectWorkspace.showResource('papers');
    });
  }
  window.ProjectLiterature={html,mount,open,async poll(){
    const panel=current;
    if(!panel?.live())return;
    await panel.refresh({poll:true});
    await panel.refreshStatus();
  }};
})();
