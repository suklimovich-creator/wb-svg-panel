# -*- coding: utf-8 -*-
"""
Строка состояния комнаты: чипы присутствия и режимов приборов.
"""

import time
from .state import to_float


# ==========================================================================
# Строка состояния комнаты
# ==========================================================================
#
# Показываем только события, а не измерения: присутствие, открытую дверь,
# протечку, работающий прибор. Всё, у чего есть плитка с графиком, сюда не
# попадает - дублировать незачем.
#
# Когда в комнате всё спокойно, строка пустая. Молчание само по себе
# информативно, и глазу не за что цепляться.

# Когда канал последний раз выходил за порог: {ключ: время}.
# Живёт в памяти процесса и обновляется при отрисовке. Панель перерисовывают
# раз в десять секунд, пока открыта хоть одна вкладка; если не смотрит никто,
# то и показывать давность некому.
SEEN = {}


# Значок «был недавно» для тех, у кого активное состояние - движение
FADED = {"motion": "person"}


# Цвет бейджа режима. Оранжевый чип означает «обрати внимание», и работающий
# кондиционер под это не подходит - он просто работает. Поэтому у приборов
# чип нейтральный, а режим показан цветом бейджа: холод синий, тепло красное,
# обдув зелёный, авто и возврат на базу нейтральные.
BADGE_COLORS = {
    "snow":  "#3B8FD4",
    "heat":  "#D9553B",
    "fan":   "#4E9A6B",
    "water": "#3B8FD4",
    "auto":  None,
    "home":  None,
}


def status_entries(panel_conf):
    """Список описаний статусов панели, всегда список словарей."""
    out = []
    for entry in (panel_conf or {}).get("status", []) or []:
        if isinstance(entry, dict) and (entry.get("channel")
                                        or entry.get("value_channel")):
            out.append(entry)
    return out


def parse_span(text, default=0):
    """'20m' -> 1200. Понимает s/m/h, голое число считает минутами."""
    if text is None:
        return default
    raw = str(text).strip().lower()
    if not raw:
        return default
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(raw[-1])
    try:
        return int(float(raw[:-1] if mult else raw)) * (mult or 60)
    except ValueError:
        return default


def status_required(entry, state):
    """
    Дополнительное условие показа - обычно «прибор включён».

    У кондиционера канал mode продолжает сообщать cool и после выключения,
    поэтому по нему одному судить нельзя.

        require:
          channel: "haier_bedroom/power"
          when: "1"
    """
    req = entry.get("require")
    if not req:
        return True
    for item in (req if isinstance(req, list) else [req]):
        if not isinstance(item, dict) or not item.get("channel"):
            continue
        raw = state.snapshot(item["channel"])["raw"]
        if not status_matches(item, raw):
            return False
    return True


def status_matches(entry, raw):
    """Выполнено ли условие показа: above / below / when."""
    if raw is None or raw == "":
        return False
    if entry.get("above") is not None or entry.get("below") is not None:
        num = to_float(raw)
        if num is None:
            return False
        if entry.get("above") is not None and num <= float(entry["above"]):
            return False
        if entry.get("below") is not None and num >= float(entry["below"]):
            return False
        return True
    when = entry.get("when")
    if when is not None:
        allowed = when if isinstance(when, (list, tuple)) else [when]
        return str(raw).strip() in [str(x) for x in allowed]
    # ни одного условия - показываем всё, кроме явно выключенного
    return str(raw).strip() not in ("0", "off", "false", "False", "none")


def chip_width(value):
    """Должно совпадать с макросом chip() в шаблоне."""
    if not value:
        return 7 + 26 + 7
    return 10 + 26 + 6 + len(str(value)) * 8.5 + 12


def build_status(panel_conf, state, now=None):
    """Описания статусов -> чипы для шаблона."""
    now = now or time.time()
    chips = []
    for entry in status_entries(panel_conf):
        key = entry.get("channel")
        raw = state.snapshot(key)["raw"] if key else None
        icon = entry.get("icon", "power")
        active = bool(key) and status_matches(entry, raw) \
            and status_required(entry, state)

        fade = parse_span(entry.get("fade"), 0)
        if active:
            SEEN[key] = now
        elif fade and key:
            # Условие уже не выполняется, но недавно выполнялось: показываем
            # приглушённый вариант. Для движения это человечек стоя - тот же
            # персонаж, другая поза, читается как «был, но не сейчас».
            if now - SEEN.get(key, 0) > fade:
                continue
            icon = entry.get("icon_faded") or FADED.get(icon, icon)
        else:
            continue

        # Бейдж режима: либо фиксированный, либо по значению канала
        badge = entry.get("badge")
        badges = entry.get("badges") or {}
        if badges and raw is not None:
            badge = badges.get(str(raw).strip(), badge)

        # Число рядом со значком - только если оно меняет решение:
        # уставка кондиционера, процент уборки. Давность движения не меняет.
        value = None
        if entry.get("value_channel"):
            num = to_float(state.snapshot(entry["value_channel"])["raw"])
            if num is not None:
                digits = int(entry.get("value_digits", 0))
                value = ("%.*f" % (digits, num)) + str(entry.get("value_unit", ""))

        # Оранжевый чип - это «обрати внимание». Работающий прибор внимания
        # не требует, поэтому тонируется только то, что помечено attention
        # (по умолчанию - всё, у чего нет бейджа режима: движение, дверь,
        # протечка). Прибор различается цветом бейджа, а не заливкой.
        attention = entry.get("attention")
        if attention is None:
            attention = not (badge or entry.get("badges"))

        # Нажатие на чип открывает график канала - но только если по нему
        # вообще есть что рисовать. Числовые условия (above/below) означают
        # величину с историей, дискретные (when) - состояние вроде «дверь
        # открыта», где график выродится в две ступеньки.
        chartable = entry.get("chart")
        if chartable is None:
            chartable = (entry.get("above") is not None
                         or entry.get("below") is not None)

        chips.append({"icon": icon, "badge": badge, "value": value,
                      "chart": key if chartable else None,
                      "chart_title": entry.get("title") or "",
                      "unit": entry.get("value_unit", ""),
                      "active": bool(active and attention),
                      "badge_color": (entry.get("badge_color")
                                      or BADGE_COLORS.get(badge)),
                      "alarm": bool(entry.get("alarm")),
                      "w": chip_width(value)})
    return chips


def place_chips(chips, right_edge, y, gap=12):
    """Разложить чипы справа налево от правого края."""
    total = sum(c["w"] for c in chips) + gap * (len(chips) - 1 if chips else 0)
    x = right_edge - total
    for c in chips:
        c["x"], c["y"] = round(x, 1), y
        x += c["w"] + gap
    return chips
