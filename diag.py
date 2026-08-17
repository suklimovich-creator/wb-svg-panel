#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Диагностика окружения для wb-svg-panel.

    python3 /mnt/data/wb-svg-panel/diag.py

Проверяет по порядку: файлы проекта, зависимости, MQTT, базу истории,
MQTT-RPC и конфиг. Ничего не меняет, только читает.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("WB_PANEL_DB", "/var/lib/wirenboard/db/data.db")
CONF = os.environ.get("WB_PANEL_CONFIG", os.path.join(HERE, "config.yaml"))

OK, BAD, WARN = "  ok  ", " ОШИБКА ", " ! "
problems = []


def say(mark, text):
    print("[%s] %s" % (mark, text))
    if mark is BAD:
        problems.append(text)


def iter_tiles(panel):
    """Плитки панели, включая лежащие внутри разделов."""
    for tile in panel.get("tiles", []) or []:
        yield tile
    for section in panel.get("sections", []) or []:
        if isinstance(section, dict):
            for tile in section.get("tiles", []) or []:
                yield tile

print("=" * 72)
print("1. Файлы проекта в %s" % HERE)
print("=" * 72)
need = ["panel.py", "config.yaml", "wb-svg-panel.service",
        "nginx-wb-svg-panel.conf", "install.sh"]
for rel in need:
    path = os.path.join(HERE, rel)
    if os.path.exists(path):
        say(OK, "%-28s %d байт" % (rel, os.path.getsize(path)))
    else:
        say(BAD, "%-28s НЕТ ФАЙЛА - скопируйте его на контроллер" % rel)

# шаблон годится в любой раскладке
tpl = None
for rel in ("templates/panel.svg.j2", "panel.svg.j2"):
    if os.path.exists(os.path.join(HERE, rel)):
        tpl = rel
        break
if tpl:
    say(OK, "%-28s %d байт" % (tpl, os.path.getsize(os.path.join(HERE, tpl))))
else:
    say(BAD, "panel.svg.j2                 НЕТ ФАЙЛА (ни в templates/, ни рядом)")

if os.path.exists(os.path.join(HERE, "docs.html")):
    say(OK, "%-28s %d байт" % ("docs.html", os.path.getsize(os.path.join(HERE, "docs.html"))))
else:
    say(WARN, "docs.html                    нет - страница /docs не откроется")

if not HERE.startswith("/mnt/data"):
    say(WARN, "каталог вне /mnt/data - обновление прошивки его сотрёт")

print()
print("=" * 72)
print("2. Зависимости")
print("=" * 72)
for mod, pkg in (("paho.mqtt.client", "python3-paho-mqtt"), ("jinja2", "python3-jinja2"),
                 ("yaml", "python3-yaml"), ("flask", "python3-flask"),
                 ("waitress", "python3-waitress (необязательно)")):
    try:
        __import__(mod)
        say(OK, "%-20s %s" % (mod, pkg))
    except ImportError:
        mark = WARN if "необязательно" in pkg else BAD
        say(mark, "%-20s нет: apt install %s" % (mod, pkg.split(" ")[0]))

print()
print("=" * 72)
print("3. Конфиг %s" % CONF)
print("=" * 72)
wanted = set()
try:
    import yaml
    with open(CONF, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    panels = cfg.get("panels", {}) or {}
    say(OK, "панелей: %d (%s)" % (len(panels), ", ".join(sorted(panels))))
    charts = []
    for pname, panel in panels.items():
        for tile in iter_tiles(panel):
            for key, value in tile.items():
                if value and (key == "channel" or key.startswith("channel_")):
                    wanted.add(value)
            for s in tile.get("series", []) or []:
                if s.get("channel"):
                    wanted.add(s["channel"])
                    charts.append(s["channel"])
    say(OK, "каналов задействовано: %d, из них в графиках: %d"
        % (len(wanted), len(set(charts))))
except Exception as exc:                              # noqa: BLE001
    say(BAD, "не читается: %s" % exc)

print()
print("=" * 72)
print("4. MQTT")
print("=" * 72)
live = {}
try:
    import paho.mqtt.client as mqtt
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="wb-panel-diag")
    except (AttributeError, TypeError):
        cli = mqtt.Client(client_id="wb-panel-diag")

    def on_msg(c, u, msg):
        p = msg.topic.split("/")
        if len(p) == 5:
            live["%s/%s" % (p[2], p[4])] = msg.payload.decode("utf-8", "replace")

    cli.on_message = on_msg
    cli.connect("localhost", 1883, 10)
    cli.subscribe("/devices/+/controls/+")
    cli.loop_start()
    time.sleep(3)
    cli.loop_stop()
    say(OK, "брокер отвечает, retained-каналов получено: %d" % len(live))
    if wanted:
        missing = sorted(w for w in wanted if w not in live)
        if missing:
            say(BAD, "нет в MQTT (%d) - проверьте имена в config.yaml:" % len(missing))
            for name in missing[:12]:
                print("        %s" % name)
            if len(missing) > 12:
                print("        ... и ещё %d" % (len(missing) - 12))
        else:
            say(OK, "все каналы из конфига присутствуют в MQTT")
except Exception as exc:                              # noqa: BLE001
    say(BAD, "MQTT недоступен: %s" % exc)

print()
print("=" * 72)
print("5. База истории %s" % DB)
print("=" * 72)
try:
    sys.path.insert(0, HERE)
    import sqlite3
    if not os.path.exists(DB):
        say(BAD, "файла нет - запущен ли wb-mqtt-db?")
    else:
        size = os.path.getsize(DB) / 1048576.0
        say(OK, "файл на месте, %.1f МБ" % size)
        con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=5)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        say(OK, "таблицы: %s" % ", ".join(tables))
        for t in tables:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            print("        %-12s %s" % (t, ", ".join(cols)))
        con.close()

        # разбор схемы теми же правилами, что и в демоне
        os.environ.setdefault("WB_PANEL_CONFIG", CONF)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "panel_mod", os.path.join(HERE, "panel.py"))
        pm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pm)
        backend = pm.SqliteBackend(DB)
        m = backend.introspect()
        if not m:
            say(BAD, "схема не разобрана: %s" % backend.error)
            say(WARN, "покажите вывод выше разработчику - правила разбора в SqliteBackend")
        else:
            say(OK, "схема разобрана: значения %s.%s, время %s (%s)"
                % (m["data"], m["value"], m["ts"], m["ts_kind"]))
            logged = backend.channels()
            say(OK, "каналов пишется в базу: %d" % len(logged))
            if wanted:
                no_hist = sorted(w for w in wanted if w not in logged)
                if no_hist:
                    say(WARN, "нет истории (график будет пустым) - %d:" % len(no_hist))
                    for name in no_hist[:12]:
                        print("        %s" % name)
                    print("        -> добавьте их в /etc/wb-mqtt-db.conf")
            top = sorted(logged.items(), key=lambda kv: -(kv[1] or 0))[:8]
            if top:
                print("        самые полные каналы:")
                for name, cnt in top:
                    print("        %-46s %s записей" % (name, cnt))
            for name in (sorted(set(charts)) if charts else [])[:3]:
                pts = backend.values(name, 86400, 90)
                if pts:
                    age = (time.time() - pts[-1][0]) / 60.0
                    say(OK, "%s: %d точек за сутки, последняя %.0f мин назад"
                        % (name, len(pts), age))
                else:
                    say(WARN, "%s: за сутки данных нет" % name)
except Exception as exc:                              # noqa: BLE001
    say(BAD, "база недоступна: %s" % exc)

print()
print("=" * 72)
print("6. MQTT-RPC (запасной источник истории)")
print("=" * 72)
try:
    import paho.mqtt.client as mqtt
    seen = []
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="wb-panel-diag2")
    except (AttributeError, TypeError):
        cli = mqtt.Client(client_id="wb-panel-diag2")
    reply = {}

    def on_msg2(c, u, msg):
        if msg.topic.endswith("/reply"):
            reply["body"] = msg.payload.decode("utf-8", "replace")
        else:
            seen.append(msg.topic)

    cli.on_message = on_msg2
    cli.connect("localhost", 1883, 10)
    cli.subscribe([("/rpc/v1/+/+/+", 0), ("/rpc/v1/+/+/+/wb-panel-diag2/reply", 0)])
    cli.loop_start()
    time.sleep(2)
    services = sorted(set("/".join(t.split("/")[3:5]) for t in seen))
    say(OK, "RPC-сервисов объявлено: %d" % len(services))
    for s in services:
        methods = sorted(set(t.split("/")[5] for t in seen
                             if "/".join(t.split("/")[3:5]) == s))
        print("        %-24s %s" % (s, ", ".join(methods)))
    if any(s.startswith("db_logger") for s in services):
        cli.publish("/rpc/v1/db_logger/history/get_channels/wb-panel-diag2",
                    json.dumps({"id": "1", "params": {}}))
        for _ in range(60):
            if "body" in reply:
                break
            time.sleep(0.25)
        if "body" in reply:
            say(OK, "db_logger отвечает, %d байт" % len(reply["body"]))
        else:
            say(WARN, "db_logger объявлен, но не ответил за 15 с "
                      "- используем прямое чтение SQLite")
    else:
        say(WARN, "db_logger среди RPC-сервисов не найден "
                  "- используем прямое чтение SQLite")
    cli.loop_stop()
except Exception as exc:                              # noqa: BLE001
    say(WARN, "проверка RPC не удалась: %s" % exc)

print()
print("=" * 72)
if problems:
    print("ИТОГ: проблем - %d" % len(problems))
    for p in problems:
        print("   • %s" % p)
    sys.exit(1)
print("ИТОГ: критичных проблем нет.")
