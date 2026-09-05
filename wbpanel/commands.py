# -*- coding: utf-8 -*-
"""
Зоны -> команды.

Плитка описывает нажатия данными: «в этой области, в такой роли, записать
такое значение». Топик подставляется здесь - из привязки роли, которую
сделал источник. Плитка про MQTT по-прежнему ничего не знает, а страница
получает те же data-атрибуты, что и раньше.

Раньше это же самое строил `tile_command` в assemble.py - перебором по
типам плиток. Логика была описана дважды, и второй экземпляр отставал: у
шторы и термостата, заданных одной строкой `service:`, нажатие не работало
вовсе, потому что перебор искал поле `channel`, которого там нет.

Здесь же собираются прямоугольники зон: разметка нажатий - следствие тех
же данных, а не отдельная веточка в шаблоне.
"""

from .const import log


# Как зона называется в data-атрибутах. Ключ topic - куда положить адрес,
# on/off/value - куда положить записываемые значения, state - куда положить
# текущее состояние (по нему страница решает, что писать при нажатии).
#
# Таблица закрыта нарочно: незнакомое имя зоны - опечатка, и она должна
# быть слышна, а не превращаться в тихо неработающую кнопку.
ZONE_KEYS = {
    "toggle": {"topic": "topic", "on": "on", "off": "off", "state": "state"},
    "open":   {"topic": "topic", "on": "on", "off": "off", "state": "state"},
    "up":     {"topic": "topic", "value": "up"},
    "down":   {"topic": "topic", "value": "down"},
    "stop":   {"topic": "stop", "value": "stop_value"},
    "mode":   {"topic": "mode_topic", "on": "mode_on", "off": "mode_off",
               "state": "enabled"},
    "bright": {"topic": "bright"},
    "temp":   {"topic": "temp"},
    "expand": {"flag": "expand"},
}


# Доли плитки под областями нажатия. «all», «center», «long» и «pad»
# прямоугольника не требуют: по ним работает вся плитка целиком либо
# только пульт.
AREA_BOX = {
    "left":   (0.00, 0.00, 0.32, 1.00),
    "right":  (0.68, 0.00, 0.32, 1.00),
    "top":    (0.00, 0.00, 1.00, 0.32),
    "bottom": (0.00, 0.68, 1.00, 0.32),
}


# Есть ли в команде хоть что-то действующее. Один только пульт без единого
# записываемого топика - это плитка, которая открывается и ничего не умеет:
# для человека она выглядит сломанной кнопкой.
# Ключи, при которых плитка считается живой: без единого из них команда
# отбрасывается целиком, вместе со всеми data-атрибутами.
#
# «pad» здесь потому, что бывает плитка, у которой нажатие не пишет
# ничего, а только открывает пульт: телевизор именно такой. Раньше
# такую плитку спасал побочный ключ - у камеры это «cam» с адресом
# потока, - и чистый пульт молча выпадал, хотя build() его прямо
# предусматривает.
LIVE_KEYS = ("topic", "bright", "temp", "stop", "mode_topic", "expand", "href",
             "cam", "pad")


def build(zones, bound, data, extra=None, width=0, height=0):
    """
    Зоны плитки -> (команда, прямоугольники зон).

    Команда - плоский словарь, который дальше раскладывается в
    data-атрибуты. Прямоугольники - прозрачные области внутри плитки для
    тех зон, что занимают её часть, а не всю.
    """
    cmd = {}
    boxes = []
    for zone in zones or []:
        # Чистый пульт: ни роли, ни топика - только «по долгому нажатию
        # открыть вот такой пульт».
        if zone.pad and not zone.role and not zone.topic:
            cmd["pad"] = zone.pad
            continue

        keys = ZONE_KEYS.get(zone.name)
        if keys is None:
            log.warning("зона %r не описана в ZONE_KEYS - нажатие не заработает",
                        zone.name)
            continue

        if "flag" in keys:
            cmd[keys["flag"]] = True
            _box(boxes, zone, width, height)
            continue

        topic = zone.topic
        if topic is None and zone.role:
            b = bound.get(zone.role)
            topic = b.command if b else None
        if not topic:
            # Роль не пишется: канал только для чтения, либо у сырого
            # топика не задан адрес команды. Зоны просто нет.
            continue

        cmd[keys["topic"]] = topic
        write = zone.write
        if isinstance(write, dict):
            for side in ("on", "off"):
                if side in keys and write.get(side) is not None:
                    cmd[keys[side]] = str(write[side])
        elif write is not None and "value" in keys:
            cmd[keys["value"]] = str(write)

        if "state" in keys:
            on = zone.state if zone.state is not None else bool(data.get("on"))
            cmd[keys["state"]] = "1" if on else "0"

        if zone.pad:
            cmd["pad"] = zone.pad
        _box(boxes, zone, width, height)

    cmd.update(extra or {})
    if not any(key in cmd for key in LIVE_KEYS):
        return None, []
    return cmd, boxes


def _box(boxes, zone, width, height):
    frame = AREA_BOX.get(zone.area)
    if not frame or not width or not height:
        return
    fx, fy, fw, fh = frame
    boxes.append({"name": zone.name,
                  "x": round(width * fx, 1), "y": round(height * fy, 1),
                  "w": round(width * fw, 1), "h": round(height * fh, 1)})


# ==========================================================================
#  Команда -> data-атрибуты
# ==========================================================================

CMD_ATTRS = [
    ("i", "data-i"), ("topic", "data-topic"), ("on", "data-on"),
    ("off", "data-off"), ("state", "data-state"), ("expand", "data-expand"),
    ("pad", "data-pad"), ("bright", "data-bright"), ("bright_max", "data-bright-max"),
    ("pos", "data-pos"), ("stop", "data-stop"), ("stop_value", "data-stop-value"),
    ("moving", "data-moving"), ("target", "data-target"), ("up", "data-up"),
    ("down", "data-down"), ("step", "data-step"), ("min", "data-min"),
    ("max", "data-max"), ("mode_topic", "data-mode-topic"),
    ("mode_off", "data-mode-off"), ("mode_on", "data-mode-on"),
    ("enabled", "data-enabled"), ("dial_start", "data-dial-start"),
    ("dial_sweep", "data-dial-sweep"), ("scale_min", "data-scale-min"),
    ("scale_max", "data-scale-max"), ("current", "data-current"),
    ("status", "data-status"), ("live", "data-live"), ("cooling", "data-cooling"),
    ("level", "data-level"), ("temp", "data-temp"), ("temp_max", "data-temp-max"),
    ("temp_unit", "data-temp-unit"), ("temp_lo", "data-temp-lo"),
    ("temp_hi", "data-temp-hi"), ("cold", "data-cold"), ("warm", "data-warm"),
    ("dot_x", "data-dot-x"), ("dot_y", "data-dot-y"),
    ("ac_target", "data-ac-target"), ("ac_mode", "data-ac-mode"),
    ("ac_fan", "data-ac-fan"), ("ac_quiet", "data-ac-quiet"),
    ("ac_lo", "data-ac-lo"), ("ac_hi", "data-ac-hi"), ("ac_step", "data-ac-step"),
    ("ac_now", "data-ac-now"), ("ac_cur", "data-ac-cur"),
    ("ac_mode_now", "data-ac-mode-now"), ("ac_fan_now", "data-ac-fan-now"),
    ("ac_quiet_now", "data-ac-quiet-now"),
    ("ac_swing", "data-ac-swing"), ("ac_swing_h", "data-ac-swing-h"),
    ("ac_swing_now", "data-ac-swing-now"), ("ac_swing_h_now", "data-ac-swing-h-now"),
    ("href", "data-href"),
    ("cam", "data-cam"), ("cam_poll", "data-cam-poll"),
    ("cam_live", "data-cam-live"), ("cam_life", "data-cam-life"),
]


def cmd_attrs(tile):
    """Команда плитки -> готовая строка data-атрибутов для тега <g>."""
    cmd = tile.get("cmd")
    if not cmd:
        return ""
    parts = ['data-kind="%s"' % xml_escape(tile["kind"]),
             'data-title="%s"' % xml_escape(tile.get("title", ""))]
    for key, attr in CMD_ATTRS:
        value = cmd.get(key)
        if value in (None, "", False):
            continue
        parts.append('%s="%s"' % (attr, xml_escape("1" if value is True else str(value))))
    return " " + " ".join(parts)


def xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
