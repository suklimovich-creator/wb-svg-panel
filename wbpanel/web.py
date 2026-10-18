# -*- coding: utf-8 -*-
"""
HTTP: приложение, сжатие ответов, сборка панели (render_panel), точка
входа main(). Маршруты - по модулям (с 1.46): web_live (картинка панели и
ожидание изменений), web_tiles (плитки и график), web_api (публикация,
списки, камеры), web_pages (страницы).
"""

import gzip
import json
import logging
import os
import sys
import threading

from flask import Flask, Response, abort, request
from jinja2 import Environment, FileSystemLoader

from . import assemble, cameras, palette, showcase, stats, themes, wallpaper
from .assemble import prepare
from .calendars import calendars
from .config import Config
from .const import BASE_DIR, CONFIG_PATH, DB_PATH, TEMPLATE_DIRS, log
from .geometry import clock_now
from .history import History
from .lists import lists
from .render import resolve_css_vars  # noqa: F401 - web.resolve_css_vars для проверок и tools/
from .state import MqttRpc, make_client, state
from .water import water


# Стили и скрипты страницы - файлы static/ (с 1.45): адрес несёт версию
# (?v=), поэтому браузер может держать их у себя.
app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"),
            static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600

#: Что сжимать (с 1.45): панель - сотня килобайт текста, и каждое изменение
#: датчика гнало её по сети целиком. Сжатая - в 6-8 раз меньше.
GZIP_TYPES = ("image/svg+xml", "text/html", "application/json", "text/css",
              "application/javascript", "text/javascript", "text/plain")
GZIP_MIN = 1400


@app.after_request
def _gzip(resp):
    """Сжать ответ, если браузер умеет. Поток камеры и короткие - как есть."""
    try:
        # Файл из static/ Flask отдаёт потоком - для сжатия читаем целиком.
        # Остальные потоки (камера) не трогаем.
        static = request.path.startswith("/static/")
        if static and resp.status_code == 200 and resp.mimetype in GZIP_TYPES:
            resp.direct_passthrough = False
        if ((not static and (resp.direct_passthrough or resp.is_streamed))
                or resp.status_code != 200
                or "Content-Encoding" in resp.headers
                or resp.mimetype not in GZIP_TYPES
                or "gzip" not in (request.headers.get("Accept-Encoding") or "")):
            return resp
        data = resp.get_data()
        if len(data) < GZIP_MIN:
            return resp
        # Уровень 4: на контроллере быстрее шестого раза в полтора при
        # почти том же размере.
        resp.set_data(gzip.compress(data, 4))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Vary"] = "Accept-Encoding"
    except Exception:  # noqa: BLE001 - сжатие не должно ронять ответ
        log.exception("сжатие ответа")
    return resp


config = None


mqtt_client = None


history = None


jinja = None


def _template(name):
    """Шаблон с общими для всех глобалами: PAL - данные тем (с 1.24)."""
    jinja.globals.setdefault("PAL", palette)
    return jinja.get_template(name)


def themes_dir():
    """Папка своих тем: themes_dir из конфига или themes/ рядом с ним."""
    path = (config.get("themes_dir") if config else None) or "themes"
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), path)


def panel_theme(panel_conf=None):
    """
    Тема для этого ответа: ?theme= из адреса (выбор на устройстве - у
    телефона и планшета на стене он свой), иначе тема панели, иначе общая.
    Неизвестное имя в адресе молча игнорируется.
    """
    palette.refresh()
    asked = request.args.get("theme") if request else None
    if asked and palette.exists(asked):
        return asked
    # theme: auto - по расписанию theme_auto (день/ночь, восход/закат).
    return themes.resolve((panel_conf or {}).get("theme", config.get("theme", "light")),
                          config)


def _source(name):
    """Откуда панель: (конфиг, состояние, история, все панели). Витрина тем
    (/themes) живёт на своём состоянии, а не на приборах из MQTT."""
    if showcase.is_demo(name):
        return showcase.panel(), showcase.state, showcase.history, {}
    panel_conf = config.panels.get(name)
    if panel_conf is None:
        abort(404, "панель %r не найдена в конфиге" % name)
    return panel_conf, state, history, config.panels


def render_panel(name, cols=None):
    config.reload()
    state.set_watched(config.used_channels(), config.service_prefixes())
    panel_conf, st, hist, all_panels = _source(name)
    # Свёрнутые разделы приходят в адресе: панель - цельная картинка с
    # посчитанной геометрией, спрятать плитки на стороне браузера нельзя,
    # останутся дыры. Список свой на каждом устройстве, страница хранит
    # его рядом с числом колонок.
    closed = [p for p in (request.args.get("closed") or "").split(",") if p]
    opened = request.args.get("open") or None
    # Вид разделов на этом устройстве (с 1.36): list, accordion или tabs.
    # Нет - как в конфиге панели.
    mode = request.args.get("mode") or None
    # Кнопки страницы в шапке (1.44): просит только страница-обёртка.
    # Картинка сама по себе (киоск, img, дисплей) рисуется без них.
    # Значение - во сколько раз крупнее рисовать кнопки: панель на 4
    # колонки на телефоне ужата вдвое, и кружок в 36 точек становился
    # мишенью в полтора десятка пикселей. Страница считает это сама.
    try:
        nav = min(1.6, max(1.0, float(request.args.get("nav") or 0))) \
            if request.args.get("nav") else 0
    except ValueError:
        nav = 1.0
    # «Облегчённо» (1.45) - выбор устройства в меню шестерёнки: без теней,
    # без линзы и отсвета стекла. Для слабых планшетов.
    lite = request.args.get("lite") == "1"

    theme = panel_theme(panel_conf)
    # Тема нужна и сборке: подписи графиков красятся под цвет подложки.
    panel_conf = dict(panel_conf, theme=theme)
    tiles, width, height, headers, top_chips, sect = prepare(
        panel_conf, st, hist, cols=cols, all_panels=all_panels,
        name=name, closed=closed, opened=opened, mode=mode, nav=nav)
    template = _template(panel_conf.get("template", "panel.svg.j2"))
    wall_img, wall_dim = wall_of(panel_conf, theme)
    return template.render(
        wall_img=wall_img,
        wall_dim=wall_dim,
        tab_nav=sect.get("tab_nav", False),
        # префикс идентификаторов: панель и раскрытая плитка живут в одном
        # документе, и без него их clipPath и градиенты перекрывают друг друга
        uid="p-",
        fs=1,
        tiles=tiles,
        headers=headers,
        top_chips=top_chips,
        accordion=sect["mode"] == "accordion",
        section_mode=sect["mode"],
        tabs=sect["tabs"],
        tab_plate=sect["tab_plate"],
        tab_dock=sect["dock"],
        head_icon=sect["head_icon"],
        head_dy=sect["head_dy"],
        nav_w=sect["nav_w"],
        nav_r=sect["nav_r"],
        plate_sections=bool(panel_conf.get("section_plate",
                                           config.get("section_plate", True))),
        width=width,
        height=height,
        rx=palette.info(theme)["radius"],
        theme=theme,
        shadows=bool(panel_conf.get("shadows", config.get("shadows", True))) and not lite,
        lite=lite,
        title=sect["title"] if sect["mode"] == "tabs" else panel_conf.get("title", ""),
        updated=clock_now()[0],
        today=clock_now()[1],
        connected=st.connected,
    )


def wall_of(panel_conf, theme):
    """
    Своя картинка обоев стекла (1.50): (адрес, вуаль) или (None, None).
    Вуаль - полупрозрачный слой поверх: на тёмном стекле картинка темнее,
    на дневном светлее, иначе подписи на плитках теряются в фотографии.
    wallpaper_dim - её плотность, 0…1 (по умолчанию 0.3).
    """
    if palette.info(theme)["depth"] != "glass":
        return None, None
    raw = panel_conf.get("wallpaper", config.get("wallpaper"))
    url = wallpaper.url_for(wallpaper.resolve(raw, BASE_DIR))
    if not url:
        return None, None
    try:
        dim = float(panel_conf.get("wallpaper_dim", config.get("wallpaper_dim", 0.3)))
    except (TypeError, ValueError):
        dim = 0.3
    dim = max(0.0, min(dim, 0.9))
    rgb = "0,0,0" if palette.page_dark(theme) else "255,255,255"
    return url, "rgba(%s,%.2f)" % (rgb, dim)


def _json(body, status=200):
    return Response(json.dumps(body, ensure_ascii=False), status=status,
                    mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


def main():
    global config, history, jinja

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(message)s")

    config = Config(CONFIG_PATH)
    # Свои темы (с 1.25): папка themes/ рядом с config.yaml, или своя -
    # themes_dir в конфиге. Перечитывается сама, когда файлы меняются.
    palette.load(themes_dir())
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
    # Календари: события забирает свой поток, отрисовка читает готовое.
    calendars.configure(config, state)
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
        stats.inc("mqtt")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.connect_async(mqtt_conf.get("host", "localhost"),
                         int(mqtt_conf.get("port", 1883)), 60)
    client.loop_start()
    # Поток paho безымянный (Thread-N): назовём, чтобы /stats говорил «mqtt».
    if getattr(client, "_thread", None) is not None:
        client._thread.name = "mqtt"

    stop = threading.Event()
    threading.Thread(target=history.prefetch_loop, args=(config, stop),
                     daemon=True, name="history").start()
    threading.Thread(target=calendars.loop, args=(stop,),
                     daemon=True, name="calendar").start()

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


# Маршруты по модулям (с 1.46). Импорт в самом низу: модули берут отсюда
# app и общие функции, а конфиг и историю читают как web.config,
# web.history - их подменяют main() и проверки. Имена, которыми пользуются
# снаружи (проверки, tools/), остаются доступны и как web.<имя>.
from .web_live import (MAIN_MARK, cached_panel, demo_svg, lean_head,  # noqa: E402,F401
                       panel_changed, panel_svg, panel_version, wait_for_panel)
from .web_tiles import chart_svg, panel_tiles, tile_svg  # noqa: E402,F401
from .web_api import allowed_topics, api_list, api_publish  # noqa: E402,F401
from .web_pages import panel_html, panel_order, wrapper_html  # noqa: E402,F401
