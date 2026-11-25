# -*- coding: utf-8 -*-
"""EvoFlow SDK 自动化工作流编排与控制平面模块 (evoflow_sdk).

将 EvovexAI/EvoFlow 的核心编排原语内置到览籍智研工作区：
1. `plan(goal, steps[])`：按 depends_on 拆解串并混合 DAG、自动映射执行角色并落库启动；
2. `supervisor(action, ...)`：按 DAG 依赖波次并发调度子任务、汇总进度与审查状态；
3. `platform(action, args, confirm)`：统一控制平面总线（workflow.* / tasks.* / employees.* / ui.panel）。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

import project_bindings
import project_tasks
import task_tools
import workflow_execution
import workflow_layout
import workflow_node_workbench
import workflow_tasks
from workflow_db import STAGES, STAGE_LABEL

# EvoFlow 预设角色到览籍智研步骤/内置专家的智能映射
ROLE_STAGE_MAP: dict[str, tuple[str, str]] = {
    "project-architect": ("idea", "builtin-idea"),
    "project-planner": ("outline", "builtin-outline"),
    "project-implementer": ("write", "builtin-write"),
    "project-reviewer": ("review", "builtin-review"),
    "project-debugger": ("notes", "builtin-notes"),
    "project-qa": ("review", "builtin-review"),
    "search-worker": ("search", "builtin-search"),
    "knowledge-retriever": ("search", "builtin-search"),
    "knowledge-curator": ("map", "builtin-map"),
    "file-worker": ("notes", "builtin-notes"),
    "code-agent": ("write", "builtin-write"),
    "agent": ("task", "builtin-steward"),
    "general-purpose": ("task", "builtin-steward"),
}

PLATFORM_CATALOG: dict[str, list[dict[str, Any]]] = {
    "workflow": [
        {"action": "workflow.list", "risk": "read", "description": "列出可用 DAG 工作流模板与当前项目绑定流程"},
        {"action": "workflow.get", "risk": "read", "description": "读取指定工作流 DAG 拓扑（nodes / edges / order）"},
        {"action": "workflow.schema", "risk": "read", "description": "读取 EvoFlow DAG 步骤与依赖定义 schema"},
        {"action": "workflow.generate", "risk": "read", "description": "预览传入步骤的 DAG；未指定步骤时预览六节点示例模板"},
        {"action": "workflow.create", "risk": "write", "description": "按 steps(depends_on) 或 nodes/edges 创建并绑定项目 DAG 工作流"},
        {"action": "workflow.run", "risk": "write", "description": "启动或重新运行指定 DAG 工作流，返回 runId 与节点任务"},
        {"action": "workflow.status", "risk": "read", "description": "查询工作流运行进度百分比、各层节点状态与待审查节点"},
        {"action": "workflow.stop", "risk": "write", "description": "停止正在运行的工作流"},
        {"action": "workflow.resume", "risk": "write", "description": "从可恢复的失败或中断检查点继续执行"},
    ],
    "tasks": [
        {"action": "tasks.list", "risk": "read", "description": "列出项目协作任务台账（含工作流节点子任务）"},
        {"action": "tasks.get", "risk": "read", "description": "读取单个节点任务详情、产物列表与审查状态"},
        {"action": "tasks.progress", "risk": "read", "description": "查询任务进度（0-100%）"},
        {"action": "tasks.execution_trail", "risk": "read", "description": "读取节点内部执行与工具调用轨迹"},
    ],
    "employees": [
        {"action": "employees.list", "risk": "read", "description": "列出当前项目已绑定及全局可用的智能体专家与团队"},
        {"action": "employees.dispatch", "risk": "write", "description": "按 executor_binding_id 和 goal 创建并启动项目任务"},
        {"action": "employees.wake", "risk": "read", "description": "打开右侧专家对话面板"},
    ],
    "ui": [
        {"action": "ui.panel", "risk": "read", "description": "控制右侧多标签 EvoFlow 侧边栏（openRightStage 打开工作流/节点审查/节点专家）"},
    ],
}


def _clean_id(raw: Any, index: int) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip()).strip("_.")
    if not text or not re.match(r"^[A-Za-z0-9]", text):
        return f"step_{index + 1}"
    return text[:64]


EVOFLOW_PRESETS: dict[str, dict[str, Any]] = {
    "literature_review": {
        "id": "builtin.evoflow.literature_review",
        "name": "文献综述五节点协同工作流",
        "description": "标准五节点串并混合文献综述工作流：文献检索筛选 -> 机制抽取与方法学对比（并行）-> 综合评述 -> 规范性终审",
        "category": "literature_review",
        "steps": [
            {
                "id": "s1_lit_search",
                "name": "Step 1: S1 文献检索与候选池构建",
                "step": "search",
                "assigned_agent": "search-worker",
                "goal": "围绕「{{topic}}」检索核心高水平文献、预印本与专著，根据相关性与证据质量筛选，构建文献候选池与清单（01_candidate_pool.csv）。",
                "outputs": ["outputs/wf_review/01_candidate_pool.csv"],
                "depends_on": [],
                "review_required": True,
            },
            {
                "id": "p1_evidence",
                "name": "Step 2.1: P1 核心机制与证据抽取",
                "step": "task",
                "assigned_agent": "knowledge-retriever",
                "goal": "基于文献候选池，并行抽取「{{topic}}」核心实验结论、定量证据、机制模型与关键数据对比表（02_extracted_evidence.md）。",
                "inputs": ["outputs/wf_review/01_candidate_pool.csv"],
                "outputs": ["outputs/wf_review/02_extracted_evidence.md"],
                "output_contract": {"requires_evidence": True},
                "depends_on": ["s1_lit_search"],
                "review_required": True,
            },
            {
                "id": "p2_methodology",
                "name": "Step 2.2: P2 方法学与技术路线对比",
                "step": "task",
                "assigned_agent": "project-planner",
                "goal": "基于文献候选池，并行梳理「{{topic}}」主流技术路线演进、方法学优劣势及实验范式对比（03_method_comparison.md）。",
                "inputs": ["outputs/wf_review/01_candidate_pool.csv"],
                "outputs": ["outputs/wf_review/03_method_comparison.md"],
                "depends_on": ["s1_lit_search"],
                "review_required": True,
            },
            {
                "id": "j1_synthesis",
                "name": "Step 3: J1 综合评述与争议收敛",
                "step": "write",
                "assigned_agent": "project-implementer",
                "goal": "汇聚机制证据与方法对比成果，深度综合撰写「{{topic}}」文献综述核心评述稿，分析关键争议与发展趋势（04_synthesis_review.md）。",
                "inputs": ["outputs/wf_review/02_extracted_evidence.md", "outputs/wf_review/03_method_comparison.md"],
                "outputs": ["outputs/wf_review/04_synthesis_review.md"],
                "depends_on": ["p1_evidence", "p2_methodology"],
                "review_required": True,
            },
            {
                "id": "s2_review",
                "name": "Step 4: S2 规范性审查与结论自证",
                "step": "review",
                "assigned_agent": "project-qa",
                "goal": "对「{{topic}}」文献综述初稿进行学术规范性核查、论据链条验证与局限性自证（05_normative_review.md），交付人工最终审查。",
                "inputs": ["outputs/wf_review/04_synthesis_review.md"],
                "outputs": ["outputs/wf_review/05_normative_review.md"],
                "depends_on": ["j1_synthesis"],
                "review_required": True,
                "manual_review_required": True,
            },
        ],
    },
    "default_research": {
        "id": "builtin.evoflow.default_research",
        "name": "EvoFlow 六节点科研协同工作流",
        "description": "标准六节点串并混合科研协同工作流：种子材料 -> 概览、步骤、风险（并行）-> 汇编总稿 -> 终检审查",
        "category": "research",
        "steps": [
            {
                "id": "s1_seed",
                "name": "Step 1: S1 建目录与素材",
                "step": "task",
                "assigned_agent": "project-architect",
                "goal": "围绕「{{topic}}」建立产出目录结构、核心研究问题与基础种子素材（00_seed.md）。",
                "outputs": ["outputs/wfW5/00_seed.md"],
                "depends_on": [],
                "review_required": True,
            },
            {
                "id": "p1_overview",
                "name": "Step 2.1: P1 概览文档",
                "step": "task",
                "assigned_agent": "search-worker",
                "goal": "基于上游种子素材，并行梳理「{{topic}}」领域研究现状与文献概览文档（01_overview.md）。",
                "inputs": ["outputs/wfW5/00_seed.md"],
                "outputs": ["outputs/wfW5/01_overview.md"],
                "depends_on": ["s1_seed"],
                "review_required": True,
            },
            {
                "id": "p2_steps",
                "name": "Step 2.2: P2 步骤文档",
                "step": "task",
                "assigned_agent": "project-planner",
                "goal": "基于上游种子素材，并行设计「{{topic}}」技术路线、实验/实现步骤与方法学文档（02_steps.md）。",
                "inputs": ["outputs/wfW5/00_seed.md"],
                "outputs": ["outputs/wfW5/02_steps.md"],
                "depends_on": ["s1_seed"],
                "review_required": True,
            },
            {
                "id": "p3_risk",
                "name": "Step 2.3: P3 风险文档",
                "step": "task",
                "assigned_agent": "knowledge-curator",
                "goal": "基于上游种子素材，并行评估「{{topic}}」潜在难点、边界条件与风险应对矩阵（03_risks.md）。",
                "inputs": ["outputs/wfW5/00_seed.md"],
                "outputs": ["outputs/wfW5/03_risks.md"],
                "depends_on": ["s1_seed"],
                "review_required": True,
            },
            {
                "id": "j1_merge",
                "name": "Step 3: J1 汇合汇编",
                "step": "task",
                "assigned_agent": "project-implementer",
                "goal": "汇聚 P1 概览、P2 步骤与 P3 风险三份并行产出，交叉对齐证据并撰写完整汇编总稿（04_merge.md）。",
                "inputs": ["outputs/wfW5/01_overview.md", "outputs/wfW5/02_steps.md", "outputs/wfW5/03_risks.md"],
                "outputs": ["outputs/wfW5/04_merge.md"],
                "depends_on": ["p1_overview", "p2_steps", "p3_risk"],
                "review_required": True,
            },
            {
                "id": "s2_check",
                "name": "Step 4: S2 终检自证",
                "step": "task",
                "assigned_agent": "project-qa",
                "goal": "对汇编总稿及全链路产物进行完整性、一致性与可复现性终检自证（05_check.md），交付人工最终审查。",
                "inputs": ["outputs/wfW5/04_merge.md"],
                "outputs": ["outputs/wfW5/05_check.md"],
                "depends_on": ["j1_merge"],
                "review_required": True,
                "manual_review_required": True,
            },
        ],
    },
}


def ensure_presets(db: Any) -> None:
    """确保 EvoFlow 标准预设固化至 workflow_layouts 模板库中，供跨项目统一检索与复用."""
    if not hasattr(db, "db"):
        return
    workflow_layout.ensure_schema(db)
    import workflow_template_catalog
    if not db.db.execute('SELECT 1 FROM workflow_layouts LIMIT 1').fetchone():
        return
    for key, preset in EVOFLOW_PRESETS.items():
        pid = preset["id"]
        row = db.db.execute("SELECT definition FROM workflow_layouts WHERE id=?", (pid,)).fetchone()
        if not row:
            if workflow_template_catalog.deliberately_deleted(db, pid):
                continue
            definition = compile_plan_to_dag(goal=preset["description"], steps=preset["steps"])
            stamp = workflow_layout.now()
            db.db.execute(
                "INSERT INTO workflow_layouts VALUES (?,?,?,?,?,?)",
                (pid, preset["name"], preset["description"], json.dumps(definition, ensure_ascii=False), stamp, stamp),
            )
        else:
            definition = json.loads(row["definition"])
            defaults = {node["id"]: node for node in preset["steps"]}
            changed = False
            for node in definition.get("nodes", []):
                contract = dict(node.get("output_contract") or {})
                required = workflow_layout.required_outputs(node)
                if required and not contract.get("required_files"):
                    contract["required_files"] = required
                original = defaults.get(node.get("id"), {})
                if set(required) == set(original.get("outputs") or []):
                    for flag, value in original.get("output_contract", {}).items():
                        contract.setdefault(flag, value)
                if contract != (node.get("output_contract") or {}):
                    node["output_contract"] = contract
                    changed = True
            if changed:
                db.db.execute("UPDATE workflow_layouts SET definition=?,updated_at=? WHERE id=?",
                              (json.dumps(definition, ensure_ascii=False), workflow_layout.now(), pid))
    db.db.commit()


def _extract_topic(goal: str) -> str:
    raw = (goal or "").strip().splitlines()[0] if goal else ""
    m = re.search(r"围绕「([^」]+)」", raw)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"「([^」]+)」", raw)
    if m2:
        return m2.group(1).strip()
    clean = re.sub(r"^(帮我|请|进行|开展|关于)\s*", "", raw)
    return clean[:36].strip() or "科研课题"


def auto_decompose_goal(goal: str) -> list[dict[str, Any]]:
    """当未显式传入 steps 时，根据科研/文档目标自动拆解为 EvoFlow 标准串并混合 DAG."""
    topic = _extract_topic(goal)
    lower = goal.lower() if goal else ""
    if any(k in lower or k in (goal or "") for k in ("综述", "文献", "调研", "review", "survey", "literature")):
        raw_steps = EVOFLOW_PRESETS["literature_review"]["steps"]
    else:
        raw_steps = EVOFLOW_PRESETS["default_research"]["steps"]
    steps = []
    for step in raw_steps:
        s = dict(step)
        if "goal" in s:
            s["goal"] = s["goal"].replace("{{topic}}", topic).replace("{{goal}}", goal)
        steps.append(s)
    return steps


def _assign_hierarchical_step_labels(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """根据拓扑依赖，为节点统一对齐主子序号 (如 Step 1, Step 2.1, Step 2.2, Step 3, Step 4)."""
    if not nodes:
        return
    in_deg: dict[str, int] = {n["id"]: 0 for n in nodes}
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        src, dst = e.get("source"), e.get("target")
        if src in adj and dst in in_deg:
            in_deg[dst] += 1
            adj[src].append(dst)

    depth: dict[str, int] = {}
    queue = [nid for nid, deg in in_deg.items() if deg == 0]
    for nid in queue:
        depth[nid] = 0

    rem_in_deg = dict(in_deg)
    while queue:
        u = queue.pop(0)
        d = depth.get(u, 0)
        for v in adj.get(u, []):
            depth[v] = max(depth.get(v, 0), d + 1)
            rem_in_deg[v] -= 1
            if rem_in_deg[v] == 0:
                queue.append(v)

    for idx, n in enumerate(nodes):
        if n["id"] not in depth:
            depth[n["id"]] = idx

    level_map: dict[int, list[dict[str, Any]]] = {}
    for n in nodes:
        lvl = depth.get(n["id"], 0)
        level_map.setdefault(lvl, []).append(n)

    sorted_levels = sorted(level_map.keys())
    for major_idx, lvl in enumerate(sorted_levels):
        group = level_map[lvl]
        major_no = major_idx + 1
        is_parallel = len(group) > 1
        for sub_idx, n in enumerate(group):
            minor_no = sub_idx + 1
            step_num = f"{major_no}.{minor_no}" if is_parallel else f"{major_no}"
            step_prefix = f"Step {step_num}"
            raw_label = str(n.get("label") or "").strip()
            clean = re.sub(r"^Step\s*\d+(\.\d+)?\s*[:：]\s*", "", raw_label, flags=re.IGNORECASE).strip()
            n["hierarchical_step"] = step_num
            n["label"] = f"{step_prefix}: {clean}"


def _find_custom_workflow(db: Any, project_id: str, query: str) -> dict[str, Any] | None:
    """根据 ID、名称或意图在已保存的工作流模板及内置预设中查找匹配的工作流."""
    if not query:
        return None
    ensure_presets(db)
    q = str(query).strip().lower()
    workflow_layout.ensure_schema(db)
    rows = db.db.execute("SELECT * FROM workflow_layouts ORDER BY created_at,id").fetchall()
    layouts = [workflow_layout._public(row) for row in rows]
    for item in layouts:
        if str(item.get("id", "")).strip().lower() == q:
            return item
    for key, preset in EVOFLOW_PRESETS.items():
        if key.lower() == q or preset["id"].lower() == q:
            for item in layouts:
                if item.get("id") == preset["id"]:
                    return item
    for item in layouts:
        if str(item.get("name", "")).strip().lower() == q:
            return item
    for item in layouts:
        name = str(item.get("name", "")).strip().lower()
        if name and (name in q or q in name):
            return item
    if any(k in q for k in ("综述", "review", "survey")):
        for item in layouts:
            if item.get("id") == EVOFLOW_PRESETS["literature_review"]["id"]:
                return item
        for item in layouts:
            name = str(item.get("name", "")).strip().lower()
            if not str(item.get("id", "")).startswith("builtin.langgraph.") and any(k in name for k in ("综述", "review", "survey")):
                return item
    if any(k in q for k in ("精读", "reading")):
        for item in layouts:
            name = str(item.get("name", "")).strip().lower()
            if any(k in name for k in ("精读", "reading")):
                return item
    if any(k in q for k in ("写作", "writing")):
        for item in layouts:
            name = str(item.get("name", "")).strip().lower()
            if any(k in name for k in ("写作", "writing")):
                return item
    return None


def compile_plan_to_dag(goal: str, steps: list[dict[str, Any]] | None = None,
                        nodes: list[dict[str, Any]] | None = None,
                        edges: list[dict[str, Any]] | None = None,
                        *, template_desc: str = "") -> dict[str, Any]:
    """将 EvoFlow `steps(depends_on)` 或 `nodes/edges` 编译为经过拓扑排序校验的标准 DAG 定义."""
    for value in (steps, nodes, edges):
        if value is not None and not isinstance(value, list):
            raise ValueError("步骤、节点和连线必须用数组表示")
    raw_steps = nodes or steps or auto_decompose_goal(goal)

    topic = _extract_topic(goal) if goal else ""
    old_topic = _extract_topic(template_desc) if template_desc else ""

    # 建立别名映射（支持 depends_on 写 "1", "Step 1", 节点 id 或节点名称）
    alias_to_id: dict[str, str] = {}
    normalized_nodes: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_steps):
        if isinstance(item, str):
            item = {"id": f"step_{idx + 1}", "name": item, "step": item if item in STAGES else "task"}
        if not isinstance(item, dict):
            raise ValueError("每个步骤必须是对象或名称")
        nid = _clean_id(item.get("id") or item.get("step_id") or f"step_{idx + 1}", idx)
        if any(existing["id"] == nid for existing in normalized_nodes):
            raise ValueError("工作流步骤 ID 重复：" + nid)
        alias_to_id[str(idx + 1)] = nid
        alias_to_id[f"step_{idx + 1}"] = nid
        alias_to_id[f"Step {idx + 1}"] = nid
        if item.get("id"):
            alias_to_id[str(item["id"]).strip()] = nid
        if item.get("name"):
            alias_to_id[str(item["name"]).strip()] = nid
            m = re.search(r"Step\s*(\d+(?:\.\d+)?)", str(item["name"]), re.IGNORECASE)
            if m:
                alias_to_id[m.group(0)] = nid
                alias_to_id[m.group(1)] = nid
        if item.get("label"):
            alias_to_id[str(item["label"]).strip()] = nid

        agent_code = str(item.get("assigned_agent") or item.get("assistant_id") or item.get("role") or "").strip()
        mapped_stage, _ = ROLE_STAGE_MAP.get(agent_code, ("", ""))
        raw_step = str(item.get("step") or item.get("stage") or mapped_stage or "task").strip()
        step_key = raw_step if raw_step in (*STAGES, "task") else (mapped_stage or "task")
        raw_label = str(item.get("name") or item.get("label") or STAGE_LABEL.get(step_key, f"Step {idx + 1}")).strip()
        if topic:
            raw_label = raw_label.replace("{{topic}}", topic).replace("{{goal}}", goal)
        label = raw_label[:120]

        raw_prompt = str(item.get("goal") or item.get("prompt") or "").strip()
        if topic:
            raw_prompt = raw_prompt.replace("{{topic}}", topic).replace("{{goal}}", goal)
            if old_topic and old_topic != topic:
                raw_prompt = raw_prompt.replace(old_topic, topic)
            if "围绕「" in raw_prompt and "」" in raw_prompt:
                raw_prompt = re.sub(r"围绕「[^」]+」", f"围绕「{topic}」", raw_prompt)
            if "抽取「" in raw_prompt and "」" in raw_prompt:
                raw_prompt = re.sub(r"抽取「[^」]+」", f"抽取「{topic}」", raw_prompt)
            if "梳理「" in raw_prompt and "」" in raw_prompt:
                raw_prompt = re.sub(r"梳理「[^」]+」", f"梳理「{topic}」", raw_prompt)
            if "撰写「" in raw_prompt and "」" in raw_prompt:
                raw_prompt = re.sub(r"撰写「[^」]+」", f"撰写「{topic}」", raw_prompt)
            if "对「" in raw_prompt and "」" in raw_prompt:
                raw_prompt = re.sub(r"对「[^」]+」", f"对「{topic}」", raw_prompt)

        prompt_parts = [raw_prompt]
        if item.get("acceptance"):
            prompt_parts.append(f"验收标准：{item['acceptance']}")
        if isinstance(item.get("outputs"), list) and item["outputs"]:
            prompt_parts.append("目标产出文件：" + "、".join(str(x) for x in item["outputs"]))
        prompt = "\n".join(p for p in prompt_parts if p).strip()

        output_contract: dict[str, Any] = dict(item.get("output_contract") or {})
        required_files = workflow_layout.required_outputs({**item, "output_contract": output_contract})
        if required_files:
            output_contract["required_files"] = required_files

        merged_tools = workflow_layout.normalize_agent_tools({**item, "step": step_key, "output_contract": output_contract})
        if merged_tools is not None:
            item = {**item, "tools": merged_tools}

        node_def: dict[str, Any] = {
            **{key: item[key] for key in ('manual_execution', 'claim_audit', 'node_kind', 'tool_name', 'tool_args',
                'workflow_template_id', 'execution_mode', 'team_id', 'member_id', 'tools', 'skills') if key in item},
            "id": nid,
            "step": step_key,
            "label": label,
            "prompt": prompt,
            "review_required": item.get("review_required", True),
            "manual_review_required": item.get("manual_review_required", False),
            "executor_role_id": _clean_id(item.get("executor_role_id") or agent_code or nid, idx),
            "output_contract": output_contract,
        }
        if item.get("executor_binding_id"):
            node_def["executor_binding_id"] = str(item["executor_binding_id"]).strip()
        if item.get("assistant_id") and not str(item["assistant_id"]).startswith("project-"):
            node_def["assistant_id"] = str(item["assistant_id"]).strip()
        normalized_nodes.append(node_def)

    compiled_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    ids = {node['id'] for node in normalized_nodes}

    def node_id(value):
        text = str(value or '').strip()
        return text if text in ids else alias_to_id.get(text, text)

    # 若调用方未提供任何 depends_on 且未提供 edges，默认串联
    has_any_dep = any(isinstance(item, dict) and item.get("depends_on") is not None for item in raw_steps) or edges is not None
    for idx, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        target_id = normalized_nodes[idx]["id"]
        deps = item.get("depends_on")
        if deps is not None and not isinstance(deps, list):
            raise ValueError("depends_on 必须是步骤 ID 数组")
        if deps is None and not has_any_dep and idx > 0:
            deps = [normalized_nodes[idx - 1]["id"]]
        for raw_dep in (deps or []):
            source_id = node_id(raw_dep)
            if source_id == target_id or source_id not in ids:
                raise ValueError("步骤依赖必须引用另一个存在的节点：" + str(raw_dep))
            pair = (source_id, target_id)
            if pair not in seen_edges:
                seen_edges.add(pair)
                edge_obj: dict[str, Any] = {"source": source_id, "target": target_id}
                if item.get("edge_artifact"):
                    art = str(item["edge_artifact"]).replace("\\", "/").strip("/")
                    if art and len(art) <= 255:
                        edge_obj["artifact"] = art
                compiled_edges.append(edge_obj)

    if edges and isinstance(edges, list):
        for e in edges:
            if not isinstance(e, dict):
                raise ValueError("每条连线必须是对象")
            src = node_id(e.get("source"))
            dst = node_id(e.get("target"))
            if not src or not dst or src == dst:
                raise ValueError("连线必须连接两个不同的节点")
            if (src, dst) not in seen_edges:
                seen_edges.add((src, dst))
                edge_obj = {"source": src, "target": dst}
                if e.get("artifact"):
                    edge_obj["artifact"] = str(e["artifact"]).replace("\\", "/").strip("/")
                if e.get("argument"):
                    edge_obj["argument"] = e["argument"]
                compiled_edges.append(edge_obj)

    validated = workflow_layout.validate_definition({
        "nodes": normalized_nodes,
        "edges": compiled_edges,
    })
    _assign_hierarchical_step_labels(validated["nodes"], validated["edges"])
    return validated


def evoflow_plan(db: Any, project_id: str, args: dict[str, Any], *, save: bool = False) -> dict[str, Any]:
    workflow_layout._project(db, project_id)
    goal = str(args.get("goal") or args.get("description") or args.get("name") or "EvoFlow 串并混合工作流").strip()
    name = str(args.get("name") or args.get("title") or goal[:32] or "EvoFlow 自动编排工作流").strip()

    # 优先支持自定义工作流调用 (通过 template_id / layout_id / appId / workflow_name 或从 goal 识别)
    custom_wf = None
    template_req = str(args.get("template_id") or args.get("layout_id") or args.get("appId") or args.get("workflow_name") or "").strip()
    if template_req:
        custom_wf = _find_custom_workflow(db, project_id, template_req)
    elif not args.get("steps") and not args.get("nodes"):
        if any(k in goal.lower() for k in ("综述", "文献", "调研", "review", "survey", "协同", "六节点")):
            custom_wf = _find_custom_workflow(db, project_id, goal)

    save_as_template = args.get("save_as_template") is True or args.get("save_template") is True

    if custom_wf:
        name = str(args.get("name") or custom_wf.get("name") or name).strip()
        user_goal = str(args.get("goal") or "").strip()
        goal = user_goal or custom_wf.get("description") or goal
        definition = compile_plan_to_dag(
            goal=goal,
            nodes=[dict(n) for n in custom_wf.get("nodes", [])],
            edges=[dict(e) for e in custom_wf.get("edges", [])],
            template_desc=custom_wf.get("description") or "",
        )
        plan_source = "custom_workflow"
    else:
        definition = compile_plan_to_dag(
            goal=goal,
            steps=args.get("steps"),
            nodes=args.get("nodes"),
            edges=args.get("edges"),
        )
        plan_source = "steps" if args.get("steps") or args.get("nodes") else "example_template"

    auto_start = args.get("auto_start") is True
    layout = {"name": name, "description": goal, **definition}

    # 如果要求另存为全局模板，写入 workflow_layouts 表
    saved_template_id = (custom_wf or {}).get("id")
    if save_as_template:
        assigned_tpl_id = str(args.get("template_id") or f"tpl_{uuid.uuid4().hex[:12]}").strip()
        tpl_layout = {"id": assigned_tpl_id, "name": name, "description": goal, **definition}
        workflow_layout.ensure_schema(db)
        saved_template_id = workflow_layout.save_layout(db, tpl_layout)['layout']['id']

    result: dict[str, Any] = {
        "ok": True,
        "sdk": "evoflow",
        "project_id": project_id,
        "layout": layout,
        "status": "preview",
        "plan_source": plan_source,
        "template_id": saved_template_id,
        "plan": {
            "name": name,
            "goal": goal,
            "total_steps": len(layout["nodes"]),
            "nodes": layout["nodes"],
            "edges": layout["edges"],
            "order": layout["order"],
        },
    }
    if not (save or auto_start):
        return result
    if getattr(db, "project_task_context", None) and db.project_task_context.parent_task_id:
        raise PermissionError("节点任务不能自行保存或启动其他工作流")
    with workflow_execution._lock:
        if auto_start and workflow_execution.active(db, project_id):
            raise ValueError("项目已有未完成的工作流，请先完成或在工作区停止该运行")
        workflow_layout.ensure_schema(db)
        layout = workflow_layout.save_layout(db, layout)["layout"]
        layout_id = layout["id"]
        if not workflow_execution.active(db, project_id):
            workflow_layout.select_layout(db, {"task_id": project_id, "layout_id": layout_id})
        workflow_tasks.auto_bind_layout_roles(db, project_id, layout)
        result.update(layout=layout, status="configured", navigation={
            "tab": "workspace",
            "project_id": project_id,
            "section": "workflow",
            "dock_tab": "workflow",
            "open_right_stage": True,
        })
        if auto_start:
            run = workflow_tasks.prepare(db, project_id, goal, template_id=layout_id, inputs=args.get("inputs"))
            workflow_node_workbench.mark_agent_started(db, run)
            run["settings"]["prepared"] = False
            workflow_tasks._save(db, run)
            if args.get("_kick", True) is not False:
                workflow_execution.kick(db.path, run["id"])
            wf_res = task_tools.workflow_result(db, workflow_execution.detail(db, run["id"]))
            navigation = {**result["navigation"], **(wf_res.get("navigation") or {}),
                          "dock_tab": "workflow", "open_right_stage": True}
            result.update(wf_res, navigation=navigation)
    return result


def _get_run(db: Any, project_id: str, run_id: str = "") -> dict[str, Any] | None:
    workflow_layout._project(db, project_id)
    if run_id and not db.db.execute("SELECT 1 FROM workflow_runs WHERE id=? AND project_id=?", (run_id, project_id)).fetchone():
        raise ValueError("工作流运行不属于当前项目")
    return workflow_execution.detail(db, run_id) if run_id else workflow_execution.latest(db, project_id)


def run_summary(db: Any, project_id: str, run_id: str = "") -> dict[str, Any]:
    """计算 EvoFlow 右侧边栏所需的实时运行摘要（进度百分比、已完成/总数、各层节点状态与产物芯片）."""
    run = _get_run(db, project_id, run_id)
    if not run:
        return {"ok": True, "has_run": False, "project_id": project_id, "progress_pct": 0, "done_count": 0, "total_count": 0, "nodes": []}
    pid = run.get("task_id") or run.get("project_id") or project_id
    tasks = project_tasks.list_tasks(db, pid, workflow_run_id=run["id"])
    task_by_id = {t["id"]: t for t in tasks}
    task_Ids_map = (run.get("settings") or {}).get("task_ids") or {}
    layout = run.get("layout") or {}
    nodes_out = []
    done_count = 0
    running_count = 0
    review_count = 0
    for idx, node in enumerate(layout.get("nodes") or []):
        tid = task_Ids_map.get(node["id"], "")
        t = task_by_id.get(tid) or next((item for item in tasks if item.get("workflow_node_id") == node["id"]), None)
        st = (t or {}).get("status") or "pending"
        if st == 'delivered' and (t or {}).get('accepted_delivery_id'):
            done_count += 1
            pct = 100
        elif st == "awaiting_review":
            review_count += 1
            pct = 100
        elif st in ("running", "queued"):
            running_count += 1
            pct = None
        else:
            pct = 0
        outputs = (t or {}).get("outputs") or []
        reply = (((t or {}).get("latest_run") or {}).get("checkpoint") or {}).get("reply") or ""
        nodes_out.append({
            "index": idx + 1,
            "id": node["id"],
            "task_id": (t or {}).get("id", ""),
            "task_run_id": (t or {}).get('latest_run_id', ''),
            "recovery": (t or {}).get('recovery'),
            "label": node.get("label") or node["id"],
            "step": node.get("step") or "task",
            "status": st,
            "progress_pct": pct,
            "summary": reply.strip().splitlines()[0][:80] if reply.strip() else (outputs[0].get("name", "") if outputs else ""),
            "outputs": outputs,
            "review_required": node.get("review_required", True),
            "node_result_available": bool(((t or {}).get("node_result") or {}).get("available")),
        })
    total = max(1, len(layout.get("nodes") or []))
    overall_pct = round((done_count / total) * 100)
    if run.get("status") in ("succeeded", "completed") or done_count == total:
        overall_pct = 100
    return {
        "ok": True,
        "has_run": True,
        "project_id": pid,
        "run_id": run["id"],
        "short_run_id": run["id"][:12],
        "name": layout.get("name") or "工作流",
        "status": run.get("status") or "running",
        "progress_pct": min(100, overall_pct),
        "done_count": done_count,
        "review_count": review_count,
        "running_count": running_count,
        "total_count": len(layout.get("nodes") or []),
        "nodes": nodes_out,
        "edges": layout.get("edges") or [],
    }


def evoflow_supervisor(db: Any, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """EvoFlow `supervisor` 工具：管理 DAG 波次执行、状态查询、节点审查放行与返工."""
    action = str(args.get("action") or "get_status").strip()
    run_id = str(args.get("run_id") or args.get("runId") or args.get("workflow_run_id") or "").strip()
    if action == "get_status":
        owner = (getattr(db, 'usage_context', {}) or {}).get('owner_id', '')
        if owner and not run_id:
            import workflow_conversation_context
            owned = workflow_conversation_context.context(db, project_id, owner)['runs']
            if not owned:
                return {'ok': True, 'has_run': False, 'project_id': project_id, 'nodes': [], 'message': '当前对话暂无工作流运行'}
            run_id = next((r['id'] for r in owned if r['status'] in ('running', 'queued', 'awaiting_review')), owned[0]['id'])
        return run_summary(db, project_id, run_id)
    if action == "start_execution":
        if getattr(db, "project_task_context", None) and db.project_task_context.parent_task_id:
            raise PermissionError("节点任务不能自行启动整个工作流")
        layout_id = str(args.get("layout_id") or args.get("appId") or args.get("template_id") or "")
        if not layout_id and (args.get("name") or args.get("workflow_name")):
            matched = _find_custom_workflow(db, project_id, str(args.get("name") or args.get("workflow_name")))
            if matched:
                layout_id = matched["id"]
        if layout_id and not run_id:
            run = workflow_tasks.prepare(db, project_id, str(args.get("goal") or ""), template_id=layout_id, inputs=args.get("inputs"))
        else:
            run = _get_run(db, project_id, run_id)
        if not run:
            raise ValueError("请先保存工作流，并指定工作流模板或运行")
        if layout_id and run['layout']['id'] != layout_id:
            raise ValueError("项目已有其他未完成的工作流")
        if run["status"] != "queued":
            raise ValueError("只有已准备的工作流可以启动；中断的运行请使用恢复")
        if (run.get("settings") or {}).get("prepared"):
            workflow_node_workbench.mark_agent_started(db, run)
            run["settings"]["prepared"] = False
            workflow_tasks._save(db, run)
        workflow_execution.kick(db.path, run["id"])
        return {
            **run_summary(db, project_id, run["id"]),
            "navigation": {"tab": "workspace", "project_id": project_id, "section": "workflow", "dock_tab": "workflow", "workflow_run_id": run["id"], "open_right_stage": True},
        }
    if action in ("approve_step", "rework_step"):
        raise PermissionError("人工核查须由用户在节点产物页完成")
    if action == "stop":
        run = _get_run(db, project_id, run_id) if run_id else workflow_execution.active(db, project_id)
        if not run:
            raise ValueError("当前没有正在运行的工作流")
        return {"ok": True, "run": workflow_tasks.cancel(db, run["id"], authority=getattr(db, "project_task_context", None)
                                                         or project_tasks.CommandContext.agent(project_id))}
    if action == "pause":
        raise ValueError("当前版本只支持停止与从中断恢复，不支持暂停")
    if action == "resume":
        run = _get_run(db, project_id, run_id)
        if not run:
            raise ValueError("找不到要恢复的工作流运行")
        if getattr(db, "project_task_context", None) and db.project_task_context.parent_task_id:
            raise PermissionError("节点任务不能自行恢复整个工作流")
        workflow_execution.resume(db, run["id"], node_id=str(args.get('node_id') or ''),
            request_key=str(args.get('request_key') or ''), expected_task_run_id=str(args.get('expected_task_run_id') or ''),
            restart_node=bool(args.get('restart_node')), authority=getattr(db, 'project_task_context', None)
            or project_tasks.CommandContext.agent(project_id))
        return run_summary(db, project_id, run["id"])
    raise ValueError(f"不支持的 supervisor action: {action}")


def platform_call(db: Any, project_id: str, action: str, args: dict[str, Any] | None = None, confirm: bool = True) -> dict[str, Any]:
    """EvoFlow `platform` 统一控制平面总线."""
    if args is not None and not isinstance(args, dict):
        raise ValueError("平台动作参数必须为对象")
    args = dict(args or {})
    act = str(action or "").strip()
    nested_project = str(args.get("project_id") or "").strip()
    if nested_project and project_id and nested_project != project_id:
        raise ValueError("平台动作必须在当前项目内执行")
    project_id = project_id or nested_project
    if act == "tasks.set_state":
        raise PermissionError("人工核查须由用户在节点产物页完成")
    if project_id or act != "workflow.schema":
        workflow_layout._project(db, project_id)
    entry = next((entry for entries in PLATFORM_CATALOG.values() for entry in entries if entry["action"] == act), {})
    if not confirm and (entry.get("risk") == "write" or args.get("auto_start") is True):
        raise ValueError("当前请求未确认执行写操作")

    if act == "workflow.list":
        ensure_presets(db)
        workflow_layout.ensure_schema(db)
        state = workflow_layout.state(db, project_id) if project_id else workflow_layout.list_layouts(db)
        layouts = []
        for l in state.get("layouts", []):
            item = dict(l)
            lid = str(item.get("id", ""))
            if lid.startswith("builtin."):
                item["source"] = "builtin"
            elif lid.startswith("tpl_"):
                item["source"] = "template"
            else:
                item["source"] = "custom"
            layouts.append(item)
        return {"ok": True, "action": act, "result": {"layouts": layouts, "selected_layout_id": (state.get("layout") or {}).get("id", "")}}
    if act == "workflow.get":
        ensure_presets(db)
        workflow_layout.ensure_schema(db)
        app_id = str(args.get("appId") or args.get("layout_id") or "").strip()
        layout = workflow_layout._layout(db, app_id) if app_id else (workflow_layout.state(db, project_id).get("layout") if project_id else None)
        return {"ok": True, "action": act, "result": {"layout": layout}}
    if act == "workflow.schema":
        return {"ok": True, "action": act, "result": {"stages": list(STAGES), "roles": list(ROLE_STAGE_MAP.keys())}}
    if act in ("workflow.generate", "workflow.create"):
        res = evoflow_plan(db, project_id, args, save=act == "workflow.create")
        return {"ok": True, "action": act, "result": res, "navigation": res.get("navigation"), "review_targets": res.get("review_targets")}
    if act == "workflow.run":
        custom_target = str(args.get("template_id") or args.get("layout_id") or args.get("name") or args.get("workflow_name") or args.get("appId") or "").strip()
        matched = _find_custom_workflow(db, project_id, custom_target) if custom_target else None
        target_layout_id = matched["id"] if matched else (args.get("layout_id") or args.get("template_id") or args.get("appId") or "")
        run_goal = str(args.get("goal") or (matched.get("description") if matched else "")).strip()
        run_id = str(args.get("runId") or args.get("run_id") or "").strip()
        if (matched or target_layout_id) and not run_id:
            res = evoflow_plan(db, project_id, {
                **args,
                "template_id": target_layout_id,
                "goal": run_goal,
                "auto_start": True,
            }, save=True)
            return {"ok": True, "action": act, "result": res, "navigation": res.get("navigation")}
        res = evoflow_supervisor(db, project_id, {
            **args,
            "action": "start_execution",
            "layout_id": target_layout_id,
            "goal": run_goal,
        })
        return {"ok": True, "action": act, "result": res, "navigation": res.get("navigation")}
    if act in ("workflow.status", "workflow.run_status"):
        return {"ok": True, "action": act, "result": run_summary(db, project_id, str(args.get("runId") or args.get("run_id") or ""))}
    if act == "workflow.stop":
        return {"ok": True, "action": act, "result": evoflow_supervisor(db, project_id, {**args, "action": "stop"})}
    if act == "workflow.resume":
        return {"ok": True, "action": act, "result": evoflow_supervisor(db, project_id, {**args, "action": "resume"})}

    if act == "tasks.list":
        rows = project_tasks.list_tasks(db, project_id, workflow_run_id=str(args.get("workflow_run_id") or args.get("run_id") or "") or None)
        status_filter = str(args.get("status") or "").strip()
        if status_filter:
            rows = [r for r in rows if r.get("status") == status_filter]
        return {"ok": True, "action": act, "result": {"tasks": rows[: int(args.get("limit") or 50)]}}
    if act == "tasks.get":
        tid = str(args.get("task_id") or args.get("id") or "").strip()
        return {"ok": True, "action": act, "result": {"task": project_tasks.get(db, tid, project_id=project_id)}}
    if act == "tasks.progress":
        return {"ok": True, "action": act, "result": run_summary(db, project_id, str(args.get("run_id") or ""))}
    if act == "tasks.execution_trail":
        tid = str(args.get("task_id") or args.get("id") or "").strip()
        t = project_tasks.get(db, tid, project_id=project_id)
        return {"ok": True, "action": act, "result": {"task_id": tid, "latest_run": t.get("latest_run"), "deliveries": t.get("deliveries", [])}}

    if act == "employees.list":
        bindings = project_bindings.list_bindings(db, project_id) if project_id else []
        return {"ok": True, "action": act, "result": {"employees": bindings, "preset_roles": list(ROLE_STAGE_MAP.keys())}}
    if act == "employees.dispatch":
        if not str(args.get("executor_binding_id") or "").strip() or not str(args.get("goal") or "").strip():
            raise ValueError("派发需要选择已授权的执行者并提供任务目标")
        made = task_tools.call(db, project_id, "task_create", args)
        task = made["task"]
        if task["status"] in ("ready", "queued"):
            made = task_tools.call(db, project_id, "task_start", {"id": task["id"], "request_key": str(args.get("request_key") or "")})
        return {"ok": True, "action": act, "result": {**made, "task_id": task["id"], "status": made["task"]["status"]},
                "navigation": {"tab": "workspace", "project_id": project_id, "section": "tasks", "task_id": task["id"]}}
    if act == "employees.wake":
        dock_tab = "expert-chat"
        return {
            "ok": True,
            "action": act,
            "result": {"status": "panel_opened"},
            "navigation": {"tab": "workspace", "project_id": project_id, "section": dock_tab, "dock_tab": dock_tab, "open_right_stage": True},
        }

    if act == "ui.panel":
        op = str(args.get("op") or "openRightStage").strip()
        kind = str(args.get("kind") or args.get("tab") or "workflow").strip()
        dock_map = {"workflow": "workflow", "dag": "workflow", "review": "node-result", "node-review": "node-result", "employee": "expert-chat", "expert": "expert-chat"}
        dock_tab = dock_map.get(kind, "workflow")
        return {
            "ok": True,
            "action": act,
            "result": {"op": op, "dock_tab": dock_tab, "project_id": project_id},
            "navigation": {"tab": "workspace", "project_id": project_id, "section": dock_tab, "dock_tab": dock_tab, "open_right_stage": True},
        }

    raise ValueError(f"未知的 EvoFlow platform action: {act}")


def route(method: str, path: str, body: dict[str, Any], query: dict[str, list[str]], db: Any) -> dict[str, Any] | None:
    """HTTP 路由挂载：/api/platform/catalog 与 /api/platform."""
    if method == "GET" and path == "/api/platform/catalog":
        domain = (query.get("domain") or [""])[0].strip()
        if domain and domain in PLATFORM_CATALOG:
            return {"ok": True, "domain": domain, "actions": PLATFORM_CATALOG[domain]}
        return {"ok": True, "catalog": PLATFORM_CATALOG}
    if method == "POST" and path == "/api/platform":
        action = str(body.get("action") or "").strip()
        raw_args = body.get("args")
        if raw_args is None and body.get("args_json"):
            raw_args = json.loads(str(body["args_json"])) if isinstance(body["args_json"], str) else body["args_json"]
        project_id = str(body.get("project_id") or (query.get("project_id") or [""])[0]).strip()
        return platform_call(db, project_id, action, raw_args, confirm=body.get("confirm", True) is True)
    return None
