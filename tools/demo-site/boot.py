# -*- coding: utf-8 -*-
"""
Сервер панели внутри браузера (Pyodide) для демо-страницы без контроллера.

Страница (tools/demo-site/shim.js) перехватывает fetch и шлёт запросы сюда
через worker.js; здесь их отвечает настоящий Flask-сервер панели через
test_client - те же картинки, пульты и команды, что на контроллере. MQTT
нет: команда сразу меняет состояние, а «прибор» отвечает правилами ниже.

Работает и в обычном Python - так build.py и проверки гоняют то же самое:

    import boot; boot.start("/путь/к/сборке"); boot.handle("GET", "/dom.svg?cols=3")
"""

import json
import math
import os
import sys
import time

ROOT = None
client = None
state = None
conf = None
_rules = None


# ------------------------------------------------------------ состояние
def _put(key, value):
    dev, control = key.split("/", 1)
    state.on_message("/devices/%s/controls/%s" % (dev, control), str(value))


def _tiles():
    out = list(conf.get("tiles") or [])
    for sec in conf.get("sections") or []:
        out.extend(sec.get("tiles") or [])
    return out


def _sensor(channel, salt=0.0):
    c = channel.lower()
    if "co2" in c:
        return 520 + (salt * 260) // 10 * 10
    if "humid" in c:
        return 38 + round(salt * 12)
    if "motion" in c:
        return 120 if salt > 0.5 else 8
    return round(21.4 + salt * 2.4, 1)


def _salt(text):
    return (sum(map(ord, text)) % 97) / 97.0


def _weather(dev):
    """Прогноз на сутки от текущего часа: осенняя Москва, днём теплее."""
    now = time.localtime()
    temps, codes, probs, rains, clouds = [], [], [], [], []
    for i in range(24):
        h = (now.tm_hour + i) % 24
        t = 7.5 + 4.0 * math.sin((h - 9) / 24.0 * 2 * math.pi) + 0.6 * math.sin(i / 3.0)
        temps.append("%.1f" % t)
        rainy = 13 <= (i + now.tm_hour) % 24 <= 16
        codes.append("61" if rainy else ("3" if h % 5 else "2"))
        probs.append("70" if rainy else "15")
        rains.append("0.6" if rainy else "0")
        clouds.append("90" if rainy else "60")
    start = time.strftime("%Y-%m-%dT%H:00", now)
    for control, value in (("temperature", temps[0]), ("description", "Облачно"),
                           ("humidity", "78"), ("pressure", "748"), ("windspeed", "4"),
                           ("forecast_ok", "1"), ("forecast_start", start),
                           ("temp_series", ",".join(temps)), ("code_series", ",".join(codes)),
                           ("precip_prob_series", ",".join(probs)),
                           ("precip_series", ",".join(rains)),
                           ("cloud_series", ",".join(clouds))):
        _put("%s/%s" % (dev, control), value)


def seed():
    """Правдоподобное начало: часть света горит, климат работает."""
    n = 0
    for tile in _tiles():
        kind = tile.get("type")
        n += 1
        on = n % 3 != 0
        for s in tile.get("series") or []:
            if isinstance(s, dict) and s.get("channel"):
                _put(s["channel"], _sensor(s["channel"], _salt(s["channel"])))
        if kind in ("switch",) and tile.get("channel"):
            _put(tile["channel"], "1" if on else "0")
        elif kind == "dimmer":
            _put(tile["channel"], "1" if on else "0")
            if tile.get("channel_brightness"):
                _put(tile["channel_brightness"], 30 + (n * 17) % 60)
        elif kind == "light":
            _put(tile["channel_switch"], "1" if on else "0")
            _put(tile["channel_brightness"], 40 + (n * 13) % 50)
            if tile.get("channel_temp"):
                _put(tile["channel_temp"], (n * 29) % 90 + 5)
        elif kind == "scene" and tile.get("channel"):
            _put(tile["channel"], "0")
        elif kind in ("roller", "curtain"):
            _put(tile["channel"], 60 if kind == "roller" else 35)
            if tile.get("channel_state"):
                _put(tile["channel_state"], "2")
        elif kind == "thermostat":
            floor = tile.get("scale_max", 35) > 35
            cur = 26.5 if floor else 21.8
            tgt = 28 if floor else 23
            _put(tile["channel_current"], cur)
            _put(tile["channel_target"], tgt)
            if tile.get("channel_mode"):
                _put(tile["channel_mode"], "1")
            if tile.get("channel_state"):
                _put(tile["channel_state"], "1" if tgt > cur else "0")
        elif kind == "ac" and tile.get("device"):
            dev = tile["device"]
            if state.values.get(dev + "/power") is None:
                for control, value in (("power", "1" if on else "0"), ("mode", "cool"),
                                       ("target_temp", "23"), ("current_temp", "24.6"),
                                       ("fan_mode", "auto"), ("quiet", "0"),
                                       ("swing_mode", "auto"), ("online", "1")):
                    _put("%s/%s" % (dev, control), value)
        elif kind == "tv" and tile.get("device"):
            dev = tile["device"]
            for control, value in (("state", "on"), ("volume", "18"), ("app", "YouTube"),
                                   ("power", "1")):
                _put("%s/%s" % (dev, control), value)
        elif kind == "forecast":
            _weather(tile.get("device", "weather"))
    for sec in conf.get("sections") or []:
        for chip in sec.get("status") or []:
            ch = chip.get("channel") if isinstance(chip, dict) else None
            if ch and state.values.get(ch) is None:
                _put(ch, _sensor(ch, _salt(ch)))


# ------------------------------------------------------------ «приборы»
def rules():
    follow, heat, lights, scenes = {}, [], [], {}
    for tile in _tiles():
        kind = tile.get("type")
        cmd = tile.get("command_topic")
        if kind in ("roller", "curtain") and cmd and tile.get("channel"):
            parts = cmd.split("/")
            if len(parts) >= 5:
                follow["%s/%s" % (parts[2], parts[4])] = (tile["channel"], tile.get("channel_state"))
        if kind == "thermostat" and tile.get("channel_state"):
            heat.append((tile.get("channel_target"), tile.get("channel_current"),
                         tile["channel_state"], tile.get("channel_mode")))
        if kind in ("switch", "dimmer") and tile.get("channel"):
            lights.append((kind, tile["channel"], tile.get("channel_brightness"), None))
        if kind == "light" and tile.get("channel_switch"):
            lights.append((kind, tile["channel_switch"], tile.get("channel_brightness"),
                           tile.get("channel_temp")))
        if kind == "scene" and tile.get("channel"):
            scenes[tile["channel"]] = str(tile.get("title", "")).lower() + " " + tile["channel"].lower()
    return follow, heat, lights, scenes


def _scene(name):
    follow, heat, lights, scenes = _rules
    if "ярк" in name or "bright" in name:
        plan = lambda kind: ("1", 100, 20)
    elif "вечер" in name or "evening" in name:
        plan = lambda kind: ("0", None, None) if kind == "switch" else ("1", 30, 90)
    else:                                   # «выключить всё», «спать»
        plan = lambda kind: ("0", None, None)
    for kind, sw, level, temp in lights:
        on, lv, tt = plan(kind)
        _put(sw, on)
        if level and lv is not None:
            _put(level, lv)
        if temp and tt is not None:
            _put(temp, tt)


def after(key, value):
    follow, heat, lights, scenes = _rules
    dev, control = key.split("/", 1)
    if key in follow:
        pos, moving = follow[key]
        _put(pos, value)
        if moving:
            _put(moving, "2")
    for target, current, heating, mode in heat:
        if key in (target, mode):
            try:
                t = float(state.values.get(target) or 0)
                c = float(state.values.get(current) or 0)
            except ValueError:
                continue
            on = str(state.values.get(mode, "1")).strip().lower() not in ("0", "false", "off")
            _put(heating, "1" if on and t > c + 0.2 else "0")
    if key in scenes and str(value) not in ("0", "false"):
        _scene(scenes[key])
        _put(key, "0")
    if control == "power" and state.values.get(dev + "/state") is not None:
        _put(dev + "/state", "on" if str(value) in ("1", "true", "on") else "off")
    if control.startswith("app_"):
        _put(dev + "/app", control[4:].replace("_", " ").title())
    if control in ("volume_up", "volume_down"):
        try:
            v = int(float(state.values.get(dev + "/volume") or 0))
        except ValueError:
            v = 0
        _put(dev + "/volume", max(0, min(100, v + (1 if control == "volume_up" else -1))))


class Mqtt(object):
    """Брокер понарошку: команда сразу становится состоянием прибора."""

    def publish(self, topic, value, *a, **k):
        if topic.endswith("/on"):
            base = topic[:-3]
            state.on_message(base, str(value))
            parts = base.split("/")
            if len(parts) >= 5:
                after("%s/%s" % (parts[2], parts[4]), value)


class History(object):
    """Графики за прошлое: плавная кривая к текущему показанию."""
    mode = "sqlite"

    def get(self, channel, span, points):
        try:
            last = float(state.values.get(channel))
        except (TypeError, ValueError):
            last = _sensor(channel, _salt(channel))
        k = _salt(channel)
        amp = max(abs(last) * 0.06, 0.6)
        now = time.time()
        out = []
        for i in range(points):
            x = i / float(max(points - 1, 1))
            wave = amp * (math.sin(x * 7 + k * 6) * 0.8 + math.sin(x * 23 + k) * 0.25)
            out.append((now - span + x * span, last + wave * (1 - x ** 3)))
        return out

    def refresh(self, *a, **k):
        return []

    def channels_with_history(self):
        return {}


# ------------------------------------------------------------ запуск
def start(root):
    """Поднять сервер панели на сборке в папке root (там wbpanel/, config.yaml)."""
    global ROOT, client, state, conf, _rules
    ROOT = root
    os.environ["TZ"] = "Europe/Moscow"
    if hasattr(time, "tzset"):
        time.tzset()
    for p in (os.path.join(root, "lib"), os.path.join(root, "tests", "fixtures"), root):
        if p not in sys.path:
            sys.path.insert(0, p)
    stubs = os.path.join(root, "stubs")
    if stubs not in sys.path:
        sys.path.append(stubs)          # sqlite3 и прочее, чего в браузере нет
    import env
    web, _palette, cfg, _sent = env.setup(os.path.join(root, "config.yaml"),
                                         freeze_time=False, history=False)
    from wbpanel import web_live
    from wbpanel.state import state as st
    state = st
    # Ждущий запрос держит страница (shim.js), а не сервер: поток здесь один.
    web_live.wait_for_panel = lambda name, since, timeout: state.version
    # Каналы сборки названы как у демо (_demo_…), но здесь всё - демо:
    # команды и графики идут в обычное состояние, а не в витрину тем.
    from wbpanel import showcase
    showcase.owns = lambda topic: False
    showcase.owns_channel = lambda channel: False
    web.history = History()
    web.mqtt_client = Mqtt()
    name = sorted(cfg.panels)[0]
    conf = cfg.panels[name]
    _rules = rules()
    seed()
    client = web.app.test_client()
    return name


def handle(method, url, body=None, headers=None):
    """Запрос страницы -> (код, заголовки, тело строкой)."""
    if not url.startswith("/"):
        url = "/" + url
    r = client.open(url, method=method or "GET", data=body,
                    headers=dict(headers or {}))
    out = dict((k, v) for k, v in r.headers.items()
               if k.lower() not in ("content-length", "content-encoding"))
    data = r.get_data()
    if out.get("Content-Encoding") == "gzip":
        import gzip
        data = gzip.decompress(data)
    return r.status_code, out, data.decode("utf-8", "replace")


def handle_json(method, url, body=None, headers=None):
    """То же для worker.js: одной строкой JSON."""
    try:
        h = json.loads(headers) if isinstance(headers, str) else headers
        status, out, text = handle(method, url, body, h)
    except Exception as e:  # ошибка сервера - видна на странице, а не тишина
        import traceback
        status, out, text = 500, {"Content-Type": "text/plain"}, traceback.format_exc()
    return json.dumps([status, out, text])
