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
    def __init__(self, name: str, label: str = "", executor_hint: str = "", review_required: bool = True):
        self.name = name
        self.label = label or name
        self.executor_hint = executor_hint  # assistant | team | default
        self.review_required = review_required

class GraphTemplate:
    """Built-in template metadata; execution belongs to workflow_tasks."""
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: Dict[str, str] = {}
        self.entry_point: Optional[str] = None

    def add_node(self, name: str, label: str = "", executor_hint: str = "assistant", review_required: bool = True) -> "GraphTemplate":
        self.nodes[name] = GraphNode(name, label=label, executor_hint=executor_hint, review_required=review_required)
        return self

    def add_edge(self, source: str, target: str) -> "GraphTemplate":
        self.edges[source] = target
        return self

    def set_entry_point(self, name: str) -> "GraphTemplate":
        self.entry_point = name
        return self

    def get_topology(self) -> List[Dict[str, Any]]:
        ordered = []
        curr = self.entry_point
        visited = set()
        while curr and curr not in visited:
            node = self.nodes[curr]
            ordered.append({
                "id": node.name,
                "label": node.label,
                "executor_hint": node.executor_hint,
                "review_required": node.review_required,
                "next": self.edges.get(curr)
            })
            visited.add(curr)
            curr = self.edges.get(curr)
        return ordered

# Scientific State Graphs
def create_research_proposal_graph() -> GraphTemplate:
    g = GraphTemplate("research_proposal", "研究开题与方案论证图")
    g.add_node("problem_formulation", "① 研究问题与科学假设提炼", executor_hint="assistant", review_required=True)
    g.add_node("literature_gap", "② 文献现状与研究空白剖析", executor_hint="assistant", review_required=True)
    g.add_node("methodology_design", "③ 技术路线与实验方案设计", executor_hint="team", review_required=True)
    g.add_node("proposal_synthesis", "④ 开题论证总结报告撰写", executor_hint="assistant", review_required=True)
    g.add_edge("problem_formulation", "literature_gap")
    g.add_edge("literature_gap", "methodology_design")
    g.add_edge("methodology_design", "proposal_synthesis")
    g.set_entry_point("problem_formulation")
    return g

def create_paper_writing_graph() -> GraphTemplate:
    g = GraphTemplate("paper_writing", "学术论文全流程起草与评审图")
    g.add_node("outline_design", "① 论文大纲与核心逻辑构思", executor_hint="assistant", review_required=True)
    g.add_node("section_drafting", "② 正文核心章节起草与论述", executor_hint="team", review_required=True)
    g.add_node("peer_review", "③ 模拟同行评审与学术批判", executor_hint="assistant", review_required=True)
    g.add_node("revision_polishing", "④ 针对审稿意见修订与润色", executor_hint="assistant", review_required=True)
    g.add_edge("outline_design", "section_drafting")
    g.add_edge("section_drafting", "peer_review")
    g.add_edge("peer_review", "revision_polishing")
    g.set_entry_point("outline_design")
    return g

def create_literature_study_graph() -> GraphTemplate:
    g = GraphTemplate("literature_study", "文献深度研读与综述图")
    g.add_node("literature_retrieval", "① 精准文献检索与初筛入库", executor_hint="assistant", review_required=True)
    g.add_node("deep_analysis", "② 核心方法与证据链深度提取", executor_hint="assistant", review_required=True)
    g.add_node("cross_comparison", "③ 文献横向比对与矩阵构建", executor_hint="team", review_required=True)
    g.add_node("literature_survey", "④ 前沿综述综合报告生成", executor_hint="assistant", review_required=True)
    g.add_edge("literature_retrieval", "deep_analysis")
    g.add_edge("deep_analysis", "cross_comparison")
    g.add_edge("cross_comparison", "literature_survey")
    g.set_entry_point("literature_retrieval")
    return g

GRAPH_REGISTRY: Dict[str, Callable[[], GraphTemplate]] = {
    "research_proposal": create_research_proposal_graph,
    "paper_writing": create_paper_writing_graph,
    "literature_study": create_literature_study_graph,
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
        definition = {"nodes": [{"id": node.name, "step": "task", "label": node.label,
            "prompt": "完成「" + node.label + "」，依据输入资料和前序交付，使用工具保存真实产物并标明证据来源。",
            "executor_role_id": node.name, "review_required": node.review_required,
            "executor_hint": node.executor_hint} for node in graph.nodes.values()],
            "edges": [{"source": source, "target": target} for source, target in graph.edges.items()]}
        definition=workflow_layout.validate_definition(definition)
        db.db.execute("INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)", (identifier, graph.description, graph.description,
            json.dumps(definition, ensure_ascii=False), _now_iso(), _now_iso()))
        db.db.commit()
    else:
        stored=workflow_layout._layout(db, identifier)
        if "order" not in stored:
            workflow_layout.save_layout(db, stored)
    return identifier


def start_graph(db, project_id: str, graph_type: str = "research_proposal", goal: str = "", inputs=None, request_key="") -> dict:
    """All graph entry points use the same versioned layouts and project tasks."""
    import project_bindings
    import project_workflows
    import workflow_execution as engine
    import workflow_layout
    import workflow_tasks
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
        if run["layout"]["id"] != template_id:
            raise ValueError("项目已有其他流程在执行或待核查，请先完成或停止当前运行")
        if inputs and run["settings"].get("task_inputs") != inputs:
            raise ValueError("当前运行已固定另一组输入，请先完成或停止当前运行")
        run["settings"]["graph_type"] = graph_type
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


def review_checkpoint(db, run_id: str, node_id: str, action: str, feedback="", revised_content="", is_human=True) -> dict:
    import workflow_execution as engine
    import project_tasks
    if not is_human:
        raise PermissionError("人工核查须由用户操作；专家可在任务中记录修改建议")
    if action not in ("approve", "approved", "reject", "rejected"):
        raise ValueError("请选择批准或退回")
    legacy = db.db.execute("SELECT * FROM langgraph_runs WHERE id=?", (run_id,)).fetchone()
    if legacy:
        checkpoint = db.db.execute("SELECT * FROM langgraph_checkpoints WHERE run_id=? AND node_id=? ORDER BY created_at DESC LIMIT 1", (run_id, node_id)).fetchone()
        if not checkpoint or not checkpoint["task_id"]:
            raise ValueError("旧版运行没有可核查任务，请在统一流程中准备新运行")
        project_tasks.review(db, checkpoint["task_id"], approved=action in ("approve", "approved"), feedback=feedback,
            authority=project_tasks.CommandContext.ui(legacy["project_id"]))
        return get_graph_state(db, legacy["project_id"], run_id)
    run = engine.detail(db, run_id)
    record = next((item for item in reversed(run["nodes"]) if item["acceptance"].get("node_id") == node_id or item["id"] == node_id), None)
    if not record:
        raise ValueError("当前节点尚未产生待核查结果")
    engine.review(db, run_id, record["id"], action in ("approve", "approved"), feedback)
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
        "name": "graph_workflow_run", "description": "启动内置科研模板；使用项目唯一 LangGraph 引擎，产物进入项目任务核查。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string"},
            "graph_type": {"type": "string", "enum": ["research_proposal", "paper_writing", "literature_study"]},
            "goal": {"type": "string"}, "inputs": {"type": "array", "items": {"type": "object"}}}, "required": ["graph_type", "goal"]}}},
    {"type": "function", "function": {
        "name": "graph_workflow_status", "description": "查询项目科研流程的实际运行状态与核查点。",
        "parameters": {"type": "object", "properties": {"project": {"type": "string"}, "run_id": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "project_create_from_idea",
        "description": "按研究目标创建或装配项目、专业专家、团队、Skill 和 LangGraph 流程，可自动启动。project 省略时全局对话新建、项目对话装配当前项目；相同 request_key 幂等复用。先根据可用 Skill 和真实专家 ID 拟定配置。返回 configured/queued/awaiting_review 均不代表研究完成。",
        "parameters": {"type": "object", "properties": {
            "idea": {"type": "string"}, "project": {"type": "string"}, "title": {"type": "string"},
            "goal": {"type": "string"}, "material_system": {"type": "string"},
            "graph_type": {"type": "string", "enum": ["research_proposal", "paper_writing", "literature_study"]},
            "request_key": {"type": "string", "description": "重试保留原 key；确需另建项目才换 key"},
            "auto_start_graph": {"type": "boolean", "description": "默认 true；只配置时传 false。执行模式沿用项目设置，不修改权限"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "真实已安装且启用的 Skill ID"},
            "team_name": {"type": "string"}, "workflow_name": {"type": "string"},
            "experts": {"type": "array", "items": {"type": "object", "properties": {
                "role_id": {"type": "string"}, "assistant_id": {"type": "string"}, "name": {"type": "string"},
                "rules": {"type": "string"}, "description": {"type": "string"}, "model_id": {"type": "string"}}, "required": ["role_id"]}},
            "workflow": {"type": "object", "description": "可选自定义 DAG，nodes 的 executor_role_id 对应 experts.role_id", "properties": {
                "nodes": {"type": "array", "items": {"type": "object", "properties": {
                    "id": {"type": "string"}, "step": {"type": "string", "enum": ["task"]}, "label": {"type": "string"},
                    "prompt": {"type": "string"}, "executor_role_id": {"type": "string"}, "review_required": {"type": "boolean"}}, "required": ["id", "step", "label", "executor_role_id"]}},
                "edges": {"type": "array", "items": {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}, "required": ["source", "target"]}}}, "required": ["nodes", "edges"]}}, "required": ["idea"]}}}
]


def invoke_agent_tool(name: str, db, project_id: str, args: Dict[str, Any]) -> Any:
    if name == "graph_workflow_run":
        graph_type = str(args.get("graph_type") or "research_proposal")
        goal = str(args.get("goal") or "")
        inputs = args.get("inputs") or []
        res = start_graph(db, project_id, graph_type=graph_type, goal=goal, inputs=inputs)
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
            return start_graph(db, project_id, graph_type, goal, inputs)
        elif action == "review":
            run_id = str(body.get("run_id") or "")
            node_id = str(body.get("node_id") or "")
            decision = str(body.get("action") or body.get("decision") or "approve")
            feedback = str(body.get("feedback") or "")
            revised_content = str(body.get("revised_content") or "")
            return review_checkpoint(db, run_id, node_id, decision, feedback, revised_content, is_human=True)
        elif action == "cancel":
            run_id = str(body.get("run_id") or "")
            return cancel_run(db, run_id)
        elif action == "create-from-idea":
            idea = str(body.get("idea") or "")
            goal = str(body.get("goal") or "")
            return project_create_from_idea(db, **body)

    return None
