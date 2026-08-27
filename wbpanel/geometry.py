# -*- coding: utf-8 -*-
"""
Цвета, шкалы, сглаживание кривых, раскладка плиток по сетке.
"""

import math
from .const import BAND, TILE
import time
import re


# ==========================================================================
#  Рисовалки: цвет, кривые, раскладка
# ==========================================================================

CCT_STOPS = [
    (2000, (255, 152, 66)),
    (2700, (255, 179, 106)),
    (3200, (255, 199, 143)),
    (4000, (255, 221, 186)),
    (5000, (255, 240, 224)),
    (6500, (216, 232, 255)),
]


# Для поля плитки нужны более звонкие цвета, чем физически точные:
# на серой подложке реалистичный «холодный белый» выглядит просто серым.
FIELD_STOPS = [
    (2000, (255, 138, 43)),
    (2700, (255, 168, 84)),
    (3200, (255, 196, 130)),
    (4000, (243, 219, 188)),
    (5000, (176, 209, 247)),
    (6500, (86, 170, 255)),
]


def _interp(stops, kelvin):
    kelvin = max(stops[0][0], min(stops[-1][0], float(kelvin)))
    for i in range(len(stops) - 1):
        k0, c0 = stops[i]
        k1, c1 = stops[i + 1]
        if k0 <= kelvin <= k1:
            f = (kelvin - k0) / (k1 - k0)
            rgb = tuple(int(round(c0[j] + (c1[j] - c0[j]) * f)) for j in range(3))
            return "#%02X%02X%02X" % rgb
    return "#FFFFFF"


def field_color(kelvin):
    """Цвет для поля плитки ленты - насыщеннее реалистичного."""
    return _interp(FIELD_STOPS, kelvin)


def cct_color(kelvin):
    """Цветовая температура в Кельвинах -> hex. Кусочно-линейная интерполяция."""
    kelvin = max(CCT_STOPS[0][0], min(CCT_STOPS[-1][0], float(kelvin)))
    for i in range(len(CCT_STOPS) - 1):
        k0, c0 = CCT_STOPS[i]
        k1, c1 = CCT_STOPS[i + 1]
        if k0 <= kelvin <= k1:
            f = (kelvin - k0) / (k1 - k0)
            rgb = tuple(int(round(c0[j] + (c1[j] - c0[j]) * f)) for j in range(3))
            return "#%02X%02X%02X" % rgb
    return "#FFFFFF"


def nice_step(lo, hi, target=4):
    """Шаг сетки «по-человечески»: 1, 2, 5, 10, 20, 50 …"""
    span = max(hi - lo, 1e-9)
    raw = span / max(target, 1)
    power = 10.0 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= power * mult:
            return power * mult
    return power * 10


def edge_anchor(x, width, margin=14.0):
    """
    Куда прижать подпись у самого края плитки.

    По центру подпись «21» на x=3 наполовину уезжает за рамку и сливается
    с подписью шкалы, которая стоит там же слева.
    """
    if x < margin:
        return (4.0, "start")
    if x > width - margin:
        return (width - 4.0, "end")
    return (x, "middle")


def label_digits(step):
    """
    Сколько знаков после запятой в подписи шкалы.

    Считаем по ШАГУ, а не по размаху: при шаге 2.5 подписи «15, 18, 20, 22»
    врут - на деле там 15, 17.5, 20, 22.5.
    """
    if step >= 1 and abs(step - round(step)) < 1e-9:
        return 0
    return 1 if step >= 0.1 else 2


def grid_bounds(values, step=None, unit="", divisions=None, target=4):
    """
    Границы шкалы, выровненные по сетке.

    Возвращает (lo, hi, step, число делений).

    Шаг по умолчанию считается ОТ РАЗМАХА ДАННЫХ, а не от единиц измерения.
    Жёсткие 5 °C и 10 % выглядят разумно на бумаге, но комнатная температура
    за 12 часов гуляет на полтора градуса - при шаге 5 шкала растягивается
    втрое шире данных, и кривая превращается в плоскую нитку. Нужен жёсткий
    шаг - задайте `grid` у серии; нужны фиксированные границы - `min`/`max`.
    """
    if not values:
        return (0.0, 1.0, 1.0, 1)
    vmin, vmax = min(values), max(values)
    if step is None:
        step = nice_step(vmin, vmax, target=target)
    lo = math.floor(vmin / step) * step
    hi = math.ceil(vmax / step) * step
    if hi - lo < step * 0.5:
        hi = lo + step
    n = int(round((hi - lo) / step))
    n = max(n, 1)
    # растягиваем до нужного числа делений, чтобы линии разных серий совпали
    if divisions and divisions > n:
        while n < divisions:
            hi += step
            n += 1
            if n < divisions:
                lo -= step
                n += 1
    return (lo, hi, step, n)


def smooth_path(points, width, height, band=BAND, pad_x=3.0,
                lo=None, hi=None):
    """
    Плавная кривая по [(ts, value)] ВО ВСЮ ширину плитки (от рамки до рамки).

    Сама кривая живёт в вертикальной полосе band (доли высоты плитки), чтобы
    не лезть на подписи, а заливка под ней уходит до самого низа - «до пола».
    """
    if len(points) < 2:
        return None
    stamps = [p[0] for p in points]
    values = [p[1] for p in points]
    t0, t1 = stamps[0], stamps[-1]
    if t1 <= t0:
        return None
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    if hi - lo < 1e-9:
        lo -= 0.5
        hi += 0.5

    y_top = height * band[0]
    y_bot = height * band[1]

    def px(t):
        return pad_x + (t - t0) / (t1 - t0) * (width - 2 * pad_x)

    def py(v):
        return y_bot - (v - lo) / (hi - lo) * (y_bot - y_top)

    xy = [(px(t), py(v)) for t, v in points]
    d = "M %.1f,%.1f" % xy[0]
    for i in range(1, len(xy)):
        x0, y0 = xy[i - 1]
        x1, y1 = xy[i]
        mx = (x0 + x1) / 2.0
        d += " C %.1f,%.1f %.1f,%.1f %.1f,%.1f" % (mx, y0, mx, y1, x1, y1)
    # заливка замыкается по нижней кромке плитки, а не по низу графика
    area = d + " L %.1f,%.1f L %.1f,%.1f Z" % (xy[-1][0], height, xy[0][0], height)
    return {"line": d, "area": area, "lo": lo, "hi": hi,
            "last": xy[-1], "first": xy[0]}


def darken(hex_color, factor=0.72):
    """Тот же оттенок, но темнее - чтобы цифра читалась на светлой подложке."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c * factor)))) for c in rgb)


def luma(hex_color):
    """
    Воспринимаемая яркость цвета, 0…1.

    Веса не равные: глаз в разы чувствительнее к зелёному, чем к синему,
    поэтому жёлтый на белом почти исчезает, а синий той же «яркости» по
    числам читается прекрасно.
    """
    h = str(hex_color).lstrip("#")
    if len(h) != 6:
        return 0.5
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return 0.5
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lighten(hex_color, amount):
    """Тот же оттенок, но светлее: подмешиваем белого на долю amount."""
    h = str(hex_color).lstrip("#")
    if len(h) != 6:
        return hex_color
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    amount = max(0.0, min(1.0, amount))
    return "#%02X%02X%02X" % tuple(
        int(round(c + (255 - c) * amount)) for c in rgb)


# Во что упирается подпись: светлая плитка #D9D9DC и тёмная #2C2C31.
# Пороги подобраны так, чтобы цифра читалась, но цвет линии оставался
# узнаваемым - а не превращался в бурый или в грязно-белый.
LABEL_LUMA = {"light": 0.46, "dark": 0.52}


def label_color(hex_color, theme="light"):
    """
    Цвет линии -> цвет подписи к ней.

    Затемнять всё на одну и ту же долю нельзя. Постоянный множитель 0.72
    превращал оранжевый #E08A2D в бурый #A16320: оттенок формально тот же,
    а линию в нём уже не узнать. Синему то же затемнение вовсе не нужно -
    он и так тёмный, - а на тёмной теме оно било мимо цели, там подпись
    наоборот должна светлеть.

    Поэтому от цвета линии отступаем ровно настолько, насколько требует
    подложка, и ни каплей больше. Оттенок не плывёт: при затемнении все
    три составляющие делятся на одно число, при осветлении подмешивается
    белый - серый, а не чужой цвет.
    """
    lum = luma(hex_color)
    if theme == "dark":
        floor = LABEL_LUMA["dark"]
        if lum >= floor:
            return hex_color
        # Сколько белого нужно, чтобы дотянуть до порога. Приближение
        # грубое, зато без поиска: яркость растёт почти линейно с примесью.
        return lighten(hex_color, min(1.0, (floor - lum) / max(1.0 - lum, 0.01)))
    ceiling = LABEL_LUMA["light"]
    if lum <= ceiling or lum <= 0:
        return hex_color
    return darken(hex_color, ceiling / lum)


def hour_ticks(t0, t1, width, pad_x=0.0, max_lines=13):
    """
    Границы часов внутри окна графика -> список x в пикселях.

    Шаг подбирается так, чтобы линий было не больше max_lines: для 12 часов
    это каждый час, для суток - каждые два, для недели - каждые 12.
    """
    if t1 <= t0:
        return []
    span_h = (t1 - t0) / 3600.0
    step_h = 1
    for candidate in (1, 2, 3, 6, 12, 24, 48):
        if span_h / candidate <= max_lines:
            step_h = candidate
            break
    else:
        step_h = 48

    # ближайшая вниз граница часа по локальному времени
    lt = time.localtime(t0)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour,
                        0, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    step = step_h * 3600
    out = []
    stamp = base
    while stamp <= t1 + step:
        if stamp > t0:
            hour = time.localtime(stamp).tm_hour
            if hour % step_h == 0:
                x = pad_x + (stamp - t0) / (t1 - t0) * (width - 2 * pad_x)
                if 2 < x < width - 2:
                    out.append({"x": round(x, 1), "hour": hour,
                                "major": hour % 6 == 0})
        stamp += 3600
    return out


def cells(value, default=1.0):
    """Размер из конфига (в плитках) -> число мелких ячеек. 1 -> 2, 0.5 -> 1."""
    try:
        n = int(round(float(value if value is not None else default) * 2))
    except (TypeError, ValueError):
        n = int(default * 2)
    return max(1, n)


def fixed_spot(tile, cols):
    """
    Координаты из конфига в мелких ячейках, либо None.

    col и row задаются в плитках, как и размеры: 0.5 - мелкая ячейка,
    1 - обычная. Отсчёт от нуля, левый верхний угол.
    """
    if tile.get("col") is None and tile.get("row") is None:
        return None
    def coord(value):
        try:
            return max(0, int(round(float(value) * 2)))
        except (TypeError, ValueError):
            return 0
    return (coord(tile.get("row", 0)), coord(tile.get("col", 0)))


def layout(tiles, cols):
    """
    Раскладка плиток разного размера. Размеры в конфиге задаются в плитках
    (1 - обычная, 2 - двойная, 0.5 - мелкая), внутри всё считается в мелких
    ячейках: cols тоже приходит уже в них.

    Сначала на сетку встают плитки с заданными col/row, потом остальные
    заполняют, что осталось, - сверху вниз, слева направо.

    Недостижимая координата не ломает панель, а просто теряет силу: плитка
    уходит в общий поток. Иначе один опечатанный col оставил бы пустой
    экран, а найти причину было бы негде.
    """
    def place(demoted):
        """Одна попытка раскладки. demoted - закреплённые, потерявшие место."""
        occupied = set()
        placed = []
        flow = []

        def free(row, col, w, h):
            return all((row + dr, col + dc) not in occupied
                       for dr in range(h) for dc in range(w))

        def take(tile, row, col, w, h):
            for dr in range(h):
                for dc in range(w):
                    occupied.add((row + dr, col + dc))
            placed.append((tile, row, col, w, h))

        # 1. закреплённые
        for tile in tiles:
            w = min(cells(tile.get("w"), 1.0), cols)
            h = cells(tile.get("h"), 1.0)
            spot = fixed_spot(tile, cols)
            if spot is None or id(tile) in demoted:
                flow.append(tile)
                continue
            row, col = spot
            # За правый край не уходим: если col задан больше ширины панели,
            # закрепление теряет смысл - на узком экране столбца просто нет.
            if col + w > cols or not free(row, col, w, h):
                flow.append(tile)
                continue
            take(tile, row, col, w, h)

        # 2. остальные - в свободные места
        for tile in flow:
            w = min(cells(tile.get("w"), 1.0), cols)
            h = cells(tile.get("h"), 1.0)
            row = 0
            while True:
                spot = None
                for col in range(cols - w + 1):
                    if free(row, col, w, h):
                        spot = col
                        break
                if spot is not None:
                    take(tile, row, spot, w, h)
                    break
                row += 1
        return placed, occupied

    # Пустые строки означают, что кто-то закрепился слишком низко: плиток
    # не хватило, чтобы дотянуть до него. Такой объект теряет закрепление и
    # уходит в конец потока - иначе панель растянется дырами.
    demoted = set()
    for _ in range(len(tiles) + 1):
        placed, occupied = place(demoted)
        rows = max((r + h for _, r, _, _, h in placed), default=0)
        empty = [r for r in range(rows)
                 if not any((r, c) in occupied for c in range(cols))]
        if not empty:
            break
        first_gap = empty[0]
        below = [t for t, r, _c, _w, _h in placed
                 if r > first_gap and fixed_spot(t, cols) and id(t) not in demoted]
        if not below:
            break
        demoted.update(id(t) for t in below)

    # Порядок в разметке должен совпадать с порядком в конфиге: от него
    # зависят индексы плиток, по которым открывается раскрытый вид.
    order = {id(t): i for i, t in enumerate(tiles)}
    placed.sort(key=lambda item: order[id(item[0])])

    rows = max((r + h for _, r, _, _, h in placed), default=0)
    return placed, rows


# Доли от кегля: у SF Pro / Segoe цифры моноширинные, знаки уже.
CHAR_W = {".": 0.28, ",": 0.28, "-": 0.36, "\u2212": 0.36, "\u00B0": 0.42, " ": 0.28}


def text_width(text, size, default=0.56):
    """Грубая ширина строки в px. Точности хватает, чтобы выровнять число."""
    return size * sum(CHAR_W.get(ch, default) for ch in str(text))


def _fmt(value, digits=1):
    if value is None:
        return "--"
    if abs(value - round(value)) < 1e-9:
        return "%d" % round(value)
    return ("%%.%df" % digits) % value


def parse_duration(text):
    """'24h' / '90m' / '7d' / 3600 -> секунды."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])?\s*", str(text))
    if not m:
        return 86400
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2) or "s"]
    return int(m.group(1)) * mult
