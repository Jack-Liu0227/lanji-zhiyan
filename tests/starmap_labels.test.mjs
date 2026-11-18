// 星图标签避让的几何自检：N 个项目均分在环上时，避让后卡片必须零重叠。
// 逻辑与 apps/desktop/web/vendor/neural/scene.js 的 step() 里那段一致（那边嵌在渲染循环中，抽不出来）。
import assert from 'node:assert/strict';

const W = 1188, H = 743, CX = W / 2, CY = H * 0.46;   // 星图 iframe 实测 CSS 尺寸
const CW = 200, CH = 92;                      // 项目层卡片实测 200×92（ax 树 353×162 除以 1.76）
const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

function build(N) {
  const R = Math.min(W, H) * 0.30;
  const out = [];
  for (let i = 0; i < N; i++) {
    const a = (-90 + i * 360 / N) * Math.PI / 180;
    const x = CX + R * Math.cos(a), y = CY + R * Math.sin(a);
    const nX = Math.cos(a), nY = Math.sin(a);
    const gap = 14 + (Math.abs(nX) * CW + Math.abs(nY) * CH) / 2;
    out.push({
      x: clamp(x + nX * gap - CW / 2, 10, W - CW - 10),
      y: clamp(y + nY * gap - CH / 2, 72, H - CH - 10),
      w: CW, h: CH,
    });
  }
  return out;
}
const hit = (A, B) =>
  Math.min(A.x + A.w, B.x + B.w) - Math.max(A.x, B.x) > 0 &&
  Math.min(A.y + A.h, B.y + B.h) - Math.max(A.y, B.y) > 0;
function overlaps(ps) {
  let n = 0;
  for (let i = 0; i < ps.length; i++)
    for (let j = i + 1; j < ps.length; j++) if (hit(ps[i], ps[j])) n++;
  return n;
}
function avoid(boxes, stepY = CH + 4) {
  for (let pass = 0; pass < 8; pass++) {
    // ① 纵向错行：撞上就一个上一个下
    for (let a = 0; a < boxes.length; a++)
      for (let b = a + 1; b < boxes.length; b++) {
        const A = boxes[a], B = boxes[b];
        if (!hit(A, B)) continue;
        if (A.y <= B.y) {
          A.y = Math.max(72, A.y - stepY);
          B.y = Math.min(H - B.h - 10, B.y + stepY);
        } else {
          A.y = Math.min(H - A.h - 10, A.y + stepY);
          B.y = Math.max(72, B.y - stepY);
        }
      }
    // ② 一维横排：y 还重叠的按 x 排序，从左往右怼开（对称推会来回振荡，排序扫才收敛）
    const row = boxes.slice().sort((p, q) => p.x - q.x);
    for (let i = 1; i < row.length; i++) {
      const P = row[i - 1], C = row[i];
      if (Math.min(P.y + P.h, C.y + C.h) - Math.max(P.y, C.y) <= 0) continue;
      const minX = P.x + P.w + 4;
      if (C.x < minX) C.x = Math.min(minX, W - C.w - 10);
    }
    for (let i = row.length - 2; i >= 0; i--) {
      const Nx = row[i + 1], C = row[i];
      if (Math.min(Nx.y + Nx.h, C.y + C.h) - Math.max(Nx.y, C.y) <= 0) continue;
      const maxX = Nx.x - C.w - 4;
      if (C.x > maxX) C.x = Math.max(10, maxX);
    }
  }
  return boxes;
}

let tested = 0, collided = 0;
for (const N of [3, 5, 9, 10, 13]) {
  const before = overlaps(build(N));
  const after = overlaps(avoid(build(N)));
    assert.equal(after, 0, `${N} 个项目：避让后仍有 ${after} 处重叠`);
  if (before > 0) collided++;
  console.log(`N=${String(N).padStart(2)}  原始 ${String(before).padStart(2)} 处重叠 → 避让后 ${after}`);
  tested++;
}
assert.equal(tested, 5);
assert.ok(collided >= 2, `只有 ${collided} 组原始状态会撞，测试覆盖不足（应该 >= 2）`);
console.log(`
OK — ${tested} 组避让后全部归零，其中 ${collided} 组原始状态确实会重叠`);
