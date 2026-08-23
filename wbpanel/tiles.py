# -*- coding: utf-8 -*-
"""
Сборка данных для каждого типа плитки.
"""

import math
import re
import time
from .const import BAND, STALE_AFTER, log
from .state import to_float
from .geometry import (CHAR_W, _fmt, cct_color, darken, edge_anchor,
                       parse_duration,
                       field_color, grid_bounds, hour_ticks,
                       label_digits, nice_step, smooth_path, text_width)


# Разрыв внизу увеличен со 90 до 116 градусов: строка «сейчас 25.5°» шире,
# чем казалось на макетах, и почти касалась дуги. Заодно круг стал крупнее,
# а нижняя подпись перестала выглядеть оторванной от него.
DIAL_START = 148.0   # градусы: нижний левый край дуги


DIAL_SWEEP = 244.0


def dial_point(cx, cy, r, deg):
    rad = math.radians(deg)
    return (cx + r * math.cos(rad), cy + r * math.sin(rad))


def dial_arc(cx, cy, r, deg_from, deg_to):
    """Дуга окружности как path d. Пустая строка, если сектор вырожденный."""
    if deg_to - deg_from < 0.35:
        return ""
    x0, y0 = dial_point(cx, cy, r, deg_from)
    x1, y1 = dial_point(cx, cy, r, deg_to)
    large = 1 if (deg_to - deg_from) > 180 else 0
    return "M %.2f,%.2f A %.2f,%.2f 0 %d 1 %.2f,%.2f" % (x0, y0, r, r, large, x1, y1)


def build_dial(width, height, lo, hi, current, target):
    """
    Циферблат термостата: незамкнутая дуга со шкалой lo…hi.

    Закрашен участок МЕЖДУ фактической температурой и уставкой - то есть
    ровно тот разрыв, который прибору предстоит закрыть. Две засечки на
    дуге показывают обе границы, снизу в разрыве дуги остаётся место
    под режим работы.
    """
    span = max(float(hi) - float(lo), 1e-6)
    cx = width / 2.0
    # Центр опущен, радиус увеличен: так нижние концы дуги подходят близко
    # к строке режима и она перестаёт выглядеть отдельной от циферблата.
    cy = height * 0.59
    r = min(width, height) * 0.36

    def deg(value):
        f = (float(value) - float(lo)) / span
        return DIAL_START + max(0.0, min(1.0, f)) * DIAL_SWEEP

    out = {
        "cx": round(cx, 1), "cy": round(cy, 1), "r": round(r, 1),
        "track": dial_arc(cx, cy, r, DIAL_START, DIAL_START + DIAL_SWEEP),
        "fill": "", "marks": [],
    }

    pts = [v for v in (current, target) if v is not None]
    if len(pts) == 2:
        a, b = sorted(deg(v) for v in pts)
        out["fill"] = dial_arc(cx, cy, r, a, b)
    elif len(pts) == 1:
        a = deg(pts[0])
        out["fill"] = dial_arc(cx, cy, r, DIAL_START, a)

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


def command_topic(channel, explicit=None):
    """
    Топик, в который пишут команду для канала.

    Wiren Board: значение приходит в /devices/<dev>/controls/<ctl>, а команда
    уходит в тот же топик с суффиксом /on - писать в топик состояния нельзя,
    его перетрёт драйвер.

    У сторонних шлюзов соглашение своё, поэтому для сырых топиков команду
    задают явно полем command_topic - угадывать тут нечего.
    """
    if explicit:
        return explicit
    if not channel or channel.count("/") != 1:
        return None
    device, control = channel.split("/", 1)
    return "/devices/%s/controls/%s/on" % (device, control)


# HomeKit: 0 - выключено, 1 - нагрев, 2 - охлаждение, 3 - авто
HEAT_MODE = {0: "Выключен", 1: "Нагрев", 2: "Охлаждение", 3: "Авто"}


# Что показываем, когда прибор включён, но прямо сейчас не работает: уставка
# достигнута и головка закрыта. «Ожидание» звучало двусмысленно - будто
# термостат чего-то ждёт от нас.
HEAT_NOW = {0: "Не греет", 1: "Греет", 2: "Охлаждает"}


def forecast_channels(tile):
    """
    Каналы виртуального устройства погоды.

    Скрипт wb-rules публикует их пачкой с общим префиксом, поэтому в конфиге
    достаточно указать device: weather - перечислять полтора десятка имён
    руками не нужно.
    """
    dev = tile.get("device", "weather")
    hours = int(tile.get("hours", 12))
    out = ["%s/%s" % (dev, n) for n in
           ("temperature", "description", "humidity", "pressure",
            "windspeed", "temp_min_12h", "temp_max_12h", "forecast_ok")]
    out += ["%s/temp_%02d" % (dev, i) for i in range(hours)]
    return out


# Коды погоды WMO, которые отдаёт Open-Meteo, - в значки.
# Диапазоны, а не перечисление: коды сгруппированы по десяткам, и внутри
# группы разница только в силе явления, а значок один и тот же.
WMO_ICONS = (
    ((0,), "sun"),
    ((1, 2), "cloud-sun"),
    ((3,), "cloud"),
    ((45, 48), "fog"),
    ((51, 53, 55, 56, 57), "rain"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    ((71, 73, 75, 77, 85, 86), "snow"),
    ((95, 96, 99), "storm"),
)


def wmo_icon(code, day=True):
    if code is None:
        return "sun" if day else "moon"
    code = int(code)
    for codes, name in WMO_ICONS:
        if code in codes:
            if name == "sun" and not day:
                return "moon"
            return name
    return "cloud"


def parse_series(raw, limit=24):
    """'13.3,13.2,...' -> [13.3, 13.2, ...]. Пропуски становятся None."""
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            out.append(None)
            continue
        out.append(to_float(part))
    return out[:limit]


def parse_iso_hour(raw):
    """'2026-08-20T03:00' -> 3. Нужен, чтобы подписать часы от начала прогноза."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}):", str(raw or "").strip())
    return int(m.group(1)) if m else None


def build_wheel(width, height, lo, hi, value, span=4.0):
    """
    Пологая дуга-«улыбка» со шкалой - та же, что в пульте.

    Плитка кондиционера рисуется ею, а не циферблатом термостата: приборы
    разные, и по виду это должно быть видно сразу.
    """
    if value is None:
        return None
    lo, hi = float(lo), float(hi)
    value = max(lo, min(hi, float(value)))
    px_deg = width / (span + 1.0)
    r = width * 1.9
    cx = width / 2.0
    # Дуга по центру плитки: выбранное значение на ней и есть главная
    # цифра, отдельной крупной уставки под ней больше нет.
    bottom = height * 0.56
    cy = bottom - r

    def pt(delta, radius=None):
        a = (delta * px_deg) / r
        rr = r if radius is None else radius
        return (cx + rr * math.sin(a), cy + rr * math.cos(a), a)

    def arc(radius, half):
        p0, p1 = pt(-half, radius), pt(half, radius)
        return "M %.1f,%.1f A %.0f,%.0f 0 0 0 %.1f,%.1f" % (
            p0[0], p0[1], radius, radius, p1[0], p1[1])

    ticks, labels = [], []
    step = 0.5 if span <= 5 else 1.0
    v = math.ceil((value - span / 2) / step) * step
    while v <= value + span / 2 + 1e-6:
        if lo - 1e-6 <= v <= hi + 1e-6:
            x, y, a = pt(v - value)
            whole = abs(v - round(v)) < 0.05
            ln = 9 if whole else 5
            ticks.append({"x1": round(x, 1), "y1": round(y, 1),
                          "x2": round(x + math.sin(a) * ln, 1),
                          "y2": round(y + math.cos(a) * ln, 1),
                          "major": whole})
            if whole:
                labels.append({"x": round(x - math.sin(a) * 15, 1),
                               "y": round(y - math.cos(a) * 15, 1),
                               "text": "%d°" % round(v),
                               "near": abs(v - value) < 0.3})
        v += step
    return {"arc": arc(r, span / 2), "ticks": ticks, "labels": labels,
            "cx": round(cx, 1), "cy": round(bottom, 1)}


# Мост wb-haier публикует эти контролы у каждого кондиционера.
AC_CONTROLS = ("power", "mode", "current_temp", "target_temp", "fan_mode",
               "quiet", "turbo", "preset", "swing_mode", "swing_h_mode", "online")


AC_MODE_RU = {"cool": "Охлаждение", "heat": "Нагрев", "dry": "Осушение",
              "fan_only": "Вентиляция", "auto": "Авто", "off": "Выключен"}


AC_MODE_ICON = {"cool": "snow", "heat": "heat", "dry": "drop",
                "fan_only": "fan", "auto": "power", "off": "power"}


AC_FAN_RU = {"low": "1", "medium": "2", "high": "3", "auto": "Авто"}


# Положение лопастей в градусах наклона. Значения - те, что отдаёт мост.
AC_SWING_DEG = {"upper": -38, "position_2": -19, "position_3": 0,
                "position_4": 19, "bottom": 38}


# Горизонтальные жалюзи: номера НЕ идут слева направо, порядок проверен
# на живом устройстве. Единица - это центр, а не крайнее положение.
#   4 лево · 5 лево-центр · 1 центр · 6 центр-право · 7 право
AC_SWING_H_DEG = {"position_4": -42, "position_5": -21, "position_1": 0,
                  "position_6": 21, "position_7": 42}


def louver_glyph(value, table, vertical=True, size=19.0):
    """
    Пиктограмма положения лопастей: полоски наклонены так же, как реальные
    жалюзи. «Авто» - веер из трёх, потому что они качаются.

    Возвращает None, когда положение неизвестно или жалюзи выключены -
    рисовать тогда нечего.
    """
    value = (value or "").strip()
    if value in ("", "off", "0"):
        return None
    if value == "auto":
        angles = [(-24, .35), (0, .6), (24, .35)]
    elif value in table:
        angles = [(table[value], 1.0)]
    else:
        return None

    blades = []
    half = size * 0.42
    for idx, (deg, op) in enumerate(angles):
        rad = math.radians(deg)
        # для горизонтальных жалюзи вид сверху: полоска поворачивается
        # вокруг той же точки, только оси поменяны местами
        cx = size / 2.0
        cy = size * (0.62 if vertical else 0.5)
        dx = half * math.cos(rad)
        dy = half * math.sin(rad)
        if not vertical:
            dx, dy = -dy, dx
        if len(angles) > 1:
            shift = (idx - 1) * (2.4 if vertical else 0)
            cy += shift
        blades.append({"x1": round(cx - dx, 1), "y1": round(cy - dy, 1),
                       "x2": round(cx + dx, 1), "y2": round(cy + dy, 1),
                       "op": op})
    return {"blades": blades}


def ac_channels(tile):
    """Каналы кондиционера по префиксу устройства - как у погоды."""
    dev = tile.get("device")
    if not dev:
        return []
    return ["%s/%s" % (dev, name) for name in AC_CONTROLS]


def from_registry(conf, state, history=None):
    """
    Мост между старым вызовом и новым контрактом.

    Сборщики зовутся как build_xxx(tile, state) и возвращают словарь для
    шаблона. Плитки на ролях устроены иначе, поэтому переводим здесь -
    остальной код о переезде знать не должен.
    """
    from .registry import build as build_by_roles

    result = build_by_roles(conf, state, history)
    data = dict(result["data"])
    data["snaps"] = result["snaps"]
    if result["problem"]:
        log.warning("плитка %r: %s", conf.get("title") or conf.get("type"),
                    result["problem"])
    # Зоны и разрешения понадобятся, когда на роли переедет слой команд;
    # пока кладём рядом, чтобы не потерять.
    data["_zones"] = result["zones"]
    data["_writable"] = result["writable"]
    return data


# Таблица оставлена ради совместимости: снаружи её всё ещё читают, но
# теперь все типы идут одним путём через реестр, и особых случаев нет.
BUILDERS = {
    "chart": from_registry,
    "switch": from_registry,
    "curtain": from_registry,
    "light": from_registry,
    "dimmer": from_registry,
    "thermostat": from_registry,
    "forecast": from_registry,
    "ac": from_registry,
    "link": from_registry,
    "value": from_registry,
    "header": from_registry,
}


# Регистрация типов на новом контракте. Импорт в самом низу намеренно:
# tiles_basic тянет registry и geometry, а те - ничего отсюда, кольца не
# возникает. Без этой строки реестр пуст и BUILDERS не найдёт плитку.
from . import tiles_basic   # noqa: E402,F401
from . import tiles_devices  # noqa: E402,F401
from . import tiles_devicewide  # noqa: E402,F401
