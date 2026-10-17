# 改动记录

> 一轮改动一条，给 PR / 交接用。只写「改了什么、影响谁、怎么验」。

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
