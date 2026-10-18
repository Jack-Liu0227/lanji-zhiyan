function markdownNoteClean(html){
  return (window.DOMPurify && window.marked)
    ? window.DOMPurify.sanitize(html, {ADD_TAGS:["mark","math","semantics","annotation","mrow","mi","mo","mn","msup","msub","mfrac","mspace","mtext"],
        ADD_ATTR:["align","style","target","rel","encoding","xmlns","aria-hidden"]})
    : html;
}
function renderMarkdown(value){
  const source = String(value||"");
  if(!window.marked || !window.marked.parse) return esc(source).replace(/\n/g,"<br />");
  try{ return markdownNoteClean(window.marked.parse(source)); }
  catch(e){ return esc(source).replace(/\n/g,"<br />"); }
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
  $("mdEmpty").classList.toggle("hidden", !!note);
  $("mdEditorShell").classList.toggle("hidden", !note);
  if(!note) return;
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
    await api("/api/win/" + (mdNarrowOn ? "narrow" : "wide"), {});
    $("mdView")?.classList.toggle("narrow", mdNarrowOn);
    document.body.classList.toggle("md-narrow", mdNarrowOn);       // 外壳（侧栏/标签/状态栏）也收掉
    if(b) b.textContent = mdNarrowOn ? "▯ 铺满" : "▯ 窄栏";
  }catch(e){ mdNarrowOn = !mdNarrowOn; mdFail(e, "切窄栏"); }        // 失败就把状态翻回去，别骗人
}
function mdFail(e, what){
  // 前端已更新、服务端还是旧的（接口 404）时最容易撞上：把话说清楚，别静默失败
  const hint = /not found|404/i.test(e?.message||"") ? "（服务端还是旧版本，重启一次「览籍智研」）" : "";
  console.error("笔记操作失败:", what, e);
  alert(`${what}失败：${e.message}${hint}`);
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
  if(!$("mdSource")) return;
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
  const input=[...(files||[])], list=input.filter(f=>/\.(?:md|markdown)$/i.test(f.name));
  if(!list.length) return alert("请导入 .md 或 .markdown 文件");
  let n=0;
  for(const f of list.slice(0,50)){
    if(f.size > 5*1024*1024) continue;
    try{
      const content=(await f.text()).replace(/^\uFEFF/,"");
      await mdNew(f.name.replace(/\.(?:md|markdown)$/i,"")||"导入笔记", content, true); n++;
    }catch(e){ alert(`无法读取「${f.name}」：${e.message}`); }
  }
  if(n) $("mdSaveState").textContent = `已导入 ${n} 篇`;
}
async function mdExport(fmt){
  const note = mdActive(); if(!note) return;
  await mdSave();
  if(fmt==="pdf"){                                   // 走浏览器打印 → 另存为 PDF
    const w = window.open("", "_blank");
    if(!w) return alert("浏览器拦了打印窗口");
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
    if(!note || !confirm(`确认删除笔记「${note.title}」？`)) return;
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
let zotRows = [], zotStart = 0, zotMore = false;
const zotSub = (it)=>[ (it.creators||[]).slice(0,3).join(", "), it.year, it.venue ].filter(Boolean).join(" · ");
function zotRowHTML(it){
  const badge = it.type === "note" ? `<em class="md-bind">📝 Zotero 笔记</em>`
              : (it.doi ? `<em class="md-bind">DOI</em>` : "");
  return `<div class="md-note-item" data-zot-key="${esc(it.key)}">
      <b>${esc(it.title || "(无标题)")}</b>${badge}<span>${esc(zotSub(it))}</span></div>`;
}
function zotClose(){ $("zoteroModal")?.classList.add("hidden"); }
async function zotOpen(){
  const box = $("zoteroModal"); if(!box) return;
  box.classList.remove("hidden");
  $("zotStatus").textContent = "正在连接 Zotero…";
  $("zotList").innerHTML = ""; $("zotCount").textContent = ""; $("zotCollection").innerHTML = "";
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
async function zotLoad(reset){
  if(reset) zotStart = 0;
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
  // 页脚那个「关闭」按钮在 v1.2.0 的模态框重排里删掉了（只留标题栏 ✕ + 点遮罩关），
  // 这里的绑定一并去掉 —— 它原来在顶层执行，取到 null 会抛错、把后面所有初始化全带没，整个界面起不来。
  $("zotCollection").onchange = ()=>zotLoad(true).catch(e=>mdFail(e,"读条目"));
  $("zotSearch").onclick = ()=>zotLoad(true).catch(e=>mdFail(e,"搜索"));
  $("zotQuery").onkeydown = (e)=>{ if(e.key === "Enter") zotLoad(true).catch(err=>mdFail(err,"搜索")); };
  $("zotToProject").onclick = async ()=>{                 // 分类 → 本项目队列：题录+本机PDF，不联网不下载
    if(!curTask) return alert("先在上面选一个项目，再导入");
    const btn = $("zotToProject"); btn.disabled = true;
    $("zotStatus").textContent = "正在从 Zotero 导入…";
    try{
      const d = await api("/api/workflow/zotero", {task_id: curTask, collection: $("zotCollection").value});
      $("zotStatus").textContent = `导入 ${d.total} 条：入库 ${d.downloaded} 篇，无 PDF 附件 ${d.manual} 条（都在「下载精读」里）。`;
      if(typeof dlRefresh === "function") dlRefresh().catch(()=>{});
    }catch(e){ $("zotStatus").textContent = "导入失败：" + e.message; }
    finally{ btn.disabled = false; }
  };
  $("zotList").onclick = (e)=>{
    if(e.target.closest("#zotMore")) return;               // 「加载更多」自己有 onclick
    const row = e.target.closest("[data-zot-key]");
    if(row) zotImport(row.dataset.zotKey);
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
    project_id: row?.project_id||curTask||"", frequency: row?.frequency||"daily", cron: row?.cron||"0 9 * * *",
    output_dir: row?.output_dir||"定时任务", enabled: row ? !!row.enabled : true };
}
function schEditorHTML(){
  if(!schEdit) return "";
  const e = schEdit;
  const projOpts = `<option value="">未绑定（运行会被阻断）</option>` +
    (S.tasks||[]).map(t=>`<option value="${t.id}" ${t.id===e.project_id?"selected":""}>${esc(t.title)}</option>`).join("");
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
      <label><span>输出目录（项目目录内的相对路径）</span><input id="schOut" value="${esc(e.output_dir)}"></label>
      <label><span>启用</span><select id="schOn"><option value="1" ${e.enabled?"selected":""}>已启用</option><option value="0" ${e.enabled?"":"selected"}>已暂停</option></select></label>
      <label class="full"><span>Prompt（到点了让 Agent 做什么）</span><textarea id="schPrompt">${esc(e.prompt)}</textarea></label>
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
      <div class="ln">${r.project_id?esc(schTaskName(r.project_id)||"已绑定项目"):"未绑定项目"} · 输出 ${esc(r.output_dir)}</div>
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
    $("schCancel") && ($("schCancel").onclick = ()=>{ schEdit=null; render(); });
    $("schCancel2") && ($("schCancel2").onclick = ()=>{ schEdit=null; render(); });
    $("schFreq").onchange = (e)=>{ const k=e.target.value; const c=SCH_PRESETS[k]; if(c) $("schCron").value=c;
      schEdit.frequency=k; if(c) schEdit.cron=c; };
    $("schSave").onclick = async ()=>{
      const body = { id: schEdit.id, name: nm(), prompt: $("schPrompt").value.trim(),
        project_id: $("schProj").value, frequency: $("schFreq").value, cron: $("schCron").value.trim(),
        output_dir: $("schOut").value.trim() || "定时任务", enabled: $("schOn").value === "1" };
      if(!body.prompt) return alert("Prompt 必填");
      try{ await api("/api/schedule/save", body); schEdit=null; await schLoad();
           schFeedback = "定时任务已保存；仅在应用运行时执行。"; render(); }
      catch(e){ alert("保存失败：" + e.message); }
    };
  };
  $("schNew").onclick = ()=>{ schEdit = schEditSeed(null); render(); setTimeout(()=>$("schName")?.focus(),30); };
  bindEditor();
  m.querySelectorAll("[data-sch-edit]").forEach(b=>b.onclick=()=>{
    const r = schRows.find(x=>x.id===b.dataset.schEdit); schEdit = schEditSeed(r); render(); });
  m.querySelectorAll("[data-sch-toggle]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/toggle", { id:b.dataset.schToggle, enabled: b.dataset.on !== "1" });
         schFeedback = "定时任务状态已更新。"; await schLoad(); render(); }
    catch(e){ alert("状态更新失败：" + e.message); } });
  m.querySelectorAll("[data-sch-del]").forEach(b=>b.onclick=async ()=>{
    const r = schRows.find(x=>x.id===b.dataset.schDel);
    if(!confirm(`确认归档“${r?r.name:""}”？归档后不会再自动运行。`)) return;
    try{ await api("/api/schedule/delete", { id:b.dataset.schDel }); schFeedback = "定时任务已归档。";
         await schLoad(); render(); }
    catch(e){ alert("归档失败：" + e.message); } });
  m.querySelectorAll("[data-sch-run]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run", { id:b.dataset.schRun }); schFeedback = "定时任务已立即触发；跑完会出现在「最近运行」。";
         render(); setTimeout(()=>{ if(route()==="schedules") render(); }, 6000); }
    catch(e){ alert("触发失败：" + e.message); } });
  m.querySelectorAll("[data-run-retry]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run", { id:b.dataset.runRetry }); schFeedback = "已按该规则重新触发一次。";
         render(); setTimeout(()=>{ if(route()==="schedules") render(); }, 6000); }
    catch(e){ alert("重试失败：" + e.message); } });
  m.querySelectorAll("[data-run-del]").forEach(b=>b.onclick=async ()=>{
    try{ await api("/api/schedule/run/delete", { ids:[b.dataset.runDel] }); await schLoad(); render(); }
    catch(e){ alert("删除运行记录失败：" + e.message); } });
  $("schRunsClear") && ($("schRunsClear").onclick = async ()=>{
    if(!confirm(`删除全部 ${schRuns.length} 条运行记录？定时规则与产物文件都不会被改动。`)) return;
    try{ await api("/api/schedule/run/delete", { ids: schRuns.map(x=>x.id) }); await schLoad(); render(); }
    catch(e){ alert("清空失败：" + e.message); } });
}
const schTaskName = (id)=> (S.tasks||[]).find(t=>t.id===id)?.title || "";

/* ---------------- 项目空间（页头 + 搜索 + 待办 + 文献/easyScholar + 星图 + 笔记选择与预览 + 概览四卡） ---------------- */
let spTab = "overview", spId = null;
const SP_TABS = [["overview","概览"],["todos","待办任务"],["papers","文献"],["stars","论文星图"],["notes","笔记"]];
let spActiveNoteId = null;
let spSelectedNoteIds = new Set();
let spTodoFilter = "ALL";
const esBadgeCache = new Map();

function spProg(d){
  const stages = (S.stages||[]).map(s=>s.key);
  const map = d.stage_map || {};
  const total = stages.length || 10;
  const done = stages.filter(k=>map[k] === "done").length;
  return { total, done, pct: Math.round(done*100/total) };
}
function spWhen(iso){ return iso ? fmtDate(iso) : "—"; }

function spBodyHTML(d){
  const t = d.task||{}, sum = d.summary||{}, p = spProg(d);

  // ---------- 1. 待办任务 ----------
  if(spTab === "todos"){
    return `<div class="card" style="margin-top:12px">
      <div class="sch-head">
        <div><p class="inst-label">PROJECT TASK BOARD</p><h3>${esc(t.title||"项目")} 的待办任务</h3></div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn ghost" id="spTodoImportWf" title="将当前项目未完成的工作流阶段一键导入为待办任务">⚡ 一键导入工作流步骤</button>
        </div>
      </div>
      <div style="display:flex;gap:8px;margin:14px 0 10px;align-items:center;flex-wrap:wrap">
        <input class="control" id="spTodoTitle" placeholder="添加新任务描述…" style="flex:1;min-width:200px">
        <select class="control" id="spTodoPriority" style="width:120px">
          <option value="P1">高优先级 (P1)</option>
          <option value="P2" selected>中优先级 (P2)</option>
          <option value="P3">低优先级 (P3)</option>
        </select>
        <button class="btn" id="spTodoAdd">＋ 添加任务</button>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:12px">
        <button class="btn ghost ${spTodoFilter==='ALL'?'on':''}" data-sp-todo-flt="ALL">全部</button>
        <button class="btn ghost ${spTodoFilter==='TODO'?'on':''}" data-sp-todo-flt="TODO">待处理</button>
        <button class="btn ghost ${spTodoFilter==='IN_PROGRESS'?'on':''}" data-sp-todo-flt="IN_PROGRESS">进行中</button>
        <button class="btn ghost ${spTodoFilter==='DONE'?'on':''}" data-sp-todo-flt="DONE">已完成</button>
      </div>
      <div id="spTodoList" style="display:flex;flex-direction:column;gap:8px;min-height:80px">
        <div class="hint">正在加载待办任务…</div>
      </div>
    </div>`;
  }

  // ---------- 2. 文献与 easyScholar 期刊分区 ----------
  if(spTab === "papers"){
    const rows = d.paper_list||[];
    return `<div class="card" style="margin-top:12px"><div class="sch-head"><div><p class="inst-label">PAPERS / PROJECT</p><h3>${esc(t.title||"")} 的文献</h3></div>
      <span class="pill">${rows.length} 条</span><button class="btn ghost" id="spToLib">去知识库 →</button></div>
      ${rows.length ? rows.map((r, idx)=>`<div class="sp-row"><span class="sp-ic">📄</span><div class="sp-main">
        <div class="sp-nm">${esc(r.title||r.filename||"未命名")}</div>
        <div class="sp-meta">${esc(r.authors||"作者未识别")}${r.doi?` · <span class="sp-doi">DOI ${esc(r.doi)}</span>`:" · DOI 未识别"}${r.year?` · ${esc(r.year)}`:""}${r.pages?` · ${String(r.pages).split(",").length} 页`:""}</div>
        <div class="es-badges" id="es-paper-${idx}" data-journal="${esc(r.journal || r.title || '')}" style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap"></div>
      </div></div>`).join("")
      : `<div class="hint" style="margin-top:10px">这个项目还没有入库文献。到「知识库」或工作流的检索步加进来。</div>`}</div>`;
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

  // ---------- 4. 笔记（左侧选择列表 + 右侧 Markdown 实时预览） ----------
  if(spTab === "notes"){
    return `<div class="sp-notes-layout">
      <div class="card">
        <div class="sch-head" style="margin-bottom:8px">
          <div><p class="inst-label">PROJECT NOTES</p><h3 style="margin:0">项目笔记</h3></div>
          <span class="pill" id="spNotesCount">0 篇</span>
        </div>
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <input class="control" id="spNoteSearchInp" placeholder="筛选项目笔记…" style="flex:1">
          <button class="btn ghost" id="spNoteNewBtn" title="新建项目笔记" style="padding:4px 10px;font-size:12px">＋ 新建</button>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--muted);padding:4px 0 8px;border-bottom:1px solid var(--line)">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" id="spNoteCheckAll"> <span>全选</span>
          </label>
          <span id="spNoteSelectedCount">已选 0 项</span>
          <button class="btn ghost" id="spNoteBatchExport" style="padding:2px 8px;font-size:11px" disabled>批量导出 MD</button>
        </div>
        <div id="spNotesList" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:6px;margin-top:8px">
          <div class="hint">正在加载项目笔记…</div>
        </div>
      </div>
      <div class="card" id="spNotePreviewCard">
        <div id="spNotePreviewEmpty" style="margin:auto;text-align:center;color:var(--muted);padding:40px">
          <div style="font-size:36px;margin-bottom:12px">📝</div>
          <h4 style="margin:0 0 6px">请选择一篇笔记进行预览</h4>
          <p class="hint">左侧勾选或点击笔记条目，即可在此处实时渲染 Markdown、公式与图表。</p>
        </div>
        <div id="spNotePreviewBody" style="display:none;flex-direction:column;height:100%;overflow:hidden">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;padding-bottom:10px;border-bottom:1px solid var(--line);margin-bottom:12px">
            <div>
              <h3 id="spPvTitle" style="margin:0 0 4px">未命名笔记</h3>
              <div class="hint" id="spPvMeta">—</div>
            </div>
            <div style="display:flex;gap:8px">
              <button class="btn ghost" id="spPvCopyBtn" style="font-size:12px;padding:4px 10px">复制原文</button>
              <button class="btn" id="spPvOpenBtn" style="font-size:12px;padding:4px 10px">去笔记页编辑 ↗</button>
            </div>
          </div>
          <div id="spPvContent" style="flex:1;overflow-y:auto;line-height:1.7;padding-right:8px;font-size:14px"></div>
        </div>
      </div>
    </div>`;
  }

  // ---------- 5. 概览 ----------
  const stages = (S.stages||[]).map(s=>({key:s.key, label:s.label, st:(d.stage_map||{})[s.key]||""}));
  const recent = (d.steps||[]).slice(0,6);
  return `
    <div class="sp-grid3" style="margin-top:12px">
      <div class="card sp-kpi"><p class="inst-label">PROGRESS</p><strong>${p.pct}%</strong>
        <p class="sp-kpi-note">${p.done}/${p.total} 步完成</p><div class="sp-bar"><div style="width:${p.pct}%"></div></div></div>
      <div class="card sp-kpi"><p class="inst-label">RESEARCH</p><strong>${sum.papers||0}</strong>
        <p class="sp-kpi-note">项目文献 · 证据 ${sum.evidence||0} 条</p></div>
      <div class="card sp-kpi sp-kpi-text"><p class="inst-label">QUESTION</p><strong>${esc(t.question||"未填研究问题")}</strong></div></div>
    <div class="sp-grid2" style="margin-top:12px">
      <div class="card"><div class="sch-head"><div><p class="inst-label">WORKFLOW SNAPSHOT</p><h3>工作流快照</h3></div>
        <button class="btn ghost" id="spToFlow">去科研台 →</button></div>
        ${stages.length ? stages.map(s=>`<div class="sp-row"><span class="sp-dot ${s.st==="done"?"on":(s.st?"run":"")}"></span>
          <div class="sp-main"><div class="sp-nm">${esc(s.label)}</div>
          <div class="sp-meta">${s.st==="done"?"已完成":(s.st?"已记录 · "+esc(s.st):"未开始")}</div></div></div>`).join("")
        : `<div class="hint" style="margin-top:10px">还没有步骤记录。</div>`}
        <div class="hint" style="margin-top:8px">最近步骤：${recent.length?esc(recent.map(r=>r.stage).join(" → ")):"—"}</div></div>
      <div class="card"><p class="inst-label">RELATIONSHIPS</p><h3 style="margin:0 0 8px">项目边界</h3>
        <div class="sp-kv"><span>项目位置</span><b>${esc(t.base_dir||"未设置（产出落工作区默认目录）")}</b></div>
        <div class="sp-kv"><span>关键词 / 体系</span><b>${esc(t.material_system||"—")}</b></div>
        <div class="sp-kv"><span>最近更新</span><b>${esc(spWhen(t.updated_at))}</b></div>
        <div class="hint" style="margin-top:10px">这一页只读聚合：不会改动文献、笔记、日程；写操作都在各自页面里。删除项目会连带它的文献、证据、笔记、会话、步骤。</div>
        <div class="pf-actions" style="margin-top:10px"><button class="btn ghost" id="spDel" style="color:var(--err)">删除项目</button></div></div>
    </div>
    <div class="card" style="margin-top:12px"><div class="sch-head"><div><p class="inst-label">STEP LOG</p><h3>步骤记录</h3></div>
      <span class="pill">${(d.steps||[]).length} 条</span></div>
      ${recent.length?recent.map(r=>`<div class="sp-row"><span class="sp-ic">•</span><div class="sp-main">
        <div class="sp-nm">${esc(r.stage)} · ${esc(r.status)}</div><div class="sp-meta">${esc(r.summary||"")} · ${esc(spWhen(r.created_at))}</div>
        </div></div>`).join(""):`<div class="hint" style="margin-top:10px">还没有步骤记录。</div>`}</div>`;
}
function spBind(d){
  const go = (id, route)=>{ const b=$(id); if(b) b.onclick = ()=>openTab(route); };
  go("spToFlow","workflow"); go("spToLib","library");
  const sg = $("spStarGen");                 // 论文星图是编译产物：点一下才算，算完落库
  if(sg) sg.onclick = async ()=>{
    const t0 = Date.now();
    sg.disabled = true; sg.innerHTML = `<span class="spin"></span>生成中 0s`;
    const tick = setInterval(()=>{ sg.innerHTML = `<span class="spin"></span>生成中 ${Math.round((Date.now()-t0)/1000)}s`; }, 500);
    try{ await api("/api/task/papergraph/build", {id: curTask}); }
    catch(e){ alert("生成失败：" + e.message); }
    finally{ clearInterval(tick); render(); }
  };
  const del = $("spDel");
  if(del) del.onclick = async ()=>{
    const t = d.task||{};
    if(!confirm(`删除项目「${t.title||""}」？\n它的文献、证据、笔记、会话、日程、步骤都会一起删掉，不可撤销。`)) return;
    try{ await api("/api/task/delete", {id: curTask}); }catch(e){ return alert("删除失败："+e.message); }
    curTask = null; localStorage.removeItem("rw-task"); await refreshState(false); openTab("tasks");
  };

  // ---------- 待办任务流转 ----------
  if(spTab === "todos"){
    const listEl = $("spTodoList");
    async function loadTodos(){
      if(!listEl) return;
      try{
        const res = await api(`/api/todos?task_id=${encodeURIComponent(curTask)}`);
        const todos = (res.todos || []).filter(t => spTodoFilter === "ALL" || t.status === spTodoFilter);
        if(!todos.length){
          listEl.innerHTML = `<div class="hint" style="padding:16px 0">${spTodoFilter==='ALL'?'暂无待办任务。点击上方输入框快速添加，或点击「一键导入工作流步骤」。':'该分类下暂无任务。'}</div>`;
          return;
        }
        listEl.innerHTML = todos.map(t => `
          <div class="sp-todo-row">
            <span class="sp-todo-st ${esc(t.status)}" data-todo-id="${esc(t.id)}" title="点击切换状态：待处理 → 进行中 → 已完成">
              ${t.status === 'DONE' ? '✓ 已完成' : t.status === 'IN_PROGRESS' ? '⏳ 进行中' : '○ 待处理'}
            </span>
            <span class="sp-todo-pri ${esc(t.priority)}">${esc(t.priority)}</span>
            ${t.stage ? `<span class="pill" style="font-size:11px">[${esc(t.stage)}]</span>` : ''}
            <div style="flex:1;min-width:0">
              <span style="font-size:13.5px;${t.status==='DONE'?'text-decoration:line-through;color:var(--muted)':''}">${esc(t.title)}</span>
              ${t.due_date ? `<span class="hint" style="margin-left:8px;font-size:11px">截止：${esc(t.due_date)}</span>` : ''}
            </div>
            <button class="btn ghost" data-todo-del="${esc(t.id)}" title="删除此任务" style="padding:2px 7px;font-size:12px;color:var(--muted)">🗑</button>
          </div>
        `).join("");

        listEl.querySelectorAll("[data-todo-id]").forEach(el => {
          el.onclick = async () => {
            await api("/api/todos/toggle", { id: el.dataset.todoId });
            await loadTodos();
          };
        });
        listEl.querySelectorAll("[data-todo-del]").forEach(btn => {
          btn.onclick = async () => {
            if(!confirm("确定删除该任务吗？")) return;
            await api("/api/todos/delete", { id: btn.dataset.todoDel });
            await loadTodos();
          };
        });
      }catch(e){
        if(listEl) listEl.innerHTML = `<div class="msg error">读取任务失败：${esc(e.message)}</div>`;
      }
    }
    loadTodos();

    const addBtn = $("spTodoAdd"), titleInp = $("spTodoTitle"), priSel = $("spTodoPriority");
    if(addBtn && titleInp){
      addBtn.onclick = async () => {
        const title = titleInp.value.trim();
        if(!title) return;
        addBtn.disabled = true;
        try{
          await api("/api/todos/add", { task_id: curTask, title, priority: priSel ? priSel.value : "P2" });
          titleInp.value = "";
          await loadTodos();
        }catch(e){ alert("添加任务失败：" + e.message); }
        finally{ addBtn.disabled = false; }
      };
      titleInp.onkeydown = (e) => { if(e.key === "Enter") addBtn.click(); };
    }

    const impWfBtn = $("spTodoImportWf");
    if(impWfBtn){
      impWfBtn.onclick = async () => {
        impWfBtn.disabled = true;
        try{
          const res = await api("/api/todos/import_workflow", { task_id: curTask });
          toast(`已根据工作流阶段同步创建 ${res.created} 条待办任务`, "success");
          await loadTodos();
        }catch(e){ alert("导入失败：" + e.message); }
        finally{ impWfBtn.disabled = false; }
      };
    }

    document.querySelectorAll("[data-sp-todo-flt]").forEach(b => {
      b.onclick = () => {
        spTodoFilter = b.dataset.spTodoFlt;
        document.querySelectorAll("[data-sp-todo-flt]").forEach(x => x.classList.remove("on"));
        b.classList.add("on");
        loadTodos();
      };
    });
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

  // ---------- 笔记列表与 Markdown 实时预览 ----------
  if(spTab === "notes"){
    const nList = $("spNotesList"), nCount = $("spNotesCount"), selCount = $("spNoteSelectedCount");
    const checkAll = $("spNoteCheckAll"), batchExp = $("spNoteBatchExport"), newBtn = $("spNoteNewBtn");
    const pvEmpty = $("spNotePreviewEmpty"), pvBody = $("spNotePreviewBody");
    const pvTitle = $("spPvTitle"), pvMeta = $("spPvMeta"), pvContent = $("spPvContent");
    const pvOpen = $("spPvOpenBtn"), pvCopy = $("spPvCopyBtn");

    let allNotes = [];
    async function loadNotes(){
      if(!nList) return;
      try{
        const res = await api(`/api/space/notes?task_id=${encodeURIComponent(curTask)}`);
        allNotes = res.notes || [];
        renderNoteRows();
        if(spActiveNoteId){
          showNotePreview(spActiveNoteId);
        } else if(allNotes.length){
          showNotePreview(allNotes[0].id);
        }
      }catch(e){
        nList.innerHTML = `<div class="msg error">加载笔记失败：${esc(e.message)}</div>`;
      }
    }

    function renderNoteRows(){
      if(!nList) return;
      const q = ($("spNoteSearchInp")?.value || "").trim().toLowerCase();
      const filtered = allNotes.filter(n => !q || (n.title||"").toLowerCase().includes(q) || (n.snippet||"").toLowerCase().includes(q));
      if(nCount) nCount.textContent = `${allNotes.length} 篇`;
      if(selCount) selCount.textContent = `已选 ${spSelectedNoteIds.size} 项`;
      if(batchExp) batchExp.disabled = spSelectedNoteIds.size === 0;

      if(!filtered.length){
        nList.innerHTML = `<div class="hint" style="padding:16px 0">${q ? '未检索到符合条件的笔记' : '当前项目暂无笔记。点击右上「＋ 新建」开始写作。'}</div>`;
        return;
      }

      nList.innerHTML = filtered.map(n => `
        <div class="sp-note-item ${n.id === spActiveNoteId ? 'active' : ''}" data-note-pick="${esc(n.id)}">
          <input type="checkbox" data-note-chk="${esc(n.id)}" ${spSelectedNoteIds.has(n.id) ? 'checked' : ''} style="margin-top:2px;cursor:pointer">
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.title || "未命名笔记")}</div>
            <div class="hint" style="font-size:11px;margin:2px 0 3px">${esc(n.updated_at || "")} · ${n.word_count || 0} 字</div>
            <div class="hint" style="font-size:11.5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(n.snippet || "")}</div>
          </div>
        </div>
      `).join("");

      nList.querySelectorAll("[data-note-pick]").forEach(item => {
        item.onclick = (e) => {
          if(e.target.type === "checkbox") return;
          showNotePreview(item.dataset.notePick);
        };
      });

      nList.querySelectorAll("[data-note-chk]").forEach(chk => {
        chk.onchange = () => {
          const nid = chk.dataset.noteChk;
          if(chk.checked) spSelectedNoteIds.add(nid);
          else spSelectedNoteIds.delete(nid);
          if(selCount) selCount.textContent = `已选 ${spSelectedNoteIds.size} 项`;
          if(batchExp) batchExp.disabled = spSelectedNoteIds.size === 0;
          if(checkAll) checkAll.checked = spSelectedNoteIds.size === allNotes.length && allNotes.length > 0;
        };
      });
    }

    async function showNotePreview(noteId){
      spActiveNoteId = noteId;
      document.querySelectorAll(".sp-note-item").forEach(el => {
        el.classList.toggle("active", el.dataset.notePick === noteId);
      });
      if(!pvBody || !pvEmpty) return;
      pvEmpty.style.display = "none";
      pvBody.style.display = "flex";
      pvContent.innerHTML = `<div class="hint">正在解析并渲染 Markdown 内容…</div>`;
      try{
        const res = await api(`/api/space/note?id=${encodeURIComponent(noteId)}`);
        const note = res.note || {};
        if(pvTitle) pvTitle.textContent = note.title || "未命名笔记";
        if(pvMeta) pvMeta.textContent = `更新时间：${note.updated_at || "—"} · 字数：${note.word_count || 0} 字`;
        if(pvContent) pvContent.innerHTML = renderMarkdown(note.content || "（笔记内容为空）");
        if(pvOpen) pvOpen.onclick = () => {
          mdActiveId = note.id;
          openTab("notes");
        };
        if(pvCopy) pvCopy.onclick = () => {
          navigator.clipboard.writeText(note.content || "");
          toast("笔记 Markdown 原文已复制到剪贴板", "success");
        };
      }catch(err){
        pvContent.innerHTML = `<div class="msg error">读取笔记详情失败：${esc(err.message)}</div>`;
      }
    }

    if(checkAll){
      checkAll.onchange = () => {
        if(checkAll.checked) allNotes.forEach(n => spSelectedNoteIds.add(n.id));
        else spSelectedNoteIds.clear();
        renderNoteRows();
      };
    }

    if(newBtn){
      newBtn.onclick = () => {
        openTab("notes");
        const btnNew = $("btnMdNew");
        if(btnNew) btnNew.click();
      };
    }

    if(batchExp){
      batchExp.onclick = async () => {
        const selected = allNotes.filter(n => spSelectedNoteIds.has(n.id));
        if(!selected.length) return;
        let mdBundle = "";
        for(const n of selected){
          try{
            const res = await api(`/api/space/note?id=${encodeURIComponent(n.id)}`);
            mdBundle += `# ${res.note.title || "未命名笔记"}\n> 更新时间：${res.note.updated_at}\n\n${res.note.content || ""}\n\n---\n\n`;
          }catch(_){}
        }
        const blob = new Blob([mdBundle], {type: "text/markdown;charset=utf-8"});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `${(d.task||{}).title || "项目"}_已选笔记_${selected.length}篇.md`;
        a.click();
        URL.revokeObjectURL(url);
        toast(`已成功打包导出 ${selected.length} 篇笔记`, "success");
      };
    }

    const sInp = $("spNoteSearchInp");
    if(sInp) sInp.oninput = () => renderNoteRows();

    loadNotes();
  }
}

async function renderSpace(m){
  m.style.padding = "";
  m.classList.remove("sp-fill");
  if(!curTask){ m.innerHTML = needTask(); return; }
  if(spId !== curTask){ spId = curTask; spTab = "overview"; spActiveNoteId = null; spSelectedNoteIds.clear(); }
  m.innerHTML = `<div class="muted">正在读取项目…</div>`;
  let d;
  try{ d = await api("/api/task?id=" + encodeURIComponent(curTask)); }
  catch(e){ m.innerHTML = `<div class="msg error">读取项目失败：${esc(e.message)}</div>`; return; }
  const t = d.task||{};
  const opts = (S.tasks||[]).map(x=>`<option value="${x.id}" ${x.id===curTask?"selected":""}>${esc(x.title)}</option>`).join("");
  m.innerHTML = `
    <div class="card">
      <div class="sp-hd1">
        <h3>${esc(t.title||"项目空间")} <span class="pill ok" title="绑定该项目的专用 agent">Partner Agent</span></h3>
        <span class="sp-step">阶段<b>${esc(t.stage_now||"未开始")}</b></span>
        <span class="sp-when">更新 ${esc(spWhen(t.updated_at))}</span>
        <span class="sp-gap"></span>
        <button class="btn" id="spFlow">进入科研台 →</button>
        <button class="btn ghost" id="spLeader">问智研管家 →</button>
      </div>
      <div class="sp-hd2">
        <p class="hint">项目级聚合只读取已关联的本地数据；写操作回到各领域的 Command 和安全通道。归档可恢复，外部文件不会被删除。</p>
        <select id="spPick" class="control" style="max-width:200px" title="选择项目空间">${opts}</select>
      </div>
      <div class="sp-search-bar" style="margin-top:12px;position:relative">
        <div style="position:relative;display:flex;gap:8px;align-items:center">
          <input id="spSearchInput" class="control" placeholder="全局搜索当前项目的文献、笔记、证据、待办任务…" style="flex:1;padding-left:32px">
          <span style="position:absolute;left:10px;top:50%;transform:translateY(-50%);opacity:1;pointer-events:none">🔍</span>
          <button class="btn ghost" id="spSearchClear" style="display:none;padding:5px 10px;font-size:12px">清除</button>
        </div>
        <div id="spSearchResults" class="card" style="display:none;position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:90;max-height:460px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,0.18);border:1px solid var(--accent);background:var(--panel)"></div>
      </div>
    </div>
    <div class="sp-tabs" id="spTabs">${SP_TABS.map(([k,l])=>`<button class="sp-tab ${k===spTab?"on":""}" data-sp-tab="${k}">${l}</button>`).join("")}</div>
    <div id="spBody"></div>`;

  $("spPick").onchange = (e)=>{ curTask = e.target.value; localStorage.setItem("rw-task", curTask); spId = null; render(); };
  $("spFlow").onclick = ()=>openTab("workflow");
  const sl = $("spLeader"); if(sl) sl.onclick = ()=>openTab("agent");
  m.querySelectorAll("[data-sp-tab]").forEach(b=>b.onclick=()=>{ spTab = b.dataset.spTab; render(); });
  $("spBody").innerHTML = spBodyHTML(d);
  if(spTab === "notes") m.classList.add("sp-fill");
  spBind(d);

  // 全局搜索交互
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

async function renderNotes(m){
  m.style.padding = "";
  m.innerHTML = `<div class="md-view" id="mdView">
    <aside class="md-note-side">
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
          <select id="mdTask" class="md-task-select" title="绑定到项目（不绑也能写）"><option value="">不绑定项目</option></select>
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
  mdBind(m);
  if(!mdLoaded) await mdLoad(); else mdPaint();
  mdFocus(localStorage.getItem("rw-md-focus")==="true", true);     // 上次在专注模式就还在专注模式
  if(mdNarrowOn){ $("mdView")?.classList.add("narrow"); document.body.classList.add("md-narrow"); }
  const nb = $("btnMdNarrow"); if(nb && mdNarrowOn) nb.textContent = "▯ 铺满";
}
if(!window.__rwMdEsc){                     // Esc 退出专注（只注册一次，renderNotes 会被反复调用）
  window.__rwMdEsc = 1;
  document.addEventListener("keydown", (e)=>{
    if(e.key==="Escape" && mdFocusOn && route()==="notes"){
      if(mdNarrowOn) mdNarrow();          // 窄栏里 Esc = 退出专注 + 回铺满（别把人留在几十像素的分栏里）
      mdFocus(false);
    }
  });
}

/* ---------------- 模型 ---------------- */
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
      if(!confirm("清除该 Provider 的凭据？")) return;
      try{ await api("/api/providers/key",{id:b.dataset.keyClear,api_key:""}); await refreshState(); }catch(e){ bail(b.dataset.keyClear,e); } });
    grid.querySelectorAll("[data-prov-del]").forEach(b=>b.onclick=async()=>{
      const id=b.dataset.provDel, p=all.find(x=>x.id===id)||{};
      if(!confirm(p.source==="custom"?"删除该 Provider？":"复位该 Provider？（清 Key 与已采用模型，卡片保留）")) return;
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
/* ---- 主题：抄 zotero-lit-tool，一个「主色」推导整套配色（8 个预设 + 自定义色号） ---- */
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
  if(!c){ alert("色值格式不正确，请输入如 #176B87 的色号"); return; }
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
const SET_SECS = [["theme", "🎨", "主题色"], ["models", "🔌", "模型列表"], ["tools", "🧰", "Tool"], ["skills", "⚡", "Skill"], ["mcp", "🔗", "MCP"]];
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
  const html = esc(t||"")
    .replace(/^### (.*)$/gm,"<h3>$1</h3>").replace(/^## (.*)$/gm,"<h2>$1</h2>").replace(/^# (.*)$/gm,"<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g,"<b>$1</b>").replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/^\s*[-*] (.*)$/gm,"<li>$1</li>").replace(/\|(.+)\|/g,m=>m);
  return html.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g,"<ul>$1</ul>").replace(/\n{2,}/g,"<br>");
}

/* ---------------- 启动 ---------------- */
async function refreshState(rerender=true){
  const st = await api("/api/state");
  S.tasks=st.tasks; S.chats=st.chats||[]; S.assistants=st.assistants||[]; S.models=st.models; S.active=st.active; S.events=st.events; S.stages=st.stages||[];
  { const pv = await api("/api/providers"); S.providers=pv.providers; S.wires=pv.wires; S.default=pv.default; }
  if(!S.tools.length) S.tools = (await api("/api/tools")).tools;
  S.loaded=true;
  if(curTask && !S.tasks.some(t=>t.id===curTask)) curTask=null;
  if(!curTask && S.tasks.length){ curTask=S.tasks[0].id; localStorage.setItem("rw-task",curTask); }
  if(curChat && !S.chats.some(c=>c.id===curChat)){ curChat=null; localStorage.removeItem("rw-chat"); }
  if(rerender) render();
}
function initTheme(){
  document.documentElement.dataset.theme = localStorage.getItem("rw-theme") || "dark";
  applyTheme(currentPrimary());
  setFont(localStorage.getItem("rw-font") || "comfortable");
}
initTheme();
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
