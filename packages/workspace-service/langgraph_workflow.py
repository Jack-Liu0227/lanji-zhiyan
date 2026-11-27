# -*- coding: utf-8 -*-
"""Compatibility entry points for the single project workflow runtime.

Conforms to .codex-tasks/20261027-project-centered-consolidation/PLAN.md:
- project_tasks is the single source of truth for execution and delivery.
- StateGraph acts as the orchestration and review checkpoint adapter.
- Real agents/teams execute real runs and write real files (no fake reports).
- Human review is strictly enforced (agents cannot self-approve).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ==============================================================================
# LangGraph Core StateGraph Implementation
# ==============================================================================

class GraphNode:
    def __init__(self, name: str, label: str = "", executor_hint: str = "", review_required: bool = True,
                 step: str = "task", prompt: str = "", tools: Optional[List[str]] = None,
                 output_contract: Optional[Dict[str, Any]] = None):
        self.name = name
        self.label = label or name
        self.executor_hint = executor_hint  # assistant | team | default
        self.review_required = review_required
        self.step = step or "task"
        self.prompt = prompt
        self.tools = list(tools) if tools is not None else None
        self.output_contract = dict(output_contract or {})

class GraphTemplate:
    """Built-in DAG template metadata; execution belongs to workflow_tasks."""
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.nodes: Dict[str, GraphNode] = {}
        self.edge_list: List[Dict[str, str]] = []
        self.edges: Dict[str, str] = {}
        self.entry_point: Optional[str] = None

    def add_node(self, name: str, label: str = "", executor_hint: str = "assistant", review_required: bool = True,
                 step: str = "task", prompt: str = "", tools: Optional[List[str]] = None,
                 output_contract: Optional[Dict[str, Any]] = None) -> "GraphTemplate":
        self.nodes[name] = GraphNode(name, label=label, executor_hint=executor_hint,
                                     review_required=review_required, step=step, prompt=prompt,
                                     tools=tools, output_contract=output_contract)
        if self.entry_point is None:
            self.entry_point = name
        return self

    def add_edge(self, source: str, target: str, artifact: str = "", argument: str = "") -> "GraphTemplate":
        edge = {"source": source, "target": target}
        if artifact:
            edge["artifact"] = artifact
        if argument:
            edge["argument"] = argument
        if edge not in self.edge_list:
            self.edge_list.append(edge)
        if source not in self.edges:
            self.edges[source] = target
        return self

    def set_entry_point(self, name: str) -> "GraphTemplate":
        self.entry_point = name
        return self

    def get_edges(self) -> List[Dict[str, str]]:
        if self.edge_list:
            return list(self.edge_list)
        return [{"source": s, "target": t} for s, t in self.edges.items()]

    def get_topology(self) -> List[Dict[str, Any]]:
        edges = self.get_edges()
        parents: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        children: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
        for e in edges:
            if e["source"] in self.nodes and e["target"] in self.nodes:
                parents[e["target"]].append(e["source"])
                children[e["source"]].append(e["target"])
        waiting = {nid: len(p) for nid, p in parents.items()}
        queue = [nid for nid in self.nodes if not waiting[nid]]
        levels: Dict[str, int] = {}
        ordered = []
        while queue:
            curr = queue.pop(0)
            lvl = max((-1, *(levels.get(p, 0) for p in parents[curr]))) + 1
            levels[curr] = lvl
            node = self.nodes[curr]
            outs = children[curr]
            ordered.append({
                "id": node.name,
                "label": node.label,
                "executor_hint": node.executor_hint,
                "execution_mode": node.executor_hint if node.executor_hint in ("assistant", "team") else "",
                "step": node.step,
                "review_required": node.review_required,
                "level": lvl,
                "next": outs[0] if len(outs) == 1 else (outs if outs else None),
            })
            for child in outs:
                waiting[child] -= 1
                if not waiting[child]:
                    queue.append(child)
        return ordered

# Scientific State Graphs
def create_research_proposal_graph() -> GraphTemplate:
    g = GraphTemplate("research_proposal", "研究开题与方案论证图")
    g.add_node("problem_formulation", "① 研究问题与科学假设提炼", executor_hint="assistant", step="idea", review_required=True)
    g.add_node("literature_gap", "② 文献现状与研究空白剖析", executor_hint="assistant", step="search", review_required=True)
    g.add_node("methodology_design", "③ 技术路线与实验方案设计", executor_hint="team", step="task", review_required=True)
    g.add_node("proposal_synthesis", "④ 开题论证总结报告撰写", executor_hint="assistant", step="write", review_required=True)
    g.add_edge("problem_formulation", "literature_gap")
    g.add_edge("literature_gap", "methodology_design")
    g.add_edge("methodology_design", "proposal_synthesis")
    g.set_entry_point("problem_formulation")
    return g

def create_paper_writing_graph() -> GraphTemplate:
    g = GraphTemplate("paper_writing", "学术论文全流程起草与评审图")
    g.add_node("outline_design", "① 论文大纲与核心逻辑构思", executor_hint="assistant", step="outline", review_required=True)
    g.add_node("section_drafting", "② 正文核心章节起草与论述", executor_hint="team", step="write", review_required=True)
    g.add_node("peer_review", "③ 模拟同行评审与学术批判", executor_hint="assistant", step="review", review_required=True)
    g.add_node("revision_polishing", "④ 针对审稿意见修订与润色", executor_hint="assistant", step="write", review_required=True)
    g.add_edge("outline_design", "section_drafting")
    g.add_edge("section_drafting", "peer_review")
    g.add_edge("peer_review", "revision_polishing")
    g.set_entry_point("outline_design")
    return g

def create_literature_study_graph() -> GraphTemplate:
    g = GraphTemplate("literature_study", "五节点文献综述：检索、并行取证、综合与终检")
    g.add_node("literature_retrieval", "A 检索与筛选", executor_hint="assistant", step="search", review_required=True,
        prompt="按研究问题和纳排标准执行多组检索。分页读取摘要并筛选，记录题录 ID、标题、作者、年份、DOI、摘要证据级别、纳排理由、全文状态。对拟纳入的 OA 条目尝试下载，精读只能使用下载返回的 paper.id。保存 candidate_papers.csv 与 screening_notes.md。不得用空表、预检占位或题名列表冒充检索交付。",
        output_contract={"required_files": ["candidate_papers.csv", "screening_notes.md"], "file_rules": {
            "candidate_papers.csv": {"min_rows": 8, "required_columns": ["title", "doi", "abstract_evidence", "screening_decision", "decision_reason", "full_text_availability"]},
            "screening_notes.md": {"min_chars": 300}}, "acceptance": ["检索式、来源、日期和命中量可追溯", "至少八条实际筛选记录；不足时在节点报告中列出工具/来源限制并停止", "全文和摘要证据明确区分"]})
    g.add_node("mechanism_evidence", "B 界面机制与证据", executor_hint="assistant", step="task", review_required=True,
        prompt="基于 A 已交付的候选 CSV 和筛选说明分页读取完整内容；按输出中的 download_with 先获取 OA 全文，再以返回 paper.id 精读。区分硫化物/氧化物/聚合物及正极/锂负极界面，记录直接观测、模型推断、作者解释、对照与测试条件。全文机制结论用 paper_evidence_record 登记物理页码和连续原文。摘要级结果单列，不补造来源或数字。保存 evidence_analysis.md。",
        output_contract={"required_files": ["evidence_analysis.md"], "requires_evidence": True, "acceptance": ["至少八条核心候选被逐条核查或明确记录未取得全文", "全文机制结论包含可查询 evidence ID", "摘要、题录和全文证据分级"]})
    g.add_node("strategy_metrics", "C 改性策略与指标比较", executor_hint="assistant", step="task", review_required=True,
        prompt="完整读取 A 的候选表。仅对实际纳入且已取得全文的研究逐篇提取改性策略、材料、界面类型、对照、测试系统、温度/压力/电流等条件、指标/单位/数值和页码/表图。无来源的字段留空并标明未报告。跨条件数据不得直接排名。保存 methods_metrics.csv。",
        output_contract={"required_files": ["methods_metrics.csv"], "file_rules": {"methods_metrics.csv": {
            "min_rows": 1, "required_columns": ["title", "doi", "interface_modification_method", "test_conditions", "metric", "unit", "value", "evidence_level", "limitations"]}},
            "acceptance": ["CSV 每行列数一致", "已读全文的数据提供定位", "未报告字段留空，不推测"]})
    g.add_node("literature_survey", "D 综合矩阵与综述初稿", executor_hint="assistant", step="write", review_required=True,
        prompt="先完整分页读取 B 与 C 每个 accepted_upstream 文件至 next_offset=null，并核对 delivery_id/content_hash。截取内容不是来源不存在。仅依据 A/B/C 固定交付撰写 2500-4000 中文字综合综述，比较硫化物与氧化物界面的机制证据、改性条件、对照与指标；区分共识、争议和未取得全文的缺口。每个关键主张对应可追溯引用，摘要级逐项标注。输出 comparison_matrix.md 与 review_draft.md。不要用一般背景扩字或虚构核心文献。",
        output_contract={"required_files": ["comparison_matrix.md", "review_draft.md"], "file_rules": {
            "comparison_matrix.md": {"min_chars": 1500}, "review_draft.md": {"min_chars": 2500}},
            "acceptance": ["比较条件和证据层级齐备", "正文至少 2500 个非空白字符", "引用只来自完整读取的上游交付"]})
    g.add_node("citation_review", "E 引用核验、修订与交付", executor_hint="assistant", step="review", review_required=True,
        prompt="逐文件分页读取 A-D 已接受的全部相关产物，直到 next_offset=null；检查 DOI/题录匹配、正文引用、BibTeX 和候选清单逐条一致。核验不通过的来源移除或标待核，不能称其题录不存在而不读取全文。用 paper_search 对疑点 DOI/title 复核。输出 literature_review.md（至少 2500 字）、references.bib（至少八条各含 title 和 DOI 或稳定 URL 的有效记录）、citation_check.md（每条逐项核验状态/证据级别/页码/问题）。全文不可得要准确标记摘要级/未核。机器结构检查和人工学术核查分开，最后进入人工核查，不自行批准。",
        output_contract={"required_files": ["literature_review.md", "references.bib", "citation_check.md"], "file_rules": {
            "literature_review.md": {"min_chars": 2500},
            "references.bib": {"min_entries": 8, "require_title_and_locator": True, "require_candidate_match": True},
            "citation_check.md": {"min_chars": 1000}},
            "acceptance": ["正文引用键与 BibTeX 一致", "至少八条题名和 DOI/稳定链接可核记录", "证据层级、页码和待核事项完整", "等待人工核查"]})
    g.add_edge("literature_retrieval", "mechanism_evidence")
    g.add_edge("literature_retrieval", "strategy_metrics")
    g.add_edge("mechanism_evidence", "literature_survey")
    g.add_edge("strategy_metrics", "literature_survey")
    g.add_edge("literature_survey", "citation_review")
    g.set_entry_point("literature_retrieval")
    return g

def create_full_research_graph() -> GraphTemplate:
    from workflow_catalog import STAGE_CATALOG
    graph = GraphTemplate("full_research", "完整十步科研流程")
    for stage in STAGE_CATALOG:
        graph.add_node(stage["id"], stage["name"], executor_hint="team", step=stage["id"])
    stages = list(graph.nodes)
    for source, target in zip(stages, stages[1:]):
        graph.add_edge(source, target)
    return graph.set_entry_point(stages[0])


def create_hybrid_research_dag() -> GraphTemplate:
    g = GraphTemplate("hybrid_research_dag", "串并混合六节点科研 DAG 流")
    g.add_node("s1_seed", "Step 1: S1 建目录与课题素材", executor_hint="assistant", step="idea", review_required=True)
    g.add_node("p1_overview", "Step 2: P1 文献检索与概览文档", executor_hint="assistant", step="search", review_required=True)
    g.add_node("p2_methodology", "Step 3: P2 实验方法与步骤文档", executor_hint="assistant", step="outline", review_required=True)
    g.add_node("p3_risk_audit", "Step 4: P3 创新与风险核查文档", executor_hint="assistant", step="task", review_required=True)
    g.add_node("j1_synthesis", "Step 5: J1 证据图谱与汇合汇编", executor_hint="team", step="map", review_required=True)
    g.add_node("s2_verification", "Step 6: S2 终检自证与成稿", executor_hint="assistant", step="review", review_required=True)
    g.add_edge("s1_seed", "p1_overview")
    g.add_edge("s1_seed", "p2_methodology")
    g.add_edge("s1_seed", "p3_risk_audit")
    g.add_edge("p1_overview", "j1_synthesis")
    g.add_edge("p2_methodology", "j1_synthesis")
    g.add_edge("p3_risk_audit", "j1_synthesis")
    g.add_edge("j1_synthesis", "s2_verification")
    return g.set_entry_point("s1_seed")


def create_literature_review_dag() -> GraphTemplate:
    g = GraphTemplate("literature_review_dag", "文献综述五节点并发工作流")
    g.add_node("lit_search", "① 文献检索与初筛", executor_hint="assistant", step="search", review_required=False,
               output_contract={"artifacts": [{"name": "candidate_papers.csv", "format": "csv"}]})
    g.add_node("lit_method", "②A 方法论与证据链提取", executor_hint="assistant", step="download", review_required=False,
               output_contract={"artifacts": [{"name": "methodology_analysis.md", "format": "markdown"}]})
    g.add_node("lit_metrics", "②B 评价指标横向抽样", executor_hint="assistant", step="map", review_required=False,
               output_contract={"artifacts": [{"name": "benchmark_metrics.csv", "format": "csv"}]})
    g.add_node("lit_matrix", "③ 综述对比矩阵汇聚", executor_hint="assistant", step="map", review_required=False,
               output_contract={"artifacts": [{"name": "comparison_matrix.md", "format": "markdown"}]})
    g.add_node("lit_draft", "④ 前沿综述综合报告与人工核查", executor_hint="assistant", step="write", review_required=True,
               output_contract={"artifacts": [{"name": "literature_survey.md", "format": "markdown"}]})
    g.add_edge("lit_search", "lit_method")
    g.add_edge("lit_search", "lit_metrics")
    g.add_edge("lit_method", "lit_matrix")
    g.add_edge("lit_metrics", "lit_matrix")
    g.add_edge("lit_matrix", "lit_draft")
    return g.set_entry_point("lit_search")


GRAPH_REGISTRY: Dict[str, Callable[[], GraphTemplate]] = {
    "full_research": create_full_research_graph,
    "research_proposal": create_research_proposal_graph,
    "paper_writing": create_paper_writing_graph,
    "literature_study": create_literature_study_graph,
    "hybrid_research_dag": create_hybrid_research_dag,
    "literature_review_dag": create_literature_review_dag,
}

# ==============================================================================
# Execution via project_tasks & Real Workers
# ==============================================================================

def _get_project_binding(db, project_id: str, hint: str = "assistant") -> dict:
    import project_bindings
    bindings = [item for item in project_bindings.list_bindings(db, project_id)
                if item.get("enabled") and item.get("available", True)]
    preferred = next((item for item in bindings if item["kind"] == hint), None)
    if preferred:
        return preferred
    if bindings:
        return bindings[0]
    existing = db.db.execute("SELECT id FROM project_executor_bindings WHERE project_id=? AND target_id='builtin-steward'", (project_id,)).fetchone()
    if existing:
        raise ValueError("项目专家已停用，请在项目执行者中选择可用专家")
    return project_bindings.register(db, project_id, "assistant", "builtin-steward", source="auto")


def _template(db, graph_type):
    import workflow_layout
    workflow_layout.ensure_schema(db)
    if graph_type not in GRAPH_REGISTRY:
        raise ValueError("科研流程模板不存在")
    graph = GRAPH_REGISTRY[graph_type]()
    identifier = "builtin.langgraph." + graph_type
    if not db.db.execute("SELECT 1 FROM workflow_layouts WHERE id=?", (identifier,)).fetchone():
        import workflow_template_catalog
        if workflow_template_catalog.deliberately_deleted(db, identifier):
            raise ValueError('此预设已删除，请在工作流工作台选择或创建模板')
        definition = {"nodes": [{"id": node.name, "step": node.step, "label": node.label,
            "prompt": node.prompt or "完成「" + node.label + "」，依据输入资料和前序交付，使用工具保存真实产物并标明证据来源。",
            "executor_role_id": node.name, "review_required": node.review_required,
            "execution_mode": node.executor_hint if node.executor_hint in ("assistant", "team") else "",
            "tools": node.tools, "output_contract": node.output_contract} for node in graph.nodes.values()],
            "edges": graph.get_edges()}
        definition=workflow_layout.validate_definition(definition)
        db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)", (identifier, graph.description, graph.description,
            json.dumps(definition, ensure_ascii=False), _now_iso(), _now_iso()))
        db.db.commit()
    else:
        stored = workflow_layout._layout(db, identifier)
        # Older LangGraph templates stored every node as a generic task. Fill only
        # missing semantic fields so existing labels, prompts and user edits survive.
        upgraded = []
        changed = False
        for raw in stored.get("nodes", []):
            spec = graph.nodes.get(raw.get("id"))
            node = dict(raw)
            if spec and raw.get("step") == "task" and spec.step != "task":
                node["step"] = spec.step
                changed = True
            if spec and not raw.get("execution_mode") and spec.executor_hint in ("assistant", "team"):
                node["execution_mode"] = spec.executor_hint
                changed = True
            upgraded.append(node)
        if changed:
            definition = workflow_layout.validate_definition({**stored, "nodes": upgraded})
            db.db.execute("UPDATE workflow_layouts SET definition=?,updated_at=? WHERE id=?",
                          (json.dumps(definition, ensure_ascii=False), _now_iso(), identifier))
            db.db.commit()
        elif "order" not in stored:
            workflow_layout.save_layout(db, stored)
    return identifier


def _resolve_graph_inputs(db, project_id: str, graph_type: str, inputs):
    """Separate prose goals from pinned inputs and auto-select project literature."""
    import asset_service
    import project_notes
    root = project_notes.project_root(db, project_id).resolve()
    explicit = []
    for raw in inputs or []:
        if isinstance(raw, dict) and (raw.get('asset_id') or raw.get('relative')):
            explicit.append(dict(raw)); continue
        if isinstance(raw, str) and raw.strip():
            candidate = Path(raw.strip())
            if not candidate.is_absolute() and not candidate.drive:
                path = (root / candidate).resolve()
                if path.is_file() and path.is_relative_to(root):
                    explicit.append({'relative': path.relative_to(root).as_posix()})
    if explicit:
        return explicit, 'explicit'
    if graph_type != 'literature_study':
        return [], 'none'
    selected = []
    try:
        # Existing Zotero papers/evidence rows may not yet have asset records.
        # Rebuild is idempotent and keeps the workflow input snapshot auditable.
        asset_service.rebuild_project_index(db, project_id, include_files=True)
        import project_resources
        catalog = project_resources.catalog(db, project_id, '', '', review_status='all')
        for item in catalog.get('items', []):
            if item.get('kind') not in ('literature', 'evidence', 'note', 'file'):
                continue
            asset_id = str(item.get('asset_id') or '')
            relative = str(item.get('relative') or '')
            candidate = {'asset_id': asset_id} if asset_id else {'relative': relative}
            if not candidate.get('asset_id') and not candidate.get('relative'):
                continue
            if candidate.get('relative'):
                path = (root / candidate['relative']).resolve()
                if not path.is_file() or not path.is_relative_to(root):
                    continue
            if candidate.get('asset_id'):
                asset = asset_service.get(db, candidate['asset_id'])
                if not asset or asset.get('project_id') != project_id or not asset_service.context_available(db, asset):
                    continue
            selected.append(candidate)
    except Exception:
        selected = []
    unique, seen = [], set()
    for item in selected:
        key = item.get('asset_id') or 'path:' + item.get('relative', '')
        if key not in seen:
            seen.add(key); unique.append(item)
    return unique[:100], 'project_literature' if unique else 'none'


def start_graph(db, project_id: str, graph_type: str = "research_proposal", goal: str = "", inputs=None, request_key="", agent_invocation: bool = False) -> dict:
    """All graph entry points use the same versioned layouts and project tasks."""
    import project_bindings
    import project_workflows
    import workflow_execution as engine
    import workflow_layout
    import workflow_tasks
    inputs, input_source = _resolve_graph_inputs(db, project_id, graph_type, inputs)
    workflow_layout._project(db, project_id)
    template_id = _template(db, graph_type)
    layout = project_workflows.current(db, project_id, template_id)
    graph = GRAPH_REGISTRY[graph_type]()
    for node in layout["nodes"]:
        role = project_bindings.role_id(node)
        configured = next((item for item in project_bindings.list_roles(db, project_id, template_id) if item["role_id"] == role), None)
        if not node.get("executor_binding_id") and not (configured or {}).get("binding_id"):
            hint = graph.nodes.get(node["id"])
            binding = _get_project_binding(db, project_id, hint.executor_hint if hint else "assistant")
            project_bindings.bind_role(db, project_id, template_id, role, binding["id"])
    with engine._lock:
        if not engine.active(db, project_id):
            workflow_layout.select_layout(db, {"task_id": project_id, "layout_id": template_id})
        run = workflow_tasks.prepare(db, project_id, goal, request_key, template_id, inputs=inputs)
        if agent_invocation:
            import workflow_node_workbench
            workflow_node_workbench.mark_agent_started(db, run)
        if run["layout"]["id"] != template_id:
            raise ValueError("项目已有其他流程在执行或待核查，请先完成或停止当前运行")
        if inputs and run["settings"].get("task_inputs") != inputs:
            raise ValueError("当前运行已固定另一组输入，请先完成或停止当前运行")
        run["settings"]["graph_type"] = graph_type
        run["settings"]["input_source"] = input_source
        workflow_tasks._save(db, run)
    if run["status"] == "queued":
        engine.kick(db.path, run["id"])
    return get_graph_state(db, project_id, run["id"])


def _legacy_state(db, project_id, row):
    """Previous experimental graph runs remain readable without restarting work."""
    import project_tasks
    value = dict(row)
    value["state_snapshot"] = json.loads(value.get("state_snapshot") or "{}")
    checkpoints = []
    for saved in db.db.execute("SELECT * FROM langgraph_checkpoints WHERE run_id=? ORDER BY created_at,id", (row["id"],)):
        checkpoint = dict(saved)
        if checkpoint.get("task_id"):
            task = project_tasks.get(db, checkpoint["task_id"], project_id=project_id)
            checkpoint.update(real_status=task["status"], outputs=task["outputs"])
        checkpoints.append(checkpoint)
    graph = GRAPH_REGISTRY.get(value["graph_type"], create_research_proposal_graph)()
    return {"has_run": True, "legacy": True, "project_id": project_id, "run": value, "checkpoints": checkpoints,
        "current_graph": {"id": graph.name, "label": graph.description, "topology": graph.get_topology()},
        "available_graphs": [{"id": key, "label": factory().description} for key, factory in GRAPH_REGISTRY.items()]}


def get_graph_state(db, project_id: str, run_id=None) -> dict:
    import workflow_execution as engine
    import workflow_layout
    import project_tasks
    workflow_layout._project(db, project_id)
    legacy = db.db.execute("SELECT * FROM langgraph_runs WHERE id=? AND project_id=?", (run_id or "", project_id)).fetchone()
    if legacy:
        return _legacy_state(db, project_id, legacy)
    run = engine.detail(db, run_id) if run_id else engine.latest(db, project_id)
    if run and run["project_id"] != project_id:
        raise ValueError("运行不属于当前项目")
    available = [{"id": key, "label": factory().description} for key, factory in GRAPH_REGISTRY.items()]
    if not run:
        old = db.db.execute("SELECT * FROM langgraph_runs WHERE project_id=? ORDER BY updated_at DESC LIMIT 1", (project_id,)).fetchone()
        if old:
            return _legacy_state(db, project_id, old)
        graph = create_research_proposal_graph()
        return {"has_run": False, "project_id": project_id, "run": None, "checkpoints": [], "available_graphs": available,
            "current_graph": {"id": graph.name, "label": graph.description, "topology": graph.get_topology()}}
    mapping = run["settings"].get("task_ids") or {}
    checkpoints = []
    for node in run["layout"]["nodes"]:
        if node["id"] not in mapping:
            continue
        task = project_tasks.get(db, mapping[node["id"]], project_id=project_id)
        latest = task.get("latest_run") or {}
        decision = (task.get("deliveries") or [{}])[-1]
        checkpoints.append({"id": task["id"], "task_id": task["id"], "task_run_id": latest.get("id", ""),
            "node_id": node["id"], "node_label": node["label"], "real_status": task["status"],
            "review_status": "approved" if task["status"] == "delivered" else "rejected" if decision.get("decision") == "rejected" else "pending",
            "reviewer_feedback": decision.get("feedback", ""), "outputs": task["outputs"],
            "output_summary": latest.get("checkpoint", {}).get("reply", "")})
    value = {**run, "graph_type": run["settings"].get("graph_type", run["layout"]["id"]),
        "title": run["layout"]["name"], "status": {"succeeded": "completed"}.get(run["status"], run["status"])}
    import task_tools
    handoff = task_tools.workflow_result(db, run)
    return {"has_run": True, "project_id": project_id, "run": value, "checkpoints": checkpoints,
        "navigation": handoff['navigation'], "review_targets": handoff['review_targets'],
        "current_graph": {"id": run["layout"]["id"], "label": run["layout"]["name"], "topology": run["layout"]["nodes"], "edges": run["layout"]["edges"]},
        "available_graphs": available}


def review_checkpoint(db, run_id: str, node_id: str, action: str, feedback="", revised_content="", is_human=True,
                      reviewed_run_id='', reviewed_output_version=None) -> dict:
    import workflow_execution as engine
    import project_tasks
    if not is_human:
        raise PermissionError("人工核查须由用户操作；专家可在任务中记录修改建议")
    if action not in ("approve", "approved", "reject", "rejected"):
        raise ValueError("请选择批准或退回")
    if revised_content:
        raise ValueError('请先在节点产物页保存修订，再核查保存后的具体版本')
    legacy = db.db.execute("SELECT * FROM langgraph_runs WHERE id=?", (run_id,)).fetchone()
    if legacy:
        checkpoint = db.db.execute("SELECT * FROM langgraph_checkpoints WHERE run_id=? AND node_id=? ORDER BY created_at DESC LIMIT 1", (run_id, node_id)).fetchone()
        if not checkpoint or not checkpoint["task_id"]:
            raise ValueError("旧版运行没有可核查任务，请在统一流程中准备新运行")
        project_tasks.review(db, checkpoint["task_id"], approved=action in ("approve", "approved"), feedback=feedback,
            authority=project_tasks.CommandContext.ui(legacy["project_id"]),
            reviewed_run_id=reviewed_run_id, reviewed_output_version=reviewed_output_version)
        return get_graph_state(db, legacy["project_id"], run_id)
    run = engine.detail(db, run_id)
    record = next((item for item in reversed(run["nodes"]) if item["acceptance"].get("node_id") == node_id or item["id"] == node_id), None)
    if not record:
        raise ValueError("当前节点尚未产生待核查结果")
    engine.review(db, run_id, record["id"], action in ("approve", "approved"), feedback,
                  reviewed_output_version, reviewed_run_id)
    if action in ("approve", "approved"):
        engine.kick(db.path, run_id)
    return get_graph_state(db, run["project_id"], run_id)


def list_runs(db, project_id: str) -> list:
    import workflow_execution as engine
    import workflow_layout
    workflow_layout._project(db, project_id)
    rows = db.db.execute("SELECT r.id FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id WHERE r.project_id=? ORDER BY r.rowid DESC", (project_id,)).fetchall()
    return [get_graph_state(db, project_id, row["id"])["run"] for row in rows] + [dict(row) for row in db.db.execute("SELECT * FROM langgraph_runs WHERE project_id=? ORDER BY updated_at DESC", (project_id,))]


def cancel_run(db, run_id: str) -> dict:
    import workflow_execution as engine
    run = engine.detail(db, run_id)
    engine.route("POST", "/api/workflow/execution/cancel", {"id": run_id}, {}, db)
    return get_graph_state(db, run["project_id"], run_id)


def project_create_from_idea(db, idea: str, goal: str = "", auto_start_graph: bool = True, **options) -> Dict[str, Any]:
    import research_setup
    import workflow
    raw_project = str(options.pop('project', '') or options.pop('project_id', '') or '')
    project_id = workflow._team_project(db, '', raw_project) if raw_project else ''
    return research_setup.configure(db, {"idea": idea, "goal": goal, "auto_start_graph": auto_start_graph, **options}, project_id=project_id)

# ==============================================================================
# Agent Tool Handlers (Strict Human Review Discipline)
# ==============================================================================

LANGGRAPH_TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "graph_workflow_run", "description": "启动内置科研 DAG 模板；产物进入项目任务核查。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string"},
            "graph_type": {"type": "string", "enum": ["full_research", "research_proposal", "paper_writing", "literature_study", "hybrid_research_dag"]},
            "goal": {"type": "string"}, "inputs": {"type": "array", "items": {"type": "object"}}}, "required": ["graph_type", "goal"]}}},
    {"type": "function", "function": {
        "name": "graph_workflow_status", "description": "查询项目科研流程的实际运行状态与核查点。",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}, "run_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "project_create_from_idea",
        "description": "按研究目标创建或装配项目、专家、团队、Skill 和 LangGraph 流程。project 省略时全局新建、项目对话装配当前项目；相同 request_key 幂等复用。按用户研究目标把最少候选数、正文篇幅、参考文献数量及必需字段写入节点 output_contract.file_rules，不能只写验收口号。检索节点应完成筛选与全文可得性记录，终检必须读取完整前序交付。configured/queued/awaiting_review 不代表研究完成。",
        "parameters": {"type": "object", "properties": {
            "idea": {"type": "string"}, "project": {"type": "string"}, "title": {"type": "string"},
            "goal": {"type": "string"}, "material_system": {"type": "string"},
            "graph_type": {"type": "string", "enum": ["full_research", "research_proposal", "paper_writing", "literature_study"]},
            "request_key": {"type": "string", "description": "重试保留原 key；确需另建项目才换 key"},
            "auto_start_graph": {"type": "boolean", "description": "默认 true；只配置时传 false。执行模式沿用项目设置，不修改权限"},
            "managed_artifacts": {"type": "boolean", "description": "新装配默认 true：纯文件名自动组织到本次运行的研究过程/节点及最终产物目录，显式目录路径保留。旧运行不迁移"},
            "review_mode": {"type": "string", "enum": ["human_review", "auto"], "description": "本次运行的核查方式。要求最终人工核查时显式传 human_review，节点 review_required 决定哪些阶段暂停；省略沿用当前设置"},
            "fully_automatic": {"type": "boolean", "description": "用户明确要求全流程自动完成时传 true。未指定模板时使用完整十步，产物校验通过后继续；强制人工节点仍暂停。"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "真实已安装且启用的 Skill ID"},
            "team_name": {"type": "string"}, "workflow_name": {"type": "string"},
            "experts": {"type": "array", "items": {"type": "object", "properties": {
                "role_id": {"type": "string"}, "assistant_id": {"type": "string"}, "name": {"type": "string"},
                "rules": {"type": "string"}, "description": {"type": "string"}, "model_id": {"type": "string"}}, "required": ["role_id"]}},
            "workflow": {"type": "object", "description": "可选自定义 DAG，nodes 的 executor_role_id 对应 experts.role_id", "properties": {
                "nodes": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "step": {"type": "string", "enum": ["idea", "search", "download", "notes", "outline", "map", "write", "review", "entrans", "convert", "task"]}, "label": {"type": "string"},
                    "prompt": {"type": "string"}, "executor_role_id": {"type": "string"},
                    "execution_mode": {"type": "string", "enum": ["assistant", "team"]},
                    "node_kind": {"type": "string", "enum": ["executor", "tool", "workflow"]},
                    "tool_name": {"type": "string"}, "tool_args": {"type": "object"},
                    "workflow_template_id": {"type": "string"},
                     "output_contract": {"type": "object", "description": "可执行产物约定。file_rules 按 required_files 路径设质量要求，例如候选CSV min_rows=8、required_columns=[title,doi]，综述 min_chars=2500，BibTeX min_entries=8、require_title_and_locator=true；具体阈值服从用户目标", "properties": {
                         "required_files": {"type": "array", "items": {"type": "string"}},
                         "file_rules": {"type": "object"}, "requires_evidence": {"type": "boolean"},
                         "min_chars": {"type": "integer"}, "acceptance": {"type": "array", "items": {"type": "string"}}}},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "skills": {"type": "array", "items": {"type": "string"}},
                     "review_required": {"type": "boolean"}, "manual_review_required": {"type": "boolean"}}, "required": ["id", "step", "label", "executor_role_id"]}},
                "edges": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}}}, "required": ["nodes", "edges"]}}, "required": ["idea"]}}}
]


def invoke_agent_tool(name: str, db, project_id: str, args: Dict[str, Any]) -> Any:
    if name == "graph_workflow_run":
        graph_type = str(args.get("graph_type") or "research_proposal")
        goal = str(args.get("goal") or "")
        inputs = args.get("inputs") or []
        res = start_graph(db, project_id, graph_type=graph_type, goal=goal, inputs=inputs, agent_invocation=True)
        return {
            "status": "started",
            "navigation": res.get('navigation'), "review_targets": res.get('review_targets', []),
            "run_id": res.get("run", {}).get("id"),
            "graph_type": graph_type,
            "current_node": res.get("run", {}).get("current_node"),
            "message": f"科研 LangGraph 图流已启动，正在通过项目真实任务 worker 执行并自动提交人工核查点。"
        }
    elif name == "graph_workflow_status":
        run_id = str(args.get("run_id") or "")
        state = get_graph_state(db, project_id, run_id or None)
        run = state.get("run") or {}
        return {
            "has_run": state.get("has_run"),
            "navigation": state.get('navigation'), "review_targets": state.get('review_targets', []),
            "run_id": run.get("id"),
            "status": run.get("status"),
            "current_node": run.get("current_node"),
            "checkpoints_count": len(state.get("checkpoints", [])),
            "message": f"图流状态：{run.get('status', '未启动')}，当前节点：{run.get('current_node', '无')}"
        }
    elif name == "graph_workflow_review":
        action = str(args.get("action") or "").lower()
        if action in ("approve", "approved"):
            # Enforce safety discipline
            raise PermissionError("安全纪律违规：AI 专家不能代替人类审核通过科研阶段成果，须由用户在项目工作区界面亲自审核批准。")
        run_id = str(args.get("run_id") or "")
        node_id = str(args.get("node_id") or "")
        feedback = str(args.get("feedback") or "")
        res = review_checkpoint(db, run_id, node_id, "reject", feedback=feedback, is_human=False)
        return {
            "ok": True,
            "action": "rejected_for_revision",
            "message": f"已将修改意见记录并重新调度该节点修改推演。"
        }
    elif name == "project_create_from_idea":
        idea = str(args.get("idea") or "")
        goal = str(args.get("goal") or "")
        return project_create_from_idea(db, **args)

    raise ValueError(f"未知的 LangGraph 工具：{name}")

# ==============================================================================
# HTTP Route Dispatcher
# ==============================================================================

def route(method: str, path: str, body: dict, query: dict, db) -> Any:
    if not path.startswith("/api/langgraph/"):
        return None

    action = path.removeprefix("/api/langgraph/").strip("/")
    if method == "GET":
        if action == "state":
            project_id = (query.get("project_id") or query.get("task_id") or [""])[0]
            run_id = (query.get("run_id") or [""])[0]
            if not project_id and not run_id:
                raise ValueError("请提供 project_id 或 run_id")
            if not project_id and run_id:
                row = db.db.execute("SELECT project_id FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
                project_id = row["project_id"] if row else ""
            return get_graph_state(db, project_id, run_id or None)
        elif action == "history":
            project_id = (query.get("project_id") or query.get("task_id") or [""])[0]
            return {"runs": list_runs(db, project_id)}

    elif method == "POST":
        if action == "start":
            project_id = str(body.get("project_id") or body.get("task_id") or "")
            graph_type = str(body.get("graph_type") or "research_proposal")
            goal = str(body.get("goal") or "")
            inputs = body.get("inputs") or []
            # The visible LangGraph entry point is the same trusted workflow
            # boundary used by the Agent tool. Preserve that provenance so
            # node replies can open the result workbench after completion.
            return start_graph(db, project_id, graph_type, goal, inputs, agent_invocation=True)
        elif action == "review":
            if not body.get('reviewed_run_id') or body.get('reviewed_output_version') is None:
                raise ValueError('请重新打开节点产物，并提交核查所用的尝试及修订版本')
            run_id = str(body.get("run_id") or "")
            node_id = str(body.get("node_id") or "")
            decision = str(body.get("action") or body.get("decision") or "approve")
            feedback = str(body.get("feedback") or "")
            revised_content = str(body.get("revised_content") or "")
            return review_checkpoint(db, run_id, node_id, decision, feedback, revised_content, is_human=True,
                                     reviewed_run_id=str(body['reviewed_run_id']), reviewed_output_version=body['reviewed_output_version'])
        elif action == "cancel":
            run_id = str(body.get("run_id") or "")
            return cancel_run(db, run_id)
        elif action == "create-from-idea":
            idea = str(body.get("idea") or "")
            goal = str(body.get("goal") or "")
            return project_create_from_idea(db, **body)

    return None
