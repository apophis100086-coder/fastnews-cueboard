"""Public context must preserve time, privacy and manual editorial selection."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastnews import db, editorial as ed, writing as w
from fastnews.config import load_config
from fastnews.product_context import public_context
from fastnews.reading_reports import build_input
from fastnews.source_catalog import configured_view
from fastnews.writing_background import add_candidate


class ProductContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root/'test.db', mode='fixture'); self.addCleanup(self.conn.close)
        self.config = load_config(); db.sync_registry(self.conn, self.config)

    def edition(self):
        return ed.create_edition(self.conn,self.config,product_code='588090',edition_date='2026-09-17',
            news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})

    def write(self, payload):
        path = self.root/(str(len(list(self.root.glob('*.json'))))+'.json')
        path.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        return path

    def product(self, privacy='public', **kw):
        p = dict(is_fixture=True,title='合成产品依据',content='合成产品跟踪科创板指数。',
            excerpt='合成产品跟踪科创板指数。',snapshot_type='product_identity',data_as_of='2026-06-30',
            published_at='2026-08-29',source_url='https://example.org/product',source_type='original_publication')
        p.update(kw)
        return ed.import_product_data(self.conn,self.write(p),authorized=True,product_code='588090',privacy_class=privacy)

    def history(self, privacy='public', **kw):
        p = dict(is_fixture=True,title='合成历史稿',content='历史稿里的合成测试事实。',publication_time='2026-09-15',
            completeness='full_text',source_url='https://example.org/history')
        p.update(kw)
        return ed.import_history(self.conn,self.write(p),authorized=True,product_code='588090',privacy_class=privacy)

    def test_private_imports_never_enter_context_and_no_implicit_news_selection(self):
        self.product('private_local'); self.history('private_local')
        e = self.edition(); c = public_context(self.conn,e)
        self.assertEqual(c['references'], []); self.assertEqual(c['published_history'], [])
        ed.save_decision(self.conn,edition_id=e['edition_id'],selected_event_id=None,selected_fact_ids=[],
            expected_revision=0,actor_type='human',config=self.config)
        self.product()
        with self.assertRaisesRegex(ValueError,'选择事实'):
            w._snapshot(self.conn,self.config,e['edition_id'],1)

    def test_cutoff_and_frozen_edition_and_latest_snapshot(self):
        old = self.product(); first = self.edition()
        self.product(published_at='2026-09-17',data_as_of='2026-09-15')
        self.history(publication_time='2026-09-17')
        self.product(published_at='2026-09-10',effective_at='2026-09-18')
        self.assertEqual(public_context(self.conn,self.edition())['references'][0]['id'],old['snapshot_id'])
        newer = self.product(data_as_of='2026-08-31',published_at='2026-09-14')
        self.assertEqual(public_context(self.conn,first)['references'][0]['id'],old['snapshot_id'])
        current = public_context(self.conn,self.edition())
        self.assertEqual(current['references'][0]['id'],newer['snapshot_id'])
        self.assertEqual(current['published_history'], [])

    def test_history_is_bounded_attributed_and_title_only_has_no_body(self):
        self.history(content='旧稿。'*1000)
        self.history(title='较新标题',publication_time='2026-09-16',completeness='title_only')
        self.history(publication_time='2026-09-13')
        c = public_context(self.conn,self.edition())
        self.assertEqual(len(c['published_history']),2)
        self.assertEqual(c['published_history'][0]['excerpt'],'')
        self.assertEqual(len(c['published_history'][1]['excerpt']),700)
        self.assertTrue(c['published_history'][1]['excerpt_truncated'])
        self.assertEqual(c['published_history'][1]['source_url'],'https://example.org/history')
        with self.assertRaises(ValueError):
            self.history(source_url='http://127.0.0.1/private')

    def test_reading_and_writing_receive_public_product_evidence_not_history_as_claim_sources(self):
        ref=self.product(); hist=self.history(); e=self.edition()
        input_=build_input(self.conn,configured_view(self.conn,self.config),'default','product',e['edition_date'],e)
        self.assertEqual(input_['product']['context']['references'][0]['id'],ref['snapshot_id'])
        payload=dict(is_fixture=True,title='人工选中新闻',content='合成新闻资料。',source_type='user_document',
            first_published_at='2026-09-15',facts=[],event={'subject':'测试','action':'公布','object':'产业','phase':'announcement'})
        imp=ed.import_evidence_payload(self.conn,payload,edition_id=e['edition_id'])
        add_candidate(self.conn,self.config,e['edition_id'],imp['event_id'])
        ed.save_decision(self.conn,edition_id=e['edition_id'],selected_event_id=imp['event_id'],selected_fact_ids=[],
            selected_clue_ids=[imp['event_id']],expected_revision=0,actor_type='human',config=self.config)
        s=w._snapshot(self.conn,self.config,e['edition_id'],1)
        self.assertEqual(s['materials'][-1]['origin'],'product_reference')
        self.assertFalse(s['materials'][-1]['must_include'])
        self.assertEqual(s['sources'][-1]['data_as_of'],'2026-06-30')
        self.assertEqual(s['sources'][-1]['evidence_id'],ref['evidence_refs'][0])
        self.assertEqual(s['product_context']['published_history'][0]['id'],hist['history_doc_id'])
        self.assertNotIn('历史稿里的',json.dumps(s['sources'],ensure_ascii=False))
        self.assertEqual(ed.latest_decision(self.conn,e['edition_id'])['selected_clue_ids'],[imp['event_id']])

    def test_product_evidence_tampering_fails_closed(self):
        ref=self.product(); e=self.edition()
        with self.conn:
            self.conn.execute('UPDATE fn_documents SET content=? WHERE document_id=?',('changed',ref['document_id']))
        with self.assertRaisesRegex(ValueError,'integrity'):
            public_context(self.conn,e)

    def test_history_cannot_be_assigned_to_a_different_etf_in_the_title(self):
        with self.assertRaisesRegex(ValueError,'different ETF code'):
            self.history(title='合成创新药ETF（520500）新闻')
        self.history(title='合成科创ETF（588090）及主题ETF（588710）')
        self.assertEqual(len(public_context(self.conn,self.edition())['published_history']),1)


if __name__ == '__main__':
    unittest.main()
