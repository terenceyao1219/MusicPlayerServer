/* Account preferences and administrator analysis controls. */
(() => {
 const enabled=$('#normalization-enabled'),target=$('#normalization-target'),peak=$('#normalization-peak');
 const status=$('#normalization-status'),auto=$('#normalization-auto'),start=$('#normalization-analyze'),stop=$('#normalization-stop');
 let generation=0,timer=null,refreshTimer=null;
 function display(p){enabled.checked=p.enabled;target.value=String(p.target);peak.value=String(p.peak);enabled.disabled=false;target.disabled=peak.disabled=!p.enabled;}
 function displayLibrary(s){
  auto.checked=s.auto_analyze;auto.disabled=false;start.disabled=s.running;stop.disabled=!s.running||s.stopping;
  $('#normalization-progress').textContent=(s.stopping?'正在停止，會先完成目前工作。 ':s.running?'背景分析中。 ':'')+
   `已完成 ${s.completed}／${s.total} 首，失敗 ${s.failed} 首。`+(s.current?` 目前：${s.current}`:'');
 }
 async function poll(version){
  if(version!==generation||!token||$('#settings-view').hidden)return;
  try{displayLibrary(await api('/normalization/library'));}catch(error){if(version===generation)$('#normalization-progress').textContent=error.message;}
  if(version===generation)timer=setTimeout(()=>poll(version),2000);
 }
 window.loadNormalizationSettings=async()=>{
  const version=++generation;clearTimeout(timer);
  enabled.disabled=target.disabled=peak.disabled=true;status.textContent='正在讀取設定…';
  const p=await api('/normalization');
  if(version!==generation)return;
  display(p);status.textContent=p.enabled?'已啟用；重新選歌或重新開啟電台後套用。':'已關閉，使用原有播放流程。';
  if(me.role==='admin')await poll(version);
 };
 async function save(){
  enabled.disabled=target.disabled=peak.disabled=true;
  try{
   const p=await api('/normalization','PUT',{enabled:enabled.checked,target:Number(target.value),peak:Number(peak.value)});
   display(p);status.textContent='已儲存；重新選歌或重新開啟電台後生效。';
  }catch(error){
   status.textContent=error.message;
   try{display(await api('/normalization'));}catch{}
   throw error;
  }
 }
 enabled.onchange=target.onchange=peak.onchange=run(save);
 auto.onchange=run(async()=>{
  auto.disabled=true;
  try{displayLibrary(await api('/normalization/library','PUT',{auto_analyze:auto.checked}));}
  catch(error){auto.checked=!auto.checked;throw error;}finally{auto.disabled=false;}
 });
 start.onclick=run(async()=>{start.disabled=true;try{displayLibrary(await api('/normalization/library','POST',{}));}catch(error){start.disabled=false;throw error;}});
 stop.onclick=run(async()=>{stop.disabled=true;displayLibrary(await api('/normalization/library','DELETE'));});
 async function refreshIcons(){
  if(token&&me&&!$('#app').hidden&&!document.hidden&&!$('#library-view').hidden){
   try{const rows=await api('/tracks'+(typeof folderId==='number'?'?folder_id='+folderId:''));if(token){const states=new Map(rows.map(t=>[t.id,t.analyzed]));for(const track of tracks)if(states.has(track.id))track.analyzed=!!states.get(track.id);
    for(const icon of document.querySelectorAll('[data-analysis]')){const ready=!!states.get(Number(icon.dataset.analysis));icon.classList.toggle('analyzed',ready);icon.title=ready?'音量分析完成':'尚未分析音量';icon.setAttribute('aria-label',icon.title);}
   }}catch{}
  }
  refreshTimer=setTimeout(refreshIcons,5000);
 }
 refreshTimer=setTimeout(refreshIcons,5000);
 window.addEventListener('sonora-signout',()=>{generation++;clearTimeout(timer);});
})();
