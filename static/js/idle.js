/* ---------- бездействие (1.57) ----------
   Окно на весь экран закрывается само, если его долго не трогали
   (pad_close), а совсем долго без касаний - заставка: своя панель из
   конфига (screensaver.panel - графики, дела, календарь, термостаты)
   картинкой во весь экран поверх обоев, или обои с часами.
   Касание заставки возвращает панель как была - в том же разделе, во
   вкладке, с той же прокруткой. Срок - из конфига, устройство может
   выбрать своё в меню шестерёнки (PADCLOSE_MODE, SAVER_MODE в core.js). */
var idleAt = Date.now(), saverOn = false, saverEl = null, saverMin = -1;
var saverUrl = null, saverTimer = null, saverSeq = 0;
function padCloseSec(){ return PADCLOSE_MODE === '' ? (+CONTROLS.pad_close || 0) : (+PADCLOSE_MODE || 0); }
/* Заставка - дело панели (1.58): где в конфиге её нет, выбор устройства
   в шестерёнке её не включает - он только меняет срок там, где она есть. */
function saverSec(){
  if (!(+CONTROLS.saver > 0)) return 0;
  return SAVER_MODE === '' ? +CONTROLS.saver : (+SAVER_MODE || 0);
}
function idleTouch(){ idleAt = Date.now(); }
['pointerdown', 'touchstart', 'mousedown', 'keydown', 'wheel'].forEach(function(n){
  document.addEventListener(n, idleTouch, true);
});

/* Касание только снимает заставку: до плитки под ней оно не доходит.
   Ловим на window в фазе перехвата - раньше обработчиков страницы
   (жесты и защита от «лишних» нажатий сами гасят события). Снимаем на
   отпускании; click, который придёт следом, тоже гасим. */
var saverGone = 0;
function saverGuard(e){
  if (!saverOn && Date.now() - saverGone > 400) return;
  if (e.cancelable) e.preventDefault();
  e.stopPropagation();
  if (e.stopImmediatePropagation) e.stopImmediatePropagation();
  if (saverOn && /up$|end$/.test(e.type)) saverHide();
}
['pointerdown', 'touchstart', 'mousedown', 'pointerup', 'touchend', 'mouseup', 'click'].forEach(function(n){
  window.addEventListener(n, saverGuard, true);
});

/* Фон заставки: своя картинка; без неё - пятна тёмного стекла или
   просто тёмный экран (часы белые: на светлом их не видно, а ночью
   тёмный экран и не слепит). */
function saverBg(){
  if (CONTROLS.saver_img) return 'url(' + CONTROLS.saver_img + ')';
  var th = rootTheme();
  return (DARK_THEME[th] && PAGE_WALL[th]) || '#0E0F12';
}
function saverTick(){
  if (!saverOn || !saverEl) return;
  var d = new Date(), m = d.getHours() * 60 + d.getMinutes();
  if (m === saverMin) return;
  saverMin = m;
  var c = saverEl.querySelector('.sv-clock');
  if (!c) return;
  var hh = d.getHours(), mm = d.getMinutes();
  var date = '';
  try { date = d.toLocaleDateString('ru-RU', {weekday: 'long', day: 'numeric', month: 'long'}); } catch(e) {}
  c.innerHTML = (hh < 10 ? '0' : '') + hh + ':' + (mm < 10 ? '0' : '') + mm +
                '<div class="sv-date">' + date + '</div>';
  // Часы понемногу переезжают: экран планшета на стене не выгорает.
  c.style.left = (5 + (m * 7) % 23) + 'vw';
  c.style.bottom = (7 + (m * 11) % 19) + 'vh';
}
function saverShow(){
  if (saverOn) return;
  if (ov.classList.contains('open')) closeOv();
  if (menuOpen) closeMenu();
  if (!saverEl) {
    saverEl = document.createElement('div');
    saverEl.id = 'saver';

    document.body.appendChild(saverEl);
  }
  saverEl.style.background = saverBg();
  saverEl.style.backgroundSize = 'cover';
  saverEl.style.backgroundPosition = 'center';
  saverEl.innerHTML = (CONTROLS.saver_panel ? '<img class="sv-panel" alt="">' : '') +
                      (CONTROLS.saver_clock ? '<div class="sv-clock"></div>' : '') +
                      '<div class="sv-hint">коснитесь, чтобы вернуться</div>';
  saverMin = -1;
  saverOn = true;
  saverEl.className = 'on';
  saverTick();
  saverVer = null; saverLast = '';
  saverPanel();
  setTimeout(function(){ if (saverOn) saverEl.className = 'on shown'; }, 30);
}
/* Панель заставки - картинкой (<img>): своих стилей и обработчиков в
   документ она не приносит, с основной панелью не путается. Раз в 30 с
   - свежая: графики и часы в шапке идут.
   Ширина (1.58) - как у панели, на которой заставка открылась: те же
   плиток в ряд (выбор устройства, иначе cols панели), картинка во всю
   ширину от верха, как сама панель; не влезла по высоте - вписана.
   Внешние картинки внутри <img src=svg> браузер не грузит - кадр камеры
   (1.58) страница берёт сама и вставляет в SVG как data:-адрес. */
function saverCols(){ return COLS || CONTROLS.cols || ''; }
function saverInline(svg){
  var re = /(<image\b[^>]*?\shref=")([^"]+)(")/g, urls = [], m;
  while ((m = re.exec(svg))) if (!/^data:/.test(m[2]) && urls.indexOf(m[2]) < 0) urls.push(m[2]);
  if (!urls.length || typeof FileReader === 'undefined') return Promise.resolve(svg);
  return Promise.all(urls.map(function(u){
    return fetch(u.replace(/&amp;/g, '&'), {cache: 'no-store'})
      .then(function(r){ return r.ok ? r.blob() : null; })
      .then(function(b){
        if (!b) return '';
        return new Promise(function(ok){
          var fr = new FileReader();
          // Тип - только «image/jpeg»: камера присылает и параметры с
          // кавычками (charset="UTF-8"), они рвали атрибут href (1.68).
          fr.onload = function(){
            var mime = String(b.type || '').split(';')[0].replace(/[^a-z0-9\/+.-]/gi, '') || 'image/jpeg';
            ok(String(fr.result || '').replace(/^data:[^,]*;base64,/, 'data:' + mime + ';base64,').replace(/"/g, ''));
          };
          fr.onerror = function(){ ok(''); };
          fr.readAsDataURL(b);
        });
      })
      .catch(function(){ return ''; });
  })).then(function(data){
    for (var i = 0; i < urls.length; i++) if (data[i]) svg = svg.split('"' + urls[i] + '"').join('"' + data[i] + '"');
    return svg;
  });
}
/* С 1.69 заставка обновляется, как панель: ждущий запрос с since=версия -
   сервер отвечает, как только поменялось что-то на панели заставки (или
   раз в refresh секунд); раньше - раз в 30 с вслепую. Та же картинка -
   не перерисовываем. */
var saverVer = null, saverLast = '', saverAbort = null;
function saverPanel(){
  clearTimeout(saverTimer);
  if (!saverOn || !CONTROLS.saver_panel) return;
  var seq = ++saverSeq, cols = saverCols(), again = 300;
  saverAbort = window.AbortController ? new AbortController() : null;
  // Тема - устройства, если у панели заставки нет своей.
  fetch(encodeURIComponent(CONTROLS.saver_panel) + '.svg?inline=1' + (LITE ? '&lite=1' : '') +
        (THEME && !CONTROLS.saver_theme ? '&theme=' + THEME : '') +
        (cols ? '&cols=' + cols : '') + (saverVer !== null ? '&since=' + saverVer : '') +
        '&t=' + Date.now(), saverAbort ? {cache: 'no-store', signal: saverAbort.signal} : {cache: 'no-store'})
    .then(function(r){
      var v = r.headers && r.headers.get ? r.headers.get('X-Panel-Version') : null;
      if (v !== null && v !== '') saverVer = parseInt(v, 10);
      if (r.status === 204) { again = 2000; return null; }   // мест для ждущих нет
      if (!r.ok) { again = 5000; return ''; }
      return r.text();
    })
    .then(function(svg){
      if (svg === null || seq !== saverSeq || !saverOn) return null;
      if (svg && svg === saverLast) return null;          // ничего не поменялось
      saverLast = svg;
      return svg;
    })
    // управляющие символы (из календаря, списков) XML не разберёт - и
    // картинки не будет вовсе (1.65)
    .then(function(svg){
      if (svg === null) return null;
      svg = svg.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, ''); saverCamsFrom(svg); return svg;
    })
    .then(function(svg){ return svg === null ? null : saverInline(svg); })
    .then(function(svg){
      if (svg === null || !saverOn || seq !== saverSeq) return;
      if (svg.indexOf('<svg') < 0) { saverFail(svg ? 'нет картинки' : 'нет ответа'); again = 5000; return; }
      var img = saverEl.querySelector('.sv-panel');
      if (!img) return;
      // фон вокруг - поле темы панели, если своей картинки нет
      if (!CONTROLS.saver_img) {
        var m = /<svg[^>]*class="([^"]*)"/.exec(svg), cls = m ? m[1].split(/\s+/) : [];
        for (var i = cls.length - 1; i >= 0; i--) if (PAGE_BG[cls[i]]) {
          saverEl.style.background = PAGE_WALL[cls[i]] || PAGE_BG[cls[i]]; break;
        }
      }
      var url = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
      img.onload = function(){
        if (saverUrl && saverUrl !== url) URL.revokeObjectURL(saverUrl);
        saverUrl = url; img.style.display = ''; saverMarkup(null); saverFail(null);
        saverCamLayout();
      };
      // Картинкой не разобралась (1.67): показываем ту же панель разметкой
      // (как сама страница - там она открывается), а причину - в подсказке
      // и в консоли: что сказал разборщик XML, если он вообще возражает.
      img.onerror = function(){
        URL.revokeObjectURL(url);
        var why = '';
        try {
          var pe = new DOMParser().parseFromString(svg, 'image/svg+xml').getElementsByTagName('parsererror')[0];
          if (pe) why = (pe.textContent || '').replace(/\s+/g, ' ').slice(0, 140);
        } catch (e) {}
        if (window.console && console.warn) console.warn('заставка: картинка не разобралась', why);
        if (saverMarkup(svg)) {
          SAVER_ERR = 'картинкой не разобралась' + (why ? ': ' + why : '') + ' - показана разметкой';
          var h = saverEl.querySelector('.sv-hint');
          if (h) h.textContent = 'коснитесь, чтобы вернуться · разметкой' + (why ? ': ' + why : '');
          img.style.display = 'none';
        } else saverFail('картинка не разобралась' + (why ? ': ' + why : ''));
      };
      img.src = url;
    })
    .catch(function(e){
      if (!saverOn || seq !== saverSeq) return;
      saverFail('ошибка ' + (e && e.message || e)); again = 5000;
    })
    .then(function(){ if (saverOn && seq === saverSeq) saverTimer = setTimeout(saverPanel, again); });
}
/* Запасной путь (1.67): панель заставки разметкой поверх фона, без
   обработчиков (pointer-events: none - касание всё равно снимает заставку).
   Стили её <style> действуют на весь документ, пока она показана, - тема
   та же, что у панели под ней, так что это незаметно; уходит вместе с
   заставкой. null - убрать. */
function saverMarkup(svg){
  if (!saverEl) return false;
  var box = saverEl.querySelector('.sv-inline');
  if (svg === null) { if (box) box.parentNode.removeChild(box); return true; }
  try {
    if (!box) {
      box = document.createElement('div'); box.className = 'sv-inline';
      saverEl.insertBefore(box, saverEl.firstChild);
    }
    box.innerHTML = svg.replace(/^[\s\S]*?(<svg\b)/, '$1').replace(/<script\b[\s\S]*?<\/script>/gi, '');
    var clock = saverEl.querySelector('.sv-clock');
    if (clock && !CONTROLS.saver_clock) clock.parentNode.removeChild(clock);
    return !!box.querySelector('svg');
  } catch (e) { return false; }
}
/* Панель заставки не пришла или не разобралась: вместо пустоты и значка
   битой картинки - часы с датой, причина - в window.SAVER_ERR (и в
   подсказке внизу). Следующая удачная картинка возвращает панель. */
var SAVER_ERR = null;
function saverFail(why){
  SAVER_ERR = why;
  if (!saverEl) return;
  var img = saverEl.querySelector('.sv-panel'), clock = saverEl.querySelector('.sv-clock');
  var hint = saverEl.querySelector('.sv-hint');
  if (why) {
    if (img) img.style.display = 'none';
    if (!clock) {
      clock = document.createElement('div'); clock.className = 'sv-clock';
      saverEl.insertBefore(clock, hint); saverMin = -1; saverTick();
    }
    if (hint) hint.textContent = 'коснитесь, чтобы вернуться · заставка: ' + why;
  } else {
    if (clock && !CONTROLS.saver_clock) clock.parentNode.removeChild(clock);
    if (hint) hint.textContent = 'коснитесь, чтобы вернуться';
  }
}
/* ---------- кадр камеры чаще панели (1.69) ----------
   refresh камеры меньше, чем панель обновляется (10 с) или заставка
   (30 с), - кадр меняет сама страница: на панели - адрес <image> плитки,
   в заставке - картинка поверх кадра на месте плитки (заставка - одна
   картинка, менять в ней кусок нельзя). Новый кадр сначала грузится
   целиком, потом подменяет старый: без моргания. */
var saverCams = [], saverVB = null, camLast = {};
function saverCamsFrom(svg){
  saverCams = [];
  var vb = /<svg\b[^>]*viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"/.exec(svg);
  saverVB = vb ? {w: +vb[3], h: +vb[4]} : null;
  var re = /<g transform="translate\(([-\d.]+),([-\d.]+)\)"[^>]*?data-cam-poll="([^"]+)"[^>]*?data-cam-every="([^"]+)"[^>]*>/g, m;
  while ((m = re.exec(svg))) {
    var tail = svg.slice(re.lastIndex, re.lastIndex + 4000);
    var im = /<image\b[^>]*?\sx="([-\d.]+)"\s+y="([-\d.]+)"\s+width="([\d.]+)"\s+height="([\d.]+)"/.exec(tail);
    var rim = /rx="([\d.]+)" class="rim"/.exec(tail);
    if (!im || !(+m[4] < 30)) continue;
    saverCams.push({poll: m[3].replace(/&amp;/g, '&'), every: +m[4],
                    x: +m[1] + +im[1], y: +m[2] + +im[2], w: +im[3], h: +im[4], rx: rim ? +rim[1] : 0});
  }
}
function saverCamLayout(){
  if (!saverEl) return;
  var old = saverEl.querySelectorAll('.sv-cam'), img = saverEl.querySelector('.sv-panel');
  var keep = {};
  for (var i = 0; i < old.length; i++) keep[old[i].getAttribute('data-poll')] = old[i];
  if (!img || !saverVB || img.style.display === 'none') saverCams = [];
  var cw = img ? img.clientWidth : 0, ch = img ? img.clientHeight : 0;
  var k = Math.min(cw / saverVB.w || 0, ch / saverVB.h || 0), ox = (img ? img.offsetLeft : 0) + (cw - saverVB.w * k) / 2,
      oy = img ? img.offsetTop : 0;
  for (var j = 0; j < saverCams.length; j++) {
    var c = saverCams[j], el = keep[c.poll];
    if (!el) {
      el = document.createElement('img'); el.className = 'sv-cam'; el.alt = '';
      el.setAttribute('data-poll', c.poll);
      saverEl.insertBefore(el, saverEl.querySelector('.sv-hint'));
    }
    delete keep[c.poll];
    el.style.left = Math.round(ox + c.x * k) + 'px'; el.style.top = Math.round(oy + c.y * k) + 'px';
    el.style.width = Math.round(c.w * k) + 'px'; el.style.height = Math.round(c.h * k) + 'px';
    el.style.borderRadius = Math.round(c.rx * k) + 'px';
  }
  for (var p in keep) if (keep.hasOwnProperty(p)) keep[p].parentNode.removeChild(keep[p]);
}
function camFrame(poll, every, put){
  var now = Date.now();
  if (camLast[poll] && now - camLast[poll] < every * 1000 - 100) return;
  camLast[poll] = now;
  var pre = new Image(), src = poll + (poll.indexOf('?') < 0 ? '?' : '&') + 'live=1&t=' + now;
  pre.onload = function(){ put(src); };
  pre.src = src;
}
setInterval(function(){
  if (document.hidden) return;
  if (saverOn) {
    for (var i = 0; i < saverCams.length; i++) (function(c){
      var el = saverEl && saverEl.querySelector('.sv-cam[data-poll="' + c.poll + '"]');
      if (el) camFrame(c.poll, c.every, function(src){ el.src = src; });
    })(saverCams[i]);
    return;
  }
  if (ov.classList.contains('open')) return;
  var tiles = host.querySelectorAll('[data-cam-poll][data-cam-every]');
  for (var j = 0; j < tiles.length; j++) (function(t){
    var every = +t.getAttribute('data-cam-every');
    if (!(every > 0 && every < 10)) return;
    var image = t.querySelector('image');
    if (image) camFrame(t.getAttribute('data-cam-poll'), every, function(src){
      image.setAttribute('href', src);
      image.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', src);
    });
  })(tiles[j]);
}, 250);
window.addEventListener('resize', function(){ if (saverOn) saverCamLayout(); });
function saverHide(){
  if (!saverOn) return;
  saverOn = false;
  saverMarkup(null);              // её стили - только пока она на экране
  saverSeq++;                     // ждущий запрос заставки больше не нужен
  if (saverAbort) { try { saverAbort.abort(); } catch (e) {} saverAbort = null; }
  clearTimeout(saverTimer);
  saverGone = Date.now();
  idleTouch();
  shieldUntil = Date.now() + SHIELD_MS;
  saverEl.className = 'on';
  setTimeout(function(){ if (!saverOn) saverEl.className = ''; }, 350);
  refresh(true);
}
setInterval(function(){
  var idle = (Date.now() - idleAt) / 1000, pc = padCloseSec(), sv = saverSec();
  if (pc > 0 && idle >= pc && ov.classList.contains('open')) closeOv();
  if (sv > 0 && idle >= sv && !saverOn) saverShow();
  saverTick();
}, 1000);
