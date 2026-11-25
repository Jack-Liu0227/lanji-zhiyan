"""「论文翻译」这一页的后端：PDF 文件、元数据抽取、BabelDOC 翻译任务、中文笔记。

三件事各自独立，都不动业务库结构（只读 papers 表 + 复用 md_notes）：
  · pdf_path(db, pid)        -> 找到那篇 PDF 的真实路径（来源是我们自己的入库记录）
  · paper_meta(db, pid)      -> DOI / 题目 / 关键词 / 发表时间，取不到给 ""（前端显示「暂无信息」）
  · start(db, ...)           -> 后台跑 BabelDOC，合并译文并持久化已完成页码
  · note_from_translation()  -> 把译文（mono.pdf）逐页文字抽出来，建成**绑定到项目**的笔记
"""
from __future__ import annotations

import pdfio            # PDF 读写（pypdf/pdfplumber，见 pdfio.py）
import json
from hashlib import sha256
import lowbox          # 低完整性（Low IL）子进程：写入范围由内核限制
import paths
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# BabelDOC 实测：模型/字体走本机缓存，只有 tiktoken 小文件首次要联网（已灌进缓存）
# 优先直跑应用执行器目录里的 exe：`uv tool run` 要写 %LOCALAPPDATA%\uv\cache，
# 在低完整性沙箱里直接 "Failed to initialize cache / 拒绝访问"；直跑 exe 就可以进沙箱。
MANAGED_BABELDOC = paths.EXECUTORS / "babeldoc" / "Scripts" / "babeldoc.exe"
_LEGACY_BABELDOC = Path(os.environ.get("APPDATA", "")) / "uv" / "tools" / "babeldoc" / "Scripts" / "babeldoc.exe"
BABELDOC_EXE = MANAGED_BABELDOC if MANAGED_BABELDOC.is_file() or not _LEGACY_BABELDOC.is_file() else _LEGACY_BABELDOC
BABELDOC_CMD = [str(BABELDOC_EXE)] if BABELDOC_EXE.is_file() else ["uv", "tool", "run", "--with", "bitstring<5", "babeldoc"]


def babeldoc_command() -> list[str]:
    # Setup can finish while the desktop service is already running.
    native = MANAGED_BABELDOC if MANAGED_BABELDOC.is_file() else BABELDOC_EXE
    return [str(native)] if native.is_file() else BABELDOC_CMD

# 从 pythonw（无控制台）里起控制台程序，Windows 会给子进程新建一个黑窗；
# 用户一关这个黑窗，子进程就收到控制台事件、以 0xC000013A 退出 —— 表现就是
# 「点翻译弹黑窗然后卡住」。所以子进程必须不创建控制台。
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0   # 控制台事件不再传播给子进程
STALL_SECONDS = 900                        # 多久没有任何输出就判定卡死（版式识别偶尔会安静几分钟）
# 实测（2026-10，DeepSeek v4-flash，同一篇 38 页真跑）：
#  ① thinking 默认开时 reasoning token 会吃掉 babeldoc 硬编码的 max_tokens=2048，正文 JSON 被挤空
#    → 整批退回「逐段单句翻译」（44s vs 4s，fallback 30/31 → 0/31）；
#  ② 段并行是版式识别的负收益：4 段并行版式 73s/13 页，单进程 38 页只要 29s ——
#    babeldoc 每个进程都按满核跑 ONNX，段一多互相抢，所以宁可少切段。
QPS_TOTAL = int(os.environ.get("RW_TRANS_QPS", "16"))      # 全局并发上限（DeepSeek 的并发上限跟余额挂钩，别超）
CHUNK_PARALLEL = int(os.environ.get("RW_TRANS_PARALLEL", "3"))   # 同时跑几段
CHUNK_MAX = int(os.environ.get("RW_TRANS_CHUNKS", "4"))           # 整篇切几段（1 = 第 1 页单跑 + 其余一整段）
JOBS: dict[str, dict[str, Any]] = {}          # paper_id -> 翻译任务状态（进程内，够用）
_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()      # 多段并行时日志文件是共享的

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
KW_RE = re.compile(r"(?i)key\s?words?\s*[:：]?\s*(.{5,240})")


# ---------------- PDF 文件 ----------------
def pdf_path(db: Any, paper_id: str) -> Path | None:
    row = db.db.execute("SELECT task_id FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row:
        return None
    from project_sources import resolve_pdf_for_read
    return resolve_pdf_for_read(db, row["task_id"], paper_id)


# ---------------- 元数据 ----------------
def _crossref(doi: str, timeout: float = 12) -> dict[str, Any]:
    """按 DOI 反查 Crossref 补全题目/时间/关键词/作者（直连；代理关着也能用）。"""
    req = urllib.request.Request(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                                 headers={"User-Agent": "research-workbench/1.0 (mailto:local)"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:                # 显式不走代理
        return json.load(r).get("message", {}) or {}


_META_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}   # 同一篇别反复去 Crossref（笔记模式也用它取短名）


def paper_meta(db: Any, paper_id: str) -> dict[str, Any]:
    path = pdf_path(db, paper_id)
    if not path:
        return {"error": "找不到这篇 PDF 的文件"}
    try:
        mt = path.stat().st_mtime
    except OSError:
        mt = 0.0
    hit = _META_CACHE.get(str(path))
    if hit and hit[0] == mt and len(hit[1]) > 1:
        return dict(hit[1])
    out: dict[str, Any] = {"filename": path.name, "doi": "", "title": "", "keywords": "",
                           "published": "", "authors": "", "pages": 0, "source": "", "added": ""}
    try:                                                     # 入项目时间（papers.created_at）
        row = db.db.execute("SELECT created_at FROM papers WHERE id=?", (paper_id,)).fetchone()
        if row and row["created_at"]:
            out["added"] = str(row["created_at"])[:19].replace("T", " ")
    except Exception:
        pass
    out["pages"] = pdfio.page_count(path)
    text = pdfio.text(path, limit=3)
    _meta = pdfio.meta(path)
    out["title"] = _meta["title"] or pdfio.biggest_title(path)
    out["published"] = _meta["creation"][:10].replace("D:", "")
    m = DOI_RE.search(text)
    if m:
        out["doi"] = m.group(0).rstrip(".,;)")
    kw = KW_RE.search(text)
    if kw:
        out["keywords"] = re.sub(r"\s+", " ", kw.group(1)).strip()[:200]
    if out["doi"]:                                              # 有 DOI 就再去 Crossref 补全
        try:
            msg = _crossref(out["doi"])
            out["source"] = "crossref"
            out["title"] = (msg.get("title") or [out["title"]])[0][:300]
            subs = msg.get("subject") or []
            if subs and len(out["keywords"]) < 5:
                out["keywords"] = "，".join(subs[:8])
            dp = (msg.get("published-print") or msg.get("published-online") or msg.get("issued") or {}).get("date-parts") or []
            if dp and dp[0]:
                out["published"] = "-".join(f"{x:02d}" if i else f"{x}" for i, x in enumerate(dp[0]))
            names = [f"{a.get('given','')} {a.get('family','')}".strip() for a in (msg.get("author") or [])[:6]]
            out["authors"] = "、".join(n for n in names if n)
        except Exception as exc:
            out["source"] = f"crossref 失败：{type(exc).__name__}"
    if len(_META_CACHE) > 64:
        _META_CACHE.clear()
    _META_CACHE[str(path)] = (mt, dict(out))
    return out


# ---------------- 翻译任务（BabelDOC） ----------------
def model_config(profile):
    from workflow_db import _assert_key_host
    if not profile or profile.get("api", "openai-completions") not in {"openai-chat", "openai-completions"}:
        raise ValueError("PDF 翻译需要 Chat Completions 模型，请在设置 → 模型列表中选择")
    base, key = profile["base_url"], profile.get("api_key") or ""
    _assert_key_host(profile)
    address = urllib.parse.urlsplit(base)
    if address.scheme not in {"http", "https"} or not address.hostname:
        raise ValueError("PDF 翻译模型缺少有效接口地址")
    if not key and address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 PDF 翻译模型缺少 API Key")
    return {"base": base, "key": key or "local", "model": profile["model"]}


def model_choices(db):
    import service_settings
    selected = service_settings.read(db, "pdf_translation_model").get("model_id", "")
    choices = []
    for profile in db.models():
        try:
            model_config(profile)
        except (ValueError, RuntimeError):
            continue
        choices.append({"id": profile["id"], "name": profile["name"]})
    return {"model_id": selected, "models": choices}


def _llm_cfg(db: Any, model_id: str = "") -> dict[str, Any]:
    import service_settings
    identifier = model_id or service_settings.read(db, "pdf_translation_model").get("model_id", "")
    try:
        profile = db.model(identifier) if identifier else db.active_model()
        return {**model_config(profile), "profile": profile}
    except KeyError as exc:
        raise ValueError("PDF 翻译模型已不可用，请重新选择") from exc


def out_paths(pdf: Path, out_dir: Path) -> tuple[Path, Path]:
    """babeldoc 的产物名：<name>.no_watermark.zh.{dual,mono}.pdf"""
    return (out_dir / f"{pdf.stem}.no_watermark.zh.dual.pdf",
            out_dir / f"{pdf.stem}.no_watermark.zh.mono.pdf")


def _state_path(out_dir: Path, paper_id: str) -> Path:
    return out_dir / f"trans_{paper_id[:8]}.json"


def output_dir(db: Any, paper_id: str, project_id: str = "") -> Path:
    row = db.db.execute("SELECT task_id FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row:
        raise ValueError("找不到这篇论文")
    owner = row["task_id"] or ""
    if project_id and project_id != owner:
        raise ValueError("论文不属于当前项目")
    return db.pdir(owner, "文献库") if owner else paths.OUT


def deliveries(db: Any, project_id: str) -> list[dict[str, Any]]:
    """Only completed translations with project-local PDF output can be delivered."""
    if not project_id or not db.db.execute("SELECT 1 FROM research_tasks WHERE id=?", (project_id,)).fetchone():
        return []
    root = db.base_dir(project_id)
    out_dir = db.pdir(project_id, "文献库").resolve()
    root = (root or out_dir).resolve()
    if not out_dir.is_relative_to(root):
        return []
    completed = []
    for paper in sorted(db.papers_of(project_id), key=lambda row: row["id"]):
        source = pdf_path(db, paper["id"])
        state = _read_state(out_dir, paper["id"])
        if not source or not state.get("done"):
            continue
        source = source.resolve()
        if not source.is_relative_to(root) or not source.stat().st_size:
            continue
        dual, mono = out_paths(source, out_dir)
        if not mono.resolve().is_relative_to(root) or not mono.is_file() or not mono.stat().st_size:
            continue
        translation_path = str(mono.resolve())
        dual_path = ""
        if not state.get("identity") and dual.resolve().is_relative_to(root) and dual.is_file() and dual.stat().st_size:
            dual_path = str(dual.resolve())
        try:
            source_pages = pdfio.page_count(source)
            translation_pages = pdfio.page_count(mono)
        except (OSError, pdfio.PdfReadError):
            continue
        pages = list(range(1, source_pages + 1))
        page_map = []
        if source_pages == translation_pages == state.get("total") and state.get("completed_pages", pages) == pages:
            page_map = [{"source": page, "translation": page} for page in pages]
        completed.append({
            "id": paper["id"], "title": paper.get("title") or paper.get("filename") or source.name,
            "source_path": str(source), "translation_path": translation_path, "dual_path": dual_path,
            "page_map": page_map, "files": [path for path in (str(source), translation_path, dual_path) if path],
        })
    return completed


def _save_state(out_dir: Path, paper_id: str, pages_done: int, total: int, done: bool, **metadata: Any) -> None:
    from asset_service import atomic_write_text
    atomic_write_text(_state_path(out_dir, paper_id), json.dumps(
        {"pages_done": pages_done, "total": total, "done": done, **metadata}, ensure_ascii=False))


def _read_state(out_dir: Path, paper_id: str) -> dict[str, Any]:
    try:
        return json.loads(_state_path(out_dir, paper_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def status(paper_id: str, out_dir: Path | None = None) -> dict[str, Any]:
    with _LOCK:
        job = JOBS.get(paper_id)
        if job:
            return dict(job)
    cand_dirs = [out_dir] if out_dir else [Path("data/out/translations"), Path("data/uploads"), Path("data/out")]
    for d in cand_dirs:
        if d and d.is_dir():
            st = _read_state(d, paper_id)
            if st:
                done = bool(st.get("done"))
                pages_done = int(st.get("pages_done") or 0)
                total = int(st.get("total") or 0)
                if done:
                    return {"state": "done", "pct": 100, "msg": "已有译文（断点缓存）",
                            "pages_done": total, "total_pages": total}
                finished = len(st.get("completed_pages", [])) if "completed_pages" in st else pages_done
                if finished > 0:
                    pct = int(finished * 100 / max(1, total)) if total else 0
                    return {"state": "interrupted", "pct": pct, "completed_count": finished,
                            "msg": f"已缓存 {finished} 页，可按所需范围续翻",
                            "pages_done": pages_done, "total_pages": total}
    return {"state": "idle"}


def _env() -> dict[str, str]:
    """babeldoc 子进程的环境：**先把凭据类变量全剥掉**（fileguard.clean_env），只补它真要的几个。
    模型 key 是命令行 `--openai-api-key` 传的，子进程不需要任何 *_KEY 环境变量 ——
    以前这里是 os.environ 整份复制，等于把家里所有 key 都递给一个第三方 PDF 工具。"""
    import fileguard
    env = {k: v for k, v in fileguard.clean_env().items() if not k.lower().endswith("_proxy")}
    env["HF_HUB_OFFLINE"] = "1"                                 # 版式模型本机已有缓存
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # babeldoc 把缓存硬编码在 Path.home()/.cache/babeldoc（const.py），所以把 home 指到 Low 目录；
    # 但版式模型在 HF 缓存里（几百 MB），那个目录**保持原位只读**，别让子进程去下载。
    env["HF_HOME"] = os.path.expanduser("~/.cache/huggingface")
    env["HF_HUB_CACHE"] = os.path.expanduser("~/.cache/huggingface/hub")
    env["TIKTOKEN_CACHE_DIR"] = str(paths.LOWTMP / "home" / ".cache" / "babeldoc" / "tiktoken")
    _fake_home = paths.LOWTMP / "home"
    _fake_home.mkdir(parents=True, exist_ok=True)
    env["USERPROFILE"] = str(_fake_home)
    env["HOMEDRIVE"], env["HOMEPATH"] = _fake_home.drive, str(_fake_home)[2:]
    env["NO_PROXY"] = "*"
    env["PYTHONUNBUFFERED"] = "1"                               # 不然日志/progress 全憋在缓冲区里，UI 看不出进度
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "220"                                      # rich 按 80 列换行会把 "12/40" 截成 "12/…"
    return lowbox.sandbox_env(env, paths.LOWTMP)                # TEMP/TMP 也要在 Low 目录里


def _parse_pages(s: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a), int(b)))
        else:
            out.append((int(part), int(part)))
    return out


def _plan(n: int) -> list[tuple[int, int]]:
    """分批顺序：先单独翻第 1 页（他要「第一页好了先展示第一页」），往后按批推。
    页多了就均分成 CHUNK_MAX 段 —— 每段都要重跑一次版式模型，段数太多反而更慢。"""
    if n <= 0:
        return []
    if n == 1:
        return [(1, 1)]
    if n > 12:
        size = max(3, min(6, -(-n // max(1, CHUNK_MAX))))
    else:
        size = 3
    parts, a = [(1, 1)], 2
    while a <= n:
        b = min(n, a + size - 1)
        parts.append((a, b))
        a = b + 1
    return parts


def _page_plan(n, pages, completed):
    ranges = _parse_pages(pages) if pages else _plan(n)
    requested = set()
    missing = []
    for a, b in ranges:
        if not 1 <= a <= b <= n:
            raise ValueError(f"页码范围必须在 1–{n} 内")
        first = None
        for page in range(a, b + 1):
            if page not in completed and page not in requested:
                first = page if first is None else first
            elif first is not None:
                missing.append((first, page - 1))
                first = None
            requested.add(page)
        if first is not None:
            missing.append((first, b))
    if not requested:
        raise ValueError("请输入页码范围，例如 3 或 2-4,8")
    return requested, missing


def start(db: Any, paper_id: str, out_dir: Path, pages: str = "", force: bool = False,
          *, model_id: str = "") -> dict[str, Any]:
    import asset_service
    path = pdf_path(db, paper_id)
    if not path:
        return {"error": "找不到这篇 PDF 的文件"}
    if os.name == "nt" and os.environ.get("RW_LOW_SERVER") == "1" and not (MANAGED_BABELDOC.is_file() or BABELDOC_EXE.is_file()):
        return {"error": "翻译引擎尚未初始化。请先完成翻译环境初始化，再重新开始翻译。"}
    n = pdfio.page_count(path)
    requested, _ = _page_plan(n, pages, set())
    cfg = _llm_cfg(db, model_id)
    identity = sha256(json.dumps(
        [asset_service.sha256_file(path), cfg["base"], cfg["model"], "en", "zh", 1]
    ).encode()).hexdigest()
    dual, mono = out_paths(path, out_dir)
    with _LOCK:
        job = JOBS.get(paper_id)
        if job and job.get("state") == "running":
            return {"ok": True, "already": True, **job}
        state = _read_state(out_dir, paper_id)
        completed = set()
        if mono.is_file() and not force:
            if state.get("identity") == identity:
                present = min(n, pdfio.page_count(mono))
                completed = {number for number in state.get("completed_pages", [])
                             if type(number) is int and 1 <= number <= present}
            elif not state.get("identity") and state.get("done") and state.get("total") == n and not model_id:
                if pdfio.page_count(mono) == n:
                    completed = set(range(1, n + 1))
        _, plan = _page_plan(n, pages, completed)
        status_fields = {"selected": bool(pages), "first_page": min(requested), "scope_total": len(requested),
                         "completed_count": len(requested & completed), "total_pages": n,
                         "pages_done": len(completed), "full_document": len(completed) == n}
        if not plan:
            JOBS[paper_id] = {**status_fields, "state": "done", "pct": 100, "msg": "已复用缓存译文",
                              "mono": str(mono), "dual": str(dual) if dual.is_file() and len(completed) == n else ""}
            return {"ok": True, "cached": True, **JOBS[paper_id]}
        if not cfg["key"]:
            return {"error": "没有可用的翻译模型。请先在模型页配置 OpenAI 兼容的翻译后端。"}
        out_dir.mkdir(parents=True, exist_ok=True)
        JOBS[paper_id] = {**status_fields, "state": "running", "pct": 0, "msg": "准备翻译",
                          "mono": str(mono) if completed else "", "dual": ""}
    threading.Thread(target=_run, args=(paper_id, path, out_dir, cfg, plan, mono, _env(), identity,
                                      n, requested, completed), daemon=True).start()
    return {"ok": True, "state": "running", "chunks": len(plan), **status_fields}


_PROG = re.compile(r"(\d+)\s*/\s*(\d+)")
_PHASES = (("doclayout", "识别版式"), ("parse page layout", "识别版式"), ("term extractor", "抽术语表"),
           ("glossary", "抽术语表"), ("translating", "翻译正文"), ("translate", "翻译正文"),
           ("save", "写回 PDF"), ("write", "写回 PDF"))


def _phase(line: str) -> str:
    low = line.lower()
    for k, v in _PHASES:
        if k in low:
            return v
    return ""


def _why(code: int) -> str:
    """把 Windows 的魔数退出码翻成人话，免得界面只显示一串数字。"""
    return {3221225786: "：子进程被控制台事件中断（黑窗被关掉/按了 Ctrl+C）",
            3221225477: "：子进程崩溃（访问冲突）",
            -9: "：看门狗判定卡死后终止"}.get(code, "")


def _spawn(paper_id: str, cmd: list[str], env: dict[str, str], fh: Any, a: int, b: int) -> int:
    """跑一段 babeldoc：日志边写边刷新，状态里同步进度"""
    # 直跑 exe 时进 Low IL 沙箱（只能写项目目录）；还在走 uv 包装器时只能普通 spawn
    # —— uv 要写 %LOCALAPPDATA%\uv\cache，Low 下直接拒绝访问。
    if Path(cmd[0]).name.lower() == "babeldoc.exe":
        proc = lowbox.popen(cmd, env=env)
    else:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", env=env,
                                creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP)
    phase, done, tot = "", 0, 0
    last = [time.monotonic()]
    stop_event = threading.Event()

    def _watch() -> None:                  # 卡死看门狗：读循环会阻塞在 readline 上，只能另起线程盯
        while not stop_event.wait(0.2):
            if proc.poll() is not None:
                return
            if time.monotonic() - last[0] > STALL_SECONDS:
                with _LOG_LOCK:
                    fh.write(f"!!! 超过 {STALL_SECONDS}s 没有任何输出，判定卡死并终止\n")
                    fh.flush()
                proc.kill()
                return

    watch_thread = threading.Thread(target=_watch, daemon=True)
    watch_thread.start()
    try:
        if proc.stdout:
            for line in proc.stdout:
                last[0] = time.monotonic()
                with _LOG_LOCK:
                    fh.write(line)
                    fh.flush()
                ph = _phase(line)
                if ph:
                    phase = ph
                if "translate" in line.lower() or "-----" in line or "━" in line:
                    for cand in _PROG.finditer(line):        # 只认 "已完成/总数"（时间戳 0:00:10/0:00:06 会被 done>total 挡掉）
                        x, y = int(cand.group(1)), int(cand.group(2))
                        if 0 < x <= y:
                            done, tot = x, y
                            break
                msg = f"第 {a}-{b} 页 · {phase or '准备中'}" + (f" {done}/{tot}" if tot else "")
                with _LOCK:
                    JOBS[paper_id] = {**JOBS.get(paper_id, {}), "state": "running", "msg": msg}
    finally:
        stop_event.set()
        watch_thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
    return proc.wait()


def _trim_tail(mono: Path, src: Path, done_pages: int) -> None:
    """只裁「超出已翻页数 done_pages」的尾部原页 —— 那些是 babeldoc 为版式上下文带进来的
    未翻译页（实测要 1-2 页会给整篇）。

    为什么必须有 done_pages 这个上限：模型/接口出问题时 babeldoc 会把段落原样 fallback，
    这些「真译文页」的文字与原文**逐字相同**，只按文字判会把 38 页全删掉（踩过一次）。
    已翻页以内的页一律不动。

    ponytail: 判据仍是「文字与原文完全相同」。整页只有公式/图的页可能也满足而被删 ——
    这种页本来没有可翻的文字，代价可接受；真要精确就按页存一份「本页已翻」的标记。
    """
    try:
        a_texts = pdfio.page_texts(mono)
        b_texts = pdfio.page_texts(src)
        n = len(a_texts)
        keep = n
        while keep > 1 and keep > done_pages:
            i = keep - 1
            a = a_texts[i]
            b = b_texts[i] if i < len(b_texts) else ""
            if a and b and a == b:
                keep -= 1
            else:
                break
        if keep == n or keep < 1:
            return
        tmp = mono.with_name(mono.name + ".trim.pdf")
        pdfio.write_pages(tmp, [(mono, i) for i in range(keep)])
        os.replace(tmp, mono)
    except Exception:
        return
def _splice(dest: Path, part: Path, a: int, b: int) -> None:
    """把 part 里第 a..b 页拼进累积译文（页序始终跟原文一一对应）。

    babeldoc 为版式上下文会多带页（实测要 1-2 页给整篇），所以只取 [a, b] 区间；
    文档只增不减：新段可能把页数往后推，乱序并行缺页时补空白页，保住页号一一对应。
    """
    tmp = dest.with_name(dest.name + ".tmp.pdf")
    if not dest.is_file():
        pdfio.write_pages(tmp, [(part, k) for k in range(a - 1, min(b, pdfio.page_count(part)))])
    else:
        old_n, new_n = pdfio.page_count(dest), pdfio.page_count(part)
        srcs: list[tuple[Path, int] | None] = []
        for k in range(max(old_n, b)):
            if (a - 1) <= k <= (b - 1) and k < new_n:
                srcs.append((part, k))
            elif k < old_n:
                srcs.append((dest, k))
            else:
                srcs.append(None)                     # 还没翻到的那页先占位，等它自己那段来填
        pdfio.write_pages(tmp, srcs, size=pdfio.page_size(part) if new_n else None)
    for _ in range(3):
        try:
            tmp.replace(dest)
            return
        except OSError:                              # 前端可能正读着这个文件，等一下再换
            time.sleep(0.4)

def _balance_hint(tail: str) -> str:
    """从日志尾部认出发钱相关的错误，直接给人话（这类错重试没用，得充值/降并发）。"""
    low = tail.lower()
    if "insufficient balance" in low or "'code': 'invalid_request_error'" in low and "402" in low:
        return "API 余额不足（402 Insufficient Balance）—— 充值后再翻"
    if "concurrency" in low and "429" in low:
        return "并发超限（429）—— 充值提升额度，或把 RW_TRANS_PARALLEL 调小"
    return ""


def _fail(paper_id: str, mono: Path, total: int, log: Path, a: int, b: int, code: int) -> None:
    hint = ""
    try:
        hint = _balance_hint(log.read_text(encoding="utf-8", errors="replace")[-4000:])
    except Exception:
        pass
    with _LOCK:
        cur = JOBS.get(paper_id, {})
        has_partial = bool(cur.get("pages_done")) and mono.is_file()
        JOBS[paper_id] = {**cur, "state": "partial" if has_partial else "error",
                          "msg": (hint or f"翻到第 {a}-{b} 页时失败（退出码 {code}{_why(code)}）"),
                          "total_pages": total, "mono": str(mono) if has_partial else "", "log": str(log)}


def _merge_translation_pages(dest, part, source, a, b, completed):
    part_length = pdfio.page_count(part)
    if part_length >= b:
        part_start = a - 1
    elif part_length == b - a + 1:
        part_start = 0
    else:
        raise ValueError("译文页数与请求范围不一致")
    tmp = dest.with_name(dest.name + ".tmp.pdf")
    out = []
    for page in range(1, max(b, max(completed, default=0)) + 1):
        if a <= page <= b:
            out.append((part, part_start + page - a))
        elif page in completed:
            out.append((dest, page - 1))
        else:
            out.append((source, page - 1))
    pdfio.write_pages(tmp, out)
    tmp.replace(dest)


def _run(paper_id: str, path: Path, out_dir: Path, cfg: dict[str, Any],
         plan: list[tuple[int, int]], mono: Path, env: dict[str, str], identity: str,
         total: int, requested: set[int], completed: set[int]) -> None:
    """Translate missing ranges in parallel and commit only completed pages."""
    log = mono.parent / f"trans_{paper_id[:8]}.log"
    if cfg.get("profile"):
        from model_checks import generate
        try:
            generate(cfg["profile"], source="pdf_translation_check")
        except Exception as exc:
            with _LOCK:
                JOBS[paper_id].update(state="partial" if completed else "error", msg="翻译模型未通过生成测试：" + str(exc)[:240])
            return

    def one(idx: int, a: int, b: int):
        part_dir = out_dir / "_parts" / paper_id[:12] / identity[:16] / f"p{a}-{b}"
        part_dir.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            fh.write(f"\n===== 第 {a}-{b} 页（第 {idx}/{len(plan)} 段）=====\n")
            fh.flush()
        _off_think = ("--openai-thinking", "disabled") if (
            "deepseek" in str(cfg.get("base") or "").lower()
            or "deepseek" in str(cfg.get("model") or "").lower()) else ()
        cmd = babeldoc_command() + ["--files", str(path), "--lang-in", "en", "--lang-out", "zh",
                              "--openai", "--openai-model", cfg["model"],
                              "--openai-base-url", cfg["base"], "--openai-api-key", cfg["key"],
                              "--watermark-output-mode", "no_watermark",
                              # 每段分到的并发 = 全局上限 / 段数（段并行后必须摊薄，否则撞余额决定的并发上限 429）
                              "--qps", str(max(1, QPS_TOTAL // max(1, CHUNK_PARALLEL))),
                              # thinking 关：它的 reasoning token 会挤空 babeldoc 的 2048 输出预算
                              *_off_think,
                              "--enable-json-mode-if-requested",   # 逼模型只吐 JSON，解析失败=整批退化
                              "--no-dual",                    # UI 只用单语译文，省掉对照版的生成
                              "--no-auto-extract-glossary",   # 省掉一整趟全篇术语抽取的 LLM 调用
                              "--pages", f"{a}-{b}",
                              "--working-dir", str(part_dir / "work"),
                              "--output", str(part_dir)]
        part_mono = part_dir / mono.name
        part_mono.unlink(missing_ok=True)
        code = _spawn(paper_id, cmd, env, fh, a, b)
        return a, b, code, part_mono

    failures = []
    try:
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            with ThreadPoolExecutor(max_workers=max(1, min(CHUNK_PARALLEL, len(plan)))) as pool:
                pending = [pool.submit(one, idx, a, b) for idx, (a, b) in enumerate(plan, 1)]
                for future in as_completed(pending):
                    a, b, code, translated = future.result()
                    if code != 0 or not translated.is_file():
                        failures.append((a, b, code))
                        continue
                    _merge_translation_pages(mono, translated, path, a, b, completed)
                    completed.update(range(a, b + 1))
                    full_document = len(completed) == total
                    _save_state(out_dir, paper_id, len(completed), total, full_document,
                                identity=identity, completed_pages=sorted(completed))
                    done = len(completed & requested)
                    with _LOCK:
                        JOBS[paper_id].update(
                            pages_done=len(completed), completed_count=done, full_document=full_document,
                            pct=int(done * 100 / len(requested)), mono=str(mono), log=str(log),
                            msg=f"当前范围已翻译 {done}/{len(requested)} 页"
                        )
        if failures:
            _fail(paper_id, mono, total, log, *failures[0])
        else:
            with _LOCK:
                JOBS[paper_id].update(state="done", pct=100, msg="翻译完成", mono=str(mono), log=str(log))
    except Exception as exc:
        with _LOCK:
            JOBS[paper_id].update(state="partial" if completed else "error",
                                  msg=f"{type(exc).__name__}: {exc}", log=str(log))


# ---------------- 译文 → 中文笔记 ----------------
def pdf_words(path: Path, max_pages: int = 0) -> dict[str, Any]:
    """每页的词 + 坐标（PDF 点，左上原点），前端按缩放比铺文字层：
    自己能控、离线可用，还能让荧光笔的选区对准（pdf.js 的 renderTextLayer 在 WebView2 里会挂）。"""
    return {"pages": pdfio.pages_with_words(path, max_pages)}


def mono_pages(mono: Path) -> list[str]:
    return pdfio.page_texts(mono)


_CJK = re.compile(r"[\u3400-\u9fff]")


def only_chinese(text: str) -> str:
    """只留含中文的段落（部分翻译时未翻的页还是英文，用户要「光要中文」）。"""
    keep = [p.strip() for p in re.split(r"\n{2,}", text) if _CJK.search(p)]
    return "\n\n".join(keep)


def short_name(title: str, stem: str = "") -> str:
    """「阅读笔记」后面跟的论文短名：中文取前 10 字，英文取前 3 个词；取不到就用文件名（去哈希前缀）。"""
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        t = re.sub(r"^[0-9a-f]{8,}_", "", stem or "").rsplit(".", 1)[0]
    if _CJK.search(t):
        head = t[:10]
    else:
        words = t.split(" ")
        head = " ".join(words[:3]) if len(words) > 3 else t
    head = head.strip(" .,:;-–—")
    if head and len(head) < len(t):
        head += "…"
    return head[:26]


def reading_note(db: Any, task_id: str, paper_id: str, out_dir: Path) -> dict[str, Any]:
    """点「笔记模式」调它：给这篇论文**复用或新建一篇「阅读笔记」**（空白、绑论文 + 绑项目），
    左边那份中文全文单独返回（不塞进笔记正文 —— 正文是他自己随记随写的地方）。"""
    path = pdf_path(db, paper_id)
    if not path:
        return {"error": "找不到这篇 PDF 的文件"}
    name = "阅读笔记 · " + (short_name(paper_meta(db, paper_id).get("title", ""), path.stem) or "未命名")
    note = db.md_note_by_paper(paper_id)
    if not note:
        note = db.save_md_note("", name, "", task_id, paper_id)
    else:                                              # 已有：补项目绑定 + 刷新自动名（他自己改过名就不动）
        t = note.get("title") or ""
        if (task_id and not note.get("task_id")) or (t.startswith("阅读笔记") and t != name):
            note = db.save_md_note(note["id"], name if t.startswith("阅读笔记") else t,
                                   note.get("content") or "", task_id, paper_id)
    _, mono = out_paths(path, out_dir)
    text, pages = "", 0
    if mono.is_file():
        kept = [only_chinese(t) for t in mono_pages(mono)]
        kept = [t for t in kept if t]
        pages = len(kept)
        text = "\n\n".join(kept)
    return {"note": note, "text": text, "pages": pages, "has_mono": mono.is_file()}


if __name__ == "__main__":                                     # 自带小样：不联网的那部分
    from datetime import datetime
    assert DOI_RE.search("see 10.1038/s41524-025-01554-0 for details").group(0) == "10.1038/s41524-025-01554-0"
    assert KW_RE.search("Keywords: machine learning, NLP, materials").group(1).startswith("machine learning")
    assert out_paths(Path("a/b/x.pdf"), Path("o"))[0].name == "x.no_watermark.zh.dual.pdf"
    assert _PROG.search("translate 12/100 0:01:42").groups() == ("12", "100")

    def prog(line: str) -> tuple[int, int]:            # _run 里那段挑 N/M 的逻辑，抽出来单独验
        if "translate" not in line.lower() and "-----" not in line and "━" not in line:
            return 0, 0
        for c in _PROG.finditer(line):
            a, b = int(c.group(1)), int(c.group(2))
            if 0 < a <= b:
                return a, b
        return 0, 0

    assert prog("translate ----- 37/94  0:01:42 0:00:11") == (37, 94)
    assert prog("translate ----- 10/6 0:00:10/0:00:06") == (0, 0)   # 时间戳冒名顶替的那种，丢掉
    assert prog("INFO doclayout:Loading ONNX model...") == (0, 0)
    assert _plan(1) == [(1, 1)] and _plan(2) == [(1, 1), (2, 2)]
    assert _plan(9) == [(1, 1), (2, 4), (5, 7), (8, 9)]            # 先给第 1 页，然后每 3 页一段
    p30 = _plan(30)
    assert sum(b - a + 1 for a, b in p30) == 30 and p30[0] == (1, 1) and len(p30) <= 8
    assert all(p30[i][1] + 1 == p30[i + 1][0] for i in range(len(p30) - 1))   # 段与段之间不留缝
    assert _parse_pages("2") == [(2, 2)] and _parse_pages("3-5") == [(3, 5)]
    assert short_name("Deep learning design of nanoscale polariton propagations") == "Deep learning design…"
    assert short_name("扭曲范德华多层结构中纳米尺度极化子传播") == "扭曲范德华多层结构中…"
    assert short_name("", "6bdd7f0c47bd_s41563-026-02723-2.pdf") == "s41563-026-02723-2"
    assert short_name("Short") == "Short"
    assert status("nope")["state"] == "idle"
    print("papertran.py 自检通过", datetime.now().strftime("%H:%M"))
