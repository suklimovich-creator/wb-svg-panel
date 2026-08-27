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

from .geometry import _fmt, field_color, text_width
from .registry import Tile, Zone, tile
from .tiles import DIAL_START, DIAL_SWEEP, HEAT_MODE, HEAT_NOW, build_dial
from .state import to_float


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

        return {
            "on": on,
            "dot_x": dot_x,
            "dot_y": dot_y,
            "has_dot": dot_x is not None and dot_y is not None,
            "cold": ctx.opt("cold_color") or field_color(k_max),
            "warm": ctx.opt("warm_color") or field_color(k_min),
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

        return {
            "on": on,
            "fill": 0.0 if (not on or brightness is None) else brightness / 100.0,
            "color": ctx.opt("color", "#E0A050"),
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
        return {"bright_max": str(to_float(ctx.meta("bright", "max"), 100.0) or 100.0),
                "level": "%.3f" % (data.get("fill") or 0.0)}


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

        return {
            "moving": moving,
            "left_open": 0.0 if left is None else max(0.0, min(100.0, left)) / 100.0,
            "right_open": 0.0 if right is None else max(0.0, min(100.0, right)) / 100.0,
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
        target_num = (_fmt(target, int(ctx.opt("digits", 0)))
                      if target is not None else "--")
        # Кегль растёт вместе с плиткой, значит и смещение знака тоже.
        big = 30.0 * float(ctx.opt("fs", 1.0))
        deg_dx = text_width(target_num, big) / 2.0 + 1.0 * float(ctx.opt("fs", 1.0))

        return {
            "dial": dial,
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
