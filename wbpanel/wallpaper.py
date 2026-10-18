# -*- coding: utf-8 -*-
"""
Обои стеклянных тем своей картинкой (с 1.50).

    wallpaper: data/wallpaper.jpg      # в config.yaml или у панели

Стекло без размытия фона (старые браузеры не умеют backdrop-filter) с
резкой фотографией за полупрозрачными плитками выглядит грязно. Поэтому
картинка размывается один раз здесь, на сервере: уменьшается до 720
точек и размывается по Гауссу - браузер растягивает её на экран, и
мягкость только прибавляется. Готовая картинка держится в памяти,
отдаётся по /wall/<ключ>.jpg; ключ меняется вместе с файлом (время и
размер), поэтому браузер хранит её у себя без срока.

Нужен Pillow (python3-pil). Нет его или файла - стекло с цветными пятнами,
как раньше, а /check говорит, чего не хватает.
"""

import hashlib
import io
import logging
import os
import threading

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageFilter, ImageOps
except ImportError:                            # pragma: no cover - на контроллере может не быть
    Image = None

#: Длинная сторона размытой картинки, точки.
SIDE = 720
#: Радиус размытия - доля длинной стороны.
BLUR = 1 / 36.0
#: Версия обработки: поменялась - ключи у всех картинок новые.
RECIPE = "1"

_lock = threading.Lock()
_paths = {}        # ключ -> путь к файлу
_ready = {}        # ключ -> готовый JPEG
_errors = {}       # путь -> текст ошибки


def available():
    return Image is not None


def resolve(path, base=None):
    """Путь из конфига: абсолютный или от каталога проекта (data/… рядом
    с config.yaml). none / false у панели - без своей картинки."""
    if not path or str(path).lower() in ("none", "false", "no"):
        return None
    path = os.path.expanduser(str(path))
    if not os.path.isabs(path):
        path = os.path.join(base or os.getcwd(), path)
    return path


def key_for(path):
    """Ключ картинки или None (нет Pillow, нет файла)."""
    if not path or Image is None:
        return None
    try:
        st = os.stat(path)
    except OSError as exc:
        _errors[path] = "нет файла (%s)" % exc.strerror
        return None
    _errors.pop(path, None)
    raw = "%s|%d|%d|%s" % (path, int(st.st_mtime), st.st_size, RECIPE)
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    with _lock:
        _paths[key] = path
    return key


def url_for(path):
    """Адрес размытой картинки относительно страницы или None."""
    key = key_for(path)
    return "wall/%s.jpg" % key if key else None


def blur(path):
    """Файл -> JPEG: повернуть по EXIF, уменьшить, размыть."""
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:                           # старый Pillow или битый EXIF
        pass
    img = img.convert("RGB")
    img.thumbnail((SIDE, SIDE), Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(max(img.size) * BLUR))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return out.getvalue()


def image(key):
    """Готовый JPEG по ключу или None."""
    with _lock:
        if key in _ready:
            return _ready[key]
        path = _paths.get(key)
    if not path or Image is None:
        return None
    try:
        data = blur(path)
    except Exception as exc:                    # не картинка, не читается
        _errors[path] = "не открывается как картинка (%s)" % exc
        log.warning("обои %s: %s", path, exc)
        return None
    with _lock:
        # Держим немного: обычно картинка одна, вторая - у панели.
        if len(_ready) >= 4:
            _ready.clear()
        _ready[key] = data
    return data


def problems(paths):
    """Для /check: [(путь, текст)]."""
    out = []
    for path in paths:
        if not path:
            continue
        if Image is None:
            out.append((path, "нет python3-pil: обои не размыть, стекло - с цветными пятнами"))
            continue
        if not os.path.exists(path):
            out.append((path, "нет файла"))
        elif path in _errors:
            out.append((path, _errors[path]))
    return out
