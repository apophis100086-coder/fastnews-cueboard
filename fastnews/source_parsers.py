"""Small independent parsers of observed public metadata formats; no script execution."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit

from .adapters import DiscoveryError
from .metadata import normalize_record, normalize_url
from .strict_json import loads

EASTMONEY_ENDPOINT = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
EASTMONEY_COLUMNS = 'TRADE_DATE,MUTUAL_TYPE,NET_DEAL_AMT,BUY_AMT,SELL_AMT'


def eastmoney_url(day=None):
    return EASTMONEY_ENDPOINT + '?' + urlencode({'sortColumns':'TRADE_DATE', 'sortTypes':'-1',
        'pageSize':10 if day is None else 1, 'pageNumber':1, 'reportName':'RPT_MUTUAL_DEAL_HISTORY',
        'columns':EASTMONEY_COLUMNS, 'source':'WEB', 'client':'WEB',
        'filter':'(MUTUAL_TYPE="006")' + (f"(TRADE_DATE='{day}')" if day else '')})


def parse_eastmoney(source_id, listing):
    try:
        data = loads(listing.text)
        if data.get('success') is not True or not isinstance(data.get('result', {}).get('data'), list):
            raise ValueError('invalid data response')
        rows = data['result']['data']
        if len(rows) > 10:
            raise ValueError('unexpected unbounded response')
        records = []
        for item in rows:
            stamp = item['TRADE_DATE']
            if item['MUTUAL_TYPE'] != '006' or not re.fullmatch(r'20\d{2}-\d{2}-\d{2} 00:00:00', stamp):
                raise ValueError('unexpected date or direction')
            day = datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S').date().isoformat()
            amounts = {}
            for field in ('NET_DEAL_AMT', 'BUY_AMT', 'SELL_AMT'):
                value = item.get(field)
                if value is None:
                    amounts[field] = None
                elif type(value) not in (int, float) or not Decimal(str(value)).is_finite():
                    raise ValueError('invalid amount')
                else:
                    amounts[field] = Decimal(str(value)) / 100
            pieces = [f'{label}{amounts[key]:.2f}亿港元' for key, label in
                [('NET_DEAL_AMT','成交净买额'),('BUY_AMT','买入成交额'),('SELL_AMT','卖出成交额')]
                if amounts[key] is not None]
            if not pieces:
                continue
            # The date is a trading period, not the first publication date.
            records.append(normalize_record({'source_id':source_id, 'url':eastmoney_url(day),
                'title':f'南向资金｜{day} 成交统计',
                'lead':'；'.join(pieces)+'。东方财富统计口径；不等同额度余额或基金净申购；具体板块去向未知。',
                'report_published_at':None, 'first_published_at':None,
                'data_period_start':day, 'data_period_end':day,
                'kind':'reported', 'origin_role':'data_aggregator',
                'attribution_chain':[{'role':'public_data_metadata', 'url':listing.url,
                    'retrieved_at':listing.retrieved_at, 'article_read':False,
                    'data_period':day, 'raw_fields_json':json.dumps(item, ensure_ascii=False, sort_keys=True), 'raw_unit':'million_HKD',
                    'display_unit':'hundred_million_HKD', 'publication_time_unknown':True}]}, is_fixture=False))
        return records
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, OverflowError):
        raise DiscoveryError('parse_error') from None


def parse_wscn(source_id, parser, listing):
    from .live_sources import ListingTree, _make
    try:
        match = re.search(r'<script\b[^>]*>\s*(?:window\.)?__SSR__\s*=\s*(.*?)</script>', listing.text, re.S)
        data = loads(match[1].strip().removesuffix(';'))['state']['default']['children']['default']['data'] if match else None
        expected = {'wscn_breakfast':'global', 'wscn_technology':'tmt', 'wscn_medicine':'medicine'}[parser]
        if not data or data['baseConfig']['channel'] != expected or not isinstance(data['items'], list):
            raise ValueError('wrong listing')
        tree = ListingTree(listing.text).root
        anchors = {normalize_url(urljoin(listing.url,a.attrs['href'])):a
                   for a in tree.nodes('a') if a.attrs.get('href','').startswith('/articles/')}
        result = {}
        for entry in data['items'][:100]:
            if entry.get('resource_type') != 'article':
                continue
            item = entry['resource']
            if any(item.get(flag) for flag in ('is_paid','is_priced','is_trial','is_in_vip_privilege','vip_type')):
                continue
            title, url, stamp = item['title'], normalize_url(item['uri']), item['display_time']
            if not isinstance(title,str) or not isinstance(item.get('content_short',''),str):
                raise ValueError('invalid text')
            if parser == 'wscn_breakfast' and '早餐' not in title:
                continue
            if (urlsplit(url).hostname != 'wallstreetcn.com' or url not in anchors
                    or type(stamp) is not int or not 946684800 <= stamp < 4102444800):
                continue
            published = datetime.fromtimestamp(stamp,timezone.utc).isoformat()
            record = _make(source_id, anchors[url], listing, title=title, published=published,
                lead=item.get('content_short','')[:600], raw_date=str(stamp), machine_datetime=published)
            if record:
                result.setdefault(record['url'], record)
        return list(result.values())
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise DiscoveryError('parse_error') from None


def parse_official_feed(source_id, listing):
    from .custom_sources import parse_feed
    page = parse_feed(source_id, listing, datetime(1970,1,1,tzinfo=timezone.utc),
                      datetime(2099,1,1,tzinfo=timezone.utc), 100)
    return [r for r in page.records if urlsplit(r['url']).hostname == urlsplit(listing.url).hostname]
