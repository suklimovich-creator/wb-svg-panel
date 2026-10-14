# -*- coding: utf-8 -*-
"""
Геометрия плитки кондиционера: залитое колесо, большая часть которого
лежит за краем плитки.

    охлаждение  колесо сверху, обод идёт из левого верхнего угла к
                правому краю; режим и вентилятор - строкой вверху справа
    нагрев      колесо справа, обод сверху вниз к правому краю; режим
                и вентилятор под цифрой уставки

Уставка - большая цифра внутри колеса прямо у своей риски. Риска стоит
на месте, под ней ездит шкала. Фактическая температура - снаружи колеса,
внизу слева над названием. От неё к уставке по ободу снаружи идёт яркий
отрезок: насколько ещё охлаждать или греть.

Всё считается здесь, в шаблон уходят готовые числа: в Jinja тригонометрия
читается плохо, а пульт тем же данным крутит шкалу при перетаскивании.
"""

import math

from .geometry import text_width

# Три точки обода (доли ширины и высоты), куда смотрит риска уставки и где
# центр блока «сейчас». Подобраны на макетах; пульт выше плитки, поэтому
# свои доли.
LAYOUT = {
    "cool": dict(pts=((0.00, -0.04), (0.40, 0.40), (1.04, 0.66)), sp=(0.66, 0.53), cur=(0.30, 0.66)),
    "heat": dict(pts=((0.26, -0.02), (0.46, 0.38), (1.04, 0.76)), sp=(0.40, 0.33), cur=(0.30, 0.66)),
}
LAYOUT_PAD = {
    "cool": dict(pts=((0.00, 0.00), (0.40, 0.42), (1.04, 0.64)), sp=(0.64, 0.52), cur=(0.31, 0.665)),
    "heat": dict(pts=((0.30, -0.02), (0.48, 0.42), (1.04, 0.80)), sp=(0.40, 0.30), cur=(0.27, 0.665)),
}

MODE_WORD = {"cool": "холод", "heat": "тепло", "dry": "сушка", "fan_only": "обдув", "auto": "авто"}
MODE_ICON = {"cool": "snow", "heat": "sun", "dry": "drop", "fan_only": "fan"}
FAN_BARS = {"low": 1, "medium": 2, "high": 3}

# Положения жалюзи по порядку на значке. Горизонтальные номера моста идут
# не слева направо - порядок проверен на живом устройстве (см. tiles.py).
SWING_V = ("upper", "position_2", "position_3", "position_4", "bottom")
SWING_H = ("position_7", "position_6", "position_1", "position_5", "position_4")
# наклон лопасти на значке, градусы
FAN5 = (-34, -17, 0, 17, 34)


def circle3(a, b, c):
    """Окружность по трём точкам: центр и радиус."""
    (x1, y1), (x2, y2), (x3, y3) = a, b, c
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    return ux, uy, math.hypot(x1 - ux, y1 - uy)


def layout_kind(mode, cur, tgt):
    """
    Какой раскладкой рисовать. Нагрев - своей, всё остальное - как
    охлаждение. Авто показывает то, что прибор сейчас делает: в комнате
    холоднее уставки - значит греет.
    """
    if mode == "heat":
        return "heat"
    if mode == "auto" and cur is not None and tgt is not None and cur < tgt:
        return "heat"
    return "cool"


def _r(v):
    return round(v, 1)


def _num(value, digits):
    if value is None:
        return "--"
    text = ("%." + str(digits) + "f") % value
    return text


def louver_icon(cx, cy, axis, value, k=1.0, r=12.0):
    """
    Стандартный значок жалюзи: веер лопастей и двойная стрелка качания.

        axis "h"  влево-вправо: вертикальные черты веером вниз, дуга под ними
        axis "v"  вверх-вниз: горизонтальные черты веером влево, дуга справа

    Зафиксировано - светится одна лопасть; качание - кружок залит,
    светится всё; выключено или неизвестно - всё приглушено.
    """
    order = SWING_H if axis == "h" else SWING_V
    value = (value or "").strip()
    swing = value == "auto"
    active = order.index(value) if value in order else None
    blades = []
    L = 3.6 * k
    for i, deg in enumerate(FAN5):
        a = math.radians(deg)
        act = swing or i == active
        if axis == "h":
            bx, by = cx + (i - 2) * 2.5 * k, cy - 3.4 * k
            dx, dy = math.sin(a) * L, math.cos(a) * L
            x1, y1, x2, y2 = bx - dx * .2, by - dy, bx + dx, by + dy * .8
        else:
            bx, by = cx - 2.4 * k, cy + (i - 2) * 2.5 * k
            dx, dy = math.cos(a) * L, math.sin(a) * L
            x1, y1, x2, y2 = bx - dx, by - dy * .8, bx + dx * .8, by + dy * .2
        blades.append({"x1": _r(x1), "y1": _r(y1), "x2": _r(x2), "y2": _r(y2),
                       "act": act, "w": round(1.25 * k * (1.4 if act and not swing else 1), 2)})
    # двойная стрелка: дуга и по два усика на концах
    if axis == "h":
        acx, acy, a0, a1 = cx, cy - 6 * k, 125.0, 55.0
    else:
        acx, acy, a0, a1 = cx - 6 * k, cy, -35.0, 35.0
    ar = 11.5 * k
    p0 = (acx + ar * math.cos(math.radians(a0)), acy + ar * math.sin(math.radians(a0)))
    p1 = (acx + ar * math.cos(math.radians(a1)), acy + ar * math.sin(math.radians(a1)))
    sweep = 1 if a1 > a0 else 0
    d = "M %.1f,%.1f A %.1f,%.1f 0 0 %d %.1f,%.1f" % (p0[0], p0[1], ar, ar, sweep, p1[0], p1[1])
    for (px, py), a, s in ((p0, a0, -1), (p1, a1, 1)):
        t = math.radians(a + 90 * s * (1 if a1 > a0 else -1)) + math.pi
        for side in (-1, 1):
            b = t + side * 0.6
            d += " M %.1f,%.1f l %.1f,%.1f" % (px, py, 3.2 * k * math.cos(b), 3.2 * k * math.sin(b))
    return {"cx": _r(cx), "cy": _r(cy), "r": _r(r * k), "swing": swing,
            "known": active is not None or swing, "blades": blades,
            "arrow": d, "sw": round(1.25 * k, 2)}


def build_disk(w, h, fs, mode, power, tgt, cur, lo, hi, fan="", quiet=False,
               swing=None, swing_h=None, pad=False, step=1.0):
    """
    Всё для отрисовки колеса в плитке w×h.

    swing / swing_h - текущие значения жалюзи или None, если таких
    контролов у прибора нет: тогда и значка нет. pad - вид для пульта:
    без значков жалюзи (они кнопками ниже) и с цифрами крупнее.
    """
    kind = layout_kind(mode, cur, tgt)
    lay = (LAYOUT_PAD if pad else LAYOUT)[kind]
    lo, hi = float(lo), float(hi)
    value = tgt if tgt is not None else (lo + hi) / 2.0
    value = max(lo, min(hi, float(value)))

    # Колесо строится в своей рамке. Обычно это вся плитка; у широкой при
    # нагреве - квадрат у правого края, иначе диск растягивался на всю
    # ширину и уставка уезжала за верх.
    bx0, bw_ = 0.0, float(w)
    if kind == "heat" and w > h * 1.4:
        bw_ = h * 1.25
        bx0 = w - bw_

    def fx(px):
        return bx0 + px * bw_

    cx, cy, R = circle3(*[(fx(px), py * h) for px, py in lay["pts"]])

    def angof(px, py):
        return math.degrees(math.atan2(py * h - cy, fx(px) - cx))

    def pol(r, a):
        a = math.radians(a)
        return cx + r * math.cos(a), cy + r * math.sin(a)

    # Теплее - к первой точке обода (влево-вверх у охлаждения, вверх у
    # нагрева): так стрелка «сейчас -> уставка» при охлаждении смотрит
    # вниз-вправо, к холоду.
    a_top, a_mid = angof(*lay["pts"][0]), angof(*lay["pts"][1])
    sgn = 1 if a_top > a_mid else -1
    per_deg = 30.0 * fs
    dpd = per_deg / R * 180.0 / math.pi * sgn
    a_sp = angof(*lay["sp"])

    def ang(t):
        return a_sp + (t - value) * dpd

    # Большая цифра: по радиусу внутрь от риски, за полосой подписей шкалы.
    big = 32.0 * fs * (1.55 if pad else 1.0)
    deg_size = big * 0.47
    num_text = _num(tgt, 0 if float(step) >= 1 else 1)
    if tgt is not None and float(step) < 1 and abs(tgt - round(tgt)) < 0.05:
        num_text = _num(tgt, 0)
    bw = text_width(num_text, big, 0.6) + deg_size * 0.5
    bh = big * 0.72
    gap = 25.0 * fs
    ux, uy = -math.cos(math.radians(a_sp)), -math.sin(math.radians(a_sp))
    ext = abs(ux) * bw / 2 + abs(uy) * bh / 2
    rpx, rpy = pol(R, a_sp)
    ncx, ncy = rpx + ux * (gap + ext), rpy + uy * (gap + ext)
    box = (ncx - bw / 2 - 3 * fs, ncy - bh / 2 - 3 * fs, ncx + bw / 2 + 3 * fs, ncy + bh / 2 + 3 * fs)

    lab_r = R - 17.0 * fs
    lab_size = 10.0 * fs
    # Обод - зубцы с прямыми шапками и круглыми желобами (макеты 14.10,
    # вариант 2). Шкала снаружи отступает на высоту зубца.
    tooth_h = 6.0 * fs
    teeth = slots_path(cx, cy, R, ang, lo - 2, hi + 2, 30.0 * fs, 7.0 * fs, tooth_h,
                       1 if dpd > 0 else -1, (0, 0, w, h))
    lift = tooth_h

    def label_hidden(lx, ly):
        m = 5.0 * fs
        inbox = box[0] - m < lx < box[2] + m and box[1] - m < ly < box[3] + m
        edge = 9.0 * fs
        return inbox or not (edge < lx < w - edge and edge < ly < h - edge)

    # Шкала - на весь диапазон, а не только видимую часть: пульт крутит её
    # целиком, и за край должны уезжать настоящие деления.
    ticks, labels = [], []
    n = int(round((hi - lo) * 2))
    for i in range(n + 1):
        t = lo + i * 0.5
        whole = abs(t - round(t)) < 0.01
        a = ang(t)
        ln = (8.0 if whole else 4.0) * fs
        po, pi_, pm, pl = pol(R + lift + ln * 0.7, a), pol(R - ln, a), pol(R, a), pol(R + lift, a)
        ticks.append({"t": t, "major": whole,
                      "ox1": _r(pl[0]), "oy1": _r(pl[1]), "ox2": _r(po[0]), "oy2": _r(po[1]),
                      "ix1": _r(pi_[0]), "iy1": _r(pi_[1]), "ix2": _r(pm[0]), "iy2": _r(pm[1]),
                      "hide": abs(t - value) < 0.01})
        if whole:
            lx, ly = pol(lab_r, a)
            rot = (a + 90.0 + 360.0) % 360.0
            if 90.0 < rot <= 270.0:
                rot -= 180.0
            labels.append({"t": t, "text": "%d" % round(t), "x": _r(lx),
                           "y": _r(ly + lab_size * 0.35), "rot": _r(rot), "ry": _r(ly),
                           "hide": abs(t - value) < 0.01 or label_hidden(lx, ly)})

    # Риска уставки: внутри длинная, указывает на цифру; снаружи - цветом плитки.
    s1, s2, s3 = pol(R - gap + 6 * fs, a_sp), pol(R, a_sp), pol(R + 11 * fs + lift, a_sp)
    mark = {"x1": _r(s1[0]), "y1": _r(s1[1]), "x2": _r(s2[0]), "y2": _r(s2[1]),
            "x3": _r(s3[0]), "y3": _r(s3[1]), "wi": _r(3.6 * fs), "wo": _r(4.4 * fs)}

    seg, cur_mark = None, None
    rr = R + 6.0 * fs + lift
    if cur is not None:
        cv = max(lo - 3, min(hi + 3, float(cur)))
        ac = ang(cv)
        q1, q2 = pol(R - 2 * fs, ac), pol(R + 12 * fs + lift, ac)
        cur_mark = {"x1": _r(q1[0]), "y1": _r(q1[1]), "x2": _r(q2[0]), "y2": _r(q2[1]),
                    "w": _r(3.2 * fs)}
        if power and tgt is not None and abs(cv - value) > 0.05:
            p0, p1 = pol(rr, ac), pol(rr, a_sp)
            seg = "M %.1f,%.1f A %.1f,%.1f 0 0 %d %.1f,%.1f" % (
                p0[0], p0[1], rr, rr, 1 if a_sp > ac else 0, p1[0], p1[1])

    # Режим и вентилятор
    word = MODE_WORD.get(mode, mode or "") if power else "выкл"
    icon = MODE_ICON.get(mode) or ("sun" if kind == "heat" else "snow")
    msize = 10.0 * fs * (1.15 if pad else 1.0)
    isize = 15.0 * fs * (1.15 if pad else 1.0)
    right = w - 16.0 * fs
    fan_text = ""
    bars = 0
    if power:
        if quiet:
            fan_text = "тихо"
        elif fan in FAN_BARS:
            bars = FAN_BARS[fan]
        elif fan == "auto":
            fan_text = "авто"
    bk = 0.85 * fs * (1.15 if pad else 1.0)
    bars_w = 16.0 * bk
    word_w = text_width(word, msize, 0.58)
    if kind == "cool":
        my = (0.09 if not pad else 0.07) * h + 4.0 * fs
        fy = my
        fx_right = right - isize - 4 * fs - word_w - 8 * fs
    else:
        my = box[3] + 15.0 * fs
        fy = my + 17.0 * fs * (1.15 if pad else 1.0)
        fx_right = right
    fan_bars = []
    if bars or fan_text == "":
        if power:
            x0 = fx_right - bars_w
            for i in range(3):
                bh_ = (5 + i * 3.5) * bk
                fan_bars.append({"x": _r(x0 + i * 6 * bk), "y": _r(fy + .5 * fs - bh_),
                                 "w": _r(3.6 * bk), "h": _r(bh_), "on": i < bars})
    modeinfo = {"word": word, "icon": icon, "x": _r(right - isize - 4 * fs), "y": _r(my),
                "size": _r(msize), "ix": _r(right - isize), "iy": _r(my - isize * 0.78),
                "isize": _r(isize), "fan_text": fan_text, "fan_x": _r(fx_right), "fan_y": _r(fy),
                "bars": fan_bars}

    # «сейчас» и фактическая температура - внизу слева над названием
    ccx, ccy = lay["cur"][0] * w, lay["cur"][1] * h
    if not pad and h > w * 1.25:
        # высокая плитка: колесо выше, блок факта - у названия
        ccy = h - 64.0 * fs
    # отступы - от кегля цифры: на пульте она крупнее, и «сейчас» над ней
    # должно отъехать вместе с ней
    cur_info = {"x": _r(ccx + big / 8), "y": _r(ccy + big * 0.5),
                "lx": _r(ccx), "ly": _r(ccy - big * 0.41),
                "size": _r(big), "deg": _r(deg_size), "dy": _r(-big * 0.44),
                "lsize": _r(9.5 * fs * (1.15 if pad else 1)),
                "text": _num(cur, 1)}

    icons = []
    if not pad:
        iy = h - 25.0 * fs
        if swing_h is not None:
            icons.append(louver_icon(w - 26 * fs, iy, "h", swing_h, fs))
        if swing is not None:
            icons.append(louver_icon(w - (55 if swing_h is not None else 26) * fs, iy, "v", swing, fs))

    return {
        "kind": kind, "teeth": teeth,
        "cx": _r(cx), "cy": _r(cy), "r": _r(R), "rr": _r(rr),
        "dpd": round(dpd, 5), "a_sp": round(a_sp, 4), "value": value,
        "lo": lo, "hi": hi, "cur": cur,
        "ticks": ticks, "labels": labels, "lab_size": _r(lab_size), "lab_r": _r(lab_r),
        "tk_w": _r(1.6 * fs), "tk_w2": _r(1.1 * fs),
        "mark": mark, "seg": seg, "seg_w": _r(6 * fs), "cur_mark": cur_mark,
        "num": {"text": num_text, "x": _r(ncx), "y": _r(ncy + bh / 2), "size": _r(big),
                "deg": _r(deg_size), "dy": _r(-big * 0.44)},
        "box": [_r(b) for b in box],
        "mode": modeinfo, "now": cur_info, "icons": icons,
        "title_size": _r(15.0 * fs * (1.25 if pad else 1.0)),
        "title_y": _r(h - 20.0 * fs),
    }


def slots_path(cx, cy, R, ang, t0, t1, per_deg, gw, th, sgn, bounds=None):
    """
    Обод с зубцами: прямые шапки на радиусе R + th, между ними круглые
    желоба до R. Желоба - между делениями шкалы (на t + 0.25), деления
    проходят над шапками. Многоугольник замыкается через центр колеса и
    ложится поверх круга радиуса R.

    bounds - (x0, y0, x1, y1): рисовать только видимое. Для пульта, где
    шкала крутится, - None: весь диапазон, иначе при повороте из-за края
    выезжал бы гладкий обод.
    """
    pts = []
    hx = gw / 2.0
    top = R + th

    def lp(a, x, r):
        a = math.radians(a + math.degrees(x / r) * sgn)
        return cx + r * math.cos(a), cy + r * math.sin(a)

    t = t0 + 0.25
    while t <= t1 + 1e-6:
        a = ang(t)
        if bounds:
            px, py = cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a))
            if not (bounds[0] - 80 < px < bounds[2] + 80 and bounds[1] - 80 < py < bounds[3] + 80):
                t += 0.5
                continue
        pts.append(lp(a, -per_deg / 4.0, top))
        pts.append(lp(a, -hx, top))
        pts.append(lp(a, -hx, R + hx))
        for j in range(1, 12):
            f = math.pi * j / 12
            pts.append(lp(a, -hx * math.cos(f), R + hx - hx * math.sin(f)))
        pts.append(lp(a, hx, R + hx))
        pts.append(lp(a, hx, top))
        t += 0.5
    if not pts:
        return None
    return "M %.1f,%.1f L " % (cx, cy) + " L ".join("%.1f,%.1f" % p for p in pts) + " Z"


def gear_path(cx, cy, r, fs):
    """Малое колесо жалюзи: тот же обод с желобами, по кругу."""
    tw, tth = 7.0 * fs, 6.0 * fs
    n = max(18, int(round(2 * math.pi * r / (16.0 * fs))))
    hw = tw / 2.0 / r
    pts = []
    for k in range(n):
        a0 = 2 * math.pi * k / n
        pts.append((cx + r * math.cos(a0 - hw), cy + r * math.sin(a0 - hw)))
        for f in range(9):
            ff = math.pi * f / 8
            x = -tw / 2 * math.cos(ff)
            rad = (r - tth + tw / 2) - (tw / 2) * math.sin(ff)
            aa = a0 + x / rad
            pts.append((cx + rad * math.cos(aa), cy + rad * math.sin(aa)))
        pts.append((cx + r * math.cos(a0 + hw), cy + r * math.sin(a0 + hw)))
        am = a0 + math.pi / n
        pts.append((cx + r * math.cos(am), cy + r * math.sin(am)))
    return "M " + " L ".join("%.1f,%.1f" % p for p in pts) + " Z"


# ---------------------------------------------------------------- пульт на весь экран

#: Раскладки пульта на весь экран: собраны в конструкторе 14.10 («Вариант 5»)
#: и выровнены - «назад» и питание одного размера, 12 px от краёв.
#: x, y, cx, cy - доли ширины и высоты экрана; r - доля меньшей стороны;
#: размеры - в единицах плитки (умножаются на fs); sp - угол риски уставки
#: в охлаждении и нагреве. W×H - экран, на котором раскладку собирали;
#: на нём единица плитки равна fs пикселям.
SCREEN = {
    "portrait": {
        "W": 390.0, "H": 844.0, "fs": 2.0,
        "wheel": {"cx": 1.6824, "cy": -0.0126, "r": 1.4235, "dir": 1, "num": 32, "gap": 25, "lab": 10, "step": 30},
        "sp": {"cool": 138.8, "heat": 164.0},
        "mode": {"x": 0.928, "y": 0.058, "anchor": "end", "size": 14.5},
        "now": {"x": 0.0308, "y": 0.449, "anchor": "start", "size": 30},
        "name": {"x": 0.0308, "y": 0.365, "anchor": "start", "size": 11},
        "back": {"x": 0.0872, "y": 0.0403, "size": 11},
        "power": {"x": 0.0872, "y": 0.1066, "size": 11},
        "vdial": {"x": 0.059, "y": 0.593, "r": 0.27, "dir": "right", "len": 0.6, "arrows": "out"},
        "hdial": {"x": 0.698, "y": 0.818, "r": 0.27, "dir": "up", "len": 0.6, "arrows": "in"},
        "fan": {"r": 0.04},
    },
    "landscape": {
        "W": 844.0, "H": 390.0, "fs": 2.0,
        "wheel": {"cx": 0.0667, "cy": -1.4802, "r": 2.2211, "dir": -1, "num": 47, "gap": 25, "lab": 11, "step": 30},
        "sp": {"cool": 78.3, "heat": 58.9},
        "mode": {"x": 0.0806, "y": 0.163, "anchor": "start", "size": 15.5, "inline": True},
        "now": {"x": 0.58, "y": 0.921, "anchor": "end", "size": 36},
        "name": {"x": 0.0142, "y": 0.916, "anchor": "start", "size": 14},
        "back": {"x": 0.0403, "y": 0.0872, "size": 11},
        "power": {"x": 0.9597, "y": 0.0872, "size": 11},
        "vdial": {"x": 0.982, "y": 0.478, "r": 0.22, "dir": "left", "len": 0.6, "arrows": "out"},
        "hdial": {"x": 0.747, "y": 1.0, "r": 0.22, "dir": "up", "len": 0.6, "arrows": "in"},
        "fan": {"r": 0.04},
    },
    "square": {
        "W": 600.0, "H": 600.0, "fs": 2.3,
        "wheel": {"cx": 1.3651, "cy": -0.2548, "r": 1.103, "dir": 1, "num": 41, "gap": 25, "lab": 10, "step": 38},
        # в нагреве риска чуть ниже, чем в конструкторе: цифра упиралась в режим
        "sp": {"cool": 130.0, "heat": 143.0},
        "mode": {"x": 0.94, "y": 0.101, "anchor": "end", "size": 13.5},
        "now": {"x": 0.02, "y": 0.353, "anchor": "start", "size": 30},
        "name": {"x": 0.02, "y": 0.93, "anchor": "start", "size": 11},
        "back": {"x": 0.0622, "y": 0.0622, "size": 11},
        "power": {"x": 0.1665, "y": 0.0622, "size": 11},
        "vdial": {"x": 0.039, "y": 0.609, "r": 0.18, "dir": "right", "len": 0.6, "arrows": "out"},
        "hdial": {"x": 0.538, "y": 0.963, "r": 0.18, "dir": "up", "len": 0.6, "arrows": "in"},
        "fan": {"r": 0.04},
    },
}

#: Скорости вентилятора по кольцам: первое кольцо - low, третье - high.
FAN_RING = {"low": 1, "medium": 2, "high": 3}
#: Сколько секунд на оборот пропеллера при такой скорости.
FAN_SPIN = {1: 2.4, 2: 1.3, 3: 0.7, "auto": 1.3, "quiet": 3.2}
#: Направления жалюзи на малых колёсах: наклон от оси колеса, градусы.
DIAL_V = (-38, -19, 0, 19, 38)
DIAL_H = (-42, -21, 0, 21, 42)
DIAL_BASE = {"right": 0.0, "left": 180.0, "up": -90.0, "down": 90.0}
MODES = (("cool", "холод"), ("heat", "тепло"), ("dry", "сушка"), ("fan_only", "обдув"), ("auto", "авто"))


def screen_kind(w, h):
    # Ближайшая по пропорциям раскладка: граница - среднее геометрическое
    # соседних (стоя 0.46, квадрат 1, на боку 2.16). Планшет 4 : 3 и окно
    # браузера 16 : 10 так попадают в квадрат и «на боку».
    ar = float(w) / float(h)
    if ar < 0.68:
        return "portrait"
    if ar > 1.47:
        return "landscape"
    return "square"


def _num_place(cx, cy, R, a_sp, size, gap, text):
    """Центр и рамка большой цифры: по радиусу внутрь от риски."""
    bw = text_width(text, size, 0.6) + size * 0.25
    bh = size * 0.72
    ux, uy = -math.cos(math.radians(a_sp)), -math.sin(math.radians(a_sp))
    ext = abs(ux) * bw / 2 + abs(uy) * bh / 2
    px, py = cx + R * math.cos(math.radians(a_sp)), cy + R * math.sin(math.radians(a_sp))
    return px + ux * (gap + ext), py + uy * (gap + ext), bw, bh


def build_screen(w, h, mode, power, tgt, cur, lo, hi, fan="", quiet=False,
                 swing=None, swing_h=None, step=1.0):
    """
    Пульт кондиционера на весь экран w×h: колесо уставки с зубчатым ободом,
    вентилятор кольцами на месте уставки другого режима, два малых колеса
    жалюзи, «назад» и питание. Раскладка - по форме экрана (SCREEN).
    Всё в пикселях: шаблон только рисует, пульт крутит.
    """
    orient = screen_kind(w, h)
    L = SCREEN[orient]
    # Раскладка собрана на экране W×H. На другом она масштабируется целиком
    # (k - чтобы влезла), а каждый элемент держится за ближние края:
    # левые - за левый, нижние - за нижний. Лишнее место уходит в середину,
    # а не растаскивает элементы: на iPhone с панелями Safari экран ниже,
    # чем в конструкторе, и колёса наезжали друг на друга.
    k = min(w / L["W"], h / L["H"])
    M = min(L["W"], L["H"]) * k
    fs = L["fs"] * k

    # Элемент посередине (доля 0.35-0.65) держится за середину экрана:
    # на планшете 4 : 3 малое колесо у нижнего края иначе уезжало вправо
    # вместе с большим и упиралось в его зубцы.
    def _at(f, ref, size):
        if f < 0.35:
            return f * ref * k
        if f > 0.65:
            return size - (1.0 - f) * ref * k
        return size / 2.0 + (f - 0.5) * ref * k

    def X(f):
        return _at(f, L["W"], w)

    def Y(f):
        return _at(f, L["H"], h)
    kind = layout_kind(mode, cur, tgt)
    other = "heat" if kind == "cool" else "cool"
    lo, hi = float(lo), float(hi)
    value = tgt if tgt is not None else (lo + hi) / 2.0
    value = max(lo, min(hi, float(value)))
    no_target = mode == "fan_only"

    wl = L["wheel"]
    cx, cy, R = X(wl["cx"]), Y(wl["cy"]), wl["r"] * M
    per_deg = wl["step"] * fs
    dpd = per_deg / R * 180.0 / math.pi * wl["dir"]
    a_sp = L["sp"][kind]

    def pol(r, a):
        a = math.radians(a)
        return cx + r * math.cos(a), cy + r * math.sin(a)

    def ang(t):
        return a_sp + (t - value) * dpd

    tooth_h = 6.0 * fs
    lift = tooth_h
    teeth = slots_path(cx, cy, R, ang, lo - 3, hi + 3, per_deg, 7.0 * fs, tooth_h,
                       1 if dpd > 0 else -1)

    num_size = wl["num"] * fs
    gap = wl["gap"] * fs
    digits = 0 if float(step) >= 1 else 1
    num_text = "—" if no_target else _num(tgt, digits)
    if tgt is not None and digits and abs(tgt - round(tgt)) < 0.05:
        num_text = _num(tgt, 0)
    ncx, ncy, bw, bh = _num_place(cx, cy, R, a_sp, num_size, gap, "24")
    box = (ncx - bw / 2 - 3 * fs, ncy - bh / 2 - 3 * fs, ncx + bw / 2 + 3 * fs, ncy + bh / 2 + 3 * fs)
    lab_r = R - 17.0 * fs
    lab_size = wl["lab"] * fs

    def label_hidden(lx, ly):
        m = 5.0 * fs
        inbox = box[0] - m < lx < box[2] + m and box[1] - m < ly < box[3] + m
        return inbox or not (9 < lx < w - 9 and 9 < ly < h - 9)

    ticks, labels = [], []
    n = int(round((hi - lo + 6) * 2))
    for i in range(n + 1):
        t = lo - 3 + i * 0.5
        whole = abs(t - round(t)) < 0.01
        a = ang(t)
        ln = (8.0 if whole else 4.0) * fs
        pl, po, pi_, pm = pol(R + lift, a), pol(R + lift + ln * 0.7, a), pol(R - ln, a), pol(R, a)
        ticks.append({"t": t, "major": whole,
                      "ox1": _r(pl[0]), "oy1": _r(pl[1]), "ox2": _r(po[0]), "oy2": _r(po[1]),
                      "ix1": _r(pi_[0]), "iy1": _r(pi_[1]), "ix2": _r(pm[0]), "iy2": _r(pm[1]),
                      "hide": (abs(t - value) < 0.01 and not no_target) or t < lo or t > hi})
        if whole and lo <= t <= hi:
            lx, ly = pol(lab_r, a)
            rot = (a + 90.0 + 360.0) % 360.0
            if 90.0 < rot <= 270.0:
                rot -= 180.0
            labels.append({"t": t, "text": "%d" % round(t), "x": _r(lx), "y": _r(ly), "rot": _r(rot),
                           "hide": (abs(t - value) < 0.01 and not no_target) or label_hidden(lx, ly)})

    s1, s2, s3 = pol(R - gap + 6 * fs, a_sp), pol(R, a_sp), pol(R + 11 * fs + lift, a_sp)
    mark = {"x1": _r(s1[0]), "y1": _r(s1[1]), "x2": _r(s2[0]), "y2": _r(s2[1]),
            "x3": _r(s3[0]), "y3": _r(s3[1]), "wi": _r(3.6 * fs), "wo": _r(4.4 * fs)}
    rr = R + 6.0 * fs + lift
    seg, cur_mark = None, None
    if cur is not None and not no_target:
        cv = max(lo - 3, min(hi + 3, float(cur)))
        ac = ang(cv)
        q1, q2 = pol(R - 2 * fs, ac), pol(R + 12 * fs + lift, ac)
        cur_mark = {"x1": _r(q1[0]), "y1": _r(q1[1]), "x2": _r(q2[0]), "y2": _r(q2[1]), "w": _r(3.2 * fs)}
        if power and abs(cv - value) > 0.05:
            p0, p1 = pol(rr, ac), pol(rr, a_sp)
            seg = "M %.1f,%.1f A %.1f,%.1f 0 0 %d %.1f,%.1f" % (
                p0[0], p0[1], rr, rr, 1 if a_sp > ac else 0, p1[0], p1[1])

    def in_disk(x, y):
        return math.hypot(x - cx, y - cy) < R

    # режим: слово и значок; нажатие открывает выбор
    md = L["mode"]
    ms = md["size"] * fs
    mx, my = X(md["x"]), Y(md["y"])
    word = MODE_WORD.get(mode, mode or "") if power else "выкл"
    icon = MODE_ICON.get(mode) or ("sun" if kind == "heat" else "snow")
    isz = ms * 1.35
    word_w = text_width(word, ms, 0.58)
    if md["anchor"] == "end":
        ix, wx = mx - isz, mx - isz - ms * 0.45
        hit = (wx - word_w - 6, my - isz - 6, mx + 6, my + ms * 0.4)
    else:
        ix, wx = mx, mx + isz + ms * 0.45
        hit = (mx - 6, my - isz - 6, wx + word_w + 6, my + ms * 0.4)
    mode_box = (hit[0], hit[1], hit[2], hit[3])
    mode_info = {"word": word, "icon": icon, "x": _r(wx), "y": _r(my), "size": _r(ms),
                 "anchor": md["anchor"], "ix": _r(ix), "iy": _r(my - isz * 0.8), "isize": _r(isz),
                 "inside": in_disk(mx, my),
                 "hit": [_r(v) for v in (hit[0], hit[1], hit[2] - hit[0], hit[3] - hit[1])]}

    # вентилятор: на месте цифры уставки другого режима
    fx, fy, _, _ = _num_place(cx, cy, R, L["sp"][other], num_size, gap, "24")
    fr = L["fan"]["r"] * M
    rg = fr * 0.62
    # Кольца не должны наезжать на слово режима и на свою цифру уставки
    # (на квадрате место уставки нагрева - у самого режима): задевают -
    # вентилятор отталкивается от того, что задел, и не уходит за экран.
    outer = fr + 3 * rg + 4 * fs

    def _push(x, y, b):
        nx, ny = max(b[0], min(b[2], x)), max(b[1], min(b[3], y))
        d = math.hypot(x - nx, y - ny)
        if d >= outer:
            return None
        if d < 1e-6:
            return (0.0, 1.0)
        return ((x - nx) / d, (y - ny) / d)

    # и на подписи шкалы: кольца целиком внутри, ближе к центру подписей
    rim_in = R - 17 * fs - wl["lab"] * fs * 0.9 - outer

    def _rim(x, y):
        d = math.hypot(x - cx, y - cy)
        if d <= rim_in or d < 1e-6:
            return None
        return ((cx - x) / d, (cy - y) / d)

    for _ in range(160):
        v = _push(fx, fy, mode_box) or _push(fx, fy, box) or _rim(fx, fy)
        if not v:
            break
        fx, fy = fx + v[0] * 3 * fs, fy + v[1] * 3 * fs
        fx = max(outer, min(w - outer, fx))
        fy = max(outer, min(h - outer, fy))
    ring = FAN_RING.get(fan, 0)
    fstate = "quiet" if quiet else ("auto" if fan == "auto" or not ring else ring)
    blades = " ".join(
        "M 0,0 C %.1f,%.1f %.1f,%.1f %.1f,%.1f C %.1f,%.1f %.1f,%.1f 0,0 Z" % tuple(
            v for p in [(fr * .18, -fr * .22), (fr * .62, -fr * .3), (fr * .72, -fr * .06),
                        (fr * .6, fr * .14), (fr * .3, fr * .12)]
            for v in _rot(p, k * 120))
        for k in range(3))
    fan_info = {"x": _r(fx), "y": _r(fy), "r": _r(fr), "rg": _r(rg), "inside": in_disk(fx, fy),
                "rings": [{"r": _r(fr + i * rg), "i": i} for i in (1, 2, 3)],
                "ring": ring if power and not quiet and fan != "auto" else 0,
                "state": fstate, "blades": blades, "hub": _r(fr * 0.14), "hubw": _r(fr * 0.06),
                "spin": FAN_SPIN.get(fstate, 1.3) if power else 0,
                "badge": _r(fr * 0.48), "bx": _r(fx + fr * 0.85), "by": _r(fy + fr * 0.85),
                "hit": _r(fr + 3 * rg + 8 * fs), "sw": _r(1.4 * fs), "sw_on": _r(2.6 * fs)}

    def dial(key, value_now, order, degs):
        d = L[key]
        if value_now is None:
            return None
        dx, dy, r = X(d["x"]), Y(d["y"]), d["r"] * M
        # «A» в центре должна быть видна целиком: колесо у края экрана
        # отодвигается ровно настолько, чтобы ступица не уходила за край
        # (и снизу не попадала в полосу жестов телефона)
        edge = r * 0.3 + 4 * fs
        dx, dy = max(edge, min(w - edge, dx)), max(edge, min(h - edge, dy))
        base = DIAL_BASE[d["dir"]]
        mirror = -1 if d["dir"] in ("left", "down") else 1
        auto = (value_now or "").strip() == "auto"
        active = order.index(value_now) if value_now in order else (2 if not auto else None)
        angs = [base + g * mirror for g in degs]
        arrows = []
        r1, r2 = r + 10 * fs, r + r * d["len"] * 0.9
        for i, a in enumerate(angs):
            A = math.radians(a)
            x1, y1 = dx + r1 * math.cos(A), dy + r1 * math.sin(A)
            x2, y2 = dx + r2 * math.cos(A), dy + r2 * math.sin(A)
            if d["arrows"] == "in":
                x1, y1, x2, y2 = x2, y2, x1, y1
                A += math.pi
            head = []
            for sd in (-1, 1):
                b = A + math.pi + sd * 0.55
                head.append((x2 + 5 * fs * math.cos(b), y2 + 5 * fs * math.sin(b)))
            arrows.append({"d": "M %.1f,%.1f L %.1f,%.1f M %.1f,%.1f L %.1f,%.1f L %.1f,%.1f" % (
                x1, y1, x2, y2, head[0][0], head[0][1], x2, y2, head[1][0], head[1][1]),
                "on": auto or i == active, "i": i})
        hub = r * 0.3
        pa = math.radians(angs[active if active is not None else 2])
        return {"x": _r(dx), "y": _r(dy), "r": _r(r), "hub": _r(hub), "gear": gear_path(dx, dy, r, fs),
                "auto": auto, "active": active if active is not None else -1,
                "angs": [_r(a) for a in angs], "vals": list(order), "arrows": arrows,
                "p1": _r(hub), "p2": _r(r - 6 * fs - 6),
                "px1": _r(dx + math.cos(pa) * hub), "py1": _r(dy + math.sin(pa) * hub),
                "px2": _r(dx + math.cos(pa) * (r - 6 * fs - 6)), "py2": _r(dy + math.sin(pa) * (r - 6 * fs - 6)),
                "pw": _r(5 * fs), "aw": _r(2.2 * fs), "aw_on": _r(4 * fs), "rim": _r(1.6 * fs),
                "hit": _r(r + r * d["len"] + 6 * fs), "font": _r(hub * 0.95)}

    def text_block(key):
        b = L[key]
        x, y = X(b["x"]), Y(b["y"])
        return {"x": _r(x), "y": _r(y), "size": _r(b["size"] * fs), "anchor": b["anchor"],
                "inside": in_disk(x, y)}

    def button(key):
        b = L[key]
        return {"x": _r(X(b["x"])), "y": _r(Y(b["y"])), "r": _r(b["size"] * fs)}

    now = text_block("now")
    now.update({"label_y": _r(now["y"] - now["size"] * 0.82), "label_size": _r(now["size"] * 0.3),
                "deg": _r(now["size"] * 0.47), "dy": _r(-now["size"] * 0.44), "text": _num(cur, 1)})

    # выбор режима - шторка у верхнего края
    # Высота кнопки считается от значка и подписи: на планшете значок
    # упирался в ширину кнопки, подпись - нет, и они наезжали друг на друга.
    cw = (w - 24 - 4 * 6) / 5.0
    ch_isz = min(cw * .28, 16 * fs)
    ch_font = min(12 * fs, cw * .2)
    ch_pad = max(5.0, ch_isz * .22)
    ch_pill = ch_pad * 2 + ch_isz + ch_font * 1.15
    ch_h = ch_pill + 6
    ch_iy = 12 + ch_pad + ch_isz / 2               # центр значка
    ch_ty = 12 + ch_pad + ch_isz + ch_font * 0.95  # базовая линия подписи
    chooser = []
    for i, (key, nm) in enumerate(MODES):
        x = 12 + i * (cw + 6)
        chooser.append({"key": key, "word": nm, "icon": MODE_ICON.get(key) or "snow",
                        "x": _r(x), "w": _r(cw), "on": key == mode,
                        "ix": _r(x + cw / 2 - ch_isz / 2),
                        "isize": _r(ch_isz), "tx": _r(x + cw / 2)})

    return {
        "orient": orient, "kind": kind, "w": w, "h": h, "fs": _r(fs),
        "cx": _r(cx), "cy": _r(cy), "r": _r(R), "rr": _r(rr), "teeth": teeth, "lift": _r(lift),
        "dpd": round(dpd, 5), "a_sp": round(a_sp, 4), "value": value, "lo": lo, "hi": hi, "cur": cur,
        "no_target": no_target,
        "ticks": ticks, "labels": labels, "lab_size": _r(lab_size), "lab_r": _r(lab_r),
        "tk_w": _r(1.6 * fs), "tk_w2": _r(1.1 * fs),
        "mark": mark, "seg": seg, "seg_w": _r(6 * fs), "cur_mark": cur_mark,
        "num": {"text": num_text, "x": _r(ncx), "y": _r(ncy + bh / 2), "size": _r(num_size),
                "deg": _r(num_size * 0.47), "dy": _r(-num_size * 0.44)},
        "box": [_r(b) for b in box],
        "mode": mode_info, "fan": fan_info,
        "vdial": dial("vdial", swing, SWING_V, DIAL_V),
        "hdial": dial("hdial", swing_h, SWING_H, DIAL_H),
        "now": now, "name": text_block("name"),
        "back": button("back"), "power": button("power"),
        "chooser": chooser, "ch_h": _r(ch_h), "ch_y": 12, "ch_pill": _r(ch_pill),
        "ch_iy": _r(ch_iy), "ch_ty": _r(ch_ty), "ch_font": _r(ch_font), "sh_blur": _r(5 * fs), "sh_key": _r(.7 * fs),
        "sh_dy": _r(5 * fs), "sh_dy2": _r(1.25 * fs),
    }


def _rot(p, deg):
    a = math.radians(deg)
    x, y = p
    return (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))
