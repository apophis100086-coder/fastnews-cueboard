"""Explicit live acceptance against an already running local dashboard.

Adds normal metadata batches to its live database; never fabricates news or
edits human decisions. Run explicitly after starting the local server.
"""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import time
from urllib.request import build_opener, ProxyHandler, Request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--date')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--output', default='data/daily_acceptance/live_api.json')
    args=parser.parse_args()
    base=f'http://127.0.0.1:{args.port}'
    opener=build_opener(ProxyHandler({}))
    token=None

    def request(path, payload=None):
        headers={}
        data=None
        if payload is not None:
            headers={'Content-Type':'application/json', 'Origin':base, 'X-Fastnews-CSRF':token}
            data=json.dumps(payload).encode('utf-8')
        with opener.open(Request(base+path, data=data, headers=headers), timeout=25) as response:
            return json.load(response)

    state=request('/api/state')
    if state['is_fixture']:
        raise RuntimeError('Live acceptance requires a live server; fixture results are not live news')
    token=state['csrf_token']
    selected=args.date or state['default_day']
    output=Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def snapshot():
        with sqlite3.connect(state['workspace_id']) as conn:
            return {
                'decisions':conn.execute('SELECT * FROM fn_decisions ORDER BY rowid').fetchall(),
                'cursors':conn.execute('SELECT source_id,cursor,resume_json,coverage_start,coverage_end,truncation_reason FROM fn_sources ORDER BY source_id').fetchall(),
                'fixture_versions':conn.execute('SELECT count(*) FROM fn_article_versions WHERE is_fixture=1').fetchone()[0],
            }

    def job(action, payload):
        job_id=request('/api/'+action,payload)['job_id']
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            result=request('/api/job?id='+job_id)
            if result['status']!='running':
                if result['status']!='completed':
                    raise RuntimeError(result.get('error','job failed'))
                return result['result']
            time.sleep(.5)
        raise TimeoutError('Live source job did not complete in 240 seconds')

    before=snapshot()
    scan=job('scan',{'date':selected})
    views={}
    for day in (selected, (date.fromisoformat(selected)-timedelta(days=1)).isoformat()):
        view=request('/api/daily?date='+day)
        views[day]={'counts':view['counts'], 'notice':view['notice'],
                    'samples':[{key:item[key] for key in ('title','url','source_name','report_published_at','first_published_at','pending')} for item in view['items'][:3]]}
    probe=job('probe',{}) if args.probe else None
    after=snapshot()
    checks={'human_decisions_preserved':before['decisions']==after['decisions'],
            'default_incremental_cursors_preserved':before['cursors']==after['cursors'],
            'no_fixture_versions':after['fixture_versions']==0,
            'zero_llm':scan['usage']['llm_requests']==0,
            'no_fulltext':scan['parameters']['fetch_full_text'] is False,
            'selected_date_used':scan['parameters']['collection_date']==selected}
    report={'is_fixture':False,'selected_date':selected,'scan':scan,'views':views,
            'probe_summary':probe['summary'] if probe else None,'checks':checks}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output.resolve()),'views':{day:value['counts'] for day,value in views.items()},
                      'scan_counts':scan['counts'],'articles_new':scan['articles_new'],
                      'probe_summary':report['probe_summary'],'checks':checks},ensure_ascii=False))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__=='__main__':
    main()
