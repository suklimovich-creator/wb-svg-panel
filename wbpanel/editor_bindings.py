"""Registry-backed binding edits and dependency reporting."""
from copy import deepcopy
import math
import re
import yaml
from yaml.nodes import MappingNode
from . import yamlx
from .assemble import _from_names
from .editor_drafts import Changeset
from .editor_model import tile_lists
from .editor_operations import MAP, NODE_LOADER, OperationError, _node_at, _placement, _serialize, list_at, set_fields
from .editor_tile_fields import COMMON, FIELDS
from .registry import REGISTRY, resolve_type


def properties(definition):
    from .state import state
    kind = resolve_type(definition, state)
    cls = REGISTRY.get(kind)
    fields = dict(COMMON)
    if kind != 'header':
        fields['screen'] = ('Показывать на ESP32', 'boolean')
    if cls:
        tile = cls()
        if tile.roles or definition.get('service'):
            fields.update(source=('Источник каналов', 'choice', 'wb', 'raw', 'spruthub'),
                          service=('Служба Sprut.hub', 'topic'))
        for role in tile.schema():
            fields[tile.field_of(role)] = (role.label, 'channel')
            if role.writable and kind != 'value':
                fields[tile.command_of(role)] = ('Топик команды · ' + role.label, 'topic')
        fields.update(FIELDS.get(kind, {}))
        fields.update(getattr(cls, 'editor_properties', {}))
    elif definition.get('service'):
        fields['service'] = ('Служба Sprut.hub', 'topic')
    return fields


def locked(doc, path):
    def protected(node):
        if node.tag.startswith('!'):
            return True
        if isinstance(node, yaml.ScalarNode):
            return '${' in node.value
        if isinstance(node, yaml.MappingNode):
            return any(k.tag.endswith(':merge') or protected(k) or protected(v) for k, v in node.value)
        return any(protected(child) for child in node.value)
    source = doc.nodes.get(path)
    return path in doc.includes or (source is not None and protected(source))


def number(value):
    return type(value) in (float, int) and abs(value) <= 1e308 and math.isfinite(value)


def validate_structure(key, value):
    def plain(item):
        if isinstance(item, dict):
            for name, child in item.items():
                _placement({'title': name})
                plain(child)
        elif isinstance(item, list):
            for child in item:
                plain(child)
        elif isinstance(item, str):
            _placement({'title': item})
        elif item is not None and type(item) is not bool and not number(item):
            raise OperationError('Недопустимое значение', key)

    plain(value)
    rows = value if isinstance(value, list) else []
    valid = isinstance(value, list)
    if key in ('items', 'series', 'status'):
        valid = valid and all(isinstance(row, dict) for row in rows)
        if valid and key != 'status':
            valid = all(isinstance(row.get('channel'), str) and row['channel'].strip()
                        and not any(c in row['channel'] for c in '#+\r\n') for row in rows)
        if valid and key == 'items':
            valid = all('level' not in row or number(row['level']) and 0 <= row['level'] <= 1 for row in rows)
        if valid and key == 'status':
            for row in rows:
                required = row.get('require', [])
                conditions = required if isinstance(required, list) else [required]
                valid = (all(isinstance(row[k], str) for k in ('channel', 'value_channel', 'air') if k in row)
                         and all(not isinstance(item, dict) or isinstance(item.get('channel', ''), str)
                                 for item in conditions)
                         and all(number(row[k]) for k in ('above', 'below') if k in row)
                         and isinstance(row.get('badges', {}), dict))
                if not valid:
                    break
        if valid and key == 'series':
            for row in rows:
                valid = (all(number(row[k]) for k in ('min', 'max', 'grid') if k in row)
                         and ('grid' not in row or row['grid'] > 0)
                         and ('digits' not in row or type(row['digits']) is int and 0 <= row['digits'] <= 10)
                         and isinstance(row.get('thresholds', []), list)
                         and all(isinstance(t, dict) for t in row.get('thresholds', [])))
                if not valid:
                    break
    elif key == 'location':
        valid = isinstance(value, dict) and all(number(value.get(k)) and abs(value[k]) <= limit
                                                for k, limit in [('lat', 90), ('lon', 180)])
    elif key in ('modes', 'fans', 'fan_states'):
        valid = isinstance(value, dict) or valid and all(isinstance(row, dict) and 'value' in row for row in rows)
    elif key == 'calendars':
        valid = isinstance(value, str) or valid and all(isinstance(row, str) for row in rows)
    elif key in ('sensors', 'apps'):
        valid = valid and all(isinstance(row, (str, dict)) for row in rows) or (key == 'sensors' and isinstance(value, str))
    if not valid:
        raise OperationError('Проверьте структуру поля по примеру в форме', key)


def property_model(doc, name, definition, policy):
    result = []
    for key, spec in properties(definition).items():
        value = definition.get(key)
        protected = locked(doc, ('elements', name, key)) or policy.contains(str(value))
        result.append(dict(key=key, spec=spec, protected=protected, value=None if protected else value))
    return result


def apply_element(config, changeset, op, originals):
    doc = config.documents
    path = ('elements', op['element'])
    if path not in doc.origins:
        raise OperationError('Элемент не найден')
    definition = doc.data_at(path)
    specs = properties(definition)
    for key, value in op['fields'].items():
        if key not in specs or locked(doc, path + (key,)):
            raise OperationError('Поле недоступно для редактирования')
        kind = specs[key][1]
        if value is None:
            continue
        if kind == 'size':
            _placement({key: value})
        elif kind == 'integer':
            if type(value) is not int or not specs[key][2] <= value <= specs[key][3]:
                raise OperationError('Значение вне диапазона', key)
        elif kind == 'boolean':
            if type(value) is not bool:
                raise OperationError('Выберите да или нет', key)
        elif kind in ('number', 'positive', 'nonnegative'):
            if not number(value) or (kind == 'positive' and value <= 0) or (kind == 'nonnegative' and value < 0):
                raise OperationError('Недопустимое числовое значение', key)
        elif kind == 'json':
            validate_structure(key, value)
        elif kind == 'choice':
            if value not in specs[key][2:]:
                raise OperationError('Неизвестный режим', key)
        else:
            _placement({'title': value})
            if kind == 'url' and (re.match(r'^\s*[^/?#]*:', value) and
                                  not re.match(r'^https?://', value, re.I) or any(c in value for c in '\r\n\t')):
                raise OperationError('Укажите веб-адрес или относительный путь', key)
            if kind == 'duration' and not re.fullmatch(r'\s*[1-9]\d*\s*[smhd]?\s*', value):
                raise OperationError('Укажите период, например 12h', key)
            if kind in ('channel', 'topic') and (not value.strip() or value.strip() != value or
                    any(c in value for c in '#+\r\n') or
                    kind == 'channel' and '/' not in value and op['fields'].get('source', definition.get('source')) != 'raw'):
                raise OperationError('Требуется конкретный канал устройства', key)
    for low, high in [('temp_min', 'temp_max'), ('scale_min', 'scale_max'), ('target_min', 'target_max')]:
        if low in op['fields'] or high in op['fields']:
            lo = op['fields'].get(low, definition.get(low))
            hi = op['fields'].get(high, definition.get(high))
            if number(lo) and number(hi) and lo >= hi:
                raise OperationError('Верхняя граница должна быть больше нижней', high)
    return write_definition(doc, changeset, op, originals, path, op['element'])


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
    return write_definition(doc, changeset, op, originals, path, element)


def write_definition(doc, changeset, op, originals, path, element):
    expected = deepcopy(doc.data)
    definition = doc.data_at(path)
    fields = op['fields']
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
    event = {'op': op['op'], 'panel': op.get('panel'), 'section': op.get('section'),
             'id': op.get('id', element), 'fields': sorted(fields), 'file': relative.name,
             'scope': 'element' if element else 'instance'}
    if element:
        event['element'] = element
    return Changeset(files), expected, event, relative, original
