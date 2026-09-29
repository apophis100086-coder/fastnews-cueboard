from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastnews.budget import BudgetExhausted, RunBudget, utc_now
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.pipeline import scan_morning
from fastnews.providers import (FixtureFulltextProvider, FixtureLLMProvider,
                                FixtureSearchProvider, ProviderFailure, ProviderResult)
from fastnews.research import research_product, validate_fact_proposals
from fastnews.routing import route_product, working_now


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'research.db'
        self.conn = connect(self.path, mode='fixture')
        self.addCleanup(self.conn.close)
        self.config = load_config()
        sync_registry(self.conn,self.config)
        scan_morning(self.conn,self.config,offline=True)

    def research(self, **kwargs):
        return research_product(self.conn,self.config,'513130',**kwargs)

    def documents(self):
        result = {}
        for row in self.conn.execute('SELECT a.canonical_url,s.source_id FROM fn_articles a JOIN fn_article_versions v USING(article_id) JOIN fn_article_sources s USING(version_id)'):
            result[row['canonical_url']] = {'content':'合成原文：示例主体计划明日实施方案。', 'url':row['canonical_url'],
                'source_id':row['source_id'],'retrieved_at':'2026-09-15T08:00:00+08:00','is_fixture':True}
        return result

    def start_budget(self, **changes):
        import uuid
        run_id = 'test_' + uuid.uuid4().hex
        limits = {**self.config['runtime']['second_round'],**changes}
        with self.conn:
            self.conn.execute('INSERT INTO fn_research_runs(run_id,mode,product_code,started_at,status,limits_json,context_json) VALUES(?,?,?,?,?,?,?)',
                (run_id,'fixture','513130',utc_now(),'running',json.dumps(limits),'{}'))
        return RunBudget(self.conn,run_id)

    def test_default_none_returns_local_pool_and_explicit_gaps_without_network(self):
        with (patch.object(socket,'create_connection',side_effect=AssertionError('no network')),
              patch.object(socket,'getaddrinfo',side_effect=AssertionError('no DNS'))):
            result = self.research()
        self.assertGreater(result['researched_event_count'],0)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['usage']['llm_requests'],0)
        self.assertEqual(result['usage']['search_requests'],0)
        self.assertEqual(result['usage']['fulltext_requests'],0)
        self.assertEqual(result['usage']['cost_status'],'not_called')
        self.assertEqual(result['provider_status']['fulltext'],'fulltext_adapter_not_connected')
        self.assertFalse(result['auto_select'])
        self.assertIsNone(result['default_selection'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_decisions').fetchone()[0],0)

    def test_explicit_market_cutoff_is_preserved_in_research_view(self):
        from fastnews.__main__ import build_parser
        from fastnews.cli_actions import dispatch
        args = build_parser().parse_args(['research-product', '513130', '--offline',
                                         '--market-cutoff', 'HK=2026-09-14@synthetic_explicit_date'])
        result = dispatch(self.conn, self.config, args)
        self.assertEqual(result['view']['market_cutoffs'], {'HK': {'date': '2026-09-14', 'source': 'synthetic_explicit_date'}})

    def test_fixture_is_never_a_live_provider(self):
        with closing(connect(Path(self.tmp.name)/'live.db',mode='live')) as live:
            sync_registry(live,self.config)
            with self.assertRaisesRegex(ValueError,'mode'):
                research_product(live,self.config,'513130',search_provider=FixtureSearchProvider())

    def test_unknown_runtime_provider_is_gap_without_implicit_credentials(self):
        self.config['runtime']['providers']['llm'] = 'service-with-no-authorized-key'
        result = self.research()
        self.assertEqual(result['provider_status']['llm'],'llm_runtime_adapter_or_authorization_missing')
        self.assertEqual(result['usage']['llm_requests'],0)

    def test_failure_and_retry_both_count_with_unknown_usage_reserved(self):
        self.config['runtime']['second_round']['max_llm_requests_including_retries'] = 2
        model = FixtureLLMProvider([ProviderFailure('first timeout'),ProviderFailure('second timeout')])
        result = self.research(fulltext_provider=FixtureFulltextProvider(self.documents()),llm_provider=model)
        self.assertEqual(result['usage']['llm_requests'],2)
        self.assertEqual(result['usage']['failed_requests'],2)
        self.assertEqual(result['usage']['accounted_input_tokens'],12000)
        self.assertEqual(result['usage']['accounted_output_tokens'],3000)
        self.assertIsNone(result['usage']['input_tokens'])
        self.assertEqual(result['usage']['cost_status'],'unknown')
        self.assertEqual(result['status'],'partial')

    def test_retry_stops_before_next_request_at_limit(self):
        self.config['runtime']['second_round']['max_llm_requests_including_retries'] = 1
        model = FixtureLLMProvider([RuntimeError('secret key must never appear in audit')])
        result = self.research(fulltext_provider=FixtureFulltextProvider(self.documents()),llm_provider=model)
        self.assertEqual(len(model.calls),1)
        self.assertEqual(result['usage']['llm_requests'],1)
        audit = str([tuple(row) for row in self.conn.execute('SELECT * FROM fn_provider_calls')])
        self.assertNotIn('secret key',audit)
        self.assertNotIn('secret key',json.dumps(result))

    def test_real_usage_releases_only_unused_reservation(self):
        budget = self.start_budget(max_input_tokens=6000,max_output_tokens=1500)
        call = budget.reserve('llm','test','one',input_tokens=6000,output_tokens=1500)
        budget.settle(call,usage={'input_tokens':200,'output_tokens':100})
        usage = budget.summary()
        self.assertEqual(usage['accounted_input_tokens'],200)
        self.assertEqual(usage['output_tokens'],100)
        self.assertIsNone(usage['actual_cost'])
        self.assertEqual(usage['cost_status'],'unknown')
        with self.assertRaisesRegex(BudgetExhausted,'max_input_tokens'):
            budget.reserve('llm','test','two',input_tokens=6000,output_tokens=1)
        budget.reserve('llm','test','three',input_tokens=5800,output_tokens=1400)

    def test_missing_usage_conservatively_consumes_token_allowance(self):
        budget = self.start_budget(max_input_tokens=6000,max_output_tokens=1500)
        call = budget.reserve('llm','test','one',input_tokens=6000,output_tokens=1500)
        budget.settle(call,status='failed')
        with self.assertRaises(BudgetExhausted):
            budget.reserve('llm','test','two',input_tokens=1)
        self.assertEqual(budget.summary()['accounted_output_tokens'],1500)

    def test_cost_cap_rejects_unknown_estimate_before_call(self):
        budget = self.start_budget(max_cost=1)
        with self.assertRaisesRegex(BudgetExhausted,'cost_estimate_unavailable'):
            budget.reserve('search','live','one')
        self.assertEqual(budget.summary()['search_requests'],0)
        call = budget.reserve('search','live','one',estimated_max_cost=.8)
        budget.settle(call,status='failed')
        with self.assertRaisesRegex(BudgetExhausted,'max_cost'):
            budget.reserve('search','live','two',estimated_max_cost=.3)

    def test_concurrent_reservations_cannot_overspend_request_limit(self):
        budget = self.start_budget(max_llm_requests_including_retries=1)
        barrier = threading.Barrier(2)
        def reserve(index):
            conn = sqlite3.connect(self.path,timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                other = RunBudget(conn,budget.run_id)
                barrier.wait()
                try:
                    other.reserve('llm','fixture',str(index),input_tokens=100,output_tokens=100)
                    return 'reserved'
                except BudgetExhausted:
                    return 'blocked'
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(reserve,(1,2)))
        self.assertCountEqual(states,['reserved','blocked'])
        self.assertEqual(budget.summary()['llm_requests'],1)

    def test_fulltext_global_and_per_event_bounds(self):
        budget = self.start_budget(max_fulltext_articles=5,max_articles_per_event=4)
        for index in range(4):
            call = budget.reserve('fulltext','fixture',str(index),event_id='a')
            budget.settle(call)
        with self.assertRaisesRegex(BudgetExhausted,'max_articles_per_event'):
            budget.reserve('fulltext','fixture','fifth',event_id='a')
        budget.reserve('fulltext','fixture','fifth',event_id='b')
        with self.assertRaisesRegex(BudgetExhausted,'max_fulltext_articles'):
            budget.reserve('fulltext','fixture','sixth',event_id='b')

    def test_shared_source_document_is_fetched_once_across_events(self):
        routed = route_product(self.conn,self.config,'513130',now=working_now(self.conn,offline=True))
        original = routed['groups']['within_cutoff'][0]
        duplicate = deepcopy(original)
        duplicate['event_id'] = 'a-different-event-sharing-original'
        routed['groups']['within_cutoff'] = [original,duplicate]
        provider = FixtureFulltextProvider(self.documents())
        with patch('fastnews.research.route_product',return_value=routed):
            result = self.research(fulltext_provider=provider)
        self.assertEqual(len(provider.calls),len(set(provider.calls)))
        self.assertEqual(result['results'][0]['evidence_refs'],result['results'][1]['evidence_refs'])

    def test_fulltext_fetch_stops_at_global_budget_and_keeps_results(self):
        self.config['runtime']['second_round']['max_fulltext_articles'] = 1
        provider = FixtureFulltextProvider(self.documents())
        result = self.research(fulltext_provider=provider)
        self.assertEqual(len(provider.calls),1)
        self.assertGreater(result['researched_event_count'],1)
        self.assertTrue(any('fulltext:max_fulltext_articles' in event['gaps'] for event in result['results']))

    def test_event_limit_preserves_unresearched_ids(self):
        self.config['runtime']['second_round']['max_event_candidates'] = 2
        result = self.research()
        self.assertEqual(result['researched_event_count'],2)
        self.assertGreater(len(result['unresearched_event_ids']),0)

    def test_same_evidence_rerun_uses_validated_cache(self):
        fulltext = FixtureFulltextProvider(self.documents())
        model = FixtureLLMProvider()
        first = self.research(fulltext_provider=fulltext,llm_provider=model)
        # At most four events enter the LLM in a run. Restrict the next run to the
        # same originally completed set so no new items legitimately need calls.
        completed = {r['event_id'] for r in first['results'] if r['semantic_assessment']=='fixture_proposals_only'}
        routed = route_product(self.conn,self.config,'513130',now=working_now(self.conn,offline=True))
        routed['groups']['within_cutoff'] = [r for r in routed['groups']['within_cutoff'] if r['event_id'] in completed]
        routed['groups']['pending_time'] = [r for r in routed['groups']['pending_time'] if r['event_id'] in completed]
        count = len(model.calls)
        with patch('fastnews.research.route_product',return_value=routed):
            second = self.research(fulltext_provider=fulltext,llm_provider=model)
        self.assertGreater(count,0)
        self.assertEqual(len(model.calls),count)
        self.assertEqual(second['usage']['llm_requests'],0)
        self.assertEqual(second['usage']['fulltext_requests'],0)
        self.assertGreater(second['usage']['cache_hits'],0)

    def test_model_version_change_invalidates_cache(self):
        self.config['runtime']['second_round']['max_event_candidates'] = 1
        fulltext = FixtureFulltextProvider(self.documents())
        first_model = FixtureLLMProvider(version='v1')
        self.research(fulltext_provider=fulltext,llm_provider=first_model)
        second_model = FixtureLLMProvider(version='v2')
        result = self.research(fulltext_provider=fulltext,llm_provider=second_model)
        self.assertEqual(result['usage']['llm_requests'],1)
        self.assertEqual(result['usage']['fulltext_requests'],0)

    def test_search_snippets_never_become_evidence_or_facts(self):
        class Search(FixtureSearchProvider):
            def search(self,query,*,news_cutoff):
                self.calls.append(query)
                return ProviderResult([{'title':'搜索摘要','url':'https://example.org/original','snippet':'不算已读原文'}],{'cost':0})
        result = self.research(search_provider=Search())
        hints = [h for r in result['results'] for h in r['search_hints']]
        self.assertTrue(hints)
        self.assertTrue(all(h['original_read'] is False for h in hints))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_facts').fetchone()[0],0)
        self.assertTrue(all(not r['evidence_refs'] for r in result['results']))

    def test_external_provider_requires_explicit_runtime_authorization(self):
        model = FixtureLLMProvider()
        model.external = True
        result = self.research(fulltext_provider=FixtureFulltextProvider(self.documents()),llm_provider=model)
        self.assertEqual(len(model.calls),0)
        self.assertEqual(result['provider_status']['llm'],'runtime_external_call_not_authorized')

    def test_invalid_fact_reference_and_false_quote_are_rejected(self):
        evidence = [{'evidence_id':'e1','content':'原文计划明日实施。','kind':'plan'}]
        proposal = {'statement':'计划明日实施','kind':'plan','evidence_id':'e1','quote':'计划明日实施','start':2,'end':8}
        validate_fact_proposals([proposal],evidence)
        for change in ({'evidence_id':'invented'},{'quote':'已经实施'},{'kind':'observed'},{'selected':True}):
            with self.assertRaises(ValueError):
                validate_fact_proposals([{**proposal,**change}],evidence)

    def test_invalid_llm_output_is_rejected_and_counts_repairs(self):
        model = FixtureLLMProvider([{'selected':True},{'facts':[{'evidence_id':'invented'}],'assessment':None,'gaps':[]}])
        self.config['runtime']['second_round']['max_llm_requests_including_retries'] = 2
        result = self.research(fulltext_provider=FixtureFulltextProvider(self.documents()),llm_provider=model)
        self.assertEqual(result['usage']['llm_requests'],2)
        self.assertEqual(result['usage']['failed_requests'],2)
        self.assertTrue(all(not r['fact_proposals'] for r in result['results']))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM fn_call_cache WHERE kind='llm'").fetchone()[0],0)

    def edition(self):
        from fastnews.editorial import create_edition
        return create_edition(self.conn,self.config,product_code='513130',edition_date='2026-09-15',
                              news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={'HK':{'date':'2026-09-14','source':'explicit fixture test'}})

    def test_pending_publication_time_is_researched_without_becoming_eligible(self):
        result = self.research()
        pending = [item for item in result['results'] if item['cutoff_state']=='pending_time']
        self.assertTrue(pending)
        self.assertTrue(all(not item['fact_refs'] for item in pending))
        self.assertFalse(any(item['cutoff_state']=='out_of_cutoff' for item in result['results']))

    def test_plain_import_without_fact_is_available_to_local_extraction(self):
        from fastnews.editorial import import_evidence_payload
        edition = self.edition()
        imported = import_evidence_payload(self.conn,{'title':'合成授权文本','content':'计划明日实施。','is_fixture':True,
            'first_published_at':'2026-09-14T12:00:00+08:00'},edition_id=edition['edition_id'])
        self.assertEqual(imported['fact_ids'],[])
        model = FixtureLLMProvider()
        result = self.research(edition_id=edition['edition_id'],llm_provider=model)
        self.assertEqual(len(model.calls),1)
        self.assertEqual(model.calls[0]['event_id'],imported['event_id'])
        self.assertEqual(model.calls[0]['evidence'][0]['content'],'计划明日实施。')
        candidate = next(item for item in result['results'] if item['event_id']==imported['event_id'])
        self.assertEqual(candidate['semantic_assessment'],'fixture_proposals_only')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_decisions').fetchone()[0],0)

    def test_external_model_cannot_receive_private_import_without_specific_consent(self):
        from fastnews.editorial import import_evidence_payload
        edition = self.edition()
        imported = import_evidence_payload(self.conn,{'title':'合成私有材料','content':'PRIVATE_SECRET_PROSE','is_fixture':True,
            'first_published_at':'2026-09-14T12:00:00+08:00'},edition_id=edition['edition_id'])
        model = FixtureLLMProvider()
        model.external = True
        result = self.research(edition_id=edition['edition_id'],llm_provider=model,external_calls_authorized=True)
        self.assertEqual(model.calls,[])
        candidate = next(item for item in result['results'] if item['event_id']==imported['event_id'])
        self.assertIn('private_material_external_consent_missing',candidate['gaps'])

    def test_expired_fulltext_cache_is_refetched_and_content_change_invalidates_model(self):
        self.config['runtime']['second_round']['max_event_candidates'] = 1
        provider = FixtureFulltextProvider(self.documents())
        provider.cache_ttl_seconds = 0
        first = self.research(fulltext_provider=provider,llm_provider=FixtureLLMProvider())
        self.assertEqual(first['usage']['llm_requests'],1)
        for doc in provider.documents.values():
            doc['content'] += ' 本文增加更正。'
        model = FixtureLLMProvider()
        second = self.research(fulltext_provider=provider,llm_provider=model)
        self.assertEqual(second['usage']['fulltext_requests'],len({s['source_url'] for s in first['results'][0]['source_chain']}))
        self.assertEqual(second['usage']['llm_requests'],1)

    def test_fetched_public_text_persists_evidence_without_creating_facts_or_decisions(self):
        edition = self.edition()
        self.config['runtime']['second_round']['max_event_candidates'] = 1
        provider = FixtureFulltextProvider(self.documents())
        result = self.research(edition_id=edition['edition_id'],fulltext_provider=provider)
        selected_result = result['results'][0]
        self.assertTrue(selected_result['evidence_refs'])
        evidence_id = selected_result['evidence_refs'][0]
        row = self.conn.execute('SELECT data_json FROM fn_evidence WHERE evidence_id=?',(evidence_id,)).fetchone()
        evidence = json.loads(row[0])
        self.assertEqual(evidence['actual_access'],'public_http')
        self.assertFalse(evidence['primary_source_found'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_facts').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_decisions').fetchone()[0],0)
        expected = len({s['source_url'] for s in selected_result['source_chain']})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_edition_evidence WHERE edition_id=?',(edition['edition_id'],)).fetchone()[0],expected)

    def test_imported_search_snippet_is_not_sent_for_original_fact_extraction(self):
        from fastnews.editorial import import_evidence_payload
        edition = self.edition()
        import_evidence_payload(self.conn,{'title':'合成搜索摘要','content':'只能作为检索提示。','is_fixture':True,
            'source_type':'search_snippet','first_published_at':'2026-09-14T12:00:00+08:00'},edition_id=edition['edition_id'])
        model = FixtureLLMProvider()
        self.research(edition_id=edition['edition_id'],llm_provider=model)
        self.assertEqual(model.calls,[])

    def test_runtime_disabled_llm_blocks_configured_provider_but_keeps_other_work(self):
        self.config['runtime']['second_round']['llm_allowed_when_configured'] = False
        model = FixtureLLMProvider()
        fulltext = FixtureFulltextProvider(self.documents())
        search = FixtureSearchProvider()
        result = self.research(llm_provider=model,fulltext_provider=fulltext,search_provider=search)
        self.assertEqual(model.calls,[])
        self.assertEqual(result['usage']['llm_requests'],0)
        self.assertEqual(result['usage']['input_tokens'],0)
        self.assertEqual(result['provider_status']['llm'],'llm_disabled_by_runtime_policy')
        self.assertTrue(any(gap['code']=='llm_disabled_by_runtime_policy' for gap in result['gaps']))
        self.assertGreater(result['usage']['fulltext_requests'],0)
        self.assertGreater(result['usage']['search_requests'],0)
        self.assertTrue(all(event['semantic_assessment']=='not_performed' for event in result['results']))

    def test_runtime_disabled_llm_does_not_reuse_previous_model_proposals(self):
        self.config['runtime']['second_round']['max_event_candidates'] = 1
        fulltext = FixtureFulltextProvider(self.documents())
        first_model = FixtureLLMProvider()
        first = self.research(fulltext_provider=fulltext,llm_provider=first_model)
        self.assertEqual(first['usage']['llm_requests'],1)
        self.assertEqual(first['results'][0]['semantic_assessment'],'fixture_proposals_only')
        self.config['runtime']['second_round']['llm_allowed_when_configured'] = False
        second_model = FixtureLLMProvider()
        second = self.research(fulltext_provider=fulltext,llm_provider=second_model)
        self.assertEqual(second_model.calls,[])
        self.assertEqual(second['usage']['llm_requests'],0)
        self.assertEqual(second['results'][0]['semantic_assessment'],'not_performed')

    def policy_case(self, *, fail_allowed=False):
        """Five local denials precede one allowed source on the same event."""
        routed = route_product(self.conn,self.config,'513130',now=working_now(self.conn,offline=True))
        event = deepcopy(routed['groups']['within_cutoff'][0])
        template = event['source_chain'][0]
        urls = [f'https://fixture.example.invalid/policy-{index}' for index in range(6)]
        event['source_chain'] = [{**template,'source_url':url} for url in urls]
        routed['groups'] = {group:([event] if group=='within_cutoff' else []) for group in routed['groups']}
        documents = {url:{'content':'合成来源正文，只有经过本地策略允许的链接才执行读取。','url':url,
                         'source_id':template['source_id'],'retrieved_at':'2026-09-15T08:00:00+08:00','is_fixture':True} for url in urls}
        class PolicyProvider(FixtureFulltextProvider):
            def access_gap(self,url,*,source_id):
                return None if url==urls[-1] else 'fulltext_access_not_approved'
            def fetch(self,url,*,source_id):
                if url!=urls[-1]:
                    raise AssertionError('A local policy rejection reached provider.fetch')
                if fail_allowed:
                    self.calls.append(url)
                    raise ProviderFailure('timeout',usage={'cost':0},retryable=False)
                return super().fetch(url,source_id=source_id)
        return routed,PolicyProvider(documents)

    def test_source_policy_skips_do_not_spend_request_or_article_budget(self):
        self.config['runtime']['second_round']['max_articles_per_event'] = 1
        self.config['runtime']['second_round']['max_fulltext_articles'] = 1
        routed,provider = self.policy_case()
        with patch('fastnews.research.route_product',return_value=routed):
            result = self.research(fulltext_provider=provider)
        usage = result['usage']
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(usage['fulltext_requests'],1)
        self.assertEqual(usage['fulltext_requested_resources'],1)
        self.assertEqual(usage['fulltext_successful_requests'],1)
        self.assertEqual(usage['fulltext_successful_resources'],1)
        self.assertEqual(usage['fulltext_candidate_resource_count'],6)
        self.assertEqual(usage['fulltext_policy_skipped_resource_count'],5)
        self.assertEqual(usage['fulltext_read_document_count'],1)
        self.assertEqual(usage['research_document_count'],1)
        self.assertEqual(usage['research_resource_count'],1)
        self.assertEqual(usage['actual_cost'],0)
        self.assertEqual(usage['cost_status'],'known')
        self.assertEqual(len(result['results'][0]['fulltext_skipped']),5)
        self.assertTrue(all(item['request_performed'] is False for item in result['results'][0]['fulltext_skipped']))
        self.assertIn('fulltext:fulltext_access_not_approved',result['results'][0]['gaps'])

    def test_actual_fetch_failure_is_counted_but_never_as_a_read_document(self):
        routed,provider = self.policy_case(fail_allowed=True)
        with patch('fastnews.research.route_product',return_value=routed):
            result = self.research(fulltext_provider=provider)
        usage = result['usage']
        self.assertEqual(len(provider.calls),1)
        self.assertEqual(usage['fulltext_requests'],1)
        self.assertEqual(usage['fulltext_requested_resources'],1)
        self.assertEqual(usage['failed_requests'],1)
        self.assertEqual(usage['fulltext_successful_resources'],0)
        self.assertEqual(usage['fulltext_read_document_count'],0)
        self.assertEqual(usage['research_document_count'],0)
        self.assertEqual(usage['research_resource_count'],1)
        self.assertEqual(usage['fulltext_candidate_resource_count'],6)
        self.assertEqual(usage['fulltext_policy_skipped_resource_count'],5)
        self.assertEqual(usage['cost_status'],'known')
        self.assertIn('fulltext:timeout',result['results'][0]['gaps'])

    def test_all_local_policy_denials_have_not_called_cost_and_zero_requests(self):
        routed,provider = self.policy_case()
        provider.access_gap = lambda url,source_id: 'fulltext_access_not_approved'
        with patch('fastnews.research.route_product',return_value=routed):
            result = self.research(fulltext_provider=provider)
        self.assertEqual(provider.calls,[])
        self.assertEqual(result['usage']['fulltext_requests'],0)
        self.assertEqual(result['usage']['fulltext_articles'],0)
        self.assertEqual(result['usage']['research_document_count'],0)
        self.assertEqual(result['usage']['research_resource_count'],0)
        self.assertEqual(result['usage']['fulltext_policy_skipped_resource_count'],6)
        self.assertEqual(result['usage']['cost_status'],'not_called')


if __name__ == '__main__':
    unittest.main()
