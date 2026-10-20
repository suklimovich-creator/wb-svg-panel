/* wb-svg-panel: жесты на плитках (1.51; с 1.53 - как в пульте) - яркость,
   шторы и уставка пальцем прямо на панели, без пульта.
   Часть скриптов страницы панели (templates/wrapper.html). Обычный скрипт с
   общими глобальными именами; зовётся из обработчиков плиток (press.js).

   С 1.53 жест на плитке - тот же, что в пульте на весь экран, и считается
   от точки, куда поставили палец:
     диммер         вверх - ярче (колесо крутится за пальцем);
     лента (шар)    вверх-вниз - яркость, вбок - теплота (шар катится за пальцем);
     рулонная       вверх - поднять;
     раздвижная     вправо - открыть;
     термостат      ручку поворачивают по кругу, как в пульте;
     кондиционер    шкала едет за пальцем: влево - теплее;
     фанкойл        ползунок вверх - теплее.
   Страница на это время стоит: одним пальцем её не листают (касания
   принадлежат плиткам), листают двумя. */

var TD_KINDS = {dimmer: 1, light: 1, roller: 1, curtain: 1, thermostat: 1, ac: 1, fancoil: 1};
var TD_START = 6;       // сколько провести, чтобы это был жест, а не нажатие

function tdAxis(node){
  return dragOn() && TD_KINDS[node.getAttribute('data-kind')] ? 'xy' : null;
}
/* Класс страницы: с жестами одним пальцем она не листается (touch-action
   в panel.css), двумя - листается (tdTouches). Зовётся после каждой
   замены картинки. */
function tdMarks(){
  document.documentElement.classList.toggle('tdrag', dragOn());
}

function tdNum(node, name, def){
  var v = parseFloat(node.getAttribute(name));
  return isNaN(v) ? def : v;
}
/* Масштаб плитки на экране: точек экрана на единицу картинки, и где на
   экране её левый верхний угол. Подложка (.plate) - на единицу внутри края. */
function tdGeo(node){
  var plate = node.querySelector('.plate'), r = tdRect(node);
  var w = plate ? parseFloat(plate.getAttribute('width')) || r.width : r.width;
  var k = r.width / (w || 1);
  return {k: k, left: r.left - k, top: r.top - k, w: r.width / k + 2, h: r.height / k + 2, r: r};
}
function tdClamp(v, a, b){ return Math.max(a, Math.min(b, v)); }
function tdSnap(v, step){ return Math.round(v / step) * step; }

/* Что меняет жест на плитке этого вида и как: at(dx, dy, pt) по смещению
   пальца от точки касания (в точках экрана) ставит m.v (и m.x у ленты). */
function tdModel(node){
  var kind = node.getAttribute('data-kind'), G = tdGeo(node);
  if (kind === 'dimmer') {
    var l0 = tdNum(node, 'data-level', 0), bmax = tdNum(node, 'data-bright-max', 100);
    // полный ход - полторы высоты плитки, как полторы высоты колеса в пульте
    var span = Math.max(200, G.r.height * 1.5);
    return {kind: 'level', v: l0, v0: l0, min: 0, max: 1, label: 'яркость', axis: 'y',
            text: function(v){ return Math.round(v * 100) + ' %'; },
            at: function(dx, dy){ this.v = tdClamp(l0 - dy / span, 0, 1); },
            send: function(){ var v = this.v;
              return tdPowerOn(node).then(function(){
                return pub(node.getAttribute('data-bright'), Math.round(v * bmax)); }); }};
  }
  if (kind === 'light') {
    var lv0 = 1 - tdNum(node, 'data-dot-y', .5), x0 = tdNum(node, 'data-dot-x', .5);
    var lmax = tdNum(node, 'data-bright-max', 100), tTopic = node.getAttribute('data-temp');
    // шар катится за пальцем, как в пульте: смещение в радиусах шара
    var body = node.querySelector('.sphere circle[fill*="sb"]');
    var rr = Math.max(40, body ? body.getBoundingClientRect().width / 2 : G.r.width / 3);
    return {kind: 'level', v: lv0, v0: lv0, x: x0, x0: x0, min: 0, max: 1, label: 'яркость', axis: 'y',
            text: function(v){ return Math.round(v * 100) + ' %'; },
            at: function(dx, dy){
              this.v = tdClamp(lv0 - dy / rr / 2.6, 0, 1);
              if (tTopic) this.x = tdClamp(x0 + dx / rr / 3.2, 0, 1);
            },
            send: function(){
              var v = this.v, x = this.x, xs = this.x0;
              return tdPowerOn(node).then(function(){
                var jobs = [pub(node.getAttribute('data-bright'), Math.round(v * lmax))];
                if (tTopic && x !== xs) {
                  var unit = node.getAttribute('data-temp-unit'), tmax = tdNum(node, 'data-temp-max', 100);
                  var lo = tdNum(node, 'data-temp-lo', 2700), hi = tdNum(node, 'data-temp-hi', 6500);
                  jobs.push(pub(tTopic, unit === 'percent' ? Math.round((1 - x) * tmax) : Math.round(hi - x * (hi - lo))));
                }
                return Promise.all(jobs);
              });
            }};
  }
  if (kind === 'roller' || kind === 'curtain') {
    var p0 = tdNum(node, 'data-pos', 0), vert = kind === 'roller';
    // рулонная - полотно за пальцем по высоте плитки; створки - от центра к краю
    var run = vert ? Math.max(120, G.r.height) : Math.max(80, G.r.width / 2);
    return {kind: 'level', v: p0, v0: p0, min: 0, max: 1, label: 'открыто', slow: true, axis: vert ? 'y' : 'x',
            text: function(v){ return Math.round(v * 100) + ' %'; },
            at: function(dx, dy){ this.v = tdClamp(p0 + (vert ? -dy : dx) / run, 0, 1); },
            send: function(){ return pub(node.getAttribute('data-topic'), Math.round(this.v * 100)); }};
  }
  if (kind === 'thermostat') {
    var t = tdNum(node, 'data-target', NaN);
    if (isNaN(t)) return null;
    var m = tdSetpoint(t, tdNum(node, 'data-step', .5), tdNum(node, 'data-min', 5),
                       tdNum(node, 'data-max', 35), node.getAttribute('data-topic'));
    // Ручку поворачивают по кругу, как в пульте: угол пальца вокруг её оси
    // (геометрия skeuo.knob_dial: центр на 0,585 высоты). Поворот считается
    // от точки касания, а не прыжком в неё.
    var lo = tdNum(node, 'data-scale-min', 14), hi = tdNum(node, 'data-scale-max', 30);
    var sweep = tdNum(node, 'data-dial-sweep', 244);
    var cx = G.left + G.w / 2 * G.k, cy = G.top + G.h * 0.585 * G.k;
    var rad = 0.33 * Math.min(G.w, G.h) * G.k;
    var turn = 0, last = null;
    m.axis = 'r';
    m.at = function(dx, dy, pt){
      var ax = pt.clientX - cx, ay = pt.clientY - cy;
      if (Math.sqrt(ax * ax + ay * ay) < rad * 0.25) {
        // у самой оси угол скачет - там ведём вверх-вниз
        this.v = tdClamp(tdSnap(t - dy / 22 * this.step, this.step), this.min, this.max);
        last = null; return;
      }
      var a = Math.atan2(ay, ax) * 180 / Math.PI;
      if (last !== null) {
        var d = a - last;
        if (d > 180) d -= 360; else if (d < -180) d += 360;
        turn += d;
      }
      last = a;
      this.v = tdClamp(tdSnap(t + turn / sweep * (hi - lo), this.step), this.min, this.max);
    };
    return m;
  }
  if (kind === 'ac') {
    var a0 = tdNum(node, 'data-ac-now', NaN);
    if (isNaN(a0) || !node.getAttribute('data-ac-target')) return null;
    var ma = tdSetpoint(a0, tdNum(node, 'data-ac-step', 1), tdNum(node, 'data-ac-lo', 16),
                        tdNum(node, 'data-ac-hi', 30), node.getAttribute('data-ac-target'));
    // Шкала едет за пальцем, как колесо в пульте: влево - теплее. Градус -
    // расстояние между подписями шкалы на плитке (34 единицы картинки).
    var lab = node.querySelector('.acw-lab');
    var fs = lab ? parseFloat(lab.getAttribute('font-size')) / (lab.classList.contains('sel') ? 13 : 11) : 1;
    var pxdeg = Math.max(18, 34 * (fs || 1) * G.k);
    ma.axis = 'x';
    ma.at = function(dx){ this.v = tdClamp(tdSnap(a0 - dx / pxdeg, this.step), this.min, this.max); };
    return ma;
  }
  if (kind === 'fancoil') {
    var f0 = tdNum(node, 'data-ac-now', NaN);
    if (isNaN(f0) || !node.getAttribute('data-ac-target')) return null;
    var mf = tdSetpoint(f0, tdNum(node, 'data-ac-step', .5), tdNum(node, 'data-ac-lo', 16),
                        tdNum(node, 'data-ac-hi', 30), node.getAttribute('data-ac-target'));
    // ползунок, как в пульте: вверх - теплее, по шкале самой плитки
    var FG = node.querySelector('[data-fcl]'), D = FG && typeof fcData === 'function' ? fcData(FG) : null;
    var ctm = FG && FG.getScreenCTM ? FG.getScreenCTM() : null;
    var perPx = D && ctm && ctm.d ? (D.smax - D.smin) / ((D.bot - D.top) || 1) / ctm.d : 1 / 22 * mf.step;
    mf.axis = 'y';
    mf.at = function(dx, dy){ this.v = tdClamp(tdSnap(f0 - dy * perPx, this.step), this.min, this.max); };
    return mf;
  }
  return null;
}
function tdSetpoint(t, step, lo, hi, topic){
  var dec = step < 1 ? (String(step).split('.')[1] || '').length : 0;
  return {kind: 'set', v: t, v0: t, min: lo, max: hi, step: step, label: 'уставка',
          text: function(v){ return v.toFixed(dec).replace('.', ',') + '°'; },
          send: function(){ return pub(topic, this.v.toFixed(dec)); }};
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
/* Плашка над плиткой - запасной вид, если рисунок плитки не узнан
   (live-tiles.js вернул null). */
var tdBox = null, tdHide = null;
function tdShow(g, node){
  if (!tdBox) {
    tdBox = document.createElement('div'); tdBox.id = 'tdrag';
    tdBox.innerHTML = '<i></i><b></b><small></small>';
    document.body.appendChild(tdBox);
  }
  clearTimeout(tdHide);
  var r = tdRect(node), th = THEME_INFO[rootTheme()] || {};
  var axis = g.m.axis === 'y' ? 'y' : 'x';
  tdBox.style.left = Math.round(r.left) + 'px'; tdBox.style.top = Math.round(r.top) + 'px';
  tdBox.style.width = Math.round(r.width) + 'px'; tdBox.style.height = Math.round(r.height) + 'px';
  tdBox.style.setProperty('--tacc', th.acc || '#E09B2D');
  // на узкой плитке (половина клетки) число мельче, чтобы не переносилось
  tdBox.childNodes[1].style.fontSize = Math.round(Math.max(18, Math.min(30, r.width / 4))) + 'px';
  tdBox.className = 'on ' + axis;
  var m = g.m, f = (m.v - m.min) / ((m.max - m.min) || 1);
  var bar = tdBox.firstChild;
  if (axis === 'y') { bar.style.height = Math.round(f * 100) + '%'; bar.style.width = ''; }
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
    if (p.axis === undefined) p.axis = tdAxis(node);
    if (!p.axis) { p.noDrag = true; return false; }
    // страница с жестами стоит - касание принадлежит плитке сразу
    p.claim = true;
    if (Math.abs(dx) < TD_START && Math.abs(dy) < TD_START) return false;
    var m = tdModel(node);
    if (!m) { p.noDrag = true; return false; }
    clearTimeout(p.timer);
    p.moved = true;
    // палец ушёл за край плитки - движения всё равно её
    try { if (e.pointerId != null && node.setPointerCapture) node.setPointerCapture(e.pointerId); } catch (_) {}
    p.drag = {m: m, x: p.x, y: p.y,
              live: liveSender(function(){ return m.send(); }, m.slow ? 700 : liveMs()),
              // живая плитка (1.52): рисунок самой плитки идёт за пальцем;
              // не вышло (другой вид, нестандартная разметка) - плашка
              lt: liveTile(node, m)};
  }
  var g = p.drag, m2 = g.m;
  m2.at(pt.clientX - g.x, pt.clientY - g.y, pt);
  tdPaint(g, node);
  // Уставку - только по отпускании: прибору важен итог, а не путь.
  if (m2.kind !== 'set') g.live.tick();
  return true;
}
function tdChanged(m){ return m.v !== m.v0 || (m.x !== undefined && m.x !== m.x0); }
/* Палец отпущен после жеста: последнее значение - обязательно и после
   всех, что уже в пути. */
function tdEnd(p, node){
  var g = p && p.drag;
  if (!g) return false;
  var m = g.m;
  tdPaint(g, node, true);
  if (tdChanged(m)) {
    // Живая плитка остаётся такой, какой её довели, пока прибор не
    // ответит (hold, update.js); плашка - гаснет, плитка бледнеет до ответа.
    if (g.lt) hold(node); else node.classList.add('busy');
    g.live.end()
      .then(function(){ setTimeout(refresh, 500); setTimeout(refresh, 2000); })
      .then(null, function(e){ flash('Не удалось: ' + e.message); })
      .then(function(){ node.classList.remove('busy'); });
  } else if (g.lt) {
    // провели и вернули на место - плитку рисует сервер, как была
    redraw(node);
  }
  if (!g.lt) tdGone(1200);
  return true;
}
/* Жест прерван (второй палец - это прокрутка): ничего не шлём, плитку
   возвращаем к тому, что знает сервер. */
function tdAbort(p, node){
  var g = p && p.drag;
  if (!g) return;
  if (g.raf) (window.cancelAnimationFrame || clearTimeout)(g.raf);
  if (g.lt) redraw(node); else tdGone(0);
}
/* Перерисовка - не чаще кадра: на старом планшете касания приходят чаще,
   чем он успевает рисовать, и без этого рисунок отставал рывками. now -
   сразу (отпускание). */
function tdPaint(g, node, now){
  function paint(){
    g.raf = 0;
    if (g.lt) { try { g.lt.set(g.m.v, g.m.x); return; } catch (e) { g.lt = null; } }
    tdShow(g, node);
  }
  if (now) { if (g.raf) { (window.cancelAnimationFrame || clearTimeout)(g.raf); } paint(); return; }
  if (!g.raf) g.raf = (window.requestAnimationFrame || setTimeout)(paint);
}
/* Касание по плитке с жестом: браузер не должен листать страницу (старые
   браузеры без PointerEvent и Safari слушают только касания). */
function tdTouchMove(e){
  if (press && press.node === this && (press.drag || press.claim) && e.cancelable) e.preventDefault();
}

/* Прокрутка двумя пальцами (1.53): с жестами на плитках страница одним
   пальцем не листается. Второй палец прерывает жест на плитке. */
var tdTwo = null;
function tdTouches(e){
  if (!dragOn() || !host.contains(e.target)) { tdTwo = null; return; }
  var t = e.touches;
  if (t && t.length >= 2) {
    var y = (t[0].clientY + t[1].clientY) / 2, x = (t[0].clientX + t[1].clientX) / 2;
    if (!tdTwo) {
      tdTwo = {x: x, y: y};
      if (press) { clearTimeout(press.timer); tdAbort(press, press.node); press = null; }
    } else {
      window.scrollBy(tdTwo.x - x, tdTwo.y - y);
      tdTwo.x = x; tdTwo.y = y;
      lastScroll = Date.now();
    }
    if (e.cancelable) e.preventDefault();
  } else if (tdTwo && (!t || t.length < 2)) {
    tdTwo = null;
  } else if (e.type === 'touchmove' && e.cancelable) {
    // один палец мимо плиток (между ними, по заголовку) - тоже не листаем
    e.preventDefault();
  }
}
['touchstart', 'touchmove', 'touchend', 'touchcancel'].forEach(function(n){
  document.addEventListener(n, tdTouches, {passive: false, capture: true});
});
