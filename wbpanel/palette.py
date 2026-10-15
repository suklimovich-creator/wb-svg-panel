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
import logging
import os
import re
import time

import yaml

log = logging.getLogger("wb-svg-panel")

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin_themes")

#: Порядок в меню выбора темы: сначала светлые, потом тёмные, потом свои.
ORDER = ("light", "autumn", "retro", "noir", "dark", "graphite")

#: Свойства темы, кроме цветов. Своя тема наследует незаданные.
PROPS = ("mode", "on_style", "inv_kinds", "sheen", "shadow")
_PROP_DEFAULTS = {"mode": "light", "on_style": "rim", "inv_kinds": (), "sheen": False,
                  "shadow": None}

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

_THEMES = {}
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
        key = "--" + str(key).lstrip("-")
        value = str(value).strip()
        if not builtin and not _VALUE_RE.match(value):
            _PROBLEMS.append((where, "%s: значение %r не похоже на цвет или число - "
                                     "пропущено" % (key[2:], value)))
            continue
        out[key] = value
    theme["vars"] = out
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
        if theme["props"].get("on_style") not in (None, "rim", "card"):
            _PROBLEMS.append((base, "on_style: %r - бывает rim или card"
                                    % theme["props"]["on_style"]))
            theme["props"].pop("on_style")
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
    return name in _THEMES


def get(name):
    return _THEMES.get(name) or _THEMES.get("light") or {
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
    """Для страницы: [{name, title, dark, bg, own}] в порядке меню."""
    return [{"name": n, "title": get(n)["title"], "dark": is_dark(n),
             "bg": info(n)["page_bg"], "own": not get(n)["builtin"]} for n in names()]


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


load()
