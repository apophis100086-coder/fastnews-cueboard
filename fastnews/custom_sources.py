"""Public RSS/Atom metadata for personal sources; no full-text fetching."""
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from urllib.parse import urljoin,urlsplit
from xml.etree import ElementTree

from .adapters import DiscoveryError, DiscoveryPage
from .live_sources import LiveAdapter
from .metadata import normalize_record, normalize_url, time_bounds
from .network import NetworkError


def plain(value):
    value = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', value or '', flags=re.I|re.S)
    return re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>',' ',value))).strip()


def parse_feed(source_id, result, start, end, limit):
    if re.search(r'<!\s*(DOCTYPE|ENTITY)\b', result.text, re.I):
        raise DiscoveryError('parse_error')
    try:
        root = ElementTree.fromstring(result.body)
    except ElementTree.ParseError:
        raise DiscoveryError('parse_error') from None
    local = lambda node: node.tag.rsplit('}',1)[-1]
    if local(root) not in {'rss','feed','RDF'}:
        raise DiscoveryError('parse_error')
    records, total = [], 0
    for node in root.iter():
        if local(node) not in {'item','entry'}:
            continue
        fields = {local(c):''.join(c.itertext()).strip() for c in node}
        links = [c.attrib.get('href') or c.text for c in node if local(c)=='link' and c.attrib.get('rel','alternate')=='alternate']
        if not links or not fields.get('title'):
            continue
        try:
            url = normalize_url(urljoin(result.url,links[0]))
        except ValueError:
            continue
        published = fields.get('pubDate') or fields.get('published') or fields.get('date')
        if published:
            try:
                time_bounds(published)
            except ValueError:
                try:
                    moment = parsedate_to_datetime(published)
                    published = moment.astimezone(timezone.utc).isoformat() if moment.tzinfo else None
                except (ValueError,TypeError,OverflowError):
                    published = None
        lower, upper = time_bounds(published)
        if lower and (lower > end or upper < start):
            continue
        total += 1
        if len(records) >= limit:
            continue
        records.append(normalize_record({'source_id':source_id,'title':plain(fields['title'])[:300],
            'url':url,'lead':plain(fields.get('description') or fields.get('summary'))[:1600],
            'report_published_at':published,'first_published_at':None,
            'kind':'reported','phase':'unknown','is_fixture':False,
            'attribution_chain':[{'source_id':source_id,'listing_url':result.url,
               'retrieved_at':result.retrieved_at,'access_status':'feed_metadata_only'}]},is_fixture=False))
    return DiscoveryPage(records,False,None,total,'feed_window_not_full_site',None,None)


class ReaderAdapter(LiveAdapter):
    def __init__(self, config, **kwargs):
        super().__init__(config,**kwargs)
        self.feeds = {s['source_id']:s for s in config.get('reader_sources',[]) if s['kind']=='custom' and s['collection']=='feed'}

    @property
    def supported_sources(self):
        return super().supported_sources | set(self.feeds)

    def discover(self, source_id, start, end, limit, resume=None):
        if source_id not in self.feeds:
            return super().discover(source_id,start,end,limit,resume)
        url = self.feeds[source_id]['url']
        try:
            result = self.client.get(url,allowed_hosts={urlsplit(url).hostname})
            return parse_feed(source_id,result,start,end,limit)
        except NetworkError as exc:
            raise DiscoveryError(exc.code) from None
