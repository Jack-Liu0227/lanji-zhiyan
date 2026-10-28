/* The project bibliography owns selection, editing and Zotero membership management. */
(() => {
  'use strict';
  const h = value => esc(String(value ?? ''));
  const fields = [['title','标题'],['authors','作者（用分号分隔）'],['year','年份'],['doi','DOI'],['url','链接'],['abstract','摘要']];
  let current = null, openManagerFor = '';
  const style = document.createElement('style');
  style.textContent = `.project-literature .pl-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0}.project-literature .pl-tools input[type=search]{flex:1;min-width:180px}.project-literature .pl-list{border-top:1px solid var(--line)}.project-literature .pl-row{display:flex;gap:12px;align-items:flex-start;padding:16px 0;border-bottom:1px solid var(--line)}.project-literature .pl-row>input{margin-top:6px}.project-literature .pl-main{flex:1;min-width:0;overflow-wrap:anywhere}.project-literature .pl-main h4{margin:0 0 6px;font-size:14px}.project-literature .pl-main p{margin:4px 0;color:var(--muted);font-size:12px}.project-literature .pl-actions{display:flex;gap:8px;flex-wrap:wrap}.project-literature details>summary{cursor:pointer;padding:12px 0}.project-literature .pl-editor{padding:16px;border:1px solid var(--line);border-radius:10px;margin:12px 0;background:var(--panel)}.project-literature .pl-editor label{display:grid;gap:5px}.project-literature .pl-editor textarea{min-height:100px}.project-literature .pl-error{color:var(--err);white-space:pre-wrap}.project-literature #zs-plan ul{max-height:230px;overflow:auto}.project-literature #zs-candidates{border:1px solid var(--line);border-radius:8px;padding:0 12px}@media(max-width:700px){.project-literature .pl-row{flex-wrap:wrap}.project-literature .pl-actions{width:100%;padding-left:28px}}`;
  document.head.append(style);
  style.textContent += `.project-literature .pl-tools input[type=search]{flex:1 1 260px;width:auto}.project-literature .pl-tools>select{flex:0 1 180px;width:auto}.project-literature .sch-head>.pl-tools{margin:0}`;

  function html() {
    return `<section class="card project-literature" id="projectLiterature" style="margin-top:12px">
      <div class="sch-head"><div><h3>项目文献 <span class="pill" id="plCount"></span></h3><p class="hint">与工作流共享题录；从情报日报、原文链接或 Zotero 添加文献，统一管理。</p></div>
        <div class="pl-tools"><button class="btn btn-primary" id="spZoteroBtn">从 Zotero 选择文献</button><button class="btn ghost" id="plLinkImport">链接 / DOI 导入</button><button class="btn ghost" id="plIntelligence">从情报日报选取</button><button class="btn ghost" id="plSync">同步与来源</button><button class="btn ghost" id="spToLib">打开知识库</button></div></div>
      <div class="pl-tools"><input class="control" type="search" id="plSearch" aria-label="搜索项目文献" placeholder="搜索标题、作者、DOI 或 Zotero Key"><select class="control" id="plFilter" aria-label="筛选项目文献"><option value="all">全部文献</option><option value="zotero">已关联 Zotero</option><option value="pdf">有 PDF</option><option value="metadata">仅题录</option></select><button class="btn ghost" id="plRefresh">刷新</button></div>
      <div class="pl-tools"><label><input type="checkbox" id="plSelectAll"> 勾选当前筛选下的 Zotero 文献</label><span class="hint" id="plSelected">已选 0 篇</span><button class="btn ghost" id="plRemove" disabled>移除所选项目关联</button><button class="btn ghost" id="plHistory">恢复关联 / 操作记录</button><button class="btn ghost" id="plConnection">Zotero 连接设置</button></div>
      <div id="plMessage" role="status" aria-live="polite"></div><div id="plError" class="pl-error" role="alert"></div>
      <details id="plManagement"><summary>文献来源、同步与项目关联管理</summary><div id="plZotero"><p class="hint">正在读取同步配置…</p></div></details>
      <section id="plEditor" class="pl-editor" hidden aria-label="编辑文献题录"></section>
      <div id="plList" class="pl-list"></div></section>`;
  }

  function mount(container, project, initialRows) {
    const selected = new Set();
    let rows = initialRows || [], editorRow = null, managerPromise = null, saving = false;
    const $ = id => container.querySelector('#'+id);
    const live = () => current?.container === container && container.isConnected && curTask === project && uiRouteIs('space') && spTab === 'papers';
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
      const keys=filtered().filter(r=>r.zotero_key).map(r=>r.zotero_key);
      $('plSelected').textContent=`已选 ${selected.size} 篇`;
      $('plRemove').disabled=!selected.size;
      $('plSelectAll').disabled=!keys.length;
      $('plSelectAll').checked=!!keys.length&&keys.every(k=>selected.has(k));
      $('plSelectAll').indeterminate=keys.some(k=>selected.has(k))&&!$('plSelectAll').checked;
    }
    function paint() {
      if(!live()) return;
      const visible=filtered(), available=new Set(rows.map(r=>r.zotero_key).filter(Boolean));
      for(const key of selected) if(!available.has(key)) selected.delete(key);
      $('plCount').textContent=`${rows.length} 篇`;
      $('plList').innerHTML=visible.map(r=>`<article class="pl-row" data-pl-id="${h(r.id)}"><input type="checkbox" data-pl-key="${h(r.zotero_key)}" aria-label="选择 ${h(r.title||r.filename)}" ${r.zotero_key?'':'disabled'} ${selected.has(r.zotero_key)?'checked':''} title="${r.zotero_key?'选择项目关联':'关联 Zotero 后可批量移除该关联'}"><div class="pl-main"><h4>${h(r.title||r.filename||'未命名')}</h4><p>${h(r.authors||'作者未识别')}${r.year?' · '+h(r.year):''}${r.doi?' · DOI '+h(r.doi):''}</p><span class="pill">${r.has_pdf?'有 PDF':'题录'}</span> ${r.zotero_key?`<span class="pill">Zotero · ${h(r.zotero_key)}</span>`:''}<div class="es-badges" data-journal="${h(r.journal||r.title||'')}" style="margin-top:6px"></div></div><div class="pl-actions">${LiteratureImport.safeLink(r.url||r.doi&&"https://doi.org/"+r.doi)?`<a class="btn ghost" href="${h(LiteratureImport.safeLink(r.url||"https://doi.org/"+r.doi))}" target="_blank" rel="noopener noreferrer">查看原文 ↗</a>`:""}${r.result_id?'<button class="btn ghost" data-pl-edit>编辑题录</button>':''}${r.has_pdf&&r.paper_id?'<button class="btn ghost" data-pl-read>阅读 PDF</button>':''}</div></article>`).join('') || `<p class="hint">${rows.length?'没有匹配的文献，请调整筛选。':'项目暂无文献。可从情报日报、链接 / DOI 或 Zotero 添加题录。'}</p>`;
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
      $('plList').querySelectorAll('[data-pl-id]').forEach(el=>{
        const row=rows.find(r=>r.id===el.dataset.plId);
        if(!row.zotero_key||row.has_pdf) return;
        const button=document.createElement('button');button.className='btn ghost';button.dataset.plImportPdf='';button.textContent='导入本机 PDF';
        button.onclick=()=>run(button,async()=>{
          const result=await api('/api/workflow/zotero',{task_id:project,keys:[row.zotero_key]});
          if(!live())return;
          message(result.downloaded?'PDF 已导入当前项目。':'未能导入 PDF，请检查 Zotero 附件；可在工作流下载队列查看原因。');
          await refresh();
        });
        el.querySelector('.pl-actions').append(button);
      });
      if(typeof spLoadPaperBadges==='function') spLoadPaperBadges($('plList'));
      updateSelection();
    }
    async function refresh() {
      const data=await api('/api/library/project?task_id='+encodeURIComponent(project));
      if(live()){rows=data.items;paint();}
    }
    $('plLinkImport').onclick=()=>LiteratureImport.open({projectId:project,onImported:refresh});
    $('plIntelligence').onclick=()=>openTab('intel');
    async function management(target='sources') {
      $('plManagement').open=true;
      if(!managerPromise) {
        managerPromise=ProjectZotero.mount($('plZotero'),project,refresh).catch(err=>{managerPromise=null;throw err;});
      }
      await managerPromise;
      if(live()) $(target==='history'?'zs-batches':'zs-sources')?.scrollIntoView({block:'nearest'});
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
    current={container,project,update(data){if(live()){rows=data.items;paint();}},management,
      refreshStatus(){if(live()&&$('plManagement').open)return ProjectZotero.refreshStatus($('plZotero'));}};
    $('plSearch').oninput=paint;$('plFilter').onchange=paint;
    $('plSelectAll').onchange=event=>{filtered().filter(r=>r.zotero_key).forEach(r=>event.target.checked?selected.add(r.zotero_key):selected.delete(r.zotero_key));paint();};
    $('plRefresh').onclick=event=>run(event.target,refresh);
    for(const id of ['spZoteroBtn','plSync']) $(id).onclick=event=>run(event.target,()=>management());
    $('plHistory').onclick=event=>run(event.target,()=>management('history'));
    $('plManagement').ontoggle=()=>{if($('plManagement').open&&!managerPromise)management().catch(err=>message(err.message,true));};
    $('plRemove').onclick=event=>run(event.target,async()=>{const keys=[...selected];await management('history');if(live())await ProjectZotero.previewRemoval($('plZotero'),keys);});
    $('plConnection').onclick=()=>IntegrationSettings.openZotero(project);
    $('spToLib').onclick=()=>openTab('library');
    paint();
    if(openManagerFor===project){openManagerFor='';management().catch(err=>message(err.message,true));}
  }
  async function open(project, manage=false) {
    if(!project || !await pnSaveCurrent())return;
    curTask=project;spId=project;spTab='papers';localStorage.setItem('rw-task',project);
    openManagerFor=manage?project:'';
    openTab('space');
  }
  window.ProjectLiterature={html,mount,open,update(project,data){if(current?.project===project)current.update(data);},
    async refreshStatus(project){if(current?.project===project)await current.refreshStatus();}};
})();
