"""Explicit fixed-material comparison in the existing isolated acceptance DB.

Never reads holdout answers, collects news, changes production selection, or
overwrites an earlier attempt. Test framework confirmation is not adoption.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews import db,editorial as ed,writing as w
from fastnews.config import load_config
from fastnews.source_catalog import configured_view,profile
from fastnews.task_control import TaskControl

BASE=ROOT/'data/v64_acceptance_20260926'
OUTPUT=ROOT/'data/writing_skill_redesign_20260927'


def save(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def progress(value):print(json.dumps(value,ensure_ascii=True),flush=True)


def run(args):
    if not args.confirm_test_outline:raise ValueError('需要显式 --confirm-test-outline；测试确认不是采用批准')
    if not 1<=args.revision<=99:raise ValueError('Invalid revision')
    target=OUTPUT/(args.code+'-'+args.variant+'-r'+str(args.revision)+'.json')
    if target.exists():return dict(status='already_recorded',path=str(target))
    frozen=json.loads((OUTPUT/'baseline-runs.json').read_text(encoding='utf-8'))[args.code]
    original=frozen['input']; original_snapshot=original['snapshot']
    editions=json.loads((BASE/'editions.json').read_text(encoding='utf-8'))
    if editions[args.code]!=original_snapshot['edition_id']:raise ValueError('Baseline edition mismatch')
    with closing(db.connect(BASE/'live.db',mode='live')) as conn:
        current=ed.latest_decision(conn,editions[args.code])
        if current['decision_revision']!=original_snapshot['decision_revision']:
            raise ValueError('选材已改变，不能继续声称固定材料对照')
        config=configured_view(conn,load_config());config['_reader_profile']=profile(conn,'default')
        config['_host_runtime']=dict(model='gpt-6-sol',reasoning_effort='high')
        control=TaskControl(480)
        prior=w.version(conn,original['parent_id'])
        if prior['stage']!='confirmed_outline':raise ValueError('Baseline lacks explicit confirmed framework')
        confirmed=w.confirm_outline(conn,dict(edition_id=prior['edition_id'],decision_revision=prior['decision_revision'],
            outline_version_id=prior['parent_id'],text=prior['content']['confirmed_text'],request_id=uuid.uuid4().hex))
        request=dict(edition_id=prior['edition_id'],decision_revision=prior['decision_revision'],
            confirmed_version_id=confirmed['version_id'],reader_profile='default',request_id=uuid.uuid4().hex)
        task=w.accept_run(conn,config,'draft',request,control)
        if args.variant=='legacy':
            # Replaying the exact frozen legacy writer is deliberately confined
            # to this tool and fixed test DB, never exposed through public APIs.
            replay=deepcopy(original)
            replay['host_runtime']=config['_host_runtime']
            current_input=json.loads(conn.execute('SELECT input_json FROM fn_writing_runs WHERE run_id=?',(task['run_id'],)).fetchone()[0])
            replay['implementation_hashes']=current_input['implementation_hashes']
            replay['comparison_test']=dict(original_version=frozen['original_version'],
                purpose='isolated_test_not_editorial_approval',confirmed_test_version=confirmed['version_id'])
            with conn:conn.execute('UPDATE fn_writing_runs SET input_json=? WHERE run_id=?',(w.dumps(replay),task['run_id']))
        result=dict(case=args.code,variant=args.variant,model=config['_host_runtime'],
            run_id=task['run_id'],purpose='isolated_test_not_editorial_approval',
            original_version=frozen['original_version'],test_confirmation=confirmed['version_id'],
            baseline_input_hash=hashlib.sha256(w.dumps(original).encode()).hexdigest())
        try:
            version=w.execute_run(conn,task['run_id'],control,progress=progress)
            result.update(status=version['checks']['status'],version=version)
        except Exception as exc:
            result.update(status=getattr(exc,'code',None) or 'failed',error=type(exc).__name__,message=str(exc))
        row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(task['run_id'],)).fetchone()
        result.update(record=w.run_record(row),run_input=json.loads(row['input_json']))
        if row['version_id']:
            last=w.public_version(w.version(conn,row['version_id']))
            result.setdefault('version',last)
            target.with_suffix('.md').write_text(last['content']['text'],encoding='utf-8')
            initial=last['content'].get('pipeline',{}).get('working_version_id')
            if initial:
                working=w.public_version(w.version(conn,initial));result['working_version']=working
                target.with_name(target.stem+'-before-edit.md').write_text(working['content']['text'],encoding='utf-8')
        save(target,result)
        return dict(status=result['status'],path=str(target),checks=result.get('version',{}).get('checks',{}),
            usage=result['record']['usage'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--code',choices=['513130','588710','517120'],required=True)
    parser.add_argument('--variant',choices=['legacy','newsroom'],required=True)
    parser.add_argument('--revision',type=int,default=1)
    parser.add_argument('--confirm-test-outline',action='store_true')
    progress(run(parser.parse_args()))
