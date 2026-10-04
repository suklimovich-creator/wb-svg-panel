#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Куда уходит процессор демона - по строке раз в пару секунд.

    python3 tools/watch-load.py            # раз в 2 с, пока не Ctrl+C
    python3 tools/watch-load.py 5          # раз в 5 с

Читает /stats у работающей службы и печатает разницу между чтениями:

    время     ЦП   отрис.     ответы ждут 204  отдано   mqtt/с  история    камера  кто ел
    02:41:10  23%  1×140мс    3      6    0    0.4 МБ   85      -          -       waitress 15%, mqtt 6%
    02:41:12  96%  -          0      6    0    -        90      12×160мс   -       history 88%, mqtt 7%

ЦП - процент одного ядра (как в top): 100 % значит одно ядро занято
целиком, у WB7 их четыре. «кто ел» - процессор по потокам: waitress -
ответы и отрисовка, history - чтение графиков из базы, mqtt - приём
сообщений, config - перечитывание конфига.

Запускать на контроллере, в соседнем окне - что угодно: открывайте панели
на телефонах, щёлкайте светом, tools/load-test.sh.
"""

import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:%s/stats"


def read(port):
    with urllib.request.urlopen(URL % port, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def d(a, b, key, part="count"):
    return b[part].get(key, 0) - a[part].get(key, 0)


def ms_each(a, b, key):
    n = d(a, b, key)
    if n <= 0:
        return "-"
    return "%d×%dмс" % (n, round(1000 * d(a, b, key, "secs") / n))


def main():
    every = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    port = sys.argv[2] if len(sys.argv) > 2 else "8088"
    try:
        prev = read(port)
    except Exception as exc:                          # noqa: BLE001
        sys.exit("нет ответа от %s: %s (служба запущена? версия 1.12.5+?)"
                 % (URL % port, exc))
    print("%-9s %4s  %-10s %6s %4s %4s  %-8s %6s  %-10s %-8s %s" % (
        "время", "ЦП", "отрис.", "ответы", "ждут", "204", "отдано",
        "mqtt/с", "история", "камера", "кто ел"))
    while True:
        time.sleep(every)
        try:
            cur = read(port)
        except Exception as exc:                      # noqa: BLE001
            print("нет ответа: %s" % exc)
            continue
        wall = max(cur["time"] - prev["time"], 1e-6)
        cpu = 100.0 * (cur["cpu"] - prev["cpu"]) / wall
        mb = d(prev, cur, "http.bytes") / 1e6
        per = []
        for name, sec in cur["threads"].items():
            used = 100.0 * (sec - prev["threads"].get(name, 0.0)) / wall
            if used >= 1:
                per.append((used, name))
        per.sort(reverse=True)
        print("%-9s %3d%%  %-10s %6d %4d %4d  %-8s %6d  %-10s %-8s %s" % (
            time.strftime("%H:%M:%S"), round(cpu),
            ms_each(prev, cur, "render"),
            d(prev, cur, "http.svg"),
            cur["count"].get("http.waiting", 0),
            d(prev, cur, "http.204"),
            ("%.1f МБ" % mb) if mb >= 0.05 else "-",
            round(d(prev, cur, "mqtt") / wall),
            ms_each(prev, cur, "history"),
            ms_each(prev, cur, "camera"),
            ", ".join("%s %d%%" % (n, u) for u, n in per[:3]) or "-"))
        sys.stdout.flush()
        prev = cur


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
