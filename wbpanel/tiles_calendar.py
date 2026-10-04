# -*- coding: utf-8 -*-
"""
Плитка расписания: события сегодняшнего дня из календарей config.yaml.

    calendars:
      family: {title: "Семья", ical: "https://…/basic.ics", color: "#3B8FD4"}

    - type: agenda
      calendars: [family, work]     # без поля - все
      w: 2
      h: 2

Сверху события на весь день, ниже по времени. Прошедшие бледные, идущее
сейчас помечено «сейчас», следующее - обычным начертанием. Не влезает -
первыми уходят прошедшие, потом последняя строка говорит «ещё N». День
кончился - «сегодня больше ничего» и первые события завтра.

Нажатие открывает окно: сегодня и завтра целиком, с местом проведения.
"""

import datetime as dt

from .calendars import WEEKDAYS, MONTHS, calendars, day_label
from .const import CELL
from .geometry import text_width
from .registry import Tile, Zone, tile

LETTER = 0.6


def _fit(text, width, size):
    if text_width(text, size, LETTER) <= width:
        return text
    out = text
    while out and text_width(out + "…", size, LETTER) > width:
        out = out[:-1]
    return out.rstrip() + "…"


def _hm(value):
    return value.strftime("%H:%M")


def _flat(text):
    return str(text).replace("|", " ").replace(";", ",")


def _when_text(ev, day, tz):
    """Что писать в колонке времени: 14:00, «до 01:00» для начатого вчера."""
    start = ev["start"].astimezone(tz)
    if start.date() < day:
        return "до " + _hm(ev["end"].astimezone(tz))
    return _hm(start)


def _state(ev, now):
    if ev["all_day"]:
        return "day"
    if ev["end"] <= now and ev["end"] != ev["start"]:
        return "past"
    if ev["start"] <= now < ev["end"]:
        return "now"
    if ev["start"] < now and ev["end"] == ev["start"]:
        return "past"
    return "next"


def agenda_attr(days, tz, now):
    """
    Окно: дни строками «дата|вид|время|название|цвет|место|состояние».
    Дни разделены записью вида «D».
    """
    parts = []
    for day, events in days:
        parts.append("D|%s" % _flat(day_label(day, now.date())))
        for ev in events:
            st = _state(ev, now) if day == now.date() else ("day" if ev["all_day"] else "next")
            when = "весь день" if ev["all_day"] else (
                _when_text(ev, day, tz) + ("–" + _hm(ev["end"].astimezone(tz))
                                           if ev["end"] > ev["start"] and
                                           ev["start"].astimezone(tz).date() == day
                                           else ""))
            parts.append("E|%s|%s|%s|%s|%s" % (
                _flat(when), _flat(ev["title"]), _flat(ev["color"]),
                _flat(ev.get("location") or ""), st))
    return ";".join(parts)


@tile("agenda")
class Agenda(Tile):
    roles = ()
    # Из MQTT ничего не читает: события приносит фоновый поток.
    reads = False

    def check(self, conf, bound):
        spec = conf.get("calendars")
        defs = calendars.defs()
        if not defs:
            return "нет раздела calendars: в config.yaml"
        if spec not in (None, "", "all"):
            items = spec if isinstance(spec, list) else [spec]
            missing = [str(n) for n in items if str(n) not in defs]
            if missing:
                return "нет календарей: %s" % ", ".join(missing)
        return None

    def prepare(self, ctx):
        names = calendars.pick(ctx.opt("calendars"))
        tz = calendars.tz()
        now = dt.datetime.now(tz)
        today = now.date()
        tomorrow = today + dt.timedelta(days=1)
        base = {"on": False, "icon": ctx.opt("icon", "calendar"),
                "always_status": True, "rows": [], "more": 0}
        title = str(ctx.opt("title") or "Сегодня")
        base["title"] = title
        base["title_lines"] = [title]
        base["title_s"] = title[:10]
        base["date"] = "%s %d %s" % (WEEKDAYS[today.weekday()], today.day,
                                     MONTHS[today.month - 1])
        if not names:
            base["status"] = "нет календарей"
            return base

        errors = [calendars.status(n) for n in names]
        never = all(not s["ok"] for s in errors)
        ev_today = calendars.day(names, today)
        ev_tomorrow = calendars.day(names, tomorrow)
        base["_agenda"] = agenda_attr([(today, ev_today), (tomorrow, ev_tomorrow)], tz, now)

        upcoming = [e for e in ev_today if _state(e, now) in ("now", "next")]
        cur = [e for e in ev_today if _state(e, now) == "now"]
        nxt = [e for e in ev_today if _state(e, now) == "next"]
        if never:
            base["status"] = ("нет связи с календарём" if any(s["error"] for s in errors)
                              else "загрузка…")
        elif cur:
            base["status"] = "сейчас: " + cur[0]["title"]
        elif nxt:
            base["status"] = "%s %s" % (_hm(nxt[0]["start"].astimezone(tz)), nxt[0]["title"])
        elif any(e["all_day"] for e in ev_today):
            base["status"] = next(e["title"] for e in ev_today if e["all_day"])
        elif ev_today:
            base["status"] = "сегодня больше ничего"
        else:
            base["status"] = "сегодня свободно"

        width = float(ctx.opt("inner_w") or 0)
        height = float(ctx.opt("inner_h") or 0)
        if width < CELL * 2:
            # Мелкая плитка: вместо «Сегодня» - когда ближайшее.
            base["compact_agenda"] = True
            base["title_s"] = ("сейчас" if cur else
                               _hm(nxt[0]["start"].astimezone(tz)) if nxt else "свободно")
            return base
        if height < 117:
            # Половинная: название и одна строка про ближайшее, по ширине.
            base["compact_agenda"] = True
            base["status"] = _fit(base["status"], width - 56 - 16, 13.5)
            return base

        size = 14.0
        line = round(size * 1.6, 1)
        top = 58.0
        bottom = 14.0
        room = max(0, int((height - top - bottom) // line))
        # «сейчас» и «весь день» должны влезать целиком, иначе колонка
        # времени превращается в «сейч…».
        time_w = 62.0
        text_x = 34 + time_w + 6
        text_room = width - text_x - 18
        # Дата в шапке справа - если остаётся место после названия.
        head_room = width - (16 + 34) - 20 - text_width(title, 15.5, LETTER) - 12
        if text_width(base["date"], 13.5, LETTER) > head_room:
            base["date"] = ""

        rows = []
        # Сегодня: весь день, потом по времени. Не влезает - первыми
        # уходят прошедшие.
        today_rows = list(ev_today)
        pasts = [e for e in today_rows if _state(e, now) == "past"]
        while len(today_rows) > room and pasts:
            today_rows.remove(pasts.pop(0))
        for ev in today_rows:
            st = _state(ev, now)
            rows.append({"kind": "ev", "state": st, "color": ev["color"],
                         "time": ("весь день" if ev["all_day"] else
                                  ("сейчас" if st == "now" else _when_text(ev, today, tz))),
                         "text": ev["title"]})
        # День кончился или пуст - показываем завтра.
        if not upcoming and room - len(rows) >= 2 and ev_tomorrow:
            rows.append({"kind": "sep", "text": "Завтра"})
            for ev in ev_tomorrow:
                rows.append({"kind": "ev", "state": "day" if ev["all_day"] else "next",
                             "color": ev["color"],
                             "time": "весь день" if ev["all_day"] else _hm(ev["start"].astimezone(tz)),
                             "text": ev["title"]})
        if not rows:
            rows.append({"kind": "empty", "text": base["status"]})

        shown = rows[:room]
        if len(rows) > room and room > 0:
            shown = rows[:room - 1]
            base["more"] = len(rows) - len(shown)

        y = top + size
        out = []
        for r in shown:
            item = dict(r)
            item["y"] = round(y, 1)
            if r["kind"] == "ev":
                item["text"] = _fit(r["text"], text_room, size)
                item["small"] = r["time"] == "весь день" or r["time"].startswith("до ")
                item["time"] = _fit(r["time"], time_w, 12 if item["small"] else size)
                item["dot_y"] = round(y - size * 0.35, 1)
            out.append(item)
            y += line
        base["rows"] = out
        base["more_y"] = round(y, 1)
        base["size"] = size
        base["text_x"] = text_x
        return base

    def zones(self, ctx, data):
        return [Zone("open", "all", pad="agenda")]

    def pad(self, ctx, data):
        return {"agenda": data.get("_agenda") or ""}
