"""Build local company-copy versions from authorized samples and public notes.

No private sample text is embedded in tracked code. Original files remain intact.
"""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from fastnews.writing_templates import digest


def build(samples,public):
    def sample(code,day):return next(s for s in samples['items'] if s['product_code']==code and s['date']==day)
    result=[]
    for code,day in [('513130','2026-09-14'),('588710','2026-09-09'),('dividend_family','2026-09-22')]:
        item=sample(code,day);paragraphs=item['text'].split('\n\n');blocks=[];edits=[]
        def add(kind,text,source=None,**extra):
            ref=source or dict(name='华泰柏瑞基金提供资料',title=item['title'],published_at=day,
                url=None,sample_id=item['id'],sha256=item['sha256'],data_period='')
            blocks.append(dict(kind=kind,text=text,sources=[ref],required=True,
                display_sources=kind in {'product_intro','manager_intro'},**extra))
        if code=='513130':
            add('product_intro',paragraphs[11])
            add('manager_intro',paragraphs[14].split('截至2026年6月末')[0])
            note=paragraphs[16];split=note.index('投资者在申购')
            add('product_note',note[:split]);add('fee_note','费率备注：'+note[split:])
            add('risk',paragraphs[17])
            edits=['持有人和管理人成绩作为待独立证据支持的可选句，未默认插入；原始全文保留。','备注按原句边界分为风险等级和费率两块，文字未改写。']
        elif code=='dividend_family':
            add('manager_intro',paragraphs[11].split('据了解，',1)[1])
            text=re.sub(r'，截至2026年中持有人户数42.29万户','',paragraphs[13])
            text=text.replace('其联接基金持有人户数148.38万户','叠加低波动因子')
            add('product_intro',text)
            add('product_note',paragraphs[15])
            add('fee_note',paragraphs[16].replace('不超过0.50%其中','不超过0.50%的标准收取佣金，其中'))
            add('risk',paragraphs[17])
            edits=['户数可选句未默认插入，保留产品定位。','修复原样例“0.50%其中”漏字，补回“的标准收取佣金，”；不改变费率。']
        else:
            text=public.get('text','')
            if '2026/09/22 11:46' not in text:raise ValueError('公开对照缺少可见发布日期')
            ref=dict(name='界面新闻·有连云',title=public['title'],url=public['url'],published_at='2026-09-22T11:46:00+08:00',
                sha256=digest(text),data_period='')
            add('product_intro','科创半导体设备ETF华泰柏瑞（588710）跟踪上证科创板半导体材料设备主题指数，聚焦上游“材料+设备”，有望受益于存储扩产、晶圆厂资本开支及国产替代趋势。场外投资者可关注其联接基金（A类024974/C类024975）。',ref)
            add('manager_intro',paragraphs[8].split('截至2026年6月末')[0])
            note=next(p for p in text.splitlines() if p.startswith('备注：科创半导体设备ETF华泰柏瑞及其联接基金'))
            split=note.index('在申购或赎回')
            add('product_note',note[:split],ref);add('fee_note','费率备注：'+note[split:],ref)
            risk=next(p for p in text.splitlines() if p.startswith('风险提示：'))
            add('risk',risk.split('未经正式授权')[0],ref)
            edits=['本地样例缺少完整风险尾部，使用同产品公开已发稿对照的产品与费率备注。','产品介绍合并稳定定位，未沿用权重、近期涨幅或管理人成绩。']
        record=dict(id='company_'+code,version='company-copy-20260929-v1',product_code=code,
            effective_from='2026-09-22T11:46:00+08:00' if code=='588710' else day,
            effective_to=None,provenance='user_authorized_company_samples',blocks=blocks,
            preparation_changes=edits,optional_dynamic_fields=['holder_count','manager_profit','ranking','index_weights'],
            validation_basis='用户指定样例及已发稿；原始日期保留，产品变化继续匹配本期官方资料')
        record['sha256']=digest(record);result.append(record)
    return dict(version='company-copy-library-v1',items=result)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--samples',type=Path,default=ROOT/'data/writing_samples/library.json')
    p.add_argument('--public-reference',type=Path,default=ROOT/'data/writing_templates/semiconductor-public-reference.json')
    p.add_argument('--output',type=Path,default=ROOT/'data/writing_templates/library.json');args=p.parse_args()
    result=build(json.loads(args.samples.read_text(encoding='utf-8')),json.loads(args.public_reference.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():raise SystemExit('已有模板库未覆盖；新版本请使用新的输出文件后明确切换配置。')
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    manifest={t['product_code']:{k:t[k] for k in ('id','version','effective_from','sha256')} for t in result['items']}
    print(json.dumps(manifest,ensure_ascii=True,indent=2))
