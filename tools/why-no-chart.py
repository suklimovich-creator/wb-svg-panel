#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Почему график пустой.

    python3 /mnt/data/wb-svg-panel/why-no-chart.py

Проходит по всем графикам из config.yaml и для каждого канала отвечает на
четыре вопроса по порядку. Первый же «нет» - и есть причина.

    1. Канал вообще существует?             (есть ли он в MQTT)
    2. Он попал в конфиг wb-mqtt-db?        (пишется ли в базу)
    3. В базе есть записи?                  (сколько и с каких пор)
    4. Есть записи за нужный период?        (за range плитки)

Ничего не меняет, только читает.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DB = os.environ.get("WB_PANEL_DB", "/var/lib/wirenboard/db/data.db")
CONF = os.environ.get("WB_PANEL_CONFIG", os.path.join(HERE, "config.yaml"))

import importlib.util                                    # noqa: E402
spec = importlib.util.spec_from_file_location("panel_mod", os.path.join(HERE, "panel.py"))
pm = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(pm)
except Exception as exc:                                 # noqa: BLE001
    sys.exit("не удалось загрузить panel.py: %s" % exc)

import yaml                                              # noqa: E402



def iter_tiles(panel):
    """Плитки панели, включая лежащие внутри разделов."""
    for tile in panel.get("tiles", []) or []:
        yield tile
    for section in panel.get("sections", []) or []:
        if isinstance(section, dict):
            for tile in section.get("tiles", []) or []:
                yield tile


def human(seconds):
    if seconds < 3600:
        return "%.0f мин" % (seconds / 60.0)
    if seconds < 172800:
        return "%.1f ч" % (seconds / 3600.0)
    return "%.1f сут" % (seconds / 86400.0)


# ---- что нужно графикам ----------------------------------------------------
with open(CONF, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}

wanted = []          # (панель, заголовок, канал, span, points)
for pname, panel in (cfg.get("panels", {}) or {}).items():
    for tile in iter_tiles(panel):
        if tile.get("type") != "chart":
            continue
        span = pm.parse_duration(tile.get("range", "24h"))
        points = int(tile.get("points", 90))
        for s in tile.get("series", []) or []:
            if s.get("channel"):
                wanted.append((pname, tile.get("title", ""), s["channel"], span, points))

if not wanted:
    sys.exit("В config.yaml нет ни одного графика (плиток type: chart).")

print("Графиков в конфиге: %d, каналов: %d\n" % (
    len(set((w[0], w[1]) for w in wanted)), len(set(w[2] for w in wanted))))

# ---- 1. MQTT ---------------------------------------------------------------
live = set()
try:
    import paho.mqtt.client as mqtt
    try:
        cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="wb-panel-why")
    except (AttributeError, TypeError):
        cli = mqtt.Client(client_id="wb-panel-why")

    def on_msg(c, u, msg):
        p = msg.topic.split("/")
        if len(p) == 5:
            live.add("%s/%s" % (p[2], p[4]))

    cli.on_message = on_msg
    cli.connect("localhost", 1883, 10)
    cli.subscribe("/devices/+/controls/+")
    cli.loop_start()
    time.sleep(3)
    cli.loop_stop()
except Exception as exc:                                 # noqa: BLE001
    print("! MQTT недоступен (%s) - проверку 1 пропускаю\n" % exc)
    live = None

# ---- 2-4. база -------------------------------------------------------------
backend = pm.SqliteBackend(DB)
schema = backend.introspect()
if not schema:
    print("! База истории не разобрана: %s" % backend.error)
    print("  Без неё графиков не будет. Запустите: systemctl status wb-mqtt-db")
    logged = {}
else:
    print("База: %s, время в колонке %s (%s)\n"
          % (os.path.basename(DB), schema["ts"], schema["ts_kind"]))
    logged = backend.channels()

# ---- отчёт -----------------------------------------------------------------
bad_mqtt, bad_conf, bad_range = [], [], []
seen = set()

for pname, title, channel, span, points in wanted:
    if (channel, span) in seen:
        continue
    seen.add((channel, span))
    tag = "%s / %s" % (pname, title)
    print("%s" % channel)
    print("   плитка: %s, глубина %s" % (tag, human(span)))

    if live is not None and channel not in live:
        print("   [1] НЕТ в MQTT - опечатка в имени? сверьтесь со страницей /channels")
        bad_mqtt.append(channel)
        print()
        continue
    if live is not None:
        print("   [1] есть в MQTT")

    if channel not in logged:
        print("   [2] НЕ ПИШЕТСЯ в базу - канала нет в /etc/wb-mqtt-db.conf")
        bad_conf.append(channel)
        print()
        continue
    print("   [3] в базе записей: %s" % logged[channel])

    series = backend.values(channel, span, points)
    if not series:
        wide = backend.values(channel, 30 * 86400, points)
        if wide:
            age = time.time() - wide[-1][0]
            print("   [4] за %s данных НЕТ; последняя запись %s назад"
                  % (human(span), human(age)))
            print("       -> либо датчик молчит, либо ретенция съела старое")
        else:
            print("   [4] данных нет и за месяц - записи только что начались?")
        bad_range.append(channel)
    else:
        age = time.time() - series[-1][0]
        step = (series[-1][0] - series[0][0]) / max(len(series) - 1, 1)
        print("   [4] точек за период: %d, последняя %s назад, шаг ~%s"
              % (len(series), human(age), human(step)))
        print("       ГРАФИК ДОЛЖЕН РИСОВАТЬСЯ")
    print()

# ---- итог ------------------------------------------------------------------
print("=" * 72)
if not (bad_mqtt or bad_conf or bad_range):
    print("Все каналы в порядке. Если график всё равно пуст - перезапустите демон")
    print("и проверьте лог: journalctl -u wb-svg-panel -n 50")
    sys.exit(0)

if bad_mqtt:
    print("\nНеверные имена каналов (%d):" % len(bad_mqtt))
    for c in bad_mqtt:
        print("   %s" % c)
    print("   -> откройте http://<контроллер>:8080/channels и скопируйте точные имена")

if bad_conf:
    print("\nНе пишутся в базу (%d). Добавьте группу в /etc/wb-mqtt-db.conf:" % len(bad_conf))
    print("""
    {
      "name": "panel",
      "channels": [%s
      ],
      "min_interval": 60,
      "values": 2000,
      "values_total": 20000
    }
""" % (",".join("\n        \"%s\"" % c for c in bad_conf)))
    print("    затем:  systemctl restart wb-mqtt-db")
    print("    первые точки появятся через несколько минут,")
    print("    полный график за 12 часов - через 12 часов.")

if bad_range:
    print("\nПишутся, но за нужный период пусто (%d):" % len(bad_range))
    for c in bad_range:
        print("   %s" % c)
    print("   -> уменьшите range у плитки, либо поднимите values в wb-mqtt-db.conf")
sys.exit(1)
