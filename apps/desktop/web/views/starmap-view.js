function renderStar(m){
  m.innerHTML = `<div class="sm-wrap">
      <div class="sm-bar">
      <b>知识星图</b>
      <button class="btn ghost sm-back" type="button" hidden>返回全部项目</button>
      <span class="hint">点项目进分支 → 点星团看条目 → 点条目看分析，Esc 逐层退回</span>
      <button class="btn ghost" id="smZotero" type="button" style="margin-left:auto" title="从本机 Zotero 导入文献到当前项目">从 Zotero 导入文献</button>
      <button class="btn ghost" id="smRebuild" type="button" style="margin-left:8px" title="重新扫库编译一次（平时只读上次编译结果，不实时重算）">重算星图</button>
    </div>
    <div class="sm-search"><input class="control" type="search" aria-label="搜索知识库项目" placeholder="搜索知识库项目名称或研究问题"><div data-sm-results hidden></div></div>
    <div class="sm-projects" aria-label="知识库项目">${(S.tasks||[]).map(project=>`<button type="button" data-sm-card="${esc(project.id)}"><strong>${esc(project.title)}</strong><span>${esc(project.question||'查看该项目的文献、笔记与关联知识')}</span><small>进入知识星图 →</small></button>`).join('')}<p data-sm-empty hidden>没有匹配的项目</p></div>
    <iframe id="kbFrame" class="sm-frame" src="/kb.html?theme=dark" title="知识星图"></iframe>
  </div>`;
  const rb = m.querySelector("#smRebuild"), fr = m.querySelector("#kbFrame"), zb = m.querySelector("#smZotero");
  const search=m.querySelector('.sm-search input'),results=m.querySelector('[data-sm-results]');
  const wrap=m.querySelector('.sm-wrap'),back=m.querySelector('.sm-back');
  const selectProject=id=>{curTask=id;localStorage.setItem('rw-task',id);fr.src='/kb.html?theme=dark&task='+encodeURIComponent(id);wrap.classList.add('sm-project-open');back.hidden=false;results.hidden=true;search.value='';renderStatus();};
  m.querySelectorAll('[data-sm-card]').forEach(button=>button.onclick=()=>selectProject(button.dataset.smCard));
  back.onclick=()=>{wrap.classList.remove('sm-project-open');back.hidden=true;search.value='';search.oninput();fr.src='/kb.html?theme=dark';};
  search.oninput=()=>{
    const q=search.value.trim().toLocaleLowerCase();results.hidden=!q;
    const projects=(S.tasks||[]).filter(project=>(project.title+' '+(project.question||'')).toLocaleLowerCase().includes(q));
    const matched=new Set(projects.map(project=>project.id));m.querySelectorAll('[data-sm-card]').forEach(card=>card.hidden=!matched.has(card.dataset.smCard));m.querySelector('[data-sm-empty]').hidden=projects.length>0;
    results.innerHTML=projects.map(project=>`<button type="button" data-sm-project="${esc(project.id)}">${esc(project.title)}</button>`).join('')||'<p role="status">没有匹配的项目</p>';
    results.querySelectorAll('[data-sm-project]').forEach(button=>button.onclick=()=>selectProject(button.dataset.smProject));
  };
  search.onkeydown=event=>{if(event.key==='Escape'){event.stopPropagation();search.value='';search.oninput();}else if(event.key==='Enter'){event.preventDefault();results.querySelector('button')?.click();}else if(event.key==='ArrowDown'){event.preventDefault();(wrap.classList.contains('sm-project-open')||innerWidth>1000?results.querySelector('button'):m.querySelector('[data-sm-card]:not([hidden])'))?.focus();}};
  if(zb) zb.onclick = () => { if(typeof window.zotOpen === "function") window.zotOpen(); else if(typeof zotOpen === "function") zotOpen(); };
  if(rb && fr) rb.onclick = ()=>{                    // 星图是编译产物：只有这里（或第一次进知识库）才重算
    rb.disabled = true; rb.textContent = "重算中…";
    fr.onload = ()=>{ rb.disabled = false; rb.textContent = "重算星图"; fr.onload = null; };
    fr.src = "/kb.html?theme=dark&rebuild=1";
  };
}

// 星图里点「在应用里打开」：论文开 PDF，笔记跳到笔记页（其他类型不猜，留在星图里看分析）
window.addEventListener("message", e => {
  const d = e.data || {};
  if(d.rw === "theme"){ setTheme(d.theme || "dark"); return; }   // 星图只在深色下画：点提示里的按钮直接切过来
  if(d.rw === "openZotero"){ if(typeof window.zotOpen === "function") window.zotOpen(); else if(typeof zotOpen === "function") zotOpen(); return; }
  if(d.rw === "openZoteroSelect"){
    if(d.key) api("/api/zotero/select?key=" + encodeURIComponent(d.key)).catch(err => console.log(err));
    return;
  }
  if(d.rw === "openPath"){
    if(d.path) api("/api/paper/open?path=" + encodeURIComponent(d.path)).catch(err => uiNotify("打开失败：" + err.message));
    return;
  }
  if(d.rw === "saveNoteFromZotero"){
    if(d.key){
      const project=curTask;
      if(!project){uiNotify('请先选择要保存笔记的项目。');return;}
      api("/api/zotero/note?key=" + encodeURIComponent(d.key))
        .then(res => {
          if(res && res.title){
            return api("/api/project/note/save", {project_id:project,path:'',title:res.title,content:res.content||''});
          }
          throw new Error('Zotero 条目没有可保存的笔记内容');
        })
        .then(() => {
          if(typeof loadMdNotes === "function") loadMdNotes();
          if(typeof toast === "function") toast("已成功存为大笔记！"); else uiNotify("已保存至笔记！");
        })
        .catch(err => uiNotify("存为笔记失败：" + err.message));
    }
    return;
  }
  if(d.rw === "selectProject"){
    if(d.id){
      curTask = d.id;
      localStorage.setItem("rw-task", curTask);
      if(d.view === "workflow") openTab("workflow");
      else if(d.view === "space") openTab("space");
      else {
        renderSidebar();
        renderStatus();
      }
    }
    return;
  }
  if(d.rw !== "open") return;
  if(d.kind === "post" || d.kind === "paper"){
    const param = d.ref ? "?id=" + encodeURIComponent(d.ref) : (d.path ? "?path=" + encodeURIComponent(d.path) : "");
    if(param) api("/api/paper/open" + param).catch(err => uiNotify("打开失败：" + err.message));
  } else if(d.kind === "note") { spTab = "notes"; openTab("space"); }
  else if(d.kind === "draft") {
    if(d.step) { wfStep = d.step; localStorage.setItem("rw-wf-step", wfStep); }
    openTab("workflow");
  }
});

/* ---------------- 笔记（大笔记：Markdown 编辑器，不绑项目） ---------------- */
let mdNotes=[], mdActiveId=null, mdSaveTimer=null, mdLoaded=false, mdFocusOn=false, mdNarrowOn=false, mdFocusPrev="split";
