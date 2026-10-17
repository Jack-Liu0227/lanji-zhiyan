# 安全口径（览籍智研）

本文件只讲**边界在哪、哪些是启发式**，不讲功能。改安全相关代码前先读这里，别把启发式当边界。

---

## 1. 信任模型

**唯一的边界是操作系统。** 程序进程内的一切（路径白名单、打开扩展名白名单、出站脱敏、凭据加密、审计）都是**事故预防**，不是隔离：

- 同一个 Windows 用户下的任何进程，都能读 `%LOCALAPPDATA%\ResearchWorkbench\`（`data/`、`user/`）、能用 DPAPI 解开里面的凭据、能 `curl 127.0.0.1:8765` 调 API。
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

- **读**：`workflow._safe_path()` —— 绝对路径 → `resolve()` → `is_relative_to` 白名单（`data/`、`data/uploads`、`papers/`、`notes/`、`out/` + 项目目录）。这些目录现在都在 `%LOCALAPPDATA%\ResearchWorkbench\data\`，不再在代码树里（`paths.py` 是唯一来源）。
- **写**：同上 + 覆盖前 `fileguard.before_write()` 留快照（`data/_backup/.rwbak/<日期>/`，每文件留 10 份），可用 `POST /api/file/rollback` 回滚；写/删/打开都记 `data/_backup/audit.jsonl`。
- **打开外部程序**：`fileguard.open_document()` 只放行文档类扩展名；`.exe/.bat/.cmd/.ps1/.lnk/.url` 等一律拒绝。
- **移动/删除**：没有通用 move 接口；`shutil` 只有两处 `copy2`（库备份、图产物）；唯一的删除是"删项目时连带删该项目在 `papers/` 下的文件"，且强制校验父目录是 `papers/`。
- **Obsidian**：接受用户自选的 vault 路径（按设计不走 `_safe_path`），只改 frontmatter 的几个键。

## 4. 已知缺口（别当成"已加固"）

1. HTTP 监听 `127.0.0.1`、**无鉴权、无 CORS 头**：本机进程能调全部 API（网页读不到响应）。
2. 进程以当前 Windows 用户**全权限**运行：没有降权、没有沙箱。
3. 脱敏只覆盖结构化字段（规则层）；人名/机构/地址会原样发给模型提供方。
4. DPAPI 只挡"把数据目录拷到别的账号/机器"；挡不住同用户进程。**顺带一句**：`user/` 拷到另一台机器或重装系统后解不开，换机只能重填 key；`user/` 千万不要放进 OneDrive 等同步目录。
5. PDF 翻译的子进程（BabelDOC）仍继承完整环境变量（它自己要用 `ZHIPUAI_API_KEY` 跑翻译），**这条没剥**；xelatex 那条已经剥了。
6. `executors/tools/`（以及 `agents/`、`skills/`）**允许模型自己写**：不做签名、不做哈希校验，写进去就能被执行。这是产品决策，不是疏忽 —— 但它意味着"模型被骗 → 直接落一个可执行文件"是可能的。要收紧就加 `tools/manifest.json`（名字 + sha256）并只认白名单。

## 5. 报告问题

自己用的话，直接看 `data/audit.jsonl` 定位"哪个动作改了什么"。要给多人用，先把 §4 第 1、2 条补上再来谈分发。
