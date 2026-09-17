from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastnews.config import load_config
from fastnews.db import connect, sync_registry
from fastnews.editorial import create_edition
from fastnews.pilot import PilotError, pilot_report, record_pilot_feedback
from fastnews.research import research_product
from fastnews.adapters import NoneAdapter


class PilotTests(unittest.TestCase):
    """All clocks, feedback and calendars below are explicit synthetic test inputs."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'pilot.db'
        self.conn = connect(self.path,mode='fixture')
        self.addCleanup(self.conn.close)
        self.config = load_config()
        sync_registry(self.conn,self.config)

    def research(self, *, conn=None, code='513130', day='2026-09-14', run_day=None, config=None):
        conn = conn or self.conn
        config = config or self.config
        cutoff_day = date.fromisoformat(day)-timedelta(days=1)
        edition = create_edition(conn,config,product_code=code,edition_date=day,
            news_cutoff=cutoff_day.isoformat()+'T23:59:59+08:00',market_cutoffs={'HK':{'date':cutoff_day.isoformat(),'source':'fixture calendar declaration'}})
        instant = (run_day or day)+'T09:00:00+08:00'
        with patch('fastnews.research.utc_now',return_value=instant):
            report = research_product(conn,config,code,edition_id=edition['edition_id'],now=instant,discovery_adapter=NoneAdapter())
        return edition,report

    def record(self, edition, run, *, conn=None, config=None, **kwargs):
        return record_pilot_feedback(conn or self.conn,config or self.config,edition_id=edition['edition_id'],
            research_run_id=run['run_id'],actor_type='human',now='2026-09-15T23:00:00+08:00',**kwargs)

    def test_unknown_measurements_stay_null_and_fixture_does_not_count(self):
        edition,run = self.research()
        record = self.record(edition,run)
        self.assertIsNone(record['accepted_event_ids'])
        self.assertIsNone(record['first_three_contains_accepted'])
        self.assertIsNone(record['actual_minutes'])
        self.assertIsNone(record['correction_count'])
        report = pilot_report(self.conn,'513130')['products'][0]
        self.assertEqual(report['record_count'],1)
        self.assertEqual(report['real_trading_session_count'],0)
        self.assertEqual(report['status'],'not_accepted_insufficient_trial_data')
        self.assertFalse(report['cohorts'][0]['automatic_rule_update'])

    def test_feedback_requires_explicit_human_actor(self):
        edition,run = self.research()
        for actor in ('model','system',None):
            with self.assertRaises(PilotError):
                record_pilot_feedback(self.conn,self.config,edition_id=edition['edition_id'],research_run_id=run['run_id'],actor_type=actor)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_pilot_feedback').fetchone()[0],0)

    def test_finished_run_must_belong_to_same_edition(self):
        edition,run = self.research()
        other,other_run = self.research(code='513530')
        with self.assertRaises(PilotError):
            self.record(edition,other_run)
        with self.assertRaises(PilotError):
            self.record(edition,{'run_id':'not-existing'})
        with self.conn:
            self.conn.execute("UPDATE fn_research_runs SET status='running' WHERE run_id=?",(run['run_id'],))
        with self.assertRaises(PilotError):
            self.record(edition,run)

    def test_repeating_identical_feedback_is_idempotent_not_another_day(self):
        edition,run = self.research()
        one = self.record(edition,run,accepted_event_ids=[])
        two = self.record(edition,run,accepted_event_ids=[])
        self.assertEqual(one,two)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_pilot_feedback').fetchone()[0],1)
        self.assertFalse(one['first_three_contains_accepted'])
        with self.assertRaisesRegex(PilotError,'already has feedback'):
            self.record(edition,run,accepted_event_ids=[],correction_count=1)

    def test_another_run_on_same_day_cannot_create_extra_sample(self):
        edition,run = self.research()
        self.record(edition,run)
        _,new_run = self.research()
        with self.assertRaisesRegex(PilotError,'already has feedback'):
            self.record(edition,new_run)

    def test_future_or_backdated_feedback_is_rejected(self):
        edition,run = self.research()
        with self.assertRaisesRegex(PilotError,'Future'):
            self.record(edition,run,feedback_date='2026-09-16')
        with self.assertRaisesRegex(PilotError,'precede'):
            self.record(edition,run,feedback_date='2026-09-13')

    def test_unknown_candidate_ids_and_invalid_measurements_are_rejected(self):
        edition,run = self.research()
        for changes in ({'accepted_event_ids':['invented']},{'baseline_minutes':-1},{'actual_minutes':float('nan')},
                        {'correction_count':True},{'missed_clues':['']},{'trading_day_confirmed':True}):
            with self.subTest(changes=changes),self.assertRaises(PilotError):
                self.record(edition,run,**changes)

    def test_ten_fixture_days_never_become_live_acceptance(self):
        for number in range(1,11):
            edition,run = self.research(day=f'2026-09-{number:02d}')
            self.record(edition,run,trading_day_confirmed=True,trading_day_source='synthetic calendar',
                        baseline_minutes=30,actual_minutes=15,accepted_event_ids=[])
        product = pilot_report(self.conn,'513130')['products'][0]
        self.assertEqual(product['record_count'],10)
        self.assertEqual(product['real_trading_session_count'],0)
        self.assertEqual(product['cohorts'][0]['remaining_to_ten'],10)

    def test_historical_replay_never_counts_as_an_actual_trial_day(self):
        with closing(connect(Path(self.temp.name)/'replay.db',mode='live')) as live:
            sync_registry(live,self.config)
            edition,run = self.research(conn=live,day='2026-09-10',run_day='2026-09-14')
            record = self.record(edition,run,conn=live,trading_day_confirmed=True,trading_day_source='test declaration')
            self.assertFalse(record['counts_as_real_trial_session'])
            self.assertIn('historical_replay_not_a_live_trial_day',record['limitations'])

    def test_unconfirmed_calendar_is_not_guessed_from_weekday(self):
        with closing(connect(Path(self.temp.name)/'calendar.db',mode='live')) as live:
            sync_registry(live,self.config)
            edition,run = self.research(conn=live,day='2026-09-14')
            record = self.record(edition,run,conn=live)
            self.assertFalse(record['counts_as_real_trial_session'])
            self.assertIn('trading_day_not_confirmed',record['limitations'])

    def test_rule_changes_split_cohorts_instead_of_combining_ten_days(self):
        with closing(connect(Path(self.temp.name)/'cohort.db',mode='live')) as live:
            sync_registry(live,self.config)
            for number in range(1,11):
                config = deepcopy(self.config)
                config['routing']['version'] = 'trial-a' if number<=5 else 'trial-b'
                edition,run = self.research(conn=live,config=config,day=f'2026-09-{number:02d}')
                self.record(edition,run,conn=live,config=config,trading_day_confirmed=True,
                    trading_day_source='explicit synthetic day declaration',baseline_minutes=20,actual_minutes=10,accepted_event_ids=[])
            product = pilot_report(live,'513130')['products'][0]
            self.assertEqual(product['real_trading_session_count'],10)
            self.assertEqual(product['version_cohort_count'],2)
            self.assertEqual([c['real_trading_session_count'] for c in product['cohorts']],[5,5])
            self.assertEqual(product['status'],'not_accepted_insufficient_trial_data')

    def test_sufficient_same_version_samples_still_require_human_review(self):
        with closing(connect(Path(self.temp.name)/'ten.db',mode='live')) as live:
            sync_registry(live,self.config)
            for number in range(1,11):
                edition,run = self.research(conn=live,day=f'2026-09-{number:02d}')
                self.record(edition,run,conn=live,trading_day_confirmed=True,trading_day_source='synthetic test calendar',
                            accepted_event_ids=[],baseline_minutes=20,actual_minutes=12,correction_count=0)
            product = pilot_report(live,'513130')['products'][0]
            cohort = product['cohorts'][0]
            self.assertEqual(product['status'],'ready_for_human_review')
            self.assertEqual(cohort['final_acceptance'],'human_review_required')
            self.assertEqual(cohort['observed_time_reduction_fraction'],.4)
            self.assertFalse(cohort['trial_targets']['first_three_accepted_at_least_80_percent'])
            self.assertFalse(cohort['automatic_rule_update'])

    def test_report_retains_registered_routes_and_flags_three_original_focus_products(self):
        report = pilot_report(self.conn)
        self.assertEqual({p['product_code'] for p in report['products']},set(self.config['products']))
        self.assertEqual(sum(p['focus_product'] for p in report['products']),3)
        self.assertTrue(all(p['status']=='not_accepted_insufficient_trial_data' for p in report['products']))
        with self.assertRaises(PilotError):
            pilot_report(self.conn,'000000')

    def test_explicit_human_revision_keeps_audit_but_counts_one_day(self):
        edition,run = self.research()
        first = self.record(edition,run)
        changed = self.record(edition,run,baseline_minutes=30,actual_minutes=20,expected_revision=first['revision'])
        self.assertEqual(changed['revision'],2)
        self.assertEqual(changed['previous_feedback_id'],first['feedback_id'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fn_pilot_feedback').fetchone()[0],2)
        product = pilot_report(self.conn,'513130')['products'][0]
        self.assertEqual(product['record_count'],1)
        self.assertEqual(product['audit_revision_count'],2)
        with self.assertRaisesRegex(PilotError,'expected_revision'):
            self.record(edition,run,actual_minutes=18,expected_revision=1)


if __name__=='__main__':
    unittest.main()
