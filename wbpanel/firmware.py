# -*- coding: utf-8 -*-
"""Хранение и публикация прошивок esp32-panel в data/firmware/."""

import hashlib
import json
import os
import re
import shutil
import struct
from datetime import datetime


PROJECT_NAME = "esp32-panel"
APP_DESC_MAGIC = 0xABCD5432
APP_DESC_OFFSET = 32
APP_DESC_SIZE = 256
MAX_IMAGE_SIZE = 6 * 1024 * 1024
VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FILENAME_RE = re.compile(
    r"esp32-panel-(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\.bin$")


class FirmwareError(Exception):
    """Ошибка образа или каталога прошивок."""


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def _cstring(data):
    return data.split(b"\0", 1)[0].decode("ascii", "strict")


def inspect_image(path):
    """Версия и дата сборки из штатного esp_app_desc_t внутри .bin."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(APP_DESC_OFFSET + APP_DESC_SIZE)
    except OSError as exc:
        raise FirmwareError("не удалось прочитать %s: %s" % (path, exc)) from exc
    if size > MAX_IMAGE_SIZE:
        raise FirmwareError("прошивка больше OTA-раздела 6 МБ")
    if len(head) < APP_DESC_OFFSET + APP_DESC_SIZE:
        raise FirmwareError("слишком короткий файл прошивки")
    magic = struct.unpack_from("<I", head, APP_DESC_OFFSET)[0]
    if magic != APP_DESC_MAGIC:
        raise FirmwareError("в файле нет описания приложения ESP-IDF")
    try:
        # esp_app_desc_t: magic(4), secure_version(4), reserv1(8),
        # затем version[32], project_name[32], time[16], date[16].
        version = _cstring(head[APP_DESC_OFFSET + 16:APP_DESC_OFFSET + 48])
        project = _cstring(head[APP_DESC_OFFSET + 48:APP_DESC_OFFSET + 80])
        time = _cstring(head[APP_DESC_OFFSET + 80:APP_DESC_OFFSET + 96])
        date = _cstring(head[APP_DESC_OFFSET + 96:APP_DESC_OFFSET + 112])
    except (UnicodeDecodeError, ValueError) as exc:
        raise FirmwareError("описание приложения содержит нечитаемые строки") from exc
    if project != PROJECT_NAME:
        raise FirmwareError("это прошивка %r, а не %s" % (project, PROJECT_NAME))
    if not VERSION_RE.fullmatch(version):
        raise FirmwareError("версия в прошивке должна иметь вид X.Y.Z: %r" % version)
    return {"version": version, "built": (date + " " + time).strip()}


def _sha256(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _atomic_json(path, body):
    tmp = os.path.join(os.path.dirname(path), ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class FirmwareStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    @property
    def manifest_path(self):
        return os.path.join(self.root, "firmware.json")

    def manifest(self):
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {"latest": None, "versions": []}
        except (OSError, ValueError) as exc:
            raise FirmwareError("не удалось прочитать firmware.json: %s" % exc) from exc
        if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
            raise FirmwareError("firmware.json должен содержать latest и список versions")
        latest = data.get("latest")
        if latest is not None and not isinstance(latest, str):
            raise FirmwareError("поле latest в firmware.json должно быть строкой или null")
        for entry in data["versions"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
                raise FirmwareError("в firmware.json есть некорректная запись версии")
        return {"latest": latest, "versions": data["versions"]}

    def file_path(self, name):
        if not FILENAME_RE.fullmatch(name):
            raise FirmwareError("недопустимое имя прошивки")
        path = os.path.join(self.root, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return path

    def publish(self, source, make_latest=True):
        info = inspect_image(source)
        version = info["version"]
        name = "%s-%s.bin" % (PROJECT_NAME, version)
        size, sha256 = _sha256(source)
        os.makedirs(self.root, exist_ok=True)
        target = os.path.join(self.root, name)

        data = self.manifest()
        previous = next((item for item in data["versions"]
                         if item.get("version") == version), None)
        if os.path.exists(target):
            _old_size, old_sha256 = _sha256(target)
            if old_sha256 != sha256:
                raise FirmwareError(
                    "версия %s уже опубликована с другим содержимым; "
                    "соберите новую версию" % version)
        else:
            tmp = os.path.join(self.root, ".%s.%d.tmp" % (name, os.getpid()))
            try:
                with open(tmp, "wb") as out, open(source, "rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

        entry = {
            "version": version,
            "file": name,
            "size": size,
            "sha256": sha256,
            "built": info["built"],
            "published": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if previous and previous.get("sha256") != sha256:
            raise FirmwareError("манифест уже содержит другую прошивку %s" % version)
        versions = [item for item in data["versions"] if item.get("version") != version]
        versions.append(entry)
        versions.sort(key=lambda item: version_key(item["version"]), reverse=True)
        result = {"latest": version if make_latest else data.get("latest"),
                  "versions": versions}
        _atomic_json(self.manifest_path, result)
        return entry, result
