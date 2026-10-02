"""Two-day overview and agent evidence boundaries on synthetic cases."""
from copy import deepcopy
import unittest
from fastnews.reading_reports import build_input,validate_report
from fastnews.host_evidence import enrich_source,SOURCE_EXTRA
from fastnews.style_profiles import profile
from fastnews.search_audit import observed_queries
from tests.test_v64 import V64Tests


class ImprovementTests(unittest.TestCase):
    def test_serve_resolves_workspace_default_before_validating_port(self):
        from unittest.mock import patch,MagicMock
        from fastnews.server import serve
        with patch('fastnews.server.LocalApplication') as app,patch('fastnews.server.make_server') as make:
            conn=MagicMock();app.return_value.connection.return_value=conn
            with patch('fastnews.server.sync_registry'),patch('fastnews.source_catalog.configured_view'),patch('fastnews.server.workspace_settings',return_value={'port':8787}):
                serve('synthetic.db',config={'synthetic':True})
            self.assertEqual(make.call_args.args[1],8787)
            make.return_value.serve_forever.assert_called_once()

    def test_yesterday_is_in_overview_and_older_events_remain_context(self):
        fixture=V64Tests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        for day in ['2026-09-12','2026-09-13','2026-09-14']:fixture.article(day,title=day)
        payload=build_input(fixture.conn,fixture.view(),'default','morning','2026-09-14')
        raw=dict(items=[dict(title=s['title'],summary=s['title'],why='合成变化',category='policy',source_refs=[s['id']])
            for s in payload['items']],web_sources=[],coverage=[],gaps=[])
        result=validate_report(raw,payload,{'web_actions':0})
        self.assertEqual({i['title'] for i in result['items']},{'2026-09-13','2026-09-14'})
        self.assertEqual(len(result['validation_audit']['context_items']),1)
        self.assertEqual(payload['main_days'],2)

    def test_agent_declared_page_text_and_search_are_not_verified(self):
        source=dict(id='web_1',url='https://example.org/a',excerpt='synthetic quote',published_at='2026-09-14')
        raw=dict({k:None for k in SOURCE_EXTRA},material_kind='page_fragment')
        trace=dict(evidence_status='agent_reported',actions=[dict(type='search',status='complete',query='科技')],
            tool_excerpts=[dict(url=source['url'],text=source['excerpt'],kind='page_fragment',tool_ref='r1')])
        result=enrich_source(source,raw,{'source_constraints':[]},trace)
        self.assertFalse(result['source_excerpt_verified']);self.assertFalse(result['fact_verified'])
        self.assertEqual(observed_queries(trace),[])

    def test_style_profile_contains_rules_without_news_or_sample_prose(self):
        result=profile('513130',library='nonexistent-library.json')
        self.assertTrue(result['common']);self.assertTrue(result['product']['patterns'])
        self.assertEqual(result['purpose'],'style_only_not_current_facts')
        self.assertTrue(result['sample_gaps']);self.assertNotIn('text',result)

    def test_new_reader_profile_preserves_the_workspace_connection_mode(self):
        from fastnews.source_catalog import save_profile
        fixture=V64Tests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        created=save_profile(fixture.conn,{'name':'合成直连方案'})
        self.assertTrue(created['settings']['agent_direct'])
        saved=save_profile(fixture.conn,{'profile_id':created['profile_id'],'name':'重命名合成方案'})
        self.assertTrue(saved['settings']['agent_direct'])
