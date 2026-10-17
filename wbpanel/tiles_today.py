# -*- coding: utf-8 -*-
"""
Плитка «Сегодня» (с 1.43): дела из списка и события календаря - одной
плиткой. Оба отвечают на один вопрос - что сегодня, - и на стене две
плитки рядом занимали вдвое больше места.

    - type: today
      list: home              # список из lists:, необязательно
      calendars: [family]     # без поля - все календари; none - без них
      w: 2
      h: 3

Сверху дела квадратиками, под чертой события по времени - как на
расписании: цветная точка календаря, время, название. Строки плотнее,
чем на отдельных плитках: при том же кегле влезает больше.

Места делится так: событиям - сколько им нужно, но не больше половины,
если дел много; дела забирают остальное. Не влезло - последняя строка
раздела говорит «ещё N».

Нажатие открывает окно: список с галочками, под ним сегодня и завтра.
"""

import datetime as dt

from .calendars import WEEKDAYS, MONTHS, calendars
from .const import CELL
from .registry import Tile, Zone, tile
from .geometry import text_width
from .tiles_calendar import _fit as _fit_ev, _state, agenda_attr, event_rows, summary
from .tiles_lists import _fit

#: Кегль строк. Межстрочный - плотнее, чем у списка (1.65) и расписания (1.6).
SIZE = 14.0
LINE = 1.45


def _no_cal(spec):
    """calendars: none - плитка без календаря, только дела."""
    return spec is False or (isinstance(spec, str) and
                             spec.strip().lower() in ("none", "no", "нет"))


def _cal_names(spec):
    return [] if _no_cal(spec) else calendars.pick(spec)


@tile("today")
class Today(Tile):
    roles = ()
    reads = False

    def check(self, conf, bound):
        from .lists import lists
        name = conf.get("list")
        spec = conf.get("calendars")
        no_cal = _no_cal(spec)
        if name and not lists.known(str(name)):
            return "нет списка %s в lists:" % name
        if not no_cal and spec not in (None, "", "all"):
            items = spec if isinstance(spec, list) else [spec]
            missing = [str(n) for n in items if str(n) not in calendars.defs()]
            if missing:
                return "нет календарей: %s" % ", ".join(missing)
        if not name and (no_cal or not calendars.defs()):
            return "нечего показать: нет ни list, ни календарей"
        return None

    def prepare(self, ctx):
        from .lists import lists

        tz = calendars.tz()
        now = dt.datetime.now(tz)
        today = now.date()
        tomorrow = today + dt.timedelta(days=1)
        title = str(ctx.opt("title") or "Сегодня")
        base = {"on": False, "always_status": True, "title": title,
                "title_lines": [title], "title_s": title[:10],
                "tasks": [], "events": [], "more_t": 0, "more_e": 0,
                "day": today.day, "wd": WEEKDAYS[today.weekday()],
                "date": "%s %d %s" % (WEEKDAYS[today.weekday()], today.day,
                                      MONTHS[today.month - 1])}

        # --- дела ---
        name = str(ctx.opt("list") or "")
        opened = []
        if name and lists.known(name):
            view = lists.view(name)
            opened = view["open"]
            base["list"] = name
            base["list_title"] = view["title"]
        base["count"] = len(opened)

        # --- события ---
        names = _cal_names(ctx.opt("calendars"))
        ev_today, ev_tomorrow, cal_text = [], [], ""
        if names:
            ev_today = calendars.day(names, today)
            ev_tomorrow = calendars.day(names, tomorrow)
            base["_agenda"] = agenda_attr([(today, ev_today), (tomorrow, ev_tomorrow)],
                                          tz, now)
            sts = [calendars.status(n) for n in names]
            if all(not s["ok"] for s in sts):
                cal_text = ("нет связи с календарём" if any(s["error"] for s in sts)
                            else "загрузка…")
            else:
                cal_text = summary(ev_today, now, tz)
        base["has_cal"] = bool(names)

        busy = [e for e in ev_today if _state(e, now) in ("now", "next")]
        base["status"] = (cal_text if busy or not opened
                          else opened[0]["text"]) or ("пусто" if name else "")

        width = float(ctx.opt("inner_w") or 0)
        height = float(ctx.opt("inner_h") or 0)
        # Узкая (в одну колонку) - тоже мелкая: два раздела по паре строк
        # на ней не читались, а «ещё N» занимало половину.
        if width < CELL * 2.6 or height < 117:
            # Мелкая: листок календаря и одна строка - ближайшее событие,
            # а если день свободен - первое дело.
            base["compact_today"] = True
            base["status"] = _fit(base["status"], width - (56 if height < 117 else 24) - 16, 13.5)
            return base

        size = SIZE
        line = round(size * LINE, 1)
        top, bottom = 56.0, 12.0
        gap = round(line * 0.7, 1)           # черта между делами и событиями
        both = bool(name) and bool(names)
        avail = height - top - bottom - (gap if both else 0)
        room = max(0, int(avail // line))

        head_room = width - 50 - 20 - _tw(title, 15.5) - 12
        if _tw(base["date"], 13.5) > head_room:
            base["date"] = ""

        # Сколько строк нужно событиям: все сегодняшние, кроме прошедших
        # (их можно и выкинуть), но хотя бы одна - «сегодня свободно».
        need_e = 0
        if names:
            need_e = max(1, len([e for e in ev_today if _state(e, now) != "past"]))
            if not busy and ev_tomorrow:
                need_e = max(need_e, 1 + len(ev_tomorrow) + len(ev_today))
        need_t = max(1, len(opened)) if name else 0
        if not names:
            room_t, room_e = room, 0
        elif not name:
            room_t, room_e = 0, room
        else:
            room_e = min(need_e, max(room - need_t, room // 2, 1))
            room_t = room - room_e
            if room_t > need_t:
                room_e += room_t - need_t
                room_t = need_t

        y = top + size
        text_room = width - 34 - 16
        if name:
            shown = opened[:room_t]
            if len(opened) > room_t and room_t > 0:
                shown = opened[:room_t - 1]
                base["more_t"] = len(opened) - len(shown)
            for item in shown:
                base["tasks"].append({"text": _fit(item["text"], text_room, size),
                                      "y": round(y, 1)})
                y += line
            if not opened and room_t:
                base["tasks_empty"] = {"y": round(y, 1),
                                       "text": "дел нет" if not lists.view(name)["done"]
                                       else "всё сделано"}
                y += line
            if base["more_t"]:
                base["more_t_y"] = round(y, 1)
                y += line
            base["rules"] = [round(top + size + i * line + size * 0.5, 1)
                             for i in range(room_t)]
        if both:
            base["div_y"] = round(y - size + gap * 0.42, 1)
            y += gap
        if names:
            time_w = 58.0
            text_x = 30 + time_w + 6
            ev_room = width - text_x - 16
            empty = cal_text or "сегодня свободно"
            rows, base["more_e"] = event_rows(ev_today, ev_tomorrow, now, tz, room_e, empty,
                                               fill=True)
            for r in rows:
                item = dict(r)
                item["y"] = round(y, 1)
                if r["kind"] == "ev":
                    item["text"] = _fit_ev(r["text"], ev_room, size)
                    item["small"] = r["time"] == "весь день" or r["time"].startswith("до ")
                    item["time"] = _fit_ev(r["time"], time_w, 12 if item["small"] else size)
                    item["dot_y"] = round(y - size * 0.35, 1)
                base["events"].append(item)
                y += line
            if base["more_e"]:
                base["more_e_y"] = round(y, 1)
            base["text_x"] = text_x
        base["size"] = size
        base["box"] = round(size * 0.72, 1)
        return base

    def zones(self, ctx, data):
        if not data.get("list") and not data.get("has_cal"):
            return []
        return [Zone("open", "all", pad="today")]

    def pad(self, ctx, data):
        out = {"agenda": data.get("_agenda") or ""}
        if data.get("list"):
            out["list"] = data["list"]
        return out


def _tw(text, size):
    return text_width(text, size, 0.6)
