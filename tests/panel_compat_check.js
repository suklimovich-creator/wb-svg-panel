const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('static/js/update.js','utf8');
const c={APP:'1.88.1',CLIENT:'page-a',PROTOCOL:'1',appReload:false,panelIncompatible:false,connectionReason:'',connectionState(){this.blocked=this.panelIncompatible;}};
vm.createContext(c);vm.runInContext(source.slice(source.indexOf('function checkApp('),source.indexOf('function noteVersion(')),c);
function check(headers){c.checkApp({headers:{get:n=>headers[n]||null}});}
check({'X-Panel-App':'1.89.0','X-Panel-Client':'page-a','X-Panel-Protocol':'1'});
assert(!c.appReload&&!c.panelIncompatible,'editor-only release must keep running');
check({'X-Panel-App':'1.89.1','X-Panel-Client':'page-b','X-Panel-Protocol':'1'});
assert(c.appReload&&!c.panelIncompatible,'compatible page upgrade must keep running');
assert.throws(()=>check({'X-Panel-Protocol':'2'}));assert(c.panelIncompatible);
check({'X-Panel-App':'1.88.1','X-Panel-Client':'page-a','X-Panel-Protocol':'1'});
assert(!c.panelIncompatible&&!c.appReload,'restart of the same version');
assert.throws(()=>check({'X-Panel-App':'1.80.0'}));
assert(!/if\s*\([^\n]*\bappReload\b/.test(source.slice(source.indexOf('function refresh('),source.indexOf('function restartPoll('))));
console.log('OK: editor release, compatible assets, incompatible protocol, restart, legacy server');
