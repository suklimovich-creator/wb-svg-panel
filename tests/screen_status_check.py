# -*- coding: utf-8 -*-
"""Декларативные status-чипы для esp32-panel 0.12.9+ (wb-svg-panel 1.87.2)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402


def main():
    web, _palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "espstatus.yaml"), freeze_time=True)
    client = web.app.test_client()
    response = client.get("/api/screen/panel-status")
    assert response.status_code == 200
    layout = response.get_json()

    motion, ac, co2, voc, leak = layout["status"]
    assert motion == {"kind": "status", "channel": "/devices/motion/controls/Max Motion",
                      "icon": "motion", "above": 60, "fade": 1200}
    assert ac["channel"] == "/devices/ac/controls/mode"
    assert ac["require"] == [{"channel": "/devices/ac/controls/power", "when": "1"}]
    assert ac["value_channel"] == "/devices/ac/controls/target"
    assert ac["value_digits"] == 1 and ac["value_unit"] == "°"
    assert ac["badges"]["fan_only"] == "fan"
    assert co2 == {"kind": "air", "channel": "/devices/msw/controls/CO2",
                   "icon": "co2", "thresholds": [1000, 1500],
                   "chip_from": 1, "alarm_from": 2, "value_unit": ""}
    assert voc["thresholds"] == [220, 660, 2200]
    assert voc["chip_from"] == 2 and voc["alarm_from"] == 3
    assert leak["kind"] == "leak" and leak["icon"] == "leak"
    assert [s["topic"] for s in leak["sensors"]] == [
        "/devices/mw/controls/Input F1", "/devices/mw/controls/Input F3"]
    assert leak["alarms"] == ["/devices/mw/controls/Leakage Mode"]

    room, = layout["pages"]
    assert room["status"] == [{"kind": "status",
                               "channel": "/devices/door/controls/Contact",
                               "icon": "door-open", "alarm": True}]
    saver, = layout["screensaver"]["pages"]
    assert saver["status"] == [{"kind": "status",
                                "channel": "/devices/saver/controls/Flag", "icon": "home"}]

    # Даже без плиток корневой status оставляет одну пустую страницу: ESP
    # сможет построить экран и запустить MQTT только ради чипов.
    _cfg.panels["status-only"] = {
        "title": "Только статус",
        "status": [{"channel": "flag/State", "icon": "home"}],
    }
    only = client.get("/api/screen/status-only").get_json()
    assert only["pages"] == [{"title": "Только статус", "tiles": []}]
    assert only["status"][0]["channel"] == "/devices/flag/controls/State"

    # Правила не содержат текущих MQTT-значений и не меняют текст раскладки.
    again = client.get("/api/screen/panel-status")
    assert again.data == response.data
    print("OK: status root/page/screensaver, require/badges, air и leak, стабильный JSON")


if __name__ == "__main__":
    main()
