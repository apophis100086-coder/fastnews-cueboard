'use strict';
const noteKinds={product_turnover:'产品成交额',product_volume:'产品成交量',product_flow:'产品份额 / 资金增持',market_flow:'市场资金',highlight:'其他亮点',writing_note:'写作偏好'};
function activePool(){return (detail?.candidate_pool||[]).filter(c=>c.active);}
function requireCurrentEdition(){
 requireEdition();
 const s=currentSettings(),e=detail.edition;
 const markets=Object.entries(e.market_cutoffs).map(([m,v])=>`${m}=${v.date}@${v.source}`).sort();
 if(s.product_code!==e.product_code||s.edition_date!==e.edition_date||Date.parse(s.news_cutoff)!==Date.parse(e.news_cutoff)||JSON.stringify([...s.market_cutoffs].sort())!==JSON.stringify(markets))throw Error('产品或截点已改变，请先检索或建立当前期次，再整理此期材料。原选择与草稿仍保留。');
}
function searchWordKey(){return 'fastnews:search-words:'+activeProfile+':'+$('product').value;}
function renderProductPlan(resetWords=false){
 if(!$('product-plan'))return;
 const p=state?.search_plans?.[$('product').value];
 if(resetWords)$('search-keywords').value=localStorage.getItem(searchWordKey())||'';
 if(!p){$('product-plan').innerHTML='';return;}
 $('product-plan').innerHTML=`<div class="search-lanes"><div class="search-lane"><span class="lane-label">01 / 领域定向</span><strong>${p.selected_sources.length} 个检索入口</strong><p>${p.category_names.map(h).join('、')}。优先读取可用列表，再检索相关网站。</p></div><div class="search-lane"><span class="lane-label">02 / 关键词补漏</span><strong>围绕产品观察变量</strong><p>${p.keywords.slice(0,8).map(h).join('、')}${p.keywords.length>8?'等':''}。</p></div></div><details><summary>查看本产品的领域网址与查询计划</summary>${p.selected_sources.map(s=>`<p>${httpLink(s.url,s.name)} · 产品定点补搜${s.host_read_allowed?'，可尝试公开片段':'，仅搜索摘要'}<br>${h(s.fact_question||'按产品观察变量补漏')}<br><span class="muted">${h(s.access_note||'')}</span></p>`).join('')}${p.queries.map(q=>`<p class="muted">${q.lane==='sector_sites'?'站点':'关键词'}：${h(q.query)}</p>`).join('')}${(p.manual_sources||[]).length?`<p><strong>另有 ${(p.manual_sources||[]).length} 个需人工资料或待确认入口：</strong> ${(p.manual_sources||[]).map(s=>httpLink(s.url,s.name)).join('、')}</p><p class="muted">原站重要内容可在“选材与导出”中添加人工补充。</p>`:''}${p.gaps.map(g=>`<p class="warning">${h(g)}</p>`).join('')}</details>`;
 if(typeof renderVisuals==='function')renderVisuals();
}
function poolControls(e){return activePool().some(c=>c.event_id===e.event_id)?'<span class="pill">已在人工候选池</span>':`<button class="compact" data-pool-add="${h(e.event_id)}">加入人工候选池</button>`;}
function reportPoolButton(report,item){
 const c=activePool().find(c=>c.report_id===report.report_id&&c.item_id===item.item_id||item.research_identity&&c.research_identity===item.research_identity||item.local_event_id===c.event_id);
 if(c&&((item.material_fingerprints||[]).some(f=>!(c.material_fingerprints||[]).includes(f))||(item.claims||[]).some(n=>!(c.claims||[]).some(o=>o.claim_fingerprint===n.claim_fingerprint&&o.evidence_fingerprint===n.evidence_fingerprint))))return `<button class="compact" data-report-adopt="${h(item.item_id)}" data-report-id="${h(report.report_id)}">合并新增依据到原线索</button>`;
 return c?`<span class="pill">已加入候选池</span> <button class="compact" data-pool-remove="${h(c.event_id)}">移出候选池</button>`:`<button class="compact" data-report-adopt="${h(item.item_id)}" data-report-id="${h(report.report_id)}">加入人工候选池</button>`;
}
function renderPool(){
 if(!$('pool-list'))return;
 const pool=activePool();if($('nav-pool-count'))$('nav-pool-count').textContent=pool.length?String(pool.length):'';if($('materials-context'))$('materials-context').textContent=detail?.edition?detail.edition.product_name+' · 新闻截至 '+displayTime(detail.edition.news_cutoff):'先检索产品，再整理本次背景。';$('pool-count').textContent=pool.length+' 条 · 已选 '+(basket?.selected_clue_ids||[]).length+' 条';
 $('pool-list').innerHTML=pool.length?pool.map(c=>`<article class="pool-card"><div class="section-head"><h3>${h(c.title)}</h3><button class="compact" data-pool-remove="${h(c.event_id)}">移出候选池</button></div><p>${h(c.summary)}</p><p class="match-reason">${h(c.why)}</p><div class="reading-links">${c.sources.map(s=>`${httpLink(s.url,s.source_name||s.title||'来源')} <span class="muted">${h(displayTime(s.published_at))}</span>`).join(' · ')}</div><p class="muted">${c.origin==='ai_research'?'AI 研究摘要，原文待核实':c.origin==='listing_metadata'?'列表信息，原文待核实':'用户提供材料，未经独立核验'}</p>${typeof evidenceDetails==='function'?evidenceDetails(c):''}<div class="event-controls"><label class="check-label"><input type="checkbox" data-clue="${h(c.event_id)}" ${(basket?.selected_clue_ids||[]).includes(c.event_id)?'checked':''}>纳入本次快讯背景</label><button class="compact" data-main-pool="${h(c.event_id)}">${basket?.selected_event_id===c.event_id?'已设为主事件':'设为主事件（可选）'}</button></div></article>`).join(''):'<p class="empty">尚未加入线索。先在产品检索结果中加入感兴趣的条目，再在这里勾选本次交接要用的内容。</p>';
 const removed=(detail?.candidate_pool||[]).filter(c=>!c.active);
 if(typeof renderVisuals==='function')renderVisuals();
 if(removed.length)$('pool-list').innerHTML+=`<details><summary>已移除的线索（${removed.length} 条，可重新加入）</summary>${removed.map(c=>`<div class="basket-row"><span>${h(c.title)}</span><button class="compact" data-pool-add="${h(c.event_id)}">重新加入候选池</button></div>`).join('')}</details>`;
}
function renderBackgroundSelections(){
 if(!basket||!$('chosen-clues'))return;
 const pool=activePool();
 $('chosen-clues').innerHTML=(basket.selected_clue_ids||[]).map((id,i)=>`<div class="basket-row"><span>${i+1}. ${h(pool.find(c=>c.event_id===id)?.title||'线索已移出池中，请重读后核对')}</span><div class="right"><button class="move" data-clue-up="${h(id)}">↑ 上移</button><button class="move" data-clue-unselect="${h(id)}">取消本次选用</button></div></div>`).join('')||'<p class="muted">本次写作线索：尚未勾选</p>';
 const count=(basket.manual_notes||[]).filter(n=>n.selected).length;
 $('background-note-count').textContent='人工补充已选 '+count+' 条；保存后才纳入导出。';
 const saved=detail.decision;
 $('export').disabled=dirty||!(saved?.selected_event_id||saved?.selected_clue_ids?.length||saved?.manual_notes?.some(n=>n.selected));
 if(dirty){$('downloads').innerHTML='';$('handoff-preview-panel').open=false;$('handoff-preview').textContent='';}
 renderPool();
}
function renderManualNotes(){
 if(!$('manual-notes'))return;
 $('manual-notes').innerHTML=(basket?.manual_notes||[]).map(n=>`<article class="manual-note"><div class="section-head"><label class="check-label"><input type="checkbox" data-note-selected="${h(n.note_id)}" ${n.selected?'checked':''}>纳入本次背景</label><button class="compact" data-note-remove="${h(n.note_id)}">移除此补充</button></div><label>内容类型<select data-note-id="${h(n.note_id)}" data-note-field="kind">${Object.entries(noteKinds).map(([k,v])=>`<option value="${k}" ${n.kind===k?'selected':''}>${v}</option>`).join('')}</select></label><label>补充内容<textarea rows="3" maxlength="6000" data-note-id="${h(n.note_id)}" data-note-field="content" placeholder="例如当日成交量、资金增持、与同口径历史数据的比较，或希望强调的角度">${h(n.content)}</textarea></label><div class="note-fields"><label>来源或资料名称<input maxlength="300" data-note-id="${h(n.note_id)}" data-note-field="source_name" value="${h(n.source_name)}" placeholder="例如交易所数据、用户导出的资料"></label><label>出处链接（可选）<input type="url" data-note-id="${h(n.note_id)}" data-note-field="source_url" value="${h(n.source_url)}" placeholder="https://…"></label><label>数据市场（可选）<select aria-label="数据市场" data-note-id="${h(n.note_id)}" data-note-field="market"><option value="">未知 / 不适用</option>${[["CN","中国内地"],["HK","香港"],["US","美国"]].map(([k,v])=>`<option value="${k}" ${n.market===k?"selected":""}>${v}</option>`).join("")}</select></label><label>数据所属日期<input type="date" data-note-id="${h(n.note_id)}" data-note-field="data_date" value="${h(n.data_date)}"></label><label>首次公开日期 / 时刻<input data-note-id="${h(n.note_id)}" data-note-field="published_at" value="${h(n.published_at)}" placeholder="未知留空，勿用输入日期代替"></label></div><p class="muted">保留为用户补充，未知字段留空。产品资金/份额精确量值仅作内部背景；市场南向与产品资金分开标注。</p></article>`).join('');
}
function updateManualField(el){
 const id=el.dataset.noteId||el.dataset.noteSelected;
 const n=(basket?.manual_notes||[]).find(n=>n.note_id===id);if(!n)return;
 if(el.dataset.noteSelected)n.selected=el.checked;
 else n[el.dataset.noteField]=el.value;
 persistDraft();
}
async function removeFromPool(id){
 requireCurrentEdition();
 const r=await api('pool-remove',{edition_id:editionId,event_id:id,expected_revision:basket.expected_revision});
 basket.selected_clue_ids=(basket.selected_clue_ids||[]).filter(e=>e!==id);
 basket.selected_fact_ids=basket.selected_fact_ids.filter(fid=>!detail.facts.some(f=>f.fact_id===fid&&f.event_id===id));
 if(basket.selected_event_id===id)basket.selected_event_id=null;
 basket.expected_revision=r.decision_revision;
 // Keep all other unsaved edits. A failed request reaches none of these writes.
 persistDraft();await loadEdition(editionId);
 message('已移出候选池，并取消这条线索的本次选用。原资料和历史交接包保留，可从检索结果重新加入。');
}
async function workflowClick(b){
 if(b.dataset.sourceNote){const source=state.reader_sources.find(s=>s.source_id===b.dataset.sourceNote);if(!source)throw Error('来源已变化，请重读页面');addManualNote(source);if(typeof showWorkspace==='function')showWorkspace('materials');message('已带入来源名称与链接。填写重要内容，再自行勾选是否纳入背景。');}
 if(b.dataset.poolRemove)await removeFromPool(b.dataset.poolRemove);
 if(b.dataset.poolAdd){requireCurrentEdition();await api('pool-add',{edition_id:editionId,event_id:b.dataset.poolAdd});await loadEdition(editionId);message('已加入人工候选池。是否纳入背景由你勾选。');}
 if(b.dataset.mainPool){requireCurrentEdition();basket.selected_event_id=b.dataset.mainPool;basket.selected_clue_ids=[...new Set([...(basket.selected_clue_ids||[]),b.dataset.mainPool])];persistDraft();renderEvents();}
 if(b.dataset.clueUnselect){basket.selected_clue_ids=basket.selected_clue_ids.filter(id=>id!==b.dataset.clueUnselect);if(basket.selected_event_id===b.dataset.clueUnselect)basket.selected_event_id=null;persistDraft();renderEvents();}
 if(b.dataset.clueUp){const i=basket.selected_clue_ids.indexOf(b.dataset.clueUp);if(i>0)[basket.selected_clue_ids[i-1],basket.selected_clue_ids[i]]=[basket.selected_clue_ids[i],basket.selected_clue_ids[i-1]];persistDraft();}
 if(b.dataset.noteRemove){basket.manual_notes=basket.manual_notes.filter(n=>n.note_id!==b.dataset.noteRemove);persistDraft();renderManualNotes();}
}
function workflowChange(el){
 if(el.dataset.clue){basket.selected_clue_ids=el.checked?[...new Set([...(basket.selected_clue_ids||[]),el.dataset.clue])]:(basket.selected_clue_ids||[]).filter(id=>id!==el.dataset.clue);if(!el.checked&&basket.selected_event_id===el.dataset.clue)basket.selected_event_id=null;persistDraft();renderEvents();}
 if(el.dataset.noteSelected||el.dataset.noteField)updateManualField(el);
}
function addManualNote(source=null){
 requireCurrentEdition();basket.manual_notes=basket.manual_notes||[];
 if(basket.manual_notes.length>=30)throw Error('本次最多 30 条补充');
 basket.manual_notes.push({note_id:'note_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,8),kind:'highlight',content:'',source_name:source?.name||'',source_url:source?.url||'',data_date:'',published_at:'',selected:false});
 persistDraft();renderManualNotes();
}
$('add-note').onclick=safe(()=>addManualNote());
$('manual-notes').oninput=event=>{if(event.target.dataset.noteField)updateManualField(event.target);};
$('search-keywords').oninput=()=>localStorage.setItem(searchWordKey(),$('search-keywords').value);
function extraSearchKeywords(){return $('search-keywords').value.split(/[\n,，;；]+/).map(s=>s.trim()).filter(Boolean);}
if(typeof state!=='undefined'&&state){renderProductPlan(true);renderBackgroundSelections();renderManualNotes();}
