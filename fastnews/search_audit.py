"""Audit observable queries; a model's coverage claim is not a search log."""
import re
from urllib.parse import urlsplit


def observed_queries(trace):
    if trace.get('evidence_status')=='agent_reported':
        return []
    result = []
    for action in trace.get('actions', []):
        if action.get('status') in {'failed', 'cancelled'} or action.get('type') not in {'search', 'search_query', 'web_search'}:
            continue
        # Codex emits both a complete batch and a truncated UI summary such as
        # "first query ...". Count the batch only, not that summary as a call.
        values = action.get('queries') or [action.get('query')]
        for value in values:
            if isinstance(value, dict):
                value = value.get('query') or value.get('q')
            if isinstance(value, str) and value.strip():
                # A direct URL is an open, not a search query.
                if not re.match(r'^https?://\S+$', value.strip()):
                    result.append(value.strip())
    return result


def query_domains(query):
    return {s.lower().removeprefix('www.') for s in re.findall(r'(?i)\bsite:([a-z0-9.-]+)', query)}


def audit_search(payload, trace):
    queries = observed_queries(trace)
    plan = payload.get('search_plan', {})
    sources = {}
    for source in payload['selected_sources']:
        domain = urlsplit(source['url']).hostname.removeprefix('www.')
        individual = [q for q in queries if query_domains(q) == {domain}]
        shared = [q for q in queries if domain in query_domains(q) and len(query_domains(q)) > 1]
        sources[source['source_id']] = {'query_status': 'queried' if individual else
            'shared_query_only' if shared else 'no_query_observed',
            'observed_queries': individual or shared}
    directions = []
    for axis in plan.get('observation_queries', []):
        matches = [q for q in queries if (not axis.get('domains') or query_domains(q) == set(axis.get('domains', []))) and
                   any(term.casefold() in q.casefold() for term in axis['match_terms'])]
        directions.append({'id': axis['id'], 'name': axis['name'], 'status':
            'source_disabled' if axis['status'] == 'source_disabled' else
            'queried' if matches else 'no_query_observed', 'observed_queries': matches})
    return {'sources': sources, 'directions': directions, 'observed_query_count': len(queries),
            'query_budget': plan.get('max_queries', 24),
            'budget_exceeded': len(queries) > plan.get('max_queries', 24),
            'note': '仅核对工具可观察查询；查过不等于找到，未观察到不等于网站没有信息。'}
