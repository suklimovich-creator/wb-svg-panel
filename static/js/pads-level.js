/* wb-svg-panel: пульты диммера, штор, термостата.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- пульт диммера: линейный ---------- */
function padLevel(node){
  if(node.getAttribute('data-look') === 'classic') return padLevelFlat(node);
  /* С 1.17: колесо, как на плитке, только крупно. Ведёшь вверх - ярче.
     Шаг относительный: колесо крутится за пальцем от того места, где
     было, а не прыгает в точку касания, - промахом не выкрутить на сто. */
  var W = Math.round(Math.min(window.innerWidth * 0.5, 200));
  var H = Math.round(Math.min(window.innerHeight * 0.5, 380));
  var box = el('div'); box.id = 'lvl'; box.className = 'roll';
  box.style.width = W + 'px'; box.style.height = H + 'px';
  var hint = el('div','hint','Крутите колесо вверх или вниз · кнопками − / + по проценту');
  // С 1.23: шапка как у кондиционера, под колесом «− число +».
  var power = powerRow(node, true);
  var head = padHead(node.getAttribute('data-title') || 'Яркость', power.el);
  power.onPaint = function(){ head.sub.textContent = power.on ? 'Включено' : 'Выключено'; };
  power.onPaint();
  var lvl = parseFloat(node.getAttribute('data-level') || '0'), PAL = ovPal();
  var NS = 'http://www.w3.org/2000/svg', KW = Math.round(W * 0.56), KH = H - 8;
  var KX = Math.round((W - KW - 22) / 2), KY = 4;
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  function mk(tag, a, parent){
    var e = document.createElementNS(NS, tag);
    for(var k in a) e.setAttribute(k, a[k]);
    (parent || svg).appendChild(e); return e;
  }
  mk('defs', {}).innerHTML = '<linearGradient id="lvcyl" x1="0" y1="0" x2="0" y2="1">'
    + '<stop offset="0" stop-color="#000" stop-opacity=".55"/><stop offset=".5" stop-color="#fff" stop-opacity=".1"/>'
    + '<stop offset="1" stop-color="#000" stop-opacity=".55"/></linearGradient>';
  mk('rect', {x: KX - 5, y: KY - 4, width: KW + 10, height: KH + 8, rx: 7, fill: PAL.slot,
              stroke: PAL.faceEdge});
  mk('rect', {x: KX, y: KY, width: KW, height: KH, rx: 3, fill: PAL.drum});
  var ribs = mk('g', {});
  mk('rect', {x: KX, y: KY, width: KW, height: KH, rx: 3, fill: 'url(#lvcyl)'});
  var BX = KX + KW + 18;
  mk('rect', {x: BX, y: KY, width: 5, height: KH, rx: 2.5, fill: PAL.track});
  var bar = mk('rect', {x: BX, width: 5, rx: 2.5, fill: PAL.acc});
  box.appendChild(svg);
  var turn = lvl * 9;            // поворот колеса в шагах рёбер, копится за пальцем
  function place(){
    var step = 15 / KH, n = Math.ceil(1.6 / step) + 1, ph = turn % 1, out = '';
    if(ph < 0) ph += 1;
    for(var k = -n; k <= n; k++){
      var th = (k + ph) * step;
      if(Math.abs(th) > 1.52) continue;
      var y = KY + (Math.sin(th) * .5 + .5) * KH, w = Math.cos(th);
      out += '<line x1="' + (KX + 4) + '" x2="' + (KX + KW - 4) + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1)
           + '" stroke="#000" stroke-width="' + (.8 + 3.2 * w).toFixed(2) + '" opacity="' + (.25 + .5 * w).toFixed(2) + '"/>'
           + '<line x1="' + (KX + 4) + '" x2="' + (KX + KW - 4) + '" y1="' + (y + 2 * w).toFixed(1) + '" y2="' + (y + 2 * w).toFixed(1)
           + '" stroke="#fff" stroke-width="' + (.8 * w).toFixed(2) + '" opacity="' + (.1 * w).toFixed(2) + '"/>';
    }
    ribs.innerHTML = out;
    bar.setAttribute('y', (KY + KH * (1 - lvl)).toFixed(1));
    bar.setAttribute('height', (KH * lvl).toFixed(1));
  }
  place();

  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max') || '100');
    return power.ensureOn()
      .then(function(){ return pub(node.getAttribute('data-bright'), Math.round(lvl * bmax)); })
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  var srow = el('div', 'vals'), sMinus = hbtn('', '', null), sPlus = hbtn('', '', null), sNum = el('div');
  sMinus.textContent = '−'; sPlus.textContent = '+';
  srow.appendChild(sMinus); srow.appendChild(sNum); srow.appendChild(sPlus);
  var step = {repaint: function(){ sNum.innerHTML = '<small>яркость</small>' + Math.round(lvl * 100) + ' %'; }};
  step.repaint();
  stepKeys(sMinus, sPlus, {
    read:  function(){ return Math.round(lvl * 100); },
    write: function(v){ turn += (v / 100 - lvl) * KH / 15; lvl = v / 100; place(); },
    send:  send,
    paint: step.repaint
  });
  openOv([head, box, srow, hint]);
  var y0 = 0, l0 = 0, t0 = 0;
  function begin(ev){ var p = evPoint(ev); y0 = p.clientY; l0 = lvl; t0 = turn; }
  function at(ev){
    var p = evPoint(ev);
    if(p.clientY === undefined) return;
    var dy = p.clientY - y0;
    // полный ход - полторы высоты колеса: точнее, чем «от низа до верха»
    lvl = Math.max(0, Math.min(1, l0 - dy / (KH * 1.5)));
    turn = t0 - dy / 15 * (H / KH);
    place(); step.repaint();
    live.tick();
  }
  // Отклик сразу (1.51, «Ручки шлют: сразу»): яркость уходит и по дороге.
  var live = liveSender(send);
  drag(box, at, function(){ live.end(); }, begin);
}

/* Прежний пульт диммера (look: classic): полоса, заполнение снизу. */
function padLevelFlat(node){
  var box = el('div'); box.id='lvl';
  box.style.width = Math.round(Math.min(window.innerWidth*0.5, 220)) + 'px';
  box.style.height = Math.round(Math.min(window.innerHeight*0.5, 380)) + 'px';
  var fill = el('div','fill'), val = el('div','val');
  box.appendChild(fill); box.appendChild(val);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Яркость');
  var hint = el('div','hint','Ведите вверх или вниз · стрелками по проценту');
  var power = powerRow(node);
  var lvl = parseFloat(node.getAttribute('data-level')||'0');
  function place(){ fill.style.height=(lvl*100)+'%'; val.textContent=Math.round(lvl*100)+' %'; }
  place();

  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    return power.ensureOn()
      .then(function(){ return pub(node.getAttribute('data-bright'), Math.round(lvl*bmax)); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }

  var step = stepRow('Яркость', {
    unit: ' %',
    read:  function(){ return Math.round(lvl*100); },
    write: function(v){ lvl = v/100; place(); },
    send:  send
  });

  openOv([title, box, step, power.el, hint]);
  function at(ev){
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    lvl = Math.max(0, Math.min(1, 1-(p.clientY-r.top)/r.height));
    place(); step.repaint();
  }
  drag(box, at, send);
}

/* Пульт шторы.
   Полоса задаёт положение, кнопки под ней - «закрыть / стоп / открыть».
   Пока палец ведёт, показываем то, что он выбрал; отпустил - отправляем
   одно значение. Промежуточные значения слать нельзя: у привода каждая
   команда это новая цель, и он дёргался бы за пальцем. */
function padCurtain(node, axis){
  /* Раскрытая штора - это та же плитка, только крупно: рисует её сервер тем
     же макросом, что и на панели. Своей вёрстки тут нет намеренно, иначе
     пришлось бы поддерживать два разных изображения одной вещи.
     Поверх картинки лежит прозрачный слой: ведём правую створку от центра
     к правому краю, как саму штору руками.

     axis = 'y' - то же самое для рулонной и римской: полотно ходит вверх,
     и жест повторяет его, а не створки, которых у неё нет. Отдельного
     пульта для этого не заводим - расходятся только ось и подписи. */
  var vert = axis === 'y';
  var i = node.getAttribute('data-i');
  // не больше плитки 4×4: на ноутбуке окно во всю ширину было плакатом
  var w = Math.min(Math.round(window.innerWidth * 0.90), 722);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 560);
  var url = 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h + themeQ();

  var card = el('div','card'); card.textContent = '…';
  var wrap = el('div'); wrap.id = 'crtwrap';
  var view = el('div'); view.id = 'crtview';
  if(vert) view.className = 'vert';
  var mark = el('div','mark');            // куда поедет
  var read = el('div','read');            // проценты крупно
  view.appendChild(mark); view.appendChild(read);
  wrap.appendChild(card); wrap.appendChild(view);

  var title = el('div','ttl', node.getAttribute('data-title') || 'Штора');
  var hint  = el('div','hint', vert ? 'Ведите нижний край полотна вверх и вниз'
                                    : 'Ведите правую створку от центра к краю');
  var pos   = parseFloat(node.getAttribute('data-pos')||'0');
  var shown = pos;
  /* Куда привод поехал по нашей команде. Держится на экране, пока он
     едет: у медленного привода факт по дороге не меняется вовсе, и без
     этой отметки после отпускания пальца не остаётся ничего - картинка
     та же, что была, как будто команда не ушла. */
  var goal = null;

  function paint(){
    // 0 - створка у центра (закрыто), 1 - у правого края (открыто);
    // у рулонной 0 - полотно внизу, 1 - у карниза
    var at = goal === null ? shown : goal;
    var svg = card.querySelector('svg');
    var rs = svg ? svg.getBoundingClientRect() : null;
    var rv = svg ? view.getBoundingClientRect() : null;
    if(vert){
      if(rs) mark.style.top = Math.round(rs.top - rv.top
                                         + (1 - at)*rs.height) + 'px';
      else   mark.style.top = ((1 - at)*100) + '%';
    } else if(rs){
      var left = rs.left - rv.left, mid = left + rs.width/2;
      mark.style.left = Math.round(mid + at*rs.width/2) + 'px';
    } else {
      mark.style.left = (50 + at*50) + '%';
    }
    read.textContent = Math.round(at*100) + ' %';
    var show = dragging || goal !== null;
    mark.style.display = read.style.display = show ? 'block' : 'none';
    mark.classList.toggle('going', !dragging && goal !== null);
  }
  var dragging = false;

  function load(){
    fetch(url + '&t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        card.innerHTML = svg;
      })
      .catch(function(e){ card.textContent = 'Не удалось: ' + e.message; });
  }
  load();

  var row = el('div','crow');
  function button(text, cls, fn){
    var b = el('span','cbtn'+(cls?' '+cls:''), text);
    b.onclick = fn; row.appendChild(b); return b;
  }

  /* Три перечитывания в первые шесть секунд - это мерка по GM64T, который
     доезжает за пару секунд. Привод помедленнее едет минуту, и к моменту
     последнего перечитывания ещё даже не тронулся: картинка застывала на
     старом положении до закрытия пульта.

     Поэтому после команды следим за плиткой на панели: у неё есть data-pos
     и data-moving. Меняется положение - перерисовываем картинку, встал
     привод - перестаём спрашивать. Панель при этом не трогаем: она под
     оверлеем и обновится сама, когда он закроется. */
  var key = node.getAttribute('data-topic');
  var poll = null, ticks = 0, seenMoving = false;
  function stopPoll(){
    if(poll){ clearInterval(poll); poll = null; }
    // Привод встал: дальше на экране правда, а не наша цель.
    if(goal !== null){ goal = null; shown = pos; paint(); }
  }
  function watch(){
    stopPoll(); ticks = 0; seenMoving = false;
    poll = setInterval(function(){
      // Предохранитель: привод мог не ответить вовсе, и таймер не должен
      // остаться тикать до конца сессии.
      if(++ticks > 40){ stopPoll(); return; }
      var q = Q();
      fetch(NAME + '.svg' + q + (q ? '&' : '?') + 't=' + Date.now(), {cache:'no-store'})
        .then(function(r){ return r.ok ? r.text() : null; })
        .then(function(text){
          if(!text) return;
          var doc = new DOMParser().parseFromString(text, 'image/svg+xml');
          var g = doc.querySelector('[data-topic="' + key + '"]');
          if(!g) return;
          var p = parseFloat(g.getAttribute('data-pos') || '0');
          if(Math.abs(p - pos) > 0.004){
            pos = p;
            if(!dragging && goal === null) shown = p;
            load();
          }
          // Между командой и первым «поехал» проходит несколько секунд:
          // у приводов за мостом это круг до устройства и обратно. Пока
          // движения не видели ни разу, даём три круга на раскачку.
          if(g.getAttribute('data-moving')) seenMoving = true;
          else if(seenMoving || ticks >= 7) stopPoll();
        })
        .catch(function(){});
    }, 3000);
  }
  ovCleanup = stopPoll;

  function send(value){
    var want = parseFloat(value);
    pub(node.getAttribute('data-topic'), value)
      .then(function(){
        if(!isNaN(want)){ goal = Math.max(0, Math.min(1, want / 100)); }
        setTimeout(load, 700); setTimeout(load, 2500);
        setTimeout(refresh, 800);
        watch(); paint();
      })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  button(vert ? 'Опустить' : 'Закрыть', '',
         function(){ send(node.getAttribute('data-off')); });
  /* Стоп есть не у всех приводов: у HomeKit-совместимых это отдельная
     характеристика, и топик задаётся в конфиге явно. Нет топика - нет кнопки:
     рисовать неработающую хуже, чем не рисовать вовсе. */
  var stopTopic = node.getAttribute('data-stop');
  if(stopTopic){
    button('Стоп', 'stop', function(){
      pub(stopTopic, node.getAttribute('data-stop-value') || '2')
        .then(function(){
          // Где именно он встанет, мы не знаем - показывать цель больше нечестно.
          goal = null; paint();
          setTimeout(load, 700); watch();
        })
        .catch(function(e){ flash('Не удалось: '+e.message); });
    });
  }
  button(vert ? 'Поднять' : 'Открыть', '',
         function(){ send(node.getAttribute('data-on')); });

  openOv([title, wrap, row, hint]);
  if(!ov.offsetHeight){ closeOv(); window.open(url, '_blank'); return; }

  function at(ev){
    // Считаем от самого рисунка: у карточки есть поля, и если брать её
    // границы, центр окна и центр жеста разъезжаются на ширину поля.
    var box = card.querySelector('svg') || view;
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    if(vert){
      // верх картинки = поднято настежь, низ = опущено до конца
      shown = Math.max(0, Math.min(1, 1 - (p.clientY - r.top) / r.height));
    } else {
      // центр картинки = створки сомкнуты, правый край = открыто настежь
      shown = Math.max(0, Math.min(1, (p.clientX - (r.left + r.width/2)) / (r.width/2)));
    }
    paint();
  }
  function begin(ev){ dragging = true; at(ev); }
  // Промежуточные значения по умолчанию не шлём: для привода каждое - новая
  // цель, и он дёргался бы за пальцем. С «Ручки шлют: сразу» (1.51) - не
  // чаще раза в 0,7 с, только положение, без слежки за приводом.
  var live = liveSender(function(){ return pub(node.getAttribute('data-topic'), Math.round(shown * 100)); }, 700);
  function done(){
    dragging = false;
    var v = Math.round(shown*100);
    live.end(function(){ send(v); });
  }
  drag(view, function(ev){ at(ev); if(dragging) live.tick(); }, done, begin);
}

/* Перетаскивание: pointer-события есть не везде, поэтому с запасным
   вариантом на мышь и касания. */
function drag(node, move, done, begin){
  var active = false;
  function start(e){
    active = true;
    try { if(e.pointerId != null && node.setPointerCapture) node.setPointerCapture(e.pointerId); }
    catch(_) {}
    if(begin) begin(e); else move(e);
    if(e.preventDefault) e.preventDefault();
  }
  function go(e){ if(active){ move(e); if(e.preventDefault) e.preventDefault(); } }
  function end(e){
    if(!active) return;
    active = false;
    // move() может споткнуться на кривом событии - но отправить значение
    // мы обязаны в любом случае, иначе жест пропадает впустую
    try { move(e); } catch(_) {}
    done();
  }
  /* Отмена жеста - это НЕ движение. Раньше здесь вызывался move() с событием
     pointercancel, у которого координат нет, и значение прыгало в край.
     Браузер шлёт отмену, стоит пальцу чуть сместиться по вертикали. */
  function cancel(){ if(!active) return; active = false; done(); }
  if(window.PointerEvent){
    node.addEventListener('pointerdown', start);
    node.addEventListener('pointermove', go);
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', cancel);
  } else {
    node.addEventListener('mousedown', start);
    document.addEventListener('mousemove', go);
    document.addEventListener('mouseup', end);
    node.addEventListener('touchstart', start);
    node.addEventListener('touchmove', go);
    node.addEventListener('touchend', end);
    node.addEventListener('touchcancel', cancel);
  }
}
