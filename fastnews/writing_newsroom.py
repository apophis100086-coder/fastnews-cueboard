"""Compose and edit within one human-confirmed task and one deadline.

The writer sees coherent news and style examples. The editor supplies evidence
separately; deterministic gates still verify quotes, dates, quantities and scope.
"""
from copy import deepcopy
import json
import time

from .budget import utc_now
from .writing_contracts import NEWS_ARTICLE, NEWS_EDIT, DRAFT_V64
from .writing_checks import issue, qualify_claim, within_cutoff
from .writing_checks import numeric_tokens, number_units

FORMAT = 'newsroom-v1'


def located_quote(quote, source):
    """Recover PDF layout whitespace only, within a single observed segment.

    Return the actual source substring and never join skipped passages or turn
    separate table numbers into a new number by deleting their whitespace.
    """
    if not quote.strip():return quote
    compact=''.join(c for c in quote if not c.isspace())
    for part in source.get('text_segments',[source.get('text','')]):
        if quote in part:return quote
        positions=[i for i,c in enumerate(part) if not c.isspace()]
        cleaned=''.join(part[i] for i in positions)
        start=cleaned.find(compact)
        while start>=0:
            original=part[positions[start]:positions[start+len(compact)-1]+1]
            if numeric_tokens(original)==numeric_tokens(quote) and number_units(original)==number_units(quote):
                return original
            start=cleaned.find(compact,start+1)
    return quote


def enabled(snapshot):
    rules=snapshot.get('rules',{})
    return rules.get('draft_pipeline')==FORMAT and 'v6.4' in rules.get('version','')


def source_dates(snapshot, outline):
    """Reuse publication proofs accepted at explicit outline confirmation."""
    sources=deepcopy(snapshot['sources'])
    for source in sources:
        if source.get('date_observed'):continue
        for claim in outline['claims']:
            if claim['source_id']!=source['id']:continue
            if not qualify_claim(claim,{source['id']:source},snapshot['news_cutoff'],snapshot['product_code']):
                source.update(published_at=claim['published_at'],date_observed=True,
                    date_basis='confirmed_outline_publication_quote')
                break
    return sources


def editorial_brief(snapshot, outline):
    """Separate unchanged generated planning notes, preserving human edits.

    Matching is exact and counted. Anything the user changed remains in the
    confirmed writing brief, including custom analysis and source-like lines.
    The complete original confirmation remains immutable in the version store.
    """
    from collections import Counter
    from .writing import outline_source_line
    sources=Counter(outline_source_line(refs) for refs in outline.get('paragraph_sources',{}).values() if refs)
    notes=Counter()
    for paragraph in outline['paragraphs']:
        notes['分析：'+paragraph['analysis']]+=1
        notes['产品承接：'+paragraph['product_connection']]+=1
    text=[]; internal=[]; removed=0
    for line in outline['confirmed_text'].splitlines():
        if sources[line]>0:
            sources[line]-=1;removed+=1
        elif notes[line]>0:
            notes[line]-=1
            if line.partition('：')[2].strip():internal.append(line)
        else:text.append(line)
    return dict(confirmed_outline='\n'.join(text),mainline_note=snapshot.get('mainline_note'),
        preferences=snapshot.get('preferences',[]),
        internal_planning_notes=internal,
        planning_notes_usage='供编辑核对关系和范围；不是要求正文逐段解释这些边界。通过准确措辞落实，不照抄核验备忘。',
        generated_source_lines_removed=removed)


def build_input(snapshot, outline):
    """Preserve whole available passages; omit whole optional records explicitly."""
    from .writing import dumps
    materials={m['id']:m for m in snapshot['materials']}
    approved={mid for p in outline['paragraphs'] for mid in p['material_ids']}
    required={m['id'] for m in materials.values() if m['must_include']}
    if snapshot.get('selected_event_id') in materials:required.add(snapshot['selected_event_id'])
    approved|=required
    news=[]; product=[]; omitted=[]; used=[]
    for source in source_dates(snapshot,outline):
        mid=source['material_id']; reason=None
        if mid not in approved:reason='not_in_confirmed_materials'
        elif not source.get('text_observed') or not source.get('text'):reason='original_not_read'
        elif not source.get('date_observed'):reason='publication_not_verified'
        elif not within_cutoff(source.get('published_at'),snapshot['news_cutoff']):reason='outside_cutoff'
        if reason:
            omitted.append(dict(source_id=source['id'],material_id=mid,title=source.get('title'),reason=reason));continue
        entry={k:source.get(k) for k in ('name','title','url','published_at','data_as_of','text')}
        entry.update(source_id=source['id'],material_id=mid,
            text_truncated=bool(source.get('text_truncated')),must_include=mid in required)
        (product if materials[mid]['origin']=='product_reference' else news).append(entry)
        used.append(source)
    missing=required-{s['material_id'] for s in used}
    if missing:
        raise ValueError('必写或主新闻原文、日期尚未核实：'+'；'.join(materials[mid]['title'] for mid in sorted(missing)))
    brief=editorial_brief(snapshot,outline)
    brief['must_include']=[dict(id=m['id'],title=m['title']) for m in materials.values() if m['id'] in required]
    result=dict(format_version=FORMAT,news_cutoff=snapshot['news_cutoff'],
        product=dict(name=snapshot['product_name'],code=snapshot['product_code']),
        editorial_brief=brief,
        news_sources=news,product_sources=product,
        style_examples=deepcopy(snapshot.get('style_examples',[])),
        input_coverage=dict(omitted=omitted,style_omitted=[],truncated_sources=[s['source_id'] for s in news+product if s['text_truncated']]))
    # Reserve space for the complete first draft and an editing instruction. The
    # same resulting source set is used in both calls; no mid-task silent trim.
    capacity=snapshot['rules']['max_input_characters']-10000
    while len(dumps(result))>capacity and result['style_examples']:
        example=result['style_examples'].pop()
        result['input_coverage']['style_omitted'].append(dict(id=example['id'],reason='input_capacity_whole_example_omitted'))
    for source in list(reversed(result['news_sources']+result['product_sources'])):
        if len(dumps(result))<=capacity:break
        if source['must_include']:continue
        target=result['news_sources'] if source in result['news_sources'] else result['product_sources']
        target.remove(source)
        result['input_coverage']['omitted'].append(dict(source_id=source['source_id'],material_id=source['material_id'],
            title=source['title'],reason='input_capacity_whole_source_omitted'))
    if len(dumps(result))>capacity:
        raise ValueError('主新闻、必写材料与确认框架超过写作容量，未截断；请精简本期材料。')
    allowed={s['source_id'] for s in result['news_sources']+result['product_sources']}
    checked_snapshot=deepcopy(snapshot)
    checked_snapshot['sources']=[s for s in used if s['id'] in allowed]
    # Keep all material identities for required-item and main-event checks.
    return result,checked_snapshot


def render_article(raw, snapshot):
    from .writing import render_draft, risk_notice
    sources={s['id']:s for s in snapshot['sources']}
    paragraphs=[]
    for paragraph in raw.get('paragraphs',[]):
        refs=[]
        for sid in dict.fromkeys(paragraph['source_ids']):
            source=sources.get(sid)
            if source:
                refs.append(dict(source_id=sid,name=source['name'],title=source.get('title') or source['name'],
                    url=source.get('url'),published_at=source.get('published_at') or '日期待核',
                    data_period='；'.join(dict.fromkeys(e['data_period'] for e in raw.get('evidence',[])
                        if e['paragraph_index']==len(paragraphs)+1 and e['source_id']==sid and e.get('data_period')))))
        paragraphs.append(dict(paragraph,sources=refs))
    content=dict(raw,paragraphs=paragraphs,format_version=snapshot.get('workflow',FORMAT),news_cutoff=snapshot['news_cutoff'],
        body_characters=sum(len(p['text']) for p in paragraphs),risk_notice=risk_notice(snapshot))
    content['text']=render_draft(content,compact_sources=True)
    return content


def validate_edited(raw, snapshot, outline):
    """Adapt the separate evidence ledger to the existing fact and policy gates."""
    from .writing import _validate_shape, validate_draft
    _validate_shape(raw,NEWS_EDIT)
    if len(raw['evidence'])>80:raise ValueError('编辑证据条目过多')
    sources={s['id']:s for s in snapshot['sources']}
    allowed={mid for p in outline['paragraphs'] for mid in p['material_ids']}
    allowed.update(m['id'] for m in snapshot['materials'] if m['must_include'])
    if snapshot.get('selected_event_id'):allowed.add(snapshot['selected_event_id'])
    claims=[]; mapped={}; extra=[]
    for index,ev in enumerate(raw['evidence'],1):
        paragraph=ev['paragraph_index']; sid=ev['source_id']; source=sources.get(sid)
        if not 1<=paragraph<=len(raw['paragraphs']) or not source or source['material_id'] not in allowed:
            extra.append(issue('unknown_source','编辑证据引用了未确认的来源或段落',str(paragraph)));continue
        if sid not in raw['paragraphs'][paragraph-1]['source_ids']:
            extra.append(issue('source_mapping','证据来源与正文段落不一致',str(paragraph)));continue
        claim=dict(id='edit_'+str(index),material_id=source['material_id'],statement=ev['statement'],
            kind=ev['kind'],source_id=sid,quote=located_quote(ev['quote'],source),published_at=source.get('published_at') or '',
            publication_quote='',data_period=ev['data_period'],supported=ev['supported'],reason=ev['reason'])
        checks=qualify_claim(claim,sources,snapshot['news_cutoff'],snapshot['product_code'])
        if claim['quote']!=ev['quote']:claim['quote_layout_normalized']=True
        extra.extend(checks);claim.update(checks=checks,usable=not checks)
        claims.append(claim);mapped.setdefault(paragraph,[]).append(claim)
    adapted={key:deepcopy(raw[key]) for key in DRAFT_V64['properties'] if key!='paragraphs'}
    adapted['paragraphs']=[]
    for index,p in enumerate(raw['paragraphs'],1):
        evidence=mapped.get(index,[])
        if set(p['source_ids'])!={e['source_id'] for e in evidence}:
            extra.append(issue('source_mapping','段落列出的来源缺少对应证据',str(index)))
        adapted['paragraphs'].append(dict(text=p['text'],claim_ids=[e['id'] for e in evidence],
            material_ids=list(dict.fromkeys(e['material_id'] for e in evidence)),outline_paragraph_id='',outline_anchor=''))
    content,checks=validate_draft(adapted,snapshot,dict(outline,claims=claims),anchors_required=False)
    used={c['material_id'] for c in claims}
    if snapshot.get('selected_event_id') and snapshot['selected_event_id'] not in used:
        extra.append(issue('main_event','正文遗漏人工确认的主新闻'))
    checks['errors'].extend(extra);checks['factual']['errors'].extend(extra)
    checks['status']='needs_changes' if checks['errors'] else 'ready'
    # Failed citations must not erase or reorder the model's original paragraphs.
    content.update(render_article(raw,snapshot))
    content.update(format_version=FORMAT,raw_article=deepcopy(raw),evidence=claims,editing_changes=raw['changes'])
    return content,checks


def combined_usage(steps, control):
    usage=dict(host_tasks=len(steps),pipeline=FORMAT,steps=steps,
        total_elapsed_seconds=round(time.monotonic()-control.started,2))
    for key in ('input_tokens','output_tokens','cached_input_tokens','cost'):
        values=[step['usage'].get(key) for step in steps]
        usage[key]=sum(values) if values and all(isinstance(v,(int,float)) for v in values) else None
    if steps:
        for key in ('host','billing','api_key_used','model_requested','reasoning_effort_requested','cost_status'):
            usage[key]=steps[0]['usage'].get(key)
    return usage


def execute(conn, row, data, control, *, runner, progress=None, fetcher=None):
    """Persist the composing version before starting editing, even on failure."""
    from . import writing as w
    snapshot=data['snapshot']; outline=data['parent']; identifier=row['run_id']
    direct=data.get('workflow')=='direct-newsroom-v2'
    format_version='direct-newsroom-v2' if direct else FORMAT
    steps=[]; first_id=None; response=None
    pipeline=dict(format_version=format_version,status='preparing',steps=[])
    def persist():
        data['pipeline']=pipeline
        with conn:conn.execute('UPDATE fn_writing_runs SET input_json=? WHERE run_id=?',(w.dumps(data),identifier))
    def invoke(stage,payload,instructions,schema,maximum):
        nonlocal response
        control.check();response=None
        context=w.dumps(payload)
        capacity=snapshot['rules']['max_input_characters']
        if len(context)>capacity:raise ValueError('编辑输入超过写作容量，起草结果已保留；没有截断材料。')
        prompt=instructions+'\nINPUT_JSON:\n'+context
        timeout=control.host_timeout(maximum,snapshot['rules']['finalize_seconds'])
        step=dict(stage=stage,status='running',input=payload,instructions=instructions,timeout=timeout)
        pipeline['steps'].append(step);pipeline['status']=stage;persist()
        if progress:progress(dict(stage='writing_'+stage,message={'research':'正在补充相关新闻与产品资料','compose':'正在起草完整快讯','edit':'正在编辑正文并核对来源'}[stage]))
        start=time.monotonic()
        try:
            response=runner(data['host'],prompt,schema,web_search=stage=='research',timeout=timeout,control=control,
                progress=progress,input_character_limit=capacity+len(instructions)+1024,
                task_kind='research' if stage=='research' else 'writing',**data.get('host_runtime',{}))
            step.update(status='returned',raw_output=response.payload,trace=response.trace)
            usage=response.usage
            return response
        except BaseException as exc:
            step.update(status=getattr(exc,'code','failed'),trace=getattr(exc,'host_trace',None))
            usage=dict(input_tokens=None,output_tokens=None,cost=None,usage_status='unknown')
            raise
        finally:
            elapsed=time.monotonic()-start
            step.update(elapsed_seconds=round(elapsed,3),usage=usage)
            steps.append(dict(stage=stage,elapsed_seconds=elapsed,usage=usage))
            persist()
    try:
        if snapshot['is_fixture'] and runner is w.run_host:
            raise ValueError('合成测试不能调用真实写作宿主')
        if direct:
            from .writing_direct import prepare,build_input as build_direct
            prepare(snapshot,data,control,invoke,persist,progress,fetcher)
            base,checked_snapshot=build_direct(snapshot,data.get('feedback',''),data.get('previous_draft'))
        else:
            base,checked_snapshot=build_input(snapshot,outline)
        pipeline['input_coverage']=base['input_coverage'];persist()
        first=invoke('compose',dict(base,stage='compose'),data['instructions'],NEWS_ARTICLE,
            snapshot['rules']['compose_seconds'] if direct else min(snapshot['rules']['compose_seconds'],snapshot['rules']['host_seconds']))
        w._validate_shape(first.payload,NEWS_ARTICLE)
        first_content=render_article(first.payload,checked_snapshot)
        first_content['pipeline']=dict(format_version=format_version,status='editing_incomplete',input_coverage=base['input_coverage'])
        first_checks=dict(status='editing_incomplete',errors=[issue('editing_incomplete','已保存起草稿，编辑步骤尚未完成')],
            factual=dict(status='pending',errors=[]),policy=dict(status='pending',errors=[]),
            editorial=dict(version='v6.4',human_status='pending',model_review=None,actual_edit_minutes=None,target_edit_minutes=10,errors=[],notes=[]),
            human_acceptance='pending',adoption_ready=None,policy_version=snapshot['rules']['version'])
        with conn:
            saved=w._save_version(conn,checked_snapshot,'draft',first_content,first_checks,parent_id=data['parent_id'],run_id=identifier)
            first_id=saved['version_id']
            conn.execute('UPDATE fn_writing_runs SET version_id=? WHERE run_id=?',(first_id,identifier))
        pipeline['working_version_id']=first_id;persist()
        control.check()
        if first.payload['status']!='ready' or not first.payload['paragraphs']:
            raise ValueError('起草未完成：'+'；'.join(first.payload['issues'] or ['没有完整正文']))
        available=snapshot['rules']['edit_seconds'] if direct else snapshot['rules']['host_seconds']-sum(s['elapsed_seconds'] for s in steps)
        if available<=0:
            from .task_control import TaskStopped
            raise TaskStopped('timed_out')
        edited=invoke('edit',dict(base,stage='edit',composing_draft=first.payload),data['editing_instructions'],NEWS_EDIT,available)
        if direct:
            from .writing_direct import validate_edited as validate_direct
            content,checks=validate_direct(edited.payload,checked_snapshot)
        else:
            content,checks=validate_edited(edited.payload,checked_snapshot,outline)
        control.check()
        content['pipeline']=dict(format_version=format_version,status='complete',working_version_id=first_id,
            changes=edited.payload['changes'],input_coverage=base['input_coverage'])
        pipeline['status']='complete';persist()
        usage=combined_usage(steps,control)
        usage['pipeline']=format_version
        with conn:
            saved=w._save_version(conn,checked_snapshot,'draft',content,checks,parent_id=first_id,run_id=identifier)
            conn.execute("UPDATE fn_writing_runs SET status='completed',version_id=?,usage_json=?,finished_at=? WHERE run_id=?",
                (saved['version_id'],w.dumps(usage),utc_now(),identifier))
        return w.public_version(saved)
    except BaseException as exc:
        code=getattr(exc,'code',None) or 'failed'
        status=code if code in {'cancelled','timed_out','interrupted'} else 'failed'
        pipeline.update(status='editing_incomplete' if first_id else 'compose_incomplete',failure_code=code)
        data['failure_message']=('编辑未完成，起草稿已保留。' if first_id else '起草未完成。')+(
            str(exc)[:700] if isinstance(exc,ValueError) or getattr(exc,'code',None) else '请查看本次运行记录。')
        # Apply the same secret scrubbing as the legacy failure path.
        from pathlib import Path
        try:w.ed._reject_secrets(Path('writing-error.txt'),data['failure_message'])
        except ValueError:data['failure_message']='写作未完成，错误详情包含敏感字段，已隐藏。'
        persist()
        with conn:conn.execute('UPDATE fn_writing_runs SET status=?,error_code=?,usage_json=?,finished_at=? WHERE run_id=?',
            (status,code,w.dumps(combined_usage(steps,control)),utc_now(),identifier))
        raise
