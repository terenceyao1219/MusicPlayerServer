const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/sidebar.js'),'utf8');
function setup(saved=null,blocked=false){
 const handlers={},attrs={},storage=new Map(saved===null?[]:[['mplayer:sidebar-width',saved]]);
 const handle={setAttribute(k,v){attrs[k]=v;},addEventListener(k,v){handlers[k]=v;},setPointerCapture(){}};
 const root={style:{setProperty(){}}},win={innerWidth:1200,addEventListener(k,v){handlers[k]=v;}};
 vm.runInNewContext(source,{window:win,document:{querySelector(){return handle;},documentElement:root,body:{classList:{add(){},remove(){}}}},localStorage:{getItem(k){if(blocked)throw Error();return storage.get(k)??null;},setItem(k,v){if(blocked)throw Error();storage.set(k,v);}}});
 return {handlers,attrs,storage,win};
}
test('drag and keyboard resizing are bounded and persist; double click resets',()=>{
 const {handlers,attrs,storage}=setup();
 handlers.pointerdown({button:0,pointerId:1,preventDefault(){}});
 handlers.pointermove({clientX:900});handlers.pointerup();
 assert.equal(attrs['aria-valuenow'],'420');assert.equal(storage.get('mplayer:sidebar-width'),'420');
 handlers.keydown({key:'Home',preventDefault(){}});assert.equal(attrs['aria-valuenow'],'180');
 handlers.dblclick();assert.equal(attrs['aria-valuenow'],'240');
});
test('bad or blocked storage is harmless; mobile cannot drag the sidebar',()=>{
 const a=setup('not a number');assert.equal(a.attrs['aria-valuenow'],'240');
 const b=setup(null,true);b.handlers.dblclick();assert.equal(b.attrs['aria-valuenow'],'240');
 b.win.innerWidth=390;b.handlers.resize();
 b.handlers.pointerdown({button:0,pointerId:1,preventDefault(){}});b.handlers.pointermove({clientX:400});
 assert.equal(b.attrs['aria-valuenow'],'180');
});
