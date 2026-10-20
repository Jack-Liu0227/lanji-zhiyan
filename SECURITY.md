# 安全口径（览籍智研）

本文件只讲**边界在哪、哪些是启发式**，不讲功能。改安全相关代码前先读这里，别把启发式当边界。

---

## 1. 信任模型

**唯一的边界是操作系统。** 程序进程内的一切（路径白名单、打开扩展名白名单、出站脱敏、凭据加密、审计）都是**事故预防**，不是隔离 ——
**这句话是给分发对象看的**：白名单防的是"手滑把文件写到系统目录 / 模型顺着上下文去读 C:\\Users 下的别的东西"，
它挡不住"有人在这台机器上写个 20 行 Python 用同一个账号去读"。别对外宣称成沙箱。

白名单只有一处实现：`paths.resolve_allowed()`（`allowed_roots()` = 自己的 `data/` 与 `uploads/papers/notes/out`
+ 用户在选择器里亲手选过的**项目目录**和**对话产出目录**）。越界一律抛错并记 `audit.jsonl` 的 `deny` 行。
其余模块（`workflow_converters._safe_path`、`workflow_documents.read_attachment`）都只是转调它，别再另写一套。

- 同一个 Windows 用户下的任何进程，都能读 `%LOCALAPPDATA%\览籍智研\`（`data/`、`user/`）、能用 DPAPI 解开里面的凭据、能 `curl 127.0.0.1:8765` 调 API。
- 路径白名单（`workflow._safe_path`）挡的是"手滑把文件写到系统目录"，不是"挡住有心的本机程序"。
- 出站脱敏（`guards.py`）是规则层（手机号/身份证/邮箱/银行卡/密钥/Bearer/私钥），**人名、机构、地址不做**；正则永远可以被绕过。
- 审计（`data/_backup/audit.jsonl`）用于**事后查**，不阻止任何操作。

想要真边界，只有三条路：给 HTTP 加一次性 token、把程序降到受限账号、把 `data/` 放进系统级加密卷。这三条都**没做**（见 §4）。

## 2. 凭据

| 位置 | 保护 |
|---|---|
| `providers.api_key` / `model_profiles.api_key` | DPAPI 密文（`securestore.py`，绑当前 Windows 用户） |
| `user/mail.json` 的 `password` | 同上 |
| `capability_mcp_config.protected`（MCP） | 同上，格式一致 |
| 前端拿到的一切 key | 一律 `***`；邮箱只回 `hasPassword`；Provider 卡片不渲染 key |

- 非 Windows 退回 `local:<base64>`（== 不加密），`securestore.security()` 会如实告诉界面。
- **key 与域名绑定**：填 key 时记下 `base_url` 的 host（`providers.key_host` / `model_profiles.key_host`），发请求前校验（`workflow._assert_key_host`）；换域名必须重填 key，防止 key 被引到别的站点。
- 外部子进程（xelatex 等）用 `fileguard.clean_env()` 启动：剥掉 key/token/secret 类环境变量。

## 3. 文件

- **读**：`workflow._safe_path()` —— 绝对路径 → `resolve()` → `is_relative_to` 白名单（`data/`、`data/uploads`、`papers/`、`notes/`、`out/` + 项目目录）。这些目录现在都在 `%LOCALAPPDATA%\览籍智研\data\`，不再在代码树里（`paths.py` 是唯一来源）。
- **写**：同上 + 覆盖前 `fileguard.before_write()` 留快照（`data/_backup/.rwbak/<日期>/`，每文件留 10 份），可用 `POST /api/file/rollback` 回滚；写/删/打开都记 `data/_backup/audit.jsonl`。
- **打开外部程序**：`fileguard.open_document()` 只放行文档类扩展名；`.exe/.bat/.cmd/.ps1/.lnk/.url` 等一律拒绝。
- **移动/删除**：没有通用 move 接口；`shutil` 只有两处 `copy2`（库备份、图产物）；唯一的删除是"删项目时连带删该项目在 `papers/` 下的文件"，且强制校验父目录是 `papers/`。
- **Obsidian**：接受用户自选的 vault 路径（按设计不走 `_safe_path`），只改 frontmatter 的几个键。

## 4. 已知缺口（别当成"已加固"）

1. HTTP 监听 `127.0.0.1`、**无鉴权、无 CORS 头**：本机进程能调全部 API（网页读不到响应）。
   *浏览器跨站那条已堵*：`do_POST` 先判 `Origin`（非本机一律 403）再判 `Content-Type: application/json`（415），
   **`/api/upload` 也不例外**（2026-10-20 之前它在两道闸门之前 return，任何网页都能往 `uploads/` 塞文件）。
   早退前会 `_drain()` 读掉 body，客户端拿到的是 403/415 而不是 RST。
2. 进程以当前 Windows 用户**全权限**运行：没有降权、没有沙箱。
   *部分已收敛（2026-10-20）*：外部子进程 **xelatex** 现在跑在**低完整性（Low IL）**里
   （`packages/workspace-core/lowbox.py`），`data/` 与各项目目录被标成 Low ——
   内核强制它**只能写这些目录**，写桌面 / Windows / 用户目录一律拒绝（实测）。
   **2026-10-20 晚升级**：整个 **HTTP 服务端进程**（库、工具、xelatex、babeldoc）都跑在 Low IL 里，
   只剩窗口宿主（WebView2，Medium）在外 —— WebView2 在 Low IL 下建不出窗口（实测 HTTP 通、窗口没有）。
   结构：`server.py`（Medium，只画窗口）→ 起 `pythonw server.py --port N --no-open` 的 **Low IL 子进程**；
   关窗时父进程 `_stop_low_server()` 一并收掉，不留僵尸。
   写白名单的目录（HOME、data/、各项目目录、对话产出目录）都标成 Low；其余一律 Medium → 服务端写不进。
   **仍未沙箱**：MCP 服务器子进程（各服务器要自己的 cache）。
   **用户新选的目录走「代建通道」**：窗口宿主（Medium）在 `127.0.0.1` 随机端口上开一个小帮手，
   只认带随机 key 的 `POST /grant`（key 只进沙箱子进程的环境变量，且被 `clean_env()` 剥离），
   替服务端 `mkdir` 并标 Low。建项目、选对话产出目录都会先过这一道 ——
   否则 Low 进程在桌面这种 Medium 目录下 `mkdir` 会被内核拒（`[WinError 5] 拒绝访问`）。
   **已知副作用**：Obsidian 库如果不在授权目录里，写回 frontmatter 会被内核拒 ——
   要么把库放进项目/数据目录，要么对它跑一次 `icacls "<库路径>" /setintegritylevel (OI)(CI)L /T`。
   回滚：`icacls <目录> /setintegritylevel M /T`。
3. 脱敏只覆盖结构化字段（规则层）；人名/机构/地址会原样发给模型提供方。
4. DPAPI 只挡"把数据目录拷到别的账号/机器"；挡不住同用户进程。**顺带一句**：`user/` 拷到另一台机器或重装系统后解不开，换机只能重填 key；`user/` 千万不要放进 OneDrive 等同步目录。
5. ~~PDF 翻译的子进程（BabelDOC）仍继承完整环境变量~~ —— **已修**（2026-10-20）：`papertran._env()` 改用
   `fileguard.clean_env()`，key/token/secret/本应用私有变量全剥掉；模型 key 本来就是命令行
   `--openai-api-key` 传的，子进程不需要任何 `*_KEY`。回归检查 `tests/test_papertran.BabeldocEnvTest`。
6. `executors/tools/`（以及 `agents/`、`skills/`）**允许模型自己写**：不做签名、不做哈希校验，写进去就能被执行。这是产品决策，不是疏忽 —— 但它意味着"模型被骗 → 直接落一个可执行文件"是可能的。要收紧就加 `tools/manifest.json`（名字 + sha256）并只认白名单。

## 5. 报告问题

自己用的话，直接看 `data/audit.jsonl` 定位"哪个动作改了什么"。要给多人用，先把 §4 第 1、2 条补上再来谈分发。
