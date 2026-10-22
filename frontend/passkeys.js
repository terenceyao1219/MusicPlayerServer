// WebAuthn uses platform verification (Face ID, Touch ID, Windows Hello or PIN).
// Biometric data never leaves the authenticator.
(() => {
 let available=false,busy=false;
 const loginButton=$('#passkey-login'),status=$('#passkey-status');
 function decode(value){const text=atob(value.replace(/-/g,'+').replace(/_/g,'/'));return Uint8Array.from(text,c=>c.charCodeAt(0));}
 function encode(value){return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');}
 function optionsFromJSON(options){
  const result={...options,challenge:decode(options.challenge)};
  if(options.user)result.user={...options.user,id:decode(options.user.id)};
  for(const key of ['allowCredentials','excludeCredentials'])if(options[key])result[key]=options[key].map(item=>({...item,id:decode(item.id)}));
  return result;
 }
 function credentialJSON(credential){
  const response={};
  for(const key of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle']){
   if(credential.response[key]!=null)response[key]=encode(credential.response[key]);
  }
  if(credential.response.getTransports)response.transports=credential.response.getTransports();
  return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,response,
   clientExtensionResults:credential.getClientExtensionResults()};
 }
 function message(error){
  if(error.name==='NotAllowedError')return '已取消或驗證逾時，請重試；也可以使用密碼登入。';
  if(error.name==='InvalidStateError')return '此裝置已有這個帳號的通行密鑰。';
  if(error.name==='SecurityError')return '網址或 HTTPS 設定不符合通行密鑰要求。';
  return error.message||'此裝置無法完成通行密鑰驗證。';
 }
 async function checkSupport(){
  if(!window.isSecureContext||!window.PublicKeyCredential||!navigator.credentials){
   status.textContent='通行密鑰需要支援的瀏覽器與 HTTPS；仍可使用密碼登入。';return;
  }
  try{
   const response=await fetch(API+'/passkeys/status');if(!response.ok)throw Error('無法取得設定');
   const info=await response.json();
   available=info.enabled&&info.origin===window.location.origin;
   loginButton.disabled=!available;
   status.textContent=available?'確認帳號後，即可使用 Face ID、指紋或裝置解鎖登入。':'管理員尚未啟用通行密鑰，或目前網址與設定不符。';
  }catch{status.textContent='暫時無法使用通行密鑰，請使用密碼登入。';}
 }
 loginButton.onclick=async()=>{
  if(!available||busy)return;
  const username=$('#login-form input[name="username"]').value;
  if(!username){$('#login-error').textContent='請先輸入帳號。';return;}
  busy=true;loginButton.disabled=true;$('#login-error').textContent='';
  const passwordButton=$('#login-form button[type="submit"]');passwordButton.disabled=true;
  try{
   const pending=await api('/passkeys/login/options','POST',{username});
   const credential=await navigator.credentials.get({publicKey:optionsFromJSON(pending.options)});
   if(!credential)throw Error('未取得通行密鑰');
   const result=await api('/passkeys/login/verify','POST',{challenge_id:pending.challenge_id,credential:credentialJSON(credential)});
   token=result.access_token;sessionStorage.setItem('mplayer-token',token);$('#login-form').reset();await enter();
  }catch(error){$('#login-error').textContent=message(error);}
  finally{busy=false;loginButton.disabled=!available;passwordButton.disabled=false;}
 };
 $('#manage-passkeys').onclick=run(async()=>{
  const keys=await api('/passkeys');
  dialog('Face ID／通行密鑰',`<p>通行密鑰可使用 Face ID、Touch ID、Windows Hello 或裝置解鎖；網站不會取得你的生物辨識資料。</p><div id="passkey-list">${keys.map(key=>`<div class="passkey-row"><span>${escapeHTML(key.name)}</span><button type="button" data-remove-key="${key.id}">移除</button></div>`).join('')||'<p class="muted">尚未綁定通行密鑰。</p>'}</div>${available?textField('新通行密鑰名稱','name','我的通行密鑰')+textField('目前密碼（確認身分）','password','','password'):'<p class="muted">目前無法新增，請使用已設定的 HTTPS 網址與支援的瀏覽器。</p>'}`,async data=>{
   if(!available)return;
   const pending=await api('/passkeys/register/options','POST',{name:data.get('name'),password:data.get('password')});
   // Clear the password before showing the operating system's biometric dialog.
   $('#dialog-content input[name="password"]').value='';
   const close=$('#dialog-close'),cancel=$('#dialog-cancel');
   const preventClose=event=>event.preventDefault();
   close.disabled=cancel.disabled=true;$('#dialog').addEventListener('cancel',preventClose);
   try{
    const credential=await navigator.credentials.create({publicKey:optionsFromJSON(pending.options)});
    if(!credential)throw Error('未取得通行密鑰');
    await api('/passkeys/register/verify','POST',{challenge_id:pending.challenge_id,credential:credentialJSON(credential)});
    toast('通行密鑰已綁定，下次可使用 Face ID 或裝置解鎖登入。');
   }catch(error){throw Error(message(error));}
   finally{close.disabled=cancel.disabled=false;$('#dialog').removeEventListener('cancel',preventClose);}
  },available?'新增通行密鑰':'關閉');
  $('#passkey-list').onclick=run(async event=>{
   const button=event.target.closest('[data-remove-key]');if(!button)return;
   if(button.dataset.confirm!=='yes'){button.dataset.confirm='yes';button.textContent='確認移除';return;}
   button.disabled=true;
   try{await api('/passkeys/'+button.dataset.removeKey,'DELETE');button.closest('.passkey-row').remove();toast('已移除通行密鑰，密碼登入仍可使用。');}
   catch(error){button.disabled=false;throw error;}
  });
 });
 checkSupport();
})();
