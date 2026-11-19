"""In-process drafts over the revision store and a value-free editor model."""

from copy import copy
from dataclasses import asdict, dataclass, field, replace
import html
import json
import re
import secrets
import threading
import time
from urllib.parse import unquote

import yaml

from . import palette, yamlx
from .assemble import _from_names
from .config import Config
from .editor_documents import DocumentError, _Loader
from .editor_model import MANAGED_SCHEMAS, PLACEMENT, placement_id
from .editor_operations import OperationError, apply, list_at, validate
from .editor_revisions import Conflict, RevisionNotFound, RevisionStore, ValidationError
from .registry import has_pad, resolve_type


class EditorError(Exception):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.body = {"error": {"code": code, "message": message, **details}}


def _secret_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _secret_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _secret_strings(item)
    elif value is not None and str(value):
        yield str(value)


_JSON_ESCAPE = re.compile(r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])')
_BINDINGS = re.compile(r"^(?:channel|service|topic|command)(?:_|$)|_(?:channel|topic)$")


def _decode(value):
    def json_char(match):
        return json.loads('"' + match.group() + '"')
    decoded = html.unescape(unquote(_JSON_ESCAPE.sub(json_char, value)))
    if any(0xD800 <= ord(char) <= 0xDFFF for char in decoded):
        decoded = decoded.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    return decoded


class SecretPolicy:
    """Scrub display fields before fitting text and check the final wire format."""

    def __init__(self, config):
        docs = config.documents
        values = set()
        if docs:
            paths = docs.secret_files | {docs.root.parent / yamlx.SECRETS_NAME}
            for path in paths & docs.files.keys():
                document = yaml.load(docs.files[path], Loader=_Loader)
                values.update(_secret_strings(document))
        self.values = sorted(values, key=lambda value: (-len(value), value))

    def contains(self, value):
        if not self.values:
            return False
        space_variant = any(" " in secret for secret in self.values)
        while True:
            if any(secret in value for secret in self.values):
                return True
            if space_variant and "+" in value:
                url_form = value.replace("+", " ")
                if any(secret in url_form for secret in self.values):
                    return True
            decoded = _decode(value)
            if decoded == value:
                return False
            value = decoded

    def label(self, text, fallback="Скрытое значение"):
        if isinstance(text, (dict, list)) or self.contains(str(text)):
            return fallback
        return text

    def check(self, content):
        if self.contains(content):
            raise EditorError("protected_output",
                              "В данных для показа найдено значение из secrets.yaml. Проверьте ID и отображаемые поля плиток.", 422)

    def check_values(self, value):
        # DTO keys are fixed by the server. User-controlled names in these
        # DTOs are values (including element IDs), never mapping keys.
        for scalar in _secret_strings(value):
            self.check(scalar)

    def rendering_config(self, config):
        def walk(value, key=""):
            if isinstance(value, dict):
                return {k: walk(v, str(k)) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v, key) for v in value]
            if isinstance(value, str) and not _BINDINGS.search(key):
                return self.label(value)
            return value

        safe = copy(config)
        safe.data = walk(config.data)
        return safe


@dataclass
class Session:
    draft: object
    candidate: object = field(default=None, repr=False)
    preview_verified: bool = False
    version: int = 0
    checked: int = -1
    closed: bool = False
    events: list = field(default_factory=list)
    originals: dict = field(default_factory=dict, repr=False)
    mutex: object = field(default_factory=threading.Lock)
    accessed: float = field(default_factory=time.monotonic)


class EditorService:
    def __init__(self, path):
        self.path = path
        self.sessions = {}
        self.mutex = threading.Lock()

    def store(self, *, create=False):
        return RevisionStore(self.path, create=create)

    def active(self):
        return Config(self.path, strict=True, confined=True)

    def _expire(self, now):
        for key, session in list(self.sessions.items()):
            if now - session.accessed > 3600 and session.mutex.acquire(blocking=False):
                try:
                    del self.sessions[key]
                finally:
                    session.mutex.release()

    def create(self):
        if self.active().get("schema") not in MANAGED_SCHEMAS:
            raise EditorError("legacy", "Сначала выполните явную миграцию через editor_cli", 409)
        with self.mutex:
            self._expire(time.monotonic())
            if len(self.sessions) >= 128:
                raise EditorError("draft_limit", "Достигнут предел открытых черновиков", 429)
            store = self.store(create=True)
            candidate, draft = store.draft_with_config(managed=True)
            key = secrets.token_urlsafe(24)
            session = Session(draft, candidate=candidate)
            result = self._model(key, session, candidate)
            self.sessions[key] = session
            return result

    def get(self, key):
        with self.mutex:
            now = time.monotonic()
            self._expire(now)
            session = self.sessions.get(key)
            if session is None:
                raise EditorError("draft_unknown", "Черновик больше недоступен. Нажмите «Новый черновик».", 404)
            session.accessed = now
            return session

    def _candidate(self, session):
        if session.closed:
            raise EditorError("draft_closed", "Черновик уже опубликован", 409,
                              draft_version=session.version)
        return self.store().candidate(session.draft, verify=False)

    def _model(self, key, session, candidate):
        from .state import state

        docs = candidate.documents
        policy = SecretPolicy(candidate)
        elements = docs.data.get("elements", {})
        inline = candidate.get("schema") == 3
        library = {}
        for name, value in elements.items():
            kind = resolve_type(value, state)
            library[name] = {"id": name, "type": kind, "pad": has_pad(kind),
                             "title": policy.label(value.get("title") or name)}
            for field in ("w", "h"):
                size = value.get(field)
                if type(size) in (int, float) and not policy.contains(str(size)):
                    library[name][field] = size
        panels = []
        for name, panel in docs.data["panels"].items():
            sections = []

            def instances(tiles):
                result = []
                for tile in tiles:
                    placement = {k: policy.label(tile[k], "") for k in sorted(PLACEMENT)
                                 if k in tile}
                    if "title" in tile and placement["title"] != tile["title"]:
                        placement["title_protected"] = True
                    item = {"id": placement_id(name, tile), **placement}
                    if "id" not in tile:
                        item["implicit_id"] = True
                    if "element" in tile:
                        item["element"] = tile["element"]
                    else:
                        kind = resolve_type(tile, state)
                        item.update(type=kind, pad=has_pad(kind))
                    result.append(item)
                return result

            if "tiles" in panel or not panel.get("sections"):
                sections.append({"section": None, "title": "Плитки панели",
                                 "tiles": instances(panel.get("tiles") or []),
                                 "outside_sections": bool(panel.get("sections"))})
            for index, section in enumerate(panel.get("sections") or []):
                if not isinstance(section, dict):
                    sections.append({"section": index, "title": "Раздел",
                                     "readonly": True, "sources": [], "tiles": []})
                    continue
                sources = _from_names(section.get("from"))
                readonly = "from" in section or "tiles" not in section
                sections.append({"section": index,
                                 "title": policy.label(section.get("title") or "Раздел"),
                                 "readonly": readonly, "sources": sources,
                                 "tiles": [] if readonly else instances(section["tiles"])})
            panel_cols = panel.get("cols", 4)
            panels.append({"id": name, "title": policy.label(panel.get("title") or name),
                           "sections": sections})
            if type(panel_cols) is int and 1 <= panel_cols <= 12 and not policy.contains(str(panel_cols)):
                panels[-1]["cols"] = panel_cols
        body = {"draft_id": key, "base_revision": session.draft.base_revision,
                "draft_version": session.version, "checked": session.checked == session.version,
                "panels": panels, "library": list(library.values()), "themes": palette.names()}
        if inline:
            body["inline_tiles"] = True
        policy.check_values(panels)
        policy.check_values(body["library"])
        policy.check_values(body["themes"])
        return body

    def model(self, key):
        session = self.get(key)
        with session.mutex:
            candidate, _summary = self._candidate(session)
            return self._model(key, session, candidate)

    def _expect(self, key, session, expected, candidate):
        if expected != session.version:
            raise EditorError("draft_version", "Версия черновика изменилась", 409,
                              current=self._model(key, session, candidate))

    def operation(self, key, version, operation):
        session = self.get(key)
        with session.mutex:
            candidate, _summary = self._candidate(session)
            self._expect(key, session, version, candidate)
            policy = SecretPolicy(candidate)
            try:
                validate(operation)
                policy.check_values(operation)
                changeset, expected, event, path, original = apply(
                    candidate, session.draft.changeset, operation, session.originals)
                if changeset._files == session.draft.changeset._files:
                    return self._model(key, session, candidate)
                draft = replace(session.draft, changeset=changeset)
                try:
                    updated, _summary = self.store().candidate(draft, verify=False)
                except Conflict:
                    raise
                except DocumentError:
                    raise OperationError("Эту операцию нельзя безопасно применить к YAML") from None
                if updated.documents.data != expected:
                    raise OperationError("Ссылка или YAML alias затрагивает другие объекты")
                staged = replace(session, draft=draft, version=session.version + 1)
                result = self._model(key, staged, updated)
            except OperationError as exc:
                supplied = operation if isinstance(operation, dict) else {}
                details = {k: supplied[k] for k in ("panel", "id") if k in supplied
                           and isinstance(supplied[k], str) and not policy.contains(supplied[k])}
                raise EditorError("operation", str(exc), field=exc.field, **details) from None
            session.candidate = updated
            session.preview_verified = False
            session.draft = draft
            session.version = staged.version
            if path in changeset._files:
                session.originals[path] = original
            else:
                session.originals.pop(path, None)
            session.events.append(event)
            return result

    def _verify(self, candidate, policy, *, verify_screens=True):
        try:
            self.store()._verify(candidate, verify_screens=verify_screens)
        except ValidationError as exc:
            details = {}
            for origin in exc.origins:
                panel, stable_id = origin.logical_path[1], origin.stable_id
                if not policy.contains(panel) and not policy.contains(stable_id):
                    details = {"id": stable_id, "panel": panel}
                    break
            message = ("У плитки неполные или неверные настройки. Исправьте её настройки или уберите её из черновика."
                       if details else "Черновик не прошёл проверку. Проверьте последние изменения в панелях.")
            raise EditorError("candidate_invalid", message, 422, **details) from None

    def diff(self, key):
        session = self.get(key)
        with session.mutex:
            candidate, summary = self._candidate(session)
            policy = SecretPolicy(candidate)
            self._verify(candidate, policy)
            operations = [dict(event) for event in session.events]
            for event in operations:
                if policy.contains(event["file"]):
                    event.pop("file")
            body = {"summary": summary, "operations": operations,
                    "draft_version": session.version, "base_revision": session.draft.base_revision}
            policy.check_values(body["operations"])
            session.checked = session.version
            return body

    def preview(self, key, draft_version):
        session = self.get(key)
        with session.mutex:
            if session.closed:
                raise EditorError("draft_closed", "Черновик уже опубликован", 409)
            self.store().verify_preview_base(session.draft, session.candidate.documents)
            candidate = session.candidate
            if draft_version is not None:
                self._expect(key, session, draft_version, candidate)
            policy = SecretPolicy(candidate)
            if not session.preview_verified:
                self._verify(candidate, policy, verify_screens=False)
                session.preview_verified = True
        return candidate, policy.rendering_config(candidate), policy

    def publish(self, key, version):
        session = self.get(key)
        with session.mutex:
            candidate, summary = self._candidate(session)
            self._expect(key, session, version, candidate)
            if session.checked != version or not (summary["writes"] or summary["deletes"]):
                raise EditorError("unchecked", "Внесите изменения и нажмите «Проверить и показать изменения» перед публикацией.", 409)
            revision = self.store().publish(session.draft)
            session.closed = True
            session.version += 1
            with self.mutex:
                self.sessions.pop(key, None)
            return asdict(revision)

    def history(self):
        return [asdict(revision) for revision in self.store().history(readonly=True)]

    def rollback(self, revision, base):
        store = self.store()
        try:
            if not store.lock.exists():
                raise RevisionNotFound()
            return asdict(store.rollback(revision, managed=True, base_revision=base))
        except RevisionNotFound:
            raise EditorError("revision_unknown", "Ревизия не найдена", 404) from None


def preview_tile(candidate, safe_config, panel, stable_id):
    from .state import state

    try:
        origin = candidate.tile_origin(stable_id)
    except KeyError:
        raise EditorError("tile_unknown", "Экземпляр не найден", 404) from None
    path = origin.logical_path
    if path[:2] != ("panels", panel):
        raise EditorError("tile_unknown", "Экземпляр не найден на этой панели", 404)
    section = path[2:4] if path[2] == "sections" else ()
    list_at(candidate.documents.data, panel, section[1] if section else None)
    conf = safe_config.panels[panel]
    container = conf["sections"][section[1]] if section else conf
    tile = container["tiles"][path[-1]]
    if not has_pad(resolve_type(tile, state)):
        raise EditorError("pad_unavailable", "Для типа нет собственного пульта", 404)
    return conf, tile, path[-1]
