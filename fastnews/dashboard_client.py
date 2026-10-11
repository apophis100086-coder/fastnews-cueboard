"""Workspace-bound local HTTP client for the conversation-to-dashboard bridge."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardClient:
    def __init__(self, url, database):
        self.url = url.rstrip('/') + '/'
        self.database = os.path.normcase(os.path.abspath(database))
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.state = self.call('state')
        actual = self.state.get('workspace_id')
        if not isinstance(actual, str) or os.path.normcase(os.path.abspath(actual)) != self.database:
            raise ValueError('当前端口不属于这个项目，请启动当前项目的看板')

    def call(self, path, body=None):
        headers = {}
        raw = None
        if body is not None:
            headers = {'Content-Type': 'application/json',
                       'X-Fastnews-CSRF': self.state['csrf_token']}
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        try:
            with self.opener.open(Request(self.url + 'api/' + path, data=raw,
                                         headers=headers), timeout=15) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                message = json.load(exc).get('error')
            except (ValueError, OSError):
                message = None
            raise ValueError(message or '看板请求未成功，请检查当前项目服务') from None

    def products(self):
        return {key: self.state[key] for key in
                ('products', 'default_day', 'default_cutoff', 'reader_profile')}

    def start(self, kind, host, product=None, date=None, cutoff=None, keywords=None,
              profile='default'):
        body = dict(kind=kind, host=host, reader_profile=profile)
        if product is not None:
            matches = [p for p in self.state['products']
                       if product in (p['code'], p['name'])]
            if len(matches) != 1:
                raise ValueError('产品未能唯一对应，请运行 products 查看代码后重试')
            body['product_code'] = matches[0]['code']
        if date:
            body['date'] = date
        if cutoff:
            body['news_cutoff'] = cutoff
        if keywords:
            body['extra_keywords'] = keywords
        result = self.call('conversation-start', body)
        result['dashboard_url'] = self.url
        return result

    def result(self, job_id):
        job = self.call('job?id=' + quote(job_id))
        result = {key: job.get(key) for key in
                  ('job_id', 'status', 'action', 'edition_id', 'product_code', 'error')}
        result['dashboard_url'] = self.url
        result['progress'] = job.get('progress', [])[-1:]
        result['saved'] = False
        if job.get('status') != 'completed':
            return result
        payload = job.get('result') or {}
        report = payload.get('reading_report') if job['action'] == 'research' else payload
        if not isinstance(report, dict) or not report.get('report_id'):
            return result
        profile = quote(job.get('reader_profile') or 'default')
        if job.get('edition_id'):
            view = self.call('edition?id=' + quote(job['edition_id']) + '&profile=' + profile)
            runs = (view.get('product_clues') or {}).get('runs') or []
            persisted_ids = {r.get('report_id') for r in runs}
        else:
            view = self.call('daily?date=' + quote(job['date']) + '&profile=' + profile)
            persisted_ids = set()
        if view.get('reading_report'):
            persisted_ids.add(view['reading_report'].get('report_id'))
        result.update(report_id=report['report_id'], saved=report['report_id'] in persisted_ids,
                      item_count=len((report.get('output') or {}).get('items') or []),
                      report_status=report.get('status'))
        return result
