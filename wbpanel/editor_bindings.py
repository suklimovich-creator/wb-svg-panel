"""Registry-backed binding edits and dependency reporting."""
from copy import deepcopy
import yaml
from yaml.nodes import MappingNode
from . import yamlx
from .assemble import _from_names
from .editor_drafts import Changeset
from .editor_model import tile_lists
from .editor_operations import MAP, NODE_LOADER, OperationError, _node_at, _placement, _serialize, list_at, set_fields
from .registry import REGISTRY, resolve_type


def binding_fields(definition):
    return getattr(REGISTRY.get(resolve_type(definition, None)), 'editor_bindings', {})


def affected_panels(data, names):
    affected = set(names)
    panels = data['panels']
    while True:
        before = set(affected)
        for name, panel in panels.items():
            sources = []
            for part in panel.get('sections') or []:
                if isinstance(part, dict):
                    sources.extend(_from_names(part.get('from')))
            saver = panel.get('screensaver', data.get('screensaver'))
            if isinstance(saver, dict):
                sources.extend(_from_names(saver.get('panel')))
                sources.extend(_from_names(saver.get('panels')))
            if affected.intersection(sources):
                affected.add(name)
        if affected == before:
            return [name for name in panels if name in affected]


def element_panels(data, element):
    direct = {path[1] for path, _, tiles in tile_lists(data['panels'])
              if any(tile.get('element') == element for tile in tiles)}
    return affected_panels(data, direct)


def binding_model(definition, policy):
    fields = binding_fields(definition)
    if not fields:
        return None
    if definition.get('service') or definition.get('roles'):
        return {'readonly': True, 'reason': 'Привязка через service/roles пока доступна только в YAML.'}
    result = {'fields': []}
    for name, label in fields.items():
        value = definition.get(name, '')
        protected = not isinstance(value, str) or policy.contains(value)
        result['fields'].append({'name': name, 'label': label,
                                 'value': '' if protected else value, 'protected': protected})
    return result


def apply_binding(config, changeset, op, originals):
    doc = config.documents
    expected = deepcopy(doc.data)
    list_path, container = list_at(doc.data, op['panel'], op['section'])
    try:
        origin = config.tile_origin(op['id'])
    except KeyError:
        raise OperationError('Экземпляр не найден', 'id') from None
    if origin.logical_path[:-1] != list_path + ('tiles',):
        raise OperationError('Экземпляр находится в другом списке', 'id')
    tile = container['tiles'][origin.logical_path[-1]]
    element = tile.get('element')
    path = ('elements', element) if element else origin.logical_path
    definition = doc.data_at(path)
    fields = op['fields']
    if not binding_fields(definition) or fields.keys() - binding_fields(definition).keys():
        raise OperationError('Эти поля привязки пока не поддерживаются', 'fields')
    if definition.get('service') or definition.get('roles'):
        raise OperationError('Привязка через service/roles пока доступна только в YAML')
    for name, value in fields.items():
        _placement({'title': value})
        if name == 'channel' and (not value.strip() or '/' not in value):
            raise OperationError('Введите канал в формате устройство/канал', name)
        if value != value.strip() or any(c in value for c in ('\n', '\r', '\x00', '#', '+')):
            raise OperationError('Введите конкретный канал без MQTT-масок и перевода строки', name)
    origin = doc.origins[path]
    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError('Файл секретов доступен только для чтения')
    root = yaml.compose(doc.files[origin.file], Loader=NODE_LOADER)
    node = _node_at(root, origin.physical_path)
    if not isinstance(node, MappingNode) or node.tag != MAP:
        raise OperationError('Привязка должна быть YAML-словарём')
    parent = expected
    for key in path[:-1]:
        parent[key] = deepcopy(parent[key])
        parent = parent[key]
    parent[path[-1]] = deepcopy(definition)
    set_fields(node, parent[path[-1]], fields)
    relative = origin.file.relative_to(doc.root.parent)
    original = originals.get(relative, doc.files[origin.file])
    files = dict(changeset._files)
    if expected != doc.data:
        body = _serialize(root)
        if body == _serialize(yaml.compose(original, Loader=NODE_LOADER)):
            files.pop(relative, None)
        else:
            files[relative] = body
    event = {'op': op['op'], 'panel': op['panel'], 'section': op['section'],
             'id': op['id'], 'fields': sorted(fields), 'file': relative.name,
             'scope': 'element' if element else 'instance'}
    if element:
        event['element'] = element
    return Changeset(files), expected, event, relative, original
