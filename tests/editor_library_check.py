"""Shared binding, independent copies and publication/rollback regressions."""
from pathlib import Path
import tempfile
from fixtures import env

env.setup(Path(__file__).parent / 'fixtures/editor/managed.yaml', freeze_time=False)
from wbpanel.config import Config
from wbpanel.editor_service import EditorService, EditorError
from wbpanel.editor_model import tile_lists, placement_id
from wbpanel.registry import writable_of
from wbpanel.state import state as mqtt_state
from wbpanel.web_screen import screen_layout


def reject(call):
    try:
        call()
    except EditorError:
        return
    raise AssertionError('operation should fail')


def run(root, schema):
    root.mkdir()
    path = root / 'config.yaml'
    path.write_text('schema: %s\ninteractive: true\nelements: !include elements.yaml\npanels: !include panels.yaml\nscreens: {desk: a}\n' % schema)
    (root / 'elements.yaml').write_text('lamp: {type: switch, channel: dev/old, w: 1}\n')
    tile = '{element: lamp}' if schema == 2 else '{id: switch-001, type: switch, channel: dev/old}'
    second = '{element: lamp}' if schema == 2 else '{id: switch-002, type: switch, channel: dev/old}'
    (root / 'tiles.yaml').write_text('- ' + tile + '\n')
    (root / 'btiles.yaml').write_text('- ' + second + '\n')
    (root / 'bsections.yaml').write_text('- title: B\n  tiles: !include btiles.yaml\n')
    (root / 'panels.yaml').write_text('a:\n  title: A\n  tiles: !include tiles.yaml\nb:\n  sections: !include bsections.yaml\nsummary:\n  sections: [{from: [a, b]}]\n')
    originals = {p: p.read_bytes() for p in root.glob('*.yaml')}
    service = EditorService(path)
    model = service.create()
    key = model['draft_id']
    base = model['base_revision']
    first_id = model['panels'][0]['sections'][0]['tiles'][0]['id']

    def edit(op, panel='a', section=None, **fields):
        nonlocal model
        model = service.operation(key, model['draft_version'], dict(op=op, panel=panel, section=section, **fields))
        return service.get(key).candidate

    c = edit('update_instance', id=first_id, fields={'w': 2})
    assert c.panels['a']['tiles'][0]['w'] == 2
    assert c.panels['b']['sections'][0]['tiles'][0].get('w', 1) == 1
    c = edit('update_binding', id=first_id, fields={'channel': 'dev/new'})
    assert c.panels['a']['tiles'][0]['channel'] == 'dev/new'
    assert c.panels['b']['sections'][0]['tiles'][0]['channel'] == ('dev/new' if schema == 2 else 'dev/old')
    assert 'dev/new' in c.used_channels()
    c = edit('update_binding', id=first_id, fields={'command_topic': '/test/relay/set'})
    assert '/test/relay/set' in writable_of(c.panels['a']['tiles'][0], mqtt_state)
    c = edit('update_binding', id=first_id, fields={'command_topic': ''})
    assert '/devices/dev/controls/new/on' in writable_of(c.panels['a']['tiles'][0], mqtt_state)
    assert service.diff(key)['affected_panels'] == (['a', 'b', 'summary'] if schema == 2 else ['a', 'summary'])
    before_version = model['draft_version']
    reject(lambda: service.operation(key, 0, dict(op='copy_panel', panel='a', section=None, new_panel='stale', title='Stale')))
    reject(lambda: edit('update_binding', id=first_id, fields={'w': 3}))
    reject(lambda: edit('update_binding', panel='b', section=0, id=first_id, fields={'channel': 'dev/no'}))
    reject(lambda: edit('update_binding', id=first_id, fields={'channel': 'dev/+'}))
    assert service.get(key).version == before_version
    c = edit('copy_panel', new_panel='a-copy', title='Copy')
    assert c.get('screens') == {'desk': 'a'}
    copy_id = next(p for p in model['panels'] if p['id'] == 'a-copy')['sections'][0]['tiles'][0]['id']
    assert copy_id != first_id
    c = edit('update_instance', panel='a-copy', id=copy_id, fields={'w': 3})
    assert c.panels['a']['tiles'][0]['w'] == 2
    assert c.panels['a-copy']['tiles'][0]['w'] == 3
    c = edit('copy_section', panel='b', section=0, title='B copy')
    sections = next(p for p in model['panels'] if p['id'] == 'b')['sections']
    assert len(sections) == 2 and sections[0]['tiles'][0]['id'] != sections[1]['tiles'][0]['id']
    copy_section_id = sections[1]['tiles'][0]['id']
    c = edit('update_instance', panel='b', section=1, id=copy_section_id, fields={'h': 2})
    assert c.panels['b']['sections'][0]['tiles'][0].get('h', 1) == 1
    c = edit('copy_panel', panel='b', new_panel='b-copy', title='B copy')
    c = edit('copy_section', panel='summary', section=0, title='Referenced copy')
    assert c.panels['summary']['sections'][1]['from'] == ['a', 'b']
    ids = [placement_id(p[1], t, p[3] if len(p) == 5 else None) for p, _, ts in tile_lists(c.documents.data['panels']) for t in ts]
    assert len(set(ids)) == len(ids)
    if schema == 3:
        assert c.get('last_tile_ids')['switch'] == max(ids)
    reject(lambda: edit('copy_panel', new_panel='a', title='Collision'))
    reject(lambda: edit('copy_section', panel='b', section=77, title='Missing'))
    service.diff(key)
    # No file changes until publication; target rendering works with candidate.
    assert all(p.read_bytes() == data for p, data in originals.items())
    layout = screen_layout('a-copy', c)
    revision = service.publish(key, model['draft_version'])
    published = Config(path, strict=True)
    assert screen_layout('a-copy', published) == layout
    assert published.panels['a-copy']['tiles'][0]['w'] == 3
    service.rollback(base, revision['id'])
    assert all(p.read_bytes() == data for p, data in originals.items())


def protected(root):
    root.mkdir()
    path = root / 'config.yaml'
    path.write_text('''schema: 2
elements:
  lamp: {type: switch, channel: !secret private}
  alias: {type: switch, channel: dev/alias}
panels:
  a: {tiles: [{element: lamp}]}
  b: {tiles: [{element: lamp}]}
''')
    (root / 'secrets.yaml').write_text('private: dev/PRIVATE-SENTINEL\n')
    service = EditorService(path)
    model = service.create()
    assert 'PRIVATE-SENTINEL' not in str(model)
    key = model['draft_id']
    ident = model['panels'][0]['sections'][0]['tiles'][0]['id']
    service.operation(key, 0, dict(op='update_binding', panel='a', section=None, id=ident, fields={'command_topic': '/custom/set'}))
    candidate = service.get(key).candidate
    assert '!secret private' in candidate.documents.files[path].decode()
    assert 'PRIVATE-SENTINEL' not in str(service.diff(key))
    # Separate elements sharing one physical include must not be silently edited.
    other = root / 'other'; other.mkdir()
    (other / 'common.yaml').write_text('type: switch\nchannel: dev/old\n')
    (other / 'config.yaml').write_text('schema: 2\nelements:\n  one: !include common.yaml\n  two: !include common.yaml\npanels:\n  a: {tiles: [{element: one}, {element: two}]}\n')
    service = EditorService(other / 'config.yaml'); model = service.create()
    ident = model['panels'][0]['sections'][0]['tiles'][0]['id']
    reject(lambda: service.operation(model['draft_id'], 0, dict(op='update_binding', panel='a', section=None, id=ident, fields={'channel': 'dev/new'})))
    assert service.get(model['draft_id']).version == 0


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for schema in (2, 3):
        run(root / str(schema), schema)
    protected(root / 'protected')
print('editor library: shared/local bindings, copies, includes, secrets, conflicts, publish and rollback OK')
