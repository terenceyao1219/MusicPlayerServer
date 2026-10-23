const $ = s => document.querySelector(s);
const API = '/api/v1/mplayer';
let token = sessionStorage.getItem('mplayer-token'), me, folders=[], tracks=[], users=[], folderId=null, selected=new Set(), playback={schema_version:1,track_id:null,position:0,mode:'folder'}, dialogAction, saveAt=0, switching=false, mediaReady=false;
const audio=$('#audio');
let transport='direct',hlsEntry=null,restoring=false;
try{if(localStorage.getItem('mplayer:transport:v1')==='hls')transport='hls';}catch{}
$('#transport').value=transport;
const hlsEngine=typeof SonoraHLS==='undefined'?null:new SonoraHLS(audio,api,updateHLS,toast);
function updateHLS(current){
 if(!current||transport!=='hls')return;
 const changed=hlsEntry!==current.entry.index;hlsEntry=current.entry.index;
 playback.track_id=current.track_id;playback.position=current.position;mediaReady=true;
 if(changed){const track=tracks.find(t=>t.id===current.track_id);if(track){folderId=track.folder_id;selected.clear();$('#search').value='';}render();storePlayback();}
}

const usernameKey='mplayer:last-username';
function restoreUsername(){try{$('#login-form input[name="username"]').value=localStorage.getItem(usernameKey)||'';}catch{}}
function rememberUsername(username){$('#login-form input[name="username"]').value=username;try{localStorage.setItem(usernameKey,username);}catch{}}
restoreUsername();
const escapeHTML = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,method='GET',data){
 const options={method,headers:{Authorization:`Bearer ${token}`}};
 if(data instanceof FormData) options.body=data;
 else if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}
 const response=await fetch(API+path,options); const result=await response.json();
 if(!response.ok){if(response.status===401 && path!='/login') signOut();const error=Error(result.error||'操作失敗');error.status=response.status;throw error;}return result;
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4500);}
function run(fn){return async event=>{try{await fn(event);}catch(error){toast(error.message);}};}
function signOut(){if(hlsEngine)hlsEngine.stop();mediaReady=false;audio.pause();audio.removeAttribute('src');audio.load();token=null;sessionStorage.removeItem('mplayer-token');$('#app').hidden=true;$('#login-screen').hidden=false;$('#dialog').close();}
async function enter(){restoring=true;$('#transport').disabled=$('#mode').disabled=true;try{me=await api('/me');rememberUsername(me.username);document.body.classList.toggle('user',me.role==='user');$('#identity').textContent=me.username;$('#role-badge').textContent=me.role==='admin'?'ADMIN · 管理員':'MEMBER · 聆聽者';$('#login-screen').hidden=true;$('#app').hidden=false;folderId=null;selected.clear();$('#search').value='';showView('library');await refresh();await restoreLocal();}finally{restoring=false;$('#transport').disabled=$('#mode').disabled=false;}}
async function refresh(){[folders,tracks]=await Promise.all([api('/folders'),api('/tracks')]);if(folderId&&!folders.some(f=>f.id===folderId))folderId=null;selected.clear();render();}
function showView(name){
 for(const view of ['library','users','settings']){
  $('#'+view+'-view').hidden=view!==name;
  $('#nav-'+view).classList.toggle('active',view===name);
 }
}
function openFolder(id){folderId=id;selected.clear();$('#search').value='';showView('library');render();}
const trackTitle = name => String(name).replace(/\.(mp3|m4a|wav|ogg)$/i,'');
function visibleTracks(){const query=$('#search').value.toLowerCase();return tracks.filter(t=>(folderId!==null&&t.folder_id===folderId)&&t.name.toLowerCase().includes(query));}
const folderIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5h7l2 2h9v13H3Z"/></svg>';
function render(){
 const atRoot=folderId===null,currentFolder=folders.find(f=>f.id===folderId);
 $('#folder-nav').innerHTML=folders.map(f=>`<button class="folder-link ${folderId===f.id?'active':''}" data-folder="${f.id}">${escapeHTML(f.name)} <span>${f.track_count}</span></button>`).join('');
 $('#library-title').textContent=currentFolder?.name||'我的音樂庫';
 $('#library-summary').textContent=atRoot?`${folders.length} 個音樂資料夾 · 選擇資料夾瀏覽歌曲`:`${tracks.filter(t=>t.folder_id===folderId).length} 首歌曲`;
 $('#breadcrumb-current').textContent=currentFolder?.name||'所有資料夾';$('#folder-up').disabled=atRoot;
 $('#search').placeholder=atRoot?'搜尋資料夾…':'搜尋此資料夾的歌曲…';
 $('#search').setAttribute('aria-label',atRoot?'搜尋資料夾':'搜尋此資料夾的歌曲');
 document.querySelectorAll('.folder-action').forEach(el=>el.hidden=atRoot);
 $('#batch').hidden=atRoot;$('#folder-list-wrap').hidden=!atRoot;$('#track-list-wrap').hidden=atRoot;
 const visible=visibleTracks(),query=$('#search').value.toLowerCase();
 const visibleFolders=folders.filter(f=>f.name.toLowerCase().includes(query));
 $('#folder-list').innerHTML=visibleFolders.map(f=>`<tr><td><button class="folder-name" data-folder="${f.id}">${folderIcon}<span>${escapeHTML(f.name)}</span></button></td><td class="muted">資料夾</td><td class="muted">${f.track_count} 首</td></tr>`).join('');
 $('#tracks').innerHTML=visible.map((t,i)=>`<tr class="track-row ${t.id===playback.track_id?'current':''}"><td class="selection-cell"><input class="admin-only" type="checkbox" data-select="${t.id}" ${selected.has(t.id)?'checked':''} aria-label="選取 ${escapeHTML(trackTitle(t.name))}"></td><td class="muted">${String(i+1).padStart(2,'0')}</td><td><button class="track-name" data-play="${t.id}">${escapeHTML(trackTitle(t.name))}</button></td><td class="muted">${escapeHTML(t.folder_name)}</td><td><span class="format">${escapeHTML(t.name.split('.').pop().toUpperCase())}</span></td><td class="actions-cell"><button class="admin-only" data-rename="${t.id}">改名</button></td></tr>`).join('');
 $('#empty').hidden=(atRoot?visibleFolders.length:visible.length)>0;
 $('#empty-message').textContent=query?'沒有符合條件的項目。':atRoot?(me.role==='admin'?'新增資料夾，開始建立你的收藏。':'請聯絡管理員，為你開放音樂資料夾。'):'此資料夾尚無歌曲。';
 $('#selection-count').textContent=`已選取 ${selected.size} 首`;$('#select-all').checked=visible.length>0&&visible.every(t=>selected.has(t.id));$('#move-selected').disabled=$('#delete-selected').disabled=!selected.size;
 updateNow();
}
function updateNow(){const t=tracks.find(t=>t.id===playback.track_id);$('#now-title').textContent=t?trackTitle(t.name):'尚未選擇歌曲';$('#now-folder').textContent=t?.folder_name||'選一首喜歡的，開始播放';}
function dialog(title,html,action,submit='確認'){ $('#dialog-title').textContent=title;$('#dialog-content').innerHTML=html;$('#dialog-error').textContent='';$('#dialog-submit').textContent=submit;$('#dialog-submit').disabled=false;dialogAction=action;$('#dialog').showModal();}
function textField(label,key,value='',type='text',required=true){return `<label>${label}<input name="${key}" type="${type}" value="${escapeHTML(value)}" ${required?'required':''} maxlength="160"></label>`;}
$('#dialog-close').onclick=$('#dialog-cancel').onclick=()=>$('#dialog').close();
$('#dialog-form').onsubmit=async event=>{event.preventDefault();$('#dialog-submit').disabled=true;try{await dialogAction(new FormData(event.target));$('#dialog').close();}catch(error){$('#dialog-error').textContent=error.message;}finally{$('#dialog-submit').disabled=false;}};
$('#login-form').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;$('#login-error').textContent='';try{const result=await api('/login','POST',Object.fromEntries(new FormData(event.target)));token=result.access_token;sessionStorage.setItem('mplayer-token',token);event.target.reset();await enter();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}};
$('#logout').onclick=run(async()=>{await persist();await api('/logout','POST');signOut();});
$('#nav-library').onclick=$('#breadcrumb-root').onclick=$('#folder-up').onclick=()=>openFolder(null);
$('#folder-nav').onclick=$('#folder-list').onclick=event=>{const button=event.target.closest('[data-folder]');if(button)openFolder(Number(button.dataset.folder));};
$('#search').oninput=()=>{selected.clear();render();};
$('#tracks').onclick=run(async event=>{const play=event.target.closest('[data-play]'),rename=event.target.closest('[data-rename]');if(play)await choose(Number(play.dataset.play));if(rename){const track=tracks.find(t=>t.id===Number(rename.dataset.rename));dialog('重新命名歌曲',textField('完整檔名（保留副檔名）','name',track.name),async data=>{await api(`/tracks/${track.id}`,'PATCH',{name:data.get('name')});await refresh();toast('已同步更新檔案名稱');});}});
$('#tracks').onchange=event=>{const id=Number(event.target.dataset.select);if(id){event.target.checked?selected.add(id):selected.delete(id);render();}};
$('#select-all').onchange=event=>{visibleTracks().forEach(t=>event.target.checked?selected.add(t.id):selected.delete(t.id));render();};
$('#add-folder').onclick=()=>dialog('新增音樂資料夾',textField('資料夾名稱','name'),async data=>{const result=await api('/folders','POST',{name:data.get('name')});folderId=result.id;await refresh();});
$('#rename-folder').onclick=()=>dialog('重新命名資料夾',textField('名稱','name',folders.find(f=>f.id===folderId).name),async data=>{await api(`/folders/${folderId}`,'PATCH',{name:data.get('name')});await refresh();});
$('#delete-folder').onclick=()=>dialog('刪除資料夾',`<p>確定刪除「${escapeHTML(folders.find(f=>f.id===folderId).name)}」？只有空資料夾可以刪除，此操作無法復原。</p>`,async()=>{await api(`/folders/${folderId}`,'DELETE');folderId=null;await refresh();},'確認刪除');
$('#upload').onchange=run(async event=>{const files=Array.from(event.target.files);event.target.value='';if(!files.length)return;if(!folderId){toast('請先選擇或新增一個資料夾，再上傳音樂');return;}const data=new FormData();files.forEach(file=>data.append('files',file));toast('音樂上傳中…');await api(`/folders/${folderId}/tracks`,'POST',data);await refresh();toast(`已上傳 ${files.length} 首音樂`);});
$('#delete-selected').onclick=()=>{const ids=[...selected];dialog('永久刪除音樂',`<p>即將永久刪除 <strong>${ids.length} 首音樂</strong>，實際音樂檔案也會刪除。此操作無法復原，確定繼續？</p>`,async()=>{await api('/tracks/delete','POST',{track_ids:ids});if(ids.includes(playback.track_id)){mediaReady=false;audio.pause();playback={...playback,track_id:null,position:0};storePlayback();loadAudio(false);}await refresh();toast('音樂已刪除');},'永久刪除');};
$('#move-selected').onclick=()=>{const ids=[...selected];dialog('搬移音樂',`<p>將 ${ids.length} 首音樂搬移到：</p><label>目的地<select name="folder_id">${folders.map(f=>`<option value="${f.id}">${escapeHTML(f.name)}</option>`).join('')}</select></label>`,async data=>{await api('/tracks/move','POST',{track_ids:ids,folder_id:Number(data.get('folder_id'))});await refresh();toast('音樂檔案已同步搬移');});};
async function openPermissions(userId=null){
 const [accounts, availableFolders]=await Promise.all([api('/users'),api('/folders')]);
 const members=accounts.filter(u=>u.role==='user');
 dialog('使用者存取權限',`<label>選擇 user<select id="permission-user" name="user_id" required><option value="">請選擇使用者</option>${members.map(u=>`<option value="${u.id}">${escapeHTML(u.username)}</option>`).join('')}</select></label><p class="muted">勾選可存取的音樂資料夾；取消勾選會撤銷存取權限。</p><p id="permission-status" role="status">${members.length?'請先選擇使用者。':'尚無 user 帳號，請先到帳號管理新增。'}</p><fieldset id="permission-folders" disabled hidden><legend>音樂資料夾</legend><div class="permission-tools"><button type="button" id="permission-all">全部勾選</button><button type="button" id="permission-none">全部取消</button></div>${availableFolders.map(f=>`<label class="check"><input type="checkbox" name="folder_ids" value="${f.id}"> ${escapeHTML(f.name)} <small class="muted">（${f.track_count} 首）</small></label>`).join('')||'<p>尚無音樂資料夾，請先新增資料夾。</p>'}</fieldset>`,async data=>{
  const selectedUser=Number(data.get('user_id'));
  if(!selectedUser||fieldset.disabled)throw Error('請先選擇使用者並等待權限載入完成');
  const folderIds=data.getAll('folder_ids').map(Number);
  selector.disabled=true;fieldset.disabled=true;
  try{await api(`/users/${selectedUser}/permissions`,'PUT',{folder_ids:folderIds});toast('此使用者的資料夾權限已儲存');}
  finally{selector.disabled=false;fieldset.disabled=false;}
 },'儲存權限');
 const selector=$('#permission-user'),fieldset=$('#permission-folders'),status=$('#permission-status'),submit=$('#dialog-submit');
 submit.disabled=true;
 let version=0;
 async function load(){
  const current=++version,id=selector.value;
  fieldset.disabled=true;fieldset.hidden=true;submit.disabled=true;$('#dialog-error').textContent='';
  fieldset.querySelectorAll('input').forEach(input=>input.checked=false);
  if(!id){status.textContent='請先選擇使用者。';return;}
  status.textContent='正在載入此使用者的資料夾權限…';
  try{
   const result=await api(`/users/${id}/permissions`);
   if(current!==version||$('#permission-user')!==selector||!$('#dialog').open)return;
   fieldset.querySelectorAll('input').forEach(input=>input.checked=result.folder_ids.includes(Number(input.value)));
   fieldset.disabled=false;fieldset.hidden=false;submit.disabled=false;updateCount();
  }catch(error){if(current===version&&$('#permission-user')===selector){status.textContent='載入失敗，請重新選擇使用者。';$('#dialog-error').textContent=error.message;}}
 }
 function updateCount(){status.textContent=`已勾選 ${fieldset.querySelectorAll('input:checked').length} / ${availableFolders.length} 個音樂資料夾，儲存後生效。`;}
 selector.onchange=load;fieldset.onchange=updateCount;
 $('#permission-all').onclick=()=>{fieldset.querySelectorAll('input').forEach(input=>input.checked=true);updateCount();};
 $('#permission-none').onclick=()=>{fieldset.querySelectorAll('input').forEach(input=>input.checked=false);updateCount();};
 if(userId&&members.some(u=>u.id===userId)){selector.value=String(userId);await load();}
}
$('#sync-library').onclick=()=>dialog('掃描並同步音樂庫',`<p>將登錄音樂目錄內一層資料夾中的 mp3、m4a、wav、ogg，並移除已不存在的資料夾與歌曲紀錄。</p><p>新資料夾預設不授權任何 user；既有資料夾保留授權。外部改名視為移除舊路徑並新增新路徑。</p><p class="muted">請先完成外部複製或搬移，再開始掃描。同步只更新資料庫，不會刪除實際檔案。</p><p id="sync-result" role="status"></p>`,async()=>{
 const button=$('#sync-library'),close=$('#dialog-close'),cancel=$('#dialog-cancel');
 button.disabled=true;close.disabled=true;cancel.disabled=true;
 const preventClose=event=>event.preventDefault();$('#dialog').addEventListener('cancel',preventClose);
 $('#sync-result').textContent='正在掃描音樂庫，請稍候…';
 try{
  const result=await api('/library/sync','POST');
  $('#sync-result').textContent='同步完成，正在更新清單…';
  const previousId=playback.track_id;
  await persist();
  const restored=await api('/playback/restore','POST',playback);
  const current=restored.state;
  if(previousId!==current.track_id){mediaReady=false;audio.pause();playback=current;storePlayback();loadAudio(false);}
  await refresh();
  toast(`同步完成：新增 ${result.folders_added} 個資料夾、${result.tracks_added} 首歌曲；移除 ${result.folders_removed} 個資料夾、${result.tracks_removed} 首歌曲紀錄；略過 ${result.skipped_count} 個項目。`);
 }catch(error){$('#sync-result').textContent='';throw error;}
 finally{button.disabled=false;close.disabled=false;cancel.disabled=false;$('#dialog').removeEventListener('cancel',preventClose);}
},'開始同步');
$('#permissions').onclick=run(()=>openPermissions());
function userDeletionReason(user){if(user.role==='admin'&&users.filter(u=>u.role==='admin').length<=1)return '不能刪除最後一位管理員';if(user.id===me.id)return '不能刪除目前登入的帳號';return ''; }
async function refreshUsers(){users=await api('/users');$('#users').innerHTML=users.map(u=>`<tr><td>${escapeHTML(u.username)}</td><td><span class="badge">${u.role}</span></td><td>${u.role==='user'?`<button data-user-permissions="${u.id}">存取權限</button> `:''}<button data-edit-user="${u.id}">編輯</button> <button class="danger" data-delete-user="${u.id}" ${userDeletionReason(u)?`disabled title="${userDeletionReason(u)}"`:""}>刪除</button>${userDeletionReason(u)?` <small class="muted">${userDeletionReason(u)}</small>`:""}</td></tr>`).join('');}
$('#nav-users').onclick=run(async()=>{showView('users');await refreshUsers();});
function editUser(user){dialog(user?'編輯帳號':'新增帳號',textField('帳號','username',user?.username||'')+textField(user?'新密碼（留白保留原密碼）':'密碼（至少 8 字元）','password','','password',!user)+`<label>角色<select name="role"><option value="user">user · 聆聽者</option><option value="admin" ${user?.role==='admin'?'selected':''}>admin · 管理員</option></select></label>`+(user?'<p class="muted">儲存後，此帳號現有登入會失效。</p>':''),async data=>{const values=Object.fromEntries(data);if(user&&!values.password)delete values.password;await api(user?`/users/${user.id}`:'/users',user?'PATCH':'POST',values);if(user?.id===me.id){signOut();toast('帳號已更新，請重新登入');}else await refreshUsers();});}
$('#add-user').onclick=()=>editUser(null);
$('#users').onclick=run(async event=>{const permission=event.target.closest('[data-user-permissions]');if(permission){await openPermissions(Number(permission.dataset.userPermissions));return;}const edit=event.target.closest('[data-edit-user]'),del=event.target.closest('[data-delete-user]');if(edit)editUser(users.find(u=>u.id===Number(edit.dataset.editUser)));if(del){const u=users.find(u=>u.id===Number(del.dataset.deleteUser));if(userDeletionReason(u)){toast(userDeletionReason(u));return;}dialog('刪除帳號',`<p>確定永久刪除帳號「${escapeHTML(u.username)}」？其登入與授權也會移除。</p>`,async()=>{await api(`/users/${u.id}`,'DELETE');await refreshUsers();},'刪除帳號');}});
let storageWarning=false,randomHistory=[],randomFuture=[];
function storageKey(){return `mplayer:playback:v1:${me.id}`;}
function warnStorage(){if(!storageWarning){storageWarning=true;toast('瀏覽器無法儲存播放紀錄；本次仍可播放，關閉後可能無法續播。');}}
function storePlayback(){
 if(!me||!token)return;
 try{localStorage.setItem(storageKey(),JSON.stringify({schema_version:1,track_id:playback.track_id,position:playback.position,mode:playback.mode}));}
 catch{warnStorage();}
}
async function restoreLocal(){
 randomHistory=[];randomFuture=[];
 let saved={schema_version:1,track_id:null,position:0,mode:'folder'};
 let raw=null;
 try{raw=localStorage.getItem(storageKey());}catch{warnStorage();}
 if(raw!==null){try{saved=JSON.parse(raw);}catch{toast('播放紀錄格式無效，已重設。');}}
 let result;
 try{result=await api('/playback/restore','POST',saved);}
 catch(error){if(error.status!==400)throw error;toast('播放紀錄格式無效，已重設。');result=await api('/playback/restore','POST',{});}
 playback=result.state;
 if(result.reason)toast('原歌曲已移除或無權存取，請重新選歌。');
 loadAudio(false);
}
async function persist(){
 if(token&&me&&!switching&&!restoring){
  if(transport==='hls'){if(hlsEngine)updateHLS(hlsEngine.current());}
  else if(playback.track_id&&mediaReady&&audio.hasAttribute('src'))playback.position=Number.isFinite(audio.currentTime)?audio.currentTime:0;
  storePlayback();
 }
}
function loadAudio(autoplay){
 // A new playback target also determines the visible folder, for every role.
 const currentTrack=tracks.find(t=>t.id===playback.track_id);
 if(currentTrack){
  if(folderId!==currentTrack.folder_id){selected.clear();}
  folderId=currentTrack.folder_id;
  // A previous search must not hide the newly playing song.
  $('#search').value='';
  showView('library');
 }
 mediaReady=false;audio.onloadedmetadata=null;updateNow();$('#mode').value=playback.mode;
 if(hlsEngine)hlsEngine.stop();hlsEntry=null;
 if(!playback.track_id){audio.removeAttribute('src');audio.load();render();return;}
 if(transport==='hls'){
  audio.removeAttribute('src');audio.load();render();$('#elapsed').textContent=clock(playback.position);$('#duration').textContent='—';
  if(!autoplay)return;
  if(!hlsEngine)throw Error('HLS 播放元件載入失敗');
  toast('正在準備 HLS，首次播放需要轉換歌曲…');
  $('#hls-status').textContent='HLS 準備中，可切回原始串流';
  return hlsEngine.start({...playback}).finally(()=>{$('#hls-status').textContent='';});
 }
 const position=playback.position,id=playback.track_id;
 audio.src=API+`/tracks/${id}/stream`;
 audio.onloadedmetadata=()=>{if(playback.track_id!==id)return;if(Number.isFinite(audio.duration))audio.currentTime=Math.min(position,audio.duration);mediaReady=true;if(autoplay)audio.play().catch(()=>toast('請按播放按鈕開始聆聽'));};
 audio.load();render();
}
async function choose(id){
 if(switching||restoring)return;switching=true;
 try{const result=await api('/playback/state','POST',{schema_version:1,track_id:id,position:0,mode:playback.mode});mediaReady=false;audio.pause();playback=result.state;randomHistory=[];randomFuture=[];storePlayback();await loadAudio(true);}
 finally{switching=false;}
}
async function advance(direction,trigger='manual'){
 if(switching||restoring)return;await persist();if(switching)return;
 if(transport==='hls'&&playback.mode!=='single'&&trigger==='manual'&&hlsEngine&&hlsEngine.step(direction)){await persist();return;}
 switching=true;
 try{
  const previous=playback.track_id,isRandom=playback.mode==='random';
  let target;
  const historyTarget=isRandom?(direction===-1?randomHistory.at(-1):randomFuture.at(-1)):null;
  if(historyTarget){const checked=await api('/playback/state','POST',{track_id:historyTarget,mode:playback.mode});target=checked.state.track_id;}
  else{const result=await api('/playback/resolve','POST',{track_id:previous,mode:playback.mode,direction,trigger});target=result.track_id;}
  if(isRandom&&previous){
   if(direction===-1&&historyTarget){randomHistory.pop();randomFuture.push(previous);}
   else{randomHistory.push(previous);if(historyTarget)randomFuture.pop();else randomFuture=[];}
   if(randomHistory.length>200)randomHistory.shift();
  }
  mediaReady=false;audio.pause();playback={...playback,track_id:target,position:0};storePlayback();await loadAudio(true);
 }finally{switching=false;}
}
$('#play').onclick=run(async()=>{if(restoring)return;if(transport==='hls'&&hlsEngine&&hlsEngine.loading){toast('HLS 正在定位所選歌曲，請稍候。');return;}if(!playback.track_id){if(!visibleTracks().length)throw Error('目前沒有可播放的音樂');await choose(visibleTracks()[0].id);}else if(transport==='hls'&&hlsEngine&&(!hlsEngine.session||hlsEngine.failed)){if(!switching){switching=true;try{const resume=await hlsEngine.resumePoint();if(resume)updateHLS(resume);await loadAudio(true);}finally{switching=false;}}}else if(audio.paused)await audio.play();else audio.pause();});
$('#next').onclick=run(()=>advance(1));$('#previous').onclick=run(()=>advance(-1));
$('#mode').onchange=run(async event=>{if(switching||restoring){event.target.value=playback.mode;return;}await persist();playback.mode=event.target.value;randomHistory=[];randomFuture=[];storePlayback();if(transport==='hls'){const playing=!audio.paused;switching=true;try{audio.pause();await loadAudio(playing);}finally{switching=false;}}});
$('#transport').onchange=run(async event=>{
 if(restoring||(switching&&!(hlsEngine&&hlsEngine.loading))){event.target.value=transport;return;}
 if(hlsEngine&&hlsEngine.loading)hlsEngine.stop();
 await persist();const playing=!audio.paused;transport=event.target.value;
 try{localStorage.setItem('mplayer:transport:v1',transport);}catch{warnStorage();}
 switching=true;try{mediaReady=false;audio.pause();await loadAudio(playing);}finally{switching=false;}
});
$('#volume').oninput=event=>audio.volume=Number(event.target.value);audio.volume=.8;
$('#seek').onchange=run(async event=>{if(transport==='hls'&&hlsEngine){hlsEngine.seek(Number(event.target.value));await persist();}else if(Number.isFinite(audio.duration)){audio.currentTime=Number(event.target.value);await persist();}});
const clock=seconds=>Number.isFinite(seconds)?`${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`:'0:00';
audio.ontimeupdate=()=>{const current=transport==='hls'&&hlsEngine?hlsEngine.current():null;if(current)updateHLS(current);const position=current?current.position:transport==='hls'?playback.position:audio.currentTime,duration=current?current.duration:transport==='hls'?0:audio.duration;$('#elapsed').textContent=clock(position);$('#duration').textContent=clock(duration);$('#seek').max=Number.isFinite(duration)?duration:0;$('#seek').value=position;if(Date.now()-saveAt>5000){saveAt=Date.now();persist().catch(error=>toast(error.message));}};
audio.onplay=()=>{$('#play').classList.toggle('is-playing',true);$('#play').setAttribute('aria-label','暫停');};audio.onpause=()=>{$('#play').classList.toggle('is-playing',false);$('#play').setAttribute('aria-label','播放');persist().catch(error=>toast(error.message));};audio.onended=run(()=>advance(1,'ended'));audio.onerror=()=>{if(transport==='hls'&&hlsEngine)hlsEngine.failed=true;if(audio.hasAttribute('src'))toast('無法播放：請確認檔案格式、資料夾權限或重新登入。');};
window.addEventListener('pagehide',()=>{persist();});
async function refreshHLSForeground(){
 if(transport!=='hls'||!hlsEngine)return;
 await hlsEngine.refresh();
 if(audio.error)hlsEngine.failed=true;
 const current=hlsEngine.current(true);if(current)updateHLS(current);
 await persist();
 if(hlsEngine.failed)toast('HLS 已中斷，按播放可從目前歌曲進度重新連線。');
}
window.addEventListener('pageshow',()=>{refreshHLSForeground().catch(error=>toast(error.message));});
if(document.addEventListener)document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshHLSForeground().catch(error=>toast(error.message));});
// Other tabs may save their own resume point; never apply storage events to live playback.
if(token)enter().catch(error=>{signOut();toast(error.message);});

fetch(API+'/version').then(response=>{if(!response.ok)throw Error();return response.json();}).then(info=>{document.querySelectorAll('.site-version').forEach(el=>el.textContent=`v${info.version}`);}).catch(()=>{document.querySelectorAll('.site-version').forEach(el=>el.textContent='版本資訊暫時無法取得');});
