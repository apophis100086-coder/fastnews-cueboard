"""Offline checks for live acceptance bookkeeping, never call a host."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools import run_v64_acceptance as live


class AcceptanceToolTests(unittest.TestCase):
    def test_explicit_retry_preserves_failed_bytes_and_does_not_erase_success(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)):
            path=Path(root)/'morning.json'
            original=b'{"status":"failed","message":"original failure"}'
            path.write_bytes(original)
            live.archive_failed_attempt(path,'synthetic diagnosed correction')
            archive=next(p for p in (Path(root)/'attempts').glob('*.json') if not p.name.endswith('.retry.json'))
            self.assertEqual(archive.read_bytes(),original)
            self.assertFalse(path.exists())
            live.save(path,{'status':'partial','output':{'items':[]}})
            saved=path.read_bytes()
            with self.assertRaises(ValueError):live.archive_failed_attempt(path,'must not replace success')
            self.assertEqual(path.read_bytes(),saved)

    def test_retry_still_waits_on_shared_budget_and_leaves_failure_intact(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)),\
            patch.object(live.w,'task_attempts',return_value=12),patch.object(live,'collect') as collect:
            path=Path(root)/'morning.json';live.save(path,{'status':'failed'})
            original=path.read_bytes()
            result=live.research(None,{},SimpleNamespace(kind='morning',retry_reason='diagnosed'))
            self.assertEqual(result['status'],'budget_wait')
            self.assertEqual(path.read_bytes(),original)
            self.assertFalse((Path(root)/'attempts').exists())
            collect.assert_not_called()

    def test_daily_limit_stops_before_collection_or_host(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)),\
            patch.object(live.w,'task_attempts',return_value=12),patch.object(live,'collect') as collect,\
            patch.object(live.rr,'generate_report') as host:
            result=live.research(None,{},SimpleNamespace(kind='morning'))
            self.assertEqual(result['status'],'budget_wait')
            collect.assert_not_called();host.assert_not_called()

    def test_comparison_distinguishes_not_run_unshown_match_and_no_human_grade(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)):
            folder=Path(root);holdout=folder/'holdout.json'
            live.save(holdout,dict(events=[dict(id='heldout',products=['513130','588710','517120'],
                expected_role='recent',match_groups=[['合成催化']])]))
            live.save(folder/'513130-research.json',dict(output=dict(items=[],raw_model_output={'title':'合成催化'})))
            live.save(folder/'588710-research.json',dict(output=dict(items=[{'title':'合成催化','editorial_role':'recent','sources':[]}])) )
            report=live.compare(holdout)
            self.assertEqual([r['status'] for r in report['recall']],['discovered_not_displayed','candidate_match_needs_review','not_run'])
            self.assertIsNone(report['adoption_rate'])
            self.assertTrue(all(r['actual_edit_minutes'] is None for r in report['draft_comparison']))


if __name__=='__main__':unittest.main()
