# -*- coding: utf-8 -*-
"""
Сборка панели: разделы, геометрия плиток, команды для нажатий.
"""

import html
import time
from .commands import cmd_attrs, xml_escape
from .commands import build as build_command
from .const import BAND, CELL, GAP, HEADER, PAD, RX, SECTION, TILE, font_scale, log
from .const import INV_SKIP, STALE_AFTER
from . import form, palette
from .geometry import _fmt, cells, clock_width, layout, text_width
from .status import build_status, place_chips
from .registry import resolve_type
from .sky import facing_of, location_of
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


#: Плитка в половину высоты: одна ячейка. Тот же порог, что LOW в
#: шаблоне tiles/_base.j2.
LOW_H = 117

#: Типы, у которых на половинной плитке значение («45 %») уходит из угла
#: во вторую строку подписи.
LOW_VALUE_KINDS = ("switch", "dimmer", "light", "curtain", "roller", "ac")

#: Средняя ширина буквы в долях кегля для названий. Кириллица шире цифр,
#: но 0.6 обрезало «Гардеробную», которая на деле помещается.
LETTER = 0.57


def _fit_low(tile, pw, fs):
    """
    Подпись плитки в половину высоты: название справа от значка одной
    строкой, значение - строкой ниже.

    Раньше значение стояло в правом верхнем углу, как на высокой плитке, а
    название шло с отступа под значок. На узкой плитке (три в ряд на
    телефоне) «Подсветка стена» и «3 %» ложились друг на друга, а
    длинное название вылезало за край. Теперь значение под названием, а
    название обрезается по ширине с многоточием.
    """
    toggle = tile["kind"] == "switch" and tile.get("icon") == "toggle"
    # Тумблер шире значка: 34 против 27, и текст от него отступает дальше.
    # Клавиша выключателя и колесо диммера (1.17) шире значка - их
    # плитка сама говорит, откуда начинать текст.
    x = tile.get("low_x") or (64 if toggle else 56)
    tile["low_x"] = x
    size = 15.0 * float(fs or 1.0)
    title = " ".join(tile.get("title_lines") or [tile.get("title") or ""]).strip()
    # Вторая строка короче: в правом нижнем углу живёт точка состояния.
    tile["title_low"] = _wrap2(title, pw - x - 10, pw - x - 28, size)
    if tile["kind"] in LOW_VALUE_KINDS:
        # У ленты в углу - «3 % · 2700 K», на половинной плитке хватит
        # яркости: положение точки и так показывает цвет.
        tile["low_value"] = tile.get("short_min") or tile.get("short") or ""
        if tile["kind"] == "ac":
            # «26.5° → 24°» уже всё говорит; состояние за ним («вент. 2»)
            # вылезало за край, поэтому строка одна и обрезана по ширине.
            tile["low_value"] = _fit(tile["low_value"], pw - x - 28, 13.5 * float(fs or 1.0))


def _wrap2(text, width, width2, size):
    """
    Название в одну строку, а не влезло - в две, по словам. Вторая строка
    обрезается с многоточием. Половинная плитка вмещает три строки: две
    названия и одну значения - «Подсветка / стена / 3 %».
    """
    if text_width(text, size, LETTER) <= width:
        return [text]
    words = text.split()
    first = ""
    while words:
        probe = (first + " " + words[0]).strip()
        if first and text_width(probe, size, LETTER) > width:
            break
        first = probe
        words.pop(0)
    if not words:
        # одно длинное слово: переносить нечего
        return [_fit(text, width, size)]
    return [_fit(first, width, size), _fit(" ".join(words), width2, size)]


def _fit(text, width, size):
    """Обрезать строку по ширине с многоточием."""
    if text_width(text, size, LETTER) <= width:
        return text
    out = text
    while out and text_width(out + "…", size, LETTER) > width:
        out = out[:-1]
    return out.rstrip() + "…"


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
    # Вид органов управления (с 1.17): новый по умолчанию, look: classic -
    # прежний. Задаётся у плитки, у панели или для всего config.yaml.
    look = conf.get("look") or (panel_conf or {}).get("look") or (
        config.get("look") if config else None)
    if look:
        conf["look"] = look
    # Шторы (с 1.20) рисуют вид за окном: где дом, куда смотрит окно и
    # откуда брать погоду. Тоже по цепочке плитка -> панель -> корень;
    # координаты ещё и из theme_auto, чтобы не задавать их дважды.
    if tile["kind"] in ("curtain", "roller"):
        glob = config or {}
        pc = panel_conf or {}
        conf["sky_location"] = location_of(
            conf.get("location"), pc.get("location"), glob.get("location"),
            (glob.get("theme_auto") or {}).get("location"))
        conf["facing"] = facing_of(conf.get("facing", pc.get("facing", glob.get("facing"))))
        wx = conf.get("weather", pc.get("weather", glob.get("weather", "weather")))
        conf["weather"] = wx if isinstance(wx, str) and wx else None
    # Тема нужна графику: подписи значений красятся под цвет подложки, а
    # она разная. CSS-переменная тут не подходит - цвет у каждой серии свой.
    conf["theme"] = (panel_conf or {}).get("theme") or (
        config.get("theme", "light") if config else "light")
    style = palette.info(conf["theme"])
    form.use(style)                 # край колёс - для геометрии (form.py)
    inv_kind = conf.get("type") in style["inv_kinds"]
    # Свой цвет карточки (с 1.43): color: sage и т. п. Шестнадцатеричный
    # color у лампы - цвет света, не карточки: его не трогаем.
    tint = conf.get("color")
    tint = tint.strip().lower() if isinstance(tint, str) else None
    if tint not in palette.TINT_NAMES:
        tint = None
    if tint == "dark":
        inv_kind, tint = True, None
    if inv_kind:
        # Тёмная карточка графика: подписи считаем как для тёмной темы.
        conf["theme"] = "dark"

    # Все типы идут одним путём через реестр. Историю принимает только
    # график, остальные её просто не спрашивают.
    tile.update(from_registry(conf, state, history))
    snaps = tile.pop("snaps", None) or [tile.pop("snap", None)]
    snaps = [s for s in snaps if s]

    if not tile["compact"] and ph < LOW_H:
        _fit_low(tile, pw, conf["fs"])

    # Тёмная карточка (класс inv): у типа - всегда, у включённой плитки -
    # если тема так делает.
    tile["inv"] = tile["kind"] not in INV_SKIP and (
        inv_kind or bool(style["card"] and tile.get("on")))
    tile["tint"] = tint

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
    # Плитки, у которых молчание канала - норма (датчик протечки шлёт
    # значение, только когда намок), жёлтой точкой не отмечаются: иначе она
    # висела бы на них всегда. Пропажу прибора по-прежнему видно - серой.
    quiet = tile.pop("_quiet", False)
    tile["stale"] = reads and not quiet and bool(known) and all(
        now - s["ts"] > stale_after for s in known)
    return tile


#: Как показаны разделы панели: списком (каждый сворачивается сам),
#: гармошкой (раскрыт один) или вкладками (ряд вкладок, виден один).
SECTION_MODES = ("list", "accordion", "tabs")


def section_mode(panel_conf, asked=None):
    """
    Режим разделов панели.

    asked - выбор устройства (параметр mode в адресе): планшету на стене
    нужны вкладки, а телефону - гармошка, хотя панель одна. Без выбора -
    как в конфиге: tabs: true, иначе accordion: true, иначе список.
    """
    if asked in SECTION_MODES:
        return asked
    if panel_conf.get("tabs"):
        return "tabs"
    if panel_conf.get("accordion"):
        return "accordion"
    return "list"


def folded_sections(panel_conf, sections, closed, opened, mode=None):
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

    Вкладки (tabs, с 1.36): как гармошка, но «свёрнуты все» не бывает -
    без открытой вкладки под рядом пусто.

    Раздел без заголовка не сворачивается ни в каком режиме: нажать, чтобы
    раскрыть его обратно, не на что. До 1.36 гармошка прятала такой раздел,
    стоило открыть соседний. Во вкладках он виден всегда, под их рядом.
    """
    mode = mode or section_mode(panel_conf)
    keys = [key for title, _tiles, _src, key in sections if key and title]
    if not keys:
        return set()
    if mode in ("accordion", "tabs"):
        if opened == "-" and mode == "accordion":
            return set(keys)
        current = opened if opened in keys else keys[0]
        return set(k for k in keys if k != current)
    return set(k for k in keys if k in closed)


#: Значок вкладки по названию раздела, если icon: не задан. Ищется
#: начало слова: «Гостиная», «гостевая» - диван, «Спальня» - кровать.
TAB_ICONS = (
    (("дом", "весь", "все", "главн", "сводк"), "home"),
    (("гост", "зал"), "sofa"),
    (("кух", "столов"), "dish"),
    (("спал",), "bed"),
    (("ван", "сануз", "туалет"), "bath"),
    (("душ",), "shower"),
    (("дет",), "child"),
    (("прих", "корид", "холл", "тамбур"), "door"),
    (("кабин", "библ"), "book"),
    (("протеч", "вод"), "leak"),
    (("отопл", "тепл"), "heat"),
    (("кондиц", "клим"), "ac-unit"),
    (("балк", "лодж", "окн"), "window"),
    (("сцен", "свет"), "bulb"),
    (("улиц", "двор", "сад", "погод"), "sun"),
    (("камер",), "camera"),
)


#: Смещение рисунка значка от центра его квадрата 24×24 (замерено по
#: границам штрихов). Во вкладке значок стоит один в круге, и рисунок,
#: сидящий в квадрате выше или ниже середины, видно сразу: блюдо кухни
#: уезжало вверх на полторы единицы. Нет в таблице - рисунок по центру.
ICON_SHIFT = {
    "undo": (0, 2), "power": (0, -0.67), "cart": (-0.6, 0.35), "strip": (0, 1.05),
    "blind": (0, 1.15), "thermo": (0, 0.75), "drop": (0, -1.35), "co2": (-0.2, 1.1),
    "wind": (-0.75, -1), "tap": (0, 2.45), "tv": (0, 0.4), "motion": (0, -0.5),
    "person": (0, -0.5), "leak": (-0.65, 0.09), "ac-unit": (-1.6, -2.79),
    "vacuum": (-1.3, -1.07), "swing": (0, 0.75), "next": (0.75, 0),
    "cloud": (0.48, -0.18), "cloud-sun": (0.56, -0.99), "fog": (0.28, -1.88),
    "dish": (0, -1.5), "sofa": (0, 0.9), "bed": (0, 0.5), "bath": (0, 0.4),
    "shower": (-0.7, 0.25),
}


def tab_icon(section, source, title):
    """Значок вкладки: icon у раздела, у панели-источника, иначе по названию."""
    if section:
        return str(section)
    if isinstance(source, dict) and source.get("icon"):
        return str(source["icon"])
    words = str(title or "").lower().replace("-", " ").split()
    for stems, icon in TAB_ICONS:
        if any(w.startswith(st) for w in words for st in stems):
            return icon
    return "home"


#: Вкладки (1.36) - для небольших экранов на стене: полоса во всю ширину
#: впритык к нижнему или верхнему краю, в ней ряд «пилюль», как выбор
#: режима в пульте кондиционера. Название открытой комнаты - в шапке со
#: значком, поэтому на вкладках по умолчанию одни значки: зачем писать
#: «Гостиная» дважды. tab_labels: true добавляет подписи, если влезают.
TAB_GAP = 6
TAB_PAD = 6          # поля полосы
TAB_ICON = 22        # значок без подписи
TAB_ICON_S = 18      # значок над подписью
TAB_FONT = 12
TAB_H = 46           # пилюля со значком и подписью
TAB_H_ICON = 40      # только значок
TAB_MAX_W = 120      # шире пилюля не растёт: три вкладки - не три полосы


def tab_bar(sections, hidden, state, width, y, icons=None, labels=False, nav=False):
    """
    Полоса вкладок (режим tabs): -> (вкладки, полоса, открытая, высота).

    Полоса от края до края панели; пилюли одинаковой ширины по центру.
    Чипов закрытых разделов не видно, поэтому тревогу (протечка,
    «проветрить») вкладка показывает сама - красной точкой.
    """
    items = []
    for title, _tiles, src, key in sections:
        if not key or not title:
            continue
        chips = build_status(src, state)
        items.append({"title": str(title), "key": key, "active": key not in hidden,
                      "alarm": any(c.get("alarm") for c in chips), "chips": chips,
                      "icon": tab_icon((icons or {}).get(key), src, title)})
    if not items:
        return [], None, None, 0
    n = len(items)
    inner = width - 2 * (PAD - TAB_PAD) - 2 * TAB_PAD
    # Кнопки страницы в ряду (1.50, полоса сверху): «назад» слева и
    # шестерёнка справа - кружками того же размера, что вкладки без
    # подписей. Ряд вкладок ужимается на их место с обеих сторон.
    if nav:
        inner -= 2 * (TAB_H_ICON + TAB_GAP * 2)
    w = min(TAB_MAX_W, (inner - TAB_GAP * (n - 1)) / float(n))
    # Круглые кнопки (без подписей): диаметр - по высоте ряда, но не шире
    # своей доли - девять комнат на экране 320 px тоже должны встать.

    # Подпись - название, не влезло - первое слово («Протечки и отопление»
    # -> «Протечки»). Только если влезли у всех: ряд, где у одних вкладок
    # слово, а у других нет, читается хуже ряда значков.
    words = False
    if labels:
        def fits(text):
            return text_width(text, TAB_FONT) * 1.05 + 12 <= w
        for it in items:
            first = it["title"].split()[0] if it["title"].split() else ""
            it["label"] = it["title"] if fits(it["title"]) else (first if fits(first) else None)
        words = all(it["label"] for it in items)
    h = TAB_H if words else min(TAB_H_ICON, w)
    bar_h = h + 2 * TAB_PAD
    plate = {"y": y, "w": width, "h": bar_h,
             # линия - со стороны плиток: сверху у нижней полосы
             "line_y": y if y > 0 else y + bar_h - 1}
    row_w = n * w + (n - 1) * TAB_GAP
    x = (width - row_w) / 2.0
    isz = TAB_ICON_S if words else min(TAB_ICON, round(h * 0.55))
    tabs = []
    for it in items:
        cx = x + w / 2.0
        top = y + TAB_PAD
        if words:
            iy = top + 6
            ty = top + 6 + isz + TAB_FONT + 1
        else:
            iy = top + (h - isz) / 2.0
            ty = None
        # Без подписей кнопка круглая, значок - в её центре; место между
        # кругами - просвет, нажатие ловит вся доля ряда.
        round_ = not words
        bx, bw = (cx - h / 2.0, h) if round_ else (x, w)
        tabs.append({
            "round": round_, "cx": round(cx, 1), "cy": round(top + h / 2.0, 1),
            "r": h / 2.0, "hit_x": round(x - TAB_GAP / 2.0, 1), "hit_w": round(w + TAB_GAP, 1),
            "key": it["key"], "title": it["title"], "icon": it["icon"],
            "label": it.get("label") if words else None,
            "active": it["active"], "alarm": it["alarm"] and not it["active"],
            "x": round(bx, 1), "y": top, "w": round(bw, 1), "h": h,
            # рисунок значка - в центр круга, а не его квадрат
            "ix": round(cx - isz / 2.0 - ICON_SHIFT.get(it["icon"], (0, 0))[0] * isz / 24.0, 2),
            "iy": round(iy - (0 if words else ICON_SHIFT.get(it["icon"], (0, 0))[1] * isz / 24.0), 2),
            "isize": isz,
            "tx": round(cx, 1), "ty": ty,
            # точка тревоги - на краю круга, под 45° вверх-вправо
            "dot_x": round(cx + h * 0.36 if round_ else bx + bw - 9, 1),
            "dot_y": round(top + h * 0.14 if round_ else top + 9, 1),
        })
        x += w + TAB_GAP
    if nav:
        d = min(TAB_H_ICON, h)
        cy = round(y + TAB_PAD + h / 2.0, 1)
        edge = PAD - TAB_PAD + d / 2.0
        plate["nav"] = {"r": d / 2.0, "cy": cy, "k": round(d / 2.0 / 18, 3),
                        "back_x": round(edge, 1), "menu_x": round(width - edge, 1)}
    current = [it for it in items if it["active"]]
    return tabs, plate, (current[0] if current else None), bar_h


#: Кнопки страницы в шапке (с 1.44): круглая «назад» слева и шестерёнка
#: справа - диаметр и промежуток до названия и часов.
NAV_D = 36
NAV_GAP = 10


def prepare(panel_conf, state, history, cols=None, all_panels=None, name=None,
            closed=(), opened=None, mode=None, nav=False):
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

    mode = section_mode(panel_conf, mode)
    # Вкладки из одного раздела - лишний ряд над ним: показываем списком.
    if mode == "tabs" and len([k for t, _x, _s, k in sections if k and t]) < 2:
        mode = "list"
    hidden = folded_sections(panel_conf, sections, set(closed or ()), opened, mode)

    tabs, tab_plate, tab_open, tab_h = [], None, None, 0
    dock = "top"
    tab_nav = False
    head_dy = 0
    if mode == "tabs":
        # tabs: bottom - полоса у нижнего края, как в приложениях на
        # телефоне; true / top - у верхнего, над шапкой.
        dock = "bottom" if str(panel_conf.get("tabs")).lower() == "bottom" else "top"
        # icon: у раздела в конфиге - по тому же имени, что у раздела в адресе
        icons = {}
        for sec in panel_conf.get("sections") or []:
            if isinstance(sec, dict) and sec.get("icon"):
                icons[sec.get("key") or sec.get("from") or sec.get("title") or ""] = sec["icon"]
        width_full = PAD * 2 + cols * CELL + (cols - 1) * GAP
        tab_nav = bool(nav) and dock == "top"
        tabs, tab_plate, tab_open, tab_h = tab_bar(
            sections, hidden, state, width_full, 0, icons,
            bool(panel_conf.get("tab_labels")), nav=tab_nav)
        if dock == "top":
            # Полоса сверху, впритык к краю; шапка и плитки - под ней.
            head_dy = tab_h
            y_cursor += tab_h

    for title, tiles_conf, status_src, key in sections:
        folded = bool(key) and key in hidden
        # плитки-ссылки должны знать, откуда на них нажали
        if name:
            for conf in tiles_conf:
                if isinstance(conf, dict) and conf.get("type") == "link":
                    conf.setdefault("_from", name)
        if title and mode != "tabs":
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
                build_status(status_src, state), right_edge, y_cursor - 2,
                left=hd["arrow_x"] + 24)
            # До первого чипа минус просвет, иначе до края строки
            first = min([c["x"] for c in hd["chips"]] or [right_edge + 8])
            hd["tap_w"] = max(60, first - 12 - hd["tap_x"])
            # Зона нажатия больше подложки. Подложка в 36 единиц на
            # сводной панели в телефоне ужимается до полутора десятков
            # пикселей, и по ней промахиваются. Поэтому нажатие ловит вся
            # полоса раздела: от просвета над заголовком до верха первой
            # плитки и от края до края. Чипы рисуются позже и лежат
            # поверх - их нажатия она не перехватывает, а мимо чипа
            # нажатие уходит заголовку, что безобидно.
            hd["hit_x"] = hd["tap_x"]
            hd["hit_y"] = y_cursor - 12
            hd["hit_w"] = right_edge + 8 - hd["tap_x"]
            hd["hit_h"] = SECTION + 12
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

    if tabs and dock == "bottom":
        # Полоса - под плитками, впритык к нижнему краю панели. У края
        # экрана её держит страница: она прикалывает копию полосы к низу
        # окна, а эта, невидимая, оставляет под неё место в прокрутке.
        bar_y = height
        for t in tabs:
            t["y"] += bar_y
            t["cy"] = round(t["cy"] + bar_y, 1)
            t["iy"] = round(t["iy"] + bar_y, 2)
            t["dot_y"] += bar_y
            if t["ty"] is not None:
                t["ty"] += bar_y
        tab_plate["y"] += bar_y
        tab_plate["line_y"] = bar_y
        height = int(round(bar_y + tab_h))

    # Статусы самой панели - в шапке, левее часов: справа стоит время,
    # и наезжать на него нельзя.
    # Заголовок панели: базовая линия на 44, значит середина чипа на 38.
    title = str(panel_conf.get("title") or "")
    # Во вкладках шапка называет открытую комнату и рисует её значок: то же
    # слово на вкладке и над плитками было бы повтором.
    head_icon = None
    if tab_open:
        title, head_icon = tab_open["title"], tab_open["icon"]
    # Без связи справа ещё «· нет связи с MQTT» - чипы отходят левее.
    # С 1.35 часы крупнее и с датой под ними: чипы - левее их блока.
    clock_w = clock_width(state.connected)
    # Кнопки страницы в шапке (1.44): слева «назад», справа шестерёнка -
    # название и часы отходят на их ширину.
    nav_r = round(NAV_D / 2.0 * float(nav), 1) if nav else 0
    nav_w = (nav_r * 2 + NAV_GAP) if nav else 0
    # Полоса вкладок сверху держит кнопки сама (1.50): шапке их не надо.
    if tab_nav:
        nav_r = nav_w = 0
    # Во вкладках чипы открытого раздела - в шапке, рядом с чипами панели:
    # своего заголовка у раздела нет.
    top_chips = place_chips(build_status(panel_conf, state)
                            + list(tab_open["chips"] if tab_open else []),
                            width - PAD - clock_w - nav_w, 20 + head_dy,
                            left=PAD + nav_w + (34 if head_icon else 0)
                            + text_width(title, 26) * 1.1 + 14 if title else None)
    return out, width, height, headers, top_chips, {
        "mode": mode, "tabs": tabs, "tab_plate": tab_plate, "dock": dock,
        "title": title, "head_icon": head_icon, "head_dy": head_dy,
        "nav_w": nav_w, "nav_r": nav_r, "tab_nav": tab_nav}
