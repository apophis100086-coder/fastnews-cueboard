"""Targeted search contracts, evidence identity and immutable human revisions."""
from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from fastnews import editorial as ed
from fastnews.host_provider import HostResult, HostUnavailable, parse_codex_output
from fastnews.host_evidence import SOURCE_EXTRA, merge_materials
from fastnews.reading_reports import (validate_report, build_input, generate_report,
    latest_report, adopt_report_item)
from fastnews.search_plan import build_search_plan
from fastnews.source_catalog import configured_view, save_source, save_profile, section_ids
from fastnews.source_access import url_policy
from fastnews.writing_background import add_candidate, pool_items
from tests import test_writing_workflow as fixtures


def source(url='https://www.fda.gov/drugs/public-test',kind='page_fragment',sid='fda',text='Synthetic public evidence.'):
    return {'id':'web_1','title':'Synthetic public release','url':url,
        'published_at':'2026-09-14','excerpt':text,
        **{k:None for k in SOURCE_EXTRA},'source_id':sid,'material_kind':kind,
        'tool_ref':'turn1view0','first_published_at':'2026-09-14'}


def report_raw(src=None, **identity):
    src=src or source()
    return {'items':[{'title':'合成药物临床公告','summary':'公司披露了合成试验进展。','why':'研究阶段需核对',
        'category':'medicine','source_refs':[src['id']],
        'event_identity':{'subject':'Synthetic Company','action':'reports','object':'Trial A',
                          'phase':'announcement','event_date':'2026-09-14',**identity},
        'claims':[{'statement':'合成药物公告了研究进展','kind':'company_statement',
                   'source_ref':src['id'],'quote':src['excerpt']}]}],
        'web_sources':[src],'coverage':[],'gaps':[]}


class TargetedSearchTests(TestCase):
    setUp=fixtures.WritingWorkflowTests.setUp
    make_edition=fixtures.WritingWorkflowTests.make_edition
    view=fixtures.WritingWorkflowTests.view
    save=fixtures.WritingWorkflowTests.save
    export=fixtures.WritingWorkflowTests.export
    note=fixtures.WritingWorkflowTests.note

    def payload(self):
        return build_input(self.conn,self.view(),'default','product','2026-09-15',self.edition)

    def validate(self,raw,trace=None):
        return validate_report(raw,self.payload(),trace or {'web_actions':1,'visited_urls':[]})

    def runner(self,raw,trace=None):
        return Mock(return_value=HostResult(raw,{'is_fixture':True},trace or {'web_actions':1,'visited_urls':[]}))

    def generate(self,raw,word,trace=None):
        return generate_report(self.conn,self.view(),'default','product','2026-09-15',
            edition=self.edition,runner=self.runner(raw,trace),extra_keywords=[word])

    def test_public_technical_failure_routes_to_product_but_not_morning(self):
        view=self.view()
        self.assertEqual(len(section_ids(view,'all',collect_only=True)),13)
        targets={s['source_id']:s for s in build_search_plan(view,'588710',['用户补充词'])['selected_sources']}
        self.assertIn('csia',targets)
        self.assertFalse(targets['csia']['program_scan_enabled'])
        self.assertIn('设备订单',targets['csia']['fact_question'])
        self.assertIn('客户导入',targets['csia']['fact_question'])
        self.assertNotIn('fda',targets)
        self.assertNotIn('用户补充词',targets['csia']['fact_question'])
        fda=next(s for s in build_search_plan(view,'517120')['selected_sources'] if s['source_id']=='fda')
        self.assertTrue(fda['host_read_allowed'])

    def test_explicit_disable_survives_edit_without_changing_program_capability(self):
        save_source(self.conn,self.config,'default',{'source_id':'miit','search_enabled':False})
        saved=save_source(self.conn,self.config,'default',{'source_id':'miit','name':'工信部参考'})
        self.assertFalse(saved['search_enabled'])
        self.assertTrue(saved['program_scan_enabled'])
        self.assertEqual(saved['search_disabled_reason'],'user_search_disabled')
        restricted=save_source(self.conn,self.config,'default',{'source_id':'stcn_egs_morning','search_enabled':True,'host_read_allowed':True})
        self.assertFalse(restricted['search_enabled'])
        for sid in ('fda','csia'):
            saved=save_source(self.conn,self.config,'default',{'source_id':sid,'name':sid})
            self.assertTrue(saved['search_enabled'])

    def test_global_off_stops_collection_and_host_web(self):
        from fastnews.product_discovery import refresh_fixed_sources
        save_profile(self.conn,{'profile_id':'default','name':'默认方案','web_search':False})
        view=self.view()
        self.assertEqual(build_search_plan(view,'517120')['queries'],[])
        adapter=Mock()
        result=refresh_fixed_sources(self.conn,view,'513130',self.edition['news_cutoff'],Mock(),mode='fixture',adapter=adapter)
        self.assertEqual(result['status'],'network_disabled')
        adapter.discover.assert_not_called()
        runner=self.runner(report_raw())
        result=generate_report(self.conn,view,'default','product','2026-09-15',edition=self.edition,runner=runner)
        self.assertFalse(runner.call_args.kwargs['web_search'])
        self.assertEqual(result['output']['items'],[])

    def test_custom_sources_survive_repeated_configured_view(self):
        custom=save_source(self.conn,self.config,'default',{'name':'公开测试来源','url':'https://example.org/public',
            'collection':'link','categories':['technology'],'sections':['sector']})
        view=configured_view(self.conn,self.view())
        self.assertEqual(sum(s['source_id']==custom['source_id'] for s in view['reader_sources']),1)

    def test_unknown_scope_stays_pending_and_claimed_source_cannot_change_domain(self):
        plan=build_search_plan(self.view(),'517120')
        self.assertIn('medcube',{s['source_id'] for s in plan['needs_confirmation']})
        constraints=self.payload()['source_constraints']
        self.assertFalse(url_policy('https://other.example.org/x',constraints,'fda')['search_enabled'])

    def test_open_action_is_not_proof_of_returned_page_text(self):
        src=source()
        trace={'web_actions':1,'visited_urls':[src['url']],
            'actions':[{'action_id':'item_1','type':'open_page','urls':[src['url']]}]}
        item=self.validate(report_raw(src),trace)['items'][0]
        self.assertEqual(item['sources'][0]['material_identity'],'host_report_pending')
        self.assertFalse(item['sources'][0]['source_excerpt_verified'])
        self.assertFalse(item['sources'][0]['fulltext_read'])
        self.assertEqual(item['claims'][0]['support_state'],'host_report_pending')
        self.assertEqual(item['sources'][0]['observed_action_ids'],['item_1'])

    def test_only_matching_adapter_excerpt_locates_quote_without_verifying_fact(self):
        src=source()
        trace={'web_actions':1,'tool_excerpts':[{'url':src['url'],'text':src['excerpt'],
            'kind':'page_fragment','tool_ref':'actual_tool_reference'}]}
        item=self.validate(report_raw(src),trace)['items'][0]
        self.assertTrue(item['sources'][0]['source_excerpt_verified'])
        self.assertEqual(item['sources'][0]['tool_ref'],'actual_tool_reference')
        self.assertEqual(item['claims'][0]['support_state'],'excerpt_located_statement_pending')
        self.assertFalse(item['claims'][0]['fact_verified'])
        raw=report_raw(src)
        raw['items'][0]['claims'][0]['quote']='Made up different quotation'
        self.assertEqual(self.validate(raw,trace)['items'][0]['claims'][0]['support_state'],'unsupported_quote')

    def test_restricted_body_is_removed_and_not_laundered_as_snippet(self):
        raw=report_raw(source('https://www.cde.org.cn/test','page_fragment','cde'))
        item=self.validate(raw)['items'][0]
        self.assertEqual(item['sources'][0]['material_identity'],'not_obtained')
        self.assertEqual(item['sources'][0]['excerpt'],'')
        self.assertEqual(item['claims'][0]['quote'],'')
        raw['web_sources'][0]['material_kind']='search_snippet'
        self.assertEqual(self.validate(raw)['items'][0]['sources'][0]['material_identity'],'host_search_hint')
        save_source(self.conn,self.config,'default',{'source_id':'cde','enabled':False})
        self.assertEqual(self.validate(raw)['items'],[])

    def test_dates_phase_and_statistical_period_are_distinct(self):
        a=self.validate(report_raw())['items'][0]
        b=self.validate(report_raw(phase='implementation'))['items'][0]
        c=self.validate(report_raw(event_date='2026-09-13'))['items'][0]
        period=source()
        period.update(data_period_start='2026-08-01',data_period_end='2026-08-31')
        d=self.validate(report_raw(period))['items'][0]
        self.assertEqual(len({i['research_identity'] for i in (a,b,c,d)}),4)
        period.update(data_period_start='2026-09-15')
        with self.assertRaises(ValueError):
            self.validate(report_raw(period))
        future=source()
        future['first_published_at']='2026-09-15'
        self.assertEqual(self.validate(report_raw(future))['items'],[])
        future['first_published_at']='2026-09-14'
        future['effective_at']='2026-10-01'
        self.assertEqual(len(self.validate(report_raw(future,phase='plan'))['items']),1)

    def test_duplicate_event_with_new_source_merges_in_one_report(self):
        raw=report_raw()
        second=source('https://example.org/public-source',sid=None)
        second['id']='web_2'
        raw['web_sources'].append(second)
        another=deepcopy(raw['items'][0])
        another.update(title='另一标题：同一合成公告',summary='同一事件的转述',source_refs=['web_2'])
        another['claims'][0]['source_ref']='web_2'
        raw['items'].append(another)
        output=self.validate(raw)
        self.assertEqual(len(output['items']),1)
        self.assertEqual(len(output['items'][0]['sources']),2)

    def test_identical_summary_does_not_merge_distinct_event_phases(self):
        raw=report_raw()
        another=deepcopy(raw['items'][0])
        another['event_identity']['phase']='implementation'
        raw['items'].append(another)
        self.assertEqual(len(self.validate(raw)['items']),2)

    def test_evidence_budget_is_configurable_and_rejects_boolean_or_excess(self):
        import yaml
        from fastnews import search_plan
        value=yaml.safe_load(search_plan.PATH.read_text(encoding='utf-8'))
        test_path=self.root/'search.yaml'
        for budget in ({'max_events':True,'max_pages':20,'max_pages_per_event':2},
                       {'max_events':7,'max_pages':20,'max_pages_per_event':2},
                       {'max_events':3,'max_pages':21,'max_pages_per_event':4},
                       {'max_events':3,'max_pages':20,'max_pages_per_event':5}):
            value['evidence']=budget
            test_path.write_text(yaml.safe_dump(value,allow_unicode=True),encoding='utf-8')
            with patch.object(search_plan,'PATH',test_path),self.assertRaises(ValueError):
                search_plan.search_policy()
        value['evidence']={'max_events':1,'max_pages':1,'max_pages_per_event':1}
        test_path.write_text(yaml.safe_dump(value,allow_unicode=True),encoding='utf-8')
        with patch.object(search_plan,'PATH',test_path):
            self.assertEqual(build_search_plan(self.view(),'517120')['evidence_budget']['max_events'],1)

    def test_merge_appends_evidence_and_preserves_old_decision_and_export(self):
        first=self.generate(report_raw(),'第一轮')
        event=adopt_report_item(self.conn,'default',first['report_id'],'item_1',self.eid)['event_id']
        self.save([event],[self.note()])
        old,old_json,old_md=self.export()
        old_decision=ed.latest_decision(self.conn,self.eid)
        next_source=source('https://example.org/another-public-source',sid=None,text='Additional public evidence.')
        newer=self.generate(report_raw(next_source),'第二轮')
        metrics=newer['output']['incremental_results']
        self.assertEqual(metrics['new_event_candidates'],0)
        self.assertEqual(metrics['new_source_urls'],1)
        result=adopt_report_item(self.conn,'default',newer['report_id'],'item_1',self.eid)
        self.assertEqual(result['event_id'],event)
        self.assertTrue(result['merged_existing_candidate'])
        pool=pool_items(self.conn,self.config,self.eid)
        self.assertEqual(len(pool),1)
        self.assertEqual(len(pool[0]['sources']),2)
        self.assertEqual(ed.latest_decision(self.conn,self.eid),old_decision)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0],0)
        self.assertEqual(self.export()[1],old_json)
        self.assertEqual(self.export()[2],old_md)
        self.save([event],[self.note()],revision=1)
        _,new_json,new_md=self.export(2)
        self.assertIn('Additional public evidence.',new_md)
        self.assertIn('宿主报告待复核',new_md)
        self.assertIn('2026-09-14',new_md)
        self.assertIn('Additional public evidence.',json.dumps(new_json,ensure_ascii=False))
        self.assertNotEqual(new_md,old_md)

    def test_unavailable_material_cannot_replace_better_existing_excerpt(self):
        old=self.validate(report_raw())['items'][0]
        missing=self.validate(report_raw(source(kind='unavailable')))['items'][0]
        merged=merge_materials(old,missing)
        self.assertEqual(merged['sources'][0]['excerpt'],'Synthetic public evidence.')
        self.assertEqual(merged['summary'],old['summary'])
        self.assertEqual(len(merged['sources']),2)

    def test_product_relation_survives_adoption_and_incremental_merge_without_changing_choice(self):
        payload=self.payload()
        reference=dict(id='snapshot_one',source_url='https://example.org/product',published_at='2026-01-01',
                       data_as_of='2025-12-31',excerpt='合成产品范围。')
        payload['product']['context']={'references':[reference]}
        raw=report_raw();raw['items'][0]['source_refs'].append(reference['id'])
        raw['items'][0]['claims'].append(dict(statement='合成产品关系',kind='reported',source_ref=reference['id'],quote=reference['excerpt']))
        with patch('fastnews.reading_reports.build_input',return_value=payload):
            first=self.generate(raw,'产品依据回放')
        event=adopt_report_item(self.conn,'default',first['report_id'],'item_1',self.eid)['event_id']
        before=pool_items(self.conn,self.config,self.eid)[0]
        self.assertEqual(before['product_relationship']['references'][0]['id'],'snapshot_one')
        self.save([event],[self.note()]);choice=ed.latest_decision(self.conn,self.eid)
        reference=dict(reference,id='snapshot_two');payload['product']['context']['references']=[reference]
        raw['items'][0]['source_refs'][-1]=reference['id'];raw['items'][0]['claims'][-1]['source_ref']=reference['id']
        with patch('fastnews.reading_reports.build_input',return_value=payload):
            newer=self.generate(raw,'增加产品依据')
        adopt_report_item(self.conn,'default',newer['report_id'],'item_1',self.eid)
        current=pool_items(self.conn,self.config,self.eid)[0]
        self.assertEqual(len(current['product_relationship']['references']),2)
        self.assertEqual(len(current['sources']),1)
        self.assertEqual(ed.latest_decision(self.conn,self.eid),choice)
        self.assertEqual(merge_materials(current,current)['product_relationship'],current['product_relationship'])
        self.assertEqual(len(before['product_relationship']['references']),1)

    def test_empty_or_failed_refresh_preserves_candidate_draft_and_previous_report(self):
        first=self.generate(report_raw(),'首次检索')
        event=adopt_report_item(self.conn,'default',first['report_id'],'item_1',self.eid)['event_id']
        self.save([event],[self.note()])
        before=pool_items(self.conn,self.config,self.eid)
        decision=ed.latest_decision(self.conn,self.eid)
        empty=self.generate({'items':[],'web_sources':[],'coverage':[],'gaps':['没有新增消息']},'无新增')
        self.assertEqual(empty['output']['items'],[])
        self.assertEqual(empty['output']['incremental_results']['new_event_candidates'],0)
        failed=Mock(side_effect=HostUnavailable('synthetic timeout or quota'))
        with self.assertRaises(HostUnavailable):
            generate_report(self.conn,self.view(),'default','product','2026-09-15',
                edition=self.edition,runner=failed,extra_keywords=['失败尝试'])
        record=latest_report(self.conn,'default','product','2026-09-15',self.eid)
        self.assertEqual(record['status'],'failed')
        self.assertIsNone(record['usage']['input_tokens'])
        self.assertEqual(record['usage']['host_tasks_attempted'],1)
        self.assertIsNotNone(record['previous_report'])
        self.assertEqual(pool_items(self.conn,self.config,self.eid),before)
        self.assertEqual(ed.latest_decision(self.conn,self.eid),decision)

    def test_trace_counts_completed_actions_without_inventing_content(self):
        output='\n'.join(json.dumps(e) for e in [
            {'type':'item.started','item':{'type':'web_search','id':'item_1'}},
            {'type':'item.completed','item':{'type':'web_search','id':'item_1','action':{'type':'search','query':'test'}}},
            {'type':'item.completed','item':{'type':'web_search','id':'item_2','action':{'type':'open_page','url':'https://example.org/page'}}},
            {'type':'turn.completed','usage':{'input_tokens':123}}])
        result=parse_codex_output(output,json.dumps({'items':[]}))
        self.assertEqual(result.trace['web_actions'],2)
        self.assertEqual(result.trace['visited_urls'],['https://example.org/page'])
        self.assertEqual(result.trace['tool_excerpts'],[])
        self.assertEqual(result.usage['input_tokens'],123)

    def test_reused_public_fragments_do_not_invalidate_their_own_cache(self):
        from fastnews import db
        conn=db.connect(self.root/'empty.fixture.db',mode='fixture')
        self.addCleanup(conn.close)
        db.sync_registry(conn,self.config)
        edition=ed.create_edition(conn,self.config,product_code='517120',edition_date='2026-09-15',
            news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        config=configured_view(conn,self.config)
        runner=self.runner(report_raw())
        first=generate_report(conn,config,'default','product','2026-09-15',edition=edition,runner=runner)
        payload=build_input(conn,config,'default','product','2026-09-15',edition)
        self.assertTrue(payload['known_events'])
        self.assertTrue(any(s.get('origin')=='same_edition_reuse' for s in payload['items']))
        second=generate_report(conn,config,'default','product','2026-09-15',edition=edition,runner=runner)
        self.assertEqual(first['report_id'],second['report_id'])
        runner.assert_called_once()

    def test_explicit_evidence_merge_to_existing_metadata_candidate_preserves_title(self):
        payload=self.payload()
        original=payload['items'][0]
        event=original['event_id']
        add_candidate(self.conn,self.view(),self.eid,event)
        old=pool_items(self.conn,self.view(),self.eid)[0]
        raw=report_raw()
        raw['items'][0]['title']=original['title']
        raw['items'][0]['source_refs'].append(original['id'])
        report=self.generate(raw,'补齐原线索')
        self.assertEqual(report['output']['items'][0]['local_event_id'],event)
        result=adopt_report_item(self.conn,'default',report['report_id'],'item_1',self.eid)
        self.assertTrue(result['merged_existing_candidate'])
        pool=pool_items(self.conn,self.view(),self.eid)
        self.assertEqual(len(pool),1)
        self.assertEqual(pool[0]['title'],old['title'])
        self.assertTrue(pool[0]['claims'])

    def test_frozen_legacy_background_keeps_the_original_markdown_bytes(self):
        from fastnews.writing_background import markdown_materials
        fixtures_path=Path(__file__).with_name('fixtures')
        legacy=json.loads((fixtures_path/'legacy_writing_materials.json').read_text(encoding='utf-8'))
        rendered='\n'.join(markdown_materials(legacy)).encode('utf-8')
        self.assertEqual(rendered,(fixtures_path/'legacy_writing_materials.md').read_bytes())
