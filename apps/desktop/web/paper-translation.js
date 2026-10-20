/* ============ 论文翻译（工作流第 4 步）============
   照用户草图：左上「＋ 添加论文」（文件对话框 / 拖进来）→ 自动解析 DOI/题目/发表时间/关键词
   → 大框里是可滚动的 PDF（缩放 / 页码 / 荧光笔）→ 底部三个功能键：中英互译 / 笔记模式 / 荧光笔。
   PDF 渲染用 vendor 的 pdf.js 3.11（能选中文字，荧光笔才有依据）；译文用 BabelDOC（后端）。 */
const TR = { pid: "", meta: null, view: "read", scale: 1.15, noteScale: 1, noteZen: false, hlOn: false, hlColor: "#FFE066", hlWidth: 14,
             pdf: null, pages: 0, sel: new Set(), hl: [], job: null, poll: 0, note: null, dual: false };
const TR_MAX = 3.0, TR_MIN = 0.5;

function trPanelHTML(active){
  return `<div class="tr" id="panel-notes"${active?"":' style="display:none"'}>
    <div class="tr-bar">
      <button class="btn btn-teal" id="trAdd">＋ 添加论文</button>
      <select id="trPick" class="tr-sel" title="这个项目文献库里已有的 PDF"><option value="">文献库已有…</option></select>
      <span class="tr-seg" id="trSeg">
        <button class="tr-seg-b active" id="trBtnEn" title="英文原文 PDF">English</button>
        <button class="tr-seg-b" id="trBtnZh" title="纯中文（BabelDOC 译文）">中文</button>
      </span>
      <span class="tr-spacer"></span>
      <span class="tr-grp">
        <button class="tr-ib" id="trPenTop" title="荧光笔" aria-label="荧光笔">🖍</button>
        <button class="tr-ib" id="trZenTop" title="全屏阅读（Esc 退出）" aria-label="专注模式">⛶</button>
      </span>
      <span class="tr-zoom"><button id="trOut" title="缩小" aria-label="缩小">−</button><span class="tr-zoomv" id="trZoomV">115%</span><button id="trIn" title="放大" aria-label="放大">＋</button></span>
      <span class="tr-pageno" id="trPageNo">0 / 0</span>
      <button class="btn" id="trBtnN" title="右栏切成阅读笔记">📝 笔记模式</button>
    </div>
    <div class="tr-strip" id="trStrip">
      <span class="tr-strip-t" id="trStripTitle">还没有打开论文</span>
      <span class="tr-strip-m" id="trStripMeta"></span>
      <button class="tr-strip-x hidden" id="trStripMore" title="看完整元数据">详情 ▾</button>
      <span class="tr-spacer"></span>
      <span class="tr-msg" id="trMsg"></span>
    </div>
    <div class="tr-detail hidden" id="trDetail">
      <div><b>题目：</b><span id="trTitle">暂无信息</span></div>
      <div><b>DOI：</b><span id="trDoi">暂无信息</span></div>
      <div><b>发表时间：</b><span id="trDate">暂无信息</span></div>
      <div><b>关键词：</b><span id="trKw">暂无信息</span></div>
    </div>
    <div class="tr-stage" id="trStage">
      <button class="tr-zen-exit" id="trZenExit" title="退出专注模式（Esc）">✕ 退出专注</button>
      <span class="tr-zen-zoom" id="trZenZoom"><button class="tr-ib" id="trZenOut" title="缩小" aria-label="缩小">−</button><button class="tr-ib" id="trZenIn" title="放大" aria-label="放大">＋</button><span class="tr-zoomv" id="trZenZoomV">100%</span></span>
      <div class="tr-drop" id="trDrop">
        <div class="tr-drop-in">
          <div class="tr-drop-ico">📄</div>
          <b>把 PDF 拖到这里，或点左上「＋ 添加论文」</b>
          <span>选好后自动解析 DOI / 题目 / 发表时间 / 关键词</span>
        </div>
      </div>
      <div class="tr-pane" id="trPaneA" data-pane="a"><div class="tr-scroll"></div></div>
    </div>
    <div class="tr-pen hidden" id="trPen">
      <b>颜色</b>
      <div class="tr-colors" id="trColors"></div>
      <b>粗细 <i title="荧光笔的粗细，越粗盖得越多">ⓘ</i></b>
      <input type="range" id="trWidth" min="6" max="34" value="14">
    </div>
    <input type="file" id="trFile" accept="application/pdf" hidden>
  </div>`;
}

/* ---------------- 选文件 / 拖进来 ---------------- */
async function trUpload(files){
  const f = [...(files||[])].find(x=>/\.pdf$/i.test(x.name));
  if(!f) return trMsg("只认 PDF 文件");
  if(!curTask) return trMsg("先选一个项目 —— PDF 要入当前项目的文献库");
  trMsg("上传中… " + f.name);
  try{
    const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}&task_id=${encodeURIComponent(curTask)}`,
                          {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:f});
    const a = await r.json(); if(a.error) throw new Error(a.error);
    if(!a.paper_id) return trMsg("入库失败：" + (a.ingest_error || "不是有效 PDF"));
    await trOpen(a.paper_id);
  }catch(e){ trMsg("上传失败：" + e.message); }
}
function trMsg(t){ const el=$("trMsg"); if(el) el.textContent = t || ""; }

async function trOpen(pid){
  TR.pid = pid; TR.note = null;
  trView("read");
  trSegActive("read");
  $("trPen")?.classList.add("hidden"); TR.hlOn = false;
  TR.job = null; TR.words = {}; TR.prog = 0; trStopPoll();
  trMeta();
  await trReader(pid);
  trHlLoad();
}

/* ---------------- 元数据 ---------------- */
async function trMeta(){
  const set = (id, v)=> { const el = $(id); if(el) el.textContent = (v && String(v).trim()) ? v : "暂无信息"; };
  if(!TR.pid) return;
  try{
    const d = await api(`/api/papermeta?id=${encodeURIComponent(TR.pid)}`);
    if(d.error) throw new Error(d.error);
    TR.meta = d;
    set("trDoi", d.doi); set("trTitle", d.title); set("trDate", d.published); set("trKw", d.keywords);
    $("trTitle").title = d.title || "";
    trStrip();
  }catch(e){ trMsg("元数据读取失败：" + e.message); }
}
function trStrip(){
  /* 顶部细条：题目 + 日期/DOI/关键词数；完整四项点「详情 ▾」展开 */
  const m = TR.meta || {}, t = $("trStripTitle"), c = $("trStripMeta"), more = $("trStripMore");
  if(!t) return;
  t.textContent = m.title || (TR.pid ? "（这篇没解析出题目）" : "还没有打开论文");
  t.title = m.title || "";
  const kw = String(m.keywords || "").split(/[;,、]/).filter(x => x.trim()).length;
  c.textContent = [m.published, m.doi ? "DOI " + m.doi : "", kw ? `关键词 ${kw}` : ""].filter(Boolean).join(" · ");
  more.classList.toggle("hidden", !TR.pid);
}

/* ---------------- 阅读器（pdf.js，一个 pane 一套） ---------------- */
function trWorker(){
  if(window.pdfjsLib && !window.pdfjsLib.GlobalWorkerOptions.workerSrc)
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.js";
}
function trPaneEl(key){ return $("trPaneA"); }
function trScrollEl(key){ return trPaneEl(key)?.querySelector(".tr-scroll"); }

async function trReader(pid){
  /* 单栏：English 原文 */
  if(!window.pdfjsLib){ trMsg("pdf.js 没加载"); return; }
  trWorker();
  trView("read");
  const box = trScrollEl("a"); if(box) box.innerHTML = "";
  await trMount("a", `/api/pdf?id=${encodeURIComponent(pid)}`);
}
async function trShowZh(v){
  /* 单栏：纯中文（BabelDOC 译文）—— 切过去保住读到哪 */
  if(!TR.pid) return trMsg("先添加一篇 PDF");
  if(!window.pdfjsLib){ trMsg("pdf.js 没加载"); return; }
  trWorker();
  const box = trScrollEl("a"), y = box ? box.scrollTop : 0;
  trView("zh");
  TR.monoUrl = trMonoUrl(v);
  await trMount("a", TR.monoUrl);
  if(box) box.scrollTop = y;
}
async function trMount(key, url){
  const box = trScrollEl(key); if(!box) return;
  box.innerHTML = `<div class="tr-load">正在渲染…</div>`;
  try{
    const pdf = await window.pdfjsLib.getDocument({url}).promise;
    TR.pdfs = TR.pdfs || {}; TR.pdfs[key] = pdf;
    TR.pages = pdf.numPages; $("trPick") && trFillPick();
    box.innerHTML = "";
    for(let n = 1; n <= pdf.numPages; n++){
      const page = await pdf.getPage(n);
      const vp = page.getViewport({scale: TR.scale});
      const el = document.createElement("div");
      el.className = "tr-page"; el.dataset.page = n;
      el.style.width = Math.round(vp.width) + "px";
      el.style.height = Math.round(vp.height) + "px";
      el.innerHTML = `<span class="tr-ph">第 ${n} 页</span>`;
      box.appendChild(el);
    }
    box.onscroll = ()=>{ if(!TR.raf){ TR.raf = requestAnimationFrame(()=>{ TR.raf = 0; trPaintNear(key); trPageNo(key); }); } };
    box.scrollTop = 0;
    trPaintNear(key);
    trPageNo(key);
  }catch(e){
    box.innerHTML = `<div class="tr-load err">PDF 打不开：${esc(e.message)}</div>`;
    if(/404|not found/i.test(String(e.message))) trMsg("还没有中文译文 —— 点「中文」它会先翻一遍（同一篇只翻一次）");
  }
}
function trPaintNear(key){
  /* 只画视口附近那几页（滚动时再补），不用 IntersectionObserver —— 在这个 WebView2 里它不触发实测 */
  const box = trScrollEl(key); if(!box) return;
  const top = box.scrollTop - 400, bot = box.scrollTop + box.clientHeight + 900;
  box.querySelectorAll(".tr-page").forEach(el=>{
    if(el.offsetTop + el.offsetHeight > top && el.offsetTop < bot) trPaint(key, +el.dataset.page, el);
  });
}
function trWords(key){
  /* 每个词的坐标从后端来（fitz），自己铺文字层：选中→荧光笔才有依据。
     两栏各自一份（a=原文 / b=译文），所以中文那栏也能划。 */
  TR.words = TR.words || {};
  if(TR.words[key]) return Promise.resolve(TR.words[key]);
  TR.words[key] = {};
  if(!TR.pid) return Promise.resolve({});
  const kind = key === "b" ? "mono" : "src";
  return api(`/api/pdftext?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(curTask||"")}&kind=${kind}`)
    .then(d=>{ const m = TR.words[key] = {}; (d.pages||[]).forEach(p=>{ m[p.n] = p.words || []; }); return m; })
    .catch(()=>TR.words[key]);
}
function trBuildText(key, n, tl, vp){
  const words = (TR.words || {})[key]?.[n];
  if(!words){ trWords(key).then(()=>{ if((TR.words||{})[key]?.[n]) trBuildText(key, n, tl, vp); }); return; }
  const s = vp.scale;
  tl.innerHTML = words.map(w=>`<span style="left:${(w.x*s).toFixed(1)}px;top:${(w.y*s).toFixed(1)}px;`
    + `width:${(w.w*s).toFixed(1)}px;height:${(w.h*s).toFixed(1)}px;`
    + `font-size:${(w.h*s*0.95).toFixed(1)}px;line-height:${(w.h*s).toFixed(1)}px">${esc(w.t)}</span>`).join("");
}
async function trPaint(key, n, el){
  if(el.dataset.painted === "1" && el.querySelector("canvas")) return;
  el.dataset.painted = "1";
  const pdf = (TR.pdfs||{})[key]; if(!pdf) return;
  try{
    const page = await pdf.getPage(n);
    const vp = page.getViewport({scale: TR.scale});
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cv = document.createElement("canvas");
    cv.width = Math.round(vp.width * dpr); cv.height = Math.round(vp.height * dpr);
    cv.style.width = "100%"; cv.style.height = "100%";
    el.innerHTML = "";
    el.appendChild(cv);
    await page.render({canvasContext: cv.getContext("2d"), viewport: vp, transform: [dpr,0,0,dpr,0,0]}).promise;
    const tl = document.createElement("div"); tl.className = "tr-text"; el.appendChild(tl);
    trBuildText(key, n, tl, vp);
    const hl = document.createElement("div"); hl.className = "tr-hls"; el.appendChild(hl);
    trPaintHls(n, el);
  }catch(e){ el.dataset.painted = ""; el.dataset.err = String(e && e.message || e);
             const ph = el.querySelector(".tr-ph"); if(ph) ph.textContent = "这一页渲染失败：" + el.dataset.err.slice(0, 80); }
}
function trPageNo(key){
  const box = trScrollEl(key); if(!box) return;
  const mid = box.scrollTop + box.clientHeight * 0.35;
  let best = 1;
  box.querySelectorAll(".tr-page").forEach(el=>{ if(el.offsetTop <= mid) best = +el.dataset.page; });
  if(key === (TR.active==="b"?"b":"a")) $("trPageNo").textContent = `${best} / ${TR.pages || 0}`;
  TR.cur = best;
}
function trZen(on){
  /* 专注模式：只把阅读区拉到全屏，其余（pdf.js 双栏、拖分隔线、点哪栏管哪栏）全复用现成的 */
  const st = $("trStage"); if(!st) return;
  const show = on === undefined ? !st.classList.contains("zen") : !!on;
  st.classList.toggle("zen", show);
  TR.zen = show;
  $("trZenTop")?.classList.toggle("active", show);
  trMsg(show ? "专注模式：缩放用左上角那对加减号，Esc 退出" : "");
  if(show){ trPaintNear("a"); trZoomLabel(); }
}
function trZoom(d){
  TR.scale = Math.min(TR_MAX, Math.max(TR_MIN, Math.round((TR.scale + d) * 100) / 100));
  trMsg(`缩放 ${Math.round(TR.scale*100)}%`);
  if(TR.pid) (TR.view === "zh" ? trShowZh() : trReader(TR.pid));
  trZoomLabel();
}
function trZoomLabel(){
  const t = Math.round(TR.scale * 100) + "%";
  const v = $("trZenZoomV"); if(v) v.textContent = t;      // 专注模式左上角那个
  const w = $("trZoomV");    if(w) w.textContent = t;      // 工具栏缩放组里那个
}
function trExitFull(){
  /* ✕ / Esc 的统一出口：笔记模式先退回对话，再把全屏收掉 */
  if(TR.noteMode) trExitNoteMode();
  trZen(false);
}

/* ---------------- 荧光笔 ---------------- */
const TR_COLORS = ["#FFE066", "#8BD498", "#7FC4F0", "#F0A9C0", "#F07C7C"];
async function trHlLoad(){
  /* 先问服务端（助手要用），没有再用本机那份（离线时画得出来） */
  let list = null;
  try{
    const d = await api(`/api/tr/hl?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(curTask||"")}`);
    if(Array.isArray(d.list)) list = d.list;
  }catch(_){}
  if(!list || !list.length){
    let raw = null;
    try{ raw = JSON.parse(localStorage.getItem("rw-hl-" + TR.pid) || "null"); }catch(_){ raw = null; }
    list = (raw && raw.v === 2) ? (raw.list || []) : [];
  }
  TR.hl = list || [];
  trPaintAllHls();
  trMsg(TR.hl.length ? `这份 PDF 上有 ${TR.hl.length} 处标注` : "");
}
function trPaintHls(n, el){
  const box = el.querySelector(".tr-hls"); if(!box) return;
  box.innerHTML = "";
  TR.hl.filter(h=>h.p === n).forEach((h, i)=>{
    const d = document.createElement("div");
    d.className = "tr-hl"; d.dataset.i = TR.hl.indexOf(h);
    d.style.cssText = `left:${h.x}px;top:${h.y}px;width:${h.w}px;height:${h.th}px;background:${h.c}`
      + ";position:absolute;border-radius:3px;opacity:.5;mix-blend-mode:multiply;cursor:pointer";
    d.title = "点一下删除这处标注";
    d.onclick = ()=>{ TR.hl.splice(+d.dataset.i, 1); trHlSave(); trPaintAllHls(); };
    box.appendChild(d);
  });
}
function trPaintAllHls(){
  document.querySelectorAll(".tr-page").forEach(el=>{ if(el.dataset.painted) trPaintHls(+el.dataset.page, el); });
}
function trPenPanel(on){
  const p = $("trPen"); if(!p) return;
  const show = on === undefined ? p.classList.contains("hidden") : !!on;
  p.classList.toggle("hidden", !show);
  TR.hlOn = show;
  $("trPenTop")?.classList.toggle("active", show);
  if(show) trMsg("选一段文字松开鼠标 → 就上色了；点已有标注 = 删掉它");
}
function trPenInit(){
  $("trColors").innerHTML = TR_COLORS.map((c,i)=>`<i data-c="${c}" class="${i===0?"on":""}" style="background:${c}"></i>`).join("");
  $("trColors").onclick = (e)=>{ const i = e.target.closest("i[data-c]"); if(!i) return;
    TR.hlColor = i.dataset.c;
    $("trColors").querySelectorAll("i").forEach(x=>x.classList.toggle("on", x===i)); };
  const w = $("trWidth"); w.value = TR.hlWidth;
  w.oninput = ()=>{ TR.hlWidth = +w.value; };
}
function trHlOnMouseUp(e){
  if(!TR.hlOn) return;
  const sel = window.getSelection();
  if(!sel || sel.isCollapsed) return;
  const pageEl = e.target.closest?.(".tr-page");
  if(!pageEl || !pageEl.dataset.painted) return;
  const pr = pageEl.getBoundingClientRect();
  const rects = [...sel.getRangeAt(0).getClientRects()].filter(r=>r.width > 3 && r.height > 3);
  if(!rects.length) return;
  const n = +pageEl.dataset.page;
  const th = TR.hlWidth;
  const txt = (sel.toString() || "").replace(/\s+/g, " ").trim().slice(0, 400);   // 圈的是哪几个字 → 助手要用
  rects.forEach((r, i)=>{
    TR.hl.push({p:n, x:Math.round(r.left - pr.left), y:Math.round(r.top - pr.top + r.height/2 - th/2),
                w:Math.round(r.width), th, c:TR.hlColor, t: i === 0 ? txt : ""});   // 一段话占多行时文字只记在第一行
  });
  trHlSave(); trPaintHls(n, pageEl); sel.removeAllRanges();
  trMsg(`已标注 ${TR.hl.length} 处 —— 右侧助手能看到你圈的内容，直接问就行`);
}

/* ---------------- 中英互译（BabelDOC 后台任务） ---------------- */
async function trTranslate(){
  if(!TR.pid) return trMsg("先添加一篇 PDF");
  if(TR.job && TR.job.state === "running") return;
  const b = $("trBtnZh");
  trSegActive("zh");
  if(b){ b.disabled = true; }
  trMsg("启动翻译…（同一篇只翻一次，第二次秒开）");
  try{
    const r = await api("/api/trans/start", {id: TR.pid, task_id: curTask, pages: TR.testPages || ""});
    if(r.error) throw new Error(r.error);
    TR.job = r;
    if(r.state === "done" || r.cached){ if(b) b.disabled = false; return trShowZh(r.pages_done || 0); }
    trStartPoll();
  }catch(e){ trMsg("翻译启动失败：" + e.message); if(b) b.disabled = false; }
}
function trStartPoll(){
  trStopPoll();
  const lbl = (t, off)=>{ const zb = $("trBtnZh"); if(zb){ zb.textContent = t || "中文"; zb.disabled = !!off; } };
  TR.poll = setInterval(async ()=>{
    try{
      const s = await api(`/api/trans/status?id=${encodeURIComponent(TR.pid)}`);
      TR.job = s;
      if(s.state === "running" || s.state === "partial"){
        const el = Math.round((Date.now() - (TR.t0 || (TR.t0 = Date.now()))) / 1000);
        lbl("翻译中…", true);
        const done = s.pages_done || 0;
        trMsg(`${s.msg || "正在翻译"}　已用 ${Math.floor(el/60)}分${el%60}秒（${TR.pages||"整篇"} 页大约十几分钟，可以先干别的）`);
        if(done > 0 && done !== TR.prog){          // 翻好一批就在「中文」里先看这一批
          TR.prog = done;
          trShowZh(done);
          trMsg(`已翻好第 1-${done} 页（共 ${s.total_pages||TR.pages||"?"} 页），后面还在翻…`);
        }
        if(s.state === "partial"){                 // 后面失败了：已翻好的照看
          trStopPoll(); TR.t0 = 0; lbl("中文", false);
          trMsg(`翻到第 ${done} 页时中断：${s.msg || ""}（已翻好的还在，日志：${s.log || ""}）`);
        }
      }
      if(s.state === "done"){ trStopPoll(); TR.t0 = 0; lbl("中文", false); trShowZh(s.pages_done || 0); }
      if(s.state === "error"){ trStopPoll(); TR.t0 = 0; lbl("中文", false);
                              trMsg("翻译失败：" + (s.msg||"") + (s.log ? `（日志：${s.log}）` : "")); }
    }catch(_){}
  }, 2500);
}
function trStopPoll(){ if(TR.poll){ clearInterval(TR.poll); TR.poll = 0; } }

function trMonoUrl(v){
  return `/api/trans/file?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(curTask||"")}&kind=mono&v=${v||0}`;
}
/* ---------------- 笔记模式（右栏 agent 对话框换成笔记窄栏） ---------------- */
function trNoteColumnHTML(){
  const pname = (TR.meta && TR.meta.title) ? trShort(TR.meta.title, 16) : (TR.pid || "").slice(0, 8);
  return `<div class="tr-nc" id="trNoteCol">
    <div class="tr-nc-h"><b>阅读笔记</b><span class="tr-nc-bind" id="trNoteBind">已绑论文：${esc(pname)} · 自动保存</span></div>
    <input id="trNoteTitle" class="tr-nc-title" placeholder="笔记名">
    <div class="tr-nc-hint"><span id="trNoteState"></span></div>
    <textarea id="trNoteSrc" class="tr-nc-src" spellcheck="true" placeholder="在这里随记随写…Ctrl+滚轮不改字号；点「助手模式」回到对话"></textarea>
  </div>`;
}
async function trNoteMode(){
  /* 笔记模式：右栏 agent 对话框整列换成「阅读笔记」窄栏；笔记绑论文，一篇一篇 */
  if(!TR.pid) return trMsg("先添加一篇 PDF");
  if(TR.noteMode) return trExitNoteMode();
  trMsg("正在打开这篇论文的「阅读笔记」…");
  try{
    const r = await api("/api/paper/note", {id: TR.pid, task_id: curTask});
    if(r.error) throw new Error(r.error);
    TR.note = r.note; TR.noteMode = true;
    trPaintNoteColumn();
    trMsg("「阅读笔记」已绑到这篇论文 —— 右栏随记随写、自动存；点「🤖 助手模式」或 Esc 回到对话");
  }catch(e){ trMsg("笔记打开失败：" + e.message + (/(not found|404)/i.test(e.message) ? "（服务端是旧版本，重启一次）" : "")); }
}
function trPaintNoteColumn(){
  /* 右栏渲染笔记窄栏（renderWorkflow 重画时 wfAgentPaint 也走这里） */
  const host = document.getElementById("wfAgent"); if(!host || !TR.noteMode) return;
  host.innerHTML = trNoteColumnHTML();
  $("trNoteTitle").value = (TR.note && TR.note.title) || "阅读笔记";
  $("trNoteSrc").value = (TR.note && TR.note.content) || "";
  trNoteBind();
  const b = $("trBtnN"); if(b){ b.textContent = "🤖 助手模式"; b.classList.add("active"); }
}
function trExitNoteMode(){
  TR.noteMode = false;
  const b = $("trBtnN"); if(b){ b.textContent = "📝 笔记模式"; b.classList.remove("active"); }
  if(typeof wfAgentPaint === "function"){
    const page = document.getElementById("wfPage");
    if(page) wfAgentPaint(page, window.wfStep || "notes");
  }
}
function trSegActive(v){
  /* English / 中文 两键的选中态只有这一处说了算 */
  $("trBtnEn")?.classList.toggle("active", v === "read");
  $("trBtnZh")?.classList.toggle("active", v === "zh");
}
function trView(v){
  /* 只有一个阅读栏：视图只管「有东西 / 没东西」和分段控件的显隐 */
  TR.view = v;
  $("trDrop")?.classList.toggle("hidden", v !== "empty");
  $("trSeg")?.classList.toggle("hidden", v === "empty");
}
function trShort(s, n){ s = String(s || ""); return s.length > n ? s.slice(0, n) + "…" : s; }
function trNoteBind(){
  const t = $("trNoteTitle"), s = $("trNoteSrc");
  const save = async ()=>{
    if(!TR.note) return;
    $("trNoteState").textContent = " · 保存中…";
    try{
      const d = await api("/api/mdnotes/save", {id: TR.note.id, title: t.value.trim() || "未命名笔记",
                                                content: s.value, task_id: curTask || ""});
      Object.assign(TR.note, d.note); $("trNoteState").textContent = " · 已保存";
      if(mdLoaded){ const i = mdNotes.findIndex(n=>n.id===TR.note.id); if(i >= 0) mdNotes[i] = {...mdNotes[i], ...TR.note}; else mdNotes.unshift(TR.note); }
    }catch(e){ $("trNoteState").textContent = " · 保存失败"; }
  };
  let t0 = 0;
  const later = ()=>{ clearTimeout(t0); t0 = setTimeout(save, 700); };
  t.oninput = later; s.oninput = later;
}
/* ---------------- 交互绑定 ---------------- *//* ---------------- 交互绑定 ---------------- */
async function trFillPick(){
  const sel = $("trPick"); if(!sel || !curTask) return;
  try{
    const d = await api(`/api/task?id=${encodeURIComponent(curTask)}`);
    const list = d.paper_list || [];
    sel.innerHTML = `<option value="">文献库已有…</option>` +
      list.map(p=>`<option value="${p.id}"${p.id===TR.pid?" selected":""}>${esc(p.filename)}</option>`).join("");
    if(list.length === 1 && !TR.pid) setTimeout(()=>trOpen(list[0].id), 50);   // 只有一篇就直接开
  }catch(_){}
}
function trBind(){
  const inFile = $("trFile");
  $("trAdd").onclick = ()=>inFile.click();
  inFile.onchange = (e)=>{ trUpload(e.target.files); e.target.value = ""; };
  $("trPick").onchange = (e)=>{ if(e.target.value) trOpen(e.target.value); };
  $("trIn").onclick = ()=>trZoom(+0.15);
  $("trOut").onclick = ()=>trZoom(-0.15);
  $("trPenTop").onclick = ()=>trPenPanel();
  $("trBtnEn").onclick = ()=>{ if(!TR.pid) return trMsg("先添加一篇 PDF"); trSegActive("read"); trReader(TR.pid); };
  $("trBtnZh").onclick = ()=>trTranslate();
  $("trBtnN").onclick = ()=>trNoteMode();
  $("trStripMore").onclick = ()=>$("trDetail")?.classList.toggle("hidden");
  trPenInit(); trFillPick(); trZoomLabel();
  if(!TR.hlBound){ TR.hlBound = 1;                     // 文档级监听只挂一次（renderWorkflow 会反复调 trBind）
    document.addEventListener("mouseup", (e)=>{ if($("trStage")?.contains(e.target)) trHlOnMouseUp(e); });
    document.addEventListener("keydown", (e)=>{ if(e.key === "Escape"){ trPenPanel(false); trExitFull(); } });
  }
  // 拖进来（整页都能接）
  const st = $("trStage"); let depth = 0;
  ["dragenter","dragover"].forEach(t=>st.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation();
    if(t==="dragenter") depth++; st.classList.add("dragging"); }));
  ["dragleave","drop"].forEach(t=>st.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation();
    if(t==="drop"){ depth = 0; st.classList.remove("dragging"); trUpload(e.dataTransfer?.files); }
    else if(--depth <= 0){ depth = 0; st.classList.remove("dragging"); } }));
  st.addEventListener("drop",(e)=>{ e.preventDefault(); trUpload(e.dataTransfer?.files); });
  $("trZenTop").onclick = () => trZen();
  $("trZenExit").onclick = () => trExitFull();
  $("trZenIn").onclick = () => trZoom(0.15);
  $("trZenOut").onclick = () => trZoom(-0.15);
  // 缩放只认左上角那对加减号：触屏捏合（浏览器报成 ctrl+wheel）/ Ctrl+滚轮 一律吞掉，
  // 不吞的话浏览器本体缩放会把 PDF 文字层带偏（文字层是按缩放比例铺的绝对定位）
  $("trStage").addEventListener("wheel",(e)=>{ if(e.ctrlKey) e.preventDefault(); },{passive:false});
}


/* ==== 梳理精读（第 5 步）：左 2/3 谱系图（大纲小标题 → 精读结论 / 实验数据）+ 右 1/3 结构 agent ==== */
const MP = { nodes:[], edges:[], sel:"", src:null, busy:false, poll:0, outline:"", pos:{}, zoom:1 };
const MP_COL = { section:"var(--muted)", paper:"var(--accent)", note:"#8f7fb5", data:"#4ade80" };
const MP_TAG = { section:"小节", paper:"论文", note:"笔记", data:"数据" };
const MP_W = { gapx: 84, gapy: 12, padx: 26, pady: 22 };   // 横向分层：层间距 84，同层卡片间距 12

function mapPanelHTML(active){
  const proj = (S.tasks||[]).find(t=>t.id===curTask);
  return `<section class="at" id="panel-map"${active?"":' style="display:none"'}>
    <div class="mp-grid">
      <div class="mp-left">
        <div class="mp-head">
          <b>🧩 谱系图</b><span class="ol-tag" id="mp-file">谱系.json</span>
          <span class="mp-meta" id="mp-meta"></span>
          <span class="mp-spacer"></span>
          <span class="mp-zoom"><button class="btn ghost" id="mp-zout" title="缩小">−</button><button class="btn ghost" id="mp-zin" title="放大">＋</button><button class="btn ghost" id="mp-zfit" title="适应画布">适应</button></span>
          <button class="btn ghost" id="mp-refresh" title="重新整理：按层级重排，保留你拖过的位置（按住 Shift 点 = 全部重排）">↻ 重新整理</button>
          <button class="btn ghost" id="mp-edit" title="直接改那份 JSON">✎ 手改</button>
          <button class="btn ghost hidden" id="mp-save">保存</button>
        </div>
        <div class="mp-src" id="mp-src"></div>
        <div class="mp-canvas-wrap" id="mp-wrap">
          <div class="mp-canvas" id="mp-canvas"></div>
        </div>
      </div>
      <div class="mp-right">
        <div class="at-head">
          <div class="at-heading"><span style="color:var(--accent);font-size:15px">🧩</span>
            <div><h1 id="map-title">${esc(proj ? proj.title : "梳理精读")}</h1>
            <p id="map-sub">与项目绑定 · 左边那张谱系图就是这一步的产物</p></div></div>
        </div>
        <div class="at-tabs">
          <button class="at-tab on" data-dv="map|conversation">对话</button>
          <button class="at-tab" data-dv="map|trajectory">轨迹 <span id="map-trail-n">0</span></button></div>
        <div class="at-msgs" id="map-msgs" role="log" aria-label="结构 agent 对话"></div>
        <div class="at-dock">
          <div class="at-panel">
            <div class="st-box st-counts">
              <div class="st-row"><span>对话轮数</span><b id="map-turns">0</b></div>
              <div class="st-row"><span>调用工具数</span><b id="map-calls">0</b></div>
              <div class="st-row"><span>消耗 token</span><b id="map-tok">0</b></div></div>
            <div class="st-box"><p>工具名称</p><div class="st-log" id="map-tools"></div></div>
            <div class="st-box"><p>产出目录</p><div class="st-log" id="map-outs"></div></div>
          </div>
          <div class="atts" id="map-atts"></div>
          <div class="composer" id="map-composer">
            <textarea id="map-ask" rows="1" placeholder="说「按大纲建一版谱系图」/ 让它把某篇挂到某一节下面…（Enter 发送，Shift+Enter 换行）"></textarea>
            <div class="at-tools">
              <div class="at-tools-l"><button class="icon-btn" id="map-plus" title="附加文件">＋</button>
                <label class="at-sel" title="这一步用哪个模型档案">🔌<select id="map-modpick"><option value="">未配置模型</option>
                  ${S.models.map(mm => `<option value="${mm.id}" ${S.active && mm.id === S.active.id ? "selected" : ""}>${esc(mm.name)}</option>`).join("")}</select></label></div>
              <div class="at-tools-r">
                <label class="at-sel" title="助手署名">🤖<input id="map-assist" placeholder="researcher" value="${esc(assistantKey())}"></label>
                <button class="send-btn" id="map-send" title="发送" aria-label="发送">↑</button></div>
            </div>
            <input type="file" id="map-filepick" multiple style="display:none">
          </div>
          <p class="at-hint">Enter 发送 · 说「更新谱系」→ 画布立刻换成新版（谱系.json 就地覆盖）</p>
        </div>
      </div>
    </div>
    <textarea class="ol-src hidden" id="mp-json" spellcheck="false"></textarea>
  </section>`;
}

async function mpLoad(){
  if(!curTask) return;
  try{
    let d = await api(`/api/map/state?id=${encodeURIComponent(curTask)}`);
    // 大纲已经有了、图还没有 → 直接按大纲长一版，不用等用户开口（服务端不会覆盖已有图）
    if(!(d.nodes||[]).length && /^###\s/m.test((d.outline&&d.outline.markdown)||"")){
      await api("/api/map/seed", {task_id: curTask});
      d = await api(`/api/map/state?id=${encodeURIComponent(curTask)}`);
    }
    MP.nodes = d.nodes||[]; MP.edges = d.edges||[]; MP.src = d.sources||null; MP.outline = (d.outline&&d.outline.markdown)||"";
    MP.sig = JSON.stringify([MP.nodes, MP.edges]);
    $("mp-file").textContent = (d.path||"谱系.json").split(/[\\/]/).pop();
    $("mp-meta").textContent = `${d.sections||0} 小节 · ${d.refs||0} 关联${d.exists?"":" · 还没有图"}${d.broken?" · JSON 坏了":""}`;
    mpSrcPaint(MP.src);
    mpPaint();
    mpMsgs(d.messages||[]);
  }catch(e){
    $("mp-meta").textContent = "读取失败：" + e.message;
  }
}

function mpSize(n){
  const ev = n.type !== "section";
  const hasD = !ev && (n.detail || mpOutlineNote(n.label));
  return { w: ev ? 190 : 216, h: ev ? 36 : (hasD ? 52 : 42) };
}
function mpDepth(){
  /* 层级只看「包含」这类父子边；「下一章」是同级串联，不参与分层（否则第二章会被排到第三章右边） */
  const hier = MP.edges.filter(e => !/下一章|下一节/.test(e.label || ""));
  const incoming = {}, kids = {};
  hier.forEach(e => { (incoming[e.to] = incoming[e.to] || []).push(e.from); (kids[e.from] = kids[e.from] || []).push(e.to); });
  const depth = {}, q = MP.nodes.filter(n => !incoming[n.id]).map(n => [n.id, 0]);
  while(q.length){
    const [id, d] = q.shift();
    if(depth[id] !== undefined) continue;
    depth[id] = d;
    (kids[id] || []).forEach(k => q.push([k, d + 1]));
  }
  MP.nodes.forEach(n => { if(depth[n.id] === undefined) depth[n.id] = 1; });   // 没挂上的孤点放第二列
  return depth;
}
function mpOrder(){
  /* 深度优先顺序：父下面紧跟它的子，这样每一列排下来，第二章会排在第一章的所有小节之后，
     而不是跟第一章的小节抢位置（横向思维导图就是这个观感） */
  const hier = MP.edges.filter(e => !/下一章|下一节/.test(e.label || ""));
  const kids = {}, incoming = {};
  hier.forEach(e => { (kids[e.from] = kids[e.from] || []).push(e.to); (incoming[e.to] = incoming[e.to] || []).push(e.from); });
  const seen = {}, out = [];
  const walk = (id) => { if(seen[id]) return; seen[id] = 1; out.push(id); (kids[id] || []).forEach(walk); };
  MP.nodes.filter(n => !incoming[n.id]).forEach(n => walk(n.id));
  MP.nodes.forEach(n => walk(n.id));
  return out;
}
function mpLayout(){
  /* 横向生长：根在左，子一层层往右。手动拖过的节点用它的 x/y，其余按层自动排。 */
  const depth = mpDepth(), pos = {}, order = {};
  const dfs = mpOrder();
  dfs.forEach((id, i) => { order[id] = i; });
  const byCol = {};
  MP.nodes.forEach(n => { const d = depth[n.id] || 0; (byCol[d] = byCol[d] || []).push(n); });
  let x = MP_W.padx;
  Object.keys(byCol).map(Number).sort((a, b) => a - b).forEach(d => {
    const col = byCol[d].slice().sort((a, b) => (order[a.id] ?? 0) - (order[b.id] ?? 0));
    const cw = Math.max(...col.map(n => mpSize(n).w), 0);
    let y = MP_W.pady;
    col.forEach(n => {
      const s = mpSize(n);
      pos[n.id] = { x, y, w: s.w, h: s.h, t: n.type === "section" ? "section" : "ref" };
      y += s.h + MP_W.gapy;
    });
    x += cw + MP_W.gapx;
  });
  // 父节点在自己的子节点中间垂直居中（思维导图的观感：章对齐到它那组小节的中线）
  const maxD = Math.max(0, ...Object.values(depth));
  for(let d = maxD - 1; d >= 0; d--){
    const parents = MP.nodes.filter(n => (depth[n.id] || 0) === d)
      .sort((a, b) => pos[a.id].y - pos[b.id].y);
    parents.forEach(n => {
      if(typeof n.x === "number" && typeof n.y === "number") return;      // 拖过的不动
      const kids = MP.edges.filter(e => e.from === n.id && pos[e.to]).map(e => pos[e.to]);
      if(!kids.length) return;
      const top = Math.min(...kids.map(k => k.y)), bot = Math.max(...kids.map(k => k.y + k.h));
      pos[n.id].y = Math.max(MP_W.pady, (top + bot) / 2 - pos[n.id].h / 2);
    });
  }
  MP.nodes.forEach(n => {
    if(typeof n.x === "number" && typeof n.y === "number"){ pos[n.id].x = n.x; pos[n.id].y = n.y; }
  });
  // 同一列里别互相压：拖过的保持原位，没拖过的往下让开
  Object.keys(byCol).forEach(d => {
    const col = MP.nodes.filter(n => (depth[n.id] || 0) === Number(d))
      .sort((a, b) => pos[a.id].y - pos[b.id].y);
    for(let i = 1; i < col.length; i++){
      const prev = pos[col[i-1].id], cur = pos[col[i].id], n = col[i];
      if(cur.y < prev.y + prev.h + MP_W.gapy){
        if(typeof n.x === "number" && typeof n.y === "number") continue;   // 拖过的不动
        cur.y = prev.y + prev.h + MP_W.gapy;
      }
    }
  });
  const xs = Object.values(pos).map(p => p.x + p.w), ys = Object.values(pos).map(p => p.y + p.h);
  MP.pos = pos;
  return { pos, w: Math.max(...xs, 320) + MP_W.padx, h: Math.max(...ys, 240) + MP_W.pady };
}

function mpOutlineNote(label){
  /* 方案 A：小节节点下面那行「说明」——直接取大纲里 `### 小节` 后面那句 `- 说明：…` */
  const md = MP.outline || ""; if(!md || !label) return "";
  const key = String(label).replace(/[\s　]+/g, "");
  const lines = md.split(/\r?\n/);
  for(let i=0;i<lines.length;i++){
    const h = lines[i].match(/^\s*#{2,4}\s+(.+?)\s*$/);
    if(!h || h[1].replace(/[\s　]+/g,"") !== key) continue;
    for(let j=i+1;j<Math.min(i+4,lines.length);j++){
      const t = lines[j].trim();
      if(/^#{2,4}\s/.test(t)) break;
      const m = t.match(/^[-*]?\s*说明[：:]\s*(.+)$/);
      if(m) return m[1].trim();
    }
  }
  return "";
}
function mpPaint(){
  const box = $("mp-canvas"); if(!box) return;
  if(!MP.nodes.length){
    box.style.width = ""; box.style.height = "";
    box.innerHTML = `<div class="mp-empty"><span class="big">🧩</span>
      <span class="t1">还没有谱系图</span>
      <span class="t2">右边跟 agent 说一句「按大纲建一版谱系图」，<br>它会把每个小标题挂上项目的精读结论；<br>实验数据直接拖到这块画布上。</span></div>`;
    return;
  }
  const L = mpLayout(), pos = L.pos;
  box.style.width = L.w + "px"; box.style.height = L.h + "px";
  box.style.zoom = MP.zoom === 1 ? "" : String(MP.zoom);   // Chromium 的 zoom 连带改布局尺寸，滚动条才准
  const NEG = /反例|反驳|冲突|不支持|反证|相左|相反/;
  const paths = [], labels = [];
  MP.edges.forEach(e=>{
    const a = pos[e.from], b = pos[e.to]; if(!a||!b) return;
    const x1 = a.x + a.w, y1 = a.y + a.h/2, x2 = b.x, y2 = b.y + b.h/2, mx = (x1+x2)/2;
    const on = (MP.sel === e.from || MP.sel === e.to);
    paths.push(`<path class="mp-edge${on ? " on" : ""}${NEG.test(e.label || "") ? " dash" : ""}" d="M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}"/>`);
    if(e.label) labels.push(`<text class="mp-elabel" x="${mx-16}" y="${(y1+y2)/2-6}">${esc(e.label)}</text>`);
  });
  const cards = MP.nodes.map(n=>{
    const p = pos[n.id]; if(!p) return "";
    const dot = `<span class="mp-dot" style="background:${MP_COL[n.type]||"var(--muted)"}"></span>`;
    if(n.type === "section"){
      const d = n.detail || mpOutlineNote(n.label);
      return `<div class="mp-node sec${MP.sel===n.id?" on":""}" data-mp="${esc(n.id)}"
        style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">
        <div class="t">${dot}${esc(n.label)}</div>
        ${d?`<div class="d">${esc(d)}</div>`:""}
      </div>`;
    }
    return `<div class="mp-node ref${MP.sel===n.id?" on":""}" data-mp="${esc(n.id)}"
      style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">
      <div class="t" title="${esc(n.detail||"")}">${dot}<span class="lbl">${esc(n.label)}</span></div>
      <span class="mp-pill">${MP_TAG[n.type]||""}</span>
    </div>`;
  }).join("");
  box.innerHTML = `<svg class="mp-svg" width="${L.w}" height="${L.h}">${paths.join("")}${labels.join("")}</svg>${cards}`;
  if(MP.sel) mpCard(MP.sel);
}

function mpCard(id){
  const n = MP.nodes.find(x=>x.id===id); if(!n) return;
  const old = document.querySelector(".mp-card"); if(old) old.remove();
  const kids = MP.edges.filter(e=>e.from===id).map(e=>(MP.nodes.find(x=>x.id===e.to)||{}).label).filter(Boolean);
  const par = MP.edges.filter(e=>e.to===id).map(e=>(MP.nodes.find(x=>x.id===e.from)||{}).label).filter(Boolean);
  const wrap = $("mp-wrap");
  const div = document.createElement("div");
  div.className = "mp-card";
  div.innerHTML = `<h4><span class="mp-dot" style="background:${MP_COL[n.type]||"var(--muted)"}"></span>编辑节点</h4>
    <label class="mp-f">标题<input id="mp-e-label" value="${esc(n.label||"")}"></label>
    <label class="mp-f">说明<textarea id="mp-e-detail" rows="3">${esc(n.detail||"")}</textarea></label>
    <label class="mp-f">类型<select id="mp-e-type">${Object.keys(MP_TAG).map(k=>`<option value="${k}"${n.type===k?" selected":""}>${MP_TAG[k]}</option>`).join("")}</select></label>
    ${n.ref?`<p class="mp-rel">来源：${esc(n.ref)}</p>`:""}
    ${par.length?`<p class="mp-rel">挂在小节：${esc(par.join("、"))}</p>`:""}
    ${kids.length?`<p class="mp-rel">它挂出去的：${esc(kids.join("、"))}</p>`:""}
    <div class="row"><button class="btn ghost" id="mp-cx">关闭</button>
      <button class="btn" id="mp-csave">保存</button>
      <button class="btn ghost" id="mp-cdel">删掉这个节点</button></div>`;
  wrap.appendChild(div);
  $("mp-cx").onclick = ()=>{ MP.sel=""; div.remove(); mpPaint(); };
  $("mp-csave").onclick = async ()=>{
    const label = ($("mp-e-label").value||"").trim();
    if(!label) return docMsg("map", "标题不能空着");
    n.label = label;
    n.detail = $("mp-e-detail").value.trim();
    n.type = $("mp-e-type").value;
    MP.sel = "";
    div.remove();
    await mpSave();
    docMsg("map", "节点已更新（谱系.json 已落盘）");
  };
  $("mp-cdel").onclick = async ()=>{
    MP.nodes = MP.nodes.filter(x=>x.id!==id);
    MP.edges = MP.edges.filter(e=>e.from!==id && e.to!==id);
    MP.sel = "";
    await mpSave(); docMsg("map", "已删掉这个节点（谱系.json 已更新）");
  };
}

async function mpTidy(force){
  /* 重新整理：按层级重排。默认保留你拖过的位置，只把没摆过的（含 agent 新加的）排进去；
     按住 Shift 点 = 丢掉所有手动位置，整张图重排一遍。 */
  if(force) MP.nodes.forEach(n=>{ delete n.x; delete n.y; });
  mpPaint();
  await mpSave();
  docMsg("map", force ? "已按层级全部重排" : "已重新整理，保留你拖过的位置");
}

async function mpSave(){
  try{
    const d = await api("/api/map/save", {task_id: curTask, data: {nodes: MP.nodes, edges: MP.edges}});
    if(d.error) throw new Error(d.error);
    MP.nodes = d.nodes; MP.edges = d.edges;
    $("mp-meta").textContent = `${d.sections} 小节 · ${d.refs} 关联`;
    mpPaint();
  }catch(e){ docMsg("map", "保存失败：" + e.message); }
}

function mpBind(m){
  docBind("map", m);                                  /* 右栏＝「确认大纲」那套（对话/轨迹/统计/附件），post 走 /api/map */
  $("mp-refresh").onclick = (e)=>mpTidy(!!(e && e.shiftKey));
  $("mp-zin").onclick = ()=>{ MP.zoom = Math.min(1.6, Math.round((MP.zoom + 0.1) * 10) / 10); mpPaint(); };
  $("mp-zout").onclick = ()=>{ MP.zoom = Math.max(0.5, Math.round((MP.zoom - 0.1) * 10) / 10); mpPaint(); };
  $("mp-zfit").onclick = ()=>{
    const wrap = $("mp-wrap"); if(!wrap || !MP.nodes.length) return;
    const L = mpLayout();
    MP.zoom = Math.max(0.5, Math.min(1, Math.round((wrap.clientWidth / Math.max(L.w, 1)) * 100) / 100));
    mpPaint();
  };
  $("mp-edit").onclick = ()=>{
    const ta = $("mp-json"), show = ta.classList.contains("hidden");
    ta.classList.toggle("hidden", !show);
    $("mp-save").classList.toggle("hidden", !show);
    if(show){ ta.value = JSON.stringify({nodes:MP.nodes, edges:MP.edges}, null, 1); $("mp-wrap").classList.add("hidden"); }
    else { $("mp-wrap").classList.remove("hidden"); }
  };
  $("mp-save").onclick = async ()=>{
    try{ const d = JSON.parse($("mp-json").value || "{}");
      const r = await api("/api/map/save", {task_id: curTask, data: d});
      if(r.error) throw new Error(r.error);
      MP.nodes = r.nodes; MP.edges = r.edges;
      $("mp-json").classList.add("hidden"); $("mp-save").classList.add("hidden"); $("mp-wrap").classList.remove("hidden");
      mpPaint();
    }catch(e){ docMsg("map", "JSON 有问题：" + e.message); }
  };
  // 画布：点节点 = 打开编辑卡；拖动节点 = 挪位置（松手落盘）；拖文件进来 = 上传实验数据
  const wrap = $("mp-wrap");
  let mdrag = null, dragged = false;
  wrap.addEventListener("pointerdown", (e)=>{
    const el = e.target.closest("[data-mp]"); if(!el) return;
    const p = MP.pos[el.dataset.mp]; if(!p) return;
    mdrag = { id: el.dataset.mp, el, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false };
    try{ el.setPointerCapture(e.pointerId); }catch(_){}
  });
  wrap.addEventListener("pointermove", (e)=>{
    if(!mdrag) return;
    const z = MP.zoom || 1;
    const dx = (e.clientX - mdrag.sx) / z, dy = (e.clientY - mdrag.sy) / z;
    if(!mdrag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;      // 手抖不算拖
    mdrag.moved = true;
    const n = MP.nodes.find(x=>x.id===mdrag.id); if(!n) return;
    n.x = Math.round(mdrag.ox + dx); n.y = Math.round(mdrag.oy + dy);
    mdrag.el.style.left = n.x + "px"; mdrag.el.style.top = n.y + "px";   // 拖动时只挪这块，松手再重算连线
  });
  const endDrag = async ()=>{
    if(!mdrag) return;
    const moved = mdrag.moved; mdrag = null; dragged = moved;
    if(!moved) return;
    mpPaint();
    await mpSave();
  };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  wrap.onclick = (e)=>{
    const el = e.target.closest("[data-mp]"); if(!el) return;
    if(dragged){ dragged = false; return; }                          // 刚拖完，别当成点击
    MP.sel = el.dataset.mp; mpPaint();
  };
  let depth = 0;
  ["dragenter","dragover"].forEach(t=>wrap.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation();
    if(t === "dragenter") depth++; wrap.classList.add("dragging"); }));
  ["dragleave","drop"].forEach(t=>wrap.addEventListener(t,(e)=>{ e.preventDefault(); e.stopPropagation();
    if(t === "drop"){ depth = 0; wrap.classList.remove("dragging"); mpUpload(e.dataTransfer && e.dataTransfer.files); }
    else if(--depth <= 0){ depth = 0; wrap.classList.remove("dragging"); } }));
  mpLoad();
  if(!MP.poll) MP.poll = setInterval(()=>{             /* 实时：别的窗口/agent 改了图，这里 3 秒跟上（只在这一步可见时问） */
    if(wfStep !== "map" || document.hidden) return;
    const p = $("panel-map"); if(!p || p.style.display === "none") return;
    mpReloadQuiet();
  }, 3000);
}
function mpSrcPaint(src){
  /* 能挂的源有多少：数字随时在变（精读会加笔记、上传会加数据），所以轮询每轮都重画这一段 */
  const el = $("mp-src"); if(!el) return;
  const html = src ? [
    `<span class="mp-chip">大纲小节 ${src.sections.length}</span>`,
    `<span class="mp-chip">精读笔记 ${src.notes.length}</span>`,
    `<span class="mp-chip">文献库 ${src.papers.length}</span>`,
    `<span class="mp-chip">实验数据 ${src.data.length}</span>`,
    `<span class="mp-chip">拖文件到画布 = 上传数据</span>`,
  ].join("") : "";
  if(el.dataset.sig !== html){ el.dataset.sig = html; el.innerHTML = html; }
}
async function mpReloadQuiet(){
  if(MP.busy || !curTask) return;
  try{
    const d = await api(`/api/map/state?id=${encodeURIComponent(curTask)}`);
    MP.src = d.sources || MP.src;
    mpSrcPaint(MP.src);                                  // 数字紧跟事实，不等图变
    $("mp-meta").textContent = `${d.sections||0} 小节 · ${d.refs||0} 关联${d.exists?"":" · 还没有图"}${d.broken?" · JSON 坏了":""}`;
    const sig = JSON.stringify([d.nodes, d.edges]);
    if(sig === MP.sig) return;
    MP.sig = sig; MP.nodes = d.nodes||[]; MP.edges = d.edges||[]; mpPaint();
  }catch(_){}
}
async function mpUpload(files){
  if(!files || !files.length) return;
  for(const f of files){
    try{
      const buf = await f.arrayBuffer();
      const r = await fetch(`/api/upload?filename=${encodeURIComponent(f.name)}&task=${encodeURIComponent(curTask||"")}`,
                            {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body: buf});
      const d = await r.json();
      if(d.error) throw new Error(d.error);
      docMsg("map", `📎 已上传 ${f.name} → ${d.path||""}`);
    }catch(e){ docMsg("map", "上传失败：" + e.message); }
  }
  await mpLoad();
  docMsg("map", "数据文件进来了 —— 跟 agent 说一句「把刚上传的这份挂到某一节下面」它就会画进谱系图。");
}
