"""Shared editorial roles and bounded public context for every product."""
from copy import deepcopy
import json
from pathlib import Path

import yaml

from .host_evidence import fingerprint, merge_materials, normalized, source_fingerprint
from .metadata import parse_instant, time_bounds
from .source_access import url_policy

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'editorial_role', 'why_now', 'validity_state', 'validity_reason',
          'newness_hint', 'related_research_identity'}


def policy():
    value = yaml.safe_load((ROOT/'config/product_reading.yaml').read_text(encoding='utf-8'))
    for key, low, high in [('recent_days',1,30), ('background_days',1,730),
        ('recent_items',1,10), ('background_items',1,5), ('exploration_items',1,5),
        ('exploration_seconds',40,480), ('host_seconds',10,450),
        ('finalize_seconds',30,60), ('history_reports',1,50)]:
        if type(value.get(key)) is not int or not low <= value[key] <= high:
            raise ValueError('产品判读配置无效：'+key)
    return value


def decorate_plan(plan):
    settings = policy()
    extra = [dict(q, domains=[], source_ids=[], lane='observation', status='planned')
             for q in settings['products'].get(plan['product_code'], [])]
    plan['observation_queries'] += extra
    if plan['queries']:
        observations = [q for q in plan['queries'] if q['lane']=='observation'] + extra
        keywords = [q for q in plan['queries'] if q['lane'] in {'keywords','user_keywords'}]
        sites = [q for q in plan['queries'] if q['lane']=='sector_sites']
        plan['queries'] = (observations+keywords+sites)[:plan['max_queries']-plan['fallback_queries']]
    plan['background'] = dict(days=settings['background_days'], max_items=settings['background_items'],
                              source_ids=[], soft_window=True)
    plan['recent_days'] = settings['recent_days']
    plan['reading_version'] = settings['version']
    return plan


def instructions(mode):
    return '\n\n'.join((ROOT/'prompts'/name).read_text(encoding='utf-8') for name in
        ['product_reading_common.md', 'product_reading_'+mode+'.md'])+'\nINPUT_JSON:\n'


def extend_schema(schema):
    result = deepcopy(schema)
    item = result['properties']['items']['items']
    item['properties'].update(
        editorial_role={'type':'string','enum':['recent','background']},
        why_now={'type':'string'}, validity_reason={'type':'string'},
        validity_state={'type':'string','enum':['active','superseded','uncertain']},
        newness_hint={'type':'string','enum':['new_event','new_stage','new_evidence','repeated']},
        related_research_identity={'type':['string','null']})
    item['required'] = list(item['properties'])
    return result


def judgment(item, raw, payload):
    if FIELDS <= set(raw):
        if raw['editorial_role'] not in {'recent','background'} or raw['validity_state'] not in {'active','superseded','uncertain'} or raw['newness_hint'] not in {'new_event','new_stage','new_evidence','repeated'}:
            raise ValueError('产品判读字段无效')
        if any(not isinstance(raw[k],str) or not raw[k].strip() or len(raw[k])>600 for k in ('why_now','validity_reason')):
            raise ValueError('需要简短的当前价值和有效性依据')
        related = raw['related_research_identity']
        if related is not None and related not in {k['research_identity'] for k in payload.get('known_events', [])}:
            raise ValueError('前序事件必须指向本次已知身份')
        item.update({k:raw[k] for k in FIELDS})
    else:
        # Legacy reports and adapters remain readable, without invented judgments.
        item.update(editorial_role='background' if item.get('time_role')=='background' else 'recent',
            why_now='', validity_state='uncertain', validity_reason='旧格式材料，尚未完成有效性判断',
            newness_hint='new_event', related_research_identity=None)
    if item['editorial_role']=='background':
        item['time_role']='background'
    elif item.get('time_role')=='background':
        item['time_role']='news_with_background'
    return item


def _rows(conn, profile_id, edition_id=None, code=None, limit=None):
    sql = "SELECT * FROM fn_reading_reports WHERE profile_id=? AND kind='product' AND status IN ('complete','partial')"
    args = [profile_id]
    if edition_id:
        sql += ' AND edition_id=?'; args.append(edition_id)
    if code:
        sql += ' AND product_code=?'; args.append(code)
    sql += ' ORDER BY rowid DESC'
    if limit:
        sql += ' LIMIT ?'; args.append(limit)
    return list(conn.execute(sql,args))


def public_history(conn, edition, profile_id, constraints):
    """Never includes human notes, imported documents or private snapshots."""
    cutoff = parse_instant(edition['news_cutoff'])
    known, carried, searches, gaps = {}, [], [], []
    for row in _rows(conn,profile_id,code=edition['product_code'],limit=policy()['history_reports']):
        context = json.loads(row['input_json'])
        if parse_instant(context['news_cutoff'])>cutoff:
            continue
        output = json.loads(row['output_json'])
        same = row['edition_id']==edition['edition_id']
        if same:
            from .search_audit import observed_queries
            searches += observed_queries(output.get('tool_trace',{}))
            gaps += output.get('gaps',[])[:4]
        for item in output['items']:
            sources = [s for s in item['sources'] if url_policy(s['url'],constraints,s.get('source_id'))['search_enabled']
                       and (time_bounds(s.get('published_at'))[0] is None or time_bounds(s.get('published_at'))[0]<=cutoff)]
            if not sources:
                continue
            identity = item.get('research_identity') or fingerprint(['legacy',item['title'],[s['url'] for s in sources]])
            if identity in known or len(known)>=24:
                continue
            record = dict(research_identity=identity,title=item['title'][:140],summary=item['summary'][:160],
                event_identity=item.get('event_identity'),source_urls=list(dict.fromkeys(s['url'] for s in sources))[:4],
                periods=sorted({(s.get('data_period_start') or '',s.get('data_period_end') or '') for s in sources}),
                editorial_role=item.get('editorial_role','background' if item.get('time_role')=='background' else 'recent'),
                validity_state=item.get('validity_state','uncertain'),same_edition=same)
            known[identity]=record
            if not same:
                carried.append(dict(research_identity=identity,prior_report_id=row['report_id'],prior_cutoff=context['news_cutoff']))
    return dict(known_events=list(known.values()),carried_background=carried[:8],
                previous_searches=list(dict.fromkeys(searches))[:24],unresolved_gaps=list(dict.fromkeys(gaps))[:8])


def reconcile_identity(item, known):
    """Stable cited identity despite rewritten titles; never merge changed stages/periods."""
    identity=item.get('event_identity') or {}
    periods=sorted({(s.get('data_period_start') or '',s.get('data_period_end') or '') for s in item['sources']})
    urls={s['url'] for s in item['sources']}
    for previous in known:
        before=previous.get('event_identity') or {}
        if (identity and before and identity.get('event_date') and
            all(normalized(identity.get(k) or '')==normalized(before.get(k) or '') for k in ('subject','object','phase','event_date')) and
            urls.intersection(previous.get('source_urls',[])) and
            periods==[tuple(p) for p in previous.get('periods',[])]):
            item['research_identity']=previous['research_identity']; break
    return item


def filter_incremental(items, prior):
    output=[]
    for item in items:
        if item.get('newness_hint')=='repeated':
            continue
        old=prior.get(item['research_identity'])
        if old:
            urls={s['url'] for s in old['sources']}
            materials={source_fingerprint(s) for s in old['sources']}
            claims={c.get('claim_fingerprint') for c in old.get('claims',[])}
            old_quotes={normalized(c.get('quote') or '') for c in old.get('claims',[])}
            new_claims=any(c.get('claim_fingerprint') not in claims and c.get('quote') and
                normalized(c['quote']) not in old_quotes for c in item.get('claims',[]))
            # Reworded summaries/snippets at the same URLs are not evidence gain.
            new_evidence=any(s['url'] not in urls or (source_fingerprint(s) not in materials and
                s.get('material_identity') in {'host_tool_fragment','program_fragment','host_report_pending','public_restatement'} and
                item.get('newness_hint')=='new_evidence') for s in item['sources'])
            validity_change=item.get('validity_state')=='superseded' and old.get('validity_state')!='superseded'
            if not new_claims and not new_evidence and not validity_change:
                continue
            item['incremental_type']='new_evidence'
        else:
            item['incremental_type']='new_stage' if item.get('related_research_identity') else 'new_event'
        output.append(item)
    return output


def aggregate_reports(conn, profile_id, edition_id):
    rows=list(reversed(_rows(conn,profile_id,edition_id=edition_id)))
    grouped, runs={},[]
    baseline=max((i for i,row in enumerate(rows) if json.loads(row['input_json']).get('request_mode','baseline')=='baseline'),default=0)
    for index,row in enumerate(rows):
        saved=json.loads(row['input_json']); output=json.loads(row['output_json'])
        mode=saved.get('request_mode','baseline')
        runs.append({'report_id':row['report_id'],'mode':mode,'finished_at':row['finished_at'],
                     'count':len(output['items']),'status':row['status'],'usage':json.loads(row['usage_json'] or '{}'),
                     'gaps':output.get('gaps',[]),'coverage':output.get('coverage',[]),
                     'search_audit':output.get('search_audit'), 'incremental_results':output.get('incremental_results',{})})
        if index<baseline:
            continue
        for original in output['items']:
            item=deepcopy(original)
            key=item.get('research_identity') or row['report_id']+item['item_id']
            item.update(report_id=row['report_id'],editorial_role=item.get('editorial_role',
                'background' if item.get('time_role')=='background' else 'recent'),last_report_id=row['report_id'])
            if key in grouped:
                old=grouped[key]; merged=merge_materials(old,item)
                merged.update({k:item[k] for k in FIELDS if k in item})
                # New evidence stays in the original card and column. A new
                # stage has its own identity and can use a different role.
                merged['editorial_role']=old['editorial_role']
                merged.update(report_id=row['report_id'],item_id=item['item_id'],last_report_id=row['report_id'],
                              incremental_type=item.get('incremental_type','new_evidence'))
                grouped[key]=merged
            else:
                grouped[key]=item
    latest=runs[-1]['report_id'] if runs else None
    result={'recent':[],'background':[],'pending':[],'runs':runs,'latest_report_id':latest,
            'limits':{k:policy()[k] for k in ('recent_items','background_items')}}
    for item in grouped.values():
        item['latest_increment']=bool(runs and runs[-1]['mode']=='explore' and item['last_report_id']==latest)
        lane='pending' if item.get('validity_state')=='superseded' else item['editorial_role']
        result[lane].append(item)
    return result
