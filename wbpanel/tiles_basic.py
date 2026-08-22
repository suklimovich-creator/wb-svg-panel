# -*- coding: utf-8 -*-
"""
Простые плитки на контракте ролей: выключатель, значение, ссылка.

Первые три переведены нарочно: у них нет ни истории, ни геометрии, зато
есть всё остальное - чтение канала, инверсия, единицы, нажатие. Если
контракт годится для них, дальше идут сложные.

Старые сборщики build_switch, build_value, build_link заменены адаптером в
tiles.py: наружу форма данных та же, панель не меняется ни на пиксель.
"""

from .geometry import _fmt
from .registry import Tile, Zone, tile
from .state import to_float


@tile("switch")
class Switch(Tile):
    roles = ("value",)

    def prepare(self, ctx):
        raw = (ctx.raw("value") or "").strip()
        on = raw not in ("", "0", "false", "False")

        # invert - для нормально замкнутых контактов: реле обесточено, а
        # нагрузка включена. Единица в канале означает «выключено».
        # Инвертируем только известное значение: нет данных - значит нет
        # данных, а не «включено».
        if ctx.opt("invert") and raw != "":
            on = not on

        return {
            "on": on,
            "status": ctx.opt("on_text", "Вкл") if on else ctx.opt("off_text", "Выкл"),
            "icon": ctx.opt("icon", "toggle"),
            "short": ctx.opt("badge", ""),
        }

    def zones(self, ctx):
        on = str(ctx.opt("command_on", "1"))
        off = str(ctx.opt("command_off", "0"))
        # При инверсии переворачивается и команда: чтобы включить нагрузку,
        # реле надо обесточить.
        if ctx.opt("invert"):
            on, off = off, on
        return [Zone("toggle", "all", "value", {"on": on, "off": off})]


@tile("value")
class Value(Tile):
    roles = ("value",)

    def prepare(self, ctx):
        return {
            "value": _fmt(to_float(ctx.raw("value")), int(ctx.opt("digits", 1))),
            # Единицы: из конфига, иначе те, что канал сообщил о себе сам.
            "unit": ctx.units("value"),
            "on": False,
        }


@tile("link")
class Link(Tile):
    """
    Ссылка на другую панель. Ничего не читает из MQTT - только переход.

    Роль value не нужна, поэтому схема пуста: контракт не требует каналов
    там, где их нет.
    """
    roles = ()

    def prepare(self, ctx):
        target = ctx.opt("panel") or ""
        href = ctx.opt("url") or ((target + ".html") if target else "")
        # К адресу дописываем, откуда пришли: тогда кнопка «назад» на той
        # стороне вернёт сюда, а не в общий список.
        if href and not ctx.opt("url") and ctx.opt("_from"):
            href += "?from=" + ctx.opt("_from")
        return {
            "on": False,
            "href": href,
            "icon": ctx.opt("icon", "door"),
            "status": ctx.opt("subtitle", ""),
        }
