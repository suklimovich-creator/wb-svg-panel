# -*- coding: utf-8 -*-
"""
Отдельные плитки картинкой: пульт на весь экран (view=pad), раскрытая
плитка, график на весь экран. Выделено из web.py в 1.46.

Общее (app, конфиг, история, шаблоны) - в web.py: конфиг и историю
подменяют main() и проверки, поэтому они читаются как web.config,
web.history, а не копируются сюда при импорте.
"""

import re
import time

from flask import Response, abort, request

from . import web
from . import palette, showcase, wallpaper
from .assemble import build_tile, resolve_sections
from .const import PAD, font_scale
from .geometry import parse_duration
from .render import resolve_css_vars
from .state import state
from .web import _source, _template, app, panel_theme

def panel_tiles(panel_conf, all_panels=None):
    """Плоский список плиток панели в том же порядке, что и на картинке."""
    out = []
    if all_panels is None:
        all_panels = web.config.panels
    for _title, tiles, _status, _key in resolve_sections(panel_conf, all_panels):
        out.extend(tiles)
    return out


@app.route("/tile/<name>/<int:index>.svg")
def tile_svg(name, index):
    """
    Одна плитка крупным планом - для полноэкранного вида.

    Размер приходит в пикселях: страница знает про экран, сервер - нет.
    """
    web.config.reload()
    panel_conf, st, hist, all_panels = _source(name)
    tiles = panel_tiles(panel_conf, all_panels)
    if not 0 <= index < len(tiles):
        abort(404, "плитки %d нет на панели %r" % (index, name))

    try:
        width = max(240, min(int(request.args.get("w") or 700), 3000))
        height = max(200, min(int(request.args.get("h") or 480), 3000))
    except ValueError:
        width, height = 700, 480

    interactive = bool(panel_conf.get("interactive",
                       web.config.get("interactive", False)))
    theme = panel_theme(panel_conf)
    panel_conf = dict(panel_conf, theme=theme)
    conf = tiles[index]
    # Пульт кондиционера на весь экран (с 1.16): плитка во весь размер
    # окна, без полей и скруглений, раскладка - по форме экрана.
    pad = request.args.get("view") == "pad"
    if pad:
        conf = dict(conf, view="pad")
    # Пояснение к зонам графика на весь экран - по кнопке «i» (с 1.35).
    if request.args.get("info") == "1":
        conf = dict(conf, info=True)
    edge = 0 if pad else PAD
    tile = build_tile(conf, 0, edge, edge, width, height,
                      panel_conf, st, hist, interactive)
    scale = font_scale(height)
    svg = _template(panel_conf.get("template", "panel.svg.j2")).render(
        uid="t%d-" % index,
        fs=scale,
        tiles=[tile], headers=[],
        width=width + edge * 2, height=height + edge * 2,
        rx=0 if pad else palette.info(theme)["radius"], theme=theme, shadows=not pad, bare=pad,
        title="", updated=time.strftime("%H:%M"), connected=st.connected)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(
            svg, theme, scale)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.route("/chart.svg")
def chart_svg():
    """
    График одного канала - для чипов строки состояния.

    Плитки под него нет: чип показывает состояние, а история к нему
    приделана сбоку. Поэтому собираем временную плитку типа chart прямо
    здесь, из параметров запроса.
    """
    web.config.reload()
    channel = request.args.get("channel") or ""
    if not channel:
        abort(400, "нужен параметр channel")
    demo = showcase.owns_channel(channel)
    st, hist = (showcase.state, showcase.history) if demo else (state, web.history)
    if not demo and channel not in web.config.used_channels():
        # Тот же принцип, что и у записи в MQTT: рисуем только то, что
        # описано в конфиге. Иначе по адресу можно вытащить историю
        # любого канала контроллера.
        abort(403, "канал %r не используется ни на одной панели" % channel)

    try:
        width = max(240, min(int(request.args.get("w") or 700), 3000))
        height = max(200, min(int(request.args.get("h") or 480), 3000))
    except ValueError:
        width, height = 700, 480

    # История греется в фоне по каналам плиток chart. Канала, который сюда
    # пришёл, там может не быть вовсе - тогда кэш пуст и график выходит
    # пустым. Просим источник прямо сейчас: это разовое действие человека,
    # подождать полсекунды он готов.
    span = parse_duration(request.args.get("range") or "12h")
    if not hist.get(channel, span, 120):
        hist.refresh(channel, span, 120)

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
        "info": request.args.get("info") == "1",
    }
    theme = panel_theme()
    tile = build_tile(conf, 0, PAD, PAD, width, height, {"theme": theme},
                      st, hist, False)
    scale = font_scale(height)
    svg = _template("panel.svg.j2").render(
        uid="c-", fs=scale, tiles=[tile], headers=[],
        width=width + PAD * 2, height=height + PAD * 2,
        rx=palette.info(theme)["radius"], theme=theme, title="", updated=time.strftime("%H:%M"),
        connected=st.connected)
    if request.args.get("flat") not in ("0", "false", "no"):
        svg = resolve_css_vars(svg, theme, scale)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


#: Рисунки для окон (с 1.46): имя -> разбор параметров адреса.
GLYPHS = ("faucet", "puck", "calpage")


@app.route("/glyph/<name>.svg")
def glyph_svg(name):
    """
    Рисунок для окна на весь экран - тем же макросом, что на плитке
    (templates/glyph.svg.j2). Зависит только от адреса, поэтому браузер
    держит его у себя: окно протечки перерисовывается раз в секунду.
    """
    if name not in GLYPHS:
        abort(404)
    args = request.args
    try:
        size = max(16, min(int(args.get("size") or 30), 400))
    except ValueError:
        size = 30
    color = args.get("color") or "#3B8FD4"
    if not re.match(r"^#[0-9A-Fa-f]{3,8}$", color):
        color = "#3B8FD4"
    theme = panel_theme()
    svg = _template("glyph.svg.j2").render(
        uid="p-", fs=1, rx=palette.info(theme)["radius"], theme=theme, name=name,
        size=size, color=color, opened=args.get("open") == "1", wet=args.get("wet") == "1",
        day=str(args.get("day") or "")[:2], wd=str(args.get("wd") or "")[:3])
    return Response(svg.strip(), mimetype="image/svg+xml",
                    headers={"Cache-Control": "max-age=3600"})


@app.route("/wall/<key>.jpg")
def wall_jpg(key):
    """
    Обои стекла своей картинкой (1.50), размытые на сервере. Ключ меняется
    вместе с файлом - браузер держит картинку у себя без срока.
    """
    data = wallpaper.image(key) if re.match(r"^[0-9a-f]{12}$", key) else None
    if data is None:
        abort(404)
    return Response(data, mimetype="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
