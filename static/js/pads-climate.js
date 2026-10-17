/* wb-svg-panel: пульты кондиционера и фанкойла.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- пульт кондиционера ----------
   Колесо сверху: шкала едет под неподвижной меткой по центру, как в
   приложении. Тянешь влево - значения растут. Ниже режимы, питание и
   скорость вентилятора. */
function padAC(node){
  // С 1.15 у кондиционера колесо как на плитке; у фанкойла с 1.42 - его
  // пульт крупно (ползунок, решётка, клавиши). look: classic - прежние.
  if(node.getAttribute('data-look') !== 'classic'){
    if(node.getAttribute('data-kind') === 'ac') return padDisk(node);
    if(node.getAttribute('data-kind') === 'fancoil') return padFancoil(node);
  }
  return padArc(node);
}

/* ---------- пульт фанкойла на весь экран (с 1.42) ----------
   Картинку рисует сервер (плитка view=pad): шапка, режимы, ползунок,
   решётка, табло, клавиши. Пульт двигает колпачок и сам рисует цифры
   табло, пока ползунок тянут; нажатия - по data-z: back, power, mode, key. */
var SEG7 = {'0':'abcdef','1':'bc','2':'abged','3':'abgcd','4':'fgbc','5':'afgcd',
            '6':'afgedc','7':'abc','8':'abcdefg','9':'abcfgd','-':'g'};
function sevenSegSvg(text, x, y, w, h, t, gap){
  var hh = h / 2, k = t / 2, out = '';
  function sk(p){ return (p[0] + (y + h - p[1]) * 0.1).toFixed(1) + ',' + p[1].toFixed(1); }
  function hs(cx, cy){ return [[cx - w/2 + k, cy], [cx - w/2 + t, cy - k], [cx + w/2 - t, cy - k],
                               [cx + w/2 - k, cy], [cx + w/2 - t, cy + k], [cx - w/2 + t, cy + k]]; }
  function vs(cx, cy){ return [[cx, cy - hh/2 + k], [cx + k, cy - hh/2 + t], [cx + k, cy + hh/2 - t],
                               [cx, cy + hh/2 - k], [cx - k, cy + hh/2 - t], [cx - k, cy - hh/2 + t]]; }
  for(var i = 0; i < text.length; i++){
    var ch = text.charAt(i);
    if(ch === '.'){
      out += '<circle cx="' + (x - gap / 2 + 1).toFixed(1) + '" cy="' + (y + h).toFixed(1) + '" r="' + (t * .62).toFixed(1) + '" class="fcp-ink"/>';
      x += 5; continue;
    }
    var cx = x + w / 2, sh = {a: hs(cx, y), g: hs(cx, y + hh), d: hs(cx, y + h),
      f: vs(x, y + hh / 2), b: vs(x + w, y + hh / 2), e: vs(x, y + hh * 1.5), c: vs(x + w, y + hh * 1.5)};
    'abcdefg'.split('').forEach(function(n){
      out += '<polygon points="' + sh[n].map(sk).join(' ') + '" class="' + ((SEG7[ch] || '').indexOf(n) >= 0 ? 'fcp-ink' : 'fcp-ghost') + '"/>';
    });
    x += w + gap;
  }
  return out;
}
function padFancoil(node){
  var wrap = el('div', 'acscreen');
  wrap.textContent = '…';
  openOv([wrap]);
  ov.classList.add('full');
  var reloadT = null, busy = false, wired = false, G = null, D = null, gest = null;
  var PADMAX = 722;
  function size(){
    var w = Math.max(240, Math.min(Math.round(window.innerWidth), PADMAX));
    var h = Math.max(200, Math.min(Math.round(window.innerHeight), PADMAX));
    var boxed = w < window.innerWidth - 1 || h < window.innerHeight - 1;
    wrap.classList.toggle('boxed', boxed);
    wrap.style.width = boxed ? w + 'px' : '';
    wrap.style.height = boxed ? h + 'px' : '';
    return [w, h];
  }
  function url(){
    var sz = size();
    return 'tile/' + encodeURIComponent(NAME) + '/' + node.getAttribute('data-i')
         + '.svg?w=' + sz[0] + '&h=' + sz[1] + '&view=pad' + themeQ();
  }
  function load(){
    if(busy){ schedule(800); return; }
    fetch(url() + '&t=' + Date.now(), {cache: 'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        if(busy){ schedule(800); return; }
        wrap.innerHTML = svg;
        var root = wrap.querySelector('svg');
        if(root){ root.setAttribute('width', '100%'); root.setAttribute('height', '100%'); }
        wire();
      })
      .catch(function(e){ if(!G) wrap.textContent = 'Не удалось: ' + e.message; });
  }
  function schedule(ms){ clearTimeout(reloadT); reloadT = setTimeout(load, ms || 1500); }
  var onResize = function(){ schedule(250); };
  window.addEventListener('resize', onResize);
  ovCleanup = function(){ window.removeEventListener('resize', onResize); clearTimeout(reloadT); ov.classList.remove('full'); };
  load();

  function num(a){ return parseFloat(G.getAttribute('data-' + a)); }
  function wire(){
    G = wrap.querySelector('[data-fcpad]');
    if(!G) return;
    D = {top: num('top'), bot: num('bot'), smin: num('smin'), smax: num('smax'), lo: num('lo'), hi: num('hi'),
         step: num('step') || 0.5, cur: num('cur'), cap: num('cap'), t: num('target'),
         seg: (G.getAttribute('data-seg') || '').split(',').map(parseFloat), on: G.getAttribute('data-on') === '1'};
    if(!wired){ drag(wrap, move, done, begin); wired = true; }
  }
  function local(ev){
    var pt = evPoint(ev), m = G && G.getScreenCTM();
    if(!m) return {x: 0, y: 0};
    var sp = G.ownerSVGElement.createSVGPoint();
    sp.x = pt.clientX; sp.y = pt.clientY;
    return sp.matrixTransform(m.inverse());
  }
  function closest(n, sel){
    while(n && n.nodeType === 1){ if(n.matches ? n.matches(sel) : n.msMatchesSelector(sel)) return n; n = n.parentNode; }
    return null;
  }
  function fy(t){ t = Math.max(D.smin, Math.min(D.smax, t)); return D.bot - (t - D.smin) * (D.bot - D.top) / ((D.smax - D.smin) || 1); }
  function lcdText(t){ return isNaN(t) ? '--.-' : (t >= 99.95 ? String(Math.round(t)) : (Math.round(t * 2) / 2).toFixed(1)); }
  function show(t){
    var y = fy(t), capg = G.querySelector('.fcp-capg');
    if(capg) capg.setAttribute('transform', 'translate(0 ' + (y - D.cap).toFixed(1) + ')');
    var fill = G.querySelector('.fcp-fill');
    if(fill && !isNaN(D.cur)){
      fill.setAttribute('y', Math.min(D.cur, y).toFixed(1));
      fill.setAttribute('height', Math.abs(D.cur - y).toFixed(1));
    }
    var lg = G.querySelector('.fcp-lcdbox .fcp-lcdg');
    if(lg && D.seg.length === 6){
      var old = lg.querySelectorAll('polygon, circle');
      for(var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
      var tmp = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      tmp.innerHTML = sevenSegSvg(lcdText(t), D.seg[0], D.seg[1], D.seg[2], D.seg[3], D.seg[4], D.seg[5]);
      while(tmp.firstChild) lg.insertBefore(tmp.firstChild, lg.firstChild);
    }
  }
  function valueAt(y){
    var t = D.smin + (D.bot - y) * (D.smax - D.smin) / ((D.bot - D.top) || 1);
    t = Math.max(D.lo, Math.min(D.hi, t));
    return Math.round(t / D.step) * D.step;
  }
  function fail(e){ flash('Не удалось: ' + e.message); }
  function begin(e){
    if(!G) return;
    busy = true;
    var p = local(e), z = closest(e.target, '[data-z]');
    gest = {zone: z ? z.getAttribute('data-z') : '', el: z, y0: p.y, moved: false, t: D.t};
  }
  function move(e){
    if(!gest || !G) return;
    var p = local(e);
    if(Math.abs(p.y - gest.y0) > 4) gest.moved = true;
    if(gest.zone === 'slider' && gest.moved){ gest.t = valueAt(p.y); show(gest.t); }
  }
  function done(){
    busy = false;
    var g = gest; gest = null;
    if(!g || !G) return;
    var power = node.getAttribute('data-topic');
    if(g.zone === 'slider'){
      if(!g.moved){ g.t = valueAt(g.y0); show(g.t); }
      var tt = node.getAttribute('data-ac-target');
      if(tt && !isNaN(g.t) && g.t !== D.t){ D.t = g.t; pub(tt, g.t).catch(fail); }
      schedule(1500); return;
    }
    if(g.moved) return;
    if(g.zone === 'back'){ closeOv(); return; }
    if(g.zone === 'power'){
      if(power) pub(power, D.on ? node.getAttribute('data-off') : node.getAttribute('data-on')).catch(fail);
      schedule(700); return;
    }
    if(g.zone === 'mode'){
      var mt = node.getAttribute('data-ac-mode'), jobs = [];
      if(!D.on && power) jobs.push(pub(power, node.getAttribute('data-on')));
      if(mt) jobs.push(pub(mt, g.el.getAttribute('data-v')));
      Promise.all(jobs).catch(fail); schedule(700); return;
    }
    if(g.zone === 'key'){
      var v = g.el.getAttribute('data-v'), ft = node.getAttribute('data-ac-fan'), js = [];
      if(v === 'off'){ if(power && D.on) js.push(pub(power, node.getAttribute('data-off'))); }
      else {
        if(!D.on && power) js.push(pub(power, node.getAttribute('data-on')));
        if(ft) js.push(pub(ft, v));
      }
      Promise.all(js).catch(fail); schedule(700); return;
    }
  }
}

/* ---------- пульт кондиционера на весь экран (с 1.16) ----------
   Картинку рисует сервер - плитка в виде pad во весь размер окна, своя
   раскладка для телефона стоя, на боку и квадратной панели. Пульт только
   разбирает жесты. Что под пальцем, решает точка касания (data-z):
   колесо крутит уставку, кольца вентилятора - скорость, малые колёса -
   жалюзи, «A» в их центре - качание. Шкала при повороте не
   перерисовывается: крутится её группа, пропеллер вертит CSS. */
function padDisk(node){
  var wrap = el('div', 'acscreen');
  wrap.textContent = '…';
  openOv([wrap]);
  ov.classList.add('full');
  var stepV = parseFloat(node.getAttribute('data-ac-step') || '1') || 1;
  var reloadT = null, busy = false, wired = false;
  var G = null, D = null, pos = 0, temp = 0, gest = null;

  /* На ноутбуке пульт во весь экран выглядел плакатом. С 1.23 он не
     больше плитки 4×4 (722 px): на телефоне и планшете на стене это всё
     равно весь экран, на большом мониторе - карточка по центру. */
  var PADMAX = 722;
  function size(){
    var w = Math.max(240, Math.min(Math.round(window.innerWidth), PADMAX));
    var h = Math.max(200, Math.min(Math.round(window.innerHeight), PADMAX));
    var boxed = w < window.innerWidth - 1 || h < window.innerHeight - 1;
    wrap.classList.toggle('boxed', boxed);
    wrap.style.width = boxed ? w + 'px' : '';
    wrap.style.height = boxed ? h + 'px' : '';
    return [w, h];
  }
  function url(){
    var sz = size(), w = sz[0], h = sz[1];
    return 'tile/' + encodeURIComponent(NAME) + '/' + node.getAttribute('data-i')
         + '.svg?w=' + w + '&h=' + h + '&view=pad' + themeQ();
  }
  function load(){
    if(busy){ schedule(800); return; }
    fetch(url() + '&t=' + Date.now(), {cache: 'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        if(busy){ schedule(800); return; }
        wrap.innerHTML = svg;
        var root = wrap.querySelector('svg');
        if(root){ root.setAttribute('width', '100%'); root.setAttribute('height', '100%'); }
        wire();
      })
      .catch(function(e){ if(!G) wrap.textContent = 'Не удалось: ' + e.message; });
  }
  function schedule(ms){ clearTimeout(reloadT); reloadT = setTimeout(load, ms || 1500); }
  var onResize = function(){ schedule(250); };
  window.addEventListener('resize', onResize);
  ovCleanup = function(){
    window.removeEventListener('resize', onResize);
    clearTimeout(reloadT);
    ov.classList.remove('full');
  };
  load();

  function num(g, a){ return parseFloat(g.getAttribute('data-' + a)); }
  function wire(){
    G = wrap.querySelector('.acs');
    if(!G) return;
    D = {cx: num(G,'cx'), cy: num(G,'cy'), r: num(G,'r'), rr: num(G,'rr'), dpd: num(G,'dpd'),
         asp: num(G,'asp'), base: num(G,'val'), lo: num(G,'lo'), hi: num(G,'hi'), cur: num(G,'cur'),
         labr: num(G,'labr'), w: num(G,'w'), h: num(G,'h'), fs: num(G,'fs') || 1,
         on: G.getAttribute('data-on') === '1', notarget: G.getAttribute('data-notarget') === '1',
         box: (G.getAttribute('data-box') || '0,0,0,0').split(',').map(parseFloat),
         // колесо линиями (с 1.39): подпись уставки не прячется, а выделяется;
         // кружки вентилятора и жалюзи открывают шторки
         arc: G.getAttribute('data-arc') === '1', wl: num(G,'wl') || num(G,'w')};
    pos = temp = D.base;
    if(!wired){ drag(wrap, move, done, begin); wired = true; }
  }
  function local(ev){
    var pt = evPoint(ev), m = G && G.getScreenCTM();
    if(!m) return {x: 0, y: 0};
    var sp = G.ownerSVGElement.createSVGPoint();
    sp.x = pt.clientX; sp.y = pt.clientY;
    return sp.matrixTransform(m.inverse());
  }
  function pol(r, a){ a = a * Math.PI / 180; return [D.cx + r * Math.cos(a), D.cy + r * Math.sin(a)]; }
  function setCls(n, base, hid){ n.setAttribute('class', base + (hid ? ' acs-hid' : '')); }
  function closest(n, sel){
    while(n && n.nodeType === 1){ if(n.matches ? n.matches(sel) : n.msMatchesSelector(sel)) return n; n = n.parentNode; }
    return null;
  }

  /* ---- колесо уставки ---- */
  function draw(){
    var rot = -(pos - D.base) * D.dpd;
    // шкала и резкая тень зубцов под ней крутятся вместе
    var sc = G.querySelectorAll('.acs-scale');
    for(var si = 0; si < sc.length; si++)
      sc[si].setAttribute('transform', 'rotate(' + rot.toFixed(3) + ' ' + D.cx + ' ' + D.cy + ')');
    var b = D.box, m = 1 * D.fs, i;   // как label_hidden в ac_disk.py
    var labs = G.querySelectorAll('.acs-lab');
    for(i = 0; i < labs.length; i++){
      var t = parseFloat(labs[i].getAttribute('data-t'));
      var p = pol(D.labr, D.asp + (t - pos) * D.dpd);
      if(D.arc){
        setCls(labs[i], 'acs-lab' + (Math.abs(t - temp) < 0.01 && !D.notarget ? ' sel' : ''),
               !(p[0] > 9 && p[0] < D.wl - 9 && p[1] > 9 && p[1] < D.h - 9));
        continue;
      }
      setCls(labs[i], 'acs-lab', Math.abs(t - temp) < 0.01
             || (p[0] > b[0] - m && p[0] < b[2] + m && p[1] > b[1] - m && p[1] < b[3] + m)
             || !(p[0] > 9 && p[0] < D.w - 9 && p[1] > 9 && p[1] < D.h - 9));
    }
    var tks = G.querySelectorAll('.acs-tk');
    for(i = 0; i < tks.length; i++){
      var tt = parseFloat(tks[i].getAttribute('data-t'));
      setCls(tks[i], 'acs-tk', (!D.arc && Math.abs(tt - temp) < 0.01) || tt < D.lo || tt > D.hi);
    }
    // отрезок «сейчас - уставка»; у колеса линиями их три (ореол и линия)
    var segs = G.querySelectorAll('.acs-seg');
    var show = D.on && !D.notarget && !isNaN(D.cur) && Math.abs(D.cur - temp) > 0.05, sd = '';
    if(show){
      var a0 = D.asp + (D.cur - pos) * D.dpd, p0 = pol(D.rr, a0), p1 = pol(D.rr, D.asp);
      sd = 'M ' + p0[0].toFixed(1) + ',' + p0[1].toFixed(1) + ' A ' + D.rr + ',' + D.rr
         + ' 0 0 ' + (D.asp > a0 ? 1 : 0) + ' ' + p1[0].toFixed(1) + ',' + p1[1].toFixed(1);
    }
    for(i = 0; i < segs.length; i++){
      if(show) segs[i].setAttribute('d', sd);
      setCls(segs[i], 'acs-seg', !show);
    }
    var v = G.querySelector('.acs-v');
    if(v) v.textContent = stepV < 1 && Math.abs(temp - Math.round(temp)) > 0.01 ? temp.toFixed(1) : String(Math.round(temp));
  }
  function angle(p){ return Math.atan2(p.y - D.cy, p.x - D.cx) * 180 / Math.PI; }
  function sendTarget(){
    var t = node.getAttribute('data-ac-target');
    if(t) pub(t, temp).catch(function(e){ flash('Не удалось: ' + e.message); });
    schedule(1500);
  }
  function animateTo(t){
    var from = pos, t0 = Date.now(), dur = 260;
    temp = t;
    (function stepA(){
      var k = Math.min(1, (Date.now() - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      pos = from + (t - from) * e; draw();
      if(k < 1) (window.requestAnimationFrame || setTimeout)(stepA, 16);
      else { pos = t; draw(); sendTarget(); }
    })();
  }

  /* ---- вентилятор ---- */
  var FANS = ['low', 'medium', 'high'];
  function fanRing(f, p){
    var d = Math.sqrt(Math.pow(p.x - num(f,'fx'), 2) + Math.pow(p.y - num(f,'fy'), 2));
    var fr = num(f,'fr'), rg = num(f,'rg');
    if(d < fr * 1.1) return 0;
    return Math.max(1, Math.min(3, Math.round((d - fr) / rg)));
  }
  function showFan(f, ring){
    var rings = f.querySelectorAll('[data-ring]');
    for(var i = 0; i < rings.length; i++){
      var k = parseInt(rings[i].getAttribute('data-ring'), 10);
      rings[i].setAttribute('class', 'acs-ring in' + (ring && k <= ring ? ' on' : '') + (!ring ? ' auto' : ''));
      rings[i].setAttribute('stroke-width', f.getAttribute(k === ring ? 'data-swon' : 'data-sw'));
    }
  }
  function setFan(f, ring){
    var topic = node.getAttribute('data-ac-fan'), q = node.getAttribute('data-ac-quiet');
    var state = f.getAttribute('data-state');
    var jobs = [];
    if(ring){
      showFan(f, ring);
      if(topic) jobs.push(pub(topic, FANS[ring - 1]));
      if(state === 'quiet' && q) jobs.push(pub(q, '0'));
      f.setAttribute('data-state', String(ring));
    } else {
      // по центру: авто -> тихо -> авто; со скорости - сразу авто
      showFan(f, 0);
      if(state === 'auto' && q){ jobs.push(pub(q, '1')); f.setAttribute('data-state', 'quiet'); }
      else {
        if(state === 'quiet' && q) jobs.push(pub(q, '0'));
        if(topic) jobs.push(pub(topic, 'auto'));
        f.setAttribute('data-state', 'auto');
      }
    }
    Promise.all(jobs).catch(function(e){ flash('Не удалось: ' + e.message); });
    schedule(900);
  }

  /* ---- жалюзи ---- */
  function dialIndex(g, p){
    var angs = g.getAttribute('data-angs').split(',').map(parseFloat);
    var a = Math.atan2(p.y - num(g,'y'), p.x - num(g,'x')) * 180 / Math.PI, best = 0, bd = 1e9;
    for(var i = 0; i < angs.length; i++){
      var d = Math.abs(((a - angs[i]) % 360 + 540) % 360 - 180);
      if(d < bd){ bd = d; best = i; }
    }
    return best;
  }
  function showDial(g, idx, auto){
    g.setAttribute('class', 'acs-dial' + (auto ? ' auto' : ''));
    var arr = g.querySelectorAll('.acs-arrow');
    for(var i = 0; i < arr.length; i++)
    { arr[i].setAttribute('class', 'acs-arrow' + (auto || i === idx ? ' on' : ''));
      arr[i].setAttribute('stroke-width', g.getAttribute(!auto && i === idx ? 'data-awon' : 'data-aw')); }
    if(!auto){
      var angs = g.getAttribute('data-angs').split(',').map(parseFloat), a = angs[idx] * Math.PI / 180;
      var x = num(g,'x'), y = num(g,'y'), p1 = num(g,'p1'), p2 = num(g,'p2'), ptr = g.querySelector('.acs-ptr');
      ptr.setAttribute('x1', (x + Math.cos(a) * p1).toFixed(1)); ptr.setAttribute('y1', (y + Math.sin(a) * p1).toFixed(1));
      ptr.setAttribute('x2', (x + Math.cos(a) * p2).toFixed(1)); ptr.setAttribute('y2', (y + Math.sin(a) * p2).toFixed(1));
      g.setAttribute('data-active', String(idx));
    }
  }
  function sendDial(g, value){
    var topic = node.getAttribute(g.getAttribute('data-z') === 'vdial' ? 'data-ac-swing' : 'data-ac-swing-h');
    if(topic) pub(topic, value).catch(function(e){ flash('Не удалось: ' + e.message); });
    schedule(1500);
  }

  /* выбор в шторке колеса линиями: скорость (с «тихо») или жалюзи */
  function pickSet(set, value){
    var jobs = [];
    if(set === 'fan'){
      var topic = node.getAttribute('data-ac-fan'), q = node.getAttribute('data-ac-quiet');
      var quietNow = G.getAttribute('data-fanstate') === 'quiet';
      if(value === 'quiet'){ if(q) jobs.push(pub(q, '1')); }
      else {
        if(quietNow && q) jobs.push(pub(q, '0'));
        if(topic) jobs.push(pub(topic, value));
      }
    } else {
      var t = node.getAttribute(set === 'vdial' ? 'data-ac-swing' : 'data-ac-swing-h');
      if(t) jobs.push(pub(t, value));
    }
    Promise.all(jobs).catch(function(e){ flash('Не удалось: ' + e.message); });
    schedule(700);
  }

  /* скорость по кругу (колесо линиями): 1 - 2 - 3 - авто - тихо. Кружок и
     подпись меняются сразу, картинка с сервера догоняет. */
  var FAN_CYCLE = ['low', 'medium', 'high', 'auto', 'quiet'];
  var FAN_WORD = {low: 'скорость 1', medium: 'скорость 2', high: 'скорость 3', auto: 'авто', quiet: 'тихо'};
  var FAN_LEVEL = {low: 1, medium: 2, high: 3, auto: 3, quiet: 1};
  function cycleFan(el){
    var now = G.getAttribute('data-fanstate'), i = FAN_CYCLE.indexOf(now);
    var next = FAN_CYCLE[(i + 1) % FAN_CYCLE.length];
    var blades = el.querySelectorAll('path.acw-blade'), grp = el.querySelector('.acl');
    for(var k = 0; k < blades.length; k++)
      blades[k].setAttribute('class', 'acl-b acw-blade' + (k < FAN_LEVEL[next] ? ' act' : ''));
    if(grp) grp.setAttribute('class', 'acl' + (next === 'auto' ? ' swing' : ''));
    var cap = el.querySelector('.acp-val');
    if(cap) cap.textContent = FAN_WORD[next];
    pickSet('fan', next);
    G.setAttribute('data-fanstate', next);
  }

  /* ---- жесты ---- */
  function begin(e){
    if(!G) return;
    busy = true;
    var p = local(e), zEl = closest(e.target, '[data-z]');
    gest = {zone: zEl ? zEl.getAttribute('data-z') : '', el: zEl, x0: p.x, y0: p.y, p: p,
            moved: false, t0: pos, a0: angle(p), idx: -1};
    if(gest.zone === 'vdial-hub' || gest.zone === 'hdial-hub') gest.dial = closest(zEl.parentNode, '[data-z]');
  }
  function move(e){
    if(!gest || !G) return;
    var p = local(e);
    if(!(p.x || p.y)) return;
    gest.p = p;
    if(Math.sqrt(Math.pow(p.x - gest.x0, 2) + Math.pow(p.y - gest.y0, 2)) > 6 * Math.max(1, D.fs / 2)) gest.moved = true;
    if(!gest.moved) return;
    var z = gest.zone;
    if(z === 'wheel' && !D.notarget){
      var d = angle(p) - gest.a0;
      while(d > 180) d -= 360;
      while(d < -180) d += 360;
      pos = Math.max(D.lo, Math.min(D.hi, gest.t0 - d / D.dpd));
      temp = Math.max(D.lo, Math.min(D.hi, Math.round(pos / stepV) * stepV));
      draw();
    } else if(D.arc){
      return;
    } else if(z === 'fan'){
      var ring = fanRing(gest.el, p);
      if(ring){ gest.ring = ring; showFan(gest.el, ring); }
    } else if(z === 'vdial' || z === 'hdial'){
      gest.idx = dialIndex(gest.el, p);
      showDial(gest.el, gest.idx, false);
    }
  }
  function done(){
    busy = false;
    var g = gest; gest = null;
    if(!g || !G) return;
    var ch = G.querySelector('.acs-ch.open') || G.querySelector('.acs-ch[data-sheet="mode"]') || G.querySelector('.acs-ch');
    var chOpen = ch && /open/.test(ch.getAttribute('class'));
    if(g.moved){
      if(g.zone === 'wheel' && !D.notarget){ pos = temp; draw(); if(Math.abs(temp - g.t0) > 0.001) sendTarget(); }
      else if(g.zone === 'fan' && g.ring) setFan(g.el, g.ring);
      else if((g.zone === 'vdial' || g.zone === 'hdial') && g.idx >= 0)
        sendDial(g.el, g.el.getAttribute('data-values').split(',')[g.idx]);
      return;
    }
    // касание без сдвига
    if(chOpen && g.zone !== 'pick'){ ch.setAttribute('class', 'acs-ch'); return; }
    var z = g.zone, p = g.p;
    if(z === 'back'){ closeOv(); return; }
    if(z === 'power'){
      var topic = node.getAttribute('data-topic');
      if(topic) pub(topic, D.on ? node.getAttribute('data-off') : node.getAttribute('data-on'))
        .catch(function(e){ flash('Не удалось: ' + e.message); });
      schedule(700); return;
    }
    if(z === 'mode'){ if(ch) ch.setAttribute('class', 'acs-ch open'); return; }
    if(D.arc && z === 'fan'){ cycleFan(g.el); return; }
    if(D.arc && (z === 'vdial' || z === 'hdial')){
      var sh = G.querySelector('.acs-ch[data-sheet="' + z + '"]');
      if(sh) sh.setAttribute('class', 'acs-ch open');
      return;
    }
    if(z === 'pick'){
      if(ch) ch.setAttribute('class', 'acs-ch');
      var set = g.el.getAttribute('data-set');
      if(set){ pickSet(set, g.el.getAttribute('data-value')); return; }
      var mt = node.getAttribute('data-ac-mode');
      if(mt) pub(mt, g.el.getAttribute('data-mode')).catch(function(e){ flash('Не удалось: ' + e.message); });
      schedule(700); return;
    }
    if(z === 'fan'){ setFan(g.el, fanRing(g.el, p)); return; }
    if(z === 'vdial-hub' || z === 'hdial-hub'){
      var dg = g.dial, auto = /auto/.test(dg.getAttribute('class')), vals = dg.getAttribute('data-values').split(',');
      var idx = parseInt(dg.getAttribute('data-active'), 10);
      if(isNaN(idx) || idx < 0) idx = 2;
      showDial(dg, idx, !auto);
      sendDial(dg, auto ? vals[idx] : 'auto');
      return;
    }
    if(z === 'vdial' || z === 'hdial'){
      var i2 = dialIndex(g.el, p); showDial(g.el, i2, false);
      sendDial(g.el, g.el.getAttribute('data-values').split(',')[i2]); return;
    }
    if(z === 'wheel' && !D.notarget){
      // тап по шкале у обода - уставка доезжает до этого деления
      var dist = Math.sqrt(Math.pow(p.x - D.cx, 2) + Math.pow(p.y - D.cy, 2));
      if(Math.abs(dist - D.r) < 26 * D.fs){
        var dd = angle(p) - D.asp;
        while(dd > 180) dd -= 360;
        while(dd < -180) dd += 360;
        var t = Math.max(D.lo, Math.min(D.hi, Math.round((pos + dd / D.dpd) / stepV) * stepV));
        if(Math.abs(t - pos) > 0.001) animateTo(t);
      }
    }
  }
}

/* ---------- пульт фанкойла: дуга-улыбка ---------- */
function padArc(node){
  var lo = parseFloat(node.getAttribute('data-ac-lo')||'16');
  var hi = parseFloat(node.getAttribute('data-ac-hi')||'30');
  var step = parseFloat(node.getAttribute('data-ac-step')||'1');
  // pos - непрерывное положение шкалы, temp - значение, которое отправим.
  // Раньше шкала перерисовывалась от округлённого значения, из-за чего
  // засечки всегда попадали в те же места и колесо казалось неподвижным.
  var temp = parseFloat(node.getAttribute('data-ac-now'));
  if(isNaN(temp)) temp = Math.round((lo+hi)/2);
  var pos = temp;

  var title = el('div','ttl', node.getAttribute('data-title') || 'Кондиционер');
  var cur = node.getAttribute('data-ac-cur');
  // Крупно сверху - ФАКТИЧЕСКАЯ температура: уставку и так видно на шкале,
  // дублировать её незачем.
  var big = el('div','wbig');
  big.innerHTML = (cur || '--').replace('°','') + '<span class="wdeg">°C</span>';
  var sub = el('div','hint','в комнате · уставку задайте колесом');

  var W = Math.min(window.innerWidth - 30, 470), H = 190;
  var wrap = el('div'); wrap.id = 'wheel';
  wrap.style.width = W + 'px'; wrap.style.height = H + 'px';
  wrap.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">'
    + '<path class="warc2" fill="none" stroke="rgba(255,255,255,.14)" stroke-width="1.5"/>'
    + '<path class="wnotch" fill="none" stroke="rgba(255,255,255,.45)" stroke-width="1.6"/>'
    + '<g class="wticks"></g>'
    + '<path class="warc" fill="none" stroke="#B08BE8" stroke-width="3"/>'
    + '</svg>';
  // ВАЖНО: узлы ещё не в документе, поэтому ищем внутри wrap.
  // Через document.getElementById здесь приходит null, и пульт падал молча.
  var gTicks = wrap.querySelector('.wticks');
  var pArc   = wrap.querySelector('.warc');
  var pArc2  = wrap.querySelector('.warc2');
  var pNotch = wrap.querySelector('.wnotch');

  /* Дуга «улыбкой»: центр окружности НАД картинкой, поэтому середина шкалы
     внизу, а края поднимаются - как в приложении. Раньше центр был снизу и
     дуга выгибалась в другую сторону. */
  var PXDEG = W / 5, R = W * 1.7, CX = W / 2, BOTTOM = H - 34, CY = BOTTOM - R;
  function pt(delta, radius){
    var a = (delta * PXDEG) / R;
    var r = radius === undefined ? R : radius;
    return [CX + r * Math.sin(a), CY + r * Math.cos(a), a];
  }
  function arcPath(radius, half){
    var p0 = pt(-half, radius), p1 = pt(half, radius);
    return 'M ' + p0[0].toFixed(1) + ',' + p0[1].toFixed(1)
         + ' A ' + radius.toFixed(0) + ',' + radius.toFixed(0)
         + ' 0 0 0 ' + p1[0].toFixed(1) + ',' + p1[1].toFixed(1);
  }
  function draw(){
    var s = '', v, from = Math.ceil((pos - 3.2) * 5) / 5;
    for(v = from; v <= pos + 3.2; v += 0.2){
      if(v < lo - 0.001 || v > hi + 0.001) continue;
      var p = pt(v - pos), whole = Math.abs(v - Math.round(v)) < 0.05;
      var len = whole ? 12 : 7;
      // засечки смотрят наружу от центра окружности, то есть вниз
      var x2 = p[0] + Math.sin(p[2]) * len, y2 = p[1] + Math.cos(p[2]) * len;
      s += '<line x1="' + p[0].toFixed(1) + '" y1="' + p[1].toFixed(1)
         + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1)
         + '" stroke="#fff" stroke-width="' + (whole ? 1.7 : 1)
         + '" opacity="' + (whole ? .7 : .3) + '"/>';
      if(whole){
        // подписи с внутренней стороны дуги, то есть выше неё
        var lx = p[0] - Math.sin(p[2]) * 20, ly = p[1] - Math.cos(p[2]) * 20;
        var near = Math.abs(v - temp) < 0.05;
        s += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1)
           + '" text-anchor="middle" font-size="' + (near ? 19 : 16)
           + '" fill="rgba(255,255,255,' + (near ? .92 : .45) + ')">'
           + Math.round(v) + '°</text>';
      }
    }
    gTicks.innerHTML = s;
    pArc.setAttribute('d', arcPath(R, 3.2));
    pArc2.setAttribute('d', arcPath(R - 74, 2.6));
    var n = pt(0, R - 74);
    pNotch.setAttribute('d', 'M ' + (n[0] - 8) + ',' + (n[1] - 9)
                           + ' L ' + n[0] + ',' + (n[1] + 1)
                           + ' L ' + (n[0] + 8) + ',' + (n[1] - 9));
  }
  draw();

  var startX = null, startPos = pos;
  function begin(ev){
    startX = evPoint(ev).clientX; startPos = pos;
  }
  function at(ev){
    if(startX === null) begin(ev);
    pos = startPos - (evPoint(ev).clientX - startX) / PXDEG;  // влево - теплее
    pos = Math.max(lo, Math.min(hi, pos));
    temp = Math.max(lo, Math.min(hi, Math.round(pos / step) * step));
    draw();
  }
  function send(){
    startX = null;
    pos = temp;                       // защёлкиваем шкалу на выбранном делении
    draw();
    var t = node.getAttribute('data-ac-target');
    if(t) pub(t, temp).catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  drag(wrap, at, send, begin);

  var parts = [title, big, wrap, sub].concat(acControls(node));
  openOv(parts);
}

/* Ряд кнопок «выбери одно». Вынесен из padAC наружу: те же ряды нужны
   полноэкранному виду, а там своего пульта нет. */
function acChooser(node, list, attr, nowValue, changed){
  var row = el('div','acrow');
  list.forEach(function(item){
    // третий элемент - значок вместо подписи (жалюзи), подпись уходит в title
    var b = el('div', 'acbtn' + (item[2] ? ' ic' : '') + (item[0] === nowValue ? ' on' : ''),
               item[2] ? '' : item[1]);
    if(item[2]){ b.innerHTML = item[2]; b.title = item[1]; }
    b.onclick = function(){
      var topic = node.getAttribute(attr);
      if(!topic) return;
      pub(topic, item[0]).then(function(){
        var all = row.querySelectorAll('.acbtn');
        for(var i = 0; i < all.length; i++) all[i].classList.remove('on');
        b.classList.add('on');
        if(changed) changed(item[0]);
      }).catch(function(e){ flash('Не удалось: ' + e.message); });
    };
    row.appendChild(b);
  });
  return row;
}

/* Набор кнопок: "значение|подпись;…" из атрибута плитки, иначе зашитый
   здесь. У фанкойла режимы и скорости числовые и свои у каждого прибора,
   да и у сплита осушение с авто есть далеко не всегда. */
function acChoices(node, attr, fallback){
  var raw = node.getAttribute(attr), out = [];
  if(!raw) return fallback;
  raw.split(';').forEach(function(item){
    var p = item.split('|');
    if(p[0] !== '') out.push([p[0], p.length > 1 ? p[1] : p[0]]);
  });
  return out.length ? out : fallback;
}

/* Управление прибором: режимы, скорость вентилятора, лопасти, питание.
   Один набор на два места - пульт и полный экран. У фанкойла короткое
   нажатие раскрывает плитку, и если бы органы управления жили только в
   пульте, до скорости вентилятора пришлось бы добираться вслепую. */
/* Значок жалюзи для кнопки: веер из пяти лопастей. pos - какая светится
   (0..4), -1 - качание: тогда светятся все и под ними двойная стрелка. */
var LV_DEG = [-34, -17, 0, 17, 34];
function lvIcon(axis, pos){
  var s = '<svg viewBox="0 0 32 26" width="36" height="29" fill="none" stroke="currentColor" stroke-linecap="round">';
  var cx = 16, cy = 13, L = 5.2, i;
  for(i = 0; i < 5; i++){
    var a = LV_DEG[i] * Math.PI / 180, act = pos < 0 || pos === i;
    var x1, y1, x2, y2;
    if(axis === 'h'){
      var bx = cx + (i - 2) * 3.6, by = cy - 4.4, dx = Math.sin(a) * L, dy = Math.cos(a) * L;
      x1 = bx - dx * .2; y1 = by - dy; x2 = bx + dx; y2 = by + dy * .8;
    } else {
      var bx2 = cx - 3.4, by2 = cy + (i - 2) * 3.6, ex = Math.cos(a) * L, ey = Math.sin(a) * L;
      x1 = bx2 - ex; y1 = by2 - ey * .8; x2 = bx2 + ex * .8; y2 = by2 + ey * .2;
    }
    s += '<line x1="' + x1.toFixed(1) + '" y1="' + y1.toFixed(1) + '" x2="' + x2.toFixed(1)
       + '" y2="' + y2.toFixed(1) + '" stroke-width="' + (act && pos >= 0 ? 2.8 : 1.7)
       + '" opacity="' + (act ? 1 : .3) + '"/>';
  }
  if(pos < 0){
    s += axis === 'h'
      ? '<path d="M 8,20 Q 16,25 24,20 M 8,20 l 3,-1 M 8,20 l 1,3 M 24,20 l -3,-1 M 24,20 l -1,3" stroke-width="1.6"/>'
      : '<path d="M 24,5 Q 29,13 24,21 M 24,5 l -.6,3 M 24,5 l 2.6,1.6 M 24,21 l -.6,-3 M 24,21 l 2.6,-1.6" stroke-width="1.6"/>';
  }
  return s + '</svg>';
}

function acControls(node, changed){
  var parts = [];
  parts.push(el('div','hint','Режим'));
  parts.push(acChooser(node, acChoices(node, 'data-ac-modes',
             [['cool','Холод'],['heat','Тепло'],['dry','Сушка'],
              ['fan_only','Обдув'],['auto','Авто']]),
             'data-ac-mode', node.getAttribute('data-ac-mode-now'), changed));
  parts.push(el('div','hint','Скорость вентилятора'));
  parts.push(acChooser(node, acChoices(node, 'data-ac-fans',
             [['low','1'],['medium','2'],['high','3'],['auto','Авто']]),
             'data-ac-fan', node.getAttribute('data-ac-fan-now'), changed));
  if(node.getAttribute('data-ac-swing')){
    parts.push(el('div','hint','Жалюзи вверх-вниз'));
    // первая кнопка - качание, дальше пять положений сверху вниз
    parts.push(acChooser(node, [['auto','Качание', lvIcon('v', -1)],
                        ['upper','Верх', lvIcon('v', 0)], ['position_2','2', lvIcon('v', 1)],
                        ['position_3','3', lvIcon('v', 2)], ['position_4','4', lvIcon('v', 3)],
                        ['bottom','Низ', lvIcon('v', 4)], ['off','Выкл']],
                       'data-ac-swing', node.getAttribute('data-ac-swing-now'), changed));
  }
  if(node.getAttribute('data-ac-swing-h')){
    parts.push(el('div','hint','Жалюзи влево-вправо'));
    // кнопки идут слева направо, как реальные положения глазами
    // человека перед блоком: 7·6·1·5·4 (в 1.16.1 перевёрнуто)
    parts.push(acChooser(node, [['auto','Качание', lvIcon('h', -1)],
                        ['position_7','Лево', lvIcon('h', 0)], ['position_6','Лево-центр', lvIcon('h', 1)],
                        ['position_1','Центр', lvIcon('h', 2)], ['position_5','Центр-право', lvIcon('h', 3)],
                        ['position_4','Право', lvIcon('h', 4)]],
                       'data-ac-swing-h', node.getAttribute('data-ac-swing-h-now'), changed));
  }
  parts.push(powerRow(node).el);
  return parts;
}
