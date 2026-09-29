"""Versioned, local company copy. Models never author or rewrite these blocks."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
from .writing_checks import within_cutoff

ROOT=Path(__file__).resolve().parents[1]
LIBRARY=ROOT/'data/writing_templates/library.json'
BODY_KINDS={'product_intro','manager_intro'}
KINDS=BODY_KINDS|{'product_note','fee_note','risk'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def select_template(code,cutoff,*,library=LIBRARY):
    if not Path(library).exists():return dict(status='missing',product_code=code,blocks=[],issues=['公司模板尚未导入'])
    records=json.loads(Path(library).read_text(encoding='utf-8'))['items']
    candidates=[t for t in records if t['product_code']==code and within_cutoff(t['effective_from'],cutoff)
        and (not t.get('effective_to') or cutoff[:10]<t['effective_to'])]
    if not candidates:return dict(status='missing',product_code=code,blocks=[],issues=['截点内没有有效公司模板'])
    template=deepcopy(max(candidates,key=lambda t:(t['effective_from'],t['version'])))
    if template.get('sha256')!=digest({k:v for k,v in template.items() if k!='sha256'}):
        return dict(status='invalid',product_code=code,blocks=[],issues=['公司模板内容与版本哈希不符'])
    issues=[];blocks=[]
    for block in template['blocks']:
        if block['kind'] not in KINDS:raise ValueError('未知模板块类型')
        if not block.get('text') or not block.get('sources'):
            if block.get('required',True):issues.append(block['kind']+' 模板或出处缺失')
            continue
        if any(not within_cutoff(s.get('published_at'),cutoff) for s in block['sources']):
            if block.get('required',True):issues.append(block['kind']+' 模板出处超过截点')
            continue
        values=block.get('variables',{})
        if any(not v.get('source') or not v.get('as_of') or not within_cutoff(v['as_of'],cutoff)
               or (v.get('valid_until') and cutoff[:10]>v['valid_until']) for v in values.values()):
            if block.get('required',False):issues.append(block['kind']+' 动态字段依据待补齐')
            continue
        blocks.append(block)
    template.update(blocks=blocks,issues=issues,status='pending' if issues else 'ready')
    return template


def attach(content,snapshot,checks=None):
    """Assemble once from the frozen template, never from model output."""
    template=deepcopy(snapshot.get('company_template') or {'status':'missing','blocks':[],'issues':['公司模板尚未导入']})
    issues=list(template.get('issues',[]))
    if template.get('status') not in {'ready','pending'}:template['blocks']=[]
    updates=snapshot.get('product_definition',{}).get('effective_updates',[])
    for block in template['blocks']:
        for update in updates:
            if any(n and n in block['text'] for n in (update.get('previous_name'),update.get('previous_index_name'))):
                issues.append('模板含已变更产品或指数名称：'+str(update.get('subject_code','')))
    if template.get('product_code')=='dividend_family':
        text=''.join(b['text'] for b in template['blocks'] if b['kind']=='product_intro')
        missing=[c for c in snapshot.get('product_definition',{}).get('member_codes',[]) if c not in text]
        if missing:issues.append('系列模板缺少成员：'+'、'.join(missing))
    content['template_blocks']=template['blocks']
    content['company_template']={k:template.get(k) for k in ('id','version','sha256','effective_from','product_code')}
    content['company_template'].update(status='pending' if issues else template.get('status'),issues=issues)
    content['appendix']=[];content['risk_notice']={'text':'','source':'company_template'}
    content['body_characters']=sum(len(p['text']) for p in content.get('paragraphs',[]))+sum(len(b['text']) for b in template['blocks'] if b['kind'] in BODY_KINDS)
    if checks is not None:
        checks['template']=deepcopy(content['company_template'])
        checks['editorial']['notes'].extend(issues)
        if issues and checks['status']=='ready':checks['status']='template_pending'
    return content


def writer_brief(template,target=1100):
    blocks=[{'kind':b['kind'],'text':b['text']} for b in template.get('blocks',[]) if b['kind'] in BODY_KINDS]
    used=sum(len(b['text']) for b in blocks)
    return dict(fixed_body_blocks=blocks,news_body_target=max(350,target-used),
        instruction='下面的公司标准介绍由程序在新闻正文之后插入，供你安排承接；不要复写。备注和风险提示由程序统一处理。')
