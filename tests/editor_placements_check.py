"""Backlog 11/12: container addresses, lossless migration and stale drafts."""
from pathlib import Path
import tempfile
from fixtures import env

env.setup(Path(__file__).parent / 'fixtures/editor/managed.yaml', freeze_time=False)
from wbpanel.config import Config
from wbpanel.editor_documents import DocumentError
from wbpanel.editor_revisions import RevisionStore
from wbpanel.editor_service import EditorService, EditorError
from wbpanel.editor_model import placement_id
from wbpanel.web_screen import screen_layout


def reject(error, call):
    try:
        call()
    except error:
        return
    raise AssertionError('must reject')


def run(root, schema):
    root.mkdir()
    path = root / 'config.yaml'
    path.write_text('schema: %d\nelements: !include elements.yaml\npanels: !include panels.yaml\n' % schema)
    (root / 'elements.yaml').write_text('lamp: {type: switch, channel: !secret channel, w: 2, h: 0.5}\n'
                                      'plain: {type: value, channel: dev/value}\n')
    (root / 'secrets.yaml').write_text('channel: dev/private-sentinel\n')
    (root / 'panels.yaml').write_text('''a:
  tiles: [{id: old-root, element: lamp}]
  sections:
  - title: Same
    tiles: [{id: old-first, element: lamp, w: 1.5}, {id: old-plain, element: plain}]
  - title: Same
    tiles: !include second.yaml
b: {tiles: [{element: lamp, h: 2}]}
summary: {sections: [{from: a}]}
''')
    (root / 'second.yaml').write_text('- {id: tile_0123456789abcdef0123456789abcdef, element: lamp, h: 1.5}\n')
    if schema == 3:
        with (root / 'second.yaml').open('a') as f:
            f.write('- {id: value-001, type: value, channel: dev/inline}\n')
    original = {p: p.read_bytes() for p in root.glob('*.yaml')}
    before = Config(path, strict=True)
    layouts = {name: screen_layout(name, before) for name in before.panels}
    store = RevisionStore(path)
    draft = store.migration()
    summary = store.inspect(draft, migrate=True)
    assert 'private-sentinel' not in repr(summary)
    assert all(p.read_bytes() == b for p, b in original.items())
    revision = store.publish(draft, migrate=True)
    after = Config(path, strict=True)
    assert {name: screen_layout(name, after) for name in after.panels} == layouts
    assert after.used_channels() == before.used_channels()
    raw = after.documents.data['panels']
    assert raw['a']['tiles'] == [{'element': 'lamp', 'w': 2, 'h': .5}]
    assert raw['a']['sections'][0]['tiles'][0] == {'element': 'lamp', 'w': 1.5, 'h': .5}
    assert raw['a']['sections'][0]['tiles'][1] == {'element': 'plain'}
    assert raw['a']['sections'][1]['tiles'][0] == {'element': 'lamp', 'w': 2, 'h': 1.5}
    assert raw['b']['tiles'][0] == {'element': 'lamp', 'w': 2, 'h': 2}
    assert (root / 'elements.yaml').read_bytes() == original[root / 'elements.yaml']
    assert '!secret' in (root / 'elements.yaml').read_text()
    assert '!include' in path.read_text()
    assert not store.migration().changeset._files
    assert store.inspect(store.migration(), migrate=True)['writes'] == 0
    if schema == 3:
        assert raw['a']['sections'][1]['tiles'][1]['id'] == 'value-001'
    service = EditorService(path)
    model = service.create()
    key = model['draft_id']
    ids = [s['tiles'][0]['id'] for s in model['panels'][0]['sections']]
    assert len(set(ids)) == 3
    assert ids[1] == placement_id('a', {'element': 'lamp'}, 0)
    model = service.operation(key, 0, {'op': 'update_instance', 'panel': 'a', 'section': 1,
        'id': ids[2], 'fields': {'w': 3}})
    c = service.get(key).candidate
    assert c.panels['a']['sections'][0]['tiles'][0]['w'] == 1.5
    assert c.panels['a']['sections'][1]['tiles'][0]['w'] == 3
    reject(EditorError, lambda: service.operation(key, 0, {'op': 'remove_instance', 'panel': 'a',
        'section': 1, 'id': ids[2]}))
    model = service.operation(key, 1, {'op': 'copy_section', 'panel': 'a', 'section': 0, 'title': 'Copy'})
    c = service.get(key).candidate
    assert 'id' not in c.documents.data['panels']['a']['sections'][1]['tiles'][0]
    # Index 1 now addresses a different section; old version must never edit it.
    reject(EditorError, lambda: service.operation(key, 1, {'op': 'update_instance', 'panel': 'a',
        'section': 1, 'id': ids[2], 'fields': {'w': 4}}))
    if schema == 2:
        reject(EditorError, lambda: service.operation(key, 2, {'op': 'add_instance', 'panel': 'a',
            'section': 1, 'element': 'lamp'}))
    service.diff(key)
    published = service.publish(key, model['draft_version'])
    service.rollback(draft.base_revision, published['id'])
    assert all(p.read_bytes() == b for p, b in original.items())


def duplicate(root):
    root.mkdir()
    path = root / 'config.yaml'
    path.write_text('schema: 2\nelements: {x: {type: switch, channel: dev/x}}\n'
                    'panels: {a: {tiles: [{id: first, element: x}, {id: second, element: x}]}}\n')
    original = path.read_bytes()
    reject(DocumentError, lambda: RevisionStore(path).migration())
    assert path.read_bytes() == original
    path.write_text(path.read_text().replace('id: first, ', '').replace('id: second, ', ''))
    reject(DocumentError, lambda: Config(path, strict=True))


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for schema in (2, 3):
        run(root / str(schema), schema)
    duplicate(root / 'duplicate')
print('placements: schema 2/3, sizes, includes, secrets, duplicates, stale versions, copies and rollback OK')
