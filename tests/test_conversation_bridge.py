"""Conversation tasks use the same application and verified saved result view."""
from contextlib import closing
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastnews.config import load_config
from fastnews.db import sync_registry
from fastnews.dashboard_client import DashboardClient
from fastnews.server import LocalApplication


class ConversationBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = LocalApplication(Path(temp.name)/'fixture.db', load_config(), offline=True)
        with closing(self.app.connection()) as conn:
            sync_registry(conn, self.app.config)
        self.app.mutate('reader-profile', dict(profile_id='default', name='测试',
            host='workbuddy', agent_direct=True, web_search=True))

    def start(self, **extra):
        data = dict(host='workbuddy', kind='research', product_code='588710')
        data.update(extra)
        with patch('fastnews.server.threading.Thread') as thread:
            result = self.app.mutate('conversation-start', data)
        return result, thread

    def test_conversation_creates_normal_research_without_selecting_materials(self):
        result, thread = self.start()
        job = self.app.jobs[result['job_id']]
        self.assertEqual(job['action'], 'research')
        self.assertEqual(job['origin'], 'conversation')
        self.assertEqual(job['product_code'], '588710')
        self.assertTrue(job['agent_direct'])
        args = thread.call_args.kwargs['args']
        self.assertTrue(args[2]['use_host'])
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_editions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_decisions').fetchone()[0], 0)
        self.assertIsNone(self.app.detail(result['edition_id'])['decision'])

    def test_wrong_host_unknown_product_and_busy_never_create_extra_periods(self):
        for extra in [dict(host='codex'), dict(product_code='not-a-product')]:
            with self.assertRaises(ValueError):
                self.start(**extra)
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_editions').fetchone()[0], 0)
        self.start()
        with self.assertRaisesRegex(ValueError, '已有任务'):
            self.start()
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_editions').fetchone()[0], 1)

    def test_explicit_cutoff_is_preserved_and_morning_uses_same_task_pipeline(self):
        result, _ = self.start(date='2026-09-15', news_cutoff='2026-09-14T23:59:59+08:00')
        view = self.app.detail(result['edition_id'])
        self.assertEqual(view['edition']['news_cutoff'], '2026-09-14T23:59:59+08:00')
        self.app.active_job = None
        morning, thread = self.start(kind='digest', date='2026-09-15')
        job = self.app.jobs[morning['job_id']]
        self.assertEqual(job['action'], 'digest')
        self.assertEqual(job['date'], '2026-09-15')
        self.assertIsNone(job['edition_id'])
        self.assertEqual(thread.call_args.kwargs['args'][2]['lookback_days'], 7)

    def test_activity_retains_terminal_result_and_excludes_payloads(self):
        result, _ = self.start()
        running = self.app.activity()
        self.assertEqual(running['job']['status'], 'running')
        job = self.app.jobs[result['job_id']]
        job['progress'].append(dict(stage='agent_direct', agent_status='processing', message='检索中'))
        self.assertNotEqual(running['revision'], self.app.activity()['revision'])
        job.update(status='completed', result={'private_material': 'not-for-status-feed'})
        self.app.last_job = result['job_id']
        self.app.active_job = None
        saved = self.app.activity()
        self.assertEqual(saved['job']['job_id'], result['job_id'])
        self.assertEqual(saved['job']['status'], 'completed')
        self.assertNotIn('result', saved['job'])
        saved['job']['progress'][0]['message'] = 'mutated'
        self.assertEqual(job['progress'][0]['message'], '检索中')

    def test_client_completion_requires_report_in_persisted_view(self):
        client = DashboardClient.__new__(DashboardClient)
        client.url = 'http://127.0.0.1:8807/'
        report = dict(report_id='report-1', status='complete', output={'items': [{'title': '合成'}]})
        job = dict(job_id='job-1', status='completed', action='research', edition_id='edition-1',
                   result={'reading_report': report})
        with patch.object(client, 'call', side_effect=[copy.deepcopy(job), {}]):
            self.assertFalse(client.result('job-1')['saved'])
        with patch.object(client, 'call', side_effect=[copy.deepcopy(job), {'reading_report': report}]):
            saved = client.result('job-1')
            self.assertTrue(saved['saved'])
            self.assertEqual(saved['item_count'], 1)
        job['status'] = 'failed'
        with patch.object(client, 'call', return_value=job) as call:
            self.assertFalse(client.result('job-1')['saved'])
            self.assertEqual(call.call_count, 1)

    def test_client_rejects_wrong_workspace_before_any_write(self):
        with patch.object(DashboardClient, 'call', return_value={'workspace_id': 'wrong.db'}) as call:
            with self.assertRaisesRegex(ValueError, '不属于这个项目'):
                DashboardClient('http://127.0.0.1:8807/', self.app.database)
            self.assertEqual(call.call_count, 1)

    def test_real_file_response_is_validated_saved_and_exposed_to_dashboard(self):
        from fastnews import agent_runner
        from fastnews.workspace import identity
        from tests.test_exploration import raw, TRACE
        root = Path(self.app.database).parent/'agent-exchange'
        collected = dict(view={'product_code': '517120'}, match_counts={'core': 0, 'background': 0},
                         usage={'llm_requests': 0, 'web_search_requests': 0})
        with patch('fastnews.collection_task.collect', return_value=collected):
            result = self.app.mutate('conversation-start', dict(host='workbuddy', kind='research',
                product_code='517120', date='2026-09-15', news_cutoff='2026-09-14T23:59:59+08:00'))
            job_id = result['job_id']
            control = self.app.controls[job_id]
            def stop_worker():
                control.cancelled.set()
                end = time.monotonic() + 3
                while self.app.active_job and time.monotonic() < end:
                    time.sleep(.02)
            self.addCleanup(stop_worker)
            end = time.monotonic() + 8
            pending = []
            while time.monotonic() < end:
                pending = agent_runner.pending_requests(root)
                if pending:
                    break
                time.sleep(.02)
            self.assertTrue(pending, self.app.jobs[job_id])
            path = pending[0]['path']
            meta = agent_runner.claim_request(path, root=root, workspace_id=identity(self.app.database))
            agent_runner.submit_response(path,
                dict({k: meta[k] for k in agent_runner.BINDING}, payload=raw(), trace=TRACE),
                root=root, workspace_id=identity(self.app.database))
            while time.monotonic() < end and self.app.active_job:
                time.sleep(.02)
            job = self.app.jobs[job_id]
            self.assertEqual(job['status'], 'completed', job)
        persisted = self.app.detail(result['edition_id'])
        self.assertEqual(len(persisted['reading_report']['output']['items']), 1)
        self.assertEqual(len(persisted['product_clues']['recent']), 1)
        self.assertIsNone(persisted['decision'])
        self.assertEqual(self.app.activity()['job']['status'], 'completed')
        with closing(self.app.connection()) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM fn_candidate_pool').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
