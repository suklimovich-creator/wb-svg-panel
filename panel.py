#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-svg-panel - SVG-панели умного дома для контроллеров Wiren Board.

Один процесс делает всё:
  * держит постоянную подписку на /devices/+/controls/+ и хранит состояние в памяти;
  * фоновым потоком тянет историю для графиков через MQTT-RPC db_logger/history;
  * отдаёт панели по HTTP как image/svg+xml.

Вся визуальная часть живёт в templates/panel.svg.j2 - Python сюда не лезет,
он только считает числа и геометрию.
"""

import html
import itertools
import json
import logging
import os
import math
import re
import sqlite3
import sys
import threading
import time

import paho.mqtt.client as mqtt
import yaml
from flask import Flask, Response, abort, request
from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_CANDIDATES = [
    "/var/lib/wirenboard/db/data.db",
    "/mnt/data/var/lib/wirenboard/db/data.db",
    "/var/lib/wb-mqtt-db/data.db",
    "/mnt/data/var/lib/wb-mqtt-db/data.db",
]


def find_db():
    """Путь к базе истории отличается между прошивками - ищем."""
    env = os.environ.get("WB_PANEL_DB")
    if env:
        return env
    for path in DB_CANDIDATES:
        if os.path.exists(path):
            return path
    return DB_CANDIDATES[0]


DB_PATH = find_db()
CONFIG_PATH = os.environ.get("WB_PANEL_CONFIG", os.path.join(BASE_DIR, "config.yaml"))
# Шаблон ищется и в подкаталоге templates/, и рядом с panel.py: при скачивании
# проекта одним архивом вложенная папка часто теряется, и это нормально.
TEMPLATE_DIRS = [
    os.environ.get("WB_PANEL_TEMPLATES", os.path.join(BASE_DIR, "templates")),
    BASE_DIR,
]

log = logging.getLogger("wb-svg-panel")

# --------------------------------------------------------------------------
# Геометрия сетки. Меняется здесь и больше нигде.
# --------------------------------------------------------------------------
# Сетка считается в ПОЛОВИНКАХ плитки. Обычная плитка занимает 2x2 таких
# ячейки и остаётся ровно 170 px, как раньше, - геометрия не изменилась.
# Зато теперь можно поставить мелкую плитку в одну ячейку: w: 0.5, h: 0.5.
CELL = 78           # сторона мелкой ячейки, px
GAP = 14            # зазор между ячейками
TILE = CELL * 2 + GAP   # обычная плитка, 170 px
PAD = 20            # внешние поля
HEADER = 54         # высота шапки панели (0 - если title не задан)
RX = 24             # радиус скругления плитки
SECTION = 44        # высота заголовка раздела внутри панели
BAND = (0.40, 0.82) # в какой доле высоты плитки живёт кривая графика


def font_scale(height, base=TILE):
    """
    Во сколько раз увеличить подписи на крупной плитке.

    Не линейно по высоте: при росте в 3.6 раза буквы в 3.6 раза выглядели бы
    плакатом. Показатель 0.6 даёт заметный, но спокойный рост.
    """
    k = max(float(height) / float(base), 1.0) ** 0.6
    return round(min(k, 2.4), 2)


# ==========================================================================
#  Конфиг
# ==========================================================================

class Config:
    """Конфиг с горячей перезагрузкой по mtime - правите YAML, жмёте F5."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = 0
        self.data = {}
        self.reload(force=True)

    def reload(self, force=False):
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path)
            except OSError:
                return
            if not force and mtime == self._mtime:
                return
            with open(self.path, "r", encoding="utf-8") as fh:
                self.data = yaml.safe_load(fh) or {}
            self._mtime = mtime
            log.info("конфиг загружен: %s", self.path)

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def panels(self):
        return self.data.get("panels", {}) or {}

    def all_tiles(self):
        """Все плитки всех панелей, включая лежащие в разделах."""
        for panel in self.panels.values():
            for tile in panel.get("tiles", []) or []:
                yield tile
            for section in panel.get("sections", []) or []:
                if isinstance(section, dict):
                    for tile in section.get("tiles", []) or []:
                        yield tile

    def used_channels(self):
        """Каналы, которые реально нарисованы хоть на одной панели."""
        out = set()
        if True:
            for tile in self.all_tiles():
                if tile.get("type") == "forecast":
                    out.update(forecast_channels(tile))
                if tile.get("type") == "ac":
                    out.update(ac_channels(tile))
                for key, value in tile.items():
                    # любое поле channel / channel_* - это канал; так новые
                    # типы плиток не приходится дописывать в этот список
                    if value and (key == "channel" or key.startswith("channel_")):
                        out.add(value)
                for s in tile.get("series", []) or []:
                    if s.get("channel"):
                        out.add(s["channel"])
        # строка состояния - не плитка, её каналы надо собрать отдельно
        for panel in (self.panels or {}).values():
            for entry in status_entries(panel):
                for key in ("channel", "value_channel"):
                    if entry.get(key):
                        out.add(entry[key])
        return out

    def raw_topics(self):
        """
        Каналы, заданные полным MQTT-топиком, а не парой «устройство/канал».

        Соглашение Wiren Board - ровно один слэш (`wb-msw-v4_149/Temperature`).
        Всё, где слэшей больше, считаем сырым топиком: так на панель попадают
        устройства чужих интеграций - Sprut.hub, Zigbee2MQTT и прочие.
        """
        return set(ch for ch in self.used_channels() if ch.count("/") != 1)

    def chart_series(self):
        """(channel, range_seconds, points) для всех графиков - для префетча."""
        out = {}
        if True:
            for tile in self.all_tiles():
                if tile.get("type") != "chart":
                    continue
                span = parse_duration(tile.get("range", "24h"))
                points = int(tile.get("points", 90))
                for s in tile.get("series", []) or []:
                    if s.get("channel"):
                        out[(s["channel"], span, points)] = True
        return list(out.keys())


def parse_duration(text):
    """'24h' / '90m' / '7d' / 3600 -> секунды."""
    if isinstance(text, (int, float)):
        return int(text)
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])?\s*", str(text))
    if not m:
        return 86400
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2) or "s"]
    return int(m.group(1)) * mult


# ==========================================================================
#  Состояние из MQTT
# ==========================================================================

class WbState:
    """Текущие значения и метаданные всех каналов контроллера."""

    def __init__(self):
        self.lock = threading.RLock()
        self.values = {}    # "device/control" -> str
        self.meta = {}      # "device/control" -> dict
        self.stamps = {}    # "device/control" -> float (когда пришло)
        self.version = 0    # растёт, когда меняется что-то нарисованное
        self.watched = set()
        self.connected = False

    def set_watched(self, channels):
        with self.lock:
            self.watched = set(channels)

    def on_message(self, topic, payload):
        parts = topic.split("/")
        # ['', 'devices', <device>, 'controls', <control>, ...]
        if len(parts) < 5 or parts[1] != "devices" or parts[3] != "controls":
            # не соглашение WB - значит сырой топик стороннего шлюза,
            # кладём под самим топиком как под ключом
            with self.lock:
                changed = self.values.get(topic) != payload
                self.values[topic] = payload
                self.stamps[topic] = time.time()
                if changed and topic in self.watched:
                    self.version += 1
            return
        key = "%s/%s" % (parts[2], parts[4])
        rest = parts[5:]
        with self.lock:
            if not rest:
                changed = self.values.get(key) != payload
                self.values[key] = payload
                self.stamps[key] = time.time()
                if changed and key in self.watched:
                    self.version += 1
            elif rest == ["meta"]:
                try:
                    meta = json.loads(payload) if payload else {}
                except ValueError:
                    return
                if isinstance(meta, dict):
                    self.meta.setdefault(key, {}).update(meta)
                    if key in self.watched:
                        self.version += 1
            elif len(rest) == 2 and rest[0] == "meta":
                # legacy-формат: /meta/units, /meta/type, /meta/error, /meta/max
                self.meta.setdefault(key, {})[rest[1]] = payload
                if key in self.watched:
                    self.version += 1

    def snapshot(self, key):
        """Всё, что известно про канал, одним словарём."""
        with self.lock:
            raw = self.values.get(key)
            meta = dict(self.meta.get(key, {}))
            ts = self.stamps.get(key, 0)
        return {
            "key": key,
            "raw": raw,
            "meta": meta,
            "ts": ts,
            "known": raw is not None,
            "error": bool(str(meta.get("error", "")).strip()),
            "units": meta.get("units", ""),
            "type": meta.get("type", ""),
        }


def to_float(raw, default=None):
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


# ==========================================================================
#  MQTT-RPC клиент (протокол: github.com/wirenboard/mqtt-rpc)
# ==========================================================================

class MqttRpc:
    """
    Запрос:  /rpc/v1/<driver>/<service>/<method>/<client_id>
    Ответ:   тот же топик + /reply

    Подписка на reply делается ОДИН раз при коннекте - иначе асинхронный
    subscribe в paho успевает не отработать до прихода ответа.
    """

    def __init__(self, client, client_id):
        self.client = client
        self.client_id = client_id
        self._ids = itertools.count(1)
        self._pending = {}
        self._lock = threading.Lock()

    def reply_topic_filter(self):
        return "/rpc/v1/+/+/+/%s/reply" % self.client_id

    def on_reply(self, payload):
        try:
            resp = json.loads(payload)
        except ValueError:
            return
        rid = str(resp.get("id"))
        with self._lock:
            slot = self._pending.get(rid)
        if slot:
            slot[1] = resp
            slot[0].set()

    def call(self, driver, service, method, params, timeout=10):
        # id по спецификации - строка с десятичным 64-битным числом
        rid = str(next(self._ids))
        ev = threading.Event()
        with self._lock:
            self._pending[rid] = [ev, None]
        topic = "/rpc/v1/%s/%s/%s/%s" % (driver, service, method, self.client_id)
        self.client.publish(topic, json.dumps({"id": rid, "params": params}))
        ok = ev.wait(timeout)
        with self._lock:
            slot = self._pending.pop(rid, None)
        if not ok:
            raise TimeoutError("RPC %s/%s/%s не ответил за %ss" % (driver, service, method, timeout))
        resp = slot[1]
        if resp.get("error"):
            err = resp["error"]
            raise RuntimeError("RPC %s: %s (code %s)" % (method, err.get("message"), err.get("code")))
        return resp.get("result")


# ==========================================================================
#  История для графиков
# ==========================================================================

class SqliteBackend:
    """
    Прямое чтение базы wb-mqtt-db (только на чтение).

    Схема между версиями отличается, поэтому имена таблиц и колонок не
    зашиты. Поддержаны обе известные раскладки:

      плоская (актуальная на wb-2602):
        channels(int_id, device, control, precision)
        data(uid, channel -> channels.int_id, value, timestamp, max, min, retained)

      со справочником устройств (старая):
        devices(int_id, device)
        channels(int_id, device_id -> devices.int_id, control)
        data(..., channel -> channels.int_id, ...)

    Метки времени распознаются автоматически: секунды, миллисекунды или
    julianday.
    """

    VALUE_COLS = ("value",)
    TS_COLS = ("timestamp", "ts", "time")
    CH_REF_COLS = ("channel", "channel_id", "channel_int_id")
    CONTROL_COLS = ("control", "control_name")
    DEVICE_COLS = ("device", "device_name", "device_id")
    ID_COLS = ("int_id", "id")

    def __init__(self, path):
        self.path = path
        self.map = None
        self.error = None
        self.ts_scale = 1.0

    def connect(self):
        return sqlite3.connect("file:%s?mode=ro" % self.path, uri=True, timeout=5.0)

    @staticmethod
    def _pick(cols, candidates):
        low = {c.lower(): c for c in cols}
        for cand in candidates:
            if cand in low:
                return low[cand]
        return None

    def introspect(self, force=False):
        if self.map is not None and not force:
            return self.map
        if not os.path.exists(self.path):
            self.error = "нет файла %s" % self.path
            return None
        try:
            con = self.connect()
        except sqlite3.Error as exc:
            self.error = "не открывается: %s" % exc
            return None
        try:
            tables = {}
            for (name,) in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"):
                if name.startswith("sqlite_"):
                    continue
                tables[name] = [r[1] for r in con.execute('PRAGMA table_info("%s")' % name)]

            # --- таблица значений ---
            data_tbl = ch_ref = val_col = ts_col = None
            for name, cols in tables.items():
                v = self._pick(cols, self.VALUE_COLS)
                t = self._pick(cols, self.TS_COLS)
                c = self._pick(cols, self.CH_REF_COLS)
                if v and t and c:
                    data_tbl, val_col, ts_col, ch_ref = name, v, t, c
                    break
            if not data_tbl:
                self.error = "не нашёл таблицу значений; таблицы: %s" % ", ".join(sorted(tables))
                return None

            # --- таблица каналов: есть контрол и что-то про устройство ---
            ch_tbl = ch_id = ch_name = ch_dev = None
            for name, cols in tables.items():
                if name == data_tbl:
                    continue
                nm = self._pick(cols, self.CONTROL_COLS)
                dv = self._pick(cols, self.DEVICE_COLS)
                if nm and dv:
                    ch_tbl, ch_name, ch_dev = name, nm, dv
                    ch_id = self._pick(cols, self.ID_COLS) or "rowid"
                    break
            if not ch_tbl:
                self.error = ("не нашёл таблицу каналов (нужны колонки control и device); "
                              "таблицы: %s" % ", ".join(sorted(tables)))
                return None

            # --- плоская схема или ссылка на справочник? ---
            # Решает содержимое: строка «wb-msw-v4_149» - плоская,
            # число - ссылка на devices.
            flat = True
            row = con.execute('SELECT "%s" FROM "%s" LIMIT 1' % (ch_dev, ch_tbl)).fetchone()
            if row is not None and row[0] is not None:
                sample = row[0]
                if isinstance(sample, int):
                    flat = False
                elif isinstance(sample, str) and sample.isdigit():
                    flat = False

            dev_tbl = dev_id = dev_name = None
            if not flat:
                for name, cols in tables.items():
                    if name in (data_tbl, ch_tbl):
                        continue
                    nm = self._pick(cols, ("device", "device_name", "name"))
                    idc = self._pick(cols, self.ID_COLS)
                    if nm and idc:
                        dev_tbl, dev_name, dev_id = name, nm, idc
                        break
                if not dev_tbl:
                    # ссылка есть, а справочника нет - работаем как с плоской
                    flat = True

            # --- секунды, миллисекунды или julianday ---
            top = con.execute('SELECT MAX("%s") FROM "%s"' % (ts_col, data_tbl)).fetchone()
            top = float((top or [0])[0] or 0)
            if top > 1e11:
                self.ts_scale = 1e-3
            elif 0 < top < 1e7:
                self.ts_scale = None
            else:
                self.ts_scale = 1.0

            self.map = {
                "data": data_tbl, "value": val_col, "ts": ts_col, "ch_ref": ch_ref,
                "channels": ch_tbl, "ch_id": ch_id, "ch_name": ch_name, "ch_dev": ch_dev,
                "flat": flat,
                "devices": dev_tbl, "dev_id": dev_id, "dev_name": dev_name,
                "ts_kind": {1e-3: "мс", 1.0: "с"}.get(self.ts_scale, "julianday"),
            }
            self.error = None
            return self.map
        except sqlite3.Error as exc:
            self.error = "ошибка SQL: %s" % exc
            return None
        finally:
            con.close()

    # ---- сборка SQL под обе раскладки ----
    def _from_join(self, m):
        if m["flat"]:
            return ('FROM "%(data)s" v JOIN "%(ch)s" c ON c."%(cid)s" = v."%(cref)s"'
                    % {"data": m["data"], "ch": m["channels"],
                       "cid": m["ch_id"], "cref": m["ch_ref"]})
        return ('FROM "%(data)s" v '
                'JOIN "%(ch)s" c ON c."%(cid)s" = v."%(cref)s" '
                'JOIN "%(dev)s" d ON d."%(did)s" = c."%(cdev)s"'
                % {"data": m["data"], "ch": m["channels"], "cid": m["ch_id"],
                   "cref": m["ch_ref"], "dev": m["devices"], "did": m["dev_id"],
                   "cdev": m["ch_dev"]})

    def _dev_expr(self, m):
        return ('c."%s"' % m["ch_dev"]) if m["flat"] else ('d."%s"' % m["dev_name"])

    def _ts_expr(self, m, alias="v"):
        col = '%s."%s"' % (alias, m["ts"])
        if self.ts_scale is None:
            return "(%s - 2440587.5) * 86400.0" % col
        return "%s * %r" % (col, self.ts_scale)

    def _ts_bound(self, seconds):
        if self.ts_scale is None:
            return seconds / 86400.0 + 2440587.5
        return seconds / self.ts_scale

    def channels(self):
        m = self.introspect()
        if not m:
            return {}
        sql = ('SELECT %(dev)s || \'/\' || c."%(cn)s", COUNT(*) %(join)s GROUP BY 1'
               % {"dev": self._dev_expr(m), "cn": m["ch_name"], "join": self._from_join(m)})
        con = self.connect()
        try:
            return {row[0]: row[1] for row in con.execute(sql)}
        except sqlite3.Error as exc:
            self.error = "ошибка SQL: %s" % exc
            return {}
        finally:
            con.close()

    def values(self, channel, span, points):
        m = self.introspect()
        if not m or "/" not in channel:
            return []
        device, control = channel.split("/", 1)
        now = time.time()
        bucket = max(1.0, float(span) / max(points, 1))
        sql = ('SELECT CAST((%(tsx)s) / %(bk)r AS INTEGER) AS b, '
               '       AVG(CAST(v."%(val)s" AS REAL)), AVG(%(tsx)s) '
               '%(join)s '
               'WHERE %(dev)s = ? AND c."%(cn)s" = ? '
               '  AND v."%(ts)s" > ? AND v."%(ts)s" <= ? '
               'GROUP BY b ORDER BY b'
               % {"tsx": self._ts_expr(m), "bk": bucket, "val": m["value"],
                  "join": self._from_join(m), "dev": self._dev_expr(m),
                  "cn": m["ch_name"], "ts": m["ts"]})
        con = self.connect()
        try:
            rows = con.execute(sql, (device, control,
                                     self._ts_bound(now - span),
                                     self._ts_bound(now))).fetchall()
            return [(float(r[2]), float(r[1])) for r in rows if r[1] is not None]
        except sqlite3.Error as exc:
            self.error = "ошибка SQL: %s" % exc
            return []
        finally:
            con.close()


class History:
    """
    Источник данных для графиков.

    По умолчанию читает SQLite напрямую - это надёжнее и быстрее, чем RPC:
    не зависит от того, отвечает ли db_logger, и усреднение делает SQL.
    RPC остаётся запасным вариантом, если файл недоступен.
    """

    def __init__(self, rpc, db_path=DB_PATH, ttl=60, prefer="auto"):
        self.rpc = rpc
        self.ttl = ttl
        self.prefer = prefer
        self.sqlite = SqliteBackend(db_path)
        self.mode = None            # 'sqlite' | 'rpc'
        self._cache = {}
        self._lock = threading.Lock()

    def pick_mode(self):
        if self.mode:
            return self.mode
        if self.prefer == "rpc":
            self.mode = "rpc"
        elif self.prefer == "sqlite":
            self.mode = "sqlite"
        elif self.sqlite.introspect():
            self.mode = "sqlite"
        else:
            log.warning("SQLite недоступен (%s), перехожу на MQTT-RPC", self.sqlite.error)
            self.mode = "rpc"
        log.info("история: режим %s", self.mode)
        return self.mode

    # ---- чтение из кэша (никогда не блокирует HTTP) ----
    def get(self, channel, span, points):
        with self._lock:
            hit = self._cache.get((channel, span, points))
        return hit[1] if hit else []

    def channels_with_history(self):
        with self._lock:
            hit = self._cache.get("__channels__")
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        found = {}
        if self.pick_mode() == "sqlite":
            found = self.sqlite.channels()
        else:
            try:
                result = self.rpc.call("db_logger", "history", "get_channels", {}, timeout=15)
                rows = result.get("channels", result) if isinstance(result, dict) else result
                for row in rows or []:
                    if isinstance(row, dict):
                        dev = row.get("device") or row.get("d")
                        ctl = row.get("control") or row.get("c")
                        cnt = row.get("items") or row.get("count") or row.get("n")
                    elif isinstance(row, (list, tuple)) and len(row) >= 2:
                        dev, ctl, cnt = row[0], row[1], None
                    else:
                        continue
                    if dev and ctl:
                        found["%s/%s" % (dev, ctl)] = cnt
            except Exception as exc:                  # noqa: BLE001
                log.warning("get_channels: %s", exc)
                return {}
        with self._lock:
            self._cache["__channels__"] = (time.time(), found)
        return found

    # ---- обновление кэша (фоновый поток) ----
    def refresh(self, channel, span, points):
        if self.pick_mode() == "sqlite":
            series = self.sqlite.values(channel, span, points)
            if not series and self.sqlite.error:
                log.warning("история %s: %s", channel, self.sqlite.error)
        else:
            series = self._refresh_rpc(channel, span, points)
        if series:
            with self._lock:
                self._cache[(channel, span, points)] = (time.time(), series)

    def _refresh_rpc(self, channel, span, points):
        if "/" not in channel:
            return []
        device, control = channel.split("/", 1)
        now = int(time.time())
        try:
            result = self.rpc.call("db_logger", "history", "get_values", {
                "ver": 1,
                "channels": [[device, control]],
                "timestamp": {"gt": now - span, "lt": now},
                "max_records": points,
            }, timeout=20)
        except Exception as exc:                      # noqa: BLE001
            log.warning("история %s: %s", channel, exc)
            return []
        series = []
        for row in (result or {}).get("values", []):
            value, stamp = row.get("v"), row.get("t")
            if value is None or stamp is None:
                continue
            try:
                series.append((float(stamp), float(value)))
            except (TypeError, ValueError):
                continue
        series.sort()
        return series

    def prefetch_loop(self, config, stop_event):
        """Греем кэш в фоне, чтобы HTTP-запрос никогда не ждал источник."""
        while not stop_event.is_set():
            try:
                config.reload()
                state.set_watched(config.used_channels())
                for channel, span, points in config.chart_series():
                    if stop_event.is_set():
                        break
                    self.refresh(channel, span, points)
            except Exception as exc:                  # noqa: BLE001
                log.exception("сбой префетча истории: %s", exc)
            stop_event.wait(self.ttl)


# ==========================================================================
#  Рисовалки: цвет, кривые, раскладка
# ==========================================================================

CCT_STOPS = [
    (2000, (255, 152, 66)),
    (2700, (255, 179, 106)),
    (3200, (255, 199, 143)),
    (4000, (255, 221, 186)),
    (5000, (255, 240, 224)),
    (6500, (216, 232, 255)),
]


# Для поля плитки нужны более звонкие цвета, чем физически точные:
# на серой подложке реалистичный «холодный белый» выглядит просто серым.
FIELD_STOPS = [
    (2000, (255, 138, 43)),
    (2700, (255, 168, 84)),
    (3200, (255, 196, 130)),
    (4000, (243, 219, 188)),
    (5000, (176, 209, 247)),
    (6500, (86, 170, 255)),
]


def _interp(stops, kelvin):
    kelvin = max(stops[0][0], min(stops[-1][0], float(kelvin)))
    for i in range(len(stops) - 1):
        k0, c0 = stops[i]
        k1, c1 = stops[i + 1]
        if k0 <= kelvin <= k1:
            f = (kelvin - k0) / (k1 - k0)
            rgb = tuple(int(round(c0[j] + (c1[j] - c0[j]) * f)) for j in range(3))
            return "#%02X%02X%02X" % rgb
    return "#FFFFFF"


def field_color(kelvin):
    """Цвет для поля плитки ленты - насыщеннее реалистичного."""
    return _interp(FIELD_STOPS, kelvin)


def cct_color(kelvin):
    """Цветовая температура в Кельвинах -> hex. Кусочно-линейная интерполяция."""
    kelvin = max(CCT_STOPS[0][0], min(CCT_STOPS[-1][0], float(kelvin)))
    for i in range(len(CCT_STOPS) - 1):
        k0, c0 = CCT_STOPS[i]
        k1, c1 = CCT_STOPS[i + 1]
        if k0 <= kelvin <= k1:
            f = (kelvin - k0) / (k1 - k0)
            rgb = tuple(int(round(c0[j] + (c1[j] - c0[j]) * f)) for j in range(3))
            return "#%02X%02X%02X" % rgb
    return "#FFFFFF"


def nice_step(lo, hi, target=4):
    """Шаг сетки «по-человечески»: 1, 2, 5, 10, 20, 50 …"""
    span = max(hi - lo, 1e-9)
    raw = span / max(target, 1)
    power = 10.0 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= power * mult:
            return power * mult
    return power * 10


def edge_anchor(x, width, margin=14.0):
    """
    Куда прижать подпись у самого края плитки.

    По центру подпись «21» на x=3 наполовину уезжает за рамку и сливается
    с подписью шкалы, которая стоит там же слева.
    """
    if x < margin:
        return (4.0, "start")
    if x > width - margin:
        return (width - 4.0, "end")
    return (x, "middle")


def label_digits(step):
    """
    Сколько знаков после запятой в подписи шкалы.

    Считаем по ШАГУ, а не по размаху: при шаге 2.5 подписи «15, 18, 20, 22»
    врут - на деле там 15, 17.5, 20, 22.5.
    """
    if step >= 1 and abs(step - round(step)) < 1e-9:
        return 0
    return 1 if step >= 0.1 else 2


def grid_bounds(values, step=None, unit="", divisions=None, target=4):
    """
    Границы шкалы, выровненные по сетке.

    Возвращает (lo, hi, step, число делений).

    Шаг по умолчанию считается ОТ РАЗМАХА ДАННЫХ, а не от единиц измерения.
    Жёсткие 5 °C и 10 % выглядят разумно на бумаге, но комнатная температура
    за 12 часов гуляет на полтора градуса - при шаге 5 шкала растягивается
    втрое шире данных, и кривая превращается в плоскую нитку. Нужен жёсткий
    шаг - задайте `grid` у серии; нужны фиксированные границы - `min`/`max`.
    """
    if not values:
        return (0.0, 1.0, 1.0, 1)
    vmin, vmax = min(values), max(values)
    if step is None:
        step = nice_step(vmin, vmax, target=target)
    lo = math.floor(vmin / step) * step
    hi = math.ceil(vmax / step) * step
    if hi - lo < step * 0.5:
        hi = lo + step
    n = int(round((hi - lo) / step))
    n = max(n, 1)
    # растягиваем до нужного числа делений, чтобы линии разных серий совпали
    if divisions and divisions > n:
        while n < divisions:
            hi += step
            n += 1
            if n < divisions:
                lo -= step
                n += 1
    return (lo, hi, step, n)


def smooth_path(points, width, height, band=BAND, pad_x=3.0,
                lo=None, hi=None):
    """
    Плавная кривая по [(ts, value)] ВО ВСЮ ширину плитки (от рамки до рамки).

    Сама кривая живёт в вертикальной полосе band (доли высоты плитки), чтобы
    не лезть на подписи, а заливка под ней уходит до самого низа - «до пола».
    """
    if len(points) < 2:
        return None
    stamps = [p[0] for p in points]
    values = [p[1] for p in points]
    t0, t1 = stamps[0], stamps[-1]
    if t1 <= t0:
        return None
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    if hi - lo < 1e-9:
        lo -= 0.5
        hi += 0.5

    y_top = height * band[0]
    y_bot = height * band[1]

    def px(t):
        return pad_x + (t - t0) / (t1 - t0) * (width - 2 * pad_x)

    def py(v):
        return y_bot - (v - lo) / (hi - lo) * (y_bot - y_top)

    xy = [(px(t), py(v)) for t, v in points]
    d = "M %.1f,%.1f" % xy[0]
    for i in range(1, len(xy)):
        x0, y0 = xy[i - 1]
        x1, y1 = xy[i]
        mx = (x0 + x1) / 2.0
        d += " C %.1f,%.1f %.1f,%.1f %.1f,%.1f" % (mx, y0, mx, y1, x1, y1)
    # заливка замыкается по нижней кромке плитки, а не по низу графика
    area = d + " L %.1f,%.1f L %.1f,%.1f Z" % (xy[-1][0], height, xy[0][0], height)
    return {"line": d, "area": area, "lo": lo, "hi": hi,
            "last": xy[-1], "first": xy[0]}


def darken(hex_color, factor=0.72):
    """Тот же оттенок, но темнее - чтобы цифра читалась на светлой подложке."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c * factor)))) for c in rgb)


def hour_ticks(t0, t1, width, pad_x=0.0, max_lines=13):
    """
    Границы часов внутри окна графика -> список x в пикселях.

    Шаг подбирается так, чтобы линий было не больше max_lines: для 12 часов
    это каждый час, для суток - каждые два, для недели - каждые 12.
    """
    if t1 <= t0:
        return []
    span_h = (t1 - t0) / 3600.0
    step_h = 1
    for candidate in (1, 2, 3, 6, 12, 24, 48):
        if span_h / candidate <= max_lines:
            step_h = candidate
            break
    else:
        step_h = 48

    # ближайшая вниз граница часа по локальному времени
    lt = time.localtime(t0)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour,
                        0, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst))
    step = step_h * 3600
    out = []
    stamp = base
    while stamp <= t1 + step:
        if stamp > t0:
            hour = time.localtime(stamp).tm_hour
            if hour % step_h == 0:
                x = pad_x + (stamp - t0) / (t1 - t0) * (width - 2 * pad_x)
                if 2 < x < width - 2:
                    out.append({"x": round(x, 1), "hour": hour,
                                "major": hour % 6 == 0})
        stamp += 3600
    return out


def cells(value, default=1.0):
    """Размер из конфига (в плитках) -> число мелких ячеек. 1 -> 2, 0.5 -> 1."""
    try:
        n = int(round(float(value if value is not None else default) * 2))
    except (TypeError, ValueError):
        n = int(default * 2)
    return max(1, n)


def layout(tiles, cols):
    """
    Раскладка плиток разного размера. Размеры в конфиге задаются в плитках
    (1 - обычная, 2 - двойная, 0.5 - мелкая), внутри всё считается в мелких
    ячейках: cols тоже приходит уже в них.

    Ищем первое свободное место сверху вниз, слева направо.
    """
    occupied = set()
    placed = []
    for tile in tiles:
        w = min(cells(tile.get("w"), 1.0), cols)
        h = cells(tile.get("h"), 1.0)
        row = 0
        while True:
            spot = None
            for col in range(cols - w + 1):
                if all((row + dr, col + dc) not in occupied
                       for dr in range(h) for dc in range(w)):
                    spot = col
                    break
            if spot is not None:
                for dr in range(h):
                    for dc in range(w):
                        occupied.add((row + dr, spot + dc))
                placed.append((tile, row, spot, w, h))
                break
            row += 1
    rows = max((r + h for _, r, _, _, h in placed), default=0)
    return placed, rows


# ==========================================================================
#  Подготовка плиток: превращаем конфиг + состояние в готовые к отрисовке числа
# ==========================================================================

STALE_AFTER = 300  # секунд без обновления - канал считается протухшим


def _fmt(value, digits=1):
    if value is None:
        return "--"
    if abs(value - round(value)) < 1e-9:
        return "%d" % round(value)
    return ("%%.%df" % digits) % value


def build_chart(tile, state, history):
    span = parse_duration(tile.get("range", "24h"))
    points = int(tile.get("points", 90))
    specs = [s for s in (tile.get("series", []) or []) if s.get("channel")]

    # 1. сначала забираем все ряды, чтобы посчитать общую сетку
    fetched = []
    for spec in specs:
        snap = state.snapshot(spec["channel"])
        data = history.get(spec["channel"], span, points)
        fetched.append((spec, snap, data))

    # 2. шкала каждого ряда выравнивается по своему шагу (°C - 5, % - 10),
    #    а число делений берём общее: тогда горизонтальные линии совпадут
    scales = []
    for spec, snap, data in fetched:
        values = [v for _, v in data]
        if spec.get("min") is not None and spec.get("max") is not None:
            lo, hi = float(spec["min"]), float(spec["max"])
            step = float(spec.get("grid") or nice_step(lo, hi))
            n = max(int(round((hi - lo) / step)), 1)
            scales.append((lo, hi, step, n))
        else:
            scales.append(grid_bounds(values, spec.get("grid"),
                                      spec.get("unit", snap["units"])))
    divisions = max([s[3] for s in scales], default=0)
    divisions = min(max(divisions, 2), 6)          # 2..6 линий - читаемо

    ticks = []
    series_out = []
    for idx, ((spec, snap, data), scale) in enumerate(zip(fetched, scales)):
        values = [v for _, v in data]
        if spec.get("min") is not None and spec.get("max") is not None:
            lo, hi, gstep = scale[0], scale[1], scale[2]
        else:
            lo, hi, gstep, _n = grid_bounds(values, spec.get("grid"),
                                            spec.get("unit", snap["units"]),
                                            divisions=divisions)
        if data and not ticks:
            ticks = hour_ticks(data[0][0], data[-1][0], tile["inner_w"], pad_x=3.0)
        raw = to_float(snap["raw"])
        geom = smooth_path(data, tile["inner_w"], tile["chart_h"], lo=lo, hi=hi)
        color = spec.get("color", ["#E08A2D", "#3B93C4", "#8E8E93"][idx % 3])
        # Пороги: цвет линии по текущему значению. Задаются от меньшего к
        # большему, побеждает последний подошедший.
        for rule in (spec.get("thresholds") or []):
            try:
                if raw is not None and raw >= float(rule["above"]):
                    color = rule.get("color", color)
            except (KeyError, TypeError, ValueError):
                continue
        series_out.append({
            "color": color,
            "text_color": spec.get("text_color") or darken(color),
            "unit": spec.get("unit", snap["units"]),
            "label": spec.get("label", ""),
            "value": _fmt(raw, int(spec.get("digits", 1))),
            "has_value": raw is not None,
            "geom": geom,
            "step": gstep,
            "npoints": len(data),
        })

    # 3. сами линии: одинаковые доли высоты для всех рядов
    hlines = []
    if any(s["geom"] for s in series_out):
        y_top = tile["chart_h"] * BAND[0]
        y_bot = tile["chart_h"] * BAND[1]
        for i in range(divisions + 1):
            f = i / float(divisions)
            hlines.append({"y": round(y_bot - f * (y_bot - y_top), 1),
                           "f": f, "edge": i in (0, divisions)})

    # 4. подписи: без них не понять, что шкала растянута под сами данные -
    #    провал на полградуса выглядит как обвал
    with_time = bool(tile.get("time_labels", False))
    for s in series_out:
        s["labels"] = []
        if not s["geom"]:
            continue
        lo, hi = s["geom"]["lo"], s["geom"]["hi"]
        digits = label_digits(s.get("step") or ((hi - lo) / max(len(hlines) - 1, 1)))
        for hl in hlines:
            # Нижнюю границу показываем всегда: без неё непонятно, от чего
            # отсчитывается шкала. Раньше её прятали ради строки часов -
            # теперь вместо этого убираются крайние подписи часов, они и
            # были причиной наложения.
            s["labels"].append({"y": hl["y"],
                                "text": _fmt(lo + hl["f"] * (hi - lo), digits)})
        s["span"] = "%s–%s %s" % (_fmt(lo, digits), _fmt(hi, digits), s["unit"])

    # Часы. Крайние подписи прижимаются к самым краям плитки - туда же, где
    # стоят подписи шкал слева и справа. Пока шкал нет, это нормально; когда
    # есть, крайние часы накрывают границы шкалы, и понять её невозможно.
    # Часов на графике много, потеря двух крайних ничего не стоит.
    with_grid = bool(tile.get("grid_labels", False))
    times = []
    for tk in ticks:
        if not tk["major"]:
            continue
        lx, anchor = edge_anchor(tk["x"], tile["inner_w"])
        if with_grid and anchor != "middle":
            continue
        times.append({"x": round(lx, 1), "anchor": anchor,
                      "text": "%02d:00" % tk["hour"]})

    note = ""
    if series_out and series_out[0].get("span"):
        note = series_out[0]["span"]

    return {"series": series_out, "ticks": ticks, "hlines": hlines,
            "times": times, "scale_note": note,
            "title_dx": round(20 + text_width(tile.get("title", ""), 15) + 9, 1),
            "grid_labels": bool(tile.get("grid_labels", False)),
            "time_labels": bool(tile.get("time_labels", False)),
            "show_span": bool(tile.get("show_span", False)),
            "range_label": tile.get("range", "24h")}


def build_switch(tile, state):
    snap = state.snapshot(tile.get("channel", ""))
    raw = (snap["raw"] or "").strip()
    on = raw not in ("", "0", "false", "False")

    # invert - для нормально замкнутых контактов: реле обесточено, а нагрузка
    # включена. Единица в канале означает "выключено". Инвертируем только
    # известное значение: нет данных - значит нет данных, а не "включено".
    if tile.get("invert") and raw != "":
        on = not on

    return {
        "on": on,
        "status": tile.get("on_text", "Вкл") if on else tile.get("off_text", "Выкл"),
        "icon": tile.get("icon", "toggle"),
        "short": tile.get("badge", ""),
        "snap": snap,
    }


def build_curtain(tile, state):
    """
    Раздвижные шторы. Две створки едут от краёв к центру.
    open_pct = 100 - полностью раздвинуты, 0 - закрыты.
    Если заданы channel_left/channel_right - створки независимые.
    """
    snaps = []
    if tile.get("channel_left") or tile.get("channel_right"):
        left_snap = state.snapshot(tile.get("channel_left", ""))
        right_snap = state.snapshot(tile.get("channel_right", ""))
        left = to_float(left_snap["raw"])
        right = to_float(right_snap["raw"])
        snaps = [left_snap, right_snap]
    else:
        snap = state.snapshot(tile.get("channel", ""))
        left = right = to_float(snap["raw"])
        snaps = [snap]

    if tile.get("inverted"):
        left = None if left is None else 100 - left
        right = None if right is None else 100 - right

    avg = [v for v in (left, right) if v is not None]
    avg = sum(avg) / len(avg) if avg else None

    # Необязательный канал состояния (у HomeKit-совместимых приводов
    # PositionState: 0 - закрывается, 1 - открывается, 2 - стоит).
    moving = None
    if tile.get("channel_state"):
        st_snap = state.snapshot(tile["channel_state"])
        snaps.append(st_snap)
        code = to_float(st_snap["raw"])
        if code is not None and int(code) in (0, 1):
            moving = "Закрывается" if int(code) == 0 else "Открывается"

    return {
        "moving": moving,
        "left_open": 0.0 if left is None else max(0.0, min(100.0, left)) / 100.0,
        "right_open": 0.0 if right is None else max(0.0, min(100.0, right)) / 100.0,
        "known": avg is not None,
        "on": bool(avg and avg > 1),
        "status": moving or (("Открыто %d%%" % round(avg)) if avg is not None
                             else "Нет данных"),
        "short": ("%d %%" % round(avg)) if avg is not None else "",
        "snaps": snaps,
    }


def build_light(tile, state):
    """
    Светодиодная лента: три величины на одной плитке.

      по горизонтали  - цветовая температура: слева холодный, справа тёплый
      по вертикали    - яркость: сверху ярко, снизу темно
      точка           - фактическое положение по обеим осям
      оранжевая рамка - включено

    Плитка целиком залита этим полем, поэтому её вид сам по себе читается
    как «тёплый приглушённый» или «холодный яркий», а точка даёт точное
    значение.
    """
    b_snap = state.snapshot(tile.get("channel_brightness", ""))
    t_snap = state.snapshot(tile.get("channel_temp", ""))
    snaps = [b_snap, t_snap]

    brightness = to_float(b_snap["raw"])
    b_max = to_float(b_snap["meta"].get("max"), 100.0) or 100.0
    if brightness is not None and b_max:
        brightness = max(0.0, min(100.0, brightness / b_max * 100.0))

    on = brightness is not None and brightness > 0
    if tile.get("channel_switch"):
        s_snap = state.snapshot(tile["channel_switch"])
        snaps.append(s_snap)
        raw = (s_snap["raw"] or "").strip()
        on = raw not in ("", "0", "false", "False")

    k_min = float(tile.get("temp_min", 2700))
    k_max = float(tile.get("temp_max", 6500))
    temp_raw = to_float(t_snap["raw"])
    kelvin = None
    if temp_raw is not None:
        if str(tile.get("temp_unit", "kelvin")).lower() == "percent":
            t_max = to_float(t_snap["meta"].get("max"), 100.0) or 100.0
            kelvin = k_min + (k_max - k_min) * max(0.0, min(1.0, temp_raw / t_max))
        else:
            kelvin = temp_raw

    # положение точки: 0 слева/сверху, 1 справа/снизу
    if kelvin is None:
        dot_x = None
    else:
        # слева холодный (k_max), справа тёплый (k_min)
        dot_x = (k_max - max(k_min, min(k_max, kelvin))) / max(k_max - k_min, 1.0)
    dot_y = None if brightness is None else 1.0 - brightness / 100.0

    bits = []
    if brightness is not None:
        bits.append("%d %%" % round(brightness))
    if kelvin is not None:
        bits.append("%d K" % round(kelvin))
    return {
        "on": on,
        "dot_x": dot_x,
        "dot_y": dot_y,
        "has_dot": dot_x is not None and dot_y is not None,
        "cold": tile.get("cold_color") or field_color(k_max),
        "warm": tile.get("warm_color") or field_color(k_min),
        "color": field_color(kelvin if kelvin is not None else 3000),
        "short": " · ".join(bits),
        # на узкой плитке (w: 1) полная подпись не влезает
        "short_min": bits[0] if bits else "",
        "status": ("Включено" if on else "Выключено") if brightness is not None
                  else "Нет данных",
        "snaps": snaps,
    }


def build_dimmer(tile, state):
    """
    Диммируемый светильник: заполнение плитки снизу вверх на высоту яркости.

    В отличие от ленты здесь одна величина, поэтому не поле, а простая
    заливка одним цветом. Цвет по умолчанию - приглушённый тёплый; для
    холодного света поставьте свой в `color`.
    """
    b_snap = state.snapshot(tile.get("channel_brightness", ""))
    snaps = [b_snap]

    brightness = to_float(b_snap["raw"])
    b_max = to_float(b_snap["meta"].get("max"), 100.0) or 100.0
    if brightness is not None and b_max:
        brightness = max(0.0, min(100.0, brightness / b_max * 100.0))

    on = brightness is not None and brightness > 0
    if tile.get("channel"):
        s_snap = state.snapshot(tile["channel"])
        snaps.append(s_snap)
        raw = (s_snap["raw"] or "").strip()
        on = raw not in ("", "0", "false", "False")

    return {
        "on": on,
        "fill": 0.0 if (not on or brightness is None) else brightness / 100.0,
        "color": tile.get("color", "#E0A050"),
        "short": ("%d %%" % round(brightness)) if brightness is not None else "",
        "status": ("Включено" if on else "Выключено") if brightness is not None
                  else "Нет данных",
        "snaps": snaps,
    }


# Доли от кегля: у SF Pro / Segoe цифры моноширинные, знаки уже.
CHAR_W = {".": 0.28, ",": 0.28, "-": 0.36, "\u2212": 0.36, "\u00B0": 0.42, " ": 0.28}


def text_width(text, size, default=0.56):
    """Грубая ширина строки в px. Точности хватает, чтобы выровнять число."""
    return size * sum(CHAR_W.get(ch, default) for ch in str(text))


# Разрыв внизу увеличен со 90 до 116 градусов: строка «сейчас 25.5°» шире,
# чем казалось на макетах, и почти касалась дуги. Заодно круг стал крупнее,
# а нижняя подпись перестала выглядеть оторванной от него.
DIAL_START = 148.0   # градусы: нижний левый край дуги
DIAL_SWEEP = 244.0


def dial_point(cx, cy, r, deg):
    rad = math.radians(deg)
    return (cx + r * math.cos(rad), cy + r * math.sin(rad))


def dial_arc(cx, cy, r, deg_from, deg_to):
    """Дуга окружности как path d. Пустая строка, если сектор вырожденный."""
    if deg_to - deg_from < 0.35:
        return ""
    x0, y0 = dial_point(cx, cy, r, deg_from)
    x1, y1 = dial_point(cx, cy, r, deg_to)
    large = 1 if (deg_to - deg_from) > 180 else 0
    return "M %.2f,%.2f A %.2f,%.2f 0 %d 1 %.2f,%.2f" % (x0, y0, r, r, large, x1, y1)


def build_dial(width, height, lo, hi, current, target):
    """
    Циферблат термостата: незамкнутая дуга со шкалой lo…hi.

    Закрашен участок МЕЖДУ фактической температурой и уставкой - то есть
    ровно тот разрыв, который прибору предстоит закрыть. Две засечки на
    дуге показывают обе границы, снизу в разрыве дуги остаётся место
    под режим работы.
    """
    span = max(float(hi) - float(lo), 1e-6)
    cx = width / 2.0
    # Центр опущен, радиус увеличен: так нижние концы дуги подходят близко
    # к строке режима и она перестаёт выглядеть отдельной от циферблата.
    cy = height * 0.59
    r = min(width, height) * 0.36

    def deg(value):
        f = (float(value) - float(lo)) / span
        return DIAL_START + max(0.0, min(1.0, f)) * DIAL_SWEEP

    out = {
        "cx": round(cx, 1), "cy": round(cy, 1), "r": round(r, 1),
        "track": dial_arc(cx, cy, r, DIAL_START, DIAL_START + DIAL_SWEEP),
        "fill": "", "marks": [],
    }

    pts = [v for v in (current, target) if v is not None]
    if len(pts) == 2:
        a, b = sorted(deg(v) for v in pts)
        out["fill"] = dial_arc(cx, cy, r, a, b)
    elif len(pts) == 1:
        a = deg(pts[0])
        out["fill"] = dial_arc(cx, cy, r, DIAL_START, a)

    for value, kind in ((current, "current"), (target, "target")):
        if value is None:
            continue
        d = deg(value)
        inner = 0.80 if kind == "target" else 0.86
        x0, y0 = dial_point(cx, cy, r * inner, d)
        x1, y1 = dial_point(cx, cy, r * 1.20, d)
        out["marks"].append({"kind": kind,
                             "x1": round(x0, 1), "y1": round(y0, 1),
                             "x2": round(x1, 1), "y2": round(y1, 1)})
    return out


def command_topic(channel, explicit=None):
    """
    Топик, в который пишут команду для канала.

    Wiren Board: значение приходит в /devices/<dev>/controls/<ctl>, а команда
    уходит в тот же топик с суффиксом /on - писать в топик состояния нельзя,
    его перетрёт драйвер.

    У сторонних шлюзов соглашение своё, поэтому для сырых топиков команду
    задают явно полем command_topic - угадывать тут нечего.
    """
    if explicit:
        return explicit
    if not channel or channel.count("/") != 1:
        return None
    device, control = channel.split("/", 1)
    return "/devices/%s/controls/%s/on" % (device, control)


# HomeKit: 0 - выключено, 1 - нагрев, 2 - охлаждение, 3 - авто
HEAT_MODE = {0: "Выключен", 1: "Нагрев", 2: "Охлаждение", 3: "Авто"}
# Что показываем, когда прибор включён, но прямо сейчас не работает: уставка
# достигнута и головка закрыта. «Ожидание» звучало двусмысленно - будто
# термостат чего-то ждёт от нас.
HEAT_NOW = {0: "Не греет", 1: "Греет", 2: "Охлаждает"}


def build_thermostat(tile, state):
    """
    Термостат: крупно уставка, в углу фактическая температура.

    Оранжевая рамка загорается, когда прибор РЕАЛЬНО работает
    (CurrentHeatingCoolingState = 1), а не когда просто включён режим
    нагрева - иначе рамка горела бы круглосуточно и ничего не значила.
    """
    cur_snap = state.snapshot(tile.get("channel_current", ""))
    tgt_snap = state.snapshot(tile.get("channel_target", ""))
    snaps = [cur_snap, tgt_snap]

    current = to_float(cur_snap["raw"])
    target = to_float(tgt_snap["raw"])

    working = None
    if tile.get("channel_state"):
        st_snap = state.snapshot(tile["channel_state"])
        snaps.append(st_snap)
        code = to_float(st_snap["raw"])
        working = int(code) if code is not None else None

    mode = None
    if tile.get("channel_mode"):
        md_snap = state.snapshot(tile["channel_mode"])
        snaps.append(md_snap)
        code = to_float(md_snap["raw"])
        mode = int(code) if code is not None else None

    if mode == 0:
        status = HEAT_MODE[0]
    elif working is not None:
        status = HEAT_NOW.get(working, HEAT_MODE.get(mode, ""))
    else:
        status = HEAT_MODE.get(mode, "")

    # насколько далеко до уставки - для полоски прогрева
    reach = None
    if current is not None and target is not None:
        delta = target - current
        reach = max(0.0, min(1.0, 1.0 - abs(delta) / 3.0))

    dial = None
    if tile.get("dial", True):
        dial = build_dial(tile.get("inner_w") or 170, tile.get("chart_h") or 170,
                          tile.get("scale_min", 14), tile.get("scale_max", 30),
                          current, target)

    # Знак градуса не должен участвовать в центрировании, иначе число
    # визуально уезжает влево. Считаем ширину числа и ставим "°" за ним.
    target_num = _fmt(target, int(tile.get("digits", 0))) if target is not None else "--"
    big = 30.0
    deg_dx = text_width(target_num, big) / 2.0 + 1.0

    return {
        "dial": dial,
        "target_num": target_num,
        "deg_dx": round(deg_dx, 1),
        "deg_size": round(big * 0.60, 1),
        "on": working == 1,
        "enabled": mode != 0,
        "target": ("%s°" % target_num) if target is not None else "--",
        "current": ("%s°" % _fmt(current, 1)) if current is not None else "",
        "short": ("%s°" % _fmt(current, 1)) if current is not None else "",
        "reach": reach,
        "status": status or "Нет данных",
        "always_status": True,
        "snaps": snaps,
    }


def forecast_channels(tile):
    """
    Каналы виртуального устройства погоды.

    Скрипт wb-rules публикует их пачкой с общим префиксом, поэтому в конфиге
    достаточно указать device: weather - перечислять полтора десятка имён
    руками не нужно.
    """
    dev = tile.get("device", "weather")
    hours = int(tile.get("hours", 12))
    out = ["%s/%s" % (dev, n) for n in
           ("temperature", "description", "humidity", "pressure",
            "windspeed", "temp_min_12h", "temp_max_12h", "forecast_ok")]
    out += ["%s/temp_%02d" % (dev, i) for i in range(hours)]
    return out


# Коды погоды WMO, которые отдаёт Open-Meteo, - в значки.
# Диапазоны, а не перечисление: коды сгруппированы по десяткам, и внутри
# группы разница только в силе явления, а значок один и тот же.
WMO_ICONS = (
    ((0,), "sun"),
    ((1, 2), "cloud-sun"),
    ((3,), "cloud"),
    ((45, 48), "fog"),
    ((51, 53, 55, 56, 57), "rain"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    ((71, 73, 75, 77, 85, 86), "snow"),
    ((95, 96, 99), "storm"),
)


def wmo_icon(code, day=True):
    if code is None:
        return "sun" if day else "moon"
    code = int(code)
    for codes, name in WMO_ICONS:
        if code in codes:
            if name == "sun" and not day:
                return "moon"
            return name
    return "cloud"


def parse_series(raw, limit=24):
    """'13.3,13.2,...' -> [13.3, 13.2, ...]. Пропуски становятся None."""
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            out.append(None)
            continue
        out.append(to_float(part))
    return out[:limit]


def parse_iso_hour(raw):
    """'2026-08-20T03:00' -> 3. Нужен, чтобы подписать часы от начала прогноза."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}):", str(raw or "").strip())
    return int(m.group(1)) if m else None


def build_forecast(tile, state):
    """
    Погода: слева текущая, дальше кривая прогноза по часам.

    Данные не из базы истории, а из каналов виртуального устройства - это
    прогноз, а не прошлое, и wb-mqtt-db тут ни при чём.

    Ряды приходят одной строкой на 24 значения (temp_series, code_series,
    precip_prob_series, cloud_series). Старый формат с отдельными каналами
    temp_00…temp_NN поддерживается как запасной: у кого-то устройство ещё
    не обновлено.
    """
    dev = tile.get("device", "weather")
    hours = int(tile.get("hours", 12))
    snaps = []

    def val(name):
        snap = state.snapshot("%s/%s" % (dev, name))
        snaps.append(snap)
        return snap

    cur = to_float(val("temperature")["raw"])
    desc_snap = val("description")
    hum = to_float(val("humidity")["raw"])
    press = to_float(val("pressure")["raw"])
    wind = to_float(val("windspeed")["raw"])
    ok_raw = (val("forecast_ok")["raw"] or "").strip()

    temps = parse_series(val("temp_series")["raw"], hours)
    codes = parse_series(val("code_series")["raw"], hours)
    probs = parse_series(val("precip_prob_series")["raw"], hours)
    rains = parse_series(val("precip_series")["raw"], hours)
    clouds = parse_series(val("cloud_series")["raw"], hours)
    start_hour = parse_iso_hour(val("forecast_start")["raw"])

    now = time.time()
    if not [v for v in temps if v is not None]:
        # Запасной путь: устройство старого образца, ряды не публикует
        temps = []
        for i in range(hours):
            snap = state.snapshot("%s/temp_%02d" % (dev, i))
            snaps.append(snap)
            temps.append(to_float(snap["raw"]))

    points = [(now + i * 3600.0, v)
              for i, v in enumerate(temps) if v is not None]

    inner_w = tile.get("inner_w") or 354
    chart_h = tile.get("chart_h") or 170
    pad_x = 3.0

    geom, hlines, times, marks, ticks = None, [], [], [], []
    bars, sky, icons = [], [], []
    if len(points) >= 2:
        values = [v for _, v in points]
        # та же сетка, что у обычных графиков: шаг от размаха, 2-5 делений
        _lo, _hi, _step, n = grid_bounds(values, tile.get("grid"), "°C", target=3)
        divisions = max(2, min(n, 5))
        lo, hi, step, _n = grid_bounds(values, tile.get("grid"), "°C",
                                       divisions=divisions, target=3)
        geom = smooth_path(points, inner_w, chart_h, band=BAND, pad_x=pad_x,
                           lo=lo, hi=hi)

        y_top = chart_h * BAND[0]
        y_bot = chart_h * BAND[1]
        digits = label_digits(step)
        with_time = bool(tile.get("time_labels", True))
        for i in range(divisions + 1):
            f = i / float(divisions)
            # у нижней линии подпись не ставим: там идёт строка часов
            hlines.append({"y": round(y_bot - f * (y_bot - y_top), 1),
                           "edge": i in (0, divisions),
                           "text": "" if (with_time and i == 0)
                                   else _fmt(lo + f * (hi - lo), digits)})

        span_t = points[-1][0] - points[0][0]

        def px(t):
            return pad_x + (t - points[0][0]) / span_t * (inner_w - 2 * pad_x)

        def py(v):
            return y_bot - (v - lo) / max(hi - lo, 1e-6) * (y_bot - y_top)

        step_h = 3 if len(points) >= 9 else 2
        if start_hour is None:
            start_hour = time.localtime(now).tm_hour
        for i, (stamp, _v) in enumerate(points):
            x = px(stamp)
            # вертикаль на каждый час, как у обычных графиков; подписанные
            # часы выделены чуть заметнее
            if 2 < x < inner_w - 2:
                ticks.append({"x": round(x, 1), "major": i % step_h == 0})
            if i % step_h == 0:
                lx, anchor = edge_anchor(x, inner_w)
                times.append({"x": round(lx, 1), "anchor": anchor,
                              "text": "%02d" % ((start_hour + i) % 24)})

        # Экстремумы отмечаем только точками: значения и так читаются по
        # шкале слева, а подписи у самого края вылезали за плитку.
        for kind, target in (("max", max(values)), ("min", min(values))):
            idx = values.index(target)
            marks.append({"kind": kind,
                          "x": round(px(points[idx][0]), 1),
                          "y": round(py(target), 1)})

        # --- осадки и облачность -------------------------------------------
        # Дождь - столбики от нижней линии: высота по вероятности, насыщенность
        # по количеству. Вероятность отвечает на «брать ли зонт», количество -
        # на «насколько промокну», и путать их не стоит.
        half = (inner_w - 2 * pad_x) / max(len(points) - 1, 1) / 2.0
        for i in range(len(points)):
            prob = probs[i] if i < len(probs) else None
            if not prob:
                continue
            mm = (rains[i] if i < len(rains) else None) or 0.0
            x = px(points[i][0])
            h = (y_bot - y_top) * 0.42 * min(prob, 100.0) / 100.0
            bars.append({"x": round(x - half * 0.72, 1),
                         "w": round(half * 1.44, 1),
                         "y": round(y_bot - h, 1),
                         "h": round(h, 1),
                         # 0.2 мм/ч это морось, 2 мм/ч уже настоящий дождь
                         "opacity": round(0.28 + 0.42 * min(mm / 2.0, 1.0), 2)})

        # Облачность - полоса под верхней границей: чем плотнее, тем серее.
        # Отдельной шкалы не нужно, это фон, а не данные для чтения.
        for i in range(len(points)):
            cloud = clouds[i] if i < len(clouds) else None
            if cloud is None:
                continue
            x = px(points[i][0])
            sky.append({"x": round(x - half, 1), "w": round(half * 2, 1),
                        "opacity": round(0.06 + 0.30 * cloud / 100.0, 3)})

        # Значок по коду погоды: раз в step_h часов, чтобы не рябило
        for i in range(0, len(points), step_h):
            code = codes[i] if i < len(codes) else None
            if code is None:
                continue
            hour = (start_hour + i) % 24
            icons.append({"x": round(px(points[i][0]), 1),
                          "name": wmo_icon(code, 6 <= hour < 21)})

    # Скрипт дописывает ветер в описание, а он и так есть отдельной цифрой -
    # убираем дубль и подрезаем строку под ширину плитки.
    desc = (desc_snap["raw"] or "").strip()
    desc = re.sub(r",\s*ветер\b.*$", "", desc).strip()
    avail = max((tile.get("inner_w") or 354) - 84, 60)
    if text_width(desc, 13.5) > avail:
        while desc and text_width(desc + "…", 13.5) > avail:
            desc = desc[:-1]
        desc = desc.rstrip(" ,") + "…"

    # Когда дождь ожидается, это важнее давления: строка снизу и так тесная
    rain_note = ""
    if (val("rain_expected")["raw"] or "").strip() in ("1", "true", "True"):
        hrs = to_float(val("rain_in")["raw"])
        prob = to_float(val("precip_prob_max_12h")["raw"])
        if hrs is not None:
            rain_note = "дождь через %s ч" % _fmt(hrs, 0)
            if prob:
                rain_note += ", %s %%" % _fmt(prob, 0)

    bits = []
    if hum is not None:
        bits.append("%s %%" % _fmt(hum, 0))
    if press is not None:
        bits.append("%s мм" % _fmt(press, 0))
    if wind is not None:
        bits.append("%s км/ч" % _fmt(wind, 0))

    return {
        "on": False,
        "now_temp": ("%s°" % _fmt(cur, 1)) if cur is not None else "--",
        "short": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "desc": desc,
        "extra": " · ".join(bits),
        "geom": geom,
        "marks": marks,
        "bars": bars,
        "sky": sky,
        "icons": icons,
        "icon_now": wmo_icon(to_float(val("code_series")["raw"].split(",")[0])
                             if (val("code_series")["raw"] or "").strip() else None,
                             6 <= time.localtime(now).tm_hour < 21),
        "rain_note": rain_note,
        "hlines": hlines,
        "ticks": ticks,
        "times": times,
        "grid_labels": bool(tile.get("grid_labels", True)),
        "time_labels": bool(tile.get("time_labels", True)),
        "hours": len(points),
        "stale_forecast": ok_raw in ("0", "false", "False"),
        "status": " · ".join(bits),
        "snaps": snaps,
    }


def build_wheel(width, height, lo, hi, value, span=4.0):
    """
    Пологая дуга-«улыбка» со шкалой - та же, что в пульте.

    Плитка кондиционера рисуется ею, а не циферблатом термостата: приборы
    разные, и по виду это должно быть видно сразу.
    """
    if value is None:
        return None
    lo, hi = float(lo), float(hi)
    value = max(lo, min(hi, float(value)))
    px_deg = width / (span + 1.0)
    r = width * 1.9
    cx = width / 2.0
    # Дуга по центру плитки: выбранное значение на ней и есть главная
    # цифра, отдельной крупной уставки под ней больше нет.
    bottom = height * 0.56
    cy = bottom - r

    def pt(delta, radius=None):
        a = (delta * px_deg) / r
        rr = r if radius is None else radius
        return (cx + rr * math.sin(a), cy + rr * math.cos(a), a)

    def arc(radius, half):
        p0, p1 = pt(-half, radius), pt(half, radius)
        return "M %.1f,%.1f A %.0f,%.0f 0 0 0 %.1f,%.1f" % (
            p0[0], p0[1], radius, radius, p1[0], p1[1])

    ticks, labels = [], []
    step = 0.5 if span <= 5 else 1.0
    v = math.ceil((value - span / 2) / step) * step
    while v <= value + span / 2 + 1e-6:
        if lo - 1e-6 <= v <= hi + 1e-6:
            x, y, a = pt(v - value)
            whole = abs(v - round(v)) < 0.05
            ln = 9 if whole else 5
            ticks.append({"x1": round(x, 1), "y1": round(y, 1),
                          "x2": round(x + math.sin(a) * ln, 1),
                          "y2": round(y + math.cos(a) * ln, 1),
                          "major": whole})
            if whole:
                labels.append({"x": round(x - math.sin(a) * 15, 1),
                               "y": round(y - math.cos(a) * 15, 1),
                               "text": "%d°" % round(v),
                               "near": abs(v - value) < 0.3})
        v += step
    return {"arc": arc(r, span / 2), "ticks": ticks, "labels": labels,
            "cx": round(cx, 1), "cy": round(bottom, 1)}


# Мост wb-haier публикует эти контролы у каждого кондиционера.
AC_CONTROLS = ("power", "mode", "current_temp", "target_temp", "fan_mode",
               "quiet", "turbo", "preset", "swing_mode", "swing_h_mode", "online")
AC_MODE_RU = {"cool": "Охлаждение", "heat": "Нагрев", "dry": "Осушение",
              "fan_only": "Вентиляция", "auto": "Авто", "off": "Выключен"}
AC_MODE_ICON = {"cool": "snow", "heat": "heat", "dry": "drop",
                "fan_only": "fan", "auto": "power", "off": "power"}
AC_FAN_RU = {"low": "1", "medium": "2", "high": "3", "auto": "Авто"}

# Положение лопастей в градусах наклона. Значения - те, что отдаёт мост.
AC_SWING_DEG = {"upper": -38, "position_2": -19, "position_3": 0,
                "position_4": 19, "bottom": 38}
# Горизонтальные жалюзи: номера НЕ идут слева направо, порядок проверен
# на живом устройстве. Единица - это центр, а не крайнее положение.
#   4 лево · 5 лево-центр · 1 центр · 6 центр-право · 7 право
AC_SWING_H_DEG = {"position_4": -42, "position_5": -21, "position_1": 0,
                  "position_6": 21, "position_7": 42}


def louver_glyph(value, table, vertical=True, size=19.0):
    """
    Пиктограмма положения лопастей: полоски наклонены так же, как реальные
    жалюзи. «Авто» - веер из трёх, потому что они качаются.

    Возвращает None, когда положение неизвестно или жалюзи выключены -
    рисовать тогда нечего.
    """
    value = (value or "").strip()
    if value in ("", "off", "0"):
        return None
    if value == "auto":
        angles = [(-24, .35), (0, .6), (24, .35)]
    elif value in table:
        angles = [(table[value], 1.0)]
    else:
        return None

    blades = []
    half = size * 0.42
    for idx, (deg, op) in enumerate(angles):
        rad = math.radians(deg)
        # для горизонтальных жалюзи вид сверху: полоска поворачивается
        # вокруг той же точки, только оси поменяны местами
        cx = size / 2.0
        cy = size * (0.62 if vertical else 0.5)
        dx = half * math.cos(rad)
        dy = half * math.sin(rad)
        if not vertical:
            dx, dy = -dy, dx
        if len(angles) > 1:
            shift = (idx - 1) * (2.4 if vertical else 0)
            cy += shift
        blades.append({"x1": round(cx - dx, 1), "y1": round(cy - dy, 1),
                       "x2": round(cx + dx, 1), "y2": round(cy + dy, 1),
                       "op": op})
    return {"blades": blades}


def ac_channels(tile):
    """Каналы кондиционера по префиксу устройства - как у погоды."""
    dev = tile.get("device")
    if not dev:
        return []
    return ["%s/%s" % (dev, name) for name in AC_CONTROLS]


def build_ac(tile, state):
    """
    Кондиционер: крупно уставка, в углу фактическая температура,
    снизу режим и скорость вентилятора.
    """
    dev = tile.get("device", "")
    snaps = []

    def val(name):
        snap = state.snapshot("%s/%s" % (dev, name))
        snaps.append(snap)
        return snap

    power_raw = (val("power")["raw"] or "").strip()
    on = power_raw not in ("", "0", "false", "False")
    mode = (val("mode")["raw"] or "").strip()
    cur = to_float(val("current_temp")["raw"])
    tgt = to_float(val("target_temp")["raw"])
    fan = (val("fan_mode")["raw"] or "").strip()
    quiet_raw = (val("quiet")["raw"] or "").strip()
    quiet = quiet_raw not in ("", "0", "false", "False")

    lo = float(tile.get("temp_min", 16))
    hi = float(tile.get("temp_max", 30))

    swing = (val("swing_mode")["raw"] or "").strip()
    swing_h = (val("swing_h_mode")["raw"] or "").strip()
    swinging = swing not in ("", "off", "0") or swing_h not in ("", "off", "0")
    louver = louver_glyph(swing, AC_SWING_DEG, vertical=True) if on else None
    louver_h = louver_glyph(swing_h, AC_SWING_H_DEG, vertical=False) if on else None

    # Режим показывает значок, поэтому словом его не дублируем - в строке
    # остаётся только то, чего значком не передать.
    bits = []
    if fan:
        bits.append("вент. " + AC_FAN_RU.get(fan, fan))
    if quiet:
        bits.append("тихо")

    target_num = _fmt(tgt, 0) if tgt is not None else "--"
    big = 30.0
    return {
        "on": on,
        "mode": mode,
        "icon": tile.get("icon") or AC_MODE_ICON.get(mode, "snow"),
        "target_num": target_num,
        "deg_dx": round(text_width(target_num, big) / 2.0 + 1.0, 1),
        "target": ("%s°" % target_num) if tgt is not None else "--",
        "current": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "short": ("%s°" % _fmt(cur, 1)) if cur is not None else "",
        "status": (" · ".join(bits) if on else "Выключен"),
        "mode_ru": AC_MODE_RU.get(mode, mode),
        "swinging": swinging,
        "louver": louver,
        "louver_h": louver_h,
        "room": tile.get("room", ""),
        "always_status": True,
        "lo": lo, "hi": hi,
        "wheel": build_wheel(tile.get("inner_w") or 170,
                             tile.get("chart_h") or 170, lo, hi, tgt),
        "enabled": on,
        "reach": None,
        "snaps": snaps,
    }


def build_link(tile, state):
    """
    Ссылка на другую панель. Ничего не читает из MQTT - только переход.

    Нужна, чтобы с общей панели проваливаться в комнату и обратно.
    """
    target = tile.get("panel") or ""
    return {
        "on": False,
        "href": tile.get("url") or ((target + ".html") if target else ""),
        "icon": tile.get("icon", "door"),
        "status": tile.get("subtitle", ""),
        "snaps": [],
    }


def build_value(tile, state):
    snap = state.snapshot(tile.get("channel", ""))
    raw = to_float(snap["raw"])
    return {
        "value": _fmt(raw, int(tile.get("digits", 1))),
        "unit": tile.get("unit", snap["units"]),
        "on": False,
        "snap": snap,
    }


BUILDERS = {
    "chart": None,   # особый случай: нужен history
    "switch": build_switch,
    "curtain": build_curtain,
    "light": build_light,
    "dimmer": build_dimmer,
    "thermostat": build_thermostat,
    "forecast": build_forecast,
    "ac": build_ac,
    "link": build_link,
    "value": build_value,
}


# ==========================================================================
# Строка состояния комнаты
# ==========================================================================
#
# Показываем только события, а не измерения: присутствие, открытую дверь,
# протечку, работающий прибор. Всё, у чего есть плитка с графиком, сюда не
# попадает - дублировать незачем.
#
# Когда в комнате всё спокойно, строка пустая. Молчание само по себе
# информативно, и глазу не за что цепляться.

# Когда канал последний раз выходил за порог: {ключ: время}.
# Живёт в памяти процесса и обновляется при отрисовке. Панель перерисовывают
# раз в десять секунд, пока открыта хоть одна вкладка; если не смотрит никто,
# то и показывать давность некому.
SEEN = {}

# Значок «был недавно» для тех, у кого активное состояние - движение
FADED = {"motion": "person"}

# Цвет бейджа режима. Оранжевый чип означает «обрати внимание», и работающий
# кондиционер под это не подходит - он просто работает. Поэтому у приборов
# чип нейтральный, а режим показан цветом бейджа: холод синий, тепло красное,
# обдув зелёный, авто и возврат на базу нейтральные.
BADGE_COLORS = {
    "snow":  "#3B8FD4",
    "heat":  "#D9553B",
    "fan":   "#4E9A6B",
    "water": "#3B8FD4",
    "auto":  None,
    "home":  None,
}


def status_entries(panel_conf):
    """Список описаний статусов панели, всегда список словарей."""
    out = []
    for entry in (panel_conf or {}).get("status", []) or []:
        if isinstance(entry, dict) and (entry.get("channel")
                                        or entry.get("value_channel")):
            out.append(entry)
    return out


def parse_span(text, default=0):
    """'20m' -> 1200. Понимает s/m/h, голое число считает минутами."""
    if text is None:
        return default
    raw = str(text).strip().lower()
    if not raw:
        return default
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(raw[-1])
    try:
        return int(float(raw[:-1] if mult else raw)) * (mult or 60)
    except ValueError:
        return default


def status_required(entry, state):
    """
    Дополнительное условие показа - обычно «прибор включён».

    У кондиционера канал mode продолжает сообщать cool и после выключения,
    поэтому по нему одному судить нельзя.

        require:
          channel: "haier_bedroom/power"
          when: "1"
    """
    req = entry.get("require")
    if not req:
        return True
    for item in (req if isinstance(req, list) else [req]):
        if not isinstance(item, dict) or not item.get("channel"):
            continue
        raw = state.snapshot(item["channel"])["raw"]
        if not status_matches(item, raw):
            return False
    return True


def status_matches(entry, raw):
    """Выполнено ли условие показа: above / below / when."""
    if raw is None or raw == "":
        return False
    if entry.get("above") is not None or entry.get("below") is not None:
        num = to_float(raw)
        if num is None:
            return False
        if entry.get("above") is not None and num <= float(entry["above"]):
            return False
        if entry.get("below") is not None and num >= float(entry["below"]):
            return False
        return True
    when = entry.get("when")
    if when is not None:
        allowed = when if isinstance(when, (list, tuple)) else [when]
        return str(raw).strip() in [str(x) for x in allowed]
    # ни одного условия - показываем всё, кроме явно выключенного
    return str(raw).strip() not in ("0", "off", "false", "False", "none")


def chip_width(value):
    """Должно совпадать с макросом chip() в шаблоне."""
    if not value:
        return 7 + 26 + 7
    return 10 + 26 + 6 + len(str(value)) * 8.5 + 12


def build_status(panel_conf, state, now=None):
    """Описания статусов -> чипы для шаблона."""
    now = now or time.time()
    chips = []
    for entry in status_entries(panel_conf):
        key = entry.get("channel")
        raw = state.snapshot(key)["raw"] if key else None
        icon = entry.get("icon", "power")
        active = bool(key) and status_matches(entry, raw) \
            and status_required(entry, state)

        fade = parse_span(entry.get("fade"), 0)
        if active:
            SEEN[key] = now
        elif fade and key:
            # Условие уже не выполняется, но недавно выполнялось: показываем
            # приглушённый вариант. Для движения это человечек стоя - тот же
            # персонаж, другая поза, читается как «был, но не сейчас».
            if now - SEEN.get(key, 0) > fade:
                continue
            icon = entry.get("icon_faded") or FADED.get(icon, icon)
        else:
            continue

        # Бейдж режима: либо фиксированный, либо по значению канала
        badge = entry.get("badge")
        badges = entry.get("badges") or {}
        if badges and raw is not None:
            badge = badges.get(str(raw).strip(), badge)

        # Число рядом со значком - только если оно меняет решение:
        # уставка кондиционера, процент уборки. Давность движения не меняет.
        value = None
        if entry.get("value_channel"):
            num = to_float(state.snapshot(entry["value_channel"])["raw"])
            if num is not None:
                digits = int(entry.get("value_digits", 0))
                value = ("%.*f" % (digits, num)) + str(entry.get("value_unit", ""))

        # Оранжевый чип - это «обрати внимание». Работающий прибор внимания
        # не требует, поэтому тонируется только то, что помечено attention
        # (по умолчанию - всё, у чего нет бейджа режима: движение, дверь,
        # протечка). Прибор различается цветом бейджа, а не заливкой.
        attention = entry.get("attention")
        if attention is None:
            attention = not (badge or entry.get("badges"))

        chips.append({"icon": icon, "badge": badge, "value": value,
                      "active": bool(active and attention),
                      "badge_color": (entry.get("badge_color")
                                      or BADGE_COLORS.get(badge)),
                      "alarm": bool(entry.get("alarm")),
                      "w": chip_width(value)})
    return chips


def place_chips(chips, right_edge, y, gap=12):
    """Разложить чипы справа налево от правого края."""
    total = sum(c["w"] for c in chips) + gap * (len(chips) - 1 if chips else 0)
    x = right_edge - total
    for c in chips:
        c["x"], c["y"] = round(x, 1), y
        x += c["w"] + gap
    return chips


def resolve_sections(panel_conf, all_panels):
    """
    Разделы панели -> [(заголовок или None, список плиток)].

    Раздел можно задать своими плитками либо ссылкой `from:` на другую
    панель - тогда её плитки подтягиваются целиком и не дублируются в
    конфиге. Панель без `sections` - это один безымянный раздел.
    """
    sections = panel_conf.get("sections")
    if not sections:
        return [(None, panel_conf.get("tiles", []) or [], panel_conf)]

    out = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        tiles = list(section.get("tiles", []) or [])
        src_name = section.get("from")
        # Откуда брать статусы раздела: свои, либо от панели-источника.
        # Комната описывает статусы один раз у себя, а сводные панели
        # подхватывают их вместе с плитками.
        src = section
        if src_name:
            source = (all_panels or {}).get(src_name) or {}
            tiles = list(source.get("tiles", []) or []) + tiles
            title = section.get("title", source.get("title", src_name))
            if not section.get("status"):
                src = source
        else:
            title = section.get("title")
        if tiles:
            out.append((title, tiles, src))
    return out or [(None, [], panel_conf)]


def build_tile(conf, index, x, y, pw, ph, panel_conf, state, history, interactive):
    """
    Собрать одну плитку: геометрия, данные, команда.

    Вынесено из prepare отдельно, потому что ту же плитку нужно уметь
    нарисовать и в произвольном размере - для полноэкранного вида.
    """
    now = time.time()
    tile = {
        "i": index,
        "kind": conf.get("type", "value"),
        "title": conf.get("title", ""),
        "subtitle": conf.get("subtitle", ""),
        "icon": conf.get("icon"),
        # Строка «Включено / Выключено» под названием плитки дублирует
        # оранжевую рамку и тумблер, поэтому скрыта.
        "show_status": bool(conf.get("show_status",
                            panel_conf.get("show_status",
                            (config.get("show_status", False)
                             if config else False)))),
        "x": x, "y": y, "w": pw, "h": ph,
        # мелкая плитка не вмещает второй строки и крупной цифры
        "compact": pw < CELL * 2,
        # на 78 px влезает примерно 9 знаков
        # Перенос строки в заголовке: "Дим трек\nправый" -> две строки.
        # Пока не нужен, но пусть будет - плитки узкие, названия длинные.
        "title_lines": [s for s in str(conf.get("title", "")).split("\n") if s != ""]
                       or [""],
        "title_s": (conf.get("title", "").replace("\n", " ")[:9] + "…")
                   if len(conf.get("title", "").replace("\n", " ")) > 10
                   else conf.get("title", "").replace("\n", " "),
        "inner_w": pw,
        "chart_h": ph,
    }
    conf = dict(conf)
    conf["inner_w"] = pw
    conf["chart_h"] = ph

    if tile["kind"] == "chart":
        tile.update(build_chart(conf, state, history))
        tile["on"] = False
        snaps = [state.snapshot(s["channel"])
                 for s in conf.get("series", []) or [] if s.get("channel")]
    else:
        builder = BUILDERS.get(tile["kind"], build_value)
        tile.update(builder(conf, state))
        snaps = tile.pop("snaps", None) or [tile.pop("snap", None)]
        snaps = [s for s in snaps if s]

    tile["cmd"] = (tile_command(tile, conf, state)
                   if (interactive or tile["kind"] == "link") else None)
    if tile["cmd"] is not None:
        tile["cmd"]["i"] = str(index)
    # Собираем data-атрибуты здесь, а не в шаблоне: с trim_blocks условные
    # блоки склеиваются без пробела и ломают разметку.
    tile["attrs"] = cmd_attrs(tile)

    known = [s for s in snaps if s["known"]]
    tile["offline"] = (not known) or any(s["error"] for s in snaps)
    tile["stale"] = bool(known) and all(now - s["ts"] > STALE_AFTER for s in known)
    return tile


def tile_command(tile, conf, state):
    """
    Что публиковать по нажатию и что можно менять в полноэкранном виде.

    Шаблон про MQTT ничего не знает - он только раскладывает эти поля в
    data-атрибуты, а обработчик на странице читает их оттуда.
    """
    kind = tile["kind"]
    if kind == "link":
        return {"href": tile.get("href") or ""} if tile.get("href") else None
    if kind == "chart" or kind == "forecast":
        # графики не управляют, но раскрываются на весь экран
        return {"expand": True}

    cmd = None
    if kind in ("switch", "dimmer", "light"):
        src_ch = conf.get("channel_switch") if kind == "light" else conf.get("channel")
        topic = command_topic(src_ch, conf.get("command_topic"))
        if topic:
            v_on = str(conf.get("command_on", "1"))
            v_off = str(conf.get("command_off", "0"))
            # При invert плитка показывает логическое состояние, а в канал надо
            # писать физическое: чтобы включить нагрузку - обесточить реле.
            # state здесь логический, он же нарисован на плитке, поэтому
            # достаточно поменять местами сами значения.
            if conf.get("invert"):
                v_on, v_off = v_off, v_on
            cmd = {"topic": topic,
                   "on": v_on,
                   "off": v_off,
                   "state": "1" if tile.get("on") else "0"}
    elif kind == "curtain":
        topic = command_topic(conf.get("channel"), conf.get("command_topic"))
        if topic:
            pos = tile.get("left_open")
            if pos is None:
                pos = 0.0
            cmd = {"topic": topic,
                   "on": str(conf.get("command_open", "100")),
                   "off": str(conf.get("command_close", "0")),
                   "state": "1" if pos > 0.5 else "0",
                   # долгое нажатие открывает пульт: полоса положения,
                   # кнопки «закрыть / стоп / открыть»
                   "pad": "curtain",
                   "pos": "%.3f" % pos}
            # «Стоп» - отдельная команда, у HomeKit-совместимых приводов это
            # C_TargetPositionState со значением 2. Топик только явный:
            # для сырых топиков моста угадывать нечего.
            stop = conf.get("command_topic_stop")
            if stop:
                cmd["stop"] = stop
                cmd["stop_value"] = str(conf.get("command_stop", "2"))
            if tile.get("moving"):
                cmd["moving"] = tile["moving"]

    if kind == "ac":
        dev = conf.get("device", "")
        def top(name):
            return "/devices/%s/controls/%s/on" % (dev, name) if dev else None
        cmd = {"topic": top("power"), "on": "1", "off": "0",
               "state": "1" if tile.get("on") else "0",
               "pad": "ac",
               "ac_target": top("target_temp"),
               "ac_mode": top("mode"),
               "ac_fan": top("fan_mode"),
               "ac_quiet": top("quiet"),
               "ac_swing": top("swing_mode"),
               "ac_swing_h": top("swing_h_mode"),
               "ac_swing_now": (state.snapshot("%s/swing_mode" % dev)["raw"] or "").strip(),
               "ac_swing_h_now": (state.snapshot("%s/swing_h_mode" % dev)["raw"] or "").strip(),
               "ac_lo": str(tile.get("lo", 16)),
               "ac_hi": str(tile.get("hi", 30)),
               "ac_step": str(conf.get("step", 1)),
               "ac_now": tile.get("target_num", "--"),
               "ac_cur": tile.get("current", ""),
               "ac_mode_now": tile.get("mode", ""),
               "ac_fan_now": (state.snapshot("%s/fan_mode" % dev)["raw"] or "").strip(),
               "ac_quiet_now": "1" if "тихий" in (tile.get("status") or "") else "0"}
        return cmd

    # у ленты и диммера есть что крутить, значит нужен полноэкранный пульт
    if kind in ("light", "dimmer"):
        cmd = cmd or {}
        b_topic = command_topic(conf.get("channel_brightness"),
                                conf.get("command_topic_brightness"))
        if b_topic:
            b_snap = state.snapshot(conf.get("channel_brightness", ""))
            cmd["bright"] = b_topic
            cmd["bright_max"] = str(to_float(b_snap["meta"].get("max"), 100.0) or 100.0)
        if kind == "light":
            t_topic = command_topic(conf.get("channel_temp"),
                                    conf.get("command_topic_temp"))
            if t_topic:
                t_snap = state.snapshot(conf.get("channel_temp", ""))
                cmd["temp"] = t_topic
                cmd["temp_max"] = str(to_float(t_snap["meta"].get("max"), 100.0) or 100.0)
                cmd["temp_unit"] = str(conf.get("temp_unit", "kelvin"))
                cmd["temp_lo"] = str(conf.get("temp_min", 2700))
                cmd["temp_hi"] = str(conf.get("temp_max", 6500))
                cmd["cold"] = tile.get("cold", "#56AAFF")
                cmd["warm"] = tile.get("warm", "#FF9337")
                cmd["dot_x"] = "%.3f" % (tile.get("dot_x") or 0.5)
                cmd["dot_y"] = "%.3f" % (tile.get("dot_y") or 0.5)
            cmd["pad"] = "xy"
        else:
            cmd["pad"] = "level"
            cmd["level"] = "%.3f" % (tile.get("fill") or 0.0)
        if not cmd.get("topic") and not cmd.get("bright"):
            cmd = None
    return cmd or None


CMD_ATTRS = [
    ("i", "data-i"), ("topic", "data-topic"), ("on", "data-on"),
    ("off", "data-off"), ("state", "data-state"), ("expand", "data-expand"),
    ("pad", "data-pad"), ("bright", "data-bright"), ("bright_max", "data-bright-max"),
    ("pos", "data-pos"), ("stop", "data-stop"), ("stop_value", "data-stop-value"),
    ("moving", "data-moving"),
    ("level", "data-level"), ("temp", "data-temp"), ("temp_max", "data-temp-max"),
    ("temp_unit", "data-temp-unit"), ("temp_lo", "data-temp-lo"),
    ("temp_hi", "data-temp-hi"), ("cold", "data-cold"), ("warm", "data-warm"),
    ("dot_x", "data-dot-x"), ("dot_y", "data-dot-y"),
    ("ac_target", "data-ac-target"), ("ac_mode", "data-ac-mode"),
    ("ac_fan", "data-ac-fan"), ("ac_quiet", "data-ac-quiet"),
    ("ac_lo", "data-ac-lo"), ("ac_hi", "data-ac-hi"), ("ac_step", "data-ac-step"),
    ("ac_now", "data-ac-now"), ("ac_cur", "data-ac-cur"),
    ("ac_mode_now", "data-ac-mode-now"), ("ac_fan_now", "data-ac-fan-now"),
    ("ac_quiet_now", "data-ac-quiet-now"),
    ("ac_swing", "data-ac-swing"), ("ac_swing_h", "data-ac-swing-h"),
    ("ac_swing_now", "data-ac-swing-now"), ("ac_swing_h_now", "data-ac-swing-h-now"),
    ("href", "data-href"),
]


def cmd_attrs(tile):
    """Команда плитки -> готовая строка data-атрибутов для тега <g>."""
    cmd = tile.get("cmd")
    if not cmd:
        return ""
    parts = ['data-kind="%s"' % xml_escape(tile["kind"]),
             'data-title="%s"' % xml_escape(tile.get("title", ""))]
    for key, attr in CMD_ATTRS:
        value = cmd.get(key)
        if value in (None, "", False):
            continue
        parts.append('%s="%s"' % (attr, xml_escape("1" if value is True else str(value))))
    return " " + " ".join(parts)


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def prepare(panel_conf, state, history, cols=None, all_panels=None):
    big_cols = int(cols or panel_conf.get("cols", 4))
    interactive = bool(panel_conf.get("interactive",
                       (config.get("interactive", False) if config else False)))
    cols = big_cols * 2          # дальше всё в мелких ячейках
    sections = resolve_sections(panel_conf, all_panels)

    out = []
    headers = []
    index = 0
    y_cursor = PAD + HEADER

    right_edge = PAD * 2 + cols * CELL + (cols - 1) * GAP - PAD

    for title, tiles_conf, status_src in sections:
        if title:
            # Было 6: чипы высотой 36 подходили вплотную к плиткам сверху.
            y_cursor += 14
            hd = {"title": title, "x": PAD, "y": y_cursor + 22, "chips": []}
            # Чипы прижаты к правому краю заголовка раздела: там пусто,
            # и не приходится гадать, какой ширины получился текст.
            # Высота полосы раздела 44, чип 36 - помещается без сдвига плиток.
            # Чип центрируется по оптической середине заголовка, а не по
            # полосе раздела: базовая линия текста ниже его середины на
            # половину роста прописных, отсюда -2 вместо +4.
            hd["chips"] = place_chips(
                build_status(status_src, state), right_edge, y_cursor - 2)
            headers.append(hd)
            y_cursor += SECTION
        placed, rows = layout(tiles_conf, cols)

        for conf, row, col, w, h in placed:
            out.append(build_tile(
                conf, index,
                PAD + col * (CELL + GAP), y_cursor + row * (CELL + GAP),
                w * CELL + (w - 1) * GAP, h * CELL + (h - 1) * GAP,
                panel_conf, state, history, interactive))
            index += 1

        if rows:
            y_cursor += rows * CELL + (rows - 1) * GAP + GAP

    width = PAD * 2 + cols * CELL + (cols - 1) * GAP
    height = max(y_cursor - GAP + PAD, PAD * 2 + HEADER)

    # Статусы самой панели - в шапке, левее часов: справа стоит время,
    # и наезжать на него нельзя.
    # Заголовок панели: базовая линия на 44, значит середина чипа на 38.
    top_chips = place_chips(build_status(panel_conf, state),
                            width - PAD - 62, 20)
    return out, width, height, headers, top_chips


# ==========================================================================
#  HTTP
# ==========================================================================

# --------------------------------------------------------------------------
#  Совместимость со старыми браузерами
# --------------------------------------------------------------------------
# Android 5.1 несёт WebView на движке Chrome 39-40. В нём нет двух вещей,
# на которых построен шаблон:
#   * CSS-переменных (var) - появились в Chrome 49. Без них не резолвятся
#     ВСЕ цвета и размеры шрифта, и половина картинки не рисуется;
#   * fetch() - появился в Chrome 42, на нём держится страница.
# Поэтому для таких браузеров отдаём SVG с уже подставленными значениями
# и страницу на XMLHttpRequest.
#
# Заодно это готовое решение для ESP32-дисплея: librsvg тоже не понимает var().

def _css_vars(style, selector):
    """Объявления --name: value из блока с заданным селектором."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", style)
    if not match:
        return {}
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)))


def resolve_css_vars(svg, theme="light", fs=1.0):
    """
    Подставить значения CSS-переменных прямо в разметку.

    Делается ВСЕГДА, для любого браузера.

    Где переменная не поддерживается, свойство просто игнорируется, и fill
    становится чёрным по умолчанию: плитки есть, нажатия работают, а на
    тёмном фоне не видно ничего. Именно так вело себя старое устройство.

    Современным браузерам подстановка не мешает - результат тот же самый.
    Заодно решается задача для rsvg-convert и ESP32-дисплея: librsvg
    переменные тоже не понимает.

    Отключить, если когда-нибудь понадобится сырой вариант: ?flat=0
    """
    style_match = re.search(r"<style>(.*?)</style>", svg, re.S)
    if not style_match:
        return svg
    style = style_match.group(1)

    values = _css_vars(style, "svg")
    if theme == "dark":
        values.update(_css_vars(style, ".dark"))
    values["--fs"] = str(fs)

    def sub_var(text, depth=0):
        if depth > 4 or "var(" not in text:
            return text
        out = re.sub(r"var\((--[\w-]+)\)",
                     lambda m: values.get(m.group(1), "inherit").strip(), text)
        return sub_var(out, depth + 1)

    body = sub_var(svg)

    # calc(9.5px * 2.17) -> 20.6px: старые движки такое умеют, но пусть
    # не считают лишнего, а заодно уйдут выражения с потерявшимися var
    def calc(m):
        try:
            return "%.2fpx" % (float(m.group(1)) * float(m.group(2)))
        except ValueError:
            return m.group(0)
    body = re.sub(r"calc\(\s*([\d.]+)px\s*\*\s*([\d.]+)\s*\)", calc, body)

    # Сами объявления больше не нужны.
    # Осторожно с границами: наивный шаблон style="--fs:…" без \b и без
    # привязки к пробелу схватывал открывающий тег <style> вместе с куском
    # CSS - в разметке оставался мусор, а картинка переставала быть XML.
    body = re.sub(r"[ \t]*--[\w-]+[ \t]*:[^;\n]*;", "", body)
    body = re.sub(r'(?<=[\s])style="--fs:[\d.]+"[ \t]*', "", body)
    return body


app = Flask(__name__)
config = None
mqtt_client = None
state = WbState()
history = None
jinja = None


def render_panel(name, cols=None):
    config.reload()
    state.set_watched(config.used_channels())
    panel_conf = config.panels.get(name)
    if panel_conf is None:
        abort(404, "панель %r не найдена в конфиге" % name)
    tiles, width, height, headers, top_chips = prepare(
        panel_conf, state, history, cols=cols, all_panels=config.panels)
    template = jinja.get_template(panel_conf.get("template", "panel.svg.j2"))
    return template.render(
        # префикс идентификаторов: панель и раскрытая плитка живут в одном
        # документе, и без него их clipPath и градиенты перекрывают друг друга
        uid="p-",
        fs=1,
        tiles=tiles,
        headers=headers,
        top_chips=top_chips,
        width=width,
        height=height,
        rx=RX,
        theme=panel_conf.get("theme", config.get("theme", "dark")),
        title=panel_conf.get("title", ""),
        updated=time.strftime("%H:%M"),
        connected=state.connected,
    )


@app.route("/")
def index():
    """Список всех панелей с живыми превью. Точка входа для человека."""
    config.reload()
    rows = []
    hidden_count = 0
    for name in sorted(config.panels):
        panel_conf = config.panels[name] or {}
        tiles = []
        for _title, chunk, _status in resolve_sections(panel_conf, config.panels):
            tiles.extend(chunk)
        kinds = {}
        for tile in tiles:
            kinds[tile.get("type", "value")] = kinds.get(tile.get("type", "value"), 0) + 1
        summary = ", ".join("%s × %d" % (k, v) for k, v in sorted(kinds.items()))
        is_hidden = bool(panel_conf.get("hidden"))
        if is_hidden:
            hidden_count += 1
        rows.append(
            '<a class="card%(cls)s" data-n="%(n)s" href="%(n)s.html">'
            '<div class="thumb">'
            '<img data-n="%(n)s" src="%(n)s.svg" loading="lazy" alt=""></div>'
            '<div class="meta"><b>%(t)s%(mark)s</b>'
            '<span class="slug">/%(n)s.svg</span>'
            '<span class="sum">%(s)s</span></div></a>' % {
                "n": html.escape(name),
                "t": html.escape(panel_conf.get("title") or name),
                "s": html.escape(summary or "пусто"),
                "cls": " is-hidden" if is_hidden else "",
                "mark": ' <span class="tagx">скрыта</span>' if is_hidden else "",
            })

    # Переключатель показываем, только когда есть что показывать: иначе он
    # просто мозолит глаза.
    toggle = ""
    if hidden_count:
        toggle = ('<label class="toggle"><input type="checkbox" id="sh">'
                  ' Показать скрытые <span class="cnt">%d</span></label>' % hidden_count)

    with state.lock:
        nchan = len(state.values)
        online = state.connected
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Панели</title><style>
 body{font:14px/1.5 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:22px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:22px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 .toggle{display:inline-flex;align-items:center;gap:7px;margin-bottom:18px;
         padding:7px 13px;background:#fff;border:1.5px solid rgba(60,60,67,.16);
         border-radius:10px;cursor:pointer;user-select:none;font-size:13.5px}
 .toggle:hover{border-color:#E09B2D}
 .toggle input{accent-color:#E09B2D;margin:0}
 .cnt{color:#8A8A90}
 .grid{display:grid;gap:16px;
       grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
 .card{display:block;text-decoration:none;color:inherit;background:#fff;
       border:1.5px solid rgba(60,60,67,.16);border-radius:16px;overflow:hidden}
 .card:hover{border-color:#E09B2D}
 .card.is-hidden{display:none;opacity:.72;border-style:dashed}
 .grid.show-hidden .card.is-hidden{display:block}
 .thumb{background:#E6E6E9;padding:10px;display:flex;justify-content:center}
 .thumb img{max-width:100%%;height:auto;border-radius:8px}
 .meta{padding:11px 14px}
 .meta b{display:block;font-size:15px}
 .tagx{font-size:11px;font-weight:600;color:#8A8A90;background:#EDEDF0;
       padding:1px 6px;border-radius:6px;vertical-align:1px}
 .slug{display:block;color:#8A8A90;font:12px ui-monospace,Menlo,monospace;
       margin-top:2px}
 .sum{display:block;color:#8A8A90;font-size:12.5px;margin-top:5px}
 .links{margin-top:22px;color:#8A8A90;font-size:13px}
 .links a{color:#3A3A3E}
 .off{color:#C0392B}
 /* тот же выбор колонок, что и на самой панели: значение общее */
 #cols{display:inline-block;background:#fff;border:1.5px solid rgba(60,60,67,.16);
       border-radius:11px;padding:3px;margin:0 0 14px 10px;vertical-align:middle}
 #cols b{display:inline-block;padding:6px 10px;border-radius:8px;cursor:pointer;
         font-size:13.5px;font-weight:600;color:#8A8A90}
 #cols b.on{background:#E09B2D;color:#fff}
</style></head><body>
<h1>Панели</h1>
<div class="sub">%(count)d шт. · каналов в памяти: %(chan)d · история: %(hist)s
 %(mqtt)s</div>
%(toggle)s<span id="cols"></span>
<div class="grid" id="g">%(rows)s</div>
<div class="links">
 <a href="docs"><b>Как собирать панели</b></a> ·
 <a href="channels">Список каналов</a> ·
 <a href="healthz">healthz</a> ·
 плитка ведёт на <code>.html</code> с автообновлением,
 сама картинка — <code>&lt;имя&gt;.svg</code>
</div>
<script>
 var sh = document.getElementById('sh');
 if (sh) sh.onchange = function () {
   document.getElementById('g').classList.toggle('show-hidden', sh.checked);
 };

 /* Число колонок общее со страницами панелей - лежит в том же ключе.
    Меняем здесь: обновляются и превью, и ссылки, по которым вы уйдёте. */
 var STORE = 'wb-panel-cols', COLS = '';
 try { COLS = localStorage.getItem(STORE) || ''; } catch (e) {}

 function applyCols() {
   var q = COLS ? ('?cols=' + COLS) : '';
   var imgs = document.querySelectorAll('#g img[data-n]');
   for (var i = 0; i < imgs.length; i++) {
     imgs[i].src = imgs[i].getAttribute('data-n') + '.svg' + q;
   }
   var links = document.querySelectorAll('#g a[data-n]');
   for (var j = 0; j < links.length; j++) {
     links[j].href = links[j].getAttribute('data-n') + '.html' + q;
   }
 }

 function drawCols() {
   var box = document.getElementById('cols'),
       opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'], h = '';
   for (var i = 0; i < opts.length; i++) {
     h += '<b data-v="' + opts[i] + '"' + (opts[i] === COLS ? ' class="on"' : '') + '>'
        + (opts[i] === '' ? 'авто' : opts[i]) + '</b>';
   }
   box.innerHTML = h;
   var btns = box.getElementsByTagName('b');
   for (var j = 0; j < btns.length; j++) {
     btns[j].onclick = function () {
       COLS = this.getAttribute('data-v');
       try { COLS ? localStorage.setItem(STORE, COLS) : localStorage.removeItem(STORE); }
       catch (e) {}
       drawCols(); applyCols();
     };
   }
 }
 drawCols(); applyCols();
</script>
</body></html>""" % {
        "count": len(rows) - hidden_count, "chan": nchan,
        "hist": html.escape(str(history.mode if history and history.mode else "—")),
        "mqtt": "" if online else '· <span class="off">нет связи с MQTT</span>',
        "toggle": toggle,
        "rows": "".join(rows) or "<i>в config.yaml нет ни одной панели</i>",
    }
    return Response(page, mimetype="text/html; charset=utf-8")


def allowed_topics():
    """
    Куда демону вообще позволено писать.

    Список строится из конфига: только те командные топики, которые
    соответствуют нарисованным плиткам. Иначе панель превратилась бы в
    открытый шлюз в MQTT - а брокер на Wiren Board по умолчанию без пароля,
    и писать в него можно что угодно, включая реле котла.
    """
    allowed = set()
    for panel_conf in config.panels.values():
        interactive = bool((panel_conf or {}).get("interactive",
                           config.get("interactive", False)))
        if not interactive:
            continue
        for _title, tiles, _status in resolve_sections(panel_conf, config.panels):
            for tile in tiles:
                kind = tile.get("type")
                pairs = []
                if kind in ("switch", "dimmer", "curtain"):
                    pairs.append((tile.get("channel"), tile.get("command_topic")))
                elif kind == "light":
                    pairs.append((tile.get("channel_switch"), tile.get("command_topic")))
                if kind == "ac" and tile.get("device"):
                    for name in ("power", "mode", "target_temp", "fan_mode",
                                 "quiet", "turbo", "preset",
                                 "swing_mode", "swing_h_mode"):
                        allowed.add("/devices/%s/controls/%s/on"
                                    % (tile["device"], name))
                if kind in ("light", "dimmer"):
                    pairs.append((tile.get("channel_brightness"),
                                  tile.get("command_topic_brightness")))
                if kind == "light":
                    pairs.append((tile.get("channel_temp"),
                                  tile.get("command_topic_temp")))
                # «Стоп» у шторы - отдельный топик, не выводимый из канала
                # положения. Без него пульт получал бы 403 на кнопку остановки.
                if kind == "curtain" and tile.get("command_topic_stop"):
                    allowed.add(tile["command_topic_stop"])
                for ch, explicit in pairs:
                    topic = command_topic(ch, explicit)
                    if topic:
                        allowed.add(topic)
    return allowed


@app.route("/api/publish", methods=["POST"])
def api_publish():
    config.reload()
    try:
        body = json.loads(request.get_data(as_text=True) or "{}")
        topic = str(body["topic"])
        value = str(body["value"])
    except (ValueError, KeyError, TypeError):
        return Response('{"error":"нужны поля topic и value"}', status=400,
                        mimetype="application/json")

    if topic not in allowed_topics():
        log.warning("отклонена публикация в %s", topic)
        return Response('{"error":"топик не разрешён"}', status=403,
                        mimetype="application/json")
    if len(value) > 32:
        return Response('{"error":"слишком длинное значение"}', status=400,
                        mimetype="application/json")
    if not mqtt_client or not state.connected:
        return Response('{"error":"нет связи с MQTT"}', status=503,
                        mimetype="application/json")

    mqtt_client.publish(topic, value)
    log.info("публикация %s = %s", topic, value)
    return Response('{"ok":true}', mimetype="application/json")


@app.route("/manifest.webmanifest")
def manifest():
    """
    Делает страницу устанавливаемой. После «Добавить на главный экран»
    она запускается без адресной строки и кнопок браузера - это и есть
    киоск без сторонних приложений.
    """
    body = {
        "name": "Панель дома",
        "short_name": "Дом",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0F0F11",
        "theme_color": "#0F0F11",
        "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/manifest+json")


@app.route("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
           '<rect width="192" height="192" rx="42" fill="#1B1B1F"/>'
           '<rect x="34" y="34" width="55" height="55" rx="14" fill="#E09B2D"/>'
           '<rect x="103" y="34" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="34" y="103" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="103" y="103" width="55" height="55" rx="14" fill="#3B8FD4"/>'
           '</svg>')
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "max-age=86400"})


@app.route("/docs")
def docs():
    """Справочник по сборке панелей. Лежит рядом с panel.py отдельным файлом."""
    for candidate in ("docs.html", "templates/docs.html"):
        path = os.path.join(BASE_DIR, candidate)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return Response(fh.read(), mimetype="text/html; charset=utf-8")
    return Response("Файл docs.html не найден в %s" % BASE_DIR,
                    status=404, mimetype="text/plain; charset=utf-8")


@app.route("/<name>.svg")
def panel_svg(name):
    config.reload()
    # /main.svg?cols=2 - та же панель в две колонки, для телефона.
    # Отдельную панель в конфиге заводить не нужно.
    try:
        cols = int(request.args.get("cols") or 0) or None
    except ValueError:
        cols = None
    if cols:
        cols = max(1, min(cols, 12))
    with state.lock:
        version = state.version
    # v2 в ключе: после смены формата страницы старые записи в кэше браузера
    # не должны совпадать с новыми
    etag = '"v2-%s-%s-%d"' % (name, cols or "d", version)
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    svg = render_panel(name, cols=cols)
    if request.args.get("flat") not in ("0", "false", "no"):
        panel_conf = config.panels.get(name) or {}
        svg = resolve_css_vars(
            svg, panel_conf.get("theme", config.get("theme", "light")), 1.0)
    return Response(svg, mimetype="image/svg+xml", headers={
        "ETag": etag,
        "Cache-Control": "no-cache, must-revalidate",
        "Access-Control-Allow-Origin": "*",
    })


def panel_tiles(panel_conf):
    """Плоский список плиток панели в том же порядке, что и на картинке."""
    out = []
    for _title, tiles, _status in resolve_sections(panel_conf, config.panels):
        out.extend(tiles)
    return out


@app.route("/tile/<name>/<int:index>.svg")
def tile_svg(name, index):
    """
    Одна плитка крупным планом - для полноэкранного вида.

    Размер приходит в пикселях: страница знает про экран, сервер - нет.
    """
    config.reload()
    panel_conf = config.panels.get(name)
    if panel_conf is None:
        abort(404, "панель %r не найдена" % name)
    tiles = panel_tiles(panel_conf)
    if not 0 <= index < len(tiles):
        abort(404, "плитки %d нет на панели %r" % (index, name))

    try:
        width = max(240, min(int(request.args.get("w") or 700), 2000))
        height = max(200, min(int(request.args.get("h") or 480), 2000))
    except ValueError:
        width, height = 700, 480

    interactive = bool(panel_conf.get("interactive",
                       config.get("interactive", False)))
    tile = build_tile(tiles[index], 0, PAD, PAD, width, height,
                      panel_conf, state, history, interactive)
    scale = font_scale(height)
    svg = jinja.get_template(panel_conf.get("template", "panel.svg.j2")).render(
        uid="t%d-" % index,
        fs=scale,
        tiles=[tile], headers=[],
        width=width + PAD * 2, height=height + PAD * 2,
        rx=RX, theme=panel_conf.get("theme", config.get("theme", "light")),
        title="", updated=time.strftime("%H:%M"), connected=state.connected)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(
            svg, panel_conf.get("theme", config.get("theme", "light")), scale)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.route("/<name>.html")
def panel_html(name):
    """
    Страница панели для планшета.

    SVG не вставляется через <img>, а встраивается в разметку: только так
    по плиткам можно кликать. Скрипты внутри <img> браузер не выполняет,
    и обработчики на элементы повесить неоткуда.
    """
    config.reload()
    every = int(config.get("http", {}).get("refresh", 10))
    panel_conf = config.panels.get(name) or {}
    interactive = bool(panel_conf.get("interactive",
                       config.get("interactive", False)))

    html_page = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<!-- Запуск без рамки браузера после «Добавить на главный экран».
     mobile-web-app-capable понимают Android-браузеры, apple-* - iOS. -->
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0F0F11">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg">
<link rel="apple-touch-icon" href="icon.svg">
<title>__NAME__</title>
<style>
 /* Страница обычная и прокручиваемая. Раньше тут была раскладка «под киоск»:
    flex по центру, height:100%, overflow:hidden. На телефоне панель влезала
    по высоте и всё выглядело нормально, а на ноутбуке широкий и низкий экран
    ужимал SVG до нуля - плитки просто исчезали. И прокрутки не было вовсе. */
 html,body{margin:0;background:#0F0F11;
           -webkit-tap-highlight-color:transparent;
           font:15px -apple-system,'Segoe UI',Inter,system-ui,sans-serif}
 body{padding:10px;box-sizing:border-box;min-height:100vh}
 #host{color:#fff;margin:0 auto;max-width:1400px}
 /* размер задаётся ШИРИНОЙ: высота подстроится по пропорции сама */
 #host svg{display:block;width:100%;height:auto}
 /* Длинное нажатие в мобильных браузерах выделяет текст и показывает
    системное меню - на пультах это мешало. Запрещаем на всей панели. */
 #host,#ov{-webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;
           user-select:none;-webkit-touch-callout:none}
 [data-topic],[data-expand],[data-pad],[data-href]{cursor:pointer}
 [data-topic].busy,[data-pad].busy{opacity:.45}
 /* выбор числа колонок: значение запоминается и переносится между панелями */
 /* назад к списку панелей: маленькая, полупрозрачная, всегда на месте */
 #back{position:fixed;top:8px;left:8px;z-index:15;text-decoration:none;
       background:rgba(28,28,32,.72);border-radius:12px;color:#fff;
       padding:7px 12px;font-size:13px;opacity:.35}
 #back:hover,#back.touched{opacity:1}
 #cols{position:fixed;top:8px;right:8px;z-index:15;
       background:rgba(28,28,32,.72);border-radius:12px;padding:3px;
       font-size:13px;line-height:1;opacity:.35}
 #cols:hover,#cols.touched{opacity:1}
 #cols b{display:inline-block;padding:6px 9px;border-radius:9px;color:#fff;
         cursor:pointer;font-weight:600}
 #cols b.on{background:#E09B2D;color:#1B1B1F}
 #err{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);
      background:#C4553B;color:#fff;padding:7px 14px;border-radius:9px;
      font-size:13px;display:none;z-index:30}
 /* Порт 8080 - вход без пароля. Пока панель открыта через него, об этом
    должно быть видно: предупреждение в документации читают один раз, а
    плашку - каждый день, пока не уберёшь причину. */
 #warn8080{position:fixed;left:0;right:0;top:0;z-index:40;display:none;
           background:#8A5A1E;color:#FFE6BF;text-align:center;
           padding:6px 10px;font-size:13px;line-height:1.35}
 #warn8080 b{color:#fff}
 #warn8080 a{color:#FFE6BF;text-decoration:underline}
 body.warned{padding-top:38px}
 body.warned #back,body.warned #cols{top:42px}
 /* оверлей: тот же язык, что и у плиток - тёмный фон, скруглённая карточка */
 /* inset и min() - свежий синтаксис, старые десктопные браузеры его не знают
    и оверлей получался нулевого размера: на телефоне работало, на ноутбуке нет.
    Пишем по-старому. */
 #ov{position:fixed;top:0;left:0;right:0;bottom:0;
     background:rgba(10,10,12,.86);display:none;z-index:2147483000;
     align-items:center;justify-content:center;flex-direction:column;
     padding:18px;box-sizing:border-box;overflow:auto}
 /* именно auto по бокам: раньше тут было "7px 0", и это правило перебивало
    margin:0 auto у самих пультов - они прижимались к левому краю */
 #ov.on > *{margin:7px auto}
 #ov.on{display:block;text-align:center}
 #ov .card{background:#F1F1F3;border-radius:22px;padding:14px;max-width:100%;
           display:inline-block;box-sizing:border-box;position:relative;z-index:1}
 #ov svg{display:block;max-width:100%;height:auto}
 #ov .ttl{color:#fff;font-size:17px;font-weight:600}
 #ov .hint{color:rgba(255,255,255,.55);font-size:13px;text-align:center}
 /* переключатель в пульте: без него подвинуть яркость у выключенной лампы
    можно, а увидеть результат - нет */
 #ov .pw{display:inline-flex;align-items:center;padding:10px 18px;
         border-radius:14px;border:2px solid rgba(255,255,255,.3);
         color:#fff;font-size:15px;font-weight:600;cursor:pointer;user-select:none}
 #ov .pw.on{border-color:#E09B2D;color:#E09B2D}
 #ov .pw .led{width:12px;height:12px;border-radius:50%;margin-right:10px;
              background:rgba(255,255,255,.35)}
 #ov .pw.on .led{background:#E09B2D}
 #ov .close{position:fixed;top:16px;right:18px;color:#fff;font-size:30px;
            line-height:1;opacity:.7;cursor:pointer}
 /* пульт ленты: поле «холодный-тёплый» по X, яркость по Y */
 /* размер задаётся из скрипта: min() поддерживают не везде */
 #pad{position:relative;border-radius:20px;touch-action:none;
      overflow:hidden;margin:0 auto}
 #pad .grad{position:absolute;top:0;left:0;right:0;bottom:0}
 #pad .dim{position:absolute;top:0;left:0;right:0;bottom:0;
           background:linear-gradient(to bottom,rgba(10,10,12,0),rgba(10,10,12,.86))}
 #pad .dot{position:absolute;width:30px;height:30px;margin:-15px 0 0 -15px;
           border-radius:50%;border:3px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.4)}
 /* пульт диммера: линейный, заполнение снизу */
 #lvl{position:relative;border-radius:20px;touch-action:none;overflow:hidden;
      background:#2C2C31;margin:0 auto}
 #lvl .fill{position:absolute;left:0;right:0;bottom:0;background:#E0A050;opacity:.75}
 #lvl .val{position:absolute;left:0;right:0;bottom:14px;text-align:center;
           color:#fff;font-size:26px;font-weight:600}
 /* пульт шторы: сверху лежит прозрачный слой для жеста, под ним - та же
    картинка плитки, что и на панели, только крупно */
 /* inline-block: обёртка должна быть ровно по картинке, иначе центр жеста
    окажется в центре экрана, а не в центре окна со шторой */
 #crtwrap{position:relative;display:inline-block;margin:0 auto}
 /* z-index больше, чем у .card (там стоит 1): иначе картинка лежит поверх
    прозрачного слоя и забирает касания себе - жест просто не доходит */
 #crtview{position:absolute;left:0;right:0;top:0;bottom:0;touch-action:none;
          cursor:ew-resize;z-index:3}
 #crtview .mark{position:absolute;top:0;bottom:0;width:2px;display:none;
                background:rgba(255,255,255,.9);
                box-shadow:0 0 12px rgba(0,0,0,.7)}
 #crtview .read{position:absolute;left:0;right:0;top:42%;display:none;
                text-align:center;color:#fff;font-size:42px;font-weight:600;
                text-shadow:0 2px 12px rgba(0,0,0,.8)}
 #ov .crow{display:block;margin:10px auto 0;text-align:center}
 #ov .cbtn{display:inline-block;margin:4px;padding:10px 18px;border-radius:12px;
           border:2px solid rgba(255,255,255,.22);color:rgba(255,255,255,.85);
           font-size:15px;font-weight:600;cursor:pointer}
 #ov .cbtn.stop{border-color:#C4553B;color:#FFD8CE}
 /* пульт кондиционера */
 #wheel{margin:0 auto;user-select:none;touch-action:none}
 #ov .wbig{color:#fff;font-size:64px;font-weight:300;line-height:1;
           letter-spacing:-2px;margin:4px 0 0}
 #ov .wbig .wdeg{font-size:22px;font-weight:500;vertical-align:super;
                 margin-left:4px;letter-spacing:0}
 #ov .acrow{display:block;margin:8px auto;max-width:460px}
 #ov .acbtn{display:inline-block;margin:4px;padding:9px 14px;border-radius:12px;
            border:2px solid rgba(255,255,255,.22);color:rgba(255,255,255,.8);
            font-size:14px;font-weight:600;cursor:pointer}
 #ov .acbtn.on{border-color:#8E6BC4;color:#fff;background:rgba(142,107,196,.22)}
</style></head><body>
<a id="back" href="./">← Панели</a>
<div id="cols"></div>
<div id="host">загрузка…</div>
<div id="err"></div>
<div id="warn8080"></div>
<div id="ov"><span class="close" onclick="closeOv()">&times;</span></div>

<script>
var NAME = __NAME_JS__, EVERY = __EVERY__000;

/* Запасной вход на 8080 не закрыт паролем: кто попал в домашнюю сеть, тот
   управляет светом и шторами. Годится, чтобы попробовать, но не как
   постоянный режим - поэтому и написано «тестовый». */
(function(){
  if (location.port !== '8080') return;
  var w = document.getElementById('warn8080');
  if (!w) return;
  w.innerHTML = '<b>Тестовый режим:</b> открыто через порт 8080, без пароля. ' +
                'Постоянный адрес — <a href="/panel/' + NAME + '.html">/panel/</a>';
  w.style.display = 'block';
  document.body.className += ' warned';
})();

/* Число колонок.
   Берём из адреса, иначе из памяти браузера, иначе оставляем как в конфиге.
   Запоминаем, чтобы при переходе на другую панель раскладка не сбрасывалась:
   у горизонтальных экранов это каждый раз раздражало. */
var STORE = 'wb-panel-cols';
function readCols(){
  var m = location.search.match(/[?&]cols=(\d+)/);
  if (m) return m[1];
  try { return localStorage.getItem(STORE) || ''; } catch(e) { return ''; }
}
var COLS = readCols();
function saveCols(v){
  COLS = v;
  try { v ? localStorage.setItem(STORE, v) : localStorage.removeItem(STORE); } catch(e) {}
}
function Q(){ return COLS ? ('?cols=' + COLS) : ''; }

/* ссылка «назад» тоже должна нести выбранное число колонок */
function fixBack(){
  var b = document.getElementById('back');
  if (b) b.href = './' + Q();
}

function drawCols(){
  fixBack();
  var box = document.getElementById('cols'), html = '';
  var opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'];
  for (var i = 0; i < opts.length; i++) {
    var v = opts[i];
    html += '<b data-v="' + v + '"' + (v === COLS ? ' class="on"' : '') + '>'
          + (v === '' ? 'авто' : v) + '</b>';
  }
  box.innerHTML = html;
  var btns = box.getElementsByTagName('b');
  for (var j = 0; j < btns.length; j++) {
    btns[j].onclick = function(){
      saveCols(this.getAttribute('data-v'));
      drawCols();
      refresh();
    };
  }
  box.className = 'touched';
  setTimeout(function(){ box.className = ''; }, 1800);
}
var INTERACTIVE = __INTERACTIVE__;
var host = document.getElementById('host'), err = document.getElementById('err');
var ov = document.getElementById('ov');
var paused = false, press = null;

/* Координаты события.
   ВАЖНО про touchend: у него список touches ПУСТОЙ - палец уже оторван.
   Пустой TouchList при этом истинный, поэтому наивная проверка
   "ev.touches ? ev.touches[0] : ev" давала undefined и падала на .clientX.
   Из-за этого ползунки двигались, а значение не отправлялось: исключение
   происходило ровно в момент отпускания, до вызова pub(). */
function evPoint(ev){
  if(ev.touches && ev.touches.length) return ev.touches[0];
  if(ev.changedTouches && ev.changedTouches.length) return ev.changedTouches[0];
  return ev;
}

function flash(t){ err.textContent=t; err.style.display='block';
  setTimeout(function(){ err.style.display='none'; }, 2600); }

function pub(topic, value){
  return fetch('api/publish', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({topic:topic, value:String(value)})
  }).then(function(r){
    if(!r.ok) return r.json().then(function(j){ throw new Error(j.error||r.status); });
  });
}

/* ---------- оверлей ---------- */
function closeOv(){ ov.classList.remove('on'); paused=false;
  var k=ov.querySelector('.card,#pad,#lvl,#crtwrap,.ttl,.hint');
  while(ov.children.length>1) ov.removeChild(ov.lastChild); refresh(); }
ov.addEventListener('click', function(e){ if(e.target===ov) closeOv(); });
document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeOv(); });

function openOv(nodes){ 
  while(ov.children.length>1) ov.removeChild(ov.lastChild);
  nodes.forEach(function(n){ ov.appendChild(n); });
  ov.classList.add('on'); paused=true;
}
function el(tag, cls, text){ var e=document.createElement(tag);
  if(cls) e.className=cls; if(text) e.textContent=text; return e; }

/* Переключатель для пульта. Возвращает объект с текущим состоянием, чтобы
   ползунки знали, надо ли сперва включить прибор. */
function powerRow(node){
  var st = {on: node.getAttribute('data-state') === '1'};
  var row = el('div', 'pw');
  var led = el('span', 'led'), label = el('span');
  row.appendChild(led); row.appendChild(label);
  function paint(){
    row.classList.toggle('on', st.on);
    label.textContent = st.on ? 'Включено' : 'Выключено';
  }
  paint();
  row.onclick = function(){
    var topic = node.getAttribute('data-topic');
    if(!topic) return;
    var next = !st.on;
    pub(topic, next ? node.getAttribute('data-on') : node.getAttribute('data-off'))
      .then(function(){ st.on = next; paint(); })
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  };
  st.el = row;
  // включить прибор, если крутят яркость у выключенного
  st.ensureOn = function(){
    var topic = node.getAttribute('data-topic');
    if(st.on || !topic) return Promise.resolve();
    return pub(topic, node.getAttribute('data-on')).then(function(){
      st.on = true; paint();
    });
  };
  return st;
}

/* ---------- график на весь экран ---------- */
function expand(node){
  var i = node.getAttribute('data-i');
  var w = Math.min(Math.round(window.innerWidth * 0.90), 1400);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 900);
  var url = 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h;
  var card = el('div','card');
  card.textContent = '…';
  var hint = el('div','hint','Нажмите вне карточки, чтобы закрыть');
  openOv([card, hint]);
  // если оверлей по какой-то причине не показался, откроем картинку отдельно
  if(!ov.offsetHeight){ closeOv(); window.open(url, '_blank'); return; }
  fetch(url, {cache:'no-store'})
    .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function(svg){
      if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
      card.innerHTML = svg;
    })
    .catch(function(e){
      card.textContent = 'Не удалось: ' + e.message;
      flash('Полный экран: ' + e.message);
    });
}

/* ---------- пульт ленты: X - температура, Y - яркость ---------- */
function padXY(node){
  var pad = el('div'); pad.id='pad';
  var side = Math.round(Math.min(window.innerWidth*0.78, window.innerHeight*0.6));
  pad.style.width = side + 'px'; pad.style.height = side + 'px';
  var grad = el('div','grad'), dim = el('div','dim'), dot = el('div','dot');
  grad.style.background = 'linear-gradient(to right,' +
      node.getAttribute('data-cold') + ',' + node.getAttribute('data-warm') + ')';
  pad.appendChild(grad); pad.appendChild(dim); pad.appendChild(dot);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Подсветка');
  var hint = el('div','hint','Слева холодный, справа тёплый · вверх ярче');
  var power = powerRow(node);
  var x = parseFloat(node.getAttribute('data-dot-x')||'0.5');
  var y = parseFloat(node.getAttribute('data-dot-y')||'0.5');
  function place(){ dot.style.left=(x*100)+'%'; dot.style.top=(y*100)+'%';
    dot.style.background = 'transparent'; }
  place();
  openOv([title, pad, power.el, hint]);

  function at(ev){
    var r = pad.getBoundingClientRect();
    var p = evPoint(ev);
    x = Math.max(0, Math.min(1, (p.clientX-r.left)/r.width));
    y = Math.max(0, Math.min(1, (p.clientY-r.top)/r.height));
    place();
  }
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    var bright = Math.round((1-y)*bmax);
    var jobs = [pub(node.getAttribute('data-bright'), bright)];
    var tTopic = node.getAttribute('data-temp');
    if(tTopic){
      var unit = node.getAttribute('data-temp-unit');
      var tmax = parseFloat(node.getAttribute('data-temp-max')||'100');
      var lo = parseFloat(node.getAttribute('data-temp-lo')||'2700');
      var hi = parseFloat(node.getAttribute('data-temp-hi')||'6500');
      // слева холодный: x=0 -> hi кельвинов
      var value = (unit === 'percent') ? Math.round((1-x)*tmax)
                                       : Math.round(hi - x*(hi-lo));
      jobs.push(pub(tTopic, value));
    }
    // если лента выключена, сперва включаем - иначе крутить бесполезно
    power.ensureOn()
      .then(function(){ return Promise.all(jobs); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  drag(pad, at, send);
}

/* ---------- пульт кондиционера ----------
   Колесо сверху: шкала едет под неподвижной меткой по центру, как в
   приложении. Тянешь влево - значения растут. Ниже режимы, питание и
   скорость вентилятора. */
function padAC(node){
  var lo = parseFloat(node.getAttribute('data-ac-lo')||'16');
  var hi = parseFloat(node.getAttribute('data-ac-hi')||'30');
  var step = parseFloat(node.getAttribute('data-ac-step')||'1');
  // pos - непрерывное положение шкалы, temp - значение, которое отправим.
  // Раньше шкала перерисовывалась от округлённого значения, из-за чего
  // засечки всегда попадали в те же места и колесо казалось неподвижным.
  var temp = parseFloat(node.getAttribute('data-ac-now'));
  if(isNaN(temp)) temp = Math.round((lo+hi)/2);
  var pos = temp;

  var title = el('div','ttl', node.getAttribute('data-title') || 'Кондиционер');
  var cur = node.getAttribute('data-ac-cur');
  // Крупно сверху - ФАКТИЧЕСКАЯ температура: уставку и так видно на шкале,
  // дублировать её незачем.
  var big = el('div','wbig');
  big.innerHTML = (cur || '--').replace('°','') + '<span class="wdeg">°C</span>';
  var sub = el('div','hint','в комнате · уставку задайте колесом');

  var W = Math.min(window.innerWidth - 30, 470), H = 190;
  var wrap = el('div'); wrap.id = 'wheel';
  wrap.style.width = W + 'px'; wrap.style.height = H + 'px';
  wrap.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">'
    + '<path class="warc2" fill="none" stroke="rgba(255,255,255,.14)" stroke-width="1.5"/>'
    + '<path class="wnotch" fill="none" stroke="rgba(255,255,255,.45)" stroke-width="1.6"/>'
    + '<g class="wticks"></g>'
    + '<path class="warc" fill="none" stroke="#B08BE8" stroke-width="3"/>'
    + '</svg>';
  // ВАЖНО: узлы ещё не в документе, поэтому ищем внутри wrap.
  // Через document.getElementById здесь приходит null, и пульт падал молча.
  var gTicks = wrap.querySelector('.wticks');
  var pArc   = wrap.querySelector('.warc');
  var pArc2  = wrap.querySelector('.warc2');
  var pNotch = wrap.querySelector('.wnotch');

  /* Дуга «улыбкой»: центр окружности НАД картинкой, поэтому середина шкалы
     внизу, а края поднимаются - как в приложении. Раньше центр был снизу и
     дуга выгибалась в другую сторону. */
  var PXDEG = W / 5, R = W * 1.7, CX = W / 2, BOTTOM = H - 34, CY = BOTTOM - R;
  function pt(delta, radius){
    var a = (delta * PXDEG) / R;
    var r = radius === undefined ? R : radius;
    return [CX + r * Math.sin(a), CY + r * Math.cos(a), a];
  }
  function arcPath(radius, half){
    var p0 = pt(-half, radius), p1 = pt(half, radius);
    return 'M ' + p0[0].toFixed(1) + ',' + p0[1].toFixed(1)
         + ' A ' + radius.toFixed(0) + ',' + radius.toFixed(0)
         + ' 0 0 0 ' + p1[0].toFixed(1) + ',' + p1[1].toFixed(1);
  }
  function draw(){
    var s = '', v, from = Math.ceil((pos - 3.2) * 5) / 5;
    for(v = from; v <= pos + 3.2; v += 0.2){
      if(v < lo - 0.001 || v > hi + 0.001) continue;
      var p = pt(v - pos), whole = Math.abs(v - Math.round(v)) < 0.05;
      var len = whole ? 12 : 7;
      // засечки смотрят наружу от центра окружности, то есть вниз
      var x2 = p[0] + Math.sin(p[2]) * len, y2 = p[1] + Math.cos(p[2]) * len;
      s += '<line x1="' + p[0].toFixed(1) + '" y1="' + p[1].toFixed(1)
         + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1)
         + '" stroke="#fff" stroke-width="' + (whole ? 1.7 : 1)
         + '" opacity="' + (whole ? .7 : .3) + '"/>';
      if(whole){
        // подписи с внутренней стороны дуги, то есть выше неё
        var lx = p[0] - Math.sin(p[2]) * 20, ly = p[1] - Math.cos(p[2]) * 20;
        var near = Math.abs(v - temp) < 0.05;
        s += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1)
           + '" text-anchor="middle" font-size="' + (near ? 19 : 16)
           + '" fill="rgba(255,255,255,' + (near ? .92 : .45) + ')">'
           + Math.round(v) + '°</text>';
      }
    }
    gTicks.innerHTML = s;
    pArc.setAttribute('d', arcPath(R, 3.2));
    pArc2.setAttribute('d', arcPath(R - 74, 2.6));
    var n = pt(0, R - 74);
    pNotch.setAttribute('d', 'M ' + (n[0] - 8) + ',' + (n[1] - 9)
                           + ' L ' + n[0] + ',' + (n[1] + 1)
                           + ' L ' + (n[0] + 8) + ',' + (n[1] - 9));
  }
  draw();

  var startX = null, startPos = pos;
  function begin(ev){
    startX = evPoint(ev).clientX; startPos = pos;
  }
  function at(ev){
    if(startX === null) begin(ev);
    pos = startPos - (evPoint(ev).clientX - startX) / PXDEG;  // влево - теплее
    pos = Math.max(lo, Math.min(hi, pos));
    temp = Math.max(lo, Math.min(hi, Math.round(pos / step) * step));
    draw();
  }
  function send(){
    startX = null;
    pos = temp;                       // защёлкиваем шкалу на выбранном делении
    draw();
    var t = node.getAttribute('data-ac-target');
    if(t) pub(t, temp).catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  drag(wrap, at, send, begin);

  function chooser(list, attr, nowValue){
    var row = el('div','acrow');
    list.forEach(function(item){
      var b = el('div', 'acbtn' + (item[0] === nowValue ? ' on' : ''), item[1]);
      b.onclick = function(){
        var topic = node.getAttribute(attr);
        if(!topic) return;
        pub(topic, item[0]).then(function(){
          var all = row.querySelectorAll('.acbtn');
          for(var i = 0; i < all.length; i++) all[i].classList.remove('on');
          b.classList.add('on');
        }).catch(function(e){ flash('Не удалось: ' + e.message); });
      };
      row.appendChild(b);
    });
    return row;
  }

  var modes = chooser([['cool','Охлаждение'],['heat','Нагрев'],['dry','Осушение'],
                       ['fan_only','Вентиляция'],['auto','Авто']],
                      'data-ac-mode', node.getAttribute('data-ac-mode-now'));
  var fans = chooser([['low','1'],['medium','2'],['high','3'],['auto','Авто']],
                     'data-ac-fan', node.getAttribute('data-ac-fan-now'));
  var power = powerRow(node);
  var parts = [title, big, wrap, sub, modes,
               el('div','hint','Скорость вентилятора'), fans];
  if(node.getAttribute('data-ac-swing')){
    parts.push(el('div','hint','Лопасти вертикальные'));
    parts.push(chooser([['auto','Авто'],['off','Выкл'],['upper','Верх'],
                        ['position_2','2'],['position_3','3'],['position_4','4'],
                        ['bottom','Низ']],
                       'data-ac-swing', node.getAttribute('data-ac-swing-now')));
  }
  if(node.getAttribute('data-ac-swing-h')){
    parts.push(el('div','hint','Лопасти горизонтальные'));
    // кнопки идут слева направо, как реальные положения: 4·5·1·6·7
    parts.push(chooser([['auto','Авто'],['position_4','◀◀'],['position_5','◀'],
                        ['position_1','●'],['position_6','▶'],['position_7','▶▶']],
                       'data-ac-swing-h', node.getAttribute('data-ac-swing-h-now')));
  }
  parts.push(power.el);
  openOv(parts);
}

/* ---------- пульт диммера: линейный ---------- */
function padLevel(node){
  var box = el('div'); box.id='lvl';
  box.style.width = Math.round(Math.min(window.innerWidth*0.5, 220)) + 'px';
  box.style.height = Math.round(Math.min(window.innerHeight*0.5, 380)) + 'px';
  var fill = el('div','fill'), val = el('div','val');
  box.appendChild(fill); box.appendChild(val);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Яркость');
  var hint = el('div','hint','Ведите вверх или вниз');
  var power = powerRow(node);
  var lvl = parseFloat(node.getAttribute('data-level')||'0');
  function place(){ fill.style.height=(lvl*100)+'%'; val.textContent=Math.round(lvl*100)+' %'; }
  place();
  openOv([title, box, power.el, hint]);
  function at(ev){
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    lvl = Math.max(0, Math.min(1, 1-(p.clientY-r.top)/r.height));
    place();
  }
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    power.ensureOn()
      .then(function(){ return pub(node.getAttribute('data-bright'), Math.round(lvl*bmax)); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  drag(box, at, send);
}

/* Пульт шторы.
   Полоса задаёт положение, кнопки под ней - «закрыть / стоп / открыть».
   Пока палец ведёт, показываем то, что он выбрал; отпустил - отправляем
   одно значение. Промежуточные значения слать нельзя: у привода каждая
   команда это новая цель, и он дёргался бы за пальцем. */
function padCurtain(node){
  /* Раскрытая штора - это та же плитка, только крупно: рисует её сервер тем
     же макросом, что и на панели. Своей вёрстки тут нет намеренно, иначе
     пришлось бы поддерживать два разных изображения одной вещи.
     Поверх картинки лежит прозрачный слой: ведём правую створку от центра
     к правому краю, как саму штору руками. */
  var i = node.getAttribute('data-i');
  var w = Math.min(Math.round(window.innerWidth * 0.90), 1400);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 900);
  var url = 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h;

  var card = el('div','card'); card.textContent = '…';
  var wrap = el('div'); wrap.id = 'crtwrap';
  var view = el('div'); view.id = 'crtview';
  var mark = el('div','mark');            // куда поедет
  var read = el('div','read');            // проценты крупно
  view.appendChild(mark); view.appendChild(read);
  wrap.appendChild(card); wrap.appendChild(view);

  var title = el('div','ttl', node.getAttribute('data-title') || 'Штора');
  var hint  = el('div','hint','Ведите правую створку от центра к краю');
  var pos   = parseFloat(node.getAttribute('data-pos')||'0');
  var shown = pos;

  function paint(){
    // 0 - створка у центра (закрыто), 1 - у правого края (открыто)
    var svg = card.querySelector('svg');
    if(svg){
      var rs = svg.getBoundingClientRect(), rv = view.getBoundingClientRect();
      var left = rs.left - rv.left, mid = left + rs.width/2;
      mark.style.left = Math.round(mid + shown*rs.width/2) + 'px';
    } else {
      mark.style.left = (50 + shown*50) + '%';
    }
    read.textContent = Math.round(shown*100) + ' %';
    mark.style.display = read.style.display = dragging ? 'block' : 'none';
  }
  var dragging = false;

  function load(){
    fetch(url + '&t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        card.innerHTML = svg;
      })
      .catch(function(e){ card.textContent = 'Не удалось: ' + e.message; });
  }
  load();

  var row = el('div','crow');
  function button(text, cls, fn){
    var b = el('span','cbtn'+(cls?' '+cls:''), text);
    b.onclick = fn; row.appendChild(b); return b;
  }
  function send(value){
    pub(node.getAttribute('data-topic'), value)
      .then(function(){
        setTimeout(load, 700); setTimeout(load, 2500); setTimeout(load, 6000);
        setTimeout(refresh, 800);
      })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  button('Закрыть', '', function(){ send(node.getAttribute('data-off')); });
  /* Стоп есть не у всех приводов: у HomeKit-совместимых это отдельная
     характеристика, и топик задаётся в конфиге явно. Нет топика - нет кнопки:
     рисовать неработающую хуже, чем не рисовать вовсе. */
  var stopTopic = node.getAttribute('data-stop');
  if(stopTopic){
    button('Стоп', 'stop', function(){
      pub(stopTopic, node.getAttribute('data-stop-value') || '2')
        .then(function(){ setTimeout(load, 700); setTimeout(load, 2000); })
        .catch(function(e){ flash('Не удалось: '+e.message); });
    });
  }
  button('Открыть', '', function(){ send(node.getAttribute('data-on')); });

  openOv([title, wrap, row, hint]);
  if(!ov.offsetHeight){ closeOv(); window.open(url, '_blank'); return; }

  function at(ev){
    // Считаем от самого рисунка: у карточки есть поля, и если брать её
    // границы, центр окна и центр жеста разъезжаются на ширину поля.
    var box = card.querySelector('svg') || view;
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    // центр картинки = створки сомкнуты, правый край = открыто настежь
    shown = Math.max(0, Math.min(1, (p.clientX - (r.left + r.width/2)) / (r.width/2)));
    paint();
  }
  function begin(ev){ dragging = true; at(ev); }
  function done(){
    dragging = false; paint();
    pos = shown;
    // Промежуточные значения не шлём: для привода каждое - новая цель,
    // и он дёргался бы за пальцем.
    send(Math.round(shown*100));
  }
  drag(view, at, done, begin);
}

/* Перетаскивание: pointer-события есть не везде, поэтому с запасным
   вариантом на мышь и касания. */
function drag(node, move, done, begin){
  var active = false;
  function start(e){
    active = true;
    try { if(e.pointerId != null && node.setPointerCapture) node.setPointerCapture(e.pointerId); }
    catch(_) {}
    if(begin) begin(e); else move(e);
    if(e.preventDefault) e.preventDefault();
  }
  function go(e){ if(active){ move(e); if(e.preventDefault) e.preventDefault(); } }
  function end(e){
    if(!active) return;
    active = false;
    // move() может споткнуться на кривом событии - но отправить значение
    // мы обязаны в любом случае, иначе жест пропадает впустую
    try { move(e); } catch(_) {}
    done();
  }
  /* Отмена жеста - это НЕ движение. Раньше здесь вызывался move() с событием
     pointercancel, у которого координат нет, и значение прыгало в край.
     Браузер шлёт отмену, стоит пальцу чуть сместиться по вертикали. */
  function cancel(){ if(!active) return; active = false; done(); }
  if(window.PointerEvent){
    node.addEventListener('pointerdown', start);
    node.addEventListener('pointermove', go);
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', cancel);
  } else {
    node.addEventListener('mousedown', start);
    document.addEventListener('mousemove', go);
    document.addEventListener('mouseup', end);
    node.addEventListener('touchstart', start);
    node.addEventListener('touchmove', go);
    node.addEventListener('touchend', end);
    node.addEventListener('touchcancel', cancel);
  }
}

/* ---------- нажатия по плиткам ---------- */
function toggle(node){
  var value = node.getAttribute('data-state')==='1'
            ? node.getAttribute('data-off') : node.getAttribute('data-on');
  node.classList.add('busy');
  pub(node.getAttribute('data-topic'), value).then(function(){
    setTimeout(refresh, 400); setTimeout(refresh, 1200);
  }).catch(function(e){ flash('Не удалось: '+e.message); node.classList.remove('busy'); });
}

function bind(){
  if(!INTERACTIVE) return;
  var nodes = host.querySelectorAll('[data-topic],[data-expand],[data-pad],[data-href]');
  for(var i=0;i<nodes.length;i++){
    var n = nodes[i];
    if(n.getAttribute('data-expand')){ n.onclick=function(){ expand(this); }; continue; }
    // короткое нажатие переключает, долгое открывает пульт
    // Короткое нажатие переключает, долгое открывает пульт.
    // Раньше случайные включения ловились из-за двух вещей: жест не
    // отменялся при сдвиге пальца (прокрутка засчитывалась как нажатие) и
    // порог был коротковат. Теперь следим за смещением и держим 600 мс.
    var down = function(e){
      var self = this, p = evPoint(e);
      press = {node:self, long:false, x:p.clientX, y:p.clientY, moved:false,
        timer:setTimeout(function(){
          if(press && !press.moved){ press.long = true; openPad(self); }
        }, 600)};
    };
    var moved = function(e){
      if(!press) return;
      var p = evPoint(e);
      if(Math.abs(p.clientX - press.x) > 12 || Math.abs(p.clientY - press.y) > 12){
        press.moved = true;
        clearTimeout(press.timer);
      }
    };
    var up = function(){
      if(!press) return;
      clearTimeout(press.timer);
      var href = this.getAttribute('data-href');
      if(!press.long && !press.moved){
        if(href) { location.href = href + Q(); }
        else if(this.getAttribute('data-topic')) toggle(this);
      }
      press = null;
    };
    var cancel = function(){ if(press){ clearTimeout(press.timer); press=null; } };
    if(window.PointerEvent){
      n.onpointerdown = down; n.onpointermove = moved;
      n.onpointerup = up; n.onpointercancel = cancel;
    } else {
      n.onmousedown = down; n.onmousemove = moved;
      n.onmouseup = up; n.onmouseleave = cancel;
      n.ontouchstart = down; n.ontouchmove = moved;
      n.ontouchend = up; n.ontouchcancel = cancel;
    }
    // долгое нажатие не должно выделять текст и звать меню браузера
    n.oncontextmenu = function(e){ if(e.preventDefault) e.preventDefault(); return false; };
  }
}
function openPad(node){
  var pad = node.getAttribute('data-pad');
  if(pad==='xy') padXY(node);
  else if(pad==='level') padLevel(node);
  else if(pad==='curtain') padCurtain(node);
  else if(pad==='ac') padAC(node);
}

/* ---------- обновление ---------- */
function refresh(){
  if(paused) return;
  var q = Q();
  fetch(NAME + '.svg' + q + (q ? '&' : '?') + 't=' + Date.now(), {cache:'no-store'})
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); })
    .then(function(svg){
      if(svg.indexOf('<svg')<0) throw new Error('пустой ответ');
      host.innerHTML = svg; bind(); err.style.display='none';
    })
    .catch(function(e){
      if(host.querySelector('svg')) return;
      host.textContent = 'Не удалось загрузить панель: ' + e.message;
    });
}
/* Экран не должен гаснуть.
   Сперва пробуем штатный способ, он есть в свежих браузерах. Если его нет -
   заводим беззвучное зацикленное видео: пока что-то воспроизводится, Android
   держит экран включённым. Приём старый, работает и на Android 5. */
var wakeLock = null;
function keepAwake(){
  if (navigator.wakeLock && navigator.wakeLock.request) {
    navigator.wakeLock.request('screen').then(function(l){
      wakeLock = l;
      l.addEventListener('release', function(){ wakeLock = null; });
    }).catch(function(){ videoAwake(); });
  } else {
    videoAwake();
  }
}
function videoAwake(){
  if (document.getElementById('nosleep')) return;
  var v = document.createElement('video');
  v.id = 'nosleep';
  v.setAttribute('playsinline', ''); v.setAttribute('muted', '');
  v.setAttribute('loop', ''); v.muted = true; v.loop = true;
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
  // односекундный чёрный кадр, меньше килобайта
  v.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAAr1tZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE0OCByMjY0MyA1YzY1NzA0IC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAxNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbAAAAAFliIQA';
  document.body.appendChild(v);
  var p = v.play();
  if (p && p.catch) p.catch(function(){ /* нужен жест пользователя */ });
  // если браузер потребовал жест - запустим при первом касании
  document.addEventListener('touchstart', function once(){
    v.play(); document.removeEventListener('touchstart', once);
  });
}
keepAwake();
document.addEventListener('visibilitychange', function(){
  if (!document.hidden && !wakeLock) keepAwake();
});

/* Полный экран по двойному касанию - на случай, если страница открыта
   обычной вкладкой, а не с главного экрана. */
document.addEventListener('dblclick', function(){
  var el = document.documentElement;
  var fn = el.requestFullscreen || el.webkitRequestFullscreen ||
           el.webkitRequestFullScreen || el.mozRequestFullScreen;
  if (fn) fn.call(el);
});

drawCols();
refresh();
setInterval(refresh, EVERY);
</script></body></html>"""
    for mark, value in (
            ("__NAME__", html.escape(name)),
            ("__NAME_JS__", json.dumps(name)),
            ("__EVERY__", str(every)),
            ("__INTERACTIVE__", "true" if interactive else "false")):
        html_page = html_page.replace(mark, value)

    return Response(html_page, mimetype="text/html; charset=utf-8")


# какой тип плитки обычно подходит для типа канала Wiren Board
TILE_HINT = {
    "switch": "switch", "pushbutton": "switch", "alarm": "switch",
    "range": "light · curtain", "rgb": "—",
    "value": "chart · value", "temperature": "chart", "rel_humidity": "chart",
    "voltage": "chart", "current": "chart", "power": "chart",
    "power_consumption": "chart", "resistance": "chart", "pressure": "chart",
    "concentration": "chart", "lux": "chart", "sound_level": "chart",
    "text": "value",
}


@app.route("/channels")
def channels():
    """
    Браузер каналов. На контроллере их бывают сотни, поэтому:
      * сгруппированы по устройствам;
      * фильтр работает мгновенно, прямо в браузере;
      * отмечено, у каких каналов есть история — только по ним можно график;
      * имя копируется в буфер одной кнопкой, чтобы не набирать руками.

    curl -s localhost:8088/channels?format=text — то же самое для консоли.
    """
    query = request.args.get("q", "").strip().lower()
    with state.lock:
        values = dict(state.values)
        metas = dict(state.meta)
        stamps = dict(state.stamps)
    logged = history.channels_with_history() if history else {}
    used = config.used_channels()
    now = time.time()

    keys = sorted(values)
    if query:
        keys = [k for k in keys if query in k.lower()]

    if request.args.get("format") == "text":
        lines = ["%-54s %-14s %-16s %s" % ("КАНАЛ", "ЗНАЧЕНИЕ", "ТИП", "ИСТОРИЯ")]
        for key in keys:
            meta = metas.get(key, {})
            lines.append("%-54s %-14s %-16s %s" % (
                key, str(values.get(key, ""))[:14], meta.get("type", ""),
                "да" if key in logged else "—"))
        body = "\n".join(lines) + "\n\nвсего: %d\n" % len(keys)
        return Response(body, mimetype="text/plain; charset=utf-8")

    # --- группировка по устройству ---
    groups = {}
    for key in keys:
        device = key.split("/", 1)[0]
        groups.setdefault(device, []).append(key)

    esc = html.escape
    rows = []
    for device in sorted(groups):
        rows.append('<tr class="dev"><td colspan="5">%s <span class="n">%d</span></td></tr>'
                    % (esc(device), len(groups[device])))
        for key in groups[device]:
            meta = metas.get(key, {})
            ctype = str(meta.get("type", ""))
            units = str(meta.get("units", ""))
            value = str(values.get(key, ""))
            age = now - stamps.get(key, 0)
            hist = logged.get(key)
            rows.append(
                '<tr data-k="%s">'
                '<td class="k"><button onclick="cp(this)" data-c="%s">⧉</button>'
                '<code>%s</code>%s</td>'
                '<td class="v">%s <span class="u">%s</span></td>'
                '<td class="t">%s</td>'
                '<td class="h">%s</td>'
                '<td class="g">%s</td></tr>' % (
                    esc(key.lower()), esc(key), esc(key.split("/", 1)[1]),
                    ' <span class="used">в панели</span>' if key in used else "",
                    esc(value[:20]), esc(units),
                    esc(ctype),
                    ('<span class="ok">история%s</span>' %
                     ((" · %s" % hist) if hist else "")) if key in logged else "",
                    esc(TILE_HINT.get(ctype, "")),
                ))

    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Каналы контроллера</title><style>
 body{font:14px/1.45 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:16px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 #q{width:100%%;max-width:520px;padding:10px 13px;font-size:15px;
    border:1.5px solid rgba(60,60,67,.2);border-radius:11px;background:#fff;
    box-sizing:border-box}
 table{border-collapse:collapse;width:100%%;margin-top:14px;background:#fff;
       border-radius:12px;overflow:hidden}
 td{padding:6px 12px;border-bottom:1px solid #EDEDF0;vertical-align:top}
 tr.dev td{background:#E6E6E9;font-weight:600;padding:9px 12px;
           position:sticky;top:0}
 tr.dev .n{color:#8A8A90;font-weight:400;margin-left:6px}
 code{font:13px ui-monospace,Menlo,Consolas,monospace}
 button{border:none;background:none;cursor:pointer;color:#8A8A90;
        font-size:14px;margin-right:7px;padding:0}
 button:hover{color:#E09B2D}
 .v{font-weight:500;white-space:nowrap}
 .u,.t,.g{color:#8A8A90;font-size:13px}
 .ok{color:#1F8F4E;font-size:12.5px}
 .used{background:#E09B2D;color:#fff;font-size:11px;padding:1px 6px;
       border-radius:6px;margin-left:7px}
 .hide{display:none}
</style></head><body>
<h1>Каналы контроллера</h1>
<div class="sub">Всего %(total)d. Скопируйте имя кнопкой ⧉ и вставьте в
<code>config.yaml</code>. График можно построить только по каналам с пометкой
«история» — остальные не пишутся в <code>wb-mqtt-db</code>.</div>
<input id="q" placeholder="фильтр: temp, curtain, wb-mr6c…" autofocus>
<table id="t">%(rows)s</table>
<script>
 var q=document.getElementById('q'), rs=document.querySelectorAll('#t tr');
 q.oninput=function(){var s=q.value.toLowerCase();
   rs.forEach(function(r){
     if(r.className==='dev'){r.classList.toggle('hide',s!=='');return;}
     r.classList.toggle('hide', s && r.dataset.k.indexOf(s)<0);});};
 function cp(b){navigator.clipboard.writeText(b.dataset.c);
   var o=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=o},900);}
</script></body></html>""" % {"total": len(keys), "rows": "".join(rows)}
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/healthz")
def healthz():
    with state.lock:
        body = {"mqtt": state.connected, "channels": len(state.values),
                "version": state.version, "panels": sorted(config.panels),
                "hidden": sorted(n for n, p in config.panels.items()
                                 if (p or {}).get("hidden")),
                "history": history.mode if history else None,
                "template_dirs": TEMPLATE_DIRS}
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/json")


# ==========================================================================
#  Запуск
# ==========================================================================

def make_client(client_id):
    """paho-mqtt 1.x на Debian 11 и 2.x в новых сборках требуют разного конструктора."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id)


def main():
    global config, history, jinja

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(message)s")

    config = Config(CONFIG_PATH)
    state.set_watched(config.used_channels())

    jinja = Environment(
        loader=FileSystemLoader(TEMPLATE_DIRS),
        autoescape=True,
        auto_reload=True,          # правки шаблона подхватываются без рестарта
        trim_blocks=True,
        lstrip_blocks=True,
    )

    mqtt_conf = config.get("mqtt", {}) or {}
    client_id = mqtt_conf.get("client_id", "wb-svg-panel-%d" % os.getpid())
    client = make_client(client_id)
    if mqtt_conf.get("username"):
        client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

    global mqtt_client
    mqtt_client = client
    rpc = MqttRpc(client, client_id)
    hconf = config.get("history", {}) or {}
    history = History(rpc,
                      db_path=hconf.get("db_path", DB_PATH),
                      ttl=int(hconf.get("cache_ttl", 60)),
                      prefer=hconf.get("source", "auto"))

    subscribed_raw = set()

    def sync_raw_topics(cli):
        """Подписаться на топики сторонних шлюзов, упомянутые в конфиге."""
        wanted = config.raw_topics()
        new = wanted - subscribed_raw
        if new:
            cli.subscribe([(t, 0) for t in sorted(new)])
            subscribed_raw.update(new)
            log.info("подписка на сторонние топики: +%d (всего %d)",
                     len(new), len(subscribed_raw))

    def on_connect(cli, userdata, flags, rc, *args):
        if rc != 0:
            log.error("MQTT: отказ подключения, код %s", rc)
            return
        state.connected = True
        cli.subscribe([("/devices/+/controls/+", 0),
                       ("/devices/+/controls/+/meta", 0),
                       ("/devices/+/controls/+/meta/+", 0),
                       (rpc.reply_topic_filter(), 0)])
        subscribed_raw.clear()
        sync_raw_topics(cli)
        for extra in (mqtt_conf.get("extra_topics") or []):
            cli.subscribe(extra, 0)
            log.info("подписка на маску: %s", extra)
        log.info("MQTT подключён, подписки оформлены")

    def on_disconnect(cli, userdata, rc, *args):
        state.connected = False
        log.warning("MQTT отключён (код %s), paho переподключится сам", rc)

    def on_message(cli, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        if msg.topic.startswith("/rpc/v1/"):
            rpc.on_reply(payload)
        else:
            state.on_message(msg.topic, payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.connect_async(mqtt_conf.get("host", "localhost"),
                         int(mqtt_conf.get("port", 1883)), 60)
    client.loop_start()

    stop = threading.Event()
    threading.Thread(target=history.prefetch_loop, args=(config, stop),
                     daemon=True, name="history").start()

    def watch_config(stop_event):
        """Появились новые сторонние топики в конфиге - подписываемся на лету."""
        while not stop_event.wait(15):
            try:
                config.reload()
                if state.connected:
                    sync_raw_topics(client)
            except Exception:                          # noqa: BLE001
                log.exception("сбой слежения за конфигом")

    threading.Thread(target=watch_config, args=(stop,),
                     daemon=True, name="config").start()

    http = config.get("http", {}) or {}
    host = http.get("host", "127.0.0.1")
    port = int(http.get("port", 8088))
    log.info("HTTP слушает http://%s:%s/  (панели: %s)",
             host, port, ", ".join(sorted(config.panels)) or "нет")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8, _quiet=True)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
