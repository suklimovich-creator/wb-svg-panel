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
SWING_H = ("position_4", "position_5", "position_1", "position_6", "position_7")
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
        po, pi_, pm = pol(R + ln * 0.7, a), pol(R - ln, a), pol(R, a)
        ticks.append({"t": t, "major": whole,
                      "ox1": _r(pm[0]), "oy1": _r(pm[1]), "ox2": _r(po[0]), "oy2": _r(po[1]),
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
    s1, s2, s3 = pol(R - gap + 6 * fs, a_sp), pol(R, a_sp), pol(R + 11 * fs, a_sp)
    mark = {"x1": _r(s1[0]), "y1": _r(s1[1]), "x2": _r(s2[0]), "y2": _r(s2[1]),
            "x3": _r(s3[0]), "y3": _r(s3[1]), "wi": _r(3.6 * fs), "wo": _r(4.4 * fs)}

    seg, cur_mark = None, None
    rr = R + 4.0 * fs
    if cur is not None:
        cv = max(lo - 3, min(hi + 3, float(cur)))
        ac = ang(cv)
        q1, q2 = pol(R - 2 * fs, ac), pol(R + 12 * fs, ac)
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
        "kind": kind,
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
