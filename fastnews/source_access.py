"""Product host permissions, independent of program collector capabilities."""
from pathlib import Path
from urllib.parse import urlsplit
import yaml

from .live_sources import UniqueLoader

PATH = Path(__file__).resolve().parents[1] / 'config' / 'host_source_access.yaml'
STATES = {'allowed', 'blocked', 'needs_review'}


def host_access_policy(source_ids):
    value = yaml.load(PATH.read_text(encoding='utf-8'), Loader=UniqueLoader)
    if not isinstance(value, dict) or set(value) != {'version', 'note', 'sources'}:
        raise ValueError('宿主来源策略字段不完整')
    if not all(isinstance(value[k], str) and value[k].strip() for k in ('version', 'note')):
        raise ValueError('宿主来源策略版本与说明必须非空')
    if not isinstance(value['sources'], dict) or set(value['sources']) != set(source_ids):
        raise ValueError('宿主来源策略需逐项覆盖现有内置来源，不得默认为已授权')
    for rule in value['sources'].values():
        if (not isinstance(rule, dict) or set(rule) != {'search', 'read', 'reason', 'note'} or
            any(not isinstance(v, str) or not v.strip() for v in rule.values()) or
            rule['search'] not in STATES or rule['read'] not in STATES or
            rule['read'] == 'allowed' and rule['search'] != 'allowed'):
            raise ValueError('宿主来源策略包含无效状态或缺少原因')
    return value


def apply_host_access(item, rule, *, web_enabled, overrides=None):
    overrides = overrides or {}
    search = overrides.get('search_enabled')
    read = overrides.get('host_read_allowed')
    if any(v is not None and type(v) is not bool for v in (search, read)):
        raise ValueError('来源的显式检索/读取偏好必须为布尔值')
    item.update(search_policy=rule['search'], host_read_policy=rule['read'],
                access_reason=rule['reason'], access_note=rule['note'],
                search_enabled_override=search, host_read_allowed_override=read)
    reason = ('source_disabled' if not item['enabled'] else 'web_disabled' if not web_enabled else
              'user_search_disabled' if search is False else None)
    item['search_enabled'] = reason is None and rule['search'] == 'allowed'
    item['host_read_allowed'] = item['search_enabled'] and read is not False and rule['read'] == 'allowed'
    item['search_disabled_reason'] = reason or (rule['reason'] if rule['search'] != 'allowed' else None)
    item['host_read_disabled_reason'] = reason or ('user_read_disabled' if read is False else None) or (
        rule['reason'] if not item['host_read_allowed'] else None)
    return item


def source_constraints(sources):
    """Send only public URLs and policy, including disabled sources as exclusions."""
    return [{k: s.get(k) for k in ('source_id', 'url', 'search_enabled',
        'host_read_allowed', 'search_disabled_reason', 'host_read_disabled_reason')}
        for s in sources]


def url_policy(url, constraints, source_id=None):
    """Unknown public origins can be traced; known restrictions remain effective.

    Same-host channels may coexist. An explicit source ID must match its host;
    a permissive channel never overrides a blocked exact path.
    """
    parsed = urlsplit(url)
    host = parsed.hostname.removeprefix('www.')
    declared = next((s for s in constraints if s['source_id'] == source_id), None)
    if declared and urlsplit(declared['url']).hostname.removeprefix('www.') != host:
        return {'search_enabled': False, 'host_read_allowed': False, 'basis': 'source_host_mismatch'}
    matches = [s for s in constraints if urlsplit(s['url']).hostname.removeprefix('www.') == host]
    if not matches:
        return {'search_enabled': True, 'host_read_allowed': True, 'basis': 'external_public_origin'}
    exact = [s for s in matches if parsed.path.rstrip('/') == urlsplit(s['url']).path.rstrip('/') or
             urlsplit(s['url']).path.rstrip('/') and parsed.path.startswith(urlsplit(s['url']).path.rstrip('/') + '/')]
    if any(not s['search_enabled'] for s in exact):
        return {'search_enabled': False, 'host_read_allowed': False, 'basis': 'excluded_source_path'}
    claimed = next((s for s in matches if s['source_id'] == source_id), None)
    if source_id is not None and any(s['source_id'] == source_id for s in constraints) and claimed is None:
        return {'search_enabled': False, 'host_read_allowed': False, 'basis': 'source_host_mismatch'}
    allowed = [s for s in matches if s['search_enabled']]
    return {'search_enabled': bool(allowed) and (claimed is None or claimed['search_enabled']),
            'host_read_allowed': bool(allowed) and all(s['host_read_allowed'] for s in exact or ([claimed] if claimed else allowed)),
            'basis': 'configured_source_policy'}
