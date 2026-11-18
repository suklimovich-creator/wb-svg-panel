"""Convert inline tiles while preserving unresolved YAML references."""

from copy import copy, deepcopy
from pathlib import Path
import os
import re

import yaml

from .editor_documents import DocumentError
from .editor_model import next_tile_id, reserve_tile_id, tile_lists

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
    lists = list(tile_lists(config.panels, legacy=True))
    occupied = {tile["id"] for _, _, tiles in lists for tile in tiles
                if isinstance(tile.get("id"), str) and tile["id"].strip()}
    used = set()

    def add_file(path, node):
        if path in files or (root / path).exists() or (root / path).is_symlink():
            raise DocumentError("Файл для миграции уже существует")
        files[path] = _serialize(node)
        return root / path

    def include(path, parent):
        return _scalar(os.path.relpath(path, parent), "!include")

    for path, _container, tiles in lists:
        for index, tile in enumerate(tiles):
            logical = path + (index,)
            node = docs.nodes[logical]
            if not isinstance(node, yaml.MappingNode):
                raise DocumentError("Для миграции плитка должна быть YAML-словарём")
            id_node = docs.nodes.get(logical + ("id",))
            if id_node is not None and (id_node.tag == "!secret" or
                    isinstance(id_node.value, str) and "${" in id_node.value):
                raise DocumentError("ID плитки должен быть задан открытым текстом")
            raw_id = tile.get("id")
            stable_id = (raw_id if isinstance(raw_id, str) and raw_id.strip()
                         else next_tile_id(tile, occupied))
            if stable_id in used:
                raise DocumentError("Повторяющийся исходный id плитки")
            used.add(stable_id)
            occupied.add(stable_id)
            suffix = stable_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stable_id) else str(len(used))
            ref = stable_id
            parent = docs.origins[logical].file.parent
            instance = [("element", _scalar(ref))]
            body = _mapping((key.value, block_copy(value, parent, parent))
                            for key, value in _effective_pairs(node) if key.value != "id")
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
                suffix = "%03d" % (len(files) + 1)
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


def block_copy(node, source, destination):
    """Copy each YAML occurrence, retaining its tag and rebasing includes."""
    result = copy(node)
    if isinstance(node, yaml.MappingNode):
        result.flow_style = False
        result.value = [(block_copy(k, source, destination), block_copy(v, source, destination))
                        for k, v in node.value]
    elif isinstance(node, yaml.SequenceNode):
        result.flow_style = False
        result.value = [block_copy(v, source, destination) for v in node.value]
    elif node.tag == "!include" and source != destination:
        result.value = os.path.relpath(source / node.value, destination)
    elif node.tag == "!secret" and source != destination:
        raise DocumentError("Плитка использует локальный !secret в другом каталоге")
    return result


def _entry_nodes(docs, path, destination):
    for key, value in _effective_pairs(docs.nodes[path]):
        logical = path + (key.value,)
        includes = docs.includes.get(logical)
        source = includes[0][0].file.parent if includes else docs.origins[logical].file.parent
        yield key.value, block_copy(value, source, destination)


def inline_tile(config, path, destination):
    docs = config.documents
    raw = docs.data_at(path)
    pairs = {}
    if "element" in raw and config.get("schema") in (2, 3):
        pairs.update(_entry_nodes(docs, ("elements", raw["element"]), destination))
    pairs.update(_entry_nodes(docs, path, destination))
    pairs.pop("element", None)
    pairs.pop("id", None)
    return pairs


def inline_migration_files(config):
    docs = config.documents
    if config.get("schema") == 3:
        raise DocumentError("Конфигурация уже использует schema: 3")
    root = docs.root.parent
    panels_file = Path("panels.yaml")
    groups = docs.includes.get(("panels",), [])
    # Only replace an existing panels file when that file exclusively
    # supplies the root panels key. Retain other source files for rollback.
    same_file = (len(groups) == 1 and docs.origins[("panels",)].file == root / panels_file
                 and not any(origin.file == root / panels_file and path[:1] != ("panels",)
                             for path, origin in docs.origins.items()))
    if not same_file and (root / panels_file).exists():
        panels_file = Path("panels-inline.yaml")
        if (root / panels_file).exists():
            raise DocumentError("Файл panels-inline.yaml уже существует")
    replacements = {}
    entries = [(path + (index,), tile)
               for path, _, tiles in tile_lists(config.panels, legacy=True)
               for index, tile in enumerate(tiles)]
    generated = re.compile(r"tile_[0-9a-f]{32}")
    occupied = {raw_id for path, _ in entries if isinstance(
        raw_id := docs.data_at(path).get("id"), str) and not generated.fullmatch(raw_id)}
    used = set()
    counters = {}
    for path, tile in entries:
        previous = docs.data_at(path).get("id")
        stable_id = (previous if isinstance(previous, str) and previous in occupied and not generated.fullmatch(previous) else next_tile_id(tile, occupied))
        if stable_id in used:
            raise DocumentError("Повторяющийся исходный id плитки")
        occupied.add(stable_id)
        used.add(stable_id)
        reserve_tile_id(counters, stable_id)
        fields = inline_tile(config, path, root)
        replacements[path] = _mapping([("id", _scalar(stable_id)), *fields.items()])

    def transform(path):
        if path in replacements:
            return replacements[path]
        node = docs.nodes[path]
        if not any(target[:len(path)] == path for target in replacements):
            includes = docs.includes.get(path)
            if includes and path != ("panels",):
                origin, reference = includes[0]
                return block_copy(reference, origin.file.parent, root)
            return block_copy(node, docs.origins[path].file.parent, root)
        if isinstance(node, yaml.MappingNode):
            return _mapping((key.value, transform(path + (key.value,)))
                            for key, _ in _effective_pairs(node))
        return yaml.SequenceNode(node.tag, [transform(path + (i,)) for i in range(len(node.value))])

    panels = transform(("panels",)) if ("panels",) in docs.nodes else _mapping([])
    if panels.tag == "!include" and same_file:
        panels = block_copy(docs.nodes[("panels",)], root, root)
    config_pairs = [("schema", _scalar("3", INT)),
                    ("panels", _scalar(str(panels_file), "!include"))]
    if counters:
        config_pairs.append(("last_tile_ids", _mapping((k, _scalar(v)) for k, v in counters.items())))
    config_pairs.extend((key, value) for key, value in _entry_nodes(docs, (), root)
                        if key not in {"schema", "panels", "elements"})
    return {panels_file: _serialize(panels), Path(docs.root.name): _serialize(_mapping(config_pairs))}


def id_changes(old, new):
    result = {}
    for path, _, tiles in tile_lists(old.documents.data.get("panels", {}), legacy=True):
        for index, tile in enumerate(tiles):
            previous = tile.get("id")
            if isinstance(previous, str) and re.fullmatch(r"tile_[0-9a-f]{32}", previous):
                result[previous] = new.documents.data_at(path + (index,))["id"]
    return result


def verify_migration(old, new):
    """Resolved data equality includes bindings, commands, order and sizes."""
    from . import web  # noqa: F401  registers screen routes
    from .web_screen import screen_layout

    def runtime(config):
        data = deepcopy(config.data)
        data.pop("schema", None)
        data.pop("elements", None)
        data.pop("last_tile_ids", None)
        for _path, _container, tiles in tile_lists(data.get("panels") or {}, legacy=True):
            for tile in tiles:
                tile.pop("id", None)
        return data

    if runtime(old) != runtime(new) or old.used_channels() != new.used_channels():
        raise DocumentError("Миграция изменила разрешённую конфигурацию")
    if any(screen_layout(name, old) != screen_layout(name, new) for name in old.panels):
        raise DocumentError("Миграция изменила ESP JSON")
