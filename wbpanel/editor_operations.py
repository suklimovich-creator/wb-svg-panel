"""Apply structural operations to unresolved, provenance-addressed YAML nodes."""

from copy import deepcopy
import math
import re

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import yamlx
from .editor_drafts import Changeset
from .editor_model import CUSTOM_TILE_ID, PLACEMENT, next_tile_id, placement_id, reserve_tile_id
from .editor_migration import _entry_nodes, _mapping, _scalar, block_copy, inline_tile, field_node
from .editor_documents import DocumentError

STR = "tag:yaml.org,2002:str"
NODE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
NODE_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)
MAP = "tag:yaml.org,2002:map"
SEQ = "tag:yaml.org,2002:seq"
_INVALID_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class OperationError(DocumentError):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


def list_at(data, panel, section):
    panels = data["panels"]
    if panel not in panels:
        raise OperationError("Панель не найдена", "panel")
    container = panels[panel]
    path = ("panels", panel)
    if section is not None:
        sections = container.get("sections") or []
        if type(section) is not int or not 0 <= section < len(sections):
            raise OperationError("Раздел не найден", "section")
        container = sections[section]
        if not isinstance(container, dict) or "from" in container or "tiles" not in container:
            raise OperationError("Раздел доступен только для чтения", "section")
        path += ("sections", section)
    return path, container


def _node_at(root, path):
    node = root
    for key in path:
        if isinstance(node, SequenceNode) and type(key) is int:
            node = node.value[key]
        elif isinstance(node, MappingNode):
            matches = [value for name, value in node.value
                       if name.tag == STR and name.value == key]
            if len(matches) != 1:
                raise OperationError("YAML-путь нельзя изменить однозначно")
            node = matches[0]
        else:
            raise OperationError("YAML-путь нельзя изменить однозначно")
    return node


def _placement(fields):
    if not isinstance(fields, dict) or not fields or fields.keys() - PLACEMENT:
        raise OperationError("Допустимы только w, h и title", "fields")
    for field, value in fields.items():
        if field == "title":
            if (not isinstance(value, str) or len(value) > 512 or "${" in value
                    or _INVALID_TEXT.search(value)
                    or value.lstrip().startswith(("!secret", "!include"))):
                raise OperationError("Требуется обычный текст до 512 символов", field)
        elif value is not None and (type(value) not in (int, float)
                                    or not 0.5 <= value <= 32
                                    or not math.isfinite(value)
                                    or value * 2 != int(value * 2)):
            raise OperationError("Размер от 0,5 до 32 с шагом 0,5", field)


def set_fields(node, target, fields):
    for key, value in fields.items():
        if value is None or value == "":
            target.pop(key, None)
            node.value[:] = [(k, v) for k, v in node.value if k.value != key]
        elif target.get(key) != value:
            target[key] = value
            new = yaml.SafeDumper("").represent_data(value)
            for i, (k, _v) in enumerate(node.value):
                if k.tag == STR and k.value == key:
                    node.value[i] = (k, new)
                    break
            else:
                node.value.append((ScalarNode(STR, key), new))


OP_FIELDS = {
    "add_instance": {"element"},
    "remove_instance": {"id"},
    "move_instance": {"id", "direction"},
    "update_instance": {"id", "fields"},
    "update_binding": {"id", "fields"},
    "update_panel": {"fields"},
    "update_section": {"fields"},
}


def validate(operation):
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise OperationError("Требуется структурная операция")
    kind = operation["op"]
    from .editor_structure import FIELDS, validate_structure
    fields = OP_FIELDS.get(kind, FIELDS.get(kind))
    required = fields | {"op", "panel", "section"} if fields is not None else set()
    optional = {"id"} if kind == "add_instance" else set()
    if fields is None or not required <= operation.keys() <= required | optional:
        raise OperationError("Неизвестная операция или лишние поля")
    for key in ("panel", "element", "id"):
        if key not in operation:
            continue
        value = operation[key]
        if not isinstance(value, str) or not value:
            raise OperationError("Требуется непустая строка", key)
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise OperationError("Требуется идентификатор из конфигурации", key)
    if kind in FIELDS:
        validate_structure(operation)
    if kind == "add_instance":
        if "id" in operation and not CUSTOM_TILE_ID.fullmatch(operation["id"]):
            raise OperationError("ID: до 64 латинских букв, цифр, дефисов и подчёркиваний", "id")
    if operation["section"] is not None and type(operation["section"]) is not int:
        raise OperationError("Требуется номер раздела", "section")
    if kind == "move_instance" and operation["direction"] not in ("up", "down"):
        raise OperationError("Допустимы направления up и down", "direction")
    if kind == "update_binding" and (not isinstance(operation["fields"], dict) or not operation["fields"]):
        raise OperationError("Укажите поля привязки", "fields")
    if kind == "update_instance":
        _placement(operation["fields"])
    elif kind in ("update_panel", "update_section"):
        from .editor_settings import validate_settings
        validate_settings(operation)


def apply(config, changeset, operation, originals):
    """Return staged bytes, expected resolved tree and a value-free event."""
    validate(operation)
    from .editor_structure import FIELDS, apply_structure
    if operation["op"] in FIELDS:
        return apply_structure(config, changeset, operation, originals)
    if operation["op"] in ("update_panel", "update_section"):
        from .editor_settings import apply_settings
        return apply_settings(config, changeset, operation, originals)
    if operation["op"] == "update_binding":
        from .editor_bindings import apply_binding
        return apply_binding(config, changeset, operation, originals)
    doc = config.documents
    expected = deepcopy(doc.data)
    panel, section, kind = (operation[k] for k in ("panel", "section", "op"))
    path, container = list_at(expected, panel, section)
    tiles = container.get("tiles", [])
    list_path = path + ("tiles",)
    stable_id = operation.get("id")
    if kind != "add_instance":
        try:
            origin = config.tile_origin(stable_id)
        except KeyError:
            raise OperationError("Экземпляр не найден", "id") from None
        if origin.logical_path[:-1] != list_path:
            raise OperationError("Экземпляр находится в другом списке", "id")
        index = origin.logical_path[-1]
    inline = config.get("schema") == 3
    if kind == "add_instance":
        element = operation.get("element")
        library = expected.get("elements", {})
        if element in library:
            definition = library[element]
            source_path = ("elements", element)
        elif inline and ("tile", element) in config.origins:
            source_path = config.tile_origin(element).logical_path
            definition = doc.data_at(source_path)
            if "element" in definition:
                definition = {**library[definition["element"]], **definition}
        else:
            raise OperationError("Источник плитки не найден", "element")
        occupied = {key for category, key in config.origins if category == "tile"}
        if stable_id in occupied:
            raise OperationError("Такой ID уже занят", "id")
        compact_id = placement_id(panel, {"element": element}, section)
        if not inline and any(t.get("element") == element for t in tiles):
            raise OperationError("Этот элемент уже размещён в выбранном разделе", "element")
        if not inline and stable_id is not None:
            raise OperationError("Для ссылки на библиотеку отдельный id не нужен", "id")
        compact = not inline
        if compact:
            stable_id = compact_id
        elif stable_id is None:
            stable_id = next_tile_id(definition, occupied | set(library) | set(
                expected.get("last_tile_ids", {}).values()))
    if kind != "update_instance":
        origin = doc.origins.get(list_path) or doc.origins[path]

    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError("Файл секретов доступен только для чтения")
    root = yaml.compose(doc.files[origin.file], Loader=NODE_LOADER)
    node = _node_at(root, origin.physical_path)
    logical = origin.logical_path
    actual = doc.nodes[logical]
    if node.tag != actual.tag:
        raise OperationError("Размещение с таким YAML-тегом доступно только для чтения")

    event = {"op": kind, "panel": panel, "section": section, "id": stable_id}
    if kind == "add_instance" and compact:
        event["element"] = element
    elif kind != "add_instance" and "id" not in tiles[index]:
        event["element"] = tiles[index]["element"]
    if kind == "update_instance":
        if not isinstance(node, MappingNode) or node.tag != MAP:
            raise OperationError("Экземпляр должен быть YAML-словарём")
        fields = operation["fields"]
        event["fields"] = sorted(fields)
        set_fields(node, tiles[index], fields)
    else:
        if logical != list_path:
            if not isinstance(node, MappingNode) or node.tag != MAP:
                raise OperationError("Список нельзя добавить в этот YAML-узел")
            sequence = SequenceNode(SEQ, [])
            node.value.append((ScalarNode(STR, "tiles"), sequence))
            node = sequence
            container["tiles"] = tiles
        if not isinstance(node, SequenceNode) or node.tag != SEQ:
            raise OperationError("Размещение должно быть YAML-списком")
        if kind == "add_instance":
            if not inline:
                entry = {"element": element} if compact else {"id": stable_id, "element": element}
                # Freeze library defaults at creation; absent sizes retain type defaults.
                entry.update({key: definition[key] for key in ("w", "h") if key in definition})
                fields = {key: field_node(doc, source_path + (key,), origin.file.parent)
                          for key in ("w", "h") if key in definition}
                entry_node = _mapping([(key, fields[key] if key in ("w", "h") else _scalar(value))
                                       for key, value in entry.items()])
            else:
                if source_path[0] == "elements":
                    fields = list(_entry_nodes(doc, source_path, origin.file.parent))
                else:
                    fields = list(inline_tile(config, source_path, origin.file.parent).items())
                entry_node = _mapping([("id", _scalar(stable_id)), *fields])
                entry = {"id": stable_id, **{key: value for key, value in definition.items()
                                             if key not in {"id", "element"}}}
            tiles.append(entry)
            node.value.append(entry_node)
        elif kind == "remove_instance":
            node.value.pop(index)
            tiles.pop(index)
        else:
            other = index + (-1 if operation["direction"] == "up" else 1)
            if not 0 <= other < len(tiles):
                raise OperationError("Достигнут край списка", "direction")
            for values in (tiles, node.value):
                values[index], values[other] = values[other], values[index]
            event["direction"] = operation["direction"]

    relative = origin.file.relative_to(doc.root.parent)
    files = dict(changeset._files)
    original = originals.get(relative, doc.files[origin.file])
    counters = expected.get("last_tile_ids", {})
    if inline and kind == "add_instance" and reserve_tile_id(counters, stable_id):
        expected["last_tile_ids"] = counters
        config_root = root if origin.file == doc.root else yaml.compose(
            doc.files[doc.root], Loader=NODE_LOADER)
        if not isinstance(config_root, MappingNode) or any(k.value == "<<" for k, _ in config_root.value):
            raise OperationError("Счётчик ID требует обычный корневой YAML-словарь", "id")
        config_root.value[:] = [(k, v) for k, v in config_root.value if k.value != "last_tile_ids"]
        config_root.value.append((_scalar("last_tile_ids"),
                                  _mapping((k, _scalar(v)) for k, v in counters.items())))
        if config_root is not root:
            files[doc.root.relative_to(doc.root.parent)] = _serialize(config_root)
    if expected != doc.data:
        body = _serialize(block_copy(root, origin.file.parent, origin.file.parent) if inline else root)
        baseline = _serialize(yaml.compose(original, Loader=NODE_LOADER))
        if body == baseline:
            files.pop(relative, None)
        else:
            files[relative] = body
    return Changeset(files), expected, dict(event, file=relative.name), relative, original


def _serialize(node):
    return yaml.serialize(node, Dumper=NODE_DUMPER, allow_unicode=True).encode("utf-8")
