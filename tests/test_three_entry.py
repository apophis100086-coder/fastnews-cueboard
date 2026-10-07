"""Contract tests use disposable data and synthetic responses; no paid requests."""
import json
from contextlib import closing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from fastnews import api_connection as c, api_provider as p
from fastnews.agent_runner import pending_requests,claim_request,submit_response,BINDING
from fastnews.config import load_config
from fastnews.execution import is_agent,preflight
from fastnews.host_provider import HostUnavailable
from fastnews.server import LocalApplication
from fastnews.source_catalog import save_profile,profile
from fastnews.task_control import TaskControl,TaskStopped
from fastnews.workspace import identity


class APIContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'connections'
        self.data=dict(base_url='https://api.example.com/v1',model='test-model',api_key='synthetic-fixture-key',
            protocol='chat_completions',web_search=False,authorized=True,max_requests=2,max_output_tokens=6000)
        self.crypto=patch.object(c,'protect',side_effect=lambda v,decrypt=False:v.removeprefix('protected:') if decrypt else 'protected:'+v)
        self.crypto.start();self.addCleanup(self.crypto.stop)
        c.save(self.data,self.root)

    def response(self,**extra):
        return dict(choices=[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],model='reported-model',
                    usage={'prompt_tokens':12,'completion_tokens':3},**extra)

    def test_secret_not_returned_and_changed_endpoint_needs_new_key(self):
        public=c.public_status(self.root)
        self.assertNotIn('api_key',public);self.assertNotIn('protected_key',public)
        self.assertNotIn(self.data['api_key'],json.dumps(public))
        with self.assertRaisesRegex(ValueError,'重新填写'):
            c.save(dict(self.data,base_url='https://another.example.com/v1',api_key=''),self.root)
        self.assertEqual(c.load(self.root)['base_url'],self.data['base_url'])

    def test_plain_compatible_api_never_claims_web_and_no_retries(self):
        send=Mock(return_value=self.response());run=p.make_runner(self.root,sender=send)
        with self.assertRaises(HostUnavailable):run('api','test',{'type':'object'},web_search=True)
        self.assertEqual(send.call_count,0)
        r=run('api','test',{'type':'object'});self.assertEqual(r.usage['input_tokens'],12)
        self.assertIsNone(r.usage['cost']);self.assertEqual(r.trace['web_actions'],0)
        run('api','test',{'type':'object'})
        with self.assertRaisesRegex(HostUnavailable,'上限'):run('api','test',{'type':'object'})
        self.assertEqual(send.call_count,2)

    def test_failure_counts_against_request_limit_and_cancel_prevents_send(self):
        send=Mock(side_effect=HostUnavailable('synthetic failure'));run=p.make_runner(self.root,sender=send)
        control=TaskControl();control.cancelled.set()
        with self.assertRaises(TaskStopped):run('api','test',{},control=control)
        self.assertEqual(send.call_count,0)
        for _ in range(2):
            with self.assertRaises(HostUnavailable):run('api','test',{})
        with self.assertRaisesRegex(HostUnavailable,'上限'):run('api','test',{})
        self.assertEqual(send.call_count,2)

    def test_invalid_json_truncation_refusal_and_secret_echo(self):
        for content,reason in [('{','stop'),('{"ok":true}','length'),(None,'stop')]:
            raw=self.response();raw['choices'][0].update(finish_reason=reason,message={'content':content})
            with self.assertRaises(HostUnavailable):p.parse_response(raw,c.load(self.root))
        raw=self.response();raw['choices'][0]['message']['content']=json.dumps({'value':self.data['api_key']})
        r=p.make_runner(self.root,sender=Mock(return_value=raw))('api','test',{})
        self.assertNotIn(self.data['api_key'],json.dumps(r.payload))

    def test_responses_tool_actions_are_not_original_text(self):
        c.save(dict(self.data,protocol='responses',web_search=True),self.root)
        settings=c.load(self.root)
        path,body=p.build_request(settings,'test',{'type':'object'},True)
        self.assertEqual(path,'/responses');self.assertEqual(body['max_tool_calls'],8);self.assertFalse(body['store'])
        raw=dict(status='completed',output=[dict(type='web_search_call',id='call',status='completed',
                 action={'type':'open_page','url':'https://example.com/news'}),
                 dict(type='message',content=[dict(type='output_text',text='{"ok":true}')])])
        r=p.parse_response(raw,settings)
        self.assertEqual(r.trace['visited_urls'],['https://example.com/news']);self.assertEqual(r.trace['tool_excerpts'],[])

    def test_public_only_url_and_credential_redirect_blocked(self):
        for url in ['http://api.example.com','https://127.0.0.1','https://[::1]','https://localhost/v1',
                    'https://example.com/v1?key=secret','https://user:password@example.com/v1','https://api.example.com/v1/responses']:
            with self.assertRaises(ValueError):c.validate_base(url)
        conn=Mock();conn.getresponse.return_value.status=302
        with patch('fastnews.network.resolve_public',return_value=('https://api.example.com/v1/chat/completions',['93.184.216.34'])),\
             patch('fastnews.network._PinnedHTTP',return_value=conn):
            with self.assertRaises(HostUnavailable):p.post_json('https://api.example.com/v1/chat/completions','fixture',{},2)
        self.assertEqual(conn.request.call_count,1);conn.close.assert_called_once()

    def test_missing_authorization_and_web_preflight(self):
        c.save(dict(self.data,authorized=False),self.root)
        with self.assertRaises(HostUnavailable):p.make_runner(self.root)
        c.save(self.data,self.root)
        with self.assertRaises(HostUnavailable):preflight({'host':'api','web_search':True},self.root)


@unittest.skipUnless(os.name=='nt','Windows credential protection')
class WindowsCredentialTests(unittest.TestCase):
    def test_current_account_can_decrypt_but_disk_is_not_plaintext(self):
        protected=c.protect('synthetic-only-key')
        self.assertNotIn('synthetic-only-key',protected)
        self.assertEqual(c.protect(protected,decrypt=True),'synthetic-only-key')


class EntryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'data/fastnews.db';self.db.parent.mkdir()
        self.app=LocalApplication(self.db,load_config(),output_dir=Path(self.tmp.name)/'exports')

    def wait_request(self):
        for _ in range(300):
            items=pending_requests(self.db.parent/'agent-exchange')
            if items:return items[0]
            time.sleep(.01)
        self.fail('No request appeared')

    def test_api_server_probe_uses_configured_transport_and_records_real_shape(self):
        root=self.db.parent/'connections'
        data=dict(base_url='https://api.example.com/v1',model='fixture-model',api_key='synthetic-only-key',
                  protocol='chat_completions',authorized=True,web_search=False)
        with patch('fastnews.api_connection.protect',side_effect=lambda v,decrypt=False:v):
            self.app.mutate('api-settings',data)
            self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host='api',web_search=False))
            raw=dict(model='fixture-model',choices=[dict(finish_reason='stop',message={'content':'{"ok":true}'})],usage={'prompt_tokens':12,'completion_tokens':4})
            with patch('fastnews.api_provider.transport',return_value=raw) as send:
                job=self.app.mutate('connection-check',{})
                for _ in range(300):
                    if not self.app.active_job:break
                    time.sleep(.01)
                self.assertEqual(self.app.jobs[job['job_id']]['status'],'completed')
                self.assertEqual(send.call_args.args[1],'/chat/completions')
                self.assertEqual(c.public_status(root)['model_status'],'available')
                self.assertNotIn('synthetic-only-key',json.dumps(self.app.state()))

    def test_damaged_api_config_does_not_hide_user_database(self):
        path=self.db.parent/'connections/api.json';path.parent.mkdir()
        path.write_text('{broken',encoding='utf-8')
        state=self.app.state()
        self.assertFalse(state['execution']['api']['configured'])
        self.assertIn('重新填写',state['execution']['api']['last_error'])

    def test_two_agent_entries_through_server_and_kept_profiles(self):
        for host in ('workbuddy','codex'):
            self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host=host,web_search=True))
            job=self.app.mutate('connection-check',{})
            item=self.wait_request();self.assertEqual(item['host'],host)
            with self.assertRaisesRegex(ValueError,'停止'):
                self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host='api'))
            meta=claim_request(item['path'],root=self.db.parent/'agent-exchange',workspace_id=identity(self.db))
            submit_response(item['path'],dict({k:meta[k] for k in BINDING},payload={'ok':True}),
                root=self.db.parent/'agent-exchange',workspace_id=identity(self.db))
            for _ in range(300):
                if not self.app.active_job:break
                time.sleep(.01)
            self.assertEqual(self.app.jobs[job['job_id']]['status'],'completed')
            self.assertEqual(self.app.jobs[job['job_id']]['result']['usage']['host'],host+'_agent')
            self.assertEqual(self.app.state()['reader_profile']['settings']['host'],host)

    def test_api_choice_is_not_unbounded_agent_and_none_is_idle(self):
        self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host='api',web_search=False))
        with closing(self.app.connection()) as conn:s=profile(conn)['settings']
        self.assertFalse(is_agent(s));self.assertFalse(s['agent_direct'])
        with self.assertRaises(HostUnavailable):self.app.mutate('connection-check',{})
        self.assertIsNone(self.app.active_job)
        self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host='none'))
        with self.assertRaises(HostUnavailable):self.app.mutate('connection-check',{})

    def test_cancelled_check_cannot_be_reanimated(self):
        self.app.mutate('reader-profile',dict(profile_id='default',name='测试',host='codex'))
        job=self.app.mutate('connection-check',{});item=self.wait_request()
        self.app.mutate('job-stop',job)
        for _ in range(300):
            if not self.app.active_job:break
            time.sleep(.01)
        self.assertEqual(self.app.jobs[job['job_id']]['status'],'cancelled')
        with self.assertRaises(ValueError):claim_request(item['path'],root=self.db.parent/'agent-exchange',workspace_id=identity(self.db))
