// 纵向布局 + 线性链识别的静态断言。用法：node apps/desktop/web/workflow-graph.test.mjs
// 这两段是纯逻辑（入参出参），不碰 DOM，所以能脱离浏览器直接跑。
import { readFileSync } from "node:fs";

function load(file, host = {}) {
  const code = readFileSync(new URL(file, import.meta.url), "utf8");
  const stub = {
    window: host,
    document: {
      createElement: () => ({ textContent: "", style: {}, setAttribute() {}, appendChild() {} }),
      head: { appendChild() {} },
      querySelector: () => null,
      addEventListener() {},
      removeEventListener() {},
    },
    esc: value => String(value ?? ""),
    renderSettings: function () {},
    SET_SECS: [],
    stages: [],
    ...host.globals,
  };
  const names = Object.keys(stub);
  new Function(...names, code)(...names.map(name => stub[name]));
  return host;
}

const layoutHost = {};
load("views/workflow-layout.js", layoutHost);
const graphHost = { globals: { esc: value => String(value ?? "") } };
load("views/project-workflow-graph.js", graphHost);

const { render } = layoutHost.WorkflowGraph;
const { chainOrder } = graphHost.ProjectWorkflowGraph;
const assert = (ok, label) => { if (!ok) { console.error("FAIL " + label); process.exitCode = 1; } else console.log("ok   " + label); };

// ── 1. 纵向布局：level 决定 y，同层决定 x ──
const nodes = [
  { id: "a", label: "创新点审查", level: 0 },
  { id: "b", label: "论文搜索", level: 1 },
  { id: "c", label: "下载精读", level: 2 },
];
const ROW = 800;
const svg = render({ nodes, edges: [{ source: "a", target: "b" }, { source: "b", target: "c" }], selected_node: "" }, false, ROW);
const svgDone = render({ nodes: [{ id: "a", label: "A", level: 0, status: "done" }], edges: [], selected_node: "" }, false, ROW);
assert(svgDone.includes("wfl-check") && !svgDone.includes("wfl-mark-no"), "已完成节点画勾不画序号");
const pos = [...svg.matchAll(/translate\((\d+),(\d+)\)/g)].map(m => ({ x: +m[1], y: +m[2] }));
assert(pos.length === 3, "三个节点都渲染出来了");
assert(pos[0].x === pos[1].x && pos[1].x === pos[2].x, "纯链同列：x 不变");
assert(pos[0].y < pos[1].y && pos[1].y < pos[2].y, "纵向排列：y 逐级递增");
assert(pos[1].y - pos[0].y === pos[2].y - pos[1].y, "层级间距均匀");

// 同层两个节点应该横向并排、y 相同
const wide = render({ nodes: [{ id: "a", label: "A", level: 0 }, { id: "b", label: "B", level: 0 }], edges: [], selected_node: "" }, false, ROW);
const widePos = [...wide.matchAll(/translate\((\d+),(\d+)\)/g)].map(m => ({ x: +m[1], y: +m[2] }));
assert(widePos[0].y === widePos[1].y && widePos[1].x > widePos[0].x, "同层节点横向并排");

// 一行一步：节点宽度吃掉可用宽度，不再固定 164
assert(svg.includes('foreignObject width="710"'), "单节点撑满 rowWidth - 90（左侧留圆标通道）");
assert(/foreignObject width="\d+" height="62"/.test(svg), "整行卡高度 62");
assert(svg.includes('wfl-row-title') && svg.includes('wfl-row-pill'), "整行卡有标题与状态药丸");
assert(svg.includes("wfl-row-no") && svg.indexOf("wfl-row-no") < svg.indexOf("wfl-row-title"), "序号在标题左侧");
assert(svg.includes('class="wfl-rail"'), "左侧竖轴存在");
assert((svg.match(/class="wfl-mark /g)||[]).length === 3, "每个节点一个圆标");
assert(svg.includes("wfl-mark-no"), "未完成节点显序号");
const wideWidths = [...wide.matchAll(/foreignObject width="(\d+)"/g)].map(m => +m[1]);
assert(wideWidths.length === 2 && wideWidths[0] === wideWidths[1] && wideWidths[0] < 710, "同层两节点平分宽度");

// 连线是纵向兜：source 底边 → target 顶边
assert(/d="M \d+ \d+ C \d+ [\d.]+, \d+ [\d.]+, \d+ \d+"/.test(svg), "连线走纵向贝塞尔");

// ── 2. chainOrder：只有严格的线性链才允许拖拽重排 ──
const chain = (ids) => ({ nodes: ids.map(id => ({ id })), edges: ids.slice(1).map((id, i) => ({ source: ids[i], target: id })) });
assert(JSON.stringify(chainOrder(chain(["a", "b", "c"]))) === '["a","b","c"]', "线性链按链序返回");
assert(chainOrder(chain(["a", "b"])) !== null, "两节点也是合法链");
assert(JSON.stringify(chainOrder({ nodes: [{ id: "a" }], edges: [] })) === '["a"]', "单节点返回自身（拖拽为空操作）");
assert(chainOrder({ nodes: [{ id: "a" }, { id: "b" }, { id: "c" }], edges: [{ source: "a", target: "b" }, { source: "a", target: "c" }] }) === null, "同一父节点的并行分支拒绝重排");
assert(chainOrder({ nodes: [{ id: "a" }, { id: "b" }], edges: [{ source: "a", target: "b" }, { source: "b", target: "a" }] }) === null, "成环拒绝重排");
assert(chainOrder({ nodes: [{ id: "a" }, { id: "b" }, { id: "c" }], edges: [{ source: "a", target: "b" }] }) === null, "断链拒绝重排");

// ── 3. 重排后重建的边仍是同一条链（rebuildChain 的等价公式） ──
const reordered = ["c", "a", "b"];
const rebuilt = reordered.slice(0, -1).map((id, index) => ({ source: id, target: reordered[index + 1] }));
assert(JSON.stringify(chainOrder({ nodes: reordered.map(id => ({ id })), edges: rebuilt })) === JSON.stringify(reordered), "重排后重建的边仍可识别为链");
