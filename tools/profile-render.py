#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Куда уходит время отрисовки панели.

    python3 tools/profile-render.py            # панель main, 5 прогонов
    python3 tools/profile-render.py livingroom 10
    python3 tools/profile-render.py main 5 --lines 40

Поднимает те же объекты, что и служба: конфиг, jinja, состояние из MQTT,
историю. Своим client_id, поэтому запускать можно на живом контроллере -
работающей панели это не помешает. HTTP не поднимается вовсе: меряется
именно отрисовка, без waitress и сети.

Перед замером ждёт, пока из MQTT приедут retained-значения: на пустом
состоянии панель рисуется быстрее и профиль получается ложным.
"""

import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jinja2 import Environment, FileSystemLoader          # noqa: E402

from wbpanel import assemble, cameras, web                # noqa: E402
from wbpanel.config import Config                         # noqa: E402
from wbpanel.const import CONFIG_PATH, DB_PATH, TEMPLATE_DIRS  # noqa: E402
from wbpanel.history import History                       # noqa: E402
from wbpanel.state import MqttRpc, make_client, state     # noqa: E402


def setup(warmup=6.0):
    config = Config(CONFIG_PATH)
    assemble.config = config
    cameras.config = config
    state.set_watched(config.used_channels())

    web.config = config
    web.jinja = Environment(
        loader=FileSystemLoader(TEMPLATE_DIRS),
        autoescape=True,
        # В службе стоит True, чтобы правки шаблонов подхватывались без
        # рестарта. Здесь тоже True - иначе профиль соврёт в нашу пользу.
        auto_reload=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    mqtt_conf = config.get("mqtt", {}) or {}
    client_id = "wb-svg-panel-profile-%d" % os.getpid()
    client = make_client(client_id)
    if mqtt_conf.get("username"):
        client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))
    rpc = MqttRpc(client, client_id)

    hconf = config.get("history", {}) or {}
    web.history = History(rpc,
                          db_path=hconf.get("db_path", DB_PATH),
                          ttl=int(hconf.get("cache_ttl", 60)),
                          prefer=hconf.get("source", "auto"))

    def on_connect(cli, userdata, flags, rc, *args):
        state.connected = True
        cli.subscribe([("/devices/+/controls/+", 0),
                       ("/devices/+/controls/+/meta", 0),
                       ("/devices/+/controls/+/meta/+", 0)])

    def on_message(cli, userdata, msg):
        state.on_message(msg.topic, msg.payload.decode("utf-8", "replace"))

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(mqtt_conf.get("host", "localhost"),
                         int(mqtt_conf.get("port", 1883)), 60)
    client.loop_start()

    print("жду %.0f с, пока приедут retained-значения…" % warmup)
    time.sleep(warmup)
    print("каналов в состоянии: %d" % len(getattr(state, "values", {}) or {}))
    return client


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name = args[0] if args else "main"
    runs = int(args[1]) if len(args) > 1 else 5
    lines = 30
    if "--lines" in sys.argv:
        lines = int(sys.argv[sys.argv.index("--lines") + 1])

    client = setup()

    # render_panel читает параметры из request (число колонок, свёрнутые
    # разделы), поэтому нужен контекст запроса. Заводим поддельный: HTTP
    # при этом не поднимается, меряется только отрисовка.
    with web.app.test_request_context("/%s.svg" % name):
        # Прогрев: первый вызов тянет историю и компилирует шаблоны, и
        # его время не имеет отношения к обычной работе.
        print("прогрев…")
        web.render_panel(name)

        print("замер: %d прогонов панели %r" % (runs, name))
        started = time.time()
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(runs):
            web.render_panel(name)
        profiler.disable()
        total = time.time() - started
    print("итого %.3f с, в среднем %.3f с на отрисовку\n"
          % (total, total / runs))

    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")
    print("=== по суммарному времени (кто кого зовёт) ===")
    stats.print_stats(lines)
    stats.sort_stats("tottime")
    print("=== по собственному времени (где реально считает) ===")
    stats.print_stats(lines)

    client.loop_stop()


if __name__ == "__main__":
    main()
