/* ==== 创新点审查（这一步 = 100% 绑项目的受限对话页，记忆分长期/短期两区）==== */
function ideaPanelHTML(active){
  return `<section class="iw-page" id="panel-idea"${active?"":' style="display:none"'}>
    <div class="iw-top">
      <div class="ol-head"><b>我的创新点</b><span class="ol-meta" id="iw-mine-state">自动保存</span>
        <span class="ol-spacer"></span><span class="ol-meta">右边助手每轮都读这份文字</span></div>
      <textarea class="iw-mine" id="iwMine" spellcheck="true"
        placeholder="写清楚：做了什么、和谁不一样、凭什么能成（约束也写上：数据 / 算力 / 时间 / 目标期刊）"></textarea>
    </div>
    <div class="iw-out">
      <div class="ol-head"><b>判定结果</b><span class="ol-tag" id="iw-verdict-at"></span>
        <span class="ol-spacer"></span><span class="ol-meta">由右侧「创新点审查助手」产出</span></div>
      <div class="md-render iw-verdict" id="iwVerdict"></div>
    </div>
  </section>`;
}

/* ==== 确认大纲 / 论文撰写 / 稿件审查：三步共用一套「左 2/3 文件 + 右 1/3 agent」面板 ====
   左边是项目里那一份 markdown（框架.md / 正文.md），右边是这一步专属的 agent 对话。
   审查那一步左边还能用荧光笔标细节（dw-*），标完一键发给 agent 逐条回应。 */
const DOC_UI = {
  outline: { ico: "🗂", file: "框架", who: "框架",
    sub: "与项目绑定 · 只敲框架：大标题 `##` / 小标题 `###` / 每个小标题一句说明",
    seed: "用模板起稿", pull: "↧ 用这条回复更新框架", post: "/api/outline", scope: "outline", hl: false,
    empty: ["🗂", "这是项目的「确认大纲」", "先说清题目和目标（投哪里 / 学位要求 / 导师硬要求），我按「大标题 + 小标题 + 一句说明」帮你把框架搭出来；定稿了说一句「更新框架」我就写进项目里那份框架文件。"],
    ph: "说想法 / 贴要求 / 让 agent 改框架…（Enter 发送，Shift+Enter 换行）",
    hint: "Enter 发送 · 说「更新框架」→ 左边立刻换成新版本（文件就地覆盖）" },
  write: { ico: "✍️", file: "正文", who: "写作",
    sub: "与项目绑定 · 按上一步敲定的框架写正文（走本机 paper-spine / paper-writer）",
    seed: "按框架搭骨架", pull: "↧ 用这条回复更新正文", post: "/api/write", scope: "write", hl: false, cite: true, quiet: true,
    empty: ["✍️", "这是项目的「论文撰写」", "先确认上一步的框架已经定了；再说清：有没有实验数据/图表、投哪里、篇幅要求。我按框架逐节写，缺数据的地方写「待补」，绝不编数字。"],
    ph: "说要求 / 让它写某一节 / 讨论怎么改…（Enter 发送）",
    hint: "Enter 发送 · 说「更新正文」→ 左边立刻换成新版本（文件就地覆盖）" },
  review: { ico: "🧐", file: "正文", who: "审查",
    sub: "与项目绑定 · 以博导/审稿人的位置审这份正文：先说大思路上的问题",
    seed: "", pull: "↧ 用这条回复更新正文", post: "/api/review", scope: "review", hl: true, cite: true,
    empty: ["🧐", "这是项目的「稿件审查」", "直接说「审一遍」，或把具体担心抛过来。左边可以用 🖍 荧光笔标出想改的细节，标完点「把标注发给 agent」，我会逐条回应；大的思路问题我会先列出来。"],
    ph: "说「审一遍」/ 问某处逻辑 / 让 agent 改…（Enter 发送）",
    hint: "Enter 发送 · 左边 🖍 标细节 → 「把标注发给 agent」逐条回应" },
  map: { ico: "🧩", file: "谱系", who: "结构",
    sub: "与项目绑定 · 左边那张谱系图就是这一步的产物：把大纲每个小标题挂上精读结论 / 实验数据",
    seed: "", pull: "", post: "/api/map", scope: "map", hl: false,
    empty: ["🧩", "这是项目的「梳理精读」", "左边是谱系图的画布。跟我说要怎么改，比如「按大纲建一版谱系图」「把 Left2025 挂到 2.1 下面，标成反例」——改完左边立刻重画。实验数据直接拖到画布上就上传。"],
    ph: "说「按大纲建一版谱系图」/ 让它把某篇挂到某一节下面 / 讨论怎么归类…（Enter 发送）",
    hint: "Enter 发送 · 说「更新谱系」→ 画布立刻换成新版（谱系.json 就地覆盖）" },
};
const DOCS = {};                                   // 每一步一份状态
function docState(k){
  DOCS[k] = DOCS[k] || { msgs: [], events: [], stat: { turns: 0, calls: 0, tok: 0 }, out: "", view: "conversation",
                         atts: [], path: "", cand: "", marks: [], pen: false, color: "#FFE066", files: null, outs: [] };
  return DOCS[k];
}
function docEl(k, id){ return $(k + "-" + id); }

function docPanelHTML(k, active){
  const c = DOC_UI[k], proj = (S.tasks || []).find(t => t.id === curTask);
  return `<section class="at" id="panel-${k}"${active ? "" : ' style="display:none"'}>
    <div class="ol-grid">
      <div class="ol-left">
        <div class="ol-head">
          <b>${c.who}</b><span class="ol-tag" id="${k}-file">${c.file}.md</span>
          <span class="ol-spacer"></span>
          <span class="ol-meta" id="${k}-meta"></span>
          ${c.hl ? `<button class="btn ghost" id="${k}-pen" title="打开荧光笔：在左边拖选文字即可标注">🖍 荧光笔</button>` : ""}
          ${c.seed ? `<button class="btn ghost" id="${k}-seed" title="${c.seed}">${c.seed}</button>` : ""}
          <button class="btn hidden" id="${k}-pull" title="模型不支持工具调用时会把内容写在回复里 —— 点这里把那一段写进文件">${c.pull}</button>
          <button class="btn ghost" id="${k}-diff" title="对比正文变更并双向合并">⇄ 差异对比</button>
          <button class="btn ghost" id="${k}-edit" title="直接在左边改 markdown">✎ 手改</button>
          <button class="btn ghost hidden" id="${k}-edit-save">保存</button>
          <button class="btn ghost hidden" id="${k}-restore" title="恢复自动保存的本地草稿快照">↻ 恢复快照</button>
          ${c.cite ? `<button class="btn ghost" id="${k}-cite" title="后台的引用台账：每一处引用的位置 + 文献信息（题名/作者/出处/DOI/页数）">📑 引用台账</button>` : ""}
          <button class="btn ghost" id="${k}-reload" title="从磁盘重新读">↻</button>
        </div>
        <div class="ol-body md-render" id="${k}-body"></div>
        <textarea class="ol-src hidden" id="${k}-src" spellcheck="false"></textarea>
        ${c.hl ? `<div class="ol-marks">
          <div class="ol-marks-h"><span id="${k}-marks-n">标注 0 处</span><span class="ol-spacer"></span>
            <button class="btn ghost" id="${k}-marks-send" title="把我的标注作为一条消息发给 agent，让它逐条回应">把标注发给 agent</button>
            <button class="btn ghost" id="${k}-marks-clear" title="清空标注">清空</button></div>
          <div class="ol-marks-l" id="${k}-marks-l"></div></div>` : ""}
      </div>
      <div class="ol-right">
        <div class="at-head">
          <div class="at-heading"><span style="color:var(--accent);font-size:15px">${c.ico}</span>
            <div><h1 id="${k}-title">${esc(proj ? proj.title : c.who)}</h1>
            <p id="${k}-sub">${c.sub}</p></div></div>
        </div>
        <div class="at-tabs">
          <button class="at-tab" data-dv="${k}|conversation">对话</button>
          <button class="at-tab" data-dv="${k}|trajectory">轨迹 <span id="${k}-trail-n">0</span></button></div>
        <div class="at-msgs" id="${k}-msgs" role="log" aria-label="${c.who}对话"></div>
        <div class="at-dock">
          <div class="at-panel">
            <div class="st-box st-counts">
              <div class="st-row"><span>对话轮数</span><b id="${k}-turns">0</b></div>
              <div class="st-row"><span>调用工具数</span><b id="${k}-calls">0</b></div>
              <div class="st-row"><span>消耗 token</span><b id="${k}-tok">0</b></div></div>
            <div class="st-box"><p>工具名称</p><div class="st-log" id="${k}-tools"></div></div>
            <div class="st-box"><p>产出目录</p><div class="st-log" id="${k}-outs"></div></div>
          </div>
          <div class="atts" id="${k}-atts"></div>
          <div class="composer" id="${k}-composer">
            <textarea id="${k}-ask" rows="1" placeholder="${c.ph}"></textarea>
            <div class="at-tools">
              <div class="at-tools-l"><button class="icon-btn" id="${k}-plus" title="附加文件">＋</button>
                <label class="at-sel" title="这一步用哪个模型档案">🔌<select id="${k}-modpick"><option value="">未配置模型</option>
                  ${S.models.map(mm => `<option value="${mm.id}" ${S.active && mm.id === S.active.id ? "selected" : ""}>${esc(mm.name)}</option>`).join("")}</select></label></div>
              <div class="at-tools-r">
                <label class="at-sel" title="助手署名">🤖<input id="${k}-assist" placeholder="researcher" value="${esc(assistantKey())}"></label>
                <button class="send-btn" id="${k}-send" title="发送" aria-label="发送">↑</button></div>
            </div>
            <input type="file" id="${k}-filepick" multiple style="display:none">
          </div>
          <p class="at-hint">${c.hint}</p>
        </div>
        <div class="dw-pen hidden" id="${k}-penpanel">
          <b>颜色</b><div class="dw-colors" id="${k}-colors"></div>
          <label class="dw-x" title="退出荧光笔">✕</label>
        </div>
      </div>
    </div>
  </section>`;
}

/* ---------- 左边那份文件 ---------- */
async function docShowCite(k){
  /* 左边切成「后台引用台账」（只读）：位置 + 文献信息，随正文改动自动对锚点 */
  const st = docState(k), body = docEl(k, "body"), btn = docEl(k, "cite");
  try{
    const d = await api(`/api/doc/cite?id=${encodeURIComponent(curTask)}`);
    st.showCite = true;
    if(btn) btn.classList.add("active");
    body.innerHTML = renderMarkdown(d.markdown || "（还没有引用记录）");
    docMsg(k, `引用台账 ${d.count} 条${d.stale ? `（${d.stale} 条锚句在正文里找不到了）` : ""} · ${d.path || ""}`);
  }catch(e){ docMsg(k, "读台账失败：" + e.message); }
}
function docMsg(k, t){ const el = docEl(k, "meta"); if(el) el.textContent = t || ""; }
function docShow(k, d){
  const st = docState(k); if(!d) return;
  st.path = d.path || ""; st.exists = !!d.exists;
  const f = docEl(k, "file");
  if(f){ f.textContent = st.path ? st.path.split(/[\\/]/).pop() : "（未命名）"; f.title = st.path; }
  const md = d.markdown || "";
  st.showCite = false;
  const cb = docEl(k, "cite"); if(cb) cb.classList.remove("active");
  const body = docEl(k, "body");
  const ta = docEl(k, "src"); if(ta) ta.value = md;
  if(body){
    body.innerHTML = md.trim() ? renderMarkdown(md)
      : `<div class="ol-empty">${DOC_UI[k].empty[1]} 还没有内容。<br>右边跟 agent 说一句，或点上面「${DOC_UI[k].seed || "让 agent 写"}」。</div>`;
    if(DOC_UI[k].hl) docPaintMarks(k);                    // 标注按文本重新贴回去
  }
  const mb = docEl(k, "meta"); if(mb) mb.textContent = md.trim() ? `${d.sections || 0} 个大标题 · ${d.subs || 0} 个小标题 · ${d.chars || 0} 字` : "";
}
function docRestore(k, msgs, out){
  /* 把存下来的对话装回右栏：doc 三步和谱系图那步共用（以前 mpLoad 调了个早就没了的 mpMsgs，
     整段 throw 掉 → 重新整理之后右栏一句话不显示，看着就像按钮没反应） */
  const st = docState(k);
  st.msgs = msgs || []; st.out = out || "";
  st.events = st.msgs.flatMap(x => x.events || []);
  st.stat = { turns: st.msgs.filter(x => x.role === "user").length,
              calls: st.events.filter(e => e.type === "tool_call").length,
              tok: st.events.filter(e => e.type === "usage").reduce((n, e) => n + (e.total || 0), 0) };
  docStats(k); docPaint(k);
}
async function docLoad(k){
  const st = docState(k);
  try{
    const d = await api(`/api/doc/state?id=${encodeURIComponent(curTask)}&step=${encodeURIComponent(k)}`);
    st.out = d.out || "";
    docRestore(k, d.messages, d.out);
    docShow(k, d);
    if(!st.files) st.files = JSON.parse(localStorage.getItem("rw-marks-" + k + "-" + (curTask || "")) || "[]");
    docPaintMarks(k); docMarks(k);
  }catch(e){ docMsg(k, "读取失败：" + e.message); }
  docPaint(k); docStats(k);
}
function docEdit(k, on){
  docEl(k, "body").classList.toggle("hidden", on);
  docEl(k, "src").classList.toggle("hidden", !on);
  docEl(k, "edit").classList.toggle("hidden", on);
  docEl(k, "edit-save").classList.toggle("hidden", !on);
  if(on) docEl(k, "src").focus();
}
async function docSaveEdit(k){
  try{ docEdit(k, false); docShow(k, await api("/api/doc/save", { task_id: curTask, step: k, markdown: docEl(k, "src").value }));
       docMsg(k, "已保存到 " + docState(k).path); }
  catch(e){ docMsg(k, "保存失败：" + e.message); }
}
function docCandidate(text){
  /* 从一条回复里抠出「像成稿」的那段：优先 ``` 代码块，否则从第一个标题行截到结尾（至少 2 个标题）。 */
  if(!text) return "";
  const fence = String(text).match(/```(?:markdown|md|text)?\s*\n([\s\S]*?)```/);
  const body = fence ? fence[1] : String(text);
  const m = body.search(/^#{1,3}\s+\S/m);
  if(m < 0) return "";
  const cut = body.slice(m).trim();
  return (/^##\s+\S/m.test(cut) && (cut.match(/^#{2,3}\s+/gm) || []).length >= 2) ? cut : "";
}
function docShowCand(k){
  const b = docEl(k, "pull"); if(!b) return;
  const srcEl = docEl(k, "src"); const cur = (srcEl ? srcEl.value : "").trim();
  b.classList.toggle("hidden", !docState(k).cand || docState(k).cand === cur);
}

/* ---------- 荧光笔（审查那一步） ---------- */
function docMarksSave(k){
  const st = docState(k);
  try{ localStorage.setItem("rw-marks-" + k + "-" + (curTask || ""), JSON.stringify(st.marks)); }catch(_){}
}
function docPaintMarks(k){
  const st = docState(k), body = docEl(k, "body"); if(!body || !st.marks.length) return;
  st.marks.forEach(m => {                                   // 按文本把标注贴回去（正文没改就能对上）
    const walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let n;
    while((n = walk.nextNode())){
      const i = n.nodeValue.indexOf(m.t);
      if(i < 0 || n.parentElement.closest("mark.dw-hl")) continue;
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + m.t.length);
      const mk = document.createElement("mark"); mk.className = "dw-hl"; mk.style.background = m.c || "#FFE066";
      mk.title = "点一下取消这处标注";
      try{ r.surroundContents(mk); }catch(_){ continue; }
      mk.onclick = ()=>{ st.marks = st.marks.filter(x => x.t !== m.t); docPaintMarks(k); docMarks(k); docMarksSave(k); };
      break;
    }
  });
}
function docMarks(k){
  const st = docState(k), n = docEl(k, "marks-n"), host = docEl(k, "marks-l");
  if(n) n.textContent = `标注 ${st.marks.length} 处`;
  if(host) host.innerHTML = st.marks.map((m, i) =>
    `<span class="att"><b>${esc(m.t.slice(0, 46))}${m.t.length > 46 ? "…" : ""}</b><span class="x" data-rm="${i}" title="移除">✕</span></span>`).join("")
    || `<span class="ol-empty" style="font-size:12px">还没标 —— 打开 🖍 荧光笔，在左边拖选要改的文字</span>`;
  if(host) host.querySelectorAll("[data-rm]").forEach(x => x.onclick = ()=>{
    st.marks.splice(+x.dataset.rm, 1); docPaintMarks(k); docMarks(k); docMarksSave(k); });
}
function docPen(k, on){
  const st = docState(k), p = docEl(k, "penpanel"), btn = docEl(k, "pen");
  const show = on === undefined ? p.classList.contains("hidden") : !!on;
  p.classList.toggle("hidden", !show);
  st.pen = show;
  btn?.classList.toggle("active", show);
  document.body.classList.toggle("dw-pen-on", show);
  if(show) docMsg(k, "拖选左边文字 → 就标上了；点标注可取消");
}
function docPenColors(k, host){
  const st = docState(k);
  host.innerHTML = ["#FFE066", "#8BD498", "#7FC4F0", "#F0A9C0", "#F07C7C"]
    .map(c => `<i data-c="${c}" class="${c === st.color ? "on" : ""}" style="background:${c}"></i>`).join("");
  host.onclick = (e)=>{ const i = e.target.closest("i[data-c]"); if(!i) return;
    st.color = i.dataset.c; docPenColors(k, host); };
}
function docPenUp(k){
  const st = docState(k);
  if(!st.pen) return;
  const sel = window.getSelection();
  const t = (sel ? sel.toString() : "").trim();
  if(t.length < 2) return;
  const body = docEl(k, "body");
  if(!body.contains(sel.anchorNode)) return;
  const r = sel.getRangeAt(0);
  const mk = document.createElement("mark"); mk.className = "dw-hl"; mk.style.background = st.color;
  mk.title = "点一下取消这处标注";
  try{ r.surroundContents(mk); }catch(_){ return; }
  mk.onclick = ()=>{ st.marks = st.marks.filter(x => x.t !== t); docPaintMarks(k); docMarks(k); docMarksSave(k); };
  if(!st.marks.some(x => x.t === t)) st.marks.push({ t, c: st.color });
  sel.removeAllRanges(); docMarks(k); docMarksSave(k);
  docMsg(k, `已标 ${st.marks.length} 处 —— 标完点「把标注发给 agent」`);
}

/* ---------- 右边对话 ---------- */
function docBind(k, m){
  if(!docEl(k, "msgs")) return;
  m.querySelectorAll("[data-dv]").forEach(b => b.onclick = ()=>{
    const [kk, v] = b.dataset.dv.split("|"); docState(kk).view = v; docPaint(kk); });
  docEl(k, "send").onclick = () => docSend(k);
  docEl(k, "ask").onkeydown = (e)=>{ if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); docSend(k); } };
  docEl(k, "ask").oninput = () => docGrow(k);
  docEl(k, "plus").onclick = () => docEl(k, "filepick").click();
  docEl(k, "filepick").onchange = (e)=>{ const fs = [...e.target.files]; e.target.value = ""; docAddFiles(k, fs); };
  docEl(k, "modpick").onchange = async (e)=>{ if(!e.target.value) return;
    await api("/api/models/active", { id: e.target.value }); await refreshState(false); render(); };
  docEl(k, "assist").oninput = (e)=>localStorage.setItem("rw-assist", e.target.value.trim() || "researcher");
  if(DOC_UI[k].seed) docEl(k, "seed").onclick = async ()=>{ try{ docShow(k, await api("/api/doc/seed", { task_id: curTask, step: k }));
    docMsg(k, k === "write" ? "已按框架搭好骨架（标题层级照抄框架）" : "已按模板起稿"); }catch(e){ docMsg(k, "起稿失败：" + e.message); } };
  if(docEl(k, "pull")) docEl(k, "pull").onclick = async ()=>{
    const st = docState(k); if(!st.cand) return;
    try{ const r = await api("/api/doc/save", { task_id: curTask, step: k, markdown: st.cand });
         st.cand = ""; docShow(k, r); docShowCand(k); docMsg(k, `已用这条回复更新（${r.chars || 0} 字）`); }
    catch(e){ docMsg(k, "写入失败：" + e.message); }
  };
  if(docEl(k, "diff")) docEl(k, "diff").onclick = ()=>{
    const st = docState(k);
    const srcEl = docEl(k, "src");
    const currentText = (srcEl && !srcEl.classList.contains("hidden") ? srcEl.value : (st.markdown || "")).trim();
    const candidateText = (st.cand || "").trim() || currentText;
    showDiffModal(currentText, candidateText, async (acceptedText)=>{
      try{
        const r = await api("/api/doc/save", { task_id: curTask, step: k, markdown: acceptedText });
        st.cand = ""; docShow(k, r); docShowCand(k); docMsg(k, `已采纳合并变更（${r.chars || 0} 字）`);
      }catch(e){ docMsg(k, "合并保存失败：" + e.message); }
    });
  };
  const draftKey = `rw-draft-${k}-${curTask||""}`;
  const restoreBtn = docEl(k, "restore");
  if(restoreBtn){
    const saved = localStorage.getItem(draftKey);
    if(saved && saved !== (docState(k).markdown||"").trim()) restoreBtn.classList.remove("hidden");
    restoreBtn.onclick = ()=>{
      const val = localStorage.getItem(draftKey);
      if(!val) return;
      const srcEl = docEl(k, "src");
      if(srcEl){ srcEl.value = val; docEdit(k, true); docMsg(k, "已从本地快照恢复草稿"); }
    };
  }
  const curSrcEl = docEl(k, "src");
  if(curSrcEl && !curSrcEl._autoSaveBound){
    curSrcEl._autoSaveBound = true;
    let sTimer = 0;
    curSrcEl.addEventListener("input", ()=>{
      clearTimeout(sTimer);
      sTimer = setTimeout(()=>{
        localStorage.setItem(draftKey, curSrcEl.value);
        if(restoreBtn) restoreBtn.classList.remove("hidden");
      }, 800);
    });
  }
  if(DOC_UI[k].cite) docEl(k, "cite").onclick = ()=>{
    if(docState(k).showCite) docLoad(k); else docShowCite(k); };
  if(docEl(k, "reload")) docEl(k, "reload").onclick = ()=>docLoad(k);
  if(docEl(k, "edit")) docEl(k, "edit").onclick = ()=>docEdit(k, true);
  if(docEl(k, "edit-save")) docEl(k, "edit-save").onclick = ()=>docSaveEdit(k);
  if(DOC_UI[k].hl){
    docEl(k, "pen").onclick = ()=>docPen(k);
    docEl(k, "penpanel").querySelector(".dw-x").onclick = ()=>docPen(k, false);
    docPenColors(k, docEl(k, "colors"));
    docEl(k, "marks-send").onclick = ()=>{
      const st = docState(k);
      if(!st.marks.length) return docMsg(k, "还没标任何地方");
      const lines = st.marks.map((m, i) => `${i + 1}. ${m.t}`).join("\n");
      docEl(k, "ask").value = `我要改这几处细节，请逐条给建议（要改就直接更新正文）：\n[我的标注]\n${lines}`;
      docSend(k);
    };
    docEl(k, "marks-clear").onclick = ()=>{ const st = docState(k); st.marks = []; docPaintMarks(k); docMarks(k); docMarksSave(k); };
    if(!window.__docHlBound){        // 全局只挂一次；**事件发生时才取当前步骤**（闭包住 k 会在别的步骤失效）
      window.__docHlBound = 1;
      document.addEventListener("mouseup", (e)=>{
        const cur = wfStep;
        if(!DOC_UI[cur] || !DOC_UI[cur].hl) return;
        if(docEl(cur, "body")?.contains(e.target)) docPenUp(cur);
      });
    }
  }
  wireDrop(docEl(k, "composer")); docSetAtts(k); docGrow(k); docLoad(k);
  docOutsTick();
}
let OUTS_TICK = 0;
function docOutsTick(){
  /* 「产出目录」纯展示：项目里落过盘的文件就该一直列在那，不靠这一轮事件里有 tool_result */
  const pull = async () => {
    if(!curTask) return;
    const p = $("panel-" + wfStep); if(document.hidden || !p || p.style.display === "none") return;
    try{
      const d = await api(`/api/step/outs?id=${encodeURIComponent(curTask)}&step=${encodeURIComponent(wfStep)}`);
      const st = docState(wfStep); st.out = d.out || st.out; st.outs = d.outs || [];
      docStats(wfStep);
    }catch(_){}
  };
  pull();
  if(!OUTS_TICK) OUTS_TICK = setInterval(pull, 4000);
}
function docProseHidden(k, mv){
  /* 写作这步（DOC_UI.write.quiet）：正文成品只放左边的展示区，右栏只留过程。
     但没落盘的那几轮（只是在问你/在讨论）正文得照常显示，否则右栏就空了。 */
  if(!DOC_UI[k].quiet) return false;
  return (mv.events || []).some(e => e.type === "tool_call" && /_write$/.test(e.name || ""));
}
function docPaint(k){
  const st = docState(k), box = docEl(k, "msgs"); if(!box) return;
  document.querySelectorAll("[data-dv]").forEach(b => b.classList.toggle("on", b.dataset.dv === `${k}|${st.view}`));
  if(st.view === "trajectory"){ renderTrajectory(box, st.events); return; }
  const c = DOC_UI[k];
  if(!st.msgs.length){ box.innerHTML = emptyState(c.empty[0], c.empty[1], c.empty[2]); return; }
  box.innerHTML = "";
  st.msgs.forEach(mv => {
    const hide = docProseHidden(k, mv);
    appendOne(box, { type: mv.role, content: hide ? "" : mv.content, replay: true, at: mv.created_at,
                     events: hide ? (mv.events || []).filter(e => !/^assistant/.test(e.type)) : mv.events });
  });
  box.scrollTop = box.scrollHeight;
}
function docStats(k){
  const st = docState(k);
  const t = docEl(k, "turns"), c = docEl(k, "calls"), tk = docEl(k, "tok"), tn = docEl(k, "trail-n");
  if(t) t.textContent = st.stat.turns;
  if(c) c.textContent = st.stat.calls;
  if(tk) tk.textContent = (st.stat.tok || 0).toLocaleString();
  if(tn) tn.textContent = st.stat.calls;
  const log = (id, rows, empty)=>{ const el = docEl(k, id); if(!el) return;
    el.innerHTML = rows.length ? rows.map(r => `<div title="${esc(r)}">${esc(r)}</div>`).join("")
                               : `<div class="none">${esc(empty)}</div>`;
    el.scrollTop = el.scrollHeight; };
  log("tools", st.events.filter(e => e.type === "tool_call").map(e => e.name).slice(-40), "还没有工具调用");
  const files = (st.outs || []).map(o => o.path);
  const evPaths = st.events.filter(e => e.type === "tool_result" && e.path).map(e => e.path);
  const outs = [...new Set([...files, ...evPaths])];
  log("outs", outs.slice(0, 40), "还没有产出");
}
function docSetAtts(k){
  const st = docState(k), host = docEl(k, "atts"); if(!host) return;
  host.innerHTML = st.atts.map((a, i) => `<span class="att"><b>${esc(a.name)}</b><span>${a.url ? "链接" : a.paper_id ? "已入库 " + a.pages + " 页" : fmtSize(a.size)}</span><span class="x" data-rm="${i}" title="移除">✕</span></span>`).join("");
  host.querySelectorAll("[data-rm]").forEach(x => x.onclick = ()=>{ st.atts.splice(+x.dataset.rm, 1); docSetAtts(k); });
}
async function docAddFiles(k, files){
  const st = docState(k);
  if(!files || !files.length) return;
  for(const f of files){
    if(!curTask && /\.pdf$/i.test(f.name)){ uiNotify("先选或建一个项目 —— PDF 要入当前项目的文献库，agent 才读得到"); return; }
    try{
      const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}&task_id=${encodeURIComponent(curTask || "")}`,
                            { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
      const a = await r.json(); if(a.error) throw new Error(a.error);
      st.atts.push(a);
    }catch(e){ uiNotify("附加失败：" + e.message); }
  }
  docSetAtts(k);
}
function docGrow(k){
  const ta = docEl(k, "ask"); if(!ta) return;
  ta.style.height = Math.min(TA_MAX, Math.max(TA_MIN, measureAsk(ta))) + "px";
}
async function docSend(k){
  if(!(await requireModelKey())) return;          // 没配 Key：先提示去设置，不发请求
  const st = docState(k);
  const ta = docEl(k, "ask"); const text = ta.value.trim();
  if(!text && !st.atts.length) return;
  const msg = st.atts.length ? (text ? text + "\n\n" : "") + "[附件]\n" + st.atts.map(a => a.url ? `- ${a.url}`
      : `- ${a.name}${a.paper_id ? `（已入库 PDF，paper_id=${a.paper_id}，${a.pages} 页）` : `（本地文件：${a.path}）`}`).join("\n") : text;
  ta.value = ""; docGrow(k); st.atts.length = 0; docSetAtts(k);
  const box = docEl(k, "msgs");
  if(st.view !== "conversation"){ st.view = "conversation"; docPaint(k); }
  if(box.querySelector(".at-empty")) box.innerHTML = "";
  appendOne(box, { type: "user", content: msg });
  st.msgs.push({ role: "user", content: msg, events: [] }); st.stat.turns++; docStats(k);
  const btn = docEl(k, "send"); btn.disabled = true; btn.textContent = "■";
  let errMsg = "", final = "", evs = [];
  try{
    const res = await fetch(DOC_UI[k].post, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: curTask, message: msg }) });
    if(!res.ok || !res.body) throw new Error("HTTP " + res.status);
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for(;;){
      const { value, done } = await reader.read(); if(done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while((i = buf.indexOf("\n\n")) >= 0){
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const line = chunk.split("\n").find(l => l.startsWith("data: ")); if(!line) continue;
        let ev; try{ ev = JSON.parse(line.slice(6)); }catch{ continue; }
        if(ev.type === "done"){ final = ev.content || ""; continue; }
        if(ev.type === "user") continue;                 // 本地已经先画了，回声不再画、也不进事件流
        if(!(DOC_UI[k].quiet && /^assistant/.test(ev.type))) appendOne(box, ev);   // 写作这步正文最后落到左边，右栏不刷稿子
        evs.push(ev);
        if(ev.type === "usage") st.stat.tok += ev.total || 0;
        else if(ev.type === "tool_call") st.stat.calls++;
        docStats(k);
      }
    }
  }catch(e){ errMsg = e.message; appendOne(box, { type: "error", message: e.message }); }
  st.msgs.push({ role: "assistant", content: final, events: evs });
  st.events = st.events.concat(evs);
  const wrote = evs.some(e => e.type === "tool_call" && (e.name === "doc_write" || e.name === "outline_write" || e.name === "map_write"));
  st.cand = wrote ? "" : docCandidate(final);            // 模型没调工具就把回复里那段收着，给个一键落盘
  btn.disabled = false; btn.textContent = "↑";
  box.scrollTop = box.scrollHeight;
  await docLoad(k);                                      // 重拉：文件可能刚被 doc_write 改过 → 左边跟着更新
  if(k === "map") await mpLoad();                          // 谱系图：聊天改完立刻重画画布
  docShowCand(k);
  if(wrote && DOC_UI[k].quiet && final.trim())
    docMsg(k, `本轮 ${final.length} 字已落到左侧《${DOC_UI[k].file}》—— 右栏只留过程`);
  else if(!wrote && DOC_UI[k].quiet && final.trim())
    appendOne(box, { type: "assistant", content: final });  // 没落盘就是在讨论，正文得让他看见
  if(!wrote && !st.cand) docMsg(k, `这轮没有改${DOC_UI[k].file} —— 说「更新${DOC_UI[k].file}」或让它「写进文件」`);
  if(errMsg) appendOne(box, { type: "error", message: errMsg });
  await refreshState(false);
}

/* ==== 格式转换：文件区 / 编译区 / PDF 预览 三张卡片；转换本身交给右栏「格式助手」 ==== */
let cvCur = { path: "", ext: "" };

function convPanelHTML(active){
  return `<section class="cv" id="panel-convert"${active ? "" : ' style="display:none"'}>
    <div class="cv-grid">
      <div class="cv-col">
        <div class="cv-h"><b>文件</b><span class="cv-spacer"></span><button class="cv-ib" id="cvRefresh" title="重新读目录">↻</button></div>
        <div class="cv-list" id="cvList"></div>
      </div>
      <div class="cv-col">
        <div class="cv-h"><b id="cvName">编译区</b><span class="cv-msg" id="cvMsg"></span><span class="cv-spacer"></span>
          <button class="cv-ib hidden" id="cvCompile" title="用 xelatex 编译这份 .tex">⚙ 编译</button>
          <button class="cv-ib" id="cvSave" title="保存到磁盘">💾 保存</button></div>
        <textarea class="cv-src" id="cvSrc" spellcheck="false"
          placeholder="左边点一个 .md / .tex 文件 → 这里显示内容，可以直接改，改完点「保存」；.tex 可以点「编译」出 PDF。"></textarea>
      </div>
      <div class="cv-col">
        <div class="cv-h"><b>PDF 预览</b><span class="cv-spacer"></span><span class="cv-tag" id="cvPdfName"></span>
          <button class="cv-ib hidden" id="cvOpenExt" title="用系统默认程序打开">↗</button></div>
        <iframe class="cv-frame" id="cvFrame" src="about:blank"></iframe>
        <div class="cv-empty" id="cvEmpty">还没有 PDF —— 打开一个 .tex 点「⚙ 编译」，编出来就显示在这里。</div>
      </div>
    </div>
  </section>`;
}

function cvMsg(t, ms){ const el = $("cvMsg"); if(el) el.textContent = t || ""; if(ms) setTimeout(()=>{ if(el && el.textContent === t) el.textContent = ""; }, ms); }
function cvShowPdf(path){
  const f = $("cvFrame"), e = $("cvEmpty"), n = $("cvPdfName"), o = $("cvOpenExt");
  if(!path){ f.src = "about:blank"; e.classList.remove("hidden"); return; }
  e.classList.add("hidden");
  n.textContent = path.split(/[\\/]/).pop();
  o.classList.remove("hidden");
  f.src = `/api/file?path=${encodeURIComponent(path)}&inline=1&t=${Date.now()}`;
}
async function cvLoad(){
  if(!curTask) return;
  try{
    const d = await api(`/api/conv/files?id=${encodeURIComponent(curTask)}`);
    const rows = d.files || [];
    const groups = {};
    rows.forEach(r => { (groups[r.group] = groups[r.group] || []).push(r); });
    $("cvList").innerHTML = Object.keys(groups).length
      ? Object.entries(groups).map(([g, list]) => `<div class="cv-g">${esc(g)}</div>` + list.map(r =>
          `<div class="cv-f" data-p="${esc(r.path)}" title="${esc(r.path)}"><span class="cv-ext">${esc(r.ext || "•")}</span>${esc(r.name)}</div>`).join("")).join("")
      : `<div class="cv-none">项目里还没有文件 —— 先在「论文撰写」写出正文</div>`;
    $("cvList").querySelectorAll(".cv-f").forEach(el => el.onclick = () => cvOpen(el.dataset.p));
    if(d.pdf && !$("cvFrame").src.includes("api/file")) cvShowPdf(d.pdf);     // 默认预览最新那份 PDF
    if(d.pdf) $("cvPdfName").textContent = d.pdf.split(/[\\/]/).pop();
  }catch(e){ cvMsg("读目录失败：" + e.message); }
}
async function cvOpen(path){
  try{
    const d = await api(`/api/conv/read?path=${encodeURIComponent(path)}`);
    if(d.error) throw new Error(d.error);
    cvCur = { path: d.path, ext: d.ext || "" };
    $("cvList").querySelectorAll(".cv-f").forEach(el => el.classList.toggle("on", el.dataset.p === d.path));
    $("cvName").textContent = "编译区 · " + (d.name || "") + (d.bytes ? ` · ${d.bytes} 字` : "");
    $("cvSrc").value = d.binary ? "" : (d.text || "");
    $("cvSrc").readOnly = !!d.binary;
    $("cvCompile").classList.toggle("hidden", d.ext !== "tex");
    $("cvSave").classList.toggle("hidden", !!d.binary);
    if(d.binary && d.ext === "pdf") cvShowPdf(d.path);
    cvMsg(d.binary ? "二进制文件（不能在这里改）" : "已打开", 2500);
  }catch(e){ cvMsg("打开失败：" + e.message); }
}
async function cvSave(){
  if(!cvCur.path) return cvMsg("先选一个文件");
  try{
    const r = await api("/api/conv/save", { path: cvCur.path, text: $("cvSrc").value });
    cvMsg(`已保存 ${r.bytes} 字节`, 3000);
  }catch(e){ cvMsg("保存失败：" + e.message); }
}
async function cvCompile(){
  if(!cvCur.path) return;
  const btn = $("cvCompile");
  btn.disabled = true; cvMsg("xelatex 编译中…");
  try{
    const r = await api("/api/conv/compile", { path: cvCur.path });
    if(r.error) throw new Error(r.error);
    cvMsg(`编译好了：${r.name}`, 6000);
    await cvLoad();
    if(r.pdf) cvShowPdf(r.pdf);
  }catch(e){ cvMsg("编译失败：" + e.message); }
  btn.disabled = false;
}
function cvBind(){
  if(!$("cvList")) return;
  $("cvRefresh").onclick = cvLoad;
  // ask 模式下，写盘 / 编译前先问一句（all 模式直接放行，见 needPerm）
  $("cvSave").onclick = async () => { if (await needPerm("把这份文件写进项目目录")) cvSave(); };
  $("cvCompile").onclick = async () => { if (await needPerm("用 xelatex 编译这份 .tex 生成 PDF")) cvCompile(); };
  $("cvOpenExt").onclick = () => { if(cvCur.path || $("cvPdfName").textContent)
    api("/api/conv/open", { path: cvCur.path }).catch(()=>{}); };
  cvLoad();
}

/* ==== 中译英：左上 ＋（选文件/拖进来）+ 右上 ⚡ 一键翻译，下面左英文 / 右中文 对照 ==== */
const EN = { src: "", out: "", name: "正文.md", path: "", srcPath: "" };

function enPanelHTML(active){
  return `<section class="en" id="panel-entrans"${active ? "" : ' style="display:none"'}>
    <div class="en-bar">
      <button class="btn btn-teal" id="en-add" title="选一个中文 markdown 文件（也可以直接拖进来）">＋ 添加文件</button>
      <select class="en-sel" id="en-pick" title="项目里的中文 markdown"><option value="">项目里的文件…</option></select>
      <select class="en-sel" id="en-style" title="润色与翻译风格">
        <option value="formal">标准学术 (Nature/Cell Formal)</option>
        <option value="concise">极简凝练 (Concise & Dense)</option>
        <option value="impactful">强调突破 (Impactful & Strong)</option>
      </select>
      <span class="en-spacer"></span>
      <span class="en-msg" id="en-msg"></span>
      <button class="btn btn-teal" id="en-run" title="把左边中文 markdown 译成英文 markdown">⚡ 一键翻译</button>
      <input type="file" id="en-file" accept=".md,.markdown,.txt" hidden>
    </div>
    <div class="en-grid">
      <div class="en-col">
        <div class="en-h"><b>英文</b><span class="en-tag" id="en-outname">译文（可改）</span><span class="en-spacer"></span>
          <button class="cv-ib" id="en-save" title="存回项目（同名 _en.md）">💾 保存</button>
          <button class="cv-ib" id="en-copy" title="整段复制走">⧉ 复制</button></div>
        <textarea class="en-src" id="en-out" spellcheck="false"
          placeholder="点右上「⚡ 一键翻译」，英文就出现在这里：markdown 结构、公式、DOI 都保留，可以直接改完再保存。"></textarea>
      </div>
      <div class="en-col">
        <div class="en-h"><b>中文</b><span class="en-tag" id="en-srcname"></span><span class="en-spacer"></span>
          <button class="cv-ib" id="en-reload" title="重新读项目里的正文">↻ 重读</button></div>
        <textarea class="en-src" id="en-in" spellcheck="false"
          placeholder="中文 markdown：默认自动取「论文撰写」的正文.md；也可以点左上 ＋ 选文件，或把 .md/.txt 拖进来。"></textarea>
      </div>
    </div>
  </section>`;
}

function enMsg(t, ms){ const el = $("en-msg"); if(el) el.textContent = t || "";
  if(ms) setTimeout(()=>{ if(el && el.textContent === t) el.textContent = ""; }, ms); }
function enName(p){ return (p || "").split(/[\\/]/).pop() || ""; }

async function enLoad(){
  if(!curTask) return;
  try{
    const d = await api(`/api/en/state?id=${encodeURIComponent(curTask)}`);
    EN.srcPath = d.source_path || ""; EN.path = d.result_path || "";
    if(!$("en-in").value.trim()){                       // 用户已经改过就别覆盖他的输入
      $("en-in").value = d.source_text || "";
      EN.name = enName(d.source_path) || "正文.md";
    }
    if(!$("en-out").value.trim()) $("en-out").value = d.result_text || "";
    $("en-srcname").textContent = EN.name + (d.source_exists ? "" : "（还没有正文）");
    $("en-outname").textContent = enName(d.result_path) || "译文（可改）";
    if(d.source_exists && !d.result_exists) enMsg("已取到正文 —— 点右上「⚡ 一键翻译」");
    else if(!d.source_exists) enMsg("项目里还没有正文 —— 点左上 ＋ 选个文件，或先去「论文撰写」");
  }catch(e){ enMsg("读取失败：" + e.message); }
  enFill();
}
async function enFill(){
  const sel = $("en-pick"); if(!sel || !curTask) return;
  try{
    const d = await api(`/api/conv/files?id=${encodeURIComponent(curTask)}`);
    const mds = (d.files || []).filter(f => ["md", "txt"].includes(f.ext));
    sel.innerHTML = `<option value="">项目里的文件…</option>` + mds.map(f =>
      `<option value="${esc(f.path)}"${f.path === EN.srcPath ? " selected" : ""}>${esc(f.name)}</option>`).join("");
  }catch(_){}
}
async function enPickFile(path){
  if(!path) return;
  try{
    const d = await api(`/api/conv/read?path=${encodeURIComponent(path)}`);
    if(d.error) throw new Error(d.error);
    $("en-in").value = d.text || "";
    EN.name = d.name || enName(path); EN.srcPath = path;
    $("en-srcname").textContent = EN.name;
    enMsg("已载入 " + EN.name, 3000);
  }catch(e){ enMsg("打不开：" + e.message); }
}
function enReadLocal(f){
  if(!f) return;
  if(!/\.(md|markdown|txt)$/i.test(f.name)){ enMsg("只认 .md / .txt（Word 请先在「格式转换」转一下）", 6000); return; }
  const fr = new FileReader();
  fr.onload = () => { $("en-in").value = String(fr.result || ""); EN.name = f.name; EN.srcPath = "";
                      $("en-srcname").textContent = f.name + "（本地文件）"; enMsg("已载入 " + f.name, 3000); };
  fr.onerror = () => enMsg("读文件失败");
  fr.readAsText(f, "utf-8");
}
async function enRun(){
  if(!(await requireModelKey())) return;          // 没配 Key：先提示去设置，不发请求
  const inTa = $("en-in"), btn = $("en-run");
  const text = (inTa.value || "").trim();
  if(!text) return enMsg("还没有中文内容 —— 点左上 ＋ 选文件，或先去「论文撰写」写出正文");
  btn.disabled = true; enMsg("正在翻译…（分片并发，长文也就几十秒）");
  try{
    const r = await api("/api/en/run", { task_id: curTask, text, name: EN.name || "正文.md", style: $("en-style")?.value || "formal" });
    if(r.error) throw new Error(r.error);
    if(!$("en-out").value.trim() || await uiConfirm("右边已经有译文了：覆盖它吗？")) $("en-out").value = r.text || "";
    EN.path = r.path || EN.path; $("en-outname").textContent = r.name || "";
    enMsg(`${r.name} · ${r.chars} 字 · ${r.sections} 个标题 · ${r.chunks} 片 · ${(r.ms / 1000).toFixed(1)} 秒`, 12000);
    await enFill();
  }catch(e){ enMsg("翻译失败：" + e.message); }
  btn.disabled = false;
}
async function enSave(){
  if(!EN.path) return enMsg("还没有产物路径 —— 先点「⚡ 一键翻译」");
  try{
    const r = await api("/api/conv/save", { path: EN.path, text: $("en-out").value });
    enMsg(`已保存 ${r.bytes} 字节`, 4000);
  }catch(e){ enMsg("保存失败：" + e.message); }
}
function enBind(){
  if(!$("en-in")) return;
  $("en-add").onclick = () => $("en-file").click();
  $("en-file").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; enReadLocal(f); };
  $("en-pick").onchange = (e) => enPickFile(e.target.value);
  $("en-run").onclick = enRun;
  $("en-save").onclick = async () => { if (await needPerm("把译文写回项目目录")) enSave(); };
  $("en-reload").onclick = () => { $("en-in").value = ""; $("en-out").value = ""; enLoad(); };
  $("en-copy").onclick = async () => {
    try{ await navigator.clipboard.writeText($("en-out").value || ""); enMsg("已复制", 2500); }
    catch(_){ $("en-out").select(); document.execCommand("copy"); enMsg("已复制", 2500); }
  };
  const host = $("panel-entrans");                       // 拖进来
  let depth = 0;
  ["dragenter", "dragover"].forEach(t => host.addEventListener(t, (e) => {
    e.preventDefault(); e.stopPropagation(); if(t === "dragenter") depth++; host.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(t => host.addEventListener(t, (e) => {
    e.preventDefault(); e.stopPropagation();
    if(t === "drop"){ depth = 0; host.classList.remove("dragging");
      const f = (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) || null; enReadLocal(f); }
    else if(--depth <= 0){ depth = 0; host.classList.remove("dragging"); } }));
  enLoad();
}

/* ---------------- 工作流 ---------------- */
const STAGE={idea:"🔍 创新点审查",search:"📚 论文搜索",download:"⬇️ 下载精读",notes:"🌐 论文翻译",outline:"🗂 确认大纲",map:"🧩 梳理精读",write:"✍️ 文章撰写",review:"🧐 稿件审查",entrans:"🌍 中译英",convert:"🔄 格式转换"};
const WF_ORDER=["idea","search","download","notes","outline","map","write","review","entrans","convert"];
const PANELS = {
  idea: (a) => typeof ideaPanelHTML === "function" ? ideaPanelHTML(a) : "",
  search: (a) => typeof searchPanelHTML === "function" ? searchPanelHTML(a) : "",
  download: (a) => typeof downloadPanelHTML === "function" ? downloadPanelHTML(a) : "",
  notes: (a) => typeof trPanelHTML === "function" ? trPanelHTML(a) : "",
  map: (a) => typeof mapPanelHTML === "function" ? mapPanelHTML(a) : "",
  outline: (a) => typeof docPanelHTML === "function" ? docPanelHTML("outline", a) : "",
  write: (a) => typeof docPanelHTML === "function" ? docPanelHTML("write", a) : "",
  review: (a) => typeof docPanelHTML === "function" ? docPanelHTML("review", a) : "",
  convert: (a) => typeof convPanelHTML === "function" ? convPanelHTML(a) : "",
  entrans: (a) => typeof enPanelHTML === "function" ? enPanelHTML(a) : ""
};   // 有专门功能区的步骤；其余留白卡
let wfStep=localStorage.getItem("rw-wf-step")||"idea";
let wfMin=localStorage.getItem("rw-wf-min")==="1";
let wfMsgs=[], wfEvents=[];   // 右栏「本步助手」的历史与事件（统计口径只算这个助手自己）

/* ==== 三栏工作台：左十步 / 中展示区+确认交付 / 右本步助手 ==== */
/* 每一步一个助手：emoji + 名字 + 一句话职责 + 聊天挂在哪个 scoped 端点 */
const WF_AGENT = {
  idea:    {emoji:"🔍", name:"创新点审查助手", sub:"多轮问清约束 → 出正式判定；对话只在这一步", api:"/api/idea",
            ph:"说说你的创新点，或回答追问…", hint:"Enter 发送 · 说「出一版判定」→ 结论写进本步产出"},
  search:  {emoji:"📚", name:"文献检索助手", sub:"检索式、筛选口径、入库都归它管", api:"/api/agent",
            ph:"说检索式，或让我按目标改筛选…", hint:"Enter 发送 · 说「重跑检索」→ 结果刷新到左边"},
  download:{emoji:"⬇️", name:"入库助手", sub:"下载队列、OA 直链、手动补全文", api:"/api/agent",
            ph:"说下要下哪批 / 哪篇要手动补…", hint:"Enter 发送 · 说「继续下载」→ 队列接着跑"},
  notes:   {emoji:"🌐", name:"翻译助手", sub:"翻译口径、术语表、双语对照", api:"/api/agent",
            ph:"说翻译要求（保留排版 / 术语表 / 只译正文）…", hint:"Enter 发送 · 说「重译这段」→ 译文更新"},
  outline: {emoji:"🗂", name:"框架助手", sub:"大标题 + 小标题 + 一句说明，定稿写进框架.md", api:"/api/outline",
            ph:"说题目和目标 / 贴导师要求…", hint:"Enter 发送 · 说「更新框架」→ 左边立刻换新版"},
  map:     {emoji:"🧩", name:"谱系助手", sub:"按大纲把精读结论挂成树，改完立刻重画", api:"/api/map",
            ph:"说说怎么改，比如「按大纲建一版谱系图」…", hint:"Enter 发送 · 说「更新谱系」→ 画布立刻换新版"},
  write:   {emoji:"✍️", name:"写作助手", sub:"按框架逐节写，缺数据写「待补」，不编数字", api:"/api/write",
            ph:"说要求 / 让我写某一节…", hint:"Enter 发送 · 说「更新正文」→ 左边立刻换新版"},
  review:  {emoji:"🧐", name:"审查助手", sub:"以审稿人视角挑问题，先说大思路", api:"/api/review",
            ph:"说「审一遍」或问某处逻辑…", hint:"Enter 发送 · 说「更新正文」→ 左边立刻换新版"},
  entrans: {emoji:"🌍", name:"中译英助手", sub:"学术口径中译英，术语统一", api:"/api/agent",
            ph:"说目标期刊口径 / 要不要译图表标题…", hint:"Enter 发送 · 说「开始翻译」→ 译文落进产出"},
  convert: {emoji:"🔄", name:"格式助手", sub:"转 docx / pdf / tex，样式跟模板走", api:"/api/agent",
            ph:"说导出格式和模板…", hint:"Enter 发送 · 说「导出 docx」→ 文件落进产出"}
};
/* 这五步的面板本来就自带对话 —— 三栏页里对话统一收到右栏，面板里那份藏掉，避免两个聊天 */
const WF_OWN_CHAT = new Set(["idea", "map", "outline", "write", "review"]);
/* 进入某一步时右栏先亮出来的「这一步打算怎么做」—— 用户点「同意」才真正开跑。
   只写这一步的固定套路，具体条件都留给用户在对话里补，别在这里编数据。 */
const WF_PLAN = {
  idea: ["把你说的创新点拆成「问题 / 做法 / 凭什么新」三块", "对着现有文献和项目记忆，指出哪一块站不住", "出一版判定：能做 / 要收窄 / 换角度"],
  search: ["把你的目标翻成检索式（关键词 + 同义词 + 排除词）", "按年限 / 来源 / 是否 OA 粗筛，重复的和跑题的剔掉", "能拿到 OA 直链的一并补上，入库到左边列表"],
  download: ["先看队列里哪些还没全文", "有 OA 直链的直接下，没有的标成「待手动下载」给你链接", "下完逐页精读，结论写进项目笔记"],
  notes: ["按你给的口径定术语表（保留排版 / 只译正文 / 双语对照）", "逐段译，公式、图注、参考文献格式不动", "术语前后不一致的地方回头统一一遍"],
  outline: ["按题目 + 研究问题出「大标题 → 小标题 → 一句说明」", "每节标出要引哪几篇、哪个数据还缺", "定稿写进「框架.md」，左边立刻换新版"],
  map: ["把已精读的论文按大纲挂到对应节点", "同一节点下把互相打架的结论标出来", "缺文献的节点留空提示，别硬凑"],
  write: ["按框架逐节写正文，先写有数据的节", "缺数据的位置写「待补」，不编数字", "写完回头对一遍引用，正文编号和文献表对齐"],
  review: ["以审稿人视角先挑大问题（逻辑 / 证据 / 创新性）", "再看小节：统计口径、图注、引用是否站得住", "按严重程度列问题清单，给改法"],
  entrans: ["定目标期刊口径（美式拼写、时态、缩写首次展开）", "术语表先统一，再逐节译，图表标题一起译", "译完自查一遍中式表达"],
  convert: ["确认要哪种格式：docx / md / html，样式跟模板走", "图片路径、编号、参考文献一起处理", "文件落到项目产出目录，左边可预览"]
};

function wfAgentHTML(step){
  const a = WF_AGENT[step] || WF_AGENT.idea;
  const mdl = (S.active && (S.active.name || S.active.model)) || "未配置模型";
  return `<header class="at-head"><div class="at-heading"><span class="wf-av">${a.emoji}</span>
      <div><h1>${esc(a.name)}</h1><p>${esc(a.sub)}</p></div>
      <span class="wf-mdl" title="这一步用的模型">${esc(mdl)}</span></div></header>
    <div class="at-tabs">
      <button class="at-tab on" data-wv="conversation">对话 <span id="wv-turns">0</span></button>
      <button class="at-tab" data-wv="trajectory">轨迹 <span id="wv-calls">0</span></button></div>
    <div class="at-msgs" id="wv-msgs" role="log" aria-label="${esc(a.name)}对话"></div>
    <div class="at-dock">
      <div class="at-panel">
        <div class="st-box st-counts">
          <div class="st-row"><span>对话轮数</span><b id="wv-st-turns">0</b></div>
          <div class="st-row"><span>调用工具</span><b id="wv-st-calls">0</b></div>
          <div class="st-row"><span>消耗 token</span><b id="wv-st-tok">0</b></div></div>
        <div class="st-box"><p>工具名称</p><div class="st-log" id="wv-tools"></div></div>
        <div class="st-box"><p>产出目录</p><div class="st-log" id="wv-outs"></div></div></div>
      <div class="composer">
        <textarea id="wv-ask" rows="1" placeholder="${esc(a.ph)}（Enter 发送，Shift+Enter 换行）"></textarea>
        <div class="at-tools">
          <div class="at-tools-l"><button class="icon-btn" id="wv-plus" title="把这台机器上的文件以路径形式附上">＋</button></div>
          <div class="at-tools-r"><button class="send-btn" id="wv-send" title="发送" aria-label="发送">↑</button></div></div></div>
      <p class="at-hint">${esc(a.hint)}</p></div>`;
}

async function wfAgentPaint(m, step){
  const host = m.querySelector("#wfAgent"); if(!host) return;
  // 论文翻译的「笔记模式」：右栏这一列换成阅读笔记窄栏（点「助手模式」再换回来）
  if(step === "notes" && window.TR && TR.noteMode && typeof trPaintNoteColumn === "function"){
    trPaintNoteColumn();
    return;
  }
  host.innerHTML = wfAgentHTML(step);
  const a = WF_AGENT[step] || WF_AGENT.idea;
  const box = $("wv-msgs"); if(!box) return;
  wfMsgs = []; wfEvents = [];
  try{
    const d = await api(`/api/step/messages?id=${encodeURIComponent(curTask)}&step=${encodeURIComponent(step)}`);
    wfMsgs = d.messages || [];
  }catch(e){}
  wfEvents = wfMsgs.flatMap(mv => mv.events || []);
  wfPaintMsgs(step);
  wfPaintStats();
  const tabs = host.querySelectorAll("[data-wv]");
  tabs.forEach(b => b.onclick = ()=>{ wfView = b.dataset.wv; tabs.forEach(x=>x.classList.toggle("on", x===b)); wfPaintMsgs(step); });
  const ta = $("wv-ask"), send = $("wv-send");
  send.onclick = ()=> wfSend(m, step);
  ta.onkeydown = e=>{ if(e.key==="Enter" && !e.shiftKey){ e.preventDefault(); wfSend(m, step); } };
  ta.oninput = ()=>{ ta.style.height="auto"; ta.style.height=Math.min(ta.scrollHeight,120)+"px"; };
  const plus = $("wv-plus"); if(plus) plus.onclick = ()=>{ ta.value += (ta.value?"\n":"") + "（附件：把文件拖到这里或贴路径）"; ta.focus(); };
}

let wfView = "conversation";
/* 右栏空态 = 这一步的助手看着上一步的交付现写的「进度同步 + 本步计划」（后端 step_plan 落库，
   已有就直接读消息，不会重复烧模型）。plan 消息的 events 带 {type:"plan"}，最后一条才挂「同意」。 */
function wfPlanHTML(step, text, pending){
  const a = WF_AGENT[step] || WF_AGENT.idea;
  const body = text
    ? `<div class="md wf-plan-md">${md(text)}</div>`
    : `<p class="wf-plan-q">这一步我打算这么做，你看行不行：</p>
       <ol>${(WF_PLAN[step]||[a.sub]).map(x=>`<li>${esc(x)}</li>`).join("")}</ol>`;
  return `<div class="wf-plan">
    <div class="wf-plan-h"><span class="wf-av">${a.emoji}</span><b>${esc(a.name)}</b><span class="wf-plan-tag">已同步进度</span></div>
    ${body}
    ${pending ? `<div class="wf-plan-b"><button class="btn" id="wvPlanOk">同意，按这个开始</button>
      <button class="btn ghost" id="wvPlanNo">先说要改什么</button></div>` : ""}</div>`;
}
let wfPlanSeq = 0;
async function wfPlanLoad(step){
  const box = $("wv-msgs"); if(!box) return;
  const a = WF_AGENT[step] || WF_AGENT.idea;
  box.innerHTML = `<div class="wf-plan wf-plan-wait"><div class="wf-plan-h"><span class="wf-av">${a.emoji}</span><b>${esc(a.name)}</b></div>
    <p class="wf-plan-sync">正在读上一步的交付，写这一步的计划…</p></div>`;
  const my = ++wfPlanSeq;
  let text = "";
  try{
    // 没配 Key 就别去敲后端（会静默失败，一直停在「正在读…」）→ 直接落到 WF_PLAN 静态兜底
    if(hasModelKey()){ const d = await api("/api/step/plan", {task_id:curTask, step}); text = (d && d.text) || ""; }
  }catch(e){}
  if(my !== wfPlanSeq || wfStep !== step) return;          // 期间换步 / 又发了一轮 → 丢弃这次结果
  if($("wv-msgs") !== box) return;
  wfMsgs = [{role:"assistant", content:text, events:[{type:"plan"}], created_at:new Date().toISOString()}];
  wfPaintMsgs(step);
}
function wfPaintMsgs(step){
  const box = $("wv-msgs"); if(!box) return;
  if(wfView === "trajectory"){ box.innerHTML = ""; renderTrajectory(box, wfEvents); return; }
  if(!wfMsgs.length){ wfPlanLoad(step); return; }
  box.innerHTML = "";
  const who = WF_AGENT[step] || WF_AGENT.idea;          // 这一步的助手说话，不是智研管家
  wfMsgs.forEach((mv, i) => {
    if((mv.events||[]).some(e => e.type === "plan")){
      box.insertAdjacentHTML("beforeend", wfPlanHTML(step, mv.content, i === wfMsgs.length-1));
      return;
    }
    appendOne(box, {type:mv.role, content:mv.content, events:mv.events, replay:true, at:mv.created_at}, who);
  });
  const ok = $("wvPlanOk"), no = $("wvPlanNo"), ta = $("wv-ask");
  if(ok) ok.onclick = ()=>{
    ok.disabled = no.disabled = true;
    const bar = ok.closest(".wf-plan-b"); if(bar) bar.remove();   // 同意后这排按钮就收起来
    if(ta) ta.value = "同意，按这个计划开始。";
    wfSend(null, step);
  };
  if(no) no.onclick = ()=>{ if(ta) ta.focus(); };
  box.scrollTop = box.scrollHeight;
}
function wfPaintStats(){
  // 口径 = 这个助手自己（本步的对话 + 本步的事件流）。项目合计不再显示：
  // 右栏标题写的是某个具体助手，混进别步/对话页的数字就定位不到「这一步花了多少」。
  const turns = wfMsgs.filter(x=>x.role==="user").length;
  const calls = wfEvents.filter(e=>e.type==="tool_call");
  const tok = wfEvents.filter(e=>e.type==="usage").reduce((n,e)=>n+(e.total||0),0);
  const set = (id,v)=>{ const el=$(id); if(el) el.textContent = v; };
  set("wv-turns", turns); set("wv-calls", calls.length);
  set("wv-st-turns", turns); set("wv-st-calls", calls.length); set("wv-st-tok", tok.toLocaleString());
  const log = (id, rows, empty)=>{ const el=$(id); if(!el) return;
    el.innerHTML = rows.length ? rows.slice(-20).map(r=>`<div title="${esc(r)}">${esc(r)}</div>`).join("")
                               : `<div class="none">${esc(empty)}</div>`;
    el.scrollTop = el.scrollHeight; };
  log("wv-tools", calls.map(e=>e.name), "还没有工具调用");
  log("wv-outs", wfEvents.filter(e=>e.type==="tool_result"&&e.path).map(e=>e.path), "还没有产出");
}
async function wfSend(m, step){
  const ta = $("wv-ask"), btn = $("wv-send"), box = $("wv-msgs");
  const text = (ta.value||"").trim(); if(!text) return;
  if(!(await requireModelKey())) return;          // 没配 Key：先提示去设置，不发请求
  const a = WF_AGENT[step] || WF_AGENT.idea;
  if(step === "idea" && typeof iwFlush === "function") await iwFlush();   // 上区刚改的字先落库，这一轮就读到最新的
  ta.value = ""; ta.style.height = "auto";
  if(box.querySelector(".wf-empty")) box.innerHTML = "";
  appendOne(box, {type:"user", content:text});
  wfMsgs.push({role:"user", content:text, events:[]});
  wfPaintStats();                                  // 轮数当场 +1，不等这一轮跑完
  btn.disabled = true; btn.textContent = "■";
  const evs = [];
  try{
    const res = await fetch(a.api, {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({task_id:curTask, message:text, step:step})});
    if(!res.ok || !res.body) throw new Error("HTTP "+res.status);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "", final = "";
    for(;;){
      const {value, done} = await reader.read(); if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf("\n\n")) >= 0){
        const chunk = buf.slice(0,i); buf = buf.slice(i+2);
        const line = chunk.split("\n").find(l=>l.startsWith("data: ")); if(!line) continue;
        let ev; try{ ev = JSON.parse(line.slice(6)); }catch{ continue; }
        if(ev.type === "done"){ final = ev.content || ""; continue; }
        if(ev.type === "user") continue;                 // 本地已经先画了，回声不再画、也不进事件流
        appendOne(box, ev, a); evs.push(ev);
        // 边跑边记账：wfEvents 是统计的唯一来源，所以进来就 push，别攒到这一轮结束。
        // 只在这三种事件上重画（content 是逐段文本，每段都重画笔工具/产出列表太重）。
        wfEvents.push(ev);
        if(ev.type === "usage" || ev.type === "tool_call" || ev.type === "tool_result") wfPaintStats();
      }
    }
    if(final && !evs.some(e=>e.type==="assistant")){ appendOne(box, {type:"assistant", content:final}, a); evs.push({type:"content", content:final}); }
  }catch(err){
    appendOne(box, {type:"error", message:"发送失败：" + err.message});
  }finally{
    btn.disabled = false; btn.textContent = "↑";
    wfMsgs.push({role:"assistant", content:"", events:evs});   // evs 已经逐条进过 wfEvents，别再整份 concat（会双份）
    wfPaintStats();
    box.scrollTop = box.scrollHeight;
    if(step === "search") wsLoadState();      // 助手检索完 → 左边结果立刻回填
    if(step === "convert") cvLoad();          // 助手导出完 → 左边文件/预览立刻刷新
    if(step === "idea" && typeof iwLoad === "function") iwLoad();   // 刚出的判定 → 左边下区刷新
  }
}

function renderWorkflow(m){
  m.style.padding = "";        // 清掉别的页面留下的内边距
  const proj = (S.tasks||[]).find(t => t.id === curTask);
  m.innerHTML = needTask() || `
    <div class="wf-page${wfMin?" min":""}" id="wfPage">
      <aside class="wf-rail" id="wfRail">
        <div class="wf-proj"><small>项目名</small><b>${esc(proj?proj.title:"未命名项目")}</b></div>
        <button class="wf-fold" id="wf-collapse" title="${wfMin?"展开步骤":"折叠步骤"}">&lt; 折叠</button>
        ${WF_ORDER.map(s=>`<button class="wf-step${s===wfStep?" on":""}" data-step="${s}" title="${STAGE[s]}">
            <span class="wf-ico">${STAGE[s].split(" ")[0]}</span><span class="wf-lbl">${STAGE[s].slice(STAGE[s].indexOf(" ")+1)}</span>
            <span class="wf-dot"></span></button>`).join("")}
      </aside>
      <main class="wf-stage" id="wfStage">
        <div class="wf-panels">${WF_ORDER.map(s=> PANELS[s] ? PANELS[s](s===wfStep)
          : `<div class="card" id="panel-${s}"${s===wfStep?"":' style="display:none"'}><h3>${STAGE[s]}</h3><div id="out-${s}"></div></div>`).join("")}</div>
        <div class="wf-foot">
          <span class="wf-pill" id="wfFootPill">第 1 / 10 步</span>
          <span class="hint" id="wfFootHint">确认后这一步锁定，下一步才能拿它当输入。</span>
          <span style="flex:1"></span>
          <button class="btn ghost" id="wfBack">退回上一步</button>
          <span class="wf-sep"></span>
          <button class="btn primary" id="wfDeliver">确认交付 →</button></div>
      </main>
      <aside class="wf-agent" id="wfAgent"></aside>
    </div>`;
  if(!curTask) return;
  const wfc = m.querySelector("#wf-collapse");
  if(wfc) wfc.onclick = ()=>{ wfMin=!wfMin; localStorage.setItem("rw-wf-min",wfMin?"1":"0"); renderWorkflow(m); };
  const bindStep = ()=>{
    if(wfStep==="idea") iwBind(m);
    if(wfStep==="search") wsBind(m);
    if(wfStep==="download") dlBind(m);
    if(wfStep==="notes") trBind(m);
    if(wfStep==="map") mpBind(m);
    if(DOC_UI[wfStep]) docBind(wfStep, m);
    if(wfStep==="convert") cvBind();
    if(wfStep==="entrans") enBind();
  };
  m.querySelectorAll(".wf-step").forEach(b=>b.onclick=()=>{
    wfStep = b.dataset.step; localStorage.setItem("rw-wf-step", wfStep);
    m.querySelectorAll(".wf-step").forEach(x=>x.classList.toggle("on", x.dataset.step===wfStep));
    WF_ORDER.forEach(s=>{ const p=$("panel-"+s); if(p) p.style.display = s===wfStep ? "" : "none"; });
    bindStep(); wfAgentPaint(m, wfStep); wfPaintFoot();
  });
  const back = $("wfBack"); if(back) back.onclick = ()=>{
    const i = WF_ORDER.indexOf(wfStep); if(i > 0) m.querySelector(`.wf-step[data-step="${WF_ORDER[i-1]}"]`)?.click();
  };
  const deliver = $("wfDeliver"); if(deliver) deliver.onclick = async ()=>{
    deliver.disabled = true; const old = deliver.textContent;
    const leaving = wfStep;
    try{ await api("/api/step/done", {task_id:curTask, step:wfStep}); }catch(e){ deliver.disabled=false; return uiNotify("确认失败：" + e.message); }
    // 论文搜索交付 = 进下载：勾中的这批当场提交自动下载（没勾就只跳步）
    const downloading = leaving === "search" ? wsKickDownload() : false;
    // 先让按钮绿一下（「点下去有反应」），再跳下一步；跳完 restoring 文字
    deliver.classList.add("ok"); deliver.textContent = "已交付 ✓";
    await loadTaskState();
    await new Promise(r => setTimeout(r, 450));
    const i = WF_ORDER.indexOf(wfStep), next = WF_ORDER[i+1];
    deliver.classList.remove("ok"); deliver.textContent = next ? old : "已到最后一步 ✓";
    deliver.disabled = false;
    if(next) m.querySelector(`.wf-step[data-step="${next}"]`)?.click();   // 顺带把右栏换成下一步的进度同步 + 计划
    if(downloading) startDlPoll();                                        // 下载进度条在「下载精读」页里转
  };
  bindStep();
  wfAgentPaint(m, wfStep);
  wfPaintFoot();
  loadTaskState();
}
function wfPaintFoot(){
  const a = WF_AGENT[wfStep];
  const hint = $("wfFootHint"), pill = $("wfFootPill");
  const name = (STAGE[wfStep]||wfStep).slice((STAGE[wfStep]||"").indexOf(" ")+1);
  if(pill) pill.textContent = `第 ${WF_ORDER.indexOf(wfStep)+1} / ${WF_ORDER.length} 步 · ${name}`;
  if(hint && a) hint.textContent = `右侧「${a.name}」的产出看着没问题就交付`;
}
function iwBind(m){
  const ta = $("iwMine"); if(!ta) return;
  let t0 = 0;
  const save = async ()=>{
    const st = $("iw-mine-state"); if(st) st.textContent = "保存中…";
    try{ await api("/api/idea/input", {task_id: curTask, text: ta.value}); if(st) st.textContent = "已保存"; }
    catch(e){ if(st) st.textContent = "保存失败：" + e.message; }
  };
  ta.oninput = ()=>{ const st = $("iw-mine-state"); if(st) st.textContent = "未保存…";
    clearTimeout(t0); t0 = setTimeout(save, 700); };
  ta.onblur = ()=>{ clearTimeout(t0); save(); };
  iwLoad();
}
async function iwFlush(){
  /* 送消息之前先把上区刚改的字落库 —— 右栏助手这一轮就要读到最新的 */
  const ta = $("iwMine"); if(!ta || !curTask) return;
  try{ await api("/api/idea/input", {task_id: curTask, text: ta.value}); }catch(_){}
}
function iwVerdictPaint(v){
  const box = $("iwVerdict"), at = $("iw-verdict-at"); if(!box) return;
  if(!v || !v.markdown){
    box.innerHTML = `<p class="ol-empty">还没有判定 —— 在右边跟助手把约束问清，说「出一版判定」，结果落在这里。</p>`;
    if(at) at.textContent = "";
    return;
  }
  box.innerHTML = renderMarkdown(v.markdown);
  if(at) at.textContent = v.created_at ? `更新于 ${fmtDate(v.created_at)}` : "";
}
async function iwLoad(){
  if(!curTask) return;
  try{
    const d = await api(`/api/idea/memory?id=${encodeURIComponent(curTask)}`);
    const ta = $("iwMine");
    if(ta && document.activeElement !== ta && ta.value !== (d.mine || "")) ta.value = d.mine || "";
    iwVerdictPaint(d.verdict);
  }catch(e){}
}

/* ==== 论文搜索（照科研工作台 literature.tsx「联网检索」：工具条 + 筛选行 + 结果面板）==== */
const WS_LABEL = {openalex:"OpenAlex",crossref:"Crossref",arxiv:"arXiv",pubmed:"PubMed"};
let wsRes=[], wsPage=1, wsRows=20, wsNote="";
let wsPicked = new Set();                       // 论文搜索里勾选的 result id（进「下载精读」处理）
let dlPoll = null, dlBusy = false;
let dlSt = null, dlLastQueue = null;      // 最近一次精读状态 / 最近一次队列（进度刻格的事实来源）
const wsFiltered = ()=> (wsRes||[]).slice();   // 没有手动筛选了：检索条件都交给右栏助手
function searchPanelHTML(active){
  return `<div class="ws" id="panel-search"${active?"":' style="display:none"'}>
    <div class="ws-results">
      <div class="ws-head"><p class="eyebrow">SEARCH / FREE SOURCES</p>
        <h1>联网检索<span id="wsCount" class="hint" style="font-size:12px;font-weight:400"></span></h1>
        <p class="hint">在右边助手说方向和年份（例：2020–2024 钠电 O3 型 NFM 掺杂），它并行检索 OpenAlex、Crossref、arXiv（只走开放获取）；结果回填到这里，勾选后可下载入库。</p></div>
      <div class="ws-list" id="wsList"></div>
      <div class="ws-foot">
        <label class="ws-field"><input type="checkbox" id="wsAll" style="width:15px;height:15px;accent-color:var(--accent)"><span>全选本页</span></label>
        <span class="ws-pickinfo" id="wsPickN">已勾选 0</span>
        <span id="wsNote"></span>
        <span class="ws-pageinfo" id="wsPageInfo"></span>
        <button class="ws-navbtn" id="wsPrev" title="上一页">‹</button>
        <button class="ws-navbtn" id="wsNext" title="下一页">›</button>
      </div>
    </div>
  </div>`;
}
/* ---------------- 下载精读：勾选 → 自动下载（文献库）/ 待手动下载清单 → 一键精读（笔记） ---------------- */
function downloadPanelHTML(active){
  return `<div class="dl-page" id="panel-download"${active?"":' style="display:none"'}>
    <div class="dl-loc"><p class="k">论文下载位置（项目文件夹）</p><p class="v" id="dlLoc">—</p></div>
    <div class="dl-stats">
      <div class="dl-stat"><p class="k">已勾选论文总数</p><p class="v" id="dlSel">0</p></div>
      <div class="dl-stat"><p class="k">已下载论文总数</p><p class="v" id="dlDone">0</p></div>
    </div>
    <div class="dl-box">
      <div class="h"><span>待手动下载清单<span class="hint" id="dlN"></span></span>
        <span class="hint" id="dlTip">无 OA 直链、或自动下载被挡（403/落地页）的论文都在这里</span></div>
      <div class="ws-list" id="dlList"></div>
    </div>
    <div class="dl-foot"><div class="dl-prog" id="dlProg" hidden></div><button class="btn" id="dlRead">一键精读</button></div>
  </div>`;
}
function dlBind(m){
  if(!$("dlList")) return;
  if(dlPoll){ clearInterval(dlPoll); dlPoll=null; }
  $("dlRead").onclick = dlRead;
  if($("dlList").onclick){
    $("dlList").onclick = (e)=>{
      const c = e.target.closest("[data-copy]"); if(!c) return;
      navigator.clipboard.writeText(c.dataset.copy);
      c.textContent = "已复制 ✓"; setTimeout(()=>c.textContent="复制 DOI", 1200);
    };
  }
  dlRefresh();
  dlResumeIfRunning();
}
async function dlResumeIfRunning(){
  /* 后端在跑就绝不能装没在跑：进这一页先问一次状态，running 就直接接上轮询、按钮切成「停止精读」。
     否则刷新或换页回来只看到「一键精读」+ 没进度，就像它自己停了 —— 实际它还在读。 */
  if(!curTask || dlReadBusy) return;
  try{
    const st = await api("/api/deepread/status?task_id=" + encodeURIComponent(curTask));
    dlSt = st && st.phase ? st : null;
    dlProgPaint();
    if(!dlSt || dlSt.phase !== "running") return;
    dlReadBusy = true;
    const b = $("dlRead");
    if(b){ b.classList.add("stop"); b.textContent = "停止精读"; }
    if(!dlReadTick) dlReadTick = setInterval(dlReadPoll, 2000);
  }catch(e){ /* 读不到状态就等用户自己点 */ }
}
async function dlRefresh(){
  if(!curTask || !$("dlList")) return null;
  try{ const d = await api("/api/workflow/queue",{task_id:curTask}); dlPaint(d); return d; }
  catch(e){ const t=$("dlTip"); if(t) t.textContent = "读取失败：" + e.message; return null; }
}
function dlPaint(d){
  if(!d) return;
  dlLastQueue = d;
  if($("dlLoc")) $("dlLoc").textContent = d.location || "—";
  if($("dlSel")) $("dlSel").textContent = d.selected || 0;
  if($("dlDone")) $("dlDone").textContent = d.downloaded || 0;
  dlProgPaint();
  const man = d.manual || [];
  if($("dlN")) $("dlN").textContent = man.length ? ` · ${man.length}` : "";
  const list = $("dlList"); if(!list) return;
  list.innerHTML = man.length ? man.map(r=>wsCard(r,"manual")).join("")
    : `<div style="display:grid;place-items:center;height:100%;padding:30px 16px;text-align:center">
        <div><p class="hint" style="font-size:13px;margin:0 0 6px">${d.selected? "这批论文全部自动下载成功 ✓" : "还没有勾选论文"}</p>
        <p class="hint">${d.selected? `${d.downloaded} 篇已进「文献库」，可以直接点下面的「一键精读」。` : "在「论文搜索」里勾选论文，然后点底部「确认交付 →」，勾中的这批会直接开始下载。"}</p></div></div>`;
}
function dlProgPaint(){
  /* 精读进度刻格：一格代表若干篇（篇数多就合并，最多 24 格），状态全部来自 /api/deepread/status：
     绿=已精读完，红=失败过，青=正在读的那篇。没跑过精读就用「已下载篇数」画空格，不编造进度。 */
  const box = $("dlProg"); if(!box) return;
  const st = dlSt && dlSt.phase ? dlSt : null;
  const total = (st && st.total) || (dlLastQueue && dlLastQueue.downloaded) || 0;
  if(!total){ box.hidden = true; box.innerHTML = ""; return; }
  const done = st ? (st.done || 0) : 0;          // done 把失败的也算进去了，所以要先刨掉
  const failed = st ? (st.failed || 0) : 0;
  const ok = Math.max(0, done - failed);
  const running = !!st && st.phase === "running";
  const cells = Math.min(total, 24);
  const step = total / cells;
  let html = "";
  for(let i = 0; i < cells; i++){
    const a = Math.round(i * step), b = Math.round((i + 1) * step);      // 这一格覆盖 [a,b) 篇
    // 失败的那几篇排在 [ok, ok+failed)，成功的是 [0, ok) —— 取交集判这一格，不能用累计值直接比
    const badIn = Math.max(0, Math.min(ok + failed, b) - Math.max(ok, a));
    const okIn = Math.min(ok, b) - Math.min(ok, a);
    const curIn = running && ok + failed >= a && ok + failed < b;
    const cls = badIn > 0 ? "bad" : curIn ? "cur" : (b > a && okIn >= b - a ? "ok" : "");
    html += `<i${cls ? ` class="${cls}"` : ""}></i>`;
  }
  const tail = st && st.phase === "running" ? " · 进行中"
             : st && st.phase === "done" ? " · 已完成"
             : st && st.phase === "stopped" ? " · 已停止" : "";
  box.innerHTML = `<span class="dots">${html}</span><span class="n"><b>${ok}</b>/${total}${tail}</span>`
                + (failed ? `<span class="bad-txt">${failed} 失败</span>` : "");
  box.hidden = false;
}
function startDlPoll(){
  if(dlPoll) clearInterval(dlPoll);
  const t0 = Date.now();
  dlPoll = setInterval(async ()=>{
    const d = await dlRefresh();
    if(dlPoll && ((!dlBusy && (!d || !d.pending)) || Date.now()-t0 > 15*60*1000)){ clearInterval(dlPoll); dlPoll=null; }
  }, 1500);
}
function wsKickDownload(){                     // 勾了的这批当场提交自动下载；跳步由「确认交付」负责
  if(!curTask || !wsPicked.size) return false;
  dlBusy = true;
  api("/api/workflow/download",{task_id:curTask, result_ids:[...wsPicked]})
    .then(r=>{ dlBusy=false; const t=$("dlTip"); if(t && r) t.textContent = `自动下载 ${r.downloaded}/${r.selected} 篇，待手动 ${r.manual} 篇`; })
    .catch(e=>{ dlBusy=false; uiNotify("自动下载失败：" + e.message); });
  return true;
}
let dlReadBusy = false, dlReadTick = null;
async function dlRead(){                       // 后台精读：起完就返回；进度靠轮询；按钮再点一次=停
  if(!curTask) return;
  const btn = $("dlRead"); if(!btn) return;
  if(dlReadBusy){
    try{ await api("/api/deepread/stop",{task_id:curTask}); btn.textContent = "正在停…（当前这篇读完就停）"; }
    catch(e){ uiNotify("停止失败：" + e.message); }
    return;
  }
  try{
    const r = await api("/api/workflow/deepread",{task_id:curTask});
    if(!r.started){ uiNotify(r.reason || "这个项目已经在精读了"); return; }
    dlReadBusy = true; dlSt = null; btn.classList.add("stop"); btn.textContent = "停止精读";
    dlReadTick = setInterval(dlReadPoll, 2000);
  }catch(err){ uiNotify("精读失败：" + err.message); }
}
async function dlReadPoll(){
  try{
    const st = await api("/api/deepread/status?task_id=" + encodeURIComponent(curTask));
    if(!st || !st.phase) return;
    dlSt = st; dlProgPaint();
    const tip = $("dlTip"), b2 = $("dlRead");
    const head = "精读 " + st.done + "/" + (st.total || "?") + " 篇" + (st.failed ? "（失败 " + st.failed + "）" : "")
               + ((st.skipped && st.skipped.length) ? " · 跳过非论文 " + st.skipped.length : "");
    if(tip) tip.textContent = st.phase === "running" ? head + " · 正在读 " + (st.current || "") : head;
    if(st.phase === "running") return;
    clearInterval(dlReadTick); dlReadTick = null; dlReadBusy = false;
    if(b2){
      b2.classList.remove("stop");
      b2.textContent = st.phase === "done" ? "精读完了（去看笔记）" : (st.phase === "stopped" ? "已停止" : "精读失败");
      setTimeout(()=>{ const b3 = $("dlRead"); if(b3 && !dlReadBusy) b3.textContent = "一键精读"; }, 5000);
    }
    if(st.phase === "failed" && st.error) uiNotify("精读失败：" + st.error);
  }catch(e){ /* 轮询失败就等下一次 */ }
}
function wsCard(r, mode){
  const search = mode !== "manual";
  const pick = search ? `<label class="ws-pick" title="勾选后进「下载精读」处理"><input type="checkbox" data-pick="${r.id}"${wsPicked.has(r.id)?" checked":""}></label>` : "";
  const acts = search
    ? `${r.url?`<a class="btn ghost" href="${esc(r.url)}" target="_blank" rel="noopener" style="text-decoration:none">来源</a>`:""}
       ${r.pdf_url?`<button class="btn" data-dl="${r.id}">下载 OA</button>`:`<span class="hint">无 OA 全文</span>`}`
    : `${r.status==="downloaded"?`<span class="ws-chip ok">已下载</span>`:`<span class="ws-chip need">待手动下载</span>`}
       ${r.url?`<a class="btn ghost" href="${esc(r.url)}" target="_blank" rel="noopener" style="text-decoration:none">去来源页</a>`:""}
       ${r.doi?`<button class="btn ghost" data-copy="${esc(r.doi)}">复制 DOI</button>`:""}`;
  return `<div class="ws-row">
      ${pick}
      <div class="ws-row-main">
        <h3>${esc(r.title||"（无标题）")}</h3>
        <p class="ws-meta">${esc(r.authors||"作者未知")} · ${esc(WS_LABEL[r.source]||r.source||"")} · ${esc(r.year||"年份未知")}${r.doi?` · ${esc(r.doi)}`:""}</p>
        ${!search && r.error?`<p class="ws-meta" style="color:var(--err)">${esc(r.error)}</p>`:""}
        ${r.abstract?`<p class="ws-abs" title="${esc(r.abstract)}">${esc(r.abstract)}</p>`:""}</div>
      <div class="ws-row-actions" data-id="${r.id}">${acts}</div>
    </div>`;
}
function wsSyncPick(){
  const n = wsPicked.size, el = $("wsPickN");
  if(el) el.textContent = `已勾选 ${n}`;
  const all = $("wsAll");
  if(all){ const vis = wsFiltered().slice((wsPage-1)*wsRows, wsPage*wsRows);
    all.checked = vis.length>0 && vis.every(r=>wsPicked.has(r.id));
    all.indeterminate = !all.checked && vis.some(r=>wsPicked.has(r.id)); }
}
async function wsPaint(){
  const listEl=$("wsList"); if(!listEl) return;
  const per = wsRows; const all = wsFiltered();
  const pages = Math.max(1, Math.ceil(all.length/per));
  wsPage = Math.min(Math.max(1, wsPage), pages);
  const slice = all.slice((wsPage-1)*per, wsPage*per);
  listEl.innerHTML = slice.length ? slice.map(r=>wsCard(r,"search")).join("")
    : `<div style="display:grid;place-items:center;height:100%;padding:30px 16px;text-align:center">
        <div><p class="hint" style="font-size:13px;margin:0 0 6px">暂无检索结果</p>
        <p class="hint">在右边助手说方向和年份，它检索完结果会回填到这里；同一个项目里会累积。</p></div></div>`;
  $("wsCount").textContent = all.length ? ` · ${all.length}` : "";
  $("wsNote").textContent = wsNote || (wsRes.length? `项目里累计 ${wsRes.length} 条检索结果` : "");
  wsSyncPick();
  $("wsPageInfo").textContent = all.length ? `第 ${wsPage} / ${pages} 页 · 每页 ${per}` : "";
  $("wsPrev").disabled = wsPage<=1; $("wsNext").disabled = wsPage>=pages;
}
async function wsLoadState(){
  if(!curTask) return;
  try{ const d = await api(`/api/task?id=${encodeURIComponent(curTask)}`);
       wsRes = d.results || []; }catch(e){}
  wsPaint();
}
function wsBind(m){
  if(!$("wsList")) return;
  $("wsPrev").onclick = ()=>{ wsPage--; wsPaint(); };
  $("wsNext").onclick = ()=>{ wsPage++; wsPaint(); };
  $("wsList").addEventListener("change", (e)=>{
    const cb = e.target.closest("[data-pick]"); if(!cb) return;
    if(cb.checked) wsPicked.add(cb.dataset.pick); else wsPicked.delete(cb.dataset.pick);
    wsSyncPick();
  });
  $("wsAll").onchange = (e)=>{
    const vis = wsFiltered().slice((wsPage-1)*wsRows, wsPage*wsRows);
    vis.forEach(r=> e.target.checked ? wsPicked.add(r.id) : wsPicked.delete(r.id));
    wsPaint();
  };
  $("wsList").onclick = async (e)=>{
    const b = e.target.closest("[data-dl]"); if(!b) return;
    const act = b.parentElement; const old = b.textContent;
    b.disabled = true; b.textContent = "下载中…";
    try{ const r = await api("/api/workflow/download",{task_id:curTask, result_id:b.dataset.dl});
         const p = r.paper||{};
         act.innerHTML = `<span class="hint">已入库 ✓ ${esc(p.filename||"")}（${p.pages||0} 页）</span>`;
    }catch(err){ b.disabled=false; b.textContent=old; act.insertAdjacentHTML("beforeend", `<span style="color:var(--err);font-size:11.5px">${esc(err.message)}</span>`); }
  };
  wsLoadState();
}

async function loadTaskState(){
  try{ const d=await api(`/api/task?id=${encodeURIComponent(curTask)}`);
    const sm=d.stage_map||{}; Object.keys(STAGE).forEach(s=>{ const el=$("st-"+s); if(el&&sm[s]) el.className="pill ok"; });
    // 左栏十步：已完成的亮、没开始的暗、当前步主色
    document.querySelectorAll(".wf-step[data-step]").forEach(b=>{
      const s=b.dataset.step, st=sm[s];
      b.classList.toggle("done", !!st && st!=="failed");
      b.classList.toggle("todo", !st && s!==wfStep);
      if(st==="failed") b.classList.add("fail");
    });
  }catch(e){}
}
/* ---------------- 项目 ---------------- */
function renderTasks(m){
  // 卡片色带 2026-10-08：原来按顺序轮换莫兰迪六色（豆沙/米驼那组偏暖），与主题脱节 → 撤掉，统一吃 --accent。
  const kws = t => String(t.material_system || "").split(/[，,、;；]+/).map(s => s.trim()).filter(Boolean)
    .slice(0, 4).map(s => `<span class="kw">${esc(s)}</span>`).join("");
  m.innerHTML = `
    <div class="pj-page">
      <h1>项目</h1>
      <div class="sub">每个课题一个托盘 —— 卡片颜色跟着项目走，一眼认出做的是哪摊</div>
      <div class="card proj-new pj-new"><h3>新建项目</h3>
        <div class="row">
          <label><span>项目标题</span>
            <input type="text" id="t-title" placeholder="例：锂离子正极掺杂研究"></label>
          <label><span>关键词</span>
            <input type="text" id="t-kw" placeholder="例：钠离子，层状正极，NFM"></label>
          <label class="full"><span>研究问题</span>
            <textarea id="t-q" rows="2" placeholder="一句话说清主线与创新点"></textarea></label>
          <label class="full"><span>项目位置</span>
            <div class="pf-dir" id="t-dir-box"><span class="dp" id="t-dir">尚未选择文件夹（可选，选好会自动建好六个子文件夹）</span>
              <button class="btn" id="t-dir-pick" type="button">＋ 选择文件夹</button></div></label>
        </div>
        <div class="pf-actions" style="margin-top:10px"><button class="btn" id="t-go">创建项目</button></div></div>
    <div class="card pj-sec"><h3>${S.tasks.length} 个项目</h3>${S.tasks.length?
      `<div class="proj-cards">${S.tasks.map((t, i) => `<div class="proj-row ${t.id===curTask?"cur":""}">
        <div class="nm">${esc(t.title)}</div>
        <div class="q">${esc(t.question||"未填研究问题")}</div>
        <div class="kws">${kws(t)}</div>
        <div class="meta"><span class="st">${esc(t.stage_now||"—")}</span>
          <button class="go" data-go="${t.id}">进入 →</button>
          <span class="tm">${fmtDate(t.updated_at)}</span>
          <button class="go del" data-del="${t.id}" title="删除项目">删除</button></div></div>`).join("")}
        <div class="proj-add">＋ 新建项目</div></div>`
      :`<div class="hint">还没有项目</div>`}</div>
    </div>`;   // /.pj-page
  m.querySelectorAll(".proj-row").forEach(r=>r.onclick=()=>{           // 点整行 = 切换当前项目（不动页面）
    const id=[...r.querySelectorAll("[data-go]")][0]?.dataset.go; if(!id) return;
    curTask = id; localStorage.setItem("rw-task",curTask); render();
  });
  m.querySelectorAll("[data-go]").forEach(b=>b.onclick=(e)=>{          // 进入项目 → 切当前 + 进科研平台（十步工作流）
    e.stopPropagation(); curTask = b.dataset.go; localStorage.setItem("rw-task",curTask); openTab("workflow");
  });
  m.querySelectorAll("[data-del]").forEach(b=>b.onclick=async (e)=>{    // 删除项目（连带数据，先确认）
    e.stopPropagation();
    const t = S.tasks.find(x=>x.id===b.dataset.del);
    if(!await uiConfirm(`删除项目「${t?t.title:""}」？\n它的文献、证据、笔记、会话、日程、步骤都会一起删掉，不可撤销。`)) return;
    try{ await api("/api/task/delete",{id:b.dataset.del}); }catch(err){ return uiNotify("删除失败："+err.message); }
    if(curTask===b.dataset.del){ curTask=null; localStorage.removeItem("rw-task"); }
    await refreshState(false); render();
  });
  const addCard = m.querySelector(".proj-add");                    // 「＋ 新建项目」卡 = 把焦点送回上面的表单
  if(addCard) addCard.onclick = ()=>{ const t = $("t-title"); t.scrollIntoView({block:"center",behavior:"smooth"}); t.focus(); };
  $("t-dir-pick").onclick = async ()=>{
    const btn = $("t-dir-pick"); const old = btn.textContent; btn.disabled = true; btn.textContent = "选择中…";
    try{
      const r = await api("/api/folder/pick?title=" + encodeURIComponent("选择项目位置") +
                          "&initial=" + encodeURIComponent(tDir||""), {method:"GET"});
      if(r && r.dir){ tDir = r.dir; $("t-dir").textContent = tDir; $("t-dir-box").classList.add("set"); }
    }catch(e){ uiNotify("打开文件夹选择框失败：" + e.message); }
    btn.disabled = false; btn.textContent = old;
  };
  $("t-go").onclick = async ()=>{
    const title=$("t-title").value.trim(); const question=$("t-q").value.trim();
    if(!title) return uiNotify("标题必填");
    const r = await api("/api/task/create",{title,question,material_system:$("t-kw").value.trim(),base_dir:tDir});
    curTask = r.task_id; localStorage.setItem("rw-task",curTask);
    tDir = "";
    await refreshState(false); render();
    if(r.dirs && r.dirs.length) uiNotify(`项目已创建，并在项目位置里建好：\n${r.dirs.join(" / ")}\n（每个子文件夹里都写了 README.md）`);
  };
}

/* ---------------- 日历：版式/类名照 lit（app.js 的 calendarDateInfo/renderCalendar/renderCalDayEvents + index.html 的 home-mid/home-bottom），只多了年/月下拉 ---------------- */
let calY = new Date().getFullYear(), calM = new Date().getMonth();   // calM 0-11
let calSel = today();                                                // 选中的日子（左边点哪天，右边就显示哪天）
let calPrefs = {lunar:true, solarTerms:true, festivals:true};         // 「历法」弹窗里那三个开关
const CAL_TYPES = {mine:{color:"var(--accent)",label:"日程"},festival:{color:"#c2415d",label:"节日"},solarTerm:{color:"#27845f",label:"节气"}};
const pad2 = n => String(n).padStart(2,"0");
const dkey = (y,m,d) => `${y}-${pad2(m+1)}-${pad2(d)}`;
const hhmm = t => String(t||"").replace("T"," ").slice(5,16);

function calendarDateInfo(dateKey){
  if(!window.Solar || !/^\d{4}-\d{2}-\d{2}$/.test(dateKey)) return {label:"",events:[]};
  try{
    const [y,mo,d] = dateKey.split("-").map(Number);
    const solar = window.Solar.fromYmd(y,mo,d), lunar = solar.getLunar();
    const term = calPrefs.solarTerms ? lunar.getJieQi() : "";
    const festivals = calPrefs.festivals ? [...solar.getFestivals(), ...lunar.getFestivals()].filter(Boolean) : [];
    let lunarLabel = "";
    if(calPrefs.lunar) lunarLabel = lunar.getDay() === 1 ? `${lunar.getMonthInChinese()}月` : lunar.getDayInChinese();
    return {label: term || festivals[0] || lunarLabel,
      events: [...(term?[{type:"solarTerm",label:term,computed:true}]:[]), ...[...new Set(festivals)].map(label=>({type:"festival",label,computed:true}))]};
  }catch(_){ return {label:"",events:[]}; }
}
function calEventsByDate(){
  const map = {};
  for(const e of S.events) (map[e.date] = map[e.date] || []).push({type:"mine",label:e.title,id:e.id,done:e.done});
  return map;
}
function renderCalendar(m){
  const y = calY, mo = calM, t0 = today();
  const lead = (new Date(y,mo,1).getDay()+6)%7;                       // 周一=0
  const daysIn = new Date(y,mo+1,0).getDate(), prevDays = new Date(y,mo,0).getDate();
  const cells = [];
  for(let i=lead-1;i>=0;i--) cells.push({d:prevDays-i,out:true,off:-1});
  for(let d=1;d<=daysIn;d++) cells.push({d,out:false,off:0});
  const trail = (7-(cells.length%7))%7;
  for(let d=1;d<=trail;d++) cells.push({d,out:true,off:1});
  const events = calEventsByDate();
  const grid = cells.map(c=>{
    const mm = mo+c.off, yy = y+Math.floor(mm/12), nm = ((mm%12)+12)%12;
    const key = dkey(yy,nm,c.d);
    const info = calendarDateInfo(key);
    const evs = [...(events[key]||[]), ...info.events];
    const dots = evs.slice(0,3).map(e=>`<i class="cal-dot" style="background:${CAL_TYPES[e.type].color}"></i>`).join("");
    const more = evs.length>3 ? `<i class="cal-more">+${evs.length-3}</i>` : "";
    const cls = ["cal-cell", c.out?"out":"", key===t0?"today":"", key===calSel?"selected":""].filter(Boolean).join(" ");
    return `<div class="${cls}" data-date="${key}" title="${esc(evs.map(e=>e.label).join("；"))}"><span class="cal-num">${c.d}</span>${info.label?`<span class="cal-lunar">${esc(info.label)}</span>`:""}<span class="cal-dots">${dots}${more}</span></div>`;
  }).join("");
  const selEvs = [...(events[calSel]||[]), ...calendarDateInfo(calSel).events];
  const dayItems = S.events.filter(e=>e.date===calSel)
    .sort((a,b)=> (a.time||"99:99").localeCompare(b.time||"99:99") || String(a.created_at).localeCompare(String(b.created_at)));   // 按当日钟点从上到下排，没定时间的垫底
  const now = new Date();
  const yearOpts = Array.from({length:11},(_,i)=>now.getFullYear()-5+i).map(v=>`<option value="${v}" ${v===y?"selected":""}>${v}</option>`).join("");
  const monthOpts = Array.from({length:12},(_,i)=>`<option value="${i}" ${i===mo?"selected":""}>${i+1}月</option>`).join("");
  const doneEvs = S.events.filter(e=>e.done).sort((a,b)=>String(b.created_at||"").localeCompare(String(a.created_at||""))).slice(0,30);
  const soon = S.events.filter(e=>!e.done && e.date>=t0).sort((a,b)=>a.date.localeCompare(b.date));
  const DAY = 86400000, base = new Date(t0+"T00:00:00").getTime();

  m.innerHTML = `<div class="cal-page">
    <div class="cal-mid">
      <div class="cal-left">
      <div class="card cal-hello">
        <span class="hello-avatar" id="calHelloAvatar">${avatarInner()}</span><b id="calHelloText">${helloText()}</b></div>
      <div class="card">
        <div class="card-head"><b>📅 科研日历</b>
          <div class="cal-nav">
            <button class="cal-nav-btn" id="calOptions" title="历法显示设置">历法</button>
            <button class="cal-nav-btn" id="calPrev" title="上个月">‹</button>
            <select id="calY" title="选择年份">${yearOpts}</select>
            <select id="calM" title="选择月份">${monthOpts}</select>
            <button class="cal-nav-btn" id="calNext" title="下个月">›</button>
            <button class="cal-nav-btn cal-today-btn" id="calToday">今天</button>
          </div>
          <div id="calOptionsPop" class="cal-pop hidden">
            <label><input type="checkbox" data-cal-pref="lunar" ${calPrefs.lunar?"checked":""}> 显示农历日期</label>
            <label><input type="checkbox" data-cal-pref="solarTerms" ${calPrefs.solarTerms?"checked":""}> 显示二十四节气</label>
            <label><input type="checkbox" data-cal-pref="festivals" ${calPrefs.festivals?"checked":""}> 显示传统与公历节日</label>
            <p>节日不代表法定放假或调休安排。</p>
          </div>
        </div>
        <div class="cal-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span class="cal-we">六</span><span class="cal-we">日</span></div>
        <div class="cal-grid">${grid}</div>
        <div class="cal-day-events"><div class="cal-day-head">📆 ${calSel===t0?"今天 · ":""}${calSel}<span class="side-count">${selEvs.length} 项</span></div></div>
      </div>
      </div>

      <div class="card">
        <div class="card-head"><b>☀ 当日工作计划</b><span class="side-count">${calSel}</span></div>
        <div class="check-list">${dayItems.length ? dayItems.map(e=>`<div class="check-item ${e.done?"done":""}"><input type="checkbox" data-chk="${e.id}" ${e.done?"checked":""}>${e.time?`<span class="check-time">${esc(e.time)}</span>`:""}<span class="check-title">${esc(e.title)}</span><button class="check-del" data-del="${e.id}" title="删除">✕</button></div>`).join("")
          : `<div class="hint">这一天还没有安排 🎉🎉🎉</div>`}</div>
        <div class="quick-add"><input id="todayInput" class="tb-input" placeholder="＋ 添加任务，回车确认…"><input id="todayTime" class="tb-input quick-time" type="time" title="可选：这条安排的具体时间"><button class="btn" id="btnTodayAdd">添加</button></div>
      </div>
    </div>

    <div class="cal-bottom">
      <div class="card">
        <div class="card-head"><b>📡 任务完成情况</b><span class="side-count">自动生成</span></div>
        <div class="feed-list">${doneEvs.length ? doneEvs.map(e=>`<div class="feed-item"><span class="feed-ico">✅</span><span class="feed-text">完成任务 [${esc(e.title)}]</span><span class="feed-time">${hhmm(e.created_at)}</span></div>`).join("")
          : `<div class="hint">还没有完成的任务</div>`}</div>
      </div>
      <div class="card">
        <div class="card-head"><b>⏳ 倒计时</b><span class="side-count">${soon.length} 项未完成</span></div>
        <div class="countdown-list">${soon.length ? soon.map(e=>{
            const d = Math.round((new Date(e.date+"T00:00:00").getTime()-base)/DAY);
            return `<div class="countdown-item"><span class="countdown-days ${d<=3?"urgent":""}">${d===0?"今天":d+"天"}</span><span class="countdown-label">${esc(e.title)}</span><span class="side-count">${e.date.slice(5)}</span></div>`;
          }).join("") : `<div class="hint">没有未完成的重要节点</div>`}</div>
      </div>
    </div>
  </div>`;

  $("calY").onchange = e=>{ calY = +e.target.value; render(); };
  $("calM").onchange = e=>{ calM = +e.target.value; render(); };
  $("calPrev").onclick = ()=>{ if(--calM<0){calM=11;calY--;} render(); };
  $("calNext").onclick = ()=>{ if(++calM>11){calM=0;calY++;} render(); };
  $("calToday").onclick = ()=>{ const n=new Date(); calY=n.getFullYear(); calM=n.getMonth(); calSel=today(); render(); };
  $("calOptions").onclick = ()=>$("calOptionsPop").classList.toggle("hidden");
  m.querySelectorAll("[data-cal-pref]").forEach(i=>i.onchange=()=>{ calPrefs[i.dataset.calPref]=i.checked; render(); });
  m.querySelectorAll(".cal-cell").forEach(c=>c.onclick=()=>{ const k=c.dataset.date; if(k===calSel) return; calSel=k; const n=new Date(k+"T00:00:00"); calY=n.getFullYear(); calM=n.getMonth(); render(); });
  m.querySelectorAll("[data-chk]").forEach(i=>i.onchange=async()=>{ await api("/api/calendar/toggle",{id:i.dataset.chk,done:i.checked}); await refreshState(); });
  m.querySelectorAll("[data-del]").forEach(b=>b.onclick=async(ev)=>{ ev.stopPropagation(); if(!await uiConfirm("删除这一条？")) return; await api("/api/calendar/delete",{id:b.dataset.del}); await refreshState(); });
  const add = async()=>{ const v=$("todayInput").value.trim(); if(!v) return;
    await api("/api/calendar/add",{title:v,date:calSel,task_id:curTask||null,kind:"todo",time:$("todayTime").value});
    $("todayInput").value=""; $("todayTime").value=""; await refreshState(); };
  $("btnTodayAdd").onclick = add;
  $("todayInput").onkeydown = e=>{ if(e.key==="Enter"){ e.preventDefault(); add(); } };
  $("todayTime").onkeydown = e=>{ if(e.key==="Enter"){ e.preventDefault(); add(); } };
}

/* 邮箱页标记：收件箱视图 + 两个弹窗 */
const MAIL_HTML = `<div class="mail-page"><!-- ===== 视图：邮箱 ===== -->
      <section id="viewMail" class="view">
        <div class="mail-wrap">
          <!-- 左栏：账户与文件夹 -->
          <aside class="mail-side">
            <div class="mail-side-head">
              <b>📧 邮箱</b>
              <button class="btn-ghost as-link" id="btnMailManage">＋ 账户管理</button>
            </div>
            <div id="mailAccounts" class="mail-accounts"></div>
            <div class="mail-side-sep"></div>
            <div id="mailFolders" class="mail-folders"></div>
          </aside>

          <!-- 中栏：邮件列表 -->
          <div class="mail-list-col">
            <div class="mail-list-head">
              <input id="mailSearch" class="tb-input mail-search" placeholder="搜索主题 / 发件人，回车确认…" />
              <button class="tb-btn" id="btnMailRefresh" title="刷新当前文件夹">⟳</button>
              <button class="tb-btn" id="btnMailMarkAllRead" title="将当前文件夹所有未读邮件标记为已读">✓ 全部已读</button>
              <button class="tb-btn" id="btnMailCompose" title="写邮件">✎ 写邮件</button>
            </div>
            <div id="mailListHead" class="mail-list-title"></div>
            <div id="mailList" class="mail-list"></div>
            <div id="mailPager" class="mail-pager"></div>
          </div>

          <!-- 右栏：邮件详情 -->
          <div class="mail-detail-col">
            <div id="mailDetail" class="mail-detail"></div>
          </div>
        </div>
      </section>

<div id="mailAccountModal" class="modal hidden">
    <div class="modal-mask"></div>
    <div class="modal-dialog modal-wide">
      <div class="modal-head"><h3>📧 邮箱账户管理</h3><button class="modal-close" id="btnMaClose">✕</button></div>
      <div class="modal-body">
        <p class="modal-desc">支持添加多个邮箱账户（如个人 163 邮箱 + 学校邮箱），随时切换查看。不需要邮箱也可以留空，不影响其他功能。<br />账号与授权码仅保存在<b>本机数据目录</b>（mail.json），不会上传到任何服务器。</p>
        <div id="maList" class="ma-list"></div>

        <div class="field-sep"></div>
        <h4 class="ma-form-title" id="maFormTitle">添加邮箱账户</h4>
        <div class="prof-grid">
          <label class="field"><span>邮箱服务商</span>
            <select id="maProvider" class="tb-select"></select>
          </label>
          <label class="field"><span>备注名称（可留空）</span>
            <input id="maLabel" class="tb-input" placeholder="如：个人邮箱 / 学校邮箱" />
          </label>
          <label class="field"><span>邮箱地址 *</span>
            <input id="maEmail" class="tb-input" placeholder="xxx@163.com" autocomplete="off" />
          </label>
          <label class="field"><span>授权码 / 密码 *</span>
            <input id="maPassword" type="password" class="tb-input" placeholder="网易/QQ 邮箱填授权码" autocomplete="new-password" />
          </label>
        </div>
        <p class="modal-desc" id="maNote"></p>
        <div class="modal-desc" id="maHelpLinks"></div>

        <details class="ma-advanced">
          <summary>高级设置（服务器地址与端口，一般无需修改）</summary>
          <div class="prof-grid">
            <label class="field"><span>IMAP 服务器</span><input id="maImapHost" class="tb-input" placeholder="imap.163.com" /></label>
            <label class="field"><span>IMAP 端口</span><input id="maImapPort" class="tb-input" placeholder="993" /></label>
            <label class="field"><span>SMTP 服务器</span><input id="maSmtpHost" class="tb-input" placeholder="smtp.163.com" /></label>
            <label class="field"><span>SMTP 端口</span><input id="maSmtpPort" class="tb-input" placeholder="465" /></label>
          </div>
          <div class="ma-checks">
            <label><input type="checkbox" id="maImapSecure" checked /> IMAP 使用 SSL/TLS</label>
            <label><input type="checkbox" id="maSmtpSecure" checked /> SMTP 使用 SSL/TLS</label>
            <label><input type="checkbox" id="maSelfSigned" /> 忽略证书校验（学校/企业自建邮箱）</label>
          </div>
        </details>
        <div id="maTestResult" class="ma-test-result hidden"></div>
      </div>
      <div class="modal-foot">
        <button id="btnMaTest" class="btn">🔌 测试连接</button>
        <span class="ob-spacer"></span>
        <button id="btnMaSave" class="btn btn-primary">保存账户</button>
        <button id="btnMaCancel" class="btn">取消编辑</button>
      </div>
    </div>
  </div>
<div id="mailComposeModal" class="modal hidden">
    <div class="modal-mask"></div>
    <div class="modal-dialog modal-wide">
      <div class="modal-head"><h3>✎ 写邮件</h3><button class="modal-close" id="btnMcClose">✕</button></div>
      <div class="modal-body">
        <div class="prof-grid">
          <label class="field"><span>发件账户</span><select id="mcFrom" class="tb-select"></select></label>
          <label class="field"><span>收件人 *</span><input id="mcTo" class="tb-input" placeholder="多个收件人用英文逗号分隔" /></label>
          <label class="field"><span>抄送</span><input id="mcCc" class="tb-input" placeholder="可留空" /></label>
          <label class="field"><span>主题</span><input id="mcSubject" class="tb-input" placeholder="邮件主题" /></label>
        </div>
        <label class="field"><span>正文</span><textarea id="mcBody" rows="10" placeholder="在此输入邮件内容…"></textarea></label>
      </div>
      <div class="modal-foot">
        <button id="btnMcSend" class="btn btn-primary">发送</button>
        <button id="btnMcCancel" class="btn">取消</button>
      </div>
    </div>
  </div></div>`;

  /* ---- 本项目缺的小工具（lit 那边是全局函数） ---- */
  function toast(msg, type){
    let el = document.getElementById("toast");
    if(!el){ el = document.createElement("div"); el.id = "toast"; document.body.appendChild(el); }
    el.textContent = msg; el.className = "toast " + (type || "");
    requestAnimationFrame(()=>el.classList.add("show"));
    clearTimeout(el._t); el._t = setTimeout(()=>el.classList.remove("show"), 2800);
  }
  function stripHtml(html){ const d = document.createElement("div"); d.innerHTML = html || ""; return d.textContent || ""; }
