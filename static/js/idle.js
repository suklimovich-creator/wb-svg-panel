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
          fr.onload = function(){ ok(String(fr.result || '')); };
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
function saverPanel(){
  clearTimeout(saverTimer);
  if (!saverOn || !CONTROLS.saver_panel) return;
  var seq = ++saverSeq, cols = saverCols();
  // Тема - устройства, если у панели заставки нет своей.
  fetch(encodeURIComponent(CONTROLS.saver_panel) + '.svg?inline=1' + (LITE ? '&lite=1' : '') +
        (THEME && !CONTROLS.saver_theme ? '&theme=' + THEME : '') +
        (cols ? '&cols=' + cols : '') + '&t=' + Date.now(), {cache: 'no-store'})
    .then(function(r){ return r.ok ? r.text() : ''; })
    .then(saverInline)
    .then(function(svg){
      if (!saverOn || seq !== saverSeq || svg.indexOf('<svg') < 0) return;
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
      img.onload = function(){ if (saverUrl && saverUrl !== url) URL.revokeObjectURL(saverUrl); saverUrl = url; };
      img.src = url;
    })
    .catch(function(){})
    .then(function(){ if (saverOn) saverTimer = setTimeout(saverPanel, 30000); });
}
function saverHide(){
  if (!saverOn) return;
  saverOn = false;
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
