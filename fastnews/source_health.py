"""User-facing source health, distinct from registration and historic coverage."""
from datetime import datetime
import json


def _clock(value):
    try:
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if value else 0
    except (ValueError,TypeError):
        return 0


def describe(source, observation=None):
    observed = observation or {}
    code = observed.get('error_code') or ''
    status = observed.get('status')
    result = {'checked_at':observed.get('checked_at'), 'last_success_at':source.get('last_success_at'),
              'error_code':code or None, 'items_seen':observed.get('items_seen'),
              'operation':observed.get('operation'), 'retryable':False}
    if not source.get('enabled',True):
        kind,title,note = 'disabled','已停用','当前方案不采集此来源，可在来源设置中重新启用。'
    elif source.get('automation_policy') == 'manual_only':
        kind,title,note = 'restricted','需授权或手工材料','站点条款限制自动采集；可打开原站阅读，或导入有权使用的材料。'
    elif not source.get('collector_available'):
        kind,title,note = 'not_connected','尚未接入采集器','已登记网址，可在产品检索中查找公开线索；专用列表采集尚未实现。'
        if code.startswith(('robots_','unsafe_')) or code in {'access_challenge','http_401','http_403','http_412','http_429','tls_certificate_invalid'}:
            kind,title,note = 'restricted','入口访问受限','最近实测未通过站点访问规则、验证或安全检查；采集器也尚未接通。保留原站阅读或授权材料路径。'
    elif code in {'adapter_not_connected','fixture_not_provided'} or not status:
        kind,title,note = 'pending','已适配，待检查','已有列表读取方式，尚无当前采集结果。'
    elif code in {'public_dns_failed','dns_failed','timeout','connection_failed','tls_failed'}:
        kind,title,note = 'network_error','连接暂时失败',{
            'public_dns_failed':'公共域名解析服务不可用，请稍后重试；尚未连接到该新闻网站。',
            'dns_failed':'该网站域名没有返回可用地址，请检查网址或稍后重试。',
            'timeout':'连接或响应超时，可稍后重试；已存条目仍可使用。',
            'connection_failed':'网络连接中断，可稍后重试；已存条目仍可使用。',
            'tls_failed':'安全连接握手未完成，可稍后重试；尚不能据此判断是反爬限制。'}[code]
        result['retryable'] = True
    elif code.startswith(('robots_','unsafe_')) or code in {'source_terms_manual_only','access_challenge','http_401','http_403','http_412','http_429','tls_certificate_invalid','https_downgrade','redirect_outside_allowed_hosts'}:
        kind,title,note = 'restricted','自动访问受限','站点访问规则、验证或安全检查未通过。保留原站入口，不自动绕过限制。'
    elif code.startswith(('parse_','invalid_')):
        kind,title,note = 'parser_error','页面解析待修复','已到达网站，但页面格式与采集规则不匹配，需要更新解析器。'
    elif code:
        kind,title,note = 'failed','本次读取未完成','来源没有返回可用列表，可打开原站查看；技术原因见详情。'
        result['retryable'] = code.startswith('http_5')
    elif status == 'empty' or (status in {'success','partial','read'} and observed.get('items_seen') == 0):
        kind,title,note = 'empty','已连接，窗口内无条目','列表读取正常，本次日期窗口未匹配到条目；不表示当天没有新闻。'
    elif status in {'success','partial','truncated','read'}:
        kind,title,note = 'ready','列表可读取','最近读取成功；仅覆盖当前公开列表，条目仍需核对。'
    else:
        kind,title,note = 'pending','待检查','尚未取得当前采集结果。'
    return dict(result,kind=kind,title=title,note=note)


def attach_health(conn, sources, summary):
    latest = {}
    def add(sid, row):
        # An unimplemented/disabled adapter made no network attempt. It must
        # not erase an earlier real access check (for example robots refusal).
        if row.get('status') in {'unavailable','skipped_disabled','budget_stopped','disabled'}:
            return
        if _clock(row.get('checked_at')) >= _clock(latest.get(sid,{}).get('checked_at')):
            latest[sid] = row

    for row in summary['sources']:
        run = row.get('last_run')
        if run:
            add(row['source_id'],dict(run,checked_at=run.get('finished_at') or run.get('started_at'),operation='scan'))
    probe = summary.get('latest_probe') or {}
    for row in probe.get('sources',[]):
        add(row['source_id'],dict(status=row.get('run_status'),error_code=row.get('error_code'),
                                 items_seen=row.get('records_parsed'),checked_at=row.get('last_probe_at'),operation='probe'))
    # Research does not advance a morning cursor, but is still an actual source
    # attempt. Include it so an old morning failure cannot mask newer recovery.
    for run in conn.execute('SELECT finished_at,report_json FROM fn_research_runs WHERE finished_at IS NOT NULL ORDER BY rowid DESC LIMIT 100'):
        report=json.loads(run['report_json']) if run['report_json'] else {}
        for row in (report.get('fixed_source_discovery') or {}).get('sources',[]):
            if row.get('status') in {'disabled','budget_stopped'}:
                continue
            add(row['source_id'],dict(row,checked_at=run['finished_at'],operation='product'))
    for source in sources:
        source['health'] = describe(source,latest.get(source['source_id']))
    by_id = {s['source_id']:s for s in sources}
    counts = {}
    for row in summary['sources']:
        source=by_id[row['source_id']]
        row['health']=source['health']
        kind=row['health']['kind']
        counts[kind]=counts.get(kind,0)+1
    summary['health_counts']=counts
    summary['adapted_sources']=sum(s['collector_available'] and s['enabled'] for s in sources)
    summary['currently_readable']=counts.get('ready',0)+counts.get('empty',0)
