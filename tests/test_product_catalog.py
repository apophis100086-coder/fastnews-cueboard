"""Product onboarding contracts and an offline STAR 50 editorial round trip."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

from fastnews import db, editorial, search_plan
from fastnews.config import ConfigError, load_config
from fastnews.metadata import normalize_record
from fastnews.pipeline import store_record
from fastnews.reading_reports import build_input
from fastnews.routing import route_product
from fastnews.source_catalog import configured_view
from fastnews.writing_background import add_candidate

ROOT = Path(__file__).resolve().parents[1]


class ProductCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_dir = self.root / 'config'
        shutil.copytree(ROOT / 'config', self.config_dir)

    def mutate(self, name, change):
        path = self.config_dir / name
        doc = yaml.safe_load(path.read_text(encoding='utf-8'))
        change(doc)
        path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding='utf-8')

    def test_overlay_cannot_replace_original_product_or_repeat_code(self):
        self.mutate('products.additional.yaml', lambda d: d['products'][0].update(code='588710'))
        with self.assertRaisesRegex(ConfigError, 'cannot override'):
            load_config(self.config_dir)
        self.mutate('products.additional.yaml', lambda d: d['products'].append(deepcopy(d['products'][0])))
        with self.assertRaisesRegex(ConfigError, 'duplicate product code'):
            load_config(self.config_dir)

    def test_overlay_validates_codes_and_references(self):
        for code in (588090, '５８８０９０', '58809', 'ABCDEF'):
            self.mutate('products.additional.yaml', lambda d: d['products'][0].update(code=code))
            with self.assertRaises(ConfigError):
                load_config(self.config_dir)
        self.mutate('products.additional.yaml', lambda d: d['products'][0].update(code='588090', preferred_sources=['unknown']))
        with self.assertRaisesRegex(ConfigError, 'unknown source reference'):
            load_config(self.config_dir)

    def test_missing_discovery_and_unknown_code_fail_early(self):
        self.mutate('research_discovery.yaml', lambda d: d['products'].pop('588090'))
        with self.assertRaisesRegex(ConfigError, 'missing fields: 588090'):
            load_config(self.config_dir)
        self.mutate('research_discovery.yaml', lambda d: d['products'].update({'000000': {'core_keywords': [], 'excluded_keywords': []}}))
        with self.assertRaisesRegex(ConfigError, 'unknown fields: 000000'):
            load_config(self.config_dir)

    def test_profile_and_search_must_cover_registry_exactly(self):
        self.mutate('product_profiles.example.yaml', lambda d: d['profiles'].pop(0))
        with self.assertRaisesRegex(editorial.EditorialError, 'exactly once'):
            editorial.load_editorial_config(self.config_dir)
        self.mutate('product_search.yaml', lambda d: d['products'].pop('588090'))
        with patch.object(search_plan, 'PATH', self.config_dir / 'product_search.yaml'):
            with self.assertRaisesRegex(ValueError, '完整覆盖'):
                search_plan.search_policy(load_config(self.config_dir))

    def test_duplicate_profile_and_wrong_name_are_rejected(self):
        self.mutate('product_profiles.example.yaml', lambda d: d['profiles'][0].update(name='错误产品'))
        with self.assertRaisesRegex(editorial.EditorialError, 'name must match'):
            editorial.load_editorial_config(self.config_dir)
        self.mutate('product_profiles.example.yaml', lambda d: d['profiles'].append(deepcopy(d['profiles'][1])))
        with self.assertRaisesRegex(editorial.EditorialError, 'exactly once'):
            editorial.load_editorial_config(self.config_dir)

    def test_next_product_can_be_added_by_configuration_without_code_changes(self):
        def new_route(doc):
            item = deepcopy(doc['products'][0])
            item.update(code='999999', name='合成第六只产品')
            doc['products'].append(item)
        def new_profile(doc):
            item = deepcopy(doc['profiles'][0])
            item.update(code='999999', name='合成第六只产品')
            doc['profiles'].append(item)
        self.mutate('products.additional.yaml', new_route)
        self.mutate('product_profiles.example.yaml', new_profile)
        for filename in ('research_discovery.yaml', 'product_search.yaml'):
            self.mutate(filename, lambda d: d['products'].update({'999999': deepcopy(d['products']['588090'])}))
        config = load_config(self.config_dir)
        self.assertEqual(len(config['products']), 6)
        self.assertEqual(len(editorial.load_editorial_config(self.config_dir)['profiles']['profiles']), 6)
        with patch.object(search_plan, 'PATH', self.config_dir / 'product_search.yaml'):
            self.assertEqual(search_plan.build_search_plan(config, '999999')['product_code'], '999999')

    def test_star50_routes_broad_index_news_and_keeps_sector_only_as_background(self):
        config = load_config()
        with closing(db.connect(self.root / 'routing.db', mode='fixture')) as conn:
            db.sync_registry(conn, config)
            for n, title in enumerate(('合成科创50样本调整公告', '合成科创板新能源企业研发进展', '合成光刻设备订单')):
                record = normalize_record({'source_id': 'cls_morning', 'url': f'https://example.invalid/test-{n}',
                    'title': title, 'lead': '', 'report_published_at': '2026-09-14T08:00:00+08:00'}, is_fixture=True)
                store_record(conn, record, config, '2026-09-14T09:00:00+08:00')
            def lanes(code):
                view = route_product(conn, config, code, edition_date='2026-09-15', news_cutoff='2026-09-14T23:59:59+08:00')
                self.assertIsNone(view['default_selection'])
                return {e['title']: e['product_match']['lane'] for group in view['groups'].values() for e in group}
            star, equipment = lanes('588090'), lanes('588710')
            self.assertEqual(star['合成科创50样本调整公告'], 'core')
            self.assertEqual(star['合成科创板新能源企业研发进展'], 'core')
            self.assertEqual(equipment['合成科创50样本调整公告'], 'unmatched')
            self.assertEqual(star['合成光刻设备订单'], 'background')
            self.assertEqual(equipment['合成光刻设备订单'], 'core')

    def test_registry_upgrade_preserves_old_selection_and_exports_new_product(self):
        config = load_config()
        conn = db.connect(self.root / 'workflow.db', mode='fixture')
        self.addCleanup(conn.close)
        legacy = deepcopy(config)
        legacy['products'].pop('588090')
        db.sync_registry(conn, legacy)
        def edition(code):
            return editorial.create_edition(conn, config, product_code=code, edition_date='2026-09-15',
                news_cutoff='2026-09-14T23:59:59+08:00', market_cutoffs={})
        old = edition('588710')
        old_choice = editorial.save_decision(conn, edition_id=old['edition_id'], selected_event_id=None,
            selected_fact_ids=[], mainline_note='合成人工取舍保持原样', expected_revision=0, actor_type='human')
        old_rows = list(conn.execute('SELECT * FROM fn_products ORDER BY code'))
        for _ in range(2):
            db.sync_registry(conn, config)
        self.assertEqual(conn.execute('SELECT count(*) FROM fn_products').fetchone()[0], 5)
        self.assertEqual([tuple(r) for r in old_rows], [tuple(r) for r in conn.execute("SELECT * FROM fn_products WHERE code!='588090' ORDER BY code")])
        self.assertEqual(editorial.get_edition(conn, old['edition_id']), old)
        self.assertEqual(editorial.latest_decision(conn, old['edition_id']), old_choice)
        new = edition('588090')
        self.assertEqual(new['product_name'], '科创50ETF华泰柏瑞')
        record = normalize_record({'source_id': 'cls_morning', 'title': '合成科创50样本调整线索',
            'url': 'https://example.invalid/star50', 'lead': '', 'report_published_at': '2026-09-14T08:00:00+08:00'}, is_fixture=True)
        with conn:
            store_record(conn, record, config, '2026-09-14T09:00:00+08:00')
        view = configured_view(conn, config)
        with patch('socket.create_connection', side_effect=AssertionError('offline fixture')):
            payload = build_input(conn, view, 'default', 'product', '2026-09-15', new)
            self.assertEqual(payload['product']['product_code'], '588090')
            self.assertIn('上证科创板50成份指数', str(payload['product']['observation_variables']))
            self.assertLessEqual(len(payload['search_plan']['queries']) + payload['search_plan']['fallback_queries'], payload['search_plan']['max_queries'])
            self.assertEqual(payload['search_plan']['evidence_budget'],
                {'max_events': 6, 'max_pages':20, 'max_pages_per_event': 4, 'within_current_host_task': True})
            event_id = conn.execute('SELECT event_id FROM fn_events').fetchone()[0]
            add_candidate(conn, config, new['edition_id'], event_id)
            self.assertIsNone(editorial.latest_decision(conn, new['edition_id']))
            editorial.save_decision(conn, edition_id=new['edition_id'], selected_event_id=None, selected_fact_ids=[],
                selected_clue_ids=[event_id], manual_notes=[], expected_revision=0, actor_type='human', config=config)
            export = editorial.export_handoff(conn, config, edition_id=new['edition_id'], decision_revision=1, output_dir=self.root/'exports')
        pack = json.loads(Path(export['paths']['json']).read_text(encoding='utf-8'))
        text = Path(export['paths']['markdown']).read_text(encoding='utf-8')
        self.assertEqual(pack['edition']['product_code'], '588090')
        self.assertIn('科创50ETF华泰柏瑞', text)
        self.assertIn('合成科创50样本调整线索', text)
        self.assertEqual(pack['usable_facts'], [])
        self.assertEqual(editorial.latest_decision(conn, old['edition_id']), old_choice)
        self.assertEqual(conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
