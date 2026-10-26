# -*- coding: utf-8 -*-
"""语义检索层：分块 → 嵌入 → 双路召回（FTS5 trigram + 向量余弦）→ RRF 融合。

设计口径（改动前先读这四条）：

1. 索引是派生数据。内容哈希 + 模型名都没变就跳过；笔记修改或删除会作废旧块，
   下次 sync 再补齐当前内容，避免旧材料继续参与检索。
2. 页码跟着块走。论文按页切块，命中就能回指「文件名 p.N」；这是科研检索的硬要求，
   比相似度多零点零几重要得多。
3. 检索恒有两条腿。词法是 FTS5 trigram（中文可切），向量是 numpy 余弦。
   嵌入服务挂了/没索引时词法单独跑，调用方不报错，只看 mode 字段。
4. 不引第三方向量库。5800 块 × 1024 维 × float32 ≈ 24MB，一次点积 <10ms，
   faiss/chroma 在这个量级只增加故障面。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    import numpy as np
except ImportError:                                     # pragma: no cover - 环境缺依赖时给出可执行提示
    np = None                                           # type: ignore[assignment]

CHUNK_CHARS = 900           # 单块字符数：bge-m3 里约 250 token，够一段完整论证
CHUNK_OVERLAP = 150         # 重叠：切在句子中间时上下文不断裂
MIN_CHUNK = 80              # 太碎的行（页眉/页脚/参考文献序号）不值得占一块
EMBED_BATCH = 8
RRF_K = 60                  # 倒数排名融合常数，业界默认值，不调
DEFAULT_BASE = "http://127.0.0.1:11434/v1"      # 本机 ollama 的 OpenAI 兼容口
DEFAULT_MODEL = "bge-m3"

_LOCK = threading.Lock()
_MATRIX_CACHE: dict[tuple[str, str], tuple[tuple[int, str], list[sqlite3.Row], Any]] = {}


class EmbedError(RuntimeError):
    """嵌入服务不可用。调用方应当退回词法检索，而不是把异常抛给用户。"""


# ---------------------------------------------------------------- 配置

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def config(db: Any) -> dict[str, str]:
    """嵌入端点配置：环境变量 > 应用内设置 > 内置默认（本机 ollama bge-m3）。

    换模型不用改代码：模型名参与台账比对，改了就自动全量重嵌。
    """
    out = {"base": DEFAULT_BASE, "model": DEFAULT_MODEL, "key": ""}
    try:
        row = db.db.execute("SELECT data FROM service_settings WHERE service='semantic' LIMIT 1").fetchone()
        if row and row[0]:
            data = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
            for k in ("base", "model", "key"):
                if str(data.get(k) or "").strip():
                    out[k] = str(data[k]).strip()
    except (sqlite3.Error, ValueError):
        pass
    for env, k in (("LANJI_EMBED_BASE", "base"), ("LANJI_EMBED_MODEL", "model"), ("LANJI_EMBED_KEY", "key")):
        if _env(env):
            out[k] = _env(env)
    out["base"] = out["base"].rstrip("/")
    return out


# ---------------------------------------------------------------- 嵌入

def embed(texts: list[str], cfg: dict[str, str] | None = None, timeout: int = 120) -> Any:
    """OpenAI 兼容 /embeddings（ollama、智谱、OpenAI 都是这个形状）。返回 L2 归一化矩阵。"""
    if np is None:
        raise EmbedError("缺少 numpy：pip install numpy（语义检索的向量运算依赖它）")
    cfg = cfg or {"base": DEFAULT_BASE, "model": DEFAULT_MODEL, "key": ""}
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    url = cfg["base"] + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if cfg.get("key"):
        headers["Authorization"] = "Bearer " + cfg["key"]
    rows: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        part = texts[i:i + EMBED_BATCH]
        rows.extend(_embed_batch(url, headers, cfg["model"], part, timeout))
    if not rows:
        raise EmbedError("嵌入服务返回空结果")
    mat = np.asarray(rows, dtype=np.float32)
    norm = np.linalg.norm(mat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return mat / norm


def _embed_batch(url: str, headers: dict[str, str], model: str, part: list[str], timeout: int) -> list[list[float]]:
    body = json.dumps({"model": model, "input": part}).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with _open(req, timeout) as res:
                data = json.loads(res.read().decode("utf-8", "replace"))
            got = _parse_embeddings(data, len(part))
            if got:
                return got
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == 1:
                raise EmbedError(f"嵌入服务不可用（{url}）：{exc}") from exc
            time.sleep(1.5)
        except EmbedError:
            if attempt == 1:
                raise
            time.sleep(1.0)
    raise EmbedError(f"嵌入服务不可用（{url}）")


def _open(req: Any, timeout: int) -> Any:
    """复用 workflow.open_url 的代理探测：代理没开时直连，别把本机 ollama 也送进代理。"""
    try:
        import workflow
        return workflow.open_url(req, timeout=timeout)
    except Exception:
        return urllib.request.urlopen(req, timeout=timeout)


def _parse_embeddings(data: dict[str, Any], expect: int) -> list[list[float]]:
    """吃两种形状：OpenAI 的 data[].embedding，和 ollama 原生 embeddings[][]。"""
    if isinstance(data.get("data"), list):
        items = sorted(data["data"], key=lambda d: int(d.get("index", 0) or 0))
        out = [d.get("embedding") or [] for d in items]
    elif isinstance(data.get("embeddings"), list):
        out = list(data["embeddings"])
    elif isinstance(data.get("embedding"), list):
        out = [data["embedding"]]
    else:
        raise EmbedError("嵌入服务返回格式不认识：%s" % str(data)[:200])
    if len(out) != expect:
        raise EmbedError("嵌入条数不符：要 %d 条，回了 %d 条" % (expect, len(out)))
    return [list(map(float, v)) for v in out]


# ---------------------------------------------------------------- 分块

_SENT = re.compile(r"(?<=[。！？；.!?;])\s*")
_REF_LINE = re.compile(r"^\s*(\[\d+\]|\d{1,3}[.)]\s|[A-Z][a-z]+,\s+[A-Z]\.)")
_REF_INLINE = re.compile(r"\[\d{1,3}\]")
_NUM_TOKEN = re.compile(r"[-+]?\d+(?:[.,]\d+)?%?")
_ACK_HEAD = re.compile(r"^(acknowledg|we thank|this work was (supported|funded)|the authors (thank|gratefully))", re.I)
CHUNKER_VERSION = "3"        # 分块/过滤规则改了就把这个 +1：参与哈希，旧块自动重嵌


def _worth_indexing(text: str) -> bool:
    """把「字面上什么都能匹配、语义上什么都不像」的块挡在索引外。

    三类：参考文献清单、致谢/基金、数值表格页。PDF 抽出来的参考文献常常是一整段
    没有换行的文本（换行判据失效），所以按行 + 按密度两道判。
    """
    body = text.strip()
    if not body:
        return False
    alnum = sum(1 for ch in body if ch.isalnum())
    if alnum / len(body) < 0.45:                 # 公式/符号/抽坏的版面
        return False
    if _ACK_HEAD.match(body):
        return False
    if len(_REF_INLINE.findall(body)) * 100 / len(body) > 1.0:      # 每 100 字超过 1 个 [n]
        return False
    tokens = body.split()
    if len(tokens) >= 25:                        # 短块里数字占比天然高（「2% 最优，91%」），不判
        numeric = sum(1 for t in tokens if _NUM_TOKEN.fullmatch(t))
        if numeric / len(tokens) > 0.30:         # 数值 > 30% = 表格堆，不是论述
            return False
    lines = [ln for ln in body.split("\n") if ln.strip()]
    if len(lines) >= 6:
        refs = sum(1 for ln in lines if _REF_LINE.match(ln))
        if refs / len(lines) > 0.4:
            return False
    return True


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按句子攒块，攒满 size 就切，尾部 overlap 字符带进下一块。

    不按固定窗口硬切：科研文本里一句话被劈两半，向量就废了。
    """
    body = (text or "").replace("\r", "\n").strip()
    if not body:
        return []
    if len(body) <= size:
        return [body] if len(body) >= MIN_CHUNK else ([body] if len(body) > 20 else [])
    out: list[str] = []
    buf = ""
    for sent in _SENT.split(body):
        if not sent:
            continue
        while len(sent) > size:                          # 单句超长（表格/参考文献行）：退化成硬切
            if buf:
                out.append(buf)
                buf = ""
            out.append(sent[:size])
            sent = sent[size - overlap:]
        if len(buf) + len(sent) <= size:
            buf += sent
        else:
            if buf:
                out.append(buf)
            buf = buf[-overlap:] + sent if overlap else sent
    if buf.strip():
        out.append(buf)
    return [c.strip() for c in out if len(c.strip()) >= MIN_CHUNK]


# ---------------------------------------------------------------- 源枚举

def _sources(conn: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    """所有值得进索引的东西。一篇论文 = 一页一块，页码原样保留。"""
    import asset_service
    papers = conn.execute("""
        SELECT p.id, p.task_id, p.filename, COALESCE(m.title,'') AS meta_title
        FROM papers p LEFT JOIN paper_meta m ON m.paper_id = p.id
    """).fetchall()
    for p in papers:
        pages = conn.execute("SELECT page_no, text FROM paper_pages WHERE paper_id=? ORDER BY page_no",
                             (p["id"],)).fetchall()
        title = (p["meta_title"] or "").strip() or (p["filename"] or "").strip()
        yield {"kind": "paper", "id": p["id"], "project_id": p["task_id"] or "", "title": title,
               "label": (p["filename"] or "").strip() or title,
               "parts": [(int(r["page_no"]), r["text"] or "") for r in pages]}

    for n in conn.execute("SELECT id, task_id, title, markdown FROM notes").fetchall():
        if asset_service.is_instruction_note(conn, n["task_id"], n["id"], n["markdown"]):
            continue
        yield {"kind": "note", "id": n["id"], "project_id": n["task_id"] or "",
               "title": (n["title"] or "").strip(), "label": (n["title"] or "").strip(),
               "parts": [(0, "# %s\n%s" % (n["title"] or "", n["markdown"] or ""))]}

    for n in conn.execute("SELECT id, task_id, title, content FROM md_notes").fetchall():
        if asset_service.is_instruction_note(conn, n["task_id"], n["id"], n["content"]):
            continue
        yield {"kind": "md_note", "id": n["id"], "project_id": n["task_id"] or "",
               "title": (n["title"] or "").strip(), "label": (n["title"] or "").strip(),
               "parts": [(0, "# %s\n%s" % (n["title"] or "", n["content"] or ""))]}

    for e in conn.execute("""
        SELECT e.id, e.paper_id, e.page_no, e.field, e.value, e.excerpt, p.task_id, p.filename
        FROM evidence e JOIN papers p ON p.id = e.paper_id
    """).fetchall():
        body = "字段：%s\n值：%s\n原文：%s" % (e["field"] or "", e["value"] or "", e["excerpt"] or "")
        label = "%s p.%s %s" % (e["filename"] or "", e["page_no"], e["field"] or "")
        yield {"kind": "evidence", "id": e["id"], "project_id": e["task_id"] or "",
               "title": label.strip(), "label": label.strip(),
               "parts": [(int(e["page_no"] or 0), body)]}


def _source_hash(src: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(src["kind"].encode())
    h.update(src["id"].encode())
    h.update(CHUNKER_VERSION.encode())
    for page_no, text in src["parts"]:
        h.update(b"\x00%d\x00" % int(page_no))
        h.update((text or "").encode("utf-8", "replace"))
    return h.hexdigest()


# ---------------------------------------------------------------- 建索引

def sync(db: Any, limit: int = 0, progress: Any = None) -> dict[str, Any]:
    """增量建索引：哈希没变就跳过。可反复跑，幂等。

    limit>0 时最多处理这么多个源（后台预热和测试都靠它分片）。
    """
    cfg = config(db)
    conn = db.db
    prune_orphans(db)
    conn.commit()
    stats = {"scanned": 0, "indexed": 0, "skipped": 0, "chunks": 0, "failed": 0,
             "model": cfg["model"], "errors": []}
    state = {(r["source_kind"], r["source_id"]): (r["content_hash"], r["model"])
             for r in conn.execute("SELECT source_kind, source_id, content_hash, model FROM sem_state")}
    done = 0
    for src in _sources(conn):
        if limit and done >= limit:
            break
        stats["scanned"] += 1
        h = _source_hash(src)
        prev = state.get((src["kind"], src["id"]))
        if prev and prev[0] == h and prev[1] == cfg["model"]:
            stats["skipped"] += 1
            continue
        try:
            n = _index_source(conn, src, h, cfg)
        except EmbedError as exc:
            stats["failed"] += 1
            stats["errors"].append(str(exc))
            if len(stats["errors"]) >= 3:               # 连续失败说明服务挂了，别把 72 篇都试一遍
                break
            continue
        stats["indexed"] += 1
        stats["chunks"] += n
        done += 1
        conn.commit()                                   # 一个源一个事务：15 分钟的全量 backfill 中断也有进度
        if progress:
            progress(stats)
    conn.commit()
    with _LOCK:
        _MATRIX_CACHE.clear()                           # 写完就作废旧矩阵缓存
    return stats


def _index_source(conn: sqlite3.Connection, src: dict[str, Any], content_hash: str, cfg: dict[str, str]) -> int:
    chunks: list[tuple[int, int, str]] = []             # (page_no, ord, text)
    for page_no, text in src["parts"]:
        for i, piece in enumerate(chunk_text(text)):
            if _worth_indexing(piece):
                chunks.append((int(page_no), i, piece))
    mat = embed([c[2] for c in chunks], cfg) if chunks else None
    if chunks and mat.shape[0] != len(chunks):
        raise EmbedError("嵌入条数与块数不符")
    # Acquire the SQLite write transaction before checking whether a source was
    # deleted while the embedding request was in flight.
    _drop_source(conn, src["kind"], src["id"])
    if not _source_exists(conn, src["kind"], src["id"], src["project_id"]):
        conn.execute("DELETE FROM sem_state WHERE source_kind=? AND source_id=?", (src["kind"], src["id"]))
        return 0
    if not chunks:
        conn.execute("""INSERT OR REPLACE INTO sem_state
            (source_kind, source_id, project_id, content_hash, model, dim, chunks, indexed_at)
            VALUES (?,?,?,?,?,0,0,?)""",
                     (src["kind"], src["id"], src["project_id"], content_hash, cfg["model"], _now()))
        return 0
    dim = int(mat.shape[1])
    now_iso = _now()
    for (page_no, ordv, text), vec in zip(chunks, mat):
        cid = "%s:%s:%d:%d" % (src["kind"], src["id"], page_no, ordv)
        cur = conn.execute("""INSERT INTO sem_chunks
            (id, source_kind, source_id, project_id, page_no, ord, title, text, model, dim, vec, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (cid, src["kind"], src["id"], src["project_id"], page_no, ordv,
                            src["title"], text, cfg["model"], dim, vec.astype(np.float32).tobytes(), now_iso))
        conn.execute("INSERT INTO sem_fts (rowid, text) VALUES (?,?)", (cur.lastrowid, text))
    conn.execute("""INSERT OR REPLACE INTO sem_state
        (source_kind, source_id, project_id, content_hash, model, dim, chunks, indexed_at)
        VALUES (?,?,?,?,?,?,?,?)""",
                 (src["kind"], src["id"], src["project_id"], content_hash, cfg["model"], dim, len(chunks), now_iso))
    return len(chunks)


def _drop_source(conn: sqlite3.Connection, kind: str, sid: str) -> None:
    conn.execute("DELETE FROM sem_fts WHERE rowid IN "
                 "(SELECT rowid FROM sem_chunks WHERE source_kind=? AND source_id=?)", (kind, sid))
    conn.execute("DELETE FROM sem_chunks WHERE source_kind=? AND source_id=?", (kind, sid))


def invalidate_source(db: Any, kind: str, sid: str) -> None:
    """Remove obsolete derived content; the next sync can index the current row."""
    _drop_source(db.db, kind, sid)
    db.db.execute("DELETE FROM sem_state WHERE source_kind=? AND source_id=?", (kind, sid))
    with _LOCK:
        _MATRIX_CACHE.clear()


def invalidate_project(db: Any, project_id: str) -> None:
    db.db.execute("DELETE FROM sem_fts WHERE rowid IN (SELECT rowid FROM sem_chunks WHERE project_id=?)", (project_id,))
    db.db.execute("DELETE FROM sem_chunks WHERE project_id=?", (project_id,))
    db.db.execute("DELETE FROM sem_state WHERE project_id=?", (project_id,))
    with _LOCK:
        _MATRIX_CACHE.clear()


def _source_exists(conn: sqlite3.Connection, kind: str, sid: str, project_id: str) -> bool:
    if project_id and not conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        return False
    if kind == "evidence":
        return bool(conn.execute("SELECT 1 FROM evidence e JOIN papers p ON p.id=e.paper_id "
                                 "WHERE e.id=? AND COALESCE(p.task_id,'')=?", (sid, project_id)).fetchone())
    table = {"paper": "papers", "note": "notes", "md_note": "md_notes"}.get(kind)
    if kind in ("note", "md_note"):
        import asset_service
        field = "markdown" if kind == "note" else "content"
        row = conn.execute(f"SELECT {field} FROM {table} WHERE id=? AND COALESCE(task_id,'')=?", (sid, project_id)).fetchone()
        return bool(row and not asset_service.is_instruction_note(conn, project_id, sid, row[0]))
    return bool(table and conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND COALESCE(task_id,'')=?",
                                       (sid, project_id)).fetchone())


def prune_orphans(db: Any) -> None:
    sources = db.db.execute("SELECT source_kind,source_id,project_id FROM sem_state UNION "
                            "SELECT source_kind,source_id,project_id FROM sem_chunks").fetchall()
    for row in sources:
        if not _source_exists(db.db, row["source_kind"], row["source_id"], row["project_id"] or ""):
            invalidate_source(db, row["source_kind"], row["source_id"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- 检索

def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    """源数量用 COUNT 数，不要枚举 _sources（那会把 583 万字页面文本读一遍，
    而 status 在每次检索前都会被调）。"""
    return {
        "paper": conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
        "note": conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
        "md_note": conn.execute("SELECT COUNT(*) FROM md_notes").fetchone()[0],
        "evidence": conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
    }


def status(db: Any) -> dict[str, Any]:
    conn = db.db
    cfg = config(db)
    chunks = conn.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0]
    dim = conn.execute("SELECT COALESCE(MAX(dim),0) FROM sem_chunks").fetchone()[0]
    model = conn.execute("SELECT COALESCE(MAX(model),'') FROM sem_chunks").fetchone()[0]
    by_kind = {r[0]: r[1] for r in conn.execute("SELECT source_kind, COUNT(*) FROM sem_chunks GROUP BY 1")}
    return {"chunks": int(chunks), "dim": int(dim), "model": model or cfg["model"],
            "endpoint": cfg["base"], "by_kind": by_kind,
            "sources_total": sum(_counts(conn).values()),
            "sources_indexed": conn.execute("SELECT COUNT(*) FROM sem_state WHERE chunks > 0").fetchone()[0]}


def _project_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM research_tasks")}


def _matrix(conn: sqlite3.Connection, db_path: str, model: str, dim: int) -> tuple[list[sqlite3.Row], Any]:
    """整库向量矩阵，按 (行数, 最近写入时间) 判断是否重建。

    ponytail: 全量装进内存做暴力点积。万级块 <10ms；真涨到十万块再换 ANN 索引。
    """
    stamp = (conn.execute("SELECT COUNT(*) FROM sem_chunks").fetchone()[0],
             conn.execute("SELECT COALESCE(MAX(created_at),'') FROM sem_chunks").fetchone()[0])
    key = (db_path, model)
    with _LOCK:
        hit = _MATRIX_CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
    rows = conn.execute("""SELECT rowid, id, source_kind, source_id, project_id, page_no, title, text, vec
        FROM sem_chunks WHERE model=? AND dim=? ORDER BY rowid""", (model, dim)).fetchall()
    keep: list[sqlite3.Row] = []
    vecs: list[Any] = []
    for r in rows:
        if not r["vec"]:
            continue
        keep.append(r)
        vecs.append(np.frombuffer(r["vec"], dtype=np.float32))
    mat = np.vstack(vecs) if vecs else np.zeros((0, dim), dtype=np.float32)
    with _LOCK:
        _MATRIX_CACHE[key] = (stamp, keep, mat)
    return keep, mat


_SCOPE_SQL = " AND project_id IN (%s)"


def search_vector(db: Any, query: str, project_ids: list[str] | None = None, k: int = 30) -> list[dict[str, Any]]:
    """向量召回。空库/嵌入挂了都返回空表，绝不让上层崩。"""
    q = (query or "").strip()
    if not q or np is None:
        return []
    cfg = config(db)
    st = status(db)
    if not st["chunks"]:
        return []
    try:
        qv = embed([q], cfg)[0]
    except EmbedError:
        return []
    conn = db.db
    keep, mat = _matrix(conn, str(db.path), st["model"], int(qv.shape[0]))
    if not keep:
        return []
    scores = mat @ qv.astype(np.float32)
    order = np.argsort(-scores)
    names = _project_names(conn)
    out: list[dict[str, Any]] = []
    scope = set(project_ids) if project_ids is not None else None
    for idx in order:
        row = keep[int(idx)]
        if not _source_exists(conn, row["source_kind"], row["source_id"], row["project_id"] or ""):
            continue
        if scope is not None and (row["project_id"] or "") not in scope:
            continue
        out.append(_hit(row, float(scores[int(idx)]), names))
        if len(out) >= k:
            break
    return out


def _hit(row: sqlite3.Row, score: float, names: dict[str, str]) -> dict[str, Any]:
    page_no = int(row["page_no"] or 0)
    title = row["title"] or ""
    body = row["text"] or ""
    context = body if len(body) <= 480 else body[:480].rstrip() + " …"   # 20 条 × 900 字会吃掉模型上下文
    hit = {"kind": row["source_kind"], "id": row["source_id"], "project": names.get(row["project_id"], ""),
           "title": title, "context": context, "score": round(float(score), 4)}
    if page_no:
        hit["page_no"] = page_no
        hit["citation"] = "%s p.%d" % (title, page_no)
    return hit


def lexical(db: Any, query: str, project_ids: list[str] | None = None, k: int = 30) -> list[dict[str, Any]]:
    """词法召回。≥3 字符走 FTS5 trigram（中文可切、带 bm25 排序）；
    更短的查（「掺杂」「层状」）trigram 切不出 token，退回 LIKE 子串扫。"""
    q = (query or "").strip()
    if not q or k <= 0 or project_ids == []:
        return []
    conn = db.db
    names = _project_names(conn)
    scope = ""
    params = []
    if project_ids is not None:
        params = list(dict.fromkeys(project_ids))
        scope = " AND COALESCE(c.project_id,'') IN (%s)" % ",".join("?" for _ in params)
    rows: list[sqlite3.Row] = []
    terms = [t for t in re.split(r"[\s,，、;；:：()（）\[\]【】]+", q) if len(t) >= 3]
    if terms:
        match = " OR ".join('"%s"' % t.replace('"', '""') for t in terms)
        try:
            rows = conn.execute("""
                SELECT c.* FROM sem_fts f JOIN sem_chunks c ON c.rowid = f.rowid
                WHERE sem_fts MATCH ?""" + scope + " ORDER BY bm25(sem_fts) LIMIT ?",
                                [match, *params, k]).fetchall()
        except sqlite3.Error:
            rows = []
    if not rows:
        rows = conn.execute("SELECT c.* FROM sem_chunks c WHERE c.text LIKE ?" + scope + " ORDER BY length(c.text) LIMIT ?",
                            ["%" + q + "%", *params, k]).fetchall()
    out: list[dict[str, Any]] = []
    for rank, row in enumerate(rows):
        if not _source_exists(conn, row["source_kind"], row["source_id"], row["project_id"] or ""):
            continue
        hit = _hit(row, 1.0 / (rank + 1), names)
        hit["lexical"] = True
        out.append(hit)
        if len(out) >= k:
            break
    return out


def hybrid(db: Any, query: str, project_ids: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
    """双路召回 + RRF 融合。词法挂了还能返回向量结果，反之亦然；两路都空就是真没有。

    同一个来源页可能贡献多个块，**不能把每个块的排名分加起来**：那样块多的页白捡便宜。
    每种召回各取该页的最好那一次排名，两路再相加。
    """
    vec = search_vector(db, query, project_ids, k=30)
    lex = lexical(db, query, project_ids, k=30)
    fused: dict[tuple[str, str], dict[str, Any]] = {}

    def _acc(modality: str, hits: list[dict[str, Any]]) -> None:
        for rank, hit in enumerate(hits):
            key = (hit["kind"], hit["id"] + ":%s" % hit.get("page_no", 0))
            item = fused.setdefault(key, dict(hit, _gain={}, matched=[]))
            gain = 1.0 / (RRF_K + rank + 1)
            item["_gain"][modality] = max(item["_gain"].get(modality, 0.0), gain)
            if modality not in item["matched"]:
                item["matched"].append(modality)

    _acc("vector", vec)
    _acc("lexical", lex)
    hits = sorted(fused.values(), key=lambda h: -sum(h["_gain"].values()))[:limit]
    for h in hits:
        h["rrf"] = round(sum(h.pop("_gain").values()), 5)
        h.pop("lexical", None)
    mode = "hybrid" if (vec and lex) else ("vector" if vec else ("lexical" if lex else "empty"))
    return {"query": query, "hits": hits, "total": len(hits), "mode": mode,
            "counts": {"vector": len(vec), "lexical": len(lex)}}


# ---------------------------------------------------------------- 预热

def warm(db_path: str, limit: int = 0, idle: float = 0.0) -> dict[str, Any]:
    """后台预热用：自己开一条连接（主连接是 check_same_thread 的，不能跨线程借）。

    idle>0 时每处理完一个源歇一下，别跟正在跑的 agent 抢 IO。
    """
    import paths                                     # noqa: F401 - 确保 sys.path 已就位
    from workflow_db import WorkflowDB
    with WorkflowDB(db_path) as dbb:
        def _tick(_stats: dict[str, Any]) -> None:
            if idle:
                time.sleep(idle)
        return sync(dbb, limit=limit, progress=_tick)


_WARMING: set[str] = set()


def warm_async(db_path: str) -> bool:
    """起一个后台线程做增量建索引（同一进程同一个库只起一次）。

    返回 False 表示已经有线程在跑。启动时调一次即可；新论文入库后下次启动自动补。
    """
    key = str(db_path)
    with _LOCK:
        if key in _WARMING:
            return False
        _WARMING.add(key)

    def _run() -> None:
        try:
            stats = warm(key, idle=0.01)
            print("[semantic] 索引完成：%s" % {k: stats[k] for k in ("indexed", "skipped", "chunks", "failed")})
        except Exception as exc:                       # 索引失败不能让服务起不来
            print("[semantic] 索引失败：%s" % exc)
        finally:
            with _LOCK:
                _WARMING.discard(key)

    threading.Thread(target=_run, daemon=True, name="semantic-warm").start()
    return True
