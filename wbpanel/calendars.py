# -*- coding: utf-8 -*-
"""
Календари: расписание на день из Google Календаря и любого другого
календаря, который отдаёт файл iCal (.ics) по ссылке.

У Google это «Настройки календаря → Интеграция календаря → Закрытый адрес
в формате iCal». Ссылка только для чтения и не истекает, OAuth не нужен.
Ссылка - доступ к расписанию, поэтому живёт в config.yaml рядом с паролем
камеры: в разметку и в журнал она не попадает.

    calendars:
      family: {title: "Семья",  ical: "https://calendar.google.com/…/basic.ics", color: "#3B8FD4"}
      work:   {title: "Работа", ical: "…", color: "#E09B2D", refresh: 15m}

Файлы забирает фоновый поток раз в refresh (по умолчанию 15 минут), а
отрисовка читает уже разобранное - HTTP-запрос панели никогда не ждёт
Google. Нет связи - показываем последнее, что успели получить.

Повторяющиеся события (RRULE) разворачивает dateutil, отмены отдельных
повторов (EXDATE) и перенесённые повторы (RECURRENCE-ID) учитываются.
Разворачиваем лениво, по дню: результат кэшируется до следующей загрузки.
"""

import datetime as dt
import re
import threading
import time
import urllib.request

from .const import log
from .geometry import parse_duration

try:                                    # Debian 11: python3-dateutil
    from dateutil.rrule import rrulestr
except ImportError:                     # pragma: no cover - на стенде есть
    rrulestr = None

try:
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - Python < 3.9
    ZoneInfo = None


DEFAULT_REFRESH = "15m"
DEFAULT_COLOR = "#3B8FD4"
#: Сколько лет назад начавшиеся повторы ещё разворачивать. Ограничение -
#: от календаря-мусорки с правилом «каждый день с 1970 года».
MAX_FILE = 8 * 1024 * 1024

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_FULL = ("понедельник", "вторник", "среда", "четверг", "пятница",
                 "суббота", "воскресенье")
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен",
          "окт", "ноя", "дек")


def local_tz(config=None):
    name = (config.get("timezone") if config else None)
    if name and ZoneInfo:
        try:
            return ZoneInfo(str(name))
        except Exception:                           # noqa: BLE001
            log.warning("timezone: неизвестный пояс %r", name)
    return dt.datetime.now().astimezone().tzinfo


def day_label(day, today):
    """«сегодня, пн 5 окт» / «завтра, вт 6 окт» / «ср 7 окт»."""
    base = "%s %d %s" % (WEEKDAYS[day.weekday()], day.day, MONTHS[day.month - 1])
    if day == today:
        return "сегодня, " + base
    if day == today + dt.timedelta(days=1):
        return "завтра, " + base
    return base


# ==========================================================================
#  Разбор iCal
# ==========================================================================

def _unfold(text):
    # Длинные строки iCal переносятся: следующая начинается с пробела.
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _prop(line):
    """'DTSTART;TZID=Europe/Moscow:20261005T100000' -> (name, params, value)."""
    # Двоеточие внутри кавычек параметра - не разделитель.
    quoted, cut = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            cut = i
            break
    if cut < 0:
        return None, {}, ""
    head, value = line[:cut], line[cut + 1:]
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def _text(value):
    return (value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\")).strip()


def _zone(name, fallback):
    if name and ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:                           # noqa: BLE001
            pass
    return fallback


def _when(value, params, tz):
    """
    Время из iCal. -> (значение, весь_день, пояс события)

    Дата без времени - событие на весь день: date. Время с Z - UTC, с
    TZID - в этом поясе, без того и другого - «плавающее», считаем местным.
    Время возвращаем осознанным (aware) datetime в поясе события: повторы
    разворачиваются по стенным часам этого пояса, иначе переход на летнее
    время сдвигал бы утренние встречи на час.
    """
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:8])), True, None
    m = re.match(r"(\d{8})T(\d{2})(\d{2})(\d{2})?(Z?)$", value)
    if not m:
        raise ValueError("время %r" % value)
    d = m.group(1)
    naive = dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                        int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))
    if m.group(5) == "Z":
        return naive.replace(tzinfo=dt.timezone.utc), False, dt.timezone.utc
    zone = _zone(params.get("TZID"), tz)
    return naive.replace(tzinfo=zone), False, zone


def _duration(value):
    m = re.match(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$",
                 value.strip())
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_ics(text, tz):
    """
    Текст .ics -> список событий-словарей (без развёртки повторов).

    Берём только VEVENT верхнего уровня; вложенные VALARM пропускаем,
    VTIMEZONE не нужен - пояса знает zoneinfo по имени.
    """
    events, cur, depth = [], None, 0
    for line in _unfold(text):
        if line.startswith("BEGIN:"):
            what = line[6:].strip().upper()
            if what == "VEVENT" and depth == 0:
                cur, depth = {"exdate": [], "rdate": []}, 1
            elif cur is not None:
                depth += 1
            continue
        if line.startswith("END:"):
            what = line[4:].strip().upper()
            if cur is not None:
                depth -= 1
                if depth == 0 and what == "VEVENT":
                    events.append(cur)
                    cur = None
            continue
        if cur is None or depth != 1:
            continue
        name, params, value = _prop(line)
        try:
            if name == "SUMMARY":
                cur["title"] = _text(value)
            elif name == "LOCATION":
                cur["location"] = _text(value)
            elif name == "UID":
                cur["uid"] = value.strip()
            elif name == "STATUS":
                cur["status"] = value.strip().upper()
            elif name == "DTSTART":
                cur["start"], cur["all_day"], cur["zone"] = _when(value, params, tz)
            elif name == "DTEND":
                cur["end"] = _when(value, params, tz)[0]
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "RRULE":
                cur["rrule"] = value.strip()
            elif name == "EXDATE":
                for v in value.split(","):
                    cur["exdate"].append(_when(v, params, tz)[0])
            elif name == "RDATE" and params.get("VALUE") != "PERIOD":
                for v in value.split(","):
                    cur["rdate"].append(_when(v, params, tz)[0])
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = _when(value, params, tz)[0]
        except (ValueError, TypeError) as exc:
            cur.setdefault("bad", str(exc))
    return [e for e in events if "start" in e and "bad" not in e]


# ==========================================================================
#  Развёртка по дням
# ==========================================================================

def _as_dt(value, tz):
    """date -> полночь местного дня; datetime -> в местный пояс."""
    if isinstance(value, dt.datetime):
        return value.astimezone(tz)
    return dt.datetime(value.year, value.month, value.day, tzinfo=tz)


def _key(value):
    """Ключ повтора для EXDATE и RECURRENCE-ID: момент времени или дата."""
    if isinstance(value, dt.datetime):
        return ("t", value.astimezone(dt.timezone.utc).replace(tzinfo=None))
    return ("d", value)


def _length(ev):
    if ev.get("end") is not None:
        if ev["all_day"] and isinstance(ev["end"], dt.date) and \
                not isinstance(ev["end"], dt.datetime):
            return ev["end"] - ev["start"]
        try:
            return _as_dt(ev["end"], dt.timezone.utc) - _as_dt(ev["start"], dt.timezone.utc)
        except TypeError:
            return dt.timedelta(0)
    if ev.get("duration") is not None:
        return ev["duration"]
    return dt.timedelta(days=1) if ev["all_day"] else dt.timedelta(0)


def _rrule_naive(rule, ev):
    """
    RRULE разворачиваем по стенным часам пояса события: dtstart без
    пояса, UNTIL переводим туда же. dateutil не любит UNTIL в UTC при
    dtstart без пояса и наоборот - так пояс исчезает из уравнения вовсе.
    """
    zone = ev.get("zone")
    start = ev["start"]
    if isinstance(start, dt.datetime):
        dstart = start.replace(tzinfo=None)
    else:
        dstart = dt.datetime(start.year, start.month, start.day)

    def fix_until(m):
        v = m.group(1)
        if v.endswith("Z") and zone is not None:
            t = dt.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            return "UNTIL=" + t.astimezone(zone).strftime("%Y%m%dT%H%M%S")
        if len(v) == 8:
            return "UNTIL=" + v + "T235959"
        return "UNTIL=" + v.rstrip("Z")
    rule = re.sub(r"UNTIL=([0-9TZ]+)", fix_until, rule)
    return rrulestr(rule, dtstart=dstart), dstart


def expand(events, start, end, tz):
    """
    Экземпляры событий, задевающие промежуток [start, end) (aware datetime).
    -> список {title, location, start, end, all_day}, где start/end - aware
    datetime в местном поясе.
    """
    overrides = {}
    for ev in events:
        if ev.get("recurrence_id") is not None and ev.get("uid"):
            overrides[(ev["uid"], _key(ev["recurrence_id"]))] = ev

    out = []

    def add(ev, s_value, length):
        if ev.get("status") == "CANCELLED":
            return
        s = _as_dt(s_value, tz)
        e = s + length if length else s
        if ev["all_day"] and not isinstance(s_value, dt.datetime):
            e = _as_dt(s_value + (length or dt.timedelta(days=1)), tz)
        if e <= start and not (e == s and start <= s < end):
            return
        if s >= end:
            return
        out.append({"title": ev.get("title") or "(без названия)",
                    "location": ev.get("location") or "",
                    "start": s, "end": e, "all_day": bool(ev["all_day"])})

    for ev in events:
        if ev.get("recurrence_id") is not None:
            # Перенесённый или изменённый повтор - отдельное событие.
            add(ev, ev["start"], _length(ev))
            continue
        length = _length(ev)
        if not ev.get("rrule") and not ev.get("rdate"):
            add(ev, ev["start"], length)
            continue
        if rrulestr is None:
            # Без dateutil показываем только первый раз - лучше, чем ничего.
            add(ev, ev["start"], length)
            continue
        excluded = set(_key(x) for x in ev["exdate"])
        try:
            rule, dstart = _rrule_naive(ev["rrule"], ev) if ev.get("rrule") else (None, None)
        except (ValueError, TypeError) as exc:
            log.warning("календарь: правило %r: %s", ev.get("rrule"), exc)
            add(ev, ev["start"], length)
            continue
        zone = ev.get("zone") or tz
        # Окно в стенных часах события, с запасом на длину события: встреча,
        # начавшаяся вчера в 23:00 и идущая до 01:00, сегодня тоже видна.
        lo = (start - length - dt.timedelta(days=1)).astimezone(zone).replace(tzinfo=None)
        hi = (end + dt.timedelta(days=1)).astimezone(zone).replace(tzinfo=None)
        moments = list(rule.between(lo, hi, inc=True)) if rule else []
        for r in ev["rdate"]:
            moments.append(r.astimezone(zone).replace(tzinfo=None)
                           if isinstance(r, dt.datetime)
                           else dt.datetime(r.year, r.month, r.day))
        for m in moments:
            if ev["all_day"]:
                inst = m.date()
            else:
                inst = m.replace(tzinfo=zone)
            k = _key(inst)
            if k in excluded or (ev.get("uid"), k) in overrides:
                continue
            add(ev, inst, length)
    out.sort(key=lambda x: (not x["all_day"], x["start"], x["title"]))
    return out


# ==========================================================================
#  Загрузка и кэш
# ==========================================================================

class Calendars(object):
    """Все календари демона. Один экземпляр на процесс."""

    def __init__(self):
        self.config = None
        self.state = None
        self.lock = threading.Lock()
        # имя -> {"events": [...], "ok": ts, "error": str, "seq": n, "next": ts}
        self.data = {}
        self._days = {}

    def configure(self, config, state=None):
        self.config = config
        self.state = state

    def defs(self):
        raw = (self.config.get("calendars") if self.config else None) or {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in raw.items()}

    def known(self, name):
        return name in self.defs()

    def tz(self):
        return local_tz(self.config)

    def pick(self, spec):
        """Имена календарей плитки: all / имя / список."""
        names = list(self.defs())
        if spec in (None, "", "all"):
            return names
        items = spec if isinstance(spec, list) else [spec]
        return [str(n) for n in items if str(n) in self.defs()]

    def color(self, name):
        return str(self.defs().get(name, {}).get("color") or DEFAULT_COLOR)

    def title(self, name):
        return str(self.defs().get(name, {}).get("title") or name)

    def status(self, name):
        with self.lock:
            d = self.data.get(name) or {}
            return {"ok": d.get("ok"), "error": d.get("error"),
                    "count": len(d.get("events") or [])}

    # ---- загрузка (фоновый поток) ----

    def fetch(self, name):
        conf = self.defs().get(name) or {}
        url = conf.get("ical") or conf.get("url")
        if not url:
            raise ValueError("не задана ссылка ical")
        req = urllib.request.Request(str(url), headers={
            "User-Agent": "wb-svg-panel", "Accept": "text/calendar, */*"})
        with urllib.request.urlopen(req, timeout=float(conf.get("timeout", 20))) as resp:
            raw = resp.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise ValueError("файл календаря больше %d МБ" % (MAX_FILE // 1048576))
        text = raw.decode("utf-8", "replace")
        if "BEGIN:VCALENDAR" not in text:
            raise ValueError("по ссылке не календарь iCal")
        return parse_ics(text, self.tz())

    def refresh_due(self, force=False):
        """Обновить те календари, у которых вышел срок. True - что-то
        поменялось."""
        from . import stats
        changed = False
        now = time.time()
        for name, conf in self.defs().items():
            with self.lock:
                d = self.data.setdefault(name, {"seq": 0, "next": 0})
                due = force or now >= d.get("next", 0)
            if not due:
                continue
            every = parse_duration(conf.get("refresh", DEFAULT_REFRESH)) or 900
            t0 = time.time()
            try:
                events = self.fetch(name)
                with self.lock:
                    sig = [(e.get("uid"), str(e.get("start")), e.get("title"),
                            e.get("rrule"), str(e.get("recurrence_id")),
                            str(e.get("end")), e.get("status"))
                           for e in events]
                    new = sig != d.get("sig")
                    d.update({"events": events, "ok": time.time(), "error": None,
                              "next": now + every, "sig": sig})
                    if new:
                        d["seq"] = d.get("seq", 0) + 1
                        self._days.clear()
                if new:
                    changed = True
                    log.info("календарь %s: событий %d", name, len(events))
            except Exception as exc:                # noqa: BLE001
                # Ссылку в журнал не пишем: это доступ к расписанию.
                msg = str(exc).split("\n")[0][:200]
                with self.lock:
                    first = d.get("error") != msg
                    d.update({"error": msg, "next": now + min(every, 300)})
                if first:
                    log.warning("календарь %s: %s", name, msg)
            finally:
                stats.took("calendar", time.time() - t0)
            if self.state is not None and changed:
                self.state.bump("calendar:" + name)
        return changed

    def loop(self, stop_event):
        """Фоновый поток: раз в полминуты смотрим, кому пора обновиться."""
        while not stop_event.is_set():
            try:
                if self.config is not None:
                    self.config.reload()
                self.refresh_due()
            except Exception:                       # noqa: BLE001
                log.exception("сбой загрузки календарей")
            stop_event.wait(30)

    # ---- чтение (отрисовка) ----

    def day(self, names, day):
        """События дня day (date) по календарям names, по порядку."""
        tz = self.tz()
        start = dt.datetime(day.year, day.month, day.day, tzinfo=tz)
        end = start + dt.timedelta(days=1)
        out = []
        for name in names:
            with self.lock:
                d = self.data.get(name) or {}
                key = (name, d.get("seq", 0), day)
                hit = self._days.get(key)
                events = d.get("events") or []
            if hit is None:
                hit = expand(events, start, end, tz)
                with self.lock:
                    self._days[key] = hit
                    if len(self._days) > 64:
                        self._days.pop(next(iter(self._days)))
            for ev in hit:
                item = dict(ev)
                item["cal"] = name
                item["color"] = self.color(name)
                out.append(item)
        out.sort(key=lambda x: (not x["all_day"], x["start"], x["title"]))
        return out


calendars = Calendars()
