import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import pdfio
from research_core import ResearchDB


def two_column_pdf(path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"""BT /F1 10 Tf
        1 0 0 1 40 700 Tm [(Left) -200 (column) -200 (starts.)] TJ
        1 0 0 1 40 685 Tm [(Left) -200 (column) -200 (continues.)] TJ
        1 0 0 1 350 700 Tm [(Right) -200 (column) -200 (starts.)] TJ
        1 0 0 1 350 685 Tm [(Right) -200 (column) -200 (continues.)] TJ
        ET""")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)
    return path


class PdfReadingOrderTest(unittest.TestCase):
    def setUp(self):
        indexer = patch("semantic.warm_async")
        indexer.start()
        self.addCleanup(indexer.stop)

    def test_two_columns_preserve_word_spaces_and_reading_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = two_column_pdf(Path(temporary) / "columns.pdf")
            self.assertEqual(pdfio.page_texts(source), [
                "Left column starts.\nLeft column continues.\nRight column starts.\nRight column continues."
            ])

    def test_ingested_column_excerpt_passes_exact_evidence_gate(self):
        with tempfile.TemporaryDirectory() as temporary, ResearchDB(Path(temporary) / "test.sqlite3") as db:
            project = db.create_task("Column extraction")
            paper = db.ingest_pdf(project, two_column_pdf(Path(temporary) / "columns.pdf"))
            accepted = db.add_evidence(paper["id"], {
                "page_no": 1, "field": "reading order", "value": "left column",
                "excerpt": "Left column starts. Left column continues.",
            })
            self.assertEqual(accepted["excerpt"], "Left column starts. Left column continues.")
            with self.assertRaisesRegex(ValueError, "not present"):
                db.add_evidence(paper["id"], {
                    "page_no": 1, "field": "reading order", "value": "invented",
                    "excerpt": "Left column invents a result.",
                })
