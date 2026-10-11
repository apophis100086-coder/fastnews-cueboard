'use strict';
let entryJob=null;
const entryLabels={workbuddy:'WorkBuddy',codex:'Codex',api:'自接 API',none:'暂不使用模型'};
function renderExecution(){
 if(!state||!$('entry-current'))return;
 const s=state.reader_profile?.settings||{},host=s.host||'none';
 $('entry-current').textContent=entryLabels[host]||host;
 $('entry-summary').textContent=host==='api'?(state.execution?.api?.model_status==='available'?'已收到模型响应':'待配置或检查响应'):host==='none'?'可浏览与管理已存资料':'当前项目使用此客户端';
 if($('conversation-hint'))$('conversation-hint').textContent=['workbuddy','codex'].includes(host)?`在 ${entryLabels[host]} 的本项目对话提出需求，进度与结果在这里查看。`:'选择产品开始检索，阅读结果后再选材。';
 const pending=(state.execution?.pending||[]).filter(v=>v.status==='waiting'||v.status==='processing');
 $('entry-next').hidden=true;
 if(pending.length){const p=pending[0],label=entryLabels[p.host]||entryLabels[host];$('entry-next').textContent=p.status==='waiting'?`下一步：回到 ${label} 中的本项目对话，输入“处理看板任务”。本页会自动显示结果；也可停止任务。`:`${label} 已接手第 ${p.step} 步，正在处理。你可以继续查看已有材料。`;}
 $('open-execution').disabled=busy;
}
function updateEntryForm(){
 const host=$('reader-host').value,isApi=host==='api';
 $('api-config').hidden=!isApi;
 $('entry-instructions').textContent={workbuddy:'在 WorkBuddy 打开本项目，直接提出检索或晨报需求。项目技能会创建任务并将结果保存到看板。也可先点看板按钮，再说“处理看板任务”。',codex:'在 Codex 打开本项目，直接提出检索或晨报需求。项目技能会创建任务并将结果保存到看板。也可先点看板按钮，再说“处理看板任务”。',api:'保存服务商地址、模型与密钥后，任务会直接调用接口。无需回到客户端接手。',none:'只查看、选材、导入和保存已有资料；需要生成时再选择模型入口。'}[host];
 const chat=$('api-protocol').value==='chat_completions';
 $('api-web').disabled=chat;if(chat)$('api-web').checked=false;
 $('reader-web').disabled=host==='none'||(isApi&&!$('api-web').checked);
 if($('reader-web').disabled)$('reader-web').checked=false;
 $('entry-capability').textContent=isApi?'普通兼容接口可以整理已采集或导入的资料；只有支持 web_search 的 Responses 接口可执行模型补搜。':'客户端的模型和联网能力取决于当前账号。连接检查只验证任务响应；来源与稿件仍分别核对。';
}
function fillEntry(){
 const s=state.reader_profile.settings,a=state.execution?.api||{};
 $('entry-result').textContent='';
 $('reader-host').value=s.host;$('reader-web').checked=!!s.web_search;
 $('api-base').value=a.base_url||'';$('api-model').value=a.model||'';$('api-key').value='';
 $('api-key').placeholder=a.key_saved?'已保存；留空保留现有密钥':'首次使用时填写';
 $('api-protocol').value=a.protocol||'chat_completions';$('api-format').value=a.response_format||'json_object';
 $('api-web').checked=!!a.web_search;$('api-authorized').checked=!!a.authorized;
 $('api-requests').value=a.max_requests||4;$('api-output').value=a.max_output_tokens||6000;
 $('api-status').textContent=a.last_error||(a.configured?(a.model_status==='available'?'已收到有效 API 响应。':'配置已保存，尚待检查。'):'尚未配置。');
 $('api-credential-note').textContent=a.credential_origin==='shared_package'?a.note:'密钥由当前 Windows 账户加密保存，不随备份或标准发行包迁移。修改地址后需重新填写密钥；更换电脑后需重新配置。';
 updateEntryForm();
}
$('open-execution').onclick=safe(async()=>{await refreshState();fillEntry();$('execution-dialog').showModal();});
$('close-execution').onclick=()=>{$('api-key').value='';$('execution-dialog').close();};
$('reader-host').onchange=updateEntryForm;$('api-protocol').onchange=updateEntryForm;$('api-web').onchange=updateEntryForm;
$('api-base').oninput=()=>{$('api-authorized').checked=false;};
$('save-api').onclick=async()=>{
 try{const a=await api('api-settings',{base_url:$('api-base').value,model:$('api-model').value,api_key:$('api-key').value,
 protocol:$('api-protocol').value,response_format:$('api-format').value,web_search:$('api-web').checked,authorized:$('api-authorized').checked,
 max_requests:Number($('api-requests').value),max_output_tokens:Number($('api-output').value)});
 $('api-key').value='';$('api-key').placeholder='已保存；留空保留现有密钥';$('api-status').textContent='API 配置已保存。点击“保存入口”后可检查或使用。';await refreshState();
 }catch(e){$('api-status').textContent=e.message;}
};
$('save-entry').onclick=async()=>{
 try{await api('reader-profile',{profile_id:activeProfile,name:state.reader_profile.name,host:$('reader-host').value,web_search:$('reader-web').checked});
 await refreshState();$('entry-result').textContent='入口已保存；已有材料、选材和稿件保留。';
 }catch(e){$('entry-result').textContent=e.message;}
};
async function followEntryJob(id){
 entryJob=id;$('stop-entry').hidden=false;setBusy(true);$('check-entry').disabled=true;$('save-entry').disabled=true;$('save-api').disabled=true;
 try{while(entryJob){const job=await api('job?id='+encodeURIComponent(entryJob));
 $('entry-result').textContent=job.progress?.at(-1)?.message||'正在准备连接检查…';
 if(job.status!=='running'){$('entry-result').textContent=job.status==='completed'?'已收到有效响应。联网原文和业务质量需在具体任务中继续核对。':job.error||'连接检查已结束';break;}
 await refreshState();await new Promise(resolve=>setTimeout(resolve,1200));}
 }catch(e){$('entry-result').textContent=e.message;
 }finally{entryJob=null;setBusy(false);$('stop-entry').hidden=true;$('check-entry').disabled=false;$('save-entry').disabled=false;$('save-api').disabled=false;await refreshState();}
}
$('check-entry').onclick=async()=>{try{if($('reader-host').value!==state.reader_profile.settings.host)throw Error('请先保存所选入口，再检查。');const r=await api('connection-check',{});await followEntryJob(r.job_id);}catch(e){$('entry-result').textContent=e.message;}};
$('stop-entry').onclick=safe(async()=>{if(entryJob)await api('job-stop',{job_id:entryJob});});
