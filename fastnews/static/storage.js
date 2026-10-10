'use strict';
function collectBrowserDrafts(){
 const result={},prefix='fastnews:'+state.workspace_id+':';
 for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(!k?.startsWith(prefix))continue;
  const suffix=k.slice(prefix.length);
  if(/^(last-edition|[A-Za-z0-9_-]{1,100}:(draft|order|direct-writing-editor|draft-edit:[A-Za-z0-9_-]{1,100}))$/.test(suffix))result[suffix]=localStorage.getItem(k);
 }
 result['reader-profile']=activeProfile;return result;
}
async function restoreBrowserDrafts(){
 try{const r=await api('browser-recovery');if(!r.restore_id)return;
 const marker='fastnews:'+state.workspace_id+':restored:'+r.restore_id;if(localStorage.getItem(marker))return;
 for(const [suffix,value] of Object.entries(r.restored_drafts||{})){
  const target=suffix==='reader-profile'?'fastnews:reader-profile':'fastnews:'+state.workspace_id+':'+suffix;
  if(localStorage.getItem(target)===null)localStorage.setItem(target,value);
 }
 const recoveredProfile=localStorage.getItem('fastnews:reader-profile');
 if(recoveredProfile&&recoveredProfile!==activeProfile){activeProfile=recoveredProfile;await refreshState();}
 localStorage.setItem(marker,'true');message('已恢复备份中的浏览器草稿；本浏览器已有修改保留。');
 }catch(e){message('迁移草稿尚未恢复：'+e.message+'。备份仍保留，可在数据与备份中重试。');}
}
function sizeLabel(n){return ((n||0)/1024/1024).toFixed(1)+' MB';}
async function loadStorage(){
 const r=await api('storage');$('storage-location').textContent='资料目录：'+r.data_directory+'\n导出目录：'+r.exports_directory;
 $('storage-size').textContent='本地数据占用 '+sizeLabel(r.data_bytes)+'（包括备份与缓存）';
 $('storage-backups').innerHTML=r.backups.length?r.backups.map(v=>`<li><a href="/api/backup-download?name=${encodeURIComponent(v.name)}" download>${h(v.name)}</a><span class="muted"> ${h(sizeLabel(v.size))}</span></li>`).join(''):'<li class="muted">还没有创建数据备份。</li>';
 $('create-backup').disabled=busy;
}
$('open-storage').onclick=safe(async()=>{$('storage-dialog').showModal();$('storage-result').textContent='';await loadStorage();});
$('close-storage').onclick=()=>$('storage-dialog').close();
$('create-backup').onclick=async()=>{
 $('create-backup').disabled=true;$('storage-result').textContent='正在创建并校验备份…';
 try{const r=await api('storage-backup',{browser_drafts:collectBrowserDrafts()});
 $('storage-result').textContent='备份已完成并通过完整性校验；已保存当前浏览器草稿。可下载保存到其他位置。';
 await loadStorage();
 }catch(e){$('storage-result').textContent=e.message;}finally{$('create-backup').disabled=busy;}
};
$('restore-browser-drafts').onclick=safe(restoreBrowserDrafts);
