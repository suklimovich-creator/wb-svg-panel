# -*- coding: utf-8 -*-
"""Плитка clock: композиция, режимы, SVG и контракт с esp32-panel."""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402

SIZES = [(2, 0.5), (1, 1), (2, 1), (2, 2), (1.5, 1.5), (1, 1.5)]
MODES = ["clock_weather", "clock", "weather"]
LAYOUTS = ["low", "square", "wide", "square", "square", "tall"]


def role(root, name):
    return [el for el in root.iter() if el.attrib.get("data-clock-" + name) == "true"]


def groups(root):
    return [el for el in root.iter() if "data-clock-layout" in el.attrib]


def blocks(root):
    return [el.attrib["data-clock-block"] for el in root.iter() if "data-clock-block" in el.attrib]


def font_size(el):
    for item in el.attrib.get("style", "").split(";"):
        if item.startswith("font-size:"):
            return float(item[len("font-size:"):-2])
    raise AssertionError("у элемента clock нет явного font-size")


def assert_fit(el, width, text_width):
    if el.attrib.get("textLength"):
        assert abs(float(el.attrib["textLength"]) - width) < 0.11
        assert el.attrib.get("lengthAdjust") == "spacingAndGlyphs"
    else:
        assert text_width(el.text or "", font_size(el)) <= width + 0.11, \
            (el.text, font_size(el), width)


def block_of(el, parents):
    while el is not None:
        if "data-clock-block" in el.attrib:
            return el.attrib["data-clock-block"]
        el = parents.get(el)
    raise AssertionError("элемент вне базового блока")


def render(web, panel, theme="light"):
    with web.app.test_request_context("/panel/%s.svg?theme=%s" % (panel, theme)):
        return web.render_panel(panel)


def main():
    web, palette, _cfg, _sent = env.setup(
        config=os.path.join(HERE, "fixtures", "clock.yaml"), freeze_time=True)
    from wbpanel.state import state
    values = {
        "temperature": "21.5", "description": "Переменная облачность",
        "humidity": "53", "pressure": "748", "windspeed": "12",
        "forecast_ok": "1", "code_series": "2,2,3",
        "temp_series": "21.5,21.0,20.0", "precip_prob_series": "0,0,10",
        "precip_series": "0,0,0", "cloud_series": "40,50,60",
        "forecast_start": "2026-10-17T12:00:00+03:00",
        "temp_min_12h": "18", "temp_max_12h": "23",
    }
    for control, value in values.items():
        state.on_message("/devices/weather/controls/%s" % control, value)

    client = web.app.test_client()
    response = client.get("/api/screen/panel-clock")
    assert response.status_code == 200
    tiles = [tile for page in response.get_json()["pages"] for tile in page["tiles"]]
    assert len(tiles) == 18
    assert [t["type"] for t in tiles] == ["clock"] * 18
    assert [(t["w"], t["h"]) for t in tiles] == [s for s in SIZES for _ in MODES]
    assert [t["view"] for t in tiles] == MODES * len(SIZES)
    for tile in tiles:
        assert tile["temp"] == "/devices/weather/controls/temperature"
        assert tile["hum"] == "/devices/weather/controls/humidity"
        assert tile["press"] == "/devices/weather/controls/pressure"
        assert tile["wind"] == "/devices/weather/controls/windspeed"
        assert tile["series"] == {
            "temp": "/devices/weather/controls/temp_series",
            "code": "/devices/weather/controls/code_series",
            "prob": "/devices/weather/controls/precip_prob_series",
            "cloud": "/devices/weather/controls/cloud_series",
            "start": "/devices/weather/controls/forecast_start",
        }
    assert response.get_data() == client.get("/api/screen/panel-clock").get_data(), \
        "время не должно менять JSON раскладки"

    svg = render(web, "clock")
    root = ET.fromstring(svg.encode("utf-8"))
    clock_groups = groups(root)
    assert [g.attrib["data-clock-layout"] for g in clock_groups] == LAYOUTS
    assert [g.attrib["data-clock-mode"] for g in clock_groups] == ["clock_weather"] * 6
    expected_blocks = [
        ["clock-low", "weather-low"],
        ["clock-half", "weather-half"],
        ["clock-square", "weather-square"],
        ["clock-half", "weather-half"],
        ["clock-half", "weather-half"],
        ["clock-square", "weather-half"],
    ]
    assert [blocks(g) for g in clock_groups] == expected_blocks
    assert "transform" not in clock_groups[1].attrib, "1×1 — эталон без масштабирования"
    for index in (3, 4):
        assert "scale(" in clock_groups[index].attrib.get("transform", ""), \
            "2×2 и 1.5×1.5 масштабируют сборку 1×1"
    assert all("fill:" not in el.attrib.get("style", "")
               and "stroke:" not in el.attrib.get("style", "")
               and "color" not in el.attrib and "stroke" not in el.attrib
               for group in clock_groups for el in group.iter())
    assert all(not any("name" in el.attrib.get("class", "").split()
                       for el in group.iter()) for group in clock_groups), \
        "видимого названия «Часы» не должно быть"

    from wbpanel.geometry import text_width
    times, dates = role(root, "time"), role(root, "date")
    assert len(times) == len(dates) == 6
    assert all(el.attrib.get("text-anchor") == "middle" for el in times)
    assert all("font-variant-numeric:tabular-nums" in el.attrib["style"] for el in times)
    assert font_size(times[1]) > 48
    assert 38 < font_size(times[0]) <= 42
    low_time, low_temp = role(clock_groups[0], "time")[0], role(clock_groups[0], "temp")[0]
    assert float(low_time.attrib["y"]) == float(low_temp.attrib["y"]) == 38
    square_rules = [el for el in clock_groups[1].iter()
                    if "tick" in el.attrib.get("class", "").split()]
    assert len(square_rules) == 1
    assert float(square_rules[0].attrib["y1"]) == float(square_rules[0].attrib["y2"]) == 85
    metrics = role(root, "metric")
    assert [el.text for el in metrics] == ["53 %", "748 мм", "12 км/ч"]
    assert [el.attrib.get("text-anchor") for el in metrics] == ["end"] * 3
    assert [float(el.attrib["y"]) for el in metrics] == [110, 127, 144]
    assert not any(word in svg for word in ("Влажность:", "Давление:", "Ветер:"))
    assert svg.count("12:00") == 7  # шесть плиток и часы шапки
    assert svg.count("сб, 17 октября") == 7
    assert svg.count("21.5°") == 6
    assert svg.count("Переменная облачность") == 6
    assert svg.count("53 % · 748 мм · 12 км/ч") == 5

    widths = {
        "time": {"clock-low": 136, "clock-half": 154, "clock-square": 154},
        "temp": {"weather-low": 112, "weather-half": 94, "weather-square": 94},
        "desc": {"weather-low": 153, "weather-half": 140, "weather-square": 144},
        "extra": {"weather-low": 153, "weather-half": 140},
        "metric": {"weather-square": 150},
    }

    def check_fits(current_root):
        parents = {child: parent for parent in current_root.iter() for child in parent}
        for name, by_block in widths.items():
            for el in role(current_root, name):
                assert_fit(el, by_block[block_of(el, parents)], text_width)

    check_fits(root)
    baseline_temp_sizes = [font_size(el) for el in role(root, "temp")]
    baseline_desc_sizes = [font_size(el) for el in role(root, "desc")]
    baseline_extra_sizes = [font_size(el) for el in role(root, "extra")]
    baseline_metric_sizes = [font_size(el) for el in role(root, "metric")]

    # Два одиночных режима используют те же базовые блоки и не рисуют второй.
    for panel, mode in (("clock-single", "clock"), ("weather-single", "weather")):
        single_root = ET.fromstring(render(web, panel).encode("utf-8"))
        single_groups = groups(single_root)
        assert len(single_groups) == 6
        assert [g.attrib["data-clock-layout"] for g in single_groups] == ["single-" + mode] * 6
        assert all(g.attrib["data-clock-mode"] == mode for g in single_groups)
        assert len(role(single_root, "time")) == (6 if mode == "clock" else 0)
        assert len(role(single_root, "temp")) == (6 if mode == "weather" else 0)
        assert all(blocks(g)[0].startswith(mode if mode == "clock" else "weather")
                   for g in single_groups)
        check_fits(single_root)

    for theme in palette.names():
        themed_root = ET.fromstring(render(web, "clock", theme).encode("utf-8"))
        assert len(groups(themed_root)) == 6
        if theme == "dark" and os.environ.get("CLOCK_DARK_SVG_OUT"):
            with open(os.environ["CLOCK_DARK_SVG_OUT"], "w", encoding="utf-8") as fh:
                fh.write(render(web, "clock", theme))

    inv_svg = render(web, "clock-inv", "noir")
    inv_root = ET.fromstring(inv_svg.encode("utf-8"))
    assert len([el for el in inv_root.iter() if "inv" in el.attrib.get("class", "").split()]) == 6
    assert len(groups(inv_root)) == 6
    assert len(role(inv_root, "desc")) == 6
    assert len(role(inv_root, "metric")) == 3
    if os.environ.get("CLOCK_INV_SVG_OUT"):
        with open(os.environ["CLOCK_INV_SVG_OUT"], "w", encoding="utf-8") as fh:
            fh.write(inv_svg)

    for value, shown in (("-12.5", "-12.5°"), ("-12", "-12°")):
        state.on_message("/devices/weather/controls/temperature", value)
        negative_svg = render(web, "clock")
        assert negative_svg.count(shown) == 6
        negative_root = ET.fromstring(negative_svg.encode("utf-8"))
        check_fits(negative_root)
        if value == "-12.5":
            negative_sizes = [font_size(el) for el in role(negative_root, "temp")]
            assert all(size <= base for size, base in zip(negative_sizes, baseline_temp_sizes))
            if os.environ.get("CLOCK_NEGATIVE_SVG_OUT"):
                with open(os.environ["CLOCK_NEGATIVE_SVG_OUT"], "w", encoding="utf-8") as fh:
                    fh.write(negative_svg)
    state.on_message("/devices/weather/controls/temperature", values["temperature"])

    import wbpanel.tiles_devicewide as devicewide
    real_clock_now = devicewide.clock_now
    time_sizes = []
    for shown_time in ("00:00", "11:11", "19:58", "23:59"):
        devicewide.clock_now = lambda value=shown_time: (value, "сб, 17 октября")
        time_root = ET.fromstring(render(web, "clock").encode("utf-8"))
        current_times = role(time_root, "time")
        assert [el.text for el in current_times] == [shown_time] * 6
        current_sizes = [font_size(el) for el in current_times]
        assert all(size == base for size, base in
                   zip(current_sizes, time_sizes[0] if time_sizes else current_sizes))
        time_sizes.append(current_sizes)
    devicewide.clock_now = real_clock_now

    sample_desc = "Пасмурно"
    sample_extra = "89 % · 751 мм · 11 км/ч"
    for control, value in (("description", sample_desc), ("humidity", "89"),
                           ("pressure", "751"), ("windspeed", "11")):
        state.on_message("/devices/weather/controls/%s" % control, value)
    sample_svg = render(web, "clock")
    sample_root = ET.fromstring(sample_svg.encode("utf-8"))
    check_fits(sample_root)
    assert [el.text for el in role(sample_root, "desc")] == [sample_desc] * 6
    assert [el.text for el in role(sample_root, "extra")] == [sample_extra] * 5
    assert [el.text for el in role(sample_root, "metric")] == ["89 %", "751 мм", "11 км/ч"]
    if os.environ.get("CLOCK_SAMPLE_SVG_OUT"):
        with open(os.environ["CLOCK_SAMPLE_SVG_OUT"], "w", encoding="utf-8") as fh:
            fh.write(sample_svg)

    pad_response = client.get("/tile/clock/0.svg?view=pad&w=700&h=480")
    assert pad_response.status_code == 200
    pad_root = ET.fromstring(pad_response.get_data())
    assert not groups(pad_root)
    assert [el.text for el in pad_root.iter()
            if el.attrib.get("data-forecast-desc") == "true"] == [sample_desc]
    assert [el.text for el in pad_root.iter()
            if el.attrib.get("data-forecast-extra") == "true"] == [sample_extra]

    long_text = "Слабая облачность, лёгкий ветер"
    state.on_message("/devices/weather/controls/description", long_text)
    long_root = ET.fromstring(render(web, "clock").encode("utf-8"))
    check_fits(long_root)
    assert [el.text for el in role(long_root, "desc")] == [long_text] * 6
    assert [font_size(el) for el in role(long_root, "extra")] == baseline_extra_sizes
    assert [font_size(el) for el in role(long_root, "metric")] == baseline_metric_sizes
    assert all(a <= b for a, b in zip([font_size(el) for el in role(long_root, "desc")],
                                      baseline_desc_sizes))

    out = os.environ.get("CLOCK_SVG_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(svg)
    print("OK: 6 размеров × 3 режима, композиция, JSON, pad, темы, inv, минус и длинные строки")


if __name__ == "__main__":
    main()
