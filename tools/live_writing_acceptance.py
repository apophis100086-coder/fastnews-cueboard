"""Explicit live acceptance using public articles in an isolated database.

Run manually, never by CI. Outline, revise and draft consume existing host quota.
Prepare/adopt-clinical read public sources; recheck runs only program checks.
The public article dates are manually checked against the publisher links.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastnews import db, editorial as ed, writing as w
from fastnews.config import load_config
from fastnews.source_catalog import configured_view
from fastnews.task_control import TaskControl
from fastnews.writing_background import add_candidate

CASES={
 '588090': [('https://fund.jrj.com.cn/2026/09/11140758410647.shtml','2026-09-11','金融界：科创50调样与产业进展')],
 '513530': [('https://fund.jrj.com.cn/2026/09/14123758425859.shtml','2026-09-14','金融界：港股红利与分红背景')],
 '517120': [('https://fund.jrj.com.cn/2026/08/20134658185658.shtml','2026-08-20','金融界：创新药临床与产业背景'),
            ('https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-04-22/517120_20260422_XZ3R.pdf','2026-04-22','上交所基金披露：517120产品身份')],
}

CLINICAL_URL=('https://www.merck.com/news/merck-and-moderna-announce-phase-3-interpath-001-trial-of-intismeran-autogene-plus-keytruda-met-endpoints-of-recurrence-free-survival-rfs-and-distant-metastasis-free-survival-dmfs-in-patient/')


def adopt_clinical(conn,config,edition,decision):
    """Explicit test-only adoption; never run as an automatic search side effect."""
    if edition['product_code']!='517120':
        raise ValueError('此人工补料验收只对应创新药案例')
    page=w._read_source(CLINICAL_URL,TaskControl(),25)
    if page.get('error'):raise RuntimeError('public_source_unavailable: '+page['error'])
    payload={'is_fixture':False,'title':'Merck：III期临床结果原始公告','content':page['text'],
        'source_type':'original_publication','source_url':CLINICAL_URL,'declared_source':'Merck',
        'first_published_at':'2026-08-19','origin_role':'reported','facts':[],
        'event':{'subject':'517120','action':'公开资料核对','object':'III期临床结果原始公告','phase':'announcement'}}
    imported=ed.import_evidence_payload(conn,payload,edition_id=edition['edition_id'],privacy_class='public',
        actual_source_path='user:writing-live-explicit-clinical-adoption')
    mid=imported['event_id'];add_candidate(conn,config,edition['edition_id'],mid)
    selected=list(decision.get('selected_clue_ids',[]))
    if mid in selected:return decision
    selected.append(mid)
    return ed.save_decision(conn,edition_id=edition['edition_id'],selected_event_id=mid,
        selected_fact_ids=decision['selected_fact_ids'],selected_clue_ids=selected,
        manual_notes=decision.get('manual_notes',[]),must_include_ids=list(dict.fromkeys(decision.get('must_include_ids',[])+[mid])),
        expected_revision=decision['decision_revision'],actor_type='human',config=config,
        mainline_note=decision.get('mainline_note','')+' 本次明确采用发行人临床公告；按原文区分本次试验结果、前期随访与监管审批。')

def prepare(conn,config,code):
    edition=ed.create_edition(conn,config,product_code=code,edition_date='2026-09-17',
        news_cutoff='2026-09-16T23:59:59+08:00',market_cutoffs={})
    if ed.latest_decision(conn,edition['edition_id']):
        return edition
    ids=[]
    for url,published,title in CASES[code]:
        page=w._read_source(url,TaskControl(),25)
        if page.get('error'):
            raise RuntimeError('public_source_unavailable: '+page['error'])
        # Keep original publisher wording as local evidence, not as public export.
        payload={'is_fixture':False,'title':title,'content':page['text'],
            'source_type':'original_publication','source_url':url,'declared_source':title.split('：')[0],
            'first_published_at':published,'origin_role':'reported','facts':[],
            'event':{'subject':code,'action':'公开资料核对','object':title,'phase':'announcement'}}
        imported=ed.import_evidence_payload(conn,payload,edition_id=edition['edition_id'],privacy_class='public',
            actual_source_path='user:writing-live-acceptance-public-page')
        mid=imported['event_id'];add_candidate(conn,config,edition['edition_id'],mid);ids.append(mid)
    ed.save_decision(conn,edition_id=edition['edition_id'],selected_event_id=ids[0],selected_fact_ids=[],
        selected_clue_ids=ids,manual_notes=[],must_include_ids=ids,expected_revision=0,actor_type='human',config=config,
        mainline_note='测试本产品的产业或经营背景，来源中的旧产品资金、短期涨跌幅不得入稿。其他基金的事实不能移植给本产品，产品身份依据不足应列缺口。')
    return edition

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--code',choices=list(CASES),required=True)
    parser.add_argument('--stage',choices=['prepare','outline','revise','draft','adopt-clinical','recheck'],default='outline')
    parser.add_argument('--feedback',default='将重点放在事实进展及其传导关系，删去重复分析；保留本产品的自然承接。')
    args=parser.parse_args();root=ROOT/'data'/'writing_acceptance';root.mkdir(exist_ok=True)
    with closing(db.connect(root/'live.db')) as conn:
        config=load_config();db.sync_registry(conn,config);view=configured_view(conn,config)
        edition=prepare(conn,view,args.code);eid=edition['edition_id'];decision=ed.latest_decision(conn,eid)
        if args.stage=='prepare':print(json.dumps({'edition_id':eid}));return
        if args.stage=='adopt-clinical':
            result=adopt_clinical(conn,view,edition,decision)
            print(json.dumps({'edition_id':eid,'decision_revision':result['decision_revision'],'host_calls':0}));return
        data=dict(edition_id=eid,decision_revision=decision['decision_revision'],request_id=uuid.uuid4().hex)
        versions=w.workflow(conn,eid)['versions']
        if args.stage=='recheck':
            draft=next(v for v in reversed(versions) if v['stage']=='draft')
            result=w.recheck_draft(conn,dict(version_id=draft['version_id'],request_id=data['request_id']))
            (root/(args.code+'-draft-recheck.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            if not result['checks']['errors']:
                (root/(args.code+'-draft.md')).write_text(result['content']['text'],encoding='utf-8')
            print(json.dumps({'version_id':result['version_id'],'checks':result['checks'],'host_calls':0}));return
        if args.stage in {'revise','draft'}:
            outline=next(v for v in reversed(versions) if v['stage']=='outline')
            if args.stage=='revise':
                data.update(parent_version_id=outline['version_id'],feedback=args.feedback)
            else:
                confirmed=w.confirm_outline(conn,dict(data,outline_version_id=outline['version_id'],text=outline['content']['text']+'\n表达要求：事实与分析衔接自然，不重复解释。'))
                data.update(request_id=uuid.uuid4().hex,confirmed_version_id=confirmed['version_id'])
        stage='draft' if args.stage=='draft' else 'outline'
        control=TaskControl();run=w.accept_run(conn,view,stage,data,control)
        def progress(item):print(json.dumps(item,ensure_ascii=True),flush=True)
        result=w.execute_run(conn,run['run_id'],control,progress=progress)
        (root/(args.code+'-'+args.stage+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'code':args.code,'stage':args.stage,'checks':result['checks'],'version_id':result['version_id']},ensure_ascii=True),flush=True)
        if stage=='draft' and not result['checks']['errors']:
            (root/(args.code+'-draft.md')).write_text(result['content']['text'],encoding='utf-8')

if __name__=='__main__':main()
