(() => {
 const list=$('#radio-list');
 let stations=[],availableFolders=[],generation=0;
 const svg=paths=>'<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+paths+'</svg>';
 const copyIcon=svg('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>');
 const eyeIcon=svg('<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>');
 const absolute=value=>new URL(value,location.origin).href;
 function field(station,key,label,value,type='text'){
  return '<label class="radio-field">'+label+'<span class="radio-value"><input readonly type="'+type+'" value="'+escapeHTML(value)+'" aria-label="'+label+'">'+
   (type==='password'?'<button type="button" data-reveal="'+station.id+'" aria-label="顯示或隱藏播放密碼">'+eyeIcon+'</button>':'')+
   '<button type="button" data-copy="'+key+'" data-id="'+station.id+'" aria-label="複製'+label+'" title="複製'+label+'">'+copyIcon+'</button></span></label>';
 }
 function render(){
  list.innerHTML=stations.map(s=>'<article class="radio-card"><div class="radio-heading"><img width="80" height="80" src="'+escapeHTML(absolute(s.icon_url))+'?v='+encodeURIComponent(s.background+s.foreground)+'" alt="電台圖示"><div><h2>'+escapeHTML(s.name)+'</h2><p class="muted">'+
   (s.enabled?'已啟用':'已停用')+' · '+(s.connected?'播放連線使用中':'等待播放')+' · '+(s.ordering==='shuffle'?'每輪洗牌':'順序循環')+'</p></div></div>'+
   field(s,'name','站點名稱',s.name)+field(s,'stream_url','串流網址',absolute(s.stream_url))+
   field(s,'username','播放帳號（此帳號所有電台共用）',s.username)+field(s,'password','播放密碼（此帳號所有電台共用）',s.password,'password')+
   field(s,'icon_url','PNG 圖示網址',absolute(s.icon_url))+
   '<p class="muted">'+(s.scope==='all'?'全部有權限的音樂':s.folder_ids.length+' 個指定資料夾')+' · '+(s.resume?'近似續播':'每次從頭開始')+' · '+'新連線取代舊連線'+'</p>'+
   '<div class="radio-actions"><button data-edit="'+s.id+'">編輯</button><button data-action="toggle" data-id="'+s.id+'">'+(s.enabled?'停用':'啟用')+'</button><button data-action="disconnect" data-id="'+s.id+'">中斷播放連線</button><button data-action="reset" data-id="'+s.id+'">重設進度</button><button data-action="credentials" data-id="'+s.id+'">重設共用播放密碼</button><button class="danger" data-action="delete" data-id="'+s.id+'">刪除</button></div></article>').join('')||
   '<div class="settings-card"><h2>建立你的第一個續播電台</h2><p class="muted">選好喜歡的音樂，讓下次上車接著聽。</p></div>';
 }
 async function refreshRadios(){
  const version=++generation,owner=me.id;
  const results=await Promise.all([api('/radios'),api('/folders')]);
  if(version!==generation||!token||me.id!==owner)return;
  [stations,availableFolders]=results;render();
 }
 $('#nav-radios').onclick=run(async()=>{showView('radios');list.textContent='正在載入電台…';await refreshRadios();});
 $('#radio-refresh').onclick=run(refreshRadios);
 function editor(station){
  const s=station||{name:'',scope:'all',folder_ids:[],ordering:'sequential',enabled:true,resume:true,background:'#183e38',foreground:'#f3d78c'};
  const missing=s.folder_ids.filter(id=>!availableFolders.some(f=>f.id===id));
  dialog(station?'編輯續播電台':'建立續播電台',
   '<label>站點名稱<input name="name" required maxlength="80" value="'+escapeHTML(s.name)+'" placeholder="例如：上班通勤"></label>'+
   '<label>播放範圍<select name="scope"><option value="all" '+(s.scope==='all'?'selected':'')+'>全部有權限的音樂</option><option value="folders" '+(s.scope==='folders'?'selected':'')+'>指定資料夾（可複選）</option></select></label>'+
   '<fieldset id="radio-folders"><legend>選擇資料夾</legend>'+availableFolders.map(f=>'<label class="check"><input type="checkbox" name="folder_ids" value="'+f.id+'" '+(s.folder_ids.includes(f.id)?'checked':'')+'> '+escapeHTML(f.name)+'</label>').join('')+
   (missing.length?'<p class="muted">有 '+missing.length+' 個原選資料夾已不存在或權限已收回，儲存時會移除。</p>':'')+'</fieldset>'+
   '<label>播放順序<select name="ordering"><option value="sequential">順序循環</option><option value="shuffle" '+(s.ordering==='shuffle'?'selected':'')+'>每輪洗牌</option></select></label>'+
   '<label class="check"><input name="resume" type="checkbox" '+(s.resume?'checked':'')+'> 記住上次播放位置（近似續播）</label>'+
   '<label class="check"><input name="enabled" type="checkbox" '+(s.enabled?'checked':'')+'> 啟用電台</label>'+
   '<fieldset class="radio-colors"><legend>電台圖示配色</legend><svg id="radio-preview" viewBox="0 0 256 256" role="img" aria-label="電台圖示配色預覽"><rect width="256" height="256" rx="20"/><g fill="none" stroke-width="7"><circle cx="128" cy="128" r="80"/><circle cx="128" cy="128" r="50"/><path d="M34 81a106 106 0 0 0 0 94M222 81a106 106 0 0 1 0 94"/></g><circle cx="128" cy="128" r="13"/></svg><label>背景色<input type="color" name="background" value="'+s.background+'"></label><label>圖案色<input type="color" name="foreground" value="'+s.foreground+'"></label><p class="muted">由 SONORA 產生 PNG 圖示，不接受上傳圖片。</p></fieldset>'+
   (station?'<p class="muted">儲存後需重新連線。變更範圍或順序會重設進度。</p>':''),
   async data=>{
    await api(station?'/radios/'+station.id:'/radios',station?'PUT':'POST',{
     name:data.get('name'),scope:data.get('scope'),folder_ids:data.getAll('folder_ids').map(Number),
     ordering:data.get('ordering'),resume:data.has('resume'),enabled:data.has('enabled'),
     background:data.get('background'),foreground:data.get('foreground')});
    await refreshRadios();toast(station?'電台已儲存，請重新連線。':'電台已建立，可複製資訊到播放器。');
   },'儲存電台');
  const scope=$('#dialog-content [name=scope]');
  scope.onchange=()=>{$('#radio-folders').hidden=scope.value!=='folders';};scope.onchange();
  const updatePreview=()=>{
   const bg=$('#dialog-content [name=background]').value,fg=$('#dialog-content [name=foreground]').value;
   $('#radio-preview rect').setAttribute('fill',bg);$('#radio-preview g').setAttribute('stroke',fg);
   $('#radio-preview > circle').setAttribute('fill',fg);
  };
  $('#dialog-content [name=background]').oninput=$('#dialog-content [name=foreground]').oninput=updatePreview;updatePreview();
 }
 $('#add-radio').onclick=run(async()=>{availableFolders=await api('/folders');editor();});
 list.onclick=run(async event=>{
  const copy=event.target.closest('[data-copy]');
  if(copy){
   const s=stations.find(s=>s.id===copy.dataset.id);if(!s)return;
   const key=copy.dataset.copy,value=key.endsWith('_url')?absolute(s[key]):s[key];
   try{await navigator.clipboard.writeText(value);toast('已複製'+copy.getAttribute('aria-label').replace('複製',''));}
   catch{const input=copy.closest('.radio-value').querySelector('input');input.type='text';input.focus();input.select();toast('無法自動複製，已選取內容，請手動複製。');}
   return;
  }
  const reveal=event.target.closest('[data-reveal]');
  if(reveal){const input=reveal.closest('.radio-value').querySelector('input');input.type=input.type==='password'?'text':'password';return;}
  const edit=event.target.closest('[data-edit]');
  if(edit){availableFolders=await api('/folders');editor(stations.find(s=>s.id===edit.dataset.edit));return;}
  const action=event.target.closest('[data-action]');if(!action)return;
  const s=stations.find(s=>s.id===action.dataset.id),kind=action.dataset.action;
  if(!s)return;
  const execute=async()=>{
   if(kind==='delete')await api('/radios/'+s.id,'DELETE');
   else await api('/radios/'+s.id+'/'+kind,'POST',kind==='toggle'?{enabled:!s.enabled}:undefined);
   await refreshRadios();toast('電台設定已更新。');
  };
  if(kind==='toggle'||kind==='disconnect'){await execute();return;}
  const descriptions={delete:'刪除後，串流與圖示網址都會失效。',reset:'會清除續播位置及本輪洗牌紀錄，並中斷目前播放。',credentials:'這會重設此帳號所有續播電台的共用密碼並中斷全部播放連線，請將新密碼更新到播放器。'};
  dialog(action.textContent,'<p>'+escapeHTML(s.name)+'</p><p>'+descriptions[kind]+'</p>',execute,action.textContent);
 });
 window.addEventListener('sonora-signout',()=>{generation++;stations=[];list.replaceChildren();});
})();

