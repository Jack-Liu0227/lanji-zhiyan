/* ============ 论文翻译（工作流第 4 步）============
   照用户草图：左上「＋ 添加论文」（文件对话框 / 拖进来）→ 自动解析 DOI/题目/发表时间/关键词
   → 大框里是可滚动的 PDF（缩放 / 页码 / 荧光笔）→ 底部三个功能键：中英互译 / 笔记模式 / 荧光笔。
   PDF 渲染用 vendor 的 pdf.js 3.11（能选中文字，荧光笔才有依据）；译文用 BabelDOC（后端）。 */
const TR = { pid: "", task: "", meta: null, view: "read", scale: 1.15, noteScale: 1, noteZen: false, hlOn: false, hlColor: "#FFE066", hlWidth: 14,
             pdf: null, pages: 0, sel: new Set(), hl: [], job: null, poll: 0, note: null, dual: false,
             scaleB: 0, active: "a", split: 50, splitBound: 0, syncBound: 0 };
const TR_MAX = 3.0, TR_MIN = 0.5;
const trTask = () => TR.task || curTask;   // 工具箱选的项目优先，其次全局当前项目


function trPanelHTML(active){
  return `<div class="tr" id="panel-notes"${active?"":' style="display:none"'}>
    <div class="tr-bar">
      <button class="btn btn-teal" id="trAdd">＋ 添加论文</button>
      <span class="tr-seg" id="trSeg">
        <button class="tr-seg-b active" id="trBtnEn" title="英文原文 PDF">English</button>
        <button class="tr-seg-b" id="trBtnDual" title="左原文 右译文 对照">对照</button>
        <button class="tr-seg-b" id="trBtnZh" title="纯中文（BabelDOC 译文）">中文</button>
      </span>
      <button class="tr-info-b" id="trInfoBtn" aria-haspopup="true" aria-expanded="false" title="论文信息">论文信息 ▾</button>
      <span class="tr-spacer"></span>
      <span class="tr-grp">
        <button class="tr-ib" id="trPenTop" title="荧光笔" aria-label="荧光笔">🖍</button>
        <button class="tr-ib" id="trZenTop" title="全屏阅读（Esc 退出）" aria-label="专注模式">⛶</button>
      </span>
      <span class="tr-zoom"><button id="trOut" title="缩小" aria-label="缩小">−</button><span class="tr-zoomv" id="trZoomV">115%</span><button id="trIn" title="放大" aria-label="放大">＋</button></span>
      <span class="tr-pageno" id="trPageNo">0 / 0</span>
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
    <div class="tr-info hidden" id="trInfo" role="dialog" aria-label="论文信息">
      <div class="tr-info-cover" id="trInfoCover"><span>第 1 页预览</span></div>
      <b class="tr-info-t" id="trInfoTitle">—</b>
      <dl class="tr-info-list">
        <div><dt>加入项目</dt><dd id="trInfoAdded">—</dd></div>
        <div><dt>DOI</dt><dd id="trInfoDoi">—</dd></div>
        <div><dt>关键词</dt><dd id="trInfoKw">—</dd></div>
        <div><dt>总页数</dt><dd id="trInfoPages">—</dd></div>
        <div><dt>已翻译</dt><dd id="trInfoProg">—</dd></div>
      </dl>
      <div class="tr-info-bar"><i id="trInfoBar"></i></div>
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
      <div class="tr-split" id="trSplit" title="拖动调整左右宽度"></div>
      <div class="tr-pane" id="trPaneB" data-pane="b"><div class="tr-scroll"></div></div>
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
  if(!trTask()) return trMsg("先选一个项目 —— PDF 要入当前项目的文献库");
  trMsg("上传中… " + f.name);
  try{
    const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}&task_id=${encodeURIComponent(trTask())}`,
                          {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:f});
    const a = await r.json(); if(a.error) throw new Error(a.error);
    if(!a.paper_id) return trMsg("入库失败：" + (a.ingest_error || "不是有效 PDF"));
    await trOpen(a.paper_id);
  }catch(e){ trMsg("上传失败：" + e.message); }
}
function trMsg(t){ const el=$("trMsg"); if(el) el.textContent = t || ""; }

async function trOpen(pid){
  TR.pid = pid;
  trView("read");
  trSegActive("read");
  trInfoToggle(false);
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

/* ---------------- 论文信息胶囊（三键右边那颗）：B 左轨的内容，弹层形式 ---------------- */
function trInfoFill(){
  const m = TR.meta || {};
  const t = $("trInfoTitle");
  if(t) t.textContent = m.title || (TR.pid ? "（这篇没解析出题目）" : "还没有打开论文");
  const kw = String(m.keywords || "").split(/[;,、，]/).filter(x => x.trim()).length;
  const set = (id, v)=>{ const el = $(id); if(el) el.textContent = v || "—"; };
  set("trInfoAdded", m.added);
  set("trInfoDoi", m.doi || (TR.pid ? "未解析到" : ""));
  set("trInfoKw", kw ? kw + " 个" : "");
  set("trInfoPages", TR.pages ? TR.pages + " 页" : "");
  const s = TR.job || {}, done = +s.pages_done || 0, total = +s.total_pages || TR.pages || 0;
  let prog = "未开始";
  if(s.state === "done" || (total && done >= total)) prog = `已译完 ${total} 页`;
  else if(s.state === "running" || s.state === "partial" || done) prog = `翻译中 ${done}/${total || "?"} 页`;
  set("trInfoProg", TR.pid ? prog : "");
  const bar = $("trInfoBar");
  if(bar) bar.style.width = total ? Math.round(done / total * 100) + "%" : (s.state === "done" ? "100%" : "0%");
}
async function trInfoThumb(){
  const box = $("trInfoCover"); if(!box) return;
  const cv0 = document.querySelector('#trPaneA .tr-page[data-page="1"] canvas');   // 已经渲好的首页直接截图，省一次渲染
  if(cv0){
    try{ box.innerHTML = `<img src="${cv0.toDataURL("image/jpeg", 0.7)}" alt="">`; return; }catch(_){}
  }
  const pdf = TR.pdfs?.a; if(!pdf) return;
  try{
    const page = await pdf.getPage(1);
    const vp = page.getViewport({scale: 0.45});
    const cv = document.createElement("canvas"); cv.width = vp.width; cv.height = vp.height;
    await page.render({canvasContext: cv.getContext("2d"), viewport: vp}).promise;
    if(box.isConnected) box.innerHTML = `<img src="${cv.toDataURL("image/jpeg", 0.75)}" alt="">`;
  }catch(_){}
}
async function trInfoLoad(){
  trInfoFill();
  if(TR.pid && !TR.meta) await trMeta();
  if(TR.pid){
    try{ TR.job = await api(`/api/trans/status?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(trTask() || "")}`); }catch(_){}
  }
  trInfoFill(); trInfoThumb();
}
function trInfoToggle(on){
  const p = $("trInfo"); if(!p) return;
  const show = on === undefined ? p.classList.contains("hidden") : !!on;
  p.classList.toggle("hidden", !show);
  $("trInfoBtn")?.setAttribute("aria-expanded", String(show));
  if(show) trInfoLoad();
}

/* ---------------- 阅读器（pdf.js，一个 pane 一套） ---------------- */
function trWorker(){
  if(window.pdfjsLib && !window.pdfjsLib.GlobalWorkerOptions.workerSrc)
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.js";
}
function trPaneEl(key){ return $(key === "b" ? "trPaneB" : "trPaneA"); }
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
function trSc(key){ return key === "b" ? (TR.scaleB || TR.scale) : TR.scale; }
function trFocus(k){
  /* 对照模式：点哪栏哪栏亮，缩放键只动这一栏 */
  TR.active = k;
  $("trPaneA")?.classList.toggle("tr-focus", k === "a");
  $("trPaneB")?.classList.toggle("tr-focus", k === "b");
  trZoomLabel();
}
async function trFitBoth(){
  const pdf = TR.pdfs?.a; if(!pdf) return;
  try{
    const vp = (await pdf.getPage(1)).getViewport({scale: 1});
    const fit = k => Math.min(TR_MAX, Math.max(TR_MIN, Math.round(((trPaneEl(k)?.clientWidth || 380) - 26) / vp.width * 100) / 100));
    TR.scale = fit("a"); TR.scaleB = fit("b");
    trZoomLabel();
  }catch(_){ return; }
  await trMount("a", `/api/pdf?id=${encodeURIComponent(TR.pid)}`);
  if(TR.pdfs?.b) await trMount("b", TR.monoUrl);
}
function trSplitBind(){
  const sp = $("trSplit"), st = $("trStage");
  if(!sp || !st || TR.splitBound) return;
  TR.splitBound = 1;
  let drag = false;
  sp.addEventListener("pointerdown", (e)=>{ drag = true; try{ sp.setPointerCapture(e.pointerId); }catch(_){} e.preventDefault(); });
  sp.addEventListener("pointermove", (e)=>{
    if(!drag) return;
    const r = st.getBoundingClientRect();
    const pct = Math.min(76, Math.max(24, (e.clientX - r.left) / r.width * 100));
    TR.split = pct;
    st.style.setProperty("--split", pct.toFixed(2) + "%");
  });
  const end = (e)=>{
    if(!drag) return;
    drag = false;
    try{ sp.releasePointerCapture(e.pointerId); }catch(_){}
    trFitBoth();                                        // 松手才重排，拖动过程中不卡
  };
  sp.addEventListener("pointerup", end);
  sp.addEventListener("pointercancel", end);
}
async function trShowDual(v){
  /* 双栏对照：左原文 右译文，滚动按比例联动。译文没生成时先提示去点「中文」 */
  if(!TR.pid) return trMsg("先添加一篇 PDF");
  if(!window.pdfjsLib){ trMsg("pdf.js 没加载"); return; }
  trWorker();
  const entering = TR.view !== "dual";
  trView("dual");
  TR.monoUrl = trMonoUrl(v);
  if(!TR.pdfs?.a) await trMount("a", `/api/pdf?id=${encodeURIComponent(TR.pid)}`);
  const pdf = TR.pdfs?.a;
  if(entering && pdf){
    try{
      const vp = (await pdf.getPage(1)).getViewport({scale: 1});
      const fit = k => Math.min(TR_MAX, Math.max(TR_MIN, Math.round(((trPaneEl(k)?.clientWidth || 380) - 26) / vp.width * 100) / 100));
      TR.scale = fit("a"); TR.scaleB = fit("b");
      trZoomLabel();
    }catch(_){}
  }
  let ready = !!(v > 0);                               // 有没有译文：问状态，别靠错误文案猜
  try{
    const st = await api(`/api/trans/status?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(trTask() || "")}`);
    TR.job = st; ready = st.state === "done" || (+st.pages_done || 0) > 0;
    if(!$("trInfo")?.classList.contains("hidden")) trInfoFill();
  }catch(_){}
  if(!ready){
    const box = trScrollEl("b");
    if(box) box.innerHTML = `<div class="tr-load">译文还没生成 —— 点上面「中文」开始翻译（后台分批跑，翻到哪看到哪）</div>`;
    TR.pdfs && (TR.pdfs.b = null);
    await trMount("a", `/api/pdf?id=${encodeURIComponent(TR.pid)}`);   // 左栏按半幅重新排一次，别让原尺寸横着顶出去
    trSyncScroll();
    return;
  }
  await Promise.all([
    trMount("a", `/api/pdf?id=${encodeURIComponent(TR.pid)}`),
    trMount("b", TR.monoUrl),
  ]);
  trSyncScroll();
}
function trSyncScroll(){
  /* 两栏按滚动比例联动，谁滚谁说了算；一帧内的回声用 lock 吞掉 */
  if(TR.syncBound) return;
  TR.syncBound = 1;
  const a = trScrollEl("a"), b = trScrollEl("b"); if(!a || !b) return;
  let lock = 0;
  const link = (src, dst)=> src.addEventListener("scroll", ()=>{
    if(lock) return;
    lock = 1;
    const maxS = Math.max(1, src.scrollHeight - src.clientHeight);
    const maxD = Math.max(0, dst.scrollHeight - dst.clientHeight);
    dst.scrollTop = Math.round(src.scrollTop / maxS * maxD);
    requestAnimationFrame(()=>{ lock = 0; });
  });
  link(a, b); link(b, a);
}
async function trMount(key, url){
  const box = trScrollEl(key); if(!box) return;
  box.innerHTML = `<div class="tr-load">正在渲染…</div>`;
  try{
    const pdf = await window.pdfjsLib.getDocument({url}).promise;
    TR.pdfs = TR.pdfs || {}; TR.pdfs[key] = pdf;
    if(key === "a") TR.pages = pdf.numPages;   // 中文译文页数和原文可能不一致："x / N" 永远按原文算
    box.innerHTML = "";
    const sc = trSc(key);
    for(let n = 1; n <= pdf.numPages; n++){
      const page = await pdf.getPage(n);
      const vp = page.getViewport({scale: sc});
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
    const noMono = key === "b" && /404|not found/i.test(String(e.message));
    box.innerHTML = noMono
      ? `<div class="tr-load">译文还没生成 —— 点上面「中文」开始翻译（后台分批跑，翻到哪看到哪）</div>`
      : `<div class="tr-load err">PDF 打不开：${esc(e.message)}</div>`;
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
    const vp = page.getViewport({scale: trSc(key)});
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
    if(key === "a") trPaintHls(n, el);   // 标注坐标是原文页上的，译文栏不画
  }catch(e){ el.dataset.painted = ""; el.dataset.err = String(e && e.message || e);
             const ph = el.querySelector(".tr-ph"); if(ph) ph.textContent = "这一页渲染失败：" + el.dataset.err.slice(0, 80); }
}
function trPageNo(key){
  const box = trScrollEl(key); if(!box) return;
  const mid = box.scrollTop + box.clientHeight * 0.35;
  let best = 1;
  box.querySelectorAll(".tr-page").forEach(el=>{ if(el.offsetTop <= mid) best = +el.dataset.page; });
  if(key === "a"){ $("trPageNo").textContent = `${best} / ${TR.pages || 0}`; TR.cur = best; }
}
function trZen(on){
  /* 专注模式：只把阅读区拉到全屏，其余（pdf.js 双栏、拖分隔线、点哪栏管哪栏）全复用现成的 */
  const st = $("trStage"); if(!st) return;
  const show = on === undefined ? !st.classList.contains("zen") : !!on;
  st.classList.toggle("zen", show);
  TR.zen = show;
  $("trZenTop")?.classList.toggle("active", show);
  trMsg(show ? "专注模式：缩放用左上角那对加减号，Esc 退出" : "");
  if(show){ trPaintNear("a"); if(TR.view === "dual") trPaintNear("b"); trZoomLabel(); }
}
function trZoom(d){
  const key = (TR.view === "dual" && TR.active === "b") ? "b" : "a";   // 对照里只缩放被点亮那一栏
  const next = Math.min(TR_MAX, Math.max(TR_MIN, Math.round((trSc(key) + d) * 100) / 100));
  if(key === "b") TR.scaleB = next; else TR.scale = next;
  const where = TR.view === "dual" ? (key === "b" ? "（右栏）" : "（左栏）") : "";
  trMsg(`缩放 ${Math.round(next*100)}%${where}`);
  if(TR.pid){
    if(TR.view === "dual") trMount(key, key === "b" ? TR.monoUrl : `/api/pdf?id=${encodeURIComponent(TR.pid)}`);
    else if(TR.view === "zh") trShowZh();
    else trReader(TR.pid);
  }
  trZoomLabel();
}
function trZoomLabel(){
  const t = Math.round(trSc(TR.view === "dual" ? (TR.active || "a") : "a") * 100) + "%";
  const v = $("trZenZoomV"); if(v) v.textContent = t;      // 专注模式左上角那个
  const w = $("trZoomV");    if(w) w.textContent = t;      // 工具栏缩放组里那个
}
function trExitFull(){
  /* ✕ / Esc 的统一出口：先把全屏收掉 */
  trZen(false);
}

/* ---------------- 荧光笔 ---------------- */
const TR_COLORS = ["#FFE066", "#8BD498", "#7FC4F0", "#F0A9C0", "#F07C7C"];
let trHlT = 0;
function trHlSave(){
  /* 本机立刻落一份（离线也画得出来），服务端 400ms 防抖 —— 专家读的是服务端那份 */
  try{ localStorage.setItem("rw-hl-" + TR.pid, JSON.stringify({v: 2, list: TR.hl})); }catch(_){}
  clearTimeout(trHlT);
  trHlT = setTimeout(()=>{ api("/api/tr/hl", {id: TR.pid, task_id: trTask() || "", list: TR.hl}).catch(()=>{}); }, 400);
}
async function trHlLoad(){
  /* 先问服务端（专家要用），没有再用本机那份（离线时画得出来） */
  let list = null;
  try{
    const d = await api(`/api/tr/hl?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(trTask()||"")}`);
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
  const k0 = el.clientWidth ? el.clientWidth : 0;     // 当前页宽
  TR.hl.filter(h=>h.p === n).forEach((h, i)=>{
    const k = h.sw && k0 ? k0 / h.sw : 1;             // 按「划线时的页宽」缩放：换缩放/双栏后标注跟着字走
    const d = document.createElement("div");
    d.className = "tr-hl"; d.dataset.i = TR.hl.indexOf(h);
    d.style.cssText = `left:${(h.x*k).toFixed(1)}px;top:${(h.y*k).toFixed(1)}px;width:${(h.w*k).toFixed(1)}px;height:${(h.th*k).toFixed(1)}px;background:${h.c}`
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
                w:Math.round(r.right - r.left), th, c:TR.hlColor, sw:Math.round(pr.width),
                t: i === 0 ? txt : ""});   // 一段话占多行时文字只记在第一行；sw = 划线时页宽（缩放用）
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
    const r = await api("/api/trans/start", {id: TR.pid, task_id: trTask(), pages: TR.testPages || ""});
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
      const s = await api(`/api/trans/status?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(trTask() || "")}`);
      TR.job = s;
      if(!$("trInfo")?.classList.contains("hidden")) trInfoFill();   // 信息弹层开着就顺带刷新进度
      if(s.state === "running" || s.state === "partial"){
        const el = Math.round((Date.now() - (TR.t0 || (TR.t0 = Date.now()))) / 1000);
        lbl("翻译中…", true);
        const done = s.pages_done || 0;
        trMsg(`${s.msg || "正在翻译"}　已用 ${Math.floor(el/60)}分${el%60}秒（${TR.pages||"整篇"} 页大约十几分钟，可以先干别的）`);
        trBusy(true, "正在翻译中", `${s.msg || "正在翻译"}　已翻 ${done}/${s.total_pages || TR.pages || "?"} 页 · 已用 ${Math.floor(el/60)}分${el%60}秒`);
        if(done > 0 && done !== TR.prog){          // 翻好一批就在「中文」里先看这一批
          TR.prog = done;
          if(TR.view === "dual") trShowDual(done); else trShowZh(done);   // 对照里翻好了就地补右栏，别把人拽回单栏
          trMsg(`已翻好第 1-${done} 页（共 ${s.total_pages||TR.pages||"?"} 页），后面还在翻…`);
        }
        if(s.state === "partial"){                 // 后面失败了：已翻好的照看
          trStopPoll(); TR.t0 = 0; lbl("中文", false);
          trMsg(`翻到第 ${done} 页时中断：${s.msg || ""}（已翻好的还在，日志：${s.log || ""}）`);
          trBusy(true, "翻译中断", `翻到第 ${done} 页时失败：${s.msg || ""}` + (s.log ? `\n日志：${s.log}` : ""), true);
        }
      }
      if(s.state === "done"){ trStopPoll(); TR.t0 = 0; lbl("中文", false); trBusy(false); if(TR.view === "dual") trShowDual(s.pages_done || 0); else trShowZh(s.pages_done || 0); }
      if(s.state === "error"){ trStopPoll(); TR.t0 = 0; lbl("中文", false);
                              trMsg("翻译失败：" + (s.msg||"") + (s.log ? `（日志：${s.log}）` : ""));
                              trBusy(true, "翻译失败", (s.msg || "") + (s.log ? `\n日志：${s.log}` : ""), true); }
    }catch(_){}
  }, 2500);
}
function trStopPoll(){ if(TR.poll){ clearInterval(TR.poll); TR.poll = 0; } }

function trMonoUrl(v){
  return `/api/trans/file?id=${encodeURIComponent(TR.pid)}&task=${encodeURIComponent(trTask()||"")}&kind=mono&v=${v||0}`;
}
/* ---------------- 视图切换（英文 / 对照 / 中文） ---------------- */
function trSegActive(v){
  /* 三键的选中态只有这一处说了算 */
  $("trBtnEn")?.classList.toggle("active", v === "read");
  $("trBtnDual")?.classList.toggle("active", v === "dual");
  $("trBtnZh")?.classList.toggle("active", v === "zh");
}
function trView(v){
  /* 视图只管「有东西 / 没东西」「单栏 / 双栏」和分段控件的显隐 */
  TR.view = v;
  $("trDrop")?.classList.toggle("hidden", v !== "empty");
  $("trSeg")?.classList.toggle("hidden", v === "empty");
  const st = $("trStage");
  st?.classList.toggle("dual", v === "dual");
  if(v === "dual"){
    TR.active = TR.active || "a";
    st?.style.setProperty("--split", (TR.split || 50).toFixed(2) + "%");
    trSplitBind(); trFocus(TR.active);
  }else{
    TR.active = "a";
    $("trPaneA")?.classList.remove("tr-focus");
    $("trPaneB")?.classList.remove("tr-focus");
    const b = trScrollEl("b"); if(b) b.innerHTML = "";
  }
}

/* ---------------- 左上 ＋：直接进这个项目的文献库挑一篇 ---------------- */
async function trOpenPicker(){
  if(!trTask()) return trMsg("先选一个项目 —— 论文在项目的文献库里");
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
  try{ list = (await api(`/api/task?id=${encodeURIComponent(trTask())}`)).paper_list || []; }
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
  $("trBtnDual").onclick = ()=>{ if(!TR.pid) return trMsg("先添加一篇 PDF"); trSegActive("dual"); trShowDual(); };
  $("trStripMore").onclick = ()=>$("trDetail")?.classList.toggle("hidden");
  $("trInfoBtn").onclick = (e)=>{ e.stopPropagation(); trInfoToggle(); };
  trPenInit(); trZoomLabel();
  if(!TR.hlBound){ TR.hlBound = 1;                     // 文档级监听只挂一次（renderWorkflow 会反复调 trBind）
    document.addEventListener("mouseup", (e)=>{ if($("trStage")?.contains(e.target)) trHlOnMouseUp(e); });
    document.addEventListener("keydown", (e)=>{ if(e.key === "Escape"){ trPenPanel(false); trInfoToggle(false); trExitFull(); } });
    document.addEventListener("pointerdown", (e)=>{
      if(!e.target.closest?.("#trInfo") && !e.target.closest?.("#trInfoBtn")) trInfoToggle(false);
      if(TR.view !== "dual") return;
      if(e.target.closest?.("#trPaneB")) trFocus("b");
      else if(e.target.closest?.("#trPaneA")) trFocus("a");
    }, true);
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



/* ---------------- 工具箱嵌入（2.0）：整块挂进给定容器，项目取工具箱选的那个 ---------------- */
function trMountPanel(host, project){
  if(!host) return;
  TR.task = project || "";
  host.innerHTML = trPanelHTML(true);
  trBind();
  if(!TR.pid || TR.mountProject !== (TR.task || "")){
    /* 换项目 = 换文献库：旧论文不能带着跑 */
    TR.mountProject = TR.task || "";
    TR.pid = ""; TR.meta = null; TR.job = null;
    trStopPoll(); trBusy(false); trView("empty"); trSegActive("read"); trStrip();
    const m = $("trMsg"); if(m) m.textContent = trTask() ? "" : "先在左上角选一个项目 —— PDF 要入当前项目的文献库";
  }else{
    trOpen(TR.pid);                                 // 切走再切回来：还是这篇
  }
}
function trUnmount(){
  trStopPoll(); trBusy(false); trInfoToggle(false);
}
window.PaperTranslation = { mount: trMountPanel, unmount: trUnmount };
