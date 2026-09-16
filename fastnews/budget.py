"""SQLite reservations made before requests; missing usage never means zero."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import re
import uuid


class BudgetExhausted(RuntimeError):
    """The next call would exceed a configured bound."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error_code(value):
    allowed = {'provider_error','invalid_output','invalid_usage','timeout','permission_denied',
        'parse_error','parse_date_error','invalid_metadata','adapter_not_connected','source_terms_manual_only',
        'unknown_registered_source','fulltext_access_not_approved','one_off_source_requires_explicit_import','redirect_outside_source_scope',
        'document_format_not_supported','readable_text_insufficient','text_context_too_large','restricted_fulltext',
        'unsafe_port','dns_failed','unsafe_dns_address','unsafe_connected_address','unsafe_url',
        'public_dns_failed','unsupported_content_encoding','response_too_large','tls_failed','connection_failed',
        'redirect_outside_allowed_hosts','redirect_loop','redirect_limit','https_downgrade',
        'robots_cross_origin_redirect','robots_invalid','robots_disallowed','robots_delay_exceeds_runtime_limit','access_challenge'}
    return value if isinstance(value,str) and (value in allowed or re.fullmatch(r'(?:robots_)?http_[1-5][0-9]{2}',value)) else 'provider_error'


@contextmanager
def _write(conn):
    if conn.in_transaction:
        raise ValueError('Budget operation requires a committed caller transaction')
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _nonnegative(value, name, integer=False, nullable=False):
    if value is None and nullable:
        return
    types = (int,) if integer else (int, float)
    if type(value) not in types or not math.isfinite(value) or value < 0:
        raise ValueError(name + ' must be a finite nonnegative ' + ('integer' if integer else 'number'))


class RunBudget:
    def __init__(self, conn, run_id):
        self.conn, self.run_id = conn, run_id
        row = conn.execute('SELECT limits_json FROM fn_research_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown research run')
        self.limits = json.loads(row[0])

    def summary(self):
        rows = self.conn.execute('SELECT * FROM fn_provider_calls WHERE run_id=?', (self.run_id,)).fetchall()
        llm = [r for r in rows if r['kind'] == 'llm']
        calls = {'search_requests': sum(r['kind'] == 'search' for r in rows),
                 'llm_requests': len(llm),
                 'fulltext_requests': sum(r['kind'] == 'fulltext' for r in rows),
                 'fulltext_articles': len({r['resource_key'] for r in rows if r['kind'] == 'fulltext'})}
        calls['fulltext_requested_resources'] = calls['fulltext_articles']
        calls['fulltext_successful_requests'] = sum(r['kind']=='fulltext' and r['status']=='success' for r in rows)
        calls['fulltext_successful_resources'] = len({r['resource_key'] for r in rows if r['kind']=='fulltext' and r['status']=='success'})
        calls['fulltext_articles_kind'] = 'distinct_requested_resources_not_successful_reads'
        # Report actual measured usage separately from the conservative budget charge.
        for label, column in [('input_tokens', 'input'), ('output_tokens', 'output')]:
            calls[label] = sum(r['actual_' + column] for r in llm) if all(r['actual_' + column] is not None for r in llm) else None
            calls['accounted_' + label] = sum(r['actual_' + column] if r['actual_' + column] is not None else r['reserved_' + column] for r in llm)
        calls['actual_cost'] = sum(r['actual_cost'] for r in rows) if rows and all(r['actual_cost'] is not None for r in rows) else None
        calls['cost_status'] = 'not_called' if not rows else ('known' if calls['actual_cost'] is not None else 'unknown')
        calls['usage_status'] = 'not_called' if not rows else ('unknown_or_reserved' if any(r['usage_status'] in {'unknown', 'reserved', 'partial'} for r in rows) else 'reported')
        calls['reserved_calls'] = sum(r['status'] == 'reserved' for r in rows)
        calls['failed_requests'] = sum(r['status'] in {'failed','invalid_output'} for r in rows)
        return calls

    def reserve(self, kind, provider, resource_key, *, event_id=None, attempt=1,
                input_tokens=0, output_tokens=0, estimated_max_cost=None):
        if kind not in {'search', 'llm', 'fulltext'}:
            raise ValueError('Unsupported provider kind')
        _nonnegative(input_tokens, 'input_tokens', integer=True)
        _nonnegative(output_tokens, 'output_tokens', integer=True)
        _nonnegative(estimated_max_cost, 'estimated_max_cost', nullable=True)
        if type(attempt) is not int or attempt < 1 or attempt > 2:
            raise BudgetExhausted('retry_limit')
        with _write(self.conn):
            status = self.conn.execute('SELECT status FROM fn_research_runs WHERE run_id=?', (self.run_id,)).fetchone()[0]
            if status != 'running':
                raise ValueError('Cannot reserve calls on a finished run')
            usage = self.summary()
            limits = self.limits
            if kind == 'search' and usage['search_requests'] >= limits['max_search_requests']:
                raise BudgetExhausted('max_search_requests')
            if kind == 'llm':
                for used, extra, maximum in [('llm_requests', 1, 'max_llm_requests_including_retries'),
                                             ('accounted_input_tokens', input_tokens, 'max_input_tokens'),
                                             ('accounted_output_tokens', output_tokens, 'max_output_tokens')]:
                    if usage[used] + extra > limits[maximum]:
                        raise BudgetExhausted(maximum)
            if kind == 'fulltext':
                distinct = {r[0] for r in self.conn.execute("SELECT DISTINCT resource_key FROM fn_provider_calls WHERE run_id=? AND kind='fulltext'", (self.run_id,))}
                if resource_key not in distinct and len(distinct) >= limits['max_fulltext_articles']:
                    raise BudgetExhausted('max_fulltext_articles')
                if event_id:
                    per_event = {r[0] for r in self.conn.execute("SELECT DISTINCT resource_key FROM fn_provider_calls WHERE run_id=? AND kind='fulltext' AND event_id=?", (self.run_id, event_id))}
                    if resource_key not in per_event and len(per_event) >= limits['max_articles_per_event']:
                        raise BudgetExhausted('max_articles_per_event')
                attempts = self.conn.execute("SELECT count(*) FROM fn_provider_calls WHERE run_id=? AND kind='fulltext' AND resource_key=?", (self.run_id, resource_key)).fetchone()[0]
                if attempts >= 2:
                    raise BudgetExhausted('fulltext_retry_limit')
            if limits.get('max_cost') is not None:
                if estimated_max_cost is None:
                    raise BudgetExhausted('cost_estimate_unavailable')
                costs = self.conn.execute('SELECT actual_cost,reserved_cost FROM fn_provider_calls WHERE run_id=?', (self.run_id,)).fetchall()
                if any(r[0] is None and r[1] is None for r in costs):
                    raise BudgetExhausted('previous_cost_unknown')
                used_cost = sum(r[0] if r[0] is not None else r[1] for r in costs)
                if used_cost + estimated_max_cost > limits['max_cost']:
                    raise BudgetExhausted('max_cost')
            call_id = 'call_' + uuid.uuid4().hex
            self.conn.execute('INSERT INTO fn_provider_calls(call_id,run_id,kind,provider,resource_key,event_id,attempt,started_at,status,reserved_input,reserved_output,reserved_cost) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                              (call_id,self.run_id,kind,provider,resource_key,event_id,attempt,utc_now(),'reserved',input_tokens,output_tokens,estimated_max_cost))
        return call_id

    def settle(self, call_id, *, status='success', usage=None, error_code=None):
        if status not in {'success','failed','invalid_output'}:
            raise ValueError('Invalid call status')
        usage = usage or {}
        values = [usage.get('input_tokens'), usage.get('output_tokens'), usage.get('cost')]
        for name, value in zip(('input_tokens','output_tokens','cost'), values):
            _nonnegative(value, name, integer=name != 'cost', nullable=True)
        # Never persist arbitrary exception messages, requests, keys or private prompts.
        if error_code is not None:
            error_code = safe_error_code(error_code)
        usage_status = 'reported' if all(v is not None for v in values) else ('unknown' if all(v is None for v in values) else 'partial')
        with _write(self.conn):
            row = self.conn.execute('SELECT status,reserved_input,reserved_output,reserved_cost FROM fn_provider_calls WHERE call_id=? AND run_id=?', (call_id,self.run_id)).fetchone()
            if row is None or row[0] != 'reserved':
                raise ValueError('Call is unknown or already settled')
            # Actual usage remains authoritative even if a provider breaches its cap.
            self.conn.execute('UPDATE fn_provider_calls SET status=?,finished_at=?,actual_input=?,actual_output=?,actual_cost=?,usage_status=?,error_code=? WHERE call_id=?',
                              (status,utc_now(),*values,usage_status,error_code,call_id))
        return self.summary()
