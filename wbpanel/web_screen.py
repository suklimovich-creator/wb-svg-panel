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

Топики выводятся теми же ролями, что и у SVG-панели, поэтому Sprut.hub
(команды в /set) и сырые топики работают без отдельной записи. Плитки,
которых экран пока не умеет (кондиционер, термостат, шторы, график...),
приходят типом info - заголовок, значение и строка подробностей.
С 1.71 у сводки кондиционера, термостата, фанкойла и штор есть поле pad -
описание пульта: экран открывает его по нажатию на плитку (уставка
колесом, режимы, положение шторы пальцем). Прошивки до 0.4 поле не читают
и показывают сводку как раньше.

У плитки панели для экрана можно задать:
    screen_w: 1..3      ширина в колонках экрана (их 3)
    screen_h: 0.5..2    высота в рядах (ряд - квадрат 146 px); без неё
                        выключатель, сцена и датчик - в полряда, диммер в
                        одну колонку и сводки - квадратом, широкий диммер -
                        полосой в полряда (решает экран, 0.6+)
    screen: false       не показывать на экране
С 1.72 у сцены и датчика есть icon (значок панели), у ленты - light: true:
экран 0.6+ рисует плитки как панель.
"""

import json
from flask import Response, abort

from . import web
from .assemble import resolve_sections
from .registry import REGISTRY, resolve_type
from .sources import bind
from .state import state
from .tiles import AC_FAN_RU, AC_MODE_RU, HEAT_MODE, HEAT_NOW, command_topic
from .tiles_basic import Scene
from .web import app

#: Сколько колонок у экрана и какие плитки по умолчанию во всю ширину.
COLS = 3
WIDE_KINDS = ("dimmer", "light")


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


def _width(conf, kind):
    try:
        w = int(conf.get("screen_w") or 0)
    except (TypeError, ValueError):
        w = 0
    if not w:
        w = COLS if kind in WIDE_KINDS else 1
    return max(1, min(COLS, w))


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


def _table_map(raw, default):
    """Таблица значений фанкойла -> {"0": "Охлаждение", ...}."""
    from .tiles_fancoil import table
    return {str(k): str(v.get("title", k)) for k, v in table(raw, default)}


def _scenes(conf):
    """Плитка scenes (клавиши сеткой) -> отдельная плитка на каждую сцену."""
    out = []
    for item in conf.get("items") or []:
        if not isinstance(item, dict) or not item.get("channel"):
            continue
        cmd = command_topic(item["channel"], item.get("command_topic"))
        if not cmd:
            continue
        if item.get("command_on") is not None:
            payload = str(item["command_on"])
        else:
            raw = str(state.values.get(item["channel"]) or "").strip().lower()
            payload = Scene.FORMS.get(raw, ("1", "0"))[0]
        tile = {"type": "scene", "title": str(item.get("title") or ""), "w": 1,
                "cmd": cmd, "payload": payload}
        if item.get("icon"):
            tile["icon"] = str(item["icon"])
        out.append(tile)
    return out


def _screen_h(conf):
    """Высота плитки на экране в рядах (screen_h: 0.5, 1, 1.5, 2) или None."""
    try:
        h = float(conf.get("screen_h") or 0)
    except (TypeError, ValueError):
        return None
    return max(0.5, min(2.0, round(h * 2) / 2)) if h > 0 else None


def screen_tiles(conf):
    """Плитка панели -> плитки экрана (обычно одна; сцены - по одной на клавишу)."""
    if not isinstance(conf, dict) or conf.get("screen") is False:
        return []
    if resolve_type(conf, state) == "scenes":
        out = _scenes(conf)
    else:
        t = screen_tile(conf)
        out = [t] if t else []
    h = _screen_h(conf)
    if h:
        for t in out:
            t["h"] = h
    return out


def screen_tile(conf):
    """Плитка панели -> плитка экрана (или None, если экрану не нужна)."""
    if not isinstance(conf, dict) or conf.get("screen") is False:
        return None
    kind, bound = _bound(conf)
    title = str(conf.get("title") or "")
    w = _width(conf, kind)

    if kind == "header":
        return None

    if kind == "switch":
        out = {"type": "switch", "title": title, "w": w,
               "state": _t(bound, "value"), "cmd": _c(bound, "value")}
        if conf.get("invert"):
            out["invert"] = True
        return out if out["state"] else None

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
        return out if out["level"] else None

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
        first = series[0]
        lines = [{"topic": topic_of(s["channel"]), "unit": s.get("unit", ""),
                  "digits": int(s.get("digits", 1))} for s in series[1:3]]
        return _info(title or "График", w, topic_of(first["channel"]),
                     first.get("unit", ""), int(first.get("digits", 1)), lines=lines)

    if kind == "forecast":
        dev = conf.get("device") or "weather"
        return _info(title or "Погода", w, _dev(dev, "temperature"), "°C", 1,
                     lines=[{"topic": _dev(dev, "description")}])

    # Остальное (списки, камеры, календарь, вода, телевизор, ссылки) -
    # честная заглушка с названием.
    return _info(title or kind, w, note="на панели")


def screen_layout(panel_name):
    panels = web.config.panels
    panel = panels.get(panel_name) or {}
    pages = []
    for title, tiles, _src, _key in resolve_sections(panel, panels):
        out = [t for c in tiles for t in screen_tiles(c)]
        if out:
            pages.append({"title": title or panel.get("title") or panel_name,
                          "tiles": out})
    return {"version": 1, "panel": panel_name, "pages": pages}


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


__all__ = ["api_screen", "screen_layout", "screen_tile", "screen_tiles"]
