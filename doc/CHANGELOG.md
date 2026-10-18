# 改动记录

> 一轮改动一条，给 PR / 交接用。只写「改了什么、影响谁、怎么验」。

## 待发 PR（本轮：清废旧代码）

按死代码审计（顶层定义全文只出现一次 / import 了没用 / CSS 类没人引）过了一遍，只删**证明确认没人用**的：

- **Python**：删 5 个文件里 12 处未使用的 import（`space_service` re、`workflow` pdfio/shutil/subprocess、`workflow_converters` urllib、`workflow_documents` time、`workflow_stages` json/time/fileguard/pdfio）；删 `research_core.open_db_session`（v1.3.0 重构留下的，全文 0 引用）。
- **前端**：删 `starmap-view.js` 里整套「知识库看板」渲染器（`renderLibrary` + 17 个 `kb*` 函数，282 行）——知识库 2026-10-17 已并入项目空间·笔记，全文 0 引用；该文件 303 → 21 行。星图仍在 `/kb.html` 里（未动）。
- **CSS**：`app.css` 删 131 条规则（判据：选择器末级类名全在死类集合里），1697 → 1550 行。主要是 `.kb-*`（旧看板）、`.tc-*`/`.tm-*`/`.tr-*`（旧团队对话/翻译面板）、`.wf-head`（三栏改版后没了）、`.sp-acts`、`.skeleton-*`、`.q-av` 等。
- 每一条都先 grep 过：动态拼类名（`"kb-" + x`）全仓 0 处，删除不会误伤。

验证：`py_compile` 6 个 py + `node --check` 全部 js 通过；全量 241 项测试仍是那 3 红（`test_feishu` 1、`test_workflow_layout` 2，与 origin/main 基线同族）；真库副本实测 团队 / 科研台 / 项目空间 / 知识库 四页：无 JS 异常、无横向溢出，科研台三栏实测 左 172 / 右 404 / 10 步。

## 待发 PR（本轮：科研台三栏改版 + 开场卡 + 两处修复）

**已定稿的视觉**：科研台三栏方案（用户 2026-10-18 确认「就这版」）。未提交，攒在一起一次 PR。

### 1. 科研台改三栏（定稿）

左 172px（折叠后 52px）｜中 展示区 + 底部确认条｜右 404px 本步助手。

- 左栏：顶部独立卡片＝浅灰小字「项目名」+ 项目名 17px 粗体（左侧 3px 主色渐变态）；下面 `< 折叠` 按钮（原 `‹` 图标废掉）；十步只留 emoji + 名称（**无序号**）。状态：已完成满亮 + 绿点、未开始 `.todo` 45% 透明、当前步主色底 + 左侧 3px 条。折叠用 `wf-page.min`（52px，只留 emoji）。
- 中栏：原有 10 个面板不动，外面包一层 `.wf-stage` + `.wf-foot`（退回上一步 / 确认交付 →）。`确认交付` → `POST /api/step/done` → `log_step(done,"用户确认交付")`。
- 右栏：`.wf-agent` 复用 `.at-*` 外壳 —— emoji 头像 + 助手名 + 一句职责 + 模型胶囊、`对话/轨迹` 带计数、消息区、三格统计（轮数/工具/token）+ 工具名/产出目录、composer（＋、🤖署名、↑）+ Enter 提示。历史走 `GET /api/step/messages?id=&step=`，发送走各步 scoped 端点（`/api/idea|map|outline|write|review`，其余 `/api/agent`）。
- 自带对话的五步（idea / map / outline / write / review）：面板里那份 `.ol-right` / `.mp-right` / `#panel-idea > .at-*` 隐藏，对话统一收到右栏（同一 scope，历史不丢）。
- 每步助手：🔍创新点审查助手 / 📚文献检索助手 / ⬇️入库助手 / 🌐翻译助手 / 🗂框架助手 / 🧩谱系助手 / ✍️写作助手 / 🧐审查助手 / 🌍中译英助手 / 🔄格式助手（表在 `workflow-view.js` 的 `WF_AGENT`）。
- 顶部 `Partner Agent · 绑定《…》· 回首页智研管家 →` 整条删除（连同 `#wfToLeader`）。

验证：CDP 实测十步逐个点开——每步只有 1 个可见面板、面板内旧对话不可见、右栏助手名对得上、无 JS 异常；`确认交付` 写入 `steps` 表；`/api/agent` 流式正常（ping 拿到 usage/tool_call/tool_result）。

### 2. 开场卡改版（方案 A）

- 原来那个药丸按钮（边框 + SVG 箭头）废掉，改成横排「进 入」行楷（`STXingkai`，系统自带；`document.fonts` 实测非回退字体）+ 笔锋线 + 朱印「研」+ `点击或按 Enter`。
- **白边根因**：`.sp-stage` 旋转 ±8° 时投影小于视口，露出 `#splash` 底色；`.sp-bg` 还有 12px 圆角。修法 = 新增 `.sp-bleed{inset:-10%}` 包住背景与雾层（8° 时远边缩 8.8%，10% 够），去掉圆角。
- 云雾：三条 `radial-gradient + blur(38px) + screen`，共用 `spFogDrift`，19/27/34s + 双向 alternate 拉开相位。
- 删掉声纳环 `.sp-btn-pulse`、SVG 箭头、`@keyframes spRadar`；冲击波位置对准新 CTA。

### 3. 智研管家对话「删不掉」

根因两处：`agent_settings.migrate()` 每次开库 `INSERT OR IGNORE INTO chats SELECT FROM teams`；`WorkflowDB.team_add()` 建团队顺手插一条同名 chats 行。两处都删（留注释）。空壳对话已清（备份在 `data/_backup/research.sqlite3.bak-20261018-124913`）。

回归：新增 `tests/test_chat_migrate.py`（团队不生成对话 / 删掉的对话重启不复活），`test_team` 24 条仍全过。

### 4. 项目空间两处

- 全局搜索框原来两个 🔍（placeholder 里一个 + 左上浮层一个）→ 删 placeholder 里的，浮层 opacity 提到 1。
- 笔记预览从 560/760 写死高度改为整页撑满：`.main.sp-fill` + `.sp-notes-layout`（`render()` 每次清 `sp-fill`，换页不串样式）。

### 5. 团队页改「一层办公室」（定稿）

- 团队从「研讨室聊天页」变成一层平面：左图纸、右人员栏。房间＝团队：每个项目两间**常驻**（文献抽取 / 文件撰写）+ 三间**加急**（智研管家为某件事拉人组建，交完归还）。点房间 → 右栏出人员结构（emoji＋名字＋职责＋模型＋状态）与最近流水。
- 数据：`teams` 加 `project_id` / `purpose`（老库 ALTER 补列）。`FIXED_TEAMS` 改成 文献抽取 / 文件撰写，**按项目一套**；`ensure_defaults(pid)` 幂等播种，老库里没绑项目的固定团队由第一个打开的项目接手改名（成员与会话不动）。加急位满了先回收「已收工」。
- 管家组队：新工具 `team_form(purpose, members, project)`；页面右上「＋ 组队」是手动版。
- 桌子固定（2 列 × N 行，按房间尺寸算一次），小人六个部件。行为跟真实 `member.status`：`running/queued` 坐桌前敲键盘（显示器亮），其余溜达、累了回自己桌前坐；`prefers-reduced-motion` 时全员坐着。
- 「修改编制」→ 原团队管理页（已按项目过滤）；「进入研讨室」→ 跳到科研台并把 `curTask` 绑到该项目。
- 顺手修两处：`_extension_route` 把 POST `/api/teams/*` 吃掉（`do_POST` 那段成了死代码）；`research_core.summary()` 认不出团队 id，导致 `team._round` 抛 KeyError。
- 回归：新增 `tests/test_team_project.py`（每项目一套 / 项目隔离 / 老库接手 / 组队 / 加急位上限回收）。
- 验证：1440×900 隔离实例（真库副本）跑通 点房间换人栏 / 小人走动 / 修改编制 / 进入研讨室 / ＋组队，无 JS 异常、无元素越界。

### 提交注意

- `sketches/`（splash-redesign、wf-panel 各版稿 + landed-final3.png）是设计稿，**不入库**。
- 工作区还有别人的未提交改动，PR 前先 `git status` 分开看。

## 待发 PR（chore/dead-code → main）

本轮三项改动，一次 PR 讲清：

| 提交 | 内容 | 详见 |
|---|---|---|
| `6f46007` | 项目空间去掉团队/日程 tab；元信息窄卡删除；阶段 + 最晚修改时间并入页头卡（视觉方案 A）；KPI 四卡改三卡；补 `S.stages` 修「工作流快照空 / 进度按 7 步算」 | 本文「项目空间收敛」 |
| `6da59f9` | 全局 Leader 全链路更名智研管家（UI / prompt / 团队 leader 名 + 旧名自愈） | 同上第 4 节 |
| `fd2bcae` | 论文星图改按需编译（右上生成键、结果落库、未生成空态、删除项目连带清缓存） | 本文「论文星图」 |
| `8397961` `c1e1d5c` `c6ad8fa` | 主页 Agent 更名智研管家；管家 Q 版形象（空状态 72px + 侧栏图标 + 列表七姿势轮换）；专家头像四处同源 | 本文「主页 Agent 更名」 |
| `4c5f7bc` + 本条 | 输入栏不再压字 + 胶囊化（方案 C）；推理/权限改键=值 | 本文「输入栏改胶囊」 |
| 本条 | 记录整理 | — |

**注意**

- `chore/dead-code` 比 `main` 多 5 条：除上面三条，还有 `b98a266`（Zotero 接 MCP）和 `3bd3e37`（其改动记录）。要么一个 PR 全带，要么先拆成两个 PR，别混着审。
- `fd2bcae` 的 `server.py` 顺带带入了两处当时未提交的改动（`stage_zotero_import` 的 import、`/api/starmap` 空 `task` 走 `overview`），属于 Zotero/星图那条线；功能一致，只是提交归属混了，看 blame 时知道一下。
- 工作区还有别人未提交的改动（`kb.html` / `vendor/neural/scene.js` / `starmap.py` / `workflow.py` / `tests/test_workflow.py` 等），本轮没动、也没提交。

## 2026-10-17 · 主页 Agent 更名「智研管家」+ 管家/专家头像三处统一

主页那一路不再叫 Agent：没绑专家就是智研管家（全局、跨项目），绑了专家才切到专家身份（项目内）。

- 改名：侧栏首项、页签标题统一「智研管家」；输入框提示按当前身份走（管家 / 绑定专家名）；专家选择器的空选项从「未绑专家」改成「智研管家（全局）」（`agent-settings.js` 也改，它才是实际重绘选项的那层）。
- 管家形象：没绑专家时空状态用 `faceOf(a,72)`——和专家 emoji 同一个 72px 方框（不再放大成 190px 大图）；侧栏「智研管家」首项图标也从 🤖 换成同一个 Q 版小人图（18px）。
- 对话列表：未绑专家 = `av1..av7` 七张姿势按创建顺序轮流；绑了专家 = 专家头像（emoji），与顶部头像、消息署名、右下角选择器前缀同源（都走新加的 `faceOf(a, px, rotate)`）。
- 副标题：管家显示「全局（不限项目）」；专家没绑项目时提示「专家按项目工作 · 当前未绑项目」，不拦操作。

验证：`node scripts/jscheck.js index.html` 14/14；headless 实测三条对话——管家对话 消息头像 av1→av2→av3 轮换、顶部/列表/选择器均为 Q 版小人；专家对话（写作搭档 ✍️）列表/顶部/消息/选择器四处一致。

## 2026-10-17 · 输入栏改胶囊（方案 C）

原问题：一排 7 个控件挤在 960px 里，`.at-sel` 被 flex 压到 `min-width:0` 还在换行，「推理/权限」被压成竖排，工作区名截断成「默认（工作区 ou…」。

- 样式：`.at-sel` 变 30px 圆形胶囊（描边 + `panel2` 底 + 悬停描边 accent），`flex:none` + `white-space:nowrap` 不再参与收缩；`select` 后统一补 `▾`（`:has(select)`，输入型 `.at-sel` 不受影响）；`＋` 同步改成同规格圆形按钮。
- 键=值：`agent-settings.js` 注入的推理/权限改成 `⚡推理 / 🛡权限`（`<span class="k">` 灰键 + select 当值）；模型空项文案「自动选择 / 跟随当前模型」→「跟随当前模型」。
- 放不下：左组 `overflow-x:auto` 隐藏滚动条兜底（窄窗口可横滑），右组 `flex:none` 保证专家头像 + 发送永远完整；960px 下实测不触发（左组 746=746）。
- 副作用：设置页/审查面板里同款 `.at-sel` 也变成胶囊，属于同一套视觉，未单列。

验证：jscheck 14/14；DOM 实测 960px 下左组 746/746、右组 177/177、无竖排（标签高 30px）、发送钮不出框。

## 2026-10-17 · Zotero 接成 MCP（读+写）+ 分类一键进下载队列

论文信息不再靠「自己抽」：题录/摘要/PDF 直接从本机 Zotero 取，省掉检索+下载+元数据猜测；正文证据抽取（`extract_ai`）不变，Zotero 给不了页码级逐字引文。

### 1. 新增 stdio MCP 服务（给 agent 用的读+写）

`packages/connectors/zotero_mcp.py`，零新依赖，复用现有连接器：

| 工具 | 走什么 | 说明 |
|---|---|---|
| `zotero_status` | 本机 Local API | 连接状态 + 能否写入（不能写会说明要哪个 key） |
| `zotero_search` / `zotero_item` | 本机 Local API | 题录（title/creators/year/venue/doi/tags/abstract）；只读、无需 key、可离线 |
| `zotero_pdf` | 本机 Local API + `storage/` | 论文 PDF 的本机绝对路径 |
| `zotero_create_item` / `zotero_create_note` | Zotero Web API | 写入需要 key：先读 env `ZOTERO_API_KEY`(+`ZOTERO_LIBRARY_ID`/`_TYPE`)，没有就复用程序「设置」里已存的那份 |

- 读=本机 API 是**只读**的，写入只能走 Web API，MCP 变不出这个能力——所以「写入可用」的前提是设置里填过 key。
- 不碰 `zotero.sqlite`，不改本机库内文件；日志只写 stderr（stdout 是 JSON-RPC）。

### 2. 分类 → 下载队列（不检索、不联网、不复制文件）

- `zotero.py`：新增 `item(key)`、`pdf_path(key)`、`storage_dir()`。本机 API 不返回附件 `path`，所以按**附件自己的 key** 进 `storage/<附件key>/` 找 PDF；数据目录不在默认位置用 `ZOTERO_DATA` 指。
- `workflow.py`：新增 `cache_result()`（命中同 DOI/同标题返回旧 id，队列才能复用）、`stage_zotero_import()`（题录用 Zotero 的、PDF 用本机附件直读入库、没附件的标「待手动」，不假装成功；笔记/附件类条目排除）。
- `server.py`：`POST /api/workflow/zotero`（`{task_id, collection?, keys?}`）。
- `index.html`：「从 Zotero 取文献」弹窗加「导入项目队列」按钮，结果写回弹窗状态行，不新增提示组件。

### 3. 验证

- `py -3.10 -m unittest tests.test_zotero` → 14/14 OK（新增：`pdf_path` 选中 PDF 附件/无附件返回空、导入分流把笔记条目排除且 DOI 补 url）。
- 直接把 JSON-RPC 喂给服务：`initialize` / `tools/list` / `tools/call` 全通；Zotero 没开时返回「Zotero 没启动（127.0.0.1:23119 无响应）」，不编造成功。
- 用程序自己的 MCP 客户端跑 `POST /api/capabilities/mcp` + `/discover`，6 个工具全部发现（不是 curl 假装互通）。
- 全量 `discover`：223 tests，4 红——3 个在干净 HEAD 上同样红（`test_workflow_layout` ×2、`test_feishu` 设备码），1 个只在全量顺序下红。均非本次引入。

### 4. 影响面 / 待办

- 改了 `.py` → **关窗重开**，「导入项目队列」按钮和 `/api/workflow/zotero` 才在。
- Zotero 本体必须先启动；「写入」要先在设置里填 Web API key（MCP 读同一份配置，不用填两遍）。
- 未做：`zotero_update_item`（改已有条目要带 version）。
- PR 注意：本条的 `workflow.py` / `server.py` / `index.html` 三处改动被并行会话的提交 `fd2bcae` 一并带走，`b98a266` 里只有 `zotero.py`、`zotero_mcp.py`、`tests/test_zotero.py`；一起 PR 时以「两个提交算一条」讲。

## 2026-10-17 · 论文星图改成「点一下才生成」

`paper_graph` 以前挂在 `GET /api/task` 里**每次打开项目都现算**（58 篇要重解析 PDF、1–2 秒，越攒越慢）。改成编译产物：用户点「生成星图」才跑一次，结果落库，平时只读缓存。

- `workflow.py`：新增表 `paper_graph(task_id, json, built_at, took_ms)`；`paper_graph()` 改为**只读缓存**（没生成过返回空图 + `built_at` 空），新增 `build_paper_graph()` 现算并 `INSERT OR REPLACE`。删除项目时连带清掉（`delete_task` 的表清单里加了 `paper_graph`）。
- `server.py`：新增 `POST /api/task/papergraph/build`；`GET /api/task` 的 `paper_graph` 现在来自缓存。
- `index.html`：论文星图 tab 顶部加工具栏——左「论文星图 + 已生成/未生成 + 时间·篇数·关系数·耗时」，右上主按钮「生成星图」，已生成后降级成 ghost「重新生成」，点击后按钮转圈并显示已等秒数；未生成且库里有文献时显示虚框空态，说明「扫库内 N 篇 PDF 全文算引用关系，不会自动跑」。
- 耗时按实际显示（不足 1 秒显示 ms），不写死估计值。
- 测试：`test_paper_graph_is_cached_until_built`（未生成=空图 / 生成后带 built_at / 再读是缓存）。

实测（临时库跑真实数据）：58 篇冷启动构建 1.4s，热缓存 33ms。

## 2026-10-17 · 项目空间收敛 + 全局 Leader 更名智研管家
分支 `chore/dead-code`，提交 `6f46007`（项目空间）+ 本次提交（更名）。合并后 PR 一次讲清两条。

### 1. 项目空间去掉「团队」「日程」

- `SP_TABS` 只剩 概览 / 文献 / 论文星图 / 笔记。
- 连带删除：团队整块（成员列表、派活、运行看板、3 秒自动刷新 `__tmTick`）、日历 tab（`spMonthEvents`、去日历入口）、概览第四张 KPI「本月日程」。
- 后端 `/api/team`、`calendar_events`、日历页本身都还在，只是项目空间不再挂入口。想恢复 = 把 `"team"` / `"calendar"` 加回 `SP_TABS` 并找回被删的两个分支。

### 2. 元信息窄卡删除，阶段与最晚修改时间并入页头卡

- 删掉「项目 ID / 阶段 / 更新」那张窄卡；项目 ID 不再在界面上出现（接口里仍返回）。
- 页头卡改成紧凑单行（视觉方案 A）：标题 + `Partner Agent` + 阶段 + 更新时间 + 两个按钮，次行放只读说明和项目切换下拉。
- 阶段只显示十步工作流里的**步骤名**（例：文章撰写），不带 N/10。
- `进入工作流 →` → `进入科研台 →`；`问全局 Leader →` → `问智研管家 →`。
- 概览 KPI 四卡 → 三卡：进度 / 文献 / 研究问题（阶段已上移，不再重复）。

### 3. 修掉工作流快照不显示十步的 bug

`S.stages` 从未被赋值（`refreshState` 只搬了 tasks/chats/models/events），导致：

- 工作流快照永远「还没有步骤记录」；
- 进度按 `stages.length || 7` 兜底成 `0/7 步完成`。

现在 `refreshState` 里补 `S.stages = st.stages || []`，快照列出十步，进度按十步算（例：`30% · 3/10 步完成`）。工作流快照 / 项目边界 / 步骤记录三块结构没动。

### 4. 全局 Leader 更名智研管家（全链路）

| 位置 | 改动 |
|---|---|
| `apps/desktop/web/index.html` | Agent 页副标题 `智研管家 · 跨项目`（2 处）、工作流页 `回首页智研管家 →`、提示语 |
| `packages/workspace-service/team.py` | `LEADER_NAME` / `LEADER_BRIEF` 改名；`ensure_leader` 读到旧名自动改写成新名（旧库不迁移也能自愈） |
| `packages/workspace-service/workflow.py` | 首页 Agent 的 system prompt 与跨项目提示语 |
| `tests/` | 断言跟着改；新增 `test_leader_old_name_migrated`（旧名迁移后仍只有 1 个 leader） |
| `README.md` / `docs/交接.md` | 文字口径同步 |

历史态文档 `doc/plan/research_workbench_source_reproduction_plan.md` 保留原名（记录当时的产品定义，不改历史）。

### 验证

- `PYTHONPATH= py -3.10 -m unittest tests.test_workflow tests.test_assistants tests.test_team` → 50 tests OK。
- 浏览器连真实服务（8765）核对：页头卡文案、4 个 tab、无项目 ID、KPI 三卡等宽、快照十步、控制台无报错。

### 影响面

- 只改 `apps/desktop/web/` → 窗口里 F5 即可。
- 改了 `.py`（team.py / workflow.py）→ **关窗重开**，否则团队页 leader 还是老名字。
