"""Deterministic product/source routing before any host task. No network calls."""
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .source_catalog import CATEGORIES

PATH = Path(__file__).resolve().parents[1] / 'config' / 'product_search.yaml'


def search_policy():
    policy = yaml.safe_load(PATH.read_text(encoding='utf-8'))
    if not isinstance(policy, dict) or set(policy) != {'version', 'note', 'max_queries', 'site_queries', 'products'}:
        raise ValueError('产品检索配置字段不完整')
    if type(policy['max_queries']) is not int or not 2 <= policy['max_queries'] <= 8:
        raise ValueError('产品检索最多 8 条查询')
    if type(policy['site_queries']) is not int or not 1 <= policy['site_queries'] < policy['max_queries']:
        raise ValueError('需要同时保留领域网站与关键词检索预算')
    if set(policy['products']) != {'513130', '513530', '588710', '517120'}:
        raise ValueError('产品检索配置需保留四只产品')
    for item in policy['products'].values():
        if set(item) != {'categories', 'background_sources', 'keyword_queries'}:
            raise ValueError('产品检索规则字段不完整')
        for key, values in item.items():
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
    manual = [{k: s[k] for k in ('source_id', 'name', 'url', 'categories', 'collector_available')}
              for s in targets if not s.get('search_enabled', True)]
    targets = [s for s in targets if s.get('search_enabled', True)]
    sources = [{k: s[k] for k in ('source_id', 'name', 'url', 'categories', 'collector_available')}
               for s in targets]
    domains = list(dict.fromkeys(urlsplit(s['url']).hostname.removeprefix('www.') for s in targets))
    queries = []
    count = min(policy['site_queries'], len(domains))
    # Group domains without adding calls; all routed domains are represented.
    for i in range(count):
        group = domains[i::count]
        words = ' '.join(extra[:3]) if extra else rule['keyword_queries'][i % len(rule['keyword_queries'])]
        queries.append({'lane': 'sector_sites', 'domains': group,
                        'query': '(' + ' OR '.join('site:' + d for d in group) + ') ' + words})
    for words in rule['keyword_queries'][:policy['max_queries'] - count]:
        queries.append({'lane': 'keywords', 'domains': [], 'query': words + (' ' + ' '.join(extra) if extra else '')})
    return {'version': policy['version'], 'product_code': code, 'categories': rule['categories'],
            'category_names': [CATEGORIES[c] for c in rule['categories']],
            'selected_sources': sources, 'manual_sources': manual, 'extra_keywords': extra,
            'keywords': config.get('discovery', {}).get('products', {}).get(code, {}).get('core_keywords', []),
            'queries': queries, 'max_queries': policy['max_queries'],
            'gaps': [] if sources else ['当前方案没有启用与本产品匹配的领域来源；本次仅能走关键词检索。'],
            'execution_status': 'planned_not_executed'}
