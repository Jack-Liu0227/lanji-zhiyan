<p align="center">
  <img src="apps/desktop/web/mark.png" width="72" alt="览籍智研图标">
</p>

<h1 align="center">览籍智研</h1>

<p align="center"><strong>为课题定制研究流程，用专家团队协作管理科研全过程</strong></p>

面向科研人员和课题组的**本机科研工作台**。以项目为中心，自由设计适合课题的工作流，组织专业 Agent 团队，集中管理文献、证据、笔记、任务与论文产物。AI 提供候选与草稿，研究者保留编辑、审查和交付的决定权。

形态：**本机 Python + 本地网页界面**（无边框桌面窗，WebView2）。全部数据在自己机器上的 `data/research.sqlite3`，不内置任何人的 API key。

## 研究流程由你定义

- **个性化工作流**：从预设流程开始，也可新建或复制布局，按研究目标增删阶段、调整顺序和依赖；每个节点可独立配置任务说明、专家、团队成员、Tool、Skill 与人工审查。
- **专家团队协作**：创建和编辑不同研究方向的助手，按项目把专家组成团队，由 Leader 分配检索、证据整理、结构梳理、写作与审查任务。节点之间传递实际产物和人工反馈，支持验收、退回和重试。
- **项目化全流程管理**：研究问题、文献、PDF、引用证据、笔记、待办、日历、定时任务和论文产物关联到项目，来源和修改记录可追溯。
- **界面与内容可个性化**：自定义主题色和字体；编辑提示词模板、笔记分类与标签；大纲和正文支持直接修改、双栏校对与差异比较。

<table>
  <tr>
    <td align="center" width="33%"><img src="doc/screenshots/settings-workflows.png" width="100%" alt="个性化工作流编辑器"><br><strong>按课题设计流程</strong><br>编辑阶段、依赖、执行角色和审查规则。</td>
    <td align="center" width="33%"><img src="doc/screenshots/team.png" width="100%" alt="专家团队协作界面"><br><strong>组织专家团队</strong><br>由 Leader 协调不同专长的助手分工。</td>
    <td align="center" width="33%"><img src="doc/screenshots/project-space.png" width="100%" alt="项目空间全流程管理"><br><strong>项目化管理研究</strong><br>在项目空间汇总进度、文献、星图和笔记。</td>
  </tr>
</table>

---

## 一、安装（Windows）

**直发（现在这条路）**：拿 `览籍智研.exe`（单文件安装器）双击即可，程序装到 `%LOCALAPPDATA%\LanjiZhiyan\app`，桌面建快捷方式并启动。数据保存在 `%LOCALAPPDATA%\览籍智研`，重装保留数据库、PDF、笔记与配置。安装器先校验和复制完整程序，失败时保留原来的安装。

**从源码跑（开发）**：

```powershell
cd research-workbench
tools\start.bat                      # 自动选择本机 Python，打开无边框桌面窗口
python apps\desktop\server.py --port 8765 --no-open   # 只起服务，自己开浏览器
```

源码运行需要 **Python 3.10 或更新版本**（已验证 3.13.5）。启动脚本依次检查项目 `.venv`、旧版默认安装位置的 3.10、`py -3` 和 PATH 中的 `python`，并使用同一环境的 `pythonw.exe` 开窗。第三方依赖见 `requirements.txt`（全部 BSD/MIT/PSF，无 AGPL），需安装在选中的 Python 环境中。

如果启动检查失败，脚本会显示错误并暂停，详细信息保存在 `data/startup.log`；服务启动后的异常见 `%LOCALAPPDATA%\览籍智研\data\cache\server-error.log`。按错误提示，用选中的 Python 执行 `-m pip install -r requirements.txt` 修复依赖。

**下载安装 0.3.0**（[GitHub Release](https://github.com/Jack-Liu0227/lanji-zhiyan/releases/tag/v0.3.0)）：[Windows 安装包](https://github.com/Jack-Liu0227/lanji-zhiyan/releases/download/v0.3.0/LanjiZhiyan-0.3.0-Setup.exe)；[免安装版](https://github.com/Jack-Liu0227/lanji-zhiyan/releases/download/v0.3.0/LanjiZhiyan-0.3.0-Portable.zip)。免安装包解压后双击里面的 `览籍智研.exe`。

**自己打包**：使用 Python 3.10 或更新版本，已验证 3.13.5。先在选中的环境执行 `python -m pip install -r requirements-build.txt`，再运行 `tools\build-app.bat`。脚本优先使用项目 `.venv`，随后检查 `py -3` 和 PATH 中的 Python；同一解释器完成程序、安装器和验收。自动调用时可加 `--no-pause`。

构建结束后，`dist\览籍智研\` 是完整程序目录，`dist\release\` 包含 `览籍智研.exe`、`览籍智研-免安装版.zip` 和 `SHA256SUMS.txt` 校验文件。程序无需目标机器安装 Python，桌面窗口使用 Windows WebView2 运行时。

构建会先在源码目录之外启动程序，再实测安装器的全新安装和重复安装，检查数据库迁移、HTTP 服务、静态资源、旧程序文件清理和数据保留。任何一步失败都会停止构建。也可单独执行 `python tools\smoke_package.py "dist\览籍智研"` 或 `python tools\smoke_package.py "dist\览籍智研.exe"`。

---

## 二、个性化科研工作流

工作台覆盖研究问题梳理、文献检索与精读、PDF 翻译、证据关系整理、提纲和正文编辑、稿件审查、英文翻译及格式转换。它们是可组合的研究能力：根据课题选择需要的环节，编辑前后依赖与执行角色，不必照固定顺序完成。

在“设置 → 工作流”中可以新建或复制工作流，调整节点名称、顺序、依赖关系和任务说明，并为每个节点选择助手、团队或成员、Tool、Skill 与审查方式。运行时，前序交付和人工意见会进入后续节点；可按节点验收、退回修改或重试。

主导航有 **12 个页面入口**：**智研管家**（跨项目对话）、**专家**（创建与管理助手）、**团队**（Leader 组织成员按任务协作）、**日历**、**项目管理**、**项目空间**（概览/进度计划/文献/论文星图/笔记）、**知识库**（知识星图与授权知识源）、**科研平台**、**定时任务**、**情报日报**（RSS）、**邮箱**和**设置**（主题色/模型/权限/Tool/Skill/MCP/工作流等）。下面的页面图鉴包含主导航、项目空间子页、设置面板和科研平台各功能页面的截图与说明。

项目笔记默认五大类，支持自定义两级分类、可搜索的多选标签、批量整理和 Agent 提示词模板引用。操作说明见 [项目笔记与提示词模板](doc/notebook-templates.md)。

**引用方式**：正文里**不写** `[1]` 标号；引用位置 + 文献信息记在后台的 `<项目>/论文正文/引用台账.md`（工具 `cite_add` 登记，随正文改动同步状态）。

---

### 页面图鉴与截图

截图由浏览器自动打开当前源码版软件后采集，使用隔离的演示工作区。图中课题与任务为示例数据，不含个人研究数据库、邮箱内容或模型密钥。

#### 主导航页面（12 页）

| 页面与说明 | 页面与说明 |
|---|---|
| **智研管家**<br>围绕课题进行对话，查看工具轨迹、研究记忆和输出产物。<br><img src="doc/screenshots/agent.png" width="100%" alt="智研管家对话页面"> | **专家**<br>创建、编辑、启停领域助手，并配置模型与能力。<br><img src="doc/screenshots/experts.png" width="100%" alt="专家管理页面"> |
| **团队**<br>查看 Leader 与专业成员编组，为文献研究、写作等任务分工。<br><img src="doc/screenshots/team.png" width="100%" alt="多 Agent 团队协作页面"> | **日历**<br>集中查看日程、项目截止、定时运行和待审工作流。<br><img src="doc/screenshots/calendar.png" width="100%" alt="科研日历页面"> |
| **项目管理**<br>按课题登记研究问题、关键词和项目工作区。<br><img src="doc/screenshots/projects.png" width="100%" alt="项目管理页面"> | **项目空间**<br>汇总项目概览、流程、文献、论文星图和笔记入口。<br><img src="doc/screenshots/project-space.png" width="100%" alt="项目空间概览页面"> |
| **知识库**<br>通过知识星图浏览项目及相关论文、关键词、笔记和检索条目。<br><img src="doc/screenshots/knowledge-base.png" width="100%" alt="知识星图页面"> | **科研平台**<br>进入可配置流程，查看阶段、项目资料和 Agent 协作面板。<br><img src="doc/screenshots/research-workflow.png" width="100%" alt="科研平台工作流页面"> |
| **定时任务**<br>配置周期研究任务，查看运行记录与归档结果。<br><img src="doc/screenshots/scheduled-tasks.png" width="100%" alt="定时任务管理页面"> | **情报日报**<br>聚合 RSS 研究资讯，按来源、年份和关键词筛选。<br><img src="doc/screenshots/research-briefing.png" width="100%" alt="情报日报页面"> |
| **邮箱**<br>可选连接多个科研邮箱，集中阅读和处理邮件。<br><img src="doc/screenshots/mail.png" width="100%" alt="邮箱管理页面"> | **设置**<br>集中管理主题、模型、权限、工具、技能、MCP 与外部连接。<br><img src="doc/screenshots/settings.png" width="100%" alt="应用设置页面"> |

#### 项目空间子页面

| 页面与说明 | 页面与说明 |
|---|---|
| **进度与计划**<br>跟踪阶段交付、待办、审核状态和 Agent 协作进展。<br><img src="doc/screenshots/project-space-plan.png" width="100%" alt="项目进度与计划页面"> | **项目文献**<br>查看当前项目的题录，并从 Zotero 导入文献。<br><img src="doc/screenshots/project-space-literature.png" width="100%" alt="项目文献页面"> |
| **论文星图**<br>从项目文献生成关系视图，探索论文之间的关联。<br><img src="doc/screenshots/project-space-papergraph.png" width="100%" alt="项目论文星图页面"> | **项目笔记**<br>编辑 Markdown 笔记，管理分类、标签和提示词模板。<br><img src="doc/screenshots/project-space-notes.png" width="100%" alt="项目笔记页面"> |

#### 设置面板

| 页面与说明 | 页面与说明 |
|---|---|
| **主题与字体**<br>切换明暗主题、预设或自定义颜色，并调整字体大小。<br><img src="doc/screenshots/settings-theme.png" width="100%" alt="主题和字体设置"> | **权限**<br>设置文件操作确认方式与模型上下文预算。<br><img src="doc/screenshots/settings-perm.png" width="100%" alt="权限设置页面"> |
| **模型列表**<br>配置 Provider、模型与登录方式，供助手和工作流选择。<br><img src="doc/screenshots/settings-models.png" width="100%" alt="模型列表设置"> | **Tool**<br>查看 Agent 工具目录与专家启用矩阵。<br><img src="doc/screenshots/settings-tools.png" width="100%" alt="Agent 工具设置"> |
| **Skill**<br>浏览全局及项目技能，为不同专家配置可用能力。<br><img src="doc/screenshots/settings-skills.png" width="100%" alt="Skill 设置页面"> | **MCP**<br>管理本机 MCP 服务与 Agent 的访问范围。<br><img src="doc/screenshots/settings-mcp.png" width="100%" alt="MCP 设置页面"> |
| **工作流定义**<br>创建或复制流程，编辑节点、前置依赖、顺序、执行角色与人工审查。<br><img src="doc/screenshots/settings-workflows.png" width="100%" alt="可编辑的工作流定义页面"> | **easyScholar 分区**<br>配置期刊等级与分区查询服务。<br><img src="doc/screenshots/settings-easyscholar.png" width="100%" alt="easyScholar 设置页面"> |
| **Zotero**<br>配置题录导入与项目 Collection 同步。<br><img src="doc/screenshots/settings-zotero.png" width="100%" alt="Zotero 设置页面"> | **飞书机器人**<br>按助手配置飞书连接与消息通知。<br><img src="doc/screenshots/settings-feishu.png" width="100%" alt="飞书机器人设置页面"> |

<details>
<summary>可组合的研究阶段界面示例</summary>

以下截图展示工作台内可按项目组合的研究功能。可通过工作流编辑器选择所需能力并设置顺序、负责人和审查方式。

| 功能页面与说明 | 功能页面与说明 |
|---|---|
| **创新点审查**<br>整理研究问题、约束与创新性判断，结论可人工修改。<br><img src="doc/screenshots/workflow-01-idea.png" width="100%" alt="创新点审查页面"> | **论文搜索**<br>检索开放学术来源，筛选并整理候选文献。<br><img src="doc/screenshots/workflow-02-search.png" width="100%" alt="论文搜索页面"> |
| **下载精读**<br>管理开放获取全文和精读任务。<br><img src="doc/screenshots/workflow-03-download.png" width="100%" alt="下载精读页面"> | **论文翻译**<br>阅读 PDF 双语文本并记录翻译笔记。<br><img src="doc/screenshots/workflow-04-notes.png" width="100%" alt="论文翻译页面"> |
| **确认大纲**<br>编辑章节结构与每节说明，确认后进入写作。<br><img src="doc/screenshots/workflow-05-outline.png" width="100%" alt="确认大纲页面"> | **梳理精读**<br>将文献结论、实验数据与大纲建立可追溯关联。<br><img src="doc/screenshots/workflow-06-map.png" width="100%" alt="梳理精读页面"> |
| **文章撰写**<br>按已确认框架写作，缺少证据的位置保留待补标记。<br><img src="doc/screenshots/workflow-07-write.png" width="100%" alt="文章撰写页面"> | **稿件审查**<br>标注问题并提交审稿意见，可退回修改。<br><img src="doc/screenshots/workflow-08-review.png" width="100%" alt="稿件审查页面"> |
| **中译英**<br>分段翻译 Markdown，保留可编辑的中英文内容。<br><img src="doc/screenshots/workflow-09-entrans.png" width="100%" alt="中译英页面"> | **格式转换**<br>将论文转换为 Word、LaTeX、Markdown 或 HTML。<br><img src="doc/screenshots/workflow-10-convert.png" width="100%" alt="格式转换页面"> |

</details>

---

## 三、怎么验（改完必跑）

```bash
tools\check.bat                                 # 一条命令跑完：Python 自检 + 前端主题自检 + 悬空 id 体检 + JS 语法
python -m unittest discover -s tests -t .    # Python 自检（等价的第一条）
node apps/desktop/web/theme.test.mjs            # 主题配色（HSL 往返 / 明暗推导 / 对比度 ≥ AA）
node apps/desktop/web/ids.test.mjs              # 悬空 id（$("x") 引用了页面里没有的 id）
```

> **改前端别只看「语法过没过」**：单文件前端里一句**顶层** `$("x").onclick = …` 撞上已不存在的 id 就会抛错，它后面所有初始化一行都不跑 —— 页面变成空壳，而 HTTP 状态、窗口标题、单测全绿。收尾请用 CDP 检查侧栏初始化、页面切换和 JavaScript 错误。当前侧栏有 12 个 `[data-nav]` 页面入口。

改完 `.py` **必须关窗重开**（窗口就是服务进程，关窗即退）；只改 `apps/desktop/web/` 按 F5 即可。

验 UI 的套路（不碰真库）：起临时实例 `LANJI_ZHIYAN_DB="C:/Users/<你>/AppData/Local/Temp/x.sqlite3" py -3.10 apps/desktop/server.py --port 8799 --no-open`，再用无头 Edge + CDP 驱动页面读数/截图。**注意别用 `/tmp/...`**：MSYS 的 `/tmp` 不会被翻译，原生 Python 会当成 `C:	mp\`，库就建到别处去了。

---

## 四、模型与 Agent

- **Provider 目录**（`packages/contracts/providers_catalog.json` 与账号 Provider）：支持 API Key，以及 Codex、Claude Code、Antigravity 官方本机账号登录。配置后采用模型，可供助手、团队成员和工作流使用。
- 已实现 OpenAI Chat Completions、Responses、Anthropic Messages、Google Gemini，以及官方账号 CLI 适配；其余协议会明确标注尚不支持运行。
- **助手 = 一个接进来的模型**：绑定助手后，智研管家、科研平台和团队成员都使用其配置的模型。
- **Agent 工具**：`library_overview` `knowledge_query` `calendar_add` `idea_review` `paper_search` `paper_download` `paper_read` `notes_compose` `draft_write` `draft_review` `format_convert` + 团队 `team_board` `team_assign`；另有按步限定的作用域工具（大纲/撰写/审查的 `doc_write`/`doc_read`/`cite_add`，梳理精读的 `map_write`/`map_read`/`map_sources`）。
- 账号登录复用官方客户端会话，不复制账号令牌；提供登录与状态检测入口。

工作流按依赖顺序传递上游交付、项目记忆与人工反馈，支持保存工具进度后续跑；节点默认需要人工审查，可按节点关闭。定时任务可启动完整工作流，全部交付后归档项目笔记。本机 Zotero 同步与 MCP 共用本机授权，无需云端 Key；项目标签支持批量解除和恢复关联。顶栏集中处理待审节点，日历联动截止日期、定时运行、结果笔记与审查提醒。配置说明见 [账号认证与项目工作流联动](doc/connected-research.md)。

---

## 五、边界（诚实标注）

- 证据抽取是**候选**不是事实：页码或原文对不上的一律拒收，仍需人工审查。
- 只下**开放获取**全文，不绕付费墙；出版社的 OA 直链（Wiley/RSC 等）实测 403，要人工。
- 模型产出（审查/撰写/精读）都是**草稿**。
- 定时任务**依赖进程**：关掉应用期间不跑，重开最多补跑一次；预检不过标「阻断」，不产产物、不伪装成功。
- 团队接力是**串行**（一轮一个成员），不是真并行。
- 知识库只碰授权库里的 `.md`，**永不写** `.obsidian/`，只改 frontmatter 那几个键。

---

## 六、目录

| 文件 | 干什么 |
|---|---|
| `apps/desktop/server.py` | 纯标准库 HTTP 服务（ThreadingHTTPServer）：静态页 + JSON API + `/api/agent` 等 SSE；启动即开无边框窗。**桌面快捷方式直接指这个文件** |
| `packages/workspace-service/workflow.py` | 可配置科研工作流、三源检索、OA 下载、精读、翻译、大纲/撰写/审查/谱系、引用台账、中译英、格式转换、日历、模型档案、Agent 工具循环（含子 agent 委派）、项目记忆与六子目录 |
| `packages/database/research_core.py` | 核心库：SQLite、任务、PDF 入库（SHA256 去重/逐页文本）、证据闸门、导出 |
| `packages/workspace-service/` | 工作流、团队、定时任务、论文翻译、项目待办、扩展路由（`feature_routes.py`） |
| `packages/connectors/` | Zotero、Obsidian、邮箱、RSS、easyScholar、飞书等外部连接器 |
| `apps/desktop/web/` | 单文件前端 `index.html`（CSS/JS 全内联）+ `vendor/`（marked / DOMPurify / KaTeX / pdf.js / lunar / starmap / neural）+ 主题自检 |
| `tests/` | 单元测试与浏览器回归（`python -m unittest discover -s tests -t .`） |
| `tools/` | `check.bat` / `build-app.bat` / `start.bat` / `*.spec` / `install.ps1` / `installer.py` / `hooks/` / `build/icon.ico`（打包与自检都在这里） |
| `packages/` | 分包布局：`database` / `workspace-service` / `workspace-core` / `connectors` / `contracts` / `ai-runtime` |
| `doc/` | 设计、计划、诊断与改动记录（`doc/CHANGELOG.md`） |
| `data/` | 全部数据（`research.sqlite3` + `papers/` `notes/` `out/` `uploads/`） |
| `交接.md` | 给下一个会话的交接（现状 / 坑 / 待办） |
