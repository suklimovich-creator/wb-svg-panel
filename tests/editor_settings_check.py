"""Panel settings: include preservation, scoped responses, validation and publication."""
from pathlib import Path
from unittest.mock import patch

from fixtures import env


def run():
    web, _, cfg, _sent = env.setup(Path(__file__).parent / 'fixtures/editor/managed.yaml', freeze_time=False)
    from wbpanel.editor_service import EditorService, SecretPolicy, EditorError
    from wbpanel.editor_drafts import Changeset
    from wbpanel.editor_revisions import RevisionStore
    from wbpanel.config import Config

    directory = Path(cfg.path).parent
    panels = directory / 'panels.yaml'
    panels.write_text(panels.read_text().replace('  cols: 3', '''  cols: 3
  screensaver:
    after: 1m
    panel: utility
    image: !secret label
    clock: false''') + '''
aliased: &shared_panel
  title: Shared
  tiles: []
alias_copy: *shared_panel
''')
    # Common login must not match server-generated names such as update_panel.
    with (directory / 'secrets.yaml').open('a') as stream:
        stream.write('\nlogin: panel\n')
    before = {p: p.read_bytes() for p in directory.glob('*.yaml')}
    service = EditorService(cfg.path)
    state = service.create()
    key = state['draft_id']
    room = next(p for p in state['panels'] if p['id'] == 'room')
    assert room['settings']['screensaver'] == {'after': '1m', 'panel': 'utility'}

    def edit(op, fields, panel='room', section=None):
        nonlocal state
        state = service.operation(key, state['draft_version'], dict(op=op, panel=panel, section=section, fields=fields), panel_only=True)
        assert [p['id'] for p in state['panels']] == [panel]
        assert not state['library'] and not state['themes']
        return state['panels'][0]

    # Ordinary saves neither render, recount all changes, nor re-read secrets.
    with patch.object(SecretPolicy, '__init__', side_effect=AssertionError('reloaded secrets')), \
         patch.object(Changeset, 'summary', side_effect=AssertionError('counted diff')), \
         patch.object(RevisionStore, '_verify', side_effect=AssertionError('rendered on save')):
        room = edit('update_panel', {'title': 'New room', 'cols': 2, 'hidden': True,
                    'tabs': False, 'accordion': True, 'swipe': False, 'theme': 'dark',
                    'pad_close': '30s', 'screensaver': {'after': '45s'}})
        assert room['settings']['screensaver'] == {'after': '45s', 'panel': 'utility'}
        assert room['title'] == 'New room' and room['cols'] == 2
    assert all(p.read_bytes() == data for p, data in before.items())
    candidate = service.sessions[key].candidate
    assert candidate.panels['room']['screensaver']['clock'] is False
    assert '!secret' in service.sessions[key].draft.changeset._files[Path('panels.yaml')].decode()
    previous = state['draft_version']
    edit('update_panel', {'cols': 2})
    assert state['draft_version'] == previous  # no-op
    for fields in ({'cols': 0}, {'cols': True}, {'cols': 1.5}, {'hidden': 'false'},
                   {'theme': 'unknown'}, {'pad_close': 'tomorrow'}, {'screensaver': -1},
                   {'screensaver': {'after': '1x'}}, {'screensaver': {'panel': 'room'}},
                   {'screensaver': {'panel': 'missing'}}, {'screensaver': {'image': 'url'}},
                   {'channel': 'x'}, {'title': '${label}'}):
        try:
            edit('update_panel', fields)
            raise AssertionError(('accepted invalid settings', fields))
        except EditorError as error:
            assert error.body['error']['code'] == 'operation'
            assert service.sessions[key].version == previous
    try:
        edit('update_panel', {'title': 'Alias edit'}, panel='aliased')
        raise AssertionError('changed shared alias')
    except EditorError as error:
        assert 'alias' in str(error)
    section = edit('update_section', {'title': 'Night', 'screensaver': '30s'}, panel='summary', section=0)['sections'][0]
    assert section['settings'] == {'title': 'Night', 'screensaver': '30s'}
    edit('update_section', {'screensaver': '1m'}, panel='summary', section=1)
    assert service.sessions[key].candidate.panels['summary']['sections'][1]['from'] == 'room'
    service.diff(key)
    revision = service.publish(key, state['draft_version'])
    published = Config(cfg.path, strict=True, confined=True)
    assert published.panels['room']['cols'] == 2 and published.panels['room']['hidden'] is True
    assert published.panels['summary']['sections'][0]['screensaver'] == '30s'
    for p, data in before.items():
        if p != panels:
            assert p.read_bytes() == data
    service.rollback(state['base_revision'], revision['id'])
    assert all(p.read_bytes() == data for p, data in before.items())
    # Exercise HTTP compatibility and partial response too.
    client = web.app.test_client()
    headers = {'X-WB-Editor': '1', 'Origin': 'http://localhost'}
    response = client.post('/api/editor/drafts', json={}, headers=headers)
    assert response.status_code == 201
    draft = response.json
    response = client.post('/api/editor/drafts/' + draft['draft_id'] + '/operation?result=panel', headers=headers,
                           json={'draft_version': 0, 'operation': {'op': 'update_panel', 'panel': 'room',
                                 'section': None, 'fields': {'hidden': True, 'screensaver': None}}})
    assert response.status_code == 200, response.json
    assert len(response.json['panels']) == 1 and 'screensaver' not in response.json['panels'][0]['settings']
    print('editor settings: includes, alias isolation, lazy checks, partial response, publish/rollback OK')


if __name__ == '__main__':
    run()
