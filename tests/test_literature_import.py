"""RSS evidence enters the canonical project bibliography without duplicate records."""
from pathlib import Path
import tempfile
import unittest

import asset_service
import literature_import as library
import project_library
import rss
import workflow


class LiteratureImportTest(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.db=workflow.WorkflowDB(Path(folder.name)/'research.sqlite3');self.addCleanup(self.db.close)
        self.pid=self.db.create_task('Research');self.other=self.db.create_task('Other')
        source=rss.save_source(self.db,{'title':'Materials','url':'https://example.test/feed'})['source']
        self.db.save_rss_items(source,[{'title':'Ion transport','link':'https://doi.org/10.1234/ION.2026','authors':'Li; Wang','published_at':'Wed, 28 Oct 2026 08:00:00 GMT','summary':'Evidence from impedance.'},
                                     {'title':'Unsafe link','link':'javascript:alert(1)'},
                                     {'title':'Second evidence','link':'https://example.test/evidence?utm_source=feed'}])
        self.rows={row['title']:row for row in self.db.rss_items()}

    def test_import_is_project_scoped_and_retains_edits_on_repeat(self):
        identifier=self.rows['Ion transport']['id']
        first=library.import_rss(self.db,self.pid,[identifier,identifier])
        self.assertEqual((first['imported'],first['existing']),(1,0))
        row=project_library.items(self.db,self.pid)[0]
        self.assertEqual(row['doi'],'10.1234/ion.2026');self.assertEqual(row['year'],'2026')
        self.assertEqual(row['abstract'],'Evidence from impedance.');self.assertFalse(row['has_pdf'])
        self.assertTrue(asset_service.list_assets(self.db,self.pid,'paper'))
        project_library.update_record(self.db,self.pid,row['result_id'],{'title':'Edited by researcher','url':'https://example.test/changed'})
        repeated=library.import_rss(self.db,self.pid,[identifier])
        self.assertEqual((repeated['imported'],repeated['existing']),(0,1))
        self.assertEqual(project_library.items(self.db,self.pid)[0]['title'],'Edited by researcher')
        self.assertEqual(library.annotate(self.db,[self.rows['Ion transport']],self.pid)[0]['imported_result_id'],row['result_id'])
        self.assertEqual(library.import_rss(self.db,self.other,[identifier])['imported'],1)
        self.assertNotEqual(project_library.items(self.db,self.other)[0]['result_id'],row['result_id'])

    def test_cross_source_doi_url_dedup_and_partial_invalid_items(self):
        rid=self.db.cache_result(self.pid,{'source':'OpenAlex','title':'Existing DOI evidence','doi':'https://doi.org/10.1234/ion.2026','url':'https://publisher.test/one'})
        result=library.import_rss(self.db,self.pid,[self.rows['Ion transport']['id'],self.rows['Unsafe link']['id']])
        self.assertEqual((result['imported'],result['existing'],len(result['failures'])),(0,1,1))
        self.assertEqual(result['items'][0]['result_id'],rid)
        manual=library.import_reference(self.db,self.pid,{'title':'Manual evidence','url':'https://example.test/evidence#reading'})
        imported=library.import_rss(self.db,self.pid,[self.rows['Second evidence']['id']])
        self.assertEqual(imported['items'][0]['result_id'],manual['result_id'])
        self.assertEqual(len(project_library.items(self.db,self.pid)),2)

    def test_manual_import_validation_and_missing_ids_never_write(self):
        before=len(self.db.results(self.pid))
        for body in ({'title':'Bad','url':'file:///C:/private.pdf'}, {'title':'Bad','doi':'bad'}, {'title':'','url':'https://example.test'}):
            with self.subTest(body=body),self.assertRaises(ValueError):library.import_reference(self.db,self.pid,body)
        with self.assertRaises(ValueError):library.import_rss(self.db,self.pid,[self.rows['Ion transport']['id'],'missing'])
        with self.assertRaises(ValueError):library.import_rss(self.db,'missing',[self.rows['Ion transport']['id']])
        self.assertEqual(len(self.db.results(self.pid)),before)
        value=library.import_reference(self.db,self.pid,{'title':'Manual DOI paper','doi':'doi:10.9876/EXAMPLE'})
        self.assertEqual(self.db.result(value['result_id'])['url'],'https://doi.org/10.9876/example')

    def test_different_urls_with_the_same_title_remain_distinct_references(self):
        first=library.import_reference(self.db,self.pid,{'title':'Annual evidence report','url':'https://example.test/2025'})
        second=library.import_reference(self.db,self.pid,{'title':'Annual evidence report','url':'https://example.test/2026'})
        self.assertNotEqual(first['result_id'],second['result_id'])
        self.assertEqual(len(project_library.items(self.db,self.pid)),2)
