"""Writing workflow invariants, isolated DBs and explicitly synthetic evidence."""
from copy import deepcopy
import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import time
import subprocess
import threading
from unittest.mock import Mock

from fastnews import db, editorial as ed, writing as w
from fastnews.config import load_config
from fastnews.host_provider import HostResult
from fastnews.source_catalog import configured_view
from fastnews.task_control import TaskControl, TaskStopped
from fastnews.writing_background import add_candidate
from fastnews.writing_checks import check_text, qualify_claim


class NewsWritingTests(unittest.TestCase):
    def setUp(self):
        # Legacy short fixtures exercise workflow; V6.4 has full-length cases.
        original_policy=w.policy()
        legacy=patch('fastnews.writing.policy',return_value=dict(original_policy,version='legacy-workflow-fixture'))
        legacy.start();self.addCleanup(legacy.stop)
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.db'
        self.conn=db.connect(self.path,mode='fixture'); self.addCleanup(self.conn.close)
        self.config=load_config(); db.sync_registry(self.conn,self.config)
        self.ed=ed.create_edition(self.conn,self.config,product_code='588090',edition_date='2026-09-17',
            news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
        self.eid=self.ed['edition_id']
        self.statement='合成产业资料：设备订单同比增长20%，仍需跟进交付。'
        imported=ed.import_evidence_payload(self.conn,{'is_fixture':True,'title':'合成产业资料',
            'content':self.statement,'source_type':'user_document','source_url':'https://example.org/evidence',
            'declared_source':'合成测试出处','first_published_at':'2026-09-15','facts':[],
            'event':{'subject':'合成设备','action':'披露','object':'订单','phase':'announcement'}},edition_id=self.eid)
        self.mid=imported['event_id']; add_candidate(self.conn,self.config,self.eid,self.mid)
        self.save(0)
        self.cfg=configured_view(self.conn,self.config)
        self.no_network=patch('socket.create_connection',side_effect=AssertionError('fixture forbids network'))
        self.no_network.start(); self.addCleanup(self.no_network.stop)

    def test_failed_model_contract_keeps_raw_response_for_audit(self):
        control=TaskControl()
        run=w.accept_run(self.conn,self.cfg,'outline',self.data(),control)
        bad={'title':'合成未通过结构检查的原始内容'}
        with self.assertRaises(ValueError):
            w.execute_run(self.conn,run['run_id'],control,runner=lambda *a,**k:HostResult(bad,{},{}))
        row=self.conn.execute('SELECT status,input_json FROM fn_writing_runs WHERE run_id=?',(run['run_id'],)).fetchone()
        self.assertEqual(row['status'],'failed')
        self.assertEqual(json.loads(row['input_json'])['raw_failed_response'],bad)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_writing_versions').fetchone()[0],0)

    def save(self,revision,**kw):
        return ed.save_decision(self.conn,edition_id=self.eid,selected_event_id=self.mid,selected_fact_ids=[],
            selected_clue_ids=[self.mid],manual_notes=[],must_include_ids=[self.mid],expected_revision=revision,
            actor_type='human',config=self.config,**kw)

    def data(self,**kw):
        return dict(edition_id=self.eid,decision_revision=1,request_id=uuid.uuid4().hex,**kw)

    def outline(self,payload):
        source=payload['sources'][0]
        return dict(mainline='从设备订单变化观察产业需求',title_directions=['设备订单增长，关注产业需求'],
            paragraphs=[dict(id='p1',purpose='披露订单进展',points=[self.statement],analysis='订单不等于收入。',
                product_connection='产品关联仍以资料为依据。',material_ids=[self.mid],claim_ids=['c1'])],
            claims=[dict(id='c1',material_id=self.mid,statement=self.statement,kind='reported',source_id=source['id'],
                quote=self.statement,published_at='2026-09-15',publication_quote='',data_period='',supported=True,reason='对应原文')],
            dispositions=[dict(material_id=self.mid,role='p1',reason='主事件')],suggestions=['保留交付阶段区别'],
            changes=[],gaps=[],blocking_gaps=[],supplement_queries=['半导体设备 交付进展'],entities=[])

    def runner(self,host,prompt,schema,**kwargs):
        self.assertFalse(kwargs['web_search'])
        payload=json.loads(prompt.split('INPUT_JSON:\n')[1]); self.last_input=payload
        if payload['stage']=='outline':
            result=self.outline(payload)
        else:
            result=dict(status='ready',title='设备订单增长，关注产业需求',paragraphs=[dict(text=self.statement,
                claim_ids=['c1'],material_ids=[self.mid],outline_paragraph_id='p1',outline_anchor=payload['confirmed_outline'].split('\n')[0])],issues=[],entities=[],
                review=dict(faithful_to_outline=True,claims_supported=True,must_include_covered=True,entities_complete=True,no_new_facts=True))
        return HostResult(result,{'input_tokens':100,'output_tokens':30},{'web_actions':0})

    def generate(self,stage='outline',**kw):
        data=self.data(**kw); control=TaskControl()
        record=w.accept_run(self.conn,self.cfg,stage,data,control)
        return w.execute_run(self.conn,record['run_id'],control,runner=self.runner)

    def confirm(self,outline,text=None):
        return w.confirm_outline(self.conn,self.data(outline_version_id=outline['version_id'],
            text=text or outline['content']['text']))

    def test_outline_confirm_draft_has_paragraph_sources_and_immutable_versions(self):
        outline=self.generate(); self.assertEqual(outline['checks']['errors'],[])
        final=self.confirm(outline,outline['content']['text']+'\n语气简洁。')
        draft=self.generate('draft',confirmed_version_id=final['version_id'])
        self.assertEqual(draft['checks']['status'],'ready')
        exported=w.export_draft(self.conn,draft['version_id'])
        self.assertIn('2026-09-15',exported['markdown'])
        self.assertIn('语气简洁',self.last_input['confirmed_outline'])
        self.assertEqual(len(w.workflow(self.conn,self.eid)['versions']),3)

    def test_program_recheck_preserves_old_version_and_does_not_call_model(self):
        outline=self.generate();confirmed=self.confirm(outline);draft=self.generate('draft',confirmed_version_id=confirmed['version_id'])
        old=w.version(self.conn,draft['version_id']);attempts=w.task_attempts(self.conn)
        data=dict(version_id=draft['version_id'],request_id=uuid.uuid4().hex)
        newer=w.recheck_draft(self.conn,data)
        self.assertEqual(newer['parent_id'],draft['version_id']);self.assertFalse(newer['checks']['errors'])
        self.assertEqual(newer,w.recheck_draft(self.conn,data));self.assertEqual(w.version(self.conn,draft['version_id']),old)
        self.assertEqual(w.task_attempts(self.conn),attempts)

    def test_verified_publication_date_can_be_written_but_extra_numbers_cannot(self):
        outline=self.generate();confirmed=self.confirm(outline)
        raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=confirmed['content']['confirmed_text'])),{},web_search=False).payload
        raw['paragraphs'][0]['text']='2026年9月15日，'+self.statement
        self.assertFalse(w.validate_draft(raw,confirmed['snapshot'],confirmed['content'])[1]['errors'])
        raw['paragraphs'][0]['text']+='另有999亿元。'
        self.assertTrue(w.validate_draft(raw,confirmed['snapshot'],confirmed['content'])[1]['errors'])

    def test_reconfirmed_outline_records_current_skill_and_keeps_old_snapshot(self):
        outline=self.generate();old=w.version(self.conn,outline['version_id'])
        original=w.instructions
        with patch('fastnews.writing.instructions',side_effect=lambda stage:original(stage)+'\n表达说明更新'):
            confirmed=self.confirm(outline)
            self.assertNotEqual(confirmed['snapshot']['skill_version'],old['snapshot']['skill_version'])
        self.assertEqual(w.version(self.conn,outline['version_id']),old)

    def test_shared_style_is_loaded_and_frozen_for_an_accepted_task(self):
        skill=Path(self.temp.name)/'skill'
        shutil.copytree(w.SKILL,skill)
        guide=skill/'guides'/'style.md'
        guide.write_text('任务发起时采用的写法。',encoding='utf-8')
        with patch('fastnews.writing.SKILL',skill):
            control=TaskControl()
            run=w.accept_run(self.conn,self.cfg,'outline',self.data(),control)
            guide.write_text('任务发起之后修订的写法。',encoding='utf-8')
            def captured_runner(host,prompt,schema,**kwargs):
                self.assertIn('任务发起时采用的写法。',prompt)
                self.assertNotIn('任务发起之后修订的写法。',prompt)
                return self.runner(host,prompt,schema,**kwargs)
            result=w.execute_run(self.conn,run['run_id'],control,runner=captured_runner)
            self.assertFalse(result['checks']['errors'])
            self.assertTrue(w.workflow(self.conn,self.eid)['versions'][0]['stale_skill_version'])

    def test_one_confirmed_outline_point_can_expand_into_sourced_natural_paragraphs(self):
        outline=self.generate();confirmed=self.confirm(outline)
        def expanded_runner(*args,**kwargs):
            result=self.runner(*args,**kwargs)
            analysis=deepcopy(result.payload['paragraphs'][0])
            analysis['text']='订单增长为需求判断提供了依据，兑现为经营成果仍取决于后续交付。'
            result.payload['paragraphs'].append(analysis)
            return result
        control=TaskControl()
        run=w.accept_run(self.conn,self.cfg,'draft',self.data(confirmed_version_id=confirmed['version_id']),control)
        result=w.execute_run(self.conn,run['run_id'],control,runner=expanded_runner)
        self.assertFalse(result['checks']['errors'])
        paragraphs=result['content']['paragraphs']
        self.assertEqual(len(paragraphs),2)
        self.assertEqual({p['outline_paragraph_id'] for p in paragraphs},{'p1'})
        self.assertTrue(all(p['sources'] and p['claim_ids']==['c1'] for p in paragraphs))
        self.assertIn('兑现为经营成果',w.export_draft(self.conn,result['version_id'])['markdown'])

    def test_no_confirmation_and_cross_edition_rejected(self):
        outline=self.generate()
        with self.assertRaisesRegex(ValueError,'确认'):
            self.generate('draft',confirmed_version_id=outline['version_id'])

    def test_new_selection_invalidates_confirmation(self):
        outline=self.generate(); confirmed=self.confirm(outline); self.save(1)
        with self.assertRaisesRegex(ValueError,'版本|更新'):
            self.generate('draft',confirmed_version_id=confirmed['version_id'])

    def test_new_selection_blocks_old_draft_export_and_recheck_but_keeps_history(self):
        outline=self.generate();confirmed=self.confirm(outline)
        draft=self.generate('draft',confirmed_version_id=confirmed['version_id'])
        before=w.version(self.conn,draft['version_id'])
        self.save(1,mainline_note='人工改为新的写作主线')
        with self.assertRaisesRegex(ValueError,'选材版本'):
            w.export_draft(self.conn,draft['version_id'])
        with self.assertRaisesRegex(ValueError,'选材版本'):
            w.recheck_draft(self.conn,dict(version_id=draft['version_id'],request_id=uuid.uuid4().hex))
        self.assertEqual(w.version(self.conn,draft['version_id']),before)
        self.assertTrue(w.workflow(self.conn,self.eid)['versions'][-1]['stale_selection'])

    def test_benchmark_definition_is_not_short_term_performance(self):
        for text in ('业绩比较基准为恒生科技指数收益率',
                     '依据2025年第4季度报告，业绩比较基准为恒生科技指数收益率，使用估值汇率折算',
                     '本基金业绩比较基准为沪深300指数收益率（使用估值汇率折算）'):
            self.assertNotIn('short_performance',[x['code'] for x in check_text(text)])
        for text in ('本基金近三个月收益率为5%，业绩比较基准为恒生科技指数收益率',
                     '本基金业绩比较基准为恒生科技指数收益率（近三个月5%）',
                     '本基金业绩比较基准为恒生科技指数收益率，近三个月上涨5%',
                     '本基金的业绩比较基准指数收益率为5%'):
            self.assertIn('short_performance',[x['code'] for x in check_text(text)])

    def test_product_reference_date_is_available_only_on_its_cited_source(self):
        outline=self.generate();confirmed=self.confirm(outline)
        snap=confirmed['snapshot'];source=snap['sources'][0]
        source.update(data_as_of='2025-12-31',evidence_id='evidence_fixture',date_observed=True)
        raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=confirmed['content']['confirmed_text'])),{},web_search=False).payload
        raw['paragraphs'][0]['text']='资料截至2025年12月31日。'+self.statement
        self.assertFalse(w.validate_draft(raw,snap,confirmed['content'])[1]['errors'])
        claim=deepcopy(confirmed['content']['claims'][0]);claim['data_period']='2025-12-31'
        self.assertFalse(qualify_claim(claim,{source['id']:source},snap['news_cutoff']))
        claim['data_period']='2025-12-29'
        self.assertIn('unsupported_period',{e['code'] for e in qualify_claim(claim,{source['id']:source},snap['news_cutoff'])})
        snap['sources'].append(dict(source,id='unused_reference'))
        source.pop('data_as_of')
        self.assertIn('new_number',{e['code'] for e in w.validate_draft(raw,snap,confirmed['content'])[1]['errors']})

    def test_known_bilingual_publisher_still_requires_its_real_quote_and_identity(self):
        quote='Hang Seng Indexes Company launched the HSTECH in July 2020.'
        source=dict(id='s',name='恒生指数公司',text=quote,text_observed=True)
        entity=dict(name='恒生指数公司',source_id='s',quote=quote,market='HK')
        self.assertFalse(w._entities([entity],{'s':source}))
        self.assertFalse(w._entities([entity],{'s':dict(source,text=quote.replace('Company launched','\nCompany launche d'))}))
        self.assertTrue(w._entities([dict(entity,name='任意其他公司')],{'s':source}))
        self.assertTrue(w._entities([entity],{'s':dict(source,name='其他出处')}))
        self.assertTrue(w._entities([entity],{'s':dict(source,text='未读到的其他内容')}))
        self.assertTrue(w._entities([entity],{'s':dict(source,text_segments=['Hang Seng Indexes Company','launched the HSTECH in July 2020.'])}))

    def test_title_cannot_change_an_observed_percentage_to_money(self):
        outline=self.generate();confirmed=self.confirm(outline)
        raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=confirmed['content']['confirmed_text'])),{},web_search=False).payload
        raw['title']='设备订单20亿元'
        self.assertIn('title_unit',{e['code'] for e in w.validate_draft(raw,confirmed['snapshot'],confirmed['content'])[1]['errors']})

    def test_legacy_passed_draft_cannot_bypass_new_export_checks(self):
        outline=self.generate();confirmed=self.confirm(outline)
        draft=self.generate('draft',confirmed_version_id=confirmed['version_id'])
        saved=w.version(self.conn,draft['version_id'])
        # Simulate a version accepted by the previous title-number-only gate.
        content=deepcopy(saved['content']);content['title']='设备订单20亿元'
        with self.conn:
            old=w._save_version(self.conn,saved['snapshot'],'draft',content,dict(errors=[],status='ready'),parent_id=confirmed['version_id'])
        with self.assertRaisesRegex(ValueError,'当前程序检查'):
            w.export_draft(self.conn,old['version_id'])
        self.assertEqual(w.version(self.conn,old['version_id']),old)

    def test_quantity_bounds_checked_at_claim_body_and_title(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1)
        self.statement='合成产业资料：设备稼动率90%以上。'
        snap['sources'][0]['text']=self.statement
        raw=self.outline(snap)
        raw['claims'][0]['statement']='设备稼动率超过90%。'
        self.assertIn('unsupported_quantity_scope',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})
        raw['claims'][0]['statement']=self.statement
        outline,checks=w.validate_outline(raw,snap)
        self.assertFalse(checks['errors']);outline['confirmed_text']=outline['text']
        draft=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=outline['text'])),{},web_search=False).payload
        draft['title']='设备稼动率超过90%';draft['paragraphs'][0]['text']='设备稼动率90%。'
        codes={e['code'] for e in w.validate_draft(draft,snap,outline)[1]['errors']}
        self.assertTrue({'title_quantity_scope','new_quantity_scope'}<=codes)
        draft['title']='设备稼动率不低于90%';draft['paragraphs'][0]['text']=self.statement
        self.assertFalse(w.validate_draft(draft,snap,outline)[1]['errors'])

    def test_duplicate_requests_not_new_model_tasks(self):
        data=self.data(); control=TaskControl()
        a=w.accept_run(self.conn,self.cfg,'outline',data,control)
        b=w.accept_run(self.conn,self.cfg,'outline',data,TaskControl())
        self.assertEqual(a['run_id'],b['run_id']); self.assertEqual(w.task_attempts(self.conn),1)
        with self.assertRaisesRegex(ValueError,'不同'):
            w.accept_run(self.conn,self.cfg,'outline',dict(data,feedback='改变'),TaskControl())

    def test_confirmation_idempotent(self):
        outline=self.generate(); data=self.data(outline_version_id=outline['version_id'],text=outline['content']['text'])
        self.assertEqual(w.confirm_outline(self.conn,data)['version_id'],w.confirm_outline(self.conn,data)['version_id'])

    def test_failed_and_cancelled_attempts_are_counted(self):
        control=TaskControl(); rec=w.accept_run(self.conn,self.cfg,'outline',self.data(),control)
        control.cancelled.set()
        with self.assertRaises(TaskStopped):
            w.execute_run(self.conn,rec['run_id'],control,runner=self.runner)
        self.assertEqual(w.task_attempts(self.conn),1)
        self.assertEqual(w.workflow(self.conn,self.eid)['runs'][0]['status'],'cancelled')

    def test_malformed_response_has_no_version(self):
        control=TaskControl(); rec=w.accept_run(self.conn,self.cfg,'outline',self.data(),control)
        with self.assertRaises(ValueError):
            w.execute_run(self.conn,rec['run_id'],control,runner=lambda *a,**k:HostResult({}, {}, {}))
        self.assertEqual(w.workflow(self.conn,self.eid)['versions'],[])

    def test_unselected_private_notes_never_enter_payload(self):
        ed.save_decision(self.conn,edition_id=self.eid,selected_event_id=self.mid,selected_fact_ids=[],
            selected_clue_ids=[self.mid],expected_revision=1,actor_type='human',config=self.config,
            manual_notes=[dict(note_id='secret_note',kind='writing_note',content='不应发送的私人备注',selected=False),
                          dict(note_id='chosen_note',kind='writing_note',content='本次写作意见',selected=True)])
        snapshot=w._snapshot(self.conn,self.cfg,self.eid,2)
        self.assertNotIn('不应发送',w.dumps(snapshot)); self.assertIn('本次写作意见',w.dumps(snapshot))

    def test_manual_note_not_original(self):
        ed.save_decision(self.conn,edition_id=self.eid,selected_event_id=self.mid,selected_fact_ids=[],
            selected_clue_ids=[self.mid],expected_revision=1,actor_type='human',config=self.config,
            manual_notes=[dict(note_id='n1',kind='highlight',content='手工数字20亿元',selected=True,source_name='声明来源')])
        snapshot=w._snapshot(self.conn,self.cfg,self.eid,2)
        self.assertFalse(snapshot['sources'][-1]['text_observed'])

    def test_legacy_fact_keeps_search_and_user_text_identity(self):
        revision=1
        for source_type in ('search_snippet','user_text','original_publication'):
            with self.subTest(source_type=source_type):
                imported=ed.import_evidence_payload(self.conn,dict(is_fixture=True,title='合成旧事实',
                    content=self.statement,source_type=source_type,source_url='https://example.org/'+source_type,
                    first_published_at='2026-09-15',facts=[],event=dict(subject=source_type,action='披露',object='合成资料',phase='announcement')),edition_id=self.eid)
                fact=ed.add_fact_from_evidence(self.conn,edition_id=self.eid,event_id=imported['event_id'],
                    evidence_id=imported['evidence_ids'][0],statement=self.statement,kind='reported',actor_type='human')
                ed.save_decision(self.conn,edition_id=self.eid,selected_event_id=None,selected_fact_ids=[fact['fact_id']],
                    selected_clue_ids=[],manual_notes=[],must_include_ids=[],expected_revision=revision,actor_type='human',config=self.config)
                revision+=1
                snapshot=w._snapshot(self.conn,self.cfg,self.eid,revision)
                source=snapshot['sources'][0]
                self.assertEqual(source['identity'],source_type)
                self.assertEqual(source['text_observed'],source_type=='original_publication')
                self.assertEqual(source['date_observed'],source_type=='original_publication')

    def test_search_hint_or_unobserved_quote_cannot_qualify(self):
        payload=w._snapshot(self.conn,self.cfg,self.eid,1); raw=self.outline(payload)
        payload['sources'][0]['text_observed']=False
        content,checks=w.validate_outline(raw,payload)
        self.assertFalse(content['claims'][0]['usable'])
        self.assertTrue(checks['errors'])

    def test_cutoff_and_missing_publication_block(self):
        payload=w._snapshot(self.conn,self.cfg,self.eid,1)
        for date in ('2026-09-18',''):
            raw=self.outline(payload);raw['claims'][0]['published_at']=date
            if not date: payload['sources'][0]['published_at']=None
            self.assertTrue(w.validate_outline(raw,payload)[1]['errors'])

    def test_must_include_and_missing_material_disposition(self):
        payload=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(payload)
        raw['dispositions']=[]
        with self.assertRaises(ValueError): w.validate_outline(raw,payload)

    def test_new_body_numbers_and_failed_review_block_export(self):
        outline=self.generate(); confirmed=self.confirm(outline)
        original=self.runner
        def bad(*args,**kwargs):
            result=original(*args,**kwargs)
            result.payload['paragraphs'][0]['text']+='新增投入999亿元。'
            return result
        control=TaskControl(); rec=w.accept_run(self.conn,self.cfg,'draft',self.data(confirmed_version_id=confirmed['version_id']),control)
        result=w.execute_run(self.conn,rec['run_id'],control,runner=bad)
        self.assertTrue(result['checks']['errors'])
        with self.assertRaises(ValueError): w.export_draft(self.conn,result['version_id'])

    def test_all_five_products_use_same_skill(self):
        for code in self.config['products']:
            edition=ed.create_edition(self.conn,self.config,product_code=code,edition_date='2026-09-17',news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
            self.assertEqual(w.workflow(self.conn,edition['edition_id'])['rules_version'],w.policy()['version'])

    def test_optional_gap_does_not_block_but_key_gap_does(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(snap)
        raw['gaps']=['未使用的数据可按需补充。']
        raw['paragraphs'][0]['product_connection']='不使用资金追捧。'
        self.assertFalse(w.validate_outline(raw,snap)[1]['errors'])
        raw['blocking_gaps']=['主线关键事实无依据']
        self.assertIn('model_gap',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})

    def test_period_uses_observed_document_and_normalizes_date_padding(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(snap)
        snap['sources'][0]['text']='9月10日披露。'+self.statement
        raw['claims'][0]['data_period']='2026年9月10日'
        self.assertFalse(w.validate_outline(raw,snap)[1]['errors'])
        raw['claims'][0]['data_period']='2027年10月30日'
        self.assertIn('unsupported_period',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})

    def test_frozen_product_identifier_is_not_an_invented_statistic(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(snap)
        snap['sources'][0]['text']='基金代码588090。'+self.statement
        raw['claims'][0]['statement']='588090相关资料表示：'+self.statement
        self.assertFalse(w.validate_outline(raw,snap)[1]['errors'])
        raw['claims'][0]['statement']='588090亿元。'
        self.assertIn('unsupported_unit',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})

    def test_located_excerpts_keep_relevant_tail_and_never_join_into_original(self):
        text='前言。'*1200+'Phase 2b follow-up showed 49% improvement.'+'尾文。'*300
        pieces=w.source_segments(text,1500,'核对49%对应阶段')
        self.assertTrue(any('Phase 2b' in p and '49%' in p for p in pieces))
        self.assertTrue(all(p in text for p in pieces));self.assertLessEqual(sum(map(len,pieces)),1500)
        snap=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(snap)
        snap['sources'][0].update(text_segments=['前一片段','后一片段'],text='前一片段\n后一片段')
        raw['claims'][0]['quote']='前一片段\n后一片段'
        self.assertIn('unobserved_quote',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})

    def test_new_currency_or_unit_cannot_borrow_an_existing_number(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1);raw=self.outline(snap)
        raw['claims'][0]['statement']='设备订单20亿元。'
        self.assertIn('unsupported_unit',{e['code'] for e in w.validate_outline(raw,snap)[1]['errors']})

    def test_user_restructure_is_the_authoritative_outline(self):
        outline=self.generate();confirmed=self.confirm(outline,'只保留一个短段：订单进展及交付观察。')
        draft=self.generate('draft',confirmed_version_id=confirmed['version_id'])
        self.assertFalse(draft['checks']['errors'])
        self.assertEqual(self.last_input['confirmed_outline'],'只保留一个短段：订单进展及交付观察。')

    def test_fabricated_outline_anchor_cannot_pass(self):
        outline=self.generate();confirmed=self.confirm(outline)
        raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=confirmed['content']['confirmed_text'])),{},web_search=False).payload
        raw['paragraphs'][0]['outline_anchor']='用户没有提出的内容'
        self.assertIn('paragraph_citation',{e['code'] for e in w.validate_draft(raw,confirmed['snapshot'],confirmed['content'])[1]['errors']})

    def test_task_output_after_deadline_never_becomes_a_version(self):
        control=TaskControl();rec=w.accept_run(self.conn,self.cfg,'outline',self.data(),control)
        def late(*a,**k):
            result=self.runner(*a,**k);control.deadline=time.monotonic()-1;return result
        with self.assertRaises(TaskStopped):w.execute_run(self.conn,rec['run_id'],control,runner=late)
        self.assertEqual(w.workflow(self.conn,self.eid)['versions'],[])
        self.assertEqual(w.workflow(self.conn,self.eid)['runs'][0]['status'],'timed_out')

    def test_reader_cancellation_closes_process_tree(self):
        control=TaskControl();process=Mock(returncode=0);process.poll.return_value=None;group=Mock()
        def cancelled(*a,**k):
            control.cancelled.set();raise subprocess.TimeoutExpired('fixture',.1)
        process.communicate.side_effect=cancelled
        with patch('fastnews.writing.subprocess.Popen',return_value=process),patch('fastnews.writing.ProcessGroup',return_value=group):
            with self.assertRaises(TaskStopped):w._read_source('https://example.org',control,12)
        group.close.assert_called_once();process.kill.assert_called_once();process.wait.assert_called_once()

    def test_failed_source_is_isolated_and_known_restriction_cannot_be_evaded(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1)
        snap['source_constraints']=[]
        source=snap['sources'][0];source.update(text='',text_observed=False,url='https://www.cls.cn/detail/123',source_id=None)
        snap['sources'].append(dict(source,id='src_2',url='https://example.org/actual'))
        fetcher=Mock(return_value=dict(text='原文片段',published_at='2026-09-15',retrieved_at='2026-09-17'))
        w.verify_sources(snap,TaskControl(),fetcher=fetcher)
        self.assertEqual(source['error'],'program_read_restricted');self.assertTrue(snap['sources'][1]['text_observed'])
        self.assertEqual(fetcher.call_count,1)

    def test_shared_source_reused_after_page_budget_without_another_request(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1)
        source=snap['sources'][0];source.update(text='',text_observed=False)
        snap['sources'].append(dict(source,id='src_2',material_id='another_material'))
        snap['sources'].append(dict(source,id='src_3',url='https://example.org/unread'))
        snap['rules']['max_pages']=1
        fetcher=Mock(return_value=dict(text=self.statement,published_at='2026-09-15'))
        w.verify_sources(snap,TaskControl(),fetcher=fetcher)
        self.assertTrue(snap['sources'][1]['text_observed'])
        self.assertEqual(snap['sources'][2]['error'],'verification_budget')
        self.assertEqual(fetcher.call_count,1)

    def test_restart_retains_request_and_marks_incomplete_run(self):
        from fastnews.server import LocalApplication
        data=self.data();rec=w.accept_run(self.conn,self.cfg,'outline',data,TaskControl())
        app=LocalApplication(self.path,self.config,offline=True)
        self.assertEqual(app.jobs[rec['job_id']]['status'],'interrupted')
        self.assertEqual(app.mutate('writing-outline',data),{'job_id':rec['job_id']})
        self.assertEqual(w.task_attempts(self.conn),1)

    def test_shared_budget_blocks_before_launching_another_task(self):
        with patch('fastnews.writing.task_attempts',return_value=12):
            with self.assertRaisesRegex(ValueError,'共享预算'):w.accept_run(self.conn,self.cfg,'outline',self.data(),TaskControl())
        self.assertEqual(w.task_attempts(self.conn),0)

    def test_server_writing_wait_does_not_lock_human_saves_or_old_export(self):
        from fastnews.server import LocalApplication
        path=Path(self.temp.name)/'concurrent.db';app=LocalApplication(path,self.config,output_dir=Path(self.temp.name)/'exports')
        with app.connection() as conn:
            self.addCleanup(conn.close);db.sync_registry(conn,self.config)
            edition=ed.create_edition(conn,self.config,product_code='588090',edition_date='2026-09-17',news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
        eid=edition['edition_id']
        material=app.mutate('import',dict(kind='evidence',edition_id=eid,filename='synthetic.txt',content='合成并发测试，本地资料。',first_published_at='2026-09-15'))
        saved=app.mutate('decision',dict(edition_id=eid,selected_clue_ids=[material['event_id']],manual_notes=[],expected_revision=0))
        ready,release=threading.Event(),threading.Event()
        request=dict(edition_id=eid,decision_revision=saved['decision_revision'],request_id='concurrent-writing')
        def wait_for_model(conn,jid,control,**kwargs):
            ready.set();release.wait(5);control.check();return {}
        with patch('fastnews.writing.execute_run',side_effect=wait_for_model):
            job=app.mutate('writing-outline',request);self.assertTrue(ready.wait(5))
            self.assertEqual(app.mutate('writing-outline',request),job)
            with self.assertRaises(ValueError):app.mutate('writing-outline',dict(request,request_id='different-writing'))
            saved=app.mutate('decision',dict(edition_id=eid,selected_clue_ids=[material['event_id']],manual_notes=[],expected_revision=1,mainline_note='生成期间新写的意见'))
            exported=app.mutate('export',dict(edition_id=eid,decision_revision=saved['decision_revision']))
            self.assertTrue(Path(exported['paths']['markdown']).exists())
            app.mutate('job-stop',job);release.set()
            end=time.monotonic()+5
            while app.active_job and time.monotonic()<end:time.sleep(.01)
            self.assertEqual(app.jobs[job['job_id']]['status'],'cancelled')
            self.assertEqual(app.detail(eid)['decision']['mainline_note'],'生成期间新写的意见')

    def test_pasted_unverified_new_fact_returns_gap_not_an_exportable_draft(self):
        outline=self.generate();confirmed=self.confirm(outline,'最终框架：写产业订单，并添加无来源的999亿元投资。')
        def reject(*args,**kwargs):
            result=self.runner(*args,**kwargs)
            result.payload.update(status='needs_materials',title='',paragraphs=[],issues=['999亿元未在材料中找到依据'])
            return result
        control=TaskControl();run=w.accept_run(self.conn,self.cfg,'draft',self.data(confirmed_version_id=confirmed['version_id']),control)
        draft=w.execute_run(self.conn,run['run_id'],control,runner=reject)
        self.assertTrue(draft['checks']['errors']);self.assertEqual(draft['content']['paragraphs'],[])
        with self.assertRaises(ValueError):w.export_draft(self.conn,draft['version_id'])

    def test_semantic_review_catches_plan_or_causal_overstatement(self):
        outline=self.generate();confirmed=self.confirm(outline)
        raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=confirmed['content']['confirmed_text'])),{},web_search=False).payload
        raw['review']['claims_supported']=False
        self.assertIn('semantic_review',{e['code'] for e in w.validate_draft(raw,confirmed['snapshot'],confirmed['content'])[1]['errors']})

    def test_all_five_product_contexts_obey_same_evidence_and_body_contract(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1)
        for code,product in self.config['products'].items():
            with self.subTest(code=code):
                current=deepcopy(snap);current.update(product_code=code,product_name=product['name'])
                outline,checks=w.validate_outline(self.outline(current),current)
                self.assertFalse(checks['errors']);outline['confirmed_text']=outline['text']
                raw=self.runner('fixture','INPUT_JSON:\n'+w.dumps(dict(stage='draft',confirmed_outline=outline['text'])),{},web_search=False).payload
                self.assertFalse(w.validate_draft(raw,current,outline)[1]['errors'])


class WritingRuleTests(unittest.TestCase):
    def test_forbidden_semantic_variants(self):
        for text in ('本基金连续3天吸金。','ETF近5个交易日上涨10%。','基金半年收益率20%。',
                     '本产品规模突破100亿元。','ETF份额增加2亿份。','基金净流出2亿元。'):
            with self.subTest(text=text): self.assertTrue(check_text(text))

    def test_allowed_industry_turnover_and_index_identity(self):
        for text in ('半导体产业规模达到100亿元，同比增长20%。','ETF日均成交额20亿元。',
                     '科创50ETF华泰柏瑞（588090）覆盖科创板相关方向。','联接基金A类份额可以申购。',
                     '南向市场净流入100亿元。'):
            with self.subTest(text=text): self.assertEqual(check_text(text),[])

    def test_company_scope_not_fund_codes(self):
        self.assertTrue(check_text('某A股公司增长',entities=[dict(name='某A股公司',market='CN')]))
        self.assertTrue(check_text('某港股公司增长',entities=[dict(name='某港股公司',market='HK')]))
        self.assertFalse(check_text('基金代码588090',product_codes=['588090']))
        self.assertTrue(check_text('中芯国际在港上市',entities=[dict(name='中芯国际',market='HK')]))
        self.assertTrue(check_text('中国移动与北方华创披露经营数据'))
        self.assertTrue(check_text('腾讯控股披露经营数据',entities=[dict(name='腾讯控股',market='HK')]))
        self.assertFalse(check_text('华泰柏瑞基金管理有限公司',entities=[dict(name='华泰柏瑞基金管理有限公司',market='UNKNOWN')]))

    def test_six_month_calendar_boundary(self):
        self.assertTrue(check_text('基金2026/1/1至2026/7/1累计上涨20%。'))
        self.assertFalse(check_text('基金2026/1/1至2026/7/2累计上涨20%。'))

    def test_normal_industry_growth_does_not_exempt_nearby_fund_return(self):
        self.assertTrue(check_text('基金近三个月上涨20%，产业营收同比增长20%。'))
        self.assertFalse(check_text('基金关注芯片需求，半导体产业规模增长20%。'))
        self.assertTrue(check_text('本基金连续11个交易日获得资金增持。'))

    def test_editorial_exemption_never_applies_to_a_public_draft(self):
        self.assertFalse(check_text('不使用基金规模100亿元等卖点。',editorial=True))
        self.assertTrue(check_text('本基金规模100亿元。',editorial=True))
        self.assertTrue(check_text('不使用基金规模100亿元等卖点。'))
