"""
Органы управления «как настоящие»: ручка термостата, колесо диммера.

Здесь только геометрия - точки, пути и размеры. Цвета берёт шаблон из
темы: так одна и та же ручка живёт и в светлой, и в графитовой.

Ручка термостата собрана по согласованным макетам (14.10): прежняя дуга
от левого нижнего края до правого, в центре зубчатая ручка с уставкой,
снаружи шкала делений с подписями. Дорожка закрашена от начала шкалы до
уставки, текущая температура - риска поперёк дорожки и шкалы.
"""

import math

from .ac_disk import gear_path
from .geometry import _fmt, text_width

#: Дуга термостата: та же, что была (tiles.DIAL_START / DIAL_SWEEP).
START = 148.0
SWEEP = 244.0


def _pt(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def _arc(cx, cy, r, d0, d1):
    if d1 - d0 < 0.35:
        return ""
    x0, y0 = _pt(cx, cy, r, d0)
    x1, y1 = _pt(cx, cy, r, d1)
    return "M %.1f,%.1f A %.1f,%.1f 0 %d 1 %.1f,%.1f" % (
        x0, y0, r, r, 1 if d1 - d0 > 180 else 0, x1, y1)


def _r(v):
    return round(v, 1)


def knob_dial(width, height, lo, hi, current, target, state, digits=0):
    """
    Ручка термостата для плитки width×height.

    state: "heat" - греет, "idle" - включён и не греет, "off" - выключен.
    Размеры от меньшей стороны m: на макете плитка 280, на панели 170,
    на весь экран - сколько дадут. Подписи шкалы не мельче 7 px.
    """
    m = float(min(width, height))
    cx, cy = width / 2.0, height * 0.585
    ra = 0.33 * m                      # дорожка
    rk = ra - 0.093 * m                # ручка
    tw = max(4.0, 0.025 * m)           # толщина дорожки
    span = max(float(hi) - float(lo), 1e-6)

    def deg(v):
        f = (float(v) - float(lo)) / span
        return START + max(0.0, min(1.0, f)) * SWEEP

    # Деления через градус, крупные и подписи - через четыре от начала.
    ticks, labels = [], []
    lab = max(7.0, 0.034 * m)
    t0 = int(math.ceil(lo))
    n = int(math.floor(hi)) - t0
    every = 4 if n <= 40 else 8
    for v in range(t0, int(math.floor(hi)) + 1):
        a = deg(v)
        major = (v - t0) % every == 0
        x1, y1 = _pt(cx, cy, ra + 0.025 * m, a)
        x2, y2 = _pt(cx, cy, ra + (0.057 if major else 0.043) * m, a)
        ticks.append({"x1": _r(x1), "y1": _r(y1), "x2": _r(x2), "y2": _r(y2), "major": major})
        if major:
            lx, ly = _pt(cx, cy, ra + 0.025 * m + 0.071 * m + lab * 0.2, a)
            # подпись не должна упираться в название плитки (оно на y=20)
            if ly - lab * 0.7 < 27:
                continue
            labels.append({"x": _r(lx), "y": _r(ly + lab * 0.36), "text": str(v)})

    out = {
        "cx": _r(cx), "cy": _r(cy), "ra": _r(ra), "rk": _r(rk), "tw": _r(tw),
        "track": _arc(cx, cy, ra, START, START + SWEEP),
        "fill": "", "ticks": ticks, "labels": labels, "lab": _r(lab),
        "tick_w": _r(max(1.0, 0.0036 * m)), "tick_w2": _r(max(1.5, 0.006 * m)),
        "gear": gear_path(cx, cy, rk, max(0.5, 0.8 * m / 280.0)),
        "state": state, "cur": None, "dot": None, "ptr": None,
        "num_size": _r(max(16.0, 0.114 * m)), "sub_size": _r(max(8.0, 0.0375 * m)),
        "deg_size": _r(max(8.0, 0.046 * m)),
        "shadow_dy": _r(max(2.0, 0.014 * m)),
    }
    if target is not None:
        at = deg(target)
        out["fill"] = _arc(cx, cy, ra, START, at)
        x, y = _pt(cx, cy, ra, at)
        out["dot"] = {"x": _r(x), "y": _r(y), "r": _r(max(4.0, 0.023 * m)),
                      "sw": _r(max(1.5, 0.007 * m))}
        x1, y1 = _pt(cx, cy, rk - 0.043 * m, at)
        x2, y2 = _pt(cx, cy, rk - 0.011 * m, at)
        out["ptr"] = {"x1": _r(x1), "y1": _r(y1), "x2": _r(x2), "y2": _r(y2),
                      "w": _r(max(2.0, 0.013 * m))}
        num = _fmt(target, digits)
        out["num"] = num
        out["deg_dx"] = _r(text_width(num, out["num_size"]) / 2.0 + 1.0)
    if current is not None:
        ac = deg(current)
        x1, y1 = _pt(cx, cy, ra - 0.025 * m, ac)
        x2, y2 = _pt(cx, cy, ra + 0.061 * m, ac)
        out["cur"] = {"x1": _r(x1), "y1": _r(y1), "x2": _r(x2), "y2": _r(y2),
                      "w": _r(max(2.0, 0.0114 * m)), "halo": _r(max(4.5, 0.025 * m))}
    return out


def roller(x, y, w, h, level, horiz=False):
    """
    Колесо диммера сбоку: рёбра сгущаются к краям, как на цилиндре.

    Шаг рёбер около 7 px по окружности при любой высоте колеса. Сдвиг
    рёбер зависит от яркости - на плитке видно, что колесо повёрнуто.
    """
    length = float(w if horiz else h)
    step = 15.0 / max(length, 10.0)
    n = int(1.6 / step) + 1
    phase = (level * 9.0) % 1.0
    ribs = []
    for k in range(-n, n + 1):
        th = (k + phase) * step
        if abs(th) > 1.52:
            continue
        t = math.sin(th) * 0.5 + 0.5
        wgt = math.cos(th)
        if horiz:
            p = x + t * w
            ribs.append({"x1": _r(p), "y1": _r(y + 3), "x2": _r(p), "y2": _r(y + h - 3),
                         "hx": _r(p + 1.6 * wgt), "w": round(0.6 + 2.6 * wgt, 2),
                         "op": round(0.25 + 0.5 * wgt, 2), "hop": round(0.10 * wgt, 2),
                         "hw": round(0.6 * wgt, 2)})
        else:
            p = y + t * h
            ribs.append({"x1": _r(x + 3), "y1": _r(p), "x2": _r(x + w - 3), "y2": _r(p),
                         "hy": _r(p + 1.6 * wgt), "w": round(0.6 + 2.6 * wgt, 2),
                         "op": round(0.25 + 0.5 * wgt, 2), "hop": round(0.10 * wgt, 2),
                         "hw": round(0.6 * wgt, 2)})
    return ribs


def dimmer_layout(w, h, level):
    """
    Где на плитке диммера колесо, шкала и диод.

    Стоя (0.5×1 и выше) - колесо по центру, шкала справа, диод сверху.
    Квадратная мелкая (0.5×0.5) - колесо поменьше, без диода.
    Лёжа (1×0.5) - колесо слева горизонтально, шкала под ним, диод справа.
    Колесо со шкалой центрируется как одна группа.
    """
    if w >= h * 1.6:
        kw, kh = 0.36 * w, 0.49 * h
        kx, ky = 0.064 * w, 0.165 * h
        return {"kind": "wide", "kx": _r(kx), "ky": _r(ky), "kw": _r(kw), "kh": _r(kh),
                "bar": {"x": _r(kx), "y": _r(ky + kh + 10), "w": _r(kw), "h": 4, "horiz": True},
                "led": {"x": _r(w - 26), "y": 13}, "text_x": _r(kx + kw + 16),
                "ribs": roller(kx, ky, kw, kh, level, horiz=True)}
    if h < 140:
        kw, kh = 0.45 * w, 0.57 * h
        gap = 9.0
        kx = (w - (kw + gap + 4)) / 2.0
        ky = 0.105 * h
        return {"kind": "small", "kx": _r(kx), "ky": _r(ky), "kw": _r(kw), "kh": _r(kh),
                "bar": {"x": _r(kx + kw + gap), "y": _r(ky), "w": 4, "h": _r(kh), "horiz": False},
                "led": None, "ribs": roller(kx, ky, kw, kh, level)}
    kw = 0.49 * w if h >= 1.6 * w else 0.32 * w
    kh = 0.56 * h
    gap = 12.0
    kx = (w - (kw + gap + 4)) / 2.0
    ky = 0.157 * h
    return {"kind": "tall", "kx": _r(kx), "ky": _r(ky), "kw": _r(kw), "kh": _r(kh),
            "bar": {"x": _r(kx + kw + gap), "y": _r(ky), "w": 4, "h": _r(kh), "horiz": False},
            "led": {"x": _r(kx + kw / 2.0), "y": _r(ky / 2.0)},
            "ribs": roller(kx, ky, kw, kh, level)}


def switch_layout(w, h):
    """Клавиша выключателя: размер и место по форме плитки."""
    if w >= h * 1.6:              # лёжа: клавиша слева, подпись справа
        kw, kh = 0.25 * w, 0.64 * h
        return {"kind": "wide", "kx": _r(0.064 * w), "ky": _r((h - kh) / 2.0), "kw": _r(kw),
                "kh": _r(kh), "horiz": True, "text_x": _r(0.064 * w + kw + 12)}
    if h < 140:                   # мелкая квадратная
        kw, kh = 0.45 * w, 0.57 * h
        return {"kind": "small", "kx": _r((w - kw) / 2.0), "ky": _r(0.12 * h), "kw": _r(kw),
                "kh": _r(kh), "horiz": False}
    if w >= h * 0.8:              # квадратная крупная: клавиша по центру сверху
        kw, kh = 0.31 * w, 0.61 * h
        return {"kind": "square", "kx": _r((w - kw) / 2.0), "ky": _r(0.093 * h), "kw": _r(kw),
                "kh": _r(kh), "horiz": False}
    kw, kh = 0.49 * w, 0.61 * h   # стоя
    return {"kind": "tall", "kx": _r((w - kw) / 2.0), "ky": _r(0.107 * h), "kw": _r(kw),
            "kh": _r(kh), "horiz": False}


def diode_box(kx, ky, kw, kh, horiz):
    """Окошко диода на клавише: полоска у верхнего (или правого) края."""
    if horiz:
        dw, dh = 6.5, min(26.0, kh * 0.32)
        return {"x": _r(kx + kw - 18 - dw / 2), "y": _r(ky + kh / 2 - dh / 2), "w": dw, "h": _r(dh)}
    dw, dh = min(26.0, kw * 0.42), 6.5
    return {"x": _r(kx + kw / 2 - dw / 2), "y": _r(ky + min(22.0, kh * 0.13) - dh / 2), "w": _r(dw), "h": dh}


# ---------------------------------------------------------------- шар ленты
#
# Шар светится цветом ленты: слева холодный, справа тёплый (цвета поля
# прежней плитки), посередине нейтральный белый. Яркость - сила свечения.
# По шару мелкие соты - геодезическая сетка: видно, что он повёрнут.
# Поворот задают сами значения: вверх-вниз - яркость, влево-вправо -
# теплота; пульт крутит шар за пальцем от этого же положения.

#: Поворот шара на всю шкалу, радианы. Те же числа в пульте (wrapper.html).
SPHERE_PITCH = 2.6
SPHERE_YAW = 3.2
_DARK = (0x14, 0x15, 0x18)
_MID = (0xEC, 0xE6, 0xDC)
_GEO = {}


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, f):
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


def _css(c):
    return "#%02X%02X%02X" % tuple(int(round(max(0, min(255, v)))) for v in c)


def light_hue(temp, cold, warm):
    """Цвет света: temp 0 - холодный, 1 - тёплый."""
    cold, warm = _hex(cold), _hex(warm)
    return _mix(cold, _MID, temp * 2) if temp < 0.5 else _mix(_MID, warm, (temp - 0.5) * 2)


def geodesic(freq):
    """Соты на шаре: ячейки вокруг вершин дробного икосаэдра."""
    if freq in _GEO:
        return _GEO[freq]
    p = (1 + 5 ** 0.5) / 2
    ico = [(-1, p, 0), (1, p, 0), (-1, -p, 0), (1, -p, 0), (0, -1, p), (0, 1, p), (0, -1, -p),
           (0, 1, -p), (p, 0, -1), (p, 0, 1), (-p, 0, -1), (-p, 0, 1)]
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
             (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
             (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]

    def nrm(v):
        n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        return (v[0] / n, v[1] / n, v[2] / n)

    verts, index, tris = [], {}, []

    def vid(v):
        v = nrm(v)
        key = tuple(round(x, 5) for x in v)
        if key not in index:
            index[key] = len(verts)
            verts.append(v)
        return index[key]

    F = freq
    for a, b, c in faces:
        A, B, C = ico[a], ico[b], ico[c]
        grid = []
        for i in range(F + 1):
            row = []
            for j in range(F + 1 - i):
                row.append(vid(tuple(A[k] + (B[k] - A[k]) * i / F + (C[k] - A[k]) * j / F
                                     for k in range(3))))
            grid.append(row)
        for i in range(F):
            for j in range(F - i):
                tris.append((grid[i][j], grid[i + 1][j], grid[i][j + 1]))
                if j < F - i - 1:
                    tris.append((grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))
    cen = [nrm(tuple(verts[t[0]][k] + verts[t[1]][k] + verts[t[2]][k] for k in range(3)))
           for t in tris]
    adj = [[] for _ in verts]
    for k, t in enumerate(tris):
        for v in t:
            adj[v].append(k)
    cells = []
    for vi, d in enumerate(verts):
        u = (1, 0, 0) if abs(d[0]) < 0.9 else (0, 1, 0)
        e1 = nrm((d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0]))
        e2 = (d[1] * e1[2] - d[2] * e1[1], d[2] * e1[0] - d[0] * e1[2], d[0] * e1[1] - d[1] * e1[0])

        def ang(k):
            c = cen[k]
            return math.atan2(sum(c[i] * e2[i] for i in range(3)), sum(c[i] * e1[i] for i in range(3)))
        cells.append((vi, sorted(adj[vi], key=ang)))
    _GEO[freq] = (verts, cen, cells)
    return _GEO[freq]


def sphere_seams(cx, cy, r, yaw, pitch, freq):
    """Швы сот на видимой стороне шара одним путём."""
    verts, cen, cells = geodesic(freq)
    cy1, sy1, cx1, sx1 = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)

    def P(v):
        x = v[0] * cy1 + v[2] * sy1
        z = -v[0] * sy1 + v[2] * cy1
        y = v[1]
        return (x, y * cx1 - z * sx1, y * sx1 + z * cx1)

    pc = [P(c) for c in cen]
    out = []
    for vi, ring in cells:
        if P(verts[vi])[2] < 0.03 or any(pc[k][2] < 0 for k in ring):
            continue
        out.append("M " + " L ".join("%.1f,%.1f" % (cx + pc[k][0] * r, cy - pc[k][1] * r)
                                     for k in ring) + " Z")
    return " ".join(out)


def sphere_tile(w, h, level, temp, on, cold, warm, compact):
    """Шар ленты на плитке: место, цвета, соты и шкалы вокруг."""
    if w >= h * 1.6:
        kind = "wide"
        r = h / 2.0 - 17
        R = r + max(6.0, r * 0.18)
        cx, cy = 8 + R + 2, h / 2.0
        led = {"x": w - 26, "y": 13}
        text_x = cx + R + 10
    elif h < 140:
        kind = "small"
        r = min(w / 2.0 - 14, (h - 22) / 2.0 - 10)
        R = r + max(5.0, r * 0.18)
        cx, cy = w / 2.0, 8 + (h - 30) / 2.0
        led, text_x = None, None
    elif w >= h * 0.8:
        kind = "square"
        r = min(w, h) * 0.27
        R = r + max(7.0, r * 0.16)
        cx, cy = w / 2.0, h * 0.45
        led = {"x": w / 2.0, "y": 14}
        text_x = None
    else:
        kind = "tall"
        top, bottom = 26.0, h - (40 if compact else 50)
        r = min(w / 2.0 - 14, (bottom - top) / 2.0 - 10)
        R = r + max(6.0, r * 0.18)
        cx, cy = w / 2.0, (top + bottom) / 2.0
        led = {"x": w / 2.0, "y": 13}
        text_x = None

    hue = light_hue(temp, cold, warm)
    lv = level if on else 0.0
    col = _mix(_DARK, hue, 0.18 + 0.82 * (level if on else 0.25))
    yaw = (temp - 0.5) * SPHERE_YAW
    pitch = -(level - 0.5) * SPHERE_PITCH
    freq = max(3, min(8, int(round(r / 11.0))))
    sw = max(2.0, r * 0.06)

    def arc_d(d0, d1, rad):
        x0, y0 = _pt(cx, cy, rad, d0)
        x1, y1 = _pt(cx, cy, rad, d1)
        if abs(d1 - d0) < 0.5:
            return ""
        return "M %.1f,%.1f A %.1f,%.1f 0 %d %d %.1f,%.1f" % (
            x0, y0, rad, rad, 1 if abs(d1 - d0) > 180 else 0, 1 if d1 > d0 else 0, x1, y1)

    B0, B1, T0, T1 = 165.0, 255.0, 140.0, 40.0
    n = 14 if r < 30 else 22
    dots = []
    for i in range(n):
        f = i / float(n - 1)
        x, y = _pt(cx, cy, R, T0 - (T0 - T1) * f)
        dots.append({"x": _r(x), "y": _r(y), "c": _css(light_hue(f, cold, warm))})
    bx, by = _pt(cx, cy, R, B0 + (B1 - B0) * level)
    tx, ty = _pt(cx, cy, R, T0 - (T0 - T1) * temp)
    return {
        "kind": kind, "cx": _r(cx), "cy": _r(cy), "r": _r(r), "R": _r(R),
        "led": {"x": _r(led["x"]), "y": _r(led["y"])} if led else None,
        "text_x": _r(text_x) if text_x else None,
        "hue": _css(hue), "halo_op": round(0.5 * lv, 2), "halo_r": _r(r * (1.45 + 0.5 * lv)),
        "c0": _css(_mix(col, (255, 255, 255), 0.25 * lv)) if on else "#585A60",
        "c1": _css(_mix(col, _DARK, 0.25)) if on else "#2E2F34",
        "c2": _css(_mix(col, _DARK, 0.78)) if on else "#121316",
        "seam": _css(_mix(col, _DARK, 0.5)) if on else "#96989E",
        "seam_op": 0.45 if on else 0.22, "seam_w": _r(max(0.5, r / 160.0)),
        "seams": sphere_seams(cx, cy, r, yaw, pitch, freq),
        "b_track": arc_d(B0, B1, R), "b_fill": arc_d(B0, B0 + (B1 - B0) * level, R),
        "dots": dots, "dot_r": _r(sw * 0.62), "sw": _r(sw),
        "knob_b": {"x": _r(bx), "y": _r(by)}, "knob_t": {"x": _r(tx), "y": _r(ty)},
        "knob_r": _r(sw * 1.55),
    }


# ------------------------------------------------------------- телевизор
def _sector(cx, cy, r0, r1, a0, a1):
    """Четверть кольца между r0 и r1 - зона стрелки на колесе."""
    p = lambda r, a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
    o0, o1, i1, i0 = p(r1, a0), p(r1, a1), p(r0, a1), p(r0, a0)
    return ("M %.1f,%.1f A %.1f,%.1f 0 0 1 %.1f,%.1f L %.1f,%.1f "
            "A %.1f,%.1f 0 0 0 %.1f,%.1f Z") % (o0 + (r1, r1) + o1 + i1 + (r0, r0) + i0)


def tv_wheel(cx, cy, R, fs=1.0):
    """Колесо пульта: зубчатый обод, стрелки по сторонам, OK в центре."""
    ok = R * 0.36
    d = R * 0.70
    s = max(4.0, R * 0.13)
    arrows = []
    for name, ang in (("up", -90), ("right", 0), ("down", 90), ("left", 180)):
        a = math.radians(ang)
        ux, uy = math.cos(a), math.sin(a)          # наружу
        vx, vy = -uy, ux                            # поперёк
        tip = (cx + ux * (d + s * 0.65), cy + uy * (d + s * 0.65))
        b1 = (cx + ux * (d - s * 0.5) + vx * s, cy + uy * (d - s * 0.5) + vy * s)
        b2 = (cx + ux * (d - s * 0.5) - vx * s, cy + uy * (d - s * 0.5) - vy * s)
        arrows.append({
            "key": name,
            "tri": "M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f Z" % (tip + b1 + b2),
            "zone": _sector(cx, cy, ok, R + 2, ang - 45, ang + 45),
        })
    return {"cx": _r(cx), "cy": _r(cy), "R": _r(R), "ok": _r(ok),
            "gear": gear_path(cx, cy, R, max(0.55, min(1.4, R / 70.0)) * fs),
            "sh": _r(max(1.5, R * 0.05)), "arrows": arrows,
            "ok_font": _r(max(9.0, ok * 0.62))}


def tv_tile(w, h, compact=False):
    """
    Плитка телевизора нового вида (1.21). Раскладки:

      square - колесо сверху, «назад» и «домой» по нижним углам колеса,
               подпись под ними (макет 15.10);
      wide   - колесо справа, кнопки слева от него;
      tall   - 0.5×1: колесо и под ним две кнопки;
      low    - половинная высота: подпись слева, малое колесо справа;
      small  - 0.5×0.5: только малое колесо.

    Нажимать по колесу можно, только когда оно достаточно крупное (keys):
    промах по стрелке в полтора десятка пикселей дороже, чем лишнее
    открытие пульта.
    """
    m = min(w, h)
    if h < 117:
        kind = "small" if compact else "low"
    elif compact:
        kind = "tall"
    elif w >= 1.6 * h:
        kind = "wide"
    else:
        kind = "square"

    btn = None
    if kind == "square":
        R = min(w * 0.29, (h - 78) / 2.0)
        wh = tv_wheel(w / 2, 12 + R, R)
        by = wh["cy"] + R * 0.78
        br = max(11.0, R * 0.21)
        btn = [{"key": "back", "cx": _r(max(br + 12, w / 2 - R * 1.05)), "cy": _r(by), "r": _r(br)},
               {"key": "home", "cx": _r(min(w - br - 12, w / 2 + R * 1.05)), "cy": _r(by), "r": _r(br)}]
    elif kind == "wide":
        R = h * 0.36
        wh = tv_wheel(w - 22 - R, h / 2, R)
        br = max(12.0, R * 0.22)
        bx = wh["cx"] - R - 14 - br
        btn = [{"key": "back", "cx": _r(bx), "cy": _r(h / 2 - br - 5), "r": _r(br)},
               {"key": "home", "cx": _r(bx), "cy": _r(h / 2 + br + 5), "r": _r(br)}]
    elif kind == "tall":
        R = w * 0.38
        wh = tv_wheel(w / 2, 14 + R, R)
        br = 11.0
        by = wh["cy"] + R + 8 + br
        btn = [{"key": "back", "cx": _r(w / 2 - br - 4), "cy": _r(by), "r": _r(br)},
               {"key": "home", "cx": _r(w / 2 + br + 4), "cy": _r(by), "r": _r(br)}]
    elif kind == "low":
        R = h * 0.30
        wh = tv_wheel(w - 12 - R, h / 2, R)
    else:
        R = m * 0.30
        wh = tv_wheel(w / 2, h * 0.40, R)
    # Стрелки и OK - только на крупном колесе; «назад» и «домой» всегда:
    # у них своя зона с запасом вокруг кружка.
    keys = R >= 40
    for b in btn or []:
        b["icon"] = max(10.0, b["r"] * 1.05)
    return {"kind": kind, "wheel": wh, "buttons": btn or [], "keys": keys,
            "text_w": _r((wh["cx"] - R - 12 - 18) if kind == "low" else 0)}
