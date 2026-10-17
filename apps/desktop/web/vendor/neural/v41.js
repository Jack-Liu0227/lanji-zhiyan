// v4.1 星核 + 木星式环系 —— 真看板渲染器（纯 Canvas 2D）
// ---------------------------------------------------------------------------
// v6.4（2026-10-13）：**参数字典换回 v3-b**。
// 代码一行没删 —— v3 与 v4 的分叉本来就全挂在可选参数上（`o.solid` / `o.arcMarks` /
// `o.overProm2`），`makeOptions()` 给到 v3-b 那份参数，画法自动逐条走 v3 老路径：
//   · k=.185 近侧视（环压成一条锋利的尘埃线）· rCore=104 · flare=.20（星芒收回）
//   · 核色板换成 v3-b 那组（hot 纯白 → deep 中性灰），lobes 4、rays 88、turb 940、fibrils 84
//   · 外层只有一圈淡尘环（v4.1 是两圈）· 极向喷流打开（jets，v3-b 的招牌）
// 与 v4.1 的差别只在「核长什么样」：环带宽度仍由真看板图谱给（形由数据生成这条原则不变）。
// ---------------------------------------------------------------------------
// v4.1 与 v3-b 是两套预设参数，由 makeOptions() 选择，画法自动走对应分支。
// 移植时保留的清单（与设计稿逐条对齐）：
//   · Voronoi 米粒镶嵌核（平涂 + 共边细缝 + 圆角 + 半分辨率离屏模糊）
//   · 多环带环系：缝隙 / 密度不均 / 环带宽度 = 各轴节点数 / 前后遮挡 / 内侧受光 / 后向散射
//   · 方位向亮弧（阿刻戎式：只在环里某一段更密，不是画一条亮带）
//   · 跨环日珥（在环系自己的坐标里取样，两条腿必然横跨前环）
//   · 前环暗带横切（面光源 → 有半影、缝处漏光）
//   · 碎临边弧 + 针状体 + 日冕鬃毛 + 暗纤（核的「边」与「体」）
//
// 与基础版本的工程差异：
//   ① 数据驱动：环带宽度 / 颜色 / 节点数来自真看板图谱（作品 · 知识 · 选题 · 草稿），
//      概念节点数决定最外两圈弥散尘环 —— 与「形由数据生成」同一条原则。
//   ② 合成策略 = 离屏预渲染：核体（团块 + 米粒 + 等离子 + 耀斑）渲 4 个呼吸相位，
//      逐帧交叉淡入；前环暗带渲一张（逐帧只调 alpha 做呼吸）。环系与尘埃逐帧实时画（它们要转）。
//   ③ 尘埃快路径：预计算三角向量 + 光场径向查表 + alpha 量化合批，3.2 万颗满帧；
//      转速按「开普勒剪切」：ω ∝ r^-1.5，内带快、外带慢。
//   ④ 零 WebGL 依赖：拿不到 2D 上下文时由调用方回退 CSS 星核（不白屏）。

export const TAU = Math.PI * 2;

// ===========================================================================
// 0. 工具（对应 core.js 的同名函数）
// ===========================================================================
// v6.4：随机源换回基准那一支（mulberry32，`seed >>> 0` 起手）。
// 之前移植版用的是 xorshift32 —— 同一颗种子给出的**不是同一串数**，于是核上每一处
// 由随机数决定的东西（团块位置、星芒与日珥的逐段亮度、米粒落点）都与基准错开。
// v4.1 的核是致密镶嵌，错开了看不出来；v3-b 的核是稀疏高对比的星芒与日珥，错开就成了
// 「结构都在原地、明暗各不相同」。要「设计稿所用同一份代码」，随机源必须也是同一份。
export function rnd(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
export const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
export const hex = h => h.replace('#', '').match(/../g).map(x => parseInt(x, 16)).join(',');

const qa = (a, n = 48) => { const v = Math.round(a * n) / n; return v <= 0 ? 0 : v; };
const qw = (w, n = 4) => clamp(Math.round(w * n), 1, n) / n;

// 角向噪声（确定性；低频团块为主，高频只留一点点）
function azNoise(th, s, amp = 1) {
  return amp * (1 + .34 * Math.sin(th * 2.3 + s) * Math.sin(th * 1.1 - s * .6)
    + .22 * Math.sin(th * 5.1 + s * 2.7)
    + .07 * Math.sin(th * 11.3 + s * 5.1));
}

// 多段柔和衰减（单段线性衰减半径一大就露出「盘边」）
function softDisc(ctx, x, y, r, stops, alpha = 1, op = 'lighter') {
  if (!(r > 0)) return;
  ctx.save(); ctx.globalCompositeOperation = op; ctx.globalAlpha = alpha;
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  for (const [o2, c] of stops) g.addColorStop(o2, c);
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
  ctx.restore();
}
function softLobe(ctx, x, y, rx, ry, rot, stops, alpha = 1, op = 'lighter') {
  ctx.save(); ctx.globalCompositeOperation = op; ctx.globalAlpha = alpha;
  ctx.translate(x, y); ctx.rotate(rot); ctx.scale(rx, ry);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
  for (const [o2, c] of stops) g.addColorStop(o2, c);
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, 1, 0, TAU); ctx.fill(); ctx.restore();
}

// 合批：同 (色, alpha, 线宽) 的成千上万条线/点合并成一个 Path2D 一次描边/填充
class Batch {
  constructor(ctx, op = 'lighter') { this.ctx = ctx; this.op = op; this.map = new Map(); }
  _p(k) { let p = this.map.get(k); if (!p) { p = new Path2D(); this.map.set(k, p); } return p; }
  line(k, x1, y1, x2, y2) { const p = this._p(k); p.moveTo(x1, y1); p.lineTo(x2, y2); }
  quad(k, x1, y1, cx, cy, x2, y2) { const p = this._p(k); p.moveTo(x1, y1); p.quadraticCurveTo(cx, cy, x2, y2); }
  rect(k, x, y, w, h) { this._p(k).rect(x, y, w, h); }
  flush(styler, mode = 'stroke') {
    const ctx = this.ctx; ctx.save(); ctx.globalCompositeOperation = this.op;
    for (const [k, p] of this.map) {
      const s = styler(k); if (!s) continue;
      ctx.strokeStyle = s.color; ctx.fillStyle = s.color;
      ctx.lineWidth = s.width || 1; ctx.globalAlpha = s.alpha ?? 1;
      if (mode === 'fill') ctx.fill(p); else ctx.stroke(p);
    }
    ctx.restore();
  }
}

// ===========================================================================
// 1. 参数旋钮
// ===========================================================================
// 三个给后续微调的旋钮（其余数值是基准原值，非必要不动）：
//   grains —— 颗粒密度：环系尘埃条数。2.6 万 = v3-b 基准原值；性能吃紧时按 0.75 / 0.5 降档（LOD 会自动降）。
//   cap    —— 亮度截顶：光场当「亮度倍率」用时的上限（基准 = 1.0）。
//             调小（如 0.8）= 高光更收、结构更清楚（更「有形」）；调大（1.2）= 更亮更雾（更「透」）。
//   tilt   —— 环倾角（弧度）：环平面相对屏幕的倾角，负值抬起右半环。v3-b 基准 = -0.235。
// 另有两个构图旋钮（scale 只是保险，正常不用动）：
//   scale  —— 整组（核 + 环系）的尺寸倍率。
//   spin   —— 转速倍率（环系剪切速度；1 = 基准，内带约 3.6°/秒）。
export const PARAMS = {
  seed: 8,
  grains: 26000,
  cap: 1.00,
  tilt: -0.235,
  scale: 1,
  spin: 1
};

// 环带 ← 真看板的图谱：作品 / 知识 / 选题 / 草稿 四圈环带，宽度 ∝ 各自的节点数；
// 概念节点数决定最外两圈弥散尘环。（与「形由数据生成」同一条原则）
export function bandsOf(graph) {
  const c = k => graph.nodes.filter(n => n.kind === k).length;
  return [
    {id: 'works', label: '作品', n: Math.max(1, c('post')), color: '#e6c68a', density: .62, rip: 17},
    {id: 'knowledge', label: '知识', n: Math.max(1, c('note')), color: '#f2dcaa', density: .95, rip: 23, sub: [[.30, .030, .80], [.64, .026, .62]]},
    {id: 'ideas', label: '选题', n: Math.max(1, c('idea')), color: '#ffd79a', density: .46, rip: 13},
    {id: 'drafts', label: '草稿', n: Math.max(1, c('draft')), color: '#e2d6a6', density: .34, rip: 9}
  ];
}
export const outerCountOf = graph => graph.nodes.filter(n => n.kind === 'concept').length;

// v3-b 「近侧视 · 能量核」原值（近侧视那套预设的参数）。
// 与 v4.1 的差别：环系压成一条锋利的尘埃线、核是发光的点核 + 星芒（不画米粒镶嵌），
// 外层留一圈极淡的尘环、打开极向喷流。**没有 `solid`** —— 这一项缺席 = 全部走 v3 老路径。
export function makeOptions(params = {}, bands, outerN = 0) {
  const P = {...PARAMS, ...params};
  const BASE_BANDS = () => ([
    {id: 'works', label: '作品', n: 29, color: '#e6c68a', density: .62, rip: 17},
    {id: 'knowledge', label: '知识', n: 82, color: '#f2dcaa', density: .95, rip: 23, sub: [[.30, .030, .80], [.64, .026, .62]]},
    {id: 'ideas', label: '选题', n: 15, color: '#ffd79a', density: .46, rip: 13},
    {id: 'drafts', label: '草稿', n: 4, color: '#e2d6a6', density: .34, rip: 9}
  ]);
  const list = (bands && bands.length ? bands : BASE_BANDS()).map(b => ({...b, density: (b.density ?? .5) * 1.06}));
  const o = {
    ...P,
    name: 'v3b-core-live',
    CX: 0, CY: 0,                      // 由 createCore 填（画布中心）
    tiltRot: P.tilt,
    k: 0.185,
    r0: 120, spanW: 165, gaps: [13, 12, 6],
    bands: list,
    ribbon: (P.ribbon ?? .145), fwd: (P.fwd ?? .95),
    haze: P.haze ?? [[132, 306, .020]],
    // 最外那圈弥散尘环（早先按「概念 40 颗」定；宽度读数仍走 outerN）
    outer: [{r0: 320, r1: 330, a: .034}],
    outerN,
    filament: {r: 398, a: .21, w: 1.0, arcs: [[1.15, .45, 1.1]]},
    rCore: 104, hotSpot: {dx: -.15, dy: -.22},
    corePal: {
      hot: '255,255,253', warm: '255,240,212',
      mid: '238,208,156', deep: '150,140,122',
      halo: '214,226,255', flow: '238,246,255'
    },
    rayPhase: 1.9, rays: 88, spic: 140,
    promAngles: [-2.25, -0.95, 0.55, 2.55],
    // v3-b 的招牌：极向喷流（沿环平面法线甩出去的两束，顺手把 3D 轴讲明白）
    jets: {len: 2.05, spread: .62},
    // —— v3-b 档 ——
    lobes: 4, prom: 5, turb: 940, fibrils: 84,
    flare: .20, flareA: .22, flareR: .48, pointA: .72, pointR: .15,
    laneK: 1.30, veil: .85
  };
  return o;
}

// ===========================================================================
// 2. 环系几何 & 材质
// ===========================================================================
export function makeSys(o) {
  // 倾角可以逐帧改（所以每次读 o.tiltRot，不缓存）
  return {
    pt(R, th) {
      const cr = Math.cos(o.tiltRot), sr = Math.sin(o.tiltRot);
      const u = Math.cos(th) * R, v = Math.sin(th) * R * o.k;
      return {x: o.CX + u * cr - v * sr, y: o.CY + u * sr + v * cr, z: Math.sin(th)};
    },
    axis() { const cr = Math.cos(o.tiltRot), sr = Math.sin(o.tiltRot); return {x: -sr, y: cr}; }
  };
}

// 环带径向密度：软边 + 内侧次缝 + 细沟（ringlet）
function radDens(b, r) {
  const u = (r - b.r0) / b.w;
  if (u < 0 || u > 1) return 0;
  let d = smooth(0, .10, u) * smooth(0, .12, 1 - u);
  if (b.sub) for (const [p, wd, dep] of b.sub) d *= 1 - dep * Math.exp(-((u - p) ** 2) / (2 * wd * wd));
  d *= .76 + .24 * Math.sin(u * b.rip * TAU + b.ripPh);
  return clamp(d, 0, 1.15);
}

// 开普勒剪切：ω ∝ r^-1.5，内带快、外带慢
const omegaAt = (o, r) => .0628 * (o.spin ?? 1) * Math.pow(o.r0 / Math.max(1, r), 1.5);

export function buildRings(o) {
  const sys = makeSys(o);
  const B = o.bands, sum = B.reduce((s, b) => s + b.n, 0);
  // 环带宽度 ∝ 该轴节点数 —— 「形由数据生成」在本版的落点
  let r = o.r0;
  B.forEach((b, i) => {
    const w = o.spanW * b.n / sum;
    b.r0 = r; b.r1 = r + w; b.w = w;
    b.seed = 3.1 + i * 2.7; b.ripPh = (i * 1.9) % TAU;
    b.om = omegaAt(o, (b.r0 + b.r1) / 2);
    r = b.r1;
    if (i < B.length - 1) r += o.gaps[i];
  });
  o.r1 = r;
  o.rEnd = Math.max(o.r1, o.filament ? o.filament.r : 0, ...(o.outer || []).map(L => L.r1));

  const R = rnd(o.seed + 31);
  const area = b => b.r1 * b.r1 - b.r0 * b.r0;
  const wsum = B.reduce((s, b) => s + area(b) * b.density, 0);

  for (const b of B) {
    // 径向 CDF → 按密度分布取半径（比拒采样稳定）
    const N = 48, cdf = [0];
    for (let i = 1; i <= N; i++) cdf.push(cdf[i - 1] + radDens(b, b.r0 + b.w * i / N));
    const inv = u => {
      const t = u * cdf[N]; let i = 1; while (i < N && cdf[i] < t) i++;
      const f = (t - cdf[i - 1]) / Math.max(1e-6, cdf[i] - cdf[i - 1]);
      return b.r0 + b.w * (i - 1 + f) / N;
    };
    const n = Math.round(o.grains * area(b) * b.density / wsum);
    const g = [];
    for (let i = 0; i < n; i++) {
      const th = R() * TAU;
      const rr = inv(R());
      const p = sys.pt(rr, th);
      const litR = 1.18 - .50 * (rr - o.r0) / Math.max(1, o.rEnd - o.r0);   // ② 内侧受光
      const fwd = p.z < 0 ? o.fwd * Math.pow(-p.z, 1.5) : 0;                // ③ 后向散射
      let a = b.density * litR * (1 + fwd) * azNoise(th, b.seed) * (.40 + R() * .95);
      if (o.arcs) a *= 1 + arcBoost(th, o.arcs);
      const w = R();
      // ⑤ 颗粒尺寸偏斜：绝大多数是极细的尘，只有极少数是能看清的「碎块」
      g.push({r: rr, th, a, s: w > .982 ? 1.5 + R() * 1.7 : .45 + w * .55, w});
    }
    // ⑥ 方位向亮弧的**实体**：在指定角窗里补一批同一材质的尘（亮弧 = 这一段更密，不是画一条亮带）
    if (o.arcMarks) for (const M of o.arcMarks) {
      if (M.b !== B.indexOf(b)) continue;
      const n2 = Math.round((M.dens ?? .030) * o.grains * 2.4);
      for (let i = 0; i < n2; i++) {
        const u = R() * 2 - 1;
        if (R() > Math.exp(-(u * u) / (2 * .46 * .46))) continue;   // 角向拒采样 → 云状边缘
        const th = M.th + u * M.w * 1.8;
        const v = (R() + R() + R() - 1.5) / 1.5;                    // 径向钟形密度 → 两条长边也是软的
        const rr = b.r0 + b.w * clamp((M.u ?? .5) + v * (M.wd ?? .45) * 1.5, .03, .97);
        const p = sys.pt(rr, th);
        const litR = 1.18 - .50 * (rr - o.r0) / Math.max(1, o.rEnd - o.r0);
        const fwd = p.z < 0 ? o.fwd * Math.pow(-p.z, 1.5) : 0;
        let a = b.density * litR * (1 + fwd) * azNoise(th, b.seed) * (1.15 + R() * 1.5) * (M.gain ?? 1.9);
        const w = R();
        g.push({r: rr, th, a, s: w > .93 ? 1.7 + R() * 2.3 : .55 + w * .70, w});
      }
    }
    b.grains = g;
  }
  o.sys = sys;

  // —— 尘埃快路径的预处理：每颗尘只留「三角向量 + 桶号」，逐帧不做 sin/cos ——
  // 屏幕位置 = 中心 + c·A + s·B（A/B 已含 r 与倾角；倾角是旋钮，改了要重建）
  const buckets = 24;
  const om0 = omegaAt(o, o.r0), om1 = omegaAt(o, o.rEnd), den = Math.max(1e-9, om0 - om1);
  const cr = Math.cos(o.tiltRot), sr = Math.sin(o.tiltRot);
  for (const b of B) {
    const g = b.grains, n = g.length;
    const packed = new Float32Array(n * 8), aArr = new Float32Array(n), sArr = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const q = g[i];
      const bi = clamp(Math.round((omegaAt(o, q.r) - om1) / den * (buckets - 1)), 0, buckets - 1);
      packed[i * 8] = q.r * cr;             // Ax
      packed[i * 8 + 1] = q.r * sr;         // Ay
      packed[i * 8 + 2] = -q.r * o.k * sr;  // Bx
      packed[i * 8 + 3] = q.r * o.k * cr;   // By
      packed[i * 8 + 4] = Math.cos(q.th);   // c0
      packed[i * 8 + 5] = Math.sin(q.th);   // s0
      packed[i * 8 + 6] = bi;               // 转速桶
      packed[i * 8 + 7] = q.w;              // 随机量（亮颗粒判定 + 色板）
      aArr[i] = q.a; sArr[i] = q.s;
    }
    b.packed = packed; b._a = aArr; b._s = sArr;
    b.om0 = om0; b.omEnd = om1; b.buckets = buckets;
  }
  o._buckets = buckets;
  return o;
}

// 阿刻戎式亮弧：角向某几段密度抬升（自然环系真实存在的非对称结构）
function arcBoost(th, arcs) {
  let s = 0;
  for (const [c, w, amp] of arcs) {
    let d = (th - c) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU;
    s += amp * Math.exp(-(d * d) / (2 * w * w));
  }
  return s;
}

// 光场查表：逐帧几万颗尘逐个算 glowAt（每颗 4 个团块、带 pow）会吃掉几毫秒，
// 所以退化成一次查表 —— 这是 2.6 万颗尘还能满帧的关键。
// v6.4 改口径：查表的自变量改成**核本征空间里的半径**（core.inv 之后取模），不是屏幕距离。
// 核是自转压扁 + 偏心的（热斑在左上），同一屏幕距离上下左右的辉光可以差一倍；
// 按屏幕距离查表会把「核下方那一片」的辉光算大，前环尘埃被压暗过头 —— 落版后核下那道
// 日珥环比基准暗一截就是这么来的。按本征半径查表只多一次开方，误差降一个量级。
function buildGlowLut(o, core) {
  const N = 160, rMax = o.rCore * 3.0, tab = new Float32Array(N + 1);
  for (let i = 0; i <= N; i++) {
    const d = rMax * i / N;
    let s = 0, w = 0;
    for (let a = 0; a < 12; a++) {
      const th = a / 12 * TAU;
      const p = core.fwd(Math.cos(th) * d, Math.sin(th) * d);   // 本征空间 → 屏幕
      s += core.glowAt(p[0], p[1]); w++;
    }
    tab[i] = s / w;
  }
  const step = rMax / N;
  return (x, y) => {
    const [u, v] = core.inv(x, y);
    const d = Math.sqrt(u * u + v * v);
    if (d >= rMax) return 0;
    const f = d / step, i = f | 0, t = f - i;
    return tab[i] + (tab[i + 1] - tab[i]) * t;
  };
}

// 环系总览（一帧里被调两次：远半环在核之后、近半环画在最后）
export function drawRings(ctx, o, pass, core) {
  const sys = o.sys, gl = o._lut || (o._lut = buildGlowLut(o, core));
  const tk = o.anim ? o.anim.t : 0;

  // —— 环平面弥散尘雾（把环系连成一个「盘」，而不是几条画上去的线）——
  if (o.haze) for (const [R0, R1, a] of o.haze) {
    const wd = R1 - R0, rm = (R0 + R1) / 2, N = 180;
    const hb = new Batch(ctx, 'lighter');
    for (let i = 0; i < N; i++) {
      const th0 = (i / N) * TAU, th1 = ((i + 1.2) / N) * TAU, thm = (th0 + th1) / 2;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const p0 = sys.pt(rm, th0), p1 = sys.pt(rm, th1);
      const near = (p0.z + 1) / 2;
      hb.line(`a${qa(a * (.5 + near * .8))}`, p0.x, p0.y, p1.x, p1.y);
    }
    hb.flush(k => ({color: 'rgba(185,143,60,1)', alpha: +k.slice(1), width: wd}));
  }

  for (const bd of o.bands) {
    const om = bd.om ?? omegaAt(o, (bd.r0 + bd.r1) / 2);
    const dth = om * tk * (o.anim ? 1 : 0);          // 整条环带的自转（剪切由 bd.om 承担）
    // ① 环带本体：一段段画，逐段给 alpha（深度 + 内侧受光 + 后向散射 + 角向噪声）
    const rb = new Batch(ctx, 'lighter');
    const N = 200;
    for (let i = 0; i < N; i++) {
      const th0 = (i / N) * TAU + dth, th1 = ((i + 1.15) / N) * TAU + dth, thm = (th0 + th1) / 2;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const rm = (bd.r0 + bd.r1) / 2;
      const p0 = sys.pt(rm, th0), p1 = sys.pt(rm, th1);
      const fwd = p0.z < 0 ? o.fwd * Math.pow(-p0.z, 1.5) : 0;
      const dens = radDens(bd, rm) * (.72 + .28 * azNoise(thm, bd.seed));
      let a = o.ribbon * bd.density * dens * (1 + fwd) * (.55 + .45 * (p0.z + 1) / 2);
      if (o.arcs) a *= 1 + arcBoost(thm, o.arcs);
      // v6.4：环带本体（几百段）改回**精确光场**——它是压在核上的那条「前环遮挡」，
      // 差一点点就会把核上那些细结构（日珥环、星芒）的明暗改掉；径向查表只留给 2.6 万颗尘埃
      if (pass === 'near') a *= 1 - o.veil * clamp(core.glowAt((p0.x + p1.x) / 2, (p0.y + p1.y) / 2), 0, 1.05);
      rb.line(`a${qa(a, 80)}`, p0.x, p0.y, p1.x, p1.y);
    }
    rb.flush(k => ({color: `rgba(${hex(bd.color)},1)`, alpha: +k.slice(1), width: bd.w * .94}));

    // ② 内缘受光：离光源最近的边先亮（一条 1.5px 的亮线）
    const ir = new Batch(ctx, 'lighter');
    for (let i = 0; i < 240; i++) {
      const th0 = (i / 240) * TAU + dth, th1 = ((i + 1.3) / 240) * TAU + dth, thm = (th0 + th1) / 2;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const p0 = sys.pt(bd.r0 + .6, th0), p1 = sys.pt(bd.r0 + .6, th1);
      let a = .30 * bd.density * (.5 + .5 * azNoise(thm, bd.seed));
      if (p0.z < 0) a *= 1 + o.fwd * .7 * Math.pow(-p0.z, 1.5);
      if (pass === 'near') a *= 1 - .88 * clamp(core.glowAt((p0.x + p1.x) / 2, (p0.y + p1.y) / 2), 0, 1.05);
      ir.line(`a${qa(a, 56)}`, p0.x, p0.y, p1.x, p1.y);
    }
    ir.flush(k => ({color: 'rgba(255,242,205,1)', alpha: +k.slice(1), width: 1.5}));
  }

  // ③ 尘埃颗粒（快路径：桶内插值的 cos/sin + 光场查表 + alpha 量化合批）
  for (const bd of o.bands) {
    const pk = bd.packed; if (!pk || !pk.length) continue;
    const nb = o._buckets || 24;
    const cosb = o._cb || (o._cb = new Float64Array(nb)), sinb = o._sb || (o._sb = new Float64Array(nb));
    for (let i = 0; i < nb; i++) {
      const om2 = bd.omEnd + (bd.om0 - bd.omEnd) * i / (nb - 1);
      const a2 = om2 * tk * (o.anim ? 1 : 0);
      cosb[i] = Math.cos(a2); sinb[i] = Math.sin(a2);
    }
    const pal = [bd.color, '#fff2cd', '#ffd79a', '#fff6e4'];
    const gr = new Batch(ctx, 'lighter');
    const basA = bd._a, sizeA = bd._s, hh = .42 + .58 * o.k;
    for (let i = 0, j = 0; i < pk.length; i += 8, j++) {
      const Ax = pk[i], Ay = pk[i + 1], Bx = pk[i + 2], By = pk[i + 3], c0 = pk[i + 4], s0 = pk[i + 5], bi = pk[i + 6];
      const cd = cosb[bi], sd = sinb[bi];
      const c = c0 * cd - s0 * sd, s = s0 * cd + c0 * sd;
      const x = o.CX + c * Ax + s * Bx, y = o.CY + c * Ay + s * By;
      const z = s;
      if ((z > 0) !== (pass === 'near')) continue;
      // ② 核的亮度决定颗粒的可见度：前面的一段被自身的光压住，后面的一段被核体挡住
      let a = basA[j] * (pass === 'near' ? .17 : .21) * (1 - (z > 0 ? .93 : .96) * clamp(gl(x, y), 0, 1.05));
      const w = pk[i + 7];
      if (w > .982) a *= 2.6;                                            // 极少数亮颗粒
      const ci = w > .9 ? 3 : w > .62 ? 2 : w > .3 ? 1 : 0;
      const sz = sizeA[j];
      gr.rect(`c${ci}a${qa(a, 64)}`, x, y, sz, sz * hh);
    }
    gr.flush(k => ({color: `rgba(${hex(pal[+k.slice(1, k.indexOf('a'))])},1)`, alpha: +k.slice(k.indexOf('a') + 1)}), 'fill');
  }

  // ④ 最外侧的细丝环（F 环质感：细、波状、带几段亮弧）—— 外缘不是一个干净的椭圆
  if (o.filament) {
    const F = o.filament;
    const dfl = omegaAt(o, F.r) * tk * (o.anim ? 1 : 0);
    const fb = new Batch(ctx, 'lighter');
    for (let i = 0; i < 420; i++) {
      const th0 = (i / 420) * TAU, th1 = ((i + 1.4) / 420) * TAU, thm = (th0 + th1) / 2;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const t0 = th0 + dfl, t1 = th1 + dfl;
      const p0 = sys.pt(F.r + Math.sin(t0 * 5.1 + 1.2) * 3.4 + Math.sin(t0 * 11.7) * 1.6, t0);
      const p1 = sys.pt(F.r + Math.sin(t1 * 5.1 + 1.2) * 3.4 + Math.sin(t1 * 11.7) * 1.6, t1);
      let a = F.a * (.35 + .65 * azNoise(thm + dfl, 9.4)) * (.5 + .5 * (p0.z + 1) / 2);
      if (p0.z < 0) a *= 1 + o.fwd * Math.pow(-p0.z, 1.5);
      if (F.arcs) a *= 1 + arcBoost(thm + dfl, F.arcs);
      fb.line(`a${qa(a, 56)}`, p0.x, p0.y, p1.x, p1.y);
    }
    fb.flush(k => ({color: 'rgba(255,242,205,1)', alpha: +k.slice(1), width: F.w || 1.1}));
  }

  // ⑤ 最外的弥散尘环（概念 40）：宽、淡、被几个缝切成 3 条亚带
  if (o.outer) for (const L of o.outer) {
    const dlo = omegaAt(o, (L.r0 + L.r1) / 2) * tk * (o.anim ? 1 : 0);
    const ob = new Batch(ctx, 'lighter');
    const rm = (L.r0 + L.r1) / 2, N = 170;
    for (let i = 0; i < N; i++) {
      const th0 = (i / N) * TAU + dlo, th1 = ((i + 1.2) / N) * TAU + dlo, thm = (th0 + th1) / 2;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const p0 = sys.pt(rm, th0), p1 = sys.pt(rm, th1);
      const fwd = p0.z < 0 ? o.fwd * Math.pow(-p0.z, 1.5) : 0;
      let a = L.a * (.55 + .45 * azNoise(thm, 4.4)) * (1 + fwd) * (.45 + .55 * (p0.z + 1) / 2);
      if (o.arcs) a *= 1 + arcBoost(thm, o.arcs) * .5;
      ob.line(`a${qa(a, 56)}`, p0.x, p0.y, p1.x, p1.y);
    }
    ob.flush(k => ({color: 'rgba(185,143,60,1)', alpha: +k.slice(1), width: L.r1 - L.r0}));
  }

  // ⑥ 方位向亮弧：按角度分成 6 个不重叠窗口，每窗口内 alpha 恒定 = 一个合批桶（读数可预测）
  if (o.arcMarks) for (const M of o.arcMarks) {
    const bd = o.bands[M.b];
    if (!bd) continue;
    const dm = (bd.om ?? 0) * tk * (o.anim ? 1 : 0);
    const rm = bd.r0 + bd.w * (M.u ?? .50), wdt = bd.w * (M.wd ?? .45);
    const R0 = rnd(9100 + M.b * 37 + Math.round(M.th * 100));
    const WINS = [[0, .08, 1.00], [.08, .16, .82], [.16, .25, .60], [.25, .34, .38], [.34, .42, .19], [.42, .52, .07]];
    for (const [t0, t1, rel] of WINS) {
      const ab = new Batch(ctx, 'lighter');
      const N = 22;
      let any = 0;
      for (const sgn of [-1, 1]) for (let i = 0; i < N; i++) {
        const t = t0 + (t1 - t0) * (i + .5) / N;
        const thm = M.th + sgn * t * M.w * 1.45 + dm;
        if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
        const jr = rm + Math.sin(thm * 6.7 + 1.3) * bd.w * .09;
        const p0 = sys.pt(jr, thm - .015), p1 = sys.pt(jr, thm + .015);
        const gl2 = clamp(core.glowAt((p0.x + p1.x) / 2, (p0.y + p1.y) / 2), 0, 1.05);
        const a = M.a * rel * (1 - .90 * gl2);
        if (a < .012) continue;
        ab.line(`a${qa(a, 8)}`, p0.x, p0.y, p1.x, p1.y);
        any++;
      }
      if (any) ab.flush(k => ({color: 'rgba(255,242,205,1)', alpha: +k.slice(1), width: wdt}));
    }
    // 弧上再点几颗亮碎块：让弧「有颗粒」，不是一条发光线
    const ck = new Batch(ctx, 'lighter');
    for (let i = 0; i < 70; i++) {
      const u = R0() * 2 - 1;
      const thm = M.th + u * M.w * 1.35 + dm;
      if ((Math.sin(thm) > 0) !== (pass === 'near')) continue;
      const jr = rm + Math.sin(thm * 6.7 + 1.3) * bd.w * .09 + (R0() - .5) * bd.w * .85;
      const p = sys.pt(jr, thm);
      const a = M.a * .85 * Math.exp(-(u * u) / (2 * .32 * .32)) * (.3 + R0() * .8);
      if (a < .03) continue;
      const s = 1.1 + R0() * 1.4;
      ck.rect(`a${qa(a, 48)}`, p.x, p.y, s, s * (.42 + .58 * o.k));
    }
    ck.flush(k => ({color: 'rgba(255,242,205,1)', alpha: +k.slice(1)}), 'fill');
  }
}

// ① 吸收：前环在核上的暗带（径向剖面贴着环带自己的密度剖面走 → 缝处漏光）
export function drawLane(ctx, o, core) {
  const sys = o.sys;
  const LB = o.anim ? 1 + (o.solid?.laneAmp ?? .22) * Math.sin(o.anim.w * o.anim.t) : 1;
  for (const bd of o.bands) {
    const nL = clamp(Math.round(bd.w / 8.5), 3, 13), dw = bd.w / nL * 1.18;
    for (let li = 0; li < nL; li++) {
      const u = (li + .5) / nL, rm = bd.r0 + bd.w * u;
      const dens = radDens(bd, rm);
      if (dens < .04) continue;
      const L = new Batch(ctx, 'source-over');
      const N = 300;
      for (let i = 0; i < N; i++) {
        const th0 = (i / N) * TAU, th1 = ((i + 1.2) / N) * TAU, thm = (th0 + th1) / 2;
        if (Math.sin(thm) <= 0) continue;                       // 只有近侧半环会挡在核前面
        const p0 = sys.pt(rm, th0), p1 = sys.pt(rm, th1);
        const g = core.glowAt((p0.x + p1.x) / 2, (p0.y + p1.y) / 2);
        if (g < .012) continue;
        let a = o.laneK * LB * bd.density * clamp(g, 0, 1.15) * Math.pow(dens, .75);
        a *= .72 + .28 * azNoise(thm, bd.seed);
        if (a < .006) continue;
        L.line(`a${qa(a, 64)}`, p0.x, p0.y, p1.x, p1.y);
      }
      L.flush(k => ({color: 'rgba(24,11,6,1)', alpha: +k.slice(1), width: dw}));
    }
    // 半影：一圈更宽更淡的肩（面光源的影子不该有硬边）
    const S = new Batch(ctx, 'source-over');
    const rm0 = (bd.r0 + bd.r1) / 2;
    for (let i = 0; i < 220; i++) {
      const th0 = (i / 220) * TAU, th1 = ((i + 1.3) / 220) * TAU, thm = (th0 + th1) / 2;
      if (Math.sin(thm) <= 0) continue;
      const p0 = sys.pt(rm0, th0), p1 = sys.pt(rm0, th1);
      const g = core.glowAt((p0.x + p1.x) / 2, (p0.y + p1.y) / 2);
      let a = .16 * bd.density * clamp(g, 0, 1.2) * (.4 + .6 * radDens(bd, rm0));
      if (a < .006) continue;
      S.line(`a${qa(a, 64)}`, p0.x, p0.y, p1.x, p1.y);
    }
    S.flush(k => ({color: 'rgba(28,14,8,1)', alpha: +k.slice(1), width: bd.w * 1.9}));
  }
}

// ===========================================================================
// 3. 星核
// ===========================================================================
export function buildCore(o) {
  const R = rnd(o.seed);
  const core = {x: o.CX, y: o.CY, r: o.rCore, lobes: []};
  const n = o.lobes ?? 5;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * TAU + (i === 0 ? 0 : (R() - .5) * 1.1);
    const d = (i === 0 ? .06 : .16 + R() * .34) * o.rCore;
    core.lobes.push({
      x: o.CX + Math.cos(a) * d, y: o.CY + Math.sin(a) * d * .80,
      rx: o.rCore * (i === 0 ? .78 : .46 + R() * .38),
      ry: o.rCore * (i === 0 ? .74 : .42 + R() * .34),
      rot: R() * TAU, k: i === 0 ? 1.05 : .50 + R() * .48
    });
  }
  // 亮部偏心：最热的一团不在正中 —— 「不读成一颗球」的第一道保险
  const hs = o.hotSpot || {dx: -.20, dy: -.24};
  core.hot = {x: o.CX + hs.dx * o.rCore, y: o.CY + hs.dy * o.rCore};
  // 主保险：星核是自转压扁的（赤道隆起）；环、核、喷流共用同一根轴
  const ob = o.oblate || {sx: 1.13, sy: .87};
  const rot = o.tiltRot; core.rot = rot; core.sx = ob.sx; core.sy = ob.sy;
  const cr = Math.cos(rot), sr = Math.sin(rot);
  core.inv = (x, y) => {
    const dx = x - core.x, dy = y - core.y;
    const u = dx * cr + dy * sr, v = -dx * sr + dy * cr;
    return [u / ob.sx, v / ob.sy];
  };
  core.fwd = (x, y) => {
    const u = x * ob.sx, v = y * ob.sy;
    return [core.x + u * cr - v * sr, core.y + u * sr + v * cr];
  };
  const hS = core.fwd(core.hot.x - core.x, core.hot.y - core.y);
  core.hotS = {x: hS[0], y: hS[1]};
  const S = o.solid;
  core.gw = S ? (S.gw ?? 1.30) : 1.30;
  core.gp = S ? (S.gp ?? 2.40) : 2.40;
  core.glowAt = (X, Y) => {
    const [x, y] = core.inv(X, Y);
    let s = 0;
    for (const L of core.lobes) {
      const dx = (x - L.x) / (L.rx * core.gw), dy = (y - L.y) / (L.ry * core.gw);
      const t = Math.sqrt(dx * dx + dy * dy);
      if (t < 1) s += L.k * Math.pow(1 - t, core.gp);
    }
    return s;
  };
  core.R = R;
  return core;
}

function inCoreSpace(ctx, core, fn) {
  ctx.save();
  ctx.translate(core.x, core.y); ctx.rotate(core.rot); ctx.scale(core.sx, core.sy);
  ctx.rotate(-core.rot); ctx.translate(-core.x, -core.y);
  fn();
  ctx.restore();
}

// 光场截顶：光场当「亮度倍率」用时必须截顶，否则加色叠起来会把核烧成一片白
const mkG1 = o => g => (g > (o.cap ?? 1) ? (o.cap ?? 1) : g);

// 软点图章（米粒组织用；drawImage 比每次新建渐变快一个数量级）
const STAMPS = {};
function stamp(rgb, tight) {
  const key = tight ? rgb + '|t' : rgb;
  if (STAMPS[key]) return STAMPS[key];
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  const rg = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  if (tight) {
    rg.addColorStop(0, `rgba(${rgb},1)`); rg.addColorStop(.62, `rgba(${rgb},.95)`);
    rg.addColorStop(.82, `rgba(${rgb},.52)`); rg.addColorStop(.93, `rgba(${rgb},.16)`);
    rg.addColorStop(1, `rgba(${rgb},0)`);
  } else {
    rg.addColorStop(0, `rgba(${rgb},1)`); rg.addColorStop(.32, `rgba(${rgb},.58)`);
    rg.addColorStop(.66, `rgba(${rgb},.18)`); rg.addColorStop(1, `rgba(${rgb},0)`);
  }
  g.fillStyle = rg; g.fillRect(0, 0, 64, 64);
  STAMPS[key] = c; return c;
}

// 核体团块的纵向剖面：前 1/3 半径是「高原」，内侧实、边缘散
// （实体感来自内侧饱和度，不来自加一条硬边 —— 硬边会读成球）
function bodyStops(P, k, S) {
  if (!S) return [
    [0, `rgba(${P.hot},${clamp(.80 * k, 0, .94)})`],
    [.16, `rgba(${P.warm},${.60 * k})`],
    [.38, `rgba(${P.mid},${.38 * k})`],
    [.62, `rgba(${P.deep},${.12 * k})`],
    [.84, `rgba(${P.deep},${.035 * k})`],
    [1, `rgba(${P.deep},0)`]
  ];
  const b = (S.bulk ?? 1) * k, c = v => clamp(v * b, 0, .97);
  return [
    [0, `rgba(${P.hot},${c(.88)})`],
    [.14, `rgba(${P.warm},${c(.76)})`],
    [.32, `rgba(${P.midW ?? P.warm},${c(.60)})`],
    [.52, `rgba(${P.mid},${c(.42)})`],
    [.74, `rgba(${P.deep},${c(.17)})`],
    [.90, `rgba(${P.deep},${c(.05)})`],
    [1, `rgba(${P.deep},0)`]
  ];
}

// 最热点 + 泛光（全画面唯一纯白的来源）；v4.1 允许垫在结构层下面
function hotDiscs(ctx, o, core, BR) {
  const P = o.corePal, r = core.r;
  const hr = o.hotR ?? .34, ha = o.hotA ?? .32;
  softDisc(ctx, core.hot.x, core.hot.y, r * hr,
    [[0, `rgba(255,255,252,${ha * BR})`], [.38, `rgba(255,246,222,${.13 * ha / .32})`], [1, 'rgba(255,236,196,0)']]);
  softDisc(ctx, core.hot.x, core.hot.y, r * (o.flareR ?? .62), [
    [0, `rgba(255,253,248,${(o.flareA ?? .55) * BR})`], [.18, `rgba(255,244,216,${(o.flareA ?? .55) * .42})`],
    [.5, `rgba(${P.warm},.10)`], [1, `rgba(${P.warm},0)`]]);
}

function drawCoreBody(ctx, o, core) {
  const P = o.corePal, r = core.r, R = core.R, S = o.solid;
  const BRB = o.anim ? 1 + (S?.brAmp2 ?? .12) * Math.sin(o.anim.w * o.anim.t) : 1;
  const sk = S ? (S.shellK ?? 1) : 1, sa = (S ? (S.shellA ?? 1) : 1) * BRB;
  const shells = [
    {n: 3, k: 2.55 * sk, off: 1.00, a: .038 * sa, rot: 1.1},
    {n: 3, k: 1.70 * sk, off: .56, a: .060 * sa, rot: 2.4},
    {n: 3, k: 1.24 * sk, off: .34, a: .082 * sa, rot: 4.0}
  ];
  for (const S2 of shells) {
    for (let i = 0; i < S2.n; i++) {
      const a = S2.rot + i * (TAU / S2.n) + (R() - .5) * .55;
      softLobe(ctx, core.x + Math.cos(a) * r * S2.off, core.y + Math.sin(a) * r * S2.off * .74,
        r * S2.k * (.74 + R() * .58), r * S2.k * (.58 + R() * .46), a * .8,
        [[0, `rgba(${P.halo},${S2.a * (.75 + R() * .6)})`],
          [.30, `rgba(${P.halo},${S2.a * .44})`],
          [.62, `rgba(${P.mid},${S2.a * .20})`],
          [.84, `rgba(${P.deep},${S2.a * .08})`],
          [1, `rgba(${P.deep},0)`]], 1, 'lighter');
    }
  }
  // 核本体：4–8 个团块（source-over）—— 形状是「团」，不是「球」
  for (const L of core.lobes) {
    softLobe(ctx, L.x, L.y, L.rx, L.ry, L.rot, bodyStops(P, L.k, S), 1, 'source-over');
  }
  // v4.1：高光垫层 —— 先画最热点 + 泛光，再让米粒（亮细胞 / 暗缝）与暗涡压上去
  if (S && S.hotUnder) {
    const BR = o.anim ? 1 + (S.brAmp ?? .07) * Math.sin(o.anim.w * o.anim.t) : 1;
    hotDiscs(ctx, o, core, BR);
  }
  if (S) { drawGranulation(ctx, o, core, S, BRB); return; }
  const cell = stamp(P.flow), dark = stamp('14,10,8');
  ctx.save();
  for (let i = 0; i < (o.gran ?? 520); i++) {
    const th = R() * TAU, rr = r * (.18 + Math.pow(R(), .58) * .88);
    const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * .90;
    const s = r * (.070 + R() * .120);
    const g = core.glowAt(x, y);
    const bright = R() < .56;
    ctx.globalAlpha = (bright ? .09 + .17 * R() : .07 + .15 * R()) * (.3 + g * .9);
    ctx.globalCompositeOperation = bright ? 'lighter' : 'source-over';
    ctx.drawImage(bright ? cell : dark, x - s, y - s, s * 2, s * 2);
  }
  ctx.restore();
}

// 两级米粒（大细胞给「团块状的表面起伏」，小细胞给「颗粒感」）+ 暗缝（按亮细胞边界生成）
function granCells(o, core, S) {
  if (o._cells) return o._cells;
  const R = core.R, r = core.r, out = [];
  const P0 = S.granP ?? [.58, .52];
  const passes = [
    {n: S.granBig[0], s0: S.granBig[1], s1: S.granBig[2], a0: S.granBig[3], a1: S.granBig[4], p: P0[0], om: 1.00},
    {n: S.granSmall[0], s0: S.granSmall[1], s1: S.granSmall[2], a0: S.granSmall[3], a1: S.granSmall[4], p: P0[1], om: 1.55}
  ];
  for (let pi = 0; pi < passes.length; pi++) {
    const P = passes[pi];
    for (let i = 0; i < P.n; i++) {
      const th = R() * TAU, u = Math.pow(R(), .55);
      out.push({
        th, u, s: r * (P.s0 + R() * (P.s1 - P.s0)),
        a: P.a0 + R() * (P.a1 - P.a0), bright: R() < P.p, pi,
        om: P.om * (.35 + R() * 1.3), ph: R() * TAU, jit: (R() - .5) * .16
      });
    }
  }
  if (S.granLane) {
    const L = S.granLane, par = out.filter(c => c.bright);
    for (let i = 0; i < L[0] && par.length; i++) {
      const q = par[(R() * par.length) | 0];
      const s = Math.max(r * L[1], q.s * L[2]);
      const rr = .16 + q.u * .92;
      const off = (q.s / r) * (.85 + R() * .55);
      const ang = R() * TAU;
      out.push({
        th: q.th + (Math.cos(ang) * off) / Math.max(.22, rr),
        u: clamp(q.u + (Math.sin(ang) * off) / .92, 0, 1),
        s, a: L[3] + R() * (L[4] - L[3]), bright: false,
        om: .80 * (.35 + R() * 1.3), ph: R() * TAU, jit: (R() - .5) * .16
      });
    }
  }
  o._cells = out;
  return out;
}

function clipHalf(poly, nx, ny, px, py) {
  const out = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const fa = (a.x - px) * nx + (a.y - py) * ny, fb = (b.x - px) * nx + (b.y - py) * ny;
    if (fa <= 0) out.push(a);
    if ((fa < 0 && fb > 0) || (fa > 0 && fb < 0)) {
      const t = fa / (fa - fb);
      out.push({x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t});
    }
  }
  return out;
}
function chaikin(poly) {
  const out = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    out.push({x: a.x * .75 + b.x * .25, y: a.y * .75 + b.y * .25});
    out.push({x: a.x * .25 + b.x * .75, y: a.y * .25 + b.y * .75});
  }
  return out;
}

// —— Voronoi 米粒镶嵌（v4.1 的结构主力）——
// 圆形细胞怎么调都读成泡泡；真实米粒组织是「铺满的分格」：格子共边、缝是共边的细线、每格亮度不同。
function granMosaic(o, core, S) {
  if (o._mosaic) return o._mosaic;
  const M = S.granMosaic, r = core.r, R = core.R;
  const n = M[0], sq = M[1] ?? .90;
  const sites = [];
  const minD = Math.sqrt(Math.PI * r * r * sq / n) * (M[13] ?? .58);
  for (let i = 0, guard = 0; i < n && guard < n * 40; guard++) {
    const th = R() * TAU, u = Math.pow(R(), .52);
    const rr = r * (.06 + u * .96);
    const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * sq;
    let ok = true;
    for (const s of sites) { const dx = s.x - x, dy = s.y - y; if (dx * dx + dy * dy < minD * minD) { ok = false; break; } }
    if (!ok) continue;
    sites.push({x, y, v: (R() - .5) * 2});
    i++;
  }
  // 圆盘边界（24 条边，半径按方位抖动 → 临边不是一个正椭圆）
  const disc = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * TAU, nx = Math.cos(a), ny = Math.sin(a) / sq, nn = nx * nx + ny * ny;
    const R2 = r * (M[11] ?? 1) * (1 + .045 * Math.sin(a * 5 + 1.2) + .03 * Math.sin(a * 9 - .4));
    disc.push({nx, ny, px: core.x + nx * R2 / nn, py: core.y + ny * R2 / nn});
  }
  const K = M[2] ?? 7;
  const out = [];
  for (let i = 0; i < sites.length; i++) {
    const A = sites[i];
    const nb = [];
    for (let j = 0; j < sites.length; j++) {
      if (j === i) continue;
      const dx = sites[j].x - A.x, dy = sites[j].y - A.y, d2 = dx * dx + dy * dy;
      if (d2 > 1e-6) nb.push({j, d2});
    }
    nb.sort((p, q) => p.d2 - q.d2);
    let poly = [{x: A.x - r * 2, y: A.y - r * 2}, {x: A.x + r * 2, y: A.y - r * 2},
      {x: A.x + r * 2, y: A.y + r * 2}, {x: A.x - r * 2, y: A.y + r * 2}];
    for (let k = 0; k < Math.min(K, nb.length); k++) {
      const B = sites[nb[k].j];
      poly = clipHalf(poly, B.x - A.x, B.y - A.y, (A.x + B.x) / 2, (A.y + B.y) / 2);
      if (poly.length < 3) break;
    }
    if (poly.length < 3) continue;
    for (const e of disc) { poly = clipHalf(poly, e.nx, e.ny, e.px, e.py); if (poly.length < 3) break; }
    if (poly.length < 3) continue;
    const rc = Math.sqrt(Math.PI * r * r * sq / n) * 1.45;
    for (let i2 = 0; i2 < 8; i2++) {
      const a2 = (i2 / 8) * TAU;
      poly = clipHalf(poly, Math.cos(a2), Math.sin(a2), A.x + Math.cos(a2) * rc, A.y + Math.sin(a2) * rc);
      if (poly.length < 3) break;
    }
    if (poly.length < 3) continue;
    let cx = 0, cy = 0;
    for (const p of poly) { cx += p.x; cy += p.y; }
    cx /= poly.length; cy /= poly.length;
    // 圆角：直边 + 尖角会读成「冰裂纹 / 碎瓷」；Chaikin 切角 + 从质心放大 5% 补回收缩
    poly = chaikin(poly);
    const sc = M[12] ?? 1.05;
    for (const p of poly) { p.x = cx + (p.x - cx) * sc; p.y = cy + (p.y - cy) * sc; }
    out.push({poly, cx, cy, v: A.v, g: core.glowAt(cx, cy)});
  }
  o._mosaic = out;
  return out;
}

// 把格子亮度映射到颜色：暗格偏深褐、亮格偏暖白（同一组暖色，零新色相）
function mosaicCol(P, k) {
  const t = clamp(k, 0, 1.35) / 1.35;
  const mix = (a, b) => Math.round(a + (b - a) * t);
  const lo = P.deep.split(',').map(Number), hi = P.flow.split(',').map(Number);
  return `${mix(lo[0], hi[0])},${mix(lo[1], hi[1])},${mix(lo[2], hi[2])}`;
}

function granNet(o, core, S) {
  if (o._net) return o._net;
  const G = S.granNet, r = core.r, R = core.R;
  const sites = granCells(o, core, S).filter(c => c.pi === (G[0] ?? 0)).map(c => {
    const th = c.th + c.jit, rr = r * (.16 + c.u * .92);
    return {x: core.x + Math.cos(th) * rr, y: core.y + Math.sin(th) * rr * .90, s: c.s};
  });
  const out = [];
  const K = G[1] ?? 6;
  for (let i = 0; i < sites.length; i++) {
    const A = sites[i];
    const nb = [];
    for (let j = 0; j < sites.length; j++) {
      if (j === i) continue;
      const dx = sites[j].x - A.x, dy = sites[j].y - A.y, d2 = dx * dx + dy * dy;
      if (d2 < 1e-6) continue;
      nb.push({j, d2, d: Math.sqrt(d2), dx, dy});
    }
    nb.sort((a, b) => a.d2 - b.d2);
    const near = nb.slice(0, K);
    const dRef = near.length ? near[Math.min(2, near.length - 1)].d : 0;
    for (const q of near) {
      if (q.j < i) continue;
      if (q.d > dRef * 1.85) continue;
      const mx = (A.x + sites[q.j].x) / 2, my = (A.y + sites[q.j].y) / 2;
      const ux = q.dx / q.d, uy = q.dy / q.d;
      const hl = q.d * .42 * (.75 + R() * .55);
      const jx = (R() - .5) * q.d * .30, jy = (R() - .5) * q.d * .30;
      const gc = core.glowAt(mx, my);
      out.push({
        x1: mx - uy * hl + jx, y1: my + ux * hl + jy,
        x2: mx + uy * hl + jx, y2: my - ux * hl + jy,
        a: (G[2] ?? .30) * (.45 + Math.min(gc, 1) * .80) * (.55 + R() * .70),
        w: .60 + R() * .85, ph: R() * TAU,
        g: (G[0] ?? 0)
      });
    }
  }
  o._net = out;
  return out;
}

function drawGranulation(ctx, o, core, S, BRB = 1) {
  const P = o.corePal, r = core.r;
  const G1 = mkG1(o);
  const cell = stamp(S.granColor ?? P.flow, S.granSharp), dark = stamp('14,10,8', S.granSharp);
  const over = S.granMode === 'over';
  const t = o.anim ? o.anim.t : 0, w = o.anim ? o.anim.w : 1;
  const K = (S.granAmp ?? 1) * BRB;
  const dR = S.granDarkR ?? 1;
  const hd = S.hotDark ?? 0;
  const hb = S.hotBright ?? 0;
  const hR = Math.max(1, r * (o.hotR ?? .30) * 1.45);
  const fr = S.granFore ?? 0;
  // ① 一级米粒：Voronoi 镶嵌，画在**半分辨率离屏**上再放大回主画布加一点 blur ——
  // 直边和硬缝在 1:1 下太锋利（读成龟裂的泥地），糊一道才有有机的过渡
  if (S.granMosaic) {
    const M = S.granMosaic, mos = granMosaic(o, core, S);
    const w2 = M[4] ?? 1.1;
    const at = o.anim ? o.anim.t : 0, aw = o.anim ? o.anim.w : 1;
    const gate = o.anim ? M[3] * (1 + (S.brAmp2 ?? .24) * .55 * Math.sin(aw * at)) : M[3];
    const fbr = o.anim ? (1 + (S.brAmp2 ?? .24) * .55 * Math.sin(aw * at)) : 1;
    const half = M[14] ?? .5, box = r * 2.5, SP = Math.ceil(box * half);
    if (!o._mcv) o._mcv = document.createElement('canvas');
    const mcv = o._mcv;
    if (mcv.width !== SP) { mcv.width = mcv.height = SP; }
    const g2 = mcv.getContext('2d');
    g2.setTransform(1, 0, 0, 1, 0, 0);
    g2.clearRect(0, 0, SP, SP);
    g2.save(); g2.scale(half, half); g2.translate(-(core.x - box / 2), -(core.y - box / 2));
    for (const c of mos) {
      const g = Math.min(c.g, 1.15);
      const sup = Math.sin(c.cx * .011 + 1.7) * Math.sin(c.cy * .013 - .6);   // 低频「超米粒」
      const shim = o.anim ? (M[16] ?? .05) * (Math.sin(aw * at * 2 + c.v * Math.PI) - Math.sin(c.v * Math.PI)) : 0;
      const k = M[5] + g * M[6] + c.v * (M[7] ?? .10) + sup * (M[8] ?? .06) + shim;
      g2.fillStyle = `rgba(${mosaicCol(P, k)},${Math.min(1, (M[9] ?? .62) * fbr)})`;
      g2.beginPath();
      g2.moveTo(c.poly[0].x, c.poly[0].y);
      for (let i = 1; i < c.poly.length; i++) g2.lineTo(c.poly[i].x, c.poly[i].y);
      g2.closePath(); g2.fill();
      // 缝的 alpha 只用光场调制（不掺随机）：一条共边会被左右两格各描一次，统一 = 干净的缝
      g2.strokeStyle = `rgba(20,11,6,${(M[10] ?? .26) * (.72 + g * .55) * gate})`;
      g2.lineWidth = (.62 + (c.v + 1) * .22) * w2;
      g2.stroke();
    }
    g2.restore();
    ctx.save();
    ctx.filter = `blur(${M[15] ?? 1.4}px)`;
    ctx.drawImage(mcv, core.x - box / 2, core.y - box / 2, box, box);
    ctx.restore();
  }
  ctx.save();
  for (const c of granCells(o, core, S)) {
    const dth = c.jit + (o.anim ? .034 * c.om * (Math.sin(w * t + c.ph) - Math.sin(c.ph)) : 0);
    const th = c.th + dth;
    const rr = r * (.16 + c.u * .92);
    const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * .90;
    const g = core.glowAt(x, y);
    const t2 = rr / r;
    const rim = 1 - smooth(.80, 1.04, t2) * (S.granLimb ?? .62);
    let a = c.a * K * ((S.granReach ?? .40) + Math.min(g, 1.00) * (S.granReach ? .70 : .85)) * rim;
    if (o.anim) a *= 1 + .20 * (Math.sin(w * t * 2 + c.ph) - Math.sin(c.ph));
    const hw = (hd || hb) ? (1 - smooth(.30, 1.05, Math.hypot(x - core.hot.x, y - core.hot.y) / hR)) : 0;
    if (c.bright && hb) a *= 1 - hb * hw;
    const dk = c.bright ? 1 : (S.granDark ?? .60) * (hd ? 1 + hd * hw : 1);
    ctx.globalAlpha = clamp(a * (c.bright ? 1 : dk), 0, .70);
    ctx.globalCompositeOperation = (c.bright && !over) ? 'lighter' : 'source-over';
    // 透视压缩：越靠临边的细胞在视线上被压得越扁越密 —— 把「纹理」变成「曲面」最便宜的一步
    const fore = fr ? (1 - fr) + fr * Math.max(.30, Math.sqrt(Math.max(0, 1 - t2 * t2 * .90))) : 1;
    const s = c.s * (c.bright ? 1 : dR) * fore;
    ctx.drawImage(c.bright ? cell : dark, x - s, y - s, s * 2, s * 2);
  }
  ctx.restore();

  // 米粒缝网：暗缝是细线，不是给细胞描边（画 Voronoi 的边，端点抖动 → 一张不规则、有连通性的裂缝网）
  if (S.granNet) {
    const G = S.granNet, net = granNet(o, core, S), nb = new Batch(ctx, 'source-over');
    for (const e of net) {
      const a = e.a * (G[3] ?? 1) * (o.anim ? 1 + .18 * (Math.sin(o.anim.w * o.anim.t + e.ph) - Math.sin(e.ph)) : 1);
      if (a < .02) continue;
      nb.line(`a${qa(a, 48)}w${qw(e.w, 3)}`, e.x1, e.y1, e.x2, e.y2);
    }
    nb.flush(k => ({color: 'rgba(20,11,6,1)', alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 1.55}));
  }

  // 内侧暗涡：几团宽而软的暖褐色（不是细纹）—— 全亮的一坨读成发光体，有暗有亮才读成物质
  if (S.mottle) {
    const R = core.R, MA = S.mottleA ?? .13;
    for (let i = 0; i < S.mottle; i++) {
      const th = R() * TAU, rr = r * (.14 + R() * .54);
      const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * .90;
      const g = core.glowAt(x, y);
      const a = MA * (.45 + Math.min(g, 1.00) * .85) * (.45 + R() * .85);
      softDisc(ctx, x, y, r * (.26 + R() * .34), S.mottleSharp
        ? [[0, `rgba(40,19,8,${a})`], [.38, `rgba(48,24,11,${a * .64})`],
          [.72, `rgba(52,26,12,${a * .20})`], [1, 'rgba(52,26,12,0)']]
        : [[0, `rgba(44,21,9,${a})`], [.45, `rgba(52,26,12,${a * .48})`], [1, 'rgba(52,26,12,0)']]);
    }
  }
}

// —— 等离子：湍流丝 / 暗纤 / 日珥 / 日冕流 / 极向喷流 / 耀斑 ——
function drawCorePlasma(ctx, o, core) {
  const S = o.solid || {};
  const P = o.corePal, R = core.R, r = core.r;
  const G1 = mkG1(o);
  const turb = (o.turb ?? 620) * .62, prom = o.prom ?? 6;
  const PK = S.plasmaK ?? 1;

  // 1) 日冕流（径向细丝，密度偏一侧 → 静态帧也能看出「在动」）
  const cb = new Batch(ctx, 'lighter');
  const nRay = o.rays ?? 74;
  for (let i = 0; i < nRay; i++) {
    const th = R() * TAU;
    const bias = .55 + .45 * Math.sin(th + o.rayPhase) * Math.sin(th * 2.3 - 1.1);
    const len = r * (.35 + R() * .95) * (S.rayLen ?? 1) * (.5 + bias * .75);
    const w = .55 + R() * .95;
    const segs = 4;
    for (let s = 0; s < segs; s++) {
      const r0 = r * .72 + len * (s / segs), r1 = r * .72 + len * ((s + 1) / segs);
      const dr = (R() - .5) * .22;
      const a0 = Math.cos(th + dr) * r0, b0 = Math.sin(th + dr) * r0;
      const a1 = Math.cos(th + dr * 1.6) * r1, b1 = Math.sin(th + dr * 1.6) * r1;
      const gg = core.glowAt(core.x + a0, core.y + b0);
      const a = .056 * PK * (S.rayA ?? 1) * (1 - s / segs * (S.rayDamp ?? .72)) * (.35 + G1(gg) * .9) * (.5 + R() * .9);
      if (a < .01) continue;
      cb.line(`a${qa(a, 64)}w${qw(w, 3)}`, core.x + a0, core.y + b0, core.x + a1, core.y + b1);
    }
  }
  cb.flush(k => ({color: `rgba(${P.halo},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.1}));

  // 2) 表面湍流：切向短丝（太阳色球/针状体的写法）——「表面在翻涌」
  const tb = new Batch(ctx, 'lighter');
  for (let i = 0; i < turb; i++) {
    const th = R() * TAU;
    const rr = r * (.20 + Math.pow(R(), .62) * (S.turbR ?? .92));
    const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * (.86 + R() * .2);
    const g = core.glowAt(x, y);
    if (g < .05) continue;
    const lim = smooth(.55, 1.02, rr / r);
    const len = r * (.05 + R() * .16), tang = th + Math.PI / 2 + (R() - .5) * .7;
    const x2 = x + Math.cos(tang) * len, y2 = y + Math.sin(tang) * len;
    const mx = (x + x2) / 2 + Math.cos(th) * (R() - .5) * 5, my = (y + y2) / 2 + Math.sin(th) * (R() - .5) * 5;
    const a = (.042 + .17 * R() * (.5 + lim) + lim * .085) * PK * (.35 + G1(g) * .8) * (.4 + Math.sin(th * 5 + rr * .2 + i) * .35);
    if (a < .012) continue;
    tb.quad(`a${qa(a, 56)}w${qw(.5 + R() * .8, 3)}`, x, y, mx, my, x2, y2);
  }
  tb.flush(k => ({color: `rgba(${P.flow},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 1.9}));

  // 2a-2) 日冕鬃毛（1.0–1.7·r 那一圈补「结构」而不是「亮度」：从雾变成毛）
  if (S.corona) {
    const co = S.corona, nb = new Batch(ctx, 'lighter');
    for (let i = 0; i < (co.n ?? 200); i++) {
      const th = R() * TAU;
      const bias = .5 + .5 * Math.sin(th * 1.7 + o.rayPhase) * Math.sin(th * 2.9 - .7);
      const r0 = r * (co.r0 ?? 1.02) * (.97 + R() * .10);
      const len = r * (co.len ?? .42) * (.35 + R() * 1.15) * (.45 + bias * .9);
      const wob = (R() - .5) * .34;
      const segs = 3;
      for (let s = 0; s < segs; s++) {
        const u0 = s / segs, u1 = (s + 1) / segs;
        const rr0 = r0 + len * u0, rr1 = r0 + len * u1;
        const t0 = th + wob * u0, t1 = th + wob * u1;
        const a = (co.a ?? .05) * (1 - u0 * (co.damp ?? .55)) * (.30 + R() * 1.15) * (.40 + bias * .95);
        if (a < .012) continue;
        nb.line(`a${qa(a, 64)}w${qw(.5 + R() * .8, 3)}`,
          core.x + Math.cos(t0) * rr0, core.y + Math.sin(t0) * rr0 * .93,
          core.x + Math.cos(t1) * rr1, core.y + Math.sin(t1) * rr1 * .93);
      }
    }
    nb.flush(k => ({color: `rgba(${P.halo},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.0}));
  }

  // 2b) 针状体：临边向外戳出的短刺 —— 让「轮廓不是一个圆」最有效的一步
  const sb = new Batch(ctx, 'lighter');
  for (let i = 0; i < (o.spic ?? 260) * .75; i++) {
    const th = R() * TAU;
    const base = r * (.90 + R() * .14);
    const len = r * (.045 + R() * .17) * (.5 + .5 * Math.sin(th * 2.1 + o.rayPhase));
    const x0 = core.x + Math.cos(th) * base, y0 = core.y + Math.sin(th) * base * .93;
    const x1 = core.x + Math.cos(th) * (base + len), y1 = core.y + Math.sin(th) * (base + len) * .93;
    const a = (.06 + .17 * R()) * (.55 + .45 * Math.sin(th + 1.2) * Math.sin(th * 1.7));
    if (a < .02) continue;
    sb.line(`a${qa(a, 48)}w${qw(.4 + R() * .5, 3)}`, x0, y0, x1, y1);
  }
  sb.flush(k => ({color: `rgba(${P.flow},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 1.5}));

  // 2c) 日冕弧阵：拱脚落在核面上；拱高按 |sin(ψ−轴角)| 调制 → 赤道方向拱最高，两极最短
  //     （环平面 / 磁轴 / 喷流在画面里是同一个方向，3D 立刻立住）
  const ax = o.sys.axis();
  const psiA = Math.atan2(ax.y, ax.x);
  const ab = new Batch(ctx, 'lighter');
  for (let i = 0; i < (o.arcade ?? 24) * .55; i++) {
    const psi = R() * TAU, ch = Math.cos(psi), sh = Math.sin(psi);
    const b = .30 + R() * .95;
    const base = r * (.86 + R() * .14);
    const cb2 = Math.cos(b), sb2 = Math.sin(b);
    const p0 = {x: core.x + base * (ch * cb2 - sh * sb2), y: core.y + base * (sh * cb2 + ch * sb2)};
    const p1 = {x: core.x + base * (ch * cb2 + sh * sb2), y: core.y + base * (sh * cb2 - ch * sb2)};
    const h = r * (.05 + .26 * Math.abs(Math.sin(psi - psiA)) * (.5 + R() * .9));
    const ap = {x: core.x + (base + h) * ch, y: core.y + (base + h) * sh};
    const jx = (R() - .5) * r * .10, jy = (R() - .5) * r * .10;
    const segs = 6;
    let px = p0.x, py = p0.y;
    for (let s = 1; s <= segs; s++) {
      const u = s / segs, m = 1 - u;
      const x = m * m * p0.x + 2 * m * u * (ap.x + jx) + u * u * p1.x;
      const y = m * m * p0.y + 2 * m * u * (ap.y + jy) + u * u * p1.y;
      const a = (.025 + .12 * (1 - Math.sin(u * Math.PI))) * (.4 + .9 * R());
      if (a * PK > .012) ab.line(`a${qa(a * PK, 48)}w${qw(.5 + R() * .9, 3)}`, px, py, x, y);
      px = x; py = y;
    }
  }
  ab.flush(k => ({color: `rgba(${P.mid},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 1.9}));

  // 2d) 临边炽热弧（色球）：一圈断开的亮弧 —— 给「表面」一个可读的边，但因为是碎的，不会退化成白边
  const lb = new Batch(ctx, 'lighter');
  const nArc = Math.round((o.limbArc ?? 13) * (o.solid ? 1.35 : 1));
  for (let i = 0; i < nArc; i++) {
    const th0 = R() * TAU;
    const span = .16 + R() * .55;
    const rr = r * (.90 + R() * .11);
    const segs = 9;
    for (let s = 0; s < segs; s++) {
      const u0 = s / segs, u1 = (s + 1.15) / segs;
      const t0 = th0 + span * u0, t1 = th0 + span * u1;
      const x0 = core.x + Math.cos(t0) * rr, y0 = core.y + Math.sin(t0) * rr * .92;
      const x1 = core.x + Math.cos(t1) * rr, y1 = core.y + Math.sin(t1) * rr * .92;
      let a = (.23 + .27 * R()) * Math.sin(Math.min(1, u1) * Math.PI) * (.5 + R() * .9);
      if (o.solid) a *= (o.limbA ?? 1) * (o.anim ? 1 + .18 * (Math.sin(o.anim.w * o.anim.t + i) - Math.sin(i)) : 1);
      if (a < .03) continue;
      lb.line(`a${qa(a, 48)}w${qw(.8 + R() * 1.1, 3)}`, x0, y0, x1, y1);
    }
  }
  lb.flush(k => ({color: `rgba(${P.flow},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.2}));

  // 3) 暗纤（吸收丝）：核面上短而软的暗纹（不是「划痕」）
  const FA = o.solid ? (o.fibA ?? [.05, .16]) : [.05, .16];
  const db = new Batch(ctx, 'source-over');
  for (let i = 0; i < (o.fibrils ?? 54) * .8; i++) {
    const th = R() * TAU, rr = r * (.22 + R() * .66);
    const x = core.x + Math.cos(th) * rr, y = core.y + Math.sin(th) * rr * .9;
    const g = core.glowAt(x, y);
    const len = r * (.14 + R() * .34), dir = th + Math.PI / 2 + (R() - .5) * .9;
    const x2 = x + Math.cos(dir) * len, y2 = y + Math.sin(dir) * len;
    const mx = (x + x2) / 2 + Math.cos(th) * (R() - .5) * len * 1.5, my = (y + y2) / 2 + Math.sin(th) * (R() - .5) * len * 1.5;
    const a = FA[0] + FA[1] * R() * G1(g);
    if (a < .015) continue;
    db.quad(`a${qa(a, 48)}w${qw(.7 + R() * 1.6, 3)}`, x, y, mx, my, x2, y2);
  }
  db.flush(k => ({color: 'rgba(30,15,8,1)', alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.4}));

  // 4) 日珥：根部在核面上、拱到核外的等离子环（约 1/3 是抛射型 = 不对称 = 在动）
  const pb = new Batch(ctx, 'lighter');
  for (let i = 0; i < prom; i++) {
    const th = o.promAngles ? o.promAngles[i % o.promAngles.length] : R() * TAU;
    const erupt = R() < .34;
    const foot = r * (.88 + R() * .12);
    const a0 = th - (.06 + R() * .14), a1 = th + (.06 + R() * .14);
    const hgt = r * (erupt ? .78 + R() * .72 : .22 + R() * .34);
    const ox = Math.cos(th) * hgt, oy = Math.sin(th) * hgt;
    const p0 = {x: core.x + Math.cos(a0) * foot, y: core.y + Math.sin(a0) * foot * .93};
    const p1 = {x: core.x + Math.cos(a1) * foot, y: core.y + Math.sin(a1) * foot * .93};
    const c1 = {x: core.x + ox * .55 + Math.cos(a0) * foot * .8, y: core.y + oy * .55 + Math.sin(a0) * foot * .8};
    const c2 = {x: core.x + ox * (.9 + R() * .3) + Math.cos(a1) * foot * .5, y: core.y + oy * (.9 + R() * .3) + Math.sin(a1) * foot * .5};
    const segs = 7;
    let px = p0.x, py = p0.y;
    for (let s = 1; s <= segs; s++) {
      const u = s / segs, m = 1 - u;
      const x = m * m * m * p0.x + 3 * m * m * u * c1.x + 3 * m * u * u * c2.x + u * u * u * p1.x;
      const y = m * m * m * p0.y + 3 * m * m * u * c1.y + 3 * m * u * u * c2.y + u * u * u * p1.y;
      const a = (.10 + .34 * Math.sin(u * Math.PI)) * (erupt ? .55 : .80) * (.5 + R() * .7);
      if (a > .015) pb.line(`a${qa(a, 64)}w${qw(.7 + R() * 1.1, 3)}`, px, py, x, y);
      px = x; py = y;
    }
    softDisc(ctx, p0.x, p0.y, r * .10, [[0, `rgba(${P.flow},.30)`], [.4, `rgba(${P.mid},.13)`], [1, `rgba(${P.mid},0)`]]);
  }
  pb.flush(k => ({color: `rgba(${P.warm},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.2}));

  // 5) 极向喷流（v3-b 的招牌）：沿环平面法线甩出去的两束 —— 环、核、喷流共用同一根轴
  if (o.jets) {
    const ax = o.sys.axis(); const jb = new Batch(ctx, 'lighter');
    for (const sgn of [1, -1]) {
      for (let i = 0; i < 46; i++) {
        const u = R();
        const len = r * o.jets.len * (.35 + u * .65);
        const sp = o.jets.spread * (u + .12);
        const dx = ax.x * sgn, dy = ax.y * sgn;
        const th = R() * TAU, rr = r * .16 * Math.sqrt(R());
        const x0 = core.x + dx * r * .16 + Math.cos(th) * rr, y0 = core.y + dy * r * .16 + Math.sin(th) * rr;
        const x1 = core.x + dx * len + Math.cos(th) * rr * 6 * sp, y1 = core.y + dy * len + Math.sin(th) * rr * 6 * sp;
        const a = .105 * (1 - u * .8) * (.4 + R());
        if (a > .012) jb.line(`a${qa(a, 64)}w${qw(.6 + R(), 3)}`, x0, y0, x1, y1);
      }
    }
    jb.flush(k => ({color: `rgba(${P.flow},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.0}));
    softDisc(ctx, core.x + ax.x * r * .9, core.y + ax.y * r * .9, r * .5, [[0, `rgba(${P.halo},.16)`], [1, `rgba(${P.halo},0)`]]);
    softDisc(ctx, core.x - ax.x * r * .9, core.y - ax.y * r * .9, r * .5, [[0, `rgba(${P.halo},.16)`], [1, `rgba(${P.halo},0)`]]);
  }

  // 6) 最热点 + 衍射耀斑（全画面唯一的纯白）
  // v4.1：hotUnder 时最热点 + 泛光已垫在核体与米粒之间，这里只留最上面那个镜面点
  // v3-b：没有 solid → 这里就是最热点 + 泛光那一层（与 core.js 的 v3 路径逐字一致）
  const BR = o.anim ? 1 + (o.solid?.brAmp ?? .07) * Math.sin(o.anim.w * o.anim.t) : 1;
  if (!S.hotUnder) hotDiscs(ctx, o, core, BR);
  softDisc(ctx, core.hot.x, core.hot.y, r * (o.pointR ?? .17), [
    [0, `rgba(255,255,255,${(o.pointA ?? .92) * BR})`], [.30, `rgba(255,250,236,${(o.pointA ?? .92) * .34})`], [1, 'rgba(255,248,230,0)']]);
}

// 6) 衍射耀斑：相机侧的产物，画在屏幕坐标系里，并且画在「近半环」之前（环会把它切断）
export function drawFlare(ctx, o, core) {
  const fl = o.flare ?? 1, r = core.r;
  if (!(fl > 0)) return;
  const hx = core.hotS.x, hy = core.hotS.y;
  ctx.save(); ctx.globalCompositeOperation = 'lighter';
  const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1], [.7, .7], [-.7, -.7], [.7, -.7], [-.7, .7]];
  dirs.forEach(([dx, dy], i) => {
    const len = (i < 2 ? r * 1.45 : i < 4 ? r * .90 : r * .52) * fl * (1 + (i % 3) * .12);
    const g = ctx.createLinearGradient(hx, hy, hx + dx * len, hy + dy * len);
    const a0 = (i < 2 ? .165 : i < 4 ? .085 : .04) * fl;
    g.addColorStop(0, `rgba(255,250,238,${a0})`); g.addColorStop(.35, `rgba(255,236,192,${a0 * .34})`); g.addColorStop(1, 'rgba(255,230,180,0)');
    ctx.strokeStyle = g; ctx.lineWidth = i < 2 ? 2.1 : i < 4 ? 1.5 : 1;
    ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx + dx * len, hy + dy * len); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx - dx * len, hy - dy * len); ctx.stroke();
  });
  ctx.restore();
}

// —— 跨环日珥：两只脚都落在核的临边（环系坐标里的方位角），拱顶越过前环整条带 ——
// v4.1 用 overProm2（新版：拱顶越过前环整条带）；v3（B/C 档）用 overProm 那支老写法。
// 两支都没有 = 不画（v3-b 就属于这一档，它没有跨环日珥）。
export function drawOverRingProminence(ctx, o, core) {
  if (o.overProm2) { inCoreSpace(ctx, core, () => overProm2(ctx, o, core)); return; }
  if (o.overProm) inCoreSpace(ctx, core, () => _overProm(ctx, o, core));
}

// v3 的跨环日珥（三次贝塞尔，拱顶只到 1.9·r）—— 与 core.js 的 _overProm 逐字一致
function _overProm(ctx, o, core) {
  const P = o.corePal, r = core.r;
  const R = core.R;
  const th = o.overProm && o.overProm.th ? o.overProm.th : 1.72;
  const foot = r * 1.0;
  const a0 = th - .16, a1 = th + .13;
  const hgt = r * (o.overProm ? o.overProm.h : 1.9);
  const p0 = {x: core.x + Math.cos(a0) * foot, y: core.y + Math.sin(a0) * foot * .93};
  const p1 = {x: core.x + Math.cos(a1) * foot, y: core.y + Math.sin(a1) * foot * .93};
  const c1 = {x: core.x + Math.cos(th) * hgt * .6 + Math.cos(a0) * foot * .9, y: core.y + Math.sin(th) * hgt * .6 + Math.sin(a0) * foot * .9};
  const c2 = {x: core.x + Math.cos(th) * hgt * 1.15 + Math.cos(a1) * foot * .5, y: core.y + Math.sin(th) * hgt * 1.15 + Math.sin(a1) * foot * .5};
  const b = new Batch(ctx, 'lighter');
  let px = p0.x, py = p0.y;
  for (let s = 1; s <= 9; s++) {
    const u = s / 9, m = 1 - u;
    const x = m ** 3 * p0.x + 3 * m * m * u * c1.x + 3 * m * u * u * c2.x + u ** 3 * p1.x;
    const y = m ** 3 * p0.y + 3 * m * m * u * c1.y + 3 * m * u * u * c2.y + u ** 3 * p1.y;
    const a = (.08 + .30 * Math.sin(u * Math.PI)) * (.6 + R() * .7);
    b.line(`a${qa(a, 64)}w${qw(.8 + R() * 1.4, 3)}`, px, py, x, y);
    px = x; py = y;
  }
  b.flush(k => ({color: `rgba(${P.flow},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.3}));
  softDisc(ctx, p0.x, p0.y, r * .12, [[0, `rgba(${P.flow},.34)`], [1, `rgba(${P.mid},0)`]]);
}

function coreLimb(core, sxc, syc, dirx, diry) {
  const cr = Math.cos(core.rot), sr = Math.sin(core.rot);
  const a_ = core.r * core.sx * sxc, b_ = core.r * core.sy * syc;
  const ca = dirx * cr + diry * sr, sa = -dirx * sr + diry * cr;
  const k = 1 / Math.sqrt((ca / a_) ** 2 + (sa / b_) ** 2);
  return {x: core.x + dirx * k, y: core.y + diry * k, k};
}

function overProm2(ctx, o, core) {
  const P = o.corePal, sys = o.sys, M = o.overProm2, R = core.R;
  const r = core.r, thA = M.thA, thB = M.thB;
  const dirAt = (th) => {
    const cr = Math.cos(o.tiltRot), sr = Math.sin(o.tiltRot);
    const dx = Math.cos(th) * cr - Math.sin(th) * o.k * sr;
    const dy = Math.cos(th) * sr + Math.sin(th) * o.k * cr;
    const n = Math.hypot(dx, dy) || 1;
    return {x: dx / n, y: dy / n, q: n};
  };
  const dA = dirAt(thA), dB = dirAt(thB);
  const fa = coreLimb(core, M.foot ?? 1.0, M.foot ?? 1.0, dA.x, dA.y);
  const fb = coreLimb(core, M.foot ?? 1.0, M.foot ?? 1.0, dB.x, dB.y);
  const mid = sys.pt(M.rCrest ?? 330, (thA + thB) / 2);
  const ax = sys.axis();
  const rise = (M.rise ?? 100) * (M.fsign ?? -1);
  const pm = sys.pt((M.rCrest ?? 330) + (M.bulge ?? 0), (thA + thB) / 2);

  const strands = [[1, 1], [.58, .72], [.34, .50]].slice(0, M.ns ?? 3);
  for (let si = 0; si < strands.length; si++) {
    const [ka, kw] = strands[si];
    const off = (si === 1 ? 1 : si === 2 ? -1 : 0) * (9 + si * 5) * (M.fsign ?? -1);
    const b = new Batch(ctx, 'lighter');
    const segs = 30;
    const p0 = {x: fa.x + ax.x * off * .35, y: fa.y + ax.y * off * .35};
    const p1 = {x: fb.x + ax.x * off * .35, y: fb.y + ax.y * off * .35};
    const ap = {x: pm.x + ax.x * (rise + off), y: pm.y + ax.y * (rise + off)};
    let px = p0.x, py = p0.y;
    for (let s = 1; s <= segs; s++) {
      const u = s / segs, m = 1 - u;
      const c1 = {x: (p0.x * .30 + ap.x * .70) + (ap.x - mid.x) * .10, y: (p0.y * .30 + ap.y * .70) + (ap.y - mid.y) * .10};
      const c2 = {x: (p1.x * .30 + ap.x * .70) + (ap.x - mid.x) * .10, y: (p1.y * .30 + ap.y * .70) + (ap.y - mid.y) * .10};
      const x = m ** 3 * p0.x + 3 * m * m * u * c1.x + 3 * m * u * u * c2.x + u ** 3 * p1.x;
      const y = m ** 3 * p0.y + 3 * m * m * u * c1.y + 3 * m * u * u * c2.y + u ** 3 * p1.y;
      const a = (M.a ?? .32) * ka * (.30 + .70 * Math.sin(u * Math.PI)) * (.72 + R() * .5);
      if (a > .015) b.line(`a${qa(a, 64)}w${qw((.7 + .9 * Math.sin(u * Math.PI)) * kw, 3)}`, px, py, x, y);
      px = x; py = y;
    }
    b.flush(k => ({color: `rgba(${hex(si === 0 ? P.flow : P.warm)},1)`, alpha: +k.slice(1, k.indexOf('w')), width: +k.slice(k.indexOf('w') + 1) * 2.4}));
  }
  // 等离子「壳」：沿拱的几处软盘，让拱有体积（只有线 = 读成一根钢丝）
  const ap0 = {x: pm.x + ax.x * rise, y: pm.y + ax.y * rise};
  for (let i = 1; i < 11; i++) {
    const u = i / 11, m = 1 - u;
    const c1 = {x: fa.x * .30 + ap0.x * .70, y: fa.y * .30 + ap0.y * .70};
    const c2 = {x: fb.x * .30 + ap0.x * .70, y: fb.y * .30 + ap0.y * .70};
    const x = m ** 3 * fa.x + 3 * m * m * u * c1.x + 3 * m * u * u * c2.x + u ** 3 * fb.x;
    const y = m ** 3 * fa.y + 3 * m * m * u * c1.y + 3 * m * u * u * c2.y + u ** 3 * fb.y;
    softDisc(ctx, x, y, r * (.105 + .075 * Math.sin(u * Math.PI)),
      [[0, `rgba(${P.flow},${.115 * Math.sin(u * Math.PI)})`], [.5, `rgba(${P.warm},.04)`], [1, `rgba(${P.warm},0)`]]);
  }
  for (const fp of [fa, fb]) softDisc(ctx, fp.x, fp.y, r * .13,
    [[0, `rgba(${P.flow},.36)`], [.45, `rgba(${P.mid},.14)`], [1, `rgba(${P.mid},0)`]]);
}

// ===========================================================================
// 4. 组装：离屏预渲染 + 逐帧合成
// ===========================================================================
const PHASES = 4;                  // 核体的呼吸相位（5 秒一圈 → 每相位 1.25 秒，交叉淡入淡出）
const PHASE_BOX = 760;             // 相位图 / 暗带图覆盖的方框（css 单位）：装得下核外 2.8·rCore 的体积光壳层

function newCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h));
  return c;
}

// 把核画进两张离屏：① 核体（source-over）② 等离子 + 衍射耀斑（lighter）。
// v6.4 拆成两张 —— 原来合成一张，逐帧只用 source-over 贴回来，于是**加色层的 alpha 会把
// 底下的环系压暗**（`dst × (1−α)`），而基准里加色层是纯加、不遮任何东西：
// 核下方那道日珥环因此在落地版里比基准暗一截。分层后逐帧用 lighter 贴加色层，与基准逐位一致。
function renderCoreLayers(o, core, box, dprP, t) {
  const mk = () => {
    const cv = newCanvas(box * dprP, box * dprP), c = cv.getContext('2d');
    c.setTransform(dprP, 0, 0, dprP, 0, 0);          // 1:1 的单位（与主画布同坐标系）
    c.translate(box / 2 - core.x, box / 2 - core.y);
    return [cv, c];
  };
  const oo = {...o, anim: {t, w: TAU / 5}};
  const [body, bc] = mk();
  inCoreSpace(bc, core, () => drawCoreBody(bc, oo, core));
  const [plasma, pc] = mk();
  inCoreSpace(pc, core, () => drawCorePlasma(pc, oo, core));
  drawFlare(pc, oo, core);
  // 软边：最外的体积光壳层在 2.85·rCore 处渐隐——不能裁早了，核的辉光全靠它。
  // 注意必须在**设备像素空间**里做（相位图是 dpr 缩放的离屏画布，用单位坐标会裁错位置）。
  if (o._nomask) return {body, plasma};
  const px = box * dprP;
  const cx = (core.x + (box / 2 - core.x)) * dprP, cy = (core.y + (box / 2 - core.y)) * dprP;   // = box/2 · dpr
  const g = bc.createRadialGradient(cx, cy, o.rCore * 2.05 * dprP, cx, cy, o.rCore * 2.85 * dprP);
  g.addColorStop(0, 'rgba(0,0,0,1)'); g.addColorStop(.55, 'rgba(0,0,0,.94)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  for (const c of [bc, pc]) {
    c.save();
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = 'destination-in';
    c.fillStyle = g; c.fillRect(0, 0, px, px);
    c.restore();
  }
  return {body, plasma};
}

// 前环暗带：形状是静态的（核不动 → 遮挡关系不动），只有呼吸的明暗在动 → 缓存一张，逐帧调 alpha
function renderLaneLayer(o, core, box, dprP, t) {
  const cv = newCanvas(box * dprP, box * dprP), ctx = cv.getContext('2d');
  ctx.setTransform(dprP, 0, 0, dprP, 0, 0);
  ctx.translate(box / 2 - core.x, box / 2 - core.y);
  drawLane(ctx, {...o, anim: {t, w: TAU / 5}}, core);
  return cv;
}

// 调试用：把核直接画到给定上下文（不走相位图），用来对齐「基准 vs 移植」的差异
export function drawCoreDirect(ctx, o, core, t) {
  const oo = {...o, anim: {t, w: TAU / 5}};
  inCoreSpace(ctx, core, () => drawCoreBody(ctx, oo, core));
  inCoreSpace(ctx, core, () => drawCorePlasma(ctx, oo, core));
  drawFlare(ctx, oo, core);
}

export function createCore({w, h, dpr = 1, params = {}, bands, outerN = 0} = {}) {
  const o = makeOptions(params, bands, outerN);
  o.W = w; o.H = h; o.dpr = dpr;
  o.CX = params.CX ?? w / 2; o.CY = params.CY ?? h / 2;   // 构图中心（默认画布中心）
  const core = buildCore(o);
  buildRings(o);

  // 预渲染（离屏）：核体 / 等离子各 4 个呼吸相位 + 前环暗带一张
  const dprP = Math.min(dpr, 1.5);
  const bodyPhases = [], plasmaPhases = [];
  for (let i = 0; i < PHASES; i++) {
    const L = renderCoreLayers(o, core, PHASE_BOX, dprP, 5 * i / PHASES);
    bodyPhases.push(L.body); plasmaPhases.push(L.plasma);
  }
  const laneLayer = renderLaneLayer(o, core, PHASE_BOX, dprP, 0);
  const PB = PHASE_BOX;

  return {
    o, core,
    // 主画布逐帧合成：远半环 → 核体 → 等离子（加色）→ 前环暗带 → 近半环 → 跨环日珥
    render(ctx, t) {
      const sc = o.scale ?? 1;                       // scale 旋钮：绕星核中心整体缩放
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, w * dpr, h * dpr);
      ctx.setTransform(dpr * sc, 0, 0, dpr * sc, dpr * core.x * (1 - sc), dpr * core.y * (1 - sc));
      o.anim = {t, w: TAU / 5};
      drawRings(ctx, o, 'far', core);
      // 核：两个相邻相位按小数部分交叉淡入（呼吸 + 表面翻涌都在相位图里）
      const f = ((t / 5) % 1 + 1) % 1, k = f * PHASES, i0 = Math.floor(k) % PHASES, i1 = (i0 + 1) % PHASES, fr2 = k - Math.floor(k);
      const x0 = core.x - PB / 2, y0 = core.y - PB / 2;
      ctx.save();
      // ① 核体：source-over（远半环被它挡住 = 遮挡顺序的第一半）
      ctx.globalAlpha = 1;
      ctx.drawImage(bodyPhases[i0], x0, y0, PB, PB);
      if (fr2 > .002) { ctx.globalAlpha = fr2; ctx.drawImage(bodyPhases[i1], x0, y0, PB, PB); }
      // ② 等离子 + 耀斑：lighter（纯加色，不遮任何东西）
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'lighter';
      ctx.drawImage(plasmaPhases[i0], x0, y0, PB, PB);
      if (fr2 > .002) { ctx.globalAlpha = fr2; ctx.drawImage(plasmaPhases[i1], x0, y0, PB, PB); }
      ctx.restore();
      // 前环暗带（呼吸：±laneAmp 的那一条）
      const laneA = clamp(1 + (o.solid?.laneAmp ?? .22) * .55 * Math.sin(TAU / 5 * t), .7, 1);
      ctx.save(); ctx.globalAlpha = laneA; ctx.drawImage(laneLayer, core.x - PB / 2, core.y - PB / 2, PB, PB); ctx.restore();
      drawRings(ctx, o, 'near', core);
      drawOverRingProminence(ctx, o, core);
    },
    // 星系仪面板的「环系剖面」读数：与画面上环带的径向密度是同一个函数
    // 调试用（逐像素对比页直接取这几张离屏）：phases = 核体层，plasmas = 加色层，lane = 前环暗带
    phases: bodyPhases, plasmas: plasmaPhases, lane: laneLayer,
    profile(steps = 96) { return ringProfile(o, steps); },
    geom: () => ({rEnd: o.rEnd, rCore: o.rCore, k: o.k, tilt: o.tiltRot, W: w, H: h})
  };
}

// 环带径向密度剖面（星系仪面板用；与画面上的环带是同一个函数，所以读数是「真」的）
function ringProfile(o, steps = 96) {
  const r0 = o.r0, r1 = o.rEnd, samples = [];
  for (let i = 0; i <= steps; i++) {
    const rr = r0 + (r1 - r0) * i / steps;
    let d = 0;
    for (const b of o.bands) d = Math.max(d, radDens(b, rr) * b.density);
    if (o.outer) for (const L of o.outer) if (rr > L.r0 && rr < L.r1) d = Math.max(d, L.a * 6.5);
    if (o.filament && Math.abs(rr - o.filament.r) < 3.5) d = Math.max(d, o.filament.a * 4.2);
    samples.push(d);
  }
  return {r0, r1, samples, bands: o.bands.map(b => ({id: b.id, label: b.label, n: b.n, r0: b.r0, r1: b.r1}))};
}

// 不建 DOM 也能拿到剖面（分析栏直接算，不必等星核渲染完）
export function profileFor(bands, outerN = 0, steps = 96) {
  const o = makeOptions({}, bands, outerN);
  o.CX = 0; o.CY = 0; o.W = 920; o.H = 700;
  buildRings(o);
  return ringProfile(o, steps);
}

export { azNoise, radDens };
