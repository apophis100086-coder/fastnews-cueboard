"""Compare frozen retrieval with separately prepared event answers after a run.

No search, model call or business database write. Matching is deliberately
literal (source URLs or explicit aliases); a human adjudicates semantic gaps.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def normalized(value):
    return ''.join(str(value).casefold().split())


def compare(report, answers):
    if report.get('news_cutoff') != answers.get('news_cutoff') or not report.get('news_cutoff'):
        raise ValueError('检索结果和重点事件清单必须使用同一冻结截点')
    events = answers.get('events', [])
    if not events or len({e.get('event_id') for e in events}) != len(events):
        raise ValueError('需有非空、事件编号唯一的独立清单')
    items = report.get('items', [])
    matched, missing = [], []
    item_hits = Counter()
    for event in events:
        aliases = event.get('aliases', [])
        urls = set(event.get('source_urls', []))
        if not event.get('event_id') or not (urls or aliases):
            raise ValueError('事件需有编号及可核对的出处或明确别名')
        hits = []
        for index, item in enumerate(items):
            text = normalized(item.get('title', '') + ' ' + item.get('summary', ''))
            sources = {s.get('url') for s in item.get('sources', [])}
            if urls & sources or any(normalized(a) in text for a in aliases if normalized(a)):
                hits.append(index)
                item_hits[index] += 1
        if hits:
            matched.append(dict(event_id=event['event_id'], item_indices=hits))
        else:
            missing.append(dict(event_id=event['event_id'], title=event.get('title'), priority=event.get('priority','normal')))
    duplicates = [m for m in matched if len(m['item_indices']) > 1]
    return dict(status='needs_gap_review' if missing or duplicates else 'literal_matches_complete_pending_human_check',
        matching='literal_urls_or_explicit_aliases_not_semantic_verification',
        news_cutoff=report['news_cutoff'],expected=len(events),matched=len(matched),recall=len(matched)/len(events),
        missing=missing,duplicate_candidates=duplicates,
        ambiguous_item_indices=[i for i,n in item_hits.items() if n>1],
        coverage_gaps=report.get('gaps', []),unmatched_item_indices=[i for i in range(len(items)) if not item_hits[i]])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--answers',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise ValueError('保留旧验收记录；复验使用新的输出文件')
    report_raw=a.report.read_bytes();answer_raw=a.answers.read_bytes()
    result=compare(json.loads(report_raw),json.loads(answer_raw))
    result.update(report_sha256=hashlib.sha256(report_raw).hexdigest(),answers_sha256=hashlib.sha256(answer_raw).hexdigest())
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
