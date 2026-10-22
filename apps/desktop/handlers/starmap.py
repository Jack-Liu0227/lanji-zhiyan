# -*- coding: utf-8 -*-
"""窗口 / 知识星图（星图简介、知识点条目、荧光笔读）

从 server.py 的 do_GET 里整块搬过来，**逻辑一行没改**（只是少缩进 4 格、末尾多一句 return None）。
DB_PATH 这类运行期常量在函数里 `from server import ...` 现取：启动时迁移失败会把它改指老库，
模块导入期就绑死会拿到旧值。
"""
from typing import Any

import starmap                   # 知识星图
import zotero                    # Zotero 本机只读
from workflow import WorkflowDB


class StarmapRoutes:
    def _r_starmap(self, path: str, q: dict[str, list[str]]) -> Any:
        """命中就返回响应（None 也算已响应），没命中返回 None 交给下一段。"""
        from server import DB_PATH, _win_hwnd, _at_work_area        # 启动可能改写它（迁移失败退回老库），导入期绑死会拿旧值
        if path == "/api/win/state":
            h = _win_hwnd()
            return self._json({"zoomed": bool(h and _at_work_area(h))})
        if path == "/api/starmap":
            tid = (q.get("task") or [""])[0]
            fresh = (q.get("rebuild") or [""])[0] in ("1", "true")
            with WorkflowDB(DB_PATH) as db:
                # 不带 task = 第一级（知识库 → 项目）；带 task = 该项目的五簇分支
                # 分支层是编译产物：第一次进来算一次落库，之后直接读（不实时重算）；?rebuild=1 才重编译
                return self._json(db.kb_graph(tid, fresh) if tid else starmap.overview(db.db))
        if path == "/api/tr/hl":                     # 论文翻译：荧光笔标注（读）
            with WorkflowDB(DB_PATH) as db:
                return self._json({"list": db.tr_hl((q.get("id") or [""])[0], (q.get("task") or [""])[0])})
        if path == "/api/starmap/brief":            # 知识点简介：库里有回库里的，没有才调一次模型
            term = (q.get("term") or [""])[0]
            with WorkflowDB(DB_PATH) as db:
                return self._json(starmap.brief(db.db, term, db.active_model()))
        if path == "/api/starmap/item":
            pid = (q.get("id") or [""])[0]
            title = (q.get("title") or [""])[0]
            doi = (q.get("doi") or [""])[0]
            key = (q.get("key") or [""])[0]
            filename = ""
            db_row = None
            if pid:
                with WorkflowDB(DB_PATH) as db:
                    db_row = db.db.execute(
                        "SELECT p.id, p.task_id, p.filename, p.source_path, p.pages, "
                        "m.title, m.authors, m.doi, m.year "
                        "FROM papers p LEFT JOIN paper_meta m ON m.paper_id=p.id "
                        "WHERE p.id=?", (pid,)).fetchone()
            if db_row:
                filename = db_row["filename"] or ""
                if not title:
                    title = db_row["title"] or filename
                if not doi:
                    doi = db_row["doi"] or ""

            res = {}
            try:
                res = zotero.get_item_details(key=key, title=title, doi=doi, filename=filename)
            except Exception as exc:
                res = {"zotero_found": False, "error": str(exc)}

            if not res or not res.get("zotero_found"):
                authors_list = []
                if db_row and db_row["authors"]:
                    for a in (db_row["authors"]).split(" · "):
                        if a.strip():
                            authors_list.append({"name": a.strip(), "role": "作者"})
                att = []
                if db_row and db_row["source_path"]:
                    att.append({
                        "title": db_row["filename"] or "PDF 全文",
                        "filename": db_row["filename"] or "",
                        "path": db_row["source_path"] or "",
                        "contentType": "application/pdf"
                    })
                res = {
                    "zotero_found": False,
                    "key": key or "",
                    "itemType": "journalArticle",
                    "itemTypeLabel": "文献",
                    "title": title or (db_row["title"] if db_row else "") or (db_row["filename"] if db_row else "未命名论文"),
                    "citationKey": "",
                    "creators": authors_list,
                    "publication": "",
                    "journalAbbr": "",
                    "publisher": "",
                    "place": "",
                    "date": (db_row["year"] if db_row else "") or "",
                    "volume": "",
                    "issue": "",
                    "pages": f"{db_row['pages']} 页" if (db_row and db_row["pages"]) else "",
                    "doi": doi or (db_row["doi"] if db_row else "") or "",
                    "url": (f"https://doi.org/{doi}" if doi else ""),
                    "accessDate": "",
                    "issn": "",
                    "isbn": "",
                    "language": "en",
                    "libraryCatalog": "本地文献库",
                    "extra": "",
                    "dateAdded": "",
                    "dateModified": "",
                    "abstract": "",
                    "tags": [],
                    "attachments": att,
                    "zotero_select_url": "",
                }
            return self._json(res)
        return None
