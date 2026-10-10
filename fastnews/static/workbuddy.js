'use strict';
let workbuddyJob=null;
function workbuddySettings(){
 const selected=$('workbuddy-verified').value;
 if(!selected)return {workbuddy_model:null,workbuddy_effort:null};
 const v=JSON.parse(selected);return {workbuddy_model:v.model,workbuddy_effort:v.effort};
}
function renderWorkBuddy(){
 const c=state?.host_status?.workbuddy||{};
 const labels={unchecked:'未检查',required:'待登录',authenticated:'已登录',available:'可用',unavailable:'不可用',unverified:'待验证',not_verified:'待验证',failed:'失败'};
 const line=`安装：${c.installed?'已找到':'未找到'} · 登录：${labels[c.login]||'未检查'} · 模型：${labels[c.model]||'未检查'} · 联网：${labels[c.web]||'未检查'}`;
 const ad=state?.host_status?.agent_direct;
 const adLine=ad?.configured&&state?.reader_profile?.settings?.agent_direct?' · 智能体直连：已配置，任务响应与来源需分别核验':'';
 $('workbuddy-check').title=line+adLine;
 $('workbuddy-login').textContent=c.login_entry==='cli'?'首次连接':'打开 WorkBuddy';
 $('workbuddy-login').title=c.login_entry==='cli'?'打开官方交互登录':'打开官方客户端；后台连接需另行检查';
 const p=state?.reader_profile?.settings||{};
 $('workbuddy-verified').innerHTML='<option value="">当前账户默认模型</option>'+(c.verified_models||[]).filter(v=>v.model||v.effort).map(v=>`<option value="${h(JSON.stringify({model:v.model,effort:v.effort}))}">${h((v.model||'默认模型')+' · '+(v.effort||'默认档位'))}</option>`).join('');
 if(p.workbuddy_model||p.workbuddy_effort)$('workbuddy-verified').value=JSON.stringify({model:p.workbuddy_model||null,effort:p.workbuddy_effort||null});
 $('workbuddy-state').textContent=line+'。'+(ad?.configured&&state?.reader_profile?.settings?.agent_direct?ad.note:(c.note||''));
}
async function checkWorkBuddy(custom=false){
 if(workbuddyJob)return;
 const data=custom?{model:$('workbuddy-model').value.trim(),effort:$('workbuddy-effort').value}:{};
 const result=await api('workbuddy-check',data);workbuddyJob=result.job_id;
 $('workbuddy-stop').hidden=false;$('workbuddy-check').disabled=true;$('workbuddy-verify-model').disabled=true;
 try{
  while(workbuddyJob){
   const job=await api('job?id='+encodeURIComponent(workbuddyJob));
   $('manager-message').textContent=job.progress?.at(-1)?.message||'正在检查 WorkBuddy 连接…';
   if(job.status!=='running'){
    await refreshState();renderManager();renderWorkBuddy();
    if(job.error)$('manager-message').textContent=job.error;
    break;
   }
   await new Promise(resolve=>setTimeout(resolve,1000));
  }
 }finally{workbuddyJob=null;$('workbuddy-stop').hidden=true;$('workbuddy-check').disabled=false;$('workbuddy-verify-model').disabled=false;}
}
$('workbuddy-login').onclick=safe(async()=>{const r=await api('workbuddy-login',{});$('manager-message').textContent=r.message;});
$('workbuddy-check').onclick=safe(()=>checkWorkBuddy(false));
$('workbuddy-verify-model').onclick=safe(()=>checkWorkBuddy(true));
$('workbuddy-stop').onclick=safe(()=>api('job-stop',{job_id:workbuddyJob}));
