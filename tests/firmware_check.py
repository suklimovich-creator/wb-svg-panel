# -*- coding: utf-8 -*-
"""Проверка хранения, публикации и HTTP-раздачи прошивок esp32-panel."""

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import env  # noqa: E402

web, _palette, _cfg, _sent = env.setup(freeze_time=True)

from wbpanel import web_firmware  # noqa: E402
from wbpanel.firmware import (APP_DESC_MAGIC, APP_DESC_OFFSET,  # noqa: E402
                              MAX_IMAGE_SIZE, FirmwareError, FirmwareStore,
                              inspect_image)


def image(path, version="0.11.10", project="esp32-panel", magic=APP_DESC_MAGIC,
          fill=0):
    """Минимальный файл с настоящей раскладкой esp_app_desc_t."""
    body = bytearray([fill]) * 512

    def put(offset, size, text):
        raw = text.encode("ascii")
        if len(raw) >= size:
            raise ValueError(text)
        body[offset:offset + size] = raw + bytes(size - len(raw))

    struct.pack_into("<I", body, APP_DESC_OFFSET, magic)
    put(APP_DESC_OFFSET + 16, 32, version)
    put(APP_DESC_OFFSET + 48, 32, project)
    put(APP_DESC_OFFSET + 80, 16, "12:34:56")
    put(APP_DESC_OFFSET + 96, 16, "Nov 11 2026")
    with open(path, "wb") as fh:
        fh.write(body)
    return bytes(body)


def expect_error(fn, needle):
    try:
        fn()
    except FirmwareError as exc:
        if needle not in str(exc):
            raise AssertionError("в ошибке %r нет %r" % (exc, needle))
        return
    raise AssertionError("ожидалась FirmwareError: %s" % needle)


def main():
    checks = 0
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "esp32-panel.bin")
        body = image(source)
        info = inspect_image(source)
        assert info == {"version": "0.11.10", "built": "Nov 11 2026 12:34:56"}
        checks += 1

        bad = os.path.join(tmp, "bad.bin")
        image(bad, magic=0)
        expect_error(lambda: inspect_image(bad), "описания приложения")
        image(bad, project="other")
        expect_error(lambda: inspect_image(bad), "а не esp32-panel")
        image(bad, version="0.11")
        expect_error(lambda: inspect_image(bad), "X.Y.Z")
        with open(bad, "wb") as fh:
            fh.write(b"short")
        expect_error(lambda: inspect_image(bad), "короткий")
        with open(bad, "wb") as fh:
            fh.seek(MAX_IMAGE_SIZE)
            fh.write(b"x")
        expect_error(lambda: inspect_image(bad), "6 МБ")
        checks += 5

        root = os.path.join(tmp, "firmware")
        store = FirmwareStore(root)
        assert store.manifest() == {"latest": None, "versions": []}
        entry, manifest = store.publish(source)
        target = os.path.join(root, entry["file"])
        with open(target, "rb") as fh:
            assert fh.read() == body
        assert entry["size"] == len(body)
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()
        assert manifest["latest"] == "0.11.10"
        assert not os.path.exists(source + ".tmp")
        checks += 5

        old = os.path.join(tmp, "old.bin")
        image(old, version="0.11.9", fill=1)
        _old_entry, manifest = store.publish(old, make_latest=False)
        assert manifest["latest"] == "0.11.10"
        assert [x["version"] for x in manifest["versions"]] == ["0.11.10", "0.11.9"]
        checks += 2

        # Повтор того же выпуска идемпотентен, подмена содержимого запрещена.
        _entry, manifest = store.publish(source)
        assert len(manifest["versions"]) == 2
        image(source, fill=2)
        expect_error(lambda: store.publish(source), "другим содержимым")
        with open(target, "rb") as fh:
            assert fh.read() == body
        checks += 3

        newer = os.path.join(tmp, "newer.bin")
        image(newer, version="0.11.11", fill=3)
        _entry, manifest = store.publish(newer)
        assert manifest["latest"] == "0.11.11"
        assert [x["version"] for x in manifest["versions"]] == [
            "0.11.11", "0.11.10", "0.11.9"]
        checks += 2

        web_firmware.store = store
        client = web.app.test_client()
        response = client.get("/fw/firmware.json")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.get_json()["latest"] == "0.11.11"
        response = client.get("/fw/esp32-panel-0.11.10.bin")
        assert response.status_code == 200
        assert response.data == body
        assert response.mimetype == "application/octet-stream"
        assert "immutable" in response.headers["Cache-Control"]
        assert client.get("/fw/esp32-panel-9.9.9.bin").status_code == 404
        assert client.get("/fw/not-a-firmware.bin").status_code == 404
        checks += 8

        with open(store.manifest_path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        assert client.get("/fw/firmware.json").status_code == 500
        checks += 1

        # Отдельно проверяем сам односкриптовый сценарий, а не только Store.
        cli_root = os.path.join(tmp, "cli-firmware")
        script = os.path.join(ROOT, "tools", "firmware_publish.py")
        result = subprocess.run(
            [sys.executable, script, old, "--firmware-dir", cli_root],
            check=False, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        with open(os.path.join(cli_root, "firmware.json"), encoding="utf-8") as fh:
            cli_manifest = json.load(fh)
        assert cli_manifest["latest"] == "0.11.9"
        result = subprocess.run(
            [sys.executable, script, newer, "--no-latest",
             "--firmware-dir", cli_root],
            check=False, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        with open(os.path.join(cli_root, "firmware.json"), encoding="utf-8") as fh:
            cli_manifest = json.load(fh)
        assert cli_manifest["latest"] == "0.11.9"
        assert [x["version"] for x in cli_manifest["versions"]] == ["0.11.11", "0.11.9"]
        checks += 4

    print("firmware: %d проверок — успешно" % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
