const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/app.js'),'utf8');
function player(storage=new Map(),user=1){
 const elements=new Map(),calls=[],events={};
 function element(selector){if(!elements.has(selector))elements.set(selector,{value:'',textContent:'',hidden:false,currentTime:0,duration:200,paused:true,src:'',classList:{toggle(){}},setAttribute(){},querySelector(){return element('child');},querySelectorAll(){return [];},hasAttribute(name){return !!this[name];},removeAttribute(name){this[name]='';},load(){},pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();},close(){},addEventListener(){}});return elements.get(selector);}
 const context=vm.createContext({document:{querySelector:element,querySelectorAll(){return [];},body:element('body')},window:{addEventListener(name,fn){events[name]=fn;}},sessionStorage:{getItem(){return null;},removeItem(){}},localStorage:{getItem(k){return storage.get(k)??null;},setItem(k,v){storage.set(k,v);}},fetch:async()=>({ok:true,json:async()=>({version:'test'})}),FormData:class{},setTimeout(){},clearTimeout(){},console});
 vm.runInContext(source,context);
 context.calls=calls;
 vm.runInContext(`me={id:${user},role:'admin'};token='test';api=async(path,method,data)=>{calls.push({path,method,data});if(path==='/playback/resolve')return {track_id:data.track_id+1};if(data===null||typeof data!=='object'||Array.isArray(data)||data.schema_version===2){const e=Error('invalid');e.status=400;throw e;}return {state:{schema_version:1,track_id:null,position:0,mode:'folder',...data},reason:null};};`,context);
 return {context,calls,events,audio:element('#audio'),run:code=>vm.runInContext(code,context)};
}
test('shared localStorage never switches another window; last save wins',async()=>{
 const storage=new Map(),a=player(storage),b=player(storage);
 await a.run('choose(10)');a.audio.onloadedmetadata();a.audio.currentTime=12;
 await b.run('choose(20)');b.audio.onloadedmetadata();b.audio.currentTime=34;
 await a.run('persist()');await b.run('persist()');
 assert.equal(JSON.parse(storage.get('mplayer:playback:v1:1')).track_id,20);
 assert.equal(a.audio.src,'/api/v1/mplayer/tracks/10/stream');
 assert.equal(a.run('playback.track_id'),10);
 assert.equal(a.events.storage,undefined);
 const before=a.calls.length;await a.run('persist()');assert.equal(a.calls.length,before);
 assert.equal(JSON.parse(storage.get('mplayer:playback:v1:1')).position,12);
 await a.run('advance(1)');assert.equal(a.audio.src,'/api/v1/mplayer/tracks/11/stream');assert.equal(b.audio.src,'/api/v1/mplayer/tracks/20/stream');
});
test('restore uses account key, clamps seek and does not autoplay',async()=>{
 const storage=new Map([['mplayer:playback:v1:1',JSON.stringify({schema_version:1,track_id:5,position:500,mode:'global'})]]);
 const a=player(storage);await a.run('restoreLocal()');a.audio.onloadedmetadata();
 assert.equal(a.audio.currentTime,200);assert.equal(a.audio.paused,true);
 const b=player(storage,2);await b.run('restoreLocal()');assert.equal(b.run('playback.track_id'),null);
});
test('unusable storage does not prevent playback; malformed states recover',async()=>{
 const a=player();a.context.localStorage.setItem=()=>{throw Error('blocked');};await a.run('choose(7)');assert.equal(a.audio.src,'/api/v1/mplayer/tracks/7/stream');
 for(const value of ['broken','null','{"schema_version":2}']){
 const b=player(new Map([['mplayer:playback:v1:1',value]]));await b.run('restoreLocal()');assert.equal(b.run('playback.track_id'),null);
 }
});
test('mode and pagehide save locally; ended passes explicit trigger',async()=>{
 const a=player();await a.run('choose(4)');a.audio.onloadedmetadata();a.audio.currentTime=22;
 await a.run("$('#mode').onchange({target:{value:'single'}})");assert.equal(a.run('playback.mode'),'single');
 a.events.pagehide();assert.equal(a.run('playback.position'),22);
 await a.audio.onended();assert.equal(a.calls.at(-1).data.trigger,'ended');
});

test('folder-first navigation and song markup are shared across roles',()=>{
 const a=player();a.run("folders=[{id:1,name:'Jazz',track_count:1}];tracks=[{id:10,folder_id:1,folder_name:'Jazz',name:'a.wav'}];openFolder(null)");
 assert.equal(a.run('visibleTracks().length'),0);assert.equal(a.run("$('#folder-list-wrap').hidden"),false);
 a.run('openFolder(1)');assert.equal(a.run('visibleTracks().length'),1);
 const markup=a.run("$('#tracks').innerHTML");a.run("me.role='user';render()");assert.equal(a.run("$('#tracks').innerHTML"),markup);
 a.run('openFolder(null)');assert.equal(a.run("$('#track-list-wrap').hidden"),true);
});
test('random previous retraces history and next restores forward history',async()=>{
 const a=player();await a.run('choose(10)');a.run("playback.mode='random'");
 await a.run('advance(1)');assert.equal(a.run('playback.track_id'),11);
 await a.run('advance(-1)');assert.equal(a.run('playback.track_id'),10);
 await a.run('advance(1)');assert.equal(a.run('playback.track_id'),11);
 assert.equal(a.calls.filter(c=>c.path==='/playback/resolve').length,1);
});

test('cross-folder playback follows the current song for admin and user',async()=>{
 for(const role of ['admin','user']){
  const a=player();a.run(`me.role='${role}';folders=[{id:1,name:'Jazz',track_count:1},{id:2,name:'Piano',track_count:1}];tracks=[{id:10,folder_id:1,folder_name:'Jazz',name:'a.wav'},{id:11,folder_id:2,folder_name:'Piano',name:'b.wav'}];openFolder(1)`);
  await a.run('choose(10)');a.run("selected.add(10);$('#search').value='a';playback.mode='global'");
  await a.run('advance(1)');
  assert.equal(a.run('folderId'),2);assert.equal(a.run('selected.size'),0);assert.equal(a.run("$('#search').value"),'');
  assert.equal(a.run("$('#breadcrumb-current').textContent"),'Piano');
  assert.match(a.run("$('#tracks').innerHTML"),/track-row current/);assert.match(a.run("$('#tracks').innerHTML"),/>b<\/button>/);
  // Browsing alone must not change the song or force the view back.
  a.run('openFolder(1)');assert.equal(a.run('playback.track_id'),11);assert.equal(a.run('folderId'),1);
 }
});

test('display titles omit only the supported final extension without changing filenames',()=>{
 const a=player();assert.equal(a.run("trackTitle('Artist.Live.MP3')"),'Artist.Live');
 a.run("folders=[{id:1,name:'Jazz',track_count:1}];tracks=[{id:10,folder_id:1,folder_name:'Jazz',name:'Artist.Live.MP3'}];playback.track_id=10;openFolder(1)");
 assert.equal(a.run("$('#now-title').textContent"),'Artist.Live');
 assert.equal(a.run('tracks[0].name'),'Artist.Live.MP3');
 assert.match(a.run("$('#tracks').innerHTML"),/>MP3</);
});

test('playback transport selection survives reload independently of account state',async()=>{
 const storage=new Map(),a=player(storage);
 await a.run("$('#transport').onchange({target:{value:'hls'}})");
 assert.equal(storage.get('mplayer:transport:v1'),'hls');
 const b=player(storage);
 assert.equal(b.run('transport'),'hls');assert.equal(b.run("$('#transport').value"),'hls');
 assert.equal(b.run('playback.mode'),'folder');
 await b.run("$('#transport').onchange({target:{value:'direct'}})");
 assert.equal(storage.get('mplayer:transport:v1'),'direct');
 assert.equal(a.run('transport'),'hls');
});

test('restoring state cannot overwrite the saved song before loading finishes',async()=>{
 const stored=JSON.stringify({schema_version:1,track_id:123,position:42,mode:'random'});
 const storage=new Map([['mplayer:playback:v1:1',stored]]),a=player(storage);
 a.run('restoring=true');await a.run('persist()');
 await a.run("$('#transport').onchange({target:{value:'hls'}})");
 assert.equal(storage.get('mplayer:playback:v1:1'),stored);
 assert.equal(a.run('transport'),'direct');
});


test('a failed progress save does not pause media playback',async()=>{
 const a=player();await a.run('choose(10)');a.audio.onloadedmetadata();
 await a.audio.play();
 a.run("persist=async()=>{throw Error('save failed');};saveAt=0;");
 a.audio.ontimeupdate();
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(a.audio.paused,false);
});
