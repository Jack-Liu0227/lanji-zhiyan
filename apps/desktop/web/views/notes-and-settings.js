// 打开 Zotero PDF 事件委托
document.addEventListener("click", async (e)=>{
  const b = e.target.closest(".zot-open-pdf");
  if(!b) return;
  const key = b.dataset.zotPdf;
  const old = b.textContent;
  try {
    b.disabled = true; b.textContent = "打开中…";
    const r = await api(`/api/zotero/open_pdf?key=${encodeURIComponent(key)}`);
    if(r.error) throw new Error(r.error);
    if(typeof uiNotify === "function") uiNotify("已在本地阅读器中打开 PDF");
  } catch(err) {
    if(typeof uiNotify === "function") uiNotify("打开失败：" + err.message);
  } finally {
    b.disabled = false; b.textContent = old;
  }
});
function markdownNoteClean(html){
  return (window.DOMPurify && window.marked)
    ? window.DOMPurify.sanitize(html, {ADD_TAGS:["mark","math","semantics","annotation","mrow","mi","mo","mn","msup","msub","mfrac","mspace","mtext"],
        ADD_ATTR:["align","style","target","rel","encoding","xmlns","aria-hidden"]})
    : html;
}
function renderMarkdown(value){
  const source = String(value||"");
  if(!window.marked || !window.marked.parse) return esc(source).replace(/\n/g,"<br />");
  const previousSlots=markdownMathSlots;
  markdownMathSlots=[];
  try{
    const root=document.createElement("div");
    root.innerHTML=markdownNoteClean(window.marked.parse(source));
    root.querySelectorAll("[data-rw-math]").forEach(node=>{
      const math=markdownMathSlots[Number(node.dataset.rwMath)];
      node.removeAttribute("data-rw-math");
      if(!math) return;
      if(!window.katex){ node.textContent=math.text; return; }
      window.katex.render(math.text,node,{displayMode:math.display,throwOnError:false,
        trust:false,maxExpand:1000,maxSize:20,output:"htmlAndMathml"});
    });
    return root.innerHTML;
  }
  catch(e){ return esc(source).replace(/\n/g,"<br />"); }
  finally{ markdownMathSlots=previousSlots; }
}
function mdActive(){ return mdNotes.find(n=>n.id===mdActiveId) || null; }
const mdTaskName = (id)=> (S.tasks||[]).find(t=>t.id===id)?.title || "";
function mdPaintList(){
  const box=$("mdNoteList"); if(!box) return;
  const q = ($("mdNoteSearch")?.value||"").trim().toLowerCase();
  const list = mdNotes.filter(n=>!q || `${n.title} ${n.content}`.toLowerCase().includes(q));
  box.innerHTML = list.length ? list.map(n=>`<div class="md-note-item${n.id===mdActiveId?" active":""}" data-md-note="${n.id}">
      <b>${esc(n.title||"未命名笔记")}</b>${n.task_id?`<em class="md-bind">📁 ${esc(mdTaskName(n.task_id)||"已绑项目")}</em>`:""}<span>${fmtDate(n.updated_at)}</span></div>`).join("")
    : `<div class="hint" style="padding:8px">没有匹配的笔记</div>`;
}
function mdPaintPreview(){ const p=$("mdPreview"); if(p) p.innerHTML = renderMarkdown($("mdSource")?.value||""); }
function mdPaint(){
  mdPaintList();
  const note = mdActive();
  [$("mdTask"), $("btnMdFocus"), $("btnMdNarrow"), $("btnMdFocusOut"), $("btnMdDelete")]
    .forEach(el => { if(el) el.style.display = ""; });   // 从项目笔记回来时把这几个还原（mdPaintProj 藏过）
  $("mdEmpty").classList.toggle("hidden", !!note);
  $("mdEditorShell").classList.toggle("hidden", !note);
  if(!note) return;
  $("mdNoteTitle").readOnly = false;
  $("mdSource").readOnly = false;
  $("mdToolbar").querySelectorAll("[data-md-command]").forEach(el => { el.disabled = false; });
  $("mdNoteTitle").value = note.title || "未命名笔记";
  $("mdSource").value = note.content || "";
  mdPaintTaskSel(note);                              // 「绑定项目」下拉（空=不绑）
  $("mdSaveState").textContent = "";
  mdPaintPreview();
}
function mdPaintTaskSel(note){
  const sel = $("mdTask"); if(!sel) return;
  const html = `<option value="">不绑定项目</option>` +
    (S.tasks||[]).map(t=>`<option value="${t.id}">${esc(t.title)}</option>`).join("");
  if(sel.dataset.sig !== html){ sel.innerHTML = html; sel.dataset.sig = html; }   // 只在项目列表变了才重建，别把展开的下拉收掉
  sel.value = note.task_id || "";
}
function mdFocus(on, silent){
  /* 专注模式：左栏/工具栏都收掉，正文变一长列居中（读论文时放旁边随手记）。
     进入时顺手折叠左侧导航；Esc / 「退出专注」回来。 */
  mdFocusOn = !!on;
  const v = $("mdView"); if(!v) return;
  const ws = $("mdWorkspace");
  if(mdFocusOn){
    mdFocusPrev = (ws?.className||"").replace("md-workspace","").trim() || "split";
    if(ws) ws.className = "md-workspace write";
    if(!sidebarCollapsed){ sidebarCollapsed = true; localStorage.setItem("rw-sidebar","true"); renderSidebar(); }
  }else{
    if(ws) ws.className = "md-workspace " + mdFocusPrev;
    $("mdToolbar")?.querySelectorAll("[data-md-mode]").forEach(x=>x.classList.toggle("active", x.dataset.mdMode===mdFocusPrev));
  }
  v.classList.toggle("focus", mdFocusOn);
  if(!silent) localStorage.setItem("rw-md-focus", mdFocusOn ? "true" : "false");
}
async function mdNarrow(){
  /* 窄栏 = 整扇窗一张白纸：服务端把窗口钉到右缘，页面上只留「笔记名 + 退出」。 */
  mdNarrowOn = !mdNarrowOn;
  const b = $("btnMdNarrow");
  try{
    await winCommand(mdNarrowOn ? "narrow" : "wide");
    $("mdView")?.classList.toggle("narrow", mdNarrowOn);
    document.body.classList.toggle("md-narrow", mdNarrowOn);       // 外壳（侧栏/标签/状态栏）也收掉
    if(b) b.textContent = mdNarrowOn ? "▯ 铺满" : "▯ 窄栏";
  }catch(e){ mdNarrowOn = !mdNarrowOn; mdFail(e, "切窄栏"); }        // 失败就把状态翻回去，别骗人
}
function mdFail(e, what){
  // 前端已更新、服务端还是旧的（接口 404）时最容易撞上：把话说清楚，别静默失败
  const hint = /not found|404/i.test(e?.message||"") ? "（服务端还是旧版本，重启一次「览籍智研」）" : "";
  console.error("笔记操作失败:", what, e);
  uiNotify(`${what}失败：${e.message}${hint}`);
}
async function mdLoad(){
  try{
    const d = await api("/api/mdnotes");
    mdNotes = d.notes||[]; mdLoaded = true;
  }catch(e){ mdNotes = []; mdLoaded = true; mdFail(e, "读取笔记"); }
  if(mdActiveId && !mdActive()) mdActiveId = null;
  if(!mdActiveId && mdNotes.length) mdActiveId = mdNotes[0].id;
  mdPaint();
}
async function mdNew(title="未命名笔记", content="", focusSource=false){
  try{
    if(mdActive()) await mdSave();
    const d = await api("/api/mdnotes/save",{title, content});
    mdNotes.unshift(d.note); mdActiveId = d.note.id;
    mdPaint();
    setTimeout(()=> (focusSource ? $("mdSource") : $("mdNoteTitle"))?.focus(), 30);
  }catch(e){ mdFail(e, "新建笔记"); }
}
async function mdSave(){
  clearTimeout(mdSaveTimer);
  if(mdProj) return mdSaveProj();
  const note = mdActive(); if(!note) return;
  const title = $("mdNoteTitle").value.trim() || "未命名笔记";
  const content = $("mdSource").value;
  const task_id = ($("mdTask")?.value) || "";
  $("mdSaveState").textContent = "保存中…";
  try{
    const d = await api("/api/mdnotes/save",{id:note.id, title, content, task_id});
    Object.assign(note, d.note);
    $("mdSaveState").textContent = "已保存";
    mdPaintList();
  }catch(e){ $("mdSaveState").textContent = "保存失败"; $("mdSaveState").title = e.message; console.error("笔记自动保存失败:", e); }
}
function mdScheduleSave(){
  if(mdProj?.read_only) return;
  if(mdProj){                                        // 项目笔记：同样 650ms 防抖，只是落到项目里的 .md
    mdProj.title = $("mdNoteTitle").value.trim();
    mdProj.content = $("mdSource").value;
    mdProj.editVersion = (mdProj.editVersion || 0) + 1;
    $("mdSaveState").textContent = "未保存";
    mdPaintPreview();
    const note = mdProj;
    clearTimeout(mdSaveTimer); mdSaveTimer = setTimeout(() => { if(mdProj === note) mdSaveProj(); }, 650);
    return;
  }
  const note = mdActive(); if(!note) return;
  note.title = $("mdNoteTitle").value.trim() || "未命名笔记";
  note.content = $("mdSource").value;
  note.task_id = ($("mdTask")?.value) || "";         // 绑定的项目（空=不绑）
  $("mdSaveState").textContent = "未保存";
  mdPaintPreview();
  clearTimeout(mdSaveTimer);
  mdSaveTimer = setTimeout(mdSave, 650);
}
function mdInsert(before, after="", placeholder=""){
  const ta=$("mdSource"), s=ta.selectionStart, e=ta.selectionEnd;
  ta.setRangeText(before + (ta.value.slice(s,e)||placeholder) + after, s, e, "end");
  ta.focus(); mdScheduleSave();
}
function mdPrefixLines(prefix, ordered=false){
  const ta=$("mdSource");
  const start = ta.value.lastIndexOf("\n", ta.selectionStart-1)+1;
  const nextNl = ta.value.indexOf("\n", ta.selectionEnd);
  const end = nextNl<0 ? ta.value.length : nextNl;
  const lines = ta.value.slice(start,end).split("\n");
  ta.setRangeText(lines.map((l,i)=>(ordered?`${i+1}. `:prefix)+l.replace(/^(?:#{1,6}|[-*>]|\d+[.])\s+/,"")).join("\n"), start, end, "select");
  ta.focus(); mdScheduleSave();
}
function mdCmd(command, value=""){
  if(!$("mdSource") || $("mdSource").readOnly) return;
  if(command==="heading") return mdPrefixLines(value?"#".repeat(Number(value))+" ":"");
  if(command==="bold") return mdInsert("**","**","加粗文字");
  if(command==="italic") return mdInsert("*","*","斜体文字");
  if(command==="highlight") return mdInsert("<mark>","</mark>","高亮文字");
  if(command==="align"&&value) return mdInsert(`<div align="${value}">\n`,"\n</div>","对齐内容");
  if(command==="font"&&value) return mdInsert(`<span style="font-family: ${value}">`,"</span>","文字");
  if(command==="size"&&value) return mdInsert(`<span style="font-size: ${value}">`,"</span>","文字");
  if(command==="ul") return mdPrefixLines("- ");
  if(command==="ol") return mdPrefixLines("", true);
  if(command==="quote") return mdPrefixLines("> ");
  if(command==="link") return mdInsert("[","](https://)","链接文字");
  if(command==="code") return mdInsert("```text\n","\n```","在这里输入代码");
  if(command==="formula") return mdInsert("$$\n","\n$$","E = mc^2");
  if(command==="table") return mdInsert("","","| 列 1 | 列 2 | 列 3 |\n| --- | --- | --- |\n| 内容 | 内容 | 内容 |");
}
async function mdImport(files){
  if(mdDashReadOnly && (route()==="space" || route()==="notes")) return;
  const input=[...(files||[])], list=input.filter(f=>/\.(?:md|markdown)$/i.test(f.name));
  if(!list.length) return uiNotify("请选择 .md 或 .markdown 文件（笔记仅读取 Markdown）");
  if(mdProj || spTab === "notes"){
    const targetFolder = (mdProj && mdProj.folder) || mdDashSelectedFolder || "笔记/文献精读笔记";
    let n = 0;
    for(const f of list.slice(0, 50)){
      if(f.size > 5*1024*1024) continue;
      try{
        const content = (await f.text()).replace(/^\uFEFF/,"");
        const title = f.name.replace(/\.(?:md|markdown)$/i,"") || "导入笔记";
        const safe = title.replace(/[\\/:*?"<>|]/g,"_").slice(0,100);
        const rel = `${targetFolder}/${safe}.md`;
        await api("/api/project/note/save", {project_id: mdDashProject, path: rel, title, content, create:true,
          ...(!/^---\s*\r?\n/.test(content)&&window.Notebook&&!Notebook.fileMode?{metadata:Notebook.metadataForNew(targetFolder)}:{})});
        n++;
      }catch(e){ uiNotify(`无法导入「${f.name}」：${e.message}`); }
    }
    if(n){
      uiNotify(`成功导入 ${n} 篇笔记至 ${targetFolder}`);
      mdDashLoad();
    }
    return;
  }
  let n=0;
  for(const f of list.slice(0,50)){
    if(f.size > 5*1024*1024) continue;
    try{
      const content=(await f.text()).replace(/^\uFEFF/,"");
      await mdNew(f.name.replace(/\.(?:md|markdown)$/i,"")||"导入笔记", content, true); n++;
    }catch(e){ uiNotify(`无法读取「${f.name}」：${e.message}`); }
  }
  if(n) $("mdSaveState").textContent = `已导入 ${n} 篇`;
}
async function mdExport(fmt){
  const note = mdProj ? {title: $("mdNoteTitle").value.trim() || "未命名笔记", content: $("mdSource").value}
                      : mdActive();
  if(!note) return;
  await mdSave();
  if(mdProj && fmt!=="pdf" && mdProj.saved_content) note.content=mdProj.saved_content;
  if(fmt==="pdf"){                                   // 走浏览器打印 → 另存为 PDF
    const w = window.open("", "_blank");
    if(!w) return uiNotify("浏览器拦了打印窗口");
    w.document.write(`<title>${esc(note.title)}</title><link rel="stylesheet" href="/vendor/katex/katex.min.css">
<link rel="stylesheet" href="/vendor/starmap.css">`
      + `<style>body{font:14px/1.75 "Microsoft YaHei",sans-serif;padding:32px;max-width:860px;margin:auto}`
      + `pre{background:#202124;color:#f4f5f7;padding:12px;border-radius:6px;overflow:auto}`
      + `table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px 9px}`
      + `blockquote{border-left:3px solid #888;margin:.8em 0;padding:.25em 1em;color:#444}</style>`
      + `<body>${renderMarkdown(note.content)}</body>`);
    w.document.close(); setTimeout(()=>{ w.focus(); w.print(); }, 350);
    return;
  }
  const blob = new Blob([note.content||""], {type:"text/markdown;charset=utf-8"});
  const url = URL.createObjectURL(blob), a = document.createElement("a");
  a.href=url; a.download = `${note.title||"note"}.md`; a.click();
  setTimeout(()=>URL.revokeObjectURL(url), 1000);
}
function mdBind(m){
  if(!$("mdNoteList")) return;
  $("btnMdNew").onclick = ()=>mdNew();
  $("btnMdImport").onclick = ()=>$("mdFileInput").click();
  $("mdFileInput").onchange = (e)=>{ mdImport(e.target.files); e.target.value=""; };
  $("mdNoteSearch").oninput = mdPaintList;
  $("btnMdZotero").onclick = zotOpen;
  $("mdNoteList").onclick = (e)=>{
    const it = e.target.closest("[data-md-note]");
    if(!it || it.dataset.mdNote===mdActiveId) return;
    mdSave().then(()=>{ mdActiveId = it.dataset.mdNote; mdPaint(); });
  };
  $("mdSource").oninput = mdScheduleSave;
  $("mdNoteTitle").oninput = mdScheduleSave;
  $("mdTask").onchange = ()=>{ const b=$("mdTask"); const n=mdActive(); if(!n) return;
    n.task_id = b.value; mdPaintList(); mdSave(); };      // 换绑定立刻落库，列表上的胶囊跟着走
  $("mdToolbar").onclick = (e)=>{
    const b = e.target.closest("button[data-md-command]");
    if(b) mdCmd(b.dataset.mdCommand, b.value);
    const mode = e.target.closest("[data-md-mode]");
    if(mode){
      $("mdToolbar").querySelectorAll("[data-md-mode]").forEach(x=>x.classList.toggle("active", x===mode));
      $("mdWorkspace").className = "md-workspace " + mode.dataset.mdMode;
    }
  };
  $("mdToolbar").onchange = (e)=>{
    const sel = e.target.closest("select[data-md-command]"); if(!sel) return;
    mdCmd(sel.dataset.mdCommand, sel.value); sel.value = "";
  };
  $("btnMdExport").onclick = ()=>mdExport("md");
  $("btnMdPdf").onclick = ()=>mdExport("pdf");
  $("btnMdFocus").onclick = ()=>mdFocus(true);
  $("btnMdFocusOut").onclick = ()=>{ if(mdNarrowOn) mdNarrow(); mdFocus(false); };   // 窄栏里点退出 = 退出专注 + 回铺满
  $("btnMdNarrow").onclick = ()=>mdNarrow();
  $("btnMdDelete").onclick = async ()=>{
    const note = mdActive();
    if(!note || !await uiConfirm(`确认删除笔记「${note.title}」？`)) return;
    await api("/api/mdnotes/delete",{id:note.id});
    mdNotes = mdNotes.filter(x=>x.id!==note.id);
    mdActiveId = mdNotes[0]?.id || null;
    mdPaint();
  };
  const zone = $("mdDropZone");
  ["dragenter","dragover"].forEach(t=>zone.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation(); zone.classList.add("dragging"); }));
  ["dragleave","drop"].forEach(t=>zone.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation(); zone.classList.remove("dragging"); }));
  zone.addEventListener("drop",(e)=>mdImport(e.dataTransfer.files));
  const root = $("mdView");
  ["dragenter","dragover"].forEach(t=>root.addEventListener(t,(e)=>{
    if(![...(e.dataTransfer?.files||[])].some(f=>/\.(?:md|markdown)$/i.test(f.name))) return;
    e.preventDefault(); e.stopPropagation(); root.classList.add("md-view-dragging");
  }));
  ["dragleave","drop"].forEach(t=>root.addEventListener(t,(e)=>{
    if(t==="drop"){ e.preventDefault(); e.stopPropagation(); mdImport(e.dataTransfer.files); }
    root.classList.remove("md-view-dragging");
  }));
}
/* ---------------- Zotero 接入（只读；本机 Local API 127.0.0.1:23119） ----------------
   不读 zotero.sqlite、不写回、不后台同步。取到的条目走原有「新建笔记」，所以前台流程不变。 */
let zotRows = [], zotStart = 0, zotMore = false, zotSelectedKeys = new Set();
const zotSub = (it)=>[ (it.creators||[]).slice(0,3).join(", "), it.year, it.venue ].filter(Boolean).join(" · ");
function zotRowHTML(it){
  const badge = it.type === "note" ? `<em class="md-bind" style="background:#4ade8022;color:#4ade80">📝 笔记</em>`
              : (it.doi ? `<em class="md-bind" style="background:#60a5fa22;color:#60a5fa" title="DOI: ${esc(it.doi)}">DOI</em>` : "");
  const venue = it.venue ? `<span style="font-size:11px;background:var(--bg-sub);color:var(--muted);padding:1px 6px;border-radius:4px">${esc(it.venue)}</span>` : "";
  const tags = (it.tags || []).slice(0, 4).map(t => `<span class="pill sm" style="font-size:10.5px">#${esc(t)}</span>`).join(" ");
  const checked = zotSelectedKeys.has(it.key) ? "checked" : "";
  return `<div class="zot-row-card" data-zot-key="${esc(it.key)}" style="display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--card)">
      <input type="checkbox" class="zot-cb" data-zot-cb="${esc(it.key)}" ${checked} style="margin-top:4px;cursor:pointer">
      <div style="flex:1;min-width:0;cursor:pointer" data-zot-select="${esc(it.key)}">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <b style="font-size:13.5px;color:var(--fg)">${esc(it.title || "(无标题)")}</b>
          ${badge} ${venue}
        </div>
        <div style="color:var(--muted);font-size:12px;margin:3px 0">${esc(zotSub(it))}</div>
        ${it.abstract ? `<div style="color:var(--muted);font-size:11.5px;line-height:1.4;max-height:2.8em;overflow:hidden;opacity:0.85">${esc(it.abstract)}</div>` : ""}
        ${tags ? `<div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${tags}</div>` : ""}
      </div>
      <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0">
        <button class="btn sm ghost zot-open-pdf" data-zot-pdf="${esc(it.key)}" title="直接在系统阅读器中打开该文献本地 PDF" style="padding:2px 8px;font-size:11.5px;white-space:nowrap;color:var(--accent)">📄 打开PDF</button>
        <button class="btn sm ghost zot-single-note" data-zot-note="${esc(it.key)}" title="将题录与批注存为单篇本地笔记" style="padding:2px 8px;font-size:11.5px;white-space:nowrap">存为笔记</button>
      </div>
  </div>`;
}
function zotClose(){ uiCloseModal($("zoteroModal")); }
async function zotOpen(){
  const box = $("zoteroModal"); if(!box) return;
  uiOpenModal(box, $("zotQuery"));
  $("zotStatus").textContent = "正在连接 Zotero 本地库…";
  $("zotList").innerHTML = ""; $("zotCount").textContent = ""; $("zotCollection").innerHTML = "";
  zotSelectedKeys.clear();
  if($("zotSelectAll")) $("zotSelectAll").checked = false;
  try{
    const st = await api("/api/zotero/status");
    $("zotStatus").textContent = st.message || "";
    if(!st.readable) return;                       // 没启动 / 版本太老：只显示中文状态，不假装连上了
    const cols = await api("/api/zotero/collections");
    $("zotCollection").innerHTML = `<option value="">全库（按修改时间）</option>` +
      (cols.collections || []).map(c=>`<option value="${esc(c.key)}">${esc(c.name)}（${c.count}）</option>`).join("");
    await zotLoad(true);
  }catch(e){ $("zotStatus").textContent = "读 Zotero 失败：" + e.message; }
}
window.zotOpen = zotOpen;
async function zotLoad(reset){
  if(reset){
    zotStart = 0;
    zotSelectedKeys.clear();
    if($("zotSelectAll")) $("zotSelectAll").checked = false;
  }
  const qs = new URLSearchParams({ collection: $("zotCollection").value,
    q: $("zotQuery").value.trim(), start: String(zotStart), limit: "30" });
  const d = await api("/api/zotero/items?" + qs.toString());
  zotRows = reset ? (d.items || []) : zotRows.concat(d.items || []);
  zotMore = !!d.hasMore; zotStart = zotRows.length;
  $("zotCount").textContent = (d.total != null) ? (zotRows.length + "/" + d.total + " 条") : (zotRows.length + " 条");
  $("zotList").innerHTML = zotRows.length
    ? zotRows.map(zotRowHTML).join("") + (zotMore ? `<button class="btn ghost" id="zotMore" style="margin:8px">加载更多</button>` : "")
    : `<div class="hint" style="padding:8px">没有匹配的条目</div>`;
  const more = $("zotMore"); if(more) more.onclick = ()=>zotLoad(false).catch(e=>mdFail(e,"加载更多"));
}
async function zotImport(key){
  try{
    const d = await api("/api/zotero/note?key=" + encodeURIComponent(key));
    zotClose();
    await mdNew(d.title || "Zotero 条目", d.content || "", true);   // 复用原有新建流程
  }catch(e){ mdFail(e, "导入 Zotero 条目"); }
}
function zotBind(){
  const box = $("zoteroModal"); if(!box) return;
  box.querySelector(".modal-mask").onclick = zotClose;
  $("btnZotClose").onclick = zotClose;
  function updateZotSelectCount(){
    const n = zotSelectedKeys.size;
    const btn = $("zotToProject");
    if(btn){
      btn.textContent = n > 0 ? `导入所选 ${n} 篇到项目` : "导入当前分类到项目";
    }
  }
  const selAll = $("zotSelectAll");
  if(selAll){
    selAll.onchange = (e)=>{
      const on = e.target.checked;
      zotRows.forEach(r => { if(on) zotSelectedKeys.add(r.key); else zotSelectedKeys.delete(r.key); });
      box.querySelectorAll(".zot-cb").forEach(cb => { cb.checked = on; });
      updateZotSelectCount();
    };
  }
  $("zotCollection").onchange = ()=>zotLoad(true).catch(e=>mdFail(e,"读条目"));
  $("zotSearch").onclick = ()=>zotLoad(true).catch(e=>mdFail(e,"搜索"));
  $("zotQuery").onkeydown = (e)=>{ if(e.key === "Enter") zotLoad(true).catch(err=>mdFail(err,"搜索")); };
  $("zotToProject").onclick = async ()=>{
    if(!curTask) return uiNotify("请先在顶部选择或新建一个项目，再导入文献");
    const btn = $("zotToProject"); btn.disabled = true;
    $("zotStatus").textContent = "正在导入题录并自动解析本机 PDF 附件…";
    const selKeys = Array.from(zotSelectedKeys);
    const body = {task_id: curTask};
    if(selKeys.length){
      body.keys = selKeys;
    } else {
      body.collection = $("zotCollection").value;
    }
    try{
      const d = await api("/api/workflow/zotero", body);
      $("zotStatus").textContent = `✓ 成功导入 ${d.total} 条（已入库 ${d.downloaded} 篇，PDF 已就绪）。知识星图已同步更新！`;
      if(typeof dlRefresh === "function") dlRefresh().catch(()=>{});
      try{
        await api("/api/task/papergraph/build", {id: curTask});
        const fr = document.getElementById("kbFrame");
        if(fr) fr.src = "/kb.html?theme=" + (document.documentElement.dataset.theme||"dark") + "&rebuild=1";
      }catch(_){}
      await refreshState(false);
      if(route() === "space" || route() === "library") render();
    }catch(e){ $("zotStatus").textContent = "导入失败：" + e.message; }
    finally{ btn.disabled = false; updateZotSelectCount(); }
  };
  $("zotList").onclick = (e)=>{
    if(e.target.closest("#zotMore")) return;
    const noteBtn = e.target.closest("[data-zot-note]");
    if(noteBtn){
      e.stopPropagation();
      return zotImport(noteBtn.dataset.zotNote);
    }
    const cb = e.target.closest("[data-zot-cb]");
    if(cb){
      const k = cb.dataset.zotCb;
      if(cb.checked) zotSelectedKeys.add(k); else zotSelectedKeys.delete(k);
      updateZotSelectCount();
      return;
    }
    const itemRow = e.target.closest("[data-zot-select]");
    if(itemRow){
      const k = itemRow.dataset.zotSelect;
      const targetCb = itemRow.parentElement.querySelector(`[data-zot-cb="${k}"]`);
      if(targetCb){
        targetCb.checked = !targetCb.checked;
        if(targetCb.checked) zotSelectedKeys.add(k); else zotSelectedKeys.delete(k);
        updateZotSelectCount();
      }
    }
  };
}
zotBind();

/* ---------------- 定时任务（规则卡 + 运行记录；语义「只在应用活着时调度」） ---------------- */
let schRows = [], schRuns = [], schFreq = {}, schRunStatus = {}, schRetryable = [], schEdit = null, schFeedback = "";
const SCH_PRESETS = {manual:"", hourly:"0 * * * *", daily:"0 9 * * *", weekdays:"0 9 * * 1-5", weekly:"0 9 * * 1"};

function schFreqLabel(k){ return schFreq[k] || k; }
function schStatusPill(s){ return `<span class="pill ${s==="completed"?"ok":(s==="running"?"":"warn")}">${esc(schRunStatus[s]||s)}</span>`; }
function schWhen(iso){ return iso ? fmtDate(iso) : "—"; }
function schEditSeed(row){
  return { id: row?.id||"", name: row?.name||"每日科研摘要", prompt: row?.prompt||"请根据项目中的最新文献与笔记，生成一份可核验的研究摘要。",
    project_id: row?.project_id||curTask||"", assistant_id: row?.assistant_id||"", workflow_id: row?.workflow_id||"", frequency: row?.frequency||"daily", cron: row?.cron||"0 9 * * *",
    output_dir: row?.output_dir||"定时任务", todo_id:row?.todo_id||"", enabled: row ? !!row.enabled : true };
}
function schEditorHTML(){
  if(!schEdit) return "";
  const e = schEdit;
  const projOpts = `<option value="">未绑定（运行会被阻断）</option>` +
    (S.tasks||[]).map(t=>`<option value="${t.id}" ${t.id===e.project_id?"selected":""}>${esc(t.title)}</option>`).join("");
  const assistantOpts = `<option value="">请选择专家</option>` +
    (S.assistants||[]).filter(a=>a.enabled).map(a=>`<option value="${esc(a.id)}" ${a.id===e.assistant_id?"selected":""}>${esc(a.name)}</option>`).join("");
  const freqOpts = Object.keys(schFreq).length ? Object.keys(schFreq) : Object.keys(SCH_PRESETS);
  return `<div class="card" style="margin-top:12px"><div class="sch-head">
    <div><p class="sch-eyebrow">${e.id?"EDIT SCHEDULE":"NEW SCHEDULE"}</p>
      <h3>${e.id?"编辑定时任务":"新建定时任务"}</h3></div>
    <span class="pill">内置默认 09:00 · 本机时区</span>
    <button class="btn ghost" id="schCancel">取消</button></div>
    <div class="sch-grid">
      <label><span>任务名称</span><input id="schName" value="${esc(e.name)}"></label>
      <label><span>频率</span><select id="schFreq">${freqOpts.map(k=>`<option value="${k}" ${k===e.frequency?"selected":""}>${esc(schFreqLabel(k))}</option>`).join("")}</select></label>
      <label><span>Cron（分 时 日 月 周）</span><input id="schCron" value="${esc(e.cron)}" style="font-family:ui-monospace,Consolas,monospace"></label>
      <label><span>项目</span><select id="schProj">${projOpts}</select></label>
       <label><span>执行专家</span><select id="schAssistant">${assistantOpts}</select></label>
       <label><span>执行完整工作流（可选）</span><select id="schWorkflow"><option value="">只运行所选专家</option></select></label>
       <label><span>关联项目待办（可选）</span><select id="schTodo"><option value="">正在读取…</option></select></label>
      <label><span>输出目录（项目目录内的相对路径）</span><input id="schOut" value="${esc(e.output_dir)}"></label>
      <label><span>启用</span><select id="schOn"><option value="1" ${e.enabled?"selected":""}>已启用</option><option value="0" ${e.enabled?"":"selected"}>已暂停</option></select></label>
      <label class="full"><span>任务目标（工作流按节点配置选择团队、助手、工具和技能）</span><textarea id="schPrompt">${esc(e.prompt)}</textarea></label>
    </div>
    <div class="hint" style="margin-top:10px">输出目录为项目目录内的相对目录，保存与运行前都会再校验一次：绝对路径、盘符、<code>..</code>、隐藏目录（如 <code>.obsidian</code>）会被拒绝。</div>
    <div class="pf-actions" style="margin-top:12px"><button class="btn ghost" id="schCancel2">取消</button><button class="btn" id="schSave">${e.id?"更新任务":"保存任务"}</button></div></div>`;
}
function schCardHTML(r){
  const next = r.enabled ? (r.next_run_at ? fmtDate(r.next_run_at) : "未排期") : "已暂停";
  return `<div class="sch-card ${r.enabled?"":"off"}">
    <div class="ic">⏰</div>
    <div class="main"><div class="nmrow"><span class="nm">${esc(r.name)}</span>
      <span class="pill ${r.enabled?"ok":"warn"}">${r.enabled?"已启用":"已暂停"}</span>
      <span class="pill">${esc(schFreqLabel(r.frequency))} · ${esc(r.cron)}</span></div>
       <div class="ln">${r.project_id?esc(schTaskName(r.project_id)||"已绑定项目"):"未绑定项目"} · ${r.workflow_id?'完整工作流':esc((S.assistants||[]).find(a=>a.id===r.assistant_id)?.name||"未绑定专家")} · 输出 ${esc(r.output_dir)}</div>
      <div class="ln">${esc((r.prompt||"").replace(/\s+/g," ").slice(0,110))}</div>
      <div class="ln">下次 ${esc(next)} · 最近 ${esc(r.last_run_at?fmtDate(r.last_run_at):"尚未运行")}</div></div>
    <div class="acts">
      <button class="btn ghost" data-sch-run="${r.id}" title="立即运行一次">▶ 立即运行</button>
      <button class="btn ghost" data-sch-toggle="${r.id}" data-on="${r.enabled?1:0}">${r.enabled?"⏸ 暂停":"✓ 启用"}</button>
      <button class="btn ghost" data-sch-edit="${r.id}">✎ 编辑</button>
      <button class="btn ghost" data-sch-del="${r.id}" style="color:var(--err)">🗑 归档</button></div></div>`;
}
function schRunHTML(e){
  const retry = schRetryable.includes(e.status);
  return `<div class="sch-run">
    <span class="nm">${esc(e.name)}</span>${schStatusPill(e.status)}
    <span class="pill">${e.trigger==="manual"?"手动触发":(e.trigger==="catchup"?"启动补跑":"定时触发")}</span>
    <span class="tm">${esc(schWhen(e.started_at))}${e.ended_at?` → ${esc(fmtDate(e.ended_at).slice(-5))}`:""}</span>
    <div class="acts">
      ${retry?`<button class="btn ghost" data-run-retry="${e.schedule_id}">重试</button>`:""}
      ${e.workflow_run_id?`<button class="btn ghost" data-run-workflow="${esc(e.workflow_run_id)}" data-run-project="${esc(schRows.find(s=>s.id===e.schedule_id)?.project_id||'')}">查看工作流 / 审查</button>`:''}
      ${e.note_id && e.note_path?`<button class="btn ghost" data-run-note="${esc(e.note_id)}" data-note-project="${esc(e.note_project_id)}" data-note-path="${esc(e.note_path)}">打开笔记</button>`:""}
      <button class="btn ghost" data-run-del="${e.id}">删除</button></div>
    ${e.reason?`<div class="det">阻断/失败原因：${esc(e.reason)}</div>`:""}
    ${e.summary?`<div class="det">摘要：${esc(e.summary)}</div>`:""}
    <div class="det">产物：${e.output_path?esc(e.output_path):"未生成"}</div></div>`;
}
async function schLoad(keepMsg){
  const d = await api("/api/schedules");
  schRows = d.schedules||[]; schRuns = d.runs||[]; schFreq = d.freq||{};
  schRunStatus = d.run_status||{}; schRetryable = d.retryable||[];
  if(!keepMsg) schFeedback = "";
}
async function renderSchedules(m){
  m.style.padding = "";
  m.innerHTML = `<div class="muted">正在读取定时任务…</div>`;
  try{ await schLoad(true); }
  catch(e){ m.innerHTML = `<div class="msg error">读取失败：${esc(e.message)}</div>`; return; }
  if(route() !== "schedules" || !m.isConnected) return;
  m.innerHTML = `
    <div class="card"><div class="sch-head">
      <div><p class="sch-eyebrow">AUTOMATION / SCHEDULES</p><h3>定时任务</h3>
        <p>管理由本应用 Agent 执行的定时研究任务。任务仅在应用运行时调度，写入范围遵循项目目录约定。</p></div>
      <button class="btn" id="schNew">＋ 新建定时任务</button></div></div>
    ${schFeedback?`<div class="card" style="margin-top:10px"><span class="hint">${esc(schFeedback)}</span></div>`:""}
    ${schEditorHTML()}
    <div class="card" style="margin-top:12px">
      <div class="sch-head"><div><p class="sch-eyebrow">RUNTIME SCHEDULE</p><h3>已配置任务</h3></div>
        <span class="pill">${schRows.length} 个任务</span></div>
      ${schRows.length? schRows.map(schCardHTML).join("") : `<div class="hint" style="margin-top:10px">还没有定时任务。创建每日摘要、文献检索或项目进度提醒，由本应用 Agent 在应用运行时执行。</div>`}
      <div class="sch-note">🕒 <span>调度依赖应用进程；关闭「览籍智研」期间不会后台执行。重新打开时错过的每日时间点最多补跑一次，其余等待下一个时间点（可随时暂停/启用）。</span></div>
    </div>
    <div class="card" style="margin-top:12px">
      <div class="sch-head"><div><p class="sch-eyebrow">RUN HISTORY</p><h3>最近运行</h3></div>
        <span class="pill">${schRuns.length} 条</span>
        ${schRuns.length?`<button class="btn ghost" id="schRunsClear">清空记录</button>`:""}</div>
      ${schRuns.length? schRuns.map(schRunHTML).join("") : `<div class="hint" style="margin-top:10px">尚无定时运行记录；每次到点触发、启动补跑或立即运行都会在这里记录状态、原因和产物路径。</div>`}
    </div>`;

  const nm = () => esc($("schName").value.trim() || "未命名任务");
  const bindEditor = ()=>{
    if(!schEdit) return;
    api('/api/workflow/layouts').then(data=>{
      const select=$('schWorkflow'); if(!select) return;
      select.innerHTML='<option value="">只运行所选专家</option>'+(data.layouts||[]).map(l=>`<option value="${esc(l.id)}" ${l.id===schEdit?.workflow_id?'selected':''}>${esc(l.name)}</option>`).join('');
      select.onchange=()=>{ $('schAssistant').disabled=!!select.value; };
      select.onchange();
    }).catch(error=>uiNotify(error.message));
    const selected = schEdit.todo_id;
    const loadTodos = async ()=>{
      const project = $("schProj").value, select = $("schTodo");
      select.disabled = true;
      try{
        const data = project ? await api("/api/todos?task_id="+encodeURIComponent(project)) : {todos:[]};
        if(!select.isConnected || $("schProj").value !== project) return;
        const rows = (data.todos||[]).filter(t=>t.id===selected || !t.execution || !["queued","running","review"].includes(t.execution.status));
        select.innerHTML = '<option value="">不关联待办</option>' + rows.map(t=>`<option value="${esc(t.id)}" ${t.id===selected?"selected":""}>${esc(t.title)}</option>`).join("");
        select.disabled = !!selected;
      }catch(e){ select.innerHTML='<option value="">读取失败，请重新选择项目</option>'; uiNotify(e.message); }
    };
    $("schProj").onchange = loadTodos;
    loadTodos();
    $("schCancel") && ($("schCancel").onclick = ()=>{ schEdit=null; render(); });
    $("schCancel2") && ($("schCancel2").onclick = ()=>{ schEdit=null; render(); });
    $("schFreq").onchange = (e)=>{ const k=e.target.value; const c=SCH_PRESETS[k]; if(c) $("schCron").value=c;
      schEdit.frequency=k; if(c) schEdit.cron=c; };
    $("schSave").onclick = async ()=>{
      const body = { id: schEdit.id, name: nm(), prompt: $("schPrompt").value.trim(),
         project_id: $("schProj").value, assistant_id: $("schAssistant").value, workflow_id:$("schWorkflow").value, todo_id:$("schTodo").value, frequency: $("schFreq").value, cron: $("schCron").value.trim(),
        output_dir: $("schOut").value.trim() || "定时任务", enabled: $("schOn").value === "1" };
      if(!body.prompt) return uiNotify("Prompt 必填");
      try{ await api("/api/schedule/save", body); schEdit=null; await schLoad();
           schFeedback = "定时任务已保存；仅在应用运行时执行。"; render(); }
      catch(e){ uiNotify("保存失败：" + e.message); }
    };
  };
  $("schNew").onclick = ()=>{ schEdit = schEditSeed(null); render(); setTimeout(()=>$("schName")?.focus(),30); };
  bindEditor();
  m.querySelectorAll("[data-sch-edit]").forEach(b=>b.onclick=()=>{
    const r = schRows.find(x=>x.id===b.dataset.schEdit); schEdit = schEditSeed(r); render(); });
  m.querySelectorAll("[data-sch-toggle]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/toggle", { id:b.dataset.schToggle, enabled: b.dataset.on !== "1" });
         schFeedback = "定时任务状态已更新。"; await schLoad(); render(); }
    catch(e){ uiNotify("状态更新失败：" + e.message); } });
  m.querySelectorAll("[data-sch-del]").forEach(b=>b.onclick=async ()=>{
    const r = schRows.find(x=>x.id===b.dataset.schDel);
    if(!await uiConfirm(`确认归档“${r?r.name:""}”？归档后不会再自动运行。`)) return;
    try{ await api("/api/schedule/delete", { id:b.dataset.schDel }); schFeedback = "定时任务已归档。";
         await schLoad(); render(); }
    catch(e){ uiNotify("归档失败：" + e.message); } });
  m.querySelectorAll("[data-sch-run]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run", { id:b.dataset.schRun }); schFeedback = "定时任务已立即触发；跑完会出现在「最近运行」。";
         render(); setTimeout(()=>{ if(route()==="schedules") render(); }, 6000); }
    catch(e){ uiNotify("触发失败：" + e.message); } });
  m.querySelectorAll("[data-run-retry]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run", { id:b.dataset.runRetry }); schFeedback = "已按该规则重新触发一次。";
         render(); setTimeout(()=>{ if(route()==="schedules") render(); }, 6000); }
    catch(e){ uiNotify("重试失败：" + e.message); } });
  m.querySelectorAll("[data-run-del]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run/delete", { ids:[b.dataset.runDel] }); await schLoad(); render(); }
    catch(e){ uiNotify("删除运行记录失败：" + e.message); } });
  m.querySelectorAll("[data-run-note]").forEach(b=>b.onclick=async()=>{
    await ConnectedResearch.openNote(b.dataset.noteProject,b.dataset.notePath);
  });
  m.querySelectorAll('[data-run-workflow]').forEach(b=>b.onclick=()=>ConnectedResearch.openRun(b.dataset.runProject,b.dataset.runWorkflow));
  $("schRunsClear") && ($("schRunsClear").onclick = async ()=>{
    if(!await uiConfirm(`删除全部 ${schRuns.length} 条运行记录？定时规则与产物文件都不会被改动。`)) return;
    try{ await api("/api/schedule/run/delete", { ids: schRuns.map(x=>x.id) }); await schLoad(); render(); }
    catch(e){ uiNotify("清空失败：" + e.message); } });
}
const schTaskName = (id)=> (S.tasks||[]).find(t=>t.id===id)?.title || "";

/* ---------------- 项目空间（页头 + 搜索 + 待办 + 文献/easyScholar + 星图 + 笔记选择与预览 + 概览四卡） ---------------- */
let spTab = "overview", spId = null;
const SP_TABS = [["overview","概览"],["plan","进度与计划"],["papers","文献"],["stars","论文星图"],["notes","笔记"]];
let spActiveNoteId = null;
let spSelectedNoteIds = new Set();
const esBadgeCache = new Map();

function spWhen(iso){ return iso ? fmtDate(iso) : "—"; }

function spBodyHTML(d){
  const t = d.task||{}, sum = d.summary||{};

  // ---------- 1. 进度与计划（嵌入原「科研进度与计划看板」，含十步管道与排期视图） ----------
  if(spTab === "plan"){
    return `<div class="card sp-plan" style="margin-top:12px;padding:14px 16px">
      <div id="spPlan" style="min-height:320px"><div class="hint">正在读取进度…</div></div>
    </div>`;
  }

  // ---------- 2. 文献与 easyScholar 期刊分区 ----------
  if(spTab === "papers"){
    const rows = d.library_items || d.paper_list || [];
    return `<div class="card" style="margin-top:12px"><div class="sch-head"><div><p class="inst-label">PAPERS / PROJECT</p><h3>${esc(t.title||"")} 的文献</h3></div>
      <span class="pill">${rows.length} 条</span><button class="btn btn-primary" id="spZoteroBtn" style="margin-left:auto">从 Zotero 导入</button><button class="btn ghost" id="spToLib">去知识库 →</button></div>
      ${rows.length ? rows.map((r, idx)=>`<div class="sp-row"><span class="sp-ic">📄</span><div class="sp-main">
        <div class="sp-nm">${esc(r.title||r.filename||"未命名")} <span class="pill">${r.has_pdf || r.source_path ? "有 PDF" : "题录"}</span>${r.zotero_key ? '<span class="pill">Zotero 已关联</span>' : ""}</div>
        <div class="sp-meta">${esc(r.authors||"作者未识别")}${r.doi?` · <span class="sp-doi">DOI ${esc(r.doi)}</span>`:" · DOI 未识别"}${r.year?` · ${esc(r.year)}`:""}${r.pages?` · ${String(r.pages).split(",").length} 页`:""}</div>
        <div class="es-badges" id="es-paper-${idx}" data-journal="${esc(r.journal || r.title || '')}" style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap"></div>
      </div></div>`).join("")
      : `<div class="hint" style="margin-top:10px">这个项目还没有入库文献。<button class="btn btn-sm btn-primary" id="spZoteroBtnEmpty" style="margin-left:8px">从本机 Zotero 一键导入</button> 或到「知识库」检索入库。</div>`}</div>`;
  }

  // ---------- 3. 论文星图 ----------
  if(spTab === "stars"){
    const g = d.paper_graph||{}, nodes = g.nodes||[], total = (d.paper_list||[]).length;
    const built = g.built_at ? spWhen(g.built_at) : "";
    const meta = built ? `${built} · ${nodes.length} 篇 · ${(g.edges||[]).length} 条关系 · 用 ${g.took_ms < 1000 ? (g.took_ms||0) + "ms" : ((g.took_ms||0)/1000).toFixed(1) + "s"}`
                       : (total ? `扫库内 ${total} 篇 PDF 全文算引用关系，不会自动跑` : "这个项目还没有入库文献");
    const btn = built ? `<button class="btn ghost" id="spStarGen">重新生成</button>` : `<button class="btn" id="spStarGen">生成星图</button>`;
    return `<div class="card" style="margin-top:12px">
      <div class="sp-stbar"><span class="sp-sttl">论文星图</span><span class="pill${built?" ok":""}">${built?"已生成":"未生成"}</span>
        <span class="sp-smeta">${esc(meta)}</span><span style="flex:1"></span>
        ${total || built ? btn : ""}</div>
      ${nodes.length ? `<div id="spStars" style="height:clamp(420px,70vh,760px)"></div>`
        : `<div class="sp-empty"><b>还没有生成星图</b>
            <span>${total ? `这一步要扫库内 ${total} 篇 PDF 全文算引用关系，不会自动跑` : "先到「文献」或工作流的检索步把这个项目的文献加进来"}</span>
            ${total ? `<span class="hint">点右上角「生成星图」，算完自动刷新</span>` : ""}</div>`}</div>`;
  }

  // ---------- 4. 笔记（项目笔记分类看板 + 完整 Markdown 双栏/专注编辑器） ----------
  if(spTab === "notes"){
    return `<div id="mdDashboard" class="md-dashboard pn sp-notes-dash" style="background:var(--panel);border:1px solid var(--line);border-radius:10px;min-height:520px">
      <div class="md-dash-head" style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
        <input id="mdDashSearch" class="pn-search" type="search" placeholder="🔍 搜索当前项目笔记…" style="flex:1;max-width:320px">
        <span style="flex:1"></span>
      </div>
      <div id="mdDashboardBody"></div>
    </div>
    <div class="md-view" id="mdView" style="display:none;min-height:520px">
      <aside class="md-note-side">
        <button class="btn ghost md-back-dash" id="mdBackDash" title="回项目笔记">← 回项目笔记</button>
        <div class="md-note-side-head">
          <button class="btn" id="btnMdNew">＋ 新建</button>
          <button class="btn ghost" id="btnMdImport">导入 .md</button>
          <button class="btn ghost" id="btnMdZotero" title="从本机 Zotero 取文献或笔记，建成一篇笔记">Zotero</button>
        </div>
        <input id="mdNoteSearch" class="ws-field-input" type="search" placeholder="搜索笔记" style="background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:7px 9px;font-size:12.5px;color:inherit;font-family:inherit;outline:none">
        <div id="mdNoteList" class="md-note-list"></div>
        <div id="mdDropZone" class="md-drop-zone">拖入 Markdown 文件</div>
        <input type="file" id="mdFileInput" accept=".md,.markdown" multiple hidden>
      </aside>
      <div class="md-note-main">
        <div id="mdEmpty" class="md-empty">新建或导入一篇 Markdown 笔记</div>
        <div id="mdEditorShell" class="md-editor-shell hidden">
          <div class="md-title-row">
            <input id="mdNoteTitle" class="md-note-title" maxlength="160" placeholder="未命名笔记">
            <select id="mdTask" class="md-task-select" title="绑定到项目" style="display:none"><option value="">当前项目</option></select>
            <span id="mdSaveState" class="md-save-state"></span>
            <button class="btn ghost" id="btnMdFocus" title="专注模式：整页缩成一长列记事本（Esc 退出）">⛶ 专注</button>
            <button class="btn ghost" id="btnMdNarrow" title="窗口缩成右缘窄栏（再点回铺满）">▯ 窄栏</button>
            <button class="btn ghost" id="btnMdFocusOut" title="退出专注模式（Esc）">退出专注</button>
            <button class="btn ghost" id="btnMdExport">导出 MD</button>
            <button class="btn ghost" id="btnMdPdf">导出 PDF</button>
            <button class="btn ghost" id="btnMdDelete" title="删除笔记">🗑</button>
          </div>
          <div class="md-toolbar" id="mdToolbar">
            <p class="md-focus-hint">专注模式 · 边读边记 · Esc 退出</p>
            <select class="md-tool-select" data-md-command="heading" title="标题级别"><option value="">正文</option><option value="1">标题 1</option><option value="2">标题 2</option><option value="3">标题 3</option><option value="4">标题 4</option></select>
            <button class="md-tool-btn" data-md-command="bold" title="加粗"><b>B</b></button>
            <button class="md-tool-btn" data-md-command="italic" title="斜体"><i>I</i></button>
            <button class="md-tool-btn" data-md-command="highlight" title="高亮">高亮</button>
            <select class="md-tool-select" data-md-command="align" title="对齐方式"><option value="">对齐</option><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select>
            <select class="md-tool-select" data-md-command="font" title="字体"><option value="">字体</option><option value="Microsoft YaHei">微软雅黑</option><option value="SimSun">宋体</option><option value="KaiTi">楷体</option><option value="Times New Roman">Times New Roman</option></select>
            <select class="md-tool-select" data-md-command="size" title="字号"><option value="">字号</option><option value="12px">12</option><option value="14px">14</option><option value="16px">16</option><option value="18px">18</option><option value="24px">24</option><option value="32px">32</option></select>
            <span class="md-tool-sep"></span>
            <button class="md-tool-btn" data-md-command="ul" title="无序列表">• 列表</button>
            <button class="md-tool-btn" data-md-command="ol" title="有序列表">1. 列表</button>
            <button class="md-tool-btn" data-md-command="quote" title="引用">引用</button>
            <button class="md-tool-btn" data-md-command="link" title="插入链接">链接</button>
            <button class="md-tool-btn" data-md-command="code" title="插入代码块">代码块</button>
            <button class="md-tool-btn" data-md-command="formula" title="插入公式块">公式</button>
            <button class="md-tool-btn" data-md-command="table" title="插入表格">表格</button>
            <span class="md-tool-spacer"></span>
            <div class="seg-tabs md-mode-tabs">
              <button class="seg-tab active" data-md-mode="split">分栏</button>
              <button class="seg-tab" data-md-mode="write">编辑</button>
              <button class="seg-tab" data-md-mode="preview">预览</button>
            </div>
          </div>
          <div id="mdWorkspace" class="md-workspace split">
            <textarea id="mdSource" class="md-source" spellcheck="true" aria-label="Markdown 编辑器"></textarea>
            <article id="mdPreview" class="md-render md-preview"></article>
          </div>
        </div>
      </div>
    </div>`;
  }

  // ---------- 5. 概览 ----------
  const stages = (d.workflow?.nodes||[]).map(s=>({key:s.key, label:s.label, st:s.status}));
  const recent = (d.steps||[]).slice(0,6);
  return `
    <div class="sp-grid2" style="margin-top:12px">
      <div class="card sp-kpi"><p class="inst-label">RESEARCH</p><strong>${sum.papers||0}</strong>
        <p class="sp-kpi-note">项目文献 · 证据 ${sum.evidence||0} 条</p></div>
      <div class="card sp-kpi sp-kpi-text"><p class="inst-label">QUESTION</p><strong>${esc(t.question||"未填研究问题")}</strong></div></div>
    <div class="card" style="margin-top:12px"><div class="sch-head"><div><p class="inst-label">WORKFLOW PIPELINE</p><h3>项目科研流程</h3></div>
        <button class="btn ghost" id="spToFlow">去科研台 →</button></div>
        <div class="sp-pipe">
          ${stages.map((s,i)=>`<button class="sp-pipe-cell ${s.st==="delivered"?"on":(s.st==="running"?"run":"")}" data-sp-flow="${esc(s.key)}"
              title="${esc(s.label)}：${esc(({delivered:"已确认交付",succeeded:"待确认交付",stale:"产物已变更，需重新确认",running:"执行中",failed:"执行失败",cancelled:"已停止",waiting:"等待上游交付",ready:"可开始"})[s.st]||"未开始")}">
            <b>${String(i+1).padStart(2,"0")}</b><span>${esc(s.label)}</span></button>`).join("")}
        </div>
        <div class="hint" style="margin-top:8px">最近步骤：${recent.length?esc(recent.map(r=>r.stage).join(" → ")):"—"}</div></div>
    <div class="sp-grid2" style="margin-top:12px">
      <div class="card"><p class="inst-label">RELATIONSHIPS</p><h3 style="margin:0 0 8px">项目边界</h3>
        <div class="sp-kv"><span>项目位置</span><b>${esc(t.base_dir||"未设置（产出落工作区默认目录）")}</b></div>
        <div class="sp-kv"><span>关键词 / 体系</span><b>${esc(t.material_system||"—")}</b></div>
        <div class="sp-kv"><span>最近更新</span><b>${esc(spWhen(t.updated_at))}</b></div>
        <div class="hint" style="margin-top:10px">这一页只读聚合：不会改动文献、笔记、日程；写操作都在各自页面里。删除项目会连带它的文献、证据、笔记、会话、步骤。</div>
        <div class="pf-actions" style="margin-top:10px"><button class="btn ghost" id="spDel" style="color:var(--err)">删除项目</button></div></div>
      <div class="card"><div class="sch-head"><div><p class="inst-label">STEP LOG</p><h3>步骤记录</h3></div>
      <span class="pill">${(d.steps||[]).length} 条</span></div>
      ${recent.length?recent.map(r=>`<div class="sp-row"><span class="sp-ic">•</span><div class="sp-main">
        <div class="sp-nm">${esc(r.stage)} · ${esc(r.status)}</div><div class="sp-meta">${esc(r.summary||"")} · ${esc(spWhen(r.created_at))}</div>
        </div></div>`).join(""):`<div class="hint" style="margin-top:10px">还没有步骤记录。</div>`}</div>
    </div>`;
}
function spBind(d){
  const go = (id, route)=>{ const b=$(id); if(b) b.onclick = ()=>openTab(route); };
  go("spToFlow","workflow"); go("spToLib","library");
  document.querySelectorAll("[data-sp-flow]").forEach(b=>b.onclick=()=>openTab("workflow"));
  const zb = $("spZoteroBtn"), zbe = $("spZoteroBtnEmpty");
  if(zb) zb.onclick = zotOpen;
  if(zbe) zbe.onclick = zotOpen;
  const sg = $("spStarGen");                 // 论文星图是编译产物：点一下才算，算完落库
  if(sg) sg.onclick = async ()=>{
    const t0 = Date.now();
    sg.disabled = true; sg.innerHTML = `<span class="spin"></span>生成中 0s`;
    const tick = setInterval(()=>{ sg.innerHTML = `<span class="spin"></span>生成中 ${Math.round((Date.now()-t0)/1000)}s`; }, 500);
    try{ await api("/api/task/papergraph/build", {id: curTask}); }
    catch(e){ uiNotify("生成失败：" + e.message); }
    finally{ clearInterval(tick); render(); }
  };
  const del = $("spDel");
  if(del) del.onclick = async ()=>{
    const t = d.task||{};
    if(!await uiConfirm(`删除项目「${t.title||""}」？\n它的文献、证据、笔记、会话、日程、步骤都会一起删掉，不可撤销。`)) return;
    try{ await api("/api/task/delete", {id: curTask}); }catch(e){ return uiNotify("删除失败："+e.message); }
    curTask = null; localStorage.removeItem("rw-task"); await refreshState(false); openTab("tasks");
  };

  // ---------- 进度与计划：直接嵌看板（四列拖拽 / 十步管道 / 排期） ----------
  if(spTab === "plan"){
    const host = $("spPlan");
    if(host && window.renderKanban) window.renderKanban(host, {embedded:true});
  }

  // ---------- easyScholar 期刊等级标签（文献页） ----------
  if(spTab === "papers"){
    const badgeContainers = document.querySelectorAll(".es-badges[data-journal]");
    badgeContainers.forEach(async el => {
      const jname = el.dataset.journal;
      if(!jname) return;
      if(esBadgeCache.has(jname)){
        const cached = esBadgeCache.get(jname);
        if(cached.length) el.innerHTML = cached.map(b => `<span class="es-badge ${b.type}">${esc(b.label)}</span>`).join("");
        return;
      }
      try{
        const res = await api("/api/easyscholar/rank?journal=" + encodeURIComponent(jname));
        if(res && res.ok && res.badges){
          esBadgeCache.set(jname, res.badges);
          if(res.badges.length){
            el.innerHTML = res.badges.map(b => `<span class="es-badge ${b.type}">${esc(b.label)}</span>`).join("");
          }
        }
      }catch(_){}
    });
  }

  // ---------- 笔记：完整项目笔记仪表板 + 编辑器 ----------
  if(spTab === "notes"){
    mdBind(document.querySelector("#spBody"));
    mdDashProject = curTask;
    mdDashLoad();
    let pnT = 0;
    const sInp = $("mdDashSearch");
    if(sInp) sInp.oninput = e => {
      mdDashQuery = e.target.value.trim().toLowerCase();
      clearTimeout(pnT);
      pnT = setTimeout(()=>{ mdDashLoad(); }, 180);
    };
    const backBtn = $("mdBackDash");
    if(backBtn) backBtn.onclick = pnBackToDash;
  }
}

async function renderSpace(m){
  m.style.padding = "";
  m.classList.remove("sp-fill");
  if(!curTask){ m.innerHTML = needTask(); return; }
  const project = curTask;
  if(spId !== curTask){ spId = curTask; spTab = "overview"; spActiveNoteId = null; spSelectedNoteIds.clear(); }
  m.innerHTML = `<div class="muted">正在读取项目…</div>`;
  let d, lastErr;
  for(let attempt = 0; attempt < 3; attempt++){
    try{
      d = await api("/api/task?id=" + encodeURIComponent(project));
      lastErr = null;
      break;
    }catch(e){
      lastErr = e;
      if(/locked/i.test(e?.message||"") && attempt < 2){
        await new Promise(r => setTimeout(r, 300 * (attempt + 1)));
        continue;
      }
      break;
    }
  }
  if(curTask !== project || route() !== "space" || !m.isConnected) return;
  if(!d || lastErr){ m.innerHTML = `<div class="msg error">读取项目失败：${esc(lastErr ? lastErr.message : "未知错误")}</div>`; return; }
  const t = d.task||{};
  const opts = (S.tasks||[]).map(x=>`<option value="${x.id}" ${x.id===curTask?"selected":""}>${esc(x.title)}</option>`).join("");
  const isSpHeadCollapsed = localStorage.getItem("rw-sp-header-collapsed") === "true";
  m.innerHTML = `
    <div class="card sp-header-card ${isSpHeadCollapsed ? 'collapsed' : ''}" id="spHeaderCard">
      <div class="sp-hd1">
        <h3>${esc(t.title||"项目空间")} <span class="pill ok" title="绑定该项目的专用 agent">Partner Agent</span></h3>
        <span class="sp-step">阶段<b>${esc(t.stage_now||"未开始")}</b></span>
        <span class="sp-when">更新 ${esc(spWhen(t.updated_at))}</span>
        <span class="sp-gap"></span>
        <button class="btn" id="spFlow">进入科研台 →</button>
        <button class="btn ghost" id="spLeader">问智研管家 →</button>
        <button class="btn ghost btn-sm" id="spRebuild" title="把旧笔记、文献和产物登记到统一资产索引">资产</button>
        <button type="button" class="btn ghost btn-sm sp-collapse-btn" id="spToggleHeader" title="收起项目概况卡片，留出工作空间">▲ 收起概况</button>
        <span class="hint" id="spAssetStatus" aria-live="polite"></span>
      </div>
      <div class="sp-hd2">
        <p class="hint">项目级聚合只读取已关联的本地数据；写操作回到各领域的 Command 和安全通道。归档可恢复，外部文件不会被删除。</p>
        <select id="spPick" class="control" style="max-width:200px" title="选择项目空间">${opts}</select>
      </div>
      <div class="sp-search-bar" style="margin-top:12px;position:relative;">
        <div style="position:relative;display:flex;gap:8px;align-items:center">
          <input id="spSearchInput" class="control" placeholder="全局搜索当前项目的文献、笔记、证据、待办任务…" style="flex:1;padding-left:32px">
          <span style="position:absolute;left:10px;top:50%;transform:translateY(-50%);opacity:1;pointer-events:none">🔍</span>
          <button class="btn ghost" id="spSearchClear" style="display:none;padding:5px 10px;font-size:12px">清除</button>
        </div>
        <div id="spSearchResults" class="card" style="display:none;position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:90;max-height:460px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,0.18);border:1px solid var(--accent);background:var(--panel)"></div>
      </div>
    </div>
    <div class="sp-tabs" id="spTabs">
      ${SP_TABS.map(([k,l])=>`<button class="sp-tab ${k===spTab?"on":""}" data-sp-tab="${k}">${l}</button>`).join("")}
      <button type="button" class="sp-tab-header-toggle" id="spTabToggleHeader" title="${isSpHeadCollapsed ? '展开项目概况信息' : '收起项目概况信息'}">
        ${isSpHeadCollapsed ? `▼ 展开项目信息 · ${esc(t.title||"项目概况")}` : '▲ 收起概况'}
      </button>
    </div>
    <div id="spBody"></div>`;

  $("spPick").onchange = (e)=>{ curTask = e.target.value; localStorage.setItem("rw-task", curTask); spId = null; render(); };
  $("spFlow").onclick = ()=>openTab("workflow");
  const sl = $("spLeader"); if(sl) sl.onclick = ()=>openTab("agent");
  m.querySelectorAll("[data-sp-tab]").forEach(b=>b.onclick=()=>{ spTab = b.dataset.spTab; render(); });
  const toggleSpHeader = () => {
    const cur = localStorage.getItem("rw-sp-header-collapsed") === "true";
    localStorage.setItem("rw-sp-header-collapsed", (!cur).toString());
    render();
  };
  const btnToggleSpHead = $("spToggleHeader");
  if(btnToggleSpHead) btnToggleSpHead.onclick = toggleSpHeader;
  const btnTabToggle = $("spTabToggleHeader");
  if(btnTabToggle) btnTabToggle.onclick = toggleSpHeader;
  $("spBody").innerHTML = spBodyHTML(d);
  if(spTab === "notes") m.classList.add("sp-fill");
  spBind(d);

  const assetStatus = $("spAssetStatus");
  const rebuild = $("spRebuild");
  const refreshAssetStatus = async () => {
    try {
      const stats = await api(`/api/assets/diagnostics?project_id=${encodeURIComponent(curTask)}`);
      if(assetStatus) assetStatus.textContent = `资产 ${stats.assets || 0} · 冲突 ${stats.conflicts || 0}`;
    } catch(_) {}
  };
  refreshAssetStatus();
  // 「阶段」实时跟十步走：这张卡不整页重画，只把 pill 换掉；页面切走就自己停
  clearInterval(renderSpace._stageTick);
  renderSpace._stageTick = setInterval(async () => {
    const pill = document.querySelector(".sp-step b");
    if(!pill){ clearInterval(renderSpace._stageTick); return; }
    try{
      const dd = await api(`/api/task?id=${encodeURIComponent(curTask)}`);
      const now = (dd.task || {}).stage_now;
      if(now && now !== pill.textContent) pill.textContent = now;
    }catch(_){}
  }, 8000);
  if(rebuild) rebuild.onclick = async () => {
    rebuild.disabled = true;
    try {
      await api("/api/assets/rebuild", {method:"POST", project_id:curTask, include_files:true});
      toast("资产索引已整理", "success");
      await refreshAssetStatus();
    } catch(err) { toast(`资产整理失败：${err.message}`, "error"); }
    finally { rebuild.disabled = false; }
  };

  const sInp = $("spSearchInput"), sClear = $("spSearchClear"), sRes = $("spSearchResults");
  let sTimer = null;
  if(sInp){
    sInp.oninput = () => {
      const q = sInp.value.trim();
      if(sClear) sClear.style.display = q ? "block" : "none";
      if(!q){ if(sRes){ sRes.style.display = "none"; sRes.innerHTML = ""; } return; }
      clearTimeout(sTimer);
      sTimer = setTimeout(async () => {
        try {
          const res = await api(`/api/space/search?task_id=${encodeURIComponent(curTask)}&q=${encodeURIComponent(q)}`);
          if(!sRes) return;
          sRes.style.display = "block";
          if(!res.total){
            sRes.innerHTML = `<div class="hint" style="padding:12px 6px">未检索到与「${esc(q)}」相关的文献、笔记或任务。</div>`;
            return;
          }
          let h = `<div style="display:flex;justify-content:space-between;align-items:center;padding:0 0 8px;border-bottom:1px solid var(--line);margin-bottom:8px">
            <span class="inst-label" style="margin:0">SEARCH RESULTS · ${res.total} 条命中</span>
            <span class="hint">按 Esc 或点击外部关闭</span></div>`;
          if(res.results.todos && res.results.todos.length){
            h += `<div style="margin-bottom:10px"><div style="font-weight:600;font-size:12px;color:var(--muted);margin-bottom:4px">待办任务 (${res.results.todos.length})</div>`;
            h += res.results.todos.map(t => `<div class="sp-row" style="cursor:pointer" data-go-tab="todos"><span class="sp-ic">${t.status==='DONE'?'✅':'⏳'}</span><div class="sp-main"><div class="sp-nm">${esc(t.title)}</div><div class="sp-meta">${esc(t.status)} · ${esc(t.priority)} · ${esc(t.due_date||'无截止期')}</div></div></div>`).join("");
            h += `</div>`;
          }
          if(res.results.papers && res.results.papers.length){
            h += `<div style="margin-bottom:10px"><div style="font-weight:600;font-size:12px;color:var(--muted);margin-bottom:4px">文献 (${res.results.papers.length})</div>`;
            h += res.results.papers.map(p => `<div class="sp-row" style="cursor:pointer" data-go-tab="papers"><span class="sp-ic">📄</span><div class="sp-main"><div class="sp-nm">${esc(p.title||p.filename||'未命名')}</div><div class="sp-meta">${esc(p.authors||'')} · DOI: ${esc(p.doi||'—')} · ${esc(p.year||'')}</div></div></div>`).join("");
            h += `</div>`;
          }
          if(res.results.notes && res.results.notes.length){
            h += `<div style="margin-bottom:10px"><div style="font-weight:600;font-size:12px;color:var(--muted);margin-bottom:4px">笔记 (${res.results.notes.length})</div>`;
            h += res.results.notes.map(n => `<div class="sp-row" style="cursor:pointer" data-go-note="${esc(n.id)}"><span class="sp-ic">📝</span><div class="sp-main"><div class="sp-nm">${esc(n.title||'未命名笔记')}</div><div class="sp-meta">${esc(n.updated_at||'')} · ${n.word_count||0} 字 · ${esc(n.snippet||'')}</div></div></div>`).join("");
            h += `</div>`;
          }
          if(res.results.evidence && res.results.evidence.length){
            h += `<div><div style="font-weight:600;font-size:12px;color:var(--muted);margin-bottom:4px">证据 (${res.results.evidence.length})</div>`;
            h += res.results.evidence.map(e => `<div class="sp-row"><span class="sp-ic">🔬</span><div class="sp-main"><div class="sp-nm">${esc(e.claim||'证据记录')}</div><div class="sp-meta">${esc(e.quote||'')}</div></div></div>`).join("");
            h += `</div>`;
          }
          sRes.innerHTML = h;
          sRes.querySelectorAll("[data-go-tab]").forEach(el => {
            el.onclick = () => { spTab = el.dataset.goTab; sRes.style.display = "none"; render(); };
          });
          sRes.querySelectorAll("[data-go-note]").forEach(el => {
            el.onclick = () => { spTab = "notes"; spActiveNoteId = el.dataset.goNote; sRes.style.display = "none"; render(); };
          });
        } catch(err) {
          if(sRes){ sRes.style.display = "block"; sRes.innerHTML = `<div class="msg error">检索异常：${esc(err.message)}</div>`; }
        }
      }, 250);
    };
  }
  if(sClear){
    sClear.onclick = () => {
      sInp.value = ""; sClear.style.display = "none";
      if(sRes){ sRes.style.display = "none"; sRes.innerHTML = ""; }
    };
  }
  document.addEventListener("click", (evt) => {
    if(sRes && !sRes.contains(evt.target) && evt.target !== sInp){
      sRes.style.display = "none";
    }
  });

  if(spTab === "stars"){
    const host = $("spStars");
    const g = d.paper_graph || { nodes: [], edges: [], stats: {} };
    if(host && window.StarMap){
      host.__api = api; host.__toast = toast;
      StarMap.mount(host, g);
    }
    else if(host) host.innerHTML = `<div class="hint">星图脚本没加载上（/vendor/starmap.js）。</div>`;
  }
}

let mdDashProject = "", mdDashSelectedFolder = "笔记/文献精读笔记", mdDashPath = "", mdDashQuery = "", mdDashMap = null;
let mdProj = null, mdDraft = false;      // 项目笔记编辑状态：{pid, path, folder, title, content}
let mdDashLoadId = 0;
let mdDashTree = null;
let mdDashReadOnly = false;
let pnSideCollapsed = localStorage.getItem("rw-pn-side-collapsed") === "true";
const pnExpandedFolders = new Set(["笔记", "笔记/文献精读笔记"]);

function pnCountFiles(node){
  if(!node) return 0;
  if(node.type === "file") return 1;
  let c = 0;
  for(const ch of (node.children || [])){
    c += pnCountFiles(ch);
  }
  return c;
}

function pnFindNode(node, p){
  if(!node) return null;
  if(node.path === p) return node;
  for(const ch of (node.children || [])){
    if(ch.path === p) return ch;
    if(ch.type === "dir"){
      const f = pnFindNode(ch, p);
      if(f) return f;
    }
  }
  return null;
}

function pnCollectFiles(node, q = ""){
  if(!node) return [];
  let files = [];
  if(node.type === "file"){
    if(!q || (node.title||"").toLowerCase().includes(q) || (node.name||"").toLowerCase().includes(q)){
      files.push(node);
    }
  }else if(node.type === "dir"){
    for(const ch of (node.children || [])){
      files = files.concat(pnCollectFiles(ch, q));
    }
  }
  return files;
}

function pnNodeMatches(node, q){
  if(!q) return true;
  if(node.type === "file"){
    return (node.title || "").toLowerCase().includes(q) || (node.name || "").toLowerCase().includes(q);
  }
  return (node.name || "").toLowerCase().includes(q) || (node.children || []).some(ch => pnNodeMatches(ch, q));
}

function pnMapHit(file){
  const g = mdDashMap; if(!g || !(g.nodes||[]).length) return null;
  const norm = s => String(s||"").replace(/[\s·:：,，.。()（）\[\]【】\-—_]/g,"").toLowerCase();
  const stem = String(file.path||"").split("/").pop().replace(/\.md$/i,"");
  const title = norm(file.title), id = norm(stem);
  for(const n of g.nodes){
    if(n.type === "section") continue;
    const ref = norm(String(n.ref||"").split("/").pop().replace(/\.md$/i,""));
    const lbl = norm(n.label);
    if(!((ref && (ref === id || ref === title)) || (lbl.length >= 4 && title && (title.includes(lbl) || lbl.includes(title))))) continue;
    const sec = (g.edges||[]).find(e => e.to === n.id && (g.nodes.find(x => x.id === e.from) || {}).type === "section");
    const host = sec && g.nodes.find(x => x.id === sec.from);
    return { label: n.label, section: host ? host.label : "" };
  }
  return null;
}

function pnJumpMap(){
  if(mdDashProject && curTask !== mdDashProject){ curTask = mdDashProject; localStorage.setItem("rw-task", curTask); }
  wfStep = "map"; localStorage.setItem("rw-wf-step", wfStep);
  openTab("workflow");
}

function pnRenderTreeNode(node, depth = 0, q = ""){
  if(!node) return "";
  if(q && !pnNodeMatches(node, q)) return "";
  const isDir = node.type === "dir";
  const isRoot = node.path === "笔记";
  const expanded = q ? true : pnExpandedFolders.has(node.path);
  const isCurFolder = mdDashSelectedFolder === node.path;
  const isCurFile = mdDashPath === node.path;
  const count = isDir ? pnCountFiles(node) : 0;
  const hit = (!isDir && node.path) ? pnMapHit(node) : null;
  const indent = depth * 14;

  let html = "";
  if(!isRoot){
    const rowClass = ["pn-tree-row", (isDir ? (isCurFolder ? "on" : "") : (isCurFile ? "on" : ""))].filter(Boolean).join(" ");
    html += `<div class="${rowClass}" style="padding-left:${indent}px" data-tree-path="${esc(node.path)}" data-tree-type="${node.type}">
      ${isDir ? `<button class="pn-tree-toggle" data-tree-toggle="${esc(node.path)}" title="${expanded?'收起':'展开'}">${expanded?'▼':'▶'}</button>` : `<span style="width:16px;display:inline-block"></span>`}
      <span class="pn-tree-icon">${isDir ? (expanded ? "📂" : "📁") : "📝"}</span>
      <span class="pn-tree-text" title="${esc(isDir ? node.name : (node.title || node.name))}">${esc(isDir ? node.name : (node.title || node.name))}</span>
      ${isDir ? `<span class="pn-tree-badge">${count}</span>` : ""}
      ${hit ? `<span class="pn-badge on" style="font-size:10px;padding:0 4px" title="已挂证据链谱系图">已挂</span>` : ""}
      <div class="pn-tree-acts">
        ${isDir ? `
          <button class="pn-tree-act" data-act="new-file" data-path="${esc(node.path)}" title="在此文件夹新建笔记">＋</button>
          <button class="pn-tree-act" data-act="new-folder" data-path="${esc(node.path)}" title="在此新建子文件夹">📁+</button>
          <button class="pn-tree-act del" data-act="del-folder" data-path="${esc(node.path)}" title="删除文件夹">🗑</button>
        ` : `
          <button class="pn-tree-act del" data-act="del-file" data-path="${esc(node.path)}" title="删除笔记">🗑</button>
        `}
      </div>
    </div>`;
  }
  if(isDir && (expanded || isRoot) && node.children && node.children.length){
    html += `<div class="pn-tree-children">`;
    for(const child of node.children){
      html += pnRenderTreeNode(child, isRoot ? 0 : depth + 1, q);
    }
    html += `</div>`;
  }
  return html;
}

function pnSideHTML(tree, q){
  if(pnSideCollapsed){
    return `<aside class="pn-side collapsed">
      <button class="pn-side-expand" id="pnBtnExpandSide" title="展开笔记目录">
        <span>▶</span>
        <span class="pn-side-expand-txt">笔记目录</span>
      </button>
    </aside>`;
  }
  return `<aside class="pn-side">
    <div class="pn-side-header">
      <div class="pn-side-head-row">
        <b class="pn-side-title">📁 笔记目录</b>
        <button class="pn-side-collapse" id="pnBtnCollapseSide" title="折叠侧栏">◀</button>
      </div>
      <div class="pn-side-btns">
        <button class="btn sm" id="pnBtnSideNewFile" title="在当前文件夹新建笔记">＋ 笔记</button>
        <button class="btn sm ghost" id="pnBtnSideNewFolder" title="在当前文件夹新建子文件夹">📁+ 文件夹</button>
        <button class="btn sm ghost" id="pnBtnSideImport" title="导入 Markdown 文件">导入</button>
        <input type="file" id="pnSideFileInput" accept=".md,.markdown" multiple hidden>
      </div>
    </div>
    <div class="pn-tree" id="pnTreeContainer">
      ${pnRenderTreeNode(tree, 0, q) || `<div class="hint" style="padding:16px 10px;text-align:center">${q ? "没有匹配的笔记" : "没有 Markdown 笔记"}</div>`}
    </div>
  </aside>`;
}

function pnBreadcrumbHTML(curPath, isFile = false){
  const parts = (curPath || "笔记").split("/").filter(Boolean);
  let accumulated = "";
  const crumbs = parts.map((part, idx) => {
    accumulated = accumulated ? `${accumulated}/${part}` : part;
    const isLast = idx === parts.length - 1;
    if(isLast){
      return `<span class="pn-crumb-item cur">${esc(part)}</span>`;
    }
    return `<span class="pn-crumb-item" data-crumb-path="${esc(accumulated)}">${esc(part)}</span>`;
  });
  return `<div class="pn-crumb"><span class="pn-crumb-icon">${isFile ? "📝" : "📁"}</span>${crumbs.join('<span class="pn-crumb-sep">/</span>')}</div>`;
}

function pnFolderViewHTML(folderNode, q){
  if(q){
    const allFiles = pnCollectFiles(folderNode, q);
    return `<div class="pn-rows" style="padding:12px 14px">
      <div style="font-size:12px;color:var(--muted);margin-bottom:10px">搜索结果 · 共 ${allFiles.length} 篇</div>
      ${allFiles.length ? allFiles.map(f => {
        const hit = pnMapHit(f);
        return `<div class="pn-row" data-pn-file="${esc(f.path)}">
          <div class="pn-rt" title="${esc(f.title || f.name)}">${esc(f.title || f.name)}</div>
          <div class="pn-rm">
            <span style="color:var(--muted)">${esc(f.path)}</span>
            <span>${new Date(f.updated_at*1000).toLocaleDateString()}</span>
            ${hit ? `<span class="pn-badge on" data-pn-map="1" title="已挂进证据链谱系图">已挂 ${esc(hit.section||hit.label)}</span>` : ""}
          </div>
        </div>`;
      }).join("") : `<div class="hint" style="padding:40px;text-align:center">没有找到与「${esc(q)}」匹配的笔记</div>`}
    </div>`;
  }

  const subDirs = (folderNode.children || []).filter(c => c.type === "dir");
  const files = (folderNode.children || []).filter(c => c.type === "file");

  if(!subDirs.length && !files.length){
    return `<div style="padding:48px 20px;text-align:center;color:var(--muted)">
      <div style="font-size:28px;margin-bottom:10px">📂</div>
      <div>此文件夹为空</div>
      <div style="margin-top:14px;display:flex;gap:8px;justify-content:center">
        <button class="btn sm" id="pnEmptyNewNote">＋ 新建笔记</button>
        <button class="btn sm ghost" id="pnEmptyNewFolder">📁+ 新建文件夹</button>
      </div>
    </div>`;
  }

  let html = `<div style="overflow-y:auto;padding:12px 14px;flex:1">`;
  if(subDirs.length){
    html += `<div style="font-size:11.5px;font-weight:600;color:var(--muted);margin-bottom:8px">子文件夹 (${subDirs.length})</div>
    <div class="pn-grid" style="padding:0;margin-bottom:18px">
      ${subDirs.map(d => `<div class="pn-card" data-card-folder="${esc(d.path)}">
        <div class="pn-card-h"><span style="font-size:18px">📁</span><span class="pn-card-title">${esc(d.name)}</span></div>
        <div class="pn-card-meta"><span>${pnCountFiles(d)} 篇笔记</span></div>
      </div>`).join("")}
    </div>`;
  }
  if(files.length){
    html += `<div style="font-size:11.5px;font-weight:600;color:var(--muted);margin-bottom:8px">笔记列表 (${files.length})</div>
    <div class="pn-rows" style="padding:0">
      ${files.map(f => {
        const hit = pnMapHit(f);
        return `<div class="pn-row" data-pn-file="${esc(f.path)}">
          <div class="pn-rt" title="${esc(f.title || f.name)}">${esc(f.title || f.name)}</div>
          <div class="pn-rm">
            <span>${new Date(f.updated_at*1000).toLocaleDateString()}</span>
            <span>${(f.size/1024).toFixed(1)} KB</span>
            ${hit ? `<span class="pn-badge on" data-pn-map="1" title="已挂进证据链谱系图">已挂 ${esc(hit.section||hit.label)}</span>` : ""}
          </div>
        </div>`;
      }).join("")}
    </div>`;
  }
  html += `</div>`;
  return html;
}

function pnBindSideEvents(host){
  const side = host.querySelector(".pn-side");
  if(!side) return;

  const btnCollapse = side.querySelector("#pnBtnCollapseSide");
  if(btnCollapse) btnCollapse.onclick = () => {
    pnSideCollapsed = true;
    localStorage.setItem("rw-pn-side-collapsed", "true");
    mdDashLoad();
  };
  const btnExpand = side.querySelector("#pnBtnExpandSide");
  if(btnExpand) btnExpand.onclick = () => {
    pnSideCollapsed = false;
    localStorage.setItem("rw-pn-side-collapsed", "false");
    mdDashLoad();
  };

  const btnNewFile = side.querySelector("#pnBtnSideNewFile");
  if(btnNewFile) btnNewFile.onclick = () => pnNewNote();

  const btnNewFolder = side.querySelector("#pnBtnSideNewFolder");
  if(btnNewFolder) btnNewFolder.onclick = async () => {
    const parentFolder = mdDashSelectedFolder || "笔记";
    const name = await uiPrompt(`在 ${parentFolder} 下新建子文件夹，请输入名称：`);
    if(!name || !name.trim()) return;
    const safe = name.trim().replace(/[\\/:*?"<>|]/g,"_");
    const targetPath = `${parentFolder}/${safe}`;
    try{
      await api("/api/project/folder/create", {project_id: mdDashProject, path: targetPath});
      pnExpandedFolders.add(parentFolder);
      pnExpandedFolders.add(targetPath);
      mdDashSelectedFolder = targetPath;
      mdDashLoad();
    }catch(err){ uiNotify("创建文件夹失败：" + err.message); }
  };

  const btnImport = side.querySelector("#pnBtnSideImport");
  const fileInp = side.querySelector("#pnSideFileInput");
  if(btnImport && fileInp){
    btnImport.onclick = () => fileInp.click();
    fileInp.onchange = (e) => {
      mdImport(e.target.files);
      e.target.value = "";
    };
  }

  // Toggle folder
  side.querySelectorAll("[data-tree-toggle]").forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const p = btn.dataset.treeToggle;
      if(pnExpandedFolders.has(p)) pnExpandedFolders.delete(p);
      else pnExpandedFolders.add(p);
      mdDashLoad();
    };
  });

  // Click tree row (folder or file)
  side.querySelectorAll("[data-tree-path]").forEach(row => {
    row.onclick = async (e) => {
      if(e.target.closest(".pn-tree-acts") || e.target.closest("[data-tree-toggle]")) return;
      const p = row.dataset.treePath;
      const type = row.dataset.treeType;
      if(type === "dir"){
        if(!await pnSaveCurrent()) return;
        mdProj = null; mdDraft = false; mdDashPath = "";
        mdDashSelectedFolder = p;
        pnExpandedFolders.add(p);
        mdDashLoad();
      }else if(type === "file"){
        if(!await pnSaveCurrent()) return;
        mdDashPath = p;
        mdProj = null; mdDraft = false;
        mdDashLoad();
      }
    };
  });

  // Tree action buttons
  side.querySelectorAll("[data-act]").forEach(btn => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const act = btn.dataset.act;
      const targetPath = btn.dataset.path;
      if(act === "new-file"){
        await pnNewNote(targetPath);
      }else if(act === "new-folder"){
        const name = await uiPrompt(`在 ${targetPath} 下新建子文件夹，请输入名称：`);
        if(!name || !name.trim()) return;
        const safe = name.trim().replace(/[\\/:*?"<>|]/g,"_");
        const newFolder = `${targetPath}/${safe}`;
        try{
          await api("/api/project/folder/create", {project_id: mdDashProject, path: newFolder});
          pnExpandedFolders.add(targetPath);
          pnExpandedFolders.add(newFolder);
          mdDashSelectedFolder = newFolder;
          mdDashLoad();
        }catch(err){ uiNotify("创建文件夹失败：" + err.message); }
      }else if(act === "del-folder"){
        if(targetPath === "笔记") return uiNotify("不能删除笔记根目录");
        if(!await uiConfirm(`确定删除文件夹「${targetPath}」及其全部内容吗？`, {ok:"删除文件夹"})) return;
        try{
          await api("/api/project/note/delete", {project_id: mdDashProject, path: targetPath});
          pnExpandedFolders.delete(targetPath);
          if(mdDashSelectedFolder === targetPath || mdDashSelectedFolder.startsWith(targetPath + "/")){
            mdDashSelectedFolder = "笔记";
          }
          mdDashLoad();
        }catch(err){ uiNotify("删除文件夹失败：" + err.message); }
      }else if(act === "del-file"){
        if(!await uiConfirm(`确定删除笔记「${targetPath}」吗？`, {ok:"删除笔记"})) return;
        try{
          await api("/api/project/note/delete", {project_id: mdDashProject, path: targetPath});
          if(mdDashPath === targetPath){
            mdDashPath = ""; mdProj = null; mdDraft = false;
          }
          mdDashLoad();
        }catch(err){ uiNotify("删除笔记失败：" + err.message); }
      }
    };
  });
}

function mdMountEditor(host){ const sh = $("mdEditorShell"); if(sh && host && sh.parentElement !== host) host.appendChild(sh); }

async function pnSaveCurrent(){
  const note = mdProj;
  if(!note) return true;
  while(mdProj === note){
    const version = note.editVersion || 0;
    if(!await mdSaveProj() || mdProj !== note) return false;
    if((note.editVersion || 0) === version) return true;
  }
  return false;
}

async function pnNewNote(folder = mdDashSelectedFolder || "笔记"){
  if(mdDashReadOnly) return;
  const pid = mdDashProject;
  if(!await pnSaveCurrent() || mdDashProject !== pid) return;
  if(!folder || !folder.startsWith("笔记")) folder = "笔记";
  mdDashSelectedFolder = folder;
  pnExpandedFolders.add(folder);
  mdProj = {pid, path: "", folder, title: "", content: "", metadata:window.Notebook&&!Notebook.fileMode?Notebook.metadataForNew(folder):undefined};
  mdDraft = true; mdDashPath = "";
  await mdDashLoad();
}

async function pnBackToDash(){
  if(!await pnSaveCurrent()) return;
  mdMountEditor(document.querySelector("#mdView .md-note-main"));
  $("mdView").style.display = "none";
  $("mdDashboard").style.display = "";
  mdDashPath = ""; mdDraft = false; mdProj = null;
  mdDashLoad();
}

async function mdSaveProj(){
  clearTimeout(mdSaveTimer);
  const note = mdProj, titleInput = $("mdNoteTitle"), source = $("mdSource");
  if(!note) return true;
  if(note.read_only) return true;
  if(!titleInput || !source) return false;
  const rawTitle = titleInput.value.trim();
  if(!note.path && !rawTitle && !source.value.trim()) return true;
  const title = rawTitle || "未命名笔记";
  let content = source.value;
  if(!note.path){
    const folder = note.folder || mdDashSelectedFolder || "笔记/文献精读笔记";
    note.path = `${folder}/${crypto.randomUUID().replace(/-/g, "")}.md`;
    note.newFile = true;
    note.content_hash = "";
    if(!/^#\s/.test(content) && !/^---\s*\r?\n/.test(content) && note.metadata?.note_type!=="prompt_template") content = `# ${title}\n\n` + content;
  }else if(/^#\s/.test(content)){
    content = content.replace(/^#\s.*$/m, `# ${title}`);
  }
  if(content !== source.value) source.value = content;
  note.title = title; note.content = content;
  $("mdSaveState").textContent = "保存中…";
  const version = note.editVersion || 0;
  const metadata = note.metadata ? JSON.parse(JSON.stringify(note.metadata)) : undefined;
  const pending = (note.savePromise || Promise.resolve(true)).then(async ok => {
    if(!ok) return false;
    try{
      const data = await api("/api/project/note/save", {project_id: note.pid, path: note.path, title, content,
        metadata, create: !!note.newFile, ...(note.content_hash !== undefined ? {expected_hash: note.content_hash} : {})});
      note.content_hash = data.content_hash;
      note.saved_content = data.content;
      if(note.metadata && data.template_id) note.metadata.template_id = data.template_id;
      note.newFile = false;
      if(mdProj === note && mdDashProject === note.pid){
        mdDashPath = note.path;
        mdDraft = false;
        if((note.editVersion || 0) === version){
          $("mdSaveState").textContent = "已保存";
          $("mdSaveState").title = "";
        }
      }
      return true;
    }catch(e){
      if(mdProj === note && mdDashProject === note.pid && $("mdSaveState")){
        $("mdSaveState").textContent = "保存失败";
        $("mdSaveState").title = e.message;
      }
      return false;
    }
  });
  note.savePromise = pending;
  try{
    return await pending;
  }finally{
    if(note.savePromise === pending) delete note.savePromise;
  }
}

function mdPaintProj(){
  const sh = $("mdEditorShell"); if(!sh) return;
  sh.classList.remove("hidden");
  $("mdNoteTitle").value = mdProj.title || "";
  $("mdSource").value = mdProj.content || "";
  $("mdNoteTitle").readOnly = !!mdProj.read_only;
  $("mdSource").readOnly = !!mdProj.read_only;
  $("mdToolbar").querySelectorAll("[data-md-command]").forEach(el => { el.disabled = !!mdProj.read_only; });
  mdPaintPreview();
  $("mdSaveState").textContent = "";
  [$("mdTask"), $("btnMdFocus"), $("btnMdNarrow"), $("btnMdFocusOut")]
    .forEach(el => { if(el) el.style.display = "none"; });
  const del = $("btnMdDelete");
  if(del){
    del.style.display = mdProj.read_only ? "none" : "";
    del.onclick = async () => {
      if(!await uiConfirm(`确定删除笔记「${mdProj.title || mdProj.path}」吗？此操作无法撤销。`, {ok:"删除笔记"})) return;
      try{
        if(mdProj.path){
          await api("/api/project/note/delete", {project_id: mdProj.pid, path: mdProj.path});
        }
        mdProj = null; mdDraft = false; mdDashPath = "";
        mdDashLoad();
      }catch(e){
        uiNotify("删除笔记失败：" + e.message);
      }
    };
  }
}

function pnApplyReadOnly(host){
  if(!mdDashReadOnly) return;
  host.querySelectorAll('[data-act], #pnBtnSideNewFile, #pnBtnSideNewFolder, #pnBtnSideImport, #mdDashNew, #mdDashNewFolder, #mdDashDelFolder, #pnEmptyNewNote, #pnEmptyNewFolder, #mdDashSave')
    .forEach(el => { el.disabled = true; });
  const notice = document.createElement("div");
  notice.className = "hint pn-readonly-hint";
  notice.style.padding = "8px 12px";
  notice.textContent = "只读项目：可阅读和导出笔记；请在独立项目目录中编辑。";
  host.prepend(notice);
}

async function mdDashLoad(){
  if(window.Notebook && !Notebook.fileMode) return Notebook.render();
    document.querySelector('.nb-note-properties')?.remove();
  const host = $("mdDashboardBody"); if(!host) return;
  const loadId = ++mdDashLoadId, pid = mdDashProject;
  const current = () => loadId === mdDashLoadId && mdDashProject === pid && host.isConnected;
  if(!mdDashProject){ host.innerHTML = `<div class="hint">选择项目后浏览该项目中的 Markdown 笔记。</div>`; return; }
  try{
    const data = await api(`/api/project/notes?project_id=${encodeURIComponent(pid)}`);
    if(!current()) return;
    mdDashReadOnly = !!data.read_only;
    mdDashTree = data.tree || {type: "dir", name: "笔记", path: "笔记", children: []};

    if(!mdDashMap || mdDashMap.pid !== mdDashProject){
      const map = mdDashMap = { pid, nodes: [], edges: [] };
      api(`/api/map/state?id=${encodeURIComponent(pid)}`)
        .then(d => { if(mdDashMap === map && mdDashProject === pid){ map.nodes = d.nodes||[]; map.edges = d.edges||[]; if(!mdProj) mdDashLoad(); } })
        .catch(()=>{});
    }

    if(!mdDashSelectedFolder || !pnFindNode(mdDashTree, mdDashSelectedFolder)){
      const defaultFolder = pnFindNode(mdDashTree, "笔记/文献精读笔记") ? "笔记/文献精读笔记" : "笔记";
      mdDashSelectedFolder = defaultFolder;
    }
    pnExpandedFolders.add(mdDashSelectedFolder);

    const q = mdDashQuery.trim().toLowerCase();

    // 1. If currently editing a note or creating draft
    if(mdDashPath || mdDraft){
      if(!mdProj || mdProj.pid !== mdDashProject || (!mdDraft && mdProj.path !== mdDashPath)){
        const file = mdDashPath
          ? await api(`/api/project/note?project_id=${encodeURIComponent(mdDashProject)}&path=${encodeURIComponent(mdDashPath)}`)
          : {title: "", content: ""};
        if(!current()) return;
        mdProj = {pid, path: mdDashPath, folder: mdDashSelectedFolder, title: file.title || "", content: file.content || "", content_hash: file.content_hash, read_only: !!file.read_only};
        mdDraft = !mdDashPath;
      }
      const hit = mdProj.path ? pnMapHit({path: mdProj.path, title: mdProj.title}) : null;
      mdMountEditor(document.querySelector("#mdView .md-note-main"));
      host.innerHTML = `<div class="pn-layout${pnSideCollapsed ? " side-collapsed" : ""}">
        ${pnSideHTML(mdDashTree, q)}
        <section class="pn-pane">
          <div class="pn-pane-h">
            <button class="btn ghost" id="mdDashBackFiles">← 返回目录</button>
            ${pnBreadcrumbHTML(mdProj.path || `${mdDashSelectedFolder}/${mdProj.title || '新笔记.md'}`, true)}
            ${hit ? `<span class="pn-badge on">已挂 ${esc(hit.section||hit.label)}</span>` : ""}
            <span style="flex:1"></span>
            <button class="btn" id="mdDashSave">保存</button>
          </div>
          <div class="pn-editor-slot" id="pnEditorSlot"></div>
        </section>
      </div>`;

      pnBindSideEvents(host);
      $("mdDashBackFiles").onclick = async () => {
        if(!await pnSaveCurrent()) return;
        mdProj = null; mdDraft = false; mdDashPath = ""; mdDashLoad();
      };
      $("mdDashSave").onclick = () => mdSaveProj();
      mdMountEditor($("pnEditorSlot"));
      mdPaintProj();
      pnApplyReadOnly(host);
      if(!mdProj.path) setTimeout(()=> $("mdNoteTitle")?.focus(), 40);
      return;
    }

    // 2. Folder Browse Mode
    mdMountEditor(document.querySelector("#mdView .md-note-main"));
    const curFolderNode = pnFindNode(mdDashTree, mdDashSelectedFolder) || mdDashTree;
    const isRoot = curFolderNode.path === "笔记";
    const totalFiles = pnCountFiles(curFolderNode);
    const readGroup = curFolderNode.name === "文献精读笔记";

    host.innerHTML = `<div class="pn-layout${pnSideCollapsed ? " side-collapsed" : ""}">
      ${pnSideHTML(mdDashTree, q)}
      <section class="pn-pane">
        <div class="pn-pane-h">
          ${pnBreadcrumbHTML(curFolderNode.path, false)}
          <span class="pn-sub">· ${totalFiles} 篇笔记</span>
          ${readGroup ? `<span class="pn-auto">精读完成自动归档到这里</span>` : ""}
          <span style="flex:1"></span>
          ${readGroup ? `<button class="btn ghost" id="pnGoMap">看谱系图 →</button>` : ""}
          <button class="btn" id="mdDashNew">＋ 新建笔记</button>
          <button class="btn ghost" id="mdDashNewFolder">📁+ 新建文件夹</button>
          ${!isRoot ? `<button class="btn ghost" id="mdDashDelFolder" title="删除此文件夹" style="color:var(--err,#e5484d)">🗑 删除</button>` : ""}
        </div>
        ${pnFolderViewHTML(curFolderNode, q)}
        <div class="pn-pane-f">当前目录：${esc(curFolderNode.path)} · 包含 ${totalFiles} 篇 Markdown 笔记</div>
      </section>
    </div>`;

    pnBindSideEvents(host);

    host.querySelectorAll("[data-crumb-path]").forEach(el => {
      el.onclick = () => {
        mdDashSelectedFolder = el.dataset.crumbPath;
        pnExpandedFolders.add(mdDashSelectedFolder);
        mdDashLoad();
      };
    });

    host.querySelectorAll("[data-card-folder]").forEach(card => {
      card.onclick = () => {
        mdDashSelectedFolder = card.dataset.cardFolder;
        pnExpandedFolders.add(mdDashSelectedFolder);
        mdDashLoad();
      };
    });

    host.querySelectorAll("[data-pn-file]").forEach(b => {
      b.onclick = (e) => {
        if(e.target.closest("[data-pn-map]")) return pnJumpMap();
        mdDashPath = b.dataset.pnFile;
        const parts = mdDashPath.split("/");
        let curP = "";
        for(let i = 0; i < parts.length - 1; i++){
          curP = curP ? `${curP}/${parts[i]}` : parts[i];
          pnExpandedFolders.add(curP);
        }
        mdDashLoad();
      };
    });

    const gm = $("pnGoMap"); if(gm) gm.onclick = pnJumpMap;
    const nb = $("mdDashNew");
    if(nb) nb.onclick = () => pnNewNote();
    const nf = $("mdDashNewFolder");
    if(nf) nf.onclick = async () => {
      const name = await uiPrompt(`在 ${curFolderNode.path} 下新建子文件夹，请输入名称：`);
      if(!name || !name.trim()) return;
      const safe = name.trim().replace(/[\\/:*?"<>|]/g,"_");
      const newFolder = `${curFolderNode.path}/${safe}`;
      try{
        await api("/api/project/folder/create", {project_id: mdDashProject, path: newFolder});
        pnExpandedFolders.add(curFolderNode.path);
        pnExpandedFolders.add(newFolder);
        mdDashSelectedFolder = newFolder;
        mdDashLoad();
      }catch(err){ uiNotify("创建文件夹失败：" + err.message); }
    };
    const df = $("mdDashDelFolder");
    if(df) df.onclick = async () => {
      if(!await uiConfirm(`确定删除文件夹「${curFolderNode.path}」及其全部内容吗？`, {ok:"删除文件夹"})) return;
      try{
        await api("/api/project/note/delete", {project_id: mdDashProject, path: curFolderNode.path});
        pnExpandedFolders.delete(curFolderNode.path);
        mdDashSelectedFolder = "笔记";
        mdDashLoad();
      }catch(err){ uiNotify("删除文件夹失败：" + err.message); }
    };
    const empNew = $("pnEmptyNewNote");
    if(empNew) empNew.onclick = () => pnNewNote();
    const empFold = $("pnEmptyNewFolder");
    if(empFold) empFold.onclick = nf.onclick;
    pnApplyReadOnly(host);

  }catch(e){
    if(!current()) return;
    mdMountEditor(document.querySelector("#mdView .md-note-main"));
    host.innerHTML=`<div class="msg error">${esc(e.message)}</div>`;
  }
}
async function renderNotes(m){
  spTab = "notes";
  return renderSpace(m);
}
if(!window.__rwMdEsc){                     // Esc 退出专注（只注册一次，renderNotes 会被反复调用）
  window.__rwMdEsc = 1;
  document.addEventListener("keydown", (e)=>{
    if(e.key==="Escape" && mdFocusOn && (route()==="notes" || (route()==="space" && spTab==="notes"))){
      if(mdNarrowOn) mdNarrow();          // 窄栏里 Esc = 退出专注 + 回铺满（别把人留在几十像素的分栏里）
      mdFocus(false);
    }
  });
}

/* ---------------- 模型与 Agent（Provider 卡片） ----------------
   对齐科研工作台的「设置 → 模型与 Agent」：Provider 卡片 / Key 不回显 / 发现模型 / 候选采用 / 设默认。
   Key 只落本机 SQLite；接口只回「已保存 | 未配置」。 */
let openProv = new Set();          // 哪些厂商的「可选模型」是展开的，rerender 后不塌回去
function renderModels(m){
  const def = S.default || null;
  const wires = S.wires || [];
  const all = S.providers || [];
  m.innerHTML = `<div class="card"><h3>模型与 Agent</h3>
      <p class="hint" style="margin:0">凭据只存本机 <code>data/research.sqlite3</code>，页面只回显「已保存 / 未配置」，<b>不回显 Key 原文</b>。
      目录里 ${all.filter(p=>p.source==="builtin").length} 家 Provider 每家一张卡片，用到哪家就填哪家的 Key；点已采用的模型 chip 即设为默认（Agent 下一轮就用它）。</p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
        <input class="control" id="p-filter" placeholder="过滤：deepseek / 通义 / anthropic…" style="max-width:280px">
        <label class="hint"><input type="checkbox" id="p-only"> 只看已配置</label>
        <label class="hint"><input type="checkbox" id="p-usable"> 只看本机可调用</label>
        <span class="hint" id="p-count"></span>
        <span style="flex:1"></span>
        <button class="btn ghost" id="p-new">＋ 自定义 Provider</button>
      </div></div>
    <div id="p-custom" hidden class="card"><h3>自定义 Provider</h3>
      <div class="grid2"><input type="text" id="p-name" placeholder="名称"><input type="text" id="p-url" placeholder="接口地址，填到 /v1"></div>
      <div class="grid2" style="margin-top:8px"><select id="p-api">${wires.map(w=>`<option value="${w.id}">${esc(w.label)}</option>`).join("")}</select><input type="password" id="p-key" placeholder="API Key（可稍后再填）"></div>
      <button class="btn" style="margin-top:8px" id="p-add">添加</button><div id="p-out"></div></div>
    <div id="p-grid"></div>`;

  const grid = $("p-grid");
  function card(p){
    const isDef = def && def.provider_id===p.id;
    const adopted = p.models || [];
    const builtin = (p.builtin_models||[]).filter(x=>!adopted.includes(x));
    return `<div class="card prov ${p.enabled?"on":""}" data-pid="${p.id}" data-name="${esc((p.name+" "+p.id+" "+(p.base_url||"")).toLowerCase())}" data-en="${p.enabled?1:0}" data-usable="${p.usable?1:0}">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <b>${esc(p.name)}</b>
        <span class="pill" title="接入协议">${esc(p.wire_label)}</span>
        ${p.source==="custom"?`<span class="pill">自定义</span>`:""}
        ${p.n_models?`<span class="pill">${p.n_models} 个模型</span>`:""}
        ${isDef?`<span class="pill ok">默认 · ${esc(def.model)}</span>`:""}
        ${p.usable?"":`<span class="pill warn" title="本机只实现了 OpenAI Chat Completions 这条协议">协议未接</span>`}
        <span style="flex:1"></span>
        <button class="btn ghost" data-prov-del="${p.id}" title="${p.source==="custom"?"删除":"复位（清 Key 与已采用模型）"}">${p.source==="custom"?"删除":"复位"}</button>
      </div>
      <div class="muted" style="font-size:12px;margin-top:2px">${esc(p.base_url||"—")}</div>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="hint">凭据：</span>${p.key_present?`<span class="pill ok">已保存</span>`:`<span class="pill warn">未配置</span>`}
        <input type="password" class="control" data-key-input="${p.id}" placeholder="粘贴 API Key" style="max-width:200px">
        <button class="btn ghost" data-key-save="${p.id}">${p.key_present?"更换 API Key":"设置 API Key"}</button>
        ${p.auth.includes("oauth")?`<button class="btn ghost" data-origin="${p.id}|${esc(p.console||"")}">OAuth 登录</button>`:""}
        ${p.console?`<button class="btn ghost" data-origin="${p.id}|${esc(p.console)}" title="打开厂商控制台">控制台 ↗</button>`:""}
        ${p.key_present?`<button class="btn ghost" data-key-clear="${p.id}">清除凭据</button>`:""}
      </div>
      <div style="margin-top:8px"><span class="hint">已采用：</span>
        ${adopted.length?adopted.map(x=>`<span class="chip dflt" data-default="${p.id}|${esc(x)}" title="设为默认模型">${esc(x)}<i class="x" data-unadopt="${p.id}|${esc(x)}">×</i></span>`).join(""):`<span class="hint">还没有采用模型</span>`}
      </div>
      ${builtin.length||adopted.length?`<details class="mods" data-mods="${p.id}" ${openProv.has(p.id)?"open":""}>
        <summary>可选模型 <b>${p.n_models||p.builtin_models.length}</b> 个${builtin.length<p.n_models?`（这里先列 ${builtin.length}）`:""} · 点开挑，点一下即采用</summary>
        <div class="cand">${builtin.slice(0,40).map(x=>`<button class="btn ghost" data-adopt="${p.id}|${esc(x)}">${esc(x)}</button>`).join("")||`<span class="hint">目录自带的都采用了，用「发现模型」或手填</span>`}</div>
      </details>`:""}
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn ghost" data-discover="${p.id}">发现模型</button>
        <input class="control" data-manual="${p.id}" placeholder="或手填模型 id" style="max-width:180px">
        <button class="btn ghost" data-adopt-manual="${p.id}">采用</button>
        <button class="btn ghost" data-prov-test="${p.id}">测试连接</button>
      </div>
      <div class="cand" data-cand="${p.id}"></div>
      <div class="provout" data-prov-out="${p.id}"></div>
    </div>`;
  }
  function draw(){
    const q = ($("p-filter").value||"").trim().toLowerCase();
    const only = $("p-only").checked, usable = $("p-usable").checked;
    const list = all.filter(p=>!q || (p.name+" "+p.id+" "+(p.base_url||"")).toLowerCase().includes(q))
      .filter(p=>!only || p.enabled).filter(p=>!usable || p.usable);
    grid.innerHTML = list.length?list.map(card).join(""):`<div class="card hint">没有匹配的 Provider</div>`;
    $("p-count").textContent = `显示 ${list.length} / ${all.length} 家`;
    bind();
  }
  function out(pid){ return grid.querySelector(`[data-prov-out="${pid}"]`); }
  function bail(pid,e){ const o=out(pid); if(o) o.innerHTML=`<div class="msg error">${esc(e.message)}</div>`; }
  function bind(){
    grid.querySelectorAll("details[data-mods]").forEach(d=>d.addEventListener("toggle",
      ()=>{ d.open ? openProv.add(d.dataset.mods) : openProv.delete(d.dataset.mods); }));
    grid.querySelectorAll("[data-key-save]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.keySave, inp=grid.querySelector(`[data-key-input="${id}"]`);
      try{ await api("/api/providers/key",{id,api_key:inp.value}); await refreshState(); }catch(e){ bail(id,e); } });
    grid.querySelectorAll("[data-key-clear]").forEach(b=>b.onclick=async()=>{
      if(!await uiConfirm("清除该 Provider 的凭据？")) return;
      try{ await api("/api/providers/key",{id:b.dataset.keyClear,api_key:""}); await refreshState(); }catch(e){ bail(b.dataset.keyClear,e); } });
    grid.querySelectorAll("[data-prov-del]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.provDel, p=all.find(x=>x.id===id)||{};
      if(!await uiConfirm(p.source==="custom"?"删除该 Provider？":"复位该 Provider？（清 Key 与已采用模型，卡片保留）")) return;
      try{ await api("/api/providers/delete",{id}); await refreshState(); }catch(e){ bail(id,e); } });
    grid.querySelectorAll("[data-origin]").forEach(b=>b.onclick=()=>{
      const [pid,url]=b.dataset.origin.split("|");
      if(url) window.open(url,"_blank");
      out(pid).innerHTML=`<div class="hint">${url?"已打开厂商控制台。":"该厂商没有公开控制台地址。"}订阅账号（OAuth）目前不能自动换 token——设备码流要各家自己的 client_id，这里不假装能自动授权：在控制台建 Key 或复制订阅 token，粘回上面的输入框保存即可。</div>`; });
    grid.querySelectorAll("[data-unadopt]").forEach(b=>b.onclick=async(e)=>{
      e.stopPropagation(); const [id,model]=b.dataset.unadopt.split("|");
      try{ await api("/api/providers/model",{id,model,on:false}); await refreshState(); }catch(err){ bail(id,err); } });
    grid.querySelectorAll("[data-adopt]").forEach(b=>b.onclick=async()=>{
      const [id,model]=b.dataset.adopt.split("|");
      try{ await api("/api/providers/model",{id,model,on:true}); await refreshState(); }catch(e){ bail(id,e); } });
    grid.querySelectorAll("[data-adopt-manual]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.adoptManual;
      try{ await api("/api/providers/model",{id,model:grid.querySelector(`[data-manual="${id}"]`).value,on:true}); await refreshState(); }catch(e){ bail(id,e); } });
    grid.querySelectorAll("[data-default]").forEach(c=>c.onclick=async(e)=>{
      if(e.target.classList.contains("x")) return;
      const [id,model]=c.dataset.default.split("|");
      try{ await api("/api/providers/default",{id,model}); await refreshState(); }catch(err){ bail(id,err); } });
    grid.querySelectorAll("[data-discover]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.discover, box=grid.querySelector(`[data-cand="${id}"]`);
      box.innerHTML=`<div class="hint">正在拉取 /models…</div>`;
      try{
        const {candidates}=await api("/api/providers/discover",{id});
        const have=new Set((all.find(x=>x.id===id)||{}).models||[]);
        box.innerHTML=`<div class="hint">候选 ${candidates.length} 个：</div><div class="cand">${candidates.slice(0,80).map(c=>
          `<button class="btn ghost" data-adopt="${id}|${esc(c.id)}" ${have.has(c.id)?"disabled":""}>${have.has(c.id)?"✓ ":""}${esc(c.id)}</button>`).join("")}</div>`;
        bind();
      }catch(e){ box.innerHTML=`<div class="msg error">${esc(e.message)}</div>`; } });
    grid.querySelectorAll("[data-prov-test]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.provTest; out(id).innerHTML=`<div class="hint">正在发一条最小消息…</div>`;
      try{ const r=await api("/api/providers/test",{id});
        out(id).innerHTML=`<div class="msg ok">${esc(r.model)} 可用：${esc((r.reply||"").slice(0,120))}</div>`; }
      catch(e){ bail(id,e); } });
  }

  $("p-new").onclick = ()=>{ const box=$("p-custom"); box.hidden=!box.hidden; };
  $("p-add").onclick = async ()=>{
    try{ await api("/api/providers/add",{name:$("p-name").value,base_url:$("p-url").value,api:$("p-api").value,api_key:$("p-key").value});
      await refreshState(); }catch(e){ $("p-out").innerHTML=`<div class="msg error">${esc(e.message)}</div>`; } };
  ["input","change"].forEach(ev=>["p-filter","p-only","p-usable"].forEach(id=>$(id).addEventListener(ev,draw)));
  draw();
}


/* ---------------- 设置 ---------------- */
/* ---- 主题：一个「主色」推导整套配色（8 个预设 + 自定义色号） ---- */
const DEFAULT_THEME = "#176B87";
const THEME_PRESETS = [
  {name:"研究蓝",primary:"#176B87"},{name:"松柏绿",primary:"#28735D"},
  {name:"群青蓝",primary:"#315F9B"},{name:"珊瑚红",primary:"#C85C4A"},
  {name:"琥珀金",primary:"#A87518"},{name:"莓果红",primary:"#A94762"},
  {name:"理性紫",primary:"#70578F"},{name:"石墨灰",primary:"#52625B"},
  {name:"莫兰迪灰",primary:"#BFBEBD"},{name:"莫兰迪米驼",primary:"#D8D3C9"},
  {name:"灰调雾霾蓝",primary:"#99B9C6"},{name:"灰豆绿",primary:"#A9B4A1"},
  {name:"裸粉豆沙",primary:"#DAC5C3"},{name:"灰调香芋紫",primary:"#B7A9B3"},
];
function hexToHsl(hex){
  const m=String(hex||"").trim().match(/^#?([0-9a-f]{6})$/i); if(!m) return null;
  const n=parseInt(m[1],16), r=((n>>16)&255)/255, g=((n>>8)&255)/255, b=(n&255)/255;
  const max=Math.max(r,g,b), min=Math.min(r,g,b), d=max-min, l=(max+min)/2;
  let h=0; const s=d===0?0:d/(1-Math.abs(2*l-1));
  if(d!==0){ if(max===r) h=60*(((g-b)/d)%6); else if(max===g) h=60*((b-r)/d+2); else h=60*((r-g)/d+4); }
  if(h<0) h+=360;
  return {h,s:s*100,l:l*100};
}
function hslToHex(h,s,l){
  s/=100; l/=100;
  const k=n=>(n+h/30)%12, a=s*Math.min(l,1-l),
    f=n=>l-a*Math.max(-1,Math.min(k(n)-3,Math.min(9-k(n),1))),
    to=x=>Math.round(255*x).toString(16).padStart(2,"0");
  return "#"+to(f(0))+to(f(8))+to(f(4));
}
function luminance(hex){
  const m=String(hex||"").match(/^#?([0-9a-f]{6})$/i); if(!m) return 0;
  const n=parseInt(m[1],16);
  const c=[(n>>16)&255,(n>>8)&255,n&255].map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4);});
  return .2126*c[0]+.7152*c[1]+.0722*c[2];
}
function currentPrimary(){ return localStorage.getItem("rw-primary") || DEFAULT_THEME; }
function contrastOn(hex, bg){                       // WCAG 对比度
  const a=luminance(hex), b=luminance(bg), hi=Math.max(a,b), lo=Math.min(a,b);
  return (hi+0.05)/(lo+0.05);
}
function applyTheme(primary){
  const c = hexToHsl(primary);
  if(!c){ uiNotify("色值格式不正确，请输入如 #176B87 的色号"); return; }
  const dark = document.documentElement.dataset.theme==="dark", root=document.documentElement;
  // 深色底要提亮；浅色底用本色，但莫兰迪那种浅灰本色在白底上等于看不见 → 一档档压暗到看得清为止
  const lightAccent = (()=>{ let l=c.l, x=primary;
    while(l>20 && contrastOn(x,"#ffffff")<3.2){ l-=3; x=hslToHex(c.h,c.s,l); } return x; })();
  const accent = dark ? hslToHex(c.h, Math.min(100, c.s*0.9+12), Math.max(58, Math.min(78, c.l+30))) : lightAccent;
  root.style.setProperty("--accent", accent);
  root.style.setProperty("--accent-ink", luminance(accent) > 0.42 ? "#0d1218" : "#ffffff");
  root.style.setProperty("--side-bg", hslToHex(c.h, Math.min(100,c.s*0.3+8), dark ? 9 : 96));
  localStorage.setItem("rw-primary", primary);
}
function themeSwatches(compact){
  const cur=currentPrimary().toLowerCase();
  return THEME_PRESETS.map(t=>`<span class="sw ${compact?"doton":""} ${t.primary.toLowerCase()===cur?"sel":""}" data-p="${t.primary}"
    style="--c:${t.primary}" title="${t.name}（${t.primary}）">${compact?"":`<em>${t.name}</em>`}</span>`).join("");
}
function bindSwatches(host){
  host.querySelectorAll("[data-p]").forEach(s=>s.onclick=()=>{ applyTheme(s.dataset.p); render(); });
}
let setSec = "theme";                    // 设置左列分区（照工作流中间可选栏）
let setMeta = null;                      // /api/settings/meta 缓存：tool 在 S.tools，skill / MCP 在这
const SET_SECS = [["theme", "🎨", "主题色"], ["perm", "🛡", "权限"], ["models", "🔌", "模型列表"], ["tools", "🧰", "Tool"], ["skills", "⚡", "Skill"], ["mcp", "🔗", "MCP"]];
function setPanel(){
  if(setSec === "theme") return `<div class="card"><h3>主题色</h3>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button class="btn" id="th">${document.documentElement.dataset.theme==="dark"?"切到浅色":"切到深色"}</button>
      <span class="hint">主题色：</span><div class="swatches" id="presets">${themeSwatches()}</div></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
      <span class="hint">自定义：</span>
      <input type="color" id="cpick" value="${currentPrimary()}" style="width:38px;height:30px;padding:2px">
      <input class="control" id="chex" value="${currentPrimary()}" style="max-width:120px" placeholder="#176B87">
      <button class="btn ghost" id="capply">应用</button>
      <button class="btn ghost" id="creset">恢复默认</button>
    </div>
    <div class="hint" style="margin-top:8px">字体大小：</div>
    <div style="display:flex;gap:6px;margin-top:4px">${[["compact","紧凑"],["comfortable","标准"],["large","大"]].map(([k,l])=>`<button class="btn ghost" data-f="${k}">${l}</button>`).join("")}</div></div>`;
  if(setSec === "perm") {
    const mode = (S.sec||{}).permission_mode || "";
    const opt = (k, t, d) => `<label class="perm-opt ${mode===k?"on":""}"><input type="radio" name="permmode" value="${k}" ${mode===k?"checked":""}><div><b>${t}</b><p>${d}</p></div></label>`;
    return `<div class="card"><h3>权限模式</h3>
      <div class="hint">我只能在程序自己的数据目录和你亲手选过的项目文件夹里读写；越界会直接拒绝并记一条审计。</div>
      <div class="perm-opts">
        ${opt("all", "允许所有", "在允许的目录里直接读写、编译、导出，不再逐次询问。")}
        ${opt("ask", "每次都要问", "编译、写文件、导出前先问你一句「允许这一次吗」。")}
      </div></div>
    <div class="card" style="margin-top:12px"><h3>上下文预算</h3>
      <div class="hint">每次对话最多带多少字历史给模型。40000 字 ≈ 2 万 token，约合十几轮长对话；调大记得更多，但更慢更贵。</div>
      <div class="ag-row" style="margin-top:8px">
        <input class="control" id="ctxchars" type="number" min="2000" max="400000" step="1000"
               value="${Number((S.sec||{}).history_chars) || 40000}" style="max-width:160px">
        <span class="hint">字（2000–400000）</span>
        <button class="btn btn-primary" id="ctxsave">保存</button>
      </div></div>`;
  }
  if(setSec === "models") return `<div class="card"><h3>模型列表</h3>
      <div class="hint">可选模型、凭据、默认模型都在这里设置（原来侧栏的「模型」页）。</div></div><div id="set-models"></div>`;
  if(setSec === "tools") return `<div class="card"><h3>Agent 可用工具 <span class="pill">${(S.tools||[]).length}</span></h3>
      <table><tr><th>工具</th><th>说明</th></tr>${(S.tools||[]).map(t=>`<tr><td><code>${esc(t.name)}</code></td><td class="muted">${esc(t.description)}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>边界</h3><ul class="hint"><li>只下载开放获取全文，不绕付费墙。</li><li>候选证据必须带页码与原文，对不上的会被拒收。</li><li>模型产出（审查/撰写）是草稿，不是结论。</li><li>数据只在本地 <code>data/research.sqlite3</code>。</li></ul></div>`;
  if(!setMeta) return `<div class="card"><h3>${setSec==="skills"?"Skill":"MCP"} 服务器</h3><div class="hint">正在读本机配置…</div></div>`;
  if(setMeta.__err) return `<div class="card"><h3>读不到</h3><div class="hint">${esc(setMeta.__err)}</div></div>`;
  if(setSec === "skills") return `<div class="card"><h3>Skill <span class="pill">${(setMeta.skills||[]).length}</span></h3>
      <div class="hint">本机技能包（只读）：<code>${esc(setMeta.hermes_dir||"")}</code> —— 安装/更新在技能目录里做。</div>
      <table><tr><th>技能</th><th>说明</th></tr>${(setMeta.skills||[]).map(x=>`<tr><td><code>${esc(x.name)}</code></td><td class="muted">${esc(x.desc)}</td></tr>`).join("")}</table></div>`;
  return `<div class="card"><h3>MCP <span class="pill">${(setMeta.mcp||[]).length}</span></h3>
      <div class="hint">本机配置里的 MCP 服务器（只读）：<code>${esc(setMeta.mcp_cfg||"")}</code></div>
      <table><tr><th>服务器</th><th>启动</th></tr>${(setMeta.mcp||[]).map(x=>`<tr><td><code>${esc(x.name)}</code></td><td class="muted">${esc(x.cmd)}</td></tr>`).join("")}</table></div>`;
}
function renderSettings(m){
  m.innerHTML = `<div class="page-head"><div><p class="inst-label">SETTINGS</p><h3>设置</h3></div>
      <div class="ph-acts"><button class="btn ghost" id="to-assist">管理助手 →</button></div></div>
    <div class="set-wrap" style="margin-top:12px">
      <nav class="set-nav">${SET_SECS.map(([k,i,l])=>`<button class="set-row ${k===setSec?"on":""}" data-set="${k}"><span class="set-ico">${i}</span>${l}</button>`).join("")}</nav>
      <div class="set-body" id="set-body">${setPanel()}</div></div>`;
  m.querySelectorAll("[data-set]").forEach(b=>b.onclick=()=>{ setSec = b.dataset.set; render(); });
  const ta = $("to-assist"); if(ta) ta.onclick = ()=>openTab("assistants");
  if(setSec === "perm"){
    m.querySelectorAll('input[name="permmode"]').forEach(r => r.onchange = async () => {
      await setPermissionMode(r.value); render();
    });
    const cs = $("ctxsave");
    if(cs) cs.onclick = async () => {
      const n = parseInt($("ctxchars").value, 10);
      if(!(n >= 2000 && n <= 400000)) return uiNotify("范围是 2000–400000 字");
      try {
        S.sec = await api("/api/security", { history_chars: n });
        uiNotify("上下文预算已保存：" + n + " 字");
      } catch(e) { uiNotify("保存失败：" + e.message); }
    };
  }
  if(setSec === "theme"){
    $("th").onclick = ()=>setTheme(document.documentElement.dataset.theme==="dark"?"light":"dark");
    bindSwatches($("presets"));
    $("cpick").oninput = (e)=>{ $("chex").value = e.target.value; applyTheme(e.target.value); };
    $("chex").onkeydown = (e)=>{ if(e.key==="Enter"){ applyTheme(e.target.value.trim()); render(); } };
    $("capply").onclick = ()=>{ applyTheme($("chex").value.trim()); render(); };
    $("creset").onclick = ()=>{ applyTheme(DEFAULT_THEME); render(); };
    m.querySelectorAll("[data-f]").forEach(b=>b.onclick=()=>setFont(b.dataset.f));
  }
  if(setSec === "models"){
    $("set-models").innerHTML = `<div class="card"><h3>模型</h3><div class="hint">可选模型、凭据、默认模型都在这里设置（原侧栏「模型」页）。</div></div>`;
    renderModels($("set-models"));      // 复用原面板
  }
  if((setSec === "skills" || setSec === "mcp") && !setMeta){
    api("/api/settings/meta").then(r=>{ setMeta = r; render(); }).catch(e=>{ setMeta = {__err:e.message}; render(); });
  }
}
const FONTS={compact:"13px",comfortable:"14px",large:"16px"};
function setTheme(t){ document.documentElement.dataset.theme=t; localStorage.setItem("rw-theme",t);
  applyTheme(currentPrimary()); render(); }   // 换明暗要重推导主色（深色底得提亮）
function setFont(f){ document.documentElement.style.setProperty("--app-font-size",FONTS[f]||"14px"); localStorage.setItem("rw-font",f); }

/* ---------------- markdown 极简渲染 ---------------- */
function md(t){
  return renderMarkdown(t);
}

/* ---------------- 启动 ---------------- */
async function refreshState(rerender=true){
  const st = await api("/api/state");
  const seen = S.loaded ? new Set((S.events||[]).map(e=>e.id)) : null;          // 首屏不算「新增」，免得一进来就跳走
  S.tasks=st.tasks; S.chats=st.chats||[]; S.assistants=st.assistants||[]; S.models=st.models; S.active=st.active; S.events=st.events; S.stages=st.stages||[];
  wfApplyCatalog(st.workflow_stages || []);
  const fresh = seen ? (st.events||[]).filter(e=>!seen.has(e.id)) : [];
  if(fresh.length){                                                            // 刚加的日程（agent 或别的窗口）：日历停到那一天
    const d = fresh[fresh.length-1].date || "";
    if(/^\d{4}-\d{2}-\d{2}$/.test(d)){ calSel = d; calY = +d.slice(0,4); calM = +d.slice(5,7)-1; }
  }
  { const pv = await api("/api/providers"); S.providers=pv.providers; S.wires=pv.wires; S.default=pv.default; }
  if(!S.tools.length) S.tools = (await api("/api/tools")).tools;
  S.loaded=true;
  await loadSecurity();                                                        // 权限模式（all / ask）
  if(curTask && !S.tasks.some(t=>t.id===curTask)) curTask=null;
  if(!curTask && S.tasks.length){ curTask=S.tasks[0].id; localStorage.setItem("rw-task",curTask); }
  if(curChat && !S.chats.some(c=>c.id===curChat)){ curChat=null; localStorage.removeItem("rw-chat"); }
  // 首次打开（本机还没选过对话）→ 默认进最新那条。新用户首启种的那条「小智」欢迎对话就在这里被打开
  if(!curChat && S.chats.length){ curChat=S.chats[0].id; localStorage.setItem("rw-chat",curChat); }
  if(rerender) render();
}
function initTheme(){
  document.documentElement.dataset.theme = localStorage.getItem("rw-theme") || "dark";
  applyTheme(currentPrimary());
  setFont(localStorage.getItem("rw-font") || "comfortable");
}
initTheme();
const savedRoute = localStorage.getItem("rw-route");
if(savedRoute && TITLES[savedRoute] && savedRoute !== "agent"){
  tabs.push({id:"restored",route:savedRoute,title:TITLES[savedRoute]}); activeTab="restored";
}
setInterval(()=>{ const el=document.querySelector(".topbar .date"); if(el) el.textContent=clock();
  const g=$("calHelloText"); if(g) g.textContent=helloText(); }, 30000);   // 顶栏日期/时间自己走；问候语跟着时间变
refreshState().catch(e=>{ $("main").innerHTML=`<div class="msg error">加载失败：${esc(e.message)}</div>`; });

// 深链：#library 这样带 hash 打开就直达那一页（书签/外部链接用）
const _hashRoute = (location.hash || "").replace(/^#\/?/, "");
if (_hashRoute && TITLES[_hashRoute]) openTab(_hashRoute);

/* ---------------- 情报日报（RSS，来源固定 5 个；读侧：
     来源/关键词/年份三条件筛选 + 手动刷新） ---------------- */
let itSrc = "", itQ = "", itYear = "", itItems = [], itTotal = 0, itTimer = 0, itSources = [];

async function intelLoad(){
  const p = new URLSearchParams({ source: itSrc, q: itQ, year: itYear });
  const d = await api("/api/rss/items?" + p.toString());
  itItems = d.items || []; itTotal = d.total || 0;
}
async function intelLoadSrcs(){
  const d = await api("/api/rss/sources");
  itSources = d.sources || [];
  const opts = [`<option value="">全部来源</option>`].concat(
    itSources.map(s=>`<option value="${s.id}" ${s.id===itSrc?"selected":""}>${esc(s.title)}（${s.count}）</option>`)).join("");
  const sel = $("itSrc"); if(sel) sel.innerHTML = opts;
}
function intelPaintList(){
  const box = $("itList"); if(!box) return;
  const n = $("itCount"); if(n) n.textContent = `${itTotal} 条`;
  box.innerHTML = itItems.length ? itItems.map(r=>`
    <div class="it-row">
      <a class="it-t" href="${esc(r.link)}" target="_blank" rel="noreferrer">${esc(r.title)}</a>
      <div class="it-m">${esc(r.authors || "作者未知")} · ${esc((r.published_at || "").slice(0,10) || "日期未知")} · ${esc(r.source_title || "")}</div>
      ${r.summary ? `<div class="it-s">${esc(r.summary)}</div>` : ""}
    </div>`).join("")
    : `<div class="hint">${itTotal ? "暂无匹配条目，换个条件或清掉筛选。" : "还没有数据：点右上「刷新」抓一批。"}</div>`;
}
async function intelRefresh(){
  const btn = $("itGo"); if(!btn || btn.disabled) return;
  const msg = $("itMsg");
  btn.disabled = true; btn.textContent = "抓取中…";
  if(msg) msg.textContent = "正在抓取 5 个来源…";
  try{
    const r = await api("/api/rss/refresh", {});
    const fail = (r.failures || []).length ? `；失败 ${r.failures.length} 个（${r.failures.map(f=>f.title).join("、")}）` : "";
    if(msg) msg.textContent = `已抓取 ${r.fetched}/${r.sources} 个来源，新增 ${r.added} 条${fail}`;
    await intelLoadSrcs(); await intelLoad(); intelPaintList();
  }catch(e){ if(msg) msg.textContent = "刷新失败：" + e.message; }
  btn.disabled = false; btn.textContent = "刷新";
}
function intelFilterDebounced(){
  clearTimeout(itTimer);
  itTimer = setTimeout(async ()=>{
    try{ await intelLoad(); intelPaintList(); }
    catch(e){ const m = $("itMsg"); if(m) m.textContent = "查询失败：" + e.message; }
  }, 300);
}
async function renderIntel(m){
  m.innerHTML = `
    <div class="card intel">
      <div class="it-head"><h3>情报日报 <span class="pill" id="itCount">…</span></h3>
        <button class="btn" id="itGo">刷新</button></div>
      <div class="it-bar">
        <select id="itSrc" class="control"><option value="">全部来源</option></select>
        <input id="itQ" placeholder="关键词（标题 / 摘要 / 作者）">
        <input id="itYear" placeholder="年份" inputmode="numeric">
      </div>
      <div class="hint" id="itMsg"></div>
      <div class="it-list" id="itList"></div>
    </div>`;
  $("itGo").onclick = intelRefresh;
  $("itQ").oninput = (e)=>{ itQ = e.target.value.trim(); intelFilterDebounced(); };
  $("itYear").oninput = (e)=>{ itYear = e.target.value.trim(); intelFilterDebounced(); };
  $("itSrc").onchange = async (e)=>{ itSrc = e.target.value; await intelLoad(); intelPaintList(); };
  try{ await intelLoadSrcs(); await intelLoad(); }
  catch(e){ m.querySelector("#itList").innerHTML = `<div class="hint">读不到数据：${esc(e.message)}</div>`; return; }
  intelPaintList();
}
