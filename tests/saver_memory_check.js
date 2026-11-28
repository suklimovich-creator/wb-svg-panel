// Resource lifecycle regression, no browser/device required.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = process.env.IDLE_SOURCE || 'static/js/idle.js';
let id=0, frame=0, made=[], current, live=new Set();
function image(){
  const attrs={};
  return {style:{},onload:null,onerror:null,parentNode:parent,
    set src(v){attrs.src=v;}, get src(){return attrs.src;},
    getAttribute:n=>attrs[n]||null,removeAttribute:n=>{delete attrs[n];}};
}
const parent={replaceChild(next,old){assert.strictEqual(old,current);current=next;old.parentNode=null;},removeChild(){}};
current=image();
const hint={textContent:''};
const element={style:{},querySelector(sel){if(sel==='.sv-panel')return current;if(sel==='.sv-hint')return hint;return null;}};
const noop=()=>{};
const context={console,Promise,Date,Blob:class{},URL:{createObjectURL(){let u='blob:'+ ++id;live.add(u);return u;},revokeObjectURL:u=>live.delete(u)},
 document:{addEventListener:noop,createElement(){let i=image();made.push(i);return i;}},window:{addEventListener:noop},
 setTimeout:()=>1,clearTimeout:noop,setInterval:noop,
 CONTROLS:{saver_panel:'test',saver_img:'background',saver_clock:false},COLS:2,THEME:'',LITE:true,panelIncompatible:false,
 checkApp:noop,completeSvg:s=>s,parseSvg:()=>({}),
 fetch:()=>Promise.resolve({ok:true,status:200,headers:{get:()=>null},text:()=>Promise.resolve('<svg xmlns="http://www.w3.org/2000/svg"><text>'+ ++frame +'</text></svg>')}),
 refresh:noop,SHIELD_MS:1};
vm.createContext(context);vm.runInContext(fs.readFileSync(source,'utf8'),context);
context.saverOn=true;context.saverEl=element;
context.saverCamsFrom=noop;context.saverCamLayout=noop;context.saverMarkup=noop;
async function start(){context.saverPanel();await new Promise(setImmediate);return made[made.length-1];}
(async()=>{
 for(let i=0;i<300;i++){
   const old=current, next=await start();assert(next.onload);context.saverSeq++;next.onload();
   assert.strictEqual(next.onload,null,'loaded frame retains its callback and previous frame');
   assert.strictEqual(next.onerror,null,'loaded frame retains SVG through error callback');
   assert.strictEqual(old.getAttribute('src'),null,'old decoded image is still referenced');
   assert.equal(live.size,1,'blob URLs accumulate');
 }
 const good=current;const bad=await start();bad.onerror();
 assert.strictEqual(current,good);assert.equal(live.size,1);assert.strictEqual(bad.onload,null);
 const pending=await start();assert.equal(live.size,2);context.saverHide();
 assert.strictEqual(pending.onload,null);assert.strictEqual(pending.onerror,null);assert.equal(live.size,1);
 assert.strictEqual(pending.getAttribute('src'),null);
 context.window.navigator={userAgent:'Mozilla/5.0 (Android) Firefox/68.0'};
 context.saverOn=true;
 const retained=live.size;let inline=false;
 context.saverMarkup=svg=>{inline=svg.includes('<text>');return true;};
 await start();assert(inline);assert.equal(live.size,retained);
 console.log('OK: 300 frames; callbacks detached; one retained URL; failed frame preserved; pending image cancelled on hide');
})().catch(e=>{console.error(e.message);process.exitCode=1;});
