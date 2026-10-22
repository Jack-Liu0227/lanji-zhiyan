# -*- coding: utf-8 -*-
"""输出校验闸门的对抗集：20 条注入伪造 + 20 条真值，量召回与误报。
判据（方案里的「9 分」）：伪造召回 ≥ 19/20，真值误报 ≤ 1/20。
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "workspace-service"))
_ROOT = Path(__file__).resolve().parents[1]
for _n in ("database", "workspace-core", "workspace-service", "connectors", "ai-runtime"):
    sys.path.insert(0, str(_ROOT / "packages" / _n))

import verify  # noqa: E402

PAGES = {
    1: "The Cu-doped sample delivers an initial capacity of 110 mAh g−1 at 0.1 C "
       "and retains 85 % of it after 50 cycles.",
    2: "The band gap is 3.10 eV and the particle size is 500 nm. "
       "Conductivity reached 0.8 S cm−1 after sintering.",
    3: "Sintering was carried out at 700 °C for 12 h, and the cell was cycled to 4.2 V.",
}

FABRICATED = [
    "初始容量 120 mAh g-1（p.1）",
    "循环后保持率 95%（p.1）",
    "稳定循环 80 次（p.1）",
    "充电到 4.5 V（p.1）",
    "烧结温度 700 ℃（p.1）",
    "带隙 0.94 eV（p.2）",
    "粒径 200 nm（p.2）",
    "电导率 1.2 S cm-1（p.2）",
    "初始容量 110 mAh g-1（p.2）",
    "保持率 85%（p.3）",
    "带隙 3.10 eV（p.3）",
    "保温 24 h（p.3）",
    "粒径 500 nm（p.3）",
    "容量 110 mAh g-1（p.7）",
    "循环 50 次（p.9）",
    "「the band gap is 2.00 eV and isotropic」这个结论（p.1）",
    "「conductivity reached 5.0 S cm−1」原文说的（p.2）",
    "电导率 1.5 S cm-1（p.2）",
    "烧结 900 ℃、保温 6 h（p.3）",
    "截止电压 3.0 V（p.3）",
]

GENUINE = [
    "初始容量 110 mAh g-1（p.1）",
    "保持率 85%（p.1）",
    "循环 50 次（p.1）",
    "倍率 0.1 C 下测得（p.1）",
    "带隙 3.10 eV（p.2）",
    "粒径 500 nm（p.2）",
    "电导率 0.8 S cm-1（p.2）",
    "烧结后电导率 0.8 S/cm（p.2）",
    "烧结温度 700 ℃（p.3）",
    "保温 12 h（p.3）",
    "截止电压 4.2 V（p.3）",
    "「initial capacity of 110 mAh g」原文照抄（p.1）",
    "「Conductivity reached 0.8 S cm」原文照抄（p.2）",
    "「Sintering was carried out at 700」原文照抄（p.3）",
    "该体系在此条件下表现出较好的结构稳定性（p.1）",       # 纯定性：不该被标
    "掺杂后晶格收缩趋势与文献一致（p.2）",                    # 纯定性：不该被标
    "样品在长循环中容量衰减平缓（p.3）",                      # 纯定性：不该被标
    "容量保持 85 %（p.1）",
    "比容量约 110 mAh g−1（p.1）",
    "电导率 0.8 S cm⁻¹（p.2）",
]


def flagged(line: str) -> bool:
    return bool(verify.check_pages(line, PAGES)["bad"])


class AdversarialGate(unittest.TestCase):
    def test_recall_on_fabrications(self):
        caught = [x for x in FABRICATED if flagged(x)]
        recall = len(caught) / len(FABRICATED)
        misses = [x for x in FABRICATED if x not in caught]
        self.assertGreaterEqual(recall, 0.95, f"漏抓 {misses}")

    def test_false_positive_on_genuine(self):
        wrong = [x for x in GENUINE if flagged(x)]
        self.assertLessEqual(len(wrong) / len(GENUINE), 0.05, f"误报 {wrong}")

    def test_qualitative_lines_are_skipped_not_flagged(self):
        rep = verify.check_pages("\n".join(GENUINE), PAGES)
        self.assertGreater(rep["skip"], 0)

    def test_unit_spelling_variants_match(self):
        for a, b in (("0.8 S cm-1", "0.8 S cm⁻¹"), ("700 ℃", "700 °C"), ("120 mAh g-1", "120 mAh g⁻¹")):
            self.assertTrue(verify.qty_in_page(a, f"value {b} here"), f"{a} 应命中 {b}")

    def test_value_tolerance(self):
        self.assertTrue(verify.qty_in_page("0.120 mAh/g", "0.12 mAh g−1"))

    def test_noise_numbers_ignored(self):
        # 年份/图号/章节号不是硬数字，不进校验
        self.assertEqual(verify.quantities("2024 年图3 的 2.1 节"), [])

    def test_quantity_gate(self):
        corpus = "capacity 110 mAh g−1; 85 % retention"
        self.assertEqual(verify.check_quantities("容量 110 mAh g-1，保持 85%", corpus), [])
        self.assertEqual([x["value"] for x in verify.check_quantities("容量 130 mAh g-1", corpus)],
                         ["130 mAh g-1"])


class StampBehavior(unittest.TestCase):
    def test_stamp_appends_and_keeps_body(self):
        md = "结论：容量 130 mAh g-1（p.1）"
        rep = verify.audit(md, pages=PAGES, corpus="110 mAh g−1")
        out = verify.stamp(md, rep)
        self.assertTrue(out.startswith(md))
        self.assertIn("## 机器校验", out)
        self.assertIn("130 mAh g-1", out)
        self.assertGreater(rep["warn"], 0)

    def test_stamp_is_idempotent(self):
        md = "容量 110 mAh g-1（p.1）"
        rep = verify.audit(md, pages=PAGES)
        once = verify.stamp(md, rep)
        twice = verify.stamp(once, rep)
        self.assertEqual(once.count("## 机器校验"), 1)
        self.assertEqual(twice.count("## 机器校验"), 1)

    def test_shadow_mode_writes_nothing_to_body_but_records_a_line(self):
        import json
        import tempfile
        old_mode, old_log = os.environ.get("RW_VERIFY"), verify._LOG
        os.environ["RW_VERIFY"] = "shadow"
        verify._LOG = Path(tempfile.mkdtemp()) / "verify_shadow.jsonl"
        try:
            md = "容量 130 mAh g-1（p.1）"
            self.assertEqual(verify.stamp(md, verify.audit(md, pages=PAGES)).strip(), md.strip())
            lines = verify._LOG.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1, "shadow 必须留下账，否则「只记录」是空话")
            self.assertEqual(json.loads(lines[0])["warn"], 1)
        finally:
            verify._LOG = old_log
            if old_mode is None:
                os.environ.pop("RW_VERIFY", None)
            else:
                os.environ["RW_VERIFY"] = old_mode

    def test_enforce_mode_does_not_write_log(self):
        import tempfile
        old_mode, old_log = os.environ.get("RW_VERIFY"), verify._LOG
        os.environ.pop("RW_VERIFY", None)
        verify._LOG = Path(tempfile.mkdtemp()) / "verify_shadow.jsonl"
        try:
            verify.audit("容量 130 mAh g-1（p.1）", pages=PAGES)
            self.assertFalse(verify._LOG.exists(), "enforce 模式结论已在产物里，不该再记账")
        finally:
            verify._LOG = old_log
            if old_mode is not None:
                os.environ["RW_VERIFY"] = old_mode


class DocWriteGate(unittest.TestCase):
    """H3 端到端：正文落盘过校验闸门。三个调用方（workflow.py / workflow_runners / server）都经过 doc_write。"""

    def test_doc_write_stamps_and_is_idempotent(self):
        import tempfile
        import workflow

        db = workflow.WorkflowDB(Path(tempfile.mkdtemp()) / "wb.sqlite3")
        try:
            task = db.create_task("O3 掺杂", "哪种掺杂元素值得验证", "NaNi1/3Fe1/3Mn1/3O2")
            md = "## 结果\n\nCu 掺杂后带隙 0.94 eV，容量 120 mAh g-1。"

            first = workflow.doc_write(db, task, "write", md)["markdown"]
            self.assertIn("## 机器校验", first)
            self.assertTrue(first.startswith(md), "正文必须逐字保留")
            self.assertIn("0.94 eV", first.split("## 机器校验")[1])
            self.assertEqual(workflow.doc_write(db, task, "write", md)["markdown"].count("## 机器校验"), 1)

            # 素材里出现这个数之后，它就不再是「待核」
            db.add_note(task, "精读笔记", "原文：band gap 0.94 eV measured by UV-vis.")
            third = workflow.doc_write(db, task, "write", md)["markdown"]
            self.assertNotIn("素材里找不到 0.94 eV", third)
            self.assertIn("120 mAh g-1", third.split("## 机器校验")[1])   # 没素材的仍然标着
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
