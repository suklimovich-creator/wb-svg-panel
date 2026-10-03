# -*- coding: utf-8 -*-
"""
HTTP: маршруты, отрисовка панелей, точка входа.
"""

import itertools
import json
import logging
import os
import re
import sys
import time
import paho.mqtt.client as mqtt
from flask import Flask, Response, abort, request
from jinja2 import Environment, FileSystemLoader
from .const import (BASE_DIR, CONFIG_PATH, DB_PATH, GAP, HEADER, PAD,
                    RX, TEMPLATE_DIRS, TILE, font_scale, log)
from .config import Config
from .state import MqttRpc, WbState, make_client, state, to_float
from .history import History
from .geometry import cells, parse_duration, text_width
from .status import status_channels, status_entries
from . import assemble
from .registry import channels_of, writable_of
from .assemble import (build_tile, cmd_attrs, prepare,
                       resolve_sections, xml_escape)
from .render import resolve_css_vars
from . import cameras
from .lists import ListError, lists
from .water import water
from . import checkup
import html
import threading
from collections import OrderedDict


app = Flask(__name__)


config = None


mqtt_client = None




history = None


jinja = None


def render_panel(name, cols=None):
    config.reload()
    state.set_watched(config.used_channels(), config.service_prefixes())
    panel_conf = config.panels.get(name)
    if panel_conf is None:
        abort(404, "панель %r не найдена в конфиге" % name)
    # Свёрнутые разделы приходят в адресе: панель - цельная картинка с
    # посчитанной геометрией, спрятать плитки на стороне браузера нельзя,
    # останутся дыры. Список свой на каждом устройстве, страница хранит
    # его рядом с числом колонок.
    closed = [p for p in (request.args.get("closed") or "").split(",") if p]
    opened = request.args.get("open") or None

    tiles, width, height, headers, top_chips = prepare(
        panel_conf, state, history, cols=cols, all_panels=config.panels,
        name=name, closed=closed, opened=opened)
    template = jinja.get_template(panel_conf.get("template", "panel.svg.j2"))
    return template.render(
        # префикс идентификаторов: панель и раскрытая плитка живут в одном
        # документе, и без него их clipPath и градиенты перекрывают друг друга
        uid="p-",
        fs=1,
        tiles=tiles,
        headers=headers,
        top_chips=top_chips,
        accordion=bool(panel_conf.get("accordion")),
        plate_sections=bool(panel_conf.get("section_plate",
                                           config.get("section_plate", True))),
        width=width,
        height=height,
        rx=RX,
        theme=panel_conf.get("theme", config.get("theme", "dark")),
        title=panel_conf.get("title", ""),
        updated=time.strftime("%H:%M"),
        connected=state.connected,
    )


def panel_order(panels):
    """
    Порядок панелей на главной.

    По умолчанию по алфавиту - но алфавит редко совпадает с тем, что нужно
    человеку: панель «all» оказывается первой просто потому, что с «a».
    Поле order задаёт вес: меньше - выше. Панели без веса идут следом,
    по алфавиту, чтобы не пришлось нумеровать все ради одной.

        panels:
          main:
            order: 1
    """
    def key(name):
        order = (panels.get(name) or {}).get("order")
        try:
            weight = float(order)
        except (TypeError, ValueError):
            weight = float("inf")
        return (weight, name)

    return sorted(panels, key=key)


@app.route("/")
def index():
    """Список всех панелей с живыми превью. Точка входа для человека."""
    config.reload()
    rows = []
    hidden_count = 0
    for name in panel_order(config.panels):
        panel_conf = config.panels[name] or {}
        tiles = []
        for _title, chunk, _status, _key in resolve_sections(panel_conf, config.panels):
            tiles.extend(chunk)
        kinds = {}
        for tile in tiles:
            kinds[tile.get("type", "value")] = kinds.get(tile.get("type", "value"), 0) + 1
        summary = ", ".join("%s × %d" % (k, v) for k, v in sorted(kinds.items()))
        is_hidden = bool(panel_conf.get("hidden"))
        if is_hidden:
            hidden_count += 1
        rows.append(
            '<a class="card%(cls)s" data-n="%(n)s" href="%(n)s.html">'
            '<div class="thumb">'
            '<img data-n="%(n)s" src="%(n)s.svg" loading="lazy" alt=""></div>'
            '<div class="meta"><b>%(t)s%(mark)s</b>'
            '<span class="slug">/%(n)s.svg</span>'
            '<span class="sum">%(s)s</span></div></a>' % {
                "n": html.escape(name),
                "t": html.escape(panel_conf.get("title") or name),
                "s": html.escape(summary or "пусто"),
                "cls": " is-hidden" if is_hidden else "",
                "mark": ' <span class="tagx">скрыта</span>' if is_hidden else "",
            })

    # Переключатель показываем, только когда есть что показывать: иначе он
    # просто мозолит глаза.
    toggle = ""
    if hidden_count:
        toggle = ('<label class="toggle"><input type="checkbox" id="sh">'
                  ' Показать скрытые <span class="cnt">%d</span></label>' % hidden_count)

    with state.lock:
        nchan = len(state.values)
        online = state.connected
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Панели</title><style>
 body{font:14px/1.5 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:22px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:22px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 .toggle{display:inline-flex;align-items:center;gap:7px;margin-bottom:18px;
         padding:7px 13px;background:#fff;border:1.5px solid rgba(60,60,67,.16);
         border-radius:10px;cursor:pointer;user-select:none;font-size:13.5px}
 .toggle:hover{border-color:#E09B2D}
 .toggle input{accent-color:#E09B2D;margin:0}
 .cnt{color:#8A8A90}
 .grid{display:grid;gap:16px;
       grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
 .card{display:block;text-decoration:none;color:inherit;background:#fff;
       border:1.5px solid rgba(60,60,67,.16);border-radius:16px;overflow:hidden}
 .card:hover{border-color:#E09B2D}
 .card.is-hidden{display:none;opacity:.72;border-style:dashed}
 .grid.show-hidden .card.is-hidden{display:block}
 .thumb{background:#E6E6E9;padding:10px;display:flex;justify-content:center}
 .thumb img{max-width:100%%;height:auto;border-radius:8px}
 .meta{padding:11px 14px}
 .meta b{display:block;font-size:15px}
 .tagx{font-size:11px;font-weight:600;color:#8A8A90;background:#EDEDF0;
       padding:1px 6px;border-radius:6px;vertical-align:1px}
 .slug{display:block;color:#8A8A90;font:12px ui-monospace,Menlo,monospace;
       margin-top:2px}
 .sum{display:block;color:#8A8A90;font-size:12.5px;margin-top:5px}
 .links{margin-top:22px;color:#8A8A90;font-size:13px}
 .links a{color:#3A3A3E}
 .off{color:#C0392B}
 /* тот же выбор колонок, что и на самой панели: значение общее */
 #cols{display:inline-block;background:#fff;border:1.5px solid rgba(60,60,67,.16);
       border-radius:11px;padding:3px;margin:0 0 14px 10px;vertical-align:middle}
 #cols b{display:inline-block;padding:6px 10px;border-radius:8px;cursor:pointer;
         font-size:13.5px;font-weight:600;color:#8A8A90}
 #cols b.on{background:#E09B2D;color:#fff}
</style></head><body>
<h1>Панели</h1>
<div class="sub">%(count)d шт. · каналов в памяти: %(chan)d · история: %(hist)s
 %(mqtt)s</div>
%(toggle)s<span id="cols"></span>
<div class="grid" id="g">%(rows)s</div>
<div class="links">
 <a href="docs"><b>Как собирать панели</b></a> ·
 <a href="channels">Список каналов</a> ·
 <a href="check">Проверка конфига</a> ·
 <a href="healthz">healthz</a> ·
 плитка ведёт на <code>.html</code> с автообновлением,
 сама картинка — <code>&lt;имя&gt;.svg</code>
</div>
<script>
 var sh = document.getElementById('sh');
 if (sh) sh.onchange = function () {
   document.getElementById('g').classList.toggle('show-hidden', sh.checked);
 };

 /* Число колонок общее со страницами панелей - лежит в том же ключе.
    Меняем здесь: обновляются и превью, и ссылки, по которым вы уйдёте. */
 var STORE = 'wb-panel-cols', COLS = '';
 try { COLS = localStorage.getItem(STORE) || ''; } catch (e) {}

 function applyCols() {
   var q = COLS ? ('?cols=' + COLS) : '';
   var imgs = document.querySelectorAll('#g img[data-n]');
   for (var i = 0; i < imgs.length; i++) {
     imgs[i].src = imgs[i].getAttribute('data-n') + '.svg' + q;
   }
   var links = document.querySelectorAll('#g a[data-n]');
   for (var j = 0; j < links.length; j++) {
     links[j].href = links[j].getAttribute('data-n') + '.html' + q;
   }
 }

 function drawCols() {
   var box = document.getElementById('cols'),
       opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'], h = '';
   for (var i = 0; i < opts.length; i++) {
     h += '<b data-v="' + opts[i] + '"' + (opts[i] === COLS ? ' class="on"' : '') + '>'
        + (opts[i] === '' ? 'авто' : opts[i]) + '</b>';
   }
   box.innerHTML = h;
   var btns = box.getElementsByTagName('b');
   for (var j = 0; j < btns.length; j++) {
     btns[j].onclick = function () {
       COLS = this.getAttribute('data-v');
       try { COLS ? localStorage.setItem(STORE, COLS) : localStorage.removeItem(STORE); }
       catch (e) {}
       drawCols(); applyCols();
     };
   }
 }
 drawCols(); applyCols();
</script>
</body></html>""" % {
        "count": len(rows) - hidden_count, "chan": nchan,
        "hist": html.escape(str(history.mode if history and history.mode else "—")),
        "mqtt": "" if online else '· <span class="off">нет связи с MQTT</span>',
        "toggle": toggle,
        "rows": "".join(rows) or "<i>в config.yaml нет ни одной панели</i>",
    }
    return Response(page, mimetype="text/html; charset=utf-8")


def allowed_topics():
    """
    Куда демону вообще позволено писать.

    Список выводится из тех же ролей, по которым собираются плитки, а не
    отдельным перебором по типам. Раньше перебора было два, и второй
    отставал: за одну неделю трижды забыли дописать топик - «стоп» у шторы,
    канал require у чипа, команды термостата, - и каждый раз это выглядело
    как «кнопка не работает».

    Открытым шлюзом в MQTT панель быть не должна: брокер на Wiren Board по
    умолчанию без пароля, и писать в него можно что угодно, включая реле
    котла.
    """
    allowed = set()
    for panel_conf in config.panels.values():
        interactive = bool((panel_conf or {}).get("interactive",
                           config.get("interactive", False)))
        if not interactive:
            continue
        for _title, tiles, _status, _key in resolve_sections(panel_conf,
                                                             config.panels):
            for tile in tiles:
                allowed.update(writable_of(tile, state))
        # Сброс тревоги протечки нажимают и из окна чипа, а чип не плитка
        # и белого списка своего не имеет. Сброс модулей из раздела water -
        # кнопка, которую человек и так нажмёт на самом модуле.
        allowed.update(water.all_resets())
    return allowed


@app.route("/api/publish", methods=["POST"])
def api_publish():
    config.reload()
    try:
        body = json.loads(request.get_data(as_text=True) or "{}")
        topic = str(body["topic"])
        value = str(body["value"])
    except (ValueError, KeyError, TypeError):
        return Response('{"error":"нужны поля topic и value"}', status=400,
                        mimetype="application/json")

    if topic not in allowed_topics():
        log.warning("отклонена публикация в %s", topic)
        return Response('{"error":"топик не разрешён"}', status=403,
                        mimetype="application/json")
    if len(value) > 32:
        return Response('{"error":"слишком длинное значение"}', status=400,
                        mimetype="application/json")
    if not mqtt_client or not state.connected:
        return Response('{"error":"нет связи с MQTT"}', status=503,
                        mimetype="application/json")

    mqtt_client.publish(topic, value)
    log.info("публикация %s = %s", topic, value)
    return Response('{"ok":true}', mimetype="application/json")


def _json(body, status=200):
    return Response(json.dumps(body, ensure_ascii=False), status=status,
                    mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


@app.route("/api/list/<name>", methods=["GET", "POST"])
def api_list(name):
    """
    Список целиком (GET) или одно действие над ним (POST).

        POST {"op": "add", "text": "Молоко"}
        POST {"op": "done" | "undo" | "delete", "id": "…"}
        POST {"op": "move", "id": "…", "to": "up" | "down" | "top"}
        POST {"op": "edit", "id": "…", "text": "…"}
        POST {"op": "clear"}            выполненные - сразу, не дожидаясь срока

    Белый список здесь - раздел lists: в config.yaml: писать можно только
    в описанные там списки, и только словами, а не топиками. Ответ на
    любое действие - список после него, чтобы окну не пришлось
    переспрашивать.

    Быстрой команде на iPhone хватает того же адреса с паролем nginx:
    POST на /panel/api/list/shopping с телом {"op":"add","text":"…"}.
    """
    config.reload()
    if not lists.known(name):
        return _json({"error": "список %r не описан в config.yaml" % name}, 404)
    if request.method == "POST":
        raw = request.get_data(as_text=True) or ""
        if len(raw) > 4096:
            return _json({"error": "слишком длинный запрос"}, 413)
        try:
            body = json.loads(raw or "{}")
        except ValueError:
            # Быстрые команды иногда шлют текст как есть, а не JSON: такое
            # тело считаем новым пунктом.
            body = {"op": "add", "text": raw}
        if not isinstance(body, dict):
            return _json({"error": "ждём объект JSON"}, 400)
        try:
            lists.apply(name, body)
        except ListError as exc:
            return _json({"error": str(exc)}, exc.status)
        log.info("список %s: %s", name, body.get("op"))
    return _json(lists.view(name))


@app.route("/cam/<name>.jpg")
def cam_jpg(name):
    """
    Один кадр с камеры.

    Кеш браузера здесь работает на нас: адрес плитки меняется раз в
    `refresh` секунд, и в промежутке картинка не перекачивается. Поэтому
    max-age равен как раз этому промежутку - панель перечитывается чаще,
    и без кеша кадр моргал бы при каждой перерисовке.
    """
    config.reload()
    cam = cameras.get(name)
    if cam is None:
        abort(404, "камера %r не описана в разделе cameras" % name)
    try:
        data, ctype = cam.snapshot()
    except cameras.CameraError as exc:
        log.warning("%s", exc)
        # 502, а не 500: не мы сломались, а прибор за нами. Страница по
        # этому коду показывает заглушку и продолжает пробовать.
        return Response(str(exc), status=502, mimetype="text/plain")
    # ?live=1 - полноэкранный просмотр снимками, там кеш только мешает.
    if request.args.get("live") in ("1", "true", "yes"):
        cache = "no-store"
    else:
        cache = "max-age=%d" % int(cam.refresh)
    return Response(data, mimetype=ctype, headers={"Cache-Control": cache})


@app.route("/cam/<name>.mjpeg")
def cam_stream(name):
    """
    Поток MJPEG, проксируемый как есть.

    Соединение живёт, пока открыто окно, и всё это время занимает поток
    waitress - отсюда и ограничение на число одновременных просмотров, и
    срок жизни одного соединения. Страница переподключается сама.
    """
    config.reload()
    cam = cameras.get(name)
    if cam is None:
        abort(404, "камера %r не описана в разделе cameras" % name)
    if not cameras.acquire_slot():
        return Response("сейчас смотрят слишком много камер",
                        status=503, mimetype="text/plain")
    try:
        gen, ctype = cam.stream()
    except cameras.CameraError as exc:
        cameras.release_slot()
        log.warning("%s", exc)
        return Response(str(exc), status=502, mimetype="text/plain")

    def body():
        try:
            for chunk in gen:
                yield chunk
        finally:
            cameras.release_slot()

    return Response(body(), mimetype=ctype, direct_passthrough=True,
                    headers={"Cache-Control": "no-store",
                             "X-Accel-Buffering": "no"})


@app.route("/manifest.webmanifest")
def manifest():
    """
    Делает страницу устанавливаемой. После «Добавить на главный экран»
    она запускается без адресной строки и кнопок браузера - это и есть
    киоск без сторонних приложений.
    """
    body = {
        "name": "Панель дома",
        "short_name": "Дом",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0F0F11",
        "theme_color": "#0F0F11",
        "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    }
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/manifest+json")


@app.route("/icon.svg")
def icon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
           '<rect width="192" height="192" rx="42" fill="#1B1B1F"/>'
           '<rect x="34" y="34" width="55" height="55" rx="14" fill="#E09B2D"/>'
           '<rect x="103" y="34" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="34" y="103" width="55" height="55" rx="14" fill="#4A4A50"/>'
           '<rect x="103" y="103" width="55" height="55" rx="14" fill="#3B8FD4"/>'
           '</svg>')
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "max-age=86400"})


@app.route("/docs")
def docs():
    """Справочник по сборке панелей. Лежит рядом с panel.py отдельным файлом."""
    for candidate in ("docs.html", "templates/docs.html"):
        path = os.path.join(BASE_DIR, candidate)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return Response(fh.read(), mimetype="text/html; charset=utf-8")
    return Response("Файл docs.html не найден в %s" % BASE_DIR,
                    status=404, mimetype="text/plain; charset=utf-8")


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
        http = config.get("http", {}) or {}
        threads = int(http.get("threads", 16))
        n = int(http.get("waiters", max(2, threads - 6)))
        _WAITERS = threading.BoundedSemaphore(max(1, n))
    return _WAITERS


def _min_render():
    """Не чаще раза в столько секунд одна и та же панель перерисовывается.
    Изменения пачкой (датчики, кондиционер) иначе давали бы отрисовку на
    каждое."""
    try:
        return max(0.0, float((config.get("http", {}) or {}).get("min_render", 1.0)))
    except (TypeError, ValueError):
        return 1.0


# После пробуждения - короткая пауза: изменения ходят пачками (кондиционер
# публикует полдюжины каналов подряд), и без неё пачка дала бы полдюжины
# отрисовок.
_SETTLE = 0.15


def _refresh_every():
    return max(2, int((config.get("http", {}) or {}).get("refresh", 10)))


def _panel_args(name, cols):
    closed = tuple(p for p in (request.args.get("closed") or "").split(",") if p)
    opened = request.args.get("open") or None
    flat = request.args.get("flat") not in ("0", "false", "no")
    return closed, opened, flat


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
    closed, opened, flat = _panel_args(name, cols)
    base = (name, cols, closed, opened, flat, getattr(config, "_mtime", 0))
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
        svg = render_panel(name, cols=cols)
        if flat:
            panel_conf = config.panels.get(name) or {}
            svg = resolve_css_vars(
                svg, panel_conf.get("theme", config.get("theme", "light")), 1.0)
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
SLOW_STATUS = ("above", "below")


def panel_watch(name):
    """
    Что панель читает: ключи каналов и префиксы служб Sprut.hub.

    По этому ждущий запрос решает, его ли это изменение. Версия состояния
    общая на весь контроллер, и без фильтра страница детской просыпалась
    бы от каждого датчика гостиной.
    """
    stamp = getattr(config, "_mtime", 0)
    hit = _WATCH.get(name)
    if hit and hit[0] == stamp:
        return hit[1], hit[2]
    panel_conf = config.panels.get(name) or {}
    keys, prefixes = set(), set()
    sources = [panel_conf]
    for _title, tiles, status_src, _key in resolve_sections(panel_conf,
                                                            config.panels):
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
            if tile.get("type") == "list" and tile.get("list"):
                keys.add("list:%s" % tile["list"])
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
    config.reload()
    # /main.svg?cols=2 - та же панель в две колонки, для телефона.
    # Отдельную панель в конфиге заводить не нужно.
    try:
        cols = int(request.args.get("cols") or 0) or None
    except ValueError:
        cols = None
    if cols:
        cols = max(1, min(cols, 12))
    if name not in config.panels:
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
                    "Access-Control-Expose-Headers": "X-Panel-Version"}
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
            return Response(status=204, headers=headers)
        try:
            wait_for_panel(name, since, _refresh_every())
        finally:
            sem.release()

    svg, version = cached_panel(name, cols)
    headers = dict(base_headers)
    headers["X-Panel-Version"] = str(version)
    if since is not None:
        headers["Cache-Control"] = "no-store"
        return Response(svg, mimetype="image/svg+xml", headers=headers)

    # v3 в ключе: после смены формата страницы старые записи в кэше браузера
    # не должны совпадать с новыми
    etag = '"v3-%s-%s-%d-%d"' % (name, cols or "d", version, hash(svg) & 0xffffffff)
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    headers.update({"ETag": etag,
                    "Cache-Control": "no-cache, must-revalidate"})
    return Response(svg, mimetype="image/svg+xml", headers=headers)


def panel_tiles(panel_conf):
    """Плоский список плиток панели в том же порядке, что и на картинке."""
    out = []
    for _title, tiles, _status, _key in resolve_sections(panel_conf, config.panels):
        out.extend(tiles)
    return out


@app.route("/tile/<name>/<int:index>.svg")
def tile_svg(name, index):
    """
    Одна плитка крупным планом - для полноэкранного вида.

    Размер приходит в пикселях: страница знает про экран, сервер - нет.
    """
    config.reload()
    panel_conf = config.panels.get(name)
    if panel_conf is None:
        abort(404, "панель %r не найдена" % name)
    tiles = panel_tiles(panel_conf)
    if not 0 <= index < len(tiles):
        abort(404, "плитки %d нет на панели %r" % (index, name))

    try:
        width = max(240, min(int(request.args.get("w") or 700), 2000))
        height = max(200, min(int(request.args.get("h") or 480), 2000))
    except ValueError:
        width, height = 700, 480

    interactive = bool(panel_conf.get("interactive",
                       config.get("interactive", False)))
    tile = build_tile(tiles[index], 0, PAD, PAD, width, height,
                      panel_conf, state, history, interactive)
    scale = font_scale(height)
    svg = jinja.get_template(panel_conf.get("template", "panel.svg.j2")).render(
        uid="t%d-" % index,
        fs=scale,
        tiles=[tile], headers=[],
        width=width + PAD * 2, height=height + PAD * 2,
        rx=RX, theme=panel_conf.get("theme", config.get("theme", "light")),
        title="", updated=time.strftime("%H:%M"), connected=state.connected)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(
            svg, panel_conf.get("theme", config.get("theme", "light")), scale)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


# Обёртка страницы вынесена в templates/wrapper.html: восемьсот строк
# разметки со скриптами внутри .py мешали и читать, и править. Файл берём
# как обычный текст, а не через Jinja - в скриптах полно фигурных скобок,
# и шаблонизатор на них спотыкается. Подстановка нужна ровно в четырёх
# местах, для этого хватает replace.
_WRAPPER = {"mtime": 0.0, "text": ""}


def wrapper_html():
    """Читает обёртку с диска, перечитывая при изменении файла."""
    for base in TEMPLATE_DIRS:
        path = os.path.join(base, "wrapper.html")
        if not os.path.exists(path):
            continue
        mtime = os.path.getmtime(path)
        if mtime != _WRAPPER["mtime"]:
            with open(path, encoding="utf-8") as fh:
                _WRAPPER["text"] = fh.read()
            _WRAPPER["mtime"] = mtime
        return _WRAPPER["text"]
    raise RuntimeError("не найден templates/wrapper.html")


@app.route("/chart.svg")
def chart_svg():
    """
    График одного канала - для чипов строки состояния.

    Плитки под него нет: чип показывает состояние, а история к нему
    приделана сбоку. Поэтому собираем временную плитку типа chart прямо
    здесь, из параметров запроса.
    """
    config.reload()
    channel = request.args.get("channel") or ""
    if not channel:
        abort(400, "нужен параметр channel")
    if channel not in config.used_channels():
        # Тот же принцип, что и у записи в MQTT: рисуем только то, что
        # описано в конфиге. Иначе по адресу можно вытащить историю
        # любого канала контроллера.
        abort(403, "канал %r не используется ни на одной панели" % channel)

    try:
        width = max(240, min(int(request.args.get("w") or 700), 2000))
        height = max(200, min(int(request.args.get("h") or 480), 2000))
    except ValueError:
        width, height = 700, 480

    # История греется в фоне по каналам плиток chart. Канала, который сюда
    # пришёл, там может не быть вовсе - тогда кэш пуст и график выходит
    # пустым. Просим источник прямо сейчас: это разовое действие человека,
    # подождать полсекунды он готов.
    span = parse_duration(request.args.get("range") or "12h")
    if not history.get(channel, span, 120):
        history.refresh(channel, span, 120)

    conf = {
        "type": "chart",
        "title": request.args.get("title") or channel.rsplit("/", 1)[-1],
        "range": request.args.get("range") or "12h",
        "points": 120,
        "grid_labels": True,
        "time_labels": True,
        "series": [{"channel": channel,
                    "unit": request.args.get("unit") or "",
                    "digits": int(request.args.get("digits") or 0)}],
    }
    theme = config.get("theme", "light")
    tile = build_tile(conf, 0, PAD, PAD, width, height, {}, state, history, False)
    scale = font_scale(height)
    svg = jinja.get_template("panel.svg.j2").render(
        uid="c-", fs=scale, tiles=[tile], headers=[],
        width=width + PAD * 2, height=height + PAD * 2,
        rx=RX, theme=theme, title="", updated=time.strftime("%H:%M"),
        connected=state.connected)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(svg, theme, scale)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.route("/<name>.html")
def panel_html(name):
    """
    Страница панели для планшета.

    SVG не вставляется через <img>, а встраивается в разметку: только так
    по плиткам можно кликать. Скрипты внутри <img> браузер не выполняет,
    и обработчики на элементы повесить неоткуда.
    """
    config.reload()
    every = int(config.get("http", {}).get("refresh", 10))
    panel_conf = config.panels.get(name) or {}
    interactive = bool(panel_conf.get("interactive",
                       config.get("interactive", False)))

    html_page = wrapper_html()
    for mark, value in (
            ("__NAME__", html.escape(name)),
            ("__NAME_JS__", json.dumps(name)),
            ("__EVERY__", str(every)),
            ("__INTERACTIVE__", "true" if interactive else "false")):
        html_page = html_page.replace(mark, value)

    return Response(html_page, mimetype="text/html; charset=utf-8")


# какой тип плитки обычно подходит для типа канала Wiren Board
TILE_HINT = {
    "switch": "switch", "pushbutton": "switch", "alarm": "switch",
    "range": "light · curtain", "rgb": "—",
    "value": "chart · value", "temperature": "chart", "rel_humidity": "chart",
    "voltage": "chart", "current": "chart", "power": "chart",
    "power_consumption": "chart", "resistance": "chart", "pressure": "chart",
    "concentration": "chart", "lux": "chart", "sound_level": "chart",
    "text": "value",
}


@app.route("/channels")
def channels():
    """
    Браузер каналов. На контроллере их бывают сотни, поэтому:
      * сгруппированы по устройствам;
      * фильтр работает мгновенно, прямо в браузере;
      * отмечено, у каких каналов есть история — только по ним можно график;
      * имя копируется в буфер одной кнопкой, чтобы не набирать руками.

    curl -s localhost:8088/channels?format=text — то же самое для консоли.
    """
    query = request.args.get("q", "").strip().lower()
    with state.lock:
        values = dict(state.values)
        metas = dict(state.meta)
        stamps = dict(state.stamps)
    logged = history.channels_with_history() if history else {}
    used = config.used_channels()
    now = time.time()

    keys = sorted(values)
    if query:
        keys = [k for k in keys if query in k.lower()]

    if request.args.get("format") == "text":
        lines = ["%-54s %-14s %-16s %s" % ("КАНАЛ", "ЗНАЧЕНИЕ", "ТИП", "ИСТОРИЯ")]
        for key in keys:
            meta = metas.get(key, {})
            lines.append("%-54s %-14s %-16s %s" % (
                key, str(values.get(key, ""))[:14], meta.get("type", ""),
                "да" if key in logged else "—"))
        body = "\n".join(lines) + "\n\nвсего: %d\n" % len(keys)
        return Response(body, mimetype="text/plain; charset=utf-8")

    # --- группировка по устройству ---
    groups = {}
    for key in keys:
        device = key.split("/", 1)[0]
        groups.setdefault(device, []).append(key)

    esc = html.escape
    rows = []
    for device in sorted(groups):
        rows.append('<tr class="dev"><td colspan="5">%s <span class="n">%d</span></td></tr>'
                    % (esc(device), len(groups[device])))
        for key in groups[device]:
            meta = metas.get(key, {})
            ctype = str(meta.get("type", ""))
            units = str(meta.get("units", ""))
            value = str(values.get(key, ""))
            age = now - stamps.get(key, 0)
            hist = logged.get(key)
            rows.append(
                '<tr data-k="%s">'
                '<td class="k"><button onclick="cp(this)" data-c="%s">⧉</button>'
                '<code>%s</code>%s</td>'
                '<td class="v">%s <span class="u">%s</span></td>'
                '<td class="t">%s</td>'
                '<td class="h">%s</td>'
                '<td class="g">%s</td></tr>' % (
                    esc(key.lower()), esc(key), esc(key.split("/", 1)[1]),
                    ' <span class="used">в панели</span>' if key in used else "",
                    esc(value[:20]), esc(units),
                    esc(ctype),
                    ('<span class="ok">история%s</span>' %
                     ((" · %s" % hist) if hist else "")) if key in logged else "",
                    esc(TILE_HINT.get(ctype, "")),
                ))

    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Каналы контроллера</title><style>
 body{font:14px/1.45 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:16px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 #q{width:100%%;max-width:520px;padding:10px 13px;font-size:15px;
    border:1.5px solid rgba(60,60,67,.2);border-radius:11px;background:#fff;
    box-sizing:border-box}
 table{border-collapse:collapse;width:100%%;margin-top:14px;background:#fff;
       border-radius:12px;overflow:hidden}
 td{padding:6px 12px;border-bottom:1px solid #EDEDF0;vertical-align:top}
 tr.dev td{background:#E6E6E9;font-weight:600;padding:9px 12px;
           position:sticky;top:0}
 tr.dev .n{color:#8A8A90;font-weight:400;margin-left:6px}
 code{font:13px ui-monospace,Menlo,Consolas,monospace}
 button{border:none;background:none;cursor:pointer;color:#8A8A90;
        font-size:14px;margin-right:7px;padding:0}
 button:hover{color:#E09B2D}
 .v{font-weight:500;white-space:nowrap}
 .u,.t,.g{color:#8A8A90;font-size:13px}
 .ok{color:#1F8F4E;font-size:12.5px}
 .used{background:#E09B2D;color:#fff;font-size:11px;padding:1px 6px;
       border-radius:6px;margin-left:7px}
 .hide{display:none}
</style></head><body>
<h1>Каналы контроллера</h1>
<div class="sub">Всего %(total)d. Скопируйте имя кнопкой ⧉ и вставьте в
<code>config.yaml</code>. График можно построить только по каналам с пометкой
«история» — остальные не пишутся в <code>wb-mqtt-db</code>.</div>
<input id="q" placeholder="фильтр: temp, curtain, wb-mr6c…" autofocus>
<table id="t">%(rows)s</table>
<script>
 var q=document.getElementById('q'), rs=document.querySelectorAll('#t tr');
 q.oninput=function(){var s=q.value.toLowerCase();
   rs.forEach(function(r){
     if(r.className==='dev'){r.classList.toggle('hide',s!=='');return;}
     r.classList.toggle('hide', s && r.dataset.k.indexOf(s)<0);});};
 function cp(b){navigator.clipboard.writeText(b.dataset.c);
   var o=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=o},900);}
</script></body></html>""" % {"total": len(keys), "rows": "".join(rows)}
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/check")
def check_page():
    """
    Проверка конфига: что панель поняла, а что нет.

    Описана в 0.10.0, но маршрут потерялся при одной из сборок файлов -
    сама проверка в checkup.py была цела, а открыть её было негде.

    curl -s localhost:8088/check?format=text - то же для консоли.
    """
    config.reload()
    problems = checkup.check_config(config, state, history)
    if request.args.get("format") == "text":
        return Response(checkup.as_text(problems),
                        mimetype="text/plain; charset=utf-8")
    esc = html.escape
    total = checkup.summary(problems)
    rows = []
    for p in problems:
        rows.append(
            '<tr class="%s"><td class="lv">%s</td><td>%s</td>'
            '<td><b>%s</b> <span class="k">%s</span></td>'
            '<td>%s%s</td></tr>' % (
                "err" if p["level"] == checkup.ERROR else "warn",
                esc(p["level"]), esc(str(p["panel"])), esc(str(p["tile"])),
                esc(str(p["kind"])), esc(p["text"]),
                ('<div class="h">%s</div>' % esc(p["hint"])) if p["hint"] else ""))
    page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка конфига</title><style>
 body{font:14px/1.45 -apple-system,'Segoe UI',Inter,system-ui,sans-serif;
      margin:0;padding:16px;background:#F1F1F3;color:#3A3A3E}
 h1{font-size:19px;margin:0 0 4px}
 .sub{color:#8A8A90;margin-bottom:14px}
 table{border-collapse:collapse;width:100%%;background:#fff;border-radius:12px;
       overflow:hidden}
 td{padding:8px 12px;border-bottom:1px solid #EDEDF0;vertical-align:top}
 .lv{font-weight:600;white-space:nowrap}
 .err .lv{color:#C0392B} .warn .lv{color:#B8791A}
 .k{color:#8A8A90;font-size:12.5px}
 .h{color:#8A8A90;font-size:13px;margin-top:3px}
 .ok{background:#fff;border-radius:12px;padding:16px;color:#1F8F4E}
</style></head><body>
<h1>Проверка конфига</h1>
<div class="sub">Ошибок %(e)d, предупреждений %(w)d ·
<a href="check?format=text">текстом</a> · <a href="./">к панелям</a></div>
%(body)s
</body></html>""" % {
        "e": total["errors"], "w": total["warnings"],
        "body": ("<table>%s</table>" % "".join(rows)) if rows
                else '<div class="ok">Проблем не найдено.</div>'}
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/healthz")
def healthz():
    with state.lock:
        body = {"mqtt": state.connected, "channels": len(state.values),
                "version": state.version, "panels": sorted(config.panels),
                "hidden": sorted(n for n, p in config.panels.items()
                                 if (p or {}).get("hidden")),
                "history": history.mode if history else None,
                "template_dirs": TEMPLATE_DIRS}
    return Response(json.dumps(body, ensure_ascii=False),
                    mimetype="application/json")


def main():
    global config, history, jinja

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(message)s")

    config = Config(CONFIG_PATH)
    # Сборка плиток заглядывает в общие настройки панели, но получить их
    # параметром неоткуда: build_tile зовётся из десятка мест.
    assemble.config = config
    # Камеры читают адреса и пароли из того же конфига, и тоже
    # вызываются оттуда, куда параметр не передать.
    cameras.config = config
    # Списки живут в файле рядом с конфигом; при старте заодно уходят
    # выполненные, у которых вышел срок, пока демон не работал.
    lists.configure(config, state)
    # Раздел water читают плитки и чипы протечки, в том числе при подписке,
    # то есть до первой отрисовки.
    water.configure(config)
    state.set_watched(config.used_channels(), config.service_prefixes())

    jinja = Environment(
        loader=FileSystemLoader(TEMPLATE_DIRS),
        autoescape=True,
        auto_reload=True,          # правки шаблона подхватываются без рестарта
        trim_blocks=True,
        lstrip_blocks=True,
    )

    mqtt_conf = config.get("mqtt", {}) or {}
    client_id = mqtt_conf.get("client_id", "wb-svg-panel-%d" % os.getpid())
    client = make_client(client_id)
    if mqtt_conf.get("username"):
        client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

    global mqtt_client
    mqtt_client = client
    # QoS 1: копия списка публикуется и при старте, ещё до подключения, а
    # сообщение с нулевым QoS paho в таком случае просто выбрасывает.
    lists.publish = lambda topic, payload, retain=False: client.publish(
        topic, payload, qos=1, retain=retain)
    rpc = MqttRpc(client, client_id)
    hconf = config.get("history", {}) or {}
    history = History(rpc,
                      db_path=hconf.get("db_path", DB_PATH),
                      ttl=int(hconf.get("cache_ttl", 60)),
                      prefer=hconf.get("source", "auto"))

    subscribed_raw = set()

    def sync_raw_topics(cli):
        """Подписаться на топики сторонних шлюзов, упомянутые в конфиге."""
        # Списки могли дописать в конфиг на ходу: тогда подписка на приём
        # пунктов и копия в брокер появляются без перезапуска.
        if lists.defs() and lists.add_topic() not in subscribed_raw:
            cli.subscribe(lists.add_topic(), 0)
            subscribed_raw.add(lists.add_topic())
            lists.mirror_all()
        wanted = config.raw_topics()
        new = wanted - subscribed_raw
        if new:
            cli.subscribe([(t, 0) for t in sorted(new)])
            subscribed_raw.update(new)
            log.info("подписка на сторонние топики: +%d (всего %d)",
                     len(new), len(subscribed_raw))

    def on_connect(cli, userdata, flags, rc, *args):
        if rc != 0:
            log.error("MQTT: отказ подключения, код %s", rc)
            return
        state.connected = True
        cli.subscribe([("/devices/+/controls/+", 0),
                       ("/devices/+/controls/+/meta", 0),
                       ("/devices/+/controls/+/meta/+", 0),
                       (rpc.reply_topic_filter(), 0)])
        subscribed_raw.clear()
        sync_raw_topics(cli)
        for extra in (mqtt_conf.get("extra_topics") or []):
            cli.subscribe(extra, 0)
            log.info("подписка на маску: %s", extra)
        log.info("MQTT подключён, подписки оформлены")

    def on_disconnect(cli, userdata, rc, *args):
        state.connected = False
        log.warning("MQTT отключён (код %s), paho переподключится сам", rc)

    def on_message(cli, userdata, msg):
        payload = msg.payload.decode("utf-8", "replace")
        if msg.topic.startswith("/rpc/v1/"):
            rpc.on_reply(payload)
        elif lists.on_message(msg.topic, payload):
            pass
        else:
            state.on_message(msg.topic, payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.connect_async(mqtt_conf.get("host", "localhost"),
                         int(mqtt_conf.get("port", 1883)), 60)
    client.loop_start()

    stop = threading.Event()
    threading.Thread(target=history.prefetch_loop, args=(config, stop),
                     daemon=True, name="history").start()

    def watch_config(stop_event):
        """Появились новые сторонние топики в конфиге - подписываемся на лету."""
        while not stop_event.wait(15):
            try:
                config.reload()
                if state.connected:
                    sync_raw_topics(client)
            except Exception:                          # noqa: BLE001
                log.exception("сбой слежения за конфигом")

    threading.Thread(target=watch_config, args=(stop,),
                     daemon=True, name="config").start()

    http = config.get("http", {}) or {}
    host = http.get("host", "127.0.0.1")
    port = int(http.get("port", 8088))
    log.info("HTTP слушает http://%s:%s/  (панели: %s)",
             host, port, ", ".join(sorted(config.panels)) or "нет")
    try:
        from waitress import serve
        serve(app, host=host, port=port,
              threads=int(http.get("threads", 16)), _quiet=True)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
