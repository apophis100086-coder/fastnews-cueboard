"""Direct generation authorization, evidence boundaries, time and version safety."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from fastnews import writing as w,writing_direct as direct
from fastnews.host_provider import HostResult
from fastnews.task_control import TaskControl,TaskStopped
from fastnews.writing_contracts import NEWS_EDIT
from tests import test_news_writing as fixtures


class DirectWritingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NewsWritingTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.conn=self.fixture.conn
        self.cfg=dict(self.fixture.cfg,_reader_profile={'settings':{'host':'workbuddy','web_search':False}})
        self.calls=[]

    def runner(self,host,prompt,schema,**kwargs):
        payload=json.loads(prompt.split('INPUT_JSON:\n')[1]);self.calls.append((payload,kwargs))
        sid=payload['news_sources'][0]['source_id'];statement=self.fixture.statement
        raw=dict(status='ready',title='设备订单增长',paragraphs=[dict(text=statement,source_ids=[sid])],issues=[])
        if payload['stage']=='edit':raw['changes']=['删除重复解释']
        if payload['stage']=='audit':
            raw=dict(evidence=[dict(paragraph_index=1,source_id=sid,statement=statement,quote=statement,kind='reported',data_period='',supported=True,reason='')],
                entities=[],review={k:True for k in NEWS_EDIT['properties']['review']['properties']},
                editorial_review={k:[] if k=='issues' else True if k=='material_sufficient' else '合成检查' for k in NEWS_EDIT['properties']['editorial_review']['properties']},)
        return HostResult(raw,dict(input_tokens=20,output_tokens=10,model_requested=kwargs.get('model'),reasoning_effort_requested=kwargs.get('reasoning_effort')), {})

    def run_direct(self,runner=None,**data):
        control=TaskControl(900)
        run=w.accept_run(self.conn,self.cfg,'generate',self.fixture.data(**data),control)
        self.run=run
        return w.execute_run(self.conn,run['run_id'],control,runner=runner or self.runner)

    def test_direct_requires_selection_but_no_outline_and_keeps_two_actual_versions(self):
        before=w.ed.latest_decision(self.conn,self.fixture.eid)
        result=self.run_direct()
        self.assertEqual([c[0]['stage'] for c in self.calls],['compose','edit','audit'])
        self.assertEqual(self.run['action'],'writing-generate')
        self.assertEqual(result['content']['format_version'],direct.FORMAT)
        self.assertFalse(result['checks']['errors'])
        self.assertTrue(result['checks']['editorial']['notes'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_writing_versions WHERE stage!='draft'").fetchone()[0],0)
        self.assertEqual(before,w.ed.latest_decision(self.conn,self.fixture.eid))
        self.assertNotIn('model',self.calls[0][1])
        self.assertNotIn('reasoning_effort',self.calls[0][1])
        self.assertLessEqual(self.calls[1][1]['timeout'],180)
        self.assertEqual(w.export_draft(self.conn,result['version_id'])['version_id'],result['version_id'])

    def test_idempotency_and_stale_selection(self):
        data=self.fixture.data();control=TaskControl(900)
        first=w.accept_run(self.conn,self.cfg,'generate',data,control)
        self.assertEqual(first,w.accept_run(self.conn,self.cfg,'generate',data,control))
        with self.assertRaises(ValueError):w.accept_run(self.conn,self.cfg,'generate',dict(data,feedback='不同意见'),control)
        with self.assertRaises(ValueError):w.accept_run(self.conn,self.cfg,'generate',dict(self.fixture.data(),decision_revision=99),control)

    def test_audit_failure_preserves_edited_version_and_does_not_rewrite_prose(self):
        def fail_audit(*args,**kwargs):
            if len(self.calls)==2:raise TaskStopped('timed_out')
            return self.runner(*args,**kwargs)
        with self.assertRaises(TaskStopped):self.run_direct(fail_audit)
        row=self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(self.run['run_id'],)).fetchone()
        saved=w.version(self.conn,row['version_id'])
        self.assertEqual(saved['content']['pipeline']['status'],'verification_incomplete')
        self.assertEqual(saved['content']['paragraphs'][0]['text'],self.fixture.statement)
        self.assertIn('编辑稿和起草稿已保留',w.run_record(row)['error'])
        self.assertIn('独立核验',w.run_record(row)['error'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_writing_versions').fetchone()[0],2)

    def test_audit_sees_fixed_body_and_length_includes_product_introduction(self):
        template=dict(status='ready',issues=[],blocks=[dict(kind='product_intro',text='产'*1000,sources=[]),
            dict(kind='risk',text='合成风险',sources=[])])
        with patch('fastnews.writing_templates.select_template',return_value=template):result=self.run_direct()
        audit=self.calls[2][0]
        self.assertEqual(audit['company_copy']['fixed_body_blocks'][0]['text'],'产'*1000)
        self.assertNotIn('style_examples',audit)
        self.assertFalse(any(e['code']=='editorial_length' for e in result['checks']['editorial']['errors']))

    def test_citation_reconciliation_requires_verified_quote_and_preserves_prose(self):
        snapshot=w._snapshot(self.conn,self.cfg,self.fixture.eid,1)
        s=snapshot['sources'][0]
        raw=dict(paragraphs=[dict(text=self.fixture.statement,source_ids=[])],evidence=[dict(
            paragraph_index=1,source_id=s['id'],statement=self.fixture.statement,quote=self.fixture.statement,
            kind='reported',data_period='',supported=True,reason='')])
        self.assertEqual(len(direct.reconcile_audit_sources(raw,snapshot)),1)
        self.assertEqual(raw['paragraphs'][0]['text'],self.fixture.statement)
        raw['paragraphs'][0]['source_ids']=[];raw['evidence'][0]['quote']='没有读到的引文'
        self.assertFalse(direct.reconcile_audit_sources(raw,snapshot))
        self.assertEqual(raw['paragraphs'][0]['source_ids'],[])
        raw['evidence'][0]['quote']=self.fixture.statement;s['published_at']='2026-10-01'
        self.assertFalse(direct.reconcile_audit_sources(raw,snapshot))

    def test_manual_edit_is_a_separate_unverified_version_with_same_export(self):
        from fastnews.writing_delivery import save_manual
        import uuid
        original=self.run_direct()
        edited=save_manual(self.conn,dict(version_id=original['version_id'],text='人工标题\n\n修改后的正文。',request_id=uuid.uuid4().hex))
        self.assertEqual(edited['parent_id'],original['version_id'])
        self.assertEqual(edited['checks']['status'],'manual_review_pending')
        self.assertEqual(w.version(self.conn,original['version_id'])['content']['title'],'设备订单增长')
        self.assertEqual(w.export_draft(self.conn,edited['version_id'])['markdown'],'人工标题\n\n修改后的正文。')
        with self.assertRaisesRegex(ValueError,'人工修改'):w.review_stored_draft(self.conn,w.version(self.conn,edited['version_id']))

    def test_feedback_frozen_and_edit_failure_keeps_composing_draft(self):
        original=self.run_direct()
        self.calls=[]
        def fail(*args,**kwargs):
            if self.calls:raise TaskStopped('cancelled')
            return self.runner(*args,**kwargs)
        with self.assertRaises(TaskStopped):self.run_direct(fail,parent_version_id=original['version_id'],feedback='突出订单变化')
        row=self.conn.execute('SELECT version_id,status FROM fn_writing_runs WHERE run_id=?',(self.run['run_id'],)).fetchone()
        self.assertEqual(row['status'],'cancelled')
        self.assertEqual(self.calls[0][0]['feedback'],'突出订单变化')
        self.assertEqual(w.version(self.conn,row['version_id'])['content']['pipeline']['status'],'editing_incomplete')
        self.assertEqual(w.version(self.conn,original['version_id'])['content']['title'],'设备订单增长')

    def test_new_data_policy_does_not_change_legacy_rules(self):
        from fastnews.writing_checks import check_text
        text='本产品跟踪恒生科技指数，基金规模达100亿元。'
        self.assertTrue({'product_flow','index_identity'}<= {e['code'] for e in check_text(text)})
        self.assertFalse(check_text(text,rules=direct.policy()))
        self.assertTrue(check_text('北方华创上涨',rules=direct.policy()))

    def test_fund_and_index_market_labels_do_not_hide_real_company_errors(self):
        from fastnews.writing_checks import check_text
        text='基金投资南方东英恒生科技指数ETF，跟踪恒生科技指数。腾讯控股发布公告。合成指数科技公司发布公告。'
        entities=[dict(name=name,market='HK') for name in ('南方东英恒生科技指数ETF','恒生科技指数','腾讯控股','合成指数科技公司')]
        errors=check_text(text,entities=entities,rules=direct.policy())
        self.assertEqual({e['excerpt'] for e in errors},{'腾讯控股','合成指数科技公司'})
        self.assertIn('index_identity',{e['code'] for e in check_text(text,entities=entities)})

    def test_discoveries_are_unverified_and_duplicate_sources_do_not_destroy_task(self):
        snapshot=w._snapshot(self.conn,self.cfg,self.fixture.eid,1);snapshot['rules']=direct.policy()
        src=dict(material_id=self.fixture.mid,title='补充原文',publisher='合成发布机构',url='https://example.org/second',
            published_at='2026-09-15',publication_quote='发表于2026-09-15',excerpt='合成新闻摘要',relation='补齐原选材',subject_code='')
        added=direct.stage_sources(dict(summary='合成补搜',gaps=[],sources=[src,src]),snapshot)
        self.assertEqual(len(added),1)
        self.assertFalse(snapshot['sources'][-1]['text_observed'])
        self.assertEqual(snapshot['supplemental_research']['skipped'][0]['reason'],'existing_source_reused')
        future=deepcopy(snapshot);future['sources'][0]['published_at']='2026-09-20'
        with self.assertRaisesRegex(ValueError,'主新闻'):direct.build_input(future)

    def test_publication_passages_tolerate_pdf_spacing_but_do_not_invent_times(self):
        from fastnews.writing_checks import within_cutoff
        source=dict(text_observed=True,date_observed=False,text='合成基金管理人\n2026 年 8 月 11 日',
            publication_hint=dict(date='2026-08-11T09:00:00+08:00',quote='合成基金管理人 2026年8月11日'))
        wrong=deepcopy(source);wrong['publication_hint']['date']='2026-08-10'
        absent=deepcopy(source);absent['text']='没有出现日期'
        snapshot=dict(sources=[source,wrong,absent]);direct.bind_dates(snapshot)
        self.assertEqual(source['published_at'],'2026-08-11')
        self.assertTrue(source['date_observed'])
        self.assertFalse(within_cutoff(source['published_at'],'2026-08-11T12:00:00+08:00'))
        self.assertTrue(within_cutoff(source['published_at'],'2026-08-11T23:59:59+08:00'))
        self.assertFalse(wrong['date_observed']);self.assertFalse(absent['date_observed'])

    def test_server_preserves_specific_edit_timeout_and_direct_workflow_usage(self):
        from fastnews.server import LocalApplication
        app=LocalApplication(self.fixture.path,self.fixture.config,offline=True)
        control=TaskControl(900)
        record=w.accept_run(self.conn,self.cfg,'generate',self.fixture.data(),control)
        identifier=record['run_id'];app.jobs[identifier]=record;app.controls[identifier]=control;app.active_job=identifier
        original=w.execute_run
        def fail_edit(*args,**kwargs):
            if self.calls:raise TaskStopped('timed_out')
            return self.runner(*args,**kwargs)
        def execute(conn,run_id,task_control,**kwargs):
            return original(conn,run_id,task_control,runner=fail_edit,**kwargs)
        with patch('fastnews.writing.execute_run',side_effect=execute):
            app._run_job(identifier,'writing-generate',{'_config':self.cfg})
        self.assertEqual(app.jobs[identifier]['status'],'timed_out')
        self.assertIn('编辑步骤达到180秒时限',app.jobs[identifier]['error'])
        self.assertIn('起草稿已保留',app.jobs[identifier]['error'])
        self.assertIsNone(app.active_job)
        stored=w.run_record(self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(identifier,)).fetchone())
        self.assertEqual(stored['usage']['pipeline'],direct.FORMAT)
        self.assertEqual(stored['error'],app.jobs[identifier]['error'])

    def test_shared_deadline_is_not_reset(self):
        clock=[100.0]
        def runner(*args,**kwargs):
            result=self.runner(*args,**kwargs);clock[0]+=220 if len(self.calls)==1 else 100;return result
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            result=self.run_direct(runner)
        self.assertEqual(result['content']['pipeline']['status'],'complete')
        self.assertLessEqual(self.calls[1][1]['timeout'],180)

    def test_expired_global_deadline_retains_completed_composing_output(self):
        clock=[100.0]
        def runner(*args,**kwargs):
            result=self.runner(*args,**kwargs);clock[0]+=901;return result
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(TaskStopped):self.run_direct(runner)
        row=self.conn.execute('SELECT status,version_id FROM fn_writing_runs WHERE run_id=?',(self.run['run_id'],)).fetchone()
        self.assertEqual(row['status'],'timed_out');self.assertEqual(len(self.calls),1)
        self.assertEqual(w.version(self.conn,row['version_id'])['content']['pipeline']['status'],'editing_incomplete')

    def test_zero_based_host_evidence_is_normalized_only_with_matching_sources(self):
        def runner(*args,**kwargs):
            response=self.runner(*args,**kwargs)
            if 'evidence' in response.payload:response.payload['evidence'][0]['paragraph_index']=0
            return response
        result=self.run_direct(runner)
        self.assertFalse(result['checks']['errors'])
        self.assertEqual(result['content']['raw_article']['evidence'][0]['paragraph_index'],0)
        self.assertIn('zero_based',result['content']['evidence_indexing_normalization'])
        raw=deepcopy(result['content']['raw_article']);raw['evidence'][0]['source_id']='unrelated'
        snapshot=w.version(self.conn,result['version_id'])['snapshot']
        content,checks=direct.validate_edited(raw,snapshot)
        self.assertNotIn('evidence_indexing_normalization',content)
        self.assertTrue(checks['errors'])

    def test_member_identity_must_belong_to_its_observed_source(self):
        result=self.run_direct();raw=deepcopy(result['content']['raw_article'])
        snapshot=w.version(self.conn,result['version_id'])['snapshot']
        snapshot['product_code']='dividend_family';snapshot['product_definition']={'kind':'family','member_codes':['123456']}
        source=snapshot['sources'][0];source.update(subject_code='123456',text=source['text']+'\n基金代码123456')
        raw['paragraphs'][0]['text']='123456。'+raw['paragraphs'][0]['text']
        raw['evidence'][0]['statement']='123456。'+raw['evidence'][0]['statement']
        _,checks=direct.validate_edited(raw,snapshot)
        self.assertNotIn('unsupported_number',{e['code'] for e in checks['errors']})
        source['subject_code']='654321'
        _,checks=direct.validate_edited(raw,snapshot)
        self.assertIn('unsupported_number',{e['code'] for e in checks['errors']})

    def test_known_effective_rename_is_not_overruled_by_an_old_document(self):
        result=self.run_direct();raw=deepcopy(result['content']['raw_article'])
        snapshot=w.version(self.conn,result['version_id'])['snapshot']
        snapshot['product_definition']['effective_updates']=[dict(previous_name='合成旧名称',previous_index_name='合成旧指数')]
        raw['paragraphs'][0]['text']+='产品跟踪合成旧指数。'
        _,checks=direct.validate_edited(raw,snapshot)
        self.assertIn('product_obsolete_identity',{e['code'] for e in checks['errors']})


if __name__=='__main__':unittest.main()
