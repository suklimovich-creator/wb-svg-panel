# -*- coding: utf-8 -*-
"""
Проверка конфига: что панель поняла, а что нет.

Раньше «не задана роль target» уходило в журнал, где его никто не видит:
плитка молча рисовалась пустой, и вечер уходил на выяснение, почему.
Здесь то же самое собирается в список и показывается на странице.

Проверки намеренно разной строгости. Ошибка - плитка точно не работает.
Предупреждение - работает, но, возможно, не так, как задумано: канал есть
в конфиге, но брокер про него молчит, или поле названо мимо роли. Второе
особенно коварно: опечатка в имени поля не ломает ничего заметного, а
просто тихо теряет часть настройки.
"""

from .const import log
from .registry import (REGISTRY, channels_of, guess_type,
                       resolve_type, writable_of)


ERROR = "ошибка"
WARNING = "внимание"


def _problem(level, panel, tile, text, hint=None):
    return {"level": level, "panel": panel,
            "tile": tile.get("title") or "без названия",
            "kind": tile.get("type", "value"), "text": text, "hint": hint}


def check_config(config, state, history=None):
    """
    Пройти по всем панелям и собрать всё, что выглядит неправильно.

    Возвращает список словарей. Порядок - ошибки раньше предупреждений,
    внутри уровня по панелям, как в конфиге: чинить удобнее сверху вниз.
    """
    from .assemble import resolve_sections

    out = []
    known = set(state.values) if state else set()
    logged = set()
    if history is not None:
        try:
            logged = set(history.channels_with_history())
        except Exception:                       # база может быть недоступна
            logged = set()

    for name, panel in (config.panels or {}).items():
        title = (panel or {}).get("title") or name
        interactive = bool((panel or {}).get("interactive",
                           config.get("interactive", False)))

        for section in ((panel or {}).get("sections") or []):
            src = section.get("from") if isinstance(section, dict) else None
            if src and src not in (config.panels or {}):
                out.append({"level": ERROR, "panel": title, "tile": "раздел",
                            "kind": "section",
                            "text": "ссылается на панель %r, которой нет" % src,
                            "hint": "проверьте имя в from:"})

        for _t, tiles, _s, _k in resolve_sections(panel, config.panels):
            for tile in tiles:
                out.extend(_check_tile(tile, title, name, config, state,
                                       known, logged, interactive))

    rank = {ERROR: 0, WARNING: 1}
    out.sort(key=lambda p: (rank.get(p["level"], 9), p["panel"]))
    return out


def _check_tile(tile, panel_title, panel_name, config, state,
                known, logged, interactive):
    out = []
    kind = resolve_type(tile, state)

    # --- служба не опознана -----------------------------------------------
    # Тип не задан, а по характеристикам угадать не вышло. Молча подставить
    # value было бы хуже всего: плитка нарисуется, покажет что-то одно, и
    # человек будет думать, что панель сломалась, вместо того чтобы дописать
    # одну строку. Поэтому перечисляем, что нашлось.
    if kind == "unknown":
        found = guess_type(tile, state)[1]
        out.append(_problem(
            ERROR, panel_title, tile,
            "тип не опознан по службе %s" % (tile.get("service") or ""),
            "характеристики: %s. Допишите type: явно"
            % (", ".join(sorted(found)) or "не найдены — служба молчит")))
        return out

    cls = REGISTRY.get(kind)
    if cls is None:
        out.append(_problem(ERROR, panel_title, tile,
                            "неизвестный тип плитки %r" % kind,
                            "известные: " + ", ".join(sorted(REGISTRY))))
        return out

    # --- обязательные роли -------------------------------------------------
    obj = cls()
    from .sources import bind
    bound = bind(tile, obj.schema(), state, obj)
    problem = obj.check(tile, bound)
    if problem:
        out.append(_problem(ERROR, panel_title, tile, problem))

    # --- ссылка ведёт в никуда --------------------------------------------
    if kind == "link":
        target = tile.get("panel")
        if target and target not in (config.panels or {}):
            out.append(_problem(ERROR, panel_title, tile,
                                "ведёт на панель %r, которой нет" % target))

    # --- камера ------------------------------------------------------------
    # Плитка ничего не читает из MQTT, поэтому обычные проверки её не
    # касаются вовсе: ошибиться можно только в конфиге камер, и молча это
    # выглядит как чёрный прямоугольник.
    if kind == "camera":
        from . import cameras
        name = tile.get("camera") or tile.get("name")
        if not name:
            out.append(_problem(ERROR, panel_title, tile,
                                "не задано поле camera",
                                "имя раздела из cameras: верхнего уровня"))
        elif not isinstance((cameras.all_confs() or {}).get(name), dict):
            out.append(_problem(
                ERROR, panel_title, tile,
                "камера %s не описана" % name,
                "известные: %s" % (", ".join(cameras.names()) or "ни одной")))
        else:
            cam = cameras.get(name)
            if cam and not cam.host:
                out.append(_problem(ERROR, panel_title, tile,
                                    "у камеры %s не задан host" % name))
            if cam and not cam.user:
                out.append(_problem(
                    WARNING, panel_title, tile,
                    "у камеры %s не задан user" % name,
                    "Hikvision без аутентификации не отдаёт ни кадр, ни поток"))

    # --- каналы, про которые брокер молчит ---------------------------------
    # Самая частая поломка: опечатка в имени. Плитка рисуется, но пустая.
    for channel in sorted(channels_of(tile, state)):
        if channel not in known:
            out.append(_problem(WARNING, panel_title, tile,
                                "канал %s не найден в MQTT" % channel,
                                "опечатка в имени либо прибор не отвечает"))

    # --- нажимать нечего ---------------------------------------------------
    # Плитка выглядит управляемой, но ни один командный топик не выведен:
    # человек жмёт, ничего не происходит, и виноватым выглядит прибор.
    if interactive and kind in ("switch", "light", "dimmer", "curtain",
                                "thermostat", "scene", "ac"):
        if not writable_of(tile, state):
            out.append(_problem(WARNING, panel_title, tile,
                                "плитка управляемая, но писать некуда",
                                "нужен command_topic либо канал без readonly"))

    # --- график без истории ------------------------------------------------
    if kind == "chart" and logged:
        for spec in (tile.get("series") or []):
            channel = spec.get("channel")
            if channel and channel not in logged:
                out.append(_problem(WARNING, panel_title, tile,
                                    "канал %s не пишется в историю" % channel,
                                    "график будет пустым; "
                                    "добавьте канал в wb-mqtt-db"))
    return out


def summary(problems):
    """Сколько чего нашлось - для строки над списком."""
    return {"errors": sum(1 for p in problems if p["level"] == ERROR),
            "warnings": sum(1 for p in problems if p["level"] == WARNING),
            "total": len(problems)}


def as_text(problems):
    """Тот же отчёт для консоли: curl -s localhost:8088/check?format=text"""
    if not problems:
        return "Проблем не найдено.\n"
    lines = []
    for p in problems:
        lines.append("%-9s %s / %s (%s): %s" % (
            p["level"], p["panel"], p["tile"], p["kind"], p["text"]))
        if p["hint"]:
            lines.append("%-9s   %s" % ("", p["hint"]))
    s = summary(problems)
    lines.append("")
    lines.append("Итого: ошибок %d, предупреждений %d." % (s["errors"], s["warnings"]))
    return "\n".join(lines) + "\n"
