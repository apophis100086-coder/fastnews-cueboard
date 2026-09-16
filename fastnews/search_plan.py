"""Deterministic product/source routing before any host task. No network calls."""
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .source_catalog import CATEGORIES
from .live_sources import UniqueLoader

PATH = Path(__file__).resolve().parents[1] / 'config' / 'product_search.yaml'


def search_policy():
    policy = yaml.load(PATH.read_text(encoding='utf-8'), Loader=UniqueLoader)
    if not isinstance(policy, dict) or set(policy) != {'version', 'note', 'max_queries', 'site_queries', 'products', 'evidence'}:
        raise ValueError('产品检索配置字段不完整')
    if type(policy['max_queries']) is not int or not 2 <= policy['max_queries'] <= 8:
        raise ValueError('产品检索最多 8 条查询')
    if type(policy['site_queries']) is not int or not 1 <= policy['site_queries'] < policy['max_queries']:
        raise ValueError('需要同时保留领域网站与关键词检索预算')
    evidence = policy['evidence']
    if (not isinstance(evidence,dict) or set(evidence)!={'max_events','max_pages_per_event'} or
            any(type(evidence[k]) is not int or not 1<=evidence[k]<=cap
                for k,cap in [('max_events',3),('max_pages_per_event',2)])):
        raise ValueError('定向补证最多3个事件，每事件最多2个页面')
    if set(policy['products']) != {'513130', '513530', '588710', '517120'}:
        raise ValueError('产品检索配置需保留四只产品')
    for item in policy['products'].values():
        if set(item) != {'categories', 'background_sources', 'keyword_queries', 'source_questions'}:
            raise ValueError('产品检索规则字段不完整')
        questions = item['source_questions']
        if not isinstance(questions, dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() or len(v)>300 for k,v in questions.items()):
            raise ValueError('来源事实问题需为有效的来源ID与简短问题')
        for key, values in item.items():
            if key == 'source_questions':
                continue
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('产品检索规则需要非空字符串列表')
        if set(item['categories']) - set(CATEGORIES):
            raise ValueError('产品检索包含未知分类')
    return policy


def build_search_plan(config, code, extra_keywords=None):
    policy = search_policy()
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
    domains = list(dict.fromkeys(urlsplit(s['url']).hostname.removeprefix('www.') for s in targets))
    queries = []
    count = min(policy['site_queries'], len(domains))
    # Group domains without adding calls; all routed domains are represented.
    for i in range(count):
        group = domains[i::count]
        words = rule['keyword_queries'][i % len(rule['keyword_queries'])]
        questions = [{'source_id':s['source_id'], 'question':s['fact_question']} for s in sources
                     if urlsplit(s['url']).hostname.removeprefix('www.') in group]
        if questions:
            words = ' '.join(q['question'] for q in questions[:2])
        queries.append({'lane': 'sector_sites', 'domains': group,
                        'query': '(' + ' OR '.join('site:' + d for d in group) + ') ' + words,
                        'source_questions': questions, 'supplementary_keywords': extra})
    for words in rule['keyword_queries'][:policy['max_queries'] - count]:
        queries.append({'lane': 'keywords', 'domains': [], 'query': words + (' ' + ' '.join(extra) if extra else '')})
    if not config.get('reader_web_search_enabled', True):
        queries = []
    return {'version': policy['version'], 'product_code': code, 'categories': rule['categories'],
            'category_names': [CATEGORIES[c] for c in rule['categories']],
            'selected_sources': sources, 'manual_sources': manual, 'extra_keywords': extra,
            'local_source_ids': local_ids,
            'keywords': config.get('discovery', {}).get('products', {}).get(code, {}).get('core_keywords', []),
            'queries': queries, 'max_queries': policy['max_queries'],
            'evidence_budget': dict(policy['evidence'],within_current_host_task=True),
            'needs_confirmation': [s for s in manual + sources if
                s['search_disabled_reason']=='scope_unconfirmed' or s['host_read_disabled_reason']=='scope_unconfirmed'],
            'gaps': [] if sources else ['当前方案没有启用与本产品匹配的领域来源；本次仅能走关键词检索。'],
            'execution_status': 'planned_not_executed'}
