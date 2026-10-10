"""Human trial records and cautious summaries; no synthetic ten-day acceptance."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import sqlite3
from zoneinfo import ZoneInfo

from .editorial import get_edition
from .metadata import parse_instant


class PilotError(ValueError):
    pass


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _numeric(value,name,integer=False):
    if value is None:
        return
    if type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or value < 0:
        raise PilotError(name+' must be nonnegative or null')


def _text(value,name,maximum=2000):
    if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>maximum):
        raise PilotError(name+' must be nonempty bounded text or null')


def _mode(conn):
    row = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
    if row is None or row[0] not in {'fixture','live'}:
        raise PilotError('Pilot requires a mode-marked database')
    return row[0]


def _versions(edition,context,report):
    """Input snapshots change daily; the analytical rules must remain fixed."""
    return {'profile_version':edition.get('profile_version'),
            'profile_hash':_hash(edition.get('profile')),
            'score_version':edition.get('rule_version'),'scoring_hash':_hash(edition.get('scoring')),
            'output_policy_version':edition.get('output_policy_version'),
            'output_policy_hash':_hash(edition.get('output_policy')),
            'routing_version':context.get('rule_version'),'routing_hash':context.get('routing_hash'),
            'research_version':context.get('research_version'),
            'prompt_versions':context.get('prompt_versions'),
            'provider_versions':report.get('provider_versions')}


def record_pilot_feedback(conn, config, *, edition_id, research_run_id, actor_type,
                          feedback_date=None, accepted_event_ids=None,
                          baseline_minutes=None, actual_minutes=None, correction_count=None,
                          missed_clues=None, reason=None, trading_day_confirmed=False,
                          trading_day_source=None, expected_revision=None, now=None):
    """Persist one explicitly supplied observation per product and trial day.

    accepted_event_ids=None means not yet judged; [] means explicitly no
    acceptable candidate. Missing measurements always remain null.
    """
    if actor_type != 'human':
        raise PilotError('Pilot feedback requires an explicit human action')
    mode = _mode(conn)
    edition = get_edition(conn,edition_id)
    row = conn.execute('SELECT * FROM fn_research_runs WHERE run_id=?',(research_run_id,)).fetchone()
    if row is None or row['edition_id'] != edition_id or row['product_code'] != edition['product_code']:
        raise PilotError('Research run must reference this exact Edition and product')
    if row['mode'] != mode or bool(edition['is_fixture']) != (mode=='fixture'):
        raise PilotError('Fixture and live trial data must stay separate')
    if row['status'] not in {'complete','partial'} or not row['report_json']:
        raise PilotError('Only finished research runs can receive trial feedback')
    report = json.loads(row['report_json'])
    context = json.loads(row['context_json'])
    zone = ZoneInfo(config['runtime']['runtime']['timezone'])
    instant = now or datetime.now(timezone.utc)
    if isinstance(instant,str):
        instant = parse_instant(instant)
    if instant.tzinfo is None:
        raise PilotError('Trial clock requires explicit timezone')
    today = instant.astimezone(zone).date()
    trial_day = date.fromisoformat(edition['edition_date'])
    feedback_day = date.fromisoformat(feedback_date) if feedback_date else today
    run_started = parse_instant(row['started_at'])
    run_day = run_started.astimezone(zone).date()
    run_finished = parse_instant(row['finished_at']) if row['finished_at'] else run_started
    if trial_day > today or run_started > instant or run_finished > instant or feedback_day > today:
        raise PilotError('Future trial/feedback dates cannot count as observations')
    if feedback_day < run_day:
        raise PilotError('Feedback cannot precede the research run')
    if type(trading_day_confirmed) is not bool:
        raise PilotError('trading_day_confirmed must be an explicit boolean')
    _text(trading_day_source,'trading_day_source')
    if trading_day_confirmed and not trading_day_source:
        raise PilotError('A human-confirmed trading day requires its calendar source')
    _numeric(baseline_minutes,'baseline_minutes')
    _numeric(actual_minutes,'actual_minutes')
    _numeric(correction_count,'correction_count',integer=True)
    _text(reason,'reason')
    event_ids = [item['event_id'] for item in report.get('results',[])]
    if accepted_event_ids is not None:
        if (not isinstance(accepted_event_ids,list) or any(not isinstance(item,str) for item in accepted_event_ids)
            or len(set(accepted_event_ids)) != len(accepted_event_ids) or set(accepted_event_ids)-set(event_ids)):
            raise PilotError('Accepted IDs must be unique candidates from the cited research run')
    if missed_clues is not None:
        if not isinstance(missed_clues,list) or len(missed_clues)>20:
            raise PilotError('missed_clues must be up to 20 human notes or null')
        for clue in missed_clues:
            _text(clue,'missed_clue',maximum=1000)
            if clue is None:
                raise PilotError('A missed clue cannot be null inside the list')
    versions = _versions(edition,context,report)
    signature = _hash(versions)
    same_day_run = run_day == trial_day
    eligible = mode=='live' and same_day_run and trading_day_confirmed
    limitations = []
    if mode=='fixture':
        limitations.append('fixture_not_real_pilot')
    if not same_day_run:
        limitations.append('historical_replay_not_a_live_trial_day')
    if not trading_day_confirmed:
        limitations.append('trading_day_not_confirmed')
    if accepted_event_ids is None:
        limitations.append('candidate_acceptance_not_recorded')
    if baseline_minutes is None or actual_minutes is None:
        limitations.append('timing_pair_missing')
    data = {'mode':mode,'is_fixture':mode=='fixture','product_code':edition['product_code'],
            'edition_id':edition_id,'research_run_id':research_run_id,'trial_date':trial_day.isoformat(),
            'feedback_date':feedback_day.isoformat(),'research_date':run_day.isoformat(),
            'actor_type':'human','accepted_event_ids':accepted_event_ids,'shown_first_three':event_ids[:3],
            'first_three_contains_accepted':None if accepted_event_ids is None else bool(set(accepted_event_ids)&set(event_ids[:3])),
            'baseline_minutes':baseline_minutes,'actual_minutes':actual_minutes,'correction_count':correction_count,
            'missed_clues':missed_clues,'reason':reason,'trading_day_confirmed':trading_day_confirmed,
            'trading_day_source':trading_day_source,'calendar_validation':'human_declared' if trading_day_confirmed else 'not_available',
            'counts_as_real_trial_session':eligible,'version_signature':signature,'versions':versions,
            'research_status':row['status'],'research_usage':report.get('usage'),
            'limitations':limitations,'automatic_rule_update':False}
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision<0):
        raise PilotError('expected_revision must be an integer revision or null')
    existing = conn.execute('SELECT data_json FROM fn_pilot_feedback WHERE product_code=? AND trial_date=? ORDER BY revision DESC LIMIT 1',
                            (edition['product_code'],trial_day.isoformat())).fetchone()
    if existing:
        saved = json.loads(existing[0])
        if {key:value for key,value in saved.items() if key not in {'feedback_id','revision','previous_feedback_id'}}==data:
            return saved
        if expected_revision != saved['revision']:
            raise PilotError('This product/day already has feedback; provide its latest expected_revision to append a correction, not another trial day')
        revision, previous = saved['revision']+1, saved['feedback_id']
    else:
        if expected_revision not in {None,0}:
            raise PilotError('No existing feedback matches expected_revision')
        revision, previous = 1, None
    data.update(revision=revision,previous_feedback_id=previous)
    data['feedback_id'] = 'pilot_' + _hash(data)[:32]
    try:
        with conn:
            conn.execute('INSERT INTO fn_pilot_feedback(feedback_id,mode,product_code,edition_id,research_run_id,trial_date,feedback_date,actor_type,revision,previous_feedback_id,version_signature,data_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (data['feedback_id'],mode,edition['product_code'],edition_id,research_run_id,trial_day.isoformat(),
                          feedback_day.isoformat(),'human',revision,previous,signature,_json(data),instant.astimezone(timezone.utc).isoformat()))
    except sqlite3.IntegrityError:
        raise PilotError('Feedback changed concurrently; read the latest revision and retry explicitly') from None
    return data


def _cohort_summary(records, signature):
    valid = [record for record in records if record['counts_as_real_trial_session']]
    judged = [record for record in valid if record['first_three_contains_accepted'] is not None]
    timed = [record for record in valid if record['baseline_minutes'] is not None and record['actual_minutes'] is not None]
    baseline = sum(record['baseline_minutes'] for record in timed)
    actual = sum(record['actual_minutes'] for record in timed)
    reduction = (baseline-actual)/baseline if baseline>0 else None
    accepted = sum(record['first_three_contains_accepted'] for record in judged)
    days = sorted({record['trial_date'] for record in valid})
    evidence_ready = len(days)>=10 and len(judged)>=10 and len(timed)>=10
    return {'version_signature':signature,'record_count':len(records),'real_trading_session_count':len(days),
            'real_trading_dates':days,'remaining_to_ten':max(0,10-len(days)),
            'acceptance_judgment_count':len(judged),'first_three_accepted_count':accepted,
            'first_three_acceptance_rate':accepted/len(judged) if judged else None,
            'timing_pair_count':len(timed),'baseline_total_minutes':baseline if timed else None,
            'actual_total_minutes':actual if timed else None,'observed_time_reduction_fraction':reduction,
            'correction_count_known_samples':sum(record['correction_count'] is not None for record in valid),
            'reported_corrections':sum(record['correction_count'] for record in valid if record['correction_count'] is not None) if any(record['correction_count'] is not None for record in valid) else None,
            'missed_clue_notes':[{'trial_date':record['trial_date'],'notes':record['missed_clues']} for record in records if record['missed_clues']],
            'sample_status':'ready_for_human_review' if evidence_ready else 'not_accepted_insufficient_trial_data',
            'trial_targets':{'ten_trading_sessions':len(days)>=10,
                'first_three_accepted_at_least_80_percent':None if len(judged)<10 else accepted/len(judged)>=.8,
                'observed_time_reduction_at_least_20_percent':None if len(timed)<10 or reduction is None else reduction>=.2},
            'final_acceptance':'human_review_required','automatic_rule_update':False}


def pilot_report(conn, product_code=None):
    mode = _mode(conn)
    # Member identities support evidence imports but are not standalone routes.
    products = [row[0] for row in conn.execute('SELECT code,config_json FROM fn_products ORDER BY code')
                if 'topic_weights' in json.loads(row[1])]
    if product_code is not None and product_code not in products:
        raise PilotError('Unknown product code')
    revisions = [json.loads(row[0]) for row in conn.execute('SELECT data_json FROM fn_pilot_feedback ORDER BY product_code,trial_date,revision')]
    latest = {(record['product_code'],record['trial_date']):record for record in revisions}
    records = list(latest.values())
    result = []
    for code in products:
        if product_code is not None and code != product_code:
            continue
        entries = [record for record in records if record['product_code']==code]
        signatures = sorted({record['version_signature'] for record in entries})
        cohorts = [_cohort_summary([record for record in entries if record['version_signature']==signature],signature) for signature in signatures]
        result.append({'product_code':code,'focus_product':code in {'513130','513530','588710'},
            'record_count':len(entries),'fixture_record_count':sum(record['is_fixture'] for record in entries),
            'audit_revision_count':sum(record['product_code']==code for record in revisions),
            'real_trading_session_count':sum(record['counts_as_real_trial_session'] for record in entries),
            'version_cohort_count':len(cohorts),'cohorts':cohorts,
            'status':'ready_for_human_review' if any(c['sample_status']=='ready_for_human_review' for c in cohorts) else 'not_accepted_insufficient_trial_data',
            'missing_data_notes':sorted({note for record in entries for note in record['limitations']}) if entries else ['no_human_trial_records']})
    return {'mode':mode,'is_fixture':mode=='fixture','products':result,
            'ten_day_acceptance':'human_review_required_after_sufficient_fixed_version_real_sessions',
            'automatic_rule_update':False,
            'note':'开发测试不计作真实交易日试跑；按产品和固定版本分别累计，不拼凑跨版本天数。日历来自明确人工声明，未接交易所日历接口。'}
