#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собирает config.yaml из настроек штатного веб-интерфейса Wiren Board.

Откуда берутся данные
---------------------
Панели, комнаты и виджеты вебморды хранятся на контроллере в одном файле:

    /mnt/data/etc/wb-webui.conf

Скрипт читает его по умолчанию, поэтому на контроллере достаточно:

    python3 wb-rooms-to-config.py > config.new.yaml

Можно передать путь явно — например выгрузку, скачанную на компьютер:

    python3 wb-rooms-to-config.py rooms.json > config.new.yaml

Понимает три формата:
  * весь wb-webui.conf (ключи "widgets" и, если есть, "dashboards");
  * JSON-массив виджетов;
  * NDJSON — объекты с "cells" подряд, как в выгрузке из вебморды.

Датчики климата
---------------
В настройках вебморды нет сведений о том, какой датчик стоит в какой
комнате - в отличие от лент, у которых привязка задана на уровне канала
и берётся из файла автоматически. Поэтому датчики указываются явно:

    python3 wb-rooms-to-config.py \
        --climate "Гостиная=wb-msw-v4_149" \
        --climate "Детская=wb-msw-v4_174" \
        --climate "Спальня=wb-msw-v4_232" \
        --range 12h > config.new.yaml

В каждую названную комнату добавится плитка chart с температурой и
влажностью этого устройства. Если комнаты с таким именем в вебморде нет
(например «Спальня»), для неё создаётся отдельная панель.

Что делает
----------
  * дашборд (или отдельный виджет, если дашбордов нет) -> панель;
  * switch -> плитка switch; для wb-mdm3 - плитка dimmer, канал яркости
    подставляется как "<устройство>/Channel <N>" по номеру K<N>;
  * тройка CCT (выключатель + "… Brightness" + "… Temperature") -> одна
    плитка light: в вебморде это три отдельные ячейки;
  * прочие range/value -> плитка value;
  * "Бра (151-K4)" -> "Бра": адрес модуля из названия убирается.

Графики скрипт не создаёт: он не знает, какие каналы пишутся в историю.
Добавьте их вручную, сверившись с `python3 diag.py` (раздел 5).
Скрипт ничего не меняет — только печатает YAML в стандартный вывод.
"""
import json
import os
import re
import sys

DEFAULT_PATHS = ["/mnt/data/etc/wb-webui.conf", "/etc/wb-webui.conf"]
DEC = json.JSONDecoder()


def load(path):
    """-> список «комнат»: [{'name': ..., 'cells': [...]}, ...]"""
    raw = open(path, encoding="utf-8-sig").read()

    # 1) цельный JSON: либо весь wb-webui.conf, либо массив виджетов
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = None

    if isinstance(doc, dict):
        widgets = doc.get("widgets") or []
        by_id = {w.get("id"): w for w in widgets if isinstance(w, dict)}
        dashboards = doc.get("dashboards") or []
        rooms = []
        for dash in dashboards:
            if not isinstance(dash, dict):
                continue
            cells = []
            for wid in dash.get("widgets", []) or []:
                widget = by_id.get(wid)
                if widget:
                    cells.extend(widget.get("cells", []) or [])
            if cells:
                rooms.append({"name": dash.get("name") or dash.get("id"),
                              "cells": cells})
        if rooms:
            return rooms
        # дашбордов нет — берём виджеты как есть
        return [{"name": w.get("name") or w.get("id"), "cells": w.get("cells", [])}
                for w in widgets if w.get("cells")]

    if isinstance(doc, list):
        return [o for o in doc if isinstance(o, dict) and o.get("cells")]

    # 2) NDJSON: объекты подряд, возможно с пустыми строками между ними
    out, i = [], 0
    while i < len(raw):
        while i < len(raw) and raw[i] in " \r\n\t":
            i += 1
        if i >= len(raw):
            break
        obj, i = DEC.raw_decode(raw, i)
        if isinstance(obj, dict) and obj.get("cells"):
            out.append(obj)
    return out


def clean(name):
    """'Бра (151-K4)' -> 'Бра'"""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()


def slug(name):
    table = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
             "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
             "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
             "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
             "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    out = "".join(table.get(ch, ch) for ch in (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", out).strip("-") or "panel"


def q(text):
    return '"%s"' % str(text).replace('"', '\\"')


HEAD = """# Сгенерировано wb-rooms-to-config.py из настроек веб-интерфейса.
# Проверьте: цветовые температуры лент (percent или kelvin),
# размеры плиток (w/h), порядок и названия. Графики добавьте вручную.

theme: light           # light | dark

mqtt:
  host: localhost
  port: 1883
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


def chart_block(device, title, span, temp="Temperature", hum="Humidity"):
    return [
        "      - type: chart",
        "        title: %s" % q(title),
        '        subtitle: "температура и влажность"',
        "        w: 2",
        "        range: %s" % span,
        "        points: 90",
        "        series:",
        "          - channel: %s" % q("%s/%s" % (device, temp)),
        '            unit: "°C"',
        '            color: "#E08A2D"',
        "          - channel: %s" % q("%s/%s" % (device, hum)),
        '            unit: "%"',
        '            color: "#3B93C4"',
        "            digits: 0",
    ]


def convert(rooms, climate=None, span="12h"):
    climate = dict(climate or {})
    lines = [HEAD.rstrip("\n")]
    used = set()

    # комнаты, которых нет в вебморде, но для которых задан датчик
    known = set((r.get("name") or "").strip().lower() for r in rooms)
    rooms = list(rooms)
    for rname in climate:
        if rname.strip().lower() not in known:
            rooms.append({"name": rname, "cells": []})

    for room in rooms:
        cells = room.get("cells", []) or []
        by_id = {c.get("id"): c for c in cells if isinstance(c, dict)}

        lights, consumed = [], set()
        for cell in cells:
            cid = cell.get("id") or ""
            if cell.get("type") != "switch":
                continue
            bright, temp = cid + " Brightness", cid + " Temperature"
            if bright in by_id and temp in by_id:
                lights.append((cell, bright, temp))
                consumed.update((cid, bright, temp))

        name, base, n = slug(room.get("name")), slug(room.get("name")), 2
        while name in used:
            name, n = "%s-%d" % (base, n), n + 1
        used.add(name)

        lines += ["", "  %s:" % name,
                  "    title: %s" % q(room.get("name") or name),
                  "    cols: 4", "    tiles:"]

        # климат первым: он задаёт тон панели
        device = None
        for rname, dev in climate.items():
            if rname.strip().lower() == (room.get("name") or "").strip().lower():
                device = dev
                break
        if device:
            lines += chart_block(device, room.get("name") or name, span)

        for cell, bright, temp in lights:
            lines += [
                "      - type: light",
                "        title: %s" % q(clean(cell.get("name")) or cell["id"]),
                "        channel_switch: %s" % q(cell["id"]),
                "        channel_brightness: %s" % q(bright),
                "        channel_temp: %s" % q(temp),
                "        temp_unit: percent   # ПРОВЕРЬТЕ: percent или kelvin",
                "        temp_min: 2700",
                "        temp_max: 6500",
            ]

        for cell in cells:
            cid = cell.get("id")
            if cid in consumed or not cid:
                continue
            title = clean(cell.get("name")) or cid
            dim = re.match(r"^(wb-mdm3_\d+)/K(\d)$", cid or "")
            if dim and cell.get("type") == "switch":
                # диммер: K<N> - выключатель, "Channel <N>" - яркость
                lines += [
                    "      - type: dimmer",
                    "        title: %s" % q(title),
                    "        channel: %s" % q(cid),
                    "        channel_brightness: %s"
                    % q("%s/Channel %s" % (dim.group(1), dim.group(2))),
                ]
            else:
                kind = "switch" if cell.get("type") == "switch" else "value"
                lines += [
                    "      - type: %s" % kind,
                    "        title: %s" % q(title),
                    "        channel: %s" % q(cid),
                ]
    return "\n".join(lines) + "\n"


def parse_args(argv):
    path, climate, span = None, {}, "12h"
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--climate":
            i += 1
            pair = argv[i] if i < len(argv) else ""
            if "=" not in pair:
                sys.exit("--climate ждёт «Комната=устройство», получено: %r" % pair)
            room, dev = pair.split("=", 1)
            climate[room.strip()] = dev.strip()
        elif arg.startswith("--climate="):
            pair = arg.split("=", 1)[1]
            if "=" not in pair:
                sys.exit("--climate ждёт «Комната=устройство», получено: %r" % pair)
            room, dev = pair.split("=", 1)
            climate[room.strip()] = dev.strip()
        elif arg == "--range":
            i += 1
            span = argv[i] if i < len(argv) else "12h"
        elif arg.startswith("--range="):
            span = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            sys.exit(__doc__)
        elif arg.startswith("-"):
            sys.exit("неизвестный ключ %r" % arg)
        else:
            path = arg
        i += 1
    return path, climate, span


if __name__ == "__main__":
    path, climate, span = parse_args(sys.argv[1:])
    if path is None:
        path = next((p for p in DEFAULT_PATHS if os.path.exists(p)), None)
        if not path:
            sys.exit("Не нашёл %s. Укажите файл явно:\n"
                     "    python3 %s <файл>\n"
                     % (" и ".join(DEFAULT_PATHS), os.path.basename(sys.argv[0])))
    rooms = load(path)
    if not rooms:
        sys.exit("В %s не нашлось ни одной комнаты с ячейками." % path)
    sys.stderr.write("Источник: %s — комнат: %d\n" % (path, len(rooms)))
    for rname, dev in climate.items():
        sys.stderr.write("Климат: %s -> %s\n" % (rname, dev))
    sys.stdout.write(convert(rooms, climate, span))
