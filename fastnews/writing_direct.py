"""Direct, researched compose/edit. Human selection is the authorization boundary."""
from copy import deepcopy
from pathlib import Path
from datetime import timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import time
import uuid
import yaml

from .metadata import normalize_url,parse_instant
from .writing_contracts import obj,arr,S
from .writing_checks import within_cutoff,date_present,issue

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/huatai-fastnews-writing'
FORMAT='direct-newsroom-v2'
PRODUCT_GUIDES={'513130':'hk_tech','588710':'semiconductor','dividend_family':'dividend_family'}
RESEARCH=obj(summary=S,gaps=arr(S),sources=arr(obj(material_id=S,title=S,publisher=S,url=S,
    published_at=S,publication_quote=S,excerpt=S,relation=S,subject_code=S)))


def policy():
    value=yaml.safe_load((ROOT/'config/writing.direct.yaml').read_text(encoding='utf-8'))
    if value['deadline_seconds']!=900 or sum(value[k] for k in ('preparation_seconds','compose_seconds','edit_seconds','finalize_seconds'))>900:
        raise ValueError('直接写作各阶段必须共用15分钟时限')
    if value['max_pages']>20 or value['pages_per_material']>4 or value['finalize_seconds']<30:
        raise ValueError('直接写作的原文容量或保存时间配置无效')
    return value


def instructions(stage,code):
    paths=[SKILL/'SKILL.md',SKILL/'guides/direct_common.md',SKILL/('guides/direct_'+stage+'.md')]
    if code in PRODUCT_GUIDES:paths.append(SKILL/('products/'+PRODUCT_GUIDES[code]+'.md'))
    return '\n\n'.join(p.read_text(encoding='utf-8') for p in paths)


def skill_fingerprint(code):
    from .writing import fingerprint
    return fingerprint([policy(),*[instructions(s,code) for s in ('research','compose','edit')]])


def accept_run(conn,config,data,control,run_id=None):
    from . import writing as w
    from .source_catalog import profile
    old=w.previous_request(conn,'generate',data)
    if old:return old
    settings=(config.get('_reader_profile') or profile(conn,data.get('reader_profile','default')))['settings']
    if settings.get('host','none')=='none':raise ValueError('请在设置中选择已登录的模型入口')
    snapshot=w._snapshot(conn,config,data['edition_id'],data['decision_revision'])
    rules=policy();code=snapshot['product_code']
    snapshot.update(workflow=FORMAT,rules=rules,skill_version=skill_fingerprint(code))
    snapshot['product_definition']=deepcopy(config['products'][code])
    add_product_sources(snapshot)
    feedback=data.get('feedback','')
    if not isinstance(feedback,str) or len(feedback)>4000:raise ValueError('修改意见最多4000字')
    w.ed._reject_secrets(Path('writing-feedback.txt'),feedback)
    parent=None
    if data.get('parent_version_id'):
        parent=w.version(conn,data['parent_version_id'])
        if parent['stage']!='draft' or parent['edition_id']!=data['edition_id']:
            raise ValueError('修改意见必须关联本期的一版初稿')
    from .writing_samples import select_examples
    focus=' '.join([snapshot.get('mainline_note') or '',*[m['title'] for m in snapshot['materials']]])
    snapshot['style_examples']=select_examples(code,focus,cutoff=snapshot['news_cutoff'])
    if not snapshot['style_examples']:
        from .product_context import writing_examples
        snapshot['style_examples']=writing_examples(conn,w.ed.get_edition(conn,data['edition_id']))
    # Reuse the exact previously read source only within the same material/URL;
    # the new task still checks dates and current permissions before writing.
    if parent:
        prior={(s['material_id'],s.get('url')):s for s in parent['snapshot']['sources']}
        for source in snapshot['sources']:
            previous=prior.get((source['material_id'],source.get('url')))
            if previous and previous.get('text_observed'):
                source.update({k:deepcopy(previous[k]) for k in ('text','text_segments','text_observed','date_observed','published_at','document_hash','text_truncated') if k in previous})
    content=dict(workflow=FORMAT,snapshot=snapshot,host=settings['host'],allow_verify=settings.get('web_search',True),
        supplement_latest=settings.get('web_search',True),feedback=feedback,
        parent_id=parent['version_id'] if parent else None,parent=None,
        previous_draft=({k:deepcopy(parent['content'].get(k)) for k in ('title','paragraphs')} if parent else None),
        instructions=instructions('compose',code),editing_instructions=instructions('edit',code),
        research_instructions=instructions('research',code),
        host_runtime=config.get('_host_runtime') or dict(model=rules['model'],reasoning_effort=rules['reasoning_effort']))
    content['implementation_hashes']={n:hashlib.sha256((ROOT/'fastnews'/n).read_bytes()).hexdigest()
        for n in ('writing_direct.py','writing_newsroom.py','writing_checks.py','writing.py','writing_samples.py','host_provider.py')}
    rid=run_id or 'writing_'+uuid.uuid4().hex
    control.check()
    with conn:
        conn.execute('''INSERT INTO fn_writing_runs(run_id,request_id,request_hash,edition_id,decision_revision,stage,
          profile_id,status,input_json,created_at,deadline_at) VALUES(?,?,?,?,?,'draft',?,'running',?,?,?)''',
          (rid,data['request_id'],w.request_signature('generate',data),data['edition_id'],data['decision_revision'],
           data.get('reader_profile','default'),w.dumps(content),control.started_at,control.deadline_at))
    return w.run_record(conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(rid,)).fetchone())


def add_product_sources(snapshot):
    from .product_cards import registry
    from .writing import fingerprint
    cards=registry();definition=snapshot['product_definition']
    members=definition.get('member_codes',[snapshot['product_code']])
    definition['members']=[dict(code=c,name=cards[c]['aliases'][0],legal_name=cards[c]['name']) for c in members if c in cards]
    snapshot['product_names']+= [n for c in members if c in cards for n in [cards[c]['name'],*cards[c]['aliases']]]
    snapshot['product_codes']=list(dict.fromkeys(snapshot['product_codes']+members))
    urls={s.get('url') for s in snapshot['sources']}
    for code in members:
        if code not in cards:continue
        card=cards[code]
        refs=[dict(url=card['source_url'],title=card['name']+' 产品公开资料',published_at=card['published_at']),*card.get('additional_sources',[])]
        for ref in refs:
            if ref['url'] in urls or (ref['published_at'] and not within_cutoff(ref['published_at'],snapshot['news_cutoff'])):continue
            mid='product_'+code
            if not any(m['id']==mid for m in snapshot['materials']):
                snapshot['materials'].append(dict(id=mid,title=card['name'],summary='公开产品检索入口，需读原文后引用',relation='产品定位与风险',origin='product_reference',must_include=False))
            snapshot['sources'].append(dict(id='product_src_'+fingerprint(ref['url'])[:16],material_id=mid,name='华泰柏瑞基金',title=ref['title'],url=ref['url'],published_at=ref['published_at'],subject_code=code,text='',research_hint='',text_observed=False,date_observed=False,identity='official_reference_entry',source_id=None,error=None))
            urls.add(ref['url'])


def research_input(snapshot):
    cutoff=parse_instant(snapshot['news_cutoff']).astimezone(ZoneInfo('Asia/Shanghai'))
    return dict(stage='research',news_cutoff=snapshot['news_cutoff'],window_start=(cutoff-timedelta(days=6)).date().isoformat(),
        product=snapshot.get('product_definition'),mainline=snapshot.get('mainline_note'),
        preferences=snapshot['preferences'],selected_materials=snapshot['materials'],
        existing_sources=[{k:s.get(k) for k in ('id','material_id','name','title','url','published_at','text_observed','date_observed','error','text')}
            for s in snapshot['sources']],source_constraints=snapshot['source_constraints'],
        max_pages=snapshot['rules']['max_pages'],pages_per_event=4,
        rule='所选资料及范文不得发送到搜索查询中；搜索词只用新闻公共主体、公开事件与产品名称。')


def stage_sources(raw,snapshot):
    """Tolerate redundant suggestions, never trust model text as observed evidence."""
    from . import writing as w
    from .source_access import url_policy
    w._validate_shape(raw,RESEARCH)
    materials={m['id']:m for m in snapshot['materials']}
    urls={normalize_url(s['url']):s for s in snapshot['sources'] if s.get('url')}
    skipped=[];added=[]
    for item in raw['sources']:
        try:url=normalize_url(item['url']);access=url_policy(url,snapshot['source_constraints'])
        except ValueError:
            skipped.append(dict(title=item['title'],reason='invalid_url'));continue
        if url in urls:
            urls[url]['publication_hint']=dict(date=item['published_at'],quote=item['publication_quote'])
            skipped.append(dict(title=item['title'],reason='existing_source_reused'));continue
        if len(added)>=snapshot['rules']['max_pages']:
            skipped.append(dict(title=item['title'],reason='source_capacity'));continue
        mid=item['material_id']
        if mid not in materials:
            if not re.fullmatch(r'(?:news|product)_[A-Za-z0-9_-]{1,60}',mid):
                mid='news_'+w.fingerprint(url)[:16]
            kind='product_reference' if mid.startswith('product_') else 'direct_supplement'
            material=dict(id=mid,title=item['title'],summary=item['excerpt'],relation=item['relation'],origin=kind,must_include=False)
            snapshot['materials'].append(material);materials[mid]=material
        sid='direct_src_'+w.fingerprint(url)[:20]
        source=dict(id=sid,material_id=mid,title=item['title'],name=item['publisher'],url=url,
            published_at=item['published_at'],publication_hint=dict(date=item['published_at'],quote=item['publication_quote']),
            subject_code=item['subject_code'],research_hint=item['excerpt'],text='',text_observed=False,date_observed=False,
            identity='search_discovery',source_id=None,error=None if access['host_read_allowed'] else 'source_restricted')
        snapshot['sources'].append(source);urls[url]=source;added.append(sid)
    snapshot['supplemental_research']=dict(enabled=True,status='pending_verification',summary=raw['summary'],
        gaps=raw['gaps'],source_ids=added,skipped=skipped,approval='authorized_direct_generation')
    return added


def bind_dates(snapshot):
    """A visible date needs an actual matching passage, not an AI declaration."""
    for source in snapshot['sources']:
        hint=source.get('publication_hint',{})
        date,quote=hint.get('date'),hint.get('quote','')
        if not source.get('date_observed') and source.get('text_observed') and quote and quote in source.get('text','') and date_present(date,quote):
            source.update(published_at=date,date_observed=True,date_basis='observed_publication_passage')


def prepare(snapshot,data,control,invoke,persist,progress,fetcher):
    from . import writing as w
    rules=snapshot['rules'];start=time.monotonic();budget={}
    if data['allow_verify']:
        w.verify_sources(snapshot,control,progress,fetcher,seconds=rules['initial_verify_seconds'],
            budget_state=budget,preserve_context=True)
        persist()
        try:
            payload=research_input(snapshot)
            # Research sees coherent leads as discovery context; full fetched
            # documents remain untouched for compose/edit and audit snapshots.
            for source in payload['existing_sources']:
                text=source.get('text') or ''
                source['text']=text[:4000];source['research_preview_truncated']=len(text)>4000
            response=invoke('research',payload,data['research_instructions'],RESEARCH,
                min(rules['research_seconds'],rules['preparation_seconds']-(time.monotonic()-start)-90))
            stage_sources(response.payload,snapshot)
        except Exception as exc:
            if getattr(exc,'code',None)=='cancelled':raise
            snapshot['supplemental_research']=dict(enabled=True,status='failed',summary='补搜未完成，继续检查已取得材料是否足够成稿',
                source_ids=[],gaps=[str(exc)[:500] if isinstance(exc,ValueError) else type(exc).__name__],approval='authorized_direct_generation')
        persist();control.check()
        remaining=max(0,rules['preparation_seconds']-(time.monotonic()-start))
        w.verify_sources(snapshot,control,progress,fetcher,seconds=remaining,budget_state=budget,preserve_context=True)
    else:
        snapshot['supplemental_research']=dict(enabled=False,status='disabled',summary='本次方案关闭联网，使用已核对材料',source_ids=[])
    bind_dates(snapshot)
    research=snapshot['supplemental_research']
    research['verified_sources']=sum(bool(s.get('text_observed') and s.get('date_observed') and within_cutoff(s.get('published_at'),snapshot['news_cutoff']))
        for s in snapshot['sources'] if s['id'] in research.get('source_ids',[]))
    if research['status']=='pending_verification':research['status']='completed' if research['verified_sources']==len(research['source_ids']) else 'partial'
    research['verification_budget']=budget
    persist()


def build_input(snapshot,feedback='',previous=None):
    from .writing import dumps
    from .source_access import url_policy
    materials={m['id']:m for m in snapshot['materials']};news=[];product=[];omitted=[];used=[]
    required={m['id'] for m in materials.values() if m['must_include']}
    if snapshot.get('selected_event_id'):required.add(snapshot['selected_event_id'])
    for source in snapshot['sources']:
        reason=None;mid=source['material_id']
        if source.get('url') and not url_policy(source['url'],snapshot['source_constraints'],source.get('source_id'))['host_read_allowed']:reason='source_restricted'
        elif not source.get('text_observed') or not source.get('text'):reason='original_not_read'
        elif not source.get('date_observed'):reason='publication_not_verified'
        elif not within_cutoff(source.get('published_at'),snapshot['news_cutoff']):reason='outside_cutoff'
        if reason:
            omitted.append(dict(source_id=source['id'],material_id=mid,title=source['title'],reason=reason));continue
        entry={k:source.get(k) for k in ('name','title','url','published_at','data_as_of','text','subject_code')}
        entry.update(source_id=source['id'],material_id=mid,must_include=mid in required,text_truncated=bool(source.get('text_truncated')))
        (product if materials[mid]['origin']=='product_reference' else news).append(entry);used.append(source)
    missing=required-{s['material_id'] for s in used}
    if missing:raise ValueError('必写或主新闻仍缺原文或有效日期：'+'；'.join(materials[mid]['title'] for mid in sorted(missing)))
    if not news:raise ValueError('尚未取得可用于本期的新闻原文；已保存本轮补搜与来源失败原因')
    result=dict(format_version=FORMAT,news_cutoff=snapshot['news_cutoff'],product=snapshot.get('product_definition'),
        editorial_brief=dict(mainline_note=snapshot.get('mainline_note'),selected_event_id=snapshot.get('selected_event_id'),
            must_include=[dict(id=m,title=materials[m]['title']) for m in sorted(required)],preferences=snapshot['preferences']),
        news_sources=news,product_sources=product,style_examples=deepcopy(snapshot.get('style_examples',[])),
        feedback=feedback,previous_draft=previous,input_coverage=dict(omitted=omitted,style_omitted=[],
            truncated_sources=[s['source_id'] for s in news+product if s['text_truncated']]))
    capacity=snapshot['rules']['max_input_characters']-12000
    # Preserve main news and full examples; remove whole optional sources first.
    for entry in reversed(list(news+product)):
        if len(dumps(result))<=capacity:break
        if entry['must_include']:continue
        target=news if entry in news else product
        if len(target)==1:continue
        if target is product and entry.get('subject_code') and sum(s.get('subject_code')==entry['subject_code'] for s in product)==1:continue
        target.remove(entry);omitted.append(dict(source_id=entry['source_id'],material_id=entry['material_id'],title=entry['title'],reason='input_capacity_whole_source_omitted'))
    if len(dumps(result))>capacity:raise ValueError('主新闻与完整范文超过本次容量；未静默截断，原文和选材已保留')
    allowed={s['source_id'] for s in news+product}
    checked=deepcopy(snapshot);checked['sources']=[s for s in used if s['id'] in allowed]
    return result,checked


def material_scope(snapshot):
    # Compatibility adapter for the evidence validator only. Never saved as an
    # outline, never presented as human confirmation, never used to plan prose.
    return dict(paragraphs=[dict(material_ids=[m['id'] for m in snapshot['materials']])],claims=[],entities=[])


def validate_edited(raw,snapshot):
    from .writing_newsroom import validate_edited as validate
    from .writing import _validate_shape
    from .writing_contracts import DIRECT_EDIT
    _validate_shape(raw,DIRECT_EDIT)
    base={k:v for k,v in raw.items() if k!='appendix'}
    content,checks=validate(base,snapshot,material_scope(snapshot))
    content['raw_article']=deepcopy(raw)
    content['format_version']=FORMAT
    editorial=checks['editorial'];editorial['version']=FORMAT
    editorial['notes'].extend(e['message'] for e in editorial['errors'])
    nonblocking=editorial['errors'];checks['errors']=[e for e in checks['errors'] if e not in nonblocking]
    checks['status']='needs_changes' if checks['errors'] else 'ready'
    definition=snapshot.get('product_definition',{})
    if definition.get('kind')=='family':
        body='\n'.join(p['text'] for p in content['paragraphs'])
        missing=[c for c in definition['member_codes'] if c not in body]
        if missing:
            error=issue('family_members','正文未完整覆盖红利系列成员：'+'、'.join(missing))
            checks['errors'].append(error);checks['policy']['errors'].append(error);checks['status']='needs_changes'
    from .writing_delivery import attach_appendix,render
    attach_appendix(content,raw['appendix'],snapshot,checks)
    content['text']=render(content)
    return content,checks
