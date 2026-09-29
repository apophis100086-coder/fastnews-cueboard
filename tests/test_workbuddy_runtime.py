"""WorkBuddy account boundaries and observable CLI events, without model calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from fastnews import workbuddy_runtime as r,workbuddy_connection as c,host_provider as h
from fastnews.task_control import TaskControl

class WorkBuddyRuntimeTests(unittest.TestCase):
    def test_stream_records_actual_model_tool_results_but_not_reasoning(self):
        events=[{'type':'system','subtype':'init','model':'fixture-default'},
            {'type':'assistant','message':{'content':[{'type':'thinking','thinking':'PRIVATE_REASONING'},
            {'type':'tool_use','id':'s','name':'WebSearch','input':{'query':'public news'}},
            {'type':'tool_use','id':'f','name':'WebFetch','input':{'url':'https://www.python.org/downloads/windows/'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'s','content':'public results'},
            {'type':'tool_result','tool_use_id':'f','content':'Python Releases for Windows'}]}},
            {'type':'result','subtype':'success','structured_output':{'ok':True},'usage':{'input_tokens':2}}]
        got=r.parse_output('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(got.payload,{'ok':True});self.assertEqual(got.trace['model_actual'],'fixture-default')
        self.assertEqual([x['status'] for x in got.trace['actions']],['completed','completed'])
        self.assertEqual(got.trace['tool_excerpts'][0]['kind'],'tool_output')
        self.assertNotIn('PRIVATE_REASONING',json.dumps(got.trace))
        self.assertTrue(got.trace['turn_completed'])

    def test_failed_fetch_and_partial_output_do_not_claim_read(self):
        events=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'f','name':'WebFetch','input':{'url':'https://example.org'}}]}},
            {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'f','is_error':True,'content':'access denied'}]}}]
        got=r.parse_output('\n'.join(json.dumps(x) for x in events),incomplete=True)
        self.assertFalse(got.trace['turn_completed']);self.assertFalse(got.trace['visited_urls']);self.assertFalse(got.trace['tool_excerpts'])
        self.assertEqual(got.trace['actions'][0]['status'],'failed')

    def test_zero_exit_auth_error_and_model_unavailable_are_failures(self):
        self.assertIn('未取得有效登录状态',r.diagnose_failure(0,'','Authentication required. Please use /login'))
        self.assertIn('所选模型',r.diagnose_failure(0,'model not found',''))
        with self.assertRaises(h.HostUnavailable):r.parse_output('{"type":"result","is_error":true,"result":"failed"}')
        with self.assertRaises(h.HostUnavailable):r.parse_output('{"type":"result","subtype":"error_max_turns","result":"{}"}')
        self.assertIsNone(r.diagnose_failure(0,json.dumps({'type':'result','structured_output':{'title':'账户余额不足的新闻'}}),''))

    def test_codex_never_launches_and_default_omits_model_override(self):
        with patch.object(h.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(h.HostUnavailable,'只连接 WorkBuddy'):h.run_host('codex','input',{})
            launch.assert_not_called()
        process=Mock(returncode=0);process.poll.return_value=0
        process.communicate.return_value=(' {"type":"result","structured_output":{"ok":true}}','')
        with patch.object(h,'host_command',return_value=['fixture-node','fixture-cli']),patch.object(h,'ProcessGroup'),patch.object(h.subprocess,'Popen',return_value=process) as launch:
            result=h.run_host('workbuddy','input',{},web_search=False)
            args=launch.call_args.args[0]
            self.assertNotIn('--model',args);self.assertNotIn('--effort',args)
            self.assertNotIn('--max-turns',args);self.assertIn('--strict-mcp-config',args)
            self.assertIsNone(result.usage['model_actual'])
            self.assertEqual(result.usage['billing'],'workbuddy_account')

    def test_installation_change_invalidates_previous_login(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(r,'CACHE',Path(tmp)/'connection.json'),patch.object(r,'installation_identity',return_value='new'):
            r.save_connection({'installation_identity':'old','login':'authenticated'})
            self.assertEqual(r.load_connection(),{})

    def test_probe_auth_error_keeps_web_unchecked_and_stops(self):
        runner=Mock(side_effect=h.HostUnavailable('WorkBuddy 尚未登录'))
        with patch.object(r,'load_connection',return_value={}),patch.object(r,'installation_identity',return_value='fixture'),patch.object(r,'save_connection'):
            got=c.probe({},TaskControl(180),runner=runner)
        self.assertEqual(got['login'],'required');self.assertEqual(got['model'],'unchecked');self.assertEqual(got['web'],'unchecked');self.assertEqual(runner.call_count,1)

    def test_probe_requires_observed_web_result_and_not_model_claim(self):
        runner=Mock(side_effect=[h.HostResult({'ok':True},{'model_actual':'fixture-model'},{}),h.HostResult({'title':'title','quote':'claimed'}, {}, {'actions':[],'tool_excerpts':[]})])
        with patch.object(r,'load_connection',return_value={}),patch.object(r,'installation_identity',return_value='fixture'),patch.object(r,'save_connection'):
            got=c.probe({},TaskControl(180),runner=runner)
        self.assertEqual(got['model'],'available');self.assertEqual(got['web'],'unverified')
        self.assertEqual(got['model_actual'],'fixture-model')

    def test_only_verified_model_effort_pair_can_be_saved(self):
        with patch.object(r,'load_connection',return_value={'verified_models':[{'model':'fixture','effort':'high'}]}):
            c.require_verified_model(None,None);c.require_verified_model('fixture','high')
            with self.assertRaises(ValueError):c.require_verified_model('fixture','low')
        self.assertEqual(c.model_options({'host':'workbuddy'}),{})
        self.assertEqual(c.model_options({'host':'workbuddy','workbuddy_model':'fixture'}),{'model':'fixture'})

    def test_headless_bundle_opens_desktop_instead_of_missing_tui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cli=root/'resources/app.asar.unpacked/cli/bin/codebuddy'
            cli.parent.mkdir(parents=True);cli.write_text('fixture');(root/'WorkBuddy.exe').write_text('fixture')
            with patch.object(r,'installation_roots',return_value=[root]),patch.object(r,'discover_command',return_value=['node',str(cli)]):
                entry=r.login_entry()
                self.assertEqual(entry['kind'],'desktop')
                with patch.object(c,'os',Mock(name='nt')),patch.object(c.subprocess,'Popen') as launch:
                    c.os.name='nt';result=c.login()
                self.assertEqual(launch.call_args.args[0],[str((root/'WorkBuddy.exe').resolve())])
                self.assertNotIn('/login',launch.call_args.args[0]);self.assertIn('不含独立命令行登录组件',result['message'])

    def test_interactive_login_requires_real_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cli=root/'cli/bin/codebuddy';cli.parent.mkdir(parents=True);cli.write_text('fixture')
            with patch.object(r,'installation_roots',return_value=[root]),patch.object(r,'discover_command',return_value=['node',str(cli)]):
                self.assertEqual(r.login_entry()['kind'],'unavailable')
                full=root/'cli/dist/codebuddy.js';full.parent.mkdir();full.write_text('fixture')
                self.assertEqual(r.login_entry(),{'kind':'cli','command':['node',str(cli)]})

    def test_no_login_entry_cannot_claim_window_opened(self):
        with patch.object(c,'os',Mock()),patch.object(r,'login_entry',return_value={'kind':'unavailable','command':None}),patch.object(c.subprocess,'Popen') as launch:
            c.os.name='nt'
            with self.assertRaisesRegex(ValueError,'未找到可用'):c.login()
            launch.assert_not_called()

if __name__=='__main__':unittest.main()
