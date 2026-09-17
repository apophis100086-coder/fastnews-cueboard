'use strict';
// Only the result region changes when a background exploration completes.
function productCluesHTML(clues,report){
 if(!clues?.runs?.length)return reportHTML(report,'product');
 const dates=item=>{
  const sources=item.sources||[];
  const published=[...new Set(sources.map(s=>s.first_published_at||s.published_at).filter(Boolean))].map(v=>'公开于 '+displayTime(v));
  const periods=[...new Set(sources.filter(s=>s.data_period_start||s.data_period_end).map(s=>'统计期 '+(s.data_period_start||'待核对')+' 至 '+(s.data_period_end||'待核对')))];
  return h([...(published.length?published:['公开时间待核实']),...periods].join(' · '));
 };
 const card=(item,index)=>`<article class="reading-item ${item.latest_increment?'clue-new':''}"><div class="reading-number">${String(index+1).padStart(2,'0')}</div><div>${item.latest_increment?`<span class="pill">${item.incremental_type==='new_evidence'?'新增依据':'本轮新增'}</span>`:''}<h4>${h(item.title)}</h4><p>${h(item.summary)}</p><p class="match-reason">产品关系：${h(item.why)}</p>${item.why_now?`<p class="clue-now">现在为何关注：${h(item.why_now)}</p>`:''}<p class="muted">${dates(item)}${item.date_state==='pending_time'?' · 截点可用性待核对':''}</p><div class="reading-links">${(item.sources||[]).map(s=>httpLink(s.url,s.source_name||s.title)+' <span class="muted">'+h(materialLabel(s))+'</span>').join(' · ')}</div><details class="clue-judgment"><summary>当前用途与有效性</summary><p>${h(({active:'截至本期仍有研究用途',superseded:'已有后续变化，请核对',uncertain:'有效性待确认'})[item.validity_state]||'旧报告尚未重新判断')}：${h(item.validity_reason||'请回查来源')}</p>${item.related_research_identity?'<p>此条关联已有事件的后续阶段，具体日期与来源见下方。</p>':''}</details>${evidenceDetails(item)}${reportPoolButton({report_id:item.report_id},item)}</div></article>`;
 const group=(key,title,cap)=>{const items=clues[key]||[];return `<section class="product-clue-group"><div class="section-head"><h3>${title} <small>${items.length} 条</small></h3><span class="muted">不凑数 · 人工选用</span></div>${items.slice(0,cap).map(card).join('')||'<p class="empty">本期暂未形成这一类线索，可继续探查补漏。</p>'}${items.length>cap?`<details data-extra-clues="${key}"><summary>展开其余 ${items.length-cap} 条${items.slice(cap).some(i=>i.latest_increment)?'（含本轮新增）':''}</summary>${items.slice(cap).map((item,i)=>card(item,i+cap)).join('')}</details>`:''}</section>`;};
 const runHTML=run=>`<p><strong>${run.mode==='explore'?'继续探查':'常规检索'}</strong> · ${h(displayTime(run.finished_at))} · ${run.count} 条 · ${run.usage?.total_elapsed_seconds??run.usage?.elapsed_seconds??'未知'} 秒 · 输入 ${run.usage?.input_tokens??'未知'} / 输出 ${run.usage?.output_tokens??'未知'} token</p>${searchAuditHTML(run.search_audit)}${(run.coverage||[]).map(s=>`<p>${h(s.name)} · ${h(({found:'找到相关材料',not_found:'未找到匹配材料',blocked:'来源受限',not_checked:'本轮未查完'})[s.status]||'待核对')}：${h(s.note)}</p>`).join('')}<ul>${(run.gaps||[]).map(g=>`<li>${h(g)}</li>`).join('')}</ul>`;
 const added=[...clues.recent,...clues.background,...(clues.pending||[])].filter(i=>i.latest_increment);
 const evidenceCount=added.filter(i=>i.incremental_type==='new_evidence').length;
 const last=clues.runs.at(-1),newCount=[...clues.recent,...clues.background,...(clues.pending||[])].filter(i=>i.latest_increment).length;
 return `<p class="muted">资讯截至 ${h(displayTime(detail.edition.news_cutoff))}。按当前价值归类，公开日期与统计期分别保留。</p>${last.mode==='explore'?`<div class="exploration-summary">本轮增量 ${newCount} 条（新线索 ${newCount-evidenceCount} · 新增依据 ${evidenceCount}）${newCount?' <button class="compact" data-show-new="true">查看本轮新增</button>':' · 未发现可保留的新增线索，原结果已保留'}</div>`:''}<div class="product-clue-columns">${group('recent','近期线索',clues.limits?.recent_items||10)}${group('background','产品背景',clues.limits?.background_items||5)}</div>${clues.pending?.length?`<details><summary>已有后续变化的材料（${clues.pending.length}）</summary>${clues.pending.map(card).join('')}</details>`:''}<details class="report-coverage"><summary>检索记录、用量与待核实事项（${clues.runs.length} 轮）</summary>${[...clues.runs].reverse().map(runHTML).join('')}</details>${report?.status==='failed'?'<p class="warning">最近一次任务未完成，以上保留已有结果。</p>':''}`;
}

async function refreshExplorationResults(job){
 const target=job.edition_id,profile=job.reader_profile||'default';
 if(editionId!==target||activeProfile!==profile)return;
 const updated=await api('edition?id='+encodeURIComponent(target));
 if(editionId!==target||activeProfile!==profile)return;
 detail.product_clues=updated.product_clues;
 detail.reading_report=updated.reading_report;
 renderReaderReports();
}

async function pollExploration(id,initial){
 explorationJob={job_id:id};setBusy(true);
 $('job').classList.remove('hidden','error');$('explore-stop').hidden=false;
 try{
  let job=initial;
  while(true){
   job=job||await api('job?id='+encodeURIComponent(id));
   explorationJob=job;
   const elapsed=job.started_at?Math.max(0,Math.round((Date.now()-Date.parse(job.started_at))/1000)):0;
   const text=`继续探查 · ${job.product_code||''} · ${job.status==='running'?`${job.stop_requested?'正在停止':'全网检索与判断中'} · ${elapsed} / 480 秒`:job.status==='completed'?'已完成':'已停止'} · 可继续选材、保存和导出`;
   $('job').textContent=text+(job.error?' · '+job.error:'');
   $('explore-progress').textContent=text;
   if(job.status!=='running'){
    await refreshExplorationResults(job);
    if(job.status==='completed')message(`继续探查已完成，保留 ${(job.result?.output?.items||[]).length} 条增量。${editionId!==job.edition_id?'结果保存在原期次。':'原有选材和草稿已保留。'}`);
    else message(job.error||'本轮探查已停止，原结果、选材和草稿已保留。');
    return;
   }
   await new Promise(resolve=>setTimeout(resolve,900));job=null;
  }
 }finally{explorationJob=null;$('explore-stop').hidden=true;setBusy(false);}
}

$('explore').onclick=safe(async()=>{
 if(busy)throw Error('已有检索任务，可继续选材；完成或停止后再探查。');
 requireCurrentEdition();
 const request={edition_id:editionId,reader_profile:activeProfile,research_question:$('explore-question').value.trim(),request_id:crypto.randomUUID()};
 explorationJob={job_id:null};setBusy(true);
 try{const result=await api('explore',request);await pollExploration(result.job_id);}
 finally{explorationJob=null;setBusy(false);}
});
$('explore-stop').onclick=safe(async()=>{
 if(!explorationJob?.job_id)return;
 await api('job-stop',{job_id:explorationJob.job_id});
 $('explore-progress').textContent='正在停止本轮探查，已有材料和草稿保留。';
});
document.addEventListener('click',event=>{
 const button=event.target.closest('button');
 if(button?.dataset.showNew){
  $('product-report').querySelectorAll('[data-extra-clues]').forEach(el=>el.open=true);
  $('product-report').querySelector('.clue-new')?.scrollIntoView({behavior:'smooth',block:'nearest'});
 }
});
