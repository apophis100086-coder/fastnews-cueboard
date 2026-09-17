"""Offline contracts for personal sources and host-generated research reports."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews import editorial
from fastnews.custom_sources import parse_feed
from fastnews.adapters import DiscoveryError
from fastnews.host_provider import clean_environment, parse_codex_output, HostResult, HostUnavailable
from fastnews.metadata import normalize_record, time_bounds, parse_instant
from fastnews.pipeline import store_record
from fastnews.reading_reports import build_input, generate_report, validate_report, adopt_report_item, latest_report
from fastnews.source_catalog import catalog, configured_view, save_profile, save_source, section_ids


class ReaderDatabaseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.conn=connect(Path(temp.name)/'reader.fixture.db',mode='fixture')
        self.addCleanup(self.conn.close)
        self.base=load_config()
        sync_registry(self.conn,self.base)

    def source(self, **changes):
        return {'name':'测试订阅','url':'https://example.org/feed.xml','collection':'feed',
                'sections':['morning','sector'],'categories':['technology'],**changes}

    def save_article(self):
        with self.conn:
            store_record(self.conn,normalize_record({'source_id':'cls_morning',
                'title':'合成测试：平台云计算业务公告','url':'https://example.org/test-article',
                'lead':'合成测试：云计算、半导体设备业务和南向资金信息，仍待核实。',
                'report_published_at':'2026-09-14T07:00:00+08:00','is_fixture':True}),
                self.base,'2026-09-14T08:00:00+08:00')

    def view(self, pid='default'):
        return configured_view(self.conn,self.base,pid)

    def edition(self, code='513130'):
        return editorial.create_edition(self.conn,self.base,product_code=code,edition_date='2026-09-15',
            news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})

    def test_all_original_sources_remain_with_categories_and_separate_sections(self):
        before=deepcopy(self.base)
        rows=catalog(self.conn,self.base)
        self.assertEqual(len(rows),34)
        self.assertTrue(set(self.base['sources']).issubset({s['source_id'] for s in rows}))
        self.assertEqual({r['source_id'] for r in rows},set(self.view()['sources']))
        self.assertTrue(all(r['categories'] and r['sections'] for r in rows))
        self.assertEqual(len(section_ids(self.view(),'morning')),6)
        self.assertEqual(len(section_ids(self.view(),'sector')),28)
        self.assertEqual(self.base,before)

    def test_custom_sources_and_enabled_overrides_are_isolated_between_profiles(self):
        p=save_profile(self.conn,{'name':'医药同事'})['profile_id']
        custom=save_source(self.conn,self.base,p,self.source())
        save_source(self.conn,self.base,p,{'source_id':'cls_morning','enabled':False})
        self.assertEqual(len(catalog(self.conn,self.base)),34)
        self.assertEqual(len(catalog(self.conn,self.base,p)),35)
        self.assertNotIn('cls_morning',section_ids(self.view(p),'morning'))
        self.assertIn('cls_morning',section_ids(self.view(),'morning'))
        with self.assertRaises(ValueError):
            save_source(self.conn,self.base,'default',{'source_id':custom['source_id'],'enabled':False})

    def test_custom_feed_in_shared_store_does_not_break_other_profiles_product_route(self):
        from fastnews.routing import route_product
        from fastnews.server import LocalApplication
        pid=save_profile(self.conn,{'name':'测试来源方案'})['profile_id']
        source=save_source(self.conn,self.base,pid,self.source())
        config=self.view(pid)
        sync_registry(self.conn,config)
        with self.conn:
            store_record(self.conn,normalize_record({'source_id':source['source_id'],
                'title':'合成云计算业务消息','url':'https://example.org/shared-item',
                'lead':'合成云计算业务资料','report_published_at':'2026-09-14','is_fixture':True}),
                config,'2026-09-14T08:00:00+08:00')
        edition=self.edition()
        route=route_product(self.conn,self.base,'513130',news_cutoff=edition['news_cutoff'])
        self.assertEqual(route['event_count'],1)
        dbpath=self.conn.execute('PRAGMA database_list').fetchone()[2]
        app=LocalApplication(dbpath,self.base,offline=True)
        self.assertEqual(app.detail(edition['edition_id'])['route']['event_count'],0)
        self.assertEqual(app.detail(edition['edition_id'],pid)['route']['event_count'],1)
        self.assertEqual(len(app.state()['reader_sources']),34)
        self.assertEqual(len(app.state()['sources']['sources']),34)

    def test_source_null_id_creates_valid_id_and_builtin_url_does_not_repoint_collector(self):
        item=save_source(self.conn,self.base,'default',self.source(source_id=None))
        self.assertTrue(item['source_id'].startswith('custom_'))
        original=next(s for s in catalog(self.conn,self.base) if s['source_id']=='cls_morning')
        changed=save_source(self.conn,self.base,'default',{'source_id':'cls_morning','url':'https://example.org/new'})
        self.assertEqual(changed['collection_url'],original['collection_url'])
        self.assertNotEqual(changed['url'],original['url'])

    def test_public_source_link_and_search_plan_never_point_at_internal_data_endpoint(self):
        item=next(s for s in catalog(self.conn,self.base) if s['source_id']=='eastmoney_hsgt')
        self.assertIn('data.eastmoney.com/hsgt/',item['url'])
        self.assertIn('datacenter-web.eastmoney.com/api/',item['collection_url'])
        from fastnews.search_plan import build_search_plan
        plan=build_search_plan(self.view(),'513530')
        self.assertNotIn('site:datacenter-web.eastmoney.com',str(plan['queries']))

    def test_source_url_validation_rejects_private_scheme_and_nonstandard_port(self):
        for url in ('http://127.0.0.1/','http://10.0.0.1/','http://[::1]/',
                    'file:///C:/private','https://user:pass@example.org/','https://example.org:8080/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                save_source(self.conn,self.base,'default',self.source(url=url))
        self.assertEqual(len(catalog(self.conn,self.base)),34)

    def test_personal_section_and_name_override_propagate_without_changing_base(self):
        save_source(self.conn,self.base,'default',{'source_id':'miit','sections':['sector'],'name':'部委信息'})
        config=self.view()
        self.assertFalse(config['sources']['miit']['morning_scan'])
        self.assertEqual(config['sources']['miit']['name'],'部委信息')
        self.assertNotEqual(config['sources']['miit']['name'],self.base['sources']['miit']['name'])

    def test_invalid_source_settings_do_not_persist(self):
        for change in ({'enabled':'yes'},{'categories':[]},{'sections':['unknown']},
                       {'categories':['not-real']},{'collection':'arbitrary-crawler'},{'command':'rm'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                save_source(self.conn,self.base,'default',self.source(**change))
        self.assertEqual(len(catalog(self.conn,self.base)),34)

    def test_morning_input_uses_chosen_day_sources_and_metadata_only(self):
        self.save_article()
        payload=build_input(self.conn,self.view(),'default','morning','2026-09-14')
        self.assertEqual(len(payload['items']),1)
        self.assertEqual(payload['items'][0]['evidence_level'],'listing_metadata')
        self.assertEqual(len(payload['selected_sources']),3)
        save_source(self.conn,self.base,'default',{'source_id':'cls_morning','enabled':False})
        self.assertEqual(build_input(self.conn,self.view(),'default','morning','2026-09-14')['items'],[])

    def test_all_product_inputs_include_distinct_observation_variables_and_cutoff(self):
        variables=[]
        for code in self.base['products']:
            edition=self.edition(code)
            payload=build_input(self.conn,self.view(),'default','product','2026-09-15',edition)
            self.assertEqual(payload['news_cutoff'],edition['news_cutoff'])
            self.assertEqual(payload['product']['observation_variables'],edition['profile']['observation_variables'])
            variables.append(tuple(payload['product']['observation_variables']))
        self.assertEqual(len(set(variables)),len(self.base['products']))

    def test_manual_reference_links_stay_local_outside_host_query_input(self):
        edition=self.edition('517120')
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',edition)
        self.assertNotIn('manual_sources',payload['search_plan'])
        self.assertIn('cde',{s['source_id'] for s in payload['selected_sources']})
        self.assertNotIn('dxy_insight',{s['source_id'] for s in payload['selected_sources']})
        restricted = next(s for s in payload['source_constraints'] if s['source_id']=='dxy_insight')
        self.assertFalse(restricted['search_enabled'])
        self.assertTrue(any('手动参考入口' in note for note in payload['coverage_notes']))
        self.assertTrue(any(q['lane']=='keywords' for q in payload['search_plan']['queries']))

    def runner(self, host, prompt, schema, **kwargs):
        payload=json.loads(prompt.split('INPUT_JSON:\n',1)[1])
        ref=payload['items'][0]['id']
        return HostResult({'items':[{'title':'合成研究标题','summary':'合成研究摘要',
            'why':'产品观察变量','category':'technology','source_refs':[ref]}],
            'web_sources':[],'coverage':[],'gaps':[]},
            {'host':host,'input_tokens':10,'output_tokens':20,'host_tasks':1,'is_fixture':True},
            {'web_actions':0,'visited_urls':[]})

    def test_reports_are_cached_and_never_auto_create_facts_or_decisions(self):
        self.save_article()
        runner=Mock(side_effect=self.runner)
        first=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
        second=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
        self.assertEqual(runner.call_count,1)
        self.assertEqual(first['report_id'],second['report_id'])
        self.assertTrue(second['cache_reused'])
        for table in ('fn_facts','fn_decisions'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assertFalse(first['output']['items'][0]['selected'])
        self.assertFalse(first['output']['items'][0]['fact_verified'])

    def test_product_reuses_morning_summary_citations_but_keeps_research_identity(self):
        self.save_article()
        report=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=self.runner)
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',self.edition())
        reused=[s for s in payload['items'] if s.get('origin')=='saved_research_report']
        self.assertEqual(len(reused),1)
        self.assertEqual(reused[0]['prior_report_id'],report['report_id'])
        self.assertEqual(reused[0]['url'],'https://example.org/test-article')
        self.assertEqual(reused[0]['evidence_level'],'prior_ai_reading_hint')
        self.assertIn('原文待核实',reused[0]['excerpt'])

    def test_today_refresh_reuses_recent_report_and_keeps_original_cutoff(self):
        self.save_article()
        runner=Mock(side_effect=self.runner)
        with patch('fastnews.reading_reports.working_now',return_value=parse_instant('2026-09-14T08:00:00+08:00')):
            first=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
        with patch('fastnews.reading_reports.working_now',return_value=parse_instant('2026-09-14T08:01:00+08:00')):
            second=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
        self.assertTrue(second['cache_reused'])
        self.assertEqual(runner.call_count,1)
        self.assertEqual(first['news_cutoff'],second['news_cutoff'])

    def test_product_cache_never_crosses_distinct_frozen_editions(self):
        self.save_article()
        first=self.edition()
        other=editorial.create_edition(self.conn,self.base,product_code='513130',edition_date='2026-09-15',
            news_cutoff=first['news_cutoff'],market_cutoffs={'HK':{'date':'2026-09-14','source':'synthetic test'}})
        runner=Mock(side_effect=self.runner)
        generate_report(self.conn,self.view(),'default','product','2026-09-15',edition=first,runner=runner)
        result=generate_report(self.conn,self.view(),'default','product','2026-09-15',edition=other,runner=runner)
        self.assertEqual(result['edition_id'],other['edition_id'])
        self.assertEqual(runner.call_count,2)

    def test_fixture_never_calls_real_host_and_none_provider_gives_clear_error(self):
        with self.assertRaisesRegex(ValueError,'合成测试模式'):
            generate_report(self.conn,self.view(),'default','morning','2026-09-14')
        save_profile(self.conn,{'profile_id':'default','name':'无模型','host':'none'})
        with self.assertRaisesRegex(HostUnavailable,'尚未选择'):
            generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=self.runner)

    def test_failed_host_persists_failure_without_erasing_metadata_or_human_choices(self):
        self.save_article()
        before=self.conn.execute('SELECT count(*) FROM fn_article_versions').fetchone()[0]
        with self.assertRaises(HostUnavailable):
            generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=Mock(side_effect=HostUnavailable('测试失败')))
        report=latest_report(self.conn,'default','morning','2026-09-14')
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['error_code'],'host_unavailable')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_article_versions').fetchone()[0],before)

    def test_daily_budget_counts_failed_attempts_across_profiles(self):
        from fastnews.reader_limits import reader_limits
        limits=reader_limits()|{'max_tasks_per_day':1}
        runner=Mock(side_effect=HostUnavailable('测试失败'))
        other=save_profile(self.conn,{'name':'另一个方案'})['profile_id']
        with patch('fastnews.reading_reports.reader_limits',return_value=limits):
            with self.assertRaises(HostUnavailable):
                generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
            with self.assertRaisesRegex(ValueError,'预算上限'):
                generate_report(self.conn,self.view(other),other,'morning','2026-09-14',runner=runner)
        self.assertEqual(runner.call_count,1)

    def test_private_imports_are_not_sent_to_host(self):
        edition=self.edition()
        editorial.import_evidence_payload(self.conn,{'title':'私有材料',
            'content':'PRIVATE_LOCAL_SENTINEL_不外发','is_fixture':True,'facts':[]},
            edition_id=edition['edition_id'],privacy_class='private_local')
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',edition)
        self.assertNotIn('PRIVATE_LOCAL_SENTINEL',json.dumps(payload,ensure_ascii=False))

    def test_invalid_model_report_keeps_measured_usage_and_marks_failure(self):
        runner=Mock(return_value=HostResult({'unexpected':[]},{'input_tokens':123,'host_tasks':1},{'web_actions':0}))
        with self.assertRaises(ValueError):
            generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=runner)
        report=latest_report(self.conn,'default','morning','2026-09-14')
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['usage']['input_tokens'],123)

    def test_manual_adoption_stays_unverified_and_cannot_use_another_edition(self):
        self.save_article()
        edition=self.edition()
        report=generate_report(self.conn,self.view(),'default','product','2026-09-15',edition=edition,runner=self.runner)
        item=report['output']['items'][0]
        with self.assertRaises(ValueError):
            adopt_report_item(self.conn,'default',report['report_id'],item['item_id'],self.edition('517120')['edition_id'])
        adopted=adopt_report_item(self.conn,'default',report['report_id'],item['item_id'],edition['edition_id'])
        self.assertNotIn(adopted['event_id'],item['event_ids'])
        self.assertEqual(adopted['fact_ids'],[])
        self.assertIsNone(editorial.latest_decision(self.conn,edition['edition_id']))
        result=editorial.edition_detail(self.conn,edition['edition_id'])
        self.assertEqual(result['evidence'][0]['access_status'],'search_snippet_only')
        again=adopt_report_item(self.conn,'default',report['report_id'],item['item_id'],edition['edition_id'])
        self.assertEqual(adopted['evidence_ids'],again['evidence_ids'])


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.payload={'kind':'product','items':[],'selected_sources':[],
            'allow_web_search':True,'coverage_notes':[],
            'news_cutoff':'2026-09-14T23:59:59+08:00','window_start':'2026-09-12T00:00:00+08:00'}
        self.raw={'items':[{'title':'测试线索','summary':'有来源的合成摘要','why':'测试',
            'category':'technology','source_refs':['web_1']}],
            'web_sources':[{'id':'web_1','title':'合成来源','url':'https://example.org/a',
                'published_at':'2026-09-14','excerpt':'合成依据'}], 'coverage':[],'gaps':[]}
        self.trace={'web_actions':1,'visited_urls':[]}

    def validate(self):
        return validate_report(self.raw,self.payload,self.trace)

    def test_missing_web_trace_never_passes_new_sources_as_retrieved(self):
        self.trace['web_actions']=0
        self.assertEqual(self.validate()['items'],[])
        self.trace['web_actions']=1
        self.payload['allow_web_search']=False
        self.assertEqual(self.validate()['items'],[])

    def test_search_hint_is_distinct_from_opened_link_and_both_remain_unverified(self):
        first=self.validate()['items'][0]
        self.assertEqual(first['sources'][0]['evidence_level'],'host_search_hint')
        self.trace['visited_urls']=['https://example.org/a']
        self.assertEqual(self.validate()['items'][0]['sources'][0]['evidence_level'],'host_opened_url')
        self.assertFalse(first['fact_verified'])

    def test_unknown_and_intraday_date_precision_remain_pending(self):
        self.raw['web_sources'][0]['published_at']=None
        self.assertEqual(self.validate()['items'][0]['date_state'],'pending_time')
        self.raw['web_sources'][0]['published_at']='2026-09-14'
        self.payload['news_cutoff']='2026-09-14T09:00:00+08:00'
        self.assertEqual(self.validate()['items'][0]['date_state'],'pending_time')

    def test_wrong_date_or_missing_citation_drops_item_instead_of_backfill(self):
        for date in ('2026-09-15','2026-09-11'):
            self.raw['web_sources'][0]['published_at']=date
            self.assertEqual(self.validate()['items'],[])
        self.raw['web_sources'][0]['published_at']='2026-09-14'
        self.raw['items'][0]['source_refs']=['web_invented']
        self.assertEqual(self.validate()['items'],[])

    def test_more_than_fifteen_product_items_or_four_citations_is_rejected(self):
        self.raw['items']*=16
        with self.assertRaises(ValueError):
            self.validate()
        self.raw['items']=self.raw['items'][:1]
        self.raw['items'][0]['source_refs']=['web_1']*5
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_item_is_only_shown_once(self):
        self.raw['items']*=2
        self.assertEqual(len(self.validate()['items']),1)

    def test_unsafe_url_and_malformed_coverage_fail_cleanly(self):
        self.raw['web_sources'][0]['url']='http://127.0.0.1/private'
        with self.assertRaises(ValueError):
            self.validate()
        self.raw['web_sources'][0]['url']='https://example.org/a'
        self.raw['coverage']=[{'source_id':[], 'status':'found','note':''}]
        with self.assertRaises(ValueError):
            self.validate()


class FeedAndHostTests(unittest.TestCase):
    def test_feed_collector_failure_isolated_and_repeat_does_not_duplicate(self):
        from fastnews.custom_sources import ReaderAdapter
        from fastnews.pipeline import scan_morning
        from fastnews.network import NetworkError
        with tempfile.TemporaryDirectory() as directory:
            conn=connect(Path(directory)/'mock-network.db')
            try:
                base=load_config();sync_registry(conn,base)
                ids=[]
                for key in ('good','blocked'):
                    row=save_source(conn,base,'default',{'name':'合成订阅 '+key,'url':'https://example.org/'+key,
                        'collection':'feed','sections':['morning'],'categories':['technology']})
                    ids.append(row['source_id'])
                config=configured_view(conn,base);sync_registry(conn,config)
                class Client:
                    stats={}
                    def get(self,url,allowed_hosts):
                        if url.endswith('blocked'):
                            raise NetworkError('robots_disallow')
                        text='<rss><channel><item><title>合成订阅标题</title><link>https://example.org/article</link><pubDate>Mon, 14 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>'
                        return SimpleNamespace(url=url,text=text,body=text.encode(),retrieved_at='2026-09-14T01:00:00Z')
                adapter=ReaderAdapter(config,client=Client())
                first=scan_morning(conn,config,collection_date='2026-09-14',source_ids=ids,adapter=adapter)
                second=scan_morning(conn,config,collection_date='2026-09-14',source_ids=ids,adapter=adapter)
                self.assertEqual(first['articles_new'],1)
                self.assertEqual(second['articles_new'],0)
                failed=next(s for s in first['sources'] if s['source_id']==ids[1])
                self.assertEqual(failed['status'],'failed')
                self.assertEqual(failed['error_code'],'robots_disallow')
                self.assertEqual(first['usage']['llm_requests'],0)
            finally:
                conn.close()

    def feed(self, xml):
        result=SimpleNamespace(text=xml,body=xml.encode(),url='https://example.org/rss',retrieved_at='2026-09-15T00:00:00Z')
        return parse_feed('custom_test',result,time_bounds('2026-09-14')[0],time_bounds('2026-09-14')[1],10)

    def test_rss_has_real_publication_date_metadata_and_no_fulltext(self):
        parsed=self.feed('<rss><channel><item><title>测试标题</title><link>https://example.org/a</link>'
            '<pubDate>Mon, 14 Sep 2026 00:00:00 GMT</pubDate><description>列表摘要</description>'
            '<content>不读取的正文</content></item></channel></rss>')
        self.assertEqual(len(parsed.records),1)
        self.assertEqual(parsed.records[0]['lead'],'列表摘要')
        self.assertIsNone(parsed.records[0]['first_published_at'])
        self.assertFalse(parsed.complete)

    def test_atom_updated_is_never_substituted_for_unknown_publication(self):
        parsed=self.feed('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>测试</title>'
            '<link href="/b"/><updated>2026-09-14T00:00:00Z</updated><summary>摘要</summary></entry></feed>')
        self.assertIsNone(parsed.records[0]['report_published_at'])
        self.assertEqual(parsed.records[0]['url'],'https://example.org/b')

    def test_entities_unsafe_links_and_wrong_dates_do_not_enter_pool(self):
        with self.assertRaises(DiscoveryError):
            self.feed('<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///private">]><rss/>')
        parsed=self.feed('<rss><channel><item><title>测试</title><link>http://127.0.0.1/a</link></item>'
            '<item><title>旧闻</title><link>https://example.org/old</link><pubDate>Mon, 07 Sep 2026 00:00:00 GMT</pubDate></item></channel></rss>')
        self.assertEqual(parsed.records,[])

    def test_host_environment_never_inherits_api_keys(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-secret','OPENAI_API_KEY':'test-secret','PATH':'test-path'},clear=True):
            env=clean_environment()
        self.assertEqual(env,{'PATH':'test-path'})

    def test_codex_trace_records_actions_urls_and_actual_usage(self):
        events=[{'type':'item.completed','item':{'type':'web_search','action':{'type':'search','query':'test'}}},
            {'type':'item.completed','item':{'type':'web_search','action':{'type':'open_page','url':'https://example.org/a'}}},
            {'type':'turn.completed','usage':{'input_tokens':123,'output_tokens':45}}]
        parsed=parse_codex_output('\n'.join(json.dumps(e) for e in events),'{"items":[]}')
        self.assertEqual(parsed.trace['web_actions'],2)
        self.assertEqual(parsed.trace['visited_urls'],['https://example.org/a'])
        self.assertEqual(parsed.usage['input_tokens'],123)
        with self.assertRaises(HostUnavailable):
            parse_codex_output('{"type":"turn.failed"}','{}')

    def test_codex_telemetry_compatibility_does_not_relax_model_json_validation(self):
        telemetry='{"type":"item.completed","item":{"type":"web_search","query":null,"query":"test","action":{"type":"search"}}}'
        self.assertEqual(parse_codex_output(telemetry,'{}').trace['web_actions'],1)
        with self.assertRaises(ValueError):
            parse_codex_output(telemetry,'{"items":[],"items":[1]}')

    def test_completed_codex_turn_can_recover_intermediate_connection_errors(self):
        error=json.dumps({'type':'error','message':'Reconnecting... synthetic connection interruption'})
        complete=json.dumps({'type':'turn.completed','usage':{'input_tokens':123}})
        parsed=parse_codex_output(error+'\n'+complete,'{"items":[]}')
        self.assertTrue(parsed.trace['turn_completed'])
        self.assertEqual(parsed.trace['recovered_error_events'],1)
        self.assertEqual(parsed.usage['input_tokens'],123)
        for events in (error,complete+'\n'+error,error+'\n'+json.dumps({'type':'turn.failed'})+'\n'+complete):
            with self.assertRaises(HostUnavailable):parse_codex_output(events,'{}')
