"""Versioned outline -> human confirmation -> evidence-bound draft workflow."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

import yaml

from . import editorial as ed
from .budget import utc_now
from .host_provider import run_host, ProcessGroup, clean_environment
from .metadata import normalize_url, parse_instant, time_bounds
from .reader_limits import reader_limits
from .source_access import source_constraints, url_policy
from .task_control import TaskControl
from .writing_checks import issue, check_text, qualify_claim, numeric_tokens, number_units, changed_quantity_scopes, within_cutoff, normalized, reference_date, FUND_MANAGER_NAMES
from .writing_contracts import OUTLINE, OUTLINE_RESEARCH, DRAFT, DRAFT_V64

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills'/'huatai-fastnews-writing'


def dumps(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def policy():
    value=yaml.safe_load((ROOT/'config'/'writing.yaml').read_text(encoding='utf-8'))
    if not 30<=value['deadline_seconds']<=480 or not 1<=value['max_pages']<=20:
        raise ValueError('写作时限或页面预算配置无效')
    if value['finalize_seconds']<30 or value['host_seconds']>450:
        raise ValueError('写作任务必须预留保存和清理时间')
    if type(value['max_source_bytes']) is not int or not 100000 <= value['max_source_bytes'] <= 4000000:
        raise ValueError('单份公开资料字节上限需在100000至4000000之间')
    if type(value['max_input_characters']) is not int or not 24000<=value['max_input_characters']<=96000:
        raise ValueError('写作输入容量需在24000至96000字符之间')
    return value


def instructions(stage):
    # The host receives text, not access to linked skill files. Load the shared
    # style guide explicitly so it is also covered by the frozen skill hash.
    paths=[SKILL/'SKILL.md',SKILL/'guides'/'common.md',SKILL/'guides'/'style.md',SKILL/'guides'/(stage+'.md')]
    if stage=='outline':
        paths.append(SKILL/'guides'/'supplement.md')
    return '\n'.join(path.read_text(encoding='utf-8') for path in paths)


def task_attempts(conn):
    today=parse_instant(utc_now()).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    lo,hi=time_bounds(today)
    from .host_task_budget import attempts
    return attempts(conn,lo,hi)


def _decision(conn, edition_id, revision):
    current=ed.latest_decision(conn,edition_id)
    if not current or type(revision) is not int or current['decision_revision']!=revision:
        raise ValueError('选材版本已改变，请保存并按当前材料重新整理框架')
    return current


def version(conn, identifier):
    row=conn.execute('SELECT * FROM fn_writing_versions WHERE version_id=?',(identifier,)).fetchone()
    if not row:
        raise ValueError('写作版本不存在')
    item=dict(row)
    for key in ('content','snapshot','checks'):
        item[key]=json.loads(item.pop(key+'_json'))
    return item


def citation_publisher(evidence,url,fallback):
    """A source label is not independent verification of its data provider."""
    label=evidence.get('declared_source') or fallback
    if evidence.get('source_type')=='media' and str(label).strip().casefold()=='wind':
        from urllib.parse import urlsplit
        return (urlsplit(url or '').hostname or '媒体')+'（转引Wind）'
    return label


def _snapshot(conn, config, edition_id, revision):
    decision=_decision(conn,edition_id,revision)
    edition=ed.get_edition(conn,edition_id)
    materials=[]
    sources=[]
    preferences=[]
    selected_clues=deepcopy(decision.get('selected_clues',[]))
    if decision.get('selected_event_id') and decision['selected_event_id'] not in {c['event_id'] for c in selected_clues}:
        from .writing_background import snapshot_event
        selected_clues.append(snapshot_event(conn,config,edition_id,decision['selected_event_id']))
    for clue in selected_clues:
        mid=clue['event_id']
        materials.append(dict(id=mid,title=clue['title'],summary=clue['summary'],relation=clue.get('why',''),
            origin=clue.get('origin'),must_include=mid in decision.get('must_include_ids',[])))
        for source in clue.get('sources',[]):
            text=source.get('excerpt','')
            observed=source.get('source_excerpt_verified') is True
            # Only the explicit original document kinds can supply local text.
            if clue.get('origin')=='user_material':
                docs=conn.execute('''SELECT d.content,d.metadata_json,e.data_json
                  FROM fn_edition_evidence ee JOIN fn_evidence e USING(evidence_id)
                  JOIN fn_documents d USING(document_id) WHERE ee.edition_id=? AND ee.event_id=? AND d.document_id=?''',
                  (edition_id,mid,source.get('document_id'))).fetchall()
                for doc in docs:
                    evidence=json.loads(doc['data_json'])
                    if evidence['source_type'] not in {'search_snippet','user_text'}:
                        text=doc['content']; observed=True
                        label=citation_publisher(evidence,source.get('url'),source.get('source_name'))
                        source=dict(source,published_at=evidence['first_published_at'],source_name=label,
                            publisher_label_basis='selected_document_declaration' if evidence.get('declared_source') else 'source_metadata')
                        break
            sources.append(dict(id='src_'+str(len(sources)+1),material_id=mid,
                title=source.get('title') or clue['title'],
                name=source.get('source_name') or source.get('title') or '公开资料',
                url=source.get('url'),published_at=source.get('first_published_at') or source.get('published_at'),
                text=text if observed else '',research_hint=text if not observed else '',
                text_observed=observed,date_observed=observed and clue.get('origin')=='user_material',
                identity=source.get('material_identity') or source.get('evidence_level') or clue.get('origin'),
                source_id=source.get('source_id'),document_id=source.get('document_id'),error=None))
            if source.get('publisher_label_basis'):
                sources[-1]['publisher_label_basis']=source['publisher_label_basis']
    for note in decision.get('manual_notes',[]):
        if not note['selected']:
            continue
        if note['kind']=='writing_note':
            preferences.append({'id':note['note_id'],'content':note['content']})
            continue
        materials.append(dict(id=note['note_id'],title='人工补充',summary=note['content'],relation='',
            origin='manual_note',must_include=note['note_id'] in decision.get('must_include_ids',[])))
        sources.append(dict(id='src_'+str(len(sources)+1),material_id=note['note_id'],
            name=note.get('source_name') or '人工补充，出处待核',url=note.get('source_url'),
            published_at=note.get('published_at'),text='',research_hint=note['content'],
            text_observed=False,date_observed=False,identity='manual_note',source_id=None,error=None))
    # Legacy selected facts remain usable, with their frozen source evidence.
    for fact in ed.edition_facts(conn,edition_id):
        if fact['fact_id'] not in decision['selected_fact_ids']:
            continue
        mid=fact['fact_id']
        materials.append(dict(id=mid,title='人工选择的事实',summary=fact['statement'],relation='',origin='fact',
                             must_include=mid in decision.get('must_include_ids',[])))
        for eid in fact.get('evidence_refs',[]):
            ev=ed._evidence(conn,eid)
            doc=conn.execute('SELECT content,metadata_json FROM fn_documents WHERE document_id=?',(ev['document_id'],)).fetchone()
            original=ev.get('source_type') not in {'search_snippet','user_text'} and ev.get('access_status')!='search_snippet_only'
            observed=bool(doc) and original and fact['evidence_state'] in {'reported','verified'}
            sources.append(dict(id='src_'+str(len(sources)+1),material_id=mid,name=ev.get('declared_source') or '提供的原始资料',
                title=json.loads(doc['metadata_json']).get('title') if doc else None,
                url=ev.get('source_url'),published_at=ev.get('first_published_at'),text=doc[0] if observed else '',
                research_hint='' if observed else ev.get('excerpt',''),text_observed=observed,date_observed=observed,
                identity=ev.get('source_type') or 'selected_fact_evidence',source_id=ev.get('source_id'),error=None))
    if not materials:
        raise ValueError('请先选择事实材料；只有写作要求不能生成框架')
    from .product_context import public_context
    context = public_context(conn, edition)
    for ref in context.get('writing_references',context['references']):
        mid = ref['id']
        materials.append(dict(id=mid,title=ref['title'],summary='有日期的产品背景依据，不能当作当天新消息或当前持仓。',
            relation=ref['snapshot_type'],origin='product_reference',must_include=False))
        sources.append(dict(id='src_'+str(len(sources)+1),material_id=mid,
            name=ref['source_name'] or ref['title'],title=ref['title'],url=ref['source_url'],published_at=ref['published_at'],
            text=ref['excerpt'],research_hint='',text_observed=True,date_observed=True,
            identity=ref['source_type'],source_id=None,document_id=ref['document_id'],
            evidence_id=ref['evidence_id'],data_as_of=ref['data_as_of'],error=None))
    rules=policy()
    return dict(edition_id=edition_id,product_code=edition['product_code'],product_name=edition['product_name'],
        decision_revision=revision,news_cutoff=edition['news_cutoff'],market_cutoffs=edition['market_cutoffs'],
        mainline_note=decision.get('mainline_note'),selected_event_id=decision.get('selected_event_id'),
        materials=materials,sources=sources,preferences=preferences,rules=rules,
        product_context={'version':context['version'],'published_history':context['published_history'],
                         'gaps':context['gaps']},
        source_constraints=source_constraints(config.get('reader_sources',[])),
        is_fixture=edition['is_fixture'],skill_version=fingerprint([instructions('outline'),instructions('draft'),rules]),
        product_names=[p['name'] for p in config['products'].values()],
        product_codes=list(config['products']))


def request_signature(stage,data):
    return fingerprint({'stage':stage,**{k:v for k,v in data.items() if not k.startswith('_')}})


def previous_request(conn,stage,data):
    rid=data.get('request_id')
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):
        raise ValueError('写作任务需要有效的请求编号')
    row=conn.execute('SELECT * FROM fn_writing_runs WHERE request_id=?',(rid,)).fetchone()
    if row and row['request_hash']!=request_signature(stage,data):
        raise ValueError('此请求编号已用于不同写作内容')
    return run_record(row) if row else None


def accept_run(conn,config,stage,data,control,run_id=None):
    prior=previous_request(conn,stage,data)
    if prior:
        return prior
    if stage not in {'outline','draft'}:
        raise ValueError('未知写作阶段')
    pid=data.get('reader_profile','default')
    from .source_catalog import profile
    settings=(config.get('_reader_profile') or profile(conn,pid))['settings']
    if settings.get('host','none')=='none':
        raise ValueError('请先在来源设置中选择已登录的宿主模型')
    snapshot=_snapshot(conn,config,data['edition_id'],data['decision_revision'])
    parent=None
    feedback=data.get('feedback','')
    if not isinstance(feedback,str) or len(feedback)>policy()['max_feedback_characters']:
        raise ValueError('框架反馈最多2000字')
    if stage=='draft':
        parent=version(conn,data.get('confirmed_version_id'))
        if parent['stage']!='confirmed_outline' or parent['edition_id']!=data['edition_id']:
            raise ValueError('正文需要本期由用户确认的最终框架')
        if parent['decision_revision']!=data['decision_revision'] or parent['snapshot']['skill_version']!=snapshot['skill_version']:
            raise ValueError('选材或写作规则已更新，请重新核对并确认框架')
        snapshot=deepcopy(parent['snapshot'])
        if parent['checks']['errors']:
            raise ValueError('关键事实或规则问题尚未解决，请先补充依据或修改框架')
    elif data.get('parent_version_id'):
        parent=version(conn,data['parent_version_id'])
        if parent['edition_id']!=data['edition_id'] or parent['stage']!='outline':
            raise ValueError('框架反馈必须关联本期框架')
        if parent['decision_revision']==snapshot['decision_revision']:
            supplemental=[m for m in parent['snapshot']['materials'] if m['origin']=='outline_supplement']
            ids={m['id'] for m in supplemental}
            snapshot['materials'].extend(deepcopy(supplemental))
            snapshot['sources'].extend(deepcopy([s for s in parent['snapshot']['sources'] if s['material_id'] in ids]))
    supplement=data.get('supplement_latest',True)
    if type(supplement) is not bool:
        raise ValueError('框架补搜开关必须为布尔值')
    content={'snapshot':snapshot,'host':settings['host'],'allow_verify':settings.get('web_search',True),
        'supplement_latest':stage=='outline' and supplement and settings.get('web_search',True),
        'feedback':feedback,'parent':parent['content'] if parent else None,
        'parent_id':parent['version_id'] if parent else None,'instructions':instructions(stage)}
    if config.get('_host_runtime'):
        content['host_runtime']={k:v for k,v in config['_host_runtime'].items() if k in {'model','reasoning_effort'}}
    # Bound selected user content, not source retrieval or instruction overhead.
    if len(dumps(snapshot['materials'])+dumps(snapshot['preferences'])+feedback)>reader_limits()['max_input_characters']:
        raise ValueError('选材超出本次输入预算，请精简补充；未丢弃任何必写项')
    identifier=run_id or 'writing_'+uuid.uuid4().hex
    from .host_task_budget import reservation
    with reservation(conn):
        conn.execute('BEGIN IMMEDIATE')
        try:
            from .host_task_budget import status, exhausted_message
            budget=status(conn,used=task_attempts(conn),limit=reader_limits()['max_tasks_per_day'])
            if budget['remaining']==0:
                raise ValueError(exhausted_message(budget))
            control.check()
            conn.execute('''INSERT INTO fn_writing_runs(run_id,request_id,request_hash,edition_id,decision_revision,stage,
                profile_id,status,input_json,created_at,deadline_at) VALUES(?,?,?,?,?,?,?,'running',?,?,?)''',
                (identifier,data['request_id'],request_signature(stage,data),data['edition_id'],data['decision_revision'],
                 stage,pid,dumps(content),control.started_at,control.deadline_at))
            conn.commit()
        except BaseException:
            conn.rollback(); raise
    return run_record(conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(identifier,)).fetchone())


def _read_source(url,control,seconds):
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    process=subprocess.Popen([sys.executable,'-X','utf8','-m','fastnews.writing_fetch'],cwd=ROOT,env=clean_environment(),
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',**flags)
    group=ProcessGroup(process)
    end=min(time.monotonic()+seconds,control.deadline-30)
    try:
        first=True
        while True:
            control.check()
            remaining=end-time.monotonic()
            if remaining<=0:
                return {'error':'source_timeout'}
            try:
                output,_=process.communicate(dumps({'url':url}) if first else None,timeout=min(.25,remaining))
                return json.loads(output) if process.returncode==0 else {'error':'read_failed'}
            except subprocess.TimeoutExpired:
                first=False
    finally:
        group.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def verify_sources(snapshot,control,progress=None,fetcher=None,*,seconds=None,source_ids=None,budget_state=None):
    fetcher=fetcher or _read_source
    rules=snapshot['rules']
    state=budget_state if budget_state is not None else {}
    started=time.monotonic()
    allowance=min(seconds if seconds is not None else rules['verification_seconds'],
                  max(0,rules['verification_seconds']-state.get('elapsed',0)))
    until=min(started+allowance,control.deadline-rules['finalize_seconds'])
    seen=state.setdefault('seen',{}); counts=state.setdefault('counts',{}); total=state.get('total',0)
    selected=[s for s in snapshot['sources'] if source_ids is None or s['id'] in source_ids]
    from .live_sources import load_live_config
    program_rules=load_live_config()['sources']
    for source in selected:
        control.check()
        if source['text_observed']:
            continue
        if (str(source.get('error') or '').startswith(('robots_','unsafe_')) or
            source.get('error') in {'source_restricted','program_read_restricted','access_challenge',
                'http_401','http_403','http_412','http_429','tls_certificate_invalid'}):
            continue
        mid=source['material_id']
        url=source.get('url')
        if not url:
            source['error']='missing_source_url'; continue
        try:
            url=normalize_url(url)
            access=url_policy(url,snapshot['source_constraints'],source.get('source_id'))
        except ValueError:
            source['error']='unsafe_url'; continue
        if not access['host_read_allowed']:
            source['error']='source_restricted'; continue
        # Known collector restrictions remain separate from host permissions.
        sid=source.get('source_id')
        from urllib.parse import urlsplit
        host=urlsplit(url).hostname.removeprefix('www.')
        matching=[r for key,r in program_rules.items() if key==sid or
            urlsplit(r.get('listing_url','')).hostname and urlsplit(r['listing_url']).hostname.removeprefix('www.')==host]
        if any(r.get('fulltext_allowed') is not True for r in matching):
            source['error']='program_read_restricted'; continue
        if url not in seen and (time.monotonic()>=until or total>=rules['max_pages'] or counts.get(mid,0)>=rules['pages_per_material']):
            source['error']='verification_budget'; continue
        if progress:
            progress({'stage':'writing_verify','message':('核对新增资料出处 ' if source_ids is not None else '核对已选材料出处 ')+str(total+1)})
        if url not in seen:
            seen[url]=fetcher(url,control,max(.1,min(rules['page_seconds'],until-time.monotonic())))
            total+=1; counts[mid]=counts.get(mid,0)+1
        result=seen[url]
        if result.get('error'):
            source['error']=result['error']; continue
        source.update(text=result['text'],text_observed=True,identity='program_fragment',
                      retrieved_at=result.get('retrieved_at'),error=None)
        if result.get('title'):
            source['title']=result['title']
        if result.get('publisher'):
            source['name']=result['publisher']
        source['upstream_text_truncated']=result.get('text_truncated',False)
        observed_date=result.get('published_at')
        try:
            if observed_date:
                time_bounds(observed_date)
                source.update(published_at=observed_date,date_observed=True)
        except ValueError:
            pass
    # Excerpts are explicitly bounded; missing passages remain a visible gap.
    state.update(total=total,elapsed=state.get('elapsed',0)+time.monotonic()-started)
    per_source=max(600,min(5000,14000//max(1,len(snapshot['sources']))))
    for source in selected:
        full=source.get('text','')
        source['document_hash']=hashlib.sha256(full.encode()).hexdigest() if source['text_observed'] else None
        source['text_truncated']=len(full)>per_source or source.get('upstream_text_truncated',False)
        material=next((m for m in snapshot['materials'] if m['id']==source['material_id']),{})
        focus=' '.join([snapshot.get('mainline_note') or '',material.get('title',''),material.get('summary','')])
        source['text_segments']=source_segments(full,per_source,focus)
        source['text']='\n【分段摘录：中间有省略】\n'.join(source['text_segments'])
        source['fingerprint']=fingerprint(source)
    return snapshot


def source_segments(text,limit,focus):
    """Keep the lead and located passages; never pretend disjoint text is whole."""
    if len(text)<=limit:return [text] if text else []
    lead=limit//2; ranges=[(0,lead)]; remaining=limit-lead
    terms=list(dict.fromkeys(re.findall(r'\d+(?:\.\d+)?\s*(?:%|亿元|亿美元|亿港元)',focus)))
    terms+=['Phase 2b','Phase 3','首付款','里程碑','跟踪','业绩比较基准']
    for term in terms:
        at=text.find(term,lead)
        if at<0 or remaining<100 or any(start<=at<end for start,end in ranges):continue
        start=max(lead,at-250);end=min(len(text),start+min(900,remaining))
        # Adjacent windows merge so every quoted passage remains a real substring.
        if any(start<e and end>s for s,e in ranges):continue
        ranges.append((start,end));remaining-=end-start
    if remaining>0:ranges[0]=(0,min(lead+remaining,min((s for s,e in ranges if s>0),default=len(text))))
    return [text[start:end] for start,end in sorted(ranges)]


def _validate_shape(raw,schema):
    ed._validate_schema(raw,schema,'writing')
    if len(dumps(raw))>85000:
        raise ValueError('写作结果超过保存上限')
    if len(raw.get('paragraphs',[]))>12 or len(raw.get('claims',[]))>50 or len(raw.get('entities',[]))>80:
        raise ValueError('写作结果条目过多')


def _entities(raw,sources):
    errors=[]
    for e in raw:
        source=sources.get(e['source_id'])
        # Index publishers may be attributed in Chinese from their English PDF.
        # Only known names matching the declared publisher qualify as aliases.
        publisher_aliases={'恒生指数公司':'Hang Seng Indexes Company','恒生指数有限公司':'Hang Seng Indexes Company',
                           '中证指数有限公司':'China Securities Index Company'}
        alias=publisher_aliases.get(e['name']) if source and source.get('name')==e['name'] else None
        identified=normalized(e['name']) in normalized(e['quote']) or bool(alias and normalized(alias) in normalized(e['quote']))
        # PDF line breaks can also split an English word. Ignore layout only,
        # checking each real segment separately, without joining omitted text.
        observed=bool(source and e['quote'] and any(normalized(e['quote']) in normalized(part) for part in source.get('text_segments',[source.get('text','')])))
        if not e['name'] or not source or not source.get('text_observed') or not observed or not identified:
            errors.append(issue('entity_identity','具体公司缺少可核对的身份依据',e.get('name','')))
        if e['market']=='UNKNOWN' and e['name'] not in FUND_MANAGER_NAMES:
            errors.append(issue('entity_identity','公司上市身份待核实',e['name']))
    return errors


def validate_outline(raw,snapshot):
    _validate_shape(raw,OUTLINE)
    materials={m['id']:m for m in snapshot['materials']}
    sources={s['id']:s for s in snapshot['sources']}
    claims={}; errors=[]
    if not raw['mainline'].strip() or not 1<=len(raw['paragraphs'])<=12 or not 1<=len(raw['title_directions'])<=3:
        raise ValueError('框架缺少主线、标题方向或段落')
    for c in raw['claims']:
        if not c['id'] or c['id'] in claims or c['material_id'] not in materials:
            raise ValueError('陈述编号重复或引用了未选材料')
        checks=qualify_claim(c,sources,snapshot['news_cutoff'],snapshot['product_code'])
        c=deepcopy(c); c.update(checks=checks,usable=not checks)
        claims[c['id']]=c
    seen=set(); used=set(); required_claims=set()
    for p in raw['paragraphs']:
        if not p['id'] or p['id'] in seen or set(p['material_ids'])-set(materials) or set(p['claim_ids'])-set(claims):
            raise ValueError('段落编号或材料引用无效')
        seen.add(p['id']); used.update(p['material_ids']); required_claims.update(p['claim_ids'])
        if not p['claim_ids']:
            errors.append(issue('paragraph_evidence','拟写段落缺少事实依据',p['id']))
        if any(claims[c]['material_id'] not in p['material_ids'] for c in p['claim_ids']):
            raise ValueError('段落的陈述与材料映射不一致')
    for cid in required_claims:
        errors.extend(claims[cid]['checks'])
    dispositions=[d['material_id'] for d in raw['dispositions']]
    if len(dispositions)!=len(set(dispositions)) or set(dispositions)!=set(materials):
        raise ValueError('框架必须说明每条已选材料的安排或未用原因')
    for mid,m in materials.items():
        if m['must_include'] and mid not in used:
            errors.append(issue('must_include','框架遗漏必须体现的材料',mid))
    if snapshot.get('selected_event_id') and snapshot['selected_event_id'] in materials and snapshot['selected_event_id'] not in used:
        errors.append(issue('main_event','框架遗漏人工主事件',snapshot['selected_event_id']))
    # Unused unavailable research may remain in the ledger without blocking a valid mainline.
    errors.extend(_entities(raw['entities'],sources))
    for p in raw['paragraphs']:
        errors.extend(check_text('。'.join(p['points'])+'。'+p['product_connection'],location=p['id'],
            entities=raw['entities'],product_codes=snapshot['product_codes'],cutoff=snapshot['news_cutoff'],editorial=True))
    for title in raw['title_directions']:
        errors.extend(check_text(title,location='title',entities=raw['entities'],product_codes=snapshot['product_codes'],cutoff=snapshot['news_cutoff']))
    if raw['blocking_gaps']:
        errors.extend(issue('model_gap',g) for g in raw['blocking_gaps'])
    if len(raw['supplement_queries'])>3 or any(len(q)>500 for q in raw['supplement_queries']):
        raise ValueError('补搜问题最多3条、每条500字')
    content=deepcopy(raw); content['claims']=list(claims.values())
    content['paragraph_sources']={}
    for p in raw['paragraphs']:
        unique={}
        for cid in p['claim_ids']:
            source=sources.get(claims[cid]['source_id'])
            if source:
                key=(source.get('url'),source.get('title'),claims[cid]['published_at'])
                unique[key]=dict(name=source['name'],title=source.get('title') or source['name'],
                    url=source.get('url'),published_at=claims[cid]['published_at'])
        content['paragraph_sources'][p['id']]=list(unique.values())
    content['text']=outline_text(content)
    return content,dict(errors=errors,status='needs_materials' if errors else 'ready_to_confirm')


def outline_source_line(refs):
    def single(value):return ' '.join(str(value or '').split())
    return '资料／数据来源：'+'；'.join(single(s['name'])+'《'+single(s['title'])+'》，'+single(s['published_at'])+
        ('，'+single(s['url']) if s.get('url') else '，用户提供的原始资料') for s in refs)


def outline_text(content):
    parts=['主线：'+content['mainline'],'标题方向：'+' / '.join(content['title_directions'])]
    for i,p in enumerate(content['paragraphs'],1):
        parts.append(f"第{i}段｜{p['purpose']}\n"+'\n'.join('- '+t for t in p['points'])+
                     '\n分析：'+p['analysis']+'\n产品承接：'+p['product_connection'])
        refs=content.get('paragraph_sources',{}).get(p['id'],[])
        if refs:
            parts[-1]+='\n'+outline_source_line(refs)
    return '\n\n'.join(parts)


def confirm_outline(conn,data):
    rid=data.get('request_id','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):
        raise ValueError('确认框架需要请求编号')
    body=data.get('text')
    if not isinstance(body,str) or not body.strip() or len(body)>policy()['max_outline_characters']:
        raise ValueError('请粘贴最终框架，最多10000字')
    parent=version(conn,data['outline_version_id'])
    if parent['stage']!='outline' or parent['edition_id']!=data['edition_id']:
        raise ValueError('请选择本期的框架版本')
    identity=fingerprint([parent['version_id'],body,data['decision_revision']])
    old=conn.execute('SELECT version_id,input_hash FROM fn_writing_versions WHERE request_id=?',(rid,)).fetchone()
    if old:
        if old['input_hash']!=identity:
            raise ValueError('此确认请求编号已用于其他框架')
        return version(conn,old['version_id'])
    ed._reject_secrets(Path('confirmed-outline.txt'),body)
    conn.execute('BEGIN IMMEDIATE')
    try:
        _decision(conn,data['edition_id'],data['decision_revision'])
        if parent['decision_revision']!=data['decision_revision']:
            raise ValueError('框架对应旧选材，请先按当前材料更新框架')
        snapshot=deepcopy(parent['snapshot'])
        # Carry human-supplied publisher labels into a new confirmation version.
        # Never change the original outline, URL, evidence identity or claim text.
        from urllib.parse import urlsplit
        for source in snapshot['sources']:
            host=urlsplit(source.get('url') or '').hostname
            if not source.get('document_id') or not host or source['name'] not in {host,host.removeprefix('www.')}:
                continue
            rows=conn.execute('''SELECT e.data_json FROM fn_edition_evidence ee
                JOIN fn_evidence e USING(evidence_id) WHERE ee.edition_id=? AND e.document_id=?''',
                (data['edition_id'],source['document_id'])).fetchall()
            evidence=[json.loads(row['data_json']) for row in rows]
            labels={citation_publisher(e,source.get('url'),source['name']) for e in evidence
                if e.get('declared_source') and e.get('source_type') not in {'user_text','search_snippet'}}
            if len(labels)==1:
                source.update(name=labels.pop(),publisher_label_basis='selected_document_declaration')
        snapshot['rules']=policy()
        snapshot['skill_version']=fingerprint([instructions('outline'),instructions('draft'),snapshot['rules']])
        proposal={key:deepcopy(parent['content'].get(key,[])) for key in OUTLINE['properties']}
        for claim in proposal['claims']:
            claim.pop('checks',None);claim.pop('usable',None)
        if 'blocking_gaps' not in parent['content']:
            proposal['blocking_gaps']=parent['content'].get('gaps',[])
        reviewed,checks=validate_outline(proposal,snapshot)
        # Only exact, program-rendered references from the saved parent are exempt.
        # Edited references and user-authored "source" lines still receive checks.
        from collections import Counter
        source_lines=Counter(outline_source_line(refs) for refs in parent['content'].get('paragraph_sources',{}).values() if refs)
        body_lines=[]
        for line in body.splitlines():
            if source_lines[line]>0:source_lines[line]-=1
            else:body_lines.append(line)
        checks['errors']+=check_text('\n'.join(body_lines),location='confirmed_outline',entities=parent['content']['entities'],
            product_codes=parent['snapshot']['product_codes'],cutoff=parent['snapshot']['news_cutoff'],editorial=True)
        if checks['errors']:
            raise ValueError('框架仍有关键依据或规则问题，请先补齐材料并更新框架')
        if snapshot.get('supplemental_research',{}).get('enabled'):
            snapshot['supplemental_research']['approval']='confirmed_with_outline'
        result=_save_version(conn,snapshot,'confirmed_outline',
            dict(reviewed,confirmed_text=body),checks,parent_id=parent['version_id'],request_id=rid,input_hash=identity)
        conn.commit()
    except BaseException:
        conn.rollback(); raise
    return result


def validate_draft(raw,snapshot,outline):
    v64='v6.4' in snapshot.get('rules',{}).get('version','')
    _validate_shape(raw,DRAFT_V64 if v64 else DRAFT)
    errors=[issue('model_review',t) for t in raw['issues']]
    if raw['status']!='ready':
        errors.append(issue('needs_materials','最终框架有事实缺口，未生成可复制稿'))
    if not all(raw['review'].values()):
        errors.append(issue('semantic_review','模型检查发现框架、证据或必写项未满足'))
    claims={c['id']:c for c in outline['claims']}
    sources={s['id']:s for s in snapshot['sources']}
    errors+=_entities(raw['entities'],sources)
    seen=set(); used=set(); rendered=[]
    allowed_title=set(); title_evidence=[]
    for index,p in enumerate(raw['paragraphs'],1):
        ids=p['claim_ids']; mid=p['material_ids']; oid=p['outline_paragraph_id']
        if not ids or set(ids)-set(claims) or not oid or not p['outline_anchor'].strip() or p['outline_anchor'] not in outline['confirmed_text']:
            errors.append(issue('paragraph_citation','段落缺少有效事实或框架引用',str(index))); continue
        if any(not claims[c]['usable'] for c in ids):
            errors.append(issue('unverified_fact','段落引用了尚待核实的陈述',str(index)))
        if set(mid)!={claims[c]['material_id'] for c in ids}:
            errors.append(issue('outline_mapping','段落的材料与事实引用不一致',str(index)))
        used.update(mid); seen.add(oid)
        evidence=' '.join(claims[c]['quote']+' '+claims[c]['statement'] for c in ids)
        citation_dates=' '.join((claims[c]['published_at'] or '')+' '+claims[c]['data_period']+' '+reference_date(sources[claims[c]['source_id']]) for c in ids if claims[c]['usable'])
        allowed=numeric_tokens(evidence+' '+citation_dates+' '+snapshot['product_name']+' '+snapshot['product_code'])
        allowed_title.update(allowed)
        quotes=' '.join(claims[c]['quote'] for c in ids)
        title_evidence.append(quotes)
        if numeric_tokens(p['text'])-allowed:
            errors.append(issue('new_number','正文包含所引事实没有的数字',str(index)))
        supported_units=number_units(evidence)
        for cid in ids:
            claim=claims[cid]
            supported_units.update(number_units(claim['quote'],table_context=sources[claim['source_id']].get('text','')))
        if number_units(p['text'])-supported_units:
            errors.append(issue('new_unit','正文的数值、单位或币种组合不对应所引事实',str(index)))
        if changed_quantity_scopes(p['text'],quotes):
            errors.append(issue('new_quantity_scope','正文改变了原文数值的约数或上下限口径',str(index)))
        errs=check_text(p['text'],location=str(index),entities=outline['entities']+raw['entities'],
                        product_codes=snapshot['product_codes'],product_names=snapshot.get('product_names',[]),
                        cutoff=snapshot['news_cutoff'])
        errors.extend(errs)
        refs=list(dict.fromkeys(claims[c]['source_id'] for c in ids))
        citation=[]
        for sid in refs:
            s=sources[sid]; cited=[claims[c] for c in ids if claims[c]['source_id']==sid]
            periods=list(dict.fromkeys(c['data_period'] for c in cited if c['data_period']))
            dates=list(dict.fromkeys(c['published_at'] or s.get('published_at') or '日期待核' for c in cited))
            citation.append(dict(source_id=sid,name=s['name'],title=s.get('title') or s['name'],url=s.get('url'),published_at='、'.join(dates),
                                 data_period='；'.join(periods)))
        rendered.append(dict(p,sources=citation))
    if raw['status']=='ready' and (not raw['title'].strip() or not raw['paragraphs']):
        errors.append(issue('empty_draft','正文为空'))
    for m in snapshot['materials']:
        if m['must_include'] and m['id'] not in used:
            errors.append(issue('must_include','正文遗漏必须体现的材料',m['id']))
    if numeric_tokens(raw['title'])-allowed_title:
        errors.append(issue('title_number','标题数字缺少正文依据'))
    if number_units(raw['title'])-number_units(' '.join(title_evidence)):
        errors.append(issue('title_unit','标题的数值、单位或币种组合不对应所引事实'))
    if changed_quantity_scopes(raw['title'],' '.join(title_evidence)):
        errors.append(issue('title_quantity_scope','标题改变了原文数值的约数或上下限口径'))
    errors.extend(check_text(raw['title'],location='title',entities=outline['entities']+raw['entities'],
        product_codes=snapshot['product_codes'],product_names=snapshot.get('product_names',[]),cutoff=snapshot['news_cutoff']))
    content=dict(raw,paragraphs=rendered,risk_notice=risk_notice(snapshot),news_cutoff=snapshot['news_cutoff'],
                 body_characters=sum(len(p['text']) for p in raw['paragraphs']))
    content['text']=render_draft(content,compact_sources=v64)
    from .writing_quality import quality_checks
    editorial=quality_checks(raw,content['body_characters'],v64)
    errors.extend(editorial['errors'])
    policy_codes={'a_share_company','hk_company','unknown_company','a_share_code','hk_code',
        'index_identity','product_flow','short_performance','certainty','flow_period'}
    return content,dict(errors=errors,status='needs_changes' if errors else 'ready',
        policy_version=snapshot.get('rules',{}).get('version','legacy'),
        factual={'errors':[e for e in errors if e['code'] not in policy_codes and e not in editorial['errors']]},
        policy={'errors':[e for e in errors if e['code'] in policy_codes]},
        editorial=editorial,human_acceptance='pending',adoption_ready=None)


def risk_notice(snapshot):
    templates=yaml.safe_load((ROOT/'config'/'writing_risk.yaml').read_text(encoding='utf-8'))
    general=templates['general']; product=templates['products'].get(snapshot['product_code'])
    sources=[s for s in (general,product) if s and within_cutoff(s['source_date'],snapshot['news_cutoff'])]
    return dict(version=templates['version'],text='\n'.join(s['text'] for s in sources),
        source_url=(sources[-1] if sources else general)['source_url'],sources=sources,
        status='sourced_template_formal_appendix_pending' if product in sources else 'general_template_product_appendix_pending',
        missing=[templates['missing']]+([] if product in sources else ['本产品特殊风险模板尚未取得本期可用出处，请补充正式备注。']))


def render_draft(content,*,compact_sources=False):
    lines=[content['title']]
    for p in content['paragraphs']:
        refs=[]; seen=set()
        for s in p.get('sources',[]):
            single=lambda value:' '.join(str(value or '').split())
            title=single(s.get('title') or s['name'])
            publisher=single(s['name']) if single(s['name'])!=title else '发布机构待核'
            key=(publisher,title,single(s['published_at']),s.get('url'))
            if compact_sources and key in seen:continue
            seen.add(key)
            refs.append(publisher+'《'+title+'》，'+single(s['published_at'])+
                ('；统计期：'+s['data_period'] if not compact_sources and s['data_period'] else '')+
                ('；'+s['url'] if s.get('url') else '；用户提供的原始资料'))
        lines.extend(['',p['text'],'','资料／数据来源：'+'；'.join(refs)])
    return '\n'.join(lines)


def _save_version(conn,snapshot,stage,content,checks,*,parent_id=None,run_id=None,request_id=None,input_hash=None):
    identifier='wv_'+uuid.uuid4().hex
    conn.execute('''INSERT INTO fn_writing_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (identifier,snapshot['edition_id'],snapshot['decision_revision'],stage,parent_id,run_id,request_id,
         input_hash or fingerprint(snapshot),dumps(content),dumps(snapshot),dumps(checks),utc_now()))
    return version(conn,identifier)


def run_record(row):
    result=dict(row)
    saved=json.loads(result.pop('input_json'))
    result.update(job_id=result['run_id'],action='writing-'+result['stage'],reader_profile=result['profile_id'],
        error=saved.get('failure_message'),
        product_code=saved['snapshot']['product_code'],news_cutoff=saved['snapshot']['news_cutoff'],
        started_at=result['created_at'],progress=[],usage=json.loads(result.pop('usage_json') or 'null'))
    return result


def execute_run(conn,identifier,control,*,runner=run_host,fetcher=None,progress=None):
    row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(identifier,)).fetchone()
    if not row or row['status']!='running':
        raise ValueError('任务不存在或已经结束')
    data=json.loads(row['input_json']); snapshot=data['snapshot']; stage=row['stage']; response=None
    supplement=stage=='outline' and data.get('supplement_latest',False)
    verification={}
    try:
        control.check()
        if snapshot['is_fixture'] and runner is run_host:
            raise ValueError('合成测试不能调用真实写作宿主')
        if stage=='outline':
            if data['allow_verify']:
                verify_sources(snapshot,control,progress,fetcher,
                    seconds=snapshot['rules']['verification_seconds']/2 if supplement else None,budget_state=verification)
            previous={k:v for k,v in (data['parent'] or {}).items() if k in OUTLINE['properties'] and k!='claims'} or None
            raw=dict(snapshot,stage=stage,feedback=data['feedback'],previous_outline=previous)
            if supplement:
                from .writing_research import research_brief
                raw['supplemental_research']=research_brief(snapshot)
            else:
                raw['supplemental_research']={'enabled':False,'status':'disabled'}
                snapshot['supplemental_research']=deepcopy(raw['supplemental_research'])
            raw['materials']=deepcopy(snapshot['materials'])
            for material in raw['materials']:
                if material['origin']=='user_material' and any(s['material_id']==material['id'] and s['text_observed'] for s in snapshot['sources']):
                    # This generated summary repeats the imported document. Keep
                    # its selected identity and the bounded, auditable source text.
                    material['summary']='所选原始资料见本条对应 sources.text；摘录范围及截断标记随来源提供。'
                    material['summary_compacted']=True
        else:
            raw=dict(snapshot,stage=stage,confirmed_outline=data['parent']['confirmed_text'],
                     outline={k:v for k,v in data['parent'].items() if k in OUTLINE['properties'] and k!='claims'},
                     claims=data['parent']['claims'])
            raw['materials']=[{k:m[k] for k in ('id','title','must_include')} for m in snapshot['materials']]
            raw['sources']=deepcopy(snapshot['sources'])
            for source in raw['sources']:
                # Drafting needs located evidence, not whole pages or duplicate hints.
                source['text']='\n'.join(dict.fromkeys(c['quote'] for c in raw['claims'] if c['source_id']==source['id']))
                source.pop('research_hint',None)
        # Source constraints are enforced in the reader, never extra writing material.
        raw.pop('source_constraints',None)
        if stage=='draft':
            # Search traces and full returned excerpts stay in the frozen audit,
            # not in the draft's bounded evidence input a second time.
            raw.pop('supplemental_research',None)
        raw['sources']=deepcopy(raw['sources'])
        for source in raw['sources']:
            for key in ('text_segments','document_hash','fingerprint','document_id','evidence_id','publisher_label_basis'):
                source.pop(key,None)  # local audit only; public identity and exact excerpts stay
            if source.get('text_observed'):
                source.pop('research_hint',None)
        context=dumps(raw)
        capacity=snapshot['rules'].get('max_input_characters',reader_limits()['max_input_characters'])
        if len(context)>capacity:
            raise ValueError(f'本次写作输入超过{capacity}字符容量，请精简选材；必写项未被自动丢弃')
        if progress:
            progress({'stage':'writing_model','message':('正在补查最新消息并整理框架' if supplement else '正在整理框架与证据') if stage=='outline' else '正在按确认框架生成正文并检查'})
        post_verify=max(0,snapshot['rules']['verification_seconds']-verification.get('elapsed',0)) if supplement else 0
        response=runner(data['host'],data['instructions']+'\nINPUT_JSON:\n'+context,
            OUTLINE_RESEARCH if supplement else OUTLINE if stage=='outline' else DRAFT_V64 if 'v6.4' in snapshot['rules']['version'] else DRAFT,web_search=supplement,
            timeout=control.host_timeout(snapshot['rules']['host_seconds'],snapshot['rules']['finalize_seconds']+post_verify),
            control=control,progress=progress,**data.get('host_runtime',{}))
        control.check()
        payload=response.payload
        if supplement:
            from .writing_research import stage_discoveries, finalize_discoveries
            payload,added=stage_discoveries(payload,snapshot)
            snapshot['supplemental_research']['trace']=response.trace
            snapshot['supplemental_research']['reported_sources']=response.payload['supplemental_sources']
            verify_sources(snapshot,control,progress,fetcher,seconds=post_verify,source_ids=added,budget_state=verification)
            finalize_discoveries(payload,snapshot,added)
        content,checks=validate_outline(payload,snapshot) if stage=='outline' else validate_draft(payload,snapshot,data['parent'])
        control.check()
        usage=dict(response.usage,total_elapsed_seconds=round(time.monotonic()-control.started,2))
        with conn:
            saved=_save_version(conn,snapshot,stage,content,checks,parent_id=data['parent_id'],run_id=identifier)
            conn.execute("UPDATE fn_writing_runs SET status='completed',version_id=?,usage_json=?,finished_at=? WHERE run_id=?",(saved['version_id'],dumps(usage),utc_now(),identifier))
        return public_version(saved)
    except BaseException as exc:
        code=getattr(exc,'code',None) or 'failed'
        status=code if code in {'cancelled','timed_out','interrupted'} else 'failed'
        usage=dict(response.usage if response else {},total_elapsed_seconds=round(time.monotonic()-control.started,2))
        if not response:
            usage.update(input_tokens=None,output_tokens=None,cost=None,usage_status='unknown')
        with conn:
            saved_input=json.loads(row['input_json'])
            saved_input['snapshot']=snapshot
            failure=str(exc) if isinstance(exc,ValueError) or getattr(exc,'code',None) else '写作任务发生错误，请重试或查看本轮记录。'
            try:
                ed._reject_secrets(Path('writing-error.txt'),failure)
            except ValueError:
                failure='写作任务失败，错误信息包含敏感字段，已隐藏。'
            saved_input['failure_message']=failure[:1000]
            if response:
                saved_input['raw_failed_response']=response.payload
            elif getattr(exc,'host_trace',None):
                saved_input['failed_host_trace']=exc.host_trace
            conn.execute('UPDATE fn_writing_runs SET input_json=? WHERE run_id=?',(dumps(saved_input),identifier))
            conn.execute('UPDATE fn_writing_runs SET status=?,error_code=?,usage_json=?,finished_at=? WHERE run_id=?',
                (status,code,dumps(usage),utc_now(),identifier))
        raise


def public_version(record):
    result={k:v for k,v in record.items() if k!='snapshot'}
    result['product_code']=record['snapshot']['product_code']
    result['news_cutoff']=record['snapshot']['news_cutoff']
    result['skill_version']=record['snapshot']['skill_version']
    result['source_evidence']=[{k:s.get(k) for k in ('id','material_id','title','name','url','published_at','data_as_of','evidence_id','text_observed','date_observed','news_freshness','identity','error','text_truncated','fingerprint')} for s in record['snapshot']['sources']]
    research=record['snapshot'].get('supplemental_research',{})
    result['supplemental_research']={k:deepcopy(research[k]) for k in ('enabled','status','summary','source_ids','verified_sources','approval') if k in research}
    result['supplemental_materials']=[{k:m[k] for k in ('id','title','relation')} for m in record['snapshot']['materials'] if m['origin']=='outline_supplement']
    result['product_context'] = deepcopy(record['snapshot'].get('product_context', {}))
    return result


def workflow(conn,edition_id):
    decision=ed.latest_decision(conn,edition_id)
    versions=[public_version(version(conn,row[0])) for row in conn.execute('SELECT version_id FROM fn_writing_versions WHERE edition_id=? ORDER BY rowid',(edition_id,))]
    current_skill=fingerprint([instructions('outline'),instructions('draft'),policy()])
    for item in versions:
        item['stale_selection']=item['decision_revision']!=(decision or {}).get('decision_revision')
        item['stale_skill_version']=item['skill_version']!=current_skill
    runs=[run_record(row) for row in conn.execute('SELECT * FROM fn_writing_runs WHERE edition_id=? ORDER BY rowid',(edition_id,))]
    from .host_task_budget import status
    return {'versions':versions,'runs':runs,'rules_version':policy()['version'],'task_budget':status(conn)}


def export_draft(conn,identifier):
    record=version(conn,identifier)
    if record['stage']!='draft' or record['checks']['errors']:
        raise ValueError('当前稿件尚有检查问题，请修改框架或补齐依据后重新生成')
    _decision(conn,record['edition_id'],record['decision_revision'])
    current_version=fingerprint([instructions('outline'),instructions('draft'),policy()])
    if record['snapshot']['skill_version']!=current_version:
        raise ValueError('写作规则已更新，请按新规则重新检查生成')
    if review_stored_draft(conn,record)[1]['errors']:
        raise ValueError('当前程序检查发现问题，请重新检查这版初稿并按提示调整框架')
    return dict(artifact_type='human_confirmed_news_draft',version_id=identifier,
        markdown=record['content']['text'],risk_notice=record['content']['risk_notice'],
        trace=public_version(record))


def review_stored_draft(conn,parent):
    """Apply current deterministic gates without rewriting a saved version."""
    confirmed=parent
    while confirmed['stage']=='draft':confirmed=version(conn,confirmed['parent_id'])
    if confirmed['stage']!='confirmed_outline':raise ValueError('稿件没有可追溯的确认框架')
    outline=deepcopy(confirmed['content'])
    sources={s['id']:s for s in parent['snapshot']['sources']}
    for claim in outline['claims']:
        claim['checks']=qualify_claim(claim,sources,parent['snapshot']['news_cutoff'],parent['snapshot']['product_code'])
        claim['usable']=not claim['checks']
    schema=DRAFT_V64 if 'v6.4' in parent['snapshot']['rules']['version'] else DRAFT
    raw={key:deepcopy(parent['content'][key]) for key in schema['properties']}
    raw['paragraphs']=[{k:p[k] for k in DRAFT['properties']['paragraphs']['items']['properties']} for p in raw['paragraphs']]
    return validate_draft(raw,parent['snapshot'],outline)


def recheck_draft(conn,data):
    """Re-run deterministic checks after a fix, retaining the original model review."""
    parent=version(conn,data['version_id']);rid=data.get('request_id','')
    if parent['stage']!='draft' or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',rid):
        raise ValueError('重新检查需要稿件版本和有效请求编号')
    _decision(conn,parent['edition_id'],parent['decision_revision'])
    current=fingerprint([instructions('outline'),instructions('draft'),policy()])
    if parent['snapshot']['skill_version']!=current:
        raise ValueError('写作技能或规则已更新，请回到框架确认后生成；程序复查不能代替新语义检查')
    previous=conn.execute('SELECT version_id,input_hash FROM fn_writing_versions WHERE request_id=?',(rid,)).fetchone()
    signature=fingerprint(['recheck',parent['version_id']])
    if previous:
        if previous['input_hash']!=signature:raise ValueError('此复查请求编号已用于其他稿件')
        return public_version(version(conn,previous['version_id']))
    content,checks=review_stored_draft(conn,parent)
    checks['review_kind']='program_recheck_original_model_review_preserved'
    with conn:
        return public_version(_save_version(conn,parent['snapshot'],'draft',content,checks,
            parent_id=parent['version_id'],request_id=rid,input_hash=signature))
