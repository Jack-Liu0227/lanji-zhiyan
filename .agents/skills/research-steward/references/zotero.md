# Zotero 与项目副本

Zotero 管理题录、数据库和附件位置；项目目录管理研究过程和派生产物。关联以 item key、来源路径和内容 SHA-256 为依据，不能只靠文件名。

1. 用 `zotero_search(query, start, limit)` 查询本机题录，`zotero_item(key)` 确认目标。
2. 绑定项目后调用 `zotero_import_pdf(key)`。它仅从连接器解析出的 PDF 路径读取，创建普通文件副本，写入项目 `文献库/Zotero副本/`，记录来源和散列；不创建硬链接或符号链接，不覆盖原附件。
3. 用返回的 paper_id 精读，或以返回的副本路径读取附件。分析结果写入 `笔记/`，稿件与导出使用项目内相应目录。再次导入不会覆盖已经加工的副本。
4. 附件未下载或 Zotero 未启动时报告错误；不要自行查写 zotero.sqlite 或猜测其他 PDF 路径。
5. Agent 调用 `paper_download` 只把 OA PDF 放入当前项目的暂存目录，不写入 Zotero，也不触发后台同步。
6. 只有用户明确要求把暂存文献写回 Zotero 时，才调用 `zotero_staged_import`；先预览，再等待用户确认，最后在 Full Access 下执行。附件使用 Zotero `imported_file` 流程交给附件管理插件，暂存 PDF 保留。
7. 其他题录写回仍遵守 Web API 的版本/预览要求；普通项目研究不触发双向同步。

绑定时应选择独立的科研项目文件夹。不要选择 Zotero 数据目录、storage 子目录或配置的 Linked Attachment Base Directory。链接附件可能散落在任意文件夹，不能单凭目录名判断它是否受 Zotero 管理；经连接器识别的原附件也始终只读。外部 MCP 由其自身进程执行，应用的项目路径检查不能视为对任意 MCP 的操作系统沙箱。

官方依据：[附件与链接文件](https://www.zotero.org/support/attaching_files)、[Zotero 数据目录](https://www.zotero.org/support/zotero_data)。
