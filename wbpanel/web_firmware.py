# -*- coding: utf-8 -*-
"""Манифест и файлы OTA для esp32-panel."""

import os

from flask import abort, send_file

from .const import BASE_DIR
from .firmware import FirmwareError, FirmwareStore
from .web import _json, app


store = FirmwareStore(os.path.join(BASE_DIR, "data", "firmware"))


@app.route("/fw/firmware.json")
def firmware_manifest():
    """Единый манифест: latest для автоматического OTA и список выпусков."""
    try:
        return _json(store.manifest())
    except FirmwareError as exc:
        return _json({"error": str(exc)}, 500)


@app.route("/fw/<name>.bin")
def firmware_file(name):
    """Неизменяемый файл конкретной версии; манифест при этом не кешируется."""
    try:
        path = store.file_path(name + ".bin")
    except (FileNotFoundError, FirmwareError):
        abort(404)
    response = send_file(path, mimetype="application/octet-stream",
                         conditional=True, max_age=31536000)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
