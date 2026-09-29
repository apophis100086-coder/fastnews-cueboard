"""Offline synthetic parser and public-network boundary tests (no HTTP calls)."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from fastnews.adapters import DiscoveryError
from fastnews.config import load_config
from fastnews.live_sources import LiveAdapter, load_live_config, parse_listing
from fastnews.network import HTTPResult, NetworkError, SafeHTTPClient, resolve_public, robots_allowed


def response(html, url='https://www.miit.gov.cn/'):
    return HTTPResult(url,200,{'content-type':'text/html;charset=utf-8'},html.encode(),
                      '2026-09-15T10:00:00+00:00')


def dated_li(day='2026-09-14', path='/synthetic.html',title='合成测试：电子信息政策公告'):
    return f'<ul><li><span>{day}</span><p><a href="{path}" title="{title}">{title}</a></p></li></ul>'


class FakeClient:
    def __init__(self,html):
        self.html=html
        self.stats={'network_requests':0,'cache_hits':0,'failed_requests':0}
    def get(self,url,allowed_hosts=None):
        return response(self.html,url)


class ParserTests(unittest.TestCase):
    def cls_column(self, column=1151, stamp=1789340400, href='/detail/123'):
        payload = {'props':{'pageProps':{'data':{'id':column,'articles':[{'article_id':123,
            'article_title':'【早报】合成测试宏观信息','article_brief':'①合成利率线索②合成科技线索',
            'article_time':stamp}]}}}}
        return response('<script id="__NEXT_DATA__" type="application/json">'+json.dumps(payload)+
            '</script><a href="'+href+'">合成晨报标题</a>','https://www.cls.cn/subject/'+str(column))

    def test_cls_public_columns_extract_only_metadata_and_exact_listing_link(self):
        for sid, column in [('cls_morning',1151),('cls_hk_morning',8705)]:
            item=parse_listing(sid,'cls_subject',self.cls_column(column))[0]
            self.assertEqual(item['url'],'https://www.cls.cn/detail/123')
            self.assertEqual(item['report_published_at'],'2026-09-13T23:00:00+00:00')
            self.assertIsNone(item['first_published_at'])
            self.assertFalse(item['attribution_chain'][0]['article_read'])
            self.assertEqual(item['evidence_state'],'pending')

    def test_cls_wrong_column_missing_anchor_or_malformed_clock_fail_visibly(self):
        for listing in [self.cls_column(999),self.cls_column(stamp='yesterday'),
                        self.cls_column(href='https://other.invalid/detail/123'),self.cls_column(stamp=0)]:
            with self.assertRaises(DiscoveryError):
                parse_listing('cls_morning','cls_subject',listing)

    def test_cls_missing_json_is_failure_instead_of_successful_zero(self):
        with self.assertRaisesRegex(DiscoveryError,'parse_error'):
            parse_listing('cls_morning','cls_subject',response('<html>维护中</html>','https://www.cls.cn/subject/1151'))

    def test_date_is_report_not_first_and_not_verified(self):
        item=parse_listing('miit','dated_li',response(dated_li()))[0]
        self.assertEqual(item['report_published_at'],'2026-09-14')
        self.assertIsNone(item['first_published_at'])
        self.assertFalse(item['is_fixture'])
        self.assertEqual(item['evidence_state'],'pending')
        self.assertFalse(item['primary_source_found'])
        self.assertFalse(item['attribution_chain'][0]['article_read'])

    def test_html_entities_and_source_url_preserved(self):
        item=parse_listing('miit','dated_li',response(dated_li(title='合成测试：政策 &amp; 产业')))[0]
        self.assertEqual(item['title'],'合成测试：政策 & 产业')
        self.assertEqual(item['attribution_chain'][0]['url'],'https://www.miit.gov.cn/')

    def test_duplicate_urls_collapsed(self):
        self.assertEqual(len(parse_listing('miit','dated_li',response(dated_li()*2))),1)

    def test_external_link_does_not_add_source(self):
        with self.assertRaisesRegex(DiscoveryError,'parse_error'):
            parse_listing('miit','dated_li',response(dated_li(path='https://other.example.com/news')))

    def test_no_dates_extracted_from_title_or_url(self):
        with self.assertRaisesRegex(DiscoveryError,'parse_error'):
            parse_listing('miit','dated_li',response('<li><a href="/20260914.html">合成2026-09-14公告</a></li>'))

    def test_invalid_date_is_not_manufactured(self):
        with self.assertRaisesRegex(DiscoveryError,'parse_error'):
            parse_listing('miit','dated_li',response(dated_li(day='2026-02-31')))

    def test_stcn_only_registered_morning_series(self):
        html='<a href="/news/1.html">【早知道】合成测试晨报</a><a href="/news/2.html">合成其他财经新闻</a>'
        items=parse_listing('stcn_egs_morning','stcn_morning',response(html,'https://egs.stcn.com/'))
        self.assertEqual(len(items),1)
        self.assertIsNone(items[0]['report_published_at'])

    def test_fda_uses_explicit_cms_report_clock_without_claiming_first_publication(self):
        html='<a href="/news-events/press-announcements/synthetic"><time datetime="2026-09-04T04:00:00Z">September 4, 2026</time> - Synthetic test drug announcement</a>'
        item=parse_listing('fda','fda',response(html,'https://www.fda.gov/'))[0]
        self.assertEqual(item['report_published_at'],'2026-09-04T04:00:00+00:00')
        self.assertIsNone(item['first_published_at'])
        self.assertEqual(item['attribution_chain'][0]['machine_datetime'],'2026-09-04T04:00:00Z')
        self.assertEqual(item['attribution_chain'][0]['raw_report_date'],'September 4, 2026')

    def test_trendforce_summary_remains_metadata(self):
        html='<div class="list-item"><h3><a href="/presscenter/news/synthetic.html">合成预测：存储价格或上涨</a></h3><h4>14 September 2026</h4><p>这是一条合成列表导语。</p></div>'
        item=parse_listing('trendforce','trendforce',response(html,'https://www.trendforce.cn/presscenter/news'))[0]
        self.assertEqual(item['lead'],'这是一条合成列表导语。')
        self.assertEqual(item['phase'],'unknown')
        self.assertEqual(item['kind'],'reported')
        self.assertEqual(item['evidence_state'],'pending')

    def test_broken_structure_is_failure_not_empty_success(self):
        with self.assertRaisesRegex(DiscoveryError,'parse_error'):
            parse_listing('miit','dated_li',response('<html>Site redesign</html>'))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.config=load_config()
        self.start=datetime(2026,9,13,tzinfo=timezone.utc)
        self.end=datetime(2026,9,15,tzinfo=timezone.utc)

    def test_in_window_and_incomplete_listing(self):
        adapter=LiveAdapter(self.config,client=FakeClient(dated_li()))
        page=adapter.discover('miit',self.start,self.end,50)
        self.assertEqual(len(page.records),1)
        self.assertFalse(page.complete)
        self.assertIsNone(page.resume)
        self.assertEqual(page.coverage_reason,'listing_scope_incomplete')

    def test_out_of_window_empty_still_reports_gap(self):
        adapter=LiveAdapter(self.config,client=FakeClient(dated_li(day='2025-01-01')))
        page=adapter.discover('miit',self.start,self.end,50)
        self.assertEqual(page.records,[])
        self.assertFalse(page.complete)

    def test_limit_resume_and_dataset_changes(self):
        client=FakeClient(dated_li(path='/one.html')+dated_li(path='/two.html'))
        adapter=LiveAdapter(self.config,client=client)
        first=adapter.discover('miit',self.start,self.end,1)
        second=adapter.discover('miit',self.start,self.end,1,first.resume)
        self.assertNotEqual(first.records[0]['url'],second.records[0]['url'])
        self.assertFalse(second.complete)
        self.assertIsNone(second.resume)
        client.html=dated_li(path='/new.html')+client.html
        changed=adapter.discover('miit',self.start,self.end,1,first.resume)
        self.assertTrue(changed.records[0]['url'].endswith('/new.html'))

    def test_unsupported_registered_source_is_not_fake_success(self):
        adapter=LiveAdapter(self.config,client=FakeClient(dated_li()))
        with self.assertRaisesRegex(DiscoveryError,'adapter_not_connected'):
            adapter.discover('nmpa',self.start,self.end,50)

    def test_known_site_terms_disable_automatic_metadata(self):
        adapter=LiveAdapter(self.config,client=FakeClient('<a href="/test">【早知道】合成测试</a>'))
        self.assertNotIn('stcn_egs_morning',adapter.supported_sources)
        self.assertNotIn('smm',adapter.supported_sources)
        with self.assertRaisesRegex(DiscoveryError,'source_terms_manual_only'):
            adapter.discover('stcn_egs_morning',self.start,self.end,50)

    def test_time_and_limit_validation(self):
        adapter=LiveAdapter(self.config,client=FakeClient(dated_li()))
        for start,end,limit in [(self.end,self.start,50),(self.start.replace(tzinfo=None),self.end,50),
                                (self.start,self.end,False),(self.start,self.end,0)]:
            with self.assertRaises(DiscoveryError):
                adapter.discover('miit',start,end,limit)

    def test_config_cannot_expand_source_registry(self):
        config=load_live_config(self.config)
        config['sources']['invented']=dict(config['sources']['miit'])
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.yaml'; path.write_text(json.dumps(config),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'already exist'):
                load_live_config(self.config,path)

    def test_duplicate_yaml_keys_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.yaml'; path.write_text('version: 1\nversion: 1\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'duplicate'):
                load_live_config(self.config,path)


class NetworkTests(unittest.TestCase):
    def test_private_literals_and_credentials_rejected_before_dns(self):
        for url in ['http://127.0.0.1/','http://169.254.169.254/','http://[::1]/',
                    'file:///etc/passwd','https://user:pass@www.example.com/',
                    'http://localhost/','http://0x7f000001/']:
            with self.subTest(url=url),patch('socket.getaddrinfo') as dns:
                with self.assertRaises(NetworkError): resolve_public(url)
                dns.assert_not_called()

    def test_nonpublic_dns_and_mixed_answers_rejected(self):
        for addresses in [['10.0.0.1'],['198.18.0.120'],['fdfe:dcba:9876::75'],['93.184.216.34','127.0.0.1']]:
            answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(a,443)) for a in addresses]
            with patch('socket.getaddrinfo',return_value=answers),self.assertRaisesRegex(NetworkError,'unsafe_dns'):
                resolve_public('https://www.example.com/')

    def test_public_dns_returns_fixed_connection_addresses(self):
        answer=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        with patch('socket.getaddrinfo',return_value=answer):
            self.assertEqual(resolve_public('https://www.example.com/')[1],['93.184.216.34'])

    def test_nonstandard_port_rejected(self):
        with self.assertRaisesRegex(NetworkError,'unsafe_port'):
            resolve_public('https://www.example.com:9090/')

    def test_robots_wildcard_longest_rule_and_agent_group(self):
        text='User-agent: *\nDisallow: /\nUser-agent: FastnewsMetadataBot\nDisallow: /*.pdf$\nDisallow: /private\nAllow: /private/public\n'
        agent=SafeHTTPClient.USER_AGENT
        self.assertTrue(robots_allowed(text,agent,'https://www.example.com/news'))
        self.assertFalse(robots_allowed(text,agent,'https://www.example.com/a.pdf'))
        self.assertTrue(robots_allowed(text,agent,'https://www.example.com/private/public/page'))
        self.assertFalse(robots_allowed(text,'OtherBot','https://www.example.com/news'))

    def test_robots_allow_tie_and_empty_disallow(self):
        self.assertTrue(robots_allowed('User-agent: *\nDisallow: /a\nAllow: /a','X','https://x.example/a'))
        self.assertTrue(robots_allowed('User-agent: *\nDisallow:','X','https://x.example/a'))

    def test_robots_403_does_not_fetch_target(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            with patch.object(client,'_resolve',side_effect=lambda u:(u,['93.184.216.34'])),patch.object(client,'_once',return_value=HTTPResult('https://www.example.com/robots.txt',403,{},b'', 'now')) as once:
                with self.assertRaisesRegex(NetworkError,'robots_http_403'):
                    client.get('https://www.example.com/news')
                self.assertEqual(once.call_count,1)

    def test_robots_404_preserves_missing_status(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            with patch.object(client,'_read',return_value=HTTPResult('https://www.example.com/robots.txt',404,{},b'', 'now')):
                client.check_robots('https://www.example.com/news')
            self.assertEqual(client.access_reviews['https://www.example.com']['status'],'robots_missing_terms_not_granted')

    def test_redirect_private_target_is_blocked_before_request(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            def resolve(url):
                if '127.0.0.1' in url: raise NetworkError('unsafe_url')
                return url,['93.184.216.34']
            with patch.object(client,'_resolve',side_effect=resolve),patch.object(client,'check_robots'),patch.object(client,'_once',return_value=HTTPResult('https://www.example.com/',302,{'location':'http://127.0.0.1/'},b'','now')) as once:
                with self.assertRaisesRegex(NetworkError,'unsafe_url'):client.get('https://www.example.com/')
                self.assertEqual(once.call_count,1)

    def test_redirect_outside_allowlist_is_blocked_before_robots(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            with patch.object(client,'_resolve',side_effect=lambda u:(u,['93.184.216.34'])),patch.object(client,'check_robots') as robots,patch.object(client,'_once',return_value=HTTPResult('https://www.example.com/',302,{'location':'https://other.example.com/'},b'','now')) as once:
                with self.assertRaisesRegex(NetworkError,'redirect_outside'):client.get('https://www.example.com/',allowed_hosts={'www.example.com'})
                self.assertEqual(once.call_count,1)
                self.assertEqual(robots.call_count,1)

    def test_network_config_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            for kwargs in [{'retries':3},{'timeout':60},{'rate_per_minute':11},{'max_bytes':6000000},{'max_redirects':9},{'timeout':True},{'dns_mode':'unsafe'}]:
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                    SafeHTTPClient(folder,**kwargs)

    def test_arbitrary_post_is_not_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            with self.assertRaisesRegex(NetworkError,'post_endpoint_not_reviewed'):
                client.post_json('https://www.example.com/delete',{})

    def test_readonly_post_parameters_are_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            client=SafeHTTPClient(folder)
            url='https://ncpscxx.moa.gov.cn/product/FarmDaily/selectDailyList'
            for payload in [{'pageSize':True,'currentPage':1},{'pageSize':1000,'currentPage':1},
                            {'pageSize':6,'currentPage':1,'delete':True}]:
                with self.assertRaisesRegex(NetworkError,'read_only_post_payload_invalid'):
                    client.post_json(url,payload)


if __name__=='__main__':unittest.main()
