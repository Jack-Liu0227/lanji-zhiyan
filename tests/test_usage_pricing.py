import tempfile
from pathlib import Path
import unittest

import workflow
import model_pricing as pricing
import usage_stats


class UsagePricingTest(unittest.TestCase):
    def row(self, **kwargs):
        return {'prompt_tokens':1000, 'output_tokens':200, 'cached_tokens':800,
                'cache_write_tokens':0, 'created_at':'2026-10-28T15:00:00+00:00', **kwargs}

    def test_official_endpoint_and_explicit_alias_only(self):
        provider=pricing.provider_for({'base_url':'https://api.deepseek.com/v1'})
        self.assertEqual(provider,'deepseek')
        self.assertEqual(pricing.lookup(provider,'DeepSeek-V4.1-Flash')['input'],.15)
        self.assertIsNone(pricing.lookup(provider,'deepseek-flash-unreleased'))
        for url in ['https://api.deepseek.com.evil.test/v1','https://proxy.test/v1','http://api.deepseek.com/v1','https://api.z.ai/api/coding/paas/v4']:
            self.assertEqual(pricing.provider_for({'base_url':url}),'custom')

    def test_cache_subset_and_reasoning_are_not_double_counted(self):
        price=pricing.lookup('deepseek','deepseek-flash')
        result=pricing.estimate(self.row(reasoning_tokens=100),price)
        self.assertAlmostEqual(result['cost'],(200*.15+800*.003+200*.6)/1e6)
        self.assertEqual(result['cost_min'],result['cost'])
        self.assertIsNone(pricing.estimate(self.row(cached_tokens=None),price)['cost'])
        self.assertIsNone(pricing.estimate(self.row(output_tokens=None),price)['cost'])
        self.assertIsNone(pricing.estimate(self.row(cached_tokens=2000),price)['cost'])
        free=pricing.lookup('zai','glm-4.7-flash')
        self.assertEqual(pricing.estimate(self.row(cached_tokens=None),free)['cost'],0)

    def test_peak_window_and_unknown_cache_ttl_are_intervals(self):
        price=pricing.lookup('deepseek','deepseek-flash')
        result=pricing.estimate(self.row(created_at='2026-10-28T01:00:00+00:00'),price)
        self.assertAlmostEqual(result['cost'],result['cost_min']*2)
        weekend=pricing.estimate(self.row(created_at='2026-10-25T01:00:00+00:00'),price)
        self.assertEqual(weekend['cost_min'],weekend['cost'])
        result=pricing.estimate(self.row(cache_write_tokens=100),pricing.lookup('anthropic','claude-sonnet-5-5'))
        self.assertAlmostEqual(result['cost_min'],(100*2+800*.1+100*2.5+200*10)/1e6)
        self.assertAlmostEqual(result['cost'],(100*2+800*.1+100*4+200*10)/1e6)

    def test_input_tier_boundary_and_published_future_rate(self):
        price=pricing.lookup('google','gemini-3.1-pro-preview')
        self.assertEqual(pricing.effective(price,200000)['input'],2)
        self.assertEqual(pricing.effective(price,200001)['input'],4)
        price=pricing.lookup('google','gemini-4-argon')
        self.assertEqual(pricing.effective(price,0,'2026-11-30')['output'],10)
        self.assertEqual(pricing.effective(price,0,'2026-12-01')['output'],20)

    def test_deepseek_usage_and_partial_usage_normalization(self):
        normalized=usage_stats.normalize({'prompt_tokens':1000,'completion_tokens':200,'prompt_cache_hit_tokens':800},'openai-completions')
        self.assertEqual(normalized['cached'],800)
        self.assertTrue(normalized['exact'])
        self.assertFalse(usage_stats.normalize({'prompt_tokens':100},'openai-completions')['exact'])
        self.assertFalse(usage_stats.normalize({'prompt_tokens':None,'completion_tokens':None},'openai-completions')['exact'])
        self.assertEqual(usage_stats.context_sizes([],[])['tools'],0)

    def test_report_manual_reset_currency_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as folder, workflow.WorkflowDB(Path(folder)/'test.sqlite3') as db:
            model=db.add_model('DeepSeek','https://api.deepseek.com/v1','deepseek-flash','DONT_RETURN_SECRET')
            usage_stats.record(model,[],[],30,{'prompt':1000,'completion':200,'cached':0,'exact':True},'ok',0)
            usage_stats.record(model,[],[],30,{},'error',0)
            result=usage_stats.report(db,{})
            self.assertEqual(len(result['daily']),30)
            self.assertEqual(result['totals']['metered'],1)
            self.assertEqual(result['totals']['priced'],1)
            self.assertNotIn('DONT_RETURN_SECRET',str(result))
            self.assertEqual(result['profile_prices'][0]['price']['origin'],'catalog')
            usage_stats.route('POST','/api/usage/prices',{'profile_id':model['id'],'input':1,'output':4,'cached':.02,'currency':'CNY'},{},db)
            result=usage_stats.report(db,{})
            self.assertAlmostEqual(result['totals']['costs']['CNY']['max'],.0018)
            self.assertEqual(result['totals']['cost'],0)
            usage_stats.route('POST','/api/usage/prices',{'profile_id':model['id'],'reset':True},{},db)
            self.assertEqual(usage_stats.report(db,{})['profile_prices'][0]['price']['origin'],'catalog')
            for invalid in [float('nan'),float('inf'),-1]:
                with self.assertRaises(ValueError):
                    usage_stats.route('POST','/api/usage/prices',{'profile_id':model['id'],'input':invalid,'output':1},{},db)

    def test_dictionary_metadata_and_prices_are_valid(self):
        self.assertGreaterEqual(len(pricing.MODEL_PRICES),40)
        for price in pricing.MODEL_PRICES.values():
            self.assertTrue(price['source'].startswith('https://'))
            self.assertEqual(price['checked_at'],'2026-10-28')
            self.assertIn(price['currency'],('USD','CNY'))
            self.assertGreaterEqual(price['input'],0)
            self.assertGreaterEqual(price['output'],0)
