# -*- coding: utf-8 -*-
"""
Плитка списка: верхние пункты одного из списков config.yaml.

Плитка только показывает. Галочки, добавление, перестановка - в окне на
весь экран: на плитке размером с ладонь строка в полтора десятка
пикселей, и промах по ней отметил бы не тот пункт. Нажатие по плитке
открывает окно сразу, без долгого нажатия: ничего, кроме окна, у неё нет.

Сколько пунктов видно, решает высота: плитку растянули - строк стало
больше. Порядок задаёт человек, стрелками в окне, поэтому сверху стоит
самое важное. Не влезло - последняя строка говорит, сколько ещё.

    lists:
      shopping: {title: "Покупки", keep_done: 7d}

    - type: list
      list: shopping
      w: 2
      h: 2
      font: 15          # кегль пунктов, по умолчанию 15
"""

from .const import CELL
from .geometry import text_width
from .registry import Tile, Zone, tile


#: Средняя ширина буквы в долях кегля. Общая оценка проекта (0.56)
#: рассчитана на цифры и латиницу, а кириллица в пунктах списка шире -
#: с ней длинная строка вылезала за край плитки.
LETTER = 0.62


def _fit(text, width, size):
    """Обрезать строку по ширине с многоточием."""
    if text_width(text, size, LETTER) <= width:
        return text
    out = text
    while out and text_width(out + "…", size, LETTER) > width:
        out = out[:-1]
    return out.rstrip() + "…"


@tile("list")
class TodoList(Tile):
    roles = ()
    # Из MQTT плитка ничего не читает: данные в файле демона. Отметка «нет
    # данных» к ней неприменима.
    reads = False

    def prepare(self, ctx):
        from .lists import lists

        name = str(ctx.opt("list") or "")
        base = {"on": False, "icon": ctx.opt("icon", "list"), "rows": [],
                "count": 0, "more": 0, "list": name, "always_status": True}
        if not name:
            base["status"] = "не задано поле list"
            return base
        if not lists.known(name):
            # Прямо на плитке, а не только в журнале: пустую плитку примут
            # за пустой список.
            base["status"] = "нет списка %s в lists:" % name
            return base

        view = lists.view(name)
        opened = view["open"]
        base["ok"] = True
        if not ctx.opt("title"):
            # Название берём у списка: писать его дважды, в lists: и в
            # плитке, незачем.
            base["title"] = view["title"]
            base["title_lines"] = [view["title"]]
            base["title_s"] = view["title"][:10]
        base["count"] = len(opened)
        base["status"] = ""

        width = float(ctx.opt("inner_w") or 0)
        height = float(ctx.opt("inner_h") or 0)
        # Название делит строку с числом пунктов справа: на узкой плитке
        # без обрезки они наезжали друг на друга.
        title = str(ctx.opt("title") or view["title"])
        count_w = text_width(str(len(opened)), 17) + 10
        base["head"] = _fit(title, width - 50 - 20 - count_w, 15.5)
        # Кегль не растёт вместе с плиткой: на панели крупная плитка нужна
        # ради лишних строк, а не ради крупных букв. Масштаб документа
        # (--fs) на панели всегда единица, сюда его не подмешиваем.
        fs = 1.0
        size = float(ctx.opt("font", 15)) * fs
        line = round(size * 1.65, 1)
        top = round(58 * fs, 1)              # под строкой названия
        bottom = round(14 * fs, 1)

        # Мелкой и низкой плитке строк не достаётся: только название и
        # число открытых пунктов.
        if width < CELL * 2 or height < 117:
            base["compact_list"] = True
            if opened:
                base["status"] = _fit(opened[0]["text"], width - 40, 13.5)
            return base

        room = max(0, int((height - top - bottom) // line))
        shown = opened[:room]
        if len(opened) > room and room > 0:
            # Последняя строка - «ещё N», иначе непонятно, что список длиннее.
            shown = opened[:room - 1]
            base["more"] = len(opened) - len(shown)

        text_room = width - 20 - 18 - 16
        y = top + size
        rows = []
        for item in shown:
            rows.append({"text": _fit(item["text"], text_room, size), "y": round(y, 1)})
            y += line
        base["rows"] = rows
        base["more_y"] = round(y, 1)
        base["size"] = round(size, 1)
        base["dot_r"] = round(max(2.5, size * 0.2), 1)
        if not opened:
            base["status"] = "пусто"
            if view["done"]:
                base["status"] = "всё сделано"
        return base

    def zones(self, ctx, data):
        if not data.get("ok"):
            return []
        return [Zone("open", "all", pad="list")]

    def pad(self, ctx, data):
        return {"list": data.get("list")} if data.get("ok") else {}
