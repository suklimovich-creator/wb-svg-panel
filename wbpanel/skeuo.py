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
