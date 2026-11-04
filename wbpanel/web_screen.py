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

У плитки панели для экрана можно задать:
    screen_w: 1..3      ширина в колонках экрана (их 3)
    screen: false       не показывать на экране
"""

import json
from flask import Response, abort

from . import web
from .assemble import resolve_sections
from .registry import REGISTRY, resolve_type
from .sources import bind
from .state import state
from .tiles import AC_FAN_RU, AC_MODE_RU, HEAT_MODE, command_topic
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
          active=None, note=None):
    out = {"type": "info", "title": title, "w": w}
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
        out.append({"type": "scene", "title": str(item.get("title") or ""), "w": 1,
                    "cmd": cmd, "payload": payload})
    return out


def screen_tiles(conf):
    """Плитка панели -> плитки экрана (обычно одна; сцены - по одной на клавишу)."""
    if not isinstance(conf, dict) or conf.get("screen") is False:
        return []
    if resolve_type(conf, state) == "scenes":
        return _scenes(conf)
    t = screen_tile(conf)
    return [t] if t else []


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
        return {"type": "scene", "title": title, "w": w, "cmd": cmd,
                "payload": _scene_payload(conf, bound)}

    if kind in ("dimmer", "light"):
        lo, hi = _limits(bound, "bright", 0, 100)
        out = {"type": "dimmer", "title": title, "w": w,
               "state": _t(bound, "switch"), "state_cmd": _c(bound, "switch"),
               "level": _t(bound, "bright"), "level_cmd": _c(bound, "bright"),
               "max": hi}
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
        return {"type": "sensor", "title": title, "w": w, "value": topic_of(b.key),
                "unit": conf.get("unit") or b.units or "",
                "digits": int(conf.get("digits", 1))}

    # ---- заглушки: показывают состояние, управление - на панели --------
    if kind == "thermostat":
        return _info(title or "Термостат", w, _t(bound, "target"), "°C", 1,
                     lines=[{"label": "сейчас", "topic": _t(bound, "current"),
                             "unit": "°", "digits": 1},
                            {"topic": _t(bound, "mode"),
                             "map": {str(k): v for k, v in HEAT_MODE.items()}}],
                     active=_t(bound, "state"))

    if kind == "fancoil":
        from .tiles_fancoil import FANCOIL_FAN_STATES, FANCOIL_MODES
        return _info(title or "Фанкойл", w, _t(bound, "target"), "°C", 1,
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
        return _info(title or "Кондиционер", w, _dev(dev, "target_temp"), "°C", 0,
                     lines=[{"topic": _dev(dev, "mode"), "map": AC_MODE_RU},
                            {"label": "сейчас", "topic": _dev(dev, "current_temp"),
                             "unit": "°", "digits": 0},
                            {"label": "вент.", "topic": _dev(dev, "fan_mode"),
                             "map": AC_FAN_RU}],
                     active=_dev(dev, "power"))

    if kind in ("curtain", "roller"):
        pos = _t(bound, "state") or _t(bound, "target")
        return _info(title or "Штора", w, pos, "%", 0)

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
