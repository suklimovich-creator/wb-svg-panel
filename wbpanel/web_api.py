# -*- coding: utf-8 -*-
"""
API для страницы: публикация в MQTT (белый список из реестра плиток),
списки, кадр и поток камеры. Выделено из web.py в 1.46.

Общее (app, конфиг, история, шаблоны) - в web.py: конфиг и историю
подменяют main() и проверки, поэтому они читаются как web.config,
web.history, а не копируются сюда при импорте.
"""

import json
from flask import Response, abort, request

from . import web
from . import cameras, showcase, stats
from .assemble import resolve_sections
from .const import log
from .lists import ListError, lists
from .registry import writable_of
from .state import state
from .water import water
from .web import _json, app

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
    for panel_conf in web.config.panels.values():
        interactive = bool((panel_conf or {}).get("interactive",
                           web.config.get("interactive", False)))
        if not interactive:
            continue
        for _title, tiles, _status, _key in resolve_sections(panel_conf,
                                                             web.config.panels):
            for tile in tiles:
                allowed.update(writable_of(tile, state))
        # Сброс тревоги протечки нажимают и из окна чипа, а чип не плитка
        # и белого списка своего не имеет. Сброс модулей из раздела water -
        # кнопка, которую человек и так нажмёт на самом модуле.
        allowed.update(water.all_resets())
    return allowed


@app.route("/api/publish", methods=["POST"])
def api_publish():
    web.config.reload()
    try:
        body = json.loads(request.get_data(as_text=True) or "{}")
        topic = str(body["topic"])
        value = str(body["value"])
    except (ValueError, KeyError, TypeError):
        return Response('{"error":"нужны поля topic и value"}', status=400,
                        mimetype="application/json")

    # Витрина тем: прибор выдуманный, нажатие меняет только её состояние.
    if showcase.owns(topic):
        if len(value) > 32:
            return Response('{"error":"слишком длинное значение"}', status=400,
                            mimetype="application/json")
        showcase.publish(topic, value)
        return Response('{"ok":true}', mimetype="application/json")
    if topic not in allowed_topics():
        log.warning("отклонена публикация в %s", topic)
        return Response('{"error":"топик не разрешён"}', status=403,
                        mimetype="application/json")
    if len(value) > 32:
        return Response('{"error":"слишком длинное значение"}', status=400,
                        mimetype="application/json")
    if not web.mqtt_client or not state.connected:
        return Response('{"error":"нет связи с MQTT"}', status=503,
                        mimetype="application/json")

    web.mqtt_client.publish(topic, value)
    log.info("публикация %s = %s", topic, value)
    stats.inc("publish")
    return Response('{"ok":true}', mimetype="application/json")


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
    web.config.reload()
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
    web.config.reload()
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
    web.config.reload()
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
