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
from .geometry import cells, text_width
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
    tiles, width, height, headers, top_chips = prepare(
        panel_conf, state, history, cols=cols, all_panels=config.panels,
        name=name)
    template = jinja.get_template(panel_conf.get("template", "panel.svg.j2"))
    return template.render(
        # префикс идентификаторов: панель и раскрытая плитка живут в одном
        # документе, и без него их clipPath и градиенты перекрывают друг друга
        uid="p-",
        fs=1,
        tiles=tiles,
        headers=headers,
        top_chips=top_chips,
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
        for _title, chunk, _status in resolve_sections(panel_conf, config.panels):
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
        for _title, tiles, _status in resolve_sections(panel_conf, config.panels):
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
    for _title, tiles, _status in resolve_sections(panel_conf, config.panels):
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

    html_page = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<!-- Запуск без рамки браузера после «Добавить на главный экран».
     mobile-web-app-capable понимают Android-браузеры, apple-* - iOS. -->
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0F0F11">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg">
<link rel="apple-touch-icon" href="icon.svg">
<title>__NAME__</title>
<style>
 /* Страница обычная и прокручиваемая. Раньше тут была раскладка «под киоск»:
    flex по центру, height:100%, overflow:hidden. На телефоне панель влезала
    по высоте и всё выглядело нормально, а на ноутбуке широкий и низкий экран
    ужимал SVG до нуля - плитки просто исчезали. И прокрутки не было вовсе. */
 html,body{margin:0;background:#0F0F11;
           -webkit-tap-highlight-color:transparent;
           font:15px -apple-system,'Segoe UI',Inter,system-ui,sans-serif}
 body{padding:10px;box-sizing:border-box;min-height:100vh}
 #host{color:#fff;margin:0 auto;max-width:1400px}
 /* размер задаётся ШИРИНОЙ: высота подстроится по пропорции сама */
 #host svg{display:block;width:100%;height:auto}
 /* Длинное нажатие в мобильных браузерах выделяет текст и показывает
    системное меню - на пультах это мешало. Запрещаем на всей панели. */
 #host,#ov{-webkit-user-select:none;-moz-user-select:none;-ms-user-select:none;
           user-select:none;-webkit-touch-callout:none}
 [data-topic],[data-expand],[data-pad],[data-href]{cursor:pointer}
 [data-topic].busy,[data-pad].busy{opacity:.45}
 /* выбор числа колонок: значение запоминается и переносится между панелями */
 /* назад к списку панелей: маленькая, полупрозрачная, всегда на месте */
 #back{position:fixed;top:8px;left:8px;z-index:15;text-decoration:none;
       background:rgba(28,28,32,.72);border-radius:12px;color:#fff;
       padding:7px 12px;font-size:13px;opacity:.35}
 #back:hover,#back.touched{opacity:1}
 #cols{position:fixed;top:8px;right:8px;z-index:15;
       background:rgba(28,28,32,.72);border-radius:12px;padding:3px;
       font-size:13px;line-height:1;opacity:.35}
 #cols:hover,#cols.touched{opacity:1}
 #cols b{display:inline-block;padding:6px 9px;border-radius:9px;color:#fff;
         cursor:pointer;font-weight:600}
 #cols b.on{background:#E09B2D;color:#1B1B1F}
 #err{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);
      background:#C4553B;color:#fff;padding:7px 14px;border-radius:9px;
      font-size:13px;display:none;z-index:30}
 /* Порт 8080 - вход без пароля. Пока панель открыта через него, об этом
    должно быть видно: предупреждение в документации читают один раз, а
    плашку - каждый день, пока не уберёшь причину. */
 #warn8080{position:fixed;left:0;right:0;top:0;z-index:40;display:none;
           background:#8A5A1E;color:#FFE6BF;text-align:center;
           padding:6px 10px;font-size:13px;line-height:1.35}
 #warn8080 b{color:#fff}
 #warn8080 a{color:#FFE6BF;text-decoration:underline}
 body.warned{padding-top:38px}
 body.warned #back,body.warned #cols{top:42px}
 /* оверлей: тот же язык, что и у плиток - тёмный фон, скруглённая карточка */
 /* inset и min() - свежий синтаксис, старые десктопные браузеры его не знают
    и оверлей получался нулевого размера: на телефоне работало, на ноутбуке нет.
    Пишем по-старому. */
 #ov{position:fixed;top:0;left:0;right:0;bottom:0;
     background:rgba(10,10,12,.86);display:none;z-index:2147483000;
     align-items:center;justify-content:center;flex-direction:column;
     padding:18px;box-sizing:border-box;overflow:auto}
 /* именно auto по бокам: раньше тут было "7px 0", и это правило перебивало
    margin:0 auto у самих пультов - они прижимались к левому краю */
 #ov.on > *{margin:7px auto}
 #ov.on{display:block;text-align:center}
 #ov .card{background:#F1F1F3;border-radius:22px;padding:14px;max-width:100%;
           display:inline-block;box-sizing:border-box;position:relative;z-index:1}
 #ov svg{display:block;max-width:100%;height:auto}
 #ov .ttl{color:#fff;font-size:17px;font-weight:600}
 #ov .hint{color:rgba(255,255,255,.55);font-size:13px;text-align:center}
 /* переключатель в пульте: без него подвинуть яркость у выключенной лампы
    можно, а увидеть результат - нет */
 #ov .pw{display:inline-flex;align-items:center;padding:10px 18px;
         border-radius:14px;border:2px solid rgba(255,255,255,.3);
         color:#fff;font-size:15px;font-weight:600;cursor:pointer;user-select:none}
 #ov .pw.on{border-color:#E09B2D;color:#E09B2D}
 #ov .pw .led{width:12px;height:12px;border-radius:50%;margin-right:10px;
              background:rgba(255,255,255,.35)}
 #ov .pw.on .led{background:#E09B2D}
 #ov .close{position:fixed;top:16px;right:18px;color:#fff;font-size:30px;
            line-height:1;opacity:.7;cursor:pointer}
 /* пульт ленты: поле «холодный-тёплый» по X, яркость по Y */
 /* размер задаётся из скрипта: min() поддерживают не везде */
 #pad{position:relative;border-radius:20px;touch-action:none;
      overflow:hidden;margin:0 auto}
 #pad .grad{position:absolute;top:0;left:0;right:0;bottom:0}
 #pad .dim{position:absolute;top:0;left:0;right:0;bottom:0;
           background:linear-gradient(to bottom,rgba(10,10,12,0),rgba(10,10,12,.86))}
 #pad .dot{position:absolute;width:30px;height:30px;margin:-15px 0 0 -15px;
           border-radius:50%;border:3px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.4)}
 /* пульт диммера: линейный, заполнение снизу */
 #lvl{position:relative;border-radius:20px;touch-action:none;overflow:hidden;
      background:#2C2C31;margin:0 auto}
 #lvl .fill{position:absolute;left:0;right:0;bottom:0;background:#E0A050;opacity:.75}
 #lvl .val{position:absolute;left:0;right:0;bottom:14px;text-align:center;
           color:#fff;font-size:26px;font-weight:600}
 /* пульт шторы: сверху лежит прозрачный слой для жеста, под ним - та же
    картинка плитки, что и на панели, только крупно */
 /* inline-block: обёртка должна быть ровно по картинке, иначе центр жеста
    окажется в центре экрана, а не в центре окна со шторой */
 #crtwrap{position:relative;display:inline-block;margin:0 auto}
 /* z-index больше, чем у .card (там стоит 1): иначе картинка лежит поверх
    прозрачного слоя и забирает касания себе - жест просто не доходит */
 #crtview{position:absolute;left:0;right:0;top:0;bottom:0;touch-action:none;
          cursor:ew-resize;z-index:3}
 #crtview .mark{position:absolute;top:0;bottom:0;width:2px;display:none;
                background:rgba(255,255,255,.9);
                box-shadow:0 0 12px rgba(0,0,0,.7)}
 #crtview .read{position:absolute;left:0;right:0;top:42%;display:none;
                text-align:center;color:#fff;font-size:42px;font-weight:600;
                text-shadow:0 2px 12px rgba(0,0,0,.8)}
 #ov .crow{display:block;margin:10px auto 0;text-align:center}
 #ov .cbtn{display:inline-block;margin:4px;padding:10px 18px;border-radius:12px;
           border:2px solid rgba(255,255,255,.22);color:rgba(255,255,255,.85);
           font-size:15px;font-weight:600;cursor:pointer}
 #ov .cbtn.stop{border-color:#C4553B;color:#FFD8CE}
 /* пульт кондиционера */
 #wheel{margin:0 auto;user-select:none;touch-action:none}
 #ov .wbig{color:#fff;font-size:64px;font-weight:300;line-height:1;
           letter-spacing:-2px;margin:4px 0 0}
 #ov .wbig .wdeg{font-size:22px;font-weight:500;vertical-align:super;
                 margin-left:4px;letter-spacing:0}
 #ov .acrow{display:block;margin:8px auto;max-width:460px}
 #ov .acbtn{display:inline-block;margin:4px;padding:9px 14px;border-radius:12px;
            border:2px solid rgba(255,255,255,.22);color:rgba(255,255,255,.8);
            font-size:14px;font-weight:600;cursor:pointer}
 #ov .acbtn.on{border-color:#8E6BC4;color:#fff;background:rgba(142,107,196,.22)}
</style></head><body>
<a id="back" href="./">← Панели</a>
<div id="cols"></div>
<div id="host">загрузка…</div>
<div id="err"></div>
<div id="warn8080"></div>
<div id="ov"><span class="close" onclick="closeOv()">&times;</span></div>

<script>
var NAME = __NAME_JS__, EVERY = __EVERY__000;

/* Запасной вход на 8080 не закрыт паролем: кто попал в домашнюю сеть, тот
   управляет светом и шторами. Годится, чтобы попробовать, но не как
   постоянный режим - поэтому и написано «тестовый». */
(function(){
  if (location.port !== '8080') return;
  var w = document.getElementById('warn8080');
  if (!w) return;
  w.innerHTML = '<b>Тестовый режим:</b> открыто через порт 8080, без пароля. ' +
                'Постоянный адрес — <a href="/panel/' + NAME + '.html">/panel/</a>';
  w.style.display = 'block';
  document.body.className += ' warned';
})();

/* Число колонок.
   Берём из адреса, иначе из памяти браузера, иначе оставляем как в конфиге.
   Запоминаем, чтобы при переходе на другую панель раскладка не сбрасывалась:
   у горизонтальных экранов это каждый раз раздражало. */
var STORE = 'wb-panel-cols';
function readCols(){
  var m = location.search.match(/[?&]cols=(\d+)/);
  if (m) return m[1];
  try { return localStorage.getItem(STORE) || ''; } catch(e) { return ''; }
}
var COLS = readCols();

/* Кнопка «назад». Если пришли с другой панели по плитке-ссылке, вернуться
   логичнее туда, а не в общий список: человек проваливался в комнату,
   а не начинал обход заново. Имя панели передаётся параметром from. */
(function(){
  var m = location.search.match(/[?&]from=([\w.-]+)/);
  var back = document.getElementById('back');
  if (!m || !back) return;
  back.href = m[1] + '.html';
  back.textContent = '\u2190 ' + decodeURIComponent(m[1]);
})();
function saveCols(v){
  COLS = v;
  try { v ? localStorage.setItem(STORE, v) : localStorage.removeItem(STORE); } catch(e) {}
}
function Q(){ return COLS ? ('?cols=' + COLS) : ''; }

/* ссылка «назад» тоже должна нести выбранное число колонок */
function fixBack(){
  var b = document.getElementById('back');
  if (b) b.href = './' + Q();
}

function drawCols(){
  fixBack();
  var box = document.getElementById('cols'), html = '';
  var opts = ['', '2', '3', '4', '5', '6', '7', '8', '9'];
  for (var i = 0; i < opts.length; i++) {
    var v = opts[i];
    html += '<b data-v="' + v + '"' + (v === COLS ? ' class="on"' : '') + '>'
          + (v === '' ? 'авто' : v) + '</b>';
  }
  box.innerHTML = html;
  var btns = box.getElementsByTagName('b');
  for (var j = 0; j < btns.length; j++) {
    btns[j].onclick = function(){
      saveCols(this.getAttribute('data-v'));
      drawCols();
      refresh();
    };
  }
  box.className = 'touched';
  setTimeout(function(){ box.className = ''; }, 1800);
}
var INTERACTIVE = __INTERACTIVE__;
var host = document.getElementById('host'), err = document.getElementById('err');
var ov = document.getElementById('ov');
var paused = false, press = null;

/* Координаты события.
   ВАЖНО про touchend: у него список touches ПУСТОЙ - палец уже оторван.
   Пустой TouchList при этом истинный, поэтому наивная проверка
   "ev.touches ? ev.touches[0] : ev" давала undefined и падала на .clientX.
   Из-за этого ползунки двигались, а значение не отправлялось: исключение
   происходило ровно в момент отпускания, до вызова pub(). */
function evPoint(ev){
  if(ev.touches && ev.touches.length) return ev.touches[0];
  if(ev.changedTouches && ev.changedTouches.length) return ev.changedTouches[0];
  return ev;
}

function flash(t){ err.textContent=t; err.style.display='block';
  setTimeout(function(){ err.style.display='none'; }, 2600); }

function pub(topic, value){
  return fetch('api/publish', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({topic:topic, value:String(value)})
  }).then(function(r){
    if(!r.ok) return r.json().then(function(j){ throw new Error(j.error||r.status); });
  });
}

/* ---------- оверлей ---------- */
function closeOv(){ ov.classList.remove('on'); paused=false;
  var k=ov.querySelector('.card,#pad,#lvl,#crtwrap,.ttl,.hint');
  while(ov.children.length>1) ov.removeChild(ov.lastChild); refresh(); }
ov.addEventListener('click', function(e){ if(e.target===ov) closeOv(); });
document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeOv(); });

function openOv(nodes){ 
  while(ov.children.length>1) ov.removeChild(ov.lastChild);
  nodes.forEach(function(n){ ov.appendChild(n); });
  ov.classList.add('on'); paused=true;
}
function el(tag, cls, text){ var e=document.createElement(tag);
  if(cls) e.className=cls; if(text) e.textContent=text; return e; }

/* Переключатель для пульта. Возвращает объект с текущим состоянием, чтобы
   ползунки знали, надо ли сперва включить прибор. */
function powerRow(node){
  var st = {on: node.getAttribute('data-state') === '1'};
  var row = el('div', 'pw');
  var led = el('span', 'led'), label = el('span');
  row.appendChild(led); row.appendChild(label);
  function paint(){
    row.classList.toggle('on', st.on);
    label.textContent = st.on ? 'Включено' : 'Выключено';
  }
  paint();
  row.onclick = function(){
    var topic = node.getAttribute('data-topic');
    if(!topic) return;
    var next = !st.on;
    pub(topic, next ? node.getAttribute('data-on') : node.getAttribute('data-off'))
      .then(function(){ st.on = next; paint(); })
      .catch(function(e){ flash('Не удалось: ' + e.message); });
  };
  st.el = row;
  // включить прибор, если крутят яркость у выключенного
  st.ensureOn = function(){
    var topic = node.getAttribute('data-topic');
    if(st.on || !topic) return Promise.resolve();
    return pub(topic, node.getAttribute('data-on')).then(function(){
      st.on = true; paint();
    });
  };
  return st;
}

/* ---------- график на весь экран ---------- */
function expand(node){
  var i = node.getAttribute('data-i');
  var w = Math.min(Math.round(window.innerWidth * 0.90), 1400);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 900);
  var url = 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h;
  var card = el('div','card');
  card.textContent = '…';
  var hint = el('div','hint','Нажмите вне карточки, чтобы закрыть');
  openOv([card, hint]);
  // если оверлей по какой-то причине не показался, откроем картинку отдельно
  if(!ov.offsetHeight){ closeOv(); window.open(url, '_blank'); return; }
  fetch(url, {cache:'no-store'})
    .then(function(r){ if(!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
    .then(function(svg){
      if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
      card.innerHTML = svg;
    })
    .catch(function(e){
      card.textContent = 'Не удалось: ' + e.message;
      flash('Полный экран: ' + e.message);
    });
}

/* ---------- пульт ленты: X - температура, Y - яркость ---------- */
function padXY(node){
  var pad = el('div'); pad.id='pad';
  var side = Math.round(Math.min(window.innerWidth*0.78, window.innerHeight*0.6));
  pad.style.width = side + 'px'; pad.style.height = side + 'px';
  var grad = el('div','grad'), dim = el('div','dim'), dot = el('div','dot');
  grad.style.background = 'linear-gradient(to right,' +
      node.getAttribute('data-cold') + ',' + node.getAttribute('data-warm') + ')';
  pad.appendChild(grad); pad.appendChild(dim); pad.appendChild(dot);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Подсветка');
  var hint = el('div','hint','Слева холодный, справа тёплый · вверх ярче');
  var power = powerRow(node);
  var x = parseFloat(node.getAttribute('data-dot-x')||'0.5');
  var y = parseFloat(node.getAttribute('data-dot-y')||'0.5');
  function place(){ dot.style.left=(x*100)+'%'; dot.style.top=(y*100)+'%';
    dot.style.background = 'transparent'; }
  place();
  openOv([title, pad, power.el, hint]);

  function at(ev){
    var r = pad.getBoundingClientRect();
    var p = evPoint(ev);
    x = Math.max(0, Math.min(1, (p.clientX-r.left)/r.width));
    y = Math.max(0, Math.min(1, (p.clientY-r.top)/r.height));
    place();
  }
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    var bright = Math.round((1-y)*bmax);
    var jobs = [pub(node.getAttribute('data-bright'), bright)];
    var tTopic = node.getAttribute('data-temp');
    if(tTopic){
      var unit = node.getAttribute('data-temp-unit');
      var tmax = parseFloat(node.getAttribute('data-temp-max')||'100');
      var lo = parseFloat(node.getAttribute('data-temp-lo')||'2700');
      var hi = parseFloat(node.getAttribute('data-temp-hi')||'6500');
      // слева холодный: x=0 -> hi кельвинов
      var value = (unit === 'percent') ? Math.round((1-x)*tmax)
                                       : Math.round(hi - x*(hi-lo));
      jobs.push(pub(tTopic, value));
    }
    // если лента выключена, сперва включаем - иначе крутить бесполезно
    power.ensureOn()
      .then(function(){ return Promise.all(jobs); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  drag(pad, at, send);
}

/* ---------- пульт кондиционера ----------
   Колесо сверху: шкала едет под неподвижной меткой по центру, как в
   приложении. Тянешь влево - значения растут. Ниже режимы, питание и
   скорость вентилятора. */
function padAC(node){
  var lo = parseFloat(node.getAttribute('data-ac-lo')||'16');
  var hi = parseFloat(node.getAttribute('data-ac-hi')||'30');
  var step = parseFloat(node.getAttribute('data-ac-step')||'1');
  // pos - непрерывное положение шкалы, temp - значение, которое отправим.
  // Раньше шкала перерисовывалась от округлённого значения, из-за чего
  // засечки всегда попадали в те же места и колесо казалось неподвижным.
  var temp = parseFloat(node.getAttribute('data-ac-now'));
  if(isNaN(temp)) temp = Math.round((lo+hi)/2);
  var pos = temp;

  var title = el('div','ttl', node.getAttribute('data-title') || 'Кондиционер');
  var cur = node.getAttribute('data-ac-cur');
  // Крупно сверху - ФАКТИЧЕСКАЯ температура: уставку и так видно на шкале,
  // дублировать её незачем.
  var big = el('div','wbig');
  big.innerHTML = (cur || '--').replace('°','') + '<span class="wdeg">°C</span>';
  var sub = el('div','hint','в комнате · уставку задайте колесом');

  var W = Math.min(window.innerWidth - 30, 470), H = 190;
  var wrap = el('div'); wrap.id = 'wheel';
  wrap.style.width = W + 'px'; wrap.style.height = H + 'px';
  wrap.innerHTML =
      '<svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '">'
    + '<path class="warc2" fill="none" stroke="rgba(255,255,255,.14)" stroke-width="1.5"/>'
    + '<path class="wnotch" fill="none" stroke="rgba(255,255,255,.45)" stroke-width="1.6"/>'
    + '<g class="wticks"></g>'
    + '<path class="warc" fill="none" stroke="#B08BE8" stroke-width="3"/>'
    + '</svg>';
  // ВАЖНО: узлы ещё не в документе, поэтому ищем внутри wrap.
  // Через document.getElementById здесь приходит null, и пульт падал молча.
  var gTicks = wrap.querySelector('.wticks');
  var pArc   = wrap.querySelector('.warc');
  var pArc2  = wrap.querySelector('.warc2');
  var pNotch = wrap.querySelector('.wnotch');

  /* Дуга «улыбкой»: центр окружности НАД картинкой, поэтому середина шкалы
     внизу, а края поднимаются - как в приложении. Раньше центр был снизу и
     дуга выгибалась в другую сторону. */
  var PXDEG = W / 5, R = W * 1.7, CX = W / 2, BOTTOM = H - 34, CY = BOTTOM - R;
  function pt(delta, radius){
    var a = (delta * PXDEG) / R;
    var r = radius === undefined ? R : radius;
    return [CX + r * Math.sin(a), CY + r * Math.cos(a), a];
  }
  function arcPath(radius, half){
    var p0 = pt(-half, radius), p1 = pt(half, radius);
    return 'M ' + p0[0].toFixed(1) + ',' + p0[1].toFixed(1)
         + ' A ' + radius.toFixed(0) + ',' + radius.toFixed(0)
         + ' 0 0 0 ' + p1[0].toFixed(1) + ',' + p1[1].toFixed(1);
  }
  function draw(){
    var s = '', v, from = Math.ceil((pos - 3.2) * 5) / 5;
    for(v = from; v <= pos + 3.2; v += 0.2){
      if(v < lo - 0.001 || v > hi + 0.001) continue;
      var p = pt(v - pos), whole = Math.abs(v - Math.round(v)) < 0.05;
      var len = whole ? 12 : 7;
      // засечки смотрят наружу от центра окружности, то есть вниз
      var x2 = p[0] + Math.sin(p[2]) * len, y2 = p[1] + Math.cos(p[2]) * len;
      s += '<line x1="' + p[0].toFixed(1) + '" y1="' + p[1].toFixed(1)
         + '" x2="' + x2.toFixed(1) + '" y2="' + y2.toFixed(1)
         + '" stroke="#fff" stroke-width="' + (whole ? 1.7 : 1)
         + '" opacity="' + (whole ? .7 : .3) + '"/>';
      if(whole){
        // подписи с внутренней стороны дуги, то есть выше неё
        var lx = p[0] - Math.sin(p[2]) * 20, ly = p[1] - Math.cos(p[2]) * 20;
        var near = Math.abs(v - temp) < 0.05;
        s += '<text x="' + lx.toFixed(1) + '" y="' + ly.toFixed(1)
           + '" text-anchor="middle" font-size="' + (near ? 19 : 16)
           + '" fill="rgba(255,255,255,' + (near ? .92 : .45) + ')">'
           + Math.round(v) + '°</text>';
      }
    }
    gTicks.innerHTML = s;
    pArc.setAttribute('d', arcPath(R, 3.2));
    pArc2.setAttribute('d', arcPath(R - 74, 2.6));
    var n = pt(0, R - 74);
    pNotch.setAttribute('d', 'M ' + (n[0] - 8) + ',' + (n[1] - 9)
                           + ' L ' + n[0] + ',' + (n[1] + 1)
                           + ' L ' + (n[0] + 8) + ',' + (n[1] - 9));
  }
  draw();

  var startX = null, startPos = pos;
  function begin(ev){
    startX = evPoint(ev).clientX; startPos = pos;
  }
  function at(ev){
    if(startX === null) begin(ev);
    pos = startPos - (evPoint(ev).clientX - startX) / PXDEG;  // влево - теплее
    pos = Math.max(lo, Math.min(hi, pos));
    temp = Math.max(lo, Math.min(hi, Math.round(pos / step) * step));
    draw();
  }
  function send(){
    startX = null;
    pos = temp;                       // защёлкиваем шкалу на выбранном делении
    draw();
    var t = node.getAttribute('data-ac-target');
    if(t) pub(t, temp).catch(function(e){ flash('Не удалось: ' + e.message); });
  }
  drag(wrap, at, send, begin);

  function chooser(list, attr, nowValue){
    var row = el('div','acrow');
    list.forEach(function(item){
      var b = el('div', 'acbtn' + (item[0] === nowValue ? ' on' : ''), item[1]);
      b.onclick = function(){
        var topic = node.getAttribute(attr);
        if(!topic) return;
        pub(topic, item[0]).then(function(){
          var all = row.querySelectorAll('.acbtn');
          for(var i = 0; i < all.length; i++) all[i].classList.remove('on');
          b.classList.add('on');
        }).catch(function(e){ flash('Не удалось: ' + e.message); });
      };
      row.appendChild(b);
    });
    return row;
  }

  var modes = chooser([['cool','Охлаждение'],['heat','Нагрев'],['dry','Осушение'],
                       ['fan_only','Вентиляция'],['auto','Авто']],
                      'data-ac-mode', node.getAttribute('data-ac-mode-now'));
  var fans = chooser([['low','1'],['medium','2'],['high','3'],['auto','Авто']],
                     'data-ac-fan', node.getAttribute('data-ac-fan-now'));
  var power = powerRow(node);
  var parts = [title, big, wrap, sub, modes,
               el('div','hint','Скорость вентилятора'), fans];
  if(node.getAttribute('data-ac-swing')){
    parts.push(el('div','hint','Лопасти вертикальные'));
    parts.push(chooser([['auto','Авто'],['off','Выкл'],['upper','Верх'],
                        ['position_2','2'],['position_3','3'],['position_4','4'],
                        ['bottom','Низ']],
                       'data-ac-swing', node.getAttribute('data-ac-swing-now')));
  }
  if(node.getAttribute('data-ac-swing-h')){
    parts.push(el('div','hint','Лопасти горизонтальные'));
    // кнопки идут слева направо, как реальные положения: 4·5·1·6·7
    parts.push(chooser([['auto','Авто'],['position_4','◀◀'],['position_5','◀'],
                        ['position_1','●'],['position_6','▶'],['position_7','▶▶']],
                       'data-ac-swing-h', node.getAttribute('data-ac-swing-h-now')));
  }
  parts.push(power.el);
  openOv(parts);
}

/* ---------- пульт диммера: линейный ---------- */
function padLevel(node){
  var box = el('div'); box.id='lvl';
  box.style.width = Math.round(Math.min(window.innerWidth*0.5, 220)) + 'px';
  box.style.height = Math.round(Math.min(window.innerHeight*0.5, 380)) + 'px';
  var fill = el('div','fill'), val = el('div','val');
  box.appendChild(fill); box.appendChild(val);
  var title = el('div','ttl', node.getAttribute('data-title') || 'Яркость');
  var hint = el('div','hint','Ведите вверх или вниз');
  var power = powerRow(node);
  var lvl = parseFloat(node.getAttribute('data-level')||'0');
  function place(){ fill.style.height=(lvl*100)+'%'; val.textContent=Math.round(lvl*100)+' %'; }
  place();
  openOv([title, box, power.el, hint]);
  function at(ev){
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    lvl = Math.max(0, Math.min(1, 1-(p.clientY-r.top)/r.height));
    place();
  }
  function send(){
    var bmax = parseFloat(node.getAttribute('data-bright-max')||'100');
    power.ensureOn()
      .then(function(){ return pub(node.getAttribute('data-bright'), Math.round(lvl*bmax)); })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  drag(box, at, send);
}

/* Пульт шторы.
   Полоса задаёт положение, кнопки под ней - «закрыть / стоп / открыть».
   Пока палец ведёт, показываем то, что он выбрал; отпустил - отправляем
   одно значение. Промежуточные значения слать нельзя: у привода каждая
   команда это новая цель, и он дёргался бы за пальцем. */
function padCurtain(node){
  /* Раскрытая штора - это та же плитка, только крупно: рисует её сервер тем
     же макросом, что и на панели. Своей вёрстки тут нет намеренно, иначе
     пришлось бы поддерживать два разных изображения одной вещи.
     Поверх картинки лежит прозрачный слой: ведём правую створку от центра
     к правому краю, как саму штору руками. */
  var i = node.getAttribute('data-i');
  var w = Math.min(Math.round(window.innerWidth * 0.90), 1400);
  var h = Math.min(Math.round(window.innerHeight * 0.62), 900);
  var url = 'tile/' + encodeURIComponent(NAME) + '/' + i + '.svg?w=' + w + '&h=' + h;

  var card = el('div','card'); card.textContent = '…';
  var wrap = el('div'); wrap.id = 'crtwrap';
  var view = el('div'); view.id = 'crtview';
  var mark = el('div','mark');            // куда поедет
  var read = el('div','read');            // проценты крупно
  view.appendChild(mark); view.appendChild(read);
  wrap.appendChild(card); wrap.appendChild(view);

  var title = el('div','ttl', node.getAttribute('data-title') || 'Штора');
  var hint  = el('div','hint','Ведите правую створку от центра к краю');
  var pos   = parseFloat(node.getAttribute('data-pos')||'0');
  var shown = pos;

  function paint(){
    // 0 - створка у центра (закрыто), 1 - у правого края (открыто)
    var svg = card.querySelector('svg');
    if(svg){
      var rs = svg.getBoundingClientRect(), rv = view.getBoundingClientRect();
      var left = rs.left - rv.left, mid = left + rs.width/2;
      mark.style.left = Math.round(mid + shown*rs.width/2) + 'px';
    } else {
      mark.style.left = (50 + shown*50) + '%';
    }
    read.textContent = Math.round(shown*100) + ' %';
    mark.style.display = read.style.display = dragging ? 'block' : 'none';
  }
  var dragging = false;

  function load(){
    fetch(url + '&t=' + Date.now(), {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); })
      .then(function(svg){
        if(svg.indexOf('<svg') < 0) throw new Error('пустой ответ');
        card.innerHTML = svg;
      })
      .catch(function(e){ card.textContent = 'Не удалось: ' + e.message; });
  }
  load();

  var row = el('div','crow');
  function button(text, cls, fn){
    var b = el('span','cbtn'+(cls?' '+cls:''), text);
    b.onclick = fn; row.appendChild(b); return b;
  }
  function send(value){
    pub(node.getAttribute('data-topic'), value)
      .then(function(){
        setTimeout(load, 700); setTimeout(load, 2500); setTimeout(load, 6000);
        setTimeout(refresh, 800);
      })
      .catch(function(e){ flash('Не удалось: '+e.message); });
  }
  button('Закрыть', '', function(){ send(node.getAttribute('data-off')); });
  /* Стоп есть не у всех приводов: у HomeKit-совместимых это отдельная
     характеристика, и топик задаётся в конфиге явно. Нет топика - нет кнопки:
     рисовать неработающую хуже, чем не рисовать вовсе. */
  var stopTopic = node.getAttribute('data-stop');
  if(stopTopic){
    button('Стоп', 'stop', function(){
      pub(stopTopic, node.getAttribute('data-stop-value') || '2')
        .then(function(){ setTimeout(load, 700); setTimeout(load, 2000); })
        .catch(function(e){ flash('Не удалось: '+e.message); });
    });
  }
  button('Открыть', '', function(){ send(node.getAttribute('data-on')); });

  openOv([title, wrap, row, hint]);
  if(!ov.offsetHeight){ closeOv(); window.open(url, '_blank'); return; }

  function at(ev){
    // Считаем от самого рисунка: у карточки есть поля, и если брать её
    // границы, центр окна и центр жеста разъезжаются на ширину поля.
    var box = card.querySelector('svg') || view;
    var r = box.getBoundingClientRect();
    var p = evPoint(ev);
    // центр картинки = створки сомкнуты, правый край = открыто настежь
    shown = Math.max(0, Math.min(1, (p.clientX - (r.left + r.width/2)) / (r.width/2)));
    paint();
  }
  function begin(ev){ dragging = true; at(ev); }
  function done(){
    dragging = false; paint();
    pos = shown;
    // Промежуточные значения не шлём: для привода каждое - новая цель,
    // и он дёргался бы за пальцем.
    send(Math.round(shown*100));
  }
  drag(view, at, done, begin);
}

/* Перетаскивание: pointer-события есть не везде, поэтому с запасным
   вариантом на мышь и касания. */
function drag(node, move, done, begin){
  var active = false;
  function start(e){
    active = true;
    try { if(e.pointerId != null && node.setPointerCapture) node.setPointerCapture(e.pointerId); }
    catch(_) {}
    if(begin) begin(e); else move(e);
    if(e.preventDefault) e.preventDefault();
  }
  function go(e){ if(active){ move(e); if(e.preventDefault) e.preventDefault(); } }
  function end(e){
    if(!active) return;
    active = false;
    // move() может споткнуться на кривом событии - но отправить значение
    // мы обязаны в любом случае, иначе жест пропадает впустую
    try { move(e); } catch(_) {}
    done();
  }
  /* Отмена жеста - это НЕ движение. Раньше здесь вызывался move() с событием
     pointercancel, у которого координат нет, и значение прыгало в край.
     Браузер шлёт отмену, стоит пальцу чуть сместиться по вертикали. */
  function cancel(){ if(!active) return; active = false; done(); }
  if(window.PointerEvent){
    node.addEventListener('pointerdown', start);
    node.addEventListener('pointermove', go);
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', cancel);
  } else {
    node.addEventListener('mousedown', start);
    document.addEventListener('mousemove', go);
    document.addEventListener('mouseup', end);
    node.addEventListener('touchstart', start);
    node.addEventListener('touchmove', go);
    node.addEventListener('touchend', end);
    node.addEventListener('touchcancel', cancel);
  }
}

/* ---------- нажатия по плиткам ---------- */
function toggle(node){
  var value = node.getAttribute('data-state')==='1'
            ? node.getAttribute('data-off') : node.getAttribute('data-on');
  node.classList.add('busy');
  pub(node.getAttribute('data-topic'), value).then(function(){
    setTimeout(refresh, 400); setTimeout(refresh, 1200);
  }).catch(function(e){ flash('Не удалось: '+e.message); node.classList.remove('busy'); });
}

function bind(){
  if(!INTERACTIVE) return;
  var nodes = host.querySelectorAll('[data-topic],[data-expand],[data-pad],[data-href]');
  for(var i=0;i<nodes.length;i++){
    var n = nodes[i];
    if(n.getAttribute('data-expand')){ n.onclick=function(){ expand(this); }; continue; }
    // короткое нажатие переключает, долгое открывает пульт
    // Короткое нажатие переключает, долгое открывает пульт.
    // Раньше случайные включения ловились из-за двух вещей: жест не
    // отменялся при сдвиге пальца (прокрутка засчитывалась как нажатие) и
    // порог был коротковат. Теперь следим за смещением и держим 600 мс.
    var down = function(e){
      var self = this, p = evPoint(e);
      press = {node:self, long:false, x:p.clientX, y:p.clientY, moved:false,
        timer:setTimeout(function(){
          if(press && !press.moved){ press.long = true; openPad(self); }
        }, 600)};
    };
    var moved = function(e){
      if(!press) return;
      var p = evPoint(e);
      if(Math.abs(p.clientX - press.x) > 12 || Math.abs(p.clientY - press.y) > 12){
        press.moved = true;
        clearTimeout(press.timer);
      }
    };
    var up = function(){
      if(!press) return;
      clearTimeout(press.timer);
      var href = this.getAttribute('data-href');
      if(!press.long && !press.moved){
        if(href) { location.href = href + Q(); }
        else if(this.getAttribute('data-topic')) toggle(this);
      }
      press = null;
    };
    var cancel = function(){ if(press){ clearTimeout(press.timer); press=null; } };
    if(window.PointerEvent){
      n.onpointerdown = down; n.onpointermove = moved;
      n.onpointerup = up; n.onpointercancel = cancel;
    } else {
      n.onmousedown = down; n.onmousemove = moved;
      n.onmouseup = up; n.onmouseleave = cancel;
      n.ontouchstart = down; n.ontouchmove = moved;
      n.ontouchend = up; n.ontouchcancel = cancel;
    }
    // долгое нажатие не должно выделять текст и звать меню браузера
    n.oncontextmenu = function(e){ if(e.preventDefault) e.preventDefault(); return false; };
  }
}
function openPad(node){
  var pad = node.getAttribute('data-pad');
  if(pad==='xy') padXY(node);
  else if(pad==='level') padLevel(node);
  else if(pad==='curtain') padCurtain(node);
  else if(pad==='ac') padAC(node);
}

/* ---------- обновление ---------- */
function refresh(){
  if(paused) return;
  var q = Q();
  fetch(NAME + '.svg' + q + (q ? '&' : '?') + 't=' + Date.now(), {cache:'no-store'})
    .then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); })
    .then(function(svg){
      if(svg.indexOf('<svg')<0) throw new Error('пустой ответ');
      host.innerHTML = svg; bind(); err.style.display='none';
    })
    .catch(function(e){
      if(host.querySelector('svg')) return;
      host.textContent = 'Не удалось загрузить панель: ' + e.message;
    });
}
/* Экран не должен гаснуть.
   Сперва пробуем штатный способ, он есть в свежих браузерах. Если его нет -
   заводим беззвучное зацикленное видео: пока что-то воспроизводится, Android
   держит экран включённым. Приём старый, работает и на Android 5. */
var wakeLock = null;
function keepAwake(){
  if (navigator.wakeLock && navigator.wakeLock.request) {
    navigator.wakeLock.request('screen').then(function(l){
      wakeLock = l;
      l.addEventListener('release', function(){ wakeLock = null; });
    }).catch(function(){ videoAwake(); });
  } else {
    videoAwake();
  }
}
function videoAwake(){
  if (document.getElementById('nosleep')) return;
  var v = document.createElement('video');
  v.id = 'nosleep';
  v.setAttribute('playsinline', ''); v.setAttribute('muted', '');
  v.setAttribute('loop', ''); v.muted = true; v.loop = true;
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none';
  // односекундный чёрный кадр, меньше килобайта
  v.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAAr1tZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE0OCByMjY0MyA1YzY1NzA0IC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAxNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbAAAAAFliIQA';
  document.body.appendChild(v);
  var p = v.play();
  if (p && p.catch) p.catch(function(){ /* нужен жест пользователя */ });
  // если браузер потребовал жест - запустим при первом касании
  document.addEventListener('touchstart', function once(){
    v.play(); document.removeEventListener('touchstart', once);
  });
}
keepAwake();
document.addEventListener('visibilitychange', function(){
  if (!document.hidden && !wakeLock) keepAwake();
});

/* Полный экран по двойному касанию - на случай, если страница открыта
   обычной вкладкой, а не с главного экрана. */
document.addEventListener('dblclick', function(){
  var el = document.documentElement;
  var fn = el.requestFullscreen || el.webkitRequestFullscreen ||
           el.webkitRequestFullScreen || el.mozRequestFullScreen;
  if (fn) fn.call(el);
});

drawCols();
refresh();
setInterval(refresh, EVERY);
</script></body></html>"""
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
