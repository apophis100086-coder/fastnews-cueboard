"""Behavior acceptance with explicitly synthetic metadata and zero network."""
from datetime import datetime, timezone
from pathlib import Path
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastnews.adapters import DiscoveryError, DiscoveryPage
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.editorial import create_edition, save_decision
from fastnews.live_sources import parse_listing
from fastnews.metadata import normalize_record, cutoff_state, parse_instant
from fastnews.network import HTTPResult
from fastnews.pipeline import store_record
from fastnews.research import research_product
from fastnews.routing import route_product


class SyntheticListings:
    supported_sources = {'cls_morning','cls_hk_morning','trendforce','fda'}
    def __init__(self, fail=None, record_mode=True):
        self.fail, self.record_mode, self.calls, self.stats = fail, record_mode, [], {}
    def discover(self, sid, start, end, limit):
        self.calls.append((sid,start,end,limit))
        if sid == self.fail:
            raise DiscoveryError('timeout')
        titles = {'cls_morning':'【早报】合成利率观察：美联储降息',
                  'cls_hk_morning':'港股早报｜合成腾讯 AI应用使用数据',
                  'trendforce':'合成 NOR Flash 与半导体设备订单',
                  'fda':'Synthetic FDA clinical cancer drug trial'}
        record = normalize_record({'source_id':sid,'url':'https://fixture.example.invalid/'+sid,
            'title':titles[sid],'lead':'合成测试元数据，不是真实新闻。',
            'report_published_at':'2026-09-14T07:00:00+08:00'},is_fixture=self.record_mode)
        return DiscoveryPage([record],False,None,1,coverage_reason='synthetic_first_page_only')


class ProductDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = connect(Path(self.tmp.name)/'fixture.db',mode='fixture')
        self.addCleanup(self.conn.close)
        self.config = load_config()
        sync_registry(self.conn,self.config)
        self.now = '2026-09-15T10:00:00+08:00'
        self.no_network = patch.object(socket,'create_connection',side_effect=AssertionError('offline test'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def research(self, code='513130', **kwargs):
        return research_product(self.conn,self.config,code,now=self.now,**kwargs)

    def test_empty_database_research_really_discovers_and_reroutes(self):
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_articles').fetchone()[0],0)
        adapter = SyntheticListings()
        result = self.research(discovery_adapter=adapter)
        self.assertGreater(result['match_counts']['core'],0)
        self.assertIn('港股早报',result['results'][0]['title'])
        self.assertEqual(result['usage']['fixed_source_requests'],3)
        self.assertNotIn('fda',[c[0] for c in adapter.calls])
        self.assertEqual(result['usage']['web_search_requests'],0)
        self.assertEqual(result['usage']['llm_requests'],0)
        self.assertEqual(result['usage']['fulltext_requests'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_decisions').fetchone()[0],0)
        self.assertTrue(all(row[0] is None for row in self.conn.execute('SELECT cursor FROM fn_sources')))
        self.assertTrue(all(call[2] == parse_instant('2026-09-14T23:59:59+08:00') for call in adapter.calls))

    def test_four_products_return_different_explained_matches(self):
        expected = {'513130':'AI应用','513530':'降息','588710':'NOR Flash','517120':'clinical'}
        signatures = set()
        for code, keyword in expected.items():
            r = self.research(code,discovery_adapter=SyntheticListings())
            core = [e for e in r['results'] if e['product_match']['lane']=='core']
            self.assertTrue(core,code)
            self.assertTrue(any(keyword in e['product_match']['keywords'] for e in core),code)
            signatures.add(tuple(e['event_id'] for e in core))
            self.assertTrue(all(e['product_match']['excerpts'] for e in core))
            self.assertTrue(all(e['product_match']['holdings_verified'] is False for e in core))
        self.assertEqual(len(signatures),4)

    def test_source_failure_isolated_and_requests_bounded_before_call(self):
        self.config['runtime']['second_round']['max_search_requests'] = 2
        adapter = SyntheticListings(fail='cls_hk_morning')
        r = self.research(discovery_adapter=adapter)
        self.assertEqual(len(adapter.calls),2)
        self.assertEqual(r['usage']['search_requests'],2)
        rows = r['fixed_source_discovery']['sources']
        self.assertTrue(any(s['status']=='failed' and s['error_code']=='timeout' for s in rows))
        self.assertTrue(any(s['status']=='read' for s in rows))
        self.assertTrue(any(s['status']=='budget_stopped' for s in rows))
        self.assertGreater(r['event_pool_count'],0)

    def test_rerun_idempotent_and_preserves_human_choice(self):
        edition = create_edition(self.conn,self.config,product_code='513130',edition_date='2026-09-15',
            news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        first = self.research(edition_id=edition['edition_id'],discovery_adapter=SyntheticListings())
        eid = first['results'][0]['event_id']
        save_decision(self.conn,edition_id=edition['edition_id'],selected_event_id=eid,
            selected_fact_ids=[],expected_revision=0,actor_type='human')
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM fn_decisions')]
        second = self.research(edition_id=edition['edition_id'],discovery_adapter=SyntheticListings())
        self.assertEqual(second['fixed_source_discovery']['articles_new'],0)
        self.assertEqual(second['fixed_source_discovery']['versions_new'],0)
        self.assertEqual(before,[tuple(r) for r in self.conn.execute('SELECT * FROM fn_decisions')])

    def test_rejects_fixture_live_mismatch_before_storage(self):
        r = self.research(discovery_adapter=SyntheticListings(record_mode=False))
        self.assertEqual(r['fixed_source_discovery']['articles_new'],0)
        self.assertEqual(r['researched_event_count'],0)
        self.assertTrue(all(s['error_code']=='invalid_metadata' for s in r['fixed_source_discovery']['sources'] if s['request_performed']))

    def test_old_and_post_cutoff_records_are_available_but_do_not_pad_candidates(self):
        for slug, title, day in [('old','合成腾讯 AI应用旧闻','2026-08-10'),('new','合成腾讯 AI应用截点外','2026-09-15'),('irrelevant','合成玉米农业信息','2026-09-14')]:
            record = normalize_record({'source_id':'cls_morning','url':'https://fixture.example.invalid/'+slug,
                                      'title':title,'lead':'','report_published_at':day})
            with self.conn:store_record(self.conn,record,self.config,self.now)
        r = self.research()
        self.assertEqual(r['researched_event_count'],0)
        route = route_product(self.conn,self.config,'513130',now=parse_instant(self.now))
        self.assertEqual(route['event_count'],3)
        self.assertEqual(len(route['groups']['out_of_cutoff']),1)
        self.assertIsNone(route['default_selection'])

    def test_fda_name_alone_or_veterinary_drugs_do_not_match_innovative_drug(self):
        for index, title in enumerate(['Synthetic FDA nutrition announcement','Synthetic FDA drug for dogs and cats']):
            with self.conn:store_record(self.conn,normalize_record({'source_id':'fda',
                'url':f'https://fixture.example.invalid/vet/{index}','title':title,'lead':'',
                'report_published_at':'2026-09-14'}),self.config,self.now)
        r = self.research('517120')
        self.assertEqual(r['match_counts']['core'],0)
        self.assertEqual(r['researched_event_count'],0)

    def test_unknown_first_publication_never_makes_later_report_eligible(self):
        record = normalize_record({'source_id':'cls_morning','title':'合成后发报道','lead':'',
            'url':'https://fixture.example.invalid/new','report_published_at':'2026-09-15T07:00:00+08:00'})
        self.assertEqual(cutoff_state(record,parse_instant('2026-09-14T23:59:59+08:00')),'out_of_cutoff')
        self.assertIsNone(record['first_published_at'])


if __name__ == '__main__':
    unittest.main()
