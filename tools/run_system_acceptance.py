"""Reviewable integration evidence. Live HTTP is an explicit separate run."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'data' / 'system_acceptance' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True)
    db = folder / ('live.db' if args.live else 'fixture.db')
    commands = []

    def run(name, *arguments, expected=0):
        cmd = [sys.executable, '-m', 'fastnews', '--db', str(db)]
        if not args.live:
            cmd.append('--offline')
        cmd.extend(arguments)
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                env=dict(os.environ, PYTHONIOENCODING='utf-8'), timeout=360)
        raw = result.stdout if result.returncode == 0 else result.stderr
        out = folder / (name + '.json')
        out.write_text(raw, encoding='utf-8')
        commands.append({'name': name, 'command': cmd, 'exit_code': result.returncode, 'expected_exit_code': expected, 'output': str(out)})
        if result.returncode != expected:
            raise AssertionError(f'{name} returned {result.returncode}; see {out}')
        print(json.dumps({'step': name, 'exit_code': result.returncode}, ensure_ascii=False), flush=True)
        return json.loads(raw).get('result', {})

    run('init', 'init-db')
    run('config', 'validate-config')
    if args.live:
        run('source_probe', 'probe-sources')
    first = run('morning', 'scan-morning', '--bootstrap-hours', '72')
    repeat = run('repeat', 'scan-morning')
    assert repeat['articles_new'] == 0 and repeat['article_versions_new'] == 0
    if args.live:
        run('explicit_fda', 'scan-morning', '--source', 'fda')
    else:
        run('product', 'import-product-data', 'examples/editorial_product.synthetic.json', '--product-code', '513530')
        run('history', 'import-history', 'examples/editorial_history.synthetic.json', '--product-code', '513530')
    for code in ('513130', '513530', '588710', '517120'):
        run('route_' + code, 'route-product', code, '--edition-date', '2026-09-15', '--news-cutoff', '2026-09-14T23:59:59+08:00')
    edition = run('edition', 'create-edition', '513530', '--edition-date', '2026-09-15',
                  '--news-cutoff', '2026-09-14T23:59:59+08:00', '--market-cutoff', 'HK=2026-09-14@explicit_acceptance_date')
    eid = edition['edition_id']
    if not args.live:
        material = run('import', 'import-evidence', 'examples/editorial_evidence.synthetic.json', '--edition-id', eid)
        run('assess', 'assess-events', '--edition-id', eid)
        run('export_unselected_rejected', 'export-handoff', '--edition-id', eid, '--decision-revision', '0', expected=2)
        chosen = run('human_fixture_selection', 'save-decision', '--edition-id', eid, '--selected-event-id', material['event_id'],
                     '--fact-id', material['fact_ids'][0], '--actor', 'human', '--expected-revision', '0')
    research = run('research_none', 'research-product', '513530', '--edition-id', eid)
    assert research['usage']['llm_requests'] == 0 and research['usage']['search_requests'] == 0
    assert research['default_selection'] is None
    details = run('edition_after_research', 'show-edition', eid)
    if not args.live:
        assert details['decision'] == chosen
        run('export_saved', 'export-handoff', '--edition-id', eid, '--decision-revision', '1', '--output-dir', str(folder / 'exports'))
        feedback = folder / 'pilot_feedback.synthetic.json'
        feedback.write_text(json.dumps({'edition_id': eid, 'research_run_id': research['run_id'], 'accepted_event_ids': None,
                                        'baseline_minutes': None, 'actual_minutes': None, 'reason': '合成集成测试，不计真实试跑'}), encoding='utf-8')
        run('pilot_record', 'record-pilot-feedback', str(feedback))
    else:
        assert details['decision'] is None
        # Explicit public reading is scoped to the registered FDA policy; no fact or selection is made.
        fda_edition = run('public_edition', 'create-edition', '517120')
        run('public_research', 'research-product', '517120', '--edition-id', fda_edition['edition_id'], '--fetch-public-text')
    run('pilot_report', 'pilot-report')
    run('sources', 'source-status')
    report = {'mode': 'live' if args.live else 'fixture', 'database': str(db), 'commands': commands,
              'commands_passed': len(commands), 'first_scan_articles': first['articles_new'],
              'repeat_scan_articles': repeat['articles_new'], 'repeat_scan_versions': repeat['article_versions_new'],
              'is_fixture': not args.live, 'human_choices_made': not args.live,
              'ten_trading_day_trial': 'not_completed', 'completed_at': datetime.now(timezone.utc).isoformat()}
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(folder / 'report.json'), 'commands_passed': len(commands)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
