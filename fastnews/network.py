"""Small public-only, robots-aware HTTP reader. No cookies, proxy or browser bypass."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .metadata import MetadataError, normalize_url


class NetworkError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def robots_allowed(document, user_agent, url):
    """Robots wildcard/end-anchor rules with longest-match and Allow tie-break."""
    groups, agents, rules = [], [], []
    for line in document.splitlines():
        line = line.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, value = (part.strip() for part in line.split(':',1))
        key = key.lower()
        if key == 'user-agent':
            if rules:
                groups.append((agents,rules)); agents,rules = [],[]
            agents.append(value.lower())
        elif agents and key in {'allow','disallow','crawl-delay','request-rate'}:
            rules.append((key,value))
    if agents:
        groups.append((agents,rules))
    selected, specificity = [], -1
    for agents, rules in groups:
        matches = [0 if agent == '*' else len(agent) for agent in agents
                   if agent == '*' or agent in user_agent.lower()]
        if not matches:
            continue
        score = max(matches)
        if score > specificity:
            selected, specificity = list(rules), score
        elif score == specificity:
            selected.extend(rules)
    parts = urlsplit(url)
    path = parts.path + ('?'+parts.query if parts.query else '')
    matches = []
    for key, pattern in selected:
        if key not in {'allow','disallow'} or not pattern:
            continue
        anchored = pattern.endswith('$')
        literal = pattern[:-1] if anchored else pattern
        expression = '^'+re.escape(literal).replace(r'\*','.*')+('$' if anchored else '')
        if re.search(expression,path):
            matches.append((len(literal.replace('*','')),key == 'allow'))
    return max(matches)[1] if matches else True


def reviewed_robots_soft_404(result):
    """This public data API returns its generic unknown-route JSON with HTTP 200.

    Treat only the observed exact /robots.txt response as an absent resource.
    HTML challenges, blank pages, other hosts and any changed response still fail
    closed. This grants no license and never substitutes for an actual denial.
    """
    if result.url != 'https://datacenter-web.eastmoney.com/robots.txt' or result.status != 200:
        return False
    return result.text.strip() == '{"code":"200","message":"全局错误:请求url格式异常:/robots.txt"}'


@dataclass
class HTTPResult:
    url: str
    status: int
    headers: dict
    body: bytes
    retrieved_at: str
    from_cache: bool = False

    @property
    def text(self):
        mime = self.headers.get('content-type', '')
        charset = re.search(r'charset=["\']?([\w-]+)', mime, re.I)
        if not charset:
            charset = re.search(rb'charset=["\']?([\w-]+)', self.body[:4096], re.I)
        encoding = charset.group(1) if charset else 'utf-8'
        if isinstance(encoding, bytes):
            encoding = encoding.decode('ascii', 'replace')
        try:
            return self.body.decode(encoding, 'replace')
        except LookupError:
            return self.body.decode('utf-8', 'replace')


def resolve_public(url):
    """Validate all DNS answers; the caller connects to the returned IP directly."""
    try:
        url = normalize_url(url)
        parts = urlsplit(url)
        if parts.port not in {None, 80, 443}:
            raise NetworkError('unsafe_port')
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)})
        if not addresses:
            raise NetworkError('dns_failed')
        for literal in addresses:
            address = ipaddress.ip_address(literal)
            if (not address.is_global or address.is_multicast or address.is_reserved
                    or address.is_unspecified or address.is_loopback):
                raise NetworkError('unsafe_dns_address')
        return url, addresses
    except MetadataError:
        raise NetworkError('unsafe_url') from None
    except socket.gaierror:
        raise NetworkError('dns_failed') from None


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout, secure):
        super().__init__(host, port=port, timeout=timeout)
        self.ip, self.secure = ip, secure

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), self.timeout)
        peer = ipaddress.ip_address(self.sock.getpeername()[0])
        if not peer.is_global or peer.is_multicast or peer.is_reserved:
            self.sock.close()
            raise NetworkError('unsafe_connected_address')
        if self.secure:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)


class SafeHTTPClient:
    USER_AGENT = 'FastnewsMetadataBot/0.2 (public metadata only; no login)'
    # Official Cloudflare DoH endpoints. Both keep the same verified TLS host.
    # A failed resolver is infrastructure failure, not a news site's refusal.
    DNS_ENDPOINTS = ('1.1.1.1', '1.0.0.1')
    PUBLIC_DNS_ENDPOINTS = ('223.5.5.5', '1.1.1.1', '1.0.0.1')
    def __init__(self, cache_dir='data/probes/http', *, timeout=12, max_bytes=2_000_000,
                 max_redirects=4, retries=1, rate_per_minute=10, cache_seconds=300,
                 dns_mode='system'):
        if (any(type(value) is not int for value in (timeout,max_bytes,max_redirects,retries,rate_per_minute,cache_seconds))
                or not 0 < timeout <= 30 or not 0 < max_bytes <= 5_000_000
                or not 0 <= retries <= 2 or not 0 <= max_redirects <= 5):
            raise ValueError('invalid HTTP limits')
        if not 1 <= rate_per_minute <= 10 or not 0 <= cache_seconds <= 86400:
            raise ValueError('invalid rate/cache limits')
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout, self.max_bytes = timeout, max_bytes
        self.max_redirects, self.retries = max_redirects, retries
        self.delay, self.cache_seconds = 60/rate_per_minute, cache_seconds
        if dns_mode not in {'system', 'cloudflare_public', 'public_fallback'}:
            raise ValueError('unknown DNS mode')
        self.dns_mode = dns_mode
        self._dns = {}
        self._dns_endpoint_failures = {}
        self._dns_endpoints = self.PUBLIC_DNS_ENDPOINTS if dns_mode == 'public_fallback' else self.DNS_ENDPOINTS
        self._dns_preferred = self._dns_endpoints[0]
        self._robots, self._last, self._locks = {}, {}, {}
        self._guard = threading.Lock()
        self.stats = {'network_requests': 0, 'dns_requests': 0, 'cache_hits': 0, 'failed_requests': 0,
                      'dns_endpoint_failures': 0, 'dns_fallbacks': 0}
        self.access_reviews = {}

    def _public_dns(self, hostname):
        # Serialise resolver health updates so a batch does not repeat the same
        # unavailable endpoint for every source. DNS answers still have a TTL.
        with self._lock('public-dns-health'):
            now = time.monotonic()
            ordered = sorted(self._dns_endpoints, key=lambda ip: (
                self._dns_endpoint_failures.get(ip, -300)+60 > now,
                ip != self._dns_preferred))
            for ip in ordered:
                if self._dns_endpoint_failures.get(ip, -300)+60 > now:
                    continue
                host, path = ('dns.alidns.com','/resolve') if ip == '223.5.5.5' else ('cloudflare-dns.com','/dns-query')
                conn = _PinnedHTTP(host,443,ip,min(self.timeout,4),True)
                self.stats['network_requests'] += 1
                self.stats['dns_requests'] += 1
                try:
                    conn.request('GET',path+'?name='+quote(hostname,safe='')+'&type=A',
                                 headers={'Accept':'application/dns-json','User-Agent':self.USER_AGENT})
                    response = conn.getresponse()
                    body = response.read(64001)
                    if response.status != 200 or len(body) > 64000:
                        raise ValueError('invalid resolver response')
                    document = json.loads(body)
                    if not isinstance(document,dict) or type(document.get('Status')) is not int:
                        raise ValueError('invalid DNS response')
                    # Authoritative negative answers are not a transport error.
                    if document['Status'] == 3:
                        raise NetworkError('dns_failed')
                    if document['Status'] != 0:
                        raise ValueError('resolver unavailable')
                    records = document.get('Answer',[])
                    if not isinstance(records,list):
                        raise ValueError('invalid DNS answers')
                    addresses = sorted({entry['data'] for entry in records if entry.get('type') == 1})
                    if not addresses:
                        raise NetworkError('dns_failed')
                    for literal in addresses:
                        address = ipaddress.ip_address(literal)
                        if (address.version != 4 or not address.is_global or address.is_multicast
                                or address.is_reserved):
                            # Never try a different resolver to evade an unsafe answer.
                            raise NetworkError('unsafe_dns_address')
                    if ip != self._dns_endpoints[0]:
                        self.stats['dns_fallbacks'] += 1
                    self._dns_preferred = ip
                    ttl = min([300]+[max(0,int(entry.get('TTL',0))) for entry in records
                                     if entry.get('type') in {1,5}])
                    return addresses,ttl
                except NetworkError:
                    raise
                except (OSError,http.client.HTTPException,ValueError,KeyError,TypeError,AttributeError):
                    self._dns_endpoint_failures[ip] = time.monotonic()
                    self.stats['dns_endpoint_failures'] += 1
                finally:
                    conn.close()
            raise NetworkError('public_dns_failed')

    def _resolve(self, url):
        if self.dns_mode == 'system':
            return resolve_public(url)
        try:
            url = normalize_url(url)
            parts = urlsplit(url)
            if parts.port not in {None, 80, 443}:
                raise NetworkError('unsafe_port')
            try:
                address = ipaddress.ip_address(parts.hostname)
                return url, [str(address)]  # normalize_url already checked literal safety.
            except ValueError:
                pass
            with self._lock('dns:'+parts.hostname):
                saved = self._dns.get(parts.hostname)
                if saved and time.monotonic() < saved[0]:
                    return url, saved[1]
                # Fixed public resolver endpoint; TLS hostname remains checked. No
                # system DNS or proxy lookup is used to reach this infrastructure.
                addresses, ttl = self._public_dns(parts.hostname)
                self._dns[parts.hostname] = (time.monotonic()+ttl,addresses)
                return url, addresses
        except MetadataError:
            raise NetworkError('unsafe_url') from None
        except (OSError, http.client.HTTPException, ValueError, KeyError) as exc:
            if isinstance(exc, NetworkError):
                raise
            raise NetworkError('public_dns_failed') from None

    def _lock(self, host):
        with self._guard:
            return self._locks.setdefault(host, threading.RLock())

    def _once(self, url, *, cache_seconds=None, request_body=None, address_index=0):
        url, addresses = self._resolve(url)
        key = hashlib.sha256(url.encode()+(request_body or b'')).hexdigest()
        meta, bodyfile = self.cache_dir/(key+'.json'), self.cache_dir/(key+'.body')
        lifetime = self.cache_seconds if cache_seconds is None else cache_seconds
        if meta.exists() and bodyfile.exists():
            try:
                saved = json.loads(meta.read_text(encoding='utf-8'))
                if (saved['url'] == url and time.time()-saved['saved_at'] < lifetime
                        and bodyfile.stat().st_size <= self.max_bytes):
                    body = bodyfile.read_bytes()
                    if hashlib.sha256(body).hexdigest() == saved['sha256']:
                        self.stats['cache_hits'] += 1
                        return HTTPResult(url, saved['status'], saved['headers'], body,
                                          saved['retrieved_at'], True)
            except (OSError, ValueError, KeyError):
                pass
        parts = urlsplit(url)
        with self._lock(parts.hostname):
            delay = self.delay-(time.monotonic()-self._last.get(parts.hostname, 0))
            if delay > 0:
                time.sleep(min(delay, 60))
            self._last[parts.hostname] = time.monotonic()
            self.stats['network_requests'] += 1
            conn = _PinnedHTTP(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80),
                               addresses[address_index % len(addresses)], self.timeout, parts.scheme == 'https')
            try:
                path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
                headers={'User-Agent': self.USER_AGENT,
                    'Accept': 'text/html,application/json,application/rss+xml,application/xml,text/plain;q=0.8',
                    'Accept-Encoding': 'identity', 'Connection': 'close'}
                if request_body is not None:
                    headers['Content-Type']='application/json'
                conn.request('POST' if request_body is not None else 'GET',path,body=request_body,headers=headers)
                response = conn.getresponse()
                headers = {key.lower(): value for key, value in response.getheaders()
                           if key.lower() in {'content-type','content-length','location','retry-after','last-modified','etag','content-encoding'}}
                if headers.get('content-encoding', 'identity').lower() not in {'identity', ''}:
                    raise NetworkError('unsupported_content_encoding')
                if int(headers.get('content-length', '0')) > self.max_bytes:
                    raise NetworkError('response_too_large')
                deadline = time.monotonic()+self.timeout
                chunks, size = [], 0
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise NetworkError('timeout')
                    if conn.sock:
                        conn.sock.settimeout(remaining)
                    chunk = response.read1(min(65536,self.max_bytes+1-size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise NetworkError('response_too_large')
                body = b''.join(chunks)
                if len(body) > self.max_bytes:
                    raise NetworkError('response_too_large')
                result = HTTPResult(url, response.status, headers, body, datetime.now(timezone.utc).isoformat())
                if response.status in {200, 404, 410}:
                    bodyfile.write_bytes(body)
                    meta.write_text(json.dumps({'url':url,'status':response.status,'headers':headers,
                        'retrieved_at':result.retrieved_at,'saved_at':time.time(),
                        'sha256':hashlib.sha256(body).hexdigest()},ensure_ascii=False),encoding='utf-8')
                return result
            except (TimeoutError, socket.timeout):
                raise NetworkError('timeout') from None
            except ssl.SSLCertVerificationError:
                raise NetworkError('tls_certificate_invalid') from None
            except ssl.SSLError:
                raise NetworkError('tls_failed') from None
            except (OSError, http.client.HTTPException, ValueError) as exc:
                if isinstance(exc, NetworkError):
                    raise
                raise NetworkError('connection_failed') from None
            finally:
                conn.close()

    def _read(self, url, *, robots_file=False, allowed_hosts=None, request_body=None):
        visited = set()
        for hop in range(self.max_redirects+1):
            url, _ = self._resolve(url)
            if allowed_hosts is not None and urlsplit(url).hostname not in allowed_hosts:
                raise NetworkError('redirect_outside_allowed_hosts')
            if url in visited:
                raise NetworkError('redirect_loop')
            visited.add(url)
            if not robots_file:
                self.check_robots(url)
            result = None
            for attempt in range(self.retries+1):
                try:
                    result = self._once(url,cache_seconds=3600 if robots_file else None,
                                        request_body=request_body,address_index=attempt)
                    if result.status not in {500,502,503,504} or attempt == self.retries:
                        break
                except NetworkError as exc:
                    if exc.code not in {'timeout','connection_failed','tls_failed'} or attempt == self.retries:
                        raise
            if result.status in {301,302,303,307,308}:
                if request_body is not None:
                    raise NetworkError('read_only_post_redirect_not_supported')
                if hop == self.max_redirects or not result.headers.get('location'):
                    raise NetworkError('redirect_limit')
                target = urljoin(url, result.headers['location'])
                target, _ = self._resolve(target)
                if urlsplit(url).scheme == 'https' and urlsplit(target).scheme != 'https':
                    raise NetworkError('https_downgrade')
                # robots redirects are only accepted within the original origin.
                if robots_file and urlsplit(target).netloc != urlsplit(url).netloc:
                    raise NetworkError('robots_cross_origin_redirect')
                url = target
                continue
            return result
        raise NetworkError('redirect_limit')

    def check_robots(self, url):
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, '', '', ''))
        with self._lock(parts.hostname):
            if origin not in self._robots:
                result = self._read(origin+'/robots.txt', robots_file=True)
                parser = RobotFileParser()
                if result.status in {404,410} or reviewed_robots_soft_404(result):
                    rules_text = 'User-agent: *\nAllow: /'
                    parser.parse(rules_text.splitlines())
                    status = ('robots_reviewed_soft_404_terms_not_granted' if result.status == 200
                              else 'robots_missing_terms_not_granted')
                elif result.status == 200:
                    if '<html' in result.text[:1024].lower() or not re.search(r'^\s*user-agent\s*:',result.text,re.M|re.I):
                        raise NetworkError('robots_invalid')
                    rules_text = result.text
                    parser.parse(rules_text.splitlines())
                    status = 'robots_checked_terms_not_granted'
                else:
                    raise NetworkError('robots_http_'+str(result.status))
                self._robots[origin] = (parser,rules_text)
                self.access_reviews[origin] = {'status':status,'robots_url':result.url,
                    'robots_http_status':result.status,'retrieved_at':result.retrieved_at,
                    'terms':'not_a_license_no_republication_no_fulltext'}
            parser, rules_text = self._robots[origin]
            if not robots_allowed(rules_text,self.USER_AGENT,url):
                raise NetworkError('robots_disallowed')
            crawl_delay = parser.crawl_delay(self.USER_AGENT)
            request_rate = parser.request_rate(self.USER_AGENT)
            required = max(crawl_delay or 0, request_rate.seconds/request_rate.requests if request_rate else 0)
            if required > 60:
                raise NetworkError('robots_delay_exceeds_runtime_limit')
            self.delay = max(self.delay, required)

    def get(self, url, *, allowed_hosts=None):
        try:
            result = self._read(url,allowed_hosts=allowed_hosts)
            if result.status != 200:
                raise NetworkError('http_'+str(result.status))
            if any(marker in result.text[:12000].lower() for marker in
                   ('cf-chl-', 'checking your browser', 'verify you are human', '人机验证', '访问过于频繁')):
                raise NetworkError('access_challenge')
            return result
        except NetworkError:
            self.stats['failed_requests'] += 1
            raise

    def post_json(self,url,payload):
        """Only this observed public listing endpoint supports read-only POST.

        Its method and request shape come from the official site's linked
        frontend chunk, not an invented or private API. It never mutates data.
        """
        if url != 'https://ncpscxx.moa.gov.cn/product/FarmDaily/selectDailyList':
            raise NetworkError('post_endpoint_not_reviewed')
        if (not isinstance(payload,dict) or set(payload)!={'pageSize','currentPage'}
                or type(payload['pageSize']) is not int or not 1<=payload['pageSize']<=50
                or type(payload['currentPage']) is not int or not 1<=payload['currentPage']<=5):
            raise NetworkError('read_only_post_payload_invalid')
        try:
            result=self._read(url,allowed_hosts={'ncpscxx.moa.gov.cn'},
                              request_body=json.dumps(payload,sort_keys=True).encode())
            if result.status!=200:
                raise NetworkError('http_'+str(result.status))
            return result
        except NetworkError:
            self.stats['failed_requests']+=1
            raise
