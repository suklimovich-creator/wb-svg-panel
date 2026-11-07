# -*- coding: utf-8 -*-
"""Контракт плиток 1.77 между wb-svg-panel и esp32-panel 0.10."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402


def main():
    web, _palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "esptiles.yaml"), freeze_time=True)
    client = web.app.test_client()

    response = client.get("/api/screen/panel-test")
    assert response.status_code == 200
    layout = response.get_json()
    assert layout["cols"] == 4
    pages = {page["title"]: page["tiles"] for page in layout["pages"]}
    assert list(pages) == ["Свет", "Сцены", "Дом"], \
        "секция заставки не должна становиться вкладкой"

    # --- диммер и лента: пульт света kind=light -----------------------------
    dimmer, strip = pages["Свет"]
    assert dimmer["type"] == "dimmer"
    assert dimmer["state"] == "/devices/dm/controls/K1"
    assert dimmer["state_cmd"] == "/devices/dm/controls/K1/on"
    assert dimmer["level"] == "/devices/dm/controls/Channel 1"
    assert dimmer["level_cmd"] == "/devices/dm/controls/Channel 1/on"
    pad = dimmer["pad"]
    assert pad["kind"] == "light"
    assert pad["state"] == dimmer["state"] and pad["state_cmd"] == dimmer["state_cmd"]
    assert pad["level"] == dimmer["level"] and pad["level_cmd"] == dimmer["level_cmd"]
    assert pad["max"] == 100
    assert "temp" not in pad, "у диммера без канала теплоты блока temp быть не должно"

    assert strip["type"] == "dimmer" and strip["light"] is True
    pad = strip["pad"]
    assert pad["kind"] == "light"
    assert pad["temp"] == "/devices/led/controls/CCT1 Temperature"
    assert pad["temp_cmd"] == "/devices/led/controls/CCT1 Temperature/on"
    assert pad["temp_min"] == 0 and pad["temp_max"] == 100
    assert pad["unit"] == "percent"
    assert pad["cold"].startswith("#") and pad["warm"].startswith("#")

    # --- сцены: одна плитка-набор -------------------------------------------
    (scenes,) = pages["Сцены"]
    assert scenes["type"] == "scenes"
    assert scenes["w"] == 1.5 and scenes["h"] == 1.5
    items = scenes["items"]
    assert [i["title"] for i in items] == ["Выкл. всё", "Спать", "Кино", "Ярко"]
    assert items[0]["cmd"] == "/devices/lightingScenes/controls/off_all/on"
    # Каналы в состоянии "false": команда по форме канала - "true".
    assert items[0]["payload"] == "true"
    assert items[1]["payload"] == "true"
    # Явная command_on побеждает форму канала.
    assert items[3]["payload"] == "true"
    # level: из конфига либо равномерно по порядку от тусклых к ярким.
    assert items[0]["level"] == 0.0
    assert items[2]["level"] == 0.4
    assert items[3]["level"] == 1.0
    assert items[1]["icon"] == "moon"

    # --- телевизор -----------------------------------------------------------
    tv, leak, weather, today = pages["Дом"]
    assert tv["type"] == "tv"
    assert tv["state"] == "/devices/tv_living/controls/power"
    assert tv["link"] == "/devices/tv_living/controls/online"
    assert tv["app"] == "/devices/tv_living/controls/current_app"
    assert tv["mode"] == "/devices/tv_living/controls/state"
    assert tv["states"] == {"on": "Включён", "standby": "Ожидание",
                            "offline": "Выключен"}
    pad = tv["pad"]
    assert pad["kind"] == "tv"
    assert pad["base"] == "/devices/tv_living/controls/"
    apps = pad["apps"]
    assert [a["title"] for a in apps] == ["Кинопоиск", "YouTube"]
    assert apps[0]["cmd"] == "/devices/tv_living/controls/app_kinopoisk/on"

    # --- протечки -------------------------------------------------------------
    assert leak["type"] == "leak"
    assert leak["icon"] == "leak"
    sensors = leak["sensors"]
    assert [s["title"] for s in sensors] == ["Ванная", "Кухня"]
    assert sensors[0]["topic"] == "/devices/mw/controls/Input F1"
    assert sensors[1]["topic"] == "/devices/mw/controls/Input F3"
    assert leak["pad"] == {"kind": "leak",
                           "reset": ["/devices/mw/controls/Leakage Mode Reset/on"]}

    # --- погода ----------------------------------------------------------------
    assert weather["type"] == "weather"
    assert weather["temp"] == "/devices/weather/controls/temperature"
    assert weather["tmin"] == "/devices/weather/controls/temp_min_12h"
    assert weather["tmax"] == "/devices/weather/controls/temp_max_12h"
    series = weather["series"]
    assert series["temp"] == "/devices/weather/controls/temp_series"
    assert series["code"] == "/devices/weather/controls/code_series"
    assert series["prob"] == "/devices/weather/controls/precip_prob_series"
    assert series["cloud"] == "/devices/weather/controls/cloud_series"
    assert series["start"] == "/devices/weather/controls/forecast_start"

    # --- «Сегодня»: в раскладке адрес данных, сами данные отдельно -------------
    assert today["type"] == "today"
    assert today["title"] == "Сегодня"
    assert today["list"] == "shopping"
    assert today["calendars"] == ["family"]
    assert today["data"] == "/api/screen/wall/today/0"

    feed = client.get(today["data"])
    assert feed.status_code == 200
    feed = feed.get_json()
    assert feed["version"] == 1
    assert feed["title"] == "Сегодня"
    # Замороженное «сейчас»: сб, 17.10.2026, 12:00 МСК.
    assert feed["date"] == "сб 17 окт"
    assert feed["list_title"] == "Покупки"
    assert feed["tasks"] == ["Молоко 2 л", "Хлеб бородинский", "Сыр",
                             "Кофе в зёрнах", "Лампочка E14 тёплая", "Батарейки AA"]
    days = feed["days"]
    assert [d["label"] for d in days] == ["сегодня, сб 17 окт", "завтра, вс 18 окт"]
    states = [(e["text"], e["state"], e["time"]) for e in days[0]["events"]]
    assert states == [("Зарядка", "past", "09:00"),
                      ("Работа над панелью", "now", "12:00"),
                      ("Тренировка", "next", "14:00"),
                      ("Ужин с родителями", "next", "16:00")]
    assert days[0]["events"][1]["end"] == "14:00"
    assert days[0]["events"][1]["color"] == "#3B8FD4"
    assert [e["text"] for e in days[1]["events"]] == ["Бассейн", "Врач, плановый осмотр"]
    assert all(e["state"] == "next" for e in days[1]["events"])
    assert client.get("/api/screen/panel-test/today/1").status_code == 404

    # --- заставка: своя плитка today со своей нумерацией -----------------------
    saver = layout["screensaver"]
    assert saver["after"] == 300 and saver["every"] == 300
    (agenda,) = saver["pages"][0]["tiles"]
    assert agenda["type"] == "today" and agenda["title"] == "Расписание"
    assert "list" not in agenda
    assert agenda["data"] == "/api/screen/wall/screensaver/today/0"
    saver_feed = client.get(agenda["data"])
    assert saver_feed.status_code == 200
    saver_feed = saver_feed.get_json()
    assert saver_feed["title"] == "Расписание"
    assert "tasks" not in saver_feed
    assert [d["label"] for d in saver_feed["days"]] == ["сегодня, сб 17 окт",
                                                        "завтра, вс 18 окт"]
    assert client.get("/api/screen/panel-test/screensaver/today/1").status_code == 404

    # --- раскладка стабильна: повторный запрос совпадает байт в байт ------------
    again = client.get("/api/screen/panel-test")
    assert again.data == response.data

    print("OK: плитки 1.77 - пульт света, набор сцен, tv, leak, weather, today")


if __name__ == "__main__":
    main()
