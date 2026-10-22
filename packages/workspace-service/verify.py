# -*- coding: utf-8 -*-
"""模型输出的机械校验：只核能被原文证伪的东西 —— 带单位的数值、页码、引号里的原文。

不判语义（"这篇到底支不支持这句话"机械判不了），不调模型，纯函数，同一输入结果恒定。

    from verify import audit, stamp
    rep = audit(md, pages={1: "第1页原文…"}, corpus="证据+笔记全文", label="x.pdf")
    md = stamp(md, rep)        # 尾部追加「机器校验」段，正文一个字不动

环境变量 RW_VERIFY：enforce（默认，落脚注）/ shadow（只算不落，用来量误报率）。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

_LOG: Path | None = None      # shadow 模式的记账文件；None = <data>/verify_shadow.jsonl

_SUP = str.maketrans("⁻¹²³⁰⁴⁵⁶⁷⁸⁹μ", "-1230456789u")
# 只认「数字 + 计量单位(+可选指数)」：年份/章节号/图号/文献号进不来（噪声率太高，会把功能做成噪音源）
_QTY = re.compile(r"(\d+(?:\.\d+)?)\s*(mAh|mA\s*h|MPa|GPa|kPa|eV|meV|S\s*cm|S/cm|℃|°C|K|nm|μm|um|Å|mA|mV|V|%|倍|h|次)"
                  r"(\s*(?:[gk]?g?\s*)?(?:[-−⁻]?\s*1|⁻¹|−1))?")
_PAGE = re.compile(r"[（(]\s*p{1,2}\.?\s*(\d+)\s*[)）]")
_QUOTE = re.compile(r"[「“]([^」”]{8,})[」”]")
_STAMP = re.compile(r"\n-{3}\n+## 机器校验（自动[^\n]*\n.*$", re.S)

# 顺序敏感：mah 先于 ma，kpa 先于 k，gpa/mpa 同族
_FAMILY = (("mah", "cap"), ("mpa", "stress"), ("gpa", "stress"), ("kpa", "stress"),
           ("ev", "energy"), ("mev", "energy"), ("scm", "cond"), ("s/cm", "cond"),
           ("°c", "temp"), ("k", "temp"), ("nm", "len"), ("um", "len"), ("å", "len"),
           ("ma", "cur"), ("mv", "volt"), ("v", "volt"), ("%", "ratio"), ("倍", "ratio"),
           ("h", "time"), ("次", "count"))


def enabled() -> bool:
    return (os.environ.get("RW_VERIFY") or "enforce").strip().lower() != "shadow"


def _norm(s: str) -> str:
    s = (s or "").replace("℃", "°C")          # ℃ 与 °C 是同一个单位，两种写法必须归一
    return re.sub(r"[\s\u00a0]+", "", s.translate(_SUP)).replace("−", "-").replace("–", "-").lower()


def _family(unit: str) -> str:
    u = _norm(unit)
    for key, fam in _FAMILY:
        if u.startswith(key):
            return fam
    return u


def quantities(text: str) -> list[tuple[float, str, str]]:
    """[(数值, 单位族, 原始写法)]"""
    return [(float(m.group(1)), _family(m.group(2)), m.group(0).strip()) for m in _QTY.finditer(text or "")]


def has_qty(corpus_qs: list[tuple[float, str, str]], value: float, fam: str) -> bool:
    for v, f, _ in corpus_qs:
        if f == fam and abs(v - value) <= max(0.005 * abs(value), 1e-3):
            return True
    return False


def qty_in_page(raw: str, page_text: str) -> bool:
    """一个「值+单位」的原始写法，是不是真的出现在这段原文里（写法差异已归一）。

    三层：① 归一后原样命中 → ② 同单位族同值（吃掉 0.120/mAh/g 与 0.12 mAh g⁻¹ 的写法差）
    → ③ 计数/时长这类中文量词（次/h）对不上西文（cycles），退回「裸数字在页上」。
    # ponytail: ③ 只保证「这个数在页上出现过」，不保证是同一个量；要严格就做中英量词映射表。
    """
    if not _norm(raw):
        return False
    if _norm(raw) in _norm(page_text):
        return True
    qs = quantities(raw)
    if len(qs) != 1:
        return False
    value, fam, _ = qs[0]
    if has_qty(quantities(page_text), value, fam):
        return True
    if fam in ("count", "time"):
        num = "%g" % value
        return re.search(r"(?<![\d.])" + re.escape(num) + r"(?![\d.])", _norm(page_text)) is not None
    return False


def check_pages(md: str, pages: dict[int, str]) -> dict:
    """每个 (p.N) 主张：行里的带单位数值或引号原文必须出现在第 N 页正文里。"""
    claims = ok = skip = 0
    bad: list[dict] = []
    for ln in (md or "").splitlines():
        m = _PAGE.search(ln)
        if not m:
            continue
        claims += 1
        no = int(m.group(1))
        head = ln[:m.start()]
        if no not in pages:
            bad.append({"kind": "page", "line": head.strip()[:80], "page": no,
                        "why": f"这篇只有 {len(pages)} 页"})
            continue
        qs = quantities(head)
        quotes = [q for q in (x.group(1) for x in _QUOTE.finditer(ln)) if q]
        if not qs and not quotes:              # 纯定性的一句，机械判不了
            skip += 1
            continue
        page = pages[no]
        good = any(qty_in_page(raw, page) for _, _, raw in qs) or any(qty_in_page(q, page) for q in quotes)
        if good:
            ok += 1
        else:
            what = qs[0][2] if qs else f"「{quotes[0][:30]}」"
            bad.append({"kind": "page", "line": head.strip()[:80], "page": no, "value": what,
                        "why": f"第 {no} 页没有 {what}"})
    return {"claims": claims, "ok": ok, "skip": skip, "bad": bad}


def check_quantities(text: str, corpus: str) -> list[dict]:
    """全文每个带单位的数值都要在素材里找到同族同值。"""
    cq = quantities(corpus)
    return [{"kind": "qty", "value": raw, "why": f"素材里找不到 {raw}"}
            for v, fam, raw in quantities(text) if not has_qty(cq, v, fam)]


def _record(rep: dict) -> None:
    """shadow 模式把结果落一行 JSONL —— 不落脚注但要有账，不然"只记录"是空话。
    enforce 模式不写：那时结论已经在产物里了。记账失败绝不打断主流程。"""
    if enabled():
        return
    try:
        import json
        out = _LOG
        if out is None:
            import paths
            out = Path(getattr(paths, "DATA", Path.home() / ".lanjizhiyan")) / "verify_shadow.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rep}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def audit(md: str, pages: dict[int, str] | None = None, corpus: str | None = None, label: str = "") -> dict:
    """corpus=None 表示「这次不核数值」；corpus="" 表示「素材是空的」——那每个数字都算没出处。"""
    rep: dict = {"label": label, "page_claims": 0, "page_ok": 0, "page_skip": 0, "bad": []}
    if pages:
        r = check_pages(md, pages)
        rep.update(page_claims=r["claims"], page_ok=r["ok"], page_skip=r["skip"])
        rep["bad"] += r["bad"]
    if corpus is not None:
        rep["bad"] += check_quantities(md, corpus)
    rep["warn"] = len(rep["bad"])
    _record(rep)
    return rep


def stamp(md: str, rep: dict) -> str:
    """尾部追加校验段。幂等：重复调用不会叠加；RW_VERIFY=shadow 时原样返回。"""
    body = _STAMP.sub("", md or "").rstrip()
    if not enabled() or (not rep.get("page_claims") and not rep.get("bad")):
        return body + ("\n" if enabled() else "")
    lines = ["", "", "---", "", f"## 机器校验（自动 · {time.strftime('%m-%d %H:%M')}）", ""]
    if rep.get("page_claims"):
        unver = rep["page_claims"] - rep["page_ok"] - rep.get("page_skip", 0)
        lines.append(f"- 页内主张 {rep['page_claims']} 条：通过 {rep['page_ok']}，未核 {unver}，"
                     f"纯定性无法机械判断 {rep.get('page_skip', 0)}")
    if rep.get("bad"):
        lines.append(f"- 待核 {rep['warn']} 处（素材里没找到出处）：")
        lines += [f"  - {b.get('value', '')} {b['why']}　←　{b.get('line', '')[:60]}"
                  for b in rep["bad"][:40]]
    else:
        lines.append("- 所有带单位的数值都在素材里找到了出处。")
    lines += ["", "> 只核了「带单位的数值、页码、引号原文」能否在素材里找到，不判观点对错。"]
    return body + "\n" + "\n".join(lines) + "\n"
