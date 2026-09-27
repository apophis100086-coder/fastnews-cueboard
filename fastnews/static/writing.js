'use strict';
let writingJob=null,writingEditionKey='',writingEditor={},writingShownId='';
function writingFailure(err){$('writing-error').textContent=err?.message||String(err);$('writing-error').hidden=false;}
function writingAction(action){return safe(async event=>{try{await action(event);}catch(err){writingFailure(err);throw err;}});}
function writingStorageKey(){return key('writing-editor');}
function writingVersions(){return detail?.writing?.versions||[];}
function writingVersion(id){return writingVersions().find(v=>v.version_id===id);}
function displayedOutline(){return writingVersion(writingShownId)||[...writingVersions()].reverse().find(v=>v.stage==='outline');}
function saveWritingEditor(){
 writingEditor={...writingEditor,text:$('writing-final').value,feedback:$('writing-feedback').value,question:$('writing-question').value,supplement_latest:$('writing-auto-research').checked};
 if(!writingEditor.outline_id&&displayedOutline())writingEditor.outline_id=displayedOutline().version_id;
 localStorage.setItem(writingStorageKey(),JSON.stringify(writingEditor));updateWritingButtons();
}
function restoreWritingEditor(){
 const storage=writingStorageKey();if(storage===writingEditionKey)return;
 writingEditionKey=storage;writingShownId='';
 try{writingEditor=JSON.parse(localStorage.getItem(storage)||'{}');}catch{writingEditor={};}
 $('writing-final').value=writingEditor.text||'';$('writing-feedback').value=writingEditor.feedback||'';$('writing-question').value=writingEditor.question||'';
 $('writing-auto-research').checked=writingEditor.supplement_latest!==false;
 $('writing-error').hidden=true;$('writing-error').textContent='';$('writing-progress').textContent='';
}
function updateWritingButtons(){
 if(!$('writing-outline'))return;
 const matching=detail?.edition?.product_code===$('product').value;
 const saved=!!detail?.decision&&!dirty&&matching;
 $('writing-outline').disabled=busy||!saved;
 $('writing-revise').disabled=busy||!saved||!displayedOutline();
 const base=writingVersion(writingEditor.outline_id)||displayedOutline();
 $('writing-generate').disabled=busy||!saved||!base||base.stale_selection||!!base.checks.errors.length||!$('writing-final').value.trim();
 $('writing-supplement').disabled=busy||!displayedOutline()||!$('writing-question').value.trim();
 $('writing-state').textContent=!detail?.edition?'先建立本期并选材。':!matching?'请先载入所选产品的期次，当前显示的稿件仍属于原产品。':dirty?'选材尚未保存；保存后可生成框架。':
  base?.stale_selection?'编辑框对应旧选材，请按当前材料更新框架。':base?.checks.errors.length?'关键依据或规则问题待处理，可补料或调整框架。':
  '框架经你修改确认后，才生成正文。';
 $('writing-editor-version').textContent=base?'编辑框关联框架 '+base.version_id.slice(-8)+' · 选材版本 '+base.decision_revision:'尚未生成框架';
 const web=state?.reader_profile?.settings?.web_search!==false;
 $('writing-auto-research').disabled=busy||!web;
 $('writing-research-note').textContent=web?'补查范围：本期截点 '+displayTime(detail?.edition?.news_cutoff)+' 之前近7个自然日。新增资料列在框架中，由你确认后进入正文。历史期次仍按原截点补查。':'当前模型方案已关闭联网搜索，本轮只核对已有材料。可在来源设置中开启联网。';
 const budget=detail?.writing?.task_budget;
 $('writing-budget').textContent=budget?(budget.limit===null?`今日已运行 ${budget.used} 次模型任务（含失败尝试）；未设置每日次数上限。账号可用额度由模型服务提供方管理。`:`项目今日模型任务：已用 ${budget.used} 次 / 上限 ${budget.limit} 次，剩余 ${budget.remaining} 次（含失败尝试）；${displayTime(budget.resets_at)} 恢复。这是本地限制，不代表账号剩余额度。`):'';
}
function supplementalHTML(outline){
 const research=outline?.supplemental_research;if(!research||research.enabled===undefined)return '';
 if(research.enabled===false)return '<p class="muted">这版框架未启用额外联网补搜。</p>';
 const materials=outline.supplemental_materials||[];
 return '<section class="writing-advice"><h3>本轮补查与新增资料</h3><p>'+h(research.summary||'补搜已返回，原文核对情况见下方。')+'</p><p class="muted">以下是框架建议补充，未改动人工选材。确认最终框架时一并确认；不采用的内容可在反馈中说明。</p>'+materials.map(m=>'<article><strong>'+h(m.title)+'</strong><p>'+h(m.relation)+'</p>'+(outline.source_evidence||[]).filter(s=>s.material_id===m.id).map(s=>'<p>'+h(s.name)+' · '+(httpLink(s.url,s.title||m.title)||h(s.title||m.title))+' · '+h(displayTime(s.published_at))+'<br>'+h(s.text_observed?'已取得可核对原文片段':'未取得可核对原文')+(s.error?' · '+h(s.error):'')+'</p>').join('')+'</article>').join('')+'</section>';
}
function writingChecksHTML(checks){return (checks?.errors||[]).map(e=>`<li><strong>${h(e.message)}</strong>${e.location?' · '+h(e.location):''}${e.excerpt?'<br>'+h(e.excerpt):''}</li>`).join('');}
function writingQualityHTML(checks){
 if(!checks?.editorial||checks.editorial.version!=='v6.4')return '<p class="muted">旧稿尚无 V6.4 编辑质量记录。</p>';
 const e=checks.editorial;
 return '<details><summary>事实、口径与编辑质量检查</summary><p>事实检查：'+(checks.factual?.status==='pending'?'待编辑后核对':checks.factual?.errors?.length?'有待处理项':'对应程序检查通过')+'；口径检查：'+(checks.policy?.status==='pending'?'待编辑后核对':checks.policy?.errors?.length?'有待处理项':'对应程序检查通过')+'</p><p>编辑质量：'+h(e.human_status==='accepted'?'已记录人工验收':'待人工验收')+'；目标修改时间10分钟内。程序与模型检查不代表可直接采用。</p><ul>'+Object.entries(e.model_review||{}).filter(([k,v])=>typeof v==='string').map(([k,v])=>'<li>'+h(v)+'</li>').join('')+'</ul></details>';
}
function writingDraftHTML(content){
 return `<article class="writing-prose news-draft"><h3>${h(content.title)}</h3>${content.paragraphs.map((p,i)=>`<p>${h(p.text)}</p><details class="paragraph-sources"><summary>第${i+1}段资料／数据来源</summary>${(p.sources||[]).map(s=>`<p>${h(s.name)} · ${httpLink(s.url,s.title||s.name)||h(s.title||s.name)}<br>公开日期：${h(s.published_at)}${s.data_period?' · 统计期：'+h(s.data_period):''}</p>`).join('')}</details>`).join('')}</article>`;
}
function writingEditingHTML(content){
 const pipeline=content.pipeline;if(!pipeline)return '';
 if(pipeline.status!=='complete')return '<p class="writing-gaps">起草稿已保存，编辑步骤尚未完成；这版不能作为已完成的交付稿。</p>';
 const original=writingVersion(pipeline.working_version_id);
 const coverage=pipeline.input_coverage||{};
 const missing=[...(coverage.omitted||[]).map(x=>(x.title||x.source_id)+'：'+x.reason),...(coverage.style_omitted||[]).map(x=>'范文 '+x.id+'：未完整送入')];
 return '<details><summary>查看后台编辑修改与起草原稿</summary><ul>'+(pipeline.changes||[]).map(x=>'<li>'+h(x)+'</li>').join('')+'</ul>'+(original?'<pre class="writing-prose">'+h(original.content.text)+'</pre>':'<p>起草版本暂未加载。</p>')+'</details>'+((missing.length||(coverage.truncated_sources||[]).length)?'<details><summary>本次材料覆盖说明</summary><ul>'+missing.map(x=>'<li>'+h(x)+'</li>').join('')+'</ul><p>已注明摘录省略的来源：'+h((coverage.truncated_sources||[]).join('、')||'无')+'</p></details>':'');
}
function renderWriting(){
 if(!$('writing-panel'))return;
 restoreWritingEditor();
 const versions=writingVersions(),outline=displayedOutline();
 $('writing-outline-result').innerHTML=outline?`<div class="section-head"><h3>建议框架</h3><button data-use-outline="${h(outline.version_id)}">放入最终框架编辑区</button></div><p class="muted">选材版本 ${outline.decision_revision} · ${h(displayTime(outline.created_at))}${outline.stale_selection?' · 对应旧选材':''}</p><pre class="writing-prose">${h(outline.content.text)}</pre><div class="writing-advice"><strong>表达建议</strong><ul>${outline.content.suggestions.map(t=>`<li>${h(t)}</li>`).join('')}</ul>${outline.content.changes.length?'<strong>本轮修改</strong><ul>'+outline.content.changes.map(t=>`<li>${h(t)}</li>`).join('')+'</ul>':''}</div>${outline.checks.errors.length?'<div class="writing-gaps"><strong>成稿前需要处理</strong><ul>'+writingChecksHTML(outline.checks)+'</ul></div>':'<p class="writing-ready">框架依据检查通过，可修改并确认。</p>'}<details><summary>选材安排与证据核对</summary><ul>${outline.content.dispositions.map(d=>`<li>${h(d.role)}：${h(d.reason)}</li>`).join('')}</ul>${outline.content.claims.map(c=>`<article class="writing-evidence"><strong>${h(c.statement)}</strong><p>${c.usable?'有可比对依据':'待核实'} · ${h(c.kind)} · ${h(c.published_at||'日期待核')}</p><blockquote>${h(c.quote)}</blockquote>${c.checks.map(e=>`<p class="warning">${h(e.message)}</p>`).join('')}</article>`).join('')}${outline.source_evidence.map(s=>`<p>${httpLink(s.url,s.name)||h(s.name)} · ${s.text_observed?'取得可核对片段':'未取得可核对原文'}${s.error?' · '+h(s.error):''}${s.text_truncated?' · 片段按预算截取':''}</p>`).join('')}</details>`:'<p class="empty">保存本次选材后，先生成框架。这里会说明重点如何安排、哪些依据还需补充。</p>';
 if(outline?.content.gaps?.length)$('writing-outline-result').innerHTML+='<details><summary>可选补料与表述边界</summary><ul>'+outline.content.gaps.map(g=>'<li>'+h(g)+'</li>').join('')+'</ul></details>';
 if(outline?.stale_skill_version)$('writing-outline-result').innerHTML='<p class="writing-gaps">这是旧版规则生成的框架。请按当前 V6.4 与补搜设置重新生成，旧内容仍保留供对照。</p>'+$('writing-outline-result').innerHTML;
 $('writing-outline-result').innerHTML+=supplementalHTML(outline);
 $('writing-query-suggestions').innerHTML=(outline?.content.supplement_queries||[]).map((q,i)=>`<button class="compact" data-writing-query="${i}">${h(q)}</button>`).join('');
 const draft=[...versions].reverse().find(v=>v.stage==='draft');
 const framePanel=$('writing-framework-panel');
 if(!writingEditor.last_rendered_draft&&draft&&!document.activeElement?.closest?.('#writing-framework-panel'))framePanel.open=false;
 if(!draft)framePanel.open=true;
 writingEditor.last_rendered_draft=draft?.version_id||null;
 $('writing-draft-result').innerHTML=draft?`<div class="section-head"><div><h3>快讯初稿</h3><p class="muted">正文 ${draft.content.body_characters} 字 · 选材版本 ${draft.decision_revision}${draft.stale_selection?' · 对应旧选材':''} · ${h(displayTime(draft.created_at))}</p></div>${draft.stale_selection?'<span class="pill">对应旧选材，请更新框架后生成初稿</span>':draft.stale_skill_version?'<span class="pill">旧版技能生成，重新确认框架后可生成新版</span>':draft.checks.errors.length?'<span class="pill">需要修改</span>':'<span class="writing-ready">程序检查通过 · 待编辑验收</span>'}</div>${draft.checks.errors.length?'<ul class="writing-gaps">'+writingChecksHTML(draft.checks)+'</ul>':''}${writingQualityHTML(draft.checks)}${writingEditingHTML(draft.content)}${writingDraftHTML(draft.content)}<div class="writing-actions"><button class="primary" data-copy-draft="${h(draft.version_id)}" ${draft.checks.errors.length||draft.stale_skill_version||draft.stale_selection?'disabled':''}>复制标题、正文与来源</button><button data-download-draft="${h(draft.version_id)}" ${draft.checks.errors.length||draft.stale_skill_version||draft.stale_selection?'disabled':''}>下载 MD / JSON</button><button data-recheck-draft="${h(draft.version_id)}" ${draft.stale_selection||draft.stale_skill_version||(draft.content.pipeline&&draft.content.pipeline.status!=='complete')?'disabled':''}>重新检查这版初稿</button><button data-return-outline="true">返回框架调整</button></div><details><summary>风险提示（单列）</summary><p>${h(draft.content.risk_notice.text)}</p><p class="muted">${h(draft.content.risk_notice.missing.join('；'))}</p>${httpLink(draft.content.risk_notice.source_url,'模板参照来源')} <button data-copy-risk="${h(draft.version_id)}">复制风险提示</button></details>`:'<p class="empty">确认最终框架后，初稿将在这里显示。正文逐段附来源，风险提示单列。</p>';
 $('writing-history').innerHTML=[...versions].reverse().map(v=>`<p><strong>${h(v.content?.pipeline?.status==='editing_incomplete'?'起草稿（编辑未完成）':v.content?.pipeline?.status==='complete'?'编辑稿':({outline:'框架',confirmed_outline:'用户确认框架',draft:'初稿'})[v.stage])}</strong> · ${h(displayTime(v.created_at))} · 选材版本 ${v.decision_revision}${v.stage==='outline'?` <button class="compact" data-view-outline="${h(v.version_id)}">查看这版框架</button>`:` <button class="compact" data-view-writing="${h(v.version_id)}">查看内容</button>`}</p>`).join('')||'<p class="muted">尚无写作版本。</p>';
 $('writing-usage').innerHTML=(detail?.writing?.runs||[]).slice().reverse().map(r=>`<p>${r.stage==='outline'?'框架':'正文'} · ${h(r.status)} · ${r.usage?.total_elapsed_seconds??'未知'} 秒 · 输入 ${r.usage?.input_tokens??'未知'} / 输出 ${r.usage?.output_tokens??'未知'} token${r.error||r.error_code?' · '+h(r.error||r.error_code):''}</p>`).join('');
 const lastRun=detail?.writing?.runs?.at(-1);
 if(!writingJob&&lastRun&&['failed','timed_out','cancelled','interrupted'].includes(lastRun.status))writingFailure(lastRun.error||'上次写作未完成（'+lastRun.status+'），已有内容保留，可重新发起。');
 updateWritingButtons();
}
async function refreshWritingResults(job){
 const eid=job.edition_id,pid=job.reader_profile||'default';
 if(editionId!==eid||activeProfile!==pid)return;
 const current=await api('edition?id='+encodeURIComponent(eid));
 if(editionId!==eid||activeProfile!==pid)return;
 detail.writing=current.writing;
 if(job.stage==='outline'&&job.result?.version_id)writingShownId=job.result.version_id;
 renderWriting();
}
async function pollWriting(id,initial){
 writingJob={job_id:id};setBusy(true);$('writing-stop').hidden=false;
 try{
  let job=initial;
  while(true){
   job=job||await api('job?id='+encodeURIComponent(id));writingJob=job;
   const elapsed=job.started_at?Math.max(0,Math.round((Date.now()-Date.parse(job.started_at))/1000)):0;
   const stage=job.stage==='outline'?'框架与依据核对':'快讯正文';
   const last=job.progress?.at(-1)?.message||'';
   const text=`${stage} · ${job.product_code||''} · ${job.status==='running'?(job.stop_requested?'正在停止':last||'处理中')+' · '+elapsed+' / 480 秒':job.status==='completed'?'完成':'已停止'}`;
   $('writing-progress').textContent=text;$('job').classList.remove('hidden');$('job').textContent=text;
   if(job.status!=='running'){
    await refreshWritingResults(job);
    if(job.status!=='completed'){$('writing-progress').textContent=text+'：'+(job.error||'未完成，原有内容已保留');writingFailure(job.error||'本次写作未完成，原有内容已保留。');}
    message(job.status==='completed'?'写作结果已保存。请查看框架、正文或待补依据。':job.error||'本次写作未完成，原有内容已保留。');
    return;
   }
   await new Promise(resolve=>setTimeout(resolve,900));job=null;
  }
 }finally{writingJob=null;$('writing-stop').hidden=true;setBusy(false);updateWritingButtons();}
}
async function startWriting(stage,extra={}){
 requireCurrentEdition();if(dirty)throw Error('请先保存当前选材。');if(busy)throw Error('已有后台任务，可继续编辑，完成后再生成。');
 const eid=editionId,pid=activeProfile;
 $('writing-error').hidden=true;$('writing-error').textContent='';
 $('writing-progress').textContent='正在提交任务；受理后显示实际进度。';
 writingJob={job_id:null};setBusy(true);
 let submitted=false;
 try{
  const response=await api('writing-'+stage,{edition_id:eid,reader_profile:pid,decision_revision:detail.decision.decision_revision,request_id:crypto.randomUUID(),...(stage==='outline'?{supplement_latest:$('writing-auto-research').checked}:{}),...extra});
  submitted=true;
  await pollWriting(response.job_id);
 }catch(err){$('writing-progress').textContent=submitted?'任务状态读取中断，请刷新查看运行记录。':'未能启动任务，原因见上方提示。';writingFailure(err);throw err;}
 finally{writingJob=null;setBusy(false);updateWritingButtons();}
}
$('writing-outline').onclick=writingAction(()=>startWriting('outline'));
$('writing-revise').onclick=writingAction(()=>startWriting('outline',{parent_version_id:displayedOutline().version_id,feedback:$('writing-feedback').value}));
$('writing-auto-research').onchange=saveWritingEditor;
$('writing-final').oninput=saveWritingEditor;$('writing-feedback').oninput=saveWritingEditor;$('writing-question').oninput=saveWritingEditor;
$('writing-generate').onclick=writingAction(async()=>{
 requireCurrentEdition();if(busy||dirty)throw Error('请先保存选材，等待当前任务结束。');
 const eid=editionId,pid=activeProfile,revision=detail.decision.decision_revision;
 const base=writingVersion(writingEditor.outline_id)||displayedOutline();
 const confirmed=await api('writing-confirm',{edition_id:eid,decision_revision:revision,outline_version_id:base.version_id,text:$('writing-final').value,request_id:crypto.randomUUID()});
 if(editionId!==eid||activeProfile!==pid||dirty)throw Error('最终框架已保存；页面或选材已变化，请返回原期次生成。');
 detail.writing.versions.push(confirmed);
 await startWriting('draft',{confirmed_version_id:confirmed.version_id});
});
$('writing-stop').onclick=safe(async()=>{if(writingJob?.job_id)await api('job-stop',{job_id:writingJob.job_id});});
$('writing-supplement').onclick=safe(async()=>{
 requireCurrentEdition();if(busy)throw Error('请等待当前任务完成。');
 const result=await api('explore',{edition_id:editionId,reader_profile:activeProfile,research_question:$('writing-question').value.trim(),request_id:crypto.randomUUID(),writing_context:{outline_version_id:displayedOutline().version_id}});
 await pollExploration(result.job_id);
 message('补搜结果已追加在产品线索中；请自行采用后保存，再更新框架。');
});
function downloadWritingFile(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.addEventListener('click',safe(async event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.useOutline){const v=writingVersion(b.dataset.useOutline);writingEditor.outline_id=v.version_id;$('writing-final').value=v.content.text;saveWritingEditor();$('writing-final').focus();}
 if(b.dataset.viewOutline){writingShownId=b.dataset.viewOutline;renderWriting();}
 if(b.dataset.viewWriting){const v=writingVersion(b.dataset.viewWriting);$('writing-history-content').textContent=v.content.confirmed_text||v.content.text||'';}
 if(b.dataset.writingQuery!==undefined){$('writing-question').value=displayedOutline().content.supplement_queries[Number(b.dataset.writingQuery)];saveWritingEditor();}
 if(b.dataset.returnOutline){$('writing-framework-panel').open=true;$('writing-final').focus();$('writing-final').scrollIntoView({behavior:'smooth',block:'center'});}
 if(b.dataset.copyDraft||b.dataset.downloadDraft){const id=b.dataset.copyDraft||b.dataset.downloadDraft;const pack=await api('writing-export',{version_id:id});
  if(b.dataset.copyDraft){await navigator.clipboard.writeText(pack.markdown);message('已复制标题、正文和逐段来源。风险提示可单独复制。');}
  else{downloadWritingFile(id+'.md',pack.markdown,'text/markdown;charset=utf-8');downloadWritingFile(id+'.json',JSON.stringify(pack,null,2),'application/json');}}
 if(b.dataset.recheckDraft){const eid=editionId;const reviewed=await api('writing-recheck',{version_id:b.dataset.recheckDraft,request_id:crypto.randomUUID()});if(eid===editionId){detail.writing.versions.push(reviewed);renderWriting();}message('程序复查已保存为新版本；沿用原语义检查，不消耗模型任务。');}
 if(b.dataset.copyRisk){await navigator.clipboard.writeText(writingVersion(b.dataset.copyRisk).content.risk_notice.text);message('已复制风险提示；产品专属正式备注仍需补齐。');}
}));
if(typeof detail!=='undefined'&&detail)renderWriting();
