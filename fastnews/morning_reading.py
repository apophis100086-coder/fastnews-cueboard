"""Seven natural days of cited context, separate from today's news."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .metadata import time_bounds, parse_instant

VERSION = 'morning-v2-seven-day'
TIMING = dict(deadline_seconds=480, collection_seconds=60, host_seconds=390, finalize_seconds=30)
DOMAINS = {
    'macro': ('宏观政策', ['政策','宏观','央行','财政','国务院']),
    'domestic_markets': ('国内市场', ['A股','沪深','收盘','股市','债市']),
    'global_markets': ('海外市场', ['美股','港股','海外','美联储','汇率']),
    'flows': ('资金', ['资金','南向','北向','增持','流入']),
    'technology': ('科技产业', ['科技','半导体','人工智能','AI','设备','云计算']),
    'medicine': ('医药', ['医药','药品','临床','医保','FDA']),
    'other_industries': ('其他重要行业', ['能源','农业','有色','消费','汽车','地产']),
}


def window(day, days=7):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError('晨报回看天数需为1—30个自然日')
    return (date.fromisoformat(day)-timedelta(days=days-1)).isoformat()


def schema(base):
    from .writing_contracts import obj, arr, S, enum
    result=deepcopy(base)
    result['properties'].update(
        timeline=arr(obj(theme=S,development=enum('new','ongoing','disputed'),summary=S,
            events=arr(obj(date=S,event=S,source_refs=arr(S))))),
        domain_coverage=arr(obj(domain=enum(*DOMAINS),status=enum('found','not_found','not_checked','blocked'),
            source_refs=arr(S),note=S)))
    result['required']=list(result['properties'])
    return result


def instructions():
    return (Path(__file__).resolve().parents[1]/'prompts/morning_reading.md').read_text(encoding='utf-8')+'\nINPUT_JSON:\n'


def validate(raw, payload, sources, trace):
    """References and dates are checked; semantic judgments remain host reports."""
    available={s['id']:s for s in sources}
    lo,hi=parse_instant(payload['window_start']),parse_instant(payload['news_cutoff'])
    timeline,omitted=[],[]
    if (not isinstance(raw.get('timeline',[]),list) or len(raw.get('timeline',[]))>60 or
        not isinstance(raw.get('domain_coverage',[]),list) or len(raw.get('domain_coverage',[]))>7):
        raise ValueError('晨报时间线或领域记录超过容量')
    for topic in raw.get('timeline',[]):
        if (not isinstance(topic,dict) or set(topic)!={'theme','development','summary','events'} or
            topic['development'] not in {'new','ongoing','disputed'} or
            any(not isinstance(topic[k],str) or not topic[k].strip() or len(topic[k])>600 for k in ('theme','summary')) or
            not isinstance(topic['events'],list) or len(topic['events'])>60):
            raise ValueError('晨报时间线结构无效')
        events=[]
        for entry in topic['events']:
            if (not isinstance(entry,dict) or set(entry)!={'date','event','source_refs'} or
                not isinstance(entry['event'],str) or not 1<=len(entry['event'])<=600 or
                not isinstance(entry['source_refs'],list) or not 1<=len(entry['source_refs'])<=4 or
                any(not isinstance(r,str) for r in entry['source_refs'])):
                raise ValueError('晨报时间线事件无效')
            try:
                start,end=time_bounds(entry['date'])
                refs=[available[r] for r in entry['source_refs']]
                valid=start is not None and len(entry['date'])==10 and start<=hi and end>=lo
                # An event date needs a dated source or an explicit date in its excerpt.
                from .writing_checks import date_present
                valid=valid and any(time_bounds(s.get('published_at'))[0] and
                    time_bounds(s['published_at'])[0].astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()==entry['date'] or
                    date_present(entry['date'],s.get('excerpt','')) for s in refs)
                valid=valid and all(time_bounds(s.get('published_at'))[0] is not None and
                    time_bounds(s['published_at'])[0]<=hi and time_bounds(s['published_at'])[1]>=lo for s in refs)
            except (ValueError,TypeError,KeyError):
                valid=False
            if not valid:
                omitted.append(dict(theme=topic['theme'],event=entry['event'],reason='timeline_date_or_reference_unverified'))
                continue
            events.append(dict(entry,sources=refs,judgment_basis='host_interpretation_cited_events'))
        if events:
            if topic['development'] in {'ongoing','disputed'} and len(events)<2:
                omitted.append(dict(theme=topic['theme'],reason='insufficient_events_for_development'))
                continue
            timeline.append(dict(topic,events=sorted(events,key=lambda e:e['date'])))
    from .search_audit import observed_queries
    queries=observed_queries(trace)
    coverage=[]
    supplied={}
    for row in raw.get('domain_coverage',[]):
        if (not isinstance(row,dict) or set(row)!={'domain','status','source_refs','note'} or
            row['domain'] not in DOMAINS or row['status'] not in {'found','not_found','not_checked','blocked'} or
            not isinstance(row['note'],str) or len(row['note'])>1000 or
            not isinstance(row['source_refs'],list) or any(not isinstance(r,str) for r in row['source_refs'])):
            raise ValueError('晨报领域覆盖记录无效')
        supplied[row['domain']]=row
    for key,(name,terms) in DOMAINS.items():
        row=supplied.get(key,dict(domain=key,status='not_checked',source_refs=[],note='未返回该领域的检查记录'))
        refs=[available[r] for r in row['source_refs'] if r in available]
        status=row['status']
        if status=='found' and not refs:
            status='not_checked'
        if status=='not_found' and not any(any(t.casefold() in q.casefold() for t in terms) for q in queries):
            status='not_checked'
        coverage.append(dict(row,name=name,status=status,sources=refs,
            basis='cited_material_and_observed_queries_not_exhaustiveness'))
    return dict(timeline=timeline,domain_coverage=coverage,timeline_audit=omitted,
        lookback_days=payload.get('lookback_days',7),reading_version=VERSION,
        today_scope=payload['day'],context_window_start=payload['window_start'])
