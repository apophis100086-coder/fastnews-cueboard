"""Offline synthetic coverage: same-publisher channels, API semantics, access policy."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from fastnews.adapters import DiscoveryError
from fastnews.config import ConfigError, load_config
from fastnews.live_sources import LiveAdapter, load_live_config, parse_listing
from fastnews.network import HTTPResult, NetworkError, SafeHTTPClient, reviewed_robots_soft_404
from fastnews.source_channels import with_channels
from fastnews.source_parsers import eastmoney_url


def response(body, url):
    return HTTPResult(url, 200, {'content-type':'application/json;charset=utf-8'},
                      (body if isinstance(body,str) else json.dumps(body)).encode(), '2026-09-16T08:00:00+00:00')


class SourceExpansionTests(unittest.TestCase):
    def stat(self, **changes):
        item = dict(TRADE_DATE='2026-09-15 00:00:00', MUTUAL_TYPE='006',
                    NET_DEAL_AMT=880.67, BUY_AMT=37744.46, SELL_AMT=36863.79)
        item.update(changes)
        return response({'success':True, 'result':{'data':[item]}}, eastmoney_url())

    def wscn(self, *, title='合成科技事件', channel='tmt', paid=False, stamp=1789531992, uri=None, anchor=True):
        url=uri or 'https://wallstreetcn.com/articles/123'
        resource=dict(title=title, uri=url, display_time=stamp, content_short='合成列表导语',is_paid=paid)
        data={'baseConfig':{'channel':channel},'items':[{'resource_type':'article','resource':resource}]}
        payload={'state':{'default':{'children':{'default':{'data':data}}}}}
        html=('<a href="/articles/123">'+title+'</a>' if anchor else '')+'<script>__SSR__ = '+json.dumps(payload)+'</script>'
        return response(html, 'https://wallstreetcn.com/news/'+channel)

    def test_stat_units_and_trade_date_never_become_publication_or_verified_fact(self):
        r=parse_listing('eastmoney_hsgt','eastmoney_southbound',self.stat())[0]
        self.assertIn('成交净买额8.81亿港元',r['lead'])
        self.assertIn('买入成交额377.44亿港元',r['lead'])
        self.assertIsNone(r['report_published_at']);self.assertIsNone(r['first_published_at'])
        self.assertEqual(r['data_period_end'],'2026-09-15');self.assertEqual(r['evidence_state'],'pending')
        self.assertFalse(r['attribution_chain'][0]['article_read'])
        self.assertEqual(json.loads(r['attribution_chain'][0]['raw_fields_json'])['NET_DEAL_AMT'],880.67)

    def test_negative_and_missing_amounts_preserved_without_zero_fill(self):
        r=parse_listing('eastmoney_hsgt','eastmoney_southbound',self.stat(NET_DEAL_AMT=-10020.87,BUY_AMT=None))[0]
        self.assertIn('-100.21亿港元',r['lead']);self.assertNotIn('买入成交额',r['lead'])
        self.assertEqual(parse_listing('eastmoney_hsgt','eastmoney_southbound',self.stat(NET_DEAL_AMT=None,BUY_AMT=None,SELL_AMT=None)),[])

    def test_wrong_direction_bad_dates_and_nonfinite_numbers_rejected(self):
        for changes in ({'MUTUAL_TYPE':'005'},{'TRADE_DATE':'2026-02-30 00:00:00'},
                        {'NET_DEAL_AMT':True},{'NET_DEAL_AMT':float('nan')}):
            with self.subTest(changes=changes),self.assertRaises(DiscoveryError):
                parse_listing('eastmoney_hsgt','eastmoney_southbound',self.stat(**changes))

    def test_statistics_window_uses_period_without_inventing_publication(self):
        class Client:
            stats={}
            def get(inner,*args,**kwargs):return self.stat()
        a=LiveAdapter(load_config(),client=Client())
        day=lambda x:datetime.fromisoformat(x).replace(tzinfo=timezone.utc)
        self.assertEqual(a.discover('eastmoney_hsgt',day('2026-09-16'),day('2026-09-17'),50).records,[])
        self.assertEqual(len(a.discover('eastmoney_hsgt',day('2026-09-14'),day('2026-09-16'),50).records),1)

    def test_wscn_uses_actual_clock_and_visible_anchor_not_pinned_date_zero(self):
        r=parse_listing('wscn_technology','wscn_technology',self.wscn())[0]
        self.assertEqual(r['report_published_at'],'2026-09-16T04:13:12+00:00')
        self.assertFalse(r['attribution_chain'][0]['article_read'])
        for kwargs in ({'paid':True},{'stamp':0},{'anchor':False},{'uri':'https://other.example.org/articles/123'}):
            self.assertEqual(parse_listing('wscn_technology','wscn_technology',self.wscn(**kwargs)),[])

    def test_wrong_ssr_channel_is_parse_error_and_absent_breakfast_is_empty(self):
        with self.assertRaises(DiscoveryError):parse_listing('wscn_technology','wscn_technology',self.wscn(channel='medicine'))
        self.assertEqual(parse_listing('wallstreetcn_breakfast','wscn_breakfast',self.wscn(channel='global')),[])
        self.assertEqual(len(parse_listing('wallstreetcn_breakfast','wscn_breakfast',self.wscn(channel='global',title='合成华尔街见闻早餐'))),1)

    def test_malformed_remote_objects_stay_isolated_discovery_errors(self):
        for payload in ([], None, {'success':True, 'result':None},
                        {'success':True, 'result':{'data':[None]}}):
            with self.subTest(payload=payload),self.assertRaisesRegex(DiscoveryError,'parse_error'):
                parse_listing('eastmoney_hsgt','eastmoney_southbound',response(payload,eastmoney_url()))
        for items in ([None], [{'resource_type':'article','resource':None}]):
            data={'baseConfig':{'channel':'tmt'},'items':items}
            payload={'state':{'default':{'children':{'default':{'data':data}}}}}
            html='<script>__SSR__ = '+json.dumps(payload)+'</script>'
            with self.subTest(items=items),self.assertRaisesRegex(DiscoveryError,'parse_error'):
                parse_listing('wscn_technology','wscn_technology',response(html,'https://wallstreetcn.com/news/tmt'))

    def test_official_rss_never_fetches_link_targets_or_conflates_announcements(self):
        r=response('<rss><channel><item><title>合成交易所新闻</title><link>https://www.hkex.com.hk/news/1</link><pubDate>Tue, 15 Sep 2026 10:00:00 +0800</pubDate></item></channel></rss>',
            'https://www.hkex.com.hk/Services/RSS-Feeds/News-Releases?sc_lang=zh-HK')
        item=parse_listing('hkex_official_news','official_rss',r)[0]
        self.assertEqual(item['source_id'],'hkex_official_news');self.assertEqual(item['evidence_state'],'pending')

    def test_only_observed_unknown_route_json_is_soft_404_no_global_relaxation(self):
        body='{"code":"200","message":"全局错误:请求url格式异常:/robots.txt"}'
        url='https://datacenter-web.eastmoney.com/robots.txt'
        self.assertTrue(reviewed_robots_soft_404(response(body,url)))
        for doc, address in [(body,'https://other.example.org/robots.txt'),('<html>verify you are human</html>',url),
                             ('{"code":"403","message":"Forbidden"}',url),('',url)]:
            self.assertFalse(reviewed_robots_soft_404(response(doc,address)))
        with tempfile.TemporaryDirectory() as folder:
            c=SafeHTTPClient(folder)
            with patch.object(c,'_read',return_value=response(body,url)):
                c.check_robots(eastmoney_url())
            self.assertEqual(c.access_reviews['https://datacenter-web.eastmoney.com']['robots_http_status'],200)
            c=SafeHTTPClient(folder)
            with patch.object(c,'_read',return_value=response('<html>challenge</html>',url)),self.assertRaisesRegex(NetworkError,'robots_invalid'):
                c.check_robots(eastmoney_url())

    def test_channels_are_additive_idempotent_and_keep_disabled_preference(self):
        base=load_config();before=deepcopy(base);expanded=with_channels(base)
        self.assertEqual(len(base['sources']),26);self.assertEqual(base,before)
        self.assertEqual(len(expanded['sources']),32)
        expanded['sources']['cls_innovative_drug']['enabled']=False
        self.assertEqual(expanded,with_channels(expanded))

    def test_channel_cannot_replace_original_or_silently_add_an_external_host(self):
        doc=yaml.safe_load(Path('config/source_channels.yaml').read_text(encoding='utf8'))
        for mutation in ('replace','external'):
            copy=deepcopy(doc)
            if mutation=='replace':copy['channels']['cls_morning']=copy['channels'].pop('cls_southbound')
            else:copy['channels']['cls_southbound']['url']='https://other.example.org/list'
            with tempfile.TemporaryDirectory() as folder:
                p=Path(folder)/'channels.yaml';p.write_text(yaml.safe_dump(copy,allow_unicode=True),encoding='utf8')
                with self.assertRaises(ConfigError):with_channels(load_config(),p)

    def test_live_api_cross_host_exception_is_exact_not_a_wildcard(self):
        doc=load_live_config();doc['sources']['eastmoney_hsgt']['listing_url']=eastmoney_url().replace('pageSize=10','pageSize=1000')
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'live.yaml';p.write_text(yaml.safe_dump(doc,allow_unicode=True),encoding='utf8')
            with self.assertRaises(ValueError):load_live_config(path=p)


if __name__ == '__main__':unittest.main()
