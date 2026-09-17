"""Opt-in real host comparison in a new store; never changes the user's database.

Run with --live. Public test outputs stay in ignored data/targeted_acceptance.
The diagnostic listing fetches are separate from normal morning collection.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews import db, editorial, host_provider
from fastnews.config import load_config
from fastnews.live_sources import LiveAdapter
from fastnews.metadata import parse_instant, time_bounds
from fastnews.reading_reports import generate_report, adopt_report_item
from fastnews.reader_limits import reader_limits
from fastnews.source_catalog import configured_view
from fastnews.search_plan import build_search_plan


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--product',choices=list(load_config()['products']),default='517120')
    parser.add_argument('--skip-program-probes',action='store_true',help='Isolate host recall without rerunning collectors')
    parser.add_argument('--cutoff',help='Explicit information cutoff for reproducible comparison')
    parser.add_argument('--mode',choices=['baseline','explore'],default='baseline')
    parser.add_argument('--seed-public-report',action='store_true',help='Reuse one prior public report as known context in an isolated exploration')
    args=parser.parse_args()
    if not args.live:
        parser.error('--live is required; this uses the logged-in host quota')
    now=datetime.now(timezone.utc)
    day=now.astimezone(ZoneInfo('Asia/Shanghai')).date()
    # Include prior acceptance attempts and the real application in this guard.
    lo,hi=time_bounds(day.isoformat())
    attempted_ids=set()
    stores=[ROOT/'data/fastnews.db',*sorted((ROOT/'data/targeted_acceptance').glob('*/acceptance.db'))]
    for path in stores:
        if path.exists():
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as conn:
                attempted_ids.update(r[0] for r in conn.execute('SELECT report_id,created_at FROM fn_reading_reports') if lo<=parse_instant(r[1])<=hi)
    attempts=len(attempted_ids)
    if attempts>=reader_limits()['max_tasks_per_day']:
        raise SystemExit('Daily host task budget reached including acceptance attempts')
    dest=ROOT/'data/targeted_acceptance'/now.strftime('%Y%m%dT%H%M%S%fZ')
    dest.mkdir(parents=True,exist_ok=False)
    cutoff=datetime.combine(day,datetime.min.time(),ZoneInfo('Asia/Shanghai'))-timedelta(seconds=1)
    if args.cutoff:
        cutoff=parse_instant(args.cutoff)
        if cutoff>now:
            parser.error('cutoff cannot be in the future')
    conn=db.connect(dest/'acceptance.db',mode='live')
    config=load_config()
    db.sync_registry(conn,config)
    config=configured_view(conn,config)
    db.sync_registry(conn,config)
    edition=editorial.create_edition(conn,config,product_code=args.product,
        edition_date=day.isoformat(),news_cutoff=cutoff.isoformat(),market_cutoffs={})
    seeded=None
    if args.seed_public_report:
        with sqlite3.connect((ROOT/'data/fastnews.db').resolve().as_uri()+'?mode=ro',uri=True) as original:
            original.row_factory=sqlite3.Row
            for row in original.execute("SELECT * FROM fn_reading_reports WHERE product_code=? AND profile_id='default' AND status IN ('complete','partial') ORDER BY rowid DESC",(args.product,)):
                public_input=json.loads(row['input_json'])
                if parse_instant(public_input['news_cutoff'])>cutoff:
                    continue
                record=dict(row);record['edition_id']=edition['edition_id']
                public_input.update(edition_id=edition['edition_id'],acceptance_seed=True)
                record['input_json']=json.dumps(public_input,ensure_ascii=False)
                with conn:
                    conn.execute('INSERT INTO fn_reading_reports('+','.join(record)+') VALUES('+','.join('?' for _ in record)+')',list(record.values()))
                seeded=row['report_id'];break
    cases=[s['source_id'] for s in build_search_plan(config,args.product)['selected_sources']][:6]
    sources={s['source_id']:s for s in config['reader_sources']}
    history_path=ROOT/'data/source_repair_20260916/probe-final.json'
    history=json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else {}
    historical={s['source_id']:s for s in history.get('sources',[])}
    adapter=LiveAdapter(config,dest/'http')
    local=[]
    for sid in cases:
        source=sources[sid]
        previous=historical.get(sid,{})
        row={'source_id':sid,'url':source['url'],'checked_at':datetime.now(timezone.utc).isoformat(),
             'prior_checked_at':previous.get('last_probe_at'),'prior_error_code':previous.get('error_code'),
             'search_enabled':source['search_enabled'],'host_read_allowed':source['host_read_allowed'],
             'policy_reason':source['access_reason'],'program_scan_enabled':source['program_scan_enabled']}
        started=time.monotonic()
        if args.skip_program_probes:
            row.update(program_status='not_requested',program_reason='host_recall_only',records=None)
        elif sid not in adapter.supported_sources:
            row.update(program_status='not_requested',program_reason='adapter_or_permission_unavailable',records=None)
        else:
            try:
                page=adapter.discover(sid,cutoff-timedelta(hours=72),cutoff,6)
                row.update(program_status='metadata_read',records=len(page.records),
                           records_in_window=[{'title':r['title'],'url':r['url'],
                               'published_at':r.get('report_published_at')} for r in page.records])
            except Exception as exc:
                row.update(program_status='failed',program_reason=getattr(exc,'code',type(exc).__name__),records=0)
        row['program_elapsed_seconds']=round(time.monotonic()-started,2)
        local.append(row)
    write(dest/'program-comparison.json',{'cases':local,'usage':adapter.stats})
    original_parse=host_provider.parse_codex_output
    def capture(output,final):
        events=[]
        for line in output.splitlines():
            try:
                event=json.loads(line)
            except ValueError:
                continue
            if event.get('type') in {'turn.completed','turn.failed','error'} or event.get('item',{}).get('type')=='web_search':
                events.append(event)
        write(dest/'actual-cli-events.json',events)
        write(dest/'raw-host-result.json',json.loads(final))
        return original_parse(output,final)
    def runner(host,prompt,schema,**kwargs):
        write(dest/'public-input.json',json.loads(prompt.split('INPUT_JSON:\n',1)[1]))
        with patch.object(host_provider,'parse_codex_output',capture):
            return host_provider.run_host(host,prompt,schema,**kwargs)
    version=subprocess.run(host_provider.host_command('codex')+['--version'],capture_output=True,text=True,
        timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0).stdout.strip()
    manifest={'is_fixture':False,'purpose':'isolated live acceptance, no production selections',
        'started_at':now.isoformat(),'product_code':args.product,'cutoff':cutoff.isoformat(),
        'cli_version':version,'existing_day_attempts':attempts,'cases':local,
        'host_tasks_planned':1,'request_mode':args.mode,'seed_public_report_id':seeded,
        'per_source_host_usage':None,'full_paragraph_availability':None}
    write(dest/'summary.json',manifest)
    print(json.dumps({'acceptance_directory':str(dest),'stage':'host_start'},ensure_ascii=False),flush=True)
    try:
        report=generate_report(conn,config,'default','product',day.isoformat(),edition=edition,runner=runner,
            request_mode=args.mode,request_id=('live-'+dest.name) if args.mode=='explore' else None,
            research_question='兼顾重要新进展和截至本期仍适用的产品背景，补充已有材料遗漏的方向。' if args.mode=='explore' else '',
            progress=lambda info:print(json.dumps(info,ensure_ascii=False),flush=True))
        write(dest/'report.json',report)
        # This selection belongs only to this disposable validation store.
        ids=[adopt_report_item(conn,'default',report['report_id'],item['item_id'],edition['edition_id'])['event_id']
             for item in report['output']['items']]
        exported=None
        if ids:
            editorial.save_decision(conn,edition_id=edition['edition_id'],selected_event_id=None,
                selected_fact_ids=[],selected_clue_ids=ids,manual_notes=[],expected_revision=0,
                actor_type='human',config=config)
            exported=editorial.export_handoff(conn,config,edition_id=edition['edition_id'],
                decision_revision=1,output_dir=dest/'exports')
        for row in local:
            host=urlsplit(row['url']).hostname.removeprefix('www.')
            matched=[s for item in report['output']['items'] for s in item['sources']
                     if urlsplit(s['url']).hostname.removeprefix('www.')==host]
            row['host_materials']=[{k:s.get(k) for k in ('url','title','material_identity','source_excerpt_verified',
                'original_url','published_at','first_published_at','retrieved_at','tool_ref','missing_items')} for s in matched]
            row['host_found_in_validated_report']=bool(matched)
            row['exported_with_event']=bool(matched and exported)
            row['coverage']=next((s for s in report['output']['coverage'] if s['source_id']==row['source_id']),None)
        manifest.update(status=report['status'],usage=report['usage'],
            incremental_results=report['output'].get('incremental_results'),export=exported,
            web_actions=report['output']['web_actions'],trace_content_available=bool(report['output']['tool_trace'].get('tool_excerpts')))
    except Exception as exc:
        latest=conn.execute('SELECT status,error_code,usage_json FROM fn_reading_reports ORDER BY rowid DESC LIMIT 1').fetchone()
        manifest.update(status='failed',error_type=type(exc).__name__,error_message=str(exc),
            usage=json.loads(latest['usage_json']) if latest and latest['usage_json'] else None)
    finally:
        manifest['finished_at']=datetime.now(timezone.utc).isoformat()
        write(dest/'summary.json',manifest)
        conn.close()
    print(json.dumps({'directory':str(dest),'status':manifest['status'],'usage':manifest.get('usage')},ensure_ascii=False),flush=True)
    return 0 if manifest['status'] in {'complete','partial'} else 1


if __name__=='__main__':
    raise SystemExit(main())
