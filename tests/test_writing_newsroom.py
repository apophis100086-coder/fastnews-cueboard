"""Two-call writing behavior, evidence scope and recovery; synthetic data only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fastnews import writing as w
from fastnews.host_provider import HostResult
from fastnews.task_control import TaskControl, TaskStopped
from fastnews.writing_contracts import NEWS_EDIT
from fastnews.writing_newsroom import build_input, validate_edited, located_quote
from fastnews.product_context import writing_examples
from tests import test_news_writing as legacy_fixtures


class NewsroomTests(unittest.TestCase):
    def setUp(self):
        real=w.policy()
        self.fixture=legacy_fixtures.NewsWritingTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        real_policy=patch('fastnews.writing.policy',return_value=real)
        real_policy.start();self.addCleanup(real_policy.stop)
        self.conn=self.fixture.conn
        outline=self.fixture.generate()
        self.confirmed=self.fixture.confirm(outline)
        self.snapshot=self.confirmed['snapshot']
        self.source=self.snapshot['sources'][0]
        self.calls=[]

    def article(self):
        return dict(status='ready',title='合成设备订单增长',
            paragraphs=[dict(text=self.fixture.statement,source_ids=[self.source['id']])],issues=[])

    def edited(self):
        result=self.article()
        result.update(evidence=[dict(paragraph_index=1,source_id=self.source['id'],
            statement=self.fixture.statement,quote=self.fixture.statement,kind='reported',data_period='',supported=True,reason='')],
            entities=[],review={k:True for k in NEWS_EDIT['properties']['review']['properties']},
            editorial_review={k:[] if k=='issues' else True if k=='material_sufficient' else '合成检查记录'
                for k in NEWS_EDIT['properties']['editorial_review']['properties']},changes=['合成编辑：核对原文并保留准确范围'])
        return result

    def runner(self,host,prompt,schema,**kwargs):
        data=json.loads(prompt.split('INPUT_JSON:\n')[1]);self.calls.append((data,kwargs))
        raw=self.article() if data['stage']=='compose' else self.edited()
        return HostResult(raw,dict(input_tokens=100,output_tokens=30,host_tasks=1,billing='fixture'),{})

    def run_draft(self,runner=None,control=None):
        control=control or TaskControl()
        run=w.accept_run(self.conn,self.fixture.cfg,'draft',self.fixture.data(
            confirmed_version_id=self.confirmed['version_id']),control)
        self.run_id=run['run_id']
        return w.execute_run(self.conn,run['run_id'],control,runner=runner or self.runner)

    def test_two_calls_keep_sources_and_original_and_use_separate_editor(self):
        result=self.run_draft()
        self.assertEqual([c[0]['stage'] for c in self.calls],['compose','edit'])
        self.assertFalse(self.calls[0][1]['web_search']);self.assertFalse(self.calls[1][1]['web_search'])
        self.assertLessEqual(self.calls[0][1]['timeout'],240)
        self.assertLessEqual(self.calls[1][1]['timeout'],390)
        self.assertEqual(self.calls[0][0]['news_sources'],self.calls[1][0]['news_sources'])
        self.assertEqual(self.calls[1][0]['composing_draft'],self.article())
        self.assertNotIn('claims',self.calls[0][0])
        first=w.version(self.conn,result['content']['pipeline']['working_version_id'])
        self.assertEqual(first['content']['paragraphs'][0]['text'],self.fixture.statement)
        self.assertEqual(first['checks']['status'],'editing_incomplete')
        self.assertEqual(result['content']['pipeline']['status'],'complete')
        self.assertFalse(any(e['code'] in {'paragraph_citation','outline_mapping'} for e in result['checks']['errors']))
        row=self.conn.execute('SELECT usage_json,input_json FROM fn_writing_runs WHERE run_id=?',(self.run_id,)).fetchone()
        self.assertEqual(json.loads(row['usage_json'])['host_tasks'],2)
        self.assertEqual(json.loads(row['usage_json'])['input_tokens'],200)
        self.assertEqual(len(json.loads(row['input_json'])['pipeline']['steps']),2)

    def test_edit_failure_and_stop_keep_first_draft_and_cannot_recheck_as_ready(self):
        def failed(*args,**kwargs):
            if len(self.calls):raise TaskStopped('cancelled')
            return self.runner(*args,**kwargs)
        with self.assertRaises(TaskStopped):self.run_draft(failed)
        row=self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(self.run_id,)).fetchone()
        self.assertEqual(row['status'],'cancelled')
        first=w.version(self.conn,row['version_id'])
        self.assertEqual(first['content']['title'],self.article()['title'])
        with self.assertRaisesRegex(ValueError,'编辑'):w.review_stored_draft(self.conn,first)
        with self.assertRaises(ValueError):w.export_draft(self.conn,first['version_id'])

    def test_restart_recovers_interrupted_edit_without_losing_first_version(self):
        def failed(*args,**kwargs):
            if len(self.calls):raise TaskStopped('timed_out')
            return self.runner(*args,**kwargs)
        with self.assertRaises(TaskStopped):self.run_draft(failed)
        row=self.conn.execute('SELECT version_id FROM fn_writing_runs WHERE run_id=?',(self.run_id,)).fetchone()
        original=w.version(self.conn,row['version_id'])
        # Simulate process loss after the independently committed first version.
        with self.conn:self.conn.execute("UPDATE fn_writing_runs SET status='running' WHERE run_id=?",(self.run_id,))
        from fastnews.server import LocalApplication
        app=LocalApplication(self.fixture.path,self.fixture.config,offline=True)
        self.assertEqual(app.jobs[self.run_id]['status'],'interrupted')
        self.assertEqual(w.version(self.conn,row['version_id'])['content'],original['content'])

    def test_total_model_deadline_is_not_reset_for_edit(self):
        clock=[100.0]
        def runner(*args,**kwargs):
            result=self.runner(*args,**kwargs);clock[0]+=210 if len(self.calls)==1 else 100
            return result
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            result=self.run_draft(runner,TaskControl())
        self.assertEqual(self.calls[1][1]['timeout'],180)
        self.assertEqual(result['content']['pipeline']['status'],'complete')

    def test_source_context_survives_and_new_verified_detail_is_allowed(self):
        detail='另一条合成原文细节：设备进入批量交付阶段。'
        self.snapshot['sources'][0]['text']+='\n'+detail
        self.snapshot['sources'][0]['text_segments']=[self.snapshot['sources'][0]['text']]
        before=deepcopy(self.snapshot)
        brief,snapshot=build_input(self.snapshot,self.confirmed['content'])
        self.assertIn(detail,brief['news_sources'][0]['text'])
        self.assertEqual(before,self.snapshot)
        raw=self.edited();raw['paragraphs'][0]['text']=detail
        raw['evidence'][0].update(statement=detail,quote=detail)
        content,checks=validate_edited(raw,snapshot,self.confirmed['content'])
        self.assertFalse(checks['factual']['errors'])
        self.assertEqual(content['paragraphs'][0]['text'],detail)

    def test_unknown_evidence_and_changed_quantity_rejected_without_erasing_raw(self):
        raw=self.edited();raw['evidence'][0]['source_id']='style_example'
        content,checks=validate_edited(raw,self.snapshot,self.confirmed['content'])
        self.assertIn('unknown_source',[e['code'] for e in checks['errors']])
        self.assertEqual(content['paragraphs'][0]['text'],raw['paragraphs'][0]['text'])
        raw=self.edited();raw['paragraphs'][0]['text']=self.fixture.statement.replace('20%','21%')
        self.assertIn('new_number',[e['code'] for e in validate_edited(raw,self.snapshot,self.confirmed['content'])[1]['errors']])

    def test_required_cutoff_and_overflow_are_explicit(self):
        self.snapshot['sources'][0]['published_at']='2026-09-20'
        with self.assertRaisesRegex(ValueError,'日期'):build_input(self.snapshot,self.confirmed['content'])
        self.snapshot['sources'][0]['published_at']='2026-09-15'
        self.snapshot['sources'][0]['text']='合成重要原文。'*10000
        with self.assertRaisesRegex(ValueError,'未截断'):build_input(self.snapshot,self.confirmed['content'])

    def test_complete_examples_are_not_evidence_and_omission_is_visible(self):
        self.snapshot['style_examples']=[dict(id='style1',text='完整范文。'*10000)]
        brief,_=build_input(self.snapshot,self.confirmed['content'])
        self.assertFalse(brief['style_examples'])
        self.assertEqual(brief['input_coverage']['style_omitted'][0]['id'],'style1')

    def test_skill_version_changes_when_editor_changes(self):
        old=w.skill_fingerprint();original=w.instructions
        with patch('fastnews.writing.instructions',side_effect=lambda s:original(s)+(' changed' if s=='edit' else '')):
            self.assertNotEqual(old,w.skill_fingerprint())

    def test_quote_layout_keeps_actual_text_and_does_not_join_omissions_or_numbers(self):
        source=dict(text_segments=['合成产品的\n科技方向，数值20\n30元。','另一段内容'])
        self.assertEqual(located_quote('合成产品的科技方向',source),'合成产品的\n科技方向')
        self.assertEqual(located_quote('数值2030元',source),'数值2030元')
        self.assertEqual(located_quote('30元。另一段内容',source),'30元。另一段内容')
        raw=self.edited();snapshot=deepcopy(self.snapshot)
        snapshot['sources'][0]['text']=self.fixture.statement.replace('合成产业资料','合成产业\n资料')
        snapshot['sources'][0]['text_segments']=[snapshot['sources'][0]['text']]
        _,checks=validate_edited(raw,snapshot,self.confirmed['content'])
        self.assertNotIn('unobserved_quote',[e['code'] for e in checks['errors']])


class PublicStyleExamplesTests(unittest.TestCase):
    def test_public_full_cutoff_and_cross_product_selection(self):
        from tests.test_product_context import ProductContextTests
        fixture=ProductContextTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.history('private_local',title='私有范文')
        fixture.history(title='仅标题',completeness='title_only')
        fixture.history(title='未来文章',publication_time='2026-09-18')
        fixture.history(title='完整已发稿',content='正文。'*1000)
        edition=fixture.edition()
        examples=writing_examples(fixture.conn,edition)
        self.assertEqual([x['title'] for x in examples],['完整已发稿'])
        self.assertEqual(len(examples[0]['text']),3000)
        examples=writing_examples(fixture.conn,dict(edition,product_code='588710'))
        self.assertEqual(examples[0]['selection_reason'],'related_product')


if __name__=='__main__':unittest.main()
