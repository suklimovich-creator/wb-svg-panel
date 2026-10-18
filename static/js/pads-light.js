/* wb-svg-panel: график на весь экран, раскрытая плитка, пульты ленты (поле и шар).
   Часть скриптов страницы панели (templates/wrapper.html), с 1.45 -
   отдельными файлами. Порядок подключения важен: это обычные скрипты
   с общими глобальными именами, без модулей и сборки (старые планшеты).
   Код, который выполняется сразу при загрузке, может звать только
   функции из этого и предыдущих файлов - проверяет tests/js_check.py. */
/* ---------- график на весь экран ---------- */
/* График по нажатию на чип строки состояния.
   Плитки под ним нет, поэтому картинку собирает отдельный маршрут
   /chart.svg по имени канала. Диапазон переключается кнопками: смотреть
   движение за трое суток и за полчаса нужно по-разному. */
function chartOv(node){
  var channel = node.getAttribute('data-chart');
  var title = node.getAttribute('data-chart-title') || '';
  var unit = node.getAttribute('data-unit') || '';
  var range = '12h';

  var card = el('div','card'); card.textContent = '…';
  var row = el('div','crow');
  var head = padHead(title, infoBtn(function(){ load(); }));

  function url(){
    var w = Math.min(Math.round(window.innerWidth * 0.90), 1400);
    var h = Math.min(Math.round(window.innerHeight * 0.55), 800);
    return 'chart.svg?channel=' + encodeURIComponent(channel) +
           '&range=' + range + '&w=' + w + '&h=' + h +
           '&unit=' + encodeURIComponent(unit) +
           '&title=' + encodeURIComponent(title) + themeQ() + (INFO_OPEN ? '&info=1' : '') +
           '&t=' + Date.now();
  }
  function load(){
    fetch(url(), {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        card.innerHTML = svg;
      })
      .catch(function(e){ card.textContent = 'Не удалось: ' + e.message; });
  }
  var buttons = {};
  ['30m','6h','12h','3d'].forEach(function(r){
    var b = el('span','cbtn', r);
    b.onclick = function(){
      range = r;
      for(var k in buttons){ buttons[k].className = 'cbtn'; }
      b.className = 'cbtn on';
      load();
    };
    if(r === range) b.className = 'cbtn on';
    buttons[r] = b;
    row.appendChild(b);
  });

  openOv([head, card, row]);
  if(!ov.offsetHeight){ closeOv(); window.open(url(), '_blank'); return; }
  load();
}

function expand(node){
  // Фанкойл (с 1.42): короткое нажатие открывает тот же пульт, что и долгое.
  if(node.getAttribute('data-kind') === 'fancoil' && node.getAttribute('data-look') !== 'classic')
    return padFancoil(node);
  var i = node.getAttribute('data-i');
  // 40 px - поля самого оверлея (18 с каждой стороны) плюс запас на
  // полосу прокрутки. Раньше просили 90% ширины и получали заметные
  // пустые поля по бокам вдобавок к рамке карточки.
  var w = Math.min(Math.max(window.innerWidth - 40, 280), 1600);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 900);
  // Шапка - как у пультов (с 1.35): «назад» слева, у графика справа «i»
  // с зонами и пояснениями. Подсказка «нажмите вне карточки» больше не нужна.
  h = Math.min(Math.round(window.innerHeight * 0.62) - 20, 900);
  var chart = node.getAttribute('data-kind') === 'chart';
  function url(){
    return 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h + themeQ() +
           (chart && INFO_OPEN ? '&info=1' : '');
  }
  var card = el('div','card wide');
  card.textContent = '…';
  var head = padHead(node.getAttribute('data-title') || '', chart ? infoBtn(function(){ load(); }) : null);
  // Плитка, у которой короткое нажатие раскрывает картинку, а пульт висит
  // на долгом: органы управления идут прямо сюда, под карточку. Иначе
  // раскрытый фанкойл - красивая картинка, которой нельзя управлять.
  var parts = [head, card];
  if(node.getAttribute('data-pad') === 'ac') parts = parts.concat(acControls(node));
  openOv(parts);
  // если оверлей по какой-то причине не показался, откроем картинку отдельно
  if(!ov.offsetHeight){ closeOv(); window.open(url(), '_blank'); return; }
  function load(){
    fetch(url(), {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        card.innerHTML = svg;
      })
      .catch(function(e){
        card.textContent = 'Не удалось: ' + e.message;
        flash('Полный экран: ' + e.message);
      });
  }
  load();
}

/* ---------- пульт ленты: X - температура, Y - яркость ---------- */
/* ---------- пульт ленты: шар (с 1.18) ----------
   Шар светится цветом ленты и покрыт сотами. Провести вверх-вниз - шар
   поворачивается, яркость меняется; влево-вправо - теплота. Толчок -
   катится по инерции и замедляется, на краю шкалы упирается. Команда
   уходит, когда шар остановился. Ниже - шаг числом и питание, как было. */
var SPH = null;
function sphereGeo(F){
  if (SPH && SPH.F === F) return SPH;
  var p = (1 + Math.sqrt(5)) / 2;
  var I = [[-1,p,0],[1,p,0],[-1,-p,0],[1,-p,0],[0,-1,p],[0,1,p],[0,-1,-p],[0,1,-p],[p,0,-1],[p,0,1],[-p,0,-1],[-p,0,1]];
  var T = [[0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],[1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],
           [3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],[4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]];
  function nrm(v){ var n = Math.hypot(v[0], v[1], v[2]); return [v[0]/n, v[1]/n, v[2]/n]; }
  var V = [], key = {}, tris = [];
  function vid(v){ v = nrm(v); var k = v.map(function(x){ return x.toFixed(5); }).join(','); if (key[k] === undefined){ key[k] = V.length; V.push(v); } return key[k]; }
  T.forEach(function(t){
    var A = I[t[0]], B = I[t[1]], C = I[t[2]], g = [], i, j;
    for (i = 0; i <= F; i++){ g.push([]); for (j = 0; j <= F - i; j++)
      g[i].push(vid([0,1,2].map(function(k){ return A[k] + (B[k]-A[k])*i/F + (C[k]-A[k])*j/F; }))); }
    for (i = 0; i < F; i++) for (j = 0; j < F - i; j++){
      tris.push([g[i][j], g[i+1][j], g[i][j+1]]);
      if (j < F - i - 1) tris.push([g[i+1][j], g[i+1][j+1], g[i][j+1]]);
    }
  });
  var C = tris.map(function(t){ return nrm([0,1,2].map(function(k){ return V[t[0]][k] + V[t[1]][k] + V[t[2]][k]; })); });
  var adj = V.map(function(){ return []; });
  tris.forEach(function(t, k){ t.forEach(function(v){ adj[v].push(k); }); });
  var cells = V.map(function(d, vi){
    var u = Math.abs(d[0]) < .9 ? [1,0,0] : [0,1,0];
    var e1 = nrm([d[1]*u[2]-d[2]*u[1], d[2]*u[0]-d[0]*u[2], d[0]*u[1]-d[1]*u[0]]);
    var e2 = [d[1]*e1[2]-d[2]*e1[1], d[2]*e1[0]-d[0]*e1[2], d[0]*e1[1]-d[1]*e1[0]];
    function a(k){ var c = C[k]; return Math.atan2(c[0]*e2[0]+c[1]*e2[1]+c[2]*e2[2], c[0]*e1[0]+c[1]*e1[1]+c[2]*e1[2]); }
    return { c: vi, ring: adj[vi].slice().sort(function(x, y){ return a(x) - a(y); }) };
  });
  SPH = { F: F, V: V, C: C, cells: cells };
  return SPH;
}

function padSphere(node){
  var PITCH = 2.6, YAW = 3.2;     // те же, что в skeuo.py
  var x = parseFloat(node.getAttribute('data-dot-x') || '0.5');        // 0 холодный - 1 тёплый
  var lvl = 1 - parseFloat(node.getAttribute('data-dot-y') || '0.5');  // яркость
  var hasTemp = !!node.getAttribute('data-temp');
  var on = node.getAttribute('data-state') === '1';
  var rot = { x: (x - .5) * YAW, y: -(lvl - .5) * PITCH };
  function hex(c){ c = (c || '#888888').replace('#', ''); return [0, 2, 4].map(function(i){ return parseInt(c.substr(i, 2), 16); }); }
  var COLD = hex(node.getAttribute('data-cold') || '#56AAFF'), WARM = hex(node.getAttribute('data-warm') || '#FF9337');
  var MID = [236, 230, 220], PAL = ovPal(), FM = FORM();
  // Тень шара и «стекло» выключенного: в светлой теме - светло-серые,
  // тёмный шар на светлом поле выглядел дырой.
  var DARK = PAL.lt ? [92, 94, 102] : [20, 21, 24], BASE = PAL.lt ? [206, 207, 212] : [46, 47, 52];
  function mix(a, b, f){ return a.map(function(v, i){ return v + (b[i] - v) * f; }); }
  function css(a, al){ return 'rgba(' + a.map(Math.round).join(',') + ',' + (al === undefined ? 1 : al) + ')'; }
  function hue(t){ return t < .5 ? mix(COLD, MID, t * 2) : mix(MID, WARM, (t - .5) * 2); }

  /* С 1.23: шапка как у кондиционера (назад, название, питание), кнопки
     − / + вокруг шара вместо подписей: вверх-вниз - яркость, влево-вправо
     - теплота, как и сам жест. Проценты - строкой под шаром. */
  var side = Math.round(Math.min(window.innerWidth * 0.94, window.innerHeight * 0.56, 460));
  var box = el('div', 'stagebox');
  box.style.width = side + 'px'; box.style.height = side + 'px';
  var stage = el('div'); stage.id = 'sphstage';
  stage.style.width = side + 'px'; stage.style.height = side + 'px';
  var cv = document.createElement('canvas'); stage.appendChild(cv);
  box.appendChild(stage);
  var hint = el('div', 'hint', 'Крутите шар: вверх-вниз — яркость, влево-вправо — теплота');
  var power = powerRow(node, true);
  var head = padHead(node.getAttribute('data-title') || 'Подсветка', power.el);
  var vals = el('div', 'vals'), vB = el('div'), vT = el('div');
  vals.appendChild(vB); if (hasTemp) vals.appendChild(vT);
  function paintVals(){
    vB.innerHTML = '<small>яркость</small>' + Math.round(lvl * 100) + ' %';
    if (hasTemp) vT.innerHTML = '<small>теплота</small>' + Math.round(x * 100) + ' %';
    head.sub.textContent = power.on ? 'Включено' : 'Выключено';
  }
  power.onPaint = paintVals;
  // Кнопки на окружности за шкалами: те же r и R, что в draw().
  var GR = side * .27, GRR = GR + Math.max(11, GR * .12), GB = GRR + 31;
  function ringBtn(cls, deg, text){
    var b = hbtn('at' + (cls ? ' ' + cls : ''), '', null);
    b.textContent = text;
    var a = deg * Math.PI / 180;
    b.style.left = (side / 2 + GB * Math.cos(a)).toFixed(1) + 'px';
    b.style.top = (side / 2 + GB * Math.sin(a)).toFixed(1) + 'px';
    box.appendChild(b); return b;
  }
  var bPlus = ringBtn('', 270, '+'), bMinus = ringBtn('', 90, '−');
  stepKeys(bMinus, bPlus, {
    read:  function(){ return Math.round(lvl * 100); },
    write: function(v){ touched = true; rot.y -= (v / 100 - lvl) * PITCH; lvl = v / 100; draw(); },
    send:  function(){ return send(); },
    paint: paintVals
  });
  if (hasTemp){
    var tMinus = ringBtn('cold', 150, '−'), tPlus = ringBtn('warm', 30, '+');
    stepKeys(tMinus, tPlus, {
      read:  function(){ return Math.round(x * 100); },
      write: function(v){ touched = true; rot.x += (v / 100 - x) * YAW; x = v / 100; draw(); },
      send:  function(){ return send(); },
      paint: paintVals
    });
  }
  paintVals();
  openOv([head, box, vals, hint]);

  function draw(){
    var dpr = window.devicePixelRatio || 1, W = side, H = side;
    if (cv.width !== Math.round(W * dpr)){ cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
    var g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
    var cx = W / 2, cy = H / 2, r = W * .27, lit = power.on || touched;
    /* Выключенный шар не серый: в нём видно, каким цветом лента
       загорится, - бледно, без свечения. */
    var lv = lit ? lvl : 0, h = hue(x);
    var c = lit ? mix(PAL.lt ? BASE : DARK, h, .18 + .82 * lvl) : mix(BASE, h, PAL.lt ? .6 : .4);
    if (lit){
      var hr = Math.min(r * (1.45 + .5 * lv), W / 2 * .98);
      var halo = g.createRadialGradient(cx, cy, r * .7, cx, cy, hr);
      halo.addColorStop(0, css(h, .5 * lv)); halo.addColorStop(1, css(h, 0));
      g.fillStyle = halo; g.beginPath(); g.arc(cx, cy, hr, 0, 7); g.fill();
    }
    /* Тень - мягкое пятно под шаром, а не сдвинутый вниз диск: тот
       выглядывал из-под шара резким серпом. */
    var shR = r * 1.16;
    if (FM.depth === 'neumo'){
      // неоморфизм: светлая тень сверху-слева, тёмная снизу-справа (1.35)
      var o = r * .09;
      [[-o, FM.neu, .9], [o, FM.shd, .75]].forEach(function(q){
        var gr = g.createRadialGradient(cx + q[0], cy + q[0], r * .82, cx + q[0], cy + q[0], shR);
        var c0 = hexRgb(q[1]);
        gr.addColorStop(0, 'rgba(' + c0.join(',') + ',' + q[2] + ')'); gr.addColorStop(1, 'rgba(' + c0.join(',') + ',0)');
        g.fillStyle = gr; g.beginPath(); g.arc(cx + q[0], cy + q[0], shR, 0, 7); g.fill();
      });
    } else if (FM.depth !== 'flat'){
      var sh = g.createRadialGradient(cx, cy + r * .07, r * .82, cx, cy + r * .07, shR);
      sh.addColorStop(0, PAL.lt ? 'rgba(0,0,0,.16)' : 'rgba(0,0,0,.45)'); sh.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = sh; g.beginPath(); g.arc(cx, cy + r * .07, shR, 0, 7); g.fill();
    }
    var body = g.createRadialGradient(cx - r * .32, cy - r * .36, r * .08, cx, cy, r * 1.04);
    body.addColorStop(0, css(mix(c, [255,255,255], lit ? .25 * lv : (PAL.lt ? .45 : .18))));
    body.addColorStop(.6, css(mix(c, DARK, .25)));
    body.addColorStop(1, css(mix(c, DARK, PAL.lt ? .6 : .78)));
    g.fillStyle = body; g.beginPath(); g.arc(cx, cy, r, 0, 7); g.fill();
    // соты
    var G = sphereGeo(9), cyw = Math.cos(rot.x), syw = Math.sin(rot.x), cp = Math.cos(rot.y), sp = Math.sin(rot.y);
    function P(v){ var X = v[0]*cyw + v[2]*syw, Z = -v[0]*syw + v[2]*cyw; return [X, v[1]*cp - Z*sp, v[1]*sp + Z*cp]; }
    var pc = G.C.map(P), pv = G.V.map(P), path = new Path2D();
    G.cells.forEach(function(cell){
      if (pv[cell.c][2] < .03) return;
      for (var k = 0; k < cell.ring.length; k++) if (pc[cell.ring[k]][2] < 0) return;
      cell.ring.forEach(function(i, k){ var q = pc[i]; if (k) path.lineTo(cx + q[0]*r, cy - q[1]*r); else path.moveTo(cx + q[0]*r, cy - q[1]*r); });
      path.closePath();
    });
    g.save(); g.beginPath(); g.arc(cx, cy, r, 0, 7); g.clip();
    g.lineJoin = 'round'; g.lineWidth = Math.max(.6, r / 160);
    g.strokeStyle = css(mix(c, DARK, .5), lit ? .45 : .3); g.stroke(path);
    var rim = g.createRadialGradient(cx, cy, r * .55, cx, cy, r);
    rim.addColorStop(0, css(DARK, 0)); rim.addColorStop(1, css(DARK, PAL.lt ? .35 : .55));
    g.fillStyle = rim; g.fillRect(cx - r, cy - r, 2 * r, 2 * r); g.restore();
    var spc = g.createRadialGradient(cx - r * .36, cy - r * .42, 0, cx - r * .36, cy - r * .42, r * .42);
    spc.addColorStop(0, 'rgba(255,255,255,' + (lit ? .55 : (PAL.lt ? .6 : .25)) + ')'); spc.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = spc; g.beginPath(); g.arc(cx, cy, r, 0, 7); g.fill();
    g.strokeStyle = PAL.lt ? 'rgba(0,0,0,.12)' : 'rgba(255,255,255,.14)'; g.lineWidth = 1; g.beginPath(); g.arc(cx, cy, r, 0, 7); g.stroke();
    // шкалы: слева яркость, снизу теплота
    var R = r + Math.max(11, r * .12), sw = Math.max(3, r * .035), B0 = 165, B1 = 255, T0 = 140, T1 = 40;
    function rad(d){ return d * Math.PI / 180; }
    g.lineCap = 'round'; g.lineWidth = sw;
    g.strokeStyle = PAL.track; g.beginPath(); g.arc(cx, cy, R, rad(B0), rad(B1)); g.stroke();
    g.strokeStyle = lit ? PAL.acc : PAL.offKnob; g.beginPath(); g.arc(cx, cy, R, rad(B0), rad(B0 + (B1 - B0) * lvl)); g.stroke();
    for (var i = 0; i < 22; i++){
      var f = i / 21, a = rad(T0 - (T0 - T1) * f);
      // точки теплоты цветные всегда: по ним видно, какой будет свет
      g.fillStyle = css(PAL.lt ? mix(hue(f), [90, 92, 100], .18) : hue(f), lit ? 1 : .8);
      g.beginPath(); g.arc(cx + R * Math.cos(a), cy + R * Math.sin(a), sw * .7, 0, 7); g.fill();
    }
    [T0 - (T0 - T1) * x, B0 + (B1 - B0) * lvl].forEach(function(d){
      var a = rad(d);
      g.fillStyle = lit ? PAL.knob : PAL.offKnob; g.strokeStyle = PAL.knobEdge; g.lineWidth = 2;
      g.beginPath(); g.arc(cx + R * Math.cos(a), cy + R * Math.sin(a), sw * 1.7, 0, 7); g.fill(); g.stroke();
    });
  }

  var timer = null;
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max') || '100');
    var jobs = [pub(node.getAttribute('data-bright'), Math.round(lvl * bmax))];
    var tTopic = node.getAttribute('data-temp');
    if (tTopic){
      var unit = node.getAttribute('data-temp-unit');
      var tmax = parseFloat(node.getAttribute('data-temp-max') || '100');
      var lo = parseFloat(node.getAttribute('data-temp-lo') || '2700');
      var hi = parseFloat(node.getAttribute('data-temp-hi') || '6500');
      jobs.push(pub(tTopic, unit === 'percent' ? Math.round((1 - x) * tmax) : Math.round(hi - x * (hi - lo))));
    }
    return power.ensureOn()
      .then(function(){ return Promise.all(jobs); })
      .then(function(){ draw(); })
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  function later(){ clearTimeout(timer); timer = setTimeout(send, 250); }

  function clamp(v){ return Math.max(0, Math.min(1, v)); }
  function move(ax, ay){
    var nx = clamp(x + (hasTemp ? ax / YAW : 0)), nl = clamp(lvl - ay / PITCH);
    rot.x += (nx - x) * YAW; rot.y -= (nl - lvl) * PITCH;
    x = nx; lvl = nl;
    draw(); paintVals();
  }
  var last = null, touched = false, moved = false;
  // крутят выключенную - на шаре сразу видно, каким будет свет
  function begin(ev){
    touched = true; moved = false;
    clearTimeout(timer);
    var p = evPoint(ev); last = { x: p.clientX, y: p.clientY };
  }
  function at(ev){
    var p = evPoint(ev);
    if (!last || p.clientX === undefined) return;
    var dx = p.clientX - last.x, dy = p.clientY - last.y, r = side * .27;
    last = { x: p.clientX, y: p.clientY };
    if (!dx && !dy) return;
    moved = true;
    move(dx / r, dy / r);
    live.tick();
  }
  // Без инерции (1.50): шар останавливается вместе с пальцем. Раньше он
  // докручивался после отпускания, и свет уходил дальше, чем ставили.
  // С «Ручки шлют: сразу» (1.51) свет идёт за шаром и по дороге.
  var live = liveSender(send);
  function done(){ if (moved) { clearTimeout(timer); live.end(); } }
  ovCleanup = function(){ clearTimeout(timer); };
  power.el.addEventListener('click', function(){ touched = false; setTimeout(draw, 500); });
  draw();
  drag(stage, at, done, begin);
}

function padXY(node){
  if (node.getAttribute('data-look') !== 'classic') return padSphere(node);
  var pad = el('div'); pad.id='pad';
  var side = Math.round(Math.min(window.innerWidth*0.78, window.innerHeight*0.6));
  pad.style.width = side + 'px'; pad.style.height = side + 'px';
  var grad = el('div','grad'), dim = el('div','dim'), dot = el('div','dot');
  grad.style.background = 'linear-gradient(to right,' +
      node.getAttribute('data-cold') + ',' + node.getAttribute('data-warm') + ')';
  pad.appendChild(grad); pad.appendChild(dim); pad.appendChild(dot);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Подсветка');
  var hint = el('div','hint','Слева холодный, справа тёплый · вверх ярче'
                             + ' · стрелками по проценту');
  var power = powerRow(node);
  var x = parseFloat(node.getAttribute('data-dot-x')||'0.5');
  var y = parseFloat(node.getAttribute('data-dot-y')||'0.5');
  function place(){ dot.style.left=(x*100)+'%'; dot.style.top=(y*100)+'%';
    dot.style.background = 'transparent'; }
  place();

  var rows = [];
  var bright = stepRow('Яркость', {
    unit: ' %',
    read:  function(){ return Math.round((1-y)*100); },
    write: function(v){ y = 1 - v/100; place(); },
    send:  function(){ return send(); }
  });
  rows.push(bright);
  // Ось цветовой температуры шагается в процентах, как и яркость: у ленты
  // это доля от холодного к тёплому, а не кельвины прибора. Считать шаг в
  // кельвинах пришлось бы по-разному для percent и kelvin, и на плитке всё
  // равно показана доля.
  var warm = null;
  if(node.getAttribute('data-temp')){
    warm = stepRow('Тёплый', {
      unit: ' %',
      read:  function(){ return Math.round(x*100); },
      write: function(v){ x = v/100; place(); },
      send:  function(){ return send(); }
    });
    rows.push(warm);
  }

  openOv([title, pad].concat(rows, [power.el, hint]));

  function at(ev){
    var r = pad.getBoundingClientRect();
    var p = evPoint(ev);
    x = Math.max(0, Math.min(1, (p.clientX-r.left)/r.width));
    y = Math.max(0, Math.min(1, (p.clientY-r.top)/r.height));
    place();
    bright.repaint();
    if(warm) warm.repaint();
  }
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    var bright = Math.round((1-y)*bmax);
    var jobs = [pub(node.getAttribute('data-bright'), bright)];
    var tTopic = node.getAttribute('data-temp');
    if(tTopic){
      var unit = node.getAttribute('data-temp-unit');
      var tmax = parseFloat(node.getAttribute('data-temp-max')||'100');
      var lo = parseFloat(node.getAttribute('data-temp-lo')||'2700');
      var hi = parseFloat(node.getAttribute('data-temp-hi')||'6500');
      // слева холодный: x=0 -> hi кельвинов
      var value = (unit === 'percent') ? Math.round((1-x)*tmax)
                                       : Math.round(hi - x*(hi-lo));
      jobs.push(pub(tTopic, value));
    }
    // если лента выключена, сперва включаем - иначе крутить бесполезно
    return power.ensureOn()
      .then(function(){ return Promise.all(jobs); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  drag(pad, at, send);
}
