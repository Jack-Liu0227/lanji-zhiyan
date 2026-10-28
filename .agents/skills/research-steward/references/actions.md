# 应用工具路由

以下为应用工具名称。只使用当轮实际提供且启用的工具；本文件不是可任意调用的 HTTP 或 shell 接口。

| 目标 | 工具与关键参数 | 验证 |
| --- | --- | --- |
| 查已有项目 | `project_list()` | 返回项目 ID、名称、根目录和当前项目 |
| 新建项目 | 未绑定对话中 `project_create(title, question, material_system, base_dir)`；没有目录偏好时省略 base_dir | 返回 project_id 和实际 base_dir；不冒充已切换当前对话 |
| 按目标自动立项与装配 | `project_create_from_idea(idea, graph_type, skills, experts, workflow, auto_start_graph, request_key)`；已有项目传 project | 返回真实项目、专家、团队、节点绑定、计划文件和实际 run_id；同 key 同配置复用，不重复开跑 |
| 查询统一资料 | `project_resources(project, kind, query)`；读取具体文本传 asset_id | kind 为 literature/note/manuscript/experiment/output/file；返回原路径、来源、版本，不生成副本 |
| 写实验记录或稿件 | `project_resource_write(project, kind, title, content)`；更新传 asset_id、expected_hash | 实验记录使用同一 Markdown 笔记服务；稿件保存在论文正文目录，返回真实 asset_id 与版本 |
| 查专家 | `assistant_manage(action="list")` | 从结果取得真实 ID |
| 新建/修改专家 | `assistant_manage(action="create", name, description, rules, model_id)`；修改用 action="update", id | 返回 assistant；不修改权限或凭据 |
| 创建团队 | `team_create(name, purpose, assistant_ids)` | 首个专家为负责人；返回 team 和 members；未指定专家时用独立团队协调员，智研管家不担任 Leader |
| 调整已有团队 | `team_manage(action="list"/"update"/"add_member"/"update_member"/"set_leader", team_id, member_id, assistant_id, prompt, model_id)` | 返回真实配置；团队与成员必须属于当前项目 |
| 选择已有工作流 | `workflow_manage(action="list"/"select", layout_id)` | list 返回与科研平台一致的步骤名称；select 绑定项目 |
| 团队执行 | `team_board(project)` 查可用团队配置；指定 project 和 team_id 调用 `team_board`，再用 `team_assign` | 跨项目团队返回调用项目的实例 ID 和成员 ID；沿用这些 ID 派活，检查真实运行与交付状态 |
| 创建工作流 | `workflow_create(project, name, description, nodes, edges)`；旧步骤可用 steps | 创建并绑定唯一 LangGraph 流程；不等于节点已执行 |
| 创建技能 | `skill_create(name, description, instructions)` | 返回项目 `.agents/skills` 内路径；name 用小写字母、数字、横线 |
| 读取技能资源 | `skill_read(skill_id, resource)`；resource 默认 SKILL.md | resource 为技能内相对文本路径 |
| 登记 MCP | `mcp_register(name, url)` 或 `mcp_register(name, command, args)` | enabled=false，后续在设置中启用并连接 |
| 待办计划 | `project_task_manage(action="list"/"create"/"update", title, description, acceptance, dependencies, priority, assistant_id, due_date, id, status)` | dependencies 为同项目的前置计划 ID；关联执行尚未交付时不能标记 DONE |
| 实际执行任务 | `task_status()` 查执行者与任务；`task_create(goal, executor_binding_id, plan_id)` → `task_start(id)` → `task_status(id)` | 一条计划可关联多个任务；每次执行保留独立尝试、模型、工具事件和产物，助手不能代替人工核查 |
| 笔记 | `project_note_list()` → `project_note_read(path)` / `project_note_write(path, content)` | path 为 `笔记/分类/文件名.md` |
| 日程 | `calendar_list` / `calendar_add(title, date, time, kind)` | 日期 YYYY-MM-DD，时间 HH:MM；不把时间塞到标题里 |
| 定时任务 | `schedule_manage(action="create", project, name, prompt, cron, workflow_id 或 assistant_id)` | workflow_id 指向项目流程；与普通项目任务共享交付和核查，核对下次运行时间与输出目录 |
| 邮件读取 | `mail_accounts()` → `mail_list(account_id, folder, page)` → `mail_read(account_id, folder, uid)` | 账户来自实际结果；保留 UID，不虚构邮件正文 |
| 文献研究 | `paper_search` → `paper_download` → `paper_read` → `notes_compose` | 只使用当前项目返回的 result_id / paper_id / note_id；精读属于下载精读步骤 |
| 论文翻译 | `paper_translate(paper_id, action="start"/"status")` | 启动后查询真实进度；只有完成的译文 PDF 可交付，精读笔记不算译文 |
| 文本论文工具 | `paper_tool(tool="translate"/"polish"/"references"/"convert", text, direction, terminology, format)` | 复用全局工具箱的实现与模型，转换返回真实文件；PDF 转换需要项目上下文 |
| 写作与导出 | `draft_write` / `draft_review` / `format_convert` | 工具成功后报告真实产物路径 |

工作流步骤：`idea` 创新点审查、`search` 论文搜索、`download` 下载精读、`notes` 论文翻译、`outline` 确认大纲、`map` 梳理精读、`write` 文章撰写、`review` 稿件审查、`entrans` 中译英、`convert` 格式转换。按需求组合，避免重复步骤。

批量创建时先确认所需对象与项目，再依次使用返回的 ID：创建专家 → 创建团队 → 创建工作流 → 创建待办 → 写计划笔记。已有对象应先查询和复用。涉及全局配置的变更在结果中明确说明。
