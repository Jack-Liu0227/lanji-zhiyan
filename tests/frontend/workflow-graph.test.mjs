// 纵向布局 + 线性链识别的静态断言。用法：node tests/frontend/workflow-graph.test.mjs
// 这两段是纯逻辑（入参出参），不碰 DOM，所以能脱离浏览器直接跑。
import { readFileSync } from "node:fs";

const web = new URL("../../apps/desktop/web/", import.meta.url);

function load(file, host = {}) {
  const code = readFileSync(new URL(file, web), "utf8");
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

const { render, computeHierarchicalStepNumbers } = layoutHost.WorkflowGraph;
const { chainOrder } = graphHost.ProjectWorkflowGraph;
const assert = (ok, label) => { if (!ok) { console.error("FAIL " + label); process.exitCode = 1; } else console.log("ok   " + label); };

const terminal = render({selected_node:'delivered',nodes:[
  {id:'delivered',label:'Accepted output',status:'delivered',progress_label:'0%',level:0},
  {id:'failed',label:'Failed branch',status:'failed',progress_label:'0%',summary_text:'MODEL_TIMEOUT',level:1},
  {id:'running',label:'Live branch',status:'running',level:1},
  {id:'review',label:'Human review',status:'awaiting_review',level:2}],edges:[]});
assert(terminal.includes('✓ 100% 已交付')&&!terminal.includes('>0%</span>'), '终态覆盖旧进度标签');
assert(terminal.includes('✗ 失败')&&terminal.includes('MODEL_TIMEOUT'), '失败卡片保留状态和真实错误');
assert(terminal.includes('待核查')&&!terminal.includes('50%'), '人工核查独立展示，运行中不伪造进度');

let emptyApiCalls=0;
const emptyHost={isConnected:true,querySelector:()=>({}),innerHTML:''};
graphHost.globals={esc:value=>String(value??''),api:()=>{emptyApiCalls++;throw Error('Empty conversation must not fetch templates');}};
load('views/project-workflow-graph.js',graphHost);
await graphHost.ProjectWorkflowGraph.mount(emptyHost,{contextual:true,scopedRuns:[],live:()=>true});
assert(emptyApiCalls===0&&emptyHost.innerHTML.includes('当前对话暂无工作流运行')&&!emptyHost.innerHTML.includes('pwg-canvas'), '空对话提前返回，不加载项目模板或画布');

// ── 0. 分层序号与并行子序号计算断言 ──
const hybridNodes = [
  { id: "s1", label: "Step 1: S1 建目录与素材", level: 0 },
  { id: "p1", label: "Step 2: P1 概览文档", level: 1 },
  { id: "p2", label: "Step 3: P2 步骤文档", level: 1 },
  { id: "p3", label: "Step 4: P3 风险文档", level: 1 },
  { id: "j1", label: "Step 5: J1 汇合汇编", level: 2 },
  { id: "s2", label: "Step 6: S2 终检自证", level: 3 },
];
const hybridEdges = [
  { source: "s1", target: "p1" }, { source: "s1", target: "p2" }, { source: "s1", target: "p3" },
  { source: "p1", target: "j1" }, { source: "p2", target: "j1" }, { source: "p3", target: "j1" },
  { source: "j1", target: "s2" },
];
const stepMap = computeHierarchicalStepNumbers(hybridNodes, hybridEdges);
assert(stepMap.get("s1").badgeLabel === "#1" && stepMap.get("s1").formattedTitle === "Step 1: S1 建目录与素材", "起点主序号为 Step 1 (#1)");
assert(stepMap.get("p1").badgeLabel === "#2.1" && stepMap.get("p1").formattedTitle === "Step 2.1: P1 概览文档", "并行第1分支为 Step 2.1 (#2.1)");
assert(stepMap.get("p2").badgeLabel === "#2.2" && stepMap.get("p2").formattedTitle === "Step 2.2: P2 步骤文档", "并行第2分支为 Step 2.2 (#2.2)");
assert(stepMap.get("p3").badgeLabel === "#2.3" && stepMap.get("p3").formattedTitle === "Step 2.3: P3 风险文档", "并行第3分支为 Step 2.3 (#2.3)");
assert(stepMap.get("j1").badgeLabel === "#3" && stepMap.get("j1").formattedTitle === "Step 3: J1 汇合汇编", "汇合节点主序号承接为 Step 3 (#3)");
assert(stepMap.get("s2").badgeLabel === "#4" && stepMap.get("s2").formattedTitle === "Step 4: S2 终检自证", "终检节点主序号承接为 Step 4 (#4)");

const hybridSvg = render({ nodes: hybridNodes, edges: hybridEdges }, false, 800);
assert(hybridSvg.includes("#2.1") && hybridSvg.includes("#2.2") && hybridSvg.includes("#2.3"), "DAG 画布上并行分支正确显示 #2.1, #2.2, #2.3");
assert(hybridSvg.includes("#3") && hybridSvg.includes("#4"), "DAG 画布上汇合与终检节点正确显示 #3 与 #4");

// ── 1. EvoFlow 居中卡片式 2D DAG 布局：level 决定 y，同层居中并排决定 x ──
const nodes = [
  { id: "a", label: "创新点审查", level: 0 },
  { id: "b", label: "论文搜索", level: 1 },
  { id: "c", label: "下载精读", level: 2 },
];
const ROW = 800;
const svg = render({ nodes, edges: [{ source: "a", target: "b" }, { source: "b", target: "c" }], selected_node: "" }, false, ROW);
const svgDone = render({ nodes: [{ id: "a", label: "A", level: 0, status: "done" }], edges: [], selected_node: "" }, false, ROW);
assert(svgDone.includes("100%") && svgDone.includes("wfl-card-dot"), "已完成节点展示 100% 与状态圆点");
const pos = [...svg.matchAll(/translate\((\d+),(\d+)\)/g)].map(m => ({ x: +m[1], y: +m[2] }));
assert(pos.length === 3, "三个节点都渲染出来了");
assert(pos[0].x === pos[1].x && pos[1].x === pos[2].x, "纯链同列水平居中：x 不变");
assert(pos[0].x === Math.round((ROW - 236) / 2), "单节点在画布中水平居中");
assert(pos[0].y < pos[1].y && pos[1].y < pos[2].y, "纵向排列：y 逐级递增");
assert(pos[1].y - pos[0].y === pos[2].y - pos[1].y, "层级间距均匀");

// 同层两个节点应该横向对称居中并排、y 相同
const wide = render({ nodes: [{ id: "a", label: "A", level: 0 }, { id: "b", label: "B", level: 0 }], edges: [], selected_node: "" }, false, ROW);
const widePos = [...wide.matchAll(/translate\((\d+),(\d+)\)/g)].map(m => ({ x: +m[1], y: +m[2] }));
assert(widePos[0].y === widePos[1].y && widePos[1].x > widePos[0].x, "同层节点横向并排");

// Status labels occupy their own row, preserving title and summary readability.
assert(svg.includes('foreignObject width="236" height="100"'), "状态卡片为标题、徽标和摘要预留空间");
assert(svg.includes('wfl-row-title') && svg.includes('wfl-card-summary'), "卡片有标题与底部产出摘要框");
assert(svg.includes("wfl-row-no") && svg.includes("wfl-bot-badge"), "卡片右上角有智能体徽章与 #序号");
assert(!svg.includes('class="wfl-rail"'), "已移除旧版左侧时间轴竖轨");
const wideWidths = [...wide.matchAll(/foreignObject width="(\d+)"/g)].map(m => +m[1]);
assert(wideWidths.length === 2 && wideWidths[0] === 236 && wideWidths[1] === 236, "同层并行节点保持等宽卡片");

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

// Verify node model config fields render cleanly without requiring global S
const graphSource = readFileSync(new URL("views/project-workflow-graph.js", web), "utf8");
assert(graphSource.includes("data-pwg-model") && graphSource.includes("data-pwg-fallbacks") && graphSource.includes("data-pwg-switch-model"), "工作流图节点面板包含节点模型、备用链和换模恢复控件");

// ── 3. 重排后重建的边仍是同一条链（rebuildChain 的等价公式） ──
const reordered = ["c", "a", "b"];
const rebuilt = reordered.slice(0, -1).map((id, index) => ({ source: id, target: reordered[index + 1] }));
assert(JSON.stringify(chainOrder({ nodes: reordered.map(id => ({ id })), edges: rebuilt })) === JSON.stringify(reordered), "重排后重建的边仍可识别为链");
