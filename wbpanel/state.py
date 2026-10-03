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
        # Будильник для тех, кто ждёт изменений: страница держит запрос
        # открытым, и ответ уходит в тот же миг, как поменялся канал, а не
        # на следующем круге опроса. Условие живёт на том же замке, что и
        # значения, - иначе между проверкой версии и засыпанием можно
        # пропустить изменение.
        self.changed = threading.Condition(self.lock)
        self.watched = set()
        self.watched_prefixes = ()
        self.touched = {}   # ключ -> версия, при которой он менялся
        self.connected = False

    def _touch(self, key):
        """Канал поменялся. Вызывается под замком."""
        self.version += 1
        # Кто и когда менялся: по этому ждущий запрос понимает, касается ли
        # изменение его панели. Иначе страница детской просыпалась бы от
        # каждого датчика гостиной и перерисовывалась впустую.
        self.touched[key] = self.version
        self.changed.notify_all()

    def bump(self, key=None):
        """Что-то нарисованное поменялось не через MQTT - например, список.
        key - метка вида list:shopping, по ней узнаёт свою панель."""
        with self.lock:
            self._touch(key or "")

    def _is_watched(self, topic):
        if topic in self.watched:
            return True
        # Служба Sprut.hub одной строкой service: - каналов поимённо в
        # конфиге нет, следим по префиксу. Без этого такие плитки
        # обновлялись бы только по таймеру.
        for prefix in self.watched_prefixes:
            if topic.startswith(prefix):
                return True
        return False

    def touched_since(self, since, keys, prefixes=()):
        """Менялось ли после версии since что-то из keys или под prefixes."""
        with self.lock:
            for key in keys:
                if self.touched.get(key, 0) > since:
                    return True
            if prefixes:
                for key, ver in self.touched.items():
                    if ver > since and any(key.startswith(p) for p in prefixes):
                        return True
        return False

    def last_touch(self, keys, prefixes=()):
        """Последняя версия, при которой менялось что-то из keys или под
        prefixes. Пустая метка - общие изменения (bump без ключа) - тоже
        считается: её видят все панели."""
        with self.lock:
            best = self.touched.get("", 0)
            for key in keys:
                ver = self.touched.get(key, 0)
                if ver > best:
                    best = ver
            if prefixes:
                for key, ver in self.touched.items():
                    if ver > best and any(key.startswith(p) for p in prefixes):
                        best = ver
            return best

    def wait_change(self, since, timeout):
        """
        Ждать, пока версия станет больше since, но не дольше timeout секунд.
        Возвращает текущую версию - ту же, если ничего не случилось.
        """
        deadline = time.time() + timeout
        with self.lock:
            while self.version <= since:
                left = deadline - time.time()
                if left <= 0:
                    break
                self.changed.wait(left)
            return self.version

    def set_watched(self, channels, prefixes=()):
        with self.lock:
            self.watched = set(channels)
            self.watched_prefixes = tuple(p.rstrip("/") + "/" for p in prefixes if p)

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
                if changed and self._is_watched(topic):
                    self._touch(topic)
            return
        key = "%s/%s" % (parts[2], parts[4])
        rest = parts[5:]
        with self.lock:
            if not rest:
                changed = self.values.get(key) != payload
                self.values[key] = payload
                self.stamps[key] = time.time()
                if changed and key in self.watched:
                    self._touch(key)
            elif rest == ["meta"]:
                try:
                    meta = json.loads(payload) if payload else {}
                except ValueError:
                    return
                if isinstance(meta, dict):
                    self.meta.setdefault(key, {}).update(meta)
                    if key in self.watched:
                        self._touch(key)
            elif len(rest) == 2 and rest[0] == "meta":
                # legacy-формат: /meta/units, /meta/type, /meta/error, /meta/max
                self.meta.setdefault(key, {})[rest[1]] = payload
                if key in self.watched:
                    self._touch(key)

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


#: Что считается «выключено». Wiren Board пишет 0, виртуальные устройства
#: wb-rules и Sprut.hub - false, Zigbee2MQTT - OFF, а недоступный прибор
#: может отдать unavailable. Раньше проверка знала только про 0 и false, и
#: OFF от Zigbee2MQTT оказывался истиной: выключенная лампа показывалась
#: включённой, а нажатие пыталось её выключить ещё раз.
OFF_WORDS = ("", "0", "false", "off", "no", "none", "null", "unavailable",
             "offline", "closed")


def is_on(raw):
    """Значение канала -> включено ли. Регистр не важен."""
    return str(raw if raw is not None else "").strip().lower() not in OFF_WORDS


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
