# -*- coding: utf-8 -*-
"""
Состояние приборов для проверки рендера (tests/render_check.py) и для
тестового сервера (tools/visual/serve.py): кондиционеры, термостаты,
свет, шторы, фанкойлы, вода, пункты списка, события календаря.

Всё выдуманное: имена устройств и топики - только для showcase.yaml.
"""

import datetime as dt


def _put(state, pairs):
    for topic, value in pairs.items():
        dev, ctl = topic.split("/", 1)
        state.on_message("/devices/%s/controls/%s" % (dev, ctl), value)


AC = {
    "ac_cool": dict(power="1", mode="cool", current_temp="26.5", target_temp="24", fan_mode="medium",
                    quiet="0", swing_mode="position_2", swing_h_mode="position_1", online="1"),
    "ac_heat": dict(power="1", mode="heat", current_temp="21.5", target_temp="24", fan_mode="auto",
                    quiet="0", swing_mode="auto", swing_h_mode="position_6", online="1"),
    "ac_off": dict(power="0", mode="cool", current_temp="25.1", target_temp="23", fan_mode="low",
                   quiet="0", swing_mode="position_3", swing_h_mode="auto", online="1"),
    "ac_auto": dict(power="1", mode="auto", current_temp="20.0", target_temp="22", fan_mode="high",
                    quiet="1", swing_mode="bottom", swing_h_mode="auto", online="1"),
    "ac_dry": dict(power="1", mode="dry", current_temp="25.0", target_temp="23", fan_mode="low",
                   quiet="0", swing_mode="upper", online="1"),
}

DEVICES = {
    # термостаты, тёплый пол, выключатели, шторы, телевизор
    "th/cur": "21.5", "th/tgt": "24", "th/st": "1", "th/mode": "1",
    "fl/target_temp": "28", "fl/current_temp": "26.5", "fl/heating": "0", "fl/power": "1",
    "sw/K1": "1", "sw/K2": "0", "sw/K3": "1", "cu/pos": "40", "cu/st": "2", "ro/pos": "60", "ro/st": "2",
    "tv_living/state": "on", "tv_living/volume": "18", "tv_living/app": "YouTube",
    # свет
    "led/CCT1": "1", "led/CCT1 Brightness": "70", "led/CCT1 Temperature": "30",
    "led/CCT2": "1", "led/CCT2 Brightness": "40", "led/CCT2 Temperature": "80",
    "led/CCT3": "0", "led/CCT3 Brightness": "50", "led/CCT3 Temperature": "20",
    "th2/cur": "19.5", "th2/tgt": "22", "th2/st": "0", "th2/mode": "0",
    "dm/K1": "1", "dm/Channel 1": "60", "dm/K2": "0", "dm/Channel 2": "60",
    "dm/K3": "1", "dm/Channel 3": "35", "dm/K4": "1", "dm/Channel 4": "80",
    "sw/K4": "0", "sw/K5": "1", "sw/K6": "0", "sw/K7": "1",
    # датчики для чипов разделов
    "msw/Temperature": "22.4", "msw/Humidity": "41", "leak/alarm": "1",
    # фанкойлы и вода
    "fc/power": "1", "fc/mode": "0", "fc/fan": "2", "fc/fans": "2", "fc/tgt": "22", "fc/cur": "24.5", "fc/valve": "1",
    "mw/Input F1": "0", "mw/Input F3": "1", "mw/Leakage Mode": "1", "mw/Output K1": "1", "mw/Output K2": "0",
    "fc2/power": "1", "fc2/mode": "1", "fc2/fan": "0", "fc2/fans": "1", "fc2/tgt": "23.5", "fc2/cur": "21.0", "fc2/valve": "1",
    "fc3/power": "0", "fc3/mode": "0", "fc3/fan": "3", "fc3/fans": "4", "fc3/tgt": "21", "fc3/cur": "22.8", "fc3/valve": "0",
    "fc4/power": "1", "fc4/mode": "2", "fc4/fan": "1", "fc4/fans": "1", "fc4/tgt": "18", "fc4/cur": "22.8", "fc4/valve": "0",
}

SCENES = ("off_all", "bedtime", "cinema", "relax", "evening", "dinner", "reading", "guests",
          "cleaning", "bright")

ITEMS = ("Молоко 2 л", "Хлеб бородинский", "Сыр", "Кофе в зёрнах", "Лампочка E14 тёплая",
         "Батарейки AA")

#: (через сколько часов от «сейчас», длительность, название)
EVENTS = ((-3, 1, "Зарядка"), (0, 2, "Работа над панелью"), (2, 1, "Тренировка"),
          (4, 1, "Ужин с родителями"), (24, 1, "Бассейн"), (27, 2, "Врач, плановый осмотр"))


def seed(state, now=None):
    """Заполнить состояние, список shopping и календарь family."""
    for dev, controls in AC.items():
        for ctl, value in controls.items():
            state.on_message("/devices/%s/controls/%s" % (dev, ctl), value)
    _put(state, DEVICES)
    for ch in SCENES:
        state.on_message("/devices/lightingScenes/controls/%s" % ch, "false")

    from wbpanel.lists import lists
    if lists.known("shopping") and not lists.view("shopping")["open"]:
        for text in ITEMS:
            lists.add("shopping", text)

    from wbpanel.calendars import calendars
    if calendars.known("family"):
        tz = calendars.tz()
        base = (now or dt.datetime.now(tz)).replace(minute=0, second=0, microsecond=0)
        events = [{"title": t, "start": base + dt.timedelta(hours=h),
                   "end": base + dt.timedelta(hours=h + n), "all_day": False,
                   "exdate": [], "rdate": []} for h, n, t in EVENTS]
        with calendars.lock:
            calendars.data["family"] = {"ok": True, "error": None, "events": events, "seq": 1}
