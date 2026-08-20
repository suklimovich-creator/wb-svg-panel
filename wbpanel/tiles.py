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


def build_chart(tile, state, history):
    span = parse_duration(tile.get("range", "24h"))
    points = int(tile.get("points", 90))
    specs = [s for s in (tile.get("series", []) or []) if s.get("channel")]

    # 1. сначала забираем все ряды, чтобы посчитать общую сетку
    fetched = []
    for spec in specs:
        snap = state.snapshot(spec["channel"])
        data = history.get(spec["channel"], span, points)
        fetched.append((spec, snap, data))

    # 2. шкала каждого ряда выравнивается по своему шагу (°C - 5, % - 10),
    #    а число делений берём общее: тогда горизонтальные линии совпадут
    scales = []
    for spec, snap, data in fetched:
        values = [v for _, v in data]
        if spec.get("min") is not None and spec.get("max") is not None:
            lo, hi = float(spec["min"]), float(spec["max"])
            step = float(spec.get("grid") or nice_step(lo, hi))
            n = max(int(round((hi - lo) / step)), 1)
            scales.append((lo, hi, step, n))
        else:
            scales.append(grid_bounds(values, spec.get("grid"),
                                      spec.get("unit", snap["units"])))
    divisions = max([s[3] for s in scales], default=0)
    divisions = min(max(divisions, 2), 6)          # 2..6 линий - читаемо

    ticks = []
    series_out = []
    for idx, ((spec, snap, data), scale) in enumerate(zip(fetched, scales)):
        values = [v for _, v in data]
        if spec.get("min") is not None and spec.get("max") is not None:
            lo, hi, gstep = scale[0], scale[1], scale[2]
        else:
            lo, hi, gstep, _n = grid_bounds(values, spec.get("grid"),
                                            spec.get("unit", snap["units"]),
                                            divisions=divisions)
        if data and not ticks:
            ticks = hour_ticks(data[0][0], data[-1][0], tile["inner_w"], pad_x=3.0)
        raw = to_float(snap["raw"])
        geom = smooth_path(data, tile["inner_w"], tile["chart_h"], lo=lo, hi=hi)
        color = spec.get("color", ["#E08A2D", "#3B93C4", "#8E8E93"][idx % 3])
        # Пороги: цвет линии по текущему значению. Задаются от меньшего к
        # большему, побеждает последний подошедший.
        for rule in (spec.get("thresholds") or []):
            try:
                if raw is not None and raw >= float(rule["above"]):
                    color = rule.get("color", color)
            except (KeyError, TypeError, ValueError):
                continue
        series_out.append({
            "color": color,
            "text_color": spec.get("text_color") or darken(color),
            "unit": spec.get("unit", snap["units"]),
            "label": spec.get("label", ""),
            "value": _fmt(raw, int(spec.get("digits", 1))),
            "has_value": raw is not None,
            "geom": geom,
            "step": gstep,
            "npoints": len(data),
        })

    # 3. сами линии: одинаковые доли высоты для всех рядов
    hlines = []
    if any(s["geom"] for s in series_out):
        y_top = tile["chart_h"] * BAND[0]
        y_bot = tile["chart_h"] * BAND[1]
        for i in range(divisions + 1):
            f = i / float(divisions)
            hlines.append({"y": round(y_bot - f * (y_bot - y_top), 1),
                           "f": f, "edge": i in (0, divisions)})

    # 4. подписи: без них не понять, что шкала растянута под сами данные -
    #    провал на полградуса выглядит как обвал
    with_time = bool(tile.get("time_labels", False))
    for s in series_out:
        s["labels"] = []
        if not s["geom"]:
            continue
        lo, hi = s["geom"]["lo"], s["geom"]["hi"]
        digits = label_digits(s.get("step") or ((hi - lo) / max(len(hlines) - 1, 1)))
        for hl in hlines:
            # Нижнюю границу показываем всегда: без неё непонятно, от чего
            # отсчитывается шкала. Раньше её прятали ради строки часов -
            # теперь вместо этого убираются крайние подписи часов, они и
            # были причиной наложения.
            s["labels"].append({"y": hl["y"],
                                "text": _fmt(lo + hl["f"] * (hi - lo), digits)})
        s["span"] = "%s–%s %s" % (_fmt(lo, digits), _fmt(hi, digits), s["unit"])

    # Часы. Крайние подписи прижимаются к самым краям плитки - туда же, где
    # стоят подписи шкал слева и справа. Пока шкал нет, это нормально; когда
    # есть, крайние часы накрывают границы шкалы, и понять её невозможно.
    # Часов на графике много, потеря двух крайних ничего не стоит.
    with_grid = bool(tile.get("grid_labels", False))
    times = []
    for tk in ticks:
        if not tk["major"]:
            continue
        lx, anchor = edge_anchor(tk["x"], tile["inner_w"])
        if with_grid and anchor != "middle":
            continue
        times.append({"x": round(lx, 1), "anchor": anchor,
                      "text": "%02d:00" % tk["hour"]})

    note = ""
    if series_out and series_out[0].get("span"):
        note = series_out[0]["span"]

    return {"series": series_out, "ticks": ticks, "hlines": hlines,
            "times": times, "scale_note": note,
            "title_dx": round(20 + text_width(tile.get("title", ""), 15) + 9, 1),
            "grid_labels": bool(tile.get("grid_labels", False)),
            "time_labels": bool(tile.get("time_labels", False)),
            "show_span": bool(tile.get("show_span", False)),
            "range_label": tile.get("range", "24h")}


def build_switch(tile, state):
    snap = state.snapshot(tile.get("channel", ""))
    raw = (snap["raw"] or "").strip()
    on = raw not in ("", "0", "false", "False")

    # invert - для нормально замкнутых контактов: реле обесточено, а нагрузка
    # включена. Единица в канале означает "выключено". Инвертируем только
    # известное значение: нет данных - значит нет данных, а не "включено".
    if tile.get("invert") and raw != "":
        on = not on

    return {
        "on": on,
        "status": tile.get("on_text", "Вкл") if on else tile.get("off_text", "Выкл"),
        "icon": tile.get("icon", "toggle"),
        "short": tile.get("badge", ""),
        "snap": snap,
    }


def build_curtain(tile, state):
    """
    Раздвижные шторы. Две створки едут от краёв к центру.
    open_pct = 100 - полностью раздвинуты, 0 - закрыты.
    Если заданы channel_left/channel_right - створки независимые.
    """
    snaps = []
    if tile.get("channel_left") or tile.get("channel_right"):
        left_snap = state.snapshot(tile.get("channel_left", ""))
        right_snap = state.snapshot(tile.get("channel_right", ""))
        left = to_float(left_snap["raw"])
        right = to_float(right_snap["raw"])
        snaps = [left_snap, right_snap]
    else:
        snap = state.snapshot(tile.get("channel", ""))
        left = right = to_float(snap["raw"])
        snaps = [snap]

    if tile.get("inverted"):
        left = None if left is None else 100 - left
        right = None if right is None else 100 - right

    avg = [v for v in (left, right) if v is not None]
    avg = sum(avg) / len(avg) if avg else None

    # Необязательный канал состояния (у HomeKit-совместимых приводов
    # PositionState: 0 - закрывается, 1 - открывается, 2 - стоит).
    moving = None
    if tile.get("channel_state"):
        st_snap = state.snapshot(tile["channel_state"])
        snaps.append(st_snap)
        code = to_float(st_snap["raw"])
        if code is not None and int(code) in (0, 1):
            moving = "Закрывается" if int(code) == 0 else "Открывается"

    return {
        "moving": moving,
        "left_open": 0.0 if left is None else max(0.0, min(100.0, left)) / 100.0,
        "right_open": 0.0 if right is None else max(0.0, min(100.0, right)) / 100.0,
        "known": avg is not None,
        "on": bool(avg and avg > 1),
        "status": moving or (("Открыто %d%%" % round(avg)) if avg is not None
                             else "Нет данных"),
        "short": ("%d %%" % round(avg)) if avg is not None else "",
        "snaps": snaps,
    }


def build_light(tile, state):
    """
    Светодиодная лента: три величины на одной плитке.

      по горизонтали  - цветовая температура: слева холодный, справа тёплый
      по вертикали    - яркость: сверху ярко, снизу темно
      точка           - фактическое положение по обеим осям
      оранжевая рамка - включено

    Плитка целиком залита этим полем, поэтому её вид сам по себе читается
    как «тёплый приглушённый» или «холодный яркий», а точка даёт точное
    значение.
    """
    b_snap = state.snapshot(tile.get("channel_brightness", ""))
    t_snap = state.snapshot(tile.get("channel_temp", ""))
    snaps = [b_snap, t_snap]

    brightness = to_float(b_snap["raw"])
    b_max = to_float(b_snap["meta"].get("max"), 100.0) or 100.0
    if brightness is not None and b_max:
        brightness = max(0.0, min(100.0, brightness / b_max * 100.0))

    on = brightness is not None and brightness > 0
    if tile.get("channel_switch"):
        s_snap = state.snapshot(tile["channel_switch"])
        snaps.append(s_snap)
        raw = (s_snap["raw"] or "").strip()
        on = raw not in ("", "0", "false", "False")

    k_min = float(tile.get("temp_min", 2700))
    k_max = float(tile.get("temp_max", 6500))
    temp_raw = to_float(t_snap["raw"])
    kelvin = None
    if temp_raw is not None:
        if str(tile.get("temp_unit", "kelvin")).lower() == "percent":
            t_max = to_float(t_snap["meta"].get("max"), 100.0) or 100.0
            kelvin = k_min + (k_max - k_min) * max(0.0, min(1.0, temp_raw / t_max))
        else:
            kelvin = temp_raw

    # положение точки: 0 слева/сверху, 1 справа/снизу
    if kelvin is None:
        dot_x = None
    else:
        # слева холодный (k_max), справа тёплый (k_min)
        dot_x = (k_max - max(k_min, min(k_max, kelvin))) / max(k_max - k_min, 1.0)
    dot_y = None if brightness is None else 1.0 - brightness / 100.0

    bits = []
    if brightness is not None:
        bits.append("%d %%" % round(brightness))
    if kelvin is not None:
        bits.append("%d K" % round(kelvin))
    return {
        "on": on,
        "dot_x": dot_x,
        "dot_y": dot_y,
        "has_dot": dot_x is not None and dot_y is not None,
        "cold": tile.get("cold_color") or field_color(k_max),
        "warm": tile.get("warm_color") or field_color(k_min),
        "color": field_color(kelvin if kelvin is not None else 3000),
        "short": " · ".join(bits),
        # на узкой плитке (w: 1) полная подпись не влезает
        "short_min": bits[0] if bits else "",
        "status": ("Включено" if on else "Выключено") if brightness is not None
                  else "Нет данных",
        "snaps": snaps,
    }


def build_dimmer(tile, state):
    """
    Диммируемый светильник: заполнение плитки снизу вверх на высоту яркости.

    В отличие от ленты здесь одна величина, поэтому не поле, а простая
    заливка одним цветом. Цвет по умолчанию - приглушённый тёплый; для
    холодного света поставьте свой в `color`.
    """
    b_snap = state.snapshot(tile.get("channel_brightness", ""))
    snaps = [b_snap]

    brightness = to_float(b_snap["raw"])
    b_max = to_float(b_snap["meta"].get("max"), 100.0) or 100.0
    if brightness is not None and b_max:
        brightness = max(0.0, min(100.0, brightness / b_max * 100.0))

    on = brightness is not None and brightness > 0
    if tile.get("channel"):
        s_snap = state.snapshot(tile["channel"])
        snaps.append(s_snap)
        raw = (s_snap["raw"] or "").strip()
        on = raw not in ("", "0", "false", "False")

    return {
        "on": on,
        "fill": 0.0 if (not on or brightness is None) else brightness / 100.0,
        "color": tile.get("color", "#E0A050"),
        "short": ("%d %%" % round(brightness)) if brightness is not None else "",
        "status": ("Включено" if on else "Выключено") if brightness is not None
                  else "Нет данных",
        "snaps": snaps,
    }


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


def build_thermostat(tile, state):
    """
    Термостат: крупно уставка, в углу фактическая температура.

    Оранжевая рамка загорается, когда прибор РЕАЛЬНО работает
    (CurrentHeatingCoolingState = 1), а не когда просто включён режим
    нагрева - иначе рамка горела бы круглосуточно и ничего не значила.
    """
    cur_snap = state.snapshot(tile.get("channel_current", ""))
    tgt_snap = state.snapshot(tile.get("channel_target", ""))
    snaps = [cur_snap, tgt_snap]

    current = to_float(cur_snap["raw"])
    target = to_float(tgt_snap["raw"])

    working = None
    if tile.get("channel_state"):
        st_snap = state.snapshot(tile["channel_state"])
        snaps.append(st_snap)
        code = to_float(st_snap["raw"])
        working = int(code) if code is not None else None

    mode = None
    if tile.get("channel_mode"):
        md_snap = state.snapshot(tile["channel_mode"])
        snaps.append(md_snap)
        code = to_float(md_snap["raw"])
        mode = int(code) if code is not None else None

    if mode == 0:
        status = HEAT_MODE[0]
    elif working is not None:
        status = HEAT_NOW.get(working, HEAT_MODE.get(mode, ""))
    else:
        status = HEAT_MODE.get(mode, "")

    # насколько далеко до уставки - для полоски прогрева
    reach = None
    if current is not None and target is not None:
        delta = target - current
        reach = max(0.0, min(1.0, 1.0 - abs(delta) / 3.0))

    dial = None
    if tile.get("dial", True):
        dial = build_dial(tile.get("inner_w") or 170, tile.get("chart_h") or 170,
                          tile.get("scale_min", 14), tile.get("scale_max", 30),
                          current, target)

    # Знак градуса не должен участвовать в центрировании, иначе число
    # визуально уезжает влево. Считаем ширину числа и ставим "°" за ним.
    target_num = _fmt(target, int(tile.get("digits", 0))) if target is not None else "--"
    big = 30.0
    deg_dx = text_width(target_num, big) / 2.0 + 1.0

    return {
        "dial": dial,
        "target_num": target_num,
        "deg_dx": round(deg_dx, 1),
        "deg_size": round(big * 0.60, 1),
        "on": working == 1,
        "enabled": mode != 0,
        "target": ("%s°" % target_num) if target is not None else "--",
        "current": ("%s°" % _fmt(current, 1)) if current is not None else "",
        "short": ("%s°" % _fmt(current, 1)) if current is not None else "",
        "reach": reach,
        "status": status or "Нет данных",
        "always_status": True,
        "snaps": snaps,
    }


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


def build_forecast(tile, state):
    """
    Погода: слева текущая, дальше кривая прогноза по часам.

    Данные не из базы истории, а из каналов виртуального устройства - это
    прогноз, а не прошлое, и wb-mqtt-db тут ни при чём.

    Ряды приходят одной строкой на 24 значения (temp_series, code_series,
    precip_prob_series, cloud_series). Старый формат с отдельными каналами
    temp_00…temp_NN поддерживается как запасной: у кого-то устройство ещё
    не обновлено.
    """
    dev = tile.get("device", "weather")
    hours = int(tile.get("hours", 12))
    snaps = []

    def val(name):
        snap = state.snapshot("%s/%s" % (dev, name))
        snaps.append(snap)
        return snap

    cur = to_float(val("temperature")["raw"])
    desc_snap = val("description")
    hum = to_float(val("humidity")["raw"])
    press = to_float(val("pressure")["raw"])
    wind = to_float(val("windspeed")["raw"])
    ok_raw = (val("forecast_ok")["raw"] or "").strip()

    temps = parse_series(val("temp_series")["raw"], hours)
    codes = parse_series(val("code_series")["raw"], hours)
    probs = parse_series(val("precip_prob_series")["raw"], hours)
    rains = parse_series(val("precip_series")["raw"], hours)
    clouds = parse_series(val("cloud_series")["raw"], hours)
    start_hour = parse_iso_hour(val("forecast_start")["raw"])

    now = time.time()
    if not [v for v in temps if v is not None]:
        # Запасной путь: устройство старого образца, ряды не публикует
        temps = []
        for i in range(hours):
            snap = state.snapshot("%s/temp_%02d" % (dev, i))
            snaps.append(snap)
            temps.append(to_float(snap["raw"]))

    points = [(now + i * 3600.0, v)
              for i, v in enumerate(temps) if v is not None]

    inner_w = tile.get("inner_w") or 354
    chart_h = tile.get("chart_h") or 170
    pad_x = 3.0

    geom, hlines, times, marks, ticks = None, [], [], [], []
    bars, sky, icons = [], [], []
    if len(points) >= 2:
        values = [v for _, v in points]
        # та же сетка, что у обычных графиков: шаг от размаха, 2-5 делений
        _lo, _hi, _step, n = grid_bounds(values, tile.get("grid"), "°C", target=3)
        divisions = max(2, min(n, 5))
        lo, hi, step, _n = grid_bounds(values, tile.get("grid"), "°C",
                                       divisions=divisions, target=3)
        geom = smooth_path(points, inner_w, chart_h, band=BAND, pad_x=pad_x,
                           lo=lo, hi=hi)

        y_top = chart_h * BAND[0]
        y_bot = chart_h * BAND[1]
        digits = label_digits(step)
        with_time = bool(tile.get("time_labels", True))
        for i in range(divisions + 1):
            f = i / float(divisions)
            # у нижней линии подпись не ставим: там идёт строка часов
            hlines.append({"y": round(y_bot - f * (y_bot - y_top), 1),
                           "edge": i in (0, divisions),
                           "text": "" if (with_time and i == 0)
                                   else _fmt(lo + f * (hi - lo), digits)})

        span_t = points[-1][0] - points[0][0]

        def px(t):
            return pad_x + (t - points[0][0]) / span_t * (inner_w - 2 * pad_x)

        def py(v):
            return y_bot - (v - lo) / max(hi - lo, 1e-6) * (y_bot - y_top)

        step_h = 3 if len(points) >= 9 else 2
        if start_hour is None:
            start_hour = time.localtime(now).tm_hour
        for i, (stamp, _v) in enumerate(points):
            x = px(stamp)
            # вертикаль на каждый час, как у обычных графиков; подписанные
            # часы выделены чуть заметнее
            if 2 < x < inner_w - 2:
                ticks.append({"x": round(x, 1), "major": i % step_h == 0})
            if i % step_h == 0:
                lx, anchor = edge_anchor(x, inner_w)
                times.append({"x": round(lx, 1), "anchor": anchor,
                              "text": "%02d" % ((start_hour + i) % 24)})

        # Экстремумы отмечаем только точками: значения и так читаются по
        # шкале слева, а подписи у самого края вылезали за плитку.
        for kind, target in (("max", max(values)), ("min", min(values))):
            idx = values.index(target)
            marks.append({"kind": kind,
                          "x": round(px(points[idx][0]), 1),
                          "y": round(py(target), 1)})

        # --- осадки и облачность -------------------------------------------
        # Дождь - столбики от нижней линии: высота по вероятности, насыщенность
        # по количеству. Вероятность отвечает на «брать ли зонт», количество -
        # на «насколько промокну», и путать их не стоит.
        half = (inner_w - 2 * pad_x) / max(len(points) - 1, 1) / 2.0
        for i in range(len(points)):
            prob = probs[i] if i < len(probs) else None
            if not prob:
                continue
            mm = (rains[i] if i < len(rains) else None) or 0.0
            x = px(points[i][0])
            h = (y_bot - y_top) * 0.42 * min(prob, 100.0) / 100.0
            bars.append({"x": round(x - half * 0.72, 1),
                         "w": round(half * 1.44, 1),
                         "y": round(y_bot - h, 1),
                         "h": round(h, 1),
                         # 0.2 мм/ч это морось, 2 мм/ч уже настоящий дождь
                         "opacity": round(0.28 + 0.42 * min(mm / 2.0, 1.0), 2)})

        # Облачность - полоса под верхней границей: чем плотнее, тем серее.
        # Отдельной шкалы не нужно, это фон, а не данные для чтения.
        for i in range(len(points)):
            cloud = clouds[i] if i < len(clouds) else None
            if cloud is None:
                continue
            x = px(points[i][0])
            sky.append({"x": round(x - half, 1), "w": round(half * 2, 1),
                        "opacity": round(0.06 + 0.30 * cloud / 100.0, 3)})

        # Значок по коду погоды: раз в step_h часов, чтобы не рябило
        for i in range(0, len(points), step_h):
            code = codes[i] if i < len(codes) else None
            if code is None:
                continue
            hour = (start_hour + i) % 24
            icons.append({"x": round(px(points[i][0]), 1),
                          "name": wmo_icon(code, 6 <= hour < 21)})

    # Скрипт дописывает ветер в описание, а он и так есть отдельной цифрой -
    # убираем дубль и подрезаем строку под ширину плитки.
    desc = (desc_snap["raw"] or "").strip()
    desc = re.sub(r",\s*ветер\b.*$", "", desc).strip()
    avail = max((tile.get("inner_w") or 354) - 84, 60)
    if text_width(desc, 13.5) > avail:
        while desc and text_width(desc + "…", 13.5) > avail:
            desc = desc[:-1]
        desc = desc.rstrip(" ,") + "…"

    # Когда дождь ожидается, это важнее давления: строка снизу и так тесная
    rain_note = ""
    if (val("rain_expected")["raw"] or "").strip() in ("1", "true", "True"):
        hrs = to_float(val("rain_in")["raw"])
        prob = to_float(val("precip_prob_max_12h")["raw"])
        if hrs is not None:
            rain_note = "дождь через %s ч" % _fmt(hrs, 0)
            if prob:
                rain_note += ", %s %%" % _fmt(prob, 0)

    bits = []
    if hum is not None:
        bits.append("%s %%" % _fmt(hum, 0))
    if press is not None:
        bits.append("%s мм" % _fmt(press, 0))
    if wind is not None:
        bits.append("%s км/ч" % _fmt(wind, 0))

    return {
        "on": False,
        "now_temp": ("%s°" % _fmt(cur, 1)) if cur is not None else "--",
        "short": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "desc": desc,
        "extra": " · ".join(bits),
        "geom": geom,
        "marks": marks,
        "bars": bars,
        "sky": sky,
        "icons": icons,
        "icon_now": wmo_icon(to_float(val("code_series")["raw"].split(",")[0])
                             if (val("code_series")["raw"] or "").strip() else None,
                             6 <= time.localtime(now).tm_hour < 21),
        "rain_note": rain_note,
        "hlines": hlines,
        "ticks": ticks,
        "times": times,
        "grid_labels": bool(tile.get("grid_labels", True)),
        "time_labels": bool(tile.get("time_labels", True)),
        "hours": len(points),
        "stale_forecast": ok_raw in ("0", "false", "False"),
        "status": " · ".join(bits),
        "snaps": snaps,
    }


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


def build_ac(tile, state):
    """
    Кондиционер: крупно уставка, в углу фактическая температура,
    снизу режим и скорость вентилятора.
    """
    dev = tile.get("device", "")
    snaps = []

    def val(name):
        snap = state.snapshot("%s/%s" % (dev, name))
        snaps.append(snap)
        return snap

    power_raw = (val("power")["raw"] or "").strip()
    on = power_raw not in ("", "0", "false", "False")
    mode = (val("mode")["raw"] or "").strip()
    cur = to_float(val("current_temp")["raw"])
    tgt = to_float(val("target_temp")["raw"])
    fan = (val("fan_mode")["raw"] or "").strip()
    quiet_raw = (val("quiet")["raw"] or "").strip()
    quiet = quiet_raw not in ("", "0", "false", "False")

    lo = float(tile.get("temp_min", 16))
    hi = float(tile.get("temp_max", 30))

    swing = (val("swing_mode")["raw"] or "").strip()
    swing_h = (val("swing_h_mode")["raw"] or "").strip()
    swinging = swing not in ("", "off", "0") or swing_h not in ("", "off", "0")
    louver = louver_glyph(swing, AC_SWING_DEG, vertical=True) if on else None
    louver_h = louver_glyph(swing_h, AC_SWING_H_DEG, vertical=False) if on else None

    # Режим показывает значок, поэтому словом его не дублируем - в строке
    # остаётся только то, чего значком не передать.
    bits = []
    if fan:
        bits.append("вент. " + AC_FAN_RU.get(fan, fan))
    if quiet:
        bits.append("тихо")

    target_num = _fmt(tgt, 0) if tgt is not None else "--"
    big = 30.0
    return {
        "on": on,
        "mode": mode,
        "icon": tile.get("icon") or AC_MODE_ICON.get(mode, "snow"),
        "target_num": target_num,
        "deg_dx": round(text_width(target_num, big) / 2.0 + 1.0, 1),
        "target": ("%s°" % target_num) if tgt is not None else "--",
        "current": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "short": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "status": (" · ".join(bits) if on else "Выключен"),
        "mode_ru": AC_MODE_RU.get(mode, mode),
        "swinging": swinging,
        "louver": louver,
        "louver_h": louver_h,
        "room": tile.get("room", ""),
        "always_status": True,
        "lo": lo, "hi": hi,
        "wheel": build_wheel(tile.get("inner_w") or 170,
                             tile.get("chart_h") or 170, lo, hi, tgt),
        "enabled": on,
        "reach": None,
        "snaps": snaps,
    }


def build_link(tile, state):
    """
    Ссылка на другую панель. Ничего не читает из MQTT - только переход.

    Нужна, чтобы с общей панели проваливаться в комнату и обратно.
    """
    target = tile.get("panel") or ""
    # К адресу дописываем, с какой панели пришли: тогда кнопка «назад» на той
    # стороне вернёт сюда, а не в общий список. Имя панели подставляет
    # assemble - плитка своего расположения не знает.
    href = tile.get("url") or ((target + ".html") if target else "")
    if href and not tile.get("url") and tile.get("_from"):
        href += "?from=" + tile["_from"]
    return {
        "on": False,
        "href": href,
        "icon": tile.get("icon", "door"),
        "status": tile.get("subtitle", ""),
        "snaps": [],
    }


def build_value(tile, state):
    snap = state.snapshot(tile.get("channel", ""))
    raw = to_float(snap["raw"])
    return {
        "value": _fmt(raw, int(tile.get("digits", 1))),
        "unit": tile.get("unit", snap["units"]),
        "on": False,
        "snap": snap,
    }


BUILDERS = {
    "chart": None,   # особый случай: нужен history
    "switch": build_switch,
    "curtain": build_curtain,
    "light": build_light,
    "dimmer": build_dimmer,
    "thermostat": build_thermostat,
    "forecast": build_forecast,
    "ac": build_ac,
    "link": build_link,
    "value": build_value,
}
