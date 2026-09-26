"""Offline checks for live acceptance bookkeeping, never call a host."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tools import run_v64_acceptance as live


class AcceptanceToolTests(unittest.TestCase):
    def test_feedback_revision_saves_new_original_without_replacing_parent(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)),\
            patch.object(live.w,'task_attempts',return_value=10000),\
            patch.object(live.ed,'latest_decision',return_value={'decision_revision':1}),\
            patch.object(live.w,'accept_run',return_value={'run_id':'test_run'}) as accept,\
            patch.object(live.w,'execute_run',return_value={'content':{'text':'new original'},'checks':{'status':'ready_to_confirm'}}):
            folder=Path(root);live.save(folder/'editions.json',{'513130':'e1'})
            parent=folder/'513130-outline.json';live.save(parent,{'stage':'outline','product_code':'513130','edition_id':'e1','version_id':'v1'})
            original=parent.read_bytes();notes=folder/'review.txt';notes.write_text('Explicit synthetic review',encoding='utf-8')
            args=SimpleNamespace(code='513130',stage='outline',revision=2,parent_outline=parent,feedback_file=notes)
            live.write(None,{},args)
            self.assertEqual(parent.read_bytes(),original)
            self.assertEqual(live.read(folder/'513130-outline-r2.json')['content']['text'],'new original')
            self.assertEqual(accept.call_args.args[3]['parent_version_id'],'v1')
            self.assertEqual(accept.call_args.args[3]['feedback'],'Explicit synthetic review')

    def test_revision_waits_on_shared_budget_before_loading_feedback(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)),\
            patch.object(live,'reader_limits',return_value={'max_tasks_per_day':12}),\
            patch.object(live.w,'task_attempts',return_value=12),patch.object(live.w,'accept_run') as accept:
            result=live.write(None,{},SimpleNamespace(code='513130',stage='outline',revision=2))
            self.assertEqual(result['status'],'budget_wait');accept.assert_not_called()

    def test_outline_file_cannot_cross_product_edition_or_fixed_folder(self):
        with tempfile.TemporaryDirectory() as root,patch.object(live,'FOLDER',Path(root)):
            path=Path(root)/'outline.json'
            live.save(path,{'stage':'outline','product_code':'513130','edition_id':'e1','version_id':'v1'})
            self.assertEqual(live.saved_outline(path,'513130','e1')['version_id'],'v1')
            for code,eid in [('588710','e1'),('513130','e2')]:
                with self.assertRaises(ValueError):live.saved_outline(path,code,eid)
            with self.assertRaises(ValueError):live.saved_outline(Path(root).parent/'outside.json','513130','e1')
            with self.assertRaises(ValueError):live.writing_key(SimpleNamespace(code='513130',stage='outline',revision=0))

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
            patch.object(live,'reader_limits',return_value={'max_tasks_per_day':12}),\
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
            patch.object(live,'reader_limits',return_value={'max_tasks_per_day':12}),\
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
