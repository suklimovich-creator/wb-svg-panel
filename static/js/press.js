/* wb-svg-panel: нажатия по плиткам: короткое, долгое, шаг уставки, мгновенный отклик.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- нажатия по плиткам ---------- */
function toggle(node){
  var value = node.getAttribute('data-state')==='1'
            ? node.getAttribute('data-off') : node.getAttribute('data-on');
  // У сцены состояния нет: правило сбрасывает канал раньше, чем панель
  // перерисуется, и подтвердить нажатие обновлением не получится. Поэтому
  // подсвечиваем сами - иначе кнопка кажется мёртвой и её жмут дважды.
  var scene = node.getAttribute('data-kind') === 'scene';
  var was = node.getAttribute('data-state') === '1';
  /* Рамка переключается сразу, не дожидаясь прибора: у реле ответ
     приходит за доли секунды, а у устройств за Sprut.hub - за две-три.
     Плитка притушена, пока прибор не подтвердил; подтверждение - это
     новая картинка плитки от сервера. Не подтвердил за HOLD_MS - плитку
     заменяет то, что сервер знает на самом деле, и рамка вернётся. */
  function flip(on){
    var rims = node.querySelectorAll('.plate,.rim');
    for (var k = 0; k < rims.length; k++) rims[k].classList.toggle('on', on);
    node.setAttribute('data-state', on ? '1' : '0');
  }
  if (!scene) { flip(!was); hold(node); }
  node.classList.add('busy');
  pub(node.getAttribute('data-topic'), value).then(function(){
    if(scene){
      node.classList.remove('busy');
      node.classList.add('fired');
      setTimeout(function(){ node.classList.remove('fired'); }, 700);
    }
    // Ответ прибора принесёт ждущий запрос. Старый сервер его не умеет -
    // тогда переспрашиваем сами, как раньше.
    if (!LIVE) { setTimeout(refresh, 400); setTimeout(refresh, 1200); }
  }).catch(function(e){
    flash('Не удалось: '+e.message);
    node.classList.remove('busy');
    if (!scene) flip(was);
  });
}

/* Узлы под селектор внутри root, включая сам root. После точечной замены
   плитки обработчики вешаются только на неё, а не на всю панель заново. */
function within(root, sel){
  var out = [];
  var m = root.matches || root.webkitMatchesSelector || root.msMatchesSelector;
  if (root !== host && m && m.call(root, sel)) out.push(root);
  var found = root.querySelectorAll(sel);
  for (var i = 0; i < found.length; i++) out.push(found[i]);
  return out;
}

/* Что видно по всей панели: кнопки шапки, вид разделов. После точечной
   замены - один раз на все новые части, а не на каждую (1.48): поиск по
   всей картинке на старом планшете стоит миллисекунды. */
function bindPanel(){
  document.documentElement.classList.toggle('svgnav', !!host.querySelector('[data-nav]'));
  // Вид разделов узнаём по разметке после каждой замены: точечная замена
  // тоже может принести другой ряд вкладок.
  SECT_MODE = host.querySelector('[data-tab="1"]') ? 'tabs'
            : host.querySelector('[data-accordion="1"]') ? 'accordion' : 'list';
  var had = HAS_SECTIONS;
  HAS_SECTIONS = host.querySelector('[data-section]') !== null || MODE !== '';
  if (had !== HAS_SECTIONS && !colsOpen) drawCols();
  tdMarks();
}
/* partOnly - только обработчики внутри root, без bindPanel(). */
function bind(root, partOnly){
  root = root || host;
  // Заголовки разделов работают и без interactive: сворачивание ничего
  // не включает и не выключает, это способ смотреть.
  var heads = within(root, '[data-headtap]');
  for (var hh = 0; hh < heads.length; hh++) heads[hh].onclick = showChrome;
  var navs = within(root, '[data-nav]');
  for (var nv = 0; nv < navs.length; nv++) navs[nv].onclick = navTap;
  var folds = within(root, '[data-section]');
  for (var f = 0; f < folds.length; f++) {
    folds[f].onclick = function(){ if (tapOk()) toggleSection(this); };
  }
  if (!partOnly) bindPanel();

  if(!INTERACTIVE) return;
  var nodes = within(root, '[data-topic],[data-expand],[data-pad],[data-href],[data-chart]');
  for(var i=0;i<nodes.length;i++){
    var n = nodes[i];
    // Плитка, у которой есть и полный экран, и пульт (фанкойл), не может
    // обойтись простым onclick: короткое нажатие раскрывает, долгое
    // открывает пульт. Поэтому она идёт общим путём с жестами ниже.
    if(n.getAttribute('data-expand') && !n.getAttribute('data-pad')){
      n.onclick=function(){ if(tapOk()) expand(this); }; continue; }
    // Чип строки состояния: короткое нажатие открывает график канала.
    // Долгого жеста у него нет - переключать нечего.
    if(n.getAttribute('data-chart')){
      n.onclick=function(){ if(tapOk()) chartOv(this); }; continue; }
    // короткое нажатие переключает, долгое открывает пульт
    // Короткое нажатие переключает, долгое открывает пульт.
    // Раньше случайные включения ловились из-за двух вещей: жест не
    // отменялся при сдвиге пальца (прокрутка засчитывалась как нажатие) и
    // порог был коротковат. Теперь следим за смещением и держим 600 мс.
    var down = function(e){
      // второй палец - прокрутка двумя пальцами (gestures.js), не нажатие
      if(tdTwo) return;
      var self = this, p = evPoint(e);
      // Зоны плюс/минус - прозрачные прямоугольники внутри плитки.
      // Запоминаем, по какой попали: к моменту отпускания цель может
      // смениться, если палец сдвинулся.
      var zone = e.target && e.target.getAttribute
                 ? e.target.getAttribute('data-zone') : null;
      // Касание, остановившее прокрутку, или сразу после закрытия окна -
      // не нажатие: ни короткого, ни долгого из него не выйдет.
      var t0 = Date.now();
      press = {node:self, long:false, zone:zone, x:p.clientX, y:p.clientY,
        moved:!tapOk(t0), t0:t0,
        timer:setTimeout(function(){
          if(press && !press.moved && tapOk(press.t0)){
            press.long = true; openPad(self);
          }
        }, 600)};
    };
    var moved = function(e){
      if(!press) return;
      // Жест на плитке (1.51): яркость, штора, уставка - пальцем по ней.
      if(press.node === this && tdMove(press, this, e)){
        if(e.type === 'touchmove' && e.cancelable) e.preventDefault();
        return;
      }
      if(press.claim && e.type === 'touchmove' && e.cancelable) e.preventDefault();
      var p = evPoint(e);
      if(Math.abs(p.clientX - press.x) > 12 || Math.abs(p.clientY - press.y) > 12){
        press.moved = true;
        clearTimeout(press.timer);
      }
    };
    var up = function(){
      if(!press) return;
      clearTimeout(press.timer);
      if(tdEnd(press, this)){ press = null; return; }
      var href = this.getAttribute('data-href');
      // Пока палец лежал, страницу прокрутили - листали, а не нажимали.
      if(!tapOk(press.t0)) press.moved = true;
      if(!press.long && !press.moved){
        if(href) { location.href = href + Q(); }
        // Колесо телевизора на плитке: стрелки, OK, «назад», «домой».
        else if(press.zone && press.zone.indexOf('tv:') === 0) tvKey(this, press.zone.slice(3));
        else if(press.zone) stepTarget(this, press.zone);
        // Термостат: по краям плитки шаг уставки, в центре - открыть пульт.
        // Режим короткое нажатие не трогает: случайно выключить отопление
        // касанием экрана нельзя, для этого есть кнопка на пульте.
        // Раскрытие стоит раньше переключения: у фанкойла есть и топик
        // питания (его берёт пульт), но выключать прибор промахом по
        // экрану нельзя - короткое нажатие только раскрывает плитку.
        else if(this.getAttribute('data-expand')) expand(this);
        else if(this.getAttribute('data-pad') === 'thermostat') openPad(this);
        // Камера: короткое нажатие открывает поток. Долгое ждать незачем -
        // переключать у неё нечего, и держать палец шесть десятых секунды
        // ради единственного действия было бы издевательством.
        else if(this.getAttribute('data-pad') === 'camera') openPad(this);
        // Телевизор: нажатие открывает пульт и ничего не переключает.
        // Случайное касание не должно выключать телевизор посреди фильма,
        // поэтому питание живёт только внутри пульта.
        else if(this.getAttribute('data-pad') === 'tv') openPad(this);
        // Список: нажатие открывает окно, на самой плитке отмечать нечего -
        // строка там в полтора десятка пикселей, промахнуться легко.
        else if(this.getAttribute('data-pad') === 'list') openPad(this);
        // Вода: протечка ничего не переключает, кран - тем более коротким
        // нажатием. Оба открывают окно; перекрыть воду - кнопкой в нём.
        else if(this.getAttribute('data-pad') === 'leak') openPad(this);
        else if(this.getAttribute('data-pad') === 'valve') openPad(this);
        // Расписание: смотреть, а не переключать - сразу окно.
        else if(this.getAttribute('data-pad') === 'agenda') openPad(this);
        else if(this.getAttribute('data-pad') === 'today') openPad(this);
        else if(this.getAttribute('data-topic')) toggle(this);
      }
      press = null;
    };
    var cancel = function(){ if(press){ clearTimeout(press.timer); tdEnd(press, this); press=null; } };
    if(window.PointerEvent){
      n.onpointerdown = down; n.onpointermove = moved;
      n.onpointerup = up; n.onpointercancel = cancel;
      // Safari и Chrome листают по касаниям, а не по pointer-событиям:
      // жест на плитке держит касание за собой здесь.
      n.ontouchmove = tdTouchMove;
    } else {
      n.onmousedown = down; n.onmousemove = moved;
      n.onmouseup = up; n.onmouseleave = cancel;
      n.ontouchstart = down; n.ontouchmove = moved;
      n.ontouchend = up; n.ontouchcancel = cancel;
    }
    // долгое нажатие не должно выделять текст и звать меню браузера
    n.oncontextmenu = function(e){ if(e.preventDefault) e.preventDefault(); return false; };
  }
}
/* Шаг уставки. Значения считает сервер и кладёт в data-up и data-down:
   браузер не должен знать ни шага привода, ни его пределов. После записи
   ждём подтверждения от прибора - следующий шаг возьмётся уже из него. */
function stepTarget(node, zone){
  var value = node.getAttribute(zone === 'up' ? 'data-up' : 'data-down');
  if(value === null) return;
  node.classList.add('busy');
  pub(node.getAttribute('data-topic'), value)
    .then(function(){ setTimeout(refresh, 500); setTimeout(refresh, 2000); })
    .catch(function(e){ flash('Не удалось: ' + e.message); })
    .then(function(){ node.classList.remove('busy'); });
}

/* Переключение режима: греет или выключен.
   Отдельной функцией, потому что зовётся из двух мест - с плитки коротким
   нажатием по центру и с пульта кнопкой. */
function toggleMode(node, after){
  var topic = node.getAttribute('data-mode-topic');
  if(!topic){ flash('Режим не настроен: нет command_topic_mode'); return; }
  var on = node.getAttribute('data-enabled') === '1';
  var value = node.getAttribute(on ? 'data-mode-off' : 'data-mode-on');
  node.setAttribute('data-enabled', on ? '0' : '1');
  pub(topic, value)
    .then(function(){
      setTimeout(function(){ refresh(true); }, 500);
      if(after) setTimeout(after, 700);
    })
    .catch(function(e){
      node.setAttribute('data-enabled', on ? '1' : '0');
      flash('Не удалось: ' + e.message);
    });
}

/* Пульт термостата.
   Повторяет плитку: та же дуга, две риски, число в центре, режим снизу.
   Рисуется здесь, а не запрашивается картинкой у сервера, - дуга должна
   отзываться на палец сразу, без похода по сети.

   Заливка идёт ОТ риски текущей температуры ДО уставки: это ровно тот
   разрыв, который прибору предстоит закрыть. Вверх - оранжевая, вниз -
   синяя: одинаковая дуга не должна означать разные вещи. */
function padThermostat(node){
  var lo = parseFloat(node.getAttribute('data-scale-min') || '14');
  var hi = parseFloat(node.getAttribute('data-scale-max') || '30');
  var step = parseFloat(node.getAttribute('data-step') || '0.5');
  var tlo = parseFloat(node.getAttribute('data-min') || '10');
  var thi = parseFloat(node.getAttribute('data-max') || '38');
  var digits = (step % 1 === 0) ? 0 : 1;
  var value = parseFloat(node.getAttribute('data-target') || '20');
  var current = parseFloat(node.getAttribute('data-current') || 'NaN');
  var live = node.getAttribute('data-live') === '1';
  var timer = null, dragging = false, PAL = ovPal();

  var S = Math.min(window.innerWidth - 40, Math.round(window.innerHeight * 0.62), 460);
  var wrap = el('div'); wrap.id = 'tdial';
  wrap.style.width = S + 'px'; wrap.style.height = S + 'px';

  var START = parseFloat(node.getAttribute('data-dial-start') || '148');
  var SWEEP = parseFloat(node.getAttribute('data-dial-sweep') || '244');
  /* С 1.17 пульт рисует ту же ручку, что плитка (wbpanel/skeuo.py): дорожка
     от начала шкалы до уставки, шкала с делениями снаружи, зубчатая ручка
     с цифрой в центре. Центр - там же, где его ждёт жест (55 % высоты). */
  var CX = S / 2, CY = S * 0.55, RA = S * 0.33, RK = RA - S * 0.093;
  var TW = Math.max(6, S * 0.025), LAB = Math.max(11, S * 0.034);
  var NS = 'http://www.w3.org/2000/svg';
  function pt(deg, radius){
    var a = deg * Math.PI / 180;
    return [CX + radius * Math.cos(a), CY + radius * Math.sin(a)];
  }
  function arc(fromDeg, toDeg, radius){
    if(Math.abs(toDeg - fromDeg) < 0.3) return '';
    var a = pt(fromDeg, radius), b = pt(toDeg, radius);
    var large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
    return 'M' + a[0].toFixed(1) + ' ' + a[1].toFixed(1) + 'A' + radius + ' ' + radius + ' 0 ' + large + ' 1 ' +
           b[0].toFixed(1) + ' ' + b[1].toFixed(1);
  }
  function degOf(v){
    var f = (v - lo) / (hi - lo);
    return START + Math.max(0, Math.min(1, f)) * SWEEP;
  }
  function mk(tag, attrs, parent){
    var e = document.createElementNS(NS, tag);
    for(var k in attrs) e.setAttribute(k, attrs[k]);
    (parent || svgEl).appendChild(e); return e;
  }
  var svgEl = document.createElementNS(NS, 'svg');
  svgEl.setAttribute('viewBox', '0 0 ' + S + ' ' + S);
  svgEl.setAttribute('width', S); svgEl.setAttribute('height', S);
  wrap.appendChild(svgEl);
  var FM = FORM();
  mk('defs', {}).innerHTML = knobDefs('td', FM, PAL.lt);
  // шкала: деления через градус, подписи через четыре
  var t0 = Math.ceil(lo), every = (Math.floor(hi) - t0) <= 40 ? 4 : 8;
  for(var v = t0; v <= Math.floor(hi); v++){
    var major = (v - t0) % every === 0, a = degOf(v);
    var p1 = pt(a, RA + S * 0.025), p2 = pt(a, RA + S * (major ? 0.057 : 0.043));
    mk('line', {x1: p1[0].toFixed(1), y1: p1[1].toFixed(1), x2: p2[0].toFixed(1), y2: p2[1].toFixed(1),
                stroke: PAL.ink, 'stroke-opacity': major ? .8 : .4, 'stroke-width': major ? 2 : 1.2, 'stroke-linecap': 'round'});
    if(major){
      var lp = pt(a, RA + S * 0.096 + LAB * 0.2);
      var tl = mk('text', {x: lp[0].toFixed(1), y: (lp[1] + LAB * 0.36).toFixed(1), 'text-anchor': 'middle',
                           fill: PAL.dim, 'font-size': LAB, 'font-weight': 600});
      tl.textContent = v;
    }
  }
  var pBg = mk('path', {fill: 'none', stroke: PAL.track, 'stroke-width': TW, 'stroke-linecap': 'round',
                        d: arc(START, START + SWEEP, RA)});
  var pArc = mk('path', {fill: 'none', 'stroke-width': TW, 'stroke-linecap': 'round'});
  var lCurH = mk('line', {stroke: PAL.halo, 'stroke-width': Math.max(6, S * 0.025), 'stroke-linecap': 'round'});
  var lCur = mk('line', {stroke: PAL.ink, 'stroke-width': Math.max(3, S * 0.0114), 'stroke-linecap': 'round'});
  // ручка в форме темы: тени, край, фаска (с 1.35)
  mk('g', {}).innerHTML = knobSVG('td', CX, CY, RK, Math.max(0.8, 0.8 * S / 280), PAL.face, PAL.faceEdge, FM, S * 0.018);
  var lPtr = mk('line', {'stroke-width': Math.max(3, S * 0.013), 'stroke-linecap': 'round'});
  var cKnob = mk('circle', {r: Math.max(6, S * 0.023), stroke: PAL.knobEdge, 'stroke-width': Math.max(2, S * 0.007)});
  var tBig = mk('text', {'text-anchor': 'middle', 'class': 'tbig'});
  var tSub = mk('text', {'text-anchor': 'middle', 'class': 'tsub'});
  var tMode = mk('text', {'text-anchor': 'middle', 'class': 'tmode'});

  function line(e, v, inner, outer){
    var a = pt(degOf(v), inner), b = pt(degOf(v), outer);
    e.setAttribute('x1', a[0].toFixed(1)); e.setAttribute('y1', a[1].toFixed(1));
    e.setAttribute('x2', b[0].toFixed(1)); e.setAttribute('y2', b[1].toFixed(1));
  }
  function paint(){
    // греет - оранжевая, включён и не греет - синяя, выключен - серая
    var enabled = node.getAttribute('data-enabled') !== '0';
    var color = !enabled ? PAL.off : (live ? PAL.heat : PAL.cool);
    var to = degOf(value);
    pArc.setAttribute('d', arc(START, to, RA));
    pArc.setAttribute('stroke', color);
    if(!isNaN(current)){
      line(lCurH, current, RA - S * 0.025, RA + S * 0.061);
      line(lCur, current, RA - S * 0.025, RA + S * 0.061);
      lCur.style.display = lCurH.style.display = '';
    } else lCur.style.display = lCurH.style.display = 'none';
    line(lPtr, value, RK - S * 0.043, RK - S * 0.011);
    lPtr.setAttribute('stroke', color);
    var k = pt(to, RA);
    cKnob.setAttribute('cx', k[0].toFixed(1)); cKnob.setAttribute('cy', k[1].toFixed(1));
    cKnob.setAttribute('fill', enabled ? PAL.knob : PAL.offKnob);

    tBig.setAttribute('x', CX); tBig.setAttribute('y', CY + S * 0.035);
    tBig.textContent = value.toFixed(digits) + '°';
    tBig.setAttribute('opacity', enabled ? 1 : .55);
    tSub.setAttribute('x', CX); tSub.setAttribute('y', CY + S * 0.095);
    tSub.textContent = isNaN(current) ? '' : ('сейчас ' + current.toFixed(1) + '°');
    tMode.setAttribute('x', CX); tMode.setAttribute('y', S - S * 0.03);
    tMode.textContent = node.getAttribute('data-status') || '';
    tMode.style.fill = enabled ? color : PAL.dim;
  }
  /* Отправляем последнее значение, а не каждое движение пальца: головка
     иначе выстроит десяток команд в очередь. */
  function send(){
    clearTimeout(timer);
    timer = setTimeout(function(){
      // Пока сценарий переваривает команду, серверная уставка ещё старая.
      // Держим свою цифру три секунды, дальше верим прибору.
      quietUntil = Date.now() + 3000;
      pub(node.getAttribute('data-topic'), value.toFixed(digits))
        .then(function(){ node.setAttribute('data-target', value.toFixed(digits));
                          setTimeout(function(){ refresh(true); }, 400);
                          setTimeout(function(){ refresh(true); sync(); }, 1500); })
        .catch(function(e){ flash('Не удалось: ' + e.message); });
    }, 350);
  }
  function shift(delta){
    value = Math.max(tlo, Math.min(thi, value + delta));
    paint(); send();
  }

  /* С 1.23: шапка как у кондиционера - «назад», название, питание (если
     задан канал режима); − и + у концов шкалы вместо ряда кнопок. */
  var power = null;
  if(node.getAttribute('data-mode-topic')){
    power = hbtn('hpw' + (node.getAttribute('data-enabled') === '1' ? ' on' : ''), hsvg(HICON.power), function(){
      toggleMode(node, function(){
        // Дальше состояние подтянет sync(): режим меняет прибор, и его
        // ответ может прийти позже нашего предположения.
        power.classList.toggle('on', node.getAttribute('data-enabled') === '1');
        paint();
      });
    });
  }
  var head = padHead(node.getAttribute('data-title') || 'Термостат', power);
  var box = el('div', 'stagebox');
  box.style.width = S + 'px'; box.style.height = S + 'px';
  box.appendChild(wrap);
  function corner(deg, text){
    var p = pt(deg, RA + S * 0.02);
    var b = hbtn('at', '', null); b.textContent = text;
    // чуть внутрь от конца дуги: снаружи там подписи 14 и 30
    var dx = (deg === START ? 1 : -1) * S * 0.055;
    b.style.left = (p[0] + dx).toFixed(1) + 'px'; b.style.top = (p[1] + S * 0.08).toFixed(1) + 'px';
    box.appendChild(b); return b;
  }
  var tMinus = corner(START, '−'), tPlus = corner(START + SWEEP, '+');
  stepKeys(tMinus, tPlus, {
    min: tlo, max: thi, step: step, delay: 350,
    read:  function(){ return value; },
    write: function(v){ value = Math.round(v / step) * step; quietUntil = Date.now() + 3000; },
    send:  function(){ send(); },
    paint: function(){ paint(); }
  });

  /* refresh() заменяет разметку панели целиком, и ссылка на плитку
     становится битой: обновления состояния до пульта не доходят вовсе.
     Поэтому после каждой перерисовки ищем плитку заново по её номеру и
     перечитываем то, что мог поменять сам прибор или сценарий. */
  var idx = node.getAttribute('data-i');
  var quietUntil = 0;
  function sync(){
    var fresh = host.querySelector('[data-pad="thermostat"][data-i="' + idx + '"]');
    if(!fresh) return;
    node = fresh;
    var c = parseFloat(node.getAttribute('data-current') || 'NaN');
    if(!isNaN(c)) current = c;
    live = node.getAttribute('data-live') === '1';
    // Уставку с сервера подхватываем, только если сами недавно не писали:
    // сценарий отвечает не мгновенно, и своё же значение перетёрло бы
    // цифру обратно на старую.
    if(Date.now() > quietUntil && !dragging){
      var t = parseFloat(node.getAttribute('data-target') || 'NaN');
      if(!isNaN(t)) value = t;
    }
    if(power) power.classList.toggle('on', node.getAttribute('data-enabled') === '1');
    paint();
  }
  var ticker = setInterval(function(){
    // Панель под окном обновляет ждущий запрос, и прибор оттуда виден
    // сразу. Со старым сервером просим свежую картинку сами.
    if (!LIVE) refresh(true);
    sync();
  }, 1200);

  paint();
  openOv([head, box]);
  ovCleanup = function(){ clearInterval(ticker); };

  function at(ev){
    var r = wrap.getBoundingClientRect();
    var p = evPoint(ev);
    var cx = r.left + r.width * 0.5, cy = r.top + r.height * 0.55;
    var deg = Math.atan2(p.clientY - cy, p.clientX - cx) * 180 / Math.PI;
    var rel = deg - START;
    while(rel < 0) rel += 360;
    while(rel > 360) rel -= 360;
    if(rel > SWEEP){
      // Палец в нижнем разрыве дуги: прижимаем к ближнему концу, иначе
      // уставка прыгает с максимума на минимум.
      rel = (rel - SWEEP < 360 - rel) ? SWEEP : 0;
    }
    var raw = lo + (hi - lo) * (rel / SWEEP);
    value = Math.max(tlo, Math.min(thi, Math.round(raw / step) * step));
    paint();
  }
  function begin(ev){ dragging = true; at(ev); }
  function done(){ dragging = false; send(); }
  drag(wrap, at, done, begin);
}
