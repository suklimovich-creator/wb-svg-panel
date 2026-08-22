#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Что панель поняла про ваши устройства.

    python3 tools/probe-roles.py                    все плитки из config.yaml
    python3 tools/probe-roles.py bedroom            одна панель
    python3 tools/probe-roles.py --service 'Sprut.hub-XXXX_1/accessories/288/13'

Показывает, какая роль к какому топику привязалась, что устройство
рассказало о себе само (единицы, шаг, пределы, права записи) и куда пойдёт
команда. Автоопределение ошибается молча - это способ увидеть его глазами.

Ничего не пишет: только слушает MQTT.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Нет paho-mqtt:  apt install python3-paho-mqtt")

from wbpanel.config import Config           # noqa: E402
from wbpanel.const import CONFIG_PATH       # noqa: E402
from wbpanel.registry import REGISTRY       # noqa: E402
from wbpanel.roles import ROLES             # noqa: E402
from wbpanel.sources import bind, pick_source   # noqa: E402
from wbpanel.state import WbState           # noqa: E402
import wbpanel.tiles_basic                  # noqa: E402,F401  (регистрирует типы)


def listen(host, port, seconds):
    """Собираем состояние и метаданные так же, как это делает демон."""
    state = WbState()
    cli = mqtt.Client()
    cli.on_message = lambda c, u, m: state.on_message(
        m.topic, m.payload.decode("utf-8", "replace"))

    def on_connect(c, u, flags, rc):
        if rc != 0:
            sys.exit("Брокер отказал в подключении, код %s" % rc)
        c.subscribe("#")

    cli.on_connect = on_connect
    cli.connect(host, port, 30)
    cli.loop_start()
    print("Слушаю MQTT %d с…" % seconds)
    time.sleep(seconds)
    cli.loop_stop()
    if not state.values:
        sys.exit("Ни одного сообщения. Брокер точно работает?")
    print("каналов: %d, с метаданными: %d\n" % (len(state.values), len(state.meta)))
    return state


def show_tile(conf, state, where=""):
    kind = conf.get("type", "value")
    cls = REGISTRY.get(kind)
    title = conf.get("title") or "(без названия)"
    head = "%s  [%s]" % (title, kind)
    if where:
        head += "   %s" % where
    print(head)

    if cls is None:
        print("    тип ещё не переведён на роли — пропускаю\n")
        return

    obj = cls()
    roles = obj.schema()
    bound = bind(conf, roles, state)
    print("    источник: %s" % pick_source(conf).name)

    for role in roles:
        b = bound.get(role.name)
        if not b:
            mark = "  ОБЯЗАТЕЛЬНА" if role.required else ""
            print("    %-9s —%s" % (role.name, mark))
            continue
        bits = []
        if b.units:
            bits.append("ед. %s" % b.units)
        if b.step:
            bits.append("шаг %g" % b.step)
        lo, hi = b.limits
        if lo is not None or hi is not None:
            bits.append("%s..%s" % (lo, hi))
        if b.title():
            bits.append("«%s»" % b.title())
        if b.auto:
            bits.append("разобрано по типу %s" % b.meta.get("type"))
        print("    %-9s %s" % (role.name, b.topic))
        print("    %-9s %s%s" % ("", "запись -> " + b.command if b.command
                                 else "только чтение",
                                 ("   " + " · ".join(bits)) if bits else ""))
    problem = obj.check(conf, bound)
    if problem:
        print("    ПРОБЛЕМА: %s" % problem)
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("panel", nargs="?", help="только одна панель")
    ap.add_argument("--service", help="разобрать одну службу Sprut.hub")
    ap.add_argument("--type", default="thermostat",
                    help="каким типом плитки её пробовать (с --service)")
    ap.add_argument("--config", default=CONFIG_PATH)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--wait", type=int, default=6)
    args = ap.parse_args()

    state = listen(args.host, args.port, args.wait)

    if args.service:
        show_tile({"type": args.type, "service": args.service,
                   "title": "проба службы"}, state)
        return

    config = Config(args.config)
    for name in sorted(config.panels):
        if args.panel and name != args.panel:
            continue
        panel = config.panels[name] or {}
        for conf in (panel.get("tiles") or []):
            if isinstance(conf, dict):
                show_tile(conf, state, where="панель %s" % name)


if __name__ == "__main__":
    main()
