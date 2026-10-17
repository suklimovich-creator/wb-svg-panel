# -*- coding: utf-8 -*-
"""
Тестовый сервер панели на выдуманных приборах - смотреть страницы и пульты
в браузере, гонять проверки Playwright'ом (tests/browser_check.js). MQTT
не нужен: команды складываются в журнал /_log, а ответ «прибор
переключился» сервер изображает сам.

    python3 tools/visual/serve.py [порт] [конфиг] [--history]

По умолчанию порт 8846 и tests/fixtures/showcase.yaml. Время не заморожено:
часы и календарь живые. --history - графики с выдуманными кривыми (для
замеров скорости: пустые графики легче настоящих).
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "fixtures"))

import env  # noqa: E402  (tests/fixtures/env.py)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
port = int(args[0]) if args else 8846
web, palette, cfg, sent = env.setup(args[1] if len(args) > 1 else None,
                                    freeze_time=False, history="--history" in sys.argv)


@web.app.route("/_log")
def _log():
    return json.dumps(sent)


print("http://127.0.0.1:%d/  темы: %s" % (port, ", ".join(palette.names())))
web.app.run(host="127.0.0.1", port=port, threaded=True)
