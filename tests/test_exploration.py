"""Meaningful offline checks: time/roles, incremental evidence and editing races."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastnews import db, editorial, product_reading
from fastnews.host_provider import HostResult, run_host
from fastnews.reading_reports import build_input, generate_report, validate_report, adopt_report_item
from fastnews.server import LocalApplication
from fastnews.task_control import TaskControl, TaskStopped
from tests import test_writing_workflow as fixtures
from tests.test_targeted_search import source, report_raw


def raw(role='recent', url='https://www.fda.gov/drugs/public-test', date='2026-09-14', **changes):
    src=source(url)
    src.update(published_at=date,first_published_at=date)
    result=report_raw(src)
    result['items'][0].update(editorial_role=role,why_now='合成：对应本期仍适用的产品观察变量',
        validity_state='active',validity_reason='合成：在本期截点前未被后续结果替代',
        newness_hint='new_event',related_research_identity=None)
    result['items'][0].update(changes)
    return result


TRACE={'web_actions':1,'visited_urls':[], 'actions':[{'type':'search','queries':['创新药 临床 最新进展']}]}


class ExplorationTests(unittest.TestCase):
    setUp=fixtures.WritingWorkflowTests.setUp
    make_edition=fixtures.WritingWorkflowTests.make_edition
    view=fixtures.WritingWorkflowTests.view
    save=fixtures.WritingWorkflowTests.save
    export=fixtures.WritingWorkflowTests.export

    def generate(self, value, **kwargs):
        runner=Mock(return_value=HostResult(deepcopy(value),{'is_fixture':True},deepcopy(TRACE)))
        report=generate_report(self.conn,self.view(),'default','product','2026-09-15',
            edition=self.edition,runner=runner,**kwargs)
        return report,runner

    def test_all_five_products_accept_older_non_statistical_background(self):
        for code in self.config['products']:
            edition=self.make_edition(code)
            payload=build_input(self.conn,self.view(),'default','product','2026-09-15',edition)
            self.assertGreaterEqual(len(payload['search_plan']['observation_queries']),5)
            result=validate_report(raw('background',date='2025-02-01'),payload,TRACE)
            item=result['items'][0]
            self.assertEqual(item['editorial_role'],'background')
            self.assertEqual(item['sources'][0]['first_published_at'],'2025-02-01')
            self.assertIsNone(item['sources'][0]['data_period_end'])
            self.assertFalse(item['fact_verified'])

    def test_recency_and_background_have_independent_caps_and_future_is_rejected(self):
        combined={'items':[],'web_sources':[],'coverage':[],'gaps':[]}
        for i in range(15):
            value=raw('recent' if i<10 else 'background',url=f'https://www.fda.gov/drugs/test-{i}')
            value['web_sources'][0]['id']=f'web_{i}'
            value['items'][0]['source_refs']=[f'web_{i}']
            value['items'][0]['claims'][0]['source_ref']=f'web_{i}'
            value['items'][0]['event_identity']['object']=f'Trial {i}'
            combined['items']+=value['items'];combined['web_sources']+=value['web_sources']
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',self.edition)
        output=validate_report(combined,payload,TRACE)
        self.assertEqual([sum(i['editorial_role']==role for i in output['items']) for role in ('recent','background')],[10,5])
        self.assertEqual(validate_report(raw('background',date='2026-09-16'),payload,TRACE)['items'],[])

    def test_exploration_is_global_with_frozen_cutoff_and_no_personal_notes(self):
        first,_=self.generate(raw('background'))
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',self.edition,
                            request_mode='explore',research_question='更多支付准入线索')
        self.assertEqual(payload['selected_sources'],[])
        self.assertEqual(payload['search_plan']['scope'],'global')
        self.assertEqual(payload['search_plan']['queries'],[])
        self.assertTrue(all(not a['domains'] for a in payload['search_plan']['observation_queries']))
        self.assertEqual(payload['news_cutoff'],self.edition['news_cutoff'])
        self.assertTrue(payload['known_events'])
        self.assertTrue(payload['previous_searches'])
        self.assertNotIn('manual_notes',payload)
        self.assertEqual(payload['task_limits']['max_product_items'],60)

    def test_repeated_titles_claim_paraphrases_and_snippets_are_not_incremental(self):
        original=raw();original['web_sources'][0]['material_kind']='search_snippet'
        first,_=self.generate(original)
        repeated=deepcopy(original)
        repeated['items'][0]['title']='换标题再次报道'
        repeated['items'][0]['claims'][0]['statement']='以另一个表述重复同一试验进展'
        second,runner=self.generate(repeated,request_mode='explore',request_id='repeat-test-1')
        self.assertEqual(second['output']['items'],[])
        self.assertEqual(second['status'],'complete')
        self.assertEqual(runner.call_count,1)
        view=product_reading.aggregate_reports(self.conn,'default',self.eid)
        self.assertEqual(len(view['recent']),1)
        self.assertEqual(view['recent'][0]['title'],first['output']['items'][0]['title'])

    def test_new_evidence_appends_without_changing_saved_selection_or_old_export(self):
        first,_=self.generate(raw('background'))
        event=adopt_report_item(self.conn,'default',first['report_id'],'item_1',self.eid)['event_id']
        self.save([event]);old_export,_,old_md=self.export()
        before=self.conn.execute('SELECT snapshot_json FROM fn_candidate_pool').fetchone()[0]
        update=raw('recent',url='https://www.fda.gov/drugs/new-public-evidence')
        update['items'][0]['newness_hint']='new_evidence'
        update['web_sources'][0]['excerpt']='Synthetic additional evidence.'
        update['items'][0]['claims'][0].update(statement='合成新增依据',quote='Synthetic additional evidence.')
        second,_=self.generate(update,request_mode='explore',request_id='new-evidence-1')
        self.assertEqual(len(second['output']['items']),1)
        self.assertEqual(self.conn.execute('SELECT snapshot_json FROM fn_candidate_pool').fetchone()[0],before)
        view=product_reading.aggregate_reports(self.conn,'default',self.eid)
        self.assertEqual(len(view['background']),1)
        self.assertEqual(len(view['background'][0]['sources']),2)
        self.assertTrue(view['background'][0]['latest_increment'])
        adopted=adopt_report_item(self.conn,'default',second['report_id'],'item_1',self.eid)
        self.assertEqual(adopted['event_id'],event)
        self.assertEqual(self.export()[2],old_md)
        self.assertEqual(Path(old_export['paths']['markdown']).read_text(encoding='utf-8'),old_md)

    def test_new_phase_is_separate_and_related_identity_must_exist(self):
        first,_=self.generate(raw())
        update=raw(related_research_identity=first['output']['items'][0]['research_identity'])
        update['items'][0]['newness_hint']='new_stage'
        update['items'][0]['event_identity']['phase']='result'
        second,_=self.generate(update,request_mode='explore',request_id='new-stage-1')
        self.assertEqual(second['output']['items'][0]['incremental_type'],'new_stage')
        self.assertEqual(len(product_reading.aggregate_reports(self.conn,'default',self.eid)['recent']),2)
        update['items'][0]['related_research_identity']='invented'
        with self.assertRaisesRegex(ValueError,'前序事件'):
            self.generate(update,request_mode='explore',request_id='bad-related-1')

    def test_cross_edition_background_respects_original_cutoff(self):
        first,_=self.generate(raw('background',date='2026-09-01'))
        later=editorial.create_edition(self.conn,self.config,product_code='513130',edition_date='2026-09-15',
            news_cutoff='2026-09-15T01:00:00+08:00',market_cutoffs={})
        payload=build_input(self.conn,self.view(),'default','product','2026-09-15',later)
        self.assertTrue(payload['carried_background'])
        earlier=editorial.create_edition(self.conn,self.config,product_code='513130',edition_date='2026-09-15',
            news_cutoff='2026-09-10T23:59:59+08:00',market_cutoffs={})
        historical=build_input(self.conn,self.view(),'default','product','2026-09-15',earlier)
        self.assertEqual(historical['carried_background'],[])

    def test_old_announcement_needs_a_dated_new_source_for_recent_role(self):
        value=raw('recent',date='2026-08-01')
        value['items'][0]['event_identity']['phase']='implementation'
        result=validate_report(value,build_input(self.conn,self.view(),'default','product','2026-09-15',self.edition),TRACE)
        item=result['items'][0]
        self.assertEqual(item['editorial_role'],'background')
        self.assertEqual(item['time_role'],'background')
        self.assertFalse(item['title'].startswith('背景｜'))
        self.assertEqual(item['sources'][0]['first_published_at'],'2026-08-01')

    def test_captured_reader_settings_survive_later_profile_changes(self):
        from fastnews.source_catalog import profile,save_profile
        captured=self.view()
        captured['_reader_profile']=profile(self.conn,'default')
        save_profile(self.conn,{'profile_id':'default','name':'合成设置变化','host':'none','web_search':False})
        runner=Mock(return_value=HostResult(raw(),{},TRACE))
        report=generate_report(self.conn,captured,'default','product','2026-09-15',edition=self.edition,
            runner=runner,request_mode='explore',request_id='snapshot-settings')
        self.assertEqual(report['status'],'complete')
        self.assertEqual(runner.call_args.args[0],captured['_reader_profile']['settings']['host'])
        self.assertTrue(runner.call_args.kwargs['web_search'])

    def test_cancelled_and_late_results_never_commit_new_material(self):
        self.generate(raw())
        control=TaskControl()
        def delayed(*args,**kwargs):
            control.cancelled.set()
            return HostResult(raw(),{},TRACE)
        with self.assertRaises(TaskStopped):
            generate_report(self.conn,self.view(),'default','product','2026-09-15',edition=self.edition,
                runner=delayed,request_mode='explore',request_id='cancel-after-output',control=control)
        row=self.conn.execute('SELECT status,error_code,output_json FROM fn_reading_reports ORDER BY rowid DESC LIMIT 1').fetchone()
        self.assertEqual(tuple(row)[:2],('failed','cancelled'))
        self.assertIn('raw_model_output',json.loads(row[2]))
        self.assertEqual(len(product_reading.aggregate_reports(self.conn,'default',self.eid)['recent']),1)

    def test_restart_marks_running_report_interrupted(self):
        first,_=self.generate(raw())
        with self.conn:
            self.conn.execute("UPDATE fn_reading_reports SET status='running' WHERE report_id=?",(first['report_id'],))
        LocalApplication(self.path,self.config,offline=True)
        row=self.conn.execute('SELECT status,error_code FROM fn_reading_reports').fetchone()
        self.assertEqual(tuple(row),('failed','interrupted'))

    def test_request_identity_and_completion_survive_server_restart(self):
        report,_=self.generate(raw(),request_mode='explore',request_id='durable-request',task_id='original-job')
        app=LocalApplication(self.path,self.config,offline=True)
        result=app.mutate('explore',{'edition_id':self.eid,'request_id':'durable-request'})
        self.assertEqual(result,{'job_id':'original-job'})
        job=app.jobs['original-job']
        self.assertEqual(job['status'],'completed')
        self.assertIsNone(job['error'])
        self.assertTrue(job['deadline_at'])
        self.assertEqual(job['news_cutoff'],report['news_cutoff'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_reading_reports').fetchone()[0],1)

    def test_deadline_is_shared_and_reserves_cleanup(self):
        with patch('fastnews.task_control.time.monotonic',return_value=100):
            control=TaskControl(480)
        with patch('fastnews.task_control.time.monotonic',return_value=130):
            self.assertEqual(control.host_timeout(),420)
        with patch('fastnews.task_control.time.monotonic',return_value=560):
            with self.assertRaises(TaskStopped):control.host_timeout()
        with patch('fastnews.task_control.time.monotonic',return_value=581):
            with self.assertRaises(TaskStopped):control.check()

    def test_real_host_adapter_cancellation_closes_process_group(self):
        control=TaskControl()
        process=Mock(returncode=0)
        def wait(*args,**kwargs):
            control.cancelled.set()
            raise subprocess.TimeoutExpired('fixture-host',0.5)
        process.communicate.side_effect=wait
        process.poll.return_value=None
        group=Mock()
        with patch('fastnews.host_provider.host_command',return_value=['fixture-host']), \
             patch('fastnews.host_provider.subprocess.Popen',return_value=process), \
             patch('fastnews.host_provider.ProcessGroup',return_value=group):
            with self.assertRaises(TaskStopped):
                run_host('workbuddy','synthetic',{},control=control)
        group.close.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_called_once()

    @unittest.skipUnless(os.name=='nt','Windows job-object containment acceptance')
    def test_slow_real_process_and_child_are_cleaned_at_host_deadline(self):
        # No model/network: run the actual adapter with a deliberately slow
        # Python parent and child. Compress the host allowance to two seconds.
        pidfile=self.root/'slow-processes.json'
        code=('import subprocess,sys,time,os,json; from pathlib import Path; '
              'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],creationflags=0x08000000); '
              f'Path({str(pidfile)!r}).write_text(json.dumps([os.getpid(),child.pid])); time.sleep(60)')
        control=TaskControl(32)
        started=time.monotonic()
        with patch('fastnews.host_provider.host_command',return_value=[sys.executable,'-c',code]):
            with self.assertRaises(TaskStopped) as caught:
                run_host('workbuddy','synthetic',{},control=control)
        self.assertEqual(caught.exception.code,'timed_out')
        self.assertLess(time.monotonic()-started,8)
        self.assertTrue(pidfile.exists())
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        for pid in json.loads(pidfile.read_text()):
            handle=kernel.OpenProcess(0x100000,False,pid)
            if handle:
                try:self.assertEqual(kernel.WaitForSingleObject(handle,1000),0)
                finally:kernel.CloseHandle(handle)

    def test_live_mode_server_allows_editing_during_explore_and_reuses_request(self):
        database=self.root/'concurrency.live.db'
        app=LocalApplication(database,self.config,output_dir=self.root/'live-exports')
        with app.connection() as conn:
            self.addCleanup(conn.close)
            db.sync_registry(conn,self.config)
            edition=editorial.create_edition(conn,self.config,product_code='513530',edition_date='2026-09-15',
                news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        ready,release=threading.Event(),threading.Event()
        def research(*args,**kwargs):
            ready.set();release.wait(5)
            kwargs['control'].check()
            return {'output':{'items':[]}}
        request={'edition_id':edition['edition_id'],'request_id':'concurrent-request','research_question':'合成公开问题'}
        with patch('fastnews.reading_reports.generate_report',side_effect=research) as generator:
            job=app.mutate('explore',request)
            self.assertTrue(ready.wait(5))
            self.assertEqual(app.mutate('explore',request),job)
            with self.assertRaises(ValueError):app.mutate('explore',dict(request,request_id='different-request'))
            imported=app.mutate('import',{'kind':'evidence','edition_id':edition['edition_id'],'filename':'note.txt',
                'content':'并发测试用户本地材料，不发送到宿主','first_published_at':'2026-09-14'})
            saved=app.mutate('decision',{'edition_id':edition['edition_id'],'selected_clue_ids':[imported['event_id']],
                'expected_revision':0,'manual_notes':[]})
            exported=app.mutate('export',{'edition_id':edition['edition_id'],'decision_revision':saved['decision_revision']})
            self.assertTrue(Path(exported['paths']['markdown']).is_file())
            app.mutate('job-stop',job);release.set()
            until=time.monotonic()+5
            while app.active_job and time.monotonic()<until:time.sleep(0.01)
            self.assertEqual(app.jobs[job['job_id']]['status'],'cancelled')
            self.assertIsNone(app.active_job)
            self.assertEqual(generator.call_count,1)
