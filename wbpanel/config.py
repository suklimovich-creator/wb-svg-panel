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
from .registry import channels_of
from .status import status_entries
from . import tiles          # noqa: F401  наполняет реестр типами плиток


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
        """
        Каналы, которые реально нарисованы хоть на одной панели.

        Список выводится из реестра: плитка сама знает, что читает - роли
        плюс то, что ролями не описывается (ряды графика, каналы прибора
        целиком). Новый тип плитки дописывать сюда не нужно.
        """
        out = set()
        for tile in self.all_tiles():
            out.update(channels_of(tile))
        # Строка состояния - не плитка, её каналы надо собрать отдельно.
        # Плитка header несёт такой же блок status, поэтому обходим и панели,
        # и плитки одним списком.
        sources = list((self.panels or {}).values()) + [
            t for t in self.all_tiles() if t.get("type") == "header"]
        for panel in sources:
            for entry in status_entries(panel):
                for key in ("channel", "value_channel"):
                    if entry.get(key):
                        out.add(entry[key])
                # Условие «прибор включён» читается из своего канала, и его
                # тоже надо слушать. Без этого require всегда видит пустоту
                # и считает прибор выключенным: чип не появляется вовсе,
                # хотя канал режима приходит исправно.
                req = entry.get("require")
                for item in (req if isinstance(req, list) else [req]):
                    if isinstance(item, dict) and item.get("channel"):
                        out.add(item["channel"])
        return out

    def raw_topics(self):
        """
        Каналы, заданные полным MQTT-топиком, а не парой «устройство/канал».

        Соглашение Wiren Board - ровно один слэш (`wb-msw-v4_149/Temperature`).
        Всё, где слэшей больше, считаем сырым топиком: так на панель попадают
        устройства чужих интеграций - Sprut.hub, Zigbee2MQTT и прочие.
        """
        out = set(ch for ch in self.used_channels() if ch.count("/") != 1)
        # Служба, заданная одной строкой `service:`, отдельных каналов в
        # конфиге не имеет - их имена станут известны только из метаданных,
        # которые ещё надо получить. Подписываемся на всю службу маской:
        # иначе плитка не увидит вообще ничего и будет пустой.
        for prefix in self.service_prefixes():
            out.add(prefix + "/#")
        return out

    def service_prefixes(self):
        """Префиксы служб Sprut.hub, упомянутых в конфиге."""
        out = set()
        for tile in self.all_tiles():
            prefix = (tile.get("service") or "").rstrip("/")
            if prefix:
                out.add(prefix)
        return out

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


