function renderStar(m){
  m.innerHTML = `<div class="sm-wrap">
      <div class="sm-bar">
      <b>知识星图</b>
      <span class="hint">点项目进分支 → 点星团看条目 → 点条目看分析，Esc 逐层退回</span>
    </div>
    <iframe id="kbFrame" class="sm-frame" src="/kb.html?theme=${document.documentElement.dataset.theme||"dark"}" title="知识星图"></iframe>
  </div>`;
}

// 星图里点「在应用里打开」：论文开 PDF，笔记跳到笔记页（其他类型不猜，留在星图里看分析）
window.addEventListener("message", e => {
  const d = e.data || {};
  if(d.rw === "theme"){ setTheme(d.theme || "dark"); return; }   // 星图只在深色下画：点提示里的按钮直接切过来
  if(d.rw !== "open") return;
  if(d.kind === "post") api("/api/paper/open?id=" + encodeURIComponent(d.ref)).catch(err => alert("打开失败：" + err.message));
  else if(d.kind === "note") openTab("notes");
});

/* ---------------- 知识库（Obsidian Vault 看板） ----------------
   版式/口径照 Baixiao-Reading-Cockpit（未读/在读/已读 + 吸收状态、年度热力、阅读完成率、状态写回笔记）；
   路径与写回边界照科研工作台的 obsidian 集成（服务端校验，永不写 .obsidian/，只改 frontmatter 那几个键）。
   Vault 路径存 localStorage：rw-vault。 */
const KB_READING = ["未读", "在读", "已读"];
const KB_ABSORB = ["待沉淀", "已沉淀", "无需沉淀"];
const KB_TABS = [["all", "全部"], ["today", "今日新增"], ["unread", "未读"], ["reading", "在读"], ["pending", "待沉淀"], ["long", "长期未读"]];
let kb = { vault: localStorage.getItem("rw-vault") || "", data: null, err: "", busy: false,
           tab: "all", folder: "", q: "", active: "", tip: "" };

async function renderLibrary(m, opts={}){
  kb.embed = !!opts.embed; kb.project = opts.project || ""; kb.host = m;   // 嵌进 项目空间·笔记：不给 PDF 版块、默认只看本项目
  m.innerHTML = kbShell();
  kbBind(m);
  if(kb.vault && !kb.data && !kb.busy) kbLoad();
  if(!kb.embed) kbPapers(m);   // 嵌进项目空间时不挂「本项目已入库 PDF」版块
}

async function kbLoad(){
  if(!kb.vault) return;
  kb.busy = true; kb.err = ""; kbRender();
  try{
    kb.data = await api("/api/vault/scan?vault=" + encodeURIComponent(kb.vault), {method:"GET"});
    if(!kb.active || !kb.data.notes.some(n=>n.path===kb.active)) kb.active = kb.data.notes[0] ? kb.data.notes[0].path : "";
  }catch(e){ kb.err = e.message; kb.data = null; }
  kb.busy = false; kbRender();
}

function kbDom(){ return kb.host || $("main"); }
function kbRender(){ const m = kbDom(); if(!m || !m.querySelector(".kb-page")) return; m.innerHTML = kbShell(); kbBind(m); }
function kbState(cls, text){ return `<span class="kb-state kb-${cls}">${esc(text)}</span>`; }
function kbFmt(iso){ return iso ? esc(iso) : "—"; }
function kbToday(){ return new Date().toLocaleDateString("sv-SE"); }

function kbFiltered(){
  if(!kb.data) return [];
  let ns = kb.data.notes.slice();
  if(kb.embed && kb.onlyProject !== false){                     // 项目空间里默认只看绑到本项目的笔记
    const name = kbProjectName();
    if(name) ns = ns.filter(n => n.project === name);
  }
  if(kb.folder) ns = ns.filter(n=>n.rootFolder === kb.folder);
  const t = kbToday();
  if(kb.tab === "today") ns = ns.filter(n=>n.createdFromFrontmatter && n.created === t);
  else if(kb.tab === "unread") ns = ns.filter(n=>n.reading === "未读");
  else if(kb.tab === "reading") ns = ns.filter(n=>n.reading === "在读");
  else if(kb.tab === "pending") ns = ns.filter(n=>n.reading === "已读" && n.absorption === "待沉淀");
  else if(kb.tab === "long") ns = ns.filter(n=>n.reading === "未读" && n.created < new Date(Date.now()-30*864e5).toLocaleDateString("sv-SE"));
  const q = kb.q.trim().toLowerCase();
  if(q) ns = ns.filter(n=>(n.title + " " + n.path + " " + n.tags.join(" ")).toLowerCase().includes(q));
  return ns;
}

function kbStat(id, label, value, caption, ico, slim){
  return `<article class="kb-stat kb-fluid-stat${slim?" kb-slim":""}" data-fluid-card="${id}">
    <canvas class="kb-fluid-canvas" aria-hidden="true"></canvas>
    <div class="kb-fluid-noise" aria-hidden="true"></div>
    <div class="kb-fluid-fallback" aria-hidden="true"></div>
    <div class="kb-stat-content"><div class="kb-stat-t">${esc(label)}</div>
    <div><span class="kb-stat-v">${value}</span><span class="kb-stat-u">篇笔记</span></div>
    <div class="kb-stat-c">${esc(caption)}</div><span class="kb-stat-i">${ico}</span></div></article>`;
}

/* 环绕卡的目录 = Vault 根目录聚合（照参考的 folders()：按篇数排序，最多 6 张） */
function kbFolders(){
  if(!kb.data) return [];
  const m = new Map();
  kb.data.notes.forEach(n=>{ const k = n.rootFolder || "根目录";
    const c = m.get(k) || {name:k, total:0, unread:0};
    c.total++; if(n.reading === "未读") c.unread++; m.set(k, c); });
  return [...m.values()].sort((a,b)=>b.total - a.total);
}

function kbShell(){
  const d = kb.data, st = d ? d.stats : null;
  const name = kb.vault ? kb.vault.replace(/[\\/]+$/, "").split(/[\\/]/).pop() : "";
  const head = `<header class="kb-head">
    ${kb.embed ? "" : `<div><h2>知识库</h2><p>未读识别 · 阅读推进 · 知识沉淀</p></div>`}
    <div class="kb-actions">
      <label class="kb-search">⌕<input id="kbQ" placeholder="搜索笔记、目录或标签…" value="${esc(kb.q)}"></label>
      <button class="btn" id="kbFolder">${kb.folder ? "⌁ " + esc(kb.folder) : "⌁ 全部目录"}</button>
      <button class="btn" id="kbVault">选择 Vault</button>
      <button class="btn btn-primary" id="kbScan">${kb.busy ? "扫描中…" : "＋ 立即扫描"}</button>

      ${kb.embed ? `<span class="kb-scope"><button class="btn${kb.onlyProject !== false ? " on" : ""}" id="kbScopeP" title="只看绑到当前项目的笔记">本项目</button><button class="btn${kb.onlyProject === false ? " on" : ""}" id="kbScopeA" title="整个 Vault">全库</button></span>` : ""}
    </div></header>`;
  const vault = kb.vault
    ? `<div class="kb-vault"><span class="kb-vault-i">◈</span><b>${esc(d ? d.name : name)}</b>
       <span class="kb-path" title="${esc(kb.vault)}">${esc(kb.vault)}</span>
       ${d ? `<span class="kb-vault-n">${d.notes.length} 篇${d.skipped ? ` · 跳过 ${d.skipped}` : ""}</span>` : ""}</div>`
    : `<div class="kb-vault kb-empty">还没有授权 Vault —— 点右上「选择 Vault」挑一个 Obsidian 库目录。<span class="muted">只读/只写你库里的 Markdown frontmatter，永不碰 .obsidian/</span></div>`;
  if(kb.err) return `<div class="kb-page">${head}${vault}<div class="card kb-err">扫描失败：${esc(kb.err)}</div></div>`;
  if(!kb.vault) return `<div class="kb-page">${head}${vault}</div>`;
  if(!d) return `<div class="kb-page">${head}${vault}<div class="card kb-load">正在扫描 Vault…</div></div>`;

  const ns = kbFiltered();

  if(ns.length && !ns.some(n => n.path === kb.active)) kb.active = ns[0].path;   // 过滤后别把看不见的笔记留在详情里

  const list = ns.length ? ns.map(n=>`<button class="kb-row${n.path===kb.active?" on":""}" data-note="${esc(n.path)}">
      <span class="kb-row-t" title="${esc(n.path)}">${esc(n.title)}</span>
      <span class="kb-row-f">${esc(n.folder)}</span>
      ${kbState("r"+KB_READING.indexOf(n.reading), n.reading)}
      ${n.reading==="已读" ? kbState("a"+KB_ABSORB.indexOf(n.absorption), n.absorption) : ""}
      <span class="kb-row-d">${kbFmt(n.created)}</span></button>`).join("")
    : `<div class="hint">当前筛选下没有笔记。</div>`;

  const folders = [...new Set(d.notes.map(n=>n.rootFolder).filter(Boolean))].sort();
  const stats = `<section class="kb-stats">
    ${kbStat("today", "今日新增", st.todayAdded, "按 frontmatter 创建日期", "▣")}
    ${kbStat("unread", "未读库存", st.unread, "全库实时统计", "〽")}
    ${kbStat("read", "已经阅读", st.read, "全库实时统计", "✓")}
    ${kbStat("backlog", "长期未读", st.longUnread, "超过 30 天的未读", "!", true)}</section>`;

  const board = `<section class="kb-board">
    <div class="card kb-queue">
      <div class="kb-sec-head"><b>阅读队列</b><span>真实数据 · ${ns.length}/${d.notes.length} 篇</span></div>
      <div class="kb-tabs">${KB_TABS.map(([k,label])=>`<button class="kb-tab${kb.tab===k?" on":""}" data-tab="${k}">${esc(label)}</button>`).join("")}</div>
      <div class="kb-list">${list}</div>
      <div class="kb-foot">${kb.embed && kb.onlyProject !== false ? `本项目 ${ns.length} 篇 · ` : ""}全库 ${d.notes.length} 篇 · 待沉淀 ${st.pending} 篇 · 词数合计 ${st.words.toLocaleString()}${folders.length?` · 目录 ${folders.length} 个`:""}</div>
    </div>
    ${kbVisual(st)}
    <div class="kb-side">${kbDetail()}</div></section>`;

  const heat = `<section class="card kb-heat">${kbHeat()}</section>`;
  const pdf = kb.embed ? "" : `<section class="card" id="kbPapers"><h3>本项目已入库 PDF</h3><div class="hint">加载中…</div></section>`;   // 项目空间里不放 PDF 版块
  return `<div class="kb-page">${head}${vault}${stats}${board}${heat}${pdf}</div>`;
}

function kbDetail(){
  const n = kb.data ? kb.data.notes.find(x=>x.path===kb.active) : null;
  if(!n) return `<div class="card kb-detail"><div class="kb-detail-id">✦ 智能详情</div><div class="hint">左边挑一篇笔记。</div></div>`;
  const opts = (list, cur) => list.map(v=>`<option value="${v}"${v===cur?" selected":""}>${v}</option>`).join("");
  return `<div class="card kb-detail">
    <div class="kb-detail-head"><span>✦ 智能详情</span><span class="muted">本机数据</span></div>
    <div class="kb-detail-id" title="${esc(n.path)}">${esc(n.path)}</div>
    <div class="kb-detail-title">${esc(n.title)}</div>
    <div class="kb-detail-grid">
      <span>所属目录</span><b>☑ ${esc(n.folder)}</b>

      ${kb.embed ? `<span>所属项目</span><select class="kb-sel" id="kbProjSel">${opts(["", ...(S.tasks || []).map(t => t.title)], n.project || "")}</select>` : ""}
      <span>创建日期</span><b>▣ ${kbFmt(n.created)}${n.createdFromFrontmatter?"":"（按文件时间）"}</b>
      <span>最近修改</span><b>${esc(fmtDate(new Date(n.modified*1000).toISOString()))}</b>
      <span>词数</span><b>${n.words.toLocaleString()}</b>
      <span>阅读状态</span><select class="kb-sel" data-field="阅读状态">${opts(KB_READING, n.reading)}</select>
      <span>吸收状态</span><select class="kb-sel" data-field="吸收状态">${opts(KB_ABSORB, n.absorption)}</select>
      <span>标签</span><b>${n.tags.length?n.tags.map(t=>`<i class="kb-tag">#${esc(t)}</i>`).join(""):"—"}</b>
    </div>
    <div class="kb-tip-card"><b>推进建议</b>• 优先处理「在读」超过 7 天的笔记<br>• 已读但还「待沉淀」的，提炼成小结或 Wiki 条目</div>
    <div class="kb-actions2">
      <button class="btn" id="kbOpen">在 Obsidian 打开</button>
      <button class="btn" id="kbNext">推进状态</button>
      <button class="btn btn-primary" id="kbAbsorb">${n.absorption==="待沉淀"?"标记已沉淀":"标回待沉淀"}</button>
    </div></div>`;
}

function kbVisual(st){
  const n = kbFolders().length;
  return `<section class="kb-visual">
    <div class="kb-toolbar">
      <span><button class="kb-orbit-mode active">◉ 3D 环绕</button><button class="kb-auto active">↻ 自动巡航</button><button class="kb-reset">⌖ 复位视角</button></span>
      <span><button class="kb-speed">速度 1×</button><button class="kb-folder-filter">${kb.folder?("⌁ "+esc(kb.folder)):"⌁ 全部目录"}</button></span>
    </div>
    <div class="kb-stage">
      <div class="kb-progress"><div class="kb-progress-controls"><button data-progress-scale="-0.1" aria-label="缩小">−</button><button data-progress-scale="0.1" aria-label="放大">＋</button></div>
        <div>阅读完成率</div><b>${st.rate}%</b><small>已读 / 全部笔记</small><i><em style="width:${st.rate}%"></em></i></div>
      <div class="kb-live"><i></i>实时交互</div>
      <div class="kb-floor"></div><div class="kb-orbit-core"></div>
      <div class="kb-scene"><div class="kb-ring"></div></div>
      <div class="kb-caption">⌁ 拖动旋转 · 滚轮缩放 · 点目录卡筛队列 <b>${esc(kb.folder || "全部目录")}</b></div>
    </div>
    <div class="kb-foot" style="padding:7px 2px 9px">目录卡 ${n} 个 · 未读 ${st.unread} · 在读 ${st.reading} · 已读 ${st.read} · 待沉淀 ${st.pending}</div>
  </section>`;
}

function kbHeat(){
  const days = kb.data.days;
  const maxAdded = Math.max(1, ...days.map(d=>d.added)), maxRead = Math.max(1, ...days.map(d=>d.read));
  const lv = (v, max) => v ? (v/max > .66 ? 2 : 1) : 0;
  const first = (new Date(days[0].date + "T12:00:00").getDay() + 6) % 7;      // 周一 = 0
  const empty = Array.from({length:first}, ()=>`<span class="kb-heat-empty"></span>`).join("");
  const today = kbToday();
  const cells = days.map(d=>`<button class="kb-heat-cell${d.date===today?" is-today":""}" data-d="${d.date}"
      data-added="${d.added}" data-read="${d.read}" data-pending="${d.pending}"
      ${d.added||d.read?`data-lv="${lv(d.added,maxAdded)}" data-rv="${lv(d.read,maxRead)}"`:""}
      aria-label="${d.date}：新增 ${d.added}，已读 ${d.read}，待沉淀 ${d.pending}"></button>`).join("");
  const months = days.filter((d,i)=>i===0 || d.date.slice(8)==="01" || i===days.length-1)
      .map(d=>`<span>${Number(d.date.slice(5,7))}月</span>`).join("");
  const last = days[days.length-1];
  return `<div class="kb-heat-head"><div><b>阅读时间轴</b><small>近一年 · 真实阅读行为</small></div>
      <time>${today}</time><button class="kb-heat-today" id="kbHeatToday">回到今日</button></div>
    <div class="kb-heat-line">今日新增：<strong>${last.added}</strong> 篇　<i></i>　全库总计：${kb.data.stats.total} 篇　<i></i>　已读：${kb.data.stats.read} 篇　<i></i>　待沉淀：${kb.data.stats.pending} 篇　<i></i>　未读积压：${kb.data.stats.unread} 篇</div>
    <div class="kb-heat-scroll"><div class="kb-heat-grid">${empty}${cells}</div></div>
    <div class="kb-heat-months">${months}</div>
    <div class="kb-heat-legend"><span class="kb-lg">无操作</span><span class="kb-lg" data-lv="1">仅新增</span><span class="kb-lg" data-lv="2">高新增</span><span class="kb-lg" data-rv="1">少量已读</span><span class="kb-lg" data-rv="2">高阅读量</span></div>
    <div class="kb-heat-tip" id="kbHeatTip">${kb.tip ? esc(kb.tip) : "鼠标移到格子上看当天明细"}</div>`;
}

function kbPapers(m){
  const box = m.querySelector("#kbPapers");
  if(!box) return;
  if(!curTask){ box.innerHTML = `<h3>本项目已入库 PDF</h3><div class="hint">先在上面「项目」里选一个项目，这里会列它的 PDF 与检索结果。</div>`; return; }
  api(`/api/task?id=${encodeURIComponent(curTask)}`).then(d=>{
    box.innerHTML = `<h3>本项目已入库 PDF <span class="pill">${(d.paper_list||[]).length}</span></h3>
      ${(d.paper_list||[]).length ? `<table><tr><th>文件名</th><th>页数</th><th>入库时间</th></tr>
        ${d.paper_list.map(p=>`<tr><td>${esc(p.filename)}</td><td class="muted">${p.pages||0}</td><td class="muted">${fmtDate(p.created_at)}</td></tr>`).join("")}</table>`
        : `<div class="hint">这个项目还没入库 PDF，去「工作流 · 下载精读」下几篇。</div>`}`;
  }).catch(()=>{ box.innerHTML = `<h3>本项目已入库 PDF</h3><div class="hint">读取失败。</div>`; });
}

function kbBind(m){
  const on = (sel, ev, fn) => m.querySelector(sel) && m.querySelector(sel).addEventListener(ev, fn);
  on("#kbVault", "click", kbPickVault);
  on("#kbScan", "click", kbLoad);
  on("#kbFolder", "click", ()=>{ const fs = [...new Set(kb.data.notes.map(n=>n.rootFolder).filter(Boolean))].sort();
      const i = fs.indexOf(kb.folder); kb.folder = i >= fs.length-1 ? "" : fs[i+1] || ""; kbRender(); });
  on("#kbQ", "input", e=>{ kb.q = e.target.value; const list = m.querySelector(".kb-list");
      if(!list) return; const keep = document.activeElement;
      m.innerHTML = kbShell(); kbBind(m);
      const q = m.querySelector("#kbQ"); if(q){ q.value = kb.q; q.focus(); } });
  on("#kbHeatToday", "click", ()=>{ const sc = m.querySelector(".kb-heat-scroll"); if(sc) sc.scrollLeft = sc.scrollWidth; });
  m.querySelectorAll(".kb-tab").forEach(b=>b.addEventListener("click", ()=>{ kb.tab = b.dataset.tab; kbRender(); }));
  m.querySelectorAll(".kb-row").forEach(b=>b.addEventListener("click", ()=>{ kb.active = b.dataset.note; kbRender(); }));
  m.querySelectorAll(".kb-heat-cell").forEach(c=>{
    c.addEventListener("mouseenter", ()=>{ kb.tip = `${c.dataset.d}：新增 ${c.dataset.added}，已读 ${c.dataset.read}，待沉淀 ${c.dataset.pending}`;
        const t = m.querySelector("#kbHeatTip"); if(t) t.textContent = kb.tip; });
    c.addEventListener("click", ()=>{ const t = m.querySelector("#kbHeatTip"); if(t) t.textContent = kb.tip + "（已固定）"; });
  });
  on("#kbProjSel", "change", e => kbBindProject(e.target.value));
  on("#kbScopeP", "click", () => { kb.onlyProject = true; kbRender(); });
  on("#kbScopeA", "click", () => { kb.onlyProject = false; kbRender(); });
  const n = kb.data ? kb.data.notes.find(x=>x.path===kb.active) : null;
  m.querySelectorAll(".kb-sel").forEach(s=>s.addEventListener("change", ()=>kbWrite(n, s.dataset.field, s.value)));
  on("#kbOpen", "click", ()=>api("/api/vault/open", {vault:kb.vault, path:kb.active}).catch(e=>alert(e.message)));
  on("#kbNext", "click", ()=>n && kbWrite(n, "阅读状态", n.reading==="未读" ? "在读" : n.reading==="在读" ? "已读" : "未读"));
  on("#kbAbsorb", "click", ()=>n && kbWrite(n, "吸收状态", n.absorption==="待沉淀" ? "已沉淀" : "待沉淀"));
  const cycleFolder = ()=>{ const fs = kbFolders().map(f=>f.name); const i = fs.indexOf(kb.folder);
      kb.folder = i >= fs.length-1 ? "" : (fs[i+1] || ""); kbRender(); };
  on("#kbFolder", "click", cycleFolder);
  if(window.KB3D){
    KB3D.fluid(m);
    KB3D.orbit(m, { folders: kbFolders(), onCycle: cycleFolder,
      onPick: name=>{ kb.folder = (kb.folder === name ? "" : name); kbRender(); } });
  }
}

const kbProjectName = () => (S.tasks || []).find(t => t.id === kb.project)?.title || "";

async function kbBindProject(project){

  const n = kb.data ? kb.data.notes.find(x => x.path === kb.active) : null;

  if(!n) return;

  try{

    const d = await api("/api/vault/project", {vault: kb.vault, path: n.path, project});

    kb.data = d; kbRender();

  }catch(e){ alert("绑定失败：" + e.message); }

}

async function kbWrite(n, field, value){
  if(!n) return;
  try{
    const d = await api("/api/vault/status", {vault:kb.vault, path:n.path, field, value});
    kb.data = d; kbRender();
  }catch(e){ alert("写回失败：" + e.message); }
}

async function kbPickVault(){
  const r = await api("/api/folder/pick?title=" + encodeURIComponent("选择 Obsidian Vault（知识库根目录）") +
                      "&initial=" + encodeURIComponent(kb.vault || ""), {method:"GET"}).catch(e=>({error:e.message}));
  if(!r || r.error) return alert("打开文件夹选择框失败：" + ((r && r.error) || "未知"));
  if(!r.dir) return;
  kb.vault = r.dir; localStorage.setItem("rw-vault", r.dir);
  kb.data = null; kb.active = ""; kb.err = ""; kbRender(); kbLoad();
}


/* ---------------- 笔记（大笔记：Markdown 编辑器，不绑项目） ---------------- */
let mdNotes=[], mdActiveId=null, mdSaveTimer=null, mdLoaded=false, mdFocusOn=false, mdNarrowOn=false, mdFocusPrev="split";
