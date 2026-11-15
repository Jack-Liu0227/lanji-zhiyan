本轮检查基于 `main@9680df0`，确认 8 个上一轮回归用例未覆盖的问题。后续已完成这 8 项修复；下面保留修复前的复现记录，文末列出实现与验证情况。

验证使用临时数据库、临时项目目录及真实 Microsoft Edge（Playwright，headless）。Zotero 请求和向量嵌入使用离线替身，未访问真实文献库或模型服务。并发和迟到响应使用受控时序，避免依赖偶然发生的竞争。

| 编号 | 优先级 | 确认的问题 | 主要位置 |
| --- | --- | --- | --- |
| F01 | P1 | 新建同名项目笔记覆盖已有笔记 | `apps/desktop/web/views/notes-and-settings.js:1367` |
| F02 | P1 | 并发保存后文件、数据库、搜索内容不一致 | `packages/workspace-service/project_notes.py:289` |
| F03 | P1 | 多篇本地文献被同步到同一个 Zotero 条目 | `packages/connectors/zotero_sync.py:177` |
| F04 | P1 | 编辑时直接新建笔记会移除编辑器并丢失尚未保存的编辑 | `apps/desktop/web/views/notes-and-settings.js:1240`、`:1445` |
| F05 | P2 | 删除项目后仍能检索到它的内容，重新同步也不清理 | `packages/workspace-service/workflow_db.py:1169`、`semantic.py:283` |
| F06 | P2 | 项目内词法检索的结果被其他项目挤掉 | `packages/workspace-service/semantic.py:483` |
| F07 | P2 | 旧保存响应更新了新草稿状态，误显示已保存 | `apps/desktop/web/views/notes-and-settings.js:1378` |
| F08 | P2 | 修改项目笔记标题没有同步持久化的标题字段 | `apps/desktop/web/views/notes-and-settings.js:1378`、`project_notes.py:295` |

1. **F01：新建同名笔记覆盖原文。**

   浏览器中创建标题为 `Collision Audit` 的笔记，正文为 `FIRST_NOTE_MUST_REMAIN`；返回目录后再次新建同名笔记，正文为 `SECOND_NOTE_NEW_DOCUMENT`。两次都保存到 `笔记/文献精读笔记/Collision Audit.md`，读取结果只剩第二份正文，数据库也只有一条有效笔记记录。创建路径只由清洗后的标题决定，保存接口不区分“创建”与“更新”，底层 `asset_service.atomic_write_text()` 在第 61 行使用 `os.replace()` 替换目标。不同标题清洗后重名、默认标题相同也会进入同一路径。这里是原笔记当前内容被替换，不能据此断言历史修订副本也被删除。

   修复方向：创建应使用独立 ID 或经过原子冲突检查的唯一路径；编辑已有笔记才允许更新同一对象。

2. **F02：文件原子替换没有覆盖数据库和索引事务。**

   对同一已有笔记，保存 A 写完文件后暂停；保存 B 完成文件、数据库和索引更新；最后让 A 完成剩余数据库操作。两个调用均成功返回，但磁盘为 `SECOND_WRITER_CONTENT_B`，`md_notes.content` 为 `FIRST_WRITER_CONTENT_A`。项目搜索对 A 返回 1 条，对实际磁盘内容 B 返回 0 条。代码在 `project_notes.py:289` 完成文件替换后，才在第 292–312 行更新记录、失效语义索引并注册资产；`register_file()` 又读取当前磁盘哈希，可能把 B 的哈希与 A 的索引正文绑定在一起。

   修复方向：按规范文件路径协调完整保存流程，覆盖文件写入、数据库记录、资产索引和修订；同时处理不同编辑入口的版本冲突。

3. **F03：Zotero 匹配缺少一对一约束。**

   本地两篇标题均为 `Editorial`、DOI 分别为 `10.1000/first` 与 `10.1000/second`；远端只有一篇同名、无 DOI 的 `REMOTE01`。预览返回两条指向同一个远端键的拉取动作，`conflicts` 为空。应用预览后，两篇本地 DOI 都变为空、作者都被远端作者替换，两条映射均指向 `REMOTE01`。`used` 集合在第 194 行记入远端键，但匹配候选的第 182–186 行不使用它，且没有对整体映射的歧义做检查。上一轮处理的是一个本地条目匹配多个远端候选，本次是反向的多个本地条目匹配同一远端候选。

   修复方向：对整个同步计划验证映射唯一性，歧义匹配不得生成直接覆盖元数据的动作。

4. **F04：编辑页的新建操作破坏编辑器及待保存内容。**

   在真实页面打开已保存的项目笔记，修改正文后，在 650 ms 自动保存触发前点击侧栏“新建笔记”。旧文未写入磁盘，新的 `mdProj.content` 为空，页面中 `#mdEditorShell` 数量从 1 变为 0，后续自动保存抛出 `Cannot read properties of null (reading 'value')`。新建处理器直接替换 `mdProj`，没有先保存待编辑内容；`mdDashLoad()` 的编辑分支用 `host.innerHTML` 替换当前容器，而编辑器已经挂载在该容器内部，随后 `mdMountEditor()` 找不到已被删除的节点。即使没有待保存的编辑，直接在编辑页新建仍会触发编辑器移除。

   修复方向：离开当前笔记前妥善提交或保留草稿；重绘包含共享编辑器的容器前先移动或保留编辑器节点。

5. **F05：删除项目遗漏语义派生数据。**

   创建笔记并完成语义索引后调用 `delete_task()`。项目和 `md_notes` 表均变为空，但 `sem_chunks`、`sem_state`、`sem_fts` 各残留 1 行。再次执行 `semantic.sync()` 扫描源数量为 0，残留不变。全局 `semantic.hybrid()` 仍通过向量和词法两条路径返回被删除笔记的完整片段，项目名称为空。`delete_task()` 未清理这些表，`sync()` 只遍历现存来源，不回收已删除来源。

   修复方向：项目删除需要同步失效全部关联语义数据和矩阵缓存；索引同步应能够回收历史孤儿数据。

6. **F06：项目范围在全局截断后才应用。**

   构造另一个项目的 121 条强匹配内容，以及当前项目中 1 条确实匹配的内容。默认 `k=30` 时全局查询先取 120 条，再在 Python 中按项目过滤，导致当前项目结果为 0。增大全局查询数量能找到该条内容，数据库也确认当前项目存在 1 个匹配块。关闭向量召回的受控验证中，`hybrid()` 返回 `mode=empty`。FTS 和短词 LIKE 分支均先执行 `LIMIT k * 4`，随后才执行范围判断。

   修复方向：在 SQL 查询中先限定项目，再排序和分页；向量服务不可用时也要保留完整的项目内词法召回。

7. **F07：旧请求完成后覆盖当前草稿状态。**

   在浏览器中让 A 的实际保存请求完成服务端写入，但暂停返回给调用者；把编辑状态切换为新空白草稿 B，再释放 A 的响应。B 的 `mdProj.title` 被改成 A 的标题，`mdDraft` 从 `true` 变为 `false`，界面显示“已保存”，但 B 的路径仍为空、标题输入框也为空。代码在 `await api(...)` 后继续读写全局 `mdProj`，没有确认它仍然是发起请求的笔记。

   此项使用实际页面函数和真实保存接口，但为隔离 F04 的 DOM 移除问题，新草稿状态切换在浏览器脚本中按新建处理器的同样赋值方式执行，没有重绘编辑器。这是受控异步状态验证，不应描述为完整鼠标端到端操作。

   修复方向：保存时捕获笔记身份和请求版本，响应只能更新对应笔记；已经离开的编辑器状态不能被旧响应修改。

8. **F08：标题输入框的修改不会持久化到笔记标题。**

   在浏览器把已有笔记标题从 `Collision Audit` 改成 `Renamed Audit Title` 并保存。编辑框立即显示新标题，Markdown 一级标题也变成新标题；但重新读取笔记接口返回的 `title` 仍是 `Collision Audit`。前端保存请求只发送 `project_id`、`path` 和 `content`；后端 `write()` 使用旧记录标题，并且更新 SQL 只更新内容。因此重新打开时旧标题会覆盖编辑框，数据库与文件正文也出现标题不一致。

   修复方向：明确标题作为笔记字段保存，并同步资产标题、检索索引和目录展示；标题变更是否影响路径应单独处理。

验证记录：

- `python -m unittest tests.test_semantic tests.test_project_notes_rehome -v`：18 项通过，运行于隔离的应用目录和数据库。这些已有用例没有覆盖上述复现场景；本轮未重新运行完整测试套件。
- 后端临时探针：`C:/Users/HK/AppData/Local/Temp/rw-followup-audit-probes.py`；结果：同目录 `rw-followup-audit-results.json`。覆盖 F01 后端写入、F02、F03、F05、F06。
- 浏览器临时探针：`C:/Users/HK/AppData/Local/Temp/rw-followup-browser-probes.py`；结果：同目录 `rw-followup-browser-results.json`。覆盖 F01、F04、F07、F08。
- F04 截图：`C:/Users/HK/AppData/Local/Temp/rw-followup-missing-editor.png`。
- 相关测试日志：`C:/Users/HK/AppData/Local/Temp/rw-followup-baseline-tests.txt`。

临时探针文件没有加入版本控制。原有未跟踪的 `doc/test-reports/`、`output/` 和 Word 文件保持不变。以上是本轮覆盖范围内的确认结果，不代表其他模块不存在问题。

修复实现：

- F01：新建项目笔记使用 UUID 文件名，标题独立保存。服务端支持只创建模式，目标已存在时拒绝覆盖。
- F02：同一数据库的笔记保存、删除、移动、项目删除和资产重建共用可重入锁，覆盖文件、数据库、索引和修订更新。锁按数据库协调，也覆盖不同编辑入口与目录操作。页面读取时取得内容哈希，保存时校验版本；过期编辑无法覆盖其他编辑器的内容或复活已删除笔记。
- F03：同步计划检查多个本地条目对同一远端键的占用，将全部歧义匹配列为冲突并移除对应动作；pull、push、merge 和历史重复映射均受保护。
- F04：新建、返回目录和树节点切换前保存当前内容；保存失败保持编辑器和草稿。重绘前把共享编辑器移出将要被替换的容器。目录加载通过请求序号和项目身份丢弃过期响应。
- F05：删除项目清理语义块、FTS、索引状态和向量缓存；增量同步也清理历史孤儿。嵌入返回后在写事务中重新确认来源仍存在，防止已删除项目被迟到的嵌入结果重新写入索引。
- F06：FTS 与 LIKE 都先在 SQL 中按项目过滤，再排序和截取结果；空项目范围返回空结果。
- F07：保存按笔记对象排队，捕获对应编辑版本；返回结果只更新发起请求的笔记，且有较新编辑时不会标记为已保存。自动保存计时器也绑定原笔记。
- F08：保存接口传递标题，后端同步两类笔记记录与资产标题；目录展示按规范路径读取标题，兼容旧文件名与 UUID 文件名。

新增 14 项回归：`tests/test_followup_audit.py` 中 9 项后端测试，以及 `tests/test_web_runtime.py` 中 5 项浏览器测试。覆盖同名创建、两种编辑入口并发保存、过期版本、标题重开、删除与索引中断、FTS/LIKE 项目范围、多对一 Zotero 映射、延迟保存与失败后保留草稿。

最终验证：

- 全量 `python -m unittest discover -s tests -t . -v`：**512 项全部通过，248.723 秒，零跳过**。使用临时 `RW_HOME` 与数据库，设置 `RW_TEST_LOWBOX=1`，包含真实 Edge 浏览器和本地 HTTP 接口测试。
- `node --test apps/desktop/web/theme.test.mjs apps/desktop/web/ids.test.mjs`：2 项通过；前端语法检查、变更 Python 文件编译检查与 `git diff --check` 通过。
- 首次完整运行中有 5 项路径配置用例受临时启动器高优先级环境变量影响；修正启动器的隔离配置后重新运行全套，得到上述 512 项通过结果。
- 本轮完整日志：`C:/Users/HK/AppData/Local/Temp/rw-followup-full-tests.txt`。
