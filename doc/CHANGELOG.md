# 改动记录

> 一轮改动一条，给 PR / 交接用。只写「改了什么、影响谁、怎么验」。

## 2026-10-24（1.1.3：打包、安装与启动验收）

- 冻结程序通过 PyInstaller 的 `_MEIPASS` 定位资源，避免误用上级源码目录中的迁移与模型目录。缺少迁移脚本时直接报错，停止创建不完整的数据库。
- 打包脚本使用同一 Python 环境构建程序与安装器，支持 Python 3.10 及更新版本；新增 `requirements-build.txt`。安装包固定命名为 `览籍智研.exe`，免安装包为 `览籍智研-免安装版.zip`，文件名不带版本号，并生成 SHA256 校验文件。
- 安装器先验证全部文件、复制到临时目录，再替换现有程序；复制或替换失败保留旧版。重复安装清除过期程序文件，用户数据独立保留。快捷方式使用 Windows 实际桌面目录，安装错误返回非零退出码，支持无人值守安装。
- 修正中文目录重启判断：只检查目录，避免中文 exe 文件名造成多余重启；不同免安装目录使用独立的目录联接。
- 验证命令：`tools\build-app.bat --no-pause`。自动验收在源码树外、不含 Python 的 PATH 下进行，检查全新安装、全部迁移、模型目录、HTTP 服务、前端资源、重复安装与数据保留。桌面窗口另通过 CDP 检查原生桥接与全部页面入口。

## 2026-10-24（1.1.2：修「装了但没窗口」—— .NET 认不得中文安装路径）

**问题**：装出来的包双击后没有无边框窗口，只在 Edge 里开个标签页。`data/cache/server-error.log` 里是两层：
- `Failed to resolve Python.Runtime.Loader.Initialize`（pythonnet 加载 `Python.Runtime.dll`）
- `FileLoadException ... 尝试从一个网络位置加载程序集`（pywebview 加载 `Microsoft.Web.WebView2.Core.dll`）

根因：`.NET Framework` 侧把**带中文的路径**当成网络位置/沙箱目标拒掉。程序装在 `%LOCALAPPDATA%\览籍智研\app`（中文），源码跑在 `site-packages`（ASCII）所以从来不复现。同一份 exe 复制到纯 ASCII 目录就能起窗，中文目录就起不来 —— 实测对照过。

**修法两条**：
1. 安装器把**程序**装到纯 ASCII 的 `%LOCALAPPDATA%\LanjiZhiyan\app`，数据根仍是 `%LOCALAPPDATA%\览籍智研`（老数据不动），并清掉数据根下残留的旧 `app/`。
2. `server.py` 加 junction 兜底：免安装版被解压到中文目录时，启动先在 `%LOCALAPPDATA%\Lanji-run` 建目录联接再从那条路径重启自己（`sys.executable` 与模块 `__file__` 全变 ASCII）；建不出来就照旧退回浏览器。

**验证**：装好后 `MainWindowHandle` 非零且窗口可见（标题 `览籍智研 · v<戳>`），`/`=200、`/api/state`=200；免安装版放在中文目录里也走 junction 重启后出窗。


## 2026-10-24（1.1.1：修打包漏 migrations/ —— 装完打开是白屏）

**问题**：1.1 的 Setup/Portable 里没有 `migrations/`。`run_migrations()` 找不到目录就 `return []`，不报错；新机器上 `task_links`、`project_todos` 关联表一类的表建不出来，`/api/state` 直接 500（`OperationalError: no such table: task_links`），浏览器里就是打不开的白屏。从源码跑不触发，因为仓库根就有 `migrations/`。

**做法**：`tools/览籍智研.spec` 的 datas 补一条 `(migrations, 'migrations')` —— 冻结包里迁移脚本落在 exe 同目录，`app_dir()/migrations` 才找得到。

**验证**：重打后清空数据根、起 exe，`/api/state` 200，`research.sqlite3` 里 63+ 表且 `task_links` 存在；老数据根（已应用 010 迁移）照常。

## 2026-10-24（1.1：引用导出成形、笔记树、工作流验收与并发加固）

**引用导出**：PDF / Word / HTML / Markdown 四个出口都带引用 —— PDF 走 `\cite` + 参考文献表（样式可选），其余三出口把 `{{Cn}}` 换成按正文出现顺序编的 `[n]` 并自动接「参考文献」节，台账里查不到的编号保留原标记并在 `cites.missing` 里报出来。著者-出版年制照 GB/T 7714 自带 `.bst` 的实测规则实现（1–3 位全列、≥4 位用「等」、同年加 a/b、文后按著者字顺），PDF 端 AY 样式明确退回数字式并给出说明。

**工作流**：上一步没交付就只是独立功能块（硬闸）；确认交付时把该步产物落库（`steps.delivery`）；工作流验收、任务执行与持久化恢复打通，补上 artifact / 调度 / 数据一致性的审计修复。

**笔记与对话**：笔记支持多级树状目录（新建文件夹、递归展示、删除）并重排了笔记空间布局；历史对话侧栏可折叠，状态存 localStorage。

**数据库**：SQLite 开 WAL、`busy_timeout` 60s、`synchronous=NORMAL`；Schema 初始化加线程锁，`/api/task` 加锁冲突重试 —— 解决多 agent 并发下的 database locked。

**规范化**：各阶段文本统一命名（文献检索、文献入库、文献精读、拟定框架、证据链谱系、论文撰写、AI预审查、学术英译、格式排版）。

**验证**：`tests/` 新增 `test_workflow_coordination / test_workflow_runtime / test_audit_regressions / test_followup_audit / test_agent_jobs` 等；打包后的 exe 起在空闲端口 curl 到 200。

## 2026-10-23（补上著者-出版年制：不自己定规则，照 GB/T 7714 的实现来）

**问题**：上一版 Word 只有顺序编码（正文 [n]）。著者-年份那套要定「几位作者开始用等、同年怎么加 a/b、文后按什么排」这些细则，不该我拍脑袋。

**做法**：跑本机 MiKTeX 里 GB/T 7714 自带的 `gbt7714-author-year.bst`，读它吐出来的 `\bibitem[…]` 标签和条目正文 —— **标准自己的实现就是规则**。实测结论并落进代码：
- 正文 1–3 位著者全列、≥4 位用「等」（中文）/「et al.」（西文）；中文名字之间用「、」、西文用「, 」；
- 同一著者同年 → 年 + a/b/c，组内按题名排；
- 缺年份按 `n.d.`；
- 文后按**著者字顺**排（不是出现顺序），条目形如 `Smith J, Lee A. 2020. 题名[J]. 出处: 页. DOI: …`。
（`AY_ETAL_FROM = 4` 是唯一可调的阈值，想改成 3 位就改这一个常量。）

**样式清单**：`cite_style` 加 `gbt7714-ay`（GB/T 7714 著者-出版年制）与 `apa`（APA 风格）；格式助手第 4 问的清单自动带上，标注了「Word/HTML/MD 生效，PDF 仍数字式」。

**PDF 为什么没跟着走著者-出版年**：实测两条路都不对 —— 装 `gbt7714` 宏包时 natbib 会把 cite 翻成数字式（标签变成 `{{1}{2020}…`）；不装宏包时标签对了但被排成方括号 `[Smith(2020)]`，且 .bbl 里的 `\doi`/`\cstr` 会 Undefined control。所以 PDF 端 AY 样式**明确退回数字式并在 `cite_note` 里说明**，不糊一个半对的输出。

**落点**：`workflow_converters.py` 新增 `is_author_year()` / `AY_STYLES` / `AY_ETAL_FROM` / `_ay_label()` / `_ay_ref()` / `_author_list()` / `_surname()`，`doc_cites(md, cite_style)` 分两种口径；`bib_style` 对 AY 样式退数字式并带说明；`workflow_docx.py` 参考文献章节下**每一行都按条目排**（原来只认 `[n]` 开头，著者-出版年制进不去）。

**验证**：`tests/test_cite_tex.py` 新增 3 条（著者字顺、同年 a/b、≥4 位用等），共 9 条；端到端（临时 HOME）`conv_run(fmt="docx", cite_style="gbt7714-ay")` → 正文 `(Smith, Lee, 2020)` / `(Chen, 2021)`、文后 Chen 在前 Smith 在后、全篇没有 `[n]`。

## 2026-10-23（Word / HTML / Markdown 导出也带引用）

**口径**：正文里的 {{Cn}} 标记在 Word/HTML/Markdown 里换成 `[n]`，编号**按正文出现顺序**给（同一篇复用同一个号），文末自动接一节「参考文献」，条目按 GB/T 7714 顺序编码的样子由台账字段排：`[1] 作者. 题名[J]. 出处, 年: 页. DOI: …`。台账里找不到的编号**保留 `{{Cn}}` 原样**并在返回的 `cites.missing` 里报出来，不悄悄丢。

**为什么不走 bibtex**：docx 那条链是 python-docx 直接排，没有 .bst 可言；引用条目只用台账字段拼（authors/title/venue/year/pages/doi），PDF 那条路仍然走 `\cite` + `bibliographystyle`。两者编号口径一致（顺序编码、同篇同号）。

**落点**：`workflow_converters.doc_cites()`（标记→[n] + 条目）/ `with_references()`（末尾接参考文献节）/ `_ref_line()`（题录排版），`conv_run` 在 md/docx/doc/html 四个出口统一过一遍；返回里加 `cites {used, missing, style}` 与 `cite_note`。docx 渲染器本来就认「参考文献」这个标题、会把 `[n]` 开头的行做成悬挂缩进小字号，不用改。

**验证**：`tests/test_cite_tex.py` 加 3 条（按出现顺序编号、台账没有的编号保留标记、真建一份 docx 再用 python-docx 读回来断言正文有 `[1]`、文末有「参考文献」和 DOI）；端到端（临时 HOME）：`conv_run(fmt="docx")` → 同一篇三处引用全变 `[1]`、文末一条条目。

## 2026-10-23（PDF 真的带上引用：正文 {{C3}} → \cite，文末按样式出参考文献表）

**口径**：正文里用双花括号包 C 编号（例如 C 的第 3 条写成两个花括号夹 C3）标引用，导出 PDF/LaTeX 时换成 `\cite{key}`，文末挂 `\bibliographystyle` + `\bibliography{refs}`。样式**问使用者**：格式助手开篇第 4 问给可选清单，`gbt7714`（GB/T 7714 顺序编码，中文稿默认）/ `unsrt` / `plain` / `ieeetr` / `elsarticle` / `aps`，工具参数 `cite_style`。key 与元数据全部来自 Zotero（台账缺条就报 missing，不自己编题录），对不上的编号在 PDF 里原样留着，不悄悄丢掉。

**编译改成完整链**：xelatex →（.aux 里有 `\bibdata`/`\citation` 时）bibtex → xelatex ×2。以前只跑一遍 xelatex，带引用的稿子编号全是 `??`。

**实测踩出的五个坑（都已修）**：
1. `gbt7714-numeric.bst` 的 .bbl 用 `\doi{}` / `\cstr{}`，不 `\usepackage{gbt7714}` 就 Undefined control sequence，整份编不出来 → 选顺序编码时自动加宏包。
2. 构建夹里用中文文件名 → bibtex 报 `I couldn't open file name '…aux'`、.bbl 是空的 → 构建一律 `paper.tex` / `refs.bib` 这类 ASCII 名，最终 PDF 再改回中文标题名（现成 .tex 是中文名时也先拷成 `paper.tex` 再编）。
3. 本机经典 `bibtex.exe`（连带 `miktex-bibtex.exe`）会 0xC0000374 堆损坏直接崩，`bibtex8.exe` / `bibtexu.exe` 正常 → 候选按可靠性排序挨个试，成功判据是「.bbl 真的生成了」而不是退出码（弃用样式名会给 rc=1，但 .bbl 是对的）。
4. 上一轮留下的 PDF 会被当成这一轮的产物（最后一趟 xelatex 崩了也不报）→ 编译前先删旧 PDF，并检查最后一趟的退出码。
5. `gbt7714-numerical` 已是弃用名，用 `gbt7714-numeric`。

**落点**：`workflow_converters.py`（`md_to_tex` 带 cite_keys/cite_style/cite_package；新增 `latex_chain` / `bib_style` / `_bibtex_candidates`；`cite_bib` 返回 C 编号→citekey 的 `keys`；`conv_run` 收 cite_style 并把 cites/passes/bibtex 报回去）、`apps/desktop/server.py`（宿主 `_compile_tex_host` 走同一条链）、`workflow.py`（format_convert 透传）、`workflow_prompts.py`（工具参数 cite_style）、`workflow_documents.py`（写作规则 8：写标记、不写 [1]）、`convert_system`（第 4 问 + 本机装了哪些 bst）。

**验证**：`tests/test_cite_tex.py` 3 条（真跑 MiKTeX 编一份带 `\cite` 的 tex，断言 .aux 出现 `\bibcite{…}{1}`、passes=3）；端到端（临时 HOME + Zotero 桩）：台账 C1 → refs.bib → `\cite{smith2020}` → `\usepackage{gbt7714}` → PDF 文本里 `[1]` 与参考文献题名都在。全量 446 tests。

## 2026-10-23（确认交付必须留下这一步的产物）

**口径**：交付不再是「点一下记个 done」。点「确认交付」时后端**现算**这一步到底产出了什么，跟台账一起落库 —— 全从库和文件里读，不采信模型自报（模型说「已交付 12 篇」不算数）。十步各有判据：创新点（原文/判定字数）、检索（结果条数+来源）、下载（入库篇数+最近三篇）、翻译笔记（条数）、大纲/正文/审查（文件名+字数+几节几小节）、谱系（节点/连线）、中译英（原文/译文字数）、格式转换（产物文件数+最新一份）。另存本轮提问数与该步最后一条助手回复（各 200 字 / 截断），事后能回答「这一步交付了什么、聊到哪」。

**落点**：`migrations/009_step_delivery.sql` 给 `steps` 加 `delivery`（JSON，老行为空串）；`workflow.step_delivery()` 现算；`/api/step/done` 写台账时同时写 delivery，并把 `summary` 从「用户确认交付」换成产物摘要（左栏 title / 时间线一眼看到）；前端交付成功后弹一句「已交付，产物已记下：…」，左栏该步 title 也带上产物摘要。

**注意**：`steps.log_step()` 原来按位置 `INSERT INTO steps VALUES (?,…)`，加列必然错位，已改成显式列名。迁移在真实库副本上预演过：458 行台账不丢、老行 `delivery=''`。

**验证**：`tests/test_step_gate.py` 6 条（新增「交付时产物落库」「产物读失败不连累交付」）；全量 443 tests；实跑 `/api/step/done` 返回 delivery JSON、`/api/task` 的 steps 里读得回来。

## 2026-10-23（工作流改成硬闸：上一步没交付，这一步只是独立功能块）

**口径**：流程步必须「上一步已经在台账里 done」。上一步没交付时，这一步不算流程里的一步 —— 右栏空态不再是「新会话已就绪」，换成一张「独立功能块」卡：写明挡在谁身上、给一个「去交付上一步」的按钮；左栏该步标 🔒 并在 title 里说明；底部「确认交付」置灰。工具本身照常能用（翻译、格式转换、检索都能直接问），只是不写「已同步进度」、不推进流程。

**落点**：`workflow.step_plan()` 把闸放在最前面，返回 `blocked/prev/prev_label`，既不调模型也不落库（以前空计划会被缓存住，上一步补完也不重写）；`/api/step/done` 跨步直接 409 —— 后端是权威闸，前端置灰只是外观。前端 `wfPrevBlocked()` 按 `stage_map` 判闸，`wfPaintMsgs` 空态先判闸，`renderWorkflow` 改成先拿到 `stage_map` 再画右栏（否则空台账会被误判成拦截）。

**注意**：`wfPlanLoad()`（右栏那张「已同步进度 + 本步计划」）现在没有任何调用点 —— 空态早就改成 hero 了，所以它只在历史消息里出现、新流程不会现写。这版没有恢复它，只把闸加在空态和交付上。

**验证**：`tests/test_step_gate.py` 4 条（含「中间跳步也被挡住」）；实跑服务：`/api/step/plan` 对 `write` 返回 `blocked:true, prev:"map"`，`/api/step/done` 跨步 409、按顺序 200；浏览器里点过：第 7 步出拦截卡 + 交付按钮停用 + 左栏 7 步带锁，交付第 1 步后自动跳到第 2 步且正常放行。

## 2026-10-23（览籍智研 1.0）

**定为 1.0**：此前对外只发过 0.2.0。功能面 = 0.2.0 全部内容 + 上一轮语义检索层，没有新增功能，只是给定型版本一个正式版本号。

**发布渠道**：资产挂到本仓库 Releases，命名为 `LanjiZhiyan-1.0-Setup.exe`（单文件安装器，双击即装、建桌面快捷方式）与 `LanjiZhiyan-1.0-Portable.zip`（免安装，解压双击 `览籍智研.exe`）。

**验证**：源码改动只有 `VERSION`、`README.md`、本文件。`dist/览籍智研/览籍智研.exe --port 8766` 返回 HTTP 200；安装器实跑复制 1561 个文件、桌面快捷方式生成、装好的 exe `--port 8767` 返回 HTTP 200；`setup` 与 `portable` 里的 exe 与界面文件 SHA-256 一致。

## 2026-10-22（语义检索层：论文全文进索引，knowledge_query 升成双路召回）

**0. 口径**：RAG 落地的判据是**命中能回指原文页码**，不是相似度好看。所以论文按页切块（页码跟着块走），每条命中带 `citation: 文件名 p.N`；不引 faiss/chroma，5800 块 × 1024 维全量装内存做暴力余弦，万级块下 <10ms。

**1. 原来缺的不是向量，是索引**：`asset_fts` 里只有 10 篇论文、88 条资产；`paper_pages` 的 1418 页 / 583 万字符**没有任何索引**，只有 9 条 evidence 摘录能被 `_knowledge_query` 的子串 `in` 扫到。所以先补 `migrations/008_semantic.sql`：`sem_chunks`（块+向量 BLOB+页码）、`sem_state`（内容哈希台账）、`sem_fts`（FTS5 **trigram**，中文才切得动；`unicode61` 会把整段中文当一个 token —— 实测 `asset_fts` 就是这个毛病）。

**2. `packages/workspace-service/semantic.py`**：分块（按句子攒 900 字、尾部重叠 150，单句超长才硬切）→ 嵌入（OpenAI 兼容 `/embeddings`，默认本机 ollama `bge-m3`，可用 `LANJI_EMBED_BASE/MODEL/KEY` 或 `service_settings(service='semantic')` 换）→ 双路召回（trigram MATCH 带 bm25 / 2 字查询退回 LIKE 子串；向量余弦）→ RRF(k=60) 融合。索引是**派生数据**：内容哈希 + 模型名都没变就跳过，所以论文入库、笔记保存、证据摘录**都不用挂钩子**，下次 sync 自然补上。

**3. 接线**：`workflow._knowledge_query` 改走 `semantic.hybrid`，空结果/嵌入服务挂了自动退回原来的子串扫并标 `mode`（`hybrid`/`vector`/`lexical`）——语义层是增强不是前置依赖。新工具 `semantic_index`（`status`/`sync`，风险级 `local_execution`）。`server._semantic_boot()` 在服务起来后起一个后台线程预热（同一进程同库只起一次，`idle=0.01` 让位给 agent）。`knowledge_query` 的工具描述改成「论文全文+笔记+证据」，让它知道现在能搜原文。

**4. 坑（下次别重踩）**：FTS5 列写成 `UNINDEXED` 就等于没建索引 —— 第一版把正文放在 `UNINDEXED` 列里，MATCH 恒返 0；改成正常列 + rowid 对齐 `sem_chunks.rowid`。trigram 要求查询 ≥3 字符，2 字中文（「掺杂」「层状」）必须退回 LIKE。「铜掺杂 循环稳定性」整串 phrase 匹配是 0 命中，要拆词 OR。

**5. 没做**：交叉编码器重排、增量 upsert（现在是整源重嵌）、知识星图/前端检索页接入。等实测命中率再说。

## 2026-10-22（览籍智研 0.2.0）

**科研工作流**：新增项目文件夹与文件管理助手；撰写可按谱系图逐节起稿；补齐谱系图手建节点、资料节点换父级、图文件上传和格式转换流程。

**资料与协作**：升级全局/项目两层记忆；增强 Zotero 文献管理、引用台账导出与本地 PDF 打开；改进检索排序、跨源去重、飞书项目绑定、项目进度看板和阶段刷新。

**桌面体验与稳定性**：优化翻译、Word 导出、桌面窗口与启动隔离；修复 WebView2 本地请求、中文文件名预览和多项项目写入边界问题；清理旧路由、死代码与样式。

**验证**：`python -m pytest tests -q`（403 passed, 1 skipped）；`node apps/desktop/web/theme.test.mjs` 与 `node apps/desktop/web/ids.test.mjs` 均通过。

## 2026-10-22（结构整理：前端分目录 / workflow.py 拆库层 / server.py 分路由）

**0. 口径**：只搬不改 —— 三个提交里没有一条业务逻辑变更，全部以「404 测试全绿 + 真应用点一遍」为验收线；对外接口（`import workflow` 的 41 处、前端全局函数、所有 HTTP 路由）一律不动。

**1. 前端 `web/` 分家**：15 个视图模块 → `web/views/`，`app.css`/`kanban.css` → `web/css/`；`index.html` 18 处 `src`/`href` 改新路径。`ids.test.mjs` 改成**递归**扫 `web/`（不递归就一条 js 也扫不到、还照样打印 OK）；`theme.test.mjs` 的 `notes-and-settings.js` 与 `app.css` 路径同步；`tests/test_web.py` 加 `JS = WEB/"views"`。图片（`home.png`/`logo.png`/`mark.png`）**没动**：它们按 URL 被 server 的静态兜底逻辑引用（`/home.png` 找不到会退回 `tools/home/home.png`），搬了只添风险不添价值。

**2. `workflow.py` 拆库层**：2929 → 1379 行。常量 / `now()` / `PROJ_DIRS` / `STAGES` / `WORKFLOW_SCHEMA` / `class WorkflowDB` 整块进 `workflow_db.py`（单向依赖：db 不 import workflow），`workflow.py` 顶部 `from workflow_db import (...)` 再导出。漏掉的名字靠「扫全仓 `workflow.X` 用法 vs 模块实际属性」脚本补齐（本次补回 `BUILTIN_ASSISTANTS`、`PROVIDER_CATALOG`）。没继续拆 `_tool_impl`：它是跨 60 多个符号的派发表，拆开反而要满屏 `workflow.` 前缀，收益不如风险。

**3. `server.py` 拆路由**：`do_GET` 的连续路由段 → `apps/desktop/handlers/{starmap,core,wf_routes,misc}.py` 四个混入类，`Handler` 多继承，`do_GET` 留派发循环。**新增 `self._responded`**：`_send`/`_sse`/`_stream_run` 直接写 `wfile`、`return` 的是 `None`，光看返回值判断不了「已回过包」，没有这个标志会把已回包的请求继续往下派、再撞一次 404。**`do_POST` 没动**：它的路由挂在共享的 `with WorkflowDB(...)` 块里，按域切要改作用域，那是重写不是搬运。

**4. 删掉根目录的旧 `CHANGELOG.md`**（1788B 的英文 asset-graph 版），只留 `doc/CHANGELOG.md`（README 本来就是指这里）。

**5. 验**：每个提交各自跑过 `404 tests OK (skipped=12)`；`node ids.test.mjs`/`theme.test.mjs` 通过；重启真应用后 9 个主页面逐个点开有内容、21 条 GET 路由全 200、`/api/nope` 仍 404。

## 2026-10-22（三栏页第 11 项「项目文件夹」+ 文件管理助手）

**0. 口径**：它是**文件夹**，不是第 11 步 —— 不进 `WF_ORDER`、不编号、不写 `steps` 表、没有「确认交付」。目的是「看文件 / 问文件 / 把文件交给某一步」都在程序内闭环，不用跳出到资源管理器。

**1. 实现**：`workflow_converters.tree_list`（一层清单，目录在前、文件按 mtime 倒序）、`file_hand`（把文件写进目标步 scope 的一条消息，**不**触发那步跑模型）、`files_system`（本栏助手 system：整夹可看、只读为主、要动手交给对应那一步）；新路由 `GET /api/tree?id=&rel=`；两个新工具 `file_list` / `file_hand`（`TOOL_RISK` 分别是 read_only / workspace_write）；前端 `filesPanelHTML + flBind/flLoad/flShow`（左 214px 目录 + 右预览，预览复用 `/api/conv/preview` 与 `/api/conv/read`，文档开外站复用 `/api/conv/open`），右栏换成「文件管理助手」（scope=`files`），发消息时把选中文件绝对路径当 `sel` 带上、服务端拼进 `extra_context`。

**2. 坑（下次别重踩）**：面板显隐原来只遍历 `WF_ORDER` → 第 11 项会被藏住，改成按 `panel-<wfStep>` 统一 toggle；`wfPaintFoot` 用 `WF_ORDER.indexOf` 会写出「第 0 / 10 步」，必须先判 files 并藏掉「退回上一步 / 确认交付」；`wfPaintMsgs` 空态默认走 `wfPlanLoad`（会白烧一次模型），files 要给静态引导；换项目后 `flRel` 还留着上一个项目的子路径 → 报「不是文件夹」卡死，`flLoad` 里 `d.error && flRel` 退回根重试一次。

**3. 没做**：改名 / 删除 / 新建夹 / 全树搜索；拖拽入口（WebView2 拿不到 `file.path`，仍靠 ＋ 粘路径）。

**4. 验**：`py -3.10 -m unittest tests.test_files_panel` 5 项 OK（含「同级项目 `../别的项目` 必须被第二道 `is_relative_to` 拦下」）；真项目只读探针：测试test 根 8 个子夹 + README、`最终产物` 的 PDF 内嵌 566×835、`论文正文/正文.md` 正文 1890 字；真跑一轮「论文正文都有什么文件」→ 助手调 `file_list` 两轮后按真实大小/时间列了 8 个文件。

## 2026-10-22（文章撰写：从零起稿按谱系图逐节生成）

**0. 口径**：谱系图只在**起稿那一次**当骨架 —— 从零写第一版时，一节一次调用、每节只喂挂在该节下的证据；正文写进文件之后，图与稿脱钩，改图不会改变已成稿的输出（要按新图重写必须明说「整篇/全文/从头」）。

**1. 实现**（`workflow_runners.py`，与另一会话并行落地的部分）：`_map_plan`（按图的 DFS 出「节 + 层级 + 该节证据」）、`_map_evid`、`_draft_from_map`（逐节 `workflow.chat`，模型自带标题剥掉、标题由框架出；单节失败写「待补」不毁全篇；最后 `doc_write` 一次整份 + 一条 digest）。同时把 `write_system` 里的【谱系图】块删掉 —— 图已经用过，留着它等于「改图 → 下次改稿偷偷换内容」。

**2. 补的坑**：`_wants_draft` 原来把「重写」也算整篇起稿，于是**已有正文时说「重写 4.2 这节」会触发整篇按图重推、覆盖改好的正文**（两种路子都是整份文件覆盖）。现在：无正文 → 自动按图起稿；有正文 → 只认「整篇/全文/全篇/通篇/从头/都写/初版…」，单说「重写某节」走对话 agent 改那一节。两条新测试锁住这两个方向。

**3. 旧口径冲突**：`test_write_system_carries_map_evidence`（要求 system 里必须带图）在【谱系图】块删掉后必然失败，改成 `test_write_system_no_map_block_after_draft` 测反向，并在 docstring 写清为什么口径反过来了。

**4. 没挂证据的节 = 自己写满，不留占位**（2026-10-22 追加）：`_draft_from_map` 里空证据那句从「一律写「待补：<还差什么>」」改成「照框架直接写成正常正文（定性论述、机制解释），用限定语兜住，数字/文献/实验结果一律不编，正文里不出现「待补」」。同时 `write_system(draft=True)` 把硬规则 3 和 5 里的「写待补」换成「定性论述、写满写实」——否则基础 system 一句话就把逐节指令顶回去；`_draft_from_map` 再追加一条【这一版的口径】压掉技能包 fallback 里那句「缺数据写待补」。单节调用真失败（不是没证据）才留「（本节未生成 …）」，digest 里也列出来。

**5. 验**：`396 tests OK (skipped=12)`。真项目只读探针（渐变氧空位：27 节点 / 21 节 / **6 条关联**）→ 起稿分 21 节、21 次串行调用，其中 15 节没证据 → 按新口径自己写满。耗时：云端 flash 几分钟；本机 27B（~6 tok/s）约 1 小时量级。

## 2026-10-22（谱系图：节点尾部 ＋ 手建节点 / 资料节点换小节）

**0. 根因**：`mpNodeInner` 早就画了 `<button class="mp-add" data-add>`，但**全仓没有一处点它的处理**——点下去顺着冒泡进了 `wrap.onclick` 的 `closest("[data-mp]")`，等于「打开编辑节点」卡；卡里只有「挂一条资料」下拉（只能挑已有笔记/数据/文献），于是「没法手动加节点」。

**1. 节点尾部 ＋（每个节点都有）**：＋ 从「只有 section 卡右上角悬浮」改成**标题行内、文字后面**（`.t` 改 flex，`.lbl` 接管三行截断；`.mp-add` 回到文档流，测量盒一起算进去，卡片自动宽 22px）。点它 → 小卡片（标题 + 类型，默认「笔记」）→ Enter 落盘 / Esc 取消；新节点以 `包含` 挂在被点节点下面，走同一个 `mpSave` → `/api/map/save`，**后端零改动**（`map_write` 本来就收任意 id/label/type）。

**2. 坑与口径**：`pointerdown` 遇到 `[data-add]` 直接返回，否则点 ＋ 那一下手抖 >4px 会当成拖节点。手建的节点带 `mine:true`（`map_write` 白名单放它过），**「重新整理」只重排大纲那部分**：`map_tidy` 先把 `mine` 节点和大纲节分开，大纲节照旧按序号/标题重认，手建节点最后原样追加 —— 类型、坐标、边都留着，`changed` 里明说「手建的 N 个节点原样留着」。不加这个标记的话，他选「小节」加的那颗（大纲里没有它）会被 `map_tidy` 当「大纲已删的节」清掉，实测 14:17 真踩到了；agent 挂的、父节被删的资料仍照旧一起摘。

**3. 验**：`381 tests OK (skipped=12)`（新增 `test_hand_made_node_roundtrip_and_survives_tidy`、`test_tidy_keeps_hand_made_nodes`）；`node --check` 过；真应用实测 27 节点 → 点 ＋ 加「1.4 测试手建节」（类型选小节）→ 磁盘 28 节点 `mine:true` + `{c1→r1 包含}` → 调 `/api/map/tidy` → `changed:["手建的 1 个节点原样留着"]`、节点与边都还在 → 测完把谱系.json 按测前快照原样还原（字节一致）。

**4. 资料节点换小节（两种入口，都走 `mpReparent`）**：
- **编辑卡下拉**：资料节点的卡里「挂在小节」从一行文字改成 `<select>`（列出全部小节 + 「没挂任何小节」），选完就换父。
- **拖到小节卡上松手**：`pointermove` 里用 `getBoundingClientRect` 命中（不用 `elementFromPoint` —— 拖动中的卡片就在光标底下会把命中挡掉），压中就给那张小节卡加 `.drop` 描边、被拖的卡 `.mp-dragging` 半透明（看得见底下）；`pointerup` 再命中一次才改父，压不上就照旧只挪位置。只认「资料 → 小节」，拖小节卡不改父。
- `mpReparent`：同一份资料只留一个小节父边（多父会塌成一个，符合「移过去」的语义），**边标签沿用原来的**（`支撑（背景）`/`旁证（氧空位假设）` 不丢），清掉手拖坐标让它跟新父重排（否则会停在松手的地方）。后端零改动。
- 实测：下拉把 p1 从 s2 挪到 s3 → `[{from:s3,to:p1,label:"支撑（背景）"}]`；拖 p3 压到「1.3」→ 高亮+半透明都在、松手后 `[{from:s4,to:p3,label:"旁证（氧空位假设）"}]`、27 节点不变；拖到空白处 → 只变坐标不改父。测完谱系.json 按快照还原（字节一致）。

## 2026-10-21（项目空间「进度与计划」：阶段口径改对 + 数字自己刷新）

**1. 阶段口径**：原来按「十步里第一处缺口」算 → 他跳着做（正文/格式已完，精读没补）就永远停在缺口那步，显示「论文翻译」。现在 = **十步里按顺序最靠后的已完成步**；正在跑且排在其后的那一步优先；纯残迹的 running（排在已完成步之前）不算。规则收在 `workflow.current_stage_key()` 一处，`stage_now()`（页头 pill、项目列表、agent 上下文）与 `project_todos.kanban_overview()`（看板 pill/管道）都走它，不再各算一套。实测该口径下 材料信息学综述：5/10、阶段 格式转换。

**2. 数字实时跟**：`kanban-view.js` 挂在嵌入看板上的 8s 轻轮询，`stats/current_stage/todos` 真变了才整块重画；弹窗开着或正在拖拽时跳过，容器被换掉（切 tab）自动停。实测外部往 `steps` 插一行 → 12s 内页面从 50% 5/10 变 60% 6/10，删掉再回落。

**3. 管道视图徽标**：`st.done` 优先于 `st.current`，不再出现「✓ 已达成」的卡片同时标「🔥 当前阶段」。

验证：`364 tests OK (skipped=12)`（`test_project_todos` 新增 `CurrentStageTest` 两条：跳步时取最靠后已完成步、无记录退回第一步）；`node --check` 通过；8765 真库实测页头 pill 与看板 pill 一致。
影响面：改了 `.py` → 已重启 8765；`kanban-view.js` → 窗口 F5。
遗留（等他定）：`write`/`convert` 两条 done 是「每日文献推送/日报导出」写进来的，不是论文的撰写/转换；清掉的话进度 5/10 → 3/10、阶段变「下载精读」。

**4. 清掉脏记录 + 堵住源头（他确认「需要清掉」）**：删 4 条（`write` 草稿 ×3、`convert` 每日资讯 ×1，全带 `每日文献推送/每日资讯推送` 摘要），`材料信息学综述` 回到 **3/10、阶段 下载精读**；删前 `PRAGMA wal_checkpoint(FULL)` + 整库备份到 `data/_backup/research.sqlite3.bak-20261021-204216`。源头：定时任务跑的是同一个 agent + 同一批工具，`draft_write`/`format_convert` 顺手记了十步 → `WorkflowDB.quiet_steps`（只由 `schedule.py` 置 True）让 `log_step` 直接跳过；调度任务的产出不再进科研台进度账。

## 2026-10-22（格式转换：未编译态、期刊模板库、交付后三问；UI 三版待选）

**1. 右栏未编译态**：`.cv-stage` 底色改纯白，中间一行「还未编译 / 在右边跟格式助手说要什么（PDF / Word），或点上面『📄 转 PDF / 📄 Word』」。

**2. 期刊模板库 `TEMPLATES`**（`workflow_converters.py`）：plain（article+ctex，默认）/ elsevier（elsarticle）/ ieee（IEEEtran）/ revtex（revtex4-2），每项带 `repo` 指向公开模板库（bamos/latex-templates、jsk-report-template/elsarticle-template、quantixed/manuscript-templates、ZhenhongDu 光学类仓库）。`md_to_tex(md, title, template=…)` 按模板出前言；`conv_run(..., template=…)` 透传，**编不过自动退回 plain 并在返回值里报 `template_fallback`**（不静默失败）。四种模板实测都能编出 PDF（elsevier 127 KB / ieee 119 KB）。顺带支持 `format=doc`（docx → Word 97 .doc，本机 LibreOffice）。

**3. 审查交付 → 格式转换先问三件事**：`open_convert()` 在 step=review 交付时往 convert 对话写一条开场白 —— ①图：正文 N 处 [图位] vs「图」文件夹 M 张，对不对；②成品：docx / doc / pdf；③范本：四个模板（带说明）还是上传自己的 .docx/.tex。只写一次。

**4. UI 定版 A 已落地**（他选 A）：`图（214px）| 展示区` 两列，右栏**沿用真实格式助手**（全局 aside，385px：对话/轨迹 tabs、轮数/工具数/token、工具名与产出目录、附件、模型与署名、composer —— 与其它步骤同一份 DOM 与 CSS，不另做简化版）。展示区标题栏新增「成品格式（PDF/docx/doc）+ 模板（通用/Elsevier/IEEE/APS）+ ⚙ 编译」；`cvBuild()` 打 `/api/conv/run` 并显示 `template_fallback` 提示；删掉旧的 cvToPdf / cvToWord 死代码。实测 1600 宽：panel 754（214+529）+ aside 385，与写作用同一尺寸；选 PDF+Elsevier 编译 → 127 KB PDF 落最终产物并在右侧 iframe 显示。

**5. 删掉展示区那一行工具**（他的口径：格式与模板是助手问使用者的，不该让使用者自己选）：`最终产物… / PDF / 通用模板 / ⚙ 编译 / ↗` 整行移除，`cvBuild`、`cvToPdf`、`cvToWord` 一起删；编译只由格式助手调 `format_convert`。没成品时展示区是**整块纯白 + 居中「未生成」**（顺手删掉压掉居中的那条旧 `.cv-empty{flex:none…}` 覆盖），清空时标题回到「展示区」。

**6. 修「127.0.0.1 发送了无效的响应」（真凶：header 里的中文文件名）**：`/api/file` 与两处 PDF 预览原来发 `Content-Disposition: inline; filename="基于局部电阻梯度调控的 … .pdf"`。`http.server` 按 latin-1 编 header，中文名让这条 header 直接编不出来 —— 响应头和正文对不上，Chromium/WebView2 整页报「127.0.0.1 发送了无效的响应」，req.log 里是同一个 URL 先 200 后 400。改成 `Content-Disposition: inline`（预览不需要文件名；下载仍走 825 行的 `filename*=UTF-8''`）。实测中文名 PDF：115993 B 全下来，展示区正常渲染。顺带：`log_request` 只记 >=400 到 `data/cache/req.log`（全记会无界长），排障时一眼看到坏请求。

## 2026-10-21（晚 4：项目「图」文件夹 + 格式转换改成「左图 / 右成品」两栏 + 撰写落图位图题）

**1. 项目文件夹多一个「图」**：`PROJ_DIRS` 加 `图`（新建项目就有；老项目首次打开格式转换时 `fig_dir()` 自动补建）。放论文最终要用的成图（PNG/SVG/JPG/PDF），文件名就是图题里要引用的名字。

**2. 格式转换页重排**（删掉「编译区」textarea 和「文件区」）：
- 左窄栏 = 「图」文件夹（`/api/fig/list`）：每行 扩展名 + 文件名 + ✎ 改名（`POST /api/fig/rename`，只允许改这个夹里的文件、没写扩展名沿用原扩展名、重名报错），点一下右栏看原图。
- 右大区 = 成品展示：顶部下拉选「最终产物」里的 PDF / Word / HTML，`/api/conv/preview` 判断类型 —— PDF 走 iframe、图片走 img、**Word 先 LibreOffice 无头转 PDF 再 iframe**（按 mtime 缓存在 `data/cache/preview`，转失败退回「用 Word 打开」卡片）+ 「📄 转 PDF」「📄 Word」两个导出按钮。

**2.1 上传口**：左栏加了 `＋`（文件选择，多选）和**拖放**（把 PNG/SVG/JPG/PDF 拖到左栏即上传）。走 `/api/upload?dest=fig&task_id=`，直接落进项目「图」文件夹、保留原始文件名（重名自动 `-2`），只收图片与 PDF（其它扩展名 400），沙箱里也能写（目录随项目根继承 Low）。
**3. 文章撰写读图文件夹**：`write_system` 新增【图文件夹】清单 + 规则 7.2：凡要配图处**另起一行**写 `[图位 1] 图1 <一句话图题> | 文件：<图文件名>`，序号按出现顺序，只能用清单里的文件名，没有就写 `[图位 N] 待补图：<该画什么> | 文件：待补`。

实测（真实项目）：左栏列出 `fig1_…png`、改名成功；点图右栏出图（0.02s）；选 `渐变氧空位Ga2O3_MSM.docx` → 转 PDF 后右栏出文档（首次 9.3s，之后命中缓存）；`360 tests OK`。

## 2026-10-21（晚 3：格式转换补 PDF 与「现成 .tex → PDF」）

`format_convert` 工具的 enum 只有 `docx/md/html` —— 跟格式助手说「转成 PDF」它没法调；而 `conv_run` 其实早就会 md→tex→xelatex。
- 新增 `format=pdf`（= 正文 → tex → PDF）和 `format=tex`（编手边现成的 .tex，可给 `source` 路径，不给就取项目里最新的 .tex，走本机 MiKTeX xelatex）。docx/html/md 仍走 `stage_convert`。
- `CONV_FORMATS` 补 pdf / tex 两项；顺手修了导出文件名带上「题目：」前缀的毛病（`题目_基于局部电阻…pdf` → `基于局部电阻…pdf`）。
- 前端「📄 转 PDF」本来就两种活都干（选中 .tex 就编它、否则正文转 LaTeX 再编），这次只是把后端能力和助手工具补齐。
实测（真实项目）：`format=pdf` → 115993 B PDF，1.1s；`format=tex` + source → 116257 B；`format=tex` 不带 source → 自动取最新 .tex 也编出来了。
验证：`tests/test_convert_tex.py` 新增路由测试（pdf/tex 必须走 conv_run、tex 要带 source），`359 tests OK`。

## 2026-10-21（晚 2：「稿件审查」左边改成审查意见）

`DOC_STEPS["review"]` 以前指向 `正文.md` —— 于是这一步左边永远是原文，而且 `doc_write` 还会把正文覆盖掉。现在：
- 产出文件是 `<项目>/论文正文/审查意见.md`，左边展示区就是它；正文仍是**输入**（`review_system` 里读 `doc_read(...,"write")`）。
- prompt 明确：「产出是审稿意见不是正文，整份 doc_write 写审查意见.md，不要覆盖正文」+「说『审一遍』就直接写，别反问要不要写」。
- 前端 `DOC_UI.review`：file 改「审查意见」、pull 改「用这条回复更新审查意见」、关掉荧光笔与引用台账、`quiet: true`（报告只进左边，右栏只留过程，规则同写作步）。
实测：说「审一遍，出审查意见放左边」→ `审查意见.md` 11472 字节（三视角打分 + Major Revision + 逐段建议），左边显示 4207 字，右栏只有工具卡片，`正文.md` 未被改动（4762 字节原样）。

## 2026-10-21（晚：项目空间「阶段」实时对齐十步 + 快捷键冷启动 6.4s→2.9s）

**1. 项目空间卡片永远写「阶段：未开始」**
`/api/task` 只回 `research_tasks.stage`（只有极少数动作会改它，他这项目走完谱系、写正文了还停在 `reading`）。改成回 `stage_now`（取最近一条 `steps` 记录，`db.stage_now()` 本来就有），前端加 8 秒 ticker 只换那个 pill（不整页重画，切走自停）。实测该卡片现在显示「阶段 文章撰写」。

**2. 点快捷方式 5 秒以上才见到窗口 —— 根因是端口探活等 RST**
本机（装了网络过滤/加速器）对**关着的** loopback 端口要 **2.02s** 才回 RST：
```
port 8766: rc=10061 2.020s     ← 关着
port 8765: rc=0     0.016s     ← 开着
```
而 `main()` 里裸 `connect_ex` 探活一次、沙箱子进程再探一次、`_spawn_low_server` 的就绪轮询里每轮又探一次 → 冷启动白等 ~4s。
改法：`_port_open(port, 0.25)`（短超时，超时当没人听）替掉三处裸 `connect_ex`。配套加了 `RW_BOOT_TRACE=1` → `data/cache/boot.log` 的分阶段计时（宿主/子进程各写各的，pid 区分），下次再喊慢先看这张表。

实测（同一台机、pythonw 起窗）：仓库里改前 `http 4.26s / window 6.45s` → 改后 `api ready 2.24s / window 2.88s`。

## 2026-10-21（本轮：谱系图「重新整理」真按大纲重认框架 + 修好谱系助手 500 + 笔记挂上去看得见 + 交付接力给文章撰写）

**0. 交付接力**：谱系图交付（`/api/step/done` step=map）时，给「文章撰写」的对话留一条开场白（`open_write()`，只留一次）——「谱系图我已经看过了。你还有什么要修改的吗？撰写上有什么特殊要求吗（投哪里 / 中文还是英文 / 篇幅 / 图表怎么安排），或者有可以参照的语料吗？」；同时 `write_system` 里加了【谱系图】块（每节挂着哪些证据），否则这句话是空口说白话。谱系图本体一直在 `<项目>/论文正文/谱系.json`。

**0.1 文章撰写右栏只留过程**：`DOC_UI.write.quiet` —— 这步落盘的那几轮，模型正文不再刷在右栏（成品只在左侧《正文》展示区），右栏留给思考摘要 + 工具卡片（调了哪个工具、多久、结果摘要）；没落盘的轮次（在跟你讨论）正文照常显示，否则右栏会空。跑完给一行 meta：`本轮 N 字已落到左侧《正文》`。

**0.2 文章撰写「左边一直没正文 / 说写好文件了」的根因**：`doc_write` 的 markdown 参数被**截断**了 —— `chat_raw` 默认 `max_tokens=4096`，推理模型的思维链先吃掉预算，`doc_write` 的 args 到服务端就是空的（实测他那轮 7 次 `doc_write` 全是空参数），空内容把 `正文.md` 覆盖成 1 字节，模型还回一句「已写好文件」。三处改：
- `run_agent` 写作这步给 `WRITE_MAX_TOKENS=8000`（跟 `chat_json` 一档）；
- `doc_write` 空 markdown 直接拒写（抛 ValueError → 模型看到「失败：markdown 不能为空」），不再清空文件；
- 兜底：写作步若模型整轮没调写文件工具却把稿子贴在回复里，`docDraft()` 抠出正文自动落盘（≥1 个标题行 + 300 字），左边照样有东西。实测：模型调 `doc_write`（args 1997 字节）→ `正文.md` 4762 字节，左侧展示区显示正文，右栏只剩工具卡片。

**1. 点「重新整理」以前彻底没反应 —— 三个原因叠在一起**
- `mpMsgs` 早就改名没了，`mpLoad` 每次都在最后一行 throw → 右栏一句话不显示；改成 `docRestore()`（doc 三步和谱系图共用），加载对话/轨迹/统计一次到位。
- 谱系图这一步的提示全写进 `docMsg("map", …)`，可这步没有 `map-meta` 元素（右栏 meta 是 `mp-meta` 计数）→ 提示静默丢失。改 `mpSay()`：整理/挂资料的结果直接说进右栏对话。
- 原来「重新整理」只重排坐标、保留手动位置 ⇒ 所有节点都有 x/y，重排等于什么都没变。现在交给服务端 `map_tidy()`。

**2. `map_tidy()`：按大纲**当前**顺序重认框架（`POST /api/map/tidy`）**
标题认身份**先认序号、再认标题文字**：他会手改小标题序号，而「实验方法」这类同名小标题在不同章里很常见，只按文字认会认错人、把挂好的笔记串到别的节。序号没变、标题改了 → 同一个节点；序号改了、文字还在 → 也还是同一个节点；节点标题一律照抄大纲，他改成什么序号图上就是什么序号。

**3. 精读笔记挂到每小节连线后面**
- `map_system` 里 `'; '.join(src['notes'])` —— 笔记源早改成 dict 了，于是**每一步谱系对话都 500**（`TypeError: sequence item 0: expected str instance, dict found`）。这是笔记一直没挂上图的真正原因。
- 笔记节点的 detail 现在按笔记文件现取（最前两条实质要点，跳过标题/DOI/许可等出版信息），笔记重读或改名后图上跟着变；ref 卡片真显示 detail（以前只用 title 悬停显示）。
- 「重新整理」发现还有没挂上的精读笔记时，自动在右栏发一句「按标题挂到对应小节，不要读附件，判断完一次 map_write；看不出归属的列出来问我」。

验证：`tests/test_map.py` 9 项通过（新增 4 项：按大纲重排、删小节带资料、笔记 detail 跟着文件、map_system 能拼）；真实项目「渐变氧空位 Ga₂O₃ MSM」点一次重新整理 → 21 节保持、3 篇论文笔记挂上 1.1/1.2/3.2/4.3、2 篇标题看不出归属的列出来问用户。

## 2026-10-20（本轮：精读不再空回 + 谱系图横向思维导图 + 各助手只看自己的账 + 死代码清理）

**1. 精读「读不出来」的根因不是 PDF 没文本层**
实测：`max_tokens=3000` 时推理模型的思维链把额度吃光，`finish_reason=length`、`content_len=0`，于是笔记只剩标题（92–95 字的空笔记）。
改法：`chat()` 空回翻倍预算重试一次；`stage_read` 每段给 6000，拿不到内容直接判失败（不再落空笔记）；
`read_done_filenames()` 让已有实质笔记（≥300 字）的论文重跑跳过。影响：检索/精读两步。
验证：真实接口复现（3000 空回 / 6000 出 3743 字），补读 Dissociation 3889 字、High-performance 3193 字，5/5 篇都有实质笔记。

**2. 右栏统计改成「只算这个助手自己」**
`usage_stats()` 删除（唯一的调用方是 `/api/step/messages`），前端不再叠加项目合计；轮数/工具数/token 边跑边刷。

**3. 检索助手跑完必须收口**
提示词 + 服务端兜底（模型没问「还要调整检索式吗」就补一句，问过不重复）。顺带修 `for step in range()` 遮蔽同名的 `step` 参数。

**4. 谱系图改成横向思维导图**
- 分层：父子边判层级（「下一章」是同级串联，不参与分层），列内 DFS 排序，父节点对齐子节点中线
- 拖动节点落盘 `x/y`；agent 重写整份 JSON 时保留用户坐标，`keep_positions=False`（前端「全部重排」）才清
- 点节点弹编辑卡（标题/说明/类型），↻ 重新整理，缩放 −/＋/适应
- `map_seed()`：大纲已有、谱系图还没有 → 自动长出「章 → 节」树，不用等用户开口；已有图绝不覆盖
- `map_sources()` 的笔记改成「标题 + id」，不再给哈希文件名

**5. 死代码清理**（判据：全仓 0 引用）
- 死 import 3 处：`workflow.py` 的 `cite_sync`/`CONV_FORMATS`、`workflow_converters.py` 的 `subprocess`、`tools/check_literature_env.py` 的 `urllib.error`
- 死函数 1 个：`team.py::_dependencies_ready`（只有 `_dependency_state` 在用）
- 死 CSS 12 条规则：`cap-grid / cap-card-* / cap-item-card` 一整套（旧能力卡片布局，js 里 0 出现），约 2.1 KB
- 加 `x/y` 持久化后留下的旧分支不动；`q/` 是 gitignore 的本地草稿（含取 token 的脚本），仓库里没有，未动

验证：`py_compile` 0 失败、`node --check` 全过、`300 tests OK (skipped=9)`、起一次应用 `/`、`/api/step/outs`、`/api/map/state`、`/api/security` 全 200、谱系图 21 节点 20 边。

## 待发 PR（本轮：项目卡片进科研平台 + 看板浅色适配 + 看板标题行）

只动前端 4 个文件，改完 **F5 就够**：`apps/desktop/web/` 下 `workflow-view.js`、`app.css`、`kanban.css`、`kanban-view.js`。

**1. 「项目」卡片「进入 →」改落科研平台**
原来跳 `openTab("space")`（项目空间），按他要求改成 `openTab("workflow")`：切当前项目 + 直接进十步工作流。
整行点击语义不变（只切当前项目、不动页面），删除/新建逻辑没碰。

**2. 看板浅色模式卡片全是灰黑块 —— 根因是主题变量名对不上**
`kanban.css` 处处写 `var(--bg-card, 暗色兜底)` / `var(--bg-sub, …)` / `var(--border, …)`，而 `app.css` 两套主题只定义
`--panel / --panel2 / --line`——这三个变量**全站从未定义过**，所以永远吃各自的深色兜底：深色看着正常，浅色就整片深灰。
同病的还有 `project-space.js`、`notes-and-settings.js`、`agent-settings.js` 里同名变量（这轮未逐一改，接上别名后一起跟着好）。

改法：
- `app.css` 浅色根补 `--bg-card:var(--panel);--bg-sub:var(--panel2);--border:var(--line)`。
  **深色根故意不补**：让深色继续走原兜底值，保证深色像素级不变。
- `kanban.css` 里 `rgba(255,255,255,α)` 这类「白色微光」表面（进度条底、徽章底、分栏线、悬停态、空列虚线）
  换成 `color-mix(in srgb, var(--fg) α%, transparent)`——跟着主题自动反色。
- 语义色收成 `.kb-root` 上的 `--kb-sky / --kb-violet / --kb-rose / --kb-amber / --kb-green`：
  深色 = 原来的粉彩，浅色 = 加深版（原色压在白底只有 1.3–2.3:1，等于看不清）。
  卡片悬停阴影改用主题 `--shadow`（浅色下 40% 纯黑会糊成灰斑）。
- `kanban-view.js` 内联的 `#f0f6fc / #8b949e / #38bdf8 / #8b5cf6 / #fb7185 / #10b981 / #f59e0b / #c4b5fd`
  换成主题变量；第二视图（十步管道 / 排期）那两段标题原来是白字白底，浅色下等于没有。

**3. 「📋 科研进度与计划看板」被挤成竖排**
`.kb-main-title` 是 flex item 且默认 `flex-shrink:1`，被右边的项目下拉（256px）+ 阶段胶囊挤到 105px 宽，
中文断成 3 行，标题块高 84px、概览区被撑到 235px。
改法：`.kb-main-title{ flex:0 0 auto; white-space:nowrap }`——标题永不被挤扁，窄窗先让「按钮组」整块折到第二行。

**4. 十步工作流每步输入框右下的「🤖 步骤名」胶囊删掉**
`wfAgentHTML()` 里发送键左边那个 `<span class="at-sel">🤖 创新点审查</span>`（只读署名胶囊，不可点）整段移除，
输入栏只剩 `＋` 和 `↑`。`.at-sel` 样式与其它页（项目/专家页的模型、署名、团队选择器）共用，未动。

**5. 「专家」页第一张卡（智研管家）的 🤖 emoji 换成主页那套 Q 版小人**
实际渲染专家页的是 `agent-settings.js` 的 `renderAssistants`（它覆盖了 `chat-views.js` 里的同名函数），
卡片标题原来直接吐 `${h(a.avatar)}`。改成 `a.id === "builtin-steward"` 时出 `<img src="${Q_AV}">`（22×22，
`object-fit:contain` + `vertical-align:-4px` 对标题基线），其余专家仍是自己的 emoji。
`Q_AV`（`/vendor/img/agent-q.png`）是 `chat-views.js` 里既有的主形象常量，没有新增资源。
`chat-views.js` 的 `asAvatarOf` 没动 —— 它还管着对话列表/消息的脸，改动面超范围。

**6. 十步工作流的回复被签成「智研管家」—— 实际是拿错了身份来源**
`appendOne()`（`chat-views.js`）渲染 `assistant` 行时，名字和头像取的是**当前打开的那个对话**绑的专家
（`S.chats` 里 `curChat.assistant_id`），拿不到就回落「智研管家」+ Q 版小人。科研平台每一步的对话根本不在
`curChat` 里，所以十步全被签成管家/上一个对话的专家——内容其实是各自步骤端点出的，只是署名错。
改法：
- `appendOne(box, ev, who)` 加第三个可选参数：传了就用手上的 `{emoji, name}`，不传仍走老逻辑
  （智研管家页、专家页一个字不变）；`ev.replay` 递归子事件时把 `who` 一起传下去。
- `workflow-view.js` 三处调用点传 `WF_AGENT[step]`：`wfPaintMsgs`（历史重放）、`wfSend` 流式 `appendOne(box, ev, a)`、以及 `done` 兜底那一行。
- 后端没改：`/api/idea`、`/api/outline`、`/api/write` …本来就是分步端点，回复内容一直是本步的。

**7. 回复正文里还自称「我是览籍智研」—— 根因在第一段 system，不在追加的那段**
署名改对后他截图反馈：正文仍是「我是览籍智研，你的科研助手…」。`AGENT_SYSTEM`（`workflow_prompts.py`）第一行
就把身份写死「你叫「览籍智研」…」，第二行还教「问你是谁就答：你是览籍智研」；`run_agent` 后面追加的
【你的身份】对它无效——模型照第一行复述。
改法（`workflow.py`，一处）：
- 新增 `_system_for(who, system)`：`who` 非空且调用方没显式传 `system` 时，把 `AGENT_SYSTEM` 开头那两句
  替换成「你叫「{who}」…」「问你是谁就答：你是{who}。」，其余规则原样保留；否则原样返回。
- `messages[0]` 从 `system or AGENT_SYSTEM` 改走 `_system_for(who, system)`。
- 前端 `wfSend` 的请求体现在带 `step`（`{task_id, message, step}`），后端 `/api/agent` 读它传给 `run_agent`
  —— 这行和 `STEP_AGENT` 那段是本轮另一路（同工作区的会话）先落的，本条目只是把最后一环接上并验过。

**验**（离线，不调模型：把 `workflow.chat_raw` 换成假函数，直接跑真 `run_agent` 抓 `messages[0]`）：
十步逐个断言第一段 system 为「你叫「X」」且不含「览籍智研」；`step=""` 时仍是「你叫「览籍智研」」，通用对话没被误改。
临时探针项目 `__身份探针__` 用完已删（`/api/task/delete` 返回 deleted=true，库里只剩他原有 3 个项目）。

**8. 从学长 fork 的 `xuezhang/asset-workflow` 挑回 main 真缺的两条路由**
那条分支与 main **无共同祖先**（从他自己的 `initial commit: 览籍智研 0.1.0` 重开），main 领先 131 个提交，
直接 `merge --allow-unrelated-histories` 会按它的树覆盖 main（还会删 `migrations/006_team_task_dag.sql`、
`tests/test_team_scheduler.py`、两版 logo）。逐个文件比对后结论：**它没有任何 main 缺少的文件**（A=0），
资产层/`/api/assets/rebuild` 等我们已有。唯一真缺的是两条 GET 路由——**main 的前端在调、后端却没实现**：
- `GET /api/starmap/item?id=&title=&doi=&key=`：知识星图点开节点看条目详情（Zotero 有就回 Zotero，
  没有就用本地 papers/paper_meta 拼一份 Zotero 形状的回包）。调用方 `web/vendor/neural/scene.js:831`。
- `GET /api/zotero/select?key=`：唤起 Zotero 定位条目（`zotero://select/items/0_<key>`）。调用方 `web/starmap-view.js:26`。
两条都按原样搬进 `apps/desktop/server.py`（插在 `/api/starmap/brief` 与 `/api/litgraph` 之间），
main 现有实现一行未改。

**验**：`py_compile` 通过；起 `server.py --no-open` 实测——`/api/zotero/select` 无 key 时按预期 `{"error":"缺少 key"}` 400，
`/api/starmap/item?id=<真 paper id>` 返回 `zotero_found:false` + 标题/年份/页数/doi 完整；全量 270 项测试 OK（skipped=8）。
没搬的：分支里 `workflow.py` 给解密失败的 key 补 `key_error/key_invalid` 提示（4 行）——`agent_settings.py` 已有同类提示，暂不动。

**验**：
- 浅色（1440×900，浏览器实测计算样式）：`kb-header` / `kb-col` 底色 `rgb(255,255,255)`、`kb-card` `rgb(241,244,248)`；
  全页扫描 **无** alpha>0.5 且亮度<0.35 的深色块、**无**文字对比度 <3:1 的元素（扫描按祖先链做 alpha 合成）。
- 深色回归：`kb-header`/`kb-col` 仍是 `rgba(22,27,34,.7)`、`kb-card` 仍 `rgba(16,20,26,.85)`、列顶条仍 `#38bdf8`，
  与改动前兜底值逐项相同。
- 标题行：1440 / 1200 / 1024 / 900 四档宽度下都是单行 195×28，概览区高 235 → 193。
- 十步联测：项目会话里逐一点 idea→search→download→notes→outline→map→write→review→entrans→convert
  十个步骤，可见输入栏右下 `children` 只有 `send-btn`，`.at-sel` 一个不剩；`node --check workflow-view.js` 通过。
- 专家页（真机 1440×900，起 `server.py --no-open` 验完就关）：`.ag-grid article.card` 第一张 `h4` 里是
  `<img src="/vendor/img/agent-q.png">`，`naturalWidth=512` 已加载、渲染 22×22，第二张卡（Pony 模式）无 img 仍是 🐴。
- 署名回归（真机、headless 起服验完就关）：创新点审查那一步的历史消息现在是
  `🔍 创新点审查助手 researcher 完成`（emoji 头像，不再是小人图片）；`appendOne` 传十个 `WF_AGENT` 逐个探针，
  依次得到 创新点审查助手 / 文献检索助手 / 入库助手 / 翻译助手 / 框架助手 / 谱系助手 / 写作助手 / 审查助手 / 中译英助手 / 格式助手。
  智研管家页不传 `who`，行为不变。
- 项目卡片跳转：改后服务返回的 `workflow-view.js` 已含 `openTab("workflow")`（曾 curl 核对）；
  **点击端到端没跑**（核对时应用窗口已关、服务不在），待 F5 后点一次确认。
- 截图（未入库）：`桌面\kanban-light.png`、`桌面\kanban-title.png`、`桌面\wf-composer.png`、`桌面\expert-steward.png`、`桌面\wf-step-agent.png`；卡片是注入的假数据，只为看配色。

## 待发 PR（本轮：品牌统一 —— 数据根改名 + 旧数据迁移，只取学长分支里的品牌那部分）

学长 fork 上还有一条 `codex/asset-workflow-upgrade`，是**另一条历史**（`initial commit: 览籍智研 0.1.0` + 一个资产统一提交），
不能直接合：他那条把 #12 的实现又做了一遍、还删了 `migrations/006_team_task_dag.sql` 和 `test_team_scheduler.py`。
这次只把其中真正缺的「品牌统一」搬过来，其余保持 main 现状。

- `packages/workspace-core/paths.py`：`APP_NAME` `ResearchWorkbench` → `览籍智研`，数据根
  `%LOCALAPPDATA%\ResearchWorkbench` → `%LOCALAPPDATA%\览籍智研`；环境变量新增 `LANJI_ZHIYAN_HOME/DATA/DB`
  并**兼容**旧的 `RW_HOME / RESEARCH_WORKBENCH_DATA / RESEARCH_WORKBENCH_DB`；
  首次启动把旧数据根里的 `data/ user/ executors/` **只补缺失、不覆盖**地复制过来，写 `.legacy-home-migrated` 标记只搬一次。
- 打包链改名：`tools/lanji-zhiyan.spec` → `tools/览籍智研.spec`（`name='览籍智研'`）、
  `installer.spec` `name='览籍智研-安装程序'`、`installer.py` `APP="览籍智研"` + 老版本目录迁移、
  `build-app.bat` 同步；删掉已被 `installer.py` 取代的 `tools/install.ps1`。
- 侧栏副标题 `Research workspace` → `科研工作台`；README / SECURITY 里的路径与变量名同步。
- 新增 `tests/test_branding.py`（3 项）：`paths.APP_NAME` 是中文、打包 spec 用中文名、侧栏显示中文品牌。
- **没照搬**的：`006_team_task_dag.sql`（#12 的团队依赖/审核门要用）、`test_team_scheduler.py`——
  他那条分支缺这两样，属于回退，不跟。

**迁移实测**（本机真库，先停应用再搬）：`%LOCALAPPDATA%\览籍智研\` 1.1G 落盘、marker 已写；
新库里 papers 62 / paper_pages 1253 / chat 22，`active_model()` 仍是 DeepSeek 且 DPAPI 解出 35 位 key
（同一 Windows 用户，密文搬过去照样能解）；`papers\` 下 36 个 PDF。
旧目录 `%LOCALAPPDATA%\ResearchWorkbench\` 原样保留当回滚备份，确认没问题再删。

**验**：全量 268 项测试 OK（skipped=8）；应用重启后 `/`、`/api/state`、`/api/workflow/layouts` 200；
浏览器实测侧栏「览籍智研 / 科研工作台」，知识库页读出 3 项目 / 62 文献 / 18 笔记 / 198 候选。

## 待发 PR（本轮：修 3 条 flaky 测试 —— 根因是新项目默认工作流会随机变成「文献精读」）

**现象**：`tests/test_workflow_layout.py` 里的 `test_definition_and_project_selection_survive_reopen` /
`test_team_queue_and_scoped_chat_supply_actual_evidence` / `test_evidence_compares_actual_times_not_iso_wall_clock_strings`
单跑、全跑、连跑结果都不一样（OK / 1F1E / 1F2E 随机），在 origin/main 上也一样。

**根因（不是测试的问题，是产品的问题）**：`workflow_layout.ensure_schema` 播种三套内置模板时逐条调 `now()`，
而 `now()` 在 Windows 上是系统时钟打点——同一时钟刻度里连调三次会给**完全相同的字符串**（实测三次全同）。
于是 `ORDER BY created_at,id` 退化成按 id 排序：`builtin.workflow.reading` < `builtin.workflow.research`，
**新项目默认打开的工作流有 18/20 的概率是「文献精读」**（没有 outline/review 节点），依赖默认布局的那三条测试就随机红。

**改法**（都在 `packages/workspace-service/workflow_layout.py`，共享层改一次）：
1. 播种时取一次基准时间，逐条 `+index µs` → 列表顺序 = 声明顺序，不受时钟粒度影响；
2. `state()` 在项目没选过布局时的兜底从 `layouts[0]` 改成**明确指向 `builtin.workflow.research`**
   （新增 `DEFAULT_LAYOUT_ID` 常量）—— 老库时间戳已经打平的情况也一起修好。

**验证**：新增 `test_seed_order_is_stable_and_default_layout_is_the_full_workflow`（播种首条必须是 research +
把三条时间戳强行打平后默认布局仍是 research 且节点里有 review）；
`tests.test_workflow_layout` 单跑 **8 次全绿**；全量 265 项**连跑两次都 OK（skipped=8）**。
真库实测：默认布局 = `builtin.workflow.research`「完整科研工作流」10 个节点，模板顺序 research → reading → writing。

## 2026-10-20 · 0.1.0 发布前清理

- 路由模块去版本号：`v130_routes.py` → `feature_routes.py`，同步 server 与测试。
- 测试按领域拆分：easyScholar、项目 TODO、项目空间、Zotero MCP、扩展路由各自独立；`ManagedConnection`、`ModelInvoker`、`RunManager` 归回对应测试文件。
- 工具脚本统一 ASCII 命名：`build-app.bat` / `check.bat` / `start.bat` / `lanji-zhiyan.spec` / `installer.spec`。
- 删除死资源 `vendor/kb3d.js`；移除重复的 `docs/` 受跟踪文件，唯一诊断文档并入 `doc/`。
- 删除 `reproduction_routes.py` 中已被 capabilities 取代且调用不存在函数的 `/api/mcp/*` 旧路由。
- 修复浏览器回归测试的过期选择器；`theme.test.mjs` 改为从拆分后的 `notes-and-settings.js` 取主题代码，`ids.test.mjs` 扫描全部前端 JS。
- 版本号统一：新增 `VERSION`，窗口标题显示应用版本 + 构建戳。

**验**：`python -m pytest tests -q` 249 项全绿；`node apps/desktop/web/theme.test.mjs`、`node apps/desktop/web/ids.test.mjs` 通过。

## 待发 PR（本轮：清废旧代码 pass 2 —— 死 import / 死 CSS / 死图 / 拆模块漏的 import）

**判据不变：只删「全仓 0 引用」的；名字可能被字符串/反射拿的一律留着。**

- **死 import**：10 条 import 语句、44 个名字（server 3、workflow 7 块 40 个、zotero_sync 1、tests 2）。
  没用 `ruff --fix`：它按「本文件内未用」判，会把 workflow.py 里给别人转导出的
  `from workflow_search import search, litgraph, …` 整段删掉，server.py 正是从 workflow import 这些，
  一删就起不来。改用「本文件没用 + 没人 `from <本模块> import` 过 + 名字没在别处当字符串出现」三条一起判。
- **死 CSS**：app.css 删 26 条规则（30 行，127241 → 122108 字符）。判据：一条规则的**每个选择器**都带死类名
  （`.tc-step .d` 这种复合的算死）。注释一条没动。
- **死图**：删 `background.png` / `logo_badge.png` / `title.png` / `star.png`（开场卡片删掉后全仓 0 引用，约 3.5MB）。
- **拆模块漏的 import（真 bug，不是清理）**：下面这些名字以前是 NameError，走到那一步才炸：
  `workflow_stages` 缺 search / enrich_oa / download_pdf / READ_SYSTEM / chat / chat_json（论文搜索、下载、精读、写作、审查五步全中）；
  `workflow_documents` 缺 pdfio、OUTLINE_TEMPLATE（读 PDF 附件、大纲起稿）；`workflow_converters` 缺 chat、stage_convert（中译英、转 docx/html）；
  `schedule` 缺 re（定时任务文件名）；`workflow` 缺 hashlib（审计事件静默丢失）。`chat/chat_json` 在 workflow.py 里，
  反向 import 会成环 → 改成函数内 import。
- **顺手补的**：`read_attachment` 被截断过（`if .pdf:` 之后没有 else 也没有 return，任何附件都返回 None）→ 补回文本分支 + 按 chars 截断；
  workflow.py 里 `make_project_tree` 有份与 workflow_search 一模一样的副本、被后面的 import 遮蔽 → 删副本（31 行）。
- **新增守卫** `tests/test_module_wiring.py`（3 项）：跨模块名字必须解析得到、read_attachment 能读能截断。

**验**：`unittest discover` 244 → 247 项，仍是既有的 test_feishu 1 红 + test_workflow_layout 2 红（后两条单跑/全跑结果会变，
与 origin/main 基线同族，今天 stash 前后都复现）；`py_compile` 全过；`capabilities` 工具数 31 → 31（没掉功能）；
server.py 能 import、真起 8765 跑通；浏览器实测 app.css 解析出 1211 条规则、侧栏 232px、深色底、聊天区正常。

**没动（下一步可选）**：20 来个服务端路由在 JS 里 0 引用（`/api/vault/*`、`/api/team/member*`、`/api/models/{add,delete,test}`、
`/api/notes`、`/api/chat/clear` 等）—— 学长那套 TS 前端有可能调它们，删前要先对一下，别自己这边清爽了、合并过去炸他那边。

## 待发 PR（本轮：聊天消息重复渲染修复 —— 用户 2026-10-19 报「问题和回答各显示两遍」）

**根因（数据没坏，是前端多画了一遍）**：库里一条消息只有一份，重复来自回放与回声两条路径。

- `chat-views.js` `appendOne()`：回放 `replay` 的助手消息时先把 `events` 里每条事件挨个画（含 assistant 文本事件），**没 return**，紧接着又把整条消息画一遍 → 每条回答成双。
  改为：嵌套事件里只要渲染出过 `.msg-row.msg-assistant` 就 return；没有 assistant 事件的老消息（只有 usage / 空回复）照旧整条渲染，内容不丢。
- 三条发送流都把服务端 `{type:"user"}` 回声又画了一遍（本地早已乐观画了「你」那条）→ 提问成双。
  `chat-views.js:send`、`workflow-view.js:docSend`、`workflow-view.js:wfSend` 跳过 `user` 事件（也不写进 `evs`，免得回放时又冒出来）；`wfSend` 另外不再把 `final` 追加一遍（流里已有 assistant 事件时）。

**验证**：工作台 8765 真库副本，那条「10月29日…学术会议」对话渲染行数 **7 → 5**（你×2、助手×2、`calendar_add` 工具卡×1，一条一份）；再在临时对话里真发一句「只回一个字：好」（落库 1 问 1 答）走完整实时流，行数 2，无重复；测试对话已删。`node --check` 两个 js 通过。

**没动**：服务端 `run_agent` 仍发 `{type:"user"}` 回声（别处可能靠它计轮数），只在客户端过滤 —— 哪天确认无人消费再删，可省三行。

## 待发 PR（本轮：团队页办公桌/小人升级 —— 用户确认「就这版」）

**定稿视觉参数**（别再动）：桌宽 **80px**、**2×2 格点**摆位、加急那排等比缩到 **56px**。

- `app.css`：桌子与小人的全部尺寸改成 em（`.tof-floor{font-size:calc(var(--ds,80px)/10)}`，1em = 桌宽/10）——
  桌面木色渐变 + 显示器（跑活时发光）+ 键盘 + 杯子 + 纸片 + 椅子 + 桌下投影；小人 = 肤色头 + 身份色头发（3 种发型，`data-hair`）
  + 上衣渐变 + 手 + 鞋 + 落地影。另加 `.tof-room.temp .tof-floor{--ds-temp:56px}`、右栏 `.av-lead`（Leader 头像用 `/mark.png`）。
- `team-office.js`：`officeSit()` 摆位改成 2×2 格点（`gw=W/2`、`gh=(H-54-26)/2`，桌心落格心），
  尺寸从 `getComputedStyle(floor).fontSize` 读（写死 px 一放大就全跑位）；`officeRoom` 成员上限 4（格点只有 4 个）；
  右栏 Leader 行改用智研 Q 版小人 `mark.png`，成员仍用自己的 emoji。
- 验证：桌宽 80 / 加急 56、四张桌心 (125,134)(374,134)(125,294)(374,294) 落在格点上、五间房都不压标题/底部流水条、
  `node --check` 通过、`test_web` + `test_workflow` 30 项全绿。


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

## 2026-10-21 进度看板并入项目空间（左侧入口下线）

「科研进度与计划看板」与项目空间各有一套进度/待办，数值口径还不一致。看板的功能更全（KPI、十步管道、排期、拖拽流转、任务指派），所以保留看板当渲染引擎，把它挂进项目空间，删掉项目空间里那套老待办列表。

| 位置 | 改动 |
| --- | --- |
| `app-core.js` | 左侧 `看板` 入口与 `TITLES.kanban` 删除 |
| `chat-views.js` | 删除 `r==="kanban"` 路由分支 |
| `kanban-view.js` | 新增嵌入模式 `renderKanban(el,{embedded:true})`：不画大标题与项目下拉（外面已有），其余原样；记住 `kbHost`，模态框保存后重画回这块容器（原先抓 `#main`，嵌入后会把整页换成一块看板） |
| `notes-and-settings.js` | `待办任务` tab → `进度与计划`，内容改为嵌入看板；删除老待办列表（含 `spTodo*` 约 3.7KB 绑定代码与 `spTodoFilter`）；概览的「工作流快照」列表换成十步管道格子（点格子跳科研台），删掉与管道重复的 PROGRESS 百分比卡（含 `spProg()`） |
| `app.css` | 新增 `.sp-pipe / .sp-pipe-cell`（绿=已完成、青=已记录、灰=未开始；窄屏 5+5 折行）、`.sp-plan .kb-root` 去重边框；删除已无引用的 `.sp-grid3`、`.sp-todo-*` 共 11 条规则 |

### 验证

- `342 tests OK (skipped=12)`；`node --check` 全部通过。
- 浏览器实测：左侧无「看板」；项目空间 5 个 tab；「进度与计划」内 4 列看板可新建/流转/删除（`/api/todos/*` 走通），页面骨架不被替换；概览管道 10 格与 `steps` 表逐项一致（材料信息学综述 5/10，✓✓✓···✓··✓），点格子进科研台。
- 后端一行未改：`/api/kanban/overview`、`/api/todos/*` 继续服务嵌入后的看板。
