"""
Вид за окном у штор (с 1.20): небо по времени суток, солнце и луна там,
где они сейчас стоят, погода с устройства прогноза.

Всё считается здесь, без сети и без пакетов, и рисуется без фильтров -
размытие на каждую плитку слишком дорого для панели на стене. Мягкость
облаков и свечения дают радиальные градиенты.

    location: {lat: 55.75, lon: 37.62}   # общие координаты дома
    facing: 180          # куда смотрит окно: 180 или S / Ю - на юг
    weather: weather     # устройство прогноза (extras/weather.js); false - без погоды

Каждое поле можно задать у плитки, у панели или в корне config.yaml.
Координаты берутся ещё и из theme_auto.location; без них - Москва.

Точность - градус-два: солнце по упрощённым формулам NOAA, луна по трём
главным членам ряда. Для картинки в окне больше не нужно.
"""

import datetime as dt
import math
import random
import time
import zlib

#: Без координат - Москва, как в extras/weather.js.
DEFAULT_LOCATION = (55.7558, 37.6173)
#: Без facing окно смотрит на юг: у нас солнце бывает там почти весь день.
DEFAULT_FACING = 180.0
#: Сколько градусов неба помещается в окно по горизонтали.
FOV = 140.0
#: Высота над горизонтом, которая приходится на верх окна.
TOP_ALT = 52.0

_SIDES = {
    "n": 0, "nne": 22.5, "ne": 45, "ene": 67.5, "e": 90, "ese": 112.5,
    "se": 135, "sse": 157.5, "s": 180, "ssw": 202.5, "sw": 225,
    "wsw": 247.5, "w": 270, "wnw": 292.5, "nw": 315, "nnw": 337.5,
    "с": 0, "св": 45, "в": 90, "юв": 135, "ю": 180, "юз": 225, "з": 270, "сз": 315,
    "north": 0, "east": 90, "south": 180, "west": 270,
    "север": 0, "восток": 90, "юг": 180, "запад": 270,
}

RAD = math.radians
DEG = math.degrees


# --------------------------------------------------------------- астрономия
def _days(ts):
    """Дни от J2000.0 (2000-01-01 12:00 UT)."""
    return ts / 86400.0 - 10957.5


def _horizontal(ra, dec, d, lat, lon):
    """Прямое восхождение и склонение (рад) -> высота и азимут (град, от
    севера по часовой)."""
    gmst = (280.46061837 + 360.98564736629 * d) % 360
    ha = RAD(gmst + lon) - ra
    la = RAD(lat)
    alt = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha) * math.cos(dec),
                    math.sin(dec) * math.cos(la) - math.cos(dec) * math.sin(la) * math.cos(ha))
    return DEG(alt), DEG(az) % 360


def _equatorial(lam, beta, eps):
    ra = math.atan2(math.sin(lam) * math.cos(eps) - math.tan(beta) * math.sin(eps), math.cos(lam))
    dec = math.asin(math.sin(beta) * math.cos(eps) + math.cos(beta) * math.sin(eps) * math.sin(lam))
    return ra, dec


def _sun_lon(d):
    g = RAD(357.529 + 0.98560028 * d)
    q = 280.459 + 0.98564736 * d
    return RAD(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))


def sun_position(ts, lat, lon):
    """(высота, азимут) солнца в градусах."""
    d = _days(ts)
    eps = RAD(23.439 - 0.00000036 * d)
    ra, dec = _equatorial(_sun_lon(d), 0.0, eps)
    return _horizontal(ra, dec, d, lat, lon)


def moon_position(ts, lat, lon):
    """(высота, азимут, возраст 0..1) луны. Возраст 0 - новолуние,
    0.5 - полнолуние; до половины растёт."""
    d = _days(ts)
    eps = RAD(23.439 - 0.00000036 * d)
    L = 218.316 + 13.176396 * d
    M = RAD(134.963 + 13.064993 * d)
    F = RAD(93.272 + 13.229350 * d)
    lam = RAD(L + 6.289 * math.sin(M))
    beta = RAD(5.128 * math.sin(F))
    ra, dec = _equatorial(lam, beta, eps)
    alt, az = _horizontal(ra, dec, d, lat, lon)
    # Параллакс луны - почти градус, у горизонта заметно.
    alt -= 0.95 * math.cos(RAD(alt))
    age = ((DEG(lam) - DEG(_sun_lon(d))) % 360) / 360.0
    return alt, az, age


def facing_of(value):
    """180, "S", "юг", "ЮЗ" -> азимут в градусах."""
    if value is None or value == "":
        return DEFAULT_FACING
    try:
        return float(value) % 360
    except (TypeError, ValueError):
        return float(_SIDES.get(str(value).strip().lower(), DEFAULT_FACING))


def location_of(*sources):
    """Первые годные координаты из списка словарей {lat, lon}."""
    for loc in sources:
        try:
            return float(loc["lat"]), float(loc["lon"])
        except (KeyError, TypeError, ValueError):
            continue
    return DEFAULT_LOCATION


# ------------------------------------------------------------------ погода
def _series_now(raw, start, now):
    """Значение ряда прогноза на текущий час. Ряд начинается с часа
    forecast_start; если прогноз давно не обновлялся, берём нужный час
    дальше по ряду, а не первый."""
    if not raw:
        return None
    parts = str(raw).split(",")
    idx = 0
    if start:
        try:
            t0 = dt.datetime.strptime(str(start).strip()[:16], "%Y-%m-%dT%H:%M")
            idx = int((dt.datetime.fromtimestamp(now) - t0).total_seconds() // 3600)
        except ValueError:
            idx = 0
    if idx < 0:
        idx = 0
    if idx >= len(parts):
        return None
    try:
        return float(parts[idx])
    except ValueError:
        return None


def weather_now(state, device, now):
    """{clouds, rain, snow, fog, storm} 0..1 по устройству прогноза, или
    None, если устройства нет. Каналы читаются напрямую из состояния, а не
    через роли: пропавший прогноз не должен красить штору «нет данных»."""
    if not device or state is None:
        return None

    def raw(name):
        return state.snapshot("%s/%s" % (device, name)).get("raw")

    start = raw("forecast_start")
    code = _series_now(raw("code_series"), start, now)
    cover = _series_now(raw("cloud_series"), start, now)
    if code is None and cover is None:
        return None
    try:
        precip = float(raw("precip_now") or 0)
    except ValueError:
        precip = 0.0

    out = {"clouds": 0.0, "rain": 0.0, "snow": 0.0, "fog": 0.0, "storm": 0.0,
           "code": None if code is None else int(code)}
    if cover is not None:
        out["clouds"] = max(0.0, min(1.0, cover / 100.0))
    c = out["code"]
    if c is not None:
        if cover is None:
            out["clouds"] = {0: 0.0, 1: 0.2, 2: 0.5, 3: 0.95}.get(c, 0.9)
        if c in (45, 48):
            out["fog"] = 0.8
        elif c in (51, 53, 55, 56, 57):
            out["rain"] = {51: .3, 53: .45, 55: .6}.get(c, .45)
        elif c in (61, 63, 65, 66, 67, 80, 81, 82):
            out["rain"] = {61: .55, 63: .8, 80: .6, 81: .85}.get(c, 1.0)
        elif c in (71, 73, 75, 77, 85, 86):
            out["snow"] = {71: .5, 73: .8, 77: .4, 85: .7}.get(c, 1.0)
        elif c in (95, 96, 99):
            out["rain"], out["storm"] = 1.0, 1.0
        if out["rain"] or out["snow"] or out["storm"]:
            out["clouds"] = max(out["clouds"], 0.85)
    if precip > 0 and not (out["rain"] or out["snow"]):
        # Код прогноза отстаёт от фактических осадков - верим осадкам.
        out["rain"] = min(1.0, 0.3 + precip / 3.0)
        out["clouds"] = max(out["clouds"], 0.8)
    return out


# ------------------------------------------------------------------- цвета
def _mix(c1, c2, f):
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))


#: Небо по высоте солнца: верх, середина, у горизонта.
_SKY = [(-18, ("#070B16", "#0E1628", "#162038")),
        (-9, ("#121A34", "#2C2E58", "#5A4A70")),
        (-3, ("#25305A", "#7A5A82", "#D98A7A")),
        (2, ("#3A4A7A", "#C47A8A", "#F6B07A")),
        (8, ("#4A70A4", "#E0A484", "#F6D2A2")),
        (20, ("#5A90C6", "#9CC2E2", "#D8E8F0")),
        (60, ("#4A88C6", "#8CBAE4", "#CDE2F0"))]


def sky_colors(alt):
    alt = max(_SKY[0][0], min(_SKY[-1][0], alt))
    for (a0, c0), (a1, c1) in zip(_SKY, _SKY[1:]):
        if a0 <= alt <= a1:
            f = (alt - a0) / float(a1 - a0)
            return [_mix(c0[k], c1[k], f) for k in range(3)]
    return list(_SKY[-1][1])


def _light(alt):
    """0 ночью, 1 днём - для холмов, облаков и прочего, что освещено."""
    return max(0.0, min(1.0, (alt + 6) / 26.0))


# ------------------------------------------------------------------- сцена
def _r(v):
    return round(v, 1)


def scene(w, h, now=None, lat=None, lon=None, facing=None, weather=None, seed="", fs=1.0):
    """
    Всё, что видно в окне, в координатах плитки w x h.

    Шаблону уходят готовые числа и цвета: градиенты (sky, glow, cloud)
    объявляются у самой плитки - цвета у них свои на каждое время суток.
    """
    now = time.time() if now is None else now
    lat, lon = (DEFAULT_LOCATION if lat is None else (lat, lon))
    facing = DEFAULT_FACING if facing is None else facing
    wx = weather or {"clouds": 0.0, "rain": 0.0, "snow": 0.0, "fog": 0.0, "storm": 0.0}
    clouds = wx.get("clouds") or 0.0
    rnd = random.Random(zlib.crc32(("%s|%dx%d" % (seed, w, h)).encode("utf-8")))
    S = min(w, h)
    # Плотность звёзд и осадков - на плитку обычного размера: на весь
    # экран их не должно становиться в десять раз больше, только крупнее.
    k2 = max(1.0, S / 170.0) ** 2
    horizon = h * 0.74

    s_alt, s_az = sun_position(now, lat, lon)
    m_alt, m_az, age = moon_position(now, lat, lon)

    top, mid, bot = sky_colors(s_alt)
    # Пасмурно - небо серее; в грозу ещё и темнее.
    if clouds > 0.35:
        g = min(1.0, (clouds - 0.35) / 0.6) * 0.85 * (0.5 + 0.5 * _light(s_alt))
        top, mid, bot = _mix(top, "#7A818A", g), _mix(mid, "#8A9097", g), _mix(bot, "#9AA0A6", g)
    if wx.get("storm"):
        top, mid, bot = _mix(top, "#2A2E36", .45), _mix(mid, "#3A3F48", .45), _mix(bot, "#4A505A", .4)

    def place(alt, az):
        dx = ((az - facing + 540) % 360) - 180
        x = w * (0.5 + dx / FOV)
        y = horizon - (alt / TOP_ALT) * (horizon - h * 0.12)
        return x, max(h * 0.12, y), dx

    out = {"sky": [top, mid, bot], "horizon": _r(horizon)}

    # Солнце: диск и свечение. За облаками - только свечение, слабее.
    sun = None
    if s_alt > -4:
        x, y, dx = place(s_alt, s_az)
        r = S * 0.07
        if abs(dx) < 90 and -3 * r < x < w + 3 * r:
            warm = _mix("#FFC07A", "#FFF4DA", (s_alt - 2) / 16.0)
            vis = 1.0 - 0.85 * clouds
            sun = {"x": _r(x), "y": _r(y), "r": _r(r), "g": _r(r * 3.4),
                   "color": warm, "disc": round(max(0.0, vis) * min(1.0, (s_alt + 4) / 4.0), 2),
                   "glow": round(0.55 * (0.4 + 0.6 * (1 - clouds)), 2)}
    out["sun"] = sun

    # Луна: фаза контуром - светлый край полукругом, терминатор эллипсом.
    moon = None
    if m_alt > -2 and s_alt < 10:
        x, y, dx = place(m_alt, m_az)
        r = S * 0.045
        if abs(dx) < 90 and -2 * r < x < w + 2 * r:
            lit = (1 - math.cos(2 * math.pi * age)) / 2
            if lit > 0.03:
                waxing = age < 0.5
                a = age if waxing else 1 - age
                s = 1 if waxing else -1
                tx = r * math.cos(2 * math.pi * a)
                term_x = s * tx
                path = ("M %.1f,%.1f A %.1f,%.1f 0 0 %d %.1f,%.1f "
                        "A %.1f,%.1f 0 0 %d %.1f,%.1f Z") % (
                    x, y - r, r, r, 1 if s > 0 else 0, x, y + r,
                    max(abs(tx), 0.1), r, 0 if term_x > 0 else 1, x, y - r)
                day = max(0.0, min(1.0, (s_alt + 2) / 10.0))
                op = (1 - 0.8 * clouds) * (1 - 0.55 * day)
                moon = {"x": _r(x), "y": _r(y), "r": _r(r), "g": _r(r * 3.2),
                        "path": path, "op": round(op, 2),
                        "glow": round(0.22 * lit * (1 - clouds) * (1 - day), 2)}
    out["moon"] = moon

    # Звёзды - в тёмных сумерках и ночью, сквозь облака не видно.
    stars = []
    vis = max(0.0, min(1.0, (-s_alt - 6) / 8.0)) * (1 - clouds)
    if vis > 0.05:
        for _ in range(int(w * h / 1400 / k2)):
            stars.append({"x": _r(rnd.random() * w), "y": _r(rnd.random() * horizon * 0.85),
                          "r": rnd.choice((0.6, 0.8, 1.1)) * fs,
                          "op": round(vis * rnd.uniform(0.45, 0.95), 2)})
    else:
        for _ in range(int(w * h / 1400 / k2)):
            rnd.random(), rnd.random(), rnd.random(), rnd.random()
    out["stars"] = stars

    # Холмы - силуэт горизонта, без него небо читается просто заливкой.
    light = _light(s_alt)
    h1 = _mix("#262538", "#5E7A6A", light)
    h2 = _mix("#17161F", "#45604F", light)
    if wx.get("snow"):
        h1, h2 = _mix(h1, "#C9D2DC", .25 + .35 * light), _mix(h2, "#B4BEC9", .2 + .35 * light)
    out["hills"] = [
        {"d": "M 0,%.1f C %.1f,%.1f %.1f,%.1f %.1f,%.1f S %.1f,%.1f %.1f,%.1f V %.1f H 0 Z" % (
            h * .79, w * .25, h * .67, w * .45, h * .73, w * .62, h * .71, w * .9, h * .65,
            w, h * .71, h), "fill": h1},
        {"d": "M 0,%.1f C %.1f,%.1f %.1f,%.1f %.1f,%.1f S %.1f,%.1f %.1f,%.1f V %.1f H 0 Z" % (
            h * .88, w * .2, h * .80, w * .5, h * .90, w * .7, h * .84, w * .9, h * .80,
            w, h * .86, h), "fill": h2},
    ]

    # Облака: гроздья эллипсов с мягким краем (радиальный градиент).
    cl = []
    if clouds > 0.05:
        day_c = _mix("#FFFFFF", "#BFC5CC", (clouds - 0.55) * 2.2)
        dusk = max(0.0, 1 - abs(s_alt - 1) / 9.0)
        color = _mix(_mix("#3E4560", day_c, light), "#E6B4A4", dusk * 0.7)
        n = int(1 + clouds * 6)
        for _ in range(n):
            cx = rnd.uniform(-0.1, 1.1) * w
            cy = rnd.uniform(0.08, 0.55) * horizon
            for k in range(rnd.choice((3, 4))):
                cl.append({"cx": _r(cx + (k - 1.5) * S * 0.10), "cy": _r(cy + rnd.uniform(-1, 1) * S * 0.025),
                           "rx": _r(rnd.uniform(0.11, 0.17) * S), "ry": _r(rnd.uniform(0.055, 0.085) * S)})
        out["cloud_color"] = color
        out["cloud_op"] = round(0.55 + 0.4 * clouds, 2)
    out["clouds"] = cl

    # Осадки и туман - поверх всего, «на стекле».
    rain = []
    for _ in range(int((wx.get("rain") or 0) * w * h / 600 / k2)):
        x, y = rnd.random() * w, rnd.random() * h
        rain.append({"x1": _r(x), "y1": _r(y), "x2": _r(x - 2.5 * fs), "y2": _r(y + 9 * fs)})
    out["rain"] = rain
    snow = []
    for _ in range(int((wx.get("snow") or 0) * w * h / 520 / k2)):
        snow.append({"x": _r(rnd.random() * w), "y": _r(rnd.random() * h),
                     "r": rnd.choice((1.0, 1.4, 1.9)) * fs})
    out["snow"] = snow
    out["fog"] = round(wx.get("fog") or 0.0, 2)
    out["fog_color"] = _mix("#3A3F4A", "#C8CDD3", light)
    out["rain_color"] = _mix("#8A9BB0", "#DCE8F4", light)

    # Для подписи и отладки: что там сейчас.
    out["sun_alt"] = round(s_alt, 1)
    out["moon_age"] = round(age, 3)
    return out


# ---------------------------------------------------------------- шторы
def drapes(w, h, open_l, open_r, fs=1.0):
    """Раздвижные: две полосы от краёв, складки сжимаются к краю.

    Ручка - на ведущей кромке правой створки, как у настоящих."""
    half = w / 2.0
    gather = max(8.0, w * 0.07)               # собранная у стены ткань
    n = max(3, int(round(half / (min(w, h) * 0.085))))
    out = []
    for side, op in (("l", open_l), ("r", open_r)):
        pw = max(gather, half * (1 - op))
        x0 = 0.0 if side == "l" else w - pw
        sw = pw / n
        out.append({"x": _r(x0), "w": _r(pw),
                    "stripes": [_r(x0 + k * sw) for k in range(n)], "sw": _r(sw + 0.4),
                    "edge": _r(x0 + pw if side == "l" else x0)})
    r = out[1]
    handle = {"x": _r(r["x"] - 2.5 * fs), "y": _r(h * 0.36), "w": _r(5 * fs), "h": _r(h * 0.2)}
    return {"panels": out, "handle": handle}


def roman(w, h, open_, fs=1.0):
    """Римская: полотно сверху, внизу - складки с провисом."""
    fold = max(14.0 * fs, h * 0.11)
    drop = max(h * (1 - open_), fold * 0.75)
    n = max(1, int(round(drop / fold)))
    fh = drop / n
    sag = min(7.0 * fs, fh * 0.28)
    folds = []
    for i in range(n):
        y = i * fh
        d = ("M 0,%.1f Q %.1f,%.1f %.1f,%.1f L %.1f,%.1f Q %.1f,%.1f 0,%.1f Z" % (
            y - 2, w / 2, y + 2, w, y - 2, w, y + fh, w / 2, y + fh + sag, y + fh))
        folds.append(d)
    hw = max(22.0 * fs, w * 0.13)
    return {"folds": folds, "drop": _r(drop + sag),
            "handle": {"x": _r(w / 2 - hw / 2), "y": _r(drop + sag + 4 * fs), "w": _r(hw),
                       "h": _r(5 * fs)}}
