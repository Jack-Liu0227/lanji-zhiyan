# -*- coding: utf-8 -*-
"""文档转换、引用台账、LaTeX 编译与中译英模块。"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fileguard
import lowbox          # 低完整性（Low IL）子进程：xelatex 只能写项目目录
import paths
from workflow_documents import doc_read, doc_path

if TYPE_CHECKING:
    from workflow import WorkflowDB


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] if (HERE.parents[1] / "packages").is_dir() else HERE
OUT = paths.OUT
DATA, PAPERS, NOTES = paths.DATA, paths.PAPERS, paths.NOTES     # 唯一路径来源（_safe_path 的白名单根）
EN_LIMIT = 2400

EN_PROMPT = """你是资深学术英语审稿人兼专业学术翻译。将以下中文学术论文段落翻译为地道、精确、简练的学术英文（Nature / IEEE 风格）。
保持原义，术语统一，句式紧凑，避免中式英语。只输出英文翻译结果，不包含任何解释、前言或总结。"""

def _doc_dir(db: "WorkflowDB", task_id: str) -> Path:
    base = db.base_dir(task_id)
    return (base / "论文正文") if base else OUT


def _doc_fallback(db: "WorkflowDB", task_id: str, name: str) -> Path:
    """项目没设位置时按项目 id 分名（否则多个项目会互相覆盖）。"""
    return _doc_dir(db, task_id) / name if db.base_dir(task_id) \
        else OUT / f"{Path(name).stem}_{task_id[:8]}{Path(name).suffix}"


# ---------------- 引用台账（后台 markdown：正文里不写标号，引用全记在这） ----------------
CITE_FILE = "引用台账.md"
CITE_JSON = "引用台账.json"


def cite_paths(db: "WorkflowDB", task_id: str) -> tuple[Path, Path]:
    return _doc_fallback(db, task_id, CITE_FILE), _doc_fallback(db, task_id, CITE_JSON)


def _cite_load(db: "WorkflowDB", task_id: str) -> list[dict[str, Any]]:
    _, js = cite_paths(db, task_id)
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def cite_sync(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """按当前正文重算每条引用的锚点状态，然后把台账 markdown 整份重写（随时跟着前台改）。"""
    md, js = cite_paths(db, task_id)
    entries = _cite_load(db, task_id)
    draft = doc_read(db, task_id, "write")["markdown"]
    lines = ["# 引用台账",
             "",
             "> 后台文档：**正文里不插标号**，每一处引用记在这里（位置 + 文献信息）。正文一改，这里的状态跟着重算。",
             ""]
    for i, e in enumerate(entries, 1):
        anchor = (e.get("anchor") or "").strip()
        ok = bool(anchor) and anchor in draft
        e["ok"] = ok
        lines += [f"## C{i} · {e.get('section') or '（未标章节）'}", ""]
        lines.append(f"- 位置：{(e.get('section') or '（未标章节）')}" +
                     (f"，锚句：「{anchor}」" if anchor else "（没给锚句，只能靠人找）"))
        if e.get("title"):
            lines.append(f"- 文献：{e['title']}")
        if e.get("authors"):
            lines.append(f"- 作者：{e['authors']}")
        if e.get("venue") or e.get("year"):
            lines.append(f"- 出处：{e.get('venue') or '?'}{', ' + str(e['year']) if e.get('year') else ''}")
        if e.get("doi"):
            lines.append(f"- DOI：{e['doi']}")
        if e.get("pages"):
            lines.append(f"- 页数：{e['pages']}" + (f"（引用在 p.{e['cite_page']}）" if e.get("cite_page") else ""))
        if e.get("url"):
            lines.append(f"- 链接：{e['url']}")
        if e.get("why"):
            lines.append(f"- 引用理由：{e['why']}")
        lines.append(f"- 状态：{'✓ 锚句还在正文里' if ok else ('⚠ 正文里找不到这句（可能已改写）' if anchor else '– 未给锚句')}")
        if e.get("paper_id"):
            lines.append(f"- 库内 paper_id：{e['paper_id']}")
        lines.append("")
    fileguard.before_write(md, "引用台账")
    fileguard.before_write(js, "引用台账")
    import asset_service
    asset_service.atomic_write_text(md, "\n".join(lines))
    asset_service.atomic_write_text(js, json.dumps(entries, ensure_ascii=False, indent=1))
    asset_service.register_file(db, md, project_id=task_id, kind="document", title=CITE_FILE,
                                source="workflow", body="\n".join(lines), metadata={"document_kind": "citation_ledger"})
    return {"path": str(md), "count": len(entries), "stale": sum(1 for e in entries if not e.get("ok")),
            "markdown": "\n".join(lines)}


def cite_read(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    out = cite_sync(db, task_id)                    # 读之前先同步（这就是「随时根据前台调整改变」）
    out["exists"] = True
    return out


def cite_add(db: "WorkflowDB", task_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """加一条引用（位置 + 文献信息），细节到题目/DOI/页数。"""
    entries = _cite_load(db, task_id)
    fields = ("section", "anchor", "title", "authors", "venue", "year", "doi", "pages", "cite_page",
              "url", "why", "paper_id")
    e = {k: (args.get(k) or "") for k in fields}
    if not any(e.values()):
        raise ValueError("这条引用什么都没给")
    entries.append(e)
    md, js = cite_paths(db, task_id)
    md.parent.mkdir(parents=True, exist_ok=True)
    fileguard.before_write(js, "引用台账")
    js.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    out = cite_sync(db, task_id)
    out["added"] = e
    return out


# ---------------- 格式转换（照 Overleaf：文件 / 代码 / 编译 → PDF） ----------------
CONV_FORMATS = {"docx": "Word (.docx)", "html": "HTML", "md": "Markdown", "latex": "LaTeX (.tex → PDF，本机 MiKTeX)"}


def md_to_tex(md: str, title: str) -> str:
    """极简 markdown → tex（标题/段落/列表够用）。真要投稿的排版还是走组内 revtex 规范。
    注意：这里**不能**用 % 或 str.format 打模板（LaTeX 自带 {} 和 % 注释）。"""
    head = [r"\documentclass[11pt,a4paper]{article}",
            r"\usepackage{ctex}          % 中文：必须 xelatex 编译（本机只装了 ctex/revtex，别引别的宏包）",
            r"\setlength{\textwidth}{16cm}",
            r"\setlength{\oddsidemargin}{0pt}",
            r"\setlength{\evensidemargin}{0pt}",
            r"\title{" + tex_escape(title) + "}",
            r"\date{\today}",
            r"\begin{document}",
            r"\maketitle",
            ""]
    body: list[str] = []
    bullets: list[str] = []
    for line in (md or "").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if bullets and (not line.startswith("- ") or m):
            body += [r"\begin{itemize}", *bullets, r"\end{itemize}", ""]
            bullets = []
        if m:
            lvl, txt = len(m.group(1)), tex_escape(m.group(2))
            body.append(r"\section{%s}" % txt if lvl <= 2 else (r"\subsection{%s}" % txt if lvl == 3 else r"\subsubsection{%s}" % txt))
        elif line.startswith("- "):
            bullets.append(r"\item " + tex_escape(line[2:]))
        elif line.strip():
            body.append(tex_escape(line))
            body.append("")
    if bullets:
        body += [r"\begin{itemize}", *bullets, r"\end{itemize}"]
    return "\n".join(head + body + ["", r"\end{document}", ""])


_TEX_MAP = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(s: str) -> str:
    return "".join(_TEX_MAP.get(c, c) for c in (s or ""))    # 逐字符换，避免把插入的反斜杠再转义一遍


def _xelatex() -> str:
    """本机 MiKTeX 的 xelatex（用户级装的那种）。"""
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "xelatex.exe"
    if cand.is_file():
        return str(cand)
    return "xelatex"


def conv_run(db: "WorkflowDB", task_id: str, fmt: str, source: str = "") -> dict[str, Any]:
    """一键格式转换：以正文.md（或指定文件）为源，导出到「最终产物」。latex 会就地编译出 PDF。"""
    fmt = (fmt or "docx").lower()
    src = Path(source) if source else doc_path(db, task_id, "write")
    md = src.read_text(encoding="utf-8", errors="replace") if src.is_file() else ""
    if not md.strip():
        raise ValueError(f"源文件是空的：{src}（先在「论文撰写」写出正文）")
    title = doc_read(db, task_id, "outline")["markdown"].splitlines()
    title = next((l.lstrip("# ").strip() for l in title if l.strip().startswith("#")),
                 db.summary(task_id)["task"]["title"] or Path(src).stem)      # 没框架就用项目名，别叫「正文」
    folder = db.pdir(task_id, "最终产物")
    safe = re.sub(r"[^\w\-. ]+", "_", title)[:60] or "paper"
    out = folder / f"{safe}.{'pdf' if fmt == 'latex' else fmt}"
    if fmt == "md":
        out.write_text(md, encoding="utf-8")
    elif fmt in ("docx", "html"):
        from workflow_stages import stage_convert      # 反向 import 会成环，用时再拿
        stage_convert(db, task_id, md, fmt, safe)
        out = folder / f"{safe}.{fmt}"
    elif fmt == "latex":
        build = folder / "_tex"
        build.mkdir(parents=True, exist_ok=True)
        tex_path = build / f"{safe}.tex"
        tex_path.write_text(md_to_tex(md, title), encoding="utf-8", newline="\n")   # 必须 LF：CR 会让 TeX 报 ^^M
        env = lowbox.sandbox_env(fileguard.clean_env(str(Path(_xelatex()).parent)), paths.LOWTMP)   # 子进程不需要我们的 key
        proc = lowbox.run([_xelatex(), "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
                          cwd=str(build), env=env, timeout=180)
        made = build / f"{safe}.pdf"
        if proc.returncode != 0 or not made.is_file():
            tail = "\n".join((proc.stdout or "").splitlines()[-12:])
            raise RuntimeError(f"xelatex 失败（退出码 {proc.returncode}）：{tail[-600:]}")
        shutil.copy2(made, out)
    else:
        raise ValueError(f"不支持的格式：{fmt}（可用 {'/'.join(CONV_FORMATS)}）")
    db.log_step(task_id, "convert", "done", f"导出 {out.name}")
    return {"path": str(out), "name": out.name, "format": fmt, "size": out.stat().st_size,
            "pdf": fmt == "latex", "source": str(src)}


CONV_EXT = (".md", ".tex", ".txt", ".json", ".csv", ".html", ".docx", ".pdf")


def conv_files(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """左边文件区：项目里这几个子夹的可读文件（按时间倒序）+ 最新那份 PDF（右边默认预览它）。"""
    rows: list[dict[str, Any]] = []
    labels = [("论文正文", _doc_dir(db, task_id)), ("最终产物", db.pdir(task_id, "最终产物")),
              ("LaTeX 源文件", db.pdir(task_id, "最终产物") / "_tex")]
    newest_pdf = ""
    seen: set[str] = set()
    for label, d in dict.fromkeys(labels):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*"), key=lambda x: -x.stat().st_mtime if x.is_file() else 0):
            if not p.is_file() or p.suffix.lower() not in CONV_EXT or str(p) in seen:
                continue                       # 没设项目位置时论文正文/最终产物是同一个目录，别把同一份列两遍
            if p.name.startswith(CITE_FILE[:-3]) and p.suffix.lower() == ".json":
                continue                       # 台账的 json 是内部账本，不摆到文件区
            seen.add(str(p))
            st = p.stat()
            rows.append({"name": p.name, "path": str(p), "ext": p.suffix.lower()[1:], "size": st.st_size,
                         "group": label, "at": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")})
            if p.suffix.lower() == ".pdf" and not newest_pdf:
                newest_pdf = str(p)
    return {"files": rows[:80], "dirs": [str(d) for _, d in dict.fromkeys(labels)], "pdf": newest_pdf}


def conv_compile(path: str) -> dict[str, Any]:
    """编译任意一份 .tex（代码区「⚙ 编译」用）：产物 PDF 放在同目录。"""
    p = _safe_path(path)
    if p.suffix.lower() != ".tex" or not p.is_file():
        raise ValueError("只能编译 .tex 文件")
    pdf = p.with_suffix(".pdf")
    env = lowbox.sandbox_env(fileguard.clean_env(str(Path(_xelatex()).parent)), paths.LOWTMP)       # 子进程不需要我们的 key
    try:
        proc = lowbox.run([_xelatex(), "-interaction=nonstopmode", "-halt-on-error", p.name],
                          cwd=str(p.parent), env=env, timeout=240)
    except OSError as exc:
        if getattr(exc, "winerror", None) in (2, 3, None) and "xelatex" not in str(exc):
            raise RuntimeError("本机没找到 xelatex（装 MiKTeX 或把它加进 PATH）") from exc
        if getattr(exc, "winerror", None) == 5:
            raise RuntimeError("xelatex 没有写入权限：这份 .tex 所在的目录还没被标成可写"
                               "（重启一次程序即可，或把文件放进项目文件夹）") from exc
        raise
    if proc.returncode != 0 or not pdf.is_file():
        tail = "\n".join((proc.stdout or "").splitlines()[-10:])
        raise RuntimeError(f"xelatex 失败（退出码 {proc.returncode}）：{tail[-500:]}")
    return {"pdf": str(pdf), "name": pdf.name, "size": pdf.stat().st_size}


def conv_open(path: str) -> dict[str, Any]:
    """用系统默认程序打开（预览窗口右上那个 ↗）。**只放行文档类扩展名**。"""
    p = _safe_path(path)
    return fileguard.open_document(p)


def _safe_path(path: str, extra_roots: list[Path] | None = None) -> Path:
    """只允许项目目录 / data 目录里的文件（读写都走它）。规则本体只有一份：paths.resolve_allowed()。"""
    return paths.resolve_allowed(path, extra_roots)


# ============================ 中译英（中文 markdown → 英文 markdown，左右对照）============================
EN_STEP = "entrans"
EN_LIMIT = 1800                                        # 每片多少字送去翻（太大容易截断）


def en_chunks(md: str, limit: int = EN_LIMIT) -> list[str]:
    """按空行切块再拼到 limit 左右 —— 尽量不切断段落；单块还超长就按行切。"""
    blocks, cur = [], ""
    for block in re.split(r"\n{2,}", md or ""):
        if cur and len(cur) + len(block) + 2 > limit:
            blocks.append(cur)
            cur = block
        else:
            cur = (cur + "\n\n" + block) if cur else block
    if cur:
        blocks.append(cur)
    out: list[str] = []
    for b in blocks:
        while len(b) > limit * 2:                      # 罕见的超长块：按行切
            cut = b.rfind("\n", 0, limit * 2)
            cut = cut if cut > limit else limit * 2
            out.append(b[:cut])
            b = b[cut:]
        if b.strip():
            out.append(b)
    return out


EN_SYSTEM_STYLES = {
    "formal": "你是专业的材料/计算科学英文学术润色与翻译专家。使用严谨纯正的学术标准英语，符合 Nature/Science/Cell 等顶刊用词规范与句式结构。",
    "concise": "你是注重高信息密度的学术编辑。在保持学术严密性的前提下，追求极简凝练，精简冗余介词短语与被动语态，提升表达紧凑度与可读性。",
    "impactful": "你是资深顶刊论文润色专家。着重强化研究的创新贡献与突破性，选用具有学术感染力与说服力的精准强动词与权威句式。",
}
EN_SYSTEM = EN_SYSTEM_STYLES["formal"] + (
    "把用户给的**中文** markdown 译成**英文** markdown，"
    "只输出译文本身：不要任何解释、前言、总结，也不要用 ``` 围栏包起来。"
    "要求：markdown 结构原样保留（#/##/### 层级、列表、表格、加粗、分隔线）；"
    "公式、LaTeX、DOI、URL、数字、人名、机构名、[引用] 原样保留；"
    "术语用材料/计算领域的常用英文说法；不增删内容，不合并段落。"
)


def en_translate(profile: dict[str, Any], md: str, style: str = "formal") -> dict[str, Any]:
    """分片并发翻（顺序保持），返回 {text, chunks, ms}。"""
    from concurrent.futures import ThreadPoolExecutor
    chunks = en_chunks(md)
    if not chunks:
        return {"text": "", "chunks": 0, "ms": 0}
    t0 = time.time()
    style_desc = EN_SYSTEM_STYLES.get(style, EN_SYSTEM_STYLES["formal"])
    sys_prompt = (
        f"{style_desc}把用户给的**中文** markdown 译成**英文** markdown，"
        "只输出译文本身：不要任何解释、前言、总结，也不要用 ``` 围栏包起来。"
        "要求：markdown 结构原样保留（#/##/### 层级、列表、表格、加粗、分隔线）；"
        "公式、LaTeX、DOI、URL、数字、人名、机构名、[引用] 原样保留；"
        "术语用材料/计算领域的常用英文说法；不增删内容，不合并段落。"
    )

    from workflow import chat                          # chat 住在 workflow.py，反向 import 会成环

    def one(piece: str) -> str:
        out = chat(profile, [{"role": "system", "content": sys_prompt},
                             {"role": "user", "content": piece}], temperature=0.1, max_tokens=4096)
        out = re.sub(r"^```(?:markdown|md)?\s*\n|```\s*$", "", (out or "").strip()).strip()
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(one, chunks))
    return {"text": "\n\n".join(p for p in parts if p), "chunks": len(chunks),
            "ms": int((time.time() - t0) * 1000), "style": style}


def en_state(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """默认拿「正文.md」当源；同时把上次译好的 <名字>_en.md 读回来。"""
    src = doc_path(db, task_id, "write")
    res = src.with_name(src.stem + "_en.md")
    return {"source_path": str(src), "source_text": src.read_text(encoding="utf-8", errors="replace") if src.is_file() else "",
            "result_path": str(res), "result_text": res.read_text(encoding="utf-8", errors="replace") if res.is_file() else "",
            "source_exists": src.is_file(), "result_exists": res.is_file()}


def en_run(db: "WorkflowDB", task_id: str, text: str, name: str = "", style: str = "formal") -> dict[str, Any]:
    """一键翻译：中文 markdown → 英文 markdown，存成 <源名>_en.md（和源同一目录）。"""
    md = (text or "").strip()
    if not md:
        raise ValueError("没有可翻的内容 —— 先把中文正文放进来（点左上 ＋ 或拖文件进来）")
    profile = db.active_model()
    if not profile:
        raise RuntimeError("还没配模型：去「模型」页加一个 OpenAI 兼容接口并设为当前模型")
    out = en_translate(profile, md, style=style)
    stem = re.sub(r"[\\/:*?\"<>|]+", "_", (name or "正文").strip()) or "正文"
    stem = stem[:-3] if stem.lower().endswith(".md") else stem
    folder = _doc_dir(db, task_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stem}_en.md"
    fileguard.before_write(dest, "中译英覆盖")
    dest.write_text(out["text"] + "\n", encoding="utf-8", newline="\n")
    db.log_step(task_id, "entrans", "done", f"译成 {dest.name}")
    return {"path": str(dest), "name": dest.name, "text": out["text"], "chars": len(out["text"]),
            "sections": len(re.findall(r"(?m)^#{1,6} ", out["text"])), "src_chars": len(md),
            "chunks": out["chunks"], "ms": out["ms"], "style": style}


def compose_draft(db: WorkflowDB, task_id: str) -> str:
    """把该任务下最新的草稿/审查/笔记拼成一篇可转换的稿件。"""
    task = db.summary(task_id)["task"]
    notes = db.notes(task_id)
    return "\n\n".join([f"# {task['title']}", f"> 研究问题：{task['question']}", f"> 关键词：{task['material_system']}"]
                       + [n["markdown"] for n in notes])
