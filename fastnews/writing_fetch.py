"""Disposable, robots-aware source reader. Parent enforces a wall-clock limit."""
import json
import sys
import re
from io import BytesIO
from urllib.parse import urlsplit
from html.parser import HTMLParser

from .network import SafeHTTPClient
from .public_text import PageText
from .metadata import time_bounds


class PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.dates=[]

    @property
    def published_at(self):
        # Conflicting publication tags are ambiguous, never last-tag-wins.
        dated=[]
        for value in self.dates:
            try:
                lo,hi=time_bounds(value)
            except ValueError:
                continue
            if lo is not None:
                dated.append((lo,hi,value))
        if not dated or max(d[0] for d in dated)>min(d[1] for d in dated):
            return None
        # Compatible day/timestamp tags retain the most precise observed value.
        return min(dated,key=lambda d:d[1]-d[0])[2]

    def handle_starttag(self,tag,attrs):
        attr=dict(attrs)
        key=(attr.get('property') or attr.get('name') or attr.get('itemprop') or '').lower()
        if tag=='meta' and key in {'article:published_time','datepublished','pubdate','publishdate','publish_time','publication_date'}:
            self.dates.append(attr.get('content'))
        if tag=='time' and key=='datepublished':
            self.dates.append(attr.get('datetime'))


def read_page(url):
    from .writing import policy
    client=SafeHTTPClient(cache_dir='data/writing/http',timeout=5,max_bytes=policy()['max_source_bytes'],
                          retries=0,max_redirects=2,rate_per_minute=10,dns_mode='public_fallback')
    response=client.get(url,allowed_hosts={urlsplit(url).hostname})
    mime=response.headers.get('content-type','').lower()
    if 'application/pdf' in mime:
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(response.body))
        if reader.is_encrypted:
            raise ValueError('restricted_pdf')
        pages=[(i+1,page.extract_text() or '') for i,page in enumerate(reader.pages[:20])]
        if len(''.join(text.strip() for _,text in pages))<80:
            raise ValueError('pdf_requires_ocr')
        text='\n'.join(f'【PDF第{i}页】\n'+body for i,body in pages if body.strip())
        found=re.search(r'报告送出日期[：:]?\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',text)
        published='-'.join([found[1],found[2].zfill(2),found[3].zfill(2)]) if found else None
        return dict(text=text[:60000],url=response.url,retrieved_at=response.retrieved_at,published_at=published,
                    pages_read=min(len(reader.pages),20),format='pdf')
    if not any(t in mime for t in ('text/html','text/plain','application/xhtml')):
        raise ValueError('unsupported_format')
    parser=PageText()
    parser.feed(response.text)
    metadata=PublicationMetadata();metadata.feed(response.text)
    # The publisher encodes the article's day in this documented URL layout.
    # This remains date precision; it cannot satisfy an intraday cutoff.
    published=metadata.published_at
    if not metadata.dates and urlsplit(response.url).hostname=='fund.jrj.com.cn':
        encoded=re.fullmatch(r'/(20\d{2})/(\d{2})/(\d{2})\d+\.shtml',urlsplit(response.url).path)
        if encoded:
            published='-'.join(encoded.groups())
    text=response.text if 'text/plain' in mime else parser.text()
    if len(text)<40:
        raise ValueError('insufficient_text')
    if any(t in text.casefold() for t in ('verify you are human','captcha','subscribe to read',
        'sign in to continue','登录后阅读全文','付费后阅读全文','订阅后阅读全文','访问验证')):
        raise ValueError('restricted_content')
    return dict(text=text[:60000],url=response.url,retrieved_at=response.retrieved_at,
                published_at=published)


if __name__=='__main__':
    try:
        request=json.load(sys.stdin)
        result=read_page(request['url'])
    except Exception as exc:
        result={'error':getattr(exc,'code',None) or str(exc)[:80]}
    print(json.dumps(result,ensure_ascii=True))
