"""Draft operations on panel collections, section lists and ESP screen assignments."""
from copy import deepcopy
import re

import yaml
from yaml.nodes import MappingNode, SequenceNode

from . import yamlx
from .assemble import _from_names
from .editor_drafts import Changeset
from .editor_operations import NODE_LOADER, OperationError, _node_at, _placement, _serialize, set_fields

FIELDS = {"add_panel": {"title"}, "remove_panel": set(), "add_section": {"title"},
          "remove_section": set(), "assign_screen": {"screen"}}
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")


def validate_structure(op):
    if op["op"] == "remove_section":
        if type(op["section"]) is not int or op["section"] < 0:
            raise OperationError("Выберите раздел")
    elif op["section"] is not None:
        raise OperationError("У операции не должно быть номера раздела")
    if "title" in op:
        _placement({"title": op["title"]})
        if not op["title"].strip():
            raise OperationError("Введите название")
    if op["op"] == "add_panel" and not NAME.fullmatch(op["panel"]):
        raise OperationError("ID панели: до 64 латинских букв, цифр, дефисов и подчёркиваний")
    if "screen" in op and (not isinstance(op["screen"], str) or not NAME.fullmatch(op["screen"])):
        raise OperationError("Введите сетевое имя ESP32, например panel-6a10")


def ensure_removable(data, name):
    screens = data.get("screens") or {}
    if any((v.get("panel") if isinstance(v, dict) else v) == name for v in screens.values()):
        raise OperationError("Сначала переназначьте ESP32, использующие эту панель")
    for other, panel in data["panels"].items():
        if other != name and any(isinstance(s, dict) and name in _from_names(s.get("from"))
                                 for s in panel.get("sections") or []):
            raise OperationError("Панель используется как источник раздела другой панели")
    for container in [data] + [p for k, p in data["panels"].items() if k != name]:
        saver = container.get("screensaver")
        if isinstance(saver, dict) and (saver.get("panel") == name or name in _from_names(saver.get("panels"))):
            raise OperationError("Сначала измените ссылку на панель в настройках заставки")


def apply_structure(config, changeset, op, originals):
    doc = config.documents
    expected = deepcopy(doc.data)
    panels = expected["panels"]
    name, kind = op["panel"], op["op"]
    path = ("panels",)
    fields = {}
    move_tiles = False
    if kind == "add_panel":
        if name in panels:
            raise OperationError("Панель с таким ID уже существует")
        fields[name] = {"title": op["title"], "tiles": []}
    elif name not in panels:
        raise OperationError("Панель не найдена")
    elif kind == "remove_panel":
        ensure_removable(expected, name)
        fields[name] = None
    elif kind == "assign_screen":
        screen = op["screen"]
        screens = expected.get("screens")
        if screens is None:
            path = ()
            fields["screens"] = {screen: name}
        elif not isinstance(screens, dict):
            raise OperationError("screens должен быть словарём")
        elif isinstance(screens.get(screen), dict):
            path = ("screens", screen)
            fields["panel"] = name
        else:
            path = ("screens",)
            fields[screen] = name
    else:
        path += (name,)
        section = {"title": op.get("title"), "tiles": []}
        sections = panels[name].get("sections")
        if kind == "add_section" and (sections is None or (sections == [] and "tiles" in panels[name])):
            move_tiles = "tiles" in panels[name]
            fields["sections"] = [section]
        else:
            if not isinstance(sections, list):
                raise OperationError("Список разделов отсутствует")
            path += ("sections",)
    origin = doc.origins[path]
    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError("Этот файл доступен только для чтения")
    root = yaml.compose(doc.files[origin.file], Loader=NODE_LOADER)
    node = _node_at(root, origin.physical_path)
    target = expected
    for key in path:
        target = target[key]
    if fields and isinstance(node, MappingNode):
        if move_tiles:
            # Move the original YAML node, including a possible !include, without expanding it.
            if "sections" in target and _node_at(node, ("sections",)).tag != "tag:yaml.org,2002:seq":
                raise OperationError("Для переноса корневых плиток список разделов должен быть задан в самой панели")
            tiles_node = _node_at(node, ("tiles",))
            tiles = target["tiles"]
            set_fields(node, target, {"tiles": None})
        set_fields(node, target, fields)
        if move_tiles:
            first = _node_at(node, ("sections", 0))
            first.value = [(k, tiles_node if k.value == "tiles" else v) for k, v in first.value]
            target["sections"][0]["tiles"] = tiles
    elif not fields and isinstance(node, SequenceNode):
        if kind == "add_section":
            node.value.append(yaml.SafeDumper("").represent_data(section))
            target.append(section)
        else:
            index = op["section"]
            if index >= len(target):
                raise OperationError("Раздел не найден")
            del target[index]
            del node.value[index]
    else:
        raise OperationError("Структуру этого YAML нельзя изменить однозначно")
    relative = origin.file.relative_to(doc.root.parent)
    original = originals.get(relative, doc.files[origin.file])
    files = dict(changeset._files)
    if expected != doc.data:
        body = _serialize(root)
        if body == _serialize(yaml.compose(original, Loader=NODE_LOADER)):
            files.pop(relative, None)
        else:
            files[relative] = body
    event = {"op": kind, "panel": name, "section": op["section"], "file": relative.name}
    if kind == "assign_screen":
        event["id"] = op["screen"]
    return Changeset(files), expected, event, relative, original
