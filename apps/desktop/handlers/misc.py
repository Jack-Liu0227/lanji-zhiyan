# -*- coding: utf-8 -*-
"""其余（Vault、团队、日程、笔记、Zotero、PDF/翻译、RSS、工具、全局搜索）

从 server.py 的 do_GET 里整块搬过来，**逻辑一行没改**（只是少缩进 4 格、末尾多一句 return None）。
DB_PATH 这类运行期常量在函数里 `from server import ...` 现取：启动时迁移失败会把它改指老库，
模块导入期就绑死会拿到旧值。
"""
from typing import Any

import agent_integrations
import capabilities
import papertran                 # PDF 服务 / 元数据 / BabelDOC 任务
import paths
import project_notes
import rss                       # 情报日报
import schedule                  # 定时任务
import team                      # 团队
import vault                     # 知识库（Obsidian）
import workflow
import zotero                    # Zotero 本机只读
from workflow import CUSTOM_WIRES, WIRE_LABEL, WorkflowDB, deepread_state


class MiscRoutes:
    def _r_misc(self, path: str, q: dict[str, list[str]]) -> Any:
        """命中就返回响应（None 也算已响应），没命中返回 None 交给下一段。"""
        from server import DB_PATH, catalog_cards        # 启动可能改写它（迁移失败退回老库），导入期绑死会拿旧值
        if path == "/api/vault/scan":              # 知识库：扫一遍已授权 Vault（路径参数走 query）
            try:
                return self._json(vault.scan((q.get("vault") or [""])[0]))
            except vault.VaultError as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/folder/pick":
            return self._json(self._pick_folder((q.get("initial") or [""])[0],
                                                (q.get("title") or [""])[0] or "选择产出文件夹"))
        if path == "/api/deepread/status":           # 精读进度（前端每 2 秒问一次）
            return self._json(deepread_state((q.get("task_id") or [""])[0]) or {"phase": ""})
        if path == "/api/settings/meta":            # 设置页分区要的元数据：工具 / 技能 / MCP
            with WorkflowDB(DB_PATH) as db:
                project = (q.get("project_id") or [""])[0]
                row = db.db.execute("SELECT base_dir FROM research_tasks WHERE id=?", (project,)).fetchone()
                return self._json(capabilities.settings_meta(db, str(row["base_dir"] or "") if row else ""))
        if path == "/api/teams":                     # 团队清单（每项目 2 常驻 + 3 加急；不带 project 就是全部）
            try:
                return self._json(team.teams((q.get("project") or [""])[0] or None))
            except ValueError as exc:
                return self._json({"error": str(exc), "teams": []}, 400)
        if path == "/api/team/chat":                 # 团队 v2：成员（从助手选）+ 群记录 + 接力状态
            try:
                return self._json(team.chat_state((q.get("scope") or [""])[0]))
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/team":                      # 团队看板：成员状态 + 派活队列 + 运行事件
            try:
                return self._json(team.board((q.get("id") or [""])[0]))
            except ValueError as exc:
                return self._json({"error": str(exc), "members": [], "tasks": [], "counts": {}}, 400)
        if path == "/api/schedules":                 # 定时任务页：规则 + 最近运行
            with WorkflowDB(DB_PATH) as db:
                return self._json({"schedules": db.schedules(), "runs": db.schedule_runs(),
                                   "freq": schedule.FREQ_LABELS, "run_status": schedule.RUN_STATUS,
                                   "retryable": sorted(schedule.RETRYABLE)})
        if path == "/api/mdnotes":
            with WorkflowDB(DB_PATH) as db:
                return self._json({"notes": db.md_notes()})
        if path == "/api/project/notes":
            with WorkflowDB(DB_PATH) as db:
                return self._json(project_notes.dashboard(db, (q.get("project_id") or [""])[0]))
        if path == "/api/project/note":
            with WorkflowDB(DB_PATH) as db:
                return self._json(project_notes.read(db, (q.get("project_id") or [""])[0],
                                                     (q.get("path") or [""])[0]))
        if path == "/api/zotero/status":            # Zotero 本机只读接入（笔记页用）
            return self._json(zotero.status(q.get("base", [""])[0] or None))
        if path == "/api/zotero/collections":
            try:
                return self._json(zotero.collections(q.get("base", [""])[0] or None))
            except zotero.ZoteroError as exc:
                return self._json({"error": str(exc), "collections": []}, 400)
        if path == "/api/zotero/items":
            try:
                return self._json(zotero.items(
                    q.get("base", [""])[0] or None,
                    (q.get("collection") or [""])[0],
                    (q.get("q") or [""])[0],
                    int((q.get("start") or ["0"])[0] or 0),
                    int((q.get("limit") or ["50"])[0] or 50)))
            except (zotero.ZoteroError, ValueError) as exc:
                return self._json({"error": str(exc), "items": []}, 400)
        if path == "/api/zotero/note":              # 条目 → 可存成笔记的 title/content（不落盘）
            try:
                return self._json(zotero.note_for(q.get("base", [""])[0] or None,
                                                  (q.get("key") or [""])[0]))
            except zotero.ZoteroError as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/pdf":                       # 论文翻译页的阅读器：按入库记录回吐 PDF 字节
            with WorkflowDB(DB_PATH) as db:
                f = papertran.pdf_path(db, (q.get("id") or [""])[0])
            if not f:
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), "application/pdf",
                              {"Content-Disposition": "inline"})
        if path == "/api/trans/file":                # 译文（mono=纯中文 / dual=左右对照）
            kind = (q.get("kind") or ["mono"])[0]
            with WorkflowDB(DB_PATH) as db:
                pid = (q.get("id") or [""])[0]
                src = papertran.pdf_path(db, pid)
                tid = (q.get("task") or [""])[0]
                out = db.pdir(tid, "文献库") if tid else (paths.OUT)
            if not src:
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            f = papertran.out_paths(src, out)[0 if kind == "dual" else 1]
            if not f.is_file():
                return self._send(404, b"no translation", "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), "application/pdf",
                              {"Content-Disposition": "inline"})
        if path == "/api/pdftext":                    # 每页词坐标（前端自己铺文字层；kind=mono 是中文那栏）
            kind = (q.get("kind") or ["src"])[0]
            with WorkflowDB(DB_PATH) as db:
                f = papertran.pdf_path(db, (q.get("id") or [""])[0])
                tid = (q.get("task") or [""])[0]
                out = db.pdir(tid, "文献库") if tid else (paths.OUT)
            if not f:
                return self._json({"error": "找不到这篇 PDF 的文件"}, 404)
            if kind == "mono":
                f = papertran.out_paths(f, out)[1]
                if not f.is_file():
                    return self._json({"error": "还没有译文"}, 404)
            return self._json(papertran.pdf_words(f, int((q.get("pages") or ["0"])[0] or 0)))
        if path == "/api/papermeta":
            with WorkflowDB(DB_PATH) as db:
                return self._json(papertran.paper_meta(db, (q.get("id") or [""])[0]))
        if path == "/api/trans/status":
            return self._json(papertran.status((q.get("id") or [""])[0]))
        if path == "/api/rss/sources":
            with WorkflowDB(DB_PATH) as db:
                return self._json({"sources": db.rss_sources(), "defaults": rss.DEFAULT_SOURCES})
        if path == "/api/rss/items":
            with WorkflowDB(DB_PATH) as db:
                sid = (q.get("source") or [""])[0]
                kw = (q.get("q") or [""])[0].strip()
                yr = (q.get("year") or [""])[0].strip()
                return self._json({"items": db.rss_items(sid, kw, yr),
                                   "total": db.rss_count(sid, kw, yr)})
        if path == "/api/tools":
            return self._json({"tools": [{"name": t["function"]["name"],
                                          "description": t["function"]["description"]} for t in [*workflow.TOOL_SPECS, *agent_integrations.tool_specs()]]})
        if path == "/api/providers":
            with WorkflowDB(DB_PATH) as db:
                return self._json({"providers": catalog_cards(db),
                                   "wires": [{"id": w, "label": WIRE_LABEL.get(w, w)} for w in CUSTOM_WIRES],
                                   "default": db.provider_default()})
        if path == "/api/find":
            needle = (q.get("q", [""])[0] or "").strip().lower()
            hits = []
            with WorkflowDB(DB_PATH) as db:
                if needle:
                    for t in db.tasks():
                        if needle in t["title"].lower() or needle in (t["question"] or "").lower():
                            hits.append({"kind": "任务", "id": t["id"], "title": t["title"], "sub": t["question"]})
                    for n in db.notes():
                        if needle in n["title"].lower() or needle in n["markdown"].lower():
                            hits.append({"kind": "笔记", "id": n["task_id"], "title": n["title"], "sub": n["created_at"][:16].replace("T", " ")})
                    for r in db.db.execute("SELECT id,task_id,title,source,year FROM search_cache"):
                        if needle in (r["title"] or "").lower():
                            hits.append({"kind": "文献", "id": r["task_id"], "title": r["title"], "sub": f"{r['source']} {r['year']}"})
                    # evidence 没有 task_id，靠 papers 关联（原来直接 SELECT task_id 会 500）
                    for e in db.db.execute("SELECT e.field, e.value, e.excerpt, p.task_id AS task_id"
                                           " FROM evidence e JOIN papers p ON p.id = e.paper_id"):
                        if needle in (e["value"] or "").lower() or needle in (e["excerpt"] or "").lower():
                            hits.append({"kind": "证据", "id": e["task_id"], "title": f"{e['field']}: {e['value']}", "sub": e["excerpt"][:120]})
                    for ev in db.events():
                        if needle in ev["title"].lower():
                            hits.append({"kind": "日程", "id": ev["task_id"] or "", "title": ev["title"], "sub": ev["date"]})
            return self._json({"q": needle, "hits": hits[:60]})
        return None
