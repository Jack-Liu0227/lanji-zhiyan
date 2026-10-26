# 览籍智研

面向科研人员/课题组的**证据优先**科研工作台：选题 → 检索 → 精读 → 梳理 → 写作 → 投稿一条链，AI 只出草稿，每一步都留证据、都能人工复核。

形态：**本机 Python + 本地网页界面**（无边框桌面窗，WebView2）。全部数据在自己机器上的 `data/research.sqlite3`，不内置任何人的 API key。

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

**下载安装**（GitHub Releases）：<https://github.com/Jack-Liu0227/lanji-zhiyan/releases/latest>。安装包统一命名为 `览籍智研.exe`，免安装包为 `览籍智研-免安装版.zip`，文件名不带版本号。免安装包解压后双击里面的 `览籍智研.exe`。

**自己打包**：使用 Python 3.10 或更新版本，已验证 3.13.5。先在选中的环境执行 `python -m pip install -r requirements-build.txt`，再运行 `tools\build-app.bat`。脚本优先使用项目 `.venv`，随后检查 `py -3` 和 PATH 中的 Python；同一解释器完成程序、安装器和验收。自动调用时可加 `--no-pause`。

构建结束后，`dist\览籍智研\` 是完整程序目录，`dist\release\` 包含 `览籍智研.exe`、`览籍智研-免安装版.zip` 和 `SHA256SUMS.txt` 校验文件。程序无需目标机器安装 Python，桌面窗口使用 Windows WebView2 运行时。

构建会先在源码目录之外启动程序，再实测安装器的全新安装和重复安装，检查数据库迁移、HTTP 服务、静态资源、旧程序文件清理和数据保留。任何一步失败都会停止构建。也可单独执行 `python tools\smoke_package.py "dist\览籍智研"` 或 `python tools\smoke_package.py "dist\览籍智研.exe"`。

---

## 二、十步工作流（每一步一个独立功能区）

| # | 步骤 | 做什么 | 左边 | 右边 |
|---|---|---|---|---|
| 1 | 🔍 创新点审查 | 受限对话，注入本机博导 skill 做 F1-F10 审计 + 五维评分 | — | 对话 + 轨迹 |
| 2 | 📚 论文搜索 | OpenAlex / Crossref / arXiv 三源检索，去重、排序、年份区间筛选 | 结果表（可勾选） | — |
| 3 | ⬇️ 下载精读 | 只下 OA 全文 → 入库；下不到的进「待手动下载清单」；一键精读（后台任务、可停） | 清单 + 计数 | — |
| 4 | 🌐 论文翻译 | PDF 文字层 + 缩放/页码/荧光笔；中英互译、笔记模式（全屏 2/3 中文 + 1/3 笔记） | PDF / 双语 / 笔记 | — |
| 5 | 🗂 确认大纲 | 框架 markdown（大标题 + 小标题 + 一句说明）就地改 | 框架 | 框架 agent |
| 6 | 🧩 梳理精读 | **谱系图**：每个小标题 → 精读结论 / 笔记 / 实验数据（脑图，实时） | 谱系图（可拖文件上传数据） | 结构 agent |
| 7 | ✍️ 文章撰写 | 按框架写正文，缺数据写「待补」；注入本机 paper-spine / paper-writer | 正文 | 写作 agent |
| 8 | 🧐 稿件审查 | 审稿人视角；左边可荧光笔标细节，标完一键发给 agent | 正文（可标注） | 审查 agent |
| 9 | 🌍 中译英 | md→md 分片并发 | 英文（可改可存） | 中文 |
| 10 | 🔄 格式转换 | Word / LaTeX(.tex→PDF，本机 MiKTeX) / Markdown / HTML | 文件区 | 代码区 + PDF 预览 |

其余页面：**Agent**（智研管家，不绑项目）、**助手**（我的/公开，规则进 system）、**团队**（2 固定 + 3 机动，成员从助手里选，一轮一个自动接力）、**日历**、**项目**、**项目空间**（概览/团队/文献/笔记/日程）、**定时任务**、**知识星图**（知识库 → 项目 → 五簇分支 = 论文 / 关键词 / 笔记 / 检索 / 工作流；点项目进分支、点簇扇出到条目、滚轮翻页、关键词可出 AI 简介，只在深色模式显示）、**知识库**（接 Obsidian，只改 frontmatter）、**笔记**（Markdown 编辑器 + 专注/窄栏）、**情报日报**（RSS）、**邮箱**、**设置**（主题色 / 模型列表 / Tool / Skill / MCP）。

**引用方式**：正文里**不写** `[1]` 标号；引用位置 + 文献信息记在后台的 `<项目>/论文正文/引用台账.md`（工具 `cite_add` 登记，随正文改动同步状态）。

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

- **40 家 Provider 目录**（`packages/contracts/providers_catalog.json`）：卡片制，填 Key → 发现模型/手填 → 采用 → 点 chip 设为默认。Key 不回显（服务端只回 `key_present`）。
- 本机只实现 **`openai-completions`** 这一条协议；另 20 家能配置、能测、卡片标「协议未接」，设默认会被拦住。
- **助手 = 一个接进来的模型**：绑了助手就用它接的模型（Agent 页 / 十步 / 团队接力全认）。
- **Agent 工具**：`library_overview` `knowledge_query` `calendar_add` `idea_review` `paper_search` `paper_download` `paper_read` `notes_compose` `draft_write` `draft_review` `format_convert` + 团队 `team_board` `team_assign`；另有按步限定的作用域工具（大纲/撰写/审查的 `doc_write`/`doc_read`/`cite_add`，梳理精读的 `map_write`/`map_read`/`map_sources`）。
- 「OAuth / 订阅登录」只做**打开厂商控制台 + 回贴凭据**，不做自动设备码流（各家要自己的 client_id）。

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
| `packages/workspace-service/workflow.py` | 十步工作流、三源检索、OA 下载、精读、翻译、大纲/撰写/审查/谱系、引用台账、中译英、格式转换、日历、模型档案、Agent 工具循环（含子 agent 委派）、项目记忆与六子目录 |
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
