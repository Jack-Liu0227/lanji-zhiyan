# -*- coding: utf-8 -*-
"""项目核心（/api/state、任务、笔记、对话、权限模式）

从 server.py 的 do_GET 里整块搬过来，**逻辑一行没改**（只是少缩进 4 格、末尾多一句 return None）。
DB_PATH 这类运行期常量在函数里 `from server import ...` 现取：启动时迁移失败会把它改指老库，
模块导入期就绑死会拿到旧值。
"""
from typing import Any

import os
import subprocess
import sys
import time
import workflow_layout
from workflow_catalog import STAGE_CATALOG

import service_settings as kv_store      # 轻量 KV：权限模式这类开关
from workflow import STAGES, STAGE_LABEL, WorkflowDB, litgraph


class CoreRoutes:
    def _r_core(self, path: str, q: dict[str, list[str]]) -> Any:
        """命中就返回响应（None 也算已响应），没命中返回 None 交给下一段。"""
        from server import DB_PATH        # 启动可能改写它（迁移失败退回老库），导入期绑死会拿旧值
        if path == "/api/zotero/select":
            key = (q.get("key") or [""])[0]
            if not key:
                return self._json({"error": "缺少 key"}, 400)
            try:
                if sys.platform == "win32":
                    os.startfile(f"zotero://select/items/0_{key}")
                else:
                    subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", f"zotero://select/items/0_{key}"])
                return self._json({"ok": True})
            except Exception as exc:
                return self._json({"error": f"唤起失败：{exc}"}, 500)
        if path == "/api/litgraph":
            tid = (q.get("task") or [""])[0]
            with WorkflowDB(DB_PATH) as db:
                return self._json(litgraph(db, tid))
        if path == "/api/state":
            with WorkflowDB(DB_PATH) as db:
                all_tasks = db.tasks()
                if not all_tasks:
                    db.create_task(
                        "锂金属固态电池界面反应动力学与多尺度演化研究",
                        "如何解决高倍率循环下固态电解质/金属锂界面的接触失效与副反应演化问题？",
                        "固态锂电池, 复合固态电解质, 锂金属负极",
                    )
                    all_tasks = db.tasks()
                active = db.active_model()
                return self._json({
                    "tasks": [{**dict(t), "stage_now": db.stage_now(t["id"], t["stage"])} for t in all_tasks],
                    "chats": db.chats(),
                    "assistants": db.assistants(),
                    "models": [{**m, "api_key": "***" if m["api_key"] else ""} for m in db.models()],
                    "active": (lambda m: ({**m, "api_key": "***"} if m and m["api_key"] else m))(active),
                    "active_model": (lambda m: ({**m, "api_key": "***"} if m and m["api_key"] else m))(active),
                    "events": db.events(q.get("month", [None])[0]),
                    "notes": [{k: n[k] for k in ("id", "task_id", "title", "created_at")} for n in db.notes()],
                    "stages": [{"key": s, "label": STAGE_LABEL[s]} for s in STAGES],
                    "workflow_stages": STAGE_CATALOG,
                })
        if path == "/api/task":
            tid = q["id"][0]
            for attempt in range(4):
                try:
                    with WorkflowDB(DB_PATH) as db:
                        s = db.summary(tid)
                        import project_library
                        library_items = project_library.items(db, tid)
                        task = dict(s["task"] or {})
                        task["stage_now"] = db.stage_now(tid, task.get("stage") or "")
                        return self._json({
                            "task": task, "summary": s, "steps": db.steps(tid),
                            "stage_map": db.stage_map(tid), "workflow": workflow_layout.state(db, tid),
                            "results": db.results(tid),
                            "notes": [{k: n[k] for k in ("id", "title", "created_at")} for n in db.notes(tid)],
                            "papers": s["papers"], "evidence": s["evidence"],
                            "paper_list": db.papers_of(tid),
                            "library_items": library_items,
                            "paper_graph": db.paper_graph(tid),
                            "evidence_list": [dict(r) for r in db.evidence(tid)],
                        })
                except Exception as exc:
                    if "locked" in str(exc).lower() and attempt < 3:
                        time.sleep(0.25 * (attempt + 1))
                        continue
                    raise
        if path == "/api/note":
            with WorkflowDB(DB_PATH) as db:
                return self._json(db.note(q["id"][0]))
        if path == "/api/notes":
            with WorkflowDB(DB_PATH) as db:
                tid = q.get("id", [None])[0]
                return self._json({"notes": db.notes(tid) if tid else db.notes()})
        if path == "/api/chat":
            with WorkflowDB(DB_PATH) as db:
                return self._json({"messages": db.messages(q["id"][0])})
        if path == "/api/security":                   # 权限模式：all=不再逐次问，ask=编译/写文件每次问
            with WorkflowDB(DB_PATH) as db:
                sec = kv_store.read(db, "security")
                return self._json({"permission_mode": str(sec.get("permission_mode") or ""),
                                   "history_chars": int(sec.get("history_chars") or 0)})
        return None
