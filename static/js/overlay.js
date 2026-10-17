/* wb-svg-panel: окно на весь экран и общие детали пультов (шапка, ряд «− число +», кнопки).
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- оверлей ---------- */
/* Пульты, которым нужно следить за состоянием, оставляют здесь функцию
   уборки: при закрытии окна её надо позвать, иначе таймер продолжит
   тикать в пустоту. */
var ovCleanup = null;
/* Открытое окно - шаг в истории браузера. Жест «назад» на iPhone и
   кнопка «назад» на Android закрывают окно, а не уводят со страницы:
   раньше выход из пульта во весь экран приводил к списку панелей. */
var ovHist = false, ovReload = false;
window.addEventListener('popstate', function(){
  if (ovReload) { location.reload(); return; }
  if (ovHist) { ovHist = false; if (ov.classList.contains('open')) closeOv(); }
});
function closeOv(){ ov.classList.remove('open'); paused=false;
  // свой шаг истории убираем сами, popstate при этом окно уже не трогает
  if(ovHist){ ovHist = false; ovReload = appReload; history.back(); if(appReload) return; }
  if(appReload){ location.reload(); return; }
  if(ovCleanup){ ovCleanup(); ovCleanup = null; }
  var k=ov.querySelector('.card,#pad,#lvl,#crtwrap,.ttl,.hint');
  while(ov.children.length>1) ov.removeChild(ov.lastChild);
  document.documentElement.classList.remove('ovlock');
  // Панель оживает не сразу: закрывающее касание ещё не отыграло своё.
  shieldUntil = Date.now() + SHIELD_MS;
  setTimeout(function(){
    if (!ov.classList.contains('open')) host.classList.remove('shield');
  }, SHIELD_MS);
  refresh(); }
/* Кнопка «назад» пульта во весь экран стоит там же, где ссылка
   «← Панели» страницы. Окно закрывалось по отпусканию пальца, а
   следом браузер присылал click - уже в ссылку под ним, и вместо
   панели открывался список панелей. Клики мимо окна в первые мгновения
   после закрытия гасим. */
document.addEventListener('click', function(e){
  if (Date.now() < shieldUntil && !ov.contains(e.target)) {
    e.preventDefault(); e.stopPropagation();
  }
}, true);
/* Закрывает только касание, которое и началось на фоне. Иначе жест,
   начатый на пульте и отпущенный мимо карточки, - потянули дугу
   термостата за край - закрывал окно вместе с недоведённой уставкой. */
var ovDownBg = false;
(window.PointerEvent ? ['pointerdown'] : ['touchstart', 'mousedown']).forEach(function(n){
  ov.addEventListener(n, function(e){ ovDownBg = e.target === ov; }, true);
});
ov.addEventListener('click', function(e){ if(e.target===ov && ovDownBg) closeOv(); });
document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeOv(); });

/* Тема панели по классу корня SVG - как у полей страницы (pageBg). */
function rootTheme(root){
  // Корень SVG несёт цепочку тем («dark graphite»): сама тема - последняя.
  root = root || host.querySelector('svg');
  var cls = (root && root.getAttribute && root.getAttribute('class')) || '';
  var parts = cls.split(' ').filter(function(c){ return PAGE_BG[c]; });
  return parts.length ? parts[parts.length - 1] : '';
}
function ovLight(){
  var n = rootTheme();
  return !!PAGE_BG[n] && !DARK_THEME[n];
}
/* Цвета для пультов, которые рисуются скриптом (шар, ручка, колесо). */
function ovPal(){
  // Акцент, нагрев и холод пультов - из темы (с 1.27).
  var t = THEME_INFO[rootTheme()] || {};
  var c = {acc: t.acc || '#E09B2D', acc2: t.acc2 || '#FFB24D', accInk: t.accInk || '#2A1A08',
           heat: t.heat || '#FF8A3D', cool: t.cool || '#3B8FD4'};
  var p = ovLight()
    ? {lt: true, ink: '#1C1C1E', dim: 'rgba(28,28,30,.55)', track: 'rgba(0,0,0,.10)',
       face: '#F4F4F6', faceEdge: 'rgba(0,0,0,.10)', shadow: 'rgba(0,0,0,.16)',
       knob: '#FFFFFF', knobEdge: 'rgba(0,0,0,.30)', off: 'rgba(0,0,0,.30)', offKnob: '#A4A6AC',
       halo: PAGE_BG[rootTheme()] || '#E6E6E9', slot: '#B6B7BD', drum: '#DCDDE2',
       wheelA: '#FFFFFF', wheelB: '#D3D4DA', wheelEdge: 'rgba(0,0,0,.08)'}
    : {lt: false, ink: '#FFFFFF', dim: 'rgba(255,255,255,.55)', track: 'rgba(255,255,255,.14)',
       face: '#2E2F34', faceEdge: 'rgba(255,255,255,.08)', shadow: 'rgba(0,0,0,.35)',
       knob: '#F2F2F4', knobEdge: '#1C1D20', off: 'rgba(255,255,255,.42)', offKnob: '#8A8C92',
       halo: '#1C1D20', slot: '#0E0F11', drum: '#3A3B40',
       wheelA: '#4A4B52', wheelB: '#232428', wheelEdge: 'rgba(255,255,255,.07)'};
  for(var k in c) p[k] = c[k];
  return p;
}
/* ---- форма темы в пультах (с 1.35) ----
   Глубина (мягкая тень, плоско, неоморфизм), край ручек и колёс (зубцы,
   гладкий, насечка) и фаска (мягкая, плоская, острая) - те же, что у
   плиток (wbpanel/ac_disk.py, _base.j2). */
function hexRgb(h){
  h = String(h || '#000').replace('#', '');
  if(h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  return [parseInt(h.slice(0, 2), 16) || 0, parseInt(h.slice(2, 4), 16) || 0, parseInt(h.slice(4, 6), 16) || 0];
}
function FORM(){
  var t = THEME_INFO[rootTheme()] || {};
  return {depth: t.depth || 'soft', edge: t.edge || 'teeth', bevel: t.bevel || 'round',
          neu: t.neu || '#FFFFFF', shd: t.shd || '#000000'};
}
function gearD(cx, cy, r, k, edge){
  var tth = 6 * k, pts = [], i;
  function p(rad, a){ pts.push((cx + rad * Math.cos(a)).toFixed(1) + ',' + (cy + rad * Math.sin(a)).toFixed(1)); }
  if(edge === 'smooth'){
    var rs = r - tth * 0.2;
    return 'M' + (cx - rs).toFixed(1) + ',' + cy.toFixed(1) + 'a' + rs.toFixed(1) + ',' + rs.toFixed(1) +
           ' 0 1 0 ' + (2 * rs).toFixed(1) + ',0a' + rs.toFixed(1) + ',' + rs.toFixed(1) + ' 0 1 0 ' + (-2 * rs).toFixed(1) + ',0z';
  }
  if(edge === 'knurl'){
    var ro = r - tth * 0.2, dp = 1.8 * k, n2 = Math.max(36, Math.round(2 * Math.PI * ro / (4.4 * k)));
    for(i = 0; i < 2 * n2; i++) p(ro - (i % 2 ? dp : 0), Math.PI * i / n2);
    return 'M ' + pts.join(' L ') + ' Z';
  }
  var tw = 7 * k, n = Math.max(18, Math.round(2 * Math.PI * r / (16 * k))), hw = tw / 2 / r;
  for(i = 0; i < n; i++){
    var a0 = 2 * Math.PI * i / n;
    p(r, a0 - hw);
    for(var f = 0; f < 9; f++){
      var ff = Math.PI * f / 8, x = -tw / 2 * Math.cos(ff), rad = (r - tth + tw / 2) - (tw / 2) * Math.sin(ff);
      p(rad, a0 + x / rad);
    }
    p(r, a0 + hw); p(r, a0 + Math.PI / n);
  }
  return 'M ' + pts.join(' L ') + ' Z';
}
/* Градиенты ручки под форму: id+'sh' тёмная тень, id+'hl' светлая,
   id+'gl' мягкий блик, id+'bv' острый скос. */
function knobDefs(id, F, lt){
  var dark = F.depth === 'neumo' ? F.shd : '#000';
  var so = F.depth === 'neumo' ? .7 : (lt ? .22 : .5);
  return '<radialGradient id="' + id + 'sh"><stop offset=".7" stop-color="' + dark + '" stop-opacity="' + so + '"/>'
    + '<stop offset="1" stop-color="' + dark + '" stop-opacity="0"/></radialGradient>'
    + '<radialGradient id="' + id + 'hl"><stop offset=".7" stop-color="' + F.neu + '" stop-opacity=".95"/>'
    + '<stop offset="1" stop-color="' + F.neu + '" stop-opacity="0"/></radialGradient>'
    + '<linearGradient id="' + id + 'gl" x1="0" y1="0" x2="1" y2="1">'
    + '<stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset=".55" stop-color="#fff" stop-opacity="0"/>'
    + '<stop offset="1" stop-color="#000" stop-opacity=".16"/></linearGradient>'
    + '<linearGradient id="' + id + 'bv" x1="0" y1="0" x2="1" y2="1">'
    + '<stop offset="0" stop-color="#fff" stop-opacity=".6"/><stop offset=".47" stop-color="#fff" stop-opacity="0"/>'
    + '<stop offset=".53" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".38"/>'
    + '</linearGradient>';
}
/* Ручка целиком: тени по глубине, тело с краем, слой фаски. */
function knobSVG(id, cx, cy, r, k, face, stroke, F, dy){
  var h = '', o = (dy * 1.3 + 2).toFixed(1), rr = (r * 1.14).toFixed(1);
  if(F.depth === 'neumo'){
    h += '<circle cx="' + (cx - o) + '" cy="' + (cy - o) + '" r="' + rr + '" fill="url(#' + id + 'hl)"/>'
       + '<circle cx="' + (cx + +o) + '" cy="' + (cy + +o) + '" r="' + rr + '" fill="url(#' + id + 'sh)"/>';
  } else if(F.depth !== 'flat'){
    h += '<circle cx="' + cx + '" cy="' + (cy + dy) + '" r="' + (r * 1.16).toFixed(1) + '" fill="url(#' + id + 'sh)"/>';
  }
  var d = gearD(cx, cy, r, k, F.edge);
  h += '<path d="' + d + '" fill="' + face + '" stroke="' + stroke + '"/>';
  if(F.bevel === 'round') h += '<path d="' + d + '" fill="url(#' + id + 'gl)"/>';
  else if(F.bevel === 'sharp')
    h += '<path d="' + d + '" fill="url(#' + id + 'bv)"/>'
       + '<circle cx="' + cx + '" cy="' + cy + '" r="' + (r * 0.8).toFixed(1) + '" fill="' + face + '" stroke="' + stroke + '"/>';
  return h;
}
function openOv(nodes){ 
  // Во весь экран, сплошным цветом поля панели; у светлых тем - светлое.
  var lt = ovLight(), pc = ovPal();
  ov.classList.toggle('lt', lt);
  // Цвета темы - переменными на окне: ими красятся кнопки пультов (1.27).
  if(ov.style.setProperty){
    ov.style.setProperty('--acc', pc.acc); ov.style.setProperty('--acc2', pc.acc2);
    ov.style.setProperty('--acc-ink', pc.accInk);
    ov.style.setProperty('--heat', pc.heat); ov.style.setProperty('--cool', pc.cool);
  }
  // Форма темы (1.35): кнопки пультов - как ручки и клавиши плиток.
  var fm = FORM();
  ov.classList.toggle('neu', fm.depth === 'neumo');
  ov.classList.toggle('flat', fm.depth === 'flat');
  ov.classList.toggle('bev-none', fm.bevel === 'none');
  ov.classList.toggle('bev-sharp', fm.bevel === 'sharp');
  if(ov.style.setProperty){
    var nl = hexRgb(fm.neu), nd = hexRgb(fm.shd);
    ov.style.setProperty('--neu-l', 'rgba(' + nl.join(',') + ',.9)');
    ov.style.setProperty('--neu-d', 'rgba(' + nd.join(',') + ',.75)');
    ov.style.setProperty('--ovbg', PAGE_BG[rootTheme()] || '#101012');
  }
  // у стекла (1.40) под окном - те же обои, что под панелью
  ov.style.background = PAGE_WALL[rootTheme()] || PAGE_BG[rootTheme()] || '#101012';
  // Прежний заголовок-строка (.ttl) - та же шапка, что у пультов: «назад»
  // слева, название рядом. Так выход во всех окнах один и тот же (1.35).
  nodes = nodes.map(function(n){
    return n.classList && n.classList.contains('ttl') ? padHead(n.textContent) : n; });
  ov.classList.toggle('hashead', nodes.some(function(n){
    return n.classList && n.classList.contains('phead'); }));
  // Своя кнопка «назад» есть у пульта телевизора; остальным окнам без
  // шапки - круглая «назад» слева сверху, там же, где в шапке (с 1.35).
  ov.classList.toggle('ownback', nodes.some(function(n){
    return n.classList && n.classList.contains('tvx'); }));
  while(ov.children.length>1) ov.removeChild(ov.lastChild);
  nodes.forEach(function(n){ ov.appendChild(n); });
  ov.classList.add('open'); paused=true;
  hintsUnderInfo();
  if(!ovHist && !STILL && window.history && history.pushState){
    try { history.pushState({ov: 1}, ''); ovHist = true; } catch(e) {}
  }
  // Фон окна закрывает только касание, начатое на нём уже после открытия.
  ovDownBg = false;
  // Нажатие по плитке, которое открыло окно, своё отработало: его таймер
  // долгого нажатия не должен сработать уже поверх окна.
  if(press){ clearTimeout(press.timer); press = null; }
  host.classList.add('shield');
  document.documentElement.classList.add('ovlock');
}
function el(tag, cls, text){ var e=document.createElement(tag);
  if(cls) e.className=cls; if(text) e.textContent=text; return e; }

/* Переключатель для пульта. Возвращает объект с текущим состоянием, чтобы
   ползунки знали, надо ли сперва включить прибор. */
/* Ряд «метка − число +».
   Короткое нажатие меняет на шаг, удержание разгоняется: первые полсекунды
   по одному, дальше быстрее. Отправка отложена на 300 мс после последнего
   нажатия - иначе на каждый процент уходила бы своя команда, и прибор
   дёргался бы за пальцем, как штора при перетаскивании. */
function stepRow(label, opts){
  var row = el('div', 'step');
  var lab = el('span', 'lab', label);
  var minus = el('button'), num = el('span', 'num'), plus = el('button');
  minus.textContent = '−'; plus.textContent = '+';
  // Кнопки рядом, а не по краям числа: большой палец дотягивается до обеих
  // не переставляя руку, и подкрутить на пару процентов можно одной рукой.
  var keys = el('div', 'keys');
  keys.appendChild(minus); keys.appendChild(plus);
  row.appendChild(lab); row.appendChild(num); row.appendChild(keys);

  function paint(){ num.textContent = opts.read() + (opts.unit || ''); }
  paint();
  opts.paint = paint;
  stepKeys(minus, plus, opts);
  row.repaint = paint;
  row.el = row;
  return row;
}


/* Кнопки шага − / +: короткое нажатие - шаг, удержание разгоняется.
   Отправка отложена на 300 мс после последнего нажатия. Общая для ряда
   «метка − число +» и для кнопок вокруг шара и ручки (1.23). */
function stepKeys(minus, plus, opts){
  var lo = opts.min === undefined ? 0 : opts.min;
  var hi = opts.max === undefined ? 100 : opts.max;
  var timer = null, hold = null, pending = null;
  function flush(){
    pending = null;
    Promise.resolve(opts.send()).catch(function(e){
      flash('Не удалось: ' + e.message);
    });
  }
  function bump(d){
    var v = Math.max(lo, Math.min(hi, opts.read() + d));
    if(v === opts.read()) return;
    opts.write(v);
    if(opts.paint) opts.paint();
    if(pending) clearTimeout(pending);
    pending = setTimeout(flush, opts.delay || 300);
  }
  function press(d, elm){
    return function(ev){
      ev.preventDefault();
      elm.classList.add('down');
      bump(d);
      var fast = 0;
      hold = setTimeout(function(){
        timer = setInterval(function(){
          fast += 1;
          bump(d * (fast > 8 ? 5 : 1));
        }, 110);
      }, 500);
    };
  }
  function release(){
    if(hold) clearTimeout(hold);
    if(timer) clearInterval(timer);
    hold = timer = null;
    minus.classList.remove('down'); plus.classList.remove('down');
  }
  var st = opts.step || 1;
  ['mousedown','touchstart'].forEach(function(e){
    minus.addEventListener(e, press(-st, minus), {passive:false});
    plus.addEventListener(e, press(st, plus), {passive:false});
  });
  ['mouseup','mouseleave','touchend','touchcancel'].forEach(function(e){
    minus.addEventListener(e, release);
    plus.addEventListener(e, release);
  });
}

/* Шапка пульта (1.23), как у кондиционера: «назад» слева, название и
   состояние посередине, питание справа. Крестик окна при ней не нужен. */
var HICON = {
  back:  '<path d="M15 5l-7 7 7 7"/>',
  power: '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
  info:  '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.6v.1"/>'
};
/* Кнопка «i» (с 1.35): пояснения на весь экран не на виду, а по нажатию.
   Выбор помнится до перезагрузки страницы. */
var INFO_OPEN = false;
function infoBtn(onToggle){
  var b = hbtn('hinfo' + (INFO_OPEN ? ' on' : ''), hsvg(HICON.info));
  b.onclick = function(){
    INFO_OPEN = !INFO_OPEN; b.classList.toggle('on', INFO_OPEN); onToggle(INFO_OPEN);
  };
  return b;
}
/* Подсказки пультов («Крутите шар…») - под «i» в шапке. Окно без шапки
   показывает их, как раньше. */
function hintsUnderInfo(){
  var head = ov.querySelector('.phead');
  var hints = ov.querySelectorAll('.hint');
  if(!head || !hints.length || head.querySelector('.hinfo')) return;
  function show(on){
    for(var i = 0; i < hints.length; i++) hints[i].style.display = on ? '' : 'none';
  }
  show(INFO_OPEN);
  head.insertBefore(infoBtn(show), head.lastChild);
}
function hbtn(cls, html, fn){
  var b = el('div', 'hbtn' + (cls ? ' ' + cls : ''));
  if(html) b.innerHTML = html;
  if(fn) b.onclick = fn;
  return b;
}
function hsvg(path){ return '<svg viewBox="0 0 24 24">' + path + '</svg>'; }
function padHead(title, power){
  var h = el('div', 'phead');
  h.appendChild(hbtn('', hsvg(HICON.back), function(){ closeOv(); }));
  var nm = el('div', 'phname');
  nm.appendChild(el('div', 'phttl', title || ''));
  var sub = el('div', 'phsub');
  nm.appendChild(sub);
  h.appendChild(nm);
  h.appendChild(power || hbtn('ghost'));
  h.sub = sub;
  return h;
}

function powerRow(node, round){
  var st = {on: node.getAttribute('data-state') === '1'};
  // round - круглая кнопка для шапки пульта (1.23), иначе прежняя плашка
  var row = round ? hbtn('hpw', hsvg(HICON.power)) : el('div', 'pw');
  var led = el('span', 'led'), label = el('span');
  if(!round){ row.appendChild(led); row.appendChild(label); }
  function paint(){
    row.classList.toggle('on', st.on);
    if(!round) label.textContent = st.on ? 'Включено' : 'Выключено';
    if(st.onPaint) st.onPaint();
  }
  paint();
  row.onclick = function(){
    var topic = node.getAttribute('data-topic');
    if(!topic) return;
    var next = !st.on;
    pub(topic, next ? node.getAttribute('data-on') : node.getAttribute('data-off'))
      .then(function(){ st.on = next; paint(); })
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  };
  st.el = row;
  // включить прибор, если крутят яркость у выключенного
  st.ensureOn = function(){
    var topic = node.getAttribute('data-topic');
    if(st.on || !topic) return Promise.resolve();
    return pub(topic, node.getAttribute('data-on')).then(function(){
      st.on = true; paint();
    });
  };
  return st;
}
