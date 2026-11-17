"""YAML documents and locations used by the managed panel configuration."""

from copy import copy
from dataclasses import dataclass
from pathlib import Path
import os

import yaml

from . import yamlx


class _Loader(yaml.SafeLoader):
    # Resolve custom tags only in _walk, where confinement and provenance
    # apply. PyYAML must not resolve them implicitly in keys or secrets.
    yaml_constructors = {
        tag: constructor for tag, constructor in yaml.SafeLoader.yaml_constructors.items()
        if tag not in ("!include", "!secret")
    }


class DocumentError(ValueError):
    """An invalid configuration document."""


@dataclass(frozen=True)
class Origin:
    file: Path
    physical_path: tuple
    logical_path: tuple
    stable_id: str = None


class Documents:
    def __init__(self, path, strict=False, confined=False):
        self.root = Path(path).absolute()
        self.strict = strict
        self.confined = confined
        self.files = {}
        self.origins = {}
        self.duplicates = []
        self.secret_files = set()
        self._secret_cache = {}
        self.missing_secrets = []
        self.nodes = {}
        self._physical = {}
        self.includes = {}
        self.stack = []
        self.data = self._read(self.root, ())
        root_secrets = self.root.parent / yamlx.SECRETS_NAME
        if yamlx.use_secrets and root_secrets.is_file():
            self._bytes(root_secrets)
        if strict or isinstance(self.data, dict) and self.data.get("schema") == 2:
            if self.duplicates:
                origin = self.duplicates[0]
                raise DocumentError("Повторяющийся YAML-ключ: %s" % origin.file.name)
            if self.missing_secrets:
                origin = self.missing_secrets[0]
                raise DocumentError("Не найдена ссылка на секрет: %s" % origin.file.name)

    def data_at(self, path):
        value = self.data
        for key in path:
            value = value[key]
        return value

    def _path(self, path):
        if self.confined and (not path.resolve().is_relative_to(self.root.parent)
                              or path.resolve() != path):
            raise DocumentError("Документ вне управляемого дерева или через symlink")
        return path

    def _bytes(self, path):
        path = self._path(path)
        if path not in self.files:
            self.files[path] = path.read_bytes()
        return self.files[path]

    def _read(self, path, logical):
        path = Path(os.path.abspath(path))
        real = path.resolve()
        if real in self.stack:
            raise DocumentError("Цикл !include: " + " → ".join(
                p.name for p in [*self.stack, real]))
        self.stack.append(real)
        try:
            raw = self._bytes(path)
            loader = _Loader(raw)
            try:
                node = loader.get_single_node()
                self._physical[loader] = {}
                self._index(loader, node, ())
                return self._walk(loader, node, Origin(path, (), logical), set())
            finally:
                self._physical.pop(loader, None)
                loader.dispose()
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = ":%d" % (mark.line + 1) if mark else ""
            raise DocumentError("Некорректный YAML: %s%s" % (path.name, line)) from None
        except OSError as exc:
            raise DocumentError("Не удалось прочитать YAML: %s" % path.name) from exc
        finally:
            self.stack.pop()

    def _index(self, loader, node, path):
        physical = self._physical[loader]
        if node is None or node in physical:
            return
        physical[node] = path
        if isinstance(node, yaml.MappingNode) and node.tag == "tag:yaml.org,2002:map":
            for key, value in node.value:
                name = ("<<" if key.tag == "tag:yaml.org,2002:merge"
                        else loader.construct_object(key, deep=True))
                self._index(loader, value, path + (name,))
        elif isinstance(node, yaml.SequenceNode) and node.tag == "tag:yaml.org,2002:seq":
            for i, value in enumerate(node.value):
                self._index(loader, value, path + (i,))

    def _secrets(self, folder):
        path = folder / yamlx.SECRETS_NAME
        self.secret_files.add(path)
        if path not in self._secret_cache:
            try:
                self._secret_cache[path] = yaml.load(self._bytes(path), Loader=_Loader) or {}
            except FileNotFoundError:
                self._secret_cache[path] = None
        return self._secret_cache[path]

    def _walk(self, loader, node, origin, ancestors):
        logical = origin.logical_path
        if node is None:
            self.origins[logical] = origin
            self.nodes[logical] = None
            return None
        if node in ancestors:
            raise DocumentError("Циклический YAML alias: %s" % origin.file.name)
        physical = self._physical[loader].get(node, origin.physical_path)
        origin = Origin(origin.file, physical, logical)
        if node.tag == "!include":
            reference = Path(loader.construct_scalar(node))
            if self.confined and reference.is_absolute():
                raise DocumentError("Абсолютный !include нельзя опубликовать")
            path = origin.file.parent / reference
            if not path.exists() and yamlx.extra_dir and not self.confined:
                path = Path(yamlx.extra_dir) / reference
            self.includes.setdefault(logical, []).append((origin, node))
            return self._read(path, logical)

        self.origins[logical] = origin
        self.nodes[logical] = node
        ancestors = ancestors | {node}

        def child(key, value):
            where = Origin(origin.file, origin.physical_path + (key,), logical + (key,))
            return self._walk(loader, value, where, ancestors)

        if isinstance(node, yaml.MappingNode) and node.tag == "tag:yaml.org,2002:map":
            seen = set()
            for key, _ in node.value:
                if key.tag != "tag:yaml.org,2002:merge":
                    name = loader.construct_object(key, deep=True)
                    if name in seen:
                        self.duplicates.append(Origin(origin.file,
                            origin.physical_path + (name,), logical + (name,)))
                    seen.add(name)
            if any(key.tag == "tag:yaml.org,2002:merge" for key, _ in node.value):
                # Flatten a shallow copy so reusing an anchor does not turn a
                # valid merge override into an apparent duplicate key.
                flattened = copy(node)
                flattened.value = list(node.value)
                loader.flatten_mapping(flattened)
                self.nodes[logical] = flattened
                node = flattened
            value = {loader.construct_object(k, deep=True):
                     child(loader.construct_object(k, deep=True), v) for k, v in node.value}
        elif isinstance(node, yaml.SequenceNode) and node.tag == "tag:yaml.org,2002:seq":
            value = [child(index, item) for index, item in enumerate(node.value)]
        elif node.tag == "!secret":
            if not yamlx.use_secrets:
                return ""
            key = loader.construct_scalar(node)
            data = self._secrets(origin.file.parent)
            if data is None and origin.file.parent != self.root.parent:
                data = self._secrets(self.root.parent)
            if isinstance(data, dict) and key in data:
                value = data[key]
            else:
                self.missing_secrets.append(origin)
                yamlx.log.error("Не найдена ссылка !secret в %s", origin.file.name)
                return ""
        else:
            value = loader.construct_object(node, deep=True)
        return self._expand(value, origin) if node.tag == "!secret" or isinstance(value, str) else value

    def _expand(self, value, origin):
        if not yamlx.use_secrets:
            return value
        if isinstance(value, list):
            return [self._expand(item, origin) for item in value]
        if isinstance(value, dict):
            return {key: self._expand(item, origin) for key, item in value.items()}
        if not isinstance(value, str) or "${" not in value:
            return value
        secrets = self._secrets(self.root.parent) or {}

        def substitute(match):
            key = match.group(1)
            if isinstance(secrets, dict) and key in secrets:
                return str(secrets[key])
            self.missing_secrets.append(origin)
            yamlx.log.error("Не найдена ссылка на подстановку в %s", origin.file.name)
            return match.group(0)

        return yamlx._SUB.sub(substitute, value)
