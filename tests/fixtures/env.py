# -*- coding: utf-8 -*-
"""
Сервер панели в этом же процессе на выдуманных приборах - общая основа
проверок (tests/render_check.py, tests/routes_check.py) и тестового
сервера (tools/visual/serve.py). Без MQTT, без истории, без сети.

    import env
    web, palette, cfg, sent = env.setup(freeze=True)

freeze=True останавливает часы: time.time, localtime и datetime.now -
одно мгновение (сб, 17.10.2026, 12:00 МСК), поэтому картинки повторяются
байт в байт. sent - список [топик, значение], ушедших «в брокер».
"""

import datetime as _dt
import json
import os
import sys
import tempfile
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

#: Замороженное «сейчас»: сб, 17 октября 2026, 12:00 по Москве.
FROZEN = 1792227600.0
TZ = "Europe/Moscow"


def freeze():
    """Часы стоят: time.time, localtime и datetime.now - одно мгновение."""
    os.environ["TZ"] = TZ
    if hasattr(_time, "tzset"):
        _time.tzset()
    real_localtime = _time.localtime
    _time.time = lambda: FROZEN

    class Localtime(object):
        # Объект, а не функция: logging держит localtime атрибутом класса,
        # и функция стала бы методом - с лишним аргументом.
        def __call__(self, t=None):
            return real_localtime(FROZEN if t is None else t)

    _time.localtime = Localtime()

    class FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.fromtimestamp(FROZEN, tz)

    _dt.datetime = FrozenDatetime


def setup(config=None, freeze_time=True):
    """Поднять панель на конфиге (по умолчанию fixtures/showcase.yaml)."""
    if freeze_time:
        freeze()
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    os.chdir(ROOT)
    # Список пишет файл - пусть пишет во временный, а не в data/.
    tmp = tempfile.mkdtemp(prefix="wbpanel-test-")
    src = open(config or os.path.join(HERE, "showcase.yaml"), encoding="utf-8").read()
    cfg_path = os.path.join(tmp, "config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        fh.write(src + "\nlists_file: %s\n" % json.dumps(os.path.join(tmp, "lists.json")))
    os.environ["WB_PANEL_CONFIG"] = cfg_path

    from jinja2 import Environment, FileSystemLoader
    from wbpanel import assemble, cameras, palette, web
    from wbpanel.calendars import calendars
    from wbpanel.config import Config
    from wbpanel.lists import lists
    from wbpanel.state import state
    from wbpanel.water import water
    import state as fixture_state  # fixtures/state.py

    cfg = Config(cfg_path)
    web.config = cfg
    assemble.config = cfg
    cameras.config = cfg

    class NoHistory(object):
        mode = None

        def get(self, *a, **k):
            return []

        def refresh(self, *a, **k):
            return []

        def channels_with_history(self):
            return {}

    web.history = NoHistory()
    lists.configure(cfg, state)
    water.configure(cfg)
    calendars.configure(cfg, state)
    state.set_watched(cfg.used_channels(), cfg.service_prefixes())
    web.jinja = Environment(loader=FileSystemLoader([ROOT + "/templates", ROOT]),
                            autoescape=True, trim_blocks=True, lstrip_blocks=True)
    palette.load(web.themes_dir())
    fixture_state.seed(state)

    sent = []

    class FakeMqtt(object):
        """Брокер понарошку: команда записывается, прибор «переключается»."""

        def publish(self, topic, value, *a, **k):
            sent.append([topic, value])
            if topic.endswith("/on"):
                state.on_message(topic[:-3], value)

    web.mqtt_client = FakeMqtt()
    state.connected = True
    return web, palette, cfg, sent
