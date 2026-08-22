# -*- coding: utf-8 -*-
"""
Источники: как роль превращается в топик у конкретного устройства.

Устройства описываются по-разному, и это единственное место, которое про
различия знает. Плитка видит только роли.

    wb          соглашения Wiren Board: устройство/канал, команда в /on
    raw         произвольный MQTT: топик как есть, адрес команды явный
    spruthub    служба HomeKit: роли раскладываются по типам характеристик

Появится ещё один мост - добавится четвёртый источник, плитки не изменятся.
"""

from .roles import ROLES


class Bound(object):
    """
    Роль, привязанная к топику. Кроме адреса несёт то, что устройство
    рассказало о себе само: единицы, шаг, название, подписи значений.
    Это избавляет конфиг от полей, которые и так известны.
    """

    def __init__(self, role, key, command=None, meta=None, auto=False):
        self.role = role
        # key - то, чем канал зовётся в состоянии и в конфиге:
        # «устройство/канал» у Wiren Board, готовый топик у мостов.
        # Полный MQTT-адрес нужен только для записи и лежит в command.
        self.key = key
        self.command = command
        self.meta = meta or {}
        self.auto = auto            # разобрано автоматически, а не задано

    @property
    def writable(self):
        return bool(self.command)

    @property
    def units(self):
        return self.meta.get("units", "")

    @property
    def step(self):
        """
        Точность канала. У Wiren Board это precision, у HomeKit minStep -
        и то и другое означает «мельче не бывает».
        """
        for key in ("precision", "minStep"):
            try:
                value = float(self.meta.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @property
    def limits(self):
        def num(key):
            try:
                return float(self.meta.get(key))
            except (TypeError, ValueError):
                return None
        return num("min") or num("minValue"), num("max") or num("maxValue")

    def title(self, lang="ru"):
        t = self.meta.get("title")
        if isinstance(t, dict):
            return t.get(lang) or t.get("en") or ""
        return t or ""

    def enum(self, value, lang="ru"):
        """Подпись значения-перечисления: «Охлаждение» вместо 2."""
        item = (self.meta.get("enum") or {}).get(str(value))
        if isinstance(item, dict):
            return item.get(lang) or item.get("en") or ""
        return item or ""

    @property
    def topic(self):
        """Прежнее имя ключа - чтобы не ломать вызовы снаружи."""
        return self.key

    def __repr__(self):
        return "<%s %s%s>" % (self.role.name, self.key,
                              " (авто)" if self.auto else "")


class Source(object):
    name = "raw"

    def resolve(self, conf, roles, state):
        raise NotImplementedError


class WbSource(Source):
    """
    Wiren Board. Метаданные канала лежат в /meta - и одним JSON, и старыми
    подтопиками; WbState разбирает оба вида. Оттуда берём права записи,
    единицы, шаг и название, вместо того чтобы держать их в конфиге.
    """
    name = "wb"

    def resolve(self, conf, roles, state):
        out = {}
        for role in roles:
            key = conf.get(role.field)
            if not key or "/" not in key:
                continue
            meta = state.meta.get(key, {}) if state else {}
            readonly = str(meta.get("readonly", "")).lower() in ("1", "true")
            dev, ctl = key.split("/", 1)
            command = None
            if role.writable and not readonly:
                command = (conf.get(role.command_field)
                           or "/devices/%s/controls/%s/on" % (dev, ctl))
            out[role.name] = Bound(role, key, command, meta)
        return out


class RawSource(Source):
    """
    Произвольный MQTT: топик как есть.

    Адрес команды не угадывается. У мостов он свой, и ошибка тут уходит в
    никуда молча: запись в топик состояния брокер просто перетрёт обратно.
    """
    name = "raw"

    def resolve(self, conf, roles, state):
        out = {}
        for role in roles:
            topic = conf.get(role.field)
            if not topic:
                continue
            command = None
            if role.writable:
                command = (conf.get(role.command_field)
                           or conf.get("command_topic")
                           if role.name == "value" else conf.get(role.command_field))
            out[role.name] = Bound(role, topic, command,
                                   state.meta.get(topic, {}) if state else {})
        return out


class SprutSource(Source):
    """
    Sprut.hub. Устройства описаны как в Apple HomeKit: аксессуар, служба,
    характеристики. Рядом со значением мост публикует type, read, write,
    minValue, maxValue - значит роли можно разложить по типам, а не
    выписывать номера характеристик руками.

    Команда идёт в подтопик /set: это соглашение моста, и запись в топик
    состояния бесполезна.
    """
    name = "spruthub"

    BY_TYPE = {
        "CurrentTemperature": "current",
        "TargetTemperature": "target",
        "CurrentHeatingCoolingState": "state",
        "TargetHeatingCoolingState": "mode",
        "CurrentPosition": "current",
        "TargetPosition": "target",
        "PositionState": "state",
        "C_TargetPositionState": "stop",
        "HoldPosition": "stop",
        "On": "switch",
        "Brightness": "bright",
        "ColorTemperature": "temp",
        "CurrentRelativeHumidity": "current",
        "BatteryLevel": "progress",
    }

    def resolve(self, conf, roles, state):
        wanted = set(r.name for r in roles)
        prefix = (conf.get("service") or "").rstrip("/")
        out = {}
        if prefix and state:
            for topic in sorted(state.meta):
                if not topic.startswith(prefix + "/"):
                    continue
                meta = state.meta[topic]
                name = self.BY_TYPE.get(meta.get("type"))
                if not name or name not in wanted or name in out:
                    continue
                writable = str(meta.get("write", "")).lower() == "true"
                out[name] = Bound(ROLES[name], topic,
                                  (topic + "/set") if writable else None,
                                  meta, auto=True)
        # Явно заданное сильнее разобранного: автоопределение ошибается
        # молча, и должен быть способ его перебить.
        out.update(RawSource().resolve(conf, roles, state))
        return out


SOURCES = {s.name: s for s in (WbSource(), RawSource(), SprutSource())}


def pick_source(conf):
    """
    Источник по виду конфига.

    Больше одного слэша в имени канала - это готовый топик чужого
    соглашения: у Wiren Board имя всегда «устройство/канал».
    """
    if conf.get("source"):
        return SOURCES.get(conf["source"], SOURCES["raw"])
    if conf.get("service"):
        return SOURCES["spruthub"]
    for key, value in conf.items():
        if key.startswith("channel") and isinstance(value, str):
            if value.count("/") > 1:
                return SOURCES["raw"]
    return SOURCES["wb"]


def bind(conf, roles, state):
    """conf + роли -> {имя роли: Bound}"""
    return pick_source(conf).resolve(conf, roles, state)
