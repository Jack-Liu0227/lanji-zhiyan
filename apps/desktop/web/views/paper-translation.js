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
  TR.job = null; TR.prog = 0; trStopPoll();
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
    TR.pages = pdf.numPages;
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
async function trBuildText(n, tl, page, vp){
  /* 文字层直接用 pdf.js 的 textContent：本地解析、零请求，页面画完就能划。
     旧做法是问后端 /api/pdftext（pdfplumber 抽全篇词坐标，39 页实测 6.65s），
     这 6 秒里页面上没有可选文字，荧光笔自然"不灵敏"。 */
  let tc;
  try{ tc = await page.getTextContent(); }catch(_){ return; }
  const s = vp.scale;
  tl.innerHTML = tc.items.map(it=>{
    if(!it.str || !it.str.trim()) return "";
    const m = window.pdfjsLib.Util.transform(vp.transform, it.transform);
    const h = Math.hypot(m[2], m[3]) || 10;
    return `<span style="left:${m[4].toFixed(1)}px;top:${(m[5] - h * 0.82).toFixed(1)}px;`
      + `width:${(it.width * s).toFixed(1)}px;height:${h.toFixed(1)}px;`
      + `font-size:${h.toFixed(1)}px;line-height:${h.toFixed(1)}px">${esc(it.str)}</span>`;
  }).join("");
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
    const tl = document.createElement("div"); tl.className = "tr-text"; el.appendChild(tl);
    const renderP = page.render({canvasContext: cv.getContext("2d"), viewport: vp, transform: [dpr,0,0,dpr,0,0]}).promise;
    const textP = trBuildText(n, tl, page, vp);
    await Promise.all([renderP, textP]);
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
let trHlT = 0;
function trHlSave(){
  /* 本机立刻落一份（离线也画得出来），服务端 400ms 防抖 —— 专家读的是服务端那份 */
  try{ localStorage.setItem("rw-hl-" + TR.pid, JSON.stringify({v: 2, list: TR.hl})); }catch(_){}
  clearTimeout(trHlT);
  trHlT = setTimeout(()=>{ api("/api/tr/hl", {id: TR.pid, task_id: curTask || "", list: TR.hl}).catch(()=>{}); }, 400);
}
async function trHlLoad(){
  /* 先问服务端（专家要用），没有再用本机那份（离线时画得出来） */
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
  const pageEl = e.target.closest?.(".tr-page")
    || (sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement)?.closest?.(".tr-page"))
    || (sel.focusNode && (sel.focusNode.nodeType === 1 ? sel.focusNode : sel.focusNode.parentElement)?.closest?.(".tr-page"));
  if(!pageEl || !pageEl.dataset.painted) return;
  const pr = pageEl.getBoundingClientRect();
  const rects = [...sel.getRangeAt(0).getClientRects()].filter(r=>r.width > 3 && r.height > 3)
    .sort((a, b)=> a.top - b.top || a.left - b.left);
  if(!rects.length) return;
  /* 文字层是逐词 span，划一行会给出十几个 rect —— 按行合并，一条高亮才是一整条，而不是断续小方块 */
  const rows = [];
  rects.forEach(r=>{
    const last = rows[rows.length - 1];
    if(last && Math.abs(r.top - last.top) < last.height * 0.6){
      last.left = Math.min(last.left, r.left);
      last.right = Math.max(last.right, r.right);
    } else rows.push({left: r.left, top: r.top, right: r.right, height: r.height});
  });
  const n = +pageEl.dataset.page;
  const th = TR.hlWidth;
  const txt = (sel.toString() || "").replace(/\s+/g, " ").trim().slice(0, 400);   // 圈的是哪几个字 → 专家要用
  rows.forEach((r, i)=>{
    TR.hl.push({p:n, x:Math.round(r.left - pr.left), y:Math.round(r.top - pr.top + r.height/2 - th/2),
                w:Math.round(r.right - r.left), th, c:TR.hlColor, t: i === 0 ? txt : ""});   // 一段话占多行时文字只记在第一行
  });
  trHlSave(); trPaintHls(n, pageEl); sel.removeAllRanges();
  trMsg(`已标注 ${TR.hl.length} 处 —— 右侧专家能看到你圈的内容，直接问就行`);
}

/* ---------------- 中英互译（BabelDOC 后台任务） ---------------- */
async function trTranslate(){
  if(!TR.pid) return trMsg("先添加一篇 PDF");
  if(TR.job && TR.job.state === "running") return;
  const b = $("trBtnZh");
  trSegActive("zh");
  if(b){ b.disabled = true; }
  trMsg("启动翻译…（同一篇只翻一次，第二次秒开）");
  TR.busyOff = 0;
  trBusy(true, "正在翻译中", "开始识别版式，第一篇要等十几秒…");
  try{
    const r = await api("/api/trans/start", {id: TR.pid, task_id: curTask, pages: TR.testPages || ""});
    if(r.error) throw new Error(r.error);
    TR.job = r;
    if(r.state === "done" || r.cached){ if(b) b.disabled = false; trBusy(false); trMsg("已打开中文译文（这篇翻过，直接用）"); return trShowZh(r.pages_done || 0); }
    trStartPoll();
  }catch(e){ trBusy(false); trMsg("翻译启动失败：" + e.message); if(b) b.disabled = false; }
}
function trStartPoll(){
  trStopPoll();
  const lbl = (t, off)=>{ const zb = $("trBtnZh"); if(zb){ zb.textContent = t || "中文"; zb.disabled = !!off; } };
  TR.poll = setInterval(async ()=>{
    try{
      const s = await api(`/api/trans/status?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(curTask || "")}`);
      TR.job = s;
      if(s.state === "running" || s.state === "partial"){
        const el = Math.round((Date.now() - (TR.t0 || (TR.t0 = Date.now()))) / 1000);
        lbl("翻译中…", true);
        const done = s.pages_done || 0;
        trMsg(`${s.msg || "正在翻译"}　已用 ${Math.floor(el/60)}分${el%60}秒（${TR.pages||"整篇"} 页大约十几分钟，可以先干别的）`);
        trBusy(true, "正在翻译中", `${s.msg || "正在翻译"}　已翻 ${done}/${s.total_pages || TR.pages || "?"} 页 · 已用 ${Math.floor(el/60)}分${el%60}秒`);
        if(done > 0 && done !== TR.prog){          // 翻好一批就在「中文」里先看这一批
          TR.prog = done;
          trShowZh(done);
          trMsg(`已翻好第 1-${done} 页（共 ${s.total_pages||TR.pages||"?"} 页），后面还在翻…`);
        }
        if(s.state === "partial"){                 // 后面失败了：已翻好的照看
          trStopPoll(); TR.t0 = 0; lbl("中文", false);
          trMsg(`翻到第 ${done} 页时中断：${s.msg || ""}（已翻好的还在，日志：${s.log || ""}）`);
          trBusy(true, "翻译中断", `翻到第 ${done} 页时失败：${s.msg || ""}` + (s.log ? `\n日志：${s.log}` : ""), true);
        }
      }
      if(s.state === "done"){ trStopPoll(); TR.t0 = 0; lbl("中文", false); trBusy(false); trShowZh(s.pages_done || 0); }
      if(s.state === "error"){ trStopPoll(); TR.t0 = 0; lbl("中文", false);
                              trMsg("翻译失败：" + (s.msg||"") + (s.log ? `（日志：${s.log}）` : ""));
                              trBusy(true, "翻译失败", (s.msg || "") + (s.log ? `\n日志：${s.log}` : ""), true); }
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
    <textarea id="trNoteSrc" class="tr-nc-src" spellcheck="true" placeholder="在这里随记随写…Ctrl+滚轮不改字号；点「专家模式」回到对话"></textarea>
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
    trMsg("「阅读笔记」已绑到这篇论文 —— 右栏随记随写、自动存；点「🤖 专家模式」或 Esc 回到对话");
  }catch(e){ trMsg("笔记打开失败：" + e.message + (/(not found|404)/i.test(e.message) ? "（服务端是旧版本，重启一次）" : "")); }
}
function trPaintNoteColumn(){
  /* 右栏渲染笔记窄栏（renderWorkflow 重画时 wfAgentPaint 也走这里） */
  const host = document.getElementById("wfAgent"); if(!host || !TR.noteMode) return;
  host.innerHTML = trNoteColumnHTML();
  $("trNoteTitle").value = (TR.note && TR.note.title) || "阅读笔记";
  $("trNoteSrc").value = (TR.note && TR.note.content) || "";
  trNoteBind();
  const b = $("trBtnN"); if(b){ b.textContent = "🤖 专家模式"; b.classList.add("active"); }
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

/* ---------------- 左上 ＋：直接进这个项目的文献库挑一篇 ---------------- */
async function trOpenPicker(){
  if(!curTask) return trMsg("先选一个项目 —— 论文在项目的文献库里");
  const d = document.createElement("dialog");
  d.className = "modal";
  d.innerHTML = `
    <div class="modal-mask"></div>
    <div class="modal-dialog" style="width:540px">
      <div class="modal-head"><h3>项目文献库</h3><button class="modal-close" data-x>✕</button></div>
      <div class="modal-body"><p class="modal-text">读取文献库…</p></div>
      <div class="modal-foot">
        <span class="tr-phint" data-n></span>
        <button class="btn ghost" data-up>＋ 添加 PDF…</button>
      </div>
    </div>`;
  const close = ()=>{ d.close(); d.remove(); };
  d.querySelector("[data-x]").onclick = close;
  d.querySelector(".modal-mask").onclick = close;
  d.querySelector("[data-up]").onclick = ()=>{ close(); $("trFile").click(); };
  document.body.appendChild(d); d.showModal();      // 先把框立起来，列表慢也不会像没反应
  let list = [], err = "";
  try{ list = (await api(`/api/task?id=${encodeURIComponent(curTask)}`)).paper_list || []; }
  catch(e){ err = e.message; }
  if(!d.isConnected) return;                        // 等的时候已经被关掉了
  const rows = list.map(p=>`
    <button class="tr-prow${p.id === TR.pid ? " on" : ""}" data-id="${esc(p.id)}" title="${esc(p.filename)}">
      <span class="tr-pn">${esc(p.filename)}</span>
      <span class="tr-pm">${p.pages ? p.pages + " 页" : ""}</span>
    </button>`).join("");
  d.querySelector(".modal-body").innerHTML = err ? `<p class="modal-text">读文献库失败：${esc(err)}</p>`
    : rows ? `<div class="tr-plist">${rows}</div>`
           : `<p class="modal-text">这个项目的文献库还是空的 —— 点下面「添加 PDF」，选好自动入库。</p>`;
  d.querySelector("[data-n]").textContent = `共 ${list.length} 篇`;
  d.querySelectorAll("[data-id]").forEach(b => b.onclick = ()=>{ close(); trOpen(b.dataset.id); });
}

/* ---------------- 翻译中：屏幕正中的一层（翻完自己消失） ---------------- */
function trBusy(show, title, sub, closable){
  let d = $("trBusy");
  if(!show){ if(d){ d.close(); d.remove(); } return; }
  if(!closable && TR.busyOff) return;                 // 点过「后台运行」就不再往屏幕上弹
  if(!d){
    d = document.createElement("dialog");
    d.className = "modal"; d.id = "trBusy";
    d.innerHTML = `<div class="modal-mask tr-mask-soft"></div>
      <div class="modal-dialog tr-busy-card" style="width:340px">
        <div class="tr-busy"><span class="tr-ring" id="trBusyRing"></span><b id="trBusyT">正在翻译中</b></div>
        <div class="tr-busy-s" id="trBusyS"></div>
        <button class="btn" id="trBusyX">后台运行</button>
      </div>`;
    document.body.appendChild(d); d.showModal();
  }
  $("trBusyT").textContent = title || "正在翻译中";
  $("trBusyS").textContent = sub || "";
  $("trBusyRing").classList.toggle("hidden", !!closable);      // 失败态：不转了
  $("trBusyX").textContent = closable ? "知道了" : "后台运行";
  $("trBusyX").onclick = ()=>{ if(!closable) TR.busyOff = 1; trBusy(false); };
}

function trBind(){
  const inFile = $("trFile");
  $("trAdd").onclick = trOpenPicker;                  // ＋ → 项目文献库（不再弹系统文件框）
  inFile.onchange = (e)=>{ trUpload(e.target.files); e.target.value = ""; };
  $("trIn").onclick = ()=>trZoom(+0.15);
  $("trOut").onclick = ()=>trZoom(-0.15);
  $("trPenTop").onclick = ()=>trPenPanel();
  $("trBtnEn").onclick = ()=>{ if(!TR.pid) return trMsg("先添加一篇 PDF"); trSegActive("read"); trReader(TR.pid); };
  $("trBtnZh").onclick = ()=>trTranslate();
  $("trBtnN").onclick = ()=>trNoteMode();
  $("trStripMore").onclick = ()=>$("trDetail")?.classList.toggle("hidden");
  trPenInit(); trZoomLabel();
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


/* ==== 证据链谱系（工作流第 6 步）：左 2/3 证据链谱系图（框架小标题 → 精读结论 / 实验数据）+ 右 1/3 证据链 agent ==== */
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
          <b>🧩 证据链谱系图</b><span class="ol-tag" id="mp-file">谱系.json</span>
          <span class="mp-meta" id="mp-meta"></span>
          <span class="mp-spacer"></span>
          <div class="mp-filter-group" id="mpFilter" style="display:inline-flex;gap:4px;background:var(--panel2);padding:2px;border-radius:6px;border:1px solid var(--line)">
            <button class="btn xs ${(!MP.filter||MP.filter==='all')?'on':''}" data-mp-filter="all">全部</button>
            <button class="btn xs ${(MP.filter==='lit')?'on':''}" data-mp-filter="lit">📑 文献/笔记</button>
            <button class="btn xs ${(MP.filter==='data')?'on':''}" data-mp-filter="data">📊 自研数据</button>
          </div>
          <button class="btn ghost primary" id="mp-add-node" title="手动添加新节点（自研数据/文献引用/小节）">＋ 加节点</button>
          <span class="mp-zoom"><button class="btn ghost" id="mp-zout" title="缩小">−</button><button class="btn ghost" id="mp-zin" title="放大">＋</button><button class="btn ghost" id="mp-zfit" title="适应画布">适应</button></span>
          <button class="btn ghost" id="mp-refresh" title="重新整理：按大纲当前的顺序重认框架（调换 2.1/1.1、改标题、加减小节之后就点它；手动拖过的位置会一并重排）">↻ 重新整理</button>
          <button class="btn ghost" id="mp-edit" title="直接改那份 JSON">✎ 手改</button>
          <button class="btn ghost hidden" id="mp-save">保存</button>
        </div>
        <div class="mp-src" id="mp-src"></div>
        <div class="mp-canvas-wrap" id="mp-wrap">
          <div class="mp-canvas" id="mp-canvas"></div>
        </div>
        <textarea class="ol-src hidden" id="mp-json" spellcheck="false"></textarea>
      </div>
      <div class="mp-right">
        <div class="at-head">
          <div class="at-heading"><span style="color:var(--accent);font-size:15px">🧩</span>
            <div><h1 id="map-title">${esc(proj ? proj.title : "证据链谱系")}</h1>
            <p id="map-sub">与项目绑定 · 左边那张证据链谱系图就是这一步的产物</p></div></div>
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
            <textarea id="map-ask" rows="1" placeholder="说「按框架建一版证据链谱系图」/ 让它把某篇挂到某一节下面…（Enter 发送，Shift+Enter 换行）"></textarea>
            <div class="at-tools">
              <div class="at-tools-l"><button class="icon-btn" id="map-plus" title="附加文件">＋</button>
                <label class="at-sel" title="这一步用哪个模型档案">🔌<select id="map-modpick"><option value="">未配置模型</option>
                  ${S.models.map(mm => `<option value="${mm.id}" ${S.active && mm.id === S.active.id ? "selected" : ""}>${esc(mm.name)}</option>`).join("")}</select></label></div>
              <div class="at-tools-r">
                <label class="at-sel" title="专家署名">🤖<input id="map-assist" placeholder="researcher" value="${esc(assistantKey())}"></label>
                <button class="send-btn" id="map-send" title="发送" aria-label="发送">↑</button></div>
            </div>
            <input type="file" id="map-filepick" multiple style="display:none">
          </div>
          <p class="at-hint">Enter 发送 · 说「更新谱系」→ 画布立刻换成新版（谱系.json 就地覆盖）</p>
        </div>
      </div>
    </div>
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
    MP_MEAS.clear();                                 // 大纲/说明变了，卡片尺寸得重新量
    MP.sig = JSON.stringify([MP.nodes, MP.edges]);
    $("mp-file").textContent = (d.path||"谱系.json").split(/[\\/]/).pop();
    $("mp-meta").textContent = `${d.sections||0} 小节 · ${d.refs||0} 关联${d.exists?"":" · 还没有图"}${d.broken?" · JSON 坏了":""}`;
    mpSrcPaint(MP.src);
    mpPaint();
    docRestore("map", d.messages, d.out);
  }catch(e){
    $("mp-meta").textContent = "读取失败：" + e.message;
  }
}

let mpGauge = null;                                  // 量字用的隐藏盒（挂在 #mp-wrap 上，不会被重绘冲掉）
const MP_MEAS = new Map();                            // type|label|detail → {w,h}
function mpNodeInner(n){
  const dot = `<span class="mp-dot" style="background:${MP_COL[n.type]||"var(--muted)"}"></span>`;
  const plus = `<button class="mp-add" data-add="${esc(n.id)}" title="在它后面加一个节点（Enter 落盘）">＋</button>`;
  if(n.type === "section"){
    const d = n.detail || mpOutlineNote(n.label);
    return `<div class="t">${dot}<span class="lbl">${esc(n.label)}</span>${plus}</div>${d?`<div class="d">${esc(d)}</div>`:""}`;
  }
  const isData = n.type === "data" || n.mine || (n.ref && String(n.ref).includes("data"));
  const tagBadge = isData
    ? `<span class="mp-pill" style="background:#4ade8022;color:#4ade80;border:1px solid #4ade8055">📊 自研数据</span>`
    : (n.type === "paper" ? `<span class="mp-pill" style="background:#60a5fa22;color:#60a5fa;border:1px solid #60a5fa55">📑 文献证据</span>`
    : `<span class="mp-pill">${MP_TAG[n.type]||"笔记"}</span>`);
  return `<div class="t" title="${esc(n.label||"")}">${dot}<span class="lbl">${esc(n.label)}</span>${plus}</div>
    ${n.detail?`<div class="d">${esc(n.detail)}</div>`:""}
    ${tagBadge}`;
}
function mpSize(n){
  /* 卡片大小按内容真量（写死尺寸会把长标题折掉）：宽度 = 文字自然宽度夹到上限，高度 = 真排出来的行高 */
  const detail = n.type === "section" ? (n.detail || mpOutlineNote(n.label)) : (n.detail || "");
  const key = `${n.type}\u0001${n.label}\u0001${detail}`;
  const hit = MP_MEAS.get(key); if(hit) return hit;
  const host = $("mp-wrap");
  if(!mpGauge || !mpGauge.isConnected){
    mpGauge = document.createElement("div");
    mpGauge.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;pointer-events:none";
    (host || document.body).appendChild(mpGauge);
  }
  const maxW = n.type === "section" ? 320 : 340, minW = n.type === "section" ? 216 : 190;
  mpGauge.innerHTML = `<div class="mp-node ${n.type==="section"?"sec":"ref"}"
    style="position:static;width:max-content;max-width:${maxW}px;height:auto">${mpNodeInner(n)}</div>`;
  const card = mpGauge.firstElementChild;
  const s = { w: Math.max(minW, Math.ceil(card.offsetWidth)), h: Math.max(34, Math.ceil(card.offsetHeight)) };
  mpGauge.innerHTML = "";
  MP_MEAS.set(key, s);
  return s;
}
function mpHier(){
  /* 只看「包含」这类父子边；「下一章」是同级串联，不参与分层、不参与居中 —— 
     以前居中那一步把「下一章」也当子节点，章 2 的中线被章 3 的位置拉偏，于是 6 跑到 2 和 5 中间。 */
  return MP.edges.filter(e => !/下一章|下一节/.test(e.label || ""));
}
function mpDepth(){
  const hier = mpHier();
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
  const hier = mpHier();
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
  const hier = mpHier();
  for(let d = maxD - 1; d >= 0; d--){
    const parents = MP.nodes.filter(n => (depth[n.id] || 0) === d)
      .sort((a, b) => pos[a.id].y - pos[b.id].y);
    parents.forEach(n => {
      if(typeof n.x === "number" && typeof n.y === "number") return;      // 拖过的不动
      const kids = hier.filter(e => e.from === n.id && pos[e.to]).map(e => pos[e.to]);
      if(!kids.length) return;
      const top = Math.min(...kids.map(k => k.y)), bot = Math.max(...kids.map(k => k.y + k.h));
      pos[n.id].y = Math.max(MP_W.pady, (top + bot) / 2 - pos[n.id].h / 2);
    });
  }
  MP.nodes.forEach(n => {
    if(typeof n.x === "number" && typeof n.y === "number"){ pos[n.id].x = n.x; pos[n.id].y = n.y; }
  });
  // 同一列里按框架顺序别互相压：居中之后可能被拉高，这里按顺序往下让开 —— 序号顺序永远不被居中打乱
  Object.keys(byCol).forEach(d => {
    const col = MP.nodes.filter(n => (depth[n.id] || 0) === Number(d))
      .sort((a, b) => (order[a.id] ?? 0) - (order[b.id] ?? 0));
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
      <span class="t1">还没有证据链谱系图</span>
      <span class="t2">右边跟 agent 说一句「按框架建一版证据链谱系图」，<br>它会把每个小标题挂上项目的精读结论与实验数据；<br>实验数据直接拖到这块画布上。</span></div>`;
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
    return `<div class="mp-node ${n.type==="section"?"sec":"ref"}${MP.sel===n.id?" on":""}" data-mp="${esc(n.id)}"
      style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">${mpNodeInner(n)}</div>`;
  }).join("");
  box.innerHTML = `<svg class="mp-svg" width="${L.w}" height="${L.h}">${paths.join("")}${labels.join("")}</svg>${cards}`;
  if(MP.sel) mpCard(MP.sel);
}

function mpPickHTML(){
  /* 能挂的东西来自 /api/map/state 的 sources（精读笔记 / 实验数据 / 文献库） */
  const src = MP.src || {};
  const grp = (name, rows) => rows.length
    ? `<optgroup label="${name}">${rows.map(([v,t])=>`<option value="${esc(v)}">${esc(t)}</option>`).join("")}</optgroup>` : "";
  return grp("精读笔记", (src.notes||[]).map(x=>[`note|${x.id}`, x.title||x.name]))
       + grp("实验数据", (src.data||[]).map(x=>[`data|${x}`, String(x).split(/[\\/]/).pop()]))
       + grp("文献库", (src.papers||[]).map(x=>[`paper|${x}`, String(x).replace(/\.pdf$/i,"")]));
}
function mpCard(id){
  const n = MP.nodes.find(x=>x.id===id); if(!n) return;
  const old = document.querySelector(".mp-card"); if(old) old.remove();
  const lab = (x)=>(MP.nodes.find(k=>k.id===x)||{}).label||x;
  const kidEdges = MP.edges.filter(e=>e.from===id);
  const secNodes = MP.nodes.filter(x=>x.type==="section");
  const secIds = new Set(secNodes.map(x=>x.id));
  const secPar = MP.edges.filter(e=>e.to===id && secIds.has(e.from)).map(e=>e.from);
  const wrap = $("mp-wrap");
  const div = document.createElement("div");
  div.className = "mp-card";
  div.innerHTML = `<h4><span class="mp-dot" style="background:${MP_COL[n.type]||"var(--muted)"}"></span>编辑节点</h4>
    <label class="mp-f">标题<input id="mp-e-label" value="${esc(n.label||"")}"></label>
    <label class="mp-f">说明<textarea id="mp-e-detail" rows="3">${esc(n.detail||"")}</textarea></label>
    <label class="mp-f">类型<select id="mp-e-type">${Object.keys(MP_TAG).map(k=>`<option value="${k}"${n.type===k?" selected":""}>${MP_TAG[k]}</option>`).join("")}</select></label>
    ${n.type==="section"?`<label class="mp-f">挂一条资料<select id="mp-e-add"><option value="">— 精读笔记 / 实验数据 / 文献库 —</option>${mpPickHTML()}</select></label>`:""}
    ${kidEdges.length?`<div class="mp-rel">挂出去的：${kidEdges.map(e=>`<button class="mp-unl" data-un="${esc(e.to)}" title="从这一节上摘掉">${esc(lab(e.to))} ✕</button>`).join("")}</div>`:""}
    ${n.ref?`<p class="mp-rel">来源：${esc(n.ref)}</p>`:""}
    ${n.type==="section"?"":`<label class="mp-f">挂在小节<select id="mp-e-par">
      <option value=""${secPar.length?"":" selected"}>— 没挂任何小节 —</option>
      ${secNodes.map(s=>`<option value="${esc(s.id)}"${secPar[0]===s.id?" selected":""}>${esc(s.label)}</option>`).join("")}
    </select></label>`}
    <div class="row"><button class="btn ghost" id="mp-cx">关闭</button>
      <button class="btn" id="mp-csave">保存</button>
      <button class="btn ghost" id="mp-cdel">删掉这个节点</button></div>`;
  wrap.appendChild(div);
  const add = $("mp-e-add");
  if(add) add.onchange = async ()=>{
    const v = add.value; if(!v) return;
    const i = v.indexOf("|");
    const kind = v.slice(0, i);
    let ref = v.slice(i + 1);
    const src = MP.src || {};
    let label = ref.split(/[\\/]/).pop().replace(/\.[a-z0-9]+$/i, "");
    if(kind === "note"){
      const o = (src.notes||[]).find(x=>x.id===ref);
      if(o){ label = o.title || o.name || label; ref = o.name || ref; }
    }
    let nid = "r1"; while(MP.nodes.some(x=>x.id===nid)) nid = "r" + (Number(nid.slice(1)) + 1);
    MP.nodes.push({id:nid, type:kind === "paper" ? "paper" : kind, label, detail:"", ref});
    MP.edges.push({from:id, to:nid, label:"支撑"});
    add.value = "";
    await mpSave();
    mpSay(`已把「${label}」挂到这一节（谱系.json 已更新）`);
  };
  const parSel = $("mp-e-par");
  if(parSel) parSel.onchange = async ()=>{
    const sid = parSel.value;
    const name = (MP.nodes.find(x=>x.id===sid)||{}).label;
    MP.sel = ""; div.remove();
    await mpReparent(id, sid);
    mpSay(sid ? `已把「${n.label}」挪到「${name}」下面（谱系.json 已更新）`
              : `已把「${n.label}」从所有小节上摘下来，节点还留着（谱系.json 已更新）`);
  };
  div.querySelectorAll("[data-un]").forEach(b=>{
    b.onclick = async ()=>{
      const cid = b.dataset.un;
      MP.edges = MP.edges.filter(e=>!(e.from===id && e.to===cid));
      if(!MP.edges.some(e=>e.to===cid)) MP.nodes = MP.nodes.filter(x=>x.id!==cid);   // 没别处挂着就一起清掉
      await mpSave(); mpSay("已摘掉这条关联（谱系.json 已更新）");
    };
  });
  $("mp-cx").onclick = ()=>{ MP.sel=""; div.remove(); mpPaint(); };
  $("mp-csave").onclick = async ()=>{
    const label = ($("mp-e-label").value||"").trim();
    if(!label) return mpSay("标题不能空着");
    n.label = label;
    n.detail = $("mp-e-detail").value.trim();
    n.type = $("mp-e-type").value;
    MP.sel = "";
    div.remove();
    await mpSave();
    mpSay("节点已更新（谱系.json 已落盘）");
  };
  $("mp-cdel").onclick = async ()=>{
    MP.nodes = MP.nodes.filter(x=>x.id!==id);
    MP.edges = MP.edges.filter(e=>e.from!==id && e.to!==id);
    MP.sel = "";
    await mpSave(); mpSay("已删掉这个节点（谱系.json 已更新）");
  };
}
function mpNew(pid){
  /* 节点尾部那个 ＋：就地建一个手打标题的节点，挂在它下面（包含）。落盘走同一个 mpSave，后端不用改。
     ponytail: 只能「加在某个节点后面」，插到前面就先加再拖；要改类型/说明点开编辑卡。 */
  const n = MP.nodes.find(x=>x.id===pid); if(!n) return;
  document.querySelectorAll(".mp-card").forEach(x=>x.remove());
  MP.sel = "";
  const div = document.createElement("div");
  div.className = "mp-card";
  div.innerHTML = `<h4>在「${esc(n.label)}」后面加一个节点</h4>
    <label class="mp-f">标题<input id="mp-n-label" placeholder="Enter 加进去 · Esc 取消"></label>
    <label class="mp-f">类型<select id="mp-n-type">${Object.keys(MP_TAG).map(k=>`<option value="${k}"${k==="note"?" selected":""}>${MP_TAG[k]}</option>`).join("")}</select></label>
    <div class="row"><button class="btn ghost" id="mp-nx">取消</button><button class="btn" id="mp-nok">加进去</button></div>`;
  $("mp-wrap").appendChild(div);
  const inp = $("mp-n-label");
  inp.focus();
  const ok = async ()=>{
    const label = (inp.value||"").trim();
    if(!label){ inp.focus(); return; }
    let nid = "r1"; while(MP.nodes.some(x=>x.id===nid)) nid = "r" + (Number(nid.slice(1)) + 1);
    MP.nodes.push({id:nid, type:$("mp-n-type").value, label, detail:"", ref:"", mine:true});
    MP.edges.push({from:pid, to:nid, label:"包含"});
    div.remove();
    await mpSave();
    mpSay(`已在「${n.label}」后面加了「${label}」（谱系.json 已更新）`);
  };
  inp.onkeydown = (e)=>{
    if(e.key === "Enter"){ e.preventDefault(); ok(); }
    else if(e.key === "Escape") div.remove();
  };
  $("mp-nok").onclick = ok;
  $("mp-nx").onclick = ()=>div.remove();
}
async function mpReparent(cid, sid){
  /* 把一条资料挪到 sid 那个小节下面：同一份资料只留一个小节父边，边标签沿用原来的（支撑（背景）这类不丢）。
     sid 为空 = 从所有小节上摘下来，节点留着。手拖过的坐标清掉，跟着新父自动重排，不留在鼠标松手的地方。 */
  const secIds = new Set(MP.nodes.filter(x=>x.type==="section").map(x=>x.id));
  const oldEdge = MP.edges.find(e=>e.to===cid && secIds.has(e.from));
  const label = (oldEdge && oldEdge.label) || "支撑";
  MP.edges = MP.edges.filter(e=>!(e.to===cid && secIds.has(e.from)));
  if(sid) MP.edges.push({from:sid, to:cid, label});
  const n = MP.nodes.find(x=>x.id===cid);
  if(n){ delete n.x; delete n.y; }
  await mpSave();
}

function mpSay(t){
  /* 谱系图这步没有 map-meta 元素（右栏 meta 是 mp-meta 计数）：整理/挂资料的结果直接说进右栏对话，
     写到别处就看不见，点了按钮像没反应 */
  const st = DOCS["map"]; if(!st || !t) return;
  st.msgs.push({ role: "assistant", content: t, events: [] });
  docPaint("map");
}

async function mpTidy(){
  /* 重新整理 = 按大纲**当前的**顺序重认一遍框架。结构性重排交给服务端（标题认身份、挂着的资料跟着走、
     手动坐标全清）；「哪条精读笔记挂哪一节」要读内容判断，交给右边谱系专家。 */
  try{
    const d = await api("/api/map/tidy", {task_id: curTask});
    if(d.error) throw new Error(d.error);
    MP.nodes = d.nodes || []; MP.edges = d.edges || []; MP.sel = ""; MP_MEAS.clear();
    MP.sig = JSON.stringify([MP.nodes, MP.edges]);
    $("mp-meta").textContent = `${d.sections||0} 小节 · ${d.refs||0} 关联`;
    mpPaint();
    (d.changed||[]).forEach(t=>mpSay("↻ " + t));
    if(!(d.changed||[]).length) mpSay(`跟大纲对过了（${d.sections||0} 节没变），位置已按层级重排`);
    const miss = d.unattached || [];
    if(miss.length && hasModelKey()){
      $("map-ask").value = "框架刚按大纲重新整理过。请把这些还没挂上的精读笔记按标题挂到对应小节下面："
        + "不要读附件、不要检索，判断完直接一次 map_write 写完整份谱系图；标题看不出归属的先别挂，最后列出来问我：\n"
        + miss.slice(0, 20).map(x=>"- " + x).join("\n");
      await docSend("map");
    }else if(miss.length){
      mpSay(`还有 ${miss.length} 条精读笔记没挂上 —— 点小节右上角 ＋ 手动挂，或跟我说「把精读笔记挂到各节下面」`);
    }
  }catch(e){
    mpSay("重新整理失败：" + e.message);
  }
}

async function mpSave(){
  try{
    const d = await api("/api/map/save", {task_id: curTask, data: {nodes: MP.nodes, edges: MP.edges}});
    if(d.error) throw new Error(d.error);
    MP.nodes = d.nodes; MP.edges = d.edges;
    $("mp-meta").textContent = `${d.sections} 小节 · ${d.refs} 关联`;
    mpPaint();
  }catch(e){ mpSay("保存失败：" + e.message); }
}

function mpBind(m){
  docBind("map", m);                                  /* 右栏＝「确认大纲」那套（对话/轨迹/统计/附件），post 走 /api/map */
  m.querySelectorAll("[data-mp-filter]").forEach(b=>{
    b.onclick = ()=>{
      MP.filter = b.dataset.mpFilter;
      m.querySelectorAll("[data-mp-filter]").forEach(x=>x.classList.toggle("on", x.dataset.mpFilter === MP.filter));
      mpPaint();
    };
  });
  const addBtn = $("mp-add-node");
  if(addBtn){
    addBtn.onclick = ()=>{
      showAddNodeModal();
    };
  }
  $("mp-refresh").onclick = ()=>mpTidy();
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
    }catch(e){ mpSay("JSON 有问题：" + e.message); }
  };
  // 画布：点节点 = 打开编辑卡；拖动节点 = 挪位置（松手落盘）；拖文件进来 = 上传实验数据
  const wrap = $("mp-wrap");
  let mdrag = null, dragged = false;
  /* 落点命中哪张小节卡：拖资料压在小节卡片上 = 挂到它下面；压不上就当普通挪位置。
     用 getBoundingClientRect 而不是 elementFromPoint —— 拖动中的卡片就在光标底下，会把命中挡掉。 */
  const mpDropTarget = (cx, cy, skip)=>{
    let hit = null;
    document.querySelectorAll("#mp-wrap .mp-node.sec").forEach(el=>{
      if(el.dataset.mp === skip) return;
      const r = el.getBoundingClientRect();
      if(cx >= r.left && cx <= r.right && cy >= r.top && cy <= r.bottom) hit = el;
    });
    return hit;
  };
  wrap.addEventListener("pointerdown", (e)=>{
    if(e.target.closest("[data-add]")) return;                 // 点的是 ＋：别当成拖节点
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
    mdrag.el.classList.add("mp-dragging");                            // 半透明：看得见底下那张小节卡
    const n = MP.nodes.find(x=>x.id===mdrag.id); if(!n) return;
    n.x = Math.round(mdrag.ox + dx); n.y = Math.round(mdrag.oy + dy);
    mdrag.el.style.left = n.x + "px"; mdrag.el.style.top = n.y + "px";   // 拖动时只挪这块，松手再重算连线
    const t = n.type === "section" ? null : mpDropTarget(e.clientX, e.clientY, mdrag.id);
    wrap.querySelectorAll(".mp-node.drop").forEach(x=>x.classList.remove("drop"));
    if(t) t.classList.add("drop");                                    // 松手就挂到它下面
  });
  const endDrag = async (e)=>{
    if(!mdrag) return;
    const { id, el, moved } = mdrag; mdrag = null; dragged = moved;
    el.classList.remove("mp-dragging");
    wrap.querySelectorAll(".mp-node.drop").forEach(x=>x.classList.remove("drop"));
    if(!moved) return;
    const me = MP.nodes.find(n=>n.id===id);
    const tgt = (e && e.type === "pointerup" && me && me.type !== "section")
      ? mpDropTarget(e.clientX, e.clientY, id) : null;
    if(tgt){
      const tid = tgt.dataset.mp;
      await mpReparent(id, tid);
      mpSay(`已把「${me.label}」挂到「${(MP.nodes.find(n=>n.id===tid)||{}).label}」下面（谱系.json 已更新）`);
      return;
    }
    mpPaint();
    await mpSave();
  };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  wrap.onclick = (e)=>{
    const ab = e.target.closest("[data-add]");
    if(ab){ dragged = false; mpNew(ab.dataset.add); return; }        // 节点尾部 ＋ = 在后面加一个节点
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
      mpSay(`📎 已上传 ${f.name} → ${d.path||""}`);
    }catch(e){ mpSay("上传失败：" + e.message); }
  }
  await mpLoad();
  mpSay("数据文件进来了 —— 跟 agent 说一句「把刚上传的这份挂到某一节下面」它就会画进证据链谱系图。");
}

function showAddNodeModal(){
  const secNodes = (MP.nodes||[]).filter(n=>n.type==="section");
  const modal = document.createElement("div");
  modal.className = "modal";
  modal.innerHTML = `
    <div class="modal-mask"></div>
    <div class="modal-dialog" style="width:480px">
      <div class="modal-head"><h3>＋ 添加证据链节点</h3><button class="modal-close" id="btnMpClose">✕</button></div>
      <div class="modal-body">
        <label class="field"><span>节点分类</span>
          <select id="mpNodeType" class="control" style="width:100%">
            <option value="data">📊 我的自研数据 (实验数据 / 模拟仿真 / 实测结果)</option>
            <option value="paper">📑 学术文献 (论文证据 / 对比基准)</option>
            <option value="note">📝 精读笔记 (理论推导 / 机理解析)</option>
            <option value="section">🗂 大纲小节 (章节标题)</option>
          </select>
        </label>
        <label class="field"><span>节点标题 / 标签</span>
          <input id="mpNodeLabel" class="control" style="width:100%" placeholder="如：Cu 掺杂暗电流降解实测 (样件#3)">
        </label>
        <label class="field"><span>结论说明 / 核心要点</span>
          <textarea id="mpNodeDetail" class="control" rows="3" style="width:100%" placeholder="简要说明支撑或反例结论，如：在 200V 反偏下漏电降低 2 个数量级"></textarea>
        </label>
        <label class="field"><span>挂靠大纲小节</span>
          <select id="mpNodeParent" class="control" style="width:100%">
            ${secNodes.map(s=>`<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("")}
          </select>
        </label>
        <label class="field"><span>与该小节关系</span>
          <select id="mpNodeRel" class="control" style="width:100%">
            <option value="支撑" selected>支撑 (正面佐证)</option>
            <option value="反例">反例 (反向证伪 / 冲突对比)</option>
            <option value="方法">方法支撑</option>
            <option value="补充">背景补充</option>
          </select>
        </label>
      </div>
      <div class="modal-foot">
        <button id="btnMpSubmit" class="btn btn-primary">确认添加</button>
        <button id="btnMpCancel" class="btn">取消</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  const close = ()=>modal.remove();
  modal.querySelector("#btnMpClose").onclick = close;
  modal.querySelector("#btnMpCancel").onclick = close;
  modal.querySelector(".modal-mask").onclick = close;
  modal.querySelector("#btnMpSubmit").onclick = async ()=>{
    const type = modal.querySelector("#mpNodeType").value;
    const label = modal.querySelector("#mpNodeLabel").value.trim();
    const detail = modal.querySelector("#mpNodeDetail").value.trim();
    const parentId = modal.querySelector("#mpNodeParent").value;
    const rel = modal.querySelector("#mpNodeRel").value;
    if(!label){ uiNotify("请填写节点标题"); return; }
    const newId = "user_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 6);
    const newNode = { id: newId, type, label, detail, mine: true, ref: type === "data" ? "data/user" : "" };
    MP.nodes.push(newNode);
    if(parentId){
      MP.edges.push({ from: parentId, to: newId, label: rel });
    }
    try{
      await api("/api/map/save", { task_id: curTask, data: { nodes: MP.nodes, edges: MP.edges } });
      uiNotify("已添加新节点并保存证据链谱系图");
      close();
      mpPaint();
    }catch(err){
      uiNotify("保存失败：" + err.message);
    }
  };
}
