"""Manual reference links survive, but never consume routine collection calls."""
from pathlib import Path
import tempfile
import unittest

from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.source_catalog import catalog, configured_view, save_source, section_ids
from fastnews.search_plan import build_search_plan


class SourceUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = connect(Path(self.temp.name) / 'usage.db', mode='fixture')
        self.addCleanup(self.conn.close)
        self.base = load_config()
        sync_registry(self.conn, self.base)

    def test_manual_sources_remain_enabled_and_linked_without_routine_collection(self):
        config = configured_view(self.conn, self.base)
        all_ids = section_ids(config, 'all')
        automatic = section_ids(config, 'all', collect_only=True)
        self.assertEqual(len(all_ids), 34)
        self.assertEqual(len(automatic), 13)
        self.assertIn('fda', all_ids)
        self.assertNotIn('fda', automatic)
        self.assertNotIn('cde', automatic)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_source_preferences').fetchone()[0], 0)
        fda = next(s for s in config['reader_sources'] if s['source_id'] == 'fda')
        self.assertTrue(fda['collector_available'])
        self.assertTrue(fda['url'].startswith('https://www.fda.gov/'))

    def test_index_only_sites_enter_host_plan_without_permission_to_open_body(self):
        config = configured_view(self.conn, self.base)
        plan = build_search_plan(config, '517120')
        self.assertIn('dxy_insight', {s['source_id'] for s in plan['manual_sources']})
        cde = next(s for s in plan['selected_sources'] if s['source_id']=='cde')
        self.assertFalse(cde['host_read_allowed'])
        self.assertTrue(any('cde.org.cn' in q['query'] for q in plan['query_hints']))
        self.assertTrue({'cls_innovative_drug', 'wscn_medicine'} <= {s['source_id'] for s in plan['selected_sources']})
        self.assertTrue(any(q['lane'] == 'keywords' for q in plan['query_hints']))

    def test_complements_respect_disabled_preferences_and_do_not_overclaim(self):
        save_source(self.conn, self.base, 'default', {'source_id':'cls_innovative_drug','enabled':False})
        row = next(s for s in catalog(self.conn, self.base) if s['source_id'] == 'cde')
        self.assertEqual([s['source_id'] for s in row['alternatives']], ['wscn_medicine'])
        self.assertIn('原站', row['alternative_note'])

    def test_user_custom_link_stays_searchable_feed_stays_collectable(self):
        data = {'name':'自定义科技来源','url':'https://example.org/tech','categories':['technology'],
                'sections':['sector'],'collection':'link'}
        link = save_source(self.conn, self.base, 'default', data)
        feed = save_source(self.conn, self.base, 'default', dict(data, url='https://example.org/rss', collection='feed'))
        config = configured_view(self.conn, self.base)
        self.assertIn(link['source_id'], {s['source_id'] for s in build_search_plan(config, '513130')['selected_sources']})
        self.assertNotIn(link['source_id'], section_ids(config, 'all', collect_only=True))
        self.assertIn(feed['source_id'], section_ids(config, 'all', collect_only=True))
