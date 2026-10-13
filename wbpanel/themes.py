# -*- coding: utf-8 -*-
"""
Тема по времени суток: днём светлая, ночью тёмная.

    theme: auto
    theme_auto:
      day: autumn                 # какая днём
      night: graphite             # какая ночью
      day_from: sunrise-30m       # «07:00» или от восхода/заката со сдвигом
      night_from: sunset+30m
      location: {lat: 55.75, lon: 37.62}   # нужна для sunrise/sunset

theme: auto можно поставить и у одной панели. Выбор темы кнопкой на
устройстве сильнее: «как в конфиге» возвращает к расписанию.

Восход и закат считаются здесь же, без сети и без пакетов: алгоритм из
«Almanac for Computers» (Морская обсерватория США), точность - минута-
две, для смены оформления больше не нужно. Время - местное время
контроллера.
"""

import datetime as dt
import math
import re

from .const import THEMES, log

DEFAULTS = {"day": "light", "night": "dark"}
#: Без координат или когда солнце не садится / не встаёт (полярные день и
#: ночь) - обычные часы.
FALLBACK = {"day_from": "07:00", "night_from": "21:00"}

_SPEC_RE = re.compile(r"^\s*(sunrise|sunset|восход|закат)\s*(?:([+-])\s*(\d+)\s*([mhмч]?))?\s*$", re.I)
_CLOCK_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")


def _sun_utc(day, lat, lon, rising):
    """Восход/закат в UTC, часы от полуночи этого дня. None - не бывает."""
    zenith = 90.833
    n = day.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    rad = math.radians
    l = (m + 1.916 * math.sin(rad(m)) + 0.020 * math.sin(rad(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(rad(l)))) % 360
    ra += (math.floor(l / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15.0
    sin_dec = 0.39782 * math.sin(rad(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = ((math.cos(rad(zenith)) - sin_dec * math.sin(rad(lat)))
             / (cos_dec * math.cos(rad(lat))))
    if cos_h > 1 or cos_h < -1:
        return None
    h = math.degrees(math.acos(cos_h))
    h = (360 - h if rising else h) / 15.0
    local_t = h + ra - 0.06571 * t - 6.622
    return (local_t - lng_hour) % 24


def sun_times(day, lat, lon):
    """(восход, закат) местным временем для даты day, или None."""
    out = []
    for rising in (True, False):
        ut = _sun_utc(day, lat, lon, rising)
        if ut is None:
            return None
        base = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
        moment = (base + dt.timedelta(hours=ut)).astimezone()
        # Закат по UTC может выпасть на следующие сутки - к нашей дате.
        out.append(moment)
    rise, sset = out
    if sset < rise:
        sset += dt.timedelta(days=1)
    return rise, sset


def _location(conf, config):
    loc = conf.get("location") or (config.get("location") if config else None) or {}
    try:
        return float(loc["lat"]), float(loc["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _moment(spec, day, sun):
    """Строка «07:00» / «sunset+30m» -> datetime на этот день, или None."""
    spec = str(spec or "")
    m = _CLOCK_RE.match(spec)
    if m:
        return dt.datetime(day.year, day.month, day.day,
                           int(m.group(1)) % 24, int(m.group(2))).astimezone()
    m = _SPEC_RE.match(spec)
    if not m or sun is None:
        return None
    which = m.group(1).lower()
    base = sun[0] if which in ("sunrise", "восход") else sun[1]
    if m.group(2):
        amount = int(m.group(3))
        unit = (m.group(4) or "m").lower()
        delta = dt.timedelta(hours=amount) if unit in ("h", "ч") else dt.timedelta(minutes=amount)
        base = base + delta if m.group(2) == "+" else base - delta
    return base


def problems(config):
    """Что не так с theme_auto - для /check. Список строк."""
    conf = (config.get("theme_auto") or {}) if config else {}
    out = []
    for key in ("day", "night"):
        name = conf.get(key, DEFAULTS[key])
        if name not in THEMES:
            out.append("theme_auto.%s: темы %r нет (есть: %s)" % (key, name, ", ".join(THEMES)))
    uses_sun = any(_SPEC_RE.match(str(conf.get(k) or "")) for k in ("day_from", "night_from"))
    for key in ("day_from", "night_from"):
        val = conf.get(key)
        if val and not (_CLOCK_RE.match(str(val)) or _SPEC_RE.match(str(val))):
            out.append("theme_auto.%s: %r не понять - нужно «07:00» или «sunset+30m»" % (key, val))
    if uses_sun and not _location(conf, config):
        out.append("theme_auto: для sunrise/sunset нужны координаты location: "
                   "{lat, lon}; пока по часам %s–%s" % (FALLBACK["day_from"], FALLBACK["night_from"]))
    return out


def auto_theme(config, now=None):
    """Какая тема сейчас по расписанию theme_auto."""
    conf = (config.get("theme_auto") or {}) if config else {}
    day_name = conf.get("day", DEFAULTS["day"])
    night_name = conf.get("night", DEFAULTS["night"])
    if day_name not in THEMES:
        day_name = DEFAULTS["day"]
    if night_name not in THEMES:
        night_name = DEFAULTS["night"]
    now = now or dt.datetime.now().astimezone()
    loc = _location(conf, config)
    sun = sun_times(now.date(), loc[0], loc[1]) if loc else None
    start = _moment(conf.get("day_from") or ("sunrise" if loc else FALLBACK["day_from"]),
                    now.date(), sun)
    end = _moment(conf.get("night_from") or ("sunset" if loc else FALLBACK["night_from"]),
                  now.date(), sun)
    if start is None:
        start = _moment(FALLBACK["day_from"], now.date(), None)
    if end is None:
        end = _moment(FALLBACK["night_from"], now.date(), None)
    if start <= end:
        is_day = start <= now < end
    else:
        # день «переваливает» через полночь - бывает при странных настройках
        is_day = now >= start or now < end
    return day_name if is_day else night_name


def resolve(name, config, now=None):
    """Имя темы из конфига -> настоящая тема (auto раскрывается)."""
    if name == "auto":
        try:
            return auto_theme(config, now)
        except Exception as exc:                # тема не должна ронять панель
            log.warning("theme_auto: %s", exc)
            return DEFAULTS["day"]
    return name if name in THEMES else "light"
