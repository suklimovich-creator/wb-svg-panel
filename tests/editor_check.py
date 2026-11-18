"""Managed configuration, explicit migration and transactional publication."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wbpanel.config import Config
from wbpanel.editor_documents import DocumentError
from wbpanel.editor_drafts import Changeset
from wbpanel.editor_revisions import Conflict, RevisionStore
from wbpanel import editor_revisions
from wbpanel.registry import writable_of
from wbpanel import web  # noqa: F401
from wbpanel.web_screen import screen_layout


def expect(error, callback):
    try:
        callback()
    except error as exc:
        return str(exc)
    raise AssertionError("expected " + error.__name__)


def put(path, contents):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(contents, encoding="utf-8")
    return path


def check_merge_overrides(root):
    path = put(root / "config.yaml", """
defaults: &defaults {type: switch, channel: dev/default, title: Default, w: 2}
panels:
  main:
    tiles:
    - <<: *defaults
      channel: dev/actual
      title: Actual
      w: 3
""")
    legacy = path.read_bytes()
    before = Config(path)
    store = RevisionStore(path)
    draft = store.migration()
    store.inspect(draft, migrate=True)
    assert path.read_bytes() == legacy
    store.publish(draft, migrate=True)
    after = Config(path, strict=True)
    assert list(before.all_tiles()) == list(after.all_tiles())
    assert after.used_channels() == {"dev/actual"}
    assert screen_layout("main", before) == screen_layout("main", after)


def check_inline_migration(root):
    path = put(root / "config.yaml", """
default: &common {type: switch, channel: dev/one}
panels:
  one:
    tiles:
    - <<: *common
      title: First
    - {type: value, channel: dev/value, id: room_meter}
  two:
    tiles:
    - <<: *common
      channel: dev/two
""")
    before = Config(path)
    store = RevisionStore(path)
    draft = store.migration(inline=True)
    store.inspect(draft, migrate=True)
    store.publish(draft, migrate=True)
    after = Config(path, strict=True)
    assert [{k: v for k, v in tile.items() if k != "id"} for tile in before.all_tiles()] == list(after.all_tiles())
    assert [stable_id for kind, stable_id in after.origins if kind == "tile"] == [
        "switch-001", "room_meter", "switch-002"]
    raw = (root / "panels.yaml").read_text()
    assert "&id" not in raw and "*id" not in raw and "element:" not in raw


def main(root):
    legacy = {
        "config.yaml": "# keep this comment exactly\npanels: !include rooms/panels.yaml\n",
        "rooms/panels.yaml": "main:\n  tiles: !include tiles.yaml\n"
                             "other:\n  sections:\n  - from: main\n",
        "rooms/tiles.yaml": "- !include light.yaml\n- !include light.yaml\n",
        "rooms/light.yaml": "type: switch\nchannel: !secret state\n"
                            "command_topic: /custom/${site}/set\nw: 2\nh: 0.5\n",
        "rooms/secrets.yaml": "state: /private/sentinel/state\n",
        "secrets.yaml": "site: broad-secret-token\n",
    }
    for name, data in legacy.items():
        put(root / name, data)
    path = root / "config.yaml"
    old = Config(path)
    store = RevisionStore(path)
    draft = store.migration()
    assert draft.base_revision == "legacy-0"
    assert not old.origins and path.read_text() == legacy[path.name]
    summary = store.inspect(draft, migrate=True)
    assert summary["elements"]["added"] == 2
    assert "secret" not in repr(summary) and "sentinel" not in repr(summary)
    assert path.read_text() == legacy[path.name]
    revision = store.publish(draft, migrate=True)
    current = Config(path)
    assert list(old.all_tiles()) == list(current.all_tiles())
    assert current.used_channels() == {"/private/sentinel/state"}
    assert set(writable_of(next(current.all_tiles()))) == {"/custom/broad-secret-token/set"}
    assert screen_layout("main", old) == screen_layout("main", current)
    origins = [v for key, v in current.origins.items() if key[0] == "tile"]
    assert len(origins) == 2 and origins[0].stable_id != origins[1].stable_id
    assert origins[0].file.parent.name == "rooms" and origins[0].physical_path == ()
    assert origins[0].logical_path == ("panels", "main", "tiles", 0)
    field = current.tile_origin(origins[0].stable_id, "channel")
    assert field.stable_id == origins[0].stable_id and field.physical_path == ("channel",)
    assert field.logical_path[0] == "elements"
    assert all(len(set(current.documents.includes[p])) > 0 for p in
               [("panels",), ("panels", "main", "tiles")])
    original = store.revisions / "legacy-0" / "files"
    assert all((original / name).read_bytes() == value.encode() for name, value in legacy.items())
    assert (original / "secrets.yaml").stat().st_mode & 0o077 == 0
    for manifest in store.revisions.glob("*/manifest.json"):
        assert "broad-secret-token" not in manifest.read_text()
        assert "/private/sentinel/state" not in manifest.read_text()

    draft = store.draft()
    put(root / "secrets.yaml", "site: external\n")
    expect(Conflict, lambda: store.publish(draft))
    put(root / "secrets.yaml", legacy["secrets.yaml"])
    draft = store.draft()
    first, second = origins
    first_rel, second_rel = [o.file.relative_to(root) for o in origins]
    first_bytes, second_bytes = [o.file.read_bytes() for o in origins]
    draft.changeset = Changeset({first_rel: first_bytes.replace(b"w: 2", b"w: 1"),
                                 second_rel: second_bytes.replace(b"w: 2", b"w: 1")})

    def failing_replace(target, data, mode=0o600):
        if target == second.file and data != second_bytes:
            raise OSError("injected write failure")
        return replace(target, data, mode)

    replace = editor_revisions._replace
    with patch.object(editor_revisions, "_replace", side_effect=failing_replace):
        expect(OSError, lambda: store.publish(draft))
    assert first.file.read_bytes() == first_bytes and second.file.read_bytes() == second_bytes
    assert store.history()[0] == revision and not store.journal.exists()

    load = store._active

    def wrong_reload(known):
        config, files = load(known)
        if files.get(first_rel) != first_bytes:
            config.data["unexpected"] = True
        return config, files

    with patch.object(store, "_active", side_effect=wrong_reload):
        assert "кандидат" in expect(DocumentError, lambda: store.publish(draft))
    assert first.file.read_bytes() == first_bytes and second.file.read_bytes() == second_bytes
    assert store.history()[0] == revision

    # A process may be interrupted after replacing a file but before HEAD.
    with store._locked():
        candidate_bytes = draft.changeset._files[first_rel]
        store.journal.write_bytes(editor_revisions._json({
            "base": revision.id, "next": "0" * 32,
            "files": {str(first_rel): store._hash(candidate_bytes)},
        }))
        replace(first.file, candidate_bytes)
    expect(Conflict, lambda: store.candidate(draft))
    assert store.journal.exists() and first.file.read_bytes() == candidate_bytes
    assert store.history()[0] == revision and first.file.read_bytes() == first_bytes

    with patch.object(store, "_active", wraps=store._active) as active:
        subsequent = store.publish(draft)
        assert active.call_count == 3
    current.reload()
    assert [tile["w"] for tile in current.all_tiles()] == [1, 1]
    expect(Conflict, lambda: store.publish(draft))
    reverted = store.rollback("legacy-0")
    assert reverted.parent == subsequent.id and reverted.source == "legacy-0"
    assert len(store.history()) == 4 and not first.file.exists()
    assert all((root / name).read_bytes() == value.encode() for name, value in legacy.items())

    managed = ("schema: 2\nelements:\n  sensor: {type: value, channel: dev/value}\n"
               "panels:\n  main:\n    tiles:\n"
               "    - {id: tile1, element: sensor}\n")
    put(path, managed)
    assert Config(path).origins["tile", "tile1"].physical_path == ("panels", "main", "tiles", 0)
    for bad, hint in (
        (managed + "    - {id: tile1, element: sensor}\n", "id"),
        (managed.replace("element: sensor", "element: other"), "element"),
        (managed.replace("element: sensor", "element: sensor, command_topic: bad"), "переопределение"),
        (managed.replace("  sensor:", "  sensor: {}\n  sensor:"), "YAML"),
        (managed.replace("dev/value", "!secret missing"), "секрет"),
        ("panels: !include config.yaml\n", "Цикл"),
        ("panels: !include missing.yaml\n", "missing.yaml"),
    ):
        put(path, bad)
        assert hint in expect(DocumentError, lambda: Config(path))

    # Status/shapes remain valid without the live MQTT state.
    put(path, managed)
    store.draft()
    print("editor OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        main(Path(directory))
        check_merge_overrides(Path(directory) / "merge")
        check_inline_migration(Path(directory) / "inline")
