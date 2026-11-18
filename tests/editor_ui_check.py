"""Structural editor HTTP, provenance, response safety and publication checks."""

from concurrent.futures import ThreadPoolExecutor
from html import escape
import io
import logging
from pathlib import Path
import threading
from unittest.mock import patch
from urllib.parse import quote
from xml.etree import ElementTree

from fixtures import env

PREFIX = "/api/editor"
HEADERS = {"X-WB-Editor": "1", "Origin": "http://localhost"}
PRIVATE = ("TEST-Private-&-секрет", "/editor-sentinel/private/state", "test-editor-token-abc")
FIXTURE = Path(__file__).parent / "fixtures" / "editor" / "managed.yaml"


def safe(response):
    text = response.get_data(as_text=True)
    for value in PRIVATE:
        assert all(token not in text for token in (value, escape(value), quote(value, safe="")))
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers
    return response


def run():
    web, _palette, cfg, sent = env.setup(FIXTURE, freeze_time=False)
    from wbpanel import assemble, editor_revisions
    from wbpanel.editor_revisions import RevisionStore
    from wbpanel.editor_service import SecretPolicy

    client = web.app.test_client()
    directory = Path(cfg.path).parent
    store = RevisionStore(cfg.path, create=False)

    def get(path, code=200):
        resp = safe(client.get(PREFIX + path))
        assert resp.status_code == code, (path, resp.status_code, resp.get_json(silent=True))
        return resp

    def post(path, data, code=200):
        resp = safe(client.post(PREFIX + path, json=data, headers=HEADERS))
        assert resp.status_code == code, (path, resp.status_code, resp.get_json())
        return resp.get_json()

    def draft():
        state = post("/drafts", {}, 201)
        return "/drafts/" + state["draft_id"], state

    def operation(url, state, op, *, panel="room", section=None, code=200, **fields):
        return post(url + "/operation", {
            "draft_version": state["draft_version"],
            "operation": dict(op=op, panel=panel, section=section, **fields),
        }, code)

    assert "Редактор панелей" in client.get("/").get_data(as_text=True)
    for url in ("/editor", "/editor/room"):
        assert b"editor.js" in safe(client.get(url)).data
    assert get("/state").get_json()["managed"]
    assert get("/revisions").get_json()["head"] is None
    assert not store.directory.exists()
    assert safe(client.post(PREFIX + "/drafts", json={})).status_code == 403
    assert safe(client.post(PREFIX + "/drafts", json={}, headers={
        **HEADERS, "Origin": "http://elsewhere.invalid"})).status_code == 403
    assert safe(client.post(PREFIX + "/drafts", json={}, headers={
        **HEADERS, "X-WB-Editor": "2"})).status_code == 403
    assert safe(client.post(PREFIX + "/drafts", data="{}", headers=HEADERS)).status_code == 415
    for raw, code in (("{", 400), ("[" + " " * 8192 + "]", 413), ('{"x":1,"x":2}', 400)):
        assert safe(client.post(PREFIX + "/drafts", data=raw, headers=HEADERS,
                                content_type="application/json")).status_code == code
    # Cloud-style termination and host rewriting upstream from nginx.
    cloud = {**HEADERS, "Origin": "https://panel.http.wirenboard.cloud",
             "X-Forwarded-Proto": "http"}
    for headers in (
        cloud,
        {**cloud, "Sec-Fetch-Site": "same-site"},
        {**cloud, "Sec-Fetch-Site": "cross-site"},
        {**cloud, "Sec-Fetch-Site": "same-origin", "Origin": "null"},
        {**cloud, "Sec-Fetch-Site": "same-origin", "X-WB-Editor": "2"},
    ):
        denied = safe(client.post(PREFIX + "/drafts", json={}, headers=headers))
        assert denied.status_code == 403 and denied.get_json()["error"]["code"] == "origin"
    assert not store.directory.exists()
    allowed = safe(client.post(PREFIX + "/drafts", json={},
                               headers={**cloud, "Sec-Fetch-Site": "same-origin"}))
    assert allowed.status_code == 201, (allowed.status_code, (allowed.get_json() or {}).get("error", {}).get("code"))
    post("/drafts", {"path": "../outside.yaml", "_files": {}}, 400)
    get("/drafts/missing", 404)
    get("/drafts/missing/diff", 404)
    get("/drafts/missing/preview/room.svg", 404)
    get("/drafts/missing/tile/room/first.svg", 404)
    post("/drafts/missing/operation", {"draft_version": 0, "operation": {}}, 404)
    post("/drafts/missing/publish", {"draft_version": 0}, 404)

    base_channels = cfg.used_channels()
    baseline_svg = client.get("/room.svg").get_data(as_text=True)
    assert "/custom/test-editor-token-abc/set" in baseline_svg
    utility_layout = client.get("/api/screen/utility").get_json()
    assert cfg.tile_origin("first").file.name == "placement.yaml"
    expected_files = {p: p.read_bytes() for p in cfg.documents.files}
    first, initial = draft()
    ElementTree.fromstring(get(first + "/preview/room.svg").data)
    def protected_render(*args, **kwargs):
        web.log.warning("Рендер: %s", PRIVATE[0])
        return "<svg>TEST-Private-&amp;-секрет</svg>"

    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    web.log.addHandler(handler)
    try:
        with patch.object(web, "render_panel", side_effect=protected_render):
            assert get(first + "/preview/room.svg", 422).get_json()["error"]["code"] == "protected_output"
        web.log.warning("Обычный запрос")
    finally:
        web.log.removeHandler(handler)
    assert PRIVATE[0] not in captured.getvalue() and "Обычный запрос" in captured.getvalue()
    from wbpanel import web_editor
    with patch.object(web_editor, "render_tile", return_value="<svg>" + quote(PRIVATE[0]) + "</svg>"):
        assert get(first + "/tile/room/first.svg", 422).get_json()["error"]["code"] == "protected_output"
    redacted = ('<svg xmlns="http://www.w3.org/2000/svg"><text>TEST-Private-&amp;-секрет</text>'
                '<g data-topic="' + quote(PRIVATE[0]) + '"><text>Дом</text></g></svg>')
    with patch.object(web, "render_panel", return_value=redacted):
        clean = ElementTree.fromstring(get(first + "/preview/room.svg").data)
        assert "Дом" in "".join(clean.itertext()) and "data-topic" not in clean[1].attrib
    second, original = draft()
    state = get(first).get_json()
    assert state == initial and state["draft_id"] != original["draft_id"]
    assert state["panels"][1]["sections"][1]["sources"] == ["room"]
    assert state["panels"][1]["sections"][1]["readonly"]
    assert "command_topic" not in repr(state) and "channel_brightness" not in repr(state)
    for op, fields in (
        ("add_instance", {"element": "missing"}),
        ("add_instance", {"element": "light", "id": "first"}),
        ("add_instance", {"element": "light", "_files": {"/tmp/file.yaml": "value"}}),
        ("remove_instance", {"id": "missing"}),
        ("remove_instance", {"id": "inline_1"}),
        ("move_instance", {"id": "first", "direction": "up"}),
        ("update_instance", {"id": "first", "fields": {"w": "1"}}),
        ("update_instance", {"id": "first", "fields": {"h": True}}),
        ("update_instance", {"id": "first", "fields": {"w": 0.25}}),
        ("update_instance", {"id": "first", "fields": {"service": "other"}}),
        ("update_instance", {"id": "first", "fields": {"title": "Bad\x00XML"}}),
        ("update_instance", {"id": "first", "fields": {"title": "!secret label"}}),
        ("update_instance", {"id": "first", "fields": {"title": "!include /tmp/outside.yaml"}}),
        ("update_instance", {"id": "first", "fields": {"title": "${label}"}}),
        ("yaml", {"yaml": "x: !include ../file.yaml"}),
    ):
        operation(first, state, op, code=400, **fields)
    operation(first, state, "add_instance", element="light", panel="../outside", code=400)
    operation(first, state, "add_instance", element="light", panel="/outside", code=400)
    operation(first, state, "add_instance", element="light", panel="summary", section=1, code=400)
    for invalid_operation in (None, [], 2, "yaml"):
        post(first + "/operation", {"draft_version": 0, "operation": invalid_operation}, 400)
    post(first + "/operation", {"draft_version": True, "operation": {}}, 400)

    undone, version = draft()
    warned = operation(undone, version, "add_instance", element="invalid_switch")
    invalid_id = warned["panels"][0]["sections"][0]["tiles"][-1]["id"]
    for endpoint in ("/diff", "/preview/room.svg"):
        failure = get(undone + endpoint, 422).get_json()["error"]
        assert failure["id"] == invalid_id and failure["panel"] == "room"
    undone_state = operation(undone, warned, "remove_instance", id=invalid_id)
    undone_state = operation(undone, undone_state, "move_instance", id="first", direction="down")
    operation(undone, undone_state, "move_instance", id="first", direction="up")
    assert get(undone + "/diff").get_json()["summary"]["writes"] == 0

    raced, open_state = draft()

    def simultaneous(title):
        with web.app.test_client() as contender:
            response = safe(contender.post(PREFIX + raced + "/operation", headers=HEADERS, json={
                "draft_version": open_state["draft_version"],
                "operation": {"op": "update_instance", "panel": "room", "section": None,
                              "id": "first", "fields": {"title": title}},
            }))
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(simultaneous, ("Первый запрос", "Второй запрос")))
    assert set(results) == {200, 409} and results[409]["error"]["current"] == results[200]
    state = operation(first, state, "add_instance", element="light")
    tiles = state["panels"][0]["sections"][0]["tiles"]
    first_id = tiles[-1]["id"]
    assert tiles[-1]["element"] == tiles[0]["element"] and first_id not in ("first", "second")
    assert len(get(second).get_json()["panels"][0]["sections"][0]["tiles"]) == 3
    stale = operation(first, initial, "remove_instance", id="first", code=409)
    assert stale["error"]["current"] == state
    state = operation(first, state, "move_instance", id="first", direction="down")
    state = operation(first, state, "update_instance", id="first",
                      fields={"w": 1.5, "h": 1, "title": "Черновик"})
    state = operation(first, state, "update_instance", id="first", fields={"title": ""})
    tile = state["panels"][0]["sections"][0]["tiles"][1]
    assert tile["id"] == "first" and tile["w"] == 1.5 and "title" not in tile
    state = operation(first, state, "update_instance", id="first", fields={"title": "Новая плитка"})
    state = operation(first, state, "remove_instance", id="third")
    assert any(e["id"] == "switch" for e in state["library"])
    state = operation(first, state, "add_instance", panel="summary", section=0, element="light")
    inline = state["panels"][1]["sections"][0]["tiles"]
    assert len(inline) == 3 and inline[-1]["id"] != first_id
    state = operation(first, state, "move_instance", panel="summary", section=0,
                      id="inline_1", direction="down")
    state = operation(first, state, "update_instance", panel="summary", section=0,
                      id="inline_1", fields={"title": "Своя", "h": 0.5})
    state = operation(first, state, "remove_instance", panel="summary", section=0, id="inline_2")
    assert [i["id"] for i in state["panels"][1]["sections"][0]["tiles"]] == ["inline_1", inline[-1]["id"]]
    post(first + "/publish", {"draft_version": state["draft_version"]}, 409)
    get(first + "/preview/missing.svg", 404)
    get(first + "/preview/room.svg?cols=0", 400)
    get(first + "/preview/room.svg?draft_version=0", 409)
    get(first + "/tile/summary/first.svg", 404)
    svg = get(first + "/preview/room.svg?cols=3&theme=dark").get_data(as_text=True)
    assert "Новая плитка" in svg and "Первая" not in svg
    ElementTree.fromstring(svg)
    pad = get(first + "/tile/room/first.svg").data
    ElementTree.fromstring(pad)
    assert sent == []
    policy = SecretPolicy(cfg)
    assert policy.contains("TEST-Private-%26-%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82")
    assert policy.contains("TEST-Private-&amp;-\\u0441екрет")
    verified = get(first + "/diff").get_json()
    assert verified["summary"]["writes"] == 3 and verified["summary"]["instances"]["added"] == 2
    assert verified == get(first + "/diff").get_json()
    assert not store.journal.exists() and len(get("/revisions").get_json()["revisions"]) == 1
    assert all(p.read_bytes() == original for p, original in expected_files.items())
    assert all(x.read_bytes() == expected_files[x] for x in expected_files if x.name != "config.yaml")

    # Hold the candidate request inside the actual prepare/render path.
    started = threading.Event()
    release = threading.Event()
    prepare = web.prepare

    def pause(*args, **kwargs):
        if kwargs.get("preview"):
            started.set()
            assert release.wait(10)
        return prepare(*args, **kwargs)

    def preview():
        with web.app.test_client() as preview_client:
            return safe(preview_client.get(PREFIX + first + "/preview/room.svg")).data.decode()

    with patch.object(web, "prepare", pause), ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(preview)
        try:
            assert started.wait(10)
            active = client.get("/room.svg?cols=3&theme=dark").get_data(as_text=True)
            assert "Первая" in active and "Новая плитка" not in active
            assert web.config is cfg and assemble.config is cfg
        finally:
            release.set()
        assert "Новая плитка" in pending.result(timeout=10)

    original_replace = editor_revisions._replace
    physical = directory / "placement.yaml"

    def fail_second(path, payload, mode=0o600):
        if path == physical and payload != expected_files[physical]:
            raise OSError("injected")
        return original_replace(path, payload, mode)

    with patch.object(editor_revisions, "_replace", fail_second):
        post(first + "/publish", {"draft_version": state["draft_version"]}, 500)
    assert all(p.read_bytes() == content for p, content in expected_files.items())
    assert store.head.read_text() == state["base_revision"] and not store.journal.exists()

    published = post(first + "/publish", {"draft_version": state["draft_version"]})
    assert "Новая плитка" in client.get("/room.svg").get_data(as_text=True)
    assert cfg.tile_origin("first").logical_path[-1] == 1
    assert cfg.tile_origin("first").file == physical
    include = cfg.documents.includes[cfg.tile_origin("first").logical_path][0][1]
    assert include.tag == "!include" and include.value == "placement.yaml"
    panels_yaml = (directory / "panels.yaml").read_text()
    assert "!secret" in panels_yaml and "${label}" in panels_yaml
    assert all(secret not in panels_yaml for secret in PRIVATE)
    assert cfg.used_channels() == base_channels
    assert client.get("/api/screen/utility").get_json() == utility_layout
    assert "/custom/test-editor-token-abc/set" in client.get("/utility.svg").get_data(as_text=True)
    assert (directory / "library.yaml").read_bytes() == expected_files[directory / "library.yaml"]
    assert (directory / "secrets.yaml").read_bytes() == expected_files[directory / "secrets.yaml"]
    assert client.get("/api/screen/room").status_code == 200
    post(first + "/publish", {"draft_version": state["draft_version"]}, 404)
    get(second + "/diff", 409)
    post(second + "/publish", {"draft_version": original["draft_version"]}, 409)

    for filename in ("secrets.yaml", "library.yaml"):
        fresh, latest = draft()
        path = directory / filename
        prior = path.read_bytes()
        path.write_bytes(prior + b"\n# external modification\n")
        get(fresh + "/preview/room.svg", 409)
        post(fresh + "/publish", {"draft_version": latest["draft_version"]}, 409)
        path.write_bytes(prior)

    symlink_draft, _ = draft()
    placement_list = directory / "placements.yaml"
    payload = placement_list.read_bytes()
    outside = directory.parent / (directory.name + "-outside.yaml")
    outside.write_bytes(payload)
    try:
        placement_list.unlink()
        placement_list.symlink_to(outside)
        get(symlink_draft + "/diff", 409)
        assert outside.read_bytes() == payload
    finally:
        placement_list.unlink()
        placement_list.write_bytes(payload)
        outside.unlink()

    base = {"revision": state["base_revision"], "base_revision": published["id"], "confirm": False}
    post("/rollback", base, 400)
    post("/rollback", {**base, "confirm": True, "revision": "f" * 32}, 404)
    post("/rollback", {**base, "confirm": True, "base_revision": state["base_revision"]}, 409)
    restored = post("/rollback", {**base, "confirm": True})
    history = get("/revisions").get_json()
    assert [rv["id"] for rv in history["revisions"]] == [restored["id"], published["id"], state["base_revision"]]
    assert cfg.used_channels() == base_channels and sent == []
    assert all(path.read_bytes() == content for path, content in expected_files.items())
    conversion = store.migration(inline=True)
    store.inspect(conversion, migrate=True)
    migrated = store.publish(conversion, migrate=True)
    cfg.reload(force=True)
    assert cfg.get("schema") == 3 and cfg.used_channels() == base_channels
    yaml_text = (directory / "panels.yaml").read_text()
    assert "!secret" in yaml_text and "${site}" in yaml_text
    assert "&id" not in yaml_text and "*id" not in yaml_text
    assert all(tile.get("type") for tile in cfg.all_tiles())

    inline, state = draft()
    state = operation(inline, state, "add_instance", element="first", id="my-light")
    assert state["panels"][0]["sections"][0]["tiles"][-1]["id"] == "my-light"
    operation(inline, state, "add_instance", element="first", id="my-light", code=400)
    state = operation(inline, state, "add_instance", element="first")
    created_id = state["panels"][0]["sections"][0]["tiles"][-1]["id"]
    assert created_id == "dimmer-001"
    state = operation(inline, state, "remove_instance", id=created_id)
    state = operation(inline, state, "add_instance", element="first")
    assert state["panels"][0]["sections"][0]["tiles"][-1]["id"] == "dimmer-002"
    get(inline + "/preview/room.svg")
    get(inline + "/diff")
    inline_published = post(inline + "/publish", {"draft_version": state["draft_version"]})
    assert cfg.get("last_tile_ids") == {"dimmer": "dimmer-002"}
    reverted = post("/rollback", {"revision": restored["id"], "base_revision": inline_published["id"],
                                  "confirm": True})
    assert reverted["source"] == restored["id"] and cfg.get("schema") == 2
    assert inline_published["parent"] == migrated.id
    print("editor HTTP: операции, SVG, секреты, конфликты, восстановление и история OK")


if __name__ == "__main__":
    run()
