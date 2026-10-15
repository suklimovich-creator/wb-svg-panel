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
from .skeuo import diode_box, switch_layout
from .tiles import command_topic
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

        # С 1.17 выключатель - клавиша с диодом-ночником (look: classic -
        # прежний значок или тумблер).
        key = None
        if ctx.opt("look", "key") != "classic":
            key = switch_layout(float(ctx.opt("inner_w") or 170), float(ctx.opt("chart_h") or 170))
            key["led"] = diode_box(key["kx"], key["ky"], key["kw"], key["kh"], key["horiz"])
        return {
            "on": on,
            "status": ctx.opt("on_text", "Вкл") if on else ctx.opt("off_text", "Выкл"),
            "icon": ctx.opt("icon", "toggle"),
            "short": ctx.opt("badge", ""),
            "key": key,
            "low_x": key["text_x"] if key and key["kind"] == "wide" else None,
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


@tile("scenes")
class Scenes(Tile):
    """
    Сцены одной плиткой (с 1.19): клавиши сеткой 3×3, от 10 до 12 - 4×3.

    Порядок - как в конфиге: задумано от тусклых к ярким, первой «выключить
    всё». Чем дальше клавиша, тем теплее её лицо, точки в углу - яркость
    сцены (можно задать level: 0…1 у пункта). Каждая клавиша - отдельная
    кнопка: нажал - сработало, как у одиночной плитки scene.

        - type: scenes
          title: "Сцены"
          w: 1.5
          h: 1.5
          items:
            - {title: "Выкл. всё", icon: power, channel: "lightingScenes/off_all"}
            - {title: "Спать",     icon: moon,  channel: "lightingScenes/bedtime"}
    """

    roles = ()

    @staticmethod
    def _items(conf):
        return [i for i in (conf.get("items") or []) if isinstance(i, dict) and i.get("channel")]

    @staticmethod
    def channels(conf):
        return set(i["channel"] for i in Scenes._items(conf))

    @staticmethod
    def writes(conf):
        out = set()
        for i in Scenes._items(conf):
            t = command_topic(i["channel"], i.get("command_topic"))
            if t:
                out.add(t)
        return out

    def prepare(self, ctx):
        items = self._items(ctx.conf)
        n = len(items)
        w = float(ctx.opt("inner_w") or 262)
        h = float(ctx.opt("chart_h") or 262)
        cols = 3 if n <= 9 else 4
        rows = max(1, -(-n // cols))
        pad, gap, top = 12.0, 8.0, 40.0
        kw = (w - 2 * pad - (cols - 1) * gap) / cols
        kh = (h - top - pad - (rows - 1) * gap) / rows
        keys = []
        for k, item in enumerate(items):
            snap = ctx.state.snapshot(item["channel"])
            ctx.snaps.append(snap)
            raw = str(snap.get("raw") or "").strip().lower()
            on = str(item.get("command_on")) if item.get("command_on") is not None \
                else Scene.FORMS.get(raw, ("1", "0"))[0]
            lv = item.get("level")
            if lv is None:
                lv = k / float(max(1, n - 1))
            lv = max(0.0, min(1.0, float(lv)))
            x = pad + (k % cols) * (kw + gap)
            y = top + (k // cols) * (kh + gap)
            keys.append({
                "x": round(x, 1), "y": round(y, 1), "w": round(kw, 1), "h": round(kh, 1),
                "title": str(item.get("title") or ""), "icon": item.get("icon") or "bulb",
                "topic": command_topic(item["channel"], item.get("command_topic")),
                "value": on, "level": round(lv, 2),
                "glow": round(0.05 + 0.4 * lv, 2),
                "dots": [lv >= (d + 0.5) / 5.0 for d in range(5)],
                "isize": round(min(28.0, kh * 0.3, kw * 0.32), 1),
                "font": round(min(13.0, kw / 7.2), 1),
            })
        return {"on": False, "skeys": keys, "status": ""}

    def zones(self, ctx, data):
        return []


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
        from .status import status_channels, status_entries
        out = set()
        for entry in status_entries(conf):
            out.update(status_channels(entry))
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
