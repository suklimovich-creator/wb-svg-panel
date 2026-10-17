/* wb-svg-panel: окна списка, воды, расписания, «Сегодня»; выбор пульта openPad.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- список ----------
   Окно рисует себя само, по ответу /api/list/<имя>. Каждое действие - это
   одна операция над одним пунктом, а не «вот новый список»: два телефона,
   отметившие по пункту почти одновременно, иначе затирали бы друг друга.
   Ответ на действие - список после него, переспрашивать не нужно.

   Пока окно открыто, список перечитывается раз в три секунды: его могли
   поменять с другого телефона, сценарием или быстрой командой. Строку
   ввода перерисовка не трогает - иначе набранное пропадало бы. */
function padList(node){
  var v = listView(node);
  if(!v) return;
  openOv([v.box]); v.start();
  ovCleanup = v.stop;
}
/* Содержимое окна списка - отдельно от окна: его же ставит над
   расписанием окно плитки «Сегодня» (1.43). */
function listView(node){
  var name = node.getAttribute('data-list');
  if(!name) return null;
  var url = 'api/list/' + encodeURIComponent(name);
  var box = el('div','lst sheet');
  var head = el('div','lhead'), ttl = el('div','lttl', node.getAttribute('data-title') || ''),
      cnt = el('div','lcnt');
  head.appendChild(ttl); head.appendChild(cnt);
  var add = el('div','ladd'), input = document.createElement('input'),
      go = el('div','lgo','Добавить');
  input.type = 'text'; input.maxLength = 200;
  input.placeholder = 'Новый пункт';
  input.setAttribute('enterkeyhint', 'done');
  add.appendChild(input); add.appendChild(go);
  var body = el('div'), foot = el('div','lfoot'), note = el('div','lnote'),
      warn = el('div','lwarn');
  box.appendChild(head); box.appendChild(warn); box.appendChild(add); box.appendChild(body);
  box.appendChild(foot); box.appendChild(note);

  var view = null, showDone = false, busy = false, timer = null, lastSig = '';

  function call(op){
    busy = true;
    return fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify(op)})
      .then(function(r){ return r.json().then(function(j){
        if(!r.ok) throw new Error(j.error || r.status); return j; }); })
      .then(function(j){ view = j; draw(); })
      .catch(function(e){ flash('Не удалось: ' + e.message); })
      .then(function(){ busy = false; });
  }
  function load(){
    if(busy) return;
    fetch(url + '?t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){
        if(!j || busy) return;
        // Ничего не поменялось - не перерисовываем: иначе раз в три
        // секунды сбрасывалось бы «Удалить?» и прыгала прокрутка.
        var sig = JSON.stringify([j.open, j.done, j.warning || '']);
        if(sig === lastSig) return;
        view = j; draw();
      })
      .catch(function(){});
  }
  function submit(){
    var text = input.value.replace(/^\s+|\s+$/g, '');
    if(!text) return;
    input.value = '';
    call({op:'add', text:text});
    // Клавиатура остаётся: пункты чаще добавляют подряд, чем по одному.
    input.focus();
  }
  go.onclick = submit;
  input.addEventListener('keydown', function(e){
    if(e.key === 'Enter'){ e.preventDefault(); submit(); }
  });

  function btn(text, cls, fn){
    var b = el('div', 'lbtn' + (cls ? ' ' + cls : ''), text);
    b.onclick = fn; return b;
  }
  function row(item, k, total, done){
    var r = el('div', 'lrow' + (done ? ' done' : ''));
    var chk = el('div','lchk'); chk.appendChild(el('i'));
    chk.onclick = function(){ call({op: done ? 'undo' : 'done', id: item.id}); };
    var txt = el('div','ltxt', item.text);
    // Правка - по нажатию на текст. Окно ввода системное: на телефоне
    // оно же открывает клавиатуру, своё рисовать незачем.
    txt.onclick = function(){
      var t = window.prompt('Пункт', item.text);
      if(t !== null && t.replace(/\s+/g,'') && t !== item.text)
        call({op:'edit', id:item.id, text:t});
    };
    r.appendChild(chk); r.appendChild(txt);
    if(!done){
      r.appendChild(btn('\u25B2', k === 0 ? 'off' : '',
                        function(){ call({op:'move', id:item.id, to:'up'}); }));
      r.appendChild(btn('\u25BC', k === total - 1 ? 'off' : '',
                        function(){ call({op:'move', id:item.id, to:'down'}); }));
    }
    // Удаление со вторым нажатием: первое превращает крестик в «Удалить?»,
    // и через три секунды он возвращается сам. Отменить удаление потом
    // нечем, а промах по крестику рядом со стрелками - дело обычное.
    var del = btn('\u2715', '', null);
    var armed = null;
    del.onclick = function(){
      if(armed){ clearTimeout(armed); call({op:'delete', id:item.id}); return; }
      del.textContent = 'Удалить?'; del.classList.add('ask');
      armed = setTimeout(function(){
        armed = null; del.textContent = '\u2715'; del.classList.remove('ask');
      }, 3000);
    };
    r.appendChild(del);
    return r;
  }
  function draw(){
    if(!view) return;
    lastSig = JSON.stringify([view.open, view.done, view.warning || '']);
    // Демон не смог записать файл: список живёт в памяти до перезапуска.
    warn.textContent = view.warning
      ? 'Список не сохраняется на контроллере и пропадёт при перезапуске. '
        + 'Откройте «Проверка конфига». (' + view.warning + ')'
      : '';
    ttl.textContent = view.title || ttl.textContent;
    cnt.textContent = view.open.length ? String(view.open.length) : '';
    body.innerHTML = '';
    if(!view.open.length)
      body.appendChild(el('div','lempty', view.done.length ? 'Всё сделано' : 'Пусто'));
    view.open.forEach(function(item, k){ body.appendChild(row(item, k, view.open.length, false)); });
    if(showDone) view.done.forEach(function(item){ body.appendChild(row(item, 0, 0, true)); });
    foot.innerHTML = '';
    if(view.done.length){
      var t = el('span', '', (showDone ? 'Скрыть выполненные' : 'Показать выполненные')
                             + ' (' + view.done.length + ')');
      t.onclick = function(){ showDone = !showDone; draw(); };
      foot.appendChild(t);
      if(showDone){
        var c = el('span', '', 'Очистить выполненные');
        c.onclick = function(){ call({op:'clear'}); };
        foot.appendChild(c);
      }
    }
    note.textContent = view.done.length
      ? 'Выполненные удаляются сами через ' + fmtDays(view.keep_days) + ' после отметки.'
      : '';
  }
  function fmtDays(d){
    if(d < 1) return Math.round(d * 24) + ' ч';
    var n = Math.round(d), m10 = n % 10, m100 = n % 100;
    var w = (m10 === 1 && m100 !== 11) ? 'день'
          : (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) ? 'дня' : 'дней';
    return n + ' ' + w;
  }

  return {box: box,
          start: function(){ load(); timer = setInterval(load, 3000); },
          stop: function(){ clearInterval(timer); }};
}

/* ---------- вода ----------
   Окна протечки и крана читают плитку на панели, а панель под окном
   обновляет ждущий запрос. Поэтому окно само видит, как прибор ответил:
   раз в секунду переискиваем плитку по номеру и перерисовываемся. */
function hhmm(ts){
  if(!ts) return '';
  var d = new Date(ts * 1000), now = new Date();
  var t = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  if(d.toDateString() !== now.toDateString())
    t = d.getDate() + '.' + ('0' + (d.getMonth() + 1)).slice(-2) + ' ' + t;
  return t;
}
function freshTile(node){
  var i = node.getAttribute('data-i');
  return host.querySelector('[data-i="' + i + '"][data-pad="' + node.getAttribute('data-pad') + '"]') || node;
}

function padLeak(node){
  var box = el('div','lst');
  var head = el('div','lhead'), ttl = el('div','lttl', node.getAttribute('data-title') || 'Протечки');
  head.appendChild(ttl); box.appendChild(head);
  var body = el('div'), foot = el('div');
  box.appendChild(body); box.appendChild(foot);
  var resetBusy = false;

  function draw(){
    node = freshTile(node);
    body.innerHTML = '';
    var raw = node.getAttribute('data-leak-rows') || '';
    raw.split(';').forEach(function(item){
      if(!item) return;
      var p = item.split('|');
      var on = p[1] === '1', unknown = p[1] === '?';
      var r = el('div', 'wrow' + (on ? ' wet' : ''));
      var pk = el('div', 'wpk'); glyphInto(pk, 'puck', 'wet=' + (on ? 1 : 0)); r.appendChild(pk);
      r.appendChild(el('div','wname', p[0]));
      // Время - последнего сообщения датчика: у сработавшего это момент
      // срабатывания, у сухого - когда он последний раз отчитался.
      if(on && p[2] && p[2] !== '0') r.appendChild(el('div','wwhen','с ' + hhmm(+p[2])));
      r.appendChild(el('div','wst', unknown ? 'нет данных' : (on ? 'Вода' : 'Сухо')));
      body.appendChild(r);
      // Что этот датчик перекрывает - знает раздел water. Видно сразу,
      // какая вода пропала и почему.
      if(p[3]) body.appendChild(el('div','wclose',
        (on ? 'перекрыл: ' : 'перекрывает: ') + p[3]));
    });
    foot.innerHTML = '';
    var alarm = node.getAttribute('data-alarm') === '1';
    var reset = node.getAttribute('data-reset');
    if(alarm && reset){
      var b = el('div', 'wbtn reset' + (resetBusy ? ' off' : ''),
                 resetBusy ? 'Сбрасываю…' : 'Сбросить тревогу');
      b.onclick = function(){
        resetBusy = true; draw();
        // Модулей может быть несколько - у каждого свой сброс. Жмём все,
        // что относятся к показанным датчикам.
        var topics = reset.split('|');
        Promise.all(topics.map(function(t){ return pub(t, 1); }))
          .catch(function(e){ flash('Не удалось: ' + e.message); })
          .then(function(){ setTimeout(function(){ resetBusy = false; draw(); }, 2500); });
      };
      foot.appendChild(b);
      foot.appendChild(el('div','lnote',
        'Сброс снимает тревогу модуля. Пока датчик мокрый, она поднимется снова.'));
    } else if(alarm){
      foot.appendChild(el('div','lnote',
        'Тревога держится до сброса — на модуле или в его настройках.'));
    }
  }
  draw();
  var timer = setInterval(draw, 1000);
  openOv([box]);
  ovCleanup = function(){ clearInterval(timer); };
}

/* ---------- расписание ----------
   Сегодня и завтра целиком. Данные - в атрибуте плитки, его обновляет
   ждущий запрос; окно перерисовывается раз в 10 с, чтобы «сейчас»
   переезжало на следующее событие без переоткрытия. Перерисовываем, только
   если строки поменялись: иначе прокрутка длинного дня прыгала бы. */
function padAgenda(node){
  var v = agendaView(node);
  openOv([v.box]); v.start();
  ovCleanup = v.stop;
}
function agendaView(node){
  var box = el('div','lst agw');
  var body = el('div');
  box.appendChild(body);
  var last = null;
  function draw(){
    node = freshTile(node);
    var raw = node.getAttribute('data-agenda') || '';
    if (raw === last) return;
    last = raw;
    body.innerHTML = '';
    var any = false, cnt = 0;
    raw.split(';').forEach(function(item){
      if(!item) return;
      var p = item.split('|');
      if (p[0] === 'D') {
        if (any && !cnt) body.appendChild(el('div','agempty','Ничего не запланировано'));
        var h = el('div','lhead');
        var label = p[1] || '';
        // Листок календаря у сегодняшнего дня (1.42), как на плитке.
        if(!any){
          var now = new Date(), pg = el('div', 'wcal');
          glyphInto(pg, 'calpage', 'size=30&day=' + now.getDate() + '&wd='
                    + encodeURIComponent(['вс','пн','вт','ср','чт','пт','сб'][now.getDay()]));
          h.appendChild(pg);
        }
        h.appendChild(el('div','lttl', label.charAt(0).toUpperCase() + label.slice(1)));
        body.appendChild(h);
        any = true; cnt = 0;
        return;
      }
      cnt += 1;
      var st = p[5] || 'next';
      var r = el('div', 'agrow ' + st);
      var dot = el('i'); dot.style.background = p[3] || '#3B8FD4';
      r.appendChild(dot);
      // «14:00–15:00» в узкой колонке переносился посередине: начало
      // крупно, конец мелко строкой ниже.
      var span = (p[1] || '').split('–');
      var tm = el('div','agtime', st === 'now' ? 'сейчас' : span[0]);
      if (st === 'now') tm.appendChild(el('div','agend', p[1]));
      else if (span[1]) tm.appendChild(el('div','agend', 'до ' + span[1]));
      r.appendChild(tm);
      var t = el('div','agtxt');
      t.appendChild(el('div','agname', p[2]));
      if (p[4]) t.appendChild(el('div','agloc', p[4]));
      r.appendChild(t);
      body.appendChild(r);
    });
    if (any && !cnt) body.appendChild(el('div','agempty','Ничего не запланировано'));
    if (!any) body.appendChild(el('div','agempty','Календарь ещё не загружен'));
  }
  var timer = null;
  return {box: box,
          start: function(){ draw(); timer = setInterval(draw, 10000); },
          stop: function(){ clearInterval(timer); }};
}

/* «Сегодня» (1.43): лист дел с галочками, под ним расписание - те же
   окна, что у отдельных плиток, одно над другим. */
function padToday(node){
  var parts = [], views = [];
  var lv = listView(node);
  if(lv){ views.push(lv); parts.push(lv.box); }
  if(node.getAttribute('data-agenda')){
    var av = agendaView(node);
    views.push(av); parts.push(av.box);
  }
  if(!parts.length) return;
  openOv(parts);
  views.forEach(function(v){ v.start(); });
  ovCleanup = function(){ views.forEach(function(v){ v.stop(); }); };
}

/* Рисунки для окон (с 1.46) - с сервера, тем же макросом, что на плитке
   (glyph/<имя>.svg, templates/glyph.svg.j2), а не копией здесь. Ответ
   зависит только от адреса: браузер и этот словарь держат его у себя,
   окно протечки перерисовывается раз в секунду без запросов. */
var GLYPHS = {};
function glyphInto(holder, name, params){
  var q = 'glyph/' + name + '.svg?theme=' + encodeURIComponent(rootTheme() || '') + '&' + params;
  holder.setAttribute('data-glyph', q);
  if (GLYPHS[q] !== undefined) { holder.innerHTML = GLYPHS[q]; return; }
  fetch(q).then(function(r){ return r.ok ? r.text() : ''; })
    .then(function(t){
      GLYPHS[q] = t;
      if (holder.getAttribute('data-glyph') === q) holder.innerHTML = t;
    })
    .catch(function(){});
}

function padValve(node){
  var box = el('div','lst');
  var head = el('div','lhead'), ttl = el('div','lttl', node.getAttribute('data-title') || 'Кран');
  head.appendChild(ttl); box.appendChild(head);
  var pic = el('div'), big = el('div','wbig'), sub = el('div','wsub'), act = el('div'), warn = el('div');
  box.appendChild(pic); box.appendChild(big); box.appendChild(sub); box.appendChild(act); box.appendChild(warn);
  var hot = node.getAttribute('data-water') === 'hot';
  // Куда просили перевести кран: пока прибор не ответил, пишем «…ется».
  var want = null, wantUntil = 0;

  function draw(){
    node = freshTile(node);
    var opened = node.getAttribute('data-state') === '1';
    var alarm = node.getAttribute('data-alarm') === '1';
    if(want !== null && (want === opened || Date.now() > wantUntil)) want = null;
    var picKey = (opened ? 1 : 0) + (hot ? 'h' : 'c');
    if(pic.getAttribute('data-k') !== picKey){
      pic.setAttribute('data-k', picKey);
      glyphInto(pic, 'faucet', 'size=120&open=' + (opened ? 1 : 0)
                + '&color=' + encodeURIComponent(hot ? '#D9433C' : '#3B8FD4'));
    }
    big.textContent = want === null ? (opened ? 'Открыт' : 'Перекрыт')
                                    : (want ? 'Открывается…' : 'Перекрывается…');
    var st = node.getAttribute('data-status') || '';
    var why = st.indexOf('Перекрыт: ') === 0 ? st.slice(10) : '';
    if(why === 'протечка') why = '';   // кто именно - не знаем
    sub.textContent = (hot ? 'Горячая вода' : 'Холодная вода')
                    + (!opened && why ? ' · перекрыл датчик: ' + why
                       : (!opened && alarm ? ' · перекрыт из-за протечки' : ''));
    act.innerHTML = '';
    // не «close»: так зовётся круглая «назад» окна (#ov .close, 1.35), и
    // кнопка уезжала к ней в угол
    var b = el('div', 'wbtn ' + (opened ? 'shut' : 'open') + (hot ? ' hot' : '')
                    + (want !== null ? ' off' : ''),
               opened ? 'Перекрыть воду' : 'Открыть воду');
    b.onclick = function(){
      var topic = node.getAttribute('data-topic');
      var value = node.getAttribute(opened ? 'data-off' : 'data-on');
      want = !opened; wantUntil = Date.now() + 15000; draw();
      pub(topic, value).catch(function(e){
        want = null; draw(); flash('Не удалось: ' + e.message);
      });
    };
    act.appendChild(b);
    warn.innerHTML = '';
    if(!opened && alarm)
      warn.appendChild(el('div','wwarn',
        'Тревога протечки не сброшена: модуль может перекрыть кран снова.'));
  }
  draw();
  var timer = setInterval(draw, 1000);
  openOv([box]);
  ovCleanup = function(){ clearInterval(timer); };
}

function openPad(node){
  var pad = node.getAttribute('data-pad');
  if(pad==='xy') padXY(node);
  else if(pad==='thermostat') padThermostat(node);
  else if(pad==='level') padLevel(node);
  else if(pad==='curtain') padCurtain(node);
  else if(pad==='roller') padCurtain(node, 'y');
  else if(pad==='ac') padAC(node);
  else if(pad==='camera') padCamera(node);
  else if(pad==='tv') padTV(node);
  else if(pad==='list') padList(node);
  else if(pad==='leak') padLeak(node);
  else if(pad==='valve') padValve(node);
  else if(pad==='agenda') padAgenda(node);
  else if(pad==='today') padToday(node);
}
