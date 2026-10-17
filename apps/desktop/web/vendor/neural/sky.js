// 星穹背景：整片宇宙都是代码自绘（零外部素材）。
// 星云/银河带/星尘由噪声一次性生成，靠 CSS 缓慢漂移（GPU 合成，不占每帧预算）；
// 星光闪烁与流星每帧重绘，量很小。
import {T} from './theme.js';

const SKY = T.sky;

// —— 值噪声（无依赖、可复现） ——
const smooth = t => t * t * (3 - 2 * t);
const hash2 = (x, y, s) => {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(s | 0, 1274126177);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};
const vnoise = (x, y, s) => {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, u = smooth(xf), v = smooth(yf);
  const a = hash2(xi, yi, s), b = hash2(xi + 1, yi, s), c = hash2(xi, yi + 1, s), d = hash2(xi + 1, yi + 1, s);
  return (a + (b - a) * u) * (1 - v) + (c + (d - c) * u) * v;
};
const fbm = (x, y, s, oct) => {
  let v = 0, amp = .5, f = 1, norm = 0;
  for (let i = 0; i < oct; i++) { v += vnoise(x * f, y * f, s + i * 131) * amp; norm += amp; amp *= .5; f *= 2.03; }
  return v / norm;
};
// 高光软压缩：K 以下线性、以上渐近到 CAP（星云峰值不会超过 CAP，保证文字对比度）
const K = 30, CAP = 82;
const knee = v => v <= K ? v : K + (CAP - K) * (1 - Math.exp(-(v - K) / (CAP - K)));
const sstep = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const rgb = s => s.split(',').map(Number);

// 银河带：过 (0.5, 0.55)、倾角 -0.42rad 的斜带，带内浓淡不均（u 方向噪声），并压出暗尘带。
const BAND = {th: -0.42, x0: .5, y0: .54, sigma: .085};
const bandD = (nx, ny) => (nx - BAND.x0) * Math.sin(BAND.th) - (ny - BAND.y0) * Math.cos(BAND.th);
const bandU = (nx, ny) => (nx - BAND.x0) * Math.cos(BAND.th) + (ny - BAND.y0) * Math.sin(BAND.th);

export const hex2rgb = hex => hex.replace('#', '').match(/../g).map(x => parseInt(x, 16)).join(',');

// 每颗星的确定性伪随机（同样的种子永远得到同一片天空）
export function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296);
}

// 生成一层星云/星系画布：噪声云气 + 可选银河带 + 星尘 + 抖动（防色带）
function buildSky(w, h, opt) {
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(w, h);
  const d = img.data;
  const layers = opt.layers.map(L => ({...L, c1: rgb(L.colors[0]), c2: rgb(L.colors[1])}));
  const milky = opt.milky ? {tint: rgb(SKY.milky.tint), warm: rgb(SKY.milky.warm)} : null;
  const rnd = rng(opt.seed || 7);
  const inv = opt.scaleInv || 1;                  // 画布比视口小，噪声坐标要按同比例缩放，云气尺度才和屏幕一致
  for (let y = 0; y < h; y++) {
    const ny = y / h;
    for (let x = 0; x < w; x++) {
      const nx = x / w;
      let r = 0, g = 0, b = 0;
      for (let i = 0; i < layers.length; i++) {
        const L = layers[i];
        const sc = 96 * L.scale;                    // 特征尺度（像素，画布为 1/2 视口，屏幕上约翻倍）
        const px = x * inv / sc, py = y * inv / sc;
        const n = fbm(px, py, L.seed, 4);
        const m = fbm(px * .34 + 3.1, py * .34 - 2.7, L.seed + 5, 3);   // 大尺度调制 → 云团有大有小
        const a = Math.pow(sstep(.36, .8, n) * sstep(.2, .7, m), 1.25) * L.alpha;
        if (a <= .002) continue;
        const k = fbm(px * .62 - 1.7, py * .62 + 4.3, L.seed + 9, 3);
        r += (L.c1[0] + (L.c2[0] - L.c1[0]) * k) * a;
        g += (L.c1[1] + (L.c2[1] - L.c1[1]) * k) * a;
        b += (L.c1[2] + (L.c2[2] - L.c1[2]) * k) * a;
      }
      if (milky) {
        // 深空摄影里的银河：一条宽的弥漫带 + 一条窄的亮核脊 + 交错的暗尘带
        const dd = bandD(nx, ny) / BAND.sigma;
        const u = bandU(nx, ny);
        const core = Math.exp(-dd * dd * .62);
        const ridge = Math.exp(-dd * dd * 7.5);
        const clump = .30 + .70 * fbm(u * 3.4, 0.5, opt.seed + 21, 3);
        const fine = .65 + .35 * fbm(u * 9.5 + 1.2, dd * 1.4, opt.seed + 27, 2);
        const lane = .30 + .70 * sstep(.34, .60, fbm(u * 6.2 + 2.4, dd * .75, opt.seed + 33, 3));  // 暗尘带
        // 四个因子相乘会把带子压没，这里用 <1 的幂把中段提起来，带子才看得见
        const a = (Math.pow(core * clump * lane * fine, .55) * .92 + ridge * clump * .42);
        const warm = sstep(.34, .78, fbm(u * 2.1 - 5, dd * .5, opt.seed + 44, 2));
        r += (milky.tint[0] + (milky.warm[0] - milky.tint[0]) * warm) * a;
        g += (milky.tint[1] + (milky.warm[1] - milky.tint[1]) * warm) * a;
        b += (milky.tint[2] + (milky.warm[2] - milky.tint[2]) * warm) * a;
        // 暗尘：从背景里再吸掉一点点亮度，带子才有「被尘埃切开」的层次
        const dustLane = sstep(.52, .74, fbm(u * 5.1 - 3.7, dd * .62, opt.seed + 51, 3)) * Math.exp(-dd * dd * .5);
        r -= dustLane * 7.5; g -= dustLane * 6.8; b -= dustLane * 6.2;
      }
      // 柔化高光：星云整体压暗、不过曝，也让压在星云上的文字保持对比度
      r = knee(r); g = knee(g); b = knee(b);
      const i4 = (y * w + x) * 4;
      const j = (rnd() - .5) * 3.2;               // 抖动：抹掉渐变色带
      d[i4] = Math.max(0, Math.min(255, r + j));
      d[i4 + 1] = Math.max(0, Math.min(255, g + j));
      d[i4 + 2] = Math.max(0, Math.min(255, b + j));
      d[i4 + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  // 星尘：细密的微光尘点，浓淡跟着银河带
  ctx.globalCompositeOperation = 'lighter';
  const dust = opt.dust | 0;
  for (let i = 0; i < dust; i++) {
    const nx = rnd(), ny = rnd();
    const near = Math.exp(-Math.pow(bandD(nx, ny) / (BAND.sigma * 2.4), 2));
    if (rnd() > .28 + near * .72) continue;
    const c = SKY.star[(i * 7919) % SKY.star.length];
    ctx.fillStyle = `rgba(${c},${(.06 + rnd() * .22 * (.5 + near)).toFixed(3)})`;
    const s = rnd() < .12 ? 2 : 1;
    ctx.fillRect(nx * w, ny * h, s, s);
  }
  ctx.globalCompositeOperation = 'source-over';
  return cv;
}

// 星点光晕精灵（含十字星芒），生成一次反复使用，避免每帧 createRadialGradient
export function glowSprite(color, size, spikes) {
  const cv = document.createElement('canvas');
  cv.width = cv.height = size;
  const ctx = cv.getContext('2d'), c = size / 2;
  const g = ctx.createRadialGradient(c, c, 0, c, c, c);
  g.addColorStop(0, `rgba(${color},1)`);
  g.addColorStop(.16, `rgba(${color},.72)`);
  g.addColorStop(.42, `rgba(${color},.2)`);
  g.addColorStop(1, `rgba(${color},0)`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  if (spikes) {
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = `rgba(${color},.5)`;
    ctx.lineWidth = size / 34;
    for (const a of [0, Math.PI / 2, Math.PI / 4, -Math.PI / 4]) {
      const len = a % (Math.PI / 2) === 0 ? c * .96 : c * .5;
      const sp = a % (Math.PI / 2) === 0 ? .5 : .3;
      const lg = ctx.createLinearGradient(c - Math.cos(a) * len, c - Math.sin(a) * len, c + Math.cos(a) * len, c + Math.sin(a) * len);
      lg.addColorStop(0, `rgba(${color},0)`); lg.addColorStop(.5, `rgba(${color},${sp})`); lg.addColorStop(1, `rgba(${color},0)`);
      ctx.strokeStyle = lg;
      ctx.beginPath(); ctx.moveTo(c - Math.cos(a) * len, c - Math.sin(a) * len); ctx.lineTo(c + Math.cos(a) * len, c + Math.sin(a) * len); ctx.stroke();
    }
  }
  return cv;
}

// 挂到页面：三层画布（两批星云 + 每帧星点/流星），与参照实现同一套参数
export function mountCosmos(root, reduced) {
  const wrap = document.createElement('div');
  wrap.className = 'cosmos';
  wrap.setAttribute('aria-hidden', 'true');
  wrap.innerHTML = '<div class="cosmos-base"></div><canvas class="cosmos-neb neb-a"></canvas>'
    + '<canvas class="cosmos-neb neb-b"></canvas><canvas class="cosmos-fx"></canvas><div class="cosmos-vignette"></div>';
  root.appendChild(wrap);
  const fx = wrap.querySelector('.cosmos-fx');
  const nebA = wrap.querySelector('.neb-a'), nebB = wrap.querySelector('.neb-b');

  // 生成星云画布（分两批，首屏先出来再渐渐显影）
  const timers = [];
  let stopped = false;
  const build = (el, opt) => {
    if (stopped || !el) return;
    const w = window.innerWidth, h = window.innerHeight, S = 2;   // 生成分辨率 = 视口的 1/2，柔化后放大
    const cv = buildSky(Math.max(2, Math.round(w / S)), Math.max(2, Math.round(h / S)), {...opt, scaleInv: S});
    el.width = cv.width; el.height = cv.height;
    el.getContext('2d').drawImage(cv, 0, 0);
    requestAnimationFrame(() => el.classList.add('on'));
  };
  timers.push(setTimeout(() => build(nebA, {layers: [{...SKY.nebula[1], scale: 1.15}], milky: true, dust: 2600, seed: 3}), 40));
  timers.push(setTimeout(() => build(nebB, {layers: [{...SKY.nebula[0], scale: .85}, {...SKY.nebula[2], scale: 1.7}], milky: false, dust: 1600, seed: 17}), 320));

  // 每帧：星光错相闪烁 + 偶发流星
  let raf, W = 0, H = 0, dpr = 1, t = 0, last = performance.now();
  const sprites = {};
  const sprite = (color, size, spikes) => (sprites[color + size + spikes] ||= glowSprite(color, size, spikes));
  let stars = [];
  const rebake = () => {
    const d = Math.min(window.devicePixelRatio || 1, 1.6);
    dpr = d;
    W = window.innerWidth; H = window.innerHeight;
    fx.width = Math.round(W * d); fx.height = Math.round(H * d);
    const rnd = rng(91);
    const n = Math.round(Math.min(620, Math.max(230, W * H / 3200)));
    // 星等按幂律分布：绝大多数是暗星尘，少量中等星，极少数亮星才有星芒；色温以白为主
    const temp = () => { const q = rnd(); return q > .90 ? SKY.star[4 + (q > .965 ? 1 : 0)] : q > .62 ? SKY.star[2 + (q > .80 ? 1 : 0)] : SKY.star[q > .30 ? 1 : 0]; };
    stars = Array.from({length: n}, (_, i) => {
      let nx = rnd(), ny = rnd();
      if (i % 2 === 0) {                      // 一半星点贴着银河带：带子上星最密，带外稀
        const u = (rnd() - .5) * 1.9, dd = (rnd() - .5) * BAND.sigma * 2.9;
        nx = BAND.x0 + u * Math.cos(BAND.th) - dd * Math.sin(BAND.th);
        ny = BAND.y0 + u * Math.sin(BAND.th) + dd * Math.cos(BAND.th);
      }
      nx = ((nx % 1) + 1) % 1; ny = ((ny % 1) + 1) % 1;
      const mag = rnd(), z = rnd();           // z：伪深度 —— 近星更大更亮，远星缩成星尘
      const tier = mag > .968 ? 2 : mag > .855 ? 1 : 0;
      return {
        x: nx, y: ny, tier,
        r: (tier === 2 ? 1.75 + rnd() * 1.1 : tier === 1 ? 1.0 + rnd() * .5 : .42 + rnd() * .46) * (.88 + .3 * z),
        a: (tier === 2 ? .95 : tier === 1 ? .52 + rnd() * .3 : .14 + rnd() * .4) * (.42 + .58 * z),
        ph: rnd() * 6.283, sp: .25 + rnd() * 1.5,
        c: temp()
      };
    });
  };
  rebake();
  const ro = new ResizeObserver(rebake);
  ro.observe(fx);

  const meteors = [];
  let nextMeteor = 2.4 + Math.random() * 4;
  const frame = now => {
    raf = requestAnimationFrame(frame);
    try {
      const dt = Math.max(0, Math.min(.05, (now - last) / 1000)); last = Math.max(last, now);
      if (document.hidden) return;
      if (!reduced) t += dt;
      const ctx = fx.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'lighter';
      for (const s of stars) {
        const tw = reduced ? 1 : .58 + .42 * Math.sin(t * s.sp + s.ph) * Math.sin(t * s.sp * .37 + s.ph * 1.7);
        const a = s.a * Math.max(.15, tw);
        const x = s.x * W, y = s.y * H;
        ctx.globalAlpha = a;
        if (s.tier === 2) {                  // 亮星：微光晕 + 四道（两道短）衍射星芒 + 实心亮核
          const size = s.r * 24;
          ctx.drawImage(sprite(s.c, 40, true), x - size / 2, y - size / 2, size, size);
          ctx.globalAlpha = Math.min(1, a * 1.1);
          ctx.fillStyle = 'rgb(255,255,255)';
          ctx.beginPath(); ctx.arc(x, y, Math.max(.8, s.r * .34), 0, 6.283); ctx.fill();
        } else if (s.tier === 1) {           // 中等星：一小团柔光，边缘自然
          const size = s.r * 6.5;
          ctx.drawImage(sprite(s.c, 22, false), x - size / 2, y - size / 2, size, size);
        } else {                             // 暗星：亚像素灰点，成片时是星尘而不是彩色小圆点
          ctx.fillStyle = `rgb(${s.c})`;
          ctx.fillRect(x - s.r / 2, y - s.r / 2, s.r, s.r);
        }
      }
      ctx.globalAlpha = 1;
      // 流星：低频、自然
      if (!reduced) {
        nextMeteor -= dt;
        if (nextMeteor <= 0 && meteors.length < 2) {
          const dir = Math.random() < .5 ? -1 : 1;
          const a = (.16 + Math.random() * .3);
          meteors.push({
            x: (dir < 0 ? .3 + Math.random() * .68 : .02 + Math.random() * .68) * W,
            y: (.02 + Math.random() * .5) * H,
            vx: dir * Math.cos(a), vy: Math.sin(a),
            sp: (.42 + Math.random() * .3) * Math.min(W, H),
            len: .1 + Math.random() * .1, life: 0, dur: .85 + Math.random() * .5
          });
          nextMeteor = 5 + Math.random() * 9;
        }
        for (let i = meteors.length - 1; i >= 0; i--) {
          const m = meteors[i];
          m.life += dt;
          if (m.life >= m.dur) { meteors.splice(i, 1); continue; }
          const k = m.life / m.dur, head = k * m.sp * (1.15 - .35 * k);
          const hx = m.x + m.vx * head, hy = m.y + m.vy * head;
          const tl = m.len * Math.min(W, H) * (1 - k * .55);
          const tx = hx - m.vx * tl, ty = hy - m.vy * tl;
          const fade = Math.sin(Math.min(1, k * 3.2) * Math.PI * .5) * (1 - Math.pow(k, 2.2));
          const lg = ctx.createLinearGradient(tx, ty, hx, hy);
          lg.addColorStop(0, `rgba(${SKY.meteor.tail},0)`);
          lg.addColorStop(.72, `rgba(${SKY.meteor.tail},${.34 * fade})`);
          lg.addColorStop(1, `rgba(${SKY.meteor.head},${.85 * fade})`);
          ctx.strokeStyle = lg; ctx.lineWidth = 1.7; ctx.lineCap = 'round';
          ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(hx, hy); ctx.stroke();
          ctx.globalAlpha = fade * .9;
          const g = sprite('255,250,236', 26, false), gs = 30;
          ctx.drawImage(g, hx - gs / 2, hy - gs / 2, gs, gs);
          ctx.globalAlpha = 1;
        }
      }
      ctx.globalCompositeOperation = 'source-over';
    } catch (err) { if (!fx.__warned) { fx.__warned = 1; console.error(err); } }
  };
  raf = requestAnimationFrame(frame);

  return () => {
    stopped = true;
    timers.forEach(clearTimeout);
    cancelAnimationFrame(raf);
    ro.disconnect();
    wrap.remove();
  };
}
