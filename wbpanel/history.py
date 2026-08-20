# -*- coding: utf-8 -*-
"""
История значений: SQLite напрямую либо через db_logger.
"""

import json
import os
import sqlite3
import threading
import time
from .const import DB_PATH, log
from .state import MqttRpc, state


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
