'use strict';
// Change visibility only: forms, drafts and selections remain mounted.
function showWorkspace(name, scroll=true){
 if(!['morning','product','materials','sources'].includes(name))return;
 if($('workspace-menu'))$('workspace-menu').open=false;
 for(const id of ['morning','product','materials','sources']){
  const screen=$(id+'-workspace');if(screen)screen.hidden=id!==name;
 }
 document.querySelectorAll('[data-workspace]').forEach(button=>{
  const active=button.dataset.workspace===name;
  if(button.getAttribute('role')==='tab'){
   button.setAttribute('aria-selected',String(active));button.tabIndex=button.dataset.workspace===(name==='sources'?'morning':name)?0:-1;
  }
  button.classList.toggle('current',active);
 });
 if(name==='product')$('editor-panel').open=true;
 const pool=$('pool-panel');
 if(name==='materials'&&pool&&!pool.dataset.viewInitialized){
  pool.open=typeof innerWidth==='undefined'||innerWidth>680;
  pool.dataset.viewInitialized='true';
 }
 if(name==='materials'&&typeof loadArchive==='function')loadArchive();
 if(scroll)$(name+'-workspace').scrollIntoView({behavior:'smooth',block:'start'});
}
document.querySelectorAll('[data-workspace]').forEach(button=>button.addEventListener('click',()=>showWorkspace(button.dataset.workspace)));
document.querySelectorAll('[role="tab"]').forEach(button=>button.addEventListener('keydown',event=>{
 const tabs=['morning','product','materials'],index=tabs.indexOf(button.dataset.workspace);
 let next;if(event.key==='ArrowRight')next=(index+1)%3;else if(event.key==='ArrowLeft')next=(index+2)%3;else if(event.key==='Home')next=0;else if(event.key==='End')next=2;else return;
 event.preventDefault();showWorkspace(tabs[next],false);$('tab-'+tabs[next]).focus();
}));
showWorkspace('morning',false);
document.addEventListener('keydown',event=>{
 if(event.key!=='Escape')return;
 for(const menu of document.querySelectorAll('.workspace-menu[open], .utility-menu[open]')){
  menu.open=false;menu.querySelector('summary').focus();
 }
});
