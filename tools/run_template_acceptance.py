"""Frozen six-case replay; preserve raw outputs, selections and baseline hashes."""
import argparse
from contextlib import closing
from copy import deepcopy
import hashlib,json,sqlite3,subprocess,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews import db,editorial as ed,writing as w,writing_direct as direct
from fastnews.config import load_config
from fastnews.source_catalog import configured_view
from fastnews.task_control import TaskControl
from fastnews.writing_templates import select_template
from fastnews.writing_delivery import render,word
BASE=ROOT/'data/direct_draft_20260929'
OUT=ROOT/'data/editorial_templates_20260929'

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def progress(value):print(json.dumps(value,ensure_ascii=True),flush=True)

def prepare():
    OUT.mkdir(exist_ok=True)
    if not (OUT/'live.db').exists():
        with sqlite3.connect(BASE/'live.db') as src,sqlite3.connect(OUT/'live.db') as dst:src.backup(dst)
    return json.loads((BASE/'cases.json').read_text(encoding='utf-8'))

def run(case):
    target=OUT/(case['id']+'.json')
    if target.exists():return dict(case=case['id'],status='already_recorded')
    baseline=BASE/(case['id']+'-attempt2.json')
    old=json.loads(baseline.read_text(encoding='utf-8'))
    with closing(db.connect(OUT/'live.db',mode='live')) as conn:
        cfg=configured_view(conn,load_config())
        cfg['_reader_profile']={'settings':{'host':'codex','web_search':False}}
        cfg['_host_runtime']={'model':'gpt-6-sol','reasoning_effort':'high'}
        decision=ed.latest_decision(conn,case['edition_id']);control=TaskControl(900)
        task=w.accept_run(conn,cfg,'generate',dict(edition_id=case['edition_id'],decision_revision=decision['decision_revision'],request_id=uuid.uuid4().hex),control)
        row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(task['run_id'],)).fetchone()
        data=json.loads(row['input_json'])
        frozen=deepcopy(old['run_input']['snapshot'])
        frozen.update(workflow=direct.FORMAT,rules=direct.policy(),skill_version=direct.skill_fingerprint(case['code']),
            company_template=select_template(case['code'],frozen['news_cutoff']))
        data.update(snapshot=frozen,allow_verify=False,supplement_latest=False,
            controlled_replay={'baseline_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),
                'source_sha256':w.fingerprint(frozen['sources']),'network_news_disabled':True,
                'selection_purpose':'isolated_test_selection_not_editorial_approval'})
        with conn:conn.execute('UPDATE fn_writing_runs SET input_json=? WHERE run_id=?',(w.dumps(data),task['run_id']))
        result=dict(case,baseline=str(baseline.relative_to(ROOT)),baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
            code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            run_id=task['run_id'],selection_hash=ed._hash(decision),controlled_replay=data['controlled_replay'])
        progress(dict(case=case['id'],status='started',run_id=task['run_id']))
        try:result.update(version=w.execute_run(conn,task['run_id'],control,progress=progress),status='returned')
        except Exception as exc:result.update(status=getattr(exc,'code',None) or 'failed',error=str(exc))
        row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(task['run_id'],)).fetchone()
        result.update(record=w.run_record(row),run_input=json.loads(row['input_json']))
        if row['version_id']:
            v=w.public_version(w.version(conn,row['version_id']));result['version']=v
            target.with_suffix('.md').write_text(render(v['content']),encoding='utf-8');target.with_suffix('.docx').write_bytes(word(v['content']))
            for key,label in [('working_version_id','起草稿'),('edited_version_id','编辑稿')]:
                vid=v['content'].get('pipeline',{}).get(key)
                if vid:
                    version=w.public_version(w.version(conn,vid));result[label]=version
                    target.with_name(target.stem+'-'+label+'.md').write_text(render(version['content']),encoding='utf-8')
        result['selection_unchanged']=ed._hash(ed.latest_decision(conn,case['edition_id']))==result['selection_hash']
        result['baseline_unchanged']=hashlib.sha256(baseline.read_bytes()).hexdigest()==result['baseline_sha256']
        write(target,result)
        return dict(case=case['id'],status=result['status'],failure=result['record'].get('error'),
            checks_status=result.get('version',{}).get('checks',{}).get('status'),
            elapsed=result['record'].get('usage',{}).get('total_elapsed_seconds'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--case');args=p.parse_args()
    cases=prepare()
    if args.run:
        for case in cases:
            if not args.case or args.case==case['id']:progress(run(case))
    else:progress({'cases':len(cases),'mode':'frozen_news_no_supplement'})
