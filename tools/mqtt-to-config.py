#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Первый config.yaml по тому, что прямо сейчас публикуется в MQTT.

    python3 tools/mqtt-to-config.py > config.new.yaml
    python3 tools/mqtt-to-config.py --wait 15 --only wb-msw-v4_11,wb-mr6cu_1
    python3 tools/mqtt-to-config.py --all --host 192.168.*.*

На чистом контроллере вебморда ещё пустая, и wb-rooms-to-config.py брать
нечего - а брокер уже полон. Скрипт слушает /devices/+/controls/# столько
секунд, сколько сказано, и раскладывает найденное по панелям: одна панель
на устройство плюс сводная main со ссылками и графиками климата.

Откуда берётся тип плитки
-------------------------
Из meta канала, а не из догадок по имени:

    switch, запись разрешена      -> switch    (или dimmer/light, см. ниже)
    pushbutton                    -> scene
    temperature + rel_humidity    -> chart на устройство
    concentration (CO2)           -> отдельный chart с порогами
    прочие числовые, чтение       -> value
    text, rgb, alarm              -> пропускаются

Пары и тройки каналов собираются в одну плитку:

    K<N> + "Channel <N>"                          -> dimmer
    X + "X Brightness" + "X Temperature"          -> light

Чего скрипт не делает
---------------------
Не знает, какие каналы пишутся в историю (проверьте `python3 diag.py`,
раздел 5), не раскладывает устройства по комнатам - в MQTT этих сведений
нет - и не трогает службы Sprut.hub: у аксессуара десятки характеристик,
и разбирать их надо глазами через `tools/probe-roles.py --service`.

Ничего не меняет и никуда не пишет: только слушает MQTT и печатает YAML в
стандартный вывод. Готовый файл сверьте и положите как config.yaml.
"""

import argparse
import json
import re
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Нет paho-mqtt:  apt install python3-paho-mqtt")


# Служебные устройства контроллера: на панели им делать нечего.
# Снимается ключом --all.
SKIP_DEVICES = ("system", "hwmon", "power_status", "network", "metrics",
                "buzzer", "wb-hwmon")

# Типы каналов, которые панель рисовать не умеет.
SKIP_TYPES = ("text", "rgb", "alarm")

# Числовые датчики: единицы по умолчанию, иконка, знаков после запятой.
# Единицы нужны только графику - плитка value берёт их из meta сама.
SENSORS = {
    "temperature":          ("°C",  "thermo", 1),
    "rel_humidity":         ("%",   "drop",   0),
    "concentration":        ("ppm", "co2",    0),
    "lux":                  ("lx",  "sun",    0),
    "sound_level":          ("dB",  "wind",   0),
    "atmospheric_pressure": ("",    "leaf",   0),
    "voltage":              ("В",   "power",  1),
    "current":              ("А",   "power",  2),
    "power":                ("Вт",  "power",  0),
    "power_consumption":    ("кВт·ч", "power", 1),
    "resistance":           ("",    "power",  0),
    "value":                ("",    "",       1),
}

# Иконка выключателя по названию канала. Порядок важен: первое совпадение.
ICONS = (
    ("лент|strip",                 "strip"),
    ("штор|жалюзи|curtain|blind",  "curtain"),
    ("свет|лампа|люстр|бра|light", "bulb"),
    ("вент|fan|вытяж",             "fan"),
    ("клапан|вод|valve|drop",      "drop"),
    ("розет|socket",               "socket"),
    ("тепл|подогрев|heat",         "heat"),
    ("окн|window",                 "window"),
    ("дверь|door",                 "door"),
)


# --------------------------------------------------------------------- MQTT

class Bus(object):
    """Слепок брокера: значения каналов и метаданные, как их видит демон."""

    def __init__(self, host, port):
        self.values = {}        # "устройство/канал" -> строка
        self.meta = {}          # "устройство/канал" -> dict
        self.devices = {}       # "устройство" -> dict (meta устройства)
        self.order = {}         # "устройство" -> [канал, ...] в порядке прихода
        self.count = 0
        self.connected = False
        self.cli = mqtt.Client()
        self.cli.on_connect = self._on_connect
        self.cli.on_message = self._on_message
        try:
            self.cli.connect(host, port, 30)
        except Exception as exc:                       # noqa: BLE001
            sys.exit("Не подключиться к брокеру %s:%d — %s" % (host, port, exc))
        self.cli.loop_start()
        deadline = time.time() + 10
        while not self.connected and time.time() < deadline:
            time.sleep(0.1)
        if not self.connected:
            sys.exit("Брокер %s:%d не ответил за 10 с" % (host, port))

    def _on_connect(self, cli, userdata, flags, rc):
        if rc != 0:
            sys.exit("Брокер отказал в подключении, код %s" % rc)
        cli.subscribe("/devices/#")
        self.connected = True

    def _on_message(self, cli, userdata, msg):
        topic = msg.topic
        if not topic.startswith("/devices/"):
            return
        rest = topic[len("/devices/"):]
        payload = msg.payload.decode("utf-8", "replace")
        self.count += 1

        if "/controls/" not in rest:
            # meta самого устройства: имя комнаты или прибора
            if "/meta" not in rest:
                return
            dev, tail = rest.split("/meta", 1)
            store = self.devices.setdefault(dev, {})
            if tail == "":
                self._merge_json(store, payload)
            elif tail.startswith("/"):
                store[tail[1:]] = payload
            return

        dev, ctl = rest.split("/controls/", 1)

        # Имя канала само может содержать косую черту (Sprut.hub), поэтому
        # meta отрезаем только с конца, а не ищем в середине.
        if ctl.endswith("/meta"):
            self._merge_json(self.meta.setdefault(
                "%s/%s" % (dev, ctl[:-len("/meta")]), {}), payload)
            return
        if "/meta/" in ctl:
            name, key = ctl.rsplit("/meta/", 1)
            self.meta.setdefault("%s/%s" % (dev, name), {})[key] = payload
            return

        self.values["%s/%s" % (dev, ctl)] = payload
        seen = self.order.setdefault(dev, [])
        if ctl not in seen:
            seen.append(ctl)

    @staticmethod
    def _merge_json(store, payload):
        try:
            doc = json.loads(payload)
        except ValueError:
            return
        if isinstance(doc, dict):
            store.update(doc)

    def settle(self, seconds):
        sys.stderr.write("Слушаю MQTT %d с…\n" % seconds)
        time.sleep(seconds)
        self.cli.loop_stop()
        if not self.values:
            sys.exit("Ни одного канала. Брокер точно работает и это WB?")


# ----------------------------------------------------------------- разбор

def text(value):
    """meta.title бывает строкой, а бывает {'ru': …, 'en': …}."""
    if isinstance(value, dict):
        for key in ("ru", "en"):
            if value.get(key):
                return str(value[key])
        for item in value.values():
            if item:
                return str(item)
        return ""
    return "" if value is None else str(value)


def truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def slug(name):
    table = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
             "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
             "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
             "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
             "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    out = "".join(table.get(ch, ch) for ch in (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", out).strip("-") or "panel"


def q(value):
    return '"%s"' % str(value).replace('"', '\\"')


def icon_for(title, control):
    probe = ("%s %s" % (title, control)).lower()
    for pattern, name in ICONS:
        if re.search(pattern, probe):
            return name
    return "power"


class Channel(object):
    """Один канал устройства вместе с тем, что он о себе рассказал."""

    def __init__(self, device, control, meta):
        self.device = device
        self.control = control
        self.meta = meta or {}
        self.type = text(self.meta.get("type")) or "value"
        self.readonly = truthy(self.meta.get("readonly"))
        self.title = text(self.meta.get("title")) or control

    @property
    def path(self):
        return "%s/%s" % (self.device, self.control)

    @property
    def writable(self):
        return not self.readonly


def collect(bus, skip, only, keep_all):
    """-> [(устройство, заголовок, [Channel, ...]), ...]"""
    out = []
    for device in sorted(bus.order):
        if only and device not in only:
            continue
        if not keep_all:
            if device in skip or device.split("_")[0] in skip:
                continue
            # Службы Sprut.hub: канал вида accessories/288/13/16 разбирать
            # вслепую бессмысленно - у аксессуара их десятки.
            if any("/" in ctl for ctl in bus.order[device]):
                sys.stderr.write("пропуск %s — похоже на службы Sprut.hub\n"
                                 % device)
                continue

        channels = []
        for control in bus.order[device]:
            chan = Channel(device, control, bus.meta.get("%s/%s" % (device, control)))
            if chan.type in SKIP_TYPES:
                continue
            channels.append(chan)
        if not channels:
            continue

        # meta.order расставляет каналы так же, как их показывает вебморда
        def rank(chan, seq=bus.order[device]):
            try:
                return (int(chan.meta.get("order")), 0)
            except (TypeError, ValueError):
                return (10 ** 6, seq.index(chan.control))

        channels.sort(key=rank)
        title = text((bus.devices.get(device) or {}).get("name")) or device
        out.append((device, title, channels))
    return out


# ------------------------------------------------------------------- YAML

HEAD = """# Сгенерировано mqtt-to-config.py: %(when)s
# Источник — каналы, которые публиковались в MQTT на момент запуска.
#
# ЧТО ПРОВЕРИТЬ РУКАМИ:
#   * названия плиток: взяты из meta канала, они технические;
#   * лишние плитки — удалить, панель не каталог;
#   * нормально замкнутые реле — добавить invert: true;
#   * ленты: temp_unit — percent или kelvin, зависит от настройки канала;
#   * графики: канал должен писаться в историю, см. `python3 diag.py`.

theme: light           # light | dark

# Нажатия на плитки. Включите, когда убедитесь, что нарисовано верно.
interactive: false

mqtt:
  host: %(host)s
  port: %(port)d
  client_id: wb-svg-panel

http:
  host: 127.0.0.1
  port: 8088
  refresh: 10

history:
  source: auto         # auto | sqlite | rpc
  cache_ttl: 60

panels:
"""


def chart_lines(title, series, span, indent="      "):
    lines = [indent + "- type: chart",
             indent + "  title: %s" % q(title),
             indent + "  w: 2",
             indent + "  range: %s" % span,
             indent + "  points: 90",
             indent + "  series:"]
    for path, unit, color, digits, thresholds in series:
        lines.append(indent + "    - channel: %s" % q(path))
        if unit:
            lines.append(indent + "      unit: %s" % q(unit))
        lines.append(indent + "      color: %s" % q(color))
        lines.append(indent + "      digits: %d" % digits)
        if thresholds:
            lines.append(indent + "      thresholds:")
            for above, tcolor in thresholds:
                lines.append(indent + "        - {above: %d, color: %s}"
                             % (above, q(tcolor)))
    return lines


def climate_charts(device, title, by_type, span):
    """Графики устройства: климат одной плиткой, CO2 отдельной."""
    charts = []
    series = []
    for kind, color in (("temperature", "#E08A2D"), ("rel_humidity", "#3B93C4")):
        chan = by_type.get(kind)
        if chan:
            unit, _, digits = SENSORS[kind]
            series.append((chan.path, text(chan.meta.get("units")) or unit,
                           color, digits, []))
    if series:
        charts.append(chart_lines(title, series, span))

    co2 = by_type.get("concentration")
    if co2:
        charts.append(chart_lines(
            "%s · воздух" % title,
            [(co2.path, text(co2.meta.get("units")) or "ppm", "#4E9A6B", 0,
              [(1000, "#D9A227"), (1500, "#C4553B")])],
            span))
    return charts


def device_tiles(channels, used):
    """Плитки устройства, кроме графиков. used пополняется занятыми каналами."""
    lines = []
    by_name = dict((c.control, c) for c in channels)

    # лента: выключатель + яркость + цветовая температура
    for chan in channels:
        if chan.type != "switch" or not chan.writable:
            continue
        bright = by_name.get(chan.control + " Brightness")
        temp = by_name.get(chan.control + " Temperature")
        if bright and temp:
            used.update((chan.control, bright.control, temp.control))
            lines += [
                "      - type: light",
                "        title: %s" % q(chan.title),
                "        w: 0.5",
                "        h: 1",
                "        channel_switch: %s" % q(chan.path),
                "        channel_brightness: %s" % q(bright.path),
                "        channel_temp: %s" % q(temp.path),
                "        temp_unit: percent   # ПРОВЕРЬТЕ: percent или kelvin",
                "        temp_min: 2700",
                "        temp_max: 6500",
            ]

    # диммер: K<N> включает, "Channel <N>" держит яркость (wb-mdm3)
    for chan in channels:
        if chan.control in used or chan.type != "switch" or not chan.writable:
            continue
        match = re.match(r"^K(\d+)$", chan.control)
        if not match:
            continue
        bright = by_name.get("Channel %s" % match.group(1))
        if bright and bright.type == "range":
            used.update((chan.control, bright.control))
            lines += [
                "      - type: dimmer",
                "        title: %s" % q(chan.title),
                "        w: 0.5",
                "        h: 1",
                "        channel: %s" % q(chan.path),
                "        channel_brightness: %s" % q(bright.path),
            ]

    for chan in channels:
        if chan.control in used:
            continue
        used.add(chan.control)

        if chan.type == "pushbutton":
            lines += ["      - type: scene",
                      "        title: %s" % q(chan.title),
                      "        channel: %s" % q(chan.path)]
            continue

        if chan.type == "switch":
            lines += ["      - type: switch",
                      "        title: %s" % q(chan.title),
                      "        icon: %s" % icon_for(chan.title, chan.control),
                      "        channel: %s" % q(chan.path)]
            if chan.readonly:
                lines.append("        # канал только для чтения — нажатие не сработает")
            continue

        # всё остальное числовое — маленькая плитка со значением
        unit, icon, digits = SENSORS.get(chan.type, ("", "", 1))
        lines += ["      - type: value",
                  "        title: %s" % q(chan.title),
                  "        channel: %s" % q(chan.path),
                  "        w: 0.5",
                  "        h: 0.5",
                  "        digits: %d" % digits]
        if icon:
            lines.append("        icon: %s" % icon)
    return lines


def convert(devices, host, port, span):
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = [(HEAD % {"when": stamp, "host": host, "port": port}).rstrip("\n")]

    panels = []      # (имя панели, заголовок, строки графиков, строки плиток)
    names = set()
    for device, title, channels in devices:
        by_type = {}
        for chan in channels:
            by_type.setdefault(chan.type, chan)

        name, base, n = slug(device), slug(device), 2
        while name in names:
            name, n = "%s-%d" % (base, n), n + 1
        names.add(name)

        charts = climate_charts(device, title, by_type, span)
        used = set(c.control for c in
                   (by_type.get("temperature"), by_type.get("rel_humidity"),
                    by_type.get("concentration")) if c)
        tiles = device_tiles(channels, used)
        panels.append((name, title, charts, tiles))

    # --- сводная панель ----------------------------------------------------
    lines += ["", "  main:", '    title: "Дом"', "    cols: 4", "    tiles:"]
    for name, title, charts, tiles in panels:
        for chart in charts:
            lines += chart
    for name, title, charts, tiles in panels:
        lines += ["      - type: link",
                  "        title: %s" % q(title),
                  "        panel: %s" % name,
                  "        icon: door"]

    # --- панель на устройство ----------------------------------------------
    for name, title, charts, tiles in panels:
        body = []
        for chart in charts:
            body += chart
        body += tiles
        if not body:
            continue
        lines += ["", "  %s:" % name,
                  "    title: %s" % q(title),
                  "    hidden: true",
                  "    cols: 4",
                  "    tiles:"] + body

    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- запуск

def main():
    parser = argparse.ArgumentParser(
        description="config.yaml по каналам MQTT",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--host", default="localhost", help="брокер MQTT")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--wait", type=int, default=8,
                        help="сколько секунд слушать (по умолчанию 8)")
    parser.add_argument("--only", default="",
                        help="только эти устройства, через запятую")
    parser.add_argument("--skip", default="",
                        help="дополнительно пропустить, через запятую")
    parser.add_argument("--all", action="store_true",
                        help="не пропускать служебные устройства и Sprut.hub")
    parser.add_argument("--range", dest="span", default="12h",
                        help="глубина графиков: 30m | 6h | 12h | 3d")
    args = parser.parse_args()

    bus = Bus(args.host, args.port)
    bus.settle(args.wait)

    only = set(x.strip() for x in args.only.split(",") if x.strip())
    skip = set(SKIP_DEVICES) | set(x.strip() for x in args.skip.split(",") if x.strip())

    devices = collect(bus, skip, only, args.all)
    if not devices:
        sys.exit("Каналы есть, но все отсеяны. Попробуйте --all или --only.")

    sys.stderr.write("каналов: %d, устройств на панели: %d\n"
                     % (len(bus.values), len(devices)))
    for device, title, channels in devices:
        sys.stderr.write("    %-24s %-20s %d каналов\n"
                         % (device, title[:20], len(channels)))

    sys.stdout.write(convert(devices, args.host, args.port, args.span))
    sys.stderr.write("\nГотово. Сверьте файл и положите как config.yaml.\n")


if __name__ == "__main__":
    main()
