// Focused browser regression: run tools/visual/serve.py on PORT (default 8899).
const {chromium} = require('playwright');
const assert = require('assert');
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args:['--no-sandbox']});
  const ctx = await browser.newContext({viewport:{width:390,height:844}});
  const p = await ctx.newPage();
  const errors=[]; p.on('pageerror', e=>errors.push(e.message));
  let fault='', documents=0;
  p.on('request',r=>{if(r.isNavigationRequest()) documents++;});
  await p.route('**/*.svg?*', async route=>{
    if(fault==='offline') return route.abort();
    if(fault==='broken') return route.fulfill({status:200,contentType:'image/svg+xml',body:'<svg><g data-main="1">'});
    if(fault==='auth') return route.fulfill({status:401,body:'Unauthorized'});
    const response=await route.fetch();
    if(fault==='version') return route.fulfill({response,headers:{...response.headers(),'x-panel-app':'99.0.0'}});
    return route.fulfill({response});
  });
  const base='http://127.0.0.1:'+(process.env.PORT||8899)+'/';
  try {
    await p.goto(base+'acc.html');
    await p.waitForSelector('#host svg');
    const key=await p.locator('#host [data-section]').last().getAttribute('data-section');
    await p.evaluate(key=>{localStorage.setItem(FOLD_KEY+':open',key);localStorage.setItem('wb-panel-cols','2');localStorage.setItem('wb-panel-theme','light');},key);
    const requests=[]; p.on('request',r=>{if(r.url().includes('/acc.svg'))requests.push(r.url());});
    await p.reload(); await p.waitForSelector('#host svg');
    assert.equal(new URL(requests[0]).searchParams.get('open'),key);
    assert.equal(new URL(requests[0]).searchParams.get('theme'),'light');
    assert.equal(new URL(requests[0]).searchParams.get('cols'),'2');
    await p.evaluate(()=>{clearTimeout(pollTimer);window.originalRoot=host.querySelector('svg');window.token=42;});
    await p.locator('body').dblclick({position:{x:5,y:5}});
    await p.waitForFunction(()=>!!document.fullscreenElement);
    const navs=documents;
    for(const f of ['offline','broken','auth']) {
      fault=f; await p.evaluate(()=>refresh(true));
      await p.waitForFunction(()=>panelBlocked);
      assert(await p.evaluate(()=>host.querySelector('svg')===originalRoot && token===42));
      assert(await p.evaluate(()=>pub('test','1').then(()=>false,()=>true)));
      if(f==='auth') assert((await p.locator('#connection').innerText()).includes('Требуется вход'));
      fault=''; await p.evaluate(()=>refresh(true)); await p.waitForFunction(()=>!panelBlocked);
    }
    assert.equal(documents,navs);
    assert(await p.evaluate(()=>!!document.fullscreenElement));
    assert(await p.evaluate(async()=>{
      try { await panelRequest('data:text/plain,ok', {}, ()=>new Promise(()=>{}), 20); return false; }
      catch(e) { return e.message === 'Время ожидания истекло'; }
    }));
    // A queued command must not escape after reconnect.
    assert(await p.evaluate(async()=>{
      let sent=0;const sender=liveSender(()=>{sent++;return Promise.resolve();});
      const promise=sender.end().catch(()=>{});
      connectionError(null,++reqSeq);connectionState('',++reqSeq);
      await promise;return sent===0;
    }));
    // Preserve the last complete screensaver on a failed refresh.
    await p.evaluate(()=>{CONTROLS.saver_panel='acc';saverShow();});
    await p.waitForFunction(()=>saverEl.querySelector('.sv-panel').getAttribute('src'));
    const src=await p.locator('.sv-panel').getAttribute('src');
    fault='broken';await p.evaluate(()=>saverPanel());
    await p.waitForFunction(()=>!!SAVER_ERR);
    assert.equal(await p.locator('.sv-panel').getAttribute('src'),src);
    assert(await p.locator('.sv-panel').isVisible());
    fault='';await p.evaluate(()=>saverHide());
    // Server version changes must not reload, including overlay close.
    fault='version';await p.evaluate(()=>refresh(true));
    await p.waitForFunction(()=>appReload);
    await p.evaluate(()=>closeOv());
    assert.equal(documents,navs);
    assert(await p.locator('#connection button').isVisible());
    assert(await p.evaluate(()=>token===42));
    assert.deepEqual(errors,[]);
    console.log('ok: saved initial layout; disconnect/reconnect; truncated SVG; auth; no command replay; screensaver retention; version without reload');
  } finally {await p.unrouteAll({behavior:'ignoreErrors'});await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
