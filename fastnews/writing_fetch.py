"""Disposable, robots-aware source reader. Parent enforces a wall-clock limit."""
import json
import sys
import re
from io import BytesIO
from urllib.parse import urlsplit
from html.parser import HTMLParser

from .network import SafeHTTPClient
from .public_text import PageText


class PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.published_at=None

    def handle_starttag(self,tag,attrs):
        attr=dict(attrs)
        key=(attr.get('property') or attr.get('name') or attr.get('itemprop') or '').lower()
        if tag=='meta' and key in {'article:published_time','datepublished','pubdate','publishdate','publish_time','publication_date'}:
            self.published_at=attr.get('content') or self.published_at
        if tag=='time' and key=='datepublished':
            self.published_at=attr.get('datetime') or self.published_at


def read_page(url):
    client=SafeHTTPClient(cache_dir='data/writing/http',timeout=5,max_bytes=600000,
                          retries=0,max_redirects=2,rate_per_minute=10,dns_mode='public_fallback')
    response=client.get(url,allowed_hosts={urlsplit(url).hostname})
    mime=response.headers.get('content-type','').lower()
    if 'application/pdf' in mime:
        from pypdf import PdfReader
        reader=PdfReader(BytesIO(response.body))
        if reader.is_encrypted:
            raise ValueError('restricted_pdf')
        text='\n'.join(f'【PDF第{i+1}页】\n'+page.extract_text() for i,page in enumerate(reader.pages[:20]))
        found=re.search(r'报告送出日期[：:]?\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',text)
        published='-'.join([found[1],found[2].zfill(2),found[3].zfill(2)]) if found else None
        if len(text.strip())<80:
            raise ValueError('pdf_requires_ocr')
        return dict(text=text[:60000],url=response.url,retrieved_at=response.retrieved_at,published_at=published,
                    pages_read=min(len(reader.pages),20),format='pdf')
    if not any(t in mime for t in ('text/html','text/plain','application/xhtml')):
        raise ValueError('unsupported_format')
    parser=PageText()
    parser.feed(response.text)
    metadata=PublicationMetadata();metadata.feed(response.text)
    # The publisher encodes the article's day in this documented URL layout.
    # This remains date precision; it cannot satisfy an intraday cutoff.
    if not metadata.published_at and urlsplit(response.url).hostname=='fund.jrj.com.cn':
        encoded=re.fullmatch(r'/(20\d{2})/(\d{2})/(\d{2})\d+\.shtml',urlsplit(response.url).path)
        if encoded:
            metadata.published_at='-'.join(encoded.groups())
    text=response.text if 'text/plain' in mime else parser.text()
    if len(text)<40:
        raise ValueError('insufficient_text')
    if any(t in text.casefold() for t in ('verify you are human','captcha','subscribe to read',
        'sign in to continue','登录后阅读全文','付费后阅读全文','订阅后阅读全文','访问验证')):
        raise ValueError('restricted_content')
    return dict(text=text[:60000],url=response.url,retrieved_at=response.retrieved_at,
                published_at=metadata.published_at)


if __name__=='__main__':
    try:
        request=json.load(sys.stdin)
        result=read_page(request['url'])
    except Exception as exc:
        result={'error':getattr(exc,'code',None) or str(exc)[:80]}
    print(json.dumps(result,ensure_ascii=True))
