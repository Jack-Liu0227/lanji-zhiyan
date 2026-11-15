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
