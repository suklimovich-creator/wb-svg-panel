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
from .tiles import BUILDERS, command_topic
from .status import status_entries
from . import assemble
from .assemble import (build_tile, cmd_attrs, prepare,
                       resolve_sections, tile_command, xml_escape)
from .render import resolve_css_vars
import html
import threading


app = Flask(__name__)


config = None


mqtt_client = None




history = None


jinja = None


def render_panel(name, cols=None):
    config.reload()
    state.set_watched(config.used_channels())
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

    Список строится из конфига: только те командные топики, которые
    соответствуют нарисованным плиткам. Иначе панель превратилась бы в
    открытый шлюз в MQTT - а брокер на Wiren Board по умолчанию без пароля,
    и писать в него можно что угодно, включая реле котла.
    """
    allowed = set()
    for panel_conf in config.panels.values():
        interactive = bool((panel_conf or {}).get("interactive",
                           config.get("interactive", False)))
        if not interactive:
            continue
        for _title, tiles, _status, _key in resolve_sections(panel_conf, config.panels):
            for tile in tiles:
                kind = tile.get("type")
                pairs = []
                if kind in ("switch", "dimmer", "curtain"):
                    pairs.append((tile.get("channel"), tile.get("command_topic")))
                elif kind == "light":
                    pairs.append((tile.get("channel_switch"), tile.get("command_topic")))
                if kind == "ac" and tile.get("device"):
                    for name in ("power", "mode", "target_temp", "fan_mode",
                                 "quiet", "turbo", "preset",
                                 "swing_mode", "swing_h_mode"):
                        allowed.add("/devices/%s/controls/%s/on"
                                    % (tile["device"], name))
                if kind == "thermostat":
                    # Уставка и, если задан, переключатель режима. Оба
                    # топика для сырых каналов моста задаются явно -
                    # вывести их из имени канала нельзя.
                    pairs.append((tile.get("channel_target"),
                                  tile.get("command_topic")))
                    if tile.get("command_topic_mode"):
                        allowed.add(tile["command_topic_mode"])
                if kind in ("light", "dimmer"):
                    pairs.append((tile.get("channel_brightness"),
                                  tile.get("command_topic_brightness")))
                if kind == "light":
                    pairs.append((tile.get("channel_temp"),
                                  tile.get("command_topic_temp")))
                # «Стоп» у шторы - отдельный топик, не выводимый из канала
                # положения. Без него пульт получал бы 403 на кнопку остановки.
                if kind == "curtain" and tile.get("command_topic_stop"):
                    allowed.add(tile["command_topic_stop"])
                for ch, explicit in pairs:
                    topic = command_topic(ch, explicit)
                    if topic:
                        allowed.add(topic)
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


@app.route("/<name>.svg")
def panel_svg(name):
    config.reload()
    # /main.svg?cols=2 - та же панель в две колонки, для телефона.
    # Отдельную панель в конфиге заводить не нужно.
    try:
        cols = int(request.args.get("cols") or 0) or None
    except ValueError:
        cols = None
    if cols:
        cols = max(1, min(cols, 12))
    with state.lock:
        version = state.version
    # v2 в ключе: после смены формата страницы старые записи в кэше браузера
    # не должны совпадать с новыми
    etag = '"v2-%s-%s-%d"' % (name, cols or "d", version)
    if request.headers.get("If-None-Match") == etag:
        return Response(status=304, headers={"ETag": etag})
    svg = render_panel(name, cols=cols)
    if request.args.get("flat") not in ("0", "false", "no"):
        panel_conf = config.panels.get(name) or {}
        svg = resolve_css_vars(
            svg, panel_conf.get("theme", config.get("theme", "light")), 1.0)
    return Response(svg, mimetype="image/svg+xml", headers={
        "ETag": etag,
        "Cache-Control": "no-cache, must-revalidate",
        "Access-Control-Allow-Origin": "*",
    })


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
    state.set_watched(config.used_channels())

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
    rpc = MqttRpc(client, client_id)
    hconf = config.get("history", {}) or {}
    history = History(rpc,
                      db_path=hconf.get("db_path", DB_PATH),
                      ttl=int(hconf.get("cache_ttl", 60)),
                      prefer=hconf.get("source", "auto"))

    subscribed_raw = set()

    def sync_raw_topics(cli):
        """Подписаться на топики сторонних шлюзов, упомянутые в конфиге."""
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
        serve(app, host=host, port=port, threads=8, _quiet=True)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
