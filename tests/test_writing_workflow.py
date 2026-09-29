"""Workflow acceptance in disposable fixture stores; no real host or network."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastnews import db, editorial as ed
from fastnews.config import load_config
from fastnews.host_provider import HostResult
from fastnews.metadata import normalize_record
from fastnews.pipeline import store_record
from fastnews.reading_reports import build_input, generate_report, adopt_report_item
from fastnews.search_plan import build_search_plan
from fastnews.source_catalog import configured_view, save_source, save_profile
from fastnews.writing_background import add_candidate, pool_items, remove_candidate, validate_notes


class WritingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'workflow.fixture.db'
        self.conn = db.connect(self.path, mode='fixture')
        self.addCleanup(self.conn.close)
        self.config = load_config()
        db.sync_registry(self.conn, self.config)
        self.edition = self.make_edition()
        self.eid = self.edition['edition_id']
        record = normalize_record({'source_id': 'cls_morning', 'title': '合成平台云计算公告',
            'lead': '合成资料：AI应用与云业务数据。', 'url': 'https://example.org/synthetic-cloud',
            'report_published_at': '2026-09-14T08:00:00+08:00'}, is_fixture=True)
        with self.conn:
            store_record(self.conn, record, self.config, '2026-09-14T09:00:00+08:00')
        p = patch('socket.create_connection', side_effect=AssertionError('offline acceptance'))
        p.start()
        self.addCleanup(p.stop)

    def make_edition(self, code='513130', **kwargs):
        return ed.create_edition(self.conn, self.config, product_code=code,
            edition_date='2026-09-15', news_cutoff='2026-09-14T23:59:59+08:00', market_cutoffs={}, **kwargs)

    def view(self, pid='default'):
        return configured_view(self.conn, self.config, pid)

    def runner(self, host, prompt, schema, **kwargs):
        payload = json.loads(prompt.split('INPUT_JSON:\n')[1])
        ref = payload['items'][0]['id']
        return HostResult({'items': [
            {'title': '合成线索' + str(i), 'summary': '合成摘要' + str(i), 'why': '云业务观察变量',
             'category': 'technology', 'source_refs': [ref]} for i in (1, 2)],
            'web_sources': [], 'coverage': [], 'gaps': []}, {'is_fixture': True}, {'web_actions': 0})

    def report(self, **kwargs):
        return generate_report(self.conn, self.view(), 'default', 'product', '2026-09-15',
                               edition=self.edition, runner=self.runner, **kwargs)

    def adopt(self, report=None, item='item_1'):
        report = report or self.report()
        return adopt_report_item(self.conn, 'default', report['report_id'], item, self.eid)['event_id']

    def note(self, **changes):
        return {'note_id': 'note_test', 'kind': 'product_turnover', 'content': '合成成交额为123亿元',
                'source_name': '用户测试资料', 'source_url': 'https://example.org/user-data',
                'published_at': '2026-09-14', 'data_date': '2026-09-14', 'selected': True, **changes}

    def save(self, clues, notes=None, revision=0, **kwargs):
        return ed.save_decision(self.conn, edition_id=self.eid, selected_event_id=kwargs.pop('selected_event_id', None),
            selected_fact_ids=[], selected_clue_ids=clues, manual_notes=notes or [], expected_revision=revision,
            actor_type='human', config=self.config, **kwargs)

    def export(self, revision=1):
        record = ed.export_handoff(self.conn, self.config, edition_id=self.eid,
                                  decision_revision=revision, output_dir=self.root / 'exports')
        return record, json.loads(Path(record['paths']['json']).read_text(encoding='utf-8')), Path(record['paths']['markdown']).read_text(encoding='utf-8')

    def test_four_products_route_distinct_sector_sources_and_both_query_lanes(self):
        plans = {code: build_search_plan(self.view(), code) for code in self.config['products']}
        def ids(code):
            return {s['source_id'] for s in plans[code]['selected_sources']}
        self.assertIn('trendforce', ids('513130'))
        self.assertIn('hkex_official_news', ids('513130'))
        self.assertIn('hkex_news', {s['source_id'] for s in plans['513130']['manual_sources']})
        self.assertNotIn('fda', ids('513130'))
        self.assertIn('cls_innovative_drug', ids('517120'))
        self.assertIn('fda', ids('517120'))
        self.assertNotIn('trendforce', ids('513530'))
        self.assertNotIn('hkex_stock_connect_daily', ids('588710'))
        for plan in plans.values():
            self.assertTrue({'sector_sites', 'keywords'}.issubset({q['lane'] for q in plan['query_hints']}))
            self.assertLessEqual(len(plan['queries']),8)
            self.assertLessEqual(len(plan['queries']) + plan['fallback_queries'], plan['max_queries'])
            self.assertEqual(plan['execution_status'], 'planned_not_executed')

    def test_custom_category_source_enabled_and_profile_preferences_control_routing(self):
        pid = save_profile(self.conn, {'name': '合成个人方案'})['profile_id']
        custom = save_source(self.conn, self.config, pid, {'name': '合成科技网址', 'url': 'https://example.org/tech',
            'categories': ['technology'], 'sections': ['sector'], 'collection': 'link'})
        def targets(p):
            return {s['source_id'] for s in build_search_plan(self.view(p), '513130')['selected_sources']}
        self.assertIn(custom['source_id'], targets(pid))
        self.assertNotIn(custom['source_id'], targets('default'))
        save_source(self.conn, self.config, pid, {'source_id': custom['source_id'], 'enabled': False})
        self.assertNotIn(custom['source_id'], targets(pid))

    def test_keywords_change_cache_but_repeat_reuses_and_never_changes_decisions(self):
        runner = Mock(side_effect=self.runner)
        args = dict(edition=self.edition, runner=runner)
        a = generate_report(self.conn, self.view(), 'default', 'product', '2026-09-15', extra_keywords=['云收入'], **args)
        b = generate_report(self.conn, self.view(), 'default', 'product', '2026-09-15', extra_keywords=['云收入'], **args)
        c = generate_report(self.conn, self.view(), 'default', 'product', '2026-09-15', extra_keywords=['平台监管'], **args)
        self.assertEqual(a['report_id'], b['report_id'])
        self.assertNotEqual(a['report_id'], c['report_id'])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(c['search_plan']['extra_keywords'], ['平台监管'])
        self.assertEqual(pool_items(self.conn, self.config, self.eid), [])
        self.assertIsNone(ed.latest_decision(self.conn, self.eid))

    def test_input_uses_sector_targets_and_keeps_morning_only_as_saved_context(self):
        payload = build_input(self.conn, self.view(), 'default', 'product', '2026-09-15', self.edition)
        self.assertNotIn('fda', {s['source_id'] for s in payload['selected_sources']})
        self.assertIn('cls_morning', payload['context_sources'])
        self.assertTrue(payload['items'])
        self.assertEqual(payload['news_cutoff'], self.edition['news_cutoff'])

    def test_invalid_keyword_input_rejected_locally(self):
        for words in ('text', ['x'] * 11, [''], ['x' * 41], [None]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                build_search_plan(self.view(), '513130', words)

    def test_fixed_discovery_includes_matching_custom_feed_and_isolates_unconnected_sources(self):
        from fastnews.adapters import DiscoveryPage
        from fastnews.product_discovery import refresh_fixed_sources
        source = save_source(self.conn, self.config, 'default', {'name': '合成科技订阅',
            'url': 'https://example.org/tech-feed', 'categories': ['technology'], 'sections': ['sector'], 'collection': 'feed'})
        config = self.view()
        db.sync_registry(self.conn, config)
        adapter = Mock()
        adapter.supported_sources = {source['source_id']}
        adapter.settings, adapter.stats, adapter.last_discoveries = {}, {}, {}
        adapter.discover.return_value = DiscoveryPage([], False, None, 1, coverage_reason='synthetic_only')
        budget = Mock()
        result = refresh_fixed_sources(self.conn, config, '513130', self.edition['news_cutoff'], budget,
                                       mode='fixture', adapter=adapter)
        self.assertEqual(adapter.discover.call_count, 1)
        self.assertEqual(adapter.discover.call_args.args[0], source['source_id'])
        self.assertNotIn('fda', [s['source_id'] for s in result['sources']])
        self.assertTrue(any(s['status'] == 'unavailable' for s in result['sources']))

    def test_adopt_is_idempotent_and_does_not_select_or_verify(self):
        report = self.report()
        event = self.adopt(report)
        self.assertEqual(self.adopt(report), event)
        pool = pool_items(self.conn, self.config, self.eid)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]['title'], '合成线索1')
        self.assertFalse(pool[0]['fact_verified'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_facts').fetchone()[0], 0)
        self.assertIsNone(ed.latest_decision(self.conn, self.eid))

    def test_remove_and_readopt_preserve_evidence_and_previous_decision(self):
        report = self.report()
        event = self.adopt(report)
        first = self.save([event], selected_event_id=event)
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM fn_edition_evidence')]
        remove_candidate(self.conn, self.eid, event, 1)
        self.assertEqual(pool_items(self.conn, self.config, self.eid), [])
        current = ed.latest_decision(self.conn, self.eid)
        self.assertEqual(current['decision_revision'], 2)
        self.assertEqual(current['selected_clue_ids'], [])
        self.assertIsNone(current['selected_event_id'])
        self.assertEqual(before, [tuple(r) for r in self.conn.execute('SELECT * FROM fn_edition_evidence')])
        self.assertEqual(json.loads(self.conn.execute('SELECT data_json FROM fn_decisions WHERE revision=1').fetchone()[0]), first)
        self.adopt(report)
        self.assertEqual(len(pool_items(self.conn, self.config, self.eid)), 1)
        self.assertEqual(ed.latest_decision(self.conn, self.eid), current)

    def test_remove_rejects_stale_revision_without_changing_pool(self):
        event = self.adopt()
        self.save([event])
        with self.assertRaisesRegex(ValueError, '版本'):
            remove_candidate(self.conn, self.eid, event, 0)
        self.assertTrue(pool_items(self.conn, self.config, self.eid)[0]['active'])
        self.assertEqual(ed.latest_decision(self.conn, self.eid)['decision_revision'], 1)

    def test_remove_only_target_preserves_other_selection_and_manual_notes(self):
        report = self.report()
        first, second = self.adopt(report), self.adopt(report, 'item_2')
        self.save([first, second], [self.note()], selected_event_id=first)
        remove_candidate(self.conn, self.eid, first, 1)
        decision = ed.latest_decision(self.conn, self.eid)
        self.assertEqual(decision['selected_clue_ids'], [second])
        self.assertEqual(decision['manual_notes'][0]['content'], self.note()['content'])

    def test_multiple_clues_and_notes_export_in_human_order_without_forcing_main(self):
        report = self.report()
        first, second = self.adopt(report), self.adopt(report, 'item_2')
        self.save([second, first], [self.note(), self.note(note_id='unselected', content='不要外发这段补充', selected=False)])
        _, pack, text = self.export()
        self.assertEqual([c['event_id'] for c in pack['selected_research_clues']], [second, first])
        self.assertLess(text.index('合成线索2'), text.index('合成线索1'))
        self.assertIn('合成成交额为123亿元', text)
        self.assertIn('https://example.org/synthetic-cloud', text)
        self.assertEqual(pack['usable_facts'], [])
        self.assertIn('未指定', text)
        self.assertNotIn('不要外发这段补充', json.dumps(pack, ensure_ascii=False))
        self.assertNotIn('不要外发这段补充', text)

    def test_empty_saved_selection_cannot_export(self):
        self.adopt()
        self.save([], [self.note(selected=False)])
        with self.assertRaises(ValueError):
            self.export()

    def test_manual_only_handoff_is_explicit_and_unverified(self):
        self.save([], [self.note()])
        _, pack, _ = self.export()
        self.assertFalse(pack['manual_supplements'][0]['fact_verified'])
        self.assertEqual(pack['selected_research_clues'], [])
        self.assertEqual(pack['usable_facts'], [])

    def test_post_cutoff_notes_and_clues_do_not_leak_via_decision_copy(self):
        event = self.adopt()
        snap = pool_items(self.conn, self.config, self.eid)[0]
        snap['sources'][0]['published_at'] = '2026-09-15'
        snap['summary'] = '不得导出的未来摘要'
        with self.conn:
            self.conn.execute('UPDATE fn_candidate_pool SET snapshot_json=?', (ed._json(snap),))
        self.save([event], [self.note(content='不得导出的未来数字', published_at='2026-09-15')])
        _, pack, text = self.export()
        self.assertEqual(len(pack['background_exclusions']), 2)
        self.assertNotIn('不得导出的未来', text + json.dumps(pack, ensure_ascii=False))

    def test_unknown_dates_stay_unknown_and_market_date_has_own_cutoff(self):
        self.save([], [self.note(published_at=None, data_date=None, source_url=None)])
        _, pack, text = self.export()
        note = pack['manual_supplements'][0]
        self.assertIsNone(note['published_at'])
        self.assertEqual(note['date_state'], 'pending_time')
        self.assertIn('数据日期：未知', text)
        self.assertIn('行情截点未知', note['usage'])

    def test_manual_market_cutoff_is_independent_of_news_date(self):
        self.edition = ed.create_edition(self.conn, self.config, product_code='513130', edition_date='2026-09-15',
            news_cutoff='2026-09-14T23:59:59+08:00', market_cutoffs={'CN': {'date': '2026-09-11', 'source': '合成显式日期'}})
        self.eid = self.edition['edition_id']
        self.save([], [self.note(market='CN')])
        _, pack, text = self.export()
        self.assertEqual(pack['manual_supplements'], [])
        self.assertEqual(len(pack['background_exclusions']), 1)
        self.assertNotIn('合成成交额为123亿元', text)

    def test_restricted_product_flow_remains_internal_and_attribution_is_not_relabelled(self):
        self.save([], [self.note(kind='product_flow', source_name='媒体转引 Wind')])
        _, pack, text = self.export()
        self.assertIn('仅内部研究背景', text)
        self.assertIn('媒体转引 Wind', text)
        self.assertIn('实际取得方式：用户在看板手工输入', text)
        self.assertEqual(pack['usable_facts'], [])

    def test_private_notes_never_enter_host_input(self):
        self.save([], [self.note(content='PRIVATE-MATERIAL-DO-NOT-SEND')])
        payload = build_input(self.conn, self.view(), 'default', 'product', '2026-09-15', self.edition)
        self.assertNotIn('PRIVATE-MATERIAL', json.dumps(payload))

    def test_cross_edition_and_removed_candidate_references_rejected(self):
        event = self.adopt()
        other = self.make_edition('517120')['edition_id']
        with self.assertRaises(ValueError):
            ed.save_decision(self.conn, edition_id=other, selected_event_id=None, selected_fact_ids=[],
                selected_clue_ids=[event], manual_notes=[], expected_revision=0, actor_type='human')
        remove_candidate(self.conn, self.eid, event, 0)
        with self.assertRaises(ValueError):
            self.save([event])

    def test_refresh_and_disabled_source_cannot_overwrite_saved_clue_snapshot(self):
        event = self.adopt()
        first = self.save([event], [self.note()])
        add_candidate(self.conn, self.config, self.eid, event, snapshot={'title': '不允许覆盖'})
        self.report(extra_keywords=['新词'])
        save_source(self.conn, self.config, 'default', {'source_id': 'cls_morning', 'enabled': False})
        self.assertEqual(ed.latest_decision(self.conn, self.eid), first)
        self.assertEqual(pool_items(self.conn, self.view(), self.eid)[0]['title'], '合成线索1')

    def test_immutable_export_survives_later_selection_and_removal(self):
        event = self.adopt()
        self.save([event], [self.note()])
        record, pack, text = self.export()
        remove_candidate(self.conn, self.eid, event, 1)
        again, new_pack, new_text = self.export()
        self.assertEqual(record, again)
        self.assertEqual(pack, new_pack)
        self.assertEqual(text, new_text)

    def test_manual_validation_rejects_bad_dates_urls_types_and_duplicates(self):
        for change in ({'source_url': 'http://127.0.0.1/'}, {'published_at': 'yesterday'},
                       {'data_date': '2026-02-31'}, {'selected': 'yes'}, {'content': ''}, {'market': 'bad'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_notes([self.note(**change)])
        with self.assertRaises(ValueError):
            validate_notes([self.note(), self.note()])

    def test_upgrade_from_v5_preserves_tables_and_backfills_only_explicit_material(self):
        path = self.root / 'old-v5.db'
        paths = db.migration_files()
        with patch.object(db, 'migration_files', return_value=paths[:5]), closing(db.connect(path, mode='fixture')) as conn:
            db.sync_registry(conn, self.config)
            edition = ed.create_edition(conn, self.config, product_code='513130', edition_date='2026-09-15',
                                       news_cutoff='2026-09-14T23:59:59+08:00', market_cutoffs={})
            imported = ed.import_evidence_payload(conn, {'title': '旧版用户材料', 'content': '合成旧资料', 'is_fixture': True}, edition_id=edition['edition_id'])
            ed.save_decision(conn, edition_id=edition['edition_id'], selected_event_id=imported['event_id'],
                             selected_fact_ids=[], expected_revision=0, actor_type='human')
            before = {t: [tuple(r) for r in conn.execute('SELECT * FROM ' + t)] for t in db._tables(conn) if t != 'fn_schema_migrations'}
        with closing(db.connect(path, mode='fixture')) as conn:
            for t, rows in before.items():
                self.assertEqual(rows, [tuple(r) for r in conn.execute('SELECT * FROM ' + t)])
            pool = pool_items(conn, self.config, edition['edition_id'])
            self.assertEqual(len(pool), 1)
            self.assertEqual(pool[0]['title'], '旧版用户材料')
            self.assertEqual(ed.latest_decision(conn, edition['edition_id'])['decision_revision'], 1)
            self.assertIsNone(conn.execute('PRAGMA foreign_key_check').fetchone())
        self.assertEqual(len(list(self.root.glob('old-v5.db.backup-*'))), 1)

    def test_server_restart_preserves_personal_source_sections(self):
        from fastnews.server import serve
        db.sync_registry(self.conn, self.view())
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM fn_sources ORDER BY source_id')]
        fake = Mock(server_port=0)
        fake.serve_forever.side_effect = KeyboardInterrupt
        with patch('fastnews.server.make_server', return_value=fake), patch('builtins.print'):
            serve(self.path, self.config, offline=True, port=0)
        self.assertEqual(before, [tuple(r) for r in self.conn.execute('SELECT * FROM fn_sources ORDER BY source_id')])


if __name__ == '__main__':
    unittest.main()
