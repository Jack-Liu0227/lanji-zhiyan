"""引用台账 → 家格式 .bib：字段清理、页码、Zotero 缺记录如实回报。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow
import workflow_converters as wc

ZOTERO_BIB = """@article{oikawa_alloym_2026,
	title = {{aLLoyM}: a large language model for alloy phase diagram prediction},
	volume = {12},
	issn = {2057-3960},
	shorttitle = {{aLLoyM}},
	url = {https://www.nature.com/articles/s41524-026-01966-6},
	doi = {10.1038/s41524-026-01966-6},
	language = {en},
	number = {1},
	urldate = {2026-10-15},
	journal = {npj Computational Materials},
	author = {Oikawa, Yuna and Deffrennes, Guillaume},
	month = jan,
	year = {2026},
	pages = {97-108},
	file = {PDF:C\\:\\\\Users\\\\x\\\\Zotero\\\\storage\\\\HTI93N2M\\\\a.pdf:application/pdf},
}"""


class CiteBibTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cite-bib-")
        self.addCleanup(temporary.cleanup)
        self.db = workflow.WorkflowDB(Path(temporary.name) / "app.sqlite3")
        self.addCleanup(self.db.close)
        self.task = self.db.create_task("Cu 掺杂论文")

    def test_bib_house_keeps_core_fields_only(self):
        out = wc.bib_house(ZOTERO_BIB)
        self.assertTrue(out.startswith("@article{oikawa_alloym_2026,"))
        for kept in ("author", "title", "journal", "volume", "number", "pages", "year", "doi"):
            self.assertIn(kept, out)
        for dropped in ("issn", "url", "urldate", "month", "language", "shorttitle", "file"):
            self.assertNotRegex(out, rf"(?m)^\s*{dropped}\s*=")
        self.assertIn("pages   = {97--108}", out)
        self.assertIn("journal = {npj Computational Materials}", out)

    def test_cite_bib_writes_ledger_and_reports_missing(self):
        wc.cite_add(self.db, self.task, {"section": "2.2", "title": "aLLoyM", "doi": "10.1038/s41524-026-01966-6"})
        wc.cite_add(self.db, self.task, {"section": "3", "title": "查不到的文献", "doi": "10.9999/nope"})

        def fake_bibtex_for(key="", doi="", title="", base=None):
            if doi.startswith("10.1038/"):
                return {"found": True, "key": "I49VPU48", "doi": doi, "bibtex": ZOTERO_BIB}
            return {"found": False, "error": "Zotero 里没有这篇"}

        with patch("zotero.bibtex_for", side_effect=fake_bibtex_for):
            out = workflow._tool_impl("cite_bib", self.db, self.task, {})

        path = Path(out["path"])
        self.assertEqual(path.name, "引用台账.bib")
        self.assertTrue(path.is_file(), "台账 .bib 没落盘")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("@article"), 1)
        self.assertNotIn("nope", text)
        self.assertEqual((out["count"], out["ledger"], len(out["missing"])), (1, 2, 1))
        self.assertIn("Zotero 里没有这篇", out["missing"][0]["reason"])

    def test_dedup_same_doi_twice(self):
        for _ in range(2):
            wc.cite_add(self.db, self.task, {"section": "1", "title": "aLLoyM", "doi": "10.1038/s41524-026-01966-6"})
        with patch("zotero.bibtex_for", return_value={"found": True, "key": "K", "doi": "10.1038/s41524-026-01966-6",
                                                      "bibtex": ZOTERO_BIB}):
            out = workflow._tool_impl("cite_bib", self.db, self.task, {})
        self.assertEqual(out["count"], 1)


if __name__ == "__main__":
    unittest.main()
