# -*- coding: utf-8 -*-
"""Контракт графика между wb-svg-panel и esp32-panel, без MQTT и SQLite."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402


def main():
    web, _palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "espchart.yaml"), freeze_time=True, history=True)
    client = web.app.test_client()

    layout_response = client.get("/api/screen/espchart")
    assert layout_response.status_code == 200
    layout = layout_response.get_json()
    tile = layout["pages"][0]["tiles"][0]
    assert layout["cols"] == 5
    assert tile["type"] == "chart"
    assert tile["w"] == 3 and tile["h"] == 2

    fraction = client.get("/api/screen/espfraction").get_json()
    fraction_tile = fraction["pages"][0]["tiles"][0]
    assert fraction["cols"] == 4
    assert fraction_tile["w"] == 1.5 and fraction_tile["h"] == 1.5

    default = client.get("/api/screen/espdefault").get_json()
    default_tile = default["pages"][0]["tiles"][0]
    assert default["cols"] == 3
    assert default_tile["w"] == 1 and default_tile["h"] == 1

    clamped = client.get("/api/screen/espclamp").get_json()
    clamped_tile = clamped["pages"][0]["tiles"][0]
    assert clamped["cols"] == 2
    assert clamped_tile["w"] == 2 and clamped_tile["h"] == 0.5
    assert tile["range"] == 12 * 3600 and tile["points"] == 90
    assert tile["history"] == "/api/screen/espchart/chart/0"
    assert len(tile["series"]) == 3
    assert tile["series"][0]["value"] == "/devices/msw/controls/Temperature"
    assert layout_response.get_data() == client.get("/api/screen/espchart").get_data(), \
        "история не должна менять раскладку"

    response = client.get(tile["history"])
    assert response.status_code == 200
    chart = response.get_json()
    assert chart["range"] == tile["range"]
    assert len(chart["series"]) == 3
    for source, result in zip(tile["series"], chart["series"]):
        assert result["color"] == source["color"]
        assert result["unit"] == source["unit"]
        assert len(result["values"]) == 90
        assert all(len(point) == 2 for point in result["values"])
        assert all(isinstance(value, (int, float)) for point in result["values"] for value in point)
    assert client.get("/api/screen/espchart/chart/1").status_code == 404
    print("OK: cols, обычные и дробные w/h, три кривые, стабильный JSON")


if __name__ == "__main__":
    main()
