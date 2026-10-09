from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from fastnews.db import connect,sync_registry
from fastnews.config import load_config
from fastnews.editorial import create_edition,save_decision
from fastnews.storage import backup,restore,verify,browser_drafts,recovery


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'原 工作区';self.root.mkdir();self.db=self.root/'data/fastnews.db'
        with closing(connect(self.db)) as conn:
            sync_registry(conn,load_config())
            self.edition=create_edition(conn,load_config(),product_code='513130',edition_date='2026-09-17',news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
        for name,text in [('data/uploads/evidence.txt','本地原始资料'),('data/writing_samples/library.json','{}'),
                ('data/writing_templates/library.json','{}'),('exports/稿件.md','已保存稿件'),
                ('data/connections/api.json','PRIVATE_API_RECORD'),('data/agent-exchange/session.json','PRIVATE_AGENT_RECORD'),
                ('data/probes/cache.json','CACHE'),('data/logs/log.txt','LOG')]:
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
        self.target=Path(self.tmp.name)/'新 工作区';(self.target/'tools').mkdir(parents=True)
        (self.target/'tools/open_dashboard.py').write_text('# fixture',encoding='utf-8')

    def make(self):
        return backup(self.db,root=self.root,drafts={self.edition['edition_id']+':draft':'{"selected_clue_ids":[]}',
            'last-edition':self.edition['edition_id']})

    def test_complete_snapshot_restore_keeps_original_and_excludes_credentials(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest();r=self.make();manifest=verify(r['path'])
        names={f['path'] for f in manifest['files']}
        self.assertIn('data/uploads/evidence.txt',names);self.assertIn('exports/稿件.md',names)
        self.assertFalse(any('connections' in n or 'agent-exchange' in n or 'probes' in n or 'logs' in n for n in names))
        result=restore(r['path'],self.target);self.assertEqual(result['counts']['fn_editions'],1)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)
        with closing(connect(self.target/'data/fastnews.db')) as conn:
            self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT edition_id FROM fn_editions').fetchone()[0],self.edition['edition_id'])
        self.assertIn('last-edition',recovery(self.target/'data/fastnews.db')['restored_drafts'])
        self.assertEqual((self.target/'exports/稿件.md').read_text(encoding='utf-8'),'已保存稿件')
        self.assertTrue((self.target/'data/setup/applied.json').exists())
        with self.assertRaisesRegex(ValueError,'已有数据'):restore(r['path'],self.target)

    def test_checksum_damage_rejected_before_target_writes(self):
        r=self.make();bad=self.root/'tampered.zip'
        with zipfile.ZipFile(r['path']) as original,zipfile.ZipFile(bad,'w') as z:
            for item in original.infolist():z.writestr(item.filename,b'changed' if item.filename=='exports/稿件.md' else original.read(item.filename))
        with self.assertRaisesRegex(ValueError,'校验失败'):restore(bad,self.target)
        self.assertFalse((self.target/'data').exists())

    def test_manifest_cannot_allow_path_escape_or_secret_restoration(self):
        for name in ('../escape','data/connections/api.json','exports/../secret','C:/secret','exports\\secret',
                     'exports/NUL.txt','exports/name.','exports//name','exports/file:stream'):
            bad=self.root/'bad.zip';raw=b'bad'
            manifest=dict(format='fastnews-user-backup-v1',files=[dict(path=name,size=3,sha256=hashlib.sha256(raw).hexdigest()),
                dict(path='data/fastnews.db',size=3,sha256=hashlib.sha256(raw).hexdigest())])
            with zipfile.ZipFile(bad,'w') as z:
                z.writestr(name,raw);z.writestr('data/fastnews.db',raw);z.writestr('BACKUP_MANIFEST.json',json.dumps(manifest))
            with self.assertRaises(ValueError):verify(bad)

    def test_local_draft_allowlist_rejects_keys_and_pending_requests(self):
        for value in ({'api-key':'secret'},{'edition_123:direct-pending-request':'old run'},{'last-edition':{}},[]):
            with self.assertRaises(ValueError):browser_drafts(value)

    def test_background_export_paths_follow_new_project(self):
        with closing(connect(self.db)) as conn,conn:
            from fastnews import editorial
            from fastnews.source_catalog import configured_view
            # A real selection/export uses imported source material and keeps trace data.
            item=editorial.import_evidence_payload(conn,{'title':'合成备份用材料','content':'仅用于离线备份检查','is_fixture':False,
                'source_type':'user_document','first_published_at':'2026-09-15','facts':[]},edition_id=self.edition['edition_id'],
                actual_source_path='user:fixture',privacy_class='private_local')
            from fastnews.writing_background import add_candidate
            add_candidate(conn,load_config(),self.edition['edition_id'],item['event_id'])
            selection=editorial.save_decision(conn,config=configured_view(conn,load_config()),edition_id=self.edition['edition_id'],
                selected_event_id=None,selected_fact_ids=[],actor_type='human',selected_clue_ids=[item['event_id']],expected_revision=0)
            record=editorial.export_handoff(conn,load_config(),edition_id=self.edition['edition_id'],decision_revision=selection['decision_revision'],output_dir=self.root/'exports')
        r=self.make();restore(r['path'],self.target)
        with closing(connect(self.target/'data/fastnews.db')) as conn:
            actual=json.loads(conn.execute('SELECT data_json FROM fn_exports').fetchone()[0])
            self.assertEqual(actual['paths'],record['paths'])
        from fastnews.server import LocalApplication
        app=LocalApplication(self.target/'data/fastnews.db',load_config(),output_dir=self.target/'exports')
        content,name=app.download(actual['export_id'],'markdown')
        self.assertEqual(name,'writing_handoff.md');self.assertIn('合成备份用材料',content.decode('utf-8'))
