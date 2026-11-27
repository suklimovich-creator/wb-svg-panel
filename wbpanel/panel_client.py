"""Page assets and wire compatibility, independent of editor/app releases."""
import hashlib
import re
from functools import lru_cache
from pathlib import Path
from .const import BASE_DIR

# Bump only when SVG attributes, command semantics or response format become
# incompatible with already-open pages. Compatible additions keep this value.
PANEL_PROTOCOL = '1'


def asset_revision(root):
    root = Path(root)
    wrapper = (root / 'templates/wrapper.html').read_bytes()
    paths = ['templates/wrapper.html', 'static/panel.css']
    paths += re.findall(r'<script src="(static/js/[\w-]+\.js)', wrapper.decode('utf-8'))
    digest = hashlib.sha256()
    for name in paths:
        digest.update(name.encode('utf-8') + b'\0')
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:20]


@lru_cache(maxsize=1)
def client_revision():
    # Read once per daemon lifetime, never per polling request.
    return asset_revision(BASE_DIR)


def client_headers():
    return {'X-Panel-Client': client_revision(), 'X-Panel-Protocol': PANEL_PROTOCOL}
