#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разведка датчиков: что публикуется и как это выглядит по истории.

    python3 probe-sensors.py                      список устройств
    python3 probe-sensors.py wb-msw-v4_11          разбор одного устройства
    python3 probe-sensors.py wb-msw-v4_11 --days 14

Для числовых каналов считает, что для вашего дома нормально: медиану и
процентили за N дней. Из них предлагает границы диапазонов - вручную такие
цифры выдумывать бессмысленно, у каждого дома они свои.

Для дискретных каналов (движение, присутствие) смотрит динамику: сколько
срабатываний в сутки, сколько длится импульс, какие паузы между ними. Это
нужно, чтобы понять, можно ли вообще показывать движение на панели, которая
перерисовывается раз в десять секунд.

Ничего не пишет: только слушает MQTT и читает базу wb-mqtt-db на чтение.
"""

import argparse
import json
import os
import sqlite3
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Нет paho-mqtt:  apt install python3-paho-mqtt")

DB_PATHS = (
    "/var/lib/wirenboard/db/data.db",
    "/mnt/data/var/lib/wirenboard/db/data.db",
    "/var/lib/wb-mqtt-db/data.db",
    "/mnt/data/var/lib/wb-mqtt-db/data.db",
)

# Каналы, которые интересны строке состояния комнаты
INTERESTING = ("temperature", "humidity", "co2", "voc", "sound", "noise",
               "illuminance", "lux", "motion", "presence", "occupancy",
               "air quality", "pressure")


# --------------------------------------------------------------------- MQTT

class Bus(object):
    def __init__(self, host="localhost", port=1883):
        self.vals, self.meta = {}, {}
        self.count, self.connected = 0, False
        self.cli = mqtt.Client()
        self.cli.on_message = self._on_msg
        self.cli.on_connect = self._on_connect
        self.cli.connect(host, port, 30)
        self.cli.loop_start()
        deadline = time.time() + 10
        while not self.connected and time.time() < deadline:
            time.sleep(0.1)
        if not self.connected:
            sys.exit("Не удалось подключиться к брокеру %s:%d" % (host, port))

    def _on_connect(self, cli, ud, flags, rc):
        if rc != 0:
            sys.exit("Брокер отказал, код %s" % rc)
        cli.subscribe("/devices/+/controls/#")
        self.connected = True

    def _on_msg(self, cli, ud, msg):
        t = msg.topic
        if not t.startswith("/devices/"):
            return
        rest = t[len("/devices/"):]
        if "/controls/" not in rest:
            return
        dev, ctl = rest.split("/controls/", 1)
        payload = msg.payload.decode("utf-8", "replace")
        self.count += 1
        if ctl.endswith("/meta"):
            try:
                self.meta.setdefault("%s/%s" % (dev, ctl[:-5]), {}).update(
                    json.loads(payload))
            except ValueError:
                pass
        elif "/meta/" in ctl:
            c, k = ctl.rsplit("/meta/", 1)
            self.meta.setdefault("%s/%s" % (dev, c), {})[k] = payload
        else:
            self.vals["%s/%s" % (dev, ctl)] = payload

    def settle(self, seconds=4):
        time.sleep(seconds)
        if self.count == 0:
            sys.exit("Ни одного сообщения из MQTT. Брокер точно работает?")


# ------------------------------------------------------------------ история

class Db(object):
    """Чтение wb-mqtt-db. Схема между версиями разная, поэтому ищем на месте."""

    def __init__(self):
        self.con, self.path, self.map = None, None, None
        for p in DB_PATHS:
            if os.path.exists(p):
                self.path = p
                break
        if not self.path:
            return
        try:
            self.con = sqlite3.connect("file:%s?mode=ro" % self.path, uri=True)
        except sqlite3.Error as exc:
            print("База не открылась: %s" % exc)
            return
        self.map = self._detect()

    def _tables(self):
        return set(r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))

    def _cols(self, table):
        return [r[1] for r in self.con.execute('PRAGMA table_info("%s")' % table)]

    def _detect(self):
        tabs = self._tables()
        if "data" not in tabs or "channels" not in tabs:
            return None
        dcols, ccols = self._cols("data"), self._cols("channels")

        def pick(cands, cols):
            for c in cands:
                if c in cols:
                    return c
            return None

        ts = pick(("timestamp", "ts", "time"), dcols)
        val = pick(("value",), dcols)
        ref = pick(("channel", "channel_id", "channel_int_id"), dcols)
        cid = pick(("int_id", "id"), ccols)
        ctl = pick(("control", "control_name"), ccols)
        dev = pick(("device", "device_name", "device_id"), ccols)
        if not all((ts, val, ref, cid, ctl, dev)):
            return None

        # Метки времени: секунды, миллисекунды или julianday
        row = self.con.execute('SELECT MAX("%s") FROM "data"' % ts).fetchone()
        top = float(row[0] or 0)
        if top > 1e11:
            scale = "ms"
        elif top < 1e7:
            scale = "julian"
        else:
            scale = "s"

        # Устройство может лежать в отдельной таблице
        dev_join = ""
        dev_expr = 'c."%s"' % dev
        if dev.endswith("_id") and "devices" in tabs:
            dcols2 = self._cols("devices")
            dname = pick(("device", "name"), dcols2)
            did = pick(("int_id", "id"), dcols2)
            if dname and did:
                dev_join = ' JOIN "devices" d ON d."%s" = c."%s"' % (did, dev)
                dev_expr = 'd."%s"' % dname
        return {"ts": ts, "val": val, "ref": ref, "cid": cid, "ctl": ctl,
                "dev_expr": dev_expr, "dev_join": dev_join, "scale": scale}

    def since_expr(self, days):
        m, now = self.map, time.time()
        if m["scale"] == "ms":
            return (now - days * 86400) * 1000.0
        if m["scale"] == "julian":
            return (now - days * 86400) / 86400.0 + 2440587.5
        return now - days * 86400

    def series(self, key, days):
        """[(время_сек, значение_строкой)] по каналу 'устройство/канал'."""
        if not self.map:
            return []
        m = self.map
        dev, ctl = key.split("/", 1)
        sql = ('SELECT v."%(ts)s", v."%(val)s" FROM "data" v '
               'JOIN "channels" c ON c."%(cid)s" = v."%(ref)s"%(dj)s '
               'WHERE %(dev)s = ? AND c."%(ctl)s" = ? AND v."%(ts)s" >= ? '
               'ORDER BY v."%(ts)s"'
               % {"ts": m["ts"], "val": m["val"], "cid": m["cid"],
                  "ref": m["ref"], "dj": m["dev_join"], "dev": m["dev_expr"],
                  "ctl": m["ctl"]})
        out = []
        for ts, val in self.con.execute(sql, (dev, ctl, self.since_expr(days))):
            t = float(ts)
            if m["scale"] == "ms":
                t /= 1000.0
            elif m["scale"] == "julian":
                t = (t - 2440587.5) * 86400.0
            out.append((t, val))
        return out


# ------------------------------------------------------------------- расчёт

def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = q * (len(sorted_vals) - 1)
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def numbers(rows):
    out = []
    for _t, v in rows:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def fmt(x, digits=1):
    return "-" if x is None else ("%.*f" % (digits, x)).rstrip("0").rstrip(".")


def report_numeric(key, rows, days):
    vals = sorted(numbers(rows))
    if len(vals) < 20:
        print("      данных мало (%d точек) - диапазоны считать не на чем"
              % len(vals))
        return
    p05, p50, p95 = (quantile(vals, 0.05), quantile(vals, 0.50),
                     quantile(vals, 0.95))
    print("      точек: %-7d за %d дн." % (len(vals), days))
    print("      минимум %s   p05 %s   медиана %s   p95 %s   максимум %s"
          % (fmt(vals[0]), fmt(p05), fmt(p50), fmt(p95), fmt(vals[-1])))
    print("      обычный день укладывается в %s..%s" % (fmt(p05), fmt(p95)))
    print("      предложение для конфига:")
    print("        - channel: \"%s\"" % key)
    print("          normal: [%s, %s]      # p05..p95, «как обычно»" % (fmt(p05), fmt(p95)))
    print("          typical: %s            # медиана, пунктир на шкале" % fmt(p50))


def report_binary(key, rows, days):
    """Динамика дискретного канала: импульсы, паузы, доля активного времени."""
    seq = []
    for t, v in rows:
        s = (v or "").strip().lower()
        seq.append((t, s not in ("", "0", "false", "off")))
    if len(seq) < 4:
        print("      переключений почти нет (%d) - показывать нечего" % len(seq))
        return

    pulses, gaps, active = [], [], 0.0
    start = None
    prev_end = None
    for t, on in seq:
        if on and start is None:
            start = t
            if prev_end is not None:
                gaps.append(t - prev_end)
        elif not on and start is not None:
            pulses.append(t - start)
            active += t - start
            prev_end = t
            start = None

    span_days = max((seq[-1][0] - seq[0][0]) / 86400.0, 0.01)
    print("      переключений: %d за %.1f дн." % (len(seq), span_days))
    if pulses:
        ps = sorted(pulses)
        print("      импульсов: %d  (%.1f в сутки)"
              % (len(pulses), len(pulses) / span_days))
        print("      длительность: медиана %s с, минимум %s с, максимум %s с"
              % (fmt(quantile(ps, 0.5)), fmt(ps[0]), fmt(ps[-1])))
        short = sum(1 for p in pulses if p < 10)
        print("      короче 10 с: %d из %d (%.0f%%)"
              % (short, len(pulses), 100.0 * short / len(pulses)))
        if short > len(pulses) * 0.3:
            print("      -> панель с обновлением раз в 10 с пропустит заметную")
            print("         часть срабатываний. Показывайте давность последнего,")
            print("         а не сам факт: «движение» / «5 минут назад».")
        else:
            print("      -> импульсы длинные, факт движения показывать можно")
    if gaps:
        gs = sorted(gaps)
        print("      паузы между: медиана %.0f мин, максимум %.0f мин"
              % (quantile(gs, 0.5) / 60.0, gs[-1] / 60.0))
        print("      разумное «гаснет через»: %.0f мин" % (quantile(gs, 0.9) / 60.0))
    print("      активно %.1f%% времени" % (100.0 * active / (span_days * 86400)))


# -------------------------------------------------------------------- вывод

def cmd_list(bus):
    bus.settle(5)
    devs = {}
    for key in bus.vals:
        devs.setdefault(key.split("/")[0], []).append(key.split("/", 1)[1])
    print("\nУстройства с интересными для строки состояния каналами:\n")
    for d in sorted(devs):
        hits = [c for c in devs[d]
                if any(h in c.lower() for h in INTERESTING)]
        if hits:
            print("  %-28s %s" % (d, ", ".join(sorted(hits))))
    print("\nДальше:  python3 probe-sensors.py <устройство>")


def cmd_device(bus, dev, days, db):
    bus.settle()
    keys = sorted(k for k in bus.vals if k.startswith(dev + "/"))
    if not keys:
        sys.exit("Устройство %r ничего не публикует. Проверьте написание." % dev)

    if db.map:
        print("\nИстория: %s" % db.path)
    else:
        print("\nИстории нет - будут только текущие значения.")

    for key in keys:
        ctl = key.split("/", 1)[1]
        meta = bus.meta.get(key, {})
        unit = meta.get("units") or meta.get("unit") or ""
        typ = meta.get("type", "")
        print("\n%s" % ("-" * 66))
        print("  %s = %s %s   %s" % (ctl, bus.vals[key], unit, typ))

        if not db.map:
            continue
        rows = db.series(key, days)
        if not rows:
            print("      в истории пусто - канал не пишется в wb-mqtt-db")
            continue

        sample = set((v or "").strip() for _t, v in rows[:200])
        binary = sample <= {"0", "1", "", "true", "false", "True", "False"}
        if binary or typ in ("switch", "pushbutton", "alarm"):
            report_binary(key, rows, days)
        else:
            report_numeric(key, rows, days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="?")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()

    bus = Bus(args.host, args.port)
    if args.device:
        cmd_device(bus, args.device, args.days, Db())
    else:
        cmd_list(bus)


if __name__ == "__main__":
    main()
