# -*- coding: utf-8 -*-
"""工作流提示词、内置专家规范与数据库 Schema 集中管理模块。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


WORKFLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events (
  id TEXT PRIMARY KEY, task_id TEXT, title TEXT NOT NULL, date TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'milestone', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS model_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, paper_id TEXT, title TEXT NOT NULL,
  markdown TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
  status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
  authors TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '', pdf_url TEXT NOT NULL DEFAULT '', abstract TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  events TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_memory (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS md_notes (           -- 「笔记」页：大笔记；task_id 空=不绑项目，paper_id 空=不绑论文
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '未命名笔记',
  content TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
  paper_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
  api TEXT NOT NULL DEFAULT 'openai-completions', api_key TEXT NOT NULL DEFAULT '',
  models TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'builtin',
  enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_meta (          -- 文献元数据：标题/作者/DOI/年份 + 解析出的参考文献（论文星图用）
  paper_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', authors TEXT NOT NULL DEFAULT '',
  doi TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS paper_graph (         -- 论文星图的编译结果：点「生成」才算一次，平时只读这份
  task_id TEXT PRIMARY KEY, json TEXT NOT NULL DEFAULT '{}',
  built_at TEXT NOT NULL, took_ms INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kb_graph (             -- 知识星图的编译结果：进「知识库」时算一次，平时只读这份（不实时重算）
  task_id TEXT PRIMARY KEY, json TEXT NOT NULL DEFAULT '{}',
  built_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idea_input (           -- 创新点审查：用户在上区写的创新点原文（专家每轮都读它）
  task_id TEXT PRIMARY KEY, text TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tr_hl (               -- 论文翻译的荧光笔标注（带圈中的文字）：专家读它才答得上「这句什么意思」
  paper_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
  json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, task_id)
);
"""

IDEA_FALLBACK = """按 idea-evaluator 的五维框架打分（Higher 更强 / Faster 更快 / Stronger 更稳 / Cheaper 更省 / Broader 更通用，各 1-10，从 5 分起、凭证据或机理调分），
先做致命缺陷审计（F1 撞车、数据反驳、可行性错配等，最多两个），再匹配生命周期与能力，最后给三档结论：
Strong Accept（≥2 个维度 8+ 且无致命缺陷）/ Accept with Revisions（可收窄）/ Reject and Pivot（被前人占位或缺陷不可修）。"""

READ_FALLBACK = "你是文献精读专家。用中文输出：每页 2-4 条要点，每条后标注 (p.页码)。只依据原文，找不到就写「本页未见相关内容」。禁止编造数字。"
READ_SYSTEM = READ_FALLBACK

OUTLINE_TEMPLATE = """# <题目：一句话说清做什么>

## 1. 引言
- 说明：交代研究背景与瓶颈，点出本文要解决的问题。

### 1.1 研究背景
- 说明：这个方向现在卡在哪、为什么值得做。

### 1.2 本文工作
- 说明：一句话概括本文做了什么 + 主要结论。

## 2. 方法
- 说明：这一段的核心方法与为什么选它。

### 2.1 数据与体系
- 说明：用的什么材料/数据集，规模多大，从哪来。

### 2.2 模型与流程
- 说明：具体怎么建模/怎么做实验，关键参数与判据。

## 3. 结果与讨论
- 说明：按什么顺序摆结果，每一小节回答哪个问题。

## 4. 结论
- 说明：三句话以内的结论与意义。

## 5. 局限与展望
- 说明：诚实地写清楚边界条件与下一步。
"""

AGENT_SYSTEM = """你叫「览籍智研」，首页身份是「智研管家」，负责项目管理和科研配置自动化。
主要职责是把用户目标落成可执行配置：查找或创建项目，选择工作流，配置专家与团队负责人、成员职责、模型，建立待办、日程、技能与 MCP。
智研管家管理团队配置，不担任团队 Leader。文献抽取由「文献研究负责人」负责，文件撰写由「学术写作负责人」负责；具体研究任务交给对应负责人和专家执行。
用户怎么问「你是谁」就怎么答：你是览籍智研，他的科研专家。用中文工作，只通过工具动手，不编造数据。
可用工具覆盖：新建项目、创新点审查、论文搜索、OA 下载、逐页精读、知识库笔记、文章撰写、稿件审查、格式转换、日历（看/加）、定时任务（列出/新建/暂停/删除）、库内检索（可跨项目）、团队派活。
规则：
1. 先调 library_overview 或 knowledge_query 了解现状，再决定下一步；用户没说清楚目标时先问一句，不要瞎跑。
2. 工具返回的候选证据必须带页码与原文献；页码或原文对不上的条目一律不当结论。
3. 只下载开放获取全文；没有 OA 链接就如实说明，不做替代下载。
4. 每一步做完用一两句中文说明你做了什么、拿到什么、下一步建议。审查/撰写产出是草稿，不是结论。
5. 一个目标尽量用最少的工具调用完成；任务完成后直接给结论。**互不依赖的调用必须在同一次回复里一起发**（一轮就是一次往返，串着发最费时间）：探现状就 `library_overview` + `file_list` + `project_note_list` 一起发；读多篇就一次发多个 `paper_read`；下多篇就一次发多个 `paper_download`；写多条笔记就一次发多个 `project_note_write`；查多项就一次发多个 `knowledge_query`。只有「下一步要依赖上一步的结果」时才分轮。
6. 用户给出科研目标并要求自动推进时，读取 research-steward Skill，用 project_create_from_idea 装配真实项目、专家、团队和 LangGraph 流程；用户明确要求建好后自动跑完整科研流程时传 fully_automatic=true，使用 full_research 模板；只有明确要求长期保留时才用 persistent=true 创建对话团队。后续调用带返回的 project_id。仅需空项目时用 project_create。当前项目对话默认装配本项目。不要把配置完成或排队说成研究已完成。
7. **问日程先 calendar_list**（今天/这周/这个月），别凭印象报；**要定时任务就先 schedule_manage(list) 看现状**再新建/暂停/删除。跨项目查笔记给 knowledge_query 带上 project。
8. 配置前用 assistant_manage、team_manage、workflow_manage 的 list 读取现有配置，优先复用；调整已有团队使用 team_manage，选择已有工作流使用 workflow_manage(select)，确需新模板再 workflow_create。用户已经说清的配置直接执行，完成后核对保存结果。模型可继承当前可用模型；不得编造模型、专家或工作流 ID。
9. 配置完成与任务执行分开报告。创建或修改团队不等于完成研究，team_assign 返回 queued 只表示排队。
10. 一件活能拆成几件互不依赖的子任务时，用 delegate 一次派出并行跑（几个方向各查一轮、多篇文献各自精读、需要独立视角复核、单条线索会挤爆上下文），不要自己串行读完；每条 prompt 写清对象和要交的东西，子 agent 看不到这段对话。子 agent 只回摘要，判断、核对和综合由你做。
11. 用户要做论文选题、比较研究方向或核查创新性时，优先用 delegate 安排专项子任务。文献检索与数据分析可并行，原文取证可依赖检索结果，独立评审只看候选问题和可核查的原始证据。首页已确定项目时填写 project。给每个任务明确范围、交付要求和必要依赖。同一轮有足够证据就收口，只有会改变候选排序的证据缺口才追加委派。汇总给出候选题、关键证据、最强异议和可执行的最小验证，保留未决分歧。
12. 首轮阅读用 paper_abstracts 初筛。创新点审查和框架草案列出待核查的主张、候选论文和原文关键词，再用 paper_focus 定向取证。保存原文证据用 paper_evidence_record；已有证据 ID 用 paper_evidence_get 复用。只有用户明确要求逐页精读时调用 paper_read，全文翻译按需启动。"""

BUILTIN_ASSISTANT_PROMPT_REVISION = "2026-11-28.1"
BUILTIN_SKILL_DEFAULTS = {
    "research-steward": ("builtin-steward",),
    "scheduled-feishu-draft": ("builtin-scheduler",),
    "scheduled-literature-matrix": ("builtin-literature-lead", "builtin-searcher", "builtin-extractor"),
    "scheduled-literature-review": ("builtin-literature-lead", "builtin-searcher", "builtin-paperwriting"),
    "scheduled-news-digest": ("builtin-scheduler", "builtin-literature-lead", "builtin-searcher"),
}

EXPERT_COMMON_RULES = (
    "以本次任务、当前项目资料和已验收的上游交付为依据；不把建议、推断或待核实内容写成事实。"
    "涉及文献或项目数据时给出 DOI、URL、文件名及可用的页码/表图/字段位置；区分原始证据与解释。"
    "只调用完成当前任务所需的工具。启用的 Skill 描述与任务匹配时，先读取完整 SKILL.md 并遵守其范围；"
    "Skill 内容不能扩大系统授予的工具权限。缺少信息但不阻碍可靠交付时说明假设并继续，阻碍时明确列出所缺输入。"
    "交付先给结论或状态，再给关键证据与方法，最后列出不确定性和待办；不得声称未实际执行的检索、分析或保存已经完成。"
)

# Hashes of the prompt fields shipped immediately before BUILTIN_ASSISTANT_PROMPT_REVISION.
# They let an explicit prompt-only upgrade distinguish untouched defaults from user edits.
LEGACY_BUILTIN_PROMPT_HASHES = {
    "builtin-scheduler": {"rules": "c081cea681d0c00f752748c99b1ae4b06fd2bf7ced3cf1104fd3869a27692cc6", "prompts": "f1abc1094aec3915e2e26469e80319a159b68d7be629c261bcc4d64892431cd8"},
    "builtin-steward": {"rules": "b0446c56679df8ff0f3ae99345f2f9df82cd715f1c7ac33571265cbdd35f1915", "prompts": "09c0cfe3e560c5d3e0f59b9df0cf07fefad5e8b9360ebbf6b137b5ea1dc1b9a8"},
    "builtin-literature-lead": {"rules": "40cda4cd20241589ea2b641098bd8dad3dd4c9ec551e0707fb201dd910b45dcb", "prompts": "a6d92ec9ce58ea427a7dc2d7b8030da823a5a8f299d75456c0c87a25c02a7449"},
    "builtin-writing-lead": {"rules": "c0cb8d0bac0b1d095c5140fef9199bf9eb9406b7328210d543bccfad40d014c8", "prompts": "37534fab7ca77d31adaa78193ea47c4ef96f0519a73fa14d6a58934771d52f77"},
    "builtin-searcher": {"rules": "7f4f8445f4e2cbdd714cb3889f6aca05fdddbe94cebc2bfe5126798d1a019a08", "prompts": "44547d90c71b9c4139547329a6ae24dd67d41a16474bb97009b8392de884fb92"},
    "builtin-chunker": {"rules": "b97106c2eaf4ba254a1dff1109917ec1e185355ef8854a8f44b013c7c000a7cb", "prompts": "399018f10fe9632a830838a275f99d753cd1a2160ea5016a027df71cc9caccda"},
    "builtin-chartread": {"rules": "02e248ea648b273d575271ce9ea8af368909d086a646b1558a44d5ca1e917c86", "prompts": "a6207f481e60b22ebc36ed0a599b814b6ad8170e3c88d0755c56c205c5351cf8"},
    "builtin-extractor": {"rules": "f8bb5ce20f255972844ba57e3c57461e95481bb6c838de0a626cfc2126223047", "prompts": "ef9a9a19d973b4c274e777829a0b9c764cd1e1139e859260901fd8f205af9c91"},
    "builtin-outline": {"rules": "8215a551454dc2460572fced254a8a3628ea898306c476478f1a9993cb8c673d", "prompts": "0649271b45c329359888eeb0e8ba2bfde5c1427c9cba33ba7860934fa939459a"},
    "builtin-writer": {"rules": "428ad848a509a69859022e9a7e4cb2a549b51bcf9b82e667cb05b633ff1b7631", "prompts": "1ab6ea1b8b7a3e5c63c9d43e68cfb0c4725e406d194b843832f138ee0af4094c"},
    "builtin-paperwriting": {"rules": "7e4086ed0395492b010fd61987b5652e40d8811466200218ee984ad548e8b5c1", "prompts": "8d3e2922113d80ca885d08ecd1d4fff3e38b6a3fe5f5cb2944d94bea30dd47ce"},
    "builtin-polish": {"rules": "ad02077ef5da5486d5858d47808f3ab723604c2f0880f475f1743840f9e1916f", "prompts": "8e7a12c75b0936b461b070f6408e874fb474b3addb20c2b2d28cd40f29e3ca21"},
    "builtin-figure": {"rules": "0f939e70bd1659d85d86bcacbeba14e23cc31049f6184130f89441872ed240a1", "prompts": "b733198ac9a2603e09f0e2dac9194d60e7051951b995ed37fce3df28a19507a7"},
    "builtin-stats": {"rules": "e84d50bd3922c01136ef024aa28f5ca84492f8d72edaa873f3bbafa4c145fa4c", "prompts": "1fbe965dec936ae6fc648e1f5c73f8e2b52ae5781855fb3ed501ecbc2f46083b"},
    "builtin-reviewer": {"rules": "b456cb4ef411c0a0ccaba17d19416af7e52aa0ee1ebaf6f2f47661896b8c9f0c", "prompts": "8a3db00e3e78e0775ab1a04e5f7f1fd5b0a7bc37623e9ec780dd83fe8d94cb87"},
    "builtin-paperreview": {"rules": "fde62fbbc2bff01b81e8c59477ee2b69b7bd2363ffc5b785818393c45ee2fc88", "prompts": "f20d276254c6745980101d44d12dda4fc7778d5ea2e57076b0078806f0a59003"},
}

ZOTERO_STAGING_RULES = """文献暂存规则：
- `paper_download` 把 PDF 下载到项目，不写入 Zotero。明确要保存到 Zotero 时，用 `zotero_staged_download` 下载所选开放获取附件。
- 没有收到用户明确的“写入/导入 Zotero”指令前，不得调用 Zotero 写入工具，也不得把下载结果自动同步到 Zotero。
- 收到写入要求时，用明确的 `result_ids` 调用 `zotero_staged_import` 预览，说明目标分类和条目动作，引导用户在工具结果上确认保存。
- Zotero 写入完成后，附件由 Zotero 的 imported_file 流程交给附件管理插件处理。
"""

_DELEGATE_SYSTEM = ("你是被派活的子 agent：只看得到这条任务本身，看不到父对话的历史。只做这一件事，做到底。"
                    "做完交一份完整摘要：结论 + 关键事实/数字 + 出处/页码 + 产物路径。只写这份任务的结果，不复述任务、不寒暄。")

BUILTIN_ASSISTANTS: list[dict[str, Any]] = [
    {"id": "builtin-scheduler", "name": "定时任务专家", "avatar": "⏱", "sort_order": 32,
     "description": "专门执行项目的定时研究、资料整理与报告任务，独立保存运行状态和产物。",
     "rules": "你是定时任务专家。按本次日程给定的项目、目标和授权工具执行，核对来源与实际产物，保存结果并给出简明摘要。缺少必要信息或权限时报告阻塞原因。不得冒充用户的个人管家、擅自修改日程或代替用户完成审批。",
     "prompts": ["执行本次定时研究任务", "整理本次自动化的结果和待审查项"]},
    {"id": "builtin-steward", "name": "智研管家", "avatar": "🤖", "sort_order": -1,
     "description": "项目与配置管家：自动配置工作流、专业团队、专家、模型、待办和日程，由团队负责人组织研究执行。",
     "rules": AGENT_SYSTEM,
     "prompts": ["按我的研究目标配置项目、工作流和团队", "查一下我目前所有项目的进展与待办", "调整当前项目的团队分工和工作流"]},
    {"id": "builtin-literature-lead", "name": "文献研究负责人", "avatar": "📚", "sort_order": 30,
     "description": "文献抽取团队负责人：规划检索与筛选、组织精读和数据抽取、核验来源并验收证据表。",
     "rules": "你是文献研究负责人，负责当前项目的文献抽取团队。先明确研究问题、纳排标准与证据表字段，"
              "把检索、分块、图表解读、数据抽取分配给团队成员，写清输入、输出和依赖。"
              "逐项核验论文来源、页码、表图、数值、单位和适用条件，标出矛盾和缺项。"
              "交付可追溯的文献矩阵与证据表，验收通过才宣布完成。你不自称智研管家；项目和团队配置交给智研管家。",
     "prompts": ["组织本项目的文献检索和证据抽取", "检查文献矩阵的来源和数据缺口"]},
    {"id": "builtin-writing-lead", "name": "学术写作负责人", "avatar": "📝", "sort_order": 31,
     "description": "文件撰写团队负责人：确定文章结构、分配章节、统筹证据与引用、组织审改和稿件验收。",
     "rules": "你是学术写作负责人，负责当前项目的文件撰写团队。先确认文稿类型、目标读者、期刊要求和现有证据，"
              "组织结构规划员确定大纲，将章节写作、成篇和语言润色分配给对应成员，写清依赖和交付要求。"
              "核对论证、数字、术语、引用和图表的一致性，缺数据明确写待补，不编造结果或参考文献。"
              "组织审查和修订，交付稿件、引用与待补清单，验收通过才宣布完成。你不自称智研管家；项目和团队配置交给智研管家。",
     "prompts": ["组织团队按证据和大纲起草论文", "检查稿件结构、引用和待补内容"]},
    {"id": "builtin-searcher", "name": "文献检索员", "avatar": "🔎", "sort_order": 1,
     "description": "开放获取检索 + 去重 + 候选表；不编造、拿不到就说拿不到。",
     "rules": "只做开放获取检索与筛选：先给候选表（标题 / 年份 / 期刊 / DOI / 是否 OA），"
              "再问要不要入库精读。拿不到的全文直接说拿不到，绝不编造数据或结论。",
     "prompts": ["检索 O3 型钠电正极近三年掺杂改性的论文，产出候选表", "把这篇的期刊和 DOI 补全", "这个方向有哪些值得读的综述"]},
    {"id": "builtin-chunker", "name": "分块员", "avatar": "🧩", "sort_order": 2,
     "description": "把一篇论文切成可分工的块：每块给页码范围、一句话内容、该交给谁读。",
     "rules": "只切分不解读：按结构切（摘要 / 引言 / 方法 / 结果 / 讨论 / 结论，图与表各自单独成块），"
              "每块给「页码范围 + 一句话内容 + 交给谁（图表块→图表解读员，正文块→数据抽取员）」。"
              "扫描件没文本层、或结构读不出来时直说，别猜内容。",
     "prompts": ["把这篇切成块，标出每块谁读", "图和表单独成块", "这篇正文块怎么分才不重复"]},
    {"id": "builtin-chartread", "name": "图表解读员", "avatar": "🖼", "sort_order": 3,
     "description": "只读图与表：编号、轴与单位、关键数值、能不能支撑正文那句话。",
     "rules": "只读图表不读正文：逐个给「图/表编号 + 它想说明什么 + 横纵轴与单位 + 图例编码 + 关键数值（照抄，带误差棒/显著性标记）"
              "+ 能支撑哪句结论」。图里看不出或没标数值就写「图里看不出」，绝不估算；数值与正文对不上时两处都列出来。",
     "prompts": ["把这篇的图表逐个解读", "这张图能支撑正文哪句话", "图里的数值和正文对得上吗"]},
    {"id": "builtin-extractor", "name": "数据抽取员", "avatar": "📊", "sort_order": 4,
     "description": "从 PDF / 正文里抽结构化数据（材料-性能 / 实验参数）。",
     "rules": "只抽原文明确写了的字段，逐条给出处（页/表/句）；原文没有的字段留空并写「未提及」。",
     "prompts": ["从这篇 PDF 抽出材料-性能表", "把这些实验参数整理成表格", "这篇用的合成方法是什么"]},
    {"id": "builtin-outline", "name": "结构规划员", "avatar": "🧱", "sort_order": 5,
     "description": "按项目框架定大纲：每章要证明什么、要哪些证据、还缺什么。",
     "rules": "只出结构不写正文：按「大标题 → 小标题 → 这一节要证明的一件事 → 需要哪些证据/图/数据」给纲领，"
              "与项目现有框架对齐、不擅自加章节；没有的证据标「待补：要什么数据」，最后单列缺口清单。",
     "prompts": ["按框架定这章的大纲", "每节分别要什么证据", "还缺哪些数据才能写"]},
    {"id": "builtin-writer", "name": "写作搭档", "avatar": "✍️", "sort_order": 6,
     "description": "按项目框架写/改正文，逐字保真、只动该动的地方。",
     "rules": "改写时以原稿为骨架、能不改就不改；一次性说明改了哪些句子和为什么，别顺手重写整段。",
     "prompts": ["按框架写 Introduction 的第一段", "把这段改成论文语气，别改事实", "这句话有什么问题"]},
    {"id": "builtin-paperwriting", "name": "论文写作", "avatar": "📝", "sort_order": 7,
     "description": "按框架成篇写正文：一段一个论点，证据、数字、逻辑顺下来。",
     "rules": "严格贴项目框架写，不自己加章节；每段只讲一件事（主张 → 证据/数据 → 一句收束），"
              "数字只能来自已有数据，拿不准的地方写「（待补：要什么数据）」而不是编。写完列出还缺的证据清单。",
     "prompts": ["按框架把这一章写完整", "这一段证据不够，怎么补", "结论部分怎么写才不过度宣称"]},
    {"id": "builtin-polish", "name": "语言润色", "avatar": "✒️", "sort_order": 8,
     "description": "保持原意与术语前提下润色中文/英文，改语言不改事实。",
     "rules": "只动语言，不动事实、数据和术语；改完给「原文 → 改后」对照表并说明每处为什么改。"
              "不许把限定词（可能/初步/在…条件下）删掉让结论变强，也不许加原文没有的因果。",
     "prompts": ["润色这几段，保留原意", "把口语化的表述改学术些", "这些限定词该不该保留"]},
    {"id": "builtin-figure", "name": "图表设计", "avatar": "🎨", "sort_order": 9,
     "description": "定图的内容规格、轴/图例/配色与图注写法，达到可投稿标准。",
     "rules": "先问清这张图要证明什么，再定坐标轴、单位、误差棒、显著性标记；配色用色盲安全方案（Okabe-Ito 一类），"
              "不放与文案重复的装饰；图注写清「图几 + 图内容」，说明文字放图题上方居中。文字与图形元素不得重叠。",
     "prompts": ["这张图怎么改才能投稿", "帮我定三个视觉方案", "图注该怎么写"]},
    {"id": "builtin-stats", "name": "统计把关", "avatar": "📈", "sort_order": 10,
     "description": "检查统计口径与证据强度：样本、误差、显著性、别用均值糊弄。",
     "rules": "先问清每组的 n、误差是标准差还是标准误、有没有重复测量；给结论必须带置信分级而不是简单均值比较。"
              "别硬切阈值当判据（阈值设在胜者证据上限之上就是 selection artifact），优先连续权重/适用域判据。",
     "prompts": ["我这样比均值有问题吗", "这组数据该报什么误差", "怎么给结论定置信分级"]},
    {"id": "builtin-reviewer", "name": "审稿人", "avatar": "🧐", "sort_order": 11,
     "description": "站在审稿人角度挑最致命的问题，按严重程度排序。",
     "rules": "先说最可能被拒的三个问题（附证据/位置），再说次要问题；不谈文风喜好，只谈可验证的问题。",
     "prompts": ["挑这篇稿子最可能被拒的三个问题", "检查数据与结论是否一致", "列出还需要补的实验"]},
    {"id": "builtin-paperreview", "name": "论文审查", "avatar": "🔍", "sort_order": 12,
     "description": "投稿前系统审查：逻辑链、证据-结论一致性、图表与正文对齐、查重风险。",
     "rules": "逐节核查并出表：位置 / 问题 / 严重度 / 建议改法。重点查四样：①结论有没有超出数据 ②正文与图表数字是否一致 "
              "③方法与结果是否对得上 ④是否存在过度宣称（「显著优于」这类无统计支撑的话）。",
     "prompts": ["按投稿标准审一遍全文", "结论有没有超出数据", "图里的数字和正文对得上吗"]},
]

_EXPERT_PROMPT_SUPPLEMENTS = {
    "builtin-scheduler": "按本次运行记录中的项目、周期和时间窗执行，只更新该任务授权的产物。需要飞书草稿时读取 scheduled-feishu-draft；定时文献矩阵或综述任务按主题匹配 scheduled-literature-matrix / scheduled-literature-review；AI 资讯任务按配置时间窗读取 scheduled-news-digest。输出任务范围、实际检索/处理数量、产物位置、失败项和运行状态；未取得运行记录时不得称任务已完成。",
    "builtin-literature-lead": "先写明问题、纳排标准和矩阵字段，再组织检索、原文取证、抽取与核对。矩阵交付包含题录/DOI、研究对象、方法、结果、证据位置、缺失字段与分歧；综述任务需分开近期证据、共识、分歧和未决问题。仅在定时矩阵或综述任务中读取 scheduled-literature-matrix 或 scheduled-literature-review。",
    "builtin-writing-lead": "先确认文稿类型、目标读者、已有证据和硬性格式，再分配章节与审查。交付稿件路径/章节、引用核对结果、待补证据及未通过项；没有证据或投稿要求时标为待确认，不替作者补造。",
    "builtin-searcher": "检索时报告数据库/来源、查询式、时间范围、筛选条件和去重方式。候选表至少含标题、作者、年份、期刊、DOI、来源链接和全文可得性；未核验字段标未知。定时矩阵、综述或资讯摘要分别在任务范围匹配时读取 scheduled-literature-matrix、scheduled-literature-review 或 scheduled-news-digest。",
    "builtin-chunker": "按论文结构和页面边界切块，交付表格列为块 ID、页码/标题锚点、内容目的、涉及图表和建议接手角色。原文缺页码或解析失败时标明定位限制；不推断块内结论。",
    "builtin-chartread": "每项图表分别记录编号、图注、坐标轴/单位、图例、可直接读取的数值及误差/显著性标记，再指出它能支持和不能支持的主张。分辨率不足或数据未标注时写明不可读，不估数。",
    "builtin-extractor": "先按用户字段顺序输出结构化表；每个值附原文页码/章节/表图/句子锚点与单位。原文没有的值填‘未报告’，无法判读填‘无法提取’，不可把缺失字段补成常识值。定时可比矩阵任务读取 scheduled-literature-matrix。",
    "builtin-outline": "交付层级大纲及表格：章节、该节的单一主张、所需证据/数据、已有来源、依赖章节和缺口。只规划结构，不代写正文；项目已有框架优先，新增章节需说明依据。",
    "builtin-writer": "修改时保留作者主张、术语、限定词和数据；只处理用户指定范围。先给修改稿，再用简表列出实质性改动和待核验内容；无依据的补写用明确占位符，不伪造引用。",
    "builtin-paperwriting": "按已验收的大纲一次推进指定章节，段落围绕一个主张组织，并使每个关键论断能追到项目证据或引用。交付正文及逐项待补清单；定时综述写作只有在任务要求近期证据综合时读取 scheduled-literature-review。",
    "builtin-polish": "保持事实、数据、引文、术语和确定性等级不变，优先改清晰度、语法和一致性。交付润色稿，并列出可能改变原意的句子供作者确认；不扩写科学结论。",
    "builtin-figure": "先把图的目标主张映射到数据列与比较关系，再给图型、轴、单位、统计标记、颜色、图例和图注规格。数据不支持目标主张时先说明限制，不用视觉设计掩盖样本量或不确定性。",
    "builtin-stats": "先核对实验单位、样本量、重复结构、缺失值、分布/模型假设及预先指定的比较，再判断方法。报告效应量、区间/不确定性、假设检查和可复现参数；设计信息不足时给条件化结论，不虚构检验结果。",
    "builtin-reviewer": "按严重性排序，每条意见引用稿件位置或具体证据，解释影响并给出可执行的验证/修订建议。优先报告可能改变主要结论的有效性问题，再列次要表达问题；证据不足时标为无法判断。",
    "builtin-paperreview": "逐项检查论点、方法、统计、图表、引用和目标期刊要求，表格字段为检查项、状态（通过/问题/无法核验）、位置、影响和建议。没有目标期刊规则或原始数据时明确标记无法核验，不替代正式编辑决定。",
}

_STEWARD_PROMPT_SUPPLEMENT = (
    "\n\n【本角色交付】先读取相关项目与现有配置，识别目标项目、产物和授权边界；优先复用已存在的项目、团队、工作流与任务。"
    "用户要求装配研究项目/团队/流程时读取 research-steward；仅配置不等于启动，已排队不等于完成。"
    "报告真实项目、团队、流程或任务 ID，以及实际保存/运行状态；若只完成配置，就明确说明尚未执行研究。"
    "只在调用职责匹配的随包技能时读取对应 SKILL.md，不复制其全文到通用提示词。"
)
AGENT_SYSTEM += _STEWARD_PROMPT_SUPPLEMENT

for _expert in BUILTIN_ASSISTANTS:
    if _expert["id"] == "builtin-steward":
        _expert["rules"] = AGENT_SYSTEM
    else:
        _expert["rules"] = _expert["rules"].rstrip() + "\n\n" + _EXPERT_PROMPT_SUPPLEMENTS[_expert["id"]] + "\n" + EXPERT_COMMON_RULES


def _schema(name: str, desc: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required or []),
            },
        },
    }


DELEGATE_TOOL = _schema("delegate", "把最多 8 个任务交给独立上下文的子 Agent。有依赖的按阶段执行，"
            "没有依赖的并行。角色分别为 literature（文献检索）、evidence（原文取证）、analysis（数据分析）、"
            "review（独立评审）；可省略角色以兼容普通并行委派。评审任务的 prompt 只描述待核查的问题，"
            "通过 depends_on 获取前置任务的证据，勿把其他 Agent 的立场、评分或多数意见写进 prompt。",
            {"jobs": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
                "id": {"type": "string", "description": "本次委派唯一 ID，可由 depends_on 引用，使用字母、数字、连字符或下划线"},
                "title": {"type": "string", "description": "子任务短名，用于前台分行显示"},
                "prompt": {"type": "string", "description": "这一份要独立完成的任务，写清对象与要交的东西"},
                "role": {"type": "string", "enum": ["literature", "evidence", "analysis", "review"]},
                "scope_key": {"type": "string", "description": "任务对象和范围的标识，用于同一轮复用；独立复核使用不同角色或范围"},
                "depends_on": {"type": "array", "items": {"type": "string"}, "description": "前置子任务的 ID"},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 6, "description": "模型循环上限，默认 4"}},
                "required": ["prompt"]}},
                "project": {"type": "string", "description": "项目 ID 或名称；绑定项目的对话可省略"},
                "parallel": {"type": "integer", "minimum": 1, "maximum": 4, "description": "并行度，默认 3"}},
            ["jobs"])


import langgraph_workflow

READING_TOOLS = [
    _schema("paper_abstracts", "按页列出项目已入库全文的摘要与候选题录，仅用于初筛（limit 最高 20）。传 result_ids 可限定本次候选池，避免遍历无关历史；仍保留项目已有全文。missing 表示摘要待核；result_id 用于下载，paper_id 用于 paper_focus，不能混用。",
            {"offset": {"type": "integer"}, "limit": {"type": "integer"}, "project": {"type": "string"},
             "result_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100}}),
    _schema("paper_focus", "根据创新主张或章节缺口，从指定已入库全文（必须使用下载/入库返回的 paper_id，不可传检索 result_id）检索最多 6 条带页码片段。返回的 start/end 是页文本原文位置；登记证据时原样传给 paper_evidence_record，由服务端从 PDF 取回原文，不要转述 excerpt。给原文可能使用的关键词或短语，查无结果标记待核。",
            {"question": {"type": "string"}, "paper_ids": {"type": "array", "items": {"type": "string"}},
             "keywords": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer"},
             "project": {"type": "string"}}, ["question", "paper_ids", "keywords"]),
    _schema("paper_evidence_get", "按证据 ID 读取当前项目已经登记的页码、原文与核查字段，供创新审查和框架共用。",
            {"ids": {"type": "array", "items": {"type": "string"}}, "project": {"type": "string"}}, ["ids"]),
]

TOOL_SPECS = [
    *langgraph_workflow.LANGGRAPH_TOOL_SPECS,
    *READING_TOOLS,
    _schema("task_create", "在当前项目创建执行任务；可选全局已启用专家或团队。plan_id 关联待办计划，子任务自动继承父任务范围。", {
        "title": {"type": "string"}, "goal": {"type": "string"}, "executor_binding_id": {"type": "string"},
        "member_id": {"type": "string"}, "inputs": {"type": "array", "items": {"type": "object"}},
        "output_contract": {"type": "object"}, "dependencies": {"type": "array", "items": {"type": "string"}},
        "plan_id": {"type": "string"}, "request_key": {"type": "string"}}, ["goal", "executor_binding_id"]),
    _schema("task_start", "启动当前项目任务，继承用户已授权模式。返回排队并不表示完成。", {"id": {"type": "string"}, "request_key": {"type": "string"}}, ["id"]),
    _schema("task_handoff", "把当前任务的控制权交给当前团队中的另一位 Agent，并传递当前上下文、证据和转移原因。", {"target_member_id": {"type": "string"}, "reason": {"type": "string"}}, ["target_member_id", "reason"]),
    _schema("task_status", "读取任务状态、项目执行者、输入版本、产物与核查记录。省略 ID 列出当前范围任务。", {"id": {"type": "string"}}),
    _schema("task_cancel", "停止当前范围内的任务并保留已有产物。", {"id": {"type": "string"}}, ["id"]),
    _schema("workflow_templates", "列出当前项目可复用的固定任务工作流及节点配置；选择模板后调用 workflow_prepare。", {}),
    _schema("workflow_prepare", "把固定工作流解析为节点任务并保存输入版本，暂不执行。inputs 只放项目资产或相对文件；自由文本要求写入 goal。核对节点合同后再调用 workflow_start。",
            {"template_id": {"type": "string"}, "goal": {"type": "string"},
             "inputs": {"type": "array", "items": {"type": "object"}}, "request_key": {"type": "string"},
             "managed_artifacts": {"type": "boolean"},
             "review_mode": {"type": "string", "enum": ["human_review", "auto"]}}),
    _schema("workflow_start", "启动当前项目已准备的工作流；关键节点按用户模式等待人工核查。", {"id": {"type": "string"}}, ["id"]),
    _schema("workflow_status", "读取持久化工作流状态和 review_targets。awaiting_review 时请用户打开节点编辑并核查，不可代替用户批准或重复启动。用户可能在你生成回复期间通过界面完成审查；交付 source=human 表示人工已处理，不能仅因最新状态 succeeded 或 review_targets 为空就断言流程未暂停审查。", {"id": {"type": "string"}}),
    _schema("workflow_cancel", "停止当前项目工作流，保留已生成产物。", {"id": {"type": "string"}}, ["id"]),
    DELEGATE_TOOL,
    _schema("paper_discover", "检索 OpenAlex 和 Crossref，返回候选文献的作者、发表日期和摘要，不入库。",
            {"query": {"type": "string"}, "days": {"type": "integer", "description": "可选，限定最近 1–90 天内有确切发表日期的结果"}}, ["query"]),
    _schema("rss_recent", "查看情报日报已收录的近期订阅条目，返回发布日期（UTC）、来源链接和摘要。",
            {"days": {"type": "integer", "description": "最近 1–90 天，默认 7 天"}, "query": {"type": "string", "description": "可选，按标题、摘要或作者查找"}}),
    _schema("paper_excerpt", "读取当前项目已索引论文的指定页原文及版本。offset 可读取同页后续文字。",
            {"paper_id": {"type": "string"}, "page": {"type": "integer"}, "offset": {"type": "integer"}}, ["paper_id", "page"]),
    _schema("research_data_profile", "统计当前项目已索引 CSV 的行数、字段缺失和分组数量，可按原始列名等值筛选。",
            {"asset_id": {"type": "string"}, "group_by": {"type": "string"}, "where": {"type": "object"}}, ["asset_id"]),
    _schema("project_list", "列出已经添加的项目、项目 ID 和文件夹，返回当前绑定项目 ID。", {}),
    _schema("library_overview", "查看当前任务的文献/检索结果/笔记/证据数量和最近条目，先调它了解现状。", {}),
    _schema("workflow_context_read", "分页读取完整输入、前序交付、产物或执行记录。输入节选不代表来源缺失。按 accepted_upstream.outputs.read_with 用 section=artifact 与 artifact 文件名读取已交付文件原文，并按 next_offset 读完再判断缺失。section=artifacts 读取当前执行节点 required_files 工作文件内容和 content_hash；文件冲突时读取、合并，再带 expected_hash 更新。",
            {"run_id": {"type": "string"}, "node_id": {"type": "string"}, "section": {"type": "string", "enum": ["input", "history", "output", "agent", "artifacts", "artifact"]},
             "artifact": {"type": "string", "description": "section=artifact 时使用交付清单中的文件名或相对路径"},
             "offset": {"type": "integer"}, "limit": {"type": "integer"},
             "reread": {"type": "boolean", "description": "明确需要重新核查同一版本页面时设为 true；否则按返回的 next_offset 继续，已提供页面不会重复返回正文。"}}),
    _schema("idea_review", "审查一个创新点并关联已登记的原文证据。", {
        "idea": {"type": "string", "description": "待审查的创新点"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}}}, ["idea"]),
    _schema("paper_search", "在 OpenAlex/Crossref/arXiv 检索论文并入库；结果按「相关度×引用量」排序，一次最多留 100 条。", {
        "query": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string", "enum": ["openalex", "crossref", "arxiv"]}},
        "rows": {"type": "integer", "description": "每源条数(1-50)，默认 30"}}, ["query"]),
    _schema("paper_download", "把一条检索结果的开放获取(OA) PDF 下载到当前项目「文献库/」暂存目录，返回 paper.id；不会写入 Zotero。没有 OA 链接会失败。", {"result_id": {"type": "string"}}, ["result_id"]),
    _schema("paper_download_batch", "批量下载项目内多条检索结果的开放获取(OA) PDF 到「文献库/」并入库，返回每条的 paper_id 或失败原因；单篇无 OA 不中断其余条目。不传 result_ids 时自动选取当前未入库但含 OA/DOI 的候选条目（默认最多 10 篇，上限 20 篇）。",
            {"result_ids": {"type": "array", "items": {"type": "string"}},
             "limit": {"type": "integer", "description": "未传 result_ids 时最多尝试下载篇数（1–20，默认 10）"}}),
    _schema("paper_read", "将已入库 PDF 逐页精读成中文笔记。用户明确要求逐页阅读时使用；初筛和定向核查用 paper_abstracts 或 paper_focus。", {"paper_id": {"type": "string"}}, ["paper_id"]),
    _schema("paper_evidence_record", "登记当前项目 PDF 的事实与原文证据。优先传递 paper_focus 返回的 excerpt_id，以及 field/value；服务端从本次任务已保存的 paper_focus 结果还原并核验页码和原文，无需复制 start/end。也支持手动传 page_no/start/end。不要转述或改写 excerpt。每条记录逐条校验，不确定时保留缺口。", {
        "paper_id": {"type": "string"}, "items": {"type": "array", "maxItems": 30, "items": {"type": "object",
            "properties": {"excerpt_id": {"type": "string"}, "page_no": {"type": "integer"},
                           "field": {"type": "string"}, "value": {"type": "string"},
                           "excerpt": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["field", "value"]}}
    }, ["paper_id", "items"]),
    _schema("paper_translate", "翻译所需 PDF 页或查询进度。pages 留空表示全文。done 表示本次请求范围完成，full_document 才表示整篇完成。",
            {"paper_id": {"type": "string"}, "action": {"type": "string", "enum": ["start", "status"]},
             "pages": {"type": "string", "description": "物理页码，如 3 或 2-4,8"},
             "model_id": {"type": "string", "description": "用户指定且已配置的 OpenAI 兼容翻译模型"}}, ["paper_id"]),
    _schema("notes_compose", "把一条精读笔记整理成带证据表的知识库笔记，可选写入 Obsidian。", {"note_id": {"type": "string"}, "vault": {"type": "string"}}, ["note_id"]),
    _schema("draft_write", "按已保存的谱系图生成证据底稿。章节、顺序和出处由程序固定，证据缺失时报待核。", {}),
    _schema("draft_review", "以审稿人视角审查稿件，给出结论/评分/问题清单/证据缺口。", {"draft": {"type": "string"}}),
    _schema("manuscript_translate_en", "把已验收中文正文译为学术英文并保存英文稿，供后续格式转换。",
            {"style": {"type": "string", "description": "英文稿件语体，可留空"}}, []),
    _schema("format_convert", "把稿件导出为 docx / doc / md / html / pdf，或把一份现成的 .tex 编成 PDF（format=tex）。用户说「转成 PDF」用 pdf；说「Word」用 docx（老版本 .doc 用 doc）；说「这份 tex 转 PDF」用 tex。template 决定 PDF 走哪套期刊模板：plain=本机通用（默认）、elsevier、ieee、revtex（APS）。cite_style 决定参考文献样式（中文稿默认 gbt7714），pdf 与 docx/html/md 都按它走：正文里的 {{C3}} 这类标记会自动变成正式引用，台账里的文献从 Zotero 取元数据。模板出处见 TEMPLATES 里的 repo（bamos/latex-templates、jsk-report-template/elsarticle-template 等公开库）。",
            {"format": {"type": "string", "enum": ["docx", "doc", "md", "html", "pdf", "tex"]},
             "source": {"type": "string", "description": "源文件路径。tex 传 .tex；导出正文传 Markdown。留空时工作流优先用已交付上游稿。"},
             "template": {"type": "string", "enum": ["plain", "elsevier", "ieee", "revtex"],
                          "description": "PDF 排版模板：plain 本机通用 / elsevier / ieee / revtex(APS)；用户没指定就用 plain"},
             "cite_style": {"type": "string", "enum": ["gbt7714", "gbt7714-ay", "apa", "unsrt", "plain", "ieeetr", "elsarticle", "aps"],
                            "description": "参考文献样式：gbt7714=GB/T 7714 顺序编码（中文期刊/学位论文，正文 [n]）/ gbt7714-ay=GB/T 7714 著者-出版年制（正文 (著者 等, 年)，文后按著者字顺，同著者同年 a/b）/ apa=APA 风格 / unsrt / plain / ieeetr(IEEE) / elsarticle(Elsevier) / aps；用户没指定就按模板默认（中文稿=gbt7714）"},
             "name": {"type": "string"}}, ["format"]),
    _schema("calendar_add", "在日历里加一条日程并关联当前任务。用户说了钟点就把 time 填上，别说「14:00 开会」这种把时间写进标题的写法。", {"title": {"type": "string", "description": "日程标题，不要带钟点"}, "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string", "description": "24 小时制 HH:MM，例如 14:00；用户没给时间就留空"}, "kind": {"type": "string"}}, ["title", "date"]),
    _schema("project_create", "新建一个科研项目（课题），返回 project_id。用户说「新建一个项目 / 课题」时用它；"
            "建完把 project_id 记下来，后面的检索、下载、精读、加日程、派活都用这个 id。",
            {"title": {"type": "string", "description": "项目标题，用户没给就按研究方向拟一个能改的"},
             "question": {"type": "string", "description": "要回答的研究问题；不知道就留空"},
             "material_system": {"type": "string", "description": "材料体系 / 关键词，逗号分隔；不知道就留空"},
             "base_dir": {"type": "string", "description": "项目文件夹位置（绝对路径）；用户没指定就留空，文件落到工作区默认目录"}},
            ["title"]),
    _schema("project_note_list", "列出项目笔记 dashboard 的分类和已有 Markdown 文件。", {"project": {"type": "string", "description": "项目名或 id；绑定项目的对话可留空"}}),
    _schema("project_note_read", "读取项目笔记目录内指定 Markdown 文件。", {"project": {"type": "string"}, "path": {"type": "string", "description": "project_note_list 返回的相对路径"}}, ["path"]),
    _schema("project_note_write", "创建或更新项目笔记。可传分类ID、标签或模板元数据；新建时path可空，由服务选定目录。", {"project": {"type": "string"}, "path": {"type": "string"}, "content": {"type": "string"}, "title": {"type": "string"}, "metadata": {"type": "object", "description": "category_id、tags；模板另含note_type=prompt_template、variables"}, "expected_hash": {"type": "string"}}, ["content"]),
    _schema("project_artifact_write", "仅供正在执行的项目任务，把 required_files 约定的 Markdown、CSV、JSON 或 TeX 产物写入项目标准目录（如 文献库/、笔记/、论文正文/、最终产物/、其余/），并登记文件版本。严禁先写入“校验中/占位”过渡内容；应在取证完成后一次性写入完整终稿。CSV/JSON 保存前校验，格式失败时按返回的行号、列数修正后重试。新建时省略 expected_hash；仅在需要更新已写文件时用 workflow_context_read(section=artifacts) 取最新 content_hash 并传 expected_hash。", {"project": {"type": "string"}, "path": {"type": "string", "description": "项目内相对路径，优先落在标准分类目录，例如 文献库/candidate_papers.csv、笔记/evidence_analysis.md、最终产物/literature_review.md"}, "content": {"type": "string"}, "title": {"type": "string"}, "expected_hash": {"type": "string"}}, ["path", "content"]),
    _schema("project_resources", "统一查询项目文献、笔记、论文稿件、实验记录和交付产物；传 asset_id 读取当前文本与 content_hash。action=index 显式整理已有文件索引，需要项目写权限，不搬移原件。", {"action": {"type": "string", "enum": ["list", "index"]}, "project": {"type": "string"}, "kind": {"type": "string", "enum": ["literature", "note", "manuscript", "experiment", "output", "file"]}, "query": {"type": "string"}, "asset_id": {"type": "string"}}),
    _schema("paper_tool", "论文工具箱：中英互译、逐段润色、引文整理（可排 GB/T 7714/Nature/IEEE）、合规预检、文稿转换、图注及图片格式转换。图片使用当前项目已上传的 asset_id；caption 需视觉模型，不编造图中缺失的信息。", {"tool": {"type": "string", "enum": ["translate", "polish", "references", "compliance", "convert", "caption", "image_convert"]}, "text": {"type": "string", "description":"原文；polish 传一个段落，compliance 传正文加文末参考文献"}, "image_ids":{"type":"array","items":{"type":"string"}}, "direction": {"type": "string", "enum": ["zh_en", "en_zh"]}, "terminology": {"type": "string"}, "prompt": {"type": "string", "description": "翻译、润色或图注风格偏好"}, "cite_style": {"type": "string", "enum": ["gb7714", "nature", "ieee"], "description": "仅为 references 选择参考文献格式"}, "format": {"type": "string", "enum": ["md", "html", "docx", "pdf", "png", "jpg", "jpeg", "webp", "tiff"]}, "project": {"type": "string"}}, ["tool"]),
    _schema("project_resource_write", "在项目统一资料库写笔记、实验记录或 Markdown 稿件；返回真实文件与版本。更新需 asset_id 和从读取结果取得的 expected_hash。", {"project": {"type": "string"}, "kind": {"type": "string", "enum": ["note", "experiment", "manuscript"]}, "title": {"type": "string"}, "content": {"type": "string"}, "asset_id": {"type": "string"}, "expected_hash": {"type": "string"}}, ["kind", "content"]),
    _schema("prompt_template_search", "按用户任务查找项目内可用提示词模板；搜索结果仅供选择，不自动应用。", {"project": {"type": "string"}, "query": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}}),
    _schema("prompt_template_read", "读取指定提示词模板及变量定义。", {"project": {"type": "string"}, "template_id": {"type": "string"}, "revision_id": {"type": "string"}}, ["template_id"]),
    _schema("prompt_template_render", "用户要求使用模板时，填写变量生成任务要求；缺信息先补齐，遵循原有工具和权限。", {"project": {"type": "string"}, "template_id": {"type": "string"}, "revision_id": {"type": "string"}, "variables": {"type": "object"}}, ["template_id"]),
    _schema("project_task_manage", "列出、创建、更新待办计划，包含验收要求和前置计划；dispatch 派给专家执行。执行状态由实际交付更新。", {"action": {"type": "string", "enum": ["list", "create", "update", "dispatch"]}, "project": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "acceptance": {"type": "string"}, "dependencies": {"type": "array", "items": {"type": "string"}}, "status": {"type": "string"}, "priority": {"type": "string"}, "assistant_id": {"type": "string"}, "due_date": {"type": "string"}, "stage": {"type": "string"}, "reviewer_id": {"type": "string"}}, ["action"]),
    _schema("assistant_manage", "列出专家或创建、更新专家档案。", {"action": {"type": "string", "enum": ["list", "create", "update"]}, "id": {"type": "string"}, "name": {"type": "string"}, "rules": {"type": "string"}, "description": {"type": "string"}, "model_id": {"type": "string"}}, ["action"]),
    _schema("team_create", "为项目创建真实团队，首位专家担任负责人。管家对话中默认临时；仅用户明确要求长期保留时传 persistent=true。", {"project": {"type": "string"}, "name": {"type": "string"}, "purpose": {"type": "string"}, "persistent": {"type": "boolean"}, "assistant_ids": {"type": "array", "items": {"type": "string"}}}, ["name"]),
    _schema("team_manage", "读取或配置项目已有团队，复用 team_id 和 member_id。支持改名与职责、添加或修改成员、指定专业负责人。", {"project": {"type": "string"}, "action": {"type": "string", "enum": ["list", "update", "add_member", "update_member", "set_leader"]}, "team_id": {"type": "string"}, "member_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "prompt": {"type": "string"}, "assistant_id": {"type": "string"}, "model_id": {"type": "string"}, "reasoning": {"type": "string"}}, ["action"]),
    _schema("workflow_manage", "列出真实工作流模板与步骤，或把已有模板选择为项目工作流。", {"project": {"type": "string"}, "action": {"type": "string", "enum": ["list", "select"]}, "layout_id": {"type": "string"}}, ["action"]),
    _schema("workflow_create", "创建并绑定项目 DAG 工作流；可提供 nodes/edges 自定义任务与依赖，或 steps 复用科研步骤。自编排节点的 output_contract.required_files 与 edges[].artifact 应按项目标准目录协同组织：文献检索清单放 文献库/（如 文献库/candidate_papers.csv），筛选与证据分析放 笔记/，正文草稿放 论文正文/，最终汇总/报告放 最终产物/（如 最终产物/literature_review.md），避免散落在根目录。传 auto_start=true 可启动，项目已有未完成运行时不会替换。", {"project": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "goal": {"type": "string"}, "auto_start": {"type": "boolean"}, "inputs": {"type": "array", "items": {"type": "object"}}, "nodes": {"type": "array", "items": {"type": "object"}}, "edges": {"type": "array", "items": {"type": "object"}}, "steps": {"type": "array", "items": {"type": "string", "enum": ["idea", "search", "download", "notes", "outline", "map", "write", "review", "entrans", "convert"]}}}, ["name"]),
    _schema("plan", "预览项目工作流。请按目标提供 steps 和 depends_on；只有用户明确要求立即启动时才传 auto_start=true。", {
        "project": {"type": "string"},
        "name": {"type": "string", "description": "工作流/计划名称，如“验证W5·串并混合六节点文档流”"},
        "goal": {"type": "string", "description": "本次计划的总体目标"},
        "auto_start": {"type": "boolean", "description": "是否保存并立即启动，默认只预览"},
        "steps": {
            "type": "array",
            "description": "步骤列表；每个步骤指定 id, name, goal, assigned_agent, depends_on, inputs, outputs, acceptance；省略时返回固定六节点示例模板",
            "items": {"type": "object"},
        },
    }, ["goal"]),
    _schema("supervisor", "启动流程或查询各节点进度和待核查状态。人工核查由用户在节点产物页完成。", {
        "project": {"type": "string"},
        "action": {"type": "string", "enum": ["start_execution", "get_status", "stop", "resume"]},
        "run_id": {"type": "string"},
        "layout_id": {"type": "string"},
        "node_id": {"type": "string", "description": "恢复指定失败节点；保留其他已交付和待核查节点"},
        "request_key": {"type": "string", "description": "本次恢复的稳定去重标识，重复提交须使用同一值"},
        "expected_task_run_id": {"type": "string", "description": "状态查询返回的当前节点尝试 ID，避免恢复过期尝试"},
    }, ["action"]),
    _schema("platform", "按项目预览、保存、运行或查询工作流，查询任务和执行者，或打开右侧面板。人工核查由用户完成。", {
        "project": {"type": "string"},
        "action": {"type": "string", "description": "平台动作名，如 workflow.generate, workflow.create, workflow.run, workflow.status, ui.panel"},
        "args": {"type": "object", "description": "动作参数对象"},
        "confirm": {"type": "boolean", "description": "写操作确认标志，默认 true"},
    }, ["action"]),
    _schema("skill_create", "在项目内创建可编辑的 SKILL.md，供该项目专家读取。", {"project": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "instructions": {"type": "string"}}, ["name", "instructions"]),
    _schema("mcp_register", "登记一个停用状态的 MCP 服务器配置；用户之后在设置中启用并发现工具。", {"name": {"type": "string"}, "url": {"type": "string"}, "command": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}}, ["name"]),
    _schema("knowledge_query", "在**论文全文 + 精读笔记 + 证据原文块**里做语义检索（词法+向量双路召回，融合排序）。不填 project 就查当前项目；跨项目问「某个项目里的某篇笔记」时把 project 填上（项目名或 id）。论文命中自带「文件名 p.页码」，可直接引用。",
            {"query": {"type": "string"},
             "project": {"type": "string", "description": "项目名或 project_id；不填=当前项目，首页 Agent 不填=全部项目一起搜"}},
            ["query"]),
    _schema("semantic_index", "语义索引体检与补建：status 看已索引多少块、还差几个源；sync 立刻增量补建（内容哈希没变就跳过，幂等可反复跑）。新导入 PDF 后想立刻可检索就调 sync。",
            {"action": {"type": "string", "enum": ["status", "sync"]},
             "limit": {"type": "integer", "description": "sync 用：本次最多处理多少个源，默认全部"}},
            ["action"]),
    _schema("calendar_list", "看日程：默认返回今天；也可以给日期区间或月份。用户问「今天/这周/这个月有什么安排」先调它，别只看系统提示里那几条。",
            {"date_from": {"type": "string", "description": "YYYY-MM-DD，默认今天"},
             "date_to": {"type": "string", "description": "YYYY-MM-DD，默认与 date_from 相同"},
             "month": {"type": "string", "description": "YYYY-MM，给月份就忽略上面两个"}}),
    _schema("schedule_manage", "管理定时任务：列出、新建、暂停/启用、删除。用户说「每天早上帮我盯一下某个项目」「把那个定时任务暂停」时用它。",
            {"action": {"type": "string", "enum": ["list", "create", "toggle", "delete"]},
             "id": {"type": "string", "description": "toggle/delete 用：定时任务 id（先 list 拿）"},
             "name": {"type": "string", "description": "create 用：任务名"},
             "todo_id": {"type": "string", "description": "可选：关联当前项目待办 id，执行结果回写待办"},
             "prompt": {"type": "string", "description": "create 用：到点了让 Agent 做什么"},
             "cron": {"type": "string", "description": "create 用：cron 五段式，例如每天 9 点 = \"0 9 * * *\"；工作日 8:30 = \"30 8 * * 1-5\""},
              "project": {"type": "string", "description": "create 用：必填，项目名或 id"},
              "assistant_id": {"type": "string", "description": "create 用：必填，已启用专家 id"},
             "workflow_id": {"type": "string", "description": "可选：定时启动当前项目的 LangGraph 模板 ID，不填则派给专家"},
             "output_dir": {"type": "string", "description": "create 用：产物子目录，默认 定时任务"},
             "enabled": {"type": "boolean", "description": "toggle 用：true=启用，false=暂停"}},
            ["action"]),
    _schema("team_board", "看团队看板：成员各自状态 + 最近派活与执行情况（谁在跑、谁完成了、失败原因）。",
            {"project": {"type": "string", "description": "项目名或 id；不填=当前项目"},
             "team_id": {"type": "string", "description": "team_create 或看板返回的团队 ID；省略时列出项目团队"}}),
    _schema("team_form", "为一个项目临时组队：说清为什么组、要交什么、拉哪几个专家（写名字），开一间加急办公室。"
            "加急位最多 3 间，用完自动归还；先 team_board 看有哪些人可用。",
            {"purpose": {"type": "string", "description": "这支队为什么组、要交什么"},
             "members": {"type": "array", "items": {"type": "string"}, "description": "专家名字，2-4 个"},
             "project": {"type": "string", "description": "项目名或 id；不填=当前项目"}}, ["purpose", "members"]),
    _schema("team_assign", "把一件事派给某个团队成员（Partner Agent）执行；先 team_board 看有哪些成员。",
            {"member": {"type": "string", "description": "成员名字（或 id）"},
             "team_id": {"type": "string", "description": "成员所属的团队 ID，与 team_board 相同"},
             "prompt": {"type": "string", "description": "要它干什么，写清楚要什么产出"},
             "project": {"type": "string", "description": "项目名或 id；不填=当前项目"},
             "depends_on": {"type": "array", "items": {"type": "string"}, "description": "IDs of prerequisite team tasks"},
             "output_contract": {"type": "object", "description": "Expected output shape"},
             "reviewer_id": {"type": "string", "description": "Reviewer member ID"}}, ["member", "prompt"]),
    _schema("memory", "记忆：跨会话留存的事实/偏好/约束/决定。ops 一次可给多条；给 query 就是检索。"
            "「做法/流程/踩坑」不属于这里 —— 那是 skill_write。",
            {"ops": {"type": "array", "description": "批量操作", "items": {
                "type": "object", "properties": {
                    "action": {"type": "string", "enum": ["add", "patch", "remove", "archive", "restore"]},
                    "id": {"type": "string", "description": "patch/remove/archive 用：先用 query 拿 id"},
                    "old_text": {"type": "string", "description": "没记 id 就用原文片段定位"},
                    "expected_text": {"type": "string", "description": "patch 前 recall 得到的当前完整正文"},
                    "reason": {"type": "string", "description": "修改正文的依据或用户确认的原因"},
                    "source_ref": {"type": "string", "description": "可核对的依据，如 DOI 和 PDF 页码。用户对话来源由系统补入"},
                    "text": {"type": "string", "description": "add/patch 用：一句话，陈述式，别写过程"},
                    "kind": {"type": "string", "enum": ["fact", "preference", "constraint", "decision", "episode"]},
                    "scope": {"type": "string", "enum": ["project", "global"],
                              "description": "默认 project；只有跨项目都成立的口径/偏好才用 global"},
                    "tags": {"type": "string", "description": "空格分隔的两个词以内"},
                    "importance": {"type": "integer", "description": "0-3，默认 1；硬约束/导师口径给 2-3"},
                    "archived": {"type": "boolean"}},
                "required": ["action"]}},
             "query": {"type": "string", "description": "给了就检索（忽略 ops）"},
             "limit": {"type": "integer"}}),
    _schema("recall", "回想：按关键词检索 记忆 + 过往对话 + 笔记/证据原文块。引用旧结论之前先查，别凭印象。",
            {"query": {"type": "string", "description": "关键词，两三个词最好；两字也能搜"},
             "include_history": {"type": "boolean", "description": "追溯旧口径、变更原因与来源时开启。也可用记忆 ID 查询"},
             "limit": {"type": "integer", "description": "每类最多几条，默认 8"}}, ["query"]),
    _schema("translate_doc", "把「论文撰写」那份正文.md 整篇译成英文、落成同目录的 正文_en.md："
                             "服务端按节分片、4 路并行翻，几十秒到两分钟，不往对话里灌全文。"
                             "用户说「翻正文 / 整篇翻 / 出英文版」就用它 —— 不要自己一段一段手译（那样又慢又贵）。",
            {"style": {"type": "string", "enum": ["formal", "concise", "impactful"],
                       "description": "formal=标准学术（默认）；concise=极简凝练；impactful=强调突破"}}),
    _schema("read_attachment", "读项目里（或用户上传的）一个文件的正文：PDF 自动抓文本，其它按文本读。"
                              "读正文/大纲/笔记/精读稿之前先调它。支持连续分页，按 next_offset 继续读取。",
            {"path": {"type": "string", "description": "文件路径。中文正文的默认位置就是 论文正文/正文.md（相对项目根）；"
                                                      "给绝对路径也行，也要能读 笔记/、文献库/ 里的文件"},
             "chars": {"type": "integer", "minimum": 1, "description": "兼容的页长度，默认 20000，可增大"},
             "offset": {"type": "integer", "minimum": 0, "description": "起始字符位置，默认 0"},
             "limit": {"type": "integer", "minimum": 1, "description": "本页字符数，优先于 chars，没有固定最大页长"}}, ["path"]),
    _schema("file_list", "列项目文件夹里某一层的内容（目录 + 文件，带大小与修改时间）。rel 相对项目根，空 = 根。"
                                "正文在大纲/撰写/审查产出里叫 论文正文/正文.md，框架是 论文正文/框架.md。",
            {"rel": {"type": "string", "description": "相对路径，如 论文正文 或 最终产物/_tex；空 = 项目根"}}),
    _schema("file_hand", "把项目里的一个文件转到某一步的对话里 —— 那一步的专家下次开口就带着它（用户说的「交给写作专家」）。",
            {"rel": {"type": "string", "description": "相对项目根的路径（文件或文件夹）"},
             "step": {"type": "string", "enum": ["idea", "search", "download", "notes", "outline", "map",
                                                 "write", "review", "entrans", "convert"],
                      "description": "交给哪一步"},
             "note": {"type": "string", "description": "附一句要那一步做什么（可空）"}}, ["rel", "step"]),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]

IDEA_TOOLS = [
    *READING_TOOLS,
    next(tool for tool in TOOL_SPECS if tool["function"]["name"] == "paper_evidence_record"),
    _schema("idea_review", "对当前创新点出正式判定（五维评分 1-10 + 致命缺陷 + 三档结论），会落成项目笔记并写入长期记忆。",
            {"idea": {"type": "string", "description": "用户要审查的创新点原文（可带上刚补充的约束）"},
             "evidence_ids": {"type": "array", "items": {"type": "string"}, "description": "支持或反驳该主张的已登记证据"}}, ["idea"]),
    _schema("memory", "把值得长期记住的结论/约束/用户偏好记一条（add）。",
            {"ops": {"type": "array", "description": "批量，一条也行", "items": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["add"]},
                               "text": {"type": "string", "description": "一句话，例如「用户只有 8 周 + 单卡 8G，算力受限」"},
                               "kind": {"type": "string", "enum": ["fact", "constraint", "preference", "decision"]},
                               "importance": {"type": "integer"}},
                "required": ["action", "text"]}}}, ["ops"]),
]


# 博导 skill：直接读本机技能目录里的 skill 正文（找不到就用内置精简版）
SKILL_ROOTS = [Path(os.environ["RW_SKILL_DIR"])] if os.environ.get("RW_SKILL_DIR") else []
SKILL_ROOTS.append(Path.home() / ".agents" / "skills")


def skill_text(*rel: str, cap: int = 7000, db=None, task_id: str = "") -> str:
    if db is not None:
        import capabilities
        project_dir = (db.summary(task_id).get("task") or {}).get("base_dir", "")
        name = rel[-2] if len(rel) > 1 else ""
        item = next((x for x in capabilities.list_skills(project_dir) if x["id"] == name), None)
        if item:
            if not capabilities.enabled(db, db.assistant_of_chat(task_id), "skill", item["id"], item_scope=item.get("scope", "")):
                return ""
            return Path(item["path"]).read_text(encoding="utf-8-sig")[:cap]
        return ""
    for root in SKILL_ROOTS:
        p = root.joinpath(*rel)
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.S)      # 去 frontmatter
        text = text.split("\n## Pitfalls")[0].split("\n## References")[0]
        return text.strip()[:cap]
    return ""



