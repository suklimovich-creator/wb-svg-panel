# -*- coding: utf-8 -*-
"""
Чтение config.yaml и слежение за его изменениями.
"""

import os
import re
import time
import yaml
from .const import CONFIG_PATH, log
from .geometry import parse_duration
import threading
from .status import status_entries
from .tiles import ac_channels, forecast_channels


# ==========================================================================
#  Конфиг
# ==========================================================================

class Config:
    """Конфиг с горячей перезагрузкой по mtime - правите YAML, жмёте F5."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = 0
        self.data = {}
        self.reload(force=True)

    def reload(self, force=False):
        with self._lock:
            try:
                mtime = os.path.getmtime(self.path)
            except OSError:
                return
            if not force and mtime == self._mtime:
                return
            with open(self.path, "r", encoding="utf-8") as fh:
                self.data = yaml.safe_load(fh) or {}
            self._mtime = mtime
            log.info("конфиг загружен: %s", self.path)

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def panels(self):
        return self.data.get("panels", {}) or {}

    def all_tiles(self):
        """Все плитки всех панелей, включая лежащие в разделах."""
        for panel in self.panels.values():
            for tile in panel.get("tiles", []) or []:
                yield tile
            for section in panel.get("sections", []) or []:
                if isinstance(section, dict):
                    for tile in section.get("tiles", []) or []:
                        yield tile

    def used_channels(self):
        """Каналы, которые реально нарисованы хоть на одной панели."""
        out = set()
        if True:
            for tile in self.all_tiles():
                if tile.get("type") == "forecast":
                    out.update(forecast_channels(tile))
                if tile.get("type") == "ac":
                    out.update(ac_channels(tile))
                for key, value in tile.items():
                    # любое поле channel / channel_* - это канал; так новые
                    # типы плиток не приходится дописывать в этот список
                    if value and (key == "channel" or key.startswith("channel_")):
                        out.add(value)
                for s in tile.get("series", []) or []:
                    if s.get("channel"):
                        out.add(s["channel"])
        # строка состояния - не плитка, её каналы надо собрать отдельно
        for panel in (self.panels or {}).values():
            for entry in status_entries(panel):
                for key in ("channel", "value_channel"):
                    if entry.get(key):
                        out.add(entry[key])
        return out

    def raw_topics(self):
        """
        Каналы, заданные полным MQTT-топиком, а не парой «устройство/канал».

        Соглашение Wiren Board - ровно один слэш (`wb-msw-v4_149/Temperature`).
        Всё, где слэшей больше, считаем сырым топиком: так на панель попадают
        устройства чужих интеграций - Sprut.hub, Zigbee2MQTT и прочие.
        """
        return set(ch for ch in self.used_channels() if ch.count("/") != 1)

    def chart_series(self):
        """(channel, range_seconds, points) для всех графиков - для префетча."""
        out = {}
        if True:
            for tile in self.all_tiles():
                if tile.get("type") != "chart":
                    continue
                span = parse_duration(tile.get("range", "24h"))
                points = int(tile.get("points", 90))
                for s in tile.get("series", []) or []:
                    if s.get("channel"):
                        out[(s["channel"], span, points)] = True
        return list(out.keys())


