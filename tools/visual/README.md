# Проверка вида

Две ступени. Первая — без браузера, её гоняет и CI:

    python3 tests/render_check.py

Все панели `tests/fixtures/showcase.yaml` во всех встроенных темах
(обычные, с подстановкой переменных, с кнопками шапки) и пульты на весь
экран. Время заморожено, поэтому отпечаток каждой картинки в
`tests/golden.json` меняется только вместе с видом. Изменение задумано —
`python3 tests/render_check.py --update` и закоммитить golden.json.

Вторая — посмотреть, *что именно* изменилось, попиксельно. Нужны Node и
Playwright с Chromium:

    git worktree add /tmp/was HEAD                       # было
    (cd /tmp/was && python3 tests/render_check.py --out /tmp/svg-a)
    python3 tests/render_check.py --out /tmp/svg-b        # стало
    node tools/visual/shots.js /tmp/svg-a /tmp/svg-b /tmp/png
    python3 tools/visual/pixdiff.py /tmp/png
    git worktree remove /tmp/was

Все адреса демона — `python3 tests/routes_check.py`. Страница в браузере
(окна, пульты, шестерёнка, «Сегодня», облегчённо) — `node
tests/browser_check.js` (нужен Playwright; сам поднимает тестовый сервер).

Страницы целиком, пульты и окна вручную — на тестовом сервере с теми же
выдуманными приборами (команды не уходят в MQTT, журнал — `/_log`):

    python3 tools/visual/serve.py 8846
    # http://127.0.0.1:8846/gal.html, /obj.html, /day.html, /tint.html …
