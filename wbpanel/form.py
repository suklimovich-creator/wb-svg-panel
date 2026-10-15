"""
Форма темы для геометрии (с 1.34): край ручек и колёс.

Пути зубчатых колёс считает Python (skeuo, ac_disk), а тема известна
только сборке плитки. Протаскивать её через каждую функцию геометрии -
два десятка подписей ради одного слова. Поэтому сборка плитки ставит
форму сюда (assemble.build_tile), а геометрия читает. ContextVar - своё
значение у каждого потока: две страницы с разными темами, которые
рисуются одновременно, друг другу не мешают.
"""

import contextvars

#: Край ручек и колёс: зубцы (как было), гладкий, рифлёный (мелкая насечка).
EDGES = ("teeth", "smooth", "knurl")

_EDGE = contextvars.ContextVar("wbpanel_edge", default="teeth")
#: Скругление углов плитки (с 1.35) - от него отступают элементы у угла.
_RADIUS = contextvars.ContextVar("wbpanel_radius", default=24)


def use(info):
    """Поставить форму темы (palette.info(...)) для текущей сборки."""
    _EDGE.set((info or {}).get("edge") or "teeth")
    _RADIUS.set((info or {}).get("radius", 24))


def edge():
    return _EDGE.get()


def radius():
    return _RADIUS.get()
