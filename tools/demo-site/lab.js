/* Конструктор темы на демо-странице (tools/demo-site): тень по точкам,
   рамка, свет горящей лампы, углы. Каждое изменение - черновик темы на
   сервере панели (api/theme/draft, тот же, что у редактора /themes), и
   панель тут же перерисовывается в нём. Готовую тему можно скопировать
   файлом: положить в themes/ рядом с config.yaml на контроллере. */
(function(){
  var KEY = 'wb-demo-lab';
  var DEF = {
    base: 'cloud',
    stops: [[0, 65, '#2E3F81'], [1, 9, '#4F68C2'], [14, 9, '#7E92D8']],
    curve: 'smooth', dy: 6.5, soft: 3.5, power: 60, shade: -30,
    edgeW: 0.8, edgeC: '#34488C', edgeA: 34,
    glow: 'flare', glowPower: 140, light1: '#FFC93C', light2: '#FF9E2C',
    card1: '#FFA24A', card2: '#F25C8A', card3: '#8B5CF6',
    radius: 17
  };
  var S = load(), open = false, timer = null, seq = 0, lastYaml = '';
  var mine = false;           // тему меняли в конструкторе - она и показывается
  try { mine = !!localStorage.getItem(KEY); } catch(e) {}

  function load(){
    try {
      var s = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (s && s.stops && s.stops.length >= 2) {
        for (var k in DEF) if (!(k in s)) s[k] = DEF[k];
        return s;
      }
    } catch(e) {}
    return JSON.parse(JSON.stringify(DEF));
  }
  function save(){ mine = true; try { localStorage.setItem(KEY, JSON.stringify(S)); } catch(e) {} }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function rgb(h){
    var m = /^#?([0-9a-f]{6})$/i.exec(h || '');
    if (!m) return [52, 72, 140];
    var n = parseInt(m[1], 16);
    return [n >> 16 & 255, n >> 8 & 255, n & 255];
  }

  /* ---------- черновик темы ---------- */
  function theme(){
    var e = rgb(S.edgeC);
    var t = {title: 'Моя тема', name: 'moya-tema', extends: S.base, radius: +S.radius,
      shadow: {dy: +S.dy, soft: +S.soft, power: S.power / 100, shade: S.shade / 100, curve: S.curve,
               stops: S.stops.map(function(p){ return [+p[0], p[1] / 100, p[2]]; })},
      glow: S.glow === 'none' ? false : {style: S.glow, power: S.glowPower / 100},
      vars: {'edge': 'rgba(' + e.join(',') + ',' + (S.edgeA / 100).toFixed(2) + ')',
             'edge-w': String(S.edgeW),
             'glow-light': S.light1.toUpperCase(), 'glow-light2': S.light2.toUpperCase(),
             'glow-a': S.card1.toUpperCase(), 'glow-b': S.card2.toUpperCase(), 'glow-c': S.card3.toUpperCase()}};
    return {theme: t};
  }
  function apply(){
    var my = ++seq;
    fetch('api/theme/draft', {method: 'POST', headers: {'Content-Type': 'application/json'},
                              body: JSON.stringify(theme())})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (my !== seq || !j.draft) return;
        lastYaml = j.yaml || '';
        var i = j.info || {};
        PAGE_BG[j.draft] = i.bg; DARK_THEME[j.draft] = !!i.dark;
        PAGE_WALL[j.draft] = i.wall || ''; THEME_INFO[j.draft] = i;
        THEME = j.draft;            // не saveTheme: черновик живёт до перезагрузки
        refresh(true);
        restartPoll();
        var pre = document.getElementById('lab-yaml');
        if (pre && !pre.hidden) pre.textContent = lastYaml;
      });
  }
  function changed(redraw){
    save();
    if (redraw) draw();
    else drawCurve();
    clearTimeout(timer);
    timer = setTimeout(apply, 180);
  }

  /* ---------- шторка ---------- */
  function slider(id, label, min, max, step, val, unit){
    return '<label class="lab-row"><span>' + label + '</span>' +
      '<input type="range" id="' + id + '" min="' + min + '" max="' + max + '" step="' + step +
      '" value="' + val + '"><b id="' + id + '-v">' + val + (unit || '') + '</b></label>';
  }
  function seg(id, items, val){
    return '<div class="lab-seg" id="' + id + '">' + items.map(function(p){
      return '<i data-v="' + p[0] + '"' + (p[0] === val ? ' class="on"' : '') + '>' + p[1] + '</i>';
    }).join('') + '</div>';
  }
  function draw(){
    var box = document.getElementById('lab');
    var stops = S.stops.map(function(p, i){
      return '<div class="lab-stop" data-i="' + i + '">' +
        '<input type="color" value="' + p[2] + '" data-k="c">' +
        '<label><span>от края</span><input type="range" min="0" max="40" step="1" value="' + p[0] + '" data-k="d"><b>' + p[0] + '</b></label>' +
        '<label><span>сила</span><input type="range" min="0" max="100" step="1" value="' + p[1] + '" data-k="o"><b>' + p[1] + '%</b></label>' +
        (S.stops.length > 2 ? '<button data-k="x" aria-label="убрать точку">×</button>' : '<span class="lab-gap"></span>') +
        '</div>';
    }).join('');
    box.querySelector('.lab-body').innerHTML =
      '<h4>Тень</h4>' +
      '<canvas id="lab-curve" width="640" height="150"></canvas>' +
      '<div class="lab-note">Сила тени на каждом расстоянии от края плитки. Точек - до шести.</div>' +
      stops +
      (S.stops.length < 6 ? '<button class="lab-btn" id="lab-add">+ точка</button>' : '') +
      seg('lab-curve-k', [['linear', 'переходы прямые'], ['smooth', 'плавные']], S.curve) +
      slider('lab-dy', 'Сдвиг вниз', 0, 12, 0.5, S.dy) +
      slider('lab-soft', 'Мягкость', 0, 6, 0.5, S.soft) +
      slider('lab-power', 'Сила всей тени', 0, 200, 5, S.power, '%') +
      slider('lab-shade', 'Цвет темнее / светлее', -60, 60, 5, S.shade) +
      '<h4>Рамка плиток</h4>' +
      slider('lab-ew', 'Толщина', 0, 3, 0.1, S.edgeW) +
      '<div class="lab-cols"><span>Цвет</span><input type="color" id="lab-ec" value="' + S.edgeC + '"></div>' +
      slider('lab-ea', 'Видимость', 0, 100, 2, S.edgeA, '%') +
      '<h4>Свет горящей лампы</h4>' +
      seg('lab-glow', [['none', 'нет'], ['flare', 'щель'], ['rim', 'край'], ['halo', 'ореол'], ['back', 'сзади']], S.glow) +
      slider('lab-gp', 'Сила света', 0, 200, 5, S.glowPower, '%') +
      '<div class="lab-cols"><span>Свет</span><input type="color" id="lab-l1" value="' + S.light1 + '">' +
      '<input type="color" id="lab-l2" value="' + S.light2 + '"></div>' +
      '<div class="lab-cols"><span>Карточка</span><input type="color" id="lab-c1" value="' + S.card1 + '">' +
      '<input type="color" id="lab-c2" value="' + S.card2 + '"><input type="color" id="lab-c3" value="' + S.card3 + '"></div>' +
      '<h4>Углы</h4>' +
      slider('lab-r', 'Скругление крупных', 0, 40, 1, S.radius) +
      '<div class="lab-note">У мелких плиток угол меньше сам - в долю их стороны.</div>' +
      '<div class="lab-acts"><button class="lab-btn main" id="lab-copy">Скопировать тему</button>' +
      '<button class="lab-btn" id="lab-show">Файл темы</button>' +
      '<button class="lab-btn" id="lab-reset">Сбросить</button></div>' +
      '<div class="lab-note" id="lab-msg">Файл - в папку themes/ рядом с config.yaml на контроллере, ' +
      'тема появится в меню как «Моя тема».</div>' +
      '<pre id="lab-yaml" hidden></pre>';
    bind();
    drawCurve();
  }

  function drawCurve(){
    var cv = document.getElementById('lab-curve');
    if (!cv) return;
    var g = cv.getContext('2d'), W = cv.width, H = cv.height, max = 40, pad = 30;
    g.clearRect(0, 0, W, H);
    // плитка слева, тень - вправо от её края
    var x0 = 70;
    function X(d){ return x0 + (W - x0 - pad) * d / max; }
    function Y(o){ return H - 22 - (H - 40) * o / 100; }
    var st = S.stops.slice().sort(function(a, b){ return a[0] - b[0]; });
    for (var px = x0; px < W - pad; px++) {
      var d = (px - x0) / (W - x0 - pad) * max, o = 0, c = st[st.length - 1][2];
      for (var i = 0; i < st.length - 1; i++) {
        if (d <= st[i + 1][0] || i === st.length - 2) {
          var a = st[i], b = st[i + 1], f = b[0] > a[0] ? Math.max(0, Math.min(1, (d - a[0]) / (b[0] - a[0]))) : 0;
          if (d > st[st.length - 1][0]) f = 1;
          if (S.curve === 'smooth') f = f * f * (3 - 2 * f);
          o = (a[1] + (b[1] - a[1]) * f) * S.power / 100;
          var ca = rgb(a[2]), cb = rgb(b[2]);
          c = 'rgb(' + [0, 1, 2].map(function(k){ return Math.round(ca[k] + (cb[k] - ca[k]) * f); }).join(',') + ')';
          break;
        }
      }
      if (d > st[st.length - 1][0]) o = 0;
      g.globalAlpha = Math.min(1, o / 100);
      g.fillStyle = c;
      g.fillRect(px, 8, 1, H - 30);
    }
    g.globalAlpha = 1;
    g.fillStyle = '#FFFFFF'; g.strokeStyle = 'rgba(52,72,140,.35)';
    g.beginPath(); g.rect(8, 8, x0 - 8, H - 30); g.fill(); g.stroke();
    g.fillStyle = '#7B8299'; g.font = '20px system-ui, sans-serif';
    g.fillText('плитка', 12, H - 34);
    // кривая силы и точки
    g.strokeStyle = '#1B2233'; g.lineWidth = 2; g.beginPath();
    st.forEach(function(p, i){ var x = X(p[0]), y = Y(p[1] * S.power / 100); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.stroke();
    st.forEach(function(p){
      g.fillStyle = p[2]; g.beginPath(); g.arc(X(p[0]), Y(p[1] * S.power / 100), 7, 0, 6.3); g.fill();
      g.strokeStyle = '#FFFFFF'; g.lineWidth = 2; g.stroke();
    });
    g.fillStyle = '#7B8299'; g.font = '18px system-ui, sans-serif';
    for (var t = 0; t <= max; t += 10) g.fillText(t + '', X(t) - 6, H - 4);
  }

  function bind(){
    function $(id){ return document.getElementById(id); }
    function num(id, key, unit){
      var el = $(id); if (!el) return;
      el.oninput = function(){ S[key] = +this.value; $(id + '-v').textContent = this.value + (unit || ''); changed(); };
    }
    num('lab-dy', 'dy'); num('lab-soft', 'soft'); num('lab-power', 'power', '%'); num('lab-shade', 'shade');
    num('lab-ew', 'edgeW'); num('lab-ea', 'edgeA', '%'); num('lab-gp', 'glowPower', '%'); num('lab-r', 'radius');
    [['lab-ec', 'edgeC'], ['lab-l1', 'light1'], ['lab-l2', 'light2'], ['lab-c1', 'card1'],
     ['lab-c2', 'card2'], ['lab-c3', 'card3']].forEach(function(p){
      $(p[0]).oninput = function(){ S[p[1]] = this.value; changed(); };
    });
    $('lab-curve-k').onclick = function(e){
      var v = e.target.getAttribute('data-v'); if (!v) return; S.curve = v; changed(true); };
    $('lab-glow').onclick = function(e){
      var v = e.target.getAttribute('data-v'); if (!v) return; S.glow = v; changed(true); };
    Array.prototype.forEach.call(document.querySelectorAll('#lab .lab-stop'), function(row){
      var i = +row.getAttribute('data-i');
      Array.prototype.forEach.call(row.querySelectorAll('[data-k]'), function(el){
        var k = el.getAttribute('data-k');
        if (k === 'x') { el.onclick = function(){ S.stops.splice(i, 1); changed(true); }; return; }
        el.oninput = function(){
          if (k === 'c') S.stops[i][2] = this.value;
          else {
            S.stops[i][k === 'd' ? 0 : 1] = +this.value;
            this.parentNode.querySelector('b').textContent = this.value + (k === 'o' ? '%' : '');
          }
          changed();
        };
      });
    });
    if ($('lab-add')) $('lab-add').onclick = function(){
      var last = S.stops[S.stops.length - 1];
      S.stops.push([Math.min(40, last[0] + 8), 0, last[2]]);
      changed(true);
    };
    $('lab-show').onclick = function(){
      var pre = $('lab-yaml'); pre.hidden = !pre.hidden; pre.textContent = lastYaml;
    };
    $('lab-copy').onclick = function(){
      var msg = $('lab-msg'), pre = $('lab-yaml');
      pre.hidden = false; pre.textContent = lastYaml;
      function picked(){
        var r = document.createRange(); r.selectNodeContents(pre);
        var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
        msg.textContent = 'Файл выделен - скопируйте его.';
      }
      try {
        navigator.clipboard.writeText(lastYaml).then(function(){
          msg.textContent = 'Скопировано. Сохраните как themes/moya-tema.yaml на контроллере.';
        }, picked);
      } catch(e) { picked(); }
    };
    $('lab-reset').onclick = function(){
      S = JSON.parse(JSON.stringify(DEF));
      try { localStorage.removeItem(KEY); } catch(e) {}
      changed(true);
    };
  }

  function toggle(){
    open = !open;
    document.documentElement.classList.toggle('lab-open', open);
    document.getElementById('lab-btn').textContent = open ? 'Скрыть конструктор' : 'Конструктор темы';
    if (open && !document.querySelector('#lab .lab-stop')) draw();
  }

  function start(){
    var b = document.createElement('button');
    b.id = 'lab-btn'; b.textContent = 'Конструктор темы'; b.onclick = toggle;
    var box = document.createElement('div');
    box.id = 'lab'; box.innerHTML = '<div class="lab-body"></div>';
    document.body.appendChild(box); document.body.appendChild(b);
    // Своя тема из конструктора - сразу, как только сервер панели
    // поднялся; иначе панель в теме из конфига, а файл темы для кнопки
    // «Скопировать» готовится заранее.
    if (mine) apply();
    else fetch('api/theme/draft', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                   body: JSON.stringify(theme())})
      .then(function(r){ return r.json(); }).then(function(j){ lastYaml = j.yaml || ''; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
