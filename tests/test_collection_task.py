"""A killed worker must leave committed data readable and no owned running run."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastnews import db
from fastnews.config import load_config
from fastnews.collection_task import collect
from fastnews.task_control import TaskControl,TaskStopped


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conn=db.connect(self.root/'fixture.db',mode='fixture');self.addCleanup(self.conn.close)
        self.cfg=load_config();db.sync_registry(self.conn,self.cfg)

    def test_real_child_transports_chinese_and_commits_fixture(self):
        result=collect(self.conn,self.cfg,'digest',{'no_network':True},TaskControl())
        self.assertFalse(result.get('collection_incomplete'))
        self.assertTrue(self.conn.execute('SELECT count(*) FROM fn_scan_batches').fetchone()[0])
        self.assertFalse(self.conn.execute("SELECT count(*) FROM fn_scan_batches WHERE status='running'").fetchone()[0])

    def test_real_child_handles_chinese_database_path_with_non_utf8_locale(self):
        folder=self.root/'中文回测';folder.mkdir()
        conn=db.connect(folder/'快讯.db',mode='fixture');self.addCleanup(conn.close)
        db.sync_registry(conn,self.cfg)
        from fastnews.host_provider import clean_environment
        # UTF-8 must be selected by the command, not inherited from the parent.
        env=dict(clean_environment(),PYTHONUTF8='0')
        with patch('fastnews.collection_task.clean_environment',return_value=env):
            result=collect(conn,self.cfg,'digest',{'no_network':True},TaskControl())
        self.assertFalse(result.get('collection_incomplete'),result)
        self.assertTrue(conn.execute('SELECT count(*) FROM fn_scan_batches').fetchone()[0])

    def interrupted(self,cancel=False):
        helper=self.root/'slow.py'
        helper.write_text('''import json,sys,sqlite3,time
r=json.load(sys.stdin)
c=sqlite3.connect(r['database'])
c.execute("INSERT INTO fn_scan_batches(run_id,mode,started_at,status,parameters_json) VALUES(?,?,?,?,?)",('owned','fixture','2026-09-26','running',json.dumps({'collection_task_id':r['config']['_collection_task_id']})))
c.execute("INSERT INTO fn_meta(key,value) VALUES('worker_committed','yes')")
c.commit()
time.sleep(120)
''',encoding='utf-8')
        real_popen=subprocess.Popen;children=[]
        def spawn(command,**kwargs):
            child=real_popen([sys.executable,str(helper)],**kwargs);children.append(child);return child
        control=TaskControl(31)
        if cancel:
            timer=threading.Timer(.7,control.cancelled.set);timer.start();self.addCleanup(timer.cancel)
        start=time.monotonic()
        with patch('fastnews.collection_task.subprocess.Popen',side_effect=spawn):
            if cancel:
                with self.assertRaises(TaskStopped):collect(self.conn,self.cfg,'digest',{},control)
            else:
                result=collect(self.conn,self.cfg,'digest',{},control)
                self.assertEqual(result['status'],'collection_timed_out')
        self.assertLess(time.monotonic()-start,8)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.conn.execute("SELECT value FROM fn_meta WHERE key='worker_committed'").fetchone()[0],'yes')
        self.assertEqual(self.conn.execute("SELECT status FROM fn_scan_batches WHERE run_id='owned'").fetchone()[0],'failed')
        # The same database can resume a later collection; no reset or new DB.
        result=collect(self.conn,self.cfg,'digest',{'no_network':True},TaskControl())
        self.assertFalse(result.get('collection_incomplete'))

    def test_timeout_retains_transactions_and_can_resume(self):self.interrupted()
    def test_stop_retains_transactions_and_can_resume(self):self.interrupted(True)


if __name__=='__main__':unittest.main()
