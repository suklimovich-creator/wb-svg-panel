# -*- coding: utf-8 -*-
"""
Фанкойл: канальный термостат BAC-1000 и родня.

Почему это термостат, а не кондиционер
--------------------------------------
Прибор сравнивает комнатную температуру с уставкой и закрывает разрыв
между ними - хоть нагревом, хоть охлаждением. Это ровно та логика, под
которую сделан циферблат термостата: дуга со шкалой, две засечки - факт
и цель, закрашен участок между ними. Дошёл до уставки - закраска исчезла,
и это видно на дуге, без единого слова. Колесо кондиционера так не умеет:
оно показывает уставку и ничего не говорит о том, далеко ли до неё.

Что прибор умеет
----------------
Режимов три - нагрев, охлаждение, вентиляция; «авто», как у сплита, нет
вовсе. В вентиляции клапан закрыт всегда, вентилятор крутится: это продув,
а не климат.

Скоростей четыре: авто, высокая, средняя, низкая. В авто прибор выбирает
скорость сам по разнице температур, поэтому «просили» и «крутится» -
разные каналы с разными таблицами. У цели ноль означает «авто», у факта
такого значения нет, зато есть «стоит».

Дойдя до уставки, прибор закрывает клапан. Что дальше - зависит от
исполнения: A1 глушит и вентилятор, A2 оставляет его на низкой скорости.
Закрытый клапан при работающем вентиляторе - норма, а не сбой.

Решения, которые из этого следуют
---------------------------------
    рамка горит по работе оборудования, а не по клапану
        клапан - это подача воды, а не работа: он открыт и у прибора,
        который только что включился и ещё ничего не сделал. Работает
        фанкойл тогда, когда крутится вентилятор;

    режим, скорость и клапан вынесены в колонку справа
        на широкой плитке дуга остаётся окружностью и смещается влево,
        а освободившееся место занимают три строки состояния. Строкой
        внизу это не передать: «охлаждает · авто (2) · клапан» никто не
        прочитает;

    короткое нажатие раскрывает плитку на весь экран
        фанкойл не выключают промахом по экрану. Питание живёт на пульте,
        который открывается долгим нажатием;

    все значения - таблицы в конфиге
        режимы и скорости у каждого прибора свои и почти всегда числовые.
        Умолчания описывают BAC-1000, чужой прибор описывается в YAML без
        единой строки Python.

Конфиг
------

    - type: fancoil
      title: "Фанкойл"
      w: 2
      channel_switch:    "thermostat-interface-v1-0_1/power"
      channel_mode:      "thermostat-interface-v1-0_1/operating_mode"
      channel_fan:       "thermostat-interface-v1-0_1/fan_speed"
      channel_fan_state: "thermostat-interface-v1-0_1/fan_speed_status"
      channel_target:    "thermostat-interface-v1-0_1/set_temperature"
      channel_current:   "thermostat-interface-v1-0_1/room_temperature"
      channel_state:     "thermostat-interface-v1-0_1/valve"
      scale_min: 14      # пределы ШКАЛЫ на дуге
      scale_max: 30
      temp_min: 16       # пределы УСТАВКИ на пульте
      temp_max: 30

Пределы уставки задаются здесь нарочно: в meta канала у BAC-1000 стоит
max 65535, и шкалу по нему не построить. Сам прибор принимает от 5 до 35.

Другой прибор - свои таблицы:

      modes:
        0: {title: "Охлаждение", icon: snow, verb: "Охлаждает", cool: true}
        3: {title: "Осушение", icon: drop}
      fans:
        0: {title: "Авто", auto: true}
        1: {title: "3", level: 3}
      fan_states:
        4: {title: "стоит", off: true}
"""

from .geometry import _fmt, text_width
from .registry import Tile, Zone, tile
from .tiles import DIAL_START, DIAL_SWEEP, dial_arc, dial_point


#: Режимы BAC-1000. Порядок тот же, в каком кнопки встанут на пульте.
#: valve - участвует ли в режиме клапан (в вентиляции он закрыт всегда),
#: cool - дуга красится синим: прибору идти вниз, а не вверх.
FANCOIL_MODES = (
    (0, {"title": "Охлаждение", "icon": "snow", "verb": "Охлаждает",
         "cool": True}),
    (1, {"title": "Нагрев", "icon": "heat", "verb": "Греет"}),
    (2, {"title": "Вентиляция", "icon": "fan", "verb": "Продув",
         "valve": False}),
)

#: Скорость - ЦЕЛЬ. Единица это максимум, поэтому level идёт наоборот.
FANCOIL_FANS = (
    (0, {"title": "Авто", "auto": True}),
    (3, {"title": "1", "level": 1}),
    (2, {"title": "2", "level": 2}),
    (1, {"title": "3", "level": 3}),
)

#: Скорость - ФАКТ. Другая таблица: нуля нет, четвёрка означает «стоит».
FANCOIL_FAN_STATES = (
    (1, {"title": "3", "level": 3}),
    (2, {"title": "2", "level": 2}),
    (3, {"title": "1", "level": 1}),
    (4, {"title": "стоит", "off": True}),
)

#: Сколько градусов считаем «дошёл»: у прибора свой гистерезис, и
#: закраска дуги в полградуса всё равно неразличима.
REACHED = 0.3


def key(value):
    """
    Ключ таблицы из того, что пришло.

    Канал отдаёт «1», конфиг - число 1, а иной прибор пришлёт «1.0». Без
    приведения кнопка режима молча не подсветится, и искать это потом долго.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def table(raw, default):
    """
    Таблица значений -> [(ключ, {title, icon, …}), ...] в порядке конфига.

    Понимает три записи, потому что в живых конфигах встречаются все три:

        {0: "Охлаждение"}                        коротко
        {0: {title: "Охлаждение", icon: snow}}   с подробностями
        [{value: 0, title: "Охлаждение"}]        списком, когда важен порядок
    """
    if not raw:
        return [(key(value), dict(body)) for value, body in default]

    if isinstance(raw, dict):
        items = sorted(raw.items(), key=lambda pair: _order(pair[0]))
    else:
        items = [(item.get("value"), item) for item in raw
                 if isinstance(item, dict)]

    out = []
    for value, body in items:
        if isinstance(body, dict):
            body = dict(body)
            body.setdefault("title", str(value))
        else:
            body = {"title": str(body)}
        out.append((key(value), body))
    return out or [(key(value), dict(body)) for value, body in default]


def _order(value):
    try:
        return (0, float(value), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(value))


def find(items, value):
    """Запись таблицы по значению канала. Не нашли - пустая, без падения."""
    for row_key, body in items:
        if row_key == value:
            return body
    return {}


def buttons(items):
    """Таблица -> "0|Охлаждение;1|Нагрев" для пульта."""
    return ";".join("%s|%s" % (row_key, body.get("title", row_key))
                    for row_key, body in items if row_key != "")


def build_gauge(width, height, lo, hi, current, target, cx):
    """
    Циферблат: незамкнутая дуга со шкалой lo…hi, закрашен разрыв между
    фактом и уставкой - то есть ровно то, что прибору предстоит закрыть.

    Тот же циферблат, что у термостата, но центр задаётся снаружи: на
    широкой плитке он уезжает влево, освобождая место колонке состояний.
    Радиус считается от меньшей стороны, поэтому дуга остаётся
    окружностью и не растягивается в овал.
    """
    span = max(float(hi) - float(lo), 1e-6)
    cy = height * 0.59
    r = min(width, height) * 0.36

    def deg(value):
        f = (float(value) - float(lo)) / span
        return DIAL_START + max(0.0, min(1.0, f)) * DIAL_SWEEP

    out = {"cx": round(cx, 1), "cy": round(cy, 1), "r": round(r, 1),
           "track": dial_arc(cx, cy, r, DIAL_START, DIAL_START + DIAL_SWEEP),
           "fill": "", "marks": []}

    pts = [v for v in (current, target) if v is not None]
    if len(pts) == 2:
        a, b = sorted(deg(v) for v in pts)
        out["fill"] = dial_arc(cx, cy, r, a, b)
    elif len(pts) == 1:
        out["fill"] = dial_arc(cx, cy, r, DIAL_START, deg(pts[0]))

    for value, kind in ((current, "current"), (target, "target")):
        if value is None:
            continue
        d = deg(value)
        inner = 0.80 if kind == "target" else 0.86
        x0, y0 = dial_point(cx, cy, r * inner, d)
        x1, y1 = dial_point(cx, cy, r * 1.20, d)
        out["marks"].append({"kind": kind,
                             "x1": round(x0, 1), "y1": round(y0, 1),
                             "x2": round(x1, 1), "y2": round(y1, 1)})
    return out


@tile("fancoil")
class Fancoil(Tile):
    """Фанкойл: циферблат термостата плюс колонка режима, скорости и клапана."""

    roles = ("switch", "mode", "fan", "fan_state", "target", "current", "state")

    def tables(self, ctx):
        return (table(ctx.opt("modes"), FANCOIL_MODES),
                table(ctx.opt("fans"), FANCOIL_FANS),
                table(ctx.opt("fan_states"), FANCOIL_FAN_STATES))

    def prepare(self, ctx):
        modes, fans, fan_states = self.tables(ctx)

        # Питание может быть и не заведено: у части приборов «выключено» -
        # это просто ещё одно значение в таблице режимов.
        power = ctx.flag("switch") if ctx.has("switch") else True

        mode_key = key(ctx.raw("mode"))
        mode = find(modes, mode_key)
        uses_valve = mode.get("valve", True)

        fan_key = key(ctx.raw("fan"))
        fan = find(fans, fan_key)

        fact_key = key(ctx.raw("fan_state")) if ctx.has("fan_state") else ""
        fact = find(fan_states, fact_key) if fact_key else {}
        spinning = bool(fact) and not fact.get("off")

        valve = ctx.flag("state") if ctx.has("state") else None

        current = ctx.number("current")
        target = ctx.number("target")
        reached = (current is not None and target is not None
                   and abs(target - current) <= REACHED)

        width = float(ctx.opt("inner_w") or 170)
        height = float(ctx.opt("chart_h") or 170)
        # Колонка состояний помещается, только если плитка заметно шире
        # своей высоты. На квадратной ей пришлось бы лезть на дугу.
        side = width >= height * 1.5
        cx = height * 0.52 if side else width / 2.0

        gauge = None
        if ctx.opt("dial", True):
            gauge = build_gauge(width, height,
                                ctx.opt("scale_min", 14), ctx.opt("scale_max", 30),
                                current, target, cx)

        # Работает ли оборудование. Клапан - это подача воды, а не работа:
        # он открыт и у прибора, который только что включился. Работа - это
        # крутящийся вентилятор; если факта нет, судим по клапану и режиму.
        if not power:
            active = False
        elif fact_key:
            active = spinning
        elif uses_valve and valve is not None:
            active = valve
        else:
            active = True

        target_num = self.number(target)
        big = 30.0 * float(ctx.opt("fs", 1.0))
        return {
            "dial": gauge,
            "side": self.side_box(width, height) if side and gauge else None,
            "rows": self.rows(width, height, mode, fan, fact, valve, power)
                    if side and gauge else [],
            # Узкая плитка: колонки нет, и значки уходят в верхние углы -
            # режим слева, вентилятор со ступенями справа. Место там всё
            # равно пустует, а под дугой остаётся одно слово состояния.
            "corners": self.corners(width, mode, fan, fact, power)
                       if gauge and not side else None,
            "on": active,
            "enabled": power,
            "cooling": bool(mode.get("cool")),
            "mode": mode_key,
            "mode_ru": mode.get("title", ""),
            "icon": (ctx.opt("icon")
                     or (mode.get("icon") if power else "power") or "fan"),
            "target_num": target_num,
            "target_value": target,
            "current_value": current,
            "target": ("%s°" % target_num) if target is not None else "--",
            "deg_dx": round(text_width(target_num, big) / 2.0
                            + 1.0 * float(ctx.opt("fs", 1.0)), 1),
            "deg_size": round(big * 0.60, 1),
            "current": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "short": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "status": self.status(power, mode, uses_valve, valve, fan, fact,
                                  reached, side),
            "always_status": True,
            "reach": None,
            "fan_now": fan_key,
            "lo": float(ctx.opt("temp_min", 16)),
            "hi": float(ctx.opt("temp_max", 30)),
        }

    @staticmethod
    def side_box(width, height):
        """Колонка справа от дуги: где начинается текст и где стоят полосы."""
        x = round(height * 1.04 + 4, 1)
        return {"x": x, "text_x": round(x + 24, 1),
                "bars_x": round(width - 20 - 21, 1)}

    @staticmethod
    def level_of(fan, fact):
        """Сколько ступеней зажечь: смотрим на факт, цель - только запасной."""
        if fact.get("off"):
            return 0
        return fact.get("level", fan.get("level", 0))

    def corners(self, width, mode, fan, fact, power):
        """Значки в верхних углах узкой плитки."""
        level = self.level_of(fan, fact)
        bars_x = width - 31 - 26
        return {
            "mode": {"icon": mode.get("icon", "fan"), "x": 14, "on": power},
            "fan": {"icon": "fan", "x": round(width - 31, 1),
                    "on": power and level > 0,
                    "bars": [{"x": round(bars_x + i * 7, 1),
                              "y": round(22 - i * 3, 1),
                              "h": 4 + i * 3, "on": power and level > i}
                             for i in range(3)]},
        }

    def rows(self, width, height, mode, fan, fact, valve, power):
        """Три строки справа: режим, вентилятор, клапан."""
        box = self.side_box(width, height)
        fact_title = fact.get("title", "")
        level = self.level_of(fan, fact)
        if fact.get("off"):
            fan_text = "Вентилятор стоит"
        elif fan.get("auto"):
            # Цель «авто» и факт вместе: скорость выбирает прибор, и
            # увидеть его выбор больше негде.
            fan_text = "Авто" + (" · %s" % fact_title if fact_title else "")
        else:
            fan_text = "Скорость " + (fact_title or fan.get("title", "?"))

        out = [
            {"icon": mode.get("icon", "fan"), "y": round(height * 0.33, 1),
             "text": mode.get("title", "—"), "on": power},
            {"icon": "fan", "y": round(height * 0.55, 1),
             "text": fan_text, "on": power and level > 0,
             "bars": [{"x": box["bars_x"] + i * 7,
                       "y": round(height * 0.55 - 3 - i * 3, 1),
                       "h": 4 + i * 3, "on": power and level > i}
                      for i in range(3)]},
        ]
        if valve is not None:
            out.append({"icon": "drop", "y": round(height * 0.77, 1),
                        "text": "Клапан открыт" if valve else "Клапан закрыт",
                        "on": bool(valve)})
        return out

    @staticmethod
    def number(value):
        """Уставка: 25.5 показываем с десятой, 25 - без неё."""
        if value is None:
            return "--"
        return _fmt(value, 0 if float(value).is_integer() else 1)

    @staticmethod
    def status(power, mode, uses_valve, valve, fan, fact, reached, side):
        """
        Строка под дугой - только состояние прибора, одним словом.

        Режим, скорость и клапан показаны значками: на широкой плитке
        колонкой справа, на узкой - по верхним углам. Дублировать их
        словами незачем, места под дугой на это всё равно нет.
        """
        if not power:
            return "Выключен"

        if not uses_valve:
            return mode.get("verb") or mode.get("title", "")
        if valve:
            return mode.get("verb", "Работает")
        if reached:
            # То самое «дошёл до уставки»: на дуге закраска уже исчезла,
            # словом остаётся назвать, что прибор держит достигнутое.
            return "Поддерживает"
        return "Ждёт"

    def zones(self, ctx, data):
        """
        Короткое нажатие раскрывает плитку, долгое открывает пульт.

        Питание тоже описано зоной, хотя касанием не переключается: из неё
        пульт берёт топик и значения для своей кнопки. Раскрытие стоит в
        обработчике раньше переключения, поэтому промах по экрану фанкойл
        не выключит.
        """
        out = [Zone("expand", "all"), Zone("pad", "long", pad="ac")]
        if ctx.has("switch"):
            out.append(Zone("toggle", "all", "switch",
                            {"on": str(ctx.opt("command_on", "1")),
                             "off": str(ctx.opt("command_off", "0"))},
                            state=bool(data.get("enabled"))))
        return out

    def pad(self, ctx, data):
        modes, fans, _fan_states = self.tables(ctx)

        def command(role):
            bound = ctx.bound.get(role)
            return bound.command if bound else None

        return {
            "ac_target": command("target"),
            "ac_mode": command("mode"),
            "ac_fan": command("fan"),
            "ac_lo": str(data.get("lo", 16)),
            "ac_hi": str(data.get("hi", 30)),
            "ac_step": str(ctx.step("target", 0.5)),
            "ac_now": data.get("target_num", "--"),
            "ac_cur": data.get("current", ""),
            "ac_mode_now": data.get("mode", ""),
            "ac_fan_now": data.get("fan_now", ""),
            # Кнопки пульта. У фанкойла это числа со своими подписями, и
            # зашитый в страницу набор кондиционера здесь не подходит.
            "ac_modes": buttons(modes),
            "ac_fans": buttons(fans),
        }
