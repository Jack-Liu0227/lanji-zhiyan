/* ============================== 页面 ============================== */
function render(){
  renderSidebar(); renderTopbar(); renderTabs(); renderStatus();
  const r = route(); const m = $("main");
  m.classList.remove("sp-fill", "tof-fill");
  if(!S.loaded) m.innerHTML = `<div class="muted">加载中…</div>`;
  else if(r==="agent") renderAgent(m);
  else if(r==="workflow") renderWorkflow(m);
  else if(r==="tasks") renderTasks(m);
  else if(r==="kanban") renderKanban(m);
  else if(r==="space") renderSpace(m);
  else if(r==="calendar") renderCalendar(m);
  else if(r==="mail") renderMail(m);
  else if(r==="library") renderStar(m);                        // 知识星图整页（项目 → 文献/笔记/知识点/候选/工作流）
  else if(r==="notes") renderNotes(m);
  else if(r==="intel") renderIntel(m);
  else if(r==="assistants") renderAssistants(m);
  else if(r==="team") renderTeam(m);
  else if(r==="schedules") renderSchedules(m);
  else if(r==="models") renderModels(m);
  else renderSettings(m);
}

function needTask(){
  if(!curTask && S.tasks && S.tasks.length){
    curTask = S.tasks[0].id;
    localStorage.setItem("rw-task", curTask);
  }
  if(!curTask) return `<div class="card"><h3>当前暂无项目</h3><div class="hint">工作流与文献均可绑定到独立项目上。你可以点击下方按钮新建项目，也可以在「项目」页自由管理。</div><div style="display:flex;gap:8px;margin-top:10px"><button class="btn btn-primary" onclick="openTab('tasks')">＋ 新建项目</button></div></div>`;
  return "";
}

async function initDefaultProject(){
  try{
    const res = await api("/api/task/create", {
      title: "锂金属固态电池界面反应动力学与多尺度演化研究",
      question: "如何解决高倍率循环下固态电解质/金属锂界面的接触失效与副反应演化问题？",
      material_system: "固态锂电池, 复合固态电解质, 锂金属负极"
    });
    curTask = res.id;
    localStorage.setItem("rw-task", curTask);
    await refreshState(false);
    render();
  }catch(e){ alert("初始化项目失败: " + e.message); }
}


/* ---------------- 助手（个人资料 + 编辑器 ----------------
   我的助手 / 公开助手 两栏；点卡片进编辑器（身份 / 提示词 / 规则 / 默认模型）。
   公开助手只读，只能「复制为我的」。 ---------------- */
let asView = "all";                       // all | official | mine | edit
let asEdit = { id:"", source:"user", name:"", avatar:"", description:"", rules:"", prompts:[], model_id:"" };

function asSource(a){ return a.source === "builtin" ? "official" : "mine"; }
function asOf(kind){
  if(kind === "all") return S.assistants || [];
  return (S.assistants||[]).filter(a=>asSource(a)===kind);
}
function asCur(){ return (S.assistants||[]).find(a=>a.id===asEdit.id) || null; }
function asChat(){ return (S.chats||[]).find(c=>c.id===curChat) || null; }
function asBound(){ const c=asChat(); return c ? (c.assistant_id||"") : ""; }
const Q_AV = "/vendor/img/agent-q.png";                                        // 智研管家主形象（Q 版小人，透明底）
const AH_AV = Array.from({length: 7}, (_, i)=>`/vendor/img/av${i + 1}.png`);   // 管家姿势池（q/ 里那 7 张，对话列表/消息轮流用）
let poseN = 0;                                                                  // 姿势轮换游标，每渲染一次对话归零
function asAvatarOf(a, size){
  const px = size||42;
  return `<span class="as-av" style="width:${px}px;height:${px}px;font-size:${Math.round(px*0.55)}px">${esc(a&&a.avatar || "👨‍🔬")}</span>`;
}
/* 一个对话的脸：绑了专家 = 专家头像；没绑 = 智研管家（主形象 / rotate 时轮流换姿势） */
function faceOf(a, px, rotate){
  if(a) return asAvatarOf(a, px);
  const src = rotate ? AH_AV[(poseN++) % AH_AV.length] : Q_AV;
  return `<span class="as-av" style="width:${px}px;height:${px}px"><img src="${src}" alt="智研管家"
    style="width:100%;height:100%;object-fit:contain" loading="lazy"></span>`;
}
/* 对话抬头副标题：管家=全局；专家=项目内（没绑项目给提示）。renderAgent 和 loadAgentMeta 共用一份，别再各写一遍 */
function atSubHTML(room){
  const a = (S.assistants||[]).find(x=>x.id===((room&&room.assistant_id)||"")) || null;
  const out = `产出 ${esc(room?outName(room.out_dir):"默认产出目录")}`;
  if(!a) return `智研管家 · 全局（不限项目）· ${out}`;
  return `${esc(a.name)} · ${room&&room.task_id ? `Partner · 绑定《${esc(projName(room.task_id)||"项目")}》` : "专家按项目工作 · 当前未绑项目"} · ${out}`;
}
function asCard(a){
  const on = a.enabled !== 0, mine = asSource(a)==="mine";
  return `<div class="as-card ${on?"":"off"}" data-as-open="${a.id}">
    <div class="as-card-top">
      <div style="display:flex;align-items:center;gap:8px">
        ${asAvatarOf(a)}
        <span class="as-src-badge" style="font-size:11px;padding:2px 7px;border-radius:6px;background:var(--panel2);color:var(--muted);border:1px solid var(--line)">${mine?"我的":"系统"}</span>
      </div>
      <button class="as-toggle ${on?"on":"off"}" data-as-toggle="${a.id}" title="${on?"已启用（点击停用）":"未启用（点击启用）"}">
        <span class="as-knob"></span><span class="as-toggle-txt">${on?"已启用":"未启用"}</span>
      </button>
    </div>
    <b>${esc(a.name)}</b>
    <i class="as-mdl">${a.model_id?("🔌 "+esc(modelName(a.model_id)||"（模型已删）")):"🔌 用当前激活模型"}</i>
    <p>${esc(a.description||"（没有描述）")}</p>
    <div class="as-card-foot">
      <button class="btn btn-primary as-go" data-as-chat="${a.id}" title="以该专家身份开启对话">💬 去对话</button>
      ${mine?`<button class="btn ghost as-act-btn" data-as-del="${a.id}" style="color:var(--err)">删除</button>`
            :`<button class="btn ghost as-act-btn" data-as-dup="${a.id}">复制为我的</button>`}
    </div></div>`;
}

/* ---------------- 团队（成员=助手实例、Leader 恒第一、身份色） ---------------- */
let teamScope = localStorage.getItem("rw-team-chat") || "";
let teamData = null, teamBusy = false, teamTick = null, teamList = null;
function tcColor(i){ const P=["var(--brand)","#5c9ea4","#b58a5e","#8f7fb5","#7fa06a","#b57f92"]; return P[(i||0)%P.length]; }
const modelName = (id)=>((S.models||[]).find(x=>x.id===id)||{}).name||"";
async function renderTeam(m){
  if(!teamList){ try{ teamList = await api("/api/teams"); }catch(e){ teamList = {teams:[], error:e.message}; } }
  const TL = (teamList.teams||[]);
  const curT = TL.find(t=>t.id===teamScope) || null;
  if(!curT){                                            // scope 不合法（老数据/被解散）→ 落到第一个固定团队
    const first = TL.find(t=>t.kind==="fixed") || TL[0] || null;
    teamScope = first ? first.id : "";
    if(teamScope) localStorage.setItem("rw-team-chat", teamScope); else localStorage.removeItem("rw-team-chat");
  }
  if(!teamScope){
    m.innerHTML = `<div class="page-head"><div><p class="inst-label">TEAM</p><h3>团队</h3>
      <p class="hint">正在开一个团队群…</p></div></div>`;
    try{ const r = await api("/api/chat/create", {title:"团队"}); teamScope = r.id || (r.chat&&r.chat.id) || "";
         localStorage.setItem("rw-team-chat", teamScope); await refreshState(false); }
    catch(e){ m.innerHTML = `<div class="page-head"><div><p class="inst-label">TEAM</p><h3>团队</h3>
      <p class="hint">开团队群失败：${esc(e.message)}</p></div></div>`; return; }
  }
  try{ teamData = await api("/api/team/chat?scope=" + encodeURIComponent(teamScope)); }
  catch(e){ teamData = {error:e.message, members:[], transcript:[]}; }
  const D = teamData || {}, all = D.members||[], leader = all.find(x=>x.role==="leader")||null;
  const ms = all.filter(x=>x.role!=="member"?false:true), picks = all.filter(x=>x.role==="member");
  const inTeam = new Set(picks.map(x=>x.assistant_id));
  const pool = (S.assistants||[]).filter(a=>a.enabled!==0 && !inTeam.has(a.id));
  const run = D.running;
  const tr = D.transcript||[];
  let lastUser = -1; tr.forEach((r,i)=>{ if(r.role==="user") lastUser = i; });
  const spoken = new Set(tr.slice(lastUser+1).map(r=>r.sender).filter(Boolean));   // 这一轮里已经说过话的成员
  let seq = 0;
  const bubble = (r)=>{
    const who = r.sender || (r.role==="user" ? "你" : "成员");
    const mm = picks.find(x=>x.name===who);
    const at = esc((r.at||"").replace("T"," ").slice(5,16));
    if(who==="系统") return `<div class="tc-sys"><span class="tc-av">!</span><div class="tc-body"><div class="tc-who"><b>系统</b><span class="tc-at">${at}</span></div><div class="tc-text">${esc(r.content||"")}</div></div></div>`;
    const mine = r.role==="user";
    const n = mine ? 0 : ++seq;
    return `<div class="tc-msg ${mine?"me":"them"}" style="--mc:${mine?"var(--accent)":tcColor(mm&&mm.color)}">
      <span class="tc-av">${mine?"🙋":esc((mm&&mm.name||"?").slice(0,1))}</span>
      <div class="tc-body"><div class="tc-who"><b>${esc(who)}</b>${n?`<span class="tc-idx">#${n}</span>`:""}<span class="tc-at">${at}</span></div>
        <div class="tc-text">${esc(r.content||"")}</div></div></div>`;
  };
  const sessions = D.sessions || [];
  const curSess = D.session || sessions.find(s=>s.active) || sessions[0] || {};
  m.innerHTML = `<div class="tc-page">
  <!-- 左侧：对话历史侧边栏 -->
  <aside class="tc-side-hist">
    <div class="tc-hist-head">
      <span>💬 会话历史</span>
      <button class="btn btn-primary" id="tcNewSession" title="新建团队会话">＋ 新建</button>
    </div>
    <div class="tc-hist-list">
      ${sessions.length ? sessions.map(s => `
        <button class="tc-hist-item ${s.id === curSess.id ? "on" : ""}" data-tc-sess="${esc(s.id)}" title="${esc(s.title || "新会话")}">
          <span class="tc-hist-title">${esc(s.title || "新会话")}</span>
          <span class="tc-hist-time">${esc(fmtAgo(s.updated_at))}</span>
        </button>
      `).join("") : `<p class="hint" style="padding:12px;font-size:11.5px">暂无会话历史，点击上方新建。</p>`}
    </div>
  </aside>

  <!-- 右侧：主研讨区 -->
  <div class="tc-stage">
    <!-- 顶部状态栏：团队下拉选择 + 项目文件夹工作区选择 + 状态与配置 -->
    <div class="tc-top-bar">
      <div class="tc-top-picks">
        <label class="at-sel" title="选择协同团队">
          <span class="tc-sel-lbl">👥 团队</span>
          <select class="control" id="tcTeamSelect">
            ${TL.map(x=>`<option value="${x.id}" ${x.id===teamScope?"selected":""}>${x.kind==="fixed"?"📌 ":"⏳ "}${esc(x.name)} (${x.picks||0}人)</option>`).join("")}
          </select>
        </label>
        <label class="at-sel" title="选择关联项目文件夹工作区（资料、产出与项目上下文对齐）">
          <span class="tc-sel-lbl">📁 工作区</span>
          <select class="control" id="tcWorkspaceSelect">
            <option value="">全局工作区（跨项目）</option>
            ${(S.tasks||[]).map(t=>`<option value="${t.id}" ${t.id===curTask?"selected":""}>${esc(t.title)}${t.base_dir?(" · "+esc(outName(t.base_dir))):""}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="tc-top-acts">
        ${run?`<span class="pill ok">${esc(run.phase||"接力推进中")} · 第 ${run.turn||0}/${run.total||6} 轮</span><button class="btn ghost" id="tcStop">停止</button>`:`<span class="pill">团队就绪</span>`}
        <button class="btn ghost" id="tcRefresh" title="刷新状态">刷新</button>
        <button class="btn ghost" id="tcConfig" title="在设置中管理团队和专家成员">⚙️ 团队配置</button>
      </div>
    </div>

    <!-- 专家成员与当前接力状态展示横条 -->
    <div class="tc-members-strip">
      <span class="tc-strip-lbl">专家成员：</span>
      ${leader ? `<span class="tc-member-pill leader" title="团队统筹 Leader"><span class="tc-av-mini">🧭</span><b>${esc(leader.name)}</b><span class="tc-pill-sub">Leader</span></span>` : ""}
      ${picks.map(x => {
        const now = !!(run && run.current === x.name);
        const done = spoken.has(x.name);
        return `<span class="tc-member-pill ${now ? "now" : (done ? "done" : "")}" style="--mc:${tcColor(x.color)}" title="${esc(x.name)} · ${esc(x.model || "跟随设置")}">
          <span class="tc-av-mini" style="background:var(--mc)">${esc((x.name||"?").slice(0,1))}</span>
          <b>${esc(x.name)}</b>
          <span class="tc-pill-sub">${now ? "发言中" : (done ? "已发言" : "待命")}</span>
        </span>`;
      }).join("")}
      ${!picks.length ? `<span class="hint" style="margin:0 4px">当前团队暂无专家，点击右上方「⚙️ 团队配置」添加专家成员</span>` : ""}
      ${run ? `<span class="tc-strip-phase">🏃 ${esc(run.current || "")} 正在分析输出…</span>` : ""}
    </div>

    <!-- 主对话区域：全宽无侧栏沉浸式对话框 -->
    <section class="tc-panel tc-chat-main tc-chat-full">
      <div class="tc-tl" id="tcChat">
        ${tr.length ? tr.map(bubble).join("")
          : `<div class="tc-empty">🧭 团队协作对话框已就绪。<br>在此输入研究目标（例如：「帮我挑一个钠电正极掺杂方向，产出文献候选与实验对比表」），Leader 将统筹团队专职专家接力推进。<br><small class="hint">需要增减专家成员或调整模型权限？随时点击右上角「⚙️ 团队配置」。</small></div>`}
      </div>
      <div class="tc-composer">
        <textarea class="control" id="tcSay" rows="3" placeholder="给团队下达研究目标或追问…（Enter 发送，Shift+Enter 换行）"></textarea>
        <div class="tc-composer-actions">
          <span class="hint">${run ? `正在接力推进：${esc(run.current||"")}` : "Enter 发送 / Shift+Enter 换行 · 团队串行接力协作"}</span>
          <button class="btn btn-primary" id="tcSend">${run ? "继续追问 / 补充指令" : "发送并让团队推进"}</button>
        </div>
      </div>
    </section>
  </div>
  </div>`;
  const box = $("tcChat"); if(box) box.scrollTop = box.scrollHeight;
  const ts = $("tcTeamSelect");
  if(ts) ts.onchange = (e)=>{
    teamScope = e.target.value;
    localStorage.setItem("rw-team-chat", teamScope);
    teamData = null; render();
  };
  const ws = $("tcWorkspaceSelect");
  if(ws) ws.onchange = (e)=>{
    curTask = e.target.value || null;
    if(curTask) localStorage.setItem("rw-task", curTask);
    else localStorage.removeItem("rw-task");
    render();
  };
  const ns = $("tcNewSession");
  if(ns) ns.onclick = async ()=>{
    try {
      await api("/api/team/chat/session", {scope: teamScope, create: true});
    } catch(err) {
      try { await api("/api/agent-settings/team-session", {team_id: teamScope, create: true}); } catch(e2) { alert(e2.message); }
    }
    teamData = null; render();
  };
  m.querySelectorAll("[data-tc-sess]").forEach(b => b.onclick = async ()=>{
    const sid = b.dataset.tcSess;
    try {
      await api("/api/team/chat/session", {scope: teamScope, session_id: sid});
    } catch(err) {
      try { await api("/api/agent-settings/team-session", {team_id: teamScope, session_id: sid}); } catch(e2) { alert(e2.message); }
    }
    teamData = null; render();
  });
  const cfg = $("tcConfig");
  if(cfg) cfg.onclick = ()=>{
    window.__teamMode = "manage";
    render();
  };
  const st = $("tcStop");
  if(st) st.onclick = async ()=>{ try{ await api("/api/team/chat/stop", {scope:teamScope}); }catch(e){ alert(e.message); } setTimeout(()=>{ teamData=null; render(); }, 600); };
  const rf = $("tcRefresh"); if(rf) rf.onclick = ()=>{ teamData=null; render(); };
  const say = async ()=>{
    const t = $("tcSay"); const text = (t.value||"").trim();
    if(!text) return;
    if(!picks.length) return alert("先在「设置 / 团队配置」中添加专家成员。");
    t.value = "";
    try{ await api("/api/team/chat/say", {scope:teamScope, text}); }
    catch(e){ return alert("发送失败：" + e.message); }
    teamData = null; render();
  };
  if($("tcSend")) $("tcSend").onclick = say;
  if($("tcSay")) $("tcSay").onkeydown = (e)=>{ if(e.key==="Enter" && !e.shiftKey){ e.preventDefault(); say(); } };
  clearTimeout(teamTick);                                   // 有轮次在跑就每 2s 刷新一次群
  if(run) teamTick = setTimeout(()=>{ if(route()==="team"){ teamData=null; render(); } }, 2000);
}
function renderAssistants(m){
  const bound = asBound();
  if(asView === "edit"){
    const a = asCur(), ro = asEdit.source === "builtin";
    m.innerHTML = `<div class="page-head">
      <div><p class="inst-label">EXPERTS</p><h3>${a?"编辑专家":"新建专家"}</h3>
        <p class="hint">${ro?"公开专家的内容只读 —— 点「复制为我的」就能改成你自己的。":"提示词和规则会在这个专家发起的对话里生效（进 system）。"}</p></div>
      <div class="ph-acts"><button class="btn" id="as-back">← 返回</button>
        ${a&&!ro?`<button class="btn ghost" id="as-del" style="color:var(--err)">删除</button>`:""}</div></div>
    <div class="card" style="margin-top:12px">
      <p class="inst-label">身份</p>
      <div class="as-form">
        <label class="field"><span>名字</span><input class="control" id="as-name" ${ro?"disabled":""} value="${esc(asEdit.name)}" placeholder="如：文献检索专家"></label>
        <label class="field"><span>头像 Emoji</span><input class="control" id="as-av" ${ro?"disabled":""} value="${esc(asEdit.avatar)}" placeholder="👨‍🔬" maxlength="4"></label>
        <label class="field"><span>描述</span><input class="control" id="as-desc" ${ro?"disabled":""} value="${esc(asEdit.description)}" placeholder="一句话说明该专家的专长与职责"></label>
        <label class="field"><span>默认模型（可留空 = 用当前模型）</span>
          <select class="control" id="as-model" ${ro?"disabled":""}>
            <option value="">跟随当前模型</option>
            ${(S.models||[]).map(mm=>`<option value="${mm.id}" ${mm.id===asEdit.model_id?"selected":""}>${esc(mm.name)}</option>`).join("")}</select></label>
      </div></div>
    <div class="card" style="margin-top:12px">
      <p class="inst-label">提示词 / 推荐问题（一行一条，空状态会显示成按钮）</p>
      <textarea class="control" id="as-prompts" rows="4" ${ro?"readonly":""} placeholder="检索 O3 型钠电正极近三年掺杂改性的论文">${esc((asEdit.prompts||[]).join("\n"))}</textarea>
      <p class="inst-label" style="margin-top:12px">规则（写进 system，约束专家怎么干活）</p>
      <textarea class="control" id="as-rules" rows="5" ${ro?"readonly":""} placeholder="只做开放获取检索；拿不到全文就说拿不到，绝不编造。">${esc(asEdit.rules||"")}</textarea>
    </div>
    ${ro?`<div style="margin-top:12px"><button class="btn" id="as-dup">复制为我的</button></div>`
        :`<div style="margin-top:12px"><button class="btn btn-primary" id="as-save">保存</button></div>`}`;
    $("as-back").onclick = ()=>{ asView = "mine"; render(); };
    const dupBtn = $("as-dup");
    if(dupBtn) dupBtn.onclick = ()=> duplicateAssistant(a);
    const delBtn = $("as-del");
    if(delBtn) delBtn.onclick = ()=> deleteAssistant(a.id, a.name);
    const saveBtn = $("as-save");
    if(saveBtn) saveBtn.onclick = async ()=>{
      const body = { id: asEdit.id||"", name: $("as-name").value.trim() || "新专家",
        avatar: $("as-av").value.trim(), description: $("as-desc").value.trim(),
        model_id: $("as-model").value,
        prompts: $("as-prompts").value.split("\n").map(x=>x.trim()).filter(Boolean),
        rules: $("as-rules").value.trim() };
      let r;
      try{ r = await api("/api/assistant/save", body); }
      catch(e){ return alert("保存失败：" + e.message); }
      await refreshState(false);
      asEdit.id = r.assistant.id;            // 新建时服务端才给 id
      asView = "all"; render();
    };
    return;
  }
  const list = asOf(asView);
  const totalCount = (S.assistants||[]).length;
  const officialCount = asOf("official").length;
  const mineCount = asOf("mine").length;
  m.innerHTML = `<div class="page-head">
    <div><p class="inst-label">EXPERTS</p><h3>专家</h3>
      <p class="hint">系统专家与自定义专家管理。直接在卡片右上角开关即可启用或停用。当前对话专家：<b>${esc(((S.assistants||[]).find(a=>a.id===bound)||{}).name || "默认研究专家")}</b></p></div>
    <div class="ph-acts" style="display:flex;gap:8px;align-items:center">
      <button class="btn btn-sm" id="as-all-on" title="启用当前列表中的所有专家">全部启用</button>
      <button class="btn btn-sm ghost" id="as-all-off" title="停用当前列表中的所有专家">全部停用</button>
      <button class="btn btn-primary btn-sm" id="as-new">＋ 新建专家</button>
    </div></div>
  <div class="at-tabs" style="margin-top:10px">
    <button class="at-tab ${asView==="all"?"on":""}" data-as-v="all">全部专家 ${totalCount}</button>
    <button class="at-tab ${asView==="official"?"on":""}" data-as-v="official">系统专家 ${officialCount}</button>
    <button class="at-tab ${asView==="mine"?"on":""}" data-as-v="mine">我的专家 ${mineCount}</button></div>
  <div class="as-grid">${list.length?list.map(asCard).join(""):`<p class="hint">这一栏还是空的。</p>`}</div>`;
  $("as-new").onclick = ()=>{ asEdit = {id:"",source:"user",name:"",avatar:"",description:"",rules:"",prompts:[],model_id:""}; asView="edit"; render(); };
  const btnAllOn = $("as-all-on"), btnAllOff = $("as-all-off");
  if(btnAllOn) btnAllOn.onclick = async ()=>{
    const targetIds = list.map(x=>x.id);
    if(!targetIds.length) return;
    try{
      await api("/api/assistant/batch-toggle", {ids: targetIds, enabled: 1});
      await refreshState(false);
      render();
    }catch(err){ alert("批量启用失败：" + err.message); }
  };
  if(btnAllOff) btnAllOff.onclick = async ()=>{
    const targetIds = list.map(x=>x.id);
    if(!targetIds.length) return;
    try{
      await api("/api/assistant/batch-toggle", {ids: targetIds, enabled: 0});
      await refreshState(false);
      render();
    }catch(err){ alert("批量停用失败：" + err.message); }
  };
  m.querySelectorAll("[data-as-v]").forEach(b=>b.onclick=()=>{ asView=b.dataset.asV; render(); });
  m.querySelectorAll("[data-as-open]").forEach(b=>b.onclick=(e)=>{
    if(e.target.closest("[data-as-chat],[data-as-del],[data-as-dup],[data-as-toggle]")) return;
    const a = (S.assistants||[]).find(x=>x.id===b.dataset.asOpen); if(!a) return;
    asEdit = { id:a.id, source:a.source, name:a.name, avatar:a.avatar||"", description:a.description||"", rules:a.rules||"",
               prompts: JSON.parse(a.prompts||"[]"), model_id:a.model_id||"" };
    asView = "edit"; render();
  });
  m.querySelectorAll("[data-as-toggle]").forEach(b=>b.onclick=async (e)=>{
    e.stopPropagation();
    const aid = b.dataset.asToggle;
    const a = (S.assistants||[]).find(x=>x.id===aid);
    if(!a) return;
    const next = a.enabled === 0 ? 1 : 0;
    try{
      await api("/api/assistant/toggle", {id: aid, enabled: next});
      a.enabled = next;
      await refreshState(false);
      render();
    }catch(err){ alert("切换状态失败：" + err.message); }
  });
  m.querySelectorAll("[data-as-chat]").forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); useAssistant(b.dataset.asChat); });
  m.querySelectorAll("[data-as-dup]").forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); duplicateAssistant((S.assistants||[]).find(x=>x.id===b.dataset.asDup)); });
  m.querySelectorAll("[data-as-del]").forEach(b=>b.onclick=(e)=>{
    e.stopPropagation(); const a=(S.assistants||[]).find(x=>x.id===b.dataset.asDel); deleteAssistant(b.dataset.asDel, a?a.name:""); });
}
async function duplicateAssistant(a){
  if(!a) return;
  const r = await api("/api/assistant/save", { name: a.name + "（我的）", avatar: a.avatar, description: a.description,
    rules: a.rules, prompts: JSON.parse(a.prompts||"[]"), model_id: a.model_id||"" });
  await refreshState(false);
  asEdit = { id:r.assistant.id, source:"user", name:r.assistant.name, avatar:r.assistant.avatar,
             description:r.assistant.description, rules:r.assistant.rules, prompts:JSON.parse(r.assistant.prompts||"[]"), model_id:r.assistant.model_id||"" };
  asView = "mine"; render();          // 落回列表：新卡片就在那儿，改它点卡片进去
}
async function deleteAssistant(id, name){
  const a = (S.assistants||[]).find(x=>x.id===id);
  if(a && a.source === "builtin") return alert("公开专家不能删除 —— 可以「复制为我的」再改。");
  if(!confirm(`删除专家「${name}」？（已经用它的对话不受影响）`)) return;
  try{ await api("/api/assistant/delete", {id}); }catch(e){ return alert("删除失败：" + e.message); }
  await refreshState(false); asView = "mine"; render();
}
async function useAssistant(id){                       // 用它对话：绑到当前对话（没有就新建一个），切到 Agent 页
  const a = (S.assistants||[]).find(x=>x.id===id);
  if(a && a.enabled === 0){
    try{ await api("/api/assistant/toggle", {id, enabled: 1}); a.enabled = 1; }catch(_){}
  }
  let cid = curChat;
  if(!cid){ const r = await api("/api/chat/create", {}); cid = r.id || (r.chat && r.chat.id); }
  try{ await api("/api/chat/assistant", {id: cid, assistant_id: id}); }catch(e){ return alert("绑定失败：" + e.message); }
  curChat = cid; localStorage.setItem("rw-chat", cid);
  await refreshState(false); openTab("agent");
}

/* ---------------- Agent（结构/版式照科研工作台 features/agent：
   左「对话历史」= 我们的项目列表，右「对话 | 轨迹」主线，底部 dock 有状态行/七步/输入区） ---------------- */
let agentView = "conversation";         // 对话 | 轨迹
const ahSel = new Set();                // 对话历史里勾选的对话 id
const outName = (p)=> (p||"").replace(/[\\/]+$/,"").split(/[\\/]/).pop() || "默认（工作区 out）";
const assistantKey = ()=> localStorage.getItem("rw-assist") || "researcher";
const emptyState = (icon,title,text)=>`<div class="at-empty"><div class="at-empty-ico">${icon}</div><h2>${title}</h2><p>${text}</p></div>`;

function historyGroups(pool){
  const g = {"今天":[],"昨天":[],"更早":[]};
  const list = pool || S.chats || [];
  for(const t of list){
    const d = new Date(t.updated_at || t.created_at || ""); const n = new Date();
    const dist = Math.floor((Date.UTC(n.getFullYear(),n.getMonth(),n.getDate()) - Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()))/86400000);
    (isNaN(dist) ? g["更早"] : g[dist<=0?"今天":dist===1?"昨天":"更早"]).push(t);
  }
  return g;
}
function fmtAgo(iso){                        // 方案 A 的副标题用相对时间（完整时间放 title 里 hover 看）
  if(!iso) return "";
  const d = new Date(iso); if(isNaN(d.getTime())) return "";
  const s = (Date.now() - d.getTime()) / 1000;
  const hm = x => String(x.getHours()).padStart(2,"0") + ":" + String(x.getMinutes()).padStart(2,"0");
  if(s < 60) return "刚刚";
  if(s < 3600) return Math.floor(s / 60) + " 分钟前";
  if(s < 86400) return Math.floor(s / 3600) + " 小时前";
  const yd = new Date(); yd.setDate(yd.getDate() - 1);
  if(d.toDateString() === yd.toDateString()) return "昨天 " + hm(d);
  if(s < 86400 * 7) return Math.floor(s / 86400) + " 天前";
  return fmtDate(iso).slice(0, 10);
}
function projName(id){ return ((S.tasks||[]).find(x=>x.id===id)||{}).title || ""; }
function renderHistory(){
  const all = (S.chats||[]).slice();
  const groups = historyGroups(all);
  const byBorn = [...all].sort((a, b)=> String(a.created_at || "").localeCompare(String(b.created_at || "")));
  const av = (t)=>{                                          // 绑了专家 → 专家头像；没绑 → 管家姿势按创建顺序轮流
    const a = (S.assistants||[]).find(x=>x.id === (t.assistant_id||""));
    if(a) return `<span class="ah-av ah-av-emoji" title="${esc(a.name)}">${esc(a.avatar||"👨‍🔬")}</span>`;
    const i = Math.max(0, byBorn.findIndex(c=>c.id === t.id));
    return `<span class="ah-av"><img src="${AH_AV[i % AH_AV.length]}" alt="" loading="lazy"
      onerror="this.src='${Q_AV}'"></span>`;                 // 文件缺了就退回主形象
  };
  const item = (t)=>`<div class="ah-item-wrap ${t.id===curChat?"on":""}" title="${esc(outName(t.out_dir))} · ${esc(fmtDate(t.updated_at))}">
      <label class="ah-check" title="选择"><input type="checkbox" data-sel="${t.id}" ${ahSel.has(t.id)?"checked":""}></label>
      <button class="ah-item" data-open="${t.id}">
        ${av(t)}
        <span class="ah-copy"><strong>${esc(t.title)}</strong>
          <span>${t.task_id?`<em class="ah-pill">${esc(projName(t.task_id)||"项目")}</em>`:""}<i>${esc(fmtAgo(t.updated_at))}</i></span></span></button>
      <button class="ah-del" data-del="${t.id}" title="删除对话">🗑</button></div>`;
  const list = Object.entries(groups).map(([k,v])=> v.length
      ? `<div class="ah-group"><p class="ah-group-label">${k}<span class="ln"></span><i>${v.length}</i></p>${v.map(item).join("")}</div>` : "").join("");
  return `<aside class="ah" aria-label="对话历史">
    <div class="ah-head"><div><p class="eyebrow">WORKSPACE</p><h2>对话</h2></div>
      <button class="ah-new" id="ah-new" title="新建对话">＋</button></div>
    <div class="ah-list">
      <div class="ah-bar"><label><input type="checkbox" id="ah-all" ${ahSel.size&&ahSel.size===(S.chats||[]).length?"checked":""}>全选 ${all.length} 个对话</label>
        <button class="ah-del-sel" id="ah-del-sel" ${ahSel.size?"":"disabled"}>删除选中 ${ahSel.size}</button></div>
      ${list || `<p class="hint" style="margin:14px 5px">还没有对话，点右上角 ＋ 新建。</p>`}</div>
    <div class="ah-foot">🕒 一个对话一份记忆 · 输出目录各自独立</div></aside>`;
}

function asEmpty(){                       // 空状态：没绑专家 = 智研管家 Q 版小人居中；绑了 = 专家头像
  const a = (S.assistants||[]).find(x=>x.id === (((S.chats||[]).find(c=>c.id===curChat)||{}).assistant_id||"")) || null;
  let ps = []; try{ ps = JSON.parse((a&&a.prompts)||"[]"); }catch(e){ ps = []; }
  const chips = document.getElementById("msgs");
  const face = faceOf(a, 72);          // 没绑专家也是 72px 的同一块方框，不再放大
  return `<div class="at-empty-wrap" style="display:flex;flex-direction:column;align-items:center;gap:20px;padding:0 24px;text-align:center;max-width:560px;margin:auto">
    ${face}
    <div style="display:flex;flex-direction:column;gap:6px">
      <span style="font-size:16px;font-weight:600">${esc(a?a.name:"智研管家")}</span>
      <span style="font-size:13px;color:var(--muted)">${esc(a&&a.description ? a.description : "全局工作台助手 · 不限项目，帮你查资料、排日程、改论文、把活派给专家。直接说你要做什么。")}</span></div>
    ${ps.length?`<div class="as-chips">${ps.map(p=>`<button class="as-chip" data-chip="${esc(p)}">${esc(p)}</button>`).join("")}</div>`:""}</div>`;
}
function bindChips(){
  document.querySelectorAll("[data-chip]").forEach(b=>b.onclick=()=>{
    const t=$("ask"); if(!t) return; t.value = b.dataset.chip; t.focus();
    if(typeof grow === "function") grow();
  });
}
function renderAgent(m){
  m.style.padding = "10px";
  const cur = (S.chats||[]).find(t=>t.id===curChat);
  const boundA = (S.assistants||[]).find(a=>a.id === ((cur&&cur.assistant_id)||"")) || null;
  const recs = (S.chatEvents||[]).length;
  m.innerHTML = `<div class="agent">
    ${renderHistory()}
    <section class="at" aria-label="对话主线">
      <header class="at-head">
        <div class="at-heading"><span class="at-idwrap" id="at-who" title="这个对话绑定的专家（去「专家」页管理）">${faceOf(boundA, 32)}</span>
          <div><h1 id="at-title" class="at-title" title="点击改名">${esc(cur?cur.title:"Hi，今天有什么安排？")}</h1>
          <p id="at-sub">${atSubHTML(cur)}</p></div></div>
        <button class="at-profile" id="at-profile" title="在「设置」里改模型与权限">
          <span>⚙</span><b>${esc(S.active? (S.active.name+" · "+S.active.model) : "未选择模型")}</b><em>自动批准</em></button>
      </header>
      <div class="at-tabs">
        <button class="at-tab ${agentView==="conversation"?"on":""}" data-v="conversation">对话</button>
        <button class="at-tab ${agentView==="trajectory"?"on":""}" data-v="trajectory">轨迹 ${recs}</button></div>
      <div class="at-msgs" id="msgs" role="log" aria-label="当前会话消息"></div>
      <div class="at-dock">
        <div class="at-panel" id="at-panel">
          <div class="st-box st-counts">
            <div class="st-row"><span>对话轮数</span><b id="st-turns">0</b></div>
            <div class="st-row"><span>调用工具数</span><b id="st-calls">0</b></div>
            <div class="st-row"><span>消耗 token</span><b id="st-tok">0</b></div>
          </div>
          <div class="st-box"><p>工具名称</p><div class="st-log" id="st-tools"></div></div>
          <div class="st-box"><p>产出目录</p><div class="st-log" id="st-outs"></div></div>
        </div>
        <div class="atts" id="atts"></div>
        <div class="composer" id="composer">
          <textarea id="ask" rows="1" placeholder="发消息给${esc(boundA?boundA.name:"智研管家")}…（Enter 发送，Shift+Enter 换行）"></textarea>
          <div class="at-tools">
            <div class="at-tools-l">
              <div class="plus-wrap">
                <button class="icon-btn" id="plus" title="添加上下文" aria-label="添加上下文">＋</button>
                <div class="att-menu" id="attmenu" style="display:none">
                  <div class="lbl">附加</div>
                  <button data-a="files">📄 文件…</button>
                  <button data-a="folder">📁 文件夹…</button>
                  <button data-a="images">🖼 图片…</button>
                  <button data-a="paste">📋 粘贴图片</button>
                  <button data-a="url">🔗 URL…</button>
                  <div class="sep"></div>
                  <div class="tip">提示：把文件直接拖到输入框也能附加。</div>
                </div>
              </div>
              <label class="at-sel" title="把当前对话绑到一个项目上（绑了：记忆/文献/产出都跟项目走；不绑：跨项目的全局对话）">🧭<select id="projbind">
                <option value="">不绑项目</option>
                ${(S.tasks||[]).map(t=>`<option value="${t.id}" ${cur&&cur.task_id===t.id?"selected":""}>${esc(t.title)}</option>`).join("")}</select></label>
              <button class="at-sel at-outbtn" id="outpick" title="选择这个对话的产出文件夹（原生文件夹选择框）">📁
                <span id="outdir-txt">${esc(cur?outName(cur.out_dir):"默认产出目录")}</span></button>
              <label class="at-sel" title="这个对话用哪个模型档案">🔌<select id="modpick"><option value="">未配置模型</option>
                ${S.models.map(mm=>`<option value="${mm.id}" ${S.active&&mm.id===S.active.id?"selected":""}>${esc(mm.name)}</option>`).join("")}</select></label>
            </div>
            <div class="at-tools-r">
              <label class="at-sel" title="这个对话用哪个专家（在「专家」页增删改）">${boundA?esc(boundA.avatar||"👨‍🔬"):`<img src="${Q_AV}" alt="" width="16" height="16" style="object-fit:contain;vertical-align:-3px">`}<select id="assist">
                <option value="">智研管家（全局）</option>
                ${(S.assistants||[]).filter(a=>a.enabled!==0 && a.id!=="builtin-steward").map(a=>`<option value="${a.id}" ${cur&&cur.assistant_id===a.id?"selected":""}>${esc(a.name)}</option>`).join("")}</select></label>
              <button class="send-btn" id="send" title="发送" aria-label="发送">↑</button>
            </div>
          </div>
          <input type="file" id="filepick" multiple style="display:none">
        </div>
        <p class="at-hint">Enter 发送 · Shift + Enter 换行 · 附件用左侧 <code>＋</code> 或直接把文件拖进输入框 · 权限：自动批准（本地任务/日历/笔记写入直接生效）</p>
      </div>
    </section></div>`;
  const tEl = $("at-title");
  if(tEl && cur) tEl.onclick = ()=>{          // 点标题原地改名
    const inp = document.createElement("input");
    inp.className = "control"; inp.value = cur.title; inp.style.cssText = "font-size:16px;font-weight:700;max-width:320px";
    tEl.replaceWith(inp); inp.focus(); inp.select();
    const commit = async ()=>{
      const v = (inp.value||"").trim();
      if(v && v !== cur.title){
        try{ await api("/api/chat/rename", {id:cur.id, title:v}); }catch(e){ alert("改名失败："+e.message); }
        await refreshState(false);
      }
      render();
    };
    inp.onblur = commit;
    inp.onkeydown = (e)=>{ if(e.key==="Enter"){ e.preventDefault(); commit(); } if(e.key==="Escape") render(); };
  };
  const whoEl = $("at-who"); if(whoEl) whoEl.onclick = ()=>openTab("assistants");
  // 左栏
  m.querySelectorAll("[data-open]").forEach(b=>b.onclick=()=>{ curChat=b.dataset.open; localStorage.setItem("rw-chat",curChat); render(); });
  m.querySelectorAll("[data-sel]").forEach(b=>b.onchange=()=>{ b.checked?ahSel.add(b.dataset.sel):ahSel.delete(b.dataset.sel); render(); });
  m.querySelectorAll("[data-del]").forEach(b=>b.onclick=async(e)=>{
    e.stopPropagation(); const t=(S.chats||[]).find(x=>x.id===b.dataset.del);
    if(!confirm(`删除对话「${t?t.title:""}」？这段对话的记忆（消息）会一起删掉，不可撤销。`)) return;
    try{ await api("/api/chat/delete",{id:b.dataset.del}); }catch(err){ return alert("删除失败："+err.message); }
    ahSel.delete(b.dataset.del);
    if(curChat===b.dataset.del){ curChat=null; localStorage.removeItem("rw-chat"); }
    await refreshState(false); render();
  });
  $("ah-new").onclick = async ()=>{
    const r = await api("/api/chat/create",{title:""});      // 通用助手对话：不绑项目
    curChat = r.chat.id; localStorage.setItem("rw-chat",curChat);
    await refreshState(false); render();
  };
  const allBox = $("ah-all");
  if(allBox) allBox.onchange = ()=>{ ahSel.clear(); if(allBox.checked) (S.chats||[]).forEach(t=>ahSel.add(t.id)); render(); };
  const delSel = $("ah-del-sel");
  if(delSel) delSel.onclick = async ()=>{
    if(!confirm(`删除选中的 ${ahSel.size} 个对话？它们的对话记忆会一起删，不可撤销。`)) return;
    for(const id of [...ahSel]){ try{ await api("/api/chat/delete",{id}); }catch(e){} if(curChat===id){ curChat=null; localStorage.removeItem("rw-chat"); } }
    ahSel.clear(); await refreshState(false); render();
  };
  // 右栏头 + 视图切换
  m.querySelectorAll("[data-v]").forEach(b=>b.onclick=()=>{ agentView=b.dataset.v; renderAgent(m); });
  $("at-profile").onclick = ()=> openTab("settings");      // 模型配置已并入设置页
  $("outpick").onclick = async ()=>{                        // 空 dir = 服务端弹原生「选择文件夹」
    if(!curChat) return alert("先新建一个对话");
    const btn=$("outpick"), txt=$("outdir-txt"), old=txt.textContent;
    btn.disabled=true; txt.textContent="选择中…";
    try{
      const r = await api("/api/chat/outdir",{id:curChat});
      if(r.cancelled){ txt.textContent = old; }
      else{
        S.chats = (S.chats||[]).map(c=> c.id===r.chat.id ? r.chat : c);
        txt.textContent = outName(r.chat.out_dir);
        await refreshState(false); paintStats(); render();
      }
    }catch(e){ txt.textContent = old; alert("选择文件夹失败："+e.message); }
    btn.disabled=false;
  };
  $("modpick").onchange = async (e)=>{ if(!e.target.value) return; await api("/api/models/active",{id:e.target.value}); await refreshState(false); render(); };
     $("assist").onchange = async (e)=>{
       if(!curChat){ alert("先新建一个对话，再选助手。"); return; }
       try{ await api("/api/chat/assistant", {id:curChat, assistant_id:e.target.value}); }catch(err){ return alert("绑定失败："+err.message); }
       await refreshState(false); render();
     };
  const pb = $("projbind");
  if(pb) pb.onchange = async ()=>{
    if(!curChat){ const r = await api("/api/chat/create", {}); curChat = r.id || (r.chat&&r.chat.id); localStorage.setItem("rw-chat", curChat); }
    try{ await api("/api/chat/bind", {id:curChat, task_id:pb.value}); }catch(e){ return alert("绑定失败："+e.message); }
    await refreshState(false); render();
  };
  // 输入区（我们自己的：＋ 菜单 / 拖放 / 粘贴 / 自动长高）
  $("send").onclick = send;
  $("ask").onkeydown = (e)=>{ if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send();} };
  $("ask").oninput = grow;
  $("ask").onpaste = (e)=>{ const fs=[...(e.clipboardData?.files||[])]; if(fs.length){ e.preventDefault(); addFiles(fs); } };
  $("plus").onclick = (e)=>{ e.stopPropagation(); const mm=$("attmenu"); mm.style.display = mm.style.display==="none" ? "block" : "none"; };
  $("attmenu").querySelectorAll("[data-a]").forEach(b=>b.onclick=()=>pick(b.dataset.a));
  $("filepick").onchange = (e)=>{ const fs=[...e.target.files]; e.target.value=""; addFiles(fs); };
  wireDrop($("composer"));
  setAtts(); grow();
  if(!curChat){
    $("msgs").innerHTML = asEmpty(); bindChips();
    loadAgentMeta();
  } else {
    loadChat();
  }
}
async function loadChat(){
  try{
    const {messages} = await api(`/api/chat?id=${encodeURIComponent(curChat)}`);
    S.chat = messages || [];
    S.chatEvents = S.chat.flatMap(m=> m.events || []);
    const tb = document.querySelector('[data-v="trajectory"]');
    if(tb) tb.textContent = `轨迹 ${S.chatEvents.length}`;
    const box=$("msgs"); if(!box) return;
    poseN = 0;                                              // 每进一次对话，管家姿势从头轮
    if(agentView==="trajectory"){ renderTrajectory(box); }
    else if(!S.chat.length){
      box.innerHTML = asEmpty(); bindChips();
    } else {
      box.innerHTML="";
      S.chat.forEach(m=>appendOne(box,{type:m.role,content:m.content,events:m.events,replay:true,at:m.created_at}));
    }
    box.scrollTop = box.scrollHeight;
    loadAgentMeta();
  }catch(e){}
}
function renderTrajectory(box, evs = S.chatEvents){  // 轨迹：把 tool_call / tool_result 配成一条条记录
  evs = evs || [];
  const calls = evs.filter(e=>e.type==="tool_call");
  if(!calls.length){ box.innerHTML = emptyState("🧭","还没有运行轨迹","这段对话还没有发起过 Agent 运行。"); return; }
  const results = evs.filter(e=>e.type==="tool_result");
  box.innerHTML = `<div style="display:grid;gap:12px;width:100%">
    <div class="st-row" style="justify-content:flex-start;gap:14px;height:26px"><span>运行 1</span><span>${calls.length} 次工具调用</span><span>${fmtDate(new Date().toISOString())}</span></div>
    ${calls.map((c,i)=>{ const r = results[i] || {};
      return `<details class="tool-card ${r.ok===false?"bad":""}" open>
        <summary><span>🔧</span><span class="tool-name">#${i+1} ${esc(c.name)}</span>
          <span class="tool-detail">${esc(r.summary||"")}</span>
          <span class="tool-dur">${r.ok===false?"失败":(r.summary?"完成":"进行中")}</span></summary>
        <div class="tool-body"><p class="tool-blk">Input</p><pre>${esc(JSON.stringify(c.args||{},null,2))}</pre>
          ${r.summary?`<p class="tool-blk">Output</p><pre>${esc(r.summary)}</pre>`:""}</div></details>`;}).join("")}
  </div>`;
}
function appendOne(box,ev){
  if(ev.replay && ev.type==="assistant" && (ev.events||[]).length){
    // 事件流里已经含这条回复的 assistant 文本事件；再按整条消息渲染一次就会重复一整行，
    // 所以嵌套事件里只要渲染出了 assistant 行，就不再画外层那一条。没有 assistant 事件的
    // 老消息（只有 usage / 空回复）照旧走下面整条渲染，内容不丢。
    const before = box.querySelectorAll(".msg-row.msg-assistant").length;
    ev.events.forEach(e=>appendOne(box,e));
    if(box.querySelectorAll(".msg-row.msg-assistant").length > before) return;
  }
  const el=document.createElement("div");
  if(ev.sub) el.style.marginLeft="14px";                 // 子 agent 的行缩进一层
  if(ev.type==="user"){
    el.className="msg-row msg-user";
    el.innerHTML=`<div class="msg-meta"><span class="msg-ava">👤</span><strong>你</strong><time>${esc(ev.at?fmtDate(ev.at):clock())}</time></div><p>${esc(ev.content)}</p>`;
  }
  else if(ev.type==="assistant"){
    const cur = (S.chats||[]).find(t=>t.id===curChat);
    const boundA = (S.assistants||[]).find(a=>a.id === ((cur&&cur.assistant_id)||"")) || null;
    const avHtml = faceOf(boundA, 22, true);
    const aName = boundA ? boundA.name : "智研管家";
    el.className="msg-row msg-assistant";
    el.innerHTML=`<div class="msg-meta">${avHtml}<strong>${esc(aName)}</strong>${ev.sub?`<span class="sub-tag">⤷ ${esc(ev.sub_title||"")}</span>`:""}<span class="msg-chip">${esc(assistantKey())}</span><span class="msg-chip">完成</span></div><div class="md">${md(ev.content||"…")}</div>`;
  }
  else if(ev.type==="error"){ el.className="msg-err"; el.textContent="⚠ "+(ev.message||"出错了"); }
  else if(ev.type==="sub"){
    if(ev.phase==="start"){
      el.className="sub-row"; el.id="sub-"+ev.sub;
      el.innerHTML=`<span class="sub-ico">⤷</span><span class="sub-title">${esc(ev.sub_title||"子任务")}</span>
        <span class="sub-note">独立上下文 · 只读工具 · 跑完只回摘要</span><span class="sub-state">运行中…</span>`;
      box.appendChild(el); box.scrollTop=box.scrollHeight; return;
    }
    const row=$("sub-"+ev.sub);
    if(row){
      row.classList.add(ev.ok===false?"bad":"done");
      const st=row.querySelector(".sub-state"); if(st) st.textContent=ev.ok===false?"失败":"完成";
      const nt=row.querySelector(".sub-note"); if(nt) nt.textContent=ev.summary||"（已回摘要）";
    }
    box.scrollTop=box.scrollHeight; return;
  }
  else if(ev.type==="tool_call"){
    el.className="tool-card"; el.id="tc"+(seq++);
    el.innerHTML=`<details open><summary><span>🔧</span><span class="tool-name">${esc(ev.name)}</span>${ev.sub?`<span class="sub-tag">⤷ ${esc(ev.sub_title||"")}</span>`:""}
      <span class="tool-detail">调用中…</span><span class="tool-dur">进行中</span></summary>
      <div class="tool-body"><p class="tool-blk">Input</p><pre>${esc(JSON.stringify(ev.args||{},null,2))}</pre></div></details>`;
    box.appendChild(el); box.scrollTop=box.scrollHeight; return;
  }
  else if(ev.type==="tool_result"){
    const cards=box.querySelectorAll(".tool-card");
    const card=cards.length?cards[cards.length-1]:null;
    if(card){
      if(!ev.ok) card.classList.add("bad");
      const d=card.querySelector(".tool-detail"); if(d) d.textContent=ev.summary||"";
      const u=card.querySelector(".tool-dur"); if(u) u.textContent=ev.ok?"完成":"失败";
      const b=card.querySelector(".tool-body");
      if(b) b.insertAdjacentHTML("beforeend",`<p class="tool-blk">Output</p><pre>${esc(ev.summary||"")}</pre>`);
    }
    box.scrollTop=box.scrollHeight; return;
  }
  else return;
  box.appendChild(el); box.scrollTop=box.scrollHeight;
}
/* ---------------- 附件（+ 菜单 / 拖放 / 粘贴，同一套入口） ---------------- */
const atts = [];                                  // 待发送附件；页签重渲染不丢
const fmtSize = (n)=> !n ? "" : n>=1048576 ? (n/1048576).toFixed(1)+"MB" : Math.max(1,Math.round(n/1024))+"KB";

function setAtts(){
  const host=$("atts"); if(!host) return;
  host.innerHTML = atts.map((a,i)=>`<span class="att"><b>${esc(a.name)}</b><span>${a.url?"链接":a.paper_id?"已入库 "+a.pages+" 页":fmtSize(a.size)}</span><span class="x" data-rm="${i}" title="移除">✕</span></span>`).join("");
  host.querySelectorAll("[data-rm]").forEach(x=>x.onclick=()=>{ atts.splice(+x.dataset.rm,1); setAtts(); });
}

async function addFiles(files){
  if(!files || !files.length) return;
  for(const f of files){
    if(!curTask && /\.pdf$/i.test(f.name)){ alert("先选或建一个项目 —— PDF 要入当前项目的文献库，Agent 才读得到"); return; }
    try{
      const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}&task_id=${encodeURIComponent(curTask||"")}`,
                            {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:f});
      const a = await r.json(); if(a.error) throw new Error(a.error);
      atts.push(a);
    }catch(e){ alert("附加失败："+e.message); }
  }
  setAtts();
}

function pick(mode){
  const mm=$("attmenu"); if(mm) mm.style.display="none";
  if(mode==="url"){
    const u = prompt("要附加的链接（http / https）：");
    if(!u) return;
    if(!/^https?:\/\//i.test(u)) return alert("只认 http / https 链接");
    atts.push({name:u.replace(/^https?:\/\//i,"").slice(0,60), url:u, size:0}); setAtts(); return;
  }
  if(mode==="paste") return void pasteImage();
  const inp=$("filepick"); if(!inp) return;
  inp.accept = mode==="images" ? "image/*" : "";
  if(mode==="folder") inp.setAttribute("webkitdirectory",""); else inp.removeAttribute("webkitdirectory");
  inp.click();
}

async function pasteImage(){
  try{
    const items = await navigator.clipboard.read(); const blobs = [];
    for(const it of items) for(const t of it.types) if(t.startsWith("image/"))
      blobs.push(new File([await it.getType(t)], `粘贴图片.${t.split("/")[1]}`, {type:t}));
    if(!blobs.length) return alert("剪贴板里没有图片");
    await addFiles(blobs);
  }catch(e){ alert("拿不到剪贴板（浏览器没放权），直接在输入框里 Ctrl+V 粘贴图片即可"); }
}

function wireDrop(el){                            // 照 use-composer-drop：进出计数 + dropEffect=copy
  if(!el) return;
  let depth = 0;
  const has = (dt)=> !!dt && Array.from(dt.types||[]).includes("Files");
  el.ondragenter = (e)=>{ if(!has(e.dataTransfer)) return; e.preventDefault(); depth++; el.classList.add("drop-on"); };
  el.ondragover  = (e)=>{ if(!has(e.dataTransfer)) return; e.preventDefault(); e.dataTransfer.dropEffect="copy"; };
  el.ondragleave = (e)=>{ if(!has(e.dataTransfer)) return; e.preventDefault(); depth=Math.max(0,depth-1); if(!depth) el.classList.remove("drop-on"); };
  el.ondrop      = (e)=>{ if(!has(e.dataTransfer)) return; e.preventDefault(); depth=0; el.classList.remove("drop-on"); addFiles([...e.dataTransfer.files]); };
}

/* 量高度用隐藏镜像那个 textarea：直接读真实框的 scrollHeight 会把
   占位提示的折行也算进去，空框就涨成好几行。镜像只放真正的内容，宽度跟真实框对齐。 */
const TA_MIN = 42, TA_MAX = 200;                 // 单行高度跟着 .composer textarea 的 padding/行高走
let taMirror = null;
function measureAsk(ta){
  if(!taMirror){
    taMirror = document.createElement("textarea");
    taMirror.className = "ta-mirror"; taMirror.setAttribute("aria-hidden","true");
    taMirror.rows = 1;                             // 不给 rows，空值时按默认 2 行算
    document.body.appendChild(taMirror);
  }
  const cs = getComputedStyle(ta);
  taMirror.style.width = ta.clientWidth + "px";
  taMirror.style.font = cs.font;
  taMirror.style.lineHeight = cs.lineHeight;
  taMirror.style.padding = cs.padding;
  taMirror.style.letterSpacing = cs.letterSpacing;
  taMirror.style.whiteSpace = "pre-wrap";
  taMirror.style.wordBreak = cs.wordBreak;
  taMirror.value = ta.value;
  taMirror.style.height = "auto";
  return taMirror.scrollHeight;
}

function grow(){                                  // 一行起步，真换行了才长高
  const ta=$("ask"); if(!ta) return;
  ta.style.height = Math.min(TA_MAX, Math.max(TA_MIN, measureAsk(ta))) + "px";
}
addEventListener("resize", ()=>{ if($("ask")) grow(); });

document.addEventListener("click",(e)=>{          // 点菜单外面收起（监听只注册一次）
  const mm=$("attmenu");
  if(mm && mm.style.display!=="none" && !mm.contains(e.target) && e.target.id!=="plus") mm.style.display="none";
});

async function send(){
  const ta=$("ask"); const text=ta.value.trim(); if(!text && !atts.length) return;
  // 没对话（或 localStorage 里那条已经删了）就直接发 → 自动新建一条不绑项目的对话，不弹框拦人
  if(!curChat || !(S.chats||[]).some(c=>c.id===curChat)){
    try{
      const r = await api("/api/chat/create", {title:""});
      curChat = r.chat.id; localStorage.setItem("rw-chat", curChat);
      await refreshState(false); render();        // 侧栏与抬头立刻换成这条新对话
    }catch(e){ return alert("新建对话失败：" + e.message); }
  }
  const msg = atts.length ? (text?text+"\n\n":"") + "[附件]\n" + atts.map(a=> a.url ? `- ${a.url}`
      : `- ${a.name}${a.paper_id?`（已入库 PDF，paper_id=${a.paper_id}，${a.pages} 页）`:`（本地文件：${a.path}）`}`).join("\n") : text;
  ta.value=""; grow(); atts.length=0; setAtts();
  const box=$("msgs"); if(box.querySelector(".muted")) box.innerHTML="";
  appendOne(box,{type:"user",content:msg});
  const btn=$("send"); btn.disabled=true; btn.classList.add("stop"); btn.textContent="■";
  let errMsg="";
  try{
    const res = await fetch("/api/agent",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task_id:curChat,message:msg})});
    if(!res.ok || !res.body) throw new Error("HTTP "+res.status);
    const reader=res.body.getReader(); const dec=new TextDecoder(); let buf="";
    for(;;){
      const {value,done}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf("\n\n"))>=0){
        const chunk=buf.slice(0,i); buf=buf.slice(i+2);
        const line=chunk.split("\n").find(l=>l.startsWith("data: ")); if(!line) continue;
        let ev; try{ ev=JSON.parse(line.slice(6)); }catch{ continue; }
        if(ev.type!=="done" && ev.type!=="user") appendOne(box,ev);  // user 已经本地先画了，服务端回声不再画一遍
        stEvent(ev);
      }
    }
  }catch(e){ errMsg = e.message; appendOne(box,{type:"error",message:e.message}); }
  btn.disabled=false; btn.classList.remove("stop"); btn.textContent="↑";
  await refreshState(false); loadChat();
  if(errMsg) appendOne(box,{type:"error",message:errMsg});   // loadChat 重渲染会把报错行刷掉，补回去
}
/* 展示区：对话轮数 / 调用工具数 / 消耗 token（各一行）+ 工具名称、产出目录（各三行滚动） */
const st = { turns:0, calls:0, tokBase:0, tok:0, tools:[], outs:[] };
const ST_KEEP = 40;                                  // 滚动窗口里最多留多少条
function curChatRow(){ return (S.chats||[]).find(c=>c.id===curChat) || null; }
function stPaint(){
  const room = curChatRow();
  const folder = room ? (room.out_dir || "默认产出目录（工作区 out）") : "";
  const t=$("st-turns"), c=$("st-calls"), k=$("st-tok");
  if(t) t.textContent = st.turns;
  if(c) c.textContent = st.calls;
  if(k) k.textContent = (st.tokBase+st.tok).toLocaleString();
  const log=(id,rows,empty)=>{ const box=$(id); if(!box) return;
    box.innerHTML = rows.length ? rows.map(r=>`<div class="${r.k||""}" title="${esc(r.t)}">${esc(r.t)}</div>`).join("")
                                : `<div class="none" title="${esc(empty)}">${esc(empty)}</div>`;
    box.scrollTop = box.scrollHeight; };
  log("st-tools", st.tools.slice(-ST_KEEP).map(n=>({t:n})), "还没有工具调用");
  log("st-outs", st.outs.slice(-ST_KEEP), folder? "还没有产出" : "还没有产出");
}
function stReset(){ Object.assign(st,{turns:0,calls:0,tokBase:0,tok:0,tools:[],outs:[]}); }
function loadAgentMeta(){                            // 从已存的对话里回放
  stReset();
  const evs = (S.chat||[]).flatMap(m=>m.events||[]);
  st.turns = (S.chat||[]).filter(m=>m.role==="user").length;
  st.calls = evs.filter(e=>e.type==="tool_call").length;
  st.tokBase = evs.filter(e=>e.type==="usage").reduce((n,e)=>n+(e.total||0),0);
  st.tools = evs.filter(e=>e.type==="tool_call").map(e=>e.name);
  st.outs = evs.filter(e=>e.type==="tool_result"&&e.path).map(e=>({t:e.path,k:"dir"}));
  const room = curChatRow();
  if(room && (room.out_dir||"").trim()) st.outs.unshift({t:room.out_dir, k:"dir"});   // 目录常驻第一行
  if(room){                                            // 首次提问后标题会变，顺手同步抬头
    const h=$("at-title"), s=$("at-sub");
    if(h) h.textContent = room.title;
    if(s) s.innerHTML = atSubHTML(room);
  }
  stPaint();
}
function stEvent(ev){                                // SSE 过程中实时刷新
  if(!ev || ev.type==="done") return;
  if(ev.type==="user") st.turns++;
  else if(ev.type==="usage") st.tok += ev.total || 0;   // 事件里是每次回包的用量，可累加
  else if(ev.type==="tool_call"){ st.calls++; st.tools.push(ev.name); }
  else if(ev.type==="tool_result"){ if(ev.path) st.outs.push({t:ev.path,k:"dir"}); }
  else return;
  stPaint();
}
