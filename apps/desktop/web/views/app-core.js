let zenModeActive = false;
let statusCollapsed = localStorage.getItem("rw-status-collapsed") === "true";

function toggleZenMode(force){
  zenModeActive = typeof force === "boolean" ? force : !zenModeActive;
  document.body.classList.toggle("zen-mode", zenModeActive);
  let exitBtn = document.getElementById("zenExitPill");
  if(zenModeActive){
    if(!exitBtn){
      exitBtn = document.createElement("button");
      exitBtn.id = "zenExitPill";
      exitBtn.className = "zen-exit-pill";
      exitBtn.textContent = "⛶ 退出专注 (Esc)";
      exitBtn.title = "退出专注工作区模式";
      exitBtn.onclick = () => toggleZenMode(false);
      document.body.appendChild(exitBtn);
    }
  } else {
    if(exitBtn) exitBtn.remove();
  }
}

function toggleSidebarCollapse(){
  sidebarCollapsed = !sidebarCollapsed;
  localStorage.setItem("rw-sidebar", sidebarCollapsed);
  renderSidebar();
}

document.addEventListener("keydown", (e) => {
  if((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B") && !e.shiftKey && !e.altKey){
    const tag = (document.activeElement?.tagName || "").toLowerCase();
    if(tag !== "textarea" && tag !== "input"){
      e.preventDefault();
      toggleSidebarCollapse();
    }
  }
  if(e.key === "F10"){
    e.preventDefault();
    toggleZenMode();
  }
  if(e.key === "Escape" && zenModeActive){
    e.preventDefault();
    toggleZenMode(false);
  }
});

/* ============================== 状态 ============================== */
const S = { tasks:[], chats:[], models:[], active:null, events:[], stages:[], tools:[], chat:[], chatEvents:[], loaded:false };
let tabs = [{id:"agent",route:"agent",title:"智研管家"}];
let activeTab = tabs[0].id;
let curTask = localStorage.getItem("rw-task") || null;
let tDir = "";                                   // 新建项目时选的「项目位置」，选完即用，不持久化
let curChat = localStorage.getItem("rw-chat") || null;   // Agent 页当前对话（独立于项目）


/* ---------------- 个人资料（照 lit：侧栏卡片 + 填写弹窗；卡片只显示头像和用户名） ---------------- */
const profile = Object.assign({ name:"研究生", avatar:"🎓", field:"", photo:"" },
  JSON.parse(localStorage.getItem("rw-profile") || "{}"));
/* 头像：有照片就用照片（本地缩成 128×128 存 localStorage），没有就回落 Emoji；默认 🎓 */
function avatarPhoto(){ const p = String(profile.photo || ""); return p.startsWith("data:image/") ? p : ""; }
function avatarInner(){                      // 给模板串用（侧栏 / 日历问候卡）
  const p = avatarPhoto();
  return p ? `<img class="avatar-img" src="${p}" alt="">` : esc(profile.avatar || "🎓");
}
function paintAvatar(){
  const a=$("profileAvatar");
  if(a){
    const p = avatarPhoto();
    if(p){ a.innerHTML = `<img class="avatar-img" src="${p}" alt="">`; a.classList.add("has-photo"); }
    else { a.textContent = profile.avatar || "🎓"; a.classList.remove("has-photo"); }
  }
  const h=$("calHelloAvatar");
  if(h){
    const p = avatarPhoto();
    if(p){ h.innerHTML = `<img class="avatar-img" src="${p}" alt="">`; }
    else h.textContent = profile.avatar || "🎓";
  }
}
function paintProfilePreview(){
  const img = $("profPhotoPrev"); if(!img) return;
  const p = avatarPhoto();
  if(p){ img.src = p; img.classList.add("on"); } else { img.removeAttribute("src"); img.classList.remove("on"); }
}
function readAvatarFile(file, cb){
  if(!file) return;
  if(!/^image\//.test(file.type || "")){ uiNotify("这不是图片文件"); return; }
  const fr = new FileReader();
  fr.onerror = ()=>uiNotify("读文件失败");
  fr.onload = ()=>{
    const im = new Image();
    im.onerror = ()=>uiNotify("这张图片读不了（格式可能不支持）");
    im.onload = ()=>{
      try{
        const S = 128, side = Math.min(im.width, im.height);
        const c = document.createElement("canvas"); c.width = c.height = S;
        const g = c.getContext("2d");
        g.drawImage(im, (im.width-side)/2, (im.height-side)/2, side, side, 0, 0, S, S);
        cb(c.toDataURL("image/jpeg", 0.86));
      }catch(e){ uiNotify("这张图片处理失败：" + e.message); }
    };
    im.src = fr.result;
  };
  fr.readAsDataURL(file);
}
function setAvatarPhoto(url){
  profile.photo = url || "";
  localStorage.setItem("rw-profile", JSON.stringify(profile));
  paintProfilePreview(); renderProfile();
}
function renderProfile(){
  const n=$("profileName");
  if(n) n.textContent = profile.name || "研究生";
  paintAvatar();
}
function openProfileModal(){
  $("profName").value = profile.name || "";
  $("profAvatar").value = profile.avatar || "🎓";
  $("profField").value = profile.field || "";
  paintProfilePreview();
  uiOpenModal($("profileModal"), $("profName"));
}
function closeProfileModal(){ uiCloseModal($("profileModal")); }
function saveProfileModal(){
  profile.name = ($("profName").value || "").trim() || "研究生";
  profile.avatar = ($("profAvatar").value || "").trim() || "🎓";
  profile.field = ($("profField").value || "").trim();
  localStorage.setItem("rw-profile", JSON.stringify(profile));
  renderProfile(); closeProfileModal();
}
function greetWord(){
  const h = new Date().getHours();
  return h<6 ? "夜深了" : h<12 ? "早上好" : h<14 ? "中午好" : h<18 ? "下午好" : "晚上好";
}
const helloText = ()=> `${greetWord()}，${profile.name || "研究生"}`;
let seq = 1;
const $ = (id)=>document.getElementById(id);
const esc = (s)=>String(s??"").replace(/[&<>"]/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

/* ---- Markdown 管线（marked + KaTeX 扩展 + DOMPurify 白名单） ---- */
let markdownMathSlots = null;
function mathSlot(text, display){
  if(!markdownMathSlots) return `<code>${esc(text)}</code>`;
  const id=markdownMathSlots.length;
  markdownMathSlots.push({text, display});
  const tag=display?"div":"span";
  return `<${tag} data-rw-math="${id}"></${tag}>`;
}
if(window.marked && window.marked.use){
  window.marked.use({gfm:true, breaks:true, extensions:[
    {name:"blockKatex", level:"block", start:(src)=>src.search(/\$\$|\\\[/),
     tokenizer(src){ const m=/^(?:\$\$\s*([\s\S]+?)\s*\$\$|\\\[\s*([\s\S]+?)\s*\\\])(?:\n|$)/.exec(src); return m?{type:"blockKatex",raw:m[0],text:m[1]||m[2]}:undefined; },
     renderer(tok){ return mathSlot(tok.text,true); }},
    {name:"inlineKatex", level:"inline", start:(src)=>src.search(/\$|\\[([]/),
     tokenizer(src){ const m=/^(?:\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\])/.exec(src); return m?{type:"inlineKatex",raw:m[0],text:m[1]||m[2]||m[3],display:!!m[3]}:undefined; },
     renderer(tok){ return mathSlot(tok.text,!!tok.display); }},
  ]});
}
if(window.DOMPurify && window.DOMPurify.addHook){
  window.DOMPurify.addHook("uponSanitizeAttribute", (_node, data)=>{   // 放行工具条写进去的 font/字号/对齐/高亮样式
    if(data.attrName === "style"){
      const ok = String(data.attrValue||"").split(";").map(s=>s.trim()).filter(s=>
        /^(?:font-family\s*:\s*[\w\s,"'-]+|font-size\s*:\s*\d{1,2}(?:\.\d+)?(?:px|pt|em|rem|%)|text-align\s*:\s*(?:left|center|right|justify)|background-color\s*:\s*(?:#[0-9a-f]{3,8}|[a-z]+)|color\s*:\s*(?:#[0-9a-f]{3,8}|[a-z]+))$/i.test(s));
      data.attrValue = ok.join("; "); data.keepAttr = ok.length > 0;
    }
    if(data.attrName === "href" && !/^(?:https?:|mailto:|#|\/)/i.test(String(data.attrValue||""))) data.keepAttr = false;
  });
}
const fmtDate = (iso)=>{ const d=new Date(iso||""); return isNaN(d) ? String(iso||"").slice(0,16).replace("T"," ") : d.toLocaleString("sv-SE",{hour12:false}).slice(0,16); };   // 本地时间、到分钟（直接切 ISO 字符串是 UTC，差 8 小时）
const today = ()=>new Date().toLocaleDateString("sv-SE");
const clock = (d = new Date())=>{ const w = "日一二三四五六"[d.getDay()];   // 2026.10.4 周日 23:55
  return `${d.getFullYear()}.${d.getMonth()+1}.${d.getDate()} 周${w} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; };

async function api(path, opts){
  // 两种调用约定都吃：api(path, {字段}) = POST JSON；api(path, {method,headers,body}) = 原样 fetch
  let init = {};
  if(opts && (opts.method || opts.body || opts.headers)) init = {...opts};
  else if(opts) init = {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(opts)};
  const r = await fetch(path, init);
  const j = await r.json().catch(()=>({error:"响应不是 JSON"}));
  if(!r.ok || (j && j.error)) throw new Error(j?.error || `请求失败（${r.status}）`);
  if(init.method && init.method !== 'GET' && (/^\/api\/todos\//.test(path) || path === '/api/step/done'))
    window.dispatchEvent(new CustomEvent('todos-changed'));
  return j;
}

/* ============================== 路由 ============================== */
const NAV = [
  {g:"primary",items:[
    {id:"agent",label:"智研管家",ico:"🤖"},
    {id:"assistants",label:"专家",ico:"👨‍🔬"},
    {id:"team",label:"团队",ico:"👥"},
    {id:"workspace",label:"项目工作区",ico:"🧭"},
    {id:"task-center",label:"任务与自动化",ico:"☑"},
    {id:"toolbox",label:"论文工具箱",ico:"🧰"},
    {id:"calendar",label:"日历",ico:"📅"}]},
  {g:"solo",items:[
    {id:"library",label:"知识库",ico:"📚"},

    {id:"intel",label:"情报日报",ico:"📡"}]},
  {g:"solo",items:[{id:"mail",label:"邮箱",ico:"📧"}]},
  {g:"foot",items:[{id:"settings",label:"设置",ico:"⚙️"}]}
];
const TITLES = {usage:"用量统计","task-center":"任务与自动化",toolbox:"论文工具箱",assistants:"专家",team:"团队",agent:"智研管家",workspace:"项目工作区",calendar:"日历",tasks:"项目管理",workflow:"项目工作区",library:"知识库",notes:"笔记",intel:"情报日报",mail:"邮箱",models:"模型",schedules:"定时自动化",space:"项目工作区",settings:"设置"};
const route = ()=> tabs.find(t=>t.id===activeTab)?.route || "agent";
function uiRouteIs(kind){
  return route()===kind || (route()==="workspace" && !!window.ProjectWorkspace?.hasSurface(kind));
}

async function uiNavigate(action){
  const workspace=window.ProjectWorkspace?.active();
  if(workspace && !await ProjectWorkspace.flush()) return;
  if(!workspace && typeof mdProj !== "undefined" && mdProj && document.getElementById("mdSource")?.getClientRects().length){
    if(!await pnSaveCurrent()){ uiNotify("笔记保存失败，请重试保存后再切换页面。"); return; }
  }
  return action();
}

function openTab(r, title){
  if(r==="schedules"){TaskCenter.requestView('automation');r='task-center';title='任务与自动化';}
  if(r==="tasks"&&window.ProjectManager){ProjectManager.open();return;}
  if(["workspace","space","workflow","notes"].includes(r) && window.ProjectWorkspace){
    ProjectWorkspace.requestRoute(r);r="workspace";title="项目工作区";
  }
  if(window.ProjectWorkspace?.active() && r!=="workspace" && ProjectWorkspace.hasDirty()){
    ProjectWorkspace.leave(()=>openTab(r,title));return;
  }
  if(r === "notes"){
    if(spId !== curTask){ spActiveNoteId = null; spSelectedNoteIds.clear(); }
    spId = curTask; spTab = "notes"; r = "space"; title = "项目空间";
  }
  const t = tabs.find(x=>x.route===r);
  if(t){ activeTab=t.id; return render(); }
  const id = "t"+(seq++); tabs.push({id,route:r,title:title||TITLES[r]||r}); activeTab=id;
  return render();
}
function closeTab(id){
  if(id===activeTab && window.ProjectWorkspace?.active() && ProjectWorkspace.hasDirty()){
    ProjectWorkspace.leave(()=>closeTab(id));return;
  }
  const i = tabs.findIndex(t=>t.id===id); if(i<0) return;
  tabs.splice(i,1);
  if(!tabs.length) tabs=[{id:"agent",route:"agent",title:"智研管家"}];
  if(activeTab===id) activeTab = tabs[Math.max(0,i-1)].id;
  render();
}

/* ============================== 外壳 ============================== */
let navResearchOpen = false;   // 「研究」分组已拆成三个平级入口，这个状态留着只是为了老 localStorage 不报错
let sidebarCollapsed = localStorage.getItem("rw-sidebar") === "true";

function renderSidebar(){
  const v = route();
  const item = (it,cls="")=>`<button class="nav-item ${v===it.id?"active":""} ${cls}" data-nav="${it.id}" title="${it.label}"><span class="nav-ico">${it.id==="agent"?`<img src="${Q_AV}" alt="" style="width:18px;height:18px;object-fit:contain;display:block;margin:0 auto">`:it.ico}</span><span class="lbl">${it.label}</span>${it.id==="mail"?`<span class="nav-badge hidden" id="navMailBadge">0</span>`:""}</button>`;
  const researchActive = NAV[1].items.some(i=>i.id===v);
  const open = navResearchOpen || researchActive;
  $("sidebar").className = "sidebar"+(sidebarCollapsed?" collapsed":"");
  $("sidebar").innerHTML = `
    <div class="brand"><img class="mark" src="/mark.png" alt="">
      <div class="lbl" style="min-width:0"><b>览籍智研</b><small>科研工作台</small></div>
      <button class="collapse" title="${sidebarCollapsed?"展开侧栏":"折叠侧栏"}">${sidebarCollapsed?"›":"‹"}</button></div>
    <button type="button" class="profile-card" id="profileCard" title="点击编辑个人资料">
      <span class="profile-avatar" id="profileAvatar">🎓</span>
      <div class="profile-info"><b id="profileName">研究生</b></div>
      <span class="profile-edit">✎</span></button>
    <nav class="nav">${NAV[0].items.map(i=>item(i)).join("")}
      ${NAV[1].items.map(i=>item(i)).join("")}${NAV[2].items.map(i=>item(i)).join("")}</nav>
    <div class="sidebar-foot">${item(NAV[NAV.length-1].items[0])}
      <div class="swatches" id="side-presets">${themeSwatches(true)}</div>
      <button class="nav-item" id="theme-toggle"><span class="nav-ico">${document.documentElement.dataset.theme==="dark"?"☀️":"🌙"}</span><span class="lbl">${document.documentElement.dataset.theme==="dark"?"浅色主题":"深色主题"}</span></button>
      <div class="online"><span class="dot"></span><span class="lbl">本地服务 已连接</span></div></div>`;
  $("sidebar").querySelectorAll("[data-nav]").forEach(b=>{
    b.setAttribute("aria-current", b.dataset.nav === v ? "page" : "false");
    b.onclick=()=>uiNavigate(()=>openTab(b.dataset.nav));
  });
  bindSwatches($("sidebar").querySelector("#side-presets"));
  $("sidebar").querySelector(".collapse").onclick=()=>{sidebarCollapsed=!sidebarCollapsed;localStorage.setItem("rw-sidebar",sidebarCollapsed);renderSidebar();};
  $("theme-toggle").onclick=()=>setTheme(document.documentElement.dataset.theme==="dark"?"light":"dark");
  $("profileCard").onclick = openProfileModal;
  $("btnProfClose").onclick = closeProfileModal;
  $("btnProfCancel").onclick = closeProfileModal;
  $("btnProfSave").onclick = saveProfileModal;
  document.querySelector("#profileModal .modal-mask").onclick = closeProfileModal;
  $("btnProfPhotoPick").onclick = ()=>$("profPhotoFile").click();
  $("profPhotoFile").onchange = (e)=>{
    const f = e.target.files && e.target.files[0];
    readAvatarFile(f, (url)=>setAvatarPhoto(url));       // 选完就立刻生效并落 localStorage
    e.target.value = "";
  };
  $("btnProfPhotoClear").onclick = ()=>setAvatarPhoto("");
  renderProfile();
}

// pw=1 = 跑在无边框自有窗口里（服务端加的标记），才需要自绘标题栏按钮
const FRAMELESS = new URLSearchParams(location.search).has("pw");
let winZoom = true;                    // 窗口现在是不是铺满工作区 → 决定右上那个键画「最大化」还是「还原」
let winStateAsked = false;
function winCommand(action){
  // 关闭时页面会消失，JS 桥接无法再回传结果；通过宿主 HTTP 通道关闭。
  if(action === "close") return api("/api/win/close", {});
  // 其余窗口命令优先走 pywebview js_api（=宿主进程）。HTTP 服务跑在 Low IL 沙箱里，
  // UIPI 会静默拦掉它对中完整性窗口的 ShowWindow / SetWindowPos / WM_CLOSE（全是「点了没反应」）。
  const py = window.pywebview && window.pywebview.api;
  return Promise.resolve(py && py.win ? py.win(action) : api("/api/win/" + action, {}));
}
function renderTopbar(){
  $("topbar").innerHTML = `
    <button type="button" class="topbar-icon-btn" id="btnTopToggleSidebar" title="折叠/展开主导航 (Ctrl+B)">☰</button>
    <span class="date">${clock()}</span>
    <span class="pill" title="当前模型">${S.active?("模型 "+esc(S.active.name)):"未配置模型"}</span>
    <div class="topbar-actions">
      <button type="button" class="btn ghost" id="usageStats">用量统计</button><button type="button" class="btn ghost" id="paperToolbox" title="中英互译、润色、引文与格式转换">论文工具箱</button>
      <button type="button" class="btn ghost btn-sm btn-zen" id="btnZenMode" title="全屏专注工作区 (F10)">⛶ 专注</button>
      <button type="button" class="btn ghost" data-command="palette" title="搜索命令与页面（Ctrl+K）">搜索命令 <kbd>Ctrl+K</kbd></button>
      <button type="button" class="btn ghost" data-command="help" title="快捷键与操作帮助（F1）" aria-label="快捷键与操作帮助">快捷键</button>
    </div>
    ${FRAMELESS?`<span class="wctl">
      <button title="最小化" data-w="minimize">\uE921</button>
      <button title="${winZoom?"还原":"最大化"}" data-w="maximize">${winZoom?"\uE923":"\uE922"}</button>
      <button class="close" title="关闭" data-w="close">\uE8BB</button>
    </span>`:""}`;
  $("topbar").querySelectorAll("[data-w]").forEach(b=>{
    b.setAttribute("aria-label", b.title);
    b.onmousedown = e => e.stopPropagation();          // 别让标题栏拖动吃掉按钮
    b.onclick = () => winCommand(b.dataset.w)
      .then(r=>{ if(r && typeof r.zoomed === "boolean") winSet(r.zoomed); })
      .catch(()=>{});
  });
  $("topbar").querySelectorAll(".topbar-actions button").forEach(b=>b.onmousedown=e=>e.stopPropagation());
  const btnTopSide = $("btnTopToggleSidebar");
  $("usageStats").onclick=()=>openTab("usage");
  $("paperToolbox").onclick=()=>PaperToolbox.open();
  if(btnTopSide) btnTopSide.onclick = toggleSidebarCollapse;
  const btnZen = $("btnZenMode");
  if(btnZen) btnZen.onclick = () => toggleZenMode();
  if(FRAMELESS && !winStateAsked){                     // 起来先问一次真实状态（还原回来可能不是铺满）
    winStateAsked = true;
    winRefresh();
  }
}
function winRefresh(){
  api("/api/win/state").then(r=>{
    if(r && typeof r.zoomed === "boolean") winSet(r.zoomed);
  }).catch(()=>{});
}
window.addEventListener("focus", ()=>{ if(FRAMELESS) winRefresh(); });   // 从任务栏还原回来时对齐一次
function winSet(zoom){
  if(zoom === winZoom) return;
  winZoom = zoom;
  renderTopbar();
}

function renderTabs(){
  localStorage.setItem("rw-route", route());
  $("tabsbar").innerHTML = `<div class="app-tablist" role="tablist" aria-label="已打开的页面">` + tabs.map(t=>`<div class="tab-wrap ${t.id===activeTab?"active":""}" data-tab-id="${t.id}">
    <button class="tab" role="tab" aria-selected="${t.id===activeTab}" tabindex="${t.id===activeTab?0:-1}" aria-controls="main" data-t="${t.id}">${esc(t.title)}</button>
    ${tabs.length>1?`<button type="button" class="tab-close" data-x="${t.id}" aria-label="关闭${esc(t.title)}" title="关闭标签页">✕</button>`:""}</div>`).join("")
    + `</div><button class="tab" id="newtab" title="打开智研管家" aria-label="打开智研管家">＋</button><button class="tab" id="tabActions" title="标签页操作" aria-label="标签页操作" aria-haspopup="menu">⋯</button>`;
  $("tabsbar").querySelectorAll("[data-t]").forEach(b=>b.onclick=()=>uiNavigate(()=>{
    activeTab=b.dataset.t; render(); document.querySelector(`[data-t="${activeTab}"]`)?.focus();
  }));
  $("tabsbar").querySelectorAll("[data-x]").forEach(b=>b.onclick=()=>uiNavigate(()=>{
    closeTab(b.dataset.x); document.querySelector(`[data-t="${activeTab}"]`)?.focus();
  }));
  $("newtab").onclick=()=>uiNavigate(()=>openTab("agent"));
}

function renderStatus(){
  const t = S.tasks.find(x=>x.id===curTask);
  $("statusbar").className = "statusbar" + (statusCollapsed ? " collapsed" : "");
  $("statusbar").innerHTML = `
    <span><span class="led"></span>本地服务 正常</span>
    <span><span class="led"></span>SQLite 正常</span>
    <span>模型 ${S.active?esc(S.active.name):"未配置"}</span>
    <span>工具 ${S.tools.length}</span>
    <span>当前项目 ${t?esc(t.title):"—"}</span>
    <span style="flex:1"></span>
    <button type="button" class="statusbar-toggle" id="btnToggleStatus" title="折叠/展开状态栏">${statusCollapsed ? "▲" : "▼"}</button>`;
  const btnSt = $("btnToggleStatus");
  if(btnSt){
    btnSt.onclick = () => {
      statusCollapsed = !statusCollapsed;
      localStorage.setItem("rw-status-collapsed", statusCollapsed);
      renderStatus();
    };
  }
}

/* ---------------- 弹出卡片：确认 / 提示 / 输入 ----------------
   全站别再用浏览器自带的 confirm/alert：样式跟界面脱节、还卡在浏览器自己的位置。
   这里统一渲染成屏幕正中的卡片，用同一套 .modal 皮肤（见 app.css）。 */
function uiNotify(text, ms = 3400) {
  const t = String(text ?? "");
  const bad = /失败|错误|不能|不允许|⚠|出错|拒绝/.test(t);
  let box = document.getElementById("uiToast");
  if (!box) {
    box = document.createElement("div");
    box.id = "uiToast";
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    box.onclick = () => box.remove();
    document.body.appendChild(box);
  }
  box.className = "ui-toast" + (bad ? " bad" : "");
  box.textContent = t;
  clearTimeout(uiNotify._t);
  uiNotify._t = setTimeout(() => box.remove(), ms);
}
                     // 全站 uiNotify(...) 一次性接到居中卡片（同步，不打断流程）

const uiModalOrigins = new WeakMap();
function uiOpenModal(modal, initial){
  if(!modal) return;
  if(modal.classList.contains("hidden")) uiModalOrigins.set(modal, document.activeElement);
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  if(!modal.hasAttribute("aria-label") && !modal.hasAttribute("aria-labelledby"))
    modal.setAttribute("aria-label", modal.querySelector("h3")?.textContent || "操作面板");
  modal.classList.remove("hidden");
  (initial || modal.querySelector("button, input, [tabindex]"))?.focus();
}
function uiCloseModal(modal){
  if(!modal) return;
  modal.classList.add("hidden");
  const origin = uiModalOrigins.get(modal);
  uiModalOrigins.delete(modal);
  if(origin?.isConnected) origin.focus();
  else document.querySelector('#tabsbar [aria-selected="true"]')?.focus();
}

function uiConfirm(text, opts = {}) {
  return new Promise(resolve => {
    const wrap = document.createElement("dialog");
    wrap.className = "modal";
    wrap.setAttribute("aria-label", opts.title || "确认操作");
    wrap.innerHTML = `
      <div class="modal-mask"></div>
      <div class="modal-dialog" style="width:${opts.width || 400}px">
        <div class="modal-head"><h3>${esc(opts.title || "确认操作")}</h3></div>
        <div class="modal-body"><p class="modal-text">${esc(text)}</p></div>
        <div class="modal-foot">
          <button class="btn ghost" data-no>${esc(opts.cancel || "取消")}</button>
          <button class="btn ${opts.danger === false ? "" : "danger"}" data-ok>${esc(opts.ok || "确定")}</button>
        </div>
      </div>`;
    const origin = document.activeElement;
    const done = v => { wrap.close(); wrap.remove(); if(origin?.isConnected) origin.focus(); resolve(v); };
    const key = e => {
      if(e.isComposing || e.keyCode === 229) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
    };
    wrap.querySelector("[data-ok]").onclick = () => done(true);
    wrap.querySelector("[data-no]").onclick = () => done(false);
    wrap.querySelector(".modal-mask").onclick = () => done(false);
    wrap.addEventListener("keydown", key);
    wrap.addEventListener("cancel", e=>{ e.preventDefault(); done(false); });
    document.body.appendChild(wrap);
    wrap.showModal();
    wrap.querySelector(opts.danger === false ? "[data-ok]" : "[data-no]").focus();
  });
}

function uiPrompt(text, def = "") {
  return new Promise(resolve => {
    const wrap = document.createElement("dialog");
    wrap.className = "modal";
    wrap.setAttribute("aria-label", text);
    wrap.innerHTML = `
      <div class="modal-mask"></div>
      <div class="modal-dialog" style="width:440px">
        <div class="modal-head"><h3>${esc(text)}</h3></div>
        <div class="modal-body"><input class="ui-input" id="uiPromptIn" aria-label="${esc(text)}" value="${esc(def)}"></div>
        <div class="modal-foot">
          <button class="btn ghost" data-no>取消</button>
          <button class="btn" data-ok>确定</button>
        </div>
      </div>`;
    const origin = document.activeElement;
    const done = v => { wrap.close(); wrap.remove(); if(origin?.isConnected) origin.focus(); resolve(v); };
    const inp = wrap.querySelector("#uiPromptIn");
    const key = e => {
      if(e.isComposing || e.keyCode === 229) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(null); }
      if (e.key === "Enter" && e.target === inp) { e.preventDefault(); done(inp.value); }
    };
    wrap.querySelector("[data-ok]").onclick = () => done(inp.value);
    wrap.querySelector("[data-no]").onclick = () => done(null);
    wrap.querySelector(".modal-mask").onclick = () => done(null);
    wrap.addEventListener("keydown", key);
    wrap.addEventListener("cancel", e=>{ e.preventDefault(); done(null); });
    document.body.appendChild(wrap);
    wrap.showModal();
    inp.focus(); inp.select();
  });
}

/* ---------------- 权限模式 ----------------
   首次启动问一次「允许所有 / 每次都要问」；ask 模式下编译、写文件这类动作逐次确认。
   存在后端 service_settings（一台机器一份），不是 localStorage —— 换浏览器/换窗口不会丢。 */
async function loadSecurity() {
  try { S.sec = await api("/api/security"); } catch (e) { S.sec = S.sec || {}; }
  return S.sec || {};
}
function uiChoice(title, text, okLabel, cancelLabel) {
  return uiConfirm(text, { title, ok: okLabel, cancel: cancelLabel, danger: false });
}
/* 需要许可时返回 true；mode=all 直接放行 */
async function needPerm(what) {
  if (((S.sec || {}).permission_mode) === "all") return true;
  return await uiChoice("需要你的许可", `这一次要${what}。允许吗？（「设置 → 权限」里可以改成不再逐次询问）`, "允许这一次", "先不要");
}
async function setPermissionMode(mode) {
  try { S.sec = await api("/api/security", { permission_mode: mode }); } catch (e) { uiNotify("保存失败：" + e.message); }
  return S.sec;
}

/* ---------------- 模型 Key 守卫 ----------------
   新装用户没配 Key 时，发消息/翻译/出计划一律先拦下来，别让一轮请求打到后端再回一句报错。
   「编译 .tex」不走这里：那是本地 xelatex，不需要任何 Key。 */
function hasModelKey() {
  if((S.models||[]).some(model=>{
    if(['codex-cli','claude-code-cli','antigravity-cli'].includes(model.api))return true;
    try{return ['localhost','127.0.0.1','[::1]'].includes(new URL(model.base_url).hostname);}catch{return false;}
  }))return true;
  if ((S.models || []).some(m => m.api_key)) return true;        // 任一档案填了 key（前端只会拿到 "***"）
  if (S.active && S.active.api_key) return true;
  return (S.providers || []).some(p => p.key_present);
}
async function requireModelKey() {
  if (hasModelKey()) return true;
  const go = await uiConfirm(
    "请先在「设置 → 模型列表」配置可用模型。云端接口通常需要 API Key，本机接口或账号客户端可使用各自的连接方式。",
    { title: "需要先配置模型", ok: "去设置", cancel: "知道了", danger: false });
  if (go){setSec='models';openTab("settings");}
  return false;
}
