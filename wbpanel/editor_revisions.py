"""Private revision store and journalled publication of config files."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

from . import yamlx
from .editor_model import MANAGED_SCHEMAS
from .config import Config
from .editor_documents import DocumentError
from .editor_drafts import Changeset, Draft


class Conflict(DocumentError):
    """The draft is based on a different active revision or file state."""


class ValidationError(DocumentError):
    def __init__(self, count, origins):
        super().__init__("Конфигурация не прошла проверку: ошибок %d" % count)
        self.origins = origins


class RevisionNotFound(DocumentError):
    """The requested revision is outside the visible history."""


@dataclass(frozen=True)
class Revision:
    id: str
    parent: str
    timestamp: str
    action: str
    source: str = None


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path, data, mode=0o600):
    if data is None:
        path.unlink(missing_ok=True)
        _sync_dir(path.parent)
        return
    fd, name = tempfile.mkstemp(prefix=".editor-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync_dir(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")


class RevisionStore:
    def __init__(self, path, directory=None, *, create=True):
        supplied = Path(os.path.abspath(path))
        self.path = supplied.resolve()
        if self.path != supplied:
            raise DocumentError("Корневой конфиг не должен находиться за symlink")
        self.root = self.path.parent
        self.directory = (Path(os.path.abspath(directory)) if directory else
                          self.root / "data" / "editor")
        if self.directory.resolve() != self.directory:
            raise DocumentError("Каталог истории не должен находиться за symlink")
        if self.root.is_relative_to(self.directory):
            raise DocumentError("Каталог истории не может содержать рабочий конфиг")
        self.revisions = self.directory / "revisions"
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            self.revisions.mkdir(mode=0o700, exist_ok=True)
        self.head = self.directory / "HEAD"
        self.journal = self.directory / "journal.json"
        self.lock = self.directory / "lock"
        self.key_path = self.directory / "key"

    @contextmanager
    def _locked(self):
        fd = os.open(self.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not self.key_path.exists():
                _replace(self.key_path, os.urandom(32))
            self._key = self.key_path.read_bytes()
            self._recover()
            yield

    @contextmanager
    def _reading(self):
        fd = os.open(self.lock, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            if self.journal.exists():
                raise Conflict("Нужно восстановить незавершённую публикацию")
            self._key = self.key_path.read_bytes()
            yield

    def _target(self, relative):
        path = self.root / relative
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or path.resolve() != path or path.is_relative_to(self.directory)):
            raise DocumentError("Недопустимый путь документа")
        return path

    def _load(self, path):
        try:
            config = Config(path, strict=True, confined=True, editor_lock=False)
            if not config.documents:
                raise DocumentError("Не найден корневой конфиг")
            return config
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
            # Parser errors can contain the entire source line.
            if isinstance(exc, DocumentError):
                raise
            raise DocumentError("Не удалось загрузить дерево конфигурации") from None
        except Exception:
            raise DocumentError("Не удалось загрузить дерево конфигурации") from None

    def _hash(self, data):
        return hmac.new(self._key, data, hashlib.sha256).hexdigest()

    def _hashes(self, files):
        return {str(path): self._hash(data) for path, data in files.items()}

    def _modes(self, files):
        return {str(path): stat.S_IMODE(self._target(path).stat().st_mode)
                for path in files}

    def _active(self, tracked=()):
        config = self._load(self.path)
        files = {p.relative_to(self.root): data for p, data in config.documents.files.items()}
        for path in tracked:
            path = Path(path)
            target = self._target(path)
            if path not in files and target.is_file():
                files[path] = target.read_bytes()
        for path in files:
            self._target(path)
        return config, files

    def _head(self):
        return self.head.read_text().strip() if self.head.exists() else None

    def _location(self, revision):
        if not isinstance(revision, str) or not re.fullmatch(r"legacy-0|[a-f0-9]{32}", revision):
            raise DocumentError("Некорректный идентификатор ревизии")
        return self.revisions / revision

    def _manifest(self, revision):
        path = self._location(revision) / "manifest.json"
        if not path.is_file():
            raise DocumentError("Ревизия не найдена")
        return json.loads(path.read_bytes())

    def _snapshot(self, revision, files, modes):
        target = self._location(revision.id)
        stage = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self.revisions))
        try:
            for path, data in files.items():
                dest = stage / "files" / path
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _replace(dest, data, 0o400)
            manifest = {"revision": vars(revision), "hashes": self._hashes(files),
                        "modes": modes}
            _replace(stage / "manifest.json", _json(manifest), 0o400)
            stage.rename(target)
            _sync_dir(self.revisions)
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def _read_snapshot(self, revision, manifest):
        directory = self._location(revision) / "files"
        files = {Path(path): (directory / path).read_bytes() for path in manifest["hashes"]}
        if self._hashes(files) != manifest["hashes"]:
            raise DocumentError("Нарушена целостность снимка")
        return files

    def _baseline(self):
        head = self._head()
        known = self._manifest(head)["hashes"] if head else {}
        config, files = self._active(known)
        hashes = self._hashes(files)
        if head is None or known != hashes:
            legacy = "schema" not in config.data
            revision = ("legacy-0" if legacy and not self._location("legacy-0").exists()
                        else uuid.uuid4().hex)
            self._snapshot(self._revision(revision, head, "external" if head else "initial"),
                           files, self._modes(files))
            _replace(self.head, revision.encode())
            head = revision
        return config, Draft(head, hashes)

    def draft_with_config(self, *, managed=False):
        with self._locked():
            if managed and self._load(self.path).get("schema") not in MANAGED_SCHEMAS:
                raise DocumentError("Сначала выполните явную миграцию")
            return self._baseline()

    def draft(self, *, managed=False):
        return self.draft_with_config(managed=managed)[1]

    def migration(self, *, inline=False):
        from .editor_migration import inline_migration_files, migration_files

        with self._locked():
            config, draft = self._baseline()
            draft.changeset = Changeset((inline_migration_files if inline else migration_files)(config))
            return draft

    def _revision(self, revision, parent, action, source=None):
        return Revision(revision, parent, datetime.now(timezone.utc).isoformat(), action, source)

    def _recover(self):
        if not self.journal.exists():
            return
        journal = json.loads(self.journal.read_bytes())
        if self._head() != journal["next"]:
            base = journal["base"]
            manifest = self._manifest(base)
            old = self._read_snapshot(base, manifest)
            for name, expected in reversed(list(journal["files"].items())):
                relative = Path(name)
                path = self._target(relative)
                previous = old.get(relative)
                present = path.read_bytes() if path.exists() else None
                if present == previous:
                    continue
                if (self._hash(present) if present is not None else None) != expected:
                    raise Conflict("Файл изменён извне во время восстановления")
                _replace(path, previous, manifest["modes"].get(name, 0o600))
            pending = self._location(journal["next"])
            if pending.exists():
                shutil.rmtree(pending)
                _sync_dir(self.revisions)
        self.journal.unlink()
        _sync_dir(self.directory)

    def _verify(self, candidate, *, verify_screens=True):
        from .checkup import publication_errors
        from . import web  # noqa: F401  registers screen routes
        from .web_screen import screen_layout

        try:
            invalid = set()
            errors = publication_errors(candidate, lambda tile: invalid.add(id(tile)))
            if errors:
                origins = []
                for (kind, _stable_id), origin in candidate.origins.items():
                    if kind != "tile":
                        continue
                    value = candidate.data
                    for part in origin.logical_path:
                        value = value[part]
                    if id(value) in invalid:
                        origins.append(origin)
                raise ValidationError(errors, origins)
            if not verify_screens:
                return
            candidate.used_channels()
            for name in candidate.panels:
                json.dumps(screen_layout(name, candidate))
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("Не удалось проверить разрешённую конфигурацию") from None

    def publish(self, draft, *, migrate=False):
        with self._locked():
            return self._publish(draft, migrate=migrate)

    def inspect(self, draft, *, migrate=False):
        with self._locked():
            return self._publish(draft, migrate=migrate, preview=True)

    def verify_preview_base(self, draft, docs):
        """Detect external edits without parsing YAML or staging a candidate."""
        with self._reading():
            if self._head() != draft.base_revision:
                raise Conflict("Активная ревизия изменилась")
            try:
                secret_paths = docs.secret_files | {docs.root.parent / yamlx.SECRETS_NAME}
                for path in secret_paths:
                    relative = path.relative_to(docs.root.parent)
                    if str(relative) not in draft._base_hashes and self._target(relative).exists():
                        raise Conflict("Набор файлов конфигурации изменился")
                for name, digest in draft._base_hashes.items():
                    if self._hash(self._target(Path(name)).read_bytes()) != digest:
                        raise Conflict("Файлы изменились после создания черновика")
            except (DocumentError, OSError):
                raise Conflict("Активная конфигурация изменилась") from None

    def candidate(self, draft, *, verify=True):
        """Read the candidate without recovering, publishing or writing history."""
        with self._reading():
            return self._publish(draft, candidate_only=True, verify=verify)

    def _publish(self, draft, *, migrate=False, source=None, preview=False,
                 candidate_only=False, verify=True, managed=False):
        from .editor_migration import id_changes, verify_migration

        def check_base():
            if self._head() != draft.base_revision:
                raise Conflict("base_revision устарела")
            try:
                config, files = self._active(draft._base_hashes)
            except (DocumentError, OSError):
                raise Conflict("Активная конфигурация изменилась после создания черновика") from None
            if self._hashes(files) != draft._base_hashes:
                raise Conflict("Файлы изменились после создания черновика")
            return config, files

        old, active = check_base()
        if (managed or (not migrate and not draft._restore)) and "schema" not in old.data:
            raise DocumentError("Сначала выполните явную миграцию")
        files = dict(active)
        changed = {}
        for path, data in draft.changeset._files.items():
            target = self._target(path)
            if path not in active and (target.exists() or target.is_symlink()):
                raise Conflict("Файл уже существует вне базовой ревизии")
            if data is not None and not isinstance(data, bytes):
                raise DocumentError("Изменение документа должно содержать байты")
            if data == active.get(path):
                continue
            if data is None:
                files.pop(path)
            else:
                files[path] = data
            changed[path] = data

        with tempfile.TemporaryDirectory(prefix="candidate-", dir=self.directory) as tmp:
            stage = Path(tmp)
            for path, data in files.items():
                target = stage / path
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                # Private temporary inputs are never used for crash recovery.
                target.write_bytes(data)
            candidate = self._load(stage / self.path.name)
            restoring_original = draft._restore and source == "legacy-0" and "schema" not in candidate.data
            if (managed or not draft._restore) and not restoring_original and candidate.get("schema") not in MANAGED_SCHEMAS:
                raise DocumentError("Публикация требует управляемую schema")
            if verify:
                self._verify(candidate)
            if candidate_only:
                summary = Changeset(changed).summary(old, candidate)
                return candidate, summary
            if migrate:
                try:
                    verify_migration(old, candidate)
                except DocumentError:
                    raise
                except Exception:
                    raise DocumentError("Не удалось сравнить ESP JSON при миграции") from None
            if preview:
                summary = draft.changeset.summary(old, candidate)
                if migrate and candidate.get("schema") == 3:
                    summary["tile_ids"] = id_changes(old, candidate)
                return summary

            check_base()
            modes = self._modes(active)
            if draft._restore:
                modes.update(self._manifest(source)["modes"])
            revision = self._revision(uuid.uuid4().hex, draft.base_revision,
                                      "migration" if migrate else "rollback" if source else "publish",
                                      source)
            journal = {"base": revision.parent, "next": revision.id,
                       "files": {}}
            _replace(self.journal, _json(journal))
            try:
                for path, data in changed.items():
                    target = self._target(path)
                    present = target.read_bytes() if target.is_file() else None
                    if present != active.get(path):
                        raise Conflict("Файл изменился во время публикации")
                    journal["files"][str(path)] = self._hash(data) if data is not None else None
                    _replace(self.journal, _json(journal))
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    _replace(target, data, modes.get(str(path), 0o600))
                actual, bytes_on_disk = self._active(files)
                candidate_paths = {p.relative_to(stage) for p in candidate.documents.files}
                actual_paths = {p.relative_to(self.root) for p in actual.documents.files}
                deleted = active.keys() - files.keys()
                if (files != bytes_on_disk or candidate_paths != actual_paths
                        or actual.data != candidate.data
                        or any(self._target(path).exists() for path in deleted)):
                    raise DocumentError("Активные файлы не соответствуют кандидату")
                self._snapshot(revision, files, self._modes(files))
                _replace(self.head, revision.id.encode())
            finally:
                self._recover()
        return revision

    def _history(self):
        result = []
        head = self._head()
        while head:
            revision = Revision(**self._manifest(head)["revision"])
            result.append(revision)
            head = revision.parent
        return result

    def history(self, *, readonly=False):
        if readonly and not self.head.exists() and not self.lock.exists():
            return []
        with (self._reading() if readonly else self._locked()):
            return self._history()

    def rollback(self, revision, *, managed=False, base_revision=None):
        with self._locked():
            if managed:
                if revision not in {entry.id for entry in self._history()}:
                    raise RevisionNotFound()
                if self._head() != base_revision:
                    raise Conflict("История изменилась")
                manifest = self._manifest(base_revision)
                draft = Draft(base_revision, manifest["hashes"])
            else:
                _config, draft = self._baseline()
            manifest = self._manifest(revision)
            files = self._read_snapshot(revision, manifest)
            draft.changeset = Changeset(dict.fromkeys(map(Path, draft._base_hashes)) | files)
            draft._restore = True
            return self._publish(draft, source=revision, managed=managed)
