"""Research platform steps shared by settings, agents and delivery checks."""

STAGE_CATALOG = [
    {"id": "idea", "name": "创新点审查", "icon": "🔍", "agent": "创新点审查专家", "purpose": "审查创新性、可行性与证据缺口"},
    {"id": "search", "name": "论文搜索", "icon": "📚", "agent": "文献检索专家", "purpose": "检索论文并筛选开放获取全文"},
    {"id": "download", "name": "下载精读", "icon": "⬇️", "agent": "入库精读专家", "purpose": "下载入库并逐页精读，整理带页码的笔记"},
    {"id": "notes", "name": "论文翻译", "icon": "🌐", "agent": "翻译专家", "purpose": "生成论文中文译文与双语对照，精读笔记不能替代译文交付"},
    {"id": "outline", "name": "确认大纲", "icon": "🗂", "agent": "框架专家", "purpose": "确定章节结构与每节论证目标"},
    {"id": "map", "name": "梳理精读", "icon": "🧩", "agent": "谱系专家", "purpose": "将精读笔记与证据按大纲组织成谱系图"},
    {"id": "write", "name": "文章撰写", "icon": "✍️", "agent": "写作专家", "purpose": "按大纲和证据撰写正文"},
    {"id": "review", "name": "稿件审查", "icon": "🧐", "agent": "审查专家", "purpose": "检查稿件逻辑、证据与引用"},
    {"id": "entrans", "name": "中译英", "icon": "🌍", "agent": "中译英专家", "purpose": "将中文稿件翻译为学术英文"},
    {"id": "convert", "name": "格式转换", "icon": "🔄", "agent": "格式专家", "purpose": "按目标格式导出稿件"},
]
STAGES = [stage["id"] for stage in STAGE_CATALOG]
STAGE_LABEL = {stage["id"]: stage["name"] for stage in STAGE_CATALOG}
STEP_AGENT = {stage["id"]: stage["agent"] for stage in STAGE_CATALOG}
STAGE_PURPOSE = {stage["id"]: stage["purpose"] for stage in STAGE_CATALOG}

# Shared by the node worker and the workbench so the execution and review
# instructions refer to the same stage deliverable.
STAGE_GUIDES = {
    "idea": {
        "input": "项目研究问题、创新点原文及已有证据。",
        "output": "创新性、可行性、证据缺口和是否推进的审查报告。",
        "checks": ["审查对应当前研究问题", "区分已有证据与待验证假设"],
        "action": "用 idea_review 保存正式审查报告。明确哪些问题需要下一步检索。",
    },
    "search": {
        "input": "创新点审查交付的问题、关键词和证据缺口。",
        "output": "已保存的候选文献、来源和可用全文入口。",
        "checks": ["文献与证据缺口对应", "核对 DOI、年份与全文可用性"],
        "action": "用 paper_search 保存真实检索结果。说明纳入和排除标准，禁止编造 DOI 或链接。",
    },
    "download": {
        "input": "已纳入的检索结果及可合法获取或已入库的全文。",
        "output": "项目 PDF 和与原文页码对应的精读笔记。",
        "checks": ["确认 PDF 可读", "抽查笔记页码及引文", "记录无法获取或无法解析的全文"],
        "action": "先复用同一文献的已入库 PDF，再对新结果调用下载工具。用 paper_read 对可读 PDF 生成逐页笔记，并用 paper_evidence_record 登记可能支撑关键结论的数值、条件和连续原句。",
    },
    "notes": {
        "input": "已入库 PDF 与精读环节确认的论文范围。",
        "output": "已完成的中文译文或双语文件及翻译范围。",
        "checks": ["核对术语、图表编号与数值", "确认译文文件已生成"],
        "action": "用 paper_translate 执行所选论文的翻译并核对完成状态。保留已完成页数和失败范围。",
    },
    "outline": {
        "input": "研究问题、文献证据及已完成的精读和翻译材料。",
        "output": "带章节论点、支撑证据和缺口提示的正文大纲。",
        "checks": ["逐节写清要回答的问题", "标出证据不足的章节"],
        "action": "用 doc_write 保存大纲。每节声明论点、所需证据和写作边界。",
    },
    "map": {
        "input": "已验收的大纲和带来源页码的精读证据。",
        "output": "将章节、论点和文献证据连接起来的谱系图。",
        "checks": ["检查重点论点能追溯到来源", "区分支持、冲突和证据缺口"],
        "action": "用 map_write 保存谱系。把真实证据挂到相应章节节点，保留页码和出处。",
    },
    "write": {
        "input": "已验收的大纲、谱系和可追溯的引文。",
        "output": "正文稿、引用标记及仍需补证的段落。",
        "checks": ["按大纲完成关键章节", "逐处核对引文支撑正文结论"],
        "action": "按证据谱系撰写，用 doc_write 保存正文。证据不足时保留明确标记。",
    },
    "review": {
        "input": "本次交接的正文及其引用证据。",
        "output": "逐条可定位的审查意见、修改优先级和引用核对结果。",
        "checks": ["重要意见可以定位到段落", "区分事实错误、证据不足和表达问题"],
        "action": "用 draft_review 审查本次交付的正文并保存审查结果，或用 doc_write 保存审查阶段的逐条意见。",
    },
    "entrans": {
        "input": "当前已确认的中文正文和审查结论。",
        "output": "同时保留章节结构、引用标记和数值的英文稿。",
        "checks": ["核对术语一致性", "检查数字、单位、图表和引用未丢失"],
        "action": "用 manuscript_translate_en 读取已确认的中文稿并保存英文稿，保持引用和章节对应。",
    },
    "convert": {
        "input": "指定的正文版本、参考文献与所选导出格式。",
        "output": "可打开的目标文件，以及可追溯的源稿和导出记录。",
        "checks": ["实际打开导出文件", "核对引用、标题、图表和目录"],
        "action": "用 format_convert 导出已验收的稿件。未指定 source 时优先采用上游交付的英文稿，再核对实体文件。",
    },
}
for stage in STAGE_CATALOG:
    stage["guide"] = STAGE_GUIDES[stage["id"]]
LEGACY_STAGE_LABEL = {
    "idea": "创新点审查", "search": "文献检索", "download": "文献入库",
    "notes": "文献精读", "outline": "拟定框架", "map": "证据链谱系",
    "write": "论文撰写", "review": "AI预审查", "entrans": "学术英译", "convert": "格式排版",
}

# The original built-in templates stored only stages; their project offices
# stored the executor configuration separately. Stable IDs preserve renamed
# offices and experts without admitting unrelated project/global assistants.
LEGACY_TEAM_WORKFLOWS = frozenset({
    "builtin.workflow.research", "builtin.workflow.reading", "builtin.workflow.writing",
})
LEGACY_TEAM_STAGES = {
    "builtin-literature-lead": frozenset({"idea", "search", "download", "notes", "map"}),
    "builtin-writing-lead": frozenset({"outline", "write", "review", "entrans", "convert"}),
}
LEGACY_MEMBER_STAGES = {
    "builtin-searcher": frozenset({"search"}),
    "builtin-chunker": frozenset({"download", "notes"}),
    "builtin-chartread": frozenset({"download", "map"}),
    "builtin-extractor": frozenset({"download", "map"}),
    "builtin-outline": frozenset({"outline"}),
    "builtin-writer": frozenset({"write"}),
    "builtin-paperwriting": frozenset({"write"}),
    "builtin-polish": frozenset({"review", "entrans"}),
}
