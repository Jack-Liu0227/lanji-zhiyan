const FL_TEXT = new Set(["md","markdown","txt","tex","bib","json","py","csv","yaml","yml","log","sh","js","css"]);
let flRel = "", flSel = "", flRows = [];
let flTreeCache = Object.create(null);
let flExpanded = (() => {
  try {
    const raw = localStorage.getItem("rw-fl-tree-expanded");
    if(raw) return new Set(JSON.parse(raw));
  } catch(_) {}
  return new Set();
})();
function flSaveExpanded() {
  try {
    localStorage.setItem("rw-fl-tree-expanded", JSON.stringify([...flExpanded]));
  } catch(_) {}
}
let flTreeLoading = new Map();
let flTreeRoot = "";
let flTreeProject = "", flTreeGeneration = 0;

function flGetIcon(ext){
  ext = String(ext || "").toLowerCase().trim();
  if(ext === "pdf") return "📕";
  if(ext === "md" || ext === "markdown") return "📝";
  if(ext === "txt" || ext === "log") return "📄";
  if(ext === "docx" || ext === "doc") return "📘";
  if(ext === "tex" || ext === "bib") return "📐";
  if(["png","jpg","jpeg","svg","webp","gif","bmp"].includes(ext)) return "🖼️";
  if(["py","js","json","html","css","sh","bat"].includes(ext)) return "⚙️";
  return "📄";
}

function flFindRow(rel){
  for(const k in flTreeCache){
    const found = (flTreeCache[k] || []).find(x => x.rel === rel);
    if(found) return found;
  }
  return null;
}

function flFlattenRows(){
  const all = [];
  for(const k in flTreeCache){
    for(const r of (flTreeCache[k] || [])){
      if(!all.some(x => x.rel === r.rel)) all.push(r);
    }
  }
  flRows = all;
  return all;
}

function flFmtSize(n){ n = Number(n || 0);
  if(!n) return "";
  if(n < 1024) return n + " B";
  if(n < 1048576) return (n/1024).toFixed(1) + " KB";
  return (n/1048576).toFixed(1) + " MB"; }
function flMsg(t){ const el = $("flMeta"); if(el) el.textContent = t || ""; }

function filesPanelHTML(active){
  return `<section class="fl" id="panel-files"${active ? "" : ' style="display:none"'}>
    <div class="fl-grid">
      <aside class="fl-col">
        <div class="fl-h"><b>项目文件夹</b><span class="fl-tag" id="flRoot" title=""></span><span class="fl-sp"></span>
          <button class="fl-ib" id="flReload" title="重新读这个文件夹">↻</button></div>
        <div class="fl-list" id="flList"><p class="fl-empty">读目录…</p></div>
      </aside>
      <div class="fl-col">
        <div class="fl-h">
          <b id="flName">选一个文件</b>
          <span class="fl-sub" id="flMeta"></span>
          <div class="seg-tabs fl-mode-tabs" id="flModeTabs" role="group" aria-label="文件工作区模式" style="margin-left:10px;display:none">
            <button type="button" class="seg-tab active" data-fl-mode="preview" title="纯预览模式">👁 预览</button>
            <button type="button" class="seg-tab" data-fl-mode="edit" title="直接编辑正文（Ctrl+S 保存）">✎ 编辑</button>
            <button type="button" class="seg-tab" data-fl-mode="split" title="双栏实时对照">◫ 双栏</button>
          </div>
          <span class="fl-sp"></span>
          <button class="btn btn-sm" id="flSaveBtn" style="display:none;margin-right:6px" title="保存文件修改 (Ctrl+S)">💾 保存</button>
          <span class="handoff-row" id="flHandoffPins"></span>
          <button class="btn btn-sm" id="flHand" disabled title="先选择文件；交接快捷键 Alt+Shift+H">交给…</button>
          <button class="btn btn-sm" id="flHandoffHistory" disabled title="查看这个文件的交接与回执">交接记录</button>
          <button class="fl-ib" id="flOpen" title="用系统程序打开">↗</button></div>
        <div class="fl-stage" id="flStage"><div class="fl-empty">左边点一个文件，这里出预览与编辑<br>
          <span>md / txt / tex / json 等支持直接在线编辑与实时保存</span></div></div>
      </div>
    </div></section>`;
}

function flLoadNode(rel=""){
  const project=flTreeProject || curTask, generation=flTreeGeneration, loading=flTreeLoading;
  if(loading.has(rel)) return loading.get(rel);
  const request=api(`/api/tree?id=${encodeURIComponent(project)}&rel=${encodeURIComponent(rel)}`).then(d=>{
    if(generation!==flTreeGeneration || project!==curTask) return;
    if(d.root) flTreeRoot = d.root;
    if(d.rows) flTreeCache[rel] = d.rows.map(row=>({...row,project_id:project}));
  }).catch(e=>{
    console.error("加载文件夹内容失败", rel, e);
  }).finally(()=>{
    loading.delete(rel);
    if(generation===flTreeGeneration && project===curTask) flFlattenRows();
  });
  loading.set(rel,request);
  return request;
}

async function flInitTree(){
  const list = $("flList"); if(!list) return;
  if(!curTask){ list.innerHTML = `<p class="fl-empty">先选一个项目</p>`; return; }
  const generation=++flTreeGeneration, project=curTask;
  if(flTreeProject!==project){flSel="";flTreeRoot="";}
  flTreeProject=project;flTreeLoading=new Map();
  list.innerHTML = `<p class="fl-empty">读目录…</p>`;
  flTreeCache = Object.create(null);
  await flLoadNode("");
  if(generation!==flTreeGeneration || project!==curTask || !list.isConnected) return;
  const tag = $("flRoot");
  if(tag && flTreeRoot){
    const parts = flTreeRoot.split(/[\\/]/).filter(Boolean);
    tag.textContent = parts.slice(-2).join("/"); tag.title = flTreeRoot;
  }
  flRenderTree();
  for(const rel of [...flExpanded].sort((a,b)=>a.split('/').length-b.split('/').length)){
    if(generation!==flTreeGeneration || project!==curTask || !list.isConnected)return;
    if(!flExpanded.has(rel)||!flFindRow(rel)?.dir||flTreeCache[rel])continue;
    const pending=flLoadNode(rel);flRenderTree();await pending;
    if(generation!==flTreeGeneration || project!==curTask || !list.isConnected)return;
    flRenderTree();
  }
}

function flRenderTree(){
  const list = $("flList"); if(!list) return;
  const focused=document.activeElement;
  const focusedRel=list.contains(focused)?focused.dataset.rel:undefined;
  const rootRows = flTreeCache[""] || [];
  if(!rootRows.length){
    list.innerHTML = `<p class="fl-empty">这个项目文件夹为空</p>`;
    return;
  }
  function renderSubTree(parentRel, depth){
    const rows = flTreeCache[parentRel] || [];
    let html = "";
    for(const r of rows){
      const isDir = !!r.dir;
      const isOpen = flExpanded.has(r.rel);
      const isSel = r.rel === flSel;
      const padLeft = 8 + depth * 14;
      if(isDir){
        const isLoading = flTreeLoading.has(r.rel);
        html += `
          <div class="fl-tree-row dir ${isOpen ? 'open' : ''} ${isSel ? 'on' : ''}" data-project-id="${esc(r.project_id)}" data-rel="${esc(r.rel)}" data-dir="1" aria-current="${isSel}" aria-expanded="${isOpen}" aria-busy="${isLoading}" style="padding-left:${padLeft}px" title="${esc(r.name)}">
            <span class="fl-chevron">${isLoading ? '⌛' : (isOpen ? '▾' : '▸')}</span>
            <span class="fl-folder-ico">${isOpen ? '📂' : '📁'}</span>
            <span class="fl-name">${esc(r.name)}</span>
            <span class="fl-count">${r.count || 0}</span>
          </div>`;
        if(isOpen){
          if(flTreeCache[r.rel]){
            html += renderSubTree(r.rel, depth + 1);
          } else if(isLoading){
            html += `<div class="fl-tree-row" style="padding-left:${padLeft + 16}px;color:var(--muted);font-size:11px">读取中…</div>`;
          }
        }
      } else {
        html += `
          <div class="fl-tree-row file ${isSel ? 'on' : ''}" data-project-id="${esc(r.project_id)}" data-rel="${esc(r.rel)}" data-dir="0" aria-current="${isSel}" style="padding-left:${padLeft}px" title="${esc(r.name)}">
            <span class="fl-indent-spacer"></span>
            <span class="fl-file-ico">${flGetIcon(r.ext)}</span>
            <span class="fl-name">${esc(r.name)}</span>
            <span class="fl-ext-badge">${esc((r.ext || '').toUpperCase())}</span>
          </div>`;
      }
    }
    return html;
  }
  list.innerHTML = `<div class="fl-tree-box">${renderSubTree("", 0)}</div>`;
  if(focusedRel!==undefined){
    const row=list.querySelector(`[data-rel="${CSS.escape(focusedRel)}"]`);
    if(row){row.tabIndex=0;row.focus({preventScroll:true});}
  }
}

function flBind(){
  const list = $("flList");
  if(list) list.onclick = async (e)=>{
    const row = e.target.closest(".fl-tree-row"); if(!row) return;
    const rel = row.dataset.rel;
    const isDir = row.dataset.dir === "1";
    if(isDir){
      if(flExpanded.has(rel)){
        flExpanded.delete(rel);
        flSaveExpanded();
        flRenderTree();
      } else {
        flExpanded.add(rel);
        flSaveExpanded();
        flRenderTree();
        if(!flTreeCache[rel]){
          await flLoadNode(rel);
          flRenderTree();
        }
      }
    } else {
      const r = flFindRow(rel);
      if(r) flShow(r);
    }
  };
  const rl = $("flReload"); if(rl) rl.onclick = ()=> flInitTree();
  const hand = $("flHand"); if(hand) hand.onclick = ()=>{
    if(!flSel){ flMsg("先在左边点一个文件"); return; }
    FileHandoff.open({project_id:curTask,relative:flSel});
  };
  const history = $("flHandoffHistory"); if(history) history.onclick=()=>flSel && FileHandoff.history({project_id:curTask,relative:flSel});
  const op = $("flOpen"); if(op) op.onclick = ()=>{
    const r = flFindRow(flSel);
    if(!r){ flMsg("先在左边点一个文件"); return; }
    api("/api/conv/open", { path: r.path }).catch(e => flMsg("打不开：" + e.message));
  };
  const modeTabs = document.querySelectorAll("#flModeTabs [data-fl-mode]");
  modeTabs.forEach(btn => {
    btn.onclick = () => {
      const ta = $("flEditText");
      if(ta && flCurrentFile) flCurrentFile.text = ta.value;
      flCurrentMode = btn.dataset.flMode;
      localStorage.setItem('rw-fl-mode', flCurrentMode);
      modeTabs.forEach(b => b.classList.toggle('active', b === btn));
      flRenderContent();
    };
  });
  const saveBtn = $("flSaveBtn");
  if(saveBtn) saveBtn.onclick = () => flSaveCurrent();
  return flInitTree();
}

let flCurrentFile = null;
let flCurrentMode = localStorage.getItem('rw-fl-mode') || 'split';

async function flShow(row){
  if(row.project_id && row.project_id!==curTask) return;
  const project = curTask;
  flSel = row.rel;
  $("flHand").disabled=false;
  $("flHand").title="交给当前工作流中的节点、团队或专家（Alt+Shift+H）";
  $("flHandoffHistory").disabled=false;
  FileHandoff.preview({project_id:project,relative:row.rel});
  document.querySelectorAll("#flList .fl-tree-row[data-rel]").forEach(el=>{
    const selected=el.dataset.rel===flSel;
    el.classList.toggle('on',selected);el.setAttribute('aria-current',String(selected));
  });
  if($("flName")) $("flName").textContent = row.name;
  flMsg([flFmtSize(row.size), row.at, row.rel.split("/").slice(0,-1).join("/")].filter(Boolean).join(" · "));
  const stage = $("flStage"); if(!stage) return;
  const modeTabs = $("flModeTabs");
  const saveBtn = $("flSaveBtn");
  stage.innerHTML = `<p class="fl-empty">打开中…</p>`;
  const ext = String(row.ext || "").toLowerCase();

  if(FL_TEXT.has(ext) || !ext){
    try{
      const d = await api(`/api/conv/read?path=${encodeURIComponent(row.path)}`);
      if(d.error) throw new Error(d.error);
      if(curTask!==project || flSel!==row.rel || !stage.isConnected) return;
      flCurrentFile = { row, projectId:project, text: d.text || "", originalText: d.text || "" };
      if(modeTabs) modeTabs.style.display = "inline-flex";
      if(saveBtn){
        saveBtn.style.display = "";
        saveBtn.textContent = "💾 保存";
      }
      flUpdateModeTabs();
      flRenderContent();
    }catch(e){
      if(modeTabs) modeTabs.style.display = "none";
      if(saveBtn) saveBtn.style.display = "none";
      stage.innerHTML = `<p class="fl-empty">读不了：${esc(e.message)}</p>`;
    }
    return;
  }

  // 非文本类文件（图片/PDF等）
  flCurrentFile = null;
  if(modeTabs) modeTabs.style.display = "none";
  if(saveBtn) saveBtn.style.display = "none";
  try{
    const d = await api(`/api/conv/preview?path=${encodeURIComponent(row.path)}`);
    if(d.error) throw new Error(d.error);
    if(curTask!==project || flSel!==row.rel || !stage.isConnected) return;
    if(d.kind === "image") stage.innerHTML = `<img class="fl-img" src="${d.url}&t=${Date.now()}" alt="${esc(row.name)}">`;
    else if(d.kind === "pdf" || d.kind === "html") stage.innerHTML = `<iframe class="fl-frame" src="${d.url}&t=${Date.now()}"></iframe>`;
    else stage.innerHTML = `<p class="fl-empty">这类文件这里不预览 —— 点右上角 ↗ 用系统程序打开</p>`;
  }catch(e){ stage.innerHTML = `<p class="fl-empty">打不开：${esc(e.message)}</p>`; }
}

function flUpdateModeTabs(){
  document.querySelectorAll("#flModeTabs [data-fl-mode]").forEach(b => {
    b.classList.toggle("active", b.dataset.flMode === flCurrentMode);
  });
}

function flRenderContent(){
  const stage = $("flStage");
  if(!stage || !flCurrentFile) return;
  const ext = String(flCurrentFile.row.ext || "").toLowerCase();
  const isMd = ext === "md" || ext === "markdown";

  if(flCurrentMode === "preview"){
    if(isMd){
      stage.innerHTML = `<div class="fl-editor-preview md-render">${renderMarkdown(flCurrentFile.text)}</div>`;
    } else {
      stage.innerHTML = `<pre class="fl-doc"><code>${esc(flCurrentFile.text)}</code></pre>`;
    }
  } else if(flCurrentMode === "edit"){
    stage.innerHTML = `<textarea class="fl-editor-textarea" id="flEditText" spellcheck="false" placeholder="在此编辑文件内容…">${esc(flCurrentFile.text)}</textarea>`;
    const ta = $("flEditText");
    if(ta){
      ta.oninput = () => {
        flCurrentFile.text = ta.value;
        const btn = $("flSaveBtn");
        if(btn) btn.textContent = flCurrentFile.text !== flCurrentFile.originalText ? "💾 保存 *" : "💾 保存";
      };
      ta.onkeydown = flEditorKeydown;
    }
  } else { // split
    stage.innerHTML = `
      <div class="fl-editor-split">
        <div class="fl-split-pane">
          <textarea class="fl-editor-textarea" id="flEditText" spellcheck="false" placeholder="在此编辑文件内容…">${esc(flCurrentFile.text)}</textarea>
        </div>
        <div class="fl-split-divider"></div>
        <div class="fl-split-pane fl-split-preview md-render" id="flSplitPreview">
          ${isMd ? renderMarkdown(flCurrentFile.text) : `<pre class="fl-doc"><code>${esc(flCurrentFile.text)}</code></pre>`}
        </div>
      </div>`;
    const ta = $("flEditText"), prev = $("flSplitPreview");
    if(ta){
      ta.oninput = () => {
        flCurrentFile.text = ta.value;
        if(prev) prev.innerHTML = isMd ? renderMarkdown(ta.value) : `<pre class="fl-doc"><code>${esc(ta.value)}</code></pre>`;
        const btn = $("flSaveBtn");
        if(btn) btn.textContent = flCurrentFile.text !== flCurrentFile.originalText ? "💾 保存 *" : "💾 保存";
      };
      ta.onkeydown = flEditorKeydown;
    }
  }
}

function flEditorKeydown(e){
  if((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")){
    e.preventDefault();
    e.stopPropagation();
    flSaveCurrent();
    return;
  }
  if(e.key === "Tab"){
    e.preventDefault();
    const start = this.selectionStart, end = this.selectionEnd;
    this.value = this.value.substring(0, start) + "  " + this.value.substring(end);
    this.selectionStart = this.selectionEnd = start + 2;
    this.dispatchEvent(new Event("input"));
  }
}

async function flSaveCurrent(){
  if(!flCurrentFile || !flCurrentFile.row) return false;
  const file = flCurrentFile;
  const ta = $("flEditText");
  if(ta) file.text = ta.value;
  const savedText = file.text;
  const btn = $("flSaveBtn");
  if(btn) btn.textContent = "保存中…";
  try{
    const res = await api("/api/conv/save", {
      path: file.row.path,
      text: savedText
    });
    if(res.error) throw new Error(res.error);
    file.originalText = savedText;
    if(btn && flCurrentFile===file) btn.textContent = file.text===savedText ? "💾 保存" : "💾 保存 *";
    uiNotify(`已保存 ${file.row.name} (${flFmtSize(res.bytes || savedText.length)})`);
    return file.text===savedText;
  }catch(e){
    if(btn) btn.textContent = "💾 保存 *";
    uiNotify("保存失败: " + e.message);
    return false;
  }
}
window.flSaveCurrent = flSaveCurrent;
