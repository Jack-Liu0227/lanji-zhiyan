from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import pdfio  # PDF 读写走宽松许可适配层（pypdf/pdfplumber）
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("PDF 功能需要 pypdf / pdfplumber，见 requirements.txt") from exc


SCHEMA = """
CREATE TABLE IF NOT EXISTS research_tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '',
  material_system TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'definition',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, base_dir TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, out_dir TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, filename TEXT NOT NULL,
  source_path TEXT NOT NULL, sha256 TEXT NOT NULL, pages INTEGER NOT NULL,
  text_chars INTEGER NOT NULL, ocr_status TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS paper_pages (
  paper_id TEXT NOT NULL, page_no INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(paper_id, page_no), FOREIGN KEY(paper_id) REFERENCES papers(id)
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, page_no INTEGER NOT NULL,
  field TEXT NOT NULL, value TEXT, excerpt TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
  created_at TEXT NOT NULL, FOREIGN KEY(paper_id) REFERENCES papers(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, command TEXT NOT NULL,
  status TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS rss_sources (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '学术论文',
  enabled INTEGER NOT NULL DEFAULT 1, in_daily INTEGER NOT NULL DEFAULT 1,
  last_error TEXT NOT NULL DEFAULT '', last_fetch TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rss_items (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, link TEXT NOT NULL,
  title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL DEFAULT '', source_title TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS rss_items_link ON rss_items(link);
CREATE TABLE IF NOT EXISTS background_jobs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'running', progress REAL NOT NULL DEFAULT 0,
  last_event_id INTEGER NOT NULL DEFAULT 0, checkpoint TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_background_jobs_status ON background_jobs(status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def excerpt_matches(excerpt: str, page_text: str) -> bool:
    return compact(excerpt).lower() in compact(page_text).lower()


@contextmanager
def open_db_session(path: str | Path, timeout: float = 10.0):
    """独立短期数据库连接上下文：自动 WAL / timeout 配置并在退出时强制释放句柄。"""
    p = Path(path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


class ManagedConnection:
    """统一数据库事务与安全连接上下文管理器。"""
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def transaction(self, timeout: float = 10.0):
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=timeout)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass


class ResearchDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")   # 子 agent 并行时各自一条连接，别立刻报 database is locked
        self.db.row_factory = sqlite3.Row
        try:
            import migrations
            migrations.run_migrations(self.db, db_path=self.path if str(self.path) != ":memory:" else None)
        except Exception:
            pass
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass

    def __enter__(self) -> "ResearchDB":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @contextmanager
    def transaction(self):
        """事务安全上下文：自动 commit / rollback。"""
        try:
            yield self.db
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def create_task(self, title: str, question: str = "", material_system: str = "", base_dir: str = "") -> str:
        if not title.strip():
            raise ValueError("task title is required")
        task_id = uuid.uuid4().hex
        t = now()
        self.db.execute(
            "INSERT INTO research_tasks (id,title,question,material_system,stage,created_at,updated_at,base_dir)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (task_id, title.strip(), question.strip(), material_system.strip(), "definition", t, t, base_dir.strip()),
        )
        self.db.commit()
        return task_id

    def tasks(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM research_tasks ORDER BY updated_at DESC"))

    # ---------- 通用 Agent 对话（独立于项目） ----------
    def create_chat(self, title: str = "") -> str:
        cid = uuid.uuid4().hex
        t = now()
        # 具名列：chats 后续会补列（assistant_id），定位插入会跟着坏
        self.db.execute("INSERT INTO chats (id,title,out_dir,created_at,updated_at) VALUES (?,?,?,?,?)",
                        (cid, (title or "").strip() or "新对话", "", t, t))
        self.db.commit()
        return cid

    def chats(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM chats ORDER BY updated_at DESC")]

    def chat(self, cid: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
        if not row:
            raise KeyError(cid)
        return dict(row)

    def update_chat(self, cid: str, title: str | None = None, out_dir: str | None = None) -> dict[str, Any]:
        cur = self.chat(cid)
        self.db.execute("UPDATE chats SET title=?, out_dir=?, updated_at=? WHERE id=?",
                        ((cur["title"] if title is None else (title.strip() or cur["title"])),
                         cur["out_dir"] if out_dir is None else out_dir.strip(), now(), cid))
        self.db.commit()
        return self.chat(cid)

    def bind_chat(self, cid: str, task_id: str | None = None) -> dict[str, Any]:
        """把这个对话绑到一个项目上（传空 = 解绑成随意对话）。历史留在对话自己身上，视野与产出跟项目走。"""
        self.chat(cid)
        tid = (task_id or "").strip()
        if tid and not self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (tid,)).fetchone():
            raise ValueError("没有这个项目")
        self.db.execute("UPDATE chats SET task_id=?, updated_at=? WHERE id=?", (tid, now(), cid))
        self.db.commit()
        return self.chat(cid)

    def delete_chat(self, cid: str) -> dict[str, Any]:
        row = self.db.execute("DELETE FROM chats WHERE id=?", (cid,)).rowcount
        self.db.commit()
        return {"deleted": bool(row)}

    def ingest_pdf(self, task_id: str, source: str | Path) -> dict[str, Any]:
        path = Path(source).expanduser().resolve()
        if path.suffix.lower() != ".pdf":
            raise ValueError("only .pdf files are accepted")
        if not path.is_file():
            raise FileNotFoundError(path)
        if self.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise KeyError(task_id)
        sha = digest(path)
        existing = self.db.execute("SELECT id FROM papers WHERE sha256=?", (sha,)).fetchone()
        if existing:
            return self.paper(existing["id"])
        pages = [(i + 1, t) for i, t in enumerate(pdfio.page_texts(path))]
        text_chars = sum(len(text) for _, text in pages)
        ocr_status = "text" if text_chars >= 100 else "likely-scan"
        paper_id = uuid.uuid4().hex
        t = now()
        self.db.execute(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)",
            (paper_id, task_id, path.name, str(path), sha, len(pages), text_chars, ocr_status, t),
        )
        self.db.executemany("INSERT INTO paper_pages VALUES (?,?,?)", [(paper_id, n, text) for n, text in pages])
        self.db.execute("UPDATE research_tasks SET stage='reading', updated_at=? WHERE id=?", (t, task_id))
        self.db.commit()
        return self.paper(paper_id)

    def paper(self, paper_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            raise KeyError(paper_id)
        pages = list(self.db.execute("SELECT page_no, text FROM paper_pages WHERE paper_id=? ORDER BY page_no", (paper_id,)))
        return {**dict(row), "pages": [dict(p) for p in pages]}

    def extract_materials(self, paper_id: str) -> list[dict[str, Any]]:
        paper = self.paper(paper_id)
        patterns = {
            "composition": r"(?i)\b(?:Na|Li|K|Mg|Ca|Al|Ti|V|Cr|Mn|Fe|Co|Ni|Cu|Zn|Zr|Nb|Mo|Sn|W|Ta|Ce|Y)\w{0,12}(?:O\d|\d{1,3})?\b",
            "temperature": r"(?i)\b\d{2,4}\s*(?:°\s*C|℃|K)\b",
            "performance": r"(?i)\b\d+(?:\.\d+)?\s*(?:MPa|GPa|mAh\s*g-?1|%|S\s*cm-?1)\b",
        }
        out: list[dict[str, Any]] = []
        for page in paper["pages"]:
            text = page["text"]
            for field, pattern in patterns.items():
                for match in re.finditer(pattern, text):
                    excerpt = compact(text[max(0, match.start() - 100):match.end() + 100])
                    item = {
                        "id": uuid.uuid4().hex, "paper_id": paper_id, "page_no": page["page_no"],
                        "field": field, "value": match.group(0), "excerpt": excerpt,
                        "kind": "candidate", "created_at": now(),
                    }
                    if self.db.execute(
                        "SELECT 1 FROM evidence WHERE paper_id=? AND page_no=? AND field=? AND value=? AND excerpt=?",
                        (paper_id, item["page_no"], item["field"], item["value"], item["excerpt"]),
                    ).fetchone() is None:
                        self.db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)", tuple(item.values()))
                        out.append(item)
        self.db.commit()
        return out

    def create_run(self, task_id: str, command: str, input_hash: str, status: str = "running") -> str:
        run_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (run_id, task_id, command, status, input_hash, now()))
        self.db.commit()
        return run_id

    def finish_run(self, run_id: str, status: str) -> None:
        self.db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        self.db.commit()

    def add_evidence(self, paper_id: str, item: dict[str, Any]) -> dict[str, Any]:
        paper = self.paper(paper_id)
        page_no = int(item.get("page_no", 0))
        field = str(item.get("field", "")).strip()
        value = str(item.get("value", "")).strip()
        excerpt = compact(str(item.get("excerpt", "")))
        if not field or not value or not excerpt:
            raise ValueError("evidence requires field, value and excerpt")
        pages = {p["page_no"]: p["text"] for p in paper["pages"]}
        if page_no not in pages:
            raise ValueError(f"evidence page out of range: {page_no}")
        if not excerpt_matches(excerpt, pages[page_no]):
            raise ValueError(f"evidence excerpt is not present on page {page_no}")
        row = {
            "id": uuid.uuid4().hex, "paper_id": paper_id, "page_no": page_no,
            "field": field, "value": value, "excerpt": excerpt,
            "kind": item.get("kind", "fact"), "created_at": now(),
        }
        self.db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)", tuple(row.values()))
        self.db.commit()
        return row

    def evidence(self, task_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT e.*, p.filename FROM evidence e JOIN papers p ON p.id=e.paper_id"
        args: tuple[Any, ...] = ()
        if task_id:
            sql += " WHERE p.task_id=?"
            args = (task_id,)
        return list(self.db.execute(sql + " ORDER BY e.created_at DESC", args))

    def export_evidence(self, task_id: str, target: str | Path) -> Path:
        target = Path(target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [dict(r) for r in self.evidence(task_id)]
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, task_id, "evidence-json", str(target), digest(target), now()),
        )
        self.db.commit()
        return target

    def export_report(self, task_id: str, target: str | Path) -> Path:
        target = Path(target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary(task_id)
        rows = [dict(r) for r in self.evidence(task_id)]
        lines = [f"# {summary['task']['title']}", "", "## 研究问题", summary["task"]["question"] or "未填写", "", "## 关键词", summary["task"]["material_system"] or "未填写", "", "## 证据记录", ""]
        if not rows:
            lines.append("暂无证据记录。")
        for row in rows:
            lines += [f"- **{row['field']}**: {row['value']}（{row['filename']}，p.{row['page_no']}）", f"  - 原文：{row['excerpt']}", f"  - 状态：{row['kind']}"]
        lines += ["", "> 本报告只汇总已登记且带页码/原文片段的记录；候选证据仍需人工审查，不等于最终结论。", ""]
        target.write_text("\n".join(lines), encoding="utf-8")
        self.db.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, task_id, "research-report-md", str(target), digest(target), now()),
        )
        self.db.commit()
        return target

    def summary(self, task_id: str) -> dict[str, Any]:
        task = self.db.execute("SELECT * FROM research_tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            # 通用 Agent 对话（不绑项目）也要能跑工具：给它一个合成的「任务」上下文
            c = self.db.execute("SELECT * FROM chats WHERE id=?", (task_id,)).fetchone()
            if not c:
                t = self.db.execute("SELECT * FROM teams WHERE id=?", (task_id,)).fetchone()
                if t:                                  # 团队自己也能当上下文（团队页的接力/组队按团队 id 走）
                    c = {"id": t["id"], "title": t["name"], "created_at": t["created_at"], "updated_at": t["updated_at"]}
            if not c:
                raise KeyError(task_id)
            task = {"id": c["id"], "title": c["title"], "question": "", "material_system": "",
                    "stage": "chat", "created_at": c["created_at"], "updated_at": c["updated_at"]}
        papers = self.db.execute("SELECT COUNT(*) n FROM papers WHERE task_id=?", (task_id,)).fetchone()["n"]
        evidence = self.db.execute("SELECT COUNT(*) n FROM evidence e JOIN papers p ON p.id=e.paper_id WHERE p.task_id=?", (task_id,)).fetchone()["n"]
        return {"task": dict(task), "papers": papers, "evidence": evidence}

    # ---------- 可靠性扩展：任务持久化、重试、可恢复运行与审计事件 ----------
    def create_async_task(self, kind: str, owner_id: str, idempotency_key: str = "", checkpoint: dict[str, Any] | None = None, task_id: str = "") -> dict[str, Any]:
        tid = task_id or uuid.uuid4().hex
        t = now()
        cp = json.dumps(checkpoint or {}, ensure_ascii=False)
        self.db.execute(
            "INSERT INTO tasks (id, kind, owner_id, status, progress, checkpoint, cancel_requested, idempotency_key, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending', 0, ?, 0, ?, ?, ?)",
            (tid, kind, owner_id, cp, idempotency_key or None, t, t)
        )
        self.db.commit()
        return self.get_async_task(tid) or {}

    def get_async_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["checkpoint"] = json.loads(res.get("checkpoint") or "{}")
        except Exception:
            res["checkpoint"] = {}
        return res

    def get_async_task_by_idempotency(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        row = self.db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["checkpoint"] = json.loads(res.get("checkpoint") or "{}")
        except Exception:
            res["checkpoint"] = {}
        return res

    def update_async_task(self, task_id: str, status: str = "", progress: float | None = None,
                          checkpoint: dict[str, Any] | None = None, cancel_requested: bool | None = None) -> dict[str, Any] | None:
        cur = self.get_async_task(task_id)
        if not cur:
            return None
        sets, vals = ["updated_at=?"], [now()]
        if status:
            sets.append("status=?")
            vals.append(status)
        if progress is not None and progress >= 0:
            sets.append("progress=?")
            vals.append(float(progress))
        if checkpoint is not None:
            sets.append("checkpoint=?")
            vals.append(json.dumps(checkpoint, ensure_ascii=False))
        if cancel_requested is not None:
            sets.append("cancel_requested=?")
            vals.append(1 if cancel_requested else 0)
        vals.append(task_id)
        self.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
        self.db.commit()
        return self.get_async_task(task_id)

    def record_task_attempt(self, task_id: str, attempt: int, status: str, error_code: str = "",
                            started_at: str = "", ended_at: str = "") -> dict[str, Any]:
        aid = uuid.uuid4().hex
        s_at = started_at or now()
        self.db.execute(
            "INSERT INTO task_attempts (id, task_id, attempt, status, error_code, started_at, ended_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, task_id, attempt, status, error_code, s_at, ended_at)
        )
        self.db.commit()
        return {"id": aid, "task_id": task_id, "attempt": attempt, "status": status, "error_code": error_code,
                "started_at": s_at, "ended_at": ended_at}

    def record_run_event(self, run_id: str, event_id: int, kind: str, payload: dict[str, Any] | str) -> dict[str, Any]:
        pl = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
        t = now()
        self.db.execute(
            "INSERT OR REPLACE INTO run_events (run_id, event_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, event_id, kind, pl, t)
        )
        self.db.commit()
        return {"run_id": run_id, "event_id": event_id, "kind": kind, "payload": payload, "created_at": t}

    def get_run_events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT run_id, event_id, kind, payload, created_at FROM run_events WHERE run_id=? AND event_id>? ORDER BY event_id ASC",
            (run_id, after_id)
        ).fetchall()
        out = []
        for r in rows:
            pl = r["payload"]
            try:
                pl = json.loads(pl)
            except Exception:
                pass
            out.append({"run_id": r["run_id"], "event_id": r["event_id"], "kind": r["kind"], "payload": pl, "created_at": r["created_at"]})
        return out

    def record_audit_event(self, actor: str, assistant_id: str = "", tool: str = "", project_id: str = "",
                           input_hash: str = "", outcome: str = "ok", duration_ms: int = 0) -> dict[str, Any]:
        ev_id = uuid.uuid4().hex
        t = now()
        self.db.execute(
            "INSERT INTO audit_events (id, actor, assistant_id, tool, project_id, input_hash, outcome, duration_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev_id, actor, assistant_id, tool, project_id, input_hash, outcome, int(duration_ms), t)
        )
        self.db.commit()
        return {"id": ev_id, "actor": actor, "assistant_id": assistant_id, "tool": tool, "project_id": project_id,
                "input_hash": input_hash, "outcome": outcome, "duration_ms": duration_ms, "created_at": t}

    def get_audit_events(self, project_id: str = "", assistant_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        clauses, vals = [], []
        if project_id:
            clauses.append("project_id=?")
            vals.append(project_id)
        if assistant_id:
            clauses.append("assistant_id=?")
            vals.append(assistant_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        vals.append(int(limit))
        rows = self.db.execute(f"SELECT * FROM audit_events{where} ORDER BY created_at DESC LIMIT ?", vals).fetchall()
        return [dict(r) for r in rows]

    def create_background_job(self, job_id: str, task_id: str = "", status: str = "running",
                              progress: float = 0.0, last_event_id: int = 0, checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
        t = now()
        cp = json.dumps(checkpoint or {}, ensure_ascii=False)
        self.db.execute(
            "INSERT OR REPLACE INTO background_jobs (id, task_id, status, progress, last_event_id, checkpoint, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, task_id, status, progress, last_event_id, cp, t, t)
        )
        self.db.commit()
        return self.get_background_job(job_id) or {}

    def get_background_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        res = dict(row)
        try:
            res["checkpoint"] = json.loads(res.get("checkpoint") or "{}")
        except Exception:
            res["checkpoint"] = {}
        return res

    def update_background_job(self, job_id: str, status: str = "", progress: float | None = None,
                              last_event_id: int | None = None, checkpoint: dict[str, Any] | None = None) -> dict[str, Any] | None:
        cur = self.get_background_job(job_id)
        if not cur:
            return None
        sets, vals = ["updated_at=?"], [now()]
        if status:
            sets.append("status=?")
            vals.append(status)
        if progress is not None and progress >= 0:
            sets.append("progress=?")
            vals.append(float(progress))
        if last_event_id is not None and last_event_id >= 0:
            sets.append("last_event_id=?")
            vals.append(int(last_event_id))
        if checkpoint is not None:
            sets.append("checkpoint=?")
            vals.append(json.dumps(checkpoint, ensure_ascii=False))
        vals.append(job_id)
        self.db.execute(f"UPDATE background_jobs SET {', '.join(sets)} WHERE id=?", vals)
        self.db.commit()
        return self.get_background_job(job_id)



def extract_ai(db: ResearchDB, task_id: str, paper_id: str, base_url: str, api_key: str, model: str) -> dict[str, Any]:
    """Optional OpenAI-compatible extraction; invalid page/excerpt claims are rejected."""
    paper = db.paper(paper_id)
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": 'Extract materials evidence. Return JSON only: {"evidence":[{"field":"composition|temperature|performance|method|other","value":"","page_no":1,"excerpt":"exact quote"}]}. Every item must quote the source exactly and include its page.'},
            {"role": "user", "content": "\n\n".join(f"[PAGE {p['page_no']}]\n{p['text']}" for p in paper["pages"])[:60000]},
        ],
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    run_id = db.create_run(task_id, "evidence.extract.ai", paper["sha256"])
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            raw = json.loads(res.read().decode())
        content = raw["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```json\s*|```$", "", content, flags=re.I).strip()
        items = json.loads(content).get("evidence", [])
        accepted, rejected = [], []
        for item in items:
            try:
                accepted.append(db.add_evidence(paper_id, item))
            except (KeyError, TypeError, ValueError) as exc:
                rejected.append({"item": item, "reason": str(exc)})
        db.finish_run(run_id, "completed" if accepted else "completed-with-warnings")
        return {"run_id": run_id, "accepted": accepted, "rejected": rejected}
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        db.finish_run(run_id, "failed")
        raise RuntimeError(f"AI extraction failed: {exc}") from exc


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="览籍智研 core")
    parser.add_argument("db")
    parser.add_argument("command", choices=["create-task", "ingest", "extract", "summary", "export", "report"])
    parser.add_argument("args", nargs="*")
    ns = parser.parse_args()
    db = ResearchDB(ns.db)
    try:
        if ns.command == "create-task":
            print(json.dumps({"task_id": db.create_task(*ns.args)}, ensure_ascii=False))
        elif ns.command == "ingest":
            print(json.dumps(db.ingest_pdf(ns.args[0], ns.args[1]), ensure_ascii=False, indent=2))
        elif ns.command == "extract":
            print(json.dumps(db.extract_materials(ns.args[0]), ensure_ascii=False, indent=2))
        elif ns.command == "export":
            print(db.export_evidence(ns.args[0], ns.args[1]))
        elif ns.command == "report":
            print(db.export_report(ns.args[0], ns.args[1]))
        else:
            print(json.dumps(db.summary(ns.args[0]), ensure_ascii=False, indent=2))
    finally:
        db.close()
