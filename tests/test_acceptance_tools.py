"""Offline checks for live acceptance bookkeeping, never call a host."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools import run_v64_acceptance as live


class AcceptanceToolTests(unittest.TestCase):
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
