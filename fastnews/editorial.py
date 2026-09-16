"""Local, evidence-bound editorial workflow; never writes news or selects for users.

Documents are untrusted data. Import functions only parse explicitly supplied
content: no URL retrieval, file paths from document bodies, code execution, LLM
calls, inferred holdings, or fact extraction from prose. Supplied claims are
reported claims with attribution, not independently verified economic facts.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .config import _read
from .metadata import KINDS, PHASES, normalize_url, parse_instant, time_bounds

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ('core_variable', 'coverage', 'linkage', 'magnitude', 'timeliness', 'incrementality')
SNAPSHOT_TYPES = {'index_constituents', 'fund_holdings', 'target_fund_holdings', 'business_segment', 'product_identity'}
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_EXCERPT = 2000


class EditorialError(ValueError):
    """Actionable invalid input; messages deliberately exclude document contents."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _hash(value):
    return hashlib.sha256((value if isinstance(value, str) else _json(value)).encode('utf-8')).hexdigest()


def _id(prefix, value):
    return prefix + _hash(value)[:32]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, name, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EditorialError(name + ' requires nonempty text')
    return value


def _fields(value, allowed, required=(), label='input'):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise EditorialError(label + ': unexpected or missing fields')
    return value


def _ids(value, label):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value) or len(set(value)) != len(value):
        raise EditorialError(label + ' must be unique ID strings')
    return value


def _fixture(conn):
    row = conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
    if not row or row[0] not in {'fixture', 'live'}:
        raise EditorialError('An initialized mode-marked database is required')
    return row[0] == 'fixture'


def load_editorial_config(config_dir=None):
    root = Path(config_dir) if config_dir is not None else ROOT / 'config'
    result = {'profiles': _read(root, 'product_profiles.example.yaml'),
              'scoring': _read(root, 'scoring.v2.yaml'), 'output_policy': _read(root, 'output_policy.example.yaml')}
    scoring = result['scoring']
    weights = scoring.get('component_max_points', {})
    if set(weights) != set(COMPONENTS) or any(type(x) not in (int, float) or x < 0 for x in weights.values()) or sum(weights.values()) != 100:
        raise EditorialError('Scoring needs six nonnegative weights totaling 100')
    if scoring.get('auto_select_threshold') is not None or scoring.get('missing_component_policy') != 'null_total_no_renormalization':
        raise EditorialError('Unsupported scoring policy: no automatic selection or missing-score substitution')
    profiles = result['profiles']
    if {p['code'] for p in profiles['profiles']} != {'513130', '513530', '588710', '517120'}:
        raise EditorialError('Profile configuration must retain all four products')
    policy = result['output_policy']
    if policy.get('auto_publication') is not False or policy.get('source_relabeling_allowed') is not False or policy.get('date_relabeling_allowed') is not False:
        raise EditorialError('Unsupported export policy')
    return result


def _config(config):
    return config.get('editorial') or load_editorial_config()


def _eligible_time(value, cutoff):
    lower, upper = time_bounds(value)
    if lower is None:
        return 'unknown'
    instant = parse_instant(cutoff)
    if lower > instant:
        return 'ineligible'
    return 'eligible' if upper <= instant else 'unknown'


def get_edition(conn, edition_id):
    row = conn.execute('SELECT data_json FROM fn_editions WHERE edition_id=?', (edition_id,)).fetchone()
    if row is None:
        raise EditorialError('Unknown edition_id')
    return json.loads(row[0])


def list_editions(conn):
    return [json.loads(r[0]) for r in conn.execute('SELECT data_json FROM fn_editions ORDER BY created_at DESC,edition_id')]


def create_edition(conn, config, *, product_code, edition_date, news_cutoff,
                   market_cutoffs, edition_id=None, created_by='human'):
    if product_code not in config['products']:
        raise EditorialError('Unknown product code')
    day = date.fromisoformat(edition_date)
    cutoff = parse_instant(news_cutoff)
    if cutoff > datetime.now(timezone.utc):
        raise EditorialError('News cutoff cannot be in the future')
    if not isinstance(market_cutoffs, dict):
        raise EditorialError('market_cutoffs must be a mapping; {} means dates are unknown, never inferred')
    for name, entry in market_cutoffs.items():
        _text(name, 'market')
        _fields(entry, {'date', 'source'}, {'date', 'source'}, 'market cutoff')
        _text(entry['source'], 'market cutoff source')
        if date.fromisoformat(entry['date']) > day:
            raise EditorialError('Market date cannot follow edition date')
    if created_by != 'human':
        raise EditorialError('Edition creation requires explicit human settings')
    settings = _config(config)
    profile = next(p for p in settings['profiles']['profiles'] if p['code'] == product_code)
    history = [json.loads(r[0]) for r in conn.execute('SELECT data_json FROM fn_history WHERE product_code=? ORDER BY history_doc_id', (product_code,))]
    history = [h for h in history if _eligible_time(h['publication_time'], news_cutoff) == 'eligible']
    snapshots = [json.loads(r[0]) for r in conn.execute('SELECT data_json FROM fn_product_snapshots WHERE product_code=? ORDER BY snapshot_id', (product_code,))]
    cutoff_day = cutoff.astimezone(ZoneInfo(config['runtime']['runtime']['timezone'])).date()
    snapshots = [s for s in snapshots if _eligible_time(s['published_at'], news_cutoff) == 'eligible'
                 and date.fromisoformat(s['data_as_of']) <= cutoff_day
                 and (not s.get('effective_at') or _eligible_time(s['effective_at'], news_cutoff) == 'eligible')]
    view = {'product_code': product_code, 'product_name': config['products'][product_code]['name'],
            'edition_date': day.isoformat(), 'news_cutoff': news_cutoff, 'market_cutoffs': market_cutoffs,
            'timezone': config['runtime']['runtime']['timezone'], 'profile_version': settings['profiles']['version'],
            'rule_version': settings['scoring']['version'], 'output_policy_version': settings['output_policy']['version'],
            'history_snapshot_id': _id('history_', [h['history_doc_id'] for h in history]),
            'history_doc_ids': [h['history_doc_id'] for h in history],
            'product_snapshot_ids': [s['snapshot_id'] for s in snapshots], 'profile': profile,
            'scoring': settings['scoring'], 'output_policy': settings['output_policy'],
            'is_fixture': _fixture(conn), 'created_by': created_by}
    eid = edition_id or _id('edition_', view)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', eid):
        raise EditorialError('edition_id must be a safe identifier')
    view['edition_id'] = eid
    existing = conn.execute('SELECT data_json FROM fn_editions WHERE edition_id=?', (eid,)).fetchone()
    if existing:
        if json.loads(existing[0]) != view:
            raise EditorialError('Edition is frozen; use a new edition_id for changed settings or snapshots')
        return view
    with conn:
        conn.execute('INSERT INTO fn_editions VALUES(?,?,?,?,?,?,?)', (eid, product_code, day.isoformat(), news_cutoff, _json(view), _now(), int(view['is_fixture'])))
    return view


def _reject_secrets(path, content):
    if path.name.lower().startswith('.env') or path.suffix.lower() in {'.pem', '.key', '.p12'}:
        raise EditorialError('Credential files are not importable materials')
    if re.search(r'(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[=:]\s*[\"\x27]?[A-Za-z0-9_/-]{12,}', content) or re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content):
        raise EditorialError('Possible credential material detected; import refused')


def _read_authorized(path, authorized):
    if authorized is not True:
        raise EditorialError('Local file import requires explicit authorization')
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in {'.json', '.md', '.txt'}:
        raise EditorialError('Supported local materials are UTF-8 JSON, Markdown or text')
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise EditorialError('Material is missing or exceeds the 4 MiB local import limit')
    try:
        raw = path.read_bytes()
        content = raw.decode('utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise EditorialError('Cannot read authorized UTF-8 material') from exc
    _reject_secrets(path, content)
    if path.suffix.lower() == '.json':
        try:
            def unique(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError('duplicate field')
                    result[k] = v
                return result
            payload = json.loads(content, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
        except ValueError as exc:
            raise EditorialError('Invalid JSON material') from exc
        if not isinstance(payload, dict):
            raise EditorialError('JSON material must be an object')
    else:
        payload = {'title': path.stem, 'content': content}
    return path, raw, payload


def _document(conn, path, raw_hash, payload, privacy_class):
    if privacy_class not in {'public', 'private_local'}:
        raise EditorialError('privacy_class must be public or private_local')
    declared_fixture = payload.get('is_fixture', False)
    if type(declared_fixture) is not bool or declared_fixture != _fixture(conn):
        raise EditorialError('Fixture/live mismatch: synthetic material requires an explicit is_fixture=true and a separate fixture database')
    content = _text(payload.get('content'), 'document content')
    if len(content.encode('utf-8')) > MAX_FILE_BYTES:
        raise EditorialError('Document exceeds the local material limit')
    _reject_secrets(Path(str(path)), content)
    document_id = _id('document_', [str(path), raw_hash, privacy_class])
    metadata = {key: value for key, value in payload.items() if key not in {'content', 'facts', 'members', 'used_claims'}}
    conn.execute('INSERT OR IGNORE INTO fn_documents VALUES(?,?,?,?,?,?,?,?)',
                 (document_id, str(path), raw_hash, content, _json(metadata), _now(), privacy_class, int(declared_fixture)))
    return document_id, content


EVIDENCE_FIELDS = {'is_fixture', 'title', 'content', 'source_id', 'source_url', 'source_type', 'origin_role',
                   'declared_source', 'actual_source', 'attribution_chain', 'first_published_at', 'report_published_at',
                   'source_revision_at', 'event', 'facts', 'note'}
FACT_FIELDS = {'statement', 'kind', 'subject', 'action', 'object', 'phase', 'observed_value', 'metric', 'unit',
               'currency', 'period_start', 'period_end', 'benchmark', 'metric_scope', 'market', 'first_published_at',
               'effective_at', 'occurred_at', 'qualification', 'excerpt', 'locator', 'conflict_key'}


def _make_evidence(conn, document_id, payload, content, excerpt, *, declared_source=None,
                   actual_access='user_authorized_local_file', actual_retrieved_at=None):
    _text(excerpt, 'evidence excerpt')
    if len(excerpt) > MAX_EXCERPT or excerpt not in content:
        raise EditorialError('Evidence excerpt must locate exactly in the supplied version and be at most 2000 characters')
    start = content.index(excerpt)
    source_id = payload.get('source_id')
    if source_id and not conn.execute('SELECT 1 FROM fn_sources WHERE source_id=?', (source_id,)).fetchone():
        raise EditorialError('Unknown evidence source_id')
    url = normalize_url(payload['source_url']) if payload.get('source_url') else None
    stype = payload.get('source_type', 'user_document')
    if stype not in {'user_document', 'original_publication', 'media', 'search_snippet', 'user_text', 'public_page'}:
        raise EditorialError('Unknown source_type')
    if stype == 'media' and str(payload.get('actual_source', '')).casefold() == 'wind':
        raise EditorialError('Media attribution cannot be relabeled as direct Wind data')
    origin = payload.get('origin_role', 'unknown')
    if origin not in {'unknown', 'original_announcement', 'self_statistics', 'direct_interview', 'republication', 'reported', 'user_supplied'}:
        raise EditorialError('Unknown origin_role')
    chain = payload.get('attribution_chain', [])
    if not isinstance(chain, list) or any(not isinstance(x, str) or not x for x in chain):
        raise EditorialError('attribution_chain must be source labels; no executable entries')
    for field in ('first_published_at', 'report_published_at', 'source_revision_at'):
        time_bounds(payload.get(field))
    doc = conn.execute('SELECT * FROM fn_documents WHERE document_id=?', (document_id,)).fetchone()
    entry = {'document_id': document_id, 'source_id': source_id, 'source_url': url, 'source_type': stype,
             'origin_role': origin, 'origin_role_status': 'unknown_unverified' if actual_access == 'public_http' else 'user_declared', 'attribution_chain': chain,
             'actual_access': actual_access, 'actual_source': source_id or (urlsplit(url).hostname if url else None) or '用户提供材料',
             'supplied_source_label': payload.get('actual_source'),
             'declared_source': declared_source or payload.get('declared_source'), 'primary_source_found': False,
             'first_published_at': payload.get('first_published_at'), 'report_published_at': payload.get('report_published_at'),
             'source_revision_at': payload.get('source_revision_at'), 'retrieved_at': actual_retrieved_at or doc['imported_at'],
             'snapshot_hash': _hash(content), 'file_hash': doc['content_hash'], 'local_path': doc['local_path'],
             'locator': {'type': 'character_range', 'start': start, 'end': start + len(excerpt)}, 'excerpt': excerpt,
             'access_status': 'search_snippet_only' if stype == 'search_snippet' else 'fulltext_read' if actual_access == 'public_http' else 'local_content_read',
             'is_fixture': bool(doc['is_fixture']), 'privacy_class': doc['privacy_class']}
    eid = _id('evidence_', entry | {'retrieved_at': None})
    entry['evidence_id'] = eid
    conn.execute('INSERT OR IGNORE INTO fn_evidence VALUES(?,?,?,?)', (eid, document_id, source_id, _json(entry)))
    return entry


def record_public_evidence(conn, *, edition_id, event_id, source_id, source_url, title, content,
                           retrieved_at, first_published_at=None, source_revision_at=None,
                           is_fixture=False, report_published_at=None):
    """Persist content actually acquired by the bounded HTTP provider.

    This function performs no network call. The provider must already have obeyed
    robots, host/IP/redirect policy, access restrictions, and response size caps.
    A page is a read source, not automatically the primary origin of every claim.
    """
    edition = get_edition(conn, edition_id)
    if type(is_fixture) is not bool or is_fixture != edition['is_fixture']:
        raise EditorialError('Public evidence mode differs from Edition')
    if not conn.execute('SELECT 1 FROM fn_events WHERE event_id=?', (event_id,)).fetchone():
        raise EditorialError('Unknown public evidence event_id')
    source = conn.execute('SELECT config_json FROM fn_sources WHERE source_id=?', (source_id,)).fetchone()
    if not source:
        raise EditorialError('Unknown public evidence source_id')
    source_config = json.loads(source[0])
    canonical = normalize_url(source_url)
    registered_hosts = {urlsplit(source_config[key]).hostname for key in ('home_url', 'discovery_url') if source_config.get(key)}
    host = urlsplit(canonical).hostname
    already_related = conn.execute('SELECT 1 FROM fn_article_sources s JOIN fn_event_articles ea USING(version_id) WHERE ea.event_id=? AND s.source_id=? AND s.source_url=?',
                                   (event_id, source_id, canonical)).fetchone()
    if host not in registered_hosts and not already_related:
        raise EditorialError('Public URL is not the configured source host or a registered article of this event')
    parse_instant(retrieved_at)
    for value in (first_published_at, source_revision_at, report_published_at):
        time_bounds(value)
    _text(content, 'public page content')
    _text(title, 'public page title')
    if len(content.encode('utf-8')) > MAX_FILE_BYTES:
        raise EditorialError('Public content exceeds local snapshot limit')
    _reject_secrets(Path('public-page.txt'), content)
    _reject_secrets(Path('public-url.txt'), canonical)
    database_path = next(r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main')
    directory = (Path(database_path).resolve().parent if database_path else ROOT / 'data') / 'evidence_cache' / ('fixture' if is_fixture else 'live')
    digest = _hash(content)
    snapshot_path = directory / (_hash(canonical)[:12] + '-' + digest + '.txt')
    directory.mkdir(parents=True, exist_ok=True)
    if snapshot_path.exists() and snapshot_path.read_text(encoding='utf-8') != content:
        raise EditorialError('Existing public evidence snapshot failed content validation')
    if not snapshot_path.exists():
        with snapshot_path.open('x', encoding='utf-8', newline='') as handle:
            handle.write(content)
    payload = {'is_fixture': is_fixture, 'title': title, 'content': content, 'source_id': source_id,
               'source_url': canonical, 'source_type': 'public_page', 'origin_role': 'unknown',
               'first_published_at': first_published_at, 'source_revision_at': source_revision_at,
               'report_published_at': report_published_at}
    with conn:
        # Include source identity in document ID even when two sources share text.
        doc_id, _ = _document(conn, snapshot_path, digest, payload, 'public')
        ev = _make_evidence(conn, doc_id, payload, content, content[:min(300, len(content))],
                            actual_access='public_http', actual_retrieved_at=retrieved_at)
        conn.execute('INSERT OR IGNORE INTO fn_edition_evidence VALUES(?,?,?)', (edition_id, event_id, ev['evidence_id']))
    return {'evidence_id': ev['evidence_id'], 'document_id': doc_id, 'event_id': event_id,
            'actual_access': 'public_http', 'access_status': 'fulltext_read', 'is_fixture': is_fixture}


def add_fact_from_evidence(conn, *, edition_id, event_id, evidence_id, statement, kind,
                           actor_type, first_published_at=None, **fields):
    """Save a human-highlighted exact quotation using its original source chain."""
    if actor_type != 'human':
        raise EditorialError('A source excerpt becomes a saved fact only through an explicit human action')
    edition = get_edition(conn, edition_id)
    if not conn.execute('SELECT 1 FROM fn_edition_evidence WHERE edition_id=? AND event_id=? AND evidence_id=?', (edition_id, event_id, evidence_id)).fetchone():
        raise EditorialError('Evidence must belong to this Edition and event')
    _fields(fields, FACT_FIELDS - {'statement', 'kind', 'excerpt', 'locator', 'first_published_at'}, label='excerpt fact')
    if kind not in KINDS or fields.get('phase', 'unknown') not in PHASES:
        raise EditorialError('Unsupported kind or event phase')
    _text(statement, 'statement')
    if len(statement) > MAX_EXCERPT:
        raise EditorialError('Select a source excerpt of at most 2000 characters')
    original = _evidence(conn, evidence_id)
    body = conn.execute('SELECT content FROM fn_documents WHERE document_id=?', (original['document_id'],)).fetchone()[0]
    if statement not in body:
        raise EditorialError('The selected statement does not occur exactly in this source version')
    for name in ('effective_at', 'occurred_at', 'period_start', 'period_end'):
        time_bounds(fields.get(name))
    time_bounds(first_published_at)
    value = fields.get('observed_value')
    if value is not None:
        if type(value) not in (str, int, float) or isinstance(value, float) and not math.isfinite(value) or str(value) not in statement:
            raise EditorialError('Original value must be finite and occur in the selected excerpt')
        if not fields.get('unit') or fields.get('metric_scope') not in {'market', 'product', 'company', 'index', 'industry'}:
            raise EditorialError('A numeric excerpt requires unit and metric_scope')
    start = body.index(statement)
    ev = dict(original)
    ev.update(locator={'type': 'character_range', 'start': start, 'end': start + len(statement)}, excerpt=statement)
    ev.pop('evidence_id')
    ev['evidence_id'] = _id('evidence_', ev | {'retrieved_at': None})
    fact = {key: fields.get(key) for key in FACT_FIELDS - {'excerpt', 'locator'}}
    fact.update(statement=statement, kind=kind, event_id=event_id, phase=fields.get('phase', 'unknown'),
                first_published_at=first_published_at or original['first_published_at'], evidence_refs=[ev['evidence_id']],
                claim_origin='human_source_excerpt', exact_statement_located=True,
                evidence_state='pending' if original['access_status'] == 'search_snippet_only' or kind == 'interpretation' else 'reported',
                export_class='requires_policy_check', is_fixture=edition['is_fixture'])
    fingerprint = {key: fact.get(key) for key in ('statement', 'subject', 'action', 'object', 'phase', 'metric', 'observed_value', 'unit', 'currency', 'period_start', 'period_end', 'kind')}
    fact['claim_fingerprint'] = _hash(fingerprint)
    fact['fact_id'] = _id('fact_', [event_id, fact['claim_fingerprint'], ev['evidence_id']])
    with conn:
        conn.execute('INSERT OR IGNORE INTO fn_evidence VALUES(?,?,?,?)', (ev['evidence_id'], ev['document_id'], ev['source_id'], _json(ev)))
        conn.execute('INSERT OR IGNORE INTO fn_edition_evidence VALUES(?,?,?)', (edition_id, event_id, ev['evidence_id']))
        conn.execute('INSERT OR IGNORE INTO fn_facts VALUES(?,?,?,?)', (fact['fact_id'], event_id, fact['claim_fingerprint'], _json(fact)))
        conn.execute('INSERT OR IGNORE INTO fn_fact_evidence VALUES(?,?)', (fact['fact_id'], ev['evidence_id']))
        conn.execute('INSERT OR IGNORE INTO fn_edition_facts VALUES(?,?)', (edition_id, fact['fact_id']))
    return {'fact_id': fact['fact_id'], 'evidence_id': ev['evidence_id'], 'event_id': event_id,
            'actual_access': ev['actual_access'], 'is_fixture': fact['is_fixture']}


def import_evidence(conn, path, *, authorized, edition_id, event_id=None,
                    declared_source=None, privacy_class='private_local'):
    local, raw, payload = _read_authorized(path, authorized)
    return import_evidence_payload(conn, payload, edition_id=edition_id, event_id=event_id,
                                   actual_source_path=str(local), privacy_class=privacy_class,
                                   declared_source=declared_source, raw_hash=hashlib.sha256(raw).hexdigest())


def import_evidence_payload(conn, payload, *, edition_id, event_id=None, actual_source_path='user:explicit-paste',
                            privacy_class='private_local', declared_source=None, raw_hash=None):
    """Import explicitly submitted text/claims; caller is responsible for the user action.

    Model providers must not invoke this action as human authorization. Facts need
    explicit supplied claims with verbatim excerpts; prose alone stays evidence.
    """
    edition = get_edition(conn, edition_id)
    _fields(payload, EVIDENCE_FIELDS, {'title', 'content'}, 'evidence document')
    _text(payload['title'], 'title')
    claims = payload.get('facts', [])
    if not isinstance(claims, list) or len(claims) > 100:
        raise EditorialError('facts must be a list of at most 100 explicitly supplied claims')
    if event_id is not None and not conn.execute('SELECT 1 FROM fn_events WHERE event_id=?', (event_id,)).fetchone():
        raise EditorialError('Unknown event_id')
    event = payload.get('event')
    if event is not None:
        _fields(event, {'subject', 'action', 'object', 'phase'}, {'subject', 'action', 'object', 'phase'}, 'event')
        for field in ('subject', 'action', 'object'):
            _text(event[field], field)
        if event['phase'] not in PHASES:
            raise EditorialError('Unknown event phase')
    if event_id is None and event is None:
        event = {'subject': 'user_supplied', 'action': 'document_import', 'object': _hash(payload['content']), 'phase': 'unknown'}
    with conn:
        document_id, content = _document(conn, actual_source_path, raw_hash or _hash(payload), payload, privacy_class)
        if event_id is None:
            signature = 'user_supplied:' + _hash(event)
            event_id = _id('event_', signature)
            conn.execute('INSERT OR IGNORE INTO fn_events VALUES(?,?,?,?,?)', (event_id, signature, event['phase'], None, _now()))
        evs, facts = [], []
        if not claims:
            evs.append(_make_evidence(conn, document_id, payload, content, content[:min(300, len(content))], declared_source=declared_source))
        for claim in claims:
            _fields(claim, FACT_FIELDS, {'statement', 'kind', 'excerpt'}, 'supplied fact')
            for field in ('statement', 'excerpt'):
                _text(claim[field], field)
            if claim['kind'] not in KINDS or claim.get('phase', 'unknown') not in PHASES:
                raise EditorialError('Unsupported fact kind or phase')
            for field in ('first_published_at', 'effective_at', 'occurred_at', 'period_start', 'period_end'):
                time_bounds(claim.get(field))
            if claim.get('observed_value') is not None:
                if type(claim['observed_value']) not in (str, int, float) or not claim.get('unit') or claim.get('metric_scope') not in {'market', 'product', 'company', 'index', 'industry'}:
                    raise EditorialError('Numerical facts require original value, unit and metric_scope')
                if isinstance(claim['observed_value'], float) and not math.isfinite(claim['observed_value']):
                    raise EditorialError('Numerical facts must be finite')
            ev = _make_evidence(conn, document_id, payload, content, claim['excerpt'], declared_source=declared_source)
            if claim.get('observed_value') is not None and str(claim['observed_value']) not in claim['excerpt']:
                raise EditorialError('Original numerical value must locate in the evidence excerpt')
            if claim.get('locator') is not None and claim['locator'] != ev['locator']:
                raise EditorialError('Supplied locator does not identify the evidence excerpt')
            first_pub = claim.get('first_published_at') or payload.get('first_published_at')
            if claim.get('first_published_at') and payload.get('first_published_at'):
                if time_bounds(claim['first_published_at'])[0] < time_bounds(payload['first_published_at'])[0]:
                    # An earlier fact needs its own earlier evidence; this page alone cannot backdate it.
                    first_pub = claim['first_published_at']
            fact = {key: claim.get(key) for key in FACT_FIELDS - {'excerpt', 'locator'}}
            fact.update(event_id=event_id, first_published_at=first_pub, evidence_refs=[ev['evidence_id']],
                        phase=claim.get('phase', 'unknown'), claim_origin='explicit_user_supplied',
                        evidence_state='pending' if ev['access_status'] == 'search_snippet_only' or claim['kind'] == 'interpretation' or claim['statement'] not in claim['excerpt'] else 'reported',
                        exact_statement_located=claim['statement'] in claim['excerpt'],
                        export_class='requires_policy_check', is_fixture=edition['is_fixture'])
            fingerprint_fields = {key: fact.get(key) for key in ('subject', 'action', 'object', 'phase', 'metric', 'observed_value', 'unit', 'currency', 'period_start', 'period_end', 'kind')}
            fingerprint_fields['statement'] = claim['statement']
            fact['claim_fingerprint'] = _hash(fingerprint_fields)
            fact['fact_id'] = _id('fact_', [event_id, fact['claim_fingerprint'], ev['evidence_id']])
            conn.execute('INSERT OR IGNORE INTO fn_facts VALUES(?,?,?,?)', (fact['fact_id'], event_id, fact['claim_fingerprint'], _json(fact)))
            conn.execute('INSERT OR IGNORE INTO fn_fact_evidence VALUES(?,?)', (fact['fact_id'], ev['evidence_id']))
            conn.execute('INSERT OR IGNORE INTO fn_edition_facts VALUES(?,?)', (edition_id, fact['fact_id']))
            facts.append(fact['fact_id'])
            evs.append(ev)
        for ev in evs:
            conn.execute('INSERT OR IGNORE INTO fn_edition_evidence VALUES(?,?,?)', (edition_id, event_id, ev['evidence_id']))
    return {'document_id': document_id, 'event_id': event_id, 'evidence_ids': list(dict.fromkeys(e['evidence_id'] for e in evs)),
            'fact_ids': facts, 'is_fixture': edition['is_fixture'], 'fact_extraction': 'explicit_supplied_claims_only',
            'note': '已读取授权材料；用户提供表述保留归属，未自动核实原始出处。' if facts else '已读取材料，尚无结构化事实；未调用模型抽取。'}


def import_product_data(conn, path, *, authorized, product_code, declared_source=None, privacy_class='private_local'):
    local, raw, payload = _read_authorized(path, authorized)
    fields = EVIDENCE_FIELDS - {'facts', 'event'} | {'product_code', 'snapshot_type', 'data_as_of', 'published_at', 'effective_at', 'members', 'identity', 'excerpt'}
    _fields(payload, fields, {'content', 'title', 'snapshot_type', 'data_as_of', 'published_at', 'excerpt'}, 'product snapshot')
    if not conn.execute('SELECT 1 FROM fn_products WHERE code=?', (product_code,)).fetchone() or payload.get('product_code', product_code) != product_code:
        raise EditorialError('Unknown or mismatched product_code')
    if payload['snapshot_type'] not in SNAPSHOT_TYPES:
        raise EditorialError('Unsupported product snapshot type')
    date.fromisoformat(payload['data_as_of'])
    time_bounds(payload['published_at'])
    if payload['published_at'] is None:
        raise EditorialError('Product snapshots require published_at; unknown structure remains a gap')
    time_bounds(payload.get('effective_at'))
    members = payload.get('members', [])
    if not isinstance(members, list):
        raise EditorialError('members must be a list')
    for member in members:
        _fields(member, {'name', 'weight', 'unit', 'business', 'excerpt'}, {'name', 'excerpt'}, 'snapshot member')
        if member['name'] not in payload['content'] or member['excerpt'] not in payload['content']:
            raise EditorialError('Snapshot members need a matching source excerpt')
        if member.get('weight') is not None:
            if type(member['weight']) not in (int, float) or not 0 <= member['weight'] <= 100 or member.get('unit') != '%':
                raise EditorialError('Snapshot weight requires a numeric percentage in [0,100]')
            if str(member['weight']) not in member['excerpt']:
                raise EditorialError('Snapshot weight must locate in the supplied member excerpt')
    with conn:
        doc_id, content = _document(conn, local, hashlib.sha256(raw).hexdigest(), payload, privacy_class)
        ev_payload = {key: value for key, value in payload.items() if key in EVIDENCE_FIELDS}
        ev_payload['first_published_at'] = payload['published_at']
        ev = _make_evidence(conn, doc_id, ev_payload, content, payload['excerpt'], declared_source=declared_source)
        entry = {key: payload.get(key) for key in ('snapshot_type', 'data_as_of', 'published_at', 'effective_at', 'members', 'identity', 'title')}
        entry.update(product_code=product_code, document_id=doc_id, evidence_refs=[ev['evidence_id']],
                     structure_status='user_supplied_not_independently_verified', is_fixture=_fixture(conn))
        entry['snapshot_id'] = _id('snapshot_', entry)
        conn.execute('INSERT OR IGNORE INTO fn_product_snapshots VALUES(?,?,?,?)', (entry['snapshot_id'], product_code, doc_id, _json(entry)))
    return entry


def import_history(conn, path, *, authorized, product_code, title=None, publication_time=None,
                   completeness=None, privacy_class='private_local'):
    local, raw, payload = _read_authorized(path, authorized)
    fields = {'is_fixture', 'title', 'content', 'product_code', 'publication_time', 'completeness', 'used_claims', 'conclusions', 'editor_tag', 'note'}
    _fields(payload, fields, {'content', 'title'}, 'history document')
    if not conn.execute('SELECT 1 FROM fn_products WHERE code=?', (product_code,)).fetchone() or payload.get('product_code', product_code) != product_code:
        raise EditorialError('Unknown or mismatched history product_code')
    publication = publication_time or payload.get('publication_time')
    if publication is None:
        raise EditorialError('History import requires explicit publication time')
    time_bounds(publication)
    quality = completeness or payload.get('completeness', 'partial_text')
    if quality not in {'title_only', 'partial_text', 'full_text'}:
        raise EditorialError('Unsupported history completeness')
    claims = payload.get('used_claims', [])
    if not isinstance(claims, list) or any(not isinstance(c, str) or not c or c not in payload['content'] for c in claims):
        raise EditorialError('History used_claims must locate in the actual imported content')
    if quality == 'title_only' and claims:
        raise EditorialError('Title-only history cannot assert previously used facts')
    with conn:
        doc_id, _ = _document(conn, local, hashlib.sha256(raw).hexdigest(), payload, privacy_class)
        entry = {'document_id': doc_id, 'product_code': product_code, 'title': title or payload['title'],
                 'publication_time': publication, 'completeness': quality, 'used_claims': claims,
                 'conclusions': payload.get('conclusions', []), 'editor_tag': payload.get('editor_tag'),
                 'is_fixture': _fixture(conn)}
        entry['history_doc_id'] = _id('historydoc_', entry)
        conn.execute('INSERT OR IGNORE INTO fn_history VALUES(?,?,?,?,?)', (entry['history_doc_id'], product_code, doc_id, publication, _json(entry)))
    return entry


def _evidence(conn, evidence_id):
    row = conn.execute('SELECT data_json FROM fn_evidence WHERE evidence_id=?', (evidence_id,)).fetchone()
    if not row:
        raise EditorialError('Unknown evidence_id')
    entry = json.loads(row[0])
    doc = conn.execute('SELECT content,content_hash FROM fn_documents WHERE document_id=?', (entry['document_id'],)).fetchone()
    locator = entry['locator']
    if not doc or _hash(doc['content']) != entry['snapshot_hash'] or doc['content_hash'] != entry['file_hash'] or doc['content'][locator['start']:locator['end']] != entry['excerpt']:
        raise EditorialError('Stored evidence snapshot or locator failed integrity validation')
    return entry


def _fact_state(fact, evidence, edition):
    state = _eligible_time(fact['first_published_at'], edition['news_cutoff'])
    reasons = []
    if state != 'eligible':
        reasons.append('首次公开时间在截点外' if state == 'ineligible' else '首次公开时间或时刻待核实')
    for ev in evidence:
        doc_state = _eligible_time(ev['first_published_at'], edition['news_cutoff'])
        if doc_state == 'ineligible':
            state = 'ineligible'
            reasons.append('所引版本在截点后才公开，不能用较早声明回填历史证据')
        if doc_state == 'unknown' and state != 'ineligible':
            state = 'unknown'
            reasons.append('证据版本公开时间不完整')
        if ev['source_revision_at'] and _eligible_time(ev['source_revision_at'], edition['news_cutoff']) != 'eligible':
            if state != 'ineligible':
                state = 'unknown'
            reasons.append('所读页面后来修订，缺少截点内版本')
    market = fact.get('market')
    if market:
        entry = edition['market_cutoffs'].get(market)
        end_value = fact.get('period_end')
        end = time_bounds(end_value)[0]
        if not entry or not end:
            if state != 'ineligible':
                state = 'unknown'
            reasons.append('行情市场截点或统计期缺失')
        elif (date.fromisoformat(end_value) if len(end_value) == 10 else end.astimezone(ZoneInfo(edition['timezone'])).date()) > date.fromisoformat(entry['date']):
            state = 'ineligible'
            reasons.append('统计期超过显式行情截点')
    evidence_state = 'out_of_cutoff' if state == 'ineligible' else 'pending' if state == 'unknown' else fact['evidence_state']
    quantitative_metric = re.search(r'\d', fact['statement']) and re.search(
        r'规模|份额|净(?:申购|流入|流出|赎回|买额|买入)|成交(?:额|量)|换手率|股息率|市盈率|市净率|收益率|涨幅', fact['statement'])
    if quantitative_metric and (fact.get('observed_value') is None or not fact.get('metric') or not fact.get('metric_scope') or not fact.get('unit')):
        reasons.append('数值指标尚未标注原始值、指标类型、单位及市场/产品范围，不能绕过产品输出规则')
        if evidence_state != 'out_of_cutoff':
            evidence_state = 'pending'
    return state, evidence_state, reasons


def edition_facts(conn, edition_id):
    edition = get_edition(conn, edition_id)
    rows = conn.execute('SELECT f.data_json FROM fn_facts f JOIN fn_edition_facts ef USING(fact_id) WHERE ef.edition_id=? ORDER BY f.fact_id', (edition_id,))
    facts = []
    for row in rows:
        fact = json.loads(row[0])
        evidence = [_evidence(conn, eid) for eid in fact['evidence_refs']]
        state, evidence_state, reasons = _fact_state(fact, evidence, edition)
        if not fact.get('exact_statement_located'):
            reasons.append('用户表述未在该证据片段逐字定位，需核验改写是否受原文支持')
        fact.update(cutoff_state=state, evidence_state=evidence_state, gaps=reasons, evidence=evidence)
        # A repeated media chain is one attributed origin, never independent validation.
        origins = {tuple(e['attribution_chain']) if e['attribution_chain'] else (e['source_url'] or e['document_id'],) for e in evidence}
        fact['attributed_origin_count'] = len(origins)
        fact['independently_verified'] = False
        facts.append(fact)
    groups = {}
    for fact in facts:
        key = fact.get('conflict_key')
        if not key and fact.get('metric') and fact.get('subject') and fact.get('period_end'):
            key = _json([fact['event_id'], fact['subject'], fact['metric'], fact.get('metric_scope'), fact.get('unit'), fact.get('currency'), fact.get('period_start'), fact['period_end'], fact['kind']])
        if key and fact.get('observed_value') is not None:
            groups.setdefault(key, []).append(fact)
    for members in groups.values():
        if len({_json(m['observed_value']) for m in members}) > 1:
            for fact in members:
                fact['evidence_state'] = 'conflicted'
                fact['gaps'].append('同指标/统计期存在不同量值；保留所有版本，待人工核实')
    return facts


def compare_history(conn, edition_id, facts):
    edition = get_edition(conn, edition_id)
    histories = []
    for hid in edition['history_doc_ids']:
        row = conn.execute('SELECT h.data_json,d.content FROM fn_history h JOIN fn_documents d USING(document_id) WHERE history_doc_id=?', (hid,)).fetchone()
        if row:
            histories.append((json.loads(row[0]), row[1]))
    if not histories:
        return {'status': 'unknown', 'difference': '未导入截点前历史稿件，无法判断已发稿重复。', 'history_completeness': 'none', 'previous_doc_refs': []}
    granularities = {h['completeness'] for h, _ in histories}
    completeness = next(x for x in ('title_only', 'partial_text', 'full_text') if x in granularities)
    if completeness == 'title_only':
        return {'status': 'unknown', 'difference': '存在仅标题稿件，只能比较主题；正文事实是否已用仍未知。',
                'history_completeness': 'title_only', 'previous_doc_refs': [h['history_doc_id'] for h, _ in histories]}
    matched, refs = set(), set()
    for fact in facts:
        for history, content in histories:
            if fact['statement'] in history['used_claims'] or fact['statement'] in content:
                matched.add(fact['fact_id'])
                refs.add(history['history_doc_id'])
    if facts and len(matched) == len(facts):
        status, difference = 'repeated', '所有当前表述均能在已导入历史正文中逐字定位；未据此判断事件其他进展。'
    elif matched:
        status, difference = 'partial', f'{len(matched)} 条表述与历史正文重合；其余表述尚未做语义撞题核验。'
    else:
        status, difference = 'unknown', '未发现逐字重合；未做语义比较，不能据此确认不撞题。'
    return {'status': status, 'difference': difference, 'history_completeness': completeness,
            'previous_doc_refs': sorted(refs) if refs else [h['history_doc_id'] for h, _ in histories]}


def calculate_score(components, weights=None):
    weights = weights or dict(zip(COMPONENTS, (20, 10, 10, 25, 20, 15)))
    if set(components) != set(COMPONENTS) or set(weights) != set(COMPONENTS):
        raise EditorialError('All six scoring components are required')
    levels = [components[k]['level'] if isinstance(components[k], dict) else components[k] for k in COMPONENTS]
    if any(level is not None and (type(level) is not int or not 0 <= level <= 4) for level in levels):
        raise EditorialError('Score level must be integer 0..4 or null')
    if any(level is None for level in levels):
        return {'raw_total': None, 'display_total': None}
    raw = sum(Decimal(level) * Decimal(str(weights[key])) / 4 for key, level in zip(COMPONENTS, levels))
    return {'raw_total': float(raw), 'display_total': int(raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP))}


def _event_title(conn, event_id):
    row = conn.execute('SELECT v.title FROM fn_article_versions v JOIN fn_event_articles ea USING(version_id) WHERE ea.event_id=? ORDER BY v.rowid DESC LIMIT 1', (event_id,)).fetchone()
    if row:
        return row[0]
    row = conn.execute('SELECT data_json FROM fn_facts WHERE event_id=? ORDER BY fact_id LIMIT 1', (event_id,)).fetchone()
    return json.loads(row[0])['statement'] if row else '用户导入材料（尚无结构化事实）'


def _base_candidate(conn, config, edition_id, event_id):
    edition = get_edition(conn, edition_id)
    if not conn.execute('SELECT 1 FROM fn_events WHERE event_id=?', (event_id,)).fetchone():
        raise EditorialError('Unknown event_id')
    facts = [f for f in edition_facts(conn, edition_id) if f['event_id'] == event_id]
    novelty = compare_history(conn, edition_id, facts)
    states = {f['evidence_state'] for f in facts}
    state = next((x for x in ('conflicted', 'reported', 'verified', 'pending', 'out_of_cutoff') if x in states), 'pending')
    cutoff = 'eligible' if any(f['cutoff_state'] == 'eligible' for f in facts) else 'ineligible' if facts and all(f['cutoff_state'] == 'ineligible' for f in facts) else 'unknown'
    dates = [f['first_published_at'] for f in facts if f.get('first_published_at')]
    gaps = list(dict.fromkeys(gap for fact in facts for gap in fact['gaps']))
    gaps += ['未做语义评分，分项未知保持 null', novelty['difference']]
    if not edition['market_cutoffs']:
        gaps.append('行情截点未知；涉及市场数据时须显式给定日期和来源，未推算交易日历')
    if not facts:
        gaps.append('仅有元数据或原文材料，尚无逐条可定位的事实')
    if not edition['product_snapshot_ids']:
        gaps.append('缺产品身份/结构依据；路由词表不视为持仓证据')
    candidate = {'schema_version': '2.0', 'candidate_id': '', 'edition_id': edition_id, 'run_id': 'none',
                 'product_code': edition['product_code'], 'event_id': event_id, 'is_fixture': edition['is_fixture'],
                 'headline': _event_title(conn, event_id), 'first_published_at': min(dates, key=lambda x: time_bounds(x)[0]) if dates else None,
                 'evidence_state': state, 'cutoff_state': cutoff, 'fact_refs': [f['fact_id'] for f in facts],
                 'evidence_refs': list(dict.fromkeys(e for f in facts for e in f['evidence_refs'])),
                 'product_relation': {'variable': ' / '.join(edition['profile']['observation_variables']),
                                      'explanation': '以上为产品观察变量；本事件作用机制、范围及结构关系尚待核验。',
                                      'scope': 'unknown', 'profile_refs': [], 'fact_refs': []},
                 'score_components': {key: {'level': None, 'rationale': '资料或语义评估未完成', 'evidence_refs': [], 'profile_refs': []} for key in COMPONENTS},
                 'score_total': None, 'score_version': edition['rule_version'], 'readiness': 'needs_evidence',
                 'possible_roles': [], 'suggested_group': 'out_of_cutoff' if cutoff == 'ineligible' else 'needs_evidence',
                 'novelty': novelty, 'gaps': gaps, 'sentiment': 'unclear', 'policy_flags': []}
    return candidate


def _validate_schema(value, schema, label='candidate'):
    """Validate the bundled JSON Schema subset without executing external schemas."""
    types = schema.get('type')
    if types:
        types = [types] if isinstance(types, str) else types
        matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
                   'string': isinstance(value, str), 'boolean': type(value) is bool,
                   'integer': type(value) is int, 'number': type(value) in (int, float), 'null': value is None}
        if not any(matches.get(t, False) for t in types):
            raise EditorialError(label + ': invalid schema type')
    if 'const' in schema and value != schema['const'] or 'enum' in schema and value not in schema['enum']:
        raise EditorialError(label + ': invalid schema enum')
    if type(value) in (int, float) and (value < schema.get('minimum', float('-inf')) or value > schema.get('maximum', float('inf'))):
        raise EditorialError(label + ': value out of range')
    if isinstance(value, str) and 'pattern' in schema and not re.search(schema['pattern'], value):
        raise EditorialError(label + ': pattern mismatch')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value) or schema.get('additionalProperties') is False and set(value) - set(properties):
            raise EditorialError(label + ': unexpected or missing schema fields')
        for key, child in value.items():
            if key in properties:
                _validate_schema(child, properties[key], label + '.' + key)
    if isinstance(value, list) and 'items' in schema:
        for child in value:
            _validate_schema(child, schema['items'], label + '[]')


def _finish_candidate(candidate):
    identity = dict(candidate)
    identity.pop('candidate_id', None)
    candidate['candidate_id'] = _id('candidate_', identity)
    schema = json.loads((ROOT / 'contracts/candidate.schema.json').read_text(encoding='utf-8'))
    _validate_schema(candidate, schema)
    _json(candidate)  # reject nonfinite values, including NaN hidden in optional output
    return candidate


def validate_assessment_proposal(conn, config, *, edition_id, event_id, proposal):
    allowed = {'headline', 'product_relation', 'score_components', 'readiness', 'possible_roles', 'sentiment', 'policy_flags', 'gaps', 'novelty'}
    _fields(proposal, allowed, label='assessment proposal (human decision and evidence-state fields are forbidden)')
    edition = get_edition(conn, edition_id)
    candidate = _base_candidate(conn, config, edition_id, event_id)
    facts = set(candidate['fact_refs'])
    evidence = set(candidate['evidence_refs'])
    profiles = set(edition['product_snapshot_ids'])
    original_novelty = candidate['novelty']
    original_gaps = candidate['gaps']
    candidate.update(deepcopy(proposal))
    candidate['gaps'] = list(dict.fromkeys(original_gaps + candidate['gaps']))
    # Validate the contract before reading nested proposal keys.
    _finish_candidate(candidate)
    relation = candidate['product_relation']
    if set(relation['fact_refs']) - facts or set(relation['profile_refs']) - profiles:
        raise EditorialError('Product relation references unavailable facts or frozen product snapshots')
    if relation['scope'] != 'unknown' and (not relation['fact_refs'] or not relation['profile_refs']):
        raise EditorialError('A claimed product relation requires fact and dated product identity/structure evidence')
    for key, component in candidate['score_components'].items():
        if set(component['evidence_refs']) - evidence or set(component['profile_refs']) - profiles:
            raise EditorialError('Score contains invented or unrelated evidence/profile references')
        if component['level'] is not None:
            if not component['evidence_refs'] or not component['rationale'].strip():
                raise EditorialError('A non-null score requires evidence and a concrete rationale')
            if key in {'core_variable', 'coverage', 'linkage'} and not component['profile_refs']:
                raise EditorialError('Product scores require a dated identity or structure snapshot')
            if key == 'timeliness' and candidate['cutoff_state'] != 'eligible':
                raise EditorialError('Timeliness cannot be scored with unknown or excluded publication evidence')
    novelty = candidate['novelty']
    if set(novelty['previous_doc_refs']) - set(edition['history_doc_ids']):
        raise EditorialError('Novelty contains unavailable historical references')
    if original_novelty['history_completeness'] != 'full_text':
        candidate['novelty'] = original_novelty
        candidate['score_components']['incrementality'] = {'level': None, 'rationale': original_novelty['difference'], 'evidence_refs': [], 'profile_refs': []}
    elif novelty['status'] != 'unknown' and not novelty['previous_doc_refs']:
        raise EditorialError('A history comparison must cite imported history')
    elif novelty['status'] == 'unknown':
        candidate['score_components']['incrementality'] = {'level': None, 'rationale': novelty['difference'], 'evidence_refs': [], 'profile_refs': []}
    if candidate['evidence_state'] in {'pending', 'conflicted', 'out_of_cutoff'}:
        if candidate['readiness'] == 'ready':
            raise EditorialError('Pending, conflicted or excluded evidence cannot be declared ready')
    candidate['score_total'] = calculate_score(candidate['score_components'], edition['scoring']['component_max_points'])['raw_total']
    candidate['run_id'] = 'validated_proposal'
    if candidate['readiness'] in {'ready', 'one_gap', 'supporting_only'} and candidate['cutoff_state'] == 'eligible':
        candidate['suggested_group'] = 'backup'
    return _finish_candidate(candidate)


def persist_assessment(conn, candidate):
    # This helper accepts only validated candidate records; validate again for persistence.
    _finish_candidate(candidate)
    edition = get_edition(conn, candidate['edition_id'])
    if candidate['is_fixture'] != edition['is_fixture'] or candidate['product_code'] != edition['product_code']:
        raise EditorialError('Candidate mode or product differs from Edition')
    expected_facts = {f['fact_id'] for f in edition_facts(conn, edition['edition_id']) if f['event_id'] == candidate['event_id']}
    if set(candidate['fact_refs']) != expected_facts:
        raise EditorialError('Candidate fact references do not match the Edition event')
    baseline = _base_candidate(conn, {}, edition['edition_id'], candidate['event_id'])
    if set(candidate['evidence_refs']) != set(baseline['evidence_refs']) or candidate['evidence_state'] != baseline['evidence_state'] or candidate['cutoff_state'] != baseline['cutoff_state']:
        raise EditorialError('Candidate cannot invent evidence or upgrade server-computed evidence/cutoff status')
    valid_profiles = set(edition['product_snapshot_ids'])
    if set(candidate['product_relation']['fact_refs']) - expected_facts or set(candidate['product_relation']['profile_refs']) - valid_profiles:
        raise EditorialError('Invalid candidate product mapping references')
    for component in candidate['score_components'].values():
        if set(component['evidence_refs']) - set(baseline['evidence_refs']) or set(component['profile_refs']) - valid_profiles:
            raise EditorialError('Invalid candidate score references')
    if candidate['score_total'] != calculate_score(candidate['score_components'], edition['scoring']['component_max_points'])['raw_total']:
        raise EditorialError('Candidate total differs from deterministic scoring formula')
    with conn:
        conn.execute('INSERT OR IGNORE INTO fn_assessments VALUES(?,?,?,?,?)',
                     (candidate['candidate_id'], candidate['edition_id'], candidate['event_id'], _json(candidate), _now()))
    return candidate


def assess_events(conn, config, *, edition_id, event_ids=None):
    get_edition(conn, edition_id)
    event_ids = event_ids if event_ids is not None else [r[0] for r in conn.execute('SELECT event_id FROM fn_events ORDER BY event_id')]
    _ids(event_ids, 'event_ids')
    candidates = [persist_assessment(conn, _finish_candidate(_base_candidate(conn, config, edition_id, eid))) for eid in event_ids]
    return {'edition_id': edition_id, 'candidates': candidates, 'llm_requests': 0, 'default_selection': None,
            'semantic_assessment': 'not_performed', 'note': '本地事实整理及确定性历史逐字对比；未做语义判断。'}


def latest_decision(conn, edition_id):
    row = conn.execute('SELECT data_json FROM fn_decisions WHERE edition_id=? ORDER BY revision DESC LIMIT 1', (edition_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save_decision(conn, *, edition_id, selected_event_id, selected_fact_ids, expected_revision,
                  actor_type, mainline_note=None, reason_code=None, selected_clue_ids=None,
                  manual_notes=None, config=None):
    get_edition(conn, edition_id)
    if actor_type != 'human':
        raise EditorialError('Only an explicit human action may save editorial choices')
    if type(expected_revision) is not int or expected_revision < 0:
        raise EditorialError('expected_revision must be the last seen nonnegative revision')
    _ids(selected_fact_ids, 'selected_fact_ids')
    _text(mainline_note, 'mainline_note', nullable=True)
    _text(reason_code, 'reason_code', nullable=True)
    facts = {f['fact_id']: f for f in edition_facts(conn, edition_id)}
    if set(selected_fact_ids) - set(facts):
        raise EditorialError('Selection references nonexistent facts or another Edition')
    if selected_event_id is not None:
        known = conn.execute('SELECT 1 FROM fn_events WHERE event_id=?', (selected_event_id,)).fetchone()
        if not known:
            raise EditorialError('Unknown selected_event_id')
    if conn.in_transaction:
        raise EditorialError('Save decision requires an independent transaction')
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = latest_decision(conn, edition_id)
        revision = current['decision_revision'] if current else 0
        if revision != expected_revision:
            raise EditorialError('Decision revision conflict: reload saved selection before editing')
        background = {}
        if selected_clue_ids is not None or manual_notes is not None or (current and current.get('background_version')):
            from .writing_background import freeze_choices
            from .config import load_config
            clues, notes = freeze_choices(conn, config or load_config(), edition_id,
                selected_clue_ids if selected_clue_ids is not None else (current or {}).get('selected_clue_ids', []),
                manual_notes if manual_notes is not None else (current or {}).get('manual_notes', []))
            background = {'background_version': 1, 'selected_clue_ids': [c['event_id'] for c in clues],
                          'selected_clues': clues, 'manual_notes': notes}
        revision += 1
        record = {'decision_id': _id('decision_', [edition_id, revision]), 'edition_id': edition_id, 'actor_type': 'human',
                  'decision_revision': revision, 'selected_event_id': selected_event_id,
                  'selected_fact_ids': selected_fact_ids, 'mainline_note': mainline_note,
                  'reason_code': reason_code, 'timestamp': _now()}
        record.update(background)
        conn.execute('INSERT INTO fn_decisions VALUES(?,?,?,?,?,?,?)', (record['decision_id'], edition_id, selected_event_id,
                     'human', revision, _json(record), record['timestamp']))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return record


def _snapshots(conn, edition):
    entries = [json.loads(conn.execute('SELECT data_json FROM fn_product_snapshots WHERE snapshot_id=?', (sid,)).fetchone()[0])
               for sid in edition['product_snapshot_ids']]
    for entry in entries:
        entry['evidence'] = [_evidence(conn, eid) for eid in entry['evidence_refs']]
    return entries


def edition_detail(conn, edition_id):
    edition = get_edition(conn, edition_id)
    assessments = {}
    for row in conn.execute('SELECT data_json FROM fn_assessments WHERE edition_id=? ORDER BY rowid', (edition_id,)):
        candidate = json.loads(row[0])
        assessments[candidate['event_id']] = candidate
    facts = edition_facts(conn, edition_id)
    source_materials = []
    for row in conn.execute('SELECT event_id,evidence_id FROM fn_edition_evidence WHERE edition_id=? ORDER BY event_id,evidence_id', (edition_id,)):
        ev = _evidence(conn, row['evidence_id'])
        document = conn.execute('SELECT content,metadata_json FROM fn_documents WHERE document_id=?', (ev['document_id'],)).fetchone()
        body = document['content']
        ev.pop('local_path', None)  # Browsing shows source and hash, not private path details.
        source_materials.append(ev | {'event_id': row['event_id'], 'content': body,
                                      'title': json.loads(document['metadata_json']).get('title')})
    decision = latest_decision(conn, edition_id)
    notices = []
    if decision:
        selected = set(decision['selected_fact_ids'])
        for f in facts:
            if f['fact_id'] in selected and f['evidence_state'] not in {'reported', 'verified'}:
                notices.append({'fact_id': f['fact_id'], 'state': f['evidence_state'], 'message': '已选材料的证据状态变化；保留原选择，请人工查看'})
        chosen_events = {f['event_id'] for f in facts if f['fact_id'] in selected}
        new_facts = [f['fact_id'] for f in facts if f['event_id'] in chosen_events and f['fact_id'] not in selected]
        if new_facts:
            notices.append({'new_fact_ids': new_facts, 'message': '已选事件有未选事实或新版本；未自动替换素材'})
    return {'edition': edition, 'facts': facts, 'evidence': source_materials, 'assessments': list(assessments.values()), 'decision': decision,
            'product_snapshots': _snapshots(conn, edition), 'selection_notices': notices,
            'gaps': ['未配置模型时不做语义评估', '无历史正文时不能确认不撞题']}


def _export_reason(fact, policy):
    reasons = list(fact['gaps'])
    if fact['evidence_state'] not in policy['fact_export']['allowed_evidence_states']:
        reasons.append('证据状态不可直接用于正文')
    if fact['cutoff_state'] != 'eligible':
        reasons.append('未满足本期信息截点')
    if fact['kind'] == 'interpretation':
        reasons.append('解释性提议待人工核验')
    if fact.get('metric_scope') == 'product' and fact.get('observed_value') is not None:
        metric = fact.get('metric')
        if metric in policy['product_metrics']['restricted_exact_fact_types']:
            reasons.append('当前输出规则限制的精确产品规模/份额/流量')
        elif metric not in policy['product_metrics']['allowed_fact_types']:
            reasons.append('该产品数值指标尚无明确输出政策')
    if fact.get('metric_scope') == 'market' and fact.get('observed_value') is not None and (not fact.get('currency') or not fact.get('period_end')):
        reasons.append('市场数值缺币种或统计日期')
    if fact['evidence_state'] == 'reported' and not all(e.get('actual_source') for e in fact['evidence']):
        reasons.append('报道事实缺实际取得出处')
    return list(dict.fromkeys(reasons))


def export_handoff(conn, config, *, edition_id, decision_revision, output_dir):
    edition = get_edition(conn, edition_id)
    row = conn.execute('SELECT data_json FROM fn_decisions WHERE edition_id=? AND revision=?', (edition_id, decision_revision)).fetchone()
    if row is None:
        raise EditorialError('No saved human decision at the requested revision')
    decision = json.loads(row[0])
    has_background = bool(decision.get('selected_clue_ids') or any(n['selected'] for n in decision.get('manual_notes', [])))
    if not decision['selected_event_id'] and not has_background:
        raise EditorialError('Choose and save a main event before exporting a writing handoff')
    facts = {f['fact_id']: f for f in edition_facts(conn, edition_id)}
    selected = [facts[fid] for fid in decision['selected_fact_ids']]
    usable, appendix = [], []
    for fact in selected:
        reasons = _export_reason(fact, edition['output_policy'])
        if reasons:
            # Restricted or unverified assertions are not reproduced as draftable claims.
            appendix.append({'fact_id': fact['fact_id'], 'event_id': fact['event_id'], 'reasons': reasons,
                             'evidence_refs': fact['evidence_refs'], 'excluded_statement_and_value': True})
        else:
            # A broad imported paragraph may also contain an excluded product
            # number. Export only the selected, literally located claim span.
            clean_fact = dict(fact)
            clean_fact['evidence'] = []
            for original in fact['evidence']:
                ev = dict(original)
                offset = original['excerpt'].find(fact['statement'])
                if offset >= 0:
                    start = original['locator']['start'] + offset
                    ev['locator'] = {'type': 'character_range', 'start': start, 'end': start + len(fact['statement'])}
                    ev['excerpt'] = fact['statement']
                    ev['export_excerpt_narrowed_to_selected_claim'] = True
                clean_fact['evidence'].append(ev)
            usable.append(clean_fact)
    if decision['selected_event_id'] and not has_background and not any(f['event_id'] == decision['selected_event_id'] for f in usable):
        appendix.append({'event_id': decision['selected_event_id'], 'fact_id': None,
                         'reasons': ['人工主事件尚无选中且可用的事实；本包仅记录选题意向，不能据此成稿'],
                         'evidence_refs': [], 'excluded_statement_and_value': True})
    detail = edition_detail(conn, edition_id)
    pack = {'artifact_type': 'writing_handoff_materials_not_news', 'schema_version': '2.0', 'edition': edition,
            'decision': decision, 'usable_facts': usable, 'not_for_body': appendix,
            'selected_main_event_id': decision['selected_event_id'], 'mainline_note': decision['mainline_note'] or '主事件已选、主线说明未补',
            'product_snapshots': _snapshots(conn, edition),
            'assessed_run_ids': sorted({c['run_id'] for c in detail['assessments']}),
            'assessment_ids': [c['candidate_id'] for c in detail['assessments']],
            'selection_notices': detail['selection_notices'], 'is_fixture': edition['is_fixture'],
            'limitations': ['仅整理人工选中材料；未生成快讯正文或发布。', '用户提供事实保留报道归属，原始出处未独立核验。',
                            '计划、预测、统计期、产品结构日期必须按各自原值使用。', '南向总量仅支持市场环境；无去向证据不推断专门买入红利或科技。']}
    if decision.get('background_version'):
        from .writing_background import export_materials
        pack.update(export_materials(decision, edition), artifact_type='writing_background_not_news', schema_version='3.0')
        # Unselected notes and excluded prose must not leak via the audit copy.
        pack['decision'] = {k: v for k, v in decision.items() if k not in {'selected_clues', 'manual_notes'}}
        pack['decision']['selected_note_ids'] = [n['note_id'] for n in decision['manual_notes'] if n['selected']]
        pack['mainline_note'] = decision['mainline_note'] or '未补充主线说明；按人工选中顺序交接'
    digest = _hash(pack)
    export_id = 'export_' + digest[:32]
    dest = Path(output_dir).expanduser().resolve() / ('fixture' if edition['is_fixture'] else 'live') / edition_id / export_id
    record = {'export_id': export_id, 'edition_id': edition_id, 'decision_revision': decision_revision,
              'content_hash': digest, 'is_fixture': edition['is_fixture'],
              'paths': {'markdown': str(dest / 'writing_handoff.md'), 'json': str(dest / 'writing_handoff.json')}}
    title = '快讯背景交接包' if decision.get('background_version') else '写作交接材料包'
    header = '# ' + title + ('（合成测试，不是真实市场信息）' if edition['is_fixture'] else '')
    main_title = next((c['title'] for c in pack.get('selected_research_clues', [])
                       if c['event_id'] == decision['selected_event_id']), decision['selected_event_id'] or '未指定')
    lines = [header, '', '## 任务', '', f"产品：{edition['product_code']} {edition['product_name']}",
             f"Edition：{edition_id}", f"新闻截点：{edition['news_cutoff']}", f"行情截点：{_json(edition['market_cutoffs'])}",
             f"人工主事件：{main_title}", f"人工主线说明：{pack['mainline_note']}"]
    if decision.get('background_version'):
        from .writing_background import markdown_materials
        lines += markdown_materials(pack)
    lines += ['', '## 可用事实（须保留归属）', '']
    for i, fact in enumerate(usable, 1):
        lines += [f"### {i}. {fact['fact_id']}", '', fact['statement'], '',
                  f"类型：{fact['kind']}；证据身份：{fact['evidence_state']}；阶段：{fact['phase']}。",
                  f"首次公开：{fact['first_published_at']}；统计期：{fact.get('period_start')} 至 {fact.get('period_end')}；生效：{fact.get('effective_at')}。",
                  f"单位：{fact.get('unit')}；币种：{fact.get('currency')}；范围：{fact.get('metric_scope')}；比较基准：{fact.get('benchmark')}。"]
        if fact.get('qualification'):
            lines.append('限定：' + fact['qualification'])
        for ev in fact['evidence']:
            lines += [f"出处：{ev['actual_source']}；实际读取：{ev['actual_access']}；声明来源：{ev['declared_source']}；转引链：{_json(ev['attribution_chain'])}。",
                      f"定位：{ev['local_path']}，字符 {ev['locator']['start']}–{ev['locator']['end']}；文档 SHA256：{ev['snapshot_hash']}。",
                      f"来源 URL：{ev['source_url']}；证据片段：{ev['excerpt']}"]
        lines.append('')
    lines += ['## 产品结构与日期', '', '画像词表仅用于观察；下列材料仍须按其真实结构对象和日期引用。']
    for snapshot in pack['product_snapshots']:
        lines += [f"- {snapshot['snapshot_id']}；对象：{snapshot['snapshot_type']}；结构日期：{snapshot['data_as_of']}；公布：{snapshot['published_at']}；依据：{', '.join(snapshot['evidence_refs'])}。"]
    if not pack['product_snapshots']:
        lines.append('尚未取得本期可用产品结构快照；不得据路由关键词填成份或权重。')
    lines += ['', '## 待补项 / 不可直接用于正文', '']
    for item in appendix:
        lines.append(f"- {item['fact_id']}：{'；'.join(item['reasons'])}。未导出该表述与量值。")
    if not appendix:
        lines.append('本次选中事实无规则排除项；仍保留各事实归属及限制。')
    lines += ['', '## 来源与重要限制', ''] + ['- ' + x for x in pack['limitations']]
    lines += ['', '## 追溯', '', f"decision_revision：{decision_revision}；profile_version：{edition['profile_version']}；rule_version：{edition['rule_version']}；output_policy_version：{edition['output_policy_version']}", f"content_hash：{digest}", '']
    files = {'json': json.dumps(pack, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n', 'markdown': '\n'.join(lines)}
    dest.mkdir(parents=True, exist_ok=True)
    for kind, text in files.items():
        path = Path(record['paths'][kind])
        if path.exists() and path.read_text(encoding='utf-8') != text:
            raise EditorialError('Immutable export file differs; existing artifact left untouched')
    for kind, text in files.items():
        path = Path(record['paths'][kind])
        if not path.exists():
            with path.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
    with conn:
        conn.execute('INSERT OR IGNORE INTO fn_exports VALUES(?,?,?,?,?,?)', (export_id, edition_id, decision['decision_id'], _json(record), digest, _now()))
    return record
