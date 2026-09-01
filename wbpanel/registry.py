# -*- coding: utf-8 -*-
"""
Реестр плиток: контракт, контекст и зоны нажатий.

Плитка объявляет, какие роли ей нужны, считает данные для шаблона и
описывает, что делает нажатие. Ни брокера, ни топиков, ни состояния
целиком она не видит - только узкую калитку ctx.
"""

from .const import log
from .roles import schema
from .sources import bind
from .state import is_on

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

    area   all | left | right | top | bottom | center | button | long | pad
           left и right занимают часть плитки, и по ним ядро само рисует
           прозрачные прямоугольники; long - долгое нажатие по всей плитке;
           pad - зона, доступная только с пульта.
    name   как зона зовётся в data-атрибутах, см. ZONE_KEYS в commands.py
    topic  запасной ход для приборов, описанных устройством целиком, а не
           набором ролей: у кондиционера полтора десятка каналов, и роль у
           всех одна - «часть этого прибора».
    state  включено ли сейчас то, что переключает зона. По умолчанию берётся
           поле on из данных плитки; термостату этого мало - у него «греет»
           и «включён» разные вещи.
    """

    def __init__(self, name, area, role=None, write=None, pad=None,
                 topic=None, state=None):
        self.name = name
        self.area = area
        self.role = role
        self.write = write      # значение либо {"on": …, "off": …}
        self.pad = pad          # открыть пульт вместо записи
        self.topic = topic
        self.state = state

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
        if not b or not b.key:
            return None
        snap = self.state.snapshot(b.key)
        self.snaps.append(snap)
        return snap

    def dev(self, suffix, default=None):
        """
        Канал устройства по имени из поля device.

        Прогноз и кондиционер описываются одним устройством с десятком
        каналов: перечислять их ролями бессмысленно, роль там у всех одна
        - «часть этого прибора».
        """
        device = self.conf.get("device")
        if not device:
            return default
        snap = self.state.snapshot("%s/%s" % (device, suffix))
        self.snaps.append(snap)
        return snap["raw"] if snap["raw"] not in (None, "") else default

    def dev_number(self, suffix, default=None):
        try:
            return float(str(self.dev(suffix)).strip())
        except (TypeError, ValueError):
            return default

    def series(self, channel, span, points):
        """Ряд из истории. Без неё график просто пуст, а не падает."""
        if not self.history:
            return []
        return self.history.get(channel, span, points)

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
        return is_on(self.raw(name))

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

    #: Читает ли плитка что-нибудь из MQTT. Заголовок, ссылка и камера -
    #: нет, и отметка «нет данных» к ним неприменима: у них нет данных
    #: по устройству, а не по неисправности. Без этого флага такая плитка
    #: вечно висела бы полупрозрачной с серой точкой.
    reads = True

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

    @staticmethod
    def channels(conf):
        """
        Каналы, не выводимые из ролей: ряды графика, каналы устройства
        целиком, чипы заголовка. Отсюда же берётся подписка на MQTT.
        """
        return set()

    @staticmethod
    def writes(conf):
        """То же самое для записи: командные топики мимо ролей."""
        return set()

    def prepare(self, ctx):
        return {}

    def zones(self, ctx, data):
        """
        Описание нажатий. data - то, что вернул prepare: считать одно и то
        же дважды не нужно, а лишний ctx.number ещё и добавляет снимок,
        отчего плитка ошибочно числится живой.
        """
        return []

    def pad(self, ctx, data):
        """
        Чем пульт отличается от плитки: пределы шкалы, геометрия дуги,
        цвета поля. Не зоны - тут нечего записывать, это параметры.
        """
        return {}


def resolve_type(conf, state):
    """
    Тип плитки: заданный человеком либо опознанный по службе.

    Явный `type:` всегда сильнее. Опознавание ошибается молча, и должен
    остаться способ его перебить - записать в конфиг то, что получилось,
    и больше к этому не возвращаться.
    """
    explicit = conf.get("type")
    if explicit:
        return str(explicit)
    if conf.get("service"):
        kind, _found = guess_type(conf, state)
        if kind:
            return kind
        return "unknown"       # честно, а не тихо подставленный value
    return "value"


def build(conf, state, history=None):
    """
    Собрать плитку целиком: привязать роли, проверить, посчитать данные,
    описать нажатия и вывести список разрешённых для записи топиков.
    """
    kind = resolve_type(conf, state)
    cls = REGISTRY.get(kind)
    if kind == "unknown":
        found = guess_type(conf, state)[1]
        return {"kind": "value", "template": "value", "data": {},
                "zones": [], "pad": {}, "bound": {}, "writable": set(),
                "snaps": [], "source": "spruthub",
                "problem": "тип не опознан по службе; характеристики: %s"
                           % (", ".join(sorted(found)) or "не найдены")}
    if cls is None:
        return {"kind": kind, "problem": "неизвестный тип плитки: %r" % kind,
                "data": {}, "zones": [], "bound": {}, "writable": set()}

    obj = cls()
    roles = obj.schema()
    bound = bind(conf, roles, state, obj)
    problem = obj.check(conf, bound)

    ctx = Ctx(conf, bound, state, history)
    data = obj.prepare(ctx) if not problem else {}
    zones = obj.zones(ctx, data) if not problem else []
    pad = obj.pad(ctx, data) if not problem else {}

    # Белый список - следствие ролей, а не отдельный перебор по типам,
    # который забывают дополнить при добавлении новой команды.
    writable = set(b.command for b in bound.values() if b.command)
    writable.update(cls.writes(conf) or ())

    return {"kind": kind, "template": obj.template, "data": data,
            "zones": zones, "pad": pad, "bound": bound, "writable": writable,
            "snaps": ctx.snaps, "problem": problem, "reads": bool(cls.reads),
            "source": conf.get("source") or type(bind).__name__}


def channels_of(conf, state=None):
    """
    Что плитка читает - для подписки. Без состояния, до старта.

    Только чтение: командные топики отдельно, в writable_of. Смешивать их
    нельзя - по этому же списку страница проверяет, можно ли показать
    график канала, и топик вида .../on там означал бы график команды.
    """
    cls = REGISTRY.get(resolve_type(conf, state))
    if cls is None:
        return set()
    obj = cls()
    out = set(cls.channels(conf) or ())
    known = set()
    for role in obj.schema():
        field = obj.field_of(role)
        known.add(field)
        value = conf.get(field)
        if value:
            out.add(value)

    # Поле channel_*, не отвечающее ни одной роли этой плитки. Раньше его
    # подхватывал общий перебор по именам полей, и опечатка вроде
    # channel_bright вместо channel_brightness молча слушала канал, ничего
    # им не показывая. Слушаем по-прежнему, но говорим вслух.
    for key, value in conf.items():
        if key != "channel" and not key.startswith("channel_"):
            continue
        if key in known or not isinstance(value, str) or not value:
            continue
        log.warning("плитка %r: поле %s не отвечает ни одной роли типа %r",
                    conf.get("title") or "без названия", key,
                    resolve_type(conf, state))
        out.add(value)
    return out


def writable_of(conf, state=None):
    """
    Куда плитке позволено писать. Тот же разбор ролей, что и при сборке,
    поэтому список не может отстать от нарисованных кнопок.

    Канал с readonly в /meta команды не получает: источник просто не
    выведет для него адрес, и в белый список попадать нечему.
    """
    cls = REGISTRY.get(resolve_type(conf, state))
    if cls is None:
        return set()
    obj = cls()
    out = set(cls.writes(conf) or ())
    for b in bind(conf, obj.schema(), state, obj).values():
        if b.command:
            out.add(b.command)
    return out


# ==========================================================================
#  Опознавание типа плитки по службе
# ==========================================================================

# Набор характеристик -> тип плитки. Порядок важен: проверяем от более
# определённого к менее. У термостата есть CurrentTemperature, как у
# датчика; у ленты есть On, как у выключателя. Кто ищет менее строгий
# признак первым, тот всё подряд объявит выключателем.
#
# need - что обязано быть, чтобы признать тип. any_of - хотя бы одно из,
# если такого условия нет, поле опущено.
AUTO_TYPES = (
    ("thermostat", {"need": ("TargetTemperature",)}),
    ("curtain",    {"need": ("TargetPosition",)}),
    ("light",      {"need": ("On", "Brightness"),
                    "any_of": ("ColorTemperature", "Hue", "Saturation")}),
    ("dimmer",     {"need": ("On", "Brightness")}),
    ("switch",     {"need": ("On",)}),
    ("value",      {"need": ("CurrentTemperature",)}),
    ("value",      {"need": ("CurrentRelativeHumidity",)}),
)


def guess_type(conf, state):
    """
    Тип плитки по набору характеристик службы.

    Возвращает (тип, пояснение) либо (None, что нашлось). Молча подставлять
    `value` нельзя: человек решит, что панель сломалась, вместо того чтобы
    дописать одну строку. Поэтому неопознанное честно возвращает None, а
    страница проверки покажет, чего не хватило.
    """
    from .sources import SprutSource

    found = SprutSource.types_of(conf, state)
    if not found:
        return None, found
    for kind, rule in AUTO_TYPES:
        if not set(rule["need"]).issubset(found):
            continue
        if "any_of" in rule and not (set(rule["any_of"]) & found):
            continue
        return kind, found
    return None, found
