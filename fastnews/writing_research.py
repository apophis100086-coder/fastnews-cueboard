"""Supplemental discoveries remain proposals until the outline is confirmed."""
from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo
import re

from .metadata import normalize_url, parse_instant, time_bounds
from .source_access import url_policy
from .writing_contracts import OUTLINE, OUTLINE_RESEARCH


def research_brief(snapshot):
    cutoff=parse_instant(snapshot['news_cutoff']).astimezone(ZoneInfo('Asia/Shanghai'))
    return dict(enabled=True,window_start=(cutoff-timedelta(days=6)).date().isoformat(),
        news_cutoff=snapshot['news_cutoff'],max_queries=8,max_new_sources=6,
        max_pages=snapshot['rules']['max_pages'],
        source_constraints=deepcopy(snapshot['source_constraints']),
        public_search_anchors=[s.get('title') or s.get('name','') for s in snapshot['sources']
            if s.get('identity') not in {'user_document','user_text','manual_note'}
            and not any(m['id']==s['material_id'] and m['origin'] in {'user_material','manual_note'}
                        for m in snapshot['materials'])],
        product_name=snapshot['product_name'],product_code=snapshot['product_code'])


def stage_discoveries(raw,snapshot):
    from .writing import _validate_shape
    _validate_shape(raw,OUTLINE_RESEARCH)
    discoveries=raw['supplemental_sources']
    if len(discoveries)>6:
        raise ValueError('框架补搜最多补充6份原始出处')
    materials={m['id'] for m in snapshot['materials']}
    sources={s['id'] for s in snapshot['sources']}
    urls={normalize_url(s['url']) for s in snapshot['sources'] if s.get('url')}
    staged=[]
    for item in discoveries:
        mid,sid=item['material_id'],item['source_id']
        if (not re.fullmatch(r'news_[A-Za-z0-9_-]{1,60}',mid) or
            not re.fullmatch(r'news_src_[A-Za-z0-9_-]{1,60}',sid) or mid in materials or sid in sources):
            raise ValueError('补搜材料或来源编号无效、重复，不能覆盖已选材料')
        url=normalize_url(item['url'])
        if url in urls:
            raise ValueError('补搜返回重复网址，请复用已有来源编号')
        if not item['title'].strip() or not item['publisher'].strip() or not item['relation'].strip():
            raise ValueError('补搜资料缺少原文标题、发布机构或与主线的关系')
        if len(item['excerpt'])>5000 or len(item['relation'])>1000:
            raise ValueError('补搜资料摘录超出单条预算')
        access=url_policy(url,snapshot['source_constraints'])
        materials.add(mid);sources.add(sid);urls.add(url)
        snapshot['materials'].append(dict(id=mid,title=item['title'],summary=item['excerpt'][:600],
            relation=item['relation'],origin='outline_supplement',must_include=False))
        # A model-provided quote is only a hint until the program obtains the page.
        source=dict(id=sid,material_id=mid,title=item['title'],name=item['publisher'],url=url,
            published_at=item['published_at'],text='',research_hint=item['excerpt'],
            text_observed=False,date_observed=False,identity='search_discovery',source_id=None,
            error=None if access['host_read_allowed'] else 'source_restricted')
        snapshot['sources'].append(source);staged.append(sid)
    snapshot['supplemental_research']=dict(enabled=True,status='completed',
        summary=raw['research_summary'],window=research_brief(snapshot),source_ids=staged,
        approval='pending_outline_confirmation')
    return {key:deepcopy(raw[key]) for key in OUTLINE['properties']},staged


def finalize_discoveries(payload,snapshot,added):
    """Keep original model metadata in the ledger, bind dates only when consistent."""
    from .writing_checks import within_cutoff
    start=research_brief(snapshot)['window_start']
    for source in snapshot['sources']:
        if source['id'] not in added:
            continue
        published=source.get('published_at')
        observed=bool(source.get('date_observed'))
        source['news_freshness']='date_unverified'
        if observed:
            instant=time_bounds(published)[0].astimezone(ZoneInfo('Asia/Shanghai'))
            source['news_freshness']=('outside_cutoff' if not within_cutoff(published,snapshot['news_cutoff'])
                else 'background' if instant.date().isoformat()<start else 'recent')
            for claim in payload['claims']:
                # A model may return the visible day while metadata provides an
                # exact timestamp. Match the day, retaining the stricter cutoff.
                if claim['source_id']==source['id'] and claim['published_at']==instant.date().isoformat():
                    claim['published_at']=published
    info=snapshot['supplemental_research']
    info['verified_sources']=sum(s['text_observed'] for s in snapshot['sources'] if s['id'] in added)
    incomplete=any(not s['text_observed'] or s.get('news_freshness') in {'outside_cutoff','date_unverified'}
                   for s in snapshot['sources'] if s['id'] in added)
    info['status']='partial' if incomplete else 'completed'
