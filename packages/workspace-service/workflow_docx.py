# -*- coding: utf-8 -*-
"""Markdown → Word（中文学位论文/学术期刊稿的权威出版级排版规范）。

排版标准与设计原则：
  - A4 纸张、页边距：上下 2.54cm，左 3.0cm（装订边），右 2.5cm
  - 正文：中文 宋体 + 英文 Times New Roman，小四(12pt)，1.5 倍行距，首行缩进 2 字符，两端对齐
  - 论文题目：黑体 三号(16pt) 加粗 居中
  - 一级标题 (##)：黑体 三号(16pt) 居中，段前 12pt，段后 12pt
  - 二级标题 (###)：黑体 四号(14pt) 左顶格，段前 6pt，段后 6pt
  - 三级标题 (####)：黑体 小四(12pt) 左顶格，段前 6pt，段后 0pt
  - 列表：无序列表 (List Bullet) 与有序列表 (List Number)，消除首行缩进，紧凑间距
  - 引用块 (> Quote)：小四(10.5pt) 楷体/Times New Roman，段前段后 4pt，左侧缩进 1cm
  - 代码块 (```)：Consolas 9.5pt，背景灰度浅底，单倍行距，边距缩进 0.8cm
  - 数学公式 ($...$, $$...$$)：Cambria Math 字体，行间公式居中排版
  - 表格：学术规范三线表（顶底 1.5pt，表头 0.75pt），五号字，列宽按内容自适应，居中对齐
  - 参考文献：五号(10.5pt)，悬挂缩进 0.8cm，单倍行距
  - 页眉页脚：居中页码域
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BLACK = RGBColor(0, 0, 0)
GRAY_TEXT = RGBColor(75, 85, 99)
CODE_TEXT = RGBColor(31, 41, 55)
SONG, HEI, KAI, LATIN = "宋体", "黑体", "楷体", "Times New Roman"
MATH_FONT = "Cambria Math"
CODE_FONT = "Consolas"
BODY_PT, SMALL_PT, TINY_PT = 12.0, 10.5, 9.5
MAX_IMG_CM = 15.0


def _rfonts(el, ea: str, latin: str = LATIN) -> None:
    """把 rFonts 的 ascii/hAnsi/eastAsia 全部钉死，并清掉主题字体。"""
    rPr = el.get_or_add_rPr()
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = rPr.makeelement(qn("w:rFonts"), {})
        rPr.insert(0, rf)
    for k in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        rf.attrib.pop(qn("w:" + k), None)
    rf.set(qn("w:ascii"), latin)
    rf.set(qn("w:hAnsi"), latin)
    rf.set(qn("w:eastAsia"), ea)


def _first_line(p, chars: int = 2, size: float = BODY_PT) -> None:
    """正文段落首行缩进 N 字符。"""
    ind = p._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), str(chars * 100))
    ind.set(qn("w:firstLine"), str(int(chars * size * 20)))


def _no_indent(p) -> None:
    """段落级取消首行缩进。"""
    p.paragraph_format.first_line_indent = Cm(0)
    ind = p._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), "0")
    ind.set(qn("w:firstLine"), "0")


def _hanging_indent(p, hanging_cm: float = 0.8) -> None:
    """参考文献悬挂缩进。"""
    p.paragraph_format.first_line_indent = -Cm(hanging_cm)
    p.paragraph_format.left_indent = Cm(hanging_cm)


def _set_shading(p, color_hex: str = "F3F4F6") -> None:
    """设置段落底纹背景色（代码块）。"""
    pPr = p._p.get_or_add_pPr()
    shd = pPr.find(qn("w:shd"))
    if shd is None:
        shd = pPr.makeelement(qn("w:shd"), {})
        pPr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)


def _style(doc, name: str, ea: str, size: float, *, bold: bool = False, align=None,
           first_line_chars: int = 0, before: float = 0, after: float = 0, line: float = 1.5) -> None:
    st = doc.styles[name]
    st.font.size, st.font.bold, st.font.italic = Pt(size), bold, False
    st.font.color.rgb = BLACK
    _rfonts(st.element, ea)
    pf = st.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    pf.line_spacing = line
    if align is not None:
        pf.alignment = align
    pPr = st.element.get_or_add_pPr()
    ind = pPr.get_or_add_ind()
    for k in ("firstLine", "firstLineChars", "hanging", "hangingChars"):
        ind.attrib.pop(qn("w:" + k), None)
    ind.set(qn("w:firstLineChars"), str(first_line_chars * 100))
    ind.set(qn("w:firstLine"), str(int(first_line_chars * size * 20)))


def _setup(doc) -> None:
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.left_margin, sec.right_margin = Cm(3.0), Cm(2.5)
    J = WD_ALIGN_PARAGRAPH.JUSTIFY
    C = WD_ALIGN_PARAGRAPH.CENTER
    _style(doc, "Normal", SONG, BODY_PT, align=J)
    _style(doc, "Title", HEI, 16, bold=True, align=C, after=18)
    _style(doc, "Heading 1", HEI, 16, bold=False, align=C, before=14, after=12)
    _style(doc, "Heading 2", HEI, 14, bold=False, before=8, after=6)
    _style(doc, "Heading 3", HEI, 12, bold=False, before=6, after=0)
    _style(doc, "Heading 4", HEI, 12, bold=True, before=6, after=0)
    for nm in ("List Bullet", "List Number"):
        _style(doc, nm, SONG, BODY_PT, after=2, line=1.35)
    _style(doc, "Footer", SONG, SMALL_PT, align=C)
    # 页码居中
    p = sec.footer.paragraphs[0]
    p.alignment = C
    _no_indent(p)
    fld = p._p.makeelement(qn("w:fldSimple"), {qn("w:instr"): " PAGE "})
    run = p.add_run()
    run.font.size = Pt(SMALL_PT)
    _rfonts(run._r, SONG)
    fld.append(run._r)
    p._p.append(fld)


def _inline(p, text: str, default_ea: str = SONG, default_latin: str = LATIN,
            default_size: float = BODY_PT, default_color: RGBColor = BLACK) -> None:
    """高精度多标记行内排版：支持 **加粗**、*斜体*、`代码`、$行内公式$、[链接] 等。"""
    token_pattern = re.compile(
        r"(\*\*.+?\*\*|\*[^*]+?\*|`[^`]+?`|\$[^$]+?\$|\[.+?\]\(.+?\))"
    )
    tokens = token_pattern.split(text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) >= 4:
            content = tok[2:-2]
            r = p.add_run(content)
            r.bold = True
            r.font.size = Pt(default_size)
            r.font.color.rgb = default_color
            _rfonts(r._r, default_ea, default_latin)
        elif tok.startswith("*") and tok.endswith("*") and len(tok) >= 2:
            content = tok[1:-1]
            r = p.add_run(content)
            r.italic = True
            r.font.size = Pt(default_size)
            r.font.color.rgb = default_color
            _rfonts(r._r, default_ea, default_latin)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) >= 2:
            content = tok[1:-1]
            r = p.add_run(content)
            r.font.size = Pt(TINY_PT)
            r.font.color.rgb = CODE_TEXT
            _rfonts(r._r, CODE_FONT, CODE_FONT)
        elif tok.startswith("$") and tok.endswith("$") and len(tok) >= 2:
            content = tok[1:-1]
            r = p.add_run(content)
            r.italic = True
            r.font.size = Pt(default_size)
            r.font.color.rgb = default_color
            _rfonts(r._r, MATH_FONT, MATH_FONT)
        elif tok.startswith("[") and "]" in tok and "(" in tok and tok.endswith(")"):
            m = re.match(r"^\[(.*?)\]\((.*?)\)$", tok)
            link_text = m.group(1) if m else tok
            r = p.add_run(link_text)
            r.font.size = Pt(default_size)
            r.font.color.rgb = default_color
            _rfonts(r._r, default_ea, default_latin)
        else:
            r = p.add_run(tok)
            r.font.size = Pt(default_size)
            r.font.color.rgb = default_color
            _rfonts(r._r, default_ea, default_latin)


def _three_line(table) -> None:
    """标准三线表（顶底 1.5pt，表头底 0.75pt）。"""
    borders = table._tbl.tblPr.makeelement(qn("w:tblBorders"), {})
    for edge, sz in (("top", 12), ("left", 0), ("bottom", 12), ("right", 0),
                     ("insideH", 0), ("insideV", 0)):
        e = borders.makeelement(qn("w:" + edge), {})
        if sz:
            e.set(qn("w:val"), "single")
            e.set(qn("w:sz"), str(sz))
            e.set(qn("w:color"), "000000")
        else:
            e.set(qn("w:val"), "none")
            e.set(qn("w:sz"), "0")
        borders.append(e)
    table._tbl.tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription")
    for cell in table.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        tcB = tcPr.makeelement(qn("w:tcBorders"), {})
        tcB.append(tcB.makeelement(qn("w:bottom"), {qn("w:val"): "single", qn("w:sz"): "6", qn("w:color"): "000000"}))
        tcPr.insert_element_before(tcB, "w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def build(markdown: str, dest: Path | str, bases: Sequence[Path | str] = ()) -> Path:
    """markdown → dest(.docx)。全面符合中文学术论文排版规范。"""
    doc = Document()
    _setup(doc)
    lines = (markdown or "").splitlines()
    seen_title = False
    in_references = False
    i = 0

    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if not stripped:
            continue

        # 1. 标题 (H1 - H6)
        m_head = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m_head:
            lvl, txt = len(m_head.group(1)), m_head.group(2).strip()
            if "参考文献" in txt or "References" in txt:
                in_references = True
            elif lvl <= 2:
                in_references = False

            if lvl == 1 and not seen_title:
                seen_title = True
                p = doc.add_paragraph(txt, style="Title")
            else:
                p = doc.add_paragraph(txt, style=f"Heading {min(max(lvl - 1, 1), 4)}")
            continue

        # 2. 代码块 (```)
        if stripped.startswith("```"):
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip().startswith("```"):
                i += 1  # 跳过闭合 ```

            for cl in code_lines:
                cp = doc.add_paragraph()
                _no_indent(cp)
                _set_shading(cp, "F4F6F8")
                cp.paragraph_format.left_indent = Cm(0.8)
                cp.paragraph_format.line_spacing = 1.15
                cp.paragraph_format.space_before = Pt(1)
                cp.paragraph_format.space_after = Pt(1)
                r = cp.add_run(cl if cl else " ")
                r.font.size = Pt(TINY_PT)
                r.font.color.rgb = CODE_TEXT
                _rfonts(r._r, CODE_FONT, CODE_FONT)
            continue

        # 3. 块级公式 ($$ ... $$)
        if stripped.startswith("$$"):
            math_lines = []
            if stripped == "$$":
                while i < len(lines) and not lines[i].strip().startswith("$$"):
                    math_lines.append(lines[i].strip())
                    i += 1
                if i < len(lines):
                    i += 1
            else:
                m_single = re.match(r"^\$\$(.+?)\$\$$", stripped)
                if m_single:
                    math_lines.append(m_single.group(1).strip())
                else:
                    math_lines.append(stripped[2:].strip())
                    while i < len(lines) and not lines[i].strip().endswith("$$"):
                        math_lines.append(lines[i].strip())
                        i += 1
                    if i < len(lines):
                        math_lines.append(lines[i].strip()[:-2].strip())
                        i += 1

            formula = " ".join(filter(None, math_lines))
            mp = doc.add_paragraph()
            _no_indent(mp)
            mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            mp.paragraph_format.space_before = Pt(6)
            mp.paragraph_format.space_after = Pt(6)
            r = mp.add_run(formula)
            r.font.size = Pt(11.0)
            r.font.italic = True
            _rfonts(r._r, MATH_FONT, MATH_FONT)
            continue

        # 4. 表格 (| ... |)
        if stripped.startswith("|") and i < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i].strip()):
            head = _cells(stripped)
            i += 1  # 跳过分隔行
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_cells(lines[i]))
                i += 1
            t = doc.add_table(rows=1 + len(body), cols=len(head))
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for r, row in ((t.rows[0], head), *((t.rows[n + 1], b) for n, b in enumerate(body))):
                for c, txt in enumerate(row[:len(head)]):
                    cell = r.cells[c]
                    cell.text = ""
                    par = cell.paragraphs[0]
                    par.style = doc.styles["Normal"]
                    _no_indent(par)
                    par.paragraph_format.line_spacing = 1.05
                    par.paragraph_format.space_before = Pt(2)
                    par.paragraph_format.space_after = Pt(2)
                    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    _inline(par, txt, default_ea=SONG, default_size=SMALL_PT)
            _three_line(t)
            _no_indent(doc.add_paragraph("", style="Normal"))
            continue

        # 5. 图片 (![cap](path))
        m_img = re.match(r"^!\[(.*?)\]\((.+?)\)\s*$", stripped)
        if m_img:
            cap, raw = m_img.group(1), m_img.group(2).strip("<>")
            path = next((q for q in ((Path(b) / raw if b else Path(raw)) for b in (bases or ("",))) if q.is_file()), None)
            if path:
                doc.add_picture(str(path))
                pic = doc.paragraphs[-1]
                if pic.runs and pic.runs[0].width and pic.runs[0].width > Cm(MAX_IMG_CM):
                    pic.runs[0].width, pic.runs[0].height = Cm(MAX_IMG_CM), None
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                _no_indent(doc.paragraphs[-1])
                if cap:
                    p = doc.add_paragraph(cap, style="Normal")
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    _no_indent(p)
                    p.paragraph_format.space_before = Pt(3)
                    p.paragraph_format.space_after = Pt(12)
                    for r in p.runs:
                        r.font.size = Pt(SMALL_PT)
                        _rfonts(r._r, SONG)
                continue

        # 6. 引用块 (> quote)
        if stripped.startswith(">"):
            q_text = re.sub(r"^>\s*", "", stripped)
            qp = doc.add_paragraph()
            _no_indent(qp)
            qp.paragraph_format.left_indent = Cm(1.0)
            qp.paragraph_format.line_spacing = 1.3
            qp.paragraph_format.space_before = Pt(3)
            qp.paragraph_format.space_after = Pt(3)
            _inline(qp, q_text, default_ea=KAI, default_size=SMALL_PT, default_color=GRAY_TEXT)
            continue

        # 7. 无序列表 (- / *)
        if stripped.startswith(("- ", "* ")):
            p = doc.add_paragraph(style="List Bullet")
            _no_indent(p)
            _inline(p, stripped[2:])
            continue

        # 8. 有序列表 (1. / 2.)
        m_num = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m_num:
            num_idx, num_txt = m_num.group(1), m_num.group(2)
            p = doc.add_paragraph(style="List Number")
            _no_indent(p)
            _inline(p, f"{num_idx}. {num_txt}")
            continue

        # 9. 参考文献条目（在参考文献章节下）
        if in_references and (stripped.startswith("[") or re.match(r"^\d+\.", stripped)):
            p = doc.add_paragraph(style="Normal")
            _hanging_indent(p, 0.8)
            p.paragraph_format.line_spacing = 1.25
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            _inline(p, stripped, default_ea=SONG, default_size=SMALL_PT)
            continue

        # 10. 摘要/关键词特别排版
        if re.match(r"^(\*\*摘要\*\*|【摘要】|摘要：|Abstract:)", stripped):
            p = doc.add_paragraph(style="Normal")
            _no_indent(p)
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.right_indent = Cm(0.6)
            p.paragraph_format.line_spacing = 1.35
            _inline(p, stripped, default_ea=SONG, default_size=SMALL_PT)
            continue

        # 11. 普通正文段落
        p = doc.add_paragraph(style="Normal")
        _first_line(p)
        _inline(p, stripped)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest


if __name__ == "__main__":
    import sys, tempfile
    md = ("# 题目 β-Ga₂O₃ 功率器件研究\n\n**摘要**：本文研究了超宽禁带半导体材料特性。\n\n"
          "## 1. 引言\n\n首段正文，用于验证缩进与公式 $E_g = 4.9\\text{ eV}$。\n\n"
          "> 这是博导审稿建议：注意反向证伪。\n\n"
          "1. 第一项指标\n2. 第二项指标\n\n"
          "- 要点一\n- 要点二\n\n```python\nprint('code sample')\n```\n\n"
          "$$\\sigma = \\frac{1}{\\rho}$$\n\n"
          "| 样品 | 暗电流 |\n|---|---|\n| A | 1e-9 |\n| B | 3e-9 |\n\n"
          "## 参考文献\n\n[1] Oikawa, Y., & Deffrennes, G. (2026). aLLoyM. npj Computational Materials.\n")
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "docx_check.docx")
    build(md, out)
    d = Document(str(out))
    s = d.sections[0]
    assert round(s.page_width.cm) == 21 and round(s.page_height.cm) == 30, "不是 A4"
    n = d.styles["Normal"]
    assert n.font.size == Pt(BODY_PT) and n.paragraph_format.line_spacing == 1.5
    body = next(x for x in d.paragraphs if "首段正文" in x.text)
    assert body._p.pPr.ind.get(qn("w:firstLineChars")) == "200", "首行缩进不是 2 字符"
    assert d.tables and len(d.tables[0].rows) == 3, "表格没转出来"
    print("workflow_docx self-check passed →", out)
