"""Verified public HTML listing readers, metadata only and never article evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
import yaml

from .adapters import DiscoveryError, DiscoveryPage
from .metadata import MetadataError, content_hash, normalize_record, normalize_url, parse_instant, time_bounds
from .network import SafeHTTPClient, NetworkError
from .strict_json import loads as strict_loads

ROOT = Path(__file__).resolve().parents[1]


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node,value_node in node.value:
        key = loader.construct_object(key_node,deep=deep)
        if key in mapping:
            raise ValueError('duplicate live config key')
        mapping[key] = loader.construct_object(value_node,deep=deep)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_unique_mapping)


class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def nodes(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.nodes(tag)

    def text(self):
        return ' '.join(''.join(child.text() if isinstance(child,Node) else child
                               for child in self.children).split())

    def first(self, tag):
        return next(self.nodes(tag), None)


class ListingTree(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 256:
            raise DiscoveryError('parse_depth_limit')
        node = Node(tag,attrs,self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self,data):
        if not any(node.tag in {'script','style'} for node in self.stack[-2:]):
            self.stack[-1].children.append(data)


def _date(text):
    found = re.search(r'(?<!\d)(20\d{2})[-.年](\d{1,2})[-.月](\d{1,2})(?:日)?(?!\d)',text)
    if found:
        try:
            return datetime(*map(int,found.groups())).date().isoformat()
        except ValueError:
            return None
    for fmt in ('%d %B %Y','%B %d, %Y'):
        try:
            return datetime.strptime(text.strip(),fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _make(source_id, anchor, listing, *, title=None, published=None, lead='', raw_date=None,
          machine_datetime=None):
    href = anchor.attrs.get('href')
    if not href:
        return None
    try:
        url = normalize_url(urljoin(listing.url,href))
        # Discovery stays within this registered site's domain. External links
        # remain on the page for a future separately reviewed source backlog.
        if urlsplit(url).hostname != urlsplit(listing.url).hostname:
            return None
        title = title or anchor.attrs.get('title') or anchor.text()
        if len(title.strip()) < 6:
            return None
        lineage = {'role':'listing_metadata','url':listing.url,'article_url':url,
                   'raw_report_date':raw_date,'machine_datetime':machine_datetime,
                   'retrieved_at':listing.retrieved_at,'article_read':False}
        return normalize_record({'source_id':source_id,'title':title,'url':url,'lead':lead,
            'first_published_at':None,'report_published_at':published,'origin_role':'unknown',
            'attribution_chain':[lineage]},is_fixture=False)
    except MetadataError:
        raise DiscoveryError('invalid_metadata') from None


def parse_listing(source_id, parser, listing):
    if parser.startswith('wscn_'):
        from .source_parsers import parse_wscn
        return parse_wscn(source_id, parser, listing)
    if parser == 'eastmoney_southbound':
        from .source_parsers import parse_eastmoney
        return parse_eastmoney(source_id, listing)
    if parser == 'official_rss':
        from .source_parsers import parse_official_feed
        return parse_official_feed(source_id, listing)
    tree = ListingTree(listing.text).root
    records = []
    if parser == 'cls_subject':
        # The public column embeds the same title/brief/date as its visible
        # cards. Parse JSON only; never execute scripts or follow app/share URLs.
        match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', listing.text, re.S)
        expected = {'cls_morning':1151, 'cls_hk_morning':8705, 'cls_southbound':9870,
                    'cls_innovative_drug':1207, 'cls_semiconductor_materials':11056}.get(source_id)
        try:
            data = strict_loads(match[1])['props']['pageProps']['data'] if match else None
            if not data or data['id'] != expected or not isinstance(data['articles'], list):
                raise ValueError('Wrong column')
            anchors = {a.attrs.get('href'): a for a in tree.nodes('a')}
            for item in data['articles'][:50]:
                aid, stamp = item['article_id'], item['article_time']
                if type(aid) is not int or type(stamp) is not int or not 946684800 <= stamp < 4102444800:
                    raise ValueError('Invalid listing identity/date')
                anchor = anchors.get('/detail/' + str(aid))
                if anchor is None:
                    continue
                title, brief = item['article_title'], item.get('article_brief') or ''
                if not isinstance(title, str) or not isinstance(brief, str):
                    raise ValueError('Invalid listing text')
                if source_id in {'cls_southbound','cls_innovative_drug','cls_semiconductor_materials'}:
                    # Telegraph cards put the headline and displayed snippet in
                    # one field. Retain the visible headline without a wall of text.
                    headline = re.match(r'^【([^】]{6,200})】(.*)', title, re.S)
                    if headline:
                        title, brief = headline[1], brief or headline[2][:600]
                published = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                records.append(_make(source_id, anchor, listing, title=title,
                    published=published, lead=brief[:4000], raw_date=str(stamp), machine_datetime=published))
        except (ValueError, KeyError, TypeError, OverflowError):
            raise DiscoveryError('parse_error') from None
    elif parser == 'trendforce':
        for card in tree.nodes('div'):
            if 'list-item' not in card.attrs.get('class','').split():
                continue
            title_node, date_node, lead = card.first('h3'), card.first('h4'), card.first('p')
            anchor = title_node.first('a') if title_node else None
            if anchor and date_node:
                date_text = date_node.text()
                published = _date(date_text)
                if published is None:
                    raise DiscoveryError('parse_date_error')
                records.append(_make(source_id,anchor,listing,published=published,
                                     lead=lead.text()[:4000] if lead else '',raw_date=date_text))
    elif parser == 'dated_li':
        for card in tree.nodes('li'):
            # A date must be printed alongside this row; do not extract years
            # from a headline or infer a date from its URL.
            date_nodes = [node for node in card.nodes('span') if _date(node.text())]
            if not date_nodes:
                continue
            anchor = card.first('a')
            if anchor:
                date_text = date_nodes[0].text()
                records.append(_make(source_id,anchor,listing,published=_date(date_text),raw_date=date_text))
    elif parser == 'stcn_morning':
        for anchor in tree.nodes('a'):
            title = anchor.attrs.get('title') or anchor.text()
            if title.startswith(('【早知道】','〖早知道〗')):
                records.append(_make(source_id,anchor,listing,title=title))
    elif parser == 'fda':
        for anchor in tree.nodes('a'):
            time_node = anchor.first('time')
            if time_node and '/press-announcements/' in anchor.attrs.get('href',''):
                raw_date = time_node.text()
                title = anchor.text().removeprefix(raw_date).strip(' -')
                machine = time_node.attrs.get('datetime')
                try:
                    # The source supplies an explicit UTC CMS timestamp. This
                    # is the report clock, never the first public fact clock.
                    published = parse_instant(machine).isoformat() if machine else None
                except MetadataError:
                    published = None
                records.append(_make(source_id,anchor,listing,title=title,
                    published=published,raw_date=raw_date,machine_datetime=machine))
    else:
        raise DiscoveryError('adapter_not_connected')
    unique = {}
    for record in records:
        if record is not None:
            unique.setdefault(record['url'],record)
    if not unique:
        raise DiscoveryError('parse_error')
    return list(unique.values())


def load_live_config(config=None, path=None):
    if config is None:
        from .config import load_config
        config = load_config()
    from .source_channels import with_channels
    config = with_channels(config)
    path = Path(path) if path else ROOT/'config/live_sources.yaml'
    try:
        if path.stat().st_size > 200000:
            raise ValueError('live config too large')
        document = yaml.load(path.read_text(encoding='utf-8'),Loader=UniqueLoader)
        if (not isinstance(document,dict) or set(document) != {'version','note','network','sources'}
                or type(document['version']) is not int or document['version'] != 1
                or not isinstance(document['sources'],dict)):
            raise ValueError('invalid live config')
        if (not isinstance(document['network'],dict) or set(document['network']) !=
                {'dns_mode','timeout','max_bytes','max_redirects','retries','rate_per_minute','cache_seconds'}):
            raise ValueError('invalid live network settings')
        if set(document['sources'])-set(config['sources']):
            raise ValueError('live source must already exist in registry')
        for sid,entry in document['sources'].items():
            if not isinstance(entry,dict) or set(entry) - {'discovery_priority'} != {'listing_url','parser','scope','date_timezone','known_gaps',
                                                         'metadata_allowed','fulltext_allowed','terms_url','terms_review'}:
                raise ValueError('invalid live source fields')
            if entry['parser'] not in {'trendforce','dated_li','stcn_morning','fda','cls_subject','none',
                                       'wscn_breakfast','wscn_technology','wscn_medicine','official_rss','eastmoney_southbound'}:
                raise ValueError('unsupported live parser')
            if type(entry.get('discovery_priority',50)) is not int or not 0 <= entry.get('discovery_priority',50) <= 100:
                raise ValueError('invalid discovery priority')
            if (not isinstance(entry['scope'],str) or not entry['scope'].strip()
                    or entry['date_timezone'] not in {None,'Asia/Shanghai'}
                    or not isinstance(entry['known_gaps'],list)
                    or any(not isinstance(gap,str) for gap in entry['known_gaps'])
                    or type(entry['metadata_allowed']) is not bool or type(entry['fulltext_allowed']) is not bool
                    or not isinstance(entry['terms_review'],str)):
                raise ValueError('invalid live scope or gaps')
            if entry['terms_url'] is not None:
                normalize_url(entry['terms_url'])
            if entry['parser'] == 'none' and entry['metadata_allowed']:
                raise ValueError('unimplemented parser cannot be metadata_allowed')
            url = normalize_url(entry['listing_url'])
            from .source_parsers import eastmoney_url
            reviewed_api = sid == 'eastmoney_hsgt' and entry['parser'] == 'eastmoney_southbound' and url == eastmoney_url()
            if not reviewed_api and urlsplit(url).hostname != urlsplit(config['sources'][sid]['home_url']).hostname:
                raise ValueError('live listing must stay at registered hostname')
        return document
    except (OSError,yaml.YAMLError,MetadataError):
        raise ValueError('invalid live source configuration') from None


class LiveAdapter:
    def __init__(self, config, cache_dir='data/probes/http', live_config_path=None, client=None):
        self.config = config
        self.settings = load_live_config(config, live_config_path)
        self.client = client or SafeHTTPClient(cache_dir, **self.settings['network'])
        self.stats = self.client.stats
        self.last_discoveries = {}

    @property
    def supported_sources(self):
        return {sid for sid,entry in self.settings['sources'].items() if entry['metadata_allowed'] and sid in self.config['sources']}

    def discover(self, source_id, start, end, limit, resume=None):
        if (not isinstance(start,datetime) or not isinstance(end,datetime)
                or start.tzinfo is None or end.tzinfo is None or end < start
                or type(limit) is not int or not 1 <= limit <= 500):
            raise DiscoveryError('invalid_discovery_window')
        entry = self.settings['sources'].get(source_id)
        if entry is None:
            raise DiscoveryError('adapter_not_connected')
        if not entry['metadata_allowed']:
            raise DiscoveryError('source_terms_manual_only')
        try:
            listing = self.client.get(entry['listing_url'],allowed_hosts={urlsplit(entry['listing_url']).hostname})
            records = parse_listing(source_id, entry['parser'], listing)
        except NetworkError as exc:
            raise DiscoveryError(exc.code) from None
        self.last_discoveries[source_id] = {'listing_url':listing.url,'listing_retrieved_at':listing.retrieved_at,
            'parsed_items':len(records),'from_cache':listing.from_cache,
            'scope':entry['scope'],'known_gaps':entry['known_gaps']}
        filtered = []
        for record in records:
            lower, upper = time_bounds(record['report_published_at'])
            if entry['parser'] == 'eastmoney_southbound':
                # Restrict historical statistics to the requested trading-date
                # window, while keeping publication time unknown in the record.
                lower, upper = time_bounds(record['data_period_end'])
            if lower is not None and (upper < start or lower > end):
                continue
            filtered.append(record)
        digest = hashlib.sha256(json.dumps([(r['url'],content_hash(r)) for r in filtered],
                                          ensure_ascii=False).encode()).hexdigest()
        offset = resume.get('offset',0) if resume and resume.get('dataset_hash') == digest else 0
        if type(offset) is not int or not 0 <= offset <= len(filtered):
            raise DiscoveryError('invalid_resume_token')
        selected = filtered[offset:offset+limit]
        token = None
        reason = 'listing_scope_incomplete'
        if offset+len(selected) < len(filtered):
            token = {'dataset_hash':digest,'offset':offset+len(selected),
                     'window_start':start.isoformat(),'window_end':end.isoformat()}
            reason = 'item_limit_and_listing_scope_incomplete'
        # A finite homepage cannot establish complete historical coverage. In
        # particular an empty in-window slice is not a complete-source success.
        return DiscoveryPage(selected,False,token,len(filtered),coverage_reason=reason)


def probe_sources(config, cache_dir='data/probes/http', live_config_path=None):
    from .source_channels import with_channels
    config = with_channels(config)
    adapter = LiveAdapter(config,cache_dir,live_config_path)
    checked = datetime.now(timezone.utc).isoformat()
    def one(pair):
        sid, source = pair
        entry = adapter.settings['sources'].get(sid)
        url = entry['listing_url'] if entry else source.get('discovery_url') or source['home_url']
        row = {'source_id':sid,'expected_enabled':source['enabled'],'expected_morning':source['morning_scan'],
            'original_home_url':source['home_url'],'probed_url':url,'validated_discovery_url':None,
            'capability_status':'needs_probe','adapter_name':None,'access_review':'not_checked',
            'last_probe_at':checked,'last_success_at':None,'run_status':None,'error_code':None,
            'metadata_fields':[],'sample_items':[],'records_parsed':0,'date_timezone':None,
            'fulltext_connected':False,'fulltext_allowed':entry['fulltext_allowed'] if entry else False,
            'terms_url':entry['terms_url'] if entry else None,'terms_review':entry['terms_review'] if entry else 'not_reviewed',
            'pagination':None,'coverage_incomplete':True,'known_gaps':[]}
        if entry and not entry['metadata_allowed']:
            row.update(capability_status='manual_only',run_status='blocked_by_terms',
                       error_code='source_terms_manual_only',access_review='terms_require_written_authorization',
                       known_gaps=['authorization_required','metadata_automation_disabled'])
            return row
        try:
            listing = adapter.client.get(url,allowed_hosts={urlsplit(url).hostname})
            origin = listing.url.split('/',3)[:3]
            row['access_review'] = adapter.client.access_reviews.get('/'.join(origin),{}).get('status','checked')
            row.update(final_url=listing.url,http_status=listing.status,retrieved_at=listing.retrieved_at,
                       from_cache=listing.from_cache,response_bytes=len(listing.body))
            if entry:
                records = parse_listing(sid,entry['parser'],listing)
                row.update(capability_status='ready_metadata',adapter_name='live_html:'+entry['parser'],
                    validated_discovery_url=listing.url,last_success_at=listing.retrieved_at,run_status='success',
                    records_parsed=len(records),date_timezone=entry['date_timezone'],scope=entry['scope'],
                    pagination='current_listing_only',known_gaps=entry['known_gaps'],
                    metadata_fields=['title','url','source_id','listing_source_chain']+(
                        ['report_published_at'] if any(r['report_published_at'] for r in records) else []),
                    sample_items=[{'title':r['title'],'url':r['url'],'report_published_at':r['report_published_at'],
                        'first_published_at':None,'evidence_state':'pending','is_fixture':False} for r in records[:3]])
            else:
                row.update(capability_status='manual_only' if sid == 'dxy_insight' else 'degraded',
                    run_status='accessible_adapter_not_connected',known_gaps=['metadata_parser_not_connected','fulltext_not_connected'],
                    error_code='adapter_not_connected')
        except (NetworkError,DiscoveryError) as exc:
            row.update(capability_status='blocked' if isinstance(exc,NetworkError) else 'degraded',
                       run_status='failed',error_code=exc.code,known_gaps=[exc.code],
                       access_review=exc.code if exc.code.startswith('robots_') else 'incomplete')
        return row
    # Independent domains run concurrently; shared client imposes per-host rate
    # limits. Every failure is returned as its own source record.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(one,pair):pair[0] for pair in config['sources'].items()}
        rows = [future.result() for future in as_completed(futures)]
    rows.sort(key=lambda row:list(config['sources']).index(row['source_id']))
    counts = {}
    for row in rows:
        key = row['capability_status']
        counts[key] = counts.get(key,0)+1
    return {'checked_at':checked,'finished_at':datetime.now(timezone.utc).isoformat(),'is_fixture':False,
        'summary':{'registered_sources':len(rows),'supported_adapters':len(adapter.supported_sources),
            'live_connected':counts.get('ready_metadata',0),'capabilities':counts,
            'fulltext_connected':0,'search_connected':False,'llm_connected':False,'wind_connected':False},
        'usage':dict(adapter.stats),'sources':rows,'access_reviews':adapter.client.access_reviews,
        'network_policy':adapter.settings['network'],
        'notice':'HTTP可访问不等于已接通。ready_metadata只表示公开列表字段可读；所有事实待核实，未采全文，无完整历史覆盖。'}
