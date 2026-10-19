// 知识星图场景：星核 + 五簇星座 + 三层聚焦（hub / 簇 / 条目）。
// 结构照参照实现：位置全部由 targets() 按窗口尺寸算出来，每帧用临界阻尼弹簧追目标值，
// 所以「切换层」是滑过去的，不是重画。整张图只建一次 DOM，逐帧只改变换属性与 SVG 的 d。
import {T} from './theme.js';
import {mountCosmos} from './sky.js';
import {baked, skeleton, hitOf, extentOf, countsOf, nameOf, BASE} from './figs.js';
import {createCore, bandsOf, outerCountOf, PARAMS} from './v41.js';

// 五簇：角度与参照实现一致（左 / 上 / 右 / 右下 / 左上），只把语义换成本项目的模块。
const MODULES = [
  {id: 'works', kind: 'post', title: '论文', en: 'Papers', angle: 180, icon: '▤'},
  {id: 'concepts', kind: 'concept', title: '关键词', en: 'Keywords', angle: -90, icon: '✦'},
  {id: 'methods', kind: 'note', title: '笔记', en: 'Notes', angle: 0, icon: '✎'},
  {id: 'ideas', kind: 'idea', title: '检索', en: 'Search', angle: 35, icon: '⌕'},
  {id: 'drafts', kind: 'draft', title: '工作流', en: 'Workflow', angle: 145, icon: '⚙'}
];
// 后端给的语义 kind → 星座层用的 kind（星座/配色/类名都按这套认）
const KIND_OF = {paper: 'post', note: 'note', concept: 'concept', candidate: 'idea', step: 'draft'};
// 项目层：每个项目占一个星团，外观按顺序轮流借五张已烘焙的星座图（不新画图）
const PROJ_FIG = ['works', 'concepts', 'methods', 'ideas', 'drafts'];
const KINDS = {
  project: {label: '项目', en: 'Project', color: T.line.core},
  post: {label: '论文', en: 'Paper', color: T.kinds.post},
  note: {label: '笔记', en: 'Note', color: T.kinds.note},
  concept: {label: '关键词', en: 'Keyword', color: T.kinds.concept},
  idea: {label: '检索', en: 'Search', color: T.kinds.idea},
  draft: {label: '工作流', en: 'Step', color: T.kinds.draft}
};

const DPR = Math.min(window.devicePixelRatio || 1, 2);
const HUB_K = 1, SUB_K = .82;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = v => v == null ? '—' : Number(v).toLocaleString('zh-CN');

// —— 数据整理：度 / 双向 / 分区 ——
function adapt(api) {
  const nodes = (api.nodes || []).map(n => ({...n, kind: KIND_OF[n.kind] || n.kind, in: 0, out: 0, mutual: 0}));
  const index = new Map(nodes.map(n => [n.id, n]));
  const links = [];
  for (const e of api.edges || []) {
    const a = index.get(e.source), b = index.get(e.target);
    if (!a || !b) continue;
    a.out++; b.in++;
    links.push({a: e.source, b: e.target, kind: e.kind});
  }
  // 双向 = 两个方向都有边（引用互相点到的、笔记与文献互相挂的）
  const seen = new Set(links.map(l => l.a + '>' + l.b));
  for (const l of links) {
    if (seen.has(l.b + '>' + l.a)) { index.get(l.a).mutual++; index.get(l.b).mutual++; }
  }
  return {
    nodes, index, links, task: api.task, tasks: api.tasks || [],
    stats: {content: nodes.length, links: links.length,
      mutual: nodes.reduce((s, n) => s + n.mutual, 0) / 2, concepts: nodes.filter(n => n.kind === 'concept').length}
  };
}

const split = n => ({out: Math.max(0, n.out - n.mutual), back: Math.max(0, n.in - n.mutual), both: n.mutual});
const moduleItems = (graph, modId) => {
  const m = MODULES.find(x => x.id === modId);
  if (!m) return [];
  const list = graph.nodes.filter(n => n.kind === m.kind);
  if (m.kind === 'concept') list.sort((a, b) => b.in - a.in);
  return list;
};
function itemMeta(n) {
  const d = split(n), meta = n.meta || [];
  const head = meta.slice(0, 2).map(([k, v]) => [k, '', v]);
  return [...head, ['↔', '', d.both]];
}
function moduleSummary(graph, m) {
  const items = moduleItems(graph, m.id);
  const sub = {
    works: () => [items.length, '篇', 'papers'],
    concepts: () => [fmt(items.reduce((s, n) => s + n.in, 0)), '次提及', 'mentions'],
    methods: () => [graph.links.filter(l => l.kind === 'reads').length, '条笔记挂了论文', 'linked'],
    ideas: () => [items.length, '条待定', 'candidates'],
    drafts: () => [items.length, '步', 'steps']
  }[m.id]();
  return {count: items.length, sub};
}

// —— 布局目标 ——
// 底部 KPI 面板占掉 ~126px（dock 高 ~92 + 底边 22 + 余量）：扇面只在剩余高度里排，
// 否则最后几条会钻到面板底下——看得见、点不到。
const fanSpan = h => Math.max(120, Math.min(Math.round(h * .94 - 150), h - 226));
const rowsThatFit = h => Math.max(2, Math.floor(fanSpan(h) / 26) + 1);
function slotOf(selId, id) {
  const others = MODULES.filter(m => m.id !== selId).map(m => m.id);
  const order = [others[0], others[1], selId, others[2], others[3]];
  const k = order.indexOf(id);
  return k < 0 ? MODULES.findIndex(m => m.id === id) : k;
}
function targets(L, t) {
  const {w: W, h: H} = L.size, st = L.stage, narrow = W < 760;
  const swim = L.reduced ? 0 : 1;
  const res = {core: {}, cluster: {}, fan: null};
  if (st === 'projects') {
    // 项目层：星核 = 全部项目，N 个星团均分在环上（只有 1 个就放右侧）
    const n = Math.max(1, L.subs.length);
    const cy = narrow ? H * .47 : H * .53;
    res.core = {x: W / 2, y: cy, s: narrow ? clamp(W * .40, 150, 210) : clamp(Math.min(W, H) * .30, 200, 300)};
    const rx = narrow ? W * .34 : Math.min(W * .30, 500), ry = narrow ? H * .22 : Math.min(H * .30, 268);
    L.subs.forEach((m, i) => {
      const a = ((n === 1 ? 0 : -90 + i * 360 / n) + swim * 5 * Math.sin(t * .17 + i * 1.7)) * Math.PI / 180;
      const r = 1 + swim * .04 * Math.sin(t * .23 + i);
      res.cluster[m.id] = {
        x: W / 2 + Math.cos(a) * rx * r, y: cy + Math.sin(a) * ry * r + swim * 8 * Math.sin(t * .9 + i * 2.1),
        s: narrow ? clamp(W * .40, 128, 176) : clamp(Math.min(W, H) * .20, 150, 210), o: 1
      };
    });
  } else if (st === 'hub') {
    const s = narrow ? clamp(W * .56, 180, 280) : clamp(Math.min(W, H) * .5, 250, 440);
    const cy = narrow ? H * .47 : H * .53;
    res.core = {x: W / 2, y: cy, s};
    const rx = narrow ? W * .38 : Math.min(W * .345, 566), ry = narrow ? H * .23 : Math.min(H * .365, 300);
    L.subs.forEach((m, i) => {
      const a = (m.angle + swim * 7 * Math.sin(t * .17 + i * 1.7)) * Math.PI / 180;
      const r = 1 + swim * .04 * Math.sin(t * .23 + i);
      res.cluster[m.id] = {x: W / 2 + Math.cos(a) * rx * r, y: cy + Math.sin(a) * ry * r + swim * 9 * Math.sin(t * .9 + i * 2.1), s: narrow ? 184 : clamp(W * .205, 206, 288), o: 1};
    });
  } else if (narrow) {
    res.core = {x: 40, y: 156, s: 86};
    L.subs.forEach((m, i) => {
      const sel = m.id === L.mod, k = slotOf(L.mod, m.id);
      res.cluster[m.id] = {x: 104 + k * (W - 136) / 4, y: 152 + swim * 4 * Math.sin(t * .9 + i * 2), s: sel ? 168 : 120, o: sel ? 1 : .55};
    });
    const n = L.fanRows || L.items.length, gap = clamp((H - 330) / Math.max(n - 1, 1), 22, 54);
    res.fan = {x: 34, y0: 286, gap};
  } else {
    const ox = Math.max(124, W * .09), cy = H * .53;
    res.core = {x: ox, y: cy, s: 178};
    const colX = ox + 158, cardH = 119, topY = 150, botY = H - 73;
    const gap = clamp((Math.min(cy - topY, botY - cy) - cardH / 2) / 2, 122, 200);
    L.subs.forEach((m, i) => {
      const sel = m.id === L.mod, k = slotOf(L.mod, m.id);
      res.cluster[m.id] = {x: colX + (sel ? 16 : 0), y: cy + (k - 2) * gap + swim * 5 * Math.sin(t * .9 + i * 2), s: sel ? 300 : 220, o: sel ? 1 : .6};
    });
    const panelLeft = W - clamp(W * .33, 380, 470) - 44;
    const n = L.fanRows || L.items.length;
    const span = Math.min(fanSpan(H), Math.max(0, n - 1) * 54);
    const midY = (96 + Math.max(96, H - 126)) / 2;
    const originX = colX + 16 + 300 * .5 + 10 + 168;
    res.fan = {x: Math.min(Math.max(panelLeft - 270 - 90, originX + 60), panelLeft - 280), y0: midY - span / 2, gap: n > 1 ? span / (n - 1) : 0};
  }
  return res;
}

function spring(st, target, dt, k = 62) {
  const c = 2 * Math.sqrt(k);
  st.v += (-k * (st.x - target) - c * st.v) * dt;
  st.x += st.v * dt;
  return st.x;
}
const sp = x => ({x, v: 0});
const bez = (p0, p1, p2, p3, u) => { const m = 1 - u; return m * m * m * p0 + 3 * m * m * u * p1 + 3 * m * u * u * p2 + u * u * u * p3; };
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

// 星座图形在装载时烘焙一次（五张离屏画布）。
const figData = Object.fromEntries(MODULES.map(m => {
  const bake = baked(m.id, DPR);
  return [m.id, {bake, sk: skeleton(m.id), hit: hitOf(m.id), ext: extentOf(m.id), n: countsOf(m.id)}];
}));

function drawCluster(canvas, data, t, on, reduced, hot) {
  if (!canvas || !data) return;
  const B = data.bake.box, ctx = canvas.getContext('2d');
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, B, B);
  ctx.drawImage(data.bake.canvas, 0, 0, B, B);
  if (on) {
    ctx.save(); ctx.globalCompositeOperation = 'lighter'; ctx.globalAlpha = .26;
    ctx.drawImage(data.bake.canvas, 0, 0, B, B);
    ctx.restore();
  }
  ctx.save();
  ctx.translate(B / 2, B / 2);
  ctx.globalCompositeOperation = 'lighter';
  for (const s of data.sk) {
    const x = s.x, y = s.y;
    const tw = reduced ? 1 : .60 + .40 * Math.sin(t * (.42 + s.idx * .11) + s.idx * 1.7) * Math.sin(t * (.19 + s.idx * .043) + s.idx * 2.3);
    ctx.globalAlpha = Math.max(.22, tw) * (.30 + s.m * .70) * (on ? 1 : .88);
    ctx.fillStyle = 'rgb(255,252,244)';
    ctx.beginPath(); ctx.arc(x, y, 1.15 + s.m * 1.5, 0, 6.284); ctx.fill();
    if (hot === s.idx) {
      const r0 = 9 + s.m * 16;
      const g = ctx.createRadialGradient(x, y, 0, x, y, r0);
      g.addColorStop(0, 'rgba(255,250,238,.85)'); g.addColorStop(1, 'rgba(255,250,238,0)');
      ctx.globalAlpha = .85; ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x, y, r0, 0, 6.284); ctx.fill();
      ctx.globalAlpha = .9; ctx.strokeStyle = 'rgba(232,200,132,.8)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, 10 + s.m * 4, 0, 6.284); ctx.stroke();
    }
  }
  ctx.restore();
}

// —— 星核（v4.1：核体 + 木星式环系，环带宽度 = 各模块节点数） ——
function mountCore(host, graph, reduced, getTime) {
  const cv = el('canvas', 'cs-v41');
  host.appendChild(cv);
  const ctx = cv.getContext('2d');
  if (!ctx) return () => cv.remove();
  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  const W = 920, H = 700;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  // 环带读的是「各簇节点数」：把语义 kind 折回参照实现的四条轴
  const bandGraph = {nodes: graph.nodes.map(n => ({kind: n.kind}))};
  const bands = bandsOf(bandGraph), outerN = outerCountOf(bandGraph);
  let scene;
  try { scene = createCore({w: W, h: H, dpr, params: {}, bands, outerN}); } catch (e) { console.error('星核初始化失败', e); return () => cv.remove(); }
  host.classList.add('v41-live');
  if (reduced) { scene.render(ctx, 0); return () => cv.remove(); }
  let raf = 0, down = 0, acc = 0, count = 0, last = performance.now();
  const step = () => {
    const now = performance.now();
    const dt = Math.max(0, Math.min(1 / 20, (now - last) / 1000)); last = now;
    if (!document.hidden) {
      try { scene.render(ctx, getTime()); } catch (e) { if (!cv.__w) { cv.__w = 1; console.error(e); } }
      acc += dt; count++;
      if (count >= 90) {                       // 连续 90 帧超预算就降颗粒档（最多两档），保证稳 60
        const avg = acc / count; acc = 0; count = 0;
        if (avg > 1 / 62 && down < 2) {
          down++;
          const grains = Math.round(PARAMS.grains * (down === 1 ? .55 : .34));
          try { scene = createCore({w: W, h: H, dpr, params: {grains}, bands, outerN}); } catch (e) { console.error('星核降档失败', e); }
        }
      }
    }
    raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
  return () => { cancelAnimationFrame(raf); cv.remove(); };
}

export function mount(root, api, opts = {}) {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const stageEl = el('section', 'neural kb-star home-stage scene stage-projects');
  stageEl.setAttribute('aria-label', '知识星图');
  root.appendChild(stageEl);
  stageEl.appendChild(el('div', 'stage-bg'));
  const unmountCosmos = mountCosmos(stageEl, reduced);

  // 星核 DOM（CSS 层的 halo/corona/nucleus 都在，v41 画上去后由 .v41-live 收起）
  const coreBox = el('div', 'core-star');
  coreBox.innerHTML = '<div class="cs-halo"></div><div class="cs-flare"></div><div class="cs-corona"></div>'
    + '<div class="cs-rays"></div><div class="cs-nucleus"></div>';
  stageEl.appendChild(coreBox);

  // 连线层（SVG）：星核→星团 与 星团→条目 的贝塞尔，逐帧只改 d
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('class', 'threads');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = `<defs><filter id="thread-blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3.2"/></filter>
    <radialGradient id="pulse-out"><stop offset="0" stop-color="${T.pulse.out[0]}"/><stop offset=".4" stop-color="${T.pulse.out[1]}"/><stop offset="1" stop-color="${T.pulse.out[1]}" stop-opacity="0"/></radialGradient>
    <radialGradient id="pulse-in"><stop offset="0" stop-color="${T.pulse.in[0]}"/><stop offset=".4" stop-color="${T.pulse.in[1]}"/><stop offset="1" stop-color="${T.pulse.in[1]}" stop-opacity="0"/></radialGradient></defs>`;
  stageEl.appendChild(svg);

  // 状态：level = projects（知识库 → 项目）/ branches（项目 → 五簇分支）；stage 只在 branches 层有 hub/module/note 三档
  const now0 = performance.now();
  const L = {
    introSeen: false, t0: now0, time: 0,
    level: 'projects', stage: 'projects', mod: null, sel: null, subs: [],
    all: [], items: [], from: 0, fanRows: 0, prevMod: null,
    size: {w: stageEl.clientWidth || 1280, h: stageEl.clientHeight || 760},
    reduced, paused: false, hover: null, hoverStar: null, itemState: [], moreState: null, fanStart: now0,
    core: {x: 640, y: 380, s: 400},
    springs: {dust: sp(.3), core: {x: sp(640), y: sp(380), s: sp(120), o: sp(0)}, cluster: {}}
  };
  try { L.introSeen = reduced || !!sessionStorage.getItem('kb-star-intro'); } catch { L.introSeen = reduced; }
  if (L.introSeen) L.t0 = performance.now() - 6000;

  // 层数据：概览常驻内存，项目详情进过一次就缓存 —— 退回项目层不再请求
  const cache = {projects: [], totals: {}, byId: {}};
  let graph = adapt({nodes: [], edges: [], task: null, tasks: []});
  const refs = {box: {}, lab: {}, cvs: {}, th: {}, pulse: {}};
  const svgEl = tag => document.createElementNS(svgNS, tag);

  function clearSubjects() {
    for (const id in refs.box) {
      refs.box[id].remove(); refs.lab[id].remove(); refs.th[id].g.remove();
    }
    refs.box = {}; refs.lab = {}; refs.cvs = {}; refs.th = {}; refs.pulse = {};
  }

  function addSubject(m) {
    const art = figData[m.fig];
    const g = svgEl('g');
    g.setAttribute('style', `color:${T.link.near}`);
    const glow = svgEl('path'); glow.setAttribute('class', 'thread-glow'); glow.setAttribute('filter', 'url(#thread-blur)');
    const core = svgEl('path'); core.setAttribute('class', 'thread-core');
    g.appendChild(glow); g.appendChild(core);
    const pulses = [0, 1, 2].map(k => {
      const c = svgEl('circle');
      c.setAttribute('r', k === 2 ? 4.5 : 5.5);
      c.setAttribute('fill', `url(#${k === 2 ? 'pulse-in' : 'pulse-out'})`);
      g.appendChild(c); return c;
    });
    svg.appendChild(g);
    refs.th[m.id] = {glow, core, g}; refs.pulse[m.id] = pulses;

    const box = el('div', 'cluster');
    box.style.width = art.bake.box + 'px'; box.style.height = art.bake.box + 'px';
    const cvs = el('canvas', 'cl-canvas');
    cvs.width = Math.round(art.bake.box * DPR); cvs.height = Math.round(art.bake.box * DPR);
    box.appendChild(cvs);
    const hit = el('button', 'cl-hit');
    hit.style.left = (art.bake.box / 2 + art.hit.cx - art.hit.w / 2) + 'px';
    hit.style.top = (art.bake.box / 2 + art.hit.cy - art.hit.h / 2) + 'px';
    hit.style.width = art.hit.w + 'px'; hit.style.height = art.hit.h + 'px';
    hit.style.clipPath = `polygon(${art.hit.clip})`;
    hit.setAttribute('aria-label', m.title);
    box.appendChild(hit);
    stageEl.appendChild(box);
    refs.box[m.id] = box; refs.cvs[m.id] = cvs;

    const lab = el('button', 'cluster-label below');
    lab.style.setProperty('--mod', KINDS[m.kind].color);
    lab.title = m.title;
    let count, sub;
    if (m.kind === 'project') {                 // 项目层：标签给规模，分支要等点进去才出现
      const p = m.data || {};
      count = p.nodes || 0;
      sub = [p.papers || 0, `篇文献 · ${p.notes || 0} 笔记`, 'project'];
    } else {
      const sm = moduleSummary(graph, m);
      count = sm.count; sub = sm.sub;
    }
    lab.innerHTML = `<span class="jl-icon">${m.icon}</span><span class="jl-count"><b>${count}</b></span>`
      + `<span class="jl-const"><b>${esc(m.title)}</b><em>${m.en.toUpperCase()}</em></span>`
      + `<span class="jl-sub">${sub[0]} ${sub[1]} <em>${sub[2]}</em></span>`;
    stageEl.appendChild(lab);
    refs.lab[m.id] = lab;

    const pick = () => m.kind === 'project' ? openProject(m.id) : setFocus({mod: m.id, sel: null});
    const enter = () => { L.hover = m.id; };
    const leave = () => { L.hover = null; L.hoverStar = null; };
    hit.addEventListener('click', pick);
    hit.addEventListener('pointerenter', enter);
    hit.addEventListener('pointerleave', leave);
    // 命中按骨架主星就近：指针落在簇的命中区里，就点亮 24 设计像素内最近的那颗主星
    hit.addEventListener('pointermove', e => {
      const r = box.getBoundingClientRect();
      if (!r.width) return;
      const k = r.width / art.bake.box;
      const px = (e.clientX - r.left) / k, py = (e.clientY - r.top) / k;
      let bi = -1, bd = 24 * 24;
      art.sk.forEach((s, i) => {
        const d = (s.x - px) ** 2 + (s.y - py) ** 2;
        if (d < bd) { bd = d; bi = i; }
      });
      L.hoverStar = bi < 0 ? null : {id: m.id, idx: bi};
    });
    lab.addEventListener('click', pick);
    lab.addEventListener('pointerenter', enter);
    lab.addEventListener('pointerleave', leave);
  }

  function rebuildSubjects() {
    clearSubjects();
    L.springs.cluster = {};
    L.subs.forEach(m => {
      L.springs.cluster[m.id] = {x: sp(L.core.x), y: sp(L.core.y), s: sp(20), o: sp(0), lo: sp(0)};
      addSubject(m);
    });
  }

  // 星核环带：项目层铺「全部项目的四类内容数」，分支层用当前项目
  const ovGraph = t => {
    const box = (k, n) => Array.from({length: Math.min(Math.max(0, n | 0), 300)}, () => ({kind: k}));
    return {nodes: [...box('post', t.papers), ...box('note', t.notes), ...box('idea', t.candidates), ...box('draft', t.steps)]};
  };
  let disposeCore = () => {};
  function remountCore() {
    try { disposeCore(); } catch { /* 旧星核已卸载 */ }
    disposeCore = mountCore(coreBox, L.level === 'projects' ? ovGraph(cache.totals) : graph, reduced, () => L.time);
    coreBox.classList.toggle('paused', L.paused);
  }

  // 底部 KPI 条（参照实现的 dock 只留读数，不要问答框）
  const dock = el('div', 'dock');
  stageEl.appendChild(dock);
  function renderDock() {
    const top = L.level === 'projects';
    const t = cache.totals || {};
    const kpis = top
      ? [['项目', 'projects', L.subs.length], ['文献', 'papers', t.papers || 0],
         ['笔记', 'notes', t.notes || 0], ['检索', 'candidates', t.candidates || 0]]
      : [['内容节点', 'nodes', graph.stats.content], ['连接', 'links', graph.stats.links],
         ['双向', 'mutual', graph.stats.mutual], ['关键词', 'keywords', graph.stats.concepts]];
    const me = top ? '全部项目' : ((graph.task && graph.task.title) || '');
    dock.innerHTML = `<div class="dock-panel glass"><div class="dock-kpis">${kpis.map(([k, en, v]) =>
      `<div><span>${k} <em>${en}</em></span><strong class="mono">${fmt(v)}</strong></div>`).join('')}</div>`
      + `<div class="dock-chips"><span class="me">${esc(me)} <em>${top ? 'library' : 'project'}</em></span>`
      + `<button data-act="replay">重播开场</button><button data-act="pause">${L.paused ? '继续' : '暂停'}</button></div></div>`;
  }

  // 面包屑
  const crumbs = el('nav', 'crumbs');
  crumbs.setAttribute('aria-label', '图谱路径');
  stageEl.appendChild(crumbs);

  // 右栏分析
  const aside = el('aside', 'analysis-wrap');
  stageEl.appendChild(aside);

  const itemsWrap = el('div');
  stageEl.appendChild(itemsWrap);
  let itemEls = [], fanPathEls = [], fanPulseEls = [], moreEl = null, fanGroup = null;

  function clearFan() {
    itemsWrap.innerHTML = '';
    itemEls = []; fanPathEls = []; fanPulseEls = []; moreEl = null;
    if (fanGroup) { fanGroup.remove(); fanGroup = null; }
  }

  // 第一级：知识库 → 项目
  function showProjects() {
    L.level = 'projects'; L.stage = 'projects'; L.mod = null; L.sel = null;
    L.subs = (cache.projects || []).map((p, i, arr) => ({
      id: p.id, kind: 'project', title: p.title || '未命名项目', en: 'Project', icon: '◈',
      fig: PROJ_FIG[i % PROJ_FIG.length], angle: arr.length === 1 ? 0 : -90 + i * 360 / arr.length, data: p
    }));
    L.all = []; L.items = []; L.fanRows = 0; L.from = 0;
    clearFan();
    rebuildSubjects();
    remountCore();
    stageEl.className = `neural kb-star home-stage scene stage-projects${L.introSeen ? ' intro-done' : ''}`;
    renderDock(); renderCrumbs(); renderAnalysis();
  }

  // 第二级：项目内部（五簇分支）
  function applyProject(a, focus) {
    graph = adapt(a);
    L.level = 'branches'; L.mod = null; L.sel = null;
    L.all = []; L.items = []; L.fanRows = 0; L.from = 0; L.prevMod = null;
    L.subs = MODULES.map(m => ({...m, fig: m.id}));
    clearFan();
    rebuildSubjects();
    remountCore();
    if (!L.introSeen) L.t0 = performance.now();     // 首次从项目层进来，播一次开场
    setFocus({mod: null, sel: null});
    renderDock();
    if (focus) {
      if (MODULES.some(m => m.id === focus)) setFocus({mod: focus, sel: null});
      else {
        const n = (graph.nodes || []).find(x => x.id === focus);
        if (n && n.m) setFocus({mod: n.m, sel: n.id});
      }
    }
  }

  function openProject(id) {
    const a = cache.byId[id];
    if (a && (a.nodes || []).length > 0) {
      applyProject(a);
      fetch('/api/starmap?task=' + encodeURIComponent(id)).then(r => r.json()).then(fresh => {
        if (fresh && fresh.task) {
          cache.byId[id] = fresh;
          if (L.level === 'branches') applyProject(fresh);
        }
      }).catch(() => {});
      return;
    }
    fetch('/api/starmap?task=' + encodeURIComponent(id)).then(r => r.json()).then(a => {
      if (!a || !a.task) throw new Error('这个项目还没有可画的内容');
      cache.byId[id] = a;
      applyProject(a);
    }).catch(e => alert('打开项目失败：' + e.message));
  }

  function setFocus(f) {
    L.mod = f.mod; L.sel = f.sel;
    L.stage = f.sel ? 'note' : f.mod ? 'module' : 'hub';
    const all = moduleItems(graph, L.mod);
    L.all = all;
    L.rows = Math.max(2, rowsThatFit(Math.max(L.size.h, 560)) - 1);   // 留一格给提示行
    if (f.mod !== L.prevMod) L.from = 0;                         // 换簇回到第一页，同一簇里保留滚动位置
    L.prevMod = f.mod;
    L.from = clamp(L.from || 0, 0, Math.max(0, all.length - L.rows));
    const shown = all.slice(L.from, L.from + L.rows);
    L.items = L.stage === 'hub' ? [] : shown;
    L.more = L.stage === 'hub' ? 0 : Math.max(0, all.length - L.from - shown.length);
    L.fanRows = L.stage === 'hub' ? 0 : L.items.length + 1;
    L.itemState = L.items.map(() => null);
    L.moreState = null;
    L.fanStart = performance.now();
    // 扇面 DOM 重建（条目数量变了才重建；每帧只改 transform）
    rebuildFan();
    stageEl.className = `neural kb-star home-stage scene stage-${L.stage}${L.introSeen ? ' intro-done' : ''}`;
    L.subs.forEach(m => {
      refs.box[m.id].classList.toggle('active', m.id === L.mod);
      const lab = refs.lab[m.id];
      lab.classList.toggle('active', m.id === L.mod);
      lab.classList.toggle('below', L.stage === 'hub');
      lab.classList.toggle('side', L.stage !== 'hub');
    });
    renderCrumbs();
    renderAnalysis();
  }

  function rebuildFan() {
    itemsWrap.innerHTML = '';
    itemEls = []; fanPathEls = []; fanPulseEls = []; moreEl = null;
    if (fanGroup) { fanGroup.remove(); fanGroup = null; }
    if (L.stage === 'hub') return;
    fanGroup = document.createElementNS(svgNS, 'g');
    fanGroup.setAttribute('style', `color:${T.link.far}`);
    L.items.forEach((n, i) => {
      const meta = itemMeta(n);
      const b = el('button', `fan-item kind-${n.kind}`);
      b.style.setProperty('--kc', KINDS[n.kind] ? KINDS[n.kind].color : T.kinds.ghost);
      b.setAttribute('aria-label', `打开 ${n.title} 的分析`);
      b.innerHTML = `<span class="fi-dot"></span><span class="fi-body"><span class="fi-title">${esc(n.short || n.title)}</span>`
        + `<span class="fi-meta">${meta.map(([k, en, v]) => `<span class="${k === '↔' ? 'mutual' : ''}">${esc(k)}${en ? `<em>${esc(en)}</em>` : ''} ${v !== '' ? `<b>${esc(v)}</b>` : ''}</span>`).join('')}</span></span>`;
      b.addEventListener('click', () => setFocus({mod: L.mod, sel: n.id}));
      itemsWrap.appendChild(b);
      itemEls.push(b);
      const p = document.createElementNS(svgNS, 'path');
      p.setAttribute('class', 'fan-thread');
      fanGroup.appendChild(p);
      fanPathEls.push(p);
      fanPulseEls.push([0, 1].map(k => {
        const c = document.createElementNS(svgNS, 'circle');
        c.setAttribute('r', k ? 3.5 : 4.5);
        c.setAttribute('fill', `url(#${k ? 'pulse-in' : 'pulse-out'})`);
        fanGroup.appendChild(c); return c;
      }));
    });
    if (L.more > 0 || L.from > 0) {
      const label = L.more > 0
        ? `还有 ${L.more} 条${L.from ? `（上面 ${L.from} 条）` : ''} · <em>${L.more} more · 滚轮翻页</em>`
        : `↑ 上面还有 ${L.from} 条 · <em>滚轮回看</em>`;
      moreEl = el('p', 'fan-more', `<span class="fi-dot"></span>${label}`);
      moreEl.style.setProperty('--kc', T.kinds.ghost);
      itemsWrap.appendChild(moreEl);
    }
    svg.appendChild(fanGroup);
  }

  // 滚轮翻页：条目多到一屏放不下时，滚一格挪 3 条（窗口在整簇列表上滑动）
  function scrollFan(step) {
    if (!L.all || L.stage === 'hub' || L.level !== 'branches') return false;
    const max = Math.max(0, L.all.length - L.rows);
    const next = clamp(L.from + step, 0, max);
    if (next === L.from) return false;
    L.from = next;
    L.items = L.all.slice(L.from, L.from + L.rows);
    L.more = Math.max(0, L.all.length - L.from - L.items.length);
    L.itemState = L.items.map(() => null);
    L.moreState = null;
    L.fanSnap = true;             // 新进窗口的条目直接出现在目标位置
    rebuildFan();
    return true;
  }

  function renderCrumbs() {
    if (L.level === 'projects') { crumbs.innerHTML = ''; crumbs.style.display = 'none'; return; }
    crumbs.style.display = '';
    const m = MODULES.find(x => x.id === L.mod) || {title: '', en: ''};
    const node = L.sel ? graph.index.get(L.sel) : null;
    crumbs.innerHTML = `<button class="crumb-back" aria-label="返回上一层">‹</button>`
      + `<button data-nav="lib">知识库 <em>library</em></button><span>/</span>`
      + `<button data-nav="core">${esc((graph.task && graph.task.title) || '项目')} <em>project</em></button>`
      + (L.mod ? `<span>/</span><button data-nav="mod" style="color:${T.modules[L.mod] ? T.modules[L.mod].color : ''}">${esc(m.title)} <em>${esc(m.en)}</em></button>` : '')
      + (node ? `<span>/</span><b>${esc(node.short || node.title)}</b>` : '');
    crumbs.querySelector('.crumb-back').onclick = () =>
      L.sel ? setFocus({mod: L.mod, sel: null}) : L.mod ? setFocus({mod: null, sel: null}) : showProjects();
    crumbs.querySelectorAll('[data-nav]').forEach(b => b.onclick = () => {
      if (b.dataset.nav === 'lib') showProjects();
      else if (b.dataset.nav === 'core') setFocus({mod: null, sel: null});
      else setFocus({mod: L.mod, sel: null});
    });
  }

  const zoteroCache = new Map();

  function showZtToast(msg) {
    let t = aside.querySelector('.zt-toast');
    if (!t) {
      t = el('div', 'zt-toast');
      aside.appendChild(t);
    }
    t.textContent = msg;
    t.style.display = 'block';
    clearTimeout(t._tm);
    t._tm = setTimeout(() => { if (t) t.style.display = 'none'; }, 2200);
  }

  function renderZoteroPaperAnalysis(n, m, outs, ins, chip, near) {
    const cached = zoteroCache.get(n.id);
    const item = cached || {
      zotero_found: false,
      title: n.title,
      doi: n.doi || '',
      itemTypeLabel: '期刊文章',
      creators: (n.authors ? n.authors.split(' · ').map(a => ({name: a, role: '作者'})) : []),
      date: n.year || '',
      publication: '',
      abstract: '',
      tags: [],
      attachments: (n.filename ? [{title: n.filename, filename: n.filename, path: ''}] : []),
    };

    const hasCached = !!cached;
    const itemType = item.itemTypeLabel || '期刊文章';
    const authorsHtml = (item.creators && item.creators.length)
      ? item.creators.map(c => `
          <div class="zt-author-row">
            <span class="zt-author-name"><b>${esc(c.name)}</b></span>
            <span class="zt-author-role">${esc(c.role || '作者')}</span>
          </div>`).join('')
      : '<span class="quiet">未填</span>';

    const fields = [
      item.citationKey ? ['引用关键词', `<span style="font-family:monospace;color:#93c5fd">${esc(item.citationKey)}</span>`] : null,
      ['条目类型', `<span style="font-weight:500">${esc(itemType)}</span>`],
      ['标题', esc(item.title || n.title)],
      ['作者', `<div class="zt-author-list">${authorsHtml}</div>`],
      item.publication ? ['出版物', `<b>${esc(item.publication)}</b>` + (item.extra && item.extra.includes('IF') ? `<span class="zt-badge-if">IF 7.4</span>` : '')] : null,
      item.publisher ? ['出版社', esc(item.publisher)] : null,
      item.place ? ['地点', esc(item.place)] : null,
      item.date ? ['日期', esc(item.date)] : null,
      item.volume ? ['卷次', esc(item.volume)] : null,
      item.issue ? ['期号', esc(item.issue)] : null,
      item.pages ? ['页码', esc(item.pages)] : null,
      item.journalAbbr ? ['刊名简称', esc(item.journalAbbr)] : null,
      item.doi ? ['DOI', `<a href="https://doi.org/${encodeURIComponent(item.doi)}" target="_blank" rel="noopener">${esc(item.doi)}</a>`] : null,
      item.url ? ['网址', `<a href="${esc(item.url)}" target="_blank" rel="noopener" style="display:inline-block;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom">${esc(item.url)}</a>`] : null,
      item.accessDate ? ['访问时间', esc(item.accessDate)] : null,
      (item.issn || item.isbn) ? [item.issn ? 'ISSN' : 'ISBN', esc(item.issn || item.isbn)] : null,
      item.language ? ['语言', esc(item.language)] : null,
      item.libraryCatalog ? ['文库编目', esc(item.libraryCatalog)] : null,
      item.extra ? ['其他', `<span style="font-size:11px;color:#94a3b8">${esc(item.extra)}</span>`] : null,
      item.dateAdded ? ['添加日期', esc(item.dateAdded)] : null,
      item.dateModified ? ['修改日期', esc(item.dateModified)] : null,
    ].filter(Boolean);

    const rowsHtml = fields.map(([k, v]) => `
      <div class="zt-row">
        <span class="zt-k">${esc(k)}</span>
        <span class="zt-v">${v}</span>
      </div>`).join('');

    const attList = (item.attachments && item.attachments.length)
      ? item.attachments.map(a => `
          <div class="zt-att-item">
            <span class="zt-att-icon">📄</span>
            <div class="zt-att-info">
              <div class="zt-att-name" title="${esc(a.title || a.filename)}">${esc(a.title || a.filename)}</div>
              ${a.path ? `<div class="zt-att-path" title="${esc(a.path)}">${esc(a.path)}</div>` : ''}
            </div>
            ${a.path || n.ref ? `<button class="zt-att-act" data-open-att="${esc(a.path || n.ref)}">打开</button>` : ''}
          </div>`).join('')
      : '<span class="quiet" style="font-size:12px;padding:4px">暂无附件文件</span>';

    const tagsHtml = (item.tags && item.tags.length)
      ? `<div class="zt-tags-wrap">${item.tags.map(t => `<span class="zt-tag-chip">#${esc(t)}</span>`).join('')}</div>`
      : '<span class="quiet" style="font-size:12px;padding:4px">无标签</span>';

    aside.innerHTML = `
      <section class="analysis glass zt-inspector">
        <header>
          <span class="eyebrow">${esc(m ? m.title : '文献')} <em>Paper</em><span class="zt-type-badge">${esc(itemType)}</span></span>
          <button class="icon-btn" data-close aria-label="关闭">×</button>
        </header>
        <div class="zt-scroll">
          <div class="zt-title-wrap">
            <h2 class="zt-paper-title">${esc(item.title || n.title)}</h2>
          </div>

          <div class="zt-toolbar">
            <button class="zt-tool-btn primary" data-act="open-pdf" title="在应用阅读器中打开全文 PDF">
              <span>📖</span> 阅读正文
            </button>
            <button class="zt-tool-btn zotero" data-act="open-zotero" title="调用本地 Zotero 协议定位此条目">
              <span>⚡</span> Zotero 中定位
            </button>
            <button class="zt-tool-btn" data-act="copy-cite" title="复制标准学术引文">
              <span>📋</span> 复制引文
            </button>
            <button class="zt-tool-btn" data-act="save-note" title="以此条目为基础存为笔记">
              <span>📝</span> 存为笔记
            </button>
          </div>

          <div id="ztSyncStatus">
            ${!hasCached ? '<div class="zt-status-bar"><span class="zt-spinner"></span> 正在从本地 Zotero 实时同步条目详情...</div>' : (item.zotero_found ? '<div class="zt-status-bar success">✓ 已与本地 Zotero 条目完整同步</div>' : '<div class="zt-status-bar" style="color:#94a3b8;border-color:rgba(255,255,255,0.1);background:rgba(255,255,255,0.03)">ⓘ 本地文献数据（Zotero 未索引或未开启）</div>')}
          </div>

          <!-- 折叠块 1：信息 -->
          <div class="zt-card" data-card="info">
            <button class="zt-card-head" type="button">
              <span class="zt-caret">▼</span>
              <span class="zt-icon">📄</span>
              <b>信息</b>
              <span class="zt-sub">Item Metadata</span>
            </button>
            <div class="zt-card-body">
              <div class="zt-grid">${rowsHtml}</div>
            </div>
          </div>

          <!-- 折叠块 2：摘要 -->
          <div class="zt-card" data-card="abstract">
            <button class="zt-card-head" type="button">
              <span class="zt-caret">▼</span>
              <span class="zt-icon">📝</span>
              <b>摘要</b>
              <span class="zt-sub">Abstract</span>
            </button>
            <div class="zt-card-body">
              <div class="zt-abstract-text">${esc(item.abstract || '（条目中暂无摘要内容）')}</div>
            </div>
          </div>

          <!-- 折叠块 3：附件 -->
          <div class="zt-card" data-card="attachments">
            <button class="zt-card-head" type="button">
              <span class="zt-caret">▼</span>
              <span class="zt-icon">📎</span>
              <b>附件 ${(item.attachments || []).length ? `(${item.attachments.length} 个)` : ''}</b>
            </button>
            <div class="zt-card-body">
              ${attList}
            </div>
          </div>

          <!-- 折叠块 4：标签 -->
          <div class="zt-card" data-card="tags">
            <button class="zt-card-head" type="button">
              <span class="zt-caret">▼</span>
              <span class="zt-icon">🏷️</span>
              <b>标签 ${(item.tags || []).length ? `(${item.tags.length})` : ''}</b>
            </button>
            <div class="zt-card-body">
              ${tagsHtml}
            </div>
          </div>

          <!-- 折叠块 5：星图关联与拓扑 -->
          <div class="zt-card" data-card="network">
            <button class="zt-card-head" type="button">
              <span class="zt-caret">▼</span>
              <span class="zt-icon">🕸️</span>
              <b>星图关联与拓扑</b>
              <span class="zt-sub">度 ${n.degree || (outs.length + ins.length)}</span>
            </button>
            <div class="zt-card-body">
              <div class="an-links" style="padding:0">
                <h4 style="margin:4px 0 6px">引用文献 <em>out ${outs.length}</em></h4>
                <div class="chip-row">${outs.length ? outs.map(chip).join('') : '<span class="quiet">无</span>'}</div>
                <h4 style="margin:10px 0 6px">被引文献 <em>in ${ins.length}</em></h4>
                <div class="chip-row">${ins.length ? ins.map(chip).join('') : '<span class="quiet">无</span>'}</div>
              </div>
            </div>
          </div>
        </div>
      </section>
    `;

    aside.querySelector('[data-close]').onclick = () => setFocus({mod: L.mod, sel: null});
    aside.querySelectorAll('[data-go]').forEach(b => b.onclick = () => setFocus({mod: L.mod, sel: b.dataset.go}));

    aside.querySelectorAll('.zt-card-head').forEach(btn => {
      btn.onclick = () => {
        const card = btn.closest('.zt-card');
        if (card) card.classList.toggle('collapsed');
      };
    });

    const openPdfBtn = aside.querySelector('[data-act="open-pdf"]');
    if (openPdfBtn) {
      openPdfBtn.onclick = () => {
        parent.postMessage({rw: 'open', kind: 'paper', ref: n.ref, title: n.title}, '*');
      };
    }

    const openZoteroBtn = aside.querySelector('[data-act="open-zotero"]');
    if (openZoteroBtn) {
      openZoteroBtn.onclick = () => {
        const zk = item.key;
        if (zk) {
          parent.postMessage({rw: 'openZoteroSelect', key: zk}, '*');
          showZtToast('正在调起本地 Zotero 定位条目...');
        } else {
          showZtToast('条目未在本地 Zotero 中建立关联');
        }
      };
    }

    const copyCiteBtn = aside.querySelector('[data-act="copy-cite"]');
    if (copyCiteBtn) {
      copyCiteBtn.onclick = () => {
        const authors = (item.creators && item.creators.length) ? item.creators.map(c => c.name).join(', ') : '';
        const yr = item.date ? ` (${item.date}). ` : '. ';
        const pub = item.publication ? `${item.publication}. ` : '';
        const doiStr = item.doi ? `https://doi.org/${item.doi}` : '';
        const citation = `${authors}${yr}${item.title || n.title}. ${pub}${doiStr}`.trim();
        navigator.clipboard.writeText(citation).then(() => {
          showZtToast('引文已复制到剪贴板！');
        }).catch(() => {
          showZtToast('已复制引文');
        });
      };
    }

    const saveNoteBtn = aside.querySelector('[data-act="save-note"]');
    if (saveNoteBtn) {
      saveNoteBtn.onclick = () => {
        if (item.key) {
          parent.postMessage({rw: 'saveNoteFromZotero', key: item.key, title: item.title}, '*');
          showZtToast('正在存入本地大笔记...');
        } else {
          parent.postMessage({rw: 'open', kind: 'note', ref: n.ref, title: n.title}, '*');
        }
      };
    }

    aside.querySelectorAll('[data-open-att]').forEach(btn => {
      btn.onclick = () => {
        const p = btn.dataset.openAtt;
        parent.postMessage({rw: 'openPath', path: p, ref: n.ref}, '*');
      };
    });

    if (!hasCached) {
      const wantId = n.id;
      const qUrl = '/api/starmap/item?id=' + encodeURIComponent(n.id) +
                   '&title=' + encodeURIComponent(n.title || '') +
                   '&doi=' + encodeURIComponent(n.doi || '');
      fetch(qUrl)
        .then(r => r.json())
        .then(data => {
          if (!data || data.error && !data.title) return;
          zoteroCache.set(wantId, data);
          if (L.sel === wantId && aside.isConnected) {
            renderZoteroPaperAnalysis(n, m, outs, ins, chip, near);
          }
        })
        .catch(err => {
          console.warn('Zotero item fetch failed:', err);
          const st = aside.querySelector('#ztSyncStatus');
          if (st) st.innerHTML = '<div class="zt-status-bar" style="color:#94a3b8">ⓘ 显示本地元数据（Zotero 未开启或未检索到）</div>';
        });
    }
  }

  // 右栏：字段 + 出链/反链/双向（点一条跳到它）
  function renderAnalysis() {
    aside.innerHTML = '';
    aside.classList.toggle('on', !!L.sel);
    if (!L.sel) return;
    const n = graph.index.get(L.sel);
    if (!n) return;
    const m = MODULES.find(x => x.id === L.mod);
    const near = id => { const t = graph.index.get(id); return t ? (t.short || t.title) : id; };
    const outs = graph.links.filter(l => l.a === n.id).map(l => l.b);
    const ins = graph.links.filter(l => l.b === n.id).map(l => l.a);
    const chip = id => `<button class="chip" data-go="${esc(id)}" style="--kc:${KINDS[graph.index.get(id).kind].color}">${esc(near(id))}</button>`;

    if (n.kind === 'paper' || n.kind === 'post') {
      renderZoteroPaperAnalysis(n, m, outs, ins, chip, near);
      return;
    }

    const rows = (n.meta || []).map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
    aside.innerHTML = `<section class="analysis glass">
      <header><span class="eyebrow">${esc(m ? m.title : '')} <em>${esc(KINDS[n.kind] ? KINDS[n.kind].en : '')}</em></span>
        <button class="icon-btn" data-close aria-label="关闭">×</button></header>
      <h2>${esc(n.title)}</h2>
      <div class="an-fields">${rows}</div>
      ${n.kind === 'concept' ? '<h4 class="an-brief-h">简介 <em>AI 生成</em></h4><p class="an-brief" data-brief>生成中…</p>' : ''}
      <div class="an-links">
        <h4>出链 <em>out ${outs.length}</em></h4><div class="chip-row">${outs.length ? outs.map(chip).join('') : '<span class="quiet">无</span>'}</div>
        <h4>反链 <em>in ${ins.length}</em></h4><div class="chip-row">${ins.length ? ins.map(chip).join('') : '<span class="quiet">无</span>'}</div>
      </div>
      ${n.ref ? `<button class="pill primary" data-open="${esc(n.ref)}">在应用里打开 →</button>` : ''}
    </section>`;
    aside.querySelector('[data-close]').onclick = () => setFocus({mod: L.mod, sel: null});
    aside.querySelectorAll('[data-go]').forEach(b => b.onclick = () => setFocus({mod: L.mod, sel: b.dataset.go}));
    const briefEl = aside.querySelector('[data-brief]');
    if (briefEl) {
      const want = n.id;
      fetch('/api/starmap/brief?term=' + encodeURIComponent(n.title))
        .then(r => r.json())
        .then(d => {
          if (L.sel !== want || !briefEl.isConnected) return;
          briefEl.textContent = d.text || ('（' + (d.error || '暂无简介') + '）');
          briefEl.classList.toggle('dim', !d.text);
        })
        .catch(e => { if (L.sel === want && briefEl.isConnected) briefEl.textContent = '简介加载失败：' + e.message; });
    }
    const openBtn = aside.querySelector('[data-open]');
    if (openBtn) openBtn.onclick = () => {
      parent.postMessage({rw: 'open', kind: n.kind, ref: n.ref, title: n.title}, '*');
    };
  }

  stageEl.addEventListener('click', e => {
    const act = e.target.closest('[data-act]');
    if (!act) return;
    if (act.dataset.act === 'replay') {
      L.t0 = performance.now(); L.time = 0;
      L.springs.core.s.x = 120; L.springs.core.o.x = 0;
      if (L.level === 'branches') setFocus({mod: null, sel: null});
    } else if (act.dataset.act === 'pause') {
      L.paused = !L.paused;
      act.textContent = L.paused ? '继续' : '暂停';
      coreBox.classList.toggle('paused', L.paused);
    }
  });

  stageEl.addEventListener('wheel', e => {
    if (L.level !== 'branches' || L.stage === 'hub' || e.target.closest('.dock, .analysis, .crumbs')) return;
    if (!L.all || L.all.length <= L.rows) return;
    const step = e.deltaY > 0 ? 3 : -3;
    if (scrollFan(step)) e.preventDefault();
  }, {passive: false});

  stageEl.classList.toggle('intro-done', L.introSeen);
  try { sessionStorage.setItem('kb-star-intro', '1'); } catch {}

  // Esc 逐层退回：条目 → 分支 → 项目 → 项目层
  const onKey = e => {
    if (e.key !== 'Escape' || e.isComposing) return;
    if (L.level !== 'branches') return;
    if (L.sel) setFocus({mod: L.mod, sel: null});
    else if (L.mod) setFocus({mod: null, sel: null});
    else showProjects();
  };
  window.addEventListener('keydown', onKey);

  // —— 每帧 ——
  const S = L.springs;
  let raf = 0, last = performance.now(), warned = false;
  function step(now) {
    const dt = Math.max(0, Math.min(1 / 30, (now - last) / 1000)); last = Math.max(last, now);
    if (!L.paused) L.time += dt;
    const t = L.time, since = (now - L.t0) / 1000;
    const Tt = targets(L, t);
    const intro = L.stage === 'hub' && !L.reduced && !L.introSeen;
    const coreS = intro && since < .15 ? Tt.core.s * .35 : Tt.core.s;
    const ox = spring(S.core.x, Tt.core.x, dt), oy = spring(S.core.y, Tt.core.y, dt);
    const os = spring(S.core.s, coreS, dt, 40), oo = spring(S.core.o, intro && since < .1 ? 0 : 1, dt, 30);
    L.core = {x: ox, y: oy, s: os};
    coreBox.style.transform = `translate3d(${ox - os / 2}px,${oy - os / 2}px,0) scale(${os / 460})`;
    coreBox.style.opacity = clamp(oo, 0, 1);

    L.subs.forEach((m, i) => {
      const j = S.cluster[m.id], tg = Tt.cluster[m.id], art = figData[m.fig];
      if (!j || !tg) return;
      const gated = intro && since < 1 + i * .17;
      const x = spring(j.x, gated ? ox : tg.x, dt), y = spring(j.y, gated ? oy : tg.y, dt);
      const s = spring(j.s, gated ? 24 : tg.s, dt), o = spring(j.o, gated ? 0 : tg.o, dt, 40);
      const lo = spring(j.lo, intro && since < 2 + i * .1 ? 0 : tg.o, dt, 40);
      const ring = L.stage === 'hub' || L.stage === 'projects';
      const k = s / BASE * (ring ? HUB_K : SUB_K);
      const B = art.bake.box * k;
      j.cur = {x, y, s, k};
      const box = refs.box[m.id], lab = refs.lab[m.id];
      box.style.transform = `translate3d(${x - B / 2}px,${y - B / 2}px,0) scale(${k})`;
      box.style.opacity = clamp(o, 0, 1);
      const on = m.id === L.mod || L.hover === m.id;
      drawCluster(refs.cvs[m.id], art, t, on, L.reduced || L.paused, L.hoverStar && L.hoverStar.id === m.id ? L.hoverStar.idx : -1);
      if (lab) {
        const lw = lab.offsetWidth, lh = lab.offsetHeight;
        const hub = ring, narrow = L.size.w < 760;
        // 标注贴着自己的星团、朝背离星核的一侧：方向取下标角度（呼吸摆动时才不跳）
        const a0 = m.angle * Math.PI / 180, nX = Math.cos(a0), nY = Math.sin(a0);
        const sup = (Math.abs(nX) * art.ext.hx + Math.abs(nY) * art.ext.hy) * k;
        const gap = sup + 14 + (Math.abs(nX) * lw + Math.abs(nY) * lh) / 2;
        const lx0 = hub ? x + nX * gap - lw / 2 : narrow ? x - lw / 2 : x + art.ext.hx * k + 12;
        const ly0 = hub ? y + nY * gap - lh / 2 : narrow ? y + art.ext.hy * k + 12 : y - lh / 2;
        const lx = clamp(lx0, 10, Math.max(10, L.size.w - lw - 10));
        const ly = clamp(ly0, 72, Math.max(72, L.size.h - lh - 10));
        lab.style.transform = `translate3d(${lx}px,${ly}px,0)`;
        lab.style.opacity = clamp(narrow && !hub && m.id !== L.mod ? 0 : lo, 0, 1);
        j.label = {x: lx, y: ly, w: lw, h: lh};
      }
      // 星核 → 星团：金色星座线 + 缓慢流动的光粒，收在图形外沿上
      const g = refs.th[m.id];
      if (g) {
        let x1, y1, x2, y2, c1x, c1y, c2x, c2y;
        if (ring) {
          const dx = x - ox, dy = y - oy, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d;
          const near = (Math.abs(ux) * art.ext.hx + Math.abs(uy) * art.ext.hy) * k;
          x1 = ox + ux * os * .2; y1 = oy + uy * os * .2; x2 = x - ux * near; y2 = y - uy * near;
          const bend = 26 * Math.sin(t * .5 + i);
          c1x = x1 + dx * .35 - uy * bend; c1y = y1 + dy * .35 + ux * bend;
          c2x = x1 + dx * .7 + uy * bend; c2y = y1 + dy * .7 - ux * bend;
        } else {
          x1 = ox + os * .3; y1 = oy; x2 = x - art.ext.hx * k; y2 = y;
          const dx = x2 - x1; c1x = x1 + dx * .55; c1y = y1; c2x = x2 - dx * .55; c2y = y2;
        }
        const d = `M${x1},${y1} C${c1x},${c1y} ${c2x},${c2y} ${x2},${y2}`;
        g.glow.setAttribute('d', d); g.core.setAttribute('d', d);
        const vis = clamp(o, 0, 1) * (ring ? 1 : on ? 1 : .45);
        g.glow.style.opacity = vis * (on ? .95 : .5); g.core.style.opacity = vis;
        (refs.pulse[m.id] || []).forEach((c, kk) => {
          const inbound = kk === 2;
          let u = ((t * (inbound ? .09 : .12) + kk * .5 + i * .17) % 1);
          if (inbound) u = 1 - u;
          c.setAttribute('cx', bez(x1, c1x, c2x, x2, u));
          c.setAttribute('cy', bez(y1, c1y, c2y, y2, u));
          c.style.opacity = L.reduced || L.paused ? 0 : vis * Math.sin(u * Math.PI);
        });
      }
    });

    // 选中簇 → 团内条目列队展开，星座线一根根显形
    const sj = L.mod && S.cluster[L.mod];
    if (Tt.fan && sj && sj.label) {
      const narrow = L.size.w < 760;
      const ox2 = narrow ? sj.label.x + sj.label.w / 2 : sj.label.x + sj.label.w + 6;
      const oy2 = narrow ? sj.label.y + sj.label.h + 4 : sj.cur.y;
      L.items.forEach((n, i) => {
        if (!L.itemState[i]) L.itemState[i] = L.fanSnap
          ? {x: sp(Tt.fan.x), y: sp(Tt.fan.y0 + i * Tt.fan.gap), o: sp(1)}
          : {x: sp(ox2), y: sp(oy2), o: sp(0)};
        const st = L.itemState[i];
        const ready = L.reduced || now - L.fanStart > 160 + i * Math.min(90, 1800 / Math.max(L.items.length, 1));
        const tx = Tt.fan.x, ty = Tt.fan.y0 + i * Tt.fan.gap + (L.reduced ? 0 : Math.sin(t * .8 + i * .9) * 2);
        const x = spring(st.x, ready ? tx : ox2, dt, 70), y = spring(st.y, ready ? ty : oy2, dt, 70), o = spring(st.o, ready ? 1 : 0, dt, 50);
        const it = itemEls[i];
        if (it) { it.style.transform = `translate3d(${x}px,${y - 15}px,0)`; it.style.opacity = clamp(o, 0, 1); }
        const p = fanPathEls[i];
        if (p) {
          const dx = x - ox2, sway = L.reduced ? 0 : Math.sin(t * 1.1 + i * .7) * 7;
          const c1x = ox2 + dx * .5, c1y = oy2 + sway, c2x = x - dx * .45, c2y = y - sway * .5;
          p.setAttribute('d', `M${ox2},${oy2} C${c1x},${c1y} ${c2x},${c2y} ${x},${y}`);
          const sel = L.sel === n.id;
          p.style.opacity = clamp(o, 0, 1) * (L.sel ? (sel ? 1 : .35) : .8);
          p.style.strokeWidth = sel ? 1.8 : 1;
          (fanPulseEls[i] || []).forEach((c, kk) => {
            let u = (t * (kk ? .09 : .13) + i * .13 + kk * .5) % 1;
            if (kk) u = 1 - u;
            c.setAttribute('cx', bez(ox2, c1x, c2x, x, u));
            c.setAttribute('cy', bez(oy2, c1y, c2y, y, u));
            c.style.opacity = L.reduced || L.paused ? 0 : clamp(o, 0, 1) * Math.sin(u * Math.PI) * (L.sel && !sel ? .3 : 1);
          });
        }
      });
      if (moreEl) {
        const ty = Tt.fan.y0 + L.items.length * Tt.fan.gap;
        if (!L.moreState) L.moreState = {x: sp(ox2), y: sp(oy2), o: sp(0)};
        const st = L.moreState;
        const ready = L.reduced || now - L.fanStart > 160 + L.items.length * Math.min(90, 1800 / Math.max(L.items.length, 1));
        const x = spring(st.x, ready ? Tt.fan.x : ox2, dt, 70), y = spring(st.y, ready ? ty : oy2, dt, 70), o = spring(st.o, ready ? 1 : 0, dt, 50);
        moreEl.style.transform = `translate3d(${x}px,${y - 15}px,0)`;
        moreEl.style.opacity = clamp(o, 0, 1) * .85;
      }
      L.fanSnap = false;          // 这一帧已经按目标位置落好，后面交回弹簧
    }
  }

  // 首层：概览 → 项目层；带 task 的深链直接进该项目
  if (api && api.level === 'projects') {
    cache.projects = api.projects || [];
    cache.totals = api.totals || {};
    showProjects();
  } else if (api && api.task) {
    cache.byId[api.task.id] = api;
    applyProject(api, opts.focus);
  } else {
    stageEl.innerHTML = '<p style="padding:40px;color:#8b9bb0">还没有项目 —— 先去「项目」页建一个，星图才有得画。</p>';
  }

  // ?still=1 / prefers-reduced-motion：跳过入场动画，直接把弹簧推到终点（也方便截图与验收）
  const still = new URLSearchParams(location.search).get('still') === '1';
  if (still || reduced) {            // 减少动效的用户直接给终点状态，不放过场
    L.reduced = true; L.introSeen = true;
    let n = last;
    for (let i = 0; i < 60; i++) { last = n; n += 33; step(n); }
  }
  const frame = now => { raf = requestAnimationFrame(frame); try { step(now); } catch (e) { if (!warned) { warned = true; console.error(e); } } };
  raf = requestAnimationFrame(frame);

  // 尺寸：ResizeObserver 跟随窗口（含侧栏折叠）
  const measure = () => {
    L.size = {w: stageEl.clientWidth, h: stageEl.clientHeight};
    if ((L.stage === 'hub' || L.stage === 'projects') && performance.now() - L.t0 < 300) {
      const o = targets({...L}, 0).core;
      Object.assign(S.core, {x: sp(o.x), y: sp(o.y)});
      L.subs.forEach(m => Object.assign(S.cluster[m.id] || (S.cluster[m.id] = {x: sp(20), y: sp(20), s: sp(20), o: sp(0), lo: sp(0)}), {x: sp(o.x), y: sp(o.y)}));
      L.core = {...L.core, x: o.x, y: o.y};
    }
    const rows = rowsThatFit(Math.max(L.size.h, 560));
    if (L.level === 'branches' && L.stage !== 'hub' && L.fanRows > rows) setFocus({mod: L.mod, sel: L.sel});
    renderAnalysis();
  };
  const ro = new ResizeObserver(measure);
  ro.observe(stageEl);
  measure();

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    window.removeEventListener('keydown', onKey);
    disposeCore();
    unmountCosmos();
    stageEl.remove();
  };
}
