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

# (метод, адрес, тело, допустимые ответы, что должно быть в ответе)
CASES = [
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
]

#: Адреса, которые проверяются выше (правила Flask).
COVERED = {"/", "/healthz", "/manifest.webmanifest", "/icon.svg", "/docs",
           "/<name>.html", "/<name>.svg", "/tile/<name>/<int:index>.svg", "/chart.svg",
           "/themes", "/api/theme/draft", "/channels", "/check", "/stats",
           "/api/list/<name>", "/api/publish", "/cam/<name>.jpg", "/cam/<name>.mjpeg",
           "/glyph/<name>.svg", "/static/<path:filename>"}


def main():
    bad = 0
    for method, url, body, statuses, needle in CASES:
        kw = {}
        if body is not None:
            kw["data"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            kw["content_type"] = "application/json"
        try:
            r = client.open(url, method=method, **kw)
            text = r.get_data(as_text=True)
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
    if not any(t == "/devices/sw/controls/K1/on" for t, _v in sent):
        bad += 1
        print("FAIL публикация не дошла до брокера")
    print("адресов: %d, проверок: %d%s" % (len(rules), len(CASES),
                                            ", ошибок: %d" % bad if bad else " - всё отвечает"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
