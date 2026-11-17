"""Convert inline tiles while preserving unresolved YAML references."""

from copy import copy, deepcopy
from pathlib import Path
import os
import uuid

import yaml

from .editor_documents import DocumentError
from .editor_model import PLACEMENT, tile_lists

MAP = "tag:yaml.org,2002:map"
STR = "tag:yaml.org,2002:str"
INT = "tag:yaml.org,2002:int"


def _scalar(value, tag=STR):
    return yaml.ScalarNode(tag, value)


def _mapping(pairs):
    return yaml.MappingNode(MAP, [(_scalar(k), v) for k, v in pairs])


def _serialize(node):
    return yaml.serialize(node, Dumper=yaml.SafeDumper, allow_unicode=True).encode("utf-8")


def _effective_pairs(node):
    # Documents flattens YAML merges in precedence order. The last value
    # wins, but keep the original nodes so tags and references survive.
    loader = yaml.SafeLoader("")
    try:
        pairs = {loader.construct_object(key, deep=True): (key, value)
                 for key, value in node.value}
        return pairs.values()
    finally:
        loader.dispose()


def migration_files(config):
    """Return new YAML bytes without modifying the active legacy tree."""
    docs = config.documents
    if not docs or "schema" in docs.data or "elements" in docs.data:
        raise DocumentError("Миграция требует исходного конфига без schema и elements")
    root = docs.root.parent
    replacements = {}
    files = {}
    library = []
    occupied = set()
    namespace = uuid.UUID("3122b73f-bba9-49f9-87c4-99bb2a863420")

    def add_file(path, node):
        if path in files or (root / path).exists() or (root / path).is_symlink():
            raise DocumentError("Файл для миграции уже существует")
        files[path] = _serialize(node)
        return root / path

    def include(path, parent):
        return _scalar(os.path.relpath(path, parent), "!include")

    for path, _container, tiles in tile_lists(config.panels, legacy=True):
        for index, tile in enumerate(tiles):
            logical = path + (index,)
            node = docs.nodes[logical]
            if not isinstance(node, yaml.MappingNode):
                raise DocumentError("Для миграции плитка должна быть YAML-словарём")
            raw_id = tile.get("id")
            stable_id = (raw_id if isinstance(raw_id, str) and raw_id.strip()
                         else "tile_" + uuid.uuid5(namespace, repr(logical)).hex)
            if stable_id in occupied:
                raise DocumentError("Повторяющийся исходный id плитки")
            occupied.add(stable_id)
            suffix = uuid.uuid5(namespace, stable_id).hex
            ref = "element_" + suffix
            parent = docs.origins[logical].file.parent
            instance = [("id", _scalar(stable_id)), ("element", _scalar(ref))]
            body = copy(node)
            body.value = []
            for key, value in _effective_pairs(node):
                if key.value in PLACEMENT:
                    instance.append((key.value, value))
                elif key.value != "id":
                    body.value.append((key, value))
            if parent == root:
                library.append((ref, body))
            else:
                element_path = parent.relative_to(root) / ("editor-element-" + suffix + ".yaml")
                library.append((ref, include(add_file(element_path, body), root)))
            replacements[logical] = _mapping(instance)

    def transform(node, logical):
        if node.tag != "!include" and logical in replacements:
            return replacements[logical]
        if not any(target[:len(logical)] == logical for target in replacements):
            return node
        if node.tag == "!include":
            origins = docs.includes[logical]
            transformed = transform(docs.nodes[logical], logical)
            # Keep relative include and local secret resolution in each directory.
            for index in reversed(range(len(origins))):
                origin, _ = origins[index]
                parent = (docs.origins[logical].file.parent if index == len(origins) - 1
                          else origins[index + 1][0].file.parent)
                suffix = uuid.uuid5(namespace, repr((logical, index))).hex
                path = parent.relative_to(root) / ("editor-include-" + suffix + ".yaml")
                transformed = include(add_file(path, transformed), origin.file.parent)
            return transformed
        updated = copy(node)
        if isinstance(node, yaml.MappingNode):
            loader = yaml.SafeLoader("")
            try:
                updated.value = [(key, transform(value, logical + (
                    loader.construct_object(key, deep=True),))) for key, value in node.value]
            finally:
                loader.dispose()
        elif isinstance(node, yaml.SequenceNode):
            updated.value = [transform(value, logical + (index,))
                             for index, value in enumerate(node.value)]
        return updated

    original = docs.nodes[()]
    new_root = copy(original)
    new_root.value = [(_scalar("schema"), _scalar("2", INT)),
                      (_scalar("elements"), include(add_file(Path("elements.yaml"),
                          _mapping(library)), root))]
    for key, value in original.value:
        if key.value == "panels":
            value = transform(value, ("panels",))
            if value.tag != "!include":
                value = include(add_file(Path("panels.yaml"), value), root)
        new_root.value.append((key, value))
    if "panels" not in docs.data:
        new_root.value.append((_scalar("panels"), _mapping([])))
    files[Path(docs.root.name)] = _serialize(new_root)
    return files


def verify_migration(old, new):
    """Resolved data equality includes bindings, commands, order and sizes."""
    from . import web  # noqa: F401  registers screen routes
    from .web_screen import screen_layout

    def runtime(config):
        data = deepcopy(config.data)
        data.pop("schema", None)
        data.pop("elements", None)
        for _path, _container, tiles in tile_lists(data.get("panels") or {}, legacy=True):
            for tile in tiles:
                tile.pop("id", None)
        return data

    if runtime(old) != runtime(new) or old.used_channels() != new.used_channels():
        raise DocumentError("Миграция изменила разрешённую конфигурацию")
    if any(screen_layout(name, old) != screen_layout(name, new) for name in old.panels):
        raise DocumentError("Миграция изменила ESP JSON")
