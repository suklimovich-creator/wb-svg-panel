#!/usr/bin/env python3
"""Опубликовать уже собранный esp32-panel.bin одним запуском.

    python3 tools/firmware_publish.py esp32-panel.bin
    python3 tools/firmware_publish.py esp32-panel-0.11.9.bin --no-latest

Версия и имя проекта читаются из самого образа ESP-IDF. Скрипт не собирает
проект и не обновляет экран: панель сама проверит latest по расписанию.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wbpanel.firmware import FirmwareError, FirmwareStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description="Опубликовать собранную прошивку esp32-panel в wb-svg-panel")
    parser.add_argument("bin", help="путь к собранному esp32-panel.bin")
    parser.add_argument("--no-latest", action="store_true",
                        help="добавить версию в список, не меняя latest")
    parser.add_argument("--firmware-dir",
                        default=os.path.join(ROOT, "data", "firmware"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        entry, manifest = FirmwareStore(args.firmware_dir).publish(
            args.bin, make_latest=not args.no_latest)
    except FirmwareError as exc:
        print("ОШИБКА: %s" % exc, file=sys.stderr)
        return 1
    print("Опубликовано: %s, %d байт" % (entry["version"], entry["size"]))
    print("Файл: %s" % os.path.join(args.firmware_dir, entry["file"]))
    print("SHA-256: %s" % entry["sha256"])
    print("latest: %s" % (manifest["latest"] or "не задан"))
    print("Манифест: %s" % os.path.join(args.firmware_dir, "firmware.json"))
    print("Проверка: curl http://192.168.1.77/panel/fw/firmware.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
