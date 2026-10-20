/* wb-svg-panel: живые плитки (1.52) - во время жеста на плитке её рисунок
   двигается за пальцем, как в пульте: колесо диммера крутится, шар ленты
   поворачивается, ручка термостата и шкала кондиционера едут, ползунок
   фанкойла и полотно шторы - тоже.
   Часть скриптов страницы панели (templates/wrapper.html). Обычный скрипт
   с общими глобальными именами; зовётся из жестов (gestures.js).

   Геометрия - та же, что у сервера (skeuo.py, ac_disk.py, sky.py,
   шаблоны плиток): страница перестраивает только то, что зависит от
   значения. Когда прибор ответит, сервер пришлёт плитку, и она заменит
   нарисованную здесь. Не ответит - через несколько секунд плитка
   вернётся к тому, что знает сервер (hold в update.js). */

var LT_NS = 'http://www.w3.org/2000/svg';
function ltEl(tag, a, before, parent){
  var e = document.createElementNS(LT_NS, tag);
  for (var k in a) if (a.hasOwnProperty(k)) e.setAttribute(k, a[k]);
  if (before) before.parentNode.insertBefore(e, before); else if (parent) parent.appendChild(e);
  return e;
}
function ltN(e, a){ return parseFloat(e.getAttribute(a)); }
function ltR(v){ return Math.round(v * 10) / 10; }
function ltPt(cx, cy, r, deg){ var a = deg * Math.PI / 180; return [cx + r * Math.cos(a), cy + r * Math.sin(a)]; }
function ltArc(cx, cy, r, d0, d1){
  if (d1 - d0 < 0.35) return '';
  var a = ltPt(cx, cy, r, d0), b = ltPt(cx, cy, r, d1);
  return 'M ' + a[0].toFixed(1) + ',' + a[1].toFixed(1) + ' A ' + r.toFixed(1) + ',' + r.toFixed(1)
       + ' 0 ' + (d1 - d0 > 180 ? 1 : 0) + ' 1 ' + b[0].toFixed(1) + ',' + b[1].toFixed(1);
}
/* Размер плитки - по подложке: plate на точку внутри края. */
function ltSize(node){
  var p = node.querySelector('.plate');
  if (!p) return null;
  return {w: ltN(p, 'width') + 2, h: ltN(p, 'height') + 2};
}
/* Число уставки, как у сервера: целое - без дробной части, иначе одна цифра. */
function ltNum(v){ return Math.abs(v - Math.round(v)) < 1e-6 ? String(Math.round(v)) : v.toFixed(1); }
function ltDrop(list){ for (var i = 0; i < list.length; i++) list[i].parentNode.removeChild(list[i]); }
/* Проценты в подписях плитки («79 %», «Открыто 40%») - новым числом. */
function ltPct(node, pct, skip){
  var ts = node.querySelectorAll('text');
  for (var i = 0; i < ts.length; i++) {
    if (skip && skip.contains(ts[i])) continue;
    var s = ts[i].textContent;
    if (/\d+\s?%/.test(s)) ts[i].textContent = s.replace(/\d+(\s?%)/, pct + '$1');
  }
}

var LIVE_TILE = {};

/* ---- диммер: колесо в прорези (skeuo.roller) ---- */
LIVE_TILE.dimmer = function(node){
  var r = node.querySelector('.roller');
  var sz = ltSize(node);
  if (!r || !sz) return null;
  var drum = r.querySelector('.drum'), cyl = r.querySelector('rect[fill*="cyl"]');
  var kx = ltN(drum, 'x'), ky = ltN(drum, 'y'), kw = ltN(drum, 'width'), kh = ltN(drum, 'height');
  var rib = r.querySelector('.rib');
  var horiz = rib ? rib.getAttribute('x1') === rib.getAttribute('x2') : kw > kh * 1.4;
  var bar = r.querySelector('.lvl'), track = r.querySelector('.lvl-track');
  var glow = node.querySelector('rect[fill*="dglow"]');
  return {set: function(v){
    ltDrop(r.querySelectorAll('.rib, .rib-hi'));
    var length = horiz ? kw : kh, step = 15 / Math.max(length, 10), n = Math.floor(1.6 / step) + 1;
    var phase = (v * 9) % 1;
    for (var k = -n; k <= n; k++) {
      var th = (k + phase) * step;
      if (Math.abs(th) > 1.52) continue;
      var t = Math.sin(th) * 0.5 + 0.5, w = Math.cos(th);
      var sw = (0.6 + 2.6 * w).toFixed(2), op = (0.25 + 0.5 * w).toFixed(2);
      var hw = (0.6 * w).toFixed(2), hop = (0.10 * w).toFixed(2);
      if (horiz) {
        var px = ltR(kx + t * kw), hx = ltR(kx + t * kw + 1.6 * w);
        ltEl('line', {x1: px, y1: ltR(ky + 3), x2: px, y2: ltR(ky + kh - 3), 'class': 'rib', 'stroke-width': sw, opacity: op}, cyl);
        ltEl('line', {x1: hx, y1: ltR(ky + 3), x2: hx, y2: ltR(ky + kh - 3), 'class': 'rib-hi', 'stroke-width': hw, opacity: hop}, cyl);
      } else {
        var py = ltR(ky + t * kh), hy = ltR(ky + t * kh + 1.6 * w);
        ltEl('line', {x1: ltR(kx + 3), y1: py, x2: ltR(kx + kw - 3), y2: py, 'class': 'rib', 'stroke-width': sw, opacity: op}, cyl);
        ltEl('line', {x1: ltR(kx + 3), y1: hy, x2: ltR(kx + kw - 3), y2: hy, 'class': 'rib-hi', 'stroke-width': hw, opacity: hop}, cyl);
      }
    }
    if (bar && track) {
      if (horiz) bar.setAttribute('width', ltR(ltN(track, 'width') * v));
      else {
        var by = ltN(track, 'y'), bh = ltN(track, 'height');
        bar.setAttribute('y', ltR(by + bh * (1 - v))); bar.setAttribute('height', ltR(bh * v));
      }
    }
    if (glow) { glow.setAttribute('y', ltR(sz.h * (1 - v) * 0.6)); glow.setAttribute('opacity', (0.35 + 0.65 * v).toFixed(2)); }
    ltPct(node, Math.round(v * 100));
  }};
};

/* ---- лента: шар с сотами и шкалы вокруг (skeuo.sphere_tile) ---- */
function ltHex(c){ c = (c || '#888888').replace('#', ''); return [0, 2, 4].map(function(i){ return parseInt(c.substr(i, 2), 16); }); }
function ltMix(a, b, f){ return a.map(function(v, i){ return v + (b[i] - v) * f; }); }
function ltCss(a){ return '#' + a.map(function(v){ var s = Math.round(Math.max(0, Math.min(255, v))).toString(16); return s.length < 2 ? '0' + s : s; }).join('').toUpperCase(); }
LIVE_TILE.light = function(node){
  var s = node.querySelector('.sphere');
  if (!s) return null;
  var body = s.querySelector('circle[fill*="sb"]'), seams = s.querySelector('path[stroke-linejoin]');
  var knobs = s.querySelectorAll('.sph-knob'), track = s.querySelector('.sph-arc');
  if (!body || !seams || knobs.length < 2 || !track) return null;
  var cx = ltN(body, 'cx'), cy = ltN(body, 'cy'), r = ltN(body, 'r');
  var kt = knobs[0], kb = knobs[1];
  var R = Math.sqrt(Math.pow(ltN(kt, 'cx') - cx, 2) + Math.pow(ltN(kt, 'cy') - cy, 2));
  var temp = parseFloat(node.getAttribute('data-dot-x') || '0.5');
  var freq = Math.max(3, Math.min(8, Math.round(r / 11)));
  var G = sphereGeo(freq);
  var fill = s.querySelector('.sph-fill') || ltEl('path', {'class': 'sph-fill', 'stroke-width': track.getAttribute('stroke-width')}, track.nextSibling, s);
  // цвета шара - как у сервера: светлая тема светлее, яркость - насыщенность
  var id = (body.getAttribute('fill').match(/#([^)]+)\)/) || [])[1];
  var grad = id ? document.getElementById(id) : null, stops = grad ? grad.querySelectorAll('stop') : [];
  var halo = s.querySelector('circle[fill*="sh"]:not([fill*="sphsh"])');
  var hid = halo ? (halo.getAttribute('fill').match(/#([^)]+)\)/) || [])[1] : null;
  var hstops = hid && document.getElementById(hid) ? document.getElementById(hid).querySelectorAll('stop') : [];
  var lt = !DARK_THEME[rootTheme()];
  var DARK = lt ? [92, 94, 102] : [0x14, 0x15, 0x18], BASE = [206, 207, 212], MID = [0xEC, 0xE6, 0xDC];
  var COLD = ltHex(node.getAttribute('data-cold') || '#56AAFF'), WARM = ltHex(node.getAttribute('data-warm') || '#FFA854');
  function hueOf(t){ return t < 0.5 ? ltMix(COLD, MID, t * 2) : ltMix(MID, WARM, (t - 0.5) * 2); }
  // v - яркость, tx - теплота (1.53: шар крутят и вбок, как в пульте)
  return {set: function(v, tx){
    if (tx === undefined) tx = temp;
    var hue = hueOf(tx);
    var yaw = (tx - 0.5) * 3.2, pitch = -(v - 0.5) * 2.6;
    var a = Math.cos(yaw), b = Math.sin(yaw), c = Math.cos(pitch), d = Math.sin(pitch);
    function P(q){ var X = q[0] * a + q[2] * b, Z = -q[0] * b + q[2] * a; return [X, q[1] * c - Z * d, q[1] * d + Z * c]; }
    var pc = G.C.map(P), out = [];
    G.cells.forEach(function(cell){
      if (P(G.V[cell.c])[2] < 0.03) return;
      for (var k = 0; k < cell.ring.length; k++) if (pc[cell.ring[k]][2] < 0) return;
      out.push('M' + cell.ring.map(function(i){
        return (cx + pc[i][0] * r).toFixed(1) + ',' + (cy - pc[i][1] * r).toFixed(1); }).join('L') + 'z');
    });
    seams.setAttribute('d', out.join(' '));
    fill.setAttribute('d', ltArcDir(cx, cy, R, 165, 165 + 90 * v));
    var p = ltPt(cx, cy, R, 165 + 90 * v);
    kb.setAttribute('cx', ltR(p[0])); kb.setAttribute('cy', ltR(p[1]));
    var q = ltPt(cx, cy, R, 140 - 100 * tx);
    kt.setAttribute('cx', ltR(q[0])); kt.setAttribute('cy', ltR(q[1]));
    for (var h = 0; h < hstops.length; h++) hstops[h].setAttribute('stop-color', ltCss(hue));
    if (stops.length === 3) {
      var col = ltMix(lt ? BASE : DARK, hue, 0.18 + 0.82 * v);
      stops[0].setAttribute('stop-color', ltCss(ltMix(col, [255, 255, 255], 0.25 * v)));
      stops[1].setAttribute('stop-color', ltCss(ltMix(col, DARK, 0.25)));
      stops[2].setAttribute('stop-color', ltCss(ltMix(col, DARK, lt ? 0.6 : 0.78)));
    }
    if (halo) halo.setAttribute('r', ltR(r * (1.45 + 0.5 * v)));
    ltPct(node, Math.round(v * 100));
  }};
};
function ltArcDir(cx, cy, r, d0, d1){
  if (Math.abs(d1 - d0) < 0.5) return '';
  var a = ltPt(cx, cy, r, d0), b = ltPt(cx, cy, r, d1);
  return 'M ' + a[0].toFixed(1) + ',' + a[1].toFixed(1) + ' A ' + r.toFixed(1) + ',' + r.toFixed(1)
       + ' 0 ' + (Math.abs(d1 - d0) > 180 ? 1 : 0) + ' ' + (d1 > d0 ? 1 : 0) + ' ' + b[0].toFixed(1) + ',' + b[1].toFixed(1);
}

/* ---- термостат: зубчатая ручка, дуга и шкала (skeuo.knob_dial) ---- */
LIVE_TILE.thermostat = function(node, m){
  var kg = node.querySelector('.knob'), sz = ltSize(node);
  if (!kg || !sz) return null;
  var lo = parseFloat(node.getAttribute('data-scale-min') || '14');
  var hi = parseFloat(node.getAttribute('data-scale-max') || '30');
  var START = parseFloat(node.getAttribute('data-dial-start') || '148');
  var SWEEP = parseFloat(node.getAttribute('data-dial-sweep') || '244');
  var mm = Math.min(sz.w, sz.h), cx = sz.w / 2, cy = sz.h * 0.585, ra = 0.33 * mm, rk = ra - 0.093 * mm;
  function deg(v){ return START + Math.max(0, Math.min(1, (v - lo) / ((hi - lo) || 1))) * SWEEP; }
  var trackP = kg.querySelector('.ktrack');
  var fill = kg.querySelector('.kfill') || ltEl('path', {'class': 'kfill', 'stroke-width': trackP.getAttribute('stroke-width')}, trackP.nextSibling, kg);
  var dot = kg.querySelector('.kdot'), ptr = kg.querySelector('.kptr');
  var nums = kg.querySelectorAll('.knum'), num = nums[0], degT = kg.querySelector('.kdeg');
  // ручка поворачивается целиком: зубцы, блик и указатель
  var turn = kg.querySelectorAll('use, .kface');
  var a0 = deg(m.v0);
  return {set: function(v){
    var at = deg(v);
    fill.setAttribute('d', ltArc(cx, cy, ra, START, at));
    if (dot) { var p = ltPt(cx, cy, ra, at); dot.setAttribute('cx', ltR(p[0])); dot.setAttribute('cy', ltR(p[1])); }
    if (ptr) {
      var p1 = ltPt(cx, cy, rk - 0.043 * mm, at), p2 = ltPt(cx, cy, rk - 0.011 * mm, at);
      ptr.setAttribute('x1', ltR(p1[0])); ptr.setAttribute('y1', ltR(p1[1]));
      ptr.setAttribute('x2', ltR(p2[0])); ptr.setAttribute('y2', ltR(p2[1]));
    }
    for (var i = 0; i < turn.length; i++)
      turn[i].setAttribute('transform', 'rotate(' + (at - a0).toFixed(1) + ' ' + ltR(cx) + ' ' + ltR(cy) + ')');
    if (num) {
      num.textContent = ltNum(v);
      if (degT && num.getComputedTextLength) degT.setAttribute('x', ltR(cx + num.getComputedTextLength() / 2 + 1));
    }
  }};
};

/* ---- кондиционер: колесо линиями, шкала под риской (ac_disk.build_arc) ---- */
LIVE_TILE.ac = function(node, m){
  var g = node.querySelector('.acw'), sz = ltSize(node);
  if (!g || !sz) return null;
  var lab = g.querySelector('.acw-lab'), tag = g.querySelector('.tag');
  var fs = lab ? ltN(lab, 'font-size') / (lab.classList.contains('sel') ? 13 : 11)
               : (tag ? ltN(tag, 'font-size') / 11 : 1);
  var w = sz.w, hh = Math.min(sz.h, w), RR = 0.88 * w, cx = w / 2, cy = 0.72 * hh - RR;
  var ad = 34 * fs / RR, amax = Math.asin(Math.min(1, (w / 2 + 4) / RR));
  var lo = parseFloat(node.getAttribute('data-ac-lo') || '16'), hi = parseFloat(node.getAttribute('data-ac-hi') || '30');
  var cur = parseFloat((node.getAttribute('data-ac-cur') || '').replace(',', '.'));
  var rim = g.querySelector('.acw-rim'), num = g.querySelector('.acw-num');
  var segs = g.querySelectorAll('.acw-glow, .acw-glow2, .acw-seg'), dotC = g.querySelector('.acw-dot');
  function pol(r, a){ return [cx + r * Math.sin(a), cy + r * Math.cos(a)]; }
  return {set: function(v){
    function ang(t){ return (t - v) * ad; }
    ltDrop(g.querySelectorAll('.acw-tk, .acw-lab'));
    var n = Math.round((hi - lo) * 2);
    for (var i = 0; i <= n; i++) {
      var t = lo + i * 0.5, a = ang(t);
      if (Math.abs(a) > amax) continue;
      var whole = Math.abs(t - Math.round(t)) < 0.01;
      var p1 = pol(RR - 1, a), p2 = pol(RR - (whole ? 9 : 5) * fs, a);
      ltEl('line', {x1: ltR(p1[0]), y1: ltR(p1[1]), x2: ltR(p2[0]), y2: ltR(p2[1]), 'class': 'acw-tk' + (whole ? ' mj' : '')}, rim);
      if (whole && Math.abs(a) <= amax * 0.78) {
        var lp = pol(RR - 22 * fs, a), sel = Math.abs(t - v) < 0.5;
        var tx = ltEl('text', {x: ltR(lp[0]), y: ltR(lp[1] + 4 * fs), 'font-size': ltR((sel ? 13 : 11) * fs),
          'text-anchor': 'middle', 'class': 'acw-lab' + (sel ? ' sel' : ''),
          transform: 'rotate(' + ltR(-a * 180 / Math.PI) + ' ' + ltR(lp[0]) + ' ' + ltR(lp[1]) + ')'}, rim);
        tx.textContent = Math.round(t) + '°';
      }
    }
    if (num && num.firstChild && num.firstChild.nodeType === 3) num.firstChild.nodeValue = ltNum(v);
    if (!isNaN(cur)) {
      var ac = Math.max(-amax, Math.min(amax, ang(cur)));
      if (dotC) { var d = pol(RR, ac); dotC.setAttribute('cx', ltR(d[0])); dotC.setAttribute('cy', ltR(d[1])); }
      if (segs.length) {
        var a1 = Math.max(ac, 0), a2 = Math.min(ac, 0), q0 = pol(RR, a1), q1 = pol(RR, a2);
        var dd = Math.abs(ac) > 0.01 ? 'M' + q0[0].toFixed(1) + ',' + q0[1].toFixed(1) + ' A' + RR.toFixed(1) + ',' + RR.toFixed(1)
               + ' 0 0 1 ' + q1[0].toFixed(1) + ',' + q1[1].toFixed(1) : '';
        for (var s = 0; s < segs.length; s++) segs[s].setAttribute('d', dd);
      }
    }
  }};
};

/* ---- фанкойл: ползунок и табло, как у пульта (fcShow, pads-climate.js) ---- */
LIVE_TILE.fancoil = function(node){
  var G = node.querySelector('[data-fcl]');
  if (!G) return null;
  var D = fcData(G);
  return {set: function(v){ fcShow(G, D, v); }};
};

/* ---- рулонная штора: полотно сверху ---- */
LIVE_TILE.roller = function(node){
  var sz = ltSize(node);
  if (!sz) return null;
  var w = sz.w, h = sz.h;
  var folds = node.querySelectorAll('path.drape');
  if (folds.length) {
    // римская над окном (sky.roman): складки с провисом и ручка
    var hd = node.querySelector('.handle'), fs = hd ? ltN(hd, 'height') / 5 : 1;
    var after = folds[folds.length - 1].nextSibling;
    while (after && after.getAttribute && /rfold/.test(after.getAttribute('fill') || '')) after = after.nextSibling;
    var rf = node.querySelector('path[fill*="rfold"]'), rfill = rf ? rf.getAttribute('fill') : null;
    return {set: function(v){
      ltDrop(node.querySelectorAll('path.drape, path[fill*="rfold"]'));
      var fold = Math.max(14 * fs, h * 0.11), drop = Math.max(h * (1 - v), fold * 0.75);
      var n = Math.max(1, Math.round(drop / fold)), fh = drop / n, sag = Math.min(7 * fs, fh * 0.28);
      for (var i = 0; i < n; i++) {
        var y = i * fh;
        var d = 'M 0,' + (y - 2).toFixed(1) + ' Q ' + (w / 2).toFixed(1) + ',' + (y + 2).toFixed(1) + ' ' + w.toFixed(1) + ',' + (y - 2).toFixed(1)
              + ' L ' + w.toFixed(1) + ',' + (y + fh).toFixed(1) + ' Q ' + (w / 2).toFixed(1) + ',' + (y + fh + sag).toFixed(1) + ' 0,' + (y + fh).toFixed(1) + ' Z';
        ltEl('path', {d: d, 'class': 'drape'}, after);
        if (rfill) ltEl('path', {d: d, fill: rfill}, after);
      }
      if (hd) hd.setAttribute('y', ltR(drop + sag + 4 * fs));
      ltPct(node, Math.round(v * 100));
    }};
  }
  var weave = node.querySelector('rect[fill*="weave"]');
  if (!weave) return null;
  var pleat = node.querySelector('.pleat'), foldB = node.querySelector('rect[fill*="foldB"]'), hem = node.querySelector('.hem');
  return {set: function(v){
    var stack = h * (0.06 + 0.10 * v), drop = Math.max(h * (1 - v), stack);
    var step = Math.min(h * 0.15, Math.max(drop / 3, 3));
    weave.setAttribute('height', drop.toFixed(1));
    if (pleat) {
      ltDrop(pleat.querySelectorAll('line'));
      for (var k = 1; k < 12; k++) {
        var py = k * step;
        if (py < drop - 1.5) ltEl('line', {x1: 0, y1: py.toFixed(1), x2: w, y2: py.toFixed(1)}, null, pleat);
      }
    }
    if (foldB) { foldB.setAttribute('y', Math.max(drop - 12, 0).toFixed(1)); foldB.setAttribute('height', Math.min(drop, 12).toFixed(1)); }
    if (hem) { hem.setAttribute('y', Math.max(drop - 2.4, 0).toFixed(1)); hem.setAttribute('height', Math.min(drop, 2.4).toFixed(1)); }
    ltPct(node, Math.round(v * 100));
  }};
};

/* ---- раздвижная штора: створки от краёв ---- */
LIVE_TILE.curtain = function(node){
  var sz = ltSize(node);
  if (!sz) return null;
  var w = sz.w, h = sz.h, half = w / 2;
  var drapes = node.querySelectorAll('rect.drape');
  if (drapes.length === 2) {
    // над окном (sky.drapes): полосы складок сжимаются к краю
    var hd = node.querySelector('.handle'), fs = hd ? ltN(hd, 'width') / 5 : 1;
    var stripes = node.querySelectorAll('rect[fill*="plt"]');
    var n = Math.max(1, stripes.length / 2), pfill = stripes.length ? stripes[0].getAttribute('fill') : null;
    var after = stripes.length ? stripes[stripes.length - 1].nextSibling : drapes[1].nextSibling;
    var gather = Math.max(8, w * 0.07);
    return {set: function(v){
      ltDrop(node.querySelectorAll('rect.drape, rect[fill*="plt"]'));
      var rx = 0;
      ['l', 'r'].forEach(function(side){
        var pw = Math.max(gather, half * (1 - v)), x0 = side === 'l' ? 0 : w - pw, sw = pw / n;
        ltEl('rect', {x: ltR(x0), y: 0, width: ltR(pw), height: h, 'class': 'drape'}, after);
        if (pfill) for (var k = 0; k < n; k++)
          ltEl('rect', {x: ltR(x0 + k * sw), y: 0, width: ltR(sw + 0.4), height: h, fill: pfill}, after);
        if (side === 'r') rx = x0;
      });
      if (hd) hd.setAttribute('x', ltR(rx - 2.5 * fs));
      ltPct(node, Math.round(v * 100));
    }};
  }
  var weaves = node.querySelectorAll('rect[fill*="weave"]');
  var fl = node.querySelector('rect[fill*="foldL"]'), fr = node.querySelector('rect[fill*="foldR"]');
  // у полностью открытой створки сервер её не рисует - тогда только плашка
  if (weaves.length !== 2 || !fl || !fr) return null;
  var wl = weaves[0], wr = weaves[1];
  return {set: function(v){
    var lw = Math.max(1, half * (1 - v)), rw = lw;
    wl.setAttribute('width', lw.toFixed(1));
    fl.setAttribute('x', Math.max(lw - 12, 0).toFixed(1)); fl.setAttribute('width', Math.min(lw, 12).toFixed(1));
    wr.setAttribute('x', (w - rw).toFixed(1)); wr.setAttribute('width', rw.toFixed(1));
    fr.setAttribute('x', (w - rw).toFixed(1)); fr.setAttribute('width', Math.min(rw, 12).toFixed(1));
    ltPct(node, Math.round(v * 100));
  }};
};

/* Живая плитка для жеста или null - тогда плашка поверх (gestures.js). */
function liveTile(node, m){
  var f = LIVE_TILE[node.getAttribute('data-kind')];
  if (!f) return null;
  try { return f(node, m); } catch (e) { return null; }
}
