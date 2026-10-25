const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(role='admin'){
 const elements=new Map(),calls=[],timers=[],events={};
 const $=s=>{if(!elements.has(s))elements.set(s,{checked:false,disabled:false,value:'',hidden:false,textContent:''});return elements.get(s);};
 let prefs={enabled:false,target:-16,peak:-2};
 const context=vm.createContext({$,token:'test',me:{role},tracks:[],document:{hidden:false,querySelectorAll:()=>[]},
  window:{addEventListener:(name,fn)=>events[name]=fn},setTimeout:fn=>(timers.push(fn),timers.length),clearTimeout(){},
  run:fn=>fn,api:async(url,method='GET',data)=>{
   calls.push({url,method,data});
   if(url==='/normalization'){if(method==='PUT')prefs={...data};return prefs;}
   return {running:false,auto_analyze:false,stopping:false,total:1,completed:1,failed:0,current:null};
  }});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/normalization.js'),'utf8'),context);
 return {$,context,calls,timers,events};
}
test('personal controls load defaults and save numerical choices',async()=>{
 const s=setup('user');
 await s.context.window.loadNormalizationSettings();
 assert.equal(s.$('#normalization-enabled').checked,false);
 assert.equal(s.$('#normalization-target').value,'-16');
 assert.equal(s.$('#normalization-target').disabled,true);
 assert.equal(s.calls.some(c=>c.url.endsWith('/library')),false);
 s.$('#normalization-enabled').checked=true;
 s.$('#normalization-target').value='-18';
 s.$('#normalization-peak').value='-3';
 await s.$('#normalization-enabled').onchange();
 const save=s.calls.find(c=>c.method==='PUT');
 assert.equal(save.data.target,-18);assert.equal(save.data.peak,-3);
 assert.equal(s.$('#normalization-target').disabled,false);
});
test('administrator analysis controls use restricted endpoints',async()=>{
 const s=setup();await s.context.window.loadNormalizationSettings();
 assert.equal(s.$('#normalization-analyze').disabled,false);
 assert.equal(s.$('#normalization-stop').disabled,true);
 await s.$('#normalization-analyze').onclick();
 await s.$('#normalization-stop').onclick();
 assert.ok(s.calls.some(c=>c.url==='/normalization/library'&&c.method==='POST'));
 assert.ok(s.calls.some(c=>c.url==='/normalization/library'&&c.method==='DELETE'));
});
test('library polling only refreshes when revision changes and skips hidden views',async()=>{
 const s=setup('user');let revision='10',refreshes=0;
 s.context.libraryRevision='10';
 s.context.api=async url=>{s.calls.push({url});return {revision};};
 s.context.refresh=async background=>{assert.equal(background,true);refreshes++;s.context.libraryRevision=revision;};
 await s.timers[0]();
 assert.equal(refreshes,0);
 assert.equal(s.calls.at(-1).url,'/library/revision');
 revision='11';await s.timers.at(-1)();assert.equal(refreshes,1);
 await s.timers.at(-1)();assert.equal(refreshes,1);
 const count=s.calls.length;s.context.document.hidden=true;
 await s.timers.at(-1)();assert.equal(s.calls.length,count);
 s.context.document.hidden=false;s.$('#library-view').hidden=true;
 await s.timers.at(-1)();assert.equal(s.calls.length,count);
});
test('failed refresh retries without accepting revision; logout ignores in-flight result',async()=>{
 const s=setup();s.context.libraryRevision='1';let refreshes=0;
 s.context.api=async()=>({revision:'2'});
 s.context.refresh=async()=>{refreshes++;throw Error('offline');};
 await s.timers[0]();await s.timers.at(-1)();assert.equal(refreshes,2);
 s.context.api=async()=>{s.context.token=null;return {revision:'2'};};
 await s.timers.at(-1)();assert.equal(refreshes,2);
});
test('background refresh preserves selection, refreshes rows and rejects stale responses',async()=>{
 const s=setup();Object.assign(s.context,{libraryRefreshGeneration:0,libraryRevision:null,folderId:1,
  selected:new Set([1,2]),render:()=>{},folders:[]});
 const source=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
 vm.runInContext(source.slice(source.indexOf('async function refresh('),source.indexOf('function showView(')),s.context);
 s.context.api=async url=>url==='/library/revision'?{revision:'3'}:url==='/folders'?[{id:1}]:[{id:1,analyzed:true}];
 await s.context.refresh(true);
 assert.equal(s.context.libraryRevision,'3');assert.deepEqual([...s.context.selected],[1]);
 assert.equal(s.context.tracks[0].analyzed,true);
 await s.context.refresh();assert.equal(s.context.selected.size,0);
 let resolve;s.context.api=()=>new Promise(r=>resolve=r);
 const pending=s.context.refresh(true);s.context.libraryRefreshGeneration++;
 s.context.api=async()=>[];resolve({revision:'4'});await pending;
 assert.equal(s.context.libraryRevision,'3');
});
