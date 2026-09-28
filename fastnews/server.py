"""Local single-page interface. Business rules remain in the CLI services."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import re
from urllib.parse import parse_qs, urlsplit
import uuid
from zoneinfo import ZoneInfo

from . import editorial
from .config import load_config
from .db import connect, sync_registry
from .pipeline import scan_morning, collection_window
from .daily import daily_news
from .routing import route_product, working_now
from .strict_json import loads

STATIC = Path(__file__).with_name('static')


class LocalApplication:
    def __init__(self, database, config, offline=False, output_dir=None):
        self.database = str(Path(database).resolve())
        self.config, self.offline = config, offline
        self.output_dir = Path(output_dir or 'exports').resolve()
        self.token = secrets.token_urlsafe(32)
        self.guard = threading.RLock()
        self.jobs = {}
        self.active_job = None
        self.controls = {}
        if Path(self.database).exists():
            with closing(self.connection()) as conn, conn:
                from .budget import utc_now
                conn.execute("UPDATE fn_reading_reports SET status='failed',error_code='interrupted',finished_at=? WHERE status='running'",(utc_now(),))
                conn.execute("UPDATE fn_writing_runs SET status='interrupted',error_code='interrupted',finished_at=? WHERE status='running'",(utc_now(),))
                from .writing import run_record
                for row in conn.execute('SELECT * FROM fn_writing_runs ORDER BY rowid DESC LIMIT 200'):
                    record=run_record(row)
                    self.jobs[record['job_id']]=record

    def connection(self):
        return connect(self.database, mode='fixture' if self.offline else 'live')

    def state(self, profile_id='default'):
        from .__main__ import source_status, capabilities
        with closing(self.connection()) as conn:
            from .source_catalog import catalog, profiles, profile, CATEGORIES
            from .host_provider import host_status
            from .source_catalog import configured_view
            from .search_plan import build_search_plan
            reader_config = configured_view(conn, self.config, profile_id)
            reader_sources = catalog(conn,self.config,profile_id)
            selected = {s['source_id']:s for s in reader_sources}
            source_summary = source_status(conn)
            source_summary['sources'] = [s for s in source_summary['sources'] if s['source_id'] in selected]
            present = {s['source_id'] for s in source_summary['sources']}
            for sid, item in selected.items():
                if sid not in present:
                    source_summary['sources'].append({'source_id':sid,'capability_status':'needs_probe',
                        'last_success_at':None,'last_run':None})
            for item in source_summary['sources']:
                source = selected[item['source_id']]
                item.update(name=source['name'],expected_enabled=source['enabled'],
                            expected_morning_scan=source['enabled'] and 'morning' in source['sections'])
            source_summary['configured'] = len(source_summary['sources'])
            source_summary['live_connected'] = 0 if self.offline else sum(s['capability_status'] in {'ready_metadata','ready_fulltext'} and bool(s['last_success_at']) for s in source_summary['sources'])
            from .source_health import attach_health
            attach_health(conn,reader_sources,source_summary)
            clock = working_now(conn, self.offline).astimezone(ZoneInfo('Asia/Shanghai'))
            latest = conn.execute('SELECT * FROM fn_scan_batches ORDER BY rowid DESC LIMIT 1').fetchone()
            from .live_sources import load_live_config
            live = load_live_config(self.config)['sources']
            briefings = []
            for source in reader_sources:
                if not source['enabled'] or 'morning' not in source['sections']:
                    continue
                sid = source['source_id']
                entry = live.get(sid,{})
                briefings.append({'source_id':sid,'name':source['name'],
                    'url':source['url'],
                    'adapter_available':source['collector_available'],
                    'note':('可读取公开栏目元数据，具体结果见所选日期' if entry.get('metadata_allowed') else
                            '站点限制自动读取，需授权供稿或手工材料' if entry else '晨报栏目采集器尚未接通')})
            return {'csrf_token': self.token, 'workspace_id': self.database, 'is_fixture': self.offline,
                    'reader_sources':reader_sources,'reader_profiles':profiles(conn),'reader_profile':profile(conn,profile_id),
                    'source_categories':CATEGORIES,'host_status':host_status(),
                    'search_plans': {code: build_search_plan(reader_config, code) for code in self.config['products']},
                    'morning_briefings':briefings,
                    'products': [p for p in self.config['products'].values() if p.get('selectable',True)], 'sources': source_summary,
                    'capabilities': capabilities(conn), 'editions': editorial.list_editions(conn),
                    'default_day': clock.date().isoformat(),
                    'server_now':clock.isoformat(),
                    'default_cutoff': (clock.date() - timedelta(days=1)).isoformat() + 'T23:59:59+08:00',
                    'latest_scan': dict(latest) if latest else None, 'active_job': self.active_job}

    def detail(self, edition_id, profile_id='default'):
        with closing(self.connection()) as conn:
            from .source_catalog import configured_view,section_ids
            config=configured_view(conn,self.config,profile_id)
            result = editorial.edition_detail(conn, edition_id)
            edition = result['edition']
            result['route'] = route_product(conn, config, edition['product_code'],
                edition_date=edition['edition_date'], news_cutoff=edition['news_cutoff'],
                market_cutoffs=edition['market_cutoffs'], now=working_now(conn, self.offline))
            enabled=set(section_ids(config,'all'))
            retained={r['event_id'] for r in result['evidence']}
            if result.get('decision'):
                retained.add(result['decision'].get('selected_event_id'))
            result['route']['groups']={group:[e for e in events if e['event_id'] in retained or
                any(s['source_id'] in enabled for s in e['source_chain'])]
                for group,events in result['route']['groups'].items()}
            result['route']['event_count']=sum(len(v) for v in result['route']['groups'].values())
            latest = conn.execute("SELECT report_json FROM fn_research_runs WHERE edition_id=? AND status IN ('partial','complete') ORDER BY rowid DESC LIMIT 1",(edition_id,)).fetchone()
            result['latest_research'] = json.loads(latest[0]) if latest and latest[0] else None
            from .reading_reports import latest_report
            result['reading_report'] = latest_report(conn,profile_id,'product',edition['edition_date'],edition_id)
            from .product_reading import aggregate_reports
            result['product_clues'] = aggregate_reports(conn,profile_id,edition_id)
            from .writing_background import pool_items
            result['candidate_pool'] = pool_items(conn, config, edition_id, include_inactive=True)
            from .writing import workflow
            result['writing']=workflow(conn,edition_id)
            return result

    def daily(self, day, profile_id='default'):
        with closing(self.connection()) as conn:
            from .source_catalog import configured_view, section_ids
            from .reading_reports import latest_report
            config = configured_view(conn,self.config,profile_id)
            result = daily_news(conn, config, day, offline=self.offline)
            enabled = set(section_ids(config,'all'))
            result['items'] = [s for s in result['items'] if s['source_id'] in enabled]
            result['undated_items'] = [s for s in result['undated_items'] if s['source_id'] in enabled]
            result['counts'].update(items=len(result['items']),undated=len(result['undated_items']),
                                    sources_with_items=len({s['source_id'] for s in result['items']}))
            result['reading_report'] = latest_report(conn,profile_id,'morning',day)
            return result

    def _run_job(self, job_id, action, data):
        try:
            with closing(self.connection()) as conn:
                from .source_catalog import configured_view, section_ids
                from .custom_sources import ReaderAdapter
                from .reading_reports import generate_report
                profile_id = data.get('reader_profile','default')
                config = data.get('_config') or configured_view(conn,self.config,profile_id)
                if not action.startswith('writing-'):
                    sync_registry(conn,config)
                if action in {'writing-outline','writing-draft','writing-generate'}:
                    from .writing import execute_run
                    def writing_progress(item):
                        with self.guard:
                            self.jobs[job_id]['progress'].append(item)
                    result=execute_run(conn,job_id,self.controls[job_id],progress=writing_progress)
                elif action in {'scan','digest'}:
                    def progress(item):
                        with self.guard:
                            self.jobs[job_id]['progress'].append({k: item.get(k) for k in ('source_id', 'status', 'error_code','stage','message')})
                    source_ids = section_ids(config,'all' if action=='digest' else data.get('section','morning'),collect_only=True)
                    if action == 'scan' and data.get('source_id'):
                        source_ids = [data['source_id']]
                    if not source_ids and action == 'scan':
                        raise ValueError('此板块没有启用的自动采集来源。可打开手动查阅链接，或在来源设置中启用自动来源。')
                    if action=='digest':
                        from .collection_task import collect
                        control=self.controls[job_id]
                        collected=collect(conn,config,'digest',dict(collection_date=data.get('date'),source_ids=source_ids),
                            control,progress) if source_ids else None
                        result = generate_report(conn,config,profile_id,'morning',data['date'],progress=progress,
                            lookback_days=data.get('lookback_days',7),control=control,task_id=job_id,collection_result=collected)
                    else:
                        result = scan_morning(conn, config, offline=self.offline, progress=progress,
                            collection_date=data.get('date'),source_ids=source_ids,
                            adapter=None if self.offline else ReaderAdapter(config)) if source_ids else None
                elif action == 'probe':
                    from types import SimpleNamespace
                    from .cli_actions import dispatch
                    result = dispatch(conn, self.config, SimpleNamespace(command='probe-sources', offline=self.offline, output=None))
                elif action in {'research','explore'}:
                    from .research import research_product
                    edition = data.get('_edition') or editorial.get_edition(conn, data['edition_id'])
                    def research_progress(item):
                        with self.guard:
                            self.jobs[job_id]['progress'].append(item)
                    if action=='explore':
                        control=self.controls[job_id]
                        control.check()
                        result=generate_report(conn,config,profile_id,'product',edition['edition_date'],
                            edition=edition,request_mode='explore',research_question=data.get('research_question',''),
                            request_id=data['request_id'],control=control,task_id=job_id,progress=research_progress,
                            writing_context=data.get('writing_context'))
                        with self.guard:
                            self.jobs[job_id].update(status='completed',result=result)
                        return
                    options = {'edition_id': edition['edition_id'], 'progress':research_progress}
                    if data.get('fetch_public_text'):
                        if self.offline:
                            raise ValueError('合成模式不能访问真实网页')
                        from .public_text import PublicTextProvider
                        options.update(fulltext_provider=PublicTextProvider(self.config), external_calls_authorized=True)
                    if not self.offline:
                        options['discovery_adapter'] = ReaderAdapter(config)
                    cached=conn.execute("SELECT report_json FROM fn_research_runs WHERE edition_id=? AND status IN ('complete','partial') AND report_json IS NOT NULL ORDER BY rowid DESC LIMIT 1",(edition['edition_id'],)).fetchone()
                    if cached and data.get('use_host') and not data.get('fetch_public_text'):
                        result=json.loads(cached[0])
                        result['fixed_source_cache_reused']=True
                        research_progress({'stage':'cache','message':'复用本期已存元数据，模型继续核对重点来源与背景'})
                    else:
                        if data.get('use_host') or not self.offline:
                            from .collection_task import collect
                            result=collect(conn,config,'research',dict(product_code=edition['product_code'],
                                edition_id=edition['edition_id'],fetch_public_text=bool(data.get('fetch_public_text'))),
                                self.controls[job_id],research_progress)
                        else:
                            result = research_product(conn, config, edition['product_code'], **options)
                    if data.get('use_host'):
                        result['reading_report'] = generate_report(conn,config,profile_id,'product',edition['edition_date'],
                                                                  edition=edition,progress=research_progress,
                                                                  extra_keywords=data.get('extra_keywords'),
                                                                  control=self.controls[job_id],task_id=job_id,
                                                                  collection_result=result)
                else:
                    raise ValueError('Unknown job action')
            with self.guard:
                self.jobs[job_id].update(status='completed', result=result)
        except Exception as exc:
            with self.guard:
                from .task_control import TaskStopped
                from .host_provider import HostUnavailable
                self.jobs[job_id].update(status=getattr(exc,'code','failed'), error=str(exc) if isinstance(exc,(ValueError,TaskStopped,HostUnavailable)) else type(exc).__name__)
        finally:
            with self.guard:
                if self.active_job==job_id:
                    self.active_job = None
                self.controls.pop(job_id,None)

    def mutate(self, action, data):
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        if action in {'explore','digest','research'}:
            from .task_control import TaskControl
            from .product_reading import policy
            seconds=policy()['exploration_seconds']
            accepted_control=TaskControl(seconds)
            accepted_at=accepted_control.started_at
        elif action in {'writing-outline','writing-draft','writing-generate'}:
            from .task_control import TaskControl
            from .writing import policy
            if action=='writing-generate':
                from .writing_direct import policy as direct_policy
                accepted_control=TaskControl(direct_policy()['deadline_seconds'])
            else:
                accepted_control=TaskControl(policy()['deadline_seconds'])
        with self.guard:
            if action in {'writing-outline','writing-draft','writing-generate'}:
                from .writing import previous_request, accept_run, policy
                from .source_catalog import configured_view, profile
                from .task_control import TaskControl
                control=accepted_control
                with closing(self.connection()) as conn:
                    previous=previous_request(conn,action.removeprefix('writing-'),data)
                    if previous:
                        self.jobs.setdefault(previous['job_id'],previous)
                        return {'job_id':previous['job_id']}
                    if self.active_job:
                        raise ValueError('已有后台任务；可继续选材和保存，完成或停止后再生成')
                    if self.offline:
                        raise ValueError('合成模式不自动调用真实写作模型')
                    frozen=configured_view(conn,self.config,data.get('reader_profile','default'))
                    frozen['_reader_profile']=profile(conn,data.get('reader_profile','default'))
                    record=accept_run(conn,frozen,action.removeprefix('writing-'),data,control)
                jid=record['job_id']
                self.jobs[jid]=record
                self.controls[jid]=control
                self.active_job=jid
                threading.Thread(target=self._run_job,args=(jid,action,dict(data,_config=frozen)),daemon=True).start()
                return {'job_id':jid}
            if action=='job-stop':
                job=self.jobs.get(data.get('job_id'))
                if not job or job['action'] not in {'explore','digest','research','writing-outline','writing-draft','writing-generate'}:
                    raise ValueError('没有可停止的探查或写作任务')
                control=self.controls.get(data['job_id'])
                if control:
                    control.cancelled.set()
                    job['stop_requested']=True
                return {'job_id':data['job_id'],'status':job['status'],'stop_requested':bool(control)}
            if action=='explore':
                if data.get('writing_context'):
                    from .writing import version
                    with closing(self.connection()) as conn:
                        parent=version(conn,data['writing_context'].get('outline_version_id'))
                        if parent['edition_id']!=data['edition_id'] or parent['stage']!='outline':
                            raise ValueError('补搜需要本期框架')
                    data=dict(data,writing_context={'outline_version_id':parent['version_id']})
                rid=data.get('request_id')
                if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):
                    raise ValueError('继续探查需要有效的请求编号')
                question=data.get('research_question','')
                if not isinstance(question,str) or len(question)>500:
                    raise ValueError('补充需求最多500字')
                for jid,job in self.jobs.items():
                    if job.get('request_id')==rid:
                        if job.get('edition_id')!=data.get('edition_id') or job.get('reader_profile')!=data.get('reader_profile','default'):
                            raise ValueError('请求编号已用于其他期次或方案')
                        return {'job_id':jid}
                with closing(self.connection()) as conn:
                    for row in conn.execute("SELECT * FROM fn_reading_reports WHERE profile_id=? AND edition_id=? AND kind='product' ORDER BY rowid DESC",(data.get('reader_profile','default'),data['edition_id'])):
                        saved=json.loads(row['input_json'])
                        if saved.get('request_id')==rid:
                            from .reading_reports import report_record
                            task=saved.get('task',{})
                            jid=task.get('job_id') or row['report_id']
                            status='completed' if row['status'] in {'complete','partial'} else row['error_code'] if row['error_code'] in {'cancelled','timed_out','interrupted'} else 'failed'
                            self.jobs[jid]={'job_id':jid,'action':'explore','edition_id':data['edition_id'],
                                'reader_profile':data.get('reader_profile','default'),'request_id':rid,'progress':[],
                                'status':status,'started_at':task.get('started_at',row['created_at']),
                                'deadline_at':task.get('deadline_at'),'product_code':row['product_code'],
                                'news_cutoff':saved.get('news_cutoff'),'stop_requested':status=='cancelled',
                                'result':report_record(row),'error':None if status=='completed' else
                                {'cancelled':'本轮探查已停止，已有结果保留','timed_out':'本轮探查达到时间上限，已有结果保留'}.get(status,'上一次任务未完成，已有结果保留')}
                            return {'job_id':jid}
            if action in {'scan', 'probe', 'research', 'digest','explore'}:
                if self.active_job:
                    raise ValueError('已有后台检索任务；可继续选材、保存和导出，完成或停止后再发起检索')
                from .source_catalog import configured_view
                with closing(self.connection()) as conn:
                    frozen=configured_view(conn,self.config,data.get('reader_profile','default'))
                    from .source_catalog import profile
                    frozen['_reader_profile']=profile(conn,data.get('reader_profile','default'))
                    if action=='digest' or action in {'research','explore'} and (data.get('use_host') or action=='explore'):
                        from .writing import task_attempts
                        from .host_task_budget import status, exhausted_message
                        budget=status(conn,used=task_attempts(conn))
                        if budget['remaining']==0:
                            raise ValueError(exhausted_message(budget))
                if action == 'scan' and 'source_id' in data:
                    from .source_catalog import catalog
                    with closing(self.connection()) as conn:
                        source = next((s for s in catalog(conn,self.config,data.get('reader_profile','default'))
                                       if s['source_id'] == data['source_id']),None)
                        if not source or not source['enabled'] or not source['collector_available']:
                            raise ValueError('此来源未启用或尚无可重试的采集器，请查看来源状态')
                if action == 'probe' and self.offline:
                    raise ValueError('合成模式不执行真实来源探测')
                if action in {'scan','digest'} and 'date' in data:
                    with closing(self.connection()) as conn:
                        collection_window(data['date'], working_now(conn, self.offline))
                    if action=='digest':
                        from .morning_reading import window
                        window(data['date'],data.get('lookback_days',7))
                if action in {'research','explore'}:
                    with closing(self.connection()) as conn:
                        edition = editorial.get_edition(conn,data['edition_id'])
                        from .search_plan import build_search_plan
                        from .source_catalog import configured_view
                        build_search_plan(configured_view(conn,self.config,data.get('reader_profile','default')),
                                          edition['product_code'], data.get('extra_keywords'))
                        if data.get('product_code',edition['product_code']) != edition['product_code']:
                            raise ValueError('当前选择的产品与已保存期次不同，请按当前产品建立本期后检索')
                        if action=='explore':
                            selected_profile=frozen['_reader_profile']
                            if self.offline:
                                raise ValueError('合成模式不执行真实联网探查')
                            if selected_profile['settings'].get('host','none')=='none' or not selected_profile['settings'].get('web_search',True):
                                raise ValueError('继续探查需要选择已登录的模型并启用联网检索')
                job_id = uuid.uuid4().hex
                self.jobs[job_id] = {'job_id': job_id, 'action': action, 'date': data.get('date'),
                                     'edition_id':data.get('edition_id'),'reader_profile':data.get('reader_profile','default'),
                                     'request_id':data.get('request_id'),'status': 'running', 'progress': []}
                if action in {'explore','digest','research'}:
                    self.controls[job_id]=accepted_control
                    self.jobs[job_id].update(started_at=accepted_at,deadline_at=accepted_control.deadline_at)
                    if action in {'explore','research'}:
                        self.jobs[job_id].update(product_code=edition['product_code'],news_cutoff=edition['news_cutoff'])
                self.active_job = job_id
                job_data=dict(deepcopy(data),_config=deepcopy(frozen))
                if action in {'research','explore'}:
                    job_data['_edition']=deepcopy(edition)
                threading.Thread(target=self._run_job, args=(job_id, action,job_data), daemon=True).start()
                return {'job_id': job_id}
            with closing(self.connection()) as conn:
                if action=='writing-confirm':
                    from .writing import confirm_outline, public_version
                    return public_version(confirm_outline(conn,data))
                if action=='writing-export':
                    from .writing import export_draft
                    return export_draft(conn,data['version_id'])
                if action=='writing-recheck':
                    from .writing import recheck_draft
                    return recheck_draft(conn,data)
                if action in {'pool-add', 'pool-remove'}:
                    from .writing_background import add_candidate, remove_candidate
                    from .source_catalog import configured_view
                    if action == 'pool-add':
                        return add_candidate(conn, configured_view(conn,self.config,data.get('reader_profile','default')),
                                             data['edition_id'], data['event_id'])
                    return remove_candidate(conn, data['edition_id'], data['event_id'], data['expected_revision'])
                if action=='reading-adopt':
                    from .reading_reports import adopt_report_item
                    return adopt_report_item(conn,data.get('reader_profile','default'),data['report_id'],
                                             data['item_id'],data['edition_id'])
                if action=='reader-profile':
                    from .source_catalog import save_profile
                    return save_profile(conn,{k:v for k,v in data.items() if k!='reader_profile'})
                if action=='reader-source':
                    from .source_catalog import save_source
                    return save_source(conn,self.config,data.get('reader_profile','default'),
                                       {k:v for k,v in data.items() if k!='reader_profile'})
                if action == 'edition':
                    from .cli_actions import parse_markets
                    return editorial.create_edition(conn, self.config, product_code=data['product_code'],
                        edition_date=data['edition_date'], news_cutoff=data['news_cutoff'],
                        market_cutoffs=parse_markets(data.get('market_cutoffs', [])))
                if action == 'decision':
                    background = {k: data[k] for k in ('selected_clue_ids', 'manual_notes','must_include_ids') if k in data}
                    return editorial.save_decision(conn, edition_id=data['edition_id'],
                        selected_event_id=data.get('selected_event_id'), selected_fact_ids=data.get('selected_fact_ids', []),
                        expected_revision=data['expected_revision'], actor_type='human', mainline_note=data.get('mainline_note') or None,
                        config=self.config, **background)
                if action == 'export':
                    record = editorial.export_handoff(conn, self.config, edition_id=data['edition_id'],
                        decision_revision=data['decision_revision'], output_dir=self.output_dir)
                    return dict(record, markdown_preview=Path(record['paths']['markdown']).read_text(encoding='utf-8'))
                if action == 'fact':
                    return editorial.add_fact_from_evidence(conn, edition_id=data['edition_id'], event_id=data['event_id'],
                        evidence_id=data['evidence_id'], statement=data['statement'], kind=data['kind'], actor_type='human')
                if action == 'import':
                    return self._import(conn, data)
                raise ValueError('Unknown action')

    def _import(self, conn, data):
        content = data['content']
        if not isinstance(content, str) or len(content.encode('utf-8')) > 2_000_000:
            raise ValueError('材料须为不超过 2 MB 的 UTF-8 文本')
        name = data.get('filename', 'pasted.txt')
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Invalid upload filename')
        editorial._reject_secrets(Path(name), content)
        suffix = Path(name).suffix.lower()
        if suffix not in {'.json', '.md', '.txt'}:
            raise ValueError('目前支持 JSON、Markdown、TXT；PDF、图片和 Excel 尚未接入')
        payload = loads(content) if suffix == '.json' else {
            'title': data.get('title') or name, 'content': content, 'is_fixture': self.offline,
            'source_type': 'user_document', 'first_published_at': data.get('first_published_at') or None,
            'source_url': data.get('source_url') or None, 'facts': []}
        kind = data.get('kind', 'evidence')
        if kind == 'evidence':
            result = editorial.import_evidence_payload(conn, payload, edition_id=data['edition_id'],
                event_id=data.get('event_id') or None, actual_source_path='user:upload:' + name,
                declared_source=data.get('declared_source') or None, privacy_class='private_local')
            from .writing_background import add_candidate
            add_candidate(conn, self.config, data['edition_id'], result['event_id'])
            return result
        # Product/history imports keep the exact uploaded file locally for provenance.
        upload_dir = Path(self.database).parent / 'uploads'
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / (uuid.uuid4().hex + suffix)
        with path.open('x', encoding='utf-8') as handle:
            handle.write(content)
        if kind == 'product':
            return editorial.import_product_data(conn, path, authorized=True, product_code=data['product_code'], declared_source=data.get('declared_source') or None)
        if kind == 'history':
            return editorial.import_history(conn, path, authorized=True, product_code=data['product_code'],
                publication_time=data.get('first_published_at') or None, title=data.get('title') or None,
                completeness=data.get('completeness') or None)
        raise ValueError('Unknown material type')

    def download(self, export_id, kind):
        if kind not in {'markdown', 'json'}:
            raise ValueError('Unknown export format')
        with closing(self.connection()) as conn:
            row = conn.execute('SELECT data_json FROM fn_exports WHERE export_id=?', (export_id,)).fetchone()
            if not row:
                raise ValueError('Export not found')
            record = json.loads(row[0])
            path = Path(record['paths'][kind]).resolve()
            if not path.is_relative_to(self.output_dir):
                raise ValueError('Export belongs to another output directory')
            return path.read_bytes(), path.name


def make_server(app, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No material, path, token or content in access logs.

        def _trusted(self, write=False):
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in hosts}:
                return False
            if write:
                return secrets.compare_digest(self.headers.get('X-Fastnews-CSRF', ''), app.token)
            return True

        def _send(self, code, body, mime='application/json; charset=utf-8', filename=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._trusted():
                return self._send(403, {'error': 'Only same-site local access is allowed'})
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            try:
                if request.path in {'/', '/app.js', '/style.css', '/editorial.css'}:
                    names = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css'), '/editorial.css': ('editorial.css', 'text/css')}
                    filename, mime = names[request.path]
                    return self._send(200, (STATIC / filename).read_bytes(), mime + '; charset=utf-8')
                if request.path in {'/reader.js', '/workflow.js', '/workspace.js', '/visuals.js','/exploration.js','/writing.js'}:
                    return self._send(200,(STATIC/request.path[1:]).read_bytes(),'text/javascript; charset=utf-8')
                if request.path == '/api/state':
                    result = app.state(query.get('profile',['default'])[0])
                elif request.path == '/api/daily':
                    result = app.daily(query['date'][0],query.get('profile',['default'])[0])
                elif request.path == '/api/edition':
                    result = app.detail(query['id'][0],query.get('profile',['default'])[0])
                elif request.path == '/api/job':
                    with app.guard:
                        result = dict(app.jobs[query['id'][0]])
                elif request.path == '/api/download':
                    content, filename = app.download(query['id'][0], query['kind'][0])
                    return self._send(200, content, 'application/octet-stream', filename)
                else:
                    return self._send(404, {'error': 'Not found'})
                return self._send(200, result)
            except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
                return self._send(400, {'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})

        def do_POST(self):
            if not self._trusted(write=True):
                return self._send(403, {'error': 'Local session token and same origin required'})
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON request required')
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 < size <= 2_100_000:
                    raise ValueError('Request size must be between 1 byte and 2 MB')
                body = loads(self.rfile.read(size).decode('utf-8'))
                path = urlsplit(self.path).path
                if not path.startswith('/api/'):
                    return self._send(404, {'error': 'Not found'})
                result = app.mutate(path.removeprefix('/api/'), body)
                return self._send(200, result)
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                return self._send(400, {'error': str(exc) if isinstance(exc, ValueError) else type(exc).__name__})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(database, config=None, *, offline=False, port=8765):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid local port')
    app = LocalApplication(database, config or load_config(), offline)
    with closing(app.connection()) as conn:
        from .source_catalog import configured_view
        sync_registry(conn, configured_view(conn, app.config))
    server = make_server(app, port)
    print(f'本地看板：http://127.0.0.1:{server.server_port} · ' + ('合成测试' if offline else '真实数据模式') + ' · Ctrl+C 停止', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
