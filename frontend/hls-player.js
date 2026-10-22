/* Native HLS on Safari; locally bundled hls.js on MSE browsers. */
class SonoraHLS {
 constructor(audio, request, update, report){
  this.audio=audio;this.request=request;this.update=update;this.report=report;
  this.generation=0;this.session=null;this.player=null;this.timer=null;this.loading=false;
 }
 static supported(audio){return !!audio.canPlayType('application/vnd.apple.mpegurl') || !!(window.Hls&&Hls.isSupported());}
 static useNative(audio){
  if(!audio.canPlayType('application/vnd.apple.mpegurl'))return false;
  const nav=window.navigator||{},ua=nav.userAgent||'';
  const ios=/iPhone|iPad|iPod/.test(ua)||(nav.platform==='MacIntel'&&nav.maxTouchPoints>1);
  const safari=/Safari\//.test(ua)&&/Apple/.test(nav.vendor||'')&&!/Chrome|Chromium|CriOS|Edg|OPR|FxiOS/.test(ua);
  // Chromium can report native HLS support without exposing seekable ranges.
  // Keep Apple's background media pipeline; prefer MSE everywhere else.
  return ios||safari||!(window.Hls&&Hls.isSupported());
 }
 stop(){
  this.generation++;if(this.cancelStart){this.cancelStart();this.cancelStart=null;}clearTimeout(this.timer);this.timer=null;this.loading=false;
  if(this.player){this.player.destroy();this.player=null;}
  const previous=this.session;this.session=null;
  if(previous)this.request('/hls/sessions/'+previous.id,'DELETE').catch(()=>{});
 }
 async start(state){
  this.stop();const generation=this.generation;
  if(!SonoraHLS.supported(this.audio))throw Error('此瀏覽器不支援 HLS，請切回原始串流。');
  this.loading=true;this.warned=false;this.started=false;this.pollWarned=false;
  try{
   let session=await this.request('/hls/sessions','POST',state);
   if(generation!==this.generation){this.request('/hls/sessions/'+session.id,'DELETE').catch(()=>{});return;}
   this.session=session;
   // Encoding is asynchronous; this loop is only used before playback starts.
   while(!session.ready){
    if(session.error)throw Error(session.error);
    await new Promise(resolve=>setTimeout(resolve,600));
    if(generation!==this.generation)return;
    session=await this.request('/hls/sessions/'+session.id);
    if(generation!==this.generation)return;
    this.session=session;
   }
   const audio=this.audio;
   audio.preload='auto';
   this.waitForStart(session.position,generation);
   // Prefer Safari's native media pipeline for background playback.
   if(SonoraHLS.useNative(audio)){audio.src=session.playlist_url;audio.load();}
   else{
    this.player=new Hls({enableWorker:false,startPosition:session.position,maxBufferLength:30});
    this.player.on(Hls.Events.ERROR,(_event,data)=>{if(data.fatal){if(this.cancelStart){this.cancelStart();this.cancelStart=null;}this.loading=false;this.report('HLS 播放中斷，請按播放重試或切回原始串流。');this.failed=true;}});
    this.player.loadSource(session.playlist_url);this.player.attachMedia(audio);
   }
   this.failed=false;this.poll(generation);
  }catch(error){if(generation===this.generation){this.stop();throw error;}}
 }
 // Native HLS may expose metadata before a seekable range exists. Never
 // interpret its default live-edge position as the song the user selected.
 waitForStart(position,generation){
  const audio=this.audio,target=position,tolerance=.35;
  let timer=null,finished=false,playPending=false,attempts=0;
  const events=['loadeddata','canplay','progress','durationchange','seeked','playing'];
  const cleanup=()=>{clearTimeout(timer);for(const event of events)audio.removeEventListener(event,check);};
  const finish=()=>{if(finished)return;finished=true;cleanup();this.cancelStart=null;this.loading=false;this.started=true;this.update(this.current());};
  const fail=()=>{finished=true;cleanup();this.cancelStart=null;audio.pause();this.loading=false;this.failed=true;this.report('HLS 尚未能定位到所選歌曲，請按播放重試或切回原始串流。');};
  const inRange=()=>{
   for(let i=0;i<audio.seekable.length;i++)if(audio.seekable.start(i)<=target+.05&&audio.seekable.end(i)>target)return true;
   return false;
  };
  const check=()=>{
   if(finished||generation!==this.generation)return;
   if(audio.readyState<2||!inRange()||audio.seeking)return;
   if(Math.abs(audio.currentTime-target)>tolerance){
    if(!audio.paused)audio.pause();
    try{audio.currentTime=target;}catch{}
    return;
   }
   if(!audio.paused){finish();return;}
   if(playPending)return;
   playPending=true;
   audio.play().then(()=>{
    playPending=false;
    if(finished||generation!==this.generation)return;
    // Safari can choose the live edge as playback begins; verify again.
    if(!audio.seeking&&Math.abs(audio.currentTime-target)<=tolerance)finish();
    else check();
   }).catch(()=>{
    playPending=false;if(finished||generation!==this.generation)return;
    finish();this.report('HLS 已定位，請按播放按鈕開始聆聽。');
   });
  };
  const retry=()=>{
   if(finished||generation!==this.generation)return;
   if(++attempts>100){fail();return;}
   check();if(!finished)timer=setTimeout(retry,200);
  };
  this.cancelStart=()=>{finished=true;cleanup();audio.onloadedmetadata=null;};
  audio.onloadedmetadata=()=>{ /* Wait for loadeddata/canplay, not metadata alone. */ };
  for(const event of events)audio.addEventListener(event,check);
  timer=setTimeout(retry,200);
 }
 current(allowFailed=false){
  if(!this.session||this.loading||(this.failed&&!(allowFailed&&this.started)))return null;
  const time=this.audio.currentTime;
  const entry=this.session.entries.find(e=>time>=e.start-.001&&time<e.start+e.duration-.001);
  if(!entry)return null;
  return {entry,track_id:entry.track_id,position:Math.max(0,time-entry.start),duration:entry.duration};
 }
 seek(position){const current=this.current();if(current)this.audio.currentTime=current.entry.start+Math.max(0,Math.min(position,current.duration-.05));}
 step(direction){
  const current=this.current();if(!current)return false;
  const next=this.session.entries.find(e=>e.index===current.entry.index+direction);
  if(!next)return false;
  this.audio.currentTime=next.start;this.update(this.current());this.audio.play().catch(()=>this.report('請按播放按鈕繼續聆聽。'));return true;
 }
 async refresh(){
  const session=this.session,generation=this.generation;if(!session)return;
  const info=await this.request('/hls/sessions/'+session.id);
  if(generation!==this.generation)return;
  this.session=info;this.update(this.current());
  if(info.error&&!this.warned){this.warned=true;this.report(info.error);}
 }
 async resumePoint(){
  // Refresh the timeline after background playback before rebuilding a failed stream.
  try{await this.refresh();}catch{}
  return this.current(true);
 }
 poll(generation){
  this.timer=setTimeout(async()=>{
   if(generation!==this.generation)return;
   try{await this.refresh();this.pollWarned=false;}catch(error){
    // Metadata polling is separate from native media playback. A temporary
    // network failure must not permanently stop polling or position tracking.
    if(generation===this.generation&&!this.pollWarned){this.pollWarned=true;this.report('HLS 狀態暫時無法更新，將自動重試。');}
   }
   if(generation===this.generation)this.poll(generation);
  },4000);
 }
}
