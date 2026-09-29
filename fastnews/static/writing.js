'use strict';
let writingJob=null,writingEditionKey='',writingEditor={},writingShownId='';
function writingFailure(err){$('writing-error').textContent=err?.message||String(err);$('writing-error').hidden=false;}
function writingAction(action){return safe(async event=>{try{await action(event);}catch(err){writingFailure(err);throw err;}});}
function writingStorageKey(){return key('direct-writing-editor');}
function writingVersions(){return detail?.writing?.versions||[];}
function writingVersion(id){return writingVersions().find(v=>v.version_id===id);}
function latestDraft(){return [...writingVersions()].reverse().find(v=>v.stage==='draft');}
function saveWritingEditor(){writingEditor.feedback=$('writing-feedback').value;localStorage.setItem(writingStorageKey(),JSON.stringify(writingEditor));}
function restoreWritingEditor(){
 const storage=writingStorageKey();if(storage===writingEditionKey)return;writingEditionKey=storage;
 try{writingEditor=JSON.parse(localStorage.getItem(storage)||'{}');}catch{writingEditor={};}
 $('writing-feedback').value=writingEditor.feedback||'';$('writing-error').hidden=true;$('writing-progress').textContent='';
}
function updateWritingButtons(){
 if(!$('writing-generate'))return;
 const matching=detail?.edition?.product_code===$('product').value;
 const count=(basket?.selected_clue_ids?.length||0)+(basket?.selected_fact_ids?.length||0)+(basket?.manual_notes||[]).filter(n=>n.selected&&n.kind!=='writing_note').length;
 $('writing-generate').disabled=busy||!matching||!count;
 $('writing-revise').disabled=busy||!matching||!latestDraft()||!count;
 $('writing-state').textContent=!detail?.edition?'先从产品检索中加入候选材料。':!matching?'当前稿件属于原产品，请载入所选产品的期次。':count?`已选 ${count} 条材料，点击生成时自动保存。`:'勾选左侧材料后，即可直接生成。';
 $('writing-research-note').textContent=detail?.edition?`资讯截至 ${displayTime(detail.edition.news_cutoff)}。${state?.reader_profile?.settings?.web_search===false?'当前方案关闭联网。':'将补查近7日相关消息，并核对产品资料。'} 最长15分钟，可随时停止。`:'';
}
function writingChecksHTML(checks){return (checks?.errors||[]).map(e=>'<li>'+h(e.message)+(e.location?' · '+h(e.location):'')+'</li>').join('');}
function writingSourceStatus(source){
 const read=source.text_observed?(source.text_truncated?'已读部分原文（内容截断）':'已读原文'):'原文未读';
 return read+(source.date_observed?' · 日期已核对':' · 日期待核对');
}
function writingOmission(reason){return ({source_restricted:'来源访问受限',original_not_read:'未取得原文',publication_not_verified:'公开日期待核对',outside_cutoff:'超过本期截点',input_capacity_whole_source_omitted:'输入容量不足，整篇未纳入'})[reason]||reason;}
function writingDraftHTML(content){
 const text=content.text||content.manual_text||'';
 return '<article class="writing-prose news-draft">'+text.split(/\n\s*\n/).map((p,i)=>i===0?'<h3>'+h(p)+'</h3>':'<p class="'+(/^（.*来源/.test(p)?'draft-source':'')+'">'+h(p)+'</p>').join('')+'</article>';
}
function renderWriting(){
 restoreWritingEditor();const versions=writingVersions(),draft=latestDraft();
 if(draft){
  const c=draft.content,checks=draft.checks||{},complete=c.pipeline?.status==='complete',legacy=draft.workflow&&!['direct-newsroom-v2','direct-newsroom-v3'].includes(draft.workflow);
  const status=c.human_edited?'人工修改已保存，新增文字待核对':legacy?'历史稿件，按原流程和口径保存':c.pipeline?.status==='verification_incomplete'?'编辑稿已保留，核验未完成':!complete?'起草稿已保留，编辑未完成':(checks.errors?.length||c.appendix_missing?.length||c.company_template?.issues?.length)?'初稿已保存，有待核对项':'编辑稿已保存，等待你审阅';
  $('writing-draft-result').innerHTML='<div class="section-head"><h3>本期初稿</h3><span class="pill">'+h(status)+'</span></div>'+
   '<div class="writing-actions"><button class="primary" data-copy-draft="'+h(draft.version_id)+'">复制完整稿件</button>'+(!legacy?'<button data-word-draft="'+h(draft.version_id)+'">下载 Word</button>':'')+'</div>'+writingDraftHTML(c)+
   (c.company_template?.issues?.length?'<p class="muted">公司模板待补：'+h(c.company_template.issues.join('；'))+'</p>':'')+
   (c.appendix_missing?.length?'<p class="muted">产品备注待补：'+h(c.appendix_missing.join('；'))+'</p>':'')+
   '<details><summary>在页面中修改稿件</summary><p class="muted">编辑内容自动保存在本机。点击保存后成为独立版本，保留原稿。</p><textarea id="writing-local-text" rows="18" maxlength="50000"></textarea><button data-save-manual="'+h(draft.version_id)+'">保存人工修改</button></details>'+
   '<details><summary>查看依据与检查（'+(checks.errors?.length||0)+'项待核对）</summary><ul>'+writingChecksHTML(checks)+'</ul><p class="muted">程序检查与稿件采用分别记录。实际采用和修改时间由编辑确认。</p>'+(draft.source_evidence||[]).map(s=>'<p>'+h(s.name)+' · '+(httpLink(s.url,s.title||'原文')||h(s.title))+' · '+h(displayTime(s.published_at))+'<br><small>'+h(writingSourceStatus(s))+(s.error?' · '+h(s.error):'')+'</small></p>').join('')+'<button data-trace-draft="'+h(draft.version_id)+'">下载追溯记录</button></details>'+
   '<details><summary>本轮补搜与实际编辑</summary><p>'+h(draft.supplemental_research?.summary||'沿用已核对材料')+'</p><ul>'+(c.editing_changes||c.pipeline?.changes||[]).map(x=>'<li>'+h(x)+'</li>').join('')+'</ul><p class="muted">'+h((c.pipeline?.input_coverage?.omitted||[]).map(x=>(x.title||x.source_id)+'：'+writingOmission(x.reason)).join('；'))+'</p></details>';
  const editor=$('writing-local-text');editor.value=localStorage.getItem(key('draft-edit:'+draft.version_id))??c.manual_text??c.text??'';
  const storage=key('draft-edit:'+draft.version_id);editor.oninput=()=>localStorage.setItem(storage,editor.value);
 }else $('writing-draft-result').innerHTML='<div class="draft-empty"><h3>选好材料，开始写作</h3><p>后台将依次补充资料、起草、编辑。完成后可在这里审阅、修改和下载。</p></div>';
 $('writing-history').innerHTML=[...versions].reverse().map(v=>'<p>'+h(v.content.human_edited?'人工修改':v.stage==='draft'?(v.content.pipeline?.status==='editing_incomplete'?'起草稿':'编辑稿'):'旧版框架')+' · '+h(displayTime(v.created_at))+' <button class="compact" data-view-writing="'+h(v.version_id)+'">查看</button></p>').join('')||'<p class="muted">尚无历史版本。</p>';
 $('writing-usage').innerHTML=(detail?.writing?.runs||[]).slice().reverse().map(r=>'<p>'+h(r.status)+' · '+(r.usage?.total_elapsed_seconds??'—')+'秒'+(r.error?' · '+h(r.error):'')+'</p>').join('');
 const lastRun=detail?.writing?.runs?.at(-1);
 if(!writingJob&&lastRun&&['failed','timed_out','cancelled','interrupted'].includes(lastRun.status))writingFailure(lastRun.error||'上次任务未完成。已有稿件与选材保留，可重新生成。');
 updateWritingButtons();
}
async function refreshWritingResults(job){
 const eid=job.edition_id,pid=job.reader_profile||'default';if(editionId!==eid||activeProfile!==pid)return;
 const current=await api('edition?id='+encodeURIComponent(eid));if(editionId!==eid||activeProfile!==pid)return;
 detail.writing=current.writing;renderWriting();
}
async function pollWriting(id,initial){
 writingJob={job_id:id};setBusy(true);$('writing-stop').hidden=false;
 try{let job=initial;while(true){
  job=job||await api('job?id='+encodeURIComponent(id));writingJob=job;
  const elapsed=job.started_at?Math.max(0,Math.round((Date.now()-Date.parse(job.started_at))/1000)):0;
  const total=job.deadline_at&&job.started_at?Math.max(1,Math.round((Date.parse(job.deadline_at)-Date.parse(job.started_at))/1000)):900;
  const s=[...(job.progress||[])].reverse().find(p=>/^writing_(audit|edit|compose|research|verify)/.test(p.stage||''))?.stage||'',stage=s.includes('audit')?'核验':s.includes('edit')?'编辑':s.includes('compose')?'起草':'补充资料';
  const text=job.status==='running'?`${job.stop_requested?'正在停止':stage} · 已用 ${Math.floor(elapsed/60)}分${elapsed%60}秒 / 最长${Math.ceil(total/60)}分钟`:(job.status==='completed'?'完成，稿件已保存':'本轮已结束，已有内容已保留');
  $('writing-progress').textContent=text;$('job').classList.remove('hidden');$('job').textContent=text;
  if(job.status!=='running'){
   await refreshWritingResults(job);
   if(job.status!=='completed')writingFailure(job.error||'本轮未完成，可查看保留的稿件与运行记录后重试。');
   message(text);return;
  }
  await new Promise(resolve=>setTimeout(resolve,1200));job=null;
 }}finally{writingJob=null;$('writing-stop').hidden=true;setBusy(false);updateWritingButtons();}
}
async function startWriting(extra={}){
 requireCurrentEdition();if(busy)throw Error('已有任务正在运行。');
 const eid=editionId,pid=activeProfile;writingJob={job_id:null};setBusy(true);$('writing-error').hidden=true;
 try{
  await saveCurrentSelection();
  if(editionId!==eid||activeProfile!==pid||dirty)throw Error('选材或产品在保存时发生变化，当前内容已保留；请再次生成。');
  const payload={edition_id:eid,reader_profile:pid,decision_revision:detail.decision.decision_revision,...extra};
  const pendingKey=key('direct-pending-request'),signature=JSON.stringify(payload);let pending;
  try{pending=JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{}
  if(!pending||pending.signature!==signature)pending={signature,id:crypto.randomUUID()};
  localStorage.setItem(pendingKey,JSON.stringify(pending));
  const response=await api('writing-generate',{...payload,request_id:pending.id});
  await pollWriting(response.job_id);localStorage.removeItem(pendingKey);
 }catch(err){writingFailure(err);throw err;}finally{writingJob=null;setBusy(false);updateWritingButtons();}
}
$('writing-generate').onclick=writingAction(()=>startWriting());
$('writing-revise').onclick=writingAction(()=>{if(!$('writing-feedback').value.trim())throw Error('请先填写修改意见。');return startWriting({parent_version_id:latestDraft().version_id,feedback:$('writing-feedback').value});});
$('writing-feedback').oninput=saveWritingEditor;
$('writing-stop').onclick=safe(async()=>{if(writingJob?.job_id)await api('job-stop',{job_id:writingJob.job_id});});
function downloadWritingFile(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
document.addEventListener('click',safe(async event=>{
 const b=event.target.closest('button');if(!b)return;
 if(b.dataset.viewWriting){const v=writingVersion(b.dataset.viewWriting);$('writing-history-content').textContent=v.content.text||v.content.confirmed_text||v.content.manual_text||'';}
 const id=b.dataset.copyDraft||b.dataset.wordDraft||b.dataset.traceDraft;
 if(id){const pack=await api('writing-export',{version_id:id});
  if(b.dataset.copyDraft){await navigator.clipboard.writeText(pack.markdown);message('已复制完整稿件，包含来源和已核实的产品备注。');}
  else if(b.dataset.wordDraft){if(!pack.word_base64)throw Error('旧版稿件请从历史记录查看，Word 下载用于新版直接成稿。');const bytes=Uint8Array.from(atob(pack.word_base64),c=>c.charCodeAt(0));downloadWritingFile('快讯-'+id.slice(-8)+'.docx',bytes,'application/vnd.openxmlformats-officedocument.wordprocessingml.document');}
  else downloadWritingFile(id+'-依据.json',JSON.stringify(pack.trace,null,2),'application/json');
 }
 if(b.dataset.saveManual){const eid=editionId,vid=b.dataset.saveManual;const v=await api('writing-save-edit',{version_id:vid,text:$('writing-local-text').value,request_id:crypto.randomUUID()});if(eid===editionId){detail.writing.versions.push(v);renderWriting();}message('人工修改已另存为新版本，原稿保留。');}
}));
if(typeof detail!=='undefined'&&detail)renderWriting();
