# 应用工具路由

以下为应用工具名称。只使用当轮实际提供且启用的工具；本文件不是可任意调用的 HTTP 或 shell 接口。

| 目标 | 工具与关键参数 | 验证 |
| --- | --- | --- |
| 查已有项目 | `project_list()` | 返回项目 ID、名称、根目录和当前项目 |
| 新建项目 | 未绑定对话中 `project_create(title, question, material_system, base_dir)`；没有目录偏好时省略 base_dir | 返回 project_id 和实际 base_dir；不冒充已切换当前对话 |
| 查专家 | `assistant_manage(action="list")` | 从结果取得真实 ID |
| 新建/修改专家 | `assistant_manage(action="create", name, description, rules, model_id)`；修改用 action="update", id | 返回 assistant；不修改权限或凭据 |
| 创建团队 | `team_create(name, purpose, assistant_ids)` | 首个专家为负责人；返回 team 和 members；未指定专家时用已有管家 |
| 团队执行 | `team_board(project)` 查团队列表；用创建/列表返回的 team_id 调用 `team_board(team_id)`，再用 `team_assign(team_id, member, prompt)` | member 使用该团队返回的成员 ID；检查排队/运行状态，勿把派发当完成 |
| 创建工作流 | `workflow_create(name, description, steps)` | 创建模板并选为当前项目工作流；不等于步骤已执行 |
| 创建技能 | `skill_create(name, description, instructions)` | 返回项目 `.agents/skills` 内路径；name 用小写字母、数字、横线 |
| 读取技能资源 | `skill_read(skill_id, resource)`；resource 默认 SKILL.md | resource 为技能内相对文本路径 |
| 登记 MCP | `mcp_register(name, url)` 或 `mcp_register(name, command, args)` | enabled=false，后续在设置中启用并连接 |
| 项目任务 | `project_task_manage(action="list"/"create"/"update", title, description, priority, assistant_id, due_date, id, status)` | 状态 TODO / IN_PROGRESS / REVIEW / DONE；priority P0/P1/P2 |
| 笔记 | `project_note_list()` → `project_note_read(path)` / `project_note_write(path, content)` | path 为 `笔记/分类/文件名.md` |
| 日程 | `calendar_list` / `calendar_add(title, date, time, kind)` | 日期 YYYY-MM-DD，时间 HH:MM；不把时间塞到标题里 |
| 定时任务 | `schedule_manage` | 根据实际 schema 选择 action；绑定项目和已启用专家，核对下次运行时间与输出目录 |
| 邮件读取 | `mail_accounts()` → `mail_list(account_id, folder, page)` → `mail_read(account_id, folder, uid)` | 账户来自实际结果；保留 UID，不虚构邮件正文 |
| 文献研究 | `paper_search` → `paper_download` → `paper_read` → `notes_compose` | 只使用当前项目返回的 result_id / paper_id / note_id |
| 写作与导出 | `draft_write` / `draft_review` / `format_convert` | 工具成功后报告真实产物路径 |

工作流步骤：`idea`、`search`、`download`、`notes`、`outline`、`map`、`write`、`review`、`entrans`、`convert`。按需求组合，避免重复步骤。

批量创建时先确认所需对象与项目，再依次使用返回的 ID：创建专家 → 创建团队 → 创建工作流 → 创建待办 → 写计划笔记。已有对象应先查询和复用。涉及全局配置的变更在结果中明确说明。
