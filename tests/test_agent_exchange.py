"""Real local file exchange, isolated workspaces; no models or web calls."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastnews import agent_runner as a
from fastnews.task_control import TaskControl, TaskStopped
from fastnews.host_provider import HostUnavailable
from fastnews.workspace import identity,settings,bootstrap_profile


class AgentExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'中文 工作区'/'agent-exchange'
        self.workspace=identity(Path(self.tmp.name)/'中文 工作区/data.db')
        self.control=TaskControl(30);self.control.make_unbounded()
        self.results=[];self.errors=[];self.progress=[]

    def start(self,run='writing_1234567890abcdef'):
        runner=a.file_runner(run,root=self.root,workspace_id=self.workspace)
        def work():
            try:self.results.append(runner('workbuddy','synthetic prompt',{'type':'object'},
                control=self.control,timeout=10,progress=self.progress.append))
            except Exception as e:self.errors.append(e)
        self.thread=threading.Thread(target=work);self.thread.start()
        self.addCleanup(self.finish)
        for _ in range(100):
            pending=a.pending_requests(self.root)
            if pending:
                self.path=Path(pending[0]['path']);self.meta=json.loads(self.path.read_text(encoding='utf-8'));return
            time.sleep(.01)
        self.fail('Request was not saved')

    def finish(self):
        self.control.cancelled.set()
        if hasattr(self,'thread'):self.thread.join(2)

    def envelope(self):
        return dict({k:self.meta[k] for k in a.BINDING},payload={'status':'ready'},model='unverified-name')

    def test_valid_response_claim_progress_and_model_identity(self):
        self.start();a.claim_request(self.path,root=self.root,workspace_id=self.workspace)
        time.sleep(.15)
        a.submit_response(self.path,self.envelope(),root=self.root,workspace_id=self.workspace)
        self.thread.join(2)
        self.assertFalse(self.errors);self.assertEqual(self.results[0].payload,{'status':'ready'})
        self.assertIsNone(self.results[0].usage['model_actual'])
        self.assertEqual(self.results[0].trace['evidence_status'],'agent_reported')
        self.assertIn('processing',[p['agent_status'] for p in self.progress])
        self.assertEqual(a.pending_requests(self.root),[])

    def test_every_binding_mismatch_is_rejected(self):
        self.start()
        for key in a.BINDING:
            raw=self.envelope();raw[key]='different'
            with self.assertRaises(HostUnavailable):a.submit_response(self.path,raw,root=self.root,workspace_id=self.workspace)
        self.assertFalse(a.sidecar(self.path,'.response.json').exists())

    def test_cancel_rejects_late_response_and_keeps_request(self):
        self.start();self.control.cancelled.set();self.thread.join(2)
        self.assertIsInstance(self.errors[0],TaskStopped)
        self.assertEqual(a.state_of(self.path)['status'],'cancelled')
        with self.assertRaises(ValueError):a.submit_response(self.path,self.envelope(),root=self.root,workspace_id=self.workspace)
        self.assertTrue(self.path.exists())
        a.set_state(self.path,'processing')
        self.assertEqual(a.pending_requests(self.root),[])

    def test_duplicate_claim_and_response_do_not_replace_output(self):
        self.start();a.claim_request(self.path,root=self.root,workspace_id=self.workspace)
        with self.assertRaises(ValueError):a.claim_request(self.path,root=self.root,workspace_id=self.workspace)
        a.submit_response(self.path,self.envelope(),root=self.root,workspace_id=self.workspace)
        with self.assertRaises((ValueError,FileExistsError)):
            a.submit_response(self.path,self.envelope(),root=self.root,workspace_id=self.workspace)

    def test_foreign_directory_and_workspace_are_rejected(self):
        self.start()
        with self.assertRaises(ValueError):a.claim_request(self.path,root=self.root,workspace_id='other')
        with self.assertRaises(ValueError):a.request_path(self.path,self.root/'other')
        with self.assertRaises(ValueError):a.exchange_dir('../escape',self.root)

    def test_recovery_archives_old_task_and_never_lists_legacy(self):
        folder=a.exchange_dir('writing_legacy123456',self.root)
        legacy=folder/'step-01.request.json';a.atomic_json(legacy,{'prompt':'legacy'})
        self.assertEqual(a.pending_requests(self.root),[])
        self.start();a.set_state(self.path,'waiting',owner_pid=0);a.recover_requests(self.root)
        self.assertEqual(a.state_of(self.path)['status'],'interrupted')
        self.assertEqual(a.pending_requests(self.root),[])

    def test_invalid_json_never_returns_a_payload(self):
        self.start();a.sidecar(self.path,'.response.json').write_text('{',encoding='utf-8')
        self.thread.join(2)
        self.assertIsInstance(self.errors[0],HostUnavailable);self.assertEqual(self.results,[])
        self.assertEqual(a.state_of(self.path)['status'],'failed')

    def test_new_step_cannot_reuse_an_old_response(self):
        self.start();a.submit_response(self.path,self.envelope(),root=self.root,workspace_id=self.workspace)
        self.thread.join(2);self.start()
        self.assertEqual(self.meta['step'],2);self.assertEqual(len(self.results),1)

    def test_unbounded_mode_keeps_network_timeout_finite(self):
        self.assertIsNone(self.control.deadline_at)
        self.assertEqual(self.control.host_timeout(12),12)
        self.control.cancelled.set()
        with self.assertRaises(TaskStopped):self.control.check()


class WorkspaceTests(unittest.TestCase):
    def test_two_chinese_paths_have_distinct_identity_and_port(self):
        with tempfile.TemporaryDirectory() as directory:
            for name,port in [('中文 工作区',8787),('另一 工作区',8788)]:
                root=Path(directory)/name;(root/'config').mkdir(parents=True)
                (root/'config/workspace.yaml').write_text(f'port: {port}\nagent_direct: true\n',encoding='utf-8')
                self.assertEqual(settings(root)['port'],port)
            self.assertNotEqual(identity(Path(directory)/'中文 工作区/data.db'),identity(Path(directory)/'另一 工作区/data.db'))

    def test_empty_package_bootstraps_direct_mode_without_overriding_choice(self):
        import sqlite3
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        conn.execute('CREATE TABLE fn_reader_profiles(profile_id TEXT,settings_json TEXT)')
        conn.execute("INSERT INTO fn_reader_profiles VALUES('default','{}')")
        conn.execute("INSERT INTO fn_reader_profiles VALUES('another','{}')")
        bootstrap_profile(conn);self.assertTrue(json.loads(conn.execute('SELECT settings_json FROM fn_reader_profiles').fetchone()[0])['agent_direct'])
        self.assertTrue(json.loads(conn.execute("SELECT settings_json FROM fn_reader_profiles WHERE profile_id='another'").fetchone()[0])['agent_direct'])
        conn.execute("UPDATE fn_reader_profiles SET settings_json='{\"agent_direct\":false}'")
        bootstrap_profile(conn);self.assertFalse(json.loads(conn.execute('SELECT settings_json FROM fn_reader_profiles').fetchone()[0])['agent_direct'])
