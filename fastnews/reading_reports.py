"""Cited AI reading reports. Suggestions never write Facts or human Decisions."""
from datetime import timedelta
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from .budget import utc_now
from .daily import daily_news
from .host_provider import run_host, HostUnavailable
from .metadata import normalize_url, parse_instant, time_bounds
from .pipeline import json_text
from .routing import route_product, working_now
from .source_catalog import CATEGORIES, profile, section_ids
from .reader_limits import reader_limits
from .host_evidence import (SOURCE_EXTRA, MATERIAL_LABELS, enrich_source, enrich_item, normalized,
    merge_report_events, merge_materials, source_fingerprint, fingerprint)
from .source_access import source_constraints, url_policy

VERSION = 'host-reading-v9-bounded-context'
VALIDATION_VERSION = 'reading-validation-v3-source-aliases'
STR = {'type':'string'}
def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def array(item):
    return {'type':'array','items':item}
REPORT_SCHEMA = obj({
    'items':array(obj({'title':STR,'summary':STR,'category':{'type':'string','enum':list(CATEGORIES)},
                       'why':STR,'source_refs':array(STR)})),
    'web_sources':array(obj({'id':STR,'title':STR,'url':STR,'published_at':{'type':['string','null']},
                             'excerpt':STR})),
    'coverage':array(obj({'source_id':STR,'status':{'type':'string','enum':['found','not_found','blocked','not_checked']},'note':STR})),
    'gaps':array(STR),
})
from copy import deepcopy
PRODUCT_REPORT_SCHEMA = deepcopy(REPORT_SCHEMA)
_nullable = {'type':['string','null']}
PRODUCT_REPORT_SCHEMA['properties']['web_sources']['items'] = obj(
    dict(REPORT_SCHEMA['properties']['web_sources']['items']['properties'],
         **{k:_nullable for k in SOURCE_EXTRA - {'material_kind'}},
         material_kind={'type':'string','enum':['search_snippet','page_fragment','public_restatement','unavailable']}))
PRODUCT_REPORT_SCHEMA['properties']['items']['items'] = obj(
    dict(REPORT_SCHEMA['properties']['items']['items']['properties'],
         event_identity=obj({'subject':STR,'action':STR,'object':STR,
             'phase':{'type':'string','enum':['unknown','announcement','plan','implementation','revision','cancellation','result']},
             'event_date':_nullable}),
         claims=array(obj({'statement':STR,'kind':{'type':'string','enum':['observed','company_statement','reported','estimate','plan','interpretation']},
                           'source_ref':STR,'quote':STR}))))
from . import product_reading
from . import morning_reading
PRODUCT_REPORT_SCHEMA = product_reading.extend_schema(PRODUCT_REPORT_SCHEMA)
MORNING_REPORT_SCHEMA = morning_reading.schema(REPORT_SCHEMA)
MORNING_REPORT_SCHEMA['properties']['web_sources']=deepcopy(PRODUCT_REPORT_SCHEMA['properties']['web_sources'])

# Compatibility name; the runtime and cache both use the versioned morning prompt.
INSTRUCTIONS = morning_reading.instructions()


def prior_morning_material(conn,profile_id,sources,code,config,start,cutoff,slots):
    """Reuse public morning research with its citations, never as verified facts."""
    from zoneinfo import ZoneInfo
    zone=ZoneInfo('Asia/Shanghai')
    first=parse_instant(start).astimezone(zone).date().isoformat()
    last=parse_instant(cutoff).astimezone(zone).date().isoformat()
    known={s['source_id'] for s in sources}
    domains={urlsplit(s['url']).hostname.removeprefix('www.') for s in sources}
    words=config.get('discovery',{}).get('products',{}).get(code,{}).get('core_keywords',[])
    candidates,days=[],set()
    for row in conn.execute("SELECT * FROM fn_reading_reports WHERE profile_id=? AND kind='morning' AND day>=? AND day<=? AND status IN ('complete','partial') ORDER BY day DESC,rowid DESC",(profile_id,first,last)):
        if row['day'] in days:
            continue
        days.add(row['day'])
        for item in json.loads(row['output_json'])['items']:
            accepted=[]
            for src in item['sources'][:4]:
                domain=urlsplit(src['url']).hostname.removeprefix('www.')
                lo,hi=time_bounds(src.get('published_at'))
                if not url_policy(src['url'],source_constraints(config['reader_sources']),src.get('source_id'))['search_enabled']:
                    continue
                if lo is not None and (lo>parse_instant(cutoff) or hi<parse_instant(start)):
                    continue
                accepted.append(dict(src,id=row['report_id']+'_'+item['item_id']+'_'+str(len(accepted)),
                    title=item['title'],excerpt='既有 AI 研究摘要（原文待核实）：'+item['summary'],
                    origin='saved_research_report',evidence_level='prior_ai_reading_hint',
                    prior_report_id=row['report_id'],event_id=None))
            if accepted:
                relevance=sum(word.casefold() in (item['title']+item['summary']).casefold() for word in words)
                candidates.append((relevance,accepted))
    candidates.sort(key=lambda c:c[0],reverse=True)
    return [source for _,group in candidates[:max(0,slots)] for source in group]


def _past_product_items(conn, edition_id, profile_id):
    items = {}
    for row in conn.execute("SELECT output_json FROM fn_reading_reports WHERE edition_id=? AND profile_id=? AND kind='product' AND status IN ('complete','partial') ORDER BY rowid DESC",(edition_id,profile_id)):
        for original in json.loads(row[0]).get('items',[]):
            item = dict(original)
            if not item.get('research_identity'):
                item['research_identity'] = fingerprint(['cited_title',normalized(item['title']),
                    sorted({(s['url'],s.get('published_at') or '') for s in item['sources']})])
            key = item['research_identity']
            items[key] = merge_materials(items[key],item) if key in items else item
    return items


def reusable_product_material(conn, edition_id, constraints, profile_id):
    known, material = [], {}
    for item in list(_past_product_items(conn,edition_id,profile_id).values())[:12]:
        sources = [s for s in item['sources'] if url_policy(s['url'],constraints,s.get('source_id'))['search_enabled']]
        if not sources:
            continue
        known.append({'research_identity':item['research_identity'],'title':item['title'],
            'summary':item['summary'][:250], 'event_identity':item.get('event_identity'),
            'source_urls':list(dict.fromkeys(s['url'] for s in sources))[:4]})
        for source in sources:
            if not source.get('excerpt') or source.get('material_identity') not in {'host_tool_fragment','host_report_pending','public_restatement'}:
                continue
            key = source_fingerprint(source)
            if len(material)<6:
                material.setdefault(key,dict(source,id='reuse_'+key[:24],origin='same_edition_reuse',
                    event_id=source.get('event_id'), evidence_level=source.get('material_identity'),
                    cache_note='同一期次已存片段，保留取得时间、指纹及原材料身份'))
    # Reuse only actually acquired public source excerpts; no imported private
    # drafts, manual notes, product holdings or whole database are sent.
    rows = conn.execute('''SELECT e.data_json,ee.event_id FROM fn_edition_evidence ee
        JOIN fn_evidence e USING(evidence_id) JOIN fn_documents d USING(document_id)
        WHERE ee.edition_id=? AND d.privacy_class='public' ORDER BY d.imported_at DESC''',(edition_id,))
    for row in rows:
        evidence = json.loads(row[0])
        url = evidence.get('source_url')
        if (len(material)>=6 or evidence.get('actual_access')!='public_http' or not url or
            not url_policy(url,constraints,evidence.get('source_id'))['search_enabled']):
            continue
        key = fingerprint(evidence['evidence_id'])
        material.setdefault(key,{'id':'program_'+key[:24],'title':evidence.get('actual_source') or '已存公开依据',
            'url':url,'excerpt':evidence['excerpt'],'source_name':evidence.get('actual_source'),
            'source_id':evidence.get('source_id'),'event_id':row[1],
            'published_at':evidence.get('first_published_at'),'retrieved_at':evidence.get('retrieved_at'),
            'origin':'same_edition_reuse','evidence_level':'program_fragment','material_identity':'program_fragment',
            'source_excerpt_verified':True,'tool_ref':None,'material_fingerprint':key})
    return known,list(material.values())


def compare_product_results(conn, edition_id, items, profile_id):
    prior = _past_product_items(conn,edition_id,profile_id)
    pools = {}
    for row in conn.execute('SELECT event_id,snapshot_json FROM fn_candidate_pool WHERE edition_id=?',(edition_id,)):
        snapshot = json.loads(row[1]) if row[1] else {}
        if snapshot.get('research_identity'):
            pools[snapshot['research_identity']] = row[0]
    counts = {'new_event_candidates':0,'existing_events_with_new_claims':0,'events_with_new_evidence':0,
              'new_source_urls':0,'duplicate_source_urls':0,'reused_fragments':0,
              'new_trace_supported_fragments':0,'new_host_report_pending_fragments':0}
    seen_urls = {s['url'] for old in prior.values() for s in old['sources']}
    for item in items:
        old = prior.get(item['research_identity'])
        if not old and not item.get('local_event_id'):
            counts['new_event_candidates'] += 1
        old_claims = {c.get('claim_fingerprint') for c in (old or {}).get('claims',[])}
        new_claims = {c.get('claim_fingerprint') for c in item.get('claims',[])} - old_claims
        if (old or item.get('local_event_id')) and new_claims:
            counts['existing_events_with_new_claims'] += 1
        old_material = {source_fingerprint(s) for s in (old or {}).get('sources',[])}
        supported = pending = 0
        for source in item['sources']:
            key = source_fingerprint(source)
            if source['url'] in seen_urls:
                counts['duplicate_source_urls'] += 1
            else:
                seen_urls.add(source['url']); counts['new_source_urls'] += 1
            if key in old_material:
                counts['reused_fragments'] += 1
            elif source.get('source_excerpt_verified'):
                supported += 1
            elif source.get('material_identity')=='host_report_pending':
                pending += 1
        counts['new_trace_supported_fragments'] += supported
        counts['new_host_report_pending_fragments'] += pending
        if old and supported:
            counts['events_with_new_evidence'] += 1
        item['pool_event_id'] = pools.get(item['research_identity'])
    counts['note'] = '新增陈述仍待核实；搜索摘要、重复URL及宿主自述不计为已补齐原文证据。'
    return counts


def build_input(conn, config, profile_id, kind, day, edition=None, extra_keywords=None, *, request_mode='baseline', research_question='', lookback_days=7, input_audit=None):
    if request_mode not in {'baseline','explore'} or not isinstance(research_question,str) or len(research_question)>500:
        raise ValueError('探查模式或补充需求无效（最多500字）')
    reader = config.get('_reader_profile') or profile(conn,profile_id)
    if kind not in {'morning','product'}:
        raise ValueError('未知整理任务')
    offline = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]=='fixture'
    clock = working_now(conn,offline)
    daily = daily_news(conn,config,day,offline=offline,now=clock)
    selected = set(section_ids(config,'all'))
    if not selected and kind == 'morning':
        raise ValueError('请先在来源管理中启用至少一个来源')
    sources = [{k:s[k] for k in ('source_id','name','url','categories','collector_available')}
               for s in config['reader_sources'] if s['source_id'] in selected and s.get('search_enabled', True)]
    plan = None
    context_sources = []
    if kind == 'product' and edition:
        from .search_plan import build_search_plan
        plan = build_search_plan(config, edition['product_code'], extra_keywords)
        sources = plan['selected_sources']
        context_sources = [s for s in config['reader_sources'] if s['source_id'] in section_ids(config, 'all') and s.get('search_enabled',True)]
        selected = set(plan.get('local_source_ids',[])) | {s['source_id'] for s in sources + context_sources}
    cutoff = min(time_bounds(day)[1],clock).isoformat()
    start = time_bounds(day)[0].isoformat()
    material = []
    deferred_events = []
    product = None
    if kind=='morning':
        start = time_bounds(morning_reading.window(day,lookback_days))[0].isoformat()
        from datetime import date
        daily_items=list(daily['items'])
        for offset in range(1,lookback_days):
            earlier=(date.fromisoformat(day)-timedelta(days=offset)).isoformat()
            daily_items+=daily_news(conn,config,earlier,offline=offline,now=clock)['items']
        for item in daily_items:
            if item['source_id'] not in selected:
                continue
            material.append({'id':'local_'+item['version_id'],'title':item['title'],'excerpt':item['lead'],
                'url':item['url'],'source_name':item['source_name'],'source_id':item['source_id'],
                'published_at':item['report_published_at'] or item['first_published_at'],
                'event_id':item['event_id'],'version_id':item['version_id'],'evidence_level':'listing_metadata'})
    else:
        if not edition:
            raise ValueError('产品检索需要明确的期次和新闻截点')
        cutoff = edition['news_cutoff']
        start = (parse_instant(cutoff)-timedelta(days=product_reading.policy()['recent_days'])).isoformat()
        code = edition['product_code']
        # Only explicitly public, frozen references can accompany the profile.
        from .product_context import public_context
        product = dict(product_code=code,name=config['products'][code]['name'],
            observation_variables=edition['profile']['observation_variables'],
            relation_boundaries=edition['profile']['relation_boundaries'],
            holdings_status='关键词和观察变量，不代表当前持仓')
        product['context'] = dict(public_context(conn, edition))
        definition=config['products'][code]
        if definition.get('kind')=='family':
            from .product_cards import registry
            cards=registry(cutoff)
            product.update(kind='family',member_codes=definition['member_codes'],
                members=[dict(code=c,name=cards[c]['name']) for c in definition['member_codes']],
                data_boundary='成员分别核对；系列没有单一代码、净值、规模或合计资金指标')
        # These are a second copy prepared exclusively for drafting. Research
        # already has the dated references; writing loads its own full context.
        product['context'].pop('writing_references',None)
        product['context'].pop('excluded_from_writing',None)
        routed = route_product(conn,config,code,edition_date=edition['edition_date'],news_cutoff=cutoff,now=clock,
                               market_cutoffs=edition['market_cutoffs'])
        events = [e for group in ('within_cutoff','pending_time') for e in routed['groups'][group]]
        # Routing ranks material; it must not remove cross-industry discoveries.
        events.sort(key=lambda e:e.get('product_match',{}).get('lane')!='core')
        recent=[e for e in events if e.get('product_match',{}).get('report_window') in {'in_window','undated'}]
        older=[e for e in events if e not in recent]
        events=recent[:6]+older[:6]+recent[6:]+older[6:]
        broad=[e for e in events if e.get('product_match',{}).get('lane') not in {'core','background'}]
        ordered=events[:5]+broad[:3]+events[5:]
        events=list({e['event_id']:e for e in ordered}.values())
        event_cap=max(1,config['runtime']['second_round']['max_event_candidates']-4)
        deferred_events=[dict(event_id=e['event_id'],title=e['title'],reason='local_event_input_budget') for e in events[event_cap:]]
        for event in events[:event_cap]:
            for version in event['metadata_versions'][:4]:
                m = version['metadata']
                if m.get('source_id') not in selected:
                    continue
                material.append({'id':'local_'+version['version_id'],'title':m['title'],
                    'excerpt':m.get('lead'),'url':version['canonical_url'],
                    'source_name':config['sources'].get(m.get('source_id'),{}).get('name','未知来源'),
                    'source_id':m.get('source_id'),'published_at':m.get('report_published_at') or m.get('first_published_at'),
                    'first_published_at':m.get('first_published_at'),
                    'data_period_start':m.get('data_period_start'), 'data_period_end':m.get('data_period_end'),
                    'time_note':'统计期不是公开时间；发布时间未知，须补证' if m.get('data_period_end') and not (m.get('report_published_at') or m.get('first_published_at')) else None,
                    'event_id':event['event_id'],'version_id':version['version_id'],'evidence_level':'listing_metadata'})
        free_slots=config['runtime']['second_round']['max_event_candidates']-len({s['event_id'] for s in material})
        material+=prior_morning_material(conn,profile_id,sources+context_sources,code,config,start,cutoff,free_slots)
    material = list({v['id']:v for v in material}.values())
    payload = {'version':VERSION,'validation_version':VALIDATION_VERSION,'kind':kind,'day':day,'news_cutoff':cutoff,'window_start':start,
               'edition_id':edition['edition_id'] if edition else None,
               'product':product,'selected_sources':sources,'items':[],
               'allow_web_search':bool(reader['settings'].get('web_search',True)),
               'coverage_notes':[],'category_names':CATEGORIES,'task_limits':reader_limits()}
    payload['source_constraints'] = [{k:v for k,v in s.items() if v is not None}
        for s in source_constraints(config['reader_sources'])]
    payload['timing_policy'] = dict(morning_reading.TIMING)
    if config.get('_host_runtime'):
        payload['host_runtime']={k:v for k,v in config['_host_runtime'].items() if k in {'model','reasoning_effort'}}
    if kind=='morning':
        payload.update(lookback_days=lookback_days,main_days=min(2,lookback_days),
            main_window_start=time_bounds(morning_reading.window(day,min(2,lookback_days)))[0].isoformat(),
            search_plan={'max_queries':24,'queries':[]},
            prompt_version=hashlib.sha256(morning_reading.instructions().encode('utf-8')).hexdigest())
    else:
        payload['task_limits']=dict(payload['task_limits'],max_web_sources=20)
    if product:
        payload['coverage_notes'].extend(product['context']['gaps'])
    if plan is not None:
        payload['selected_sources']=[{k:s.get(k) for k in ('source_id','name','url','fact_question','host_read_allowed')} for s in sources]
        payload.update(request_mode=request_mode,research_question=research_question,
                       editorial_policy=product_reading.policy() | {'products':None})
        payload['prompt_version']=hashlib.sha256(product_reading.instructions(request_mode).encode('utf-8')).hexdigest()
        # Manual references stay in the local UI, outside the host's search
        # input. Existing, cited material can still be reused as background.
        payload['search_plan'] = {k:v for k,v in plan.items() if k not in
            {'manual_sources','needs_confirmation','selected_sources','category_names','keywords','local_source_ids'}}
        payload['search_plan']['queries'] = [dict(q) for q in plan['queries']]
        payload['background_window_start'] = (parse_instant(cutoff)-timedelta(days=plan['background']['days'])).isoformat()
        if plan.get('manual_sources'):
            payload['coverage_notes'].append(f"另有 {len(plan['manual_sources'])} 个手动参考入口因停用、权限或范围待确认，未纳入宿主定向检索")
        payload['context_sources'] = [s['source_id'] for s in context_sources]
        payload['coverage_notes'].extend(plan['gaps'])
        payload['known_events'], reused = reusable_product_material(conn, edition['edition_id'], payload['source_constraints'],profile_id)
        material += reused
        payload.update(product_reading.public_history(conn,edition,profile_id,payload['source_constraints']))
        if request_mode=='explore':
            payload['selected_sources']=[]
            payload['search_plan'].update(scope='global',queries=[],local_source_ids=[])
            payload['search_plan']['observation_queries']=[dict(q,domains=[],source_ids=[]) for q in plan['observation_queries']]
            payload['task_limits']=dict(payload['task_limits'],max_product_items=product_reading.policy()['exploration_items'])
    cap=payload['task_limits']['max_input_characters']
    # Keep a useful material allowance; historical context must not crowd it out.
    history_truncated=False
    for key in ('previous_searches','unresolved_gaps','carried_background','known_events'):
        while payload.get(key) and len(json_text(payload))>cap-5000:
            payload[key].pop()
            history_truncated=True
    if history_truncated:
        payload['coverage_notes'].append('达到输入预算，部分跨期摘要或既往查询未送入模型；本地完整记录仍保留')
    if len(json_text(payload))>cap-500:
        raise ValueError('选中的来源网址超过本次资料长度预算，请先停用部分来源')
    # Bound public input locally. Truncation is always disclosed.
    omitted,truncated=[],[]
    # Interleave sources so a long listing cannot consume every input slot.
    from collections import defaultdict, deque
    groups=defaultdict(deque)
    for item in material:
        groups[item.get('source_id') or item.get('source_name') or 'web'].append(item)
    material=[]
    while any(groups.values()):
        for group in groups.values():
            if group:
                material.append(group.popleft())
    for item in material:
        original=item['excerpt'] or ''
        item['excerpt'] = original[:600 if kind=='morning' else 1200]
        if len(original)>len(item['excerpt']):
            truncated.append(item['id'])
        if len(json_text(payload))+len(json_text(item)) > cap-1500:
            omitted.append(item['id'])
            continue
        payload['items'].append(item)
    payload['input_coverage']={'available_items':len(material),'included_items':len(payload['items']),
        'omitted_count':len(omitted),'excerpt_truncated_count':len(truncated),
        'deferred_local_events_count':len(deferred_events),
        'omitted_ids':omitted[:8],'excerpt_truncated_ids':truncated[:8]}
    if input_audit is not None:
        input_audit.update(payload['input_coverage'],omitted_ids=omitted,excerpt_truncated_ids=truncated,
            included_ids=[s['id'] for s in payload['items']],deferred_local_events=deferred_events)
    if deferred_events:
        payload['coverage_notes'].append(f'本地候选预算：另有{len(deferred_events)}个事件未送入本轮模型，不能视为逐项已检查；完整清单保留在输入审计中')
    if omitted or truncated:
        payload['coverage_notes'].append(f'输入长度受限：{len(omitted)}条未送入、{len(truncated)}条摘录截短；本地完整材料保留，不能称全部已检查')
    if not payload['items']:
        payload['coverage_notes'].append('当前日期 / 产品范围没有已存匹配材料，需要宿主联网检索或补充来源')
    if not payload['allow_web_search']:
        payload['coverage_notes'].append('本次关闭补充联网检索，仅整理已存列表信息')
    if offline:
        payload['coverage_notes'].append('合成测试资料，不是真实新闻')
    return payload


def normalize_source_aliases(raw, payload):
    """Canonicalize unambiguous local labels; never infer a missing citation."""
    ids=[s.get('id') if isinstance(s,dict) else None for s in raw['web_sources']]
    if any(not isinstance(s,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,68}',s) for s in ids):
        raise ValueError('模型引用编号无效或重复')
    existing={s['id'] for s in payload['items']}
    existing.update(r['id'] for r in ((payload.get('product') or {}).get('context') or {}).get('references',[]))
    if len(ids)!=len(set(ids)) or existing.intersection(ids):
        raise ValueError('模型引用编号无效或重复')
    def references(value):
        if isinstance(value,dict):
            for key,part in value.items():
                if key=='source_ref' and isinstance(part,str):yield part
                elif key=='source_refs' and isinstance(part,list):
                    yield from (ref for ref in part if isinstance(ref,str))
                else:yield from references(part)
        elif isinstance(value,list):
            for part in value:yield from references(part)
    reserved=existing|set(ids)|set(references(raw));aliases={}
    for sid in ids:
        if re.fullmatch(r'web_[A-Za-z0-9_-]{1,64}',sid):continue
        target='web_'+sid
        if len(sid)>64 or target in reserved:
            raise ValueError('模型引用编号无法无歧义规范化')
        aliases[sid]=target;reserved.add(target)
    if not aliases:return raw,[]
    def remap(value):
        if isinstance(value,list):return [remap(part) for part in value]
        if not isinstance(value,dict):return value
        result={}
        for key,part in value.items():
            if key=='source_ref' and isinstance(part,str):result[key]=aliases.get(part,part)
            elif key=='source_refs' and isinstance(part,list):
                result[key]=[aliases.get(ref,ref) if isinstance(ref,str) else ref for ref in part]
            else:result[key]=remap(part)
        return result
    normalized=remap(raw)
    for source in normalized['web_sources']:source['id']=aliases.get(source['id'],source['id'])
    return normalized,[dict(reported_id=k,normalized_id=v,reason='unique_local_source_alias') for k,v in aliases.items()]


def validate_report(raw, payload, trace):
    valid_fields=[set(REPORT_SCHEMA['properties'])]
    if payload['kind']=='morning':
        valid_fields.append(set(MORNING_REPORT_SCHEMA['properties']))
    if not isinstance(raw,dict) or set(raw) not in valid_fields:
        raise ValueError('模型输出结构不符合报告契约')
    for key in raw:
        if not isinstance(raw[key],list):
            raise ValueError('模型报告字段必须是列表')
    limits=payload.get('task_limits') or reader_limits()
    if len(raw['web_sources'])>limits['max_web_sources'] or len(raw['items'])>limits['max_product_items' if payload['kind']=='product' else 'max_morning_items']:
        raise ValueError('模型输出超过候选或来源预算')
    source_aliases=[]
    if payload['allow_web_search'] and trace.get('web_actions')!=0:
        raw,source_aliases=normalize_source_aliases(raw,payload)
    sources = {s['id']:dict(s,origin=s.get('origin','saved_metadata')) for s in payload['items']}
    warnings = list(payload['coverage_notes'])
    if source_aliases:
        warnings.append(f'{len(source_aliases)}个唯一来源编号已规范化；未修改来源正文、日期或证据状态')
    cutoff = parse_instant(payload['news_cutoff'])
    start = parse_instant(payload['window_start'])
    blocked = set()
    background = payload.get('search_plan', {}).get('background', {})
    semantic = payload.get('version')==VERSION and payload['kind']=='product'
    background_start = parse_instant(payload.get('background_window_start', payload['window_start']))
    background_domains = {urlsplit(s['url']).hostname.removeprefix('www.') for s in payload['selected_sources']
                          if s['source_id'] in background.get('source_ids', [])}
    for src in raw['web_sources']:
        if not payload['allow_web_search'] or trace.get('web_actions')==0:
            warnings.append('宿主没有可确认的联网动作，已剔除新网址提议')
            continue
        if not isinstance(src,dict) or set(src) not in ({'id','title','url','published_at','excerpt'}, {'id','title','url','published_at','excerpt'} | SOURCE_EXTRA):
            raise ValueError('联网来源字段不完整')
        sid = src['id']
        if not isinstance(sid,str) or not re.fullmatch(r'web_[A-Za-z0-9_-]{1,64}',sid) or sid in sources:
            raise ValueError('模型引用编号无效或重复')
        if any(not isinstance(src[k],str) or len(src[k])>2200 or (k!='excerpt' and not src[k].strip()) for k in ('title','url','excerpt')):
            raise ValueError('联网来源需包含真实标题、链接和内容依据')
        url = normalize_url(src['url'])
        lower, upper = time_bounds(src['published_at'])
        first_lower, first_upper = time_bounds(src.get('first_published_at'))
        # A recent repost does not renew a statistic's first disclosure date.
        is_background = (lower is not None and upper < start) or (first_upper is not None and first_upper < start)
        background_allowed = (background.get('max_items', 0) > 0 and is_background and
            lower is not None and min(lower, first_lower or lower) >= background_start and urlsplit(url).hostname.removeprefix('www.') in background_domains and
            src.get('data_period_start') and src.get('data_period_end'))
        if (lower is not None and lower>cutoff) or (is_background and not background_allowed and not semantic) or (first_lower is not None and first_lower>cutoff):
            blocked.add(sid)
            continue
        proposed = dict(src,url=url,source_name=src['title'],event_id=None,version_id=None,
            origin='host_web_discovery', evidence_level='host_opened_url' if url in trace.get('visited_urls',[]) else 'host_search_hint',
            published_at_basis='host_reported_not_independently_verified')
        if is_background:
            proposed['time_role'] = 'background'
            proposed['time_role_label'] = '较早发布的资料，当前用途及有效性见条目判断' if semantic else '较早发布的统计 / 预测背景，非本窗口新增消息'
        accepted = enrich_source(proposed,src,payload,trace)
        if accepted is None:
            warnings.append('已剔除来自停用或受限来源的新网址；未覆盖旧材料')
            blocked.add(sid)
        else:
            sources[sid] = accepted
    items, seen, rejected_items = [], set(), []
    product_refs = {r['id']:r for r in ((payload.get('product') or {}).get('context') or {}).get('references', [])}
    for item in raw['items']:
        base_fields = {'title','summary','category','why','source_refs'}
        if not isinstance(item,dict) or set(item) not in (base_fields,base_fields|{'claims','event_identity'},base_fields|{'claims','event_identity'}|product_reading.FIELDS,
                base_fields|{'claims','event_identity'}|product_reading.FIELDS|product_reading.RECOMMENDATION_FIELDS):
            raise ValueError('模型条目结构不完整')
        if any(not isinstance(item[k],str) or len(item[k])>600 for k in ('title','summary','why')) or not item['title'].strip() or not item['summary'].strip():
            raise ValueError('模型条目应是简短的标题与摘要')
        if not isinstance(item['category'],str) or item['category'] not in CATEGORIES:
            raise ValueError('模型返回了未配置的分类')
        refs = item['source_refs']
        if not isinstance(refs,list) or not 1<=len(refs)<=limits['max_refs_per_item'] or any(not isinstance(r,str) for r in refs):
            raise ValueError('每条线索需有 1—4 个可追溯来源')
        relationship_refs = [r for r in dict.fromkeys(refs) if r in product_refs]
        if relationship_refs:
            # Product scope supports the relationship, never the news event.
            # Keep it outside event fingerprints so every event sharing the
            # same prospectus does not become a duplicate of every other one.
            refs = [r for r in refs if r not in product_refs]
            if not refs:
                rejected_items.append({'title':item['title'],'reason':'product_background_only',
                                       'source_refs':item['source_refs']})
                continue
            item = dict(item, source_refs=refs)
        if any(ref not in sources or ref in blocked for ref in refs):
            warnings.append('已剔除引用缺失或日期超出范围的模型条目')
            rejected_items.append({'title':item['title'],'reason':'unavailable_source_ref',
                'source_refs':refs,'unavailable_refs':[ref for ref in refs if ref not in sources or ref in blocked]})
            continue
        cited = [sources[ref] for ref in dict.fromkeys(refs)]
        relation_claims = []
        if relationship_refs and item.get('claims'):
            context_sources = [dict(id=r,url=product_refs[r]['source_url'],excerpt=product_refs[r]['excerpt'],
                source_excerpt_verified=True,material_identity='public_product_reference',
                published_at=product_refs[r]['published_at'],missing_items=[]) for r in relationship_refs]
            # Reuse the normal contract/quote checker before separating claims.
            checked = enrich_item({'sources':cited+context_sources},item,payload)
            relation_claims = [c for c in checked['claims'] if c['source_ref'] in product_refs]
            item = dict(item,claims=[c for c in item['claims'] if c['source_ref'] not in product_refs])
        dated = [time_bounds(s.get('published_at')) for s in cited]
        if any(lo is not None and (lo>cutoff or (hi<start and s.get('time_role')!='background' and not semantic)) for s,(lo,hi) in zip(cited,dated)) or any(time_bounds(s.get('first_published_at'))[0] is not None and time_bounds(s.get('first_published_at'))[0]>cutoff for s in cited):
            warnings.append('已剔除新闻截点或采集窗口之外的条目')
            rejected_items.append({'title':item['title'],'reason':'outside_time_window','source_refs':refs})
            continue
        identity = re.sub(r'\s+','',item['title']+item['summary'])
        if payload['kind']=='morning' and identity in seen:
            continue
        seen.add(identity)
        prepared = dict(item,item_id='item_'+str(len(items)+1),rank=len(items)+1,sources=cited,
            event_ids=list(dict.fromkeys(s['event_id'] for s in cited if s.get('event_id'))),
            date_state='pending_time' if any(lo is None or hi>cutoff for lo,hi in dated) else 'reported_in_window',
            fact_verified=False,selected=False)
        if any(s.get('time_role')=='background' for s in cited):
            prepared['time_role'] = 'background' if all(s.get('time_role')=='background' for s in cited) else 'news_with_background'
            if prepared['time_role']=='background':
                if not semantic and sum(i.get('time_role')=='background' for i in items) >= background.get('max_items', 0):
                    warnings.append('较早发布的背景资料达到预算，未替代近期消息名额')
                    continue
                prepared['date_state'] = 'background_before_window'
                if item.get('editorial_role')!='recent' and not prepared['title'].startswith('背景｜'):
                    prepared['title'] = '背景｜' + prepared['title']
        grounded = {s['event_id'] for s in cited if s.get('event_id') and s.get('title') and
                    normalized(s['title'])==normalized(item['title']) and s.get('origin')=='saved_metadata'}
        prepared['local_event_id'] = next(iter(grounded)) if len(grounded)==1 else None
        enriched=enrich_item(prepared,item,payload)
        if relationship_refs:
            enriched['product_relationship'] = {'references':[product_refs[r] for r in relationship_refs],
                                               'claims':relation_claims,'purpose':'product_scope_not_news_evidence'}
        if payload['kind']=='product':
            product_reading.judgment(enriched,item,payload)
            product_reading.reconcile_identity(enriched,payload.get('known_events',[]))
        items.append(enriched)
    before_merge=len(items)
    items = merge_report_events(items)
    context_items=[]
    if payload['kind']=='morning':
        today=parse_instant(payload['main_window_start']) if payload.get('main_window_start') else time_bounds(payload['day'])[0]
        context_items=[i for i in items if all(time_bounds(s.get('first_published_at') or s.get('published_at'))[1] is not None and
            time_bounds(s.get('first_published_at') or s.get('published_at'))[1]<today for s in i['sources'])]
        items=[i for i in items if i not in context_items]
    if semantic:
        primary=0
        for item in items:
            requested=item.get('primary_recommendation',False)
            recommended=item.get('primary_recommendation',False) and primary<3 and item.get('validity_state')=='active'
            item['primary_recommendation']=recommended
            if recommended:
                primary+=1
            elif requested:
                item['recommendation_reason']='本轮主要方向最多3个，或有效性待核实；保留供人工选用。模型原理由：'+item.get('recommendation_reason','')
            if not item.get('recommendation_reason'):
                item['recommendation_reason']='保留供人工选用；本轮未列为主要方向'
    coverage = []
    from .search_audit import audit_search
    search_audit = audit_search(payload, trace)
    supplied = {}
    for item in raw['coverage']:
        if not isinstance(item,dict) or set(item)!={'source_id','status','note'} or not isinstance(item['source_id'],str) or not isinstance(item['status'],str) or item['status'] not in {'found','not_found','blocked','not_checked'} or not isinstance(item['note'],str) or len(item['note'])>600:
            raise ValueError('模型来源覆盖说明无效')
        supplied[item['source_id']] = item
    for source in payload['selected_sources']:
        item = supplied.get(source['source_id'],{'source_id':source['source_id'],'status':'not_checked','note':'宿主未返回该来源的覆盖说明'})
        if trace.get('web_actions') == 0:
            item = dict(item,status='not_checked',note='没有观察到本次外部检索；仅可复用已存材料')
        checked = search_audit['sources'][source['source_id']]
        claimed_status = item['status']
        if payload['kind']=='product' and checked['query_status'] != 'queried' and item['status'] in {'found','not_found'}:
            item = dict(item,status='not_checked',note='未观察到本站独立查询；宿主说明：'+item['note'])
        coverage.append(dict(item,name=source['name'],url=source['url'],status_basis='query_trace_and_host_report',
                             host_reported_status=claimed_status, **checked,
                             body_read_allowed=source.get('host_read_allowed',False)))
    for direction in search_audit['directions']:
        if direction['status'] != 'queried':
            warnings.append('观察方向未匹配到查询记录（含同义检索待核对）：' + direction['name'])
    if search_audit['budget_exceeded']:
        warnings.append('观察到查询次数超出计划预算；宿主查询上限为指令约束，实际用量已记录')
    for gap in raw['gaps']:
        if not isinstance(gap,str) or len(gap)>1000:
            raise ValueError('模型缺口说明无效')
        warnings.append(gap)
    warnings.append('AI 研究摘要；列表元数据与宿主检索结果均需核对原文，未自动转为已核实事实。')
    if payload['kind']=='product':
        warnings.append(f'本次返回 {len(items)} 条有引用材料；数量不足不凑数，当前用途按条目判断。')
    if rejected_items:
        warnings.append(f'模型返回{len(raw["items"])}条，经引用、日期和材料用途检查，{len(rejected_items)}条未作为新闻展示；不等于没有搜到消息')
    result={'validation_version':VALIDATION_VERSION,'items':items,'coverage':coverage,'gaps':list(dict.fromkeys(warnings)),
            'validation_audit':{'proposed_items':len(raw['items']),'accepted_items':len(items),'rejected_items':rejected_items,
                'source_aliases':source_aliases,
                'merged_count':before_merge-len(items)-len(context_items),'context_items':context_items,
                'pending_items':[{'title':i['title'],'reason':'; '.join(i.get('missing_items',[])) or 'evidence_pending'}
                    for i in items if not all(s.get('source_excerpt_verified') for s in i['sources'])]},
            'input_coverage':payload.get('input_coverage'),
            'sources':list(sources.values()),'auto_select':False,
            'web_actions':trace.get('web_actions'),'visited_urls':trace.get('visited_urls',[]),
            'tool_trace':trace,
            'search_audit':search_audit,
            'targeted_execution':'web_activity_observed' if trace.get('web_actions') else 'no_observed_external_search',
            'planned_queries':len(payload.get('search_plan',{}).get('queries',[]))}
    if payload['kind']=='morning':
        result.update(morning_reading.validate(raw,payload,result['sources'],trace))
        if result['timeline_audit']:
            result['gaps'].append('部分跨日判断的日期或引用无法对应，未展示；见时间线核对记录')
    return result


def report_record(row):
    if row is None:
        return None
    result = dict(row)
    for key in ('output_json','usage_json'):
        value = result.pop(key)
        result[key.removesuffix('_json')] = json.loads(value) if value else None
    saved_input=json.loads(result.pop('input_json'))
    result['news_cutoff']=saved_input['news_cutoff']
    result['window_start']=saved_input['window_start']
    result['request_mode']=saved_input.get('request_mode','baseline')
    result['request_id']=saved_input.get('request_id')
    result['search_plan']=saved_input.get('search_plan')
    result['lookback_days']=saved_input.get('lookback_days')
    result['timing_policy']=saved_input.get('timing_policy')
    if result['search_plan'] and result.get('output'):
        result['search_plan']['execution_status']=result['output'].get('targeted_execution','legacy_unmeasured')
    return result


def latest_report(conn,profile_id,kind,day,edition_id=None):
    row = conn.execute('SELECT * FROM fn_reading_reports WHERE profile_id=? AND kind=? AND day=? AND edition_id IS ? ORDER BY rowid DESC LIMIT 1',
                       (profile_id,kind,day,edition_id)).fetchone()
    result = report_record(row)
    if row and row['status'] in {'running','failed'}:
        prior = conn.execute("SELECT * FROM fn_reading_reports WHERE profile_id=? AND kind=? AND day=? AND edition_id IS ? AND status IN ('complete','partial') ORDER BY rowid DESC LIMIT 1",(profile_id,kind,day,edition_id)).fetchone()
        result['previous_report'] = report_record(prior)
    return result


def generate_report(conn, config, profile_id, kind, day, *, edition=None, runner=run_host, progress=None, extra_keywords=None,
                    request_mode='baseline',research_question='',request_id=None,control=None,task_id=None,writing_context=None,
                    lookback_days=7,collection_result=None):
    if control is None:
        from .task_control import TaskControl
        control=TaskControl(product_reading.policy()['exploration_seconds'])
    if control:
        control.check()
    reader = config.get('_reader_profile') or profile(conn,profile_id)
    host = reader['settings'].get('host','none')
    if host=='none':
        raise HostUnavailable('尚未选择模型执行工具，请在来源管理中选择 Codex 或 WorkBuddy')
    mode = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]
    if mode=='fixture' and runner is run_host:
        raise ValueError('合成测试模式不自动调用真实宿主')
    input_audit={}
    payload = build_input(conn,config,profile_id,kind,day,edition,extra_keywords,request_mode=request_mode,research_question=research_question,lookback_days=lookback_days,input_audit=input_audit)
    if collection_result and collection_result.get('collection_incomplete'):
        payload['coverage_notes'].append('前置采集达到60秒上限，已入库材料保留；未完成来源由本轮模型按缺口继续检查'
            if collection_result.get('status')=='collection_timed_out' else
            '前置采集失败，已入库材料保留；尚未完成本次来源采集，不代表没有新资讯')
    if collection_result is not None:
        input_audit['collection']=collection_result
    if request_mode=='explore' and not payload['allow_web_search']:
        raise HostUnavailable('继续探查需要启用联网检索；现有材料仍可正常选用')
    if request_id:
        payload['request_id']=request_id
    if writing_context:
        payload['writing_context']=writing_context
    if control:
        payload['task']={'job_id':task_id or request_id,'started_at':control.started_at,'deadline_at':control.deadline_at}
    raw_input = json_text(payload)
    cache_input=dict(payload)
    cache_input.pop('task',None)
    cache_input.pop('input_coverage',None)
    # The previous result is useful input but must not invalidate its own cache.
    if request_mode!='explore':
        for field in ('known_events','carried_background','previous_searches','unresolved_gaps'):
            cache_input.pop(field,None)
    cache_input['items'] = [s for s in cache_input['items'] if s.get('origin')!='same_edition_reuse']
    empty_note='当前日期 / 产品范围没有已存匹配材料，需要宿主联网检索或补充来源'
    cache_input['coverage_notes']=[n for n in cache_input['coverage_notes'] if n!=empty_note]
    if not cache_input['items']:
        cache_input['coverage_notes'].append(empty_note)
    if kind=='morning' and parse_instant(payload['news_cutoff'])<time_bounds(day)[1]:
        # Cache briefly within today's changing clock without falsifying the
        # original report's exact information cutoff or crossing Edition IDs.
        cache_input['news_cutoff']='current_day_dynamic_cutoff'
    input_hash = hashlib.sha256((json_text(cache_input)+host).encode('utf-8')).hexdigest()
    row = conn.execute("SELECT * FROM fn_reading_reports WHERE profile_id=? AND input_hash=? AND status IN ('complete','partial') ORDER BY rowid DESC LIMIT 1",(profile_id,input_hash)).fetchone()
    if request_mode!='explore' and row and (parse_instant(utc_now())-parse_instant(row['created_at'])).total_seconds()<300:
        return dict(report_record(row),cache_reused=True)
    # Count all attempts across personal profiles, including failures. The
    # budget therefore cannot be evaded by switching profile or failed retries.
    from zoneinfo import ZoneInfo
    today=parse_instant(utc_now()).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    lo,hi=time_bounds(today)
    from .writing import task_attempts
    from .host_task_budget import reservation
    rid = 'reading_'+uuid.uuid4().hex
    with reservation(conn):
        from .host_task_budget import status, exhausted_message
        budget=status(conn,used=task_attempts(conn),limit=reader_limits()['max_tasks_per_day'])
        if budget['remaining']==0:
            raise ValueError(exhausted_message(budget))
        with conn:
            conn.execute('INSERT INTO fn_reading_reports(report_id,profile_id,kind,day,edition_id,product_code,host,status,input_hash,input_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (rid,profile_id,kind,day,edition['edition_id'] if edition else None,edition['product_code'] if edition else None,
                 host,'running',input_hash,raw_input,utc_now()))
    response=None
    started = time.monotonic()
    try:
        options={'web_search':payload['allow_web_search'],'progress':progress}
        options.update(payload.get('host_runtime',{}))
        if control:
            limits=payload['timing_policy']
            options.update(control=control,timeout=control.host_timeout(limits['host_seconds'],limits['finalize_seconds']))
        prompt=product_reading.instructions(request_mode) if kind=='product' else morning_reading.instructions()
        response = runner(host,prompt+raw_input,PRODUCT_REPORT_SCHEMA if kind=='product' else MORNING_REPORT_SCHEMA,**options)
        if control:
            control.check()
        output = validate_report(response.payload,payload,response.trace)
        output['raw_model_output']=response.payload
        output['input_audit']=input_audit
        if kind=='product':
            if request_mode=='explore':
                output['items']=product_reading.filter_incremental(output['items'],_past_product_items(conn,edition['edition_id'],profile_id))
            output['incremental_results'] = compare_product_results(conn,edition['edition_id'],output['items'],profile_id)
        audit = output['search_audit']
        status = 'complete' if (output['items'] and all(c['status']=='found' for c in output['coverage']) and
            all(a['status']=='queried' for a in audit['directions']) and not audit['budget_exceeded']) else 'partial'
        if kind=='morning' and (not output.get('timeline') or any(c['status']!='found' for c in output.get('domain_coverage',[]))):
            status='partial'
        if request_mode=='explore' and response.trace.get('web_actions'):
            status='partial' if audit['budget_exceeded'] else 'complete'
        if control:
            control.check()
            response.usage['total_elapsed_seconds']=round(time.monotonic()-control.started,2)
        with conn:
            conn.execute('UPDATE fn_reading_reports SET status=?,output_json=?,usage_json=?,finished_at=? WHERE report_id=?',
                (status,json_text(output),json_text(response.usage),utc_now(),rid))
    except Exception as exc:
        with conn:
            # Failed schema/semantic checks must not erase what was discovered.
            # A failed record remains ineligible for adoption and normal cache.
            if response:
                conn.execute('UPDATE fn_reading_reports SET output_json=? WHERE report_id=?',
                    (json_text(dict(raw_model_output=response.payload,tool_trace=response.trace,
                        input_audit=input_audit,validation_error=str(exc))),rid))
            elif getattr(exc,'host_trace',None):
                conn.execute('UPDATE fn_reading_reports SET output_json=? WHERE report_id=?',
                    (json_text(dict(tool_trace=exc.host_trace,input_audit=input_audit,
                        incomplete=True,validation_error=str(exc))),rid))
            conn.execute("UPDATE fn_reading_reports SET status='failed',error_code=?,usage_json=?,finished_at=? WHERE report_id=?",
                (getattr(exc,'code',None) or ('host_unavailable' if isinstance(exc,HostUnavailable) else 'invalid_report'),
                 json_text(response.usage) if response else json_text({'host':host,'host_tasks_attempted':1,
                     'host_tasks':None,'input_tokens':None,'output_tokens':None,
                     'elapsed_seconds':round(time.monotonic()-started,2),'cost':None,
                     'cost_status':'managed_by_host','failure_usage':'host_did_not_return_usage'}),utc_now(),rid))
        raise
    return report_record(conn.execute('SELECT * FROM fn_reading_reports WHERE report_id=?',(rid,)).fetchone())


def adopt_report_item(conn, profile_id, report_id, item_id, edition_id):
    """Explicit human import of a suggestion; it remains unverified material."""
    from . import editorial
    row = conn.execute('SELECT * FROM fn_reading_reports WHERE report_id=? AND profile_id=?',
                       (report_id,profile_id)).fetchone()
    if row is None or row['kind']!='product' or row['edition_id']!=edition_id or row['status'] not in {'complete','partial'}:
        raise ValueError('该模型报告不属于当前方案和期次')
    item = next((s for s in json.loads(row['output_json'])['items'] if s['item_id']==item_id),None)
    if item is None:
        raise ValueError('线索已变化，请重新读取报告')
    # The displayed card can aggregate several exploration rounds. Explicit
    # adoption takes that cited union, never silently changes existing choices.
    if item.get('research_identity'):
        aggregate=product_reading.aggregate_reports(conn,profile_id,edition_id)
        merged=next((s for lane in ('recent','background','pending') for s in aggregate[lane]
                     if s.get('research_identity')==item['research_identity']),None)
        if merged:
            item=merged
    edition = editorial.get_edition(conn,edition_id)
    # A morning article may contain dozens of unrelated stories. Reusing its
    # event would give this chosen clue the entire morning article's title.
    # Preserve the explicitly adopted clue as its own unverified candidate.
    identity = item.get('research_identity')
    current = None
    if identity:
        for candidate in conn.execute('SELECT event_id,snapshot_json FROM fn_candidate_pool WHERE edition_id=?',(edition_id,)):
            snapshot = json.loads(candidate[1]) if candidate[1] else {}
            if snapshot.get('research_identity') == identity:
                event_id, current = candidate[0], snapshot
                break
        else:
            event_id = item.get('local_event_id')
    else:
        event_id = None
    if event_id and current is None:
        candidate = conn.execute('SELECT snapshot_json FROM fn_candidate_pool WHERE edition_id=? AND event_id=?',
                                 (edition_id,event_id)).fetchone()
        if candidate:
            if candidate[0]:
                current = json.loads(candidate[0])
            else:
                from .writing_background import snapshot_event
                from .config import load_config
                from .source_catalog import configured_view
                current = snapshot_event(conn,configured_view(conn,load_config(),profile_id),edition_id,event_id,retained=True)
    body = 'AI研究线索（不是原文摘录，尚未核实）：\n'+item['summary']+'\n\n关注理由：'+item['why']+'\n\n出处：\n'+'\n'.join(
        s.get('source_name',s.get('title','来源'))+' '+s['url'] for s in item['sources'])
    payload = {'title':item['title'],'content':body,'source_type':'search_snippet',
        'source_url':item['sources'][0]['url'],'first_published_at':None,
        'attribution_chain':[row['host']+'整理的研究线索，原文待核实'] + [
            json_text({'source_url':s['url'],'version_id':s.get('version_id'),
             'event_id':s.get('event_id'),'access_status':s.get('evidence_level')})
            for s in item['sources']],
        'facts':[],'is_fixture':edition['is_fixture']}
    if identity:
        payload['event'] = {'subject':'host_research','action':'cited_event',
                            'object':identity,'phase':'unknown'}
    result = editorial.import_evidence_payload(conn,payload,edition_id=edition_id,event_id=event_id,
        actual_source_path='reading:'+report_id+':'+item_id,privacy_class='public',declared_source='AI研究摘要，非原文证据')
    from .writing_background import add_candidate
    snapshot = {
        'research_material_version': 1,
        'title': item['title'], 'summary': item['summary'], 'why': item['why'], 'sources': item['sources'],
        'origin': 'ai_research', 'date_state': item['date_state'], 'report_id': report_id,
        'item_id': item_id, 'fact_verified': False, 'evidence_ids': result['evidence_ids'],
        **{k:item.get(k) for k in ('research_identity','event_identity','claims','missing_items','material_fingerprints','time_role','product_relationship')},
        **{k:item[k] for k in product_reading.FIELDS if k in item}}
    add_candidate(conn, {}, edition_id, result['event_id'], snapshot=snapshot)
    if current:
        merged = merge_materials(current,snapshot)
        merged.update({k:snapshot[k] for k in product_reading.FIELDS if k in snapshot})
        for key in ('research_identity','event_identity','research_material_version'):
            if not merged.get(key):
                merged[key] = snapshot.get(key)
        merged['evidence_ids'] = list(dict.fromkeys(current.get('evidence_ids',[]) + result['evidence_ids']))
        merged['supplementary_reports'] = list(dict.fromkeys(current.get('supplementary_reports',[]) + [report_id]))
        with conn:
            conn.execute('UPDATE fn_candidate_pool SET snapshot_json=?,updated_at=? WHERE edition_id=? AND event_id=?',
                (json_text(merged),utc_now(),edition_id,result['event_id']))
    result['merged_existing_candidate'] = current is not None
    return result
