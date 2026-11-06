# -*- coding: utf-8 -*-
"""Контракт секции-заставки между wb-svg-panel и esp32-panel."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402


def main():
    web, _palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "espsaver.yaml"), freeze_time=True, history=True)
    client = web.app.test_client()

    response = client.get("/api/screen/panel-test")
    assert response.status_code == 200
    layout = response.get_json()
    saver = layout["screensaver"]
    assert saver["after"] == 300 and saver["every"] == 300
    assert "panel" not in saver, "отдельная панель заставки больше не нужна"
    assert saver["cols"] == layout["cols"] == 4
    assert [page["title"] for page in layout["pages"]] == ["Основная", "Воздух"], \
        "секция заставки не должна становиться вкладкой"
    assert [page["title"] for page in saver["pages"]] == ["Заставка"]
    temperature, chart = saver["pages"][0]["tiles"]
    assert temperature["type"] == "sensor" and temperature["w"] == 1
    assert chart["type"] == "chart" and chart["w"] == 3 and chart["h"] == 1.5
    assert chart["history"] == "/api/screen/wall/screensaver/chart/0"

    # Основная раскладка и заставка имеют независимую нумерацию графиков.
    main_chart = layout["pages"][0]["tiles"][0]
    assert main_chart["history"] == "/api/screen/wall/chart/0"
    main_history = client.get(main_chart["history"]).get_json()
    saver_history = client.get(chart["history"]).get_json()
    assert main_history["title"] == "Основной график"
    assert saver_history["title"] == "График заставки"
    assert saver_history["range"] == 6 * 3600 and saver_history["points"] == 30
    assert len(saver_history["series"][0]["values"]) == 30
    assert client.get("/api/screen/panel-test/screensaver/chart/1").status_code == 404
    assert response.get_data() == client.get("/api/screen/panel-test").get_data(), \
        "новые точки истории не должны менять раскладку"

    other_layout = client.get("/api/screen/other").get_json()
    other = other_layout["screensaver"]
    assert other["after"] == 30 and other["cols"] == 2
    assert other["pages"][0]["title"] == "Заставка другой панели"
    assert other["pages"][0]["tiles"][0]["title"] == "Влажность"
    assert [page["title"] for page in other_layout["pages"]] == ["Основная"]

    for name in ("disabled", "invalid", "empty", "legacy"):
        disabled = client.get("/api/screen/" + name).get_json()
        assert "screensaver" not in disabled, name
        assert [page["title"] for page in disabled["pages"]] == ["Основная"], name

    multiple = client.get("/api/screen/multiple").get_json()
    assert multiple["pages"] == [], "все отмеченные секции скрыты из вкладок"
    assert multiple["screensaver"]["after"] == 10
    assert multiple["screensaver"]["every"] == 10
    assert [page["title"] for page in multiple["screensaver"]["pages"]] == [
        "Первая заставка", "Вторая заставка"]

    print("OK: секции screensaver скрыты из вкладок и чередуются по таймеру")


if __name__ == "__main__":
    main()
