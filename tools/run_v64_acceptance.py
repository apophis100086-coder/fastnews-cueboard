"""Resumable, explicit live acceptance. Never invoked by application or CI.

All attempts share the project quota. Holdout answers are read ONLY by compare,
after retrieval. Test selection/confirmation require explicit commands and occur
only in the fixed acceptance database; they are not editorial approval.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews import db,editorial as ed,writing as w,reading_reports as rr
from fastnews.config import load_config
from fastnews.source_catalog import configured_view,profile
from fastnews.task_control import TaskControl
from fastnews.collection_task import collect
from fastnews.reader_limits import reader_limits
from fastnews.writing_background import add_candidate

FOLDER=ROOT/'data/v64_acceptance_20260926'
OLD=ROOT/'data/product_backtest_20260926'
CODES=['588090','588710','513130','513530','517120']


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def recorded_status(path):
    if not path.exists():return 'not_run'
    value=read(path)
    return value.get('status') or value.get('checks',{}).get('status') or 'recorded'


def archive_failed_attempt(path,reason):
    """Only an explicit diagnosed retry can replace the current result pointer."""
    if not isinstance(reason,str) or not reason.strip():
        raise ValueError('A diagnosed retry reason is required')
    if path.resolve().parent!=FOLDER.resolve():
        raise ValueError('Retry must stay in the fixed acceptance folder')
    if recorded_status(path) not in {'failed','timed_out','cancelled','interrupted'}:
        raise ValueError('Only failed attempts can be retried; completed originals are immutable')
    folder=FOLDER/'attempts';folder.mkdir(exist_ok=True)
    target=folder/(path.stem+'-'+uuid.uuid4().hex+'.json')
    original=path.read_bytes()
    with target.open('xb') as stream:stream.write(original)
    markdown=path.with_suffix('.md')
    if markdown.exists():
        with target.with_suffix('.md').open('xb') as stream:stream.write(markdown.read_bytes())
    save(target.with_suffix('.retry.json'),dict(reason=reason,original_path=str(path),
        original_sha256=hashlib.sha256(original).hexdigest(),archived_path=str(target)))
    path.unlink()
    if markdown.exists():markdown.unlink()


def prepare():
    FOLDER.mkdir(exist_ok=True)
    path=FOLDER/'live.db'
    if not path.exists():
        with closing(sqlite3.connect((ROOT/'data/fastnews.db').resolve().as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(path)) as target:source.backup(target)
    if (FOLDER/'editions.json').exists():return
    with closing(db.connect(path,mode='live')) as conn:
        cfg=load_config();db.sync_registry(conn,cfg)
        editions={code:ed.create_edition(conn,cfg,product_code=code,edition_date='2026-09-26',
            news_cutoff='2026-09-25T23:59:59+08:00',market_cutoffs={})['edition_id'] for code in CODES}
        save(FOLDER/'editions.json',editions)
    old_dir=FOLDER/'old';old_dir.mkdir(exist_ok=True)
    hashes={}
    for code in CODES:
        for suffix in ['draft.json','draft.md','research.json','selection.json']:
            source=OLD/(code+'-'+suffix)
            if source.exists():
                target=old_dir/source.name
                if not target.exists():shutil.copyfile(source,target)
                hashes[target.name]=hashlib.sha256(target.read_bytes()).hexdigest()
    save(FOLDER/'baseline-hashes.json',hashes)


def progress(item):print(json.dumps(item,ensure_ascii=True),flush=True)


def research(conn,cfg,args):
    key='morning' if args.kind=='morning' else args.code+'-research'
    out=FOLDER/(key+'.json')
    if out.exists() and not getattr(args,'retry_reason',None):return dict(status='already_recorded',path=str(out))
    if w.task_attempts(conn)>=reader_limits()['max_tasks_per_day']:
        return dict(status='budget_wait',reason='shared_project_daily_limit',used=w.task_attempts(conn))
    if out.exists():archive_failed_attempt(out,args.retry_reason)
    control=TaskControl(480)
    edition=None
    if args.kind=='morning':
        from fastnews.source_catalog import section_ids
        params=dict(collection_date='2026-09-26',source_ids=section_ids(cfg,'all',collect_only=True))
        collected=collect(conn,cfg,'digest',params,control,progress)
    else:
        edition=ed.get_edition(conn,read(FOLDER/'editions.json')[args.code])
        collected=collect(conn,cfg,'research',dict(product_code=args.code,edition_id=edition['edition_id']),control,progress)
    result=rr.generate_report(conn,cfg,'default',args.kind,'2026-09-26',edition=edition,
        control=control,progress=progress,lookback_days=7,collection_result=collected)
    save(out,result)
    return dict(status=result['status'],items=len((result.get('output') or {}).get('items',[])),path=str(out))


def select(conn,cfg,args):
    if not args.selection:raise ValueError('select requires a public-material selection file')
    eid=read(FOLDER/'editions.json')[args.code]
    if ed.latest_decision(conn,eid):raise ValueError('Test selection already exists; preserve it and review explicitly')
    selection=read(Path(args.selection));ids=[]
    for source in selection['sources']:
        page=read(Path(source['page_file']))
        if page.get('error') or not page.get('text'):raise ValueError('Unread public source')
        data=dict(title=source['title'],content=page['text'],source_type=source.get('source_type','original_publication'),
            source_url=source['url'],declared_source=source['name'],first_published_at=source['published_at'],
            origin_role='reported',facts=[],event=dict(subject=args.code,action='隔离回测明确选材',object=source['title'],phase='announcement'))
        imported=ed.import_evidence_payload(conn,data,edition_id=eid,privacy_class='public',actual_source_path='backtest:explicit-v64-selection')
        mid=imported['event_id'];add_candidate(conn,cfg,eid,mid);ids.append(mid)
    result=ed.save_decision(conn,edition_id=eid,selected_event_id=ids[0],selected_fact_ids=[],selected_clue_ids=ids,
        mainline_note=selection['mainline'],manual_notes=[],must_include_ids=[],expected_revision=0,actor_type='human',config=cfg)
    save(FOLDER/(args.code+'-test-selection.json'),dict(purpose='isolated_test_not_editorial_approval',selection=selection,decision=result))
    return dict(status='test_selected',edition_id=eid)


def writing_key(args):
    revision=getattr(args,'revision',1)
    if type(revision) is not int or not 1<=revision<=99:raise ValueError('Revision must be 1..99')
    return args.code+'-'+args.stage+('' if revision==1 else '-r'+str(revision))


def saved_outline(path,code,edition_id):
    path=Path(path)
    if path.resolve().parent!=FOLDER.resolve():raise ValueError('Use an outline in the fixed acceptance folder')
    result=read(path)
    if result.get('stage')!='outline' or result.get('product_code')!=code or result.get('edition_id')!=edition_id:
        raise ValueError('Outline must belong to this product and edition')
    return result


def write(conn,cfg,args):
    out=FOLDER/(writing_key(args)+'.json')
    if out.exists() and not getattr(args,'retry_reason',None):return dict(status='already_recorded',path=str(out))
    if w.task_attempts(conn)>=reader_limits()['max_tasks_per_day']:
        return dict(status='budget_wait',reason='shared_project_daily_limit',used=w.task_attempts(conn))
    if out.exists():archive_failed_attempt(out,args.retry_reason)
    eid=read(FOLDER/'editions.json')[args.code];decision=ed.latest_decision(conn,eid)
    if not decision:raise ValueError('Explicit test selection is required')
    data=dict(edition_id=eid,decision_revision=decision['decision_revision'],reader_profile='default',request_id=uuid.uuid4().hex)
    parent_path=getattr(args,'parent_outline',None);feedback_path=getattr(args,'feedback_file',None)
    if parent_path or feedback_path:
        if args.stage!='outline' or not parent_path or not feedback_path or getattr(args,'revision',1)==1:
            raise ValueError('Outline revision requires a parent, feedback and a new revision number')
        feedback_path=Path(feedback_path)
        if feedback_path.resolve().parent!=FOLDER.resolve() or feedback_path.suffix not in {'.txt','.md'}:
            raise ValueError('Use explicit review notes in the fixed acceptance folder')
        feedback=feedback_path.read_text(encoding='utf-8')
        if not feedback.strip():raise ValueError('Review notes cannot be empty')
        parent=saved_outline(parent_path,args.code,eid)
        data.update(parent_version_id=parent['version_id'],feedback=feedback)
    if getattr(args,'outline_file',None) and args.stage!='draft':raise ValueError('outline-file is only for explicit draft confirmation')
    if args.stage=='draft':
        if not args.confirm_test_outline:raise ValueError('Explicit --confirm-test-outline required; not editorial approval')
        outline=saved_outline(getattr(args,'outline_file',None) or FOLDER/(args.code+'-outline.json'),args.code,eid)
        confirmed=w.confirm_outline(conn,dict(data,outline_version_id=outline['version_id'],text=outline['content']['text']))
        save(FOLDER/(args.code+'-test-confirmation-'+confirmed['version_id']+'.json'),dict(purpose='isolated_test_not_editorial_approval',
            outline_id=outline['version_id'],confirmed_version_id=confirmed['version_id']))
        data.update(request_id=uuid.uuid4().hex,confirmed_version_id=confirmed['version_id'])
    control=TaskControl(480);run=w.accept_run(conn,cfg,args.stage,data,control)
    result=w.execute_run(conn,run['run_id'],control,progress=progress)
    save(out,result)
    # Preserve every raw draft, including rejected or incomplete ones.
    out.with_suffix('.md').write_text(result['content']['text'],encoding='utf-8')
    return dict(status=result['checks']['status'],path=str(out),checks=result['checks'])


def compare(holdout):
    """Mechanical recall candidates require later evidence review; no AI grades."""
    rows=[]
    for event in read(Path(holdout))['events']:
        for code in event['products']:
            path=FOLDER/(code+'-research.json')
            if not path.exists():
                rows.append(dict(id=event['id'],product=code,status='not_run',reason='live_report_pending'));continue
            report=read(path);output=report.get('output') or {};hits=[]
            for item in output.get('items',[]):
                text=json.dumps(item,ensure_ascii=False).casefold()
                if any(all(term.casefold() in text for term in group) for group in event['match_groups']):hits.append(item)
            raw=json.dumps(output.get('raw_model_output',{}),ensure_ascii=False).casefold()
            raw_hit=any(all(term.casefold() in raw for term in group) for group in event['match_groups'])
            status='candidate_match_needs_review' if hits else 'discovered_not_displayed' if raw_hit else 'run_failed' if report.get('status')=='failed' else 'not_found'
            rows.append(dict(id=event['id'],product=code,status=status,expected_role=event.get('expected_role'),
                candidates=[dict(title=i['title'],role=i.get('editorial_role'),sources=i.get('sources',[])) for i in hits],
                reason='match_is_not_fact_verification' if hits else 'inspect_raw_output_and_validation_audit' if raw_hit else 'not_in_saved_output'))
    drafts=[]
    for code in ['513130','588710','517120']:
        old=FOLDER/'old'/(code+'-draft.md');new=FOLDER/(code+'-draft.json')
        drafts.append(dict(code=code,old_path=str(old) if old.exists() else None,new_path=str(new) if new.exists() else None,
            status='failed_no_complete_draft' if recorded_status(new)=='failed' else 'awaiting_editor_review' if new.exists() else 'not_run',fact_omissions=None,main_news_position=None,
            product_relationship=None,repeated_paragraphs=None,structure_retained=None,actual_edit_minutes=None,accepted=None))
    result=dict(recall=rows,draft_comparison=drafts,quality_acceptance='pending_actual_editor_feedback',
        adoption_rate=None,holdout_in_search_prompt=False)
    save(FOLDER/'comparison.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','status','research','select','write','compare'])
    p.add_argument('--code',choices=CODES);p.add_argument('--kind',choices=['morning','product'],default='product')
    p.add_argument('--stage',choices=['outline','draft'],default='outline');p.add_argument('--selection');p.add_argument('--holdout')
    p.add_argument('--revision',type=int,default=1,help='Save a separate original result, e.g. outline-r2; never overwrite r1')
    p.add_argument('--parent-outline',help='Existing outline for an explicit feedback revision')
    p.add_argument('--feedback-file',help='Local .txt/.md review notes; requires parent-outline and revision > 1')
    p.add_argument('--outline-file',help='Exact saved outline to explicitly confirm for a draft')
    p.add_argument('--retry-reason',help='Explicit diagnosed retry of a failed case; archives the untouched original and shares quota')
    p.add_argument('--codex-model',help='Explicit model experiment through the existing ChatGPT subscription; recorded in task input')
    p.add_argument('--codex-reasoning',choices=['low','medium','high','xhigh','max','ultra'])
    p.add_argument('--confirm-test-outline',action='store_true');args=p.parse_args()
    if args.action=='prepare':prepare();print('prepared');return
    if args.action=='compare':progress(compare(args.holdout));return
    if not (FOLDER/'editions.json').exists():raise ValueError('Run prepare once; never reset the acceptance database')
    with closing(db.connect(FOLDER/'live.db',mode='live')) as conn:
        cfg=configured_view(conn,load_config());cfg['_reader_profile']=profile(conn,'default')
        if args.codex_model or args.codex_reasoning:
            if cfg['_reader_profile']['settings']['host']!='codex':raise ValueError('Codex experiment requires the existing Codex host')
            cfg['_host_runtime']={k:v for k,v in {'model':args.codex_model,'reasoning_effort':args.codex_reasoning}.items() if v}
        if args.action=='status':
            result=dict(used=w.task_attempts(conn),limit=reader_limits()['max_tasks_per_day'],host=cfg['_reader_profile']['settings']['host'],
                research={key:recorded_status(FOLDER/(key+'.json')) for key in ['morning']+[c+'-research' for c in CODES]},
                drafts={c:recorded_status(FOLDER/(c+'-draft.json')) for c in ['513130','588710','517120']})
        else:
            from fastnews.budget import utc_now
            started=utc_now()
            try:
                if args.action=='research':result=research(conn,cfg,args)
                elif args.action=='select':result=select(conn,cfg,args)
                else:result=write(conn,cfg,args)
            except Exception as exc:
                # Freeze failed attempts too. A later batch cannot silently
                # rerun the same case or erase a rejected original response.
                result=dict(status='failed',error=type(exc).__name__,message=str(exc),started_at=started)
                if args.action=='research':
                    key='morning' if args.kind=='morning' else args.code+'-research'
                    row=conn.execute('SELECT * FROM fn_reading_reports WHERE created_at>=? ORDER BY rowid DESC LIMIT 1',(started,)).fetchone()
                    if row:result['record']=rr.report_record(row)
                    result['output']=(result.get('record') or {}).get('output') or {}
                elif args.action=='write':
                    key=writing_key(args)
                    row=conn.execute('SELECT input_json,run_id FROM fn_writing_runs WHERE created_at>=? ORDER BY rowid DESC LIMIT 1',(started,)).fetchone()
                    if row:
                        saved=json.loads(row['input_json'])
                        result.update(run_id=row['run_id'],raw_failed_response=saved.get('raw_failed_response'),
                            failed_host_trace=saved.get('failed_host_trace'),
                            supplemental_research=saved.get('snapshot',{}).get('supplemental_research'))
                else:key=args.code+'-selection-error'
                failed_path=FOLDER/(key+'.json')
                # A rejected retry must never overwrite the completed result it
                # tried to replace, nor an earlier failed artifact left intact.
                if failed_path.exists():failed_path=FOLDER/(key+'-rejected-'+uuid.uuid4().hex+'.json')
                save(failed_path,result)
        progress({k:v for k,v in result.items() if k not in
            {'record','output','raw_failed_response','failed_host_trace','supplemental_research'}})


if __name__=='__main__':main()
