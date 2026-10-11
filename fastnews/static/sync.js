'use strict';
// The conversation and every open page observe the same saved task.
let dashboardSyncTimer=null,dashboardSyncRunning=false,dashboardSyncDelay=1500;
let dashboardSyncInitialized=false;
const seenDashboardJobs=new Map();
function seenJobKey(){return 'fastnews:'+state.workspace_id+':seen-job';}
function localWorkInProgress(){
 const editing=document.activeElement;
 return dirty||!!editing?.matches?.('input:not([type=button]),textarea,[contenteditable=true]');
}
function observeDashboardJob(job){
 if(!job?.job_id)return;
 seenDashboardJobs.set(job.job_id,job.status);
 if(state&&job.status!=='running')localStorage.setItem(seenJobKey(),JSON.stringify({id:job.job_id,status:job.status}));
 renderTaskActivity(job);
}
function renderTaskActivity(job){
 const panel=$('task-activity');if(!panel)return;
 panel.hidden=!job;if(!job)return;
 const running=job.status==='running',done=job.status==='completed';
 const progress=job.progress||[],last=progress.at(-1)||{};
 const title=({research:'产品检索',digest:'晨报整理',explore:'补充检索',
  'writing-generate':'快讯写作','writing-draft':'快讯写作','connection-check':'连接检查',
  scan:'更新资讯',probe:'检查信息源'})[job.action]||'当前任务';
 $('task-title').textContent=title+(job.product_code?' · '+job.product_code:'');
 $('task-status').textContent=running?(last.agent_status==='waiting'?'等待客户端接手':'正在处理'):done?'结果已保存':'本次未完成';
 const count=job.result?.reading_report?.output?.items?.length??job.result?.output?.items?.length;
 $('task-description').textContent=job.error||(running?
  (last.agent_status==='waiting'&&job.origin==='conversation'?'任务已从对话创建，等待当前客户端处理。':last.message||'任务已创建，正在准备资料。'):
  done?(count===0?'本轮未形成有效线索，请查看结果区的覆盖缺口。':Number.isInteger(count)?`本轮保存 ${count} 条线索，可在下方阅读和选材。`:'处理结果已保存到本机。'):'已保存的材料和稿件仍可查看。');
 panel.classList.toggle('task-failed',!running&&!done);
 panel.classList.toggle('task-complete',done);
 const step=done?2:1;
 $('task-steps').innerHTML=['任务已建立','检索与整理','结果保存'].map((name,i)=>
  `<li class="${i<step||done?'complete':i===step?'current':''}"${i===step?' aria-current="step"':''}>${h(name)}</li>`).join('');
 $('task-record').hidden=false;
}
async function activateDashboardJob(job){
 setBusy(true);$('sync-result').hidden=true;
 try{
  const profile=job.reader_profile||'default';
  if(activeProfile!==profile){activeProfile=profile;localStorage.setItem('fastnews:reader-profile',profile);}
  await refreshState();
  if(job.edition_id&&job.edition_id!==editionId)await loadEdition(job.edition_id);
  if(job.date&&['digest','scan'].includes(job.action)){$('news-day').value=job.date;await loadDaily();}
  if(typeof showWorkspace==='function')showWorkspace(job.action?.startsWith('writing-')?'materials':
   ['research','explore'].includes(job.action)?'product':['digest','scan'].includes(job.action)?'morning':'sources',false);
  await pollJob(job.job_id);
 }finally{setBusy(false);}
}
async function checkDashboardUpdates(){
 if(!state||busy||dashboardSyncRunning)return;
 if(!dashboardSyncInitialized){
  dashboardSyncInitialized=true;
  try{const saved=JSON.parse(localStorage.getItem(seenJobKey())||'null');if(saved?.id)seenDashboardJobs.set(saved.id,saved.status);}catch{}
 }
 dashboardSyncRunning=true;
 try{
  const activity=await api('activity');
  if(activity.workspace_id!==state.workspace_id)throw Error('看板服务目录已变化，请重新打开当前项目。');
  $('sync-status').textContent='已连接 · 自动同步';
  dashboardSyncDelay=1500;
  const job=activity.job;
  if(!job||(!['research','digest','explore','scan'].includes(job.action)&&!job.action?.startsWith('writing-')))return;
  if(seenDashboardJobs.get(job.job_id)===job.status&&job.status!=='running')return;
  renderTaskActivity(job);
  if(localWorkInProgress()||(job.reader_profile||'default')!==activeProfile){
   $('sync-result').hidden=false;
   $('sync-result-label').textContent=localWorkInProgress()?'有新的任务进展，当前编辑已保留。':'另一来源方案有新的任务进展。';
   $('show-synced-result').onclick=safe(()=>activateDashboardJob(job));return;
  }
  await activateDashboardJob(job);
 }catch(error){
  $('sync-status').textContent='同步暂停 · '+error.message;
  dashboardSyncDelay=6000;
 }finally{dashboardSyncRunning=false;}
}
function startDashboardSync(){
 if(dashboardSyncTimer!==null)return;
 const tick=async()=>{await checkDashboardUpdates();dashboardSyncTimer=setTimeout(tick,dashboardSyncDelay);};
 dashboardSyncTimer=setTimeout(tick,1500);
}
document.addEventListener('DOMContentLoaded',startDashboardSync);
