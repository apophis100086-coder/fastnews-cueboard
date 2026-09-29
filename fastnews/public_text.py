"""Explicit, bounded public-page reading for research; no semantic claims."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .metadata import MetadataError, normalize_url
from .network import SafeHTTPClient, NetworkError
from .providers import ProviderFailure, ProviderResult


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.in_title = False
        self.title = []
        self.parts = []
        self.article_parts = []
        self.article_depth = 0
        self.has_article_container = False
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag in {"article", "main"}:
            self.has_article_container = True
            self.article_depth += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")
            if self.article_depth:
                self.article_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False
        if tag in {"article", "main"}:
            self.article_depth = max(0, self.article_depth - 1)

    def handle_data(self, data):
        if self.hidden:
            return
        if self.in_title:
            self.title.append(data)
        self.parts.append(data)
        if self.article_depth:
            self.article_parts.append(data)

    def text(self):
        # A declared article region is authoritative even when it is empty or
        # short. Navigation and footers must not turn a failed body into success.
        chosen = self.article_parts if self.has_article_container else self.parts
        return "\n".join(line for line in (re.sub(r"\s+", " ", part).strip() for part in "".join(chosen).splitlines()) if line)


class PublicTextProvider:
    name, version, mode, configured, external = "public_html", "public-html-v1", "live", True, True

    def __init__(self, config, client=None, live_config=None):
        self.sources = config["sources"]
        from .live_sources import load_live_config
        live = live_config if live_config is not None else load_live_config()
        self.policies = live.get("sources", {})
        if client is None:
            client = SafeHTTPClient(cache_dir="data/research/http", max_bytes=500_000,
                                    dns_mode=live.get("network", {}).get("dns_mode", "system"))
            client.USER_AGENT = "FastnewsEvidenceReader/0.3 (public shortlist; local research; no login)"
        self.client = client
        self.receipts = []

    def estimate_max_cost(self, **kwargs):
        # This reader invokes ordinary public HTTP, not a priced search or model API.
        return 0

    def access_gap(self, url, *, source_id):
        """Purely local policy preflight; never performs DNS, HTTP or a paid call."""
        try:
            url = normalize_url(url)
        except MetadataError:
            return "unsafe_url"
        if source_id not in self.sources:
            return "unknown_registered_source"
        source = self.sources[source_id]
        if self.policies.get(source_id, {}).get("fulltext_allowed") is not True:
            return "fulltext_access_not_approved"
        hosts = {urlsplit(source[key]).hostname for key in ("home_url", "discovery_url") if source.get(key)}
        requested_host = urlsplit(url).hostname
        if requested_host not in hosts:
            return "one_off_source_requires_explicit_import"
        return None

    def fetch(self, url, *, source_id):
        gap = self.access_gap(url, source_id=source_id)
        if gap:
            raise ProviderFailure(gap, usage={"cost":0,"input_tokens":0,"output_tokens":0}, retryable=False)
        url = normalize_url(url)
        source = self.sources[source_id]
        hosts = {urlsplit(source[key]).hostname for key in ("home_url", "discovery_url") if source.get(key)}
        try:
            response = self.client.get(url, allowed_hosts=hosts)
        except NetworkError as exc:
            raise ProviderFailure(exc.code, usage={"cost": 0}, retryable=False) from None
        final_host = urlsplit(response.url).hostname
        if final_host not in hosts:
            raise ProviderFailure("redirect_outside_source_scope", usage={"cost": 0}, retryable=False)
        content_type = response.headers.get("content-type", "").lower()
        if not ("text/html" in content_type or "application/xhtml" in content_type or "text/plain" in content_type):
            raise ProviderFailure("document_format_not_supported", usage={"cost": 0}, retryable=False)
        parser = PageText()
        if "text/plain" in content_type:
            # Text files may legitimately contain <comparison> or code syntax.
            # Interpreting them as HTML would silently discard source content.
            content = response.text.strip()
        else:
            parser.feed(response.text)
            content = parser.text()
        if len(content) < 40:
            raise ProviderFailure("readable_text_insufficient", usage={"cost": 0}, retryable=False)
        if len(content) > 60_000:
            raise ProviderFailure("text_context_too_large", usage={"cost": 0}, retryable=False)
        if any(marker in content.casefold() for marker in (
                "订阅后阅读全文", "付费后阅读全文", "登录后查看全文", "登录后阅读全文", "购买后查看",
                "sign in to continue", "log in to continue", "login to read the full",
                "subscribe to continue", "subscribe to read the full", "purchase to read",
                "verify you are human", "please complete the captcha")):
            raise ProviderFailure("restricted_fulltext", usage={"cost": 0}, retryable=False)
        self.receipts.append({"requested_url": url, "final_url": response.url, "retrieved_at": response.retrieved_at,
                              "cache": response.from_cache, "source_id": source_id})
        return ProviderResult({"url": url, "source_id": source_id, "content": content,
            "title": "".join(parser.title).strip(), "retrieved_at": response.retrieved_at,
            "first_published_at": None, "origin_role": "unknown", "is_fixture": False},
            {"cost": 0, "input_tokens": 0, "output_tokens": 0})
