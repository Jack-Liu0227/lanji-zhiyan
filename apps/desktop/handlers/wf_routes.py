# -*- coding: utf-8 -*-
"""三栏页（step/doc/map/conv/fig/tree/file/idea）

从 server.py 的 do_GET 里整块搬过来，**逻辑一行没改**（只是少缩进 4 格、末尾多一句 return None）。
DB_PATH 这类运行期常量在函数里 `from server import ...` 现取：启动时迁移失败会把它改指老库，
模块导入期就绑死会拿到旧值。
"""
from typing import Any

from pathlib import Path

import fileguard
from workflow import (WorkflowDB, _safe_path, cite_read, conv_files, conv_preview, doc_read,
                      en_state, fig_list, map_read, map_sources, project_outs, tree_list)


class WorkflowRoutes:
    def _r_workflow(self, path: str, q: dict[str, list[str]]) -> Any:
        """命中就返回响应（None 也算已响应），没命中返回 None 交给下一段。"""
        from server import DB_PATH        # 启动可能改写它（迁移失败退回老库），导入期绑死会拿旧值
        if path == "/api/step/messages":               # 三栏页：右栏「本步助手」的历史（按 scope 取）
            with WorkflowDB(DB_PATH) as db:
                step = (q.get("step") or ["idea"])[0]
                tid = q["id"][0]
                return self._json({"step": step, "messages": db.messages(tid, step)})
        if path == "/api/doc/state":                  # 确认大纲 / 论文撰写 / 稿件审查 共用的「左边那份文件」
            step = (q.get("step") or ["outline"])[0]
            with WorkflowDB(DB_PATH) as db:
                tid = q["id"][0]
                return self._json({"step": step, "out": str(db.out_dir(tid)),
                                   "long": db.memory(tid, "long"),
                                   "messages": db.messages(tid, step),
                                   **doc_read(db, tid, step)})
        if path == "/api/map/state":                  # 梳理精读：谱系图 + 能挂的源 + 大纲
            with WorkflowDB(DB_PATH) as db:
                tid = q["id"][0]
                return self._json({"messages": db.messages(tid, "map"),
                                   "outline": doc_read(db, tid, "outline"),
                                   "sources": map_sources(db, tid),
                                   **map_read(db, tid)})
        if path == "/api/step/outs":                   # 右栏「产出目录」：项目里落过盘的文件，新的在前
            with WorkflowDB(DB_PATH) as db:
                tid = q["id"][0]
                return self._json({"out": str(db.out_dir(tid)), "outs": project_outs(db, tid)})
        if path == "/api/en/state":                   # 中译英：默认源（正文.md）+ 上次译文
            with WorkflowDB(DB_PATH) as db:
                return self._json(en_state(db, q["id"][0]))
        if path == "/api/doc/cite":                   # 后台引用台账（读之前先按当前正文同步状态）
            with WorkflowDB(DB_PATH) as db:
                return self._json(cite_read(db, q["id"][0]))
        if path == "/api/conv/files":                 # 格式转换：可导出的成品（右栏下拉用）
            with WorkflowDB(DB_PATH) as db:
                return self._json(conv_files(db, q["id"][0]))
        if path == "/api/fig/list":                   # 格式转换：左栏「图」文件夹
            with WorkflowDB(DB_PATH) as db:
                return self._json(fig_list(db, q["id"][0]))
        if path == "/api/tree":                       # 项目文件夹：一层清单（rel 相对项目根）
            with WorkflowDB(DB_PATH) as db:
                try:
                    return self._json(tree_list(db, q["id"][0], (q.get("rel") or [""])[0]))
                except Exception as exc:
                    return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
        if path == "/api/conv/preview":               # 右栏展示区：PDF/图片直给，Word 先转 PDF
            try:
                return self._json(conv_preview(q.get("path", [""])[0]))
            except Exception as exc:
                return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
        if path == "/api/conv/read":                  # 打开一个文件（代码区）
            with WorkflowDB(DB_PATH) as db:
                p = _safe_path(q.get("path", [""])[0])
                if not p.is_file():
                    return self._json({"error": "文件不存在"}, 404)
                if p.suffix.lower() in (".docx", ".pdf"):
                    return self._json({"path": str(p), "binary": True, "ext": p.suffix.lower()[1:], "text": ""})
                text = p.read_text(encoding="utf-8", errors="replace")
                cap = int(q.get("cap", ["200000"])[0])
                return self._json({"path": str(p), "name": p.name, "ext": p.suffix.lower()[1:],
                                   "text": text[:cap], "truncated": len(text) > cap, "bytes": len(text)})
        if path == "/api/file/snapshots":             # 某文件还有几个历史版本（写前快照）
            p = _safe_path(q.get("path", [""])[0])
            return self._json({"path": str(p),
                               "snapshots": [{"at": s.parent.name + " " + s.name, "bytes": s.stat().st_size}
                                             for s in fileguard.snapshots(p)]})
        if path == "/api/paper/open":                 # 星图/文献双击：用系统默认程序打开这篇论文的 PDF
            pid = q.get("id", [""])[0]
            req_path = q.get("path", [""])[0]
            p = None
            if pid:
                with WorkflowDB(DB_PATH) as db:
                    row = db.db.execute("SELECT source_path FROM papers WHERE id=?", (pid,)).fetchone()
                if row and row["source_path"]:
                    p = Path(row["source_path"])
            if (not p or not p.is_file()) and req_path:
                p = Path(req_path)
            if not p or not p.is_file():
                if req_path:
                    fname = Path(req_path).name
                    with WorkflowDB(DB_PATH) as db:
                        row = db.db.execute("SELECT source_path FROM papers WHERE filename=? OR source_path LIKE ?",
                                            (fname, f"%{fname}")).fetchone()
                    if row and row["source_path"] and Path(row["source_path"]).is_file():
                        p = Path(row["source_path"])
            if not p or not p.is_file():
                return self._json({"error": "找不到这篇论文的 PDF 文件（可能已移走）"}, 404)
            return self._json(fileguard.open_document(p))
        if path == "/api/file":                       # 把项目里的文件发出去（PDF 给 iframe 预览用）
            p = _safe_path(q.get("path", [""])[0])
            if not p.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ext = p.suffix.lower()
            ctype = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                     ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".md": "text/markdown; charset=utf-8",
                     ".tex": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                     ".html": "text/html; charset=utf-8"}.get(ext, "application/octet-stream")
            # inline 预览不带文件名:http.server 按 latin-1 编 header,中文名会让整条响应坏掉(Chromium 报「无效的响应」);下载走 filename*
            extra = {"Content-Disposition": "inline"} if (ext == ".pdf" or q.get("inline")) else None
            return self._send(200, p.read_bytes(), ctype, extra)
        if path == "/api/idea/memory":
            with WorkflowDB(DB_PATH) as db:
                tid = q["id"][0]
                db.seed_long_memory(tid)
                return self._json({
                    "out": str(db.out_dir(tid)),
                    "mine": db.idea_input(tid),                 # 上区：用户写的创新点原文
                    "verdict": db.idea_verdict(tid),            # 下区：最近一次判定（笔记 markdown）
                    "long": db.memory(tid, "long"),
                    "short": [{"role": m["role"], "content": m["content"], "created_at": m["created_at"],
                               "events": m.get("events") or []} for m in db.messages(tid)],
                })
        if path == "/api/easyscholar/ranks":
            import easyscholar
            venues = [v.strip() for v in (q.get("venues", [""])[0] or "").split(",") if v.strip()]
            ranks = {}
            for v in venues[:40]:
                try:
                    r = easyscholar.query_publication_rank(v)
                    if r and not r.get("error"):
                        ranks[v] = {
                            "badges": easyscholar.parse_badges(r),
                            "sciUp": r.get("sciUp"),
                            "sciUpTop": r.get("sciUpTop"),
                            "sci": r.get("sci"),
                            "sciif": r.get("sciif"),
                            "ccf": r.get("ccf"),
                            "pku": r.get("pku"),
                            "sciwarn": r.get("sciwarn")
                        }
                except Exception:
                    pass
            cfg = easyscholar.get_config()
            return self._json({"ranks": ranks, "has_key": bool(cfg.get("secret_key"))})
        if path == "/api/step/sessions":
            with WorkflowDB(DB_PATH) as db:
                tid = (q.get("id") or [""])[0]
                step = (q.get("step") or [""])[0]
                sessions = []
                archived_workflow = []
                assistant_chats = []
                try:
                    # 1. 各步骤当前活跃会话统计
                    rows = db.db.execute("SELECT scope, role, content, created_at FROM chat WHERE task_id=? ORDER BY created_at DESC", (tid,)).fetchall()
                    step_groups = {}
                    for r in rows:
                        sc = r["scope"] or "idea"
                        if sc not in step_groups:
                            step_groups[sc] = {"count": 0, "last_msg": r["content"][:60], "updated_at": r["created_at"]}
                        step_groups[sc]["count"] += 1
                    for sc, data in step_groups.items():
                        sessions.append({"step": sc, "count": data["count"], "last_msg": data["last_msg"], "updated_at": data["updated_at"]})

                    # 2. 归档的工作流历史会话（在 chats 表中 assistant_id='step-xxx'）
                    arc_rows = db.db.execute("SELECT id, title, assistant_id, created_at, updated_at FROM chats WHERE task_id=? AND assistant_id LIKE 'step-%' ORDER BY updated_at DESC", (tid,)).fetchall()
                    for ar in arc_rows:
                        cid = ar["id"]
                        sc = ar["assistant_id"][5:]
                        cnt_row = db.db.execute("SELECT count(*) as c, content FROM chat WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (cid,)).fetchone()
                        cnt = cnt_row["c"] if cnt_row else 0
                        last = cnt_row["content"][:60] if cnt_row and cnt_row["content"] else ""
                        archived_workflow.append({
                            "id": cid, "step": sc, "title": ar["title"], "count": cnt,
                            "last_msg": last, "updated_at": ar["updated_at"]
                        })

                    # 3. 一级助手 / 通用会话（智研管家、独立助手等）
                    ast_rows = db.db.execute("SELECT id, title, assistant_id, created_at, updated_at FROM chats WHERE (assistant_id NOT LIKE 'step-%' OR assistant_id IS NULL) ORDER BY updated_at DESC").fetchall()
                    assistants_meta = {a["id"]: a["name"] for a in db.assistants()}
                    assistants_meta["builtin-steward"] = "智研管家"
                    assistants_meta[""] = "智研管家"
                    for asr in ast_rows:
                        cid = asr["id"]
                        aid = asr["assistant_id"] or ""
                        aname = assistants_meta.get(aid, "智能助手")
                        cnt_row = db.db.execute("SELECT count(*) as c, content FROM chat WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (cid,)).fetchone()
                        cnt = cnt_row["c"] if cnt_row else 0
                        last = cnt_row["content"][:60] if cnt_row and cnt_row["content"] else ""
                        assistant_chats.append({
                            "id": cid, "title": asr["title"], "assistant_id": aid,
                            "assistant_name": aname, "count": cnt,
                            "last_msg": last, "updated_at": asr["updated_at"]
                        })
                except Exception:
                    pass
                return self._json({
                    "task_id": tid, "current_step": step,
                    "sessions": sessions,
                    "archived_workflow": archived_workflow,
                    "assistant_chats": assistant_chats
                })
        return None
