(() => {
 const enabled=$('#quick-test-enabled'),copy=$('#quick-test-copy'),status=$('#quick-test-status');
 let link=null,version=0;
 function display(state){
  enabled.checked=state.enabled;link=state.url?new URL(state.url,location.origin).href:null;
  copy.disabled=!link;status.textContent=state.enabled?'已啟用，可以複製 .m3u8 測試連結。':'目前已關閉，請先啟用測試連結。';
  $('#quick-test-manual').hidden=true;$('#quick-test-url').value='';
 }
 $('#nav-settings').onclick=run(async()=>{
  showView('settings');const current=++version;
  enabled.disabled=copy.disabled=true;link=null;$('#quick-test-manual').hidden=true;$('#quick-test-url').value='';
  status.textContent='正在讀取設定…';
  try{const state=await api('/quick-test');if(current===version){display(state);enabled.disabled=false;}}
  catch(error){if(current===version)status.textContent='設定讀取失敗，請重新開啟進階設定。';throw error;}
 });
 enabled.onchange=run(async()=>{
  const requested=enabled.checked;enabled.disabled=copy.disabled=true;
  status.textContent=requested?'正在準備測試音效…':'正在關閉測試連結…';
  try{display(await api('/quick-test','PUT',{enabled:requested}));}
  catch(error){enabled.checked=!requested;copy.disabled=!link;status.textContent='設定未成功儲存，請重試。';throw error;}
  finally{enabled.disabled=false;}
 });
 copy.onclick=run(async()=>{
  if(!link)return;
  try{
   if(!navigator.clipboard?.writeText)throw Error('clipboard unavailable');
   await navigator.clipboard.writeText(link);
   toast('連接 .m3u8 的 URL 已經拷貝到剪貼簿裡');
  }catch{
   $('#quick-test-url').value=link;$('#quick-test-manual').hidden=false;
   $('#quick-test-url').focus();$('#quick-test-url').select();
   toast('瀏覽器無法自動複製，請手動複製下方連結。');
  }
 });
})();
