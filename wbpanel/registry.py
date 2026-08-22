# -*- coding: utf-8 -*-
"""
Реестр плиток: контракт, контекст и зоны нажатий.

Плитка объявляет, какие роли ей нужны, считает данные для шаблона и
описывает, что делает нажатие. Ни брокера, ни топиков, ни состояния
целиком она не видит - только узкую калитку ctx.
"""

from .roles import schema
from .sources import bind

REGISTRY = {}


def tile(name):
    """Регистрация типа плитки. Дубликат имени - ошибка, а не сюрприз."""
    def wrap(cls):
        if name in REGISTRY:
            raise KeyError("тип плитки %r уже занят классом %s"
                           % (name, REGISTRY[name].__name__))
        cls.kind = name
        REGISTRY[name] = cls
        return cls
    return wrap


class Zone(object):
    """
    Что делает нажатие. Данные, а не код: топик подставит ядро, проверив
    по схеме, что роль записываемая. Те же зоны потом можно отдать
    дисплею, у которого свой обработчик касаний и никакого JavaScript.

    area: all | left | right | top | bottom | center | button
    """

    def __init__(self, name, area, role=None, write=None, pad=None):
        self.name = name
        self.area = area
        self.role = role
        self.write = write      # значение либо {"on": …, "off": …}
        self.pad = pad          # открыть пульт вместо записи

    def __repr__(self):
        return "<Zone %s %s -> %s=%s>" % (self.name, self.area,
                                          self.role, self.write)


class Ctx(object):
    """Узкая калитка к состоянию."""

    def __init__(self, conf, bound, state, history=None):
        self.conf = conf
        self.bound = bound
        self.state = state
        self.history = history
        self.snaps = []          # что прочитали - для отметки «нет данных»

    def _snap(self, name):
        """
        Снимок канала. Каждое обращение запоминается: по снимкам ядро
        отмечает плитку как «нет данных», и пропущенный снимок означает
        плитку, которая выглядит живой при мёртвом канале.
        """
        b = self.bound.get(name)
        if not b:
            return None
        snap = self.state.snapshot(b.key)
        self.snaps.append(snap)
        return snap

    def meta(self, name, key, default=None):
        """Что канал рассказал о себе: max, units, precision…"""
        b = self.bound.get(name)
        return (b.meta.get(key, default) if b else default)

    def raw(self, name, default=None):
        snap = self._snap(name)
        return (snap or {}).get("raw") or default

    def number(self, name, default=None):
        try:
            return float(str(self.raw(name)).strip())
        except (TypeError, ValueError):
            return default

    def flag(self, name):
        """Дискретное значение: пусто и «0» - ложь, остальное - истина."""
        return str(self.raw(name) or "").strip() not in ("", "0", "false", "False")

    def has(self, name):
        return name in self.bound

    def opt(self, name, default=None):
        """Не-канальное поле конфига."""
        return self.conf.get(name, default)

    def units(self, name, default=""):
        b = self.bound.get(name)
        return (b.units if b else "") or self.opt("unit", default)

    def step(self, name, default=0.5):
        """
        Шаг: сперва из конфига, потом от устройства, потом умолчание.
        Устройство знает свою точность лучше нас, но человек вправе
        загрубить: шагать по десятой градуса кнопками неудобно.
        """
        if self.opt("step") is not None:
            return float(self.opt("step"))
        b = self.bound.get(name)
        return (b.step if b else None) or default

    def limits(self, name, lo=None, hi=None):
        b = self.bound.get(name)
        dlo, dhi = b.limits if b else (None, None)
        lo = self.opt("target_min", dlo if dlo is not None else lo)
        hi = self.opt("target_max", dhi if dhi is not None else hi)
        return float(lo), float(hi)

    def title(self, name=None):
        """Название: из конфига, иначе то, что сообщило устройство."""
        if self.opt("title"):
            return self.opt("title")
        b = self.bound.get(name or "value")
        return (b.title() if b else "") or ""

    def enum(self, name, value):
        b = self.bound.get(name)
        return b.enum(value) if b else ""


class Tile(object):
    """Базовый класс. Минимум для новой плитки - roles и prepare."""

    kind = None
    roles = ()
    template = None

    # Имена полей, если они отличаются от принятых у роли. В конфигах уже
    # сложились свои: у диммера выключатель зовётся channel, а не
    # channel_switch. Ломать чужие конфиги ради стройности не стоит.
    fields = {}
    commands = {}

    def field_of(self, role):
        return self.fields.get(role.name, role.field)

    def command_of(self, role):
        return self.commands.get(role.name, role.command_field)

    def schema(self):
        return schema(self.roles)

    def check(self, conf, bound):
        for role in self.schema():
            if role.required and role.name not in bound:
                return "не задана роль %s (поле %s)" % (role.name,
                                                        self.field_of(role))
        return None

    def prepare(self, ctx):
        return {}

    def zones(self, ctx):
        return []


def build(conf, state, history=None):
    """
    Собрать плитку целиком: привязать роли, проверить, посчитать данные,
    описать нажатия и вывести список разрешённых для записи топиков.
    """
    kind = conf.get("type", "value")
    cls = REGISTRY.get(kind)
    if cls is None:
        return {"kind": kind, "problem": "неизвестный тип плитки: %r" % kind,
                "data": {}, "zones": [], "bound": {}, "writable": set()}

    obj = cls()
    roles = obj.schema()
    bound = bind(conf, roles, state, obj)
    problem = obj.check(conf, bound)

    ctx = Ctx(conf, bound, state, history)
    data = obj.prepare(ctx) if not problem else {}
    zones = obj.zones(ctx) if not problem else []

    # Белый список - следствие ролей, а не отдельный перебор по типам,
    # который забывают дополнить при добавлении новой команды.
    writable = set(b.command for b in bound.values() if b.command)

    return {"kind": kind, "template": obj.template, "data": data,
            "zones": zones, "bound": bound, "writable": writable,
            "snaps": ctx.snaps, "problem": problem,
            "source": conf.get("source") or type(bind).__name__}


def channels_of(conf):
    """Топики всех ролей плитки - для подписки. Без состояния, до старта."""
    cls = REGISTRY.get(conf.get("type", "value"))
    if cls is None:
        return set()
    out = set()
    obj = cls()
    for role in obj.schema():
        value = conf.get(obj.field_of(role))
        if value:
            out.add(value)
        cmd = conf.get(obj.command_of(role))
        if cmd:
            out.add(cmd)
    if conf.get("service"):
        out.add(conf["service"])       # префикс: подписка возьмёт всю службу
    return out
