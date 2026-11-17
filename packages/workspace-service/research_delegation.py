"""Bounded delegation with role-specific tools and reusable task results."""

from concurrent.futures import ThreadPoolExecutor, wait
from hashlib import sha256
import json
import re

import execution


# Each role defines the tools it can use and the deliverable it owes the caller.
ROLES = {
    "literature": ("文献检索", ("paper_discover", "paper_abstracts"),
                   "交付最相近的研究、搜索范围及覆盖缺口。摘要和元数据只用作候选线索。"),
    "evidence": ("原文取证", ("knowledge_query", "paper_excerpt", "paper_focus", "paper_evidence_get"),
                 "交付来源、页码和原文依据，保留实验条件及反证。"),
    "analysis": ("数据分析", ("project_resources", "research_data_profile", "knowledge_query"),
                 "交付数据版本、筛选条件、统计口径及对照缺口。用工具计算并区分样本数和独立研究数。"),
    "review": ("独立评审", ("knowledge_query", "paper_excerpt", "research_data_profile"),
               "仅根据原始证据独立评审。提出最强反例、结论边界和最小验证，证据不足时明确标记。"),
}
EVIDENCE_TOOLS = {"paper_discover", "paper_abstracts", "paper_excerpt", "paper_focus", "paper_evidence_get",
                  "knowledge_query", "research_data_profile"}
MAX_JOBS = 8
MAX_RUN_JOBS = 16
MAX_STEPS = 6


def normalize(jobs):
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("delegate 需要至少一个子任务（jobs[].prompt）")
    if len(jobs) > MAX_JOBS:
        raise ValueError(f"每次最多委派 {MAX_JOBS} 个子任务")
    normalized = {}
    for index, job in enumerate(jobs, 1):
        if not isinstance(job, dict) or not isinstance(job.get("prompt"), str) or not job["prompt"].strip():
            raise ValueError("每个子任务需要非空的 prompt")
        job_id = str(job.get("id") or f"job-{index}")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", job_id) or job_id in normalized:
            raise ValueError(f"子任务 ID 无效或重复：{job_id}")
        role = str(job.get("role") or "general")
        if role != "general" and role not in ROLES:
            raise ValueError(f"未知子任务角色：{role}")
        deps = job.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(dep, str) for dep in deps):
            raise ValueError("depends_on 必须是子任务 ID 数组")
        normalized[job_id] = {
            "role": role,
            "title": str(job.get("title") or (ROLES[role][0] if role in ROLES else f"子任务 {index}"))[:40],
            "prompt": job["prompt"].strip(),
            "scope_key": " ".join(str(job.get("scope_key") or "").casefold().split()),
            "deps": tuple(dict.fromkeys(deps)),
            "steps": max(1, min(int(job.get("max_steps") or 4), MAX_STEPS)),
        }
    # Validate all dependencies before launching any work.
    remaining = normalized.copy()
    phases = []
    while remaining:
        phase = [job_id for job_id, job in remaining.items()
                 if all(dep in normalized and dep not in remaining for dep in job["deps"])]
        if not phase:
            raise ValueError("子任务依赖包含未知 ID 或循环")
        phases.append(phase)
        for job_id in phase:
            del remaining[job_id]
    return normalized, phases


def run(jobs, phases, parallel, state, worker, drain_events):
    """Schedule ready jobs, coalescing identical work before any worker starts."""
    results = {}
    cache = state.setdefault("results", {})
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, 4))) as pool:
        for phase in phases:
            execution.check()
            pending = {}
            for job_id in phase:
                job = jobs[job_id]
                upstream = [results[dep] for dep in job["deps"]]
                failed = [dep["id"] for dep in upstream if not dep["ok"]]
                if failed and job["role"] != "review":
                    results[job_id] = {"id": job_id, "title": job["title"], "role": job["role"],
                                       "ok": False, "blocked": True, "steps": 0,
                                       "summary": "前置任务未完成：" + "、".join(failed)}
                    continue
                inputs = [
                    {"id": dep["id"], "available": dep["ok"], "evidence": dep.get("evidence", [])}
                    for dep in upstream
                ]
                if job["role"] != "review":
                    for item, dep in zip(inputs, upstream):
                        item["summary"] = dep["summary"][:500]
                key_inputs = [dict(item, id=None) for item in inputs]
                key = sha256(json.dumps([job["role"], job["scope_key"], " ".join(job["prompt"].split()), job["steps"],
                                         key_inputs], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                reused = key in cache or key in pending
                if not reused:
                    if state.get("started", 0) >= MAX_RUN_JOBS:
                        results[job_id] = {"id": job_id, "title": job["title"], "role": job["role"],
                                           "ok": False, "steps": 0, "summary": "已达到本轮委派预算"}
                        continue
                    state["started"] = state.get("started", 0) + 1
                    pending[key] = pool.submit(worker, job, inputs)
                results[job_id] = (key, reused)
            running = set(pending.values())
            while running:
                _, running = wait(running, timeout=0.05)
                drain_events()
                execution.check()
            for job_id in phase:
                entry = results[job_id]
                if isinstance(entry, dict):
                    continue
                key, reused = entry
                result = cache.get(key)
                if result is None:
                    result = pending[key].result()
                    if result["ok"]:
                        cache[key] = result
                job = jobs[job_id]
                results[job_id] = {**result, "id": job_id, "title": job["title"],
                                   "role": job["role"], "reused": reused}
    return [results[job_id] for job_id in jobs]


def collect_evidence(events):
    """Pass bounded tool outputs to reviewers without upstream conclusions."""
    evidence = []
    for event in events:
        tool = event.get("name")
        if event.get("type") != "tool_result" or not event.get("ok") or tool not in EVIDENCE_TOOLS:
            continue
        preview = json.dumps(event.get("result"), ensure_ascii=False, default=str)
        evidence.append({"tool": tool, "excerpt": preview[:900], "truncated": len(preview) > 900})
        if len(evidence) == 3:
            break
    return evidence
