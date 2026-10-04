# -*- coding: utf-8 -*-
"""
Счётчики демона: на что уходит процессор.

«top показывает 100 %» не говорит, кто их съел: отрисовка, история из
базы, пересылка картинок или поток MQTT. Здесь демон сам считает, сколько
раз и сколько времени он делал каждое дело, а /stats отдаёт это вместе
с процессорным временем каждого потока. tools/watch-load.py раз в
пару секунд читает /stats и печатает разницу строкой.

Счётчики дешёвые: словарь под замком, без истории и без файлов.
"""

import os
import threading
import time

_lock = threading.Lock()
_count = {}
_secs = {}
_started = time.time()


def inc(name, n=1):
    with _lock:
        _count[name] = _count.get(name, 0) + n


def took(name, seconds):
    """Одно выполнение дела name, длившееся seconds."""
    with _lock:
        _count[name] = _count.get(name, 0) + 1
        _secs[name] = _secs.get(name, 0.0) + seconds


class timer(object):
    """with stats.timer("render"): ..."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        took(self.name, time.time() - self.t0)
        return False


def _tick():
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError):
        return 100.0


def threads():
    """Процессорное время каждого потока процесса, секунд: {имя: сек}.
    Из /proc/self/task - там видно и потоки, которые запустил не Python
    (paho, waitress), а имена берём у threading по native_id."""
    names = {}
    for t in threading.enumerate():
        nid = getattr(t, "native_id", None)
        if nid:
            names[nid] = t.name
    out = {}
    tick = _tick()
    base = "/proc/self/task"
    try:
        tids = os.listdir(base)
    except OSError:
        return out
    for tid in tids:
        try:
            with open(os.path.join(base, tid, "stat")) as fh:
                raw = fh.read()
        except OSError:
            continue
        # comm в скобках может содержать пробелы - режем по последней «)»
        rest = raw[raw.rfind(")") + 2:].split()
        try:
            cpu = (int(rest[11]) + int(rest[12])) / tick
        except (IndexError, ValueError):
            continue
        name = names.get(int(tid)) or raw[raw.find("(") + 1:raw.rfind(")")]
        # waitress называет рабочие потоки waitress-0, -1, ... - сводим в
        # одну строку, иначе их дюжина и сравнивать неудобно. Там же
        # отрисовка: она идёт в потоке запроса.
        if name.startswith("waitress"):
            name = "waitress"
        elif name == "MainThread":
            name = "main"
        out[name] = out.get(name, 0.0) + cpu
    return out


def snapshot():
    with _lock:
        count = dict(_count)
        secs = dict(_secs)
    return {"time": time.time(), "uptime": time.time() - _started,
            "cpu": time.process_time(), "count": count, "secs": secs,
            "threads": threads()}
