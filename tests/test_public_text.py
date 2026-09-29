"""Offline public-text provider policy, extraction and failure contract tests."""
import unittest

from fastnews.config import load_config
from fastnews.network import HTTPResult, NetworkError
from fastnews.providers import ProviderFailure
from fastnews.public_text import PublicTextProvider


URL='https://www.fda.gov/news-events/press-announcements/synthetic-test'
PARAGRAPH='Synthetic test: this public announcement describes a pending research observation. '


class Client:
    def __init__(self,content,*,url=URL,mime='text/html; charset=utf-8',error=None):
        self.response=HTTPResult(url,200,{'content-type':mime},content.encode(),'2026-09-15T11:00:00+00:00')
        self.calls=[]
        self.error=error
    def get(self,url,allowed_hosts=None):
        self.calls.append((url,allowed_hosts))
        if self.error:raise self.error
        return self.response


class PublicTextTests(unittest.TestCase):
    def setUp(self):
        self.config=load_config()

    def make(self,content=PARAGRAPH,**kwargs):
        client=Client(content,**kwargs)
        return PublicTextProvider(self.config,client=client),client

    def test_default_access_requires_explicit_true_policy(self):
        provider,client=self.make()
        for sid,url in [('miit','https://www.miit.gov.cn/test'),('nda','https://www.nda.gov.cn/test'),
                        ('trendforce','https://www.trendforce.cn/test'),('stcn_egs_morning','https://egs.stcn.com/test')]:
            with self.subTest(source=sid),self.assertRaisesRegex(ProviderFailure,'fulltext_access_not_approved'):
                provider.fetch(url,source_id=sid)
        self.assertEqual(client.calls,[])

    def test_truthy_string_is_not_permission(self):
        provider,client=self.make()
        provider.policies['fda']['fulltext_allowed']='true'
        with self.assertRaisesRegex(ProviderFailure,'fulltext_access_not_approved'):
            provider.fetch(URL,source_id='fda')
        self.assertEqual(client.calls,[])

    def test_fda_passes_exact_source_hosts_to_network(self):
        provider,client=self.make('<title>Synthetic FDA notice</title><main><p>'+PARAGRAPH+'</p></main>')
        result=provider.fetch(URL,source_id='fda')
        self.assertEqual(client.calls,[(URL,{'www.fda.gov'})])
        self.assertEqual(result.payload['source_id'],'fda')
        self.assertEqual(result.payload['title'],'Synthetic FDA notice')
        self.assertIsNone(result.payload['first_published_at'])
        self.assertEqual(result.payload['origin_role'],'unknown')
        self.assertFalse(result.payload['is_fixture'])
        self.assertNotIn('verified',result.payload)
        self.assertEqual(len(provider.receipts),1)

    def test_script_style_svg_and_noscript_are_excluded(self):
        html='<title>Test title</title><main><p>'+PARAGRAPH+'</p><script>INJECT_SCRIPT</script><style>INJECT_STYLE</style><svg>INJECT_SVG</svg><noscript>INJECT_NOSCRIPT</noscript></main>'
        provider,_=self.make(html)
        content=provider.fetch(URL,source_id='fda').payload['content']
        self.assertIn(PARAGRAPH.strip(),content)
        self.assertNotIn('INJECT_',content)

    def test_navigation_does_not_pad_short_declared_article(self):
        provider,_=self.make('<nav>'+PARAGRAPH*5+'</nav><main>Too short</main><footer>'+PARAGRAPH*3+'</footer>')
        with self.assertRaisesRegex(ProviderFailure,'readable_text_insufficient'):
            provider.fetch(URL,source_id='fda')

    def test_empty_article_does_not_fall_back_to_nav(self):
        provider,_=self.make('<nav>'+PARAGRAPH*5+'</nav><article></article>')
        with self.assertRaisesRegex(ProviderFailure,'readable_text_insufficient'):
            provider.fetch(URL,source_id='fda')

    def test_text_plain_preserves_angle_brackets(self):
        raw=PARAGRAPH+' A < B and <literal> remain part of the source.'
        provider,_=self.make(raw,mime='text/plain; charset=utf-8')
        result=provider.fetch(URL,source_id='fda')
        self.assertEqual(result.payload['content'],raw)
        self.assertEqual(result.payload['title'],'')

    def test_nontext_document_is_not_claimed_as_read(self):
        provider,_=self.make('%PDF synthetic',mime='application/pdf')
        with self.assertRaisesRegex(ProviderFailure,'document_format_not_supported'):
            provider.fetch(URL,source_id='fda')
        self.assertEqual(provider.receipts,[])

    def test_long_text_hits_context_budget(self):
        provider,_=self.make('x'*60001,mime='text/plain')
        with self.assertRaisesRegex(ProviderFailure,'text_context_too_large'):
            provider.fetch(URL,source_id='fda')

    def test_precise_login_subscription_and_challenge_markers(self):
        for marker in ['登录后阅读全文','订阅后阅读全文','Sign in to continue',
                       'Subscribe to read the full article','Verify you are human']:
            provider,_=self.make('<main>'+PARAGRAPH+marker+'</main>')
            with self.subTest(marker=marker),self.assertRaisesRegex(ProviderFailure,'restricted_fulltext'):
                provider.fetch(URL,source_id='fda')
            self.assertEqual(provider.receipts,[])

    def test_unrelated_login_navigation_does_not_deny_public_article(self):
        provider,_=self.make('<nav>Sign in</nav><article>'+PARAGRAPH+'</article>')
        self.assertIn(PARAGRAPH.strip(),provider.fetch(URL,source_id='fda').payload['content'])

    def test_offsite_urls_are_rejected_without_http(self):
        provider,client=self.make()
        with self.assertRaisesRegex(ProviderFailure,'one_off_source_requires_explicit_import'):
            provider.fetch('https://other.example.com/test',source_id='fda')
        self.assertEqual(client.calls,[])

    def test_redirect_outside_scope_is_rejected_defensively(self):
        provider,_=self.make(PARAGRAPH,url='https://other.example.com/test')
        with self.assertRaisesRegex(ProviderFailure,'redirect_outside_source_scope'):
            provider.fetch(URL,source_id='fda')

    def test_unsafe_url_obeys_provider_failure_contract(self):
        provider,client=self.make()
        with self.assertRaisesRegex(ProviderFailure,'unsafe_url'):
            provider.fetch('http://127.0.0.1/',source_id='fda')
        self.assertEqual(client.calls,[])

    def test_unknown_source_obeys_provider_failure_contract(self):
        provider,client=self.make()
        with self.assertRaisesRegex(ProviderFailure,'unknown_registered_source'):
            provider.fetch(URL,source_id='invented')
        self.assertEqual(client.calls,[])

    def test_network_failures_are_controlled_and_no_false_receipt(self):
        provider,_=self.make(error=NetworkError('robots_disallowed'))
        with self.assertRaisesRegex(ProviderFailure,'robots_disallowed') as raised:
            provider.fetch(URL,source_id='fda')
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(provider.receipts,[])

    def test_access_gap_is_local_and_reports_known_policy_without_http(self):
        from fastnews.budget import safe_error_code
        provider,client=self.make()
        self.assertIsNone(provider.access_gap(URL,source_id='fda'))
        self.assertEqual(provider.access_gap('https://www.miit.gov.cn/test',source_id='miit'),'fulltext_access_not_approved')
        self.assertEqual(provider.access_gap(URL,source_id='invented'),'unknown_registered_source')
        self.assertEqual(provider.access_gap('http://127.0.0.1/',source_id='fda'),'unsafe_url')
        self.assertEqual(client.calls,[])
        self.assertEqual(safe_error_code('fulltext_access_not_approved'),'fulltext_access_not_approved')


if __name__=='__main__':unittest.main()
