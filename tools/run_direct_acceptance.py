"""Six explicitly authorized live cases; isolated selections, no outline approval.

Copies the existing acceptance database solely to preserve old selections. The
application daily cap remains unlimited; provider subscription limits still apply.
No holdout answers or private sample originals are written to Git.
"""
import argparse
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import hashlib,json,sqlite3,subprocess,sys,uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews import db,editorial as ed,writing as w
from fastnews.config import load_config
from fastnews.source_catalog import configured_view,profile
from fastnews.writing_background import add_candidate
from fastnews.task_control import TaskControl
from fastnews.writing_delivery import render,word
BASE=ROOT/'data/v64_acceptance_20260926';OUT=ROOT/'data/direct_draft_20260929'

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def progress(value):print(json.dumps(value,ensure_ascii=True),flush=True)

def prepare():
    OUT.mkdir(exist_ok=True)
    path=OUT/'live.db'
    if not path.exists():
        with sqlite3.connect(BASE/'live.db') as src,sqlite3.connect(path) as dst:src.backup(dst)
    if (OUT/'cases.json').exists():return json.loads((OUT/'cases.json').read_text(encoding='utf-8'))
    editions=json.loads((BASE/'editions.json').read_text(encoding='utf-8'));cases=[]
    with closing(db.connect(path,mode='live')) as conn:
        config=load_config();db.sync_registry(conn,config)
        for code in ['513130','588710','dividend_family']:
            for number in [1,2]:
                eid='direct_test_'+code+'_'+str(number)
                e=ed.create_edition(conn,config,product_code=code,edition_date='2026-09-26',news_cutoff='2026-09-25T23:59:59+08:00',market_cutoffs={},edition_id=eid)
                if code!='dividend_family':
                    with conn:conn.execute('INSERT OR IGNORE INTO fn_edition_evidence SELECT ?,event_id,evidence_id FROM fn_edition_evidence WHERE edition_id=?',(eid,editions[code]))
                    old=ed.latest_decision(conn,editions[code]);ids=old['selected_clue_ids']
                    if number==2:ids=[old['selected_event_id'] or ids[0]]
                    main=old['selected_event_id'];note=old.get('mainline_note','')
                    comparison='同题材料基线，补搜新增材料另列' if number==1 else '仅保留主事件，测试自主补足背景'
                else:
                    url='https://wap.eastmoney.com/a/202609223881132972.html'
                    page=w._read_source(url,TaskControl(60),25)
                    if page.get('error'):raise ValueError('红利测试新闻原文获取失败：'+page['error'])
                    save(OUT/('family-source-'+str(number)+'.json'),page)
                    # Known publication shown on the publisher page, not a
                    # date invented from acquisition time.
                    published=page.get('published_at')
                    if not published and '09-22 13:25' in page['text'][:500] and '2026年中期分红' in page['text']:
                        published='2026-09-22T13:25:00+08:00'
                        page['date_verification']='人工核对网页顶部09-22 13:25、正文2026年与发布日期URL三者一致'
                        page['published_at']=published;save(OUT/('family-source-'+str(number)+'.json'),page)
                    if not published:raise ValueError('红利测试新闻发布日期尚未核实')
                    imported=ed.import_evidence_payload(conn,dict(is_fixture=False,title=page.get('title') or 'A股中期现金分红与红利配置',content=page['text'],source_type='original_publication',source_url=url,declared_source='东方财富网刊载界面新闻',first_published_at=published,origin_role='reported',facts=[],event=dict(subject='A股现金分红',action='披露',object='2026年中期分红与利率配置',phase='announcement')),edition_id=eid,privacy_class='public',actual_source_path='user:authorized-direct-writing-isolated-test')
                    ids=[imported['event_id']];main=ids[0]
                    note='突出中期现金分红的新进展，再自然联系红利系列七只成员。' if number==1 else '从利率环境与分红现金流的配置价值展开，补查近7日新进展，简明说明七只成员的差异。'
                    comparison='公开分红新闻，补搜系列产品资料' if number==1 else '同一新闻的利率线索，补搜利率与配置新材料'
                for mid in ids:
                    frozen=next((deepcopy(c) for c in old['selected_clues'] if c['event_id']==mid),None) if code!='dividend_family' else None
                    add_candidate(conn,config,eid,mid,snapshot=frozen)
                current=ed.latest_decision(conn,eid)
                if not current:
                    ed.save_decision(conn,edition_id=eid,selected_event_id=main,selected_fact_ids=[],selected_clue_ids=ids,manual_notes=[],must_include_ids=[],expected_revision=0,actor_type='human',config=config,mainline_note=note)
                cases.append(dict(id=code+'-'+str(number),code=code,edition_id=eid,selection_purpose='isolated_test_selection_not_editorial_approval',comparison=comparison,baseline_edition=editions.get(code)))
        save(OUT/'cases.json',cases)
    return cases

def run(case,attempt=1):
    target=OUT/(case['id']+('' if attempt==1 else '-attempt'+str(attempt))+'.json')
    if target.exists():return {'case':case['id'],'status':'already_recorded'}
    with closing(db.connect(OUT/'live.db',mode='live')) as conn:
        cfg=configured_view(conn,load_config());cfg['_reader_profile']=profile(conn,'default')
        cfg['_host_runtime']={'model':'gpt-6-sol','reasoning_effort':'high'}
        d=ed.latest_decision(conn,case['edition_id']);control=TaskControl(900)
        task=w.accept_run(conn,cfg,'generate',dict(edition_id=case['edition_id'],decision_revision=d['decision_revision'],request_id=uuid.uuid4().hex,reader_profile='default'),control)
        result=dict(case,attempt=attempt,run_id=task['run_id'],selection_hash=ed._hash(d),
            code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        progress({'case':case['id'],'run_id':task['run_id'],'status':'started'})
        try:result.update(version=w.execute_run(conn,task['run_id'],control,progress=progress),status='returned')
        except Exception as exc:result.update(status=getattr(exc,'code',None) or 'failed',error=str(exc))
        row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(task['run_id'],)).fetchone()
        result.update(record=w.run_record(row),run_input=json.loads(row['input_json']))
        if row['version_id']:
            v=w.public_version(w.version(conn,row['version_id']));result['version']=v
            target.with_suffix('.md').write_text(render(v['content']),encoding='utf-8');target.with_suffix('.docx').write_bytes(word(v['content']))
            working=v['content'].get('pipeline',{}).get('working_version_id')
            if working:
                result['working_version']=w.public_version(w.version(conn,working))
                target.with_name(target.stem+'-起草稿.md').write_text(render(result['working_version']['content']),encoding='utf-8')
        result['selection_unchanged']=ed._hash(ed.latest_decision(conn,case['edition_id']))==result['selection_hash']
        save(target,result)
        return dict(case=case['id'],status=result['status'],error=result.get('error'),checks=result.get('version',{}).get('checks'),elapsed=result['record'].get('usage',{}).get('total_elapsed_seconds'))


def enrich_hk_reference_case():
    """The old HK selection has one clue; recover its already-read public
    supplements for a genuinely material-rich case, without touching baseline.
    The reduced case keeps only the original main event. Never load holdout data.
    """
    edition_id='direct_test_513130_1';marker=OUT/'hk-reference-materials.json'
    if marker.exists():return
    baseline=ROOT/'data/writing_skill_redesign_20260927/513130-newsroom-r2.json'
    raw=json.loads(baseline.read_text(encoding='utf-8'))['run_input']['snapshot']
    materials={m['id']:m for m in raw['materials']};added=[]
    with closing(db.connect(OUT/'live.db',mode='live')) as conn:
        cfg=load_config();current=ed.latest_decision(conn,edition_id)
        for source in raw['sources']:
            if materials[source['material_id']]['origin']=='product_reference' or not source.get('text_observed') or not source.get('date_observed') or not source.get('url'):continue
            if source['material_id']==current['selected_event_id']:continue
            item=ed.import_evidence_payload(conn,dict(is_fixture=False,title=source['title'],content=source['text'],source_type='original_publication',source_url=source['url'],declared_source=source['name'],first_published_at=source['published_at'],origin_role='reported',facts=[],event=dict(subject=source['title'],action='报道',object='数字贸易与AI应用',phase='announcement')),edition_id=edition_id,privacy_class='public',actual_source_path='user:authorized-isolated-reference-materials')
            add_candidate(conn,cfg,edition_id,item['event_id']);added.append(dict(event_id=item['event_id'],url=source['url'],title=source['title']))
        ids=list(dict.fromkeys(current['selected_clue_ids']+[s['event_id'] for s in added]))
        ed.save_decision(conn,edition_id=edition_id,selected_event_id=current['selected_event_id'],selected_fact_ids=current['selected_fact_ids'],selected_clue_ids=ids,manual_notes=current['manual_notes'],must_include_ids=current['must_include_ids'],expected_revision=current['decision_revision'],actor_type='human',config=cfg,mainline_note=current['mainline_note'])
    save(marker,dict(purpose='isolated_test_selection_not_editorial_approval',baseline=str(baseline.relative_to(ROOT)),baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),added=added,initial_revision=current['decision_revision'],selected_count=len(ids)))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');parser.add_argument('--case');parser.add_argument('--attempt',type=int,default=1);parser.add_argument('--enrich-reference-materials',action='store_true');args=parser.parse_args()
    if args.attempt<1:parser.error('attempt must be positive')
    cases=prepare();progress({'cases':cases})
    if args.enrich_reference_materials:enrich_hk_reference_case()
    if args.run:
        for case in cases:
            if not args.case or args.case==case['id']:progress(run(case,args.attempt))
