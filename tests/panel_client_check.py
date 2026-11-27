"""Compatibility headers and asset scope; no MQTT or network required."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / 'fixtures'))
import env
web, _, _, _ = env.setup(freeze_time=True)
from wbpanel.panel_client import asset_revision, client_headers, client_revision
client = web.app.test_client()
expected = client_headers()
for url in ('/gal.svg', '/_demo.svg', '/_demo-gal.svg'):
    response = client.get(url)
    assert response.status_code == 200, url
    for key, value in expected.items():
        assert response.headers[key] == value, (url, key)
    if url == '/_demo-gal.svg':
        continue  # Its seeded state may advance while rendering the first frame.
    # Exhausted polling slots return 204 with the same compatibility headers.
    with patch('wbpanel.web_live._waiters') as waiters:
        waiters.return_value.acquire.return_value = False
        response = client.get(url + '?since=' + response.headers['X-Panel-Version'])
        assert response.status_code == 204, (url, response.status_code)
        for key, value in expected.items():
            assert response.headers[key] == value, (url, key)
page = client.get('/gal.html').get_data(as_text=True)
assert '__CLIENT_JS__' not in page and '__PROTOCOL_JS__' not in page
assert client_revision() in page
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'templates').mkdir()
    (root / 'static/js').mkdir(parents=True)
    (root / 'templates/wrapper.html').write_text('<script src="static/js/update.js"></script>')
    (root / 'static/panel.css').write_text('body {}')
    (root / 'static/js/update.js').write_text('var a = 1;')
    initial = asset_revision(root)
    (root / 'static/js/editor.js').write_text('editor changes')
    assert asset_revision(root) == initial
    (root / 'static/js/update.js').write_text('var a = 2;')
    assert asset_revision(root) != initial
print('OK: compatibility headers (200/204), HTML bootstrap, editor-independent asset revision')
