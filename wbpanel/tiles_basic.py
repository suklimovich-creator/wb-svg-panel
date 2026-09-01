# -*- coding: utf-8 -*-
"""
Простые плитки на контракте ролей: выключатель, значение, ссылка.

Первые три переведены нарочно: у них нет ни истории, ни геометрии, зато
есть всё остальное - чтение канала, инверсия, единицы, нажатие. Если
контракт годится для них, дальше идут сложные.

Старые сборщики build_switch, build_value, build_link заменены адаптером в
tiles.py: наружу форма данных та же, панель не меняется ни на пиксель.
"""

import time
from urllib.parse import quote

from .geometry import _fmt
from .registry import Tile, Zone, tile
from .state import is_on
from .state import to_float


@tile("switch")
class Switch(Tile):
    roles = ("value",)

    def prepare(self, ctx):
        raw = (ctx.raw("value") or "").strip()
        on = is_on(raw)

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

    def zones(self, ctx, data):
        on = str(ctx.opt("command_on", "1"))
        off = str(ctx.opt("command_off", "0"))
        # При инверсии переворачивается и команда: чтобы включить нагрузку,
        # реле надо обесточить.
        if ctx.opt("invert"):
            on, off = off, on
        return [Zone("toggle", "all", "value", {"on": on, "off": off})]


@tile("scene")
class Scene(Tile):
    """
    Сцена: нажал - сработало. Не выключатель.

    В wb-rules сцена заведена как канал типа switch, но правило,
    отработав, само возвращает его в ноль. Состояния у неё поэтому нет:
    к следующему опросу панели канал уже сброшен, и подсвечивать нечего.
    Отсюда два отличия от `switch`: плитка никогда не считается
    включённой, а нажатие всегда пишет одно и то же значение, а не
    переключает.
    """

    roles = ("value",)

    #: Каким словом канал говорит «включено». Виртуальные устройства
    #: wb-rules отдают true/false, модули Wiren Board - 1/0. Гадать не
    #: нужно: канал retained, его текущее значение уже прочитано, и форма
    #: записи видна прямо в нём.
    FORMS = {"true": ("true", "false"), "false": ("true", "false"),
             "1": ("1", "0"), "0": ("1", "0")}

    def _form(self, ctx):
        explicit = ctx.opt("command_on")
        if explicit is not None:
            return str(explicit)
        raw = (ctx.raw("value") or "").strip().lower()
        return self.FORMS.get(raw, ("1", "0"))[0]

    def prepare(self, ctx):
        return {
            # Сцена не горит: правило сбрасывает канал быстрее, чем панель
            # успевает перерисоваться, и оранжевая рамка только сбивала бы
            # с толку - она означает «прибор включён», а сцена не прибор.
            "on": False,
            "icon": ctx.opt("icon", "power"),
            "status": ctx.opt("subtitle", ""),
        }

    def zones(self, ctx, data):
        # Оба значения одинаковые: страница шлёт off при state=1 и on при
        # state=0, а нам нужно всегда одно и то же. state всегда 0, так
        # что сюда попадёт on - но пусть и второй путь ведёт туда же.
        value = self._form(ctx)
        return [Zone("toggle", "all", "value", {"on": value, "off": value})]


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


@tile("header")
class Header(Tile):
    """
    Заголовок области: название комнаты и чипы состояния.

    Плитка, а не строка разметки - поэтому её можно поставить в нужное
    место сетки. Панель «кухня и гостиная» собирается так: два заголовка
    закреплены сверху слева и справа, каждый над своим столбцом.

    Ролей нет: чипы описываются блоком status, у каждого свои каналы и
    свои условия - это отдельный словарь, а не роли плитки.
    """

    roles = ()
    reads = False

    @staticmethod
    def channels(conf):
        from .status import status_entries
        out = set()
        for entry in status_entries(conf):
            for key in ("channel", "value_channel"):
                if entry.get(key):
                    out.add(entry[key])
            req = entry.get("require")
            for item in (req if isinstance(req, list) else [req]):
                if isinstance(item, dict) and item.get("channel"):
                    out.add(item["channel"])
        return out

    def prepare(self, ctx):
        from .status import build_status

        chips = build_status(ctx.conf, ctx.state)
        # Чипы прижаты вправо: слева название, между ними пустота, которая
        # и держит разницу между заголовком и обычной плиткой.
        gap = 10
        total = sum(c["w"] for c in chips) + gap * max(len(chips) - 1, 0)
        x = ctx.opt("inner_w", 0) - 14 - total
        y = round((ctx.opt("inner_h", 36) - 36) / 2.0, 1)
        for chip in chips:
            chip["x"], chip["y"] = round(x, 1), y
            x += chip["w"] + gap

        return {
            "on": False,
            "chips": chips,
            # Без плашки по умолчанию: иначе на панели появляется
            # прямоугольник, похожий на кнопку, которая ничего не делает.
            "plain": bool(ctx.opt("plain", True)),
            "status": "",
        }


@tile("camera")
class Camera(Tile):
    """
    Кадр с камеры. Ролей нет: MQTT здесь ни при чём.

    Плитка показывает снимок, обновляющийся раз в несколько секунд, по
    нажатию открывается поток на весь экран. И то и другое идёт через
    демон - пароль от камеры в разметку попадать не должен, а браузер
    всё равно не открывает http://user:pass@... внутри <img>.

    В конфиге:

        - type: camera
          camera: nursery        # имя из раздела cameras: верхнего уровня
          title: Детская

    Адрес снимка нарочно меняется не каждую перерисовку, а раз в
    `refresh` секунд: панель перечитывается каждые десять секунд целиком,
    и картинка с уникальным адресом моргала бы при каждой. С адресом,
    постоянным внутри промежутка, браузер берёт кадр из кеша, и плитка
    стоит неподвижно ровно столько, сколько задумано.
    """

    roles = ()
    reads = False

    def prepare(self, ctx):
        from . import cameras

        name = str(ctx.opt("camera") or ctx.opt("name") or "")
        cam = cameras.get(name)
        if cam is None:
            # Показываем прямо на плитке, а не только в журнале: пустой
            # прямоугольник человек примет за неработающую камеру и пойдёт
            # проверять сеть вместо одной строки в конфиге.
            return {"on": False, "camera": name, "src": "",
                    "icon": ctx.opt("icon", "camera"),
                    "always_status": True,
                    "status": ("нет камеры %s" % name) if name
                              else "не задано поле camera"}

        bucket = int(time.time() // cam.refresh)
        base = "cam/" + quote(name, safe="")
        return {
            "on": False,
            "camera": name,
            "src": "%s.jpg?t=%d" % (base, bucket),
            "stream": ("%s.mjpeg" % base) if cam.stream_path else "",
            "poll": "%s.jpg" % base,
            # Сколько миллисекунд браузеру держать поток открытым, прежде
            # чем переподключиться: сервер закрывает соединение сам, и без
            # переподключения картинка тихо застывает.
            "life": int(cam.stream_limit * 1000) if cam.stream_limit else 0,
            "icon": ctx.opt("icon", "camera"),
            "status": ctx.opt("subtitle", ""),
            # Подпись поверх кадра, а не под ним: плитка занята картинкой.
            "over": True,
        }

    def zones(self, ctx, data):
        # Нажатие ничего не пишет - оно открывает окно. Роли и топика у
        # зоны нет, поэтому слой команд видит чистый пульт.
        return [Zone("open", "all", pad="camera")] if data.get("src") else []

    def pad(self, ctx, data):
        if not data.get("src"):
            return {}
        return {"cam": data.get("stream") or data.get("poll"),
                "cam_poll": data.get("poll"),
                "cam_live": "1" if data.get("stream") else "0",
                "cam_life": str(data.get("life") or 0)}


@tile("link")
class Link(Tile):
    """
    Ссылка на другую панель. Ничего не читает из MQTT - только переход.

    Роль value не нужна, поэтому схема пуста: контракт не требует каналов
    там, где их нет.
    """
    roles = ()
    reads = False

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

    def pad(self, ctx, data):
        # Ссылка ничего не пишет, но нажатие у неё есть: адрес перехода
        # уезжает в тот же data-атрибут, что и команды.
        return {"href": data.get("href")} if data.get("href") else {}
