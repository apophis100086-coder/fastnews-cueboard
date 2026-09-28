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
        self.cfg=dict(self.fixture.cfg,_reader_profile={'settings':{'host':'codex','web_search':False}})
        self.calls=[]

    def runner(self,host,prompt,schema,**kwargs):
        payload=json.loads(prompt.split('INPUT_JSON:\n')[1]);self.calls.append((payload,kwargs))
        sid=payload['news_sources'][0]['source_id'];statement=self.fixture.statement
        raw=dict(status='ready',title='设备订单增长',paragraphs=[dict(text=statement,source_ids=[sid])],issues=[])
        if payload['stage']=='edit':
            raw.update(appendix=[],evidence=[dict(paragraph_index=1,source_id=sid,statement=statement,quote=statement,kind='reported',data_period='',supported=True,reason='')],
                entities=[],review={k:True for k in NEWS_EDIT['properties']['review']['properties']},
                editorial_review={k:[] if k=='issues' else True if k=='material_sufficient' else '合成检查' for k in NEWS_EDIT['properties']['editorial_review']['properties']},changes=['删除重复解释'])
        return HostResult(raw,dict(input_tokens=20,output_tokens=10,model_requested=kwargs['model'],reasoning_effort_requested=kwargs['reasoning_effort']),{})

    def run_direct(self,runner=None,**data):
        control=TaskControl(900)
        run=w.accept_run(self.conn,self.cfg,'generate',self.fixture.data(**data),control)
        self.run=run
        return w.execute_run(self.conn,run['run_id'],control,runner=runner or self.runner)

    def test_direct_requires_selection_but_no_outline_and_keeps_two_actual_versions(self):
        before=w.ed.latest_decision(self.conn,self.fixture.eid)
        result=self.run_direct()
        self.assertEqual([c[0]['stage'] for c in self.calls],['compose','edit'])
        self.assertEqual(self.run['action'],'writing-generate')
        self.assertEqual(result['content']['format_version'],direct.FORMAT)
        self.assertFalse(result['checks']['errors'])
        self.assertTrue(result['checks']['editorial']['notes'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM fn_writing_versions WHERE stage!='draft'").fetchone()[0],0)
        self.assertEqual(before,w.ed.latest_decision(self.conn,self.fixture.eid))
        self.assertEqual(self.calls[0][1]['model'],'gpt-6-sol')
        self.assertEqual(self.calls[0][1]['reasoning_effort'],'high')
        self.assertLessEqual(self.calls[1][1]['timeout'],180)
        self.assertEqual(w.export_draft(self.conn,result['version_id'])['version_id'],result['version_id'])

    def test_idempotency_and_stale_selection(self):
        data=self.fixture.data();control=TaskControl(900)
        first=w.accept_run(self.conn,self.cfg,'generate',data,control)
        self.assertEqual(first,w.accept_run(self.conn,self.cfg,'generate',data,control))
        with self.assertRaises(ValueError):w.accept_run(self.conn,self.cfg,'generate',dict(data,feedback='不同意见'),control)
        with self.assertRaises(ValueError):w.accept_run(self.conn,self.cfg,'generate',dict(self.fixture.data(),decision_revision=99),control)

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

    def test_shared_deadline_is_not_reset(self):
        clock=[100.0]
        def runner(*args,**kwargs):
            result=self.runner(*args,**kwargs);clock[0]+=220 if len(self.calls)==1 else 100;return result
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            result=self.run_direct(runner)
        self.assertEqual(result['content']['pipeline']['status'],'complete')
        self.assertLessEqual(self.calls[1][1]['timeout'],180)


if __name__=='__main__':unittest.main()
