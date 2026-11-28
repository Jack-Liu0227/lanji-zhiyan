/* ============================== 团队 · 一层平面 ==============================
   房间 = 团队：常驻办公室（项目在它就在）+ 加急办公室（智研管家为某件事拉人组建）。
   左边是图纸，右边是人员结构；小人由 rAF 驱动在房间里来回走，颜色即身份。 */

const OFFICE_INK = "#7E93A3";
const OFFICE_COLORS = ["#7E93A3", "#93A385", "#B08B84", "#C9A67E", "#9C8FA6", "#7E9C90",
                       "#B98F6A", "#8E8C86", "#A8A06B", "#C0A0A6", "#6F8B8E"];
const OFFICE_CN = { idle: "休眠", queued: "排队", running: "运行中", done: "完成", failed: "失败", blocked: "等待输入", awaiting_review: "待审查" };
let officeSel = "";
let officeRAF = 0;
let officeWalkers = [];
let officeRenderSequence = 0;
let officeRO = null;

const offEsc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const offAgo = (iso) => {
  if (typeof fmtAgo === "function") return fmtAgo(iso);
  return String(iso || "").replace("T", " ").slice(5, 16);
};
const offTint = (h) => `background:${h}1f;border-color:${h}99;color:${h}`;

/* ---------- 走动引擎 ----------
   每个人都有自己固定的桌子（位置由 JS 摆好，不随动画变）。
   有活（running/queued）→ 绝大多数时间坐在桌前敲键盘；没活 → 在房间里溜达，溜达累了回桌前坐一会。
   ponytail: 不做寻路与避障，目标点本身就是分开的；真要避让再加斥力。 */
class OfficeWalker {
  constructor(el, host, home, status) {
    this.el = el;
    this.in = el.querySelector(".in");
    this.host = host;
    this.home = home;
    this.status = status || "idle";
    this.hot = this.status === "running" || this.status === "queued";
    this.desk = el._desk || null;
    this.speed = (this.hot ? 20 : 15) + Math.random() * 8;
    this.fw = el.offsetWidth || 23;
    const tag = el.querySelector(".tag");
    this.tagHalf = Math.max(26, ((tag && tag.offsetWidth) || 72) * 0.5 + 8);
    this.x = this.clampX(home.x); this.y = home.y;
    this.tx = this.x; this.ty = this.y;
    this.dir = Math.random() < .5 ? 1 : -1;
    this.moving = false; this.seated = true; this.atHome = true;
    this.wait = Math.random() * 2.5;
    this.last = -1;
    this.sit();
    this.paint();
  }
  clampX(x) {
    const minX = Math.max(8, this.tagHalf - this.fw / 2);
    const maxX = Math.max(minX, this.host.clientWidth - this.tagHalf - this.fw / 2);
    return Math.min(maxX, Math.max(minX, x));
  }
  paint() {
    this.el.style.transform = `translate(${this.x}px,${this.y}px)`;
    this.in.style.transform = `scaleX(${this.dir})`;
  }
  spots() {
    const w = Math.max(0, this.host.clientWidth - 13), h = Math.max(0, this.host.clientHeight - 21);
    return [[.16, .74], [.36, .82], [.58, .82], [.78, .74], [.50, .48], [.18, .50], [.72, .50]]
      .map(([a, b]) => ({ x: this.clampX(Math.round(a * w)), y: Math.round(b * h) }));
  }
  pick() {
    const sp = this.spots();
    let i = this.last;
    while (i === this.last && sp.length > 1) i = Math.floor(Math.random() * sp.length);
    this.last = i;
    return sp[i];
  }
  sit() {
    this.seated = true; this.moving = false;
    this.el.classList.remove("walking");
    this.el.classList.add("seat");
    this.el.classList.toggle("typing", this.hot);
    if (this.desk) this.desk.dataset.busy = this.hot ? "1" : "0";
  }
  stand() {
    this.seated = false;
    this.el.classList.remove("seat", "typing");
    if (this.desk) this.desk.dataset.busy = "0";
  }
  walk(pt) {
    this.stand();
    const clampedHomeX = this.clampX(this.home.x);
    this.tx = this.clampX(pt.x); this.ty = pt.y;
    this.atHome = Math.abs(this.tx - clampedHomeX) < 2 && Math.abs(pt.y - this.home.y) < 2;
    this.moving = true;
    this.el.classList.add("walking");
  }
  decide() {
    const r = Math.random();
    if (this.hot) {                                  // 有活：坐得多，偶尔起身走两步再回来
      if (this.seated) {
        if (r < .18) this.walk(this.pick());
        else { this.wait = 3 + Math.random() * 7; this.el.classList.toggle("typing", r < .82); }
      } else this.walk(this.home);
    } else {                                         // 没活：溜达为主，累了回桌前坐
      if (this.seated) {
        if (r < .7) this.walk(this.pick());
        else this.wait = 4 + Math.random() * 9;
      } else if (r < .45) this.walk(this.home);
      else this.walk(this.pick());
    }
  }
  step(dt) {
    if (this.moving) {
      const dx = this.tx - this.x, dy = this.ty - this.y;
      const d = Math.hypot(dx, dy), v = this.speed * dt;
      if (d <= v + .2) {
        this.x = this.tx; this.y = this.ty; this.moving = false;
        this.el.classList.remove("walking");
        if (this.atHome) this.sit();
        else this.el.classList.remove("seat");
        this.wait = this.hot ? (1.2 + Math.random() * 2.4) : (1.8 + Math.random() * 4.5);
      } else {
        this.x += dx / d * v; this.y += dy / d * v;
        if (Math.abs(dx) > .4) this.dir = dx > 0 ? 1 : -1;
      }
      this.paint();
    } else {
      this.wait -= dt;
      if (this.wait <= 0) this.decide();
    }
  }
}

const OFFICE_CORR_SPOTS = [[.05, .5], [.30, .5], [.52, .5], [.74, .5], [.90, .5]];
const OFFICE_SKINS = ["#E8C7A8", "#DDB28C", "#C99A76", "#B98560"];   // 肤色四档
const offSkin = (m) => OFFICE_SKINS[Math.abs(offHash(m.assistant_id || m.name || "x")) % OFFICE_SKINS.length];
const offHair = (m) => Math.abs(offHash((m.assistant_id || m.name || "x") + "#h")) % 3;
function offHash(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return h; }

/* 摆桌子（固定位置）+ 把每个人放到自己桌前 */
function officeSit() {
  cancelAnimationFrame(officeRAF);
  officeWalkers = [];
  document.querySelectorAll(".tof-sheet .tof-room .tof-floor").forEach((floor) => {
    const desks = [...floor.querySelectorAll(".tof-desk")];
    const figs = [...floor.querySelectorAll(".tof-fig")];
    const W = floor.clientWidth, H = floor.clientHeight;
    const n = desks.length;
    // 尺寸全从 CSS 读（--ds 改桌子宽度 → em 跟着变），这里只算「半宽/半高」这类偏移
    const em = parseFloat(getComputedStyle(floor).fontSize) || 8.0;
    const DW = 10 * em, DH = 4.78 * em, FW = 2.83 * em, FH = 4.57 * em;
    // 2×2 格点：每间房 4 个工位，桌子中心落在格子中心，按人数从左上往右填
    const PAD_X = Math.max(20, Math.round(W * .07));
    const PAD_T = 62, PAD_B = 34;                 // 房间标题 / 底部流水条占掉的高度
    const cols = n === 1 ? 1 : 2;
    const rows = n <= 2 ? 1 : 2;
    const gw = Math.max(1, (W - PAD_X * 2) / cols), gh = Math.max(1, (H - PAD_T - PAD_B) / rows);
    desks.forEach((d, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const cx = PAD_X + gw * (col + .5), cy = PAD_T + gh * (row + .5);
      d.style.left = Math.min(Math.max(12, W - DW - 12), Math.max(12, Math.round(cx - DW / 2))) + "px";
      d.style.top = Math.min(Math.max(PAD_T, H - DH - PAD_B), Math.max(PAD_T, Math.round(cy - DH / 2))) + "px";
    });
    figs.forEach((el, i) => {
      const d = desks[i] || desks[desks.length - 1];
      const home = d
        ? { x: parseFloat(d.style.left) + DW / 2 - FW / 2, y: parseFloat(d.style.top) + DH - FH * .28 }
        : { x: W * .5, y: H * .5 };
      el._desk = d;
      officeWalkers.push(new OfficeWalker(el, floor, home, el.dataset.status));
    });
  });
  const corr = document.querySelector(".tof-corr");
  if (corr) corr.querySelectorAll(".tof-fig").forEach((el) => {
    const W = corr.clientWidth;
    officeWalkers.push(new OfficeWalker(el, corr, { x: Math.round(W * .5), y: 8 }, "running"));
  });
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) return;                                // 少动效：都坐在自己桌前，不再走
  let last = performance.now();
  const tick = (now) => {
    if (!document.querySelector(".tof-sheet")) return;
    const dt = Math.min((now - last) / 1000, .05);
    last = now;
    for (const w of officeWalkers) w.step(dt);
    officeRAF = requestAnimationFrame(tick);
  };
  officeRAF = requestAnimationFrame(tick);
}

/* ---------- 渲染 ---------- */
function officeRoom(p, colorOf) {
  const allMembers = [p.leader, ...(p.members || [])].filter(Boolean);
  const members = allMembers.slice(0, 4);   // 2×2 格点 = 4 个工位
  const running = allMembers.some((m) => m.status === "running" || m.status === "queued");
  const figs = members.map((m) => `<div class="tof-fig ${m.status === "running" || m.status === "queued" ? "sel act-run" : ""}" data-status="${offEsc(m.status || "idle")}"
      data-hair="${offHair(m)}" style="--c:${colorOf(m)};--skin:${offSkin(m)}" title="${offEsc(m.name)} · ${OFFICE_CN[m.status] || m.status}">
      <span class="tag"><i class="tag-dot" style="display:inline-block;width:5px;height:5px;border-radius:50%;background:${m.status==='running'?'var(--accent,#4ade80)':m.status==='queued'?'#eab308':'var(--muted)'};margin-right:3px;vertical-align:middle"></i>${offEsc(m.name)}</span>
      <span class="in"><i class="h"></i><i class="b"></i><i class="a1"></i><i class="a2"></i><i class="l1"></i><i class="l2"></i></span></div>`).join("");
  const desks = members.map((m) => `<div class="tof-desk" title="${offEsc(m.name)} 的工位">
      <span class="mon"></span><span class="kb"></span><span class="cup"></span><span class="pp"></span>
    </div>`).join("");
  const ev = (p.events || [])[0];
  let actHtml = "这间还没派过活";
  if (p.active_task) {
    const actM = p.active_task.member_name || "成员";
    const actT = p.active_task.title || "任务";
    const actSt = p.active_task.status === "running" ? "在跑" : "排队";
    actHtml = `⚡ <b>「${offEsc(actM)}」</b>${actSt}「${offEsc(actT)}」`;
  } else if (ev) {
    actHtml = offEsc(offAgo(ev.at)) + " · " + offEsc(ev.text || "");
  }
  return `<div class="tof-room ${p.id === officeSel ? "sel" : ""}" data-office="${offEsc(p.id)}">
    <div class="nm">${offEsc(p.name)}</div>
    <div class="kind">团队 · ${allMembers.length} 人</div>
    <div class="st ${running ? "run" : ""}"><span class="dot"></span>${running ? "在跑" : "待命"}</div>
    <div class="tof-floor">${desks}${figs}</div>
    <div class="act"><span>${actHtml}</span></div>
  </div>`;
}

function officePanel(p, colorOf) {
  if (!p) return `<div class="tof-pd"><p class="hint">还没有选中的团队。</p></div>`;
  const lead = p.leader, ms = p.members || [];
  const row = (m, isLead) => {
    const c = colorOf(m);
    const st = m.status || "idle";
    const isSteward = isLead && (m.name === "智研管家" || m.assistant_id === "builtin-steward");
    const av = isSteward
      ? `<div class="av av-lead" title="智研管家 · 自动统筹"><img src="/mark.png" alt="智研"></div>`
      : `<div class="av" style="${offTint(c)}" title="${offEsc(m.name || "")}">${offEsc(m.avatar || (m.name || "?").slice(0, 1))}</div>`;
    return `<div class="tof-mrow">
      ${av}
      <div class="mc"><b>${offEsc(m.name)}<u>${offEsc((m.assistant_id || m.id || "").slice(-4))}</u></b>
        <i>${offEsc(m.prompt || (m.model ? "模型 " + m.model : "跟随当前模型"))}</i></div>
      <span class="pill ${st === "running" || st === "queued" ? "run" : ""}">${isLead?"负责人 · ":""}${OFFICE_CN[st]||st}</span>
    </div>`;
  };
  return `<div class="tof-pd">
    <div class="k"><span>人员结构</span><span>${ms.length} 位成员${lead ? " + 1 位负责人" : ""}</span></div>
    <h2>${offEsc(p.name)}</h2>
    <div class="sub">${p.purpose ? offEsc(p.purpose) : "负责人拆解任务，专家并行研究并交付"}</div>
    <div class="gh"><span>统 筹</span><span>状态</span></div>
    ${lead ? row(lead, true) : `<p class="hint" style="padding:8px 0">这间还没有指定 Leader（可在「修改编制」中指定任意成员为 Leader）。</p>`}
    <div class="gh"><span>成 员</span><span>颜色即身份</span></div>
    ${ms.length ? ms.map((m) => row(m, false)).join("")
      : `<p class="hint" style="padding:8px 0">一个人都没有 —— 点「修改编制」从专家里拉人。</p>`}
    <div class="feed">
      <div class="fk"><span>最 近 流 水</span><span>${(p.events || []).length} 条</span></div>
      ${(p.events || []).length
        ? p.events.map((e) => `<div class="fr"><u>${offEsc(offAgo(e.at))}</u><span>${offEsc(e.text)}</span></div>`).join("")
        : `<div class="fr"><span>这间还没派过活。</span></div>`}
    </div>
    <div class="to"><span>→</span><span>研讨室＝科研台里这个项目正在跑的任务与对话</span></div>
    <div class="ft">
      <button class="btn ghost" id="tofDelete" style="color:var(--err)">删除</button>
      <button class="btn" id="tofEdit">修改编制</button>
      <button class="btn btn-primary" id="tofEnter">进入研讨室</button>
    </div>
  </div>`;
}

async function renderTeamOffice(m) {
  const ticket=++officeRenderSequence;
  cancelAnimationFrame(officeRAF);
  const pid = curTask || ((S.tasks || [])[0] || {}).id || "";
  if(pid&&!curTask){curTask=pid;localStorage.setItem('rw-task',pid);}
  if (!pid) {
    m.classList.add("tof-fill");
    m.innerHTML = `<div class="tof-empty"><b>团队挂在项目上</b>
      <p>先去「项目」建一个项目，团队和办公室都会跟着项目走。</p>
      <button class="btn btn-primary" onclick="openTab('tasks')">去建项目</button></div>`;
    return;
  }
  m.classList.add("tof-fill");
  m.innerHTML='<div class="hint" role="status">正在读取团队…</div>';
  let d;
  try { d = await api("/api/teams?project=" + encodeURIComponent(pid)); }
  catch (e) {
    if(ticket!==officeRenderSequence || route()!=='team' || curTask!==pid || !m.isConnected)return;
    m.innerHTML = `<div class="tof-empty"><b>团队没读出来</b><p>${offEsc(e.message)}</p></div>`;
    return;
  }
  if(ticket!==officeRenderSequence || route()!=='team' || curTask!==pid || !m.isConnected)return;
  const teams = d.teams || [];
  if (!teams.some((t) => t.id === officeSel)) officeSel = (teams[0] || {}).id || "";

  // 颜色：按成员在项目里的先后分配，同一个人换房间也是同一个色
  const order = [];
  teams.forEach((t) => [t.leader, ...(t.members || [])].filter(Boolean).forEach((x) => {
    const key = x.assistant_id || x.name;
    if (!order.includes(key)) order.push(key);
  }));
  const colorOf = (x) => OFFICE_COLORS[Math.max(0, order.indexOf(x.assistant_id || x.name)) % OFFICE_COLORS.length];

  const project = (S.tasks || []).find((t) => t.id === pid) || {};
  const sel = teams.find((t) => t.id === officeSel) || teams[0];

  m.innerHTML = `<div class="tof">
    <header class="tof-head">
      <div><h2>团队</h2><p>${offEsc(project.title || "")} · ${teams.length} 个团队</p></div>
      <div class="tof-hd-r">
        <label class="tof-sel"><span>项目</span>
          <select id="tofProj">${(S.tasks || []).map((t) =>
            `<option value="${offEsc(t.id)}" ${t.id === pid ? "selected" : ""}>${offEsc(t.title)}</option>`).join("")}</select>
        </label>
        <button class="btn btn-primary" id="tofNew">＋ 新建团队</button>
      </div>
    </header>
    <div class="tof-body">
      <div class="tof-stage"><div class="tof-sheet" id="tofSheet">
        ${teams.length
          ? `<div class="tof-grid" data-count="${teams.length}">${teams.map((p) => officeRoom(p, colorOf)).join("")}</div>`
          : `<div class="tof-empty"><b>当前项目还没有团队</b><p>点击右上角「＋ 新建团队」，从专家中挑人组建团队。</p></div>`}
      </div></div>
      <aside class="tof-panel" id="tofPanel">${officePanel(sel, colorOf)}</aside>
    </div>
  </div>`;

  m.querySelectorAll(".tof-room[data-office]").forEach((el) => {
    el.onclick = () => {
      officeSel = el.dataset.office;
      m.querySelectorAll(".tof-room").forEach((x) => x.classList.toggle("sel", x === el));
      const p = teams.find((t) => t.id === officeSel);
      $("tofPanel").innerHTML = officePanel(p, colorOf);
      bindPanel();
    };
  });
  const bindPanel = () => {
    const ed = $("tofEdit"), en = $("tofEnter"), cl = $("tofClose"), del = $("tofDelete");
    if (ed) ed.onclick = () => { teamScope=officeSel;teamSession='';teamList=null;localStorage.setItem('rw-team-chat',teamScope);window.__teamMode = "manage"; render(); };
    if (en) en.onclick = () => officeEnter(pid,officeSel);
    if (cl) cl.onclick = () => { window.__teamMode = ""; render(); };
    if (del) del.onclick = async () => {
      const target = teams.find((t) => t.id === officeSel);
      if (!target) return;
      if (!await uiConfirm(`删除团队「${target.name}」？历史会话仍保留，团队将不再可用。`)) return;
      try {
        await api("/api/teams/delete", { id: target.id });
        if (teamScope === target.id) { teamScope = ""; localStorage.removeItem("rw-team-chat"); }
        officeSel = ""; teamList = null; render();
      } catch (e) { uiNotify(e.message); }
    };
  };
  bindPanel();
  const pj = $("tofProj");
  if (pj) pj.onchange = (e) => {
    curTask = e.target.value || null;
    if (curTask) localStorage.setItem("rw-task", curTask); else localStorage.removeItem("rw-task");
    officeSel = ""; render();
  };
  const nw = $("tofNew");
  if (nw) nw.onclick = () => officeForm(pid);
  officeSit();
  if (officeRO) { officeRO.disconnect(); officeRO = null; }
  const sheet = $("tofSheet");
  if (sheet && typeof ResizeObserver === "function") {
    let lastW = sheet.clientWidth, lastH = sheet.clientHeight;
    officeRO = new ResizeObserver(() => {
      if (!sheet.isConnected) { officeRO?.disconnect(); officeRO = null; return; }
      if (Math.abs(sheet.clientWidth - lastW) > 6 || Math.abs(sheet.clientHeight - lastH) > 6) {
        lastW = sheet.clientWidth; lastH = sheet.clientHeight;
        officeSit();
      }
    });
    officeRO.observe(sheet);
  }
}

function officeEnter(pid,teamId=officeSel) {
  if (pid) { curTask = pid; localStorage.setItem("rw-task", pid); }
  teamScope=teamId;teamList=null;teamData=null;teamSession='';
  localStorage.setItem('rw-team-chat',teamScope);
  window.__teamMode = "chat";
  return openTab('team');
}

/* 新建团队（所有团队统一级别，可随时编辑或删除） */
function officeForm(pid) {
  const pool = (S.assistants || []).filter((a) => a.enabled !== 0 && a.id !== "builtin-steward");
  const box = document.createElement("div");
  box.className = "tof-mask";
  box.innerHTML = `<div class="tof-modal">
    <h3>新建团队</h3>
    <p class="hint">填写团队名称与分工目标，并从专家中挑选成员。所有团队平级管理，支持随时修改或删除。</p>
    <label class="fld"><span>团队名称（可选）</span><input class="control" id="tofName" placeholder="例如：文献精读组 / 论文撰写组"></label>
    <label class="fld"><span>目标 / 职责</span><input class="control" id="tofPurpose" placeholder="例如：第 3 章配图与结果对比"></label>
    <div class="fld"><span>选择成员</span>
      <div class="tof-pool">${pool.map((a) => `<label class="pick"><input type="checkbox" value="${offEsc(a.id)}">
        <b>${offEsc(a.name)}</b><small>${offEsc(a.description || "").slice(0, 26)}</small></label>`).join("")}</div></div>
    <div class="row"><button class="btn" id="tofCancel">取消</button><button class="btn btn-primary" id="tofOk">创建团队</button></div>
  </div>`;
  document.body.appendChild(box);
  const close = () => box.remove();
  box.querySelector("#tofCancel").onclick = close;
  box.onclick = (e) => { if (e.target === box) close(); };
  box.querySelector("#tofOk").onclick = async () => {
    const name = box.querySelector("#tofName").value.trim();
    const purpose = box.querySelector("#tofPurpose").value.trim();
    const ids = [...box.querySelectorAll(".pick input:checked")].map((x) => x.value);
    if (!name && !purpose) return uiNotify("请填写团队名称或目标");
    try {
      const r = await api("/api/teams/new", { kind: "team", name: name || purpose.slice(0, 24), purpose, project: pid });
      for (const id of ids) await api("/api/team/chat/member", { scope: r.team.id, assistant_id: id });
      officeSel = r.team.id;
      teamList = null;
      close(); render();
    } catch (e) { uiNotify(e.message); }
  };
}
