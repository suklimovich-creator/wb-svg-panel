# -*- coding: utf-8 -*-
"""
Раскладка для настенного экрана esp32-panel (с 1.70).

Экран не рисует SVG: плитки и анимации у него свои, на LVGL, значения он
читает прямо из MQTT. От сервера ему нужно одно - какие страницы, какие
плитки и в какие топики смотреть и писать. Это и отдаёт

    GET /api/screen/<имя>        JSON раскладки

<имя> - имя экрана из раздела screens: (экран спрашивает по своему имени
в сети, например panel-6a10) либо прямо имя панели. Панель для экрана -
обычная панель из panels:, со своими sections и from:, обычно короткая:
на 4" помещается 4-12 плиток на вкладку.

    screens:
      panel-6a10: wall          # экран panel-6a10 показывает панель wall
    panels:
      wall:
        hidden: true
        sections:
          - {from: livingroom, title: "Гостиная"}
          - title: "Сцены"
            tiles: [...]
          - title: "Заставка"
            screensaver: 30s  # не вкладка, а экран после 30 с бездействия
            tiles: [...]

Топики выводятся теми же ролями, что и у SVG-панели, поэтому Sprut.hub
(команды в /set) и сырые топики работают без отдельной записи. Плитки,
Такие плитки, как кондиционер, термостат и шторы, приходят типом info -
заголовок, значение и строка подробностей. С 1.73 график приходит типом chart,
а его точки - отдельно через адрес из поля history.
С 1.71 у сводки кондиционера, термостата, фанкойла и штор есть поле pad -
описание пульта: экран открывает его по нажатию на плитку (уставка
колесом, режимы, положение шторы пальцем). Прошивки до 0.4 поле не читают
и показывают сводку как раньше.

С 1.74 панель передаёт экрану свой cols (по умолчанию 3), а размеры плиток -
обычные w и h с шагом 0.5, как в SVG-раскладке. Ширина ограничивается числом
колонок, высота отдельно не ограничивается. По умолчанию плитка 1x1.
    screen: false       не показывать на экране
С 1.72 у сцены и датчика есть icon (значок панели), у ленты - light: true:
экран 0.6+ рисует плитки как панель. С 1.75 камера приходит типом camera с
относительными snapshot и stream; логин, пароль и адрес камеры не выдаются.
С 1.76 секции со свойством screensaver: 30s не попадают во вкладки экрана,
а добавляют одноимённый блок: after/every в секундах, унаследованный cols и
pages с плитками этих секций. Страницы заставки меняются по кругу через every.
Её графики читаются через /api/screen/<имя>/screensaver/chart/<n>.
С 1.77 (нужна esp32-panel 0.10+):
- у диммера и ленты есть поле pad kind=light - пульт света во весь экран
  (яркость и теплота детально, как pads-light.js у панели);
- scenes приходит одной плиткой-набором с items, а не плиткой на клавишу;
- телевизор - типом tv со своим пультом (kind=tv): крестовина, медиа,
  громкость, приложения;
- протечки - типом leak со списком датчиков, флагом тревоги и сбросом
  (kind=leak);
- прогноз - типом weather: текущие каналы и ряды прогноза одними топиками;
- «Сегодня» и расписание - типом today; дела и события меняются, поэтому
  отдаются отдельно: /api/screen/<имя>/today/<n> (у заставки -
  /api/screen/<имя>/screensaver/today/<n>), как точки графиков.
С 1.77.2 свойство панели swipe: true передаётся корневым полем swipe;
esp32-panel 0.11.7+ разрешает по нему горизонтальное листание вкладок.
"""

import datetime as dt
import json
import math
from urllib.parse import quote

from flask import Response, abort

from . import cameras, palette, web
from .assemble import resolve_sections
from .calendars import MONTHS, WEEKDAYS, calendars, day_label
from .geometry import cells, field_color, parse_duration
from .lists import lists
from .registry import REGISTRY, resolve_type
from .sources import bind
from .state import state
from .tiles import AC_FAN_RU, AC_MODE_RU, HEAT_MODE, HEAT_NOW, command_topic
from .tiles_basic import Scene
from .tiles_calendar import _hm, _state, _when_text
from .tiles_devicewide import TV_STATE_RU, _series_kind, tv_apps
from .tiles_today import _cal_names
from .water import water
from .web import app

#: Прежняя ширина экрана; cols панели может её изменить (с 1.74).
DEFAULT_COLS = 3
MAX_COLS = 12


def topic_of(key):
    """Ключ состояния -> MQTT-топик. У Wiren Board ключ - «устройство/канал»."""
    if not key:
        return None
    if key.count("/") == 1 and not key.startswith("/"):
        dev, ctl = key.split("/", 1)
        return "/devices/%s/controls/%s" % (dev, ctl)
    return key


def _bound(conf):
    """Роли плитки, привязанные к топикам: {роль: Bound}."""
    kind = resolve_type(conf, state)
    cls = REGISTRY.get(kind)
    if cls is None:
        return kind, {}
    conf = cls.expand(conf)
    obj = cls()
    return kind, bind(conf, obj.schema(), state, obj)


def _t(bound, role):
    b = bound.get(role)
    return topic_of(b.key) if b and b.key else None


def _c(bound, role):
    b = bound.get(role)
    return b.command if b and b.command else None


def _limits(bound, role, lo, hi):
    b = bound.get(role)
    if b:
        a, z = b.limits
        if a is not None:
            lo = a
        if z is not None:
            hi = z
    return int(lo), int(hi)


def _cols(panel):
    """Число колонок экрана из обычной настройки панели."""
    try:
        cols = int(panel.get("cols", DEFAULT_COLS))
    except (TypeError, ValueError):
        cols = DEFAULT_COLS
    return max(1, min(MAX_COLS, cols))


def _size(conf, key, max_cells=None):
    """Обычный размер плитки -> число колонок/рядов с шагом 0.5."""
    n = cells(conf.get(key), 1.0)
    if max_cells is not None:
        n = min(n, max_cells)
    return n // 2 if n % 2 == 0 else n / 2


def _width(conf, cols):
    return _size(conf, "w", cols * 2)


def _scene_payload(conf, bound):
    """Чем сцена «нажимается»: как у SVG-панели - по виду значения канала."""
    if conf.get("command_on") is not None:
        return str(conf["command_on"])
    b = bound.get("value")
    raw = (state.values.get(b.key) or "").strip().lower() if b and b.key else ""
    return "true" if raw in ("true", "false") else "1"


def _dev(dev, name):
    return "/devices/%s/controls/%s" % (dev, name)


def _info(title, w, value=None, unit="", digits=1, value_map=None, lines=None,
          active=None, note=None, pad=None, invert=False):
    out = {"type": "info", "title": title, "w": w}
    if pad:
        out["pad"] = {k: v for k, v in pad.items() if v not in (None, [], "")}
    if invert:
        out["invert"] = True
    if value:
        out["value"] = value
        out["unit"] = unit
        out["digits"] = digits
        if value_map:
            out["map"] = value_map
    if lines:
        out["lines"] = [l for l in lines if l.get("topic")]
    if active:
        out["active"] = active
    if note:
        out["note"] = note
    return out


# ---------------------------------------------------------------- пульты
#: Режимы кондиционера на кнопках пульта: значение, подпись, оттенок колеса.
#: Четвёртое - слово на плашке режима, пятое - значок (как AC_MODE_ICON).
AC_PAD_MODES = (("cool", "Холод", "cool", "холод", "snow"),
                ("heat", "Тепло", "heat", "тепло", "heat"),
                ("dry", "Сушка", "cool", "сушка", "drop"),
                ("fan_only", "Обдув", "", "обдув", "fan"),
                ("auto", "Авто", "", "авто", "power"))
AC_PAD_FANS = (("low", "1", ""), ("medium", "2", ""), ("high", "3", ""),
               ("auto", "Авто", ""))


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _scale(conf, bound, role, lo, hi, step):
    """Пределы и шаг уставки для колеса: от устройства, сужает конфиг."""
    b = bound.get(role)
    if conf.get("step") is not None:
        step = _num(conf["step"], step)
    elif b is not None and getattr(b, "step", None):
        step = float(b.step)
    a, z = b.limits if b else (None, None)
    lo = max(_num(a, lo), _num(conf.get("scale_min"), lo)) if a is not None else _num(conf.get("scale_min"), lo)
    hi = min(_num(z, hi), _num(conf.get("scale_max"), hi)) if z is not None else _num(conf.get("scale_max"), hi)
    if hi <= lo:
        hi = lo + step
    return lo, hi, step


def _climate_pad(target, target_cmd, current, lo, hi, step, power=None,
                 power_cmd=None, on="1", off="0", mode=None, mode_cmd=None,
                 modes=(), fan=None, fan_cmd=None, fans=(), tone="heat",
                 style="dial", state=None, states=None, extra=None):
    """Пульт климата. style: ac - колесо-шкала кондиционера, dial -
    циферблат термостата (у фанкойла под ним режим и скорость)."""
    if not target or not target_cmd:
        return None
    out = {"style": style, "state": state, "states": states}
    out.update(extra or {})
    return dict(out, **{"kind": "climate", "target": target, "target_cmd": target_cmd,
            "current": current, "min": lo, "max": hi, "step": step,
            "power": power, "power_cmd": power_cmd if power else None,
            "on": on, "off": off, "tone": tone,
            "mode": mode if mode_cmd else None, "mode_cmd": mode_cmd,
            "modes": [list(m) for m in modes] if mode_cmd else [],
            "fan": fan if fan_cmd else None, "fan_cmd": fan_cmd,
            "fans": [list(f) for f in fans] if fan_cmd else []})


def _fancoil_opts(raw, default, cool_flag=False):
    from .tiles_fancoil import table
    out = []
    for k, v in table(raw, default):
        tone = ("cool" if v.get("cool") else "" if v.get("valve") is False else "heat") \
            if cool_flag else ""
        word = str(v.get("short") or v.get("title", k)).lower()
        out.append((str(k), str(v.get("title", k)), tone, word, str(v.get("icon") or "")))
    return out


def _cover_pad(conf, bound, style):
    pos, cmd = _t(bound, "target"), _c(bound, "target")
    if not pos or not cmd:
        return None
    return {"kind": "cover", "style": style, "pos": pos, "pos_cmd": cmd,
            "invert": bool(conf.get("inverted")) or None,
            "open": str(conf.get("command_open", "100")),
            "close": str(conf.get("command_close", "0")),
            "stop_cmd": _c(bound, "stop"),
            "stop": str(conf.get("command_stop", "2")) if _c(bound, "stop") else None}


def _light_pad(conf, bound):
    """Пульт света во весь экран (с 1.77): яркость и теплота детально.

    Топики те же, что у плитки, - продублированы, как у сводок: пульт
    разбирает только своё поле pad и плитку не видит.
    """
    level, level_cmd = _t(bound, "bright"), _c(bound, "bright")
    if not level or not level_cmd:
        return None
    _lo, hi = _limits(bound, "bright", 0, 100)
    pad = {"kind": "light", "state": _t(bound, "switch"),
           "state_cmd": _c(bound, "switch"),
           "level": level, "level_cmd": level_cmd, "max": hi}
    if _t(bound, "temp"):
        # Цвета - от кельвинов конфига, даже когда канал в процентах
        # (как в Light.prepare: cold - холодный край, k_max).
        unit = str(conf.get("temp_unit", "percent"))
        tlo, thi = (0, 100) if unit == "percent" else (
            int(conf.get("temp_min", 2700)), int(conf.get("temp_max", 6500)))
        tlo, thi = _limits(bound, "temp", tlo, thi)
        k_lo, k_hi = int(conf.get("temp_min", 2700)), int(conf.get("temp_max", 6500))
        pad.update({"temp": _t(bound, "temp"), "temp_cmd": _c(bound, "temp"),
                    "temp_min": tlo, "temp_max": thi, "unit": unit,
                    "cold": str(conf.get("cold_color") or field_color(k_hi)),
                    "warm": str(conf.get("warm_color") or field_color(k_lo))})
    return pad


def _table_map(raw, default):
    """Таблица значений фанкойла -> {"0": "Охлаждение", ...}."""
    from .tiles_fancoil import table
    return {str(k): str(v.get("title", k)) for k, v in table(raw, default)}


def _scenes_tile(conf, w):
    """Плитка scenes (клавиши сеткой) -> один набор на экране (с 1.77)."""
    raw = [i for i in (conf.get("items") or []) if isinstance(i, dict) and i.get("channel")]
    items = []
    for k, item in enumerate(raw):
        cmd = command_topic(item["channel"], item.get("command_topic"))
        if not cmd:
            continue
        if item.get("command_on") is not None:
            payload = str(item["command_on"])
        else:
            raw_value = str(state.values.get(item["channel"]) or "").strip().lower()
            payload = Scene.FORMS.get(raw_value, ("1", "0"))[0]
        try:
            level = float(item.get("level"))
        except (TypeError, ValueError):
            # Без level - как на панели: от тусклых к ярким по порядку.
            level = k / float(max(1, len(raw) - 1))
        out = {"title": str(item.get("title") or ""), "cmd": cmd,
               "payload": payload, "level": round(max(0.0, min(1.0, level)), 2)}
        if item.get("icon"):
            out["icon"] = str(item["icon"])
        items.append(out)
    if not items:
        return None
    return {"type": "scenes", "title": str(conf.get("title") or "Сцены"),
            "w": w, "items": items}


def screen_tiles(conf, cols):
    """Плитка панели -> плитки экрана (сейчас всегда одна)."""
    if not isinstance(conf, dict) or conf.get("screen") is False:
        return []
    t = screen_tile(conf, cols)
    out = [t] if t else []
    h = _size(conf, "h")
    for t in out:
        t["h"] = h
    return out


def screen_tile(conf, cols):
    """Плитка панели -> плитка экрана (или None, если экрану не нужна)."""
    if not isinstance(conf, dict) or conf.get("screen") is False:
        return None
    kind, bound = _bound(conf)
    title = str(conf.get("title") or "")
    w = _width(conf, cols)

    if kind == "header":
        return None

    if kind == "camera":
        # Экран ходит только на наши относительные адреса. Имя камеры из
        # конфига кодируется, а host/user/password наружу не попадают.
        name = conf.get("camera") or conf.get("name")
        cam = cameras.get(str(name)) if name else None
        if cam is None:
            return _info(title or "Камера", w, note="не настроена")
        base = "/cam/%s" % quote(str(name), safe="")
        out = {"type": "camera", "title": title or str(cam.title), "w": w,
               "snapshot": base + ".jpg"}
        if cam.stream_path:
            out["stream"] = base + ".mjpeg?width=480&fps=5"
        return out

    if kind == "switch":
        out = {"type": "switch", "title": title, "w": w,
               "state": _t(bound, "value"), "cmd": _c(bound, "value")}
        if conf.get("invert"):
            out["invert"] = True
        return out if out["state"] else None

    if kind == "scenes":
        # С 1.77 - одна плитка-набор; экраны до 0.10 раскладывали её на
        # отдельные плитки сцен и этот тип не покажут.
        return _scenes_tile(conf, w)

    if kind == "scene":
        cmd = _c(bound, "value")
        # Канал сцены - виртуальный выключатель; если он readonly или задан
        # сырым топиком без адреса команды, нажимать некуда.
        if not cmd:
            return None
        out = {"type": "scene", "title": title, "w": w, "cmd": cmd,
               "payload": _scene_payload(conf, bound)}
        if conf.get("icon"):
            out["icon"] = str(conf["icon"])
        return out

    if kind in ("dimmer", "light"):
        lo, hi = _limits(bound, "bright", 0, 100)
        out = {"type": "dimmer", "title": title, "w": w,
               "state": _t(bound, "switch"), "state_cmd": _c(bound, "switch"),
               "level": _t(bound, "bright"), "level_cmd": _c(bound, "bright"),
               "max": hi}
        if kind == "light":
            out["light"] = True
        if kind == "light" and _t(bound, "temp"):
            unit = conf.get("temp_unit", "percent")
            tlo, thi = (0, 100) if unit == "percent" else (
                int(conf.get("temp_min", 2700)), int(conf.get("temp_max", 6500)))
            tlo, thi = _limits(bound, "temp", tlo, thi)
            out.update({"temp": _t(bound, "temp"), "temp_cmd": _c(bound, "temp"),
                        "temp_min": tlo, "temp_max": thi})
        if not out["level"]:
            return None
        pad = _light_pad(conf, bound)
        if pad:
            out["pad"] = {k: v for k, v in pad.items() if v is not None}
        return out

    if kind == "value":
        b = bound.get("value")
        if not b:
            return None
        out = {"type": "sensor", "title": title, "w": w, "value": topic_of(b.key),
               "unit": conf.get("unit") or b.units or "",
               "digits": int(conf.get("digits", 1))}
        if conf.get("icon"):
            out["icon"] = str(conf["icon"])
        return out

    # ---- заглушки: показывают состояние, управление - на панели --------
    if kind == "thermostat":
        lo, hi, step = _scale(conf, bound, "target", 14, 30, 0.5)
        pad = _climate_pad(_t(bound, "target"), _c(bound, "target"), _t(bound, "current"),
                           lo, hi, step, power=_t(bound, "mode"), power_cmd=_c(bound, "mode"),
                           on=str(conf.get("command_mode_on", "1")),
                           off=str(conf.get("command_mode_off", "0")),
                           state=_t(bound, "state"),
                           states={str(k): v for k, v in HEAT_NOW.items()})
        return _info(title or "Термостат", w, _t(bound, "target"), "°C", 1,
                     lines=[{"label": "сейчас", "topic": _t(bound, "current"),
                             "unit": "°", "digits": 1},
                            {"topic": _t(bound, "mode"),
                             "map": {str(k): v for k, v in HEAT_MODE.items()}}],
                     active=_t(bound, "state"), pad=pad)

    if kind == "fancoil":
        from .tiles_fancoil import FANCOIL_FAN_STATES, FANCOIL_FANS, FANCOIL_MODES
        lo, hi, step = _scale(conf, bound, "target", 16, 30, 0.5)
        pad = _climate_pad(_t(bound, "target"), _c(bound, "target"), _t(bound, "current"),
                           lo, hi, step, power=_t(bound, "switch"), power_cmd=_c(bound, "switch"),
                           mode=_t(bound, "mode"), mode_cmd=_c(bound, "mode"),
                           modes=_fancoil_opts(conf.get("modes"), FANCOIL_MODES, True),
                           fan=_t(bound, "fan"), fan_cmd=_c(bound, "fan"),
                           fans=_fancoil_opts(conf.get("fans"), FANCOIL_FANS))
        return _info(title or "Фанкойл", w, _t(bound, "target"), "°C", 1, pad=pad,
                     lines=[{"topic": _t(bound, "mode"),
                             "map": _table_map(conf.get("modes"), FANCOIL_MODES)},
                            {"label": "сейчас", "topic": _t(bound, "current"),
                             "unit": "°", "digits": 1},
                            {"label": "скорость", "topic": _t(bound, "fan_state"),
                             "map": _table_map(conf.get("fan_states"), FANCOIL_FAN_STATES)}],
                     active=_t(bound, "switch"))

    if kind == "ac":
        dev = conf.get("device")
        if not dev:
            return _info(title or "Кондиционер", w)
        on = lambda name: _dev(dev, name) + "/on"
        pad = _climate_pad(_dev(dev, "target_temp"), on("target_temp"), _dev(dev, "current_temp"),
                           _num(conf.get("temp_min"), 16), _num(conf.get("temp_max"), 30),
                           _num(conf.get("step"), 1),
                           power=_dev(dev, "power"), power_cmd=on("power"),
                           mode=_dev(dev, "mode"), mode_cmd=on("mode"), modes=AC_PAD_MODES,
                           fan=_dev(dev, "fan_mode"), fan_cmd=on("fan_mode"), fans=AC_PAD_FANS,
                           tone="cool", style="ac",
                           extra={"swing": _dev(dev, "swing_mode"), "swing_cmd": on("swing_mode"),
                                  "swing_h": _dev(dev, "swing_h_mode"),
                                  "swing_h_cmd": on("swing_h_mode")})
        return _info(title or "Кондиционер", w, _dev(dev, "target_temp"), "°C", 0, pad=pad,
                     lines=[{"topic": _dev(dev, "mode"), "map": AC_MODE_RU},
                            {"label": "сейчас", "topic": _dev(dev, "current_temp"),
                             "unit": "°", "digits": 0},
                            {"label": "вент.", "topic": _dev(dev, "fan_mode"),
                             "map": AC_FAN_RU}],
                     active=_dev(dev, "power"))

    if kind in ("curtain", "roller"):
        # Положение - роль target (у шторы state - это «едет/стоит»).
        return _info(title or "Штора", w, _t(bound, "target") or _t(bound, "left"), "%", 0,
                     pad=_cover_pad(conf, bound, kind), invert=bool(conf.get("inverted")))

    if kind == "chart":
        series = [s for s in (conf.get("series") or []) if isinstance(s, dict) and s.get("channel")]
        if not series:
            return _info(title or "График", w)
        try:
            points = int(conf.get("points", 90))
        except (TypeError, ValueError):
            points = 90
        points = max(10, min(points, 120))
        # Как в SVG-графике: свой color сильнее темы, затем цвет величины
        # (CO2/VOC и т. п.), и только потом общая палитра. Раньше здесь
        # принудительно подставлялись оранжевый/синий, поэтому ESP получал
        # их как явный color и не мог применить свои цвета по умолчанию.
        colors = palette.series_colors("light", [
            (spec.get("color"), _series_kind(spec, state.snapshot(spec["channel"])))
            for spec in series[:3]])
        out_series = []
        for i, spec in enumerate(series[:3]):
            try:
                digits = max(0, min(int(spec.get("digits", 1)), 3))
            except (TypeError, ValueError):
                digits = 1
            item = {"channel": spec["channel"], "value": topic_of(spec["channel"]),
                    "unit": spec.get("unit", ""), "digits": digits,
                    "color": colors[i],
                    "label": spec.get("label", "")}
            if spec.get("min") is not None and spec.get("max") is not None:
                try:
                    item["min"], item["max"] = float(spec["min"]), float(spec["max"])
                except (TypeError, ValueError):
                    pass
            out_series.append(item)
        return {"type": "chart", "title": title or "График", "w": w,
                "range": parse_duration(conf.get("range", "24h")),
                "points": points, "series": out_series}

    if kind == "forecast":
        # Погода (с 1.77): текущие каналы устройства и ряды прогноза.
        # Значения экран читает из MQTT, раскладка от этого стабильна.
        dev = conf.get("device") or "weather"
        return {"type": "weather", "title": title or "Погода", "w": w,
                "temp": _dev(dev, "temperature"), "desc": _dev(dev, "description"),
                "hum": _dev(dev, "humidity"), "press": _dev(dev, "pressure"),
                "wind": _dev(dev, "windspeed"),
                "tmin": _dev(dev, "temp_min_12h"), "tmax": _dev(dev, "temp_max_12h"),
                "series": {"temp": _dev(dev, "temp_series"),
                           "code": _dev(dev, "code_series"),
                           "prob": _dev(dev, "precip_prob_series"),
                           "cloud": _dev(dev, "cloud_series"),
                           "start": _dev(dev, "forecast_start")}}

    if kind == "tv":
        # Телевизор (с 1.77): плитка со статусом и пульт kind=tv. Адреса
        # кнопок экран собирает сам из base и имён контролов моста wb-tv.
        dev = conf.get("device")
        if not dev:
            return _info(title or "Телевизор", w, note="не настроен")
        base = _dev(dev, "")
        apps = [{"title": name, "cmd": "%s%s/on" % (base, control)}
                for control, name in tv_apps(conf)]
        pad = {"kind": "tv", "base": base, "state": _dev(dev, "power"),
               "link": _dev(dev, "online"), "app": _dev(dev, "current_app"),
               "mode": _dev(dev, "state"),
               "states": {str(k): v for k, v in TV_STATE_RU.items()},
               "apps": apps}
        return {"type": "tv", "title": title or "Телевизор", "w": w,
                "state": pad["state"], "link": pad["link"], "app": pad["app"],
                "mode": pad["mode"], "states": pad["states"],
                "pad": {k: v for k, v in pad.items() if v not in (None, [], "")}}

    if kind == "leak":
        # Протечки (с 1.77): датчики списком (экран рисует чипы сам),
        # общий флаг тревоги и адреса сброса из раздела water.
        sensors = water.pick_sensors(conf.get("sensors"), conf.get("channel"))
        rows = []
        for s in sensors:
            topic = topic_of(s.get("channel"))
            if topic:
                rows.append({"topic": topic, "title": str(s.get("title") or "")})
        alarm = _t(bound, "alarm")
        if not rows and not alarm:
            return _info(title or "Протечки", w, note="не заданы датчики")
        resets = list(water.resets(sensors))
        b = bound.get("reset")
        if b and b.command and b.command not in resets:
            resets.append(b.command)
        pad = {"kind": "leak", "reset": resets}
        out = {"type": "leak", "title": title or "Протечки", "w": w,
               "icon": str(conf.get("icon") or "leak"), "sensors": rows,
               "pad": {k: v for k, v in pad.items() if v not in (None, [], "")}}
        if alarm:
            out["alarm"] = alarm
        return out

    if kind in ("today", "agenda"):
        # «Сегодня» и расписание (с 1.77): в раскладке - только откуда брать
        # данные; дела и события приходят отдельно через адрес из поля data,
        # как точки графиков (иначе раскладка менялась бы каждый час).
        out = {"type": "today",
               "title": title or ("Сегодня" if kind == "today" else "Расписание"),
               "w": w}
        if conf.get("list"):
            out["list"] = str(conf["list"])
        names = _cal_names(conf.get("calendars"))
        if names:
            out["calendars"] = names
        return out

    # Остальное (списки, календарь, вода, телевизор, ссылки) -
    # честная заглушка с названием.
    return _info(title or kind, w, note="на панели")


def _screen_sections(panel, panels):
    """(секция конфига, результат resolve_sections), в исходном порядке."""
    rows = resolve_sections(panel, panels)
    configured = [s for s in panel.get("sections", []) or [] if isinstance(s, dict)]
    if not configured:
        return [(None, rows[0])] if rows else []
    return list(zip(configured, rows))


def _saver_after(section):
    """Интервал у секции; само наличие screensaver делает её служебной."""
    return web._seconds(section.get("screensaver")) if section and "screensaver" in section else 0


def _screen_pages(panel_name, panel, panels, cols, history_base, screensaver=False):
    """Секции панели -> обычные страницы экрана либо одна заставка."""
    pages = []
    chart_index = 0
    today_index = 0
    today_base = history_base[:-len("chart")] + "today"
    for section, (title, tiles, _src, _key) in _screen_sections(panel, panels):
        marked = bool(section and "screensaver" in section)
        if screensaver:
            # Все активные секции заставки показываются по кругу. Период
            # общий: его задаёт первая из них в screen_layout.
            if not marked or _saver_after(section) <= 0:
                continue
        elif marked:
            # Служебная секция не становится обычной вкладкой, даже если
            # интервал выключен или написан с ошибкой.
            continue
        out = [t for c in tiles for t in screen_tiles(c, cols)]
        for tile in out:
            if tile.get("type") == "chart":
                # История меняется независимо от раскладки: отдельный адрес,
                # иначе каждый новый точечный ряд выглядел бы как новая раскладка.
                tile["history"] = "%s/%d" % (history_base, chart_index)
                chart_index += 1
            elif tile.get("type") == "today":
                # Дела и события - тоже отдельно: они меняются каждый час.
                tile["data"] = "%s/%d" % (today_base, today_index)
                today_index += 1
        if out:
            pages.append({"title": title or panel.get("title") or panel_name,
                          "tiles": out})
    return pages


def screen_layout(panel_name):
    panels = web.config.panels
    panel = panels.get(panel_name) or {}
    cols = _cols(panel)
    base = "/api/screen/%s" % quote(panel_name, safe="")
    out = {"version": 1, "panel": panel_name, "cols": cols,
           "pages": _screen_pages(panel_name, panel, panels, cols, base + "/chart")}
    if panel.get("swipe") is True:
        out["swipe"] = True

    # Заставка - одна из секций этой же панели. Отдельная панель и ссылка
    # screensaver.panel для экрана больше не нужны; сетка общая.
    saver_pages = _screen_pages(panel_name, panel, panels, cols,
                                base + "/screensaver/chart", screensaver=True)
    if saver_pages:
        after = next((_saver_after(section) for section, _row in _screen_sections(panel, panels)
                      if _saver_after(section) > 0), 0)
        if after > 0:
            out["screensaver"] = {"after": after, "every": after,
                                  "cols": cols, "pages": saver_pages}
    return out


def panel_for(name):
    """Имя экрана -> имя панели: из screens:, иначе само имя."""
    screens = web.config.get("screens") or {}
    target = screens.get(name, name)
    if isinstance(target, dict):
        target = target.get("panel")
    return target if target in web.config.panels else None


@app.route("/api/screen/<name>")
def api_screen(name):
    panel = panel_for(name)
    if not panel:
        abort(404)
    # Одинаковая раскладка - одинаковый текст: экран сравнивает ответ
    # целиком и перестраивается, только если что-то изменилось.
    body = json.dumps(screen_layout(panel), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return Response(body, mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


def _chart_response(name, index, screensaver=False):
    """История графика основной раскладки или её заставки."""
    panel = panel_for(name)
    if not panel:
        abort(404)
    layout = screen_layout(panel)
    pages = layout.get("screensaver", {}).get("pages", []) if screensaver else layout["pages"]
    charts = [tile for page in pages for tile in page["tiles"]
              if tile.get("type") == "chart"]
    if index < 0 or index >= len(charts):
        abort(404)
    chart = charts[index]
    out = {"version": 1, "title": chart["title"], "range": chart["range"],
           "points": chart["points"], "series": []}
    for spec in chart["series"]:
        values = []
        if web.history is not None:
            data = web.history.get(spec["channel"], chart["range"], chart["points"])
            for stamp, value in data or []:
                try:
                    stamp, value = float(stamp), float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(stamp) and math.isfinite(value):
                    values.append([round(stamp, 3), round(value, 4)])
        item = {k: spec[k] for k in ("unit", "digits", "color", "label")}
        if "min" in spec and "max" in spec:
            item.update({"min": spec["min"], "max": spec["max"]})
        item["values"] = values
        out["series"].append(item)
    body = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return Response(body, mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


@app.route("/api/screen/<name>/chart/<int:index>")
def api_screen_chart(name, index):
    """История одного графика экрана; индекс - из поля history в раскладке."""
    return _chart_response(name, index)


def _today_response(name, index, screensaver=False):
    """Дела и события плитки «Сегодня» основной раскладки или заставки."""
    panel = panel_for(name)
    if not panel:
        abort(404)
    layout = screen_layout(panel)
    pages = layout.get("screensaver", {}).get("pages", []) if screensaver else layout["pages"]
    tiles = [tile for page in pages for tile in page["tiles"]
             if tile.get("type") == "today"]
    if index < 0 or index >= len(tiles):
        abort(404)
    tile = tiles[index]

    tz = calendars.tz()
    now = dt.datetime.now(tz)
    today = now.date()
    out = {"version": 1, "title": tile.get("title") or "Сегодня",
           "date": day_label(today, today).split(", ", 1)[-1]}

    name_l = tile.get("list")
    if name_l and lists.known(str(name_l)):
        view = lists.view(str(name_l))
        out["list_title"] = str(view.get("title") or name_l)
        out["tasks"] = [str(item.get("text") or "")
                        for item in view.get("open") or []]

    names = [str(n) for n in tile.get("calendars") or []]
    if names:
        days = []
        for shift in (0, 1):
            day = today + dt.timedelta(days=shift)
            events = []
            for ev in calendars.day(names, day):
                row = {"text": str(ev.get("title") or ""),
                       "color": str(ev.get("color") or ""),
                       "state": (_state(ev, now) if day == today
                                 else ("day" if ev["all_day"] else "next"))}
                if ev["all_day"]:
                    row["time"] = "весь день"
                else:
                    row["time"] = _when_text(ev, day, tz)
                    if (ev["end"] > ev["start"] and
                            ev["start"].astimezone(tz).date() == day):
                        row["end"] = _hm(ev["end"].astimezone(tz))
                where = str(ev.get("location") or "")
                if where:
                    row["where"] = where
                events.append(row)
            days.append({"label": day_label(day, today), "events": events})
        out["days"] = days

    body = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return Response(body, mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


@app.route("/api/screen/<name>/today/<int:index>")
def api_screen_today(name, index):
    """Дела и события плитки «Сегодня»; индекс - из поля data в раскладке."""
    return _today_response(name, index)


@app.route("/api/screen/<name>/screensaver/today/<int:index>")
def api_screen_saver_today(name, index):
    """Данные плитки «Сегодня» заставки; нумерация не зависит от основных плиток."""
    return _today_response(name, index, screensaver=True)


@app.route("/api/screen/<name>/screensaver/chart/<int:index>")
def api_screen_saver_chart(name, index):
    """История графика заставки; нумерация не зависит от основных плиток."""
    return _chart_response(name, index, screensaver=True)


__all__ = ["api_screen", "api_screen_chart", "api_screen_saver_chart",
           "api_screen_saver_today", "api_screen_today",
           "screen_layout", "screen_tile", "screen_tiles"]
