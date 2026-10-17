/* wb-svg-panel: камера и пульт телевизора.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- камера ----------
   Поток MJPEG живёт в обычном <img>: браузер сам разбирает multipart и
   перерисовывает кадр за кадром. Никакого проигрывателя не нужно.

   Две тонкости, из-за которых тут больше кода, чем ожидаешь.

   Первая: сервер закрывает соединение через data-cam-life миллисекунд -
   иначе забытое окно держит поток waitress до тайм-аута TCP. Когда
   соединение закрылось, <img> не сообщает об этом ничем: он просто
   застывает на последнем кадре. Поэтому переподключаемся по таймеру
   сами, чуть раньше срока.

   Вторая: поток может не пойти вовсе - MJPEG выключен на камере,
   слотов не осталось, прошивка отвечает иначе. Тогда переходим на
   снимки: то же окно, та же картинка, просто дёргается. Показать
   человеку живой кадр важнее, чем настоять на способе. */
function padCamera(node){
  var stream = node.getAttribute('data-cam-live') === '1'
             ? node.getAttribute('data-cam') : '';
  var poll   = node.getAttribute('data-cam-poll');
  var life   = parseInt(node.getAttribute('data-cam-life') || '0', 10);
  var title  = (node.getAttribute('data-title') || '').replace(/\n/g, ' ');

  var card = el('div','card camwrap');
  var img  = document.createElement('img');
  img.className = 'cam';
  img.alt = '';
  card.appendChild(img);
  var ttl  = el('div','ttl', title);

  var alive = true, polling = false, timer = null, revive = null;

  function shot(){
    if(!alive) return;
    /* live=1 просит сервер не кешировать: на плитке кадр держится
       десять секунд намеренно, а здесь нужен свежий. */
    img.src = poll + '?live=1&t=' + Date.now();
  }
  function toPolling(){
    if(polling || !alive) return;
    polling = true;
    if(revive){ clearTimeout(revive); revive = null; }
    shot();
    timer = setInterval(shot, 700);
  }
  function connect(){
    if(!alive) return;
    img.src = stream + '?t=' + Date.now();
    if(life > 2000){
      revive = setTimeout(connect, life - 1500);
    }
  }

  /* Ошибка загрузки означает разное: у потока - что он не пошёл, у
     снимка - что камера прямо сейчас не ответила. В первом случае
     переключаемся, во втором просто ждём следующего кадра. */
  img.onerror = function(){
    if(!polling) toPolling();
  };

  openOv([ttl, card]);
  if(!ov.offsetHeight){ closeOv(); window.open(poll, '_blank'); return; }

  /* Пока панель обновляется в фоне, кадр камеры тянуть незачем: он и
     так перед глазами. Останавливаем на время просмотра. */
  ovCleanup = function(){
    alive = false;
    if(timer) clearInterval(timer);
    if(revive) clearTimeout(revive);
    /* Пустой src обрывает соединение с сервером немедленно. Без этого
       браузер держит его открытым ещё какое-то время, а на той стороне
       занят поток. */
    img.onerror = null;
    img.src = '';
  };

  if(stream) connect(); else toPolling();
}

/* ---------- пульт телевизора ---------- */
/* Кнопки не знают ни про Samsung, ни про коды клавиш: всё это живёт в
   мосте wb-tv. Пульт публикует единицу в контрол и на этом заканчивает
   свою работу - потому же он одинаково подойдёт телевизору другой марки.

   Значки рисуются встроенным SVG, а не символами юникода: «⏻» и эмодзи
   приглушения есть не во всех шрифтах, и на ноутбуке кнопка оказывалась
   пустой. */
var TVI = {
  power: '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
  up:    '<path d="M6 15l6-6 6 6"/>',
  down:  '<path d="M6 9l6 6 6-6"/>',
  left:  '<path d="M15 6l-6 6 6 6"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  /* Плей и пауза на одном значке: кнопка одна, и по ней должно быть
     видно, что она делает и то, и другое. Рисуется заливкой, поэтому
     полоски паузы - прямоугольники, а не линии: класс fill снимает
     обводку у всего значка разом. */
  playpause: '<path d="M3 5l8.5 7-8.5 7z"/>'
           + '<path d="M14.5 5h2.5v14h-2.5z"/><path d="M19.5 5h2.5v14h-2.5z"/>',
  rew:   '<path d="M11 6l-7 6 7 6z"/><path d="M20 6l-7 6 7 6z"/>',
  fwd:   '<path d="M13 6l7 6-7 6z"/><path d="M4 6l7 6-7 6z"/>',
  mute:  '<path d="M11 5L7 9H4v6h3l4 4z"/><path d="M16 9l5 6M21 9l-5 6"/>',
  input: '<path d="M4 6h16v10H4z"/><path d="M9 20h6"/>'
};

/* ---------- пульт телевизора (с 1.21) ----------
   Раскладка как у настоящего пульта (макет 15.10): сверху «назад», имя,
   вход и питание; колесо со стрелками и OK; «Назад» и «Меню» у нижних
   углов колеса; перемотка; громкость кнопками − / +, между ними без
   звука; приложения внизу. Карточка тёмная, в светлых темах светлая (1.22) - как пульт в
   руке. Стрелки и громкость при удержании повторяют нажатие. Прежний
   светлый пульт - look: classic. */
var TVX = {
  back: '<path d="M9 7L5 11l4 4"/><path d="M5 11h9a5 5 0 0 1 0 10h-3"/>',
  menu: '<path d="M5 5h5v5H5zM14 5h5v5h-5zM5 14h5v5H5zM14 14h5v5h-5z"/>',
  chev: '<path d="M15 5l-7 7 7 7"/>'
};
function tvGear(cx, cy, r, k){
  var tw = 7 * k, tth = 6 * k, n = Math.max(18, Math.round(2 * Math.PI * r / (16 * k)));
  var hw = tw / 2 / r, pts = [];
  function p(rad, a){ pts.push((cx + rad * Math.cos(a)).toFixed(1) + ',' + (cy + rad * Math.sin(a)).toFixed(1)); }
  for(var i = 0; i < n; i++){
    var a0 = 2 * Math.PI * i / n;
    p(r, a0 - hw);
    for(var f = 0; f < 9; f++){
      var ff = Math.PI * f / 8, x = -tw / 2 * Math.cos(ff);
      var rad = (r - tth + tw / 2) - (tw / 2) * Math.sin(ff);
      p(rad, a0 + x / rad);
    }
    p(r, a0 + hw); p(r, a0 + Math.PI / n);
  }
  return 'M ' + pts.join(' L ') + ' Z';
}
function padTV(node){
  if(node.getAttribute('data-look') === 'classic') return padTVFlat(node);
  var base = node.getAttribute('data-tv-base');
  if(!base) return;
  var link = node.getAttribute('data-tv-link') === '1';
  var on = node.getAttribute('data-tv-on') === '1';
  var dimmed = [], PAL = ovPal(), FM = FORM();

  function press(control){
    pub(base + control + '/on', 1)
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  function pressPower(){
    var want = on ? 0 : 1;
    pub(base + 'power/on', want).then(function(){
      on = !on; pw.classList.toggle('on', on);
      sub.textContent = on ? 'Включается…' : 'Выключается…';
      setTimeout(refresh, 800); setTimeout(refresh, 3000);
    }).catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  /* Нажатие с повтором: первое сразу, при удержании - через 450 мс и
     дальше каждые 160 мс. Для стрелок и громкости; OK, «Назад» и
     приложения не повторяются - дважды выбранное не отменить. */
  function hold(elm, fire, repeat){
    var t1 = null, t2 = null;
    function stop(){ clearTimeout(t1); clearInterval(t2); t1 = t2 = null; elm.classList.remove('down'); }
    function start(e){
      if(e && e.preventDefault) e.preventDefault();
      stop(); elm.classList.add('down'); fire(e);
      var again = typeof repeat === 'function' ? repeat() : repeat;
      if(again) t1 = setTimeout(function(){ t2 = setInterval(function(){ fire(e, true); }, 160); }, 450);
    }
    if(window.PointerEvent){
      elm.addEventListener('pointerdown', start);
      ['pointerup','pointercancel','pointerleave'].forEach(function(n){ elm.addEventListener(n, stop); });
    } else {
      elm.addEventListener('touchstart', start); elm.addEventListener('mousedown', start);
      ['touchend','touchcancel','mouseup','mouseleave'].forEach(function(n){ elm.addEventListener(n, stop); });
    }
    return elm;
  }
  function svgIcon(path, fill){
    return '<svg viewBox="0 0 24 24" class="' + (fill ? 'fl' : '') + '">' + path + '</svg>';
  }
  function key(cls, control, icon, opts){
    opts = opts || {};
    var b = el('div', 'tx ' + cls);
    if(icon) b.innerHTML = icon;
    if(control === 'power') b.onclick = pressPower;
    else hold(b, function(){ press(control); }, opts.repeat);
    if(opts.needs !== false) dimmed.push(b);
    return b;
  }
  function labeled(b, text){
    var w = el('div', 'txl'); w.appendChild(b); w.appendChild(el('div', 'txlab', text)); return w;
  }
  function setLive(live){
    link = live;
    for(var k = 0; k < dimmed.length; k++) dimmed[k].classList.toggle('tvdim', !live);
  }

  var card = el('div', 'card tvx');

  /* шапка */
  var head = el('div', 'txhead');
  var close = el('div', 'tx sm', ''); close.innerHTML = svgIcon(TVX.chev);
  close.onclick = function(){ closeOv(); };
  head.appendChild(close);
  var name = el('div', 'txname');
  name.appendChild(el('div', 'txttl', node.getAttribute('data-tv-title') || 'Телевизор'));
  var app = node.getAttribute('data-tv-app') || '';
  var sub = el('div', 'txsub', (node.getAttribute('data-tv-state') || '') + (app ? ' · ' + app : '')
                               + (link ? '' : ' · доступно только включение'));
  name.appendChild(sub);
  head.appendChild(name);
  head.appendChild(key('sm', 'source', svgIcon(TVI.input)));
  var pw = key('sm txpw' + (on ? ' on' : ''), 'power', svgIcon(TVI.power), {needs: false});
  head.appendChild(pw);
  card.appendChild(head);

  /* колесо: стрелки по сторонам, OK в центре. Что нажато, решает точка
     касания: ближе к центру - OK, дальше - стрелка той четверти. */
  var R = 116, C = 130, OK = 44;
  var wb = el('div', 'txwheel');
  var arr = '', dirs = [['up', -90], ['right', 0], ['down', 90], ['left', 180]];
  dirs.forEach(function(d){
    var a = d[1] * Math.PI / 180, ux = Math.cos(a), uy = Math.sin(a), vx = -uy, vy = ux;
    var dd = R * 0.70, sz = 13;
    var tip = [C + ux * (dd + sz * .65), C + uy * (dd + sz * .65)];
    var b1 = [C + ux * (dd - sz * .5) + vx * sz, C + uy * (dd - sz * .5) + vy * sz];
    var b2 = [C + ux * (dd - sz * .5) - vx * sz, C + uy * (dd - sz * .5) - vy * sz];
    arr += '<path class="ar" data-k="' + d[0] + '" d="M' + tip.join(',') + ' L' + b1.join(',') + ' L' + b2.join(',') + ' Z"/>';
  });
  wb.innerHTML = '<svg viewBox="0 0 260 260" width="260" height="260">'
    + '<defs><radialGradient id="txface" cx=".35" cy=".3" r=".9"><stop offset="0" stop-color="' + PAL.wheelA + '"/>'
    + '<stop offset="1" stop-color="' + PAL.wheelB + '"/></radialGradient>' + knobDefs('tw', FM, PAL.lt)
    + '<radialGradient id="txsh"><stop offset=".72" stop-color="#000" stop-opacity="' + (PAL.lt ? .2 : .5) + '"/>'
    + '<stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>'
    + '<radialGradient id="txok" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="' + PAL.acc2 + '"/>'
    + '<stop offset="1" stop-color="' + PAL.acc + '"/></radialGradient></defs>'
    + knobSVG('tw', C, C, R, 1.25, 'url(#txface)', PAL.wheelEdge, FM, 4)
    + arr
    + '<circle cx="' + C + '" cy="' + (C + 2) + '" r="' + (OK * 1.2) + '" fill="url(#txsh)"/>'
    + '<circle class="okb" data-k="ok" cx="' + C + '" cy="' + C + '" r="' + OK + '" fill="url(#txok)"/>'
    + '<text x="' + C + '" y="' + (C + 8) + '" text-anchor="middle" class="okt">OK</text></svg>';
  var wsvg = wb.firstChild;
  dimmed.push(wsvg);
  function which(e){
    var pt = evPoint(e), r = wsvg.getBoundingClientRect();
    var x = (pt.clientX - r.left) * 260 / r.width - C, y = (pt.clientY - r.top) * 260 / r.height - C;
    var d = Math.sqrt(x * x + y * y);
    if(d <= OK + 4) return 'ok';
    if(d > R + 6) return null;
    var a = Math.atan2(y, x) * 180 / Math.PI;
    return a >= -135 && a < -45 ? 'up' : a >= -45 && a < 45 ? 'right' : a >= 45 && a < 135 ? 'down' : 'left';
  }
  var cur = null;
  hold(wsvg, function(e, again){
    if(!again) cur = which(e);
    if(!cur) return;
    var n = wsvg.querySelector('[data-k="' + cur + '"]');
    if(n){ n.classList.add('hit'); setTimeout(function(){ n.classList.remove('hit'); }, 140); }
    press(cur);
  }, function(){ return cur && cur !== 'ok'; });   // OK удержанием не повторяется
  var wrapW = el('div', 'txwrap');
  wrapW.appendChild(wb);
  var lb = labeled(key('', 'back', svgIcon(TVX.back)), 'Назад'); lb.className += ' l';
  var rb = labeled(key('', 'home', svgIcon(TVX.menu, true)), 'Меню'); rb.className += ' r';
  wrapW.appendChild(lb); wrapW.appendChild(rb);
  card.appendChild(wrapW);

  /* перемотка и плей-пауза */
  var media = el('div', 'txrow');
  media.appendChild(key('', 'rewind', svgIcon(TVI.rew, true)));
  media.appendChild(key('', 'play_pause', svgIcon(TVI.playpause, true)));
  media.appendChild(key('', 'forward', svgIcon(TVI.fwd, true)));
  card.appendChild(media);

  /* громкость: мы не знаем, сколько сейчас, поэтому только шаги */
  var vol = el('div', 'txvol');
  var vd = el('div', 'txv'); vd.innerHTML = '<b>−</b><span>тише</span>';
  var vu = el('div', 'txv'); vu.innerHTML = '<b>+</b><span>громче</span>';
  hold(vd, function(){ press('vol_down'); }, true);
  hold(vu, function(){ press('vol_up'); }, true);
  dimmed.push(vd); dimmed.push(vu);
  vol.appendChild(vd); vol.appendChild(key('sm', 'mute', svgIcon(TVI.mute))); vol.appendChild(vu);
  card.appendChild(vol);

  /* приложения */
  var appsRaw = node.getAttribute('data-tv-apps') || '';
  if(appsRaw){
    var grid = el('div', 'txapps');
    appsRaw.split(';').forEach(function(item){
      var parts = item.split('|');
      if(!parts[0]) return;
      var title = parts[1] || parts[0];
      var b = el('div', 'txapp' + (title === app ? ' now' : ''), title);
      b.onclick = (function(c, nd){ return function(){
        press(c);
        var all = grid.querySelectorAll('.txapp');
        for(var k = 0; k < all.length; k++) all[k].classList.remove('now');
        nd.classList.add('now');
      }; })(parts[0], b);
      dimmed.push(b);
      grid.appendChild(b);
    });
    card.appendChild(grid);
  }
  setLive(link);

  function sync(){
    var q = Q();
    fetch(NAME + '.svg' + q + (q ? '&' : '?') + 't=' + Date.now(), {cache:'no-store'})
      .then(function(r){ return r.ok ? r.text() : null; })
      .then(function(text){
        if(!text) return;
        var doc = new DOMParser().parseFromString(text, 'image/svg+xml');
        var g = doc.querySelector('[data-tv-base="' + base + '"]');
        if(!g) return;
        on = g.getAttribute('data-tv-on') === '1';
        pw.classList.toggle('on', on);
        var st = g.getAttribute('data-tv-state') || '';
        var now = g.getAttribute('data-tv-app') || '';
        var live = g.getAttribute('data-tv-link') === '1';
        sub.textContent = st + (now ? ' · ' + now : '') + (live ? '' : ' · доступно только включение');
        if(live !== link) setLive(live);
        var chips = card.querySelectorAll('.txapp');
        for(var i = 0; i < chips.length; i++) chips[i].classList.toggle('now', chips[i].textContent === now);
      })
      .catch(function(){});
  }
  var timer = setInterval(sync, 2000);
  ovCleanup = function(){ clearInterval(timer); };
  openOv([card]);
}

/* Кнопка колеса на плитке (зона tv:<клавиша>): нажатие уходит прямо в
   мост, пульт не открывается. Нажатое на миг гаснет - иначе непонятно,
   дошло ли касание до плитки. */
function tvKey(node, control){
  var base = node.getAttribute('data-tv-base');
  if(!base) return;
  var parts = node.querySelectorAll('[data-k="' + control + '"]');
  for(var i = 0; i < parts.length; i++) parts[i].style.opacity = '.45';
  setTimeout(function(){ for(var i = 0; i < parts.length; i++) parts[i].style.opacity = ''; }, 180);
  pub(base + control + '/on', 1).catch(function(e){ flash('Не удалось: ' + e.message); });
}

function padTVFlat(node){
  var base = node.getAttribute('data-tv-base');
  if(!base) return;
  var link = node.getAttribute('data-tv-link') === '1';
  var on = node.getAttribute('data-tv-on') === '1';

  function press(control){
    pub(base + control + '/on', 1)
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  /* Питание - переключатель, а не кнопка: единица означает «включить», и
     нажатие на включённом телевизоре не выключало его, а подтверждало
     текущее состояние. Шлём противоположное значение и запоминаем его,
     чтобы второе нажатие подряд сработало до обновления панели. */
  function pressPower(){
    var want = on ? 0 : 1;
    pub(base + 'power/on', want).then(function(){
      on = !on;
      sub.textContent = on ? 'Включается…' : 'Выключается…';
      setTimeout(refresh, 800); setTimeout(refresh, 3000);
    }).catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  /* Кнопка. icon - ключ из TVI, иначе показывается текст.

     Нажатие вешается всегда, даже когда канал управления недоступен.
     Раньше такие кнопки были мертвы намертво, и это било дважды: пока
     телевизор просыпается, пульт бесполезен, а состояние «ожил» до него
     не доходило вовсе - sync обновлял подпись и подсветку приложений, но
     обработчики оставались неприклеенными до конца сессии. Нажал питание,
     дождался включения - и пульт всё равно не работает.

     Теперь недоступность только показывается бледным, а решает
     telegram-мост: не дошло так не дошло, второе нажатие ничего не
     испортит. needs === false у питания: оно идёт через Wake-on-LAN и в
     канале управления не нуждается, поэтому не гаснет никогда. */
  var dimmed = [];
  function btn(cls, label, control, icon, needs){
    var b = el('div', cls);
    if(icon) b.innerHTML = '<svg viewBox="0 0 24 24">' + TVI[icon] + '</svg>';
    else b.textContent = label;
    b.onclick = control === 'power'
              ? pressPower
              : function(){ press(control); };
    if(needs !== false) dimmed.push(b);
    return b;
  }
  function setLive(live){
    link = live;
    for(var k = 0; k < dimmed.length; k++){
      dimmed[k].classList.toggle('tvdim', !live);
    }
  }
  function row(items){
    var r = el('div','tvrow');
    items.forEach(function(n){ r.appendChild(n); });
    return r;
  }
  function hole(){ return el('div','tvc hole'); }

  var card = el('div','card tvcard');

  /* шапка: название, состояние, вход и питание */
  var head = el('div','tvhead');
  var name = el('div','tvname');
  name.appendChild(el('div','tvttl', node.getAttribute('data-tv-title') || 'Телевизор'));
  var app = node.getAttribute('data-tv-app') || '';
  var sub = el('div','tvsub',
      (node.getAttribute('data-tv-state') || '')
      + (app ? ' · ' + app : '')
      + (link ? '' : ' · доступно только включение'));
  name.appendChild(sub);
  head.appendChild(name);
  head.appendChild(btn('tvc', 'Вход', 'source', 'input'));
  head.appendChild(btn('tvc pw', '', 'power', 'power', false));
  card.appendChild(head);

  /* приложения: по три в ряд, активное подсвечено по current_app */
  var appsRaw = node.getAttribute('data-tv-apps') || '';
  if(appsRaw){
    var grid = el('div','tvapps');
    appsRaw.split(';').forEach(function(item){
      var parts = item.split('|');
      if(!parts[0]) return;
      var title = parts[1] || parts[0];
      var b = el('div','tvapp' + (title === app ? ' now' : ''), title);
      b.onclick = (function(c, node){ return function(){
        press(c);
        /* Подсвечиваем нажатое сразу, не дожидаясь ответа. Мост
           переспрашивает телевизор на ближайшем круге опроса, и через
           пару секунд sync() либо подтвердит, либо снимет подсветку -
           если приложение почему-то не запустилось. */
        var all = grid.querySelectorAll('.tvapp');
        for(var k = 0; k < all.length; k++) all[k].classList.remove('now');
        node.classList.add('now');
      }; })(parts[0], b);
      dimmed.push(b);
      grid.appendChild(b);
    });
    card.appendChild(grid);
  }

  /* крестовина: OK крупнее стрелок, «Назад» и «Меню» у нижних углов */
  card.appendChild(row([hole(), btn('tvc','','up','up'), hole()]));
  card.appendChild(row([btn('tvc','','left','left'),
                        btn('tvc big ok','OK','ok'),
                        btn('tvc','','right','right')]));
  card.appendChild(row([btn('tvc','Назад','back'),
                        btn('tvc','','down','down'),
                        btn('tvc','Меню','home')]));

  /* медиа: перемотка в обе стороны, посередине плей-пауза одной кнопкой.
     Две кнопки тут были от бедности: у Samsung есть KEY_PLAY_BACK, она
     переключает воспроизведение сама, и на пульте от телевизора она
     тоже одна. Какую именно кнопку слать, решает конфиг моста - панель
     по-прежнему публикует единицу в контрол play_pause и ничего не знает
     про коды клавиш. */
  card.appendChild(row([btn('tvc','','rewind','rew'),
                        btn('tvc fill','','play_pause','playpause'),
                        btn('tvc','','forward','fwd')]));

  /* громкость: широкая пара, приглушение между */
  var vd = el('div','tvvol','VOL −'), vu = el('div','tvvol','VOL +');
  vd.onclick = function(){ press('vol_down'); };
  vu.onclick = function(){ press('vol_up'); };
  dimmed.push(vd); dimmed.push(vu);
  card.appendChild(row([vd, btn('tvc','','mute','mute'), vu]));
  setLive(link);

  /* Пока пульт открыт, автообновление панели стоит - иначе разметка
     менялась бы под пальцем. Но статус нужен именно сейчас, когда пульт
     перед глазами, поэтому опрашиваем сами, чаще и не трогая страницу.
     Таймер снимается через ovCleanup: иначе он тикал бы в пустоту. */
  function sync(){
    var q = Q();
    fetch(NAME + '.svg' + q + (q ? '&' : '?') + 't=' + Date.now(), {cache:'no-store'})
      .then(function(r){ return r.ok ? r.text() : null; })
      .then(function(text){
        if(!text) return;
        var doc = new DOMParser().parseFromString(text, 'image/svg+xml');
        var g = doc.querySelector('[data-tv-base="' + base + '"]');
        if(!g) return;
        on = g.getAttribute('data-tv-on') === '1';
        var st = g.getAttribute('data-tv-state') || '';
        var now = g.getAttribute('data-tv-app') || '';
        var live = g.getAttribute('data-tv-link') === '1';
        sub.textContent = st + (now ? ' · ' + now : '')
                        + (live ? '' : ' · доступно только включение');
        // Телевизор просыпается секунд двадцать. Как только мост до него
        // достучался, пульт перестаёт быть бледным сам - без этого он
        // оставался в том виде, в каком открылся.
        if(live !== link) setLive(live);
        // Подсветка активного приложения: телевизор могли переключить и
        // обычным пультом, поэтому идём от того, что сообщил мост.
        var chips = card.querySelectorAll('.tvapp');
        for(var i = 0; i < chips.length; i++){
          chips[i].classList.toggle('now', chips[i].textContent === now);
        }
      })
      .catch(function(){});
  }
  var timer = setInterval(sync, 2000);
  ovCleanup = function(){ clearInterval(timer); };

  openOv([card]);
}
