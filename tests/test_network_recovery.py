"""Transport recovery must not weaken the public-only/robots boundary."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastnews.network import HTTPResult, NetworkError, SafeHTTPClient


class ResolverRecoveryTests(unittest.TestCase):
    def client(self, folder):
        return SafeHTTPClient(folder, dns_mode='cloudflare_public')

    def reply(self, addresses=None, status=0, ttl=60):
        conn = Mock()
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read.return_value = json.dumps({
            'Status':status, 'Answer':[{'type':1,'data':ip,'TTL':ttl}
                                      for ip in (addresses or ['93.184.216.34'])]}).encode()
        return conn

    def test_failed_endpoint_falls_back_and_is_not_repeated_for_each_source(self):
        dead, good = Mock(), self.reply()
        dead.request.side_effect = TimeoutError()
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',
                side_effect=[dead,good,good]) as transport:
            client = self.client(folder)
            self.assertEqual(client._resolve('https://example.org/')[1], ['93.184.216.34'])
            client._resolve('https://another.example.org/')
            self.assertEqual([x.args[2] for x in transport.call_args_list], ['1.1.1.1','1.0.0.1','1.0.0.1'])
            self.assertTrue(all(x.args[0]=='cloudflare-dns.com' and x.args[4] for x in transport.call_args_list))
            self.assertEqual(client.stats['dns_endpoint_failures'],1)
            self.assertEqual(client.stats['dns_fallbacks'],2)
            dead.close.assert_called()

    def test_all_down_fails_and_cools_down_without_system_dns(self):
        dead = Mock(); dead.request.side_effect = TimeoutError()
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',return_value=dead) as transport, patch('socket.getaddrinfo') as system:
            client = self.client(folder)
            for host in ['example.org','another.example.org']:
                with self.assertRaisesRegex(NetworkError,'public_dns_failed'):
                    client._resolve('https://'+host+'/')
            self.assertEqual(transport.call_count,2)
            system.assert_not_called()

    def test_private_or_mixed_answers_fail_closed_without_resolver_fallback(self):
        for ips in [['127.0.0.1'],['198.18.0.162'],['93.184.216.34','10.0.0.1']]:
            with self.subTest(ips=ips), tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',return_value=self.reply(ips)) as transport:
                with self.assertRaisesRegex(NetworkError,'unsafe_dns_address'):
                    self.client(folder)._resolve('https://example.org/')
                self.assertEqual(transport.call_count,1)

    def test_nxdomain_is_not_mistaken_for_broken_infrastructure(self):
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',return_value=self.reply(status=3)) as transport:
            with self.assertRaisesRegex(NetworkError,'dns_failed'):
                self.client(folder)._resolve('https://example.org/')
            self.assertEqual(transport.call_count,1)

    def test_cache_respects_dns_ttl(self):
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',return_value=self.reply(ttl=2)) as transport, patch('fastnews.network.time.monotonic',return_value=100) as clock:
            client=self.client(folder)
            client._resolve('https://example.org/a'); client._resolve('https://example.org/b')
            self.assertEqual(transport.call_count,1)
            clock.return_value=103
            client._resolve('https://example.org/c')
            self.assertEqual(transport.call_count,2)

    def test_malformed_response_isolated_to_resolver(self):
        bad=self.reply(); bad.getresponse.return_value.read.return_value=b'<html>error</html>'
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',side_effect=[bad,self.reply()]):
            self.assertEqual(self.client(folder)._resolve('https://example.org/')[1],['93.184.216.34'])

    def test_regional_resolver_has_verified_host_and_public_pinned_address(self):
        with tempfile.TemporaryDirectory() as folder, patch('fastnews.network._PinnedHTTP',return_value=self.reply()) as transport:
            SafeHTTPClient(folder,dns_mode='public_fallback')._resolve('https://example.org/')
            self.assertEqual(transport.call_args.args,('dns.alidns.com',443,'223.5.5.5',4,True))

    def test_transient_tls_retry_uses_next_address_without_repeating_restrictions(self):
        with tempfile.TemporaryDirectory() as folder:
            client=self.client(folder)
            with patch.object(client,'_resolve',side_effect=lambda u:(u,['93.184.216.34'])), patch.object(client,'check_robots'), patch.object(client,'_once',side_effect=[NetworkError('tls_failed'),HTTPResult('https://example.org/',200,{},b'listing','now')]) as fetch:
                client.get('https://example.org/')
                self.assertEqual([c.kwargs['address_index'] for c in fetch.call_args_list],[0,1])
            for error in ['tls_certificate_invalid','unsafe_dns_address','robots_disallowed']:
                with patch.object(client,'_resolve',side_effect=lambda u:(u,['93.184.216.34'])), patch.object(client,'check_robots'), patch.object(client,'_once',side_effect=NetworkError(error)) as fetch:
                    with self.assertRaisesRegex(NetworkError,error):client.get('https://example.org/')
                    self.assertEqual(fetch.call_count,1)

if __name__=='__main__':unittest.main()
