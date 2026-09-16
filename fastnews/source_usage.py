"""Editorial access choices; never alter the user's enabled/source preferences."""

# These are coverage complements, not claims that another publisher supplies the
# original site's datasets, regulatory decisions or company announcements.
COMPLEMENTS = {
    'stcn_egs_morning': ('cls_morning', 'wallstreetcn_breakfast'),
    'yicai_overnight': ('cls_morning', 'wallstreetcn_breakfast'),
    'zhitong_hk_morning': ('cls_hk_morning', 'wallstreetcn_breakfast'),
    'hkex_stock_connect_daily': ('eastmoney_hsgt', 'cls_southbound'),
    'ubs_southbound': ('eastmoney_hsgt', 'cls_southbound'),
    'hkex_news': ('cls_hk_morning', 'hkex_official_news'),
    'csia': ('trendforce', 'wscn_technology', 'cls_semiconductor_materials'),
    'semi': ('trendforce', 'wscn_technology', 'cls_semiconductor_materials'),
    **{sid: ('cls_innovative_drug', 'wscn_medicine') for sid in
       ('nmpa', 'cde', 'medcube', 'dxy_insight', 'fda', 'clinicaltrials')},
    'gov_policy_library': ('miit', 'nda'),
}


def apply_usage(sources):
    for source in sources:
        # FDA has a parser but repeated real TLS failures. Keep an explicit
        # diagnostic retry available, while ordinary scans use the curated set.
        automatic = source['collector_available'] and source['source_id'] != 'fda'
        source['usage_mode'] = ('automatic' if automatic else 'search_only'
            if source['kind'] == 'custom' else 'manual')
        source['search_enabled'] = source['usage_mode'] != 'manual'
    available = {s['source_id']: s for s in sources
                 if s['enabled'] and s['usage_mode'] == 'automatic'}
    for source in sources:
        source['alternatives'] = [
            {k: available[sid][k] for k in ('source_id', 'name', 'url')}
            for sid in COMPLEMENTS.get(source['source_id'], ()) if sid in available]
        source['alternative_note'] = (
            '补充同类公开资讯；原站公告、专业数据仍以原站为准。'
            if source['alternatives'] else '')
    return sources
