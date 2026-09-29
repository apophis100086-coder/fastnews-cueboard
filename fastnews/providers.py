"""Runtime provider contracts. Development accounts are never runtime credentials."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol, Any


@dataclass
class ProviderResult:
    payload: Any
    usage: dict = field(default_factory=dict)


class ProviderFailure(RuntimeError):
    def __init__(self, code='provider_error', *, usage=None, retryable=True):
        super().__init__(code)
        self.code, self.usage, self.retryable = code, usage or {}, retryable


class SearchProvider(Protocol):
    name: str
    version: str
    mode: str
    configured: bool
    external: bool
    def search(self, query: str, *, news_cutoff: str) -> ProviderResult: ...


class LLMProvider(Protocol):
    name: str
    version: str
    mode: str
    configured: bool
    external: bool
    input_limit: int
    output_limit: int
    def generate(self, payload: dict, *, max_output_tokens: int) -> ProviderResult: ...


class NoneSearchProvider:
    name, version, mode, configured, external = 'none', 'none-v1', 'any', False, False
    unavailable_reason = 'search_not_configured'
    def search(self, query, *, news_cutoff):
        raise ProviderFailure('provider_not_configured', retryable=False)


class NoneLLMProvider:
    name, version, mode, configured, external = 'none', 'none-v1', 'any', False, False
    unavailable_reason = 'llm_not_configured'
    input_limit, output_limit = 6000, 1500
    def generate(self, payload, *, max_output_tokens):
        raise ProviderFailure('provider_not_configured', retryable=False)


class FixtureSearchProvider:
    """Explicit synthetic search; results remain discovery hints, never evidence."""
    name, version, mode, configured, external = 'fixture', 'fixture-search-v1', 'fixture', True, False
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []
    def search(self, query, *, news_cutoff):
        self.calls.append(query)
        return ProviderResult(deepcopy(self.results.get(query, [])), {'input_tokens':0,'output_tokens':0,'cost':0})


class FixtureLLMProvider:
    """Supply explicit synthetic proposal responses; this performs no inference."""
    name, version, mode, configured, external = 'fixture', 'fixture-llm-v1', 'fixture', True, False
    input_limit, output_limit = 6000, 1500
    def __init__(self, responses=None, *, version=None):
        self.responses = list(responses or [])
        self.calls = []
        if version:
            self.version = version
    def generate(self, payload, *, max_output_tokens):
        self.calls.append(deepcopy(payload))
        response = self.responses.pop(0) if self.responses else {'facts': [], 'assessment': None, 'gaps': ['fixture未提供语义提议']}
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ProviderResult):
            return deepcopy(response)
        return ProviderResult(deepcopy(response), {'input_tokens': min(len(str(payload)), self.input_limit), 'output_tokens': min(len(str(response)), max_output_tokens), 'cost': 0})


class FixtureFulltextProvider:
    name, version, mode, configured, external = 'fixture', 'fixture-fulltext-v1', 'fixture', True, False
    def __init__(self, documents):
        self.documents = documents
        self.calls = []
    def fetch(self, url, *, source_id):
        self.calls.append(url)
        if url not in self.documents:
            raise ProviderFailure('fixture_missing', retryable=False)
        return ProviderResult(deepcopy(self.documents[url]), {'input_tokens':0,'output_tokens':0,'cost':0})


def configured_providers(config, mode):
    """Only none is automatic. Fixture requires explicit offline configuration."""
    settings = config['runtime']['providers']
    search, llm = NoneSearchProvider(), NoneLLMProvider()
    for kind, current, fixture_type in [('search', search, FixtureSearchProvider), ('llm', llm, FixtureLLMProvider)]:
        requested = settings.get(kind, 'none')
        if requested == 'fixture' and mode == 'fixture':
            current = fixture_type()
        elif requested != 'none':
            current.unavailable_reason = kind + '_runtime_adapter_or_authorization_missing'
        if kind == 'search':
            search = current
        else:
            llm = current
    return search, llm
