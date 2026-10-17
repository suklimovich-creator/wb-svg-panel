# -*- coding: utf-8 -*-
"""
Картинка панели и её доставка: кэш отрисовки, ожидание изменений
(since=), голова без повтора (have=), витрина тем. Выделено из web.py в
1.46.

Общее (app, конфиг, история, шаблоны) - в web.py: конфиг и историю
подменяют main() и проверки, поэтому они читаются как web.config,
web.history, а не копируются сюда при импорте.
"""

import hashlib
import re
import threading
import time
from collections import OrderedDict
from flask import Response, abort, request

from . import web
from . import __version__, palette, showcase, stats
from .assemble import resolve_sections
from .calendars import calendars
from .registry import channels_of
from .render import resolve_css_vars
from .state import state
from .status import status_channels, status_entries
from .web import app, panel_theme, render_panel

# ==========================================================================
#  Панель: кэш готовой картинки и ожидание изменений
# ==========================================================================

# Готовые картинки. Телефон и планшет на одной панели получают одну и ту же
# отрисовку, а не две: на WB7 большая панель стоит 0.13-0.15 с процессора.
# Держим по одной на панель, свёртку и число колонок, которые сейчас
# открыты.
#
# Перерисовка - не на любое изменение в доме, а только на изменение своих
# каналов панели (версия панели, а не общая), и не чаще раза в _MIN_RENDER
# секунд. До 1.12.4 ключом была общая версия: прихожая перерисовывалась от
# каждого датчика CO2 в спальне, а десять открытых экранов разгоняли
# процессор WB7 до 60 %.
_RENDERED = OrderedDict()
_RENDER_LOCK = threading.Lock()
_RENDER_MAX = 12

# Сколько запросов одновременно могут ждать изменений. Каждый держит поток
# waitress, и часть потоков нужна камерам и пультам. Лишний запрос не
# ждёт, а получает короткий ответ 204 без картинки - страница спросит
# через пару секунд.
_WAITERS = None


def _waiters():
    global _WAITERS
    if _WAITERS is None:
        http = web.config.get("http", {}) or {}
        threads = int(http.get("threads", 16))
        n = int(http.get("waiters", max(2, threads - 6)))
        _WAITERS = threading.BoundedSemaphore(max(1, n))
    return _WAITERS


def _min_render():
    """Не чаще раза в столько секунд одна и та же панель перерисовывается.
    Изменения пачкой (датчики, кондиционер) иначе давали бы отрисовку на
    каждое."""
    try:
        return max(0.0, float((web.config.get("http", {}) or {}).get("min_render", 1.0)))
    except (TypeError, ValueError):
        return 1.0


# После пробуждения - короткая пауза: изменения ходят пачками (кондиционер
# публикует полдюжины каналов подряд), и без неё пачка дала бы полдюжины
# отрисовок.
_SETTLE = 0.15


def _refresh_every():
    return max(2, int((web.config.get("http", {}) or {}).get("refresh", 10)))


def _panel_args(name, cols):
    closed = tuple(p for p in (request.args.get("closed") or "").split(",") if p)
    opened = request.args.get("open") or None
    mode = request.args.get("mode") or None
    flat = request.args.get("flat") not in ("0", "false", "no")
    asked = request.args.get("theme")
    theme = asked if asked and palette.exists(asked) else None
    return closed, opened, flat, theme, mode


def panel_version(name):
    """Версия своих каналов панели: растёт, только когда меняется то, что
    она рисует. Общая версия растёт от любого датчика в доме."""
    keys, prefixes = panel_watch(name)
    return state.last_touch(keys, prefixes)


def cached_panel(name, cols):
    """
    Готовая панель и версия состояния, которой она соответствует.

    Из кэша, если с прошлой отрисовки не менялось ничего своего и не
    сменилась отметка времени (часы в шапке, точки «давно не обновлялось»,
    кадр камеры и графики живут по таймеру, а не по версии). Если своё
    поменялось, но прошлая отрисовка моложе _MIN_RENDER, тоже из кэша - с
    её старой версией: страница увидит, что отстала, и спросит снова через
    секунду. Так пачка изменений даёт одну отрисовку, а не десять.
    """
    closed, opened, flat, _asked, mode = _panel_args(name, cols)
    # В ключе - тема, которая получится на самом деле: у theme: auto она
    # меняется на закате, и кэш не должен отдать дневную картинку ночью.
    theme = panel_theme(web.config.panels.get(name) or {})
    base = (name, cols, closed, opened, mode, flat, theme, getattr(web.config, "_mtime", 0),
            palette.version())
    bucket = int(time.time() // _refresh_every())
    with _RENDER_LOCK:
        now = time.time()
        rv = panel_version(name)
        hit = _RENDERED.get(base)
        if hit is not None and hit["bucket"] == bucket:
            if hit["rv"] == rv:
                _RENDERED.move_to_end(base)
                # Своё не менялось: картинка верна на текущую версию.
                return hit["svg"], state.version
            if now - hit["t"] < _min_render():
                _RENDERED.move_to_end(base)
                return hit["svg"], hit["ver"]
        # Версию берём ДО отрисовки: то, что поменяется во время неё,
        # страница увидит в следующем ответе, а не потеряет.
        ver = state.version
        with stats.timer("render"):
            svg = render_panel(name, cols=cols)
        if flat:
            panel_conf = web.config.panels.get(name) or {}
            svg = resolve_css_vars(svg, panel_theme(panel_conf), 1.0)
        _RENDERED[base] = {"svg": svg, "ver": ver, "rv": rv,
                           "bucket": bucket, "t": now}
        _RENDERED.move_to_end(base)
        while len(_RENDERED) > _RENDER_MAX:
            _RENDERED.popitem(last=False)
        return svg, ver


_WATCH = {}

#: Типы плиток, чьи каналы не будят ждущий запрос: обновляются по таймеру.
SLOW_TILES = ("chart", "forecast")

#: Поля чипа строки состояния, при которых он считается медленным.
SLOW_STATUS = ("above", "below", "air")


def panel_watch(name):
    """
    Что панель читает: ключи каналов и префиксы служб Sprut.hub.

    По этому ждущий запрос решает, его ли это изменение. Версия состояния
    общая на весь контроллер, и без фильтра страница детской просыпалась
    бы от каждого датчика гостиной.
    """
    stamp = getattr(web.config, "_mtime", 0)
    hit = _WATCH.get(name)
    if hit and hit[0] == stamp:
        return hit[1], hit[2]
    panel_conf = web.config.panels.get(name) or {}
    keys, prefixes = set(), set()
    sources = [panel_conf]
    for _title, tiles, status_src, _key in resolve_sections(panel_conf,
                                                            web.config.panels):
        sources.append(status_src)
        for tile in tiles:
            if not isinstance(tile, dict):
                continue
            # Графики и прогноз - медленные: температура и CO2 меняются
            # в сотых каждые пару секунд, и будить из-за них все экраны,
            # перерисовывать и пересылать всю панель незачем. Они
            # обновляются по таймеру, раз в refresh секунд, как и раньше.
            if tile.get("type") in SLOW_TILES:
                continue
            keys.update(channels_of(tile, state))
            if tile.get("service"):
                prefixes.add(str(tile["service"]).rstrip("/") + "/")
            if tile.get("type") in ("list", "today") and tile.get("list"):
                keys.add("list:%s" % tile["list"])
            if tile.get("type") in ("agenda", "today"):
                # Новая загрузка календаря будит панель, как пункт списка.
                for cal in calendars.pick(tile.get("calendars")):
                    keys.add("calendar:%s" % cal)
            if tile.get("type") == "header":
                sources.append(tile)
    for src in sources:
        for entry in status_entries(src or {}):
            # Чип с порогом (движение: above: 60) меняется на экране, только
            # когда значение пересекает порог, а Max Motion шумит каждую
            # секунду. Такой чип тоже обновляется по таймеру.
            if any(k in entry for k in SLOW_STATUS):
                continue
            keys.update(status_channels(entry))
    _WATCH[name] = (stamp, frozenset(keys), tuple(sorted(prefixes)))
    return _WATCH[name][1], _WATCH[name][2]


def panel_changed(name, since):
    """Менялось ли после версии since то, что панель показывает сразу."""
    keys, prefixes = panel_watch(name)
    return state.touched_since(since, keys, prefixes) or \
        state.touched_since(since, ("",))


def wait_for_panel(name, since, timeout):
    """
    Ждать изменения, которое касается этой панели. Возвращает версию.

    Просыпаемся на любое изменение, но отвечаем, только если поменялось
    то, что панель рисует; чужое пропускаем и ждём дальше. Время вышло -
    отвечаем как есть: часы и кадр камеры обновляются по таймеру, как и
    раньше.
    """
    deadline = time.time() + timeout
    keys, prefixes = panel_watch(name)
    seen = since
    while True:
        left = deadline - time.time()
        if left <= 0:
            return state.version
        version = state.wait_change(seen, left)
        if version <= seen:
            return version
        # «list:» и пустая метка тоже ключи: список и прочее, что поднимает
        # версию не через MQTT.
        if state.touched_since(seen, keys, prefixes) or \
                state.touched_since(seen, ("",)):
            time.sleep(_SETTLE)
            return state.version
        seen = version


@app.route("/<name>.svg")
def panel_svg(name):
    """
    Панель картинкой.

    С параметром since=<версия> запрос держится открытым, пока не
    поменяется что-нибудь нарисованное на этой панели, но не дольше
    refresh секунд. Так изменение доезжает до экрана сразу, а не на
    следующем круге опроса. Номер версии отдаётся в X-Panel-Version -
    страница пришлёт его в следующем запросе.
    """
    web.config.reload()
    # /main.svg?cols=2 - та же панель в две колонки, для телефона.
    # Отдельную панель в конфиге заводить не нужно.
    try:
        cols = int(request.args.get("cols") or 0) or None
    except ValueError:
        cols = None
    if cols:
        cols = max(1, min(cols, 12))
    if showcase.is_demo(name):
        return demo_svg(cols)
    if name not in web.config.panels:
        abort(404, "панель %r не найдена в конфиге" % name)

    since = request.args.get("since")
    if since is not None:
        try:
            since = int(since)
        except ValueError:
            since = None
    # Ждём, только если страница видела ровно текущую версию. Меньше -
    # она что-то пропустила, отвечаем сразу. Больше - демон перезапускался
    # и считает заново с нуля: тоже отвечаем сразу, иначе страница ждала
    # бы версии, до которой счётчик дойдёт нескоро.
    base_headers = {"Access-Control-Allow-Origin": "*",
                    "Access-Control-Expose-Headers":
                        "X-Panel-Version, X-Panel-App, X-Panel-Head, X-Panel-Lean, "
                        "X-Panel-Height",
                    # версия демона: страница со старыми скриптами
                    # перезагрузится сама (см. APP в wrapper.html)
                    "X-Panel-App": __version__}
    # Ждём, если страница видела текущую версию или с её версии не
    # менялось ничего из того, что панель показывает сразу: датчик CO2 в
    # спальне не повод слать прихожей всю картинку заново. Версия больше
    # текущей - демон перезапускался, отвечаем сразу.
    if since is not None and since <= state.version and (
            since == state.version or not panel_changed(name, since)):
        sem = _waiters()
        if not sem.acquire(blocking=False):
            # Мест для ждущих нет. Картинку не рисуем и не шлём: страница
            # и так показывает текущую версию. Короткий ответ - и она
            # спросит снова через пару секунд.
            headers = dict(base_headers)
            headers.update({"X-Panel-Version": str(since),
                            "Cache-Control": "no-store"})
            stats.inc("http.204")
            return Response(status=204, headers=headers)
        stats.inc("http.waiting")
        try:
            with stats.timer("http.waited"):
                wait_for_panel(name, since, _refresh_every())
        finally:
            sem.release()
            stats.inc("http.waiting", -1)

    svg, version = cached_panel(name, cols)
    headers = dict(base_headers)
    headers["X-Panel-Version"] = str(version)
    if since is not None or request.args.get("have"):
        headers["Cache-Control"] = "no-store"
        svg = lean_head(svg, request.args.get("have"), headers)
        stats.inc("http.svg")
        stats.inc("http.bytes", len(svg))
        return Response(svg, mimetype="image/svg+xml", headers=headers)

    # v3 в ключе: после смены формата страницы старые записи в кэше браузера
    # не должны совпадать с новыми
    etag = '"v3-%s-%s-%d-%d"' % (name, cols or "d", version, hash(svg) & 0xffffffff)
    if request.headers.get("If-None-Match") == etag:
        stats.inc("http.304")
        return Response(status=304, headers={"ETag": etag})
    headers.update({"ETag": etag,
                    "Cache-Control": "no-cache, must-revalidate"})
    lean_head(svg, None, headers)       # только отпечаток головы
    stats.inc("http.svg")
    stats.inc("http.bytes", len(svg))
    return Response(svg, mimetype="image/svg+xml", headers=headers)


#: Начало главной группы панели: до него - стили и заготовки (data-main в
#: panel.svg.j2).
MAIN_MARK = '<g data-main="1"'


def lean_head(svg, have, headers):
    """
    Голова панели - корень, <style> и <defs> - две трети картинки, и
    меняется она только с темой, колонками или раскладкой. Страница
    присылает отпечаток головы, которая у неё есть (have=); совпал -
    отдаём только главную группу, страница приставит голову сама
    (с 1.45). X-Panel-Head - отпечаток головы этой картинки.

    Высота корня в отпечаток не входит (1.47): свернули раздел - панель
    стала ниже, а стили и заготовки те же. Высоту страница берёт из
    X-Panel-Height и ставит в свою голову.
    """
    idx = svg.find(MAIN_MARK)
    if idx <= 0:
        return svg
    head = svg[:idx]
    found = ROOT_SIZE.search(head)
    key = head
    if found:
        key = head[:found.start()] + found.group(1) + found.group(3) + head[found.end():]
    mark = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    headers["X-Panel-Head"] = mark
    if have and have == mark:
        headers["X-Panel-Lean"] = "1"
        if found:
            headers["X-Panel-Height"] = found.group(2)
        return svg[idx:]
    return svg


#: Высота корня: height="H" и viewBox="0 0 W H" подряд (panel.svg.j2).
ROOT_SIZE = re.compile(r'( width="[^"]*") height="([^"]*)" viewBox="[^"]*"([^>]*>)')


def demo_svg(cols):
    """Витрина тем картинкой. Как обычная панель, только своё состояние:
    ждущий запрос просыпается от нажатий на витрине, а не от приборов."""
    st = showcase.state
    try:
        since = int(request.args.get("since"))
    except (TypeError, ValueError):
        since = None
    headers = {"Access-Control-Allow-Origin": "*",
               "Access-Control-Expose-Headers": "X-Panel-Version, X-Panel-App",
               "X-Panel-App": __version__, "Cache-Control": "no-store"}
    if since is not None and since == st.version:
        sem = _waiters()
        if not sem.acquire(blocking=False):
            headers["X-Panel-Version"] = str(since)
            return Response(status=204, headers=headers)
        try:
            st.wait_change(since, _refresh_every())
        finally:
            sem.release()
    version = st.version
    svg = render_panel(showcase.NAME, cols=cols)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(svg, panel_theme(), 1.0)
    headers["X-Panel-Version"] = str(version)
    return Response(svg, mimetype="image/svg+xml", headers=headers)
