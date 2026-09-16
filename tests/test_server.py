"""Local UI boundary tests using in-memory HTTP streams, with no socket/network."""
from contextlib import closing
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews import editorial
from fastnews.server import LocalApplication, make_server


class ServerTests(unittest.TestCase):
    def test_research_rejects_changed_product_instead_of_using_old_edition(self):
        status, body, _ = self.request('POST','/api/research',{'edition_id':self.eid,'product_code':'513130'})
        self.assertEqual(status,400)
        self.assertIn('产品',body['error'])
        self.assertIsNone(self.app.active_job)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_config()
        self.database = self.root / 'fixture.db'
        self.app = LocalApplication(self.database, self.config, offline=True, output_dir=self.root / 'exports')
        with closing(self.app.connection()) as conn:
            sync_registry(conn, self.config)
            self.edition = editorial.create_edition(conn, self.config, product_code='513530', edition_date='2026-09-15',
                news_cutoff='2026-09-14T23:59:59+08:00', market_cutoffs={'HK': {'date':'2026-09-14','source':'synthetic explicit date'}})
        self.eid = self.edition['edition_id']
        def fake_server(address, handler):
            self.bound_address = address
            return SimpleNamespace(server_port=8765, RequestHandlerClass=handler)
        with patch('fastnews.server.ThreadingHTTPServer', side_effect=fake_server), patch('socket.socket', side_effect=AssertionError('no sockets in tests')):
            self.server = make_server(self.app, 8765)

    def request(self, method, path, data=None, headers=None, raw=None):
        head = {'Host': '127.0.0.1:8765'}
        if method == 'POST':
            head.update({'Origin': 'http://127.0.0.1:8765', 'Content-Type':'application/json', 'X-Fastnews-CSRF':self.app.token})
        if data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        raw = raw or b''
        head['Content-Length'] = str(len(raw))
        if headers:
            for key, value in headers.items():
                if value is None:
                    head.pop(key, None)
                else:
                    head[key] = value
        request = f'{method} {path} HTTP/1.1\r\n'.encode('ascii')
        request += ''.join(f'{k}: {v}\r\n' for k, v in head.items()).encode('ascii') + b'\r\n' + raw
        handler = self.server.RequestHandlerClass.__new__(self.server.RequestHandlerClass)
        handler.server, handler.client_address = self.server, ('127.0.0.1', 9999)
        handler.rfile, handler.wfile = io.BytesIO(request), io.BytesIO()
        handler.close_connection = True
        with patch('socket.socket', side_effect=AssertionError('no network')), patch('socket.create_connection', side_effect=AssertionError('no network')):
            handler.handle_one_request()
        output = handler.wfile.getvalue()
        response_headers, content = output.split(b'\r\n\r\n', 1)
        status = int(response_headers.split(b' ', 2)[1])
        headers_out = dict(line.decode('latin1').split(': ', 1) for line in response_headers.split(b'\r\n')[1:])
        try:
            decoded = json.loads(content)
        except (ValueError, UnicodeError):
            decoded = content
        return status, decoded, headers_out

    def payload(self, statement='合成测试：规则将在次日实施。'):
        return {'is_fixture': True, 'title':'合成测试材料', 'content':statement,
                'first_published_at':'2026-09-14T18:00:00+08:00',
                'event': {'subject':'合成主体','action':'计划公告','object':statement,'phase':'announcement'},
                'facts':[{'statement':statement,'excerpt':statement,'kind':'plan','effective_at':'2026-09-16'}]}

    def upload(self, payload=None, **kwargs):
        data = {'kind':'evidence','edition_id':self.eid,'filename':'synthetic.json',
                'content':json.dumps(payload or self.payload(), ensure_ascii=False)}
        data.update(kwargs)
        return self.request('POST', '/api/import', data)

    def choose(self, imported, fact_ids=None, revision=0):
        return self.request('POST', '/api/decision', {'edition_id':self.eid, 'selected_event_id':imported['event_id'],
            'selected_fact_ids':fact_ids if fact_ids is not None else imported['fact_ids'], 'expected_revision':revision})

    def test_server_is_bound_to_loopback_only(self):
        self.assertEqual(self.bound_address, ('127.0.0.1', 8765))

    def test_http_state_is_local_with_security_headers(self):
        status, body, headers = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(body['csrf_token'], self.app.token)
        self.assertTrue(body['is_fixture'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_daily_endpoint_reads_without_creating_edition(self):
        from fastnews.pipeline import scan_morning
        with closing(self.app.connection()) as conn:
            before = conn.execute('SELECT count(*) FROM fn_editions').fetchone()[0]
            scan_morning(conn, self.config, offline=True)
        status, result, _ = self.request('GET', '/api/daily?date=2026-09-14')
        self.assertEqual(status, 200)
        self.assertEqual(result['date'], '2026-09-14')
        self.assertTrue(result['is_fixture'])
        self.assertGreater(result['counts']['items'], 0)
        self.assertFalse(result['coverage']['complete'])
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_editions').fetchone()[0], before)
            self.assertIsNone(editorial.latest_decision(conn, self.eid))

    def test_bad_daily_dates_and_scan_dates_rejected_before_job(self):
        for day in ('2099-01-01', '2026-02-30', '20260914', ''):
            with self.subTest(day=day):
                self.assertEqual(self.request('GET', '/api/daily?date='+day)[0], 400)
                self.assertEqual(self.request('POST', '/api/scan', {'date':day})[0], 400)
                self.assertIsNone(self.app.active_job)
        self.assertEqual(self.request('POST', '/api/scan', {'date':None})[0], 400)

    def test_selected_date_passes_to_scan_and_keeps_decision(self):
        _, imported, _ = self.upload()
        _, saved, _ = self.choose(imported)
        with patch('fastnews.server.threading.Thread.start'):
            status, job, _ = self.request('POST', '/api/scan', {'date':'2026-09-14'})
        self.assertEqual(status, 200)
        jid=job['job_id']
        with patch('fastnews.server.scan_morning', return_value={'articles_new':0}) as scan:
            self.app._run_job(jid, 'scan', {'date':'2026-09-14'})
        self.assertEqual(scan.call_args.kwargs['collection_date'], '2026-09-14')
        status, result, _ = self.request('GET', '/api/job?id='+jid)
        self.assertEqual(status, 200)
        self.assertEqual(result['date'], '2026-09-14')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['articles_new'], 0)
        with closing(self.app.connection()) as conn:
            self.assertEqual(editorial.latest_decision(conn,self.eid), saved)

    def test_daily_page_primary_date_control_and_independent_editor(self):
        status, body, _ = self.request('GET', '/')
        self.assertEqual(status, 200)
        page=body.decode('utf-8')
        self.assertIn('id="news-day"', page)
        self.assertIn('获取当天信息', page)
        self.assertIn('id="product-workspace" class="workspace-screen" role="tabpanel" aria-labelledby="tab-product" hidden', page)
        self.assertLess(page.index('id="morning-workspace"'),page.index('id="product-workspace"'))
        self.assertLess(page.index('id="product-workspace"'),page.index('id="materials-workspace"'))
        self.assertIn('原文核对与事实摘录（可选）',page)

    def test_retry_rejects_unimplemented_source_without_scheduling(self):
        with self.assertRaisesRegex(ValueError,'可重试'):
            self.app.mutate('scan',{'source_id':'cde','date':'2026-09-15'})
        self.assertIsNone(self.app.active_job)

    def test_bad_hosts_rejected_including_rebinding_domains(self):
        for host in ('evil.example:8765', '127.0.0.1.evil.example:8765', '127.0.0.1:80', None):
            with self.subTest(host=host):
                self.assertEqual(self.request('GET','/api/state',headers={'Host':host})[0],403)
        self.assertEqual(self.request('GET','/api/state',headers={'Host':'localhost:8765'})[0],200)

    def test_cross_site_fetch_and_origin_rejected(self):
        self.assertEqual(self.request('GET','/api/state',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.request('POST','/api/decision',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('GET','/api/state',headers={'Origin':'https://evil.example'})[0],403)

    def test_missing_wrong_csrf_cannot_mutate(self):
        for token in (None, 'incorrect'):
            self.assertEqual(self.request('POST','/api/decision',{},headers={'X-Fastnews-CSRF':token})[0],403)
        with closing(self.app.connection()) as conn:
            self.assertIsNone(editorial.latest_decision(conn,self.eid))

    def test_wrong_content_type_empty_or_oversized_request_rejected(self):
        self.assertEqual(self.request('POST','/api/edition',{},headers={'Content-Type':'text/plain'})[0],400)
        self.assertEqual(self.request('POST','/api/edition',raw=b'')[0],400)
        self.assertEqual(self.request('POST','/api/edition',raw=b'{}',headers={'Content-Length':'2100001'})[0],400)

    def test_upload_reads_submitted_content_and_keeps_private(self):
        status, result, _ = self.upload()
        self.assertEqual(status,200)
        self.assertEqual(len(result['fact_ids']),1)
        status, detail, _ = self.request('GET','/api/edition?id='+self.eid)
        self.assertEqual(status,200)
        self.assertEqual(detail['evidence'][0]['privacy_class'],'private_local')
        self.assertEqual(detail['facts'][0]['statement'], self.payload()['content'])

    def test_pasted_text_is_explicit_upload_not_auto_extracted_claim(self):
        status, result, _ = self.upload(filename='pasted.txt',content='实际提交的合成纯正文。')
        self.assertEqual(status,200)
        self.assertEqual(result['fact_ids'],[])
        with closing(self.app.connection()) as conn:
            self.assertEqual(editorial.edition_detail(conn,self.eid)['evidence'][0]['content'],'实际提交的合成纯正文。')

    def test_secret_upload_refused_without_leaking_value(self):
        secret = 'syntheticsecret012345678901234'
        status, error, _ = self.upload(filename='notes.txt',content='API_KEY='+secret)
        self.assertEqual(status,400)
        self.assertNotIn(secret,json.dumps(error))
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_documents').fetchone()[0],0)

    def test_web_input_never_reads_arbitrary_server_path(self):
        arbitrary = self.root/'never-read.txt'
        arbitrary.write_text('SERVER_SIDE_PRIVATE_SENTINEL',encoding='utf-8')
        status, _, _ = self.request('POST','/api/import',{'kind':'evidence','edition_id':self.eid,'path':str(arbitrary)})
        self.assertEqual(status,400)
        status, result, _ = self.upload(filename='pasted.txt',content='ONLY_SUBMITTED_CONTENT',path=str(arbitrary))
        self.assertIn(status,(200,400))  # An unused path may be rejected or ignored, never read.
        with closing(self.app.connection()) as conn:
            stored = '\n'.join(r[0] for r in conn.execute('SELECT content FROM fn_documents'))
            self.assertNotIn('SERVER_SIDE_PRIVATE_SENTINEL',stored)
        for filename in ('../private.txt','..\\private.txt',str(arbitrary)):
            self.assertEqual(self.upload(filename=filename,content='text')[0],400)

    def test_nested_json_duplicate_keys_and_nonfinite_refused(self):
        duplicate = '{"is_fixture":false,"is_fixture":true,"title":"test","content":"test"}'
        self.assertEqual(self.upload(content=duplicate)[0],400)
        self.assertEqual(self.upload(content='{"title":"test","content":"test","is_fixture":true,"note":NaN}')[0],400)

    def test_fixture_live_documents_not_mixed(self):
        real = self.payload()
        real['is_fixture']=False
        self.assertEqual(self.upload(real)[0],400)
        live = LocalApplication(self.root/'live.db',self.config,offline=False,output_dir=self.root/'live-output')
        with closing(live.connection()) as conn:
            sync_registry(conn,self.config)
            ed = editorial.create_edition(conn,self.config,product_code='513530',edition_date='2026-09-15',
                news_cutoff='2026-09-14T23:59:59+08:00',market_cutoffs={})
        with self.assertRaises(ValueError):
            live.mutate('import',{'kind':'evidence','edition_id':ed['edition_id'],'filename':'test.json','content':json.dumps(self.payload())})

    def test_active_job_blocks_selection_without_changing_prior_state(self):
        _, imported, _ = self.upload()
        _, saved, _ = self.choose(imported)
        with patch('fastnews.server.threading.Thread.start'):
            status, job, _ = self.request('POST','/api/scan',{})
        self.assertEqual(status,200)
        self.assertEqual(self.app.active_job,job['job_id'])
        self.assertEqual(self.choose(imported,revision=1)[0],400)
        self.assertEqual(self.request('POST','/api/export',{'edition_id':self.eid,'decision_revision':1})[0],400)
        with closing(self.app.connection()) as conn:
            self.assertEqual(editorial.latest_decision(conn,self.eid),saved)

    def test_job_failure_is_recorded_and_unlocks(self):
        self.app.active_job='test-job'
        self.app.jobs['test-job']={'job_id':'test-job','status':'running','progress':[]}
        with patch('fastnews.server.scan_morning',side_effect=ValueError('synthetic failure')):
            self.app._run_job('test-job','scan',{})
        self.assertIsNone(self.app.active_job)
        self.assertEqual(self.app.jobs['test-job']['status'],'failed')
        self.assertEqual(self.app.jobs['test-job']['error'],'synthetic failure')

    def test_offline_probe_cannot_start_live_access(self):
        self.assertEqual(self.request('POST','/api/probe',{})[0],400)
        self.assertIsNone(self.app.active_job)

    def test_human_selection_saved_reload_order_kept_and_stale_revision_refused(self):
        _, first, _ = self.upload()
        _, second, _ = self.upload(self.payload('合成测试：另一项计划已对外公布。'))
        order = second['fact_ids'] + first['fact_ids']
        status, saved, _ = self.choose(second,fact_ids=order)
        self.assertEqual(status,200)
        self.assertEqual(saved['actor_type'],'human')
        other_app = LocalApplication(self.database,self.config,offline=True,output_dir=self.root/'exports')
        with closing(other_app.connection()) as conn:
            self.assertEqual(editorial.latest_decision(conn,self.eid)['selected_fact_ids'],order)
            editorial.assess_events(conn,self.config,edition_id=self.eid)
            self.assertEqual(editorial.latest_decision(conn,self.eid),saved)
        self.assertEqual(self.choose(first,revision=0)[0],400)
        status, reordered, _ = self.choose(second,fact_ids=list(reversed(order)),revision=1)
        self.assertEqual(status,200)
        self.assertEqual(reordered['selected_fact_ids'],list(reversed(order)))

    def test_no_selection_export_refused_and_pending_intention_supported(self):
        self.assertEqual(self.request('POST','/api/export',{'edition_id':self.eid,'decision_revision':1})[0],400)
        _, imported, _ = self.upload(filename='pending.txt',content='只读正文，还没有结构化事实。')
        self.assertEqual(self.choose(imported)[0],200)
        status, result, _ = self.request('POST','/api/export',{'edition_id':self.eid,'decision_revision':1})
        self.assertEqual(status,200)
        pack=json.loads(Path(result['paths']['json']).read_text(encoding='utf-8'))
        self.assertEqual(pack['usable_facts'],[])
        self.assertTrue(pack['not_for_body'])

    def test_download_only_registered_output_and_known_format(self):
        arbitrary=self.root/'private.txt'
        arbitrary.write_text('NEVER_DOWNLOAD',encoding='utf-8')
        self.assertEqual(self.request('GET','/api/download?id=../../private.txt&kind=json')[0],400)
        self.assertEqual(self.request('GET','/api/download?path='+str(arbitrary))[0],400)
        _, imported, _=self.upload()
        self.choose(imported)
        _, output, _=self.request('POST','/api/export',{'edition_id':self.eid,'decision_revision':1})
        status, body, headers=self.request('GET',f"/api/download?id={output['export_id']}&kind=json")
        self.assertEqual(status,200)
        self.assertEqual(body['artifact_type'],'writing_handoff_materials_not_news')
        self.assertIn('attachment;',headers['Content-Disposition'])
        self.assertEqual(self.request('GET',f"/api/download?id={output['export_id']}&kind=private.txt")[0],400)
        app_other_dir=LocalApplication(self.database,self.config,offline=True,output_dir=self.root/'different-output')
        with self.assertRaises(ValueError):
            app_other_dir.download(output['export_id'],'json')

    def test_http_human_excerpt_rejects_fabricated_quote(self):
        _, imported, _=self.upload(filename='plain.txt',content='合成原文片段。',first_published_at='2026-09-14T18:00:00+08:00')
        data={'edition_id':self.eid,'event_id':imported['event_id'],'evidence_id':imported['evidence_ids'][0],
              'statement':'原文不存在的内容。','kind':'reported'}
        self.assertEqual(self.request('POST','/api/fact',data)[0],400)
        data['statement']='合成原文片段。'
        status,result,_=self.request('POST','/api/fact',data)
        self.assertEqual(status,200)
        with closing(self.app.connection()) as conn:
            self.assertEqual(editorial.edition_facts(conn,self.eid)[0]['fact_id'],result['fact_id'])


if __name__=='__main__':
    unittest.main()
