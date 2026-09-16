'use strict';
// Every visual count comes from the currently loaded local data, never a score.
let readingCategory='all';
function applyReadingCategory(){
 document.querySelectorAll('#morning-report [data-report-category]').forEach(section=>{
  section.hidden=readingCategory!=='all'&&section.dataset.reportCategory!==readingCategory;
 });
}
function renderVisuals(){
 if(!state||!$('overview-articles'))return;
 $('cutoff-readable').textContent=displayTime($('cutoff').value).replace(' 北京时间','');
 const sources=(state.reader_sources||[]).filter(s=>s.enabled);
 const automatic=sources.filter(s=>sourceMode(s)==='automatic');
 const manual=sources.filter(s=>sourceMode(s)!=='automatic');
 const ready=automatic.filter(s=>['ready','empty'].includes(s.health?.kind)).length;
 $('overview-articles').textContent=daily?String(daily.counts.items):'—';
 $('overview-ready').textContent=state.is_fixture?'演示':String(ready);
 const pool=typeof activePool==='function'?activePool().length:0;
 $('overview-pool').textContent=detail?String(pool):'—';
 $('product-pool-count').textContent=detail?'('+pool+')':'';
 $('source-coverage-visual').innerHTML=`<div class="coverage-ratio">${automatic.length}<small>/ ${sources.length} 个启用入口</small></div><progress class="coverage-track" max="${Math.max(1,sources.length)}" value="${automatic.length}" aria-hidden="true"></progress><div class="coverage-legend">自动采集 ${automatic.length} · 手动 / 检索入口 ${manual.length}</div>`;
 const items=daily?.reading_report?.output?.items||[];
 const groups=Object.entries(state.source_categories||{}).map(([id,name])=>({id,name,count:items.filter(i=>i.category===id).length})).filter(g=>g.count);
 if(!groups.some(g=>g.id===readingCategory))readingCategory='all';
 $('topic-caption').textContent=items.length?'分类总报共 '+items.length+' 条，点击分类筛选。':'生成分类总报后，显示各类线索的实际数量。';
 const max=Math.max(1,...groups.map(g=>g.count));
 $('topic-chart').innerHTML=groups.length?groups.map(g=>`<button class="topic-bar" data-reading-category="${h(g.id)}" aria-pressed="${readingCategory===g.id}"><span>${h(g.name)}<strong>${g.count}</strong></span><progress max="${max}" value="${g.count}" aria-hidden="true"></progress></button>`).join(''):'<p class="muted">尚无分类数据</p>';
 $('topic-chart').querySelectorAll('[data-reading-category]').forEach(button=>button.onclick=()=>{
  readingCategory=readingCategory===button.dataset.readingCategory?'all':button.dataset.readingCategory;
  renderVisuals();
 });
 $('clear-topic').hidden=readingCategory==='all';
 $('clear-topic').onclick=()=>{readingCategory='all';renderVisuals();};
 applyReadingCategory();
}
if(typeof state!=='undefined'&&state)renderVisuals();
