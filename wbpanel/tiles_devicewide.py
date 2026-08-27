# -*- coding: utf-8 -*-
"""
Плитки, которым роли пока не дают выигрыша.

    chart      каналов сколько угодно, и все равноправны - это ряды, а не
               роли; зато нужна история
    forecast   одно устройство с двумя десятками каналов: роль у всех одна,
               «часть этого прибора»
    ac         то же самое, плюс свои словари режимов

Тела сборщиков перенесены как есть: они работают и проверены, а
переписывать двести строк ради формы - лишний риск. Классы вокруг них
дают главное: тип в реестре, объявленные каналы и единый вызов.
"""

import math
import re
import time

from .const import BAND, log
from .geometry import (CHAR_W, _fmt, cct_color, darken, edge_anchor, field_color,
                       grid_bounds, hour_ticks, label_digits, nice_step,
                       parse_duration, smooth_path, text_width)
from .registry import Tile, Zone, tile
from .state import to_float
from .tiles import (AC_CONTROLS, AC_FAN_RU, AC_MODE_ICON, AC_MODE_RU,
                    AC_SWING_DEG, AC_SWING_H_DEG, WMO_ICONS, ac_channels,
                    build_wheel, forecast_channels, louver_glyph, parse_iso_hour,
                    parse_series, wmo_icon)

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

@tile("chart")
class Chart(Tile):
    """
    График. Ролей нет: каналы здесь равноправные ряды, а не смыслы.
    Зато нужна история - единственная плитка, которой она нужна.
    """

    roles = ()

    @staticmethod
    def channels(conf):
        return set(s["channel"] for s in (conf.get("series") or [])
                   if isinstance(s, dict) and s.get("channel"))

    def prepare(self, ctx):
        data = build_chart(ctx.conf, ctx.state, ctx.history)
        # Снимки собираем сами: сборщик о них не знает, а без них плитка
        # не отметится как «нет данных».
        for spec in (ctx.conf.get("series") or []):
            if isinstance(spec, dict) and spec.get("channel"):
                ctx.snaps.append(ctx.state.snapshot(spec["channel"]))
        data["on"] = False
        return data

    def zones(self, ctx, data):
        # Управлять нечем, но раскрыться на весь экран график должен.
        return [Zone("expand", "all")]


@tile("forecast")
class Forecast(Tile):
    """Погода: одно виртуальное устройство, два десятка каналов."""

    roles = ()

    @staticmethod
    def channels(conf):
        return set(forecast_channels(conf))

    def prepare(self, ctx):
        data = build_forecast(ctx.conf, ctx.state)
        ctx.snaps.extend(data.pop("snaps", []) or [])
        return data

    def zones(self, ctx, data):
        return [Zone("expand", "all")]


@tile("ac")
class Ac(Tile):
    """Кондиционер: то же устройство целиком, плюс словари режимов."""

    roles = ()

    @staticmethod
    def channels(conf):
        return set(ac_channels(conf))

    #: Каналы, в которые пульт кондиционера пишет. Ролей у них нет -
    #: прибор описан одним полем device, а не десятком каналов, - поэтому
    #: список объявлен здесь, рядом с плиткой, а не в белом списке веб-слоя.
    WRITES = ("power", "mode", "target_temp", "fan_mode", "quiet", "turbo",
              "preset", "swing_mode", "swing_h_mode")

    @staticmethod
    def writes(conf):
        dev = conf.get("device")
        if not dev:
            return set()
        return set("/devices/%s/controls/%s/on" % (dev, name)
                   for name in Ac.WRITES)

    def prepare(self, ctx):
        data = build_ac(ctx.conf, ctx.state)
        ctx.snaps.extend(data.pop("snaps", []) or [])
        return data

    @staticmethod
    def _top(dev, name):
        return "/devices/%s/controls/%s/on" % (dev, name) if dev else None

    def zones(self, ctx, data):
        dev = ctx.opt("device")
        if not dev:
            return []
        return [Zone("toggle", "all", topic=self._top(dev, "power"),
                     write={"on": "1", "off": "0"}),
                Zone("pad", "long", pad="ac")]

    def pad(self, ctx, data):
        dev = ctx.opt("device")
        if not dev:
            return {}
        return {
            "ac_target": self._top(dev, "target_temp"),
            "ac_mode": self._top(dev, "mode"),
            "ac_fan": self._top(dev, "fan_mode"),
            "ac_quiet": self._top(dev, "quiet"),
            "ac_swing": self._top(dev, "swing_mode"),
            "ac_swing_h": self._top(dev, "swing_h_mode"),
            "ac_swing_now": (ctx.dev("swing_mode") or "").strip(),
            "ac_swing_h_now": (ctx.dev("swing_h_mode") or "").strip(),
            "ac_lo": str(data.get("lo", 16)),
            "ac_hi": str(data.get("hi", 30)),
            "ac_step": str(ctx.opt("step", 1)),
            "ac_now": data.get("target_num", "--"),
            "ac_cur": data.get("current", ""),
            "ac_mode_now": data.get("mode", ""),
            "ac_fan_now": (ctx.dev("fan_mode") or "").strip(),
            "ac_quiet_now": "1" if "тихий" in (data.get("status") or "") else "0",
        }
