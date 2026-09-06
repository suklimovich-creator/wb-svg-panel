# -*- coding: utf-8 -*-
"""
Сборка панели: разделы, геометрия плиток, команды для нажатий.
"""

import html
import time
from .commands import cmd_attrs, xml_escape
from .commands import build as build_command
from .const import BAND, CELL, GAP, HEADER, PAD, RX, SECTION, TILE, font_scale, log
from .const import STALE_AFTER
from .geometry import _fmt, cells, layout, text_width
from .status import build_status, place_chips
from .registry import resolve_type
from .tiles import from_registry

# Текущий конфиг. Заполняется в web.main() при старте: сюда сборка плиток
# заглядывает за общими настройками панели (show_status, interactive).
# Через параметр их не передать - build_tile вызывается из десятка мест.
config = None


def resolve_sections(panel_conf, all_panels):
    """
    Разделы панели -> [(заголовок или None, список плиток)].

    Раздел можно задать своими плитками либо ссылкой `from:` на другую
    панель - тогда её плитки подтягиваются целиком и не дублируются в
    конфиге. Панель без `sections` - это один безымянный раздел.
    """
    sections = panel_conf.get("sections")
    if not sections:
        return [(None, panel_conf.get("tiles", []) or [], panel_conf, "")]

    out = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        tiles = list(section.get("tiles", []) or [])
        src_name = section.get("from")
        # Откуда брать статусы раздела: свои, либо от панели-источника.
        # Комната описывает статусы один раз у себя, а сводные панели
        # подхватывают их вместе с плитками.
        src = section
        if src_name:
            source = (all_panels or {}).get(src_name) or {}
            tiles = list(source.get("tiles", []) or []) + tiles
            title = section.get("title", source.get("title", src_name))
            if not section.get("status"):
                src = source
        else:
            title = section.get("title")
        if tiles:
            # Имя раздела - то, по чему его узнают в адресе: имя
            # панели-источника, иначе заголовок. Порядковый номер не
            # годится: добавили раздел в середину - и свёрнутым оказался
            # чужой.
            key = section.get("key") or src_name or title or ""
            out.append((title, tiles, src, key))
    return out or [(None, [], panel_conf, "")]


def build_tile(conf, index, x, y, pw, ph, panel_conf, state, history, interactive):
    """
    Собрать одну плитку: геометрия, данные, команда.

    Вынесено из prepare отдельно, потому что ту же плитку нужно уметь
    нарисовать и в произвольном размере - для полноэкранного вида.
    """
    now = time.time()
    tile = {
        "i": index,
        # Тип может быть не задан: у службы Sprut.hub он выводится из
        # набора характеристик. Явный type: всегда сильнее.
        "kind": resolve_type(conf, state),
        "title": conf.get("title", ""),
        "subtitle": conf.get("subtitle", ""),
        "icon": conf.get("icon"),
        # Строка «Включено / Выключено» под названием плитки дублирует
        # оранжевую рамку и тумблер, поэтому скрыта.
        "show_status": bool(conf.get("show_status",
                            panel_conf.get("show_status",
                            (config.get("show_status", False)
                             if config else False)))),
        "x": x, "y": y, "w": pw, "h": ph,
        # мелкая плитка не вмещает второй строки и крупной цифры
        "compact": pw < CELL * 2,
        # на 78 px влезает примерно 9 знаков
        # Перенос строки в заголовке: "Дим трек\nправый" -> две строки.
        # Пока не нужен, но пусть будет - плитки узкие, названия длинные.
        "title_lines": [s for s in str(conf.get("title", "")).split("\n") if s != ""]
                       or [""],
        "title_s": (conf.get("title", "").replace("\n", " ")[:9] + "…")
                   if len(conf.get("title", "").replace("\n", " ")) > 10
                   else conf.get("title", "").replace("\n", " "),
        "inner_w": pw,
        "chart_h": ph,
    }
    conf = dict(conf)
    conf["inner_w"] = pw
    conf["chart_h"] = ph
    conf["inner_h"] = ph
    # Крупная плитка рисуется теми же макросами, но с увеличенным кеглем.
    # Сборщикам это нужно, чтобы считать ширину текста в тех же единицах.
    conf["fs"] = font_scale(ph)
    # Тема нужна графику: подписи значений красятся под цвет подложки, а
    # она разная. CSS-переменная тут не подходит - цвет у каждой серии свой.
    conf["theme"] = (config.get("theme", "light") if config else "light")

    # Все типы идут одним путём через реестр. Историю принимает только
    # график, остальные её просто не спрашивают.
    tile.update(from_registry(conf, state, history))
    snaps = tile.pop("snaps", None) or [tile.pop("snap", None)]
    snaps = [s for s in snaps if s]

    # Служебные поля реестра до шаблона доходить не должны.
    zones = tile.pop("_zones", None) or []
    bound = tile.pop("_bound", None) or {}
    pad = tile.pop("_pad", None) or {}
    tile.pop("_writable", None)
    # Плитка, ничего не читающая из MQTT, не бывает «без данных».
    reads = tile.pop("_reads", True)

    # Ссылка ведёт на другую панель и никуда не пишет, поэтому работает и
    # на панели, где управление выключено.
    if interactive or tile["kind"] == "link":
        tile["cmd"], tile["zones"] = build_command(zones, bound, tile, pad,
                                                   pw, ph)
    else:
        tile["cmd"], tile["zones"] = None, []
    if tile["cmd"] is not None:
        tile["cmd"]["i"] = str(index)
    # Собираем data-атрибуты здесь, а не в шаблоне: с trim_blocks условные
    # блоки склеиваются без пробела и ломают разметку.
    tile["attrs"] = cmd_attrs(tile)

    known = [s for s in snaps if s["known"]]
    tile["offline"] = reads and ((not known) or any(s["error"] for s in snaps))
    # Через сколько молчания канал считается протухшим. Пять минут годятся
    # для Modbus, который опрашивается непрерывно, но не для Zigbee: датчик
    # на батарейке отчитывается раз в полчаса и был бы вечно серым. Задаётся
    # у плитки, у панели или глобально.
    stale_after = float(conf.get("stale_after",
                        panel_conf.get("stale_after",
                        (config.get("stale_after", STALE_AFTER)
                         if config else STALE_AFTER))))
    tile["stale"] = reads and bool(known) and all(
        now - s["ts"] > stale_after for s in known)
    return tile


def folded_sections(panel_conf, sections, closed, opened):
    """
    Какие разделы свёрнуты.

    Обычный режим: свёрнуто то, что перечислено в closed.
    Режим гармошки (accordion): раскрыт ровно один раздел - тот, что
    назван в opened, иначе первый. Так на сводной панели видно одну
    комнату целиком, а не начало каждой.

    Особый случай - opened == "-": свёрнуты все. Нажатие на раскрытый
    раздел раньше не делало ничего, и свернуть панель целиком было
    нельзя, хотя список одних заголовков - самый быстрый способ дойти
    до нужной комнаты.
    """
    keys = [key for _t, _tiles, _src, key in sections if key]
    if not keys:
        return set()
    if panel_conf.get("accordion"):
        if opened == "-":
            return set(keys)
        current = opened if opened in keys else keys[0]
        return set(k for k in keys if k != current)
    return set(k for k in keys if k in closed)


def prepare(panel_conf, state, history, cols=None, all_panels=None, name=None,
            closed=(), opened=None):
    big_cols = int(cols or panel_conf.get("cols", 4))
    interactive = bool(panel_conf.get("interactive",
                       (config.get("interactive", False) if config else False)))
    cols = big_cols * 2          # дальше всё в мелких ячейках
    sections = resolve_sections(panel_conf, all_panels)

    out = []
    headers = []
    index = 0
    y_cursor = PAD + HEADER

    right_edge = PAD * 2 + cols * CELL + (cols - 1) * GAP - PAD

    hidden = folded_sections(panel_conf, sections, set(closed or ()), opened)

    for title, tiles_conf, status_src, key in sections:
        folded = bool(key) and key in hidden
        # плитки-ссылки должны знать, откуда на них нажали
        if name:
            for conf in tiles_conf:
                if isinstance(conf, dict) and conf.get("type") == "link":
                    conf.setdefault("_from", name)
        if title:
            # Было 6: чипы высотой 36 подходили вплотную к плиткам сверху.
            y_cursor += 14
            hd = {"title": title, "x": PAD, "y": y_cursor + 22, "chips": [],
                  # Заголовок становится кнопкой: нажатие сворачивает и
                  # разворачивает раздел. Стрелка показывает, что будет.
                  "key": key, "folded": folded,
                  # Ширина считается для обычного начертания, а заголовок
                  # полужирный - отсюда поправка 1.08. Плюс просвет, иначе
                  # стрелка липнет к последней букве.
                  "arrow_x": PAD + text_width(title, 18) * 1.08 + 16,
                  "arrow_y": y_cursor + 16,
                  # Нажимать хочется по всей строке, а не по стрелке.
                  # Ширину дотягиваем до первого чипа: у них свои нажатия,
                  # заезжать на них нельзя. Ставится ниже, когда чипы
                  # уже разложены.
                  "tap_x": PAD - 8, "tap_y": y_cursor - 2, "tap_w": 0}
            # Чипы прижаты к правому краю заголовка раздела: там пусто,
            # и не приходится гадать, какой ширины получился текст.
            # Высота полосы раздела 44, чип 36 - помещается без сдвига плиток.
            # Чип центрируется по оптической середине заголовка, а не по
            # полосе раздела: базовая линия текста ниже его середины на
            # половину роста прописных, отсюда -2 вместо +4.
            hd["chips"] = place_chips(
                build_status(status_src, state), right_edge, y_cursor - 2)
            # До первого чипа минус просвет, иначе до края строки
            first = min([c["x"] for c in hd["chips"]] or [right_edge + 8])
            hd["tap_w"] = max(60, first - 12 - hd["tap_x"])
            headers.append(hd)
            y_cursor += SECTION

        # Свёрнутый раздел не занимает места и не рисуется - в этом и
        # выгода, сводная панель из шести комнат перестаёт быть простынёй.
        # Но номера его плиток остаются за ним: номер - это ключ, по
        # которому полноэкранный вид находит плитку в полном списке панели.
        # Пропустить свёрнутое - значит сдвинуть всё, что ниже, и по
        # нажатию откроется чужая плитка. Раскрытый первым раздел совпадал
        # случайно, а дальше по панели съезжало.
        if folded:
            index += len(tiles_conf)
            continue

        placed, rows = layout(tiles_conf, cols)

        # layout переставляет плитки: закреплённые по col/row встают
        # первыми. Номер должен считаться по порядку в конфиге, а не по
        # порядку размещения - полноэкранный вид ищет в неразложенном
        # списке.
        order = {id(conf): n for n, conf in enumerate(tiles_conf)}

        for conf, row, col, w, h in placed:
            out.append(build_tile(
                conf, index + order.get(id(conf), 0),
                PAD + col * (CELL + GAP), y_cursor + row * (CELL + GAP),
                w * CELL + (w - 1) * GAP, h * CELL + (h - 1) * GAP,
                panel_conf, state, history, interactive))

        index += len(tiles_conf)

        if rows:
            y_cursor += rows * CELL + (rows - 1) * GAP + GAP

    width = PAD * 2 + cols * CELL + (cols - 1) * GAP
    height = max(y_cursor - GAP + PAD, PAD * 2 + HEADER)

    # Статусы самой панели - в шапке, левее часов: справа стоит время,
    # и наезжать на него нельзя.
    # Заголовок панели: базовая линия на 44, значит середина чипа на 38.
    top_chips = place_chips(build_status(panel_conf, state),
                            width - PAD - 62, 20)
    return out, width, height, headers, top_chips
