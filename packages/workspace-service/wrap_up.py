"""交付后沉淀一次：经过、教训、可复用配方。失败只丢这次经验，绝不影响交付。

落点都是现成的，这里只补「谁在什么时机写」和「写之前的闸门」：
- 经过 → memory_store.add(kind='episode')，episode 不进常驻注入、同正文自动去重
- 教训 → memory_store.add(kind='constraint', importance=2)
- 配方 → capabilities.write_skill()，写前过闸门（纯散文且太短 = 套话，丢弃）

时机：不挂在交付请求上（模型调用慢）。交付事件本身就是队列，启动时 drain 一次补齐。
闸门口径来自对标平台实测：自动沉淀里 69% 是「认真核对」这类套话，全放进技能目录
会直接吃满每轮注入预算。判据：有强判据词/命令，或正文 ≥400 字，才留下。
"""
from __future__ import annotations

import json
import re

MIN_CRAFT = 400          # 纯散文低于这个字数，判为套话
MAX_DESC = 80            # description 就是要注入的那一行，说长了是纯成本
_STRONG = re.compile(r"判据|断言|exit code|必须|否则|禁止|不许|验收|校验|```|`[^`\n]{2,}`", re.I)


def craft_ok(body: str) -> bool:
    text = str(body or "").strip()
    if not text:
        return False
    return len(text) >= MIN_CRAFT or bool(_STRONG.search(text))


def slug(text: str, fallback: str) -> str:
    """skill 名字只能 [a-z0-9._-]；模型给中文标题时退回 task-<id8>。"""
    name = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-._")
    return (name[:48].strip("-._") or fallback)


def _prompt(task: dict, run: dict) -> list[dict]:
    output = run.get("output_snapshot") or {}
    files = [item.get("name") or item.get("path", "") for item in (output.get("files") or [])][:8]
    brief = {
        "任务": task.get("title", ""),
        "目标": task.get("goal", ""),
        "产物": files,
        "正文": str(output.get("content") or "")[:2000],
        "回复": str((run.get("checkpoint") or {}).get("reply") or "")[:1500],
    }
    return [
        {"role": "system", "content": (
            "你刚交付完一项任务。只输出 JSON 对象："
            '{"journal":"这次做了什么、结论是什么（写完就不必再提）",'
            '"lesson":"下次同类任务必须遵守的一句话约束；没有实质约束就留空字符串",'
            '"craft":{"name":"英文小写短标识","description":"一行说清什么时候用这条",'
            '"body":"可复现的做法：步骤 + 命令或判据；没有可复现内容就留空字符串"}}。'
            "禁止套话（「认真核对」「保持严谨」「注意质量」这类一律不要）。"
            "拿不准就不写，空字符串比编一条更值钱。")},
        {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
    ]


def record(db, task: dict, run: dict, project_dir: str = "") -> dict:
    """同步沉淀一次（命令行/测试用）；生产入口是 async_run。"""
    import workflow
    model = db.active_model()
    if not model:
        return {"ok": False, "reason": "没有可用模型"}
    got = workflow.chat_json(model, _prompt(task, run))
    if not isinstance(got, dict):
        return {"ok": False, "reason": "模型没有返回对象"}

    import memory_store
    project = str(task.get("project_id") or "")
    written, dropped = [], []

    journal = str(got.get("journal") or "").strip()[:2000]
    if journal:
        memory_store.add(db, journal, scope="project", scope_id=project, kind="episode",
                         source="wrap-up", importance=1, tags="wrap-up")
        written.append("journal")

    lesson = str(got.get("lesson") or "").strip()[:600]
    if lesson:
        memory_store.add(db, lesson, scope="project", scope_id=project, kind="constraint",
                         source="wrap-up", importance=2, tags="wrap-up")
        written.append("lesson")

    craft = got.get("craft") if isinstance(got.get("craft"), dict) else {}
    body = str(craft.get("body") or "").strip()
    if body and craft_ok(body):
        import capabilities
        name = slug(craft.get("name"), "task-" + str(task.get("id") or "")[:8])
        description = " ".join(str(craft.get("description") or "").split())[:MAX_DESC] or "交付沉淀的做法"
        capabilities.write_skill(name, description, body, scope="project",
                                 project_dir=project_dir or db.base_dir(project), mode="append")
        written.append("craft:" + name)
    elif body:
        dropped.append("craft:套话")
    return {"ok": True, "written": written, "dropped": dropped}


def pending(db, limit: int = 3, days: int = 7) -> list[dict]:
    """已交付但还没沉淀的任务。交付事件本身当队列用 —— 不新建表。"""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 7)))).isoformat()
    rows = db.db.execute(
        "SELECT e.task_id, e.task_run_id FROM project_task_events e "
        "WHERE e.kind='delivered' AND e.created_at >= ? "
        "AND NOT EXISTS (SELECT 1 FROM project_task_events w WHERE w.task_id=e.task_id AND w.kind='wrap-up') "
        "ORDER BY e.created_at LIMIT ?", (cutoff, max(1, int(limit)))).fetchall()
    return [dict(row) for row in rows]


def _mark(db, task_id: str, run_id: str, outcome: dict) -> None:
    """记一条审计行：成功不再重跑，失败也不无限重试（每次都是真金白银的模型调用）。
    直接写表、不走 project_tasks._event —— 看板没必要知道这条账。"""
    from datetime import datetime, timezone
    from uuid import uuid4
    db.db.execute(
        "INSERT INTO project_task_events(id,task_id,task_run_id,kind,data,created_at) VALUES(?,?,?,?,?,?)",
        ("ptw-" + uuid4().hex, task_id, run_id or "", "wrap-up",
         json.dumps(outcome, ensure_ascii=False, default=str), datetime.now(timezone.utc).isoformat()))
    db.db.commit()


def drain(db, limit: int = 3, days: int = 7) -> dict:
    """补齐「已交付未沉淀」的任务。放在启动时跑：重活不占交付请求路径。"""
    import project_tasks
    done, failed = [], []
    for row in pending(db, limit, days):
        task_id, run_id = row["task_id"], row["task_run_id"]
        try:
            task = project_tasks._task(db, task_id)
            run = project_tasks.get_run(db, run_id) if run_id else None
            if not run:
                _mark(db, task_id, run_id, {"ok": False, "reason": "找不到这次运行"})
                failed.append(task_id)
                continue
            outcome = record(db, task, run)
            _mark(db, task_id, run_id, outcome)
            (done if outcome.get("ok") else failed).append(task_id)
        except Exception as exc:          # 一条坏账不许拖累后面几条
            try:
                _mark(db, task_id, run_id, {"ok": False, "reason": type(exc).__name__})
            except Exception:
                pass
            failed.append(task_id)
    return {"done": done, "failed": failed}


def drain_at_startup(db_path: str, limit: int = 3) -> None:
    """启动线程里调用：独立连接，任何异常都不许影响启动。"""
    try:
        import workflow
        with workflow.WorkflowDB(db_path) as fresh:
            drain(fresh, limit)
    except Exception:
        pass


if __name__ == "__main__":      # python wrap_up.py
    assert craft_ok("## 步骤\n1. 先跑 `tools/check.bat`\n2. 判据：退出码 0"), "有命令应放行"
    assert craft_ok("x" * 400), "够长应放行"
    assert not craft_ok("这次任务要认真核对数据，保持严谨。"), "套话应丢弃"
    assert not craft_ok(""), "空正文应丢弃"
    assert slug("Paper Outline v2", "f") == "paper-outline-v2", slug("Paper Outline v2", "f")
    assert slug("论文框架", "task-abcd1234") == "task-abcd1234", slug("论文框架", "task-abcd1234")
    assert slug("..//", "fb") == "fb", slug("..//", "fb")
    print("wrap_up self-check ok")
