"""Real workflow with synthetic web results; no network or real model calls."""
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch, Mock

from tests import test_news_writing as fixtures
from fastnews import writing as w, editorial as ed
from fastnews.host_provider import HostResult
from fastnews.task_control import TaskControl, TaskStopped
from fastnews.writing_research import research_brief


class OutlineResearchTests(unittest.TestCase):
    setUp=fixtures.NewsWritingTests.setUp
    save=fixtures.NewsWritingTests.save
    data=fixtures.NewsWritingTests.data
    outline=fixtures.NewsWritingTests.outline
    confirm=fixtures.NewsWritingTests.confirm
    runner=fixtures.NewsWritingTests.runner
    news='合成公开政策增加了设备采购支持。'

    def discovery_runner(self,host,prompt,schema,**options):
        self.assertTrue(options['web_search'])
        self.assertLessEqual(options['timeout'],390)
        self.assertGreaterEqual(options['control'].deadline-time.monotonic()-options['timeout'],89)
        self.assertIn('框架内补查最新消息',prompt)
        payload=json.loads(prompt.split('INPUT_JSON:\n')[1]);self.last_input=payload
        self.assertTrue(payload['supplemental_research']['enabled'])
        self.assertNotIn('合成产业资料',payload['supplemental_research']['public_search_anchors'])
        raw=self.outline(payload)
        raw['supplemental_sources']=[dict(material_id='news_policy',source_id='news_src_policy',
            title='合成政策原文',publisher='合成发布机构',url='https://example.org/new-policy',
            published_at='2026-09-16',excerpt=self.news,relation='补充已选设备主题的政策进展')]
        raw['research_summary']='合成检查：找到一项新政策。'
        raw['claims'].append(dict(id='c2',material_id='news_policy',source_id='news_src_policy',
            statement=self.news,quote=self.news,published_at='2026-09-16',publication_quote='',
            data_period='',kind='reported',supported=True,reason='对应新增原文'))
        raw['paragraphs'].append(dict(id='p2',purpose='新增政策事实',points=[self.news],analysis='',
            product_connection='',material_ids=['news_policy'],claim_ids=['c2']))
        raw['dispositions'].append(dict(material_id='news_policy',role='p2',reason='独立增量'))
        return HostResult(raw,{'input_tokens':100,'output_tokens':50},{'web_actions':2,'visited_urls':['https://example.org/new-policy']})

    def fetch(self,url,control,seconds):
        self.assertLessEqual(seconds,12)
        return dict(text=self.news,published_at='2026-09-16',title='合成原站标题',publisher='合成原站机构')

    def run_outline(self,runner=None,fetcher=None,**options):
        control=TaskControl();data=self.data(supplement_latest=True,**options)
        run=w.accept_run(self.conn,self.cfg,'outline',data,control)
        self.last_run=run
        return w.execute_run(self.conn,run['run_id'],control,runner=runner or self.discovery_runner,fetcher=fetcher or self.fetch)

    def test_default_search_single_task_then_confirmed_sources_flow_to_draft(self):
        before=deepcopy(ed.latest_decision(self.conn,self.eid))
        data=self.data();data.pop('supplement_latest');control=TaskControl()
        run=w.accept_run(self.conn,self.cfg,'outline',data,control)
        def with_large_audit(*args,**kwargs):
            response=self.discovery_runner(*args,**kwargs)
            response.trace['test_audit']='x'*24000
            return response
        outline=w.execute_run(self.conn,run['run_id'],control,runner=with_large_audit,fetcher=self.fetch)
        self.assertFalse(outline['checks']['errors'])
        self.assertEqual(w.task_attempts(self.conn),1)
        self.assertEqual(ed.latest_decision(self.conn,self.eid),before)
        self.assertEqual(outline['supplemental_research']['verified_sources'],1)
        self.assertEqual(outline['source_evidence'][-1]['title'],'合成原站标题')
        confirmed=self.confirm(outline)
        self.assertEqual(confirmed['snapshot']['supplemental_research']['approval'],'confirmed_with_outline')
        def draft_runner(*args,**kwargs):
            response=self.runner(*args,**kwargs)
            self.assertNotIn('supplemental_research',self.last_input)
            response.payload['paragraphs'].append(dict(text=self.news,claim_ids=['c2'],material_ids=['news_policy'],
                outline_paragraph_id='p2',outline_anchor='新增政策事实'))
            return response
        control=TaskControl()
        run=w.accept_run(self.conn,self.cfg,'draft',self.data(confirmed_version_id=confirmed['version_id']),control)
        draft=w.execute_run(self.conn,run['run_id'],control,runner=draft_runner)
        self.assertFalse(draft['checks']['errors'])
        exported=w.export_draft(self.conn,draft['version_id'])['markdown']
        for expected in ['合成原站标题','合成原站机构','2026-09-16','https://example.org/new-policy']:
            self.assertIn(expected,exported)

    def test_snippets_and_failed_fetches_cannot_become_confirmed_facts(self):
        outline=self.run_outline(fetcher=lambda *a:dict(error='source_timeout'))
        self.assertEqual(outline['supplemental_research']['status'],'partial')
        self.assertIn('unobserved_quote',[e['code'] for e in outline['checks']['errors']])
        self.assertTrue(outline['source_evidence'][0]['text_observed'])
        with self.assertRaisesRegex(ValueError,'依据'):
            self.confirm(outline)

    def test_observed_later_date_and_invented_quote_are_rejected(self):
        outline=self.run_outline(fetcher=lambda *a:dict(text='另一段合成文字',published_at='2026-09-17'))
        codes={e['code'] for e in outline['checks']['errors']}
        self.assertTrue({'unobserved_quote','publication_unverified'}<=codes)
        self.assertFalse(outline['content']['claims'][-1]['usable'])

    def test_exact_metadata_date_is_bound_without_weakening_cutoff(self):
        outline=self.run_outline(fetcher=lambda *a:dict(text=self.news,published_at='2026-09-16T10:00:00+08:00'))
        self.assertFalse(outline['checks']['errors'])
        self.assertEqual(outline['content']['claims'][-1]['published_at'],'2026-09-16T10:00:00+08:00')
        self.assertEqual(outline['source_evidence'][-1]['news_freshness'],'recent')

    def test_revision_keeps_prior_supplement_without_changing_manual_decision(self):
        original=self.run_outline()
        def revise(host,prompt,schema,**kw):
            payload=json.loads(prompt.split('INPUT_JSON:\n')[1])
            self.assertIn('news_policy',{m['id'] for m in payload['materials']})
            self.assertNotIn('paragraph_sources',payload['previous_outline'])
            for source in payload['sources']:
                for key in ['document_hash','fingerprint','document_id','evidence_id']:
                    self.assertNotIn(key,source)
                for key in ['id','material_id','title','name','url','text','text_observed']:
                    self.assertIn(key,source)
            raw={key:deepcopy(original['content'][key]) for key in w.OUTLINE['properties']}
            for claim in raw['claims']:
                claim.pop('usable',None);claim.pop('checks',None)
            raw.update(supplemental_sources=[],research_summary='复用上版已核对补充，无新发现')
            return HostResult(raw,{},dict(web_actions=0))
        newer=self.run_outline(runner=revise,parent_version_id=original['version_id'],feedback='保持新政策段')
        self.assertFalse(newer['checks']['errors'])
        self.assertEqual(len(newer['supplemental_materials']),1)
        self.assertEqual(ed.latest_decision(self.conn,self.eid)['decision_revision'],1)

    def test_compact_permission_brief_keeps_all_rules_and_blocked_paths(self):
        snapshot=w._snapshot(self.conn,self.cfg,self.eid,1)
        before=deepcopy(snapshot['source_constraints'])
        brief=research_brief(snapshot)['source_constraints']
        self.assertEqual(len(brief),len(before))
        for a,b in zip(before,brief):
            self.assertEqual(b,{k:v for k,v in a.items() if v is not None})
            for key in ['source_id','url','search_enabled','host_read_allowed']:self.assertEqual(a[key],b[key])
        self.assertEqual(snapshot['source_constraints'],before)

    def test_revision_does_not_retry_an_explicitly_blocked_original(self):
        original=self.run_outline(fetcher=lambda *a:dict(error='robots_disallowed'))
        snapshot=w.version(self.conn,original['version_id'])['snapshot']
        fetcher=Mock(side_effect=AssertionError('blocked original must not be retried'))
        w.verify_sources(snapshot,TaskControl(),fetcher=fetcher)
        fetcher.assert_not_called()
        self.assertEqual(snapshot['sources'][-1]['error'],'robots_disallowed')

    def test_source_collision_private_url_and_excess_count_fail_without_overwrite(self):
        for mutation in ['collision','private','excess']:
            with self.subTest(mutation=mutation):
                def changed(*args,**kwargs):
                    result=self.discovery_runner(*args,**kwargs)
                    item=result.payload['supplemental_sources'][0]
                    if mutation=='collision':item['source_id']='src_1'
                    elif mutation=='private':item['url']='http://127.0.0.1/private'
                    else:result.payload['supplemental_sources']*=7
                    return result
                fetcher=Mock(side_effect=AssertionError('must not fetch rejected input'))
                with self.assertRaises(ValueError):self.run_outline(runner=changed,fetcher=fetcher)
                fetcher.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_writing_versions').fetchone()[0],0)
        run=w.run_record(self.conn.execute('SELECT * FROM fn_writing_runs ORDER BY rowid DESC LIMIT 1').fetchone())
        self.assertIn('6份',run['error'])

    def test_disabled_web_and_opt_out_do_not_enable_search(self):
        from fastnews.source_catalog import save_profile, configured_view
        save_profile(self.conn,dict(profile_id='default',name='默认方案',host='codex',web_search=False))
        self.cfg=configured_view(self.conn,self.config)
        outline=self.run_outline(runner=self.runner,fetcher=Mock(side_effect=AssertionError('no fetch')))
        self.assertFalse(outline['supplemental_research']['enabled'])

    def test_cancel_after_model_retains_discovered_sources_and_raw_output(self):
        def stop(url,control,seconds):
            control.cancelled.set();control.check()
        with self.assertRaises(TaskStopped):self.run_outline(fetcher=stop)
        row=self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(self.last_run['run_id'],)).fetchone()
        saved=json.loads(row['input_json'])
        self.assertEqual(row['status'],'cancelled')
        self.assertEqual(saved['snapshot']['sources'][-1]['id'],'news_src_policy')
        self.assertTrue(saved['raw_failed_response']['supplemental_sources'])

    def test_verification_uses_shared_page_budget_across_before_and_after_search(self):
        snapshot=w._snapshot(self.conn,self.cfg,self.eid,1)
        snapshot['rules']['max_pages']=1
        template=dict(snapshot['sources'][0],text='',text_observed=False,date_observed=False)
        snapshot['sources']=[dict(template,id='s1',url='https://example.org/first')]
        fetcher=Mock(side_effect=self.fetch);budget={};control=TaskControl()
        w.verify_sources(snapshot,control,fetcher=fetcher,seconds=30,budget_state=budget)
        snapshot['sources'].append(dict(template,id='s2',url='https://example.org/second'))
        w.verify_sources(snapshot,control,fetcher=fetcher,seconds=30,source_ids=['s2'],budget_state=budget)
        self.assertEqual(fetcher.call_count,1)
        self.assertEqual(snapshot['sources'][-1]['error'],'verification_budget')

    def test_window_uses_shanghai_natural_dates_and_skills_freeze(self):
        snap=w._snapshot(self.conn,self.cfg,self.eid,1)
        snap['news_cutoff']='2026-09-17T00:01:00+08:00'
        self.assertEqual(research_brief(snap)['window_start'],'2026-09-11')
        self.assertIn('不得把 preferences',w.instructions('outline'))


if __name__=='__main__':unittest.main()
