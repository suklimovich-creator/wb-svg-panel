# -*- coding: utf-8 -*-
"""
Демо своих панелей (с 1.54): панель из config.yaml на выдуманных приборах.

    demo_panels: [livingroom]      # в config.yaml

Страница /_demo-livingroom.html - та же гостиная, те же плитки, пульты,
жесты и живые плитки, но ни одна команда не уходит в MQTT. Каналы панели
переименованы в приборы витрины (/devices/_demo_<прибор>/controls/...):
нажатия меняют только состояние витрины (showcase.state), и «прибор»
отвечает сразу, как отвечал бы настоящий. Начальные значения - текущие
показания дома, графики - его настоящая история.

Плитки, которые трогают то, что не переименовать (краны и протечки -
модуль воды, камера, списки, переходы на другие панели), в демо не
попадают: нажатие на них дошло бы до настоящего дома.
"""

import copy
import re
import threading
import time

from . import showcase

#: Имя демо-панели: префикс + имя панели из конфига.
NAME_PREFIX = "_demo-"
#: Плитки, которых в демо нет: их команды или данные - настоящие.
SKIP_TYPES = ("valve", "leak", "camera", "list", "link", "today", "agenda", "scenes_link")

_LOCK = threading.Lock()
_cache = {}          # имя -> {"conf", "stamp", "back", "seeded", "follow", "off", "heat"}
_back = {}           # канал демо -> настоящий канал (графики, начальные значения)


def config_names(config):
    """Демо-панели из конфига: demo_panels: [livingroom, ...] (или строка)."""
    raw = config.get("demo_panels") if config else None
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [NAME_PREFIX + str(n) for n in raw if str(n) in (config.panels or {})]


def is_demo_panel(name):
    return str(name).startswith(NAME_PREFIX)


def source_name(name):
    return str(name)[len(NAME_PREFIX):]


# ------------------------------------------------------------ переименование
def _dev(device):
    return showcase.PREFIX + re.sub(r"[^A-Za-z0-9_-]+", "_", str(device))


def map_channel(channel):
    """«устройство/канал» -> «_demo_устройство/канал». Сырой топик шлюза
    (Sprut.hub-…/accessories/288/13/18) -> «_demo_sprut/accessories_288_13_18»."""
    ch = str(channel).strip()
    if ch.startswith("/devices/"):
        parts = ch.split("/")
        if len(parts) >= 5:
            return "%s/%s" % (_dev(parts[2]), parts[4])
    if ch.count("/") == 1:
        dev, control = ch.split("/", 1)
        return "%s/%s" % (_dev(dev), control)
    parts = [p for p in ch.split("/") if p]
    if not parts:
        return ch
    head = re.split(r"[-.]", parts[0])[0].lower() or "raw"
    return "%s/%s" % (_dev(head), "_".join(parts[1:]) or "value")


def map_command(topic):
    """Командный топик -> /devices/_demo_…/controls/…/on. У сырого
    топика шлюза команда - канал + /set: пишем в тот же канал демо."""
    t = str(topic).strip()
    if t.startswith("/devices/"):
        parts = t.split("/")
        if len(parts) >= 5:
            return "/devices/%s/controls/%s/on" % (_dev(parts[2]), parts[4])
    base = t[:-4] if t.endswith("/set") else t
    dev, control = map_channel(base).split("/", 1)
    return "/devices/%s/controls/%s/on" % (dev, control)


def _remap(obj, back):
    """Копия конфига плитки с каналами и командами демо."""
    if isinstance(obj, list):
        return [_remap(v, back) for v in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for key, value in obj.items():
        k = str(key)
        if isinstance(value, str) and (k.startswith("channel") or k == "value_channel"):
            out[key] = map_channel(value)
            back[out[key]] = value
        elif isinstance(value, str) and k.startswith("command_topic"):
            out[key] = map_command(value)
            base = value[:-4] if value.endswith("/set") else value
            if not base.startswith("/devices/"):
                back[map_channel(base)] = base
        elif isinstance(value, str) and k == "device":
            out[key] = _dev(value)
            back[out[key] + "/"] = value + "/"
        else:
            out[key] = _remap(value, back)
    return out


def _safe_tiles(tiles):
    return [t for t in (tiles or []) if isinstance(t, dict)
            and str(t.get("type", "value")) not in SKIP_TYPES]


def build(config, name):
    """Конфиг демо-панели по панели из конфига или None."""
    from .assemble import resolve_sections
    src_name = source_name(name)
    src = (config.panels or {}).get(src_name)
    if src is None or name not in config_names(config):
        return None
    back = {}
    sections = []
    for title, tiles, status_src, _key in resolve_sections(src, config.panels):
        sec = {"tiles": _remap(_safe_tiles(copy.deepcopy(tiles)), back)}
        if title:
            sec["title"] = title
        status = (status_src or {}).get("status")
        if status and status_src is not src:
            sec["status"] = _remap(copy.deepcopy(status), back)
        sections.append(sec)
    conf = {k: copy.deepcopy(v) for k, v in src.items()
            if k not in ("tiles", "sections", "hidden", "status")}
    if src.get("status"):
        conf["status"] = _remap(copy.deepcopy(src["status"]), back)
    if len(sections) == 1 and "title" not in sections[0]:
        conf["tiles"] = sections[0]["tiles"]
        if sections[0].get("status"):
            conf["status"] = sections[0]["status"]
    else:
        conf["sections"] = sections
    conf["title"] = str(src.get("title") or src_name) + " · демо"
    # Демо - чтобы трогать: нажатия, ручки сразу, жесты и живые плитки.
    conf["interactive"] = True
    conf["live_controls"] = True
    conf["tile_gestures"] = True
    return conf, back


# ------------------------------------------------------------ состояние
def _tiles_of(conf):
    out = list(conf.get("tiles") or [])
    for sec in conf.get("sections") or []:
        out.extend(sec.get("tiles") or [])
    return out


#: Если настоящего значения нет - с чего начать (по ключу конфига плитки).
_DEFAULTS = {"channel_brightness": "50", "channel_temp": "50", "channel_switch": "0",
             "channel_current": "22", "channel_target": "23", "channel_state": "0",
             "channel_mode": "1", "channel_goal": "50"}


def _seed(conf, back):
    """Начальные значения - то, что сейчас показывает дом."""
    from .state import state as real
    st = showcase.state
    with real.lock:
        values = dict(real.values)
        meta = {k: dict(v) for k, v in real.meta.items()}
    for demo_key, real_key in back.items():
        if demo_key.endswith("/"):           # прибор целиком (кондиционер, ТВ, погода)
            for key, value in values.items():
                if key.startswith(real_key):
                    _put(st, demo_key + key[len(real_key):], value, meta.get(key))
        elif real_key in values:
            _put(st, demo_key, values[real_key], meta.get(real_key))
    # Нет показаний (прибор молчит) - правдоподобное начало, чтобы плитка
    # отвечала на нажатия, а не стояла «нет данных».
    for tile in _tiles_of(conf):
        kind = tile.get("type")
        for key, value in tile.items():
            if not isinstance(value, str) or not key.startswith("channel"):
                continue
            if st.values.get(value) is not None:
                continue
            default = _DEFAULTS.get(key)
            if key == "channel":
                default = {"switch": "0", "dimmer": "0", "roller": "50", "curtain": "50",
                           "scene": "0"}.get(kind, default)
            if key == "channel_state" and kind in ("roller", "curtain"):
                default = "2"                       # стоит
            if default is not None:
                _put(st, value, default, None)
        # графики: последнее показание, если датчик молчит
        for s in tile.get("series") or []:
            ch = s.get("channel") if isinstance(s, dict) else None
            if ch and st.values.get(ch) is None:
                _put(st, ch, _sensor_default(ch), None)
        # приборы целиком: кондиционер и телевизор без моста
        dev = tile.get("device")
        if kind == "ac" and dev:
            for control, value in (("power", "0"), ("mode", "cool"), ("target_temp", "23"),
                                   ("current_temp", "24.5"), ("fan_mode", "auto"),
                                   ("quiet", "0"), ("online", "1")):
                if st.values.get(dev + "/" + control) is None:
                    _put(st, dev + "/" + control, value, None)
        if kind == "tv" and dev and st.values.get(dev + "/state") is None:
            _put(st, dev + "/state", "off", None)


def _sensor_default(channel):
    c = channel.lower()
    if "co2" in c:
        return "650"
    if "voc" in c:
        return "120"
    if "humid" in c:
        return "45"
    return "22.5"


def _put(st, key, value, meta):
    dev, control = key.split("/", 1)
    st.on_message("/devices/%s/controls/%s" % (dev, control), str(value))
    if meta:
        import json
        st.on_message("/devices/%s/controls/%s/meta" % (dev, control), json.dumps(meta))


def _rules(conf):
    """Как «приборы» демо отвечают: штора едет к цели, термостат греет,
    сцена «выключить всё» гасит свет панели."""
    follow, heat, switches = {}, [], []
    for tile in _tiles_of(conf):
        kind = tile.get("type")
        if tile.get("channel_goal") and tile.get("channel"):
            follow[tile["channel_goal"]] = (tile["channel"], tile.get("channel_state"))
        cmd = tile.get("command_topic")
        if kind in ("roller", "curtain") and cmd and tile.get("channel"):
            parts = cmd.split("/")
            ck = "%s/%s" % (parts[2], parts[4]) if len(parts) >= 5 else None
            if ck and ck != tile["channel"]:
                follow[ck] = (tile["channel"], tile.get("channel_state"))
        if kind == "thermostat" and tile.get("channel_state") and tile.get("channel_current"):
            heat.append((tile.get("channel_target"), tile["channel_current"],
                         tile["channel_state"], tile.get("channel_mode")))
        if kind in ("switch", "dimmer") and tile.get("channel"):
            switches.append(tile["channel"])
        if kind == "light" and tile.get("channel_switch"):
            switches.append(tile["channel_switch"])
    return follow, heat, switches


#: Через сколько секунд без нажатий демо снова берёт показания дома.
RESET_AFTER = 600


def panel(config, name):
    """Конфиг демо-панели для сборки; при первом показе и после долгой
    тишины засевает состояние показаниями дома."""
    with _LOCK:
        stamp = getattr(config, "_mtime", 0)
        item = _cache.get(name)
        if item is None or item["stamp"] != stamp:
            built = build(config, name)
            if built is None:
                return None
            conf, back = built
            follow, heat, switches = _rules(conf)
            item = {"conf": conf, "back": back, "stamp": stamp, "seeded": 0,
                    "follow": follow, "heat": heat, "switches": switches, "touch": 0}
            _cache[name] = item
            _back.update(back)
        now = time.time()
        if not item["seeded"] or now - max(item["seeded"], item["touch"]) > RESET_AFTER:
            _seed(item["conf"], item["back"])
            item["seeded"] = now
        return copy.deepcopy(item["conf"])


def after_publish(key, value):
    """Нажатие в демо уже записано в состояние витрины (showcase.publish);
    здесь - то, что сделал бы сам прибор."""
    st = showcase.state
    for item in list(_cache.values()):
        item["touch"] = time.time()
        if key in item["follow"]:
            pos, moving = item["follow"][key]
            _put(st, pos, value, None)
            if moving:
                _put(st, moving, "2", None)          # приехала и стоит
        for target, current, heating, mode in item["heat"]:
            if key in (target, mode):
                try:
                    t = float(st.values.get(target) or 0)
                    c = float(st.values.get(current) or 0)
                    on = str(st.values.get(mode, "1")).strip().lower() not in ("0", "false", "off")
                except ValueError:
                    continue
                _put(st, heating, "1" if on and t > c + 0.2 else "0", None)
        control = key.split("/", 1)[1] if "/" in key else key
        if "off" in control.lower() and key.split("/")[0].lower().endswith("scenes"):
            for sw in item["switches"]:
                _put(st, sw, "0", None)


def real_channel(channel):
    """Настоящий канал за каналом демо - для графиков (история дома)."""
    channel = str(channel)
    if channel in _back:
        return _back[channel]
    dev = channel.split("/", 1)[0] + "/"
    if dev in _back:
        return _back[dev] + channel.split("/", 1)[1]
    return None
