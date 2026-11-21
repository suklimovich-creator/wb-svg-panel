"""Independent layout copies retaining shared library references."""
from copy import deepcopy
import yaml
from yaml.nodes import MappingNode, SequenceNode, ScalarNode
from . import yamlx
from .editor_drafts import Changeset
from .editor_migration import block_copy
from .editor_model import next_tile_id, reserve_tile_id
from .editor_operations import MAP, SEQ, STR, NODE_LOADER, OperationError, _node_at, _serialize, set_fields


def clone_node(docs, path, destination):
    node = docs.nodes[path]
    origin = docs.origins[path]
    # Expand structural includes; retain scalar references without their values.
    if isinstance(node, MappingNode):
        return MappingNode(MAP, [(yaml.SafeDumper('').represent_data(key), clone_node(docs, path + (key,), destination))
                                 for key in docs.data_at(path)])
    if isinstance(node, SequenceNode):
        return SequenceNode(SEQ, [clone_node(docs, path + (i,), destination) for i in range(len(node.value))])
    if path in docs.includes:
        origin, node = docs.includes[path][0]
    if node is None:
        return yaml.SafeDumper('').represent_data(None)
    try:
        return block_copy(node, origin.file.parent, destination)
    except ValueError:
        raise OperationError('Копирование между каталогами с локальными !secret пока не поддерживается') from None


def apply_copy(config, changeset, op, originals):
    docs = config.documents
    expected = deepcopy(docs.data)
    name, kind = op['panel'], op['op']
    if name not in expected['panels']:
        raise OperationError('Панель не найдена')
    source = ('panels', name)
    if kind == 'copy_panel':
        destination = ('panels',)
        if op['new_panel'] in expected['panels']:
            raise OperationError('Панель с таким ID уже существует', 'new_panel')
    else:
        sections = expected['panels'][name].get('sections') or []
        if not 0 <= op['section'] < len(sections) or not isinstance(sections[op['section']], dict):
            raise OperationError('Раздел не найден')
        destination = source + ('sections',)
        source = destination + (op['section'],)
    origin = docs.origins[destination]
    if origin.file.name == yamlx.SECRETS_NAME:
        raise OperationError('Файл секретов доступен только для чтения')
    root = yaml.compose(docs.files[origin.file], Loader=NODE_LOADER)
    target_node = _node_at(root, origin.physical_path)
    if target_node.tag != (MAP if kind == 'copy_panel' else SEQ):
        raise OperationError('Этот YAML-узел нельзя изменить однозначно')
    value = deepcopy(docs.data_at(source))
    copied = clone_node(docs, source, origin.file.parent)
    occupied = {key for category, key in config.origins if category == 'tile'}
    occupied.update(expected.get('elements', {}))
    occupied.update(expected.get('last_tile_ids', {}).values())

    counters = deepcopy(expected.get("last_tile_ids", {}))

    def new_ids(container, node):
        for index, tile in enumerate(container.get('tiles') or []):
            definition = expected.get('elements', {}).get(tile.get('element'), tile)
            new_id = next_tile_id(definition, occupied)
            occupied.add(new_id)
            reserve_tile_id(counters, new_id)
            set_fields(_node_at(node, ('tiles', index)), tile, {'id': new_id})
        for index, part in enumerate(container.get('sections') or []):
            if isinstance(part, dict):
                new_ids(part, _node_at(node, ('sections', index)))

    new_ids(value, copied)
    set_fields(copied, value, {'title': op['title']})
    if kind == 'copy_panel':
        expected['panels'][op['new_panel']] = value
        target_node.value.append((ScalarNode(STR, op['new_panel']), copied))
    else:
        expected['panels'][name] = deepcopy(expected['panels'][name])
        expected['panels'][name]['sections'].insert(op['section'] + 1, value)
        target_node.value.insert(op['section'] + 1, copied)
    relative = origin.file.relative_to(docs.root.parent)
    original = originals.get(relative, docs.files[origin.file])
    files = dict(changeset._files)
    if config.get("schema") == 3 and counters != expected.get("last_tile_ids", {}):
        expected["last_tile_ids"] = counters
        config_root = root if origin.file == docs.root else yaml.compose(docs.files[docs.root], Loader=NODE_LOADER)
        if not isinstance(config_root, MappingNode) or any(k.value == "<<" for k, _ in config_root.value):
            raise OperationError("Счётчик ID требует обычный корневой YAML-словарь")
        config_root.value[:] = [(k, v) for k, v in config_root.value if k.value != "last_tile_ids"]
        config_root.value.append((ScalarNode(STR, "last_tile_ids"), yaml.SafeDumper("").represent_data(counters)))
        if config_root is not root:
            files[docs.root.relative_to(docs.root.parent)] = _serialize(config_root)
    files[relative] = _serialize(root)
    event = {'op': kind, 'panel': name, 'section': op['section'], 'file': relative.name}
    if kind == 'copy_panel':
        event['new_panel'] = op['new_panel']
    return Changeset(files), expected, event, relative, original
