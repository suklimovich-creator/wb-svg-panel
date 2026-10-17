/* wb-svg-panel: обновление панели: запросы, голова, точечная замена, ждущий запрос, запуск.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- обновление ---------- */
/* Ответы приходят не в том порядке, в каком ушли запросы. Фоновое
   обновление и запрос после нажатия на заголовок идут параллельно, и если
   фоновое ушло до нажатия со старой свёрткой, а вернулось после, оно
   перерисовывало панель обратно. Поэтому у каждого запроса номер, и ответ
   старее показанного выбрасывается, а ответ на запрос с другой свёрткой
   или числом колонок - тоже. */
var reqSeq = 0, shownSeq = 0, pendingSvg = null, inflight = 0, lastReqAt = 0;

/* Последние картинки по состоянию свёртки: повторно раскрытый раздел
   показывается из памяти мгновенно, а свежие данные догоняют. Держим
   немного - это по одной на раздел гармошки плюс пара вариантов колонок. */
var svgCache = {}, cacheKeys = [], CACHE_MAX = 12;
function remember(q, svg){
  if (!svgCache[q]) {
    cacheKeys.push(q);
    if (cacheKeys.length > CACHE_MAX) delete svgCache[cacheKeys.shift()];
  }
  svgCache[q] = {svg: svg, at: Date.now()};
}

/* ---------- точечная замена ----------
   Новая картинка разбирается целиком, но в документ попадают только те
   плитки, что изменились. Остальные узлы остаются прежними - с их
   обработчиками, нажатием, которое сейчас идёт, и ссылками, которые
   держат пульты. Раньше innerHTML менял всю панель: полторы сотни
   килобайт разметки и обработчики заново на каждую плитку раз в десять
   секунд.

   Сравниваются дети главной группы: заголовки разделов, чипы, плитки,
   часы в шапке - каждый своей строкой. Стили, defs и размеры - отдельно:
   если поменялись они, раскладка другая, и панель заменяется целиком. */
var XP = window.DOMParser ? new DOMParser() : null;
var XS = window.XMLSerializer ? new XMLSerializer() : null;
var shownHead = null, shownParts = null, shownSvg = null, patchWhy = '', perfMerged = 0;
/* Плитки с мгновенным откликом: индекс -> до какого времени не трогать.
   Пока прибор не ответил, сервер присылает плитку в прежнем виде, и без
   этого рамка мигнула бы обратно на первом же ответе. */
var holds = {}, HOLD_MS = 8000;

function kids(node){
  var out = [];
  for (var c = node.firstChild; c; c = c.nextSibling)
    if (c.nodeType === 1) out.push(c);
  return out;
}
function mainOf(svgEl){
  var k = kids(svgEl);
  return k.length ? k[k.length - 1] : null;
}
function headOf(svgEl){
  var k = kids(svgEl), s = '';
  for (var i = 0; i < k.length - 1; i++) s += XS.serializeToString(k[i]);
  var a = ['width', 'class', 'style'];
  for (var j = 0; j < a.length; j++) s += '|' + (svgEl.getAttribute(a[j]) || '');
  return s;
}
/* Голова без высоты корня (1.47): свернули раздел - панель стала ниже, но
   стили и заготовки те же, и менять её целиком незачем. Высоту новой
   картинки patch() переносит на показанную. */
function headKey(raw){
  return raw.replace(/<svg\b[^>]*>/, function(tag){
    return tag.replace(/ height="[^"]*"/, '').replace(/ viewBox="[^"]*"/, '');
  });
}
function sizeFrom(root, cur){
  var a = ['height', 'viewBox'];
  for (var i = 0; i < a.length; i++) {
    var v = root.getAttribute(a[i]);
    if (v !== null && cur.getAttribute(a[i]) !== v) cur.setAttribute(a[i], v);
  }
}
function partsOf(g){
  var k = kids(g), out = [];
  for (var i = 0; i < k.length; i++) out.push(XS.serializeToString(k[i]));
  return out;
}
function parseSvg(svg){
  if (!XP || !XS) return null;
  var doc = XP.parseFromString(svg, 'image/svg+xml');
  if (!doc || !doc.documentElement || doc.getElementsByTagName('parsererror').length)
    return null;
  return doc.documentElement;
}
function hold(node){
  var g = host.querySelector('svg') ? mainOf(host.querySelector('svg')) : null;
  if (!g) return;
  var k = kids(g);
  for (var n = node; n && n !== g; n = n.parentNode) {
    var i = k.indexOf(n);
    if (i >= 0) { holds[i] = Date.now() + HOLD_MS; return; }
  }
}

/* Заменить только изменившееся. false - не вышло, нужна полная замена. */
function patch(root, headRaw){
  var cur = host.querySelector('svg');
  // Почему пришлось менять целиком - для замера ?perf=1 (1.47).
  patchWhy = !cur ? 'первая' : !root ? 'не разобрать' : !shownParts ? 'первая' : '';
  if (patchWhy) return false;
  // Голову сравниваем исходным текстом, а не разобранной и собранной
  // заново: 60 КБ стилей сериализовались на каждом обновлении (1.45).
  var head = headKey(headRaw || headOf(root));
  if (head !== shownHead) { patchWhy = 'голова'; return false; }
  var ng = mainOf(root), cg = mainOf(cur);
  if (!ng || !cg) { patchWhy = 'нет группы'; return false; }
  var nk = kids(ng), ck = kids(cg);
  if (ck.length !== shownParts.length) { patchWhy = 'раскладка'; return false; }
  sizeFrom(root, cur);
  // Число частей поменялось (чип появился или пропал, раздел свернули) -
  // сводим по совпадающим строкам, а не меняем панель целиком (1.47).
  if (nk.length !== ck.length) return merge(nk, ck, cg);
  var now = Date.now();
  for (var i = 0; i < nk.length; i++) {
    var str = XS.serializeToString(nk[i]);
    // Не изменилась - не трогаем. Исключение - плитка с мгновенным
    // откликом, которую прибор так и не подтвердил: её возвращаем к
    // тому, что знает сервер.
    var expired = holds[i] && holds[i] <= now;
    if (str === shownParts[i] && !expired) continue;
    var fresh = document.importNode(nk[i], true);
    cg.replaceChild(fresh, ck[i]);
    shownParts[i] = str;
    delete holds[i];
    bind(fresh);
  }
  return true;
}

/* Свести новые части главной группы с показанными: одинаковые остаются
   на месте (с обработчиками и мгновенным откликом), новые вставляются,
   пропавшие удаляются. Совпадение ищем недалеко вперёд: порядок частей
   почти всегда тот же, меняется немногое. */
var MERGE_LOOK = 12;
function merge(nk, ck, cg){
  var parts = [], newHolds = {}, j = 0, now = Date.now();
  for (var i = 0; i < nk.length; i++) {
    var str = XS.serializeToString(nk[i]), at = -1;
    for (var m = j; m < Math.min(ck.length, j + MERGE_LOOK); m++)
      if (shownParts[m] === str && !(holds[m] && holds[m] <= now)) { at = m; break; }
    if (at >= 0) {
      for (var d = j; d < at; d++) cg.removeChild(ck[d]);
      if (holds[at]) newHolds[parts.length] = holds[at];
      j = at + 1;
    } else {
      var fresh = document.importNode(nk[i], true);
      if (j < ck.length) cg.insertBefore(fresh, ck[j]); else cg.appendChild(fresh);
      bind(fresh);
    }
    parts.push(str);
  }
  for (var r = j; r < ck.length; r++) cg.removeChild(ck[r]);
  shownParts = parts;
  holds = newHolds;
  perfMerged++;
  return true;
}

function full(svg, root, headRaw){
  if (root) {
    shownHead = headKey(headRaw || headOf(root));
    shownParts = partsOf(mainOf(root));
    holds = {};
    var node = document.importNode(root, true);
    while (host.firstChild) host.removeChild(host.firstChild);
    host.appendChild(node);
  } else {
    // Разобрать как XML не вышло - вставляем по-старому, без точечных
    // замен в дальнейшем, пока не придёт разбираемая картинка.
    shownHead = shownParts = null;
    host.innerHTML = svg;
  }
  bind();
}

function applySvg(svg, seq){
  if (seq < shownSeq) return;
  if (fingerHeld()) {
    if (!pendingSvg || seq > pendingSvg.seq) pendingSvg = {svg: svg, seq: seq};
    return;
  }
  shownSeq = seq; pendingSvg = null;
  // Ровно та же картинка - разбирать и сравнивать нечего (если нет плиток,
  // ждущих подтверждения прибора: их надо вернуть к серверному виду).
  var holding = false;
  for (var h in holds) if (holds.hasOwnProperty(h)) { holding = true; break; }
  if (svg === shownSvg && !holding && host.querySelector('svg')) {
    host.classList.remove('wait'); err.style.display = 'none';
    return;
  }
  var t0 = PERF ? perfNow() : 0;
  var at = svg.indexOf(MAIN_MARK), headRaw = at > 0 ? svg.slice(0, at) : null;
  var root = parseSvg(svg);
  var patched = patch(root, headRaw);
  if (!patched) full(svg, root, headRaw);
  shownSvg = svg;
  // Облегчённо: вращение вентиляторов и круги на воде стоят на месте.
  var shown = host.querySelector('svg');
  if (LITE && shown && shown.pauseAnimations) shown.pauseAnimations();
  if (PERF) perfApply(perfNow() - t0, patched, svg.length);
  pageBg(root);
  dockBar();
  glassScroll();
  if (!DEMO) setTimeout(navScale, 0);
  host.classList.remove('wait');
  err.style.display = 'none';
  if (AUTO_PAD) {
    var want = AUTO_PAD; AUTO_PAD = '';
    setTimeout(function(){
      var n = host.querySelector('[data-pad="' + want + '"]');
      if (n) openPad(n);
    }, 60);
  }
}
function flushPending(){
  if (!pendingSvg || fingerHeld()) return;
  var p = pendingSvg; pendingSvg = null;
  applySvg(p.svg, p.seq);
}

/* Версия состояния, которую видела страница. Её отдаёт сервер в
   X-Panel-Version, и ждущий запрос держится открытым, пока она не
   изменится. Нет заголовка - старый сервер, работаем опросом. */
var VER = null, LIVE = true;
function noteVersion(r){
  var app = r.headers && r.headers.get ? r.headers.get('X-Panel-App') : null;
  if (app && APP && app !== APP) {
    // под открытым окном не дёргаем - перезагрузимся, когда его закроют
    if (ov.classList.contains('open')) appReload = true; else location.reload();
  }
  var v = r.headers && r.headers.get ? r.headers.get('X-Panel-Version') : null;
  if (v === null || v === undefined || v === '') { LIVE = false; return; }
  VER = parseInt(v, 10);
}
function svgUrl(q, extra){
  // have= - отпечаток головы (стили и заготовки), что уже есть у страницы:
  // совпадёт - сервер пришлёт только главную группу (1.45)
  return NAME + '.svg' + q + (q ? '&' : '?') + (extra || '')
       + (HEAD ? 'have=' + HEAD.mark + '&' : '') + 't=' + Date.now();
}
/* Голова панели - всё до главной группы: корень, <style>, <defs>. Две
   трети картинки, а меняется она только с темой, колонками и раскладкой.
   Помним последнюю; сервер, увидев её отпечаток, шлёт ответ без неё. */
var HEAD = null, MAIN_MARK = '<g data-main="1"';
/* Отпечаток головы - без высоты (1.47): та же голова у панели другой
   высоты. Высоту сервер присылает отдельно, она ставится в корень. */
function withHeight(text, h){
  return text.replace(/<svg\b[^>]*>/, function(tag){
    return tag.replace(/ height="[^"]*"/, ' height="' + h + '"')
              .replace(/ viewBox="(\S+ \S+ \S+) [^"]*"/, ' viewBox="$1 ' + h + '"');
  });
}
function takeSvg(r){
  return r.text().then(function(t){
    if (PERF) perfNet(t.length, r.headers && r.headers.get && r.headers.get('X-Panel-Lean') === '1');
    var mark = r.headers && r.headers.get ? r.headers.get('X-Panel-Head') : null;
    if (r.headers && r.headers.get && r.headers.get('X-Panel-Lean') === '1') {
      if (HEAD && HEAD.mark === mark) {
        var h = r.headers.get('X-Panel-Height');
        return (h ? withHeight(HEAD.text, h) : HEAD.text) + t;
      }
      HEAD = null;
      throw new Error('нет головы панели');
    }
    var at = mark ? t.indexOf(MAIN_MARK) : -1;
    if (at > 0) HEAD = {mark: mark, text: t.slice(0, at)};
    return t;
  });
}

/* Разовый запрос: после нажатий, смены свёртки, закрытия окна.
   force нужен пультам: пока открыт оверлей, автообновление стоит, но
   пульт термостата сам следит за прибором и переспрашивает осознанно. */
function refresh(force){
  if(paused && !force) return;
  var q = Q(), seq = ++reqSeq, settled = false;
  function settle(){ if(!settled){ settled = true; inflight--; } }
  inflight++; lastReqAt = Date.now();
  fetch(svgUrl(q), {cache:'no-store'})
    .then(function(r){
      if(!r.ok) throw new Error('HTTP '+r.status);
      noteVersion(r);
      return takeSvg(r);
    })
    .then(function(svg){
      settle();
      if(svg.indexOf('<svg')<0) throw new Error('пустой ответ');
      remember(q, svg);
      // Пока запрос шёл, свёртку или колонки поменяли - картинка уже не та.
      if(q !== Q()) return;
      applySvg(svg, seq);
      // Первая загрузка: гармошку страница узнаёт только из разметки, и
      // первый запрос ушёл без раскрытого раздела. Переспрашиваем сразу.
      if(q !== Q()) refresh(true);
    })
    .catch(function(e){
      settle();
      // Притушенную панель не оставляем: свежего не будет, а старое
      // лучше, чем ничего.
      if(q === Q()) host.classList.remove('wait');
      if(host.querySelector('svg')) return;
      host.textContent = 'Не удалось загрузить панель: ' + e.message;
    });
}

/* ---------- ждущий запрос ----------
   Один запрос всегда открыт: сервер отвечает, как только на панели
   что-то поменялось, а без изменений - через refresh секунд, как раньше
   ходил таймер. Ответ - вся панель, но в документ попадают только
   изменившиеся плитки.

   Ответ пришёл подозрительно быстро - сервер не стал ждать (занято или
   изменения сыплются подряд). Тогда следующий запрос через секунду, а не
   сразу: иначе страница крутилась бы в цикле и грела контроллер. */
var pollTimer = null, pollAbort = null, pollBusy = false, pollFails = 0;
function schedule(ms){
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, ms);
}
function poll(){
  if (pollBusy || STILL) return;
  if (document.hidden) return;          // продолжим, когда вкладку покажут
  if (!LIVE) { if (!paused) refresh(); schedule(EVERY); return; }
  pollBusy = true;
  var q = Q(), t0 = Date.now();
  var ctl = window.AbortController ? new AbortController() : null;
  pollAbort = ctl;
  var opts = {cache:'no-store'};
  if (ctl) opts.signal = ctl.signal;
  fetch(svgUrl(q, VER !== null ? 'since=' + VER + '&' : ''), opts)
    .then(function(r){
      if(!r.ok) throw new Error('HTTP ' + r.status);
      noteVersion(r);
      // 204 - на сервере нет места для ждущих (открыто много экранов).
      // Картинка у нас и так текущая: спросим снова чуть позже.
      return r.status === 204 ? null : takeSvg(r);
    })
    .then(function(svg){
      pollBusy = false; pollAbort = null; pollFails = 0;
      if (svg === null) { schedule(q !== Q() ? 0 : 2000); return; }
      if (svg.indexOf('<svg') >= 0) {
        remember(q, svg);
        // Панель обновляется и под открытым окном: замена точечная и
        // ничего не ломает под пальцем, а пульты (термостат) читают
        // прибор прямо с неё.
        // Номер - на момент ответа, а не запроса: ждущий запрос уходит
        // раньше всех, а приносит самое свежее. С номером запроса его
        // ответ проигрывал бы разовому, ушедшему позже, и изменение
        // терялось бы до следующего таймаута.
        if (q === Q()) applySvg(svg, ++reqSeq);
      }
      if (q !== Q()) { schedule(0); return; }
      schedule(Date.now() - t0 < 300 ? 1000 : 0);
    })
    .catch(function(e){
      pollBusy = false; pollAbort = null;
      if (e && e.name === 'AbortError') { schedule(0); return; }
      pollFails += 1;
      // Контроллер перезагружается или сеть пропала: не долбим, а
      // отступаем, но не дольше обычного шага обновления.
      schedule(Math.min(EVERY, 1000 * pollFails));
    });
}
/* Свёртку или колонки поменяли - ждущий запрос несёт старый адрес.
   Обрываем его и открываем новый, иначе следующее изменение пришло бы
   только после его таймаута. */
function restartPoll(){
  if (pollAbort) { try { pollAbort.abort(); } catch(e) {} }
  else schedule(0);
}
/* Совместимость: прежний тик раз в EVERY. Теперь им служит ждущий
   запрос, а функция осталась для тех, кто её зовёт. */
function tick(){
  if (inflight > 0 && Date.now() - lastReqAt < 15000) return;
  refresh();
}
/* Экран не должен гаснуть.
   Сперва пробуем штатный способ, он есть в свежих браузерах. Если его нет -
   заводим беззвучное зацикленное видео: пока что-то воспроизводится, Android
   держит экран включённым. Приём старый, работает и на Android 5. */
var wakeLock = null;
function keepAwake(){
  if (navigator.wakeLock && navigator.wakeLock.request) {
    navigator.wakeLock.request('screen').then(function(l){
      wakeLock = l;
      l.addEventListener('release', function(){ wakeLock = null; });
    }).catch(function(){ videoAwake(); });
  } else {
    videoAwake();
  }
}
function videoAwake(){
  if (document.getElementById('nosleep')) return;
  var v = document.createElement('video');
  v.id = 'nosleep';
  v.setAttribute('playsinline', ''); v.setAttribute('muted', '');
  v.setAttribute('loop', ''); v.muted = true; v.loop = true;
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
  // односекундный чёрный кадр, меньше килобайта
  v.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAAr1tZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE0OCByMjY0MyA1YzY1NzA0IC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAxNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbAAAAAFliIQA';
  document.body.appendChild(v);
  var p = v.play();
  if (p && p.catch) p.catch(function(){ /* нужен жест пользователя */ });
  // если браузер потребовал жест - запустим при первом касании
  document.addEventListener('touchstart', function once(){
    v.play(); document.removeEventListener('touchstart', once);
  });
}
keepAwake();
document.addEventListener('visibilitychange', function(){
  if (!document.hidden && !wakeLock) keepAwake();
  // Вкладку спрятали - ждущий запрос не держим, поток на контроллере
  // нужнее. Показали - сразу свежая картинка и снова ждём.
  if (document.hidden) {
    if (pollAbort) { try { pollAbort.abort(); } catch(e) {} }
    clearTimeout(pollTimer);
  } else {
    refresh(); schedule(300);
  }
});

/* Полный экран по двойному касанию - на случай, если страница открыта
   обычной вкладкой, а не с главного экрана. */
document.addEventListener('dblclick', function(){
  var el = document.documentElement;
  var fn = el.requestFullscreen || el.webkitRequestFullscreen ||
           el.webkitRequestFullScreen || el.mozRequestFullScreen;
  if (fn) fn.call(el);
});

drawCols();
refresh();
// Ждущий запрос - чуть позже первой картинки: она приходит без версии, а
// с версией он и ждёт по-настоящему.
schedule(800);
