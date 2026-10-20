# Research Workbench 源码复现方案

**目标**：在不依赖闭源服务和专有桌面运行时的前提下，复现一个“证据优先、助手可编排、十阶段科研工作流”的本地科研工作台。  
**基线**：当前仓库的 Python + SQLite + 原生 Web UI；先保持行为兼容，再按阶段替换可靠性薄弱点。  
**适用平台**：Windows 11（主路径），Linux/macOS 作为开发和 CI 平台。  
**非目标**：不绕过付费墙、不自动代替作者做学术结论、不存储用户云端 API key 到远端、不复制任何开源项目的受限代码或品牌。

## 1. 产品范围与核心原则

### 1.1 核心功能

1. 项目、论文、PDF、笔记、证据和产物的本地持久化。
2. 十阶段流程：创新点审查、论文搜索、下载精读、论文翻译、确认大纲、梳理精读、文章撰写、稿件审查、中译润色、格式转换。
3. 全局 Leader、项目 Partner Agent、用户/内置 Assistant、Team Leader 和 Delegate 子任务。
4. Provider/模型切换；OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini 适配。
5. Tool、Skill、MCP 的发现、绑定、撤销、显式授权和服务端二次校验。
6. 证据台账：论文 SHA256、页码、原文片段、字段、状态和导出报告。
7. OpenAlex、Crossref、arXiv 等开放检索；仅对 OA 全文执行下载。
8. Markdown、Word、LaTeX/HTML 等产物导出，并保留运行记录和输入哈希。

### 1.2 设计原则

- **证据优先**：模型输出只是草稿；事实必须绑定 `paper_id + page_no + excerpt`。
- **最小权限**：助手默认只拥有必要工具；`read_only` 在服务端强制过滤和复核。
- **本地优先**：默认数据落在用户机器；外部调用前做脱敏并显示 provider。
- **可恢复**：所有长任务有状态、checkpoint、幂等键、重试和取消语义。
- **可审计**：每次模型、工具、MCP、导出和权限变更都有结构化事件。

## 2. 推荐技术栈

| 层 | 基线复现 | 可选升级 | 选择理由 |
|---|---|---|---|
| 语言 | Python 3.10+（CI 3.13） | Python 3.12/3.13 | 与现有代码和 Windows 打包兼容 |
| HTTP | `ThreadingHTTPServer` + JSON/SSE | FastAPI/Starlette | 基线无需额外运行时；规模化时再升级 |
| 桌面 | pywebview + WebView2 | Tauri/Electron | 保持当前无边框桌面体验，减少重写 |
| 前端 | 原生 HTML/CSS/JS、ES modules | React/TypeScript | 当前页面为单页原生实现，先保持行为一致 |
| 数据库 | SQLite 3、WAL、`busy_timeout` | PostgreSQL（团队版） | 本地单用户低运维；团队版再分离存储 |
| PDF | pypdf、pdfplumber、pdf.js | OCRmyPDF/Tesseract | 文本层和坐标读取已有适配层 |
| 文档 | python-docx、Pandoc/Typst/MiKTeX 可选 | 独立转换 worker | 支持 Windows 本地安装与可诊断失败 |
| AI 协议 | 自研统一 adapter | LiteLLM/官方 SDK（逐项评估） | 统一 timeout、重试、usage 和 streaming |
| MCP | stdio、Streamable HTTP/SSE | MCP SDK 沙箱 | 保留显式发现和权限矩阵 |
| 测试 | unittest、Playwright、Node 自检 | pytest、k6/Locust | 现有测试可直接运行，压力测试后加 |
| 打包 | PyInstaller + 安装器 | MSIX/Tauri bundle | 复现现有 Windows 发布链 |

## 3. 分层目录结构

```text
research-workbench/
├─ apps/desktop/
│  ├─ server.py                 # 静态页、JSON API、SSE、桌面入口
│  └─ web/
│     ├─ index.html             # 壳层、工作流、设置和对话 UI
│     ├─ capabilities.js       # Tool/Skill/MCP 能力 UI
│     └─ workflow-layout.js     # 自定义步骤布局
├─ packages/
│  ├─ contracts/                # provider catalog、事件和 API schema
│  ├─ database/                 # SQLite 连接、迁移、仓储
│  ├─ workspace-core/           # guards、pdfio、证据和文件边界
│  ├─ workspace-service/        # workflow、agent、team、schedule
│  ├─ ai-runtime/               # provider adapter、重试、usage、预算
│  └─ connectors/               # Zotero、Obsidian、RSS、Mail、搜索源
├─ migrations/                  # 版本化 SQL 迁移
├─ tests/                       # 单元、运行时、浏览器和安全测试
├─ tools/                       # 启动、自检、打包和安装器
├─ data/                        # SQLite、papers、notes、out、uploads
└─ doc/                         # 诊断和复现文档
```

## 4. 数据模型与迁移

### 4.1 核心实体

| 表 | 关键字段 | 作用 |
|---|---|---|
| `research_tasks` | `id,title,question,material_system,stage,base_dir` | 项目和当前阶段 |
| `chats`/`chat` | `id,assistant_id,task_id,scope,role,content,events` | 通用/项目/步骤会话 |
| `assistants` | `id,source,name,rules,model_id,enabled` | 内置和用户助手 |
| `providers`/`model_profiles` | `base_url,api,api_key,models,is_active` | Provider 和模型 |
| `papers` | `sha256,pages,text_chars,ocr_status` | PDF 去重和元数据 |
| `paper_pages` | `paper_id,page_no,text` | 页级文本和引用定位 |
| `evidence` | `paper_id,page_no,field,value,excerpt,kind` | 证据台账 |
| `notes`/`md_notes` | `task_id,paper_id,markdown` | 阅读和项目笔记 |
| `search_cache` | `source,doi,url,pdf_url,abstract` | 检索缓存 |
| `steps` | `task_id,stage,status,summary` | 十阶段执行记录 |
| `artifacts` | `task_id,kind,path,sha256` | 导出产物 |
| `runs` | `command,status,input_hash` | 可重复运行记录 |
| `team_members`/`team_tasks`/`team_events` | 成员、任务、事件 | 团队和委派 |
| `capability_bindings` | `assistant_id,kind,item_id,enabled` | 权限矩阵 |
| `capability_mcp_config`/`capability_mcp_tools` | 加密配置、工具缓存 | MCP 连接和发现 |

### 4.2 可靠性扩展表

```sql
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
  checkpoint TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE task_attempts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE run_events (
  run_id TEXT NOT NULL, event_id INTEGER NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(run_id,event_id)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, assistant_id TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
  input_hash TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
```

迁移策略：启动时只执行 `migrations/<version>_<name>.sql`，由 `schema_migrations(version, checksum, applied_at)` 记录；禁止继续堆叠无版本的 `ALTER TABLE`。升级前自动备份 SQLite、验证 WAL checkpoint，失败时保留原库和迁移日志。

## 5. HTTP/SSE API 契约

### 5.1 项目与论文

```text
GET    /api/projects
POST   /api/projects                         {title, question, material_system, base_dir}
GET    /api/projects/:id/summary
POST   /api/projects/:id/papers/ingest      multipart PDF
GET    /api/projects/:id/papers
POST   /api/papers/:id/evidence              {page_no, field, value, excerpt}
GET    /api/projects/:id/evidence
POST   /api/projects/:id/export              {kind, target}
```

### 5.2 Agent、助手和团队

```text
GET/POST /api/chats
POST     /api/chats/:id/bind                 {task_id, assistant_id}
GET      /api/assistants
POST     /api/assistants                     {name, rules, model_id, enabled}
POST     /api/teams/:id/members
POST     /api/teams/:id/leader
POST     /api/teams/:id/tasks
POST     /api/agent                          {chat_id, message, max_steps, idempotency_key}
POST     /api/runs/:id/cancel
GET      /api/runs/:id/events?after=<id>
GET      /api/runs/:id/stream                SSE + Last-Event-ID
```

### 5.3 Provider、Capability 和 MCP

```text
GET/POST /api/providers
POST     /api/providers/:id/discover
POST     /api/providers/:id/models
GET      /api/capabilities?assistant_id=...
POST     /api/capabilities/bind              {kind, item_id, enabled}
POST     /api/mcp/config                     # 保存加密配置，不执行命令
POST     /api/mcp/discover                   # 用户显式触发
POST     /api/mcp/reveal                     # 二次确认后短时返回
POST     /api/mcp/revoke                     {server, tool}
```

### 5.4 事件格式

```json
{
  "event_id": 42,
  "run_id": "run-...",
  "type": "assistant|tool_call|tool_result|usage|progress|error|done",
  "payload": {},
  "created_at": "2026-10-17T00:00:00Z"
}
```

客户端断线后带 `Last-Event-ID` 重连；服务端先回放 `run_events`，再继续实时事件。所有事件 payload 做大小限制和敏感字段脱敏。

## 6. Agent、权限与 MCP 模型

### 6.1 上下文层次

```text
system policy
  -> global Leader / project Partner / team member identity
  -> assistant rules
  -> enabled Skill summary
  -> project memory and evidence context
  -> bounded chat history
  -> user message
```

工具调用流程：模型返回 tool call → JSON schema 校验 → allowlist 检查 → 权限级别检查 → 项目/路径边界检查 → 执行并记录审计 → tool result 回注模型。任何一步失败都返回结构化错误，不直接执行模型字符串。

### 6.2 权限级别

| 权限 | 允许 | 必须阻止 |
|---|---|---|
| `read_only` | 搜索、读取项目、查看论文和日历 | 文件写入、数据库写、邮件、远程写、MCP 写工具 |
| `workspace_write` | 工作区文件、笔记、草稿、项目内数据库写 | 外发邮件、消息和远程写入需确认 |
| `full_access` | 全部已启用工具 | 仍受路径、大小、超时、审计和撤销约束 |

### 6.3 Skill 与 MCP

- Skill 只从用户级和项目级 `.agents/skills` 根目录读取；解析 frontmatter，限制 1 MiB，读取前显式 `skill_read`。
- MCP 配置使用 Windows DPAPI；非 Windows 仅使用明确标注的本地权限保护，不把它宣称为系统密钥环。
- MCP server 启动前检查命令白名单/用户确认、cwd、环境变量、URL 和私网地址；执行时限制并发、stdout/stderr、响应大小和超时。
- 工具发现结果带 `config_hash`；配置、撤销或权限变化立即清除旧缓存，避免“已撤销工具”继续可调用。

## 7. 十阶段状态机与数据流

```text
definition
  -> idea -> search -> download -> notes -> outline
  -> map -> write -> review -> entrans -> convert
  -> completed
```

每个阶段允许 `pending/running/paused/failed/done`；阶段输入为项目上下文、前一阶段产物和显式用户操作，输出为带 SHA256 的 artifact。失败只写 `failed` 和 error code，不伪造 `done`。阶段转换需满足前置条件，例如：

- `download` 只接受 OA URL 或用户手动导入。
- `notes` 需要成功入库的 PDF；扫描件标记 `likely-scan`，不得假装有文本。
- `write` 可生成 `[TODO: Require Evidence <id>]`，但不能把无证据句子登记为事实。
- `convert` 失败时保存编译日志、命令、版本和输入哈希。

## 8. 搜索、PDF、证据与写作链

1. **搜索**：并发请求 OpenAlex/Crossref/arXiv，规范化 DOI、标题和作者；按 DOI/规范化标题去重；写入 `search_cache`，保留 source 和 fetched_at。
2. **下载**：只接收 HTTPS OA 链接；写入临时文件，校验 MIME、大小和 SHA256 后原子移动到 `data/papers`；403/付费墙进入人工下载清单。
3. **解析**：pypdf 提取页文本，pdfplumber 提供词坐标；页面文本为空时标记 OCR 候选；不在解析层生成事实。
4. **证据**：`add_evidence` 校验 page range 和 `excerpt_matches`；AI 抽取结果逐条过同一闸门，拒绝项保存 reason。
5. **大纲/写作**：文档版本带 `base_revision`；生成建议先进入 Diff，用户 Accept 后再写入正文；引用台账记录 paper/page/excerpt 和状态。
6. **审查/翻译**：分片任务有顺序号和 checkpoint；重复提交使用 idempotency key；输出保留原文、译文、模型、时间和输入哈希。

## 9. 前端页面与交互

- **Shell**：启动卡片、侧栏、主题、命令面板、状态中心和全局 Agent。
- **Projects/Space**：项目列表、概览、团队、文献、笔记、日程。
- **Workflow**：十阶段时间轴、阶段输入/输出、运行日志、Diff 和证据抽屉。
- **Assistants/Team**：助手卡片、规则编辑、模型绑定、成员和 Leader 设置。
- **Settings**：Provider、模型、Tool、Skill、MCP、凭证 reveal 和权限矩阵。
- **连接器页**：Zotero、Obsidian、RSS、Mail；每个连接器显示授权范围、最近同步和错误。

交互要求：所有长任务都有进度、取消、重试和恢复；编辑器使用 revision/optimistic locking；错误提示给出用户动作而不是堆栈；移动端能力设置使用可滚动对话框；主题对比度保持 WCAG AA。

## 10. 安全设计清单

- 凭证：API key/MCP header/env 只存保护后的值；JSON/SSE/日志默认 mask；reveal 需要显式操作并短时有效。
- 路径：所有 target `resolve()` 后检查 `commonpath`；拒绝符号链接越界、UNC 未授权路径和危险扩展名。
- 网络：URL scheme 白名单、SSRF 私网/loopback 拦截、连接/read timeout、响应体上限和重定向次数上限。
- 文件：PDF/MD/CSV/PNG/TIFF 白名单、大小上限、临时文件隔离、原子写入和 SHA256。
- Prompt injection：外部论文/网页文本作为不可信数据；工具权限不由文本指令改变；引用必须回到原文页码。
- 审计：记录 actor、assistant、tool、project、input hash、结果、时长和错误码，严禁记录原始密钥。
- 备份：数据库备份前 checkpoint；恢复后运行 schema/checksum/foreign-key 检查。

## 11. 测试策略

| 层级 | 必测内容 | 门禁 |
|---|---|---|
| 单元 | schema、证据闸门、路径、脱敏、协议解析、重试 | 100% 关键模块覆盖；`unittest` 全绿 |
| 合约 | Provider wire、搜索源字段、MCP tool schema | 固定 fixture；上游变化可定位 |
| 集成 | SQLite WAL、任务恢复、PDF、导出、连接器 | 断电/进程杀死后状态正确 |
| 浏览器 | 启动、导航、助手、团队、能力、工作流、移动端 | Playwright 无 console error、无 4xx/5xx |
| 前端静态 | JS 语法、主题对比度、DOM id | Node 自检全绿 |
| 安全 | 伪造 tool call、越权路径、SSRF、泄密扫描 | 负向用例全部拒绝 |
| 压力 | 1,000 run、10 万页、并发写入、MCP 大输出 | P95、内存、句柄、SQLite 锁可量化 |

CI 最低命令：

```powershell
python -W error::ResourceWarning -m unittest discover -s tests -t .
node apps/desktop/web/theme.test.mjs
node apps/desktop/web/ids.test.mjs
```

## 12. Windows 与 Docker 部署

### Windows 本地

1. Python 3.10+ 创建 `.venv`，执行 `pip install -r requirements.txt`。
2. 设置可选的 `RESEARCH_WORKBENCH_DB` 指向用户数据目录，避免把测试库写入源码树。
3. `python apps/desktop/server.py --port 8765 --no-open` 启动服务；pywebview/Edge WebView2 负责桌面壳。
4. `tools/start.bat` 负责环境探测；`tools/check.bat` 负责回归；PyInstaller 生成发布目录。
5. 应用升级前备份 `data/research.sqlite3`、`data/papers`、`data/notes` 和 `data/out`。

### Docker（可选服务模式）

- 基础镜像 Python slim；挂载 `/app/data`；健康检查 `GET /api/health`。
- 默认禁用桌面窗口，只运行 HTTP/SSE；MCP stdio 需要显式 capability 和容器内命令白名单。
- 生产团队版把 SQLite 替换为 PostgreSQL/对象存储，但保留同一 repository 和事件契约。

## 13. 分阶段实施路线

| 阶段 | 交付 | 验收 |
|---|---|---|
| M0 基线 | 目录、SQLite、项目/PDF/证据、静态 UI、mock provider | 可创建项目、导入 PDF、导出证据 |
| M1 Agent | Assistant、Provider、Chat、tool loop、read_only | 伪造工具调用被拒；事件可回放 |
| M2 十阶段 | 搜索、OA 下载、翻译、大纲、图谱、写作、审查、转换 | 十阶段 happy path + 失败路径可恢复 |
| M3 Team/MCP | Leader、成员、Delegate、Skill、MCP discovery/revoke | 权限矩阵、凭证脱敏、进程回收通过 |
| M4 可靠性 | tasks/checkpoint/SSE replay/retry/cancel/audit | 断线、重启、重复提交不丢不重 |
| M5 发布 | Windows 打包、Docker 服务、备份恢复、性能基线 | CI 全绿；安装/升级/回滚可验证 |

## 14. MVP 与完整版本差异

**MVP**：SQLite、本地单用户、OpenAI-compatible 一个 wire、三源搜索、PDF 文本层、十阶段基本入口、一个 Assistant、SSE 无恢复但有明确限制、Mock MCP。  
**完整版本**：四类协议、Provider 弹性层、Team/Delegate、Skill/MCP 权限、证据台账、任务恢复、SSE replay、Word/LaTeX worker、连接器、审计、压力和安全门禁。

## 15. 开源借鉴与合规边界

- Open WebUI：借鉴多模型/知识库工作台信息架构；<https://github.com/open-webui/open-webui>
- Khoj：借鉴本地知识库和语义检索；<https://github.com/khoj-ai/khoj>
- AnythingLLM：借鉴 workspace、RAG 和连接器；<https://github.com/Mintplex-Labs/anything-llm>
- Onyx：借鉴企业搜索权限和 connector 分层；<https://github.com/onyx-dot-app/onyx>
- LangGraph：借鉴 checkpoint、状态图和人机协同；<https://github.com/langchain-ai/langgraph>
- PaperQA2：借鉴带证据的科学问答；<https://github.com/Future-House/paper-qa>
- LlamaIndex：借鉴数据连接器和 workflow；<https://github.com/run-llama/llama_index>
- GrokSearch：借鉴搜索 adapter 和 MCP 封装；<https://github.com/GuDaStudio/GrokSearch>

合规要求：每次引入依赖记录仓库 URL、commit/tag、许可证 SPDX、源码修改范围和归属声明；不复制 AGPL/GPL 代码到当前 BSD/MIT/PSF 依赖集合，除非明确接受对应分发义务；所有许可证以项目当前仓库为准并在发布前复核。

本次运行环境没有暴露可调用的 GrokSearch MCP server，因此复现实现应把搜索层抽象为 `SearchProvider`：普通 HTTP provider、GrokSearch MCP provider 和离线 fixture 都实现同一接口，运行时通过配置选择，避免把某个搜索服务硬编码到工作流。

## 16. 完成定义（Definition of Done）

- 十阶段从项目创建到格式导出有可重复 fixture，成功和失败路径均有测试。
- Assistant/Team/Delegate/Tool/Skill/MCP 权限均有正向和负向测试，凭证扫描为零。
- 所有长任务可查询状态、取消、重试、恢复；SSE 支持重连和事件回放。
- SQLite migration、备份恢复、外部 provider mock、资源句柄和句柄泄漏门禁全部通过。
- Windows 安装包、命令行服务和 Docker 服务均能启动；README、诊断报告和许可证清单同步更新。

