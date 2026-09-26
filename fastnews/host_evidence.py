"""Trace-aware research material and conservative identity; never creates Facts."""
from copy import deepcopy
import hashlib
import json
import re

from .budget import utc_now
from .metadata import normalize_url, time_bounds
from .source_access import url_policy

KINDS = {'search_snippet','page_fragment','public_restatement','unavailable'}
PHASES = {'unknown','announcement','plan','implementation','revision','cancellation','result'}
SOURCE_EXTRA = {'source_id','material_kind','tool_ref','original_url','first_published_at',
                'occurred_at','effective_at','data_period_start','data_period_end'}
CLAIM_FIELDS = {'statement','kind','source_ref','quote'}
IDENTITY_FIELDS = {'subject','action','object','phase','event_date'}
MATERIAL_LABELS = {'listing_metadata':'程序列表摘要', 'prior_ai_reading_hint':'既有研究摘要',
    'host_search_hint':'搜索摘要 / 列表线索', 'host_opened_url':'宿主尝试访问，内容待复核',
    'host_report_pending':'宿主报告待复核', 'host_tool_fragment':'宿主工具可核对片段',
    'public_restatement':'公开转述，保留实际读取路径', 'not_obtained':'未取得内容',
    'program_fragment':'程序取得的正文片段'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def normalized(text):
    return re.sub(r'[\W_]+','',text.casefold())


def source_fingerprint(source):
    return fingerprint([source.get('url'),source.get('excerpt'),source.get('material_identity') or source.get('evidence_level'),
        source.get('published_at'),source.get('data_period_start'),source.get('data_period_end')])


def enrich_source(source, raw, payload, trace):
    extras = set(raw) - {'id','title','url','published_at','excerpt'}
    if extras and extras != SOURCE_EXTRA:
        raise ValueError('定点来源的材料身份与时间字段不完整')
    policy = url_policy(source['url'],payload.get('source_constraints',[]),raw.get('source_id'))
    if not policy['search_enabled']:
        return None
    requested = raw.get('material_kind','search_snippet')
    if not isinstance(requested,str) or requested not in KINDS:
        raise ValueError('未知的来源材料身份')
    if raw.get('source_id') is not None and not isinstance(raw['source_id'],str):
        raise ValueError('来源ID必须是字符串或null')
    for field in ('first_published_at','occurred_at','effective_at','data_period_start','data_period_end'):
        time_bounds(raw.get(field))
    period_start, _ = time_bounds(raw.get('data_period_start'))
    _, period_end = time_bounds(raw.get('data_period_end'))
    if period_start is not None and period_end is not None and period_start > period_end:
        raise ValueError('数据统计期起止顺序无效')
    origin = normalize_url(raw['original_url']) if raw.get('original_url') else None
    ref = raw.get('tool_ref')
    if ref is not None and (not isinstance(ref,str) or len(ref)>160):
        raise ValueError('工具引用格式无效')
    actions = [a for a in trace.get('actions',[]) if source['url'] in a.get('urls',[]) and
               a['type'] in {'open_page','open','find_in_page','find'}]
    # Only actual tool-result excerpts supplied by the adapter can establish
    # text support. An open action or a model's own quote is not that result.
    matched = [e for e in trace.get('tool_excerpts',[]) if e.get('url')==source['url'] and
               source['excerpt'] and source['excerpt'] in e.get('text','') and
               e.get('kind')=='page_fragment' and e.get('tool_ref')]
    missing = []
    if requested == 'unavailable':
        material = 'not_obtained'
        source['excerpt'] = ''
        missing.append('未取得来源内容')
    elif requested in {'page_fragment','public_restatement'} and not policy['host_read_allowed']:
        # Do not launder a prohibited body into an alleged search excerpt.
        material = 'not_obtained'
        source['excerpt'] = ''
        missing.append('此来源正文路径停止；需公开搜索摘要或获准原始出处')
    elif requested in {'page_fragment','public_restatement'}:
        material = ('public_restatement' if requested=='public_restatement' else 'host_tool_fragment') if matched else 'host_report_pending'
        if not matched:
            missing.append('宿主报告了片段，但CLI日志未提供可独立比对的页面文本')
    else:
        material = 'host_search_hint'
        missing.append('只取得搜索摘要；尚无原文片段')
    if source.get('published_at') is None:
        missing.append('报道日期待核实')
    missing.append('当前读取不证明历史截点前已存此页面版本')
    source.update({k:raw.get(k) for k in SOURCE_EXTRA})
    source.update(original_url=origin,material_identity=material,material_label=MATERIAL_LABELS[material],
        reported_material_kind=requested, retrieved_at=utc_now(),
        tool_ref=matched[0]['tool_ref'] if matched else ref,
        tool_ref_basis='adapter_tool_result' if matched else 'host_reported_not_verified',
        observed_action_ids=[a['action_id'] for a in actions],
        source_excerpt_verified=bool(matched) and material in {'host_tool_fragment','public_restatement'},
        fulltext_read=False, fact_verified=False, missing_items=missing,
        access_policy=policy)
    source['material_fingerprint'] = source_fingerprint(source)
    return source


def enrich_item(item, raw, payload):
    claims = raw.get('claims',[])
    if not isinstance(claims,list) or len(claims)>3:
        raise ValueError('每条线索最多三项待核对陈述')
    cited = {s['id']:s for s in item['sources']}
    clean = []
    for claim in claims:
        if (not isinstance(claim,dict) or set(claim)!=CLAIM_FIELDS or
            any(not isinstance(v,str) for v in claim.values()) or not claim['statement'].strip() or
            len(claim['statement'])>600 or len(claim['quote'])>1200 or
            claim['kind'] not in {'observed','company_statement','reported','estimate','plan','interpretation'}):
            raise ValueError('陈述与引用格式无效')
        source = cited.get(claim['source_ref'])
        if source is None:
            raise ValueError('陈述引用不属于此事件')
        quoted = bool(claim['quote']) and claim['quote'] in (source.get('excerpt') or '')
        supported = quoted and source.get('source_excerpt_verified') is True
        state = 'excerpt_located_statement_pending' if supported else 'host_report_pending'
        if not quoted:
            state = 'unsupported_quote'
        record = dict(claim, quote=claim['quote'] if quoted else '', source_url=source['url'],
            material_identity=source.get('material_identity',source.get('evidence_level')),
            support_state=state, fact_verified=False,
            evidence_fingerprint=source.get('material_fingerprint') or source_fingerprint(source),
            missing_items=[] if supported else ['原文与陈述对应关系待复核'])
        record['claim_fingerprint'] = fingerprint([normalized(record['statement']),record['kind'],
            source.get('data_period_start'),source.get('data_period_end')])
        clean.append(record)
    identity = raw.get('event_identity')
    if identity is not None:
        if (not isinstance(identity,dict) or set(identity)!=IDENTITY_FIELDS or
            any(not isinstance(identity[k],str) or len(identity[k])>150 for k in ('subject','action','object','phase')) or
            identity['phase'] not in PHASES):
            raise ValueError('事件身份字段无效')
        time_bounds(identity['event_date'])
    complete = identity and identity['event_date'] and all(identity[k].strip() for k in ('subject','action','object'))
    if complete:
        key = fingerprint(['host_event_identity', *[normalized(identity[k]) for k in ('subject','action','object')],
                           identity['phase'],identity['event_date'],
                           sorted({(s.get('data_period_start') or '',s.get('data_period_end') or '')
                                   for s in item['sources'] if s.get('data_period_start') or s.get('data_period_end')})])
    else:
        key = fingerprint(['cited_title',normalized(item['title']),
            sorted({(s['url'],s.get('published_at') or '') for s in item['sources']})])
    item.update(claims=clean, event_identity=identity, research_identity=key,
        missing_items=list(dict.fromkeys(g for s in item['sources'] for g in s.get('missing_items',[]))),
        material_fingerprints=[s.get('material_fingerprint') or source_fingerprint(s) for s in item['sources']])
    return item


def merge_materials(original, newer):
    """Append new versions/evidence; keep the original title, summary and choices."""
    result = deepcopy(original)
    sources = {source_fingerprint(s):s for s in result.get('sources',[])}
    sources.update({source_fingerprint(s):s for s in newer.get('sources',[]) if source_fingerprint(s) not in sources})
    result['sources'] = list(sources.values())
    claims = {fingerprint([c.get('claim_fingerprint'),c.get('evidence_fingerprint')]):c for c in (result.get('claims') or [])}
    for claim in newer.get('claims') or []:
        claims.setdefault(fingerprint([claim.get('claim_fingerprint'),claim.get('evidence_fingerprint')]),claim)
    result['claims'] = list(claims.values())
    result['missing_items'] = list(dict.fromkeys((result.get('missing_items') or []) + (newer.get('missing_items') or [])))
    result['material_fingerprints'] = list(sources)
    if newer.get('product_relationship'):
        old_relation=result.get('product_relationship') or {}
        new_relation=newer['product_relationship']
        references={r['id']:deepcopy(r) for r in old_relation.get('references',[])}
        for ref in new_relation.get('references',[]):
            references.setdefault(ref['id'],deepcopy(ref))
        relationship_claims={fingerprint(c):deepcopy(c) for c in old_relation.get('claims',[])}
        for claim in new_relation.get('claims',[]):
            relationship_claims.setdefault(fingerprint(claim),deepcopy(claim))
        result['product_relationship']=dict(purpose='product_scope_not_news_evidence',
            references=list(references.values()),claims=list(relationship_claims.values()))
    return result


def merge_report_events(items):
    grouped = {}
    for item in items:
        key = item['research_identity']
        if key not in grouped:
            grouped[key] = item
        else:
            grouped[key] = merge_materials(grouped[key],item)
            grouped[key]['event_ids'] = list(dict.fromkeys(grouped[key]['event_ids'] + item['event_ids']))
    return [dict(item,item_id='item_'+str(i),rank=i) for i,item in enumerate(grouped.values(),1)]
