# -*- coding: utf-8 -*-
"""
Состояние MQTT в памяти и вызовы MQTT-RPC.
"""

import re
import threading
import time
import json
import paho.mqtt.client as mqtt
from .const import log
import itertools


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
#  Запуск
# ==========================================================================

def make_client(client_id):
    """paho-mqtt 1.x на Debian 11 и 2.x в новых сборках требуют разного конструктора."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except (AttributeError, TypeError):
        return mqtt.Client(client_id=client_id)


# Единственный на процесс экземпляр. Живёт здесь, а не в web, чтобы фоновый
# префетч истории не тянул за собой веб-слой: иначе получается кольцо
# импортов, которое питон разорвёт в самый неудобный момент.
state = WbState()
