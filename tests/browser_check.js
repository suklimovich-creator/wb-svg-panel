// Проверка страницы в настоящем браузере (с 1.46): сам поднимает тестовый
// сервер (tools/visual/serve.py) на выдуманных приборах и проходит то, чем
// пользуются руками: страницы без ошибок JS, окна, пульт фанкойла,
// выключатель, меню шестерёнки, окно «Сегодня», обновление без головы,
// «облегчённо», замер ?perf=1.
//
//   npm i --no-save playwright && node tests/browser_check.js
//
// Браузер - тот, что знает Playwright, или путь в переменной CHROMIUM.
// Порт сервера - переменная PORT (по умолчанию 8899).
const {chromium} = require('playwright');
const {spawn} = require('child_process');
const path = require('path');
const http = require('http');

const ROOT = path.join(__dirname, '..');
const PORT = +(process.env.PORT || 8899);
const BASE = 'http://127.0.0.1:' + PORT + '/';
let failed = 0;

function ok(name, cond, extra) {
  if (!cond) failed++;
  console.log((cond ? 'ok   ' : 'FAIL ') + name + (extra ? '  ' + extra : ''));
}
function get(url) {
  return new Promise(res => {
    http.get(url, r => { let b = ''; r.on('data', d => b += d); r.on('end', () => res({status: r.statusCode, body: b})); })
      .on('error', () => res({status: 0, body: ''}));
  });
}
async function waitServer() {
  for (let i = 0; i < 60; i++) {
    const r = await get(BASE + 'healthz');
    if (r.status === 200) return true;
    await new Promise(r => setTimeout(r, 250));
  }
  return false;
}
async function log() { return JSON.parse((await get(BASE + '_log')).body || '[]'); }

(async () => {
  const srv = spawn('python3', [path.join(ROOT, 'tools/visual/serve.py'), String(PORT)],
                    {cwd: ROOT, stdio: ['ignore', 'ignore', 'pipe']});
  let srvErr = '';
  srv.stderr.on('data', d => { srvErr += d; });
  if (!(await waitServer())) { console.log('сервер не поднялся\n' + srvErr.slice(-2000)); process.exit(1); }

  const opts = {args: ['--no-sandbox']};
  if (process.env.CHROMIUM) opts.executablePath = process.env.CHROMIUM;
  const br = await chromium.launch(opts);
  async function page(url, setup) {
    const ctx = await br.newContext({viewport: {width: 390, height: 844}});
    const p = await ctx.newPage();
    p.errors = [];
    p.on('pageerror', e => p.errors.push(e.message));
    p.on('console', m => { if (m.type() === 'error') p.errors.push(m.text()); });
    if (setup) await p.addInitScript(setup);
    await p.goto(BASE + url);
    await p.waitForTimeout(1500);
    return p;
  }
  async function tap(p, sel) {
    const el = await p.$(sel);
    if (!el) return false;
    const b = await el.boundingBox();
    await p.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
    return true;
  }

  try {
    // 1. страницы и темы - без ошибок JS
    for (const u of ['gal.html', 'misc.html', 'obj.html', 'day.html', 'tint.html', 'fcs.html',
                     'clim.html', 'acc.html', '_demo.html', 'themes', 'check', 'docs',
                     'gal.html?theme=glass', 'gal.html?theme=noir-dark', 'gal.html?theme=neumo',
                     'gal.html?theme=glass-day']) {
      const p = await page(u);
      ok('страница ' + u, p.errors.length === 0, p.errors.slice(0, 2).join(' | '));
      await p.context().close();
    }

    // 2. окна и пульты всех видов открываются и закрываются
    for (const u of ['obj.html', 'misc.html', 'gal.html', 'clim.html']) {
      const p = await page(u);
      const kinds = await p.evaluate(() => {
        const seen = {}, out = [];
        document.querySelectorAll('#host [data-pad]').forEach((n, i) => {
          const k = n.getAttribute('data-pad'); if (!seen[k]) { seen[k] = 1; out.push([k, i]); }
        });
        return out;
      });
      for (const [k, i] of kinds) {
        await p.evaluate(i => openPad(document.querySelectorAll('#host [data-pad]')[i]), i);
        await p.waitForTimeout(500);
        const open = await p.evaluate(() => document.getElementById('ov').classList.contains('open'));
        await p.evaluate(() => closeOv());
        await p.waitForTimeout(300);
        // камеры в тестовом наборе нет - её кадр отвечает ошибкой, это не сбой страницы
        const errs = p.errors.filter(e => !/Failed to load resource/.test(e));
        ok('окно ' + k + ' (' + u + ')', open && errs.length === 0, errs.slice(0, 1).join(''));
      }
      await p.context().close();
    }

    // 3. выключатель: нажатие уходит в брокер, плитка переключается
    {
      const p = await page('misc.html');
      const before = await p.evaluate(() => document.querySelector('[data-kind="switch"]').getAttribute('data-state'));
      await tap(p, '[data-kind="switch"]');
      await p.waitForTimeout(2000);
      const after = await p.evaluate(() => document.querySelector('[data-kind="switch"]').getAttribute('data-state'));
      const sent = await log();
      ok('выключатель', before !== after && sent.some(s => s[0].indexOf('/devices/sw/controls/') === 0),
         before + ' -> ' + after);
      // обновление после нажатия пришло без головы
      const lean = await p.evaluate(() => !!(HEAD && HEAD.mark));
      ok('голова панели запомнена', lean);
      await p.context().close();
    }

    // 4. пульт фанкойла: клавиша скорости уходит командой
    {
      const p = await page('gal.html');
      await p.evaluate(() => expand(document.querySelector('[data-kind="fancoil"]')));
      await p.waitForTimeout(1500);
      const n0 = (await log()).length;
      const key = await p.$('#ov [data-z="key"]');
      if (key) {
        const keys = await p.$$('#ov [data-z="key"]');
        const b = await keys[keys.length - 2].boundingBox();
        await p.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
        await p.waitForTimeout(800);
      }
      const sent = (await log()).slice(n0);
      ok('пульт фанкойла', !!key && sent.some(s => /fan\/on$/.test(s[0])), JSON.stringify(sent));
      await p.context().close();
    }

    // 5. меню шестерёнки: колонки, закрытие нажатием мимо, «назад»
    {
      const p = await page('gal.html');
      await tap(p, '[data-nav="menu"]');
      await p.waitForTimeout(300);
      const opened = await p.evaluate(() => document.getElementById('navmenu').classList.contains('open'));
      await p.click('#navmenu [data-v="3"]');
      await p.waitForTimeout(1200);
      const cols = await p.evaluate(() => COLS);
      await p.mouse.click(200, 830);   // ниже меню: с 1.51 оно выше
      await p.waitForTimeout(300);
      const closed = await p.evaluate(() => !document.getElementById('navmenu').classList.contains('open'));
      ok('шестерёнка', opened && cols === '3' && closed, 'cols=' + cols);
      // нажатие мимо меню на полсекунды гасит касания - как на планшете
      await p.waitForTimeout(600);
      await tap(p, '[data-nav="back"]');
      await p.waitForTimeout(800);
      ok('кнопка «назад»', /\/(\?|$)/.test(p.url().replace(BASE, '/')), p.url());
      await p.context().close();
    }

    // 6. окно «Сегодня»: галочка отмечает пункт списка
    {
      const p = await page('day.html');
      await p.evaluate(() => openPad(document.querySelector('[data-pad="today"]')));
      await p.waitForTimeout(1200);
      const boxes = await p.evaluate(() => document.querySelectorAll('#ov .lst').length);
      const n0 = await p.evaluate(() => document.querySelectorAll('#ov .lrow:not(.done)').length);
      await p.click('#ov .lrow .lchk');
      await p.waitForTimeout(1200);
      const n1 = await p.evaluate(() => document.querySelectorAll('#ov .lrow:not(.done)').length);
      ok('окно «Сегодня»', boxes === 2 && n1 === n0 - 1, boxes + ' блока, пунктов ' + n0 + ' -> ' + n1);
      await p.context().close();
    }

    // 7. облегчённо: адрес с lite=1, у стекла нет отсвета, анимации стоят
    {
      const p = await page('gal.html?theme=glass', () => { try { localStorage.setItem('wb-panel-lite', '1'); } catch (e) {} });
      const st = await p.evaluate(() => ({q: Q(), glare: !!document.querySelector('#host [data-glare]'),
        paused: document.querySelector('#host svg').animationsPaused()}));
      ok('облегчённо', /lite=1/.test(st.q) && !st.glare && st.paused, JSON.stringify(st));
      await p.context().close();
    }

    // 9. раздел свернули - части главной группы сводятся без полной замены,
    //    и на экране ровно то, что прислал сервер
    {
      const p = await page('acc.html');
      await p.evaluate(() => { MODE = 'list'; saveFold(); refresh(true); });
      await p.waitForTimeout(1500);
      const before = await p.evaluate(() => ({merged: perfMerged, n: document.querySelectorAll('#host [data-main] > *').length}));
      await p.evaluate(() => toggleSection(document.querySelector('[data-section]')));
      await p.waitForTimeout(1800);
      const st = await p.evaluate(() => fetch(svgUrl(Q()).replace(/have=[^&]*&/, ''), {cache: 'no-store'})
        .then(r => r.text()).then(t => {
          // часы живые: минута может смениться между ответами
          const live = a => a.filter(x => !/class="(clock|h1-sub date)"/.test(x));
          const fresh = live(partsOf(mainOf(parseSvg(t))));
          const root = parseSvg(t), cur = document.querySelector('#host svg');
          const shown = live(partsOf(mainOf(cur)));
          return {merged: perfMerged, n: shown.length, same: fresh.join('') === shown.join('')
            && root.getAttribute('viewBox') === cur.getAttribute('viewBox')
            && root.getAttribute('height') === cur.getAttribute('height')};
        }));
      // голова не поменялась: следующий ответ - без неё, с новой высотой
      const lean = await p.evaluate(() => fetch(svgUrl(Q()), {cache: 'no-store'}).then(r =>
        ({lean: r.headers.get('X-Panel-Lean'), h: r.headers.get('X-Panel-Height'),
          shown: document.querySelector('#host svg').getAttribute('height')})));
      st.lean = lean.lean === '1' && lean.h === lean.shown;
      st.text = await p.evaluate(() => TEXT_PARTS && HTML_PARTS);
      ok('свёртка раздела без полной замены', st.merged > before.merged && st.same && st.lean && st.text && st.n !== before.n,
         JSON.stringify([before, st]));
      await p.evaluate(() => { MODE = ''; FOLDED = []; saveFold(); });
      await p.context().close();
    }

    // 11. вкладки сверху: «назад» и шестерёнка в приколотой полосе (1.50)
    {
      const p = await page('tabs.html');
      const st = await p.evaluate(() => ({
        back: !!document.querySelector('#tabdock [data-nav="back"]'),
        head: document.querySelectorAll('#host [data-nav]').length,
        hidden: !!document.querySelector('#host [data-tabbar] [data-nav="menu"]')}));
      await tap(p, '#tabdock [data-nav="menu"]');
      await p.waitForTimeout(400);
      st.menu = await p.evaluate(() => document.getElementById('navmenu').classList.contains('open'));
      ok('кнопки в ряду вкладок', st.back && st.menu && st.head === 2 && st.hidden, JSON.stringify(st));
      await p.context().close();
    }

    // 12. своя картинка обоев стекла (1.50): фон страницы и линза
    {
      const p = await page('gal.html?theme=glass');
      const st = await p.evaluate(() => {
        const pw = document.getElementById('pagewall'), img = document.querySelector('#host [data-wall] image');
        return {page: pw ? /url\(.*wall\//.test(pw.style.background) : false,
                lens: !!img, size: img ? +img.getAttribute('height') : 0};
      });
      const r = await p.evaluate(() => fetch(document.querySelector('#host svg').getAttribute('data-wall-img'))
        .then(r => r.status + ' ' + r.headers.get('content-type')));
      ok('обои своей картинкой', st.page && st.lens && st.size > 100 && r === '200 image/jpeg', JSON.stringify(st) + ' ' + r);
      await p.context().close();
    }

    // 13. жесты на плитках (с 1.53 - как в пульте, от точки касания):
    //     диммер вверх - ярче, термостат - поворот ручки по кругу,
    //     кондиционер - шкала за пальцем (влево - теплее), шар вбок - теплота
    {
      const p = await page('gal.html', () => { try { localStorage.setItem('wb-panel-drag', '1'); } catch (e) {} });
      // path(k, b) - точка пальца на шаге k из 8 по рамке плитки b
      async function swipe(sel, path) {
        const el = await p.$(sel);
        await el.scrollIntoViewIfNeeded();
        const b = await el.boundingBox();
        const s0 = path(0, b);
        await p.mouse.move(s0[0], s0[1]); await p.mouse.down();
        for (let k = 1; k <= 8; k++) { const q = path(k, b); await p.mouse.move(q[0], q[1]); await p.waitForTimeout(40); }
        // живая плитка (1.52): рисунок самой плитки идёт за пальцем, без плашки
        const lt = await p.evaluate(() => !!(press && press.drag && press.drag.lt));
        await p.mouse.up(); await p.waitForTimeout(900);
        return lt;
      }
      const line = (dx, dy) => (k, b) => [b.x + b.width / 2 + dx * k / 8, b.y + b.height / 2 + dy * k / 8];
      // по кругу ручки термостата по часовой: от верха на 40°
      const knob = (k, b) => {
        const cx = b.x + b.width / 2, cy = b.y + b.height * 0.585, r = 0.33 * b.width;
        const a = (-90 + 40 * k / 8) * Math.PI / 180;
        return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
      };
      const n0 = (await log()).length;
      const st = await p.evaluate(() => {
        const q = s => document.querySelector(s);
        return {cls: document.documentElement.className, ta: getComputedStyle(q('#host')).touchAction,
          lvl: +q('[data-kind="dimmer"]').getAttribute('data-level'),
          tgt: +q('[data-kind="thermostat"]').getAttribute('data-target'),
          ac: +q('[data-kind="ac"]').getAttribute('data-ac-now'),
          acTopic: q('[data-kind="ac"]').getAttribute('data-ac-target'),
          tempTopic: q('[data-kind="light"]').getAttribute('data-temp')};
      });
      st.live = [await swipe('[data-kind="dimmer"]', line(0, -70)),
                 await swipe('[data-kind="thermostat"]', knob),
                 await swipe('[data-kind="ac"]', line(-70, 0)),
                 await swipe('[data-kind="light"]', line(60, 0))];
      const sent = (await log()).slice(n0);
      const val = topic => sent.filter(s => s[0] === topic).map(s => +s[1]);
      const bright = sent.filter(s => /Channel 1\/on$/.test(s[0])).map(s => +s[1]);
      st.bright = bright; st.tgt2 = val('th/tgt/on'); st.ac2 = val(st.acTopic); st.temp2 = val(st.tempTopic);
      st.toggled = sent.some(s => s[0] === '/devices/dm/controls/K1/on' && s[1] === '0');
      ok('жесты на плитках', /tdrag/.test(st.cls) && st.ta === 'none'
         && bright.length && bright[bright.length - 1] > st.lvl * 100
         && st.tgt2.length === 1 && st.tgt2[0] > st.tgt
         && st.ac2.length === 1 && st.ac2[0] > st.ac
         && st.temp2.length === 1 && !st.toggled && st.live.every(Boolean), JSON.stringify(st));
      await p.context().close();
    }

    // 14. ручки шлют сразу (1.51): пульт диммера - значения и по дороге
    {
      const p = await page('gal.html', () => { try { localStorage.setItem('wb-panel-live', '1'); } catch (e) {} });
      await p.evaluate(() => openPad(document.querySelector('[data-kind="dimmer"]')));
      await p.waitForTimeout(800);
      const n0 = (await log()).length;
      const b = await (await p.$('#lvl')).boundingBox();
      const x = b.x + b.width / 2, y = b.y + b.height * 0.2;
      await p.mouse.move(x, y); await p.mouse.down();
      // вниз - темнее: по дороге уходят убывающие значения
      for (let k = 1; k <= 12; k++) { await p.mouse.move(x, y + k * 14); await p.waitForTimeout(120); }
      const during = (await log()).slice(n0).filter(s => /Channel 1\/on$/.test(s[0])).length;
      await p.mouse.up(); await p.waitForTimeout(800);
      const all = (await log()).slice(n0).filter(s => /Channel 1\/on$/.test(s[0])).map(s => +s[1]);
      ok('ручки шлют сразу', during >= 2 && all[0] > all[all.length - 1]
         && all[all.length - 1] === Math.min.apply(null, all), JSON.stringify({during, all}));
      await p.context().close();
    }

    // 10. пока листают, обновление ждёт и догоняет после (1.48)
    {
      const p = await page('gal.html');
      const st = await p.evaluate(() => new Promise(done => {
        const svg = shownSvg.replace(/(class="clock"[^>]*>)[^<]*/, '$1 99:99');
        lastScroll = Date.now();
        applySvg(svg, ++reqSeq);
        const during = shownSvg === svg;
        setTimeout(() => done({during, after: shownSvg === svg,
          clock: (document.querySelector('#host .clock') || {}).textContent}), 900);
      }));
      ok('обновление после прокрутки', !st.during && st.after && /99:99/.test(st.clock), JSON.stringify(st));
      await p.context().close();
    }

    // 8. замер ?perf=1
    {
      const p = await page('gal.html?perf=1');
      await p.mouse.wheel(0, 400);
      await p.waitForTimeout(800);
      const txt = await p.evaluate(() => (document.getElementById('perf') || {}).textContent || '');
      ok('замер ?perf=1', /обновление/.test(txt) && /прокрутка/.test(txt));
      await p.context().close();
    }
  } catch (e) {
    failed++;
    console.log('FAIL исключение: ' + (e && e.stack || e));
  }
  await br.close();
  srv.kill();
  console.log(failed ? 'не прошло: ' + failed : 'браузерные проверки пройдены');
  process.exit(failed ? 1 : 0);
})();
