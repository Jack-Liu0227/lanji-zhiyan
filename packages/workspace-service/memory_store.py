"""记忆体系：全局/项目两层、可改可删、FTS 检索、确定性合并。

改动前先认这三条口径：

1. **事实进这里，做法进 skill**（capabilities.skills）。同一句话不许两边都写。
2. **记忆不会静默消失**：超预算的条目归档（archived=1），recall 还能查到，只是不再常驻注入。
   老实现是「只取最后 40 条」，早期的结论会无声丢掉 —— 那是这套要替掉的东西。
3. 写入只有两条路：模型调 `memory` 工具、用户纠正时 `capture()` 规则捕获。
   没有 LLM 后台总结（ponytail: 规则捕获已覆盖「用户纠正」这个真痛点；要做语义抽取
   再加一个 background job，别现在猜）。
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = ("fact", "preference", "constraint", "decision", "episode")
KIND_LABEL = {"fact": "事实", "preference": "偏好", "constraint": "约束", "decision": "决定", "episode": "经过"}
BUDGET = {"global": 3000, "project": 8000}          # 字符，不是 token：中文 1 字≈1 token 量级
MERGE_AT = 0.82                                     # 三元组相似度到这就算同一件事

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'project', scope_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'fact', text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'agent', importance INTEGER NOT NULL DEFAULT 1,
  hits INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope, scope_id, archived);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(mem_id UNINDEXED, text, tags, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS chat_fts USING fts5(chat_id UNINDEXED, task_id UNINDEXED, role UNINDEXED,
                                                      content, tokenize='trigram');
"""

_READY: set[str] = set()


# ------------------------------------------------------------------ 底座
def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def ready(db) -> None:
    """建表 + 旧数据搬家 + 索引补齐。幂等；每个库每个进程只跑一次。

    缓存不能只按路径认：同一个路径的库文件可能被删掉重建（测试、换库、恢复备份），
    那样缓存说「建过了」，实际查表就撞 no such table。所以先空查一次表。
    """
    key = str(getattr(db, "path", "") or id(db))
    if key in _READY:
        try:
            db.db.execute("SELECT 1 FROM memory LIMIT 1")
            return
        except sqlite3.OperationalError:
            _READY.discard(key)
    conn = db.db
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO memory (id,scope,scope_id,kind,text,tags,source,importance,"
                 "hits,archived,superseded_by,created_at,updated_at) "
                 "SELECT id,'project',task_id,CASE WHEN kind='short' THEN 'episode' ELSE 'fact' END,"
                 "text,'','import',1,0,0,'',created_at,created_at FROM project_memory "
                 "WHERE TRIM(COALESCE(text,''))<>''")
    conn.execute("INSERT INTO memory_fts (mem_id,text,tags) SELECT m.id,m.text,m.tags FROM memory m "
                 "WHERE m.id NOT IN (SELECT mem_id FROM memory_fts)")
    conn.commit()
    _READY.add(key)


def _similar(a: str, b: str) -> float:
    """同一件事的判定。用 difflib 而不是「三元组重叠」：中文短句（十几个字）三元组集合太小，
    改一个字就把 jaccard 打到 0.5 以下。quick_ratio 先做 O(n) 上界剪枝，长文本也不会退化成 O(n²)。"""
    a, b = _clean(a).casefold(), _clean(b).casefold()
    if not a or not b:
        return 0.0
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.quick_ratio() < MERGE_AT:
        return 0.0
    return matcher.ratio()


def _age_days(stamp: str) -> float:
    try:
        then = datetime.fromisoformat(stamp)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 99.0


def _index(conn, mem_id: str, text: str, tags: str) -> None:
    conn.execute("DELETE FROM memory_fts WHERE mem_id=?", (mem_id,))
    conn.execute("INSERT INTO memory_fts (mem_id,text,tags) VALUES (?,?,?)", (mem_id, text, tags))


def _scope_sql(scope: str, scope_id: str, include_global: bool) -> tuple[str, list]:
    if scope == "global":
        return "scope='global'", []
    if include_global:
        return "(scope='global' OR scope_id=?)", [scope_id]
    return "scope='project' AND scope_id=?", [scope_id]


def _rows(db, sql: str, args: list | tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in db.db.execute(sql, args)]


# ------------------------------------------------------------------ 写
def add(db, text: str, *, scope: str = "project", scope_id: str = "", kind: str = "fact",
        tags: str = "", source: str = "agent", importance: int = 1) -> dict[str, Any]:
    """写一条。同scope里已有同一件事（完全相同或三元组相似）就只加 hits，不重复堆。"""
    ready(db)
    body = _clean(text)
    if not body:
        raise ValueError("记忆内容不能为空")
    kind = kind if kind in KINDS else "fact"
    importance = max(0, min(3, int(importance if importance is not None else 1)))
    scope = "global" if scope == "global" else "project"
    scope_id = "" if scope == "global" else str(scope_id or "")
    conn = db.db
    same = _rows(db, f"SELECT * FROM memory WHERE {_scope_sql(scope, scope_id, False)[0]} "
                     "AND archived=0 ORDER BY importance DESC, updated_at DESC LIMIT 200",
                 _scope_sql(scope, scope_id, False)[1])
    for row in same:
        if row["text"] == body or _similar(row["text"], body) >= MERGE_AT:
            conn.execute("UPDATE memory SET hits=hits+1, updated_at=?, importance=MAX(importance,?) WHERE id=?",
                         (now(), importance, row["id"]))
            conn.commit()
            return {**row, "duplicate": True}
    mem_id = uuid.uuid4().hex
    stamp = now()
    conn.execute("INSERT INTO memory (id,scope,scope_id,kind,text,tags,source,importance,hits,archived,"
                 "superseded_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,0,'',?,?)",
                 (mem_id, scope, scope_id, kind, body, _clean(tags), source, importance, stamp, stamp))
    _index(conn, mem_id, body, _clean(tags))
    conn.commit()
    dump(db, scope_id)
    return {"id": mem_id, "scope": scope, "scope_id": scope_id, "kind": kind, "text": body,
            "tags": _clean(tags), "source": source, "importance": importance, "created_at": stamp}


def resolve(db, mem_id: str = "", old_text: str = "", scope_id: str = "") -> dict[str, Any]:
    """按 id 或按原文片段定位一条（模型常常只记得那句话，不记得 id）。"""
    ready(db)
    if mem_id:
        row = db.db.execute("SELECT * FROM memory WHERE id=?", (mem_id,)).fetchone()
        if not row:
            raise ValueError("没有这条记忆：" + mem_id)
        return dict(row)
    body = _clean(old_text)
    if not body:
        raise ValueError("要给 id 或 old_text")
    for row in _rows(db, "SELECT * FROM memory WHERE (scope_id=? OR scope='global') ORDER BY updated_at DESC",
                     (scope_id,)):
        if body in row["text"]:
            return row
    raise ValueError("找不到包含「%s」的记忆" % body[:30])


def patch(db, mem_id: str = "", old_text: str = "", *, scope_id: str = "", text: str | None = None,
          kind: str | None = None, tags: str | None = None, importance: int | None = None,
          archived: bool | None = None) -> dict[str, Any]:
    """改一条。改正文自动重建索引；改完把旧口令靠 superseded_by 留痕。"""
    row = resolve(db, mem_id, old_text, scope_id)
    conn = db.db
    fields, args, changes = [], [], {}
    for column, value in (("text", _clean(text) if text is not None else None),
                          ("kind", kind if kind in KINDS else None),
                          ("tags", _clean(tags) if tags is not None else None),
                          ("importance", max(0, min(3, int(importance))) if importance is not None else None),
                          ("archived", int(bool(archived)) if archived is not None else None)):
        if value is not None and value != row.get(column):
            fields.append(f"{column}=?")
            args.append(value)
            changes[column] = value
    if not fields:
        return row
    fields.append("updated_at=?")
    args += [now(), row["id"]]
    conn.execute(f"UPDATE memory SET {', '.join(fields)} WHERE id=?", args)
    if text is not None:
        _index(conn, row["id"], _clean(text), changes.get("tags", row.get("tags") or ""))
        _drop_lines(db, row["scope_id"], {row["text"]})     # 旧句子的文件行也别留着（不然会被读回来）
    conn.commit()
    dump(db, row["scope_id"])
    return {**row, **changes}


def _drop_lines(db, scope_id: str, texts: set[str]) -> None:
    """删/改记忆时把文件里对应的那行也去掉 —— 否则下一次 dump 会先把文件行读回来，
    记忆就「删了又长出来」。文件是副本，写到副本上的手改仍会被收编，但删掉就是删掉。"""
    base = db.base_dir(scope_id) if scope_id else None
    if not base or not texts:
        return
    folder = db.pdir(scope_id, "记忆")
    for name in ("长期记忆.md", "短期记忆.md"):
        path = folder / name
        try:
            if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
                continue
            kept = [line for line in path.read_text(encoding="utf-8").splitlines()
                    if not (line.startswith("- ") and line[2:].strip() in texts)]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            continue


def remove(db, mem_id: str = "", old_text: str = "", scope_id: str = "") -> dict[str, Any]:
    row = resolve(db, mem_id, old_text, scope_id)
    db.db.execute("DELETE FROM memory WHERE id=?", (row["id"],))
    db.db.execute("DELETE FROM memory_fts WHERE mem_id=?", (row["id"],))
    db.db.commit()
    _drop_lines(db, row["scope_id"], {row["text"]})
    dump(db, row["scope_id"])
    return {"removed": row["id"], "text": row["text"]}


def apply(db, scope_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """`memory` 工具的入口：一次可以给多条 ops。"""
    if args.get("query"):
        return {"query": args["query"], "hits": search(db, args["query"], scope_id, int(args.get("limit") or 10))}
    done = []
    for op in args.get("ops") or []:
        if not isinstance(op, dict):
            continue
        action = str(op.get("action") or "").lower()
        try:
            if action == "add":
                done.append(add(db, op.get("text", ""), scope=str(op.get("scope") or "project"),
                                scope_id=scope_id, kind=str(op.get("kind") or "fact"),
                                tags=str(op.get("tags") or ""), source="agent",
                                importance=int(op.get("importance") or 1)))
            elif action == "patch":
                done.append(patch(db, str(op.get("id") or ""), str(op.get("old_text") or ""), scope_id=scope_id,
                                  text=op.get("text"), kind=op.get("kind"), tags=op.get("tags"),
                                  importance=op.get("importance"), archived=op.get("archived")))
            elif action in ("remove", "forget"):
                done.append(remove(db, str(op.get("id") or ""), str(op.get("old_text") or ""), scope_id))
            elif action in ("archive", "restore"):
                done.append(patch(db, str(op.get("id") or ""), str(op.get("old_text") or ""),
                                  scope_id=scope_id, archived=(action == "archive")))
            else:
                done.append({"error": "不认识的 action：%s（add/patch/remove/archive/restore）" % action})
        except (ValueError, KeyError) as exc:
            done.append({"error": str(exc)})
    return {"applied": len(done), "results": done, "stats": stats(db, scope_id)}


# ------------------------------------------------------------------ 查
def search(db, query: str, scope_id: str = "", limit: int = 10, include_archived: bool = False) -> list[dict]:
    """FTS5(trigram) + LIKE 合并打分。中文两字词 trigram 打不中，所以两条路都走。"""
    ready(db)
    q = _clean(query)
    if not q:
        return []
    conn = db.db
    where, args = _scope_sql("project", scope_id, True)
    extra = "" if include_archived else " AND archived=0"
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for row in _rows(db, f"SELECT * FROM memory WHERE {where}{extra} ORDER BY updated_at DESC LIMIT 500", args):
        rows[row["id"]] = row
    if len(q) >= 3:
        phrase = '"' + q.replace('"', '""') + '"'
        try:
            for mem_id, bm in conn.execute("SELECT mem_id, bm25(memory_fts) FROM memory_fts "
                                           "WHERE memory_fts MATCH ? LIMIT 200", (phrase,)):
                if mem_id in rows:
                    scores[mem_id] = -float(bm)
        except sqlite3.OperationalError:
            pass
    like = "%" + q + "%"
    for row in rows.values():
        if q.casefold() in row["text"].casefold() or q.casefold() in (row["tags"] or "").casefold():
            scores[row["id"]] = max(scores.get(row["id"], 0.0), 1.0)
    out = []
    for mem_id, base in scores.items():
        row = rows[mem_id]
        row = dict(row)
        row["score"] = round(base + 0.8 * row["importance"] + 0.2 * min(row["hits"], 5)
                             + 1.5 / (1 + _age_days(row["updated_at"]) / 30), 3)
        out.append(row)
    out.sort(key=lambda r: -r["score"])
    for row in out[:limit]:                       # 命中即算被用上：重要性按实际使用走，不靠人点
        conn.execute("UPDATE memory SET hits=hits+1 WHERE id=?", (row["id"],))
    conn.commit()
    return out[:limit]


def _fts_chats(db, query: str, task_id: str, limit: int) -> list[dict]:
    ready(db)
    conn = db.db
    if not conn.execute("SELECT 1 FROM chat_fts LIMIT 1").fetchone():
        conn.execute("INSERT INTO chat_fts (chat_id,task_id,role,content) "
                     "SELECT c.id,c.task_id,c.role,c.content FROM chat c "
                     "WHERE c.id NOT IN (SELECT chat_id FROM chat_fts)")
        conn.commit()
    q = _clean(query)
    hits: dict[str, dict] = {}
    if len(q) >= 3:
        phrase = '"' + q.replace('"', '""') + '"'
        try:
            for chat_id, bm in conn.execute("SELECT chat_id, bm25(chat_fts) FROM chat_fts "
                                            "WHERE chat_fts MATCH ? LIMIT 100", (phrase,)):
                row = conn.execute("SELECT id,task_id,role,content,created_at FROM chat WHERE id=?",
                                   (chat_id,)).fetchone()
                if row and (not task_id or row["task_id"] == task_id):
                    hits[row["id"]] = {**dict(row), "score": -float(bm)}
        except sqlite3.OperationalError:
            pass
    if not hits:                                   # 两字词 / 索引没建：退回 LIKE
        for row in _rows(db, "SELECT id,task_id,role,content,created_at FROM chat WHERE content LIKE ? "
                             + ("AND task_id=? " if task_id else "") + "ORDER BY created_at DESC LIMIT ?",
                         tuple(([("%" + q + "%")] + ([task_id] if task_id else []) + [limit * 3]))):
            hits[row["id"]] = {**row, "score": 1.0}
    out = [{"id": r["id"], "role": r["role"], "created_at": r["created_at"],
            "excerpt": r["content"][:240], "score": round(r["score"], 3)}
           for r in sorted(hits.values(), key=lambda r: -r["score"])[:limit]]
    return out


def recall(db, query: str, scope_id: str = "", limit: int = 8) -> dict[str, Any]:
    """跨层回想：记忆 + 历史对话。笔记/证据仍走 knowledge_query（那里带页码出处）。"""
    return {"query": query,
            "memory": [{k: r[k] for k in ("id", "kind", "text", "tags", "importance", "updated_at", "score")}
                       for r in search(db, query, scope_id, limit)],
            "chats": _fts_chats(db, query, scope_id, limit)}


# ------------------------------------------------------------------ 注入
def _section(db, scope: str, scope_id: str, budget: int) -> tuple[list[str], int]:
    where, args = _scope_sql(scope, scope_id, False)
    rows = _rows(db, f"SELECT * FROM memory WHERE {where} AND archived=0 "
                     "ORDER BY importance DESC, updated_at DESC", args)
    picked, used = [], 0
    for row in rows:
        line = row["text"] if not row["tags"] else f"{row['text']}（{row['tags']}）"
        if used + len(line) > budget:
            continue
        picked.append(line)
        used += len(line)
    return picked, len(rows) - len(picked)


def _import_from_files(db, scope_id: str) -> int:
    """人工直接改 `记忆/长期记忆.md` 的路子留着：文件里 `- ` 开头的行补进库（只插没有的原句）。

    直接走 SQL、不经过 add() —— add() 会回写文件，那样「读文件」和「写文件」会互相踩掉对方。
    """
    base = db.base_dir(scope_id) if scope_id else None
    if not base:
        return 0
    folder = db.pdir(scope_id, "记忆")
    added = 0
    conn = db.db
    for name, kind in (("长期记忆.md", "fact"), ("短期记忆.md", "episode")):
        path = folder / name
        try:
            if not path.is_file() or not path.resolve().is_relative_to(base.resolve()):
                continue
            known = {r["text"] for r in _rows(db, "SELECT text FROM memory WHERE scope_id=?", (scope_id,))}
            for line in path.read_text(encoding="utf-8").splitlines():
                body = line[2:].strip()
                if not line.startswith("- ") or not body or "（暂无）" in body or body in known:
                    continue
                mem_id, stamp = uuid.uuid4().hex, now()
                conn.execute("INSERT INTO memory (id,scope,scope_id,kind,text,tags,source,importance,hits,"
                             "archived,superseded_by,created_at,updated_at) VALUES (?,?,?,?,?,'','import',1,0,0,'',?,?)",
                             (mem_id, "project", scope_id, kind, body, stamp, stamp))
                _index(conn, mem_id, body, "")
                known.add(body)
                added += 1
        except (OSError, ValueError):
            continue
    if added:
        conn.commit()
    return added


def sync_files(db, scope_id: str) -> None:
    _import_from_files(db, scope_id)


def block(db, scope_id: str, now_window: int = 10, line: int = 120) -> str:
    """拼给模型看的记忆块：全局 → 项目 → 窗口外的老对话。超预算的条目只报数，不静默丢。"""
    ready(db)
    try:
        sync_files(db, scope_id)
    except Exception:
        pass
    parts: list[str] = []
    over: list[str] = []
    for scope, title, budget in (("global", "全局记忆（跨项目）", BUDGET["global"]),
                                 ("project", "项目记忆", BUDGET["project"])):
        picked, missed = _section(db, scope, scope_id, budget)
        if picked:
            parts.append(f"【{title}】\n" + "\n".join(f"- {x}" for x in picked))
        if missed:
            over.append(f"{title} 还有 {missed} 条超预算没注入")
    if scope_id:
        try:
            msgs = db.messages(scope_id)
            older = msgs[:-now_window] if len(msgs) > now_window else []
            short = [f"{m['role']}: {' '.join((m['content'] or '').split())[:line]}" for m in older][-20:]
            if short:
                parts.append("【更早的对话（短期记忆）】\n" + "\n".join("- " + x for x in short))
        except Exception:
            pass
    if over:
        parts.append("（" + "；".join(over) + "。要查用 recall，要腾地方先 memory patch/archive 合并）")
    return "\n\n".join(parts)


# ------------------------------------------------------------------ 维护
def all_rows(db, scope_id: str = "", limit: int = 400) -> list[dict[str, Any]]:
    """记忆面板用：全局 + 这个项目的全部条目（归档的排后面）。"""
    ready(db)
    where, args = _scope_sql("project", scope_id, True)
    return _rows(db, f"SELECT * FROM memory WHERE {where} "
                     "ORDER BY archived, importance DESC, updated_at DESC LIMIT ?", args + [limit])


def stats(db, scope_id: str = "") -> dict[str, Any]:
    ready(db)
    def one(scope: str, sid: str) -> dict[str, Any]:
        where, args = _scope_sql(scope, sid, False)
        rows = _rows(db, f"SELECT * FROM memory WHERE {where}", args)
        live = [r for r in rows if not r["archived"]]
        return {"count": len(live), "archived": len(rows) - len(live),
                "chars": sum(len(r["text"]) for r in live),
                "budget": BUDGET[scope], "kinds": _tally(live)}
    return {"global": one("global", ""), "project": one("project", scope_id)}


def _tally(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row["kind"]] = out.get(row["kind"], 0) + 1
    return out


def consolidate(db, scope_id: str = "") -> dict[str, Any]:
    """合并近重复 + 把超预算的尾巴归档。确定性，不叫模型 —— 记忆整理不该有随机性。"""
    ready(db)
    merged = archived = 0
    conn = db.db
    for scope in ("global", "project"):
        where, args = _scope_sql(scope, scope_id, False)
        rows = _rows(db, f"SELECT * FROM memory WHERE {where} AND archived=0 "
                         "ORDER BY importance DESC, updated_at DESC", args)
        kept: list[dict] = []
        for row in rows:
            twin = next((k for k in kept if _similar(k["text"], row["text"]) >= MERGE_AT), None)
            if twin:
                conn.execute("UPDATE memory SET hits=hits+?, archived=1, superseded_by=?, updated_at=? WHERE id=?",
                             (row["hits"] + 1, twin["id"], now(), row["id"]))
                merged += 1
            else:
                kept.append(row)
        used = 0
        for row in kept:
            if used + len(row["text"]) <= BUDGET[scope]:
                used += len(row["text"])
            else:
                conn.execute("UPDATE memory SET archived=1, updated_at=? WHERE id=?", (now(), row["id"]))
                archived += 1
    conn.commit()
    dump(db, scope_id)
    return {"merged": merged, "archived": archived, "stats": stats(db, scope_id)}


def dump(db, scope_id: str = "") -> None:
    """落一份人可读的副本（长期/短期），外加全局记忆。写不进去就算了，库才是真相。"""
    try:
        base = db.base_dir(scope_id) if scope_id else None
        if base:
            _import_from_files(db, scope_id)          # 先收人工改的，再整份重写：别把人的改动冲掉
            folder = db.pdir(scope_id, "记忆")
            long = [r["text"] for r in _rows(db, "SELECT * FROM memory WHERE scope_id=? AND archived=0 "
                                                 "AND kind<>'episode' ORDER BY importance DESC, updated_at DESC",
                                             (scope_id,))]
            short = [r["text"] for r in _rows(db, "SELECT * FROM memory WHERE scope_id=? AND archived=0 "
                                                  "AND kind='episode' ORDER BY updated_at DESC", (scope_id,))]
            _write(folder / "长期记忆.md", "长期记忆", "项目要点与重要结论（自动维护，可直接编辑，下次运行会读回来）。", long)
            _write(folder / "短期记忆.md", "短期记忆", "这个项目的对话内容与细节（按时间顺序，越靠下越新）。", short)
        try:
            import paths
            user_root = Path(paths.USER)
        except ImportError:                          # 自检/单测里没有 workspace-core 的 sys.path
            user_root = Path.home() / ".lanji-zhiyan"
        glob = [r["text"] for r in _rows(db, "SELECT * FROM memory WHERE scope='global' AND archived=0 "
                                             "ORDER BY importance DESC, updated_at DESC")]
        _write(user_root / "记忆" / "全局记忆.md", "全局记忆",
               "跨项目有效的口径与偏好（自动维护，可直接编辑，下次运行会读回来）。", glob)
    except (OSError, ValueError, AttributeError):
        return


def _write(path: Path, title: str, note: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# %s\n\n%s\n\n%s\n" % (title, note, "\n".join(f"- {x}" for x in lines) or "- （暂无）")
    path.write_text(body, encoding="utf-8")


# ------------------------------------------------------------------ 纠正捕获
_CORRECTION = re.compile(r"(不要|别|不准|禁止|必须|一律|统一|以后都|记住|我说过|不是这样|应该用|改成|口径)")


def capture(db, scope_id: str, text: str) -> dict[str, Any] | None:
    """用户当场定的规矩别等下轮：命中纠正口吻就记一条约束（去重靠 add 自己的相似判定）。
    ponytail: 规则命中，不做语义判断；误记的代价是一条可删的约束，漏记的代价是下轮又犯。"""
    body = _clean(text)
    if not scope_id or not body or len(body) > 400:
        return None
    match = _CORRECTION.search(body)
    if not match:
        return None
    sentence = re.split(r"[。！？!?\n]", body)
    line = next((s for s in sentence if match.group(1) in s), body)
    return add(db, "用户口径：" + line.strip()[:160], scope_id=scope_id, kind="constraint",
               source="auto", importance=2)


# ------------------------------------------------------------------ 自检
if __name__ == "__main__":                            # python memory_store.py
    import sqlite3 as _sqlite

    class _DB:                                        # 最小替身：真跑 sqlite，不拉起整个 WorkflowDB
        def __init__(self):
            self.path = ":memory:"
            self.db = _sqlite.connect(":memory:")
            self.db.row_factory = _sqlite.Row
            self.db.execute("CREATE TABLE project_memory (id TEXT PRIMARY KEY, task_id TEXT, kind TEXT, "
                            "text TEXT, created_at TEXT)")
            self.db.execute("INSERT INTO project_memory VALUES ('p1','t1','long','旧库里的结论',?)", (now(),))
        def base_dir(self, _tid=""):
            return None
        def messages(self, _tid=""):
            return []

    db = _DB()
    ready(db)
    assert stats(db, "t1")["project"]["count"] == 1, "旧数据没搬过来"
    a = add(db, "导师要求中文稿不用小括号", scope_id="t1", kind="constraint", importance=3)
    assert add(db, "导师要求中文稿不用小括号", scope_id="t1")["duplicate"], "同一条没去重"
    assert search(db, "小括号", "t1")[0]["id"] == a["id"], "三字以上检索失效"
    assert search(db, "小括", "t1")[0]["id"] == a["id"], "两字检索失效"
    assert not search(db, "小括号", "t2"), "串项目了"
    patch(db, old_text="小括号", scope_id="t1", text="导师要求中文稿不用小括号、不用分号")
    assert "分号" in search(db, "分号", "t1")[0]["text"], "改正文没重建索引"
    remove(db, old_text="分号", scope_id="t1")
    assert not search(db, "分号", "t1"), "删了还能搜到"
    add(db, "配图用 Okabe-Ito 色盲安全配色", scope_id="t1", kind="preference")
    add(db, "访谈结论：单卡 8G 显存，训练只能小批量", scope_id="t2", kind="decision")
    assert add(db, "访谈结论：单卡 8G 显存，训练只能小批", scope_id="t2")["duplicate"], "写入时没去重"
    for i in range(60):
        add(db, "条目%d：%s" % (i, chr(0x4e00 + i * 5) * 200), scope_id="t2", importance=1)
    out = consolidate(db, "t2")
    assert out["archived"] > 0, "超预算没归档"
    assert out["stats"]["project"]["chars"] <= BUDGET["project"], "预算没兜住"
    assert search(db, "8G", "t2", include_archived=True), "归档后连检索也查不到了"
    for i in range(2):                       # 绕过 add 直接塞两条近重复，验证 consolidate 的兜底合并
        db.db.execute("INSERT INTO memory (id,scope,scope_id,kind,text,tags,source,importance,hits,archived,"
                      "superseded_by,created_at,updated_at) VALUES (?,?,?,?,?,'','import',1,0,0,'',?,?)",
                      ("dup%d" % i, "project", "t3", "fact", "同一件事换了个说法版本" + ("甲乙"[i]), now(), now()))
    db.db.commit()
    assert consolidate(db, "t3")["merged"] == 1, "consolidate 没合并近重复"
    got = capture(db, "t1", "以后都按这个口径来，不要自己改数字")
    assert got and got["kind"] == "constraint", "纠正没捕获"
    assert capture(db, "t1", "今天天气不错") is None, "没纠正也记了"
    add(db, "全局偏好：回复先给结论", scope="global", kind="preference")
    ctx = block(db, "t1")
    assert "全局记忆" in ctx and ctx.index("全局记忆") < ctx.index("项目记忆"), "注入顺序不对"
    print("[OK] 记忆体系自检通过", stats(db, "t1"))
