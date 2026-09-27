"""Acceptance contracts use synthetic facts; live recall answers stay separate."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from fastnews import db, editorial as ed, writing as w, morning_reading as morning
from fastnews.config import load_config
from fastnews.source_catalog import configured_view
from fastnews.metadata import normalize_record, time_bounds, parse_instant
from fastnews.pipeline import store_record
from fastnews.reading_reports import build_input, validate_report, generate_report
from fastnews.host_provider import HostResult
from fastnews.task_control import TaskControl,TaskStopped
from fastnews.writing_checks import check_text
from fastnews.writing_quality import quality_checks
from fastnews.writing_background import add_candidate
from fastnews.product_context import public_context
from fastnews.host_evidence import SOURCE_EXTRA
from fastnews.writing_contracts import DRAFT_V64


def editorial_review(**changes):
    return dict(main_news_first='首段交代明确变化',independent_information='各段有独立事实',
        product_link='依据产品资料说明关系',repeated_mechanisms='未重复',generic_endings='无空泛收尾',
        product_manual_style='仅介绍本次相关特点',material_sufficient=True,issues=[],**changes)


class V64Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conn=db.connect(self.root/'fixture.db',mode='fixture');self.addCleanup(self.conn.close)
        self.config=load_config();db.sync_registry(self.conn,self.config)

    def view(self):
        return configured_view(self.conn,self.config)

    def article(self,day,title='合成跨行业政策',lead='公开的合成事实',sid='cls_morning',number=0):
        with self.conn:
            store_record(self.conn,normalize_record(dict(source_id=sid,title=title,
                url=f'https://www.cls.cn/detail/synthetic-{day}-{number}',lead=lead,
                report_published_at=day+'T07:00:00+08:00',is_fixture=True)),self.config,day+'T08:00:00+08:00')

    def test_seven_natural_days_and_first_day_boundary(self):
        for day in ['2026-09-07','2026-09-08','2026-09-14']:
            self.article(day)
        payload=build_input(self.conn,self.view(),'default','morning','2026-09-14')
        self.assertEqual(payload['window_start'],time_bounds('2026-09-08')[0].isoformat())
        self.assertEqual({s['published_at'][:10] for s in payload['items']},{'2026-09-08','2026-09-14'})
        self.assertEqual(payload['search_plan']['max_queries'],24)
        self.assertGreater(len(payload['selected_sources']),3)
        with self.assertRaises(ValueError):
            build_input(self.conn,self.view(),'default','morning','2026-09-14',lookback_days=True)

    def test_changed_window_has_distinct_cache_identity_and_source_failure_is_local(self):
        self.article('2026-09-14')
        def run(host,prompt,schema,**kwargs):
            payload=json.loads(prompt.split('INPUT_JSON:\n')[1])
            self.assertLessEqual(kwargs['timeout'],390)
            return HostResult(dict(items=[dict(title='合成消息',summary='取得的列表资料',why='研究背景',category='policy',
                source_refs=[payload['items'][0]['id']])],web_sources=[],coverage=[],gaps=['某来源访问失败'],
                timeline=[],domain_coverage=[]),{},dict(web_actions=0))
        one=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=run,lookback_days=1)
        seven=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=run)
        self.assertNotEqual(one['input_hash'],seven['input_hash'])
        self.assertEqual(len(seven['output']['items']),1)
        self.assertEqual(len(seven['output']['domain_coverage']),7)
        self.assertEqual(seven['status'],'partial')

    def test_timeline_cannot_invent_a_day_and_older_news_stays_out_of_today(self):
        self.article('2026-09-08');self.article('2026-09-14')
        p=build_input(self.conn,self.view(),'default','morning','2026-09-14')
        old=next(s for s in p['items'] if s['published_at'].startswith('2026-09-08'))
        now=next(s for s in p['items'] if s['published_at'].startswith('2026-09-14'))
        raw=dict(items=[dict(title='旧背景',summary='合成事实',category='policy',why='背景',source_refs=[old['id']])],
            web_sources=[],coverage=[],gaps=[],domain_coverage=[],
            timeline=[dict(theme='合成主题',development='ongoing',summary='两次明确进展',events=[
                dict(date='2026-09-08',event='初次计划',source_refs=[old['id']]),
                dict(date='2026-09-14',event='新的修订',source_refs=[now['id']])])])
        valid=validate_report(raw,p,{'web_actions':0})
        self.assertEqual(len(valid['timeline'][0]['events']),2)
        self.assertEqual(valid['items'],[])
        self.assertEqual(len(valid['validation_audit']['context_items']),1)
        raw['timeline'][0]['events'][0]['date']='2026-09-09'
        invalid=validate_report(raw,p,{'web_actions':0})
        self.assertEqual(invalid['timeline'],[])
        self.assertTrue(invalid['timeline_audit'])

    def test_explicit_runtime_experiment_has_separate_cache_identity(self):
        self.article('2026-09-14')
        calls=[]
        def run(host,prompt,schema,**kwargs):
            calls.append(kwargs)
            return HostResult(dict(items=[],web_sources=[],coverage=[],gaps=[],timeline=[],domain_coverage=[]),{},dict(web_actions=0))
        first=generate_report(self.conn,self.view(),'default','morning','2026-09-14',runner=run)
        cfg=self.view();cfg['_host_runtime']={'model':'gpt-6-sol','reasoning_effort':'low'}
        second=generate_report(self.conn,cfg,'default','morning','2026-09-14',runner=run)
        self.assertNotEqual(first['input_hash'],second['input_hash'])
        self.assertEqual(calls[-1]['model'],'gpt-6-sol')
        self.assertEqual(calls[-1]['reasoning_effort'],'low')
        self.assertEqual(len(calls),2)

    def test_imprecise_event_date_preserves_candidate_and_original_without_inventing_day(self):
        from fastnews.host_evidence import enrich_item
        raw={'event_identity':dict(subject='合成主体',action='披露',object='合成统计',phase='result',event_date='2026-08')}
        before=deepcopy(raw)
        item={'title':'合成统计公告','sources':[{'id':'s1','url':'https://example.org/public','published_at':'2026-09-14'}]}
        result=enrich_item(item,raw,{})
        self.assertIsNone(result['event_identity']['event_date'])
        self.assertEqual(result['event_date_audit']['reported'],'2026-08')
        self.assertTrue(result['missing_items'])
        self.assertEqual(raw,before)
        other=deepcopy(raw);other['event_identity']['event_date']='2026-07'
        earlier=enrich_item(deepcopy(item),other,{})
        self.assertNotEqual(earlier['research_identity'],result['research_identity'])

    def test_input_truncation_is_audited_and_does_not_drop_short_later_items(self):
        for n in range(60):
            self.article('2026-09-14',title='合成公告'+str(n),lead='合成长摘要。'*350,number=n)
        self.article('2026-09-14',title='合成简短跨行业事件',number=100)
        p=build_input(self.conn,self.view(),'default','morning','2026-09-14')
        self.assertLessEqual(len(json.dumps(p,ensure_ascii=False)),p['task_limits']['max_input_characters'])
        self.assertGreater(p['input_coverage']['omitted_count'],0)
        self.assertGreater(p['input_coverage']['excerpt_truncated_count'],0)
        self.assertTrue(any('未送入' in gap for gap in p['coverage_notes']))

    def test_product_research_does_not_repeat_drafting_only_references(self):
        edition=ed.create_edition(self.conn,self.config,product_code='588090',edition_date='2026-09-14',
            news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        context=dict(version='synthetic',references=[],writing_references=[{'excerpt':'合成重复资料'*1800}],
            excluded_from_writing=[],published_history=[],gaps=[])
        original=deepcopy(context)
        cfg=self.view()
        with patch('fastnews.product_context.public_context',return_value=context):
            payload=build_input(self.conn,cfg,'default','product','2026-09-14',edition)
        self.assertLess(len(json.dumps(payload,ensure_ascii=False)),24000)
        self.assertNotIn('writing_references',payload['product']['context'])
        self.assertEqual(context,original)
        from fastnews.source_access import source_constraints,url_policy
        full=source_constraints(cfg['reader_sources'])
        for source in full:
            self.assertEqual(url_policy(source['url'],full,source['source_id']),
                url_policy(source['url'],payload['source_constraints'],source['source_id']))

    def test_v64_names_trends_and_precise_amounts(self):
        for body in ['本基金截至2026年9月25日连续11日获资金增持。',
            '恒生科技ETF华泰柏瑞（513130）关注港股科技方向。',
            '华泰柏瑞南方东英恒生科技指数交易型开放式指数证券投资基金（QDII）',
            '产业规模100亿元，ETF成交额20亿元。']:
            self.assertEqual(check_text(body),[],body)
        for body in ['本基金截至9月25日连续11日获资金增持1亿元。','本基金总份额达到2亿份。',
                     '本基金跟踪恒生科技指数。','本基金跟踪HSTECH。','本基金跟踪代码000688。']:
            self.assertTrue(check_text(body),body)
        self.assertTrue(check_text('合成香港企业披露结果',entities=[dict(name='合成香港企业',market='HK')]))
        self.assertTrue(check_text('本基金连续11日获资金增持。'))
        self.assertEqual(check_text('南向资金净流入100亿元，本基金截至9月25日连续11日获资金增持。'),[])

    def test_local_event_budget_deferral_is_distinct_from_character_truncation(self):
        for number in range(16):self.article('2026-09-14',title=f'合成产业事件{number}',number=number)
        edition=ed.create_edition(self.conn,self.config,product_code='588090',edition_date='2026-09-14',
            news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        audit={}
        payload=build_input(self.conn,self.view(),'default','product','2026-09-14',edition,input_audit=audit)
        self.assertGreater(payload['input_coverage']['deferred_local_events_count'],0)
        self.assertEqual(payload['input_coverage']['deferred_local_events_count'],len(audit['deferred_local_events']))
        self.assertTrue(all(e['reason']=='local_event_input_budget' for e in audit['deferred_local_events']))

    def test_editorial_review_is_not_human_acceptance(self):
        raw=dict(status='ready',paragraphs=[{'text':'本段增加合成事实。'}],editorial_review=editorial_review())
        q=quality_checks(raw,1100,True)
        self.assertEqual(q['errors'],[])
        self.assertEqual(q['human_status'],'pending')
        self.assertIsNone(q['actual_edit_minutes'])
        self.assertTrue(quality_checks(raw,250,True)['errors'])
        raw['editorial_review']['issues']=['各段重复解释相同机制，缺少事实推进']
        self.assertEqual(quality_checks(raw,1100,True)['errors'][0]['code'],'editorial_review')
        raw['editorial_review']['issues']=[]
        raw['paragraphs'][0]['text']='跨市场推广的空间能否打开，仍取决于市场衔接。'
        self.assertTrue(quality_checks(raw,1100,True)['errors'])
        self.assertIsNone(quality_checks({},0,False)['model_review'])

    def test_skill_text_and_editorial_contract_are_actually_loaded(self):
        text=w.instructions('draft')
        self.assertIn('V6.4',text);self.assertIn('1—2步',text)
        self.assertIn('同一任务内',text)
        self.assertNotIn('允许指数名称和代码',text)
        self.assertIn('editorial_review',DRAFT_V64['required'])
        self.assertLessEqual(w.policy()['host_seconds'],390)

    def test_known_internal_caveats_fail_but_material_conditions_remain_allowed(self):
        raw=dict(status='ready',paragraphs=[{'text':'相关措施的具体实施情况仍应以正式发布为准。'},
            {'text':'展会案例不能据此推断展示项目属于基金持仓。'},
            {'text':'基金2026年3月更新的产品资料概要显示相关范围。'}],editorial_review=editorial_review())
        errors=quality_checks(raw,1100,True)['errors']
        self.assertEqual([e['code'] for e in errors],['editorial_internal_caveat','editorial_internal_caveat','editorial_product_provenance'])
        raw['paragraphs']=[{'text':'协议约定最高1亿美元首付款和近期里程碑付款，后续款项以满足里程碑条件为前提。申请获受理，尚未获批。政策提出将优化服务。'}]
        self.assertEqual(quality_checks(raw,1100,True)['errors'],[])

    def test_v64_source_rendering_deduplicates_document_and_keeps_audit_periods(self):
        refs=[dict(name='合成机构',title='\n  合成资料标题\n ',published_at='2026-09-14',
            url='https://example.org/source',data_period=period) for period in ['资料送出日期2026-09-14','本次资料日期2026-09-14']]
        content=dict(title='合成标题',paragraphs=[dict(text='原样正文。',sources=refs)])
        before=deepcopy(content)
        text=w.render_draft(content,compact_sources=True)
        self.assertEqual(text.count('https://example.org/source'),1)
        self.assertIn('合成机构《合成资料标题》，2026-09-14',text)
        self.assertNotIn('统计期',text)
        self.assertEqual(content,before)
        self.assertIn('统计期',w.render_draft(content))

    def test_full_draft_records_separate_checks_and_compact_sources(self):
        # Deliberately synthetic prose verifies the pipeline, not writing quality.
        text=('合成政策明确设备检验的范围和实施步骤，新增办理环节并公开申请安排。'
              '合成行业资料分别记录研发、试验和交付进度，各项进展对应独立原文。')*18
        self.assertTrue(950<=len(text)<=1250)
        source=dict(id='s1',material_id='m1',name='合成发布机构',title='合成政策与行业资料',
            url='https://example.org/original',text=text,text_observed=True,date_observed=True,published_at='2026-09-14')
        snapshot=dict(rules=w.policy(),product_code='588090',product_name='科创50ETF华泰柏瑞',
            product_codes=['588090'],news_cutoff='2026-09-14T23:59:59+08:00',sources=[source],
            materials=[dict(id='m1',must_include=True)])
        claim=dict(id='c1',material_id='m1',statement=text,quote=text,published_at='2026-09-14',
            data_period='',source_id='s1',usable=True)
        outline=dict(confirmed_text='第一段交代变化',entities=[],claims=[claim])
        raw=dict(status='ready',title='合成政策明确实施步骤',paragraphs=[dict(text=text,claim_ids=['c1'],
            material_ids=['m1'],outline_paragraph_id='p1',outline_anchor='第一段交代变化')],
            issues=[],entities=[],review={k:True for k in DRAFT_V64['properties']['review']['properties']},
            editorial_review=editorial_review())
        content,checks=w.validate_draft(raw,snapshot,outline)
        self.assertEqual(checks['factual']['errors'],[]);self.assertEqual(checks['policy']['errors'],[])
        self.assertEqual(checks['human_acceptance'],'pending');self.assertIsNone(checks['adoption_ready'])
        self.assertIn('合成发布机构《合成政策与行业资料》，2026-09-14',content['text'])
        self.assertNotIn('本期新闻截点',content['text'])
        raw['paragraphs'][0]['text']+='本基金总份额2亿份。'
        self.assertTrue(w.validate_draft(raw,snapshot,outline)[1]['policy']['errors'])

    def test_budget_deduplicates_backups_but_counts_other_live_databases(self):
        import sqlite3
        from fastnews import host_task_budget
        folder=self.root/'data';folder.mkdir()
        connections=[]
        for name,ids in [('formal.db',['a']),('backup.db',['a']),('backtest.db',['b'])]:
            con=sqlite3.connect(folder/name);connections.append(con);self.addCleanup(con.close)
            con.executescript("CREATE TABLE fn_meta(key TEXT,value TEXT); INSERT INTO fn_meta VALUES('mode','live'); CREATE TABLE fn_reading_reports(report_id TEXT,created_at TEXT);")
            con.executemany('INSERT INTO fn_reading_reports VALUES(?,?)',[(i,'2026-09-14T10:00:00+08:00') for i in ids]);con.commit()
        with patch.object(host_task_budget,'ROOT',self.root):
            self.assertEqual(host_task_budget.attempts(connections[0],*time_bounds('2026-09-14')),2)

    def test_budget_reservation_excludes_other_database_and_releases(self):
        from fastnews import host_task_budget
        (self.root/'data').mkdir()
        a=db.connect(self.root/'data/a.db',mode='live');self.addCleanup(a.close)
        b=db.connect(self.root/'data/b.db',mode='live');self.addCleanup(b.close)
        with patch.object(host_task_budget,'ROOT',self.root):
            with host_task_budget.reservation(a):
                with self.assertRaisesRegex(ValueError,'共享预算'):
                    with host_task_budget.reservation(b):pass
            with host_task_budget.reservation(b):pass

    def test_old_quarterly_identity_cannot_hide_current_legal_document(self):
        for title,asof,published in [('合成产品资料概要更新','2026-03-18','2026-03-18'),
                                    ('合成产品：交易所定期报告产品概况','2026-06-30','2026-07-21')]:
            payload=dict(is_fixture=True,title=title,content='合成产品投资范围。',excerpt='合成产品投资范围。',
                snapshot_type='product_identity',data_as_of=asof,published_at=published,
                source_url='https://example.org/legal/'+published,source_type='original_publication')
            path=self.root/(published+'.json');path.write_text(json.dumps(payload),encoding='utf-8')
            ed.import_product_data(self.conn,path,authorized=True,product_code='588090',privacy_class='public')
        edition=ed.create_edition(self.conn,self.config,product_code='588090',edition_date='2026-09-17',
            news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
        context=public_context(self.conn,edition)
        self.assertIn('定期报告',context['references'][0]['title'])
        self.assertIn('资料概要',context['writing_references'][0]['title'])
        self.assertEqual(context['writing_references'][0]['data_as_of'],'2026-03-18')
        self.assertTrue(context['excluded_from_writing'])

    def test_one_deadline_includes_collection_and_reserves_save_time(self):
        with patch('fastnews.task_control.time.monotonic',return_value=100):
            control=TaskControl(480)
        with patch('fastnews.task_control.time.monotonic',return_value=160):
            self.assertEqual(control.host_timeout(390,30),390)
        with patch('fastnews.task_control.time.monotonic',return_value=500):
            self.assertEqual(control.host_timeout(390,30),50)
        control.cancelled.set()
        with self.assertRaises(TaskStopped):control.host_timeout(390,30)


if __name__=='__main__':
    unittest.main()
