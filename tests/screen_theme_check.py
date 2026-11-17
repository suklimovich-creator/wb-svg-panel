# -*- coding: utf-8 -*-
"""
Палитра настенного экрана (1.78): /api/screen/<имя>/theme отдаёт режим,
ревизию и цвета по ролям; 304 по ревизии; светлая и тёмная темы читаемы;
графит совпадает с цветами прошивки без сервера; автотема слушает канал WB.

    python3 tests/screen_theme_check.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402

ROOT = os.path.dirname(HERE)
HEX = re.compile(r"^#[0-9A-F]{6}$")


def firmware_defaults():
    """Роли и цвета по умолчанию из main/theme.h соседнего esp32-panel."""
    path = os.path.join(ROOT, "..", "esp32-panel", "main", "theme.h")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read()
    return dict((name, "#%06X" % int(value, 16)) for name, value in
                re.findall(r'X\(\s*\w+\s*,\s*"([\w.]+)"\s*,\s*(0x[0-9A-Fa-f]+)\s*\)', text))


def lum(color):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main():
    web, palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "esptheme.yaml"), freeze_time=True)
    client = web.app.test_client()

    # Все роли, цвета #RRGGBB, текст читается на плитке у каждой темы.
    for name in palette.names():
        body = palette.screen_palette(name)
        assert body["version"] == 1 and body["mode"] in ("light", "dark"), name
        assert list(body["palette"]) == list(palette.SCREEN_ROLES), name
        for role, color in body["palette"].items():
            assert HEX.match(color), (name, role, color)
        pal = body["palette"]
        assert contrast(pal["text.primary"], pal["tile.background"]) >= 4.5, name
        assert contrast(pal["text.secondary"], pal["tile.background"]) >= 2.0, name
        assert contrast(pal["text.on_accent"], pal["state.active"]) >= 3.0, name
        assert palette.screen_problems(name) == [], (name, palette.screen_problems(name))

    # Прошивка без сервера = графит: роли те же, цвета те же.
    fw = firmware_defaults()
    if fw is not None:
        assert set(fw) == set(palette.SCREEN_ROLES), set(fw) ^ set(palette.SCREEN_ROLES)
        graphite = palette.screen_palette("graphite")["palette"]
        diff = dict((k, (fw[k], graphite[k])) for k in fw if fw[k] != graphite[k])
        assert not diff, diff

    # Тема экрана: своя в screens:, иначе панели, иначе общая.
    light = client.get("/api/screen/wall-light/theme")
    assert light.status_code == 200 and light.headers["Cache-Control"] == "no-store"
    data = light.get_json()
    assert data["theme"] == "light" and data["mode"] == "light"
    assert light.headers["ETag"] == '"%s"' % data["revision"]
    dark = client.get("/api/screen/wall-dark/theme").get_json()
    assert dark["theme"] == "graphite" and dark["mode"] == "dark"
    assert dark["revision"] != data["revision"]
    assert client.get("/api/screen/esppanellight/theme").get_json()["mode"] == "light"
    assert client.get("/api/screen/missing/theme").status_code == 404

    # Ревизия совпала - 304 без тела; другая - полный ответ.
    same = client.get("/api/screen/wall-light/theme",
                      headers={"If-None-Match": light.headers["ETag"]})
    assert same.status_code == 304 and same.get_data() == b""
    other = client.get("/api/screen/wall-light/theme", headers={"If-None-Match": '"0"'})
    assert other.status_code == 200

    # Раскладка от темы не зависит: смена темы не перестраивает экран.
    assert "palette" not in client.get("/api/screen/wall-light").get_data(as_text=True)
    assert client.get("/api/screen/wall-light").get_data() == \
        client.get("/api/screen/wall-dark").get_data()

    # Автотема: канал WB сильнее расписания (часы стоят на 12:00 - день).
    from wbpanel.state import state
    state.values.pop("panel/night", None)
    assert client.get("/api/screen/wall-auto/theme").get_json()["theme"] == "light"
    for raw, want in (("1", "graphite"), ("night", "graphite"), ("0", "light"),
                      ("dark", "graphite"), ("neumo", "neumo"), ("??", "light")):
        state.values["panel/night"] = raw
        got = client.get("/api/screen/wall-auto/theme").get_json()["theme"]
        assert got == want, (raw, got, want)
    state.values.pop("panel/night", None)
    assert "panel/night" in web.config.used_channels()

    # Ряды графика: свой color - без роли, остальные - роль темы.
    series = client.get("/api/screen/wall-dark").get_json()["pages"][0]["tiles"][0]["series"]
    assert "role" not in series[0] and series[0]["color"] == "#E08A2D"
    assert series[1]["role"] == "chart.co2" and series[1]["color"]
    assert series[2]["role"] in ("chart.humidity", "chart.series3")
    json.dumps(series)
    print("OK: роли и контраст у %d тем, 304 по ревизии, тема экрана/панели/общая, "
          "канал автотемы, роли рядов графика" % len(palette.names()))


if __name__ == "__main__":
    main()
