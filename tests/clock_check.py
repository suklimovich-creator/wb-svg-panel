# -*- coding: utf-8 -*-
"""Плитка clock: SVG и контракт с esp32-panel, без MQTT и сети."""
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
import env  # noqa: E402


def clock_role(root, role):
    return [el for el in root.iter()
            if el.attrib.get("data-clock-" + role) == "true"]


def forecast_role(root, role):
    return [el for el in root.iter()
            if el.attrib.get("data-forecast-" + role) == "true"]


def font_size(el):
    for item in el.attrib.get("style", "").split(";"):
        if item.startswith("font-size:"):
            return float(item[len("font-size:"):-2])
    raise AssertionError("у элемента clock нет явного font-size")


def assert_fit(el, width, text_width):
    """Строка либо помещается сама, либо получает точный textLength."""
    if el.attrib.get("textLength"):
        assert abs(float(el.attrib["textLength"]) - width) < 0.11
        assert el.attrib.get("lengthAdjust") == "spacingAndGlyphs"
    else:
        assert text_width(el.text or "", font_size(el)) <= width + 0.11, \
            (el.text, font_size(el), width)


def main():
    web, _palette, _cfg, _sent = env.setup(
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
    tiles = response.get_json()["pages"][0]["tiles"]
    assert len(tiles) == 3
    assert [t["type"] for t in tiles] == ["clock"] * 3
    assert [t["title"] for t in tiles] == ["Часы"] * 3
    assert [(t["w"], t["h"]) for t in tiles] == [(2, 1), (1, 1), (2, 0.5)]
    for tile in tiles:
        assert tile["temp"] == "/devices/weather/controls/temperature"
        assert tile["hum"] == "/devices/weather/controls/humidity"
        assert tile["press"] == "/devices/weather/controls/pressure"
        assert tile["wind"] == "/devices/weather/controls/windspeed"
        assert tile["tmin"] == "/devices/weather/controls/temp_min_12h"
        assert tile["tmax"] == "/devices/weather/controls/temp_max_12h"
        assert tile["series"] == {
            "temp": "/devices/weather/controls/temp_series",
            "code": "/devices/weather/controls/code_series",
            "prob": "/devices/weather/controls/precip_prob_series",
            "cloud": "/devices/weather/controls/cloud_series",
            "start": "/devices/weather/controls/forecast_start",
        }
    assert response.get_data() == client.get("/api/screen/panel-clock").get_data(), \
        "время не должно менять JSON раскладки"

    with web.app.test_request_context("/panel/clock.svg?theme=light"):
        svg = web.render_panel("clock")
    root = ET.fromstring(svg.encode("utf-8"))
    clock_groups = [el for el in root.iter() if "data-clock-layout" in el.attrib]
    assert [el.attrib["data-clock-layout"] for el in clock_groups] == [
        "wide", "square", "low"]
    assert all("transform" not in el.attrib for el in clock_groups[1:]), \
        "компактные плитки должны использовать всю площадь без уменьшения 2x1"
    assert all("fill:" not in el.attrib.get("style", "")
               and "stroke:" not in el.attrib.get("style", "")
               and "color" not in el.attrib and "stroke" not in el.attrib
               for group in clock_groups for el in group.iter()), \
        "цвета clock и значков должны наследоваться от темы, а не задаваться inline"
    assert all(not any("name" in el.attrib.get("class", "").split()
                       for el in group.iter()) for group in clock_groups), \
        "видимого названия «Часы» не должно быть ни в одной SVG-раскладке"

    from wbpanel.geometry import text_width
    times = clock_role(root, "time")
    dates = clock_role(root, "date")
    assert len(dates) == 3
    assert [el.attrib.get("text-anchor") for el in times] == ["middle"] * 3
    assert [float(el.attrib["x"]) for el in times] == [88.5, 85.0, 88.5]
    assert all("font-variant-numeric:tabular-nums" in el.attrib["style"]
               for el in times)
    assert (float(times[0].attrib["y"]), float(dates[0].attrib["y"])) == (93, 120), \
        "время и дата широкой плитки центрируются по вертикали как единый блок"
    assert 48 < font_size(times[1]) <= 56, "время 1×1 должно стать крупнее"
    assert 38 < font_size(times[2]) <= 42, "время 2×0.5 должно немного увеличиться"

    square_rules = [el for el in clock_groups[1].iter()
                    if "tick" in el.attrib.get("class", "").split()]
    assert len(square_rules) == 1
    assert float(square_rules[0].attrib["y1"]) == float(square_rules[0].attrib["y2"]) == 85
    assert float(times[1].attrib["y"]) < 85 < float(clock_role(clock_groups[1], "temp")[0].attrib["y"])

    low_rules = [el for el in clock_groups[2].iter()
                 if "tick" in el.attrib.get("class", "").split()]
    assert len(low_rules) == 1
    assert float(low_rules[0].attrib["x1"]) == float(low_rules[0].attrib["x2"]) == 177
    low_time = clock_role(clock_groups[2], "time")[0]
    low_temp = clock_role(clock_groups[2], "temp")[0]
    assert float(low_time.attrib["y"]) == float(low_temp.attrib["y"]) == 38

    fit_widths = {
        "time": {"wide": 154, "square": 154, "low": 136},
        "temp": {"wide": 94, "square": 94, "low": 112},
        "desc": {"wide": 144, "square": 140, "low": 153},
        "extra": {"square": 140, "low": 153},
        "metric": {"wide": 150},
    }
    for role, widths in fit_widths.items():
        for el in clock_role(root, role):
            layout = next(group.attrib["data-clock-layout"] for group in clock_groups
                          if el in list(group.iter()))
            assert_fit(el, widths[layout], text_width)
    baseline_temp_sizes = [font_size(el) for el in clock_role(root, "temp")]
    baseline_desc_sizes = [font_size(el) for el in clock_role(root, "desc")]
    baseline_extra_sizes = [font_size(el) for el in clock_role(root, "extra")]
    baseline_metric_sizes = [font_size(el) for el in clock_role(root, "metric")]
    assert len(baseline_desc_sizes) == 3
    assert len(baseline_extra_sizes) == 2
    assert len(baseline_metric_sizes) == 3
    out = os.environ.get("CLOCK_SVG_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(svg)
    assert svg.count("12:00") == 4  # три плитки и часы шапки
    assert svg.count("сб, 17 октября") == 4
    assert svg.count("21.5°") == 3
    assert svg.count("Переменная облачность") == 3
    assert svg.count("53 % · 748 мм · 12 км/ч") == 2
    baseline_metrics = clock_role(root, "metric")
    assert [el.text for el in baseline_metrics] == ["53 %", "748 мм", "12 км/ч"]
    assert [float(el.attrib["x"]) for el in baseline_metrics] == [334, 334, 334]
    assert [el.attrib.get("text-anchor") for el in baseline_metrics] == ["end"] * 3
    assert float(clock_role(root, "temp")[0].attrib["x"]) == 334
    assert [float(el.attrib["y"]) for el in baseline_metrics] == [110, 127, 144]
    assert not any(word in svg for word in ("Влажность:", "Давление:", "Ветер:"))
    assert "Низкая" not in svg

    # Все встроенные темы должны рендериться без отдельных inline-цветов.
    for theme in _palette.names():
        with web.app.test_request_context("/panel/clock.svg?theme=" + theme):
            themed = web.render_panel("clock")
        themed_root = ET.fromstring(themed.encode("utf-8"))
        assert sum("data-clock-layout" in el.attrib for el in themed_root.iter()) == 3
        if theme == "dark" and os.environ.get("CLOCK_DARK_SVG_OUT"):
            with open(os.environ["CLOCK_DARK_SVG_OUT"], "w", encoding="utf-8") as fh:
                fh.write(themed)

    # color: dark включает .inv: вторичный текст и разделители тоже должны
    # получить инверсные цвета, а не остаться светло-серыми на тёмной карточке.
    with web.app.test_request_context("/panel/clock-inv.svg?theme=noir"):
        inv_svg = web.render_panel("clock-inv")
    inv_root = ET.fromstring(inv_svg.encode("utf-8"))
    inv_groups = [el for el in inv_root.iter()
                  if "inv" in el.attrib.get("class", "").split()]
    assert len(inv_groups) == 3
    inv_classes = [el.attrib.get("class", "").split()
                   for group in inv_groups for el in group.iter()]
    assert sum("wnow" in classes for classes in inv_classes) == 6
    assert sum("status" in classes for classes in inv_classes) == 11
    assert sum("tick" in classes for classes in inv_classes) == 3
    assert len(clock_role(inv_root, "desc")) == 3
    assert len(clock_role(inv_root, "extra")) == 2
    assert len(clock_role(inv_root, "metric")) == 3
    if os.environ.get("CLOCK_INV_SVG_OUT"):
        with open(os.environ["CLOCK_INV_SVG_OUT"], "w", encoding="utf-8") as fh:
            fh.write(inv_svg)

    # Минус и дробная часть не должны прижимать температуру к значку.
    for value, shown in (("-12.5", "-12.5°"), ("-12", "-12°")):
        state.on_message("/devices/weather/controls/temperature", value)
        with web.app.test_request_context("/panel/clock.svg?theme=light"):
            negative_svg = web.render_panel("clock")
        assert negative_svg.count(shown) == 3
        negative_root = ET.fromstring(negative_svg.encode("utf-8"))
        negative_groups = [el for el in negative_root.iter()
                           if "data-clock-layout" in el.attrib]
        for el in clock_role(negative_root, "temp"):
            layout = next(group.attrib["data-clock-layout"] for group in negative_groups
                          if el in list(group.iter()))
            assert_fit(el, fit_widths["temp"][layout], text_width)
        if value == "-12.5":
            negative_sizes = [font_size(el) for el in clock_role(negative_root, "temp")]
            assert negative_sizes[0] < baseline_temp_sizes[0]
            assert all(size <= base for size, base in
                       zip(negative_sizes, baseline_temp_sizes))
            if os.environ.get("CLOCK_NEGATIVE_SVG_OUT"):
                with open(os.environ["CLOCK_NEGATIVE_SVG_OUT"], "w", encoding="utf-8") as fh:
                    fh.write(negative_svg)
    state.on_message("/devices/weather/controls/temperature", values["temperature"])

    # Разные цифры имеют одинаковую геометрию: tabular-nums и центральная
    # привязка не дают времени «гулять» внутри своей половины.
    import wbpanel.tiles_devicewide as devicewide
    real_clock_now = devicewide.clock_now
    time_sizes = []
    for shown_time in ("00:00", "11:11", "19:58", "23:59"):
        devicewide.clock_now = lambda value=shown_time: (value, "сб, 17 октября")
        with web.app.test_request_context("/panel/clock.svg?theme=light"):
            time_svg = web.render_panel("clock")
        time_root = ET.fromstring(time_svg.encode("utf-8"))
        current_times = clock_role(time_root, "time")
        assert [el.text for el in current_times] == [shown_time] * 3
        assert [el.attrib.get("text-anchor") for el in current_times] == ["middle"] * 3
        current_sizes = [font_size(el) for el in current_times]
        assert all(size == base for size, base in zip(current_sizes, time_sizes[0] if time_sizes else current_sizes))
        time_sizes.append(current_sizes)
        if shown_time == "11:11" and os.environ.get("CLOCK_TIME_SVG_OUT"):
            with open(os.environ["CLOCK_TIME_SVG_OUT"], "w", encoding="utf-8") as fh:
                fh.write(time_svg)
    assert all(sizes == time_sizes[0] for sizes in time_sizes)
    devicewide.clock_now = real_clock_now

    def check_clock_text_fits(svg):
        current_root = ET.fromstring(svg.encode("utf-8"))
        groups = [el for el in current_root.iter() if "data-clock-layout" in el.attrib]
        for role in ("desc", "extra", "metric"):
            for el in clock_role(current_root, role):
                layout = next(group.attrib["data-clock-layout"] for group in groups
                              if el in list(group.iter()))
                assert_fit(el, fit_widths[role][layout], text_width)
        return current_root

    def check_pad_text_fits(pad_root):
        from wbpanel.const import font_scale
        pad_fs = font_scale(480)
        assert_fit(forecast_role(pad_root, "desc")[0], 616, text_width)
        extra_width = max(700 - 56 - text_width("Часы", 15 * pad_fs), 60)
        assert_fit(forecast_role(pad_root, "extra")[0], extra_width, text_width)

    # Реальный пример: description и extra - два независимых текста, а не
    # одна строка «Пасмурно · 89 % ...». Оба выводятся во всех размерах и pad.
    sample_desc = "Пасмурно"
    sample_extra = "89 % · 751 мм · 11 км/ч"
    for control, value in (("description", sample_desc), ("humidity", "89"),
                           ("pressure", "751"), ("windspeed", "11")):
        state.on_message("/devices/weather/controls/%s" % control, value)
    with web.app.test_request_context("/panel/clock.svg?theme=light"):
        sample_svg = web.render_panel("clock")
    sample_root = check_clock_text_fits(sample_svg)
    assert [el.text for el in clock_role(sample_root, "desc")] == [sample_desc] * 3
    assert [el.text for el in clock_role(sample_root, "extra")] == [sample_extra] * 2
    sample_metrics = ["89 %", "751 мм", "11 км/ч"]
    assert [el.text for el in clock_role(sample_root, "metric")] == sample_metrics
    sample_groups = [el for el in sample_root.iter() if "data-clock-layout" in el.attrib]
    for group in sample_groups:
        desc = clock_role(group, "desc")[0]
        following = clock_role(group, "extra") + clock_role(group, "metric")
        assert following
        assert all(float(desc.attrib["y"]) < float(el.attrib["y"]) for el in following)
    sample_desc_sizes = [font_size(el) for el in clock_role(sample_root, "desc")]
    sample_extra_sizes = [font_size(el) for el in clock_role(sample_root, "extra")]
    sample_metric_sizes = [font_size(el) for el in clock_role(sample_root, "metric")]
    if os.environ.get("CLOCK_SAMPLE_SVG_OUT"):
        with open(os.environ["CLOCK_SAMPLE_SVG_OUT"], "w", encoding="utf-8") as fh:
            fh.write(sample_svg)

    sample_pad_response = client.get("/tile/clock/0.svg?view=pad&w=700&h=480")
    assert sample_pad_response.status_code == 200
    sample_pad_root = ET.fromstring(sample_pad_response.get_data())
    assert [el.text for el in forecast_role(sample_pad_root, "desc")] == [sample_desc]
    assert [el.text for el in forecast_role(sample_pad_root, "extra")] == [sample_extra]
    check_pad_text_fits(sample_pad_root)
    if os.environ.get("CLOCK_PAD_SVG_OUT"):
        with open(os.environ["CLOCK_PAD_SVG_OUT"], "wb") as fh:
            fh.write(sample_pad_response.get_data())

    # Длинный description уменьшает только свою строку. Показатели остаются
    # на своём месте и с тем же кеглем, включая полный прогноз.
    long_text = "Слабая облачность, лёгкий ветер"
    state.on_message("/devices/weather/controls/description", long_text)
    with web.app.test_request_context("/panel/clock.svg?theme=light"):
        long_svg = web.render_panel("clock")
    long_root = check_clock_text_fits(long_svg)
    assert [el.text for el in clock_role(long_root, "desc")] == [long_text] * 3
    assert [el.text for el in clock_role(long_root, "extra")] == [sample_extra] * 2
    assert [el.text for el in clock_role(long_root, "metric")] == sample_metrics
    long_sizes = [font_size(el) for el in clock_role(long_root, "desc")]
    assert all(size < base for size, base in zip(long_sizes, sample_desc_sizes))
    assert [font_size(el) for el in clock_role(long_root, "extra")] == sample_extra_sizes
    assert [font_size(el) for el in clock_role(long_root, "metric")] == sample_metric_sizes
    if os.environ.get("CLOCK_LONG_SVG_OUT"):
        with open(os.environ["CLOCK_LONG_SVG_OUT"], "w", encoding="utf-8") as fh:
            fh.write(long_svg)

    long_pad_response = client.get("/tile/clock/0.svg?view=pad&w=700&h=480")
    long_pad_root = ET.fromstring(long_pad_response.get_data())
    assert [el.text for el in forecast_role(long_pad_root, "desc")] == [long_text]
    assert [el.text for el in forecast_role(long_pad_root, "extra")] == [sample_extra]
    check_pad_text_fits(long_pad_root)
    if os.environ.get("CLOCK_LONG_PAD_SVG_OUT"):
        with open(os.environ["CLOCK_LONG_PAD_SVG_OUT"], "wb") as fh:
            fh.write(long_pad_response.get_data())

    # Обратная независимость: более длинный extra не меняет description.
    state.on_message("/devices/weather/controls/description", sample_desc)
    for control, value in (("humidity", "100"), ("pressure", "1000"),
                           ("windspeed", "100")):
        state.on_message("/devices/weather/controls/%s" % control, value)
    with web.app.test_request_context("/panel/clock.svg?theme=light"):
        wide_extra_svg = web.render_panel("clock")
    wide_extra_root = check_clock_text_fits(wide_extra_svg)
    assert [el.text for el in clock_role(wide_extra_root, "desc")] == [sample_desc] * 3
    assert [font_size(el) for el in clock_role(wide_extra_root, "desc")] == sample_desc_sizes
    wide_extra_sizes = [font_size(el) for el in clock_role(wide_extra_root, "extra")]
    assert all(size <= base for size, base in zip(wide_extra_sizes, sample_extra_sizes))
    assert [el.text for el in clock_role(wide_extra_root, "metric")] == [
        "100 %", "1000 мм", "100 км/ч"]

    # Если даже минимального кегля мало, только описание получает мягкое
    # SVG-сжатие ровно до доступной ширины; строка показателей не затрагивается.
    extreme_text = "Очень сильная облачность с продолжительным дождём и порывистым ветром"
    state.on_message("/devices/weather/controls/description", extreme_text)
    with web.app.test_request_context("/panel/clock.svg?theme=light"):
        extreme_svg = web.render_panel("clock")
    extreme_root = check_clock_text_fits(extreme_svg)
    assert any(el.attrib.get("textLength") for el in clock_role(extreme_root, "desc"))
    assert [(font_size(el), el.attrib.get("textLength"))
            for el in clock_role(extreme_root, "extra")] == [
                (font_size(el), el.attrib.get("textLength"))
                for el in clock_role(wide_extra_root, "extra")]
    assert [(font_size(el), el.attrib.get("textLength"))
            for el in clock_role(extreme_root, "metric")] == [
                (font_size(el), el.attrib.get("textLength"))
                for el in clock_role(wide_extra_root, "metric")]

    print("OK: clock/forecast SVG без названия, центральные блоки, крупное время, темы, inv, −12.5° и цифры")


if __name__ == "__main__":
    main()
