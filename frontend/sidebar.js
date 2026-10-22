(() => {
 const handle=document.querySelector('#sidebar-resizer');
 const root=document.documentElement;
 const key='mplayer:sidebar-width';
 let width=240,dragging=false;
 const desktop=()=>window.innerWidth>700;
 function apply(value,save=false){
  const maximum=Math.min(420,Math.max(180,window.innerWidth-380));
  width=Math.round(Math.min(maximum,Math.max(180,Number.isFinite(value)?value:240)));
  root.style.setProperty('--sidebar-width',`${width}px`);
  handle.setAttribute('aria-valuenow',String(width));
  handle.setAttribute('aria-valuemax',String(maximum));
  if(save){try{localStorage.setItem(key,String(width));}catch{/* Resizing still works without storage. */}}
 }
 try{const saved=localStorage.getItem(key);if(saved!==null)width=Number(saved);}catch{}
 apply(width);
 handle.addEventListener('pointerdown',event=>{
  if(!desktop()||event.button!==0)return;
  event.preventDefault();dragging=true;handle.setPointerCapture(event.pointerId);
  document.body.classList.add('resizing-sidebar');
 });
 handle.addEventListener('pointermove',event=>{if(dragging)apply(event.clientX);});
 function finish(){if(!dragging)return;dragging=false;document.body.classList.remove('resizing-sidebar');apply(width,true);}
 handle.addEventListener('pointerup',finish);
 handle.addEventListener('pointercancel',finish);
 handle.addEventListener('lostpointercapture',finish);
 handle.addEventListener('dblclick',()=>apply(240,true));
 handle.addEventListener('keydown',event=>{
  if(!desktop())return;
  const value={ArrowLeft:width-10,ArrowRight:width+10,Home:180,End:420}[event.key];
  if(value!==undefined){event.preventDefault();apply(value,true);}
 });
 window.addEventListener('resize',()=>{if(!desktop())finish();apply(width);});
})();
