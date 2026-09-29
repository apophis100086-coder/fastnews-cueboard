from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from fastnews.adapters import DiscoveryError, DiscoveryPage
from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.pipeline import scan_morning
from fastnews.__main__ import source_status


class SourceSelectionTests(unittest.TestCase):
    def test_explicit_source_preserves_original_flags_and_other_failures(self):
        config = load_config()
        class Adapter:
            def __init__(self): self.seen = []
            def discover(self, sid, *args):
                self.seen.append(sid)
                raise DiscoveryError('adapter_not_connected')
        with tempfile.TemporaryDirectory() as folder, closing(connect(Path(folder)/'live.db')) as conn:
            sync_registry(conn, config)
            scan_morning(conn, config, no_network=True)
            adapter = Adapter()
            r = scan_morning(conn, config, adapter=adapter, source_ids=['fda'])
            self.assertEqual(adapter.seen, ['fda'])
            self.assertFalse(config['sources']['fda']['morning_scan'])
            self.assertEqual(r['coverage']['requested_sources_this_run'], 1)
            states = {x['source_id']: x for x in source_status(conn)['sources']}
            self.assertEqual(states['miit']['last_run']['status'], 'unavailable')
            with self.assertRaises(ValueError):
                scan_morning(conn, config, adapter=adapter, source_ids=['unregistered'])

    def test_terms_failure_keeps_manual_only_even_with_previous_success(self):
        class Adapter:
            def discover(self, *args): raise DiscoveryError('source_terms_manual_only')
        with tempfile.TemporaryDirectory() as folder, closing(connect(Path(folder)/'live.db')) as conn:
            config = load_config()
            sync_registry(conn, config)
            with conn:
                conn.execute("UPDATE fn_sources SET last_success_at='2026-09-14',capability_status='ready_metadata' WHERE source_id='stcn_egs_morning'")
            scan_morning(conn, config, adapter=Adapter(), source_ids=['stcn_egs_morning'])
            row = conn.execute("SELECT capability_status FROM fn_sources WHERE source_id='stcn_egs_morning'").fetchone()
            self.assertEqual(row[0], 'manual_only')
