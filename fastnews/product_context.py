"""Bounded, public, cutoff-frozen product references and publication memory.

Private imports never become host input through this module. References are
background evidence, not automatically selected news or current holdings.
"""
import json
import re

from . import editorial as ed
from .metadata import normalize_url
from .writing_checks import within_cutoff

VERSION = 'public-product-context-v2-writing-scope'


def writing_examples(conn, edition):
    """Complete public examples, separate from dated facts and frozen on use.

    Publication memory remains edition-specific. Style examples may come from
    another product, but never from a private, partial or future publication.
    """
    candidates=[]
    technology={'588090','588710','513130'}
    for row in conn.execute('SELECT h.data_json,d.content,d.privacy_class FROM fn_history h JOIN fn_documents d USING(document_id)'):
        item=json.loads(row['data_json'])
        if row['privacy_class']!='public' or item.get('completeness')!='full_text' or not row['content']:
            continue
        if not item.get('source_url') or not within_cutoff(item['publication_time'],edition['news_cutoff']):
            continue
        code=item['product_code']; same=code==edition['product_code']
        related=code in technology and edition['product_code'] in technology
        candidates.append((same,related,item['publication_time'],item['history_doc_id'],dict(
            id=item['history_doc_id'],title=item['title'],product_code=code,
            published_at=item['publication_time'],url=normalize_url(item['source_url']),
            text=row['content'],complete=True,purpose='style_only_not_current_facts',
            selection_reason='same_product' if same else 'related_product' if related else 'public_house_style')))
    unique=[]; seen=set()
    for *_,value in sorted(candidates,key=lambda c:c[:4],reverse=True):
        if value['text'] in seen:continue
        seen.add(value['text']);unique.append(value)
        if len(unique)==2:break
    return unique


def public_context(conn, edition):
    references, history, gaps = [], [], []
    writing_candidates={}
    seen = set()
    snapshots = sorted(ed._snapshots(conn, edition),
                       key=lambda s: (s['data_as_of'], s['published_at'], s['snapshot_id']), reverse=True)
    for item in snapshots:
        if item['product_code'] != edition['product_code']:
            continue
        for ev in item['evidence']:
            if ev.get('privacy_class') != 'public' or not ev.get('source_url'):
                continue
            if ev.get('source_type') in {'search_snippet', 'user_text'}:
                continue
            # Syntax validation only; any later retrieval uses the safe reader.
            url = normalize_url(ev['source_url'])
            ref=dict(id=item['snapshot_id'], title=item['title'],
                snapshot_type=item['snapshot_type'], data_as_of=item['data_as_of'],
                published_at=item['published_at'], effective_at=item.get('effective_at'),
                source_url=url, excerpt=ev['excerpt'], evidence_id=ev['evidence_id'],
                source_type=ev['source_type'], source_name=ev.get('declared_source'),
                document_id=item['document_id'], structure_status=item['structure_status'],
                purpose='dated_product_background_not_current_news')
            if item['snapshot_type'] not in seen and len(references)<3:
                references.append(ref)
                seen.add(item['snapshot_type'])
            if item['snapshot_type'] in {'product_identity','business_segment'} and not re.search(r'定期报告|季度报告|第.?季|中期报告|年度报告|年报|季报|20\d{2}\s*[Qq][1-4]',item['title']):
                writing_candidates.setdefault(item['snapshot_type'],ref)
            break
    if not references:
        gaps.append('本期没有可传给宿主的公开产品依据；关键词不是持仓证明')
    if not seen.intersection({'index_constituents', 'fund_holdings', 'target_fund_holdings'}):
        gaps.append('缺少本期可用的有日期成份或持仓资料，不能给出当前权重或宣称新闻主体属于持仓')
    rows = []
    for hid in edition.get('history_doc_ids', []):
        row = conn.execute('''SELECT h.data_json,d.content,d.privacy_class FROM fn_history h
            JOIN fn_documents d USING(document_id) WHERE h.history_doc_id=?''', (hid,)).fetchone()
        if not row or row['privacy_class'] != 'public':
            continue
        entry = json.loads(row['data_json'])
        if entry['product_code'] != edition['product_code'] or not entry.get('source_url'):
            continue
        rows.append((entry, row['content']))
    for item, body in sorted(rows, key=lambda r: (r[0]['publication_time'], r[0]['history_doc_id']), reverse=True)[:2]:
        history.append(dict(id=item['history_doc_id'], title=item['title'],
            publication_time=item['publication_time'], completeness=item['completeness'],
            source_url=normalize_url(item['source_url']),
            excerpt='' if item['completeness'] == 'title_only' else body[:700],
            excerpt_truncated=item['completeness'] != 'title_only' and len(body) > 700,
            purpose='prior_publication_only_not_evidence_for_new_claims'))
    if not history:
        gaps.append('本期没有可用的公开已发稿记录，不能声称已排除与历史稿重复')
    writing_references=[dict(r,purpose='product_identity_and_scope_only_not_current_quantities')
        for r in writing_candidates.values()]
    excluded=[]
    for ref in references:
        quarterly=bool(re.search(r'定期报告|季度报告|第.?季|中期报告|年度报告|年报|季报|20\d{2}\s*[Qq][1-4]',ref['title']))
        internal=ref['snapshot_type'] not in {'product_identity','business_segment'}
        if quarterly or internal:
            excluded.append(dict(id=ref['id'],title=ref['title'],reason=
                'periodic_report_not_current_product_description' if quarterly else 'internal_relationship_verification'))
    if not writing_references:
        gaps.append('缺少可用于本期正文的产品定位与覆盖资料；旧定期报告和指数资料仅供内部核对')
    return dict(version=VERSION, references=references, writing_references=writing_references,
        excluded_from_writing=excluded,published_history=history,gaps=gaps)
