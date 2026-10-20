/* wb-svg-panel: жесты на плитках (1.51) - яркость, шторы и уставка пальцем
   прямо на панели, без пульта.
   Часть скриптов страницы панели (templates/wrapper.html). Обычный скрипт с
   общими глобальными именами; зовётся из обработчиков плиток (press.js). */

/* Ось жеста по виду плитки. Вверх-вниз - яркость и рулонная штора (вверх -
   ярче, выше); вбок - створки шторы и уставка (вправо - открыть, теплее).
   На длинной странице, которую листают, вертикальный жест спорил бы с
   прокруткой - там все жесты боковые. Для того и задумано: вкладки на
   стене, панель в один экран. */
var TD_AXIS = {dimmer: 'y', light: 'y', roller: 'y', curtain: 'x',
               thermostat: 'x', ac: 'x', fancoil: 'x'};
var TD_START = 6;       // сколько провести по оси, чтобы это был жест, а не нажатие

function pageScrolls(){
  var d = document.documentElement;
  return (d.scrollHeight || 0) - (window.innerHeight || 0) > 4;
}
function tdAxis(node){
  var a = TD_AXIS[node.getAttribute('data-kind')];
  if (!a || !dragOn()) return null;
  return a === 'y' && pageScrolls() ? 'x' : a;
}
/* Классы страницы для touch-action: браузер не должен листать там, где
   палец ведёт ручку плитки. Зовётся после каждой замены картинки. */
function tdMarks(){
  var cls = document.documentElement.classList, on = dragOn();
  cls.toggle('tdrag', on);
  // высоту страницы меряем, только когда жесты включены: это пересчёт вёрстки
  cls.toggle('tdrag-v', on && !pageScrolls());
}

function tdNum(node, name, def){
  var v = parseFloat(node.getAttribute(name));
  return isNaN(v) ? def : v;
}
/* Что и как меняет жест на плитке этого вида. */
function tdModel(node){
  var kind = node.getAttribute('data-kind');
  if (kind === 'dimmer' || kind === 'light') {
    var lvl = kind === 'light' ? 1 - tdNum(node, 'data-dot-y', .5) : tdNum(node, 'data-level', 0);
    var bmax = tdNum(node, 'data-bright-max', 100);
    return {kind: 'level', v: lvl, v0: lvl, min: 0, max: 1, label: 'яркость',
            text: function(v){ return Math.round(v * 100) + ' %'; },
            send: function(v){
              return tdPowerOn(node).then(function(){
                return pub(node.getAttribute('data-bright'), Math.round(v * bmax)); });
            }};
  }
  if (kind === 'roller' || kind === 'curtain') {
    var pos = tdNum(node, 'data-pos', 0);
    return {kind: 'level', v: pos, v0: pos, min: 0, max: 1, label: 'открыто', slow: true,
            text: function(v){ return Math.round(v * 100) + ' %'; },
            send: function(v){ return pub(node.getAttribute('data-topic'), Math.round(v * 100)); }};
  }
  if (kind === 'thermostat') {
    var t = tdNum(node, 'data-target', NaN);
    if (isNaN(t)) return null;
    return tdSetpoint(t, tdNum(node, 'data-step', .5), tdNum(node, 'data-min', 5),
                      tdNum(node, 'data-max', 35), node.getAttribute('data-topic'));
  }
  if (kind === 'ac' || kind === 'fancoil') {
    var a = tdNum(node, 'data-ac-now', NaN);
    if (isNaN(a) || !node.getAttribute('data-ac-target')) return null;
    return tdSetpoint(a, tdNum(node, 'data-ac-step', 1), tdNum(node, 'data-ac-lo', 16),
                      tdNum(node, 'data-ac-hi', 30), node.getAttribute('data-ac-target'));
  }
  return null;
}
function tdSetpoint(t, step, lo, hi, topic){
  var dec = step < 1 ? (String(step).split('.')[1] || '').length : 0;
  return {kind: 'set', v: t, v0: t, min: lo, max: hi, step: step, label: 'уставка',
          text: function(v){ return v.toFixed(dec).replace('.', ',') + '°'; },
          send: function(v){ return pub(topic, v.toFixed(dec)); }};
}
/* Выключенный свет включается вместе с первой яркостью - как в пульте. */
function tdPowerOn(node){
  var topic = node.getAttribute('data-topic');
  if (!topic || node.getAttribute('data-state') === '1' || node._tdOn) return Promise.resolve();
  node._tdOn = true;
  return pub(topic, node.getAttribute('data-on'));
}

/* Границы плитки - по её подложке: у части рисунков (свечение, тени)
   обрезанные края выходят за плитку, и рамка всей группы шире. */
function tdRect(node){
  var plate = node.querySelector('.plate') || node.querySelector('rect') || node;
  return plate.getBoundingClientRect();
}
/* Плашка над плиткой на время жеста: число крупно и полоса. Сама плитка
   перерисуется, когда ответит прибор. */
var tdBox = null, tdHide = null;
function tdShow(g, node){
  if (!tdBox) {
    tdBox = document.createElement('div'); tdBox.id = 'tdrag';
    tdBox.innerHTML = '<i></i><b></b><small></small>';
    document.body.appendChild(tdBox);
  }
  clearTimeout(tdHide);
  var r = tdRect(node), th = THEME_INFO[rootTheme()] || {};
  tdBox.style.left = Math.round(r.left) + 'px'; tdBox.style.top = Math.round(r.top) + 'px';
  tdBox.style.width = Math.round(r.width) + 'px'; tdBox.style.height = Math.round(r.height) + 'px';
  tdBox.style.setProperty('--tacc', th.acc || '#E09B2D');
  // на узкой плитке (половина клетки) число мельче, чтобы не переносилось
  tdBox.childNodes[1].style.fontSize = Math.round(Math.max(18, Math.min(30, r.width / 4))) + 'px';
  tdBox.className = 'on ' + g.axis;
  var m = g.m, f = (m.v - m.min) / ((m.max - m.min) || 1);
  var bar = tdBox.firstChild;
  if (g.axis === 'y') { bar.style.height = Math.round(f * 100) + '%'; bar.style.width = ''; }
  else { bar.style.width = Math.round(f * 100) + '%'; bar.style.height = ''; }
  tdBox.childNodes[1].textContent = m.text(m.v);
  tdBox.childNodes[2].textContent = m.kind === 'set' && m.v !== m.v0
    ? 'было ' + m.text(m.v0) : m.label;
}
function tdGone(delay){
  clearTimeout(tdHide);
  tdHide = setTimeout(function(){ if (tdBox) tdBox.className = ''; }, delay || 0);
}

/* Движение пальца по плитке. Возвращает true, если это жест: тогда нажатие
   не считается ни коротким, ни долгим. p - состояние нажатия из press.js. */
function tdMove(p, node, e){
  if (!p || p.noDrag) return !!(p && p.drag);
  var pt = evPoint(e);
  if (pt.clientX === undefined) return !!p.drag;
  var dx = pt.clientX - p.x, dy = pt.clientY - p.y;
  if (!p.drag) {
    var axis = p.axis === undefined ? (p.axis = tdAxis(node)) : p.axis;
    if (!axis) { p.noDrag = true; return false; }
    var along = axis === 'y' ? Math.abs(dy) : Math.abs(dx);
    var across = axis === 'y' ? Math.abs(dx) : Math.abs(dy);
    if (!along && !across) return false;
    // Первое же движение поперёк - это прокрутка или промах, не жест.
    if (across > along) { p.noDrag = true; return false; }
    p.claim = true;                                // не отдаём касание прокрутке
    if (along < TD_START) return false;
    var m = tdModel(node);
    if (!m) { p.noDrag = true; return false; }
    clearTimeout(p.timer);
    p.moved = true;
    // палец ушёл за край плитки - движения всё равно её
    try { if (e.pointerId != null && node.setPointerCapture) node.setPointerCapture(e.pointerId); } catch (_) {}
    var r = tdRect(node);
    // Полный ход - высота (ширина) плитки, но не меньше 160 точек: на
    // маленькой плитке иначе не попасть в нужный процент.
    var span = Math.max(160, axis === 'y' ? r.height : r.width);
    p.drag = {axis: axis, m: m, x: pt.clientX, y: pt.clientY, span: span,
              live: liveSender(function(){ return m.send(m.v); }, m.slow ? 700 : liveMs()),
              // живая плитка (1.52): рисунок самой плитки идёт за пальцем;
              // не вышло (другой вид, нестандартная разметка) - плашка
              lt: liveTile(node, m)};
  }
  var g = p.drag, m2 = g.m;
  var d = g.axis === 'y' ? -(pt.clientY - g.y) : (pt.clientX - g.x);
  if (m2.kind === 'set') {
    // уставка - шагами: шаг на каждые 22 точки
    var n = Math.round(d / 22);
    m2.v = Math.max(m2.min, Math.min(m2.max, m2.v0 + n * m2.step));
  } else {
    m2.v = Math.max(0, Math.min(1, m2.v0 + d / g.span));
  }
  tdPaint(g, node);
  // Уставку - только по отпускании: прибору важен итог, а не путь.
  if (m2.kind !== 'set') g.live.tick();
  return true;
}
/* Палец отпущен после жеста: последнее значение - обязательно и после
   всех, что уже в пути. */
function tdEnd(p, node){
  var g = p && p.drag;
  if (!g) return false;
  var m = g.m;
  tdPaint(g, node, true);
  if (m.v !== m.v0) {
    // Живая плитка остаётся такой, какой её довели, пока прибор не
    // ответит (hold, update.js); плашка - гаснет, плитка бледнеет до ответа.
    if (g.lt) hold(node); else node.classList.add('busy');
    g.live.end()
      .then(function(){ setTimeout(refresh, 500); setTimeout(refresh, 2000); })
      .then(null, function(e){ flash('Не удалось: ' + e.message); })
      .then(function(){ node.classList.remove('busy'); });
  }
  if (!g.lt) tdGone(1200);
  return true;
}
/* Перерисовка - не чаще кадра: на старом планшете касания приходят чаще,
   чем он успевает рисовать, и без этого рисунок отставал рывками. now -
   сразу (отпускание). */
function tdPaint(g, node, now){
  function paint(){
    g.raf = 0;
    if (g.lt) { try { g.lt.set(g.m.v); return; } catch (e) { g.lt = null; } }
    tdShow(g, node);
  }
  if (now) { if (g.raf) { (window.cancelAnimationFrame || clearTimeout)(g.raf); } paint(); return; }
  if (!g.raf) g.raf = (window.requestAnimationFrame || setTimeout)(paint);
}
/* Касание по плитке с жестом: если жест начат или идёт вдоль оси, браузер
   не должен листать страницу (старые браузеры без PointerEvent и Safari
   слушают только это). */
function tdTouchMove(e){
  if (press && press.node === this && (press.drag || press.claim) && e.cancelable) e.preventDefault();
}
