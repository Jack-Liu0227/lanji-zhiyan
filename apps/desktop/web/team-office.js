/* ============================== 团队 · 一层平面 ==============================
   房间 = 团队：常驻办公室（项目在它就在）+ 加急办公室（智研管家为某件事拉人组建）。
   左边是图纸，右边是人员结构；小人由 rAF 驱动在房间里来回走，颜色即身份。 */

const OFFICE_INK = "#7E93A3";
const OFFICE_COLORS = ["#7E93A3", "#93A385", "#B08B84", "#C9A67E", "#9C8FA6", "#7E9C90",
                       "#B98F6A", "#8E8C86", "#A8A06B", "#C0A0A6", "#6F8B8E"];
const OFFICE_CN = { idle: "待命", queued: "排队", running: "在跑", done: "完成", failed: "失败", blocked: "卡住" };
let officeSel = "";
let officeRAF = 0;
let officeWalkers = [];

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
    this.x = home.x; this.y = home.y;
    this.tx = this.x; this.ty = this.y;
    this.dir = Math.random() < .5 ? 1 : -1;
    this.moving = false; this.seated = true; this.atHome = true;
    this.wait = Math.random() * 2.5;
    this.last = -1;
    this.sit();
    this.paint();
  }
  paint() {
    this.el.style.transform = `translate(${this.x}px,${this.y}px)`;
    this.in.style.transform = `scaleX(${this.dir})`;
  }
  spots() {
    const w = Math.max(0, this.host.clientWidth - 13), h = Math.max(0, this.host.clientHeight - 21);
    return [[.10, .78], [.34, .90], [.60, .90], [.84, .78], [.50, .46], [.12, .50], [.74, .52]]
      .map(([a, b]) => ({ x: Math.round(a * w), y: Math.round(b * h) }));
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
    this.tx = pt.x; this.ty = pt.y;
    this.atHome = Math.abs(pt.x - this.home.x) < 2 && Math.abs(pt.y - this.home.y) < 2;
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

/* 摆桌子（固定位置）+ 把每个人放到自己桌前 */
function officeSit() {
  cancelAnimationFrame(officeRAF);
  officeWalkers = [];
  document.querySelectorAll(".tof-sheet .tof-room .tof-floor").forEach((floor) => {
    const desks = [...floor.querySelectorAll(".tof-desk")];
    const figs = [...floor.querySelectorAll(".tof-fig")];
    const W = floor.clientWidth, H = floor.clientHeight;
    const n = desks.length, rows = Math.max(1, Math.ceil(n / 2));
    desks.forEach((d, i) => {
      const col = i % 2, row = Math.floor(i / 2);
      const x = Math.round(W * (n === 1 ? .5 : (col ? .66 : .34)) - 23);
      const y = Math.round(H * (rows === 1 ? .30 : (row ? .60 : .20)) - 11);
      d.style.left = Math.max(2, x) + "px";
      d.style.top = Math.max(2, y) + "px";
    });
    figs.forEach((el, i) => {
      const d = desks[i] || desks[desks.length - 1];
      const home = d
        ? { x: parseFloat(d.style.left) + 23 - 6, y: parseFloat(d.style.top) + 22 - 6 }
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
  const members = [p.leader, ...(p.members || [])].filter(Boolean).slice(0, 8);
  const running = (p.members || []).some((m) => m.status === "running" || m.status === "queued");
  const figs = members.map((m) => `<div class="tof-fig" data-status="${offEsc(m.status || "idle")}" style="--c:${colorOf(m)}">
      <span class="in"><i class="h"></i><i class="b"></i><i class="a1"></i><i class="a2"></i><i class="l1"></i><i class="l2"></i></span></div>`).join("");
  const desks = members.map(() => `<div class="tof-desk"></div>`).join("");
  const ev = (p.events || [])[0];
  return `<div class="tof-room ${p.id === officeSel ? "sel" : ""}" data-office="${offEsc(p.id)}">
    <div class="nm">${offEsc(p.name)}</div>
    <div class="kind ${p.kind === "temp" ? "t" : ""}">${p.kind === "temp" ? "加急" : "常驻"} · ${members.length} 人</div>
    <div class="st ${running ? "run" : ""}"><span class="dot"></span>${running ? "在跑" : "待命"}</div>
    <div class="tof-floor">${desks}${figs}</div>
    <div class="act"><span>${ev ? offEsc(offAgo(ev.at)) + " · " + offEsc(ev.text || "") : "这间还没派过活"}</span></div>
  </div>`;
}

function officePanel(p, colorOf) {
  if (!p) return `<div class="tof-pd"><p class="hint">这层还没有选中的办公室。</p></div>`;
  const lead = p.leader, ms = p.members || [];
  const row = (m, isLead) => {
    const c = colorOf(m);
    const st = m.status || "idle";
    return `<div class="tof-mrow">
      <div class="av" style="${offTint(c)}">${offEsc(m.avatar || (m.name || "?").slice(0, 1))}</div>
      <div class="mc"><b>${offEsc(m.name)}<u>${offEsc((m.assistant_id || m.id || "").slice(-4))}</u></b>
        <i>${offEsc(m.prompt || (m.model ? "模型 " + m.model : "跟随当前模型"))}</i></div>
      <span class="pill ${st === "running" || st === "queued" ? "run" : ""}">${isLead ? "Leader" : (OFFICE_CN[st] || st)}</span>
    </div>`;
  };
  return `<div class="tof-pd">
    <div class="k"><span>人员结构</span><span>${ms.length} / ${Math.max(ms.length, 4)} 在编</span></div>
    <h2>${offEsc(p.name)}</h2>
    <div class="sub">${p.kind === "temp"
      ? "加急 · 智研管家为「" + offEsc(p.purpose || "某件事") + "」组建，交完归还位子"
      : "常驻 · 不属于哪件事，项目在它就在"}</div>
    <div class="gh"><span>统 筹</span><span>状态</span></div>
    ${lead ? row(lead, true) : `<p class="hint" style="padding:8px 0">这间还没有 Leader（智研管家会在下次派活时补上）。</p>`}
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
      <button class="btn" id="tofEdit">修改编制</button>
      <button class="btn btn-primary" id="tofEnter">进入研讨室</button>
    </div>
  </div>`;
}

async function renderTeamOffice(m) {
  cancelAnimationFrame(officeRAF);
  const pid = curTask || ((S.tasks || [])[0] || {}).id || "";
  if (!pid) {
    m.classList.add("tof-fill");
    m.innerHTML = `<div class="tof-empty"><b>团队挂在项目上</b>
      <p>先去「项目」建一个项目，团队和办公室都会跟着项目走。</p>
      <button class="btn btn-primary" onclick="openTab('tasks')">去建项目</button></div>`;
    return;
  }
  m.classList.add("tof-fill");
  let d;
  try { d = await api("/api/teams?project=" + encodeURIComponent(pid)); }
  catch (e) {
    m.innerHTML = `<div class="tof-empty"><b>团队没读出来</b><p>${offEsc(e.message)}</p></div>`;
    return;
  }
  const teams = d.teams || [];
  if (!teams.some((t) => t.id === officeSel)) officeSel = (teams[0] || {}).id || "";

  // 颜色：按成员在项目里的先后分配，同一个人换房间也是同一个色
  const order = [];
  teams.forEach((t) => [t.leader, ...(t.members || [])].filter(Boolean).forEach((x) => {
    const key = x.assistant_id || x.name;
    if (!order.includes(key)) order.push(key);
  }));
  const colorOf = (x) => OFFICE_COLORS[Math.max(0, order.indexOf(x.assistant_id || x.name)) % OFFICE_COLORS.length];

  const fixed = teams.filter((t) => t.kind !== "temp");
  const temp = teams.filter((t) => t.kind === "temp");
  const project = (S.tasks || []).find((t) => t.id === pid) || {};
  const sel = teams.find((t) => t.id === officeSel) || teams[0];
  const vacancies = Math.max(0, 3 - temp.length);

  m.innerHTML = `<div class="tof">
    <header class="tof-head">
      <div><h2>团队</h2><p>${offEsc(project.title || "")} · 常驻 ${fixed.length} 间 / 加急 ${temp.length} 间</p></div>
      <div class="tof-hd-r">
        <label class="tof-sel"><span>项目</span>
          <select id="tofProj">${(S.tasks || []).map((t) =>
            `<option value="${offEsc(t.id)}" ${t.id === pid ? "selected" : ""}>${offEsc(t.title)}</option>`).join("")}</select>
        </label>
        <button class="btn btn-primary" id="tofNew">＋ 组队</button>
      </div>
    </header>
    <div class="tof-body">
      <div class="tof-stage"><div class="tof-sheet" id="tofSheet">
        <div class="tof-row top" style="grid-template-columns:repeat(${Math.max(1, fixed.length)},1fr)">${fixed.map((p) => officeRoom(p, colorOf)).join("")}</div>
        <div class="tof-corr">
          <span>走 廊</span><i class="cl"></i>
          <div class="tof-fig butler" data-status="running" style="--c:var(--accent)">
            <span class="in"><i class="h"></i><i class="b"></i><i class="a1"></i><i class="a2"></i><i class="l1"></i><i class="l2"></i></span></div>
        </div>
        <div class="tof-row bottom">${temp.map((p) => officeRoom(p, colorOf)).join("")}
          ${Array.from({ length: vacancies }).map(() => `<div class="tof-room vacant"><span>空 位</span></div>`).join("")}
        </div>
      </div></div>
      <aside class="tof-panel" id="tofPanel">${officePanel(sel, colorOf)}</aside>
    </div>
  </div>`;

  // 交互
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
    const ed = $("tofEdit"), en = $("tofEnter"), cl = $("tofClose");
    if (ed) ed.onclick = () => { window.__teamMode = "manage"; render(); };
    if (en) en.onclick = () => officeEnter(pid);
    if (cl) cl.onclick = () => { window.__teamMode = ""; render(); };
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
}

function officeEnter(pid) {
  if (pid) { curTask = pid; localStorage.setItem("rw-task", pid); }
  window.__teamMode = "";
  openTab("workflow");       // 科研台：这个项目正在跑的任务/对话
}

/* 手动组队（管家也能用 team_form 工具自己组） */
function officeForm(pid) {
  const pool = (S.assistants || []).filter((a) => a.enabled !== 0);
  const box = document.createElement("div");
  box.className = "tof-mask";
  box.innerHTML = `<div class="tof-modal">
    <h3>组一支加急队</h3>
    <p class="hint">说清为什么组、要交什么；人从专家里挑。用完在「修改编制」里解散，位子还回来。</p>
    <label class="fld"><span>为什么组</span><input class="control" id="tofPurpose" placeholder="例如：第 3 章配图，交三张对比图"></label>
    <div class="fld"><span>拉谁进来</span>
      <div class="tof-pool">${pool.map((a) => `<label class="pick"><input type="checkbox" value="${offEsc(a.id)}">
        <b>${offEsc(a.name)}</b><small>${offEsc(a.description || "").slice(0, 26)}</small></label>`).join("")}</div></div>
    <div class="row"><button class="btn" id="tofCancel">取消</button><button class="btn btn-primary" id="tofOk">组队</button></div>
  </div>`;
  document.body.appendChild(box);
  const close = () => box.remove();
  box.querySelector("#tofCancel").onclick = close;
  box.onclick = (e) => { if (e.target === box) close(); };
  box.querySelector("#tofOk").onclick = async () => {
    const purpose = box.querySelector("#tofPurpose").value.trim();
    const ids = [...box.querySelectorAll(".pick input:checked")].map((x) => x.value);
    if (!purpose) return alert("先说清为什么组");
    if (!ids.length) return alert("一个人都没挑");
    try {
      const r = await api("/api/teams/new", { kind: "temp", name: purpose.slice(0, 24), purpose, project: pid });
      for (const id of ids) await api("/api/team/chat/member", { scope: r.team.id, assistant_id: id });
      officeSel = r.team.id;
      close(); render();
    } catch (e) { alert(e.message); }
  };
}
