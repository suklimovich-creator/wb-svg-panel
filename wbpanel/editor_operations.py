"""Apply structural operations to unresolved, provenance-addressed YAML nodes."""

from copy import deepcopy
import math
import re
import uuid

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from . import yamlx
from .editor_drafts import Changeset
from .editor_model import PLACEMENT
from .editor_documents import DocumentError

STR = "tag:yaml.org,2002:str"
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


OP_FIELDS = {
    "add_instance": {"element"},
    "remove_instance": {"id"},
    "move_instance": {"id", "direction"},
    "update_instance": {"id", "fields"},
}


def validate(operation):
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise OperationError("Требуется структурная операция")
    kind = operation["op"]
    fields = OP_FIELDS.get(kind)
    if fields is None or operation.keys() != fields | {"op", "panel", "section"}:
        raise OperationError("Неизвестная операция или лишние поля")
    for key in ("panel", "element", "id"):
        if key not in operation:
            continue
        value = operation[key]
        if not isinstance(value, str) or not value:
            raise OperationError("Требуется непустая строка", key)
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise OperationError("Требуется идентификатор из конфигурации", key)
    if operation["section"] is not None and type(operation["section"]) is not int:
        raise OperationError("Требуется номер раздела", "section")
    if kind == "move_instance" and operation["direction"] not in ("up", "down"):
        raise OperationError("Допустимы направления up и down", "direction")
    if kind == "update_instance":
        _placement(operation["fields"])


def apply(config, changeset, operation, originals):
    """Return staged bytes, expected resolved tree and a value-free event."""
    validate(operation)
    doc = config.documents
    expected = deepcopy(doc.data)
    panel, section, kind = (operation[k] for k in ("panel", "section", "op"))
    path, container = list_at(expected, panel, section)
    tiles = container.get("tiles", [])
    list_path = path + ("tiles",)
    stable_id = operation.get("id")
    if stable_id is not None:
        try:
            origin = config.tile_origin(stable_id)
        except KeyError:
            raise OperationError("Экземпляр не найден", "id") from None
        if origin.logical_path[:-1] != list_path:
            raise OperationError("Экземпляр находится в другом списке", "id")
        index = origin.logical_path[-1]
    if kind == "add_instance":
        element = operation["element"]
        if element not in expected["elements"]:
            raise OperationError("Элемент библиотеки не найден", "element")
        stable_id = "tile_" + uuid.uuid4().hex
        while ("tile", stable_id) in config.origins:
            stable_id = "tile_" + uuid.uuid4().hex
    if kind != "update_instance":
        origin = doc.origins.get(list_path) or doc.origins[path]

    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError("Файл секретов доступен только для чтения")
    root = yaml.compose(doc.files[origin.file], Loader=yaml.SafeLoader)
    node = _node_at(root, origin.physical_path)
    logical = origin.logical_path
    actual = doc.nodes[logical]
    if node.tag != actual.tag:
        raise OperationError("Размещение с таким YAML-тегом доступно только для чтения")

    event = {"op": kind, "panel": panel, "section": section, "id": stable_id}
    if kind == "update_instance":
        if not isinstance(node, MappingNode) or node.tag != MAP:
            raise OperationError("Экземпляр должен быть YAML-словарём")
        fields = operation["fields"]
        event["fields"] = sorted(fields)
        old = tiles[index]
        for key, value in fields.items():
            if value is None or value == "":
                old.pop(key, None)
                node.value[:] = [(k, v) for k, v in node.value if k.value != key]
            elif old.get(key) != value:
                old[key] = value
                new = yaml.SafeDumper("").represent_data(value)
                for i, (k, _v) in enumerate(node.value):
                    if k.tag == STR and k.value == key:
                        node.value[i] = (k, new)
                        break
                else:
                    node.value.append((ScalarNode(STR, key), new))
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
            entry = {"id": stable_id, "element": element}
            tiles.append(entry)
            node.value.append(yaml.SafeDumper("").represent_data(entry))
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
    if expected != doc.data:
        body = _serialize(root)
        baseline = _serialize(yaml.compose(original, Loader=yaml.SafeLoader))
        if body == baseline:
            files.pop(relative, None)
        else:
            files[relative] = body
    return Changeset(files), expected, dict(event, file=relative.name), relative, original


def _serialize(node):
    return yaml.serialize(node, Dumper=yaml.SafeDumper, allow_unicode=True).encode("utf-8")
