"""
Темы оформления как данные (с 1.24).

Раньше тема была блоком CSS в templates/tiles/_base.j2, а ещё несколько
мест знали её по имени: тени панели, отсвет тёмных тем, тёмная карточка
включённой плитки, подписи графиков, поля страницы и светлый или тёмный
пульт. Новая тема требовала правки в шести местах. Теперь тема - файл
wbpanel/builtin_themes/<имя>.yaml, и всё перечисленное берётся из него:

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

Вид панели от этого не меняется: CSS собирается из тех же значений в том
же порядке, что был в шаблоне.
"""

import glob
import logging
import os

import yaml

log = logging.getLogger("wb-svg-panel")

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin_themes")

#: Порядок в меню выбора темы: сначала светлые, потом тёмные.
ORDER = ("light", "autumn", "retro", "noir", "dark", "graphite")

_THEMES = {}

#: Чего ждём от файла темы и что подставляем, если поля нет.
_DEFAULTS = {"title": None, "extends": "light", "mode": "light", "on_style": "rim",
             "inv_kinds": (), "sheen": False, "shadow": None, "vars": {}}


def _load_file(path):
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("ожидался словарь")
    name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
    theme = dict(_DEFAULTS)
    theme.update(data)
    theme["name"] = name
    theme["title"] = theme.get("title") or name
    theme["vars"] = dict(("--" + str(k).lstrip("-"), str(v).strip())
                         for k, v in (data.get("vars") or {}).items())
    theme["inv_kinds"] = tuple(theme.get("inv_kinds") or ())
    if name == "light":
        theme["extends"] = None
    return theme


def load():
    """Перечитать встроенные темы. Битый файл пропускается с записью в журнал."""
    found = {}
    for path in sorted(glob.glob(os.path.join(BUILTIN_DIR, "*.yaml"))):
        try:
            theme = _load_file(path)
        except Exception as exc:             # тема не должна ронять панель
            log.warning("тема %s: %s", os.path.basename(path), exc)
            continue
        found[theme["name"]] = theme
    _THEMES.clear()
    _THEMES.update(found)


def names():
    """Имена тем в порядке меню."""
    first = [n for n in ORDER if n in _THEMES]
    return first + sorted(n for n in _THEMES if n not in ORDER)


def exists(name):
    return name in _THEMES


def get(name):
    return _THEMES.get(name) or _THEMES.get("light") or dict(_DEFAULTS, name="light", vars={})


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
    return get(name).get("mode") == "dark"


def info(name):
    """Всё, что шаблону и коду нужно о теме, кроме цветов."""
    theme = get(name)
    shadow = theme.get("shadow")
    return {
        "name": theme["name"],
        "title": theme["title"],
        "mode": theme.get("mode", "light"),
        "dark": is_dark(name),
        "card": theme.get("on_style") == "card",
        "inv_kinds": theme.get("inv_kinds", ()),
        "sheen": bool(theme.get("sheen")),
        "shadow": dict(shadow) if isinstance(shadow, dict) else None,
        "page_bg": css_vars(name).get("--bg-bottom", "#E6E6E9"),
    }


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
        parent = _THEMES[name].get("extends")
        if parent:
            emit(parent)
        done.add(name)
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
    """Для страницы: [{name, title, dark, bg}] в порядке меню."""
    return [{"name": n, "title": get(n)["title"], "dark": is_dark(n),
             "bg": info(n)["page_bg"]} for n in names()]


load()
