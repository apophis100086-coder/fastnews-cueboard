"""Personal source overlays. Original YAML files and old evidence stay intact."""
from copy import deepcopy
import json
import re
import uuid
from urllib.parse import urlsplit

from .budget import utc_now
from .metadata import normalize_url
from .pipeline import json_text

CATEGORIES = {'morning':'综合晨报', 'policy':'政策与宏观', 'hk':'港股与港股通',
              'medicine':'医药与创新药', 'technology':'科技与半导体',
              'agriculture':'农业', 'metals':'金属与材料', 'other':'其他'}
GROUPS = {
    'morning': 'cls_morning stcn_egs_morning yicai_overnight wallstreetcn_breakfast cls_hk_morning zhitong_hk_morning',
    'policy': 'miit nda nmpa moa gov_policy_library',
    'hk': 'cls_hk_morning zhitong_hk_morning hkex_stock_connect_daily hkex_news ubs_southbound eastmoney_hsgt',
    'medicine': 'nmpa cde medcube dxy_insight fda clinicaltrials',
    'technology': 'trendforce miit nda csia semi',
    'agriculture': 'moa moa_agri_market', 'metals': 'mysteel smm',
}


def profile(conn, profile_id='default'):
    row = conn.execute('SELECT * FROM fn_reader_profiles WHERE profile_id=?', (profile_id,)).fetchone()
    if row is None:
        raise ValueError('个人方案不存在，请重新选择')
    return {'profile_id':row['profile_id'], 'name':row['name'], 'settings':json.loads(row['settings_json'])}


def profiles(conn):
    return [profile(conn, r[0]) for r in conn.execute('SELECT profile_id FROM fn_reader_profiles ORDER BY rowid')]


def save_profile(conn, data):
    name = data.get('name')
    if not isinstance(name,str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError('方案名称需为 1—40 个字')
    pid = data.get('profile_id') or 'reader_' + uuid.uuid4().hex[:12]
    from .workspace import settings as workspace_settings
    current = profile(conn,pid)['settings'] if data.get('profile_id') else {
        'host':'workbuddy','web_search':True,'agent_direct':workspace_settings()['agent_direct']}
    settings = dict(current)
    if 'host' in data:
        if data['host'] not in {'workbuddy','none'}:
            raise ValueError('请选择支持的模型执行工具')
        settings['host'] = data['host']
    if 'web_search' in data:
        if type(data['web_search']) is not bool:
            raise ValueError('联网检索必须为布尔值')
        settings['web_search'] = data['web_search']
    if any(k in data for k in ('workbuddy_model','workbuddy_effort')):
        from .workbuddy_connection import require_verified_model
        model=data.get('workbuddy_model') or None;effort=data.get('workbuddy_effort') or None
        require_verified_model(model,effort)
        settings.update(workbuddy_model=model,workbuddy_effort=effort)
    with conn:
        conn.execute('INSERT INTO fn_reader_profiles VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET name=excluded.name,settings_json=excluded.settings_json',
                     (pid,name.strip(),json_text(settings),utc_now()))
    return profile(conn,pid)


def catalog(conn, config, profile_id='default'):
    from .source_channels import with_channels
    config = with_channels(config)
    reader = profile(conn,profile_id)
    from .source_access import host_access_policy, apply_host_access
    builtin_sources = {sid:src for sid,src in config['sources'].items() if not sid.startswith('custom_')}
    access = host_access_policy(builtin_sources)
    overrides = {r['source_id']:json.loads(r['settings_json']) for r in conn.execute(
        'SELECT * FROM fn_source_preferences WHERE profile_id=?',(profile_id,))}
    from .live_sources import load_live_config
    live = load_live_config(config)['sources']
    states = {r['source_id']:dict(r) for r in conn.execute('SELECT * FROM fn_sources')}
    rows = []
    for sid, src in builtin_sources.items():
        entry = live.get(sid,{})
        groups = config.get('source_channel_categories', {}).get(sid) or [key for key, members in GROUPS.items() if sid in members.split()] or ['other']
        public_url = src['home_url'] if entry.get('parser') == 'eastmoney_southbound' else entry.get('listing_url') or src['home_url']
        item = {'source_id':sid,'name':src['name'], 'url':public_url,
                'categories':groups, 'sections':['morning'] if 'morning' in groups else ['sector'],
                'enabled':True, 'kind':'builtin', 'collection':'builtin'}
        preferences = overrides.pop(sid,{})
        item.update(preferences)
        item.update(kind='builtin',collection='builtin',source_id=sid)
        # An edited original URL is an entry link; it does not silently replace
        # the reviewed collector endpoint or grant that collector new access.
        item['collection_url'] = entry.get('listing_url')
        item['discovery_priority'] = entry.get('discovery_priority', 50)
        item['collector_available'] = bool(entry.get('metadata_allowed'))
        item['automation_policy'] = 'manual_only' if entry and not entry.get('metadata_allowed') else 'public_metadata'
        apply_host_access(item, access['sources'][sid], web_enabled=reader['settings'].get('web_search', True), overrides=preferences)
        item['access_policy_version'] = access['version']
        rows.append(item)
    for sid, item in overrides.items():
        custom = dict(item, source_id=sid,kind='custom',
                         collector_available=item['collection']=='feed',
                         collection_url=item['url'] if item['collection']=='feed' else None,
                         access_note='RSS / Atom 订阅，连接尚需实测' if item['collection']=='feed' else
                                     '原站快捷入口；AI 可按域名检索，尚未接通专用采集器')
        apply_host_access(custom, {'search':'allowed','read':'allowed','reason':'user_public_source',
            'note':'用户登记的公开入口；可以定点搜索，读取遇站点限制即停止。'},
            web_enabled=reader['settings'].get('web_search',True), overrides=item)
        rows.append(custom)
    for item in rows:
        state = states.get(item['source_id'],{})
        item['last_success_at'] = state.get('last_success_at')
        item['capability_status'] = state.get('capability_status','needs_probe')
    from .source_usage import apply_usage
    return apply_usage(rows)


def save_source(conn, config, profile_id, data):
    existing = {s['source_id']:s for s in catalog(conn,config,profile_id)}
    sid = data.get('source_id')
    if sid is not None and sid not in existing:
        raise ValueError('该来源不属于当前个人方案')
    item = dict(existing[sid]) if sid else {'source_id':'custom_'+uuid.uuid4().hex[:16],
             'enabled':True,'kind':'custom','collection':'link','sections':['sector'],'categories':['other']}
    allowed = {'source_id','name','url','enabled','categories','sections','collection','search_enabled','host_read_allowed'}
    if set(data)-allowed:
        raise ValueError('不支持的来源设置字段')
    item.update({k:v for k,v in data.items() if k!='source_id'})
    if not isinstance(item.get('name'),str) or not 1 <= len(item['name'].strip()) <= 80:
        raise ValueError('来源名称需为 1—80 个字')
    item['name'] = item['name'].strip()
    if not isinstance(item.get('url'),str) or len(item['url']) > 2048:
        raise ValueError('请填写公开网站网址')
    item['url'] = normalize_url(item['url'])
    if urlsplit(item['url']).port not in {None,80,443}:
        raise ValueError('只支持标准 HTTP / HTTPS 端口')
    for field, options in [('categories',set(CATEGORIES)),('sections',{'morning','sector'})]:
        values = item.get(field)
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or v not in options for v in values):
            raise ValueError('请选择有效的类别和使用板块')
        item[field] = list(dict.fromkeys(values))
    if type(item.get('enabled')) is not bool:
        raise ValueError('启用状态必须为布尔值')
    for field in ('search_enabled', 'host_read_allowed'):
        if field in data and type(data[field]) is not bool:
            raise ValueError('显式检索/读取偏好必须为布尔值')
    if item['kind']=='builtin':
        if item['collection'] != 'builtin':
            raise ValueError('原来源的适配器由接入配置管理')
    elif item['collection'] not in {'link','feed'}:
        raise ValueError('自定义来源支持网站入口或 RSS / Atom')
    stored = {k:item[k] for k in allowed - {'source_id', 'search_enabled', 'host_read_allowed'}} | {'kind':item['kind']}
    for field in ('search_enabled', 'host_read_allowed'):
        explicit = data.get(field, item.get(field + '_override'))
        if explicit is not None:
            stored[field] = explicit
    with conn:
        conn.execute('INSERT INTO fn_source_preferences VALUES(?,?,?,?) ON CONFLICT(profile_id,source_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at',
                     (profile_id,item['source_id'],json_text(stored),utc_now()))
    return next(s for s in catalog(conn,config,profile_id) if s['source_id']==item['source_id'])


def configured_view(conn, config, profile_id='default'):
    from .source_channels import with_channels
    result = with_channels(config)
    rows = catalog(conn,result,profile_id)
    for item in rows:
        sid = item['source_id']
        if item['kind']=='custom':
            result['sources'][sid] = {'id':sid,'name':item['name'],'home_url':item['url'],
                'enabled':item['enabled'],'morning_scan':'morning' in item['sections'],
                'fetch_full_text_in_morning':False,'obey_robots_txt':True,'rate_limit_per_minute':10,
                'tier':'T4_OTHER','tags':[],'discovery_mode':item['collection']}
            result['source_runtime'][sid] = {'capability_status':'needs_probe','access_review':'not_checked'}
        else:
            result['sources'][sid]['enabled'] = item['enabled']
            result['sources'][sid]['name'] = item['name']
            result['sources'][sid]['morning_scan'] = 'morning' in item['sections']
    result['reader_sources'] = rows
    result['reader_web_search_enabled'] = profile(conn,profile_id)['settings'].get('web_search',True)
    from .workbuddy_connection import model_options
    options=model_options(profile(conn,profile_id)['settings'])
    result['_host_runtime']=options
    return result


def section_ids(config, section, *, collect_only=False):
    if section not in {'morning','sector','all'}:
        raise ValueError('未知来源板块')
    return [s['source_id'] for s in config['reader_sources'] if s['enabled'] and
            (not collect_only or s.get('usage_mode') == 'automatic') and
            (section=='all' or section in s['sections'])]
