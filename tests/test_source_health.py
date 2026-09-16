import json
import sqlite3
import unittest
from fastnews.source_health import describe, attach_health


class SourceHealthTests(unittest.TestCase):
    def source(self,**kw):
        return dict(source_id='demo',enabled=True,collector_available=True,**kw)

    def test_unimplemented_and_terms_are_not_network_failures(self):
        self.assertEqual(describe(dict(source_id='x',enabled=True,collector_available=False))['kind'],'not_connected')
        h=describe(self.source(automation_policy='manual_only'),{'status':'failed','error_code':'source_terms_manual_only'})
        self.assertEqual(h['kind'],'restricted');self.assertFalse(h['retryable'])

    def test_dns_failure_is_explained_as_before_site_connection(self):
        h=describe(self.source(),{'status':'failed','error_code':'public_dns_failed'})
        self.assertEqual(h['kind'],'network_error');self.assertIn('尚未连接',h['note']);self.assertTrue(h['retryable'])

    def test_successful_empty_window_not_failed_or_complete_coverage(self):
        h=describe(self.source(),{'status':'partial','items_seen':0})
        self.assertEqual(h['kind'],'empty');self.assertIn('不表示当天没有新闻',h['note'])

    def test_access_refusal_does_not_offer_retry(self):
        for code in ['robots_disallowed','http_412','access_challenge','tls_certificate_invalid']:
            h=describe(self.source(),{'status':'failed','error_code':code})
            self.assertEqual(h['kind'],'restricted');self.assertFalse(h['retryable'])

    def test_latest_research_recovery_replaces_old_morning_failure(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        self.addCleanup(c.close)
        c.execute('CREATE TABLE fn_research_runs(finished_at TEXT,report_json TEXT)')
        c.execute('INSERT INTO fn_research_runs VALUES(?,?)',('2026-09-16T05:00:00+00:00',json.dumps({'fixed_source_discovery':{'sources':[{'source_id':'demo','status':'read','items_seen':5}]}})))
        source=self.source()
        summary={'sources':[{'source_id':'demo','last_run':{'finished_at':'2026-09-16T11:00:00+08:00','status':'failed','error_code':'public_dns_failed'}}]}
        attach_health(c,[source],summary)
        self.assertEqual(source['health']['kind'],'ready')
        self.assertEqual(source['health']['operation'],'product')
        self.assertEqual(summary['currently_readable'],1)
        summary['latest_probe']={'sources':[{'source_id':'demo','last_probe_at':'2026-09-16T14:00:00+08:00','run_status':'failed','error_code':'timeout'}]}
        attach_health(c,[source],summary)
        self.assertEqual(source['health']['kind'],'network_error')
        self.assertEqual(summary['currently_readable'],0)

    def test_new_adapter_is_pending_even_if_old_version_unavailable(self):
        self.assertEqual(describe(self.source(),{'status':'unavailable','error_code':'adapter_not_connected'})['kind'],'pending')

    def test_unimplemented_skip_keeps_last_real_robots_observation(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;self.addCleanup(c.close)
        c.execute('CREATE TABLE fn_research_runs(finished_at TEXT,report_json TEXT)')
        source=dict(source_id='demo',enabled=True,collector_available=False)
        summary={'sources':[{'source_id':'demo','last_run':{'finished_at':'2026-09-16T13:00:00+08:00','status':'unavailable','error_code':'adapter_not_connected'}}],
                 'latest_probe':{'sources':[{'source_id':'demo','last_probe_at':'2026-09-16T12:00:00+08:00','run_status':'failed','error_code':'robots_disallowed'}]}}
        attach_health(c,[source],summary)
        self.assertEqual(source['health']['error_code'],'robots_disallowed')
        self.assertEqual(source['health']['kind'],'restricted')
        self.assertIn('采集器也尚未接通',source['health']['note'])
        self.assertFalse(source['health']['retryable'])

if __name__=='__main__':unittest.main()
