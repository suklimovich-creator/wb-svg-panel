"""
Витрина тем (с 1.28): выдуманная панель со всеми плитками для /themes.

Панель не из config.yaml и не из MQTT. У неё своё состояние - отдельный
WbState, засеянный правдоподобными значениями, - и своя история для
графиков. Нажатия на её плитках и пультах до брокера не доходят: топики
/devices/_demo_*/controls/*/on меняют только это состояние, и витрина
отвечает, как отвечал бы прибор. Так тему можно примерить на всех
плитках, во всех состояниях и со всеми пультами, даже если у себя дома
половины этих приборов нет.

Имя панели - «_demo»: страница /_demo.html, картинка /_demo.svg. Оно
занято витриной - панель с таким именем в config.yaml видна не будет.
"""

import copy
import math
import re
import threading
import time

from .state import WbState

NAME = "_demo"
#: Устройства витрины. Только они пишутся «в обход» брокера.
PREFIX = "_demo_"


def is_demo(name):
    return name == NAME


_TOPIC = re.compile(r"^/devices/" + PREFIX + r"[^/]+/controls/[^/]+/on$")


def owns(topic):
    """Топик витрины - не уходит в MQTT. Только запись в канал: .../on."""
    return bool(_TOPIC.match(str(topic)))


def owns_channel(channel):
    return str(channel).startswith(PREFIX)


# ----------------------------------------------------------------- панель
_LED = "_demo_led/"
_PANEL = {
    "title": "Витрина",
    "cols": 2,
    "interactive": True,
    "status": [{"air": "_demo_msw/CO2"}],
    "sections": [
        {"title": "Свет", "tiles": [
            {"type": "light", "w": 0.5, "h": 1, "title": "Лента",
             "channel_switch": _LED + "CCT1", "channel_brightness": _LED + "CCT1 Brightness",
             "channel_temp": _LED + "CCT1 Temperature", "temp_unit": "percent",
             "temp_min": 2700, "temp_max": 6500},
            {"type": "dimmer", "w": 0.5, "h": 1, "title": "Споты",
             "channel": "_demo_dim/K1", "channel_brightness": "_demo_dim/Channel 1"},
            {"type": "light", "w": 1, "h": 0.5, "title": "Подсветка",
             "channel_switch": _LED + "CCT2", "channel_brightness": _LED + "CCT2 Brightness",
             "channel_temp": _LED + "CCT2 Temperature", "temp_unit": "percent",
             "temp_min": 2700, "temp_max": 6500},
            {"type": "dimmer", "w": 1, "h": 0.5, "title": "Трек",
             "channel": "_demo_dim/K2", "channel_brightness": "_demo_dim/Channel 2"},
            {"type": "switch", "w": 0.5, "h": 1, "title": "Бра", "channel": "_demo_relay/K1"},
            {"type": "switch", "w": 0.5, "h": 1, "title": "Коридор", "channel": "_demo_relay/K2"},
            {"type": "switch", "title": "Вентилятор", "channel": "_demo_relay/K3"},
            {"type": "scenes", "w": 1.5, "h": 1.5, "title": "Сцены", "items": [
                {"title": "Выкл. всё", "icon": "power", "channel": "_demo_scenes/off_all"},
                {"title": "Спать", "icon": "moon", "channel": "_demo_scenes/bedtime"},
                {"title": "Кино", "icon": "tv", "channel": "_demo_scenes/cinema"},
                {"title": "Вечер", "icon": "bulb", "channel": "_demo_scenes/evening"},
                {"title": "Ужин", "icon": "bulb", "channel": "_demo_scenes/dinner"},
                {"title": "Чтение", "icon": "bulb", "channel": "_demo_scenes/reading"},
                {"title": "Гости", "icon": "bulb", "channel": "_demo_scenes/guests"},
                {"title": "Уборка", "icon": "sun", "channel": "_demo_scenes/cleaning"},
                {"title": "Ярко", "icon": "sun", "channel": "_demo_scenes/bright", "level": 1},
            ]},
            {"type": "value", "w": 0.5, "h": 0.5, "title": "Улица",
             "channel": "_demo_out/Temperature", "unit": "°C", "icon": "thermo"},
            {"type": "value", "w": 0.5, "h": 0.5, "title": "Влажность",
             "channel": "_demo_msw/Humidity", "unit": "%", "icon": "drop"},
            {"type": "switch", "w": 0.5, "h": 0.5, "title": "Ночник", "channel": "_demo_relay/K4"},
        ]},
        {"title": "Климат", "tiles": [
            {"type": "thermostat", "title": "Гостиная",
             "channel_current": "_demo_th1/current", "channel_target": "_demo_th1/target",
             "channel_state": "_demo_th1/heating", "channel_mode": "_demo_th1/power",
             "target_min": 10, "target_max": 38, "step": 0.5},
            {"type": "thermostat", "title": "Тёплый пол",
             "channel_current": "_demo_th2/current", "channel_target": "_demo_th2/target",
             "channel_state": "_demo_th2/heating", "channel_mode": "_demo_th2/power",
             "scale_min": 15, "scale_max": 45},
            {"type": "ac", "title": "Кондиционер", "device": "_demo_ac1"},
            {"type": "ac", "title": "Спальня", "device": "_demo_ac2"},
            {"type": "chart", "title": "Гостиная", "icon": "thermo", "w": 2, "range": "12h",
             "series": [{"channel": "_demo_msw/Temperature", "unit": "°C"},
                        {"channel": "_demo_msw/Humidity", "unit": "%", "digits": 0}]},
            {"type": "chart", "title": "Воздух", "icon": "co2", "w": 2, "range": "12h",
             "series": [{"channel": "_demo_msw/CO2", "unit": "ppm", "digits": 0},
                        {"channel": "_demo_msw/Air Quality (VOC)", "digits": 0}]},
        ]},
        {"title": "Окна и техника", "tiles": [
            {"type": "curtain", "title": "Штора", "channel": "_demo_curtain/position"},
            {"type": "roller", "title": "Римская", "channel": "_demo_roller/position"},
            {"type": "tv", "title": "Телевизор", "device": "_demo_tv", "w": 2, "h": 0.5,
             "apps": [{"control": "app_kinopoisk", "title": "Кинопоиск"},
                      {"control": "app_youtube", "title": "YouTube"}]},
        ]},
    ],
}

#: Начальные значения: «device/control» -> значение.
_SEED = {
    _LED + "CCT1": "1", _LED + "CCT1 Brightness": "70", _LED + "CCT1 Temperature": "30",
    _LED + "CCT2": "0", _LED + "CCT2 Brightness": "40", _LED + "CCT2 Temperature": "80",
    "_demo_dim/K1": "1", "_demo_dim/Channel 1": "60",
    "_demo_dim/K2": "0", "_demo_dim/Channel 2": "35",
    "_demo_relay/K1": "1", "_demo_relay/K2": "0", "_demo_relay/K3": "0", "_demo_relay/K4": "1",
    "_demo_out/Temperature": "7.4", "_demo_msw/Temperature": "22.6",
    "_demo_msw/Humidity": "43", "_demo_msw/CO2": "1180",
    "_demo_msw/Air Quality (VOC)": "140",
    "_demo_th1/current": "21.5", "_demo_th1/target": "24", "_demo_th1/heating": "1",
    "_demo_th1/power": "1",
    "_demo_th2/current": "26.5", "_demo_th2/target": "28", "_demo_th2/heating": "0",
    "_demo_th2/power": "0",
    "_demo_curtain/position": "40", "_demo_roller/position": "60",
    "_demo_tv/state": "on", "_demo_tv/volume": "18", "_demo_tv/app": "YouTube",
}
_AC = {
    "_demo_ac1": dict(power="1", mode="cool", current_temp="26.5", target_temp="24",
                      fan_mode="medium", quiet="0", swing_mode="position_2",
                      swing_h_mode="position_1", online="1"),
    "_demo_ac2": dict(power="0", mode="heat", current_temp="20.1", target_temp="23",
                      fan_mode="auto", quiet="0", swing_mode="auto",
                      swing_h_mode="auto", online="1"),
}
for _dev, _controls in _AC.items():
    for _k, _v in _controls.items():
        _SEED["%s/%s" % (_dev, _k)] = _v
for _item in _PANEL["sections"][0]["tiles"][7]["items"]:
    _SEED[_item["channel"]] = "0"

_UNITS = {"_demo_msw/CO2": "ppm", "_demo_msw/Temperature": "°C",
          "_demo_msw/Humidity": "%", "_demo_out/Temperature": "°C"}


class _Everything(set):
    """Витрина следит за всеми своими каналами - список не нужен."""

    def __contains__(self, item):
        return True


class _State(WbState):
    def __init__(self):
        WbState.__init__(self)
        self.watched = _Everything()
        self.connected = True
        self.seeded_at = 0.0

    def seed(self):
        for key, value in _SEED.items():
            dev, control = key.split("/", 1)
            self.on_message("/devices/%s/controls/%s" % (dev, control), value)
        for key, units in _UNITS.items():
            dev, control = key.split("/", 1)
            self.on_message("/devices/%s/controls/%s/meta/units" % (dev, control), units)
        self.seeded_at = time.time()


state = _State()
state.seed()
_LOCK = threading.Lock()

#: Через сколько секунд без нажатий витрина возвращается к началу: чтобы
#: следующий, кто откроет /themes, увидел все состояния, а не чужие опыты.
RESET_AFTER = 600
_last_touch = [time.time()]


def panel():
    """Конфиг панели витрины - копия, сборка может её дополнять."""
    if time.time() - _last_touch[0] > RESET_AFTER:
        with _LOCK:
            _last_touch[0] = time.time()
            state.seed()
    return copy.deepcopy(_PANEL)


def publish(topic, value):
    """Нажатие на витрине: прибор «слушается» сразу, без брокера."""
    _last_touch[0] = time.time()
    topic = topic[:-3]
    parts = topic.split("/")
    # Кнопки пульта телевизора и сцен - новые ключи; но не сотни: витрину
    # нельзя раздуть, засыпав её выдуманными топиками.
    if len(parts) == 5 and "%s/%s" % (parts[2], parts[4]) not in state.values \
            and len(state.values) > 300:
        return
    state.on_message(topic, str(value))


class _History:
    """История для графиков витрины: гладкие волны, одинаковые при каждом
    показе - чтобы темы сравнивались на одной и той же картинке."""
    mode = "demo"

    def get(self, channel, span, points):
        now = time.time()
        points = max(int(points or 2), 2)
        out = []
        for i in range(points):
            t = now - span + i * span / float(points - 1)
            f = i / float(points - 1)
            if "CO2" in channel:
                v = 520 + 660 * f + 110 * math.sin(i / 6.0)
            elif "VOC" in channel:
                v = 100 + 40 * f + 15 * math.sin(i / 5.0)
            elif "Humidity" in channel:
                v = 42 + 3 * math.sin(i / 9.0)
            elif "_demo_out" in channel:
                v = 6 + 2.5 * math.sin(i / 14.0)
            else:
                v = 22.4 + 0.5 * math.sin(i / 8.0)
            out.append((t, v))
        return out

    def refresh(self, *args, **kwargs):
        return []

    def channels_with_history(self):
        return []


history = _History()
