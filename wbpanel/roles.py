# -*- coding: utf-8 -*-
"""
Роли каналов.

Роль - это не поле конфига, а смысл: цель всегда пишется, факт всегда
читается, и правила для них одинаковы у всех плиток. Из объявленных ролей
ядро само выводит три вещи, которые раньше приходилось поддерживать
руками и потому регулярно забывали:

    подписку на MQTT      - топики всех ролей всех плиток
    белый список записи   - команды ролей с writable, и только они
    проверку конфига      - «не задана роль target» вместо пустой плитки

Набор расширяемый: добавить роль - одна строка.
"""


class Role(object):
    def __init__(self, name, writable=False, required=False, numeric=True,
                 label="", doc=""):
        self.name = name
        self.writable = writable
        self.required = required
        self.numeric = numeric
        # label и doc нужны не коду, а человеку: по ним строится справка,
        # а когда дойдёт до графического редактора - форма полей.
        self.label = label or name
        self.doc = doc

    @property
    def field(self):
        """Как роль называется в конфиге: channel, channel_target…"""
        return "channel" if self.name == "value" else "channel_" + self.name

    @property
    def command_field(self):
        """Явный адрес команды, если он не выводится из канала."""
        return "command_topic" if self.name == "value" else "command_topic_" + self.name

    def __repr__(self):
        return "<Role %s%s>" % (self.name, "*" if self.required else "")


ROLES = {}


def role(*args, **kwargs):
    r = Role(*args, **kwargs)
    ROLES[r.name] = r
    return r


role("value", writable=True, required=True, label="Канал",
     doc="единственный канал простой плитки")
role("current", label="Текущее значение", doc="то, что прибор измеряет")
role("target", writable=True, label="Уставка", doc="то, к чему он стремится")
role("state", label="Состояние", doc="работает сейчас или ждёт")
role("mode", writable=True, label="Режим")
role("stop", writable=True, numeric=False, label="Остановка",
     doc="прервать движение на полпути")
role("switch", writable=True, numeric=False, label="Включение")
role("bright", writable=True, label="Яркость")
role("temp", writable=True, label="Цветовая температура")
role("progress", label="Выполнено")
role("left", writable=True, label="Левая створка")
role("right", writable=True, label="Правая створка")


def schema(names):
    """Имена ролей -> объекты. Опечатка ловится здесь, а не при отрисовке."""
    out = []
    for name in names:
        if name not in ROLES:
            raise KeyError("нет такой роли: %r (есть: %s)"
                           % (name, ", ".join(sorted(ROLES))))
        out.append(ROLES[name])
    return out
