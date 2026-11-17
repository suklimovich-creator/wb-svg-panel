"""Resolve managed panel tiles without changing the runtime tile contract."""

from copy import deepcopy
from dataclasses import replace

from .editor_documents import DocumentError
from .registry import REGISTRY

PLACEMENT = frozenset(("w", "h", "title"))


def _mapping(value, what):
    if not isinstance(value, dict):
        raise DocumentError("%s: ожидался словарь" % what)
    return value


def _sequence(value, what):
    if not isinstance(value, list):
        raise DocumentError("%s: ожидался список" % what)
    return value


def tile_lists(panels, legacy=False):
    for name, panel in panels.items():
        _mapping(panel, "Панель")
        base = ("panels", name)
        if "tiles" in panel and (not legacy or panel["tiles"] is not None):
            yield base + ("tiles",), panel, _sequence(panel["tiles"], "tiles")
        sections = panel.get("sections", [])
        if legacy and sections is None:
            continue
        for index, section in enumerate(_sequence(sections, "sections")):
            if not isinstance(section, dict):
                if legacy or isinstance(section, str):
                    continue
                raise DocumentError("Ожидалось описание раздела")
            if "tiles" in section and (not legacy or section["tiles"] is not None):
                yield base + ("sections", index, "tiles"), section, _sequence(section["tiles"], "tiles")


def resolve(documents):
    data = documents.data or {}
    _mapping(data, "Конфигурация")
    if "schema" not in data:
        return data, {}
    if type(data["schema"]) is not int or data["schema"] != 2:
        raise DocumentError("Неподдерживаемая версия schema")
    elements = _mapping(data.get("elements"), "elements")
    panels = _mapping(data.get("panels"), "panels")
    origins = {}
    for name, element in elements.items():
        _mapping(element, "Элемент")
        kind = element.get("type")
        if (not isinstance(name, str) or not name.strip()
                or "id" in element or "element" in element
                or (kind is not None and (not isinstance(kind, str) or kind not in REGISTRY))):
            raise DocumentError("Некорректное описание элемента библиотеки")
        origins[("element", name)] = replace(documents.origins[("elements", name)],
                                              stable_id=name)

    result = deepcopy(panels)
    for path, container, instances in tile_lists(result, legacy=True):
        resolved = []
        for index, item in enumerate(instances):
            _mapping(item, "Экземпляр")
            if item.keys() - PLACEMENT - {"id", "element"}:
                raise DocumentError("Экземпляр содержит запрещённое переопределение")
            stable_id, ref = item.get("id"), item.get("element")
            if not isinstance(stable_id, str) or not stable_id.strip():
                raise DocumentError("Экземпляру нужен стабильный id")
            key = ("tile", stable_id)
            if key in origins:
                raise DocumentError("Повторяющийся id экземпляра")
            if not isinstance(ref, str) or ref not in elements:
                raise DocumentError("Ссылка element не указывает на элемент библиотеки")
            origins[key] = replace(documents.origins[path + (index,)], stable_id=stable_id)
            tile = deepcopy(elements[ref])
            tile.update({field: item[field] for field in PLACEMENT if field in item})
            resolved.append(tile)
        container["tiles"] = resolved
    return dict(data, panels=result), origins
