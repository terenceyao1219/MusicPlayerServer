const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/hls-player.js'),'utf8');
function engine(){
 const listeners=new Map();
 const audio={currentTime:0,paused:true,seeking:false,readyState:0,seekable:{length:0,start(){return 0;},end(){return 70;}},canPlayType(){return 'maybe';},play(){this.paused=false;return Promise.resolve();},pause(){this.paused=true;},load(){},src:'',
 addEventListener(event,fn){if(!listeners.has(event))listeners.set(event,new Set());listeners.get(event).add(fn);},removeEventListener(event,fn){listeners.get(event)?.delete(fn);},emit(event){for(const fn of [...(listeners.get(event)||[])])fn();},ready(){this.readyState=3;this.seekable.length=1;this.emit('canplay');}};
 const calls=[],updates=[],errors=[];
 const context=vm.createContext({window:{},setTimeout(){},clearTimeout(){},console});
 vm.runInContext(source+';this.Engine=SonoraHLS;',context);
 const player=new context.Engine(audio,async(path,method,state)=>{calls.push({path,method,state});return {id:'s1',ready:true,position:state?.position||0,playlist_url:'/tracks/hls/s1/index.m3u8',entries:[{index:0,track_id:10,start:0,duration:20},{index:1,track_id:11,start:20,duration:30},{index:2,track_id:10,start:50,duration:20}]};},value=>updates.push(value),error=>errors.push(error));
 return {audio,player,calls,updates,errors};
}
test('HLS restores song offset and maps continuous time to individual songs',async()=>{
 const {audio,player,updates}=engine();
 await player.start({track_id:10,position:7,mode:'folder'});
 assert.equal(player.current(),null);
 audio.onloadedmetadata();assert.equal(audio.currentTime,0);
 audio.ready();assert.equal(audio.currentTime,7);audio.emit("seeked");await Promise.resolve();assert.equal(player.loading,false);
 audio.currentTime=25;assert.equal(player.current().track_id,11);assert.equal(player.current().position,5);
 player.seek(15);assert.equal(audio.currentTime,35);
 assert.equal(player.step(1),true);assert.equal(audio.currentTime,50);
 assert.equal(updates.at(-1).track_id,10);
 assert.equal(player.step(1),false);
});
test('HLS stop retires only its own session and invalidates pending callbacks',async()=>{
 const {player,calls,audio}=engine();await player.start({track_id:10,position:0});
 const metadata=audio.onloadedmetadata;player.stop();metadata();
 assert.equal(player.session,null);assert.equal(player.loading,false);
 assert.equal(calls.at(-1).path,'/hls/sessions/s1');assert.equal(calls.at(-1).method,'DELETE');
});


test('Safari live-edge startup is corrected only after the target becomes seekable',async()=>{
 const {audio,player,updates}=engine();
 await player.start({track_id:10,position:0});
 audio.currentTime=55;audio.onloadedmetadata();
 assert.equal(audio.currentTime,55);assert.equal(player.current(),null);assert.equal(audio.paused,true);
 audio.ready();assert.equal(audio.currentTime,0);assert.equal(updates.length,0);
 audio.seeking=true;audio.emit('canplay');assert.equal(audio.paused,true);
 audio.seeking=false;audio.emit('seeked');await Promise.resolve();
 assert.equal(player.loading,false);assert.equal(updates.at(-1).track_id,10);assert.equal(updates.at(-1).position,0);
});

test('late callbacks from a replaced selection cannot start the old song',async()=>{
 const {audio,player,updates}=engine();
 await player.start({track_id:10,position:7});
 player.stop();audio.ready();audio.emit('seeked');await Promise.resolve();
 assert.equal(audio.paused,true);assert.equal(updates.length,0);
});

test('a seekable range at the live edge cannot be mistaken for the selected song',async()=>{
 const {audio,player,updates}=engine();await player.start({track_id:10,position:0});
 audio.currentTime=55;audio.seekable.start=()=>50;audio.ready();
 assert.equal(audio.paused,true);assert.equal(player.loading,true);assert.equal(updates.length,0);
 audio.seekable.start=()=>0;audio.emit('progress');audio.emit('seeked');await Promise.resolve();
 assert.equal(updates.at(-1).track_id,10);
});


test('failed startup never saves the browser fallback song as the selected track',async()=>{
 const {audio,player}=engine();await player.start({track_id:10,position:0});
 audio.currentTime=55;player.loading=false;player.failed=true;
 assert.equal(player.current(),null);
});
