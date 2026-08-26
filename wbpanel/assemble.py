# -*- coding: utf-8 -*-
"""
Сборка панели: разделы, геометрия плиток, команды для нажатий.
"""

import html
import time
from .const import BAND, CELL, GAP, HEADER, PAD, RX, SECTION, TILE, font_scale, log
from .geometry import _fmt, cells, layout, text_width
from .state import to_float
from .status import build_status, place_chips
from .tiles import BUILDERS, DIAL_START, DIAL_SWEEP, command_topic
from .const import STALE_AFTER
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
        "kind": conf.get("type", "value"),
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

    # Все типы теперь идут одним путём через реестр. Историю принимает
    # только график, остальные её просто не спрашивают.
    builder = BUILDERS.get(tile["kind"], from_registry)
    tile.update(builder(conf, state, history))
    snaps = tile.pop("snaps", None) or [tile.pop("snap", None)]
    snaps = [s for s in snaps if s]

    tile["cmd"] = (tile_command(tile, conf, state)
                   if (interactive or tile["kind"] == "link") else None)
    if tile["cmd"] is not None:
        tile["cmd"]["i"] = str(index)
    # Собираем data-атрибуты здесь, а не в шаблоне: с trim_blocks условные
    # блоки склеиваются без пробела и ломают разметку.
    tile["attrs"] = cmd_attrs(tile)

    known = [s for s in snaps if s["known"]]
    tile["offline"] = (not known) or any(s["error"] for s in snaps)
    tile["stale"] = bool(known) and all(now - s["ts"] > STALE_AFTER for s in known)
    return tile


def tile_command(tile, conf, state):
    """
    Что публиковать по нажатию и что можно менять в полноэкранном виде.

    Шаблон про MQTT ничего не знает - он только раскладывает эти поля в
    data-атрибуты, а обработчик на странице читает их оттуда.
    """
    kind = tile["kind"]
    if kind == "link":
        return {"href": tile.get("href") or ""} if tile.get("href") else None
    if kind == "chart" or kind == "forecast":
        # графики не управляют, но раскрываются на весь экран
        return {"expand": True}

    cmd = None
    if kind in ("switch", "dimmer", "light"):
        src_ch = conf.get("channel_switch") if kind == "light" else conf.get("channel")
        topic = command_topic(src_ch, conf.get("command_topic"))
        if topic:
            v_on = str(conf.get("command_on", "1"))
            v_off = str(conf.get("command_off", "0"))
            # При invert плитка показывает логическое состояние, а в канал надо
            # писать физическое: чтобы включить нагрузку - обесточить реле.
            # state здесь логический, он же нарисован на плитке, поэтому
            # достаточно поменять местами сами значения.
            if conf.get("invert"):
                v_on, v_off = v_off, v_on
            cmd = {"topic": topic,
                   "on": v_on,
                   "off": v_off,
                   "state": "1" if tile.get("on") else "0"}
    elif kind == "thermostat":
        topic = command_topic(conf.get("channel_target"), conf.get("command_topic"))
        if topic:
            target = tile.get("target_value")
            step = float(conf.get("step", 0.5))
            lo = float(conf.get("target_min", 5))
            hi = float(conf.get("target_max", 35))
            # Плюс и минус считаются на сервере: в браузере не должно быть
            # знаний ни о шаге привода, ни о его пределах.
            def clamp(value):
                return max(lo, min(hi, round(value / step) * step))
            digits = 0 if float(step).is_integer() else 1
            if target is not None:
                # Шаблону нужно знать про зоны, а из cmd он их не видит:
                # команда уезжает в data-атрибуты, а не в поля плитки.
                tile["pad_kind"] = "thermostat"
                cmd = {"topic": topic,
                       "pad": "thermostat",
                       "target": _fmt(target, digits),
                       "up": _fmt(clamp(target + step), digits),
                       "down": _fmt(clamp(target - step), digits),
                       "step": _fmt(step, digits),
                       "min": _fmt(lo, digits),
                       "max": _fmt(hi, digits)}
                # Режим - отдельная характеристика, топик только явный:
                # у сырых топиков моста адрес команды не выводится.
                mode_topic = conf.get("command_topic_mode")
                if mode_topic:
                    cmd["mode_topic"] = mode_topic
                    cmd["mode_off"] = str(conf.get("command_mode_off", "0"))
                    cmd["mode_on"] = str(conf.get("command_mode_on", "1"))
                    cmd["enabled"] = "1" if tile.get("enabled") else "0"
                # Геометрия дуги нужна пульту: перетаскивание вдоль неё
                # переводится в градусы теми же числами, что рисуют шкалу.
                cmd["dial_start"] = "%.1f" % DIAL_START
                cmd["dial_sweep"] = "%.1f" % DIAL_SWEEP
                cmd["scale_min"] = _fmt(float(conf.get("scale_min", 14)), 1)
                cmd["scale_max"] = _fmt(float(conf.get("scale_max", 30)), 1)
                # Фактическая температура - для тонкой засечки на пульте:
                # видно, куда прибору идти от текущего значения.
                if tile.get("current_value") is not None:
                    cmd["current"] = _fmt(tile["current_value"], 1)
                cmd["status"] = tile.get("status", "")
                cmd["live"] = "1" if tile.get("on") else "0"
                cmd["cooling"] = "1" if tile.get("cooling") else "0"

    elif kind == "curtain":
        topic = command_topic(conf.get("channel"), conf.get("command_topic"))
        if topic:
            pos = tile.get("left_open")
            if pos is None:
                pos = 0.0
            cmd = {"topic": topic,
                   "on": str(conf.get("command_open", "100")),
                   "off": str(conf.get("command_close", "0")),
                   "state": "1" if pos > 0.5 else "0",
                   # долгое нажатие открывает пульт: полоса положения,
                   # кнопки «закрыть / стоп / открыть»
                   "pad": "curtain",
                   "pos": "%.3f" % pos}
            # «Стоп» - отдельная команда, у HomeKit-совместимых приводов это
            # C_TargetPositionState со значением 2. Топик только явный:
            # для сырых топиков моста угадывать нечего.
            stop = conf.get("command_topic_stop")
            if stop:
                cmd["stop"] = stop
                cmd["stop_value"] = str(conf.get("command_stop", "2"))
            if tile.get("moving"):
                cmd["moving"] = tile["moving"]

    if kind == "ac":
        dev = conf.get("device", "")
        def top(name):
            return "/devices/%s/controls/%s/on" % (dev, name) if dev else None
        cmd = {"topic": top("power"), "on": "1", "off": "0",
               "state": "1" if tile.get("on") else "0",
               "pad": "ac",
               "ac_target": top("target_temp"),
               "ac_mode": top("mode"),
               "ac_fan": top("fan_mode"),
               "ac_quiet": top("quiet"),
               "ac_swing": top("swing_mode"),
               "ac_swing_h": top("swing_h_mode"),
               "ac_swing_now": (state.snapshot("%s/swing_mode" % dev)["raw"] or "").strip(),
               "ac_swing_h_now": (state.snapshot("%s/swing_h_mode" % dev)["raw"] or "").strip(),
               "ac_lo": str(tile.get("lo", 16)),
               "ac_hi": str(tile.get("hi", 30)),
               "ac_step": str(conf.get("step", 1)),
               "ac_now": tile.get("target_num", "--"),
               "ac_cur": tile.get("current", ""),
               "ac_mode_now": tile.get("mode", ""),
               "ac_fan_now": (state.snapshot("%s/fan_mode" % dev)["raw"] or "").strip(),
               "ac_quiet_now": "1" if "тихий" in (tile.get("status") or "") else "0"}
        return cmd

    # у ленты и диммера есть что крутить, значит нужен полноэкранный пульт
    if kind in ("light", "dimmer"):
        cmd = cmd or {}
        b_topic = command_topic(conf.get("channel_brightness"),
                                conf.get("command_topic_brightness"))
        if b_topic:
            b_snap = state.snapshot(conf.get("channel_brightness", ""))
            cmd["bright"] = b_topic
            cmd["bright_max"] = str(to_float(b_snap["meta"].get("max"), 100.0) or 100.0)
        if kind == "light":
            t_topic = command_topic(conf.get("channel_temp"),
                                    conf.get("command_topic_temp"))
            if t_topic:
                t_snap = state.snapshot(conf.get("channel_temp", ""))
                cmd["temp"] = t_topic
                cmd["temp_max"] = str(to_float(t_snap["meta"].get("max"), 100.0) or 100.0)
                cmd["temp_unit"] = str(conf.get("temp_unit", "kelvin"))
                cmd["temp_lo"] = str(conf.get("temp_min", 2700))
                cmd["temp_hi"] = str(conf.get("temp_max", 6500))
                cmd["cold"] = tile.get("cold", "#56AAFF")
                cmd["warm"] = tile.get("warm", "#FF9337")
                cmd["dot_x"] = "%.3f" % (tile.get("dot_x") or 0.5)
                cmd["dot_y"] = "%.3f" % (tile.get("dot_y") or 0.5)
            cmd["pad"] = "xy"
        else:
            cmd["pad"] = "level"
            cmd["level"] = "%.3f" % (tile.get("fill") or 0.0)
        if not cmd.get("topic") and not cmd.get("bright"):
            cmd = None
    return cmd or None


CMD_ATTRS = [
    ("i", "data-i"), ("topic", "data-topic"), ("on", "data-on"),
    ("off", "data-off"), ("state", "data-state"), ("expand", "data-expand"),
    ("pad", "data-pad"), ("bright", "data-bright"), ("bright_max", "data-bright-max"),
    ("pos", "data-pos"), ("stop", "data-stop"), ("stop_value", "data-stop-value"),
    ("moving", "data-moving"), ("target", "data-target"), ("up", "data-up"),
    ("down", "data-down"), ("step", "data-step"), ("min", "data-min"),
    ("max", "data-max"), ("mode_topic", "data-mode-topic"),
    ("mode_off", "data-mode-off"), ("mode_on", "data-mode-on"),
    ("enabled", "data-enabled"), ("dial_start", "data-dial-start"),
    ("dial_sweep", "data-dial-sweep"), ("scale_min", "data-scale-min"),
    ("scale_max", "data-scale-max"), ("current", "data-current"),
    ("status", "data-status"), ("live", "data-live"), ("cooling", "data-cooling"),
    ("level", "data-level"), ("temp", "data-temp"), ("temp_max", "data-temp-max"),
    ("temp_unit", "data-temp-unit"), ("temp_lo", "data-temp-lo"),
    ("temp_hi", "data-temp-hi"), ("cold", "data-cold"), ("warm", "data-warm"),
    ("dot_x", "data-dot-x"), ("dot_y", "data-dot-y"),
    ("ac_target", "data-ac-target"), ("ac_mode", "data-ac-mode"),
    ("ac_fan", "data-ac-fan"), ("ac_quiet", "data-ac-quiet"),
    ("ac_lo", "data-ac-lo"), ("ac_hi", "data-ac-hi"), ("ac_step", "data-ac-step"),
    ("ac_now", "data-ac-now"), ("ac_cur", "data-ac-cur"),
    ("ac_mode_now", "data-ac-mode-now"), ("ac_fan_now", "data-ac-fan-now"),
    ("ac_quiet_now", "data-ac-quiet-now"),
    ("ac_swing", "data-ac-swing"), ("ac_swing_h", "data-ac-swing-h"),
    ("ac_swing_now", "data-ac-swing-now"), ("ac_swing_h_now", "data-ac-swing-h-now"),
    ("href", "data-href"),
]


def cmd_attrs(tile):
    """Команда плитки -> готовая строка data-атрибутов для тега <g>."""
    cmd = tile.get("cmd")
    if not cmd:
        return ""
    parts = ['data-kind="%s"' % xml_escape(tile["kind"]),
             'data-title="%s"' % xml_escape(tile.get("title", ""))]
    for key, attr in CMD_ATTRS:
        value = cmd.get(key)
        if value in (None, "", False):
            continue
        parts.append('%s="%s"' % (attr, xml_escape("1" if value is True else str(value))))
    return " " + " ".join(parts)


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def folded_sections(panel_conf, sections, closed, opened):
    """
    Какие разделы свёрнуты.

    Обычный режим: свёрнуто то, что перечислено в closed.
    Режим гармошки (accordion): раскрыт ровно один раздел - тот, что
    назван в opened, иначе первый. Так на сводной панели видно одну
    комнату целиком, а не начало каждой.
    """
    keys = [key for _t, _tiles, _src, key in sections if key]
    if not keys:
        return set()
    if panel_conf.get("accordion"):
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
                  "arrow_y": y_cursor + 16}
            # Чипы прижаты к правому краю заголовка раздела: там пусто,
            # и не приходится гадать, какой ширины получился текст.
            # Высота полосы раздела 44, чип 36 - помещается без сдвига плиток.
            # Чип центрируется по оптической середине заголовка, а не по
            # полосе раздела: базовая линия текста ниже его середины на
            # половину роста прописных, отсюда -2 вместо +4.
            hd["chips"] = place_chips(
                build_status(status_src, state), right_edge, y_cursor - 2)
            headers.append(hd)
            y_cursor += SECTION

        # Свёрнутый раздел не занимает места и не собирает плиток: их не
        # надо ни считать, ни подписывать. Это и есть главная выгода -
        # сводная панель из шести комнат перестаёт быть простынёй.
        if folded:
            continue

        placed, rows = layout(tiles_conf, cols)

        for conf, row, col, w, h in placed:
            out.append(build_tile(
                conf, index,
                PAD + col * (CELL + GAP), y_cursor + row * (CELL + GAP),
                w * CELL + (w - 1) * GAP, h * CELL + (h - 1) * GAP,
                panel_conf, state, history, interactive))
            index += 1

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
