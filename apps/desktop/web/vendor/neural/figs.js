// 五个星座的真实骨架 —— 看板 v6.3 落地：主视图的五个星团 / 内页的星座轨都用它。
//
// 数据来源与口径：
//   · 星位：J2000 赤经/赤纬（度）+ 视星等 + 学名，取自 d3-celestial 的 constellations.lines.json /
//     stars.6.json / starnames.json（与 Stellarium、Sky & Telescope 星座图同一套经典连线）；
//   · 投影：x = −(RA−RA₀)·cos(Dec₀)、y = −(Dec−Dec₀) —— 天图口径（北在上、东在左，不做镜像），
//     圆心用圆均值并把 RA 解卷绕到 RA₀±180°（双鱼座横跨 0h，直接取平均会被拉直），再归一到「最大半幅 = 1」；
//   · 星等只影响光晕/星芒大小，不连线 ——「哪颗亮」是真的；
//   · 连线逐段照抄经典连线（射手座是整幅 25★/29 段），没有自创任何一段。
//
// 映射：知识笔记→天蝎 Sco · 概念→双鱼 Psc · 作品→处女 Vir · 选题→射手 Sgr · 草稿→水瓶 Aqr。
import {T} from './theme.js';
import {rng, hex2rgb} from './sky.js';

export const SPECS = {
  methods: {
    zh: '天蝎座', en: 'Scorpius', nodes: 82,
    stars: [
      {ra: 239.7130, dec: -26.1141, mag: 2.89, n: 'π Fang'},
      {ra: 240.0834, dec: -22.6217, mag: 2.29, n: 'δ Dschubba'},
      {ra: 241.3593, dec: -19.8055, mag: 2.56, n: 'β1 Acrab'},
      {ra: 245.2972, dec: -25.5928, mag: 2.90, n: 'σ Alniyat'},
      {ra: 247.3519, dec: -26.4320, mag: 1.06, n: 'α Antares'},
      {ra: 248.9706, dec: -28.2160, mag: 2.82, n: 'τ Paikauhale'},
      {ra: 252.5409, dec: -34.2932, mag: 2.29, n: 'ε Larawag'},
      {ra: 252.9676, dec: -38.0474, mag: 3.00, n: 'μ1 Xamidimura'},
      {ra: 253.6459, dec: -42.3613, mag: 3.62, n: 'ζ2 Grafias'},
      {ra: 258.0383, dec: -43.2392, mag: 3.32, n: 'η'},
      {ra: 264.3297, dec: -42.9978, mag: 1.86, n: 'θ Sargas'},
      {ra: 266.8962, dec: -40.1270, mag: 2.99, n: 'ι1 Girtab'},
      {ra: 265.6220, dec: -39.0300, mag: 2.39, n: 'κ Mula'},
      {ra: 263.4022, dec: -37.1038, mag: 1.62, n: 'λ Shaula'}
    ],
    edges: [[0, 1], [1, 2], [1, 3], [3, 4], [4, 5], [5, 6], [6, 7], [7, 8], [8, 9], [9, 10], [10, 11], [11, 12], [12, 13]]
  },
  concepts: {
    zh: '双鱼座', en: 'Pisces', nodes: 40,
    stars: [
      {ra: 18.4373, dec: 24.5837, mag: 4.67, n: 'φ'},
      {ra: 17.9152, dec: 30.0896, mag: 4.51, n: 'τ'},
      {ra: 19.8666, dec: 27.2641, mag: 4.74, n: 'υ'},
      {ra: 17.8634, dec: 21.0347, mag: 4.66, n: 'χ'},
      {ra: 22.8709, dec: 15.3458, mag: 3.62, n: 'η Alpherg'},
      {ra: 26.3485, dec: 9.1577, mag: 4.26, n: 'ο Torcular'},
      {ra: 30.5118, dec: 2.7638, mag: 3.82, n: 'α Alrescha'},
      {ra: 28.3890, dec: 3.1875, mag: 4.61, n: 'ξ'},
      {ra: 25.3579, dec: 5.4876, mag: 4.45, n: 'ν'},
      {ra: 22.5463, dec: 6.1438, mag: 4.84, n: 'μ'},
      {ra: 18.4329, dec: 7.5754, mag: 5.21, n: 'ζ Revati'},
      {ra: 15.7359, dec: 7.8901, mag: 4.27, n: 'ε'},
      {ra: 12.1706, dec: 7.5851, mag: 4.44, n: 'δ Kuton'},
      {ra: 359.8279, dec: 6.8633, mag: 4.03, n: 'ω'},
      {ra: 354.9877, dec: 5.6263, mag: 4.13, n: 'ι'},
      {ra: 351.9921, dec: 6.3790, mag: 4.27, n: 'θ'},
      {ra: 350.0858, dec: 5.3813, mag: 5.05, n: 'b'},
      {ra: 349.2914, dec: 3.2823, mag: 3.70, n: 'γ'},
      {ra: 351.7331, dec: 1.2556, mag: 4.95, n: 'κ'},
      {ra: 355.5117, dec: 1.7800, mag: 4.49, n: 'λ'},
      {ra: 356.5980, dec: 3.4868, mag: 4.95, n: '19'},
      {ra: 345.9692, dec: 3.8200, mag: 4.48, n: 'β Fumalsamakah'}
    ],
    edges: [[0, 1], [1, 2], [0, 2], [0, 3], [3, 4], [4, 5], [5, 6], [6, 7], [7, 8], [8, 9], [9, 10], [10, 11], [11, 12], [12, 13], [13, 14], [14, 15], [15, 16], [16, 17], [17, 18], [18, 19], [19, 20], [14, 20], [17, 21]]
  },
  works: {
    zh: '处女座', en: 'Virgo', nodes: 29,
    stars: [
      {ra: 176.4648, dec: 6.5294, mag: 4.04, n: 'ν'},
      {ra: 177.6738, dec: 1.7647, mag: 3.59, n: 'β Zavijava'},
      {ra: 184.9765, dec: -0.6668, mag: 3.89, n: 'η Zaniah'},
      {ra: 190.4152, dec: -1.4494, mag: 2.74, n: 'γ Porrima'},
      {ra: 197.4875, dec: -5.5390, mag: 4.38, n: 'θ Apami-Atsa'},
      {ra: 201.2982, dec: -11.1613, mag: 0.98, n: 'α Spica'},
      {ra: 214.0036, dec: -6.0005, mag: 4.07, n: 'ι Syrma'},
      {ra: 220.7651, dec: -5.6582, mag: 3.87, n: 'μ Rijl al Awwa'},
      {ra: 195.5442, dec: 10.9592, mag: 2.85, n: 'ε Vindemiatrix'},
      {ra: 193.9009, dec: 3.3975, mag: 3.39, n: 'δ Minelauva'},
      {ra: 203.6733, dec: -0.5958, mag: 3.38, n: 'ζ Heze'},
      {ra: 210.4116, dec: 1.5445, mag: 4.23, n: 'τ'},
      {ra: 221.5622, dec: 1.8929, mag: 3.73, n: '109'}
    ],
    edges: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 7], [8, 9], [3, 9], [4, 10], [10, 11], [11, 12]]
  },
  ideas: {
    zh: '射手座', en: 'Sagittarius', nodes: 15,
    stars: [
      {ra: 274.4068, dec: -36.7617, mag: 3.10, n: 'η Hamalwarid'},
      {ra: 276.0430, dec: -34.3846, mag: 1.79, n: 'ε Kaus Australis'},
      {ra: 275.2485, dec: -29.8281, mag: 2.72, n: 'δ Kaus Media'},
      {ra: 276.9927, dec: -25.4217, mag: 2.82, n: 'λ Kaus Borealis'},
      {ra: 273.4409, dec: -21.0588, mag: 3.84, n: 'μ Polis'},
      {ra: 290.6596, dec: -44.4590, mag: 3.96, n: 'β1 Arkab Prior'},
      {ra: 290.9716, dec: -40.6159, mag: 3.96, n: 'α Rukbat'},
      {ra: 285.6530, dec: -29.8801, mag: 2.60, n: 'ζ Ascella'},
      {ra: 281.4141, dec: -26.9908, mag: 3.17, n: 'φ Namalsadirah'},
      {ra: 298.8154, dec: -41.8683, mag: 4.12, n: 'ι'},
      {ra: 299.9341, dec: -35.2763, mag: 4.37, n: 'θ1'},
      {ra: 298.9598, dec: -26.2995, mag: 4.70, n: 'ω Terebellum'},
      {ra: 294.1768, dec: -24.8836, mag: 4.59, n: 'h2'},
      {ra: 291.3187, dec: -24.5086, mag: 5.02, n: 'χ1'},
      {ra: 288.8851, dec: -25.2567, mag: 4.86, n: 'ψ Al Kiladah'},
      {ra: 283.8164, dec: -26.2967, mag: 2.05, n: 'σ Nunki'},
      {ra: 271.4520, dec: -30.4241, mag: 2.98, n: 'γ2 Alnasl'},
      {ra: 286.7350, dec: -27.6704, mag: 3.32, n: 'τ'},
      {ra: 286.1708, dec: -21.7415, mag: 3.76, n: 'ο Manubrij'},
      {ra: 287.4410, dec: -21.0236, mag: 2.88, n: 'π Albaldah'},
      {ra: 289.4087, dec: -18.9529, mag: 4.88, n: 'd'},
      {ra: 290.4182, dec: -17.8472, mag: 3.92, n: 'ρ1'},
      {ra: 290.4318, dec: -15.9550, mag: 4.52, n: 'υ'},
      {ra: 284.4325, dec: -21.1067, mag: 3.52, n: 'ξ2'},
      {ra: 283.5424, dec: -22.7448, mag: 4.86, n: 'ν1 Ainalrami'}
    ],
    edges: [[0, 1], [1, 2], [2, 3], [3, 4], [5, 6], [6, 7], [7, 8], [3, 8], [9, 10], [10, 11], [11, 12], [12, 13], [13, 14], [14, 15], [8, 15], [2, 8], [2, 16], [1, 16], [1, 7], [7, 17], [15, 17], [15, 18], [18, 19], [19, 20], [20, 21], [21, 22], [18, 23], [23, 24], [15, 24]]
  },
  drafts: {
    zh: '水瓶座', en: 'Aquarius', nodes: 4,
    stars: [
      {ra: 311.9190, dec: -9.4958, mag: 3.78, n: 'ε Albali'},
      {ra: 313.1635, dec: -8.9833, mag: 4.73, n: 'μ Albulan'},
      {ra: 322.8897, dec: -5.5712, mag: 2.90, n: 'β Sadalsuud'},
      {ra: 331.4460, dec: -0.3199, mag: 2.95, n: 'α Sadalmelik'},
      {ra: 335.4141, dec: -1.3873, mag: 3.86, n: 'γ Sadachbia'},
      {ra: 337.2080, dec: -0.0200, mag: 3.65, n: 'ζ1 Sadaltager'},
      {ra: 338.8391, dec: -0.1175, mag: 4.04, n: 'η'},
      {ra: 343.1536, dec: -7.5796, mag: 3.73, n: 'λ Hydor'},
      {ra: 349.4759, dec: -9.1825, mag: 4.41, n: 'ψ2'},
      {ra: 347.3617, dec: -21.1724, mag: 3.68, n: 'c2'},
      {ra: 331.6093, dec: -13.8697, mag: 4.29, n: 'ι'},
      {ra: 334.2085, dec: -7.7833, mag: 4.17, n: 'θ Ancha'},
      {ra: 336.3193, dec: 1.3774, mag: 4.80, n: 'π Seat'},
      {ra: 350.7426, dec: -20.1006, mag: 3.96, n: 'b1'},
      {ra: 355.4409, dec: -17.8165, mag: 4.82, n: 'A2'}
    ],
    edges: [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [6, 7], [7, 8], [8, 9], [2, 10], [3, 11], [5, 12], [8, 13], [8, 14]]
  }
};

// 每个簇的摆位（设计空间 = 288 基准像素；与设计稿同一套数值）：
//   s   每单位像素（归一到「最大半幅 = 1」后再乘 s）
//   fx/fy 成员星场的分布范围（贴住各自骨架，扁的星座给扁的星场）
export const FIG = {
  concepts: {s: 112, fx: 150, fy: 100},
  works: {s: 130, fx: 150, fy: 88},
  methods: {s: 132, fx: 142, fy: 152},
  ideas: {s: 100, fx: 108, fy: 84},
  drafts: {s: 118, fx: 118, fy: 84}
};
// 模块名（与图例同一套叫法）
export const MODS = {
  works: ['文献', 'Papers'], methods: ['笔记', 'Notes'],
  ideas: ['检索候选', 'Candidates'], drafts: ['工作流', 'Workflow'], concepts: ['知识点', 'Concepts']
};
// 设计基准：主视图里簇的「标准尺寸」——簇在屏幕上的尺寸为 size 时，图形按 size/288 缩放
export const BASE = 288;

// —— 投影 + 归一 + 星等折算（模块加载时算一次，坐标表保持「真实数据」形态便于复核） ——
const rad = d => d * Math.PI / 180;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
for (const sp of Object.values(SPECS)) {
  const ra0 = (Math.atan2(
    sp.stars.reduce((a, t) => a + Math.sin(rad(t.ra)), 0) / sp.stars.length,
    sp.stars.reduce((a, t) => a + Math.cos(rad(t.ra)), 0) / sp.stars.length
  ) * 180 / Math.PI + 360) % 360;
  const dec0 = sp.stars.reduce((a, t) => a + t.dec, 0) / sp.stars.length;
  const cs = Math.cos(rad(dec0));
  const unwrap = ra => { let d = ra - ra0; while (d > 180) d -= 360; while (d < -180) d += 360; return ra0 + d; };
  const raw = sp.stars.map(t => [-(unwrap(t.ra) - ra0) * cs, -(t.dec - dec0)]);
  const half = Math.max(...raw.map(p => Math.max(Math.abs(p[0]), Math.abs(p[1]))));
  sp.pts = raw.map(p => [p[0] / half, p[1] / half]);
  // 星等窗口 (5.6, 4.6)：1 等星 = 1.0（心宿二 1.06 / 角宿一 0.98），5 等星 ≈ .16
  sp.stars.forEach(t => { t.m = clamp((5.6 - t.mag) / 4.6, .16, 1); });
}

// 骨架主星在设计空间里的坐标（原点 = 簇心）
export function skeleton(id) {
  const sp = SPECS[id], L = FIG[id];
  if (!sp) return [];
  return sp.pts.map((p, i) => ({x: p[0] * L.s, y: p[1] * L.s, m: sp.stars[i].m, name: sp.stars[i].n, idx: i}));
}

// 簇画布的设计边长：把骨架外沿、成员星场、亮星的星芒都装进去，再留一点余量
const PAD = 46;
export function boxOf(id) {
  const L = FIG[id];
  const sk = skeleton(id);
  const ext = Math.max(
    ...sk.map(p => Math.max(Math.abs(p.x), Math.abs(p.y))),
    L.fx, L.fy
  ) + PAD;
  return 2 * Math.ceil(ext);
}

// 骨架的两个半幅（设计空间）—— 标注要「贴着图形」就靠它，跟成员星场无关：
// 标注要贴近星座、远离星核区，所以标注按骨架外沿量，不按星场。
export function extentOf(id) {
  const sk = skeleton(id);
  return {hx: Math.max(...sk.map(p => Math.abs(p.x))), hy: Math.max(...sk.map(p => Math.abs(p.y)))};
}

// 命中区 = 骨架外沿多边形（凸包，径向外扩 34 设计像素），不是 bbox：
// 星座是「J 形 / 长折线 / 双鱼丝带」这类细长形状，bbox 的四个空角会误伤相邻的星座与标签。
export function hitOf(id) {
  const sk = skeleton(id);
  const pts = [...sk.map(p => [p.x, p.y])].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const half = (arr) => {
    const out = [];
    for (const p of arr) { while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], p) <= 0) out.pop(); out.push(p); }
    return out;
  };
  const hull = [...half(pts).slice(0, -1), ...half([...pts].reverse()).slice(0, -1)];
  const gx = pts.reduce((s, p) => s + p[0], 0) / pts.length, gy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
  const out = (hull.length >= 3 ? hull : pts).map(([x, y]) => {
    const dx = x - gx, dy = y - gy, d = Math.hypot(dx, dy) || 1, m = 34;
    return [x + dx / d * m, y + dy / d * m];
  });
  const minX = Math.min(...out.map(p => p[0])), maxX = Math.max(...out.map(p => p[0]));
  const minY = Math.min(...out.map(p => p[1])), maxY = Math.max(...out.map(p => p[1]));
  const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
  return {
    w, h, cx: (minX + maxX) / 2, cy: (minY + maxY) / 2,
    clip: out.map(([x, y]) => `${((x - minX) / w * 100).toFixed(1)}% ${((y - minY) / h * 100).toFixed(1)}%`).join(', ')
  };
}

// —— 画法（同一套：星场 → 归属连线 → 骨架连线 → 成员暗星 → 主星） ——
const segDist2 = (px, py, ax, ay, bx, by) => {
  const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L)) : 0;
  return (px - ax - dx * t) ** 2 + (py - ay - dy * t) ** 2;
};

// ctx 的当前原点必须是簇心；返回 {S, field}（星场供逐帧闪烁与命中用）
export function paintConstellation(ctx, id, opt = {}) {
  const sp = SPECS[id], L = FIG[id];
  if (!sp || !L) return null;
  const M = T.modules[id] || T.modules.works;
  const R = rng((opt.seed ?? 1) * 7 + 5);
  const S = skeleton(id);
  const boost = opt.boost ?? 1;
  const gold = hex2rgb(T.line.core), goldFlow = hex2rgb(T.line.flow), gold2 = hex2rgb(T.line.glow);

  // ① 成员星场：骨架之外的节点星（数量 = 该模块节点数 − 骨架星数；选题/草稿比骨架星少，所以没有星场）
  const field = [];
  if (opt.field !== false) {
    const nF = Math.max(0, sp.nodes - sp.stars.length);
    for (let i = 0; i < nF; i++) {
      let q = null;
      for (let tryI = 0; tryI < 6 && !q; tryI++) {
        const a = R() * 6.283, d = Math.pow(R(), .58);
        const x = Math.cos(a) * d * L.fx, y = Math.sin(a) * d * L.fy;
        let near = 1e9;
        for (const e of sp.edges) {
          const p = S[e[0]], r = S[e[1]];
          near = Math.min(near, segDist2(x, y, p.x, p.y, r.x, r.y));
        }
        if (near < (L.s * .80) ** 2 || R() < .5) q = {x, y};
      }
      if (!q) continue;
      let bi = 0, bd = 1e9;                              // 归属主星（就近）—— 星场因此长在图形上，而不是一个圆盘上
      S.forEach((p, k) => { const d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2; if (d < bd) { bd = d; bi = k; } });
      field.push({...q, b: Math.pow(R(), 2.2), w: R(), host: bi});
    }
  }

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';

  // ② 星场连线：只连「归属于自己的主星」的那一根，极淡 —— 把暗星焊进星座
  if (opt.links !== false) {
    ctx.lineWidth = .6;
    for (const f of field) {
      if (R() > .34) continue;
      const p = S[f.host];
      ctx.strokeStyle = `rgba(${gold2},${(.06 + f.b * .08) * boost})`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(f.x, f.y); ctx.stroke();
    }
  }

  // ③ 骨架连线：先一道宽而淡的光晕，再一道细的实体 —— 星座的「看得见」靠这两笔
  for (const pass of [[4.6, .075], [1.3, .70]]) {
    ctx.lineWidth = pass[0];
    for (const [a, b] of sp.edges) {
      const p = S[a], q = S[b];
      const g = ctx.createLinearGradient(p.x, p.y, q.x, q.y);
      const al = pass[1] * boost;
      g.addColorStop(0, `rgba(${gold},${al})`);
      g.addColorStop(.5, `rgba(${goldFlow},${al * 1.25})`);
      g.addColorStop(1, `rgba(${gold},${al})`);
      ctx.strokeStyle = g;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
    }
  }

  // ④ 成员暗星
  for (const f of field) {
    const a = (.20 + f.b * .55) * boost, r = .45 + f.b * 1.3;
    ctx.fillStyle = `rgba(${hex2rgb(f.w < .45 ? M.color : f.w < .8 ? M.warm : '#ffffff')},${a})`;
    ctx.beginPath(); ctx.arc(f.x, f.y, r, 0, 7); ctx.fill();
  }

  // ⑤ 骨架主星：光晕 → 星芒（亮星）→ 白点。主星必须比星场高一档，否则图形立不住
  for (const st of S) {
    const halo = 7 + st.m * 15;
    const g = ctx.createRadialGradient(st.x, st.y, 0, st.x, st.y, halo);
    g.addColorStop(0, `rgba(${hex2rgb(M.warm)},${.50 * boost})`);
    g.addColorStop(.30, `rgba(${hex2rgb(M.color)},${.19 * boost})`);
    g.addColorStop(1, `rgba(${hex2rgb(M.color)},0)`);
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(st.x, st.y, halo, 0, 7); ctx.fill();
    if (st.m > .6) {                                    // 亮星：十字星芒（长短不一，像真的衍射）
      const len = (11 + st.m * 30) * boost;
      const sg = ctx.createLinearGradient(st.x - len, st.y, st.x + len, st.y);
      sg.addColorStop(0, 'rgba(255,250,238,0)'); sg.addColorStop(.5, `rgba(255,250,238,${.5 * boost})`); sg.addColorStop(1, 'rgba(255,250,238,0)');
      ctx.fillStyle = sg; ctx.fillRect(st.x - len, st.y - .55, len * 2, 1.1);
      const vg = ctx.createLinearGradient(st.x, st.y - len * .78, st.x, st.y + len * .78);
      vg.addColorStop(0, 'rgba(255,250,238,0)'); vg.addColorStop(.5, `rgba(255,250,238,${.42 * boost})`); vg.addColorStop(1, 'rgba(255,250,238,0)');
      ctx.fillStyle = vg; ctx.fillRect(st.x - .55, st.y - len * .78, 1.1, len * 1.56);
    }
    ctx.fillStyle = `rgba(255,255,253,${(.72 + st.m * .28) * boost})`;
    ctx.beginPath(); ctx.arc(st.x, st.y, 1.15 + st.m * 1.5, 0, 7); ctx.fill();
  }
  ctx.restore();

  // ⑥ 界线圈（可选）：一圈极淡的虚线，读作「星图上的星座边界」
  if (opt.rim) {
    ctx.save();
    ctx.setLineDash([4, 7]); ctx.lineWidth = 1;
    ctx.strokeStyle = `rgba(${gold},${.10 * opt.rim})`;
    ctx.beginPath(); ctx.ellipse(0, 0, L.fx * 1.12, L.fy * 1.10, 0, 0, 7); ctx.stroke();
    ctx.restore();
  }
  return {S, field};
}

// —— 烘焙：星座图形是静态的（星位/星等/连线都是真实数据，跟列表内容无关），
//    整幅画到一张离屏画布上，逐帧只做一次 drawImage + 主星闪烁 ——
const bake = new Map();
export function baked(id, dpr = 2) {
  const key = id + '@' + dpr;
  if (bake.has(key)) return bake.get(key);
  const B = boxOf(id), c = document.createElement('canvas');
  c.width = c.height = Math.round(B * dpr);
  const g = c.getContext('2d');
  if (g) {
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.translate(B / 2, B / 2);
    try { paintConstellation(g, id, {seed: 3}); } catch (err) { console.error('[v6.3 星座] 烘焙失败', id, err); }
  }
  const out = {canvas: c, box: B};
  bake.set(key, out);
  return out;
}

// 标注上的一行「星数 · 连线」——数字来自真实星表，不是模块条目数
export const countsOf = id => ({stars: SPECS[id].stars.length, edges: SPECS[id].edges.length, nodes: SPECS[id].nodes});
export const nameOf = id => SPECS[id] ? [SPECS[id].zh, SPECS[id].en] : ['', ''];
