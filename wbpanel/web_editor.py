"""Same-origin routes for the structural editor."""

from contextvars import ContextVar
from functools import wraps
import json
import logging
import threading
from urllib.parse import urlsplit

from flask import Response, request
from werkzeug.exceptions import HTTPException

from . import __version__, web
from .editor_documents import DocumentError
from .editor_revisions import Conflict
from .editor_service import EditorError, EditorService, SecretPolicy, preview_tile
from .web import app
from .web_tiles import render_tile

MAX_BODY = 8192
service = None
_service_lock = threading.Lock()

_editor_request = ContextVar("wbpanel_editor_request", default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # Existing runtime diagnostics can quote resolved tile fields.
        # The editor reports a safe error to the caller and logs generic failures.
        return not _editor_request.get()


web.log.addFilter(_PrivateRequestFilter())


def _service():
    global service
    with _service_lock:
        path = web.config.path if web.config else web.CONFIG_PATH
        if service is None or service.path != path:
            service = EditorService(path)
        return service


def _response(value, status=200):
    return Response(json.dumps(value, ensure_ascii=False, allow_nan=False),
                    status=status, mimetype="application/json")


def _route(func):
    @wraps(func)
    def handle(*args, **kwargs):
        try:
            request_context = _editor_request.set(True)
            try:
                response = func(*args, **kwargs)
            finally:
                _editor_request.reset(request_context)
            if not isinstance(response, Response):
                response = _response(response)
        except EditorError as exc:
            response = _response(exc.body, exc.status)
        except Conflict:
            response = _response({"error": {"code": "base_revision",
                "message": "Активная конфигурация изменилась. Откройте новый черновик."}}, 409)
        except DocumentError:
            response = _response({"error": {"code": "invalid_configuration",
                "message": "Конфигурация не прошла проверку или содержит неподдерживаемый YAML."}}, 422)
        except HTTPException as exc:
            response = _response({"error": {"code": "request", "message": "Запрос отклонён"}},
                                 exc.code)
        except Exception:
            # Never log a parser/render exception that could quote resolved config.
            web.log.error("Ошибка обработки запроса редактора")
            response = _response({"error": {"code": "editor_failure",
                "message": "Ошибка чтения, записи или отрисовки конфигурации."}}, 500)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    return handle


def _origin_allowed():
    if request.headers.get("X-WB-Editor") != "1":
        return False
    site = request.headers.get("Sec-Fetch-Site")
    if site not in (None, "same-origin"):
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # A cross-origin script cannot send the custom header without CORS.
    try:
        url = urlsplit(origin)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None
                or url.path or url.query or url.fragment):
            return False
        _port = url.port
        # Browser-controlled metadata reflects the public page/request pair.
        # A reverse proxy can replace the scheme and host before Flask.
        if site == "same-origin":
            return True
        expected = request.headers.get("X-Forwarded-Proto", request.scheme)
        return url.scheme == expected and url.netloc.lower() == request.host.lower()
    except ValueError:
        return False


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError
    return result


def _invalid_constant(_value):
    raise ValueError


def _payload(fields):
    if not _origin_allowed():
        raise EditorError("origin",
                          "Редактор не смог подтвердить адрес страницы. Откройте редактор заново. "
                          "Если ошибка появляется через Wiren Board Cloud, попробуйте локальный адрес контроллера.",
                          403)
    if request.mimetype != "application/json":
        raise EditorError("content_type", "Требуется application/json", 415)
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise EditorError("body_size", "Запрос слишком большой", 413)
    data = request.stream.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise EditorError("body_size", "Запрос слишком большой", 413)
    try:
        body = json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=_invalid_constant)
        # Reject isolated Unicode surrogates even when supplied as JSON escapes.
        json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):
        raise EditorError("json", "Некорректный JSON") from None
    if not isinstance(body, dict) or body.keys() != fields:
        raise EditorError("fields", "Отсутствуют обязательные поля или переданы лишние")
    return body


def _version(value):
    if type(value) is not int or value < 0:
        raise EditorError("draft_version", "Требуется целая версия черновика")
    return value


def _query_int(name, minimum, maximum):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
        if minimum <= value <= maximum:
            return value
    except ValueError:
        pass
    raise EditorError("preview_option", "Некорректная настройка предпросмотра")


def _svg(value, policy):
    policy.check(value)
    return Response(value, mimetype="image/svg+xml", headers={
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@app.route("/editor")
@app.route("/editor/<name>")
@_route
def editor_page(name=None):
    return Response(web._template("editor.html").render(
        version=__version__, root="../" if name is not None else "./"),
        mimetype="text/html")


@app.route("/api/editor/state")
@_route
def editor_state():
    config = _service().active()
    return {"managed": config.get("schema") in (2, 3)}


@app.route("/api/editor/drafts", methods=["POST"])
@_route
def editor_create():
    _payload(set())
    return _response(_service().create(), 201)


@app.route("/api/editor/drafts/<draft_id>")
@_route
def editor_model(draft_id):
    return _service().model(draft_id)


@app.route("/api/editor/drafts/<draft_id>/operation", methods=["POST"])
@_route
def editor_operation(draft_id):
    body = _payload({"draft_version", "operation"})
    return _service().operation(draft_id, _version(body["draft_version"]), body["operation"])


@app.route("/api/editor/drafts/<draft_id>/diff")
@_route
def editor_diff(draft_id):
    return _service().diff(draft_id)


@app.route("/api/editor/drafts/<draft_id>/preview/<name>.svg")
@_route
def editor_preview(draft_id, name):
    version = _query_int("draft_version", 0, 2**53 - 1)
    cols = _query_int("cols", 1, 12)
    candidate, safe, policy = _service().preview(draft_id, version)
    if name not in candidate.panels:
        raise EditorError("panel_unknown", "Панель не найдена", 404)
    if request.args.get("root_tiles") == "1" and safe.panels[name].get("sections"):
        safe.data["panels"][name] = dict(safe.panels[name], sections=[])
    return _svg(web.render_panel(name, cols, candidate=safe), policy)


@app.route("/api/editor/drafts/<draft_id>/tile/<name>/<stable_id>.svg")
@_route
def editor_tile(draft_id, name, stable_id):
    version = _query_int("draft_version", 0, 2**53 - 1)
    candidate, safe, policy = _service().preview(draft_id, version)
    panel, tile, index = preview_tile(candidate, safe, name, stable_id)
    return _svg(render_tile(panel, tile, index, web.state, web.history, candidate=safe), policy)


@app.route("/api/editor/drafts/<draft_id>/publish", methods=["POST"])
@_route
def editor_publish(draft_id):
    body = _payload({"draft_version"})
    revision = _service().publish(draft_id, _version(body["draft_version"]))
    web.config.reload(force=True)
    return revision


@app.route("/api/editor/revisions")
@_route
def editor_history():
    editor = _service()
    history = editor.history()
    body = {"revisions": history, "head": history[0]["id"] if history else None}
    SecretPolicy(editor.active()).check(json.dumps(body, ensure_ascii=False))
    return body


@app.route("/api/editor/rollback", methods=["POST"])
@_route
def editor_rollback():
    body = _payload({"revision", "base_revision", "confirm"})
    if body["confirm"] is not True or any(
            not isinstance(body[key], str) or not body[key] for key in ("revision", "base_revision")):
        raise EditorError("confirmation", "Требуются ревизия и подтверждение отката")
    revision = _service().rollback(body["revision"], body["base_revision"])
    web.config.reload(force=True)
    return revision
