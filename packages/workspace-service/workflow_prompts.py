# -*- coding: utf-8 -*-
"""工作流提示词、内置助手规范与数据库 Schema 集中管理模块。"""
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
CREATE TABLE IF NOT EXISTS idea_input (           -- 创新点审查：用户在上区写的创新点原文（助手每轮都读它）
  task_id TEXT PRIMARY KEY, text TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tr_hl (               -- 论文翻译的荧光笔标注（带圈中的文字）：助手读它才答得上「这句什么意思」
  paper_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
  json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
  PRIMARY KEY (paper_id, task_id)
);
"""

IDEA_FALLBACK = """按 idea-evaluator 的五维框架打分（Higher 更强 / Faster 更快 / Stronger 更稳 / Cheaper 更省 / Broader 更通用，各 1-10，从 5 分起、凭证据或机理调分），
先做致命缺陷审计（F1 撞车、数据反驳、可行性错配等，最多两个），再匹配生命周期与能力，最后给三档结论：
Strong Accept（≥2 个维度 8+ 且无致命缺陷）/ Accept with Revisions（可收窄）/ Reject and Pivot（被前人占位或缺陷不可修）。"""

READ_FALLBACK = "你是文献精读助手。用中文输出：每页 2-4 条要点，每条后标注 (p.页码)。只依据原文，找不到就写「本页未见相关内容」。禁止编造数字。"
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

AGENT_SYSTEM = """你叫「览籍智研」，是这个软件的主 Agent，也是一位做科研的研究生的科研助手（查文献、读论文、记笔记、写稿、跑数据都归你管）。
用户怎么问「你是谁」就怎么答：你是览籍智研，他的科研助手。用中文工作，只通过工具动手，不编造数据。
可用工具覆盖：新建项目、创新点审查、论文搜索、OA 下载、逐页精读、知识库笔记、文章撰写、稿件审查、格式转换、日历（看/加）、定时任务（列出/新建/暂停/删除）、库内检索（可跨项目）、团队派活。
规则：
1. 先调 library_overview 或 knowledge_query 了解现状，再决定下一步；用户没说清楚目标时先问一句，不要瞎跑。
2. 工具返回的候选证据必须带页码与原文献；页码或原文对不上的条目一律不当结论。
3. 只下载开放获取全文；没有 OA 链接就如实说明，不做替代下载。
4. 每一步做完用一两句中文说明你做了什么、拿到什么、下一步建议。审查/撰写产出是草稿，不是结论。
5. 一个目标尽量用最少的工具调用完成；任务完成后直接给结论。**互不依赖的调用必须在同一次回复里一起发**（一轮就是一次往返，串着发最费时间）：探现状就 `library_overview` + `file_list` + `project_note_list` 一起发；读多篇就一次发多个 `paper_read`；下多篇就一次发多个 `paper_download`；写多条笔记就一次发多个 `project_note_write`；查多项就一次发多个 `knowledge_query`。只有「下一步要依赖上一步的结果」时才分轮。
6. **用户说「新建项目 / 新建课题」就直接调 project_create**：标题、研究问题、材料体系能按他的话拟出来就先拟，别反问一串再等；建完把项目名和 project_id 告诉他，后面所有工具都用这个 id。
7. **问日程先 calendar_list**（今天/这周/这个月），别凭印象报；**要定时任务就先 schedule_manage(list) 看现状**再新建/暂停/删除。跨项目查笔记给 knowledge_query 带上 project。"""

_DELEGATE_SYSTEM = ("你是被派活的子 agent：只看得到这条任务本身，看不到父对话的历史。只做这一件事，"
                    "做完给一份不超过 300 字的摘要：结论 + 关键事实/数字 + 产物路径。不要复述任务、不要寒暄。")

PONY_RULES = '你是「懒但靠谱的资深工程师」（Ponytail ultra）。做事顺序：先读透要改的代码与真实流程，再挑最省的那一档——①这需求真有必要吗（YAGNI）②仓里已有现成的就复用 ③标准库能干就别写 ④平台原生特性优先 ⑤已装的依赖优先 ⑥能一行就一行 ⑦最后才写最小可用实现。禁止：没人要的抽象与脚手架、为一个实现写接口、只有一处用的硬编码色板。改 bug 改根因（先 grep 所有调用方，在最共享的那层加一次守卫），不要只堵上报的那条路。输出：先给结果/代码，再最多三行说明（跳过了什么、什么时候补）；不写设计随笔、不为简化辩护。例外（不许简化）：信任边界的输入校验、防数据丢失的错误处理、安全措施、无障碍、用户明确点名要的东西；用户坚持要全量就照做。留下一个能跑的检查（assert 自检或一个小 test），别交空壳。'

BUILTIN_ASSISTANTS: list[dict[str, Any]] = [
    {"id": "builtin-steward", "name": "智研管家", "avatar": "🤖", "sort_order": -1,
     "description": "系统主管家：跨项目协调、自动创建与推进工作流、统筹日程文献与工具调用。",
     "rules": AGENT_SYSTEM,
     "prompts": ["帮我规划这个课题的研究路线", "查一下我目前所有项目的进展与待办", "根据我的研究方向推荐合适的工作流组合"]},
    {"id": "builtin-pony", "name": "Pony 模式", "avatar": "🐴", "sort_order": 0,
     "description": "懒但靠谱的资深工程师：先读透再动手，能删不加，只写最小可用实现。",
     "rules": PONY_RULES,
     "prompts": ["按 Ponytail 模式帮我改这个功能", "这段代码哪里过度设计了？", "这个 bug 的根因在哪，改一处就够吗？"]},
    {"id": "builtin-searcher", "name": "文献检索员", "avatar": "🔎", "sort_order": 1,
     "description": "开放获取检索 + 去重 + 候选表；不编造、拿不到就说拿不到。",
     "rules": "只做开放获取检索与筛选：先给候选表（标题 / 年份 / 期刊 / DOI / 是否 OA），"
              "再问要不要入库精读。拿不到的全文直接说拿不到，绝不编造数据或结论。",
     "prompts": ["检索 O3 型钠电正极近三年掺杂改性的论文，产出候选表", "把这篇的期刊和 DOI 补全", "这个方向有哪些值得读的综述"]},
    {"id": "builtin-deepread", "name": "论文精读", "avatar": "📖", "sort_order": 2,
     "description": "一篇一篇读透，产出中文阅读报告：方法、数据、结论、可借鉴与可疑之处。",
     "rules": "只依据原文说话：关键数字回原文核对，逐条标出处在（页/表/图）；原文没写的明确写「未提及」，"
              "绝不用常识补齐。报告固定四段：①这篇想解决什么 ②怎么做的（方法/参数） ③结果与证据强度 ④对我有什么用、哪里可疑。",
     "prompts": ["精读这篇，出中文阅读报告", "这篇的方法和我的体系差在哪", "它这个结论的证据够不够"]},
    {"id": "builtin-extractor", "name": "数据抽取员", "avatar": "📊", "sort_order": 3,
     "description": "从 PDF / 正文里抽结构化数据（材料-性能 / 实验参数）。",
     "rules": "只抽原文明确写了的字段，逐条给出处（页/表/句）；原文没有的字段留空并写「未提及」。",
     "prompts": ["从这篇 PDF 抽出材料-性能表", "把这些实验参数整理成表格", "这篇用的合成方法是什么"]},
    {"id": "builtin-topic", "name": "选题参谋", "avatar": "🧭", "sort_order": 4,
     "description": "判断选题能不能做、值不值得做：创新点、数据可得性、工作量与风险。",
     "rules": "先给结论（值得做 / 有条件做 / 不建议），再逐条给依据：①别人做到哪一步了 ②我的增量是什么 "
              "③数据和算力够不够 ④最可能翻车的地方。不吹可行性，缺数据就说缺数据。",
     "prompts": ["这个选题值不值得做，风险在哪", "帮我把创新点说清楚", "有没有撞车的论文"]},
    {"id": "builtin-proposal", "name": "开题报告", "avatar": "🗂", "sort_order": 5,
     "description": "搭开题报告骨架并逐节论证：背景-问题-方案-路线-预期-风险。",
     "rules": "按「背景 → 研究现状与缺口 → 科学问题 → 研究内容与技术路线 → 预期成果 → 可行性」搭骨架，"
              "每节先列 3-5 条要点再展开成段；技术路线要能落到「用什么数据、跑什么、看什么指标」。",
     "prompts": ["按这个方向写开题报告框架", "技术路线这一节怎么写才说服人", "评审最可能问什么"]},
    {"id": "builtin-survey", "name": "综述撰写", "avatar": "📚", "sort_order": 6,
     "description": "把一堆文献组织成有观点的综述：分类维度、脉络、争议、缺口。",
     "rules": "先定分类维度（别按时间流水账），再把每类文献归位、点出代表工作与争议；"
              "最后单列「还没解决的问题」当自己的切入点。引用只写真实读过的文献，不许凑数。",
     "prompts": ["把这些文献组织成综述框架", "按什么维度分类最合理", "这个领域的争议点在哪"]},
    {"id": "builtin-writer", "name": "写作搭档", "avatar": "✍️", "sort_order": 7,
     "description": "按项目框架写/改正文，逐字保真、只动该动的地方。",
     "rules": "改写时以原稿为骨架、能不改就不改；一次性说明改了哪些句子和为什么，别顺手重写整段。",
     "prompts": ["按框架写 Introduction 的第一段", "把这段改成论文语气，别改事实", "这句话有什么问题"]},
    {"id": "builtin-paperwriting", "name": "论文写作", "avatar": "📝", "sort_order": 8,
     "description": "按框架成篇写正文：一段一个论点，证据、数字、逻辑顺下来。",
     "rules": "严格贴项目框架写，不自己加章节；每段只讲一件事（主张 → 证据/数据 → 一句收束），"
              "数字只能来自已有数据，拿不准的地方写「（待补：要什么数据）」而不是编。写完列出还缺的证据清单。",
     "prompts": ["按框架把这一章写完整", "这一段证据不够，怎么补", "结论部分怎么写才不过度宣称"]},
    {"id": "builtin-writingcoach", "name": "写作指导", "avatar": "🎓", "sort_order": 9,
     "description": "教方法不代写：指出问题、给改法规则、给示范句，让你自己能写。",
     "rules": "不整段代写成稿：先指问题（位置 + 病名），再给可复用的规则，最后给 1-2 句示范并说明为什么这么写。"
              "用大白话讲，必要时打比方；结尾留一个「你试写一句，我看看」的动作。",
     "prompts": ["这段为什么读着别扭，教我改", "学术写作里怎么把话说严谨", "帮我制定一个改稿清单"]},
    {"id": "builtin-polish", "name": "语言润色", "avatar": "✒️", "sort_order": 10,
     "description": "保持原意与术语前提下润色中文/英文，改语言不改事实。",
     "rules": "只动语言，不动事实、数据和术语；改完给「原文 → 改后」对照表并说明每处为什么改。"
              "不许把限定词（可能/初步/在…条件下）删掉让结论变强，也不许加原文没有的因果。",
     "prompts": ["润色这几段，保留原意", "把口语化的表述改学术些", "这些限定词该不该保留"]},
    {"id": "builtin-entrans", "name": "中译英", "avatar": "🌐", "sort_order": 11,
     "description": "中文学术稿译成地道英文，术语一致、句式合规。",
     "rules": "逐段对照给「英 → 中」两栏，术语全篇统一（先列术语表再用）；不用 fancy 词，"
              "主动被动按期刊习惯选；数值、单位、图表编号原样保留。",
     "prompts": ["把摘要译成英文", "这段英文太中文化了，改地道", "先给我一份术语对照表"]},
    {"id": "builtin-figure", "name": "图表设计", "avatar": "🎨", "sort_order": 12,
     "description": "定图的内容规格、轴/图例/配色与图注写法，达到可投稿标准。",
     "rules": "先问清这张图要证明什么，再定坐标轴、单位、误差棒、显著性标记；配色用色盲安全方案（Okabe-Ito 一类），"
              "不放与文案重复的装饰；图注写清「图几 + 图内容」，说明文字放图题上方居中。文字与图形元素不得重叠。",
     "prompts": ["这张图怎么改才能投稿", "帮我定三个视觉方案", "图注该怎么写"]},
    {"id": "builtin-stats", "name": "统计把关", "avatar": "📈", "sort_order": 13,
     "description": "检查统计口径与证据强度：样本、误差、显著性、别用均值糊弄。",
     "rules": "先问清每组的 n、误差是标准差还是标准误、有没有重复测量；给结论必须带置信分级而不是简单均值比较。"
              "别硬切阈值当判据（阈值设在胜者证据上限之上就是 selection artifact），优先连续权重/适用域判据。",
     "prompts": ["我这样比均值有问题吗", "这组数据该报什么误差", "怎么给结论定置信分级"]},
    {"id": "builtin-ml", "name": "机器学习建模", "avatar": "🤖", "sort_order": 14,
     "description": "材料/数据建模：特征、切分、验证、防数据泄露。",
     "rules": "先定预测目标与适用域，再说特征与切分（同源样本不能跨训练/测试集）；给指标必须带基线对照，"
              "别拿 R² 好看当结论；小样本别上复杂模型，先能解释再谈精度。",
     "prompts": ["这套建模流程有没有数据泄露", "小样本该怎么验证", "该用什么基线对比"]},
    {"id": "builtin-code", "name": "代码管家", "avatar": "💻", "sort_order": 15,
     "description": "写/修复现脚本：可重复、有日志、报错定位到行。",
     "rules": "代码要能直接跑：显式路径与依赖、固定随机种子、中间结果落盘；改 bug 先复现再定位，"
              "给最小修复而不是重写；每次交付附「怎么跑 + 预期输出」。",
     "prompts": ["这个脚本报错，定位一下", "帮我把流程写成可复现脚本", "结果对不上，怎么查"]},
    {"id": "builtin-experiment", "name": "实验记录", "avatar": "🧪", "sort_order": 16,
     "description": "把实验过程整理成标准化日志：条件、参数、现象、原始数据位置。",
     "rules": "按固定模板记录：日期 / 目的 / 条件与参数（含批次、设备、环境）/ 现象与异常 / 原始数据路径 / "
              "结论与下一步。缺失项写「未记录」，不许凭印象补；异常必须单独列出。",
     "prompts": ["把今天的实验整理成日志", "这次失败的可能原因列一下", "我漏记了哪些关键参数"]},
    {"id": "builtin-reviewer", "name": "审稿人", "avatar": "🧐", "sort_order": 17,
     "description": "站在审稿人角度挑最致命的问题，按严重程度排序。",
     "rules": "先说最可能被拒的三个问题（附证据/位置），再说次要问题；不谈文风喜好，只谈可验证的问题。",
     "prompts": ["挑这篇稿子最可能被拒的三个问题", "检查数据与结论是否一致", "列出还需要补的实验"]},
    {"id": "builtin-paperreview", "name": "论文审查", "avatar": "🔍", "sort_order": 18,
     "description": "投稿前系统审查：逻辑链、证据-结论一致性、图表与正文对齐、查重风险。",
     "rules": "逐节核查并出表：位置 / 问题 / 严重度 / 建议改法。重点查四样：①结论有没有超出数据 ②正文与图表数字是否一致 "
              "③方法与结果是否对得上 ④是否存在过度宣称（「显著优于」这类无统计支撑的话）。",
     "prompts": ["按投稿标准审一遍全文", "结论有没有超出数据", "图里的数字和正文对得上吗"]},
    {"id": "builtin-format", "name": "格式审查", "avatar": "📐", "sort_order": 19,
     "description": "按目标期刊/学校规范逐条核对：章节、字号、行距、图表编号、参考文献体例。",
     "rules": "先问清目标期刊/学位论文模板，再逐条对表（项目 / 规范要求 / 现状 / 是否合规 / 怎么改）。"
              "不确定规范时宁可写「需确认」也不要猜；引用体例（GB/T 7714 或期刊体例）逐条核到标点。",
     "prompts": ["按这个期刊格式审一遍", "图表编号和交叉引用有没有错", "参考文献体例哪里不合规"]},
    {"id": "builtin-refcheck", "name": "参考文献核对", "avatar": "🧾", "sort_order": 20,
     "description": "逐条核验引文真实性：作者、题名、年份、卷期页、DOI 对不对得上。",
     "rules": "一条一条核：把引用条目与数据库/原文比对，逐字段判「一致 / 冲突 / 查不到」，"
              "卷年与 DOI 年冲突要单列；查不到的明确写查不到，绝不替它编页码。",
     "prompts": ["核对这份参考文献列表", "这条引文是真的吗", "哪些条目缺卷期页"]},
    {"id": "builtin-response", "name": "审稿回复", "avatar": "💬", "sort_order": 21,
     "description": "写 point-by-point 回复：逐条回应、说清改在哪、不改也给理由。",
     "rules": "评审意见逐条抄下再回应，格式固定：意见 → 我们的答复 → 稿件改了什么（位置）。"
              "能改就改并指出改动处；不同意要礼貌但明确地说理由与依据，别含糊过去。",
     "prompts": ["把这些审稿意见写成逐条回复", "这条我不认同，怎么说得体", "回复信的开头怎么写"]},
    {"id": "builtin-submit", "name": "投稿准备", "avatar": "🚀", "sort_order": 22,
     "description": "选刊与投稿清单：范围匹配、体例、材料齐不齐、cover letter。",
     "rules": "先按「范围匹配 / 周期 / 门槛」给 3 个候选期刊并说依据，再出投稿清单（正文、图表源文件、补充材料、"
              "声明、作者信息、推荐审稿人）；cover letter 只写事实与卖点，不夸大。",
     "prompts": ["这篇稿子投哪个刊合适", "投稿前还缺什么材料", "帮我写 cover letter"]},
    {"id": "builtin-slides", "name": "演示稿", "avatar": "📽", "sort_order": 23,
     "description": "答辩/汇报 PPT：一页一个结论、图表能自证、配讲稿。",
     "rules": "先给大纲（每页一句话结论），再逐页给标题 + 图上放什么 + 3 条以内要点；"
              "数据页必须有图或表支撑，别用大段文字；每页附 30 秒讲稿，先讲结论再讲怎么得到的。",
     "prompts": ["帮我把这篇论文做成答辩 PPT 大纲", "这页信息太多，怎么精简", "配一版讲稿"]},
    {"id": "builtin-grant", "name": "项目申报", "avatar": "📑", "sort_order": 24,
     "description": "申报书按评分点写：讲清做什么、为什么是我、怎么保证做成。",
     "rules": "先对齐评审指标（逐项列出评分点与对应材料），再写内容：问题与意义 → 目标 → 技术路线 → "
              "基础与条件 → 预期成果。每一段都要能被评分表打到分，空话删掉；时间节点给到月。",
     "prompts": ["按这个申报模板列提纲", "评审会盯哪些点", "研究基础这节怎么写"]},
    {"id": "builtin-minutes", "name": "会议纪要", "avatar": "🗓", "sort_order": 25,
     "description": "从聊天/录音记录里抽出决策、待办、负责人与时间点。",
     "rules": "只抽记录里说过的：决策（谁定的） / 待办（做什么 + 谁 + 何时）/ 待澄清问题。"
              "每条后括注来源句子，别替人补结论；没提时间就写「未定」。",
     "prompts": ["把这段记录整理成纪要", "抽出所有待办和负责人", "这次会上定了什么"]},
    {"id": "builtin-tutor", "name": "概念讲解", "avatar": "🧑🏫", "sort_order": 26,
     "description": "把方法/原理讲到能懂：先类比再严格，最后给可验证的小例子。",
     "rules": "先用一句大白话类比说清它是什么、解决什么问题，再补严格定义与前提；"
              "给了公式要配一个能手算的小例子，最后列 2 个「怎么自己验证」的办法。术语第一次出现就用中文解释。",
     "prompts": ["用通俗的话讲清这个方法", "这个概念的前提是什么", "给我一个能手算的例子"]},
    {"id": "builtin-chunker", "name": "分块员", "avatar": "🧩", "sort_order": 27,
     "description": "把一篇论文切成可分工的块：每块给页码范围、一句话内容、该交给谁读。",
     "rules": "只切分不解读：按结构切（摘要 / 引言 / 方法 / 结果 / 讨论 / 结论，图与表各自单独成块），"
              "每块给「页码范围 + 一句话内容 + 交给谁（图表块→图表解读员，正文块→论文精读）」。"
              "扫描件没文本层、或结构读不出来时直说，别猜内容。",
     "prompts": ["把这篇切成块，标出每块谁读", "图和表单独成块", "这篇正文块怎么分才不重复"]},
    {"id": "builtin-chartread", "name": "图表解读员", "avatar": "🖼", "sort_order": 28,
     "description": "只读图与表：编号、轴与单位、关键数值、能不能支撑正文那句话。",
     "rules": "只读图表不读正文：逐个给「图/表编号 + 它想说明什么 + 横纵轴与单位 + 图例编码 + 关键数值（照抄，带误差棒/显著性标记）"
              "+ 能支撑哪句结论」。图里看不出或没标数值就写「图里看不出」，绝不估算；数值与正文对不上时两处都列出来。",
     "prompts": ["把这篇的图表逐个解读", "这张图能支撑正文哪句话", "图里的数值和正文对得上吗"]},
    {"id": "builtin-outline", "name": "结构规划员", "avatar": "🧱", "sort_order": 29,
     "description": "按项目框架定大纲：每章要证明什么、要哪些证据、还缺什么。",
     "rules": "只出结构不写正文：按「大标题 → 小标题 → 这一节要证明的一件事 → 需要哪些证据/图/数据」给纲领，"
              "与项目现有框架对齐、不擅自加章节；没有的证据标「待补：要什么数据」，最后单列缺口清单。",
     "prompts": ["按框架定这章的大纲", "每节分别要什么证据", "还缺哪些数据才能写"]},
]


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


DELEGATE_TOOL = _schema("delegate", "把 1-4 个互不依赖的子任务**并行**派给独立上下文的子 agent，各自跑完只回一份摘要。"
            "适合：分别读/算好几篇文献或好几个方向、需要独立视角复核、单条线索太长会挤爆上下文时。"
            "子 agent 看不到这段对话历史、只有只读工具；写操作留在你（父 agent）手里。",
            {"jobs": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string", "description": "子任务短名，用于前台分行显示"},
                "prompt": {"type": "string", "description": "这一份要独立完成的任务，写清对象与要交的东西"},
                "max_steps": {"type": "integer", "description": "最多几步工具循环，默认 4"}},
                "required": ["prompt"]}},
                "parallel": {"type": "integer", "description": "并行度，默认 3"}},
            ["jobs"])


TOOL_SPECS = [
    DELEGATE_TOOL,
    _schema("project_list", "列出已经添加的项目、项目 ID 和文件夹，返回当前绑定项目 ID。", {}),
    _schema("library_overview", "查看当前任务的文献/检索结果/笔记/证据数量和最近条目，先调它了解现状。", {}),
    _schema("idea_review", "审查一个创新点：给新颖性/可行性/证据充分度评分、风险与待答问题。", {"idea": {"type": "string", "description": "待审查的创新点"}}, ["idea"]),
    _schema("paper_search", "在 OpenAlex/Crossref/arXiv 检索论文并入库；结果按「相关度×引用量」排序，一次最多留 100 条。", {
        "query": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string", "enum": ["openalex", "crossref", "arxiv"]}},
        "rows": {"type": "integer", "description": "每源条数(1-50)，默认 30"}}, ["query"]),
    _schema("paper_download", "下载一条检索结果的开放获取(OA) PDF 并入库；没有 OA 链接会失败。", {"result_id": {"type": "string"}}, ["result_id"]),
    _schema("paper_read", "把一篇已入库 PDF 逐页精读成中文要点（带 p.页码）。", {"paper_id": {"type": "string"}}, ["paper_id"]),
    _schema("notes_compose", "把一条精读笔记整理成带证据表的知识库笔记，可选写入 Obsidian。", {"note_id": {"type": "string"}, "vault": {"type": "string"}}, ["note_id"]),
    _schema("draft_write", "用当前任务的笔记与证据起草一个章节。", {"section": {"type": "string"}, "title": {"type": "string"}}),
    _schema("draft_review", "以审稿人视角审查稿件，给出结论/评分/问题清单/证据缺口。", {"draft": {"type": "string"}}),
    _schema("format_convert", "把稿件导出为 docx / doc / md / html / pdf，或把一份现成的 .tex 编成 PDF（format=tex）。用户说「转成 PDF」用 pdf；说「Word」用 docx（老版本 .doc 用 doc）；说「这份 tex 转 PDF」用 tex。template 决定 PDF 走哪套期刊模板：plain=本机通用（默认）、elsevier、ieee、revtex（APS）。cite_style 决定参考文献样式（中文稿默认 gbt7714），pdf 与 docx/html/md 都按它走：正文里的 {{C3}} 这类标记会自动变成正式引用，台账里的文献从 Zotero 取元数据。模板出处见 TEMPLATES 里的 repo（bamos/latex-templates、jsk-report-template/elsarticle-template 等公开库）。",
            {"format": {"type": "string", "enum": ["docx", "doc", "md", "html", "pdf", "tex"]},
             "source": {"type": "string", "description": "tex 才用：要编译的 .tex 绝对路径；不给就取项目里最新的 .tex"},
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
    _schema("project_note_write", "在项目笔记分类中创建或更新 Markdown 文件。", {"project": {"type": "string"}, "path": {"type": "string", "description": "例如 笔记/文献矩阵/主题.md"}, "content": {"type": "string"}}, ["path", "content"]),
    _schema("project_task_manage", "列出、创建、更新待办，或通过 dispatch 派给绑定专家执行。", {"action": {"type": "string", "enum": ["list", "create", "update", "dispatch"]}, "project": {"type": "string"}, "id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string"}, "priority": {"type": "string"}, "assistant_id": {"type": "string"}, "due_date": {"type": "string"}, "stage": {"type": "string"}, "reviewer_id": {"type": "string"}}, ["action"]),
    _schema("assistant_manage", "列出专家或创建、更新专家档案。", {"action": {"type": "string", "enum": ["list", "create", "update"]}, "id": {"type": "string"}, "name": {"type": "string"}, "rules": {"type": "string"}, "description": {"type": "string"}, "model_id": {"type": "string"}}, ["action"]),
    _schema("team_create", "为项目创建团队并添加已有专家。", {"project": {"type": "string"}, "name": {"type": "string"}, "purpose": {"type": "string"}, "assistant_ids": {"type": "array", "items": {"type": "string"}}}, ["name"]),
    _schema("workflow_create", "从现有科研步骤组合并绑定项目工作流。", {"project": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "steps": {"type": "array", "items": {"type": "string", "enum": ["idea", "search", "download", "notes", "outline", "map", "write", "review", "entrans", "convert"]}}}, ["name", "steps"]),
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
             "output_dir": {"type": "string", "description": "create 用：产物子目录，默认 定时任务"},
             "enabled": {"type": "boolean", "description": "toggle 用：true=启用，false=暂停"}},
            ["action"]),
    _schema("team_board", "看团队看板：成员各自状态 + 最近派活与执行情况（谁在跑、谁完成了、失败原因）。",
            {"project": {"type": "string", "description": "项目名或 id；不填=当前项目"},
             "team_id": {"type": "string", "description": "team_create 或看板返回的团队 ID；省略时列出项目团队"}}),
    _schema("team_form", "为一个项目临时组队：说清为什么组、要交什么、拉哪几个助手（写名字），开一间加急办公室。"
            "加急位最多 3 间，用完自动归还；先 team_board 看有哪些人可用。",
            {"purpose": {"type": "string", "description": "这支队为什么组、要交什么"},
             "members": {"type": "array", "items": {"type": "string"}, "description": "助手名字，2-4 个"},
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
             "limit": {"type": "integer", "description": "每类最多几条，默认 8"}}, ["query"]),
    _schema("translate_doc", "把「论文撰写」那份正文.md 整篇译成英文、落成同目录的 正文_en.md："
                             "服务端按节分片、4 路并行翻，几十秒到两分钟，不往对话里灌全文。"
                             "用户说「翻正文 / 整篇翻 / 出英文版」就用它 —— 不要自己一段一段手译（那样又慢又贵）。",
            {"style": {"type": "string", "enum": ["formal", "concise", "impactful"],
                       "description": "formal=标准学术（默认）；concise=极简凝练；impactful=强调突破"}}),
    _schema("read_attachment", "读项目里（或用户上传的）一个文件的正文：PDF 自动抓文本，其它按文本读。"
                              "读正文/大纲/笔记/精读稿之前先调它 —— 路径给绝对路径或相对项目根都行；chars 是返回字符上限。",
            {"path": {"type": "string", "description": "文件路径。中文正文的默认位置就是 论文正文/正文.md（相对项目根）；"
                                                      "给绝对路径也行，也要能读 笔记/、文献库/ 里的文件"},
             "chars": {"type": "integer", "description": "最多返回多少字，默认 20000（上限 48000）"}}, ["path"]),
    _schema("file_list", "列项目文件夹里某一层的内容（目录 + 文件，带大小与修改时间）。rel 相对项目根，空 = 根。"
                                "正文在大纲/撰写/审查产出里叫 论文正文/正文.md，框架是 论文正文/框架.md。",
            {"rel": {"type": "string", "description": "相对路径，如 论文正文 或 最终产物/_tex；空 = 项目根"}}),
    _schema("file_hand", "把项目里的一个文件转到某一步的对话里 —— 那一步的助手下次开口就带着它（用户说的「交给写作助手」）。",
            {"rel": {"type": "string", "description": "相对项目根的路径（文件或文件夹）"},
             "step": {"type": "string", "enum": ["idea", "search", "download", "notes", "outline", "map",
                                                 "write", "review", "entrans", "convert"],
                      "description": "交给哪一步"},
             "note": {"type": "string", "description": "附一句要那一步做什么（可空）"}}, ["rel", "step"]),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]

# 创新点审查这一步只放开这两个工具：正式判定 + 记一条长期记忆
IDEA_TOOLS = [
    _schema("idea_review", "对当前创新点出正式判定（五维评分 1-10 + 致命缺陷 + 三档结论），会落成项目笔记并写入长期记忆。",
            {"idea": {"type": "string", "description": "用户要审查的创新点原文（可带上刚补充的约束）"}}, ["idea"]),
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
            if not capabilities.enabled(db, db.assistant_of_chat(task_id), "skill", item["id"]):
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



