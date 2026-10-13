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
    VOC (индекс)      до 200 норма, 200-300 хуже обычного, выше - плохо

Про VOC. У WB-MSW v4 это не концентрация, а индекс Sensirion: датчик сам
считает «100» обычным для этой комнаты уровнем, ниже - чище обычного, выше
- хуже. Индекс относительный, сравнивать комнаты по нему нельзя, а
«плохо» значит «заметно хуже, чем обычно здесь»: готовка, краска, мусор.
"""

from .state import to_float

GOOD = "#4E9A6B"
WARN = "#D9A227"
BAD = "#C4553B"

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
        "zones": [(None, GOOD, "норма"), (200, WARN, "хуже обычного"),
                  (300, BAD, "плохо")],
        "about": "Индекс летучих веществ: 100 - обычный уровень для этой "
                 "комнаты, выше - готовка, краска, мусор. Относительный: "
                 "комнаты по нему не сравнить.",
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
            "icon": entry.get("icon") or kind.get("icon") or "co2",
            "title": entry.get("title") or kind.get("title") or channel}


def chip_state(entry, state):
    """(показывать?, зона, значение, слово, цвет, тревога) для чипа воздуха."""
    spec = chip_spec(entry)
    value = to_float(state.snapshot(spec["channel"])["raw"])
    n = level(spec["zones"], value)
    if n is None or n == 0:
        return None
    above, color, word = spec["zones"][n]
    return {"value": value, "zone": n, "word": word, "color": color,
            "alarm": n >= len(spec["zones"]) - 1 and n >= 2,
            "title": spec["title"], "channel": spec["channel"],
            "icon": spec["icon"]}
