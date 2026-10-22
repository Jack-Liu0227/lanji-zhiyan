"""检索结果的排序 / 去重 / 条数上限（离线自检，不联网）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "workspace-service"))
sys.path.insert(0, str(ROOT / "packages" / "workspace-core"))

from workflow_search import MAX_RESULTS, rank  # noqa: E402

Q = "sodium ion battery cathode doping"


def _item(title, abstract="", cites=0, doi="", source="openalex", year="2024", pdf=""):
    return {"title": title, "abstract": abstract, "citations": cites, "doi": doi,
            "source": source, "year": year, "pdf_url": pdf}


class RankTest(unittest.TestCase):
    def test_quality_orders_above_relevance_tie(self):
        hot = _item("Sodium ion battery cathode doping design", "sodium ion battery cathode doping", 500)
        cold = _item("Sodium ion battery cathode doping effect", "sodium ion battery cathode doping", 0)
        out = rank(Q, [cold, hot])
        self.assertEqual(out[0]["title"], hot["title"])
        self.assertGreater(out[0]["score"], out[1]["score"])

    def test_relevance_still_matters(self):
        off = _item("Soil mechanics of clay", "unrelated abstract about soil", 5000)
        on = _item("Sodium ion battery cathode doping", "sodium ion battery cathode doping mechanism", 1)
        self.assertEqual(rank(Q, [off, on])[0]["title"], on["title"])

    def test_dedupe_by_doi_keeps_best_and_inherits_pdf(self):
        a = _item("Sodium ion battery cathode doping A", "sodium ion battery cathode doping", 10, doi="10.1/x")
        b = _item("Sodium ion battery cathode doping A (crossref)", "sodium ion battery cathode doping", 400,
                  doi="10.1/X", source="crossref", pdf="http://example.org/a.pdf")
        out = rank(Q, [a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["citations"], 400)
        self.assertEqual(out[0]["pdf_url"], "http://example.org/a.pdf")

    def test_cap_at_max_results(self):
        items = [_item("Sodium ion battery cathode doping %d" % i, "sodium ion battery cathode doping",
                       i, doi="10.9/%d" % i) for i in range(150)]
        self.assertEqual(len(rank(Q, items)), MAX_RESULTS)
        self.assertEqual(MAX_RESULTS, 100)


if __name__ == "__main__":
    unittest.main()
