# -*- coding: utf-8 -*-
"""
Каждый адрес демона отвечает и не падает (с 1.46): страницы, картинки
панелей и плиток, график, API списков, публикация, темы, проверка
конфига, статистика. Без сети и MQTT - на выдуманных приборах
(tests/fixtures), как tests/render_check.py.

    python3 tests/render_check.py && python3 tests/routes_check.py

Новый адрес в web*.py без строки здесь - ошибка: проверка сверяет список
ниже с картой адресов Flask.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import env  # noqa: E402  (fixtures/env.py)

web, palette, cfg, sent = env.setup(freeze_time=True)
client = web.app.test_client()

# Обои стекла своей картинкой (1.50): адрес с ключом - из картинки панели.
_wall = client.get("/gal.svg?theme=glass").get_data(as_text=True)
_wall = _wall.split('data-wall-img="', 1)[1].split('"', 1)[0] if 'data-wall-img="' in _wall else "wall/none.jpg"

# Заставка (1.57): резкая картинка - адрес из настроек страницы панели.
_page = client.get("/gal.html").get_data(as_text=True)
_sharp = _page.split('"saver_img": "', 1)[1].split('"', 1)[0] if '"saver_img": "' in _page else "wall/none-s.jpg"

# (метод, адрес, тело, допустимые ответы, что должно быть в ответе)
CASES = [
    ("GET", "/" + _wall, None, (200,), ""),
    ("GET", "/" + _sharp, None, (200,), ""),
    ("GET", "/gal.html", None, (200,), '"pad_close": 120'),
    ("GET", "/obj.svg", None, (200,), 'data-list-font="19"'),
    ("GET", "/gal.html", None, (200,), '"saver_panel": "obj"'),
    ("GET", "/gal.svg?theme=glass&inline=1", None, (200,), 'data:image/jpeg;base64,'),
    # демо своей панели (1.54): demo_panels: [gal] в fixtures/showcase.yaml
    ("GET", "/_demo-gal.html", None, (200,), "_demo-gal"),
    ("GET", "/_demo-gal.svg", None, (200,), "/devices/_demo_"),
    ("GET", "/_demo-misc.svg", None, (404,), ""),
    ("GET", "/tile/_demo-gal/0.svg?w=390&h=844&view=pad", None, (200,), "<svg"),
    ("GET", "/wall/0123456789ab.jpg", None, (404,), ""),
    ("GET", "/", None, (200,), "gal"),
    ("GET", "/healthz", None, (200,), ""),
    ("GET", "/manifest.webmanifest", None, (200,), "name"),
    ("GET", "/icon.svg", None, (200,), "<svg"),
    ("GET", "/docs", None, (200, 404), ""),
    ("GET", "/gal.html", None, (200,), "static/js/core.js"),
    ("GET", "/_demo.html", None, (200,), "static/js/update.js"),
    ("GET", "/gal.svg", None, (200,), 'data-main="1"'),
    ("GET", "/gal.svg?nav=1.2&lite=1&theme=glass", None, (200,), 'data-nav="menu"'),
    ("GET", "/obj.svg?cols=3&mode=tabs", None, (200,), "<svg"),
    ("GET", "/_demo.svg", None, (200,), "<svg"),
    ("GET", "/nope.svg", None, (404,), ""),
    ("GET", "/nope.html", None, (200, 404), ""),
    ("GET", "/tile/gal/0.svg?w=390&h=844&view=pad", None, (200,), "<svg"),
    ("GET", "/tile/fcs/0.svg?w=390&h=844&view=pad", None, (200,), "<svg"),
    ("GET", "/tile/gal/999.svg", None, (404,), ""),
    ("GET", "/chart.svg?channel=msw/Temperature&title=t&range=6h", None, (200,), "<svg"),
    ("GET", "/chart.svg", None, (400,), ""),
    ("GET", "/themes", None, (200,), ""),
    ("POST", "/api/theme/draft", {"theme": {"title": "проба", "extends": "light",
                                            "vars": {"tile": "#FFEEDD"}}}, (200,), "draft"),
    ("POST", "/api/theme/draft", "не json", (400,), ""),
    ("GET", "/channels", None, (200,), ""),
    ("GET", "/check", None, (200,), ""),
    ("GET", "/stats", None, (200,), ""),
    ("GET", "/api/list/shopping", None, (200,), "open"),
    ("POST", "/api/list/shopping", {"op": "add", "text": "проверка"}, (200,), "проверка"),
    ("POST", "/api/list/shopping", {"op": "nope"}, (400,), ""),
    ("GET", "/api/list/nope", None, (404,), ""),
    ("POST", "/api/publish", {"topic": "/devices/sw/controls/K1/on", "value": "1"}, (200,), "ok"),
    ("POST", "/api/publish", {"topic": "/devices/evil/controls/x/on", "value": "1"}, (403,), ""),
    ("POST", "/api/publish", {}, (400,), ""),
    ("GET", "/cam/nope.jpg", None, (404,), ""),
    ("GET", "/cam/nope.mjpeg", None, (404,), ""),
    ("GET", "/glyph/faucet.svg?open=1&color=%23D9433C&size=120", None, (200,), 'class="fct'),
    ("GET", "/glyph/puck.svg?wet=1", None, (200,), "puck wet"),
    ("GET", "/glyph/calpage.svg?day=17&wd=%D1%81%D0%B1&size=30", None, (200,), "СБ"),
    ("GET", "/glyph/nope.svg", None, (404,), ""),
    ("GET", "/static/panel.css", None, (200,), ""),
    ("GET", "/static/js/core.js", None, (200,), ""),
    # Раскладка для настенного экрана esp32-panel (1.70)
    ("GET", "/api/screen/misc", None, (200,), '"type":"dimmer"'),
    ("GET", "/api/screen/scn", None, (200,), '"type":"scene"'),
    ("GET", "/api/screen/scn", None, (200,), '"icon":"moon"'),
    ("GET", "/api/screen/misc", None, (200,), '"light":true'),
    ("GET", "/api/screen/clim", None, (200,), '"type":"info"'),
    ("GET", "/api/screen/clim", None, (200,), '"kind":"climate"'),
    ("GET", "/api/screen/nope/chart/0", None, (404,), ""),
    ("GET", "/api/screen/misc/screensaver/chart/0", None, (404,), ""),
    ("GET", "/api/screen/nope", None, (404,), ""),
]

#: Адреса, которые проверяются выше (правила Flask).
COVERED = {"/", "/healthz", "/manifest.webmanifest", "/icon.svg", "/docs",
           "/<name>.html", "/<name>.svg", "/tile/<name>/<int:index>.svg", "/chart.svg",
           "/themes", "/api/theme/draft", "/channels", "/check", "/stats",
           "/api/list/<name>", "/api/publish", "/cam/<name>.jpg", "/cam/<name>.mjpeg",
           "/glyph/<name>.svg", "/static/<path:filename>", "/wall/<key>.jpg",
           "/api/screen/<name>", "/api/screen/<name>/chart/<int:index>",
           "/api/screen/<name>/screensaver/chart/<int:index>"}


def main():
    bad = 0
    for method, url, body, statuses, needle in CASES:
        kw = {}
        if body is not None:
            kw["data"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            kw["content_type"] = "application/json"
        try:
            r = client.open(url, method=method, **kw)
            text = r.get_data().decode("utf-8", "replace")
            good = r.status_code in statuses and (not needle or needle in text)
            note = "%d" % r.status_code
        except Exception as exc:  # noqa: BLE001
            good, note = False, "исключение %s: %s" % (type(exc).__name__, exc)
        if not good:
            bad += 1
            print("FAIL %-6s %-55s %s (ждали %s%s)" % (method, url, note, statuses,
                                                       ", «%s»" % needle if needle else ""))
    rules = {str(r) for r in web.app.url_map.iter_rules()}
    missing = sorted(rules - COVERED - {"/_log"})
    if missing:
        bad += 1
        print("FAIL адреса без проверки в tests/routes_check.py: %s" % ", ".join(missing))
    # Демо: ни одного настоящего топика в картинке, и нажатие не уходит в брокер.
    import re as _re
    demo_svg = client.get("/_demo-gal.svg").get_data(as_text=True)
    real = [t for t in _re.findall(r'data-(?:topic|bright|temp|target|ac-[a-z-]+|mode-topic|stop|reset)="([^"]*/[^"]*)"',
                                     demo_svg) if not t.startswith("/devices/_demo_")]
    n_sent = len(sent)
    topic = _re.search(r'data-kind="switch"[^>]*data-topic="([^"]+)"', demo_svg).group(1)
    r = client.post("/api/publish", data=json.dumps({"topic": topic, "value": "1"}),
                    content_type="application/json")
    if real or r.status_code != 200 or len(sent) != n_sent:
        bad += 1
        print("FAIL демо: настоящие топики %s, ответ %d, ушло в брокер %d" % (real[:3], r.status_code, len(sent) - n_sent))
    if not any(t == "/devices/sw/controls/K1/on" for t, _v in sent):
        bad += 1
        print("FAIL публикация не дошла до брокера")
    # 1.58: заставка - дело панели: общая только на panels:, своя у панели
    # важнее, на самой панели заставки её нет, ширина - cols панели
    _keep = cfg.get("screensaver")
    cfg.data["screensaver"] = {"after": "5m", "panel": "obj", "panels": ["gal"]}
    _i = [web.idle_of(cfg.panels.get(n) or {}, n)["saver"] for n in ("gal", "ac_light", "obj")]
    _own = web.idle_of({"screensaver": "1m", "cols": 6}, "ac_light")
    cfg.data["screensaver"] = _keep
    if _i != [300, 0, 0] or _own["saver"] != 60 or _own["cols"] != 6:
        print("FAIL заставка по панелям:", _i, _own)
        bad += 1

    # 1.63: from: списком и сборная панель внутри сборной
    from wbpanel.assemble import resolve_sections as _rs
    _ps = {"a": {"tiles": [{"type": "switch"}], "status": [{"leak": "all"}]},
           "b": {"tiles": [{"type": "dimmer"}, {"type": "light"}]},
           "ab": {"sections": [{"from": ["a", "b"]}]}}
    _r1 = _rs({"sections": [{"from": ["a", "b"], "title": "Т"}]}, _ps)
    _r2 = _rs({"sections": [{"from": "ab", "title": "Т"}]}, _ps)
    if [len(_r1[0][1]), _r1[0][1][1].get("type"), _r1[0][3], len(_r1[0][2].get("status") or [])] != [4, "header", "a-b", 1] or len(_r2[0][1]) != 3:
        print("FAIL from списком:", _r1, _r2)
        bad += 1
    # 1.61: !secret и !include, перечитывание по правке secrets.yaml
    import tempfile, time as _t
    from wbpanel.config import Config as _C
    _d = tempfile.mkdtemp()
    with open(os.path.join(_d, "config.yaml"), "w") as _f:
        _f.write("cams: {hall: !secret cam}\ncal: !include cal.yaml\nt: /sprut/${sid}/x\nu: a ${nope} b\npanels: {}\n")
    with open(os.path.join(_d, "secrets.yaml"), "w") as _f:
        _f.write("cam: A\nics: B\nsid: 77\n")
    with open(os.path.join(_d, "cal.yaml"), "w") as _f:
        _f.write("home: !secret ics\n")
    _c = _C(os.path.join(_d, "config.yaml"))
    _a = (_c.data["cams"]["hall"], _c.data["cal"]["home"])
    _t.sleep(1.1)
    with open(os.path.join(_d, "secrets.yaml"), "w") as _f:
        _f.write("cam: C\nics: D\nsid: 77\n")
    _c.reload()
    if (_c.data["t"], _c.data["u"]) != ("/sprut/77/x", "a ${nope} b"):
        print("FAIL ${имя}:", _c.data["t"], _c.data["u"])
        bad += 1
    if _a != ("A", "B") or (_c.data["cams"]["hall"], _c.data["cal"]["home"]) != ("C", "D"):
        print("FAIL секреты:", _a, _c.data)
        bad += 1
    print("адресов: %d, проверок: %d%s" % (len(rules), len(CASES),
                                            ", ошибок: %d" % bad if bad else " - всё отвечает"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
