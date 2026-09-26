"""Bounded, public, cutoff-frozen product references and publication memory.

Private imports never become host input through this module. References are
background evidence, not automatically selected news or current holdings.
"""
import json

from . import editorial as ed
from .metadata import normalize_url

VERSION = 'public-product-context-v1'


def public_context(conn, edition):
    references, history, gaps = [], [], []
    seen = set()
    snapshots = sorted(ed._snapshots(conn, edition),
                       key=lambda s: (s['data_as_of'], s['published_at'], s['snapshot_id']), reverse=True)
    for item in snapshots:
        if item['product_code'] != edition['product_code'] or item['snapshot_type'] in seen:
            continue
        for ev in item['evidence']:
            if ev.get('privacy_class') != 'public' or not ev.get('source_url'):
                continue
            if ev.get('source_type') in {'search_snippet', 'user_text'}:
                continue
            # Syntax validation only; any later retrieval uses the safe reader.
            url = normalize_url(ev['source_url'])
            references.append(dict(id=item['snapshot_id'], title=item['title'],
                snapshot_type=item['snapshot_type'], data_as_of=item['data_as_of'],
                published_at=item['published_at'], effective_at=item.get('effective_at'),
                source_url=url, excerpt=ev['excerpt'], evidence_id=ev['evidence_id'],
                source_type=ev['source_type'], source_name=ev.get('declared_source'),
                document_id=item['document_id'], structure_status=item['structure_status'],
                purpose='dated_product_background_not_current_news'))
            seen.add(item['snapshot_type'])
            break
        if len(references) == 3:
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
    return dict(version=VERSION, references=references, published_history=history, gaps=gaps)
