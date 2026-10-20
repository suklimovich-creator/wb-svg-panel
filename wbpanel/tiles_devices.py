# -*- coding: utf-8 -*-
"""
Устройства с управлением: лента, диммер, штора, термостат.

Ради них контракт и затевался: у каждой есть цель, факт и состояние, и
раньше эти связи выписывались в конфиге по одной, а разрешения на запись
- отдельным перебором в другом файле, который регулярно забывали дополнить.

Имена полей оставлены прежними: у диммера выключатель зовётся `channel`,
у ленты - `channel_switch`. Ломать существующие конфиги ради стройности
незачем, для этого у плитки есть fields и commands.
"""

import time

from .geometry import _fmt, field_color, text_width
from .registry import Tile, Zone, tile
from .tiles import DIAL_START, DIAL_SWEEP, HEAT_MODE, HEAT_NOW, build_dial
from .state import to_float
from .skeuo import dimmer_layout, knob_dial, sphere_tile
from .sky import drapes, roman, scene, weather_now
from . import palette


def _percent(ctx, role, default_max=100.0):
    """
    Величина в процентах от своего максимума.

    Максимум канал сообщает сам: у диммера Wiren Board шкала 0..100, у
    ленты бывает 0..255, и зашивать это в конфиг не нужно.
    """
    value = ctx.number(role)
    if value is None:
        return None
    top = to_float(ctx.meta(role, "max"), default_max) or default_max
    return max(0.0, min(100.0, value / top * 100.0))


@tile("light")
class Light(Tile):
    """
    Светодиодная лента: три величины на одной плитке.

      по горизонтали  - цветовая температура: слева холодный, справа тёплый
      по вертикали    - яркость: сверху ярко, снизу темно
      точка           - фактическое положение по обеим осям
      оранжевая рамка - включено
    """

    roles = ("switch", "bright", "temp")
    fields = {"switch": "channel_switch", "bright": "channel_brightness",
              "temp": "channel_temp"}
    commands = {"switch": "command_topic",
                "bright": "command_topic_brightness",
                "temp": "command_topic_temp"}

    def prepare(self, ctx):
        brightness = _percent(ctx, "bright")

        on = brightness is not None and brightness > 0
        if ctx.has("switch"):
            on = ctx.flag("switch")

        k_min = float(ctx.opt("temp_min", 2700))
        k_max = float(ctx.opt("temp_max", 6500))
        temp_raw = ctx.number("temp")
        kelvin = None
        if temp_raw is not None:
            if str(ctx.opt("temp_unit", "kelvin")).lower() == "percent":
                top = to_float(ctx.meta("temp", "max"), 100.0) or 100.0
                kelvin = k_min + (k_max - k_min) * max(0.0, min(1.0, temp_raw / top))
            else:
                kelvin = temp_raw

        # положение точки: 0 слева/сверху, 1 справа/снизу
        if kelvin is None:
            dot_x = None
        else:
            # слева холодный (k_max), справа тёплый (k_min)
            dot_x = (k_max - max(k_min, min(k_max, kelvin))) / max(k_max - k_min, 1.0)
        dot_y = None if brightness is None else 1.0 - brightness / 100.0

        bits = []
        if brightness is not None:
            bits.append("%d %%" % round(brightness))
        if kelvin is not None:
            bits.append("%d K" % round(kelvin))

        cold = ctx.opt("cold_color") or field_color(k_max)
        warm = ctx.opt("warm_color") or field_color(k_min)
        # С 1.18 лента - светящийся шар в сотах (look: classic - прежнее
        # поле). Яркость и теплота помнятся и у выключенной.
        sphere = None
        if ctx.opt("look", "sphere") != "classic":
            sphere = sphere_tile(float(ctx.opt("inner_w") or 170), float(ctx.opt("chart_h") or 170),
                                 (brightness or 0.0) / 100.0,
                                 dot_x if dot_x is not None else 0.5, on, cold, warm,
                                 float(ctx.opt("inner_w") or 170) < 156,
                                 light=not palette.is_dark(ctx.opt("theme") or "light"))
        return {
            "on": on,
            "sphere": sphere,
            "low_x": sphere["text_x"] if sphere and sphere["kind"] == "wide" else None,
            "dot_x": dot_x,
            "dot_y": dot_y,
            "has_dot": dot_x is not None and dot_y is not None,
            "cold": cold,
            "warm": warm,
            "color": field_color(kelvin if kelvin is not None else 3000),
            "short": " · ".join(bits),
            # на узкой плитке (w: 1) полная подпись не влезает
            "short_min": bits[0] if bits else "",
            "status": ("Включено" if on else "Выключено") if brightness is not None
                      else "Нет данных",
        }

    def zones(self, ctx, data):
        role = "switch" if ctx.has("switch") else "bright"
        return [Zone("toggle", "all", role,
                     {"on": str(ctx.opt("command_on", "1")),
                      "off": str(ctx.opt("command_off", "0"))}),
                Zone("pad", "long", pad="xy"),
                # Яркость и цвет крутятся только на пульте: на плитке для
                # них нет места, а промахнуться пальцем по полю легко.
                Zone("bright", "pad", "bright"),
                Zone("temp", "pad", "temp")]

    def pad(self, ctx, data):
        out = {"bright_max": str(to_float(ctx.meta("bright", "max"), 100.0) or 100.0)}
        if ctx.opt("look") == "classic":
            out["look"] = "classic"
        else:
            # шар крутится от текущего положения, даже если цвета нет
            out.update({"dot_x": "%.3f" % (data["dot_x"] if data.get("dot_x") is not None else 0.5),
                        "dot_y": "%.3f" % (data["dot_y"] if data.get("dot_y") is not None else 0.5),
                        "cold": data.get("cold", "#56AAFF"), "warm": data.get("warm", "#FF9337")})
        if ctx.has("temp"):
            out.update({
                "temp_max": str(to_float(ctx.meta("temp", "max"), 100.0) or 100.0),
                "temp_unit": str(ctx.opt("temp_unit", "kelvin")),
                "temp_lo": str(ctx.opt("temp_min", 2700)),
                "temp_hi": str(ctx.opt("temp_max", 6500)),
                "cold": data.get("cold", "#56AAFF"),
                "warm": data.get("warm", "#FF9337"),
                "dot_x": "%.3f" % (data.get("dot_x") or 0.5),
                "dot_y": "%.3f" % (data.get("dot_y") or 0.5),
            })
        return out


def _light_color(value):
    """Цвет света лампы: только #rrggbb; имя цвета карточки - не он."""
    value = str(value or "")
    return value if value.startswith("#") else "#E0A050"


@tile("dimmer")
class Dimmer(Tile):
    """
    Диммируемый светильник: заполнение плитки снизу вверх на высоту яркости.

    В отличие от ленты здесь одна величина, поэтому не поле, а простая
    заливка одним цветом.
    """

    roles = ("switch", "bright")
    fields = {"switch": "channel", "bright": "channel_brightness"}
    commands = {"switch": "command_topic",
                "bright": "command_topic_brightness"}

    def prepare(self, ctx):
        brightness = _percent(ctx, "bright")

        on = brightness is not None and brightness > 0
        if ctx.has("switch"):
            on = ctx.flag("switch")

        # С 1.17 диммер - колесо в прорези со шкалой (look: classic -
        # прежняя заливка). Уровень помним и у выключенного: серая шкала
        # показывает, с какой яркостью он включится.
        level = (brightness or 0.0) / 100.0
        roll = None
        if ctx.opt("look", "roller") != "classic":
            roll = dimmer_layout(float(ctx.opt("inner_w") or 170), float(ctx.opt("chart_h") or 170),
                                 level)
        return {
            "on": on,
            "roll": roll,
            "level": round(level, 3),
            "low_x": roll["text_x"] if roll and roll["kind"] == "wide" else None,
            "fill": 0.0 if (not on or brightness is None) else brightness / 100.0,
            # color: sage и т. п. - цвет карточки (1.43), а не света
            "color": _light_color(ctx.opt("color")),
            "short": ("%d %%" % round(brightness)) if brightness is not None else "",
            "status": ("Включено" if on else "Выключено") if brightness is not None
                      else "Нет данных",
        }

    def zones(self, ctx, data):
        role = "switch" if ctx.has("switch") else "bright"
        return [Zone("toggle", "all", role,
                     {"on": str(ctx.opt("command_on", "1")),
                      "off": str(ctx.opt("command_off", "0"))}),
                Zone("pad", "long", pad="level"),
                Zone("bright", "pad", "bright")]

    def pad(self, ctx, data):
        # Уровень - и у выключенного (1.52): диммер помнит яркость, с ней и
        # включится; пульт и жест на плитке начинают с неё, а не с нуля.
        out = {"bright_max": str(to_float(ctx.meta("bright", "max"), 100.0) or 100.0),
               "level": "%.3f" % (data.get("level") or 0.0)}
        if ctx.opt("look") == "classic":
            out["look"] = "classic"
        return out


def _window(ctx, kind):
    """Вид за окном для штор нового вида; None - прежний вид (look: classic)."""
    if ctx.opt("look") == "classic":
        return None
    w = float(ctx.opt("inner_w") or 170)
    h = float(ctx.opt("chart_h") or 170)
    fs = float(ctx.opt("fs") or 1.0)
    lat, lon = ctx.opt("sky_location") or (None, None)
    now = time.time()
    return scene(w, h, now=now, lat=lat, lon=lon, facing=ctx.opt("facing"),
                 weather=weather_now(ctx.state, ctx.opt("weather"), now),
                 seed="%s|%s" % (kind, ctx.opt("title") or ""), fs=fs)


@tile("curtain")
class Curtain(Tile):
    """
    Раздвижные шторы. Две створки едут от краёв к центру.
    100 - полностью раздвинуты, 0 - закрыты.

    Если заданы левая и правая роли, створки независимы; иначе обе
    рисуются по одному каналу положения.
    """

    roles = ("target", "left", "right", "state", "stop")
    fields = {"target": "channel", "left": "channel_left",
              "right": "channel_right", "state": "channel_state",
              "stop": "channel_stop"}
    commands = {"target": "command_topic", "stop": "command_topic_stop"}

    def prepare(self, ctx):
        if ctx.has("left") or ctx.has("right"):
            left, right = ctx.number("left"), ctx.number("right")
        else:
            left = right = ctx.number("target")

        if ctx.opt("inverted"):
            left = None if left is None else 100 - left
            right = None if right is None else 100 - right

        known = [v for v in (left, right) if v is not None]
        avg = sum(known) / len(known) if known else None

        # Необязательный канал состояния (у HomeKit-совместимых приводов
        # PositionState: 0 - закрывается, 1 - открывается, 2 - стоит).
        moving = None
        code = ctx.number("state")
        if code is not None and int(code) in (0, 1):
            moving = "Закрывается" if int(code) == 0 else "Открывается"

        lo = 0.0 if left is None else max(0.0, min(100.0, left)) / 100.0
        ro = 0.0 if right is None else max(0.0, min(100.0, right)) / 100.0
        sky = _window(ctx, "curtain")
        return {
            "moving": moving,
            "sky": sky,
            "drape": drapes(float(ctx.opt("inner_w") or 170), float(ctx.opt("chart_h") or 170),
                            lo, ro, float(ctx.opt("fs") or 1.0)) if sky else None,
            "low_x": 20 if sky else None,
            # Процент - строкой под названием: в углу над небом он терялся.
            "always_status": bool(sky) and float(ctx.opt("chart_h") or 170) >= 117,
            "left_open": lo,
            "right_open": ro,
            "known": avg is not None,
            "on": bool(avg and avg > 1),
            "status": moving or (("Открыто %d%%" % round(avg)) if avg is not None
                                 else "Нет данных"),
            "short": ("%d %%" % round(avg)) if avg is not None else "",
        }

    def zones(self, ctx, data):
        target = "left" if ctx.has("left") else "target"
        # Наполовину раздвинутая штора при нажатии закрывается: считаем её
        # открытой с середины хода, а не с первого процента. Иначе стоящая
        # на 5% штора «выключена», и нажатие едет её открывать - хотя
        # человек только что её оттуда и убрал.
        out = [Zone("open", "all", target,
                    {"on": str(ctx.opt("command_open", "100")),
                     "off": str(ctx.opt("command_close", "0"))},
                    state=(data.get("left_open") or 0.0) > 0.5),
               Zone("pad", "long", pad="curtain")]
        if ctx.has("stop"):
            out.append(Zone("stop", "button", "stop",
                            str(ctx.opt("command_stop", "2"))))
        return out

    def pad(self, ctx, data):
        out = {"pos": "%.3f" % (data.get("left_open") or 0.0)}
        if data.get("moving"):
            out["moving"] = data["moving"]
        return out


@tile("roller")
class Roller(Tile):
    """
    Рулонные и римские шторы. Полотно уходит вверх, а не в стороны.
    100 - поднято (открыто), 0 - опущено.

    Роли и команды те же, что у раздвижной: у привода это одна и та же
    служба WindowCovering, разница только в том, куда едет ткань. Поэтому
    тип отдельный, а не флаг у curtain: створок здесь нет в принципе, и
    channel_left с channel_right в этой геометрии нечему соответствовать.
    """

    roles = ("target", "goal", "state", "stop")
    fields = {"target": "channel", "goal": "channel_goal",
              "state": "channel_state", "stop": "channel_stop"}
    commands = {"target": "command_topic", "stop": "command_topic_stop"}

    def prepare(self, ctx):
        pos = ctx.number("target")
        if ctx.opt("inverted") and pos is not None:
            pos = 100 - pos

        # PositionState у HomeKit-совместимых приводов: 0 - к нулю,
        # 1 - к сотне, 2 - стоит. Для вертикального полотна это
        # «опускается» и «поднимается», а не «закрывается» и «открывается».
        moving = None
        code = ctx.number("state")
        if code is not None and int(code) in (0, 1):
            moving = "Опускается" if int(code) == 0 else "Поднимается"

        # Куда едет. Показываем только на ходу: у стоящего привода цель
        # равна положению, и пунктир по краю полотна был бы просто шумом.
        #
        # Нужно это там, где факт по дороге молчит. Медленный привод за
        # мостом публикует положение только в конце хода, и полминуты
        # плитка показывает старое - верно, но бесполезно. Цель же
        # приходит сразу, и по ней сразу видно, что команда принята.
        goal = ctx.number("goal") if moving else None
        if goal is not None and ctx.opt("inverted"):
            goal = 100 - goal
        if goal is not None and pos is not None and abs(goal - pos) < 1:
            goal = None

        status = moving or (("Открыто %d%%" % round(pos))
                            if pos is not None else "Нет данных")
        if moving and goal is not None:
            status = "%s до %d%%" % (moving, round(goal))

        op = 0.0 if pos is None else max(0.0, min(100.0, pos)) / 100.0
        sky = _window(ctx, "roller")
        return {
            "moving": moving,
            "sky": sky,
            "roman": roman(float(ctx.opt("inner_w") or 170), float(ctx.opt("chart_h") or 170),
                           op, float(ctx.opt("fs") or 1.0)) if sky else None,
            "low_x": 20 if sky else None,
            "always_status": bool(sky) and float(ctx.opt("chart_h") or 170) >= 117,
            "open": op,
            "goal": None if goal is None else max(0.0, min(100.0, goal)) / 100.0,
            "known": pos is not None,
            "on": bool(pos and pos > 1),
            "status": status,
            "short": ("%d %%" % round(pos)) if pos is not None else "",
        }

    def zones(self, ctx, data):
        # Половина хода как граница «открыто», по тем же соображениям,
        # что и у раздвижной: полотно, спущенное на четверть, человек
        # считает поднятым, и нажатие должно его опускать.
        out = [Zone("open", "all", "target",
                    {"on": str(ctx.opt("command_open", "100")),
                     "off": str(ctx.opt("command_close", "0"))},
                    state=(data.get("open") or 0.0) > 0.5),
               Zone("pad", "long", pad="roller")]
        if ctx.has("stop"):
            out.append(Zone("stop", "button", "stop",
                            str(ctx.opt("command_stop", "2"))))
        return out

    def pad(self, ctx, data):
        out = {"pos": "%.3f" % (data.get("open") or 0.0)}
        if data.get("moving"):
            out["moving"] = data["moving"]
        return out


@tile("thermostat")
class Thermostat(Tile):
    """
    Термостат: уставка крупно, фактическая температура рядом.

    Рамка загорается по каналу состояния, а не по режиму: включённый
    термостат, который сейчас не греет, не должен выглядеть активным.
    """

    roles = ("current", "target", "state", "mode")
    commands = {"target": "command_topic", "mode": "command_topic_mode"}

    def prepare(self, ctx):
        current = ctx.number("current")
        target = ctx.number("target")

        working = ctx.number("state")
        working = int(working) if working is not None else None
        mode = ctx.number("mode")
        mode = int(mode) if mode is not None else None

        if mode == 0:
            status = HEAT_MODE[0]
        elif working is not None:
            status = HEAT_NOW.get(working, HEAT_MODE.get(mode, ""))
        else:
            status = HEAT_MODE.get(mode, "")

        # насколько далеко до уставки - для полоски прогрева
        reach = None
        if current is not None and target is not None:
            reach = max(0.0, min(1.0, 1.0 - abs(target - current) / 3.0))

        dial = None
        if ctx.opt("dial", True):
            dial = build_dial(ctx.opt("inner_w") or 170,
                              ctx.opt("chart_h") or 170,
                              ctx.opt("scale_min", 14), ctx.opt("scale_max", 30),
                              current, target)

        # Знак градуса не должен участвовать в центрировании, иначе число
        # визуально уезжает влево. Считаем ширину числа и ставим «°» за ним.
        # Разрядность - из шага уставки, как у кнопок и пульта: при шаге
        # 0.1 уставка 22.5 на плитке была «22», а в пульте «22.5». Поле
        # digits по-прежнему сильнее.
        digits = ctx.opt("digits")
        if digits is None:
            digits = self._scale(ctx, None)[3]
        target_num = (_fmt(target, int(digits)) if target is not None else "--")
        # Кегль растёт вместе с плиткой, значит и смещение знака тоже.
        big = 30.0 * float(ctx.opt("fs", 1.0))
        deg_dx = text_width(target_num, big) / 2.0 + 1.0 * float(ctx.opt("fs", 1.0))

        # С 1.17 по умолчанию ручка со шкалой (look: classic - прежняя дуга).
        # Состояние красит дорожку: греет - оранжевая, включён и не греет -
        # синяя, выключен - серая.
        knob = None
        if dial is not None and ctx.opt("look", "knob") != "classic":
            state = "off" if mode == 0 else ("heat" if working == 1 else "idle")
            knob = knob_dial(ctx.opt("inner_w") or 170, ctx.opt("chart_h") or 170,
                             ctx.opt("scale_min", 14), ctx.opt("scale_max", 30),
                             current, target, state, int(digits))

        return {
            "dial": dial,
            "knob": knob,
            "target_num": target_num,
            # Число без округления: по нему считается шаг уставки.
            "target_value": target,
            "current_value": current,
            # Куда прибору идти: вниз - охлаждение, дуга синяя.
            "cooling": (current is not None and target is not None
                        and target < current),
            "deg_dx": round(deg_dx, 1),
            "deg_size": round(big * 0.60, 1),
            "on": working == 1,
            "enabled": mode != 0,
            "target": ("%s°" % target_num) if target is not None else "--",
            "current": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "short": ("%s°" % _fmt(current, 1)) if current is not None else "",
            "reach": reach,
            "status": status or "Нет данных",
            "always_status": True,
        }

    def _scale(self, ctx, data):
        """Шаг, пределы и разрядность - одни и те же для зон и для пульта."""
        step = ctx.step("target", 0.5)
        lo, hi = ctx.limits("target", 5, 35)
        digits = 0 if float(step).is_integer() else 1
        return step, lo, hi, digits

    def zones(self, ctx, data):
        target = data.get("target_value")
        if target is None:
            return []
        step, lo, hi, digits = self._scale(ctx, data)

        # Плюс и минус считаются здесь: в браузере не должно быть знаний ни
        # о шаге привода, ни о его пределах.
        def clamp(value):
            return max(lo, min(hi, round(value / step) * step))

        out = [Zone("down", "left", "target", _fmt(clamp(target - step), digits)),
               Zone("up", "right", "target", _fmt(clamp(target + step), digits)),
               Zone("pad", "center", pad="thermostat")]
        if ctx.has("mode"):
            # Включён ли термостат - не то же самое, что «греет сейчас»:
            # рамка горит по факту работы, а кнопка режима по режиму.
            out.append(Zone("mode", "button", "mode",
                            {"on": str(ctx.opt("command_mode_on", "1")),
                             "off": str(ctx.opt("command_mode_off", "0"))},
                            state=bool(data.get("enabled"))))
        return out

    def pad(self, ctx, data):
        target = data.get("target_value")
        if target is None:
            return {}
        step, lo, hi, digits = self._scale(ctx, data)
        out = {
            "target": _fmt(target, digits),
            "step": _fmt(step, digits),
            "min": _fmt(lo, digits),
            "max": _fmt(hi, digits),
            # Геометрия дуги нужна пульту: перетаскивание вдоль неё
            # переводится в градусы теми же числами, что рисуют шкалу.
            "dial_start": "%.1f" % DIAL_START,
            "dial_sweep": "%.1f" % DIAL_SWEEP,
            "scale_min": _fmt(float(ctx.opt("scale_min", 14)), 1),
            "scale_max": _fmt(float(ctx.opt("scale_max", 30)), 1),
            "status": data.get("status", ""),
            "live": "1" if data.get("on") else "0",
            "cooling": "1" if data.get("cooling") else "0",
        }
        # Фактическая температура - для тонкой засечки на пульте: видно,
        # куда прибору идти от текущего значения.
        if data.get("current_value") is not None:
            out["current"] = _fmt(data["current_value"], 1)
        return out
