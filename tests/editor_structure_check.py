"""Create, assign and remove real draft objects through the HTTP API."""
from pathlib import Path
from fixtures import env


def run():
    web, _, cfg, _ = env.setup(Path(__file__).parent / "fixtures/editor/managed.yaml", freeze_time=False)
    client = web.app.test_client()
    headers = {"X-WB-Editor": "1", "Origin": "http://localhost"}
    config = Path(cfg.path)
    with config.open("a") as stream:
        stream.write("\nscreens:\n  hall-esp: {panel: room, theme: dark}\n")
    with (config.parent / "secrets.yaml").open("a") as stream:
        stream.write("\nlogin: panel\n")
    panel_file = config.parent / "panels.yaml"
    panel_file.write_text(panel_file.read_text().replace("  tiles: !include", "  sections: []\n  tiles: !include", 1))
    original = panel_file.read_bytes()
    cfg.reload()

    def post(path, data):
        return client.post("/api/editor/" + path, json=data, headers=headers)

    response = post("drafts", {})
    assert float(response.headers["Server-Timing"].split("editor;dur=")[1]) >= 0
    state = response.json
    base = state["base_revision"]
    draft = "drafts/" + state["draft_id"]

    def edit(op, panel, section=None, fail=False, **fields):
        nonlocal state
        result = post(draft + "/operation", {"draft_version": state["draft_version"],
                      "operation": dict(op=op, panel=panel, section=section, **fields)})
        if fail:
            assert result.status_code == 400, result.json
        else:
            assert result.status_code == 200, result.json
            state = result.json
            return next((p for p in state["panels"] if p["id"] == panel), None)

    edit("remove_panel", "room", fail=True)
    ids = [t["id"] for t in next(p for p in state["panels"] if p["id"] == "room")["sections"][0]["tiles"]]
    moved = edit("add_section", "room", title="Основной раздел")
    assert ids == [t["id"] for t in moved["sections"][0]["tiles"]]
    assert len(moved["sections"]) == 1 and len(moved["sections"][0]["tiles"]) == 3
    edit("add_panel", "desk", title="Рабочий стол")
    edit("add_panel", "desk", title="Копия", fail=True)
    edit("add_section", "desk", title="Свет")
    edit("add_instance", "desk", 0, element="light")
    edit("add_section", "desk", title="Временно")
    edit("remove_section", "desk", 1)
    edit("add_panel", "spare", title="Временная")
    assert edit("remove_panel", "spare") is None
    edit("assign_screen", "desk", screen="hall-esp")
    edit("assign_screen", "desk", screen="panel-6a10")
    assert {"panel": "desk", "name": None} in state["screens"]
    assert "panel-6a10" not in str(state)
    edit("remove_panel", "desk", fail=True)
    assert client.get("/api/screen/hall-esp").json["panel"] == "room"
    assert client.get("/api/editor/" + draft + "/diff").status_code == 200
    rev = post(draft + "/publish", {"draft_version": state["draft_version"]})
    assert rev.status_code == 200, rev.json
    for screen in ("hall-esp", "panel-6a10"):
        assert client.get("/api/screen/" + screen).json["panel"] == "desk"
    assert cfg.get("screens")["hall-esp"]["theme"] == "dark"
    assert "!include" in (config.parent / "panels.yaml").read_text()
    assert len(cfg.panels["room"]["sections"][0]["tiles"]) == 3
    assert len(cfg.panels["desk"]["sections"]) == 1
    assert cfg.panels["desk"]["sections"][0]["tiles"]
    assert post("rollback", {"revision": base, "base_revision": rev.json["id"], "confirm": True}).status_code == 200
    assert client.get("/api/screen/hall-esp").json["panel"] == "room"
    assert panel_file.read_bytes() == original
    assert "desk" not in cfg.panels
    print("editor structure: panels, sections, ESP assignments, publication and rollback OK")


if __name__ == "__main__":
    run()
