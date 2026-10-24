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
test('analysis icon refresh switches dim and lit states with accessible descriptions',async()=>{
 const s=setup('user'),states=[];
 const icon={dataset:{analysis:'1'},classList:{toggle:(name,on)=>states.push([name,on])},setAttribute:(name,value)=>states.push([name,value])};
 s.context.document.querySelectorAll=()=>[icon];
 s.context.api=async()=>[{id:1,analyzed:true}];
 s.context.tracks.push({id:1});
 await s.timers[0]();
 assert.equal(s.context.tracks[0].analyzed,true);
 assert.equal(icon.title,'音量分析完成');
 assert.ok(states.some(([name,on])=>name==='analyzed'&&on===true));
 s.context.api=async()=>[{id:1,analyzed:false}];
 await s.timers.at(-1)();
 assert.equal(icon.title,'尚未分析音量');
 assert.ok(states.some(([name,on])=>name==='analyzed'&&on===false));
});
