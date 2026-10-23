# -*- coding: utf-8 -*-
"""文档转换、引用台账、LaTeX 编译与中译英模块。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
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
FIG_DIR = "图"          # 项目里的成图夹：格式转换左栏、文章撰写的图位都读它

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


def _cite_page_ok(db: "WorkflowDB", e: dict[str, Any]) -> bool | None:
    """这条引用的数值，是否真在它声称的那一页上。None = 核不了（无全文 / 无页码 / 纯定性）。"""
    import verify
    pid, page = str(e.get("paper_id") or ""), str(e.get("cite_page") or "").strip()
    if not (pid and page.isdigit()):
        return None
    try:
        pages = {p["page_no"]: p["text"] for p in db.paper(pid)["pages"]}
    except Exception:
        return None
    if int(page) not in pages:
        return False
    qs = verify.quantities(e.get("anchor") or "")
    if not qs:
        return None
    return any(verify.qty_in_page(raw, pages[int(page)]) for _, _, raw in qs)


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
        e["page_ok"] = _cite_page_ok(db, e) if ok else None
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
        if not ok:
            state = "⚠ 正文里找不到这句（可能已改写）" if anchor else "– 未给锚句"
        elif e["page_ok"] is False:
            state = f"⚠ 锚句在正文，但数值不在 p.{e.get('cite_page')}"
        elif e["page_ok"] is True:
            state = "✓ 锚句在正文，数值在引用页"
        else:
            state = "✓ 锚句在正文（无全文/无页码，未核数值）"
        lines.append(f"- 状态：{state}")
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
            "suspect": sum(1 for e in entries if e.get("page_ok") is False), "markdown": "\n".join(lines)}


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


# ---------------- 引用台账 → 家格式 .bib（元数据只从 Zotero 取，不自己拼） ----------------
CITE_BIB = "引用台账.bib"
BIB_KEEP = ("author", "title", "journal", "booktitle", "volume", "number", "pages", "year", "doi")
BIB_DROP = {"issn", "isbn", "url", "urldate", "month", "language", "shorttitle", "file", "publisher",
            "address", "abstract", "keywords", "numpages", "eprint", "archiveprefix", "primaryclass",
            "series", "edition", "chapter", "institution", "school", "organization", "note", "annote"}


def cite_bib_path(db: "WorkflowDB", task_id: str) -> Path:
    base = db.base_dir(task_id)
    return (Path(base) if base else OUT) / "文献" / CITE_BIB


def bib_house(text: str) -> str:
    """Zotero 导出的 BibTeX → 家格式：只留必要字段，页码单横线改双横线。"""
    head = re.search(r"@(\w+)\s*\{\s*([^,\n]+),", text)
    if not head:
        return ""
    fields: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        m = re.match(r"\s*(\w+)\s*=\s*\{(.*)\}\s*,?\s*$", line)
        if not m:
            continue
        name = m.group(1).lower()
        if name in BIB_DROP:
            continue
        fields[name] = " ".join(m.group(2).split())
    if "pages" in fields:
        fields["pages"] = re.sub(r"(\d)\s*-\s*(\d)", r"\1--\2", fields["pages"])
    body = ",\n".join(f"  {name:<8}= {{{fields[name]}}}" for name in BIB_KEEP if name in fields)
    return "@%s{%s,\n%s\n}" % (head.group(1).lower(), head.group(2).strip(), body)


def cite_bib(db: "WorkflowDB", task_id: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """引用台账 → <项目>/文献/引用台账.bib。每条元数据从 Zotero 本机库取；库里没有就报缺，绝不自己编。"""
    import asset_service
    import zotero

    entries = _cite_load(db, task_id)
    packed: dict[str, str] = {}
    keys: dict[int, str] = {}                 # 台账编号 C<n> → BibTeX citekey（正文 {{Cn}} 靠它转 \cite）
    missing: list[dict[str, Any]] = []
    for i, e in enumerate(entries, 1):
        doi = str(e.get("doi") or "").strip()
        title = str(e.get("title") or "").strip()
        if not (doi or title):
            missing.append({"index": i, "reason": "台账这条没给 DOI / 题目"})
            continue
        try:
            hit = zotero.bibtex_for(doi=doi, title=title)
        except Exception as exc:                               # noqa: BLE001 — Zotero 没开也要如实回报
            missing.append({"index": i, "title": title, "reason": str(exc)})
            continue
        if not hit.get("found"):
            missing.append({"index": i, "title": title, "doi": doi,
                            "reason": "Zotero 里没有这篇：先导进 Zotero（项目分类），再重跑"})
            continue
        item = bib_house(hit["bibtex"])
        if not item:
            missing.append({"index": i, "title": title, "reason": "Zotero 导出的 BibTeX 解析不了"})
            continue
        km = CITE_KEY_RE.match(item)
        if km:
            keys[i] = km.group(1)
        packed.setdefault((hit.get("doi") or doi or title).lower(), item)

    path = cite_bib_path(db, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fileguard.before_write(path, "引用台账")
    text = ("% House-style bibliography (引用台账，元数据来自 Zotero)\n\n" + "\n\n".join(packed.values()) + "\n"
            if packed else "")
    asset_service.atomic_write_text(path, text)
    if packed:
        asset_service.register_file(db, path, project_id=task_id, kind="document", title=CITE_BIB,
                                    source="workflow", metadata={"document_kind": "citation_bib"})
    return {"path": str(path), "count": len(packed), "keys": keys, "ledger": len(entries), "missing": missing,
            "message": (f"{len(packed)} 条已写入 {CITE_BIB}" + (f"，{len(missing)} 条缺 Zotero 记录" if missing else "")
                        if packed else "一条都没写出来：台账是空的，或这些文献都还没进 Zotero")}


# ---------------- 格式转换（照 Overleaf：文件 / 代码 / 编译 → PDF） ----------------
# 期刊/模板库：优先本机 MiKTeX 已装的 class；GitHub 只作为「模板从哪来」的出处
# 公开模板库：bamos/latex-templates、quantixed/manuscript-templates、jsk-report-template/elsarticle-template、
#            ZhenhongDu/Templates-for-submission-to-optical-journals（光学类）
TEMPLATES: dict[str, dict[str, str]] = {
    "plain":    {"name": "本机通用（article + ctex）", "cls": "article", "repo": "",
                 "note": "不挑期刊时的默认：中文友好、图表编号规矩"},
    "elsevier": {"name": "Elsevier（elsarticle）", "cls": "elsarticle",
                 "repo": "https://github.com/jsk-report-template/elsarticle-template",
                 "note": "Elsevier 系期刊（Optics & Laser Technology、Applied Surface Science 等）"},
    "ieee":     {"name": "IEEE（IEEEtran）", "cls": "IEEEtran",
                 "repo": "https://github.com/bamos/latex-templates",
                 "note": "IEEE 期刊/会议，双栏"},
    "revtex":   {"name": "APS 物理类（revtex4-2）", "cls": "revtex4-2",
                 "repo": "https://github.com/bamos/latex-templates",
                 "note": "PRB / PRApplied 这类 APS 期刊"},
}

_TEX_HEAD = {
    "plain": [r"\documentclass[11pt,a4paper]{article}",
              r"\usepackage{ctex}",
              r"\setlength{\textwidth}{16cm}",
              r"\setlength{\oddsidemargin}{0pt}",
              r"\setlength{\evensidemargin}{0pt}"],
    "elsevier": [r"\documentclass[preprint,12pt]{elsarticle}",
                 r"\usepackage{ctex}"],
    "ieee": [r"\documentclass[journal]{IEEEtran}",
             r"\usepackage{ctex}"],
    "revtex": [r"\documentclass[aps,prb,reprint]{revtex4-2}",
               r"\usepackage{ctex}"],
}


CONV_FORMATS = {"docx": "Word (.docx)", "doc": "Word 97-2003 (.doc)", "html": "HTML", "md": "Markdown", "pdf": "PDF（正文.md → PDF）",
                "latex": "LaTeX (.tex → PDF，本机 MiKTeX)", "tex": "现成 .tex → PDF（本机 MiKTeX）"}

# ---------------- 引用：正文标记 {{C3}} ↔ 引用台账第 3 条 ↔ BibTeX key ----------------
CITE_MARK_RE = re.compile(r"\{\{\s*C(\d+)\s*\}\}")
CITE_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
BIB_NAME = "refs"          # 构建夹里的 .bib 用纯 ASCII 名：老 bibtex 吃不动中文文件名（实测）
BUILD_STEM = "paper"        # 同理：.tex/.aux 也得 ASCII，否则 bibtex 报「I couldn't open file name」，.bbl 空
CITE_STYLES: dict[str, tuple[str, str]] = {
    "gbt7714":    ("gbt7714-numeric", "GB/T 7714 顺序编码（中文期刊 / 学位论文）"),
    "gbt7714-ay": ("gbt7714-author-year", "GB/T 7714 著者-出版年制（Word/HTML/MD：正文 (著者 等, 年)、文后按著者字顺、同著者同年 a/b；PDF 仍数字式）"),
    "apa":        ("apalike", "APA 风格（作者-年份，Word/HTML/MD；PDF 仍数字式）"),
    "unsrt":      ("unsrt", "unsrt（英文通用，按正文出现顺序编号）"),
    "plain":      ("plain", "plain（英文通用，按作者字母排序编号）"),
    "ieeetr":     ("ieeetr", "ieeetr（IEEE 数字式）"),
    "elsarticle": ("elsarticle-num", "elsarticle-num（Elsevier 数字式）"),
    "aps":        ("apsrev4-2", "APS / revtex 数字式"),
}
_TEMPLATE_CITE = {"plain": "gbt7714", "elsevier": "elsarticle", "ieee": "ieeetr", "revtex": "aps"}
AY_STYLES = ("gbt7714-ay", "apa")            # 著者-出版年制：正文给 (著者 年)、文后按著者字顺
AY_ETAL_FROM = 4                              # ≥4 位作者才用「等 / et al.」，1–3 位全列（常见期刊口径）


def is_author_year(cite_style: str = "", bst: str = "") -> bool:
    """这个样式是不是著者-出版年制（决定 Word 正文给 (著者, 年) 还是 [n]）。"""
    key = (cite_style or "").strip().lower()
    return key in AY_STYLES or (bst or "").startswith(("gbt7714-author-year", "apalike"))


def bib_style(template: str = "", cite_style: str = "") -> tuple[str, str, str]:
    """→（.bst 名, 要顺手装的宏包, 说明）。用户选的优先，没选按期刊模板给默认；本机没这个 bst 就退 plain。

    宏包那一项是 gbt7714 —— 它的 .bbl 用 \\doi{}，不装宏包直接编不出。
    著者-出版年制（gbt7714-ay / apa）只在 Word/HTML/MD 那条路上生效：PDF 走 bst 时标签会被
    natbib 排成方括号、gbt7714 宏包又会把 cite 翻成数字式，实测两头都不对，所以 PDF 退回数字式并说明。
    """
    key = (cite_style or "").strip().lower()
    if key not in CITE_STYLES:
        key = _TEMPLATE_CITE.get(template, "gbt7714")
    if key in AY_STYLES:
        bst, label = CITE_STYLES[key]
        return ("gbt7714-numeric", "gbt7714",
                "%s 只在 Word/HTML/MD 里生效；PDF 仍按 GB/T 7714 数字式排（%s.bst 的标签会被排成方括号）"
                % (label.split("（")[0], bst))
    bst, label = CITE_STYLES[key]
    if not _kpsewhich(bst + ".bst"):
        return "plain", "", "%s 的 %s.bst 本机没装，退回 plain" % (label, bst)
    return bst, ("gbt7714" if bst.startswith("gbt7714") else ""), ""


def _kpsewhich(name: str) -> bool:
    """本机有没有这个 .bst/.sty。探测不出来就当有 —— 别把编译堵死在探路上。"""
    exe = Path(_xelatex()).with_name("kpsewhich.exe")
    try:
        proc = subprocess.run([str(exe) if exe.is_file() else "kpsewhich", name],
                              capture_output=True, text=True, timeout=20,
                              creationflags=0x08000000 if os.name == "nt" else 0)
        return bool((proc.stdout or "").strip())
    except Exception:
        return True


def _kpse_path(name: str) -> str:
    """kpsewhich 的完整路径版（找不到就空串）。"""
    exe = Path(_xelatex()).with_name("kpsewhich.exe")
    try:
        proc = subprocess.run([str(exe) if exe.is_file() else "kpsewhich", name],
                              capture_output=True, text=True, timeout=20,
                              creationflags=0x08000000 if os.name == "nt" else 0)
        lines = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        return lines[-1] if lines else ""
    except Exception:
        return ""


def _aux_wants_bibtex(aux: Path) -> bool:
    try:
        text = aux.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "\\bibdata" in text or "\\citation" in text


def _bibtex_candidates() -> list[str]:
    """候选按可靠性排：这台机器上经典 bibtex.exe 会 0xC0000374（堆损坏）直接崩，
    bibtex8 / bibtexu 正常 —— 所以挨个试，谁真产出 .bbl 就用谁。"""
    folder = Path(_xelatex()).parent
    names = (["bibtex.exe", "bibtex8.exe", "bibtexu.exe"] if os.name == "nt"
             else ["bibtex", "bibtex8", "bibtexu"])
    return [str(folder / n) if (folder / n).is_file() else n for n in names]


def _tail(proc: Any, n: int = 10) -> str:
    text = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    return "\n".join(text.splitlines()[-n:])


def latex_chain(tex: Path, run: Any) -> dict[str, Any]:
    """xelatex →（.aux 要参考文献时）bibtex → xelatex ×2。

    以前只跑一遍 xelatex：带 \\cite 的稿子编号全落成 ?? —— 既没跑 bibtex，也没第二遍排交叉引用。
    run(cmd_list, cwd) 返回带 returncode/stdout/stderr 的东西（宿主 subprocess、沙箱 lowbox.run 都行）。
    """
    tex = Path(tex)
    xel = _xelatex()
    pdf = tex.with_suffix(".pdf")
    if pdf.is_file():
        pdf.unlink()                     # 上一轮留下的 PDF 不能冒充这一轮的产物
    passes = 0

    def once() -> Any:
        nonlocal passes
        passes += 1
        return run([xel, "-interaction=nonstopmode", "-halt-on-error", tex.name], tex.parent)

    last = once()
    note = ""
    aux = tex.with_suffix(".aux")
    bbl = tex.with_suffix(".bbl")
    if aux.is_file() and _aux_wants_bibtex(aux):
        if bbl.is_file():
            bbl.unlink()                 # 空/旧的 .bbl 会让「编出来了」变成假象
        for exe in _bibtex_candidates():
            proc = run([exe, tex.stem], tex.parent)
            if bbl.is_file() and bbl.stat().st_size > 0:     # 判据是 .bbl 真生成了，不是退出码
                note = "ok(%s)" % Path(exe).stem              # 弃用样式名会给 rc=1，但 .bbl 是对的
                last = once()
                last = once()            # 引文进 .bbl，再两遍编号才落定
                break
            note = "bibtex 失败（%s，退出码 %s）：%s" % (
                Path(exe).stem, getattr(proc, "returncode", "?"), _tail(proc, 6)[-300:])
    if getattr(last, "returncode", 0) != 0 or not pdf.is_file():
        return {"error": "xelatex 失败（最后一趟退出码 %s）：%s"
                         % (getattr(last, "returncode", "?"), _tail(last)[-600:]),
                "passes": passes, "bibtex": note}
    return {"pdf": str(pdf), "passes": passes, "bibtex": note}


def _ref_line(num: int, e: dict[str, Any]) -> str:
    """台账一条 → GB/T 7714 顺序编码的一条（[n] 作者. 题名[J]. 出处, 年: 页. DOI: …）。
    这里只用台账字段排版，不引 bibtex —— Word/HTML 走这条，PDF 走 .bst。"""
    authors = str(e.get("authors") or "").strip().rstrip(".")
    title = str(e.get("title") or "").strip().rstrip(".")
    venue = str(e.get("venue") or "").strip()
    year = str(e.get("year") or "").strip()
    pages = str(e.get("pages") or "").strip()
    doi = str(e.get("doi") or "").strip()
    url = str(e.get("url") or "").strip()
    head = ("%s. " % authors if authors else "") + (title or "（题名待补）") + "[J]"
    tail = ", ".join(x for x in (venue, year) if x)      # 题录里用西文逗号，顿号是中文并列用的
    if pages:
        tail = (tail + ": " + pages) if tail else pages
    line = head + ". " + (tail + "." if tail else "")
    if doi:
        line += " DOI: %s." % doi
    elif url:
        line += " %s" % url
    return "[%d] %s" % (num, line.strip())


def _author_list(e: dict[str, Any]) -> list[str]:
    raw = str(e.get("authors") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[;,，；、]|\s+and\s+", raw) if p.strip()]


def _surname(name: str) -> str:
    """台账里的作者串通常是「Smith J」或「张三」，取正文标注要用的那一段（姓）。"""
    name = (name or "").strip()
    if not name:
        return ""
    if any("\u4e00" <= ch <= "\u9fff" for ch in name):
        return name[:2] if len(name) > 2 else name          # 中文：前两字当简称（张三 / 欧阳）
    parts = name.split()
    if len(parts) == 1:
        return name
    if parts[0].endswith("."):                             # J. Smith → Smith
        return parts[-1]
    return parts[0]                                        # Smith J → Smith


def _author_key(e: dict[str, Any]) -> str:
    return " ".join(_surname(n) for n in _author_list(e)).lower()


def _ay_label(e: dict[str, Any], suffix: str = "") -> str:
    """著者-出版年制正文标注：1–3 位著者全列，≥4 位用「等 / et al.」，同年加 a/b；缺年份按 n.d.。"""
    names = _author_list(e)
    year = str(e.get("year") or "").strip() or "n.d."
    if not names:
        return "(佚名, %s%s)" % (year, suffix)
    cjk = any("\u4e00" <= ch <= "\u9fff" for ch in names[0])
    sur = [_surname(n) for n in names if _surname(n)]
    if len(sur) >= AY_ETAL_FROM:
        who = sur[0] + (" 等" if cjk else " et al.")
    else:
        who = ("、" if cjk else ", ").join(sur)
    return "(%s, %s%s)" % (who, year, suffix)


def _ay_ref(e: dict[str, Any], suffix: str = "") -> str:
    """著者-出版年制文后条目：著者. 年a. 题名[J]. 出处: 页. DOI: …"""
    authors = str(e.get("authors") or "").strip().rstrip(".")
    title = str(e.get("title") or "").strip().rstrip(".") or "（题名待补）"
    year = (str(e.get("year") or "").strip() or "n.d.") + suffix
    venue = str(e.get("venue") or "").strip()
    pages = str(e.get("pages") or "").strip()
    doi = str(e.get("doi") or "").strip()
    url = str(e.get("url") or "").strip()
    line = "%s. %s. %s[J]. " % (authors, year, title) if authors else "%s. %s[J]. " % (year, title)
    tail = venue + (": " + pages if pages else "")
    line += (tail + ".") if tail else ""
    if doi:
        line += " DOI: %s." % doi
    elif url:
        line += " %s" % url
    return line.strip()


def doc_cites(db: "WorkflowDB", task_id: str, md: str, cite_style: str = "") -> dict[str, Any]:
    """Word / HTML / Markdown 导出用：正文 {{Cn}} → 标注，并给出文末参考文献条目。

    cite_style 决定两种口径（跟 PDF 那条 .bst 对齐）：
      * 顺序编码（默认，gbt7714 等）：正文 [n]（按出现顺序、同篇同号），文后按出现顺序；
      * 著者-出版年制（gbt7714-ay / apa）：正文 (著者 等, 年)，文后按**著者字顺**，同著者同年加 a/b。
    台账里找不到的编号原样保留标记，并在 missing 里报出来。
    """
    entries = _cite_load(db, task_id)
    order: list[int] = []
    for m in CITE_MARK_RE.finditer(md or ""):
        num = int(m.group(1))
        if num not in order:
            order.append(num)
    known = [num for num in order if 1 <= num <= len(entries)]
    missing = [num for num in order if num not in known]

    if is_author_year(cite_style):
        groups: dict[tuple[str, str], list[int]] = {}
        for num in known:
            e = entries[num - 1]
            groups.setdefault((_author_key(e), str(e.get("year") or "")), []).append(num)
        suffix: dict[int, str] = {}
        for nums in groups.values():                       # 同著者同年 → a/b/c，组内按题名排
            if len(nums) == 1:
                suffix[nums[0]] = ""
            else:
                for i, num in enumerate(sorted(nums, key=lambda n: str(entries[n - 1].get("title") or ""))):
                    suffix[num] = "abcdefghij"[i] if i < 10 else str(i + 1)
        labels = {num: _ay_label(entries[num - 1], suffix[num]) for num in known}
        ordered = sorted(known, key=lambda n: (_author_key(entries[n - 1]),
                                              str(entries[n - 1].get("year") or ""), suffix[n]))
        refs = [_ay_ref(entries[n - 1], suffix[n]) for n in ordered]
        body = CITE_MARK_RE.sub(
            lambda m: labels[int(m.group(1))] if int(m.group(1)) in labels else m.group(0), md or "")
        return {"md": body, "refs": refs, "used": len(order), "ledger": len(entries),
                "missing": missing, "mode": "author-year"}

    numbers = {num: i + 1 for i, num in enumerate(order)}
    known_nums = {num: numbers[num] for num in known}
    body = CITE_MARK_RE.sub(
        lambda m: "[%d]" % known_nums[int(m.group(1))] if int(m.group(1)) in known_nums else m.group(0), md or "")
    refs = [_ref_line(known_nums[num], entries[num - 1]) for num in order if num in known_nums]
    return {"md": body, "refs": refs, "used": len(order), "ledger": len(entries),
            "missing": missing, "mode": "numeric"}


def with_references(md: str, refs: list[str]) -> str:
    """正文末尾接一节「参考文献」。docx 渲染器认得这个标题（引用条目按悬挂缩进排）。"""
    if not refs:
        return md
    return (md or "").rstrip() + "\n\n## 参考文献\n\n" + "\n".join(refs) + "\n"


def _line_with_cites(line: str, cite_keys: dict[int, str] | None, missing: set[int]) -> str:
    """一行正文 → tex：`{{C3}}` 换成 `\\cite{key}`；对不上编号的原样留着并记进 missing（不悄悄丢引用）。"""
    out: list[str] = []
    pos = 0
    for m in CITE_MARK_RE.finditer(line):
        out.append(tex_escape(line[pos:m.start()]))
        num = int(m.group(1))
        key = (cite_keys or {}).get(num)
        if key:
            out.append(r"\cite{%s}" % key)
        else:
            missing.add(num)
            out.append(tex_escape(m.group(0)))
        pos = m.end()
    out.append(tex_escape(line[pos:]))
    return "".join(out)


def md_to_tex(md: str, title: str, template: str = "plain",
              cite_keys: dict[int, str] | None = None, cite_style: str = "",
              cite_package: str = "") -> str:
    """极简 markdown → tex（标题/段落/列表够用），外加引用：正文 `{{C3}}` → `\\cite{}`，文末挂参考文献。

    真要投稿的排版还是走组内 revtex 规范。注意：这里**不能**用 % 或 str.format 打模板（LaTeX 自带 {} 和 % 注释）。
    """
    missing: set[int] = set()
    head = list(_TEX_HEAD.get(template) or _TEX_HEAD["plain"])
    if (cite_package or cite_style or "").startswith("gbt7714"):
        # gbt7714-numerical.bst 吐出来的 .bbl 用 \doi{} / \cstr{}，宏包不装就是 Undefined control
        # sequence，整份直接编不出来（编号也就不会变成 [1]）。顺序编码必须先装它。
        head.append(r"\usepackage{gbt7714}")
    head += [
        r"\title{" + tex_escape(title) + r"}",
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
            lvl, txt = len(m.group(1)), _line_with_cites(m.group(2), cite_keys, missing)
            body.append(r"\section{%s}" % txt if lvl <= 2 else (r"\subsection{%s}" % txt if lvl == 3 else r"\subsubsection{%s}" % txt))
        elif line.startswith("- "):
            bullets.append(r"\item " + _line_with_cites(line[2:], cite_keys, missing))
        elif line.strip():
            body.append(_line_with_cites(line, cite_keys, missing))
            body.append("")
    if bullets:
        body += [r"\begin{itemize}", *bullets, r"\end{itemize}"]
    tail: list[str] = []
    if cite_keys:
        tail = ["", r"\bibliographystyle{%s}" % (cite_style or "plain"), r"\bibliography{%s}" % BIB_NAME]
    return "\n".join(head + body + tail + ["", r"\end{document}", ""])


_TEX_MAP = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}

# lmodern 里没有希腊字母/上下标/数学符号这些字形，直接写进 tex 会静默丢字（实测 β-Ga₂O₃ 全部消失）。
# 换数学模式才打得出来 —— 字符 → 命令名，值的 $...$ 在下面拼。
_MATH_SYMS = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "θ": r"\theta",
    "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ρ": r"\rho", "σ": r"\sigma",
    "τ": r"\tau", "φ": r"\varphi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Δ": r"\Delta", "Ω": r"\Omega", "Σ": r"\Sigma", "Γ": r"\Gamma", "Θ": r"\Theta",
    "Λ": r"\Lambda", "Π": r"\Pi", "Φ": r"\Phi",
    "≤": r"\le", "≥": r"\ge", "≠": r"\neq", "≈": r"\approx", "±": r"\pm", "×": r"\times",
    "÷": r"\div", "→": r"\rightarrow", "∞": r"\infty", "°": r"^\circ",
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4", "₅": "_5", "₆": "_6", "₇": "_7",
    "₈": "_8", "₉": "_9", "⁻": "^-",
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7",
    "⁸": "^8", "⁹": "^9",
}
for _ch, _cmd in _MATH_SYMS.items():
    _TEX_MAP[_ch] = "$" + _cmd + "$"


def tex_escape(s: str) -> str:
    return "".join(_TEX_MAP.get(c, c) for c in (s or ""))    # 逐字符换，避免把插入的反斜杠再转义一遍


def _xelatex() -> str:
    """本机 MiKTeX 的 xelatex（用户级装的那种）。"""
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64" / "xelatex.exe"
    if cand.is_file():
        return str(cand)
    return "xelatex"


def _newest_tex(db: "WorkflowDB", task_id: str) -> Path | None:
    """项目里最新的 .tex（最终产物 / _tex 构建夹、论文正文都算）。"""
    folder = db.pdir(task_id, "最终产物")
    cands = sorted(folder.rglob("*.tex"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def conv_run(db: "WorkflowDB", task_id: str, fmt: str, source: str = "", template: str = "",
             cite_style: str = "") -> dict[str, Any]:
    """一键格式转换：以正文.md（或指定文件）为源，导出到「最终产物」。

    pdf = 正文 → tex → PDF；latex = 同上但留一份 .tex；tex = 直接编手边那份现成 .tex
    （组内 revtex 稿、overleaf 导出、审改留下的 .tex 都走这条）。都是本机 MiKTeX 的 xelatex。
    """
    fmt = (fmt or "docx").lower()
    if fmt == "pdf":
        fmt = "latex"                                     # 同一条 md→tex→xelatex 路，产出都是 PDF
    folder = db.pdir(task_id, "最终产物")
    if fmt == "tex":                                      # 现成 .tex → PDF（不读正文.md）
        src_tex = Path(source) if source else _newest_tex(db, task_id)
        if not src_tex or not src_tex.is_file():
            raise ValueError("找不到要编译的 .tex：把 path 指到文件，或者先导出一份 LaTeX")
        safe = re.sub(r"[^\w\-. ]+", "_", src_tex.stem)[:60] or "paper"
        # 现成 .tex 若是中文名，先拷进构建夹用 ASCII 名编 —— bibtex 打不开中文 aux
        if not src_tex.stem.isascii():
            build = folder / "_tex"
            build.mkdir(parents=True, exist_ok=True)
            for bib in src_tex.parent.glob("*.bib"):
                shutil.copy2(bib, build / bib.name)
            target_tex = build / f"{BUILD_STEM}.tex"
            shutil.copy2(src_tex, target_tex)
            src_tex = target_tex
        done = host_compile(src_tex)
        passes, bib_note = int((done or {}).get("passes") or 0), str((done or {}).get("bibtex") or "")
        pdf = Path(done["pdf"]) if done and done.get("pdf") else src_tex.with_suffix(".pdf")
        if not pdf.is_file():
            env = lowbox.sandbox_env(fileguard.clean_env(str(Path(_xelatex()).parent)), paths.LOWTMP)
            got = latex_chain(src_tex, lambda cmd, cwd: lowbox.run(cmd, cwd=str(cwd), env=env, timeout=180))
            passes, bib_note = got.get("passes", 0), got.get("bibtex", "")
            if got.get("error") or not pdf.is_file():
                raise RuntimeError(got.get("error") or "编译没出 PDF")
        out = folder / f"{safe}.pdf"
        shutil.copy2(pdf, out)
        import asset_service
        asset_service.register_file(db, out, project_id=task_id, kind="export", source="format_convert",
                                    metadata={"source_path": str(src_tex), "format": fmt})
        db.log_step(task_id, "convert", "done", f"导出 {out.name}")
        return {"path": str(out), "name": out.name, "format": fmt, "size": out.stat().st_size,
                "pdf": True, "source": str(src_tex), "passes": passes, "bibtex": bib_note}
    src = Path(source) if source else doc_path(db, task_id, "write")
    md = src.read_text(encoding="utf-8", errors="replace") if src.is_file() else ""
    if not md.strip():
        raise ValueError(f"源文件是空的：{src}（先在「论文撰写」写出正文）")
    title = doc_read(db, task_id, "outline")["markdown"].splitlines()
    title = next((re.sub(r"^(题目|标题)[：:]\s*", "", l.lstrip("# ").strip()) for l in title if l.strip().startswith("#")),
                 db.summary(task_id)["task"]["title"] or Path(src).stem)      # 没框架就用项目名，别叫「正文」
    safe = re.sub(r"[^\w\-. ]+", "_", title)[:60] or "paper"
    out = folder / f"{safe}.{'pdf' if fmt == 'latex' else fmt}"
    # Word / HTML / Markdown 也带引用：{{Cn}} → [n]（按出现顺序），文末接「参考文献」
    doc_cite = {"used": 0, "missing": [], "refs": [], "ledger": 0}
    if fmt in ("md", "docx", "doc", "html") and CITE_MARK_RE.search(md or ""):
        doc_cite = doc_cites(db, task_id, md, cite_style)
        md = with_references(doc_cite["md"], doc_cite["refs"])
    if fmt == "md":
        out.write_text(md, encoding="utf-8")
    elif fmt in ("docx", "html", "doc"):
        from workflow_stages import stage_convert      # 反向 import 会成环，用时再拿
        base_fmt = "docx" if fmt == "doc" else fmt
        stage_convert(db, task_id, md, base_fmt, safe)
        out = folder / f"{safe}.{base_fmt}"
        if fmt == "doc":                              # 老版 Word 97：docx → doc（本机 LibreOffice）
            target = folder / f"{safe}.doc"
            env = lowbox.sandbox_env(fileguard.clean_env(), paths.LOWTMP)
            proc = lowbox.run([_soffice(), "--headless", "--norestore", "--convert-to", "doc:MS Word 97",
                               "--outdir", str(folder),
                               f"-env:UserInstallation=file:///{paths.LOWTMP.as_posix()}/lo", str(out)],
                              env=env, timeout=180)
            if proc.returncode != 0 or not target.is_file():
                raise RuntimeError("转 .doc 失败（本机 LibreOffice）")
            out = target
    elif fmt == "latex":
        build = folder / "_tex"
        build.mkdir(parents=True, exist_ok=True)
        tpl = template if template in TEMPLATES else "plain"
        # 引用：正文里的 {{Cn}} → \cite{key}，台账 → refs.bib（元数据走 Zotero，缺的不编）
        used = sorted({int(m) for m in CITE_MARK_RE.findall(md)})
        cite_keys: dict[int, str] = {}
        cite_missing: list[int] = []
        cite_note = ""
        if used:
            got = cite_bib(db, task_id)
            cite_keys = {n: got["keys"][n] for n in used if (got.get("keys") or {}).get(n)}
            cite_missing = [n for n in used if n not in cite_keys]
            src_bib = cite_bib_path(db, task_id)
            if cite_keys and src_bib.is_file():
                shutil.copy2(src_bib, build / (BIB_NAME + ".bib"))
            if cite_missing:
                cite_note = ("%d 处引用在 Zotero 里对不上（%s），PDF 里会原样留 {{Cn}}"
                             % (len(cite_missing), "、".join("C%d" % n for n in cite_missing[:6])))
            elif got.get("missing"):
                cite_note = "%d 条台账条目在 Zotero 里没记录，已跳过" % len(got["missing"])
        meta: dict[str, Any] = {"passes": 0, "bibtex": "", "style": "", "notes": [cite_note] if cite_note else []}

        def _build(name: str) -> Path:
            style, pkg, style_note = bib_style(name, cite_style) if cite_keys else ("", "", "")
            if style_note:
                meta["notes"].append(style_note)
            meta["style"] = style
            tex_path = build / f"{BUILD_STEM}.tex"       # 构建夹里必须 ASCII：bibtex 打不开中文 aux
            tex_path.write_text(md_to_tex(md, title, template=name, cite_keys=cite_keys, cite_style=style,
                                                 cite_package=pkg),
                                encoding="utf-8", newline="\n")
            done = host_compile(tex_path)
            meta["passes"] = int((done or {}).get("passes") or 0)
            meta["bibtex"] = str((done or {}).get("bibtex") or "")
            if done and not done.get("pdf") and done.get("error"):
                raise RuntimeError(done["error"])
            if done and done.get("pdf"):
                return Path(done["pdf"])
            env = lowbox.sandbox_env(fileguard.clean_env(str(Path(_xelatex()).parent)), paths.LOWTMP)
            got_c = latex_chain(tex_path, lambda cmd, cwd: lowbox.run(cmd, cwd=str(cwd), env=env, timeout=180))
            meta["passes"], meta["bibtex"] = got_c.get("passes", 0), got_c.get("bibtex", "")
            made_pdf = build / f"{BUILD_STEM}.pdf"
            if got_c.get("error") or not made_pdf.is_file():
                raise RuntimeError(got_c.get("error") or "编译没出 PDF")
            return made_pdf

        fallback = ""
        try:
            made = _build(tpl)
        except RuntimeError:
            if tpl == "plain":
                raise
            fallback = tpl
            tpl = "plain"
            made = _build("plain")
        shutil.copy2(made, out)
    else:
        raise ValueError(f"不支持的格式：{fmt}（可用 {'/'.join(CONV_FORMATS)}）")
    import asset_service
    asset_service.register_file(db, out, project_id=task_id, kind="export", source="format_convert",
                                body=md, metadata={"source_path": str(src), "format": fmt})
    db.log_step(task_id, "convert", "done", f"导出 {out.name}")
    res = {"path": str(out), "name": out.name, "format": fmt, "size": out.stat().st_size,
           "pdf": fmt == "latex", "source": str(src),
           "template": tpl if fmt == "latex" else "", "template_fallback": fallback if fmt == "latex" else ""}
    if fmt == "latex":
        res.update({"cites": {"used": len(used), "linked": len(cite_keys), "missing": cite_missing},
                    "cite_style": meta.get("style", ""), "passes": meta.get("passes", 0),
                    "bibtex": meta.get("bibtex", ""), "cite_note": "；".join(meta.get("notes") or [])})
    else:
        ay = doc_cite.get("mode") == "author-year"
        res["cites"] = {"used": doc_cite.get("used", 0), "missing": doc_cite.get("missing", []),
                        "mode": doc_cite.get("mode", ""),
                        "style": (("著者-出版年制（正文 (著者 等, 年)，文后按著者字顺）" if ay
                                   else "顺序编码（按出现顺序，GB/T 7714 式条目）")
                                  if doc_cite.get("used") else "")}
        if doc_cite.get("missing"):
            res["cite_note"] = ("%d 处引用在台账里找不到（%s），正文里保留了 {{Cn}}"
                                % (len(doc_cite["missing"]), "、".join("C%d" % n for n in doc_cite["missing"][:6])))
    return res


CONV_EXT = (".md", ".tex", ".txt", ".json", ".csv", ".html", ".docx", ".pdf")


def fig_dir(db: "WorkflowDB", task_id: str) -> Path:
    """项目里的「图」文件夹：论文最终要用成图放这儿（没有就建）。没设项目位置的按项目 id 分夹。"""
    base = db.base_dir(task_id)
    d = (base / FIG_DIR) if base else (OUT / f"{FIG_DIR}_{task_id[:8]}")
    d.mkdir(parents=True, exist_ok=True)
    return d


FIG_EXT = (".png", ".svg", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".pdf")


def fig_list(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """「格式转换」左栏：图文件夹里的图（新的在前），一份给前端直接用的清单。"""
    d = fig_dir(db, task_id)
    rows = []
    for p in sorted(d.glob("*"), key=lambda x: -x.stat().st_mtime if x.is_file() else 0):
        if not p.is_file() or p.suffix.lower() not in FIG_EXT:
            continue
        st = p.stat()
        rows.append({"name": p.name, "path": str(p), "ext": p.suffix.lower()[1:], "size": st.st_size,
                     "at": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")})
    return {"dir": str(d), "files": rows}


def fig_rename(db: "WorkflowDB", task_id: str, path: str, name: str) -> dict[str, Any]:
    """给图改名（只允许动「图」文件夹里的文件；没写扩展名就沿用原来的）。"""
    d = fig_dir(db, task_id).resolve()
    p = Path(str(path)).resolve()
    if d != p.parent:
        raise ValueError("只能改「图」文件夹里的文件")
    if not p.is_file():
        raise ValueError("文件不存在")
    clean = re.sub(r'[\\/:*?"<>|]+', "_", (name or "").strip()).strip(". ")
    if not clean:
        raise ValueError("新名字不能空")
    if not Path(clean).suffix:
        clean += p.suffix
    dest = p.with_name(clean)
    if dest != p:
        if dest.exists():
            raise ValueError("已经有同名文件")
        p.rename(dest)
        fileguard.audit("rename", dest, note=f"{p.name} → {clean}")
    return {"renamed": str(dest), **fig_list(db, task_id)}


def _soffice() -> str:
    """LibreOffice（docx → PDF 预览用；沙箱里也能跑，只要把用户配置指到可写目录）。"""
    cand = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    return str(cand) if cand.is_file() else "soffice"


def docx_to_pdf(src: Path) -> Path | None:
    """把 .docx 转成 PDF 给右栏预览。按 mtime 缓存在 data/cache/preview，转失败返回 None。"""
    cache = paths.CACHE / "preview"
    out = cache / f"{src.stem}-{int(src.stat().st_mtime)}.pdf"
    if out.is_file() and out.stat().st_size > 0:
        return out
    cache.mkdir(parents=True, exist_ok=True)
    env = lowbox.sandbox_env(fileguard.clean_env(), paths.LOWTMP)
    env["HOME"] = str(paths.LOWTMP)
    try:
        proc = lowbox.run([_soffice(), "--headless", "--norestore", "--convert-to", "pdf",
                           "--outdir", str(cache), f"-env:UserInstallation=file:///{paths.LOWTMP.as_posix()}/lo",
                           str(src)], env=env, timeout=180)
    except OSError:
        return None
    made = cache / f"{src.stem}.pdf"
    if proc.returncode != 0 or not made.is_file():
        return None
    made.replace(out)
    return out


def open_convert(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """审查交付 → 格式转换接手：**只抛第一个问题**（图），后面两件由格式助手在对话里依次问，
    这样右栏是一问一答的对话，不是一口气砸三段。"""
    body = doc_read(db, task_id, "write")["markdown"]
    slots = re.findall(r"\[图位\s*(\d+)\]", body)
    figs = fig_list(db, task_id)["files"]
    msg = (
        "审查意见我看了，开始排版。先对第一件事 —— **图**：" + chr(10) + chr(10) +
        f"正文里有 {len(slots)} 处 [图位]，项目「图」文件夹里有 {len(figs)} 张成图"
        + (f"（{'、'.join(f['name'] for f in figs[:6])}{'…' if len(figs) > 6 else ''}）" if figs else "（还是空的）")
        + "。" + chr(10) + chr(10) +
        "位置和数目对得上吗？要删或要补，直接说「第 3 处删掉」「2.2 再加一张」这种就行。"
        "（对的话回「对」，我接着问成品格式和模板。）"
    )
    for m in db.messages(task_id, "convert"):
        if (m.get("content") or "").startswith("审查意见我看了，开始排版"):
            return {"opened": False}
    db.add_message(task_id, "assistant", msg, scope="convert", sender="格式")
    return {"opened": True, "message": msg}


def convert_system(db: "WorkflowDB", task_id: str) -> str:
    """格式助手（格式转换这一步）的 system：三件事**一次只问一件**，问完全按模板编译。"""
    task = db.summary(task_id)["task"]
    figs = fig_list(db, task_id)["files"]
    tpl_lines = chr(10).join(f"- `{k}`：{v['name']} —— {v['note']}" + (f"（来源 {v['repo']}）" if v["repo"] else "")
                             for k, v in TEMPLATES.items())
    style_lines = chr(10).join(f"- `{k}`：{label}" + ("（本机没这个 bst，会退 plain）" if not _kpsewhich(bst + ".bst") else "")
                               for k, (bst, label) in CITE_STYLES.items())
    return (
        "你是「览籍智研」里「格式转换」这一步的格式助手，用中文工作，站在排版工的位置。" + chr(10) + chr(10) +
        f"项目《{task['title']}》，你只做一件事：把「论文正文/正文.md」按用户要的格式与期刊模板导成成品，"
        "放进「最终产物」。" + chr(10) + chr(10) +
        "**开场必须依次问四件事，一次只问一件，拿到回答再问下一件**（不要一口气问完，也不要自己替用户定）：" + chr(10) +
        "1. 图：正文里的 [图位] 与「图」文件夹里的成图，位置和数目对不对；" + chr(10) +
        "2. 成品：docx / doc（Word 97）/ pdf；" + chr(10) +
        "3. 范本：用下面哪个期刊模板，还是用户自己上传 .docx/.tex 范本；" + chr(10) +
        "4. 参考文献样式（pdf / docx / html / md 都按它走）：正文里写成 {{C3}} 的标记会变成正式引用，台账里的文献从 Zotero 取元数据。" +
        "下面这套是可选样式，问他要哪一种（他不挑就按模板默认：中文稿 gbt7714）：" + chr(10) + style_lines + chr(10) + chr(10) +
        "四件都定下来之后：调 `format_convert`（format=pdf/docx/doc，template=模板名，cite_style=上一步选的样式；要 .tex→PDF 用 format=tex），"
        "把产出路径和页数/大小告诉用户，别贴正文。若返回里 cites.missing 不为空（这些 C 编号在 Zotero 里找不到），把缺的编号原样告诉他，"
        "让他先导进 Zotero 再重编；cite_note / bibtex 字段有内容也要如实转述。" + chr(10) + chr(10) +
        "【可选模板】" + chr(10) + tpl_lines + chr(10) + chr(10) +
        f"【「图」文件夹现状】{len(figs)} 张：" + (chr(10) + chr(10).join(f"- {f['name']}" for f in figs[:20]) if figs else "（空）") + chr(10) + chr(10) +
        "规矩：不编数据；用户没明确说的模板不要自己拍；编译失败要把错误摘要原样告诉用户，并说清退回的是哪套模板。"
    )


# ============================ 项目文件夹（三栏页第 11 项：看 / 预览 / 交给某一步）============================
SKIP_NAMES = {"_work", "_parts", "_tex", "__pycache__", ".obsidian", ".git", ".agents", "node_modules"}
HAND_STEPS = {"idea": "创新点审查助手", "search": "文献检索助手", "download": "入库助手",
              "notes": "翻译助手", "outline": "框架助手", "map": "谱系助手",
              "write": "写作助手", "review": "审查助手", "entrans": "中译英助手", "convert": "格式助手"}


def _fsize(n: int) -> str:
    v = float(n or 0)
    for unit in ("B", "KB", "MB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB"


def tree_list(db: "WorkflowDB", task_id: str, rel: str = "") -> dict[str, Any]:
    """项目文件夹的一层清单（rel 相对项目根，'' = 根）。
    两道闸：_safe_path 先保证在白名单里，再判 is_relative_to(root) —— 只加前者的话，
    rel='../别的项目' 也能过（同级项目都在白名单里）。"""
    base = db.base_dir(task_id)
    if not base or not base.is_dir():
        return {"root": str(base or ""), "rel": "", "rows": [], "missing": True}
    root = _safe_path(str(base))
    target = _safe_path(str(root / rel)) if rel else root
    if not target.is_relative_to(root):
        raise ValueError("只能看这个项目文件夹里的东西")
    if not target.is_dir():
        raise ValueError("不是文件夹：" + rel)
    dirs: list[dict[str, Any]] = []
    files: list[tuple[float, dict[str, Any]]] = []
    for p in target.iterdir():
        if p.name.startswith((".", "~$")) or p.name in SKIP_NAMES:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        row = {"name": p.name, "rel": p.relative_to(root).as_posix(), "path": str(p),
               "dir": p.is_dir(), "ext": "" if p.is_dir() else p.suffix.lower().lstrip("."),
               "size": 0 if p.is_dir() else st.st_size, "count": 0,
               "at": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")}
        if row["dir"]:
            try:
                row["count"] = sum(1 for c in p.iterdir() if c.is_file())
            except OSError:
                pass
            dirs.append(row)
        else:
            files.append((st.st_mtime, row))
    dirs.sort(key=lambda r: r["name"])
    files.sort(key=lambda x: -x[0])                       # 文件按修改时间倒序：刚改的排前面
    return {"root": str(root), "rel": "" if target == root else target.relative_to(root).as_posix(),
            "rows": dirs + [r for _, r in files], "missing": False}


def file_hand(db: "WorkflowDB", task_id: str, rel: str, step: str, note: str = "") -> dict[str, Any]:
    """把项目里的一个文件转到某一步的对话里（scope=step）—— 那一步的助手下次开口就带着它。
    只写一条消息，不触发那一步跑模型（用户自己走到那一步时才会读到）。"""
    base = db.base_dir(task_id)
    if not base:
        raise ValueError("这个项目还没设项目位置")
    who = HAND_STEPS.get(step or "")
    if not who:
        raise ValueError("没有这一步：" + (step or ""))
    root = _safe_path(str(base))
    p = _safe_path(str(root / rel)) if rel else root
    if not p.is_relative_to(root) or not p.exists():
        raise ValueError("文件不在这项目里：" + (rel or ""))
    size = sum(c.stat().st_size for c in p.iterdir() if c.is_file()) if p.is_dir() else p.stat().st_size
    msg = f"【文件管理助手转来】{p.relative_to(root).as_posix()}（{_fsize(size)}）" + (f"\n{note}" if note else "")
    db.add_message(task_id, "user", msg, scope=step, sender="files")
    return {"ok": True, "step": step, "who": who, "name": p.name, "path": str(p), "size": size}


def files_system(db: "WorkflowDB", task_id: str) -> str:
    """文件管理助手（项目文件夹这一栏）的 system：整夹随便看，只读为主，要动手就交给对应那一步。"""
    task = db.summary(task_id)["task"]
    base = db.base_dir(task_id)
    try:
        lines = chr(10).join(
            (f"- {r['name']}/  {r['count']} 个文件" if r["dir"] else f"- {r['name']}  {_fsize(r['size'])}")
            for r in tree_list(db, task_id)["rows"][:26]) or "（空）"
    except Exception as exc:
        lines = f"（读不到目录：{exc}）"
    return (
        "你是「览籍智研」三栏页「项目文件夹」这一栏的文件管理助手，用中文工作。" + chr(10) +
        f"项目《{task['title']}》，文件夹 {base or '（这个项目还没设项目位置）'}。" + chr(10) + chr(10) +
        "用户会把文件拖进对话框、或在左边点选文件来问你。你要做的是：说清这是什么文件、里面有什么、"
        "和项目里别的文件对不对得上（比如正文和引用台账、图和图注）。" + chr(10) +
        "- 读内容用 read_attachment（自动认 md/txt/pdf/docx）；看目录/找文件用 file_list（rel 相对项目根）。" + chr(10) +
        "- 用户说「交给 X 助手」→ 调 file_hand（rel + step + note），然后一句话说清转给了谁、那一步会拿它干什么。" +
        "step 取值：" + "、".join(f"{k}={v}" for k, v in HAND_STEPS.items()) + "。" + chr(10) +
        "- 只读为主：改内容、挪位置、删除，都要用户明确说了才做，做之前先用一句话复述一遍。" + chr(10) +
        "- 不编造文件里没有的内容；找不到就说找不到，并列出你到底看了哪几个路径。" + chr(10) + chr(10) +
        "【项目根目录现状】" + chr(10) + lines
    )


def conv_preview(path: str) -> dict[str, Any]:
    """右栏展示区要什么：PDF/图片直接给 /api/file 的地址；Word 先转成 PDF 再展示。"""
    p = _safe_path(str(path))          # 只认项目 / data 目录里的文件（路径校验，防任意路径被拉去转换）
    if not p.is_file():
        raise ValueError("文件不存在")
    ext = p.suffix.lower()
    if ext in (".png", ".svg", ".jpg", ".jpeg", ".webp", ".gif"):
        return {"kind": "image", "name": p.name, "url": f"/api/file?path={quote(str(p))}&inline=1", "path": str(p)}
    if ext == ".pdf":
        return {"kind": "pdf", "name": p.name, "url": f"/api/file?path={quote(str(p))}&inline=1", "path": str(p)}
    if ext in (".docx", ".doc"):
        pdf = docx_to_pdf(p)
        if pdf:
            return {"kind": "pdf", "name": p.name, "url": f"/api/file?path={quote(str(pdf))}&inline=1",
                    "path": str(p), "converted": str(pdf)}
        return {"kind": "docx", "name": p.name, "path": str(p)}
    if ext in (".html", ".htm"):
        return {"kind": "html", "name": p.name, "url": f"/api/file?path={quote(str(p))}&inline=1", "path": str(p)}
    return {"kind": "other", "name": p.name, "path": str(p)}


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


def host_compile(tex: Path) -> dict[str, Any] | None:
    """让窗口宿主（Medium 权限）去跑 xelatex。

    沙箱（Low IL）里 dvipdfmx 写不了 MiKTeX 自己的 log/cache，实测直接崩（退出码 3221226505），
    页都排出来了 PDF 却没写成。宿主是正常 Medium 进程，本地编译交给它最省事。
    宿主没起（纯 CLI）或通道不通 → 返回 None，调用方退回沙箱编译。
    """
    import urllib.request

    addr, key = os.environ.get("RW_GRANT"), os.environ.get("RW_GRANT_KEY")
    if not (addr and key):
        return None
    try:
        req = urllib.request.Request(
            "http://%s/tex" % addr,
            data=json.dumps({"tex": str(tex)}, ensure_ascii=False).encode("utf-8"),
            headers={"X-RW-Key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=320) as r:
            got = json.loads(r.read() or b"{}")
        return got if isinstance(got, dict) else None
    except Exception:
        return None



def conv_compile(path: str) -> dict[str, Any]:
    """编译任意一份 .tex（代码区「⚙ 编译」用）：产物 PDF 放在同目录。"""
    p = _safe_path(path)
    if p.suffix.lower() != ".tex" or not p.is_file():
        raise ValueError("只能编译 .tex 文件")
    pdf = p.with_suffix(".pdf")
    done = host_compile(p)                       # 宿主能编就别在沙箱里编（dvipdfmx 在 Low IL 下会崩）
    if done and done.get("pdf"):
        return {"pdf": str(done["pdf"]), "name": done.get("name") or pdf.name, "size": done.get("size") or 0}
    if done and done.get("error"):
        raise RuntimeError(done["error"])
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
    from contextvars import copy_context
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
        futures = [pool.submit(copy_context().run, one, piece) for piece in chunks]
        parts = [future.result() for future in futures]
    missing = [str(i + 1) for i, part in enumerate(parts) if not part.strip()]
    if missing:
        raise RuntimeError("翻译分片返回空内容（第 " + "、".join(missing) + " 段），原译文保留，请重试")
    return {"text": "\n\n".join(p for p in parts if p), "chunks": len(chunks),
            "ms": int((time.time() - t0) * 1000), "style": style}


def en_state(db: "WorkflowDB", task_id: str) -> dict[str, Any]:
    """默认拿「正文.md」当源；同时把上次译好的 <名字>_en.md 读回来。"""
    src = doc_path(db, task_id, "write")
    res = src.with_name(src.stem + "_en.md")
    import service_settings
    saved = service_settings.read(db, "translation:" + task_id)
    if saved.get("result_path"):
        candidate = Path(saved["result_path"]).resolve()
        if candidate.parent == src.parent.resolve():
            res = candidate
    source = src.read_text(encoding="utf-8", errors="replace") if src.is_file() else ""
    if saved.get("custom_source"):
        source = saved.get("source_text", "")
    return {"source_path": str(src), "source_text": source,
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
    if not out["text"].strip():
        raise RuntimeError("模型没有返回译文，原译文保留，请重试")
    import asset_service
    asset_service.atomic_write_text(dest, out["text"] + "\n")
    asset_service.register_file(db, dest, project_id=task_id, kind="document", source="translation",
                                body=out["text"], metadata={"document_kind": "entrans"})
    import service_settings
    service_settings.write(db, "translation:" + task_id, {"result_path": str(dest), "source_text": md,
                           "custom_source": stem != doc_path(db, task_id, "write").stem})
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
