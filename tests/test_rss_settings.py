"""Configured RSS sources govern fetching without losing existing intelligence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rss
import workflow


class RSSSettingsTest(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.db=workflow.WorkflowDB(Path(temporary.name)/'db.sqlite3');self.addCleanup(self.db.close)

    def test_edit_disable_refresh_and_delete_preserve_articles(self):
        source=rss.save_source(self.db,{'title':'Original','url':'HTTPS://example.test/feed','category':'Materials'})['source']
        self.assertEqual(source['url'],'https://example.test/feed')
        self.db.save_rss_items(source,[{'title':'Existing evidence','link':'https://example.test/a'}])
        edited=rss.save_source(self.db,{'id':source['id'],'title':'Renamed','url':'https://example.test/new-feed','enabled':False})['source']
        self.assertEqual(edited['id'],source['id']);self.assertEqual(self.db.rss_count(),1)
        with patch.object(rss,'fetch') as fetch:
            self.assertEqual(rss.refresh(self.db)['sources'],0);fetch.assert_not_called()
        rss.save_source(self.db,{'id':source['id'],'enabled':True})
        with patch.object(rss,'fetch',return_value=[{'title':'New evidence','link':'https://example.test/b'}]) as fetch:
            self.assertEqual(rss.refresh(self.db)['added'],1);fetch.assert_called_once_with('https://example.test/new-feed')
        deleted=rss.delete_source(self.db,{'id':source['id']})
        self.assertEqual(deleted['retained_items'],2)
        self.assertTrue(all(item['source_deleted'] for item in self.db.rss_items()))
        self.assertEqual(self.db.rss_count(),2)

    def test_empty_user_configuration_is_not_replaced_and_invalid_changes_are_rejected(self):
        for url in ('ftp://example.test/feed','file:///tmp/feed','http://','https://example.test:bad/feed','https://bad host/feed'):
            with self.subTest(url=url),self.assertRaises(ValueError):rss.save_source(self.db,{'url':url})
        source=rss.save_source(self.db,{'title':'User','url':'https://example.test/feed'})['source']
        with self.assertRaises(ValueError):rss.save_source(self.db,{'title':'Duplicate','url':source['url']})
        with self.assertRaises(ValueError):rss.save_source(self.db,{'id':'missing','url':source['url']})
        with self.assertRaises(ValueError):rss.delete_source(self.db,{'id':'missing'})
        self.assertEqual(self.db.rss_source(source['id'])['title'],'User')
        rss.delete_source(self.db,{'id':source['id']})
        with patch.object(rss,'fetch') as fetch:
            self.assertEqual(rss.refresh(self.db)['sources'],0);fetch.assert_not_called()
        self.assertEqual(self.db.rss_sources(),[])

    def test_adding_defaults_only_fills_missing_urls_and_keeps_user_overrides(self):
        source=rss.save_source(self.db,{'title':'My Nature','url':rss.DEFAULT_SOURCES[0]['url'],'enabled':False})['source']
        rss.save_source(self.db,{'title':'Custom','url':'https://custom.example/feed'})
        self.assertEqual(rss.add_defaults(self.db)['added'],4)
        self.assertEqual(rss.add_defaults(self.db)['added'],0)
        self.assertEqual(self.db.rss_source(source['id'])['title'],'My Nature')
        self.assertEqual(self.db.rss_source(source['id'])['enabled'],0)
        with patch.object(rss,'fetch',return_value=[]) as fetch:
            self.assertEqual(rss.refresh(self.db)['sources'],5);self.assertEqual(fetch.call_count,5)

    def test_first_refresh_still_bootstraps_existing_default_sources(self):
        with patch.object(rss,'fetch',return_value=[]) as fetch:
            result=rss.refresh(self.db)
        self.assertEqual(result['sources'],5);self.assertEqual(result['fetched'],5);self.assertEqual(fetch.call_count,5)
