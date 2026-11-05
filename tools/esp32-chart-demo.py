#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Локальный стенд графика esp32-panel, без Wiren Board, MQTT и SQLite.

Экспорт JSON для симулятора:
    python3 tools/esp32-chart-demo.py --out /tmp/esp32-chart
    tools/sim/sim --screen /tmp/esp32-chart/layout.json \
        /tmp/esp32-chart/values.txt /tmp/esp32-chart/tile.ppm 0 \
        /tmp/esp32-chart/history.json
    # Последний аргумент 1 открывает график во весь экран.

Настоящий HTTP API на демонстрационных данных:
    python3 tools/esp32-chart-demo.py --serve --port 8080
    curl http://127.0.0.1:8080/api/screen/espchart
    curl http://127.0.0.1:8080/api/screen/espchart/chart/0
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))
import env  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="/tmp/esp32-chart", help="папка для JSON и значений MQTT")
    parser.add_argument("--serve", action="store_true", help="после экспорта запустить HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    web, _palette, _cfg, _sent = env.setup(
        config=os.path.join(ROOT, "tests", "fixtures", "espchart.yaml"),
        freeze_time=True, history=True)
    client = web.app.test_client()
    layout_response = client.get("/api/screen/espchart")
    assert layout_response.status_code == 200, layout_response.get_data(as_text=True)
    layout = layout_response.get_json()
    tile = layout["pages"][0]["tiles"][0]
    history_response = client.get(tile["history"])
    assert history_response.status_code == 200, history_response.get_data(as_text=True)
    history = history_response.get_json()

    os.makedirs(args.out, exist_ok=True)
    for name, data in (("layout.json", layout), ("history.json", history)):
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    with open(os.path.join(args.out, "values.txt"), "w", encoding="utf-8") as fh:
        for source, result in zip(tile["series"], history["series"]):
            value = result["values"][-1][1] if result["values"] else 0
            fh.write("%s=%.*f\n" % (source["value"], source["digits"], value))

    print("Стенд: %s" % args.out)
    print("Плитка:  sim --screen %s/layout.json %s/values.txt %s/tile.ppm 0 %s/history.json" %
          (args.out, args.out, args.out, args.out))
    print("Экран:   sim --screen %s/layout.json %s/values.txt %s/full.ppm 0 %s/history.json 1" %
          (args.out, args.out, args.out, args.out))
    if args.serve:
        print("API:     http://%s:%d/api/screen/espchart" % (args.host, args.port))
        web.app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
