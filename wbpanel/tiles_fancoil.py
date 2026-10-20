# -*- coding: utf-8 -*-
"""
Фанкойл: канальный термостат BAC-1000 и родня.

Почему это термостат, а не кондиционер
--------------------------------------
Прибор сравнивает комнатную температуру с уставкой и закрывает разрыв
между ними - хоть нагревом, хоть охлаждением. Это ровно та логика, под
которую сделан циферблат термостата: дуга со шкалой, две засечки - факт
и цель, закрашен участок между ними. Дошёл до уставки - закраска исчезла,
и это видно на дуге, без единого слова. Колесо кондиционера так не умеет:
оно показывает уставку и ничего не говорит о том, далеко ли до неё.

Что прибор умеет
----------------
Режимов три - нагрев, охлаждение, вентиляция; «авто», как у сплита, нет
вовсе. В вентиляции клапан закрыт всегда, вентилятор крутится: это продув,
а не климат.

Скоростей четыре: авто, высокая, средняя, низкая. В авто прибор выбирает
скорость сам по разнице температур, поэтому «просили» и «крутится» -
разные каналы с разными таблицами. У цели ноль означает «авто», у факта
такого значения нет, зато есть «стоит».

Дойдя до уставки, прибор закрывает клапан. Что дальше - зависит от
исполнения: A1 глушит и вентилятор, A2 оставляет его на низкой скорости.
Закрытый клапан при работающем вентиляторе - норма, а не сбой.

Решения, которые из этого следуют
---------------------------------
    рамка горит по работе оборудования, а не по клапану
        клапан - это подача воды, а не работа: он открыт и у прибора,
        который только что включился и ещё ничего не сделал. Работает
        фанкойл тогда, когда крутится вентилятор;

    режим, скорость и клапан вынесены в колонку справа
        на широкой плитке дуга остаётся окружностью и смещается влево,
        а освободившееся место занимают три строки состояния. Строкой
        внизу это не передать: «охлаждает · авто (2) · клапан» никто не
        прочитает;

    короткое нажатие раскрывает плитку на весь экран
        фанкойл не выключают промахом по экрану. Питание живёт на пульте,
        который открывается долгим нажатием;

    все значения - таблицы в конфиге
        режимы и скорости у каждого прибора свои и почти всегда числовые.
        Умолчания описывают BAC-1000, чужой прибор описывается в YAML без
        единой строки Python.

Конфиг
------

    - type: fancoil
      title: "Фанкойл"
      w: 2
      channel_switch:    "thermostat-interface-v1-0_1/power"
      channel_mode:      "thermostat-interface-v1-0_1/operating_mode"
      channel_fan:       "thermostat-interface-v1-0_1/fan_speed"
      channel_fan_state: "thermostat-interface-v1-0_1/fan_speed_status"
      channel_target:    "thermostat-interface-v1-0_1/set_temperature"
      channel_current:   "thermostat-interface-v1-0_1/room_temperature"
      channel_state:     "thermostat-interface-v1-0_1/valve"
      scale_min: 14      # пределы ШКАЛЫ на дуге
      scale_max: 30
      temp_min: 16       # пределы УСТАВКИ на пульте
      temp_max: 30

Пределы уставки задаются здесь нарочно: в meta канала у BAC-1000 стоит
max 65535, и шкалу по нему не построить. Сам прибор принимает от 5 до 35.

Другой прибор - свои таблицы:

      modes:
        0: {title: "Охлаждение", icon: snow, verb: "Охлаждает", cool: true}
        3: {title: "Осушение", icon: drop}
      fans:
        0: {title: "Авто", auto: true}
        1: {title: "3", level: 3}
      fan_states:
        4: {title: "стоит", off: true}
"""

import math

from .geometry import _fmt, text_width
from .registry import Tile, Zone, tile
from .tiles import DIAL_START, DIAL_SWEEP, dial_arc, dial_point


#: Режимы BAC-1000. Порядок тот же, в каком кнопки встанут на пульте.
#: valve - участвует ли в режиме клапан (в вентиляции он закрыт всегда),
#: cool - дуга красится синим: прибору идти вниз, а не вверх.
FANCOIL_MODES = (
    (0, {"title": "Охлаждение", "short": "холод", "icon": "snow", "verb": "Охлаждает",
         "cool": True}),
    (1, {"title": "Нагрев", "short": "тепло", "icon": "heat", "verb": "Греет"}),
    (2, {"title": "Вентиляция", "short": "обдув", "icon": "fan", "verb": "Продув",
         "valve": False}),
)

#: Скорость - ЦЕЛЬ. Единица это максимум, поэтому level идёт наоборот.
FANCOIL_FANS = (
    (0, {"title": "Авто", "auto": True}),
    (3, {"title": "1", "level": 1}),
    (2, {"title": "2", "level": 2}),
    (1, {"title": "3", "level": 3}),
)

#: Скорость - ФАКТ. Другая таблица: нуля нет, четвёрка означает «стоит».
FANCOIL_FAN_STATES = (
    (1, {"title": "3", "level": 3}),
    (2, {"title": "2", "level": 2}),
    (3, {"title": "1", "level": 1}),
    (4, {"title": "стоит", "off": True}),
)

#: Сколько градусов считаем «дошёл»: у прибора свой гистерезис, и
#: закраска дуги в полградуса всё равно неразличима.
REACHED = 0.3


def key(value):
    """
    Ключ таблицы из того, что пришло.

    Канал отдаёт «1», конфиг - число 1, а иной прибор пришлёт «1.0». Без
    приведения кнопка режима молча не подсветится, и искать это потом долго.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def table(raw, default):
    """
    Таблица значений -> [(ключ, {title, icon, …}), ...] в порядке конфига.

    Понимает три записи, потому что в живых конфигах встречаются все три:

        {0: "Охлаждение"}                        коротко
        {0: {title: "Охлаждение", icon: snow}}   с подробностями
        [{value: 0, title: "Охлаждение"}]        списком, когда важен порядок
    """
    if not raw:
        return [(key(value), dict(body)) for value, body in default]

    if isinstance(raw, dict):
        items = sorted(raw.items(), key=lambda pair: _order(pair[0]))
    else:
        items = [(item.get("value"), item) for item in raw
                 if isinstance(item, dict)]

    out = []
    for value, body in items:
        if isinstance(body, dict):
            body = dict(body)
            body.setdefault("title", str(value))
        else:
            body = {"title": str(body)}
        out.append((key(value), body))
    return out or [(key(value), dict(body)) for value, body in default]


def _order(value):
    try:
        return (0, float(value), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(value))


def find(items, value):
    """Запись таблицы по значению канала. Не нашли - пустая, без падения."""
    for row_key, body in items:
        if row_key == value:
            return body
    return {}


def buttons(items):
    """Таблица -> "0|Охлаждение;1|Нагрев" для пульта."""
    return ";".join("%s|%s" % (row_key, body.get("title", row_key))
                    for row_key, body in items if row_key != "")


def build_gauge(width, height, lo, hi, current, target, cx):
    """
    Циферблат: незамкнутая дуга со шкалой lo…hi, закрашен разрыв между
    фактом и уставкой - то есть ровно то, что прибору предстоит закрыть.

    Тот же циферблат, что у термостата, но центр задаётся снаружи: на
    широкой плитке он уезжает влево, освобождая место колонке состояний.
    Радиус считается от меньшей стороны, поэтому дуга остаётся
    окружностью и не растягивается в овал.
    """
    span = max(float(hi) - float(lo), 1e-6)
    cy = height * 0.59
    r = min(width, height) * 0.36

    def deg(value):
        f = (float(value) - float(lo)) / span
        return DIAL_START + max(0.0, min(1.0, f)) * DIAL_SWEEP

    out = {"cx": round(cx, 1), "cy": round(cy, 1), "r": round(r, 1),
           "track": dial_arc(cx, cy, r, DIAL_START, DIAL_START + DIAL_SWEEP),
           "fill": "", "marks": []}

    pts = [v for v in (current, target) if v is not None]
    if len(pts) == 2:
        a, b = sorted(deg(v) for v in pts)
        out["fill"] = dial_arc(cx, cy, r, a, b)
    elif len(pts) == 1:
        out["fill"] = dial_arc(cx, cy, r, DIAL_START, deg(pts[0]))

    for value, kind in ((current, "current"), (target, "target")):
        if value is None:
            continue
        d = deg(value)
        inner = 0.80 if kind == "target" else 0.86
        x0, y0 = dial_point(cx, cy, r * inner, d)
        x1, y1 = dial_point(cx, cy, r * 1.20, d)
        out["marks"].append({"kind": kind,
                             "x1": round(x0, 1), "y1": round(y0, 1),
                             "x2": round(x1, 1), "y2": round(y1, 1)})
    return out


# ---------------------------------------------------------------------------
# Фанкойл «пультом» (с 1.37) - широкая плитка как настенный прибор.
#
# Слева ползунок-фейдер уставки: колпачок стоит на уставке, в прорези цветом
# режима залито «сейчас -> уставка». В середине квадратная решётка, за ней
# пропеллер (крутится, пока крутится вентилятор), под ней табло с уставкой
# сегментными цифрами. Справа «сейчас», клавиши скорости как у старого
# вентилятора (нажатая утоплена, её светодиод горит; «0» - выключен), внизу
# режим и клапан. Рисуется в своей рамке 354×170 и масштабируется в плитку.
# ---------------------------------------------------------------------------

PANEL_W, PANEL_H = 354.0, 170.0
SLIDER_X, SLIDER_TOP, SLIDER_BOT = 46.0, 22.0, 148.0
GRILLE_W, RIGHT_X = 90.0, 202.0
GRILLE_X = round((SLIDER_X + 20 + RIGHT_X - 10) / 2 - GRILLE_W / 2)

#: Сегменты цифр табло: какие горят у какой цифры.
SEG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
       "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcfgd", "-": "g"}


def seven_seg(text, x, y, w, h, t, gap):
    """
    Сегментные цифры, как на табло настенного термостата: все семь сегментов
    каждой цифры, у каждого - горит ли. Незажжённые рисуются бледно - так
    табло выглядит табло. Наклон - как у настоящих индикаторов.
    """
    hh, k = h / 2.0, t / 2.0
    polys, dots = [], []

    def sk(p):
        return "%.1f,%.1f" % (p[0] + (y + h - p[1]) * 0.1, p[1])

    def hs(cx, cy):
        return [(cx - w / 2 + k, cy), (cx - w / 2 + t, cy - k), (cx + w / 2 - t, cy - k),
                (cx + w / 2 - k, cy), (cx + w / 2 - t, cy + k), (cx - w / 2 + t, cy + k)]

    def vs(cx, cy):
        return [(cx, cy - hh / 2 + k), (cx + k, cy - hh / 2 + t), (cx + k, cy + hh / 2 - t),
                (cx, cy + hh / 2 - k), (cx - k, cy + hh / 2 - t), (cx - k, cy - hh / 2 + t)]

    for ch in text:
        if ch == ".":
            dots.append({"x": round(x - gap / 2.0 + 1, 1), "y": round(y + h, 1), "r": round(t * .62, 1)})
            x += 5
            continue
        cx = x + w / 2.0
        shapes = {"a": hs(cx, y), "g": hs(cx, y + hh), "d": hs(cx, y + h),
                  "f": vs(x, y + hh / 2), "b": vs(x + w, y + hh / 2),
                  "e": vs(x, y + hh * 1.5), "c": vs(x + w, y + hh * 1.5)}
        for name in "abcdefg":
            polys.append({"p": " ".join(sk(q) for q in shapes[name]),
                          "on": name in SEG.get(ch, "")})
        x += w + gap
    return polys, dots


def _fc_blade(cx, cy, r, base):
    def p(rr, d):
        a = base + d
        return "%.1f,%.1f" % (cx + rr * math.cos(a), cy + rr * math.sin(a))
    return "M%s Q%s %s Q%s %sZ" % (p(8, -0.25), p(r * 0.8, -0.1), p(r, 0.4), p(r * 0.6, 0.85), p(8, 0.6))


def lcd_text(target):
    """Строка табло: уставка с шагом 0.5, «--.-» без данных."""
    if target is None:
        return "--.-"
    if float(target) >= 99.95:
        return "%.0f" % float(target)
    return "%.1f" % (round(float(target) * 2) / 2.0)


def lcd_block(x, y, w, h, target, dh_max, level=None, auto=False, deg=True):
    """
    Табло уставки в рамке x, y, w, h: сегментные цифры по центру рамки и «°C».
    Высота цифр - по высоте рамки, но не больше dh_max и не шире рамки: три
    цифры, точка, наклон и «°C» должны поместиться внутри стекла с полями.
    level - столбики вентилятора справа (как на табло BAC-1000), None - без них.
    """
    bars = 18.0 if level is not None else 0.0
    unit = 15.0 if deg else 0.0
    room = w - 16 - unit - bars                       # поля по 8 от рамки
    # ширина надписи: 3 цифры по 0.52 dh, два зазора, точка, наклон 0.1 dh
    dh = min(h - 16, dh_max, (room - 13) / 1.66)
    dw, gap = dh * 0.52, max(3.0, dh * 0.14)
    tw = 3 * dw + 2 * gap + 5 + dh * 0.1
    x0 = x + 8 + (room - tw) / 2.0
    y0 = y + (h - dh) / 2.0
    polys, dots = seven_seg(lcd_text(target), x0, y0, dw, dh, max(2.2, dh * 0.11), gap)
    out = {"x": x, "y": y, "w": w, "h": h, "polys": polys, "dots": dots,
           # для пульта: он рисует цифры сам, пока тянут ползунок
           "seg": [round(x0, 1), round(y0, 1), round(dw, 2), round(dh, 2),
                   round(max(2.2, dh * 0.11), 2), round(gap, 2)],
           "cx": round(x0 + tw + 2, 1), "cy": round(y0 + 8, 1), "deg": deg, "bars": []}
    if level is not None:
        bx = x + w - 8 - bars + 2
        for i in range(3):
            bh = dh * (0.4 + 0.3 * i)
            out["bars"].append({"x": round(bx + i * 5.5, 1), "y": round(y0 + dh - bh, 1),
                                "w": 3.6, "h": round(bh, 1), "on": i < level})
        out["auto"] = {"x": round(bx + 7.3, 1), "y": round(y0 + 6, 1)} if auto else None
    return out


def grille_block(x, y, w, level, spin):
    """Квадратная решётка с пропеллером за ней."""
    cx, cy, r = x + w / 2.0, y + w / 2.0, w / 2.0 - 8
    pitch = (w - 8) / 8.0
    return {"x": x, "y": y, "w": w, "cx": cx, "cy": cy,
            "slats": [round(y + 6 + i * pitch, 1) for i in range(8)],
            "blades": [_fc_blade(cx, cy, r, math.radians(i * 72)) for i in range(5)],
            "hub": round(w * 0.1, 1), "spin": spin,
            "dur": {1: 2.4, 2: 1.3, 3: 0.7}.get(level, 1.3)}


def slider_block(sx, top, bot, lo, hi, current, target, power, labels=True):
    """Ползунок уставки: деления, прорезь, заливка факт -> уставка, колпачок."""
    lo, hi = float(lo), float(hi)
    span = (hi - lo) or 1.0

    def fy(t):
        t = max(lo, min(hi, float(t)))
        return bot - (t - lo) * (bot - top) / span

    ticks = []
    t = int(math.ceil(lo))
    while t <= hi:
        major = t % 2 == 0
        ticks.append({"x1": sx - (22 if major else 18), "x2": sx - 12, "y": round(fy(t), 1),
                      "major": major, "label": t if labels and t % 4 == 0 else None})
        t += 1
    cap_y = round(fy(target if target is not None else (lo + hi) / 2.0), 1)
    cur_y = round(fy(current), 1) if current is not None else None
    fill = None
    if power and cur_y is not None and target is not None and abs(cur_y - cap_y) > 1:
        fill = {"y": min(cur_y, cap_y), "h": round(abs(cur_y - cap_y), 1)}
    # top, bot, smin, smax - для живой плитки (1.52): страница двигает
    # колпачок за пальцем по той же шкале, что и пульт.
    return {"sx": sx, "ticks": ticks, "slot_y": top - 8, "slot_h": bot - top + 16,
            "cap_y": cap_y, "cur_y": cur_y, "fill": fill,
            "top": top, "bot": bot, "smin": lo, "smax": hi}


def build_panel(lo, hi, current, target, power, tone, level, keys, mode, valve, spinning, spin):
    """Всё для широкой плитки-пульта в рамке 354×170 (см. выше)."""
    out = slider_block(SLIDER_X, SLIDER_TOP, SLIDER_BOT, lo, hi, current, target, power)
    gx, gy, gw = GRILLE_X, 12.0, GRILLE_W
    wy = gy + gw + 8
    n = len(keys)
    kgap = 6.0
    kw = (PANEL_W - 14 - RIGHT_X - kgap * (n - 1)) / float(n)
    key_list = []
    for i, k in enumerate(keys):
        x = RIGHT_X + i * (kw + kgap)
        key_list.append(dict(k, x=round(x, 1), w=round(kw, 1), cx=round(x + kw / 2, 1)))
    out.update({
        "tone": tone,
        "grille": grille_block(gx, gy, gw, level, bool(spin and power and spinning)),
        "lcd": lcd_block(gx, wy, gw, PANEL_H - 6 - wy, target, 30.0),
        "right": RIGHT_X, "keys": key_list, "ky": 66.0, "kh": 44.0,
        "mode": mode, "valve": valve, "vx": PANEL_W - 14 - 46 - 16 - 5, "ml": 142.0,
    })
    return out


#: Квадратная плитка фанкойла (1×1) - своя рамка 170×170.
SQUARE = 170.0


def tag_width(text, size=11.0):
    """Ширина подписи .tag: прописные полужирные с разрядкой 0.4 px."""
    return text_width(text, size, 0.70) + 0.4 * len(text)


def fit_title(text, size, room):
    """Название в отведённую ширину: не влезло - обрезаем с многоточием."""
    if tag_width(text, size) <= room:
        return text
    while text and tag_width(text + "…", size) > room:
        text = text[:-1]
    return text.rstrip() + "…"


def build_square(lo, hi, current, target, power, tone, level, auto, mode, spinning, spin,
                 title):
    """
    Фанкойл на плитке 1×1 (с 1.38) - «фейдер», тем же языком, что широкий
    пульт: слева ползунок уставки, справа решётка с пропеллером и под ней
    табло с уставкой и столбиками скорости. Сверху слева - текущая
    температура, справа - значок режима и название.
    """
    out = slider_block(36.0, 36.0, 148.0, lo, hi, current, target, power, labels=False)
    out.update({
        "tone": tone, "mode": mode,
        "grille": grille_block(68.0, 32.0, 88.0, level, bool(spin and power and spinning)),
        "lcd": lcd_block(68.0, 126.0, 88.0, 34.0, target, 22.0,
                         level if power else 0, auto and power, False),
        # 156 - правый край, значок режима 14 и зазор 4, текущая слева до 52
        "title": fit_title(title, 11, SQUARE - 14 - 14 - 4 - 52),
    })
    out["icon_x"] = round(SQUARE - 14 - (tag_width(out["title"]) + 4
                                         if out["title"] else 0) - 14, 1)
    return out


#: Пульт фанкойла на весь экран (с 1.42) - столбик в рамке 390×700:
#: шапка, режимы, ползунок уставки слева, решётка и табло справа, клавиши.
PAD_W, PAD_H = 390.0, 700.0


def build_pad(w, h, lo, hi, smin, smax, current, target, power, tone, level, keys,
              modes, mode_key, valve, spinning, spin):
    """Всё для пульта w×h; рамка 390×700 вписывается и центруется."""
    k = min(w / PAD_W, h / PAD_H)
    dx, dy = (w - PAD_W * k) / 2.0, (h - PAD_H * k) / 2.0
    top, bot = 150.0, 540.0
    out = slider_block(78.0, top, bot, smin, smax, current, target, power)
    gx, gw = 150.0, 214.0
    out.update({
        "k": round(k, 4), "dx": round(dx, 1), "dy": round(dy, 1), "tone": tone,
        "w": w, "h": h, "top": top, "bot": bot,
        "lo": float(lo), "hi": float(hi), "smin": float(smin), "smax": float(smax),
        "grille": grille_block(gx, 142.0, gw, level, bool(spin and power and spinning)),
        "lcd": lcd_block(gx, 368.0, gw, 84.0, target, 52.0, level if power else 0,
                         False, True),
        "lcd_box": [gx, 368.0, gw, 84.0],
        "now_y": 500.0, "now_x": gx,
    })
    # клавиши скорости во всю ширину
    n = len(keys)
    kgap, kx0 = 10.0, 24.0
    kw = (PAD_W - 2 * kx0 - kgap * (n - 1)) / float(n)
    out["keys"] = [dict(kk, x=round(kx0 + i * (kw + kgap), 1), w=round(kw, 1),
                        cx=round(kx0 + i * (kw + kgap) + kw / 2, 1)) for i, kk in enumerate(keys)]
    out["ky"], out["kh"] = 576.0, 64.0
    # режимы - ряд пилюль под шапкой
    m = len(modes)
    mgap = 8.0
    mw = (PAD_W - 2 * 24 - mgap * (m - 1)) / float(max(m, 1))
    out["modes"] = []
    for i, (row, body) in enumerate(modes):
        word = (body.get("short") or body.get("title") or str(row)).lower()
        x = 24 + i * (mw + mgap)
        span = 18 + 6 + text_width(word, 14, 0.6)
        ix = x + (mw - span) / 2.0
        out["modes"].append({"x": round(x, 1), "w": round(mw, 1), "ix": round(ix, 1),
                             "tx": round(ix + 24, 1), "value": str(row),
                             "icon": body.get("icon", "fan"), "word": word,
                             "on": power and row == mode_key})
    out["my"], out["mh"] = 78.0, 44.0
    out["valve"] = valve
    return out


@tile("fancoil")
class Fancoil(Tile):
    """Фанкойл: циферблат термостата плюс колонка режима, скорости и клапана."""

    roles = ("switch", "mode", "fan", "fan_state", "target", "current", "state")

    def tables(self, ctx):
        return (table(ctx.opt("modes"), FANCOIL_MODES),
                table(ctx.opt("fans"), FANCOIL_FANS),
                table(ctx.opt("fan_states"), FANCOIL_FAN_STATES))

    def prepare(self, ctx):
        modes, fans, fan_states = self.tables(ctx)

        # Питание может быть и не заведено: у части приборов «выключено» -
        # это просто ещё одно значение в таблице режимов.
        power = ctx.flag("switch") if ctx.has("switch") else True

        mode_key = key(ctx.raw("mode"))
        mode = find(modes, mode_key)
        uses_valve = mode.get("valve", True)

        fan_key = key(ctx.raw("fan"))
        fan = find(fans, fan_key)

        fact_key = key(ctx.raw("fan_state")) if ctx.has("fan_state") else ""
        fact = find(fan_states, fact_key) if fact_key else {}
        spinning = bool(fact) and not fact.get("off")

        valve = ctx.flag("state") if ctx.has("state") else None

        current = ctx.number("current")
        target = ctx.number("target")
        reached = (current is not None and target is not None
                   and abs(target - current) <= REACHED)

        width = float(ctx.opt("inner_w") or 170)
        height = float(ctx.opt("chart_h") or 170)
        # Колонка состояний помещается, только если плитка заметно шире
        # своей высоты. На квадратной ей пришлось бы лезть на дугу.
        side = width >= height * 1.5
        cx = height * 0.52 if side else width / 2.0

        gauge = None
        if ctx.opt("dial", True):
            gauge = build_gauge(width, height,
                                ctx.opt("scale_min", 14), ctx.opt("scale_max", 30),
                                current, target, cx)

        # Работает ли оборудование. Клапан - это подача воды, а не работа:
        # он открыт и у прибора, который только что включился. Работа - это
        # крутящийся вентилятор; если факта нет, судим по клапану и режиму.
        if not power:
            active = False
        elif fact_key:
            active = spinning
        elif uses_valve and valve is not None:
            active = valve
        else:
            active = True

        target_num = self.number(target)
        big = 30.0 * float(ctx.opt("fs", 1.0))

        # Широкая плитка - пультом (с 1.37); look: classic - прежний циферблат.
        panel = None
        pad = None
        if ctx.opt("view") == "pad" and ctx.opt("look") != "classic":
            # Пульт на весь экран (с 1.42) - тот же прибор, крупно.
            tone = "cool" if mode.get("cool") else ("fan" if not uses_valve else "heat")
            pad = build_pad(width, height, ctx.opt("temp_min", 16), ctx.opt("temp_max", 30),
                            ctx.opt("scale_min", 14), ctx.opt("scale_max", 30),
                            current, target, power, tone,
                            self.level_of(fan, fact) if power else 0,
                            self.keys(fans, fan_key, power), modes, mode_key,
                            (bool(valve) if valve is not None and uses_valve else None),
                            active, ctx.opt("spin", True))
            pad["title"] = str(ctx.opt("title") or "Фанкойл")
            pad["current"] = _fmt(current, 1) if current is not None else "--"
            pad["step"] = float(ctx.step("target", 0.5) or 0.5)
            pad["target"] = target
        elif side and ctx.opt("look") != "classic":
            if mode.get("cool"):
                tone = "cool"
            elif not uses_valve:
                tone = "fan"
            else:
                tone = "heat"
            panel = build_panel(
                ctx.opt("scale_min", 14), ctx.opt("scale_max", 30), current, target,
                power, tone, self.level_of(fan, fact) if power else 0,
                self.keys(fans, fan_key, power), {"icon": mode.get("icon", "fan"),
                                                  "title": (mode.get("short") or mode.get("title") or "—").lower(),
                                                  "on": power},
                (bool(valve) if valve is not None and uses_valve else None), active,
                ctx.opt("spin", True))
            scale = min(width / PANEL_W, height / PANEL_H)
            panel["k"] = round(scale, 4)
            panel["dx"] = round((width - PANEL_W * scale) / 2.0, 1)
            panel["dy"] = round((height - PANEL_H * scale) / 2.0, 1)
            panel["title"] = str(ctx.opt("title") or "").upper()
            panel["current"] = _fmt(current, 1) if current is not None else "--"
        elif (not side and ctx.opt("look") != "classic"
              and min(width, height) >= 120):
            # Квадратная плитка (с 1.38) - тот же прибор, только мельче.
            if mode.get("cool"):
                tone = "cool"
            elif not uses_valve:
                tone = "fan"
            else:
                tone = "heat"
            panel = build_square(
                ctx.opt("scale_min", 14), ctx.opt("scale_max", 30), current, target,
                power, tone, self.level_of(fan, fact) if power else 0,
                bool(fan.get("auto")),
                {"icon": mode.get("icon", "fan"),
                 "title": (mode.get("short") or mode.get("title") or "—").lower(), "on": power},
                active, ctx.opt("spin", True), str(ctx.opt("title") or "").upper())
            scale = min(width / SQUARE, height / SQUARE)
            panel["k"] = round(scale, 4)
            panel["dx"] = round((width - SQUARE * scale) / 2.0, 1)
            panel["dy"] = round((height - SQUARE * scale) / 2.0, 1)
            panel["current"] = _fmt(current, 1) if current is not None else "--"
            panel["square"] = True

        return {
            "pad": pad,
            "panel": panel,
            "dial": gauge,
            "side": self.side_box(width, height) if side and gauge else None,
            "rows": self.rows(width, height, mode, fan, fact, valve, power)
                    if side and gauge else [],
            # Узкая плитка: колонки нет, и значки уходят в верхние углы -
            # режим слева, вентилятор со ступенями справа. Место там всё
            # равно пустует, а под дугой остаётся одно слово состояния.
            "corners": self.corners(width, mode, fan, fact, power)
                       if gauge and not side else None,
            "on": active,
            "enabled": power,
            "cooling": bool(mode.get("cool")),
            "mode": mode_key,
            "mode_ru": mode.get("title", ""),
            "icon": (ctx.opt("icon")
                     or (mode.get("icon") if power else "power") or "fan"),
            "target_num": target_num,
            "target_value": target,
            "current_value": current,
            "target": ("%s°" % target_num) if target is not None else "--",
            "deg_dx": round(text_width(target_num, big) / 2.0
                            + 1.0 * float(ctx.opt("fs", 1.0)), 1),
            "deg_size": round(big * 0.60, 1),
            "current": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "short": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "status": self.status(power, mode, uses_valve, valve, fan, fact,
                                  reached, side),
            "always_status": True,
            "reach": None,
            "fan_now": fan_key,
            "lo": float(ctx.opt("temp_min", 16)),
            "hi": float(ctx.opt("temp_max", 30)),
        }

    @staticmethod
    def keys(fans, fan_key, power):
        """
        Клавиши скорости: «0» (выключен), ступени по возрастанию и «А», если
        прибор умеет авто. Утоплена одна - по цели скорости; у выключенного
        прибора - «0». Светодиод горит у утопленной, кроме «0».
        """
        steps = sorted(((body.get("level"), row) for row, body in fans if body.get("level")),
                       key=lambda pair: pair[0])
        auto = [row for row, body in fans if body.get("auto")]
        out = [{"label": "0", "down": not power, "led": False, "value": "off"}]
        for lvl, row in steps:
            down = power and row == fan_key
            out.append({"label": str(lvl), "down": down, "led": down, "value": str(row)})
        if auto:
            down = power and auto[0] == fan_key
            out.append({"label": "А", "down": down, "led": down, "value": str(auto[0])})
        return out

    @staticmethod
    def side_box(width, height):
        """Колонка справа от дуги: где начинается текст и где стоят полосы."""
        x = round(height * 1.04 + 4, 1)
        return {"x": x, "text_x": round(x + 24, 1),
                "bars_x": round(width - 20 - 21, 1)}

    @staticmethod
    def level_of(fan, fact):
        """Сколько ступеней зажечь: смотрим на факт, цель - только запасной."""
        if fact.get("off"):
            return 0
        return fact.get("level", fan.get("level", 0))

    def corners(self, width, mode, fan, fact, power):
        """Значки в верхних углах узкой плитки."""
        level = self.level_of(fan, fact)
        bars_x = width - 31 - 26
        return {
            "mode": {"icon": mode.get("icon", "fan"), "x": 14, "on": power},
            "fan": {"icon": "fan", "x": round(width - 31, 1),
                    "on": power and level > 0,
                    "bars": [{"x": round(bars_x + i * 7, 1),
                              "y": round(22 - i * 3, 1),
                              "h": 4 + i * 3, "on": power and level > i}
                             for i in range(3)]},
        }

    def rows(self, width, height, mode, fan, fact, valve, power):
        """Три строки справа: режим, вентилятор, клапан."""
        box = self.side_box(width, height)
        fact_title = fact.get("title", "")
        level = self.level_of(fan, fact)
        if fact.get("off"):
            fan_text = "Вентилятор стоит"
        elif fan.get("auto"):
            # Цель «авто» и факт вместе: скорость выбирает прибор, и
            # увидеть его выбор больше негде.
            fan_text = "Авто" + (" · %s" % fact_title if fact_title else "")
        else:
            fan_text = "Скорость " + (fact_title or fan.get("title", "?"))

        out = [
            {"icon": mode.get("icon", "fan"), "y": round(height * 0.33, 1),
             "text": mode.get("title", "—"), "on": power},
            {"icon": "fan", "y": round(height * 0.55, 1),
             "text": fan_text, "on": power and level > 0,
             "bars": [{"x": box["bars_x"] + i * 7,
                       "y": round(height * 0.55 - 3 - i * 3, 1),
                       "h": 4 + i * 3, "on": power and level > i}
                      for i in range(3)]},
        ]
        if valve is not None:
            out.append({"icon": "drop", "y": round(height * 0.77, 1),
                        "text": "Клапан открыт" if valve else "Клапан закрыт",
                        "on": bool(valve)})
        return out

    @staticmethod
    def number(value):
        """Уставка: 25.5 показываем с десятой, 25 - без неё."""
        if value is None:
            return "--"
        return _fmt(value, 0 if float(value).is_integer() else 1)

    @staticmethod
    def status(power, mode, uses_valve, valve, fan, fact, reached, side):
        """
        Строка под дугой - только состояние прибора, одним словом.

        Режим, скорость и клапан показаны значками: на широкой плитке
        колонкой справа, на узкой - по верхним углам. Дублировать их
        словами незачем, места под дугой на это всё равно нет.
        """
        if not power:
            return "Выключен"

        if not uses_valve:
            return mode.get("verb") or mode.get("title", "")
        if valve:
            return mode.get("verb", "Работает")
        if reached:
            # То самое «дошёл до уставки»: на дуге закраска уже исчезла,
            # словом остаётся назвать, что прибор держит достигнутое.
            return "Поддерживает"
        return "Ждёт"

    def zones(self, ctx, data):
        """
        Короткое нажатие раскрывает плитку, долгое открывает пульт.

        Питание тоже описано зоной, хотя касанием не переключается: из неё
        пульт берёт топик и значения для своей кнопки. Раскрытие стоит в
        обработчике раньше переключения, поэтому промах по экрану фанкойл
        не выключит.
        """
        out = [Zone("expand", "all"), Zone("pad", "long", pad="ac")]
        if ctx.has("switch"):
            out.append(Zone("toggle", "all", "switch",
                            {"on": str(ctx.opt("command_on", "1")),
                             "off": str(ctx.opt("command_off", "0"))},
                            state=bool(data.get("enabled"))))
        return out

    def pad(self, ctx, data):
        modes, fans, _fan_states = self.tables(ctx)

        def command(role):
            bound = ctx.bound.get(role)
            return bound.command if bound else None

        return {
            "ac_target": command("target"),
            "ac_mode": command("mode"),
            "ac_fan": command("fan"),
            "ac_lo": str(data.get("lo", 16)),
            "ac_hi": str(data.get("hi", 30)),
            "ac_step": str(ctx.step("target", 0.5)),
            "ac_now": data.get("target_num", "--"),
            "ac_cur": data.get("current", ""),
            "ac_mode_now": data.get("mode", ""),
            "ac_fan_now": data.get("fan_now", ""),
            # Кнопки пульта. У фанкойла это числа со своими подписями, и
            # зашитый в страницу набор кондиционера здесь не подходит.
            "ac_modes": buttons(modes),
            "ac_fans": buttons(fans),
            # прежний пульт-дуга - только у look: classic (с 1.42)
            "look": "classic" if ctx.opt("look") == "classic" else None,
        }
