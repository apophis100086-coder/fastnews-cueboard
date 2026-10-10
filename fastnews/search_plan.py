"""Deterministic product/source routing before any host task. No network calls."""
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .source_catalog import CATEGORIES
from .config import load_config
from .live_sources import UniqueLoader

PATH = Path(__file__).resolve().parents[1] / 'config' / 'product_search.yaml'


def search_policy(config=None):
    policy = yaml.load(PATH.read_text(encoding='utf-8'), Loader=UniqueLoader)
    if not isinstance(policy, dict) or set(policy) != {'version', 'note', 'max_queries', 'site_queries', 'products', 'evidence', 'fallback_queries', 'source_terms', 'background'}:
        raise ValueError('产品检索配置字段不完整')
    if type(policy['max_queries']) is not int or not 2 <= policy['max_queries'] <= 24:
        raise ValueError('产品检索最多 24 条查询')
    if type(policy['site_queries']) is not int or not 1 <= policy['site_queries'] < policy['max_queries']:
        raise ValueError('需要同时保留领域网站与关键词检索预算')
    if type(policy['fallback_queries']) is not int or not 1 <= policy['fallback_queries'] <= 6:
        raise ValueError('补搜预留预算需为1—6条')
    if policy['max_queries'] <= policy['fallback_queries'] + 1:
        raise ValueError('主查询和补搜需要各自保留预算')
    background = policy['background']
    if (not isinstance(background, dict) or set(background) != {'days', 'max_items', 'source_ids'} or
            type(background['days']) is not int or not 1 <= background['days'] <= 120 or
            type(background['max_items']) is not int or not 0 <= background['max_items'] <= 3 or
            not isinstance(background['source_ids'], list) or
            any(not isinstance(s, str) for s in background['source_ids'])):
        raise ValueError('背景资料窗口或预算无效')
    if not isinstance(policy['source_terms'], dict) or any(not isinstance(v, str) or not 1 <= len(v) <= 80 for v in policy['source_terms'].values()):
        raise ValueError('来源检索词需为简短字符串')
    evidence = policy['evidence']
    if (not isinstance(evidence,dict) or set(evidence)!={'max_events','max_pages','max_pages_per_event'} or
            any(type(evidence[k]) is not int or not 1<=evidence[k]<=cap
                for k,cap in [('max_events',6),('max_pages',20),('max_pages_per_event',4)])):
        raise ValueError('定向补证最多6个事件、合计20页、每事件4页')
    config = load_config() if config is None else config
    if not isinstance(policy['products'], dict) or set(policy['products']) != set(config['products']):
        raise ValueError('产品检索配置必须完整覆盖已登记产品，且不能包含未登记代码')
    for item in policy['products'].values():
        if set(item) not in ({'categories', 'background_sources', 'keyword_queries', 'source_questions'}, {'categories', 'background_sources', 'keyword_queries', 'source_questions', 'observation_queries'}):
            raise ValueError('产品检索规则字段不完整')
        questions = item['source_questions']
        if not isinstance(questions, dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() or len(v)>300 for k,v in questions.items()):
            raise ValueError('来源事实问题需为有效的来源ID与简短问题')
        for key, values in item.items():
            if key in {'source_questions', 'observation_queries'}:
                continue
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('产品检索规则需要非空字符串列表')
        if set(item['categories']) - set(CATEGORIES):
            raise ValueError('产品检索包含未知分类')
        observations = item.get('observation_queries', [])
        if not isinstance(observations, list) or len(observations) > 6:
            raise ValueError('产品观察方向最多6项')
        for q in observations:
            if (not isinstance(q, dict) or set(q) != {'id', 'name', 'source_id', 'query', 'match_terms'} or
                    any(not isinstance(q[k], str) or not 1 <= len(q[k]) <= 80 for k in ('id', 'name', 'source_id', 'query')) or
                    not isinstance(q['match_terms'], list) or not q['match_terms'] or
                    any(not isinstance(v, str) or not v.strip() for v in q['match_terms'])):
                raise ValueError('产品观察方向字段无效')
    return policy


def build_search_plan(config, code, extra_keywords=None):
    policy = search_policy(config)
    rule = policy['products'][code]
    extra_keywords = [] if extra_keywords is None else extra_keywords
    if (not isinstance(extra_keywords, list) or len(extra_keywords) > 10 or
            any(not isinstance(w, str) or not w.strip() or len(w) > 40 or '\n' in w for w in extra_keywords)):
        raise ValueError('补充检索词最多 10 个，每个 1—40 字')
    extra = list(dict.fromkeys(w.strip() for w in extra_keywords))
    preferred = config['products'][code]['preferred_sources']
    enabled = [s for s in config.get('reader_sources', []) if s['enabled']]
    targets = [s for s in enabled if ('sector' in s['sections'] and
               (set(s['categories']) & set(rule['categories']) or s['source_id'] in preferred))
               or s['source_id'] in rule['background_sources']]
    targets.sort(key=lambda s: (s['source_id'] not in preferred,
                               preferred.index(s['source_id']) if s['source_id'] in preferred else 999))
    local_ids = [s['source_id'] for s in targets]
    fields = ('source_id', 'name', 'url', 'categories', 'collector_available',
              'program_scan_enabled', 'host_read_allowed', 'capability_status', 'access_note',
              'search_disabled_reason', 'host_read_disabled_reason')
    manual = [{k: s.get(k) for k in fields}
              for s in targets if not s.get('search_enabled', True)]
    targets = [s for s in targets if s.get('search_enabled', True)]
    sources = [{k: s.get(k) for k in fields}
               for s in targets]
    for source in sources:
        source['fact_question'] = rule['source_questions'].get(source['source_id'],
            '新闻截点内，' + rule['keyword_queries'][0] + '有什么新披露的事实、阶段和同口径数字？')
        source['tasks'] = ['discover_new_events', 'fill_existing_evidence_gaps']
    queries = []
    by_id = {s['source_id']: s for s in sources}
    observations = []
    # Priority directions run before incidental company headlines. They search
    # ordinary public terms, never benchmark answers or user draft passages.
    for q in rule.get('observation_queries', []):
        if q['source_id'] not in by_id:
            observations.append(dict(q, status='source_disabled'))
            continue
        domain = urlsplit(by_id[q['source_id']]['url']).hostname.removeprefix('www.')
        queries.append(dict(q, lane='observation', domains=[domain], source_ids=[q['source_id']],
                            query='site:' + domain + ' ' + q['query']))
        observations.append(dict(q, domains=[domain], status='planned'))
    covered = {sid for q in queries for sid in q['source_ids']}
    site_capacity = policy['max_queries'] - policy['fallback_queries'] - len(rule['keyword_queries']) - bool(extra)
    for s in sources:
        if s['source_id'] in covered or len(queries)>=site_capacity or sum(q['lane']=='sector_sites' for q in queries) >= policy['site_queries']:
            continue
        domain = urlsplit(s['url']).hostname.removeprefix('www.')
        words = policy['source_terms'].get(code+'/'+s['source_id'], policy['source_terms'].get(
            s['source_id'], ' '.join(rule['keyword_queries'][0].split()[:3])))
        queries.append({'lane':'sector_sites', 'domains':[domain], 'source_ids':[s['source_id']],
                        'query':'site:' + domain + ' ' + words})
    for words in rule['keyword_queries']:
        queries.append({'lane':'keywords', 'domains':[], 'source_ids':[], 'query':words})
    # Extra words get their own query; they never narrow every mandatory query.
    if extra:
        queries.append({'lane':'user_keywords', 'domains':[], 'source_ids':[], 'query':' '.join(extra)})
    queries = queries[:policy['max_queries'] - policy['fallback_queries']]
    if not config.get('reader_web_search_enabled', True):
        queries = []
    result = {'version': policy['version'], 'product_code': code, 'categories': rule['categories'],
            'category_names': [CATEGORIES[c] for c in rule['categories']],
            'selected_sources': sources, 'manual_sources': manual, 'extra_keywords': extra,
            'local_source_ids': local_ids,
            'keywords': config.get('discovery', {}).get('products', {}).get(code, {}).get('core_keywords', []),
            'queries': queries, 'max_queries': policy['max_queries'],
            'fallback_queries': policy['fallback_queries'], 'observation_queries': observations,
            'background': dict(policy['background']) if observations else {'days':0, 'max_items':0, 'source_ids':[]},
            'evidence_budget': dict(policy['evidence'],within_current_host_task=True),
            'needs_confirmation': [s for s in manual + sources if
                s['search_disabled_reason']=='scope_unconfirmed' or s['host_read_disabled_reason']=='scope_unconfirmed'],
            'gaps': [] if sources else ['当前方案没有启用与本产品匹配的领域来源；本次仅能走关键词检索。'],
            'execution_status': 'planned_not_executed'}
    from .product_reading import decorate_plan
    return decorate_plan(result)
