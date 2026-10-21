# 改动记录

> 一轮改动一条，给 PR / 交接用。只写「改了什么、影响谁、怎么验」。

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
