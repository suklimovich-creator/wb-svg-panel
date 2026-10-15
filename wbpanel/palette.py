"""
Темы оформления как данные (с 1.24).

Раньше тема была блоком CSS в templates/tiles/_base.j2, а ещё несколько
мест знали её по имени: тени панели, отсвет тёмных тем, тёмная карточка
включённой плитки, подписи графиков, поля страницы и светлый или тёмный
пульт. Теперь тема - файл yaml, и всё перечисленное берётся из него:

    name: autumn
    title: "осень"
    extends: light          # что не задано - берётся у этой темы
    mode: light             # light | dark
    on_style: card          # rim - рамка у включённой, card - тёмная карточка
    inv_kinds: []           # типы, у которых карточка тёмная всегда
    sheen: false            # отсвет из угла плитки
    shadow: {amb: .16, key: .09}   # тени плиток; null - без теней
    vars:                   # CSS-переменные плиток, без «--»
      tile: "#FFFFFF"
      ...

Встроенные темы - wbpanel/builtin_themes/. Свои (с 1.25) - папка themes/
рядом с config.yaml: по файлу на тему, панель видит все *.yaml сама.
Файлы, имя которых начинается с «_», - заготовки, их не читаем. Своя тема
наследует встроенную (extends) и задаёт только то, что меняет; свойства
(mode, on_style, …) тоже наследуются, если не заданы.
"""

import glob
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict

import yaml

log = logging.getLogger("wb-svg-panel")

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin_themes")

#: Порядок в меню выбора темы: сначала светлые, потом тёмные, потом свои.
ORDER = ("light", "autumn", "retro", "noir", "neumo", "dark", "graphite")

#: Свойства темы, кроме цветов. Своя тема наследует незаданные.
PROPS = ("mode", "on_style", "inv_kinds", "sheen", "shadow", "chart", "depth", "edge",
         "bevel", "radius")
_PROP_DEFAULTS = {"mode": "light", "on_style": "rim", "inv_kinds": (), "sheen": False,
                  "shadow": None, "chart": None, "depth": "soft", "edge": "teeth",
                  "bevel": "round", "radius": 24}
#: Форма - вторая ось темы (с 1.32): глубина плиток и органов управления.
#: soft - мягкая тень снизу, как было; flat - без теней; neumo -
#: неоморфизм: плитка из того же материала, что и фон, светлая тень
#: сверху-слева и тёмная снизу-справа, включённая - утоплена.
DEPTHS = ("soft", "flat", "neumo")
#: Край ручек и колёс (с 1.34): зубцы, гладкий, рифлёный.
EDGES = ("teeth", "smooth", "knurl")
#: Фаска ручек и клавиш (с 1.34): скруглённая (мягкий блик, как было),
#: без фаски (плоское лицо), острая (кольцо скоса вокруг плоского лица).
BEVELS = ("round", "none", "sharp")
#: Какие свойства какими значениями бывают - для проверки файлов.
_CHOICES = {"depth": DEPTHS, "edge": EDGES, "bevel": BEVELS}

#: Значение переменной: цвет, число или ссылка на другую переменную. Всё
#: прочее не пропускаем - значения уходят прямо в <style> и в атрибуты, и
#: «;» или «}» в файле темы сломали бы панель.
_VALUE_RE = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|rgba?\(\s*[0-9.]+%?\s*,\s*[0-9.]+%?\s*,\s*[0-9.]+%?\s*(,\s*[0-9.]+%?\s*)?\)"
    r"|hsla?\(\s*[0-9.]+(deg)?\s*,\s*[0-9.]+%\s*,\s*[0-9.]+%\s*(,\s*[0-9.]+%?\s*)?\)"
    r"|var\(--[a-z0-9-]+\)"
    r"|-?[0-9]*\.?[0-9]+"
    r"|[a-zA-Z]+)$")
#: Имя темы - класс корня SVG и параметр адреса: латиница, цифры, дефис.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
     "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e",
     "yu", "ya"]))

#: Короткие имена основных цветов для своих тем.
ALIASES = {"bg": "bg-bottom", "accent": "active", "heat": "ac-seg-heat", "card": "inv-tile"}

_THEMES = {}
#: Черновики редактора (с 1.29): тема, которую ещё правят на /themes.
#: В меню и в списках их нет; живут в памяти, последние DRAFT_MAX.
_DRAFTS = OrderedDict()
DRAFT_MAX = 16
DRAFT_PREFIX = "draft-"
_PROBLEMS = []          # [(файл, текст)] - для /check
_STATE = {"dir": None, "sig": None, "checked": 0.0, "version": 0}


def slug(text):
    """«Лес у реки» -> «les-u-reki»: имя темы годится для класса и адреса."""
    out = "".join(_TRANSLIT.get(ch, ch) for ch in str(text).strip().lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    if out and not out[0].isalpha():
        out = "t-" + out
    return out


def _read(path):
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("ожидался словарь: name, title, extends, vars…")
    return data


def _theme(data, name, builtin, where):
    theme = {"name": name, "builtin": builtin, "file": where,
             "title": str(data.get("title") or name),
             "extends": data.get("extends"),
             "props": dict((k, data[k]) for k in PROPS if k in data)}
    if "inv_kinds" in theme["props"]:
        theme["props"]["inv_kinds"] = tuple(theme["props"]["inv_kinds"] or ())
    out = {}
    for key, value in (data.get("vars") or {}).items():
        key = str(key).lstrip("-")
        key = "--" + (ALIASES.get(key, key) if not builtin else key)
        value = str(value).strip()
        if not builtin and not _VALUE_RE.match(value):
            _PROBLEMS.append((where, "%s: значение %r не похоже на цвет или число - "
                                     "пропущено" % (key[2:], value)))
            continue
        out[key] = value
    theme["vars"] = out
    # Что задано в самом файле, до расчёта производных: с этого редактор
    # начинает правку своей темы (с 1.29).
    theme["raw"] = dict(out)
    return theme


def _load_builtin():
    found = {}
    for path in sorted(glob.glob(os.path.join(BUILTIN_DIR, "*.yaml"))):
        try:
            data = _read(path)
            name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
            theme = _theme(data, name, True, path)
        except Exception as exc:             # тема не должна ронять панель
            log.warning("тема %s: %s", os.path.basename(path), exc)
            continue
        if name == "light":
            theme["extends"] = None
        # Встроенная тема, у которой задано мало, может попросить
        # рассчитать остальное, как у своих (derive: true).
        theme["derive"] = bool(data.get("derive"))
        found[name] = theme
    return found


def _load_user(directory, builtin):
    found = {}
    known = set()
    for theme in builtin.values():
        known.update(theme["vars"])
    if not directory or not os.path.isdir(directory):
        return found
    for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        base = os.path.basename(path)
        if base.startswith("_"):
            continue                         # заготовка
        try:
            data = _read(path)
        except Exception as exc:
            first = str(exc).strip().split("\n")
            where = re.search(r"line (\d+)", str(exc))
            _PROBLEMS.append((base, "не читается: %s%s" % (
                first[0].rstrip(":"), (" (строка %s)" % where.group(1)) if where else "")))
            continue
        raw = str(data.get("name") or os.path.splitext(base)[0])
        name = raw if _NAME_RE.match(raw) else slug(raw)
        if not name:
            _PROBLEMS.append((base, "не вышло имя темы - задайте name латиницей"))
            continue
        if name in builtin:
            _PROBLEMS.append((base, "имя %r занято встроенной темой - задайте другое "
                                    "name" % name))
            continue
        if name in found:
            _PROBLEMS.append((base, "имя %r уже у %s" % (name, found[name]["file"])))
            continue
        theme = _theme(data, name, False, base)
        extra = sorted(k[2:] for k in theme["vars"] if k not in known)
        if extra:
            _PROBLEMS.append((base, "таких переменных нет, ни на что не влияют: %s"
                                    % ", ".join(extra)))
        if theme["props"].get("mode") not in (None, "light", "dark"):
            _PROBLEMS.append((base, "mode: %r - бывает light или dark"
                                    % theme["props"]["mode"]))
            theme["props"].pop("mode")
        chart = theme["props"].get("chart")
        if chart is not None:
            theme["props"]["chart"] = _clean_chart(chart, base)
        if theme["props"].get("on_style") not in (None, "rim", "card"):
            _PROBLEMS.append((base, "on_style: %r - бывает rim или card"
                                    % theme["props"]["on_style"]))
            theme["props"].pop("on_style")
        for key, allowed in _CHOICES.items():
            if theme["props"].get(key) not in (None,) + allowed:
                _PROBLEMS.append((base, "%s: %r - бывает %s"
                                        % (key, theme["props"][key], ", ".join(allowed))))
                theme["props"].pop(key)
        if "radius" in theme["props"]:
            try:
                ok = 0 <= float(theme["props"]["radius"]) <= 40
            except (TypeError, ValueError):
                ok = False
            if not ok:
                _PROBLEMS.append((base, "radius: %r - число от 0 до 40"
                                        % (theme["props"]["radius"],)))
                theme["props"].pop("radius")
        if not theme["extends"]:
            theme["extends"] = "dark" if theme["props"].get("mode") == "dark" else "light"
        found[name] = theme
    # Наследовать можно и от своей темы - проверяем, когда прочитаны все.
    for name, theme in found.items():
        parent = theme["extends"]
        if parent not in builtin and parent not in found:
            fallback = "dark" if theme["props"].get("mode") == "dark" else "light"
            _PROBLEMS.append((theme["file"], "extends: темы %r нет - беру %s"
                                             % (parent, fallback)))
            theme["extends"] = fallback
    return found


def _signature(directory):
    if not directory or not os.path.isdir(directory):
        return None
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append((os.path.basename(path), st.st_mtime, st.st_size))
    return tuple(out)


def load(directory=None):
    """Перечитать встроенные темы и свои из папки."""
    if directory is not None:
        _STATE["dir"] = directory
    del _PROBLEMS[:]
    builtin = _load_builtin()
    user = _load_user(_STATE["dir"], builtin)
    _THEMES.clear()
    _THEMES.update(builtin)
    _THEMES.update(user)
    for name in [n for n in ORDER if n in builtin and builtin[n].get("derive")]:
        _derive(name)
    for name in _derive_order(user):
        _derive(name)
    for name in user:
        _contrast(name)
    _STATE["sig"] = _signature(_STATE["dir"])
    _STATE["version"] += 1


def refresh(every=2.0):
    """Перечитать свои темы, если в папке что-то поменялось. Дёшево:
    не чаще раза в every секунд и только stat файлов."""
    now = time.time()
    if now - _STATE["checked"] < every:
        return
    _STATE["checked"] = now
    if _signature(_STATE["dir"]) != _STATE["sig"]:
        load()
        log.info("темы перечитаны: %s", ", ".join(names()))


def version():
    """Растёт при каждом перечитывании - в ключ кэша картинок."""
    return _STATE["version"]


def problems():
    """[(файл, текст)] - что не так со своими темами."""
    return list(_PROBLEMS)


def names():
    """Имена тем в порядке меню."""
    first = [n for n in ORDER if n in _THEMES]
    rest = [n for n in _THEMES if n not in ORDER]
    builtin_rest = sorted(n for n in rest if _THEMES[n]["builtin"])
    user = sorted((n for n in rest if not _THEMES[n]["builtin"]),
                  key=lambda n: _THEMES[n]["title"].lower())
    return first + builtin_rest + user


def exists(name):
    return name in _THEMES or name in _DRAFTS


def is_draft(name):
    return name in _DRAFTS


def get(name):
    return _THEMES.get(name) or _DRAFTS.get(name) or _THEMES.get("light") or {
        "name": "light", "title": "light", "extends": None, "props": {}, "vars": {},
        "builtin": True, "file": None}


def chain(name):
    """[light, …, name] - от корня к самой теме. Петли обрываются."""
    out, seen = [], set()
    theme = get(name)
    while theme and theme["name"] not in seen:
        seen.add(theme["name"])
        out.append(theme)
        parent = theme.get("extends")
        theme = _THEMES.get(parent) if parent else None
    return list(reversed(out))


def css_vars(name):
    """Все переменные темы с учётом наследования: {'--tile': '#FFFFFF', …}."""
    out = {}
    for theme in chain(name):
        out.update(theme["vars"])
    return out


def prop(name, key):
    """Свойство темы с наследованием: ближайшее заданное по цепочке."""
    for theme in reversed(chain(name)):
        if key in theme["props"]:
            return theme["props"][key]
    return _PROP_DEFAULTS[key]


def classes(name):
    """
    Классы корня SVG. По ним работают правила вида .graphite .tick, а
    страница узнаёт тему: светлая - «light», остальные - цепочкой без
    корня («dark graphite»).
    """
    ch = [t["name"] for t in chain(name)]
    rest = ch[1:] if len(ch) > 1 else ch
    return " ".join(rest)


def is_dark(name):
    return prop(name, "mode") == "dark"


def info(name):
    """Всё, что шаблону и коду нужно о теме, кроме цветов."""
    theme = get(name)
    shadow = prop(name, "shadow")
    return {
        "name": theme["name"],
        "title": theme["title"],
        "mode": prop(name, "mode"),
        "dark": is_dark(name),
        "card": prop(name, "on_style") == "card",
        "inv_kinds": tuple(prop(name, "inv_kinds") or ()),
        "sheen": bool(prop(name, "sheen")),
        "shadow": dict(shadow) if isinstance(shadow, dict) else None,
        "page_bg": css_vars(name).get("--bg-bottom", "#E6E6E9"),
        "depth": prop(name, "depth") if prop(name, "depth") in DEPTHS else "soft",
        "edge": prop(name, "edge") if prop(name, "edge") in EDGES else "teeth",
        "bevel": prop(name, "bevel") if prop(name, "bevel") in BEVELS else "round",
        "radius": _radius(prop(name, "radius")),
    }


def _radius(value):
    """Скругление углов плитки (с 1.35): 0-40 px, иначе прежние 24."""
    try:
        r = float(value)
    except (TypeError, ValueError):
        return 24
    if not 0 <= r <= 40:
        return 24
    return int(r) if r == int(r) else round(r, 1)


def css():
    """
    Блоки CSS-переменных всех тем: корневая - «svg {}», остальные -
    «.имя {}» только со своими значениями. Родитель всегда раньше
    потомка, иначе каскад перебил бы потомка родителем.
    """
    done, parts = set(), []

    def emit(name):
        if name in done or name not in _THEMES:
            return
        done.add(name)
        parent = _THEMES[name].get("extends")
        if parent:
            emit(parent)
        theme = _THEMES[name]
        sel = "svg" if not parent else "." + name
        lines = ["    %s: %s;" % (k, v) for k, v in theme["vars"].items()]
        if not parent:
            lines.insert(0, "    --fs: 1;")
        parts.append("  %s {\n%s\n  }" % (sel, "\n".join(lines)))

    for name in names():
        emit(name)
    return "\n".join(parts)


def menu():
    """Для страницы: [{name, title, dark, bg, own, acc…}] в порядке меню.
    Цвета пультов (с 1.27): акцент, он же светлее для выпуклых кнопок,
    текст на акценте, нагрев и холод - из темы, а не свои у страницы."""
    return [menu_entry(n) for n in names()]


def menu_entry(n):
    """Одна тема для страницы: цвета полей и пультов (см. menu)."""
    v = css_vars(n)
    tile = _parse(v.get("--tile"), v) or (217, 217, 220)
    acc = _solid(v.get("--active"), v, tile) or (224, 155, 45)
    heat = _solid(v.get("--ac-seg-heat"), v, tile) or (224, 102, 26)
    cool = _solid(v.get("--cool"), v, tile) or (59, 143, 212)
    page = info(n)["page_bg"]
    # Нагрев и холод на пульте - и дуга, и надпись: должны читаться на поле.
    field = _parse(page, v) or tile
    heat, cool = _lift(heat, field), _lift(cool, field)
    return {"name": n, "title": get(n)["title"], "dark": is_dark(n),
            "bg": page, "own": not get(n)["builtin"],
            "acc": _hex(acc), "acc2": _hex(_mix(acc, _WHITE, 0.28)),
            "accInk": _hex(_readable_on(acc, (255, 250, 242), (42, 26, 8))),
            "heat": _hex(heat), "cool": _hex(cool),
            # форма (с 1.35): пульты рисуют ручки, колёса и кнопки так же,
            # как плитки, - глубина, край, фаска и цвета двух теней
            "depth": info(n)["depth"], "edge": info(n)["edge"], "bevel": info(n)["bevel"],
            "neu": _hex(_parse(v.get("--neu-light"), v) or _WHITE),
            "shd": _hex(_parse(v.get("--shadow"), v) or _BLACK)}


#: Основные цвета темы для страницы /themes: (ключ, переменная, подпись).
SWATCHES = (("bg", "--bg-bottom", "фон"), ("tile", "--tile", "плитка"),
            ("ink", "--ink", "текст"), ("accent", "--active", "акцент"),
            ("heat", "--ac-seg-heat", "тепло"), ("cool", "--cool", "холод"),
            ("card", "--inv-tile", "карточка"))


def describe(name):
    """
    Для страницы /themes (с 1.28): откуда тема и от кого наследует, её
    основные цвета (как они получились после наследования и расчёта),
    контраст текста и что с файлом не так.
    """
    theme = get(name)
    v = css_vars(name)
    i = info(name)
    tile = _parse(v.get("--tile"), v) or (217, 217, 220)
    swatches = []
    for key, var, title in SWATCHES:
        if key == "card" and not i["card"]:
            continue
        c = _solid(v.get(var), v, tile)
        if c:
            swatches.append({"key": key, "title": title, "color": _hex(c)})
    ink = _parse(v.get("--ink"), v)
    if "problems" in theme:                    # черновик редактора
        own = list(theme["problems"])
    else:
        # У встроенных тем ошибок не бывает, а имя файла у них может
        # совпасть со своим (light.yaml в themes/) - не приписываем чужое.
        own = [text for where, text in _PROBLEMS
               if not theme["builtin"] and where == theme["file"]]
    return {"name": theme["name"], "title": theme["title"],
            "own": not theme["builtin"],
            "file": os.path.basename(theme["file"]) if theme["file"] else "",
            "extends": theme.get("extends") or "",
            "mode": i["mode"], "on_style": "card" if i["card"] else "rim",
            "depth": i["depth"], "edge": i["edge"], "bevel": i["bevel"], "radius": i["radius"],
            "swatches": swatches,
            "contrast": round(contrast(tuple(ink) + (1.0,), tuple(tile) + (1.0,)), 1) if ink else None,
            "problems": own,
            # Все основные цвета - и карточка, и тень, даже если тема их не
            # показывает: от них редактор начинает новую тему (с 1.29).
            "keys": dict((short, _hex(c)) for short, c in (
                (short, _solid(v.get("--" + full), v, tile)) for short, full in KEY_VARS) if c),
            # Своя тема как она записана в файле - редактор правит её с этого.
            "src": None if theme["builtin"] else {
                "extends": theme.get("extends") or "",
                "props": dict((k, v) for k, v in theme["props"].items()
                              if k in ("mode", "on_style", "sheen", "inv_kinds", "shadow", "chart",
                                       "depth", "edge", "bevel", "radius")),
                "vars": dict((k[2:], v) for k, v in theme.get("raw", {}).items())}}


def stray_problems():
    """Ошибки файлов, из которых тема не получилась вовсе (не читается,
    имя занято): их не к чему приложить на странице /themes."""
    files = set(t["file"] for t in _THEMES.values() if not t["builtin"])
    return [(where, text) for where, text in _PROBLEMS if where not in files]


def _solid(value, vars_, bg):
    """Цвет без прозрачности: rgba кладём на подложку."""
    c = _rgba(value, vars_)
    if not c:
        return None
    return _over(c, tuple(bg) + (1.0,))[:3]


# --------------------------------------------------- цвета графиков (1.27)
#: Величины, у которых в теме бывает свой цвет.
CHART_KINDS = ("temperature", "humidity", "co2", "voc", "pressure", "power")
#: Насколько цвета двух линий на одной плитке должны различаться (ΔE в Lab).
DISTINCT = 22.0


def _clean_chart(chart, where):
    """Проверить раздел chart своей темы: только цвета, известные имена."""
    out = {}
    if not isinstance(chart, dict):
        _PROBLEMS.append((where, "chart: ожидался словарь (palette, temperature, …)"))
        return None
    for key, value in chart.items():
        if key == "palette":
            good = [str(c).strip() for c in (value or []) if _VALUE_RE.match(str(c).strip())]
            if len(good) != len(value or []):
                _PROBLEMS.append((where, "chart.palette: не все значения - цвета"))
            out["palette"] = good
            for i, a in enumerate(good):
                for b in good[i + 1:]:
                    ca, cb = _rgba(a, {}), _rgba(b, {})
                    if ca and cb and delta_e(ca, cb) < DISTINCT:
                        _PROBLEMS.append((where, "chart.palette: %s и %s почти не различить "
                                                 "на одном графике" % (a, b)))
        elif key in CHART_KINDS:
            if _VALUE_RE.match(str(value).strip()):
                out[key] = str(value).strip()
            else:
                _PROBLEMS.append((where, "chart.%s: %r - не цвет" % (key, value)))
        else:
            _PROBLEMS.append((where, "chart.%s: такой величины нет (есть: palette, %s)"
                                     % (key, ", ".join(CHART_KINDS))))
    return out


def _lab(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(x)) for x in c[:3])
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3) if t > 0.008856 else 7.787 * t + 16.0 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b):
    """Различие цветов (ΔE76): ~2 - едва заметно, 20+ - явно разные."""
    la, lb = _lab(a), _lab(b)
    return sum((la[i] - lb[i]) ** 2 for i in range(3)) ** 0.5


def chart_colors(name):
    """{'palette': [...], 'temperature': ..., ...} - с наследованием."""
    out = {}
    for theme in chain(name):
        chart = theme["props"].get("chart") or {}
        out.update(chart)
    return out


def series_colors(name, series):
    """
    Цвета линий одной плитки. series - [(свой цвет или None, величина или
    None)]. Свой цвет из конфига сильнее темы. Остальным - цвет величины
    из темы, иначе следующий из палитры; если он почти как уже занятый на
    этой плитке, берётся следующий: две линии не должны сливаться.
    """
    chart = chart_colors(name)
    pal = list(chart.get("palette") or ["#E08A2D", "#3B93C4", "#8E8E93"])
    used = [_rgba(c, {}) for c, _k in series if c]
    used = [u for u in used if u]
    out = []
    for i, (own, kind) in enumerate(series):
        if own:
            out.append(own)
            continue
        cands = ([chart[kind]] if kind and chart.get(kind) else []) + \
            pal[i % len(pal):] + pal[:i % len(pal)]
        pick = None
        for cand in cands:
            c = _rgba(cand, {})
            if c and all(delta_e(c, u) >= DISTINCT for u in used):
                pick = cand
                break
        pick = pick or cands[0]
        out.append(pick)
        c = _rgba(pick, {})
        if c:
            used.append(c)
    return out


# ------------------------------------------------------------ контраст
def _rgba(value, vars_, depth=0):
    """Цвет -> (r, g, b, a) или None, если не разобрать."""
    value = (value or "").strip()
    m = re.match(r"^var\((--[a-z0-9-]+)\)$", value)
    if m and depth < 5:
        return _rgba(vars_.get(m.group(1)), vars_, depth + 1)
    if re.match(r"^#[0-9a-fA-F]{6}$", value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) + (1.0,)
    if re.match(r"^#[0-9a-fA-F]{3}$", value):
        return tuple(int(value[i] * 2, 16) for i in (1, 2, 3)) + (1.0,)
    m = re.match(r"^rgba?\(([^)]*)\)$", value)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        try:
            rgb = tuple(float(p) for p in parts[:3])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return rgb + (a,)
        except ValueError:
            return None
    return None


def _over(fg, bg):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def _lum(c):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(fg, bg):
    """Контраст по WCAG: 1…21."""
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


#: Что с чем проверяем: (текст, подложка, минимум, как сказать).
_PAIRS = (("--ink", "--tile", 4.5, "основной текст на плитке"),
          ("--ink-dim", "--tile", 2.3, "приглушённый текст на плитке"),
          ("--inv-ink", "--inv-tile", 4.5, "текст на тёмной карточке"))


def _contrast(name):
    vars_ = css_vars(name)
    for fg_key, bg_key, need, what in _PAIRS:
        if fg_key.startswith("--inv") and not info(name)["card"]:
            continue
        bg = _rgba(vars_.get(bg_key), vars_)
        fg = _rgba(vars_.get(fg_key), vars_)
        if not bg or not fg:
            continue
        ratio = contrast(_over(fg, bg), bg)
        if ratio < need:
            _PROBLEMS.append((get(name)["file"] or name,
                              "%s читается плохо: контраст %.1f, нужно от %.1f"
                              % (what, ratio, need)))



# ------------------------------------------------- производные цвета (1.26)
# Своя тема задаёт основные цвета - фон, плитку, текст, акцент, холод,
# тепло, тень, тёмную карточку, - а всё, что от них зависит, считается
# здесь: приглушённый текст, значки, кромка, чип, кнопки, колесо
# кондиционера. Правило срабатывает, только если сама тема задала хоть
# один из исходных цветов и не задала производный: иначе остаётся то,
# что унаследовано, или то, что задано руками. Встроенные темы не
# пересчитываются - у них всё задано.

def _parse(value, vars_):
    c = _rgba(value, vars_)
    return c[:3] if c else None


def _hex(c):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(v)))) for v in c[:3])


def _mix(a, b, f):
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


def _alpha(c, a):
    return "rgba(%d,%d,%d,%s)" % (int(round(c[0])), int(round(c[1])), int(round(c[2])),
                                  ("%.2f" % a).lstrip("0"))


_WHITE, _BLACK = (255.0, 255.0, 255.0), (0.0, 0.0, 0.0)


def _readable_on(bg, light=(244, 244, 246), dark=(26, 20, 8)):
    """Светлый или тёмный текст - что контрастнее на этой подложке."""
    bg4 = tuple(bg) + (1.0,)
    return light if contrast(tuple(light) + (1.0,), bg4) >= contrast(tuple(dark) + (1.0,), bg4) else dark


def _lift(color, bg, need=3.0):
    """Подмешивать белое или чёрное, пока цвет не станет заметен на bg."""
    target = _WHITE if _lum(tuple(bg) + (1.0,)) < 0.4 else _BLACK
    for k in range(11):
        c = _mix(color, target, k / 12.0)
        if contrast(tuple(c) + (1.0,), tuple(bg) + (1.0,)) >= need:
            return c
    return _mix(color, target, 0.85)


def _rules(dark, card):
    """(производная, исходные, расчёт по словарю v разобранных цветов)."""
    return [
        ("--bg-top", ("--bg-bottom",),
         lambda v: _hex(_mix(v["--bg-bottom"], _WHITE, 0.06 if dark else 0.3))),
        ("--ink-dim", ("--ink", "--tile"), lambda v: _hex(_mix(v["--ink"], v["--tile"], 0.5))),
        ("--ink-faint", ("--ink",), lambda v: _alpha(v["--ink"], 0.32)),
        ("--stroke", ("--ink", "--tile"), lambda v: _hex(_mix(v["--ink"], v["--tile"], 0.22))),
        ("--edge", ("--ink",), lambda v: _alpha(v["--ink"], 0.16 if dark else 0.14)),
        ("--active-soft", ("--active", "--tile"),
         lambda v: _hex(_mix(v["--active"], v["--tile"], 0.72))),
        ("--active-soft-edge", ("--active",), lambda v: _alpha(v["--active"], 0.35)),
        ("--active-ink", ("--active", "--ink"),
         lambda v: _hex(v["--active"] if dark else _mix(v["--active"], v["--ink"], 0.35))),
        ("--sheen-on", ("--active",), lambda v: _alpha(v["--active"], 0.30)),
        ("--ac-seg-cool", ("--cool",), lambda v: _hex(v["--cool"])),
        ("--ac-disk", ("--inv-tile",) if card else (("--active",) if dark else ("--ink",)),
         lambda v: _hex(v["--inv-tile"] if card else
                        (v["--active"] if dark else _mix(v["--ink"], _BLACK, 0.15)))),
        ("--ac-ink", ("--ac-disk",), lambda v: _hex(_readable_on(v["--ac-disk"]))),
        ("--ac-dim", ("--ac-ink",), lambda v: _alpha(v["--ac-ink"], 0.62)),
        ("--ac-pill", ("--tile", "--ink"),
         lambda v: _hex(_mix(v["--tile"], _WHITE, 0.08) if dark else _mix(v["--tile"], v["--ink"], 0.07))),
        ("--ac-pill-on", ("--ac-disk",), lambda v: _hex(v["--ac-disk"])),
        ("--ac-pill-ink", ("--ac-ink",), lambda v: _hex(v["--ac-ink"])),
        ("--ac-off", ("--tile", "--ink"), lambda v: _hex(_mix(v["--tile"], v["--ink"], 0.08))),
        ("--ac-off-ink", ("--ink",), lambda v: _hex(v["--ink"])),
        ("--ac-off-dim", ("--stroke",), lambda v: _hex(v["--stroke"])),
        ("--inv-ink", ("--inv-tile",),
         lambda v: _hex(_mix(_WHITE, v["--inv-tile"], 0.06))
         if _lum(tuple(v["--inv-tile"]) + (1.0,)) < 0.3 else _hex(v["--ink"])),
        ("--inv-dim", ("--inv-ink",), lambda v: _alpha(v["--inv-ink"], 0.75)),
        ("--inv-stroke", ("--inv-ink", "--active"),
         lambda v: _hex(_mix(v["--inv-ink"], v["--active"], 0.25))),
        # Светлая тень неоморфизма (depth: neumo) - фон, разбавленный белым.
        ("--neu-light", ("--bg-bottom",),
         lambda v: _hex(_mix(v["--bg-bottom"], _WHITE, 0.16 if dark else 0.72))),
        ("--inv-accent", ("--active", "--inv-tile"),
         lambda v: _hex(_lift(v["--active"], v["--inv-tile"]))),
    ]


def _derive_order(user):
    """Свои темы так, чтобы родитель считался раньше потомка."""
    out, seen = [], set()

    def visit(name):
        if name in seen or name not in user:
            return
        seen.add(name)
        visit(user[name].get("extends"))
        out.append(name)
    for name in user:
        visit(name)
    return out


def _derive(name):
    theme = get(name)
    own = set(theme["vars"])
    changed = set(own)
    merged = css_vars(name)
    dark, card = is_dark(name), prop(name, "on_style") == "card"
    derived = {}
    for target, sources, fn in _rules(dark, card):
        if target in own or not any(src in changed for src in sources):
            continue
        parsed = {}
        for key in set(sources) | {"--ink", "--tile", "--active", "--inv-tile", "--ac-disk",
                                   "--stroke", "--ac-ink", "--inv-ink", "--cool", "--bg-bottom"}:
            col = _parse(merged.get(key), merged)
            if col is not None:
                parsed[key] = col
        if any(src not in parsed for src in sources):
            continue
        try:
            value = fn(parsed)
        except Exception:                      # расчёт не должен ронять тему
            continue
        derived[target] = value
        merged[target] = value
        changed.add(target)
    theme["vars"] = dict(theme["vars"], **derived)
    theme["derived"] = sorted(k[2:] for k in derived)

# ------------------------------------------------- редактор тем (1.29)
#: Ключи основных цветов в файле: короткое имя -> переменная.
KEY_VARS = (("bg", "bg-bottom"), ("tile", "tile"), ("ink", "ink"), ("accent", "active"),
            ("heat", "ac-seg-heat"), ("cool", "cool"), ("shadow", "shadow"),
            ("card", "inv-tile"))
_KEY_NOTES = {"bg": "фон страницы и поля вокруг панели", "tile": "плитка",
              "ink": "текст", "accent": "рамка включённого, дуги, ручки",
              "heat": "тепло: нагрев", "cool": "холод: охлаждение, «не греет»",
              "shadow": "тени", "card": "тёмная карточка (on_style: card)"}
_VAR_KEY_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def _clean_draft(data):
    """Тема из редактора -> словарь как из файла. Лишнее отбрасываем:
    запрос приходит со страницы, доверять его виду нельзя."""
    if not isinstance(data, dict):
        raise ValueError("ожидался словарь темы")
    out = {}
    title = str(data.get("title") or "").strip()[:60] or "моя тема"
    out["title"] = title
    raw_name = str(data.get("name") or "").strip()
    out["name"] = raw_name if _NAME_RE.match(raw_name) else (slug(raw_name or title) or "my-theme")
    parent = str(data.get("extends") or "")
    out["extends"] = parent if parent in _THEMES else None
    for key in ("mode", "on_style", "depth", "edge", "bevel"):
        if data.get(key) is not None:
            out[key] = str(data[key])
    if data.get("radius") is not None:
        out["radius"] = _radius(data["radius"])
    for key in ("sheen", "inv_kinds", "shadow", "chart"):
        if key in data:
            out[key] = data[key]
    vars_ = data.get("vars") or {}
    if not isinstance(vars_, dict) or len(vars_) > 120:
        raise ValueError("vars: ожидался словарь до 120 переменных")
    out["vars"] = dict((str(k), str(v)) for k, v in vars_.items()
                       if _VAR_KEY_RE.match(str(k)) and len(str(v)) <= 80)
    return out


def draft(data):
    """
    Черновик редактора: тема в памяти под именем draft-<хэш>. Считается
    так же, как файл в themes/: наследование, производные цвета, проверки.
    Возвращает имя черновика; ошибки - в describe(имя)["problems"].
    """
    data = _clean_draft(data)
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    name = DRAFT_PREFIX + hashlib.sha1(blob).hexdigest()[:12]
    if name in _DRAFTS:
        _DRAFTS.move_to_end(name)
        return name, data
    mark = len(_PROBLEMS)
    where = "черновик"
    known = set()
    for theme in _THEMES.values():
        if theme["builtin"]:
            known.update(theme["vars"])
    theme = _theme(data, name, False, where)
    theme["title"] = data["title"]
    extra = sorted(k[2:] for k in theme["vars"] if k not in known)
    if extra:
        _PROBLEMS.append((where, "таких переменных нет, ни на что не влияют: %s"
                                 % ", ".join(extra)))
    if theme["props"].get("mode") not in (None, "light", "dark"):
        theme["props"].pop("mode")
    if theme["props"].get("on_style") not in (None, "rim", "card"):
        theme["props"].pop("on_style")
    for key, allowed in _CHOICES.items():
        if theme["props"].get(key) not in (None,) + allowed:
            theme["props"].pop(key)
    if theme["props"].get("chart") is not None:
        theme["props"]["chart"] = _clean_chart(theme["props"]["chart"], where)
    if not theme["extends"]:
        theme["extends"] = "dark" if theme["props"].get("mode") == "dark" else "light"
    if data["name"] in _THEMES and _THEMES[data["name"]]["builtin"]:
        _PROBLEMS.append((where, "имя %r занято встроенной темой - задайте другое"
                                 % data["name"]))
    _DRAFTS[name] = theme
    _derive(name)
    _contrast(name)
    theme["problems"] = [text for _w, text in _PROBLEMS[mark:]]
    del _PROBLEMS[mark:]
    while len(_DRAFTS) > DRAFT_MAX:
        _DRAFTS.popitem(last=False)
    return name, data


def _q(value):
    """Строка в кавычках, годная и для YAML: «"», «\\» и переводы строк
    экранируются так же, как в JSON."""
    return json.dumps(str(value), ensure_ascii=False)


def to_yaml(data, stamp=""):
    """Файл темы для папки themes/: основные цвета короткими именами и с
    пояснениями, остальное как есть. data - как из _clean_draft."""
    data = _clean_draft(data)
    lines = ["# Тема «%s» для wb-svg-panel%s." % (data["title"].replace("\n", " "),
                                                   (" - собрана в редакторе /themes " + stamp)
                                                   if stamp else ""),
             "# Положите файл в папку themes/ рядом с config.yaml: панель увидит",
             "# его сама, через пару секунд. Что не задано - как у темы из extends,",
             "# производные цвета (приглушённый текст, кнопки, колесо",
             "# кондиционера) панель рассчитает из основных.",
             "",
             "name: %s" % data["name"],
             "title: %s" % _q(data["title"])]
    if data.get("extends"):
        lines.append("extends: %s" % data["extends"])
    if data.get("mode") in ("light", "dark"):
        lines.append("mode: %s           # light | dark" % data["mode"])
    if data.get("on_style") in ("rim", "card"):
        lines.append("on_style: %s        # rim - рамка у включённой, card - тёмная карточка"
                     % data["on_style"].ljust(4))
    if data.get("depth") in DEPTHS:
        lines.append("depth: %s           # soft - мягкая тень, flat - плоско, neumo - неоморфизм"
                     % data["depth"].ljust(5))
    if data.get("edge") in EDGES:
        lines.append("edge: %s           # край ручек: teeth - зубцы, smooth - гладкий, knurl - насечка"
                     % data["edge"].ljust(6))
    if data.get("bevel") in BEVELS:
        lines.append("bevel: %s           # фаска: round - мягкая, none - плоско, sharp - острая"
                     % data["bevel"].ljust(5))
    if data.get("radius") is not None:
        lines.append("radius: %s              # скругление углов плитки, px (0-40)"
                     % str(data["radius"]).ljust(3))
    for key in ("sheen", "inv_kinds", "shadow"):
        if key in data:
            lines.append("%s: %s" % (key, json.dumps(data[key], ensure_ascii=False)))
    if data.get("chart"):
        lines.append("chart:")
        for k, v in data["chart"].items():
            lines.append("  %s: %s" % (k, json.dumps(v, ensure_ascii=False)))
    vars_ = dict(data["vars"])
    lines += ["", "vars:"]
    keyed = False
    for short, full in KEY_VARS:
        for name in (short, full):
            if name in vars_:
                value = vars_.pop(name)
                if not keyed:
                    lines.append("  # основные цвета")
                    keyed = True
                lines.append("  %s %s# %s" % ((short + ":").ljust(8), _q(value).ljust(12),
                                              _KEY_NOTES[short]))
                break
    if vars_:
        lines.append("  # остальное - как задано")
        for k in sorted(vars_):
            lines.append("  %s: %s" % (k, _q(vars_[k])))
    if len(lines) and lines[-1] == "vars:":
        lines[-1] = "vars: {}"
    return "\n".join(lines) + "\n"


load()
