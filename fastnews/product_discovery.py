"""Bounded public listing refresh and transparent product discovery matching.

No full text, model calls, adopted choices, current holdings or verified facts.
"""
from datetime import timedelta
import re

from .adapters import DiscoveryError
from .budget import BudgetExhausted, safe_error_code, utc_now
from .metadata import _ALLOWED_INPUTS, normalize_record, parse_instant, time_bounds
from .pipeline import store_record


def keyword_hits(text, keywords):
    return [word for word in keywords if re.search(
        (r'(?<![a-z0-9_])' + re.escape(word) + r'(?![a-z0-9_])')
        if word.isascii() else r'(?<!逆)回购' if word=='回购' else re.escape(word), text, re.I)]


def product_match(event, config, code, cutoff):
    policy = config.get('discovery', {})
    settings = policy.get('products', {}).get(code, {})
    keywords = settings.get('core_keywords', config['products'][code]['search_expansion'])
    references, found, excluded = [], set(), set()
    dates = []
    for version in event['metadata_versions']:
        record = version['metadata']
        text = record['title'] + '\n' + record.get('lead', '')
        exclusions = keyword_hits(text, settings.get('excluded_keywords', []))
        excluded.update(exclusions)
        hits = keyword_hits(text, keywords) if not exclusions else []
        found.update(hits)
        # Exact listing snippets, never generated assertions. Different digest
        # bullets may relate to different products; show the matching bullets.
        for snippet in re.split(r'[\n①②③④⑤⑥⑦⑧⑨⑩；]+', text):
            words = keyword_hits(snippet, hits)
            if words and len(references) < 4:
                references.append({'text':snippet.strip()[:600], 'keywords':words,
                    'version_id':version['version_id'], 'url':version['canonical_url'],
                    'basis':'listing_metadata_not_original_evidence'})
        report_date = record.get('source_revision_at') or record.get('report_published_at') or record.get('first_published_at')
        if report_date is None and record.get('source_id') == 'fda':
            # Older saved records retain the original explicit CMS timestamp
            # in lineage. Use it for discovery age only, without rewriting DB.
            for item in record.get('attribution_chain',[]):
                if isinstance(item,dict) and item.get('machine_datetime'):
                    try:
                        report_date = parse_instant(item['machine_datetime']).isoformat()
                    except ValueError:
                        pass
        lower, upper = time_bounds(report_date)
        if lower is not None:
            dates.append((lower, upper))
    background_topics = set(event['routing']['matched_topic_weights'])
    if code == '517120':
        background_topics.discard('INNOVATIVE_DRUG')  # FDA/BD acronym alone is insufficient.
    lane = 'core' if found else 'background' if background_topics and not excluded else 'unmatched'
    recent_start = cutoff - timedelta(hours=policy.get('lookback_hours',72))
    window = ('in_window' if any(lo <= cutoff and hi >= recent_start for lo,hi in dates)
              else 'after_cutoff' if dates and min(lo for lo,hi in dates) > cutoff
              else 'older' if dates else 'undated')
    return {'lane':lane, 'keywords':sorted(found), 'excerpts':references,
            'excluded_keywords':sorted(excluded), 'report_window':window,
            'latest_report_at':max((lo for lo,hi in dates), default=None).isoformat() if dates else None,
            'rule_version':policy.get('version','original_search_expansion'),
            'explanation':'标题/列表摘要命中观察变量：' + '、'.join(sorted(found)) if found else
                          '仅作宏观或行业背景，尚无产品专项命中' if lane=='background' else '未命中该产品的观察变量',
            'holdings_verified':False, 'fact_verified':False}


def refresh_fixed_sources(conn, config, code, cutoff, budget, *, mode, adapter=None, progress=None):
    end = parse_instant(cutoff)
    start = end - timedelta(hours=config.get('discovery',{}).get('lookback_hours',72))
    report = {'window_start':start.isoformat(), 'window_end':end.isoformat(),
              'is_fixture':mode=='fixture', 'sources':[], 'articles_new':0, 'versions_new':0,
              'request_count':0, 'status':'offline_disabled' if mode=='fixture' and adapter is None else 'partial',
              'network_usage':{}, 'note':'当前固定来源列表的有限补取；不是全网搜索，不抓全文，不修改晨检游标。'}
    if mode == 'fixture' and adapter is None:
        return report
    if adapter is None:
        from .live_sources import LiveAdapter
        adapter = LiveAdapter(config)
    source_ids = list(dict.fromkeys(config['products'][code]['preferred_sources'] +
                                   config.get('discovery',{}).get('supplemental_sources',[])))
    if config.get('reader_sources'):
        from .search_plan import build_search_plan
        targets = build_search_plan(config, code)['selected_sources']
        source_ids = list(dict.fromkeys([s['source_id'] for s in targets] +
                                       config.get('discovery', {}).get('supplemental_sources', [])))
        # Manual reference links stay in the catalog; routine research spends
        # requests on automatic collectors and the user's custom search links.
        manual_ids = {s['source_id'] for s in config['reader_sources'] if s.get('usage_mode') == 'manual'}
        source_ids = [sid for sid in source_ids if sid not in manual_ids]
        priorities = {s['source_id']:s.get('discovery_priority', 50) for s in config['reader_sources']}
        source_ids.sort(key=lambda sid: (sid not in getattr(adapter, 'supported_sources', source_ids),
                                        priorities.get(sid, 50)))
    for sid in source_ids:
        row = {'source_id':sid, 'name':config['sources'][sid]['name'], 'status':'unavailable',
               'error_code':None, 'items_seen':0, 'articles_new':0, 'request_performed':False}
        report['sources'].append(row)
        settings = getattr(adapter, 'settings', {}).get('sources', {})
        if not config['sources'][sid]['enabled']:
            row.update(status='disabled', error_code='source_disabled')
        elif hasattr(adapter, 'supported_sources') and sid not in adapter.supported_sources:
            row['error_code'] = 'source_terms_manual_only' if sid in settings else 'adapter_not_connected'
        else:
            try:
                call = budget.reserve('search','fixed_source_metadata',sid,estimated_max_cost=0)
            except BudgetExhausted as exc:
                row.update(status='budget_stopped',error_code=str(exc))
            else:
                report['request_count'] += 1
                row['request_performed'] = True
                try:
                    limit = min(50,config['runtime']['morning_scan']['max_items_per_source'])
                    page = adapter.discover(sid,start,end,limit)
                    if len(page.records) > limit:
                        raise DiscoveryError('invalid_metadata')
                    checked = []
                    for record in page.records:
                        if record.get('source_id') != sid or record.get('is_fixture') != (mode=='fixture'):
                            raise DiscoveryError('invalid_metadata')
                        checked.append(normalize_record({k:v for k,v in record.items() if k in _ALLOWED_INPUTS},is_fixture=mode=='fixture'))
                    added_articles, added_versions = 0, 0
                    with conn:
                        for record in checked:
                            # Preserve actual listing acquisition time, including cache reuse.
                            retrieved = next((s['retrieved_at'] for s in record['attribution_chain'] if isinstance(s,dict) and s.get('retrieved_at')), utc_now())
                            articles, versions = store_record(conn,record,config,retrieved)
                            added_articles += articles
                            added_versions += versions
                        if mode == 'live':
                            conn.execute("UPDATE fn_sources SET last_success_at=?,capability_status='ready_metadata',adapter_name=? WHERE source_id=?",
                                         (utc_now(),type(adapter).__name__,sid))
                    row['articles_new'] = added_articles
                    report['articles_new'] += added_articles
                    report['versions_new'] += added_versions
                    row.update(status='read' if checked else 'empty',items_seen=len(checked),
                               coverage_incomplete=not page.complete,
                               discovery=getattr(adapter,'last_discoveries',{}).get(sid))
                    budget.settle(call,usage={'cost':0,'input_tokens':0,'output_tokens':0})
                except Exception as exc:
                    code = safe_error_code(exc.code) if isinstance(exc,DiscoveryError) else 'invalid_metadata'
                    row.update(status='failed',error_code=code)
                    budget.settle(call,status='failed',usage={'cost':0,'input_tokens':0,'output_tokens':0},error_code=code)
        if progress:
            progress(dict(row))
    report['network_usage'] = dict(getattr(adapter,'stats',{}))
    return report
