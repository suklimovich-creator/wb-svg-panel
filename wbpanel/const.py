# -*- coding: utf-8 -*-
"""
Пути, размеры сетки и прочие постоянные величины.
"""

import logging
import os


# Корень проекта - на уровень выше пакета: рядом лежат config.yaml,
# templates/ и install.sh, и все пути в документации отсчитываются оттуда.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DB_CANDIDATES = [
    "/var/lib/wirenboard/db/data.db",
    "/mnt/data/var/lib/wirenboard/db/data.db",
    "/var/lib/wb-mqtt-db/data.db",
    "/mnt/data/var/lib/wb-mqtt-db/data.db",
]


def find_db():
    """Путь к базе истории отличается между прошивками - ищем."""
    env = os.environ.get("WB_PANEL_DB")
    if env:
        return env
    for path in DB_CANDIDATES:
        if os.path.exists(path):
            return path
    return DB_CANDIDATES[0]


DB_PATH = find_db()


CONFIG_PATH = os.environ.get("WB_PANEL_CONFIG", os.path.join(BASE_DIR, "config.yaml"))


# Шаблон ищется и в подкаталоге templates/, и рядом с panel.py: при скачивании
# проекта одним архивом вложенная папка часто теряется, и это нормально.
TEMPLATE_DIRS = [
    os.environ.get("WB_PANEL_TEMPLATES", os.path.join(BASE_DIR, "templates")),
    BASE_DIR,
]


log = logging.getLogger("wb-svg-panel")


# --------------------------------------------------------------------------
# Геометрия сетки. Меняется здесь и больше нигде.
# --------------------------------------------------------------------------
# Сетка считается в ПОЛОВИНКАХ плитки. Обычная плитка занимает 2x2 таких
# ячейки и остаётся ровно 170 px, как раньше, - геометрия не изменилась.
# Зато теперь можно поставить мелкую плитку в одну ячейку: w: 0.5, h: 0.5.
CELL = 78           # сторона мелкой ячейки, px


GAP = 14            # зазор между ячейками


TILE = CELL * 2 + GAP   # обычная плитка, 170 px


PAD = 20            # внешние поля


HEADER = 54         # высота шапки панели (0 - если title не задан)


RX = 24             # радиус скругления плитки


SECTION = 44        # высота заголовка раздела внутри панели


BAND = (0.40, 0.82) # в какой доле высоты плитки живёт кривая графика


def font_scale(height, base=TILE):
    """
    Во сколько раз увеличить подписи на крупной плитке.

    Не линейно по высоте: при росте в 3.6 раза буквы в 3.6 раза выглядели бы
    плакатом. Показатель 0.6 даёт заметный, но спокойный рост.
    """
    k = max(float(height) / float(base), 1.0) ** 0.6
    return round(min(k, 2.4), 2)


# ==========================================================================
#  Подготовка плиток: превращаем конфиг + состояние в готовые к отрисовке числа
# ==========================================================================

STALE_AFTER = 300  # секунд без обновления - канал считается протухшим
