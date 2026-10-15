# -*- coding: utf-8 -*-
"""
Качество воздуха: что значит число на графике.

«CO2 850» или «VOC 140» сами по себе ничего не говорят. Здесь - зоны:
до какого значения норма, с какого душно, с какого пора проветрить, - и
слово для каждой. Их видно на графике полосами за линией и словом рядом
с числом, а чип строки состояния появляется, когда воздух вышел из нормы.

Зоны берутся из порогов ряда графика (thresholds), а для известных
каналов есть значения по умолчанию - писать их в каждом графике незачем:

    CO2, ppm          до 1000 норма, 1000-1500 душно, выше - проветрить
    VOC, ppb          до 220 норма, 220-660 приемлемо, 660-2200 плохо,
                      выше - очень плохо

Про VOC (с 1.30). У WB-MSW v4 это концентрация летучих веществ в ppb (по
этанолу), 0-60000, а не индекс, как считалось в 1.14: до августа 2023 в
датчике стоял Sensirion SGPC3, после - ScioSense ENS160. Зоны - по шкале
качества воздуха ENS160 (UBA): обычные для квартиры 200-400 - норма или
«приемлемо», всплески от готовки и краски доходят до тысяч. Чип строки
состояния у VOC появляется с «плохо»: «приемлемо» - обычное дело, не
повод мозолить глаза.
"""

from .state import to_float

GOOD = "#4E9A6B"
FAIR = "#A9A23A"
WARN = "#D9A227"
BAD = "#C4553B"
WORST = "#8E2F3C"

#: Известные величины: как узнать по имени канала, единица, зоны и
#: пояснение для полноэкранного графика. Зона - (с какого значения,
#: цвет, слово); первая - с минус бесконечности.
KINDS = {
    "co2": {
        "match": ("co2",),
        "title": "CO2",
        "icon": "co2",
        "zones": [(None, GOOD, "норма"), (1000, WARN, "душно"),
                  (1500, BAD, "проветрить")],
        "about": "Углекислый газ, ppm; на улице около 420. До 1000 - норма, "
                 "1000-1500 - душно, клонит в сон, выше - пора проветрить.",
    },
    "voc": {
        "match": ("voc", "air quality"),
        "title": "VOC",
        "icon": "voc",
        "zones": [(None, GOOD, "норма"), (220, FAIR, "приемлемо"),
                  (660, BAD, "плохо"), (2200, WORST, "очень плохо")],
        # «приемлемо» - обычное дело в квартире: чип - только с «плохо»
        "chip_from": 2,
        "about": "Летучие вещества, ppb: готовка, краска, бытовая химия, мусор. "
                 "До 220 - норма, 220-660 - приемлемо, 660-2200 - плохо, "
                 "выше - очень плохо, проветрить.",
    },
}

#: Слова для порогов из конфига без своих подписей - по порядку.
DEFAULT_WORDS = ("норма", "хуже нормы", "плохо", "очень плохо")


def kind_of(channel):
    """Какая это величина, по имени канала. None - не знаем."""
    name = str(channel or "").split("/")[-1].lower()
    for key, k in KINDS.items():
        if any(m in name for m in k["match"]):
            return key
    return None


def zones(spec, base_color=None):
    """
    Зоны ряда графика: [(с_какого, цвет, слово), ...] или [].

    Пороги из конфига сильнее значений по умолчанию; слова к ним - свои
    (label у порога), иначе из известной величины по порядку, иначе общие.
    zones: false у ряда - без зон вовсе.
    """
    if spec.get("zones") is False:
        return []
    kind = KINDS.get(kind_of(spec.get("channel")) or "")
    rules = spec.get("thresholds") or []
    if not rules:
        return list(kind["zones"]) if kind else []
    known = kind["zones"] if kind else []
    words = [z[2] for z in known] if known else list(DEFAULT_WORDS)
    out = [(None, base_color or (known[0][1] if known else GOOD),
            words[0] if words else "норма")]
    for i, rule in enumerate(rules):
        try:
            above = float(rule["above"])
        except (KeyError, TypeError, ValueError):
            continue
        word = rule.get("label") or (words[i + 1] if i + 1 < len(words)
                                     else DEFAULT_WORDS[min(i + 1, 3)])
        out.append((above, rule.get("color") or WARN, word))
    return out


def level(zs, value):
    """Номер зоны для значения: 0 - первая (норма). None - нет данных."""
    if value is None or not zs:
        return None
    n = 0
    for i, (above, _c, _w) in enumerate(zs):
        if above is None or value >= above:
            n = i
    return n


def about(spec):
    kind = KINDS.get(kind_of(spec.get("channel")) or "")
    return spec.get("about") or (kind["about"] if kind else "")


def chip_spec(entry):
    """
    Описание чипа воздуха из строки status:.

        status:
          - air: "wb-msw-v4_149/CO2"
          - air: "wb-msw-v4_149/Air Quality (VOC)"
            title: "VOC"

    Пороги - те же, что у графика: thresholds у записи, иначе по
    умолчанию для известного канала.
    """
    channel = str(entry.get("air") or "")
    spec = {"channel": channel, "thresholds": entry.get("thresholds")}
    zs = zones(spec)
    kind = KINDS.get(kind_of(channel) or "") or {}
    return {"channel": channel, "zones": zs,
            # С какой зоны чип виден. Свои пороги в записи - со второй, как
            # раньше: человек сам решил, что считать плохим.
            "chip_from": 1 if entry.get("thresholds") else kind.get("chip_from", 1),
            "icon": entry.get("icon") or kind.get("icon") or "co2",
            "title": entry.get("title") or kind.get("title") or channel}


def chip_state(entry, state):
    """(показывать?, зона, значение, слово, цвет, тревога) для чипа воздуха."""
    spec = chip_spec(entry)
    value = to_float(state.snapshot(spec["channel"])["raw"])
    n = level(spec["zones"], value)
    if n is None or n < spec["chip_from"]:
        return None
    above, color, word = spec["zones"][n]
    return {"value": value, "zone": n, "word": word, "color": color,
            "alarm": n >= len(spec["zones"]) - 1 and n >= 2,
            "title": spec["title"], "channel": spec["channel"],
            "icon": spec["icon"]}
