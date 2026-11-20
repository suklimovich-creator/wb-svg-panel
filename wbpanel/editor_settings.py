"""Editable panel and section settings over the existing YAML contract."""

from copy import deepcopy
import math
import re

import yaml
from yaml.nodes import MappingNode

from . import palette, yamlx
from .editor_drafts import Changeset
from .editor_operations import NODE_LOADER, MAP, OperationError, _node_at, _placement, _serialize, set_fields

PANEL_FIELDS = frozenset(("title", "hidden", "cols", "theme", "tabs", "accordion",
                          "swipe", "pad_close", "screensaver"))
SECTION_FIELDS = frozenset(("title", "screensaver"))


def duration(value, field):
    if value is None or value == "none":
        return
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return
    if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*[smhd]?\s*", value):
        return
    raise OperationError("Укажите секунды или время вида 30s, 5m, 1h", field)


def validate_settings(operation):
    fields = operation["fields"]
    is_panel = operation["op"] == "update_panel"
    allowed = PANEL_FIELDS if is_panel else SECTION_FIELDS
    if not isinstance(fields, dict) or not fields or fields.keys() - allowed:
        raise OperationError("Переданы неподдерживаемые свойства", "fields")
    if is_panel != (operation["section"] is None):
        raise OperationError("Неверный адрес настроек", "section")
    for name, value in fields.items():
        if value is None:
            continue
        if name == "title":
            _placement({"title": value})
        elif name in ("hidden", "tabs", "accordion", "swipe"):
            if type(value) is not bool:
                raise OperationError("Требуется да или нет", name)
        elif name == "cols":
            if type(value) is not int or not 1 <= value <= 12:
                raise OperationError("Число колонок от 1 до 12", name)
        elif name == "theme":
            if not isinstance(value, str) or value not in palette.names():
                raise OperationError("Выберите существующую тему", name)
        elif name == "screensaver" and is_panel and isinstance(value, dict):
            if not value or value.keys() - {"after", "panel"}:
                raise OperationError("Допустимы задержка и панель заставки", name)
            if "after" in value:
                duration(value["after"], "screensaver.after")
            if "panel" in value and value["panel"] is not None and not isinstance(value["panel"], str):
                raise OperationError("Выберите панель заставки", "screensaver.panel")
        else:
            duration(value, name)


def settings_model(panel):
    result = {key: panel[key] for key in PANEL_FIELDS - {"screensaver"} if key in panel}
    saver = panel.get("screensaver")
    if isinstance(saver, dict):
        result["screensaver"] = {key: saver[key] for key in ("after", "panel") if key in saver}
    elif "screensaver" in panel:
        result["screensaver"] = saver
    return result


def apply_settings(config, changeset, operation, originals):
    doc = config.documents
    expected = deepcopy(doc.data)
    name, section = operation["panel"], operation["section"]
    if name not in expected["panels"]:
        raise OperationError("Панель не найдена", "panel")
    # Detach aliases in the expected tree so modifying another panel is detected.
    panel = expected["panels"][name] = deepcopy(expected["panels"][name])
    path = ("panels", name)
    target = panel
    if section is not None:
        sections = panel.get("sections") or []
        if not 0 <= section < len(sections) or not isinstance(sections[section], dict):
            raise OperationError("Раздел недоступен для настройки", "section")
        target = sections[section] = deepcopy(sections[section])
        path += ("sections", section)
    origin = doc.origins[path]
    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError("Настройки из файла секретов доступны только для чтения")
    root = yaml.compose(doc.files[origin.file], Loader=NODE_LOADER)
    node = _node_at(root, origin.physical_path)
    if not isinstance(node, MappingNode) or node.tag != MAP:
        raise OperationError("Настройки должны быть YAML-словарём")
    fields = dict(operation["fields"])
    saver = fields.pop("screensaver", None)
    if "screensaver" in operation["fields"]:
        if isinstance(saver, dict):
            destination = saver.get("panel")
            if destination and (destination not in config.panels or destination == name):
                raise OperationError("Выберите другую существующую панель", "screensaver.panel")
            existing = target.get("screensaver")
            if isinstance(existing, dict):
                matches = [value for key, value in node.value if key.value == "screensaver"]
                if not matches or not isinstance(matches[0], MappingNode) or matches[0].tag != MAP:
                    raise OperationError("Заставка из include или alias требует настройки в исходном файле",
                                         "screensaver")
                target["screensaver"] = deepcopy(existing)
                set_fields(matches[0], target["screensaver"], saver)
            else:
                data = {"after": existing} if existing is not None else {}
                data.update(saver)
                fields["screensaver"] = {key: value for key, value in data.items() if value is not None}
        else:
            fields["screensaver"] = saver
    set_fields(node, target, fields)
    relative = origin.file.relative_to(doc.root.parent)
    original = originals.get(relative, doc.files[origin.file])
    files = dict(changeset._files)
    if expected != doc.data:
        data = _serialize(root)
        if data == _serialize(yaml.compose(original, Loader=NODE_LOADER)):
            files.pop(relative, None)
        else:
            files[relative] = data
    event = {"op": operation["op"], "panel": name, "section": section,
             "fields": sorted(operation["fields"]), "file": relative.name}
    return Changeset(files), expected, event, relative, original
