# -*- coding: utf-8 -*-
"""
Секреты и куски конфига (1.61) - как в Home Assistant.

    # config.yaml
    cameras:
      hall:
        url: !secret hall_camera_url
    calendars: !include calendars.yaml

    # secrets.yaml - рядом с config.yaml, в .gitignore (secrets.*)
    hall_camera_url: rtsp://admin:пароль@192.168.1.64/Streaming/Channels/101

`!secret имя` - значение из secrets.yaml, который лежит рядом с файлом, где
написан тег (иначе - рядом с config.yaml). `!include файл.yaml` - кусок
конфига из отдельного файла (путь относительно того, где написан тег);
внутри него тоже можно `!secret` и `!include`.

Часть значения (1.62) - `${имя}` внутри строки, имя из secrets.yaml:

    topic: /sprut/${sprut_id}/accessories/341/13/state

Так config.yaml можно отдавать кому угодно (и присылать на правку):
пароли и закрытые ссылки остаются в secrets.yaml.

Теги регистрируются на yaml.SafeLoader при импорте модуля - их понимает
любой yaml.safe_load в процессе. Модуль ни от чего в пакете не зависит:
его можно брать и из утилит в tools/.
"""

import os
import re
import logging

import yaml

log = logging.getLogger("wb-svg-panel")

SECRETS_NAME = "secrets.yaml"
#: Файлы, прочитанные при последней загрузке (secrets.yaml и куски
#: !include) - Config следит за их временем изменения, как за самим конфигом.
touched = set()
#: Где искать secrets.yaml, если тег написан не в файле (строка в памяти).
default_dir = None
#: False - !secret отдаёт пустую строку (демо-сайт, выгрузки на сторону).
use_secrets = True
#: Запасная папка для !include (демо-сайт читает копию конфига из temp).
extra_dir = None

_cache = {}


def _dir_of(loader):
    name = getattr(loader, "name", "") or ""
    if name and not name.startswith("<") and os.path.exists(name):
        return os.path.dirname(os.path.abspath(name))
    return default_dir or os.getcwd()


def _secrets(folder):
    path = os.path.join(folder, SECRETS_NAME)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return path, None
    touched.add(path)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return path, hit[1]
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        log.error("%s: ожидался словарь «имя: значение»", path)
        data = {}
    _cache[path] = (mtime, data)
    return path, data


def _secret(loader, node):
    key = loader.construct_scalar(node)
    if not use_secrets:
        return ""
    folder = _dir_of(loader)
    path, data = _secrets(folder)
    if data is None and default_dir and folder != default_dir:
        path, data = _secrets(default_dir)
    if data is None:
        log.error("!secret %s: нет файла %s", key, path)
        return ""
    if key not in data:
        log.error("!secret %s: нет такого имени в %s", key, path)
        return ""
    return data[key]


def _include(loader, node):
    rel = loader.construct_scalar(node)
    path = rel if os.path.isabs(rel) else os.path.join(_dir_of(loader), rel)
    if not os.path.exists(path) and extra_dir and not os.path.isabs(rel):
        path = os.path.join(extra_dir, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            touched.add(os.path.abspath(path))
            return yaml.safe_load(fh)
    except OSError as exc:
        log.error("!include %s: %s", rel, exc)
        return None


yaml.SafeLoader.add_constructor("!secret", _secret)
yaml.SafeLoader.add_constructor("!include", _include)


_SUB = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


def expand(data, folder=None):
    """`${имя}` в строках - значение из secrets.yaml (1.62). Неизвестное
    имя остаётся как есть (и ошибка в журнале): `${` в обычном тексте
    ничего не ломает. Без секретов (демо) - тоже как есть."""
    if not use_secrets:
        return data
    _p, secrets = _secrets(folder or default_dir or os.getcwd())
    secrets = secrets or {}
    missing = set()

    def one(m):
        key = m.group(1)
        if key in secrets:
            return str(secrets[key])
        missing.add(key)
        return m.group(0)

    def walk(v):
        if isinstance(v, str):
            return _SUB.sub(one, v) if "${" in v else v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    out = walk(data)
    for key in sorted(missing):
        log.error("${%s}: нет такого имени в %s", key, SECRETS_NAME)
    return out


def load(path):
    """Прочитать YAML-файл с тегами и ${имя}; вернуть данные. Список
    прочитанных рядом файлов - в touched."""
    global default_dir
    touched.clear()
    if default_dir is None:
        default_dir = os.path.dirname(os.path.abspath(path))
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return expand(data, os.path.dirname(os.path.abspath(path)))


def stamp(path):
    """Время изменения конфига вместе с secrets.yaml и кусками !include:
    правка любого из них перечитывает конфиг."""
    files = {os.path.abspath(path), os.path.join(os.path.dirname(os.path.abspath(path)), SECRETS_NAME)}
    files.update(touched)
    out = 0
    for f in files:
        try:
            out = max(out, os.path.getmtime(f))
        except OSError:
            pass
    return out
