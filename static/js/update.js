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
   В документ попадают только те плитки, что изменились. Остальные узлы
   остаются прежними - с их обработчиками, нажатием, которое сейчас идёт,
   и ссылками, которые держат пульты. Раньше innerHTML менял всю панель:
   полторы сотни килобайт разметки и обработчики заново на каждую плитку
   раз в десять секунд.

   Сравниваются дети главной группы: заголовки разделов, чипы, плитки,
   часы в шапке - каждый своей строкой. Голова (стили, заготовки, ширина) -
   отдельно: поменялась она - панель заменяется целиком.

   С 1.48 строки частей режутся прямо из текста ответа, без разбора всей
   картинки: на старом планшете разбор 380 КБ разметки и сборка строк
   обратно стоили полсекунды на каждом обновлении. Разбираются только
   изменившиеся части - одним маленьким документом. */
var XP = window.DOMParser ? new DOMParser() : null;
var XS = window.XMLSerializer ? new XMLSerializer() : null;
var shownHead = null, shownParts = null, shownSvg = null, patchWhy = '', perfMerged = 0;
/* Части - куски текста ответа (TEXT_PARTS) или, если резка однажды не
   сошлась с разбором браузера, сериализованные узлы, как до 1.48. */
var TEXT_PARTS = true;
var PART_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">';
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
function nextEl(n){
  for (n = n.nextSibling; n && n.nodeType !== 1; n = n.nextSibling) {}
  return n;
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
/* Высота и viewBox новой картинки - на показанную. */
function sizeTo(cur, h, vb){
  if (h !== null && cur.getAttribute('height') !== h) cur.setAttribute('height', h);
  if (vb !== null && cur.getAttribute('viewBox') !== vb) cur.setAttribute('viewBox', vb);
}
function sizeOfHead(headRaw){
  var tag = (headRaw.match(/<svg\b[^>]*>/) || [''])[0];
  var h = tag.match(/ height="([^"]*)"/), vb = tag.match(/ viewBox="([^"]*)"/);
  return {h: h ? h[1] : null, vb: vb ? vb[1] : null};
}
function partsOf(g){
  var k = kids(g), out = [];
  for (var i = 0; i < k.length; i++) out.push(XS.serializeToString(k[i]));
  return out;
}
/* Дети главной группы - кусками текста. Разметку пишет наш шаблон:
   угловые скобки в значениях атрибутов экранированы, поэтому хватает
   поиска «<» и «>». null - что-то непонятное, тогда разбираем целиком. */
function splitMain(t, at){
  var i = t.indexOf('>', at) + 1, depth = 0, start = 0, out = [];
  if (i <= 0) return null;
  for (;;) {
    var lt = t.indexOf('<', i);
    if (lt < 0) return null;
    var c = t.charAt(lt + 1);
    if (c === '!') {
      var close = t.substr(lt, 4) === '<!--' ? '-->' : t.substr(lt, 9) === '<![CDATA[' ? ']]>' : null;
      if (!close) return null;
      var ce = t.indexOf(close, lt);
      if (ce < 0) return null;
      i = ce + close.length;
      continue;
    }
    var gt = t.indexOf('>', lt);
    if (gt < 0) return null;
    if (c === '/') {
      if (depth === 0) return out;            // </g> главной группы
      if (--depth === 0) out.push(t.slice(start, gt + 1));
    } else if (c === '?') {
      return null;
    } else if (t.charAt(gt - 1) === '/') {
      if (depth === 0) out.push(t.slice(lt, gt + 1));
    } else {
      if (depth === 0) start = lt;
      depth++;
    }
    i = gt + 1;
  }
}
/* Несколько частей - одним маленьким документом. */
function parseParts(list){
  if (!list.length) return [];
  var doc = XP.parseFromString(PART_OPEN + list.join('') + '</svg>', 'image/svg+xml');
  if (!doc || !doc.documentElement || doc.getElementsByTagName('parsererror').length) return null;
  var k = kids(doc.documentElement);
  return k.length === list.length ? k : null;
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

/* Свести новые части (strs) с показанными. Одинаковые узлы остаются на
   месте, новые вставляются, пропавшие удаляются. Число частей то же -
   сравниваем по местам; другое (раздел свернули, чип появился, 1.47) -
   ищем совпадение недалеко вперёд: порядок почти всегда тот же.
   Плитку с мгновенным откликом, которую прибор так и не подтвердил,
   возвращаем к тому, что знает сервер. nodesFor(индексы) - узлы для
   новых частей; null - не вышло, нужна полная замена. */
var MERGE_LOOK = 12;
function sync(strs, nodesFor, cg){
  var ck = kids(cg), now = Date.now(), plan = [], need = [], j = 0;
  var same = strs.length === ck.length;
  for (var i = 0; i < strs.length; i++) {
    var at = -1;
    if (same) {
      if (strs[i] === shownParts[i] && !(holds[i] && holds[i] <= now)) at = i;
    } else {
      for (var m = j; m < Math.min(ck.length, j + MERGE_LOOK); m++)
        if (shownParts[m] === strs[i] && !(holds[m] && holds[m] <= now)) { at = m; break; }
      if (at >= 0) j = at + 1;
    }
    plan.push(at);
    if (at < 0) need.push(i);
  }
  if (!need.length && same) { shownParts = strs; return true; }
  var fresh = nodesFor(need);
  if (!fresh) return false;
  var kept = {}, newHolds = {}, f = 0, added = [];
  for (var p = 0; p < plan.length; p++) if (plan[p] >= 0) kept[plan[p]] = true;
  for (var r = 0; r < ck.length; r++) if (!kept[r]) cg.removeChild(ck[r]);
  var cursor = kids(cg)[0] || null;
  for (var q = 0; q < plan.length; q++) {
    if (plan[q] >= 0) {
      if (holds[plan[q]]) newHolds[q] = holds[plan[q]];
      cursor = nextEl(ck[plan[q]]);
    } else {
      var node = document.importNode(fresh[f++], true);
      cg.insertBefore(node, cursor);
      added.push(node);
    }
  }
  shownParts = strs;
  holds = newHolds;
  if (!same) perfMerged++;
  for (var b = 0; b < added.length; b++) bind(added[b]);
  return true;
}

/* Без разбора всей картинки - по тексту (1.48). false - не вышло. */
function patchText(svg, at, headRaw){
  var cur = host.querySelector('svg');
  patchWhy = !cur || !shownParts ? 'первая' : '';
  if (patchWhy) return false;
  if (headKey(headRaw) !== shownHead) { patchWhy = 'голова'; return false; }
  var cg = mainOf(cur);
  if (!cg) { patchWhy = 'нет группы'; return false; }
  if (kids(cg).length !== shownParts.length) { patchWhy = 'раскладка'; return false; }
  var strs = splitMain(svg, at);
  if (!strs) { patchWhy = 'не разрезать'; return false; }
  var ok = sync(strs, function(need){
    var list = [];
    for (var i = 0; i < need.length; i++) list.push(strs[need[i]]);
    return parseParts(list);
  }, cg);
  if (!ok) { patchWhy = 'не разобрать часть'; return false; }
  var sz = sizeOfHead(headRaw);
  sizeTo(cur, sz.h, sz.vb);
  return true;
}

/* По разобранной картинке - когда резка текста не годится. */
function patch(root, headRaw){
  var cur = host.querySelector('svg');
  patchWhy = !cur ? 'первая' : !root ? 'не разобрать' : !shownParts ? 'первая' : '';
  if (patchWhy) return false;
  // Голову сравниваем исходным текстом, а не разобранной и собранной
  // заново: 60 КБ стилей сериализовались на каждом обновлении (1.45).
  var head = headKey(headRaw || headOf(root));
  if (head !== shownHead) { patchWhy = 'голова'; return false; }
  var ng = mainOf(root), cg = mainOf(cur);
  if (!ng || !cg) { patchWhy = 'нет группы'; return false; }
  if (kids(cg).length !== shownParts.length) { patchWhy = 'раскладка'; return false; }
  var nk = kids(ng), strs = [];
  for (var i = 0; i < nk.length; i++) strs.push(XS.serializeToString(nk[i]));
  sync(strs, function(need){
    var out = [];
    for (var n = 0; n < need.length; n++) out.push(nk[need[n]]);
    return out;
  }, cg);
  sizeTo(cur, root.getAttribute('height'), root.getAttribute('viewBox'));
  return true;
}

function full(svg, root, headRaw, at){
  if (root) {
    shownHead = headKey(headRaw || headOf(root));
    var g = mainOf(root), strs = TEXT_PARTS && at > 0 ? splitMain(svg, at) : null;
    // Резка текста должна давать те же части, что и разбор браузера;
    // не сошлось хоть раз - дальше по-старому, через разбор.
    if (strs && strs.length !== kids(g).length) strs = null;
    if (!strs) TEXT_PARTS = false;
    shownParts = strs || partsOf(g);
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

/* Пока панель листают, обновление ждёт (1.48): на старом планшете
   вставка посреди прокрутки - лишний рывок. */
var SCROLL_QUIET = 400;
function scrolling(){ return Date.now() - lastScroll < SCROLL_QUIET; }

function applySvg(svg, seq){
  if (seq < shownSeq) return;
  if (fingerHeld() || scrolling()) {
    if (!pendingSvg || seq > pendingSvg.seq) pendingSvg = {svg: svg, seq: seq};
    if (!fingerHeld()) setTimeout(flushPending, SCROLL_QUIET + 50);
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
  var root = null, patched = false;
  if (TEXT_PARTS && headRaw && XP) patched = patchText(svg, at, headRaw);
  if (!patched) {
    root = parseSvg(svg);
    // Текст не разрезался или часть не разобралась - дальше через разбор
    // всей картинки. Другая голова или раскладка - сразу целиком.
    var cut = patchWhy === 'не разрезать' || patchWhy === 'не разобрать часть';
    if (cut) TEXT_PARTS = false;
    if (cut || !TEXT_PARTS || !headRaw) patched = patch(root, headRaw);
    if (!patched) full(svg, root, headRaw, at);
  }
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
  if (scrolling()) { setTimeout(flushPending, SCROLL_QUIET + 50); return; }
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
