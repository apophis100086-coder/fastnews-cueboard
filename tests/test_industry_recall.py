"""Synthetic regression checks for omissions, source limits and chronology."""
from unittest import TestCase
from unittest.mock import Mock

from fastnews.host_provider import HostResult
from fastnews.reading_reports import build_input, validate_report, generate_report, adopt_report_item
from fastnews.search_audit import audit_search
from fastnews.search_plan import build_search_plan
from fastnews.source_catalog import save_source, section_ids
from tests.test_targeted_search import source, report_raw
from tests import test_writing_workflow as fixtures


def trace(*queries):
    return {'web_actions':1, 'visited_urls':[], 'actions':[
        {'type':'search', 'status':'completed', 'queries':list(queries)}]}


class IndustryRecallTests(TestCase):
    make_edition = fixtures.WritingWorkflowTests.make_edition
    view = fixtures.WritingWorkflowTests.view
    save = fixtures.WritingWorkflowTests.save
    export = fixtures.WritingWorkflowTests.export

    def setUp(self):
        fixtures.WritingWorkflowTests.setUp(self)
        self.edition = self.make_edition('588090')
        self.eid = self.edition['edition_id']

    def payload(self):
        return build_input(self.conn, self.view(), 'default', 'product', '2026-09-15', self.edition)

    def background(self, **changes):
        s = source('https://www.semi.org.cn/site/semi/article/synthetic.html', sid='semi_china_public')
        s.update(published_at='2026-09-03', first_published_at='2026-09-03',
                 data_period_start='2026-04-01', data_period_end='2026-06-30', **changes)
        return s

    def test_short_separate_site_queries_and_priority_axes_with_fallback_room(self):
        p = build_search_plan(self.view(), '588090', ['用户补充'])
        self.assertEqual(len(p['observation_queries']), 6)
        self.assertEqual(p['queries'][0]['lane'], 'observation')
        self.assertLessEqual(len(p['queries']) + p['fallback_queries'], p['max_queries'])
        for q in p['queries']:
            if q['domains']:
                self.assertEqual(len(q['domains']), 1)
                self.assertNotIn(' OR ', q['query'])
                self.assertNotIn('用户补充', q['query'])
                self.assertLess(len(q['query']), 110)

    def test_research_sources_are_not_automatic_and_disable_survives_plan(self):
        view = self.view()
        self.assertEqual(len(section_ids(view, 'all', collect_only=True)), 13)
        save_source(self.conn, self.config, 'default', {'source_id':'nbs_public', 'search_enabled':False})
        p = build_search_plan(self.view(), '588090')
        self.assertFalse(any('stats.gov.cn' in q['query'] for q in p['queries']))
        self.assertEqual(p['observation_queries'][0]['status'], 'source_disabled')

    def test_shared_or_queries_and_url_opens_do_not_establish_individual_coverage(self):
        p = self.payload()
        audit = audit_search(p, trace('(site:stats.gov.cn OR site:semi.org.cn) 集成电路',
                                     'https://www.stats.gov.cn/sj/zxfb/'))
        self.assertEqual(audit['sources']['nbs_public']['query_status'], 'shared_query_only')
        self.assertEqual(audit['observed_query_count'], 1)
        # The broad direction was queried; a source still has no independent check.
        self.assertEqual(audit['directions'][0]['status'], 'queried')
        audit = audit_search(p, trace('site:stats.gov.cn.evil.example 集成电路'))
        self.assertEqual(audit['sources']['nbs_public']['query_status'], 'no_query_observed')

    def test_axes_require_relevant_queries_and_do_not_infer_finding(self):
        p = self.payload()
        audit = audit_search(p, trace('site:stats.gov.cn 2026 集成电路 产量',
                                     'site:semi.org.cn 2026 半导体设备 出货'))
        self.assertEqual([a['status'] for a in audit['directions'][:3]], ['queried','queried','no_query_observed'])
        raw = report_raw()
        raw['coverage'] = [{'source_id':'csia','status':'not_found','note':'合成无结果'}]
        result = validate_report(raw, p, trace('site:stats.gov.cn 集成电路'))
        row = next(s for s in result['coverage'] if s['source_id']=='csia')
        self.assertEqual(row['status'], 'not_checked')
        self.assertEqual(row['host_reported_status'], 'not_found')

    def test_actual_query_overrun_is_visible(self):
        result = audit_search(self.payload(), trace(*['site:stats.gov.cn 集成电路'] * 25))
        self.assertEqual(result['observed_query_count'], 25)
        self.assertTrue(result['budget_exceeded'])

    def test_cli_batch_summary_and_find_pattern_are_not_extra_queries(self):
        t = trace('site:stats.gov.cn 集成电路', 'site:semi.org.cn 出货')
        t['actions'][0]['query'] = 'site:stats.gov.cn 集成电路 ...'
        t['actions'].append({'type':'other','query':"'集成电路'"})
        self.assertEqual(audit_search(self.payload(), t)['observed_query_count'], 2)

    def test_background_keeps_publication_and_period_and_remains_unverified(self):
        output = validate_report(report_raw(self.background()), self.payload(), trace('site:semi.org.cn 出货'))
        item = output['items'][0]
        self.assertEqual(item['time_role'], 'background')
        self.assertEqual(item['date_state'], 'background_before_window')
        self.assertTrue(item['title'].startswith('背景｜'))
        self.assertEqual(item['sources'][0]['published_at'], '2026-09-03')
        self.assertEqual(item['sources'][0]['data_period_end'], '2026-06-30')
        self.assertFalse(item['fact_verified'])
        self.assertFalse(item['sources'][0]['source_excerpt_verified'])

    def test_background_still_rejects_false_source_identity_and_future_publication(self):
        for changes in ({'url':'https://example.org/old'}, {'first_published_at':'2026-09-16'},
                        {'published_at':'2026-09-16'}):
            s = self.background(); s.update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(validate_report(report_raw(s), self.payload(), trace('site:semi.org.cn 出货'))['items'], [])

    def test_forecast_period_can_be_future_but_publication_cannot(self):
        s = self.background()
        s.update(data_period_start='2027-01-01',data_period_end='2027-12-31',effective_at='2027-01-01')
        item = validate_report(report_raw(s,phase='plan'), self.payload(), trace('site:semi.org.cn 存储'))['items'][0]
        self.assertEqual(item['event_identity']['phase'], 'plan')
        self.assertEqual(item['sources'][0]['data_period_end'], '2027-12-31')

    def test_current_restatement_does_not_refresh_old_statistics(self):
        s = self.background();s['published_at']='2026-09-14'
        item = validate_report(report_raw(s), self.payload(), trace('site:semi.org.cn 存储'))['items'][0]
        self.assertEqual(item['time_role'], 'background')
        self.assertEqual(item['sources'][0]['published_at'], '2026-09-14')
        self.assertEqual(item['sources'][0]['first_published_at'], '2026-09-03')

    def test_background_budget_and_mixed_chronology(self):
        raw = {'items':[], 'web_sources':[], 'coverage':[], 'gaps':[]}
        for n in range(6):
            s = self.background(); s.update(id='web_'+str(n),url=f'https://www.semi.org.cn/site/semi/article/test{n}.html')
            one = report_raw(s,object='Synthetic series '+str(n))
            raw['items'] += one['items']; raw['web_sources'] += one['web_sources']
        result = validate_report(raw, self.payload(), trace('site:semi.org.cn 出货'))
        self.assertEqual(len(result['items']), 6)
        newer = source('https://www.stats.gov.cn/sj/zxfb/synthetic.html',sid='nbs_public')
        newer['id']='web_recent'
        raw = report_raw(self.background());raw['web_sources'].append(newer)
        raw['items'][0]['source_refs'].append('web_recent')
        item = validate_report(raw, self.payload(), trace('site:stats.gov.cn 集成电路'))['items'][0]
        self.assertEqual(item['time_role'], 'news_with_background')

    def test_new_background_metadata_survives_manual_adoption_and_export(self):
        raw = report_raw(self.background())
        runner = Mock(return_value=HostResult(raw, {'is_fixture':True}, trace('site:semi.org.cn 出货')))
        r = generate_report(self.conn, self.view(), 'default', 'product', '2026-09-15', edition=self.edition, runner=runner)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_decisions').fetchone()[0], 0)
        event = adopt_report_item(self.conn, 'default', r['report_id'], 'item_1', self.eid)['event_id']
        self.save([event])
        _, exported, md = self.export()
        self.assertIn('早于新闻窗口的统计 / 预测背景', md)
        self.assertIn('2026-09-03', md)
        self.assertIn('2026-06-30', md)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0], 0)
