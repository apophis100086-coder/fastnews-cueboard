"""Frozen 15-case editorial acceptance; unreviewed work never counts as adopted.

Only a human editor supplies reviews. This tool does not run models, choose
news, write approval records into the business database or publish anything.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
import sqlite3
from pathlib import Path

PRODUCTS={'513130':'恒生科技','588710':'科创半导体设备','dividend_family':'红利全家桶'}
SCENARIOS=['丰富选材','仅主事件','资金主线','政策或行业新阶段','近期背景与新消息衔接']
CRITERIA=['mainline_retained','structure_retained','product_link_retained','main_text_retained',
          'data_and_minor_wording_only','no_substantive_errors']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def init(directory):
    directory=Path(directory)
    if (directory/'batch.json').exists():raise ValueError('验收批次已存在，不覆盖旧结果')
    cases=[dict(case_id=f'{code}-{i+1:02d}',product_code=code,scenario=scenario,status='awaiting_materials',
                artifact_hash=None,not_used_for_tuning=None,review=None)
           for code in PRODUCTS for i,scenario in enumerate(SCENARIOS)]
    value=dict(version='quality-acceptance-20261002-v1',created_at=datetime.now(timezone.utc).isoformat(),
        fixed_company_copy_excluded=True,threshold=dict(total=12,per_product=4),cases=cases)
    write(directory/'batch.json',value)
    instructions='''# 15篇人工质量验收

三个产品各五篇。当前仅建立案例槽位，未运行、未评审不计入采用率。
使用没有用于调教的新题，保留真实失败，不删除后只算重跑。
在独立验收库冻结截点、来源和测试选材，不更改正式库人工选择。
每个案例运行新版流程后，将完整 writing.version 记录另存 JSON，再使用 register 登记。
认可样例只参与风格输入，评审答案单独保管，不能放入看板检索或写作输入。

人工评审新闻正文的主线、结构、产品承接和主要文字；固定公司模板不计入评分。
只需更新数据和少量措辞、没有实质错误时，各条件才填 true。未评审保留空值。
编辑用时另填 actual_edit_minutes；reviewer 填实际评审人，不由模型代填。
只有15篇全部评审、每类至少4篇通过、总体至少12篇通过，批次才通过。
register 用 --not-used-for-tuning 记录已由操作人检查过题目独立性；不能把旧试稿冒充新题。
'''
    (directory/'填写说明.md').write_text(instructions,encoding='utf-8')
    write(directory/'review-template.json',dict(reviewer=None,actual_edit_minutes=None,
        criteria={key:None for key in CRITERIA},notes=''))
    return summary(directory)


def register(directory,case_id,artifact,not_used_for_tuning=False):
    directory=Path(directory);batch=read(directory/'batch.json')
    case=next((c for c in batch['cases'] if c['case_id']==case_id),None)
    if not case or case['artifact_hash']:raise ValueError('案例不存在或已冻结；保留旧结果，不能覆盖')
    if not not_used_for_tuning:raise ValueError('需要检查本题未用于调教后再登记')
    artifact=Path(artifact);raw=artifact.read_bytes();value=json.loads(raw)
    snapshot=value.get('snapshot',{});content=value.get('content',{})
    if snapshot.get('product_code')!=case['product_code'] or snapshot.get('workflow')!='direct-newsroom-v4-agent':
        raise ValueError('案例产品或写作流程不匹配')
    failed=value.get('status') in {'failed','cancelled','timed_out','interrupted'}
    identifier=value.get('version_id') or value.get('run_id')
    if any(c.get('version_id')==identifier for c in batch['cases'] if c['artifact_hash']):
        raise ValueError('同一稿件或失败任务不能重复计为多个案例')
    if not snapshot.get('news_cutoff') or not identifier or (not content.get('paragraphs') and not failed):
        raise ValueError('案例缺少冻结截点、版本或明确的失败记录')
    case.update(artifact_hash=hashlib.sha256(raw).hexdigest(),not_used_for_tuning=True,status='awaiting_human_review',
        version_id=identifier,news_cutoff=snapshot['news_cutoff'],
        program_status=value.get('checks',{}).get('status'))
    (directory/'artifacts').mkdir(exist_ok=True)
    (directory/'artifacts'/f'{case_id}.json').write_bytes(raw)
    write(directory/'batch.json',batch)
    return case


def review(directory,case_id,review_file):
    directory=Path(directory);batch=read(directory/'batch.json')
    case=next((c for c in batch['cases'] if c['case_id']==case_id),None)
    if not case or not case['artifact_hash'] or case['review'] is not None:
        raise ValueError('案例尚未冻结或已经评审；不能覆盖原记录')
    if hashlib.sha256((directory/'artifacts'/f'{case_id}.json').read_bytes()).hexdigest()!=case['artifact_hash']:
        raise ValueError('冻结稿件已变化，拒绝记录评审')
    value=read(review_file);criteria=value.get('criteria',{})
    if set(criteria)!=set(CRITERIA) or any(type(v) is not bool for v in criteria.values()):
        raise ValueError('全部六项人工评审条件需明确填写true/false')
    minutes=value.get('actual_edit_minutes');reviewer=value.get('reviewer')
    if type(minutes) not in {int,float} or not math.isfinite(minutes) or not 0<=minutes<=1440 or not isinstance(reviewer,str) or not reviewer.strip():
        raise ValueError('需要实际编辑时间及真实评审人')
    case['review']=dict(value,reviewed_at=datetime.now(timezone.utc).isoformat())
    case['status']='passed' if all(criteria.values()) and case['program_status']=='ready' else 'needs_changes'
    write(directory/'reviews'/f'{case_id}.json',case['review']);write(directory/'batch.json',batch)
    return summary(directory)


def summary(directory):
    batch=read(Path(directory)/'batch.json');cases=batch['cases']
    groups={}
    for code,name in PRODUCTS.items():
        rows=[c for c in cases if c['product_code']==code]
        reviewed=sum(c['review'] is not None for c in rows);passed=sum(c['status']=='passed' for c in rows)
        groups[code]=dict(name=name,total=len(rows),registered=sum(bool(c['artifact_hash']) for c in rows),
            reviewed=reviewed,passed=passed,adoption_rate=passed/len(rows) if reviewed==len(rows) else None)
    reviewed=sum(g['reviewed'] for g in groups.values());passed=sum(g['passed'] for g in groups.values())
    complete=reviewed==15
    return dict(status='passed' if complete and passed>=12 and all(g['passed']>=4 for g in groups.values()) else
        'needs_changes' if complete else 'awaiting_materials_or_human_review',
        total=15,reviewed=reviewed,passed=passed,adoption_rate=passed/15 if complete else None,products=groups)


def capture(database,version_id,output):
    if Path(output).exists():raise ValueError('保留既有稿件快照，请使用新的输出文件')
    database=Path(database).resolve()
    conn=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        row=conn.execute('SELECT * FROM fn_writing_versions WHERE version_id=?',(version_id,)).fetchone()
        if row is None:raise ValueError('稿件版本不存在')
        result=dict(version_id=row['version_id'],edition_id=row['edition_id'],decision_revision=row['decision_revision'],
            snapshot=json.loads(row['snapshot_json']),content=json.loads(row['content_json']),checks=json.loads(row['checks_json']))
        current=conn.execute('SELECT MAX(revision) FROM fn_decisions WHERE edition_id=?',(row['edition_id'],)).fetchone()[0]
        if current!=row['decision_revision']:raise ValueError('人工选材已变化，不能登记旧稿为当前验收结果')
        write(output,result)
        return {'version_id':version_id,'output':str(output)}
    finally:conn.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['init','register','review','summary','capture'])
    p.add_argument('directory',type=Path);p.add_argument('--case');p.add_argument('--artifact',type=Path)
    p.add_argument('--review-file',type=Path);p.add_argument('--not-used-for-tuning',action='store_true')
    p.add_argument('--database',type=Path);p.add_argument('--version');args=p.parse_args()
    # capture's directory argument is the output JSON; the DB and version are explicit.
    if args.command=='init':result=init(args.directory)
    elif args.command=='summary':result=summary(args.directory)
    elif args.command=='register':result=register(args.directory,args.case,args.artifact,args.not_used_for_tuning)
    elif args.command=='review':result=review(args.directory,args.case,args.review_file)
    else:
        if not args.database or not args.version:p.error('capture 需要 --database 和 --version')
        result=capture(args.database,args.version,args.directory)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
