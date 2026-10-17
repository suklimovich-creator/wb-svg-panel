/* wb-svg-panel: настройки страницы, колонки и тема, кнопки шапки и меню шестерёнки, замер ?perf=1, предохранители касаний.
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* NAME, EVERY, APP, appReload, THEME_LIST, INTERACTIVE - задаются в
   wrapper.html: их подставляет сервер. */
/* Витрина тем (/themes, с 1.28): та же страница на выдуманной панели.
   still=1 - миниатюра: без ожидания изменений и без шагов истории;
   pad=<вид> - сразу открыть пульт этого вида (xy, level, ac…). */
var DEMO = NAME === '_demo';
var STILL = DEMO && /[?&]still=1/.test(location.search);
var AUTO_PAD = DEMO ? ((location.search.match(/[?&]pad=([\w-]+)/) || [])[1] || '') : '';
/* Тему и колонки витрины выбирает страница /themes вокруг неё. */
if (DEMO) {
  document.getElementById('back').style.display = 'none';
  document.getElementById('cols').style.display = 'none';
  document.getElementById('host').style.paddingTop = '10px';
}

/* Запасной вход на 8080 не закрыт паролем: кто попал в домашнюю сеть, тот
   управляет светом и шторами. Годится, чтобы попробовать, но не как
   постоянный режим - поэтому и написано «тестовый». */
(function(){
  if (location.port !== '8080') return;
  var w = document.getElementById('warn8080');
  if (!w) return;
  w.innerHTML = '<b>Тестовый режим:</b> открыто через порт 8080, без пароля. ' +
                'Постоянный адрес — <a href="/panel/' + NAME + '.html">/panel/</a>';
  w.style.display = 'block';
  document.body.className += ' warned';
})();

/* Число колонок.
   Берём из адреса, иначе из памяти браузера, иначе оставляем как в конфиге.
   Запоминаем, чтобы при переходе на другую панель раскладка не сбрасывалась:
   у горизонтальных экранов это каждый раз раздражало. */
var STORE = 'wb-panel-cols';
function readCols(){
  var m = location.search.match(/[?&]cols=(\d+)/);
  if (m) return m[1];
  try { return localStorage.getItem(STORE) || ''; } catch(e) { return ''; }
}
var COLS = readCols();

/* Тема - тоже выбор устройства: на стене в коридоре тёмная, на телефоне
   светлая. Пусто - как в конфиге. Уезжает на сервер параметром theme,
   ему же отдаётся картинка раскрытой плитки и графика. */
var THEME_STORE = 'wb-panel-theme';
/* Темы приходят с сервера (с 1.24): [{name, title, dark, bg}] из данных
   тем - и меню, и цвет полей, и светлый ли пульт. Своей копии списка в
   скрипте больше нет. */
/* THEME_LIST - задаётся в wrapper.html (подставляет сервер) */
var THEMES = [['', 'как в конфиге']], PAGE_BG = {}, DARK_THEME = {}, THEME_INFO = {}, PAGE_WALL = {};
THEME_LIST.forEach(function(t){
  THEMES.push([t.name, t.title]); PAGE_BG[t.name] = t.bg; DARK_THEME[t.name] = !!t.dark;
  PAGE_WALL[t.name] = t.wall || '';
  THEME_INFO[t.name] = t;
});
var THEME = (function(){
  var m = location.search.match(/[?&]theme=([\w-]+)/);
  if (m) return m[1];
  try { return localStorage.getItem(THEME_STORE) || ''; } catch(e) { return ''; }
})();
function saveTheme(v){
  THEME = v;
  if (DEMO) return;          // выбор на витрине - не выбор этого устройства
  try { v ? localStorage.setItem(THEME_STORE, v) : localStorage.removeItem(THEME_STORE); } catch(e) {}
}
function themeQ(){ return THEME ? '&theme=' + THEME : ''; }
/* Поля страницы вокруг панели - в цвет низа её фона: у светлых тем
   чёрная рамка вокруг выглядела чужой. Тему узнаём по классу корня. */
var pageBgNow = '';
function pageBg(root){
  root = root || host.querySelector('svg');
  var name = rootTheme(root);
  var bg = PAGE_BG[name], wall = PAGE_WALL[name] || '';
  if (!bg || bg + wall === pageBgNow) return;
  pageBgNow = bg + wall;
  document.documentElement.style.background = bg;
  // Стекло (1.40): обои - неподвижный слой под страницей. Фон body
  // прозрачный, иначе он лёг бы поверх слоя.
  var pw = document.getElementById('pagewall');
  if (wall) {
    if (!pw) { pw = document.createElement('div'); pw.id = 'pagewall'; document.body.insertBefore(pw, document.body.firstChild); }
    pw.style.background = wall;
    document.body.style.background = 'transparent';
  } else {
    if (pw) pw.parentNode.removeChild(pw);
    document.body.style.background = bg;
  }
  glassScroll();
}
/* Отсвет стекла (1.40) - свет сверху экрана. Градиент в координатах
   картинки сдвигается на величину прокрутки: обои стоят, плитки едут под
   светом. Один атрибут на кадр. */
var glassRaf = 0;
function glassScroll(){
  var g = host.querySelector('[data-glare]'), svg = host.querySelector('svg');
  if (!g || !svg) return;
  var W = +(svg.getAttribute('viewBox') || '0 0 0 0').split(' ')[2] || 1;
  var r = svg.getBoundingClientRect(), k = (r.width || W) / W;
  g.setAttribute('gradientTransform', 'translate(0 ' + (-r.top / k).toFixed(1) + ')');
  // Обои в картинке (линза под стеклом, 1.42) - ровно окно браузера: пятна
  // под плиткой совпадают с неподвижным фоном страницы.
  var wall = host.querySelector('[data-wall]');
  if (wall) {
    var vw = document.documentElement.clientWidth || window.innerWidth, vh = window.innerHeight;
    var x = (-r.left / k).toFixed(1), y = (-r.top / k).toFixed(1);
    var w = (vw / k).toFixed(1), h = (vh / k).toFixed(1), rs = wall.querySelectorAll('rect');
    for (var i = 0; i < rs.length; i++) {
      rs[i].setAttribute('x', x); rs[i].setAttribute('y', y);
      rs[i].setAttribute('width', w); rs[i].setAttribute('height', h);
    }
  }
}
window.addEventListener('scroll', function(){
  if (glassRaf) return;
  glassRaf = (window.requestAnimationFrame || setTimeout)(function(){ glassRaf = 0; glassScroll(); });
}, {passive: true});
window.addEventListener('resize', glassScroll);

/* Кнопка «назад». Если пришли с другой панели по плитке-ссылке, вернуться
   логичнее туда, а не в общий список: человек проваливался в комнату,
   а не начинал обход заново. Имя панели передаётся параметром from.

   Запоминаем в переменной, а не только в href: fixBack() перебивает адрес
   кнопки, когда меняется число колонок, - и без этого возврат молча
   превращался в переход к списку панелей. */
var BACK_TO = (function(){
  var m = location.search.match(/[?&]from=([\w.-]+)/);
  return m ? m[1] : '';
})();
function saveCols(v){
  COLS = v;
  if (DEMO) return;
  try { v ? localStorage.setItem(STORE, v) : localStorage.removeItem(STORE); } catch(e) {}
}
/* Свёрнутые разделы. Список свой на каждом устройстве: на планшете в
   коридоре и на телефоне нужны разные комнаты. Хранится рядом с числом
   колонок и уезжает на сервер параметром - панель цельная картинка, и
   спрятать плитки на стороне браузера нельзя, останутся дыры. */
var FOLD_KEY = 'wb-panel-fold:' + NAME;
var FOLDED = (function(){
  try { return JSON.parse(localStorage.getItem(FOLD_KEY) || '[]') || []; }
  catch(e) { return []; }
})();
var OPENED = (function(){
  try { return localStorage.getItem(FOLD_KEY + ':open') || ''; }
  catch(e) { return ''; }
})();
/* Вид разделов: list, accordion, tabs. SECT_MODE - что нарисовал сервер
   (узнаём из разметки), MODE - выбор этого устройства в меню (с 1.36):
   планшету на стене вкладки, телефону гармошка. Пусто - как в конфиге. */
var SECT_MODE = 'list', HAS_SECTIONS = false;
var MODE = (function(){
  try { return localStorage.getItem(FOLD_KEY + ':mode') || ''; }
  catch(e) { return ''; }
})();
var MODES = [['', 'как в конфиге'], ['list', 'списком'],
             ['accordion', 'гармошкой'], ['tabs', 'вкладками']];
/* Полоса вкладок у края окна. В картинке она стоит над шапкой или под
   плитками, а страница прикалывает её копию к верху или низу экрана - при
   прокрутке длинной комнаты вкладки остаются под пальцем. Сама полоса в
   картинке невидима и держит под копию место. */
var DOCK = '';
function dockBar(){
  var bar = host.querySelector('[data-tabbar]');
  var dock = document.getElementById('tabdock');
  var cls = document.documentElement.classList;
  DOCK = bar ? bar.getAttribute('data-tabbar') : '';
  cls.toggle('dock-top', DOCK === 'top');
  cls.toggle('dock-bottom', DOCK === 'bottom');
  if (!bar) { if (dock) dock.parentNode.removeChild(dock); return; }
  var svg = host.querySelector('svg');
  var W = +(svg.getAttribute('viewBox') || '0 0 0 0').split(' ')[2];
  var y = +bar.getAttribute('data-bar-y'), h = +bar.getAttribute('data-bar-h');
  if (!dock) {
    dock = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    dock.id = 'tabdock';
    document.body.appendChild(dock);
  }
  dock.setAttribute('class', svg.getAttribute('class') || '');
  dock.setAttribute('style', svg.getAttribute('style') || '');
  dock.setAttribute('viewBox', '0 ' + y + ' ' + W + ' ' + h);
  dock.setAttribute('preserveAspectRatio', 'none');
  // Высота - в масштабе панели на странице: полоса той же толщины, что
  // была бы в картинке, а по ширине - от края до края окна.
  var k = (svg.getBoundingClientRect().width || W) / W;
  dock.style.height = Math.round(h * k) + 'px';
  var copy = bar.cloneNode(true);
  copy.removeAttribute('data-tabbar');
  // Пилюли растягивать нельзя: полоса шире панели на поля страницы.
  // Ряд пересчитываем по центру окна в единицах картинки.
  var scaleX = (document.documentElement.clientWidth || window.innerWidth) / (W * k);
  var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('transform', 'translate(' + (W / 2) + ' 0) scale(' + (1 / scaleX) + ' 1) translate(' + (-W / 2) + ' 0)');
  var plates = copy.querySelectorAll('.tabbar,.tabbar-line');
  for (var i = 0; i < plates.length; i++) plates[i].parentNode.removeChild(plates[i]);
  for (var j = 0; j < plates.length; j++) copy.insertBefore(plates[j], copy.firstChild);
  var tabs = copy.querySelectorAll('[data-tab]');
  for (var t = 0; t < tabs.length; t++) g.appendChild(tabs[t]);
  copy.appendChild(g);
  while (dock.firstChild) dock.removeChild(dock.firstChild);
  dock.appendChild(copy);
  var btns = dock.querySelectorAll('[data-tab]');
  for (var n = 0; n < btns.length; n++)
    btns[n].onclick = function(){ if (tapOk()) toggleSection(this); };
  document.documentElement.style.setProperty('--dock-h', dock.style.height);
}
var chromeTimer = null;
function showChrome(){
  if (!tapOk()) return;
  var cls = document.documentElement.classList;
  cls.add('chrome-on');
  clearTimeout(chromeTimer);
  function hide(){
    // Пока открыт выбор колонок или темы - не прячем.
    if (colsOpen) { chromeTimer = setTimeout(hide, 2000); return; }
    cls.remove('chrome-on');
  }
  chromeTimer = setTimeout(hide, 6000);
}
function oneOpen(){ var m = MODE || SECT_MODE; return m === 'accordion' || m === 'tabs'; }

function saveFold(){
  try {
    localStorage.setItem(FOLD_KEY, JSON.stringify(FOLDED));
    localStorage.setItem(FOLD_KEY + ':open', OPENED);
    MODE ? localStorage.setItem(FOLD_KEY + ':mode', MODE)
         : localStorage.removeItem(FOLD_KEY + ':mode');
  } catch(e) {}
}

function toggleSection(node){
  var key = node.getAttribute('data-section');
  if (!key) return;
  if (node.getAttribute('data-tab')) {
    // Вкладка: открытая остаётся открытой - пустого экрана под рядом
    // вкладок не бывает.
    if (node.getAttribute('data-folded') !== '1') return;
    OPENED = key;
  } else if (SECT_MODE === 'accordion') {
    // В гармошке раскрыт ровно один раздел. Нажатие на свёрнутый
    // раскрывает его, на раскрытый - сворачивает всё: список одних
    // заголовков это самый быстрый способ дойти до нужной комнаты.
    // Сервер понимает "-" как «свёрнуты все».
    OPENED = node.getAttribute('data-folded') === '1' ? key : '-';
  } else {
    var i = FOLDED.indexOf(key);
    if (i < 0) FOLDED.push(key); else FOLDED.splice(i, 1);
  }
  saveFold();
  // Это состояние свёртки уже видели - показываем его сразу, а свежие
  // данные догонят через долю секунды. Нет - притушиваем панель, чтобы
  // было видно, что нажатие принято, и ждём сервер.
  var hit = svgCache[Q()];
  if (hit) {
    applySvg(hit.svg, ++reqSeq);
    // Картинка могла пролежать долго - пусть остаётся притушенной, пока
    // не придёт свежая: старое значение не должно выглядеть текущим.
    if (Date.now() - hit.at > EVERY * 3) host.classList.add('wait');
  } else {
    host.classList.add('wait');
  }
  refresh(true);
  restartPoll();
}

function Q(){
  var parts = [];
  // кнопки страницы - в шапке картинки (1.44); витрине тем они не нужны
  if (!DEMO) parts.push('nav=' + NAV_S);
  if (LITE) parts.push('lite=1');
  if (COLS) parts.push('cols=' + COLS);
  if (THEME) parts.push('theme=' + THEME);
  if (MODE) parts.push('mode=' + MODE);
  if (oneOpen()) {
    if (OPENED) parts.push('open=' + encodeURIComponent(OPENED));
  } else if (FOLDED.length) {
    parts.push('closed=' + FOLDED.map(encodeURIComponent).join(','));
  }
  return parts.length ? ('?' + parts.join('&')) : '';
}

/* Ссылка «назад» тоже должна нести выбранное число колонок: на широком
   экране незачем терять раскладку из-за перехода на соседнюю панель. */
function fixBack(){
  var b = document.getElementById('back');
  if (!b) return;
  if (BACK_TO) {
    b.href = BACK_TO + '.html' + Q();
    b.textContent = '← ' + decodeURIComponent(BACK_TO);
  } else {
    b.href = './' + Q();
  }
}

/* Выбор колонок свёрнут в одну кнопку «3 ▾»: полный ряд из девяти
   вариантов шириной в треть экрана закрывал шапку и чипы. Нажатие
   раскрывает ряд, выбор или пять секунд без выбора - сворачивают. */
var colsOpen = '', colsTimer = null;   // '' - свёрнуто, 'cols' или 'theme'
function themeName(v){
  for (var i = 0; i < THEMES.length; i++) if (THEMES[i][0] === v) return THEMES[i][1];
  return v;
}
function modeName(v){
  for (var i = 0; i < MODES.length; i++) if (MODES[i][0] === v) return MODES[i][1];
  return v;
}
function drawCols(){
  fixBack();
  var box = document.getElementById('cols'), html = '';
  if (!colsOpen) {
    html = '<b class="cur" data-open="cols">' + (COLS === '' ? 'авто' : COLS)
         + ' кол. ▾</b><b class="cur" data-open="theme">'
         + (THEME ? themeName(THEME) : 'тема') + ' ▾</b>';
    // Вид разделов - только у панели с разделами.
    if (HAS_SECTIONS)
      html += '<b class="cur" data-open="mode">' + (MODE ? modeName(MODE) : 'вид') + ' ▾</b>';
  } else if (colsOpen === 'mode') {
    for (var m = 0; m < MODES.length; m++) {
      html += '<b data-m="' + MODES[m][0] + '"' + (MODES[m][0] === MODE ? ' class="on"' : '')
            + '>' + MODES[m][1] + '</b>';
    }
  } else if (colsOpen === 'cols') {
    var opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'];
    for (var i = 0; i < opts.length; i++) {
      var v = opts[i];
      html += '<b data-v="' + v + '"' + (v === COLS ? ' class="on"' : '') + '>'
            + (v === '' ? 'авто' : v) + '</b>';
    }
  } else {
    for (var k = 0; k < THEMES.length; k++) {
      html += '<b data-t="' + THEMES[k][0] + '"' + (THEMES[k][0] === THEME ? ' class="on"' : '')
            + '>' + THEMES[k][1] + '</b>';
    }
    // Все темы на всех плитках и пультах - страница /themes (с 1.28).
    html += '<b data-go="themes">все темы…</b>';
  }
  box.innerHTML = html;
  var btns = box.getElementsByTagName('b');
  for (var j = 0; j < btns.length; j++) {
    btns[j].onclick = function(){
      clearTimeout(colsTimer);
      var open = this.getAttribute('data-open');
      if (open) {
        colsOpen = open; drawCols();
        colsTimer = setTimeout(function(){ colsOpen = ''; drawCols(); }, 5000);
        return;
      }
      colsOpen = '';
      if (this.hasAttribute('data-go')) {
        location.href = 'themes' + (THEME ? '?theme=' + THEME : ''); return;
      }
      if (this.hasAttribute('data-t')) saveTheme(this.getAttribute('data-t'));
      else if (this.hasAttribute('data-m')) { MODE = this.getAttribute('data-m'); saveFold(); }
      else saveCols(this.getAttribute('data-v'));
      drawCols();
      refresh();
      restartPoll();
    };
  }
  box.className = 'touched';
  setTimeout(function(){ box.className = ''; }, 1800);
}
/* ---------- кнопки в шапке (1.44) ----------
   «Назад» - туда же, куда вела прежняя «← Панели» (с учётом from=).
   Шестерёнка открывает меню: колонки, тема, вид разделов. Выбор
   применяется сразу, меню остаётся открытым - видно, как стало; закрывает
   его шестерёнка или нажатие мимо. */
var menuOpen = false;
/* Во сколько раз крупнее кнопки шапки: картинка ужата под экран (панель
   на 4 колонки на телефоне - вдвое), а палец - нет. Считаем по последней
   картинке; поменялось - перезапросим. */
/* «Облегчённо» (1.45): выбор этого устройства - без теней, линзы и отсвета
   стекла, анимации на паузе. Для слабых планшетов. */
/* С 1.47 по умолчанию «авто»: слабое устройство - сразу облегчённо. На
   планшете с Android 5.1 и Firefox 68 тени панели на 4 колонки (два
   размытия на всю её высоту) давали 10 кадров в секунду при прокрутке и
   замирания на десятки секунд. Признаки слабого: нет PointerEvent (старый
   движок), Android до 8, памяти до 2 ГБ, ядер до 2. */
function weakDevice(){
  var ua = navigator.userAgent || '';
  if (!window.PointerEvent) return true;
  if (/Android [1-7][.;)]/.test(ua)) return true;
  if (navigator.deviceMemory && navigator.deviceMemory <= 2) return true;
  if (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) return true;
  return false;
}
var LITE_MODE = (function(){   // 'auto' | '0' | '1' - выбор этого устройства
  try { return localStorage.getItem('wb-panel-lite') || 'auto'; } catch(e) { return 'auto'; }
})();
var LITE = LITE_MODE === '1' || (LITE_MODE === 'auto' && weakDevice());
function saveLite(mode){
  LITE_MODE = mode;
  LITE = mode === '1' || (mode === 'auto' && weakDevice());
  try { mode === 'auto' ? localStorage.removeItem('wb-panel-lite') : localStorage.setItem('wb-panel-lite', mode); } catch(e) {}
}
var NAV_S = (function(){ try { return localStorage.getItem('wb-panel-navs') || '1.0'; } catch(e) { return '1.0'; } })();
var navScaleAt = 0;
function navScale(){
  var svg = host.querySelector('svg');
  if (!svg || !svg.getAttribute('viewBox')) return;
  var W = +svg.getAttribute('viewBox').split(' ')[2] || 0, shown = svg.getBoundingClientRect().width;
  if (!W || !shown) return;
  var k = Math.min(1.6, Math.max(1, W / shown));
  var want = (Math.round(k * 5) / 5).toFixed(1);
  if (want === NAV_S) return;
  // Не чаще раза в 10 с: на границе округления (полоса прокрутки, поворот)
  // масштаб мог бы прыгать туда-обратно, и каждая смена - новая картинка.
  if (Date.now() - navScaleAt < 10000 && Math.abs(want - NAV_S) < 0.3) return;
  navScaleAt = Date.now();
  NAV_S = want;
  try { localStorage.setItem('wb-panel-navs', want); } catch(e) {}
  refresh(true);
}
function navTap(e){
  if (!tapOk()) return;
  var what = this.getAttribute('data-nav');
  if (what === 'back') { fixBack(); location.href = document.getElementById('back').href; return; }
  if (what === 'menu') { menuOpen ? closeMenu() : openMenu(this); }
  if (e && e.stopPropagation) e.stopPropagation();
}
function openMenu(anchor){
  menuOpen = true;
  var m = document.getElementById('navmenu');
  var r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
  if (r) {
    m.style.top = Math.max(8, Math.round(r.bottom + 8)) + 'px';
    m.style.right = Math.max(8, Math.round(window.innerWidth - r.right)) + 'px';
  }
  drawMenu();
  m.classList.add('open');
}
function closeMenu(){
  menuOpen = false;
  document.getElementById('navmenu').classList.remove('open');
}
function drawMenu(){
  var m = document.getElementById('navmenu');
  var th = rootTheme(), info = THEME_INFO[th] || {};
  m.classList.toggle('lt', !!PAGE_BG[th] && !DARK_THEME[th]);
  if (m.style.setProperty) {
    m.style.setProperty('--macc', info.acc || '#E09B2D');
    m.style.setProperty('--macc-ink', info.accInk || '#1B1B1F');
  }
  function row(cls, items, cur, attr){
    var h = '<div class="mrow' + (cls ? ' ' + cls : '') + '">';
    items.forEach(function(it){
      h += '<b ' + attr + '="' + it[0] + '"' + (it[0] === cur ? ' class="on"' : '') + '>' + it[1] + '</b>';
    });
    return h + '</div>';
  }
  var cols = [['', 'авто']].concat(['2','3','4','5','6','7','8','9'].map(function(v){ return [v, v]; }));
  var html = '<div class="mh">Плиток в ряд</div>' + row('num', cols, COLS, 'data-v')
           + '<div class="mh">Тема</div>' + row('', THEMES, THEME, 'data-t');
  if (HAS_SECTIONS) html += '<div class="mh">Разделы</div>' + row('', MODES, MODE, 'data-m');
  html += '<div class="mh">Это устройство</div>'
        + row('', [['auto', 'авто' + (weakDevice() ? ' (облегчённо)' : '')],
                   ['0', 'как есть'], ['1', 'облегчённо']], LITE_MODE, 'data-lite');
  html += '<a class="mlink" data-go="themes">все темы на витрине…</a>';
  m.innerHTML = html;
  var btns = m.querySelectorAll('[data-v],[data-t],[data-m],[data-go],[data-lite]');
  for (var i = 0; i < btns.length; i++) btns[i].onclick = function(e){
    e.stopPropagation();
    if (this.hasAttribute('data-go')) { location.href = 'themes' + (THEME ? '?theme=' + THEME : ''); return; }
    if (this.hasAttribute('data-t')) saveTheme(this.getAttribute('data-t'));
    else if (this.hasAttribute('data-m')) { MODE = this.getAttribute('data-m'); saveFold(); }
    else if (this.hasAttribute('data-lite')) saveLite(this.getAttribute('data-lite'));
    else saveCols(this.getAttribute('data-v'));
    drawCols();
    drawMenu();
    refresh();
    restartPoll();
  };
}
// Нажатие мимо меню закрывает его (и не доходит до плитки под ним). Без
// PointerEvent (Firefox 68 на Android) - касанием и мышью.
function menuOutside(e){
  if (!menuOpen) return;
  var m = document.getElementById('navmenu');
  for (var n = e.target; n; n = n.parentNode) {
    if (n === m || (n.getAttribute && n.getAttribute('data-nav') === 'menu')) return;
  }
  closeMenu();
  e.preventDefault(); e.stopPropagation();
  shieldUntil = Date.now() + 350;
}
(window.PointerEvent ? ['pointerdown'] : ['touchstart', 'mousedown']).forEach(function(n){
  document.addEventListener(n, menuOutside, true);
});

/* ---------- замер (?perf=1, 1.45) ----------
   Для старого планшета: что браузер умеет, сколько весит обновление,
   сколько миллисекунд уходит на его разбор и вставку и как идёт прокрутка.
   Снимок экрана с этой плашкой - всё, что нужно для разбора. */
var PERF = /[?&]perf=1/.test(location.search);
var perfS = {apply: [], patched: 0, full: 0, bytes: 0, lean: 0, frames: [], why: {}};
function perfNow(){ return window.performance && performance.now ? performance.now() : Date.now(); }
function perfApply(ms, patched, len){
  perfS.apply.push(ms); if (perfS.apply.length > 30) perfS.apply.shift();
  if (patched) perfS.patched++;
  else { perfS.full++; perfS.why[patchWhy || '?'] = (perfS.why[patchWhy || '?'] || 0) + 1; }
  perfDraw();
}
function perfWhyText(){
  var out = [];
  for (var k in perfS.why) if (perfS.why.hasOwnProperty(k)) out.push(k + ' ' + perfS.why[k]);
  return out.length ? ' (' + out.join(', ') + ')' : '';
}
function perfNet(len, lean){ perfS.bytes = len; if (lean) perfS.lean++; }
function perfStat(a){
  if (!a.length) return '—';
  var b = a.slice().sort(function(x, y){ return x - y; });
  return Math.round(b[Math.floor(b.length / 2)]) + ' / ' + Math.round(b[b.length - 1]) + ' мс';
}
function perfFeatures(){
  var out = [], d = document.createElement('div');
  out.push('PointerEvent ' + (window.PointerEvent ? 'да' : 'НЕТ'));
  var cssOk = window.CSS && CSS.supports;
  out.push('CSS-переменные ' + (cssOk && CSS.supports('--a', '0') ? 'да' : 'НЕТ'));
  out.push('blur фона ' + (cssOk && (CSS.supports('backdrop-filter', 'blur(2px)') ||
                                      CSS.supports('-webkit-backdrop-filter', 'blur(2px)')) ? 'да' : 'нет'));
  // gap у флекса: два квадрата по 1px с промежутком 1px - высота 3
  d.style.cssText = 'display:flex;flex-direction:column;gap:1px;position:absolute;visibility:hidden';
  d.appendChild(document.createElement('div')); d.appendChild(document.createElement('div'));
  d.firstChild.style.height = d.lastChild.style.height = '1px';
  document.body.appendChild(d);
  out.push('gap у флекса ' + (d.scrollHeight === 3 ? 'да' : 'НЕТ'));
  document.body.removeChild(d);
  out.push('SMIL ' + (document.createElementNS && typeof document.createElementNS(
    'http://www.w3.org/2000/svg', 'animate').beginElement === 'function' ? 'да' : 'НЕТ'));
  return out.join(' · ');
}
var perfBox = null, perfFeat = '';
function perfDraw(){
  if (!PERF) return;
  if (!perfBox) {
    perfBox = document.createElement('div'); perfBox.id = 'perf';
    document.body.appendChild(perfBox);
    perfFeat = perfFeatures();
  }
  var ua = navigator.userAgent.replace(/^Mozilla\/5\.0 /, '');
  perfBox.textContent = ua + '\n' + perfFeat
    + '\nверсия панели ' + APP + (LITE ? ' · облегчённо' + (LITE_MODE === 'auto' ? ' (авто)' : '') : '')
    + ' · тема ' + (rootTheme() || '?')
    + '\nобновление: разбор+вставка ' + perfStat(perfS.apply) + ' (медиана / худшее)'
    + '\n  точечно ' + perfS.patched + ' (со сдвигом ' + perfMerged + (TEXT_PARTS ? (HTML_PARTS ? '' : ', части XML') : ', через разбор') + '), целиком ' + perfS.full + perfWhyText()
    + ', без головы ' + perfS.lean
    + ', последнее ' + Math.round(perfS.bytes / 1024) + ' КБ'
    + '\nпрокрутка: кадр ' + perfStat(perfS.frames) + (perfS.frames.length ? '' : ' — полистайте панель');
}
/* Кадры прокрутки: пока страницу листают, меряем промежутки между кадрами. */
var perfLast = 0, perfUntil = 0, perfRaf = false;
function perfFrame(t){
  if (perfLast) { perfS.frames.push(t - perfLast); if (perfS.frames.length > 120) perfS.frames.shift(); }
  perfLast = t;
  if (perfNow() < perfUntil) requestAnimationFrame(perfFrame);
  else { perfRaf = false; perfLast = 0; perfDraw(); }
}
if (PERF) {
  window.addEventListener('scroll', function(){
    perfUntil = perfNow() + 400;
    if (!perfRaf && window.requestAnimationFrame) { perfRaf = true; requestAnimationFrame(perfFrame); }
  }, {passive: true});
  setTimeout(perfDraw, 500);
}

/* INTERACTIVE - задаётся в wrapper.html (подставляет сервер) */
window.addEventListener('resize', function(){ if (DOCK) dockBar(); if (!DEMO) navScale(); });
var host = document.getElementById('host'), err = document.getElementById('err');
var ov = document.getElementById('ov');
var paused = false, press = null;

/* ---------- предохранители касаний ----------
   Плитки ловят нажатие парой pointerdown/pointerup, а не click, - так
   работает долгое нажатие. Обратная сторона: браузер не отсеивает за нас
   касания, которые нажатиями не были.

   Касание, остановившее инерционную прокрутку. Палец не двигался, порог
   сдвига не превышен, отмены не было - для плитки это обычное короткое
   нажатие, и выключатель щёлкал. Поэтому помним, когда страница или
   оверлей последний раз прокручивались: прокрутка перед касанием или во
   время него означает, что человек листал, а не нажимал.

   Касание сразу после закрытия оверлея. Окно закрылось, панель тут же
   снова принимает касания - и второе касание, или запоздалые события
   браузера от первого, уходят в плитку под пальцем. Поэтому пока окно
   открыто и ещё немного после, панель касаний не принимает вовсе. */
var SCROLL_GAP = 250, SHIELD_MS = 450;
var lastScroll = 0, lastDown = 0, shieldUntil = 0, fingerDown = false;
// Прокрутка не всплывает, поэтому слушаем на перехвате: так ловится и
// страница, и оверлей со своей полосой прокрутки.
document.addEventListener('scroll', function(){ lastScroll = Date.now(); },
                          {capture:true, passive:true});

/* Старые браузеры без PointerEvent (Android WebView до Chrome 55). Там
   плитки слушают и касания, и мышь, а браузер после каждого касания
   досылает «мышиные» mousedown/mouseup в ту же точку. Нажатие
   отрабатывало дважды: выключатель щёлкал туда и обратно, окно
   протечки или списка открывалось и тут же закрывалось - запоздалый
   mousedown приходился уже на фон окна. Мышиные события сразу после
   касания глушим здесь, на перехвате, раньше всех остальных
   обработчиков. click не трогаем: на нём живут кнопки окон. */
if (!window.PointerEvent) {
  var lastTouchAt = 0;
  ['touchstart', 'touchend', 'touchcancel'].forEach(function(n){
    document.addEventListener(n, function(){ lastTouchAt = Date.now(); }, true);
  });
  ['mousedown', 'mouseup', 'mousemove'].forEach(function(n){
    document.addEventListener(n, function(e){
      if (Date.now() - lastTouchAt < 800) {
        if (e.stopImmediatePropagation) e.stopImmediatePropagation();
        e.stopPropagation();
      }
    }, true);
  });
}

/* Можно ли считать касание, начатое в t0, нажатием. */
function tapOk(t0){
  t0 = t0 || lastDown || Date.now();
  if (Date.now() < shieldUntil || t0 < shieldUntil) return false;
  return lastScroll < t0 - SCROLL_GAP;
}

/* Палец на экране. Пока он там, разметку панели не подменяем: если
   замена приходится между касанием и отпусканием, элемент, на котором
   начали, исчезает, и нажатие пропадает целиком. Отсюда были «иногда не
   попадаю по заголовку». Отложенная картинка ставится сразу после
   отпускания - с небольшой задержкой, чтобы успел пройти click. */
function fingerOn(){ fingerDown = true; lastDown = Date.now(); }
function fingerOff(){
  fingerDown = false;
  if (pendingSvg) setTimeout(flushPending, 120);
}
if (window.PointerEvent) {
  document.addEventListener('pointerdown', fingerOn, true);
  document.addEventListener('pointerup', fingerOff, true);
  document.addEventListener('pointercancel', fingerOff, true);
} else {
  ['touchstart', 'mousedown'].forEach(function(e){
    document.addEventListener(e, fingerOn, true);
  });
  ['touchend', 'touchcancel', 'mouseup'].forEach(function(e){
    document.addEventListener(e, fingerOff, true);
  });
}
// Палец «на экране» дольше десяти секунд - значит, отпускание потерялось
// (ушли из окна, переключили приложение). Обновления не должны встать
// из-за этого навсегда.
function fingerHeld(){ return fingerDown && Date.now() - lastDown < 10000; }

/* Координаты события.
   ВАЖНО про touchend: у него список touches ПУСТОЙ - палец уже оторван.
   Пустой TouchList при этом истинный, поэтому наивная проверка
   "ev.touches ? ev.touches[0] : ev" давала undefined и падала на .clientX.
   Из-за этого ползунки двигались, а значение не отправлялось: исключение
   происходило ровно в момент отпускания, до вызова pub(). */
function evPoint(ev){
  if(ev.touches && ev.touches.length) return ev.touches[0];
  if(ev.changedTouches && ev.changedTouches.length) return ev.changedTouches[0];
  return ev;
}

function flash(t){ err.textContent=t; err.style.display='block';
  setTimeout(function(){ err.style.display='none'; }, 2600); }

function pub(topic, value){
  return fetch('api/publish', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({topic:topic, value:String(value)})
  }).then(function(r){
    if(!r.ok) return r.json().then(function(j){ throw new Error(j.error||r.status); });
  });
}
